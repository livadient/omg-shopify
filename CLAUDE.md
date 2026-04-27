# OMG Shopify → TShirtJunkies Order Service

## Project Overview

Python FastAPI service that receives Shopify webhook events from **omg.com.cy** and automatically creates corresponding orders on **tshirtjunkies.co** (a Shopify store based in Cyprus). When an order comes in, the service uses **Playwright browser automation** to upload the design, select color (White) and matching size, and add the customized item to cart on TShirtJunkies via their Qstomizer app. An **email notification** is then sent with order details and shareable cart/checkout links.

## Architecture

```
omg.com.cy (Shopify)  →  webhook: orders/create
        ↓
   FastAPI Service (this project)
   ├── Receive webhook (respond immediately)
   ├── Map OMG variant IDs → TShirtJunkies variant/product IDs
   ├── [Background] Playwright: upload design to Qstomizer, select White + matching size, add to cart
   ├── [Background] Build shareable cart permalink with pre-filled checkout params
   └── [Background] Email notification with order details, shipping info + cart links
```

## Tech Stack

- **Python 3.13** (venv in `.venv/`)
- **FastAPI** + **Uvicorn**
- **httpx** for async HTTP requests
- **Pydantic** for data models
- **Playwright** for browser automation (Qstomizer customization)
- **pyngrok** for ngrok tunnel management
- **aiosmtplib** for async email notifications
- **python-dotenv** for `.env` configuration

## Running

```bash
# Install dependencies
.venv/Scripts/pip install -r requirements.txt
playwright install chromium

# Configure (copy .env.example to .env and fill in values)
cp .env.example .env

# Run (must use main.py directly, not uvicorn)
.venv/Scripts/python app/main.py
```

**Important (Windows):** Playwright runs in a separate thread with its own `ProactorEventLoop` to work around uvicorn's `SelectorEventLoop` which doesn't support subprocesses on Windows. The `__main__` block also restricts the reload watcher to `app/` only (excludes `static/`, `*.png`, `*.json`) to prevent restarts when Playwright saves screenshots.

## Configuration

All settings are loaded from environment variables (`.env` file supported via python-dotenv):

| Variable | Default | Purpose |
|----------|---------|---------|
| `SHOPIFY_WEBHOOK_SECRET` | (empty) | Webhook verification secret |
| `TSHIRTJUNKIES_BASE_URL` | `https://tshirtjunkies.co` | Target store URL |
| `SMTP_HOST` | (empty) | SMTP server (e.g. `smtp.gmail.com`) |
| `SMTP_PORT` | `587` | SMTP port (587 for STARTTLS) |
| `SMTP_USERNAME` | (empty) | SMTP login username |
| `SMTP_PASSWORD` | (empty) | SMTP login password (app password for Gmail) |
| `EMAIL_SENDER` | (empty) | From address for notifications |
| `EMAIL_RECIPIENTS` | (empty) | Comma-separated list of notification recipients |
| `OMG_SHOPIFY_DOMAIN` | `52922c-2.myshopify.com` | OMG store myshopify domain |
| `OMG_SHOPIFY_CLIENT_ID` | (empty) | Shopify app client ID (for OAuth) |
| `OMG_SHOPIFY_CLIENT_SECRET` | (empty) | Shopify app client secret (for OAuth) |
| `OMG_SHOPIFY_ADMIN_TOKEN` | (empty) | Admin API access token (obtained via OAuth) |
| `NGROK_DOMAIN` | (empty) | Fixed ngrok domain (e.g. `myapp.ngrok-free.dev`) — avoids random URLs |
| `PORT` | `8080` | Server port (must match Shopify OAuth redirect URI whitelist) |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | (empty) | Path to Google Cloud service account JSON key |
| `GOOGLE_SEARCH_CONSOLE_SITE` | (empty) | Comma-separated GSC sites (e.g. `sc-domain:omg.com.cy,sc-domain:omg.gr`) |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | (empty) | Google Ads API developer token (requires Basic access) |
| `GOOGLE_ADS_CLIENT_ID` | (empty) | OAuth2 client ID for Google Ads |
| `GOOGLE_ADS_CLIENT_SECRET` | (empty) | OAuth2 client secret for Google Ads |
| `GOOGLE_ADS_REFRESH_TOKEN` | (empty) | OAuth2 refresh token for Google Ads |
| `GOOGLE_ADS_CUSTOMER_ID` | (empty) | Google Ads account ID (10 digits, no dashes) |

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/test-webhook` | Test webhook form — send fake orders with country selection (CY/GR/FR) |
| `POST` | `/map-products?source_url=...&target_url=...` | Create mapping between two product URLs |
| `GET` | `/mappings` | View all saved product mappings |
| `POST` | `/webhook/order-created` | Shopify webhook handler — maps items, runs Playwright in background, sends email |
| `GET` | `/manual-order` | Manual order form — paste OMG order email to trigger automation |
| `POST` | `/manual-order` | Submit manual order for processing |
| `GET` | `/fulfill-order` | Fulfill order form — paste TShirtJunkies shipping email or enter tracking manually |
| `POST` | `/fulfill-order` | Fulfill an OMG order with tracking info |
| `POST` | `/fulfill-order/parse` | Parse TShirtJunkies fulfillment email for order number + tracking |
| `GET` | `/shopify-auth` | Start Shopify OAuth flow to authorize the app |
| `GET` | `/shopify-auth/callback` | OAuth callback — exchanges code for access token |
| `POST` | `/fix-shipping-profile` | Add products to Cyprus shipping profile (JSON body: `product_ids` list, or empty for all) |

## Webhook Auto-Registration

On startup, if an ngrok tunnel is active and a Shopify admin token is available, the service automatically registers (or updates) the `orders/create` webhook URL in Shopify. This ensures the webhook always points to the current ngrok URL, even if it changes between restarts.

If the token lacks `read_orders` scope, registration will fail gracefully with a message to re-authorize at `/shopify-auth` or set the webhook URL manually in Shopify admin (Settings > Notifications > Webhooks).

## Webhook Flow

1. Shopify sends `orders/create` webhook with full order JSON
2. Service maps each line item's `variant_id` to a TShirtJunkies variant (matched by size)
3. Responds immediately with mapping results
4. **Background task** runs for each mapped item:
   - Determines product type (male/female) from source handle
   - Extracts size from `variant_title`
   - Runs Playwright automation in a separate thread (ProactorEventLoop)
   - Qstomizer: selects White color, uploads design, selects size, adds to cart
   - Fetches cart contents (`/cart.js`) to get Qstomizer properties
   - Builds a shareable cart permalink with pre-filled checkout params
5. **Email notification** sent with order details, shipping info block (for copy-paste), and cart/checkout links

## Cart Permalink (Shareable Links)

Shopify checkout sessions don't persist across browsers. Instead of returning `/checkouts/...` URLs, the service builds **cart permalinks**:

```
https://tshirtjunkies.co/cart/VARIANT_ID:QTY?checkout[email]=...&checkout[shipping_address][first_name]=...&attributes[_customorderid]=...
```

These links:
- Work in any browser/device (no session dependency)
- Pre-fill the checkout form with customer shipping details
- Include Qstomizer properties (`_customorderid`, `_customorderkey`, `_customimagefront`, etc.) as cart attributes

## Product Placement (Front / Back) Variants

All t-shirts have three variant options: **Gender** (Male/Female), **Placement** (Front/Back), and **Size** — 24 variants per product. The Placement option lets customers order the same design printed on the front OR on the back of the tee at the same price. Selecting a variant on the product page swaps the gallery to the matching mockup (gender + placement), the same way the gender swap works.

Implementation details:
- **Schema**: `VARIANTS` in `shopify_product_creator.py` is a list comprehension over `(gender, placement, size)`. Shopify options: `[Gender, Placement, Size]`.
- **Qstomizer**: our automation clicks the stage thumbnail (`#stagemini0` = front, `#stagemini1` = back) before uploading so the design lands on the correct canvas. `customize_and_add_to_cart(placement=...)` drives this.
- **Mango approval**: `execute_approval` generates 6 marketing scenes (4 female + 2 male model back views) via gpt-image-1 from the design PNG, uploads them first with gender-linked `variant_ids` (female closeup is primary product image), then uploads the 4 Qstomizer mockups (male/female × front/back) also gender/placement-linked. The female closeup becomes the product-card primary image; picking a variant on the product page swaps the gallery to the matching gender+placement shot.
- **Webhook parser**: `app/main.py` handles both `"Male / Front / L"` (new) and `"Male / L"` (legacy) variant titles for backward compat.
- **Migration**: `scripts/add_back_variants.py` was run once against all 18 standard tees on 2026-04-12 to add the Placement option and back mockups. Legacy color-based Astous male/female-limited tees were excluded (they use a different schema).

## Shipping Method Mapping

OMG shipping methods are mapped to TShirtJunkies checkout options by country:

| Country | OMG Method | OMG Price | TJ Match | TJ Price |
|---------|-----------|-----------|----------|----------|
| **CY** | Home Delivery | EUR 4.50 | Home Delivery | EUR 3.00 |
| **GR** | Geniki Taxydromiki | EUR 5.00 | Γενικής Ταχυδρομικής | EUR 5.00 |
| **GR** | Home Delivery | EUR 10.00 | Παράδοσης κατ' οίκον | varies |
| **FR** | Europe postal | EUR 6.00 | Postal Shipping | EUR 5.00 |

Mapping is defined in `SHIPPING_METHOD_MAP` in `qstomizer_automation.py`. Values can be a single string (one option for the country) or a dict keyed by OMG method title (multiple options, chosen by what the customer picked on OMG). The customer's chosen OMG method is extracted from `shipping_lines[0].title` in the webhook payload and threaded through as `shipping["shipping_method"]`.

## Product Mappings

Mappings are stored in `product_mappings.json` at the project root (not tracked in git — server-authoritative). Variants are matched by size option.

### Astous na Laloun - Limited Edition Tee (unified product)

The old separate male/female EU edition products have been replaced by a single unified product with Gender+Size variants:

- **OMG:** `astous-na-laloun-cyprus-unisex-tee` (EUR 30-39.50)
- **TShirtJunkies (male variants):** `classic-tee-up-to-5xl` (EUR 20-22) -- sizes S-5XL
- **TShirtJunkies (female variants):** `women-t-shirt` (EUR 23) -- sizes S-XL

Note: The old `astous-na-laloun-graphic-tee-male-eu-edition` and `astous-na-laloun-graphic-tee-female-eu-edition` mappings have been removed.

## Qstomizer (Product Customization)

TShirtJunkies uses **Qstomizer** for custom design uploads. The service automates this via Playwright:

| Product | Qstomizer Product ID |
|---------|---------------------|
| Classic Tee (Male, up to 5XL) | `9864408301915` |
| Women's T-Shirt | `8676301799771` |

### Playwright Automation Steps

1. Open Qstomizer page (1920x1080 viewport required for canvas rendering)
2. Hide overlapping elements (text window, sticky header)
3. Select color via native MouseEvent dispatch on `.colorVarWrap[data-colordes]` + `Ka()` call
4. Upload design image to `#btnUploadImage` file input
5. Wait for upload + processing (`#msgUploading` then `#msgProcessing`)
6. Click `.imagesubcontainer` thumbnail via jQuery to place on canvas
7. Select size via `#variantValues1` dropdown
8. Click `#addtocart` (ORDER NOW) via jQuery
9. Set size/qty in quantity window (`.infoQty` inputs matched to `.Rtable-cell` labels)
10. Click ADD TO CART in quantity window
11. Wait for "Saving Data..." then redirect to `/cart`
12. Fetch `/cart.js` to get Qstomizer properties (`_customorderid`, `_customorderkey`, etc.)
13. Build shareable cart permalink with checkout pre-fill params

Available colors: Black, Navy Blue, Red, Royal Blue, Sport Grey, White (default: White)

### Per-Product Tee Color

`ProductMapping` has a `color` field (default `"White"`) that's persisted in `product_mappings.json` and used by the webhook order flow to select the matching Qstomizer fabric at print time. Mango's design schema includes a `tee_color` enum (one of the 6 available colors) — the LLM picks based on legibility rules (light artwork → Black, dark artwork → White, slogan style → whatever makes it pop). The value flows through `concept.tee_color` → `execute_approval` → `create_mappings_for_product(color=...)` → persisted on the mapping. Unknown values silently coerce to `White` with a warning via `_normalize_tee_color`. Scripts like `mail_tj_mockups.py` declare color per design in the `DESIGNS` tuple.

### Upper-Back Placement (Konva reposition)

Qstomizer is **Konva.js-based** (not fabric.js). After `.imagesubcontainer` click, the uploaded design auto-centers in the print area — which lands the print **mid-back**. Our marketing mockups show it at the upper back, so `customize_and_add_to_cart` / `fetch_mockup_from_qstomizer` / `_precache_mockups` default `vertical_offset=-0.25` (fraction of print-area height, negative = up toward collar). The reposition:
1. Scans `Konva.stages` for the active stage whose layer has `Group` nodes named `grupoimage*` (3 copies: preview + actual + ghost).
2. Finds the print area (dashed `Rect` on that layer) — its height varies 236–300 px per tee view, so always measure with `rect.height()`.
3. Uses `group.getClientRect({relativeTo: stage})` for **actual rendered bounds** (attr `width`/`height` misleads on tall multi-line designs).
4. **Clamps** the upward delta so the design's rendered top stays inside the print area with a 4 px safety pad — tall 4-line designs like "NORMAL PEOPLE SCARE ME" (design_h ≈ 166) would otherwise clip into the collar.
5. Fires `dragend` on each group so Qstomizer's internal save hook captures the new position (TJ prints from the stored position, not the rendered preview — without dragend, the mockup looks right but the actual print reverts to centered).

### Color Selection Details

Qstomizer binds color change via jQuery delegation on `.colorVariationCont`. The automation:
1. Toggles `colorVarWrapActive` class on the correct swatch
2. Calls `Ka({updateVisualPrice: false})` to update Qstomizer's internal state
3. The color is stored in Qstomizer's order data (not as a Shopify variant — TJ products only have Size as a variant option)

Note: The canvas/mockup always shows the default color (black). The actual color for printing is stored in Qstomizer's backend via `_customorderid`.

### Manual Usage

```bash
.venv/Scripts/python -m app.qstomizer_automation male L White
```

## Playwright on Windows

Playwright requires subprocess support which Windows' `SelectorEventLoop` (used by uvicorn) doesn't provide. The solution:

- `customize_and_add_to_cart()` runs Playwright in a **separate thread** with its own `ProactorEventLoop` via `ThreadPoolExecutor`
- This is handled transparently — callers use `await customize_and_add_to_cart(...)` as normal
- Max 2 concurrent Playwright instances (`_playwright_executor` pool)

## Test Webhook

The `/test-webhook` page lets you test the full automation flow without placing real orders:

- Select product type (male/female), size, quantity
- Select country (Cyprus, Greece, France) with matching test addresses
- Sends a fake order to `/webhook/order-created` using real variant IDs from mappings
- Triggers the full Playwright + email flow

## TShirtJunkies API Details

tshirtjunkies.co is a Shopify store. The following public endpoints are available without authentication:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/products.json?limit=250&page=N` | GET | Browse full catalog |
| `/products/{handle}.json` | GET | Single product details |
| `/collections.json` | GET | List collections |
| `/cart/add.js` | POST | Add item to cart (`{"id": variant_id, "quantity": N}`) |
| `/cart.js` | GET | Read current cart |
| `/cart/update.js` | POST | Update quantities |
| `/cart/clear.js` | POST | Empty cart |

- **Storefront GraphQL API is blocked** (403, no public token)
- Cart operations are session/cookie-based (use `requests.Session()` or `httpx.AsyncClient()`)
- Cart permalink format: `https://tshirtjunkies.co/cart/variant_id:qty,variant_id:qty`
- Checkout pre-fill params: `?checkout[email]=...&checkout[shipping_address][first_name]=...`
- Payment still requires human confirmation (no programmatic checkout without Storefront API token from store owner)

## OMG Store API Notes

- **omg.com.cy blocks individual product `.json` endpoints** (404)
- Products must be fetched via `/products.json?limit=250&page=N` catalog endpoint
- The `shopify_client.py` handles this automatically with a fallback

## Key Files

```
app/
  main.py                — FastAPI app, webhook handler, test webhook, background task orchestration
  config.py              — Settings loaded from .env (SMTP, webhook secret, ngrok, port, etc.)
  models.py              — Pydantic models (ProductMapping, VariantMapping)
  mapper.py              — Product mapping logic, saves to product_mappings.json
  shopify_client.py      — Fetches products from any Shopify store
  cart_client.py         — TShirtJunkies cart operations
  qstomizer_automation.py — Playwright browser automation, cart permalink builder, shipping method mapping
  email_service.py       — Async email notifications via aiosmtplib (includes shipping details block)
  email_parser.py        — Parse OMG order confirmation emails
  omg_fulfillment.py     — OMG Shopify Admin API: order lookup, fulfillment creation, OAuth token exchange
  seo_management.py      — SEO optimization: fix handles, homepage meta, create collections
  shopify_blog.py        — Shopify Blog Article Admin API
  shopify_product_creator.py — Create products with Gender+Size variants, upload images, fetch mockups
  shopify_translations.py — Shopify GraphQL Translations API: read/write translations for products
  agents/
    llm_client.py        — Anthropic Claude API wrapper (generate, generate_with_search, generate_json)
    image_client.py      — OpenAI DALL-E 3 image generation + rembg background removal + text validation
    scheduler.py         — APScheduler cron setup for all agent jobs
    approval.py          — Token-based proposal storage and approval workflow
    agent_email.py       — Shared email utilities for agents (inline images, error notifications)
    blog_writer.py       — Agent "Olive": SEO blog post generation
    design_creator.py    — Agent "Mango": Trend research, design generation, mockup pre-caching
    ranking_advisor.py   — Agent "Atlas": Daily SEO and Google Ads recommendations
    google_search_console.py — Google Search Console API: real search queries, clicks, CTR, positions
    google_keyword_planner.py — Google Ads Keyword Planner API: real CPC, search volume, competition
    translation_checker.py — Agent "Hermes": Daily translation checker (English→Greek via Claude)
product_mappings.json    — Saved variant ID mappings
data/
  proposals.json         — Agent proposal storage (persisted via Docker volume)
  ranking_history.json   — Ranking advisor report history
scripts/
  dont_tempt_me_gptimage.py  — Single-tee marketing photo script (DTM style), gpt-image-1 + Pillow transparent design
  tee_scenes_from_refs.py    — Generalised marketing photo script, takes any reference tee and produces 4 scenes + transparents
  redesign_omg_tees.py       — gpt-image-1 redesign of existing OMG slogan tees; supports per-scene CLI filter to re-roll a single scene
  dont_tempt_me_compose.py   — Compose pipeline for white tees: blank tees + Claude-bbox + Pillow paste (guaranteed-correct text)
  told_her_compose.py        — Compose pipeline for black tees with white serif caps (inverted bbox detection, no italic shear)
static/
  front_design.png       — Design image uploaded to Qstomizer
  proposals/             — Generated design images + cached mockups, plus per-slug marketing photo folders (dont_tempt_me_v3, i_dont_get_drunk, etc.)
  checkout_result.png    — Latest Playwright screenshot (auto-generated)
tests/                   — Unit tests (pytest) — 130 tests, all mocked, no external services
.env.example             — Template for environment variables
```

## Agent Schedules

All times are Cyprus time (Europe/Nicosia):

| Agent (Name) | Schedule | Time | Purpose |
|--------------|----------|------|---------|
| Translation Checker (Hermes) | Daily | 02:00 | Check for untranslated/outdated content, translate EN→GR |
| Design QA (Argus) | Daily | 03:00 | Verify all mapped designs render correctly on TShirtJunkies |
| Design Creator (Mango) | Daily | 04:00 | Research trends, generate 5 designs, pre-cache mockups |
| ~~SEO Optimizer (Sphinx)~~ | ~~Mon-Fri~~ | ~~04:30~~ | **DISABLED** — manually executing Atlas' recommendations instead. Can still run manually: `.venv/Scripts/python -m app.seo_management all` |
| Blog Writer (Olive) | Tue, Fri | 05:00 | Generate SEO blog post for review |
| Ranking Advisor (Atlas) | Mon-Fri | 07:00 | Daily SEO/Google Ads recommendations |

## Agent Names and Personalities

Each agent has a name and personality reflected in email communications:

| Agent | Name | Email Color | Greeting Style |
|-------|------|-------------|----------------|
| Design Creator | Mango | Purple | "Hey boss, Mango here!" |
| Blog Writer | Olive | Green | "Olive here -- new post ready!" |
| Ranking Advisor | Atlas | Blue | "Atlas reporting for duty" |
| Design QA | Argus | Orange | "Argus here — nightly QA report" |
| Translation Checker | Hermes | Blue | "Hermes here -- translation run complete" |
| SEO Optimizer | Sphinx | N/A | **DISABLED** — does not send emails; run manually when needed |

## Documentation Index

Detailed documentation for every subsystem is in `doc/`:

| Document | Topic |
|----------|-------|
| [architecture.md](doc/architecture.md) | System architecture, agent pattern, file structure |
| [setup-guide.md](doc/setup-guide.md) | Installation, configuration, Docker, troubleshooting |
| [configuration.md](doc/configuration.md) | All environment variables and Settings class |
| [api-endpoints.md](doc/api-endpoints.md) | REST API reference for all endpoints |
| [webhook-order-flow.md](doc/webhook-order-flow.md) | Shopify webhook → Playwright → email flow |
| [qstomizer-automation.md](doc/qstomizer-automation.md) | Playwright browser automation (13-step process) |
| [product-mappings.md](doc/product-mappings.md) | OMG ↔ TShirtJunkies product mapping system |
| [shopify-integration.md](doc/shopify-integration.md) | All Shopify API integrations (Admin + Storefront) |
| [email-system.md](doc/email-system.md) | Order notifications, agent emails, email parsing |
| [approval-workflow.md](doc/approval-workflow.md) | Token-based proposal approval system |
| [llm-image-clients.md](doc/llm-image-clients.md) | Claude API and DALL-E 3 client wrappers |
| [agent1-blog-writer.md](doc/agent1-blog-writer.md) | SEO Blog Writer agent specification |
| [agent2-design-creator.md](doc/agent2-design-creator.md) | Design Creator agent (5 types, mockup pre-caching) |
| [agent3-ranking-advisor.md](doc/agent3-ranking-advisor.md) | Ranking Advisor agent (market rotation, GSC + Keyword Planner) |
| [agent4-translation-checker.md](doc/agent4-translation-checker.md) | Translation Checker agent (EN→GR via Claude) |
| [marketing-photo-generation.md](doc/marketing-photo-generation.md) | Standalone gpt-image-1 scripts for slogan tee marketing photos + Qstomizer transparent PNG |
| [design-replication-workflow.md](doc/design-replication-workflow.md) | 5-step end-to-end: confirm inputs → pick pipeline → render scenes → wire into mail_tj_mockups → send email |

## Testing

Run unit tests: `pytest tests/ -v`

130 tests covering all modules. All external services (HTTP, SMTP, Claude, DALL-E, Playwright) are mocked. File-based tests use `tmp_path` for isolation.

## OMG Shopify Admin API (OAuth)

The service connects to the OMG Shopify store via a custom app using OAuth. The app has the following scopes:

`read_orders`, `write_fulfillments`, `read_products`, `write_products`, `read_customers`, `write_customers`, `read_inventory`, `write_inventory`, `read_shipping`, `write_shipping`, `read_order_edits`, `write_order_edits`, `read_translations`, `write_translations`, `read_locales`, `write_locales`, `read_markets`, `write_markets`, `read_online_store_navigation`, `write_online_store_navigation`, `read_themes`, `write_themes`

### Setup

1. Set `OMG_SHOPIFY_CLIENT_ID` and `OMG_SHOPIFY_CLIENT_SECRET` in `.env`
2. Start the server and go to `/shopify-auth` to authorize
3. Save the returned token as `OMG_SHOPIFY_ADMIN_TOKEN` in `.env`
4. The token is permanent — no refresh needed

### Admin API Usage

- Base URL: `https://52922c-2.myshopify.com/admin/api/2024-01/`
- Auth header: `X-Shopify-Access-Token: {token}`
- Used by `app/omg_fulfillment.py` for order lookup and fulfillment creation

## OAuth Redirect URI

The Shopify OAuth redirect URI is hardcoded to `http://localhost:8080/shopify-auth/callback`. This must match the **Allowed redirection URL(s)** configured in the Shopify Partners dashboard for the app. If you change the port, update the whitelist there too.

## Email Notifications

Order notification emails include:
- Order number, customer name, total
- **Shipping details block** with name, address, city, zip, country, phone, email (for copy-paste if needed)
- Product table with size, qty, and **cart permalink** (shareable checkout link)
- If Playwright fails: red banner with link to Manual Order page (`http://40.81.137.193:8080/manual-order`)
- Qstomizer fallback links for manual design upload

## Deployment — Azure Ubuntu VM (Docker + Auto-Deploy)

This project needs to be deployed to the same Azure VM as bot-trading (`40.81.137.240`, user `vangelisl`).

### Steps to deploy

1. **Create `Dockerfile`:**
   - Base: `python:3.13-slim`
   - Install system deps for Playwright: `libnss3`, `libatk1.0-0`, `libatk-bridge2.0-0`, `libcups2`, `libxdamage1`, `libpango-1.0-0`, `libcairo2`, `libgbm1`, `libasound2`, `libxrandr2`, `libxcomposite1`, `libxshmfence1`, `fonts-liberation`
   - `pip install -r requirements.txt && playwright install chromium --with-deps`
   - Non-root user
   - Entrypoint: `python app/main.py`
   - Expose port `8080`

2. **Create `docker-compose.yml`:**
   - Service: `omg-shopify`
   - `env_file: .env` for secrets (Shopify tokens, SMTP, etc.)
   - Mount `product_mappings.json` and `static/front_design.png`
   - Port: `8080:8080`
   - `restart: unless-stopped`

3. **Create `.env.example`** with all required vars (see Configuration section above)

4. **Create `.dockerignore`** — exclude `.git/`, `__pycache__/`, `.env`, `.venv/`, `*.png` (except `front_design.png`)

5. **Create `.github/workflows/deploy.yml`:**
   ```yaml
   name: Deploy to Azure VM
   on:
     push:
       branches: [main]  # or master, check which branch
   jobs:
     deploy:
       runs-on: ubuntu-latest
       steps:
         - name: Deploy via SSH
           uses: appleboy/ssh-action@v1
           with:
             host: ${{ secrets.SERVER_HOST }}
             username: ${{ secrets.SERVER_USER }}
             key: ${{ secrets.SERVER_SSH_KEY }}
             script: |
               cd ~/omg-shopify-python
               git pull origin main
               docker compose up -d --build
   ```

6. **GitHub repo secrets** (Settings > Secrets > Actions):
   - `SERVER_HOST` = `40.81.137.240`
   - `SERVER_USER` = `vangelisl`
   - `SERVER_SSH_KEY` = same deploy key as bot-trading (already on server)

7. **On the server:**
   ```bash
   # Clone (deploy key already set up from bot-trading)
   git clone git@github.com:livadient/omg-shopify-python.git
   cd omg-shopify-python
   cp .env.example .env
   nano .env  # fill in Shopify tokens, SMTP, etc.
   # Copy design file if not in repo
   docker compose up -d --build
   ```

8. **Azure networking:** Open port `8080` inbound (same as port 8765 was opened for bot-trading dashboard)

9. **Ngrok:** Not needed in production — Shopify webhook URL should point directly to `http://40.81.137.240:8080/webhook/order-created` (or use a domain/reverse proxy with HTTPS)

### Notes
- Playwright in Docker needs `--no-sandbox` flag or the chromium deps listed above
- The Windows `ProactorEventLoop` workaround in `main.py` is not needed on Linux — may need a small conditional
- `product_mappings.json` is **server-authoritative** (not tracked in git). The running service creates mappings when products are created/approved. Seed manually on first deploy from `.env` or a backup. Never overwrite via `git pull`.
- Email notification links currently reference `http://40.81.137.193:8080` — update to the correct server IP or domain

## Shipping Profile (Critical for New Products)

New products **must** be added to the Cyprus shipping profile, otherwise they show as **"sold out"** even with inventory set. This is because Shopify requires products to belong to a shipping profile that has rates for the customer's country.

- **Profile ID:** `120742379801` ([admin link](https://admin.shopify.com/store/52922c-2/settings/shipping/profiles/120742379801))
- **Constant:** `OMG_SHIPPING_PROFILE_ID` in `shopify_product_creator.py`
- **Automatic:** `create_product()` calls `_add_to_shipping_profile()` via the GraphQL `deliveryProfileUpdate` mutation with `variantsToAssociate`
- **Manual fix:** `POST /fix-shipping-profile` with optional `product_ids` JSON body, or `POST /fix-sold-out/{product_id}` which also adds to the profile
- **GraphQL field:** `variantsToAssociate` takes a flat list of variant GIDs (`gid://shopify/ProductVariant/{id}`), NOT `productVariantsToAssociate` (that field does not exist on `DeliveryProfileInput`)

## T-Shirts Collection

All t-shirt products are added to the "OMG T-Shirts" collection automatically on creation.

- **Collection ID:** `451595010329` — https://omg.com.cy/collections/t-shirts
- **Constant:** `OMG_TSHIRTS_COLLECTION_ID` in `shopify_product_creator.py`
- **Automatic:** `create_product()` calls `_add_to_collection()` via `/collects.json`

## Category Collections & Auto-Categorization

New products are automatically added to category collections based on their tags and variants. Defined in `CATEGORY_COLLECTIONS` and `COLLECTION_TAG_RULES` in `shopify_product_creator.py`.

| Collection | Handle | ID | Auto-Rule |
|------------|--------|----|-----------|
| Ανδρικά \| Men | `mens` | `683599987068` | All products with Male variants |
| Γυναικεία \| Women | `womens` | `683599954300` | All products with Female variants |
| Geeky | `programmers` | `683599921532` | Tags: geeky, programmer, coding, nerd, tech, gaming, 404, debug |
| Slogan Tees | `slogan-tees` | `683602674044` | Tags: slogan, typography, quote, text tee, energy, overthinker |
| Κυπριακά \| Cyprus Tees | `cyprus-tees` | `683597857148` | Tags: cyprus, astous, cypriot, κύπρος |
| Τοπικά Σχέδια \| Local Designs | `local-designs` | `683600019836` | Tags: cyprus, local, astous, mediterranean |

Since all our tees have both Male+Female variants, every product is auto-added to both Men and Women. Tag-based collections match against product tags, handle, and title.

## Main Navigation Menu

The main menu (`gid://shopify/Menu/230918783257`) is managed via the Shopify GraphQL Admin API (requires `read_online_store_navigation` + `write_online_store_navigation` scopes).

```
Home
OMG Clothing  ▾  (/collections/t-shirts)
  ├── All T-Shirts
  ├── Κυπριακά | Cyprus Tees
  ├── Geeky
  ├── Slogan Tees
  ├── Γυναικεία | Women
  ├── Ανδρικά | Men
  └── Τοπικά Σχέδια | Local Designs
OMG Beauty  (/collections/omg-beauty)
Blog  (/blogs/news)
Contact  (/pages/contact)
```

## T-Shirt Product Metafields

All t-shirt products get standard metafields set automatically on creation. These match the metafield structure used by all other OMG products (defined in `TSHIRT_METAFIELDS` in `shopify_product_creator.py`):

| Namespace.Key | Type | Content |
|---------------|------|---------|
| `custom.units_sold` | single_line_text_field | `100+` |
| `custom.period_shipping` | multi_line_text_field | Delivery 1-2 days + 30-day guarantee |
| `custom.periods_pec` | multi_line_text_field | Material, weight, fit, print method, sizes |
| `custom.period_features` | multi_line_text_field | Premium cotton, DTG print, pre-shrunk |
| `custom.instructions` | multi_line_text_field | Wash/care instructions |

Note: The key names (`period_shipping`, `periods_pec`) match the existing store convention (legacy naming from beauty products).

## Shopify Translations (EN→GR)

The Translation Checker agent ("Hermes") uses the Shopify GraphQL Translations API to maintain Greek translations for all store content.

### How It Works

1. `shopify_translations.py` queries `translatableResources` via GraphQL to find all translatable fields for products, collections, etc.
2. Compares each field's `translatableContent` (English) with its existing `translatedContent` (Greek)
3. Fields that are untranslated or outdated (English changed since last translation) are collected
4. Claude translates the English text to Greek
5. `translationsRegister` GraphQL mutation writes the translations back to Shopify
6. `handle` fields are always skipped (URL slugs must remain in English)

### Email Report

Hermes sends an email with an English/Greek side-by-side HTML table showing all translations made in the run. If nothing needed translating, a short "all up to date" message is sent instead.

### API Scopes Required

`read_translations`, `write_translations`, `read_locales`, `write_locales` -- added to the OAuth scope list. Re-authorization via `/shopify-auth` is required if these scopes were not previously granted.

## DALL-E Text Validation

The image client (`app/agents/image_client.py`) includes text validation for generated designs:

- `generate_text_design` now uses transparent RGBA background instead of white
- `generate_text_design` follows the Kyriaki-approved "Don't Tempt Me" template: **modest print scale** (~55% of canvas width, not 80%+) and a **two-line hierarchy** when the slogan contains a `\n` — bold condensed top (Impact / Liberation-Sans-Bold / Times-Bold) + regular-weight sub at 45% size (Arial / Liberation-Sans-Regular). `TEXT_DESIGN_HIERARCHY_FONTS` holds the (top, sub) font pairs. Mango's schema tells the LLM to embed `\n` at the natural punchline break (e.g. `"DON'T TEMPT ME\nI'LL SAY YES"`). Single-line slogans render at the same modest scale without hierarchy.
- `validate_design_text(image_path, expected_text)` -- Claude vision reads text in generated images and checks correctness
- `generate_design_with_text_check(concept, expected_text, ...)` -- generates via DALL-E, validates text with Claude, regenerates with correction prompt if wrong (up to 2 retries)
- Pillow text designs (slogan type) skip `rembg` since they are already transparent PNG

Note: `image_client.generate_design` was switched from `dall-e-3` to `gpt-image-1` on 2026-04-19. GPT-Image-1 is GPT-4o's native image model (the one ChatGPT uses since March 2025), returns base64 directly via `resp.data[0].b64_json` (no URL download), and produces much cleaner text rendering on fabric. Quality levels are `low`/`medium`/`high`; sizes `1024x1024` / `1536x1024` / `1024x1536`.

## Marketing Photo Generation (CLI Scripts)

Standalone scripts for hand-curated slogan tee launches. Separate from the Mango agent pipeline — these are one-shot CLI utilities that produce e-commerce photos plus a transparent PNG for Qstomizer/TJ upload.

- **`scripts/dont_tempt_me_gptimage.py`** — hard-coded to the "DON'T TEMPT ME / I'LL SAY YES" design. Renders a transparent PNG via Pillow (upright, bold condensed, maroon `#8B1A1A`), then uses it as the reference image for `gpt-image-1 images.edit` to generate 4 matched scenes. Output: `static/proposals/dont_tempt_me_v3/`.
- **`scripts/tee_scenes_from_refs.py`** — generalised `gpt-image-1` version. Each reference tee in the `REFERENCES` dict produces its own transparent + 4 scenes. Output: `static/proposals/<slug>/`.
- **`scripts/redesign_omg_tees.py`** — `gpt-image-1` variant for redesigning existing OMG slogan tees in place. Supports per-tee `tee_color` (white/black) and per-scene filter on the CLI: `python -m scripts.redesign_omg_tees <slug> [scene_label]` re-rolls only the named scene, leaving approved scenes untouched.
- **`scripts/dont_tempt_me_compose.py`** — **fallback/compose pipeline** for white tees. DALL-E 3 generates BLANK white tees, Claude vision finds the torso bbox, Pillow pastes the slogan at an explicit `width_pct`/`height_pct` of the bbox. Guarantees exact print size but produces a flatter "sticker" look.
- **`scripts/told_her_compose.py`** — compose pipeline adapted for **black tees** with white serif text. Same three-stage approach (DALL-E blank black tee → Claude bbox with inverted black-fabric detection → Pillow paste at a configured `PRINT_GEOMETRY`). Used when gpt-image-1's size floor is too large.

### Pipeline A — gpt-image-1 (default, natural-looking)

1. **Pillow renders the slogan** onto an RGBA canvas. Saved twice: padded (reference) and tight-cropped (Qstomizer upload).
2. **`gpt-image-1 images.edit`** is called with the padded PNG as the reference. Prompt describes the scene plus an `ARTWORK_SPEC` asking the model to copy the reference artwork verbatim.
3. Up to 16 parallel calls when running 4 refs × 4 scenes. Wall time ~45-60s.

### Pipeline B — compose (fallback when Pipeline A's size is wrong)

1. **DALL-E 3** generates a completely blank tee scene (no print).
2. **Claude vision** returns a torso bbox for the white (or black) shirt fabric; a second pass snaps the bbox top to the first row of "near-pure-fabric" pixels so collars and hair don't inflate the print area.
3. **Pillow renders the slogan** sized to an explicit sub-rectangle of that bbox (`PRINT_GEOMETRY` per scene: `top_offset_pct`, `height_pct`, `width_pct`) and `alpha_composite`-s it onto the scene.

### "All live products" scope for batch regeneration

When the ask is "regenerate marketing photos for all my live/mapped products", the in-scope set is the **intersection of**:

1. **Active OMG products** — `GET https://{domain}/admin/api/2024-01/products.json?status=active&limit=250`
2. **Server-side mappings** — `~/omg-shopify/product_mappings.json` on the Azure VM (40.81.137.240). Local `product_mappings.json` is usually stale; always `scp` from server.
3. **Design PNG availability** — `static/design_<slug>.png` either locally OR on server at `/home/vangelisl/omg-shopify/static/design_<slug>.png`.

**5 astous tees default-excluded** — they use real product photography rather than the compose pipeline:
- `astous-na-laloun-cyprus-female-tee` / `-male-tee` / `-female-limited-tee` / `-male-limited-tee` / `-unisex-tee`

(Exception 2026-04-25 WIP: `astous-na-laloun-cyprus-unisex-tee` is being added — Vangelis provided a clean transparent design at `static/astous.png.png`. The other 4 astous variants stay excluded.)

For any in-scope slug whose design PNG is missing locally, `scp` from server before running `compose_marketing_scenes`.

### Updating live OMG products with new scene images

Use `app/shopify_product_creator.py:upload_product_image(product_id, image_path, alt, variant_ids)`.

#### Standard gallery sequence (products with Front/Back placement)

```
1. mockup_cache_..._male_back.png    → male back variants (8)
2. mockup_cache_..._female_back.png  → female back variants (4)
3. mockup_cache_..._male_front.png   → male front variants (8)
4. mockup_cache_..._female_front.png → female front variants (4)
5. 01_closeup_back.png               → ALL female variants (8 = back+front)
6. 02_fullbody_back.png              → ALL female variants (8)
7. 03_product_back.png               → unlinked (gender-neutral)
8. 04_hanger_back.png                → unlinked
9. 01_closeup_back_male.png          → ALL male variants (16)
10. 02_fullbody_back_male.png        → ALL male variants (16)
```

For products without Placement (e.g. `you-are-beautiful-amazing-and-enough-tee`): only 2 TJ mockups (male/female back), and TJ male → all male variants, TJ female → all female variants.

#### When refreshing scenes on a live product

1. Fetch current images via `GET /admin/api/2024-01/products.json?status=active`
2. Delete old scene images — filename starts with `01_`/`02_`/`03_`/`04_` AND does NOT contain `mockup_cache`
3. Keep `mockup_cache_*` images untouched (those are TJ Qstomizer mockups linked to placement variants)
4. Upload new scenes from `static/proposals/<slug>/` with the variant linking above

#### Variant linking gotchas

- **Substring bug**: `'male_back'` IS a substring of `'female_back'`. When matching TJ mockup filenames, **check female FIRST** OR use `'_female_back'` / `'_male_back'` patterns with leading underscores.
- **Shopify variant_ids drop**: Shopify's POST/PUT image endpoints intermittently drop the `variant_ids` field server-side, even when the response shows correct count. Always re-query the product after upload to verify. The `upload_product_image` helper does a PUT retry on POST drops.
- **Reorder + relink in one call**: When updating both position and variant_ids, send both in the same PUT — `{"image": {"id": ..., "position": N, "variant_ids": [...]}}` — and verify the response's variant count matches what you requested.

#### TJ mockup cache (don't re-run Playwright unnecessarily)

TJ Qstomizer mockups are cached locally at `static/proposals/mockup_cache_design_transparent_tj_<slug>_<gender>_<placement>.png`. If you accidentally delete a TJ mockup from Shopify, **restore from cache** via `upload_product_image(pid, cached_path, ..., variant_ids=...)` rather than re-running Playwright (~3 min per mockup).

Regenerate via Qstomizer ONLY when: design PNG has changed OR tee color has changed.

#### Vocabulary: "long body back"

When Vangelis says **"long body back"** for a female image, he means the **lifestyle fullbody scene** `02_fullbody_back.png` (model walking away showing print on the upper back) — **NOT** the TJ mockup. Don't delete TJ mockups unless he explicitly says "TJ mockup" or "Qstomizer mockup".

### Pipeline B in `app/agents/marketing_pipeline.py` (Mango approval scenes)

The agent path (`compose_marketing_scenes`) uses gpt-image-1 for blank scenes. **All geometry is scene-type or design-type based, NOT per-tee** — tuning the rules updates every product uniformly.

#### PRINT_GEOMETRY knobs

- `top_offset_pct` — fraction of bbox height. Negatives allowed (pulls print above snapped fabric top — useful when `_snap_to_fabric_top` overshoots past hair).
- `width_pct` — fraction of bbox width. Bbox-relative; fragile across re-rolls. Last-resort fallback.
- `image_width_pct` — fraction of 1024 (image width). **Used for TEXT designs** (aspect < 0.4). Stable absolute pixel size.
- `image_max_dim_pct` — fraction of 1024, sizes so `max(pw, ph) == this * 1024`. **Used for IMAGE designs** (aspect >= 0.4). Square illustrations sized by `image_width_pct` would have huge `ph`.
- `x_offset_pct` — fraction of bbox width, positive = right. Per-scene fine-tune; brittle.

#### Auto-detection: text vs image design

Inside `compose_marketing_scenes.finish` callback: `is_image_design = design_aspect >= 0.4`. Text designs use `image_width_pct`; image designs use `image_max_dim_pct`, plus `top_offset_pct - 0.05` (sits visually higher), plus heavier spine-weighted blend, plus -20px left bias.

#### Horizontal anchor: blended image-center + spine_x

`_detect_shirt_bbox` returns both bbox AND `spine_x` (Claude-detected back centerline). Final anchor is a weighted blend:
- **TEXT designs**: `0.70 * 512 + 0.30 * spine_x` (prefer image-center, text drift less obvious)
- **IMAGE designs**: `0.30 * 512 + 0.70 * spine_x` (trust spine more for visually-heavy illustrations)
- **IMAGE + closeup only**: additional `-20px` bias — gpt-image-1's closeup composition has a slight rightward model bias on closeups specifically. NOT applied to fullbody/product/hanger (the print is small relative to shirt area there and 20px would overshoot visibly).

#### Fullbody robustness fixes

Fullbody scenes had recurring bugs (overflow shirt, drop to waist, off-center). Three layered fixes:
1. **`_snap_to_fabric_top` capped at 80px max push-down** — prevents the snap from sliding y1 past long hair to the lower back.
2. **Print width capped at `bbox_w * 0.85`** in `_compute_print_rect` — image_width_pct on a 200px-wide fullbody shirt would otherwise overflow.
3. **Force image-center horizontal anchor for all scenes** — overrides `_compute_print_rect`'s bbox-midpoint default; image-center (512) is the most consistent anchor across products and re-rolls.

#### Tuning workflow

```python
await compose_marketing_scenes(
    design_path=..., out_dir=..., tee_color="White",
    scene_filter={"02_fullbody_back"},  # re-roll only this scene
)
```

#### Re-roll variance gotcha

Every gpt-image-1 re-roll produces a different bbox → same `PRINT_GEOMETRY` values give different visual results. If a tweak appears to make things worse, suspect the new bbox before re-tuning.

### Known Limitations

- **`gpt-image-1` has a hard floor on print size.** Dropping reference-PNG `size_ratio` 0.22 → 0.14 → 0.09 → 0.06 → 0.04 and rewriting the prompt with "30-40% back width" / "small caption" barely changed the rendered print size. The model has a fixed "slogan tee print should be this big" prior and ignores size instructions. If a review calls the print "too big" and gpt-image-1 can't go smaller, switch the affected scene to the compose pipeline.
- **`gpt-image-1` reflows multi-word lines.** It will break long horizontal lines into shorter stacks (e.g. `"TOLD HER SHE'S THE ONE."` becomes 2-3 lines) regardless of the reference PNG layout or explicit "single-line" prompt instructions.
- **`gpt-image-1` sometimes truncates words** (e.g. drops "ME" from "DON'T TEMPT ME"). Mitigation: shrink the reference to ~55% canvas width for generous margin and add a "do NOT crop word X" line to `ARTWORK_SPEC`. Re-rolling usually lands a clean one.
- **DALL-E framing varies per call in the compose pipeline.** Each re-roll can give a closer or more distant shot, which shifts the torso bbox size and therefore the print absolute-size. If the print looks wrong, re-rolling the scene (not just adjusting `PRINT_GEOMETRY`) is often the fix. Tightening framing in the scene prompt (e.g. "subject fills 80% of vertical frame") helps.
- **OpenAI moderation blocks profanity-adjacent slogans** (`σέξι μαδαφάκα` returns `safety_violations=[abuse]`).

### Typography Rules (Kyriaki's style review, 2026-04-20/22)

Applied as defaults for all slogan tees:

- **Modest print scale** — target 30-50% of shirt back width, not 80%+. Oversized fills look wrong.
- **Two-line hierarchy needs visible size AND weight difference.** Same font at different sizes reads chunky; pair a bold condensed top (Impact) with a regular-weight sans sub (Arial Regular). Sub-to-top size ratio ≤ 0.50.
- **Keep the Mango-proposed slogan wording verbatim** in the rendered print — don't substitute a different variant of the phrase.
- **Hybrid pipeline for review-driven tuning.** When gpt-image-1 renders the closeup/product/hanger shots at an acceptable size but the fullbody looks disproportionately large, swap just the fullbody through the compose pipeline. Copy the single scene into the canonical folder; accept minor style drift between sticker-paste and DTG-render scenes.
- **Using an approved scene as `images.edit` reference can preserve size proportions.** Passing e.g. `01_closeup_back.png` (approved) to `gpt-image-1` when regenerating `02_fullbody_back.png` anchors the print size more reliably than passing the transparent design PNG alone. Works better than any prompt-only tweak.

See `doc/marketing-photo-generation.md` for font candidates, reference table, full CLI reference, and the `PRINT_GEOMETRY` schema used by the compose scripts.

### Design Replication Workflow (end-to-end)

When Vangelis asks to "replicate" / "launch" a new slogan tee, run these 5 steps in one pass — don't ping-pong between them:

1. **Confirm inputs:** slug (kebab), exact slogan wording (with `\n` for two-line hierarchy), tee color (one of the 6 Qstomizer colors). These are the only things you can't infer.
2. **Pick pipeline:** gpt-image-1 by default (`tee_scenes_from_refs.py` / `redesign_omg_tees.py`); compose fallback (`dont_tempt_me_compose.py` / `told_her_compose.py`) when size floor / line reflow bites.
3. **Render scenes:** add entry to the script's config dict (`REFERENCES` / `TEES`), run it. Output lands in `static/proposals/<slug>/` with 4 scenes + two transparent PNGs (padded + tight-cropped).
4. **Register in `scripts/mail_tj_mockups.py`:** append `(title, slug, [4 scene names], qstomizer_color)` tuple to the `DESIGNS` list. Color MUST match the tee color (white-text on White Qstomizer = blank mockup).
5. **Send:** `.venv/Scripts/python -m scripts.mail_tj_mockups send --force`. Produces 12 TJ mockups at `vertical_offset=-0.25` (upper back, clamped for tall designs), emails 24 inlined images to livadient@gmail.com + kmarangos@hotmail.com + kyriaki_mara@yahoo.com. ~12 min end-to-end. Short-circuits (~8s) on cached mockups unless `--force`.

Expected feedback loops from Kyriaki: "too big" → switch scene to compose / use approved scene as `images.edit` reference; "too high" → tune `vertical_offset`; "missing word" → shrink `text_width_ratio` + `do NOT crop X` in `ARTWORK_SPEC`. Sweet-spot `width_pct` 0.38-0.48.

Full workflow with expected failures + fixes: `doc/design-replication-workflow.md`.

## Google API Integrations (Atlas Data Sources)

Atlas uses real Google data to ground its SEO and Ads recommendations.

### Google Search Console

**Module:** `app/agents/google_search_console.py`
**Auth:** Service account (`GOOGLE_SERVICE_ACCOUNT_FILE`) with `webmasters.readonly` scope.

| Market | Site | Country Filter |
|--------|------|----------------|
| CY | `sc-domain:omg.com.cy` | Cyprus |
| GR | `sc-domain:omg.gr` | Greece |
| EU | Both sites combined | No filter |

- `ohmangoes.com` is excluded — Google chose `omg.gr` as its canonical
- GSC data has ~3 day lag; date ranges are adjusted automatically
- For EU, results from both sites are merged with weighted average positions

### Google Ads Keyword Planner

**Module:** `app/agents/google_keyword_planner.py`
**Auth:** OAuth2 (`GOOGLE_ADS_*` env vars). Refresh token obtained via `scripts/get_google_refresh_token.py`.

- Requires **Basic access** developer token (test access only works with test accounts)
- Fetches keyword ideas with real monthly search volume, CPC ranges, and competition
- Market-specific geo targets (Cyprus/Greece/Europe) and language (Greek/English)
- Google Ads Customer ID: `9820211305`

### Google Cloud Project

- **Project:** `omg-shop-492712`
- **Service account:** `omg-384@omg-shop-492712.iam.gserviceaccount.com`
- **Enabled APIs:** Search Console API, Google Ads API

## Future Considerations

- Contact tshirtjunkies.co (`info@tshirtjunkies.co` / `+357-99897089`) for a Storefront API token to enable fully programmatic checkout
- They have a wholesale/partner program that could simplify this
- Add `asyncio.Semaphore` to limit concurrent Playwright instances if order volume increases
- Add webhook signature verification using `SHOPIFY_WEBHOOK_SECRET`
- Set a fixed ngrok domain (`NGROK_DOMAIN`) to avoid webhook URL changes on restart
- Extend shipping method mapping for more countries as sales expand
