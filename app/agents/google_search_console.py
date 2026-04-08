"""Fetch real search performance data from Google Search Console."""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Country codes for Search Console API (ISO 3166-1 alpha-3)
COUNTRY_CODES = {
    "CY": "cyp",
    "GR": "grc",
    "EU": None,  # No filter = all countries
}

# Market → site mapping (CY uses .com.cy, GR uses .gr, EU queries both)
MARKET_SITES = {
    "CY": ["sc-domain:omg.com.cy"],
    "GR": ["sc-domain:omg.gr"],
    "EU": ["sc-domain:omg.com.cy", "sc-domain:omg.gr"],
}


def _get_configured_sites() -> list[str]:
    """Parse comma-separated site URLs from config."""
    raw = settings.google_search_console_site
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def _get_service():
    """Build authenticated Search Console service."""
    key_file = settings.google_service_account_file
    if not key_file:
        return None

    key_path = Path(key_file)
    if not key_path.is_absolute():
        key_path = Path(__file__).resolve().parent.parent.parent / key_file
    if not key_path.exists():
        logger.warning(f"Service account file not found: {key_path}")
        return None

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        str(key_path),
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
    )
    return build("searchconsole", "v1", credentials=creds)


def _query_site(service, site_url: str, start_date: str, end_date: str,
                country_code: str | None, row_limit: int) -> dict:
    """Query a single site for search performance data."""
    query_body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["query"],
        "rowLimit": row_limit,
        "type": "web",
    }
    if country_code:
        query_body["dimensionFilterGroups"] = [{
            "filters": [{"dimension": "country", "expression": country_code}]
        }]

    query_resp = service.searchanalytics().query(
        siteUrl=site_url, body=query_body
    ).execute()

    page_body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["page"],
        "rowLimit": 25,
        "type": "web",
    }
    if country_code:
        page_body["dimensionFilterGroups"] = [{
            "filters": [{"dimension": "country", "expression": country_code}]
        }]

    page_resp = service.searchanalytics().query(
        siteUrl=site_url, body=page_body
    ).execute()

    return {"query_rows": query_resp.get("rows", []),
            "page_rows": page_resp.get("rows", [])}


def fetch_search_performance(
    market_code: str = "CY",
    days: int = 28,
    row_limit: int = 50,
) -> dict | None:
    """Fetch search queries, impressions, clicks, CTR, and position for a market.

    Maps market to the relevant site(s):
    - CY → omg.com.cy
    - GR → omg.gr
    - EU → both sites combined

    Returns dict with 'queries' and 'pages' lists, or None if not configured.
    """
    service = _get_service()
    if not service:
        logger.info("Google Search Console not configured, skipping")
        return None

    configured_sites = _get_configured_sites()
    if not configured_sites:
        return None

    # Determine which sites to query for this market
    target_sites = MARKET_SITES.get(market_code, configured_sites)
    # Only query sites that are actually configured
    sites_to_query = [s for s in target_sites if s in configured_sites]
    if not sites_to_query:
        sites_to_query = configured_sites  # fallback to all

    now = datetime.now(timezone.utc)
    end_date = (now - timedelta(days=3)).strftime("%Y-%m-%d")  # GSC data has ~3 day lag
    start_date = (now - timedelta(days=days + 3)).strftime("%Y-%m-%d")

    country_code = COUNTRY_CODES.get(market_code)

    try:
        # Collect data from all relevant sites
        all_query_rows = []
        all_page_rows = []
        sites_queried = []

        for site_url in sites_to_query:
            result = _query_site(service, site_url, start_date, end_date,
                                 country_code, row_limit)
            all_query_rows.extend(result["query_rows"])
            all_page_rows.extend(result["page_rows"])
            sites_queried.append(site_url)

        # Merge duplicate queries across sites (same query from both sites)
        merged_queries = {}
        for row in all_query_rows:
            key = row["keys"][0]
            if key in merged_queries:
                existing = merged_queries[key]
                total_clicks = existing["clicks"] + row["clicks"]
                total_impressions = existing["impressions"] + row["impressions"]
                # Weighted average position
                existing["position"] = (
                    (existing["position"] * existing["impressions"]
                     + row["position"] * row["impressions"])
                    / max(total_impressions, 1)
                )
                existing["clicks"] = total_clicks
                existing["impressions"] = total_impressions
                existing["ctr"] = total_clicks / max(total_impressions, 1)
            else:
                merged_queries[key] = {
                    "clicks": row["clicks"],
                    "impressions": row["impressions"],
                    "ctr": row["ctr"],
                    "position": row["position"],
                }

        queries = sorted(
            [
                {
                    "query": key,
                    "clicks": v["clicks"],
                    "impressions": v["impressions"],
                    "ctr": round(v["ctr"] * 100, 1),
                    "position": round(v["position"], 1),
                }
                for key, v in merged_queries.items()
            ],
            key=lambda x: x["impressions"],
            reverse=True,
        )[:row_limit]

        pages = [
            {
                "page": row["keys"][0],
                "clicks": row["clicks"],
                "impressions": row["impressions"],
                "ctr": round(row["ctr"] * 100, 1),
                "position": round(row["position"], 1),
            }
            for row in all_page_rows
        ]
        pages.sort(key=lambda x: x["impressions"], reverse=True)
        pages = pages[:25]

        logger.info(
            f"GSC: fetched {len(queries)} queries, {len(pages)} pages "
            f"for {market_code} from {', '.join(sites_queried)} ({start_date} to {end_date})"
        )

        return {
            "queries": queries,
            "pages": pages,
            "period": f"{start_date} to {end_date}",
            "market": market_code,
            "sites": sites_queried,
        }

    except Exception as e:
        logger.error(f"Google Search Console API error: {e}")
        return None
