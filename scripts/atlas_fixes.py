"""Execute Atlas recommendations #1 and #3."""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def fix_pricing():
    """#3: Fix duplicate Astous product pricing — set to 30.00 EUR."""
    import httpx
    from app.config import settings
    from app.shopify_product_creator import _admin_url, _headers

    handles_to_fix = [
        "astous-va-laloun-graphic-tee-female-limited-edition-1",
        "astous-va-laloun-graphic-tee-vintage-sunshine-limited-edition-t-shirt",
    ]

    async with httpx.AsyncClient() as client:
        for handle in handles_to_fix:
            # Fetch product by handle
            r = await client.get(
                _admin_url(f"products.json?handle={handle}"),
                headers=_headers(),
                timeout=30,
            )
            r.raise_for_status()
            products = r.json().get("products", [])
            if not products:
                logger.warning(f"Product not found: {handle}")
                continue

            product = products[0]
            pid = product["id"]
            logger.info(f"Found product: {product['title']} (ID: {pid})")

            for v in product.get("variants", []):
                vid = v["id"]
                current_price = v.get("price", "?")
                if current_price != "30.00":
                    r = await client.put(
                        _admin_url(f"variants/{vid}.json"),
                        headers=_headers(),
                        json={"variant": {"id": vid, "price": "30.00"}},
                        timeout=15,
                    )
                    r.raise_for_status()
                    logger.info(f"  Variant {vid} ({v.get('title', '?')}): {current_price} -> 30.00 EUR")
                else:
                    logger.info(f"  Variant {vid} ({v.get('title', '?')}): already 30.00 EUR")


async def create_cyprus_collection():
    """#1: Create Cyprus-specific collection with Greek product names."""
    import httpx
    from app.config import settings
    from app.shopify_product_creator import _admin_url, _headers

    handle = "cyprus-tees"
    title = "Άστους να Λαλούν T-Shirts Κύπρος"
    body_html = (
        "<p>Ανακαλύψτε τη συλλογή <strong>Άστους να Λαλούν</strong> — "
        "μπλουζάκια εμπνευσμένα από την κυπριακή κουλτούρα και παράδοση. "
        "Κάθε σχέδιο αντικατοπτρίζει το πνεύμα της Κύπρου, τη γλώσσα μας και τον τρόπο ζωής μας. "
        "Φόρεσε την κυπριακή σου ταυτότητα με στυλ!</p>"
    )
    seo_title = "Άστους να Λαλούν T-Shirts | Κυπριακά Μπλουζάκια | OMG Cyprus"
    seo_description = (
        "Κυπριακά μπλουζάκια Άστους να Λαλούν — μοναδικά designs εμπνευσμένα "
        "από την κυπριακή κουλτούρα. Δωρεάν αποστολή στην Κύπρο."
    )

    async with httpx.AsyncClient() as client:
        # Check if collection already exists
        r = await client.get(
            _admin_url(f"custom_collections.json?handle={handle}"),
            headers=_headers(),
            timeout=15,
        )
        r.raise_for_status()
        existing = r.json().get("custom_collections", [])

        if existing:
            collection_id = existing[0]["id"]
            logger.info(f"Collection '{handle}' already exists (ID: {collection_id}), updating...")
            r = await client.put(
                _admin_url(f"custom_collections/{collection_id}.json"),
                headers=_headers(),
                json={"custom_collection": {
                    "id": collection_id,
                    "title": title,
                    "body_html": body_html,
                    "metafields_global_title_tag": seo_title,
                    "metafields_global_description_tag": seo_description,
                    "sort_order": "best-selling",
                    "published": True,
                }},
                timeout=15,
            )
            r.raise_for_status()
        else:
            logger.info(f"Creating collection '{handle}'...")
            r = await client.post(
                _admin_url("custom_collections.json"),
                headers=_headers(),
                json={"custom_collection": {
                    "title": title,
                    "handle": handle,
                    "body_html": body_html,
                    "metafields_global_title_tag": seo_title,
                    "metafields_global_description_tag": seo_description,
                    "sort_order": "best-selling",
                    "published": True,
                }},
                timeout=15,
            )
            r.raise_for_status()
            collection_id = r.json()["custom_collection"]["id"]
            logger.info(f"Created collection: {collection_id}")

        # Find all Astous products and add them
        r = await client.get(
            _admin_url("products.json?limit=250"),
            headers=_headers(),
            timeout=30,
        )
        r.raise_for_status()
        products = r.json().get("products", [])

        astous_products = [p for p in products if "astous" in p.get("handle", "").lower()]
        logger.info(f"Found {len(astous_products)} Astous products")

        for p in astous_products:
            r = await client.post(
                _admin_url("collects.json"),
                headers=_headers(),
                json={"collect": {"product_id": p["id"], "collection_id": collection_id}},
                timeout=15,
            )
            if r.status_code in (200, 201):
                logger.info(f"  Added: {p['title']}")
            elif r.status_code == 422:
                logger.info(f"  Already in collection: {p['title']}")
            else:
                logger.warning(f"  Failed to add {p['title']}: {r.status_code}")

        print(f"\nCollection URL: https://omg.com.cy/collections/{handle}")
        print(f"Admin: https://admin.shopify.com/store/52922c-2/collections/{collection_id}")


async def main():
    print("=" * 60)
    print("Atlas Recommendation #3: Fix duplicate Astous pricing")
    print("=" * 60)
    await fix_pricing()

    print()
    print("=" * 60)
    print("Atlas Recommendation #1: Create Cyprus collection")
    print("=" * 60)
    await create_cyprus_collection()


if __name__ == "__main__":
    asyncio.run(main())
