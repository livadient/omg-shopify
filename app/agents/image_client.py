"""DALL-E 3 image generation wrapper for t-shirt designs."""
import base64
import logging
import random
from pathlib import Path

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

# Curated palette of bold, high-contrast colors that print well on white tees
# (the default Qstomizer color). Each is dark/saturated enough to read clearly.
TEXT_DESIGN_COLORS = [
    "#000000",  # black
    "#1a1a2e",  # near-black navy
    "#1e3a8a",  # navy blue
    "#0c4a6e",  # deep teal
    "#7f1d1d",  # dark red
    "#9a3412",  # rust
    "#7c2d12",  # burnt brick
    "#831843",  # burgundy
    "#581c87",  # dark purple
    "#14532d",  # forest green
    "#365314",  # olive
    "#3f3f46",  # charcoal
]

# Font candidates checked at runtime — first matches available on the host
# (Linux server has Liberation + DejaVu fonts; dev machines may have Windows fonts).
TEXT_DESIGN_FONTS = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/COURBD.TTF",
]

# Two-line-hierarchy templates (matches Kyriaki-approved "Don't Tempt Me"
# style: bold condensed top + visibly thinner/smaller regular-weight sub).
# Each entry picks a (top, sub) font pair so the weight contrast reads
# even at small print sizes. First-available wins per entry.
TEXT_DESIGN_HIERARCHY_FONTS: list[tuple[list[str], list[str]]] = [
    (
        ["C:/Windows/Fonts/impact.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
        ["C:/Windows/Fonts/arial.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    ),
    (
        ["C:/Windows/Fonts/arialbd.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
         "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"],
        ["C:/Windows/Fonts/arial.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/freefont/FreeSans.ttf"],
    ),
    (
        ["C:/Windows/Fonts/timesbd.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"],
        ["C:/Windows/Fonts/arial.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    ),
]


def _first_existing(candidates: list[str]) -> str | None:
    for fp in candidates:
        if Path(fp).exists():
            return fp
    return None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def generate_design(
    concept: str,
    style: str = "bold graphic illustration",
    size: str = "1024x1024",
    quality: str = "hd",
) -> Path:
    """Generate a t-shirt design using gpt-image-1 (GPT-4o native image gen,
    same model ChatGPT uses) and save to static/. Text rendering and general
    prompt adherence are materially better than dall-e-3, especially for
    slogan tees where the text has to match `text_on_shirt` verbatim.

    Returns the path to the saved PNG file.
    """
    client = _get_client()

    prompt = (
        f"Create a standalone graphic artwork for printing: {concept}. "
        f"Style: {style}. "
        "IMPORTANT: This is ONLY the graphic/artwork/illustration itself — "
        "do NOT show a t-shirt, clothing, mannequin, or any garment. "
        "Just the design artwork on a plain solid white background. "
        "Requirements: solid white background, high contrast, clean sharp edges, "
        "bold and eye-catching artwork suitable for screen printing. "
        "No copyrighted characters or logos. Centered composition."
    )

    # Map legacy dall-e-3 quality values onto gpt-image-1's scale.
    quality_map = {"hd": "high", "standard": "medium", "low": "low",
                   "medium": "medium", "high": "high", "auto": "auto"}
    gpt_quality = quality_map.get(quality, "high")

    logger.info(f"Generating design (gpt-image-1, {gpt_quality}): {concept[:80]}...")

    response = await client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size=size,
        quality=gpt_quality,
        n=1,
    )

    # gpt-image-1 returns base64 directly, no URL download step.
    b64 = response.data[0].b64_json
    if not b64:
        raise RuntimeError("gpt-image-1 returned no image data")

    proposals_dir = STATIC_DIR / "proposals"
    proposals_dir.mkdir(exist_ok=True)

    import uuid
    filename = f"design_{uuid.uuid4().hex[:8]}.png"
    filepath = proposals_dir / filename
    filepath.write_bytes(base64.b64decode(b64))

    logger.info(f"Design saved: {filepath}")
    return filepath


async def generate_text_design(
    text: str,
    style: str = "bold modern",
    size: tuple[int, int] = (1024, 1024),
) -> Path:
    """Generate a text-only design using Pillow typography.

    Follows the Kyriaki-approved "Don't Tempt Me" template (2026-04-22):
    modest print scale (~55% of canvas width, generous fabric around all
    sides), and for two-line slogans a visible size+weight hierarchy —
    bold condensed caps on top + noticeably smaller thinner regular-weight
    sub line. Single-line slogans render at the same modest scale.

    Picks a random color, hierarchy font pair, treatment (plain/outline/
    shadow) and case on each call so successive slogan tees feel visually
    distinct instead of all being identical.

    Returns the path to the saved PNG file.
    """
    from PIL import Image, ImageDraw, ImageFont

    # Randomized look — color, font pair, treatment, case
    color_hex = random.choice(TEXT_DESIGN_COLORS)
    # Pick a (top, sub) font pair; skip pairs where neither font is available
    viable_pairs: list[tuple[str, str]] = []
    for top_cands, sub_cands in TEXT_DESIGN_HIERARCHY_FONTS:
        top_fp = _first_existing(top_cands)
        sub_fp = _first_existing(sub_cands)
        if top_fp and sub_fp:
            viable_pairs.append((top_fp, sub_fp))
    if viable_pairs:
        top_font_path, sub_font_path = random.choice(viable_pairs)
    else:
        # Fallback to the old any-bold font pool (used on very bare Linux)
        available_fonts = [fp for fp in TEXT_DESIGN_FONTS if Path(fp).exists()]
        top_font_path = random.choice(available_fonts) if available_fonts else None
        sub_font_path = top_font_path

    treatment = random.choices(
        ["plain", "outline", "shadow"], weights=[70, 15, 15], k=1
    )[0]
    use_uppercase = random.random() < 0.7  # mostly all-caps, sometimes original case

    logger.info(
        f"Text design look: color={color_hex} treatment={treatment} "
        f"upper={use_uppercase} "
        f"top_font={Path(top_font_path).name if top_font_path else 'default'} "
        f"sub_font={Path(sub_font_path).name if sub_font_path else 'default'}"
    )

    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Respect explicit newlines; no auto-wrap for long single lines — the
    # LLM is instructed to put the hierarchy break in the text, and an
    # auto-wrap mid-line would flatten the top/sub distinction.
    raw = text.upper() if use_uppercase else text
    lines = raw.split("\n")

    # Modest print scale — text fills ~55% of the canvas width, leaving
    # generous fabric margin on all sides. This matches Kyriaki's
    # "small modest caption" feedback and keeps the print chest-pocket-
    # sized rather than billboard-sized.
    text_width_ratio = 0.55
    max_text_w = int(size[0] * text_width_ratio)
    max_text_h = int(size[1] * 0.70)

    # Sub line should read as noticeably smaller AND thinner than top.
    # 0.45 is the ratio Kyriaki approved on the DTM style (top=Impact,
    # sub=Arial Regular at 45%).
    sub_to_top_ratio = 0.45

    def _load(fp: str | None, sz: int):
        if not fp:
            return ImageFont.load_default()
        return ImageFont.truetype(fp, sz)

    # Find the largest top-line font size that fits within max_text_w /
    # max_text_h when combined with sub (if present).
    top_size = 400
    top_font = sub_font = None
    top_bb = sub_bb = None
    tot_h = 0
    while top_size > 20:
        top_font = _load(top_font_path, top_size)
        top_bb = draw.textbbox((0, 0), lines[0], font=top_font)
        top_w = top_bb[2] - top_bb[0]
        top_h = top_bb[3] - top_bb[1]

        if len(lines) > 1:
            sub_size = max(1, int(top_size * sub_to_top_ratio))
            sub_font = _load(sub_font_path, sub_size)
            sub_bb = draw.textbbox((0, 0), lines[1], font=sub_font)
            sub_w = sub_bb[2] - sub_bb[0]
            sub_h = sub_bb[3] - sub_bb[1]
            gap = int(top_size * 0.18)
            tot_h = top_h + gap + sub_h
            if max(top_w, sub_w) <= max_text_w and tot_h <= max_text_h:
                break
        else:
            sub_font = None
            tot_h = top_h
            if top_w <= max_text_w and top_h <= max_text_h:
                break
        top_size -= 6

    def _draw_line(line: str, font, x: int, y: int):
        if treatment == "shadow":
            offset = max(4, top_size // 30)
            draw.text((x + offset, y + offset), line, fill=(0, 0, 0, 110), font=font)
            draw.text((x, y), line, fill=color_hex, font=font)
        elif treatment == "outline":
            stroke_w = max(2, top_size // 40)
            draw.text(
                (x, y), line, fill=color_hex, font=font,
                stroke_width=stroke_w, stroke_fill="black",
            )
        else:
            draw.text((x, y), line, fill=color_hex, font=font)

    cx = size[0] / 2
    y_start = (size[1] - tot_h) / 2
    top_w = top_bb[2] - top_bb[0]
    top_h = top_bb[3] - top_bb[1]
    _draw_line(
        lines[0], top_font,
        int(cx - top_w / 2 - top_bb[0]),
        int(y_start - top_bb[1]),
    )
    if len(lines) > 1 and sub_font and sub_bb:
        gap = int(top_size * 0.18)
        sub_w = sub_bb[2] - sub_bb[0]
        _draw_line(
            lines[1], sub_font,
            int(cx - sub_w / 2 - sub_bb[0]),
            int(y_start + top_h + gap - sub_bb[1]),
        )
        # If there are more than 2 lines, treat the rest as extra sub
        # lines at the same sub size — stacked below.
        extra_y = y_start + top_h + gap - sub_bb[1] + (sub_bb[3] - sub_bb[1]) + gap
        for extra_line in lines[2:]:
            extra_bb = draw.textbbox((0, 0), extra_line, font=sub_font)
            extra_w = extra_bb[2] - extra_bb[0]
            _draw_line(
                extra_line, sub_font,
                int(cx - extra_w / 2 - extra_bb[0]),
                int(extra_y - extra_bb[1]),
            )
            extra_y += (extra_bb[3] - extra_bb[1]) + gap

    # Save
    proposals_dir = STATIC_DIR / "proposals"
    proposals_dir.mkdir(exist_ok=True)

    import uuid
    filename = f"design_{uuid.uuid4().hex[:8]}.png"
    filepath = proposals_dir / filename
    img.save(filepath, "PNG")

    logger.info(f"Text design saved: {filepath}")
    return filepath


async def validate_design_text(image_path: Path, intended_text: str = "") -> dict:
    """Use Claude vision to read text in a generated design and check for errors.

    If intended_text is provided, checks that the image text matches it exactly.
    If intended_text is empty, checks for any gibberish/garbled text that DALL-E
    may have added unprompted — short legible words or no text at all are fine.

    Returns {"valid": True/False, "found_text": "...", "errors": "..."}.
    """
    from app.agents import llm_client

    image_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")

    if intended_text:
        prompt = (
            f"Read ALL text visible in this image exactly as it appears, character by character. "
            f"The intended text was: \"{intended_text}\"\n\n"
            f"Compare the text in the image with the intended text. "
            f"Report any spelling mistakes, missing letters, extra letters, or garbled words. "
            f"Respond in this exact JSON format:\n"
            f'{{"found_text": "exact text you see in the image", "valid": true/false, "errors": "description of errors or empty string if none"}}'
        )
    else:
        prompt = (
            "Examine this image carefully for ANY visible text, letters, or words. "
            "If there is NO text at all, respond with valid=true. "
            "If there IS text, determine whether it is readable, correctly spelled English "
            "(or a recognizable word/phrase in any language). Short decorative words, "
            "brand-style marks, or intentional artistic text are fine. "
            "Flag as INVALID only if you see garbled, misspelled, or nonsensical text — "
            "random letter combinations, gibberish, or words that aren't real words in any language. "
            "Respond in this exact JSON format:\n"
            '{"found_text": "exact text you see (or empty string if none)", "valid": true/false, '
            '"errors": "description of gibberish found, or empty string if text is fine/absent"}'
        )

    client = llm_client._get_client()
    response = await llm_client._create_with_retry(
        client,
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        temperature=0,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_data}},
                {"type": "text", "text": prompt},
            ],
        }],
    )

    import json
    import re
    text = response.content[0].text

    # Find the last balanced {...} in the response so prepended prose
    # ("Here's my analysis: {...}") or trailing commentary doesn't break the
    # parser. Markdown fences are stripped first so they don't confuse the
    # brace counter.
    stripped = re.sub(r"```(?:json)?", "", text)
    candidates: list[str] = []
    depth = 0
    start = -1
    for i, ch in enumerate(stripped):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                candidates.append(stripped[start : i + 1])
                start = -1

    result: dict | None = None
    for blob in reversed(candidates):  # try the last one first
        try:
            parsed = json.loads(blob)
            if isinstance(parsed, dict) and "valid" in parsed:
                result = parsed
                break
        except json.JSONDecodeError:
            continue

    if result is None:
        logger.warning(f"Failed to parse validation response: {text}")
        result = {"valid": False, "found_text": "", "errors": "validator response unparseable"}

    logger.info(f"Text validation: valid={result.get('valid')}, errors={result.get('errors', '')}")
    return result


class TextValidationError(Exception):
    """Raised when DALL-E keeps producing misspelled/garbled text after all retries."""


async def generate_design_with_text_check(
    concept: str,
    intended_text: str = "",
    style: str = "bold graphic illustration",
    size: str = "1024x1024",
    quality: str = "hd",
    max_retries: int = 4,
) -> Path:
    """Generate a design via DALL-E, validate text with Claude, and regenerate if wrong.

    When intended_text is set: validates that the image text matches it exactly.
    When intended_text is empty: validates that no gibberish/garbled text was
    added by DALL-E (a common artifact). Legible English or no text = pass.

    Raises TextValidationError if no attempt produces a clean image.
    """
    for attempt in range(1, max_retries + 1):
        image_path = await generate_design(concept, style, size, quality)
        validation = await validate_design_text(image_path, intended_text)

        # Fail-closed: missing/false `valid` blocks the image. Only an explicit
        # True from the validator lets us return.
        if validation.get("valid", False):
            logger.info(f"Text validation passed on attempt {attempt}")
            return image_path

        logger.warning(
            f"Text validation failed (attempt {attempt}/{max_retries}): {validation.get('errors', '')}"
        )

        if attempt < max_retries:
            if intended_text:
                # Regenerate with explicit spelling correction
                concept = (
                    f"{concept}. "
                    f"CRITICAL FIX: The previous image had text errors: {validation['errors']}. "
                    f"The text MUST read EXACTLY: \"{intended_text}\" — "
                    f"spell every word correctly, letter by letter."
                )
            else:
                # Regenerate with explicit no-text instruction
                concept = (
                    f"{concept}. "
                    f"CRITICAL: The previous image contained garbled/gibberish text: "
                    f"\"{validation.get('found_text', '')}\". "
                    f"Do NOT include ANY text, letters, words, or typography in this image. "
                    f"Pure artwork/illustration only — zero text of any kind."
                )

    raise TextValidationError(
        f"Text validation failed after {max_retries} attempts; last errors: "
        f"{validation.get('errors', '')!r}, last text: {validation.get('found_text', '')!r}"
    )


async def remove_background(image_path: Path) -> Path:
    """Remove background from an image for print-ready transparent PNG.

    Uses rembg if available, otherwise returns the original image.
    """
    try:
        from rembg import remove
        from PIL import Image
        import io

        input_img = Image.open(image_path)
        output_img = remove(input_img)

        output_path = image_path.with_name(
            image_path.stem + "_nobg" + image_path.suffix
        )
        output_img.save(output_path, "PNG")
        logger.info(f"Background removed: {output_path}")
        return output_path

    except (ImportError, SystemExit):
        logger.warning("rembg not available (missing onnxruntime?), skipping background removal")
        return image_path
    except Exception as e:
        logger.warning(f"Background removal failed: {e}, using original")
        return image_path
