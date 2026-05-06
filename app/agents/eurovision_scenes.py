"""Generate 8 photoreal model scenes for an approved tee, using gpt-image-2
image-edit with a design + anchor reference.

Originally built for the Astous Eurovision launch (2026-05-05/06) and
generalised on 2026-05-06 to run for every Mango approval.

The 8 scenes:
  01_female_front_close   01_female_back_close
  02_female_front_full    02_female_back_full
  03_male_front_close     03_male_back_close
  04_male_front_full      04_male_back_full

Each call passes the design PNG as the primary reference (so the
artwork copies pixel-faithfully) AND an anchor photo (the design's
own hanger render — `04_hanger_back.png`) as the secondary
reference, so gpt-image-2 inherits the proportional-print SIZE from
the anchor instead of stretching the artwork edge-to-edge across the
torso.

ARTWORK_SPEC explicitly demands ~40-50% tee-width print with generous
fabric margin all around — Vangelis flagged on 2026-05-06 that
billboard-sized prints made the tees look like a different product.

Usage:
  from app.agents.eurovision_scenes import generate_proportional_scenes
  scenes = await generate_proportional_scenes(
      design_path=Path("static/design_my-slug.png"),
      anchor_path=Path("static/proposals/my_slug/04_hanger_back.png"),
      out_dir=Path("static/proposals/my_slug_proportional"),
  )

Environment:
  OPENAI_API_KEY            (required)
  OPENAI_IMAGE_MODEL        (optional; defaults to "gpt-image-2")
  IMAGE_EDIT_BACKEND=openai (the default; krea is also wired)
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from app.agents.image_edit_backend import edit_image

logger = logging.getLogger(__name__)


# Photoreal preamble: pushes gpt-image-2 toward documentary photography
# style instead of glossy AI-renderer aesthetic. Generic Mediterranean /
# coastal backdrop — works for Cyprus-themed and global designs alike.
PHOTOREAL = (
    "Documentary-style photograph, shot on a Fujifilm X-T4 with a 35mm prime, "
    "natural Mediterranean afternoon light, soft golden hour. "
    "Realistic skin texture, individual visible pores, fine hair strands, "
    "subtle catch-light in the eyes, natural shadows, slight motion blur, "
    "imperfect framing — feels caught off-guard, NOT posed. "
    "Mediterranean / coastal backdrop: terracotta-rendered walls, olive trees, "
    "weathered wood doors, sun-bleached stucco, or pebbled beach with turquoise "
    "sea — pick whichever fits the framing. "
    "Avoid: airbrushed skin, plastic perfection, dramatic studio lighting, "
    "saturated colour grade, uncanny faces, perfect symmetry. "
    "If the model has any visible feature that screams 'AI face', adjust toward "
    "asymmetric, ordinary, real-person features."
)

# Proportional-print spec — the print is the artwork on the tee, NOT a
# billboard covering it. Hanger image is the size benchmark.
ARTWORK_SPEC = (
    "The first reference image is the EXACT artwork that must appear on the "
    "tee. Reproduce the artwork pixel-faithfully — every letter, every shape, "
    "every colour — DO NOT redraw, restyle, or translate. The print must look "
    "like high-quality DTG fabric output: subtle weave texture, no plastic-decal "
    "sheen.\n\n"
    "CRITICAL — PRINT SIZE: the artwork occupies roughly 40-50% of the tee's "
    "visible width and 30-40% of its visible height. There is GENEROUS WHITE "
    "FABRIC margin all around the print: clear margin between the print and the "
    "neckline (front) or collar seam (back), clear margin between the print and "
    "each side seam, clear margin between the print and the hem. The tee is the "
    "dominant visual element; the print is a centred graphic on it, NOT a "
    "billboard covering the whole front/back. Match the proportional size shown "
    "in the second reference image (the hanger photo) — print is centred and "
    "modest, not stretched edge-to-edge."
)


# 8 scene definitions: (label, gender, framing, placement, scene_prompt).
# Generic Mediterranean lifestyle photography — works for any tee approved
# by Mango. The artwork itself drives design distinctness; backdrops stay
# consistent so all of OMG's product photos read as one brand.
SCENES: list[tuple[str, str, str, str, str]] = [
    (
        "01_female_front_close", "female", "close", "front",
        "Medium close-up of a Mediterranean woman in her late 20s, wearing the tee, "
        "leaning casually against a sun-warmed terracotta wall in an old-town "
        "alley. Wavy dark hair tucked behind one ear, slight side-glance, "
        "natural smile lines. She's holding an iced coffee. "
        "The artwork is printed on the centre of her chest at a moderate size — "
        "about 40-45% of the tee's width — with clear white fabric margin "
        "between the print and her collarbone, and between the print and the hem. "
        "Frame: chest-up, 3:4 aspect."
    ),
    (
        "02_female_front_full", "female", "full", "front",
        "Full-body shot of a young Mediterranean woman walking through a "
        "sun-dappled village street toward the camera. Tee tucked into "
        "high-waist blue denim jeans, white sneakers. Late afternoon light, "
        "long warm shadows. The artwork is printed on the centre of her chest "
        "at a moderate size — about 40-45% of the tee's width, generous fabric "
        "margin all around. The artwork is clearly readable but does NOT "
        "dominate the torso; the tee is the main visual element. Slight motion "
        "in her stride."
    ),
    (
        "03_male_front_close", "male", "close", "front",
        "Medium close-up of a 30-something Mediterranean man with a short "
        "beard, weathered tan, wearing the tee. Standing in front of a wooden "
        "fishing-village door, blue paint flaking off. Slight squint into the "
        "afternoon sun, looking just past the camera. "
        "The artwork is printed on his chest at a moderate size — about 40-45% "
        "of the tee's width — clearly readable, with generous fabric margin "
        "around the print on all sides. "
        "Frame: chest-up, 3:4 aspect. Real-camera depth of field, gentle "
        "background blur."
    ),
    (
        "04_male_front_full", "male", "full", "front",
        "Full-body shot of a Mediterranean man in his early 30s walking along "
        "a pebbled-beach promenade toward the camera. Tee, beige linen shorts, "
        "leather sandals. Olive trees and turquoise sea behind. Hands relaxed, "
        "slight shoulder roll mid-step. The artwork is printed on his chest at "
        "a moderate size — about 40-45% of the tee's width — visible but "
        "proportional, with clear fabric all around. The tee is the dominant "
        "element. Diffuse late-afternoon light, no harsh shadows."
    ),
    (
        "01_female_back_close", "female", "close", "back",
        "Medium close-up from BEHIND of a Mediterranean woman in her late 20s, "
        "wearing the tee, walking away through a narrow stone alley. Dark hair "
        "gathered into a loose low bun, exposing her upper back. "
        "The artwork is printed on the upper-back area at a moderate size — "
        "about 40-45% of the tee's width — centred between the shoulder blades, "
        "with clear fabric margin between the print and the collar and between "
        "the print and the side seams. "
        "Frame: shoulders to mid-torso. Soft side-light from a stone wall. "
        "Real fabric drape, subtle wrinkles."
    ),
    (
        "02_female_back_full", "female", "full", "back",
        "Full-body shot from BEHIND of a young Mediterranean woman walking "
        "away down a seaside boardwalk toward distant blue water. Tee loose "
        "over high-waist denim shorts, white sneakers. The artwork is printed "
        "on the upper-back area at a moderate size — about 40-45% of the tee's "
        "width — centred between the shoulder blades, with generous fabric "
        "margin around it. The tee is the dominant visual; the print sits "
        "cleanly on it. Wind catches her hair gently. Late afternoon golden hour."
    ),
    (
        "03_male_back_close", "male", "close", "back",
        "Medium close-up from BEHIND of a 30-something Mediterranean man, "
        "broad-shouldered, short dark hair, slight tan line at the neck. Tee. "
        "He's looking off toward an out-of-frame harbour. Sun-bleached stone "
        "wall behind, blue shutters in the background blur. "
        "The artwork is printed on the upper-back area at moderate size — "
        "about 40-45% of the tee's width — centred between the shoulder blades, "
        "with clear fabric margin around all sides. "
        "Frame: shoulders down to mid-torso, 3:4 aspect."
    ),
    (
        "04_male_back_full", "male", "full", "back",
        "Full-body shot from BEHIND of a Mediterranean man in his early 30s "
        "walking along a coastal path away from the camera. Tee, beige linen "
        "shorts, leather sandals. Olive trees, dry-stone walls, distant "
        "turquoise sea. Slight stride mid-step. The artwork is printed on the "
        "upper-back area at a moderate size — about 40-45% of the tee's width — "
        "centred between the shoulder blades. Generous fabric margin all around "
        "the print. The tee is the dominant visual element. Soft Mediterranean "
        "light, no harsh contrast."
    ),
]


# gpt-image-2 is heavy (~2-3 min/call). 2 concurrent is the safe ceiling
# before OpenAI's per-org rate limit kicks in.
DEFAULT_CONCURRENCY = 2


async def _one_scene(
    label: str, prompt: str, design_path: Path, anchor_path: Path | None,
    out_dir: Path, sem: asyncio.Semaphore,
) -> Path | Exception:
    out = out_dir / f"{label}.png"
    if out.exists():
        logger.info(f"[eurovision/{label}] already exists at {out} — skipping")
        return out
    backend = os.getenv("IMAGE_EDIT_BACKEND", "openai").lower()
    full_prompt = f"{prompt}\n\n{ARTWORK_SPEC}\n\n{PHOTOREAL}"
    async with sem:
        logger.info(
            f"[eurovision/{label}] generating via {backend} "
            f"primary={design_path.name} "
            f"anchor={anchor_path.name if anchor_path else 'none'}"
        )
        try:
            png_bytes = await edit_image(
                reference_path=design_path, prompt=full_prompt,
                size="1024x1024", quality="high",
                style_image_path=anchor_path if (anchor_path and anchor_path.exists()) else None,
            )
        except Exception as e:
            logger.exception(f"[eurovision/{label}] failed")
            return e
    out.write_bytes(png_bytes)
    logger.info(f"[eurovision/{label}] saved -> {out}")
    return out


async def generate_proportional_scenes(
    design_path: Path,
    anchor_path: Path | None,
    out_dir: Path,
    slug: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict[str, Path]:
    """Generate the 8 proportional-print model scenes for one design.

    Returns dict mapping scene label -> output Path. Scenes that
    already exist on disk are skipped (idempotent — safe to re-run
    after partial failures).

    `anchor_path` should be the design's own hanger photo
    (`04_hanger_back.png` from the marketing-pipeline output) — it
    establishes the proportional print size for the model scenes.
    Pass None to skip the anchor; gpt-image-2 will then try to size
    from the prompt alone, which historically renders the print
    too large.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)

    if not design_path.exists():
        raise FileNotFoundError(f"design PNG missing: {design_path}")
    if anchor_path is None or not anchor_path.exists():
        logger.warning(
            f"eurovision: anchor missing ({anchor_path}); print size will "
            f"depend on prompt alone — likely too large."
        )

    tasks = [
        _one_scene(label, prompt, design_path, anchor_path, out_dir, sem)
        for label, _, _, _, prompt in SCENES
    ]
    results = await asyncio.gather(*tasks)

    out: dict[str, Path] = {}
    errors = 0
    for (label, *_), r in zip(SCENES, results):
        if isinstance(r, Path):
            out[label] = r
        else:
            errors += 1
    if errors:
        logger.error(
            f"eurovision[{slug or design_path.stem}]: {len(out)}/{len(SCENES)} OK, "
            f"{errors} errors"
        )
    else:
        logger.info(
            f"eurovision[{slug or design_path.stem}]: {len(out)}/{len(SCENES)} OK"
        )
    return out
