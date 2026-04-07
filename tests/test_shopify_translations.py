"""Tests for app/shopify_translations.py — Shopify GraphQL Translations API client."""
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


class TestGraphqlNullSafety:
    @pytest.mark.asyncio
    async def test_sets_data_to_empty_dict_when_none(self):
        """When the API returns data=None, _graphql should set data['data'] to {}."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": None, "errors": None}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.shopify_translations.httpx.AsyncClient", return_value=mock_client):
            from app.shopify_translations import _graphql
            result = await _graphql("{ shop { name } }")

        assert result["data"] == {}

    @pytest.mark.asyncio
    async def test_preserves_data_when_present(self):
        """When the API returns valid data, it should be preserved."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": {"shop": {"name": "test"}}}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.shopify_translations.httpx.AsyncClient", return_value=mock_client):
            from app.shopify_translations import _graphql
            result = await _graphql("{ shop { name } }")

        assert result["data"] == {"shop": {"name": "test"}}


class TestEnsureLocaleEnabled:
    @pytest.mark.asyncio
    async def test_already_enabled_and_published(self):
        """If locale is already enabled and published, should return True immediately."""
        from app.shopify_translations import ensure_locale_enabled

        with patch("app.shopify_translations._graphql", new_callable=AsyncMock) as mock_gql:
            mock_gql.return_value = {
                "data": {
                    "shopLocales": [
                        {"locale": "en", "published": True},
                        {"locale": "el", "published": True},
                    ]
                }
            }

            result = await ensure_locale_enabled("el")

        assert result is True
        # Only one call to check locales, no enable/publish calls
        mock_gql.assert_called_once()

    @pytest.mark.asyncio
    async def test_enable_and_publish_new_locale(self):
        """If locale is not present, should enable then publish."""
        from app.shopify_translations import ensure_locale_enabled

        with patch("app.shopify_translations._graphql", new_callable=AsyncMock) as mock_gql:
            mock_gql.side_effect = [
                # First call: check existing locales — el not present
                {"data": {"shopLocales": [{"locale": "en", "published": True}]}},
                # Second call: enable locale
                {"data": {"shopLocaleEnable": {"shopLocale": {"locale": "el", "published": False}, "userErrors": []}}},
                # Third call: publish locale
                {"data": {"shopLocaleUpdate": {"shopLocale": {"locale": "el", "published": True}, "userErrors": []}}},
            ]

            result = await ensure_locale_enabled("el")

        assert result is True
        assert mock_gql.call_count == 3

    @pytest.mark.asyncio
    async def test_publish_already_enabled_but_unpublished(self):
        """If locale exists but not published, should skip enable, just publish."""
        from app.shopify_translations import ensure_locale_enabled

        with patch("app.shopify_translations._graphql", new_callable=AsyncMock) as mock_gql:
            mock_gql.side_effect = [
                # Check: el exists but unpublished
                {"data": {"shopLocales": [{"locale": "el", "published": False}]}},
                # Publish
                {"data": {"shopLocaleUpdate": {"shopLocale": {"locale": "el", "published": True}, "userErrors": []}}},
            ]

            result = await ensure_locale_enabled("el")

        assert result is True
        assert mock_gql.call_count == 2

    @pytest.mark.asyncio
    async def test_enable_error_returns_false(self):
        """If enable mutation returns userErrors, should return False."""
        from app.shopify_translations import ensure_locale_enabled

        with patch("app.shopify_translations._graphql", new_callable=AsyncMock) as mock_gql:
            mock_gql.side_effect = [
                {"data": {"shopLocales": []}},
                {"data": {"shopLocaleEnable": {"shopLocale": None, "userErrors": [{"message": "access denied", "field": None}]}}},
            ]

            result = await ensure_locale_enabled("el")

        assert result is False

    @pytest.mark.asyncio
    async def test_publish_error_returns_false(self):
        """If publish mutation returns userErrors, should return False."""
        from app.shopify_translations import ensure_locale_enabled

        with patch("app.shopify_translations._graphql", new_callable=AsyncMock) as mock_gql:
            mock_gql.side_effect = [
                {"data": {"shopLocales": [{"locale": "el", "published": False}]}},
                {"data": {"shopLocaleUpdate": {"shopLocale": None, "userErrors": [{"message": "fail", "field": None}]}}},
            ]

            result = await ensure_locale_enabled("el")

        assert result is False


class TestFindUntranslated:
    @pytest.mark.asyncio
    async def test_skips_handle_keys(self):
        """Fields with key='handle' should be skipped."""
        from app.shopify_translations import find_untranslated

        with patch("app.shopify_translations.get_translatable_resources", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {
                "data": {
                    "translatableResources": {
                        "edges": [
                            {
                                "node": {
                                    "resourceId": "gid://shopify/Product/123",
                                    "translatableContent": [
                                        {"key": "handle", "value": "my-product", "digest": "abc", "locale": "en"},
                                        {"key": "title", "value": "My Product", "digest": "def", "locale": "en"},
                                    ],
                                    "translations": [],
                                }
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }

            result = await find_untranslated(locale="el", resource_types=["PRODUCT"], max_per_type=50)

        assert len(result) == 1
        field_keys = [f["key"] for f in result[0]["fields"]]
        assert "handle" not in field_keys
        assert "title" in field_keys

    @pytest.mark.asyncio
    async def test_skips_empty_values(self):
        """Fields with empty/None value should be skipped."""
        from app.shopify_translations import find_untranslated

        with patch("app.shopify_translations.get_translatable_resources", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {
                "data": {
                    "translatableResources": {
                        "edges": [
                            {
                                "node": {
                                    "resourceId": "gid://shopify/Product/123",
                                    "translatableContent": [
                                        {"key": "title", "value": "", "digest": "abc", "locale": "en"},
                                        {"key": "body_html", "value": None, "digest": "def", "locale": "en"},
                                        {"key": "meta_title", "value": "Real Title", "digest": "ghi", "locale": "en"},
                                    ],
                                    "translations": [],
                                }
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }

            result = await find_untranslated(locale="el", resource_types=["PRODUCT"], max_per_type=50)

        assert len(result) == 1
        field_keys = [f["key"] for f in result[0]["fields"]]
        assert "title" not in field_keys
        assert "body_html" not in field_keys
        assert "meta_title" in field_keys

    @pytest.mark.asyncio
    async def test_detects_outdated_translations(self):
        """Fields with outdated translations should be included."""
        from app.shopify_translations import find_untranslated

        with patch("app.shopify_translations.get_translatable_resources", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {
                "data": {
                    "translatableResources": {
                        "edges": [
                            {
                                "node": {
                                    "resourceId": "gid://shopify/Product/123",
                                    "translatableContent": [
                                        {"key": "title", "value": "Updated Title", "digest": "abc", "locale": "en"},
                                    ],
                                    "translations": [
                                        {"key": "title", "value": "Old Greek", "outdated": True},
                                    ],
                                }
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }

            result = await find_untranslated(locale="el", resource_types=["PRODUCT"], max_per_type=50)

        assert len(result) == 1
        assert result[0]["fields"][0]["key"] == "title"

    @pytest.mark.asyncio
    async def test_skips_already_translated(self):
        """Fields with valid, non-outdated translations should be excluded."""
        from app.shopify_translations import find_untranslated

        with patch("app.shopify_translations.get_translatable_resources", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {
                "data": {
                    "translatableResources": {
                        "edges": [
                            {
                                "node": {
                                    "resourceId": "gid://shopify/Product/123",
                                    "translatableContent": [
                                        {"key": "title", "value": "My Product", "digest": "abc", "locale": "en"},
                                    ],
                                    "translations": [
                                        {"key": "title", "value": "Greek Title", "outdated": False},
                                    ],
                                }
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }

            result = await find_untranslated(locale="el", resource_types=["PRODUCT"], max_per_type=50)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_detects_missing_translations(self):
        """Fields with no translation at all should be included."""
        from app.shopify_translations import find_untranslated

        with patch("app.shopify_translations.get_translatable_resources", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {
                "data": {
                    "translatableResources": {
                        "edges": [
                            {
                                "node": {
                                    "resourceId": "gid://shopify/Product/456",
                                    "translatableContent": [
                                        {"key": "title", "value": "Cool Tee", "digest": "xyz", "locale": "en"},
                                        {"key": "body_html", "value": "<p>Desc</p>", "digest": "uvw", "locale": "en"},
                                    ],
                                    "translations": [],
                                }
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }

            result = await find_untranslated(locale="el", resource_types=["PRODUCT"], max_per_type=50)

        assert len(result) == 1
        assert len(result[0]["fields"]) == 2


class TestRegisterTranslations:
    @pytest.mark.asyncio
    async def test_correct_mutation_variables(self):
        """Verify register_translations passes correct variables to _graphql."""
        from app.shopify_translations import register_translations

        translations_input = [
            {
                "locale": "el",
                "key": "title",
                "value": "Greek Title",
                "translatableContentDigest": "abc123",
            }
        ]

        with patch("app.shopify_translations._graphql", new_callable=AsyncMock) as mock_gql:
            mock_gql.return_value = {
                "data": {
                    "translationsRegister": {
                        "translations": [{"key": "title", "value": "Greek Title"}],
                        "userErrors": [],
                    }
                }
            }

            result = await register_translations("gid://shopify/Product/123", translations_input)

        mock_gql.assert_called_once()
        call_args = mock_gql.call_args
        variables = call_args[1]["variables"] if "variables" in call_args[1] else call_args[0][1]
        assert variables["resourceId"] == "gid://shopify/Product/123"
        assert variables["translations"] == translations_input

    @pytest.mark.asyncio
    async def test_returns_registered_data(self):
        """Verify it returns the translationsRegister data."""
        from app.shopify_translations import register_translations

        with patch("app.shopify_translations._graphql", new_callable=AsyncMock) as mock_gql:
            mock_gql.return_value = {
                "data": {
                    "translationsRegister": {
                        "translations": [{"key": "title", "value": "Greek"}],
                        "userErrors": [],
                    }
                }
            }

            result = await register_translations("gid://shopify/Product/123", [])

        assert result["translations"] == [{"key": "title", "value": "Greek"}]
        assert result["userErrors"] == []
