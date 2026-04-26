"""Atlas SEO: Add Greek meta titles, meta descriptions, and image alt text for all t-shirt products."""
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


async def main():
    import httpx
    from app.shopify_product_creator import _admin_url, _headers
    from app.config import settings
    from app.agents.llm_client import generate_json

    headers = _headers()
    graphql_url = f"https://{settings.omg_shopify_domain}/admin/api/2024-01/graphql.json"

    async with httpx.AsyncClient() as client:
        # ── Step 0: Fetch all products (cursor-based pagination) ────────
        all_products = []
        url = _admin_url("products.json?limit=250")
        while url:
            r = await client.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            all_products.extend(r.json().get("products", []))
            # Cursor pagination via Link header
            link = r.headers.get("link", "")
            url = None
            if 'rel="next"' in link:
                for part in link.split(","):
                    if 'rel="next"' in part:
                        url = part.split("<")[1].split(">")[0]

        # Filter to t-shirts (exclude beauty/other products)
        tshirt_keywords = ["tee", "t-shirt", "tshirt", "shirt"]
        tshirts = [
            p for p in all_products
            if any(kw in p.get("product_type", "").lower() for kw in tshirt_keywords)
            or any(kw in p.get("handle", "").lower() for kw in tshirt_keywords)
            or any("size" in (opt.get("name", "").lower()) for opt in p.get("options", []))
        ]

        if not tshirts:
            # Fallback: use all products if filter is too narrow
            tshirts = all_products

        print(f"Found {len(tshirts)} t-shirt products out of {len(all_products)} total\n")

        # ── Step 1: Use Claude to generate Greek SEO metadata ───────────
        print("=" * 60)
        print("Step 1: Generating Greek SEO metadata via Claude")
        print("=" * 60)

        product_summaries = []
        for p in tshirts:
            product_summaries.append({
                "id": p["id"],
                "handle": p["handle"],
                "title": p["title"],
                "product_type": p.get("product_type", ""),
                "tags": p.get("tags", ""),
                "images": [
                    {"id": img["id"], "src": img.get("src", ""), "alt": img.get("alt", "")}
                    for img in p.get("images", [])
                ],
            })

        system_prompt = """You are an expert Greek SEO copywriter for OMG (omg.com.cy), a Cyprus-based online t-shirt store.
Generate Greek SEO metadata for each product. Rules:
- meta_title: max 60 characters. Format: '[Product Name in Greek] | Μπλουζάκι [Category] | OMG Cyprus'
  Keep the design name in English if it's a brand name or pun (e.g. '404 Sleep Not Found').
  Translate descriptive names to Greek.
- meta_description: max 155 characters. Must include a call-to-action and mention free shipping.
  Template style: 'Αγοράστε το [product description]. Premium βαμβάκι, unisex εφαρμογή. Δωρεάν παράδοση στην Κύπρο.'
- image_alt: for each image, descriptive Greek alt text. Format: 'Μπλουζάκι [design name] σε [color] χρώμα'
  If the image URL or alt text hints at color/gender, include that. Otherwise use generic description.

Return ONLY valid JSON array, one object per product:
[
  {
    "id": 12345,
    "handle": "product-handle",
    "meta_title": "...",
    "meta_description": "...",
    "images": [{"id": 67890, "alt": "Greek alt text"}]
  }
]"""

        user_prompt = f"Generate Greek SEO metadata for these {len(product_summaries)} products:\n\n{json.dumps(product_summaries, ensure_ascii=False, indent=2)}"

        print(f"Sending {len(product_summaries)} products to Claude for Greek translation...")
        seo_data = await generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=8192,
            temperature=0.3,
        )
        print(f"Received Greek SEO for {len(seo_data)} products\n")

        # Build lookup by product id
        seo_by_id = {item["id"]: item for item in seo_data}

        # ── Step 2: Set English meta fields (where missing) ─────────────
        print("=" * 60)
        print("Step 2: Ensuring English meta descriptions exist")
        print("=" * 60)
        for p in tshirts:
            # Check if product already has meta description
            existing_meta = p.get("metafields_global_description_tag") or ""
            if existing_meta:
                logger.info(f"  English meta already set: {p['handle']}")
                continue

            # Set a basic English meta description from the title
            en_meta = (
                f"{p['title']} — Premium cotton graphic tee, unisex fit. "
                f"Free delivery in Cyprus. Shop now at OMG."
            )
            if len(en_meta) > 155:
                en_meta = en_meta[:152] + "..."

            r = await client.put(
                _admin_url(f"products/{p['id']}.json"),
                headers=headers,
                json={"product": {
                    "id": p["id"],
                    "metafields_global_description_tag": en_meta,
                }},
                timeout=15,
            )
            r.raise_for_status()
            logger.info(f"  English meta set: {p['handle']}")

        # ── Step 3: Set English meta titles (where missing) ─────────────
        print("\n" + "=" * 60)
        print("Step 3: Ensuring English meta titles exist")
        print("=" * 60)
        for p in tshirts:
            existing_title = p.get("metafields_global_title_tag") or ""
            if existing_title:
                logger.info(f"  English title already set: {p['handle']}")
                continue

            en_title = f"{p['title']} | OMG Cyprus"
            if len(en_title) > 60:
                en_title = f"{p['title'][:48]} | OMG Cyprus"

            r = await client.put(
                _admin_url(f"products/{p['id']}.json"),
                headers=headers,
                json={"product": {
                    "id": p["id"],
                    "metafields_global_title_tag": en_title,
                }},
                timeout=15,
            )
            r.raise_for_status()
            logger.info(f"  English title set: {p['handle']}")

        # ── Step 4: Register Greek translations via GraphQL ─────────────
        print("\n" + "=" * 60)
        print("Step 4: Registering Greek meta titles + descriptions via Translations API")
        print("=" * 60)

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

        mutation = """
        mutation translationsRegister($resourceId: ID!, $translations: [TranslationInput!]!) {
          translationsRegister(resourceId: $resourceId, translations: $translations) {
            translations { key value locale }
            userErrors { field message }
          }
        }
        """

        for p in tshirts:
            seo = seo_by_id.get(p["id"])
            if not seo:
                logger.warning(f"  No Greek SEO data for {p['handle']}, skipping")
                continue

            product_gid = f"gid://shopify/Product/{p['id']}"

            # Get digests for all translatable fields
            dr = await client.post(
                graphql_url, headers=headers,
                json={"query": digest_query, "variables": {"resourceId": product_gid}},
                timeout=15,
            )
            dr.raise_for_status()
            digest_data = (
                dr.json()
                .get("data", {})
                .get("translatableResource", {})
                .get("translatableContent", [])
            )
            digest_map = {f["key"]: f["digest"] for f in digest_data}

            translations = []

            # Greek meta title
            gr_title = seo.get("meta_title", "")
            if gr_title and digest_map.get("meta_title"):
                if len(gr_title) > 60:
                    gr_title = gr_title[:57] + "..."
                translations.append({
                    "key": "meta_title",
                    "value": gr_title,
                    "locale": "el",
                    "translatableContentDigest": digest_map["meta_title"],
                })

            # Greek meta description
            gr_desc = seo.get("meta_description", "")
            if gr_desc and digest_map.get("meta_description"):
                if len(gr_desc) > 155:
                    gr_desc = gr_desc[:152] + "..."
                translations.append({
                    "key": "meta_description",
                    "value": gr_desc,
                    "locale": "el",
                    "translatableContentDigest": digest_map["meta_description"],
                })

            if not translations:
                logger.warning(f"  No digests for {p['handle']} — ensure English meta fields are set first")
                continue

            r = await client.post(
                graphql_url, headers=headers,
                json={"query": mutation, "variables": {
                    "resourceId": product_gid,
                    "translations": translations,
                }},
                timeout=15,
            )
            r.raise_for_status()
            result = r.json()
            errors = result.get("data", {}).get("translationsRegister", {}).get("userErrors", [])
            if errors:
                logger.warning(f"  Translation error for {p['handle']}: {errors}")
            else:
                logger.info(f"  Greek meta set: {p['handle']} — title: {gr_title[:40]}... desc: {gr_desc[:40]}...")

        # ── Step 5: Set Greek image alt text ────────────────────────────
        print("\n" + "=" * 60)
        print("Step 5: Setting Greek alt text for product images")
        print("=" * 60)

        for p in tshirts:
            seo = seo_by_id.get(p["id"])
            if not seo or not seo.get("images"):
                continue

            seo_images_by_id = {img["id"]: img["alt"] for img in seo["images"]}

            for img in p.get("images", []):
                gr_alt = seo_images_by_id.get(img["id"])
                if not gr_alt:
                    continue

                # First set English alt text if missing
                if not img.get("alt"):
                    en_alt = f"{p['title']} t-shirt"
                    r = await client.put(
                        _admin_url(f"products/{p['id']}/images/{img['id']}.json"),
                        headers=headers,
                        json={"image": {"id": img["id"], "alt": en_alt}},
                        timeout=15,
                    )
                    r.raise_for_status()
                    logger.info(f"  English alt set: image {img['id']}")

                # Register Greek alt text via translations
                image_gid = f"gid://shopify/ProductImage/{img['id']}"

                dr = await client.post(
                    graphql_url, headers=headers,
                    json={"query": digest_query, "variables": {"resourceId": image_gid}},
                    timeout=15,
                )
                dr.raise_for_status()
                translatable = (
                    dr.json()
                    .get("data", {})
                    .get("translatableResource")
                )
                if not translatable:
                    logger.warning(f"  Image {img['id']} not translatable (GID: {image_gid})")
                    continue
                img_digest_data = translatable.get("translatableContent", [])
                alt_digest = next(
                    (f["digest"] for f in img_digest_data if f["key"] == "alt"),
                    None,
                )

                if alt_digest:
                    r = await client.post(
                        graphql_url, headers=headers,
                        json={"query": mutation, "variables": {
                            "resourceId": image_gid,
                            "translations": [{
                                "key": "alt",
                                "value": gr_alt,
                                "locale": "el",
                                "translatableContentDigest": alt_digest,
                            }],
                        }},
                        timeout=15,
                    )
                    r.raise_for_status()
                    errors = (
                        r.json()
                        .get("data", {})
                        .get("translationsRegister", {})
                        .get("userErrors", [])
                    )
                    if errors:
                        logger.warning(f"  Alt text error for image {img['id']}: {errors}")
                    else:
                        logger.info(f"  Greek alt set: image {img['id']} — {gr_alt[:50]}")
                else:
                    logger.warning(f"  No alt digest for image {img['id']} — set English alt first")

        # ── Step 6: Inject Greek JSON-LD schema markup via metafield ────
        print("\n" + "=" * 60)
        print("Step 6: Adding Greek structured data (JSON-LD) via metafields")
        print("=" * 60)

        for p in tshirts:
            seo = seo_by_id.get(p["id"])
            if not seo:
                continue

            # Build Greek JSON-LD Product schema
            schema = {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": seo.get("meta_title", p["title"]),
                "description": seo.get("meta_description", ""),
                "brand": {"@type": "Brand", "name": "OMG Cyprus"},
                "offers": {
                    "@type": "AggregateOffer",
                    "priceCurrency": "EUR",
                    "availability": "https://schema.org/InStock",
                    "availableDeliveryMethod": "https://schema.org/DeliveryModeFreight",
                    "areaServed": [
                        {"@type": "Country", "name": "Cyprus"},
                        {"@type": "Country", "name": "Greece"},
                    ],
                    "shippingDetails": {
                        "@type": "OfferShippingDetails",
                        "shippingDestination": {
                            "@type": "DefinedRegion",
                            "addressCountry": ["CY", "GR"],
                        },
                    },
                },
                "inLanguage": "el",
                "url": f"https://omg.com.cy/products/{p['handle']}",
            }

            # Add price range from variants
            prices = [
                float(v["price"])
                for v in p.get("variants", [])
                if v.get("price")
            ]
            if prices:
                schema["offers"]["lowPrice"] = str(min(prices))
                schema["offers"]["highPrice"] = str(max(prices))

            # Add image
            if p.get("images"):
                schema["image"] = p["images"][0].get("src", "")

            schema_json = json.dumps(schema, ensure_ascii=False)

            # Store as product metafield
            r = await client.put(
                _admin_url(f"products/{p['id']}.json"),
                headers=headers,
                json={"product": {
                    "id": p["id"],
                    "metafields": [{
                        "namespace": "custom",
                        "key": "greek_schema_markup",
                        "value": schema_json,
                        "type": "json",
                    }],
                }},
                timeout=15,
            )
            r.raise_for_status()
            logger.info(f"  Schema metafield set: {p['handle']}")

        # ── Summary ─────────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("DONE! Summary:")
        print(f"  Products processed: {len(tshirts)}")
        print(f"  Greek SEO generated: {len(seo_data)}")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
