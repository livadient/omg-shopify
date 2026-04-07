# Email System

## Overview

The project has two email subsystems: **order notification emails** for the webhook/order flow, and **agent emails** shared by all AI agents. Both use the same SMTP configuration.

## SMTP Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `SMTP_HOST` | (empty) | SMTP server (e.g., `smtp.gmail.com`) |
| `SMTP_PORT` | `587` | SMTP port (587 for STARTTLS) |
| `SMTP_USERNAME` | (empty) | SMTP login username |
| `SMTP_PASSWORD` | (empty) | SMTP login password (app password for Gmail) |
| `EMAIL_SENDER` | (empty) | From address for all notifications |
| `EMAIL_RECIPIENTS` | (empty) | Comma-separated list of notification recipients |

Both subsystems skip sending silently if SMTP is not configured.

## Order Notification Emails

**File:** `app/email_service.py`

Function: `send_order_notification(order_number, customer_name, order_total, currency, items, shipping)`

Sends an HTML email when a new OMG order is processed. The email contains:

- **Subject:** `New OMG Order #1234 -- Customer Name`
- **Order summary:** Order number, customer name, total with currency
- **Shipping details block:** Name, address, city, zip, country, phone, email -- formatted for easy copy-paste into TJ checkout
- **Product table:** Each item with title, size, quantity, and action link:
  - **Cart permalink** (if Playwright succeeded) -- "Open Cart" link
  - **Qstomizer fallback link** (if cart link unavailable) -- direct link to manual design upload
  - **Error message** (if automation failed) -- shown in red below the link
  - **Mockup image** (if available) -- thumbnail of the rendered product preview
- **Playwright failure banner:** If any items failed automation, a red banner appears with a link to the Manual Order page at `{server_base_url}/manual-order`
- **Footer:** Explanation that cart links open TShirtJunkies with the customized item ready for checkout

Uses `aiosmtplib` with STARTTLS for async sending. Email is constructed as `MIMEMultipart("alternative")` with HTML body.

## Agent Emails

**File:** `app/agents/agent_email.py`

Shared email utility used by all AI agents. Each agent has a name and personality reflected in email styling:

| Agent | Name | Email Color | Greeting |
|-------|------|-------------|----------|
| Design Creator | Mango | Purple | "Hey boss, Mango here!" |
| Blog Writer | Olive | Green | "Olive here -- new post ready!" |
| Ranking Advisor | Atlas | Blue | "Atlas reporting for duty" |
| Translation Checker | Hermes | Blue | "Hermes here -- translation run complete" |
| SEO Optimizer | Sphinx | N/A | Does not send emails |

### `send_agent_email(subject, html_body, inline_images=None)`

Sends an HTML email with optional inline image attachments. Supports CID-based inline images:

```python
await send_agent_email(
    subject="New Design Proposal",
    html_body='<img src="cid:design_preview">',
    inline_images={"design_preview": Path("static/proposals/design_abc.png")},
)
```

- Uses `MIMEMultipart("related")` with nested `MIMEMultipart("alternative")` for HTML + inline images
- Images are attached as `MIMEImage` with `Content-ID` headers
- Sends to the same `EMAIL_RECIPIENTS` as order notifications

### `send_error_email(agent_name, error, context="")`

Sends a formatted error notification when an agent fails:

- Red header banner with agent name
- Error type and message
- Optional context string
- Collapsible full traceback (HTML `<details>` element)
- Footer with server URL

## Email Parser

**File:** `app/email_parser.py`

Function: `parse_order_email(text)` -- Parses pasted OMG order confirmation email text into structured data for the manual order flow.

### Parsing Logic

1. **Line items:** Regex matches `Product Name x Qty` pattern, followed by a size line (XS through 5XL). Determines `product_type` (male/female) from title keywords.
2. **Total:** Finds "total" header and extracts the next line containing a euro sign.
3. **Shipping address:** Finds "Shipping address" header, then reads:
   - Line 1: Full name (split into first/last)
   - Line 2: Street address
   - Line 3: Zip + city (handles both "2109 Nicosia" and "Nicosia 2109" formats)
   - Line 4: Country name

### Country Code Mapping

`COUNTRY_CODES` dict maps country names to ISO codes. Covers 25+ countries including:
- Cyprus (CY), Greece (GR), United Kingdom (GB), Germany (DE), France (FR), Italy (IT), Spain (ES), Netherlands (NL), Belgium (BE), Austria (AT), Portugal (PT), Ireland (IE), Sweden (SE), Denmark (DK), Finland (FI), Poland (PL), Czech Republic (CZ), Romania (RO), Bulgaria (BG), Hungary (HU), United States (US), Canada (CA), Australia (AU)

Fallback: uses first two characters of the country name uppercased.

### Return Value

```python
{
    "items": [{"title": "...", "variant_title": "L", "quantity": 1, "product_type": "male"}],
    "shipping": {"first_name": "...", "last_name": "...", "address1": "...", "city": "...", "zip": "...", "country": "...", "country_code": "CY"},
    "total": "EUR 30,00",
}
```

## Fulfillment Email Parser

**File:** `app/omg_fulfillment.py` -- `parse_fulfillment_email(text)`

Parses TShirtJunkies shipping/fulfillment emails to extract:
- OMG order number from `(OMG #1234)` pattern
- Tracking URL from any URL containing "track"
- Tracking company from carrier name keywords (DHL, Cyprus Post, ACS, FedEx, UPS, etc.)
- Tracking number from alphanumeric strings near "tracking" keyword
