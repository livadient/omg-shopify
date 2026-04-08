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
5. Load previous recommendations (for context / avoid repetition)
6. Call Claude API with SEO analysis prompt
7. Claude generates structured recommendations
8. Send email directly to user (no approval needed)
9. Save report to history
```

**No approval flow** — this agent is advisory only. It doesn't take automated actions.

### Exclusion List

Atlas's system prompt includes a permanent exclusion list of topics it must **never** recommend, because they require manual Shopify admin configuration and cannot be implemented programmatically:

- Adding or changing payment methods (JCC cards, PayPal badges, etc.)
- Payment gateway configuration or checkout payment options
- Checkout customizations (address autocomplete, Google Places API, postal code validation, custom checkout scripts)

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

## Phase 2: Real Data Integration

In a future phase, add actual ranking data:

### Google Search Console API
- Requires: Google Cloud project + OAuth2 service account
- Provides: actual search queries, impressions, clicks, average position
- Endpoint: `searchanalytics/query`

### Google Ads API
- Requires: Google Ads developer token + OAuth2
- Provides: keyword planner data, campaign performance
- Can create/manage campaigns programmatically

These are skipped in Phase 1 to avoid setup complexity. The LLM-only approach still provides valuable recommendations based on general SEO knowledge and the store's current state.

## Configuration

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API for analysis + recommendations |
| `AGENT_TIMEZONE` | Timezone for schedule (Europe/Nicosia) |

## Module

**File:** `app/agents/ranking_advisor.py`

**Dependencies:** `app/agents/llm_client.py`, `app/email_service.py`
