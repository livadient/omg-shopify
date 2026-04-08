# Agent 3: Google Ranking Advisor (Atlas)

## Purpose

Provides daily actionable SEO and Google Ads recommendations to improve the OMG store's visibility. Starts with Cyprus, expands to Greece, then all of Europe.

## Schedule

**Daily:** Monday through Friday at 07:00 Cyprus time (Europe/Nicosia)

### Market Focus Rotation

| Day | Market Focus |
|-----|-------------|
| Monday | Cyprus (CY) |
| Tuesday | Greece (GR) |
| Wednesday | Cyprus (CY) |
| Thursday | Greece (GR) |
| Friday | Europe (EU) |

## Flow

```
1. Scheduler triggers ranking_advisor.generate_daily_report()
2. Determine today's market focus (CY/GR/EU based on day of week)
3. Fetch current OMG products via Shopify Admin API
4. Fetch existing blog articles (if any)
5. Fetch Google Search Console data for the market's site (real queries, clicks, CTR, positions)
6. Fetch Google Ads Keyword Planner data (real CPC, search volume, competition)
7. Load previous recommendations (for context / avoid repetition)
8. Call Claude API with SEO analysis prompt + real Google data
9. Claude generates structured recommendations grounded in actual search data
10. Send email directly to user (no approval needed)
11. Save report to history
```

**No approval flow** — this agent is advisory only. It doesn't take automated actions.

### Exclusion List

Atlas's system prompt includes a permanent exclusion list of topics it must **never** recommend, because they require manual Shopify admin configuration and cannot be implemented programmatically:

- Adding or changing payment methods (JCC cards, PayPal badges, etc.)
- Payment gateway configuration or checkout payment options
- Checkout customizations (address autocomplete, Google Places API, postal code validation, custom checkout scripts)
- Theme Liquid file changes (schema markup, hreflang tags, structured data injection)

To add more exclusions, edit the `SYSTEM_PROMPT` in `app/agents/ranking_advisor.py`.

## LLM Prompt Strategy

The system prompt includes:
- Current product catalog (titles, descriptions, handles, prices)
- Published blog articles (titles, tags, publication dates)
- Target market for today (Cyprus, Greece, or Europe)
- Previous 5 recommendations (to avoid repetition)
- Knowledge of the brand (OMG, Cyprus-based, t-shirts, targeting Mediterranean/European market)

### Market-Specific Context

**Cyprus (CY):**
- Population: ~1.2M, small market
- Language: Greek + English
- Focus: local SEO, Google Maps, social media
- Competitors: local print shops

**Greece (GR):**
- Population: ~10.5M
- Language: Greek
- Focus: Greek-language SEO, Google.gr rankings
- Shipping: Geniki Taxydromiki (EUR 5)
- Competitors: larger Greek e-commerce

**Europe (EU):**
- Focus: English-language SEO, broader keywords
- Shipping: Postal (EUR 5-6)
- Competitors: Redbubble, Teespring, Amazon Merch

## Email Report Format

```
Subject: [OMG SEO] Daily Ranking Report — Cyprus Focus (Mon, Apr 7)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 TODAY'S TOP 3 ACTIONS

1. Add alt text to all product images on omg.com.cy
   Impact: Medium | Effort: 15 min
   Why: 4 product images have no alt text, hurting image SEO

2. Create a Google Business Profile for OMG
   Impact: High | Effort: 30 min
   Why: No GBP found — critical for Cyprus local searches

3. Add "Cyprus" to your homepage title tag
   Impact: High | Effort: 5 min
   Current: "OMG - Graphic Tees" → Suggested: "OMG - Graphic Tees Cyprus | Custom T-Shirts"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📈 SEO OPPORTUNITIES

• Title tag optimization for product pages
• Internal linking between blog posts and products
• Schema markup for Product type (price, availability)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✍️ CONTENT IDEAS

• "Where to Buy Custom T-Shirts in Limassol" (high local intent)
• "Greek Slogan T-Shirts: 10 Ideas" (targets Greek market)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💰 GOOGLE ADS SUGGESTIONS

• Keyword: "custom t-shirts cyprus" — est. CPC €0.30, vol: 200/mo
• Keyword: "graphic tees limassol" — est. CPC €0.15, vol: 50/mo
• Suggested daily budget: €5-10 to start
• Campaign type: Search + Shopping

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/agents/ranking/generate` | Manually trigger today's report |
| GET | `/agents/ranking/history` | View past reports (last 30 days) |

## Google Search Console Integration

Atlas fetches real search performance data from Google Search Console for each market's site. Data includes actual search queries, impressions, clicks, CTR, and average position.

**Module:** `app/agents/google_search_console.py`

### Site-to-Market Mapping

| Market | Site | Country Filter |
|--------|------|----------------|
| CY | `sc-domain:omg.com.cy` | Cyprus (`cyp`) |
| GR | `sc-domain:omg.gr` | Greece (`grc`) |
| EU | Both sites combined | No filter |

### Setup

1. Create a **Service Account** in Google Cloud Console
2. Download the JSON key file → place in project root
3. Add the service account email as a user in Google Search Console for each site
4. Set `GOOGLE_SERVICE_ACCOUNT_FILE` and `GOOGLE_SEARCH_CONSOLE_SITE` in `.env`

### Notes

- GSC data has a ~3 day lag — the module automatically adjusts date ranges
- For EU market, data from both sites is merged (duplicate queries combined with weighted average position)
- `ohmangoes.com` is excluded — Google chose `omg.gr` as the canonical for that domain

## Google Ads Keyword Planner Integration

Atlas fetches real keyword ideas with actual CPC ranges, monthly search volumes, and competition levels from Google Ads Keyword Planner.

**Module:** `app/agents/google_keyword_planner.py`

### Setup

1. Create a Google Ads account (or manager account)
2. Apply for **Basic access** in Google Ads → Tools → API Center (required — test access only works with test accounts)
3. Create OAuth2 Desktop credentials in Google Cloud Console
4. Run `scripts/get_google_refresh_token.py` to get a refresh token
5. Set all `GOOGLE_ADS_*` variables in `.env`

### Market-Specific Config

| Market | Geo Target | Language | Seed Keywords |
|--------|-----------|----------|---------------|
| CY | Cyprus (2196) | Greek (1022) | t-shirt cyprus, μπλουζάκια κύπρος, ... |
| GR | Greece (2300) | Greek (1022) | t-shirt ελλάδα, μπλουζάκια, ... |
| EU | Europe (2067) | English (1000) | graphic tees europe, custom t-shirt, ... |

### Notes

- Requires **Basic access** developer token (not test access)
- Keyword data is sorted by search volume and top 20 are included in the prompt
- Real CPC/volume data replaces Claude's estimates in the google_ads section

## Configuration

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API for analysis + recommendations |
| `AGENT_TIMEZONE` | Timezone for schedule (Europe/Nicosia) |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Path to Google Cloud service account JSON key |
| `GOOGLE_SEARCH_CONSOLE_SITE` | Comma-separated Search Console sites (e.g. `sc-domain:omg.com.cy,sc-domain:omg.gr`) |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Google Ads API developer token (requires Basic access) |
| `GOOGLE_ADS_CLIENT_ID` | OAuth2 client ID for Google Ads |
| `GOOGLE_ADS_CLIENT_SECRET` | OAuth2 client secret for Google Ads |
| `GOOGLE_ADS_REFRESH_TOKEN` | OAuth2 refresh token for Google Ads |
| `GOOGLE_ADS_CUSTOMER_ID` | Google Ads account ID (10 digits, no dashes) |

## Modules

| File | Purpose |
|------|---------|
| `app/agents/ranking_advisor.py` | Main agent: orchestrates data fetching, prompt building, email |
| `app/agents/google_search_console.py` | Google Search Console API client |
| `app/agents/google_keyword_planner.py` | Google Ads Keyword Planner API client |
| `app/agents/llm_client.py` | Claude API wrapper |
| `app/agents/agent_email.py` | Email sending |
