"""Tests for app/models.py — Pydantic models."""
import pytest
from pydantic import ValidationError

from app.models import MappingConfig, ProductMapping, VariantMapping


class TestVariantMapping:
    def test_creation(self, sample_variant_mapping_data):
        vm = VariantMapping(**sample_variant_mapping_data)
        assert vm.source_variant_id == 100
        assert vm.source_title == "M"
        assert vm.target_variant_id == 200
        assert vm.target_title == "M"
        assert vm.target_price == "20.00"

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            VariantMapping(
                source_variant_id=1,
                source_title="S",
                # missing target fields
            )

    def test_wrong_type_raises(self):
        with pytest.raises(ValidationError):
            VariantMapping(
                source_variant_id="not_an_int",
                source_title="S",
                target_variant_id=2,
                target_title="S",
                target_price="10.00",
            )


class TestProductMapping:
    def test_creation_with_defaults(self, sample_variant_mapping_data):
        pm = ProductMapping(
            source_handle="my-tee",
            source_title="My Tee",
            target_handle="classic-tee",
            target_title="Classic Tee",
            target_product_id=123,
            variants=[VariantMapping(**sample_variant_mapping_data)],
        )
        assert pm.design_image == "front_design.png"
        assert len(pm.variants) == 1

    def test_custom_design_image(self, sample_variant_mapping_data):
        pm = ProductMapping(
            source_handle="my-tee",
            source_title="My Tee",
            target_handle="classic-tee",
            target_title="Classic Tee",
            target_product_id=123,
            variants=[VariantMapping(**sample_variant_mapping_data)],
            design_image="custom_design.png",
        )
        assert pm.design_image == "custom_design.png"

    def test_empty_variants_list(self):
        pm = ProductMapping(
            source_handle="my-tee",
            source_title="My Tee",
            target_handle="classic-tee",
            target_title="Classic Tee",
            target_product_id=123,
            variants=[],
        )
        assert pm.variants == []


class TestMappingConfig:
    def test_default_empty(self):
        mc = MappingConfig()
        assert mc.mappings == []

    def test_with_mappings(self, sample_product_mapping_data):
        mc = MappingConfig(mappings=[ProductMapping(**sample_product_mapping_data)])
        assert len(mc.mappings) == 1
        assert mc.mappings[0].source_handle == "my-tee-male"

    def test_model_dump_json(self, sample_product_mapping_data):
        mc = MappingConfig(mappings=[ProductMapping(**sample_product_mapping_data)])
        json_str = mc.model_dump_json()
        assert "my-tee-male" in json_str
