"""Unit tests for Settings validation (Issue #259).

Tests that:
- Safe defaults: debug=False, use_local_storage=False
- Production guard: weak EDIT_TOKEN_SECRET raises ValueError on startup
- Production guard: debug=True raises ValueError on startup
- Non-production environments pass through without restriction
- CORS: controlled via CORS_ORIGINS env var; unset/empty falls back to
  defaults that include the production origins
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import (
    _MIN_SECRET_LENGTH,
    _WEAK_DEFAULT_SECRET,
    _WEAK_SECRETS,
    Settings,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prod_settings(**overrides: object) -> Settings:
    """Build a Settings object for environment=production with given overrides."""
    base = {
        "environment": "production",
        "debug": False,
        "edit_token_secret": "a" * _MIN_SECRET_LENGTH,
    }
    base.update(overrides)
    return Settings.model_validate(base)


# ---------------------------------------------------------------------------
# Default value tests
# ---------------------------------------------------------------------------


def test_debug_default_is_false() -> None:
    """debug should default to False (secure default)."""
    s = Settings.model_validate({})
    assert s.debug is False


def test_use_local_storage_default_is_false() -> None:
    """use_local_storage should default to False (secure default)."""
    s = Settings.model_validate({})
    assert s.use_local_storage is False


def test_edit_token_secret_default_is_weak_sentinel() -> None:
    """edit_token_secret default is the known-weak dev sentinel value."""
    s = Settings.model_validate({})
    assert s.edit_token_secret == _WEAK_DEFAULT_SECRET


# ---------------------------------------------------------------------------
# Production guard: EDIT_TOKEN_SECRET
# ---------------------------------------------------------------------------


def test_production_with_strong_secret_passes() -> None:
    """A strong secret in production should not raise."""
    s = _prod_settings()
    assert s.environment == "production"
    assert len(s.edit_token_secret) >= _MIN_SECRET_LENGTH


def test_production_with_weak_default_secret_raises() -> None:
    """production + default (weak) EDIT_TOKEN_SECRET must raise on startup."""
    with pytest.raises(ValidationError, match="EDIT_TOKEN_SECRET"):
        Settings.model_validate(
            {
                "environment": "production",
                "debug": False,
                "edit_token_secret": _WEAK_DEFAULT_SECRET,
            }
        )


def test_production_with_empty_secret_raises() -> None:
    """production + empty EDIT_TOKEN_SECRET must raise on startup."""
    with pytest.raises(ValidationError, match="EDIT_TOKEN_SECRET"):
        Settings.model_validate(
            {
                "environment": "production",
                "debug": False,
                "edit_token_secret": "",
            }
        )


def test_production_with_short_secret_raises() -> None:
    """production + secret shorter than _MIN_SECRET_LENGTH must raise."""
    short_secret = "x" * (_MIN_SECRET_LENGTH - 1)
    with pytest.raises(ValidationError, match="EDIT_TOKEN_SECRET"):
        Settings.model_validate(
            {
                "environment": "production",
                "debug": False,
                "edit_token_secret": short_secret,
            }
        )


def test_production_with_exact_minimum_length_secret_passes() -> None:
    """production + secret of exactly _MIN_SECRET_LENGTH chars must pass."""
    exact_secret = "z" * _MIN_SECRET_LENGTH
    s = _prod_settings(edit_token_secret=exact_secret)
    assert s.edit_token_secret == exact_secret


@pytest.mark.parametrize("weak_secret", sorted(_WEAK_SECRETS))
def test_production_with_known_weak_secret_raises(weak_secret: str) -> None:
    """All known weak/sample secrets must be rejected in production,
    even when they satisfy the minimum length requirement
    (e.g. the .env.example placeholder is 32+ chars long)."""
    with pytest.raises(ValidationError, match="EDIT_TOKEN_SECRET"):
        Settings.model_validate(
            {
                "environment": "production",
                "debug": False,
                "edit_token_secret": weak_secret,
            }
        )


def test_env_example_placeholder_is_long_enough_but_still_rejected() -> None:
    """Guard regression test: the .env.example placeholder passes the length
    check, so it must be caught by the weak-secret list specifically."""
    placeholder = "change-me-in-production-use-at-least-32-chars"
    assert len(placeholder) >= _MIN_SECRET_LENGTH
    assert placeholder in _WEAK_SECRETS


# ---------------------------------------------------------------------------
# Production guard: debug flag
# ---------------------------------------------------------------------------


def test_production_with_debug_true_raises() -> None:
    """production + debug=True must raise on startup."""
    with pytest.raises(ValidationError, match="DEBUG"):
        _prod_settings(debug=True)


def test_production_with_debug_false_passes() -> None:
    """production + debug=False (default) must not raise."""
    s = _prod_settings(debug=False)
    assert s.debug is False


# ---------------------------------------------------------------------------
# Production guard: multiple errors reported together
# ---------------------------------------------------------------------------


def test_production_multiple_violations_reported_together() -> None:
    """When both debug=True and weak secret exist, both errors should be present."""
    with pytest.raises(ValidationError) as exc_info:
        Settings.model_validate(
            {
                "environment": "production",
                "debug": True,
                "edit_token_secret": _WEAK_DEFAULT_SECRET,
            }
        )
    message = str(exc_info.value)
    assert "DEBUG" in message
    assert "EDIT_TOKEN_SECRET" in message


# ---------------------------------------------------------------------------
# Non-production environments: no restrictions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["development", "staging", "test"])
def test_non_production_allows_weak_secret(env: str) -> None:
    """development/staging/test environments must not be blocked by weak secret."""
    s = Settings.model_validate(
        {
            "environment": env,
            "debug": True,
            "edit_token_secret": _WEAK_DEFAULT_SECRET,
        }
    )
    assert s.environment == env


# ---------------------------------------------------------------------------
# CORS behaviour
# ---------------------------------------------------------------------------


def test_cors_default_includes_production_origins() -> None:
    """Default CORS origins must include production Firebase Hosting URLs."""
    s = Settings.model_validate({})
    assert "https://douga-2f6f8.web.app" in s.cors_origins
    assert "https://douga-2f6f8.firebaseapp.com" in s.cors_origins


def test_cors_env_override_replaces_origins() -> None:
    """When CORS_ORIGINS is overridden, only those origins are returned."""
    s = Settings.model_validate({"cors_origins_raw": "https://example.com"})
    assert s.cors_origins == ["https://example.com"]
    # Production origins should NOT be added when an explicit override is set
    assert "https://douga-2f6f8.web.app" not in s.cors_origins


def test_cors_pipe_separated() -> None:
    """Pipe-separated CORS_ORIGINS (Cloud Run style) is parsed correctly."""
    s = Settings.model_validate(
        {"cors_origins_raw": "https://a.example.com|https://b.example.com"}
    )
    assert s.cors_origins == ["https://a.example.com", "https://b.example.com"]


def test_cors_json_array() -> None:
    """JSON array CORS_ORIGINS is parsed correctly."""
    s = Settings.model_validate(
        {"cors_origins_raw": '["https://a.example.com","https://b.example.com"]'}
    )
    assert s.cors_origins == ["https://a.example.com", "https://b.example.com"]


# ---------------------------------------------------------------------------
# CORS behaviour: real environment variables (operational contract)
# ---------------------------------------------------------------------------


def test_cors_origins_env_var_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CORS_ORIGINS env var (name used in docker-compose / env.yaml docs)
    must control the allowlist — not just CORS_ORIGINS_RAW."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CORS_ORIGINS", "https://only.example.com")
    s = Settings(_env_file=None)
    assert s.cors_origins == ["https://only.example.com"]


def test_cors_origins_env_var_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    """The JSON-array form used in docker-compose.yml must be honored."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:5173","http://localhost:3000"]')
    s = Settings(_env_file=None)
    assert s.cors_origins == ["http://localhost:5173", "http://localhost:3000"]


def test_cors_origins_raw_env_var_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backwards compatibility: CORS_ORIGINS_RAW continues to work."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setenv("CORS_ORIGINS_RAW", "https://raw.example.com")
    s = Settings(_env_file=None)
    assert s.cors_origins == ["https://raw.example.com"]


def test_cors_origins_env_var_wins_over_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both CORS_ORIGINS and CORS_ORIGINS_RAW are set, CORS_ORIGINS wins."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CORS_ORIGINS", "https://primary.example.com")
    monkeypatch.setenv("CORS_ORIGINS_RAW", "https://secondary.example.com")
    s = Settings(_env_file=None)
    assert s.cors_origins == ["https://primary.example.com"]


def test_cors_origins_env_var_empty_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CORS_ORIGINS= (empty string, as seen in docker-compose setups) must NOT
    produce an empty allowlist; it falls back to the default origins."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CORS_ORIGINS", "")
    s = Settings(_env_file=None)
    assert s.cors_origins != []
    assert "https://douga-2f6f8.web.app" in s.cors_origins
    assert "https://douga-2f6f8.firebaseapp.com" in s.cors_origins
    assert "http://localhost:5173" in s.cors_origins


def test_cors_origins_whitespace_only_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only CORS_ORIGINS is treated the same as empty."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CORS_ORIGINS", "   ")
    s = Settings(_env_file=None)
    assert "https://douga-2f6f8.web.app" in s.cors_origins
