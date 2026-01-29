from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AssetFolderCreate(BaseModel):
    """Request model for creating a folder."""

    name: str = Field(..., min_length=1, max_length=255)


class AssetFolderUpdate(BaseModel):
    """Request model for updating a folder."""

    name: str = Field(..., min_length=1, max_length=255)


class AssetFolderResponse(BaseModel):
    """Response model for folder data."""

    id: UUID
    project_id: UUID
    name: str
    created_at: datetime

    class Config:
        from_attributes = True


class AssetMoveToFolder(BaseModel):
    """Request model for moving an asset to a folder."""

    folder_id: UUID | None = None
