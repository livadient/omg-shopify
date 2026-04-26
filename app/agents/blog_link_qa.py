"""Olive's link QA pass: scan blog posts for broken product links.

Walks every article on the OMG blog, extracts product `<a href>` links,
checks each handle against the live (active + published) product catalog,
and asks Claude to suggest a replacement from the live set when a link
is broken. Per-article proposals go through the standard approval flow;
one summary email lists every article that needs attention.

Folded into Olive's Tue/Fri 05:00 run — the link sweep happens before a
new post is generated.
"""
import logging
import re
from html import escape

import httpx

from app.agents import llm_client
from app.agents.agent_email import send_agent_email
from app.agents.approval import approval_url, create_proposal
from app.config import settings

logger = logging.getLogger(__name__)

EXTRA_RECIPIENTS = ["kmarangos@hotmail.com", "kyriaki_mara@yahoo.com"]

# Match <a ... href="..."> capturing the href and inner text.
_A_TAG_RE = re.compile(
    r'<a\b([^>]*?)\bhref\s*=\s*["\']([^"\']+)["\']([^>]*)>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# Match a /products/<handle> path (with optional query/fragment).
_PRODUCT_PATH_RE = re.compile(r"/products/([a-z0-9][a-z0-9\-]*)", re.IGNORECASE)


async def _fetch_active_products() -> list[dict]:
    """Fetch active + published products. Treats archived/unpublished as broken."""
    token = settings.omg_shopify_admin_token
    if not token:
        return []
    domain = settings.omg_shopify_domain
    headers = {"X-Shopify-Access-Token": token}
    products: list[dict] = []
    page_info: str | None = None
    async with httpx.AsyncClient() as client:
        while True:
            url = f"https://{domain}/admin/api/2024-01/products.json?status=active&limit=250"
            if page_info:
                url += f"&page_info={page_info}"
            resp = await client.get(url, headers=headers, timeout=20)
            if resp.status_code != 200:
                break
            batch = resp.json().get("products", [])
            products.extend(batch)
            link = resp.headers.get("link", "")
            m = re.search(r'<[^>]+page_info=([^&>]+)[^>]*>;\s*rel="next"', link)
            if not m:
                break
            page_info = m.group(1)
    return [p for p in products if p.get("published_at")]


def _is_omg_host(href: str) -> bool:
    h = href.lower().strip()
    return (
        h.startswith("/products/")
        or "omg.com.cy/products/" in h
        or "omg.gr/products/" in h
        or "ohmangoes.com/products/" in h
    )


def _extract_product_links(body_html: str) -> list[dict]:
    """Find every <a> pointing at /products/<handle>. Returns [{match, handle, anchor, context}]."""
    links: list[dict] = []
    for m in _A_TAG_RE.finditer(body_html):
        href = m.group(2)
        if not _is_omg_host(href):
            continue
        handle_match = _PRODUCT_PATH_RE.search(href)
        if not handle_match:
            continue
        anchor_text = re.sub(r"<[^>]+>", " ", m.group(4))
        anchor_text = re.sub(r"\s+", " ", anchor_text).strip()

        # Pull a window of surrounding text for Claude's context.
        start = max(0, m.start() - 400)
        end = min(len(body_html), m.end() + 400)
        context_html = body_html[start:end]
        context_text = re.sub(r"<[^>]+>", " ", context_html)
        context_text = re.sub(r"\s+", " ", context_text).strip()

        links.append({
            "full_match": m.group(0),
            "href": href,
            "handle": handle_match.group(1).lower(),
            "anchor": anchor_text,
            "context": context_text[:800],
        })
    return links


async def _suggest_replacement(
    anchor: str, context: str, broken_handle: str, live_products: list[dict]
) -> dict:
    """Ask Claude to pick a replacement handle from the live catalog."""
    catalog = "\n".join(
        f"- {p['handle']}: {p.get('title', '')}"
        for p in live_products
    )
    system = (
        "You retarget broken product links in blog posts. Given the anchor text, "
        "the surrounding paragraph, the broken handle, and a list of currently live "
        "products, pick the single best replacement. Output JSON only."
    )
    user = f"""Anchor text: {anchor}
Broken handle: {broken_handle}
Surrounding context: {context}

LIVE PRODUCTS:
{catalog}

Pick the closest match. Confidence rubric:
- "high": replacement clearly fits the anchor + context (same theme, same product type)
- "med": plausible match but some drift in theme or wording
- "low": no good match — anchor refers to something we don't sell anymore

Output JSON:
{{
  "replacement_handle": "<one of the live handles, or null if confidence is low>",
  "confidence": "high" | "med" | "low",
  "reason": "one short sentence",
  "anchor_mismatch": true | false
}}

`anchor_mismatch` = true when the anchor text wouldn't make sense after the swap
(e.g. anchor says "Cyprus Map Tee" but the only fit is the Astous Limited tee)."""
    try:
        return await llm_client.generate_json(
            system_prompt=system,
            user_prompt=user,
            max_tokens=300,
            temperature=0,
        )
    except Exception as e:
        logger.warning(f"replacement suggestion failed for {broken_handle}: {e}")
        return {"replacement_handle": None, "confidence": "low",
                "reason": f"LLM error: {e}", "anchor_mismatch": False}


def _rewrite_links(body_html: str, swaps: list[dict]) -> str:
    """Apply approved hrefs back into body_html. Anchor text untouched (v1)."""
    new_html = body_html
    for s in swaps:
        old_tag = s["full_match"]
        new_tag = old_tag.replace(s["old_href"], s["new_href"], 1)
        new_html = new_html.replace(old_tag, new_tag, 1)
    return new_html


async def check_blog_links() -> dict:
    """Run link QA across every article. Email a single summary."""
    try:
        return await _check_blog_links_impl()
    except Exception as e:
        logger.exception("Olive link QA failed")
        from app.agents.agent_email import send_error_email
        await send_error_email("Olive", e, context="link QA pass")
        raise


async def _check_blog_links_impl() -> dict:
    from app.shopify_blog import list_articles

    logger.info("Olive: starting blog link QA")

    articles = await list_articles()
    if not articles:
        logger.info("no articles to scan")
        return {"articles_scanned": 0, "broken": 0, "proposals": []}

    live_products = await _fetch_active_products()
    live_handles = {p["handle"].lower() for p in live_products}
    logger.info(f"scanning {len(articles)} article(s) against {len(live_handles)} live handles")

    article_reports: list[dict] = []

    for art in articles:
        body = art.get("body_html") or ""
        links = _extract_product_links(body)
        if not links:
            continue

        broken_links: list[dict] = []
        for link in links:
            if link["handle"] in live_handles:
                continue
            suggestion = await _suggest_replacement(
                anchor=link["anchor"],
                context=link["context"],
                broken_handle=link["handle"],
                live_products=live_products,
            )
            broken_links.append({**link, "suggestion": suggestion})

        if not broken_links:
            continue

        # Build the swap list — only med/high confidence get auto-applied.
        swaps: list[dict] = []
        manual: list[dict] = []
        for bl in broken_links:
            sug = bl["suggestion"]
            if (
                sug.get("confidence") in ("high", "med")
                and sug.get("replacement_handle")
                and sug["replacement_handle"] in live_handles
            ):
                new_href = f"https://omg.com.cy/products/{sug['replacement_handle']}"
                swaps.append({
                    "full_match": bl["full_match"],
                    "old_href": bl["href"],
                    "new_href": new_href,
                    "anchor": bl["anchor"],
                    "old_handle": bl["handle"],
                    "new_handle": sug["replacement_handle"],
                    "confidence": sug["confidence"],
                    "reason": sug.get("reason", ""),
                    "anchor_mismatch": bool(sug.get("anchor_mismatch")),
                })
            else:
                manual.append({
                    "old_handle": bl["handle"],
                    "anchor": bl["anchor"],
                    "reason": sug.get("reason", "no live match"),
                })

        new_body = _rewrite_links(body, swaps) if swaps else body

        proposal_data = {
            "article_id": art["id"],
            "article_title": art.get("title", ""),
            "article_handle": art.get("handle", ""),
            "old_body_html": body,
            "new_body_html": new_body,
            "swaps": swaps,
            "manual": manual,
        }
        proposal = create_proposal("blog_link_fix", proposal_data)

        article_reports.append({
            "proposal_id": proposal["id"],
            "token": proposal["token"],
            "title": art.get("title", ""),
            "handle": art.get("handle", ""),
            "swaps": swaps,
            "manual": manual,
            "has_swaps": bool(swaps),
        })
        logger.info(
            f"  {art.get('handle')}: {len(swaps)} auto-fix, {len(manual)} manual"
        )

    if article_reports:
        await _send_summary_email(article_reports)
    else:
        await _send_all_clear_email(len(articles))

    return {
        "articles_scanned": len(articles),
        "broken_articles": len(article_reports),
        "proposals": [r["proposal_id"] for r in article_reports],
    }


def _confidence_badge(c: str) -> str:
    color = {"high": "#059669", "med": "#d97706", "low": "#6b7280"}.get(c, "#6b7280")
    return (
        f'<span style="background:{color};color:white;padding:1px 6px;'
        f'border-radius:4px;font-size:11px;font-weight:bold;">{c.upper()}</span>'
    )


async def _send_summary_email(reports: list[dict]) -> None:
    blocks = ""
    for r in reports:
        approve = approval_url(r["proposal_id"], r["token"], "approve")
        reject = approval_url(r["proposal_id"], r["token"], "reject")
        preview = f"{settings.server_base_url}/agents/blog_link_fix/preview/{r['proposal_id']}"

        swap_rows = ""
        for s in r["swaps"]:
            mismatch = (
                ' <span style="color:#dc2626;font-size:11px;">[anchor mismatch]</span>'
                if s["anchor_mismatch"] else ""
            )
            swap_rows += f"""
            <tr>
                <td style="padding:4px 8px;font-size:13px;color:#dc2626;text-decoration:line-through;">{escape(s['old_handle'])}</td>
                <td style="padding:4px 8px;font-size:13px;">→</td>
                <td style="padding:4px 8px;font-size:13px;color:#059669;">{escape(s['new_handle'])}</td>
                <td style="padding:4px 8px;">{_confidence_badge(s['confidence'])}{mismatch}</td>
                <td style="padding:4px 8px;font-size:12px;color:#6b7280;">"{escape(s['anchor'])}" — {escape(s['reason'])}</td>
            </tr>"""

        manual_rows = ""
        for mu in r["manual"]:
            manual_rows += f"""
            <tr>
                <td style="padding:4px 8px;font-size:13px;color:#dc2626;">{escape(mu['old_handle'])}</td>
                <td style="padding:4px 8px;font-size:12px;color:#6b7280;" colspan="4">"{escape(mu['anchor'])}" — needs manual pick ({escape(mu['reason'])})</td>
            </tr>"""

        action_buttons = ""
        if r["has_swaps"]:
            action_buttons = f"""
            <a href="{approve}" style="display:inline-block;padding:8px 20px;background:#059669;color:white;text-decoration:none;border-radius:6px;font-weight:bold;margin:4px;">Approve fixes</a>
            <a href="{reject}" style="display:inline-block;padding:8px 20px;background:#dc2626;color:white;text-decoration:none;border-radius:6px;font-weight:bold;margin:4px;">Reject</a>"""
        else:
            action_buttons = (
                '<p style="color:#6b7280;font-size:13px;">No auto-fixable swaps — manual edit required.</p>'
            )

        blocks += f"""
        <div style="border:1px solid #e5e7eb;border-radius:8px;padding:16px;margin-bottom:16px;background:white;">
            <h3 style="margin:0 0 4px;color:#111;">{escape(r['title'])}</h3>
            <p style="margin:0 0 12px;color:#6b7280;font-size:12px;">/blogs/news/{escape(r['handle'])}</p>
            <table style="width:100%;border-collapse:collapse;margin-bottom:12px;">
                <thead>
                    <tr style="background:#f3f4f6;">
                        <th style="padding:6px 8px;text-align:left;font-size:12px;">Broken</th>
                        <th></th>
                        <th style="padding:6px 8px;text-align:left;font-size:12px;">Replacement</th>
                        <th style="padding:6px 8px;text-align:left;font-size:12px;">Confidence</th>
                        <th style="padding:6px 8px;text-align:left;font-size:12px;">Notes</th>
                    </tr>
                </thead>
                <tbody>{swap_rows}{manual_rows}</tbody>
            </table>
            <div style="text-align:center;">
                {action_buttons}
                <br><a href="{preview}" style="color:#2563eb;font-size:13px;">View diff</a>
            </div>
        </div>"""

    total_swaps = sum(len(r["swaps"]) for r in reports)
    total_manual = sum(len(r["manual"]) for r in reports)

    html = f"""
    <div style="font-family:sans-serif;max-width:780px;margin:0 auto;">
        <div style="background:#059669;color:white;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">Olive here — link check complete</h2>
            <p style="margin:4px 0 0;opacity:0.9;">{len(reports)} article(s) have broken product links. Auto-fix: {total_swaps} | Manual: {total_manual}</p>
        </div>
        <div style="padding:16px;background:#f9fafb;">
            {blocks}
        </div>
        <div style="padding:12px;text-align:center;color:#9ca3af;font-size:12px;">
            Olive's nightly link sweep · {settings.server_base_url}
        </div>
    </div>
    """
    await send_agent_email(
        subject=f"[Olive] Broken product links in {len(reports)} article(s)",
        html_body=html,
        extra_recipients=EXTRA_RECIPIENTS,
    )


async def _send_all_clear_email(article_count: int) -> None:
    html = f"""
    <div style="font-family:sans-serif;max-width:560px;margin:0 auto;">
        <div style="background:#059669;color:white;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">Olive here — all links healthy</h2>
            <p style="margin:4px 0 0;opacity:0.9;">Scanned {article_count} article(s). Every product link points at a live product.</p>
        </div>
    </div>
    """
    await send_agent_email(
        subject="[Olive] All blog links healthy",
        html_body=html,
    )


async def execute_blog_link_fix(proposal_id: str) -> dict:
    """Apply the rewritten body_html to the live article."""
    from app.agents.approval import get_proposal, update_status
    from app.shopify_blog import update_article

    proposal = get_proposal(proposal_id)
    if not proposal:
        raise ValueError(f"Proposal {proposal_id} not found")

    data = proposal["data"]
    if not data.get("swaps"):
        raise ValueError("No auto-fixable swaps in this proposal")

    article = await update_article(
        article_id=data["article_id"],
        body_html=data["new_body_html"],
    )
    update_status(proposal_id, "approved")
    logger.info(
        f"link fix applied to article {data['article_id']} ({len(data['swaps'])} swaps)"
    )
    return article
