"""Proposal storage and token-based approval workflow."""
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
PROPOSALS_FILE = DATA_DIR / "proposals.json"

# Serializes claim/status transitions to prevent race conditions when multiple
# approval requests for the same proposal arrive in parallel (e.g. user click +
# Gmail link prefetcher).
_claim_lock = threading.Lock()


def _load_proposals() -> list[dict]:
    if not PROPOSALS_FILE.exists():
        return []
    return json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))


def _save_proposals(proposals: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    PROPOSALS_FILE.write_text(
        json.dumps(proposals, indent=2, default=str), encoding="utf-8"
    )


def create_proposal(agent: str, data: dict) -> dict:
    """Create a new proposal with a unique ID and secret token."""
    proposal = {
        "id": str(uuid.uuid4())[:8],
        "token": str(uuid.uuid4()),
        "agent": agent,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    proposals = _load_proposals()
    proposals.append(proposal)
    _save_proposals(proposals)
    logger.info(f"Created {agent} proposal {proposal['id']}")
    return proposal


def get_proposal(proposal_id: str) -> dict | None:
    """Get a proposal by ID."""
    for p in _load_proposals():
        if p["id"] == proposal_id:
            return p
    return None


def list_proposals(agent: str | None = None, status: str | None = None) -> list[dict]:
    """List proposals, optionally filtered by agent and/or status."""
    proposals = _load_proposals()
    if agent:
        proposals = [p for p in proposals if p["agent"] == agent]
    if status:
        proposals = [p for p in proposals if p["status"] == status]
    return proposals


def validate_token(proposal_id: str, token: str) -> dict | None:
    """Validate a proposal token. Returns the proposal if pending and token matches."""
    proposal = get_proposal(proposal_id)
    if proposal and proposal["token"] == token and proposal["status"] == "pending":
        return proposal
    return None


def claim_proposal(proposal_id: str, token: str) -> dict | None:
    """Atomically validate token and transition pending → processing.

    Returns the proposal if the caller successfully claimed it. Returns None if
    the token is invalid, the proposal is missing, or another request already
    claimed it. The load/check/save is serialized via _claim_lock so concurrent
    callers cannot both observe `pending`.
    """
    with _claim_lock:
        proposals = _load_proposals()
        for p in proposals:
            if p["id"] != proposal_id:
                continue
            if p["token"] != token or p["status"] != "pending":
                return None
            p["status"] = "processing"
            p["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_proposals(proposals)
            logger.info(f"Proposal {proposal_id} claimed (status → processing)")
            return p
        return None


def update_status(proposal_id: str, status: str) -> dict | None:
    """Update a proposal's status (approved/rejected/pending)."""
    with _claim_lock:
        proposals = _load_proposals()
        for p in proposals:
            if p["id"] == proposal_id:
                p["status"] = status
                p["updated_at"] = datetime.now(timezone.utc).isoformat()
                _save_proposals(proposals)
                logger.info(f"Proposal {proposal_id} status → {status}")
                return p
        return None


def approval_url(proposal_id: str, token: str, action: str = "approve") -> str:
    """Build an approval/rejection URL for email links."""
    agent = get_proposal(proposal_id)["agent"]
    base = settings.server_base_url
    return f"{base}/agents/{agent}/{action}/{proposal_id}?token={token}"
