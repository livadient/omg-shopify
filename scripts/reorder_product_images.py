"""Reorder product images so a clean product shot is the primary (card
thumbnail), and delete the 'Design Artwork' transparent PNGs that were
previously uploaded as the last image on each product. Local static/
design_<handle>.png remains the source of truth.

Desired order (primary first):
  1. 03_product_back            (flat-lay, unisex — primary)
  2. 04_hanger_back / 04_product_front  (hanger or front flat-lay, unisex)
  3. TJ female-back mockup      (hero print preview)
  4. 01_closeup_back            (female closeup)
  5. 02_fullbody_back           (female fullbody)
  6. 01_closeup_back_male       (male closeup)
  7. 02_fullbody_back_male      (male fullbody)
  8. TJ male-back mockup
  9. TJ female-front mockup
 10. TJ male-front mockup

Variant_ids on each image are preserved (we re-send them on the PUT).

Run:
  .venv/Scripts/python -m scripts.reorder_product_images
  .venv/Scripts/python -m scripts.reorder_product_images --dry-run
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ADMIN_BASE = f"https://{settings.omg_shopify_domain}/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": settings.omg_shopify_admin_token}
STATIC = ROOT / "static"

# Filename fragments → priority. Lower number = earlier position.
# Unmatched images go after these at position 99 (preserved order).
# Order logic: TJ male mockup is the card thumbnail (position 1), TJ female
# mockup is the hover image shown on card hover (position 2). Both are
# clean garment shots with the print on them — ideal for browse grids.
# Then male lifestyle shots (so male variant select shows male model),
# female lifestyle shots, and the remaining mockups.
PRIORITY = [
    ("_male_back.png", 1),                  # TJ male back mockup — card thumbnail
    ("_female_back.png", 2),                # TJ female back mockup — card hover
    ("01_closeup_back_male.png", 3),        # male model close-up
    ("02_fullbody_back_male.png", 4),       # male model full-body
    ("01_closeup_back.png", 5),             # female model close-up
    ("02_fullbody_back.png", 6),            # female model full-body
    ("03_product_back.png", 7),             # flat-lay back
    ("04_hanger_back.png", 8),              # hanger
    ("04_product_front.png", 8),            # hanger / front flat-lay variant
    ("_male_front.png", 9),                 # TJ male front mockup
    ("_female_front.png", 10),              # TJ female front mockup
]

SKIP_HANDLES = {
    "astous-na-laloun-cyprus-male-tee",
    "astous-na-laloun-cyprus-female-limited-tee",
    "astous-na-laloun-cyprus-male-limited-tee",
}


def classify(src: str, alt: str) -> tuple[int, str]:
    """Return (priority, label). Lower priority = earlier position."""
    # Filename-based — extract the last path segment before the query string
    fn = src.split("?")[0].rsplit("/", 1)[-1].lower()
    for fragment, prio in PRIORITY:
        # TJ mockup fragments are substrings (e.g. "_male_back.png" inside
        # "mockup_cache_design_transparent_tj_told_her_shes_the_one_male_back.png").
        # Scene fragments are also substrings but more specific — the longer
        # TJ pattern always wins because we listed it separately.
        if fragment in fn:
            return (prio, fn)
    return (99, fn)


async def fetch_active_tshirts() -> list[dict]:
    async with httpx.AsyncClient() as c:
        resp = await c.get(f"{ADMIN_BASE}/products.json?limit=250", headers=HEADERS, timeout=60)
        resp.raise_for_status()
        products = resp.json().get("products", [])
    return [
        p for p in products
        if p.get("status") == "active"
        and p.get("product_type", "").lower() in ("t-shirt", "tshirt", "t shirt")
        and p.get("handle") not in SKIP_HANDLES
    ]


async def fetch_product_images(product_id: int) -> list[dict]:
    async with httpx.AsyncClient() as c:
        resp = await c.get(
            f"{ADMIN_BASE}/products/{product_id}/images.json",
            headers=HEADERS, timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("images", [])


async def backup_design_artwork(handle: str, image: dict) -> bool:
    """If static/design_<handle>.png doesn't exist, download the Design
    Artwork image before we delete it from Shopify.
    """
    dest = STATIC / f"design_{handle}.png"
    if dest.exists():
        return True
    src = image.get("src")
    if not src:
        return False
    try:
        async with httpx.AsyncClient() as c:
            resp = await c.get(src, timeout=60, follow_redirects=True)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        logger.info(f"  [{handle}] backed up Design Artwork → {dest}")
        return True
    except Exception as e:
        logger.warning(f"  [{handle}] backup failed: {e}")
        return False


async def delete_image(product_id: int, image_id: int, dry_run: bool) -> bool:
    if dry_run:
        return True
    async with httpx.AsyncClient() as c:
        resp = await c.delete(
            f"{ADMIN_BASE}/products/{product_id}/images/{image_id}.json",
            headers=HEADERS, timeout=30,
        )
    return resp.status_code < 400


async def set_position(product_id: int, image_id: int, position: int,
                       variant_ids: list[int], dry_run: bool) -> bool:
    """Issue a PUT to update position. Re-sends variant_ids so the gender
    linking is preserved (Shopify clears fields not in the payload on PUT).
    """
    if dry_run:
        return True
    payload = {"image": {"id": image_id, "position": position, "variant_ids": variant_ids}}
    async with httpx.AsyncClient() as c:
        resp = await c.put(
            f"{ADMIN_BASE}/products/{product_id}/images/{image_id}.json",
            headers=HEADERS, json=payload, timeout=30,
        )
    if resp.status_code >= 400:
        logger.warning(f"  PUT position failed: {resp.status_code} {resp.text[:200]}")
        return False
    return True


async def process_product(product: dict, dry_run: bool) -> dict:
    handle = product.get("handle")
    product_id = product["id"]
    images = await fetch_product_images(product_id)
    logger.info(f"[{handle}] {len(images)} images")

    # Step 1: find & delete Design Artwork image (after backup to static/)
    design_images = [i for i in images if (i.get("alt") or "") == "Design Artwork"]
    deleted = 0
    for img in design_images:
        await backup_design_artwork(handle, img)
        if await delete_image(product_id, img["id"], dry_run):
            deleted += 1
            logger.info(f"  [{handle}] deleted Design Artwork image {img['id']}")

    # Step 2: reorder remaining images
    remaining = [i for i in images if (i.get("alt") or "") != "Design Artwork"]
    enriched = []
    for img in remaining:
        prio, fn = classify(img.get("src", ""), img.get("alt", ""))
        enriched.append((prio, fn, img))
    enriched.sort(key=lambda x: (x[0], x[1]))

    reordered = 0
    for new_pos, (_prio, fn, img) in enumerate(enriched, start=1):
        current_pos = img.get("position")
        if current_pos == new_pos:
            continue
        vids = img.get("variant_ids") or []
        if await set_position(product_id, img["id"], new_pos, vids, dry_run):
            reordered += 1

    logger.info(f"[{handle}] deleted={deleted} reordered={reordered} total_remaining={len(enriched)}")
    return {"handle": handle, "deleted": deleted, "reordered": reordered, "total": len(enriched)}


async def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logger.info("DRY RUN — no changes will be made")

    products = await fetch_active_tshirts()
    logger.info(f"Processing {len(products)} active t-shirts")
    results = []
    for p in products:
        try:
            r = await process_product(p, dry_run)
            results.append(r)
        except Exception as e:
            logger.exception(f"[{p.get('handle')}] failed: {e}")
            results.append({"handle": p.get("handle"), "error": str(e)})
    print("\n=== SUMMARY ===")
    total_del = sum(r.get("deleted", 0) for r in results)
    total_reo = sum(r.get("reordered", 0) for r in results)
    print(f"Products processed: {len(results)}")
    print(f"Design Artwork images deleted: {total_del}")
    print(f"Image positions updated: {total_reo}")


if __name__ == "__main__":
    asyncio.run(main())
