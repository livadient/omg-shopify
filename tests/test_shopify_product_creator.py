"""Tests for app/shopify_product_creator.py — product creation helpers."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.shopify_product_creator import (
    ADMIN_API_VERSION,
    CATEGORY_COLLECTIONS,
    COLLECTION_TAG_RULES,
    TJ_PRODUCTS,
    VARIANTS,
    _admin_url,
    create_product,
    upload_product_image,
)


class TestVariantsConstant:
    def test_total_count(self):
        # 3 options now: Gender × Placement × Size = 2 × 2 × (8+4) = 24
        assert len(VARIANTS) == 24

    def test_male_variants_count(self):
        # 8 sizes × 2 placements = 16
        male = [v for v in VARIANTS if v["option1"] == "Male"]
        assert len(male) == 16

    def test_female_variants_count(self):
        # 4 sizes × 2 placements = 8
        female = [v for v in VARIANTS if v["option1"] == "Female"]
        assert len(female) == 8

    def test_placements_front_and_back(self):
        placements = {v["option2"] for v in VARIANTS}
        assert placements == {"Front", "Back"}

    def test_all_have_shopify_inventory_management(self):
        for v in VARIANTS:
            assert v["inventory_management"] == "shopify"

    def test_male_sizes(self):
        sizes = []
        seen = set()
        for v in VARIANTS:
            if v["option1"] == "Male" and v["option3"] not in seen:
                sizes.append(v["option3"])
                seen.add(v["option3"])
        assert sizes == ["S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL"]

    def test_female_sizes(self):
        sizes = []
        seen = set()
        for v in VARIANTS:
            if v["option1"] == "Female" and v["option3"] not in seen:
                sizes.append(v["option3"])
                seen.add(v["option3"])
        assert sizes == ["S", "M", "L", "XL"]

    def test_all_have_price(self):
        for v in VARIANTS:
            assert "price" in v
            assert float(v["price"]) > 0

    def test_front_and_back_same_price(self):
        """Back variant should cost the same as Front for any gender/size."""
        by_key = {(v["option1"], v["option3"], v["option2"]): v["price"] for v in VARIANTS}
        for (gender, size, placement), price in by_key.items():
            other = "Back" if placement == "Front" else "Front"
            assert by_key[(gender, size, other)] == price


class TestTJProducts:
    def test_has_male_and_female(self):
        assert "male" in TJ_PRODUCTS
        assert "female" in TJ_PRODUCTS

    def test_male_product(self):
        assert TJ_PRODUCTS["male"]["handle"] == "classic-tee-up-to-5xl"
        assert TJ_PRODUCTS["male"]["product_id"] == 9864408301915

    def test_female_product(self):
        assert TJ_PRODUCTS["female"]["handle"] == "women-t-shirt"
        assert TJ_PRODUCTS["female"]["product_id"] == 8676301799771


class TestAdminUrl:
    def test_builds_correct_url(self, monkeypatch):
        monkeypatch.setattr(
            "app.shopify_product_creator.settings.omg_shopify_domain",
            "52922c-2.myshopify.com",
        )
        url = _admin_url("products.json")
        assert url == f"https://52922c-2.myshopify.com/admin/api/{ADMIN_API_VERSION}/products.json"

    def test_fallback_for_non_myshopify_domain(self, monkeypatch):
        monkeypatch.setattr(
            "app.shopify_product_creator.settings.omg_shopify_domain",
            "omg.com.cy",
        )
        url = _admin_url("products.json")
        assert "52922c-2.myshopify.com" in url


class TestCreateProduct:
    @pytest.mark.asyncio
    async def test_create_product_calls_api(self, monkeypatch):
        monkeypatch.setattr(
            "app.shopify_product_creator.settings.omg_shopify_domain",
            "test.myshopify.com",
        )
        monkeypatch.setattr(
            "app.shopify_product_creator.settings.omg_shopify_admin_token",
            "fake-token",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "product": {"id": 999, "title": "Test Tee", "variants": VARIANTS}
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await create_product("Test Tee", "<p>Description</p>", tags="test")
            assert result["id"] == 999


class TestUploadProductImage:
    @pytest.mark.asyncio
    async def test_upload_image(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.shopify_product_creator.settings.omg_shopify_domain",
            "test.myshopify.com",
        )
        monkeypatch.setattr(
            "app.shopify_product_creator.settings.omg_shopify_admin_token",
            "fake-token",
        )

        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"image": {"id": 1, "src": "https://cdn.shopify.com/test.png"}}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await upload_product_image(999, img, alt="Test")
            assert result["id"] == 1



class TestCategoryCollectionWiring:
    """Auto-categorization rules must route products correctly into the
    new feminine-tees collection without cross-polluting."""

    def test_feminine_tees_id_present(self):
        assert CATEGORY_COLLECTIONS["feminine-tees"] == 683679842684

    def test_feminine_tag_routes_to_feminine_tees(self):
        rules = COLLECTION_TAG_RULES["feminine-tees"]
        assert "feminine" in rules

    def test_aesthetic_keywords_present(self):
        rules = COLLECTION_TAG_RULES["feminine-tees"]
        for kw in ("coquette", "cottagecore", "soft girl", "that girl", "dreamy"):
            assert kw in rules, f"missing {kw}"

    def test_generic_women_keyword_excluded(self):
        # Every OMG tee has female variants, so 'women' as a tag would
        # cross-pollute and dump everything into the feminine collection.
        rules = COLLECTION_TAG_RULES["feminine-tees"]
        assert "women" not in rules

    def test_generic_pink_keyword_excluded(self):
        # 'pink' is too broad — many designs use pink without being feminine-coded.
        rules = COLLECTION_TAG_RULES["feminine-tees"]
        assert "pink" not in rules

    def test_summer_tees_unchanged(self):
        # Regression: the summer collection wiring should still work.
        assert CATEGORY_COLLECTIONS["summer-tees"] == 683678204284
        assert "summer" in COLLECTION_TAG_RULES["summer-tees"]
