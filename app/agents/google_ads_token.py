"""Persistent storage for the Google Ads refresh token.

The OAuth consent screen is in Testing mode, so refresh tokens expire every 7
days. To avoid editing .env + restarting the container each time, we persist
the latest token in a file under data/ (mounted as a Docker volume) and let
the Google Ads clients read from it at request time.
"""
from pathlib import Path

from app.config import settings

TOKEN_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "google_ads_refresh_token.txt"


def get_refresh_token() -> str:
    """Return the current refresh token, preferring the on-disk override."""
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    return settings.google_ads_refresh_token


def save_refresh_token(token: str) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip() + "\n", encoding="utf-8")
