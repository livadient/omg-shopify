"""DALL-E 3 image generation wrapper for t-shirt designs."""
import base64
import logging
from pathlib import Path

import httpx
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


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

    Returns the path to the saved PNG file.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)

    # Try to load a bold font, fall back to default
    font = None
    font_paths = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",  # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux alt
        "C:/Windows/Fonts/arialbd.ttf",  # Windows
        "C:/Windows/Fonts/impact.ttf",  # Windows Impact
    ]
    for fp in font_paths:
        if Path(fp).exists():
            font = ImageFont.truetype(fp, 10)  # size set below
            break

    # Split text into lines and find optimal font size
    lines = text.upper().split("\n") if "\n" in text else [text.upper()]
    # If single long line, wrap it
    if len(lines) == 1 and len(lines[0]) > 20:
        words = lines[0].split()
        wrapped = []
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

    # Find the largest font size that fits
    margin = 80
    max_w = size[0] - margin * 2
    max_h = size[1] - margin * 2
    font_size = 200
    while font_size > 20:
        if font:
            test_font = ImageFont.truetype(font.path, font_size)
        else:
            test_font = ImageFont.load_default()
            break
        line_bboxes = [draw.textbbox((0, 0), line, font=test_font) for line in lines]
        total_w = max(bb[2] - bb[0] for bb in line_bboxes)
        line_height = max(bb[3] - bb[1] for bb in line_bboxes)
        total_h = line_height * len(lines) + (len(lines) - 1) * (font_size * 0.3)
        if total_w <= max_w and total_h <= max_h:
            break
        font_size -= 4

    if font:
        final_font = ImageFont.truetype(font.path, font_size)
    else:
        final_font = ImageFont.load_default()

    # Calculate total block height for centering
    line_bboxes = [draw.textbbox((0, 0), line, font=final_font) for line in lines]
    line_height = max(bb[3] - bb[1] for bb in line_bboxes)
    spacing = int(font_size * 0.3)
    total_height = line_height * len(lines) + spacing * (len(lines) - 1)
    y_start = (size[1] - total_height) // 2

    # Draw each line centered
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=final_font)
        text_w = bbox[2] - bbox[0]
        x = (size[0] - text_w) // 2
        y = y_start + i * (line_height + spacing)
        draw.text((x, y), line, fill="black", font=final_font)

    # Save
    proposals_dir = STATIC_DIR / "proposals"
    proposals_dir.mkdir(exist_ok=True)

    import uuid
    filename = f"design_{uuid.uuid4().hex[:8]}.png"
    filepath = proposals_dir / filename
    img.save(filepath, "PNG")

    logger.info(f"Text design saved: {filepath}")
    return filepath


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
