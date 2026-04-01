"""Automate Qstomizer: upload design image, select size, and add to cart."""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

QSTOMIZER_URLS = {
    "male": "https://tshirtjunkies.co/apps/qstomizer/?qstomizer-product-id=9864408301915",
    "female": "https://tshirtjunkies.co/apps/qstomizer/?qstomizer-product-id=8676301799771",
}

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
DEFAULT_IMAGE = STATIC_DIR / "front_design.png"


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

    Args:
        product_type: "male" or "female"
        size: Size label matching the dropdown (XS, S, M, L, XL, 2XL, 3XL, 4XL, 5XL)
        color: Color name (Black, Navy Blue, Red, Royal Blue, Sport Grey, White)
        image_path: Path to the design image (PNG/JPG)
        quantity: Number of items
        headless: Run browser without GUI
        shipping: Customer shipping details dict with keys:
            email, first_name, last_name, address1, address2, city,
            country_code, zip, phone

    Returns:
        The checkout URL after adding to cart (with shipping pre-filled if provided).
    """
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
        color_result = await qf.evaluate(f"""
            () => {{
                const target = '{color}';
                const swatches = document.querySelectorAll('.colorVarWrap');
                for (const swatch of swatches) {{
                    const name = swatch.querySelector('.colorVarDesc')?.textContent?.trim();
                    if (name === target) {{
                        if (typeof jQuery !== 'undefined') {{
                            jQuery(swatch).trigger('click');
                        }} else {{
                            swatch.click();
                        }}
                        return 'selected: ' + name;
                    }}
                }}
                return 'color_not_found: ' + target;
            }}
        """)
        print(f"  {color_result}")
        await page.wait_for_timeout(2000)

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
                await page.goto("https://tshirtjunkies.co/checkout", wait_until="networkidle", timeout=30000)
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
    field_map = {
        "email": "input[name='email']",
        "first_name": "input[name='firstName'][id^='TextField']",
        "last_name": "input[name='lastName'][id^='TextField']",
        "address1": "input[name='address1'][id='shipping-address1']",
        "address2": "input[name='address2'][id^='TextField']",
        "city": "input[name='city']",
        "zip": "input[name='postalCode'][id^='TextField']",
        "phone": "input[name='phone'][id^='TextField']",
    }

    for key, selector in field_map.items():
        value = shipping.get(key, "")
        if not value:
            continue
        try:
            field = await page.wait_for_selector(selector, timeout=5000)
            await field.click()
            await field.fill(str(value))
            await page.wait_for_timeout(300)
            print(f"  {key}: filled")
        except Exception as e:
            print(f"  {key}: failed ({e})")

    # Select country
    country_code = shipping.get("country_code", "")
    if country_code:
        try:
            country_select = await page.wait_for_selector(
                "select[name='countryCode']", timeout=5000
            )
            await country_select.select_option(value=country_code)
            await page.wait_for_timeout(1000)
            print(f"  country: {country_code}")
        except Exception as e:
            print(f"  country: failed ({e})")

    print("Shipping details filled (stopping before payment)")


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
