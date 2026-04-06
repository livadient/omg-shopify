"""Tests for app/agents/approval.py — proposal storage and approval workflow."""
import json

import pytest

from app.agents.approval import (
    _load_proposals,
    _save_proposals,
    approval_url,
    create_proposal,
    get_proposal,
    list_proposals,
    update_status,
    validate_token,
)


@pytest.fixture(autouse=True)
def use_tmp_proposals(tmp_path, monkeypatch):
    """Redirect proposals storage to tmp_path for every test."""
    monkeypatch.setattr("app.agents.approval.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.agents.approval.PROPOSALS_FILE", tmp_path / "proposals.json")


class TestCreateProposal:
    def test_creates_unique_id_and_token(self):
        p1 = create_proposal("design", {"name": "Test 1"})
        p2 = create_proposal("design", {"name": "Test 2"})
        assert p1["id"] != p2["id"]
        assert p1["token"] != p2["token"]

    def test_has_expected_fields(self):
        p = create_proposal("blog", {"title": "My Blog"})
        assert p["agent"] == "blog"
        assert p["status"] == "pending"
        assert "created_at" in p
        assert p["data"]["title"] == "My Blog"

    def test_persisted_to_file(self, tmp_path):
        create_proposal("test", {"key": "value"})
        data = json.loads((tmp_path / "proposals.json").read_text())
        assert len(data) == 1


class TestGetProposal:
    def test_returns_correct_proposal(self):
        p = create_proposal("design", {"name": "X"})
        found = get_proposal(p["id"])
        assert found is not None
        assert found["id"] == p["id"]
        assert found["data"]["name"] == "X"

    def test_returns_none_for_missing_id(self):
        assert get_proposal("nonexistent") is None


class TestListProposals:
    def test_filter_by_agent(self):
        create_proposal("design", {})
        create_proposal("blog", {})
        create_proposal("design", {})
        assert len(list_proposals(agent="design")) == 2
        assert len(list_proposals(agent="blog")) == 1

    def test_filter_by_status(self):
        p = create_proposal("design", {})
        update_status(p["id"], "approved")
        create_proposal("design", {})
        assert len(list_proposals(status="pending")) == 1
        assert len(list_proposals(status="approved")) == 1

    def test_filter_by_agent_and_status(self):
        p1 = create_proposal("design", {})
        create_proposal("blog", {})
        update_status(p1["id"], "approved")
        assert len(list_proposals(agent="design", status="approved")) == 1
        assert len(list_proposals(agent="blog", status="approved")) == 0

    def test_no_filter_returns_all(self):
        create_proposal("a", {})
        create_proposal("b", {})
        assert len(list_proposals()) == 2


class TestValidateToken:
    def test_valid_token_pending(self):
        p = create_proposal("design", {})
        result = validate_token(p["id"], p["token"])
        assert result is not None
        assert result["id"] == p["id"]

    def test_wrong_token_returns_none(self):
        p = create_proposal("design", {})
        assert validate_token(p["id"], "wrong-token") is None

    def test_non_pending_returns_none(self):
        p = create_proposal("design", {})
        update_status(p["id"], "approved")
        assert validate_token(p["id"], p["token"]) is None

    def test_missing_id_returns_none(self):
        assert validate_token("missing", "any-token") is None


class TestUpdateStatus:
    def test_changes_status(self):
        p = create_proposal("design", {})
        updated = update_status(p["id"], "approved")
        assert updated["status"] == "approved"

    def test_adds_updated_at(self):
        p = create_proposal("design", {})
        updated = update_status(p["id"], "rejected")
        assert "updated_at" in updated

    def test_missing_id_returns_none(self):
        assert update_status("missing", "approved") is None

    def test_persisted(self):
        p = create_proposal("design", {})
        update_status(p["id"], "approved")
        reloaded = get_proposal(p["id"])
        assert reloaded["status"] == "approved"


class TestApprovalUrl:
    def test_builds_correct_url(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.server_base_url", "http://localhost:8080")
        p = create_proposal("design", {})
        url = approval_url(p["id"], p["token"], "approve")
        assert url == f"http://localhost:8080/agents/design/approve/{p['id']}?token={p['token']}"

    def test_reject_action(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.server_base_url", "http://localhost:8080")
        p = create_proposal("blog", {})
        url = approval_url(p["id"], p["token"], "reject")
        assert "/agents/blog/reject/" in url
