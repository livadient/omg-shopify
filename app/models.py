from pydantic import BaseModel


class VariantMapping(BaseModel):
    source_variant_id: int
    source_title: str
    target_variant_id: int
    target_title: str
    target_price: str


class ProductMapping(BaseModel):
    source_handle: str
    source_title: str
    target_handle: str
    target_title: str
    target_product_id: int
    variants: list[VariantMapping]
    design_image: str = "front_design.png"  # per-product design PNG in static/
    # Qstomizer tee color printed under the design. Default White for legacy
    # mappings; black/etc products set this at creation time so the webhook
    # order flow (and TJ mockup pre-cache) pick the matching fabric.
    color: str = "White"


class MappingConfig(BaseModel):
    mappings: list[ProductMapping] = []
