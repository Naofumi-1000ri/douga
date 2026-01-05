from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


AssetType = Literal["video", "audio", "image"]
AssetSubtype = Literal["avatar", "background", "slide", "narration", "bgm", "se", "effect", "other"]


class AssetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    type: AssetType
    subtype: AssetSubtype
    storage_key: str
    storage_url: str
    file_size: int
    mime_type: str
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    has_alpha: bool = False
    chroma_key_color: str | None = None


class AssetResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    type: str
    subtype: str
    storage_key: str
    storage_url: str
    thumbnail_url: str | None
    duration_ms: int | None
    width: int | None
    height: int | None
    file_size: int
    mime_type: str
    sample_rate: int | None
    channels: int | None
    has_alpha: bool
    chroma_key_color: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class AssetUploadUrl(BaseModel):
    upload_url: str
    storage_key: str
    expires_at: datetime
