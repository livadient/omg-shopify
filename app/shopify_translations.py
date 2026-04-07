"""Shopify GraphQL Translations API client for managing store translations."""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ADMIN_API_VERSION = "2024-01"

# Translatable resource types to check (ordered by priority)
RESOURCE_TYPES = [
    "SHOP",
    "PRODUCT",
    "COLLECTION",
    "PRODUCT_OPTION",
    "PRODUCT_OPTION_VALUE",
    "LINK",
    "MENU",
    "METAFIELD",
    "PAGE",
    "ARTICLE",
    "DELIVERY_METHOD_DEFINITION",
]


def _graphql_url() -> str:
    domain = settings.omg_shopify_domain
    if not domain.endswith(".myshopify.com"):
        domain = "52922c-2.myshopify.com"
    return f"https://{domain}/admin/api/{ADMIN_API_VERSION}/graphql.json"


def _headers() -> dict:
    return {
        "X-Shopify-Access-Token": settings.omg_shopify_admin_token,
        "Content-Type": "application/json",
    }


async def _graphql(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query against the Shopify Admin API."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _graphql_url(),
            headers=_headers(),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("errors"):
        logger.error(f"GraphQL errors: {data['errors']}")
    if data.get("data") is None:
        data["data"] = {}
    return data


async def ensure_locale_enabled(locale: str = "el") -> bool:
    """Enable and publish a locale on the store. Returns True if successful."""
    # Check if already enabled
    data = await _graphql("{ shopLocales { locale published } }")
    locales = data.get("data", {}).get("shopLocales", [])
    existing = {loc["locale"]: loc for loc in locales}

    if locale in existing and existing[locale]["published"]:
        logger.info(f"Locale '{locale}' already enabled and published")
        return True

    # Enable if not yet enabled
    if locale not in existing:
        result = await _graphql(
            """mutation($locale: String!) {
                shopLocaleEnable(locale: $locale) {
                    shopLocale { locale published }
                    userErrors { message field }
                }
            }""",
            {"locale": locale},
        )
        errors = result.get("data", {}).get("shopLocaleEnable", {}).get("userErrors", [])
        if errors:
            logger.error(f"Failed to enable locale '{locale}': {errors}")
            return False
        logger.info(f"Enabled locale '{locale}'")

    # Publish
    result = await _graphql(
        """mutation($locale: String!) {
            shopLocaleUpdate(locale: $locale, shopLocale: { published: true }) {
                shopLocale { locale published }
                userErrors { message field }
            }
        }""",
        {"locale": locale},
    )
    errors = result.get("data", {}).get("shopLocaleUpdate", {}).get("userErrors", [])
    if errors:
        logger.error(f"Failed to publish locale '{locale}': {errors}")
        return False

    logger.info(f"Locale '{locale}' published")
    return True


async def get_translatable_resources(
    resource_type: str,
    locale: str = "el",
    first: int = 50,
    cursor: str | None = None,
) -> dict:
    """Fetch translatable resources of a given type with their current translations."""
    query = """
    query($type: TranslatableResourceType!, $first: Int!, $cursor: String, $locale: String!) {
        translatableResources(resourceType: $type, first: $first, after: $cursor) {
            edges {
                node {
                    resourceId
                    translatableContent {
                        key
                        value
                        digest
                        locale
                    }
                    translations(locale: $locale) {
                        key
                        value
                        outdated
                    }
                }
            }
            pageInfo {
                hasNextPage
                endCursor
            }
        }
    }
    """
    variables = {"type": resource_type, "first": first, "locale": locale}
    if cursor:
        variables["cursor"] = cursor

    return await _graphql(query, variables)


async def get_resource_translation(resource_id: str, locale: str = "el") -> dict:
    """Get translatable content and existing translations for a single resource."""
    query = """
    query($id: ID!, $locale: String!) {
        translatableResource(resourceId: $id) {
            resourceId
            translatableContent {
                key
                value
                digest
                locale
            }
            translations(locale: $locale) {
                key
                value
                outdated
            }
        }
    }
    """
    data = await _graphql(query, {"id": resource_id, "locale": locale})
    return data.get("data", {}).get("translatableResource", {})


async def register_translations(
    resource_id: str,
    translations: list[dict],
) -> dict:
    """Register translations for a resource.

    Each translation dict must have: locale, key, value, translatableContentDigest
    """
    mutation = """
    mutation($resourceId: ID!, $translations: [TranslationInput!]!) {
        translationsRegister(resourceId: $resourceId, translations: $translations) {
            translations { key value }
            userErrors { message field }
        }
    }
    """
    result = await _graphql(mutation, {
        "resourceId": resource_id,
        "translations": translations,
    })

    reg_data = result.get("data", {}).get("translationsRegister", {})
    errors = reg_data.get("userErrors", [])
    if errors:
        logger.warning(f"Translation errors for {resource_id}: {errors}")

    registered = reg_data.get("translations", [])
    if registered:
        logger.info(f"Registered {len(registered)} translations for {resource_id}")

    return reg_data


async def find_untranslated(
    locale: str = "el",
    resource_types: list[str] | None = None,
    max_per_type: int = 100,
) -> list[dict]:
    """Find all resources that need translation (untranslated or outdated).

    Returns list of dicts with: resource_id, resource_type, fields (list of
    {key, value, digest} that need translating).
    """
    types = resource_types or RESOURCE_TYPES
    needs_translation = []

    for rtype in types:
        cursor = None
        fetched = 0

        while fetched < max_per_type:
            batch_size = min(50, max_per_type - fetched)
            data = await get_translatable_resources(rtype, locale, batch_size, cursor)

            resources = data.get("data", {}).get("translatableResources", {})
            edges = resources.get("edges", [])
            if not edges:
                break

            for edge in edges:
                node = edge["node"]
                content = node.get("translatableContent", [])
                existing = {t["key"]: t for t in node.get("translations", [])}

                missing_fields = []
                # Keys that should never be translated (URL slugs, etc.)
                SKIP_KEYS = {"handle"}

                for field in content:
                    # Skip empty source values
                    if not field.get("value"):
                        continue
                    # Skip non-primary locale content
                    if field.get("locale") and field["locale"] != "en":
                        continue
                    # Skip URL handles — they must stay as-is
                    if field.get("key") in SKIP_KEYS:
                        continue

                    trans = existing.get(field["key"])
                    if not trans or not trans.get("value") or trans.get("outdated"):
                        missing_fields.append({
                            "key": field["key"],
                            "value": field["value"],
                            "digest": field["digest"],
                        })

                if missing_fields:
                    needs_translation.append({
                        "resource_id": node["resourceId"],
                        "resource_type": rtype,
                        "fields": missing_fields,
                    })

            page_info = resources.get("pageInfo", {})
            fetched += len(edges)
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info["endCursor"]

        if needs_translation:
            count = sum(1 for r in needs_translation if r["resource_type"] == rtype)
            logger.info(f"Found {count} {rtype} resources needing translation")

    return needs_translation
