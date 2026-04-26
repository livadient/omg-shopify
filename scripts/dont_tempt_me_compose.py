"""Generate 4 marketing photos for 'Don't Tempt Me, I'll Say Yes' tee.

Approach (avoids DALL-E text-on-fabric gibberish):
  1. DALL-E generates the 4 scenes with a BLANK white tee.
  2. Claude vision locates the print area (bounding box) on each scene.
  3. Pillow renders the two-line slogan in bold italic condensed maroon caps
     and composites it into the detected region.

Outputs go to static/proposals/dont_tempt_me_v2/ .
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

OUT_DIR = ROOT / "static" / "proposals" / "dont_tempt_me_v2"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SLOGAN_COLOR = "#8B1A1A"
TOP_TEXT = "DON'T TEMPT ME"
SUB_TEXT = "I'LL SAY YES"

FONT_CANDIDATES = [
    "C:/Windows/Fonts/impact.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def get_font(size: int) -> ImageFont.FreeTypeFont:
    for fp in FONT_CANDIDATES:
        if Path(fp).exists():
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default()


SCENES: dict[str, str] = {
    "01_closeup_back": (
        "Medium-shot photograph taken from behind a young woman walking away from "
        "the camera. She is turned away, back toward the lens. Her brown hair is "
        "pulled up into a high bun so her neck, shoulders, and entire upper back "
        "of the shirt are completely visible and unobstructed. Frame the shot "
        "from just above her bun down to her hips -- show the FULL upper half "
        "of the shirt from shoulders to waist. She wears a plain white crew-"
        "neck cotton t-shirt -- totally blank, NO print, NO text, NO graphics, "
        "NO logo anywhere on the shirt. Soft natural daylight, clean minimalist "
        "light-grey studio background, professional fashion e-commerce product "
        "photography, photorealistic, sharp focus, 4k."
    ),
    "02_fullbody_back": (
        "Full-body photograph of a young woman walking away from the camera, "
        "back view. She is turned away with her back to the lens, casually "
        "walking forward. She wears a plain white crew-neck cotton t-shirt -- "
        "totally blank, NO print, NO text, NO graphics, NO logo anywhere on the "
        "shirt -- tucked loosely into light blue straight-leg jeans and white "
        "sneakers. Full body from head to feet in frame. Clean minimalist "
        "light-grey studio background, soft natural daylight, professional "
        "fashion e-commerce photography, photorealistic, 4k."
    ),
    "03_product_back": (
        "Overhead flat-lay product photograph of a plain white crew-neck cotton "
        "t-shirt laid perfectly FLAT on a pure white seamless background, "
        "photographed straight down from above. The shirt is laid with the BACK "
        "FACING UP -- the inside tag/label is visible just inside the collar, "
        "and the front of the shirt is hidden against the background. Short "
        "sleeves spread out symmetrically. Evenly lit, soft shadows, no model, "
        "no hanger, no wrinkles. The shirt is totally blank -- NO print, NO "
        "text, NO graphics, NO logo anywhere. Professional e-commerce apparel "
        "flat-lay photography, photorealistic, 4k, sharp focus."
    ),
    "04_product_front": (
        "Flat ghost-mannequin product photograph of a completely plain white "
        "crew-neck cotton t-shirt shown from the FRONT, floating on a pure "
        "white seamless studio background, evenly lit, no shadows, no model, "
        "no hanger. The shirt is totally blank -- NO print, NO text, NO "
        "graphics, NO logo anywhere. Professional e-commerce apparel product "
        "photography, photorealistic, 4k, sharp focus."
    ),
}

FALLBACK_BOX = {
    "01_closeup_back":  (300, 380, 740, 620),
    "02_fullbody_back": (430, 290, 620, 400),
    "03_product_back":  (350, 280, 700, 500),
    "04_product_front": (350, 340, 700, 560),
}

# Labels where we skip Claude detection and use a fixed print-area directly on
# the DALL-E output. Product shots have predictable framing and Claude's strict
# "torso-only" interpretation tends to return a too-narrow bbox, shrinking the
# slogan to unreadable size.
FIXED_PRINT_BOX = {
    "03_product_back":  (295, 290, 720, 445),
    "04_product_front": (345, 305, 675, 440),
}

SIDE = {
    "01_closeup_back": "back", "02_fullbody_back": "back",
    "03_product_back": "back", "04_product_front": "front",
}


def _parse_json_obj(text: str) -> dict | None:
    """Extract the last balanced {...} JSON object from Claude's free-form response."""
    for m in reversed(list(re.finditer(r"\{[^{}]*\}", text, re.DOTALL))):
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
    return None


async def detect_shirt_bbox(image_path: Path) -> tuple[int, int, int, int] | None:
    """Ask Claude vision for the TORSO (not sleeves) bounding box of the visible
    white t-shirt fabric in a 1024x1024 image. The print area is later computed
    deterministically as a sub-rectangle of this box."""
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    client = llm_client._get_client()
    prompt = (
        "This image is 1024x1024 pixels. Find the bounding box of the visible "
        "WHITE T-SHIRT TORSO fabric only. Be STRICT: every pixel inside the "
        "box must be white shirt fabric.\n"
        "EXCLUDE everything else: the collar/neckline band, the shoulder seams, "
        "the sleeves, the wearer's hair (including any bun or ponytail that "
        "sits over the upper back), neck skin, face, arms, pants, and "
        "background. If a bun or hair obstructs the upper back, the TOP of "
        "the box (y1) must start BELOW that hair, on clean fabric. The TOP "
        "must also start BELOW the collar band, on clean torso fabric -- NOT "
        "on the collar itself.\n"
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
    threshold: int = 195,
    min_white_ratio: float = 0.60,
) -> tuple[int, int, int, int]:
    """Tighten the shirt bbox top down to the first row where the central
    portion of the bbox is actually white fabric. Claude often over-includes
    the hair/neck/collar band above the real fabric. Uses a loose "near-white"
    threshold (~195) because shirt fabric in these renders is typically
    RGB(210,210,210), not pure white.
    """
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
        white = (row[:, 0] > threshold) & (row[:, 1] > threshold) & (row[:, 2] > threshold)
        if white.mean() >= min_white_ratio:
            new_y1 = y
            break
    return (x1, new_y1, x2, y2)


def compute_print_area(
    shirt: tuple[int, int, int, int],
    top_offset_pct: float = 0.12,
    height_pct: float = 0.22,
    width_pct: float = 0.70,
) -> tuple[int, int, int, int]:
    """Deterministic sub-rectangle of the shirt torso.

    Defaults place the print centered horizontally at `top_offset_pct` of the
    shirt height from the top, sized at `width_pct` wide x `height_pct` tall.
    """
    sx1, sy1, sx2, sy2 = shirt
    sw, sh = sx2 - sx1, sy2 - sy1
    cx = (sx1 + sx2) // 2
    pw = int(sw * width_pct)
    ph = int(sh * height_pct)
    py1 = sy1 + int(sh * top_offset_pct)
    px1 = cx - pw // 2
    return (px1, py1, px1 + pw, py1 + ph)


# Per-label overrides — tuned for the framing DALL-E typically produces.
PRINT_GEOMETRY = {
    "01_closeup_back":  {"top_offset_pct": 0.08, "height_pct": 0.18, "width_pct": 0.70},
    "02_fullbody_back": {"top_offset_pct": 0.10, "height_pct": 0.22, "width_pct": 0.80},
    "03_product_back":  {"top_offset_pct": 0.14, "height_pct": 0.20, "width_pct": 0.65},
    "04_product_front": {"top_offset_pct": 0.18, "height_pct": 0.18, "width_pct": 0.60},
}


def render_slogan(target_w: int, target_h: int) -> Image.Image:
    """Render the two-line slogan in bold italic condensed maroon caps, sized
    to fit (target_w, target_h). Returns a transparent RGBA image."""
    pad = int(target_h * 0.35) + 20
    cw, ch = target_w + pad * 2, target_h + pad * 2
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    max_text_w = int(target_w * 0.98)
    max_text_h = int(target_h * 0.95)

    top_size = 400
    while top_size > 10:
        top_font = get_font(top_size)
        sub_size = max(1, int(top_size * 0.6))
        sub_font = get_font(sub_size)
        top_bb = draw.textbbox((0, 0), TOP_TEXT, font=top_font)
        sub_bb = draw.textbbox((0, 0), SUB_TEXT, font=sub_font)
        top_w = top_bb[2] - top_bb[0]
        top_h = top_bb[3] - top_bb[1]
        sub_w = sub_bb[2] - sub_bb[0]
        sub_h = sub_bb[3] - sub_bb[1]
        gap = int(top_size * 0.15)
        tot_h = top_h + gap + sub_h
        if max(top_w, sub_w) <= max_text_w and tot_h <= max_text_h:
            break
        top_size -= 4

    cx = cw / 2
    y_start = pad + (target_h - tot_h) / 2

    draw.text(
        (cx - top_w / 2 - top_bb[0], y_start - top_bb[1]),
        TOP_TEXT, fill=SLOGAN_COLOR, font=top_font,
    )
    draw.text(
        (cx - sub_w / 2 - sub_bb[0], y_start + top_h + gap - sub_bb[1]),
        SUB_TEXT, fill=SLOGAN_COLOR, font=sub_font,
    )

    # Italic shear: input_x = x - s*y -> content leans right at the top
    shear = 0.18
    sheared = canvas.transform(
        canvas.size, Image.AFFINE,
        (1, -shear, shear * pad, 0, 1, 0),
        resample=Image.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )
    return sheared.crop((pad, pad, pad + target_w, pad + target_h))


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
    """Ask Claude whether the image clearly shows the BACK of the subject."""
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    client = llm_client._get_client()
    prompt = (
        "Look at this photograph of a person in a white t-shirt. "
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
    # Optional CLI: a space-separated list of labels to regenerate (default: all)
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
