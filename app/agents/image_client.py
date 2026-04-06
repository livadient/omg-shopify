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
        f"Create a t-shirt design: {concept}. "
        f"Style: {style}. "
        "Requirements: solid white background, high contrast, clean edges suitable "
        "for DTG (direct-to-garment) printing. Bold and eye-catching. "
        "No copyrighted characters or logos. The design should be centered and "
        "work well printed on the front of a t-shirt."
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

    except ImportError:
        logger.warning("rembg not installed, skipping background removal")
        return image_path
    except Exception as e:
        logger.warning(f"Background removal failed: {e}, using original")
        return image_path
