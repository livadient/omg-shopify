# Design Replication Workflow

End-to-end steps for launching a new slogan tee — from a wording/reference to an email with 24 inlined images (12 marketing scenes + 12 TJ Qstomizer mockups) sent to Vangelis + Kyriaki.

Don't drive this step-by-step — run the whole pipeline in one pass and report the outcome. The only inputs that can't be inferred are the **slug**, the **slogan wording**, and the **tee fabric color**; confirm those up front and then execute.

## The 5 Steps

### 1. Confirm Inputs

- **Slug** — kebab-case folder name under `static/proposals/` (e.g. `dont_tempt_me_v3`, `told_her_shes_the_one`, `normal_people_scare_me`). Use underscores in new slugs to match existing convention.
- **Slogan wording** — exact text. For two-line designs, put a literal `\n` at the natural punchline break (e.g. `"DON'T TEMPT ME\nI'LL SAY YES"`).
- **Tee color** — one of the 6 Qstomizer colors (`White`, `Black`, `Navy Blue`, `Red`, `Royal Blue`, `Sport Grey`). Pick for legibility:
  - Light/white text → Black fabric
  - Dark text → White fabric
  - Signature slogan style (e.g. maroon italic on white) → match the style

### 2. Pick the Pipeline

| Pipeline | Script | Use when |
|----------|--------|----------|
| **A — gpt-image-1** (default) | `scripts/tee_scenes_from_refs.py` (new design from reference photo) or `scripts/redesign_omg_tees.py` (existing OMG tee) | Most cases. Natural DTG-print look. |
| **B — compose** | `scripts/dont_tempt_me_compose.py` (white tees) or `scripts/told_her_compose.py` (black tees) | gpt-image-1's size floor is too big; long lines reflow into stacks; exact print size matters. Flatter "sticker" look. |

See `doc/marketing-photo-generation.md` for the full trade-off analysis.

### 3. Render and Generate Scenes

Add the design entry to the script's config dict:
- **`tee_scenes_from_refs.py`** — entry in `REFERENCES` dict with `ref`, `tee_color`, `placement`, `slogan_desc`, and Pillow `design` spec (width/height + list of `(text, font_candidates, size_ratio, hex_color)` lines).
- **`redesign_omg_tees.py`** — entry in `TEES` dict with `title`, `style_desc`, `tee_color`, Pillow `design` spec. Per-scene filter CLI: `python -m scripts.redesign_omg_tees <slug> [scene_label]` re-rolls only the named scene.
- **Compose scripts** — adjust the hard-coded slogan/font/`PRINT_GEOMETRY` (or duplicate the script for a new slug).

Run the script. Output goes to `static/proposals/<slug>/` with:
- `01_closeup_back.png` — medium close-up, model's back
- `02_fullbody_back.png` — full-body, walking away
- `03_product_back.png` — overhead flat-lay, back-up
- `04_hanger_back.png` or `04_product_front.png` — hanger shot or front flat-lay
- `design_transparent.png` — padded transparent PNG (used as the reference handed to `gpt-image-1 images.edit`)
- `design_transparent_tj.png` — tight-cropped transparent PNG (uploaded to Qstomizer for the TJ mockups)

### 4. Wire Into `scripts/mail_tj_mockups.py`

Append a tuple to the `DESIGNS` list:

```python
DESIGNS = [
    # (display_title, slug, [4 scene filenames without .png], qstomizer_color)
    ("My New Tee", "my_new_tee", ["01_closeup_back", "02_fullbody_back", "03_product_back", "04_hanger_back"], "Black"),
    ...
]
```

The **color** here must match the tee color in step 1 — it's what the Qstomizer Playwright automation will click for the 4 TJ mockups. A white-text design on a White Qstomizer tee renders a completely blank mockup (white blends into fabric).

### 5. Run Send

```bash
.venv/Scripts/python -m scripts.mail_tj_mockups send --force
```

This does:
- Runs 4 Qstomizer Playwright sessions per design (male × {front, back}, female × {front, back}) at the default `vertical_offset=-0.25` (upper-back placement, Konva clamp protects tall designs from clipping). ~1 min per session, ~12 min total for 3 designs.
- Builds an HTML email with per-design sections: 4 marketing scenes ("Marketing photos") and 4 Qstomizer mockups ("TJ Qstomizer mockups").
- Inlines all 24 images via Content-ID.
- Sends to `livadient@gmail.com`, `kmarangos@hotmail.com`, `kyriaki_mara@yahoo.com`.

Without `--force`, the script short-circuits when all 4 cached mockup PNGs for a slug already exist on disk (skips Playwright entirely, ~8s total). Use `--force` when color / offset / design has changed.

## Expected Feedback Loops

From Kyriaki's style reviews (see `memory/feedback_slogan_tee_typography.md`):

- **"too big"** → switch that specific scene to Pipeline B (compose) with a tuned `PRINT_GEOMETRY.width_pct` around 0.38-0.48 (sweet spot; `<0.30` too small, `>0.50` too big). Or pass an already-approved scene as the `images.edit` reference when regenerating the oversized scene — anchors the proportions better than any prompt tweak.
- **"too high / too low"** on the TJ mockup → tune `vertical_offset` in the `customize_and_add_to_cart` call. `-0.25` is the default; more negative = higher. The clamp in `qstomizer_automation.py` prevents tall 4-line designs from clipping the collar.
- **"missing word"** (gpt-image-1 cropped a word) → shrink the reference PNG's `text_width_ratio` to ~0.55 and add a "do NOT crop word X" line to `ARTWORK_SPEC`. Re-rolling the single scene usually lands a clean one.
- **"modest print"** is always the target — Kyriaki's "too small" threshold sits around `width_pct ≈ 0.30`; "too big" around `0.50`. Aim 0.38-0.48.
- **Slogan wording must match verbatim.** Don't substitute a different variant of the phrase in the render.

## Related: Bulk Product-Image Refresh

`scripts/refresh_all_product_images.py` runs the same pipeline across the entire active OMG catalog:

- **Phase 1:** uploads pre-generated scenes for the 3 priority slugs (Don't Tempt Me, Told Her, Normal People) to their existing Shopify products, gender-linked.
- **Phase 3:** for every other active 3-option t-shirt, fetches the Design Artwork PNG from Shopify, generates 6 scenes via gpt-image-1, regenerates 4 TJ mockups at `vertical_offset=-0.25`, deletes old product images, uploads new ones with gender-linked variant_ids.
- Skips legacy 2-option Astous limited-edition products (different variant schema).
- Emails a summary report on completion.

Run modes: `--phase1-only`, `--phase3-only`, or no flag for both. ~80 min end-to-end for a full catalog refresh.

## What Not to Do

- Don't invent slogan wording — use what Vangelis or Mango specified.
- Don't add decorations / extra text beyond the slogan unless asked.
- Don't skip `--force` when the placement, color, or design has changed — cached mockups will be stale.
- Don't ask for confirmation between each step; run steps 2-5 end-to-end and report the outcome with the email timestamp.
