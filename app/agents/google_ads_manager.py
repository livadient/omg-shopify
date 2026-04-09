"""Google Ads campaign management — create campaigns and fetch performance."""
import logging
from datetime import datetime, timedelta, timezone

from app.config import settings

logger = logging.getLogger(__name__)

# Hard budget cap — Atlas can never set a daily budget above this
MAX_DAILY_BUDGET_EUR = 10.00

# Campaign naming convention
CAMPAIGN_PREFIX = "OMG-Atlas"


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
        logger.warning("google-ads package not installed, skipping")
        return None

    return GoogleAdsClient.load_from_dict({
        "developer_token": settings.google_ads_developer_token,
        "client_id": settings.google_ads_client_id,
        "client_secret": settings.google_ads_client_secret,
        "refresh_token": settings.google_ads_refresh_token,
        "login_customer_id": settings.google_ads_customer_id,
        "use_proto_plus": True,
    })


def create_search_campaign(proposal: dict) -> dict:
    """Create a Google Ads Search campaign from an approved proposal.

    Args:
        proposal: dict with keys:
            - campaign_name: str
            - daily_budget_eur: float (capped at MAX_DAILY_BUDGET_EUR)
            - keywords: list[dict] with {keyword, match_type}
            - ad_headlines: list[str] (3-15 headlines, max 30 chars each)
            - ad_descriptions: list[str] (2-4 descriptions, max 90 chars each)
            - final_url: str (landing page URL)
            - market: str (CY/GR/EU)

    Returns:
        dict with campaign_id, ad_group_id, ad_id, status
    """
    client = _get_client()
    if not client:
        raise RuntimeError("Google Ads not configured")

    customer_id = settings.google_ads_customer_id

    # Enforce budget cap
    daily_budget = min(proposal["daily_budget_eur"], MAX_DAILY_BUDGET_EUR)
    budget_micros = int(daily_budget * 1_000_000)

    # 1. Create campaign budget
    budget_service = client.get_service("CampaignBudgetService")
    budget_op = client.get_type("CampaignBudgetOperation")
    budget = budget_op.create
    budget.name = f"{CAMPAIGN_PREFIX} Budget — {proposal['campaign_name']}"
    budget.amount_micros = budget_micros
    budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD

    budget_response = budget_service.mutate_campaign_budgets(
        customer_id=customer_id, operations=[budget_op]
    )
    budget_resource = budget_response.results[0].resource_name

    # 2. Create campaign
    campaign_service = client.get_service("CampaignService")
    campaign_op = client.get_type("CampaignOperation")
    campaign = campaign_op.create
    campaign.name = f"{CAMPAIGN_PREFIX} — {proposal['campaign_name']}"
    campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
    campaign.status = client.enums.CampaignStatusEnum.PAUSED  # Start paused for safety
    campaign.manual_cpc.enhanced_cpc_enabled = False
    campaign.campaign_budget = budget_resource
    campaign.network_settings.target_google_search = True
    campaign.network_settings.target_search_network = False

    # Set geo targeting via campaign criterion later
    campaign_response = campaign_service.mutate_campaigns(
        customer_id=customer_id, operations=[campaign_op]
    )
    campaign_resource = campaign_response.results[0].resource_name

    # 3. Set geo targeting
    geo_targets = {
        "CY": "2196",
        "GR": "2300",
        "EU": None,  # No geo restriction for EU
    }
    geo_id = geo_targets.get(proposal.get("market", "CY"))
    if geo_id:
        criterion_service = client.get_service("CampaignCriterionService")
        criterion_op = client.get_type("CampaignCriterionOperation")
        criterion = criterion_op.create
        criterion.campaign = campaign_resource
        criterion.location.geo_target_constant = f"geoTargetConstants/{geo_id}"
        criterion_service.mutate_campaign_criteria(
            customer_id=customer_id, operations=[criterion_op]
        )

    # 4. Create ad group
    ad_group_service = client.get_service("AdGroupService")
    ad_group_op = client.get_type("AdGroupOperation")
    ad_group = ad_group_op.create
    ad_group.name = f"{CAMPAIGN_PREFIX} AG — {proposal['campaign_name']}"
    ad_group.campaign = campaign_resource
    ad_group.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
    ad_group.cpc_bid_micros = int(proposal.get("max_cpc_eur", 0.50) * 1_000_000)

    ad_group_response = ad_group_service.mutate_ad_groups(
        customer_id=customer_id, operations=[ad_group_op]
    )
    ad_group_resource = ad_group_response.results[0].resource_name

    # 5. Add keywords
    keyword_service = client.get_service("AdGroupCriterionService")
    keyword_ops = []
    match_types = {
        "BROAD": client.enums.KeywordMatchTypeEnum.BROAD,
        "PHRASE": client.enums.KeywordMatchTypeEnum.PHRASE,
        "EXACT": client.enums.KeywordMatchTypeEnum.EXACT,
    }
    for kw in proposal.get("keywords", []):
        kw_op = client.get_type("AdGroupCriterionOperation")
        criterion = kw_op.create
        criterion.ad_group = ad_group_resource
        criterion.keyword.text = kw["keyword"]
        criterion.keyword.match_type = match_types.get(
            kw.get("match_type", "PHRASE").upper(),
            client.enums.KeywordMatchTypeEnum.PHRASE,
        )
        keyword_ops.append(kw_op)

    if keyword_ops:
        keyword_service.mutate_ad_group_criteria(
            customer_id=customer_id, operations=keyword_ops
        )

    # 6. Create responsive search ad
    ad_service = client.get_service("AdGroupAdService")
    ad_op = client.get_type("AdGroupAdOperation")
    ad_group_ad = ad_op.create
    ad_group_ad.ad_group = ad_group_resource
    ad_group_ad.status = client.enums.AdGroupAdStatusEnum.ENABLED

    ad = ad_group_ad.ad
    ad.final_urls.append(proposal.get("final_url", "https://omg.com.cy"))

    for headline in proposal.get("ad_headlines", [])[:15]:
        headline_asset = client.get_type("AdTextAsset")
        headline_asset.text = headline[:30]
        ad.responsive_search_ad.headlines.append(headline_asset)

    for desc in proposal.get("ad_descriptions", [])[:4]:
        desc_asset = client.get_type("AdTextAsset")
        desc_asset.text = desc[:90]
        ad.responsive_search_ad.descriptions.append(desc_asset)

    ad_response = ad_service.mutate_ad_group_ads(
        customer_id=customer_id, operations=[ad_op]
    )

    # Extract IDs from resource names
    campaign_id = campaign_resource.split("/")[-1]
    ad_group_id = ad_group_resource.split("/")[-1]
    ad_id = ad_response.results[0].resource_name.split("/")[-1]

    logger.info(
        f"Created campaign '{proposal['campaign_name']}': "
        f"campaign={campaign_id}, ad_group={ad_group_id}, budget=EUR {daily_budget}/day, "
        f"status=PAUSED"
    )

    return {
        "campaign_id": campaign_id,
        "campaign_resource": campaign_resource,
        "ad_group_id": ad_group_id,
        "ad_id": ad_id,
        "daily_budget_eur": daily_budget,
        "status": "PAUSED",
        "keywords_count": len(proposal.get("keywords", [])),
    }


def enable_campaign(campaign_id: str) -> bool:
    """Enable (unpause) a campaign."""
    client = _get_client()
    if not client:
        return False

    customer_id = settings.google_ads_customer_id
    campaign_service = client.get_service("CampaignService")
    campaign_op = client.get_type("CampaignOperation")
    campaign = campaign_op.update
    campaign.resource_name = f"customers/{customer_id}/campaigns/{campaign_id}"
    campaign.status = client.enums.CampaignStatusEnum.ENABLED

    field_mask = client.get_type("FieldMask")
    field_mask.paths.append("status")
    campaign_op.update_mask.CopyFrom(field_mask)

    campaign_service.mutate_campaigns(
        customer_id=customer_id, operations=[campaign_op]
    )
    logger.info(f"Campaign {campaign_id} enabled")
    return True


def pause_campaign(campaign_id: str) -> bool:
    """Pause a campaign."""
    client = _get_client()
    if not client:
        return False

    customer_id = settings.google_ads_customer_id
    campaign_service = client.get_service("CampaignService")
    campaign_op = client.get_type("CampaignOperation")
    campaign = campaign_op.update
    campaign.resource_name = f"customers/{customer_id}/campaigns/{campaign_id}"
    campaign.status = client.enums.CampaignStatusEnum.PAUSED

    field_mask = client.get_type("FieldMask")
    field_mask.paths.append("status")
    campaign_op.update_mask.CopyFrom(field_mask)

    campaign_service.mutate_campaigns(
        customer_id=customer_id, operations=[campaign_op]
    )
    logger.info(f"Campaign {campaign_id} paused")
    return True


def fetch_campaign_performance(days: int = 1) -> list[dict] | None:
    """Fetch performance data for all Atlas campaigns.

    Returns list of dicts with campaign metrics, or None if not configured.
    """
    client = _get_client()
    if not client:
        return None

    customer_id = settings.google_ads_customer_id
    ga_service = client.get_service("GoogleAdsService")

    end_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            campaign_budget.amount_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.ctr,
            metrics.average_cpc,
            metrics.cost_micros,
            metrics.conversions,
            metrics.conversions_value
        FROM campaign
        WHERE campaign.name LIKE '{CAMPAIGN_PREFIX}%'
        AND segments.date BETWEEN '{start_date}' AND '{end_date}'
    """

    try:
        response = ga_service.search(customer_id=customer_id, query=query)
        campaigns = []
        for row in response:
            campaigns.append({
                "campaign_id": str(row.campaign.id),
                "name": row.campaign.name,
                "status": row.campaign.status.name,
                "daily_budget_eur": round(row.campaign_budget.amount_micros / 1_000_000, 2),
                "impressions": row.metrics.impressions,
                "clicks": row.metrics.clicks,
                "ctr": round(row.metrics.ctr * 100, 2),
                "avg_cpc_eur": round(row.metrics.average_cpc / 1_000_000, 2),
                "cost_eur": round(row.metrics.cost_micros / 1_000_000, 2),
                "conversions": round(row.metrics.conversions, 1),
                "conversion_value_eur": round(row.metrics.conversions_value, 2),
            })

        logger.info(f"Fetched performance for {len(campaigns)} Atlas campaigns")
        return campaigns

    except Exception as e:
        logger.error(f"Failed to fetch campaign performance: {e}")
        return None


def fetch_keyword_performance(campaign_id: str, days: int = 7) -> list[dict] | None:
    """Fetch keyword-level performance for a specific campaign."""
    client = _get_client()
    if not client:
        return None

    customer_id = settings.google_ads_customer_id
    ga_service = client.get_service("GoogleAdsService")

    end_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    query = f"""
        SELECT
            ad_group_criterion.keyword.text,
            ad_group_criterion.keyword.match_type,
            metrics.impressions,
            metrics.clicks,
            metrics.ctr,
            metrics.average_cpc,
            metrics.cost_micros,
            metrics.conversions
        FROM keyword_view
        WHERE campaign.id = {campaign_id}
        AND segments.date BETWEEN '{start_date}' AND '{end_date}'
        ORDER BY metrics.impressions DESC
        LIMIT 30
    """

    try:
        response = ga_service.search(customer_id=customer_id, query=query)
        keywords = []
        for row in response:
            keywords.append({
                "keyword": row.ad_group_criterion.keyword.text,
                "match_type": row.ad_group_criterion.keyword.match_type.name,
                "impressions": row.metrics.impressions,
                "clicks": row.metrics.clicks,
                "ctr": round(row.metrics.ctr * 100, 2),
                "avg_cpc_eur": round(row.metrics.average_cpc / 1_000_000, 2),
                "cost_eur": round(row.metrics.cost_micros / 1_000_000, 2),
                "conversions": round(row.metrics.conversions, 1),
            })
        return keywords

    except Exception as e:
        logger.error(f"Failed to fetch keyword performance: {e}")
        return None
