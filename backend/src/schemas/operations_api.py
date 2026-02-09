"""Schemas for the operations API (collaborative editing)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class OperationItem(BaseModel):
    """A single operation in a batch."""

    type: str = Field(..., description="Operation type, e.g. 'clip.move', 'layer.add'")
    clip_id: str | None = None
    layer_id: str | None = None
    track_id: str | None = None
    marker_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class ApplyOperationsRequest(BaseModel):
    """Request to apply a batch of operations atomically."""

    version: int = Field(..., description="Client's current version")
    operations: list[OperationItem] = Field(..., min_length=1)


class ApplyOperationsResponse(BaseModel):
    """Response after successfully applying operations."""

    version: int
    timeline_data: dict[str, Any]


class OperationHistoryItem(BaseModel):
    """A single operation in the history."""

    id: UUID
    version: int
    type: str
    user_id: UUID | None = None
    user_name: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class OperationHistoryResponse(BaseModel):
    """Response for operation history polling."""

    current_version: int
    operations: list[OperationHistoryItem]
