"""Tests for app/shopify_product_creator.py — product creation helpers."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.shopify_product_creator import (
    ADMIN_API_VERSION,
    TJ_PRODUCTS,
    VARIANTS,
    _admin_url,
    create_product,
    upload_product_image,
)


class TestVariantsConstant:
    def test_total_count(self):
        assert len(VARIANTS) == 12  # 8 male + 4 female

    def test_male_variants_count(self):
        male = [v for v in VARIANTS if v["option1"] == "Male"]
        assert len(male) == 8

    def test_female_variants_count(self):
        female = [v for v in VARIANTS if v["option1"] == "Female"]
        assert len(female) == 4

    def test_all_have_no_inventory_management(self):
        for v in VARIANTS:
            assert v["inventory_management"] is None

    def test_male_sizes(self):
        male_sizes = [v["option2"] for v in VARIANTS if v["option1"] == "Male"]
        assert male_sizes == ["S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL"]

    def test_female_sizes(self):
        female_sizes = [v["option2"] for v in VARIANTS if v["option1"] == "Female"]
        assert female_sizes == ["S", "M", "L", "XL"]

    def test_all_have_price(self):
        for v in VARIANTS:
            assert "price" in v
            assert float(v["price"]) > 0


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
