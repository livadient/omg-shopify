"""Generate 4 marketing photos per reference tee via gpt-image-1 image-edit.

Takes each reference image from C:/Users/vangelisl/Downloads/attachments/ as
the source artwork and produces a matched quartet of scene compositions:

  01_closeup_<placement>   — medium close-up, model's back or front
  02_fullbody_<placement>  — full-body walking shot
  03_product_<placement>   — overhead flat-lay, print side up
  04_product_<opposite>    — overhead flat-lay, opposite side up

Outputs to static/proposals/<slug>/ per reference.

Run:
  .venv/Scripts/python -m scripts.tee_scenes_from_refs              # all refs
  .venv/Scripts/python -m scripts.tee_scenes_from_refs normal_people_scare_me
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

ATTACH_DIR = Path("C:/Users/vangelisl/Downloads/attachments")
OUT_ROOT = ROOT / "static" / "proposals"

# Font candidates per style (first-exists wins)
FONT_SANS_BOLD = [
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
FONT_SANS_REG = [
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
FONT_SERIF_BOLD = [
    "C:/Windows/Fonts/timesbd.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
]
FONT_SCRIPT = [
    "C:/Windows/Fonts/segoesc.ttf",   # Segoe Script
    "C:/Windows/Fonts/gabriola.ttf",  # Gabriola fallback
    "C:/Windows/Fonts/ITCEDSCR.ttf",
]


def _pick_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for fp in candidates:
        if Path(fp).exists():
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default()


REFERENCES: dict[str, dict] = {
    "i_dont_get_drunk": {
        "ref": ATTACH_DIR / "image1.jpeg",
        "tee_color": "white",
        "placement": "front",
        "slogan_desc": (
            'a single horizontal line of small, slim, lowercase BLACK '
            'sans-serif text reading exactly: "I don\'t get drunk. I get '
            'awesome" — no other text, no caps, no brand name'
        ),
        # Transparent design spec: list of (text, font_family, size_ratio,
        # color). size_ratio is relative to the canvas height.
        "design": {
            "lines": [
                ("I don't get drunk. I get awesome", FONT_SANS_BOLD, 0.18, "#000000"),
            ],
            "width": 2400, "height": 800,
        },
    },
    "normal_people_scare_me": {
        "ref": ATTACH_DIR / "image2.jpeg",
        "tee_color": "black",
        "placement": "back",
        "slogan_desc": (
            'four lines of bold WHITE SERIF CAPITAL letters (Times / '
            'Garamond style), one word per line, stacked vertically and '
            'centered, printed at a modest scale that fills roughly 30-40% '
            'of the shirt back width (NOT oversized), matching the '
            'proportions of the reference image'
        ),
        # Reduced from 0.22 to 0.12 — Kyriaki flagged the previous render
        # as "a bit too big" vs the reference photo. Smaller ratio +
        # larger canvas = more transparent padding, which makes
        # gpt-image-1 render the print at a proportional size on the tee.
        "design": {
            "lines": [
                ("NORMAL", FONT_SERIF_BOLD, 0.12, "#FFFFFF"),
                ("PEOPLE", FONT_SERIF_BOLD, 0.12, "#FFFFFF"),
                ("SCARE",  FONT_SERIF_BOLD, 0.12, "#FFFFFF"),
                ("ME",     FONT_SERIF_BOLD, 0.12, "#FFFFFF"),
            ],
            "width": 2000, "height": 2400,
        },
    },
    "sexi_madafaka": {
        "ref": ATTACH_DIR / "image3.jpeg",
        "tee_color": "black",
        "placement": "front",
        "slogan_desc": (
            'two centered lines of WHITE lowercase Greek text in a clean '
            'sans-serif: "σέξι" on the first line and "μαδαφάκα" on the '
            'second. Spell every Greek letter correctly.'
        ),
        "design": {
            "lines": [
                ("σέξι",     FONT_SANS_REG, 0.30, "#FFFFFF"),
                ("μαδαφάκα", FONT_SANS_REG, 0.30, "#FFFFFF"),
            ],
            "width": 2400, "height": 1400,
        },
    },
    "to_the_person_behind_me": {
        "ref": ATTACH_DIR / "image4.jpeg",
        "tee_color": "black",
        "placement": "back",
        "slogan_desc": (
            'a multi-line WHITE print. Top block, bold sans-serif CAPS: '
            '"TO THE PERSON" on line 1, "BEHIND ME:" on line 2. Then a '
            'blank line. Then the main block in larger bold sans-serif '
            'CAPS: "YOU ARE" on one line, "AMAZING," on the next, '
            '"BEAUTIFUL AND" on the next, "ENOUGH." on the next. Finally '
            'a handwritten white script signature underneath reading '
            '"Remember that" (mixed case, cursive).'
        ),
        "design": {
            "lines": [
                ("TO THE PERSON",    FONT_SANS_BOLD,   0.10, "#FFFFFF"),
                ("BEHIND ME:",       FONT_SANS_BOLD,   0.10, "#FFFFFF"),
                ("",                 FONT_SANS_BOLD,   0.06, "#FFFFFF"),  # gap
                ("YOU ARE",          FONT_SANS_BOLD,   0.13, "#FFFFFF"),
                ("AMAZING,",         FONT_SANS_BOLD,   0.13, "#FFFFFF"),
                ("BEAUTIFUL AND",    FONT_SANS_BOLD,   0.13, "#FFFFFF"),
                ("ENOUGH.",          FONT_SANS_BOLD,   0.13, "#FFFFFF"),
                ("Remember that",    FONT_SCRIPT,      0.11, "#FFFFFF"),
            ],
            "width": 2400, "height": 2800,
        },
    },
}


def render_transparent_design(slug: str, cfg: dict, out_path: Path) -> Path:
    """Render the tee slogan as a stacked transparent PNG for TJ upload."""
    design = cfg["design"]
    lines = design["lines"]
    width = design["width"]
    height = design["height"]

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # First pass: compute font sizes and line heights.
    rendered = []
    total_h = 0
    gap = int(height * 0.04)
    for text, font_candidates, size_ratio, color in lines:
        size_px = int(height * size_ratio)
        font = _pick_font(font_candidates, size_px)
        if text:
            bb = draw.textbbox((0, 0), text, font=font)
            w, h = bb[2] - bb[0], bb[3] - bb[1]
            # Shrink if too wide for 92% of canvas.
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

    # Tight-cropped version for TJ upload.
    tj_path = out_path.with_name(out_path.stem + "_tj" + out_path.suffix)
    bbox = canvas.getbbox()
    (canvas.crop(bbox) if bbox else canvas).save(tj_path, "PNG")
    logger.info(f"[{slug}] transparent design (tight) -> {tj_path}")
    return out_path


def build_scenes(cfg: dict) -> dict[str, str]:
    """Build the 4 scene prompts for a given reference config."""
    tee_color = cfg["tee_color"]
    placement = cfg["placement"]  # "front" or "back"
    slogan = cfg["slogan_desc"]

    artwork_spec = (
        f'The t-shirt is a plain {tee_color} crew-neck cotton tee. The print '
        f'on the shirt is: {slogan}. The print must be the EXACT artwork '
        f'shown in the reference image — copy wording, spelling, letter '
        f'shapes, layout, weights, sizes and colour verbatim. Do NOT '
        f'redraw, retype, re-spell, restyle, or reinterpret the text. '
        f'Render it as a natural DTG fabric print that follows the '
        f'garment\'s shading and folds. Every word must be fully visible '
        f'within the print area with margin on all sides — do NOT crop, '
        f'truncate, or cut off any word. Do NOT add quotation marks, '
        f'extra text, other brand names, signatures, captions, labels, or '
        f'watermarks anywhere in the image.'
    )

    def model_view(side: str) -> str:
        if side == "back":
            return (
                "She is turned with her back fully to the camera — her face "
                "is not visible. Her hair is pulled up so the upper back of "
                "the shirt is completely unobstructed."
            )
        return (
            "She is facing the camera directly, framed from the chin down "
            "to the waist so the chest print is unobstructed (face not "
            "the focus)."
        )

    return {
        f"01_closeup_{placement}": (
            f"Medium close-up lifestyle e-commerce photograph of a young "
            f"woman wearing a plain {tee_color} crew-neck cotton t-shirt, "
            f"viewed from the {placement}. {model_view(placement)}\n\n"
            f"{artwork_spec}\n\n"
            f"Soft natural daylight, clean minimalist light-grey studio "
            f"background, professional fashion e-commerce product "
            f"photography, photorealistic, sharp focus, 4k."
        ),
        f"02_fullbody_{placement}": (
            f"Full-body lifestyle e-commerce photograph of a young woman "
            f"{'walking away from' if placement == 'back' else 'standing facing'} "
            f"the camera, {placement} view. She wears a plain {tee_color} "
            f"crew-neck cotton t-shirt tucked loosely into light blue "
            f"straight-leg jeans and white sneakers. Full body from head "
            f"to feet in frame, the shirt's {placement} print is clearly "
            f"legible.\n\n"
            f"{artwork_spec}\n\n"
            f"Clean minimalist light-grey studio background, soft natural "
            f"daylight, professional fashion e-commerce photography, "
            f"photorealistic, 4k."
        ),
        f"03_product_{placement}": (
            f"Overhead flat-lay product photograph of a plain {tee_color} "
            f"crew-neck cotton t-shirt laid flat on a pure white seamless "
            f"background, photographed straight down from above. The "
            f"shirt is laid with the {placement.upper()} facing up. Short "
            f"sleeves spread out symmetrically. Evenly lit, soft shadows, "
            f"no model, no hanger.\n\n"
            f"{artwork_spec}\n\n"
            f"Professional e-commerce apparel flat-lay photography, "
            f"photorealistic, 4k, sharp focus."
        ),
        f"04_hanger_{placement}": (
            f"Product photograph of a plain {tee_color} crew-neck cotton "
            f"t-shirt on a plain wooden clothes hanger, hung against a "
            f"clean minimalist light-grey wall. The shirt is facing the "
            f"camera straight-on from the {placement} so the {placement} "
            f"print is clearly visible. Natural fabric drape, short "
            f"sleeves, even soft studio lighting.\n\n"
            f"{artwork_spec}\n\n"
            f"Professional e-commerce apparel product photography, "
            f"photorealistic, 4k, sharp focus."
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


async def run_reference(client: AsyncOpenAI, slug: str, cfg: dict) -> list:
    out_dir = OUT_ROOT / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    # Render the transparent PNGs (padded + TJ-tight) before any API calls.
    render_transparent_design(slug, cfg, out_dir / "design_transparent.png")

    scenes = build_scenes(cfg)
    logger.info(f"[{slug}] generating {len(scenes)} scenes from {cfg['ref'].name}")
    results = await asyncio.gather(
        *(
            generate_scene(client, slug, label, prompt, cfg["ref"], out_dir)
            for label, prompt in scenes.items()
        ),
        return_exceptions=True,
    )
    return list(zip(scenes.keys(), results))


async def main():
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY missing")

    args = sys.argv[1:] if len(sys.argv) > 1 else list(REFERENCES.keys())
    bad = [a for a in args if a not in REFERENCES]
    if bad:
        raise SystemExit(f"Unknown refs: {bad}. Valid: {sorted(REFERENCES)}")

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    # Refs run in parallel — 4 refs x 4 scenes = 16 concurrent image-edit
    # calls. OpenAI handles the rate limits; retry logic kept out of scope.
    all_results = await asyncio.gather(
        *(run_reference(client, slug, REFERENCES[slug]) for slug in args)
    )

    print("\n=== RESULTS ===")
    for slug, results in zip(args, all_results):
        print(f"\n[{slug}]")
        for label, r in results:
            if isinstance(r, Exception):
                print(f"  {label}: ERROR - {r}")
            else:
                print(f"  {label}: {r}")


if __name__ == "__main__":
    asyncio.run(main())
