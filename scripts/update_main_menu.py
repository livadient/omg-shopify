"""Atlas recommendation: Update main navigation menu with Cyprus-focused structure."""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
# Fix Windows console encoding for Greek characters
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main():
    import httpx
    from app.shopify_product_creator import _admin_url, _headers

    graphql_url = f"https://52922c-2.myshopify.com/admin/api/2024-01/graphql.json"
    headers = _headers()

    async with httpx.AsyncClient() as client:
        # Step 1: Check existing collections
        logger.info("Fetching existing collections...")
        r = await client.get(_admin_url("custom_collections.json?limit=250"), headers=headers, timeout=30)
        r.raise_for_status()
        custom_collections = r.json().get("custom_collections", [])

        r = await client.get(_admin_url("smart_collections.json?limit=250"), headers=headers, timeout=30)
        r.raise_for_status()
        smart_collections = r.json().get("smart_collections", [])

        all_collections = custom_collections + smart_collections
        print(f"\nExisting collections ({len(all_collections)}):")
        for c in all_collections:
            print(f"  - {c['title']} (handle: {c['handle']}, id: {c['id']})")

        # Step 2: Create missing collections
        needed_collections = {
            "programmers": {
                "title": "Προγραμματιστές | Programmer Tees",
                "body_html": "<p>T-shirts για προγραμματιστές και tech lovers. Coding humor, developer jokes, και geeky designs.</p>",
                "metafields_global_title_tag": "Programmer T-Shirts | Μπλουζάκια Προγραμματιστών | OMG",
                "metafields_global_description_tag": "Μπλουζάκια για προγραμματιστές — coding humor, developer tees, tech designs. Δωρεάν αποστολή στην Κύπρο.",
            },
            "womens": {
                "title": "Γυναικεία | Women's Tees",
                "body_html": "<p>Γυναικεία μπλουζάκια fitted cut σε μεγέθη S-XL. Premium βαμβάκι, μοντέρνα designs.</p>",
                "metafields_global_title_tag": "Γυναικεία T-Shirts | Women's Tees | OMG Cyprus",
                "metafields_global_description_tag": "Γυναικεία graphic tees — fitted cut, premium cotton, S-XL. Δωρεάν αποστολή στην Κύπρο.",
            },
            "mens": {
                "title": "Ανδρικά | Men's Tees",
                "body_html": "<p>Ανδρικά μπλουζάκια classic fit σε μεγέθη S-5XL. Premium βαμβάκι, bold designs.</p>",
                "metafields_global_title_tag": "Ανδρικά T-Shirts | Men's Tees | OMG Cyprus",
                "metafields_global_description_tag": "Ανδρικά graphic tees — classic fit, premium cotton, S-5XL. Δωρεάν αποστολή στην Κύπρο.",
            },
            "local-designs": {
                "title": "Τοπικά Σχέδια | Local Designs",
                "body_html": "<p>Μοναδικά designs εμπνευσμένα από την Κύπρο — τοπική κουλτούρα, ελληνικά, μεσογειακό lifestyle.</p>",
                "metafields_global_title_tag": "Τοπικά Σχέδια Κύπρου | Cyprus Local Designs | OMG",
                "metafields_global_description_tag": "Κυπριακά designs — τοπική κουλτούρα, ελληνικά quotes, μεσογειακό στυλ. Δωρεάν αποστολή στην Κύπρο.",
            },
        }

        existing_handles = {c["handle"] for c in all_collections}
        collection_ids = {c["handle"]: c["id"] for c in all_collections}

        for handle, data in needed_collections.items():
            if handle in existing_handles:
                logger.info(f"Collection '{handle}' already exists (ID: {collection_ids[handle]})")
                continue
            logger.info(f"Creating collection '{handle}'...")
            r = await client.post(
                _admin_url("custom_collections.json"),
                headers=headers,
                json={"custom_collection": {
                    "handle": handle,
                    "sort_order": "best-selling",
                    "published": True,
                    **data,
                }},
                timeout=15,
            )
            r.raise_for_status()
            new_id = r.json()["custom_collection"]["id"]
            collection_ids[handle] = new_id
            logger.info(f"Created collection '{handle}': {new_id}")

        # Step 3: Auto-populate collections based on product attributes
        logger.info("\nPopulating collections with matching products...")
        r = await client.get(_admin_url("products.json?limit=250"), headers=headers, timeout=30)
        r.raise_for_status()
        products = r.json().get("products", [])

        for product in products:
            pid = product["id"]
            title_lower = product.get("title", "").lower()
            handle = product.get("handle", "").lower()
            tags = product.get("tags", "").lower()
            variants = product.get("variants", [])

            # Programmers collection: geeky/programmer products
            programmer_keywords = ["programmer", "coding", "404", "sleep", "debug", "code", "developer", "digital-detox", "overthinker"]
            if any(kw in handle or kw in title_lower or kw in tags for kw in programmer_keywords):
                await _add_to_collection(client, headers, pid, collection_ids.get("programmers"), product["title"])

            # Local designs: Cyprus/Astous products
            if "astous" in handle or "cyprus" in handle or "κύπρος" in tags:
                await _add_to_collection(client, headers, pid, collection_ids.get("local-designs"), product["title"])

            # Women's: products that have Female variants
            has_female = any(v.get("option1", "").lower() == "female" for v in variants)
            if has_female:
                await _add_to_collection(client, headers, pid, collection_ids.get("womens"), product["title"])

            # Men's: products that have Male variants
            has_male = any(v.get("option1", "").lower() == "male" for v in variants)
            if has_male:
                await _add_to_collection(client, headers, pid, collection_ids.get("mens"), product["title"])

        # Step 4: Try to update the main menu via GraphQL
        logger.info("\nAttempting to update main navigation menu...")

        # Query existing menus
        query = """
        {
          menu(handle: "main-menu") {
            id
            title
            handle
            items(first: 20) {
              nodes {
                id
                title
                url
                resourceId
              }
            }
          }
        }
        """
        r = await client.post(graphql_url, headers=headers, json={"query": query}, timeout=15)
        r.raise_for_status()
        data = r.json()

        if data.get("errors"):
            logger.warning(f"GraphQL errors querying menu: {data['errors']}")
            print("\n⚠ Cannot update menu via API — likely missing 'read_online_store_navigation' scope.")
            print("  Re-authorize at /shopify-auth or update manually in Shopify Admin:")
            print("  Online Store > Navigation > Main menu")
            _print_manual_instructions(collection_ids)
            return

        menu_data = data.get("data", {}).get("menu")
        if not menu_data:
            logger.warning("Main menu not found")
            print("\n⚠ 'main-menu' not found. Update manually in Shopify Admin.")
            _print_manual_instructions(collection_ids)
            return

        menu_gid = menu_data["id"]
        print(f"\nCurrent main menu ({menu_gid}):")
        for item in menu_data["items"]["nodes"]:
            print(f"  - {item['title']} → {item.get('url', 'N/A')}")

        # Build new menu items
        store_url = "https://omg.com.cy"
        new_items = [
            {"title": "Κυπριακά T-Shirts", "url": f"{store_url}/collections/cyprus-tees"},
            {"title": "Προγραμματιστές", "url": f"{store_url}/collections/programmers"},
            {"title": "Γυναικεία", "url": f"{store_url}/collections/womens"},
            {"title": "Ανδρικά", "url": f"{store_url}/collections/mens"},
            {"title": "Τοπικά Σχέδια", "url": f"{store_url}/collections/local-designs"},
        ]

        mutation = """
        mutation menuUpdate($id: ID!, $title: String!, $items: [MenuItemInput!]!) {
          menuUpdate(id: $id, title: "OMG Clothing", items: $items) {
            menu {
              id
              title
              items(first: 20) {
                nodes {
                  title
                  url
                }
              }
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        variables = {
            "id": menu_gid,
            "title": "OMG Clothing",
            "items": new_items,
        }

        r = await client.post(graphql_url, headers=headers, json={"query": mutation, "variables": variables}, timeout=15)
        r.raise_for_status()
        result = r.json()

        if result.get("errors"):
            logger.warning(f"GraphQL errors updating menu: {result['errors']}")
            print("\n⚠ Cannot update menu — scope issue or API limitation.")
            _print_manual_instructions(collection_ids)
            return

        user_errors = result.get("data", {}).get("menuUpdate", {}).get("userErrors", [])
        if user_errors:
            logger.warning(f"Menu update errors: {user_errors}")
            print(f"\n⚠ Menu update failed: {user_errors}")
            _print_manual_instructions(collection_ids)
            return

        updated_menu = result["data"]["menuUpdate"]["menu"]
        print(f"\nMenu updated successfully!")
        print(f"New menu structure:")
        for item in updated_menu["items"]["nodes"]:
            print(f"  - {item['title']} → {item['url']}")


async def _add_to_collection(client, headers, product_id, collection_id, product_title):
    """Add a product to a collection, silently skip if already there."""
    from app.shopify_product_creator import _admin_url
    if not collection_id:
        return
    r = await client.post(
        _admin_url("collects.json"),
        headers=headers,
        json={"collect": {"product_id": product_id, "collection_id": collection_id}},
        timeout=15,
    )
    if r.status_code in (200, 201):
        logger.info(f"  Added to collection {collection_id}: {product_title}")
    elif r.status_code == 422:
        pass  # already in collection, silent
    else:
        logger.warning(f"  Failed to add {product_title} to collection {collection_id}: {r.status_code}")


def _print_manual_instructions(collection_ids):
    """Print manual instructions if API menu update fails."""
    print("\n" + "=" * 60)
    print("MANUAL MENU UPDATE INSTRUCTIONS")
    print("=" * 60)
    print("Go to: Shopify Admin > Online Store > Navigation > Main menu")
    print("\nSet these menu items (replace existing):\n")
    print("  1. Κυπριακά T-Shirts  → /collections/cyprus-tees")
    print("  2. Προγραμματιστές     → /collections/programmers")
    print("  3. Γυναικεία           → /collections/womens")
    print("  4. Ανδρικά             → /collections/mens")
    print("  5. Τοπικά Σχέδια      → /collections/local-designs")
    print("\nRemove 'Beauty' if it still exists.")
    print(f"\nCollections are all created and populated:")
    for handle, cid in sorted(collection_ids.items()):
        print(f"  - {handle}: https://omg.com.cy/collections/{handle}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
