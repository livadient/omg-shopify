"""Atlas: Add bilingual size guide (EN/GR) to all t-shirt products.

Sets a `custom.size_guide` metafield with an HTML size table,
appends the size guide to body_html, registers Greek translations
for the size guide and existing metafields.
"""
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

# ── Size data ───────────────────────────────────────────────────────────────

MALE_SIZES = [
    # (size_label, EU, chest_cm, length_cm)
    ("S", "36-38", "91", "71"),
    ("M", "40-42", "96", "74"),
    ("L", "44-46", "101", "76"),
    ("XL", "48-50", "106", "79"),
    ("2XL", "52-54", "111", "81"),
    ("3XL", "56-58", "117", "84"),
    ("4XL", "60-62", "122", "86"),
    ("5XL", "64-66", "127", "89"),
]

FEMALE_SIZES = [
    ("S", "36-38", "82", "63"),
    ("M", "40-42", "86", "65"),
    ("L", "44-46", "91", "67"),
    ("XL", "48-50", "96", "69"),
]

# ── English HTML ────────────────────────────────────────────────────────────

SIZE_GUIDE_EN = """<div class="size-guide" style="margin:24px 0;padding:20px;background:#f8f8f8;border-radius:8px;font-family:sans-serif">
<h3 style="margin:0 0 16px;font-size:18px">📏 Size Guide</h3>
<p style="margin:0 0 12px;font-size:14px;color:#555">All measurements in centimeters (cm). Measure a t-shirt that fits you well and compare.</p>

<h4 style="margin:16px 0 8px;font-size:15px">Men's / Unisex</h4>
<table style="width:100%;border-collapse:collapse;font-size:14px;text-align:center">
<thead><tr style="background:#222;color:#fff">
<th style="padding:8px">Size</th><th style="padding:8px">EU</th><th style="padding:8px">Chest (cm)</th><th style="padding:8px">Length (cm)</th>
</tr></thead>
<tbody>
""" + "".join(
    f'<tr style="background:{"#fff" if i % 2 == 0 else "#f0f0f0"}"><td style="padding:8px;font-weight:bold">{s[0]}</td><td style="padding:8px">{s[1]}</td><td style="padding:8px">{s[2]}</td><td style="padding:8px">{s[3]}</td></tr>\n'
    for i, s in enumerate(MALE_SIZES)
) + """</tbody></table>

<h4 style="margin:16px 0 8px;font-size:15px">Women's</h4>
<table style="width:100%;border-collapse:collapse;font-size:14px;text-align:center">
<thead><tr style="background:#222;color:#fff">
<th style="padding:8px">Size</th><th style="padding:8px">EU</th><th style="padding:8px">Chest (cm)</th><th style="padding:8px">Length (cm)</th>
</tr></thead>
<tbody>
""" + "".join(
    f'<tr style="background:{"#fff" if i % 2 == 0 else "#f0f0f0"}"><td style="padding:8px;font-weight:bold">{s[0]}</td><td style="padding:8px">{s[1]}</td><td style="padding:8px">{s[2]}</td><td style="padding:8px">{s[3]}</td></tr>\n'
    for i, s in enumerate(FEMALE_SIZES)
) + """</tbody></table>

<p style="margin:12px 0 0;font-size:13px;color:#777">💡 Tip: If you're between sizes, we recommend sizing up for a relaxed fit.</p>
</div>"""

# ── Greek HTML ──────────────────────────────────────────────────────────────

SIZE_GUIDE_GR = """<div class="size-guide" style="margin:24px 0;padding:20px;background:#f8f8f8;border-radius:8px;font-family:sans-serif">
<h3 style="margin:0 0 16px;font-size:18px">📏 Οδηγός Μεγεθών</h3>
<p style="margin:0 0 12px;font-size:14px;color:#555">Όλες οι μετρήσεις σε εκατοστά (cm). Μετρήστε ένα μπλουζάκι που σας ταιριάζει και συγκρίνετε.</p>

<h4 style="margin:16px 0 8px;font-size:15px">Ανδρικά / Unisex</h4>
<table style="width:100%;border-collapse:collapse;font-size:14px;text-align:center">
<thead><tr style="background:#222;color:#fff">
<th style="padding:8px">Μέγεθος</th><th style="padding:8px">EU</th><th style="padding:8px">Στήθος (cm)</th><th style="padding:8px">Μήκος (cm)</th>
</tr></thead>
<tbody>
""" + "".join(
    f'<tr style="background:{"#fff" if i % 2 == 0 else "#f0f0f0"}"><td style="padding:8px;font-weight:bold">Μέγεθος {s[0]} ({s[1]})</td><td style="padding:8px">{s[1]}</td><td style="padding:8px">{s[2]}</td><td style="padding:8px">{s[3]}</td></tr>\n'
    for i, s in enumerate(MALE_SIZES)
) + """</tbody></table>

<h4 style="margin:16px 0 8px;font-size:15px">Γυναικεία</h4>
<table style="width:100%;border-collapse:collapse;font-size:14px;text-align:center">
<thead><tr style="background:#222;color:#fff">
<th style="padding:8px">Μέγεθος</th><th style="padding:8px">EU</th><th style="padding:8px">Στήθος (cm)</th><th style="padding:8px">Μήκος (cm)</th>
</tr></thead>
<tbody>
""" + "".join(
    f'<tr style="background:{"#fff" if i % 2 == 0 else "#f0f0f0"}"><td style="padding:8px;font-weight:bold">Μέγεθος {s[0]} ({s[1]})</td><td style="padding:8px">{s[1]}</td><td style="padding:8px">{s[2]}</td><td style="padding:8px">{s[3]}</td></tr>\n'
    for i, s in enumerate(FEMALE_SIZES)
) + """</tbody></table>

<p style="margin:12px 0 0;font-size:13px;color:#777">💡 Συμβουλή: Αν είστε ανάμεσα σε δύο μεγέθη, προτείνουμε το μεγαλύτερο για πιο άνετη εφαρμογή.</p>
</div>"""

# ── Greek translations for existing metafields ──────────────────────────────

METAFIELD_TRANSLATIONS_GR = {
    "period_shipping": (
        "- Οι παραγγελίες παραδίδονται εντός 1-2 εργάσιμων ημερών\n"
        "- Εγγύηση επιστροφής χρημάτων 30 ημερών"
    ),
    "periods_pec": (
        "Υλικό: 100% Premium Βαμβάκι\n"
        "Βάρος: 180 GSM\n"
        "Εφαρμογή: Κλασική unisex / Γυναικεία εφαρμοστή\n"
        "Εκτύπωση: Υψηλής ποιότητας DTG (Direct-to-Garment)\n"
        "Μεγέθη: S–5XL (Ανδρικά), S–XL (Γυναικεία)"
    ),
    "period_features": (
        "Premium βαρύ βαμβάκι για μακράς διάρκειας άνεση\n\n"
        "Ζωντανή εκτύπωση DTG που δεν σπάει ούτε ξεθωριάζει\n\n"
        "Προπλυμένο ύφασμα — αληθινό στο μέγεθος"
    ),
    "instructions": (
        "Πλύσιμο στο πλυντήριο σε κρύο νερό, ανάποδα, με παρόμοια χρώματα.\n\n"
        "Μην χρησιμοποιείτε λευκαντικό ή στεγνωτήριο.\n\n"
        "Σιδέρωμα σε χαμηλή θερμοκρασία, αποφεύγοντας την τυπωμένη περιοχή.\n\n"
        "Στέγνωμα σε κρεμάστρα για καλύτερα αποτελέσματα."
    ),
}


async def main():
    import httpx
    from app.shopify_product_creator import _admin_url, _headers
    from app.config import settings

    headers = _headers()
    graphql_url = f"https://{settings.omg_shopify_domain}/admin/api/2024-01/graphql.json"

    digest_query = """
    query ($resourceId: ID!) {
      translatableResource(resourceId: $resourceId) {
        translatableContent { key value digest }
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

    async with httpx.AsyncClient() as client:
        # ── Fetch all products ──────────────────────────────────────────
        all_products = []
        url = _admin_url("products.json?limit=250")
        while url:
            r = await client.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            all_products.extend(r.json().get("products", []))
            link = r.headers.get("link", "")
            url = None
            if 'rel="next"' in link:
                for part in link.split(","):
                    if 'rel="next"' in part:
                        url = part.split("<")[1].split(">")[0]

        # Filter t-shirts
        tshirt_keywords = ["tee", "t-shirt", "tshirt", "shirt"]
        tshirts = [
            p for p in all_products
            if any(kw in p.get("product_type", "").lower() for kw in tshirt_keywords)
            or any(kw in p.get("handle", "").lower() for kw in tshirt_keywords)
            or any("size" in (opt.get("name", "").lower()) for opt in p.get("options", []))
        ]
        if not tshirts:
            tshirts = all_products

        print(f"Found {len(tshirts)} t-shirt products out of {len(all_products)} total\n")

        # ── Step 1: Add size guide to body_html ─────────────────────────
        print("=" * 60)
        print("Step 1: Appending English size guide to product descriptions")
        print("=" * 60)

        for p in tshirts:
            body = p.get("body_html") or ""

            if "size-guide" in body:
                logger.info(f"  Already has size guide: {p['handle']}")
                continue

            new_body = body + "\n" + SIZE_GUIDE_EN

            r = await client.put(
                _admin_url(f"products/{p['id']}.json"),
                headers=headers,
                json={"product": {"id": p["id"], "body_html": new_body}},
                timeout=15,
            )
            r.raise_for_status()
            logger.info(f"  Size guide added: {p['handle']}")

        # ── Step 2: Set size_guide metafield ────────────────────────────
        print("\n" + "=" * 60)
        print("Step 2: Setting custom.size_guide metafield (English)")
        print("=" * 60)

        for p in tshirts:
            r = await client.post(
                _admin_url(f"products/{p['id']}/metafields.json"),
                headers=headers,
                json={"metafield": {
                    "namespace": "custom",
                    "key": "size_guide",
                    "value": SIZE_GUIDE_EN,
                    "type": "multi_line_text_field",
                }},
                timeout=15,
            )
            if r.status_code == 422:
                # Already exists — update instead
                # Fetch existing metafield ID
                mr = await client.get(
                    _admin_url(f"products/{p['id']}/metafields.json?namespace=custom&key=size_guide"),
                    headers=headers, timeout=15,
                )
                mr.raise_for_status()
                existing = mr.json().get("metafields", [])
                if existing:
                    mf_id = existing[0]["id"]
                    r = await client.put(
                        _admin_url(f"products/{p['id']}/metafields/{mf_id}.json"),
                        headers=headers,
                        json={"metafield": {"id": mf_id, "value": SIZE_GUIDE_EN}},
                        timeout=15,
                    )
                    r.raise_for_status()
                    logger.info(f"  Size guide metafield updated: {p['handle']}")
                else:
                    logger.warning(f"  Could not find/update size_guide metafield for {p['handle']}")
            else:
                r.raise_for_status()
                logger.info(f"  Size guide metafield set: {p['handle']}")

        # ── Step 3: Register Greek translation for body_html ────────────
        print("\n" + "=" * 60)
        print("Step 3: Registering Greek body_html translation (with size guide)")
        print("=" * 60)

        for p in tshirts:
            product_gid = f"gid://shopify/Product/{p['id']}"

            # Get digest for body_html
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
            digest_map = {f["key"]: f for f in digest_data}

            translations = []

            # Greek body_html — take existing English body, replace size guide section
            body_field = digest_map.get("body_html")
            if body_field and body_field.get("value"):
                en_body = body_field["value"]
                # Replace the English size guide with Greek, or append Greek
                if "size-guide" in en_body:
                    # Split at size guide div and replace
                    parts = en_body.split('<div class="size-guide"')
                    if len(parts) == 2:
                        gr_body = parts[0] + SIZE_GUIDE_GR
                    else:
                        gr_body = en_body + "\n" + SIZE_GUIDE_GR
                else:
                    gr_body = en_body + "\n" + SIZE_GUIDE_GR

                translations.append({
                    "key": "body_html",
                    "value": gr_body,
                    "locale": "el",
                    "translatableContentDigest": body_field["digest"],
                })

            if translations:
                r = await client.post(
                    graphql_url, headers=headers,
                    json={"query": mutation, "variables": {
                        "resourceId": product_gid,
                        "translations": translations,
                    }},
                    timeout=15,
                )
                r.raise_for_status()
                errors = r.json().get("data", {}).get("translationsRegister", {}).get("userErrors", [])
                if errors:
                    logger.warning(f"  body_html translation error for {p['handle']}: {errors}")
                else:
                    logger.info(f"  Greek body_html set: {p['handle']}")
            else:
                logger.warning(f"  No body_html digest for {p['handle']}")

        # ── Step 4: Register Greek translations for metafields ──────────
        print("\n" + "=" * 60)
        print("Step 4: Translating metafields (shipping, specs, features, care)")
        print("=" * 60)

        # Metafields are separate translatable resources — need to find them
        # Query all METAFIELD translatable resources
        metafield_query = """
        query($type: TranslatableResourceType!, $first: Int!, $cursor: String) {
            translatableResources(resourceType: $type, first: $first, after: $cursor) {
                edges {
                    node {
                        resourceId
                        translatableContent { key value digest }
                        translations(locale: "el") { key value outdated }
                    }
                }
                pageInfo { hasNextPage endCursor }
            }
        }
        """

        cursor = None
        metafield_resources = []
        while True:
            variables = {"type": "METAFIELD", "first": 50}
            if cursor:
                variables["cursor"] = cursor
            r = await client.post(
                graphql_url, headers=headers,
                json={"query": metafield_query, "variables": variables},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json().get("data", {}).get("translatableResources", {})
            edges = data.get("edges", [])
            metafield_resources.extend(edges)
            page_info = data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info["endCursor"]

        print(f"  Found {len(metafield_resources)} translatable metafield resources")

        translated_count = 0
        for edge in metafield_resources:
            node = edge["node"]
            content = node.get("translatableContent", [])
            existing_trans = {t["key"]: t for t in node.get("translations", [])}

            for field in content:
                if field["key"] != "value" or not field.get("value"):
                    continue

                value = field["value"]
                # Match against our known metafield values
                gr_value = None
                for mf_key, gr_text in METAFIELD_TRANSLATIONS_GR.items():
                    # Check if this metafield value matches one of our known English values
                    if mf_key == "period_shipping" and "delivered within" in value:
                        gr_value = gr_text
                        break
                    elif mf_key == "periods_pec" and "180 GSM" in value:
                        gr_value = gr_text
                        break
                    elif mf_key == "period_features" and "heavyweight cotton" in value:
                        gr_value = gr_text
                        break
                    elif mf_key == "instructions" and "Machine wash cold" in value:
                        gr_value = gr_text
                        break

                if not gr_value:
                    continue

                # Check if already translated
                trans = existing_trans.get("value")
                if trans and trans.get("value") and not trans.get("outdated"):
                    continue

                r = await client.post(
                    graphql_url, headers=headers,
                    json={"query": mutation, "variables": {
                        "resourceId": node["resourceId"],
                        "translations": [{
                            "key": "value",
                            "value": gr_value,
                            "locale": "el",
                            "translatableContentDigest": field["digest"],
                        }],
                    }},
                    timeout=15,
                )
                r.raise_for_status()
                errors = r.json().get("data", {}).get("translationsRegister", {}).get("userErrors", [])
                if errors:
                    logger.warning(f"  Metafield translation error {node['resourceId']}: {errors}")
                else:
                    translated_count += 1
                    logger.info(f"  Translated metafield: {node['resourceId']}")

        print(f"  Translated {translated_count} metafield values")

        # ── Step 5: Register Greek size_guide metafield translation ─────
        print("\n" + "=" * 60)
        print("Step 5: Registering Greek translation for size_guide metafield")
        print("=" * 60)

        # Re-scan metafield resources for size_guide (just added in step 2)
        for edge in metafield_resources:
            node = edge["node"]
            content = node.get("translatableContent", [])
            for field in content:
                if field["key"] == "value" and field.get("value") and "size-guide" in field["value"]:
                    r = await client.post(
                        graphql_url, headers=headers,
                        json={"query": mutation, "variables": {
                            "resourceId": node["resourceId"],
                            "translations": [{
                                "key": "value",
                                "value": SIZE_GUIDE_GR,
                                "locale": "el",
                                "translatableContentDigest": field["digest"],
                            }],
                        }},
                        timeout=15,
                    )
                    r.raise_for_status()
                    errors = r.json().get("data", {}).get("translationsRegister", {}).get("userErrors", [])
                    if errors:
                        logger.warning(f"  size_guide translation error {node['resourceId']}: {errors}")
                    else:
                        logger.info(f"  Greek size_guide metafield translated: {node['resourceId']}")

        # ── Summary ─────────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("DONE! Summary:")
        print(f"  T-shirt products processed: {len(tshirts)}")
        print(f"  English size guide added to body_html")
        print(f"  custom.size_guide metafield set (EN)")
        print(f"  Greek translations registered for body_html + metafields")
        print(f"  Metafield translations: {translated_count}")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
