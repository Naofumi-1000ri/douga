"""Tests for src.utils.field_encryption — AES-GCM field-level encryption."""

from __future__ import annotations

import base64

import pytest

from src.utils.field_encryption import (
    _CIPHERTEXT_PREFIX,
    _reset_cache,
    decrypt_field,
    encrypt_field,
    is_encrypted,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_KEY = base64.b64encode(b"A" * 32).decode()  # 32 bytes → valid AES-256 key


def _set_key(monkeypatch: pytest.MonkeyPatch, key: str | None = _VALID_KEY) -> None:
    """Set (or clear) the encryption key env-var and reset the in-memory cache."""
    _reset_cache()
    if key is None:
        monkeypatch.delenv("AI_KEY_ENCRYPTION_KEY", raising=False)
    else:
        monkeypatch.setenv("AI_KEY_ENCRYPTION_KEY", key)


# ---------------------------------------------------------------------------
# encrypt_field
# ---------------------------------------------------------------------------


class TestEncryptField:
    def test_returns_enc_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch)
        result = encrypt_field("sk-test-1234")
        assert result is not None
        assert result.startswith(_CIPHERTEXT_PREFIX)

    def test_none_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch)
        assert encrypt_field(None) is None

    def test_empty_string_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch)
        assert encrypt_field("") == ""

    def test_already_encrypted_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch)
        first = encrypt_field("sk-test-1234")
        second = encrypt_field(first)
        assert first == second  # No double-encryption.

    def test_each_call_produces_different_ciphertext(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AES-GCM uses a random nonce so identical plaintext yields different ciphertext."""
        _set_key(monkeypatch)
        ct1 = encrypt_field("same-key")
        ct2 = encrypt_field("same-key")
        assert ct1 != ct2

    def test_key_not_set_returns_plaintext(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch, None)
        plaintext = "sk-plaintext-fallback"
        result = encrypt_field(plaintext)
        assert result == plaintext

    def test_invalid_key_length_returns_plaintext(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A key that decodes to wrong byte-length should be rejected → fallback to plaintext."""
        _set_key(monkeypatch, base64.b64encode(b"short").decode())
        plaintext = "sk-test"
        assert encrypt_field(plaintext) == plaintext

    def test_invalid_key_error_logged_only_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An invalid key must log ERROR only on the first call, not per-request."""
        _set_key(monkeypatch, base64.b64encode(b"short").decode())
        with caplog.at_level("ERROR", logger="src.utils.field_encryption"):
            encrypt_field("sk-first")
            encrypt_field("sk-second")
            encrypt_field("sk-third")
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 1

    def test_unset_key_warning_logged_only_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Missing key must log WARNING only on the first call."""
        _set_key(monkeypatch, None)
        with caplog.at_level("WARNING", logger="src.utils.field_encryption"):
            encrypt_field("sk-first")
            encrypt_field("sk-second")
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# decrypt_field
# ---------------------------------------------------------------------------


class TestDecryptField:
    def test_roundtrip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch)
        original = "sk-openai-super-secret-key"
        ciphertext = encrypt_field(original)
        assert decrypt_field(ciphertext) == original

    def test_plaintext_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Legacy plaintext values must pass through decrypt_field unchanged."""
        _set_key(monkeypatch)
        plaintext = "legacy-plaintext-key"
        assert decrypt_field(plaintext) == plaintext

    def test_none_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch)
        assert decrypt_field(None) is None

    def test_empty_string_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch)
        assert decrypt_field("") == ""

    def test_key_not_set_returns_none_for_ciphertext(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If key is unavailable and value is ciphertext, return None (fail safe)."""
        _set_key(monkeypatch)
        ciphertext = encrypt_field("sk-some-key")
        # Now remove the key.
        _reset_cache()
        monkeypatch.delenv("AI_KEY_ENCRYPTION_KEY", raising=False)
        assert decrypt_field(ciphertext) is None

    def test_key_not_set_plaintext_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without a key, plaintext legacy values must still be returned (transparent migration)."""
        _set_key(monkeypatch, None)
        assert decrypt_field("legacy-no-key") == "legacy-no-key"

    def test_corrupted_ciphertext_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch)
        bad = f"{_CIPHERTEXT_PREFIX}not-valid-base64!!!"
        assert decrypt_field(bad) is None


# ---------------------------------------------------------------------------
# is_encrypted
# ---------------------------------------------------------------------------


class TestIsEncrypted:
    def test_encrypted_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch)
        ct = encrypt_field("some-key")
        assert is_encrypted(ct) is True

    def test_plaintext_is_not_encrypted(self) -> None:
        assert is_encrypted("sk-plain") is False

    def test_none_is_not_encrypted(self) -> None:
        assert is_encrypted(None) is False

    def test_empty_is_not_encrypted(self) -> None:
        assert is_encrypted("") is False


# ---------------------------------------------------------------------------
# Transparent migration (plaintext→ciphertext on next write)
# ---------------------------------------------------------------------------


class TestTransparentMigration:
    def test_legacy_plaintext_read_then_reencrypt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Simulates: read a legacy plaintext value, write it back as ciphertext."""
        _set_key(monkeypatch)
        legacy = "sk-old-openai-key"

        # Step 1: read — should come back unchanged (transparent migration)
        plaintext_read = decrypt_field(legacy)
        assert plaintext_read == legacy

        # Step 2: write — re-encrypt before persisting
        new_db_value = encrypt_field(plaintext_read)
        assert is_encrypted(new_db_value)

        # Step 3: read again from the new encrypted value
        final_key = decrypt_field(new_db_value)
        assert final_key == legacy
