"""Fix the live 'Σέξι Μαδαφάκα' product on Shopify — strip accents everywhere.

1. Regenerate a clean Pillow design image (uppercase ΣΕΞΙ / ΜΑΔΑΦΑΚΑ, no tonos).
2. Update the Shopify product: title, body, handle → accent-free.
3. Replace the Design Artwork image on the product.
4. Regenerate front + back mockups for male & female via Qstomizer.
5. Re-upload all 4 mockups linked to their variant subsets.
6. Delete the old gibberish mockup/design images on Shopify.
"""
import asyncio
import base64
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import STATIC_DIR
from app.shopify_product_creator import (
    _admin_url,
    _headers,
    upload_product_image,
    fetch_mockup_from_qstomizer,
)

PRODUCT_ID = 15952809132412
OLD_HANDLE = "σέξι-μαδαφάκα-greek-slogan-tee"
NEW_HANDLE = "sexi-madafaka-greek-slogan-tee"
NEW_TITLE = "Sexi Madafaka Greek Slogan Tee"
NEW_BODY_HTML = (
    "<p>Make a bold statement with this edgy Greek typographic tee featuring "
    "<strong>ΣΕΞΙ ΜΑΔΑΦΑΚΑ</strong> in striking lettering. This provocative "
    "design combines Greek street attitude with clean bold typography — "
    "perfect for those who love a little cheek with their style.</p>"
)
NEW_DESIGN_FILENAME = f"design_{NEW_HANDLE}.png"


def regenerate_clean_design() -> Path:
    """Create a clean Pillow text design — uppercase Greek, no tonos, no apostrophes."""
    from PIL import Image, ImageDraw, ImageFont

    text = "ΣΕΞΙ\nΜΑΔΑΦΑΚΑ"
    size = (1024, 1024)
    color_hex = "#000000"

    # Pick a Greek-capable bold font
    font_candidates = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    font_path = next((f for f in font_candidates if Path(f).exists()), None)

    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    lines = text.split("\n")

    # Find largest font size that fits
    margin = 80
    max_w = size[0] - margin * 2
    max_h = size[1] - margin * 2
    font_size = 240
    final_font = None
    while font_size > 30:
        test_font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
        line_bboxes = [draw.textbbox((0, 0), line, font=test_font) for line in lines]
        total_w = max(bb[2] - bb[0] for bb in line_bboxes)
        line_height = max(bb[3] - bb[1] for bb in line_bboxes)
        total_h = line_height * len(lines) + (len(lines) - 1) * (font_size * 0.3)
        if total_w <= max_w and total_h <= max_h:
            final_font = test_font
            break
        font_size -= 4

    # Render
    line_bboxes = [draw.textbbox((0, 0), line, font=final_font) for line in lines]
    line_height = max(bb[3] - bb[1] for bb in line_bboxes)
    spacing = int(font_size * 0.3)
    total_height = line_height * len(lines) + spacing * (len(lines) - 1)
    y_start = (size[1] - total_height) // 2

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=final_font)
        text_w = bbox[2] - bbox[0]
        x = (size[0] - text_w) // 2
        y = y_start + i * (line_height + spacing)
        draw.text((x, y), line, fill=color_hex, font=final_font)

    out_path = STATIC_DIR / NEW_DESIGN_FILENAME
    img.save(out_path, "PNG")
    print(f"Saved clean design: {out_path} (font_size={font_size}pt)")
    return out_path


async def fetch_product() -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(_admin_url(f"products/{PRODUCT_ID}.json"), headers=_headers())
        r.raise_for_status()
        return r.json()["product"]


async def update_product_metadata():
    """PUT /products/{id}.json — change title, handle, body."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.put(
            _admin_url(f"products/{PRODUCT_ID}.json"),
            headers=_headers(),
            json={
                "product": {
                    "id": PRODUCT_ID,
                    "title": NEW_TITLE,
                    "handle": NEW_HANDLE,
                    "body_html": NEW_BODY_HTML,
                }
            },
        )
        if r.status_code >= 400:
            print(f"Update failed: {r.status_code} {r.text[:300]}")
            r.raise_for_status()
        print(f"Updated title → {NEW_TITLE!r}")
        print(f"Updated handle → {NEW_HANDLE!r}")


async def delete_image(image_id: int):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.delete(
            _admin_url(f"products/{PRODUCT_ID}/images/{image_id}.json"), headers=_headers(),
        )
        print(f"  Deleted image {image_id}: {r.status_code}")


async def main():
    print("=== Step 1: Regenerate clean design ===")
    new_design_path = regenerate_clean_design()

    print("\n=== Step 2: Fetch product + remember old image IDs ===")
    product = await fetch_product()
    old_images = {img["id"]: img.get("alt", "") for img in product.get("images", [])}
    print(f"Found {len(old_images)} existing images to replace: {old_images}")

    print("\n=== Step 3: Update title/handle/body ===")
    await update_product_metadata()

    print("\n=== Step 4: Delete old images ===")
    for img_id in old_images:
        await delete_image(img_id)

    print("\n=== Step 5: Re-fetch product for fresh variant IDs ===")
    product = await fetch_product()

    # Group variant IDs by (gender, placement)
    ids_by_key: dict[tuple[str, str], list[int]] = {
        ("male", "front"): [], ("male", "back"): [],
        ("female", "front"): [], ("female", "back"): [],
    }
    for v in product.get("variants", []):
        g = (v.get("option1") or "").lower()
        p = (v.get("option2") or "").lower()
        gk = "female" if "female" in g else ("male" if "male" in g else None)
        pk = "back" if p == "back" else ("front" if p == "front" else None)
        if gk and pk:
            ids_by_key[(gk, pk)].append(v["id"])
    print(f"Variant groups: {[(k, len(v)) for k, v in ids_by_key.items()]}")

    print("\n=== Step 6: Generate + upload 4 mockups ===")
    design_path_str = str(new_design_path)
    combos = [
        ("male", "L", "front", "Male Front"),
        ("male", "L", "back", "Male Back"),
        ("female", "M", "front", "Female Front"),
        ("female", "M", "back", "Female Back"),
    ]
    for ptype, size, placement, label in combos:
        target_ids = ids_by_key.get((ptype, placement)) or []
        if not target_ids:
            print(f"  [{label}] no variants — skip")
            continue
        print(f"\n[{label}] fetching mockup from Qstomizer...")
        mockup_url = await fetch_mockup_from_qstomizer(
            design_path_str, ptype, size, placement=placement,
        )
        if not mockup_url:
            print(f"  [{label}] no mockup URL")
            continue
        mockup_path = STATIC_DIR / "proposals" / f"mockup_{NEW_HANDLE}_{ptype}_{placement}.png"
        mockup_path.parent.mkdir(exist_ok=True)
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(mockup_url, follow_redirects=True)
            r.raise_for_status()
            mockup_path.write_bytes(r.content)
        await upload_product_image(
            PRODUCT_ID, mockup_path, alt=f"{label} T-Shirt Mockup", variant_ids=target_ids,
        )
        print(f"  [{label}] uploaded, linked to {len(target_ids)} variants")

    print("\n=== Step 7: Upload design artwork as last image ===")
    await upload_product_image(PRODUCT_ID, new_design_path, alt="Design Artwork")

    print("\n=== Step 8: Regenerate mapping with new handle + design filename ===")
    from app.shopify_product_creator import create_mappings_for_product
    product = await fetch_product()
    await create_mappings_for_product(omg_product=product, design_image=NEW_DESIGN_FILENAME)

    print("\nDONE — product fully cleaned up.")


asyncio.run(main())
