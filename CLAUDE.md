# OMG Shopify → TShirtJunkies Order Service

## Project Overview

Python FastAPI service that receives Shopify webhook events from **omg.com.cy** and automatically creates corresponding orders on **tshirtjunkies.co** (a Shopify store based in Cyprus).

## Architecture

```
omg.com.cy (Shopify)  →  webhook: orders/create
        ↓
   FastAPI Service (this project)
   ├── Receive webhook
   ├── Map OMG variant IDs → TShirtJunkies variant IDs
   ├── POST to tshirtjunkies.co/cart/add.js (session-based)
   └── Return checkout URL for human confirmation
```

## Tech Stack

- **Python 3.13** (venv in `.venv/`)
- **FastAPI** + **Uvicorn**
- **httpx** for async HTTP requests
- **Pydantic** for data models

## Running

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload
```

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/map-products?source_url=...&target_url=...` | Create mapping between two product URLs |
| `GET` | `/mappings` | View all saved product mappings |
| `POST` | `/webhook/order-created` | Shopify webhook handler — maps items, builds cart, returns checkout URL |
| `POST` | `/test-cart?variant_id=...&quantity=...` | Test adding an item to TShirtJunkies cart |

## Product Mappings

Mappings are stored in `product_mappings.json` at the project root. Variants are matched by size option.

### Male Tee (S–5XL) — Full coverage

- **OMG:** `astous-na-laloun-graphic-tee-male-eu-edition` (€30–39.50)
- **TShirtJunkies:** `classic-tee-up-to-5xl` (€20–22)

### Female Tee (S–XL) — Full coverage

- **OMG:** `astous-na-laloun-graphic-tee-female-eu-edition` (€30)
- **TShirtJunkies:** `women-t-shirt` (€23)

Note: OMG female EU edition only goes up to XL, which matches TJ perfectly.

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
  main.py           — FastAPI app with all endpoints
  config.py         — Settings (tshirtjunkies base URL, webhook secret)
  models.py         — Pydantic models (ProductMapping, VariantMapping)
  mapper.py         — Product mapping logic, saves to product_mappings.json
  shopify_client.py — Fetches products from any Shopify store
  cart_client.py    — TShirtJunkies cart operations
product_mappings.json — Saved variant ID mappings
```

## Future Considerations

- Contact tshirtjunkies.co (`info@tshirtjunkies.co` / `+357-99897089`) for a Storefront API token to enable fully programmatic checkout
- They have a wholesale/partner program that could simplify this
- Browser automation (Playwright) is an alternative for auto-checkout but is more fragile
