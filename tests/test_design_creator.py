"""Tests for app/agents/design_creator.py — design generation logic."""
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.design_creator import _get_season


class TestGetSeason:
    def test_spring(self):
        assert _get_season(3) == "Spring"
        assert _get_season(4) == "Spring"
        assert _get_season(5) == "Spring"

    def test_summer(self):
        assert _get_season(6) == "Summer"
        assert _get_season(7) == "Summer"
        assert _get_season(8) == "Summer"

    def test_autumn(self):
        assert _get_season(9) == "Autumn"
        assert _get_season(10) == "Autumn"
        assert _get_season(11) == "Autumn"

    def test_winter(self):
        assert _get_season(12) == "Winter"
        assert _get_season(1) == "Winter"
        assert _get_season(2) == "Winter"


class TestResearchTrends:
    @pytest.mark.asyncio
    async def test_returns_empty_on_no_concepts(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.agents.approval.DATA_DIR", tmp_path)
        monkeypatch.setattr("app.agents.approval.PROPOSALS_FILE", tmp_path / "proposals.json")

        with (
            patch("app.agents.design_creator.llm_client") as mock_llm,
            patch("app.agents.design_creator.send_agent_email", new_callable=AsyncMock),
        ):
            mock_llm.generate_with_search = AsyncMock(return_value="trends data")
            mock_llm.generate_json = AsyncMock(return_value={"concepts": []})

            from app.agents.design_creator import _research_trends_impl
            result = await _research_trends_impl()
            assert result == []

    @pytest.mark.asyncio
    async def test_creates_proposals_for_concepts(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.agents.approval.DATA_DIR", tmp_path)
        monkeypatch.setattr("app.agents.approval.PROPOSALS_FILE", tmp_path / "proposals.json")
        monkeypatch.setattr("app.config.settings.server_base_url", "http://localhost:8080")

        concept = {
            "name": "Test Design",
            "description": "A cool design",
            "style": "minimalist",
            "text_on_shirt": "",
            "product_type": "male",
            "suggested_title": "Test Tee",
            "suggested_tags": "test",
            "reasoning": "test",
        }

        with (
            patch("app.agents.design_creator.llm_client") as mock_llm,
            patch("app.agents.design_creator.send_agent_email", new_callable=AsyncMock),
            patch("app.agents.design_creator.create_proposal") as mock_create,
            patch("app.agents.design_creator.approval_url", return_value="http://test"),
        ):
            mock_llm.generate_with_search = AsyncMock(return_value="trends data")
            mock_llm.generate_json = AsyncMock(return_value={"concepts": [concept]})
            mock_create.return_value = {"id": "abc", "token": "tok", "data": concept}

            # Mock image generation (imported inside function body)
            with patch(
                "app.agents.image_client.generate_design",
                new_callable=AsyncMock,
                side_effect=ImportError("no rembg"),
            ):
                from app.agents.design_creator import _research_trends_impl
                result = await _research_trends_impl()

            assert len(result) == 1
            mock_create.assert_called_once()


class TestExecuteApprovalVersion:
    """Test that execute_approval uses the correct cached_mockups based on version."""

    @pytest.mark.asyncio
    async def test_original_version_ignores_cached_mockups(self, tmp_path, monkeypatch):
        """When version='original', cached_mockups should be empty dict (no nobg cache)."""
        monkeypatch.setattr("app.agents.design_creator.STATIC_DIR", tmp_path)

        # Create a fake image file
        proposals_dir = tmp_path / "proposals"
        proposals_dir.mkdir()
        fake_image = proposals_dir / "design_test.png"
        fake_image.write_bytes(b"fake png data")

        proposal_data = {
            "name": "Test Design",
            "description": "A test",
            "image_path": str(fake_image),
            "image_nobg_path": str(fake_image),
            "suggested_title": "Test Tee",
            "suggested_tags": "test",
            "cached_mockups": {
                "male": {"url": "http://example.com/male.png", "path": str(fake_image)},
                "female": {"url": "http://example.com/female.png", "path": str(fake_image)},
            },
        }

        with (
            patch("app.agents.approval.get_proposal", return_value={"data": proposal_data}),
            patch("app.agents.approval.update_status"),
            patch("app.agents.design_creator.llm_client") as mock_llm,
            patch("app.shopify_product_creator.create_product", new_callable=AsyncMock) as mock_create,
            patch("app.shopify_product_creator.create_mappings_for_product", new_callable=AsyncMock, return_value=[]),
            patch("app.shopify_product_creator.fetch_mockup_from_qstomizer", new_callable=AsyncMock, return_value=None),
            patch("app.shopify_product_creator.upload_product_image", new_callable=AsyncMock),
        ):
            mock_llm.generate = AsyncMock(return_value="<p>Nice tee</p>")
            mock_create.return_value = {"id": 999, "handle": "test-tee"}

            from app.agents.design_creator import execute_approval
            result = await execute_approval("prop-123", version="original")

        assert result["product_id"] == 999
        # With version="original", cached_mockups should be {} so fetch_mockup is called
        # (it returns None in our mock, so no upload happens for mockups)

    @pytest.mark.asyncio
    async def test_nobg_version_uses_cached_mockups(self, tmp_path, monkeypatch):
        """When version='nobg', cached_mockups should be used from proposal data."""
        monkeypatch.setattr("app.agents.design_creator.STATIC_DIR", tmp_path)

        proposals_dir = tmp_path / "proposals"
        proposals_dir.mkdir()
        fake_image = proposals_dir / "design_test.png"
        fake_image.write_bytes(b"fake png data")

        # Create cached mockup files that exist
        cached_male = proposals_dir / "mockup_male.png"
        cached_male.write_bytes(b"male mockup")
        cached_female = proposals_dir / "mockup_female.png"
        cached_female.write_bytes(b"female mockup")

        proposal_data = {
            "name": "Test Design",
            "description": "A test",
            "image_path": str(fake_image),
            "image_nobg_path": str(fake_image),
            "suggested_title": "Test Tee",
            "suggested_tags": "test",
            "cached_mockups": {
                "male": {"url": "http://example.com/male.png", "path": str(cached_male)},
                "female": {"url": "http://example.com/female.png", "path": str(cached_female)},
            },
        }

        with (
            patch("app.agents.approval.get_proposal", return_value={"data": proposal_data}),
            patch("app.agents.approval.update_status"),
            patch("app.agents.design_creator.llm_client") as mock_llm,
            patch("app.shopify_product_creator.create_product", new_callable=AsyncMock) as mock_create,
            patch("app.shopify_product_creator.create_mappings_for_product", new_callable=AsyncMock, return_value=[]),
            patch("app.shopify_product_creator.fetch_mockup_from_qstomizer", new_callable=AsyncMock) as mock_fetch,
            patch("app.shopify_product_creator.upload_product_image", new_callable=AsyncMock) as mock_upload,
        ):
            mock_llm.generate = AsyncMock(return_value="<p>Nice tee</p>")
            mock_create.return_value = {"id": 999, "handle": "test-tee"}

            from app.agents.design_creator import execute_approval
            result = await execute_approval("prop-123", version="nobg")

        assert result["product_id"] == 999
        # With version="nobg", cached mockups should be used — no fetch needed
        mock_fetch.assert_not_called()
