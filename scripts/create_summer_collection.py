"""One-shot: create the 'Summer Collection 2026' on omg.com.cy and add all current
t-shirt products to it. Run once per setup; idempotent on re-run.

Usage (locally or on server):
    .venv/Scripts/python -m scripts.create_summer_collection
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# OMG T-Shirts collection — source of products to copy into the new Summer collection
OMG_TSHIRTS_COLLECTION_ID = 451595010329

COLLECTION_DEF = {
    "title": "Summer Collection 2026",
    "handle": "summer-graphic-tees",
    "body_html": (
        "<p>Beat the Mediterranean heat in style. Our 2026 Summer Collection brings "
        "you cool cotton graphic tees designed for sunny days, beach trips, and "
        "everything in between — from Cyprus to anywhere the sun shines.</p>"
        "<p>Each shirt is printed on premium 100% cotton, breathable enough for "
        "August in Limassol and bold enough to stand out at any beach bar. "
        "Free shipping across Cyprus and Greece.</p>"
        "<p>Καλοκαιρινά μπλουζάκια από βαμβάκι, εμπνευσμένα από τον ήλιο της "
        "Μεσογείου. Δροσερά, άνετα και έτοιμα για κάθε καλοκαιρινή σου εξόρμηση.</p>"
    ),
    "meta_title": "Summer Graphic Tees | Cool Cotton T-Shirts | OMG Cyprus",
    "meta_description": (
        "Shop the 2026 Summer Collection — premium cotton graphic tees designed in "
        "Cyprus for hot Mediterranean days. Free shipping across Cyprus & Greece."
    ),
    "sort_order": "best-selling",
    "published": True,
}


async def main() -> int:
    import httpx
    from app.shopify_product_creator import _admin_url, _headers

    headers = _headers()
    if not headers.get("X-Shopify-Access-Token"):
        logger.error("OMG_SHOPIFY_ADMIN_TOKEN not set — aborting")
        return 1

    async with httpx.AsyncClient(timeout=30) as client:
        # Step 1: check whether the collection already exists
        r = await client.get(
            _admin_url(f"custom_collections.json?handle={COLLECTION_DEF['handle']}"),
            headers=headers,
        )
        existing = r.json().get("custom_collections", []) if r.status_code == 200 else []

        if existing:
            collection_id = existing[0]["id"]
            logger.info(f"Collection already exists (id={collection_id}) — updating meta")
            r = await client.put(
                _admin_url(f"custom_collections/{collection_id}.json"),
                headers=headers,
                json={"custom_collection": {
                    "id": collection_id,
                    "body_html": COLLECTION_DEF["body_html"],
                    "metafields_global_title_tag": COLLECTION_DEF["meta_title"],
                    "metafields_global_description_tag": COLLECTION_DEF["meta_description"],
                    "sort_order": COLLECTION_DEF["sort_order"],
                    "published": COLLECTION_DEF["published"],
                }},
            )
            r.raise_for_status()
        else:
            logger.info("Creating new collection...")
            r = await client.post(
                _admin_url("custom_collections.json"),
                headers=headers,
                json={"custom_collection": {
                    "title": COLLECTION_DEF["title"],
                    "handle": COLLECTION_DEF["handle"],
                    "body_html": COLLECTION_DEF["body_html"],
                    "metafields_global_title_tag": COLLECTION_DEF["meta_title"],
                    "metafields_global_description_tag": COLLECTION_DEF["meta_description"],
                    "sort_order": COLLECTION_DEF["sort_order"],
                    "published": COLLECTION_DEF["published"],
                }},
            )
            if r.status_code not in (200, 201):
                logger.error(f"Failed to create collection: {r.status_code} {r.text[:300]}")
                return 1
            collection_id = r.json()["custom_collection"]["id"]
            logger.info(f"Created collection (id={collection_id})")

        # Step 2: fetch all product IDs in the OMG T-Shirts source collection
        r = await client.get(
            _admin_url(f"collections/{OMG_TSHIRTS_COLLECTION_ID}/products.json?limit=250"),
            headers=headers,
        )
        if r.status_code != 200:
            logger.error(f"Failed to list source-collection products: {r.status_code} {r.text[:300]}")
            return 1
        products = r.json().get("products", [])
        logger.info(f"Found {len(products)} t-shirts in source collection {OMG_TSHIRTS_COLLECTION_ID}")

        # Step 3: add each to the new Summer collection (idempotent — 422 = already in)
        added = 0
        already = 0
        failed = 0
        for p in products:
            r = await client.post(
                _admin_url("collects.json"),
                headers=headers,
                json={"collect": {
                    "product_id": p["id"],
                    "collection_id": collection_id,
                }},
            )
            if r.status_code in (200, 201):
                logger.info(f"  + {p['handle']}")
                added += 1
            elif r.status_code == 422:
                logger.info(f"  = {p['handle']} (already in collection)")
                already += 1
            else:
                logger.warning(f"  ! {p['handle']}: {r.status_code} {r.text[:200]}")
                failed += 1

        logger.info("=" * 60)
        logger.info(f"DONE — Summer Collection ID: {collection_id}")
        logger.info(f"      Added: {added}, already in: {already}, failed: {failed}")
        logger.info(f"      URL: https://omg.com.cy/collections/{COLLECTION_DEF['handle']}")
        logger.info("=" * 60)
        logger.info(f"Bake this into CATEGORY_COLLECTIONS in shopify_product_creator.py:")
        logger.info(f'    "summer-tees": {collection_id},')

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
