from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class SequenceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class SequenceUpdate(BaseModel):
    timeline_data: dict[str, Any]
    version: int  # Required for optimistic locking


class SequenceListItem(BaseModel):
    id: UUID
    name: str
    version: int
    duration_ms: int
    is_default: bool
    locked_by: UUID | None = None
    lock_holder_name: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SequenceDetail(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    timeline_data: dict[str, Any]
    version: int
    duration_ms: int
    is_default: bool
    locked_by: UUID | None = None
    lock_holder_name: str | None = None
    locked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SequenceDefaultResponse(BaseModel):
    id: UUID


class LockResponse(BaseModel):
    locked: bool
    locked_by: UUID | None = None
    lock_holder_name: str | None = None
    locked_at: datetime | None = None
