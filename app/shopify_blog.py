"""Shopify Blog Article Admin API client."""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ADMIN_API_VERSION = "2024-01"


def _admin_url(path: str) -> str:
    domain = settings.omg_shopify_domain
    if not domain.endswith(".myshopify.com"):
        domain = "52922c-2.myshopify.com"
    return f"https://{domain}/admin/api/{ADMIN_API_VERSION}/{path}"


def _headers() -> dict:
    return {"X-Shopify-Access-Token": settings.omg_shopify_admin_token}


async def list_blogs() -> list[dict]:
    """List all blogs on the store."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _admin_url("blogs.json"), headers=_headers(), timeout=15
        )
        resp.raise_for_status()
        return resp.json().get("blogs", [])


async def list_articles(blog_id: str | None = None, limit: int = 50) -> list[dict]:
    """List articles from a blog."""
    bid = blog_id or settings.omg_shopify_blog_id
    if not bid:
        return []
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _admin_url(f"blogs/{bid}/articles.json?limit={limit}"),
            headers=_headers(),
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("articles", [])
    return []


async def create_article(
    title: str,
    body_html: str,
    tags: str = "",
    meta_title: str = "",
    meta_description: str = "",
    published: bool = True,
    blog_id: str | None = None,
) -> dict:
    """Create a blog article on the OMG Shopify store."""
    bid = blog_id or settings.omg_shopify_blog_id
    if not bid:
        raise ValueError("No blog_id configured. Set OMG_SHOPIFY_BLOG_ID in .env")

    article_data = {
        "article": {
            "title": title,
            "body_html": body_html,
            "tags": tags,
            "published": published,
        }
    }
    if meta_title:
        article_data["article"]["metafields_global_title_tag"] = meta_title
    if meta_description:
        article_data["article"]["metafields_global_description_tag"] = meta_description

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _admin_url(f"blogs/{bid}/articles.json"),
            headers=_headers(),
            json=article_data,
            timeout=30,
        )
        resp.raise_for_status()
        article = resp.json().get("article", {})
        logger.info(f"Published article: {article.get('id')} — {title}")
        return article


async def update_article(
    article_id: int | str,
    body_html: str,
    blog_id: str | None = None,
) -> dict:
    """Update an existing article's body_html."""
    bid = blog_id or settings.omg_shopify_blog_id
    if not bid:
        raise ValueError("No blog_id configured. Set OMG_SHOPIFY_BLOG_ID in .env")

    payload = {"article": {"id": int(article_id), "body_html": body_html}}
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            _admin_url(f"blogs/{bid}/articles/{article_id}.json"),
            headers=_headers(),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        article = resp.json().get("article", {})
        logger.info(f"Updated article: {article.get('id')} — {article.get('title', '')}")
        return article
