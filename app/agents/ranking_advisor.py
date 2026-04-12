"""Agent 3: Google Ranking Advisor — daily SEO & Google Ads recommendations."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.agents import llm_client
from app.agents.agent_email import send_agent_email
from app.agents.google_keyword_planner import fetch_keyword_ideas, fetch_historical_metrics
from app.agents.google_search_console import fetch_search_performance
from app.agents.google_trends import fetch_trending_searches, fetch_related_topics
from app.agents.memory import build_memory_prompt, build_trends_prompt, save_performance_trend
from app.config import settings

# Extra recipients who should receive Atlas' emails (in addition to settings.email_recipients)
EXTRA_RECIPIENTS = ["kyriaki_mara@yahoo.com"]

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

You may receive GOOGLE TRENDS data showing trending searches and rising related queries. When available, identify trends relevant to t-shirts/fashion/pop culture and suggest how to capitalize on them — new designs, blog posts, ad campaigns, or social media content that rides the trend wave.

CRITICAL — TRENDS VERIFICATION: A "rising" query in Google Trends shows percentage growth versus the previous 3-month baseline of THAT exact query, NOT absolute search volume. A query going from 1 search to 9 is "rising 800%" but is essentially noise. When rising queries have been cross-referenced against Keyword Planner (you will see "Keyword Planner verified: X searches/mo" next to them), TRUST THE VERIFIED ABSOLUTE VOLUME, not the rising %. NEVER recommend building a collection page, blog post, or ad campaign for a query with fewer than 50 verified monthly searches no matter how high its rising % — it's low-base noise. Treat 50–200/mo as a modest opportunity worth a paragraph or tag, and 200+/mo as a real surge worth dedicated content.

TRANSPARENCY: When recommending action on a specific query or trend, briefly cite the source in the action description so the reader can judge it — e.g. "(per Search Console: 142 impressions, position 12)", "(per Keyword Planner: 480/mo, EUR 0.30 CPC, LOW competition)", "(per Google Trends rising 850%, but Keyword Planner shows only 30/mo — skip)". This helps the user trust your recommendations and overrule them when appropriate.

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


def _format_trends_data(trends: list[str] | None, related: dict | None) -> str:
    """Format Google Trends data for the prompt.

    If rising queries have been cross-referenced with Keyword Planner, the
    formatter shows the verified absolute volume + CPC alongside the rising %.
    Atlas should distrust high rising % when verified volume is tiny (<50/mo).
    """
    sections = []
    if trends:
        lines = "\n".join(f"- {t}" for t in trends[:15])
        sections.append(f"TRENDING SEARCHES TODAY (Google Trends, raw):\n{lines}")
    if related:
        rising = related.get("rising", [])
        if rising:
            lines_out = []
            for r in rising[:10]:
                base = f"- \"{r['query']}\" (rising {r['value']}%, from seed: {r['seed']})"
                if r.get("verified_volume") is not None:
                    vol = r["verified_volume"]
                    cpc = r.get("verified_cpc_eur", "?")
                    comp = r.get("verified_competition", "?")
                    if vol < 50:
                        flag = " ⚠ LOW-BASE NOISE — verified volume is tiny, ignore"
                    elif vol < 200:
                        flag = " ⚠ small absolute volume — modest opportunity"
                    else:
                        flag = " ✓ verified real volume"
                    base += (
                        f"\n    → Keyword Planner verified: {vol} searches/mo, "
                        f"CPC EUR {cpc}, competition {comp}{flag}"
                    )
                else:
                    base += "\n    → Keyword Planner verification unavailable"
                lines_out.append(base)
            sections.append(
                "RISING RELATED QUERIES (Google Trends, last 3 months — cross-referenced with Keyword Planner):\n"
                + "\n".join(lines_out)
            )
    if not sections:
        return ""
    return "\nGOOGLE TRENDS DATA:\n" + "\n\n".join(sections) + "\n"


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
    trends_data = fetch_trending_searches(market_code)
    related_data = fetch_related_topics(market_code)

    # Cross-reference Google Trends "rising" queries with Keyword Planner
    # to distinguish real surges from low-base noise. Mutates related_data
    # in place so both prompt formatter and email pick up the verified fields.
    if related_data and related_data.get("rising"):
        rising_terms = [r["query"] for r in related_data["rising"][:10]]
        verified = fetch_historical_metrics(rising_terms, market_code)
        if verified:
            for r in related_data["rising"][:10]:
                m = verified.get(r["query"].lower())
                if m:
                    r["verified_volume"] = m["avg_monthly_searches"]
                    r["verified_cpc_eur"] = f"{m['low_cpc_eur']}-{m['high_cpc_eur']}"
                    r["verified_competition"] = m["competition"]
            logger.info(
                f"Cross-referenced {sum(1 for r in related_data['rising'][:10] if 'verified_volume' in r)} "
                f"rising queries against Keyword Planner volume"
            )

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
{_format_trends_data(trends_data, related_data)}
{build_trends_prompt(market_code)}
{build_memory_prompt("atlas")}
Generate today's ranking recommendations focused on the {market_name} market. Be specific and actionable. Use the real keyword data for your Google Ads suggestions instead of estimating. If there are relevant trending topics, suggest how to capitalize on them (blog posts, ads, social media)."""

    # Call Claude
    report = await llm_client.generate_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=2048,
        temperature=0.8,
    )

    # Save performance trends for week-over-week tracking
    if gsc_data and (gsc_data.get("queries") or gsc_data.get("pages")):
        save_performance_trend(market_code, gsc_data)

    # Check active campaign performance
    perf_data = await review_campaign_performance()

    # Save to history
    report["generated_at"] = now.isoformat()
    if perf_data:
        report["campaign_performance"] = perf_data.get("review", {})
    history.append(report)
    _save_history(history)

    # Send email
    html = _build_email_html(report, market_name, market_code, now, gsc_data, keyword_data, perf_data,
                             trends_data, related_data)
    day_name = now.strftime("%a")
    await send_agent_email(
        subject=f"[Atlas] {market_name} briefing — {day_name}, {now.strftime('%b %d')}",
        html_body=html,
        extra_recipients=EXTRA_RECIPIENTS,
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


def _build_trends_section_html(trends: list[str] | None, related: dict | None) -> str:
    """Build the Google Trends section for the email."""
    if not trends and not related:
        return ""

    trending_html = ""
    if trends:
        items = "".join(
            f'<span style="display:inline-block;padding:3px 10px;background:#ede9fe;color:#5b21b6;'
            f'border-radius:12px;font-size:12px;margin:3px;">{t}</span>'
            for t in trends[:12]
        )
        trending_html = f"""
            <h4 style="color:#7c3aed;margin:0 0 8px;">Trending Searches Today</h4>
            <div style="margin-bottom:12px;">{items}</div>"""

    rising_html = ""
    if related and related.get("rising"):
        rows = ""
        for r in related["rising"][:8]:
            verified_cell = "<span style='color:#9ca3af;'>—</span>"
            verdict_cell = "<span style='color:#9ca3af;font-size:11px;'>not verified</span>"
            if r.get("verified_volume") is not None:
                vol = r["verified_volume"]
                verified_cell = f"{vol}/mo<br><span style='font-size:10px;color:#6b7280;'>EUR {r.get('verified_cpc_eur', '?')}</span>"
                if vol < 50:
                    verdict_cell = "<span style='color:#dc2626;font-size:11px;font-weight:bold;'>NOISE</span>"
                elif vol < 200:
                    verdict_cell = "<span style='color:#d97706;font-size:11px;font-weight:bold;'>MODEST</span>"
                else:
                    verdict_cell = "<span style='color:#059669;font-size:11px;font-weight:bold;'>REAL</span>"
            rows += (
                f"<tr>"
                f"<td style='padding:3px 8px;border-bottom:1px solid #e5e7eb;font-size:12px;'>{r['query']}</td>"
                f"<td style='padding:3px 8px;border-bottom:1px solid #e5e7eb;font-size:12px;text-align:center;color:#7c3aed;font-weight:bold;'>{r['value']}%</td>"
                f"<td style='padding:3px 8px;border-bottom:1px solid #e5e7eb;font-size:12px;text-align:center;'>{verified_cell}</td>"
                f"<td style='padding:3px 8px;border-bottom:1px solid #e5e7eb;font-size:11px;text-align:center;'>{verdict_cell}</td>"
                f"<td style='padding:3px 8px;border-bottom:1px solid #e5e7eb;font-size:11px;color:#6b7280;'>{r['seed']}</td>"
                f"</tr>"
            )
        rising_html = f"""
            <h4 style="color:#7c3aed;margin:12px 0 4px;">Rising Related Queries
                <span style="font-size:11px;font-weight:normal;color:#6b7280;">(cross-referenced with Keyword Planner)</span>
            </h4>
            <table style="width:100%;border-collapse:collapse;">
                <thead><tr style="background:#ede9fe;">
                    <th style="padding:3px 8px;text-align:left;font-size:11px;">Query</th>
                    <th style="padding:3px 8px;text-align:center;font-size:11px;">Trends growth</th>
                    <th style="padding:3px 8px;text-align:center;font-size:11px;">Verified volume</th>
                    <th style="padding:3px 8px;text-align:center;font-size:11px;">Verdict</th>
                    <th style="padding:3px 8px;text-align:left;font-size:11px;">Seed</th>
                </tr></thead>
                <tbody>{rows}</tbody>
            </table>
            <p style="font-size:11px;color:#6b7280;margin:6px 0 0;">
                <strong>NOISE</strong> = &lt;50/mo (low-base growth, ignore) ·
                <strong>MODEST</strong> = 50–200/mo · <strong>REAL</strong> = 200+/mo
            </p>"""

    return f"""
        <div style="padding:20px;background:#f5f3ff;border:1px solid #c4b5fd;border-top:none;">
            <h3 style="color:#7c3aed;margin-top:0;">Google Trends <span style="font-size:11px;font-weight:normal;">(live data)</span></h3>
            {trending_html}
            {rising_html}
        </div>"""


def _build_sources_section_html(
    gsc_data: dict | None,
    keyword_data: list[dict] | None,
    trends_data: list[str] | None,
    related_data: dict | None,
) -> str:
    """Explain to the reader where each piece of today's intel came from."""
    has_gsc = bool(gsc_data and (gsc_data.get("queries") or gsc_data.get("pages")))
    has_kw = bool(keyword_data)
    has_trends = bool(trends_data)
    has_rising = bool(related_data and related_data.get("rising"))
    cross_ref_count = (
        sum(1 for r in related_data["rising"][:10] if r.get("verified_volume") is not None)
        if has_rising else 0
    )

    def _row(label: str, available: bool, body: str) -> str:
        dot = "#059669" if available else "#9ca3af"
        return (
            f"<li style='margin-bottom:8px;'>"
            f"<span style='display:inline-block;width:8px;height:8px;background:{dot};"
            f"border-radius:50%;margin-right:8px;'></span>"
            f"<strong>{label}:</strong> {body}"
            f"</li>"
        )

    period = (gsc_data or {}).get("period", "")
    rows = [
        _row(
            "Google Search Console",
            has_gsc,
            f"Real omg.com.cy traffic ({period or 'no data yet'}, ~3 day lag). "
            "Source for top queries, CTR, position, top pages.",
        ),
        _row(
            "Google Ads Keyword Planner",
            has_kw,
            "Real monthly search volumes, CPC ranges, and competition levels around our seed keywords. "
            "Source for everything in the Google Ads suggestions table below.",
        ),
        _row(
            "Google Trends (live)",
            has_trends,
            "Trending searches and rising related queries via pytrends. "
            "Caveat: 'rising %' is relative growth vs the previous 3-month baseline of "
            "THAT exact query, NOT absolute volume. A query going from 1 → 9 searches counts as 800%.",
        ),
        _row(
            "Trends ↔ Keyword Planner cross-reference",
            cross_ref_count > 0,
            f"Verified {cross_ref_count} rising queries against real Keyword Planner volume. "
            "The 'Verdict' column in the Rising table flags low-base noise (&lt;50/mo) so we don't "
            "build a collection page for a query nobody actually searches for.",
        ),
        _row(
            "Shopify Admin API",
            True,
            "Live product catalog and blog articles — used to ground recommendations in what we actually sell.",
        ),
        _row(
            "Internal report history",
            True,
            "Last 5 Atlas reports — used to avoid repeating yesterday's recommendations.",
        ),
    ]

    return f"""
        <div style="padding:20px;background:#fffbeb;border:1px solid #fcd34d;border-top:none;">
            <h3 style="color:#92400e;margin-top:0;">How I sourced today's intel</h3>
            <ul style="margin:0;padding-left:0;list-style:none;font-size:13px;color:#374151;">
                {''.join(rows)}
            </ul>
            <p style="margin:12px 0 0;font-size:11px;color:#6b7280;font-style:italic;">
                When I cite a source inline (e.g. "per Search Console: 142 impressions, position 12"),
                that's where the number came from. Overrule me when the data tells a different story than your gut.
            </p>
        </div>"""


def _build_email_html(report: dict, market_name: str, market_code: str, now: datetime,
                      gsc_data: dict | None = None, keyword_data: list[dict] | None = None,
                      perf_data: dict | None = None,
                      trends_data: list[str] | None = None, related_data: dict | None = None) -> str:
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

    has_trends = bool(trends_data)
    if has_trends:
        sources.append(f'<span style="display:inline-block;padding:3px 10px;background:#7c3aed;color:white;border-radius:12px;font-size:11px;font-weight:bold;margin-right:6px;">TRENDS: LIVE</span>')

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

        {_build_sources_section_html(gsc_data, keyword_data, trends_data, related_data)}

        {_build_gsc_section_html(gsc_data) if has_gsc else ''}
        {_build_trends_section_html(trends_data, related_data) if has_trends else ''}

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

        {build_performance_email_html(perf_data) if perf_data else ''}

        <div style="padding:16px;text-align:center;color:#9ca3af;font-size:12px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
            Your strategist, Atlas | <a href="{settings.server_base_url}/agents/ranking/history" style="color:#6b7280;">View History</a> | <a href="{settings.server_base_url}/agents/feedback/form?agent=atlas" style="color:#6b7280;">Give Feedback</a>
        </div>
    </div>
    """


def get_history(limit: int = 30) -> list[dict]:
    """Return recent ranking reports."""
    history = _load_history()
    return history[-limit:]


# ── Campaign Proposals ──────────────────────────────────────────────

CAMPAIGN_PROPOSAL_PROMPT = """You are Atlas, the Google Ads strategist for OMG (omg.com.cy), a Cyprus-based t-shirt store.

Based on the product catalog, keyword data, and Google Trends below, propose ONE Google Ads Search campaign.

RULES:
- Daily budget MUST be between EUR 3 and EUR 10
- Max CPC MUST be between EUR 0.10 and EUR 1.00
- Choose 5-10 keywords with appropriate match types (BROAD, PHRASE, or EXACT)
- Write 5 ad headlines (max 30 characters each) and 3 descriptions (max 90 characters each)
- The final_url should be a specific product or collection page, not the homepage
- Focus on the given market
- If Google Trends shows relevant trending topics, incorporate them into keywords and ad copy
- Prefer keywords that match current trends — ride the wave of what people are searching for NOW

Output JSON:
{
  "campaign_name": "Short descriptive name",
  "market": "CY|GR|EU",
  "daily_budget_eur": 5.00,
  "max_cpc_eur": 0.30,
  "final_url": "https://omg.com.cy/collections/...",
  "keywords": [
    {"keyword": "keyword text", "match_type": "PHRASE"}
  ],
  "ad_headlines": ["Headline 1", "Headline 2", ...],
  "ad_descriptions": ["Description 1", "Description 2", ...],
  "reasoning": "Why this campaign and these keywords"
}"""


async def propose_all_campaigns() -> list[dict]:
    """Propose campaigns for all 3 markets (CY, GR, EU). Scheduled weekly."""
    results = []
    for market in ("CY", "GR", "EU"):
        try:
            proposal = await propose_campaign(market_override=market)
            results.append({"market": market, "proposal_id": proposal["id"], "status": "proposed"})
        except Exception as e:
            logger.error(f"Campaign proposal for {market} failed: {e}")
            results.append({"market": market, "status": "failed", "error": str(e)})
    return results


async def propose_campaign(market_override: str | None = None) -> dict:
    """Generate a campaign proposal and send it for approval via email."""
    try:
        return await _propose_campaign_impl(market_override)
    except Exception as e:
        logger.exception("Campaign proposal failed")
        from app.agents.agent_email import send_error_email
        await send_error_email("Atlas", e, "campaign proposal")
        raise


async def _propose_campaign_impl(market_override: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    weekday = now.weekday()

    if market_override:
        market_code = market_override
        market_name = {"CY": "Cyprus", "GR": "Greece", "EU": "Europe"}.get(
            market_override, market_override
        )
    else:
        market_code, market_name = MARKET_ROTATION.get(weekday, ("EU", "Europe"))

    products = await _fetch_products()
    keyword_data = fetch_keyword_ideas(market_code)
    trends_data = fetch_trending_searches(market_code)
    related_data = fetch_related_topics(market_code)

    product_summary = "\n".join(
        f"- {p['title']} (handle: {p['handle']}, price: {p['variants'][0]['price']} EUR, "
        f"url: https://omg.com.cy/products/{p['handle']})"
        for p in products
    ) or "No products found"

    kw_section = _format_keyword_data(keyword_data)
    trends_section = _format_trends_data(trends_data, related_data)

    user_prompt = f"""Market: {market_name} ({market_code})
Store: https://omg.com.cy

PRODUCTS:
{product_summary}
{kw_section}
{trends_section}
Propose a campaign for the {market_name} market. Incorporate relevant trends if any."""

    proposal_data = await llm_client.generate_json(
        system_prompt=CAMPAIGN_PROPOSAL_PROMPT,
        user_prompt=user_prompt,
        max_tokens=1024,
        temperature=0.7,
    )

    # Enforce budget cap
    from app.agents.google_ads_manager import MAX_DAILY_BUDGET_EUR
    proposal_data["daily_budget_eur"] = min(
        proposal_data.get("daily_budget_eur", 5.0), MAX_DAILY_BUDGET_EUR
    )
    proposal_data["market"] = market_code

    # Create approval proposal
    from app.agents.approval import create_proposal, approval_url
    proposal = create_proposal("ads", proposal_data)

    approve_url = approval_url(proposal["id"], proposal["token"], "approve")
    reject_url = approval_url(proposal["id"], proposal["token"], "reject")

    # Build email
    keywords_html = "".join(
        f"<tr><td style='padding:4px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;'>{kw['keyword']}</td>"
        f"<td style='padding:4px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;'>{kw.get('match_type', 'PHRASE')}</td></tr>"
        for kw in proposal_data.get("keywords", [])
    )

    headlines_html = "".join(
        f"<li style='font-size:13px;'>{h}</li>"
        for h in proposal_data.get("ad_headlines", [])
    )

    descriptions_html = "".join(
        f"<li style='font-size:13px;'>{d}</li>"
        for d in proposal_data.get("ad_descriptions", [])
    )

    html = f"""
    <div style="font-family:sans-serif;max-width:650px;margin:0 auto;color:#111;">
        <div style="background:#1e40af;color:white;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">Atlas — Campaign Proposal</h2>
            <p style="margin:4px 0 0;opacity:0.9;">New Google Ads campaign for {market_name} — needs your approval</p>
        </div>

        <div style="padding:20px;background:#f9fafb;border:1px solid #e5e7eb;">
            <h3 style="margin-top:0;color:#1e40af;">{proposal_data.get('campaign_name', 'Untitled')}</h3>
            <table style="width:100%;font-size:14px;">
                <tr><td style="padding:4px 0;color:#6b7280;">Market:</td><td><strong>{market_name}</strong></td></tr>
                <tr><td style="padding:4px 0;color:#6b7280;">Daily Budget:</td><td><strong>EUR {proposal_data.get('daily_budget_eur', '?')}</strong></td></tr>
                <tr><td style="padding:4px 0;color:#6b7280;">Max CPC:</td><td><strong>EUR {proposal_data.get('max_cpc_eur', '?')}</strong></td></tr>
                <tr><td style="padding:4px 0;color:#6b7280;">Landing Page:</td><td><a href="{proposal_data.get('final_url', '#')}" style="color:#2563eb;">{proposal_data.get('final_url', '?')}</a></td></tr>
            </table>

            <p style="margin-top:12px;color:#374151;font-size:13px;"><strong>Reasoning:</strong> {proposal_data.get('reasoning', '')}</p>
        </div>

        <div style="padding:20px;border:1px solid #e5e7eb;border-top:none;">
            <h4 style="color:#dc2626;margin-top:0;">Keywords ({len(proposal_data.get('keywords', []))})</h4>
            <table style="width:100%;border-collapse:collapse;">
                <thead><tr style="background:#f3f4f6;">
                    <th style="padding:4px 8px;text-align:left;font-size:12px;">Keyword</th>
                    <th style="padding:4px 8px;text-align:center;font-size:12px;">Match Type</th>
                </tr></thead>
                <tbody>{keywords_html}</tbody>
            </table>
        </div>

        <div style="padding:20px;background:#f9fafb;border:1px solid #e5e7eb;border-top:none;">
            <h4 style="color:#7c3aed;margin-top:0;">Ad Copy</h4>
            <p style="font-size:12px;color:#6b7280;margin:0 0 8px;">Headlines (max 30 chars):</p>
            <ul style="margin:0 0 12px;padding-left:20px;">{headlines_html}</ul>
            <p style="font-size:12px;color:#6b7280;margin:0 0 8px;">Descriptions (max 90 chars):</p>
            <ul style="margin:0;padding-left:20px;">{descriptions_html}</ul>
        </div>

        <div style="padding:20px;border:1px solid #e5e7eb;border-top:none;">
            <p style="margin:0 0 8px;color:#6b7280;font-size:13px;">Campaign will be created in <strong>PAUSED</strong> state. You can enable it from Google Ads after review.</p>
            <div style="text-align:center;margin:16px 0;">
                <a href="{approve_url}" style="display:inline-block;padding:12px 32px;background:#059669;color:white;text-decoration:none;border-radius:6px;font-weight:bold;font-size:16px;margin-right:12px;">APPROVE</a>
                <a href="{reject_url}" style="display:inline-block;padding:12px 32px;background:#dc2626;color:white;text-decoration:none;border-radius:6px;font-weight:bold;font-size:16px;">REJECT</a>
            </div>
        </div>

        <div style="padding:12px;text-align:center;color:#9ca3af;font-size:12px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
            Atlas | Budget cap: EUR {MAX_DAILY_BUDGET_EUR}/day max
        </div>
    </div>
    """

    await send_agent_email(
        subject=f"[Atlas] Campaign proposal — {proposal_data.get('campaign_name', 'New')} ({market_name})",
        html_body=html,
        extra_recipients=EXTRA_RECIPIENTS,
    )

    logger.info(f"Campaign proposal sent: {proposal_data.get('campaign_name')}")
    return proposal


async def execute_campaign_approval(proposal_id: str) -> dict:
    """Execute an approved campaign proposal — create it in Google Ads."""
    from app.agents.approval import get_proposal, update_status
    from app.agents.google_ads_manager import create_search_campaign

    proposal = get_proposal(proposal_id)
    if not proposal:
        raise ValueError(f"Proposal {proposal_id} not found")

    result = create_search_campaign(proposal["data"])
    update_status(proposal_id, "approved")

    # Send confirmation email
    html = f"""
    <div style="font-family:sans-serif;max-width:650px;margin:0 auto;color:#111;">
        <div style="background:#059669;color:white;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">Campaign Created!</h2>
            <p style="margin:4px 0 0;opacity:0.9;">Atlas has set up your Google Ads campaign</p>
        </div>
        <div style="padding:20px;border:1px solid #e5e7eb;">
            <table style="font-size:14px;">
                <tr><td style="padding:4px 8px;color:#6b7280;">Campaign:</td><td><strong>{proposal['data'].get('campaign_name', '?')}</strong></td></tr>
                <tr><td style="padding:4px 8px;color:#6b7280;">Campaign ID:</td><td>{result['campaign_id']}</td></tr>
                <tr><td style="padding:4px 8px;color:#6b7280;">Budget:</td><td>EUR {result['daily_budget_eur']}/day</td></tr>
                <tr><td style="padding:4px 8px;color:#6b7280;">Keywords:</td><td>{result['keywords_count']}</td></tr>
                <tr><td style="padding:4px 8px;color:#6b7280;">Status:</td><td><strong style="color:#d97706;">PAUSED</strong></td></tr>
            </table>
            <p style="margin-top:16px;padding:12px;background:#fef3c7;border-radius:6px;font-size:13px;">
                The campaign is <strong>paused</strong>. Go to
                <a href="https://ads.google.com/aw/campaigns?campaignId={result['campaign_id']}" style="color:#2563eb;">Google Ads</a>
                to review and enable it when ready.
            </p>
        </div>
        <div style="padding:12px;text-align:center;color:#9ca3af;font-size:12px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
            Atlas | Performance review will be in tomorrow's briefing
        </div>
    </div>
    """

    await send_agent_email(
        subject=f"[Atlas] Campaign live — {proposal['data'].get('campaign_name', 'New')}",
        html_body=html,
        extra_recipients=EXTRA_RECIPIENTS,
    )

    return result


# ── Performance Review ───────────────────────────────────────────────

PERFORMANCE_REVIEW_PROMPT = """You are Atlas, the Google Ads analyst for OMG (omg.com.cy).

Review the campaign performance data below and provide specific, actionable recommendations.

For each campaign, consider:
- Is the budget being spent efficiently? (check CPC vs industry average for t-shirts)
- Which keywords are performing well vs poorly? (pause poor performers, increase bids on winners)
- Is the CTR healthy? (search ads should be >2% CTR, if below suggest ad copy changes)
- Any conversions? What's the cost per conversion?
- Should we increase/decrease budget?
- Should we add new keywords or negative keywords?

Output JSON:
{
  "summary": "One-paragraph overall assessment",
  "campaign_reviews": [
    {
      "campaign_name": "...",
      "campaign_id": "...",
      "verdict": "performing|underperforming|needs_changes|pause",
      "changes": [
        {
          "action": "pause_keyword|add_keyword|adjust_bid|adjust_budget|update_ad_copy|add_negative",
          "detail": "Specific change to make",
          "reasoning": "Why"
        }
      ]
    }
  ],
  "budget_recommendation": "Keep at EUR X/day | Increase to EUR X/day | Decrease to EUR X/day"
}"""


async def review_campaign_performance() -> dict | None:
    """Review active campaign performance and email recommendations.

    Called as part of the daily Atlas briefing.
    """
    from app.agents.google_ads_manager import fetch_campaign_performance, fetch_keyword_performance

    campaigns = fetch_campaign_performance(days=1)
    if not campaigns:
        return None

    # Also fetch keyword-level data for each campaign
    campaign_details = []
    for c in campaigns:
        kw_perf = fetch_keyword_performance(c["campaign_id"], days=7)
        c["keyword_performance"] = kw_perf or []
        campaign_details.append(c)

    if not campaign_details:
        return None

    # Format for Claude
    perf_lines = []
    for c in campaign_details:
        perf_lines.append(
            f"\nCampaign: {c['name']} (ID: {c['campaign_id']}, status: {c['status']})\n"
            f"  Budget: EUR {c['daily_budget_eur']}/day | Spent: EUR {c['cost_eur']} | "
            f"Impressions: {c['impressions']} | Clicks: {c['clicks']} | CTR: {c['ctr']}% | "
            f"Avg CPC: EUR {c['avg_cpc_eur']} | Conversions: {c['conversions']}"
        )
        for kw in c.get("keyword_performance", [])[:10]:
            perf_lines.append(
                f"    [{kw['match_type']}] \"{kw['keyword']}\" — "
                f"impr: {kw['impressions']}, clicks: {kw['clicks']}, "
                f"CTR: {kw['ctr']}%, CPC: EUR {kw['avg_cpc_eur']}, "
                f"cost: EUR {kw['cost_eur']}"
            )

    user_prompt = f"CAMPAIGN PERFORMANCE DATA (last 24h, keyword data last 7 days):\n{''.join(perf_lines)}"

    review = await llm_client.generate_json(
        system_prompt=PERFORMANCE_REVIEW_PROMPT,
        user_prompt=user_prompt,
        max_tokens=1500,
        temperature=0.5,
    )

    return {
        "campaigns": campaign_details,
        "review": review,
    }


def build_performance_email_html(perf_data: dict) -> str:
    """Build email HTML for campaign performance review."""
    campaigns = perf_data.get("campaigns", [])
    review = perf_data.get("review", {})

    # Campaign metrics table
    metrics_html = ""
    for c in campaigns:
        status_color = "#059669" if c["status"] == "ENABLED" else "#d97706"
        metrics_html += f"""
        <tr>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;font-weight:bold;">{c['name'].replace('{CAMPAIGN_PREFIX} — ', '')}</td>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;color:{status_color};">{c['status']}</td>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;">{c['impressions']}</td>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;">{c['clicks']}</td>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;">{c['ctr']}%</td>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;">EUR {c['avg_cpc_eur']}</td>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;text-align:center;font-weight:bold;">EUR {c['cost_eur']}</td>
        </tr>"""

    # Review recommendations
    reviews_html = ""
    verdict_colors = {
        "performing": "#059669", "underperforming": "#dc2626",
        "needs_changes": "#d97706", "pause": "#6b7280",
    }
    for cr in review.get("campaign_reviews", []):
        v_color = verdict_colors.get(cr.get("verdict", ""), "#6b7280")
        changes_html = "".join(
            f"<li style='margin-bottom:6px;font-size:13px;'>"
            f"<strong>{ch.get('action', '?').replace('_', ' ').title()}</strong>: {ch.get('detail', '')}"
            f"<br><span style='color:#6b7280;font-size:12px;'>{ch.get('reasoning', '')}</span></li>"
            for ch in cr.get("changes", [])
        )
        reviews_html += f"""
        <div style="margin-bottom:16px;padding:12px;background:#f9fafb;border-radius:6px;border-left:4px solid {v_color};">
            <strong>{cr.get('campaign_name', '?')}</strong>
            <span style="display:inline-block;padding:2px 8px;background:{v_color};color:white;border-radius:10px;font-size:11px;margin-left:8px;">{cr.get('verdict', '?').upper()}</span>
            <ul style="margin:8px 0 0;padding-left:20px;">{changes_html}</ul>
        </div>"""

    return f"""
        <div style="padding:20px;background:#fef3c7;border:1px solid #fbbf24;border-top:none;">
            <h3 style="color:#d97706;margin-top:0;">Google Ads Performance Review <span style="font-size:11px;font-weight:normal;color:#059669;">(real data)</span></h3>
            <p style="font-size:13px;color:#374151;">{review.get('summary', '')}</p>

            <table style="width:100%;border-collapse:collapse;margin:12px 0;">
                <thead><tr style="background:#fde68a;">
                    <th style="padding:4px 8px;text-align:left;font-size:11px;">Campaign</th>
                    <th style="padding:4px 8px;text-align:center;font-size:11px;">Status</th>
                    <th style="padding:4px 8px;text-align:center;font-size:11px;">Impr.</th>
                    <th style="padding:4px 8px;text-align:center;font-size:11px;">Clicks</th>
                    <th style="padding:4px 8px;text-align:center;font-size:11px;">CTR</th>
                    <th style="padding:4px 8px;text-align:center;font-size:11px;">Avg CPC</th>
                    <th style="padding:4px 8px;text-align:center;font-size:11px;">Cost</th>
                </tr></thead>
                <tbody>{metrics_html}</tbody>
            </table>

            {reviews_html}

            <p style="margin-top:12px;font-size:14px;padding:8px;background:#fef9c3;border-radius:4px;">
                <strong>Budget recommendation:</strong> {review.get('budget_recommendation', 'No change')}
            </p>
        </div>"""
