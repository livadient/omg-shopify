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
