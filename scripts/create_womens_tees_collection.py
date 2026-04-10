"""One-shot: create the 'Women's Graphic Tees' collection on omg.com.cy.

This is the curated home for Mango's 'feminine' concept type — distinct
from the catch-all 'Γυναικεία | Women' collection (which auto-includes
every product with female variants). New feminine designs are routed
here automatically by COLLECTION_TAG_RULES["feminine-tees"].

Per Atlas's recommendation, the SEO targets the keyword "women's graphic
tees" (rising trend per Google Trends, matches our female product variants).

Usage:
    python -m scripts.create_womens_tees_collection
Idempotent — running again just updates the collection's body / meta.
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

COLLECTION_DEF = {
    "title": "Women's Graphic Tees",
    "handle": "womens-graphic-tees",
    "body_html": (
        "<p>Trending feminine graphic tees, curated for women who want their "
        "wardrobe to do the talking. From coquette and cottagecore to soft-girl "
        "minimalism and dreamy romance — these are the designs we made with "
        "you in mind, not the unisex shelf.</p>"
        "<p>Premium 100% cotton, true-to-size women's fit, printed in Cyprus. "
        "Free shipping across Cyprus and Greece.</p>"
        "<p>Γυναικεία γραφικά μπλουζάκια με μοντέρνα σχέδια εμπνευσμένα από "
        "τα τρέχοντα trends — coquette, cottagecore, soft girl και πολλά άλλα. "
        "Premium βαμβάκι, γυναικεία εφαρμογή.</p>"
    ),
    "meta_title": "Women's Graphic Tees | Trending Feminine Designs | OMG Cyprus",
    "meta_description": (
        "Shop trending women's graphic tees — coquette, cottagecore, soft girl "
        "and more. Premium cotton, women's fit, designed in Cyprus. "
        "Free shipping across Cyprus & Greece."
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

        logger.info("=" * 60)
        logger.info(f"DONE — Women's Graphic Tees Collection ID: {collection_id}")
        logger.info(f"      URL: https://omg.com.cy/collections/{COLLECTION_DEF['handle']}")
        logger.info("=" * 60)
        logger.info("Bake this into CATEGORY_COLLECTIONS in shopify_product_creator.py:")
        logger.info(f'    "feminine-tees": {collection_id},')
        logger.info(
            "Future feminine-type Mango designs will be auto-routed via "
            "COLLECTION_TAG_RULES['feminine-tees']."
        )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
