from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class RenderRequest(BaseModel):
    quality: str = "high"  # low, medium, high
    format: str = "mp4"
    force: bool = False  # Force start even if another job exists
    start_ms: int | None = None  # Optional start time in milliseconds (for partial export)
    end_ms: int | None = None  # Optional end time in milliseconds (for partial export)


class RenderJobResponse(BaseModel):
    id: UUID
    project_id: UUID
    status: str
    progress: int
    current_stage: str | None
    output_url: str | None
    output_size: int | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RenderProgress(BaseModel):
    job_id: UUID
    status: str
    progress: int
    stage: str | None
    eta_seconds: int | None = None


class RenderPackageResponse(BaseModel):
    download_url: str
    package_size: int
    expires_at: datetime
