"""Generate 4 marketing photos via GPT-Image-1 (OpenAI's newer model with
reliable in-image text rendering). Single-pass: text is baked into the prompt,
no Pillow compositing step required.

Outputs to static/proposals/dont_tempt_me_v3/.

Run:
  .venv/Scripts/python -m scripts.dont_tempt_me_gptimage            # all 4
  .venv/Scripts/python -m scripts.dont_tempt_me_gptimage 01_closeup_back
"""
from __future__ import annotations

import asyncio
import base64
import logging
import sys
from pathlib import Path

from openai import AsyncOpenAI
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OUT_DIR = ROOT / "static" / "proposals" / "dont_tempt_me_v3"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# When we pass the Pillow-rendered transparent design as a reference image, we
# just tell the model to copy that artwork verbatim — no typography drift.
ARTWORK_SPEC = (
    'The t-shirt has a SMALL MODEST CAPTION printed on it — NOT a big '
    'statement slogan, NOT a large graphic, NOT a billboard. Think of a '
    'minimal boutique tee where the print is a delicate, refined caption '
    'sized like a chest-pocket graphic, occupying only a narrow horizontal '
    'strip roughly 25-30% of the garment width with generous blank fabric '
    'on all sides. The caption is two lines of deep maroon capital letters '
    'as shown in the reference image. Copy the reference artwork verbatim: '
    'same wording, same spelling, same letter shapes, same typographic '
    'hierarchy (top line bold condensed caps, bottom line noticeably '
    'SMALLER and noticeably THINNER in a regular-weight sans-serif), same '
    'maroon color. Do NOT redraw, retype, re-spell, restyle, re-space, '
    'italicize, or reinterpret the text, and do NOT make the second line '
    'bold or the same weight as the top line. Do NOT scale the print up — '
    'keep it small and understated. Render it as a natural DTG fabric '
    'print that follows the garment\'s shading and folds.\n\n'
    'CRITICAL: the top line must contain ALL THREE WORDS "DON\'T TEMPT '
    'ME" — do NOT crop, truncate, cut off, or omit the word "ME". All '
    'three words must be fully visible on the garment, on one horizontal '
    'line. The second (smaller, thinner) line reads "I\'LL SAY YES" in '
    'full.\n\n'
    'Do NOT add quotation marks, extra text, brand name, signature, '
    'caption, label, or watermark anywhere in the image or on the shirt.'
)

SCENES: dict[str, dict] = {
    "01_closeup_back": {
        "prompt": (
            "Medium close-up lifestyle e-commerce photograph taken from directly "
            "behind a young woman. She is turned with her back fully to the "
            "camera -- her face is not visible. Her brown hair is pulled up "
            "into a high bun so the upper back of her t-shirt is completely "
            "unobstructed. The frame shows her from just above the bun down "
            "to her hips. She wears a plain white crew-neck cotton t-shirt. "
            f"The slogan is printed centered across the UPPER BACK of the shirt.\n\n{ARTWORK_SPEC}\n\n"
            "Soft natural daylight, clean minimalist light-grey studio "
            "background, professional fashion e-commerce product photography, "
            "photorealistic, sharp focus, 4k."
        ),
    },
    "02_fullbody_back": {
        "prompt": (
            "Full-body lifestyle e-commerce photograph of a young woman walking "
            "away from the camera, back view. She is fully turned away, her "
            "face is not visible. She wears a plain white crew-neck cotton "
            "t-shirt tucked loosely into light blue straight-leg jeans and "
            "white sneakers. Full body from head to feet in frame, the shirt's "
            "back print is clearly legible.\n\n"
            f"The slogan is printed centered across the UPPER BACK of the shirt.\n\n{ARTWORK_SPEC}\n\n"
            "Clean minimalist light-grey studio background, soft natural "
            "daylight, professional fashion e-commerce photography, "
            "photorealistic, 4k."
        ),
    },
    "03_product_back": {
        "prompt": (
            "Overhead flat-lay product photograph of a plain white crew-neck "
            "cotton t-shirt laid flat on a pure white seamless background, "
            "photographed straight down from above. The shirt is laid with the "
            "BACK facing up (inside tag/label visible at the collar). Short "
            "sleeves spread out symmetrically. Evenly lit, soft shadows, no "
            "model, no hanger.\n\n"
            f"The slogan is printed centered across the UPPER BACK area of the shirt.\n\n{ARTWORK_SPEC}\n\n"
            "Professional e-commerce apparel flat-lay photography, "
            "photorealistic, 4k, sharp focus."
        ),
    },
    "04_product_front": {
        "prompt": (
            "Overhead flat-lay product photograph of a plain white crew-neck "
            "cotton t-shirt laid flat on a pure white seamless background, "
            "photographed straight down from above. The shirt is laid with the "
            "FRONT facing up (crew neckline visible at the collar, no inside "
            "tag/label showing). Short sleeves spread out symmetrically. Evenly "
            "lit, soft shadows, no model, no hanger.\n\n"
            f"The slogan is printed centered across the UPPER CHEST area of the shirt.\n\n{ARTWORK_SPEC}\n\n"
            "Professional e-commerce apparel flat-lay photography, "
            "photorealistic, 4k, sharp focus."
        ),
    },
    # Male-model variants (Kyriaki 2026-04-23 feedback: "tops would be
    # better marketed if the model was a semi buffed man from the back,
    # let's do the woman but also the man").
    "01_closeup_back_male": {
        "prompt": (
            "Medium close-up lifestyle e-commerce photograph taken from directly "
            "behind a fit, semi-muscular young man. He is turned with his back "
            "fully to the camera -- his face is not visible. His short dark hair "
            "is neatly cut so the upper back of his t-shirt is completely "
            "unobstructed. The frame shows him from just above the neck down to "
            "his hips. He wears a plain white crew-neck cotton t-shirt that fits "
            "well across defined shoulders and a toned back (athletic, "
            "gym-regular physique, not bodybuilder). "
            f"The slogan is printed centered across the UPPER BACK of the shirt.\n\n{ARTWORK_SPEC}\n\n"
            "Soft natural daylight, clean minimalist light-grey studio "
            "background, professional fashion e-commerce product photography, "
            "photorealistic, sharp focus, 4k."
        ),
    },
    "02_fullbody_back_male": {
        "prompt": (
            "Full-body lifestyle e-commerce photograph of a fit, semi-muscular "
            "young man walking away from the camera, back view. He is fully "
            "turned away, his face is not visible. He wears a plain white "
            "crew-neck cotton t-shirt that fits well across defined shoulders "
            "and a toned back (athletic, gym-regular, not bodybuilder), tucked "
            "loosely into dark straight-leg jeans and white sneakers. Full body "
            "from head to feet in frame, the shirt's back print is clearly "
            "legible.\n\n"
            f"The slogan is printed centered across the UPPER BACK of the shirt.\n\n{ARTWORK_SPEC}\n\n"
            "Clean minimalist light-grey studio background, soft natural "
            "daylight, professional fashion e-commerce photography, "
            "photorealistic, 4k."
        ),
    },
}


SLOGAN_COLOR = "#8B1A1A"
TOP_TEXT = "DON'T TEMPT ME"
SUB_TEXT = "I'LL SAY YES"
# Top line: bold condensed (Impact). Kyriaki's feedback asks for a visibly
# thinner bottom line with a clearer size drop vs the top, so the sub
# uses a regular-weight sans instead of the same bold Impact.
TOP_FONT_CANDIDATES = [
    "C:/Windows/Fonts/impact.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
SUB_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _get_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for fp in candidates:
        if Path(fp).exists():
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default()


def render_transparent_design(
    out_path: Path,
    width: int = 2400,
    height: int = 1600,
    text_width_ratio: float = 0.55,
) -> Path:
    """Render the slogan as a standalone transparent PNG for Qstomizer/TJ upload.

    Also doubles as the reference image handed to gpt-image-1 for mockup
    generation — the transparent padding around the text gives the model
    safety margin so words like "ME" don't get cropped when it rescales the
    artwork onto the garment. 3:2 aspect roughly matches a chest/back print
    area so the model doesn't have to squish or crop anything.

    Upright (non-italic) to match the style spec. Top line fills ~55% of the
    canvas width (was 72% — dropped after gpt-image-1 kept cropping "ME"
    on the tee); the subline is 45% of the top's size in a thinner
    regular-weight font per Kyriaki's feedback.
    """
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    max_text_w = int(width * text_width_ratio)
    max_text_h = int(height * 0.70)

    top_size = int(height * 1.2)
    while top_size > 20:
        top_font = _get_font(TOP_FONT_CANDIDATES, top_size)
        sub_size = max(1, int(top_size * 0.45))
        sub_font = _get_font(SUB_FONT_CANDIDATES, sub_size)
        top_bb = draw.textbbox((0, 0), TOP_TEXT, font=top_font)
        sub_bb = draw.textbbox((0, 0), SUB_TEXT, font=sub_font)
        top_w, top_h = top_bb[2] - top_bb[0], top_bb[3] - top_bb[1]
        sub_w, sub_h = sub_bb[2] - sub_bb[0], sub_bb[3] - sub_bb[1]
        gap = int(top_size * 0.18)
        tot_h = top_h + gap + sub_h
        if max(top_w, sub_w) <= max_text_w and tot_h <= max_text_h:
            break
        top_size -= 6

    cx = width / 2
    y_start = (height - tot_h) / 2
    draw.text(
        (cx - top_w / 2 - top_bb[0], y_start - top_bb[1]),
        TOP_TEXT, fill=SLOGAN_COLOR, font=top_font,
    )
    draw.text(
        (cx - sub_w / 2 - sub_bb[0], y_start + top_h + gap - sub_bb[1]),
        SUB_TEXT, fill=SLOGAN_COLOR, font=sub_font,
    )

    # Keep the padded canvas as the reference image handed to gpt-image-1 —
    # the transparent margin gives the model safety room so it doesn't crop
    # words like "ME" when scaling onto the garment.
    canvas.save(out_path, "PNG")
    logger.info(f"Transparent design (padded, for gpt-image-1 reference) saved -> {out_path}")

    # Also write a tight-cropped version for TJ / Qstomizer upload — that
    # pipeline wants no blank margin so it sizes the print correctly.
    tj_path = out_path.with_name(out_path.stem + "_tj" + out_path.suffix)
    bbox = canvas.getbbox()
    tj_canvas = canvas.crop(bbox) if bbox else canvas
    tj_canvas.save(tj_path, "PNG")
    logger.info(f"Transparent design (tight-cropped, for TJ upload) saved -> {tj_path}")
    return out_path


async def generate(client: AsyncOpenAI, label: str, prompt: str, design_path: Path) -> Path:
    """Generate a scene via gpt-image-1 image-edit, using the transparent
    design PNG as the reference artwork so the typography stays pixel-identical
    across all 4 scenes.
    """
    logger.info(f"[{label}] generating via gpt-image-1 (image-edit)...")
    with design_path.open("rb") as f:
        resp = await client.images.edit(
            model="gpt-image-1",
            image=f,
            prompt=prompt,
            size="1024x1024",
            quality="high",
            n=1,
        )
    b64 = resp.data[0].b64_json
    out = OUT_DIR / f"{label}.png"
    out.write_bytes(base64.b64decode(b64))
    logger.info(f"[{label}] saved -> {out}")
    return out


async def main():
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY missing")
    args = sys.argv[1:] if len(sys.argv) > 1 else list(SCENES.keys()) + ["design"]
    valid = set(SCENES) | {"design"}
    bad = [t for t in args if t not in valid]
    if bad:
        raise SystemExit(f"Unknown targets: {bad}. Valid: {sorted(valid)}")

    scene_targets = [a for a in args if a in SCENES]

    # The transparent design is also the reference artwork for scene
    # generation, so always render it (and refresh it) when scenes are
    # requested — even if the caller didn't explicitly list "design".
    design_path = OUT_DIR / "design_transparent.png"
    if "design" in args or scene_targets:
        render_transparent_design(design_path)

    if not scene_targets:
        return

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    results = await asyncio.gather(
        *(generate(client, label, SCENES[label]["prompt"], design_path) for label in scene_targets),
        return_exceptions=True,
    )
    print("\n=== RESULTS ===")
    for label, r in zip(scene_targets, results):
        if isinstance(r, Exception):
            print(f"  {label}: ERROR - {r}")
        else:
            print(f"  {label}: {r}")


if __name__ == "__main__":
    asyncio.run(main())
