"""Tests for app/agents/ranking_advisor.py — ranking report logic."""
import json

import pytest

from app.agents.ranking_advisor import (
    MARKET_ROTATION,
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
