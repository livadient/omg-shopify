"""Fetch trending searches and interest data from Google Trends."""
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Google Trends geo codes
GEO_CODES = {
    "CY": "CY",
    "GR": "GR",
    "EU": "",  # Empty = worldwide
}

# Our core product categories for trend comparison
CORE_KEYWORDS = [
    "graphic tees",
    "custom t-shirt",
    "funny t-shirt",
    "slogan tee",
]

CORE_KEYWORDS_GR = [
    "μπλουζάκια",
    "t-shirt",
    "graphic tee",
    "funny t-shirt",
]


def fetch_trending_searches(market_code: str = "CY") -> list[str] | None:
    """Fetch today's trending searches for a market.

    Returns list of trending search terms, or None on error.
    """
    try:
        from pytrends.request import TrendReq
        import time

        pytrends = TrendReq(hl="en-US", tz=120)

        # pytrends.trending_searches uses country names, not codes
        country_map = {"CY": "greece", "GR": "greece", "EU": "united_states"}
        pn = country_map.get(market_code, "united_states")

        trending = pytrends.trending_searches(pn=pn)
        terms = trending[0].tolist() if not trending.empty else []
        logger.info(f"Google Trends: {len(terms)} trending searches for {market_code}")
        return terms[:20]

    except Exception as e:
        logger.warning(f"Google Trends trending searches failed: {e}")
        return None


def fetch_related_topics(market_code: str = "CY") -> dict | None:
    """Fetch related topics and queries for t-shirt/fashion keywords.

    Returns dict with 'rising' and 'top' related queries, or None on error.
    """
    try:
        from pytrends.request import TrendReq

        import time
        time.sleep(2)  # Avoid rate limiting after trending_searches call

        pytrends = TrendReq(hl="en-US", tz=120)
        geo = GEO_CODES.get(market_code, "")
        keywords = CORE_KEYWORDS_GR if market_code in ("CY", "GR") else CORE_KEYWORDS

        # Build payload — pytrends supports max 5 keywords at once
        pytrends.build_payload(
            keywords[:5],
            cat=0,
            timeframe="today 3-m",
            geo=geo,
        )

        # Related queries
        related = pytrends.related_queries()

        rising = []
        top = []
        for kw, data in related.items():
            if data.get("rising") is not None and not data["rising"].empty:
                for _, row in data["rising"].head(10).iterrows():
                    rising.append({
                        "query": row["query"],
                        "value": str(row["value"]),
                        "seed": kw,
                    })
            if data.get("top") is not None and not data["top"].empty:
                for _, row in data["top"].head(10).iterrows():
                    top.append({
                        "query": row["query"],
                        "value": str(row["value"]),
                        "seed": kw,
                    })

        logger.info(f"Google Trends: {len(rising)} rising, {len(top)} top queries for {market_code}")
        return {"rising": rising, "top": top}

    except Exception as e:
        logger.warning(f"Google Trends related queries failed: {e}")
        return None


def fetch_interest_over_time(keywords: list[str], market_code: str = "CY") -> dict | None:
    """Fetch interest over time for specific keywords.

    Returns dict mapping keyword -> latest interest score (0-100), or None on error.
    """
    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="en-US", tz=120)
        geo = GEO_CODES.get(market_code, "")

        pytrends.build_payload(
            keywords[:5],
            cat=0,
            timeframe="today 3-m",
            geo=geo,
        )

        interest = pytrends.interest_over_time()
        if interest.empty:
            return None

        # Get latest values
        latest = interest.iloc[-1]
        result = {kw: int(latest.get(kw, 0)) for kw in keywords if kw in latest.index}

        logger.info(f"Google Trends interest: {result}")
        return result

    except Exception as e:
        logger.warning(f"Google Trends interest over time failed: {e}")
        return None
