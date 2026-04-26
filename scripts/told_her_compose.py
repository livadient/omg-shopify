"""Compose pipeline for 'Told Her She's The One, Not The Only One' tee.

gpt-image-1 refused to render a modest-sized print for this design no
matter how we tuned the reference PNG or prompt, so this script uses the
pixel-composite fallback (same approach as dont_tempt_me_compose.py):

  1. DALL-E 3 generates 4 scenes with a BLANK BLACK tee.
  2. Claude vision locates the black torso bbox on each scene.
  3. Pillow renders the two-line slogan in bold WHITE serif caps and
     composites it into a deliberately-small sub-rectangle of that box.

Guarantees a small, readable print regardless of what DALL-E or
gpt-image-1 would otherwise produce. Tradeoff: print is a pixel-paste,
so it looks slightly flatter than a true DTG render.

Outputs to static/proposals/told_her_shes_the_one_compose/.

Run:
  .venv/Scripts/python -m scripts.told_her_compose                  # all 4
  .venv/Scripts/python -m scripts.told_her_compose 02_fullbody_back
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import sys
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.agents import llm_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OUT_DIR = ROOT / "static" / "proposals" / "told_her_shes_the_one_compose"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SLOGAN_COLOR = "#FFFFFF"
LINE_1 = "TOLD HER SHE'S THE ONE."
LINE_2 = "NOT THE ONLY ONE."

FONT_CANDIDATES = [
    "C:/Windows/Fonts/timesbd.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def get_font(size: int) -> ImageFont.FreeTypeFont:
    for fp in FONT_CANDIDATES:
        if Path(fp).exists():
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default()


SCENES: dict[str, str] = {
    "01_closeup_back": (
        "Medium-shot photograph taken from behind a young woman walking away "
        "from the camera. She is turned away, back toward the lens. Her "
        "brown hair is pulled up into a high bun so her neck, shoulders, "
        "and entire upper back of the shirt are completely visible and "
        "unobstructed. Frame the shot from just above her bun down to her "
        "hips -- show the FULL upper half of the shirt from shoulders to "
        "waist. She wears a plain BLACK crew-neck cotton t-shirt -- totally "
        "blank, NO print, NO text, NO graphics, NO logo anywhere on the "
        "shirt. Soft natural daylight, clean minimalist light-grey studio "
        "background, professional fashion e-commerce product photography, "
        "photorealistic, sharp focus, 4k."
    ),
    "02_fullbody_back": (
        "Full-body fashion photograph of a young woman standing with her "
        "BACK fully to the camera. She is turned completely away, back "
        "toward the lens, her face not visible. She wears a plain BLACK "
        "crew-neck cotton t-shirt -- totally blank, NO print, NO text, NO "
        "graphics, NO logo anywhere on the shirt -- tucked loosely into "
        "light blue straight-leg jeans and white sneakers. FRAMING: tight "
        "full-body crop, the subject FILLS the frame vertically from just "
        "above her head down to her feet, taking up at least 80% of the "
        "vertical frame height. The shirt torso must be LARGE and "
        "PROMINENT in the frame -- NOT a distant shot. Clean minimalist "
        "light-grey studio background, soft natural daylight, professional "
        "fashion e-commerce photography, photorealistic, 4k."
    ),
    "03_product_back": (
        "Overhead flat-lay product photograph of a plain BLACK crew-neck "
        "cotton t-shirt laid perfectly FLAT on a pure white seamless "
        "background, photographed straight down from above. The shirt is "
        "laid with the BACK FACING UP -- the inside tag/label is visible "
        "just inside the collar, and the front of the shirt is hidden "
        "against the background. Short sleeves spread out symmetrically. "
        "Evenly lit, soft shadows, no model, no hanger, no wrinkles. The "
        "shirt is totally blank -- NO print, NO text, NO graphics, NO logo "
        "anywhere. Professional e-commerce apparel flat-lay photography, "
        "photorealistic, 4k, sharp focus."
    ),
    "04_hanger_back": (
        "Product photograph of a plain BLACK crew-neck cotton t-shirt on a "
        "plain wooden clothes hanger, hung against a clean minimalist "
        "light-grey wall. The shirt is facing the camera straight-on from "
        "the BACK so the upper back is clearly visible. The shirt is "
        "totally blank -- NO print, NO text, NO graphics, NO logo anywhere. "
        "Natural fabric drape, short sleeves, even soft studio lighting, "
        "professional e-commerce apparel product photography, "
        "photorealistic, 4k, sharp focus."
    ),
}

# Fallback bboxes if Claude fails — deliberately narrower/shorter than the
# dont_tempt_me version to enforce a smaller caption-sized print.
FALLBACK_BOX = {
    "01_closeup_back":  (380, 360, 660, 480),
    "02_fullbody_back": (460, 290, 580, 360),
    "03_product_back":  (400, 320, 660, 440),
    "04_hanger_back":   (400, 340, 660, 440),
}

# Labels where we skip Claude detection and use a fixed print-area directly
# on the DALL-E output. Product and hanger shots have predictable framing.
FIXED_PRINT_BOX = {
    "03_product_back": (400, 320, 660, 440),
    "04_hanger_back":  (400, 340, 660, 440),
}

# Print geometry overrides per label — smaller than the DTM version because
# Kyriaki flagged the gpt-image-1 renders as "still too big" even at 30%
# back-width; this script targets 22-28% of the torso width.
PRINT_GEOMETRY = {
    "01_closeup_back":  {"top_offset_pct": 0.10, "height_pct": 0.14, "width_pct": 0.45},
    "02_fullbody_back": {"top_offset_pct": 0.12, "height_pct": 0.16, "width_pct": 0.55},
    "03_product_back":  {"top_offset_pct": 0.16, "height_pct": 0.14, "width_pct": 0.42},
    "04_hanger_back":   {"top_offset_pct": 0.18, "height_pct": 0.14, "width_pct": 0.42},
}


def _parse_json_obj(text: str) -> dict | None:
    for m in reversed(list(re.finditer(r"\{[^{}]*\}", text, re.DOTALL))):
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
    return None


async def detect_shirt_bbox(image_path: Path) -> tuple[int, int, int, int] | None:
    """Ask Claude vision for the TORSO (not sleeves) bounding box of the
    visible BLACK t-shirt fabric in a 1024x1024 image."""
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    client = llm_client._get_client()
    prompt = (
        "This image is 1024x1024 pixels. Find the bounding box of the "
        "visible BLACK T-SHIRT TORSO fabric only. Be STRICT: every pixel "
        "inside the box must be black shirt fabric.\n"
        "EXCLUDE everything else: the collar/neckline band, the shoulder "
        "seams, the sleeves, the wearer's hair (including any bun or "
        "ponytail that sits over the upper back), neck skin, face, arms, "
        "pants, and background. If a bun or hair obstructs the upper back, "
        "the TOP of the box (y1) must start BELOW that hair, on clean "
        "fabric. The TOP must also start BELOW the collar band, on clean "
        "torso fabric -- NOT on the collar itself.\n"
        "Return ONLY this JSON, no other text:\n"
        '{"x1": <int>, "y1": <int>, "x2": <int>, "y2": <int>}'
    )
    resp = await llm_client._create_with_retry(
        client,
        model="claude-sonnet-4-20250514",
        max_tokens=200,
        temperature=0,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    text = resp.content[0].text
    parsed = _parse_json_obj(text)
    if not parsed or not all(k in parsed for k in ("x1", "y1", "x2", "y2")):
        logger.warning(f"shirt bbox parse failed, raw: {text[:200]}")
        return None
    try:
        x1, y1, x2, y2 = (int(parsed["x1"]), int(parsed["y1"]),
                          int(parsed["x2"]), int(parsed["y2"]))
    except (TypeError, ValueError):
        return None
    if not (0 <= x1 < x2 <= 1024 and 0 <= y1 < y2 <= 1024):
        logger.warning(f"shirt bbox out of range: {(x1, y1, x2, y2)}")
        return None
    return (x1, y1, x2, y2)


def snap_to_fabric_top(
    image_path: Path,
    shirt: tuple[int, int, int, int],
    threshold: int = 60,
    min_black_ratio: float = 0.60,
) -> tuple[int, int, int, int]:
    """Tighten the shirt bbox top down to the first row where the central
    portion of the bbox is actually black fabric. Threshold inverted vs
    the white-shirt version — pixel values below ~60 count as 'black
    enough' to be fabric."""
    import numpy as np
    img = np.asarray(Image.open(image_path).convert("RGB"))
    x1, y1, x2, y2 = shirt
    cx = (x1 + x2) // 2
    sample_half = max(20, (x2 - x1) // 6)
    col_a = max(0, cx - sample_half)
    col_b = min(img.shape[1], cx + sample_half)

    new_y1 = y1
    for y in range(y1, min(y2, img.shape[0])):
        row = img[y, col_a:col_b]
        black = (row[:, 0] < threshold) & (row[:, 1] < threshold) & (row[:, 2] < threshold)
        if black.mean() >= min_black_ratio:
            new_y1 = y
            break
    return (x1, new_y1, x2, y2)


def compute_print_area(
    shirt: tuple[int, int, int, int],
    top_offset_pct: float = 0.12,
    height_pct: float = 0.16,
    width_pct: float = 0.48,
) -> tuple[int, int, int, int]:
    sx1, sy1, sx2, sy2 = shirt
    sw, sh = sx2 - sx1, sy2 - sy1
    cx = (sx1 + sx2) // 2
    pw = int(sw * width_pct)
    ph = int(sh * height_pct)
    py1 = sy1 + int(sh * top_offset_pct)
    px1 = cx - pw // 2
    return (px1, py1, px1 + pw, py1 + ph)


def render_slogan(target_w: int, target_h: int) -> Image.Image:
    """Render the two-line slogan in bold WHITE serif caps, sized to fit
    (target_w, target_h). No italic shear — upright serif."""
    pad = 20
    cw, ch = target_w + pad * 2, target_h + pad * 2
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    max_text_w = int(target_w * 0.98)
    max_text_h = int(target_h * 0.95)

    size = 400
    while size > 10:
        font = get_font(size)
        bb1 = draw.textbbox((0, 0), LINE_1, font=font)
        bb2 = draw.textbbox((0, 0), LINE_2, font=font)
        w1 = bb1[2] - bb1[0]
        h1 = bb1[3] - bb1[1]
        w2 = bb2[2] - bb2[0]
        h2 = bb2[3] - bb2[1]
        gap = int(size * 0.20)
        tot_h = h1 + gap + h2
        if max(w1, w2) <= max_text_w and tot_h <= max_text_h:
            break
        size -= 4

    cx = cw / 2
    y_start = pad + (target_h - tot_h) / 2

    draw.text(
        (cx - w1 / 2 - bb1[0], y_start - bb1[1]),
        LINE_1, fill=SLOGAN_COLOR, font=font,
    )
    draw.text(
        (cx - w2 / 2 - bb2[0], y_start + h1 + gap - bb2[1]),
        LINE_2, fill=SLOGAN_COLOR, font=font,
    )

    return canvas.crop((pad, pad, pad + target_w, pad + target_h))


async def _one_dalle_call(client: AsyncOpenAI, http: httpx.AsyncClient, prompt: str, dest: Path) -> None:
    resp = await client.images.generate(
        model="dall-e-3", prompt=prompt, size="1024x1024", quality="hd",
        n=1, response_format="url",
    )
    logger.info(f"revised prompt: {resp.data[0].revised_prompt[:120]}...")
    img = await http.get(resp.data[0].url, timeout=60)
    img.raise_for_status()
    dest.write_bytes(img.content)


async def _verify_back_orientation(image_path: Path) -> bool:
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    client = llm_client._get_client()
    prompt = (
        "Look at this photograph of a person in a black t-shirt. "
        "Are we seeing the BACK of the person and the BACK of the shirt "
        "(the person is turned away from the camera, their face is NOT visible)? "
        'Return ONLY: {"back_visible": true} or {"back_visible": false}'
    )
    resp = await llm_client._create_with_retry(
        client, model="claude-sonnet-4-20250514", max_tokens=80, temperature=0,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": prompt},
        ]}],
    )
    parsed = _parse_json_obj(resp.content[0].text) or {}
    return bool(parsed.get("back_visible"))


async def generate_scene(client: AsyncOpenAI, http: httpx.AsyncClient, label: str, prompt: str) -> Path:
    out = OUT_DIR / f"{label}_scene.png"
    needs_back = label in ("01_closeup_back", "02_fullbody_back")
    for attempt in range(1, 5):
        logger.info(f"[{label}] generating blank-tee scene (attempt {attempt})...")
        await _one_dalle_call(client, http, prompt, out)
        if not needs_back:
            break
        if await _verify_back_orientation(out):
            logger.info(f"[{label}] back orientation verified")
            break
        logger.warning(f"[{label}] front visible, regenerating")
        prompt = (
            prompt + " VERY IMPORTANT: the person must be facing COMPLETELY AWAY "
            "from the camera. The camera sees ONLY their back. Their face, eyes, "
            "and mouth must NOT be visible at all."
        )
    logger.info(f"[{label}] scene saved -> {out.name}")
    return out


async def process_one(client, http, label, prompt) -> Path:
    scene_path = await generate_scene(client, http, label, prompt)
    if label in FIXED_PRINT_BOX:
        bbox = FIXED_PRINT_BOX[label]
        logger.info(f"[{label}] using fixed print bbox {bbox}")
    else:
        shirt = await detect_shirt_bbox(scene_path)
        if shirt is None:
            bbox = FALLBACK_BOX[label]
            logger.warning(f"[{label}] shirt detection failed, using fallback print bbox {bbox}")
        else:
            snapped = snap_to_fabric_top(scene_path, shirt)
            bbox = compute_print_area(snapped, **PRINT_GEOMETRY.get(label, {}))
            logger.info(f"[{label}] shirt={shirt} snapped={snapped} print={bbox}")

    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    slogan = render_slogan(w, h)

    scene = Image.open(scene_path).convert("RGBA")
    r, g, b, a = slogan.split()
    a = a.point(lambda v: int(v * 0.95))
    slogan = Image.merge("RGBA", (r, g, b, a))
    scene.alpha_composite(slogan, dest=(x1, y1))

    final_path = OUT_DIR / f"{label}.png"
    scene.convert("RGB").save(final_path, "PNG")
    logger.info(f"[{label}] final composite -> {final_path.name}")
    return final_path


async def main():
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY missing")
    targets = sys.argv[1:] if len(sys.argv) > 1 else list(SCENES.keys())
    bad = [t for t in targets if t not in SCENES]
    if bad:
        raise SystemExit(f"Unknown labels: {bad}. Valid: {list(SCENES.keys())}")
    logger.info(f"Processing: {targets}")

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    async with httpx.AsyncClient() as http:
        results = await asyncio.gather(
            *(process_one(client, http, label, SCENES[label]) for label in targets),
            return_exceptions=True,
        )
    print("\n=== RESULTS ===")
    for label, r in zip(targets, results):
        if isinstance(r, Exception):
            print(f"  {label}: ERROR - {r}")
        else:
            print(f"  {label}: {r}")


if __name__ == "__main__":
    asyncio.run(main())
