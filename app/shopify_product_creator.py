"""Create products on the OMG Shopify store via Admin API."""
import base64
import logging
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ADMIN_API_VERSION = "2024-01"

# Shipping profile for Cyprus delivery — products must be added here to be purchasable
# From: https://admin.shopify.com/store/52922c-2/settings/shipping/profiles/120742379801
OMG_SHIPPING_PROFILE_ID = 120742379801

# T-Shirts collection — all tees must be added here
# From: https://omg.com.cy/collections/t-shirts
OMG_TSHIRTS_COLLECTION_ID = 451595010329

# Category collections — products auto-added based on tags/variants
CATEGORY_COLLECTIONS = {
    "mens": 683599987068,       # All products with Male variants
    "womens": 683599954300,     # All products with Female variants
    "geeky": 683599921532,      # Tags: geeky, programmer, coding, nerd, tech, gaming
    "slogan-tees": 683602674044, # Tags: slogan, typography, quote, bold, text tee
    "cyprus-tees": 683597857148, # Tags: cyprus, astous, κύπρος, cypriot
    "local-designs": 683600019836, # Tags: cyprus, local, astous, mediterranean
    "summer-tees": 683678204284,  # Tags: summer, beach, tropical, sun, vacation, sea
    "feminine-tees": 683679842684, # Curated home for trending feminine designs (separate from the catch-all "womens")
}

# Tag keywords that map products to category collections
COLLECTION_TAG_RULES = {
    "geeky": {"geeky", "programmer", "coding", "nerd", "tech", "gaming", "geek", "developer", "404", "debug", "code"},
    "slogan-tees": {"slogan", "typography", "quote", "text tee", "energy", "overthinker", "main character", "no cap"},
    "cyprus-tees": {"cyprus", "astous", "cypriot", "κύπρος", "limassol", "nicosia", "ayia napa"},
    "local-designs": {"cyprus", "local", "astous", "cypriot", "mediterranean", "κύπρος"},
    "summer-tees": {"summer", "beach", "tropical", "sun", "sunset", "ocean", "sea", "vacation", "holiday", "palm", "surf", "καλοκαίρι"},
    "feminine-tees": {
        # Aesthetic-specific keywords only — avoid generic "women" / "pink" which
        # would cross-pollute since every OMG tee has female variants. The
        # `feminine` tag is the canonical signal Mango's feminine concept type emits.
        "feminine", "femme", "girly", "coquette", "ballet core", "ballet-core",
        "cottagecore", "soft girl", "soft-girl", "clean girl", "that girl",
        "dreamy", "vintage romance", "blush palette",
        "ribbon", "pearl", "bow",
    },
}

# Standard metafields for all t-shirt products
TSHIRT_METAFIELDS = [
    {
        "namespace": "custom",
        "key": "units_sold",
        "value": "100+",
        "type": "single_line_text_field",
    },
    {
        "namespace": "custom",
        "key": "period_shipping",
        "value": "- Orders are delivered within 1-2 business days\n- Backed by our 30-day money back guarantee",
        "type": "multi_line_text_field",
    },
    {
        "namespace": "custom",
        "key": "periods_pec",
        "value": "Material: 100% Premium Cotton\nWeight: 180 GSM\nFit: Classic unisex / Women's fitted\nPrint: High-quality DTG (Direct-to-Garment)\nSizes: S–5XL (Male), S–XL (Female)",
        "type": "multi_line_text_field",
    },
    {
        "namespace": "custom",
        "key": "period_features",
        "value": "Premium heavyweight cotton for lasting comfort\n\nVibrant DTG print that won't crack or fade\n\nPre-shrunk fabric — true to size",
        "type": "multi_line_text_field",
    },
    {
        "namespace": "custom",
        "key": "instructions",
        "value": "Machine wash cold inside out with similar colours.\n\nDo not bleach or tumble dry.\n\nIron on low heat, avoiding the printed area.\n\nHang dry for best results.",
        "type": "multi_line_text_field",
    },
    {
        "namespace": "custom",
        "key": "size_guide",
        "value": (
            "Men's/Unisex: S(36-38) 91x71cm | M(40-42) 96x74cm | L(44-46) 101x76cm | "
            "XL(48-50) 106x79cm | 2XL(52-54) 111x81cm | 3XL(56-58) 117x84cm | "
            "4XL(60-62) 122x86cm | 5XL(64-66) 127x89cm\n"
            "Women's: S(36-38) 82x63cm | M(40-42) 86x65cm | L(44-46) 91x67cm | XL(48-50) 96x69cm\n"
            "Measurements: Chest x Length in cm"
        ),
        "type": "multi_line_text_field",
    },
]

# Variants: Gender x Placement x Size with pricing
# Male sizes S-5XL, Female sizes S-XL; each duplicated for Front/Back placement.
# 24 variants total (under Shopify's 100-variant limit).
# inventory_management="shopify" + policy="continue" keeps them always purchasable (print-on-demand).
_SIZE_PRICES = {
    "Male": [("S", "25.00"), ("M", "25.00"), ("L", "25.00"), ("XL", "25.00"),
             ("2XL", "35.00"), ("3XL", "37.00"), ("4XL", "39.50"), ("5XL", "39.50")],
    "Female": [("S", "25.00"), ("M", "25.00"), ("L", "25.00"), ("XL", "25.00")],
}

VARIANTS = [
    {
        "option1": gender,
        "option2": placement,
        "option3": size,
        "price": price,
        "inventory_management": "shopify",
        "inventory_policy": "continue",
    }
    for gender in ("Male", "Female")
    for placement in ("Front", "Back")
    for size, price in _SIZE_PRICES[gender]
]

# TShirtJunkies target product IDs for mapping
TJ_PRODUCTS = {
    "male": {
        "handle": "classic-tee-up-to-5xl",
        "product_id": 9864408301915,
    },
    "female": {
        "handle": "women-t-shirt",
        "product_id": 8676301799771,
    },
}


def _admin_url(path: str) -> str:
    domain = settings.omg_shopify_domain
    if not domain.endswith(".myshopify.com"):
        domain = "52922c-2.myshopify.com"
    return f"https://{domain}/admin/api/{ADMIN_API_VERSION}/{path}"


def _headers() -> dict:
    return {
        "X-Shopify-Access-Token": settings.omg_shopify_admin_token,
        "Content-Type": "application/json",
    }


async def create_product(
    title: str,
    body_html: str,
    tags: str = "",
    image_path: Path | None = None,
    published: bool = True,
) -> dict:
    """Create a product on OMG Shopify with Male/Female + Size variants.

    All products get both male (S-5XL) and female (S-XL) variants.
    Inventory tracking is disabled (print-on-demand — always available).
    Design image is NOT uploaded here — it's added last after mockups.
    """
    variants = [{**v} for v in VARIANTS]

    product_data = {
        "product": {
            "title": title,
            "body_html": body_html,
            "vendor": "OMG",
            "product_type": "T-Shirt",
            "tags": tags,
            "published": published,
            "options": [{"name": "Gender"}, {"name": "Placement"}, {"name": "Size"}],
            "variants": variants,
        }
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _admin_url("products.json"),
            headers=_headers(),
            json=product_data,
            timeout=60,
        )
        resp.raise_for_status()
        product = resp.json().get("product", {})
        logger.info(f"Created product: {product.get('id')} — {title} ({len(product.get('variants', []))} variants)")

    # Ensure all variants are purchasable by setting inventory
    await _ensure_inventory_available(product)

    # Add product to the Cyprus shipping profile so it's not "sold out"
    await _add_to_shipping_profile(product)

    # Add to T-Shirts collection
    await _add_to_collection(product, OMG_TSHIRTS_COLLECTION_ID)

    # Auto-categorize into mens/womens/geeky/cyprus/local collections
    await _auto_categorize(product, tags)

    # Set standard t-shirt metafields
    await _set_tshirt_metafields(product)

    return product


async def _ensure_inventory_available(product: dict) -> None:
    """Set inventory_policy=continue on all variants and set stock to 999.

    This prevents the 'sold out' issue on print-on-demand products.
    """
    product_id = product.get("id")
    if not product_id:
        return

    async with httpx.AsyncClient() as client:
        for v in product.get("variants", []):
            vid = v.get("id")
            if not vid:
                continue

            # Update variant: set inventory_management to "shopify" so we can control stock,
            # and inventory_policy to "continue" as safety net
            try:
                resp = await client.put(
                    _admin_url(f"variants/{vid}.json"),
                    headers=_headers(),
                    json={"variant": {"id": vid, "inventory_management": "shopify", "inventory_policy": "continue"}},
                    timeout=30,
                )
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"Failed to update variant {vid}: {e}")

            # Set inventory level to 999
            inventory_item_id = v.get("inventory_item_id")
            if not inventory_item_id:
                # Re-fetch variant to get inventory_item_id (may not be in the original response)
                try:
                    vr = await client.get(_admin_url(f"variants/{vid}.json"), headers=_headers(), timeout=30)
                    vr.raise_for_status()
                    inventory_item_id = vr.json().get("variant", {}).get("inventory_item_id")
                except Exception:
                    pass
            if not inventory_item_id:
                continue

            try:
                # Get location_id from existing inventory levels
                # (locations.json endpoint requires read_locations scope which we may not have)
                if not hasattr(_ensure_inventory_available, "_location_id") or not _ensure_inventory_available._location_id:
                    lvl_resp = await client.get(
                        _admin_url(f"inventory_levels.json?inventory_item_ids={inventory_item_id}"),
                        headers=_headers(),
                        timeout=30,
                    )
                    lvl_resp.raise_for_status()
                    levels = lvl_resp.json().get("inventory_levels", [])
                    if levels:
                        _ensure_inventory_available._location_id = levels[0]["location_id"]
                location_id = getattr(_ensure_inventory_available, "_location_id", None)
                if not location_id:
                    logger.warning(f"No location_id found for variant {vid}")
                    continue

                # Now set the level
                resp = await client.post(
                    _admin_url("inventory_levels/set.json"),
                    headers=_headers(),
                    json={
                        "location_id": location_id,
                        "inventory_item_id": inventory_item_id,
                        "available": 999,
                    },
                    timeout=30,
                )
                if resp.status_code < 400:
                    logger.info(f"Set inventory for variant {vid} to 999")
                else:
                    logger.warning(f"Failed to set inventory for {vid}: {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"Failed to set inventory for variant {vid}: {e}")

    logger.info(f"Ensured inventory available for product {product_id}")


async def _add_to_shipping_profile(product: dict) -> None:
    """Add a product to the OMG Cyprus shipping profile via GraphQL.

    Without this, products show as 'sold out' because they have no shipping rates.
    """
    product_id = product.get("id")
    if not product_id:
        return

    graphql_url = f"https://{settings.omg_shopify_domain}/admin/api/{ADMIN_API_VERSION}/graphql.json"
    headers = _headers()

    profile_gid = f"gid://shopify/DeliveryProfile/{OMG_SHIPPING_PROFILE_ID}"

    # Use deliveryProfileUpdate to add the product to the shipping profile
    mutation = """
    mutation deliveryProfileUpdate($id: ID!, $profile: DeliveryProfileInput!) {
      deliveryProfileUpdate(id: $id, profile: $profile) {
        profile {
          id
        }
        userErrors {
          field
          message
        }
      }
    }
    """

    variables = {
        "id": profile_gid,
        "profile": {
            "variantsToAssociate": [
                f"gid://shopify/ProductVariant/{v['id']}"
                for v in product.get("variants", []) if v.get("id")
            ],
        },
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            graphql_url,
            headers=headers,
            json={"query": mutation, "variables": variables},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        # Check for top-level GraphQL errors
        if data.get("errors"):
            logger.warning(f"Shipping profile GraphQL errors for product {product_id}: {data['errors']}")
            return
        user_errors = data.get("data", {}).get("deliveryProfileUpdate", {}).get("userErrors", [])
        if user_errors:
            logger.warning(f"Shipping profile user errors for product {product_id}: {user_errors}")
        else:
            logger.info(f"Added product {product_id} to shipping profile {OMG_SHIPPING_PROFILE_ID}")


async def _add_to_collection(product: dict, collection_id: int) -> None:
    """Add a product to a Shopify custom collection."""
    product_id = product.get("id")
    if not product_id:
        return

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _admin_url("collects.json"),
            headers=_headers(),
            json={"collect": {"product_id": product_id, "collection_id": collection_id}},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            logger.info(f"Added product {product_id} to collection {collection_id}")
        elif resp.status_code == 422:
            logger.info(f"Product {product_id} already in collection {collection_id}")
        else:
            logger.warning(f"Failed to add product {product_id} to collection {collection_id}: {resp.status_code} {resp.text[:200]}")


async def _auto_categorize(product: dict, tags: str) -> None:
    """Auto-add product to category collections based on tags and variants."""
    product_id = product.get("id")
    if not product_id:
        return

    tag_set = {t.strip().lower() for t in tags.split(",") if t.strip()}
    # Also check product handle/title for keyword matches
    handle = product.get("handle", "").lower()
    title = product.get("title", "").lower()
    all_text = tag_set | {handle, title}

    # Always add to mens and womens (all our tees have both genders)
    variants = product.get("variants", [])
    has_male = any(v.get("option1", "").lower() == "male" for v in variants)
    has_female = any(v.get("option1", "").lower() == "female" for v in variants)

    if has_male and "mens" in CATEGORY_COLLECTIONS:
        await _add_to_collection(product, CATEGORY_COLLECTIONS["mens"])
    if has_female and "womens" in CATEGORY_COLLECTIONS:
        await _add_to_collection(product, CATEGORY_COLLECTIONS["womens"])

    # Tag-based collections
    for collection_key, keywords in COLLECTION_TAG_RULES.items():
        if collection_key not in CATEGORY_COLLECTIONS:
            continue
        # Match if any keyword appears in tags, handle, or title
        if any(kw in tag for tag in all_text for kw in keywords):
            await _add_to_collection(product, CATEGORY_COLLECTIONS[collection_key])

    logger.info(f"Auto-categorized product {product_id}")


async def _set_tshirt_metafields(product: dict) -> None:
    """Set standard t-shirt metafields on a product."""
    product_id = product.get("id")
    if not product_id:
        return

    async with httpx.AsyncClient() as client:
        for mf in TSHIRT_METAFIELDS:
            resp = await client.post(
                _admin_url(f"products/{product_id}/metafields.json"),
                headers=_headers(),
                json={"metafield": mf},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                logger.info(f"Set metafield {mf['key']} on product {product_id}")
            else:
                logger.warning(f"Failed to set metafield {mf['key']} on product {product_id}: {resp.status_code} {resp.text[:200]}")


async def add_products_to_shipping_profile(product_ids: list[int]) -> list[dict]:
    """Add multiple existing products to the Cyprus shipping profile. Returns results per product."""
    results = []
    for pid in product_ids:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                _admin_url(f"products/{pid}.json"),
                headers=_headers(),
                timeout=30,
            )
            resp.raise_for_status()
            product = resp.json().get("product", {})
        await _add_to_shipping_profile(product)
        results.append({"product_id": pid, "title": product.get("title", ""), "status": "added"})
    return results


async def fix_sold_out_product(product_id: int) -> dict:
    """Fix an existing sold-out product by updating all variant inventory policies."""
    async with httpx.AsyncClient() as client:
        # Fetch the product
        resp = await client.get(
            _admin_url(f"products/{product_id}.json"),
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        product = resp.json().get("product", {})

    await _ensure_inventory_available(product)
    await _add_to_shipping_profile(product)
    return {"product_id": product_id, "variants_fixed": len(product.get("variants", []))}


async def upload_product_image(
    product_id: int,
    image_path: Path,
    alt: str = "",
    variant_ids: list[int] | None = None,
) -> dict:
    """Upload an additional image to an existing product.

    If variant_ids is provided, the image is linked to those variants — picking
    one of them on the product page swaps the gallery to this image.
    """
    img_bytes = image_path.read_bytes()
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    image_payload: dict = {
        "attachment": img_b64,
        "filename": image_path.name,
        "alt": alt,
    }
    if variant_ids:
        image_payload["variant_ids"] = variant_ids

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _admin_url(f"products/{product_id}/images.json"),
            headers=_headers(),
            json={"image": image_payload},
            timeout=60,
        )
        resp.raise_for_status()
        img = resp.json().get("image", {})

        # Shopify's POST /images intermittently drops variant_ids (observed
        # May 2026: 5/8 uploads in a refresh run lost variant linking).
        # Follow up with a PUT whenever the returned variant_ids don't match
        # what we asked for, so the gender/placement gallery swap works.
        if variant_ids and set(img.get("variant_ids") or []) != set(variant_ids):
            try:
                put_resp = await client.put(
                    _admin_url(f"products/{product_id}/images/{img['id']}.json"),
                    headers=_headers(),
                    json={"image": {"id": img["id"], "variant_ids": variant_ids}},
                    timeout=30,
                )
                if put_resp.status_code < 400:
                    img = put_resp.json().get("image", img)
                else:
                    logger.warning(
                        f"variant_ids PUT failed for image {img.get('id')}: "
                        f"{put_resp.status_code} {put_resp.text[:200]}"
                    )
            except Exception as e:
                logger.warning(f"variant_ids PUT error for image {img.get('id')}: {e}")

        return img


async def create_mappings_for_product(
    omg_product: dict,
    design_image: str = "front_design.png",
    color: str = "White",
) -> list[dict]:
    """Create product mappings: male variants → TJ Classic Tee, female → TJ Women's Tee.

    Handles both the legacy 2-option schema (Gender + Size — option1, option2) and
    the new 3-option schema (Gender + Placement + Size — option1, option2, option3).
    Placement is encoded in the source_title so the webhook handler can thread it
    through to Qstomizer.
    """
    from app.mapper import load_mappings, save_mappings
    from app.models import ProductMapping, VariantMapping
    from app.shopify_client import fetch_product_by_handle

    # Detect schema: new 3-option products have option2 = "Front"/"Back"
    has_placement = any(
        (v.get("option2") or "").lower() in ("front", "back")
        for v in omg_product.get("variants", [])
    )

    # Group OMG variants by gender
    male_variants = []
    female_variants = []
    for v in omg_product.get("variants", []):
        gender = (v.get("option1") or "").lower()
        if "female" in gender:
            female_variants.append(v)
        else:
            male_variants.append(v)

    mappings = []
    for gender, omg_variants, tj_key in [
        ("male", male_variants, "male"),
        ("female", female_variants, "female"),
    ]:
        if not omg_variants:
            continue

        tj_info = TJ_PRODUCTS[tj_key]
        tj_product = await fetch_product_by_handle(
            settings.tshirtjunkies_base_url, tj_info["handle"]
        )
        if not tj_product:
            logger.warning(f"Could not fetch TJ product: {tj_info['handle']}")
            continue

        # TJ variants are keyed by size (option1 on TJ)
        tj_by_size = {v.get("option1", ""): v for v in tj_product.get("variants", [])}

        variant_mappings = []
        for omg_v in omg_variants:
            if has_placement:
                placement = omg_v.get("option2", "Front")
                size = omg_v.get("option3", "")
                source_title = f"{omg_v.get('option1', '')} / {placement} / {size}"
            else:
                placement = None
                size = omg_v.get("option2", "")
                source_title = f"{omg_v.get('option1', '')} / {size}"

            tj_v = tj_by_size.get(size)
            if tj_v:
                variant_mappings.append(VariantMapping(
                    source_variant_id=omg_v["id"],
                    source_title=source_title,
                    target_variant_id=tj_v["id"],
                    target_title=size,
                    target_price=str(tj_v.get("price", "0")),
                ))

        mapping = ProductMapping(
            source_handle=omg_product["handle"],
            source_title=omg_product["title"],
            target_handle=tj_info["handle"],
            target_title=tj_product.get("title", tj_info["handle"]),
            target_product_id=tj_info["product_id"],
            variants=variant_mappings,
            design_image=design_image,
            color=color,
        )
        mappings.append(mapping)
        logger.info(
            f"Mapping: {omg_product['handle']} ({gender}"
            f"{', with placement' if has_placement else ''}) "
            f"→ {tj_info['handle']} ({len(variant_mappings)} variants)"
        )

    # Save mappings — replace any existing ones for the same source+target handle pair
    config = load_mappings()
    new_keys = {(m.source_handle, m.target_handle) for m in mappings}
    config.mappings = [
        m for m in config.mappings
        if (m.source_handle, m.target_handle) not in new_keys
    ]
    config.mappings.extend(mappings)
    save_mappings(config)

    return [m.model_dump() for m in mappings]


async def fetch_mockup_from_qstomizer(
    design_image_path: str,
    product_type: str = "male",
    size: str = "L",
    placement: str = "front",
    color: str = "White",
    vertical_offset: float = -0.25,
    horizontal_offset: float = 0.0,
    vertical_safety_pad_px: int = 4,
) -> str | None:
    """Run Qstomizer automation and return the mockup image URL.

    The mockup is a rendered t-shirt image from TShirtJunkies/Qstomizer
    that shows what the printed product will look like.
    """
    from app.qstomizer_automation import customize_and_add_to_cart

    try:
        result = await customize_and_add_to_cart(
            product_type=product_type,
            size=size,
            color=color,
            image_path=design_image_path,
            quantity=1,
            headless=True,
            placement=placement,
            vertical_offset=vertical_offset,
            horizontal_offset=horizontal_offset,
            vertical_safety_pad_px=vertical_safety_pad_px,
        )
        mockup_url = result.get("mockup_url")
        if mockup_url:
            logger.info(f"Got {product_type} {placement} {color} mockup: {mockup_url}")
        return mockup_url
    except Exception as e:
        logger.error(f"Failed to get {product_type} {color} mockup: {e}")
        return None


async def download_image(url: str, dest: Path) -> Path:
    """Download an image from URL to a local file."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return dest
