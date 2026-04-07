"""Tests for app/agents/image_client.py — image generation and text validation."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGenerateTextDesign:
    @pytest.mark.asyncio
    async def test_creates_rgba_image(self, tmp_path):
        """generate_text_design should produce an RGBA (transparent) image, not RGB."""
        with patch("app.agents.image_client.STATIC_DIR", tmp_path):
            proposals_dir = tmp_path / "proposals"
            proposals_dir.mkdir()

            from app.agents.image_client import generate_text_design
            result = await generate_text_design("HELLO WORLD", style="bold modern")

        assert result.exists()
        # Verify the image is RGBA (transparent background)
        from PIL import Image
        img = Image.open(result)
        assert img.mode == "RGBA"

    @pytest.mark.asyncio
    async def test_wraps_long_text(self, tmp_path):
        """Long single-line text should be wrapped into multiple lines."""
        with patch("app.agents.image_client.STATIC_DIR", tmp_path):
            proposals_dir = tmp_path / "proposals"
            proposals_dir.mkdir()

            from app.agents.image_client import generate_text_design
            result = await generate_text_design(
                "THIS IS A VERY LONG TEXT THAT SHOULD GET WRAPPED",
                style="bold",
            )

        assert result.exists()

    @pytest.mark.asyncio
    async def test_multiline_text(self, tmp_path):
        """Text with explicit newlines should produce multiple lines."""
        with patch("app.agents.image_client.STATIC_DIR", tmp_path):
            proposals_dir = tmp_path / "proposals"
            proposals_dir.mkdir()

            from app.agents.image_client import generate_text_design
            result = await generate_text_design("LINE ONE\nLINE TWO", style="bold")

        assert result.exists()


def _make_dummy_image(tmp_path):
    """Helper to create a small dummy PNG file."""
    dummy_image = tmp_path / "test.png"
    from PIL import Image
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    img.save(dummy_image, "PNG")
    return dummy_image


def _mock_claude_vision(response_text):
    """Helper to mock the Claude vision API call used by validate_design_text."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=response_text)]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    return mock_client


class TestValidateDesignText:
    @pytest.mark.asyncio
    async def test_valid_response(self, tmp_path):
        """When Claude says text is valid, result should have valid=True."""
        dummy_image = _make_dummy_image(tmp_path)
        mock_client = _mock_claude_vision('{"found_text": "HELLO", "valid": true, "errors": ""}')

        with patch("app.agents.llm_client._get_client", return_value=mock_client):
            from app.agents.image_client import validate_design_text
            result = await validate_design_text(dummy_image, "HELLO")

        assert result["valid"] is True
        assert result["found_text"] == "HELLO"
        assert result["errors"] == ""

    @pytest.mark.asyncio
    async def test_invalid_response(self, tmp_path):
        """When Claude finds text errors, result should have valid=False."""
        dummy_image = _make_dummy_image(tmp_path)
        mock_client = _mock_claude_vision('{"found_text": "HELO", "valid": false, "errors": "Missing letter L"}')

        with patch("app.agents.llm_client._get_client", return_value=mock_client):
            from app.agents.image_client import validate_design_text
            result = await validate_design_text(dummy_image, "HELLO")

        assert result["valid"] is False
        assert "Missing letter" in result["errors"]

    @pytest.mark.asyncio
    async def test_json_parse_failure_returns_valid(self, tmp_path):
        """When Claude response can't be parsed as JSON, should default to valid=True."""
        dummy_image = _make_dummy_image(tmp_path)
        mock_client = _mock_claude_vision("This is not JSON at all")

        with patch("app.agents.llm_client._get_client", return_value=mock_client):
            from app.agents.image_client import validate_design_text
            result = await validate_design_text(dummy_image, "HELLO")

        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_json_in_code_fences_parsed(self, tmp_path):
        """Claude response wrapped in ```json fences should be parsed correctly."""
        dummy_image = _make_dummy_image(tmp_path)
        mock_client = _mock_claude_vision('```json\n{"found_text": "OK", "valid": true, "errors": ""}\n```')

        with patch("app.agents.llm_client._get_client", return_value=mock_client):
            from app.agents.image_client import validate_design_text
            result = await validate_design_text(dummy_image, "OK")

        assert result["valid"] is True


class TestGenerateDesignWithTextCheck:
    @pytest.mark.asyncio
    async def test_empty_intended_text_skips_validation(self):
        """When intended_text is empty, should skip validation and just generate."""
        mock_path = Path("/fake/design.png")

        with patch("app.agents.image_client.generate_design", new_callable=AsyncMock, return_value=mock_path) as mock_gen:
            from app.agents.image_client import generate_design_with_text_check
            result = await generate_design_with_text_check(
                concept="A cool design",
                intended_text="",
            )

        assert result == mock_path
        mock_gen.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_on_first_try(self):
        """When text validation passes on first attempt, should return immediately."""
        mock_path = Path("/fake/design.png")

        with (
            patch("app.agents.image_client.generate_design", new_callable=AsyncMock, return_value=mock_path),
            patch("app.agents.image_client.validate_design_text", new_callable=AsyncMock) as mock_validate,
        ):
            mock_validate.return_value = {"valid": True, "found_text": "HELLO", "errors": ""}

            from app.agents.image_client import generate_design_with_text_check
            result = await generate_design_with_text_check(
                concept="A design with text",
                intended_text="HELLO",
                max_retries=3,
            )

        assert result == mock_path
        mock_validate.assert_called_once()

    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        """When text validation fails, should retry up to max_retries."""
        path1 = Path("/fake/design1.png")
        path2 = Path("/fake/design2.png")

        with (
            patch("app.agents.image_client.generate_design", new_callable=AsyncMock, side_effect=[path1, path2]),
            patch("app.agents.image_client.validate_design_text", new_callable=AsyncMock) as mock_validate,
        ):
            mock_validate.side_effect = [
                {"valid": False, "found_text": "HELO", "errors": "Missing letter"},
                {"valid": True, "found_text": "HELLO", "errors": ""},
            ]

            from app.agents.image_client import generate_design_with_text_check
            result = await generate_design_with_text_check(
                concept="A design with text",
                intended_text="HELLO",
                max_retries=2,
            )

        assert result == path2
        assert mock_validate.call_count == 2

    @pytest.mark.asyncio
    async def test_uses_last_image_after_all_retries_fail(self):
        """After exhausting retries, should return the last generated image."""
        path1 = Path("/fake/design1.png")
        path2 = Path("/fake/design2.png")

        with (
            patch("app.agents.image_client.generate_design", new_callable=AsyncMock, side_effect=[path1, path2]),
            patch("app.agents.image_client.validate_design_text", new_callable=AsyncMock) as mock_validate,
        ):
            mock_validate.return_value = {"valid": False, "found_text": "WRONG", "errors": "Bad text"}

            from app.agents.image_client import generate_design_with_text_check
            result = await generate_design_with_text_check(
                concept="A design with text",
                intended_text="HELLO",
                max_retries=2,
            )

        assert result == path2
        assert mock_validate.call_count == 2
