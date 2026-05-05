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


TEXT_DESIGN_LAYOUTS = ("hierarchy", "stacked", "single", "inverted", "stagger")


def _layout_line_scales(layout: str, n_lines: int) -> list[float]:
    """Return per-line size factors (relative to the largest line = 1.0).

    Each layout shapes the slogan differently so successive slogan tees
    don't all look like big-top + small-sub:

    - hierarchy: lines[0] = 1.0, the rest = 0.45 (Don't Tempt Me style)
    - stacked:   every line = 0.95 (Normal People Scare Me style)
    - inverted:  lines[0] = 0.45, last = 1.0, mid = 0.45 (small setup → BIG punchline)
    - single:    only lines[0] = 1.0; extra lines collapsed onto one line via space-join
    - stagger:   line in the middle = 1.0, others = 0.55 (visual emphasis dead-centre)

    For a single-line slogan every layout collapses to one big line.
    """
    if n_lines == 1:
        return [1.0]
    if layout == "stacked":
        return [0.95] * n_lines
    if layout == "inverted":
        scales = [0.45] * n_lines
        scales[-1] = 1.0
        return scales
    if layout == "single":
        # Caller is expected to pre-flatten lines for "single", but fall
        # back to scaling the first line large if multi-line slipped through.
        return [1.0] + [0.0] * (n_lines - 1)
    if layout == "stagger":
        scales = [0.55] * n_lines
        scales[n_lines // 2] = 1.0
        return scales
    # default: hierarchy
    return [1.0] + [0.45] * (n_lines - 1)


async def generate_text_design(
    text: str,
    style: str = "bold modern",
    size: tuple[int, int] = (1024, 1024),
    layout: str = "hierarchy",
) -> Path:
    """Generate a text-only design using Pillow typography.

    `layout` selects the size pattern across lines (see
    _layout_line_scales). Default 'hierarchy' matches the Kyriaki-approved
    Don't Tempt Me template; other values give Mango variety so slogan
    tees don't all read as big-top + small-sub.

    Picks a random color, font pair, treatment (plain/outline/shadow)
    and case on each call so successive slogan tees feel visually
    distinct beyond just the layout choice.

    Print scale is generous (~70% of canvas width) — Vangelis flagged
    on 2026-05-05 that the previous 55% was too small.

    Returns the path to the saved PNG file.
    """
    from PIL import Image, ImageDraw, ImageFont
    if layout not in TEXT_DESIGN_LAYOUTS:
        logger.warning(f"Unknown layout {layout!r}, falling back to 'hierarchy'")
        layout = "hierarchy"

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

    raw = text.upper() if use_uppercase else text
    lines = raw.split("\n")
    if layout == "single":
        # Collapse any newlines into a single line so it renders as one mega line.
        lines = [" ".join(l.strip() for l in lines if l.strip())]
    # Drop empty lines that would otherwise force odd vertical spacing.
    lines = [l for l in lines if l.strip()] or [raw or " "]

    scales = _layout_line_scales(layout, len(lines))
    # Wider print — bumped from 0.55 → 0.70 on 2026-05-05 per Vangelis.
    text_width_ratio = 0.70
    max_text_w = int(size[0] * text_width_ratio)
    max_text_h = int(size[1] * 0.78)

    logger.info(
        f"Text design layout={layout} scales={scales} "
        f"width_ratio={text_width_ratio} treatment={treatment} "
        f"upper={use_uppercase} top_font="
        f"{Path(top_font_path).name if top_font_path else 'default'}"
    )

    def _load(fp: str | None, sz: int):
        if not fp:
            return ImageFont.load_default()
        return ImageFont.truetype(fp, sz)

    # Pick a per-line font: lines with scale==1.0 use the bold/condensed top
    # font, smaller scaled lines use the regular sub font for visible weight
    # contrast on hierarchy/inverted/stagger layouts.
    def _font_for(scale: float, base_size: int):
        size_px = max(1, int(base_size * scale))
        path = top_font_path if scale >= 0.95 else sub_font_path
        return _load(path, size_px), size_px

    # Find the largest base size that fits the widest line + total height.
    base_size = 480
    fitted_lines: list[tuple[str, "ImageFont.FreeTypeFont", tuple[int, int, int, int]]] = []
    while base_size > 20:
        fitted_lines.clear()
        max_w = 0
        tot_h = 0
        gap_estimate = int(base_size * 0.16)
        for line, scale in zip(lines, scales):
            if scale <= 0.0:
                continue
            font, _ = _font_for(scale, base_size)
            bb = draw.textbbox((0, 0), line, font=font)
            w = bb[2] - bb[0]
            h = bb[3] - bb[1]
            max_w = max(max_w, w)
            tot_h += h
            fitted_lines.append((line, font, bb))
        if fitted_lines:
            tot_h += gap_estimate * (len(fitted_lines) - 1)
        if max_w <= max_text_w and tot_h <= max_text_h:
            break
        base_size -= 8

    def _draw_line(line: str, font, x: int, y: int, line_size: int):
        if treatment == "shadow":
            offset = max(4, line_size // 30)
            draw.text((x + offset, y + offset), line, fill=(0, 0, 0, 110), font=font)
            draw.text((x, y), line, fill=color_hex, font=font)
        elif treatment == "outline":
            stroke_w = max(2, line_size // 40)
            draw.text(
                (x, y), line, fill=color_hex, font=font,
                stroke_width=stroke_w, stroke_fill="black",
            )
        else:
            draw.text((x, y), line, fill=color_hex, font=font)

    # Render fitted lines centered horizontally, stacked vertically.
    cx = size[0] / 2
    gap = int(base_size * 0.16)
    total_height = sum((bb[3] - bb[1]) for _, _, bb in fitted_lines)
    total_height += gap * max(0, len(fitted_lines) - 1)
    y_cursor = (size[1] - total_height) / 2
    for line, font, bb in fitted_lines:
        line_w = bb[2] - bb[0]
        line_h = bb[3] - bb[1]
        line_size = font.size if hasattr(font, "size") else base_size
        _draw_line(
            line, font,
            int(cx - line_w / 2 - bb[0]),
            int(y_cursor - bb[1]),
            line_size,
        )
        y_cursor += line_h + gap

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
