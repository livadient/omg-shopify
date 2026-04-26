"""Repair variant_ids on all active t-shirt product images.

Some POST /images uploads dropped variant_ids silently (Shopify side
behaviour inconsistency we saw after the big refresh run). This script
walks every active 3-option t-shirt, classifies each image by filename,
and PUTs the correct variant_ids so the gallery swaps correctly when a
customer picks Male / Female / Front / Back on the product page.

Run:
  .venv/Scripts/python -m scripts.fix_variant_image_links
  .venv/Scripts/python -m scripts.fix_variant_image_links --dry-run
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

SKIP_HANDLES = {
    "astous-na-laloun-cyprus-male-tee",
    "astous-na-laloun-cyprus-female-limited-tee",
    "astous-na-laloun-cyprus-male-limited-tee",
}


def classify_image(src: str) -> str:
    """Return a canonical label from the image filename."""
    fn = src.split("?")[0].rsplit("/", 1)[-1].lower()
    # TJ mockups (always more specific match — check these first)
    if "_male_back" in fn and "mockup_cache" in fn:
        return "tj_male_back"
    if "_female_back" in fn and "mockup_cache" in fn:
        return "tj_female_back"
    if "_male_front" in fn and "mockup_cache" in fn:
        return "tj_male_front"
    if "_female_front" in fn and "mockup_cache" in fn:
        return "tj_female_front"
    # Scenes (filenames prefixed with scene name, may have UUID suffix from Shopify)
    if fn.startswith("01_closeup_back_male"):
        return "closeup_male"
    if fn.startswith("02_fullbody_back_male"):
        return "fullbody_male"
    if fn.startswith("01_closeup_back"):
        return "closeup_female"
    if fn.startswith("02_fullbody_back"):
        return "fullbody_female"
    if fn.startswith("03_product_back"):
        return "flat_lay_back"
    if fn.startswith("04_hanger_back"):
        return "hanger_back"
    if fn.startswith("04_product_front"):
        return "flat_lay_front"
    return "unknown"


def group_variants(product: dict) -> dict[tuple[str, str], list[int]]:
    groups: dict[tuple[str, str], list[int]] = {
        ("male", "front"): [], ("male", "back"): [],
        ("female", "front"): [], ("female", "back"): [],
    }
    for v in product.get("variants", []):
        g = (v.get("option1") or "").lower()
        p = (v.get("option2") or "").lower()
        gkey = "female" if "female" in g else ("male" if "male" in g else None)
        pkey = "back" if p == "back" else ("front" if p == "front" else None)
        if gkey and pkey:
            groups[(gkey, pkey)].append(v["id"])
    return groups


def expected_vids(label: str, groups: dict[tuple[str, str], list[int]]) -> list[int]:
    """Shopify enforces one-variant-per-image — the same variant cannot be
    linked to two images (silent revert on the second). Only the 4 TJ
    mockups carry variant_ids so the variant-image swap is unambiguous:
    picking Male/Back swaps the featured image to TJ male back, etc.
    Lifestyle shots and product flat-lay/hanger stay unlinked — they
    remain in the gallery but don't fight for the variant mapping.
    """
    return {
        "tj_male_back": groups[("male", "back")],
        "tj_female_back": groups[("female", "back")],
        "tj_male_front": groups[("male", "front")],
        "tj_female_front": groups[("female", "front")],
        "closeup_male": [],
        "fullbody_male": [],
        "closeup_female": [],
        "fullbody_female": [],
        "flat_lay_back": [],
        "hanger_back": [],
        "flat_lay_front": [],
    }.get(label, [])


async def fetch_active_tshirts(c: httpx.AsyncClient) -> list[dict]:
    resp = await c.get(f"{ADMIN_BASE}/products.json?limit=250", headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return [
        p for p in resp.json().get("products", [])
        if p.get("status") == "active"
        and p.get("product_type", "").lower() in ("t-shirt", "tshirt", "t shirt")
        and p.get("handle") not in SKIP_HANDLES
    ]


async def put_variant_ids(c: httpx.AsyncClient, product_id: int, image: dict,
                          variant_ids: list[int], dry_run: bool) -> bool:
    if dry_run:
        return True
    payload = {"image": {"id": image["id"], "variant_ids": variant_ids}}
    resp = await c.put(
        f"{ADMIN_BASE}/products/{product_id}/images/{image['id']}.json",
        headers=HEADERS, json=payload, timeout=30,
    )
    if resp.status_code >= 400:
        logger.warning(f"  PUT failed: {resp.status_code} {resp.text[:200]}")
        return False
    return True


async def process_product(c: httpx.AsyncClient, product: dict, dry_run: bool) -> dict:
    handle = product.get("handle")
    product_id = product["id"]
    groups = group_variants(product)
    # Fetch full product (with images)
    resp = await c.get(f"{ADMIN_BASE}/products/{product_id}.json", headers=HEADERS, timeout=60)
    resp.raise_for_status()
    images = resp.json()["product"].get("images", [])

    fixed = 0
    unknown = 0
    for img in images:
        label = classify_image(img.get("src", ""))
        if label == "unknown":
            unknown += 1
            continue
        want = expected_vids(label, groups)
        current = set(img.get("variant_ids") or [])
        if current == set(want):
            continue
        logger.info(f"  [{handle}] {label}: {len(current)} → {len(want)} vids")
        if await put_variant_ids(c, product_id, img, want, dry_run):
            fixed += 1

    logger.info(f"[{handle}] total={len(images)} fixed={fixed} unknown={unknown}")
    return {"handle": handle, "total": len(images), "fixed": fixed, "unknown": unknown}


async def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logger.info("DRY RUN — no changes")

    async with httpx.AsyncClient() as c:
        products = await fetch_active_tshirts(c)
        logger.info(f"Checking {len(products)} products")
        results = []
        for p in products:
            try:
                results.append(await process_product(c, p, dry_run))
            except Exception as e:
                logger.exception(f"[{p.get('handle')}] error: {e}")
                results.append({"handle": p.get("handle"), "error": str(e)})

    print("\n=== SUMMARY ===")
    print(f"Products: {len(results)}")
    print(f"Images fixed: {sum(r.get('fixed', 0) for r in results)}")
    print(f"Unknown filenames: {sum(r.get('unknown', 0) for r in results)}")


if __name__ == "__main__":
    asyncio.run(main())
