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
]

# Variants: Gender x Size with pricing
# Male sizes S-5XL, Female sizes S-XL
# inventory_management=null means Shopify won't track stock (always available) — correct for print-on-demand
VARIANTS = [
    # Male — inventory_management="shopify" so we can set stock levels; policy="continue" as fallback
    {"option1": "Male", "option2": "S", "price": "30.00", "inventory_management": "shopify", "inventory_policy": "continue"},
    {"option1": "Male", "option2": "M", "price": "30.00", "inventory_management": "shopify", "inventory_policy": "continue"},
    {"option1": "Male", "option2": "L", "price": "30.00", "inventory_management": "shopify", "inventory_policy": "continue"},
    {"option1": "Male", "option2": "XL", "price": "30.00", "inventory_management": "shopify", "inventory_policy": "continue"},
    {"option1": "Male", "option2": "2XL", "price": "35.00", "inventory_management": "shopify", "inventory_policy": "continue"},
    {"option1": "Male", "option2": "3XL", "price": "37.00", "inventory_management": "shopify", "inventory_policy": "continue"},
    {"option1": "Male", "option2": "4XL", "price": "39.50", "inventory_management": "shopify", "inventory_policy": "continue"},
    {"option1": "Male", "option2": "5XL", "price": "39.50", "inventory_management": "shopify", "inventory_policy": "continue"},
    # Female
    {"option1": "Female", "option2": "S", "price": "30.00", "inventory_management": "shopify", "inventory_policy": "continue"},
    {"option1": "Female", "option2": "M", "price": "30.00", "inventory_management": "shopify", "inventory_policy": "continue"},
    {"option1": "Female", "option2": "L", "price": "30.00", "inventory_management": "shopify", "inventory_policy": "continue"},
    {"option1": "Female", "option2": "XL", "price": "30.00", "inventory_management": "shopify", "inventory_policy": "continue"},
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
            "options": [{"name": "Gender"}, {"name": "Size"}],
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


async def upload_product_image(product_id: int, image_path: Path, alt: str = "") -> dict:
    """Upload an additional image to an existing product."""
    img_bytes = image_path.read_bytes()
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _admin_url(f"products/{product_id}/images.json"),
            headers=_headers(),
            json={"image": {"attachment": img_b64, "filename": image_path.name, "alt": alt}},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("image", {})


async def create_mappings_for_product(
    omg_product: dict,
    design_image: str = "front_design.png",
) -> list[dict]:
    """Create TWO product mappings: male variants → TJ Classic Tee, female variants → TJ Women's Tee.

    OMG product has Gender+Size options (e.g. "Male / L", "Female / S").
    Each gender maps to a different TJ product.
    """
    from app.mapper import load_mappings, save_mappings
    from app.models import ProductMapping, VariantMapping
    from app.shopify_client import fetch_product_by_handle

    # Group OMG variants by gender
    male_variants = []
    female_variants = []
    for v in omg_product.get("variants", []):
        gender = v.get("option1", "").lower()
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

        # Match by size (option2 on OMG, option1 on TJ)
        tj_by_size = {v.get("option1", ""): v for v in tj_product.get("variants", [])}

        variant_mappings = []
        for omg_v in omg_variants:
            size = omg_v.get("option2", "")
            tj_v = tj_by_size.get(size)
            if tj_v:
                variant_mappings.append(VariantMapping(
                    source_variant_id=omg_v["id"],
                    source_title=f"{omg_v.get('option1', '')} / {size}",
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
        )
        mappings.append(mapping)
        logger.info(f"Mapping: {omg_product['handle']} ({gender}) → {tj_info['handle']} ({len(variant_mappings)} variants)")

    # Save all mappings
    config = load_mappings()
    config.mappings.extend(mappings)
    save_mappings(config)

    return [m.model_dump() for m in mappings]


async def fetch_mockup_from_qstomizer(
    design_image_path: str,
    product_type: str = "male",
    size: str = "L",
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
            color="White",
            image_path=design_image_path,
            quantity=1,
            headless=True,
        )
        mockup_url = result.get("mockup_url")
        if mockup_url:
            logger.info(f"Got {product_type} mockup: {mockup_url}")
        return mockup_url
    except Exception as e:
        logger.error(f"Failed to get {product_type} mockup: {e}")
        return None


async def download_image(url: str, dest: Path) -> Path:
    """Download an image from URL to a local file."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return dest
