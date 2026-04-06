"""Tests for app/shopify_client.py — URL parsing and HTTP calls."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.shopify_client import fetch_product_by_handle, fetch_product_from_url


class TestFetchProductFromUrl:
    @pytest.mark.asyncio
    async def test_extracts_handle_from_simple_url(self):
        with patch("app.shopify_client.fetch_product_by_handle", new_callable=AsyncMock) as mock:
            mock.return_value = {"id": 1, "handle": "my-tee"}
            base_url, product = await fetch_product_from_url(
                "https://store.com/products/my-tee"
            )
            assert base_url == "https://store.com"
            mock.assert_called_once_with("https://store.com", "my-tee")

    @pytest.mark.asyncio
    async def test_extracts_handle_from_collection_url(self):
        with patch("app.shopify_client.fetch_product_by_handle", new_callable=AsyncMock) as mock:
            mock.return_value = {"id": 1, "handle": "cool-shirt"}
            base_url, product = await fetch_product_from_url(
                "https://store.com/collections/all/products/cool-shirt"
            )
            assert base_url == "https://store.com"
            mock.assert_called_once_with("https://store.com", "cool-shirt")

    @pytest.mark.asyncio
    async def test_trailing_slash_stripped(self):
        with patch("app.shopify_client.fetch_product_by_handle", new_callable=AsyncMock) as mock:
            mock.return_value = {"id": 1, "handle": "my-tee"}
            await fetch_product_from_url("https://store.com/products/my-tee/")
            mock.assert_called_once_with("https://store.com", "my-tee")

    @pytest.mark.asyncio
    async def test_no_products_in_path(self):
        base_url, product = await fetch_product_from_url("https://store.com/about")
        assert product is None

    @pytest.mark.asyncio
    async def test_returns_none_when_product_not_found(self):
        with patch("app.shopify_client.fetch_product_by_handle", new_callable=AsyncMock) as mock:
            mock.return_value = None
            _, product = await fetch_product_from_url(
                "https://store.com/products/nonexistent"
            )
            assert product is None


class TestFetchProductByHandle:
    @pytest.mark.asyncio
    async def test_direct_endpoint_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"product": {"id": 1, "handle": "tee"}}

        with patch("httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await fetch_product_by_handle("https://store.com", "tee")
            assert result == {"id": 1, "handle": "tee"}

    @pytest.mark.asyncio
    async def test_fallback_to_catalog(self):
        not_found = MagicMock()
        not_found.status_code = 404

        catalog_resp = MagicMock()
        catalog_resp.status_code = 200
        catalog_resp.json.return_value = {
            "products": [
                {"handle": "other", "id": 2},
                {"handle": "my-tee", "id": 3},
            ]
        }

        empty_page = MagicMock()
        empty_page.status_code = 200
        empty_page.json.return_value = {"products": []}

        with patch("httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(side_effect=[not_found, catalog_resp])
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await fetch_product_by_handle("https://store.com", "my-tee")
            assert result == {"handle": "my-tee", "id": 3}
