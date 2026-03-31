"""Authentication helpers for various marketing APIs."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta

import requests

from src.config import get_settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Naver Search Ad HMAC-SHA256 Signature
# ──────────────────────────────────────────────

def generate_naver_sa_signature(timestamp: str, method: str, uri: str) -> str:
    """Generate HMAC-SHA256 signature for Naver Search Ad API.

    Args:
        timestamp: Unix timestamp in milliseconds (string).
        method: HTTP method (GET, POST, etc.).
        uri: API endpoint path (e.g., /ncc/campaigns).

    Returns:
        Base64-encoded HMAC-SHA256 signature.
    """
    import base64

    settings = get_settings()
    secret_key = settings.naver_sa.naver_sa_secret_key

    message = f"{timestamp}.{method}.{uri}"
    signature = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    return base64.b64encode(signature).decode("utf-8")


def get_naver_sa_headers(method: str, uri: str, customer_id: str) -> dict[str, str]:
    """Build complete headers for Naver Search Ad API request."""
    settings = get_settings()
    timestamp = str(int(time.time() * 1000))
    signature = generate_naver_sa_signature(timestamp, method, uri)

    return {
        "X-Timestamp": timestamp,
        "X-API-KEY": settings.naver_sa.naver_sa_api_key,
        "X-Customer": customer_id,
        "X-Signature": signature,
        "Content-Type": "application/json; charset=UTF-8",
    }


# ──────────────────────────────────────────────
# Meta Access Token Refresh
# ──────────────────────────────────────────────

def refresh_meta_long_lived_token(current_token: str) -> str | None:
    """Exchange a valid token for a new long-lived token (60 days).

    Should be called before the current token expires.
    Returns the new token string, or None on failure.
    """
    settings = get_settings()
    url = "https://graph.facebook.com/v22.0/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": settings.meta.meta_app_id,
        "client_secret": settings.meta.meta_app_secret,
        "fb_exchange_token": current_token,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        new_token = data.get("access_token")
        expires_in = data.get("expires_in", 0)
        logger.info(f"Meta token refreshed. Expires in {expires_in // 86400} days.")
        return new_token
    except Exception as e:
        logger.error(f"Failed to refresh Meta token: {e}")
        return None


def check_meta_token_expiry(token: str) -> int | None:
    """Check remaining days until Meta token expires. Returns days or None on error."""
    url = "https://graph.facebook.com/debug_token"
    settings = get_settings()
    params = {
        "input_token": token,
        "access_token": f"{settings.meta.meta_app_id}|{settings.meta.meta_app_secret}",
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        expires_at = data.get("expires_at", 0)
        if expires_at == 0:
            return None  # Token never expires (system user)
        remaining = datetime.fromtimestamp(expires_at) - datetime.utcnow()
        return max(0, remaining.days)
    except Exception as e:
        logger.error(f"Failed to check Meta token expiry: {e}")
        return None
