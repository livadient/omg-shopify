"""Tests for app/agents/ranking_advisor.py — ranking report logic."""
import json

import pytest

from app.agents.ranking_advisor import (
    MARKET_ROTATION,
    _build_sources_section_html,
    _format_trends_data,
    _load_history,
    _save_history,
)


class TestMarketRotation:
    def test_monday_is_cy(self):
        assert MARKET_ROTATION[0] == ("CY", "Cyprus")

    def test_tuesday_is_gr(self):
        assert MARKET_ROTATION[1] == ("GR", "Greece")

    def test_wednesday_is_cy(self):
        assert MARKET_ROTATION[2] == ("CY", "Cyprus")

    def test_thursday_is_gr(self):
        assert MARKET_ROTATION[3] == ("GR", "Greece")

    def test_friday_is_eu(self):
        assert MARKET_ROTATION[4] == ("EU", "Europe")

    def test_all_weekdays_covered(self):
        for day in range(5):
            assert day in MARKET_ROTATION


class TestHistoryFileIO:
    @pytest.fixture(autouse=True)
    def use_tmp_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.agents.ranking_advisor.DATA_DIR", tmp_path)
        monkeypatch.setattr("app.agents.ranking_advisor.HISTORY_FILE", tmp_path / "ranking_history.json")

    def test_load_empty_when_no_file(self):
        assert _load_history() == []

    def test_save_and_load(self):
        data = [{"market_focus": "CY", "date": "2026-01-01"}]
        _save_history(data)
        loaded = _load_history()
        assert len(loaded) == 1
        assert loaded[0]["market_focus"] == "CY"

    def test_save_truncates_to_60(self):
        data = [{"i": i} for i in range(100)]
        _save_history(data)
        loaded = _load_history()
        assert len(loaded) == 60
        # Should keep the last 60
        assert loaded[0]["i"] == 40

    def test_save_creates_data_dir(self, tmp_path, monkeypatch):
        subdir = tmp_path / "newdir"
        monkeypatch.setattr("app.agents.ranking_advisor.DATA_DIR", subdir)
        monkeypatch.setattr("app.agents.ranking_advisor.HISTORY_FILE", subdir / "history.json")
        _save_history([{"test": True}])
        assert (subdir / "history.json").exists()


class TestTrendsCrossReference:
    """Trends rising queries should be flagged as NOISE/MODEST/REAL once
    cross-referenced against verified Keyword Planner volume."""

    def test_low_volume_marked_as_noise_in_prompt(self):
        related = {
            "rising": [
                {
                    "query": "summer graphic tees",
                    "value": "850",
                    "seed": "graphic tee",
                    "verified_volume": 30,
                    "verified_cpc_eur": "0.10-0.50",
                    "verified_competition": "LOW",
                },
            ]
        }
        out = _format_trends_data(None, related)
        assert "Keyword Planner verified: 30 searches/mo" in out
        assert "LOW-BASE NOISE" in out

    def test_real_volume_marked_in_prompt(self):
        related = {
            "rising": [
                {
                    "query": "funny dad shirt",
                    "value": "120",
                    "seed": "funny t-shirt",
                    "verified_volume": 1200,
                    "verified_cpc_eur": "0.20-0.80",
                    "verified_competition": "MEDIUM",
                },
            ]
        }
        out = _format_trends_data(None, related)
        assert "1200 searches/mo" in out
        assert "verified real volume" in out

    def test_unverified_query_marked_as_unverified(self):
        related = {
            "rising": [
                {"query": "x", "value": "500", "seed": "y"},
            ]
        }
        out = _format_trends_data(None, related)
        assert "Keyword Planner verification unavailable" in out


class TestSourcesSection:
    """The 'How I sourced today's intel' email block must explain every source."""

    def test_lists_all_six_sources(self):
        html = _build_sources_section_html(None, None, None, None)
        for label in [
            "Google Search Console",
            "Google Ads Keyword Planner",
            "Google Trends",
            "Trends ↔ Keyword Planner cross-reference",
            "Shopify Admin API",
            "Internal report history",
        ]:
            assert label in html

    def test_cross_reference_count_reflects_verified_queries(self):
        related = {
            "rising": [
                {"query": "a", "value": "100", "seed": "x", "verified_volume": 500},
                {"query": "b", "value": "200", "seed": "x"},
                {"query": "c", "value": "300", "seed": "x", "verified_volume": 30},
            ]
        }
        html = _build_sources_section_html(None, None, None, related)
        # 2 of the 3 rising queries have verified_volume
        assert "Verified 2 rising queries" in html

    def test_caveat_about_rising_percentage(self):
        html = _build_sources_section_html(None, None, ["x"], None)
        # The Trends caveat must be present so the user understands what 'rising' means
        assert "relative growth" in html
        assert "NOT absolute volume" in html
