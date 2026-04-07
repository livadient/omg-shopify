"""Tests for app/agents/translation_checker.py — Translation Checker agent."""
from unittest.mock import AsyncMock, patch

import pytest


class TestTranslateBatch:
    @pytest.mark.asyncio
    async def test_single_field_uses_simple_prompt(self):
        """Single field should call llm_client.generate once, not use JSON format."""
        from app.agents.translation_checker import _translate_batch

        with patch("app.agents.translation_checker.llm_client") as mock_llm:
            mock_llm.generate = AsyncMock(return_value="  Greek Translation  ")

            result = await _translate_batch({"title": "Cool Tee"})

        assert result == {"title": "Greek Translation"}
        mock_llm.generate.assert_called_once()
        # Verify it used the simple prompt (not JSON batch)
        call_kwargs = mock_llm.generate.call_args[1]
        assert "Cool Tee" in call_kwargs["user_prompt"]

    @pytest.mark.asyncio
    async def test_multi_field_uses_json_format(self):
        """Multiple fields should use JSON batch format."""
        from app.agents.translation_checker import _translate_batch

        with patch("app.agents.translation_checker.llm_client") as mock_llm:
            mock_llm.generate = AsyncMock(
                return_value='```json\n{"title": "Greek Title", "body_html": "Greek Body"}\n```'
            )

            result = await _translate_batch({"title": "Cool Tee", "body_html": "A description"})

        assert result == {"title": "Greek Title", "body_html": "Greek Body"}
        mock_llm.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_json_parse_failure_falls_back_to_individual(self):
        """If JSON parsing fails, should fall back to translating one by one."""
        from app.agents.translation_checker import _translate_batch

        with patch("app.agents.translation_checker.llm_client") as mock_llm:
            mock_llm.generate = AsyncMock(
                side_effect=[
                    "not valid json at all",  # First call: batch fails
                    "Greek Title",  # Fallback: title
                    "Greek Body",  # Fallback: body_html
                ]
            )

            result = await _translate_batch({"title": "Cool Tee", "body_html": "A description"})

        assert result == {"title": "Greek Title", "body_html": "Greek Body"}
        assert mock_llm.generate.call_count == 3

    @pytest.mark.asyncio
    async def test_multi_field_json_without_code_fences(self):
        """JSON response without code fences should also parse."""
        from app.agents.translation_checker import _translate_batch

        with patch("app.agents.translation_checker.llm_client") as mock_llm:
            mock_llm.generate = AsyncMock(
                return_value='{"title": "Greek Title", "body_html": "Greek Body"}'
            )

            result = await _translate_batch({"title": "Cool", "body_html": "Desc"})

        assert result == {"title": "Greek Title", "body_html": "Greek Body"}


class TestCheckAndFixTranslations:
    @pytest.mark.asyncio
    async def test_full_flow_translates_and_registers(self):
        """Test the full flow: enable locale, find untranslated, translate, register."""
        with (
            patch("app.agents.translation_checker.ensure_locale_enabled", new_callable=AsyncMock, return_value=True),
            patch("app.agents.translation_checker.find_untranslated", new_callable=AsyncMock) as mock_find,
            patch("app.agents.translation_checker.register_translations", new_callable=AsyncMock) as mock_register,
            patch("app.agents.translation_checker._translate_batch", new_callable=AsyncMock) as mock_translate,
            patch("app.agents.translation_checker.send_agent_email", new_callable=AsyncMock),
        ):
            mock_find.return_value = [
                {
                    "resource_id": "gid://shopify/Product/123",
                    "resource_type": "PRODUCT",
                    "fields": [
                        {"key": "title", "value": "Cool Tee", "digest": "abc"},
                    ],
                }
            ]
            mock_translate.return_value = {"title": "Greek Tee"}
            mock_register.return_value = {
                "translations": [{"key": "title", "value": "Greek Tee"}],
                "userErrors": [],
            }

            from app.agents.translation_checker import _check_and_fix_impl
            result = await _check_and_fix_impl()

        assert result["translated"] == 1
        assert result["errors"] == 0
        mock_register.assert_called_once()
        # Verify translation input to register
        call_args = mock_register.call_args
        assert call_args[0][0] == "gid://shopify/Product/123"
        translations = call_args[0][1]
        assert translations[0]["key"] == "title"
        assert translations[0]["value"] == "Greek Tee"
        assert translations[0]["locale"] == "el"
        assert translations[0]["translatableContentDigest"] == "abc"

    @pytest.mark.asyncio
    async def test_returns_early_when_locale_fails(self):
        """If locale enabling fails, should return error immediately."""
        with patch("app.agents.translation_checker.ensure_locale_enabled", new_callable=AsyncMock, return_value=False):
            from app.agents.translation_checker import _check_and_fix_impl
            result = await _check_and_fix_impl()

        assert "error" in result

    @pytest.mark.asyncio
    async def test_nothing_to_translate(self):
        """When everything is already translated, should report 0."""
        with (
            patch("app.agents.translation_checker.ensure_locale_enabled", new_callable=AsyncMock, return_value=True),
            patch("app.agents.translation_checker.find_untranslated", new_callable=AsyncMock, return_value=[]),
        ):
            from app.agents.translation_checker import _check_and_fix_impl
            result = await _check_and_fix_impl()

        assert result == {"translated": 0, "errors": 0}

    @pytest.mark.asyncio
    async def test_translation_failure_counted_as_error(self):
        """If _translate_batch raises, error_count should increase."""
        with (
            patch("app.agents.translation_checker.ensure_locale_enabled", new_callable=AsyncMock, return_value=True),
            patch("app.agents.translation_checker.find_untranslated", new_callable=AsyncMock) as mock_find,
            patch("app.agents.translation_checker._translate_batch", new_callable=AsyncMock, side_effect=Exception("API down")),
            patch("app.agents.translation_checker.send_agent_email", new_callable=AsyncMock),
        ):
            mock_find.return_value = [
                {
                    "resource_id": "gid://shopify/Product/123",
                    "resource_type": "PRODUCT",
                    "fields": [{"key": "title", "value": "Test", "digest": "abc"}],
                }
            ]

            from app.agents.translation_checker import _check_and_fix_impl
            result = await _check_and_fix_impl()

        assert result["errors"] == 1
        assert result["translated"] == 0

    @pytest.mark.asyncio
    async def test_missing_translation_key_counted_as_error(self):
        """If _translate_batch returns without a key, that should be an error."""
        with (
            patch("app.agents.translation_checker.ensure_locale_enabled", new_callable=AsyncMock, return_value=True),
            patch("app.agents.translation_checker.find_untranslated", new_callable=AsyncMock) as mock_find,
            patch("app.agents.translation_checker.register_translations", new_callable=AsyncMock) as mock_reg,
            patch("app.agents.translation_checker._translate_batch", new_callable=AsyncMock) as mock_translate,
            patch("app.agents.translation_checker.send_agent_email", new_callable=AsyncMock),
        ):
            mock_find.return_value = [
                {
                    "resource_id": "gid://shopify/Product/123",
                    "resource_type": "PRODUCT",
                    "fields": [
                        {"key": "title", "value": "Test", "digest": "abc"},
                        {"key": "body_html", "value": "Body", "digest": "def"},
                    ],
                }
            ]
            # Only returns title, missing body_html
            mock_translate.return_value = {"title": "Greek Test"}
            mock_reg.return_value = {
                "translations": [{"key": "title", "value": "Greek Test"}],
                "userErrors": [],
            }

            from app.agents.translation_checker import _check_and_fix_impl
            result = await _check_and_fix_impl()

        assert result["errors"] == 1  # body_html missing
        assert result["translated"] == 1
