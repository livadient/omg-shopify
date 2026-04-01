import json
from pathlib import Path

from app.models import MappingConfig, ProductMapping, VariantMapping
from app.shopify_client import fetch_product_from_url

MAPPING_FILE = Path(__file__).parent.parent / "product_mappings.json"


def load_mappings() -> MappingConfig:
    if MAPPING_FILE.exists():
        data = json.loads(MAPPING_FILE.read_text())
        return MappingConfig(**data)
    return MappingConfig()


def save_mappings(config: MappingConfig):
    MAPPING_FILE.write_text(config.model_dump_json(indent=2))


def _match_variants_by_option(
    source_variants: list[dict],
    target_variants: list[dict],
) -> list[VariantMapping]:
    """Match variants between two products by option values (e.g. size)."""
    mappings = []

    # Build lookup by normalized option values for target
    target_lookup: dict[str, dict] = {}
    for tv in target_variants:
        key = _variant_option_key(tv)
        target_lookup[key] = tv

    for sv in source_variants:
        key = _variant_option_key(sv)
        tv = target_lookup.get(key)
        if tv:
            mappings.append(VariantMapping(
                source_variant_id=sv["id"],
                source_title=sv.get("title", ""),
                target_variant_id=tv["id"],
                target_title=tv.get("title", ""),
                target_price=tv.get("price", ""),
            ))

    return mappings


def _variant_option_key(variant: dict) -> str:
    """Create a normalized key from variant options for matching."""
    parts = []
    for opt in ["option1", "option2", "option3"]:
        val = variant.get(opt)
        if val and val != "Default Title":
            parts.append(val.strip().lower())
    return "|".join(parts) if parts else "default"


async def create_mapping_from_urls(
    source_url: str,
    target_url: str,
) -> ProductMapping:
    """Fetch both products and create a mapping between them."""
    _, source_product = await fetch_product_from_url(source_url)
    _, target_product = await fetch_product_from_url(target_url)

    if not source_product:
        raise ValueError(f"Could not fetch source product from: {source_url}")
    if not target_product:
        raise ValueError(f"Could not fetch target product from: {target_url}")

    variant_mappings = _match_variants_by_option(
        source_product.get("variants", []),
        target_product.get("variants", []),
    )

    mapping = ProductMapping(
        source_handle=source_product["handle"],
        source_title=source_product["title"],
        target_handle=target_product["handle"],
        target_title=target_product["title"],
        target_product_id=target_product["id"],
        variants=variant_mappings,
    )

    # Save to file
    config = load_mappings()
    # Replace existing mapping for same source handle
    config.mappings = [
        m for m in config.mappings if m.source_handle != mapping.source_handle
    ]
    config.mappings.append(mapping)
    save_mappings(config)

    return mapping
