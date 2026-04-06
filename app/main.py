import logging
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.staticfiles import StaticFiles

from fastapi.responses import HTMLResponse

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
        <select id="product_type">
            <option value="male">Male Tee (Classic Tee up to 5XL)</option>
            <option value="female">Female Tee (Women's T-Shirt)</option>
        </select>
        <label>Size:</label>
        <select id="size">
            <option>S</option><option selected>M</option><option>L</option>
            <option>XL</option><option>2XL</option><option>3XL</option>
            <option>4XL</option><option>5XL</option>
        </select>
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
       <br>Shipping method will be auto-selected: CY=Travel Express, GR=Geniki Taxydromiki, FR=Postal.</p>
    <div id="status"></div>
    <script>
        const VARIANT_MAP = %VARIANT_MAP%;
        document.getElementById('product_type').addEventListener('change', function() {
            const sizeSelect = document.getElementById('size');
            const sizes = Object.keys(VARIANT_MAP[this.value]);
            sizeSelect.innerHTML = sizes.map(s => '<option>' + s + '</option>').join('');
        });
        document.getElementById('form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const status = document.getElementById('status');
            const type = document.getElementById('product_type').value;
            const size = document.getElementById('size').value;
            const qty = parseInt(document.getElementById('qty').value);
            const name = document.getElementById('customer_name').value.split(' ');
            const variant = VARIANT_MAP[type][size];
            if (!variant) { alert('No mapping for ' + type + ' ' + size); return; }
            status.style.display = 'block';
            status.className = 'loading';
            status.textContent = 'Sending test webhook...';
            const countryEl = document.getElementById('country');
            const opt = countryEl.options[countryEl.selectedIndex];
            const country_code = countryEl.value;
            const order = {
                id: Date.now(), order_number: 'TEST-' + Date.now(),
                line_items: [{
                    variant_id: variant.source, quantity: qty,
                    title: type === 'male' ? 'Astous na Laloun Graphic Tee Male - EU Edition'
                                           : 'Astous na Laloun Graphic Tee Female - EU Edition',
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
    """Serve a form to send a fake webhook for testing."""
    import json
    config = load_mappings()
    # Build variant map: {product_type: {size: {source: id, target: id}}}
    variant_map = {"male": {}, "female": {}}
    for mapping in config.mappings:
        ptype = "female" if "female" in mapping.source_handle else "male"
        for v in mapping.variants:
            variant_map[ptype][v.source_title] = {
                "source": v.source_variant_id,
                "target": v.target_variant_id,
            }
    return TEST_WEBHOOK_HTML.replace("%VARIANT_MAP%", json.dumps(variant_map))


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
    for mapping in config.mappings:
        for v in mapping.variants:
            variant_map[v.source_variant_id] = v.target_variant_id
            product_id_map[v.source_variant_id] = mapping.target_product_id
            handle_map[v.source_variant_id] = mapping.source_handle
            design_map[v.source_variant_id] = getattr(mapping, "design_image", "front_design.png")

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

        # Handle both old format ("L") and new Gender+Size format ("Male / L")
        if " / " in variant_title:
            gender_str, size = variant_title.split(" / ", 1)
            product_type = "female" if "female" in gender_str.lower() else "male"
        else:
            product_type = "female" if "female" in source_handle else "male"
            size = variant_title

        # Extract shipping details from order
        shipping_address = order.get("shipping_address") or {}
        customer = order.get("customer", {})
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
        }

        try:
            design_file = STATIC_DIR / item.get("design_image", "front_design.png")
            if not design_file.exists():
                design_file = FRONT_DESIGN_IMAGE  # fallback to default

            result = await customize_and_add_to_cart(
                product_type=product_type,
                size=size,
                color="White",
                image_path=str(design_file),
                quantity=item["quantity"],
                headless=True,
                shipping=shipping,
            )
            item["cart_url"] = result["checkout_url"]
            item["mockup_url"] = result.get("mockup_url")
            logger.info(f"  {item['title']} ({size}) → {item['cart_url']}")
            if item.get("mockup_url"):
                logger.info(f"  Mockup: {item['mockup_url']}")
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
            result = await customize_and_add_to_cart(
                product_type=item["product_type"],
                size=item["variant_title"],
                color="White",
                image_path=str(FRONT_DESIGN_IMAGE),
                quantity=item["quantity"],
                headless=True,
                shipping=shipping,
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
    """Fulfill an OMG order with tracking info."""
    body = await request.json()
    order_number = body.get("order_number", "").strip()
    if not order_number:
        return {"status": "error", "detail": "Order number is required"}

    return await fulfill_order(
        order_number=order_number,
        tracking_number=body.get("tracking_number", ""),
        tracking_url=body.get("tracking_url", ""),
        tracking_company=body.get("tracking_company", ""),
    )


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
    """Approve and publish a blog post."""
    from app.agents.approval import validate_token, update_status
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse(
            "<h1>Invalid or expired link</h1><p>This proposal may have already been processed.</p>",
            status_code=403,
        )
    # Execute
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
        return HTMLResponse(f"<h1>Error publishing</h1><p>{e}</p>", status_code=500)


@app.get("/agents/blog/reject/{proposal_id}", response_class=HTMLResponse)
async def blog_reject(proposal_id: str, token: str = ""):
    """Reject a blog proposal."""
    from app.agents.approval import validate_token, update_status
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse(
            "<h1>Invalid or expired link</h1>", status_code=403,
        )
    update_status(proposal_id, "rejected")
    return HTMLResponse("""
    <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;text-align:center;">
        <h1 style="color:#dc2626;">Blog Post Rejected</h1>
        <p>A new one will be generated on the next scheduled run.</p>
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


@app.get("/agents/design/approve/{proposal_id}", response_class=HTMLResponse)
async def design_approve(proposal_id: str, token: str = ""):
    """Approve a design — creates product on Shopify + mapping."""
    from app.agents.approval import validate_token
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse(
            "<h1>Invalid or expired link</h1><p>This proposal may have already been processed.</p>",
            status_code=403,
        )
    from app.agents.design_creator import execute_approval
    try:
        result = await execute_approval(proposal_id)
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;text-align:center;">
            <h1 style="color:#059669;">Product Created!</h1>
            <p><strong>{proposal['data'].get('suggested_title', proposal['data'].get('name', ''))}</strong></p>
            <p>Product ID: {result.get('product_id', '?')}</p>
            <p>Handle: {result.get('product_handle', '?')}</p>
            <a href="{result.get('product_url', '#')}" style="color:#2563eb;">View on store</a>
            <p style="color:#6b7280;margin-top:16px;">Mapping to TShirtJunkies has been created automatically.</p>
        </body></html>
        """)
    except Exception as e:
        return HTMLResponse(f"<h1>Error creating product</h1><p>{e}</p>", status_code=500)


@app.get("/agents/design/reject/{proposal_id}", response_class=HTMLResponse)
async def design_reject(proposal_id: str, token: str = ""):
    """Reject a design proposal."""
    from app.agents.approval import validate_token, update_status
    proposal = validate_token(proposal_id, token)
    if not proposal:
        return HTMLResponse("<h1>Invalid or expired link</h1>", status_code=403)
    update_status(proposal_id, "rejected")
    return HTMLResponse("""
    <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px;text-align:center;">
        <h1 style="color:#dc2626;">Design Rejected</h1>
        <p>New designs will be generated on the next scheduled run (Monday 10:00).</p>
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


@app.get("/shopify-auth", response_class=HTMLResponse)
async def shopify_auth_start():
    """Redirect to Shopify OAuth to authorize the app."""
    client_id = settings.omg_shopify_client_id
    domain = settings.omg_shopify_domain
    if not domain.endswith(".myshopify.com"):
        domain = "52922c-2.myshopify.com"
    scopes = "read_orders,write_fulfillments,read_products,write_products,read_customers,write_customers,read_inventory,write_inventory,read_locations,read_shipping,write_shipping,read_order_edits,write_order_edits,read_content,write_content"
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
