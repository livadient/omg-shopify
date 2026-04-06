"""Create products on the OMG Shopify store via Admin API."""
import base64
import logging
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ADMIN_API_VERSION = "2024-01"

# Variants: Gender x Size with pricing
# Male sizes S-5XL, Female sizes S-XL
# inventory_management=null means Shopify won't track stock (always available) — correct for print-on-demand
VARIANTS = [
    # Male
    {"option1": "Male", "option2": "S", "price": "30.00", "inventory_management": None, "inventory_policy": "continue"},
    {"option1": "Male", "option2": "M", "price": "30.00", "inventory_management": None, "inventory_policy": "continue"},
    {"option1": "Male", "option2": "L", "price": "30.00", "inventory_management": None, "inventory_policy": "continue"},
    {"option1": "Male", "option2": "XL", "price": "30.00", "inventory_management": None, "inventory_policy": "continue"},
    {"option1": "Male", "option2": "2XL", "price": "35.00", "inventory_management": None, "inventory_policy": "continue"},
    {"option1": "Male", "option2": "3XL", "price": "37.00", "inventory_management": None, "inventory_policy": "continue"},
    {"option1": "Male", "option2": "4XL", "price": "39.50", "inventory_management": None, "inventory_policy": "continue"},
    {"option1": "Male", "option2": "5XL", "price": "39.50", "inventory_management": None, "inventory_policy": "continue"},
    # Female
    {"option1": "Female", "option2": "S", "price": "30.00", "inventory_management": None, "inventory_policy": "continue"},
    {"option1": "Female", "option2": "M", "price": "30.00", "inventory_management": None, "inventory_policy": "continue"},
    {"option1": "Female", "option2": "L", "price": "30.00", "inventory_management": None, "inventory_policy": "continue"},
    {"option1": "Female", "option2": "XL", "price": "30.00", "inventory_management": None, "inventory_policy": "continue"},
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

            # Update variant to ensure inventory_policy is "continue"
            try:
                resp = await client.put(
                    _admin_url(f"variants/{vid}.json"),
                    headers=_headers(),
                    json={"variant": {"id": vid, "inventory_policy": "continue"}},
                    timeout=30,
                )
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"Failed to update variant {vid} inventory_policy: {e}")

            # Set inventory level to 999 via inventory API
            inventory_item_id = v.get("inventory_item_id")
            if not inventory_item_id:
                continue
            try:
                # Get the location ID first
                loc_resp = await client.get(
                    _admin_url("locations.json"),
                    headers=_headers(),
                    timeout=30,
                )
                loc_resp.raise_for_status()
                locations = loc_resp.json().get("locations", [])
                if not locations:
                    continue
                location_id = locations[0]["id"]

                # Set inventory level
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
                # This may fail if inventory_management is null — that's OK
                if resp.status_code < 400:
                    logger.debug(f"Set inventory for variant {vid} to 999")
            except Exception:
                pass  # Not critical — inventory_policy=continue is the main fix

    logger.info(f"Ensured inventory available for product {product_id}")


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
