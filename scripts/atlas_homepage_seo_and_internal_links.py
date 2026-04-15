"""One-shot: act on Atlas's recommendation
  - Set homepage meta title/description to target 'custom t-shirts cyprus'
  - Add internal link from the Cyprus-culture blog post to the Astous product
  - Add internal link from the Astous product description back to the blog post

Run inside the container so it picks up the live env (Shopify token, etc).
"""
import asyncio
import logging
import sys

sys.path.insert(0, "/project")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
logger = logging.getLogger("atlas_internal_links")

import httpx

from app.config import settings
from app.seo_management import update_homepage_seo


PRODUCT_HANDLE = "astous-na-laloun-cyprus-unisex-tee"
PRODUCT_PATH = f"/products/{PRODUCT_HANDLE}"
BLOG_ID = 99865624857
BLOG_HANDLE = "news"
ARTICLE_ID = 1000954823036
ARTICLE_HANDLE = "άστους-να-λαλούν-the-cypriot-phrase-taking-europe-by-storm"
ARTICLE_PATH = f"/blogs/{BLOG_HANDLE}/{ARTICLE_HANDLE}"

# Plain-text fragments to swap for hyperlinks in the blog body. We replace
# the FIRST plain-text occurrence (idempotent — once it's an anchor, the
# plain-text marker won't match again).
BLOG_LINK_TARGETS = [
    "<strong>Άστους να Λαλούν graphic tees</strong>",
]

# Callout block appended to the product body_html (deduped by marker).
PRODUCT_CALLOUT_MARKER = "<!-- atlas-blog-callout -->"
PRODUCT_CALLOUT_HTML = (
    f'\n{PRODUCT_CALLOUT_MARKER}\n'
    f'<div style="margin:24px 0;padding:16px 20px;background:#fff7ed;'
    f'border-left:4px solid #f97316;border-radius:6px;font-family:sans-serif;">'
    f'<p style="margin:0;font-size:14px;color:#7c2d12;">'
    f"<strong>Curious about the phrase?</strong> "
    f'Read <a href="{ARTICLE_PATH}" style="color:#c2410c;font-weight:bold;text-decoration:underline;">'
    f"the full story behind &ldquo;Άστους να Λαλούν&rdquo;</a> on our blog."
    f"</p></div>"
)


async def _admin_get(client: httpx.AsyncClient, path: str) -> dict:
    headers = {"X-Shopify-Access-Token": settings.omg_shopify_admin_token}
    url = f"https://{settings.omg_shopify_domain}/admin/api/2024-01/{path}"
    r = await client.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


async def _admin_put(client: httpx.AsyncClient, path: str, payload: dict) -> dict:
    headers = {
        "X-Shopify-Access-Token": settings.omg_shopify_admin_token,
        "Content-Type": "application/json",
    }
    url = f"https://{settings.omg_shopify_domain}/admin/api/2024-01/{path}"
    r = await client.put(url, headers=headers, json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


async def link_blog_to_product(client: httpx.AsyncClient) -> None:
    art = (await _admin_get(client, f"blogs/{BLOG_ID}/articles/{ARTICLE_ID}.json"))["article"]
    body = art["body_html"]
    original = body
    if PRODUCT_PATH in body:
        logger.info("Blog already links to the product — leaving body_html alone.")
        return

    replaced = False
    for marker in BLOG_LINK_TARGETS:
        if marker in body:
            anchor = f'<a href="{PRODUCT_PATH}">{marker}</a>'
            body = body.replace(marker, anchor, 1)
            logger.info(f"Wrapped '{marker[:60]}...' in product link")
            replaced = True
            break

    if not replaced:
        logger.warning(
            "No expected plain-text marker found in blog body — "
            "blog text may have changed since the script was written. "
            "No edit performed."
        )
        return

    if body == original:
        return

    await _admin_put(client, f"blogs/{BLOG_ID}/articles/{ARTICLE_ID}.json", {
        "article": {"id": ARTICLE_ID, "body_html": body}
    })
    logger.info("Blog article updated with product link.")


async def link_product_to_blog(client: httpx.AsyncClient) -> None:
    # Need product ID — fetch by handle via storefront-style products.json.
    # The blog→product link gives us /products/<handle>; updating requires id.
    products = (await _admin_get(
        client, f"products.json?handle={PRODUCT_HANDLE}&fields=id,handle,body_html"
    ))["products"]
    if not products:
        logger.warning(f"No product found for handle '{PRODUCT_HANDLE}'")
        return
    p = products[0]
    body = p["body_html"] or ""
    if PRODUCT_CALLOUT_MARKER in body:
        logger.info("Product description already has the blog callout — skipping.")
        return

    new_body = body + PRODUCT_CALLOUT_HTML
    await _admin_put(client, f"products/{p['id']}.json", {
        "product": {"id": p["id"], "body_html": new_body}
    })
    logger.info(f"Product {p['id']} description updated with blog callout.")


async def main():
    logger.info("Step 1/3: pushing homepage meta title + description...")
    await update_homepage_seo()

    async with httpx.AsyncClient() as client:
        logger.info("Step 2/3: adding link from Cyprus-culture blog post to Astous product...")
        await link_blog_to_product(client)

        logger.info("Step 3/3: adding link from Astous product to Cyprus-culture blog post...")
        await link_product_to_blog(client)

    logger.info("Done.")


asyncio.run(main())
