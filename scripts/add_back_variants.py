"""One-shot: add Placement (Front/Back) option to existing OMG t-shirt products.

For every tee product that still has the 2-option schema (Gender × Size),
this script:
  1. Adds a new "Placement" option with values ["Front", "Back"]
  2. Keeps existing 12 variants (they become Gender / Front / Size)
  3. Creates 12 new variants with the same sizes/prices but Placement="Back"
  4. Sets inventory + shipping profile for the new Back variants
  5. Generates back mockups via Qstomizer and uploads them linked to Back variants
  6. Regenerates the product's mapping in product_mappings.json

Idempotent: products that already have 3 options are skipped.
Safe: existing Front variant IDs are preserved — old orders keep working.

Usage:
    python -m scripts.add_back_variants --dry-run       # list what would change
    python -m scripts.add_back_variants                 # do it for real
    python -m scripts.add_back_variants --only HANDLE   # restrict to one product
"""
import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.shopify_product_creator import (
    ADMIN_API_VERSION,
    _admin_url,
    _headers,
    _ensure_inventory_available,
    _add_to_shipping_profile,
    create_mappings_for_product,
    upload_product_image,
    fetch_mockup_from_qstomizer,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("add_back_variants")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def _fetch_all_products() -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(_admin_url("products.json?limit=250"), headers=_headers())
        resp.raise_for_status()
        return resp.json().get("products", [])


def _is_unmigrated_tee(product: dict) -> bool:
    """True if this is a standard Gender+Size tee that hasn't been migrated yet.

    Skips:
      - non-tee products
      - products already having a Placement option
      - legacy single-gender products whose option1 is Color (e.g. the old
        male-limited / female-limited Astous tees). Their option1 values
        are 'White' etc., not 'Male' / 'Female'.
    """
    if product.get("product_type") != "T-Shirt":
        return False
    options = product.get("options", [])
    option_names = [(o.get("name") or "").lower() for o in options]
    if "placement" in option_names:
        return False  # already migrated
    # Must have a Gender option with Male/Female values
    variants = product.get("variants", [])
    if not variants:
        return False
    option1_values = {(v.get("option1") or "").lower() for v in variants}
    if not option1_values.issubset({"male", "female"}) and not option1_values & {"male", "female"}:
        return False
    # Reject products where option1 is clearly Color (White, Black, etc.)
    color_words = {"white", "black", "navy", "red", "grey", "gray", "blue"}
    if option1_values & color_words:
        return False
    return True


async def _update_product_with_back_variants(product: dict, dry_run: bool) -> dict:
    """PUT /products/{id}.json with a 3-option schema and 24 variants.

    Existing variants keep their variant_id (same option1+option2=size combo,
    we inject option2=Front and push size to option3). New Back variants are
    added with option2=Back.

    Returns the updated product payload on success.
    """
    pid = product["id"]
    existing_variants = product.get("variants", [])

    # Build updated variants list: first the existing 12 with option3 set,
    # then 12 new with placement=Back.
    updated_variants = []

    # Existing variants — shift option2 → option3, add option2 = "Front"
    for v in existing_variants:
        gender = v.get("option1", "")
        size = v.get("option2", "")  # current schema: option2 is size
        updated_variants.append({
            "id": v["id"],                # critical: preserves the variant ID
            "option1": gender,
            "option2": "Front",
            "option3": size,
            "price": v.get("price", "25.00"),
            "inventory_management": v.get("inventory_management", "shopify"),
            "inventory_policy": v.get("inventory_policy", "continue"),
        })

    # New Back variants — one per existing variant, new IDs assigned by Shopify
    for v in existing_variants:
        gender = v.get("option1", "")
        size = v.get("option2", "")
        updated_variants.append({
            "option1": gender,
            "option2": "Back",
            "option3": size,
            "price": v.get("price", "25.00"),
            "inventory_management": "shopify",
            "inventory_policy": "continue",
        })

    payload = {
        "product": {
            "id": pid,
            "options": [
                {"name": "Gender"},
                {"name": "Placement"},
                {"name": "Size"},
            ],
            "variants": updated_variants,
        }
    }

    if dry_run:
        logger.info(
            f"  [DRY-RUN] would PUT /products/{pid}.json with "
            f"{len(updated_variants)} variants (12 existing kept + 12 new Back)"
        )
        return product

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.put(
            _admin_url(f"products/{pid}.json"),
            headers=_headers(),
            json=payload,
        )
        if resp.status_code >= 400:
            logger.error(f"  Product update failed: {resp.status_code} {resp.text[:400]}")
            resp.raise_for_status()
        return resp.json().get("product", {})


async def _generate_and_upload_back_mockups(
    product: dict,
    design_filename: str,
    dry_run: bool,
) -> None:
    """Generate male-back + female-back mockups via Qstomizer, upload linked to Back variants."""
    pid = product["id"]
    handle = product.get("handle", "?")
    variants = product.get("variants", [])

    design_path = STATIC_DIR / design_filename
    if not design_path.exists():
        logger.warning(f"  Design file not found: {design_path} — skipping back mockups")
        return

    # Collect Back variant IDs per gender
    ids_by_gender: dict[str, list[int]] = {"male": [], "female": []}
    for v in variants:
        gender = (v.get("option1") or "").lower()
        placement = (v.get("option2") or "").lower()
        if placement != "back":
            continue
        gkey = "female" if "female" in gender else ("male" if "male" in gender else None)
        if gkey:
            ids_by_gender[gkey].append(v["id"])

    for ptype, size in [("male", "L"), ("female", "M")]:
        target_ids = ids_by_gender.get(ptype) or []
        if not target_ids:
            logger.info(f"  No {ptype}/Back variants to link — skipping")
            continue

        if dry_run:
            logger.info(
                f"  [DRY-RUN] would generate {ptype} back mockup and link to "
                f"{len(target_ids)} variants"
            )
            continue

        logger.info(f"  Fetching {ptype} back mockup from Qstomizer for '{handle}'...")
        try:
            mockup_url = await fetch_mockup_from_qstomizer(
                str(design_path), ptype, size, placement="back",
            )
            if not mockup_url:
                logger.warning(f"  No {ptype} back mockup URL returned")
                continue

            # Download
            mockup_path = STATIC_DIR / "proposals" / f"mockup_{handle}_{ptype}_back.png"
            mockup_path.parent.mkdir(exist_ok=True)
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.get(mockup_url, follow_redirects=True)
                r.raise_for_status()
                mockup_path.write_bytes(r.content)

            await upload_product_image(
                pid,
                mockup_path,
                alt=f"{ptype.capitalize()} Back T-Shirt Mockup",
                variant_ids=target_ids,
            )
            logger.info(
                f"  Uploaded {ptype} back mockup linked to {len(target_ids)} variants"
            )
        except Exception as e:
            logger.warning(f"  Failed {ptype} back mockup: {e}")


async def _regenerate_mapping(product: dict, design_filename: str, dry_run: bool) -> None:
    """Rebuild this product's entry in product_mappings.json with new variant titles."""
    if dry_run:
        logger.info(f"  [DRY-RUN] would regenerate mapping for {product.get('handle')}")
        return
    await create_mappings_for_product(omg_product=product, design_image=design_filename)
    logger.info(f"  Mapping regenerated in product_mappings.json")


async def migrate_product(product: dict, design_filename: str, dry_run: bool) -> bool:
    handle = product.get("handle", "?")
    logger.info(f"[{handle}] starting migration (dry_run={dry_run})")

    if not _is_unmigrated_tee(product):
        logger.info(f"[{handle}] already has Placement option or is not a tee — skip")
        return False

    # Step 1: add Placement option + Back variants
    try:
        updated = await _update_product_with_back_variants(product, dry_run)
    except Exception as e:
        logger.error(f"[{handle}] failed to update variants: {e}")
        return False

    if dry_run:
        logger.info(f"[{handle}] dry-run complete (no real changes)")
        return True

    # Re-fetch so we have fresh variant IDs for the new Back variants
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(
            _admin_url(f"products/{product['id']}.json"), headers=_headers()
        )
        r.raise_for_status()
        updated = r.json().get("product", {})

    # Step 2: inventory + shipping profile for new Back variants
    try:
        await _ensure_inventory_available(updated)
    except Exception as e:
        logger.warning(f"[{handle}] inventory setup partial failure: {e}")
    try:
        await _add_to_shipping_profile(updated)
    except Exception as e:
        logger.warning(f"[{handle}] shipping profile partial failure: {e}")

    # Step 3: back mockups
    await _generate_and_upload_back_mockups(updated, design_filename, dry_run)

    # Step 4: regenerate mapping
    await _regenerate_mapping(updated, design_filename, dry_run)

    logger.info(f"[{handle}] migration complete")
    return True


def _pick_design_filename(product: dict) -> str:
    """Pick the design filename to use for back-mockup generation.

    Preference: design_<handle>.png if it exists, else front_design.png fallback.
    """
    handle = product.get("handle", "")
    candidate = f"design_{handle}.png"
    if (STATIC_DIR / candidate).exists():
        return candidate
    return "front_design.png"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing")
    parser.add_argument("--only", type=str, default=None, help="Restrict to a single product handle")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of products migrated")
    args = parser.parse_args()

    if not settings.omg_shopify_admin_token:
        logger.error("OMG_SHOPIFY_ADMIN_TOKEN not set")
        return

    logger.info(f"Fetching all OMG products...")
    all_products = await _fetch_all_products()
    logger.info(f"  {len(all_products)} products total")

    tees = [p for p in all_products if _is_unmigrated_tee(p)]
    logger.info(f"  {len(tees)} tees need migration")

    if args.only:
        tees = [p for p in tees if p.get("handle") == args.only]
        logger.info(f"  Filtered to --only {args.only}: {len(tees)} products")

    if args.limit:
        tees = tees[: args.limit]
        logger.info(f"  Capped at --limit {args.limit}: {len(tees)} products")

    migrated = 0
    for p in tees:
        design = _pick_design_filename(p)
        ok = await migrate_product(p, design, args.dry_run)
        if ok:
            migrated += 1

    logger.info(f"Done: {migrated}/{len(tees)} products processed")


if __name__ == "__main__":
    asyncio.run(main())
