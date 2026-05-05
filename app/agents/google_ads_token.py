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


def capture_rotated_token(client) -> bool:
    """If the GoogleAdsClient's underlying credentials picked up a new
    refresh_token (Google rotates them on Testing-mode tokens), persist
    it. Returns True if a new token was saved.

    This keeps the token chain alive across the 7-day Testing-mode
    expiry window: as long as Atlas runs at least once a week and one
    of those calls actually exchanges the access token, Google's
    response includes a fresh refresh_token, we save it, and the next
    run uses that one. The user only has to manually re-authorise via
    /google-ads/refresh-flow if access is fully revoked.
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        creds = getattr(client, "credentials", None)
        new_token = getattr(creds, "refresh_token", None) if creds else None
        current = get_refresh_token()
        if new_token and new_token != current:
            save_refresh_token(new_token)
            logger.info(
                f"Captured rotated Google Ads refresh token "
                f"({current[:8]}... → {new_token[:8]}...)"
            )
            return True
    except Exception as e:
        logger.warning(f"capture_rotated_token failed (non-blocking): {e}")
    return False
