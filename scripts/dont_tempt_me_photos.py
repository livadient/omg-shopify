"""Generate 4 marketing photos for the 'Don't Tempt Me, I'll Say Yes' tee.

Style reference: white crew-neck tee with dark-red/maroon bold italic condensed
sans-serif capitals — "DON'T TEMPT ME" (larger) + "I'LL SAY YES" (smaller
subline). No brand signature underneath.

Outputs 4 photos:
  1. close-up of someone wearing it, shot from the back
  2. full-body shot of someone wearing it, back visible
  3. product shot, text on the BACK
  4. product shot, text on the FRONT

Run:
  .venv/Scripts/python -m scripts.dont_tempt_me_photos
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import httpx
from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.agents.image_client import validate_design_text  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OUT_DIR = ROOT / "static" / "proposals" / "dont_tempt_me"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SLOGAN_STYLE = (
    'Printed in two stacked lines: the top line reads "DON\'T TEMPT ME" in '
    "large bold italic condensed sans-serif capital letters in deep maroon red "
    "(hex #8B1A1A). Directly below, on its own line, the smaller subline reads "
    '"I\'LL SAY YES" in the same bold italic condensed sans-serif capitals and '
    "same deep maroon red, about 60 percent of the size of the top line. "
    "No other text, no brand name, no watermark, no signature anywhere on the shirt."
)

PROMPTS: dict[str, str] = {
    "01_closeup_back": (
        "Close-up lifestyle e-commerce photograph, shoulders and upper back of "
        "a young woman with brown hair in soft waves, standing with her back to "
        "the camera, wearing a plain white crew-neck cotton t-shirt. " + SLOGAN_STYLE +
        " The slogan is printed across the upper back, centered. Soft natural "
        "daylight, clean minimalist light-grey studio background, professional "
        "fashion e-commerce product photography, photorealistic, shallow depth "
        "of field on the background, sharp on the print. 4k."
    ),
    "02_fullbody_back": (
        "Full-body lifestyle e-commerce photograph of a young woman standing "
        "casually with her back three-quarters to the camera, wearing a plain "
        "white crew-neck cotton t-shirt tucked loosely into light blue straight-"
        "leg jeans and white sneakers. " + SLOGAN_STYLE +
        " The slogan is centered across the upper back and clearly readable. "
        "Clean minimalist light-grey studio background, soft natural daylight, "
        "professional fashion e-commerce photography, photorealistic, sharp focus, 4k."
    ),
    "03_product_back": (
        "Flat ghost-mannequin product photograph of a plain white crew-neck "
        "cotton t-shirt shown from the BACK, floating on a pure white seamless "
        "studio background, evenly lit, no shadows, no model, no hanger. "
        + SLOGAN_STYLE +
        " The slogan is printed across the upper back of the shirt, perfectly "
        "centered, crisp and clearly legible. Professional e-commerce apparel "
        "product photography, photorealistic, 4k, sharp focus on the print."
    ),
    "04_product_front": (
        "Flat ghost-mannequin product photograph of a plain white crew-neck "
        "cotton t-shirt shown from the FRONT, floating on a pure white seamless "
        "studio background, evenly lit, no shadows, no model, no hanger. "
        + SLOGAN_STYLE +
        " The slogan is printed across the upper chest of the shirt, perfectly "
        "centered, crisp and clearly legible. Professional e-commerce apparel "
        "product photography, photorealistic, 4k, sharp focus on the print."
    ),
}

INTENDED_TEXT = "DON'T TEMPT ME\nI'LL SAY YES"


async def _generate_one(
    client: AsyncOpenAI,
    http: httpx.AsyncClient,
    label: str,
    prompt: str,
    max_attempts: int = 3,
) -> Path | None:
    """Generate one image, validate the slogan spelling, retry if wrong."""
    current_prompt = prompt
    last_errors = ""
    last_found = ""
    for attempt in range(1, max_attempts + 1):
        logger.info(f"[{label}] attempt {attempt}/{max_attempts}")
        resp = await client.images.generate(
            model="dall-e-3",
            prompt=current_prompt,
            size="1024x1024",
            quality="hd",
            n=1,
            response_format="url",
        )
        url = resp.data[0].url
        logger.info(f"[{label}] revised prompt: {resp.data[0].revised_prompt[:140]}...")

        img = await http.get(url, timeout=60)
        img.raise_for_status()
        out_path = OUT_DIR / f"{label}_attempt{attempt}.png"
        out_path.write_bytes(img.content)
        logger.info(f"[{label}] saved {out_path.name}")

        validation = await validate_design_text(out_path, INTENDED_TEXT)
        last_errors = validation.get("errors", "")
        last_found = validation.get("found_text", "")
        if validation.get("valid"):
            final = OUT_DIR / f"{label}.png"
            final.write_bytes(out_path.read_bytes())
            logger.info(f"[{label}] PASSED on attempt {attempt} -> {final.name}")
            return final

        logger.warning(f"[{label}] failed: {last_errors} (saw: {last_found!r})")
        if attempt < max_attempts:
            current_prompt = (
                prompt + f" CRITICAL: the previous attempt had text errors ({last_errors}). "
                f'The slogan on the shirt MUST read EXACTLY, on two lines: '
                f'line 1 "DON\'T TEMPT ME" and line 2 "I\'LL SAY YES". Spell every letter correctly.'
            )

    # Keep the last attempt even if validation never passed, so the user can still pick one.
    fallback = OUT_DIR / f"{label}_BEST_EFFORT.png"
    fallback.write_bytes((OUT_DIR / f"{label}_attempt{max_attempts}.png").read_bytes())
    logger.error(
        f"[{label}] never passed validation. Last found: {last_found!r}. "
        f"Saved best effort as {fallback.name}"
    )
    return fallback


async def main() -> None:
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY missing from .env")

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    async with httpx.AsyncClient() as http:
        results = await asyncio.gather(
            *(_generate_one(client, http, label, prompt) for label, prompt in PROMPTS.items()),
            return_exceptions=True,
        )

    print("\n=== RESULTS ===")
    for label, result in zip(PROMPTS.keys(), results):
        if isinstance(result, Exception):
            print(f"  {label}: ERROR - {result}")
        elif result is None:
            print(f"  {label}: NO OUTPUT")
        else:
            print(f"  {label}: {result}")


if __name__ == "__main__":
    asyncio.run(main())
