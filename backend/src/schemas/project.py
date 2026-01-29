from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


def _validate_even(value: int | None, field_name: str) -> int | None:
    """Validate that value is even (required for FFmpeg H.264 encoding)."""
    if value is not None and value % 2 != 0:
        raise ValueError(f"{field_name} must be an even number (got {value})")
    return value


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    width: int = Field(default=1920, ge=256, le=4096)
    height: int = Field(default=1080, ge=256, le=4096)
    fps: int = Field(default=30, ge=15, le=60)

    @field_validator("width", "height")
    @classmethod
    def validate_even(cls, v: int, info) -> int:
        if v % 2 != 0:
            raise ValueError(f"{info.field_name} must be an even number (got {v})")
        return v


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    width: int | None = Field(None, ge=256, le=4096)
    height: int | None = Field(None, ge=256, le=4096)
    fps: int | None = Field(None, ge=15, le=60)
    timeline_data: dict[str, Any] | None = None
    status: str | None = None

    @field_validator("width", "height")
    @classmethod
    def validate_even(cls, v: int | None, info) -> int | None:
        if v is not None and v % 2 != 0:
            raise ValueError(f"{info.field_name} must be an even number (got {v})")
        return v


class ProjectResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    description: str | None
    width: int
    height: int
    fps: int
    duration_ms: int
    timeline_data: dict[str, Any]
    video_brief: dict[str, Any] | None = None
    video_plan: dict[str, Any] | None = None
    status: str
    thumbnail_url: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProjectListResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    status: str
    duration_ms: int
    thumbnail_url: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
