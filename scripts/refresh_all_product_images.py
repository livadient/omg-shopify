"""End-to-end refresh of product images on OMG Shopify to use our new
marketing + TJ mockup pipeline:

- 6 marketing scenes per product (4 female + 2 male model scenes) generated
  via gpt-image-1 from the product's Design Artwork reference PNG.
- 4 TJ Qstomizer mockups (male/female x front/back) at vertical_offset=-0.25
  (Konva reposition, auto-clamped for tall designs).
- Old images deleted, new ones uploaded with variant_ids linked by gender.

Phase 1: upload the already-generated scenes for 3 priority slugs
  (dont_tempt_me_v3 / told_her_shes_the_one / normal_people_scare_me).

Phase 3: for every other active 3-option t-shirt on OMG, fetch the Design
  Artwork image from Shopify, run the full scene pipeline, re-upload.

Sends one summary email to Vangelis + Kyriaki on completion.

Run:
  .venv/Scripts/python -m scripts.refresh_all_product_images
  .venv/Scripts/python -m scripts.refresh_all_product_images --phase1-only
  .venv/Scripts/python -m scripts.refresh_all_product_images --phase3-only
"""
from __future__ import annotations

import asyncio
import base64
import logging
import sys
import traceback
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROPOSALS = ROOT / "static" / "proposals"
STATIC = ROOT / "static"

# ------------------------------------------------------------------
# Phase 1 — priority slugs with pre-generated scenes.
# Scenes already live in static/proposals/<slug>/.
# ------------------------------------------------------------------
PHASE1_PRODUCTS = [
    # (display title, slug, female_scene_names, male_scene_names, unisex_scene_names, tee_color)
    ("Don't Tempt Me I'll Say Yes",
     "dont_tempt_me_v3",
     ["01_closeup_back", "02_fullbody_back"],
     ["01_closeup_back_male", "02_fullbody_back_male"],
     ["03_product_back", "04_product_front"],
     "White"),
    ("Told Her She's The One",
     "told_her_shes_the_one",
     ["01_closeup_back", "02_fullbody_back"],
     ["01_closeup_back_male", "02_fullbody_back_male"],
     ["03_product_back", "04_hanger_back"],
     "Black"),
    ("Normal People Scare Me",
     "normal_people_scare_me",
     ["01_closeup_back", "02_fullbody_back"],
     [],  # no male scenes yet for normal_people (Kyriaki didn't request)
     ["03_product_back", "04_hanger_back"],
     "Black"),
]

# Map slug -> OMG Shopify handle (learned by discovery, saves a round-trip).
SLUG_TO_HANDLE = {
    "dont_tempt_me_v3": "dont-tempt-me-ill-say-yes-tee",
    "told_her_shes_the_one": "told-her-shes-the-one-tee",
    "normal_people_scare_me": "normal-people-scare-me-tee",
}

# Phase 1 / Phase 3 products we don't touch (legacy 2-option schema).
SKIP_HANDLES = {
    "astous-na-laloun-cyprus-male-tee",
    "astous-na-laloun-cyprus-female-limited-tee",
    "astous-na-laloun-cyprus-male-limited-tee",
}


# ------------------------------------------------------------------
# Shopify helpers
# ------------------------------------------------------------------
ADMIN_BASE = f"https://{settings.omg_shopify_domain}/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": settings.omg_shopify_admin_token}


async def fetch_all_active_tshirts() -> list[dict]:
    async with httpx.AsyncClient() as c:
        resp = await c.get(f"{ADMIN_BASE}/products.json?limit=250", headers=HEADERS, timeout=60)
        resp.raise_for_status()
        products = resp.json().get("products", [])
    return [
        p for p in products
        if p.get("status") == "active"
        and p.get("product_type", "").lower() in ("t-shirt", "tshirt", "t shirt")
        and p.get("handle") not in SKIP_HANDLES
    ]


async def fetch_product(product_id: int) -> dict:
    async with httpx.AsyncClient() as c:
        resp = await c.get(f"{ADMIN_BASE}/products/{product_id}.json", headers=HEADERS, timeout=60)
        resp.raise_for_status()
        return resp.json().get("product", {})


async def delete_product_images(product_id: int, keep_alt: set[str] | None = None) -> int:
    """Delete all images on a product except ones whose alt matches keep_alt.

    Keeping 'Design Artwork' by default so we can always re-reference it.
    """
    keep_alt = keep_alt or {"Design Artwork"}
    async with httpx.AsyncClient() as c:
        resp = await c.get(f"{ADMIN_BASE}/products/{product_id}.json", headers=HEADERS, timeout=60)
        resp.raise_for_status()
        images = resp.json().get("product", {}).get("images", [])
        deleted = 0
        for img in images:
            if (img.get("alt") or "") in keep_alt:
                continue
            try:
                d = await c.delete(
                    f"{ADMIN_BASE}/products/{product_id}/images/{img['id']}.json",
                    headers=HEADERS, timeout=30,
                )
                d.raise_for_status()
                deleted += 1
            except Exception as e:
                logger.warning(f"Failed to delete image {img['id']}: {e}")
        return deleted


async def upload_image(product_id: int, path: Path, alt: str, variant_ids: list[int] | None) -> bool:
    """POST the image, then PUT variant_ids separately.

    Shopify's POST /images endpoint intermittently drops variant_ids (we saw
    5/8 images lose their linking in a single refresh run). Doing a follow-up
    PUT reliably sets them. No-op if the POST already persisted the link.
    """
    img_b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    payload = {"image": {"attachment": img_b64, "filename": path.name, "alt": alt}}
    if variant_ids:
        payload["image"]["variant_ids"] = variant_ids
    async with httpx.AsyncClient() as c:
        resp = await c.post(
            f"{ADMIN_BASE}/products/{product_id}/images.json",
            headers=HEADERS, json=payload, timeout=120,
        )
        if resp.status_code >= 400:
            logger.warning(f"  upload {path.name} failed: {resp.status_code} {resp.text[:200]}")
            return False
        img = resp.json().get("image", {})
        # Shopify sometimes drops variant_ids on POST — verify and PUT-repair.
        if variant_ids and set(img.get("variant_ids") or []) != set(variant_ids):
            try:
                put_resp = await c.put(
                    f"{ADMIN_BASE}/products/{product_id}/images/{img['id']}.json",
                    headers=HEADERS,
                    json={"image": {"id": img["id"], "variant_ids": variant_ids}},
                    timeout=30,
                )
                if put_resp.status_code >= 400:
                    logger.warning(f"  variant_ids PUT {path.name} failed: {put_resp.status_code}")
            except Exception as e:
                logger.warning(f"  variant_ids PUT {path.name} error: {e}")
    return True


def group_variants(product: dict) -> dict[tuple[str, str], list[int]]:
    """Return variant IDs grouped by (gender, placement). Both lowercased."""
    groups: dict[tuple[str, str], list[int]] = {
        ("male", "front"): [], ("male", "back"): [],
        ("female", "front"): [], ("female", "back"): [],
    }
    for v in product.get("variants", []):
        g = (v.get("option1") or "").lower()
        p = (v.get("option2") or "").lower()
        gkey = "female" if "female" in g else ("male" if "male" in g else None)
        pkey = "back" if p == "back" else ("front" if p == "front" else None)
        if gkey and pkey:
            groups[(gkey, pkey)].append(v["id"])
    return groups


async def fetch_design_artwork(product_id: int, product: dict, dest: Path) -> Path | None:
    """Download the 'Design Artwork' image from the product (by alt)."""
    images = product.get("images", [])
    design_img = next((i for i in reversed(images) if (i.get("alt") or "") == "Design Artwork"), None)
    if not design_img:
        return None
    src = design_img.get("src")
    async with httpx.AsyncClient() as c:
        resp = await c.get(src, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return dest


# ------------------------------------------------------------------
# Phase 1: upload pre-generated scenes for priority products.
# ------------------------------------------------------------------
async def run_phase1() -> list[dict]:
    from app.agents.design_creator import _precache_mockups
    import shutil

    all_products = await fetch_all_active_tshirts()
    by_handle = {p["handle"]: p for p in all_products}

    results = []
    for title, slug, fem_scenes, male_scenes, unisex_scenes, color in PHASE1_PRODUCTS:
        handle = SLUG_TO_HANDLE.get(slug)
        product = by_handle.get(handle)
        if not product:
            logger.warning(f"[Phase1/{slug}] product not found on Shopify ({handle}), skipping")
            results.append({"slug": slug, "status": "not_found"})
            continue

        product_id = product["id"]
        groups = group_variants(product)
        all_female = groups[("female", "front")] + groups[("female", "back")]
        all_male = groups[("male", "front")] + groups[("male", "back")]
        all_front = groups[("male", "front")] + groups[("female", "front")]
        all_back = groups[("male", "back")] + groups[("female", "back")]

        scene_dir = PROPOSALS / slug
        logger.info(f"[Phase1/{slug}] product {product_id} — scene dir {scene_dir}")

        # TJ mockups may already exist (mail_tj_mockups.py cache)
        stem = f"design_transparent_tj_{slug}"
        mockup_files = {
            ("male", "front"): PROPOSALS / f"mockup_cache_{stem}_male_front.png",
            ("male", "back"): PROPOSALS / f"mockup_cache_{stem}_male_back.png",
            ("female", "front"): PROPOSALS / f"mockup_cache_{stem}_female_front.png",
            ("female", "back"): PROPOSALS / f"mockup_cache_{stem}_female_back.png",
        }
        if not all(fp.exists() for fp in mockup_files.values()):
            logger.info(f"[Phase1/{slug}] TJ mockups missing — running Qstomizer pipeline")
            design_tj = scene_dir / "design_transparent_tj.png"
            unique = PROPOSALS / f"design_transparent_tj_{slug}.png"
            shutil.copy2(design_tj, unique)
            await _precache_mockups(str(unique), title, color=color)

        # Delete old non-design images
        logger.info(f"[Phase1/{slug}] deleting old product images...")
        n_del = await delete_product_images(product_id)
        logger.info(f"[Phase1/{slug}] deleted {n_del} old images")

        uploaded = 0
        # Upload order — see doc/design-replication-workflow.md. TJ male
        # mockup is the card thumbnail (position 1), TJ female mockup is
        # the hover image (position 2). Only the 4 TJ mockups carry
        # variant_ids — Shopify enforces one-variant-per-image, so if
        # e.g. a hanger ALSO held male-back variants the TJ male-back
        # PUT would silently revert. Lifestyle + flat-lay/hanger therefore
        # pass None for variant_ids (still render in the gallery, just
        # don't fight the variant-image-swap mapping).

        # 1) TJ male back — card thumbnail
        fp = mockup_files.get(("male", "back"))
        if fp and fp.exists():
            vids = groups[("male", "back")] or None
            ok = await upload_image(product_id, fp, f"{title} — TJ male back mockup", vids)
            uploaded += int(ok)

        # 2) TJ female back — card hover image
        fp = mockup_files.get(("female", "back"))
        if fp and fp.exists():
            vids = groups[("female", "back")] or None
            ok = await upload_image(product_id, fp, f"{title} — TJ female back mockup", vids)
            uploaded += int(ok)

        # 3) Male lifestyle scenes — UNLINKED
        for scene in male_scenes:
            fp = scene_dir / f"{scene}.png"
            if fp.exists():
                ok = await upload_image(product_id, fp, f"{title} — male {scene.replace('_male','')}", None)
                uploaded += int(ok)

        # 4) Female lifestyle scenes — UNLINKED
        for scene in fem_scenes:
            fp = scene_dir / f"{scene}.png"
            if fp.exists():
                ok = await upload_image(product_id, fp, f"{title} — female {scene}", None)
                uploaded += int(ok)

        # 5) Unisex product shots (flat-lay, hanger) — UNLINKED
        for scene in unisex_scenes:
            fp = scene_dir / f"{scene}.png"
            if not fp.exists():
                continue
            ok = await upload_image(product_id, fp, f"{title} — {scene}", None)
            uploaded += int(ok)

        # 6) Remaining TJ mockups — the only non-back images with variant_ids
        for (g, p) in [("male", "front"), ("female", "front")]:
            fp = mockup_files.get((g, p))
            if not fp or not fp.exists():
                continue
            vids = groups[(g, p)] or None
            ok = await upload_image(product_id, fp, f"{title} — TJ {g} {p} mockup", vids)
            uploaded += int(ok)

        logger.info(f"[Phase1/{slug}] uploaded {uploaded} images")
        results.append({
            "slug": slug, "product_id": product_id, "status": "ok",
            "uploaded": uploaded, "deleted": n_del,
        })
    return results


# ------------------------------------------------------------------
# Phase 3: backfill every other active product.
# ------------------------------------------------------------------
async def _extract_design_text(design_path: Path) -> str | None:
    """Ask Claude to read the text on the design PNG, or return None for
    pure illustrations. The returned text is the GROUND TRUTH we bake into
    the scene prompt so gpt-image-1 can't invent different wording.
    """
    import base64 as _b64
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    img_b64 = _b64.b64encode(design_path.read_bytes()).decode("utf-8")
    try:
        resp = await client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                    {"type": "text", "text": (
                        "Read this t-shirt design PNG and return ONLY the visible text verbatim "
                        "(preserving line breaks with \\n, capitalization, punctuation, and spacing). "
                        "If the design is a pure illustration with NO text at all, respond with exactly "
                        "the word: NONE.\n\n"
                        "Return ONLY the text itself or the word NONE — no explanation, no quotes, "
                        "no commentary."
                    )},
                ],
            }],
        )
        text = resp.content[0].text.strip()
        if text.upper() == "NONE" or not text:
            return None
        return text
    except Exception as e:
        logger.warning(f"Claude text extraction failed for {design_path.name}: {e}")
        return None


def _build_artwork_spec(fabric: str, exact_text: str | None) -> str:
    """Build the ARTWORK_SPEC clause.

    When the design has known text we lock gpt-image-1 to those exact words.
    When the design is a pure illustration we explicitly forbid any text —
    otherwise the model invents captions like 'DON'T HURRY' for a silent
    walking-penguin illustration.
    """
    base = (
        f"The t-shirt has a printed graphic centered on the upper back. The "
        f"print must be the EXACT artwork shown in the reference image — copy "
        f"it verbatim, pixel-faithful. The print should be SMALL and "
        f"UNDERSTATED — roughly 20-30% of the shirt's upper back width, "
        f"sized like a chest-pocket caption, with generous {fabric} fabric "
        f"on all sides. Do NOT scale it up. This is deliberately modest — "
        f"think minimalist boutique tee, NOT a statement slogan, NOT a "
        f"billboard. Render as a natural DTG fabric print that follows the "
        f"garment's shading and folds. Do NOT add quotation marks, brand "
        f"name, signature, label, or watermark."
    )
    if exact_text:
        # Typography / slogan design — pin the wording.
        oneline = " / ".join(exact_text.split("\n"))
        return (
            base
            + f" CRITICAL: the ONLY text that appears on the shirt is exactly: "
              f"\"{oneline}\" (preserving original line breaks and capitalisation). "
              f"Do NOT change, substitute, paraphrase, shorten, lengthen, or invent "
              f"any other wording. Render these EXACT words and NO OTHER text. "
              f"Every word must be fully visible with margin — do NOT crop or "
              f"truncate."
        )
    # Pure illustration — lock out any text.
    return (
        base
        + " CRITICAL: this design is a PURE ILLUSTRATION with NO TEXT. Do NOT "
          "add ANY text, letters, numbers, words, slogans, captions, or "
          "writing anywhere on the shirt or in the image. The ONLY thing on "
          "the shirt is the graphic illustration from the reference — no "
          "text of any kind, invented or otherwise."
    )


def _phase3_scene_prompts(tee_color: str, exact_text: str | None) -> dict[str, str]:
    flat_bg = "pure white seamless" if tee_color.lower() != "white" else "light grey seamless"
    fabric = tee_color.lower()
    artwork = _build_artwork_spec(fabric, exact_text)
    return {
        "01_closeup_back": (
            f"Medium close-up lifestyle e-commerce photograph taken from directly "
            f"behind a young woman. She is turned with her back fully to the camera "
            f"— her face is not visible. Her hair is pulled up into a high bun so "
            f"the upper back of her t-shirt is completely unobstructed. The frame "
            f"shows her from just above the bun down to her hips. She wears a plain "
            f"{fabric} crew-neck cotton t-shirt.\n\n{artwork}\n\n"
            f"Soft natural daylight, clean minimalist light-grey studio background, "
            f"professional fashion e-commerce product photography, photorealistic, "
            f"sharp focus, 4k."
        ),
        "02_fullbody_back": (
            f"Full-body lifestyle e-commerce photograph of a young woman walking "
            f"away from the camera, back view. She is fully turned away, her face "
            f"is not visible. She wears a plain {fabric} crew-neck cotton t-shirt "
            f"tucked loosely into light blue straight-leg jeans and white sneakers. "
            f"Full body from head to feet in frame, the shirt's back print is "
            f"clearly legible.\n\n{artwork}\n\n"
            f"Clean minimalist light-grey studio background, soft natural daylight, "
            f"professional fashion e-commerce photography, photorealistic, 4k."
        ),
        "03_product_back": (
            f"Overhead flat-lay product photograph of a plain {fabric} crew-neck "
            f"cotton t-shirt laid flat on a {flat_bg} background, photographed "
            f"straight down from above. The shirt is laid with the BACK facing up. "
            f"Short sleeves spread out symmetrically. Evenly lit, soft shadows, no "
            f"model, no hanger.\n\n{artwork}\n\n"
            f"Professional e-commerce apparel flat-lay photography, "
            f"photorealistic, 4k, sharp focus."
        ),
        "04_hanger_back": (
            f"Product photograph of a plain {fabric} crew-neck cotton t-shirt on "
            f"a plain wooden clothes hanger, hung against a clean minimalist "
            f"light-grey wall. The shirt is facing the camera straight-on from "
            f"the back so the back print is clearly visible. Natural fabric drape, "
            f"short sleeves, even soft studio lighting.\n\n{artwork}\n\n"
            f"Professional e-commerce apparel product photography, photorealistic, "
            f"4k, sharp focus."
        ),
        "01_closeup_back_male": (
            f"Medium close-up lifestyle e-commerce photograph taken from directly "
            f"behind a fit, semi-muscular young man. He is turned with his back "
            f"fully to the camera — his face is not visible. His short dark hair "
            f"is neatly cut so the upper back of his t-shirt is completely "
            f"unobstructed. The frame shows him from just above the neck down to "
            f"his hips. He wears a plain {fabric} crew-neck cotton t-shirt that "
            f"fits well across defined shoulders and a toned back (athletic, "
            f"gym-regular, not bodybuilder).\n\n{artwork}\n\n"
            f"Soft natural daylight, clean minimalist light-grey studio background, "
            f"professional fashion e-commerce product photography, photorealistic, "
            f"sharp focus, 4k."
        ),
        "02_fullbody_back_male": (
            f"Full-body lifestyle e-commerce photograph of a fit, semi-muscular "
            f"young man walking away from the camera, back view. He is fully "
            f"turned away, his face is not visible. He wears a plain {fabric} "
            f"crew-neck cotton t-shirt that fits well across defined shoulders "
            f"and a toned back (athletic, gym-regular, not bodybuilder), tucked "
            f"loosely into dark straight-leg jeans and white sneakers. Full body "
            f"from head to feet in frame, the shirt's back print is clearly "
            f"legible.\n\n{artwork}\n\n"
            f"Clean minimalist light-grey studio background, soft natural "
            f"daylight, professional fashion e-commerce photography, "
            f"photorealistic, 4k."
        ),
    }


def _infer_tee_color(product: dict) -> str:
    """Best-effort fabric color from tags / title / existing mapping."""
    # Mapping file wins if present
    try:
        import json
        mapping_file = ROOT / "product_mappings.json"
        if mapping_file.exists():
            data = json.loads(mapping_file.read_text())
            for m in data.get("mappings", []):
                if m.get("source_handle") == product.get("handle"):
                    c = m.get("color")
                    if c:
                        return c
    except Exception:
        pass
    # Tag / title heuristics
    tags = (product.get("tags") or "").lower()
    title = (product.get("title") or "").lower()
    if "black" in tags or "black" in title:
        return "Black"
    return "White"


async def run_phase3() -> list[dict]:
    from openai import AsyncOpenAI
    from app.agents.design_creator import _precache_mockups
    import shutil

    if not settings.openai_api_key:
        logger.error("OPENAI_API_KEY missing — skipping Phase 3")
        return []

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    all_products = await fetch_all_active_tshirts()
    # Skip the 3 priority (Phase 1) products
    phase1_handles = {SLUG_TO_HANDLE[s] for _, s, *_ in PHASE1_PRODUCTS}
    targets = [p for p in all_products if p.get("handle") not in phase1_handles]
    logger.info(f"[Phase3] {len(targets)} products to backfill")

    results = []
    for product in targets:
        handle = product.get("handle")
        product_id = product["id"]
        title = product.get("title", handle)
        # Re-fetch fully to have images list
        full = await fetch_product(product_id)

        slug = handle.replace("-", "_")
        scene_dir = PROPOSALS / slug
        scene_dir.mkdir(parents=True, exist_ok=True)

        # 1) Ensure design artwork PNG available
        design_path = STATIC / f"design_{handle}.png"
        if not design_path.exists():
            logger.info(f"[Phase3/{handle}] fetching Design Artwork from Shopify")
            fetched = await fetch_design_artwork(product_id, full, design_path)
            if not fetched:
                logger.warning(f"[Phase3/{handle}] no Design Artwork image — skipping")
                results.append({"slug": handle, "status": "no_design_artwork"})
                continue

        tee_color = _infer_tee_color(full)

        # 2) Ensure a transparent_tj PNG (Qstomizer upload artifact).
        tj_ref = scene_dir / "design_transparent_tj.png"
        if not tj_ref.exists():
            try:
                from PIL import Image
                img = Image.open(design_path).convert("RGBA")
                bbox = img.getbbox()
                (img.crop(bbox) if bbox else img).save(tj_ref, "PNG")
            except Exception as e:
                logger.warning(f"[Phase3/{handle}] could not build design_transparent_tj: {e}")

        # 3) TJ mockups FIRST — so the on-model scenes can use the
        # actually-rendered tee+print as their source (Reading B). Cached
        # if available; otherwise run Qstomizer Playwright now.
        unique = PROPOSALS / f"design_transparent_tj_{slug}.png"
        stem = unique.stem
        expected_mockups = {
            (g, p): PROPOSALS / f"mockup_cache_{stem}_{g}_{p}.png"
            for g in ("male", "female") for p in ("front", "back")
        }
        all_mockups_cached = all(fp.exists() for fp in expected_mockups.values())
        if tj_ref.exists() and not all_mockups_cached:
            shutil.copy2(tj_ref, unique)
            logger.info(f"[Phase3/{handle}] running Qstomizer for 4 mockups ({tee_color})")
            try:
                await _precache_mockups(str(unique), title, color=tee_color)
            except Exception as e:
                logger.warning(f"[Phase3/{handle}] Qstomizer pipeline failed: {e}")
        elif all_mockups_cached:
            logger.info(f"[Phase3/{handle}] using cached TJ mockups (skip Playwright)")

        # 4) Scenes — paste the actually-rendered TJ mockup tee onto each
        # blank-model scene so the on-model image is pixel-identical to
        # the print preview the customer sees.
        scene_labels = ["01_closeup_back", "02_fullbody_back", "03_product_back",
                        "04_hanger_back", "01_closeup_back_male", "02_fullbody_back_male"]
        scene_files = {lbl: scene_dir / f"{lbl}.png" for lbl in scene_labels}
        all_scenes_cached = all(p.exists() for p in scene_files.values())
        force_scenes = "--force-scenes" in sys.argv

        if all_scenes_cached and not force_scenes:
            logger.info(f"[Phase3/{handle}] using cached scenes (skip compose)")
            generated: dict[str, Path] = {lbl: p for lbl, p in scene_files.items() if p.exists()}
        else:
            from app.agents.marketing_pipeline import compose_marketing_scenes
            logger.info(f"[Phase3/{handle}] composing 6 scenes (paste transparent design, color={tee_color})")
            generated = await compose_marketing_scenes(
                design_path=tj_ref if tj_ref.exists() else design_path,
                out_dir=scene_dir,
                tee_color=tee_color,
            )
            logger.info(f"[Phase3/{handle}] {len(generated)}/6 scenes ok")

        # 4) Delete old images + upload new ones
        groups = group_variants(full)
        all_female = groups[("female", "front")] + groups[("female", "back")]
        all_male = groups[("male", "front")] + groups[("male", "back")]
        all_front = groups[("male", "front")] + groups[("female", "front")]
        all_back = groups[("male", "back")] + groups[("female", "back")]

        n_del = await delete_product_images(product_id)
        logger.info(f"[Phase3/{handle}] deleted {n_del} old images")

        uploaded = 0
        # Upload order — only TJ mockups carry variant_ids (Shopify enforces
        # one-variant-per-image, so any other image holding those vids would
        # silently block the TJ mockup from getting the variant linkage).
        stem = f"design_transparent_tj_{slug}"
        mockup_paths = {
            (g, p): PROPOSALS / f"mockup_cache_{stem}_{g}_{p}.png"
            for g in ("male", "female") for p in ("front", "back")
        }

        # 1) TJ male back — card thumbnail (linked to male-back variants)
        fp = mockup_paths.get(("male", "back"))
        if fp and fp.exists():
            vids = groups[("male", "back")] or None
            ok = await upload_image(product_id, fp, f"{title} — TJ male back mockup", vids)
            uploaded += int(ok)

        # 2) TJ female back — card hover image (linked to female-back variants)
        fp = mockup_paths.get(("female", "back"))
        if fp and fp.exists():
            vids = groups[("female", "back")] or None
            ok = await upload_image(product_id, fp, f"{title} — TJ female back mockup", vids)
            uploaded += int(ok)

        # 3) Male + 4) Female lifestyle — UNLINKED (gallery-only)
        for label in ("01_closeup_back_male", "02_fullbody_back_male",
                      "01_closeup_back", "02_fullbody_back"):
            path = generated.get(label)
            if path and path.exists():
                ok = await upload_image(product_id, path, f"{title} — {label}", None)
                uploaded += int(ok)

        # 5) Unisex product shots — UNLINKED
        for label in ("03_product_back", "04_hanger_back"):
            path = generated.get(label)
            if path and path.exists():
                ok = await upload_image(product_id, path, f"{title} — {label}", None)
                uploaded += int(ok)

        # 6) Remaining TJ mockups — the only non-back images with variant_ids
        for (g, p) in [("male", "front"), ("female", "front")]:
            fp = mockup_paths.get((g, p))
            if fp and fp.exists():
                vids = groups[(g, p)] or None
                ok = await upload_image(product_id, fp, f"{title} — TJ {g} {p} mockup", vids)
                uploaded += int(ok)

        logger.info(f"[Phase3/{handle}] uploaded {uploaded} images")
        results.append({
            "slug": handle, "product_id": product_id, "status": "ok",
            "scenes_generated": len(generated), "uploaded": uploaded, "deleted": n_del,
        })

    return results


# ------------------------------------------------------------------
# Summary email
# ------------------------------------------------------------------
async def send_summary_email(phase1: list[dict], phase3: list[dict], elapsed_s: float) -> None:
    from app.agents.agent_email import send_agent_email

    def row(r: dict) -> str:
        status_color = "#10b981" if r.get("status") == "ok" else "#dc2626"
        return (
            f"<tr>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;'>{r.get('slug','?')}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;color:{status_color};font-weight:bold;'>{r.get('status','?')}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:right;'>{r.get('scenes_generated','–')}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:right;'>{r.get('uploaded','–')}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:right;'>{r.get('deleted','–')}</td>"
            f"</tr>"
        )

    phase1_rows = "".join(row(r) for r in phase1)
    phase3_rows = "".join(row(r) for r in phase3)

    total_uploaded = sum(r.get("uploaded", 0) for r in phase1 + phase3 if isinstance(r.get("uploaded"), int))
    total_deleted = sum(r.get("deleted", 0) for r in phase1 + phase3 if isinstance(r.get("deleted"), int))

    html = f"""
    <div style="font-family:sans-serif;max-width:800px;margin:0 auto;">
        <div style="background:#7c3aed;color:white;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">Product images refresh — complete</h2>
            <p style="margin:4px 0 0;opacity:0.9;">
                All active t-shirts now use the new marketing pipeline
                (male + female scenes, upper-back TJ placement).
                Elapsed: {elapsed_s/60:.1f} min · Uploaded {total_uploaded} images ·
                Deleted {total_deleted} old images.
            </p>
        </div>
        <div style="padding:20px;border:1px solid #e5e7eb;border-radius:0 0 8px 8px;">
            <h3 style="margin-top:0;">Phase 1 — priority products (pre-generated scenes)</h3>
            <table style="width:100%;border-collapse:collapse;font-size:14px;">
                <thead>
                    <tr style="background:#f9fafb;">
                        <th style="padding:8px 10px;text-align:left;">Slug</th>
                        <th style="padding:8px 10px;text-align:left;">Status</th>
                        <th style="padding:8px 10px;text-align:right;">Scenes</th>
                        <th style="padding:8px 10px;text-align:right;">Uploaded</th>
                        <th style="padding:8px 10px;text-align:right;">Deleted</th>
                    </tr>
                </thead>
                <tbody>{phase1_rows}</tbody>
            </table>
            <h3>Phase 3 — backfilled existing active products</h3>
            <table style="width:100%;border-collapse:collapse;font-size:14px;">
                <thead>
                    <tr style="background:#f9fafb;">
                        <th style="padding:8px 10px;text-align:left;">Handle</th>
                        <th style="padding:8px 10px;text-align:left;">Status</th>
                        <th style="padding:8px 10px;text-align:right;">Scenes</th>
                        <th style="padding:8px 10px;text-align:right;">Uploaded</th>
                        <th style="padding:8px 10px;text-align:right;">Deleted</th>
                    </tr>
                </thead>
                <tbody>{phase3_rows}</tbody>
            </table>
            <p style="color:#6b7280;font-size:13px;margin-top:20px;">
                New Mango design approvals (Phase 2) now also run this full
                pipeline automatically — 6 marketing scenes + 4 TJ mockups,
                gender-linked variant images, upper-back Qstomizer placement.
            </p>
        </div>
    </div>
    """
    await send_agent_email(
        subject=f"Product images refresh — {len(phase1)+len(phase3)} products updated",
        html_body=html,
        extra_recipients=["kmarangos@hotmail.com", "kyriaki_mara@yahoo.com"],
    )


async def main():
    import time
    args = sys.argv[1:]
    run_p1 = "--phase3-only" not in args
    run_p3 = "--phase1-only" not in args

    t0 = time.time()
    phase1_results: list[dict] = []
    phase3_results: list[dict] = []

    if run_p1:
        logger.info("=" * 60)
        logger.info("PHASE 1 — uploading pre-generated scenes for priority products")
        logger.info("=" * 60)
        try:
            phase1_results = await run_phase1()
        except Exception:
            logger.exception("Phase 1 crashed")

    if run_p3:
        logger.info("=" * 60)
        logger.info("PHASE 3 — backfilling existing active products")
        logger.info("=" * 60)
        try:
            phase3_results = await run_phase3()
        except Exception:
            logger.exception("Phase 3 crashed")

    elapsed = time.time() - t0
    logger.info(f"ALL DONE — {elapsed/60:.1f} min")
    if "--no-email" in args:
        logger.info("Skipping summary email (--no-email)")
    else:
        try:
            await send_summary_email(phase1_results, phase3_results, elapsed)
        except Exception:
            logger.exception("Summary email failed")


if __name__ == "__main__":
    asyncio.run(main())
