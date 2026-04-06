# Qstomizer Automation

## Purpose

Automate the TShirtJunkies Qstomizer product customization flow using Playwright. The automation uploads a design image, selects color and size, adds the customized product to cart, and retrieves the cart data needed to build shareable checkout links.

**File:** `app/qstomizer_automation.py`

## Qstomizer Product IDs

| Product | Qstomizer Product ID | URL |
|---------|---------------------|-----|
| Classic Tee (Male, up to 5XL) | `9864408301915` | `https://tshirtjunkies.co/apps/qstomizer/?qstomizer-product-id=9864408301915` |
| Women's T-Shirt | `8676301799771` | `https://tshirtjunkies.co/apps/qstomizer/?qstomizer-product-id=8676301799771` |

## 13-Step Automation Process

1. **Open Qstomizer page** -- Navigate to the Qstomizer URL for the product type. Viewport must be 1920x1080 for canvas rendering.
2. **Hide overlapping elements** -- Remove `#qsmzTextWindow` (text editor overlay) and `.shopify-section-header-sticky` (site header) to prevent click interference.
3. **Open Colors/Size window** -- Click `#btnvariations` to open the variations selection panel.
4. **Select color** -- Find the `.colorVarWrap[data-colordes]` swatch matching the target color, click its `.qsmzImageVariation` child via jQuery. Wait for canvas update.
5. **Select size in variations window** -- Set value on `#qsmzVariationsWindow #variantValues1` dropdown.
6. **Confirm selection** -- Click `#btnselectvariant` (OK button) to apply color/size.
7. **Upload design image** -- Click `#btnUploadImage`, set file on `input[name='qsmz-file']`, dispatch `change` event.
8. **Wait for upload + processing** -- Wait for `#msgUploading` then `#msgProcessing` to appear and disappear (up to 60s).
9. **Place image on canvas** -- Click last `.imagesubcontainer` thumbnail via jQuery to place the uploaded image onto the product canvas.
10. **Select size (main dropdown)** -- Set `#variantValues1` dropdown to the target size.
11. **Click ORDER NOW** -- Trigger click on `#addtocart` via jQuery. A quantity window appears.
12. **Set quantity in quantity window** -- Zero out all `.infoQty` inputs, then set the correct size's input by matching `.Rtable-cell` labels. Click the ADD TO CART button in the quantity window. Handle disclaimer popup if it appears.
13. **Wait for save + redirect** -- Wait for "Saving Data..." to finish, then redirect to `/cart`. Fetch `/cart.js` to get Qstomizer properties (`_customorderid`, `_customorderkey`, `_customimagefront`). Build shareable cart permalink with checkout pre-fill params.

## Windows Event Loop Workaround

Playwright requires subprocess support, which Windows' `SelectorEventLoop` (used by uvicorn) does not provide. The solution:

```
customize_and_add_to_cart()          # async, called by webhook handler
  └── loop.run_in_executor()         # submits to ThreadPoolExecutor(max_workers=2)
      └── _run_playwright_in_thread()  # creates new ProactorEventLoop in thread
          └── _customize_and_add_to_cart_impl()  # actual Playwright code
```

- `_playwright_executor` is a `ThreadPoolExecutor(max_workers=2)` -- limits concurrent Playwright instances
- Each thread creates its own `asyncio.ProactorEventLoop` (Windows) or `asyncio.new_event_loop()` (Linux)
- Callers use `await customize_and_add_to_cart(...)` as a normal async function

## Color Selection

Qstomizer stores color as metadata (not as a Shopify variant -- TJ products only have Size as a variant option). The automation:

1. Opens the variations window (`#btnvariations`)
2. Finds the `.colorVarWrap` swatch with matching `data-colordes` attribute
3. Clicks the `.qsmzImageVariation` image inside via jQuery (triggers `changeVariant`)
4. Waits for canvas to update with the new color background
5. Confirms with OK button (`#btnselectvariant`)

The canvas/mockup always shows the selected color. The actual color for printing is stored in Qstomizer's backend via `_customorderid`.

**Available colors:** Black, Navy Blue, Red, Royal Blue, Sport Grey, White (default: White)

## Shipping Method Map

Defined in `SHIPPING_METHOD_MAP` at module level:

```python
SHIPPING_METHOD_MAP = {
    "CY": "Travel Express",    # must actively select; not the default
    "GR": "Geniki",            # first option, auto-selected
    "FR": "Postal",            # only option, auto-selected
}
```

For unmapped countries, the default (first/cheapest) shipping option is kept.

## Cart Permalink Builder

`_build_checkout_permalink(cart_data, shipping)` constructs a URL in the format:

```
https://tshirtjunkies.co/cart/VARIANT_ID:QTY?checkout[email]=...&checkout[shipping_address][first_name]=...&attributes[_customorderid]=...
```

Shipping fields mapped: `email`, `first_name`, `last_name`, `address1`, `address2`, `city`, `zip`, `country_code`, `phone`.

Qstomizer line item properties are added as `attributes[KEY]=VALUE` params.

## Manual Usage

```bash
.venv/Scripts/python -m app.qstomizer_automation male L White
```

Arguments: `product_type` (male/female), `size` (S-5XL), `color` (default: White).

Runs with `headless=False` so you can watch the browser automation.

## Return Value

`customize_and_add_to_cart()` returns a dict:

```python
{
    "checkout_url": "https://tshirtjunkies.co/cart/...",
    "mockup_url": "https://cdn.shopify.com/..."  # or None
}
```

The `mockup_url` is the Qstomizer-rendered product preview image (hosted on Shopify CDN, works in emails).
