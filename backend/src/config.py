import json
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Weak default secret that must never reach production
_WEAK_DEFAULT_SECRET = "dev-edit-token-secret"

# Known weak / sample secrets rejected in production even when long enough.
# Includes the .env.example placeholder so a copy-pasted sample cannot ship.
_WEAK_SECRETS = frozenset(
    {
        _WEAK_DEFAULT_SECRET,
        "change-me-in-production-use-at-least-32-chars",  # .env.example placeholder
    }
)

# Minimum secret length enforced in production
_MIN_SECRET_LENGTH = 32

# Default CORS origins used when CORS_ORIGINS is unset or empty.
# Includes production Firebase Hosting origins so that Cloud Run deployments
# without CORS_ORIGINS continue to work unchanged.
_DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,http://localhost:5174,http://localhost:3000,"
    "https://douga-2f6f8.web.app,https://douga-2f6f8.firebaseapp.com"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    app_name: str = "Douga API"
    app_version: str = "0.1.0"
    git_hash: str = "unknown"  # Set via GIT_HASH env var at build time
    environment: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = False

    # Database (Cloud SQL)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/douga"
    database_echo: bool = False

    # Google Cloud Storage
    gcs_bucket_name: str = "douga-assets"
    gcs_project_id: str = ""

    # Local storage for development (when GCS is not configured)
    use_local_storage: bool = False  # Set USE_LOCAL_STORAGE=true in local .env
    local_storage_path: str = "/tmp/douga-storage"

    # Firebase
    firebase_project_id: str = ""

    # AI API Keys (for Whisper transcription and AI chat assistant)
    openai_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""

    # Default AI provider for chat assistant
    default_ai_provider: Literal["openai", "gemini", "anthropic"] = "openai"

    # CORS - stored as string, parsed via computed property.
    # Controlled via the CORS_ORIGINS env var (CORS_ORIGINS_RAW is also
    # accepted for backwards compatibility; CORS_ORIGINS wins if both are set).
    # When unset or empty, falls back to _DEFAULT_CORS_ORIGINS which includes
    # the production Firebase Hosting origins, so Cloud Run deployments
    # without CORS_ORIGINS continue to work.
    cors_origins_raw: str = Field(
        default=_DEFAULT_CORS_ORIGINS,
        validation_alias=AliasChoices("cors_origins", "cors_origins_raw"),
    )

    @computed_field
    @property
    def cors_origins(self) -> list[str]:
        """Parse CORS origins from pipe/comma-separated string or JSON array.

        If the CORS_ORIGINS environment variable is set to a non-empty value,
        only those origins are returned (no implicit extras).  When the
        variable is unset or empty (e.g. ``CORS_ORIGINS=`` in docker-compose),
        the default — which contains the production Firebase Hosting origins —
        is used, preserving the pre-change behaviour.
        """
        v = self.cors_origins_raw.strip()
        if not v:
            # Empty CORS_ORIGINS must not result in an empty allowlist
            v = _DEFAULT_CORS_ORIGINS
        # Try JSON first
        if v.startswith("["):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                pass
        # Try pipe-separated (for Cloud Run compatibility)
        if "|" in v:
            return [origin.strip() for origin in v.split("|") if origin.strip()]
        # Fall back to comma-separated
        return [origin.strip() for origin in v.split(",") if origin.strip()]

    # File Upload
    max_upload_size_mb: int = 500
    allowed_audio_types: list[str] = ["audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp3"]
    allowed_video_types: list[str] = ["video/mp4", "video/quicktime", "video/x-msvideo"]
    allowed_image_types: list[str] = ["image/png", "image/jpeg", "image/gif"]

    # FFmpeg
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    # Render settings
    render_output_width: int = 1920
    render_output_height: int = 1080
    render_fps: int = 30
    render_video_bitrate: str = "10M"
    render_audio_bitrate: str = "320k"
    render_audio_sample_rate: int = 48000

    # Render memory management (OOM prevention)
    # Maximum memory budget for a single render (in bytes). 0 = auto-detect from cgroup.
    render_max_memory_bytes: int = 0
    # Safety margin: reject render if estimated memory exceeds this fraction of limit
    render_memory_safety_ratio: float = 0.80
    # Chunk duration (in seconds) for chunked rendering when memory is tight
    render_chunk_duration_s: int = 120
    # Maximum threads for server-side FFmpeg compositing (limits per-thread buffer memory)
    render_ffmpeg_threads: int = 2
    # Maximum muxing queue size (limits FFmpeg muxer memory)
    render_ffmpeg_max_muxing_queue: int = 1024
    # H.264 encoding preset (fixed to avoid non-deterministic output near the 180 s boundary).
    # "fast" is the default; compatible with the COMPOSITE 1500 s Cloud Run timeout (#268).
    # Override via RENDER_FFMPEG_PRESET env var if you need higher quality (e.g. "medium").
    render_ffmpeg_preset: str = "fast"

    # Development/Testing - DEV_USER bypasses Firebase auth
    dev_mode: bool = False  # Set DEV_MODE=true in local .env to bypass auth
    dev_user_email: str = "dev@example.com"
    dev_user_name: str = "開発ユーザー"
    dev_user_id: str = "dev-user-123"

    # Edit session token (HMAC signing key for X-Edit-Session tokens).
    # Must be overridden in production via EDIT_TOKEN_SECRET env var.
    edit_token_secret: str = _WEAK_DEFAULT_SECRET

    @model_validator(mode="after")
    def _validate_production_safety(self) -> "Settings":
        """Refuse to start with unsafe settings in production."""
        if self.environment != "production":
            return self

        errors: list[str] = []

        if self.debug:
            errors.append("DEBUG must be False in production")

        # DEV_MODE=true bypasses ALL authentication (deps.py returns the dev user
        # when no Bearer token is supplied). A production deployment with this
        # flag set would expose every endpoint unauthenticated — refuse to start
        # (#154 / #261 review finding B).
        if self.dev_mode:
            errors.append("DEV_MODE must be False in production")

        secret = self.edit_token_secret
        if not secret or secret in _WEAK_SECRETS or len(secret) < _MIN_SECRET_LENGTH:
            errors.append(
                f"EDIT_TOKEN_SECRET must be set to a random value of at least "
                f"{_MIN_SECRET_LENGTH} characters in production "
                f"(known sample/dev values are rejected; "
                f"current: {'<empty>' if not secret else repr(secret[:4] + '...')})"
            )

        if errors:
            raise ValueError(
                "Unsafe configuration detected for production environment:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
