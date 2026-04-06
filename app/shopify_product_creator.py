"""Create products on the OMG Shopify store via Admin API."""
import base64
import logging
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ADMIN_API_VERSION = "2024-01"

# Standard pricing matching existing OMG products
MALE_VARIANTS = [
    {"option1": "S", "price": "30.00"},
    {"option1": "M", "price": "30.00"},
    {"option1": "L", "price": "30.00"},
    {"option1": "XL", "price": "30.00"},
    {"option1": "2XL", "price": "35.00"},
    {"option1": "3XL", "price": "37.00"},
    {"option1": "4XL", "price": "39.50"},
    {"option1": "5XL", "price": "39.50"},
]

FEMALE_VARIANTS = [
    {"option1": "S", "price": "30.00"},
    {"option1": "M", "price": "30.00"},
    {"option1": "L", "price": "30.00"},
    {"option1": "XL", "price": "30.00"},
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
    product_type: str = "male",
    tags: str = "",
    image_path: Path | None = None,
    published: bool = True,
) -> dict:
    """Create a product on the OMG Shopify store with size variants.

    Args:
        title: Product title
        body_html: Product description HTML
        product_type: "male" or "female" (determines variants and pricing)
        tags: Comma-separated tags
        image_path: Path to product image (uploaded as base64)
        published: Whether to publish immediately

    Returns:
        Created product dict from Shopify API
    """
    variants = MALE_VARIANTS if product_type == "male" else FEMALE_VARIANTS

    product_data = {
        "product": {
            "title": title,
            "body_html": body_html,
            "vendor": "OMG",
            "product_type": "T-Shirt",
            "tags": tags,
            "published": published,
            "options": [{"name": "Size"}],
            "variants": variants,
        }
    }

    # Upload image if provided
    if image_path and image_path.exists():
        img_bytes = image_path.read_bytes()
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
        product_data["product"]["images"] = [
            {"attachment": img_b64, "filename": image_path.name}
        ]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _admin_url("products.json"),
            headers=_headers(),
            json=product_data,
            timeout=60,
        )
        resp.raise_for_status()
        product = resp.json().get("product", {})
        logger.info(f"Created product: {product.get('id')} — {title}")
        return product


async def create_mapping_for_product(
    omg_product: dict,
    product_type: str = "male",
    design_image: str = "front_design.png",
) -> dict:
    """Create a product mapping between the new OMG product and TShirtJunkies.

    Uses the existing TJ base products (classic tee or women's tee) and matches
    variants by size, similar to mapper.py logic.
    """
    from app.mapper import load_mappings, save_mappings
    from app.models import MappingConfig, ProductMapping, VariantMapping
    from app.shopify_client import fetch_product_by_handle

    tj_info = TJ_PRODUCTS.get(product_type, TJ_PRODUCTS["male"])

    # Fetch TJ product to get variant IDs
    tj_product = await fetch_product_by_handle(
        settings.tshirtjunkies_base_url, tj_info["handle"]
    )
    if not tj_product:
        raise ValueError(f"Could not fetch TJ product: {tj_info['handle']}")

    # Build variant mapping by size
    tj_variants_by_size = {}
    for v in tj_product.get("variants", []):
        size = v.get("option1", "")
        tj_variants_by_size[size] = v

    variant_mappings = []
    for omg_variant in omg_product.get("variants", []):
        size = omg_variant.get("option1", "")
        tj_variant = tj_variants_by_size.get(size)
        if tj_variant:
            variant_mappings.append(VariantMapping(
                source_variant_id=omg_variant["id"],
                source_title=size,
                target_variant_id=tj_variant["id"],
                target_title=size,
                target_price=str(tj_variant.get("price", "0")),
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

    # Add to existing mappings
    config = load_mappings()
    config.mappings.append(mapping)
    save_mappings(config)

    logger.info(f"Mapping created: {omg_product['handle']} → {tj_info['handle']} ({len(variant_mappings)} variants)")
    return mapping.model_dump()
