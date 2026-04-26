"""Compose-pipeline marketing-scene generator.

Each scene is built in 3 stages so the design lands on the model's tee
PIXEL-PERFECT — no gpt-image-1 reinterpretation, no hallucinated text,
no aspect-ratio drift between the on-model scene and the TJ Qstomizer
mockup (both end up showing the same design PNG).

Stages:
  1. gpt-image-1 generates a BLANK tee scene (no print at all).
  2. Claude vision returns the bounding box of the visible torso fabric.
  3. Pillow pastes the design PNG (preserving its aspect ratio) into a
     deterministic sub-rectangle of that bbox.

Used by `app.agents.design_creator.execute_approval` and
`scripts.refresh_all_product_images.run_phase3` so newly-approved Mango
designs and backfilled existing products both follow the same recipe.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path

import httpx
from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage 1 — blank-tee scene prompts
# ---------------------------------------------------------------------------
def _scene_prompts(tee_color: str) -> dict[str, str]:
    fabric = tee_color.lower()
    flat_bg = "pure white seamless" if fabric != "white" else "light grey seamless"
    no_print = (
        "The t-shirt is COMPLETELY BLANK — no print, no graphic, no text, no "
        "logo, no design of any kind on the fabric. Just plain solid "
        f"{fabric} cotton."
    )
    return {
        "01_closeup_back": (
            f"Medium close-up lifestyle e-commerce photograph taken from "
            f"directly behind a young woman. She is turned with her back fully "
            f"to the camera — her face is not visible. Her hair is pulled up "
            f"into a high bun so the upper back of her t-shirt is completely "
            f"unobstructed. The frame shows her from just above the bun down "
            f"to her hips. She wears a plain {fabric} crew-neck cotton "
            f"t-shirt.\n\n{no_print}\n\n"
            f"Soft natural daylight, clean minimalist light-grey studio "
            f"background, professional fashion e-commerce product photography, "
            f"photorealistic, sharp focus, 4k."
        ),
        "02_fullbody_back": (
            f"Full-body lifestyle e-commerce photograph of a young woman "
            f"walking away from the camera, back view. She is fully turned "
            f"away, her face is not visible. She wears a plain {fabric} "
            f"crew-neck cotton t-shirt tucked loosely into light blue "
            f"straight-leg jeans and white sneakers. Full body from head to "
            f"feet in frame, the back of the shirt is clearly visible.\n\n"
            f"{no_print}\n\n"
            f"Clean minimalist light-grey studio background, soft natural "
            f"daylight, professional fashion e-commerce photography, "
            f"photorealistic, 4k."
        ),
        "03_product_back": (
            f"Overhead flat-lay product photograph of a plain {fabric} "
            f"crew-neck cotton t-shirt laid flat on a {flat_bg} background, "
            f"photographed straight down from above. The shirt is laid with "
            f"the BACK facing up. Short sleeves spread out symmetrically. "
            f"Evenly lit, soft shadows, no model, no hanger.\n\n{no_print}\n\n"
            f"Professional e-commerce apparel flat-lay photography, "
            f"photorealistic, 4k, sharp focus."
        ),
        "04_hanger_back": (
            f"Product photograph of a plain {fabric} crew-neck cotton t-shirt "
            f"on a plain wooden clothes hanger, hung against a clean "
            f"minimalist light-grey wall. The shirt is facing the camera "
            f"straight-on from the back. Natural fabric drape, short sleeves, "
            f"even soft studio lighting.\n\n{no_print}\n\n"
            f"Professional e-commerce apparel product photography, "
            f"photorealistic, 4k, sharp focus."
        ),
        "01_closeup_back_male": (
            f"Medium close-up lifestyle e-commerce photograph taken from "
            f"directly behind a fit, semi-muscular young man. He is turned "
            f"with his back fully to the camera — his face is not visible. "
            f"His short dark hair is neatly cut so the upper back of his "
            f"t-shirt is completely unobstructed. The frame shows him from "
            f"just above the neck down to his hips. He wears a plain "
            f"{fabric} crew-neck cotton t-shirt that fits well across "
            f"defined shoulders and a toned back (athletic, gym-regular, "
            f"not bodybuilder).\n\n{no_print}\n\n"
            f"Soft natural daylight, clean minimalist light-grey studio "
            f"background, professional fashion e-commerce product "
            f"photography, photorealistic, sharp focus, 4k."
        ),
        "02_fullbody_back_male": (
            f"Full-body lifestyle e-commerce photograph of a fit, "
            f"semi-muscular young man walking away from the camera, back "
            f"view. He is fully turned away, his face is not visible. He "
            f"wears a plain {fabric} crew-neck cotton t-shirt that fits well "
            f"across defined shoulders and a toned back (athletic, "
            f"gym-regular, not bodybuilder), tucked loosely into dark "
            f"straight-leg jeans and white sneakers. Full body from head to "
            f"feet in frame, the back of the shirt is clearly visible.\n\n"
            f"{no_print}\n\n"
            f"Clean minimalist light-grey studio background, soft natural "
            f"daylight, professional fashion e-commerce photography, "
            f"photorealistic, 4k."
        ),
    }


# Per-scene print geometry — fraction of detected shirt bbox.
# `width_pct` ≈ 0.50 matches the print/tee ratio Qstomizer renders on the
# TJ mockup (Qstomizer's print area is ~35% of its 800px stage, but the
# Claude-detected bbox over-includes shoulders + collar, so 0.50 of bbox
# ≈ 35% of the visible tee — same visual as TJ). height_pct is derived
# from the design PNG's aspect ratio at paste time so 2-line slogans stay
# 2 lines, 4-line stacks stay 4 lines, and illustrations preserve aspect.
PRINT_GEOMETRY: dict[str, dict] = {
    # Sizing has two modes per scene:
    #  - image_width_pct: used for TEXT/slogan designs (aspect < 0.4),
    #    where the print is wide and short — width is the natural anchor.
    #  - image_max_dim_pct: used for IMAGE/illustration designs (aspect
    #    >= 0.4), where width-only sizing would make the print height
    #    explode past TJ's ~35% print area. This caps max(pw, ph).
    # The right one is auto-picked per design at the call site based on
    # the design's aspect ratio.
    "01_closeup_back":      {"top_offset_pct": 0.10, "width_pct": 0.50, "image_width_pct": 0.45, "image_max_dim_pct": 0.32},
    "02_fullbody_back":     {"top_offset_pct": 0.02, "width_pct": 0.52, "image_width_pct": 0.18, "image_max_dim_pct": 0.13},
    "03_product_back":      {"top_offset_pct": 0.16, "width_pct": 0.45, "image_width_pct": 0.42, "image_max_dim_pct": 0.32},
    "04_hanger_back":       {"top_offset_pct": 0.18, "width_pct": 0.42, "image_width_pct": 0.40, "image_max_dim_pct": 0.30},
    "01_closeup_back_male": {"top_offset_pct": 0.10, "width_pct": 0.50, "image_width_pct": 0.45, "image_max_dim_pct": 0.32},
    "02_fullbody_back_male":{"top_offset_pct": 0.10, "width_pct": 0.52, "image_width_pct": 0.24, "image_max_dim_pct": 0.18},
}


# ---------------------------------------------------------------------------
# Stage 1 — blank-tee scene generation
# ---------------------------------------------------------------------------
async def _generate_blank_scene(client, label: str, prompt: str, dest: Path) -> Path | None:
    try:
        resp = await client.images.generate(
            model="gpt-image-1", prompt=prompt,
            size="1024x1024", quality="high", n=1,
        )
        dest.write_bytes(base64.b64decode(resp.data[0].b64_json))
        return dest
    except Exception as e:
        logger.warning(f"[{label}] blank scene generation failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Stage 2 — fabric bounding box detection (Claude vision)
# ---------------------------------------------------------------------------
def _parse_json_obj(text: str) -> dict | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


async def _detect_shirt_bbox(
    image_path: Path, fabric: str
) -> tuple[tuple[int, int, int, int], int | None] | None:
    """Ask Claude for the back-torso bbox of the visible tee fabric AND the
    spine_x (vertical centerline of the visible back panel).

    Returns ((x1, y1, x2, y2), spine_x) or None on failure. The bbox
    excludes collar, sleeves, hair, neck. spine_x is the x-coordinate
    halfway between the two shoulder seams (or, for flat-lay/hanger shots,
    the centerline of the shirt). It's a more reliable horizontal anchor
    than the bbox midpoint, which can drift off-spine when Claude's bbox
    over-includes one shoulder/sleeve more than the other.
    """
    import anthropic
    if not settings.anthropic_api_key:
        return None
    cli = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    img_b64 = base64.b64encode(image_path.read_bytes()).decode()
    prompt = (
        f"This image is 1024x1024 pixels. Find the bounding box of the "
        f"visible {fabric.upper()} T-SHIRT TORSO/BACK fabric only. Be STRICT: "
        f"every pixel inside the box must be {fabric} shirt fabric.\n\n"
        f"EXCLUDE everything else: the collar/neckline band, the shoulder "
        f"seams, the sleeves, the wearer's hair (including any bun or "
        f"ponytail that sits over the upper back), neck skin, face, arms, "
        f"pants, and background. If a bun or hair obstructs the upper back, "
        f"the TOP of the box (y1) must start BELOW that hair, on clean "
        f"fabric. The TOP must also start BELOW the collar band, on clean "
        f"torso fabric — NOT on the collar itself.\n\n"
        f"ALSO return spine_x: the x-coordinate of the vertical centerline "
        f"of the visible back panel — exactly halfway between the two "
        f"shoulder seams (for flat-lay/hanger shots, the vertical "
        f"centerline of the shirt). This is where a centered print would "
        f"sit. spine_x is NOT necessarily the bbox midpoint — they differ "
        f"when the bbox includes one sleeve/arm more than the other.\n\n"
        f"Return ONLY this JSON, no other text:\n"
        f'{{"x1": <int>, "y1": <int>, "x2": <int>, "y2": <int>, "spine_x": <int>}}'
    )
    try:
        resp = await cli.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=200,
            temperature=0,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        parsed = _parse_json_obj(resp.content[0].text)
    except Exception as e:
        logger.warning(f"bbox detection failed: {e}")
        return None
    if not parsed or not all(k in parsed for k in ("x1", "y1", "x2", "y2")):
        return None
    try:
        bbox = (int(parsed["x1"]), int(parsed["y1"]),
                int(parsed["x2"]), int(parsed["y2"]))
        spine_x = int(parsed["spine_x"]) if "spine_x" in parsed else None
    except (TypeError, ValueError):
        return None
    x1, y1, x2, y2 = bbox
    if not (0 <= x1 < x2 <= 1024 and 0 <= y1 < y2 <= 1024):
        return None
    if spine_x is not None and not (0 <= spine_x <= 1024):
        spine_x = None
    return (bbox, spine_x)


def _snap_to_fabric_top(
    image_path: Path,
    shirt: tuple[int, int, int, int],
    fabric: str,
    max_snap_px: int = 80,
) -> tuple[int, int, int, int]:
    """Tighten the shirt bbox top down to the first row that's actually fabric.

    For white fabric we look for near-white pixels (> 195); for dark fabrics
    we look for near-black (< 80). Claude often over-includes hair/collar
    above the real fabric — this snaps it down.

    `max_snap_px` caps the downward shift so that long hair (which can
    extend 200+ px down the back in fullbody shots) doesn't drag the snap
    all the way to the waist, leaving only the lower back as the bbox and
    making the print land at the bottom of the shirt.
    """
    try:
        import numpy as np
    except ImportError:
        return shirt
    img = np.asarray(Image.open(image_path).convert("RGB"))
    x1, y1, x2, y2 = shirt
    cx = (x1 + x2) // 2
    sample_half = max(20, (x2 - x1) // 6)
    col_a = max(0, cx - sample_half)
    col_b = min(img.shape[1], cx + sample_half)

    is_white = fabric.lower() == "white"
    threshold = 195 if is_white else 80

    def is_fabric_row(y: int) -> bool:
        row = img[y, col_a:col_b]
        if is_white:
            mask = (row[:, 0] > threshold) & (row[:, 1] > threshold) & (row[:, 2] > threshold)
        else:
            mask = (row[:, 0] < threshold) & (row[:, 1] < threshold) & (row[:, 2] < threshold)
        return bool(mask.mean() >= 0.60)

    new_y1 = y1
    snap_limit = min(y2, img.shape[0], y1 + max_snap_px)
    for y in range(y1, snap_limit):
        if is_fabric_row(y):
            new_y1 = y
            break
    return (x1, new_y1, x2, y2)


# ---------------------------------------------------------------------------
# Stage 3 — paste design onto blank scene
# ---------------------------------------------------------------------------
def _compute_print_rect(
    shirt: tuple[int, int, int, int],
    design_aspect: float,
    top_offset_pct: float,
    width_pct: float,
    x_offset_pct: float = 0.0,
    image_width_pct: float | None = None,
    image_max_dim_pct: float | None = None,
    image_size: int = 1024,
    spine_x: int | None = None,
) -> tuple[int, int, int, int]:
    """Sub-rectangle of the shirt bbox where the design gets pasted.

    Width is `width_pct` of the bbox width — UNLESS `image_width_pct` is
    set, in which case width is that fraction of the image width (stable
    absolute pixel size, immune to bbox detection variance across
    re-rolls). Height is derived from the design's aspect ratio so 2-line
    / 4-line / illustration prints stay proportionally correct.

    Horizontal anchor priority:
      1. `spine_x` (Claude-detected back centerline) — most reliable, used
         by default whenever Claude returns it.
      2. bbox midpoint — fallback when spine_x is missing.
    `x_offset_pct` is then added on top as a fraction of bbox width
    (positive = right) for per-scene fine-tuning.
    """
    sx1, sy1, sx2, sy2 = shirt
    sw, sh = sx2 - sx1, sy2 - sy1
    if image_max_dim_pct is not None:
        # Size so max(pw, ph) == image_max_dim_pct * image_size — used for
        # image/illustration designs (aspect ratio ~ 1.0) where width-only
        # sizing would make the print's height blow up well past TJ's
        # ~35% print area.
        max_dim = image_size * image_max_dim_pct
        pw = int(max_dim / max(1.0, design_aspect))
    elif image_width_pct is not None:
        pw = int(image_size * image_width_pct)
    else:
        pw = int(sw * width_pct)
    # Cap pw to bbox width so the print can never extend past the visible
    # shirt fabric — important for fullbody scenes where image_width_pct
    # of the 1024 image can easily exceed the small detected shirt bbox.
    max_bbox_w = int(sw * 0.85)
    if pw > max_bbox_w:
        pw = max_bbox_w
    base_cx = spine_x if spine_x is not None else (sx1 + sx2) // 2
    cx = base_cx + int(sw * x_offset_pct)
    ph = int(pw * design_aspect)
    py1 = sy1 + int(sh * top_offset_pct)
    px1 = cx - pw // 2
    return (px1, py1, px1 + pw, py1 + ph)


def _paste_design(scene_path: Path, design_path: Path, rect: tuple[int, int, int, int]) -> Path:
    """Resize the design to fit `rect` (preserving aspect) and alpha-composite
    it onto the scene at that rect. Writes back to `scene_path`."""
    scene = Image.open(scene_path).convert("RGBA")
    design = Image.open(design_path).convert("RGBA")
    x1, y1, x2, y2 = rect
    target_w, target_h = x2 - x1, y2 - y1
    resized = design.resize((target_w, target_h), Image.LANCZOS)
    scene.alpha_composite(resized, dest=(x1, y1))
    scene.save(scene_path, "PNG")
    return scene_path


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Reading-B compose — paste the actually-rendered TJ Qstomizer mockup onto
# blank model scenes. Guarantees the on-model scene shows the EXACT same
# tee+print pixels the customer sees as the print preview.
# ---------------------------------------------------------------------------

# Which TJ mockup gender we paste for each scene label. Flat-lay + hanger
# don't have a model so the gender is arbitrary — we use female back to
# match the secondary card-image (TJ female back is the hover image).
_SCENE_TJ_GENDER: dict[str, str] = {
    "01_closeup_back":      "female",
    "02_fullbody_back":     "female",
    "03_product_back":      "female",
    "04_hanger_back":       "female",
    "01_closeup_back_male": "male",
    "02_fullbody_back_male": "male",
}


def _alpha_cutout_white_bg(mockup_path: Path) -> Image.Image:
    """Mask out the near-white background of a TJ mockup so we end up with
    a transparent-bg silhouette of the rendered tee+print. The mockup BG is
    a clean light grey/white, so a brightness threshold is enough — no need
    for a neural background remover (which fails on this Windows box's
    onnxruntime memory allocation anyway).
    """
    import numpy as np
    img = Image.open(mockup_path).convert("RGBA")
    arr = np.array(img)
    rgb = arr[:, :, :3]
    # BG = pixels where R, G, B are all near-white AND nearly equal — this
    # excludes anything tinted (e.g. maroon DTM print, white text on a
    # black tee fabric).
    near_white = (rgb > 240).all(axis=2)
    rg_sim = np.abs(rgb[:, :, 0].astype(int) - rgb[:, :, 1].astype(int)) < 8
    gb_sim = np.abs(rgb[:, :, 1].astype(int) - rgb[:, :, 2].astype(int)) < 8
    bg_mask = near_white & rg_sim & gb_sim
    arr[bg_mask, 3] = 0
    cutout = Image.fromarray(arr, mode="RGBA")
    bbox = cutout.getbbox()
    if bbox:
        cutout = cutout.crop(bbox)
    return cutout


async def compose_scenes_from_tj_mockups(
    tj_mockup_paths: dict[tuple[str, str], Path],
    out_dir: Path,
    tee_color: str,
    scene_filter: set[str] | None = None,
) -> dict[str, Path]:
    """Reading B: paste the actually-rendered TJ Qstomizer mockup tee onto
    each blank model scene. Result: pixel-perfect consistency between the
    on-model scene and the TJ mockup customers see at print time.

    `tj_mockup_paths` keys: (gender, placement) — must include
    ("male", "back") and ("female", "back") at minimum.
    """
    from openai import AsyncOpenAI

    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY missing — skipping TJ-paste compose")
        return {}

    male_back = tj_mockup_paths.get(("male", "back"))
    female_back = tj_mockup_paths.get(("female", "back"))
    if not (male_back and male_back.exists() and female_back and female_back.exists()):
        logger.warning("missing TJ male/back or TJ female/back mockup — cannot compose")
        return {}

    out_dir.mkdir(parents=True, exist_ok=True)

    # Pre-build alpha cutouts for each gender once (reused across 6 scenes)
    silhouettes: dict[str, Image.Image] = {
        "male": _alpha_cutout_white_bg(male_back),
        "female": _alpha_cutout_white_bg(female_back),
    }
    logger.info(
        f"compose-from-TJ: silhouettes male={silhouettes['male'].size} "
        f"female={silhouettes['female'].size}"
    )

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    prompts = _scene_prompts(tee_color)
    if scene_filter:
        prompts = {k: v for k, v in prompts.items() if k in scene_filter}

    # Stage 1 — blank scenes
    async def gen_blank(label: str, prompt: str) -> tuple[str, Path | None]:
        out = out_dir / f"{label}.png"
        path = await _generate_blank_scene(client, label, prompt, out)
        return (label, path)

    pairs = await asyncio.gather(*(gen_blank(l, p) for l, p in prompts.items()))
    blank_scenes = {l: p for l, p in pairs if p}

    # Stage 2+3 — bbox + paste the TJ silhouette
    async def composite(label: str, scene_path: Path) -> tuple[str, Path | None]:
        try:
            detected = await _detect_shirt_bbox(scene_path, tee_color)
            if not detected:
                logger.warning(f"[{label}] bbox failed, leaving blank scene")
                return (label, scene_path)
            bbox, _spine_x = detected
            bbox = _snap_to_fabric_top(scene_path, bbox, tee_color)
            bx1, by1, bx2, by2 = bbox

            gender = _SCENE_TJ_GENDER.get(label, "female")
            silhouette = silhouettes[gender]

            # Resize silhouette to span the detected tee bbox width,
            # preserving aspect. Slight upward bias on the paste y so the
            # collar lines up with the model's collar (TJ silhouettes
            # include the full collar band).
            sw, sh = silhouette.size
            target_w = bx2 - bx1
            scale = target_w / sw
            target_h = int(sh * scale)
            resized = silhouette.resize((target_w, target_h), Image.LANCZOS)

            scene = Image.open(scene_path).convert("RGBA")
            paste_y = max(0, by1 - int(target_h * 0.05))
            scene.alpha_composite(resized, dest=(bx1, paste_y))
            scene.save(scene_path, "PNG")
            return (label, scene_path)
        except Exception as e:
            logger.warning(f"[{label}] composite failed: {e}")
            return (label, scene_path)

    finished = await asyncio.gather(*(composite(l, p) for l, p in blank_scenes.items()))
    return {l: p for l, p in finished if p}


async def compose_marketing_scenes(
    design_path: Path,
    out_dir: Path,
    tee_color: str,
    scene_filter: set[str] | None = None,
) -> dict[str, Path]:
    """Compose all 6 marketing scenes for a product.

    Returns a label → Path dict of successfully composed scenes (subset of
    the 6 if anything failed mid-pipeline).
    """
    from openai import AsyncOpenAI

    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY missing — skipping compose")
        return {}
    if not design_path.exists():
        logger.warning(f"design PNG missing: {design_path}")
        return {}

    out_dir.mkdir(parents=True, exist_ok=True)
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    # Tight-crop the design before sizing — a padded 1024x1024 PNG (text in
    # the middle, transparent margins) renders the print way too small after
    # paste because the empty margins eat the print rect's pixels. Cropping
    # to the non-transparent bbox makes the text fill its target rect.
    with Image.open(design_path) as d:
        d_rgba = d.convert("RGBA")
        bbox = d_rgba.getbbox()
        if bbox and (bbox[2] - bbox[0] < d_rgba.size[0] or bbox[3] - bbox[1] < d_rgba.size[1]):
            tight_path = out_dir / "_design_tight.png"
            d_rgba.crop(bbox).save(tight_path, "PNG")
            design_path = tight_path
            dw, dh = (bbox[2] - bbox[0], bbox[3] - bbox[1])
            logger.info(f"compose: tight-cropped design {dw}x{dh} -> {tight_path.name}")
        else:
            dw, dh = d_rgba.size
    design_aspect = dh / dw if dw else 1.0
    logger.info(f"compose: design aspect={design_aspect:.2f}")

    prompts = _scene_prompts(tee_color)
    if scene_filter:
        prompts = {k: v for k, v in prompts.items() if k in scene_filter}

    # Stage 1 — generate blank scenes in parallel
    async def stage1(label: str, prompt: str) -> tuple[str, Path | None]:
        out = out_dir / f"{label}.png"
        path = await _generate_blank_scene(client, label, prompt, out)
        return (label, path)

    pairs = await asyncio.gather(*(stage1(l, p) for l, p in prompts.items()))
    blank_scenes: dict[str, Path] = {l: p for l, p in pairs if p}
    logger.info(f"compose: stage1 ok {len(blank_scenes)}/{len(prompts)}")

    # Auto-pick sizing mode based on design aspect: text/slogan designs
    # (aspect < 0.4) get width-based sizing; image/illustration designs
    # (aspect >= 0.4) get max-dimension sizing so square/tall artwork
    # doesn't exceed TJ's print area.
    is_image_design = design_aspect >= 0.4

    # Stage 2+3 — bbox + paste, in parallel per scene
    async def finish(label: str, scene_path: Path) -> tuple[str, Path | None]:
        try:
            detected = await _detect_shirt_bbox(scene_path, tee_color)
            if not detected:
                logger.warning(f"[{label}] bbox detection failed, skipping paste")
                return (label, scene_path)  # still return blank scene
            bbox, spine_x = detected
            bbox = _snap_to_fabric_top(scene_path, bbox, tee_color)
            geom = dict(PRINT_GEOMETRY.get(label, {"top_offset_pct": 0.12, "width_pct": 0.32}))
            if is_image_design:
                geom.pop("image_width_pct", None)  # use image_max_dim_pct
                # Image designs (square illustrations) sit visually higher
                # than the text designs at the same top_offset_pct because
                # they're taller. Shift up by 0.05 of bbox height so the
                # illustration sits on the upper-back, not mid-back.
                geom["top_offset_pct"] = geom.get("top_offset_pct", 0.10) - 0.05
            else:
                geom.pop("image_max_dim_pct", None)  # use image_width_pct
            # Horizontal anchor: blend image-center (stable) with Claude's
            # spine_x (catches actual model drift in frame). Text designs
            # use 70/30 toward image-center (text drift is less visually
            # obvious). Image designs use 30/70 toward spine_x — square
            # illustrations are heavy and an off-center print is far more
            # noticeable, so we trust the spine signal more aggressively.
            if spine_x is not None:
                if is_image_design:
                    anchor_x = int(0.30 * 512 + 0.70 * spine_x)
                else:
                    anchor_x = int(0.70 * 512 + 0.30 * spine_x)
            else:
                anchor_x = 512
            # Image designs on CLOSEUP shots tend to land slightly right
            # of true spine (gpt-image-1's blank-scene composition has a
            # slight rightward bias that spine_x doesn't fully catch).
            # Apply -20px nudge only to closeup scenes; fullbody/product/
            # hanger don't have the same bias and the spine blend alone
            # is enough centering.
            if is_image_design and "closeup" in label:
                anchor_x -= 20
            rect = _compute_print_rect(bbox, design_aspect, spine_x=None, **geom)
            px1, py1, px2, py2 = rect
            pw = px2 - px1
            px1 = anchor_x - pw // 2
            rect = (px1, py1, px1 + pw, py2)
            _paste_design(scene_path, design_path, rect)
            return (label, scene_path)
        except Exception as e:
            logger.warning(f"[{label}] paste failed: {e}")
            return (label, scene_path)

    finished = await asyncio.gather(*(finish(l, p) for l, p in blank_scenes.items()))
    return {l: p for l, p in finished if p}
