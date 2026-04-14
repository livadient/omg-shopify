"""Agent 1: SEO Blog Writer — generates blog posts about OMG t-shirts."""
import logging

import httpx

from app.agents import llm_client
from app.agents.agent_email import send_agent_email
from app.agents.approval import approval_url, create_proposal
from app.config import settings

logger = logging.getLogger(__name__)

# Extra recipients for Olive's blog post notifications.
EXTRA_RECIPIENTS = ["kmarangos@hotmail.com", "kyriaki_mara@yahoo.com"]

SYSTEM_PROMPT = """You are an expert SEO blog writer for OMG (omg.com.cy), a Cyprus-based online t-shirt store that sells custom graphic tees. The store ships to Cyprus, Greece, and across Europe.

Brand voice: casual, trendy, Mediterranean lifestyle. Anyone can enjoy a great graphic tee — do NOT mention target demographics, age ranges, or audience segments in the blog post.

Your job is to write an SEO-optimized blog post that will rank on Google and drive organic traffic to the store.

Requirements:
- 800-1500 words
- Include relevant keywords naturally (2-3% density)
- Use H2 and H3 headings for structure
- Include a compelling meta description (under 160 characters)
- Reference actual OMG products where relevant
- Include a call-to-action linking to the store
- Write in English but include Greek/Cypriot cultural references when relevant
- Focus on the target market provided

Rotate through these topic angles:
- Product spotlights and styling guides
- Seasonal content (summer collections, holiday gifting)
- Greek/Cypriot culture meets fashion
- T-shirt trends and streetwear culture
- Behind-the-scenes brand storytelling

Output your response as JSON:
{
  "title": "Blog post title (SEO-optimized, 50-60 chars)",
  "meta_description": "Compelling meta description under 160 chars",
  "body_html": "<h2>...</h2><p>...</p>...",
  "tags": "comma,separated,tags",
  "target_keywords": ["primary keyword", "secondary keyword"],
  "topic_angle": "which angle this post covers"
}"""


async def _fetch_products() -> list[dict]:
    """Fetch current OMG product catalog."""
    token = settings.omg_shopify_admin_token
    if not token:
        return []
    domain = settings.omg_shopify_domain
    url = f"https://{domain}/admin/api/2024-01/products.json?limit=50"
    headers = {"X-Shopify-Access-Token": token}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("products", [])
    return []


async def _fetch_existing_articles() -> list[dict]:
    """Fetch existing blog articles to avoid duplication."""
    from app.shopify_blog import list_articles
    return await list_articles()


async def generate_proposal() -> dict:
    """Generate a blog post proposal and email it for approval."""
    try:
        return await _generate_proposal_impl()
    except Exception as e:
        logger.exception("Blog Writer failed")
        from app.agents.agent_email import send_error_email
        await send_error_email("Olive", e)
        raise


async def _generate_proposal_impl() -> dict:
    """Internal implementation."""
    logger.info("Blog Writer: generating proposal")

    products = await _fetch_products()
    articles = await _fetch_existing_articles()

    product_summary = "\n".join(
        f"- {p['title']} ({p['handle']}) — {p.get('body_html', '')[:100]}"
        for p in products
    ) or "No products found"

    existing_titles = "\n".join(
        f"- {a['title']}" for a in articles
    ) or "No existing articles"

    from datetime import datetime, timezone
    from app.agents.memory import build_memory_prompt
    _memory_prompt = build_memory_prompt("olive")
    now = datetime.now(timezone.utc)

    user_prompt = f"""Today's date: {now.strftime('%A, %B %d, %Y')}
Store URL: https://omg.com.cy
Target markets: Cyprus, Greece, Europe

CURRENT PRODUCTS:
{product_summary}

EXISTING BLOG POSTS (do NOT repeat these topics):
{existing_titles}

Write a new, unique SEO blog post. Choose a fresh angle that complements the existing content.
{_memory_prompt}"""

    blog_data = await llm_client.generate_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=4096,
        temperature=0.8,
    )

    # Create proposal
    proposal = create_proposal("blog", blog_data)

    # Send approval email
    approve = approval_url(proposal["id"], proposal["token"], "approve")
    reject = approval_url(proposal["id"], proposal["token"], "reject")
    preview = f"{settings.server_base_url}/agents/blog/preview/{proposal['id']}"

    body_preview = blog_data.get("body_html", "")[:500]
    tags = blog_data.get("tags", "")
    meta = blog_data.get("meta_description", "")
    keywords = ", ".join(blog_data.get("target_keywords", []))

    html = f"""
    <div style="font-family:sans-serif;max-width:650px;margin:0 auto;">
        <div style="background:#059669;color:white;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">Olive here — new post ready!</h2>
            <p style="margin:4px 0 0;opacity:0.9;">I've put together something I think our readers will love. Take a look?</p>
        </div>
        <div style="padding:20px;border:1px solid #e5e7eb;">
            <h3 style="margin-top:0;color:#111;">{blog_data.get('title', 'Untitled')}</h3>
            <table style="width:100%;margin-bottom:16px;">
                <tr><td style="color:#6b7280;padding:4px 0;">Meta:</td><td>{meta}</td></tr>
                <tr><td style="color:#6b7280;padding:4px 0;">Tags:</td><td>{tags}</td></tr>
                <tr><td style="color:#6b7280;padding:4px 0;">Keywords:</td><td>{keywords}</td></tr>
                <tr><td style="color:#6b7280;padding:4px 0;">Angle:</td><td>{blog_data.get('topic_angle', '?')}</td></tr>
            </table>
            <div style="background:#f9fafb;padding:16px;border-radius:8px;border:1px solid #e5e7eb;font-size:14px;">
                {body_preview}...
            </div>
        </div>
        <div style="padding:20px;background:#f3f4f6;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;text-align:center;">
            <a href="{approve}" style="display:inline-block;padding:12px 32px;background:#059669;color:white;text-decoration:none;border-radius:6px;font-weight:bold;margin:0 8px;">Approve & Publish</a>
            <a href="{reject}" style="display:inline-block;padding:12px 32px;background:#dc2626;color:white;text-decoration:none;border-radius:6px;font-weight:bold;margin:0 8px;">Reject</a>
            <br><br>
            <a href="{preview}" style="color:#2563eb;">View Full Preview</a> | <a href="{settings.server_base_url}/agents/feedback/form?agent=olive" style="color:#6b7280;">Give Feedback</a>
        </div>
    </div>
    """

    await send_agent_email(
        subject=f"[Olive] New post ready: \"{blog_data.get('title', 'Untitled')}\"",
        html_body=html,
        extra_recipients=EXTRA_RECIPIENTS,
    )

    logger.info(f"Blog proposal {proposal['id']} created and emailed")
    return proposal


async def execute_approval(proposal_id: str) -> dict:
    """Publish an approved blog post to Shopify."""
    from app.agents.approval import get_proposal, update_status
    from app.shopify_blog import create_article

    proposal = get_proposal(proposal_id)
    if not proposal:
        raise ValueError(f"Proposal {proposal_id} not found")

    data = proposal["data"]
    article = await create_article(
        title=data["title"],
        body_html=data["body_html"],
        tags=data.get("tags", ""),
        meta_title=data.get("title", ""),
        meta_description=data.get("meta_description", ""),
        published=True,
    )

    update_status(proposal_id, "approved")
    logger.info(f"Blog post published: {article.get('id')} — {data['title']}")
    return article
