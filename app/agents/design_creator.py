"""Agent 2: Trend Research & Design Creator — researches trends, generates designs, creates products."""
import logging
import shutil
from pathlib import Path

from app.agents import llm_client
from app.agents.agent_email import send_agent_email
from app.agents.approval import approval_url, create_proposal
from app.config import settings

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

SYSTEM_PROMPT = """You are a creative director and trend researcher for OMG (omg.com.cy), a Cyprus-based online t-shirt brand. Your target market is young adults (18-35) in Cyprus, Greece, and Europe.

Your job is to research what t-shirt designs are currently trending and come up with ORIGINAL design concepts. You must NOT copy or reference any existing copyrighted designs, characters, logos, or trademarks.

Think about:
- Current memes and internet culture (but nothing offensive)
- Mediterranean/Greek/Cypriot cultural themes and humor
- Seasonal trends and upcoming events
- Streetwear and graphic tee aesthetics
- Typography and slogan tees
- Minimalist and artistic designs
- Nature, travel, and lifestyle themes

For each concept, provide enough detail for an AI image generator to create the design.

Output as JSON:
{
  "concepts": [
    {
      "name": "Short concept name",
      "description": "Detailed description of the design for image generation",
      "style": "art style (e.g., minimalist vector, vintage retro, bold graphic, watercolor, line art)",
      "text_on_shirt": "Any text/slogan to include (or empty string if none)",
      "target_audience": "male|female|unisex",
      "product_type": "male|female",
      "suggested_title": "Product title for the store",
      "suggested_tags": "comma,separated,tags",
      "reasoning": "Why this design would sell well right now"
    }
  ]
}

Generate exactly 3 concepts with diverse styles and themes."""


async def research_trends() -> list[dict]:
    """Research trends, generate designs, and create proposals for approval."""
    try:
        return await _research_trends_impl()
    except Exception as e:
        logger.exception("Design Creator failed")
        from app.agents.agent_email import send_error_email
        await send_error_email("Design Creator", e)
        raise


async def _research_trends_impl() -> list[dict]:
    """Internal implementation."""
    logger.info("Design Creator: researching trends and generating concepts")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    user_prompt = f"""Today's date: {now.strftime('%A, %B %d, %Y')}
Store: OMG (omg.com.cy), Cyprus-based t-shirt brand
Markets: Cyprus, Greece, Europe
Current season: {_get_season(now.month)}

Research current t-shirt design trends and generate 3 original, commercially viable design concepts.
Each concept should be different in style and appeal to different segments of our audience.
Be specific in the design description so an AI image generator can create it accurately."""

    # Get design concepts from Claude
    result = await llm_client.generate_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=3000,
        temperature=0.9,
    )

    concepts = result.get("concepts", [])
    if not concepts:
        logger.warning("No design concepts generated")
        return []

    # Generate images for each concept
    proposals = []
    for concept in concepts:
        try:
            # Generate the design image
            from app.agents.image_client import generate_design, remove_background
            image_path = await generate_design(
                concept=concept["description"],
                style=concept.get("style", "bold graphic illustration"),
            )

            # Try background removal for print-ready version
            clean_path = await remove_background(image_path)

            # Create proposal
            concept["image_path"] = str(clean_path)
            concept["image_filename"] = clean_path.name
            proposal = create_proposal("design", concept)
            proposals.append(proposal)

        except Exception as e:
            logger.error(f"Failed to generate design for '{concept.get('name', '?')}': {e}")
            concept["error"] = str(e)
            concept["image_path"] = ""
            concept["image_filename"] = ""
            proposal = create_proposal("design", concept)
            proposals.append(proposal)

    # Send email with all designs
    if proposals:
        await _send_design_email(proposals)

    logger.info(f"Design Creator: {len(proposals)} proposals created")
    return proposals


async def execute_approval(proposal_id: str) -> dict:
    """Create a Shopify product with male+female variants, fetch TJ mockups, and create mappings."""
    from app.agents.approval import get_proposal, update_status
    from app.shopify_product_creator import (
        create_product, create_mappings_for_product,
        fetch_mockup_from_qstomizer, upload_product_image, download_image,
    )

    proposal = get_proposal(proposal_id)
    if not proposal:
        raise ValueError(f"Proposal {proposal_id} not found")

    data = proposal["data"]
    image_path = Path(data.get("image_path", "")) if data.get("image_path") else None

    # Generate product description
    description = await llm_client.generate(
        system_prompt="Write a short, engaging Shopify product description (2-3 sentences) for a graphic t-shirt. Include the design concept and why customers will love it. Output only the HTML, no JSON.",
        user_prompt=f"Design: {data.get('name', '')} — {data.get('description', '')}",
        max_tokens=500,
        temperature=0.7,
    )

    # Create the product on OMG Shopify (male + female variants in one product)
    product = await create_product(
        title=data.get("suggested_title", data.get("name", "New Design Tee")),
        body_html=description,
        tags=data.get("suggested_tags", "graphic tee"),
        image_path=image_path,
        published=True,
    )

    product_id = product.get("id")
    handle = product.get("handle", "")

    # Copy design image to static/ for Playwright automation
    design_filename = f"design_{handle}.png"
    if image_path and image_path.exists():
        dest = STATIC_DIR / design_filename
        shutil.copy2(image_path, dest)
        logger.info(f"Design image copied to {dest}")

    # Create mappings (male → TJ Classic Tee, female → TJ Women's Tee)
    mappings = await create_mappings_for_product(
        omg_product=product,
        design_image=design_filename,
    )

    # Fetch TJ mockup images via Qstomizer and upload to OMG product
    design_path = str(STATIC_DIR / design_filename)
    for ptype, size, label in [("male", "L", "Male"), ("female", "M", "Female")]:
        logger.info(f"Fetching {label} mockup from TShirtJunkies...")
        mockup_url = await fetch_mockup_from_qstomizer(design_path, ptype, size)
        if mockup_url:
            try:
                mockup_path = STATIC_DIR / "proposals" / f"mockup_{handle}_{ptype}.png"
                mockup_path.parent.mkdir(exist_ok=True)
                await download_image(mockup_url, mockup_path)
                await upload_product_image(product_id, mockup_path, alt=f"{label} T-Shirt Mockup")
                logger.info(f"Uploaded {label} mockup to product {product_id}")
            except Exception as e:
                logger.warning(f"Failed to upload {label} mockup: {e}")

    update_status(proposal_id, "approved")
    logger.info(f"Design approved: product {product_id} created with {len(mappings)} mappings + mockups")

    return {
        "product_id": product_id,
        "product_handle": handle,
        "product_url": f"https://omg.com.cy/products/{handle}",
        "mappings": mappings,
    }


async def _send_design_email(proposals: list[dict]) -> None:
    """Send email with all design proposals for review."""
    inline_images: dict[str, Path] = {}
    designs_html = ""
    for p in proposals:
        data = p["data"]
        approve = approval_url(p["id"], p["token"], "approve")
        reject = approval_url(p["id"], p["token"], "reject")

        image_html = ""
        if data.get("image_path") and Path(data["image_path"]).exists():
            cid = f"design_{p['id']}"
            inline_images[cid] = Path(data["image_path"])
            image_html = f'<img src="cid:{cid}" style="max-width:300px;border-radius:8px;margin-bottom:12px;" alt="{data.get("name", "design")}">'

        error_html = ""
        if data.get("error"):
            error_html = f'<p style="color:#dc2626;font-size:13px;">Generation error: {data["error"]}</p>'

        designs_html += f"""
        <div style="padding:20px;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:16px;">
            {image_html}
            {error_html}
            <h3 style="margin:0 0 8px;">{data.get('name', 'Untitled')}</h3>
            <table style="width:100%;margin-bottom:12px;font-size:14px;">
                <tr><td style="color:#6b7280;padding:2px 0;width:100px;">Style:</td><td>{data.get('style', '?')}</td></tr>
                <tr><td style="color:#6b7280;padding:2px 0;">Text:</td><td>{data.get('text_on_shirt', 'None')}</td></tr>
                <tr><td style="color:#6b7280;padding:2px 0;">Type:</td><td>{data.get('product_type', '?')} tee</td></tr>
                <tr><td style="color:#6b7280;padding:2px 0;">Title:</td><td>{data.get('suggested_title', '?')}</td></tr>
                <tr><td style="color:#6b7280;padding:2px 0;">Why:</td><td style="font-size:13px;">{data.get('reasoning', '?')}</td></tr>
            </table>
            <div style="text-align:center;">
                <a href="{approve}" style="display:inline-block;padding:10px 24px;background:#059669;color:white;text-decoration:none;border-radius:6px;font-weight:bold;margin:0 6px;">Approve</a>
                <a href="{reject}" style="display:inline-block;padding:10px 24px;background:#dc2626;color:white;text-decoration:none;border-radius:6px;font-weight:bold;margin:0 6px;">Reject</a>
            </div>
        </div>
        """

    html = f"""
    <div style="font-family:sans-serif;max-width:650px;margin:0 auto;">
        <div style="background:#7c3aed;color:white;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">New T-Shirt Design Concepts</h2>
            <p style="margin:4px 0 0;opacity:0.9;">{len(proposals)} designs ready for review</p>
        </div>
        <div style="padding:20px;background:#f9fafb;">
            {designs_html}
        </div>
        <div style="padding:12px;text-align:center;color:#9ca3af;font-size:12px;">
            Generated by OMG AI Design Creator
        </div>
    </div>
    """

    await send_agent_email(
        subject=f"[OMG Design] {len(proposals)} new t-shirt concepts ready for review",
        html_body=html,
        inline_images=inline_images,
    )


def _get_season(month: int) -> str:
    if month in (3, 4, 5):
        return "Spring"
    if month in (6, 7, 8):
        return "Summer"
    if month in (9, 10, 11):
        return "Autumn"
    return "Winter"
