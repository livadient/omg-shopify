"""Fetch real keyword data from Google Ads Keyword Planner."""
import logging

from app.config import settings

logger = logging.getLogger(__name__)

# Geo target constants for Google Ads API
GEO_TARGETS = {
    "CY": "2196",   # Cyprus
    "GR": "2300",   # Greece
    "EU": "2067",   # Europe (as a region — not perfect but covers broad EU)
}

# Language constants
LANGUAGE_CONSTANTS = {
    "CY": "1022",  # Greek
    "GR": "1022",  # Greek
    "EU": "1000",  # English
}

# Seed keywords per market
SEED_KEYWORDS = {
    "CY": [
        "t-shirt cyprus", "μπλουζάκια κύπρος", "graphic tees cyprus",
        "custom t-shirt", "funny t-shirt", "slogan tee",
    ],
    "GR": [
        "t-shirt ελλάδα", "μπλουζάκια", "graphic tees greece",
        "custom t-shirt greece", "funny t-shirt greek",
    ],
    "EU": [
        "graphic tees europe", "custom t-shirt", "funny t-shirts",
        "slogan tee", "unique t-shirts online",
    ],
}


def _get_client():
    """Build authenticated Google Ads client."""
    if not all([
        settings.google_ads_developer_token,
        settings.google_ads_client_id,
        settings.google_ads_client_secret,
        settings.google_ads_refresh_token,
        settings.google_ads_customer_id,
    ]):
        return None

    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError:
        logger.warning("google-ads package not installed, skipping keyword planner")
        return None

    return GoogleAdsClient.load_from_dict({
        "developer_token": settings.google_ads_developer_token,
        "client_id": settings.google_ads_client_id,
        "client_secret": settings.google_ads_client_secret,
        "refresh_token": settings.google_ads_refresh_token,
        "login_customer_id": settings.google_ads_customer_id,
        "use_proto_plus": True,
    })


def fetch_keyword_ideas(
    market_code: str = "CY",
    seed_keywords: list[str] | None = None,
    max_results: int = 30,
) -> list[dict] | None:
    """Fetch keyword ideas with real CPC and volume from Google Ads.

    Returns list of dicts with keyword, avg_monthly_searches, competition,
    low_cpc, high_cpc — or None if not configured.
    """
    client = _get_client()
    if not client:
        logger.info("Google Ads not configured, skipping keyword planner")
        return None

    customer_id = settings.google_ads_customer_id

    try:
        keyword_plan_idea_service = client.get_service("KeywordPlanIdeaService")
        request = client.get_type("GenerateKeywordIdeasRequest")
        request.customer_id = customer_id

        # Set language
        lang_id = LANGUAGE_CONSTANTS.get(market_code, "1000")
        request.language = f"languageConstants/{lang_id}"

        # Set geo target
        geo_id = GEO_TARGETS.get(market_code, "2196")
        request.geo_target_constants.append(f"geoTargetConstants/{geo_id}")

        # Set seed keywords
        keywords = seed_keywords or SEED_KEYWORDS.get(market_code, SEED_KEYWORDS["EU"])
        request.keyword_seed.keywords.extend(keywords)

        response = keyword_plan_idea_service.generate_keyword_ideas(request=request)

        results = []
        for idea in response:
            metrics = idea.keyword_idea_metrics
            competition = idea.keyword_idea_metrics.competition.name if metrics.competition else "UNKNOWN"

            results.append({
                "keyword": idea.text,
                "avg_monthly_searches": metrics.avg_monthly_searches or 0,
                "competition": competition,
                "low_cpc_eur": round(metrics.low_top_of_page_bid_micros / 1_000_000, 2) if metrics.low_top_of_page_bid_micros else 0,
                "high_cpc_eur": round(metrics.high_top_of_page_bid_micros / 1_000_000, 2) if metrics.high_top_of_page_bid_micros else 0,
            })

        # Sort by search volume
        results.sort(key=lambda x: x["avg_monthly_searches"], reverse=True)
        results = results[:max_results]

        logger.info(f"Keyword Planner: fetched {len(results)} keyword ideas for {market_code}")
        return results

    except Exception as e:
        logger.error(f"Google Ads Keyword Planner error: {e}")
        return None
