"""Tests for app/omg_fulfillment.py — fulfillment email parsing and helpers."""
import pytest

from app.omg_fulfillment import ADMIN_API_VERSION, _admin_url, _shop_domain, parse_fulfillment_email


class TestParseFulfillmentEmail:
    def test_extracts_omg_order_number(self):
        text = "Customer: John Doe (OMG #1234) has been shipped."
        result = parse_fulfillment_email(text)
        assert result["omg_order_number"] == "1234"

    def test_extracts_tracking_url(self):
        text = "Track your package: https://www.dhl.com/tracking?code=ABC123"
        result = parse_fulfillment_email(text)
        assert "dhl.com/tracking" in result["tracking_url"]

    def test_extracts_tracking_number(self):
        # The regex requires "tracking" (or "track"/"shipment") followed by the number
        text = "Your tracking #ABC12345678 is ready"
        result = parse_fulfillment_email(text)
        assert result["tracking_number"] == "ABC12345678"

    def test_detects_carrier(self):
        text = "Shipped via DHL Express"
        result = parse_fulfillment_email(text)
        assert result["tracking_company"] == "DHL"

    def test_detects_cyprus_post(self):
        text = "Shipped via Cyprus Post. Tracking: RR123456789CY"
        result = parse_fulfillment_email(text)
        assert result["tracking_company"] == "Cyprus Post"
        assert result["tracking_number"] == "RR123456789CY"

    def test_full_email(self):
        text = (
            "Dear Customer,\n"
            "Your order Name (OMG #1055) has been shipped.\n"
            "Carrier: DHL\n"
            "Tracking: 1234567890ABC\n"
            "Track at: https://www.dhl.com/tracking?code=1234567890ABC\n"
        )
        result = parse_fulfillment_email(text)
        assert result["omg_order_number"] == "1055"
        assert result["tracking_number"] == "1234567890ABC"
        assert "dhl.com/tracking" in result["tracking_url"]
        assert result["tracking_company"] == "DHL"

    def test_missing_all_fields(self):
        result = parse_fulfillment_email("Hello, this is a random email.")
        assert result["omg_order_number"] == ""
        assert result["tracking_number"] == ""
        assert result["tracking_url"] == ""
        assert result["tracking_company"] == ""

    def test_empty_input(self):
        result = parse_fulfillment_email("")
        assert result["omg_order_number"] == ""


class TestShopDomain:
    def test_myshopify_domain_unchanged(self, monkeypatch):
        monkeypatch.setattr("app.omg_fulfillment.settings.omg_shopify_domain", "test.myshopify.com")
        assert _shop_domain() == "test.myshopify.com"

    def test_non_myshopify_falls_back(self, monkeypatch):
        monkeypatch.setattr("app.omg_fulfillment.settings.omg_shopify_domain", "omg.com.cy")
        assert _shop_domain() == "52922c-2.myshopify.com"


class TestAdminUrl:
    def test_builds_correct_url(self, monkeypatch):
        monkeypatch.setattr("app.omg_fulfillment.settings.omg_shopify_domain", "test.myshopify.com")
        url = _admin_url("orders.json")
        assert url == f"https://test.myshopify.com/admin/api/{ADMIN_API_VERSION}/orders.json"
