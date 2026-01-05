from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    width: int = 1920
    height: int = 1080
    fps: int = 30


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    timeline_data: dict[str, Any] | None = None
    status: str | None = None


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
