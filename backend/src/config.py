import json
from functools import lru_cache
from typing import Literal

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    app_name: str = "Douga API"
    app_version: str = "0.1.0"
    git_hash: str = "unknown"  # Set via GIT_HASH env var at build time
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = True

    # Database (Cloud SQL)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/douga"
    database_echo: bool = False

    # Google Cloud Storage
    gcs_bucket_name: str = "douga-assets"
    gcs_project_id: str = ""

    # Local storage for development (when GCS is not configured)
    use_local_storage: bool = True  # Set to False in production
    local_storage_path: str = "/tmp/douga-storage"

    # Firebase
    firebase_project_id: str = ""

    # AI API Keys (for Whisper transcription and AI chat assistant)
    openai_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""

    # Default AI provider for chat assistant
    default_ai_provider: Literal["openai", "gemini", "anthropic"] = "openai"

    # CORS - stored as string, parsed via computed property
    cors_origins_raw: str = "http://localhost:5173,http://localhost:5174,http://localhost:3000"

    @computed_field
    @property
    def cors_origins(self) -> list[str]:
        """Parse CORS origins from pipe/comma-separated string or JSON array."""
        v = self.cors_origins_raw
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
        origins = [origin.strip() for origin in v.split(",") if origin.strip()]
        # Ensure production web origins are allowed
        for extra in ("https://douga-2f6f8.web.app", "https://douga-2f6f8.firebaseapp.com"):
            if extra not in origins:
                origins.append(extra)
        return origins

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
    # Maximum threads for FFmpeg (limits per-thread buffer memory)
    render_ffmpeg_threads: int = 2
    # Maximum muxing queue size (limits FFmpeg muxer memory)
    render_ffmpeg_max_muxing_queue: int = 1024

    # Development/Testing - DEV_USER bypasses Firebase auth
    dev_mode: bool = True  # Set to False in production
    dev_user_email: str = "dev@example.com"
    dev_user_name: str = "開発ユーザー"
    dev_user_id: str = "dev-user-123"

    # Edit session token
    edit_token_secret: str = "dev-edit-token-secret"


@lru_cache
def get_settings() -> Settings:
    return Settings()
