"""Stateless HMAC-based edit session token.

Encodes project_id + sequence_id + user_id into a signed token so that
downstream APIs can resolve the active editing context without DB lookup.
"""

import base64
import hashlib
import hmac
import json
import time
from uuid import UUID

_TOKEN_VERSION = 1
_TOKEN_MAX_AGE = 24 * 3600  # 24 hours


def create_edit_token(
    project_id: UUID,
    sequence_id: UUID,
    user_id: UUID,
    secret: str,
) -> str:
    """Create a signed edit-session token (Base64)."""
    payload = {
        "v": _TOKEN_VERSION,
        "pid": str(project_id),
        "sid": str(sequence_id),
        "uid": str(user_id),
        "iat": int(time.time()),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    sig = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).digest()
    token_bytes = payload_bytes + b"." + base64.urlsafe_b64encode(sig)
    return base64.urlsafe_b64encode(token_bytes).decode()


def decode_edit_token(
    token: str,
    secret: str,
) -> dict:
    """Decode and verify a signed edit-session token.

    Returns dict with keys: pid, sid, uid (all as str).
    Raises ValueError on invalid/expired token.
    """
    try:
        token_bytes = base64.urlsafe_b64decode(token)
    except Exception:
        raise ValueError("Invalid token encoding")

    parts = token_bytes.rsplit(b".", 1)
    if len(parts) != 2:
        raise ValueError("Invalid token format")

    payload_bytes, sig_b64 = parts
    try:
        sig = base64.urlsafe_b64decode(sig_b64)
    except Exception:
        raise ValueError("Invalid token signature encoding")

    expected_sig = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("Invalid token signature")

    try:
        payload = json.loads(payload_bytes)
    except Exception:
        raise ValueError("Invalid token payload")

    if payload.get("v") != _TOKEN_VERSION:
        raise ValueError("Unsupported token version")

    iat = payload.get("iat", 0)
    if time.time() - iat > _TOKEN_MAX_AGE:
        raise ValueError("Token expired")

    return {
        "pid": payload["pid"],
        "sid": payload["sid"],
        "uid": payload["uid"],
    }
