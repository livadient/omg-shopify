from fastapi import FastAPI, Request

from app.config import settings
from app.mapper import create_mapping_from_urls, load_mappings
from app.models import ProductMapping

app = FastAPI(title="OMG Shopify → TShirtJunkies Order Service")

QSTOMIZER_URL = f"{settings.tshirtjunkies_base_url}/apps/qstomizer/"


@app.post("/map-products")
async def map_products(source_url: str, target_url: str) -> ProductMapping:
    """Map a product from your store to a tshirtjunkies product by providing both URLs."""
    return await create_mapping_from_urls(source_url, target_url)


@app.get("/mappings")
async def get_mappings() -> list[ProductMapping]:
    """List all product mappings."""
    return load_mappings().mappings


@app.post("/webhook/order-created")
async def handle_order_created(request: Request) -> dict:
    """Handle Shopify order/created webhook.

    Receives order data, maps items to tshirtjunkies variants,
    and returns Qstomizer customization URLs for each item
    so the design image can be uploaded before checkout.
    """
    order = await request.json()
    config = load_mappings()

    # Build lookups from source variant ID
    variant_map: dict[int, int] = {}  # source_variant_id -> target_variant_id
    product_id_map: dict[int, int] = {}  # source_variant_id -> target_product_id
    for mapping in config.mappings:
        for v in mapping.variants:
            variant_map[v.source_variant_id] = v.target_variant_id
            product_id_map[v.source_variant_id] = mapping.target_product_id

    items_mapped = []
    items_skipped = []

    for line_item in order.get("line_items", []):
        source_variant_id = line_item.get("variant_id")
        quantity = line_item.get("quantity", 1)

        target_variant_id = variant_map.get(source_variant_id)
        target_product_id = product_id_map.get(source_variant_id)

        if target_variant_id and target_product_id:
            qstomizer_url = (
                f"{QSTOMIZER_URL}?qstomizer-product-id={target_product_id}"
            )
            items_mapped.append({
                "source_variant_id": source_variant_id,
                "target_variant_id": target_variant_id,
                "target_product_id": target_product_id,
                "quantity": quantity,
                "title": line_item.get("title", ""),
                "variant_title": line_item.get("variant_title", ""),
                "qstomizer_url": qstomizer_url,
            })
        else:
            items_skipped.append({
                "source_variant_id": source_variant_id,
                "title": line_item.get("title", ""),
                "reason": "no mapping found",
            })

    return {
        "status": "ok",
        "order_id": order.get("id"),
        "order_number": order.get("order_number"),
        "items_mapped": items_mapped,
        "items_skipped": items_skipped,
    }
