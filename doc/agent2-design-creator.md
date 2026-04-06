# Agent 2: Trend Research & Design Creator

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
   b. Post-process: remove background with rembg for print-ready PNG
   c. Pre-cache TShirtJunkies mockups via Qstomizer Playwright automation
      (male L + female M) so approval is near-instant
6. Save proposals to data/proposals.json (status: "pending")
7. Email user with design thumbnails + Approve/Reject per design
8. User clicks Approve for chosen designs
9. For each approved design:
   a. Use cached mockup images if available (skips Playwright wait)
   b. Generate product description via LLM
   c. Create product on OMG Shopify (with Gender+Size variants)
   d. Upload design as product image
   e. Upload cached mockup images to product
   f. Save design PNG to static/ for Playwright automation
   g. Auto-create mapping in product_mappings.json
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

This pre-generates and caches mockup images so that when the user approves a design, the product creation is near-instant (no need to wait for Playwright). Cached mockups are stored alongside the proposal data.

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
