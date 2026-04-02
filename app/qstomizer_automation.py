"""Automate Qstomizer: upload design image, select size, and add to cart."""
import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from playwright.async_api import async_playwright

_playwright_executor = ThreadPoolExecutor(max_workers=2)


# Map OMG shipping methods to TShirtJunkies checkout shipping options.
# Key: country code -> preferred TJ shipping method name (substring match on label text).
# TJ checkout shows shipping options after address is filled; we pick the best match.
#
# OMG EU edition profile -> TJ checkout:
#   CY: Travel Express EUR 3  -> TJ: Travel Express EUR 3 (must select; not first)
#   GR: Geniki Taxydromiki EUR 5 -> TJ: Geniki pickup EUR 5 (auto-selected, first option)
#   FR: Europe postal EUR 6   -> TJ: Postal Shipping EUR 5 (auto-selected, only option)
SHIPPING_METHOD_MAP = {
    "CY": "Travel Express",
    "GR": "Geniki",             # first option, auto-selected
    "FR": "Postal",             # only option, auto-selected
}
# Fallback: keep the default (first/cheapest) option for unmapped countries

QSTOMIZER_URLS = {
    "male": "https://tshirtjunkies.co/apps/qstomizer/?qstomizer-product-id=9864408301915",
    "female": "https://tshirtjunkies.co/apps/qstomizer/?qstomizer-product-id=8676301799771",
}

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
DEFAULT_IMAGE = STATIC_DIR / "front_design.png"


def _run_playwright_in_thread(coro_func, *args, **kwargs):
    """Run an async Playwright function in a separate thread with ProactorEventLoop.

    Uvicorn on Windows uses SelectorEventLoop which doesn't support subprocesses.
    Playwright needs subprocess support, so we run it in its own thread/loop.
    """
    def _thread_target():
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro_func(*args, **kwargs))
        finally:
            loop.close()

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_thread_target)
        return future.result()


async def customize_and_add_to_cart(
    product_type: str = "male",
    size: str = "L",
    color: str = "White",
    image_path: Path | str = DEFAULT_IMAGE,
    quantity: int = 1,
    headless: bool = False,
    shipping: dict | None = None,
) -> str:
    """Open Qstomizer, upload design, select color and size, add to cart,
    and optionally fill in checkout shipping details.

    Runs Playwright in a separate thread with ProactorEventLoop on Windows
    to avoid uvicorn's SelectorEventLoop subprocess limitation.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _playwright_executor,
        lambda: _run_playwright_in_thread(
            _customize_and_add_to_cart_impl,
            product_type=product_type,
            size=size,
            color=color,
            image_path=image_path,
            quantity=quantity,
            headless=headless,
            shipping=shipping,
        ),
    )


async def _customize_and_add_to_cart_impl(
    product_type: str = "male",
    size: str = "L",
    color: str = "White",
    image_path: Path | str = DEFAULT_IMAGE,
    quantity: int = 1,
    headless: bool = False,
    shipping: dict | None = None,
) -> str:
    """Actual Playwright implementation."""
    image_path = Path(image_path).resolve()
    if not image_path.exists():
        raise FileNotFoundError(f"Design image not found: {image_path}")

    url = QSTOMIZER_URLS.get(product_type)
    if not url:
        raise ValueError(f"Unknown product_type '{product_type}'. Use 'male' or 'female'.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        # Use wide viewport so Qstomizer canvas renders properly
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        print(f"Opening Qstomizer page for {product_type} tee...")
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(5000)

        # Find the Qstomizer iframe
        qf = None
        for frame in page.frames:
            if "qstomizer.bigvanet.com" in frame.url:
                qf = frame
                break
        if not qf:
            await browser.close()
            raise RuntimeError("Could not find Qstomizer iframe")

        # Hide overlapping elements
        await qf.evaluate("document.getElementById('qsmzTextWindow').style.display = 'none'")
        await page.evaluate("document.querySelector('.shopify-section-header-sticky')?.remove()")

        # --- Step 0: Select color ---
        print(f"Selecting color: {color}")
        # Qstomizer binds click via jQuery delegation:
        #   $(".colorVariationCont").on("click", ".colorVarWrap", handler)
        # The handler toggles colorVarWrapActive and calls Ka().
        # We must trigger the click so it bubbles through jQuery's delegation.
        color_result = await qf.evaluate(f"""
            () => {{
                const target = '{color}';
                const swatches = document.querySelectorAll('.colorVarWrap');
                for (const swatch of swatches) {{
                    if (swatch.getAttribute('data-colordes') === target) {{
                        // Manually do what the delegated click handler does:
                        // 1. Remove active from siblings, add to this
                        const group = swatch.getAttribute('data-optiongroup');
                        jQuery('.' + group).removeClass('colorVarWrapActive');
                        jQuery(swatch).addClass('colorVarWrapActive');
                        // 2. Clear the variant dropdown for this option
                        const opt = swatch.getAttribute('data-option');
                        jQuery('#variantValues' + opt).val('');
                        // 3. Call Ka to update visual price (the key step)
                        if (typeof Ka === 'function') Ka({{updateVisualPrice: false}});
                        return 'selected: ' + target + ' (id=' + swatch.dataset.variationid + ')';
                    }}
                }}
                return 'color_not_found: ' + target;
            }}
        """)
        print(f"  {color_result}")

        # Wait for color change to take effect (canvas reloads the product image)
        await page.wait_for_timeout(5000)

        # Verify color was actually selected
        active_color = await qf.evaluate("""
            () => {
                const active = document.querySelector('.colorVarWrapActive');
                return active ? (active.getAttribute('data-colordes') || 'unknown') : 'no_active';
            }
        """)
        print(f"  Active color: {active_color}")

        # --- Step 1: Upload the design image ---
        print(f"Uploading design: {image_path.name}")
        upload_btn = await qf.wait_for_selector("#btnUploadImage", timeout=10000)
        await upload_btn.click(force=True)
        await page.wait_for_timeout(1000)

        file_input = await qf.wait_for_selector(
            "input[name='qsmz-file'][accept='.jpg,.jpeg,.png']", timeout=10000
        )
        await file_input.set_input_files(str(image_path))
        await file_input.dispatch_event("change")

        # Wait for upload + processing phases
        for msg_id in ["#msgUploading", "#msgProcessing"]:
            try:
                await qf.wait_for_selector(msg_id, state="visible", timeout=5000)
            except Exception:
                pass
            await qf.wait_for_selector(msg_id, state="hidden", timeout=60000)
        print("  Upload and processing complete!")
        await page.wait_for_timeout(2000)

        # --- Step 2: Click uploaded image thumbnail to place on canvas ---
        print("Placing image on canvas...")
        # Use jQuery to click the imagesubcontainer (Qstomizer is jQuery-based)
        click_result = await qf.evaluate("""
            () => {
                const containers = document.querySelectorAll('.imagesubcontainer');
                if (containers.length === 0) return 'no_containers';
                const target = containers[containers.length - 1];
                if (typeof jQuery !== 'undefined') {
                    jQuery(target).trigger('click');
                } else {
                    target.click();
                }
                return 'clicked';
            }
        """)
        if click_result != "clicked":
            print(f"  Warning: {click_result}")
        await page.wait_for_timeout(3000)
        print("  Image placed on canvas")

        # --- Step 3: Select size via the variant dropdown ---
        print(f"Selecting size: {size}")
        size_select = await qf.query_selector("#variantValues1")
        if size_select:
            await size_select.select_option(label=size)
            await page.wait_for_timeout(500)

        # --- Step 4: Click ORDER NOW ---
        print("Clicking ORDER NOW...")
        await qf.evaluate("jQuery('#addtocart').trigger('click')")
        await page.wait_for_timeout(3000)

        # --- Step 5: Handle the Quantity window that appears ---
        # The quantity window shows size/qty selection and has ADD TO CART button
        qty_window = await qf.query_selector("#qsmzQuantitiesWindow")
        if qty_window and await qty_window.is_visible():
            print("Quantity window appeared")

            # Set the correct size quantity in the quantity window
            # Each size has a Rtable-cell label and an infoQty input
            qty_set = await qf.evaluate(f"""
                () => {{
                    const targetSize = '{size}';
                    const qty = {quantity};
                    const window = document.getElementById('qsmzQuantitiesWindow');
                    const sizeLabels = window.querySelectorAll('.Rtable-cell');
                    const qtyInputs = window.querySelectorAll('input.infoQty');

                    // Zero out all quantities first
                    for (const input of qtyInputs) {{
                        if (input.value !== '0') {{
                            input.value = '0';
                            input.dispatchEvent(new Event('change', {{bubbles: true}}));
                        }}
                    }}

                    // Find the size label and its corresponding input
                    let inputIndex = 0;
                    for (let i = 0; i < sizeLabels.length; i++) {{
                        const label = sizeLabels[i].textContent.trim();
                        // Skip non-size labels (header cells, etc)
                        if (['XS','S','M','L','XL','2XL','3XL','4XL','5XL'].includes(label)) {{
                            if (label === targetSize && inputIndex < qtyInputs.length) {{
                                qtyInputs[inputIndex].value = qty;
                                qtyInputs[inputIndex].dispatchEvent(new Event('change', {{bubbles: true}}));
                                return 'set ' + label + ' to ' + qty;
                            }}
                            inputIndex++;
                        }}
                    }}
                    return 'size_not_found: ' + targetSize;
                }}
            """)
            print(f"  Qty: {qty_set}")

            # Click ADD TO CART in the quantity window
            print("  Clicking ADD TO CART in quantity window...")
            add_cart_result = await qf.evaluate("""
                () => {
                    // Find the ADD TO CART button in the quantities window
                    const window = document.getElementById('qsmzQuantitiesWindow');
                    if (!window) return 'window_not_found';

                    // Look for the action buttons
                    const btns = window.querySelectorAll('a, button');
                    for (const btn of btns) {
                        const text = btn.textContent.trim().toUpperCase();
                        if (text.includes('ADD TO CART')) {
                            if (typeof jQuery !== 'undefined') {
                                jQuery(btn).trigger('click');
                            } else {
                                btn.click();
                            }
                            return 'clicked_add_to_cart: ' + text;
                        }
                    }
                    return 'no_add_to_cart_button';
                }
            """)
            print(f"  Result: {add_cart_result}")
            await page.wait_for_timeout(5000)

        # --- Step 6: Handle disclaimer popup if it appears ---
        disclaimer = await qf.query_selector("#qsmzDisclaimerPopup")
        if disclaimer and await disclaimer.is_visible():
            print("Accepting disclaimer...")
            await qf.evaluate("""
                () => {
                    const popup = document.getElementById('qsmzDisclaimerPopup');
                    const btns = popup.querySelectorAll('a.actionBtn');
                    for (const btn of btns) {
                        if (!btn.classList.contains('closePopupBtn')) {
                            jQuery(btn).trigger('click');
                            return;
                        }
                    }
                }
            """)
            await page.wait_for_timeout(5000)

        # --- Step 7: Wait for Qstomizer to save data and redirect to checkout ---
        print("Waiting for Qstomizer to save and redirect...")
        # Wait for "Saving Data..." to finish (up to 60s)
        try:
            await page.wait_for_url("**/checkout**", timeout=60000)
        except Exception:
            # May redirect to cart instead
            try:
                await page.wait_for_url("**/cart**", timeout=10000)
            except Exception:
                pass

        current_url = page.url
        print(f"After add-to-cart URL: {current_url}")

        # --- Step 8: Fill in checkout shipping details ---
        if shipping:
            # Navigate to checkout if we landed on cart
            if "/cart" in current_url and "/checkout" not in current_url:
                await page.goto("https://tshirtjunkies.co/checkout", wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)

            # Wait for checkout form to load
            try:
                await page.wait_for_selector("input[name='email']", timeout=15000)
            except Exception:
                print("Warning: Checkout form not found, skipping shipping fill")
                shipping = None

        if shipping:
            print("Filling in shipping details...")
            await _fill_checkout(page, shipping)
            current_url = page.url

        # Take screenshot
        screenshot_path = STATIC_DIR / "checkout_result.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        print(f"Screenshot saved: {screenshot_path}")
        print(f"Final URL: {current_url}")

        await browser.close()

    return current_url


async def _fill_checkout(page, shipping: dict) -> None:
    """Fill in the Shopify checkout form with customer shipping details."""

    # Select country first (affects available fields and address autocomplete)
    country_code = shipping.get("country_code", "")
    if country_code:
        try:
            country_select = await page.wait_for_selector(
                "select[name='countryCode']", timeout=5000
            )
            await country_select.select_option(value=country_code)
            await page.wait_for_timeout(1500)
            print(f"  country: {country_code}")
        except Exception as e:
            print(f"  country: failed ({e})")

    # Fill fields using type() instead of fill() for React compatibility
    field_map = {
        "email": "input[name='email']",
        "first_name": "input[name='firstName']",
        "last_name": "input[name='lastName']",
        "address1": "input[name='address1']",
        "address2": "input[name='address2']",
        "city": "input[name='city']",
        "zip": "input[name='postalCode']",
        "phone": "input[name='phone']",
    }

    for key, selector in field_map.items():
        value = shipping.get(key, "")
        if not value:
            continue
        try:
            field = await page.wait_for_selector(selector, timeout=5000)
            # Use React's native value setter + input event to update React state
            await field.evaluate("""(el, val) => {
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                setter.call(el, val);
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('blur', {bubbles: true}));
            }""", str(value))
            await page.wait_for_timeout(500)
            # Dismiss autocomplete dropdown if it appears
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)
            print(f"  {key}: filled")
        except Exception as e:
            print(f"  {key}: failed ({e})")

    # Tab out and wait for Shopify to autosave the checkout
    await page.keyboard.press("Tab")
    await page.wait_for_timeout(3000)

    # Select shipping method based on country
    if country_code:
        await _select_shipping_method(page, country_code)

    print("Shipping details filled (stopping before payment)")


async def _select_shipping_method(page, country_code: str) -> None:
    """Select the appropriate shipping method at TJ checkout based on country."""
    preferred = SHIPPING_METHOD_MAP.get(country_code, "")
    print(f"  Selecting shipping method for {country_code} (preferred: {preferred or 'cheapest'})...")

    # Wait for actual shipping method options to appear (not Ship/Pickup toggle)
    shipping_found = False
    for attempt in range(5):
        await page.wait_for_timeout(3000)
        count = await page.evaluate("""
            () => {
                // Count radio buttons that are NOT the Ship/Pickup delivery type toggle
                const radios = document.querySelectorAll('input[type="radio"]');
                let shippingCount = 0;
                for (const r of radios) {
                    if (r.id === 'SHIPPING' || r.id === 'PICK_UP') continue;
                    const label = document.querySelector('label[for="' + r.id + '"]');
                    const text = label ? label.textContent.trim() : '';
                    if (text.includes('Credit') || text.includes('PayPal') || text.includes('Viva')) continue;
                    if (text === 'Ship' || text === 'Pickup') continue;
                    shippingCount++;
                }
                return shippingCount;
            }
        """)
        if count > 0:
            shipping_found = True
            break
        print(f"  Waiting for shipping methods (attempt {attempt + 1})...")

    if not shipping_found:
        print("  Warning: No shipping methods found")
        return

    # List available shipping methods and select preferred one
    # Skip: Ship/Pickup toggle, payment methods, non-shipping radios
    result = await page.evaluate(f"""
        () => {{
            const preferred = '{preferred}'.toLowerCase();
            const skip = ['ship', 'pickup', 'credit', 'paypal', 'card', 'viva'];
            const skipIds = ['SHIPPING', 'PICK_UP'];
            const methods = [];

            const radios = document.querySelectorAll('input[type="radio"]');
            for (const radio of radios) {{
                if (skipIds.includes(radio.id)) continue;
                const label = document.querySelector('label[for="' + radio.id + '"]');
                if (!label) continue;
                const text = label.textContent.trim();
                if (skip.some(s => text.toLowerCase() === s || text.toLowerCase().includes('credit')))
                    continue;
                methods.push({{id: radio.id, text: text, el: radio}});
            }}

            // Also check role="radio" elements (newer Shopify checkout)
            const roleRadios = document.querySelectorAll('[role="radio"]');
            for (const rr of roleRadios) {{
                const text = rr.textContent.trim();
                if (skip.some(s => text.toLowerCase() === s)) continue;
                if (text.includes('Credit') || text.includes('PayPal') || text.includes('Viva')) continue;
                if (!methods.some(m => m.text === text)) {{
                    methods.push({{id: rr.id, text: text, el: rr}});
                }}
            }}

            if (methods.length === 0) return 'no_shipping_methods (may be auto-selected)';

            const listing = methods.map(m => m.text.substring(0, 60)).join(' | ');

            // Try to match preferred method
            if (preferred) {{
                for (const m of methods) {{
                    if (m.text.toLowerCase().includes(preferred)) {{
                        m.el.click();
                        return 'selected: ' + m.text.substring(0, 80) + ' [from: ' + listing + ']';
                    }}
                }}
            }}

            // Fallback: pick first (usually cheapest)
            methods[0].el.click();
            return 'fallback: ' + methods[0].text.substring(0, 80) + ' [from: ' + listing + ']';
        }}
    """)
    print(f"  Shipping: {result.encode('ascii', 'replace').decode()}")


async def process_order_items(items: list[dict], headless: bool = False) -> list[dict]:
    """Process multiple order items through Qstomizer.

    Args:
        items: List of dicts with keys: product_type, size, quantity, image_path (optional)
        headless: Run browser without GUI

    Returns:
        List of results with status for each item.
    """
    results = []
    for item in items:
        try:
            checkout_url = await customize_and_add_to_cart(
                product_type=item.get("product_type", "male"),
                size=item.get("size", "L"),
                color=item.get("color", "White"),
                image_path=item.get("image_path", DEFAULT_IMAGE),
                quantity=item.get("quantity", 1),
                headless=headless,
                shipping=item.get("shipping"),
            )
            results.append({
                "status": "ok",
                "item": item,
                "checkout_url": checkout_url,
            })
        except Exception as e:
            results.append({
                "status": "error",
                "item": item,
                "error": str(e),
            })
    return results


if __name__ == "__main__":
    import sys

    product_type = sys.argv[1] if len(sys.argv) > 1 else "male"
    size = sys.argv[2] if len(sys.argv) > 2 else "L"
    color = sys.argv[3] if len(sys.argv) > 3 else "White"

    result = asyncio.run(customize_and_add_to_cart(
        product_type=product_type,
        size=size,
        color=color,
        headless=False,
    ))
    print(f"\nDone! URL: {result}")
