"""Agent 3: Google Ranking Advisor — daily SEO & Google Ads recommendations."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.agents import llm_client
from app.agents.agent_email import send_agent_email
from app.agents.google_keyword_planner import fetch_keyword_ideas
from app.agents.google_search_console import fetch_search_performance
from app.config import settings

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
HISTORY_FILE = DATA_DIR / "ranking_history.json"

# Market focus rotation by day of week (0=Mon, 1=Tue, etc.)
MARKET_ROTATION = {
    0: ("CY", "Cyprus"),
    1: ("GR", "Greece"),
    2: ("CY", "Cyprus"),
    3: ("GR", "Greece"),
    4: ("EU", "Europe"),
}

SYSTEM_PROMPT = """You are an expert e-commerce consultant, SEO specialist, and Google Ads strategist for a Cyprus-based online t-shirt store called OMG (omg.com.cy).

The store is built on Shopify and sells custom graphic tees, shipping to Cyprus, Greece, and across Europe. The fulfillment partner is TShirtJunkies (tshirtjunkies.co), a Cyprus-based print-on-demand supplier.

Your job is to provide daily, actionable recommendations across THREE areas:
1. **E-shop improvements** — concrete changes to product pages, descriptions, images, collections, navigation, checkout flow, trust signals, mobile UX, or Shopify theme settings that will increase conversions and sales. These must be specific enough that a developer can implement them immediately (e.g. "Add size guide section to product pages with measurements in cm" not "improve product pages").
2. **SEO & content** — keyword targeting, meta tags, blog content, internal linking, schema markup.
3. **Google Ads** — keyword suggestions, campaign structure, budget allocation.

IMPORTANT: Every recommendation must be specific and actionable — something that can be copy-pasted to a developer to implement. Reference actual product titles, pages, handles, and keywords. No generic advice.

DO NOT recommend any of the following — these require manual Shopify admin/theme configuration or Shopify Plus and cannot be implemented programmatically:
- Adding or changing payment methods (JCC cards, PayPal badges, etc.)
- Payment gateway configuration or checkout payment options
- Checkout customizations (address autocomplete, Google Places API, postal code validation, custom checkout scripts)
- Complex theme customizations (layout changes, section rewrites) — simple injections like schema markup and hreflang tags are OK

You may receive REAL Google Search Console data showing actual search queries, impressions, clicks, CTR, and average position. When this data is available, USE IT to ground your recommendations in reality — identify:
- High-impression but low-CTR queries (improve meta titles/descriptions)
- Queries where position is 5-20 (quick wins to push to page 1)
- Queries you're ranking for that don't have dedicated content
- Top-performing pages and what makes them work
- Missing keywords that competitors would target

You may also receive REAL Google Ads Keyword Planner data with actual monthly search volumes, CPC ranges, and competition levels. When available, use these real numbers in your google_ads recommendations instead of estimating.

Output your response as JSON with this structure:
{
  "market_focus": "CY|GR|EU",
  "date": "YYYY-MM-DD",
  "top_actions": [
    {
      "title": "Action title",
      "description": "Detailed actionable description with exact changes to make",
      "impact": "High|Medium|Low",
      "effort": "5 min|15 min|30 min|1 hour"
    }
  ],
  "shop_improvements": [
    {
      "area": "product pages|collections|navigation|checkout|trust|mobile|speed|images",
      "title": "What to change",
      "description": "Exact implementation details — what to add/change/remove and where",
      "impact": "High|Medium|Low",
      "effort": "15 min|30 min|1 hour|2 hours"
    }
  ],
  "seo_opportunities": [
    "Specific SEO recommendation..."
  ],
  "content_ideas": [
    {
      "title": "Blog post title suggestion",
      "target_keyword": "keyword to target",
      "reasoning": "Why this would rank"
    }
  ],
  "google_ads": [
    {
      "keyword": "keyword suggestion",
      "estimated_cpc": "EUR X.XX",
      "monthly_volume": "estimated searches",
      "campaign_note": "Strategy note"
    }
  ],
  "weekly_budget_suggestion": "EUR X-Y per day"
}"""


def _format_keyword_data(keyword_data: list[dict] | None) -> str:
    """Format keyword planner data for the prompt."""
    if not keyword_data:
        return ""
    lines = "\n".join(
        f"- \"{kw['keyword']}\" — {kw['avg_monthly_searches']} searches/mo, "
        f"CPC EUR {kw['low_cpc_eur']}-{kw['high_cpc_eur']}, "
        f"competition: {kw['competition']}"
        for kw in keyword_data[:20]
    )
    return f"""
GOOGLE ADS KEYWORD PLANNER DATA (real CPC and volume):
{lines}
"""


async def _fetch_products() -> list[dict]:
    """Fetch current OMG product catalog via Admin API."""
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


async def _fetch_articles() -> list[dict]:
    """Fetch existing blog articles via Admin API."""
    token = settings.omg_shopify_admin_token
    blog_id = settings.omg_shopify_blog_id
    if not token or not blog_id:
        return []

    domain = settings.omg_shopify_domain
    url = f"https://{domain}/admin/api/2024-01/blogs/{blog_id}/articles.json?limit=50"
    headers = {"X-Shopify-Access-Token": token}

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("articles", [])
    return []


def _load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))


def _save_history(history: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    # Keep last 60 reports
    history = history[-60:]
    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, default=str), encoding="utf-8"
    )


async def generate_daily_report(market_override: str | None = None) -> dict:
    """Generate and email today's ranking report."""
    try:
        return await _generate_daily_report_impl(market_override)
    except Exception as e:
        logger.exception("Ranking Advisor failed")
        from app.agents.agent_email import send_error_email
        await send_error_email("Atlas", e, f"market={market_override}")
        raise


async def _generate_daily_report_impl(market_override: str | None = None) -> dict:
    """Internal implementation."""
    now = datetime.now(timezone.utc)
    weekday = now.weekday()

    if market_override:
        market_code = market_override
        market_name = {"CY": "Cyprus", "GR": "Greece", "EU": "Europe"}.get(
            market_override, market_override
        )
    else:
        market_code, market_name = MARKET_ROTATION.get(weekday, ("EU", "Europe"))

    logger.info(f"Generating ranking report for {market_name} ({market_code})")

    # Gather context
    products = await _fetch_products()
    articles = await _fetch_articles()
    gsc_data = fetch_search_performance(market_code)
    keyword_data = fetch_keyword_ideas(market_code)
    history = _load_history()
    recent = history[-5:] if history else []

    product_summary = "\n".join(
        f"- {p['title']} (handle: {p['handle']}, price: {p['variants'][0]['price']} EUR)"
        for p in products
    ) or "No products found"

    article_summary = "\n".join(
        f"- {a['title']} (tags: {a.get('tags', '')}, published: {a.get('published_at', 'draft')})"
        for a in articles
    ) or "No blog articles yet"

    recent_recs = "\n".join(
        f"- [{r.get('market_focus', '?')}] {', '.join(a['title'] for a in r.get('top_actions', []))}"
        for r in recent
    ) or "No previous recommendations"

    # Format GSC data if available
    gsc_section = ""
    if gsc_data:
        query_lines = "\n".join(
            f"- \"{q['query']}\" — {q['clicks']} clicks, {q['impressions']} impressions, "
            f"CTR {q['ctr']}%, position {q['position']}"
            for q in gsc_data["queries"][:30]
        )
        page_lines = "\n".join(
            f"- {p['page']} — {p['clicks']} clicks, {p['impressions']} impressions, "
            f"CTR {p['ctr']}%, position {p['position']}"
            for p in gsc_data["pages"][:15]
        )
        gsc_section = f"""
GOOGLE SEARCH CONSOLE DATA ({gsc_data['period']}, market: {gsc_data['market']}):

Top Search Queries:
{query_lines or 'No query data yet'}

Top Pages:
{page_lines or 'No page data yet'}
"""

    user_prompt = f"""Today's date: {now.strftime('%A, %B %d, %Y')}
Market focus: {market_name} ({market_code})
Store URL: https://omg.com.cy

CURRENT PRODUCTS:
{product_summary}

EXISTING BLOG ARTICLES:
{article_summary}
{gsc_section}
RECENT RECOMMENDATIONS (avoid repeating):
{recent_recs}
{_format_keyword_data(keyword_data)}
Generate today's ranking recommendations focused on the {market_name} market. Be specific and actionable. Use the real keyword data for your Google Ads suggestions instead of estimating."""

    # Call Claude
    report = await llm_client.generate_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=2048,
        temperature=0.8,
    )

    # Save to history
    report["generated_at"] = now.isoformat()
    history.append(report)
    _save_history(history)

    # Send email
    html = _build_email_html(report, market_name, market_code, now, gsc_data, keyword_data)
    day_name = now.strftime("%a")
    await send_agent_email(
        subject=f"[Atlas] {market_name} briefing — {day_name}, {now.strftime('%b %d')}",
        html_body=html,
    )

    logger.info(f"Ranking report sent for {market_name}")
    return report


def _build_gsc_section_html(gsc_data: dict | None) -> str:
    """Build the Google Search Console data section for the email."""
    if not gsc_data:
        return ""

    queries = gsc_data.get("queries", [])[:10]
    pages = gsc_data.get("pages", [])[:5]
    if not queries and not pages:
        return ""

    query_rows = ""
    for q in queries:
        pos_color = "#059669" if q["position"] <= 10 else ("#d97706" if q["position"] <= 20 else "#dc2626")
        query_rows += f"""
        <tr>
            <td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;">{q['query']}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;">{q['clicks']}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;">{q['impressions']}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;">{q['ctr']}%</td>
            <td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;color:{pos_color};font-weight:bold;">{q['position']}</td>
        </tr>"""

    page_rows = ""
    for p in pages:
        short_page = p["page"].replace("https://omg.com.cy", "").replace("https://omg.gr", "") or "/"
        page_rows += f"""
        <tr>
            <td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;">{short_page}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;">{p['clicks']}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;">{p['impressions']}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;">{p['ctr']}%</td>
            <td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;font-weight:bold;">{p['position']}</td>
        </tr>"""

    pages_section = ""
    if page_rows:
        pages_section = f"""
            <h4 style="color:#0891b2;margin:16px 0 8px;">Top Pages</h4>
            <table style="width:100%;border-collapse:collapse;">
                <thead><tr style="background:#f3f4f6;">
                    <th style="padding:4px 8px;text-align:left;font-size:12px;">Page</th>
                    <th style="padding:4px 8px;text-align:center;font-size:12px;">Clicks</th>
                    <th style="padding:4px 8px;text-align:center;font-size:12px;">Impr.</th>
                    <th style="padding:4px 8px;text-align:center;font-size:12px;">CTR</th>
                    <th style="padding:4px 8px;text-align:center;font-size:12px;">Pos.</th>
                </tr></thead>
                <tbody>{page_rows}</tbody>
            </table>"""

    return f"""
        <div style="padding:20px;background:#ecfdf5;border:1px solid #a7f3d0;border-top:none;">
            <h3 style="color:#059669;margin-top:0;">Search Console Data <span style="font-size:11px;font-weight:normal;">(real data — {gsc_data.get('period', '')})</span></h3>
            <h4 style="color:#059669;margin:0 0 8px;">Top Search Queries</h4>
            <table style="width:100%;border-collapse:collapse;">
                <thead><tr style="background:#d1fae5;">
                    <th style="padding:4px 8px;text-align:left;font-size:12px;">Query</th>
                    <th style="padding:4px 8px;text-align:center;font-size:12px;">Clicks</th>
                    <th style="padding:4px 8px;text-align:center;font-size:12px;">Impr.</th>
                    <th style="padding:4px 8px;text-align:center;font-size:12px;">CTR</th>
                    <th style="padding:4px 8px;text-align:center;font-size:12px;">Pos.</th>
                </tr></thead>
                <tbody>{query_rows}</tbody>
            </table>
            {pages_section}
        </div>"""


def _build_email_html(report: dict, market_name: str, market_code: str, now: datetime,
                      gsc_data: dict | None = None, keyword_data: list[dict] | None = None) -> str:
    # Data sources banner
    has_gsc = gsc_data and (gsc_data.get("queries") or gsc_data.get("pages"))
    has_kw = bool(keyword_data)

    sources = []
    if has_gsc:
        sources.append(f'<span style="display:inline-block;padding:3px 10px;background:#059669;color:white;border-radius:12px;font-size:11px;font-weight:bold;margin-right:6px;">GSC: LIVE DATA</span>')
    else:
        sources.append(f'<span style="display:inline-block;padding:3px 10px;background:#9ca3af;color:white;border-radius:12px;font-size:11px;font-weight:bold;margin-right:6px;">GSC: NO DATA YET</span>')
    if has_kw:
        sources.append(f'<span style="display:inline-block;padding:3px 10px;background:#059669;color:white;border-radius:12px;font-size:11px;font-weight:bold;margin-right:6px;">KEYWORD PLANNER: LIVE DATA</span>')
    else:
        sources.append(f'<span style="display:inline-block;padding:3px 10px;background:#9ca3af;color:white;border-radius:12px;font-size:11px;font-weight:bold;margin-right:6px;">KEYWORD PLANNER: AI ESTIMATES</span>')

    data_sources_html = " ".join(sources)

    # Top actions
    actions_html = ""
    for i, action in enumerate(report.get("top_actions", []), 1):
        actions_html += f"""
        <tr>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;vertical-align:top;font-weight:bold;color:#2563eb;">{i}.</td>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;">
                <strong>{action['title']}</strong><br>
                <span style="color:#374151;">{action['description']}</span><br>
                <span style="font-size:12px;color:#6b7280;">Impact: {action.get('impact', '?')} | Effort: {action.get('effort', '?')}</span>
            </td>
        </tr>"""

    # Shop improvements
    shop_html = ""
    for imp in report.get("shop_improvements", []):
        area_colors = {
            "product pages": "#2563eb", "collections": "#7c3aed", "navigation": "#0891b2",
            "checkout": "#dc2626", "trust": "#059669", "mobile": "#d97706",
            "speed": "#4f46e5", "images": "#0d9488",
        }
        color = area_colors.get(imp.get("area", ""), "#6b7280")
        shop_html += f"""
        <tr>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;vertical-align:top;">
                <span style="display:inline-block;padding:2px 8px;background:{color};color:white;border-radius:4px;font-size:11px;font-weight:bold;">{imp.get('area', '?').upper()}</span>
            </td>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;">
                <strong>{imp['title']}</strong><br>
                <span style="color:#374151;">{imp['description']}</span><br>
                <span style="font-size:12px;color:#6b7280;">Impact: {imp.get('impact', '?')} | Effort: {imp.get('effort', '?')}</span>
            </td>
        </tr>"""

    # SEO opportunities
    seo_html = "".join(
        f"<li style='margin-bottom:6px;'>{opp}</li>"
        for opp in report.get("seo_opportunities", [])
    )

    # Content ideas
    content_html = "".join(
        f"<li style='margin-bottom:6px;'><strong>{idea['title']}</strong> "
        f"<span style='color:#6b7280;'>(keyword: {idea.get('target_keyword', '?')})</span><br>"
        f"<span style='font-size:13px;'>{idea.get('reasoning', '')}</span></li>"
        for idea in report.get("content_ideas", [])
    )

    # Google Ads
    ads_html = ""
    for ad in report.get("google_ads", []):
        ads_html += f"""
        <tr>
            <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;font-weight:bold;">{ad['keyword']}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{ad.get('estimated_cpc', '?')}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{ad.get('monthly_volume', '?')}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;font-size:13px;">{ad.get('campaign_note', '')}</td>
        </tr>"""

    budget = report.get("weekly_budget_suggestion", "Not specified")

    return f"""
    <div style="font-family:sans-serif;max-width:650px;margin:0 auto;color:#111;">
        <div style="background:#1e40af;color:white;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">Atlas reporting for duty</h2>
            <p style="margin:4px 0 0;opacity:0.9;">Today's intel for {market_name} ({market_code}) — {now.strftime('%A, %B %d, %Y')}</p>
            <p style="margin:10px 0 0;">{data_sources_html}</p>
        </div>

        <div style="padding:20px;background:#f9fafb;border:1px solid #e5e7eb;">
            <h3 style="color:#1e40af;margin-top:0;">Today's Top Actions</h3>
            <table style="width:100%;border-collapse:collapse;">{actions_html}</table>
        </div>

        <div style="padding:20px;border:1px solid #e5e7eb;border-top:none;">
            <h3 style="color:#d97706;margin-top:0;">E-Shop Improvements</h3>
            <table style="width:100%;border-collapse:collapse;">{shop_html}</table>
        </div>

        <div style="padding:20px;background:#f9fafb;border:1px solid #e5e7eb;border-top:none;">
            <h3 style="color:#059669;margin-top:0;">SEO Opportunities</h3>
            <ul style="margin:0;padding-left:20px;">{seo_html}</ul>
        </div>

        <div style="padding:20px;background:#f9fafb;border:1px solid #e5e7eb;border-top:none;">
            <h3 style="color:#7c3aed;margin-top:0;">Content Ideas</h3>
            <ul style="margin:0;padding-left:20px;">{content_html}</ul>
        </div>

        {_build_gsc_section_html(gsc_data) if has_gsc else ''}

        <div style="padding:20px;border:1px solid #e5e7eb;border-top:none;">
            <h3 style="color:#dc2626;margin-top:0;">Google Ads Suggestions {('<span style="font-size:11px;font-weight:normal;color:#059669;">(Keyword Planner data)</span>' if has_kw else '<span style="font-size:11px;font-weight:normal;color:#9ca3af;">(AI estimates)</span>')}</h3>
            <table style="width:100%;border-collapse:collapse;">
                <thead>
                    <tr style="background:#f3f4f6;">
                        <th style="padding:6px 10px;text-align:left;">Keyword</th>
                        <th style="padding:6px 10px;text-align:left;">Est. CPC</th>
                        <th style="padding:6px 10px;text-align:left;">Volume</th>
                        <th style="padding:6px 10px;text-align:left;">Note</th>
                    </tr>
                </thead>
                <tbody>{ads_html}</tbody>
            </table>
            <p style="margin-top:12px;font-size:14px;color:#6b7280;">Suggested daily budget: <strong>{budget}</strong></p>
        </div>

        <div style="padding:16px;text-align:center;color:#9ca3af;font-size:12px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
            Your strategist, Atlas | <a href="{settings.server_base_url}/agents/ranking/history" style="color:#6b7280;">View History</a>
        </div>
    </div>
    """


def get_history(limit: int = 30) -> list[dict]:
    """Return recent ranking reports."""
    history = _load_history()
    return history[-limit:]
