# Webhook Order Flow

## Overview

When a customer places an order on **omg.com.cy**, Shopify sends an `orders/create` webhook to this service. The service maps the order items to TShirtJunkies products, then runs Playwright automation in the background to upload the design, customize the product, and build shareable checkout links.

## Webhook Endpoint

**`POST /webhook/order-created`**

Shopify sends the full order JSON payload. The service processes it in two phases: an immediate synchronous response and background async tasks.

## Flow Diagram

```
Shopify (omg.com.cy)
  │  POST /webhook/order-created
  ▼
FastAPI Service
  ├── 1. Map variant_id → TShirtJunkies variant (by size)
  ├── 2. Respond immediately with mapping results
  │
  └── Background Task (per mapped item):
      ├── 3. Determine product_type (male/female) from source handle
      ├── 4. Extract size from variant_title
      ├── 5. Run Playwright automation (separate thread, ProactorEventLoop)
      │     └── Qstomizer: upload design, select White + size, add to cart
      ├── 6. Fetch /cart.js for Qstomizer properties
      ├── 7. Build shareable cart permalink
      └── 8. Send email notification with order details + links
```

## Step-by-Step

### 1. Variant Mapping (Synchronous)

Each line item's `variant_id` is looked up in `product_mappings.json`. Variants are matched by **size option** (e.g., OMG "Male / L" maps to TJ "L" on `classic-tee-up-to-5xl`).

### 2. Immediate Response

The service responds to Shopify immediately with the mapping results. This prevents webhook timeout errors. All heavy work happens in background tasks.

### 3. Product Type Detection

The `source_handle` determines the product type:
- Contains `female` or `women` -> `female` (maps to TJ `women-t-shirt`)
- Otherwise -> `male` (maps to TJ `classic-tee-up-to-5xl`)

### 4. Size Extraction

Size is extracted from `variant_title` (e.g., "Male / L" -> "L", or just "L").

### 5. Playwright Automation

Runs in a **separate thread** with its own `ProactorEventLoop` (required on Windows because uvicorn's `SelectorEventLoop` does not support subprocesses). See [Qstomizer Automation](qstomizer-automation.md) for the full 13-step process.

### 6. Cart Data Retrieval

After Qstomizer adds the item to cart, the service fetches `/cart.js` to obtain Qstomizer-specific properties:
- `_customorderid` -- Qstomizer order identifier
- `_customorderkey` -- Order authentication key
- `_customimagefront` -- Rendered mockup image URL

### 7. Cart Permalink

Shopify checkout sessions are browser-specific and do not persist across devices. Instead of returning `/checkouts/...` URLs, the service builds **cart permalinks**:

```
https://tshirtjunkies.co/cart/VARIANT_ID:QTY?checkout[email]=...&checkout[shipping_address][first_name]=...&attributes[_customorderid]=...
```

These links:
- Work in any browser or device (no session dependency)
- Pre-fill the checkout form with customer shipping details
- Include Qstomizer properties as cart attributes

### 8. Email Notification

An HTML email is sent with:
- Order number, customer name, total
- Shipping details block (for copy-paste into TJ checkout if needed)
- Product table with size, quantity, and **cart permalink** for each item
- If Playwright failed: red error banner with link to the Manual Order page

See [Email System](email-system.md) for details.

## Shipping Method Mapping

OMG shipping methods are mapped to TShirtJunkies checkout options by country code:

| Country | OMG Method | OMG Price | TJ Match | TJ Price | Notes |
|---------|-----------|-----------|----------|----------|-------|
| CY | Travel Express | EUR 3.00 | Travel Express | EUR 3.00 | Must be actively selected |
| GR | Geniki Taxydromiki | EUR 5.00 | Geniki pickup | EUR 5.00 | Auto-selected (first option) |
| FR | Europe postal | EUR 6.00 | Postal Shipping | EUR 5.00 | Auto-selected (only option) |

Defined in `SHIPPING_METHOD_MAP` in `app/qstomizer_automation.py`. Unmapped countries fall back to the default (first/cheapest) shipping option.

## Manual Order Flow

**`GET /manual-order`** -- Form page where you can paste an OMG order confirmation email.

**`POST /manual-order`** -- Parses the pasted email text using `email_parser.py`, extracts items, shipping address, and totals, then triggers the same Playwright + email flow as the webhook.

This is used as a fallback when:
- The webhook fails or is missed
- Playwright automation fails for some items (the error email includes a link to this page)
- Testing the flow without placing a real order

## Fulfillment Flow

**`GET /fulfill-order`** -- Form page for fulfilling OMG orders with tracking info.

**`POST /fulfill-order`** -- Creates a fulfillment on the OMG Shopify store via Admin API. Accepts:
- OMG order number
- Tracking number, URL, and carrier name

**`POST /fulfill-order/parse`** -- Parses a TShirtJunkies shipping/fulfillment email to extract the OMG order number and tracking details automatically.

The fulfillment flow uses `app/omg_fulfillment.py` which calls the Shopify Admin API to look up the order by number and create a fulfillment for all unfulfilled line items.

## Key Files

- `app/main.py` -- Webhook handler, background task orchestration, manual order and fulfillment endpoints
- `app/mapper.py` -- Product mapping logic
- `app/qstomizer_automation.py` -- Playwright browser automation + cart permalink builder
- `app/email_service.py` -- Order notification emails
- `app/email_parser.py` -- OMG order email parser
- `app/omg_fulfillment.py` -- Shopify Admin API fulfillment
