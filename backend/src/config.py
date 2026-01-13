import json
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    app_name: str = "Douga API"
    app_version: str = "0.1.0"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = True

    # Database (Cloud SQL)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/douga"
    database_echo: bool = False

    # Redis (Memorystore)
    redis_url: str = "redis://localhost:6379/0"

    # Google Cloud Storage
    gcs_bucket_name: str = "douga-assets"
    gcs_project_id: str = ""

    # Local storage for development (when GCS is not configured)
    use_local_storage: bool = True  # Set to False in production
    local_storage_path: str = "/tmp/douga-storage"

    # Firebase
    firebase_project_id: str = ""

    # OpenAI API (for Whisper transcription)
    openai_api_key: str = ""

    # CORS (accepts comma-separated string or JSON array)
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        """Parse CORS origins from comma-separated string or list."""
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            # Try JSON first
            if v.startswith("["):
                try:
                    return json.loads(v)
                except json.JSONDecodeError:
                    pass
            # Fall back to comma-separated
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

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

    # Development/Testing - DEV_USER bypasses Firebase auth
    dev_mode: bool = True  # Set to False in production
    dev_user_email: str = "dev@example.com"
    dev_user_name: str = "開発ユーザー"
    dev_user_id: str = "dev-user-123"


@lru_cache
def get_settings() -> Settings:
    return Settings()
