import httpx


async def fetch_product_by_handle(base_url: str, handle: str) -> dict | None:
    """Fetch a single product from a Shopify store by its handle.

    Tries the individual product endpoint first, then falls back to
    searching the catalog (some stores block /products/{handle}.json).
    """
    async with httpx.AsyncClient(timeout=15) as client:
        # Try direct endpoint first
        resp = await client.get(f"{base_url}/products/{handle}.json")
        if resp.status_code == 200:
            return resp.json().get("product")

        # Fallback: search through paginated catalog
        page = 1
        while True:
            resp = await client.get(
                f"{base_url}/products.json",
                params={"limit": 250, "page": page},
            )
            if resp.status_code != 200:
                break
            products = resp.json().get("products", [])
            if not products:
                break
            for product in products:
                if product.get("handle") == handle:
                    return product
            page += 1

    return None


async def fetch_product_from_url(product_url: str) -> tuple[str, dict | None]:
    """Extract base_url and handle from a Shopify product URL, then fetch it.

    Accepts URLs like:
      https://store.com/products/my-product
      https://store.com/collections/all/products/my-product
    """
    from urllib.parse import urlparse

    parsed = urlparse(product_url.rstrip("/"))
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    path_parts = parsed.path.strip("/").split("/")

    # Find "products" in path and take the next segment as handle
    handle = None
    for i, part in enumerate(path_parts):
        if part == "products" and i + 1 < len(path_parts):
            handle = path_parts[i + 1]
            break

    if not handle:
        return base_url, None

    product = await fetch_product_by_handle(base_url, handle)
    return base_url, product
