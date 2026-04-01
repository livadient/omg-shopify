"""Fulfill orders on the OMG Shopify store via Admin API."""
import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ADMIN_API_VERSION = "2024-01"

# Cache the access token after OAuth exchange
_access_token: str | None = None


def _shop_domain() -> str:
    domain = settings.omg_shopify_domain
    if not domain.endswith(".myshopify.com"):
        domain = "52922c-2.myshopify.com"
    return domain


def _admin_url(path: str) -> str:
    return f"https://{_shop_domain()}/admin/api/{ADMIN_API_VERSION}/{path}"


async def _get_access_token() -> str:
    """Get an access token, using cached value or exchanging client credentials."""
    global _access_token

    # If we have a direct admin token, use it
    if settings.omg_shopify_admin_token:
        return settings.omg_shopify_admin_token

    # If we already exchanged, use cached
    if _access_token:
        return _access_token

    # If we have client credentials but no token yet, user needs to authorize first
    if settings.omg_shopify_client_id and settings.omg_shopify_client_secret:
        raise RuntimeError(
            "Shopify app not authorized yet. Go to /shopify-auth to authorize."
        )

    raise RuntimeError("No Shopify credentials configured (need OMG_SHOPIFY_ADMIN_TOKEN or CLIENT_ID + CLIENT_SECRET)")


async def exchange_code_for_token(code: str) -> str:
    """Exchange an OAuth authorization code for an access token."""
    global _access_token
    domain = _shop_domain()
    url = f"https://{domain}/admin/oauth/access_token"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, json={
            "client_id": settings.omg_shopify_client_id,
            "client_secret": settings.omg_shopify_client_secret,
            "code": code,
        }, timeout=15)
        if r.status_code == 200:
            _access_token = r.json().get("access_token", "")
            logger.info("Obtained Shopify access token via OAuth")
            return _access_token
        else:
            raise RuntimeError(f"Token exchange failed: {r.status_code} {r.text[:300]}")


async def _headers() -> dict:
    token = await _get_access_token()
    return {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }


async def find_order_by_number(order_number: str) -> dict | None:
    """Look up an OMG order by its order number (e.g. '1001')."""
    url = _admin_url(f"orders.json?name=%23{order_number}&status=any")
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=await _headers(), timeout=15)
        r.raise_for_status()
        orders = r.json().get("orders", [])
        return orders[0] if orders else None


async def fulfill_order(
    order_number: str,
    tracking_number: str = "",
    tracking_url: str = "",
    tracking_company: str = "",
) -> dict:
    """Create a fulfillment for all unfulfilled items on an OMG order.

    Args:
        order_number: OMG order number (e.g. '1001')
        tracking_number: Carrier tracking number
        tracking_url: Tracking URL
        tracking_company: Carrier name (e.g. 'DHL', 'Cyprus Post')

    Returns:
        Fulfillment result dict with status.
    """
    order = await find_order_by_number(order_number)
    if not order:
        return {"status": "error", "detail": f"Order #{order_number} not found on OMG"}

    order_id = order["id"]

    # Get fulfillment orders for this order
    async with httpx.AsyncClient() as client:
        r = await client.get(
            _admin_url(f"orders/{order_id}/fulfillment_orders.json"),
            headers=await _headers(),
            timeout=15,
        )
        r.raise_for_status()
        fulfillment_orders = r.json().get("fulfillment_orders", [])

    # Filter to only open/unfulfilled fulfillment orders
    open_fos = [
        fo for fo in fulfillment_orders
        if fo.get("status") in ("open", "in_progress")
    ]

    if not open_fos:
        return {
            "status": "error",
            "detail": f"Order #{order_number} has no unfulfilled items",
        }

    # Build line items for fulfillment
    line_items_by_fo = []
    for fo in open_fos:
        items = [
            {"id": li["id"], "quantity": li["quantity"]}
            for li in fo.get("line_items", [])
            if li.get("fulfillable_quantity", 0) > 0
        ]
        if items:
            line_items_by_fo.append({
                "fulfillment_order_id": fo["id"],
                "fulfillment_order_line_items": items,
            })

    if not line_items_by_fo:
        return {
            "status": "error",
            "detail": f"Order #{order_number} has no fulfillable line items",
        }

    # Create fulfillment
    tracking_info = {}
    if tracking_number:
        tracking_info["number"] = tracking_number
    if tracking_url:
        tracking_info["url"] = tracking_url
    if tracking_company:
        tracking_info["company"] = tracking_company

    payload = {
        "fulfillment": {
            "line_items_by_fulfillment_order": line_items_by_fo,
            "notify_customer": True,
        }
    }
    if tracking_info:
        payload["fulfillment"]["tracking_info"] = tracking_info

    async with httpx.AsyncClient() as client:
        r = await client.post(
            _admin_url("fulfillments.json"),
            headers=await _headers(),
            json=payload,
            timeout=15,
        )
        if r.status_code >= 400:
            logger.error(f"Fulfillment API error: {r.status_code} {r.text}")
            return {
                "status": "error",
                "detail": f"Shopify API error: {r.status_code} — {r.text[:300]}",
            }

        fulfillment = r.json().get("fulfillment", {})

    return {
        "status": "ok",
        "order_number": order_number,
        "fulfillment_id": fulfillment.get("id"),
        "tracking_number": tracking_number,
        "tracking_url": tracking_url,
    }


def parse_fulfillment_email(text: str) -> dict:
    """Parse a TShirtJunkies fulfillment/shipping email to extract
    the OMG order number and tracking info.

    Returns dict with: omg_order_number, tracking_number, tracking_url, tracking_company.
    """
    result = {
        "omg_order_number": "",
        "tracking_number": "",
        "tracking_url": "",
        "tracking_company": "",
    }

    # Find OMG order number from name field: "Name (OMG #1234)"
    omg_match = re.search(r"\(OMG\s*#(\w+)\)", text)
    if omg_match:
        result["omg_order_number"] = omg_match.group(1)

    # Find tracking URL (common patterns)
    url_match = re.search(r"(https?://\S*track\S*)", text, re.IGNORECASE)
    if url_match:
        result["tracking_url"] = url_match.group(1).rstrip(".,;)")

    # Find tracking number — usually a standalone alphanumeric string
    # Common carriers: DHL, Cyprus Post, ACS, etc.
    for carrier in ["DHL", "Cyprus Post", "ACS", "FedEx", "UPS", "EMS", "USPS", "Royal Mail"]:
        if carrier.lower() in text.lower():
            result["tracking_company"] = carrier
            break

    # Tracking number patterns (long alphanumeric strings)
    tracking_match = re.search(
        r"(?:tracking|track|shipment)[\s:]*#?\s*([A-Z0-9]{8,30})",
        text, re.IGNORECASE,
    )
    if tracking_match:
        result["tracking_number"] = tracking_match.group(1)

    return result
