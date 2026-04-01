import httpx

from app.config import settings


class TShirtJunkiesCart:
    """Manages a session-based cart on tshirtjunkies.co."""

    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=settings.tshirtjunkies_base_url,
            timeout=15,
        )

    async def add_item(self, variant_id: int, quantity: int = 1) -> dict:
        resp = await self.client.post(
            "/cart/add.js",
            json={"id": variant_id, "quantity": quantity},
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_cart(self) -> dict:
        resp = await self.client.get("/cart.js")
        resp.raise_for_status()
        return resp.json()

    async def clear_cart(self) -> dict:
        resp = await self.client.post("/cart/clear.js")
        resp.raise_for_status()
        return resp.json()

    async def get_checkout_url(self) -> str:
        """Build a checkout URL from the current cart."""
        cart = await self.get_cart()
        items = cart.get("items", [])
        if not items:
            raise ValueError("Cart is empty")

        # Shopify direct checkout URL format: /cart/variant_id:qty,variant_id:qty
        parts = [f"{item['variant_id']}:{item['quantity']}" for item in items]
        return f"{settings.tshirtjunkies_base_url}/cart/{','.join(parts)}"

    async def close(self):
        await self.client.aclose()
