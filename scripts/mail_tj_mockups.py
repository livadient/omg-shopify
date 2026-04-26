"""One-off: run Qstomizer on the three transparent slogan designs and
email the resulting TJ mockups to Vangelis + Kyriaki.

Runs `_precache_mockups(design_transparent_tj.png, name)` for each design
sequentially (Playwright pool is limited to 2 on Windows), collects the
12 returned mockup paths, and sends a single HTML email with everything
inlined.

Each design's transparent PNG is copied to a slug-prefixed filename
before `_precache_mockups` runs — otherwise all three designs share the
same basename (`design_transparent_tj.png`) and the cached mockup files
collide because `_precache_mockups` keys its output filename off the
input stem.

Run:
  .venv/Scripts/python -m scripts.mail_tj_mockups
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROPOSALS = ROOT / "static" / "proposals"

# Each design tuple: (display title, folder slug, list of marketing-scene
# filenames, Qstomizer tee color).
# Scene lists include male-model variants (01_closeup_back_male,
# 02_fullbody_back_male) per Kyriaki's 2026-04-23 feedback — tops market
# better with both female and semi-muscular male models from behind.
DESIGNS = [
    ("Normal People Scare Me",                  "normal_people_scare_me", ["01_closeup_back", "02_fullbody_back", "03_product_back", "04_hanger_back"], "Black"),
    ("Don't Tempt Me, I'll Say Yes",            "dont_tempt_me_v3",       ["01_closeup_back", "02_fullbody_back", "01_closeup_back_male", "02_fullbody_back_male", "03_product_back", "04_product_front"], "White"),
    ("Told Her She's The One, Not The Only One", "told_her_shes_the_one", ["01_closeup_back", "02_fullbody_back", "01_closeup_back_male", "02_fullbody_back_male", "03_product_back", "04_hanger_back"], "Black"),
]


async def main():
    from app.agents.agent_email import send_agent_email
    from app.agents.design_creator import _precache_mockups

    # Force regenerate when `--force` is passed. Normally we reuse cached
    # mockups to skip 12 min of Playwright. The Qstomizer automation now
    # defaults to vertical_offset=-0.25 so the print lands at the upper
    # back (matching our marketing mockups), so existing cached files from
    # before that change need to be regenerated.
    force = "--force" in sys.argv

    results: list[tuple[str, str, list[str], dict]] = []
    for title, slug, scenes, color in DESIGNS:
        design_path = PROPOSALS / slug / "design_transparent_tj.png"
        if not design_path.exists():
            logger.error(f"[{slug}] missing {design_path}, skipping")
            continue
        # Copy to a slug-prefixed filename so _precache_mockups writes
        # to unique cache filenames per design (otherwise all three
        # designs clobber each other's mockups).
        unique_path = PROPOSALS / f"design_transparent_tj_{slug}.png"
        shutil.copy2(design_path, unique_path)
        # Short-circuit: if all 4 expected cache files already exist, skip
        # the 4 Playwright runs and build the cached dict directly.
        stem = unique_path.stem
        expected = {
            (g, p): PROPOSALS / f"mockup_cache_{stem}_{g}_{p}.png"
            for g in ("male", "female") for p in ("front", "back")
        }
        if not force and all(fp.exists() for fp in expected.values()):
            logger.info(f"[{slug}] using existing cached mockups (skip Playwright)")
            cached = {"male": {}, "female": {}}
            for (g, p), fp in expected.items():
                cached[g][p] = {"url": None, "path": str(fp)}
        else:
            logger.info(f"[{slug}] running Qstomizer on {color} tee for 4 mockups...")
            cached = await _precache_mockups(str(unique_path), title, color=color)
        results.append((title, slug, scenes, cached))
        logger.info(f"[{slug}] done: {cached}")

    # Build email — per design: marketing scenes first, then TJ mockups.
    inline: dict[str, Path] = {}
    sections: list[str] = []
    for title, slug, scene_names, cached in results:
        title_key = title.replace(" ", "_").replace("'", "").replace(",", "")

        # Marketing scene photos
        marketing_imgs = []
        for scene in scene_names:
            scene_path = PROPOSALS / slug / f"{scene}.png"
            if scene_path.exists():
                cid = f"{title_key}_marketing_{scene}"
                inline[cid] = scene_path
                marketing_imgs.append(
                    f'<div style="display:inline-block;margin:6px;">'
                    f'<img src="cid:{cid}" style="width:300px;border:1px solid #e5e7eb;border-radius:6px;">'
                    f'<div style="font-size:12px;color:#6b7280;text-align:center;margin-top:4px;">{scene}</div>'
                    f"</div>"
                )

        # TJ Qstomizer mockups
        tj_imgs = []
        for gender in ("male", "female"):
            for placement in ("front", "back"):
                entry = cached.get(gender, {}).get(placement)
                if entry and Path(entry["path"]).exists():
                    cid = f"{title_key}_tj_{gender}_{placement}"
                    inline[cid] = Path(entry["path"])
                    tj_imgs.append(
                        f'<div style="display:inline-block;margin:6px;">'
                        f'<img src="cid:{cid}" style="width:300px;border:1px solid #e5e7eb;border-radius:6px;">'
                        f'<div style="font-size:12px;color:#6b7280;text-align:center;margin-top:4px;">TJ {gender} / {placement}</div>'
                        f"</div>"
                    )

        sections.append(
            f'<h3 style="margin-top:32px;color:#111;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">{title}</h3>'
            f'<h4 style="margin:16px 0 8px;color:#374151;">Marketing photos</h4>'
            f'<div>{"".join(marketing_imgs)}</div>'
            f'<h4 style="margin:16px 0 8px;color:#374151;">TJ Qstomizer mockups</h4>'
            f'<div>{"".join(tj_imgs)}</div>'
        )

    html = f"""
    <div style="font-family:sans-serif;max-width:900px;margin:0 auto;">
        <div style="background:#0ea5e9;color:white;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">Three slogan designs — marketing photos + TJ mockups</h2>
            <p style="margin:4px 0 0;opacity:0.9;">
                For each design: 4 marketing scene photos (closeup, fullbody,
                product, hanger/product-front) and 4 Qstomizer mockups on
                TShirtJunkies' classic tee (male) and women's tee (female),
                front + back placement.
            </p>
        </div>
        <div style="padding:20px;border:1px solid #e5e7eb;border-radius:0 0 8px 8px;">
            {"".join(sections)}
        </div>
    </div>
    """

    # Email send is gated — run `.venv/Scripts/python -m scripts.mail_tj_mockups send`
    # to actually dispatch. By default we just log the resolved cache paths
    # so you can spot-check them on disk before committing to an email.
    if len(sys.argv) > 1 and sys.argv[1] == "send":
        await send_agent_email(
            subject="Three slogan designs — marketing photos + TJ mockups",
            html_body=html,
            inline_images=inline,
            extra_recipients=["kmarangos@hotmail.com", "kyriaki_mara@yahoo.com"],
        )
        logger.info(f"Email sent with {len(inline)} inlined images (marketing + TJ mockups)")
    else:
        logger.info(f"Dry run — {len(inline)} images resolved. Paths:")
        for cid, path in inline.items():
            logger.info(f"  {cid} -> {path}")
        logger.info("Re-run with 'send' arg to actually email: .venv/Scripts/python -m scripts.mail_tj_mockups send")


if __name__ == "__main__":
    asyncio.run(main())
