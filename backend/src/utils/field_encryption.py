"""AES-GCM field-level encryption for sensitive database columns.

Usage
-----
Encrypt a value before writing to the DB:

    from src.utils.field_encryption import encrypt_field, decrypt_field

    ciphertext = encrypt_field(plaintext)   # returns "enc:v1:<base64>" or plaintext on key-not-set
    setattr(project, "ai_api_key", ciphertext)

Decrypt when reading:

    plaintext = decrypt_field(project.ai_api_key)

Transparent migration
---------------------
- ``decrypt_field`` accepts both ciphertext (prefixed with ``enc:v1:``) and
  legacy plaintext values.
- Legacy plaintext is returned as-is without touching the database.
- On the *next* write (e.g. project update) the value will be re-encrypted if
  ``AI_KEY_ENCRYPTION_KEY`` is set.

Key setup
---------
Generate a 32-byte key:

    openssl rand -base64 32

Set the environment variable:

    AI_KEY_ENCRYPTION_KEY=<base64-encoded-32-byte-key>

When the variable is absent the module logs a WARNING and skips encryption.
This keeps local-dev zero-config while ensuring production encryption is
enforced once the key is deployed.
"""

from __future__ import annotations

import base64
import logging
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_CIPHERTEXT_PREFIX = "enc:v1:"
_KEY_BYTES = 32  # AES-256
_NONCE_BYTES = 12  # GCM standard

# Cache the key in memory after the first successful load.
_cached_key: bytes | None = None
_key_warning_issued = False
_invalid_key_flagged = False


def _load_key() -> bytes | None:
    """Return the encryption key bytes, or ``None`` if the env var is unset.

    The key must be a 32-byte value encoded as standard base64.  Any errors
    during decoding are logged at ERROR level (once per process) and treated
    as "key not set".
    """
    global _cached_key, _key_warning_issued, _invalid_key_flagged  # noqa: PLW0603
    if _cached_key is not None:
        return _cached_key

    raw = os.environ.get("AI_KEY_ENCRYPTION_KEY", "").strip()
    if not raw:
        if not _key_warning_issued:
            logger.warning(
                "AI_KEY_ENCRYPTION_KEY is not set — ai_api_key will be stored as plaintext. "
                "Set this variable in production to enable AES-GCM field-level encryption."
            )
            _key_warning_issued = True
        return None

    try:
        key_bytes = base64.b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        if not _invalid_key_flagged:
            logger.error("AI_KEY_ENCRYPTION_KEY is not valid base64: %s", exc)
            _invalid_key_flagged = True
        return None

    if len(key_bytes) != _KEY_BYTES:
        if not _invalid_key_flagged:
            logger.error(
                "AI_KEY_ENCRYPTION_KEY must decode to exactly %d bytes, got %d",
                _KEY_BYTES,
                len(key_bytes),
            )
            _invalid_key_flagged = True
        return None

    _cached_key = key_bytes
    return _cached_key


def _reset_cache() -> None:
    """Reset the cached key and log-once flags.  Intended for tests only."""
    global _cached_key, _key_warning_issued, _invalid_key_flagged  # noqa: PLW0603
    _cached_key = None
    _key_warning_issued = False
    _invalid_key_flagged = False


def encrypt_field(plaintext: str | None) -> str | None:
    """Encrypt *plaintext* with AES-256-GCM.

    Returns a ``"enc:v1:<base64>"`` string on success.
    Returns *plaintext* unchanged when:
    - *plaintext* is ``None`` or empty.
    - The encryption key is not configured (logs a warning once).
    - *plaintext* already looks like a ciphertext (already encrypted).
    """
    if not plaintext:
        return plaintext

    # Idempotent: already encrypted → skip.
    if plaintext.startswith(_CIPHERTEXT_PREFIX):
        return plaintext

    key = _load_key()
    if key is None:
        return plaintext  # Encryption unavailable — store as plaintext.

    aesgcm = AESGCM(key)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)

    # Encode: [4-byte big-endian nonce-length][nonce][ciphertext]
    # Embedding the length makes future nonce-size changes backward-compatible.
    payload = struct.pack(">I", _NONCE_BYTES) + nonce + ciphertext
    encoded = base64.b64encode(payload).decode()
    return f"{_CIPHERTEXT_PREFIX}{encoded}"


def decrypt_field(value: str | None) -> str | None:
    """Decrypt *value* if it is an ``enc:v1:`` ciphertext, otherwise return as-is.

    Transparent migration contract:
    - Encrypted values are decrypted using the current key.
    - Plaintext values (legacy) are returned without modification.
    - ``None`` / empty values are returned as-is.
    """
    if not value:
        return value

    if not value.startswith(_CIPHERTEXT_PREFIX):
        # Legacy plaintext value — return as-is for transparent migration.
        return value

    key = _load_key()
    if key is None:
        logger.error(
            "Encrypted ai_api_key found in DB but AI_KEY_ENCRYPTION_KEY is not set. "
            "Cannot decrypt — returning None to avoid exposing garbage data."
        )
        return None

    encoded = value[len(_CIPHERTEXT_PREFIX) :]
    try:
        payload = base64.b64decode(encoded)
        nonce_len = struct.unpack(">I", payload[:4])[0]
        nonce = payload[4 : 4 + nonce_len]
        ciphertext = payload[4 + nonce_len :]
        aesgcm = AESGCM(key)
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext_bytes.decode()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to decrypt field (corrupted ciphertext?): %s", exc)
        return None


def is_encrypted(value: str | None) -> bool:
    """Return ``True`` if *value* is an ``enc:v1:`` ciphertext."""
    return bool(value and value.startswith(_CIPHERTEXT_PREFIX))
