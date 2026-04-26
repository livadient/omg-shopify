# Marketing Photo Generation

Standalone scripts that generate e-commerce marketing photos for slogan tees, plus the matching transparent PNG for TShirtJunkies/Qstomizer upload. These are one-shot utilities run from the CLI — not part of the agent schedule or webhook flow.

## Overview

Each script produces, per tee concept:

- **4 scene photos** via OpenAI `gpt-image-1` (GPT-4o-native image model, the one ChatGPT uses since March 2025):
  - `01_closeup_<placement>` — medium close-up lifestyle shot, model's print side
  - `02_fullbody_<placement>` — full-body lifestyle shot, model's print side
  - `03_product_<placement>` — overhead flat-lay, print side up
  - `04_hanger_<placement>` — tee on a wooden hanger, print side facing camera
- **Two transparent PNGs** (pure Pillow, no API call):
  - `design_transparent.png` — padded canvas, used as the reference image fed back into `gpt-image-1`
  - `design_transparent_tj.png` — tight-cropped version for uploading to Qstomizer so it sizes the print correctly

Outputs land in `static/proposals/<slug>/`.

## Scripts

Two pipelines are available:
- **Pipeline A (gpt-image-1)** — default. Natural DTG-style fabric print, but gpt-image-1 has a hard size floor and reflows long lines.
- **Pipeline B (compose)** — fallback. DALL-E blank tees + Claude bbox + Pillow paste. Guaranteed exact size and layout, flatter "sticker" look.

### Pipeline A — `gpt-image-1`

#### `scripts/dont_tempt_me_gptimage.py`

Single-tee script hard-coded to the "DON'T TEMPT ME / I'LL SAY YES" design (the reference style saved in `project_dont_tempt_me_style.md`). Output dir: `static/proposals/dont_tempt_me_v3/`.

```bash
.venv/Scripts/python -m scripts.dont_tempt_me_gptimage                 # all 4 scenes + design
.venv/Scripts/python -m scripts.dont_tempt_me_gptimage 01_closeup_back # re-roll one scene
.venv/Scripts/python -m scripts.dont_tempt_me_gptimage design          # re-render transparents only
```

#### `scripts/tee_scenes_from_refs.py`

Generalised version that takes any reference tee photo as input and produces the same 4-scene quartet plus transparents. Configs live in the `REFERENCES` dict at the top of the file — each entry declares:

- `ref` — path to the reference tee image
- `tee_color` — `"white"` or `"black"` (drives the scene prompts)
- `placement` — `"front"` or `"back"` (drives which side the model faces in the lifestyle shots)
- `slogan_desc` — a textual description of the print for the scene prompt
- `design` — Pillow render spec: list of `(text, font_candidates, size_ratio, hex_color)` lines, plus canvas `width`/`height`

```bash
.venv/Scripts/python -m scripts.tee_scenes_from_refs                        # all refs
.venv/Scripts/python -m scripts.tee_scenes_from_refs normal_people_scare_me # one ref
```

Current references (stored in `C:\Users\vangelisl\Downloads\attachments\`):

| Slug | Reference | Tee | Placement |
|------|-----------|-----|-----------|
| `i_dont_get_drunk` | image1.jpeg | white | front |
| `normal_people_scare_me` | image2.jpeg | black | back |
| `sexi_madafaka` | image3.jpeg | black | front |
| `to_the_person_behind_me` | image4.jpeg | black | back |

#### `scripts/redesign_omg_tees.py`

`gpt-image-1` variant for redesigning existing OMG slogan tees. Each tee in the `TEES` dict declares `title`, `style_desc`, optional `tee_color` (defaults to `"white"`), and a Pillow `design` spec. Supports per-scene re-rolls via a second positional arg:

```bash
.venv/Scripts/python -m scripts.redesign_omg_tees                                        # all tees, all scenes
.venv/Scripts/python -m scripts.redesign_omg_tees told_her_shes_the_one                  # one tee, all 4 scenes
.venv/Scripts/python -m scripts.redesign_omg_tees told_her_shes_the_one 02_fullbody_back # one tee, one scene
```

Scene filter is crucial when you've already approved 3 of the 4 scenes and only want to re-roll the remaining one — a fresh full run would replace all 4 with new gpt-image-1 samples.

### Pipeline B — compose (blank tees + Pillow paste)

#### `scripts/dont_tempt_me_compose.py`

Compose pipeline for WHITE tees with the "DON'T TEMPT ME / I'LL SAY YES" design. DALL-E 3 generates blank white tee scenes; Claude vision returns the white-fabric torso bbox (snapped to the first row of near-white pixels so hair/collar don't inflate it); Pillow renders the italic-sheared maroon slogan and composites it onto an explicit sub-rectangle of the bbox. Output: `static/proposals/dont_tempt_me_v2/`.

#### `scripts/told_her_compose.py`

Compose pipeline adapted for BLACK tees with white serif caps. Same three-stage flow as above with two key differences:

- **Inverted fabric detection.** `detect_shirt_bbox` asks Claude for the BLACK torso bbox, and `snap_to_fabric_top` uses an inverted threshold (`<60` ≈ black-enough-to-be-fabric) instead of the white-tee's `>195`.
- **Upright serif, no italic shear.** `render_slogan` draws both lines in Times Bold (`timesbd.ttf`) without the affine transform the DTM script applies.

Output: `static/proposals/told_her_shes_the_one_compose/`. When the print size is dialled in, copy the scene over the canonical folder:

```bash
cp static/proposals/told_her_shes_the_one_compose/02_fullbody_back.png \
   static/proposals/told_her_shes_the_one/02_fullbody_back.png
```

#### `PRINT_GEOMETRY` schema

Each scene in a compose script has an entry in `PRINT_GEOMETRY` that deterministically carves a sub-rectangle out of the detected torso bbox:

```python
PRINT_GEOMETRY = {
    "01_closeup_back":  {"top_offset_pct": 0.10, "height_pct": 0.14, "width_pct": 0.45},
    "02_fullbody_back": {"top_offset_pct": 0.12, "height_pct": 0.16, "width_pct": 0.55},
    ...
}
```

- `top_offset_pct` — vertical distance from the top of the torso bbox to the top of the print, as a fraction of bbox height.
- `height_pct` — print box height as a fraction of bbox height.
- `width_pct` — print box width as a fraction of bbox width.

Kyriaki's "too small" threshold sits around `width_pct ≈ 0.30`; her "too big" threshold sits around `width_pct ≈ 0.50`. Middle ground is `0.38 - 0.48`.

#### Extended schema in `app/agents/marketing_pipeline.py`

The agent path (`compose_marketing_scenes`, used by Mango approval) extends `PRINT_GEOMETRY` with two extra knobs to handle gpt-image-1 re-roll variance:

```python
PRINT_GEOMETRY = {
    "02_fullbody_back": {
        "top_offset_pct": -0.07,   # negative pulls print above snapped bbox top
        "width_pct": 0.52,          # ignored when image_width_pct is set
        "image_width_pct": 0.16,    # fraction of 1024 — STABLE size across re-rolls
        "x_offset_pct": 0.0,        # fraction of bbox width, positive = right
    },
    ...
}
```

- **Negative `top_offset_pct`** — pulls print *above* the snapped fabric top (compensates when `_snap_to_fabric_top` lands too low because hair/collar pushed y1 down on a particular re-roll).
- **`image_width_pct`** — when set, overrides `width_pct` and gives **stable absolute pixel size across re-rolls** because it's bound to the 1024-px image, not the variable bbox. Use for fullbody scenes where bbox detection variance is largest.
- **`x_offset_pct`** — shifts print horizontally relative to bbox width to compensate when Claude returns an asymmetric bbox (one shoulder/sleeve over-included drags the midpoint off-spine). Brittle — calibrated for one re-roll, may overshoot on the next.

**Tuning workflow** — re-roll only the affected scene to save ~50s of gpt-image-1 calls:

```python
await compose_marketing_scenes(
    design_path=..., out_dir=..., tee_color="White",
    scene_filter={"02_fullbody_back"},
)
```

**Re-roll variance gotcha** — every `gpt-image-1` blank-scene generation produces a different framing, which makes Claude return a different bbox, which makes the same `PRINT_GEOMETRY` values render at different sizes/positions. If a tweak appears to make things worse, suspect the new bbox before re-tuning.

#### Auto-detection: text vs image design

Inside `compose_marketing_scenes.finish` callback:
```python
is_image_design = design_aspect >= 0.4
```
- aspect < 0.4 = text/slogan (wide and short) → use `image_width_pct`
- aspect >= 0.4 = image/illustration (square or tall) → use `image_max_dim_pct` + shifted-up `top_offset_pct - 0.05` + 30/70 spine-weighted blend + -20px left bias

#### Horizontal anchor: blended image-center + spine_x

`_detect_shirt_bbox` returns both bbox AND `spine_x` (Claude-detected back centerline — more reliable than bbox midpoint when one shoulder/sleeve is over-included). Final anchor:
- **TEXT designs**: `0.70 * 512 + 0.30 * spine_x` (prefer image-center, text drift less visually obvious)
- **IMAGE designs**: `0.30 * 512 + 0.70 * spine_x` (trust spine more for visually-heavy illustrations)
- **IMAGE + closeup only**: additional `-20px` bias — gpt-image-1's closeup composition has a slight rightward model bias on closeups specifically. NOT applied to fullbody/product/hanger (the print is small relative to shirt area and 20px would overshoot visibly).

#### Fullbody robustness layered fixes

Fullbody scenes had recurring bugs (overflow shirt, drop to waist, off-center). Three layered guardrails:
1. **`_snap_to_fabric_top` capped at 80px** max push-down — prevents the snap from sliding y1 past long hair to the lower back area.
2. **`_compute_print_rect` caps `pw` at `bbox_w * 0.85`** — `image_width_pct` of the 1024 image easily exceeds the 200px-ish small-fullbody bbox; without the cap the print overflows the visible shirt.
3. **Image-center horizontal anchor for ALL scenes** (modulated by spine blend above) — `spine_x` and bbox midpoint both have variance; image-center (512) is the most consistent across products and re-rolls.

#### Batch regeneration scope ("all live mapped products")

When regenerating marketing photos for the full live catalogue, the in-scope set is the **intersection of**:

1. Active OMG products (`GET /admin/api/2024-01/products.json?status=active&limit=250`)
2. Server-side `~/omg-shopify/product_mappings.json` on the Azure VM (40.81.137.240) — local copy is usually stale, always `scp` first
3. Design PNG availability — local `static/design_<slug>.png` OR server `/home/vangelisl/omg-shopify/static/design_<slug>.png`

**Always exclude the 5 astous tees** — they use real product photography rather than the compose pipeline:
- `astous-na-laloun-cyprus-female-tee` / `-male-tee` / `-female-limited-tee` / `-male-limited-tee` / `-unisex-tee`

For any in-scope slug missing the design PNG locally, `scp` it from the server before running `compose_marketing_scenes`. Output goes to `static/proposals/<slug>/` per design (6 scenes each).

## Pipeline

Both scripts follow the same three-stage pipeline:

1. **Render the transparent design via Pillow.** The slogan is drawn onto an RGBA canvas using font candidates (Impact, Arial Bold, Times Bold, Segoe Script — first-exists wins per line). Canvas is saved twice: padded (for the gpt-image-1 reference) and tight-cropped (for Qstomizer upload).
2. **Generate scenes via `gpt-image-1` image-edit.** `client.images.edit(model="gpt-image-1", image=<reference>, prompt=<scene>)` — the padded transparent PNG is the reference. Prompt describes the scene (closeup/fullbody/flat-lay/hanger) plus an `ARTWORK_SPEC` that asks the model to copy the reference artwork verbatim. Returns `b64_json`, decoded and written to disk.
3. **Runs in parallel.** All 4 scenes per ref are `asyncio.gather`-ed. When multiple refs run together (the default for `tee_scenes_from_refs`), that's 16 concurrent image-edit calls.

Total wall time for 16 scenes: ~45-60 seconds.

## Known Limitations

- **`gpt-image-1` edit mode is not a pixel composite.** Even with the reference image, the model redraws the text onto the garment. On long slogans it sometimes crops, truncates, or drops words (e.g. "DON'T TEMPT" without "ME"). Re-rolling individual scenes usually lands a clean one. For guaranteed-correct text, fall back to the compose pipeline (blank tees + Claude-bbox + Pillow paste), at the cost of a flatter "sticker-like" look.
- **`gpt-image-1` has a hard floor on print size** (confirmed over a full tuning session on 2026-04-22 for the "Told Her She's The One" design). Dropping the reference-PNG `size_ratio` from 0.22 → 0.14 → 0.09 → 0.06 → 0.04 and switching the prompt from "50-60% back width" to "30-40%" to "small caption, 25-30%, generous fabric around all sides" barely changed the rendered print. The model has a fixed "slogan tee print should be this big" prior and ignores explicit size instructions. If a review calls the print "too big" and `gpt-image-1` can't go smaller, switch that scene to the compose pipeline (pipeline B) — that is the only reliable lever.
- **`gpt-image-1` reflows multi-word lines.** Long horizontal lines like `"TOLD HER SHE'S THE ONE."` get broken into 2-3 shorter stacks no matter what the reference PNG shows or the prompt says. Don't waste re-rolls trying to force a specific line count; if 2-line-only is a hard requirement, use the compose pipeline.
- **Passing an approved scene as the `images.edit` reference** (instead of the transparent design PNG) can stabilise print size across scenes. When `01_closeup_back.png` was passed as the reference while regenerating `02_fullbody_back.png`, gpt-image-1 produced a proportionally-matched print more reliably than any prompt tweak. Worth trying before falling back to pipeline B.
- **DALL-E framing varies per call in the compose pipeline.** Each re-roll can give a closer or more distant shot, which shifts the torso bbox and therefore the print absolute-size. Tightening framing in the scene prompt ("subject fills 80% of vertical frame", "tight full-body crop") reduces variance. If a single re-roll produces a dramatically smaller print than expected, check the logged `shirt=(...)` bbox — a small bbox usually means DALL-E pulled the camera too far back; re-roll to get a closer shot rather than tuning `PRINT_GEOMETRY`.
- **OpenAI moderation blocks some slogans.** The `σέξι μαδαφάκα` design returns `safety_violations=[abuse]` because the transliteration hits the profanity filter. Stripping the literal text from the slogan description and relying on the reference image alone reduces the hit rate but doesn't eliminate it.
- **`04` is a hanger shot, not a flat-lay.** Earlier iterations made `04_product_<opposite>` show the plain opposite side of the garment (empty for single-sided prints). That was unusable for listings, so it was replaced with a print-side hanger shot so every scene shows the slogan. Stale `04_product_*` files from older runs may still exist in proposal folders — delete them manually if the directory mixes conventions.

## Fonts

Scripts prefer Windows system fonts, with Linux fallbacks so the same code runs in Docker:

| Style | Windows | Linux |
|-------|---------|-------|
| Sans-serif bold condensed | `impact.ttf` | `LiberationSans-Bold.ttf` |
| Sans-serif bold | `arialbd.ttf` | `LiberationSans-Bold.ttf` |
| Sans-serif regular | `arial.ttf` | `LiberationSans-Regular.ttf` |
| Serif bold | `timesbd.ttf` | `LiberationSerif-Bold.ttf` |
| Script | `segoesc.ttf` → `gabriola.ttf` → `ITCEDSCR.ttf` | (Pillow default) |

## Relation to the Design Creator Agent (Mango)

The agent pipeline in `app/agents/image_client.py` also uses `gpt-image-1` (switched from DALL-E 3 on 2026-04-19 — see `project_dont_tempt_me_style.md`). The agent path is optimised for autonomous daily runs (research trends → generate one design → pre-cache Qstomizer mockups) and writes to proposal storage. These CLI scripts are for hand-curated launches of specific slogan tees and do not interact with the proposal approval workflow.
