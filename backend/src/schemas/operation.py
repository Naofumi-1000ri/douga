"""Schemas for operation history and rollback.

These schemas define the request/response formats for operation tracking,
history queries, and rollback functionality.
"""

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# Diff Schemas
# =============================================================================


class ChangeDetail(BaseModel):
    """Detail of a single change within an operation."""

    entity_type: Literal["clip", "layer", "audio_clip", "audio_track", "marker", "keyframe"]
    entity_id: str
    change_type: Literal["created", "modified", "deleted"]
    before: dict[str, Any] | None = None  # Previous state (for modified/deleted)
    after: dict[str, Any] | None = None  # New state (for created/modified)


class TimelineDiff(BaseModel):
    """Diff information for an operation.

    Contains only the affected entities, not full timeline snapshots.
    """

    operation_id: str
    operation_type: str
    changes: list[ChangeDetail] = Field(default_factory=list)
    duration_before_ms: int
    duration_after_ms: int


# =============================================================================
# Request/Result Summary Schemas
# =============================================================================


class RequestSummary(BaseModel):
    """Summary of the operation request.

    Minimal information for AI to understand what was requested.
    """

    endpoint: str  # e.g., "/clips"
    method: str  # e.g., "POST"
    target_ids: list[str] = Field(default_factory=list)  # IDs being operated on
    key_params: dict[str, Any] = Field(default_factory=dict)  # Key parameters


class ResultSummary(BaseModel):
    """Summary of the operation result."""

    success: bool
    created_ids: list[str] = Field(default_factory=list)
    modified_ids: list[str] = Field(default_factory=list)
    deleted_ids: list[str] = Field(default_factory=list)
    message: str | None = None


# =============================================================================
# Operation Record Schemas
# =============================================================================


class OperationRecord(BaseModel):
    """Full operation record as returned by API."""

    id: UUID
    project_id: UUID
    operation_type: str
    source: str

    affected_clips: list[str]
    affected_layers: list[str]
    affected_audio_clips: list[str]

    diff: TimelineDiff | None = None
    request_summary: RequestSummary | None = None
    result_summary: ResultSummary | None = None

    rollback_available: bool
    rolled_back: bool
    rolled_back_at: datetime | None = None
    rolled_back_by: UUID | None = None

    success: bool
    error_code: str | None = None
    error_message: str | None = None

    idempotency_key: str | None = None
    user_id: UUID | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OperationSummary(BaseModel):
    """Lightweight operation summary for history lists."""

    id: UUID
    operation_type: str
    source: str
    success: bool
    rollback_available: bool
    rolled_back: bool
    created_at: datetime

    # Include result summary for quick overview
    result_summary: ResultSummary | None = None

    # Rollback URL for easy agent access (populated by history endpoint)
    rollback_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# History Query Schemas
# =============================================================================


class HistoryQuery(BaseModel):
    """Query parameters for history endpoint.

    Note: since/until datetime values without timezone info are treated as UTC.
    """

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    operation_type: str | None = None
    source: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    success_only: bool = False
    clip_id: str | None = Field(
        default=None,
        description="Filter by affected clip. Requires full ID (exact match, no partial ID support).",
    )

    @field_validator("since", "until", mode="after")
    @classmethod
    def ensure_timezone_aware(cls, v: datetime | None) -> datetime | None:
        """Ensure datetime values are timezone-aware (treat naive as UTC)."""
        if v is None:
            return None
        if v.tzinfo is None:
            # Naive datetime -> treat as UTC
            return v.replace(tzinfo=timezone.utc)
        return v


class HistoryResponse(BaseModel):
    """Response for history query."""

    operations: list[OperationSummary]
    total: int
    page: int
    page_size: int
    has_more: bool


# =============================================================================
# Rollback Schemas
# =============================================================================


class RollbackRequest(BaseModel):
    """Request to rollback an operation."""

    # No body needed - operation_id is in path


class RollbackResponse(BaseModel):
    """Response from rollback operation."""

    rollback_operation_id: UUID  # New operation created by rollback
    reverted_changes: list[ChangeDetail]
    success: bool
    message: str | None = None


# =============================================================================
# Operation Result (for mutation responses)
# =============================================================================


class OperationMeta(BaseModel):
    """Operation metadata included in mutation responses."""

    operation_id: UUID
    rollback_available: bool = True


class MutationResponseWithOperation(BaseModel):
    """Base class for mutation responses that include operation tracking."""

    operation: OperationMeta
    diff: TimelineDiff | None = None  # Only if options.include_diff=True
