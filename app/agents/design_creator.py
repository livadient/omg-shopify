"""Agent 2: Trend Research & Design Creator — researches trends, generates designs, creates products."""
import asyncio
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

# Extra recipients for Mango's NEW DESIGNS PROPOSAL email only (not for the
# admin-only approval success/failure notifications).
PROPOSAL_EXTRA_RECIPIENTS = ["kmarangos@hotmail.com", "kyriaki_mara@yahoo.com"]

# Qstomizer's supported tee colors. Anything else is silently coerced to
# White so an LLM hallucination ("Olive Green") doesn't break the Playwright
# color click in qstomizer_automation.select_color.
QSTOMIZER_COLORS = {"White", "Black", "Navy Blue", "Red", "Royal Blue", "Sport Grey"}


def _normalize_tee_color(value: object) -> str:
    """Coerce a raw LLM value to a supported Qstomizer color, default White."""
    if not isinstance(value, str):
        return "White"
    candidate = value.strip()
    if not candidate:
        return "White"
    for allowed in QSTOMIZER_COLORS:
        if candidate.lower() == allowed.lower():
            return allowed
    logger.warning(f"Unknown tee_color {candidate!r} — falling back to White")
    return "White"


# Marketing-scene pipeline (shared with scripts/refresh_all_product_images.py).
# We generate 6 scenes per product via gpt-image-1: 4 female + 2 male model
# back views, sized for upper-back print placement. Both scripts use the
# same artwork spec so newly-approved Mango designs look consistent with
# the backfilled catalog.


def _build_marketing_artwork_spec(fabric: str, exact_text: str | None) -> str:
    """Artwork spec for gpt-image-1. When the design has known text we pin
    it verbatim; when it's a pure illustration we explicitly forbid any
    text. Without this lock gpt-image-1 hallucinates captions (e.g. "DON'T
    HURRY" on a silent walking-penguin illustration).
    """
    base = (
        f"The t-shirt has a printed graphic centered on the upper back. The "
        f"print must be the EXACT artwork shown in the reference image — copy "
        f"it verbatim, pixel-faithful. The print should be SMALL and "
        f"UNDERSTATED — roughly 20-30% of the shirt's upper back width, "
        f"sized like a chest-pocket caption, with generous {fabric} fabric "
        f"on all sides. Do NOT scale it up. This is deliberately modest — "
        f"think minimalist boutique tee, NOT a statement slogan, NOT a "
        f"billboard. Render as a natural DTG fabric print that follows the "
        f"garment's shading and folds. Do NOT add quotation marks, brand "
        f"name, signature, label, or watermark."
    )
    if exact_text:
        oneline = " / ".join(exact_text.split("\n"))
        return (
            base
            + f" CRITICAL: the ONLY text on the shirt is exactly: \"{oneline}\" "
              f"(preserving original line breaks and capitalisation). Do NOT "
              f"change, substitute, paraphrase, shorten, lengthen, or invent "
              f"any other wording. Render these EXACT words and NO OTHER text. "
              f"Every word must be fully visible — do NOT crop or truncate."
        )
    return (
        base
        + " CRITICAL: this design is a PURE ILLUSTRATION with NO TEXT. Do NOT "
          "add ANY text, letters, numbers, words, slogans, captions, or "
          "writing anywhere on the shirt or in the image. The ONLY thing on "
          "the shirt is the graphic illustration from the reference."
    )


async def _extract_design_text_via_claude(design_path: Path) -> str | None:
    """Read the exact text on the design PNG via Claude vision; returns
    None for pure-illustration designs (no text at all).
    """
    import base64 as _b64
    import anthropic
    if not settings.anthropic_api_key:
        return None
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    img_b64 = _b64.b64encode(design_path.read_bytes()).decode("utf-8")
    try:
        resp = await client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                    {"type": "text", "text": (
                        "Read this t-shirt design PNG and return ONLY the visible text verbatim "
                        "(preserving line breaks with \\n, capitalization, punctuation, spacing). "
                        "If the design is a pure illustration with NO text at all, respond exactly: NONE.\n\n"
                        "Return ONLY the text or NONE — no explanation, no quotes."
                    )},
                ],
            }],
        )
        text = resp.content[0].text.strip()
        if text.upper() == "NONE" or not text:
            return None
        return text
    except Exception as e:
        logger.warning(f"Claude text extraction failed for {design_path.name}: {e}")
        return None


def _build_marketing_scene_prompts(tee_color: str, exact_text: str | None) -> dict[str, str]:
    fabric = tee_color.lower()
    flat_bg = "pure white seamless" if fabric != "white" else "light grey seamless"
    artwork = _build_marketing_artwork_spec(fabric, exact_text)
    return {
        "01_closeup_back": (
            f"Medium close-up lifestyle e-commerce photograph taken from directly "
            f"behind a young woman. She is turned with her back fully to the "
            f"camera — her face is not visible. Her hair is pulled up into a high "
            f"bun so the upper back of her t-shirt is completely unobstructed. "
            f"The frame shows her from just above the bun down to her hips. She "
            f"wears a plain {fabric} crew-neck cotton t-shirt.\n\n{artwork}\n\n"
            f"Soft natural daylight, clean minimalist light-grey studio "
            f"background, professional fashion e-commerce product photography, "
            f"photorealistic, sharp focus, 4k."
        ),
        "02_fullbody_back": (
            f"Full-body lifestyle e-commerce photograph of a young woman walking "
            f"away from the camera, back view. She is fully turned away, her face "
            f"is not visible. She wears a plain {fabric} crew-neck cotton t-shirt "
            f"tucked loosely into light blue straight-leg jeans and white sneakers. "
            f"Full body from head to feet in frame, the shirt's back print is "
            f"clearly legible.\n\n{artwork}\n\n"
            f"Clean minimalist light-grey studio background, soft natural daylight, "
            f"professional fashion e-commerce photography, photorealistic, 4k."
        ),
        "03_product_back": (
            f"Overhead flat-lay product photograph of a plain {fabric} crew-neck "
            f"cotton t-shirt laid flat on a {flat_bg} background, photographed "
            f"straight down from above. The shirt is laid with the BACK facing "
            f"up. Short sleeves spread out symmetrically. Evenly lit, soft "
            f"shadows, no model, no hanger.\n\n{artwork}\n\n"
            f"Professional e-commerce apparel flat-lay photography, "
            f"photorealistic, 4k, sharp focus."
        ),
        "04_hanger_back": (
            f"Product photograph of a plain {fabric} crew-neck cotton t-shirt on "
            f"a plain wooden clothes hanger, hung against a clean minimalist "
            f"light-grey wall. The shirt is facing the camera straight-on from "
            f"the back so the back print is clearly visible. Natural fabric "
            f"drape, short sleeves, even soft studio lighting.\n\n{artwork}\n\n"
            f"Professional e-commerce apparel product photography, "
            f"photorealistic, 4k, sharp focus."
        ),
        "01_closeup_back_male": (
            f"Medium close-up lifestyle e-commerce photograph taken from directly "
            f"behind a fit, semi-muscular young man. He is turned with his back "
            f"fully to the camera — his face is not visible. His short dark hair "
            f"is neatly cut so the upper back of his t-shirt is completely "
            f"unobstructed. The frame shows him from just above the neck down to "
            f"his hips. He wears a plain {fabric} crew-neck cotton t-shirt that "
            f"fits well across defined shoulders and a toned back (athletic, "
            f"gym-regular, not bodybuilder).\n\n{artwork}\n\n"
            f"Soft natural daylight, clean minimalist light-grey studio "
            f"background, professional fashion e-commerce product photography, "
            f"photorealistic, sharp focus, 4k."
        ),
        "02_fullbody_back_male": (
            f"Full-body lifestyle e-commerce photograph of a fit, semi-muscular "
            f"young man walking away from the camera, back view. He is fully "
            f"turned away, his face is not visible. He wears a plain {fabric} "
            f"crew-neck cotton t-shirt that fits well across defined shoulders "
            f"and a toned back (athletic, gym-regular, not bodybuilder), tucked "
            f"loosely into dark straight-leg jeans and white sneakers. Full body "
            f"from head to feet in frame, the shirt's back print is clearly "
            f"legible.\n\n{artwork}\n\n"
            f"Clean minimalist light-grey studio background, soft natural "
            f"daylight, professional fashion e-commerce photography, "
            f"photorealistic, 4k."
        ),
    }


def _group_variant_ids_by_gender_placement(product: dict) -> dict[tuple[str, str], list[int]]:
    groups: dict[tuple[str, str], list[int]] = {
        ("male", "front"): [], ("male", "back"): [],
        ("female", "front"): [], ("female", "back"): [],
    }
    for v in product.get("variants", []):
        g = (v.get("option1") or "").lower()
        p = (v.get("option2") or "").lower()
        gkey = "female" if "female" in g else ("male" if "male" in g else None)
        pkey = "back" if p == "back" else ("front" if p == "front" else None)
        if gkey and pkey:
            groups[(gkey, pkey)].append(v["id"])
    return groups


async def _generate_marketing_scenes(
    handle: str,
    design_path: Path,
    tee_color: str,
) -> dict[str, Path]:
    """Compose the 6 marketing scenes by pasting the transparent design
    PNG directly onto blank-tee model photos. Returns label → path dict.

    Pipeline lives in app.agents.marketing_pipeline so the Phase 3 backfill
    script and this approval flow share one recipe. We use the transparent
    design (not a TJ-mockup silhouette) so the model's actual tee shows
    through — pasting a tee silhouette over the model's tee creates a
    visible "tee on tee" edge.
    """
    from app.agents.marketing_pipeline import compose_marketing_scenes

    slug = handle.replace("-", "_")
    scene_dir = STATIC_DIR / "proposals" / slug
    scene_dir.mkdir(parents=True, exist_ok=True)

    generated = await compose_marketing_scenes(
        design_path=design_path, out_dir=scene_dir, tee_color=tee_color,
    )
    logger.info(f"Composed {len(generated)}/6 marketing scenes for {handle}")
    return generated

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
      "tee_color": "White|Black|Navy Blue|Red|Royal Blue|Sport Grey",
      "reasoning": "Why this design would sell well right now"
    }
  ]
}

NAMING RULES (strict):
- `name`: 2–4 words, evocative, what you'd call the design over coffee. NO trailing
  "Tee", "T-Shirt", "Design", "Humor", "Typography", "Programmer". NO colons or dashes.
  Good: "Quantum Cat Paradox", "Digital Detox Mode", "404 Sleep Not Found".
  Bad:  "404 Sleep Not Found Programmer Humor Tee", "Quantum Cat Paradox Physics Humor Science Tee",
        "Professional Overthinker Bold Typography Tee", "No Cap Energy Only Bold Typography T-Shirt".
- `suggested_title`: Real product title a shopper sees. 3–6 words, includes ONE descriptor
  word like "Tee" or "T-Shirt" at the end (only one). NO chained adjectives like
  "bold typography humor". Good: "Quantum Cat Paradox Tee", "Digital Detox Mode T-Shirt".
  Bad: "Quantum Cat Paradox Physics Humor Science Tee" (six adjectives stapled together).
- `text_on_shirt`: keep it SHORT (1–6 words max if you want DALL-E to spell it right).
  Long phrases consistently come back misspelled — prefer slogan-type for anything 7+ words
  so we render with Pillow instead. For SLOGAN-type designs, split the slogan across
  two lines using a literal `\n` when there's a natural punchline break (top line = the
  setup/statement, bottom line = the kicker/punchline). The Pillow renderer applies a
  size+weight hierarchy: top line in bold condensed caps, bottom line noticeably smaller
  and thinner in a regular-weight sans. This matches the approved "Don't Tempt Me /
  I'll Say Yes" template — modest chest-pocket scale (~55% canvas width), not billboard.
  Example good shapes: `"DON'T TEMPT ME\nI'LL SAY YES"`, `"TOLD HER SHE'S THE ONE\nNOT THE ONLY ONE"`.
  Single-line slogans render at the same modest scale without hierarchy.
- `tee_color`: the fabric color the design is PRINTED ON. Must be exactly one of
  `White`, `Black`, `Navy Blue`, `Red`, `Royal Blue`, `Sport Grey` — any other value
  is rejected. Pick for legibility:
  * Light-toned artwork (white text, pastels, pale illustrations) → `Black` or a dark
    fabric so the print actually shows.
  * Dark-toned artwork (black text, dark ink, deep colors) → `White` (default).
  * Mid-tones or full-color illustrations → default to `White` unless black
    dramatically improves contrast. When in doubt, use `White`.
  * Slogan-type designs where the slogan color is inherent to the style
    (e.g. maroon italic caps) → use the fabric that makes the slogan pop.
"""


def _is_summer_season() -> bool:
    """Summer designs sell from early spring through end of summer (Mar–Sep)."""
    from datetime import datetime, timezone
    return 3 <= datetime.now(timezone.utc).month <= 9


def _compute_concept_plan() -> dict:
    """Return the active concept plan for today's run.

    Keys:
      total_count — how many concepts Claude must generate
      types_block — the numbered markdown list of type definitions
      cyprus_indices_str — "#1, #7" etc. — which concepts MAY be Cyprus-themed
      other_str — "#2-#5, #6" etc. — which MUST be globally scoped

    Shared by _build_system_prompt (for the system prompt) and the runtime
    user prompt so the two cannot drift apart — they used to contradict each
    other, causing Claude to generate 8 one day and 5 the next.
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
    for _label, body, is_cyprus in extras:
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

    return {
        "total_count": total_count,
        "types_block": types_block,
        "cyprus_indices_str": cyprus_indices_str,
        "other_str": other_str,
    }


def _build_system_prompt() -> str:
    """Build Mango's system prompt.

    Always includes Feminine and Love Cyprus concept types (year-round).
    Adds the Summer concept type only in season (Mar–Sep).

    Concept types are numbered consecutively from 1; Cyprus-themed types
    (#1 cyprus and the love-cyprus type) are exempt from the
    "must not reference Cyprus" scope rule applied to the others.
    """
    plan = _compute_concept_plan()
    total_count = plan["total_count"]
    types_block = plan["types_block"]
    cyprus_indices_str = plan["cyprus_indices_str"]
    other_str = plan["other_str"]

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


def _mockup_order(target_audience: str) -> list[tuple[str, str, str, str]]:
    """Return (ptype, size, placement, label) tuples in upload order.

    The targeted gender's FRONT mockup is uploaded first so it appears as the
    main product image in the storefront gallery. Female-targeted designs
    (e.g. the feminine concept type) put the female mockup first; everything
    else defaults to male-first. Within each gender, front comes before back.
    """
    male_front = ("male", "L", "front", "Male Front")
    male_back = ("male", "L", "back", "Male Back")
    female_front = ("female", "M", "front", "Female Front")
    female_back = ("female", "M", "back", "Female Back")
    if (target_audience or "").lower() == "female":
        return [female_front, female_back, male_front, male_back]
    return [male_front, male_back, female_front, female_back]


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


async def _proofread_concept_texts(concepts: list[dict]) -> None:
    """Proofread each concept's text_on_shirt for spelling/grammar in English
    and Greek. Mutates the concepts list in place. Runs one batched Haiku
    call for the whole run so cost is ~1 cheap call regardless of concept
    count. Failures are swallowed — better to print slightly-wrong text than
    to crash the run.
    """
    items = [
        (i, (c.get("text_on_shirt") or "").strip())
        for i, c in enumerate(concepts)
    ]
    items = [(i, t) for i, t in items if t]
    if not items:
        return

    payload = [{"index": i, "text": t} for i, t in items]
    system_prompt = (
        "You are a bilingual (English/Greek) proofreader for t-shirt slogans. "
        "You will be given a JSON array of {index, text} entries. For each, "
        "return the text with spelling, grammar, accent, and punctuation "
        "errors fixed. Preserve the original style, casing, line breaks, "
        "emoji, and intent — do not rewrite or translate. Keep it the same "
        "language it started in. Greek examples of errors to fix: missing "
        "final sigma (ΩΡΕ → ΩΡΕΣ), wrong accents, wrong breathings. "
        "If the text is already correct, return it unchanged."
    )
    user_prompt = (
        "Proofread each slogan. Reply with JSON only, no prose:\n"
        '{"results": [{"index": 0, "text": "corrected"}, ...]}\n\n'
        f"Slogans:\n{json.dumps(payload, ensure_ascii=False)}"
    )

    try:
        result = await llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            temperature=0,
        )
    except Exception as e:
        logger.warning(f"Slogan proofread failed, using originals: {e}")
        return

    for entry in result.get("results", []):
        idx = entry.get("index")
        new_text = (entry.get("text") or "").strip()
        if not isinstance(idx, int) or not (0 <= idx < len(concepts)) or not new_text:
            continue
        old_text = (concepts[idx].get("text_on_shirt") or "").strip()
        if new_text != old_text:
            logger.info(f"Proofread slogan #{idx}: {old_text!r} -> {new_text!r}")
            concepts[idx]["text_on_shirt"] = new_text


def _build_exclusion_prompt() -> str:
    """Build a prompt section listing past designs to avoid."""
    past = _load_past_designs()
    if not past:
        return ""
    # Show the last 50 for context
    recent = past[-50:]
    lines = [f"- [{e['date']}] ({e['type']}) {e['name']}: {e['description'][:120]}" for e in recent]
    # Pull just the names for the strict blacklist — concept name reuse is the
    # most obvious form of repetition and the easiest for Claude to police.
    name_blacklist = sorted({e["name"] for e in recent if e.get("name")})
    return (
        "\n\nDO NOT REPEAT PAST DESIGNS — strict rules:\n"
        "1. Never reuse any of these exact concept names, and never produce a paraphrase "
        "that shares ≥2 distinctive content words with one of them "
        "(e.g. if 'Digital Detox Mode' is on the list, also avoid 'Digital Detox Club', "
        "'Digital Detox Weekend', etc. — pick a totally different theme):\n"
        + "\n".join(f"   - {n}" for n in name_blacklist)
        + "\n2. Avoid recycled themes that have already appeared multiple times in the last 50 runs "
        "(coffee philosophy, mediterranean sunset, digital detox, overthinker, "
        "main character energy, etc.). If you find yourself reaching for one of those, pick something else.\n"
        "3. Full context of recent designs (date, type, name, description) for reference:\n"
        + "\n".join(lines)
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

    # Step 1: Research actual t-shirt trends via web search.
    # Rotate the angle by day-of-week so we don't keep dragging back the same
    # 10–15 trending memes every run (which is how Mango ended up generating
    # "Digital Detox Mode" / "Mediterranean Sunset Vibes" / "Overthinker" etc.
    # over and over). Each weekday biases the search toward a different corner
    # of culture so the seed pool stays fresh.
    trend_angles = [
        # Mon
        "Focus this run on TIKTOK / INSTAGRAM micro-trends, viral sounds, "
        "and Gen Z slang (delulu, mid, no cap, brainrot, coquette, etc.). "
        "Find 3–4 specific trending phrases/concepts that would work as a tee.",
        # Tue
        "Focus this run on PROGRAMMER / TECH / GAMING humor — recent dev memes, "
        "AI-era jokes, esports moments, retro gaming nostalgia, IDE/terminal humor. "
        "Find 3–4 fresh angles that haven't been done to death.",
        # Wed
        "Focus this run on WORLD EVENTS, sports, music drops, and pop culture moments "
        "from the last 2–3 weeks that would translate to a graphic tee. Skip evergreen — "
        "give me what's burning RIGHT NOW.",
        # Thu
        "Focus this run on NICHE SUBCULTURES and hobbies — climbers, runners, cyclists, "
        "chess players, D&D, F1 fans, vinyl collectors, plant moms, coffee snobs, "
        "dog-breed-specific humor. Pick 3–4 niches and surface in-jokes from each.",
        # Fri
        "Focus this run on GREEK / CYPRIOT / MEDITERRANEAN culture — local slang, "
        "café/koulouri humor, frappés, panigyria, στραβός γείτονας energy, "
        "ΕΛ-flavored irony. Find concepts a local would actually screenshot to a friend.",
        # Sat
        "Focus this run on STREETWEAR and FASHION-FORWARD aesthetics — what indie brands "
        "and small-batch labels are putting out, tonal/blackwork designs, retro-futurist, "
        "Y2K revival mutations, archival-style graphics. Skip the obvious mainstream stuff.",
        # Sun
        "Focus this run on TYPOGRAPHIC / SLOGAN tees — short-form jokes and one-liners "
        "trending on shitpost/meme accounts, screenshot-to-friend energy, deadpan humor, "
        "absurdist quotes. Find phrases real people are reposting THIS week.",
    ]
    angle = trend_angles[now.weekday()]

    trend_research = await llm_client.generate_with_search(
        system_prompt="You are a fashion trend researcher specializing in graphic t-shirts and streetwear. Provide concise, actionable trend insights.",
        user_prompt=f"""Today is {date_str}. Research CURRENT trending t-shirt designs for {season} 2026.

{angle}

Also briefly cover (1–2 lines each):
- Anything else genuinely trending across Etsy / Redbubble / Pinterest / Instagram / TikTok this week
- Memes / phrases / cultural moments that would work on a tee

Summarize what you find with concrete examples. Focus on what's selling/trending NOW — skip evergreen advice and skip anything that's been a meme for 6+ months.""",
        max_tokens=2000,
        temperature=0.5,
    )

    logger.info(f"Trend research complete: {len(trend_research)} chars")

    # Step 2: Generate design concepts informed by real trends
    from app.agents.memory import build_memory_prompt
    _memory_prompt = build_memory_prompt("mango")
    exclusion_prompt = _build_exclusion_prompt()

    plan = _compute_concept_plan()
    total_count = plan["total_count"]
    cyprus_indices_str = plan["cyprus_indices_str"]
    other_str = plan["other_str"]

    user_prompt = f"""Today's date: {date_str}
Store: OMG (omg.com.cy), Cyprus-based t-shirt brand
Markets: Cyprus, Greece, Europe
Current season: {season}

CURRENT T-SHIRT TRENDS (from real-time research):
{trend_research}

Based on these REAL current trends, generate {total_count} original, commercially viable design concepts (one per type).
Remember: only concepts {cyprus_indices_str} (Cyprus types) should be Cyprus-themed. Concepts {other_str} must be purely global — no Mediterranean, no Cyprus, no Greece references.
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

    # Proofread slogan text before it goes to the printer. Claude Sonnet
    # occasionally produces broken English or Greek in the generation call
    # (e.g. "ΩΡΕ ΓΡΑΦΕΙΟΥ" missing the final Σ, "EST. ANTIQUITY"), and since
    # slogan tees render text_on_shirt verbatim via Pillow, those errors land
    # on real shirts. A cheap Haiku pass catches spelling/grammar mistakes.
    await _proofread_concept_texts(concepts)

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
                TextValidationError,
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
            tee_color = _normalize_tee_color(concept.get("tee_color"))
            concept["tee_color"] = tee_color
            cached_mockups = await _precache_mockups(
                mockup_image, concept.get("name", ""), color=tee_color,
            )
            concept["cached_mockups"] = cached_mockups

            proposal = create_proposal("design", concept)
            proposals.append(proposal)

        except TextValidationError as e:
            # DALL-E couldn't get the text right after all retries — drop the
            # proposal entirely rather than email a misspelled image.
            logger.warning(
                f"Dropping design '{concept.get('name', '?')}' — text validation never passed: {e}"
            )
            continue
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


async def _precache_mockups(
    design_image_path: str,
    concept_name: str,
    color: str = "White",
    vertical_offset: float = -0.25,
) -> dict:
    """Pre-generate TShirtJunkies mockups via Qstomizer so approval is near-instant.

    Generates 4 mockups: male×front, male×back, female×front, female×back.
    Returns nested dict keyed by gender then placement:
        {"male": {"front": {"url": ..., "path": ...}, "back": {...}},
         "female": {"front": {...}, "back": {...}}}
    """
    from app.shopify_product_creator import fetch_mockup_from_qstomizer, download_image

    cached: dict = {"male": {}, "female": {}}
    proposals_dir = STATIC_DIR / "proposals"
    proposals_dir.mkdir(exist_ok=True)

    combos = [
        ("male", "L", "front"),
        ("male", "L", "back"),
        ("female", "M", "front"),
        ("female", "M", "back"),
    ]
    for ptype, size, placement in combos:
        try:
            logger.info(f"Pre-caching {ptype} {placement} {color} mockup for '{concept_name}'...")
            mockup_url = await fetch_mockup_from_qstomizer(
                design_image_path, ptype, size,
                placement=placement, color=color, vertical_offset=vertical_offset,
            )
            if mockup_url:
                filename = f"mockup_cache_{Path(design_image_path).stem}_{ptype}_{placement}.png"
                local_path = proposals_dir / filename
                await download_image(mockup_url, local_path)
                cached[ptype][placement] = {"url": mockup_url, "path": str(local_path)}
                logger.info(f"Cached {ptype} {placement} {color} mockup: {local_path}")
        except Exception as e:
            logger.warning(
                f"Failed to pre-cache {ptype} {placement} {color} mockup for '{concept_name}': {e}"
            )

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
        color=_normalize_tee_color(data.get("tee_color")),
    )

    design_path_obj = STATIC_DIR / design_filename
    tee_color = _normalize_tee_color(data.get("tee_color"))
    title = data.get("suggested_title", data.get("name", "Graphic Tee"))

    # 1) Resolve TJ mockup paths FIRST — they drive the on-model scenes
    # below (we paste the rendered TJ tee onto blank-model photos for
    # pixel-perfect consistency between the product-gallery TJ mockup and
    # the lifestyle shots). Prefer Mango's pre-cache; fall back to live
    # Playwright for anything missing.
    cached_mockups = data.get("cached_mockups", {}) if version == "nobg" else {}
    design_path = str(design_path_obj)
    mockup_paths: dict[tuple[str, str], Path | None] = {}
    for (ptype, size, placement) in [
        ("male", "L", "front"), ("male", "L", "back"),
        ("female", "M", "front"), ("female", "M", "back"),
    ]:
        cached = cached_mockups.get(ptype, {}).get(placement, {}) if isinstance(cached_mockups.get(ptype), dict) else {}
        cached_path = Path(cached["path"]) if cached.get("path") else None
        if cached_path and cached_path.exists():
            mockup_paths[(ptype, placement)] = cached_path
            continue
        logger.info(f"Fetching {ptype}/{placement} mockup from TShirtJunkies...")
        mockup_url = await fetch_mockup_from_qstomizer(
            design_path, ptype, size, placement=placement,
        )
        if not mockup_url:
            mockup_paths[(ptype, placement)] = None
            continue
        local = STATIC_DIR / "proposals" / f"mockup_{handle}_{ptype}_{placement}.png"
        local.parent.mkdir(exist_ok=True)
        await download_image(mockup_url, local)
        mockup_paths[(ptype, placement)] = local

    # 2) Compose the 6 marketing scenes by pasting the transparent design
    # PNG onto blank-tee model photos. Pixel-perfect text (the design PNG
    # is the source of truth) without the "tee on tee" edge artifact you
    # get from pasting a TJ-mockup silhouette over the model's existing tee.
    scenes: dict[str, Path] = {}
    if design_path_obj.exists():
        try:
            scenes = await _generate_marketing_scenes(
                handle=handle, design_path=design_path_obj, tee_color=tee_color,
            )
        except Exception as e:
            logger.warning(f"Marketing scene generation failed: {e}")

    # Build gender/placement variant groups for linking.
    groups = _group_variant_ids_by_gender_placement(product)
    all_female = groups[("female", "front")] + groups[("female", "back")]
    all_male = groups[("male", "front")] + groups[("male", "back")]
    all_back = groups[("male", "back")] + groups[("female", "back")]

    # Unified upload plan (shared shape with scripts/refresh_all_product_images.py):
    # 1) TJ male back mockup = card thumbnail
    # 2) TJ female back mockup = card hover image
    # 3) Male lifestyle (selecting Male swaps gallery to male model)
    # 4) Female lifestyle
    # 5) Flat-lay + hanger (unisex)
    # 6) Remaining TJ mockups
    # Gender-variant linking is preserved so the gallery swaps to the
    # matching shot when Male / Female / Front / Back is selected.
    # NOTE: For IMAGE/illustration designs (aspect >= 0.4), the transparent
    # Design Artwork is appended as the LAST product image after the upload
    # plan below — so customers can see the artwork standalone. Skipped for
    # text/slogan designs (the standalone slogan PNG isn't visually
    # interesting). See the post-loop block.
    # Only the 4 TJ mockups carry variant_ids — Shopify enforces
    # one-variant-per-image, so linking a variant to a lifestyle shot would
    # silently block the TJ mockup from claiming that variant.
    upload_plan: list[tuple[Path | None, list[int] | None, str]] = [
        (mockup_paths.get(("male", "back")), groups[("male", "back")] or None, f"{title} — TJ male back mockup"),
        (mockup_paths.get(("female", "back")), groups[("female", "back")] or None, f"{title} — TJ female back mockup"),
        (scenes.get("01_closeup_back_male"), None, f"{title} — male closeup back"),
        (scenes.get("02_fullbody_back_male"), None, f"{title} — male fullbody back"),
        (scenes.get("01_closeup_back"), None, f"{title} — female closeup back"),
        (scenes.get("02_fullbody_back"), None, f"{title} — female fullbody back"),
        (scenes.get("03_product_back"), None, f"{title} — back flat-lay"),
        (scenes.get("04_hanger_back"), None, f"{title} — back hanger"),
        (mockup_paths.get(("male", "front")), groups[("male", "front")] or None, f"{title} — TJ male front mockup"),
        (mockup_paths.get(("female", "front")), groups[("female", "front")] or None, f"{title} — TJ female front mockup"),
    ]
    uploaded = 0
    for path, vids, alt in upload_plan:
        if not (path and path.exists()):
            continue
        try:
            await upload_product_image(product_id, path, alt=alt, variant_ids=vids)
            uploaded += 1
        except Exception as e:
            logger.warning(f"Upload {alt} failed: {e}")

    # For IMAGE/illustration designs (aspect >= 0.4 — square or tall artwork
    # like mushroom, penguin, astous), also upload the transparent design PNG
    # itself as the LAST product image so customers can see the artwork
    # standalone. Skipped for TEXT/slogan designs (wide and short, aspect <
    # 0.4) since the artwork-on-its-own isn't visually interesting for plain
    # typography.
    try:
        from PIL import Image as _Img
        with _Img.open(design_path_obj) as _di:
            _bbox = _di.convert("RGBA").getbbox()
        if _bbox:
            _w = _bbox[2] - _bbox[0]
            _h = _bbox[3] - _bbox[1]
            _aspect = _h / _w if _w else 1.0
            if _aspect >= 0.4:
                try:
                    await upload_product_image(
                        product_id, design_path_obj,
                        alt=f"{title} — design artwork",
                        variant_ids=None,
                    )
                    uploaded += 1
                    logger.info(f"Uploaded design artwork as last image (image design, aspect={_aspect:.2f})")
                except Exception as e:
                    logger.warning(f"Design artwork upload failed: {e}")
            else:
                logger.info(f"Skipping design artwork upload (text design, aspect={_aspect:.2f})")
    except Exception as e:
        logger.warning(f"Design aspect detection failed, skipping design artwork upload: {e}")

    logger.info(f"Uploaded {uploaded} images to product {product_id}")

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
        extra_recipients=PROPOSAL_EXTRA_RECIPIENTS,
    )


def _get_season(month: int) -> str:
    if month in (3, 4, 5):
        return "Spring"
    if month in (6, 7, 8):
        return "Summer"
    if month in (9, 10, 11):
        return "Autumn"
    return "Winter"
