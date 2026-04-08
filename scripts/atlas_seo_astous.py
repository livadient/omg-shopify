"""Atlas SEO: Add meta descriptions + optimize handles for all Astous products."""
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Handle renames: old_handle -> new_handle
HANDLE_RENAMES = {
    # Unified product (the active one with Male+Female variants)
    "astous-va-laloun-limited-edition-tee": "astous-na-laloun-cyprus-unisex-tee",
    # Old single-gender products
    "astous-va-laloun-graphic-tee-female-limited-edition": "astous-na-laloun-cyprus-female-tee",
    "astous-va-laloun-graphic-tee-male-limited-edition-t-shirt-1": "astous-na-laloun-cyprus-male-tee",
    "astous-va-laloun-graphic-tee-female-eu-edition": "astous-na-laloun-cyprus-female-eu-tee",
    "astous-va-laloun-graphic-tee-female-limited-edition-1": "astous-na-laloun-cyprus-female-limited-tee",
    "astous-va-laloun-graphic-tee-male-eu-edition": "astous-na-laloun-cyprus-male-eu-tee",
    "astous-va-laloun-graphic-tee-vintage-sunshine-limited-edition-t-shirt": "astous-na-laloun-cyprus-male-limited-tee",
}

# English meta descriptions per product
META_DESCRIPTIONS_EN = {
    "astous-va-laloun-limited-edition-tee": (
        "Astous na Laloun Limited Edition T-Shirt — authentic Cypriot design celebrating local culture. "
        "Premium cotton, unisex fit (Male S-5XL, Female S-XL). Free delivery in Cyprus."
    ),
    "astous-va-laloun-graphic-tee-female-limited-edition": (
        "Astous na Laloun Women's T-Shirt — authentic Cypriot graphic tee. "
        "Premium cotton, sizes S-5XL. Free delivery in Cyprus."
    ),
    "astous-va-laloun-graphic-tee-male-limited-edition-t-shirt-1": (
        "Astous na Laloun Men's T-Shirt — authentic Cypriot graphic tee. "
        "Premium cotton, sizes S-5XL. Free delivery in Cyprus."
    ),
    "astous-va-laloun-graphic-tee-female-eu-edition": (
        "Astous na Laloun Women's EU Edition — Cypriot design t-shirt for Europe. "
        "Premium cotton, fitted cut. Ships across EU."
    ),
    "astous-va-laloun-graphic-tee-female-limited-edition-1": (
        "Astous na Laloun Women's Limited Edition — Cypriot cultural t-shirt. "
        "Premium cotton, sizes S-3XL. Free delivery in Cyprus."
    ),
    "astous-va-laloun-graphic-tee-male-eu-edition": (
        "Astous na Laloun Men's EU Edition — Cypriot design t-shirt for Europe. "
        "Premium cotton, classic fit. Ships across EU."
    ),
    "astous-va-laloun-graphic-tee-vintage-sunshine-limited-edition-t-shirt": (
        "Astous na Laloun Men's Limited Edition — Cypriot cultural t-shirt. "
        "Premium cotton, sizes XS-3XL. Free delivery in Cyprus."
    ),
}

# Greek meta description (same for all Astous products)
META_DESCRIPTION_GR = (
    "Άστους να Λαλούν T-Shirt Κύπρος — Αυθεντικό κυπριακό σχέδιο σε premium βαμβάκι. "
    "Δωρεάν παράδοση στην Κύπρο. Ελληνικό design, τοπική κουλτούρα."
)


async def main():
    import httpx
    from app.shopify_product_creator import _admin_url, _headers
    from app.config import settings

    headers = _headers()
    graphql_url = f"https://{settings.omg_shopify_domain}/admin/api/2024-01/graphql.json"

    async with httpx.AsyncClient() as client:
        # Fetch all Astous products
        r = await client.get(_admin_url("products.json?limit=250"), headers=headers, timeout=30)
        r.raise_for_status()
        products = r.json().get("products", [])
        astous = [p for p in products if "astous" in p.get("handle", "").lower()]

        print(f"Found {len(astous)} Astous products\n")

        # Step 1: Set English meta descriptions
        print("=" * 60)
        print("Step 1: Setting English meta descriptions")
        print("=" * 60)
        for p in astous:
            old_handle = p["handle"]
            meta_en = META_DESCRIPTIONS_EN.get(old_handle)
            if not meta_en:
                logger.warning(f"No meta description for {old_handle}, skipping")
                continue

            r = await client.put(
                _admin_url(f"products/{p['id']}.json"),
                headers=headers,
                json={"product": {"id": p["id"], "metafields_global_description_tag": meta_en}},
                timeout=15,
            )
            r.raise_for_status()
            logger.info(f"  Meta set: {old_handle}")

        # Step 2: Set Greek meta descriptions via Translations API
        print("\n" + "=" * 60)
        print("Step 2: Setting Greek meta descriptions via Translations API")
        print("=" * 60)
        for p in astous:
            product_gid = f"gid://shopify/Product/{p['id']}"

            mutation = """
            mutation translationsRegister($resourceId: ID!, $translations: [TranslationInput!]!) {
              translationsRegister(resourceId: $resourceId, translations: $translations) {
                translations { key value locale }
                userErrors { field message }
              }
            }
            """
            variables = {
                "resourceId": product_gid,
                "translations": [{
                    "key": "meta_description",
                    "value": META_DESCRIPTION_GR,
                    "locale": "el",
                    "translatableContentDigest": "",  # empty = force overwrite
                }],
            }

            # Need the digest first — query the translatable content
            digest_query = """
            query ($resourceId: ID!) {
              translatableResource(resourceId: $resourceId) {
                translatableContent {
                  key
                  value
                  digest
                }
              }
            }
            """
            dr = await client.post(graphql_url, headers=headers,
                json={"query": digest_query, "variables": {"resourceId": product_gid}}, timeout=15)
            dr.raise_for_status()
            digest_data = dr.json().get("data", {}).get("translatableResource", {}).get("translatableContent", [])
            meta_digest = next((f["digest"] for f in digest_data if f["key"] == "meta_description"), None)

            if meta_digest:
                variables["translations"][0]["translatableContentDigest"] = meta_digest

                r = await client.post(graphql_url, headers=headers,
                    json={"query": mutation, "variables": variables}, timeout=15)
                r.raise_for_status()
                result = r.json()
                errors = result.get("data", {}).get("translationsRegister", {}).get("userErrors", [])
                if errors:
                    logger.warning(f"  Translation error for {p['handle']}: {errors}")
                else:
                    logger.info(f"  Greek meta set: {p['handle']}")
            else:
                logger.warning(f"  No meta_description digest for {p['handle']} — set English meta first, then retry")

        # Step 3: Rename handles
        print("\n" + "=" * 60)
        print("Step 3: Optimizing product URL handles")
        print("=" * 60)
        for p in astous:
            old_handle = p["handle"]
            new_handle = HANDLE_RENAMES.get(old_handle)
            if not new_handle:
                logger.info(f"  No rename for {old_handle}, skipping")
                continue

            r = await client.put(
                _admin_url(f"products/{p['id']}.json"),
                headers=headers,
                json={"product": {"id": p["id"], "handle": new_handle}},
                timeout=15,
            )
            r.raise_for_status()
            actual_handle = r.json().get("product", {}).get("handle", "?")
            logger.info(f"  {old_handle} -> {actual_handle}")

        # Step 4: Update product_mappings.json if the unified product handle changed
        print("\n" + "=" * 60)
        print("Step 4: Updating product_mappings.json")
        print("=" * 60)
        mappings_path = Path(__file__).resolve().parent.parent / "product_mappings.json"
        raw = mappings_path.read_bytes().decode("utf-8", errors="replace")
        # The mapping uses "astous-na-laloun-limited-edition-tee" but the Shopify handle
        # was "astous-va-laloun-limited-edition-tee". The new handle is "astous-na-laloun-cyprus-unisex-tee"
        old_mapping_handle = "astous-na-laloun-limited-edition-tee"
        new_mapping_handle = "astous-na-laloun-cyprus-unisex-tee"
        if old_mapping_handle in raw:
            raw = raw.replace(old_mapping_handle, new_mapping_handle)
            mappings_path.write_text(raw, encoding="utf-8")
            logger.info(f"  Updated mapping: {old_mapping_handle} -> {new_mapping_handle}")
        else:
            logger.info(f"  No mapping found for {old_mapping_handle}")

        print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
