from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

AssetType = Literal["video", "audio", "image", "session"]
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
    hash: str | None = None
    is_internal: bool = False
    folder_id: UUID | None = None
    created_at: datetime
    metadata: dict | None = None  # For session assets: app_version, created_at

    class Config:
        from_attributes = True


class TimingFactSource(BaseModel):
    source: str
    duration_ms: int | None = None
    sample_rate: int | None = None
    channels: int | None = None


class TimingDriftObservation(BaseModel):
    field: str
    source_a: str
    source_b: str
    value_a: int
    value_b: int
    delta: int


class TimingFallbackRisk(BaseModel):
    code: str
    message: str


class AssetTimingAuditEntry(BaseModel):
    asset_id: UUID
    asset_name: str
    asset_type: str
    asset_subtype: str
    source_asset_id: UUID | None = None
    sources: list[TimingFactSource]
    drifts: list[TimingDriftObservation]
    fallback_risks: list[TimingFallbackRisk]
    storage_probe_error: str | None = None


class AssetTimingAuditResponse(BaseModel):
    limit: int
    offset: int
    returned_entries: int
    has_more: bool
    total_assets: int
    assets_with_drifts: int
    assets_with_fallback_risks: int
    assets_missing_waveform: int
    entries: list[AssetTimingAuditEntry]


class AssetUploadUrl(BaseModel):
    upload_url: str
    storage_key: str
    expires_at: datetime


# Session-related schemas
class Fingerprint(BaseModel):
    """Asset fingerprint for session mapping"""

    hash: str | None = None  # SHA-256 hash "sha256:..."
    file_size: int | None = None
    duration_ms: int | None = None  # 0 for images, None if unknown


class AssetMetadata(BaseModel):
    """Asset metadata for display purposes (not used in matching)"""

    codec: str | None = None
    width: int | None = None
    height: int | None = None


class AssetReference(BaseModel):
    """Reference to an asset used in a session"""

    id: str  # Original asset UUID
    name: str
    type: str  # video, audio, image
    fingerprint: Fingerprint
    metadata: AssetMetadata | None = None


class SessionData(BaseModel):
    """Session file data structure"""

    schema_version: str = "1.0"
    created_at: str | None = None  # ISO 8601, set by server
    app_version: str | None = None  # Set by server
    timeline_data: dict  # The actual timeline JSON
    asset_references: list[AssetReference] = []


class SessionSaveRequest(BaseModel):
    """Request body for saving a session"""

    session_name: str = Field(..., min_length=1, max_length=255)
    session_data: SessionData


class RenameRequest(BaseModel):
    """Request body for renaming an asset"""

    name: str = Field(..., min_length=1, max_length=255)
