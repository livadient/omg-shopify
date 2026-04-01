# OMG Shopify → TShirtJunkies Order Service

## Project Overview

Python FastAPI service that receives Shopify webhook events from **omg.com.cy** and automatically creates corresponding orders on **tshirtjunkies.co** (a Shopify store based in Cyprus). When an order comes in, the service uses **Playwright browser automation** to upload the design, select color (White) and matching size, and add the customized item to cart on TShirtJunkies via their Qstomizer app. An **email notification** is then sent with order details and cart links.

## Architecture

```
omg.com.cy (Shopify)  →  webhook: orders/create
        ↓
   FastAPI Service (this project)
   ├── Receive webhook (respond immediately)
   ├── Map OMG variant IDs → TShirtJunkies variant/product IDs
   ├── [Background] Playwright: upload design to Qstomizer, select White + matching size, add to cart
   └── [Background] Email notification with order details + cart link to configured recipients
```

## Tech Stack

- **Python 3.13** (venv in `.venv/`)
- **FastAPI** + **Uvicorn**
- **httpx** for async HTTP requests
- **Pydantic** for data models
- **Playwright** for browser automation (Qstomizer customization)
- **aiosmtplib** for async email notifications
- **python-dotenv** for `.env` configuration

## Running

```bash
# Install dependencies
.venv/Scripts/pip install -r requirements.txt
playwright install chromium

# Configure (copy .env.example to .env and fill in values)
cp .env.example .env

# Run
.venv/Scripts/python -m uvicorn app.main:app --reload
```

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

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
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

## Webhook Flow

1. Shopify sends `orders/create` webhook with full order JSON
2. Service maps each line item's `variant_id` to a TShirtJunkies variant (matched by size)
3. Responds immediately with mapping results
4. **Background task** runs for each mapped item:
   - Determines product type (male/female) from source handle
   - Extracts size from `variant_title`
   - Runs Playwright automation: opens Qstomizer, selects White, uploads `static/front_design.png`, selects correct size, adds to cart
5. **Email notification** sent to configured recipients with order details + cart links

## Product Mappings

Mappings are stored in `product_mappings.json` at the project root. Variants are matched by size option.

### Male Tee (S–5XL) — Full coverage

- **OMG:** `astous-na-laloun-graphic-tee-male-eu-edition` (€30–39.50)
- **TShirtJunkies:** `classic-tee-up-to-5xl` (€20–22)

### Female Tee (S–XL) — Full coverage

- **OMG:** `astous-na-laloun-graphic-tee-female-eu-edition` (€30)
- **TShirtJunkies:** `women-t-shirt` (€23)

Note: OMG female EU edition only goes up to XL, which matches TJ perfectly.

## Qstomizer (Product Customization)

TShirtJunkies uses **Qstomizer** for custom design uploads. The service automates this via Playwright:

| Product | Qstomizer Product ID |
|---------|---------------------|
| Classic Tee (Male, up to 5XL) | `9864408301915` |
| Women's T-Shirt | `8676301799771` |

### Playwright Automation Steps

1. Open Qstomizer page (1920x1080 viewport required for canvas rendering)
2. Hide overlapping elements (text window, sticky header)
3. Select color via `.colorVarWrap` swatch click (jQuery trigger)
4. Upload design image to `#btnUploadImage` file input
5. Wait for upload + processing (`#msgUploading` then `#msgProcessing`)
6. Click `.imagesubcontainer` thumbnail via jQuery to place on canvas
7. Select size via `#variantValues1` dropdown
8. Click `#addtocart` (ORDER NOW) via jQuery
9. Set size/qty in quantity window (`.infoQty` inputs matched to `.Rtable-cell` labels)
10. Click ADD TO CART in quantity window
11. Wait for "Saving Data..." → redirect to `/cart`

Available colors: Black, Navy Blue, Red, Royal Blue, Sport Grey, White (default: White)

### Manual Usage

```bash
.venv/Scripts/python -m app.qstomizer_automation male L White
```

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
- Checkout URL format: `https://tshirtjunkies.co/cart/variant_id:qty,variant_id:qty`
- Payment still requires human confirmation (no programmatic checkout without Storefront API token from store owner)

## OMG Store API Notes

- **omg.com.cy blocks individual product `.json` endpoints** (404)
- Products must be fetched via `/products.json?limit=250&page=N` catalog endpoint
- The `shopify_client.py` handles this automatically with a fallback

## Key Files

```
app/
  main.py                — FastAPI app, webhook handler, background task orchestration
  config.py              — Settings loaded from .env (SMTP, webhook secret, etc.)
  models.py              — Pydantic models (ProductMapping, VariantMapping)
  mapper.py              — Product mapping logic, saves to product_mappings.json
  shopify_client.py      — Fetches products from any Shopify store
  cart_client.py         — TShirtJunkies cart operations
  qstomizer_automation.py — Playwright browser automation for Qstomizer
  email_service.py       — Async email notifications via aiosmtplib
product_mappings.json    — Saved variant ID mappings
static/
  front_design.png       — Design image uploaded to Qstomizer
.env.example             — Template for environment variables
```

## OMG Shopify Admin API (OAuth)

The service connects to the OMG Shopify store via a custom app using OAuth. The app has the following scopes:

`read_orders`, `write_fulfillments`, `read_products`, `write_products`, `read_customers`, `write_customers`, `read_inventory`, `write_inventory`, `read_shipping`, `write_shipping`, `read_order_edits`, `write_order_edits`

### Setup

1. Set `OMG_SHOPIFY_CLIENT_ID` and `OMG_SHOPIFY_CLIENT_SECRET` in `.env`
2. Start the server and go to `/shopify-auth` to authorize
3. Save the returned token as `OMG_SHOPIFY_ADMIN_TOKEN` in `.env`
4. The token is permanent — no refresh needed

### Admin API Usage

- Base URL: `https://52922c-2.myshopify.com/admin/api/2024-01/`
- Auth header: `X-Shopify-Access-Token: {token}`
- Used by `app/omg_fulfillment.py` for order lookup and fulfillment creation

## Future Considerations

- Contact tshirtjunkies.co (`info@tshirtjunkies.co` / `+357-99897089`) for a Storefront API token to enable fully programmatic checkout
- They have a wholesale/partner program that could simplify this
- Add `asyncio.Semaphore` to limit concurrent Playwright instances if order volume increases
- Add webhook signature verification using `SHOPIFY_WEBHOOK_SECRET`
