import logging
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.staticfiles import StaticFiles

from fastapi.responses import HTMLResponse, JSONResponse

from app.config import settings
from app.email_parser import parse_order_email
from app.email_service import send_order_notification
from app.mapper import load_mappings
from app.models import ProductMapping
from app.omg_fulfillment import exchange_code_for_token, fulfill_order, parse_fulfillment_email
from app.qstomizer_automation import customize_and_add_to_cart

logger = logging.getLogger(__name__)

app = FastAPI(title="OMG Shopify → TShirtJunkies Order Service")

# Dedup: track recently processed order IDs to ignore Shopify webhook retries
_processed_orders: set[int] = set()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

QSTOMIZER_URL = f"{settings.tshirtjunkies_base_url}/apps/qstomizer/"
FRONT_DESIGN_IMAGE = STATIC_DIR / "front_design.png"


def _reject_confirm_page(
    proposal_id: str, token: str, post_path: str, thing_label: str
) -> str:
    """HTML interstitial shown on GET of a reject link.

    Requires the user to click a button that POSTs to confirm the rejection —
    stops email-scanner prefetchers from silently rejecting proposals.
    """
    return f"""
    <html><body style="font-family:sans-serif;max-width:560px;margin:40px auto;padding:20px;text-align:center;">
        <h1 style="color:#111;">Reject this {thing_label}?</h1>
        <p style="color:#6b7280;">Click the button below to confirm. If you arrived here by mistake you can safely close this tab.</p>
        <form method="POST" action="{post_path}" style="margin-top:24px;">
            <input type="hidden" name="proposal_id" value="{proposal_id}">
            <input type="hidden" name="token" value="{token}">
            <button type="submit" style="padding:12px 28px;background:#dc2626;color:white;border:0;border-radius:6px;font-size:16px;font-weight:bold;cursor:pointer;">
                Yes, reject this {thing_label}
            </button>
        </form>
    </body></html>
    """


def _approve_confirm_page(
    proposal_id: str, token: str, post_path: str, thing_label: str,
    title: str = "", extras: dict | None = None,
) -> str:
    """HTML interstitial shown on GET of an approve link.

    Requires the user to click a button that POSTs to actually approve —
    stops email-scanner prefetchers (Outlook Safe Links, Gmail, etc.) from
    silently approving proposals and publishing products/posts without consent.
    """
    extras = extras or {}
    extra_inputs = "".join(
        f'<input type="hidden" name="{k}" value="{v}">' for k, v in extras.items()
    )
    title_block = f'<p style="color:#374151;"><strong>{title}</strong></p>' if title else ""
    return f"""
    <html><body style="font-family:sans-serif;max-width:560px;margin:40px auto;padding:20px;text-align:center;">
        <h1 style="color:#111;">Approve this {thing_label}?</h1>
        {title_block}
        <p style="color:#6b7280;">Click the button below to confirm. If you arrived here by mistake you can safely close this tab.</p>
        <form method="POST" action="{post_path}" style="margin-top:24px;">
            <input type="hidden" name="proposal_id" value="{proposal_id}">
            <input type="hidden" name="token" value="{token}">
            {extra_inputs}
            <button type="submit" style="padding:12px 28px;background:#059669;color:white;border:0;border-radius:6px;font-size:16px;font-weight:bold;cursor:pointer;">
                Yes, approve this {thing_label}
            </button>
        </form>
    </body></html>
    """


# Approximate Qstomizer fabric colors as RGB — used to flatten transparent
# design PNGs onto the matching tee backdrop before vision comparison, so
# light-text-on-transparent designs (e.g. white text on a Black tee) don't
# render as blank to Claude.
_TEE_COLOR_RGB: dict[str, tuple[int, int, int]] = {
    "White": (255, 255, 255),
    "Black": (0, 0, 0),
    "Navy Blue": (30, 50, 90),
    "Red": (200, 30, 30),
    "Royal Blue": (40, 70, 150),
    "Sport Grey": (160, 160, 160),
}


def _flatten_design_for_vision(design_path: Path, tee_color: str) -> tuple[bytes, str]:
    """Return (image_bytes, mime) of the design ready for Claude vision.

    If the source PNG has an alpha channel, it's composited onto a solid
    backdrop matching the tee color so light-on-transparent artwork is
    visible. Designs without alpha are passed through unchanged.
    """
    raw = design_path.read_bytes()
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return raw, "image/png" if raw.startswith(b"\x89PNG") else "image/jpeg"
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(raw))
        if img.mode not in ("RGBA", "LA") and "A" not in img.mode:
            return raw, "image/png"
        rgb = _TEE_COLOR_RGB.get(tee_color, _TEE_COLOR_RGB["White"])
        backdrop = Image.new("RGB", img.size, rgb)
        backdrop.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[3])
        out = io.BytesIO()
        backdrop.save(out, format="PNG")
        return out.getvalue(), "image/png"
    except Exception:
        return raw, "image/png"


async def verify_mockup_matches_design(
    mockup_url: str, design_path: Path, tee_color: str = "White",
) -> dict:
    """Download the Qstomizer mockup and compare it to our uploaded design using Claude vision.

    Catches Qstomizer _customorderid collisions where the mockup shows a completely
    different design than what we uploaded (e.g., another customer's custom order).

    `tee_color` is the Qstomizer fabric color the mockup was rendered on; it's
    used to composite transparent design PNGs onto a matching backdrop before
    sending to Claude (otherwise white-on-transparent looks blank to vision).

    Returns {"match": True/False, "details": "..."}.
    """
    import base64
    import mimetypes
    try:
        from app.agents import llm_client

        def _guess_media_type(path_or_url: str, content: bytes) -> str:
            """Guess image media type from extension or magic bytes."""
            mt, _ = mimetypes.guess_type(path_or_url)
            if mt and mt.startswith("image/"):
                return mt
            # Fallback: check magic bytes
            if content[:3] == b"\xff\xd8\xff":
                return "image/jpeg"
            if content[:8] == b"\x89PNG\r\n\x1a\n":
                return "image/png"
            return "image/png"  # safe default

        # Download the mockup image
        async with httpx.AsyncClient() as client:
            resp = await client.get(mockup_url, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            mockup_bytes = resp.content
            mockup_b64 = base64.b64encode(mockup_bytes).decode("utf-8")
            mockup_mime = _guess_media_type(mockup_url, mockup_bytes)

        design_bytes, design_mime = _flatten_design_for_vision(design_path, tee_color)
        design_b64 = base64.b64encode(design_bytes).decode("utf-8")

        api_client = llm_client._get_client()
        response = await llm_client._create_with_retry(
            api_client,
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            temperature=0,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        f"Image 1 is our original design artwork (rendered on a {tee_color} backdrop matching the tee fabric). "
                        f"Image 2 is a t-shirt mockup of a {tee_color} tee that should show the same design printed on it. "
                        "Do they show the SAME design? Focus on whether the core artwork, "
                        "graphic elements, and text CONTENT are identical. "
                        "IGNORE these normal mockup differences: smaller scale, different positioning "
                        "on the shirt, slight detail loss at reduced size, cropping of edges, "
                        "color saturation shifts from printing. "
                        "Flag as NOT matching ONLY if: completely different artwork, wrong/garbled text, "
                        "a different design entirely, or major missing graphic elements. "
                        "Respond in JSON: {\"match\": true/false, \"details\": \"brief explanation\"}"
                    )},
                    {"type": "image", "source": {"type": "base64", "media_type": design_mime, "data": design_b64}},
                    {"type": "image", "source": {"type": "base64", "media_type": mockup_mime, "data": mockup_b64}},
                ],
            }],
        )

        import json
        text = response.content[0].text
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        result = json.loads(text.strip())
        logger.info(f"Mockup verification: match={result.get('match')}, details={result.get('details', '')}")
        return result
    except Exception as e:
        logger.warning(f"Mockup verification failed (non-blocking): {e}")
        return {"match": True, "details": f"verification error: {e}"}


@app.get("/tj-checkout/{token}", response_class=HTMLResponse)
async def tj_checkout_redirect(token: str):
    """Redirect Vangelis to a TJ checkout with the cart properly rebuilt.

    Replaces the broken /cart/VID:QTY?attributes[…] permalink. Shopify's
    permalink format only carries cart-level attributes, so Qstomizer's line
    item properties (_customimagefront, _customimageback, _customorderid)
    were getting stripped on rebuild — TJ then printed the wrong/no image.

    The flow:
      1. Look up the saved cart payload (variant, qty, line item properties,
         shipping) by token.
      2. Return a tiny HTML page that:
         a. clears any existing TJ cart via fetch (storefront /cart/clear.js
            allows CORS), then
         b. auto-POSTs a form to TJ's /cart/add — `properties[key]` survives
            this path — and uses return_to=/checkout?... to land on a
            shipping-prefilled checkout.
    """
    from html import escape
    from urllib.parse import urlencode
    from app.tj_checkout import get_session

    session = get_session(token)
    if not session:
        return HTMLResponse("<h1>Checkout link expired or invalid</h1>", status_code=404)

    items = session.get("items", [])
    shipping = session.get("shipping", {}) or {}
    if not items:
        return HTMLResponse("<h1>Empty cart — nothing to check out</h1>", status_code=400)

    # Build the checkout-prefill query string for return_to
    prefill_map = {
        "email": "checkout[email]",
        "first_name": "checkout[shipping_address][first_name]",
        "last_name": "checkout[shipping_address][last_name]",
        "address1": "checkout[shipping_address][address1]",
        "address2": "checkout[shipping_address][address2]",
        "city": "checkout[shipping_address][city]",
        "zip": "checkout[shipping_address][zip]",
        "country_code": "checkout[shipping_address][country]",
        "phone": "checkout[shipping_address][phone]",
    }
    checkout_params = {
        param: shipping[key]
        for key, param in prefill_map.items()
        if shipping.get(key)
    }
    return_to = "/checkout"
    if checkout_params:
        return_to += "?" + urlencode(checkout_params)

    # The first item carries return_to (TJ redirects after /cart/add).
    # Subsequent items would each cause their own redirect — to add multiple
    # items in one shot, we use the /cart/add.js fetch chain in JS instead of
    # a sequence of form posts. Single item is the common case.
    first = items[0]
    rest = items[1:]

    def _hidden(name: str, value: str) -> str:
        return f'<input type="hidden" name="{escape(name)}" value="{escape(value)}">'

    first_inputs = [
        _hidden("id", str(first["variant_id"])),
        _hidden("quantity", str(first["quantity"])),
        _hidden("return_to", return_to),
    ]
    for k, v in first.get("properties", {}).items():
        first_inputs.append(_hidden(f"properties[{k}]", v))

    # Extra items get added via fetch BEFORE the form submit (so they end up
    # in the same cart). Build their JSON payloads here.
    import json as _json
    extras_json = _json.dumps([
        {
            "id": str(it["variant_id"]),
            "quantity": it["quantity"],
            "properties": it.get("properties", {}),
        }
        for it in rest
    ])

    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Opening TJ checkout…</title>
<style>body{{font-family:sans-serif;text-align:center;padding:60px;color:#374151;}}</style>
</head><body>
<h2>Opening TJ checkout…</h2>
<p>Clearing any leftover cart, then taking you to checkout with the correct design attached.</p>
<form id="tjf" method="POST" action="https://tshirtjunkies.co/cart/add" accept-charset="UTF-8">
{"".join(first_inputs)}
</form>
<script>
(async () => {{
  try {{
    await fetch('https://tshirtjunkies.co/cart/clear.js', {{
      method: 'POST', credentials: 'include',
    }});
  }} catch (e) {{ /* non-fatal — proceed anyway */ }}

  const extras = {extras_json};
  for (const item of extras) {{
    try {{
      await fetch('https://tshirtjunkies.co/cart/add.js', {{
        method: 'POST',
        credentials: 'include',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(item),
      }});
    }} catch (e) {{ /* non-fatal */ }}
  }}

  document.getElementById('tjf').submit();
}})();
</script>
</body></html>""")


@app.post("/map-products")
async def map_products(source_url: str, target_url: str) -> ProductMapping:
    """Map a product from your store to a tshirtjunkies product by providing both URLs."""
    from app.mapper import create_mapping_from_urls
    return await create_mapping_from_urls(source_url, target_url)


@app.get("/mappings")
async def get_mappings() -> list[ProductMapping]:
    """List all product mappings."""
    return load_mappings().mappings


TEST_WEBHOOK_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Test Webhook</title>
    <style>
        body { font-family: sans-serif; max-width: 700px; margin: 40px auto; padding: 0 20px; }
        h1 { font-size: 22px; }
        label { font-size: 14px; font-weight: bold; display: block; margin-top: 12px; }
        select, input { padding: 8px 12px; border: 2px solid #e5e7eb; border-radius: 8px;
                       font-size: 14px; margin: 4px 0 8px; }
        button { margin-top: 16px; padding: 12px 32px; background: #9333ea; color: white;
                 border: none; border-radius: 8px; font-size: 16px; cursor: pointer; }
        button:hover { background: #7e22ce; }
        .hint { color: #6b7280; font-size: 13px; }
        #status { margin-top: 20px; padding: 16px; border-radius: 8px; display: none; }
        .success { background: #f0fdf4; border: 1px solid #bbf7d0; color: #166534; }
        .error { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; }
        .loading { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af; }
    </style>
</head>
<body>
    <h1>Test Webhook (Fake Order)</h1>
    <p>Send a fake <code>orders/create</code> webhook to test the full automation flow
       without placing a real order.</p>
    <form id="form">
        <label>Product:</label>
        <select id="product_key" style="width:100%;max-width:480px;"></select>
        <label>Size:</label>
        <select id="size"></select>
        <label>Quantity:</label>
        <input type="number" id="qty" value="1" min="1" max="10" style="width:80px;">
        <label>Customer Name:</label>
        <input type="text" id="customer_name" value="Test Customer" style="width:300px;">
        <label>Country:</label>
        <select id="country">
            <option value="CY" data-city="Nicosia" data-zip="1000" data-addr="123 Test Street" data-phone="+35799000000" selected>Cyprus</option>
            <option value="GR" data-city="Athens" data-zip="10563" data-addr="10 Ermou St" data-phone="+306900000000">Greece</option>
            <option value="FR" data-city="Paris" data-zip="75001" data-addr="10 Rue de Rivoli" data-phone="+33600000000">France</option>
        </select>
        <br>
        <button type="submit">Send Test Webhook</button>
    </form>
    <p class="hint">This posts a fake order to <code>/webhook/order-created</code> using real variant IDs
       from your mappings. Playwright will run and you'll get an email.
       <br>Shipping method will be auto-selected: CY=Home Delivery, GR=Geniki Taxydromiki, FR=Postal.</p>
    <div id="status"></div>
    <script>
        const PRODUCTS = %PRODUCTS%;
        const productSel = document.getElementById('product_key');
        const sizeSel = document.getElementById('size');
        function rebuildProductOptions() {
            productSel.innerHTML = PRODUCTS.map(p =>
                '<option value="' + p.key + '">' + p.label + '</option>').join('');
        }
        function rebuildSizeOptions() {
            const p = PRODUCTS.find(x => x.key === productSel.value);
            const sizes = p ? Object.keys(p.sizes) : [];
            sizeSel.innerHTML = sizes.map(s => '<option>' + s + '</option>').join('');
        }
        rebuildProductOptions();
        rebuildSizeOptions();
        productSel.addEventListener('change', rebuildSizeOptions);
        document.getElementById('form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const status = document.getElementById('status');
            const product = PRODUCTS.find(p => p.key === productSel.value);
            const size = sizeSel.value;
            const qty = parseInt(document.getElementById('qty').value);
            const name = document.getElementById('customer_name').value.split(' ');
            const variant_id = product && product.sizes[size];
            if (!variant_id) { alert('No mapping for ' + product.label + ' ' + size); return; }
            status.style.display = 'block';
            status.className = 'loading';
            status.textContent = 'Sending test webhook...';
            const countryEl = document.getElementById('country');
            const opt = countryEl.options[countryEl.selectedIndex];
            const country_code = countryEl.value;
            const order = {
                id: Date.now(), order_number: 'TEST-' + Date.now(),
                line_items: [{
                    variant_id: variant_id, quantity: qty,
                    title: product.title,
                    variant_title: size,
                }],
                customer: { first_name: name[0] || 'Test', last_name: name.slice(1).join(' ') || 'Customer' },
                shipping_address: {
                    first_name: name[0] || 'Test', last_name: name.slice(1).join(' ') || 'Customer',
                    address1: opt.dataset.addr, city: opt.dataset.city,
                    country_code: country_code, zip: opt.dataset.zip,
                    phone: opt.dataset.phone,
                },
                total_price: '30.00', currency: 'EUR',
            };
            try {
                const res = await fetch('/webhook/order-created', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(order),
                });
                const data = await res.json();
                if (data.items_mapped && data.items_mapped.length > 0) {
                    status.className = 'success';
                    status.innerHTML = '<strong>Sent!</strong> Order #' + data.order_number +
                        '<br>Mapped: ' + data.items_mapped.map(i => i.title + ' (' + i.variant_title + ')').join(', ') +
                        '<br><em>Playwright is running in the background. Check your email.</em>';
                } else {
                    status.className = 'error';
                    status.textContent = 'No items mapped: ' + JSON.stringify(data.items_skipped);
                }
            } catch (err) {
                status.className = 'error';
                status.textContent = 'Failed: ' + err.message;
            }
        });
    </script>
</body>
</html>
"""


@app.get("/test-webhook", response_class=HTMLResponse)
async def test_webhook_form():
    """Serve a form to send a fake webhook for testing.

    Builds a per-mapping list keyed by (source_handle, target_product_id)
    so the form can pick a SPECIFIC product/gender combo. The previous
    (gender, size)-only key collapsed every product onto the same slot
    (last-write-wins), which made the form silently submit a different
    product's variant_id than the one the user thought they picked.
    """
    import json

    config = load_mappings()
    products = []  # [{key, label, gender, title, handle, sizes: {size: src_id}}]
    for m in config.mappings:
        gender = "female" if "female" in m.source_handle else "male"
        sizes = {v.source_title: v.source_variant_id for v in m.variants}
        if not sizes:
            continue
        # Use a unique key (handle + product_id) — some handles map to
        # both a male and a female TJ product, distinguished by target.
        key = f"{m.source_handle}__{m.target_product_id}"
        label = f"{m.source_handle} ({gender})"
        products.append({
            "key": key,
            "label": label,
            "gender": gender,
            "title": m.source_handle.replace("-", " ").title(),
            "handle": m.source_handle,
            "sizes": sizes,
        })
    products.sort(key=lambda p: p["label"])

    return TEST_WEBHOOK_HTML.replace("%PRODUCTS%", json.dumps(products))


@app.middleware("http")
async def log_all_requests(request: Request, call_next):
    print(f">>> {request.method} {request.url.path}", flush=True)
    response = await call_next(request)
    print(f"<<< {response.status_code}", flush=True)
    return response


@app.post("/webhook/order-created")
async def handle_order_created(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Handle Shopify order/created webhook.

    Maps items to tshirtjunkies variants, kicks off Playwright automation
    in the background to customize and add to cart, then sends an email
    notification with order details and cart links.
    """
    order = await request.json()
    order_id = order.get("id")

    # Shopify often sends duplicate webhooks (retries). Skip if already processing.
    if order_id and order_id in _processed_orders:
        logger.info(f"Duplicate webhook for order #{order.get('order_number', order_id)}, skipping")
        return {"status": "duplicate", "order_id": order_id}
    if order_id:
        _processed_orders.add(order_id)

    config = load_mappings()

    # Build lookups from source variant ID
    variant_map: dict[int, int] = {}  # source_variant_id -> target_variant_id
    product_id_map: dict[int, int] = {}  # source_variant_id -> target_product_id
    handle_map: dict[int, str] = {}  # source_variant_id -> source_handle
    design_map: dict[int, str] = {}  # source_variant_id -> design image filename
    color_map: dict[int, str] = {}  # source_variant_id -> Qstomizer tee color
    for mapping in config.mappings:
        for v in mapping.variants:
            variant_map[v.source_variant_id] = v.target_variant_id
            product_id_map[v.source_variant_id] = mapping.target_product_id
            handle_map[v.source_variant_id] = mapping.source_handle
            design_map[v.source_variant_id] = getattr(mapping, "design_image", "front_design.png")
            color_map[v.source_variant_id] = getattr(mapping, "color", "White")

    items_mapped = []
    items_skipped = []

    for line_item in order.get("line_items", []):
        source_variant_id = line_item.get("variant_id")
        quantity = line_item.get("quantity", 1)

        target_variant_id = variant_map.get(source_variant_id)
        target_product_id = product_id_map.get(source_variant_id)

        if target_variant_id and target_product_id:
            qstomizer_url = (
                f"{QSTOMIZER_URL}?qstomizer-product-id={target_product_id}"
            )
            items_mapped.append({
                "source_variant_id": source_variant_id,
                "target_variant_id": target_variant_id,
                "target_product_id": target_product_id,
                "quantity": quantity,
                "title": line_item.get("title", ""),
                "variant_title": line_item.get("variant_title", ""),
                "qstomizer_url": qstomizer_url,
                "design_image": design_map.get(source_variant_id, "front_design.png"),
                "front_design_url": f"/static/{design_map.get(source_variant_id, 'front_design.png')}",
                "color": color_map.get(source_variant_id, "White"),
            })
        else:
            items_skipped.append({
                "source_variant_id": source_variant_id,
                "title": line_item.get("title", ""),
                "reason": "no mapping found",
            })

    # Kick off Playwright automation + email in background
    if items_mapped:
        background_tasks.add_task(
            _process_order_background,
            order=order,
            items_mapped=items_mapped,
            handle_map=handle_map,
        )

    return {
        "status": "ok",
        "order_id": order.get("id"),
        "order_number": order.get("order_number"),
        "items_mapped": items_mapped,
        "items_skipped": items_skipped,
    }


async def _process_order_background(
    order: dict,
    items_mapped: list[dict],
    handle_map: dict[int, str],
) -> None:
    """Run Playwright automation for each item, then send email notification."""
    order_number = order.get("order_number", order.get("id", "N/A"))
    logger.info(f"Background processing started for order #{order_number}")

    for item in items_mapped:
        source_handle = handle_map.get(item["source_variant_id"], "")
        variant_title = item["variant_title"]

        # Parse variant title. Supported formats:
        #   "Male / Front / L"  (new 3-option schema: Gender / Placement / Size)
        #   "Male / L"          (legacy 2-option schema: Gender / Size)
        #   "L"                 (oldest legacy: Size only)
        placement = "front"
        if " / " in variant_title:
            parts = [p.strip() for p in variant_title.split(" / ")]
            if len(parts) >= 3:
                gender_str, placement_str, size = parts[0], parts[1], parts[2]
                placement = "back" if placement_str.lower() == "back" else "front"
            else:
                gender_str, size = parts[0], parts[1]
            product_type = "female" if "female" in gender_str.lower() else "male"
        else:
            product_type = "female" if "female" in source_handle else "male"
            size = variant_title

        # Extract shipping details from order
        shipping_address = order.get("shipping_address") or {}
        customer = order.get("customer", {})
        # Extract the shipping method the customer chose on OMG, so we can
        # pick the matching method on TJ (some countries have >1 option).
        shipping_lines = order.get("shipping_lines") or []
        omg_shipping_method = (shipping_lines[0].get("title", "") if shipping_lines else "")

        shipping = {
            "email": settings.email_sender,
            "first_name": shipping_address.get("first_name", ""),
            "last_name": f"{shipping_address.get('last_name', '')} (OMG #{order_number})",
            "address1": shipping_address.get("address1", ""),
            "address2": shipping_address.get("address2", ""),
            "city": shipping_address.get("city", ""),
            "country_code": shipping_address.get("country_code", ""),
            "zip": shipping_address.get("zip", ""),
            "phone": shipping_address.get("phone", ""),
            "shipping_method": omg_shipping_method,
        }

        try:
            design_file = STATIC_DIR / item.get("design_image", "front_design.png")
            if not design_file.exists():
                design_file = FRONT_DESIGN_IMAGE  # fallback to default

            from app.qstomizer_offsets import get_offsets
            v_off, h_off, pad = get_offsets(
                source_handle, product_type, placement,
                design_path=str(design_file),
            )
            result = await customize_and_add_to_cart(
                product_type=product_type,
                size=size,
                color=item.get("color", "White"),
                image_path=str(design_file),
                quantity=item["quantity"],
                headless=True,
                shipping=shipping,
                placement=placement,
                vertical_offset=v_off,
                horizontal_offset=h_off,
                vertical_safety_pad_px=pad,
            )
            item["cart_url"] = result["checkout_url"]
            item["mockup_url"] = result.get("mockup_url")
            logger.info(f"  {item['title']} ({size}) → {item['cart_url']}")
            if item.get("mockup_url"):
                logger.info(f"  Mockup: {item['mockup_url']}")
                # Verify mockup matches our uploaded design (catches Qstomizer ID collisions)
                verification = await verify_mockup_matches_design(item["mockup_url"], design_file)
                if not verification.get("match", True):
                    item["mockup_mismatch"] = verification.get("details", "Design mismatch detected")
                    logger.warning(
                        f"  MOCKUP MISMATCH for {item['title']}: {item['mockup_mismatch']}"
                    )
        except Exception as e:
            logger.error(f"  Playwright failed for {item['title']} ({size}): {e}")
            item["cart_url"] = None
            item["error"] = str(e)

    # Send email notification
    customer = order.get("customer", {})
    customer_name = (
        f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        or "Unknown"
    )
    order_total = order.get("total_price", "N/A")
    currency = order.get("currency", "EUR")

    await send_order_notification(
        order_number=order_number,
        customer_name=customer_name,
        order_total=order_total,
        currency=currency,
        items=items_mapped,
        shipping=shipping,
    )

    logger.info(f"Background processing complete for order #{order_number}")


MANUAL_ORDER_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Manual Order — OMG → TShirtJunkies</title>
    <style>
        body { font-family: sans-serif; max-width: 700px; margin: 40px auto; padding: 0 20px; }
        h1 { font-size: 22px; }
        textarea { width: 100%; height: 300px; font-family: monospace; font-size: 14px;
                   padding: 12px; border: 2px solid #e5e7eb; border-radius: 8px; resize: vertical; }
        textarea:focus { outline: none; border-color: #2563eb; }
        button { margin-top: 12px; padding: 12px 32px; background: #2563eb; color: white;
                 border: none; border-radius: 8px; font-size: 16px; cursor: pointer; }
        button:hover { background: #1d4ed8; }
        .hint { color: #6b7280; font-size: 13px; margin-top: 8px; }
        #status { margin-top: 20px; padding: 16px; border-radius: 8px; display: none; }
        .success { background: #f0fdf4; border: 1px solid #bbf7d0; color: #166534; }
        .error { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; }
        .loading { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af; }
    </style>
</head>
<body>
    <h1>Manual Order — OMG → TShirtJunkies</h1>
    <p>Paste the OMG order confirmation email text below and hit Submit.
       The system will parse it, run the Playwright automation, and send you an email.</p>
    <form id="form">
        <textarea id="email_text" name="email_text" placeholder="Paste the OMG order email here...

Order summary

Astous na Laloun Graphic Tee Male — EU Edition × 1
M
€30,00
...
Shipping address
Name
Address
Zip City
Country"></textarea>
        <br>
        <label for="order_number" style="font-size:14px;font-weight:bold;">OMG Order # (optional):</label><br>
        <input type="text" id="order_number" name="order_number" placeholder="e.g. 1001"
               style="padding:8px 12px;border:2px solid #e5e7eb;border-radius:8px;font-size:14px;margin:4px 0 12px;">
        <br>
        <button type="submit">Submit Order</button>
    </form>
    <p class="hint">The automation runs in the background. You'll receive an email when it's done.</p>
    <div id="status"></div>

    <script>
        document.getElementById('form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const status = document.getElementById('status');
            const text = document.getElementById('email_text').value;
            if (!text.trim()) return;

            status.style.display = 'block';
            status.className = 'loading';
            status.textContent = 'Processing... parsing email and starting automation.';

            try {
                const res = await fetch('/manual-order', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        email_text: text,
                        order_number: document.getElementById('order_number').value,
                    }),
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    status.className = 'success';
                    const items = data.items.map(i => `${i.title} (${i.variant_title}) x${i.quantity}`).join(', ');
                    status.innerHTML = `<strong>Queued!</strong> ${items}<br>
                        Shipping to: ${data.shipping.first_name} ${data.shipping.last_name},
                        ${data.shipping.city}, ${data.shipping.country_code}<br>
                        <em>You'll get an email when it's done.</em>`;
                } else {
                    status.className = 'error';
                    status.textContent = 'Error: ' + (data.detail || JSON.stringify(data));
                }
            } catch (err) {
                status.className = 'error';
                status.textContent = 'Request failed: ' + err.message;
            }
        });
    </script>
</body>
</html>
"""


@app.get("/manual-order", response_class=HTMLResponse)
async def manual_order_form():
    """Serve the manual order form page."""
    return MANUAL_ORDER_HTML


@app.post("/manual-order")
async def manual_order_submit(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Parse pasted OMG order email and run the Playwright automation."""
    body = await request.json()
    email_text = body.get("email_text", "")
    order_number = body.get("order_number", "").strip() or "MANUAL"

    parsed = parse_order_email(email_text)
    items = parsed["items"]
    shipping = parsed["shipping"]

    if not items:
        return {"status": "error", "detail": "Could not parse any items from the email text."}

    # Determine Qstomizer URLs for each item
    qstomizer_product_ids = {
        "male": 9864408301915,
        "female": 8676301799771,
    }

    items_for_processing = []
    for item in items:
        product_type = item["product_type"]
        product_id = qstomizer_product_ids[product_type]
        item["qstomizer_url"] = f"{QSTOMIZER_URL}?qstomizer-product-id={product_id}"
        items_for_processing.append(item)

    # Add email sender for checkout and OMG order ref to last name
    shipping["email"] = settings.email_sender
    if shipping.get("last_name"):
        shipping["last_name"] = f"{shipping['last_name']} (OMG #{order_number})"

    background_tasks.add_task(
        _process_manual_order_background,
        items=items_for_processing,
        shipping=shipping,
        total=parsed["total"],
        order_number=order_number,
    )

    return {
        "status": "ok",
        "items": items,
        "shipping": shipping,
        "total": parsed["total"],
    }


async def _process_manual_order_background(
    items: list[dict],
    shipping: dict,
    total: str,
    order_number: str = "MANUAL",
) -> None:
    """Run Playwright automation for manually submitted order."""
    customer_name = f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip()
    logger.info(f"Manual order processing started for {customer_name}")

    for item in items:
        try:
            from app.qstomizer_offsets import get_offsets
            # Manual orders have no source handle, so defaults apply
            # (with graphic-vs-text auto-detection from the design PNG).
            v_off, h_off, pad = get_offsets(
                None, item["product_type"], "front",
                design_path=str(FRONT_DESIGN_IMAGE),
            )
            result = await customize_and_add_to_cart(
                product_type=item["product_type"],
                size=item["variant_title"],
                color="White",
                image_path=str(FRONT_DESIGN_IMAGE),
                quantity=item["quantity"],
                headless=True,
                shipping=shipping,
                vertical_offset=v_off,
                horizontal_offset=h_off,
                vertical_safety_pad_px=pad,
            )
            item["cart_url"] = result["checkout_url"]
            item["mockup_url"] = result.get("mockup_url")
            logger.info(f"  {item['title']} ({item['variant_title']}) → {item['cart_url']}")
            if item.get("mockup_url"):
                logger.info(f"  Mockup: {item['mockup_url']}")
        except Exception as e:
            logger.error(f"  Playwright failed for {item['title']}: {e}")
            item["cart_url"] = None
            item["error"] = str(e)

    await send_order_notification(
        order_number=order_number,
        customer_name=customer_name,
        order_total=total,
        currency="",
        items=items,
        shipping=shipping,
    )

    logger.info(f"Manual order processing complete for {customer_name}")


FULFILL_ORDER_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Fulfill OMG Order</title>
    <style>
        body { font-family: sans-serif; max-width: 700px; margin: 40px auto; padding: 0 20px; }
        h1 { font-size: 22px; }
        textarea { width: 100%; height: 200px; font-family: monospace; font-size: 14px;
                   padding: 12px; border: 2px solid #e5e7eb; border-radius: 8px; resize: vertical; }
        textarea:focus { outline: none; border-color: #16a34a; }
        input[type=text] { padding: 8px 12px; border: 2px solid #e5e7eb; border-radius: 8px;
                          font-size: 14px; width: 300px; margin: 4px 0 12px; }
        input:focus { outline: none; border-color: #16a34a; }
        label { font-size: 14px; font-weight: bold; display: block; margin-top: 12px; }
        button { margin-top: 16px; padding: 12px 32px; background: #16a34a; color: white;
                 border: none; border-radius: 8px; font-size: 16px; cursor: pointer; }
        button:hover { background: #15803d; }
        .hint { color: #6b7280; font-size: 13px; margin-top: 4px; }
        #status { margin-top: 20px; padding: 16px; border-radius: 8px; display: none; }
        .success { background: #f0fdf4; border: 1px solid #bbf7d0; color: #166534; }
        .error { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; }
        .loading { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af; }
        .or-divider { margin: 20px 0; text-align: center; color: #9ca3af; font-size: 13px; }
    </style>
</head>
<body>
    <h1>Fulfill OMG Order</h1>
    <p>Paste the TShirtJunkies shipping/fulfillment email to auto-fulfill the matching OMG order,
       or fill in the fields manually.</p>

    <form id="form">
        <label>Paste TShirtJunkies fulfillment email:</label>
        <textarea id="email_text" placeholder="Paste the TShirtJunkies shipping email here...
It will auto-extract the OMG order number and tracking info."></textarea>
        <p class="hint">The email should contain the customer name with (OMG #1234) and tracking details.</p>

        <button type="button" onclick="parseEmail()">Parse Email</button>

        <div class="or-divider">— or fill in manually —</div>

        <label>OMG Order #:</label>
        <input type="text" id="order_number" placeholder="e.g. 1001">

        <label>Tracking Number:</label>
        <input type="text" id="tracking_number" placeholder="e.g. JD014600012345678901">

        <label>Tracking URL:</label>
        <input type="text" id="tracking_url" placeholder="e.g. https://track.dhl.com/..." style="width:100%;">

        <label>Carrier:</label>
        <input type="text" id="tracking_company" placeholder="e.g. DHL, Cyprus Post, ACS">

        <br>
        <button type="submit">Fulfill Order</button>
    </form>

    <div id="status"></div>

    <script>
        async function parseEmail() {
            const text = document.getElementById('email_text').value;
            if (!text.trim()) return;
            try {
                const res = await fetch('/fulfill-order/parse', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({email_text: text}),
                });
                const data = await res.json();
                if (data.omg_order_number) document.getElementById('order_number').value = data.omg_order_number;
                if (data.tracking_number) document.getElementById('tracking_number').value = data.tracking_number;
                if (data.tracking_url) document.getElementById('tracking_url').value = data.tracking_url;
                if (data.tracking_company) document.getElementById('tracking_company').value = data.tracking_company;

                const status = document.getElementById('status');
                status.style.display = 'block';
                status.className = 'success';
                status.textContent = 'Parsed! Review the fields below and click Fulfill Order.';
            } catch (err) {
                const status = document.getElementById('status');
                status.style.display = 'block';
                status.className = 'error';
                status.textContent = 'Parse failed: ' + err.message;
            }
        }

        document.getElementById('form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const status = document.getElementById('status');
            const order_number = document.getElementById('order_number').value.trim();
            if (!order_number) { alert('OMG Order # is required'); return; }

            status.style.display = 'block';
            status.className = 'loading';
            status.textContent = 'Fulfilling order #' + order_number + '...';

            try {
                const res = await fetch('/fulfill-order', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        order_number: order_number,
                        tracking_number: document.getElementById('tracking_number').value,
                        tracking_url: document.getElementById('tracking_url').value,
                        tracking_company: document.getElementById('tracking_company').value,
                    }),
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    status.className = 'success';
                    status.innerHTML = '<strong>Fulfilled!</strong> OMG Order #' + data.order_number +
                        (data.tracking_number ? ' — Tracking: ' + data.tracking_number : '') +
                        '<br>Customer will be notified by Shopify.';
                } else {
                    status.className = 'error';
                    status.textContent = 'Error: ' + (data.detail || JSON.stringify(data));
                }
            } catch (err) {
                status.className = 'error';
                status.textContent = 'Request failed: ' + err.message;
            }
        });
    </script>
</body>
</html>
"""


@app.get("/fulfill-order", response_class=HTMLResponse)
async def fulfill_order_form():
    """Serve the fulfill order form page."""
    return FULFILL_ORDER_HTML


@app.post("/fulfill-order/parse")
async def fulfill_order_parse(request: Request) -> dict:
    """Parse a TShirtJunkies fulfillment email to extract order number and tracking."""
    body = await request.json()
    return parse_fulfillment_email(body.get("email_text", ""))


@app.post("/fulfill-order")
async def fulfill_order_submit(request: Request) -> dict:
    """Fulfill an OMG order with tracking info.

    Catches Admin-API HTTP errors and returns them as JSON so the
    front-end form (which JSON.parses every response) gets a parseable
    error message instead of FastAPI's default 500 + HTML page.
    """
    body = await request.json()
    order_number = body.get("order_number", "").strip()
    if not order_number:
        return {"status": "error", "detail": "Order number is required"}

    try:
        return await fulfill_order(
            order_number=order_number,
            tracking_number=body.get("tracking_number", ""),
            tracking_url=body.get("tracking_url", ""),
            tracking_company=body.get("tracking_company", ""),
        )
    except httpx.HTTPStatusError as e:
        body_preview = e.response.text[:300] if e.response is not None else ""
        detail = (
            f"Shopify Admin API returned {e.response.status_code} for "
            f"{e.request.url}. {body_preview}"
        )
        if e.response is not None and e.response.status_code in (401, 403):
            detail += (
                " — token likely missing scopes. Re-authorize at "
                "/shopify-auth and update OMG_SHOPIFY_ADMIN_TOKEN."
            )
        logger.exception(f"fulfill_order failed: {detail}")
        return {"status": "error", "detail": detail}
    except Exception as e:
        logger.exception(f"fulfill_order unexpected error: {e}")
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}


# ─── AI Agent Endpoints ────────────────────────────────────────────────

@app.post("/agents/blog/generate")
async def blog_generate():
    """Manually trigger a new blog proposal."""
    from app.agents.blog_writer import generate_proposal
    proposal = await generate_proposal()
    return {
        "proposal_id": proposal["id"],
        "status": "pending",
        "title": proposal["data"].get("title", "Untitled"),
        "message": "Email sent with preview and approval links",
    }


@app.get("/agents/blog/proposals")
async def blog_proposals():
    """List all blog proposals."""
    from app.agents.approval import list_proposals
    return {"proposals": list_proposals(agent="blog")}


@app.get("/agents/blog/preview/{proposal_id}", response_class=HTMLResponse)
async def blog_preview(proposal_id: str):
    """View full blog post HTML."""
    from app.agents.approval import get_proposal
    proposal = get_proposal(proposal_id)
    if not proposal:
        return HTMLResponse("<h1>Proposal not found</h1>", status_code=404)
    data = proposal["data"]
    return HTMLResponse(f"""
    <html><head><title>{data.get('title', 'Preview')}</title>
    <style>body{{font-family:sans-serif;max-width:800px;margin:40px auto;padding:20px;}}
    img{{max-width:100%;}}</style></head>
    <body>
        <p style="color:#6b7280;font-size:13px;">Status: {proposal['status']} | Created: {proposal['created_at']}</p>
        <h1>{data.get('title', 'Untitled')}</h1>
        <p style="color:#6b7280;font-style:italic;">{data.get('meta_description', '')}</p>
        <hr>
        {data.get('body_html', '<p>No content</p>')}
        <hr>
        <p style="color:#6b7280;">Tags: {data.get('tags', '')} | Keywords: {', '.join(data.get('target_keywords', []))}</p>
    </body></html>
    """)


@app.get("/agents/blog/approve/{proposal_id}", response_class=HTMLResponse)
async def blog_approve(proposal_id: str, token: str = ""):
    """GET shows a confirmation page — real approval requires POST.

    This interstitial stops email-scanner prefetchers (Outlook Safe Links,
    Gmail, etc.) from silently publishing posts when they crawl email links.
    """
    from app.agents.approval import validate_token
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse(
            "<h1>Invalid or expired link</h1><p>This proposal may have already been processed.</p>",
            status_code=403,
        )
    return HTMLResponse(_approve_confirm_page(
        proposal_id, token, "/agents/blog/approve", "blog post",
        title=proposal["data"].get("title", ""),
    ))


@app.post("/agents/blog/approve", response_class=HTMLResponse)
async def blog_approve_confirm(proposal_id: str = Form(...), token: str = Form(...)):
    """Actually approve and publish the blog post (POST, so scanners can't trigger it)."""
    from app.agents.approval import claim_proposal, update_status
    proposal = claim_proposal(proposal_id, token)
    if not proposal:
        return HTMLResponse(
            "<h1>Invalid or expired link</h1><p>This proposal may have already been processed.</p>",
            status_code=403,
        )
    from app.agents.blog_writer import execute_approval
    try:
        article = await execute_approval(proposal_id)
        article_handle = article.get('handle', '')
        article_url = f"https://omg.com.cy/blogs/news/{article_handle}" if article_handle else "https://omg.com.cy/blogs"
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;text-align:center;">
            <h1 style="color:#059669;">Blog Post Published!</h1>
            <p><strong>{proposal['data'].get('title', '')}</strong></p>
            <p>Article ID: {article.get('id', '?')}</p>
            <a href="{article_url}" style="color:#2563eb;">View on store</a>
        </body></html>
        """)
    except Exception as e:
        update_status(proposal_id, "pending")
        return HTMLResponse(f"<h1>Error publishing</h1><p>{e}</p>", status_code=500)


@app.get("/agents/blog/reject/{proposal_id}", response_class=HTMLResponse)
async def blog_reject(proposal_id: str, token: str = ""):
    """GET shows a confirmation page — real rejection requires POST."""
    from app.agents.approval import validate_token
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse(
            "<h1>Invalid or expired link</h1>", status_code=403,
        )
    return HTMLResponse(_reject_confirm_page(
        proposal_id, token, "/agents/blog/reject", "blog post"
    ))


@app.post("/agents/blog/reject", response_class=HTMLResponse)
async def blog_reject_confirm(proposal_id: str = Form(...), token: str = Form(...)):
    """Actually reject the blog proposal (POST, so scanners can't trigger it)."""
    from app.agents.approval import validate_token, update_status
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse("<h1>Invalid or expired link</h1>", status_code=403)
    update_status(proposal_id, "rejected")
    return HTMLResponse("""
    <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;text-align:center;">
        <h1 style="color:#dc2626;">Blog Post Rejected</h1>
        <p>A new one will be generated on the next scheduled run.</p>
    </body></html>
    """)


@app.get("/agents/blog_link_fix/preview/{proposal_id}", response_class=HTMLResponse)
async def blog_link_fix_preview(proposal_id: str):
    """Side-by-side diff of the article body before and after link rewrites."""
    from html import escape as _esc
    from app.agents.approval import get_proposal
    proposal = get_proposal(proposal_id)
    if not proposal:
        return HTMLResponse("<h1>Proposal not found</h1>", status_code=404)
    data = proposal["data"]

    swap_rows = ""
    for s in data.get("swaps", []):
        mismatch = ' <span style="color:#dc2626;font-size:11px;">[anchor mismatch]</span>' if s.get("anchor_mismatch") else ""
        swap_rows += f"""
        <tr>
            <td style="padding:6px;color:#dc2626;text-decoration:line-through;font-family:monospace;font-size:13px;">{_esc(s['old_handle'])}</td>
            <td style="padding:6px;">→</td>
            <td style="padding:6px;color:#059669;font-family:monospace;font-size:13px;">{_esc(s['new_handle'])}</td>
            <td style="padding:6px;font-size:12px;">{_esc(s['confidence']).upper()}{mismatch}</td>
            <td style="padding:6px;font-size:12px;color:#6b7280;">"{_esc(s['anchor'])}" — {_esc(s.get('reason', ''))}</td>
        </tr>"""

    manual_rows = ""
    for mu in data.get("manual", []):
        manual_rows += f"""
        <tr>
            <td style="padding:6px;color:#dc2626;font-family:monospace;font-size:13px;" colspan="2">{_esc(mu['old_handle'])}</td>
            <td style="padding:6px;font-size:12px;color:#6b7280;" colspan="3">"{_esc(mu['anchor'])}" — needs manual pick ({_esc(mu.get('reason', ''))})</td>
        </tr>"""

    old_body = data.get("old_body_html", "")
    new_body = data.get("new_body_html", "")
    return HTMLResponse(f"""
    <html><head><title>Link diff: {_esc(data.get('article_title', ''))}</title>
    <style>
        body{{font-family:sans-serif;max-width:1100px;margin:20px auto;padding:20px;}}
        .col{{width:48%;display:inline-block;vertical-align:top;border:1px solid #e5e7eb;border-radius:6px;padding:12px;background:#fafafa;}}
        .col h3{{margin-top:0;}}
        .col-old{{margin-right:1%;}}
        .col-new{{margin-left:1%;}}
        table{{width:100%;border-collapse:collapse;margin-bottom:16px;}}
        th{{background:#f3f4f6;padding:6px;text-align:left;font-size:12px;}}
    </style></head>
    <body>
        <p style="color:#6b7280;font-size:13px;">Status: {proposal['status']} | Created: {proposal['created_at']}</p>
        <h1>{_esc(data.get('article_title', 'Untitled'))}</h1>
        <p style="color:#6b7280;">/blogs/news/{_esc(data.get('article_handle', ''))}</p>
        <h2>Proposed swaps</h2>
        <table>
            <thead><tr><th>Old</th><th></th><th>New</th><th>Confidence</th><th>Notes</th></tr></thead>
            <tbody>{swap_rows}{manual_rows}</tbody>
        </table>
        <h2>Body diff</h2>
        <div class="col col-old"><h3 style="color:#dc2626;">Before</h3>{old_body}</div><div class="col col-new"><h3 style="color:#059669;">After</h3>{new_body}</div>
    </body></html>
    """)


@app.get("/agents/blog_link_fix/approve/{proposal_id}", response_class=HTMLResponse)
async def blog_link_fix_approve(proposal_id: str, token: str = ""):
    from app.agents.approval import validate_token
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse(
            "<h1>Invalid or expired link</h1><p>This proposal may have already been processed.</p>",
            status_code=403,
        )
    return HTMLResponse(_approve_confirm_page(
        proposal_id, token, "/agents/blog_link_fix/approve", "link fix",
        title=proposal["data"].get("article_title", ""),
    ))


@app.post("/agents/blog_link_fix/approve", response_class=HTMLResponse)
async def blog_link_fix_approve_confirm(proposal_id: str = Form(...), token: str = Form(...)):
    from app.agents.approval import claim_proposal, update_status
    proposal = claim_proposal(proposal_id, token)
    if not proposal:
        return HTMLResponse(
            "<h1>Invalid or expired link</h1><p>This proposal may have already been processed.</p>",
            status_code=403,
        )
    from app.agents.blog_link_qa import execute_blog_link_fix
    try:
        article = await execute_blog_link_fix(proposal_id)
        article_handle = article.get("handle", "")
        article_url = f"https://omg.com.cy/blogs/news/{article_handle}" if article_handle else "https://omg.com.cy/blogs"
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;text-align:center;">
            <h1 style="color:#059669;">Links Fixed!</h1>
            <p><strong>{proposal['data'].get('article_title', '')}</strong></p>
            <p>{len(proposal['data'].get('swaps', []))} link(s) updated.</p>
            <a href="{article_url}" style="color:#2563eb;">View on store</a>
        </body></html>
        """)
    except Exception as e:
        update_status(proposal_id, "pending")
        return HTMLResponse(f"<h1>Error applying fix</h1><p>{e}</p>", status_code=500)


@app.get("/agents/blog_link_fix/reject/{proposal_id}", response_class=HTMLResponse)
async def blog_link_fix_reject(proposal_id: str, token: str = ""):
    from app.agents.approval import validate_token
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse("<h1>Invalid or expired link</h1>", status_code=403)
    return HTMLResponse(_reject_confirm_page(
        proposal_id, token, "/agents/blog_link_fix/reject", "link fix"
    ))


@app.post("/agents/blog_link_fix/reject", response_class=HTMLResponse)
async def blog_link_fix_reject_confirm(proposal_id: str = Form(...), token: str = Form(...)):
    from app.agents.approval import validate_token, update_status
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse("<h1>Invalid or expired link</h1>", status_code=403)
    update_status(proposal_id, "rejected")
    return HTMLResponse("""
    <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;text-align:center;">
        <h1 style="color:#dc2626;">Link Fix Rejected</h1>
        <p>The article was left unchanged.</p>
    </body></html>
    """)


@app.post("/agents/design/research")
async def design_research():
    """Trigger trend research and design generation."""
    from app.agents.design_creator import research_trends
    proposals = await research_trends()
    return {
        "proposals": [
            {
                "proposal_id": p["id"],
                "status": "pending",
                "concept": p["data"].get("name", "Untitled"),
                "style": p["data"].get("style", "?"),
                "image_url": f"/static/proposals/{p['data'].get('image_filename', '')}" if p["data"].get("image_filename") else None,
            }
            for p in proposals
        ],
        "message": f"{len(proposals)} designs generated. Email sent for review.",
    }


@app.get("/agents/design/proposals")
async def design_proposals():
    """List all design proposals."""
    from app.agents.approval import list_proposals
    return {"proposals": list_proposals(agent="design")}


@app.get("/agents/design/preview/{proposal_id}", response_class=HTMLResponse)
async def design_preview(proposal_id: str):
    """View design image and details."""
    from app.agents.approval import get_proposal
    proposal = get_proposal(proposal_id)
    if not proposal:
        return HTMLResponse("<h1>Proposal not found</h1>", status_code=404)
    data = proposal["data"]
    image_html = ""
    if data.get("image_filename"):
        image_html = f'<img src="/static/proposals/{data["image_filename"]}" style="max-width:500px;">'
    return HTMLResponse(f"""
    <html><head><title>Design: {data.get('name', 'Preview')}</title>
    <style>body{{font-family:sans-serif;max-width:800px;margin:40px auto;padding:20px;}}</style></head>
    <body>
        <p style="color:#6b7280;">Status: {proposal['status']} | Created: {proposal['created_at']}</p>
        <h1>{data.get('name', 'Untitled')}</h1>
        {image_html}
        <table style="margin-top:16px;">
            <tr><td style="color:#6b7280;padding:4px 8px;">Style:</td><td>{data.get('style', '?')}</td></tr>
            <tr><td style="color:#6b7280;padding:4px 8px;">Text:</td><td>{data.get('text_on_shirt', 'None')}</td></tr>
            <tr><td style="color:#6b7280;padding:4px 8px;">Type:</td><td>{data.get('product_type', '?')}</td></tr>
            <tr><td style="color:#6b7280;padding:4px 8px;">Title:</td><td>{data.get('suggested_title', '?')}</td></tr>
            <tr><td style="color:#6b7280;padding:4px 8px;">Tags:</td><td>{data.get('suggested_tags', '?')}</td></tr>
            <tr><td style="color:#6b7280;padding:4px 8px;">Reasoning:</td><td>{data.get('reasoning', '?')}</td></tr>
        </table>
    </body></html>
    """)


# Module-level set keeps strong references to running background approval tasks
# so asyncio doesn't garbage-collect them mid-execution. Tasks remove themselves
# via add_done_callback(set.discard) once they finish.
_background_approval_tasks: set = set()


@app.get("/agents/design/approve/{proposal_id}", response_class=HTMLResponse)
async def design_approve(proposal_id: str, token: str = "", version: str = "original"):
    """GET shows a confirmation page — real approval requires POST.

    This interstitial stops email-scanner prefetchers (Outlook Safe Links,
    Gmail, etc.) from silently approving and publishing products when they
    crawl email links. See commit history for the incident that prompted this.
    """
    from app.agents.approval import validate_token
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse(
            "<h1>Invalid or expired link</h1><p>This proposal may have already been processed.</p>",
            status_code=403,
        )
    title = proposal["data"].get("suggested_title") or proposal["data"].get("name", "")
    version_label = "Transparent (no bg)" if version == "nobg" else "Original (with bg)"
    return HTMLResponse(_approve_confirm_page(
        proposal_id, token, "/agents/design/approve", "design",
        title=f"{title} — {version_label}",
        extras={"version": version},
    ))


@app.post("/agents/design/approve", response_class=HTMLResponse)
async def design_approve_confirm(
    proposal_id: str = Form(...),
    token: str = Form(...),
    version: str = Form("original"),
):
    """Actually approve the design — claims the proposal, returns immediately,
    builds the product on Shopify in the background.

    Building the product takes 60-90s (Playwright + Shopify uploads). Holding
    the HTTP response open that long causes browsers to time out and users to
    double-click the approval link, which would race against the in-flight
    request. Decoupling the work via asyncio.create_task gives the user
    instant feedback and the actual work happens server-side. A success/
    failure email is sent when the background task finishes.
    """
    import asyncio
    from app.agents.approval import claim_proposal
    from app.agents.design_creator import execute_approval_in_background

    proposal = claim_proposal(proposal_id, token)
    if not proposal:
        return HTMLResponse(
            "<h1>Invalid or expired link</h1><p>This proposal may have already been processed.</p>",
            status_code=403,
        )

    task = asyncio.create_task(
        execute_approval_in_background(proposal_id, version, proposal["data"])
    )
    _background_approval_tasks.add(task)
    task.add_done_callback(_background_approval_tasks.discard)

    title = proposal["data"].get("suggested_title") or proposal["data"].get("name", "")
    return HTMLResponse(f"""
    <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;text-align:center;">
        <h1 style="color:#059669;">Approved — building now…</h1>
        <p><strong>{title}</strong></p>
        <p style="color:#374151;">Mango is uploading mockups to Qstomizer and creating the product on Shopify.</p>
        <p style="color:#6b7280;font-size:14px;margin-top:24px;">
            This usually takes 1–2 minutes. You can close this tab —
            you'll get an email when it's live with the product link.
        </p>
        <p style="color:#9ca3af;font-size:12px;margin-top:24px;">
            Don't click the approve link again; the work is already running in the background.
        </p>
    </body></html>
    """)


@app.get("/agents/design/reject/{proposal_id}", response_class=HTMLResponse)
async def design_reject(proposal_id: str, token: str = ""):
    """GET shows a confirmation page — real rejection requires POST.

    This interstitial stops email-scanner prefetchers from silently rejecting
    proposals (Outlook ATP, Yahoo link scanners, antivirus URL checkers, etc.).
    """
    from app.agents.approval import validate_token
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse("<h1>Invalid or expired link</h1>", status_code=403)
    return HTMLResponse(_reject_confirm_page(
        proposal_id, token, "/agents/design/reject", "design"
    ))


@app.post("/agents/design/reject", response_class=HTMLResponse)
async def design_reject_confirm(proposal_id: str = Form(...), token: str = Form(...)):
    """Actually reject the design proposal (POST, so scanners can't trigger it)."""
    from app.agents.approval import validate_token, update_status
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse("<h1>Invalid or expired link</h1>", status_code=403)
    update_status(proposal_id, "rejected")
    return HTMLResponse("""
    <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;text-align:center;">
        <h1 style="color:#dc2626;">Design Rejected</h1>
        <p>New designs will be generated on the next scheduled run.</p>
    </body></html>
    """)


@app.post("/agents/ranking/generate")
async def ranking_generate(market: str | None = None):
    """Manually trigger a ranking report."""
    from app.agents.ranking_advisor import generate_daily_report
    report = await generate_daily_report(market_override=market)
    return {
        "status": "sent",
        "market_focus": report.get("market_focus", "?"),
        "recommendations_count": len(report.get("top_actions", [])),
        "message": "Daily ranking report sent via email",
    }


@app.get("/agents/ranking/history")
async def ranking_history(limit: int = 30):
    """View past ranking reports."""
    from app.agents.ranking_advisor import get_history
    return {"reports": get_history(limit)}


@app.get("/google-ads/refresh-flow", response_class=HTMLResponse)
async def google_ads_refresh_flow():
    """One-click form to regenerate the Google Ads OAuth refresh token.

    The OAuth consent screen is in Testing mode so refresh tokens expire every
    7 days. This page kicks off the auth flow and accepts the resulting
    redirect URL, exchanges it for a new refresh token, and persists it.
    """
    from urllib.parse import urlencode

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "response_type": "code",
        "client_id": settings.google_ads_client_id,
        "redirect_uri": "http://localhost:9090",
        "scope": "https://www.googleapis.com/auth/adwords",
        "access_type": "offline",
        "prompt": "consent",
    })

    return HTMLResponse(f"""
    <html><head><title>Google Ads — Refresh Token</title></head>
    <body style="font-family:sans-serif;max-width:720px;margin:40px auto;padding:20px;color:#111;">
        <h1>Google Ads — Refresh Token</h1>
        <ol style="line-height:1.8;">
            <li>Click the button below and authorise with the Google account that owns Ads customer <code>{settings.google_ads_customer_id}</code>.</li>
            <li>You'll be redirected to <code>localhost:9090</code> which won't load — that's expected.</li>
            <li>Copy the <strong>full URL</strong> from your browser's address bar (starts with <code>http://localhost:9090/?...code=...</code>).</li>
            <li>Paste it into the form below and click <strong>Save</strong>.</li>
        </ol>

        <p style="margin:24px 0;">
            <a href="{auth_url}" target="_blank" style="display:inline-block;padding:12px 24px;background:#2563eb;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">
                1. Open Google authorisation
            </a>
        </p>

        <form method="POST" action="/google-ads/refresh-flow/exchange" style="margin-top:32px;">
            <label for="redirect_url" style="display:block;font-weight:bold;margin-bottom:8px;">2. Paste the redirect URL here:</label>
            <input
                type="text"
                id="redirect_url"
                name="redirect_url"
                placeholder="http://localhost:9090/?code=4/0A...&scope=..."
                style="width:100%;padding:12px;font-family:monospace;font-size:13px;border:1px solid #d1d5db;border-radius:6px;"
                required
            >
            <button type="submit" style="margin-top:16px;padding:12px 24px;background:#16a34a;color:white;border:0;border-radius:6px;font-weight:bold;cursor:pointer;font-size:14px;">
                Save new refresh token
            </button>
        </form>
    </body></html>
    """)


@app.post("/google-ads/refresh-flow/exchange", response_class=HTMLResponse)
async def google_ads_refresh_exchange(redirect_url: str = Form(...)):
    """Exchange the authorisation code from the redirect URL for a refresh token."""
    from urllib.parse import parse_qs, urlparse

    from app.agents.google_ads_token import save_refresh_token

    code = parse_qs(urlparse(redirect_url).query).get("code", [None])[0]
    if not code:
        return HTMLResponse(
            "<p style='color:#dc2626;font-family:sans-serif;'>No <code>code</code> parameter found in the URL. Make sure you copied the full address bar URL.</p>",
            status_code=400,
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": settings.google_ads_client_id,
            "client_secret": settings.google_ads_client_secret,
            "redirect_uri": "http://localhost:9090",
            "grant_type": "authorization_code",
        })

    data = resp.json()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        return HTMLResponse(
            f"<p style='color:#dc2626;font-family:sans-serif;'>Token exchange failed: <pre>{data}</pre></p>",
            status_code=400,
        )

    save_refresh_token(refresh_token)

    masked = refresh_token[:10] + "..." + refresh_token[-6:]
    return HTMLResponse(f"""
    <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;text-align:center;">
        <h1 style="color:#16a34a;">Refresh token saved</h1>
        <p>New token (<code>{masked}</code>) is live for both Atlas's Keyword Planner and campaign manager — no restart needed.</p>
        <p style="color:#6b7280;font-size:13px;margin-top:24px;">Next expiry: ~7 days from now (Testing-mode OAuth limit).</p>
        <p style="margin-top:24px;">
            <a href="/google-ads/refresh-flow" style="color:#2563eb;">Back to refresh flow</a>
        </p>
    </body></html>
    """)


@app.get("/agents/feedback/form", response_class=HTMLResponse)
async def feedback_form(agent: str = ""):
    """HTML form to submit feedback to any agent."""
    return HTMLResponse(f"""
    <html><head><title>Agent Feedback</title></head>
    <body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;">
        <h1>Agent Feedback</h1>
        <p>Tell your agents what to do differently.</p>
        <form method="POST" action="/agents/feedback">
            <label><strong>Agent:</strong></label><br>
            <select name="agent" style="width:100%;padding:8px;margin:8px 0 16px;font-size:16px;">
                <option value="atlas" {"selected" if agent == "atlas" else ""}>Atlas (Ranking Advisor)</option>
                <option value="mango" {"selected" if agent == "mango" else ""}>Mango (Design Creator)</option>
                <option value="olive" {"selected" if agent == "olive" else ""}>Olive (Blog Writer)</option>
                <option value="hermes" {"selected" if agent == "hermes" else ""}>Hermes (Translation Checker)</option>
            </select><br>
            <label><strong>Feedback type:</strong></label><br>
            <div style="margin:8px 0 16px;">
                <label><input type="radio" name="type" value="general" checked> General feedback</label><br>
                <label><input type="radio" name="type" value="preference"> Preference (do more of this)</label><br>
                <label><input type="radio" name="type" value="blocked"> Block topic (never suggest this)</label>
            </div>
            <label><strong>Your feedback:</strong></label><br>
            <textarea name="note" rows="4" style="width:100%;padding:8px;margin:8px 0 16px;font-size:14px;" placeholder="e.g. Focus more on mobile UX, stop suggesting payment changes..."></textarea><br>
            <button type="submit" style="padding:12px 32px;background:#2563eb;color:white;border:none;border-radius:6px;font-size:16px;cursor:pointer;">Submit Feedback</button>
        </form>
    </body></html>
    """)


@app.post("/agents/feedback", response_class=HTMLResponse)
async def feedback_submit(request: Request):
    """Submit feedback for an agent."""
    form = await request.form()
    agent = form.get("agent", "")
    note = form.get("note", "").strip()
    feedback_type = form.get("type", "general")

    if not agent or not note:
        return HTMLResponse("<h1>Missing agent or feedback</h1>", status_code=400)

    from app.agents.memory import save_feedback, VALID_AGENTS
    if agent not in VALID_AGENTS:
        return HTMLResponse(f"<h1>Unknown agent: {agent}</h1>", status_code=400)

    save_feedback(agent, note, feedback_type)

    agent_names = {"atlas": "Atlas", "mango": "Mango", "olive": "Olive", "hermes": "Hermes"}
    return HTMLResponse(f"""
    <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;text-align:center;">
        <h1 style="color:#059669;">Feedback Saved!</h1>
        <p><strong>{agent_names.get(agent, agent)}</strong> will use this in future runs.</p>
        <p style="color:#6b7280;">Type: {feedback_type}</p>
        <p style="background:#f9fafb;padding:12px;border-radius:6px;text-align:left;">"{note}"</p>
        <a href="/agents/feedback/form?agent={agent}" style="color:#2563eb;">Submit more feedback</a>
    </body></html>
    """)


@app.get("/agents/feedback/{agent}")
async def feedback_view(agent: str):
    """View an agent's memory."""
    from app.agents.memory import load_memory, VALID_AGENTS
    if agent not in VALID_AGENTS:
        return {"error": f"Unknown agent: {agent}"}
    return load_memory(agent)


@app.post("/agents/translation/check")
async def translation_check():
    """Manually trigger Hermes translation check."""
    from app.agents.translation_checker import check_and_fix_translations
    result = await check_and_fix_translations()
    return {
        "status": "done",
        "message": "Translation check complete, email sent",
        "result": result,
    }


@app.post("/agents/design-qa/run")
async def design_qa_run():
    """Manually trigger Argus design QA checker."""
    from app.agents.design_qa import run_design_qa
    result = await run_design_qa()
    return {
        "status": "done",
        "passed": result["passed"],
        "failed": result["failed"],
        "errors": result["errors"],
        "total_time": f"{result['total_time']:.0f}s",
        "message": f"QA complete — {result['passed']} passed, {result['failed']} failed. Email sent.",
    }


@app.post("/agents/ads/propose")
async def ads_propose(market: str | None = None):
    """Manually trigger campaign proposal(s). No market = all 3 markets."""
    if market:
        from app.agents.ranking_advisor import propose_campaign
        proposal = await propose_campaign(market_override=market)
        return {
            "status": "proposed",
            "proposal_id": proposal["id"],
            "campaign_name": proposal["data"].get("campaign_name", "?"),
            "message": "Campaign proposal sent via email for approval",
        }
    else:
        from app.agents.ranking_advisor import propose_all_campaigns
        results = await propose_all_campaigns()
        return {
            "status": "proposed",
            "proposals": results,
            "message": "Campaign proposals for CY, GR, EU sent via email",
        }


@app.get("/agents/ads/approve/{proposal_id}", response_class=HTMLResponse)
async def ads_approve(proposal_id: str, token: str = ""):
    """GET shows a confirmation page — real approval requires POST.

    Stops email-scanner prefetchers from silently creating Google Ads
    campaigns when they crawl email links.
    """
    from app.agents.approval import validate_token
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse(
            "<h1>Invalid or expired link</h1><p>This proposal may have already been processed.</p>",
            status_code=403,
        )
    return HTMLResponse(_approve_confirm_page(
        proposal_id, token, "/agents/ads/approve", "campaign",
        title=proposal["data"].get("campaign_name", ""),
    ))


@app.post("/agents/ads/approve", response_class=HTMLResponse)
async def ads_approve_confirm(proposal_id: str = Form(...), token: str = Form(...)):
    """Approve a campaign proposal — Atlas emails a paste-ready setup.

    Atlas does NOT create campaigns via the Google Ads API (dev token is on
    test-account access only). Approving marks the proposal and emails the
    full configuration in copy/paste-friendly form for manual setup.
    """
    from app.agents.approval import claim_proposal, update_status
    proposal = claim_proposal(proposal_id, token)
    if not proposal:
        return HTMLResponse(
            "<h1>Invalid or expired link</h1><p>This proposal may have already been processed.</p>",
            status_code=403,
        )
    from app.agents.ranking_advisor import execute_campaign_approval
    try:
        result = await execute_campaign_approval(proposal_id)
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;text-align:center;">
            <h1 style="color:#059669;">Approved — paste-ready setup emailed</h1>
            <p><strong>{proposal['data'].get('campaign_name', '?')}</strong></p>
            <p>Daily Budget: EUR {result.get('daily_budget_eur', '?')}</p>
            <p>Keywords: {result.get('keywords_count', '?')}</p>
            <p style="color:#6b7280;margin-top:16px;">Check your inbox — Atlas just emailed you the full setup. Create the campaign in the Google Ads UI (paused) using those values.</p>
            <p><a href="https://ads.google.com/aw/campaigns" style="color:#2563eb;">Open Google Ads</a></p>
        </body></html>
        """)
    except Exception as e:
        update_status(proposal_id, "pending")
        return HTMLResponse(f"<h1>Error approving proposal</h1><p>{e}</p>", status_code=500)


@app.get("/agents/ads/reject/{proposal_id}", response_class=HTMLResponse)
async def ads_reject(proposal_id: str, token: str = ""):
    """GET shows a confirmation page — real rejection requires POST."""
    from app.agents.approval import validate_token
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse("<h1>Invalid or expired link</h1>", status_code=403)
    return HTMLResponse(_reject_confirm_page(
        proposal_id, token, "/agents/ads/reject", "campaign"
    ))


@app.post("/agents/ads/reject", response_class=HTMLResponse)
async def ads_reject_confirm(proposal_id: str = Form(...), token: str = Form(...)):
    """Actually reject the campaign proposal (POST, so scanners can't trigger it)."""
    from app.agents.approval import validate_token, update_status
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse("<h1>Invalid or expired link</h1>", status_code=403)
    update_status(proposal_id, "rejected")
    return HTMLResponse("""
    <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;text-align:center;">
        <h1 style="color:#dc2626;">Campaign Rejected</h1>
        <p>Atlas will propose a new campaign in the next briefing cycle.</p>
    </body></html>
    """)


@app.get("/shopify-auth", response_class=HTMLResponse)
async def shopify_auth_start():
    """Redirect to Shopify OAuth to authorize the app."""
    client_id = settings.omg_shopify_client_id
    domain = settings.omg_shopify_domain
    if not domain.endswith(".myshopify.com"):
        domain = "52922c-2.myshopify.com"
    scopes = "read_orders,write_fulfillments,read_merchant_managed_fulfillment_orders,write_merchant_managed_fulfillment_orders,read_assigned_fulfillment_orders,write_assigned_fulfillment_orders,read_products,write_products,read_customers,write_customers,read_inventory,write_inventory,read_locations,read_shipping,write_shipping,read_order_edits,write_order_edits,read_content,write_content,read_translations,write_translations,read_locales,write_locales,read_markets,write_markets,read_script_tags,write_script_tags"
    redirect_uri = "http://localhost:8080/shopify-auth/callback"
    auth_url = (
        f"https://{domain}/admin/oauth/authorize"
        f"?client_id={client_id}"
        f"&scope={scopes}"
        f"&redirect_uri={redirect_uri}"
    )
    return f"""
    <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;">
        <h2>Authorize OMG Shopify App</h2>
        <p>Click the button below to authorize this app to read orders and create fulfillments on your OMG store.</p>
        <a href="{auth_url}" style="display:inline-block;padding:12px 32px;background:#2563eb;
           color:white;text-decoration:none;border-radius:8px;font-size:16px;">Authorize on Shopify</a>
        <p style="color:#6b7280;font-size:13px;margin-top:16px;">This is a one-time setup. After authorizing, you can use the fulfill-order endpoint.</p>
    </body></html>
    """


@app.get("/shopify-auth/callback")
async def shopify_auth_callback(code: str = "", shop: str = ""):
    """Handle OAuth callback from Shopify, exchange code for token."""
    if not code:
        return {"status": "error", "detail": "No authorization code received"}

    try:
        token = await exchange_code_for_token(code)
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;">
            <h2 style="color:#16a34a;">Authorized!</h2>
            <p>Access token obtained successfully. The app can now read orders and create fulfillments.</p>
            <p style="background:#f0fdf4;padding:12px;border-radius:8px;border:1px solid #bbf7d0;">
                Token starts with: <code>{token[:12]}...</code>
            </p>
            <p style="color:#6b7280;font-size:13px;">
                To make this permanent, add this to your <code>.env</code>:<br>
                <code>OMG_SHOPIFY_ADMIN_TOKEN={token}</code>
            </p>
            <a href="/fulfill-order" style="color:#2563eb;">Go to Fulfill Order page</a>
        </body></html>
        """)
    except Exception as e:
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;">
            <h2 style="color:#dc2626;">Authorization Failed</h2>
            <p>{e}</p>
            <a href="/shopify-auth">Try again</a>
        </body></html>
        """)


async def _register_shopify_webhook(public_url: str) -> None:
    """Register or update the orders/create webhook in Shopify to point at our current ngrok URL."""
    import httpx
    from app.omg_fulfillment import _admin_url, _get_access_token

    token = await _get_access_token()
    if not token:
        print("  [!] No Shopify admin token — skipping webhook registration")
        return

    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    webhook_address = f"{public_url}/webhook/order-created"
    topic = "orders/create"

    async with httpx.AsyncClient() as client:
        # List existing webhooks
        resp = await client.get(_admin_url("webhooks.json"), headers=headers)
        resp.raise_for_status()
        existing = resp.json().get("webhooks", [])

        # Find existing webhook for this topic
        for wh in existing:
            if wh.get("topic") == topic:
                if wh["address"] == webhook_address:
                    print(f"  [OK] Webhook already registered: {webhook_address}")
                    return
                # Update existing webhook to new address
                wh_id = wh["id"]
                resp = await client.put(
                    _admin_url(f"webhooks/{wh_id}.json"),
                    headers=headers,
                    json={"webhook": {"id": wh_id, "address": webhook_address}},
                )
                resp.raise_for_status()
                print(f"  [OK] Webhook updated: {webhook_address}")
                return

        # No existing webhook — create one
        resp = await client.post(
            _admin_url("webhooks.json"),
            headers=headers,
            json={"webhook": {"topic": topic, "address": webhook_address, "format": "json"}},
        )
        if resp.status_code == 422:
            errors = resp.json().get("errors", {})
            print(f"  [!] Webhook registration failed (missing read_orders scope?): {errors}")
            print(f"    Re-authorize at /shopify-auth or manually set webhook URL in Shopify admin to:")
            print(f"      {webhook_address}")
            return
        resp.raise_for_status()
        print(f"  [OK] Webhook created: {webhook_address}")


# ---------------------------------------------------------------------------
# SEO Management endpoints
# ---------------------------------------------------------------------------

@app.post("/seo/fix-handles")
async def seo_fix_handles(background_tasks: BackgroundTasks):
    """Fix duplicate product handles and standardize na→va spelling."""
    from app.seo_management import fix_handles
    background_tasks.add_task(fix_handles)
    return {"status": "started", "task": "fix-handles", "message": "Fixing product handles in background"}


@app.post("/seo/homepage")
async def seo_homepage(background_tasks: BackgroundTasks):
    """Update homepage SEO meta tags."""
    from app.seo_management import update_homepage_seo
    background_tasks.add_task(update_homepage_seo)
    return {"status": "started", "task": "homepage-seo", "message": "Updating homepage SEO in background"}


@app.post("/seo/collections")
async def seo_create_collections(background_tasks: BackgroundTasks):
    """Create Cyprus-specific product collections."""
    from app.seo_management import create_collections
    background_tasks.add_task(create_collections)
    return {"status": "started", "task": "create-collections", "message": "Creating collections in background"}


@app.post("/seo/all")
async def seo_run_all(background_tasks: BackgroundTasks):
    """Run all SEO optimization tasks."""
    from app.seo_management import run_all
    background_tasks.add_task(run_all)
    return {"status": "started", "task": "all", "message": "Running all SEO tasks in background"}


@app.get("/debug-inventory/{product_id}")
async def debug_inventory(product_id: int):
    """Debug: show inventory state for all variants of a product."""
    try:
        domain = settings.omg_shopify_domain
        base = f"https://{domain}/admin/api/2024-01"
        hdrs = {"X-Shopify-Access-Token": settings.omg_shopify_admin_token, "Content-Type": "application/json"}

        results = []
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base}/products/{product_id}.json", headers=hdrs, timeout=30)
            resp.raise_for_status()
            product = resp.json().get("product", {})

            loc_resp = await client.get(f"{base}/locations.json", headers=hdrs, timeout=30)
            locations = loc_resp.json().get("locations", [])
            location_id = locations[0]["id"] if locations else None

            for v in product.get("variants", []):
                info = {
                    "id": v["id"],
                    "title": v["title"],
                    "inventory_management": v.get("inventory_management"),
                    "inventory_policy": v.get("inventory_policy"),
                    "inventory_quantity": v.get("inventory_quantity"),
                    "inventory_item_id": v.get("inventory_item_id"),
                }
                if location_id and v.get("inventory_item_id"):
                    try:
                        inv_resp = await client.get(
                            f"{base}/inventory_levels.json?inventory_item_ids={v['inventory_item_id']}&location_ids={location_id}",
                            headers=hdrs, timeout=30,
                        )
                        levels = inv_resp.json().get("inventory_levels", [])
                        info["inventory_levels"] = levels
                    except Exception:
                        pass
                results.append(info)
        return {"product_id": product_id, "title": product.get("title"), "variants": results}
    except Exception as e:
        import traceback
        return JSONResponse(status_code=500, content={"error": str(e), "traceback": traceback.format_exc()})


@app.post("/debug-fix-variant/{variant_id}")
async def debug_fix_variant(variant_id: int):
    """Debug: try to fix a single variant and return all raw API responses."""
    domain = settings.omg_shopify_domain
    base = f"https://{domain}/admin/api/2024-01"
    hdrs = {"X-Shopify-Access-Token": settings.omg_shopify_admin_token, "Content-Type": "application/json"}
    steps = []

    async with httpx.AsyncClient() as client:
        # Step 1: Get variant
        r = await client.get(f"{base}/variants/{variant_id}.json", headers=hdrs, timeout=30)
        variant = r.json().get("variant", {})
        steps.append({"step": "get_variant", "status": r.status_code, "inventory_item_id": variant.get("inventory_item_id"), "inventory_management": variant.get("inventory_management"), "inventory_policy": variant.get("inventory_policy")})

        inv_item_id = variant.get("inventory_item_id")

        # Step 2: Get locations
        r = await client.get(f"{base}/locations.json", headers=hdrs, timeout=30)
        locations = r.json().get("locations", [])
        loc_id = locations[0]["id"] if locations else None
        steps.append({"step": "get_locations", "status": r.status_code, "location_id": loc_id, "count": len(locations)})

        # Step 3: Update variant to shopify managed
        r = await client.put(f"{base}/variants/{variant_id}.json", headers=hdrs, json={"variant": {"id": variant_id, "inventory_management": "shopify", "inventory_policy": "continue"}}, timeout=30)
        steps.append({"step": "update_variant", "status": r.status_code, "body": r.text[:300]})

        # Step 4: Connect inventory to location
        r = await client.post(f"{base}/inventory_levels/connect.json", headers=hdrs, json={"location_id": loc_id, "inventory_item_id": inv_item_id}, timeout=30)
        steps.append({"step": "connect", "status": r.status_code, "body": r.text[:300]})

        # Step 5: Set inventory level
        r = await client.post(f"{base}/inventory_levels/set.json", headers=hdrs, json={"location_id": loc_id, "inventory_item_id": inv_item_id, "available": 999}, timeout=30)
        steps.append({"step": "set_level", "status": r.status_code, "body": r.text[:300]})

        # Step 6: Verify
        r = await client.get(f"{base}/inventory_levels.json?inventory_item_ids={inv_item_id}", headers=hdrs, timeout=30)
        steps.append({"step": "verify_levels", "status": r.status_code, "body": r.text[:500]})

    return {"variant_id": variant_id, "steps": steps}


@app.post("/fix-sold-out/{product_id}")
async def fix_sold_out(product_id: int):
    """Fix a sold-out product by setting inventory_policy=continue on all variants."""
    from app.shopify_product_creator import fix_sold_out_product
    result = await fix_sold_out_product(product_id)
    return {"status": "fixed", **result}


@app.post("/fix-sold-out-all")
async def fix_sold_out_all(background_tasks: BackgroundTasks):
    """Fix ALL products that may show as sold out."""
    from app.shopify_product_creator import fix_sold_out_product

    async def _fix_all():
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://{settings.omg_shopify_domain}/admin/api/2024-01/products.json?limit=250",
                headers={"X-Shopify-Access-Token": settings.omg_shopify_admin_token},
                timeout=60,
            )
            resp.raise_for_status()
            products = resp.json().get("products", [])
            for p in products:
                try:
                    await fix_sold_out_product(p["id"])
                except Exception as e:
                    logger.error(f"Failed to fix product {p['id']}: {e}")

    background_tasks.add_task(_fix_all)
    return {"status": "started", "message": "Fixing all products in background"}


@app.post("/fix-shipping-profile")
async def fix_shipping_profile(product_ids: list[int] | None = None):
    """Add products to the Cyprus shipping profile. If no IDs given, fixes ALL products."""
    from app.shopify_product_creator import add_products_to_shipping_profile

    if not product_ids:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://{settings.omg_shopify_domain}/admin/api/2024-01/products.json?limit=250",
                headers={"X-Shopify-Access-Token": settings.omg_shopify_admin_token},
                timeout=60,
            )
            resp.raise_for_status()
            product_ids = [p["id"] for p in resp.json().get("products", [])]

    results = await add_products_to_shipping_profile(product_ids)
    return {"status": "done", "results": results}


@app.post("/fix-archive-redirects")
async def fix_archive_redirects(target: str | None = None):
    """301 every archived product's /products/<handle> URL to a sensible target.

    Default target: /collections/t-shirts. Skips products that already have
    a redirect at that path. Safe to re-run.
    """
    from app.shopify_redirects import DEFAULT_REDIRECT_TARGET, redirect_archived_products

    return await redirect_archived_products(target or DEFAULT_REDIRECT_TARGET)


@app.post("/sync-product/{product_id}")
async def sync_product(product_id: int):
    """Download design image from Shopify and create mappings for a product."""
    from app.shopify_product_creator import create_mappings_for_product, _admin_url, _headers

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _admin_url(f"products/{product_id}.json"),
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        product = resp.json()["product"]
        handle = product["handle"]

        # Download the design artwork image
        images = product.get("images", [])
        design_img = next((i for i in reversed(images) if i.get("alt") == "Design Artwork"), None)
        if not design_img:
            return {"error": "No Design Artwork image found on product"}

        img_resp = await client.get(design_img["src"], timeout=30, follow_redirects=True)
        dest = STATIC_DIR / f"design_{handle}.png"
        dest.write_bytes(img_resp.content)

    mappings = await create_mappings_for_product(product, design_image=f"design_{handle}.png")
    return {"handle": handle, "design_image": f"design_{handle}.png", "mappings_created": len(mappings)}


@app.on_event("startup")
async def print_endpoints():
    base = f"http://localhost:{settings.port}"

    # Start ngrok tunnel for HTTPS
    try:
        from pyngrok import ngrok
        kwargs = {"addr": settings.port}
        if settings.ngrok_domain:
            kwargs["hostname"] = settings.ngrok_domain
        tunnel = ngrok.connect(**kwargs)
        public_url = tunnel.public_url
        print(f"\n  ngrok tunnel: {public_url}")
        print(f"  Webhook URL:  {public_url}/webhook/order-created\n")
    except Exception as e:
        public_url = None
        print(f"\n  ngrok failed: {e}")
        print("  Install ngrok or run 'ngrok http 8000' manually.\n")

    # Auto-register webhook with current ngrok URL
    if public_url:
        try:
            await _register_shopify_webhook(public_url)
        except Exception as e:
            print(f"  [!] Webhook registration failed: {e}")

    print("=" * 50)
    print("  OMG Shopify → TShirtJunkies Service")
    print("=" * 50)
    print(f"  Test Webhook:    {base}/test-webhook")
    print(f"  Manual Order:    {base}/manual-order")
    print(f"  Fulfill Order:   {base}/fulfill-order")
    print(f"  Shopify Auth:    {base}/shopify-auth")
    print(f"  View Mappings:   {base}/mappings")
    if public_url:
        print(f"  Webhook (public): {public_url}/webhook/order-created")
    print(f"  Agent: Blog       {base}/agents/blog/generate (POST)")
    print(f"  Agent: Design     {base}/agents/design/research (POST)")
    print(f"  Agent: Ranking    {base}/agents/ranking/generate (POST)")
    print("=" * 50)

    # Start AI agent scheduler
    if settings.anthropic_api_key:
        try:
            from app.agents.scheduler import start_scheduler
            start_scheduler()
            print("  AI Agents:       Scheduler started")
        except Exception as e:
            print(f"  AI Agents:       Failed to start ({e})")
    else:
        print("  AI Agents:       Disabled (no ANTHROPIC_API_KEY)")

    print("=" * 50 + "\n")


@app.on_event("shutdown")
async def shutdown_scheduler():
    try:
        from app.agents.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=True,
        reload_dirs=["app"],
        reload_excludes=["static/*", "*.png", "*.json"],
        loop="asyncio",
    )
