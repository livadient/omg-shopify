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

### Male Tee (S-5XL) - Full coverage

- **OMG:** `astous-na-laloun-graphic-tee-male-eu-edition` (EUR 30-39.50)
- **TShirtJunkies:** `classic-tee-up-to-5xl` (EUR 20-22)

### Female Tee (S-XL) - Full coverage

- **OMG:** `astous-na-laloun-graphic-tee-female-eu-edition` (EUR 30)
- **TShirtJunkies:** `women-t-shirt` (EUR 23)

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
product_mappings.json    — Saved variant ID mappings
static/
  front_design.png       — Design image uploaded to Qstomizer
  checkout_result.png    — Latest Playwright screenshot (auto-generated)
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

## OAuth Redirect URI

The Shopify OAuth redirect URI is hardcoded to `http://localhost:8080/shopify-auth/callback`. This must match the **Allowed redirection URL(s)** configured in the Shopify Partners dashboard for the app. If you change the port, update the whitelist there too.

## Email Notifications

Order notification emails include:
- Order number, customer name, total
- **Shipping details block** with name, address, city, zip, country, phone, email (for copy-paste if needed)
- Product table with size, qty, and **cart permalink** (shareable checkout link)
- If Playwright fails: red banner with link to Manual Order page (`http://40.81.137.193:8080/manual-order`)
- Qstomizer fallback links for manual design upload

## Future Considerations

- Contact tshirtjunkies.co (`info@tshirtjunkies.co` / `+357-99897089`) for a Storefront API token to enable fully programmatic checkout
- They have a wholesale/partner program that could simplify this
- Add `asyncio.Semaphore` to limit concurrent Playwright instances if order volume increases
- Add webhook signature verification using `SHOPIFY_WEBHOOK_SECRET`
- Set a fixed ngrok domain (`NGROK_DOMAIN`) to avoid webhook URL changes on restart
- Extend shipping method mapping for more countries as sales expand
