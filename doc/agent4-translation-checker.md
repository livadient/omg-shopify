# Agent 4: Translation Checker (Hermes)

## Purpose

Maintains Greek translations for all OMG store content. Checks for untranslated or outdated translatable resources in Shopify and translates English to Greek using Claude.

## Schedule

**Daily:** Every day at 02:00 Cyprus time (Europe/Nicosia)

Unlike other agents that run Mon-Fri, Hermes runs daily (including weekends) since translations may be needed for products created at any time.

## Flow

```
1. Scheduler triggers translation_checker.run_translation_check()
2. Query Shopify GraphQL API for all translatableResources (products, collections, etc.)
3. For each resource, compare translatableContent (EN) with translatedContent (GR)
4. Collect fields that are untranslated or outdated (English changed since last translation)
5. Skip "handle" fields (URL slugs must remain in English)
6. Batch translate collected fields via Claude API (EN → GR)
7. Register translations back to Shopify via translationsRegister GraphQL mutation
8. Send email report with English/Greek side-by-side table
```

**No approval flow** -- translations are registered immediately. The email report is informational only.

## Shopify Translations API

### GraphQL Queries Used

**`translatableResources`** -- Fetches all translatable fields for a resource type:
```graphql
query {
  translatableResources(resourceType: PRODUCT, first: 50) {
    edges {
      node {
        resourceId
        translatableContent {
          key
          value
          digest
          locale
        }
        translations(locale: "el") {
          key
          value
          outdated
        }
      }
    }
  }
}
```

**`translationsRegister`** -- Writes translations:
```graphql
mutation {
  translationsRegister(
    resourceId: "gid://shopify/Product/123"
    translations: [
      { key: "title", value: "Greek title", locale: "el", translatableContentDigest: "abc123" }
    ]
  ) {
    translations { key value }
    userErrors { field message }
  }
}
```

### Field Skipping

`handle` fields are always skipped because:
- URL slugs must remain in English for SEO
- Translating handles would break existing links and bookmarks
- Shopify uses handles for URL routing

## Email Report

Hermes sends a styled HTML email with:
- Blue header with "Hermes here -- translation run complete"
- Summary count of translations made
- Side-by-side table: Field | English | Greek
- If nothing needed translating: short "all up to date" message

## OAuth Scopes Required

`read_translations`, `write_translations`, `read_locales`, `write_locales`

These must be added to the app's OAuth scope list and re-authorized via `/shopify-auth` if not already granted.

## Configuration

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API for EN→GR translation |
| `OMG_SHOPIFY_ADMIN_TOKEN` | Shopify Admin API access (with translation scopes) |

## Modules

- **Agent:** `app/agents/translation_checker.py`
- **Shopify API:** `app/shopify_translations.py`
- **Dependencies:** `app/agents/llm_client.py`, `app/agents/agent_email.py`, `app/config.py`
