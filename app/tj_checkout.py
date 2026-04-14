"""TJ checkout session storage.

Replaces the broken /cart/VID:QTY?attributes[...] permalink. Saves the cart
payload (variant, qty, line item properties, shipping prefill) keyed by a
token so the /tj-checkout/{token} endpoint can rebuild the cart on TJ via
form POST to /cart/add (which preserves line item properties — the permalink
format does not).
"""
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SESSIONS_FILE = DATA_DIR / "tj_checkout_sessions.json"

_lock = threading.Lock()


def _load() -> dict:
    if not SESSIONS_FILE.exists():
        return {}
    try:
        return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(sessions: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2), encoding="utf-8")


def save_session(cart_data: dict, shipping: dict | None = None) -> str:
    """Store a cart payload and return a token for the redirect URL.

    The payload keeps each item's variant_id, quantity, and line item
    properties verbatim — that's what TJ needs to render the order with the
    correct design preview.
    """
    items = []
    for item in cart_data.get("items", []):
        props = {k: str(v) for k, v in (item.get("properties") or {}).items() if v}
        items.append({
            "variant_id": item.get("variant_id") or item.get("id"),
            "quantity": item.get("quantity", 1),
            "properties": props,
        })

    token = str(uuid.uuid4())[:16]
    with _lock:
        sessions = _load()
        sessions[token] = {
            "items": items,
            "shipping": shipping or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _save(sessions)
    return token


def get_session(token: str) -> dict | None:
    with _lock:
        return _load().get(token)
