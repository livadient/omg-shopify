"""Redesign the remaining OMG slogan tees with meaning-matched typography.

Uses the Pillow-rendered transparent PNG as the gpt-image-1 reference so the
typography stays pixel-identical across all 4 scenes per tee. Each design
picks fonts that reinforce the slogan's vibe:

  Impact brutalist → overthinker / couch potato
  Monospace        → emotional damage / social battery
  Minimal thin     → nihilistic penguin
  Serif + script   → told her she's the one / chaos coordinator
  Rounded playful  → mushroom kingdom therapy

Outputs to static/proposals/<slug>/ per tee.

Run:
  .venv/Scripts/python -m scripts.redesign_omg_tees              # all 8
  .venv/Scripts/python -m scripts.redesign_omg_tees chaos_coordinator
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

OUT_ROOT = ROOT / "static" / "proposals"

# Font candidates (first-exists wins on each platform)
F_IMPACT       = ["C:/Windows/Fonts/impact.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
F_SANS_BOLD    = ["C:/Windows/Fonts/arialbd.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
F_SANS_REG     = ["C:/Windows/Fonts/arial.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
F_SANS_ITALIC  = ["C:/Windows/Fonts/ariali.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf"]
F_SERIF_BOLD   = ["C:/Windows/Fonts/timesbd.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"]
F_SERIF_REG    = ["C:/Windows/Fonts/times.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"]
F_MONO         = ["C:/Windows/Fonts/consola.ttf",
                  "C:/Windows/Fonts/cour.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"]
F_MONO_BOLD    = ["C:/Windows/Fonts/consolab.ttf",
                  "C:/Windows/Fonts/courbd.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"]
F_SCRIPT       = ["C:/Windows/Fonts/segoesc.ttf",
                  "C:/Windows/Fonts/gabriola.ttf",
                  "C:/Windows/Fonts/ITCEDSCR.ttf"]
F_COMIC        = ["C:/Windows/Fonts/comicbd.ttf",
                  "C:/Windows/Fonts/comic.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]


def _pick_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for fp in candidates:
        if Path(fp).exists():
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default()


# Each tee: white shirt, BACK placement, big statement print.
# design.lines = list of (text, font_candidates, size_ratio, hex_color)
# size_ratio is relative to canvas height. Empty text = vertical gap.
TEES: dict[str, dict] = {
    "professional_overthinker": {
        "title": "Professional Overthinker",
        "style_desc": (
            "HUGE bold condensed BLACK sans-serif capital letters (Impact "
            "style), two lines stacked tight: \"PROFESSIONAL\" on top, "
            "\"OVERTHINKER\" on bottom — both the same large size"
        ),
        "design": {
            "lines": [
                ("PROFESSIONAL", F_IMPACT, 0.30, "#000000"),
                ("OVERTHINKER",  F_IMPACT, 0.30, "#000000"),
            ],
            "width": 2400, "height": 1600,
        },
    },
    "nihilistic_penguin": {
        "title": "Nihilistic Penguin",
        "style_desc": (
            "a single horizontal line of thin italic BLACK sans-serif "
            "text reading \"nothing matters but the penguin walks anyway\""
        ),
        "design": {
            "lines": [
                ("nothing matters but the penguin", F_SANS_ITALIC, 0.30, "#000000"),
                ("walks anyway",                    F_SANS_ITALIC, 0.30, "#000000"),
            ],
            "width": 2400, "height": 1200,
        },
    },
    "chaos_coordinator": {
        "title": "Chaos Coordinator",
        "style_desc": (
            "a contrast of two lines: the word \"CHAOS\" in enormous bold "
            "condensed BLACK all-caps sans-serif (Impact style), and below "
            "it the word \"coordinator\" in a small elegant black cursive "
            "script — emphasizing the contradiction between the two words"
        ),
        "design": {
            "lines": [
                ("CHAOS",       F_IMPACT, 0.52, "#000000"),
                ("coordinator", F_SCRIPT, 0.20, "#000000"),
            ],
            "width": 2400, "height": 1800,
        },
    },
    "emotional_damage": {
        "title": "Emotional Damage Calculator",
        "style_desc": (
            "monospaced BLACK terminal/calculator-style text, styled like a "
            "damage readout from a game: \"+9999\" on the first line very "
            "large, and \"EMOTIONAL DAMAGE\" on the second line in monospace "
            "caps — LCD / retro-gaming readout feel"
        ),
        "design": {
            "lines": [
                ("+9999",            F_MONO_BOLD, 0.42, "#000000"),
                ("EMOTIONAL DAMAGE", F_MONO_BOLD, 0.14, "#000000"),
            ],
            "width": 2400, "height": 1800,
        },
    },
    "certified_couch_potato": {
        "title": "Certified Couch Potato",
        "style_desc": (
            "three stacked lines in MASSIVE bold condensed BLACK all-caps "
            "brutalist sans-serif (Impact style), one word per line, "
            "tightly packed: \"CERTIFIED\" / \"COUCH\" / \"POTATO\""
        ),
        "design": {
            "lines": [
                ("CERTIFIED", F_IMPACT, 0.24, "#000000"),
                ("COUCH",     F_IMPACT, 0.28, "#000000"),
                ("POTATO",    F_IMPACT, 0.28, "#000000"),
            ],
            "width": 2200, "height": 2400,
        },
    },
    "mushroom_kingdom_therapy": {
        "title": "Mushroom Kingdom Therapy",
        "style_desc": (
            "three stacked lines of rounded playful BOLD BLACK "
            "sans-serif (all caps), softly psychedelic, centered: "
            "\"MUSHROOM\" / \"KINGDOM\" / \"THERAPY\""
        ),
        "design": {
            "lines": [
                ("MUSHROOM", F_COMIC, 0.22, "#000000"),
                ("KINGDOM",  F_COMIC, 0.22, "#000000"),
                ("THERAPY",  F_COMIC, 0.22, "#000000"),
            ],
            "width": 2400, "height": 2200,
        },
    },
    "social_battery": {
        "title": "Social Battery",
        "style_desc": (
            "a monospaced BLACK terminal/LCD-style readout with a battery "
            "indicator: \"SOCIAL BATTERY\" on the first line in caps "
            "monospace, and \"1%\" on a second line very large to emphasize "
            "how drained — evokes a phone battery warning"
        ),
        "design": {
            "lines": [
                ("SOCIAL BATTERY", F_MONO_BOLD, 0.15, "#000000"),
                ("1%",             F_MONO_BOLD, 0.48, "#000000"),
            ],
            "width": 2400, "height": 1800,
        },
    },
    "told_her_shes_the_one": {
        "title": "Told Her She's The One",
        # 2026-04-23 — Kyriaki: 4-line layout instead of 2-line, slightly
        # bigger so it reads better on TJ. The more-square aspect (vs the
        # wide 2-line banner) makes Qstomizer auto-scale the print larger
        # in the fixed print area. Natural hierarchy: setup pair on top
        # (TOLD HER / SHE'S THE ONE), kicker pair below (NOT THE / ONLY ONE).
        "tee_color": "black",
        "style_desc": (
            "a MODEST CAPTION in WHITE SERIF CAPITAL letters "
            "(Times Bold / Garamond style) on a black tee, reading "
            "\"TOLD HER / SHE'S THE ONE / NOT THE / ONLY ONE\" stacked "
            "on FOUR separate lines (exactly 4 lines, not 2, not 3). "
            "Think minimal boutique tee with a refined stacked caption "
            "sitting high on the upper back, with generous blank black "
            "fabric around it. Each line CENTERED. Keep it understated "
            "— no billboard sizing"
        ),
        "design": {
            "lines": [
                ("TOLD HER",      F_SERIF_BOLD, 0.09, "#FFFFFF"),
                ("SHE'S THE ONE", F_SERIF_BOLD, 0.09, "#FFFFFF"),
                ("NOT THE",       F_SERIF_BOLD, 0.09, "#FFFFFF"),
                ("ONLY ONE",      F_SERIF_BOLD, 0.09, "#FFFFFF"),
            ],
            "width": 2000, "height": 2000,
        },
    },
}


def render_transparent_design(slug: str, cfg: dict, out_path: Path) -> Path:
    design = cfg["design"]
    lines = design["lines"]
    width = design["width"]
    height = design["height"]

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    rendered = []
    total_h = 0
    gap = int(height * 0.03)
    for text, font_candidates, size_ratio, color in lines:
        size_px = int(height * size_ratio)
        font = _pick_font(font_candidates, size_px)
        if text:
            bb = draw.textbbox((0, 0), text, font=font)
            w, h = bb[2] - bb[0], bb[3] - bb[1]
            while w > width * 0.92 and size_px > 20:
                size_px -= 8
                font = _pick_font(font_candidates, size_px)
                bb = draw.textbbox((0, 0), text, font=font)
                w, h = bb[2] - bb[0], bb[3] - bb[1]
        else:
            bb = (0, 0, 0, int(size_px * 0.5))
            w, h = 0, bb[3]
        rendered.append((text, font, bb, w, h, color))
        total_h += h
    total_h += gap * (len(lines) - 1)

    y = (height - total_h) / 2
    for text, font, bb, w, h, color in rendered:
        if text:
            x = (width - w) / 2 - bb[0]
            draw.text((x, y - bb[1]), text, fill=color, font=font)
        y += h + gap

    canvas.save(out_path, "PNG")
    logger.info(f"[{slug}] transparent design (padded) -> {out_path}")

    tj_path = out_path.with_name(out_path.stem + "_tj" + out_path.suffix)
    bbox = canvas.getbbox()
    (canvas.crop(bbox) if bbox else canvas).save(tj_path, "PNG")
    logger.info(f"[{slug}] transparent design (tight) -> {tj_path}")
    return out_path


def build_scenes(cfg: dict) -> dict[str, str]:
    style = cfg["style_desc"]
    tee_color = cfg.get("tee_color", "white")
    flat_bg = "light grey seamless" if tee_color == "white" else "pure white seamless"
    artwork_spec = (
        f'The t-shirt has a large printed graphic centered on the upper '
        f'back. The print is: {style}. The print must be the EXACT artwork '
        f'shown in the reference image — copy wording, spelling, letter '
        f'shapes, layout, weights, sizes and colour verbatim. Do NOT '
        f'redraw, retype, re-spell, restyle, or reinterpret the text. The '
        f'print should be BIG and bold, filling roughly 50-60% of the '
        f'shirt\'s upper back width, like a statement slogan tee. Render '
        f'it as a natural DTG fabric print that follows the garment\'s '
        f'shading and folds. Every word must be fully visible with margin '
        f'on all sides — do NOT crop, truncate, or cut off any word. Do '
        f'NOT add quotation marks, extra text, brand name, signature, '
        f'caption, label, or watermark anywhere in the image.'
    )

    return {
        "01_closeup_back": (
            f"Medium close-up lifestyle e-commerce photograph taken from "
            f"directly behind a young woman. She is turned with her back "
            f"fully to the camera — her face is not visible. Her hair is "
            f"pulled up into a high bun so the upper back of her t-shirt is "
            f"completely unobstructed. The frame shows her from just above "
            f"the bun down to her hips. She wears a plain {tee_color} "
            f"crew-neck cotton t-shirt.\n\n"
            f"{artwork_spec}\n\n"
            "Soft natural daylight, clean minimalist light-grey studio "
            "background, professional fashion e-commerce product "
            "photography, photorealistic, sharp focus, 4k."
        ),
        "02_fullbody_back": (
            f"Full-body lifestyle e-commerce photograph of a young woman "
            f"walking away from the camera, back view. She is fully turned "
            f"away, her face is not visible. She wears a plain {tee_color} "
            f"crew-neck cotton t-shirt tucked loosely into light blue "
            f"straight-leg jeans and white sneakers. Full body from head "
            f"to feet in frame, the shirt's back print is clearly legible.\n\n"
            f"{artwork_spec}\n\n"
            "Clean minimalist light-grey studio background, soft natural "
            "daylight, professional fashion e-commerce photography, "
            "photorealistic, 4k."
        ),
        "03_product_back": (
            f"Overhead flat-lay product photograph of a plain {tee_color} "
            f"crew-neck cotton t-shirt laid flat on a {flat_bg} "
            f"background, photographed straight down from above. The shirt "
            f"is laid with the BACK facing up. Short sleeves spread out "
            f"symmetrically. Evenly lit, soft shadows, no model, no "
            f"hanger.\n\n"
            f"{artwork_spec}\n\n"
            "Professional e-commerce apparel flat-lay photography, "
            "photorealistic, 4k, sharp focus."
        ),
        "04_hanger_back": (
            f"Product photograph of a plain {tee_color} crew-neck cotton "
            f"t-shirt on a plain wooden clothes hanger, hung against a "
            f"clean minimalist light-grey wall. The shirt is facing the "
            f"camera straight-on from the back so the back print is "
            f"clearly visible. Natural fabric drape, short sleeves, even "
            f"soft studio lighting.\n\n"
            f"{artwork_spec}\n\n"
            "Professional e-commerce apparel product photography, "
            "photorealistic, 4k, sharp focus."
        ),
        # Male-model variants (Kyriaki 2026-04-23 feedback: "tops would be
        # better marketed if the model was a semi buffed man from the back,
        # let's do the woman but also the man").
        "01_closeup_back_male": (
            f"Medium close-up lifestyle e-commerce photograph taken from "
            f"directly behind a fit, semi-muscular young man. He is turned "
            f"with his back fully to the camera — his face is not visible. "
            f"His short dark hair is neatly cut so the upper back of his "
            f"t-shirt is completely unobstructed. The frame shows him from "
            f"just above the neck down to his hips. He wears a plain "
            f"{tee_color} crew-neck cotton t-shirt that fits well across "
            f"defined shoulders and a toned back (athletic, not "
            f"bodybuilder — gym-regular physique).\n\n"
            f"{artwork_spec}\n\n"
            "Soft natural daylight, clean minimalist light-grey studio "
            "background, professional fashion e-commerce product "
            "photography, photorealistic, sharp focus, 4k."
        ),
        "02_fullbody_back_male": (
            f"Full-body lifestyle e-commerce photograph of a fit, "
            f"semi-muscular young man walking away from the camera, back "
            f"view. He is fully turned away, his face is not visible. He "
            f"wears a plain {tee_color} crew-neck cotton t-shirt that fits "
            f"well across defined shoulders and a toned back (athletic, "
            f"gym-regular, not bodybuilder), tucked loosely into dark "
            f"straight-leg jeans and white sneakers. Full body from head "
            f"to feet in frame, the shirt's back print is clearly "
            f"legible.\n\n"
            f"{artwork_spec}\n\n"
            "Clean minimalist light-grey studio background, soft natural "
            "daylight, professional fashion e-commerce photography, "
            "photorealistic, 4k."
        ),
    }


async def generate_scene(
    client: AsyncOpenAI,
    slug: str,
    label: str,
    prompt: str,
    ref_path: Path,
    out_dir: Path,
) -> Path:
    logger.info(f"[{slug}/{label}] generating via gpt-image-1 (image-edit)...")
    with ref_path.open("rb") as f:
        resp = await client.images.edit(
            model="gpt-image-1",
            image=f,
            prompt=prompt,
            size="1024x1024",
            quality="high",
            n=1,
        )
    b64 = resp.data[0].b64_json
    out = out_dir / f"{label}.png"
    out.write_bytes(base64.b64decode(b64))
    logger.info(f"[{slug}/{label}] saved -> {out}")
    return out


async def run_tee(client: AsyncOpenAI, slug: str, cfg: dict, scene_filter: set[str] | None = None) -> list:
    out_dir = OUT_ROOT / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    design_path = out_dir / "design_transparent.png"
    render_transparent_design(slug, cfg, design_path)

    scenes = build_scenes(cfg)
    if scene_filter:
        scenes = {k: v for k, v in scenes.items() if k in scene_filter}
    logger.info(f"[{slug}] '{cfg['title']}' — generating {len(scenes)} scenes")
    results = await asyncio.gather(
        *(
            generate_scene(client, slug, label, prompt, design_path, out_dir)
            for label, prompt in scenes.items()
        ),
        return_exceptions=True,
    )
    return list(zip(scenes.keys(), results))


async def main():
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY missing")

    # Parse args: first any matching slugs, remaining treated as scene filter.
    # e.g. `redesign_omg_tees told_her_shes_the_one 02_fullbody_back`
    raw = sys.argv[1:] if len(sys.argv) > 1 else []
    slug_args = [a for a in raw if a in TEES]
    scene_args = [a for a in raw if a not in TEES]
    if raw and not slug_args:
        raise SystemExit(f"Unknown slugs: {raw}. Valid: {sorted(TEES)}")
    slugs = slug_args or list(TEES.keys())
    scene_filter = set(scene_args) or None

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    all_results = await asyncio.gather(
        *(run_tee(client, slug, TEES[slug], scene_filter) for slug in slugs)
    )

    print("\n=== RESULTS ===")
    for slug, results in zip(slugs, all_results):
        print(f"\n[{slug}] {TEES[slug]['title']}")
        for label, r in results:
            if isinstance(r, Exception):
                print(f"  {label}: ERROR - {r}")
            else:
                print(f"  {label}: {r}")


if __name__ == "__main__":
    asyncio.run(main())
