"""One-shot: link existing tee mockup images to their gender's variants.

Background: until now, mockup images uploaded by the design creator agent
weren't linked to specific variants — so picking a Female variant on the
product page didn't swap the gallery to the female mockup. The upload code
was fixed to pass `variant_ids`, but existing products need a backfill.

This script:
  1. Pages through every OMG product via the Admin API.
  2. Filters to t-shirts that have both Male and Female variants.
  3. For each, finds images whose alt text matches "Male T-Shirt Mockup"
     or "Female T-Shirt Mockup" and PUTs an update to link them to that
     gender's variant IDs.
  4. Idempotent — skips images already linked correctly.

Usage:
    python -m scripts.backfill_variant_images --dry-run
    python -m scripts.backfill_variant_images
"""
import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app.shopify_product_creator import _admin_url, _headers  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


MALE_ALT = "male t-shirt mockup"
FEMALE_ALT = "female t-shirt mockup"


async def _fetch_all_products(client: httpx.AsyncClient) -> list[dict]:
    products: list[dict] = []
    page_info: str | None = None
    while True:
        url = (
            _admin_url(f"products.json?limit=250&page_info={page_info}")
            if page_info
            else _admin_url("products.json?limit=250")
        )
        r = await client.get(url, headers=_headers(), timeout=30)
        r.raise_for_status()
        products.extend(r.json().get("products", []))

        link = r.headers.get("link", "")
        if 'rel="next"' in link:
            m = re.search(r'page_info=([^>&]+)', link)
            if m:
                page_info = m.group(1)
                continue
        break
    return products


def _group_variants_by_gender(product: dict) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {"male": [], "female": []}
    for v in product.get("variants", []):
        gender = (v.get("option1") or "").lower()
        if "female" in gender:
            groups["female"].append(v["id"])
        elif "male" in gender:
            groups["male"].append(v["id"])
    return groups


def _find_mockup_image(images: list[dict], target_alt: str) -> dict | None:
    for img in images:
        if (img.get("alt") or "").strip().lower() == target_alt:
            return img
    return None


async def _update_image_variants(
    client: httpx.AsyncClient,
    product_id: int,
    image_id: int,
    variant_ids: list[int],
) -> None:
    url = _admin_url(f"products/{product_id}/images/{image_id}.json")
    body = {"image": {"id": image_id, "variant_ids": variant_ids}}
    r = await client.put(url, headers=_headers(), json=body, timeout=30)
    r.raise_for_status()


async def backfill(dry_run: bool = False) -> None:
    async with httpx.AsyncClient() as client:
        products = await _fetch_all_products(client)
        logger.info(f"Fetched {len(products)} products from OMG store")

        scanned = 0
        skipped_not_tee = 0
        no_mockup_found = 0
        already_linked = 0
        updated = 0

        for product in products:
            variants_by_gender = _group_variants_by_gender(product)
            if not variants_by_gender["male"] or not variants_by_gender["female"]:
                skipped_not_tee += 1
                continue

            scanned += 1
            images = product.get("images", [])
            product_id = product["id"]
            handle = product.get("handle", "")

            for gender, target_alt in (("male", MALE_ALT), ("female", FEMALE_ALT)):
                img = _find_mockup_image(images, target_alt)
                if not img:
                    no_mockup_found += 1
                    logger.warning(
                        f"  {handle}: no image with alt='{target_alt}' (skipping {gender})"
                    )
                    continue

                target_ids = sorted(variants_by_gender[gender])
                current_ids = sorted(img.get("variant_ids") or [])
                if current_ids == target_ids:
                    already_linked += 1
                    continue

                if dry_run:
                    logger.info(
                        f"  [DRY-RUN] {handle}: would link image {img['id']} ({gender}) "
                        f"to {len(target_ids)} variants"
                    )
                    updated += 1
                else:
                    try:
                        await _update_image_variants(
                            client, product_id, img["id"], target_ids
                        )
                        logger.info(
                            f"  {handle}: linked image {img['id']} ({gender}) "
                            f"to {len(target_ids)} variants"
                        )
                        updated += 1
                    except Exception as e:
                        logger.error(f"  {handle}: failed to update image {img['id']}: {e}")

        logger.info("=" * 60)
        logger.info(f"Scanned (tee with both genders): {scanned}")
        logger.info(f"Skipped (not a unisex tee):      {skipped_not_tee}")
        logger.info(f"Already linked correctly:         {already_linked}")
        logger.info(f"Missing mockup alt text:          {no_mockup_found}")
        logger.info(f"{'Would update' if dry_run else 'Updated'}:                 {updated}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Don't write changes")
    args = parser.parse_args()
    asyncio.run(backfill(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
