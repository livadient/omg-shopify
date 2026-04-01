from fastapi import FastAPI, Request

from app.cart_client import TShirtJunkiesCart
from app.mapper import create_mapping_from_urls, load_mappings
from app.models import ProductMapping

app = FastAPI(title="OMG Shopify → TShirtJunkies Order Service")


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
    creates a cart, and returns a checkout URL.
    """
    order = await request.json()
    config = load_mappings()

    # Build lookup: source_variant_id -> target_variant_id
    variant_map: dict[int, int] = {}
    for mapping in config.mappings:
        for v in mapping.variants:
            variant_map[v.source_variant_id] = v.target_variant_id

    cart = TShirtJunkiesCart()
    try:
        items_added = []
        items_skipped = []

        for line_item in order.get("line_items", []):
            source_variant_id = line_item.get("variant_id")
            quantity = line_item.get("quantity", 1)

            target_variant_id = variant_map.get(source_variant_id)
            if target_variant_id:
                await cart.add_item(target_variant_id, quantity)
                items_added.append({
                    "source_variant_id": source_variant_id,
                    "target_variant_id": target_variant_id,
                    "quantity": quantity,
                    "title": line_item.get("title", ""),
                })
            else:
                items_skipped.append({
                    "source_variant_id": source_variant_id,
                    "title": line_item.get("title", ""),
                    "reason": "no mapping found",
                })

        checkout_url = await cart.get_checkout_url() if items_added else None

        return {
            "status": "ok",
            "checkout_url": checkout_url,
            "items_added": items_added,
            "items_skipped": items_skipped,
        }
    finally:
        await cart.close()


@app.post("/test-cart")
async def test_cart(variant_id: int, quantity: int = 1) -> dict:
    """Test adding an item to tshirtjunkies cart and get checkout URL."""
    cart = TShirtJunkiesCart()
    try:
        await cart.clear_cart()
        result = await cart.add_item(variant_id, quantity)
        checkout_url = await cart.get_checkout_url()
        return {
            "add_result": result,
            "checkout_url": checkout_url,
        }
    finally:
        await cart.close()
