from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class APIKeyCreate(BaseModel):
    """Request to create a new API key."""

    name: str = Field(..., min_length=1, max_length=255, description="Descriptive name for the key")
    expires_in_days: int | None = Field(
        None, ge=1, le=365, description="Expiration in days (None = never expires)"
    )


class APIKeyResponse(BaseModel):
    """Response for API key (without the actual key)."""

    id: UUID
    name: str
    key_prefix: str
    is_active: bool
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class APIKeyCreated(BaseModel):
    """Response when a new API key is created.

    IMPORTANT: The `key` field is only returned once at creation time.
    It cannot be retrieved later.
    """

    id: UUID
    name: str
    key: str = Field(..., description="The actual API key (shown only once)")
    key_prefix: str
    created_at: datetime
    expires_at: datetime | None

    class Config:
        from_attributes = True
