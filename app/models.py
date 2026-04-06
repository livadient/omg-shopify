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


class MappingConfig(BaseModel):
    mappings: list[ProductMapping] = []
