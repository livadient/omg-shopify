"""Create 'Professional Overthinker' text-only t-shirt on OMG store."""
import asyncio
import logging
import shutil
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def main():
    from app.agents.image_client import generate_text_design
    from app.agents import llm_client
    from app.shopify_product_creator import (
        create_product, create_mappings_for_product,
        fetch_mockup_from_qstomizer, upload_product_image, download_image,
    )

    # Step 1: Generate the text design
    logger.info("Generating text design: PROFESSIONAL OVERTHINKER")
    image_path = await generate_text_design(
        text="PROFESSIONAL\nOVERTHINKER",
        style="bold modern",
    )
    logger.info(f"Design saved: {image_path}")

    # Step 2: Generate product description via Claude
    description = await llm_client.generate(
        system_prompt="Write a short, engaging Shopify product description (2-3 sentences) for a graphic t-shirt. Include the design concept and why customers will love it. Output only the HTML, no JSON.",
        user_prompt="Design: Professional Overthinker — A bold, minimalist typography tee for the chronic overthinker. Perfect for anyone whose brain never stops analyzing every possible outcome.",
        max_tokens=500,
        temperature=0.7,
    )
    logger.info(f"Description: {description[:100]}...")

    # Step 3: Create product on OMG Shopify
    logger.info("Creating product on OMG Shopify...")
    product = await create_product(
        title="Professional Overthinker — Bold Typography Tee",
        body_html=description,
        tags="slogan,typography,funny,overthinker,humor,text tee,minimalist",
        image_path=None,
        published=True,
    )
    product_id = product.get("id")
    handle = product.get("handle", "")
    logger.info(f"Product created: {product_id} — handle: {handle}")

    # Step 4: Copy design to static/ for Playwright
    design_filename = f"design_{handle}.png"
    dest = STATIC_DIR / design_filename
    shutil.copy2(image_path, dest)
    logger.info(f"Design copied to {dest}")

    # Step 5: Create mappings (OMG → TShirtJunkies)
    logger.info("Creating product mappings...")
    mappings = await create_mappings_for_product(
        omg_product=product,
        design_image=design_filename,
    )
    logger.info(f"Created {len(mappings)} mappings")

    # Step 6: Fetch mockups from TShirtJunkies and upload images
    proposals_dir = STATIC_DIR / "proposals"
    proposals_dir.mkdir(exist_ok=True)

    for ptype, size, label in [("male", "L", "Male"), ("female", "M", "Female")]:
        logger.info(f"Fetching {label} mockup from TShirtJunkies...")
        mockup_url = await fetch_mockup_from_qstomizer(str(dest), ptype, size)
        if not mockup_url:
            logger.warning(f"No {label} mockup returned")
            continue

        mockup_path = proposals_dir / f"mockup_{handle}_{ptype}.png"
        await download_image(mockup_url, mockup_path)

        try:
            await upload_product_image(product_id, mockup_path, alt=f"{label} T-Shirt Mockup")
            logger.info(f"Uploaded {label} mockup")
        except Exception as e:
            logger.warning(f"Failed to upload {label} mockup: {e}")

    # Step 7: Upload the design artwork as the last image
    try:
        await upload_product_image(product_id, image_path, alt="Design Artwork")
        logger.info("Uploaded design artwork")
    except Exception as e:
        logger.warning(f"Failed to upload design artwork: {e}")

    # Done!
    print(f"\n{'='*60}")
    print(f"Product created successfully!")
    print(f"  ID:     {product_id}")
    print(f"  Handle: {handle}")
    print(f"  URL:    https://omg.com.cy/products/{handle}")
    print(f"  Admin:  https://admin.shopify.com/store/52922c-2/products/{product_id}")
    print(f"  Mappings: {len(mappings)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
