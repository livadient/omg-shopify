"""Regenerate one female-back TJ mockup using the updated Konva reposition
(adds horizontal centering on top of the upper-back vertical offset). Saves
to static/proposals/_sample_centered_female/ for visual comparison against
the current cached version which has the design skewed left.

Run:
  .venv/Scripts/python -m scripts.sample_centered_female_tj
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def main():
    from app.qstomizer_automation import customize_and_add_to_cart
    from app.shopify_product_creator import download_image

    # Use the professional-overthinker design — it's a typography tee where
    # the centering issue is most visible.
    design = ROOT / "static/proposals/professional_overthinker_bold_typography_tee/design_transparent_tj.png"
    if not design.exists():
        # fallback: tight-crop the static design PNG
        from PIL import Image
        src = ROOT / "static/design_professional-overthinker-bold-typography-tee.png"
        img = Image.open(src).convert("RGBA")
        bbox = img.getbbox()
        design.parent.mkdir(parents=True, exist_ok=True)
        (img.crop(bbox) if bbox else img).save(design, "PNG")

    out_dir = ROOT / "static" / "proposals" / "_sample_centered_female"
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Running Qstomizer (female back, horizontal-centered Konva reposition)...")
    result = await customize_and_add_to_cart(
        product_type="female",
        size="M",
        color="White",
        image_path=str(design),
        quantity=1,
        headless=True,
        placement="back",
        # vertical_offset defaults to -0.25 (upper back) and the JS now also
        # adds horizontal centering on top of it.
    )
    mockup_url = result.get("mockup_url")
    if mockup_url:
        out = out_dir / "female_back_centered.png"
        await download_image(mockup_url, out)
        logger.info(f"Saved: {out}")
    else:
        logger.error("No mockup URL returned")


if __name__ == "__main__":
    asyncio.run(main())
