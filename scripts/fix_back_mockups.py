"""One-shot: replace the bad (empty-front) back mockups on every migrated tee
with fresh back mockups that actually show the design on the back side.

For each migrated tee product:
  1. Find and delete the existing "Male Back T-Shirt Mockup" + "Female Back T-Shirt Mockup" images.
  2. Regenerate fresh back mockups via Qstomizer (now fetches _customimageback correctly).
  3. Upload them linked to the Back variants so the gallery swap works.
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import STATIC_DIR
from app.shopify_product_creator import (
    _admin_url,
    _headers,
    upload_product_image,
    fetch_mockup_from_qstomizer,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("fix_back_mockups")


async def _fetch_all_products() -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(_admin_url("products.json?limit=250"), headers=_headers())
        resp.raise_for_status()
        return resp.json().get("products", [])


def _is_migrated_tee(product: dict) -> bool:
    if product.get("product_type") != "T-Shirt":
        return False
    option_names = [(o.get("name") or "").lower() for o in product.get("options", [])]
    return "placement" in option_names


async def _delete_image(pid: int, image_id: int) -> bool:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.delete(
            _admin_url(f"products/{pid}/images/{image_id}.json"), headers=_headers(),
        )
        return r.status_code < 300


def _pick_design_path(product: dict) -> Path | None:
    handle = product.get("handle", "")
    candidates = [
        STATIC_DIR / f"design_{handle}.png",
        STATIC_DIR / "front_design.png",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


async def fix_product(product: dict, dry_run: bool) -> bool:
    pid = product["id"]
    handle = product.get("handle", "?")
    logger.info(f"[{handle}] fixing back mockups (dry_run={dry_run})")

    # Collect existing back-mockup image IDs to delete
    back_image_ids = []
    for img in product.get("images", []):
        alt = (img.get("alt") or "").lower()
        if "back" in alt and ("mockup" in alt or "t-shirt" in alt):
            back_image_ids.append(img["id"])
    logger.info(f"  {len(back_image_ids)} existing back mockups to delete")

    # Group Back variant IDs by gender
    ids_by_gender = {"male": [], "female": []}
    for v in product.get("variants", []):
        if (v.get("option2") or "").lower() != "back":
            continue
        g = (v.get("option1") or "").lower()
        if "female" in g:
            ids_by_gender["female"].append(v["id"])
        elif "male" in g:
            ids_by_gender["male"].append(v["id"])

    design_path = _pick_design_path(product)
    if not design_path:
        logger.warning(f"  [{handle}] no design file found — skip")
        return False

    if dry_run:
        logger.info(
            f"  [DRY-RUN] would delete {len(back_image_ids)} old back mockups, "
            f"regenerate 2 new ones from {design_path.name}"
        )
        return True

    # Delete old bad back mockups
    for img_id in back_image_ids:
        ok = await _delete_image(pid, img_id)
        logger.info(f"  Deleted image {img_id}: {'ok' if ok else 'FAILED'}")

    # Regenerate + upload fresh back mockups (with retry for transient Qstomizer errors)
    for ptype, size in [("male", "L"), ("female", "M")]:
        target_ids = ids_by_gender[ptype]
        if not target_ids:
            continue

        mockup_url = None
        for attempt in range(1, 4):  # up to 3 tries
            logger.info(f"  Fetching {ptype} back mockup for '{handle}' (attempt {attempt}/3)...")
            try:
                mockup_url = await fetch_mockup_from_qstomizer(
                    str(design_path), ptype, size, placement="back",
                )
                if mockup_url:
                    break
                logger.warning(f"  {ptype} back attempt {attempt}: no URL")
            except Exception as e:
                logger.warning(f"  {ptype} back attempt {attempt} failed: {e}")
            await asyncio.sleep(3)

        if not mockup_url:
            logger.error(f"  {ptype} back: gave up after 3 attempts")
            continue

        try:
            mockup_path = STATIC_DIR / "proposals" / f"mockup_{handle}_{ptype}_back.png"
            mockup_path.parent.mkdir(exist_ok=True)
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.get(mockup_url, follow_redirects=True)
                r.raise_for_status()
                mockup_path.write_bytes(r.content)

            await upload_product_image(
                pid, mockup_path,
                alt=f"{ptype.capitalize()} Back T-Shirt Mockup",
                variant_ids=target_ids,
            )
            logger.info(f"  Uploaded {ptype} back mockup linked to {len(target_ids)} variants")
        except Exception as e:
            logger.warning(f"  {ptype} back upload failed: {e}")

    logger.info(f"[{handle}] done")
    return True


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", type=str, default=None)
    args = parser.parse_args()

    logger.info("Fetching products...")
    products = await _fetch_all_products()
    tees = [p for p in products if _is_migrated_tee(p)]
    logger.info(f"  {len(tees)} migrated tees")

    if args.only:
        tees = [p for p in tees if p.get("handle") == args.only]
        logger.info(f"  Filtered to --only: {len(tees)}")

    fixed = 0
    for p in tees:
        if await fix_product(p, args.dry_run):
            fixed += 1
    logger.info(f"Done: {fixed}/{len(tees)} processed")


asyncio.run(main())
