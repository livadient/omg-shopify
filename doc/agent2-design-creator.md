# Agent 2: Trend Research & Design Creator (Mango)

## Purpose

Researches trending t-shirt designs, generates original artwork using AI, and on approval creates the product on OMG Shopify store with automatic mapping to TShirtJunkies for fulfillment.

## Schedule

**Daily:** Monday through Friday at 04:00 Cyprus time (Europe/Nicosia)

## Flow

```
1. Scheduler triggers design_creator.research_trends()
2. Web search for real current trends (fashion, pop culture, memes, seasonal)
3. Call Claude API with trend research prompt + search results
4. Claude returns 5 design concepts with types: cyprus, global-trend, slogan, funny, geeky
5. For each concept:
   a. Call DALL-E 3 to generate design image
   b. For designs with text: validate text via Claude vision, retry up to 2x if wrong
   c. Post-process: remove background with rembg for print-ready PNG
      (slogan-type Pillow designs skip rembg since already transparent)
   d. Pre-cache TShirtJunkies mockups via Qstomizer Playwright automation
      (male L + female M) so approval is near-instant
6. Save proposals to data/proposals.json (status: "pending")
7. Email user with design thumbnails + Approve/Reject per design
8. User clicks Approve for chosen designs
9. For each approved design:
   a. "Approve Original" regenerates mockups from original image
   b. "Approve nobg" uses pre-cached mockups (from background-removed version)
   c. Generate product description via LLM
   d. Create product on OMG Shopify (with Gender+Size variants)
   e. Upload design as product image
   f. Upload cached mockup images to product
   g. Save design PNG to static/ for Playwright automation
   h. Auto-create mapping in product_mappings.json
```

## Image Generation

### DALL-E 3 (Primary)

- **API:** OpenAI API (`openai>=1.60.0`)
- **Model:** `dall-e-3`
- **Size:** 1024x1024 (standard) or 1792x1024 (landscape)
- **Cost:** $0.04/image (standard) or $0.08/image (HD)

### Prompt Engineering for T-Shirt Designs

```
Create a bold, original t-shirt design: [concept description].
Style: [vector illustration / minimalist / vintage / street art].
Requirements:
- Solid color background (will be removed for transparent PNG)
- High contrast, clean edges suitable for DTG printing
- No copyrighted characters or logos
- [Any text/slogans centered and clearly readable]
```

### Post-Processing

DALL-E 3 doesn't produce transparent PNGs natively. Background removal is handled by:
- **Primary:** `rembg` library (runs locally, no API cost, uses u2net model)
- **Fallback:** remove.bg API (if rembg quality is insufficient)

The final design must be:
- Transparent PNG background
- High resolution (upscaled if needed)
- Clean edges for DTG (Direct-to-Garment) printing

## Mockup Pre-Caching

After each design image is generated and background-removed, the system runs Qstomizer Playwright automation for both:
- **Male (Classic Tee)** - size L
- **Female (Women's Tee)** - size M

Each pre-cache run uses:
- **`color`** — the `tee_color` the LLM picked on the concept (coerced via `_normalize_tee_color` to one of `White | Black | Navy Blue | Red | Royal Blue | Sport Grey`, default `White` if missing or unrecognised).
- **`vertical_offset=-0.25`** (default across the whole Qstomizer automation) — nudges the design to the upper back. The Konva clamp prevents tall multi-line designs from clipping the collar. See `doc/qstomizer-automation.md` for the mechanics.

This pre-generates and caches mockup images so that when the user approves a design, the product creation is near-instant (no need to wait for Playwright). Cached mockups are stored alongside the proposal data.

## Tee Color (`tee_color`)

Mango's JSON schema includes `tee_color` (enum over the 6 Qstomizer colors). The LLM picks based on legibility:
- Light artwork (white text, pastels, pale illustrations) → `Black`
- Dark artwork (black text, dark ink) → `White`
- Slogan style with a signature fabric (e.g. maroon italic on white) → match the style
- Default `White` when in doubt

On approval, the value flows `concept.tee_color` → `execute_approval` → `create_mappings_for_product(color=...)` → persisted on the `ProductMapping` in `product_mappings.json`. At order time, the webhook handler reads `mapping.color` and threads it to `customize_and_add_to_cart` so the cart is built on the correct fabric.

## Slogan Hierarchy Template

For `slogan`-type designs, the LLM is instructed to embed a literal `\n` in `text_on_shirt` at the natural punchline break (e.g. `"DON'T TEMPT ME\nI'LL SAY YES"`, `"TOLD HER SHE'S THE ONE\nNOT THE ONLY ONE"`). `image_client.generate_text_design` then applies the Kyriaki-approved template:
- **Modest print scale** — text fills ~55% of the 1024×1024 canvas width, not billboard 80%+
- **Two-line hierarchy** — top line bold condensed caps (Impact / Liberation-Sans-Bold / Times-Bold), sub line regular-weight sans (Arial / Liberation-Sans-Regular / DejaVu-Sans) at 45% of top size for visible size+weight contrast
- Font pairs live in `TEXT_DESIGN_HIERARCHY_FONTS`; one is picked randomly per run so successive slogan tees look distinct
- Color, treatment (plain/outline/shadow) and case (uppercase 70% of the time) are randomised as before
- Single-line slogans render at the same modest scale without hierarchy

## Shopify Product Creation

Uses existing `write_products` scope (already authorized).

### Product Creation Payload

```json
{
  "product": {
    "title": "Mediterranean Sunset Graphic Tee",
    "body_html": "<p>LLM-generated description...</p>",
    "vendor": "OMG",
    "product_type": "T-Shirt",
    "tags": "graphic tee, mediterranean, summer",
    "variants": [
      {"option1": "Male", "option2": "S", "price": "30.00", "sku": "MED-SUNSET-M-S", "inventory_management": null},
      {"option1": "Male", "option2": "M", "price": "30.00", "sku": "MED-SUNSET-M-M", "inventory_management": null},
      {"option1": "Male", "option2": "L", "price": "30.00", "sku": "MED-SUNSET-M-L", "inventory_management": null},
      {"option1": "Male", "option2": "XL", "price": "30.00", "sku": "MED-SUNSET-M-XL", "inventory_management": null},
      {"option1": "Male", "option2": "2XL", "price": "35.00", "sku": "MED-SUNSET-M-2XL", "inventory_management": null},
      {"option1": "Male", "option2": "3XL", "price": "37.00", "sku": "MED-SUNSET-M-3XL", "inventory_management": null},
      {"option1": "Male", "option2": "4XL", "price": "39.50", "sku": "MED-SUNSET-M-4XL", "inventory_management": null},
      {"option1": "Male", "option2": "5XL", "price": "39.50", "sku": "MED-SUNSET-M-5XL", "inventory_management": null},
      {"option1": "Female", "option2": "S", "price": "30.00", "sku": "MED-SUNSET-F-S", "inventory_management": null},
      {"option1": "Female", "option2": "M", "price": "30.00", "sku": "MED-SUNSET-F-M", "inventory_management": null},
      {"option1": "Female", "option2": "L", "price": "30.00", "sku": "MED-SUNSET-F-L", "inventory_management": null},
      {"option1": "Female", "option2": "XL", "price": "30.00", "sku": "MED-SUNSET-F-XL", "inventory_management": null}
    ],
    "options": [{"name": "Gender"}, {"name": "Size"}],
    "images": [{"src": "base64_or_url"}]
  }
}
```

Note: `inventory_management` is set to `null` for all variants since this is print-on-demand (POD) and always available.

### Size Variants

| Gender | Sizes | Price Range |
|--------|-------|-------------|
| Male (Classic Tee) | S, M, L, XL, 2XL, 3XL, 4XL, 5XL | EUR 30-39.50 |
| Female (Women's Tee) | S, M, L, XL | EUR 30 |

Prices match existing OMG products for consistency.

## Product Mapping

After creating the OMG product, the agent auto-creates a mapping to TShirtJunkies:

- **Male designs** → TJ `classic-tee-up-to-5xl` (product ID: 9864408301915)
- **Female designs** → TJ `women-t-shirt` (product ID: 8676301799771)

Variant matching is by size (same as existing `mapper.py` logic). The new `ProductMapping` includes a `design_image` field pointing to the specific design PNG in `static/`.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/agents/design/research` | Manually trigger trend research |
| GET | `/agents/design/proposals` | List all design proposals |
| GET | `/agents/design/approve/{id}?token=...` | Approve design → create product |
| GET | `/agents/design/reject/{id}?token=...` | Reject design |
| GET | `/agents/design/preview/{id}` | View design image + details |

## Email Preview Format

```
Subject: [OMG Design] 5 new t-shirt concepts ready for review

For each design:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Design Image Thumbnail]
Concept: Mediterranean Sunset
Type: global-trend
Style: Minimalist vector illustration
Colors: Warm oranges and deep blues
Target: Unisex / Male tee

[✅ APPROVE]  [❌ REJECT]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Configuration

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API for trend research + product descriptions |
| `OPENAI_API_KEY` | DALL-E 3 for design generation |

## Modules

- **Agent:** `app/agents/design_creator.py`
- **Image generation:** `app/agents/image_client.py`
- **Product creation:** `app/shopify_product_creator.py`
- **Dependencies:** `app/agents/llm_client.py`, `app/agents/approval.py`, `app/mapper.py`

## Copyright Safety

All designs are AI-generated originals. The Claude prompt explicitly instructs:
- No copyrighted characters, logos, or trademarks
- No derivative works of existing designs
- Original compositions only
- If text is included, it must be original slogans (not copyrighted phrases)
