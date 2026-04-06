"""Tests for app/cart_client.py — TShirtJunkies cart operations."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cart_client import TShirtJunkiesCart


@pytest.fixture
def mock_settings(monkeypatch):
    monkeypatch.setattr("app.cart_client.settings.tshirtjunkies_base_url", "https://tshirtjunkies.co")


class TestGetCheckoutUrl:
    @pytest.mark.asyncio
    async def test_builds_correct_url(self, mock_settings):
        cart = TShirtJunkiesCart()
        cart.client = AsyncMock()
        cart.client.get = AsyncMock(return_value=MagicMock(
            status_code=200,
            json=MagicMock(return_value={
                "items": [
                    {"variant_id": 111, "quantity": 1},
                    {"variant_id": 222, "quantity": 2},
                ],
            }),
            raise_for_status=MagicMock(),
        ))
        url = await cart.get_checkout_url()
        assert url == "https://tshirtjunkies.co/cart/111:1,222:2"

    @pytest.mark.asyncio
    async def test_empty_cart_raises(self, mock_settings):
        cart = TShirtJunkiesCart()
        cart.client = AsyncMock()
        cart.client.get = AsyncMock(return_value=MagicMock(
            status_code=200,
            json=MagicMock(return_value={"items": []}),
            raise_for_status=MagicMock(),
        ))
        with pytest.raises(ValueError, match="Cart is empty"):
            await cart.get_checkout_url()


class TestAddItem:
    @pytest.mark.asyncio
    async def test_add_item(self, mock_settings):
        cart = TShirtJunkiesCart()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 111, "quantity": 1}
        mock_resp.raise_for_status = MagicMock()
        cart.client = AsyncMock()
        cart.client.post = AsyncMock(return_value=mock_resp)
        result = await cart.add_item(111, 1)
        assert result == {"id": 111, "quantity": 1}
        cart.client.post.assert_called_once()


class TestClearCart:
    @pytest.mark.asyncio
    async def test_clear_cart(self, mock_settings):
        cart = TShirtJunkiesCart()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_resp.raise_for_status = MagicMock()
        cart.client = AsyncMock()
        cart.client.post = AsyncMock(return_value=mock_resp)
        result = await cart.clear_cart()
        assert result == {"items": []}
