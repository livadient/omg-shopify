"""SEO management tasks for the OMG Shopify store via Admin API.

Run directly:
    .venv/Scripts/python -m app.seo_management [task]

Tasks:
    fix-handles     Fix duplicate product handles and standardize spelling
    homepage-seo    Update homepage title tag and meta description
    create-collections  Create Cyprus-specific product collections
    all             Run all tasks
"""
import asyncio
import json
import logging
import sys

import httpx

from app.config import settings
from app.omg_fulfillment import _admin_url, _headers

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


# ---------------------------------------------------------------------------
# Task 1: Fix product URL handles and duplicate content
# ---------------------------------------------------------------------------

# Map of current (broken/duplicate) handles → desired new handle
HANDLE_FIXES = {
    # Female limited edition uses same handle as male - fix it
    # We'll identify by product title containing "Female" or "Vintage Sunshine"
}

# Products to find and update (by title substring → new handle)
PRODUCT_HANDLE_UPDATES = [
    {
        "match_title": "Female",
        "match_handle_contains": "male-limited-edition",
        "new_handle": "astous-va-laloun-graphic-tee-female-limited-edition",
    },
    {
        "match_title": "Vintage Sunshine",
        "match_handle_contains": "male-limited-edition",
        "new_handle": "astous-va-laloun-vintage-sunshine-tee",
    },
]

# Standardize 'na' → 'va' in all Astous product handles
SPELLING_STANDARDIZATION = {
    "astous-na-laloun": "astous-va-laloun",
}


async def _get_all_products() -> list[dict]:
    """Fetch all products from OMG store via Admin API."""
    products = []
    page_info = None
    headers = await _headers()

    async with httpx.AsyncClient() as client:
        while True:
            if page_info:
                url = _admin_url(f"products.json?limit=250&page_info={page_info}")
            else:
                url = _admin_url("products.json?limit=250")

            r = await client.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            batch = r.json().get("products", [])
            products.extend(batch)

            # Check for pagination via Link header
            link = r.headers.get("link", "")
            if 'rel="next"' in link:
                # Extract page_info from next link
                import re
                match = re.search(r'page_info=([^>&]+)', link)
                if match:
                    page_info = match.group(1)
                    continue
            break

    logger.info(f"Fetched {len(products)} products from OMG store")
    return products


async def _update_product(product_id: int, updates: dict) -> dict:
    """Update a product via Admin API."""
    url = _admin_url(f"products/{product_id}.json")
    headers = await _headers()
    payload = {"product": {"id": product_id, **updates}}

    async with httpx.AsyncClient() as client:
        r = await client.put(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        return r.json().get("product", {})


async def fix_handles():
    """Fix duplicate handles and standardize na→va spelling."""
    products = await _get_all_products()
    updated = 0

    # Step 1: Fix specific duplicate handles
    for rule in PRODUCT_HANDLE_UPDATES:
        for product in products:
            title = product.get("title", "")
            handle = product.get("handle", "")
            if (rule["match_title"].lower() in title.lower()
                    and rule["match_handle_contains"] in handle):
                logger.info(
                    f"Fixing handle: '{handle}' → '{rule['new_handle']}' "
                    f"(product: {title}, id: {product['id']})"
                )
                await _update_product(product["id"], {"handle": rule["new_handle"]})
                updated += 1

    # Step 2: Standardize 'na' → 'va' spelling in all Astous handles
    for product in products:
        handle = product.get("handle", "")
        title = product.get("title", "")
        new_handle = handle
        for old, new in SPELLING_STANDARDIZATION.items():
            if old in handle:
                new_handle = handle.replace(old, new)

        if new_handle != handle:
            # Also fix the title if it contains 'na'
            new_title = title.replace("na Laloun", "va Laloun").replace("Na Laloun", "Va Laloun")
            updates = {"handle": new_handle}
            if new_title != title:
                updates["title"] = new_title
            logger.info(
                f"Standardizing: '{handle}' → '{new_handle}' "
                f"(product id: {product['id']})"
            )
            await _update_product(product["id"], updates)
            updated += 1

    if updated == 0:
        logger.info("No handle fixes needed — products already correct or not found")
    else:
        logger.info(f"Updated {updated} product handles")

    # Step 3: Update local product_mappings.json to match new handles
    _update_local_mappings()


def _update_local_mappings():
    """Update source_handle values in product_mappings.json to use 'va' spelling."""
    mappings_path = "product_mappings.json"
    try:
        with open(mappings_path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning("product_mappings.json not found, skipping local update")
        return

    changed = False
    for mapping in data.get("mappings", []):
        handle = mapping.get("source_handle", "")
        for old, new in SPELLING_STANDARDIZATION.items():
            if old in handle:
                mapping["source_handle"] = handle.replace(old, new)
                title = mapping.get("source_title", "")
                mapping["source_title"] = title.replace("na Laloun", "va Laloun").replace("Na Laloun", "Va Laloun")
                changed = True
                logger.info(f"Updated local mapping: '{handle}' → '{mapping['source_handle']}'")

    if changed:
        with open(mappings_path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Saved updated product_mappings.json")


# ---------------------------------------------------------------------------
# Task 2: Optimize homepage for Cyprus t-shirt searches
# ---------------------------------------------------------------------------

HOMEPAGE_TITLE = "Custom T-Shirts Cyprus | Graphic Tees | OMG.com.cy"
HOMEPAGE_META_DESCRIPTION = (
    "Premium custom graphic t-shirts designed in Cyprus. "
    "Free shipping across Cyprus & Greece. "
    "Unique designs printed on demand by TShirtJunkies."
)


async def update_homepage_seo():
    """Update the OMG store homepage SEO (title and meta description).

    Uses Shopify Admin API metafield approach:
    - PUT /admin/api/2024-01/metafields.json for global SEO settings

    Note: Shopify homepage SEO is set via Online Store > Preferences.
    The Admin API doesn't expose this directly, but we can use the
    pages API or fall back to manual instructions.
    """
    headers = await _headers()

    # Try to update via the shop metafields (Shopify stores homepage SEO
    # in the shop resource's meta_title and meta_description)
    # The correct approach is through the online store channel's metafields
    # or through the GraphQL Admin API

    # Use GraphQL Admin API which supports shop SEO updates
    graphql_url = f"https://{settings.omg_shopify_domain}/admin/api/2024-01/graphql.json"

    mutation = """
    mutation shopUpdate($input: ShopInput!) {
      shopUpdate(input: $input) {
        shop {
          id
        }
        userErrors {
          field
          message
        }
      }
    }
    """

    # Note: The shopUpdate mutation may not support SEO fields directly.
    # Shopify homepage SEO is managed via Online Store > Preferences.
    # We'll try the metafields approach first.

    # Set homepage SEO via metafields on the shop resource
    metafields = [
        {
            "namespace": "global",
            "key": "title_tag",
            "value": HOMEPAGE_TITLE,
            "type": "single_line_text_field",
        },
        {
            "namespace": "global",
            "key": "description_tag",
            "value": HOMEPAGE_META_DESCRIPTION,
            "type": "single_line_text_field",
        },
    ]

    async with httpx.AsyncClient() as client:
        for mf in metafields:
            url = _admin_url("metafields.json")
            payload = {"metafield": {**mf, "owner_resource": "shop"}}
            r = await client.post(url, headers=headers, json=payload, timeout=15)
            if r.status_code in (200, 201):
                logger.info(f"Set shop metafield: {mf['namespace']}.{mf['key']}")
            else:
                logger.warning(
                    f"Could not set metafield {mf['key']}: {r.status_code} {r.text[:200]}"
                )

    # Provide manual instructions as fallback
    logger.info(
        "\n📋 MANUAL STEP (if metafields didn't work):\n"
        "Go to OMG Shopify Admin → Online Store → Preferences\n"
        f"  Title: {HOMEPAGE_TITLE}\n"
        f"  Meta description: {HOMEPAGE_META_DESCRIPTION}\n"
        "Also add these keywords in product descriptions and tags:\n"
        "  - 'Cyprus t-shirts'\n"
        "  - 'Κύπρος μπλουζάκια'\n"
    )


# ---------------------------------------------------------------------------
# Task 3: Create Cyprus-specific product collections
# ---------------------------------------------------------------------------

COLLECTIONS_TO_CREATE = [
    {
        "title": "Cyprus Graphic Tees",
        "handle": "cyprus-graphic-tees",
        "body_html": (
            "<p>Premium graphic t-shirts designed in Cyprus. "
            "Featuring unique Cypriot-inspired designs including our signature "
            "Astous va Laloun collection — celebrating Greek Cypriot culture "
            "with bold, expressive graphics.</p>"
            "<p>Ανακαλύψτε μοναδικά γραφικά μπλουζάκια σχεδιασμένα στην Κύπρο. "
            "Δωρεάν αποστολή σε Κύπρο και Ελλάδα.</p>"
        ),
        "meta_title": "Cyprus Graphic Tees | Cypriot T-Shirt Designs | OMG.com.cy",
        "meta_description": (
            "Shop unique graphic tees designed in Cyprus. "
            "Greek Cypriot inspired designs, premium quality. "
            "Free shipping across Cyprus & Greece."
        ),
        "sort_order": "best-selling",
        "published": True,
    },
    {
        "title": "Greek Cyprus Shirts",
        "handle": "greek-cyprus-shirts",
        "body_html": (
            "<p>Celebrate Greek Cypriot culture with our exclusive t-shirt collection. "
            "From the iconic Astous va Laloun series to contemporary Cypriot designs, "
            "each shirt tells a story rooted in Cyprus.</p>"
            "<p>Γιορτάστε τον ελληνοκυπριακό πολιτισμό με τη συλλογή μας. "
            "Μοναδικά σχέδια εμπνευσμένα από την Κύπρο.</p>"
        ),
        "meta_title": "Greek Cyprus Shirts | Ελληνοκυπριακά Μπλουζάκια | OMG.com.cy",
        "meta_description": (
            "Greek Cypriot t-shirts celebrating Cyprus culture. "
            "Astous va Laloun collection and more. "
            "Κυπριακά μπλουζάκια με δωρεάν αποστολή."
        ),
        "sort_order": "best-selling",
        "published": True,
    },
]


async def create_collections():
    """Create Cyprus-specific product collections on the OMG store."""
    headers = await _headers()
    products = await _get_all_products()

    # Find Astous products to add to collections
    astous_product_ids = [
        p["id"] for p in products
        if "astous" in p.get("handle", "").lower()
        or "astous" in p.get("title", "").lower()
    ]
    logger.info(f"Found {len(astous_product_ids)} Astous products to add to collections")

    async with httpx.AsyncClient() as client:
        for collection_def in COLLECTIONS_TO_CREATE:
            # Check if collection already exists
            url = _admin_url(
                f"custom_collections.json?handle={collection_def['handle']}"
            )
            r = await client.get(url, headers=headers, timeout=15)
            existing = r.json().get("custom_collections", []) if r.status_code == 200 else []

            if existing:
                collection_id = existing[0]["id"]
                logger.info(
                    f"Collection '{collection_def['title']}' already exists "
                    f"(id: {collection_id}), updating..."
                )
                # Update existing collection
                url = _admin_url(f"custom_collections/{collection_id}.json")
                payload = {
                    "custom_collection": {
                        "id": collection_id,
                        "body_html": collection_def["body_html"],
                        "metafields_global_title_tag": collection_def["meta_title"],
                        "metafields_global_description_tag": collection_def["meta_description"],
                        "sort_order": collection_def["sort_order"],
                        "published": collection_def["published"],
                    }
                }
                r = await client.put(url, headers=headers, json=payload, timeout=15)
                if r.status_code == 200:
                    logger.info(f"Updated collection '{collection_def['title']}'")
                else:
                    logger.error(f"Failed to update: {r.status_code} {r.text[:200]}")
                    continue
            else:
                # Create new collection
                # Include collects inline to add products at creation time
                payload = {
                    "custom_collection": {
                        "title": collection_def["title"],
                        "handle": collection_def["handle"],
                        "body_html": collection_def["body_html"],
                        "metafields_global_title_tag": collection_def["meta_title"],
                        "metafields_global_description_tag": collection_def["meta_description"],
                        "sort_order": collection_def["sort_order"],
                        "published": collection_def["published"],
                    }
                }
                url = _admin_url("custom_collections.json")
                r = await client.post(url, headers=headers, json=payload, timeout=15)
                if r.status_code in (200, 201):
                    collection_id = r.json()["custom_collection"]["id"]
                    logger.info(
                        f"Created collection '{collection_def['title']}' "
                        f"(id: {collection_id})"
                    )
                else:
                    logger.error(
                        f"Failed to create collection '{collection_def['title']}': "
                        f"{r.status_code} {r.text[:200]}"
                    )
                    continue

            # Add products to the collection via collects
            for product_id in astous_product_ids:
                url = _admin_url("collects.json")
                payload = {
                    "collect": {
                        "product_id": product_id,
                        "collection_id": collection_id,
                    }
                }
                r = await client.post(url, headers=headers, json=payload, timeout=15)
                if r.status_code in (200, 201):
                    logger.info(f"  Added product {product_id} to collection")
                elif r.status_code == 422:
                    # Already in collection
                    logger.info(f"  Product {product_id} already in collection")
                else:
                    logger.warning(
                        f"  Failed to add product {product_id}: "
                        f"{r.status_code} {r.text[:100]}"
                    )

    logger.info("Collection creation complete")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

TASKS = {
    "fix-handles": fix_handles,
    "homepage-seo": update_homepage_seo,
    "create-collections": create_collections,
}


async def run_all():
    """Run all SEO tasks in sequence."""
    logger.info("=" * 60)
    logger.info("Task 1: Fix product URL handles")
    logger.info("=" * 60)
    await fix_handles()

    logger.info("")
    logger.info("=" * 60)
    logger.info("Task 2: Update homepage SEO")
    logger.info("=" * 60)
    await update_homepage_seo()

    logger.info("")
    logger.info("=" * 60)
    logger.info("Task 3: Create Cyprus-specific collections")
    logger.info("=" * 60)
    await create_collections()

    logger.info("")
    logger.info("All SEO tasks complete!")


def main():
    task = sys.argv[1] if len(sys.argv) > 1 else "all"

    if task == "all":
        asyncio.run(run_all())
    elif task in TASKS:
        asyncio.run(TASKS[task]())
    else:
        print(f"Unknown task: {task}")
        print(f"Available: {', '.join(TASKS.keys())}, all")
        sys.exit(1)


if __name__ == "__main__":
    main()
