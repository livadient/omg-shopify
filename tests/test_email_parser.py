"""Tests for app/email_parser.py — pure string parsing."""
from app.email_parser import COUNTRY_CODES, _country_to_code, parse_order_email


class TestParseOrderEmail:
    def test_full_email(self, sample_order_email_text):
        result = parse_order_email(sample_order_email_text)

        # Items
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["title"] == "Astous na Laloun Graphic Tee Male \u2014 EU Edition"
        assert item["variant_title"] == "M"
        assert item["quantity"] == 1
        assert item["product_type"] == "male"

        # Total
        assert "\u20ac33,00" in result["total"]

        # Shipping
        shipping = result["shipping"]
        assert shipping["first_name"] == "Vangelis"
        assert shipping["last_name"] == "Livadiotis"
        assert shipping["address1"] == "7 Michalaki Zampa"
        assert shipping["zip"] == "2109"
        assert shipping["city"] == "Nicosia"
        assert shipping["country"] == "Cyprus"
        assert shipping["country_code"] == "CY"

    def test_female_product_detection(self):
        text = (
            "Order summary\n"
            "\n"
            "Astous na Laloun Graphic Tee Female \u2014 EU Edition \u00d7 2\n"
            "S\n"
            "\u20ac60,00\n"
            "\n"
            "Shipping address\n"
            "Jane Doe\n"
            "123 Main St\n"
            "10431 Athens\n"
            "Greece\n"
        )
        result = parse_order_email(text)
        assert len(result["items"]) == 1
        assert result["items"][0]["product_type"] == "female"
        assert result["items"][0]["quantity"] == 2
        assert result["items"][0]["variant_title"] == "S"
        assert result["shipping"]["country_code"] == "GR"

    def test_multiple_items(self):
        text = (
            "Order summary\n"
            "\n"
            "My Tee Male \u00d7 1\n"
            "L\n"
            "\u20ac30,00\n"
            "My Tee Female \u00d7 1\n"
            "XL\n"
            "\u20ac30,00\n"
            "\n"
            "Shipping address\n"
            "John Doe\n"
            "1 Street\n"
            "1000 City\n"
            "France\n"
        )
        result = parse_order_email(text)
        assert len(result["items"]) == 2
        assert result["items"][0]["product_type"] == "male"
        assert result["items"][0]["variant_title"] == "L"
        assert result["items"][1]["product_type"] == "female"
        assert result["items"][1]["variant_title"] == "XL"

    def test_missing_shipping_address(self):
        text = (
            "Order summary\n"
            "My Tee \u00d7 1\n"
            "M\n"
            "\u20ac30,00\n"
        )
        result = parse_order_email(text)
        assert result["shipping"] == {}
        assert len(result["items"]) == 1

    def test_empty_input(self):
        result = parse_order_email("")
        assert result["items"] == []
        assert result["shipping"] == {}
        assert result["total"] == ""

    def test_city_zip_reversed_format(self):
        text = (
            "Shipping address\n"
            "John Doe\n"
            "1 Street\n"
            "Athens 10431\n"
            "Greece\n"
        )
        result = parse_order_email(text)
        assert result["shipping"]["city"] == "Athens"
        assert result["shipping"]["zip"] == "10431"

    def test_city_without_zip(self):
        text = (
            "Shipping address\n"
            "John Doe\n"
            "1 Street\n"
            "London\n"
            "United Kingdom\n"
        )
        result = parse_order_email(text)
        assert result["shipping"]["city"] == "London"
        assert "zip" not in result["shipping"]


class TestCountryToCode:
    def test_known_countries(self):
        assert _country_to_code("Cyprus") == "CY"
        assert _country_to_code("Greece") == "GR"
        assert _country_to_code("France") == "FR"
        assert _country_to_code("United Kingdom") == "GB"
        assert _country_to_code("USA") == "US"
        assert _country_to_code("Germany") == "DE"

    def test_case_insensitive(self):
        assert _country_to_code("CYPRUS") == "CY"
        assert _country_to_code("greece") == "GR"
        assert _country_to_code("United kingdom") == "GB"

    def test_unknown_country_returns_two_letter_prefix(self):
        assert _country_to_code("Japan") == "JA"
        assert _country_to_code("Brazil") == "BR"

    def test_empty_string(self):
        assert _country_to_code("") == ""

    def test_whitespace_stripped(self):
        assert _country_to_code("  Cyprus  ") == "CY"

    def test_country_codes_dict_has_expected_entries(self):
        assert len(COUNTRY_CODES) >= 20
        assert "cyprus" in COUNTRY_CODES
        assert "greece" in COUNTRY_CODES
