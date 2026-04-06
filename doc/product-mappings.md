# Product Mappings

## Overview

Product mappings connect OMG store products to their TShirtJunkies fulfillment counterparts. When an order comes in, the service uses these mappings to determine which TJ product/variant to customize and order.

## Storage

Mappings are stored in `product_mappings.json` at the project root. This file persists across deploys (mounted as a Docker volume in production).

## Data Models

**File:** `app/models.py`

### MappingConfig

Top-level container:
```python
class MappingConfig(BaseModel):
    mappings: list[ProductMapping] = []
```

### ProductMapping

Maps one OMG product to one TJ product:
```python
class ProductMapping(BaseModel):
    source_handle: str          # OMG product handle
    source_title: str           # OMG product title
    target_handle: str          # TJ product handle
    target_title: str           # TJ product title
    target_product_id: int      # TJ product ID
    variants: list[VariantMapping]
    design_image: str = "front_design.png"  # per-product design PNG in static/
```

The `design_image` field specifies which PNG file in `static/` to upload to Qstomizer during automation. Each product mapping can have its own design file.

### VariantMapping

Maps one OMG variant to one TJ variant (matched by size):
```python
class VariantMapping(BaseModel):
    source_variant_id: int      # OMG Shopify variant ID
    source_title: str           # e.g., "Male / L"
    target_variant_id: int      # TJ Shopify variant ID
    target_title: str           # e.g., "L"
    target_price: str           # TJ price (e.g., "20.00")
```

## Current Mappings

### Male Tee (S-5XL)

- **OMG:** `astous-va-laloun-graphic-tee-male-eu-edition` (EUR 30-39.50)
- **TJ:** `classic-tee-up-to-5xl` (EUR 20-22)
- Full size coverage: S, M, L, XL, 2XL, 3XL, 4XL, 5XL

### Female Tee (S-XL)

- **OMG:** `astous-va-laloun-graphic-tee-female-eu-edition` (EUR 30)
- **TJ:** `women-t-shirt` (EUR 23)
- Full size coverage: S, M, L, XL

## Variant Matching Logic

**File:** `app/mapper.py`

Variants are matched between source and target products by **normalized option values**. The `_variant_option_key()` function builds a lowercase key from `option1`, `option2`, `option3` (excluding "Default Title"), joined by `|`.

For example:
- OMG variant with option1="Male", option2="L" -> key `"male|l"`
- TJ variant with option1="L" -> key `"l"`

Since OMG products have Gender+Size options but TJ products only have Size, the mapping via `create_mappings_for_product()` in `shopify_product_creator.py` handles this by first grouping OMG variants by gender, then matching each group against the appropriate TJ product by size (option2 on OMG, option1 on TJ).

## Creating Mappings

### Via API Endpoint

```
POST /map-products?source_url=https://omg.com.cy/products/my-tee&target_url=https://tshirtjunkies.co/products/classic-tee-up-to-5xl
```

Fetches both products, matches variants by option values, and saves the mapping. Replaces any existing mapping for the same source handle.

### Via Design Creator Agent

When the design creator agent's proposal is approved, `create_mappings_for_product()` automatically creates TWO mappings (male variants -> TJ Classic Tee, female variants -> TJ Women's Tee) and saves the design image filename.

### Programmatically

```python
from app.mapper import load_mappings, save_mappings, create_mapping_from_urls

# Load existing
config = load_mappings()

# Create from URLs
mapping = await create_mapping_from_urls(source_url, target_url)

# Or manipulate directly
config.mappings.append(new_mapping)
save_mappings(config)
```

## Key Files

- `app/models.py` -- Pydantic models (MappingConfig, ProductMapping, VariantMapping)
- `app/mapper.py` -- Loading, saving, variant matching, URL-based creation
- `app/shopify_product_creator.py` -- `create_mappings_for_product()` for design creator flow
- `product_mappings.json` -- Persisted mapping data
