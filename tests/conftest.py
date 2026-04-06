"""Shared fixtures for the test suite."""
import json

import pytest


@pytest.fixture
def sample_variant_mapping_data():
    return {
        "source_variant_id": 100,
        "source_title": "M",
        "target_variant_id": 200,
        "target_title": "M",
        "target_price": "20.00",
    }


@pytest.fixture
def sample_product_mapping_data(sample_variant_mapping_data):
    return {
        "source_handle": "my-tee-male",
        "source_title": "My Tee Male",
        "target_handle": "classic-tee-up-to-5xl",
        "target_title": "Classic Tee",
        "target_product_id": 9864408301915,
        "variants": [sample_variant_mapping_data],
    }


@pytest.fixture
def sample_mapping_config_data(sample_product_mapping_data):
    return {"mappings": [sample_product_mapping_data]}


@pytest.fixture
def sample_order_email_text():
    return (
        "Order summary\n"
        "\n"
        "Astous na Laloun Graphic Tee Male \u2014 EU Edition \u00d7 1\n"
        "M\n"
        "\u20ac30,00\n"
        "\n"
        "Subtotal\n"
        "\u20ac30,00\n"
        "Shipping\n"
        "\u20ac3,00\n"
        "Total\n"
        "\u20ac33,00\n"
        "\n"
        "Shipping address\n"
        "Vangelis Livadiotis\n"
        "7 Michalaki Zampa\n"
        "2109 Nicosia\n"
        "Cyprus\n"
    )


@pytest.fixture
def sample_shopify_product():
    return {
        "id": 12345,
        "handle": "my-tee",
        "title": "My Tee",
        "variants": [
            {"id": 1, "title": "S", "option1": "S", "option2": None, "option3": None, "price": "20.00"},
            {"id": 2, "title": "M", "option1": "M", "option2": None, "option3": None, "price": "20.00"},
            {"id": 3, "title": "L", "option1": "L", "option2": None, "option3": None, "price": "20.00"},
        ],
    }


@pytest.fixture
def tmp_json_file(tmp_path):
    """Return a helper that writes JSON to a temp file and returns the path."""
    def _write(data, filename="test.json"):
        path = tmp_path / filename
        path.write_text(json.dumps(data, indent=2))
        return path
    return _write
