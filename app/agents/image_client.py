"""DALL-E 3 image generation wrapper for t-shirt designs."""
import base64
import logging
import random
from pathlib import Path

import httpx
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
    """Generate a t-shirt design using DALL-E 3 and save to static/.

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

    logger.info(f"Generating design: {concept[:80]}...")

    response = await client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size=size,
        quality=quality,
        n=1,
        response_format="url",
    )

    image_url = response.data[0].url
    revised_prompt = response.data[0].revised_prompt
    logger.info(f"DALL-E revised prompt: {revised_prompt[:100]}...")

    # Download the image
    async with httpx.AsyncClient() as http:
        img_resp = await http.get(image_url, timeout=30)
        img_resp.raise_for_status()

    # Save to static/proposals/
    proposals_dir = STATIC_DIR / "proposals"
    proposals_dir.mkdir(exist_ok=True)

    import uuid
    filename = f"design_{uuid.uuid4().hex[:8]}.png"
    filepath = proposals_dir / filename
    filepath.write_bytes(img_resp.content)

    logger.info(f"Design saved: {filepath}")
    return filepath


async def generate_text_design(
    text: str,
    style: str = "bold modern",
    size: tuple[int, int] = (1024, 1024),
) -> Path:
    """Generate a text-only design using Pillow typography.

    Picks a random color, font, treatment (plain/outline/shadow) and case
    on each call so successive slogan tees feel visually distinct instead of
    all being identical black-on-white Liberation Sans blocks.

    Returns the path to the saved PNG file.
    """
    from PIL import Image, ImageDraw, ImageFont

    # Randomized look — color, font, treatment, case
    color_hex = random.choice(TEXT_DESIGN_COLORS)
    available_fonts = [fp for fp in TEXT_DESIGN_FONTS if Path(fp).exists()]
    font_path = random.choice(available_fonts) if available_fonts else None
    treatment = random.choices(
        ["plain", "outline", "shadow"], weights=[70, 15, 15], k=1
    )[0]
    use_uppercase = random.random() < 0.7  # mostly all-caps, sometimes original case

    logger.info(
        f"Text design look: color={color_hex} treatment={treatment} "
        f"upper={use_uppercase} font={Path(font_path).name if font_path else 'default'}"
    )

    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Split text into lines (respect explicit newlines, otherwise wrap long lines)
    raw = text.upper() if use_uppercase else text
    lines = raw.split("\n") if "\n" in raw else [raw]
    if len(lines) == 1 and len(lines[0]) > 20:
        words = lines[0].split()
        wrapped: list[str] = []
        current = ""
        for w in words:
            test = f"{current} {w}".strip()
            if len(test) > 18 and current:
                wrapped.append(current)
                current = w
            else:
                current = test
        if current:
            wrapped.append(current)
        lines = wrapped

    # Find the largest font size that fits inside the canvas
    margin = 80
    max_w = size[0] - margin * 2
    max_h = size[1] - margin * 2
    font_size = 200
    final_font = None
    while font_size > 20:
        if not font_path:
            final_font = ImageFont.load_default()
            break
        test_font = ImageFont.truetype(font_path, font_size)
        line_bboxes = [draw.textbbox((0, 0), line, font=test_font) for line in lines]
        total_w = max(bb[2] - bb[0] for bb in line_bboxes)
        line_height = max(bb[3] - bb[1] for bb in line_bboxes)
        total_h = line_height * len(lines) + (len(lines) - 1) * (font_size * 0.3)
        if total_w <= max_w and total_h <= max_h:
            final_font = test_font
            break
        font_size -= 4

    if final_font is None:
        final_font = (
            ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
        )

    # Centering math
    line_bboxes = [draw.textbbox((0, 0), line, font=final_font) for line in lines]
    line_height = max(bb[3] - bb[1] for bb in line_bboxes)
    spacing = int(font_size * 0.3)
    total_height = line_height * len(lines) + spacing * (len(lines) - 1)
    y_start = (size[1] - total_height) // 2

    # Draw each line centered with the chosen treatment
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=final_font)
        text_w = bbox[2] - bbox[0]
        x = (size[0] - text_w) // 2
        y = y_start + i * (line_height + spacing)

        if treatment == "shadow":
            offset = max(4, font_size // 30)
            draw.text(
                (x + offset, y + offset), line,
                fill=(0, 0, 0, 110), font=final_font,
            )
            draw.text((x, y), line, fill=color_hex, font=final_font)
        elif treatment == "outline":
            stroke_w = max(2, font_size // 40)
            draw.text(
                (x, y), line, fill=color_hex, font=final_font,
                stroke_width=stroke_w, stroke_fill="black",
            )
        else:
            draw.text((x, y), line, fill=color_hex, font=final_font)

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
    text = response.content[0].text
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    try:
        result = json.loads(text.strip())
    except json.JSONDecodeError:
        # Fail-closed: if we can't parse the validator's response, treat as invalid
        # so the design either gets retried or dropped, rather than silently passing.
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
