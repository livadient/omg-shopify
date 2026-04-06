"""Agent 2: Trend Research & Design Creator — researches trends, generates designs, creates products."""
import json
import logging
import shutil
from pathlib import Path

from app.agents import llm_client
from app.agents.agent_email import send_agent_email
from app.agents.approval import approval_url, create_proposal
from app.config import settings

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
PAST_DESIGNS_FILE = DATA_DIR / "past_designs.json"

SYSTEM_PROMPT = """You are a creative director and trend researcher for OMG (omg.com.cy), an online t-shirt brand. Your target market is ages 16-45 globally.

Your job is to research what t-shirt designs are currently trending WORLDWIDE and come up with ORIGINAL design concepts. You must NOT copy or reference any existing copyrighted designs, characters, logos, or trademarks.

Think about:
- Current memes and internet culture (but nothing offensive)
- Global pop culture, viral moments, and trending topics
- Seasonal trends and upcoming events
- Streetwear and graphic tee aesthetics
- Typography and slogan tees
- Minimalist and artistic designs
- Nature, travel, and lifestyle themes

For each concept, provide enough detail for an AI image generator to create the design.

You MUST generate exactly 5 concepts, one of each type:

1. **Cyprus/Local Design** — A design that celebrates Cyprus, its culture, landmarks, beaches, lifestyle, Greek language, or local humor. Something a Cypriot would proudly wear or a tourist would buy as a souvenir. Can include Greek text. Think Ayia Napa, Limassol, halloumi, Cyprus cats, Mediterranean vibes, Cypriot slang, etc.
2. **Global Trend Design** — A design based on whatever is trending RIGHT NOW worldwide. This should have absolutely nothing to do with Cyprus, the Mediterranean, or any specific country. Pure global internet culture, viral moments, or worldwide pop culture trends that would sell anywhere on the planet.
3. **Slogan/Quote** — A bold typographic design featuring a funny, clever, or inspirational quote or slogan. The text IS the design. Think punchy one-liners, witty observations, or motivational statements. NOT Cyprus-related.
4. **Funny Design** — A humorous illustration that makes people laugh or smile. Visual comedy, absurd situations, clever visual puns, meme-inspired (but original) artwork. NOT Cyprus-related.
5. **Geeky/Nerd Design** — Something for tech lovers, gamers, science nerds, programmers, anime fans, or sci-fi enthusiasts. Clever references, pixel art, code jokes, retro gaming, science humor, or fantasy/D&D themes. Must be original (no copyrighted characters). NOT Cyprus-related.

IMPORTANT: Only concept #1 should be Cyprus/Mediterranean themed. Concepts #2-#5 must be globally appealing with NO references to Cyprus, Mediterranean, Greece, or any specific region.

Output as JSON:
{
  "concepts": [
    {
      "name": "Short concept name",
      "type": "cyprus|global-trend|slogan|funny|geeky",
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

Generate exactly 5 concepts — one of each type. All must be original."""


def _load_past_designs() -> list[dict]:
    """Load previously generated design concepts to avoid repetition."""
    if not PAST_DESIGNS_FILE.exists():
        return []
    try:
        return json.loads(PAST_DESIGNS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_past_designs(past: list[dict]) -> None:
    """Save design history. Keep the last 100 entries to avoid unbounded growth."""
    DATA_DIR.mkdir(exist_ok=True)
    past = past[-100:]
    PAST_DESIGNS_FILE.write_text(json.dumps(past, indent=2), encoding="utf-8")


def _record_designs(concepts: list[dict]) -> None:
    """Record newly generated concepts to the history file."""
    from datetime import datetime, timezone
    past = _load_past_designs()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for c in concepts:
        past.append({
            "date": date_str,
            "name": c.get("name", ""),
            "type": c.get("type", ""),
            "description": c.get("description", "")[:200],
        })
    _save_past_designs(past)


def _build_exclusion_prompt() -> str:
    """Build a prompt section listing past designs to avoid."""
    past = _load_past_designs()
    if not past:
        return ""
    # Show the last 50 for context
    recent = past[-50:]
    lines = [f"- [{e['date']}] ({e['type']}) {e['name']}: {e['description'][:120]}" for e in recent]
    return (
        "\n\nIMPORTANT — DO NOT repeat or closely resemble any of these previously generated designs. "
        "Come up with completely fresh, different ideas:\n" + "\n".join(lines)
    )


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
    logger.info("Design Creator: researching current t-shirt trends")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    season = _get_season(now.month)
    date_str = now.strftime('%A, %B %d, %Y')

    # Step 1: Research actual t-shirt trends via web search
    trend_research = await llm_client.generate_with_search(
        system_prompt="You are a fashion trend researcher specializing in graphic t-shirts and streetwear. Provide concise, actionable trend insights.",
        user_prompt=f"""Today is {date_str}. Research the CURRENT trending t-shirt designs for {season} 2026.

Search for:
1. What graphic tee designs are trending right now globally (Etsy, Redbubble, Pinterest, Instagram, TikTok)
2. Popular t-shirt design styles and aesthetics in 2026
3. Trending memes, phrases, or cultural moments that would work on t-shirts
4. What's selling well on the biggest t-shirt marketplaces worldwide

Summarize the top 10-15 specific trends you find, with concrete examples. Focus on what's actually selling NOW globally, not generic advice.""",
        max_tokens=2000,
        temperature=0.5,
    )

    logger.info(f"Trend research complete: {len(trend_research)} chars")

    # Step 2: Generate design concepts informed by real trends
    exclusion_prompt = _build_exclusion_prompt()

    user_prompt = f"""Today's date: {date_str}
Store: OMG (omg.com.cy), Cyprus-based t-shirt brand
Markets: Cyprus, Greece, Europe
Current season: {season}

CURRENT T-SHIRT TRENDS (from real-time research):
{trend_research}

Based on these REAL current trends, generate 5 original, commercially viable design concepts (one per type).
Remember: only concept #1 (cyprus type) should be Cyprus-themed. Concepts #2-#5 must be purely global — no Mediterranean, no Cyprus, no Greece references.
Be specific in the design description so an AI image generator can create it accurately.{exclusion_prompt}"""

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

    # Record concepts to history so they won't be repeated
    _record_designs(concepts)

    # Generate images for each concept
    proposals = []
    for concept in concepts:
        try:
            # Slogan/quote designs use Pillow text rendering; others use DALL-E
            from app.agents.image_client import generate_design, generate_text_design
            if concept.get("type") == "slogan" and concept.get("text_on_shirt"):
                image_path = await generate_text_design(
                    text=concept["text_on_shirt"],
                    style=concept.get("style", "bold modern"),
                )
            else:
                image_path = await generate_design(
                    concept=concept["description"],
                    style=concept.get("style", "bold graphic illustration"),
                )

            from app.agents.image_client import remove_background
            nobg_path = await remove_background(image_path)

            # Store both versions: original and background-removed
            concept["image_path"] = str(image_path)
            concept["image_filename"] = image_path.name
            if nobg_path != image_path:
                concept["image_nobg_path"] = str(nobg_path)
                concept["image_nobg_filename"] = nobg_path.name

            # Pre-cache TShirtJunkies mockups using nobg version (looks better on mockup)
            mockup_image = str(nobg_path) if nobg_path != image_path else str(image_path)
            cached_mockups = await _precache_mockups(mockup_image, concept.get("name", ""))
            concept["cached_mockups"] = cached_mockups

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


async def _precache_mockups(design_image_path: str, concept_name: str) -> dict:
    """Pre-generate TShirtJunkies mockups via Qstomizer so approval is near-instant.

    Returns dict with cached mockup file paths for male and female.
    """
    from app.shopify_product_creator import fetch_mockup_from_qstomizer, download_image

    cached = {}
    proposals_dir = STATIC_DIR / "proposals"
    proposals_dir.mkdir(exist_ok=True)

    for ptype, size in [("male", "L"), ("female", "M")]:
        try:
            logger.info(f"Pre-caching {ptype} mockup for '{concept_name}'...")
            mockup_url = await fetch_mockup_from_qstomizer(design_image_path, ptype, size)
            if mockup_url:
                # Download and save locally so it survives across sessions
                filename = f"mockup_cache_{Path(design_image_path).stem}_{ptype}.png"
                local_path = proposals_dir / filename
                await download_image(mockup_url, local_path)
                cached[ptype] = {"url": mockup_url, "path": str(local_path)}
                logger.info(f"Cached {ptype} mockup: {local_path}")
        except Exception as e:
            logger.warning(f"Failed to pre-cache {ptype} mockup for '{concept_name}': {e}")

    return cached


async def execute_approval(proposal_id: str, version: str = "original") -> dict:
    """Create a Shopify product with male+female variants, fetch TJ mockups, and create mappings.

    version: "original" uses the original image, "nobg" uses the transparent background version.
    """
    from app.agents.approval import get_proposal, update_status
    from app.shopify_product_creator import (
        create_product, create_mappings_for_product,
        fetch_mockup_from_qstomizer, upload_product_image, download_image,
    )

    proposal = get_proposal(proposal_id)
    if not proposal:
        raise ValueError(f"Proposal {proposal_id} not found")

    data = proposal["data"]
    # Pick the right image version
    if version == "nobg" and data.get("image_nobg_path"):
        image_path = Path(data["image_nobg_path"])
        if not image_path.exists():
            image_path = Path(data.get("image_path", "")) if data.get("image_path") else None
    else:
        image_path = Path(data.get("image_path", "")) if data.get("image_path") else None

    # Generate product description
    description = await llm_client.generate(
        system_prompt="Write a short, engaging Shopify product description (2-3 sentences) for a graphic t-shirt. Include the design concept and why customers will love it. Output only the HTML, no JSON.",
        user_prompt=f"Design: {data.get('name', '')} — {data.get('description', '')}",
        max_tokens=500,
        temperature=0.7,
    )

    # Create the product on OMG Shopify (male + female variants, no image yet)
    product = await create_product(
        title=data.get("suggested_title", data.get("name", "New Design Tee")),
        body_html=description,
        tags=data.get("suggested_tags", "graphic tee"),
        image_path=None,  # images added in order below
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

    # Upload images in order: 1) Male mockup, 2) Female mockup, 3) Design artwork
    # Use pre-cached mockups from design generation if available
    cached_mockups = data.get("cached_mockups", {})
    design_path = str(STATIC_DIR / design_filename)
    for ptype, size, label in [("male", "L", "Male"), ("female", "M", "Female")]:
        cached = cached_mockups.get(ptype, {})
        cached_path = Path(cached["path"]) if cached.get("path") else None

        if cached_path and cached_path.exists():
            logger.info(f"Using pre-cached {label} mockup: {cached_path}")
            mockup_path = cached_path
        else:
            logger.info(f"No cached mockup for {label}, fetching from TShirtJunkies...")
            mockup_url = await fetch_mockup_from_qstomizer(design_path, ptype, size)
            if not mockup_url:
                continue
            mockup_path = STATIC_DIR / "proposals" / f"mockup_{handle}_{ptype}.png"
            mockup_path.parent.mkdir(exist_ok=True)
            await download_image(mockup_url, mockup_path)

        try:
            await upload_product_image(product_id, mockup_path, alt=f"{label} T-Shirt Mockup")
            logger.info(f"Uploaded {label} mockup to product {product_id}")
        except Exception as e:
            logger.warning(f"Failed to upload {label} mockup: {e}")

    # Upload the original design artwork as the last image
    if image_path and image_path.exists():
        try:
            await upload_product_image(product_id, image_path, alt="Design Artwork")
            logger.info(f"Uploaded design artwork as last image")
        except Exception as e:
            logger.warning(f"Failed to upload design artwork: {e}")

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
        approve_original = approval_url(p["id"], p["token"], "approve") + "&version=original"
        approve_nobg = approval_url(p["id"], p["token"], "approve") + "&version=nobg"
        reject = approval_url(p["id"], p["token"], "reject")

        # Original image
        image_html = ""
        if data.get("image_path") and Path(data["image_path"]).exists():
            cid = f"design_{p['id']}"
            inline_images[cid] = Path(data["image_path"])
            image_html = f'<img src="cid:{cid}" style="max-width:280px;border-radius:8px;border:1px solid #e5e7eb;" alt="{data.get("name", "design")}">'

        # Background-removed image
        nobg_html = ""
        has_nobg = data.get("image_nobg_path") and Path(data["image_nobg_path"]).exists()
        if has_nobg:
            cid_nobg = f"design_nobg_{p['id']}"
            inline_images[cid_nobg] = Path(data["image_nobg_path"])
            nobg_html = f'<img src="cid:{cid_nobg}" style="max-width:280px;border-radius:8px;border:1px solid #e5e7eb;background:#f3f4f6;" alt="{data.get("name", "design")} (transparent)">'

        error_html = ""
        if data.get("error"):
            error_html = f'<p style="color:#dc2626;font-size:13px;">Generation error: {data["error"]}</p>'

        # Build approval buttons — two approve options if nobg exists
        if has_nobg:
            buttons_html = f"""
            <table style="width:100%;margin-top:8px;"><tr>
                <td style="width:50%;text-align:center;vertical-align:top;">
                    <p style="font-size:12px;color:#6b7280;margin:0 0 6px;">With Background</p>
                    {image_html}
                    <br><a href="{approve_original}" style="display:inline-block;padding:10px 20px;background:#059669;color:white;text-decoration:none;border-radius:6px;font-weight:bold;margin-top:8px;">Approve Original</a>
                </td>
                <td style="width:50%;text-align:center;vertical-align:top;">
                    <p style="font-size:12px;color:#6b7280;margin:0 0 6px;">Transparent (no bg)</p>
                    {nobg_html}
                    <br><a href="{approve_nobg}" style="display:inline-block;padding:10px 20px;background:#7c3aed;color:white;text-decoration:none;border-radius:6px;font-weight:bold;margin-top:8px;">Approve Transparent</a>
                </td>
            </tr></table>
            <div style="text-align:center;margin-top:12px;">
                <a href="{reject}" style="display:inline-block;padding:10px 24px;background:#dc2626;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">Reject</a>
            </div>
            """
        else:
            buttons_html = f"""
            <div style="text-align:center;">
                {image_html}
                <br>
                <a href="{approve_original}" style="display:inline-block;padding:10px 24px;background:#059669;color:white;text-decoration:none;border-radius:6px;font-weight:bold;margin:8px 6px 0;">Approve</a>
                <a href="{reject}" style="display:inline-block;padding:10px 24px;background:#dc2626;color:white;text-decoration:none;border-radius:6px;font-weight:bold;margin:8px 6px 0;">Reject</a>
            </div>
            """

        designs_html += f"""
        <div style="padding:20px;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:16px;">
            {error_html}
            <h3 style="margin:0 0 8px;">{data.get('name', 'Untitled')}</h3>
            <table style="width:100%;margin-bottom:12px;font-size:14px;">
                <tr><td style="color:#6b7280;padding:2px 0;width:100px;">Style:</td><td>{data.get('style', '?')}</td></tr>
                <tr><td style="color:#6b7280;padding:2px 0;">Text:</td><td>{data.get('text_on_shirt', 'None')}</td></tr>
                <tr><td style="color:#6b7280;padding:2px 0;">Type:</td><td>{data.get('product_type', '?')} tee</td></tr>
                <tr><td style="color:#6b7280;padding:2px 0;">Title:</td><td>{data.get('suggested_title', '?')}</td></tr>
                <tr><td style="color:#6b7280;padding:2px 0;">Why:</td><td style="font-size:13px;">{data.get('reasoning', '?')}</td></tr>
            </table>
            {buttons_html}
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
