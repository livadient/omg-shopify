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

## Shipping Method Mapping

OMG shipping methods are mapped to TShirtJunkies checkout options by country:

| Country | OMG Method | OMG Price | TJ Match | TJ Price |
|---------|-----------|-----------|----------|----------|
| **CY** | Travel Express | EUR 3.00 | Travel Express pickup | EUR 3.00 |
| **GR** | Geniki Taxydromiki | EUR 5.00 | Geniki Taxydromiki pickup | EUR 5.00 |
| **FR** | Europe postal | EUR 6.00 | Postal Shipping | EUR 5.00 |

Mapping is defined in `SHIPPING_METHOD_MAP` in `qstomizer_automation.py`. For CY, the automation actively selects Travel Express. For GR and FR, the correct option is auto-selected (first/only option).

## Product Mappings

Mappings are stored in `product_mappings.json` at the project root. Variants are matched by size option.

### Astous na Laloun - Limited Edition Tee (unified product)

The old separate male/female EU edition products have been replaced by a single unified product with Gender+Size variants:

- **OMG:** `astous-na-laloun-limited-edition-tee` (EUR 30-39.50)
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
    translation_checker.py — Agent "Hermes": Daily translation checker (English→Greek via Claude)
product_mappings.json    — Saved variant ID mappings
data/
  proposals.json         — Agent proposal storage (persisted via Docker volume)
  ranking_history.json   — Ranking advisor report history
static/
  front_design.png       — Design image uploaded to Qstomizer
  proposals/             — Generated design images + cached mockups
  checkout_result.png    — Latest Playwright screenshot (auto-generated)
tests/                   — Unit tests (pytest) — 130 tests, all mocked, no external services
.env.example             — Template for environment variables
```

## Agent Schedules

All times are Cyprus time (Europe/Nicosia):

| Agent (Name) | Schedule | Time | Purpose |
|--------------|----------|------|---------|
| Translation Checker (Hermes) | Daily | 02:00 | Check for untranslated/outdated content, translate EN→GR |
| Design Creator (Mango) | Mon-Fri | 04:00 | Research trends, generate 5 designs, pre-cache mockups |
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
| [agent3-ranking-advisor.md](doc/agent3-ranking-advisor.md) | Ranking Advisor agent (market rotation) |
| [agent4-translation-checker.md](doc/agent4-translation-checker.md) | Translation Checker agent (EN→GR via Claude) |

## Testing

Run unit tests: `pytest tests/ -v`

130 tests covering all modules. All external services (HTTP, SMTP, Claude, DALL-E, Playwright) are mocked. File-based tests use `tmp_path` for isolation.

## OMG Shopify Admin API (OAuth)

The service connects to the OMG Shopify store via a custom app using OAuth. The app has the following scopes:

`read_orders`, `write_fulfillments`, `read_products`, `write_products`, `read_customers`, `write_customers`, `read_inventory`, `write_inventory`, `read_shipping`, `write_shipping`, `read_order_edits`, `write_order_edits`, `read_translations`, `write_translations`, `read_locales`, `write_locales`, `read_online_store_navigation`, `write_online_store_navigation`

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
- `product_mappings.json` should persist across deploys (mount as volume or commit to repo)
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
- `validate_design_text(image_path, expected_text)` -- Claude vision reads text in generated images and checks correctness
- `generate_design_with_text_check(concept, expected_text, ...)` -- generates via DALL-E, validates text with Claude, regenerates with correction prompt if wrong (up to 2 retries)
- Pillow text designs (slogan type) skip `rembg` since they are already transparent PNG

## Future Considerations

- Contact tshirtjunkies.co (`info@tshirtjunkies.co` / `+357-99897089`) for a Storefront API token to enable fully programmatic checkout
- They have a wholesale/partner program that could simplify this
- Add `asyncio.Semaphore` to limit concurrent Playwright instances if order volume increases
- Add webhook signature verification using `SHOPIFY_WEBHOOK_SECRET`
- Set a fixed ngrok domain (`NGROK_DOMAIN`) to avoid webhook URL changes on restart
- Extend shipping method mapping for more countries as sales expand
