"""Tests for app/mapper.py — product mapping logic."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.mapper import (
    _match_variants_by_option,
    _variant_option_key,
    load_mappings,
    save_mappings,
)
from app.models import MappingConfig, ProductMapping, VariantMapping


class TestVariantOptionKey:
    def test_single_option(self):
        assert _variant_option_key({"option1": "M"}) == "m"

    def test_multiple_options(self):
        assert _variant_option_key({"option1": "Male", "option2": "L"}) == "male|l"

    def test_default_title_ignored(self):
        assert _variant_option_key({"option1": "Default Title"}) == "default"

    def test_none_options(self):
        assert _variant_option_key({"option1": None, "option2": None}) == "default"

    def test_whitespace_stripped(self):
        assert _variant_option_key({"option1": " XL "}) == "xl"

    def test_empty_dict(self):
        assert _variant_option_key({}) == "default"


class TestMatchVariantsByOption:
    def test_matching_by_size(self):
        source = [
            {"id": 1, "title": "S", "option1": "S"},
            {"id": 2, "title": "M", "option1": "M"},
            {"id": 3, "title": "XL", "option1": "XL"},
        ]
        target = [
            {"id": 10, "title": "S", "option1": "S", "price": "20.00"},
            {"id": 20, "title": "M", "option1": "M", "price": "20.00"},
            {"id": 30, "title": "L", "option1": "L", "price": "20.00"},
        ]
        result = _match_variants_by_option(source, target)
        assert len(result) == 2  # S and M match, XL and L don't
        assert result[0].source_variant_id == 1
        assert result[0].target_variant_id == 10
        assert result[1].source_variant_id == 2
        assert result[1].target_variant_id == 20

    def test_case_insensitive_matching(self):
        source = [{"id": 1, "title": "small", "option1": "Small"}]
        target = [{"id": 10, "title": "SMALL", "option1": "SMALL", "price": "20.00"}]
        result = _match_variants_by_option(source, target)
        assert len(result) == 1

    def test_no_matches(self):
        source = [{"id": 1, "title": "XS", "option1": "XS"}]
        target = [{"id": 10, "title": "5XL", "option1": "5XL", "price": "20.00"}]
        result = _match_variants_by_option(source, target)
        assert len(result) == 0

    def test_empty_inputs(self):
        assert _match_variants_by_option([], []) == []


class TestLoadSaveMappings:
    def test_load_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.mapper.MAPPING_FILE", tmp_path / "missing.json")
        config = load_mappings()
        assert config.mappings == []

    def test_load_existing_file(self, tmp_path, monkeypatch, sample_product_mapping_data):
        path = tmp_path / "mappings.json"
        data = {"mappings": [sample_product_mapping_data]}
        path.write_text(json.dumps(data))
        monkeypatch.setattr("app.mapper.MAPPING_FILE", path)
        config = load_mappings()
        assert len(config.mappings) == 1
        assert config.mappings[0].source_handle == "my-tee-male"

    def test_save_and_reload(self, tmp_path, monkeypatch, sample_product_mapping_data):
        path = tmp_path / "mappings.json"
        monkeypatch.setattr("app.mapper.MAPPING_FILE", path)
        config = MappingConfig(mappings=[ProductMapping(**sample_product_mapping_data)])
        save_mappings(config)
        assert path.exists()
        reloaded = load_mappings()
        assert len(reloaded.mappings) == 1
        assert reloaded.mappings[0].target_product_id == 9864408301915


class TestCreateMappingFromUrls:
    @pytest.mark.asyncio
    async def test_create_mapping_from_urls(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.mapper.MAPPING_FILE", tmp_path / "mappings.json")

        source_product = {
            "handle": "src-tee",
            "title": "Source Tee",
            "variants": [
                {"id": 1, "title": "M", "option1": "M"},
            ],
        }
        target_product = {
            "id": 999,
            "handle": "tgt-tee",
            "title": "Target Tee",
            "variants": [
                {"id": 10, "title": "M", "option1": "M", "price": "22.00"},
            ],
        }

        async def mock_fetch(url):
            if "source" in url:
                return ("https://source.com", source_product)
            return ("https://target.com", target_product)

        with patch("app.mapper.fetch_product_from_url", side_effect=mock_fetch):
            from app.mapper import create_mapping_from_urls
            mapping = await create_mapping_from_urls(
                "https://source.com/products/src-tee",
                "https://target.com/products/tgt-tee",
            )
        assert mapping.source_handle == "src-tee"
        assert mapping.target_handle == "tgt-tee"
        assert len(mapping.variants) == 1
        assert mapping.variants[0].target_price == "22.00"
