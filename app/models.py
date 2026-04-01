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
    variants: list[VariantMapping]


class MappingConfig(BaseModel):
    mappings: list[ProductMapping] = []
