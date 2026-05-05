"""Automate Qstomizer: upload design image, select size, and add to cart."""
import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from playwright.async_api import async_playwright

_playwright_executor = ThreadPoolExecutor(max_workers=2)


# Map OMG shipping methods to TShirtJunkies checkout shipping options.
# Key: country code -> preferred TJ shipping method name (substring match on label text).
# TJ checkout shows shipping options after address is filled; we pick the best match.
# Values are either a string (single method for that country) or a dict mapping
# OMG shipping method title → TJ method substring. "_default" is used when the
# OMG method title is unknown.
#
# OMG → TJ:
#   CY: Home Delivery EUR 4.50 → TJ: Home Delivery
#   GR: Geniki Taxydromiki EUR 5 → TJ: Γενικής Ταχυδρομικής
#   GR: Home Delivery EUR 10 → TJ: Παράδοσης κατ' οίκον
#   FR: EU Flat Rate EUR 4.79 → TJ: Postal
SHIPPING_METHOD_MAP = {
    "CY": "Home Delivery",
    "GR": {
        "Geniki Taxydromiki": "Γενικής",
        "Home Delivery": "κατ' οίκον",
        "_default": "Γενικής",
    },
    "FR": "Postal",
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
    placement: str = "front",
    vertical_offset: float = -0.25,
    horizontal_offset: float = 0.0,
    vertical_safety_pad_px: int = 4,
) -> str:
    """Open Qstomizer, upload design, select color and size, add to cart,
    and optionally fill in checkout shipping details.

    Args:
        placement: "front" (default) or "back" — switches Qstomizer's canvas
            view before uploading so the design is placed on the correct side.
        vertical_offset: fraction of the Qstomizer print-area height to
            nudge the placed design. Negative moves the print UP (toward
            the collar), positive moves it DOWN. 0.0 keeps Qstomizer's
            default dead-center placement. Our marketing mockups sit the
            print around the upper back; -0.25 gets close to that. The
            reposition targets Konva Group nodes named 'grupoimage*' and
            fires 'dragend' so Qstomizer's save hook captures the new
            position (TJ prints from the stored position, not just the
            rendered preview).

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
            placement=placement,
            vertical_offset=vertical_offset,
            horizontal_offset=horizontal_offset,
            vertical_safety_pad_px=vertical_safety_pad_px,
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
    placement: str = "front",
    vertical_offset: float = -0.25,
    horizontal_offset: float = 0.0,
    vertical_safety_pad_px: int = 4,
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

        # --- Step 0: Select color + size via Colors/Size variations window ---
        # This is the proper way — opens the variations window which has product
        # image swatches that actually change the canvas (not just metadata).
        print(f"Opening Colors/Size window...")
        await qf.evaluate("jQuery('#btnvariations').trigger('click')")
        await page.wait_for_timeout(2000)

        # Click the color swatch (qsmzImageVariation) inside the variations window
        print(f"Selecting color: {color}")
        color_result = await qf.evaluate(f"""
            () => {{
                const target = '{color}';
                const window = document.getElementById('qsmzVariationsWindow');
                // Look for color swatches — they have data-colordes or image variations
                const swatches = window.querySelectorAll('.colorVarWrap');
                for (const swatch of swatches) {{
                    const desc = swatch.getAttribute('data-colordes');
                    if (desc === target) {{
                        // Click the image variation inside (triggers changeVariant)
                        const imgVar = swatch.querySelector('.qsmzImageVariation');
                        if (imgVar) {{
                            jQuery(imgVar).trigger('click');
                            return 'clicked image variation: ' + desc;
                        }}
                        // Fallback: click the swatch itself
                        jQuery(swatch).trigger('click');
                        return 'clicked swatch: ' + desc;
                    }}
                }}
                return 'color_not_found: ' + target;
            }}
        """)
        print(f"  {color_result}")
        # Wait for canvas to update with the new color
        await page.wait_for_timeout(5000)

        # Select size in the variations window dropdown
        print(f"Selecting size: {size}")
        size_select = await qf.query_selector("#qsmzVariationsWindow #variantValues1")
        if size_select:
            await size_select.select_option(label=size)
            await page.wait_for_timeout(500)

        # Click OK to confirm color/size selection
        print("Clicking OK...")
        await qf.evaluate("jQuery('#btnselectvariant').trigger('click')")
        await page.wait_for_timeout(3000)

        # Verify color was applied
        active_color = await qf.evaluate("""
            () => {
                const active = document.querySelector('.colorVarWrapActive');
                return active ? (active.getAttribute('data-colordes') || 'unknown') : 'no_active';
            }
        """)
        print(f"  Active color: {active_color}")

        import time

        # --- Step 0b: Switch canvas view to front/back before uploading ---
        # Qstomizer exposes stage thumbnails (#stagemini0 = front, #stagemini1 = back, etc.)
        # Clicking a thumbnail activates the corresponding print area canvas, so the
        # next file upload is placed on that side.
        stage_id = 1 if (placement or "front").lower() == "back" else 0
        print(f"Switching to stage {stage_id} ({placement or 'front'} view)...")
        stage_result = await qf.evaluate(f"""
            () => {{
                const el = document.getElementById('stagemini{stage_id}');
                if (!el) return 'no_stage_{stage_id}';
                if (typeof jQuery !== 'undefined') jQuery(el).trigger('click');
                else el.click();
                return 'clicked_stage_{stage_id}';
            }}
        """)
        print(f"  {stage_result}")
        await page.wait_for_timeout(1500)

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

        # --- Step 2b: Optionally reposition the placed image vertically ---
        # Qstomizer is Konva.js-based (not fabric). It auto-centers the
        # uploaded design in the print area (a dashed Rect on the active
        # stage), which lands the print mid-back. Our marketing mockups show
        # the print higher up (upper-back). `vertical_offset` is a fraction
        # of the PRINT-AREA height — negative = up (toward collar), positive
        # = down, 0 = leave Qstomizer's default alone.
        if vertical_offset or horizontal_offset:
            print(f"Nudging placed image by vertical_offset={vertical_offset} of print-area height, horizontal_offset={horizontal_offset} of print-area width...")
            move_result = await qf.evaluate(f"""
                () => {{
                    const offset = {vertical_offset};
                    const hOffset = {horizontal_offset};
                    if (typeof Konva === 'undefined') return 'no_konva';
                    // The active stage is the one whose layer contains
                    // Group nodes named grupoimage* — the other stages are
                    // passive views (front/back/male/female mockup sides).
                    let activeStage = null;
                    for (const stage of (Konva.stages || [])) {{
                        for (const layer of stage.getLayers()) {{
                            const hasGroup = layer.getChildren().some(c =>
                                c.getClassName() === 'Group' && /^grupoimage/.test(c.id())
                            );
                            if (hasGroup) {{ activeStage = stage; break; }}
                        }}
                        if (activeStage) break;
                    }}
                    if (!activeStage) return 'no_active_stage_with_grupoimage';
                    const layer = activeStage.getLayers()[0];
                    // Print area = dashed Rect (non-full-size)
                    const rect = layer.getChildren().find(c =>
                        c.getClassName() === 'Rect' && c.attrs.dash && c.width() < 790
                    );
                    if (!rect) return 'no_print_area_rect';
                    const printTop = rect.y();
                    const printCenterX = rect.x() + rect.width() / 2;
                    const groups = layer.getChildren().filter(c =>
                        c.getClassName() === 'Group' && /^grupoimage/.test(c.id())
                    );
                    if (groups.length === 0) return 'no_groups_to_move';
                    // Use getClientRect() for the ACTUAL rendered bounds of
                    // the design image (respecting intrinsic aspect ratio +
                    // fit-to-print-area scaling). The Group node's attr
                    // width/height is a bounding box hint and doesn't match
                    // the rendered image for tall multi-line designs.
                    const g = groups[0];
                    const bounds = g.getClientRect({{relativeTo: activeStage}});
                    const requestedDelta = rect.height() * offset;
                    // Clamp: keep the rendered top inside the print area
                    // with a small safety pad so tall designs don't clip
                    // the collar/shoulder zone.
                    const safetyPad = {vertical_safety_pad_px};
                    const maxUpwardMove = -(bounds.y - printTop - safetyPad);
                    const deltaY = offset < 0
                        ? Math.max(requestedDelta, maxUpwardMove)
                        : requestedDelta;
                    // Horizontal centering — Qstomizer's auto-placement can
                    // leave the design slightly off-center horizontally
                    // (especially on the female tee where the print area
                    // Rect doesn't perfectly match the visible tee body).
                    // Compute the delta needed to center the rendered design
                    // on the print-area centre and apply it alongside deltaY.
                    const renderedCenterX = bounds.x + bounds.width / 2;
                    // Auto-center, then apply user-supplied horizontal nudge.
                    // hOffset is a fraction of print-area width (positive = right).
                    const deltaX = (printCenterX - renderedCenterX) + rect.width() * hOffset;
                    groups.forEach(node => {{
                        node.y(node.y() + deltaY);
                        node.x(node.x() + deltaX);
                        node.fire('dragend', {{target: node, evt: null}});
                    }});
                    layer.getChildren().forEach(node => {{
                        if (node.getClassName() === 'Transformer' && typeof node.forceUpdate === 'function') {{
                            try {{ node.forceUpdate(); }} catch (e) {{}}
                        }}
                    }});
                    layer.batchDraw();
                    const clamped = deltaY > requestedDelta;
                    return `moved ${{groups.length}} groups by ${{deltaY.toFixed(1)}}px`
                        + ` (requested ${{requestedDelta.toFixed(1)}}, print_h=${{rect.height()}}`
                        + `, design_h=${{bounds.height.toFixed(0)}}${{clamped ? ', CLAMPED' : ''}})`;
                }}
            """)
            print(f"  {move_result}")
            await page.wait_for_timeout(1500)

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

        # --- Step 8: Build shareable cart permalink with pre-filled checkout ---
        # Fetch cart contents to get Qstomizer properties
        print("Building shareable checkout link...")
        cart_data = await page.evaluate("fetch('/cart.js').then(r => r.json())")

        # When placement=back, leave Qstomizer's `Custom Image:` (Shopify
        # CDN front URL) alone — TJ's cart theme only renders inline-image
        # previews for Shopify CDN URLs, and the front mirror is the only
        # one Qstomizer auto-mirrors. Swapping it to a bigvanet back URL
        # would make the cart page lose its inline thumbnail (renders as
        # plain text instead). Instead, add a NEW visible property
        # `Back Preview:` pointing to the bigvanet back URL so the cart
        # page shows: a) the existing inline (blank-front) thumbnail from
        # `Custom Image:`, b) a separate clickable text URL to the actual
        # back-side mockup. Best of both — preserves the familiar cart UI
        # and exposes the back design at a glance. The print itself is
        # driven by _customorderid, which Qstomizer already bound to the
        # back-side render.
        if (placement or "front").lower() == "back":
            for item_idx, item in enumerate(cart_data.get("items", []), start=1):
                props = item.get("properties") or {}
                back_url = props.get("_customimageback")
                if back_url and props.get("Back Preview:") != back_url:
                    new_props = {**props, "Back Preview:": back_url}
                    js = (
                        "fetch('/cart/change.js', {method: 'POST',"
                        "headers: {'Content-Type': 'application/json'},"
                        f"body: JSON.stringify({{line: {item_idx},"
                        f"properties: {json.dumps(new_props)}}})}})"
                        ".then(r => r.json())"
                    )
                    try:
                        updated = await page.evaluate(js)
                        cart_data = updated
                        print(
                            f"Added line {item_idx} `Back Preview:` property pointing "
                            f"to the back-side bigvanet URL."
                        )
                    except Exception as e:
                        print(f"WARNING: failed to add Back Preview property: {e}")

        # Build the durable cart-rebuild permalink. We tried filling the
        # TJ checkout in this Playwright session and capturing the
        # resulting /checkouts/cn/HASH/... URL (commit 3d98c6b), but
        # modern Shopify checkout stores email/address/shipping per
        # cookie not per URL — opening the captured URL in another
        # browser starts a fresh session, so nothing pre-fills. The
        # approach was reverted on 2026-05-05.
        #
        # Cross-browser, the only fields URL-pre-fill currently honours
        # on modern Shopify checkout are: cart contents + line-item
        # properties + the `?discount=` query param. Email, address,
        # and shipping method must be entered manually OR the buyer can
        # log into a TJ customer account where the email + saved
        # addresses persist server-side.
        checkout_url = _build_checkout_permalink(cart_data, shipping)
        print(f"Checkout permalink: {checkout_url}")

        # Extract Qstomizer mockup image URL (the rendered product preview).
        # For back placement, the design is on the back canvas — front mockup
        # would be an empty tee. Pick the image that actually shows our design.
        # CRITICAL: when placement=back, NEVER fall back to a front URL —
        # the front view is blank for back-printed orders, so the email would
        # display a misleadingly-blank tee. Better to send no preview than
        # the wrong one (the email template handles missing mockup_url).
        mockup_url = None
        is_back = (placement or "front").lower() == "back"
        for item in cart_data.get("items", []):
            props = item.get("properties", {})
            if is_back:
                # Back placement: ONLY use _customimageback (bigvanet — no
                # Shopify CDN mirror exists for back). If missing, leave
                # mockup_url=None and log loud.
                mockup_url = props.get("_customimageback")
                if not mockup_url:
                    print(
                        f"WARNING: placement=back but _customimageback missing in cart "
                        f"properties. props_keys={list(props.keys())}. "
                        f"Email will skip the mockup preview."
                    )
            else:
                # Front placement: prefer the Shopify CDN URL (cached, email-friendly).
                mockup_url = props.get("Custom Image:") or props.get("_customimagefront")
            if mockup_url:
                print(f"Mockup image ({placement or 'front'}): {mockup_url}")
                break

        await browser.close()

    return {"checkout_url": checkout_url, "mockup_url": mockup_url}


def _build_checkout_permalink(cart_data: dict, shipping: dict | None = None) -> str:
    """Return a self-hosted /tj-checkout/{token} URL that rebuilds the cart on
    TJ via form POST to /cart/add (preserving line item properties).

    The native Shopify /cart/VID:QTY?attributes[…] permalink only accepts
    cart-level attributes — Qstomizer's _customimagefront/_customimageback/
    _customorderid live as line item properties, and those are stripped when
    the cart is rebuilt from a permalink. The result is TJ printing the wrong
    image (or none) because the order arrives without the design metadata
    attached to the line item. The /tj-checkout endpoint emits a tiny HTML
    page that auto-POSTs to TJ's /cart/add with properties[…] intact.
    """
    if not cart_data.get("items"):
        return "https://tshirtjunkies.co/cart"

    from app.config import settings
    from app.tj_checkout import save_session

    token = save_session(cart_data, shipping)
    return f"{settings.server_base_url}/tj-checkout/{token}"


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
            await field.click(click_count=3)
            await field.type(str(value), delay=30)
            await page.wait_for_timeout(300)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)
            print(f"  {key}: filled")
        except Exception as e:
            print(f"  {key}: failed ({e})")

    await page.keyboard.press("Tab")
    await page.wait_for_timeout(2000)

    # Select shipping method based on country + OMG-selected method
    if country_code:
        await _select_shipping_method(
            page, country_code, shipping.get("shipping_method", "")
        )

    print("Shipping details filled (stopping before payment)")


async def _select_shipping_method(page, country_code: str, omg_method: str = "") -> None:
    """Select the appropriate shipping method at TJ checkout.

    Looks up SHIPPING_METHOD_MAP[country_code]; if the value is a dict, uses
    omg_method (the shipping method title from the OMG order) to pick the
    matching TJ substring. If no match, falls back to _default.
    """
    entry = SHIPPING_METHOD_MAP.get(country_code, "")
    if isinstance(entry, dict):
        preferred = entry.get(omg_method) or entry.get("_default", "")
    else:
        preferred = entry
    print(
        f"  Selecting shipping method for {country_code} "
        f"(omg={omg_method or 'unknown'} → tj preferred={preferred or 'cheapest'})..."
    )

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
    # Pass preferred as an argument (safe against quotes/unicode in the string).
    result = await page.evaluate("""
        (preferredRaw) => {
            const preferred = (preferredRaw || '').toLowerCase();
            const skip = ['ship', 'pickup', 'credit', 'paypal', 'card', 'viva'];
            const skipIds = ['SHIPPING', 'PICK_UP'];
            const methods = [];

            const radios = document.querySelectorAll('input[type="radio"]');
            for (const radio of radios) {
                if (skipIds.includes(radio.id)) continue;
                const label = document.querySelector('label[for="' + radio.id + '"]');
                if (!label) continue;
                const text = label.textContent.trim();
                if (skip.some(s => text.toLowerCase() === s || text.toLowerCase().includes('credit')))
                    continue;
                methods.push({id: radio.id, text: text, el: radio, kind: 'input'});
            }

            // Also check role="radio" elements (newer Shopify checkout)
            const roleRadios = document.querySelectorAll('[role="radio"]');
            for (const rr of roleRadios) {
                const text = rr.textContent.trim();
                if (skip.some(s => text.toLowerCase() === s)) continue;
                if (text.includes('Credit') || text.includes('PayPal') || text.includes('Viva')) continue;
                if (!methods.some(m => m.text === text)) {
                    methods.push({id: rr.id, text: text, el: rr, kind: 'role'});
                }
            }

            if (methods.length === 0) return 'no_shipping_methods (may be auto-selected)';

            const listing = methods.map(m => m.text.substring(0, 60)).join(' | ');

            // Robust selection: native radios need .checked + change event for
            // React/Shopify state updates to fire (a plain .click() sometimes
            // toggles the underlying control without notifying React, which
            // re-renders and reverts the selection on next paint). For
            // role=radio elements (Shopify's newer custom checkout), click
            // + KeyDown(Space) covers both code paths.
            const selectMethod = (m) => {
                try {
                    if (m.kind === 'input') {
                        // Hit the visible label first — Shopify often hooks
                        // its handler there rather than on the bare input.
                        const lbl = document.querySelector('label[for=\"' + m.el.id + '\"]');
                        if (lbl) lbl.click();
                        m.el.checked = true;
                        m.el.dispatchEvent(new Event('change', {bubbles: true}));
                        m.el.dispatchEvent(new Event('input',  {bubbles: true}));
                    } else {
                        m.el.click();
                        m.el.dispatchEvent(new KeyboardEvent('keydown', {key: ' ', bubbles: true}));
                    }
                } catch (e) {
                    try { m.el.click(); } catch (e2) {}
                }
            };

            // Try to match preferred method
            if (preferred) {
                for (const m of methods) {
                    if (m.text.toLowerCase().includes(preferred)) {
                        selectMethod(m);
                        return 'selected: ' + m.text.substring(0, 80) + ' [from: ' + listing + ']';
                    }
                }
                return 'NO_MATCH for preferred=' + preferred + ' [from: ' + listing + '] — leaving default';
            }

            // No preferred specified: pick first (usually cheapest)
            selectMethod(methods[0]);
            return 'fallback: ' + methods[0].text.substring(0, 80) + ' [from: ' + listing + ']';
        }
    """, preferred)
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
    print(f"\nDone! Checkout: {result['checkout_url']}")
    if result.get("mockup_url"):
        print(f"Mockup: {result['mockup_url']}")
