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

# Extra recipients who should receive Mango's emails (in addition to settings.email_recipients)
EXTRA_RECIPIENTS = ["kyriaki_mara@yahoo.com"]

SYSTEM_PROMPT_BASE = """You are a creative director and trend researcher for OMG (omg.com.cy), an online t-shirt brand. Your target market is ages 16-45 globally.

Your job is to research what t-shirt designs are currently trending WORLDWIDE and come up with ORIGINAL design concepts. You must NOT copy or reference any existing copyrighted designs, characters, logos, or trademarks.

Think about:
- Current memes and internet culture (but nothing offensive)
- Global pop culture, viral moments, and trending topics
- Seasonal trends and upcoming events
- Streetwear and graphic tee aesthetics
- Typography and slogan tees
- Minimalist and artistic designs
- Nature, travel, and lifestyle themes

For each concept, provide enough detail for an AI image generator to create the design."""

CONCEPT_TYPES_CORE = """1. **Cyprus/Local Design** — A design that celebrates Cyprus, its culture, landmarks, beaches, lifestyle, Greek language, or local humor. Something a Cypriot would proudly wear or a tourist would buy as a souvenir. Can include Greek text. Think Ayia Napa, Limassol, halloumi, Cyprus cats, Mediterranean vibes, Cypriot slang, etc.
2. **Global Trend Design** — A design based on whatever is trending RIGHT NOW worldwide. This should have absolutely nothing to do with Cyprus, the Mediterranean, or any specific country. Pure global internet culture, viral moments, or worldwide pop culture trends that would sell anywhere on the planet.
3. **Slogan/Quote** — A bold typographic design where the text IS the design. LEAN HUMOROUS most of the time — punchy one-liners, witty observations, deadpan or sarcastic takes, absurd statements, dark humor, self-deprecating jokes. The kind of slogan someone would actually screenshot and send to a friend. Occasionally (maybe 1 in 4 runs) do a clever motivational/inspirational one for variety. Avoid generic "live laugh love" energy. NOT Cyprus-related.
4. **Funny Design** — A humorous illustration that makes people laugh or smile. Visual comedy, absurd situations, clever visual puns, meme-inspired (but original) artwork. NOT Cyprus-related.
5. **Geeky/Nerd Design** — Something for tech lovers, gamers, science nerds, programmers, anime fans, or sci-fi enthusiasts. Clever references, pixel art, code jokes, retro gaming, science humor, or fantasy/D&D themes. Must be original (no copyrighted characters). NOT Cyprus-related."""

CONCEPT_TYPE_SUMMER = """6. **Summer/Vacation Vibes** — A bright, optimistic summer-energy design. Think palm trees, sunsets, ocean waves, beach cocktails, retro postcards, surf culture, sun rays, swimming, ice cream, flip-flops, "summer mode", sunscreen jokes, beach reading, vacation vibes. Bold saturated colors that scream warm weather. Should appeal to anyone planning a holiday or wishing they were on one. Can be Mediterranean-flavored (it's our home turf) or universal beach/summer vibes — your choice. Keep it fun, not generic stock-art."""

CONCEPT_TYPE_FEMININE = """**Trending Feminine Tee** — A design aimed squarely at women, riding the current feminine fashion zeitgeist. Lean into trending aesthetics like coquette / ballet-core (bows, ribbons, pearls, ballet pinks), cottagecore (floral, romantic, pastoral), "that girl" / "clean girl" (minimalist, soft pastels), soft girl, dreamy / ethereal, vintage romance, butterflies, cherries, hearts, delicate hand-drawn florals, retro femme, or whatever feminine micro-trend is hot RIGHT NOW. Soft palettes (blush, sage, cream, lavender, butter yellow) but can also do bold feminine (hot pink, red, black-and-pink). Should feel something a 16-30 year old woman would screenshot from a Pinterest board. NOT generic — pick a specific aesthetic and commit to it. NOT Cyprus-related. CRITICAL: for this concept the `target_audience` MUST be `female` AND `suggested_tags` MUST include the exact word `feminine` (so it auto-routes to the curated Women's Graphic Tees collection) plus the specific aesthetic name you picked (e.g. `feminine,coquette,bows,ribbons` or `feminine,cottagecore,floral,romantic`)."""

CONCEPT_TYPE_LOVE_CYPRUS = """**Love Cyprus (Tourist / Souvenir)** — An ELEVATED Cyprus souvenir tee aimed at tourists, expats, and people who fell in love with the island. The brief: classier than the cliché "I ❤ CYPRUS" gift-shop tee — no big hearts, no Comic Sans, no airport-merch energy. Think instead: refined takes on the Cyprus flag (minimalist, vintage stamp, embroidered-look, tonal/monochrome treatments), elegant single-line art of iconic landmarks (Petra tou Romiou, Kyrenia castle silhouette, Aphrodite, Curium amphitheatre, traditional windmills, Troodos peaks), tasteful "EST. CYPRUS" / "CYPRUS — EST. ANTIQUITY" / "MEDITERRANEAN, CYPRUS" typographic marks, vintage postcard aesthetics, Mediterranean color palettes (terracotta, olive, sea-blue, sand, cream), retro travel-poster style, refined map outlines, Greek-letter wordmarks done with care. Something a 30-something traveller would actually wear back home, not stuff in a drawer. This concept is DIFFERENT from concept #1 (cyprus) which targets locals with insider humor and Cypriot slang — Love Cyprus targets OUTSIDERS who want a beautiful keepsake. CRITICAL: `suggested_tags` MUST include `cyprus,tourist,souvenir` plus your specific aesthetic (e.g. `cyprus,tourist,souvenir,flag,minimalist` or `cyprus,tourist,souvenir,vintage,postcard`)."""

OUTPUT_SCHEMA = """Output as JSON:
{
  "concepts": [
    {
      "name": "Short concept name",
      "type": "cyprus|global-trend|slogan|funny|geeky|summer|feminine|love-cyprus",
      "description": "Detailed description of the design for image generation",
      "style": "art style (e.g., minimalist vector, vintage retro, bold graphic, watercolor, line art)",
      "text_on_shirt": "Any text/slogan to include (or empty string if none)",
      "target_audience": "male|female|unisex",
      "product_type": "male|female",
      "suggested_title": "Product title for the store",
      "suggested_tags": "comma,separated,tags (include 'summer' for summer-type, 'feminine' for feminine-type, 'cyprus,tourist,souvenir' for love-cyprus type)",
      "reasoning": "Why this design would sell well right now"
    }
  ]
}"""


def _is_summer_season() -> bool:
    """Summer designs sell from early spring through end of summer (Mar–Sep)."""
    from datetime import datetime, timezone
    return 3 <= datetime.now(timezone.utc).month <= 9


def _build_system_prompt() -> str:
    """Build Mango's system prompt.

    Always includes Feminine and Love Cyprus concept types (year-round).
    Adds the Summer concept type only in season (Mar–Sep).

    Concept types are numbered consecutively from 1; Cyprus-themed types
    (#1 cyprus and the love-cyprus type) are exempt from the
    "must not reference Cyprus" scope rule applied to the others.
    """
    summer_active = _is_summer_season()

    # Number the optional types after the 5 core ones. Each entry is
    # (label, body, is_cyprus_themed).
    extras: list[tuple[str, str, bool]] = []
    if summer_active:
        # CONCEPT_TYPE_SUMMER starts with "6. " from when summer was hardcoded
        # at position 6 — strip that leading number so we can renumber dynamically
        # while preserving the **bold** markdown.
        extras.append(("summer", CONCEPT_TYPE_SUMMER.lstrip("0123456789. "), False))
    extras.append(("feminine", CONCEPT_TYPE_FEMININE, False))
    extras.append(("love-cyprus", CONCEPT_TYPE_LOVE_CYPRUS, True))

    extra_blocks: list[str] = []
    cyprus_indices: list[int] = [1]  # core concept #1 is cyprus
    next_n = 6
    for label, body, is_cyprus in extras:
        extra_blocks.append(f"{next_n}. " + body)
        if is_cyprus:
            cyprus_indices.append(next_n)
        next_n += 1
    total_count = next_n - 1

    types_block = CONCEPT_TYPES_CORE + "\n" + "\n".join(extra_blocks)

    cyprus_indices_str = ", ".join(f"#{i}" for i in cyprus_indices)
    other_indices = [n for n in range(1, total_count + 1) if n not in cyprus_indices]
    if len(other_indices) == 1:
        other_str = f"#{other_indices[0]}"
    elif len(other_indices) <= 4:
        other_str = ", ".join(f"#{n}" for n in other_indices)
    else:
        # Compress the long contiguous block from core types
        other_str = (
            f"#{other_indices[0]}-#{other_indices[-1]}"
            if other_indices == list(range(other_indices[0], other_indices[-1] + 1))
            else ", ".join(f"#{n}" for n in other_indices)
        )

    scope_note = (
        f"IMPORTANT: Only concepts {cyprus_indices_str} may be Cyprus/Mediterranean themed. "
        f"Concepts {other_str} must be globally appealing with NO references to Cyprus, "
        "Mediterranean, Greece, or any specific region."
    )

    count_word = f"exactly {total_count} concepts, one of each type"

    return (
        f"{SYSTEM_PROMPT_BASE}\n\n"
        f"You MUST generate {count_word}:\n\n"
        f"{types_block}\n\n"
        f"{scope_note}\n\n"
        f"{OUTPUT_SCHEMA}\n\n"
        f"Generate {count_word.replace(', one of each type', '')} — one of each type. All must be original."
    )


def _mockup_order(target_audience: str) -> list[tuple[str, str, str]]:
    """Return (ptype, size, label) tuples in upload order.

    The targeted gender's mockup is uploaded first so it appears as the
    main product image in the storefront gallery. Female-targeted designs
    (e.g. the feminine concept type) put the female mockup first; everything
    else defaults to male-first (current historical behavior).
    """
    male = ("male", "L", "Male")
    female = ("female", "M", "Female")
    if (target_audience or "").lower() == "female":
        return [female, male]
    return [male, female]


# Backwards-compatible alias for any imports/tests still referencing SYSTEM_PROMPT
SYSTEM_PROMPT = _build_system_prompt()


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
        await send_error_email("Mango", e)
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
    from app.agents.memory import build_memory_prompt
    _memory_prompt = build_memory_prompt("mango")
    exclusion_prompt = _build_exclusion_prompt()

    user_prompt = f"""Today's date: {date_str}
Store: OMG (omg.com.cy), Cyprus-based t-shirt brand
Markets: Cyprus, Greece, Europe
Current season: {season}

CURRENT T-SHIRT TRENDS (from real-time research):
{trend_research}

Based on these REAL current trends, generate 5 original, commercially viable design concepts (one per type).
Remember: only concept #1 (cyprus type) should be Cyprus-themed. Concepts #2-#5 must be purely global — no Mediterranean, no Cyprus, no Greece references.
Be specific in the design description so an AI image generator can create it accurately.{exclusion_prompt}
{_memory_prompt}"""

    # Get design concepts from Claude — rebuild prompt each run so the
    # Summer type kicks in/out automatically as the season changes.
    result = await llm_client.generate_json(
        system_prompt=_build_system_prompt(),
        user_prompt=user_prompt,
        max_tokens=3500,
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
            # Slogan/quote designs use Pillow text rendering (already transparent);
            # others use DALL-E with text validation if text_on_shirt is set
            from app.agents.image_client import (
                generate_design, generate_text_design,
                generate_design_with_text_check, remove_background,
            )
            is_pillow_text = concept.get("type") == "slogan" and concept.get("text_on_shirt")

            if is_pillow_text:
                image_path = await generate_text_design(
                    text=concept["text_on_shirt"],
                    style=concept.get("style", "bold modern"),
                )
            else:
                image_path = await generate_design_with_text_check(
                    concept=concept["description"],
                    intended_text=concept.get("text_on_shirt", ""),
                    style=concept.get("style", "bold graphic illustration"),
                )

            # Pillow text designs are already transparent — skip rembg
            if is_pillow_text:
                nobg_path = image_path
            else:
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


async def execute_approval_in_background(
    proposal_id: str,
    version: str,
    proposal_data: dict,
) -> None:
    """Run execute_approval in the background and notify the user by email.

    The user-facing approve handler returns the success page immediately
    after claiming the proposal — actual product creation (Playwright +
    Shopify uploads, ~60-90s) happens here, decoupled from the HTTP
    response so the browser doesn't time out and the user doesn't double-
    click thinking it's broken. On success/failure we send a follow-up
    email so the user knows the outcome without watching the server.
    """
    from app.agents.approval import update_status
    from app.agents.agent_email import send_agent_email

    title = proposal_data.get("suggested_title") or proposal_data.get("name") or "Untitled design"
    try:
        result = await execute_approval(proposal_id, version=version)
        await send_agent_email(
            subject=f"[Mango] Product live: {title}",
            html_body=f"""
            <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
                <div style="background:#059669;color:white;padding:20px;border-radius:8px 8px 0 0;">
                    <h2 style="margin:0;">Product live!</h2>
                    <p style="margin:4px 0 0;opacity:0.9;">Mango finished building your product on Shopify.</p>
                </div>
                <div style="padding:20px;background:#f9fafb;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
                    <h3 style="margin:0 0 12px;">{title}</h3>
                    <p style="margin:4px 0;">Product ID: <code>{result.get('product_id', '?')}</code></p>
                    <p style="margin:4px 0;">Handle: <code>{result.get('product_handle', '?')}</code></p>
                    <p style="margin:16px 0;">
                        <a href="{result.get('product_url', '#')}"
                           style="display:inline-block;padding:10px 20px;background:#2563eb;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">
                            View on store
                        </a>
                    </p>
                    <p style="color:#6b7280;font-size:13px;margin-top:16px;">
                        Mapping to TShirtJunkies created automatically. {len(result.get('mappings', []))} variant mapping(s).
                    </p>
                </div>
            </div>
            """,
            extra_recipients=EXTRA_RECIPIENTS,
        )
        logger.info(f"Background approval succeeded for proposal {proposal_id} → product {result.get('product_id')}")
    except Exception as e:
        logger.exception(f"Background approval FAILED for proposal {proposal_id}")
        update_status(proposal_id, "pending")  # rollback so user can re-click the original link
        await send_agent_email(
            subject=f"[Mango] FAILED to create product: {title}",
            html_body=f"""
            <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
                <div style="background:#dc2626;color:white;padding:20px;border-radius:8px 8px 0 0;">
                    <h2 style="margin:0;">Product creation failed</h2>
                </div>
                <div style="padding:20px;background:#fef2f2;border:1px solid #fecaca;border-top:none;border-radius:0 0 8px 8px;">
                    <p>The proposal <strong>'{title}'</strong> failed during product creation.</p>
                    <pre style="background:#fff;padding:12px;overflow:auto;font-size:12px;border:1px solid #fca5a5;border-radius:4px;">{type(e).__name__}: {e}</pre>
                    <p style="color:#6b7280;font-size:13px;margin-top:12px;">
                        Status has been rolled back to <strong>pending</strong> — you can re-click the original approval link in the proposal email to retry.
                    </p>
                </div>
            </div>
            """,
            extra_recipients=EXTRA_RECIPIENTS,
        )


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

    # Upload images in order: 1) Primary-gender mockup, 2) Other-gender mockup, 3) Design artwork.
    # Female-targeted concepts (e.g. "feminine" type) put the female mockup first so
    # it appears as the main image in the storefront gallery; everything else stays
    # male-first as before.
    # Use pre-cached mockups only if approving nobg (they were generated from nobg).
    # For "original" approval, regenerate mockups from the original design.
    cached_mockups = data.get("cached_mockups", {}) if version == "nobg" else {}
    design_path = str(STATIC_DIR / design_filename)
    upload_order = _mockup_order(data.get("target_audience", ""))
    logger.info(
        f"Mockup upload order: {[label for _, _, label in upload_order]} "
        f"(target_audience={data.get('target_audience', 'unisex')})"
    )

    # Group variant IDs by gender so each mockup can be linked to its variants —
    # picking a Female variant on the product page swaps the gallery to the female mockup.
    variant_ids_by_gender: dict[str, list[int]] = {"male": [], "female": []}
    for v in product.get("variants", []):
        gender = (v.get("option1") or "").lower()
        if "female" in gender:
            variant_ids_by_gender["female"].append(v["id"])
        elif "male" in gender:
            variant_ids_by_gender["male"].append(v["id"])

    for ptype, size, label in upload_order:
        cached = cached_mockups.get(ptype, {})
        cached_path = Path(cached["path"]) if cached.get("path") else None

        if cached_path and cached_path.exists():
            logger.info(f"Using pre-cached {label} mockup: {cached_path}")
            mockup_path = cached_path
        else:
            logger.info(f"Fetching {label} mockup from TShirtJunkies (version={version})...")
            mockup_url = await fetch_mockup_from_qstomizer(design_path, ptype, size)
            if not mockup_url:
                continue
            mockup_path = STATIC_DIR / "proposals" / f"mockup_{handle}_{ptype}.png"
            mockup_path.parent.mkdir(exist_ok=True)
            await download_image(mockup_url, mockup_path)

        try:
            await upload_product_image(
                product_id,
                mockup_path,
                alt=f"{label} T-Shirt Mockup",
                variant_ids=variant_ids_by_gender.get(ptype) or None,
            )
            logger.info(
                f"Uploaded {label} mockup to product {product_id} "
                f"(linked to {len(variant_ids_by_gender.get(ptype) or [])} {ptype} variants)"
            )
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
            <h2 style="margin:0;">Hey boss, Mango here!</h2>
            <p style="margin:4px 0 0;opacity:0.9;">I've been painting all night — {len(proposals)} fresh designs for you to check out</p>
        </div>
        <div style="padding:20px;background:#f9fafb;">
            {designs_html}
        </div>
        <div style="padding:12px;text-align:center;color:#9ca3af;font-size:12px;">
            Crafted with love by Mango, your resident artist | <a href="{settings.server_base_url}/agents/feedback/form?agent=mango" style="color:#9ca3af;">Give Feedback</a>
        </div>
    </div>
    """

    await send_agent_email(
        subject=f"[Mango] {len(proposals)} fresh designs hot off the easel",
        html_body=html,
        inline_images=inline_images,
        extra_recipients=EXTRA_RECIPIENTS,
    )


def _get_season(month: int) -> str:
    if month in (3, 4, 5):
        return "Spring"
    if month in (6, 7, 8):
        return "Summer"
    if month in (9, 10, 11):
        return "Autumn"
    return "Winter"
