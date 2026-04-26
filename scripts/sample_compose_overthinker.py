"""Generate 6 sample compose-pipeline scenes for professional-overthinker
using the transparent design PNG. Local-only — does NOT touch Shopify.

Saves to: static/proposals/_sample_overthinker/
  01_closeup_back.png
  02_fullbody_back.png
  03_product_back.png
  04_hanger_back.png
  01_closeup_back_male.png
  02_fullbody_back_male.png

Run:
  .venv/Scripts/python -m scripts.sample_compose_overthinker
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def main():
    from app.agents.marketing_pipeline import compose_marketing_scenes

    # The local design PNG (transparent black-text on transparent BG)
    design = ROOT / "static" / "design_professional-overthinker-bold-typography-tee.png"
    if not design.exists():
        raise SystemExit(f"Missing {design}")

    out_dir = ROOT / "static" / "proposals" / "_sample_overthinker"
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Composing 6 scenes for professional-overthinker into {out_dir}")
    scenes = await compose_marketing_scenes(
        design_path=design, out_dir=out_dir, tee_color="White",
    )
    print(f"\n=== {len(scenes)}/6 scenes ===")
    for label, path in scenes.items():
        print(f"  {label}: {path}")


if __name__ == "__main__":
    asyncio.run(main())
