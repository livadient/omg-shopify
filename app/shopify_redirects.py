"""Shopify URL Redirects Admin API client.

Used to 301 archived product URLs to a sensible target (e.g. /collections/t-shirts)
so old links from email/SEO/blog posts don't die with a 404.
"""
import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ADMIN_API_VERSION = "2024-01"
DEFAULT_REDIRECT_TARGET = "/collections/t-shirts"


def _admin_url(path: str) -> str:
    domain = settings.omg_shopify_domain
    if not domain.endswith(".myshopify.com"):
        domain = "52922c-2.myshopify.com"
    return f"https://{domain}/admin/api/{ADMIN_API_VERSION}/{path}"


def _headers() -> dict:
    return {"X-Shopify-Access-Token": settings.omg_shopify_admin_token}


async def list_redirects() -> list[dict]:
    """Fetch every URL redirect on the store (paginated)."""
    redirects: list[dict] = []
    page_info: str | None = None
    async with httpx.AsyncClient() as client:
        while True:
            url = _admin_url("redirects.json?limit=250")
            if page_info:
                url += f"&page_info={page_info}"
            resp = await client.get(url, headers=_headers(), timeout=20)
            if resp.status_code != 200:
                break
            redirects.extend(resp.json().get("redirects", []))
            link = resp.headers.get("link", "")
            m = re.search(r'<[^>]+page_info=([^&>]+)[^>]*>;\s*rel="next"', link)
            if not m:
                break
            page_info = m.group(1)
    return redirects


async def create_redirect(path: str, target: str) -> dict | None:
    """Create a single URL redirect. Returns the new redirect, or None on failure."""
    payload = {"redirect": {"path": path, "target": target}}
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _admin_url("redirects.json"),
            headers=_headers(),
            json=payload,
            timeout=20,
        )
        if resp.status_code in (200, 201):
            return resp.json().get("redirect")
        logger.warning(f"create_redirect failed for {path}: {resp.status_code} {resp.text[:200]}")
        return None


async def redirect_archived_products(target: str = DEFAULT_REDIRECT_TARGET) -> dict:
    """Create 301 redirects for every archived/unpublished product.

    Skips products that already have a redirect at /products/<handle>.
    Returns a summary of what was created vs. skipped.
    """
    token = settings.omg_shopify_admin_token
    if not token:
        return {"error": "no admin token configured"}

    domain = settings.omg_shopify_domain
    headers = {"X-Shopify-Access-Token": token}
    archived: list[dict] = []
    page_info: str | None = None
    async with httpx.AsyncClient() as client:
        while True:
            url = (
                f"https://{domain}/admin/api/{ADMIN_API_VERSION}/products.json"
                f"?status=archived&limit=250"
            )
            if page_info:
                url += f"&page_info={page_info}"
            resp = await client.get(url, headers=headers, timeout=20)
            if resp.status_code != 200:
                break
            archived.extend(resp.json().get("products", []))
            link = resp.headers.get("link", "")
            m = re.search(r'<[^>]+page_info=([^&>]+)[^>]*>;\s*rel="next"', link)
            if not m:
                break
            page_info = m.group(1)

    if not archived:
        return {"archived": 0, "created": 0, "skipped": 0, "details": []}

    existing = {r["path"]: r for r in await list_redirects()}

    created: list[dict] = []
    skipped: list[dict] = []
    for p in archived:
        path = f"/products/{p['handle']}"
        if path in existing:
            skipped.append({
                "handle": p["handle"],
                "reason": f"already redirects to {existing[path].get('target')}",
            })
            continue
        result = await create_redirect(path, target)
        if result:
            created.append({"handle": p["handle"], "path": path, "target": target})
            logger.info(f"redirect created: {path} → {target}")
        else:
            skipped.append({"handle": p["handle"], "reason": "API call failed"})

    return {
        "archived": len(archived),
        "created": len(created),
        "skipped": len(skipped),
        "target": target,
        "created_details": created,
        "skipped_details": skipped,
    }
