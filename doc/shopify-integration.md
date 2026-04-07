# Shopify Integration

## Overview

The project integrates with two Shopify stores:
- **omg.com.cy** (OMG store) -- via Admin API (authenticated, OAuth)
- **tshirtjunkies.co** (TJ store) -- via public Storefront/cart endpoints (no auth)

Admin API version: **2024-01**

## OMG Shopify Admin API

### Authentication

Uses a custom Shopify app with OAuth. The access token is permanent (no refresh needed).

**OAuth scopes:** `read_orders`, `write_fulfillments`, `read_products`, `write_products`, `read_customers`, `write_customers`, `read_inventory`, `write_inventory`, `read_shipping`, `write_shipping`, `read_order_edits`, `write_order_edits`, `read_translations`, `write_translations`, `read_locales`, `write_locales`

**Base URL:** `https://52922c-2.myshopify.com/admin/api/2024-01/`

**Auth header:** `X-Shopify-Access-Token: {token}`

### OAuth Flow

1. Navigate to `/shopify-auth` to start authorization
2. Redirects to Shopify OAuth consent screen
3. Shopify calls back to `/shopify-auth/callback` with an authorization code
4. `exchange_code_for_token()` exchanges the code for an access token
5. Save the token as `OMG_SHOPIFY_ADMIN_TOKEN` in `.env`

Redirect URI is hardcoded to `http://localhost:8080/shopify-auth/callback` -- must match the Shopify Partners dashboard configuration.

### Order Fulfillment (`app/omg_fulfillment.py`)

- `find_order_by_number(order_number)` -- Look up an order by its display number (e.g., "1001")
- `fulfill_order(order_number, tracking_number, tracking_url, tracking_company)` -- Create a fulfillment for all unfulfilled line items. Uses fulfillment orders API. Notifies the customer automatically.
- `parse_fulfillment_email(text)` -- Extract OMG order number and tracking info from TJ shipping emails

### Product Creation (`app/shopify_product_creator.py`)

Creates products on the OMG store with standardized variant structure:

- **Options:** Gender (Male/Female) + Size
- **Male variants:** S, M, L, XL (EUR 30.00), 2XL (EUR 35.00), 3XL (EUR 37.00), 4XL, 5XL (EUR 39.50)
- **Female variants:** S, M, L, XL (EUR 30.00)
- **inventory_management:** `null` -- Shopify does not track stock (print-on-demand, always available)

Functions:
- `create_product(title, body_html, tags, image_path, published)` -- Create a product with all Gender+Size variants
- `upload_product_image(product_id, image_path, alt)` -- Upload an image to an existing product (base64 encoded)
- `create_mappings_for_product(omg_product, design_image)` -- Create TWO product mappings (male -> TJ Classic Tee, female -> TJ Women's Tee), fetches TJ product data to match variant IDs by size
- `fetch_mockup_from_qstomizer(design_image_path, product_type, size)` -- Run Qstomizer automation to get a rendered mockup URL
- `download_image(url, dest)` -- Download an image from URL to local file

TShirtJunkies target product IDs:
- Male: `classic-tee-up-to-5xl` (product ID `9864408301915`)
- Female: `women-t-shirt` (product ID `8676301799771`)

### Blog Management (`app/shopify_blog.py`)

- `list_blogs()` -- List all blogs on the OMG store
- `list_articles(blog_id, limit)` -- List articles from a blog (uses `OMG_SHOPIFY_BLOG_ID` if not specified)
- `create_article(title, body_html, tags, meta_title, meta_description, published, blog_id)` -- Create a blog article with SEO metadata

### SEO Management (`app/seo_management.py`)

Run via CLI: `.venv/Scripts/python -m app.seo_management [task]`

**Tasks:**

1. **`fix-handles`** -- Fix duplicate product URL handles and standardize "na" -> "va" spelling in Astous product handles/titles. Also updates `product_mappings.json` to match.

2. **`homepage-seo`** -- Update homepage SEO meta tags via shop metafields (GraphQL Admin API). Sets title to "Graphic T-Shirts Cyprus | Unique Tees | OMG.com.cy" and meta description with Cyprus-focused keywords. Falls back to manual instructions if metafields approach fails.

3. **`create-collections`** -- Create Cyprus-specific custom collections ("Cyprus Graphic Tees", "Greek Cyprus Shirts") with bilingual descriptions (English + Greek) and SEO metadata. Automatically adds all Astous products to the collections.

4. **`all`** (`run_all()`) -- Runs all three tasks in sequence.

## Shopify Client (`app/shopify_client.py`)

Public storefront client for fetching product data from any Shopify store (no authentication required).

- `fetch_product_by_handle(base_url, handle)` -- Fetch a product by handle. Tries `/products/{handle}.json` first, falls back to paginated `/products.json?limit=250&page=N` search.
- `fetch_product_from_url(product_url)` -- Parse a product URL to extract base_url and handle, then fetch.

**Note:** omg.com.cy blocks individual product `.json` endpoints (returns 404). The fallback to paginated catalog search handles this automatically.

## TShirtJunkies Public API

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
- Cart operations are session/cookie-based
- Cart permalink format: `https://tshirtjunkies.co/cart/variant_id:qty,variant_id:qty`
- Payment requires human confirmation (no programmatic checkout without a Storefront API token)

### Translations (`app/shopify_translations.py`)

Uses the Shopify GraphQL Translations API to manage Greek (el) translations for all store content.

- `get_untranslated_resources(resource_type, locale)` -- Query `translatableResources` to find fields missing translations or with outdated translations
- `register_translations(resource_id, translations, locale)` -- Write translations via `translationsRegister` mutation
- Skips `handle` fields (URL slugs must remain in English)
- Used by the Translation Checker agent (Hermes)

**Required scopes:** `read_translations`, `write_translations`, `read_locales`, `write_locales`

## Key Files

- `app/omg_fulfillment.py` -- Order lookup, fulfillment creation, OAuth token exchange
- `app/shopify_product_creator.py` -- Product creation with Gender+Size variants, mockup fetching
- `app/shopify_blog.py` -- Blog article CRUD
- `app/seo_management.py` -- Handle fixes, homepage SEO, collection creation
- `app/shopify_translations.py` -- Shopify GraphQL Translations API (EN→GR)
- `app/shopify_client.py` -- Public storefront product fetching
- `app/cart_client.py` -- TShirtJunkies cart operations
