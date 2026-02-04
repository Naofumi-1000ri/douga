from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ResponseMeta(BaseModel):
    api_version: str = "1.0"
    processing_time_ms: int
    timestamp: datetime
    warnings: list[str] = Field(default_factory=list)


class ErrorLocation(BaseModel):
    field: str | None = None
    clip_id: str | None = None
    layer_id: str | None = None
    index: int | None = None


class SuggestedAction(BaseModel):
    action: str
    endpoint: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class ErrorInfo(BaseModel):
    code: str
    message: str
    location: ErrorLocation | None = None
    retryable: bool = False
    suggested_fix: str | None = None  # Human-readable fix suggestion
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)


class EnvelopeResponse(BaseModel):
    request_id: str
    data: Any | None = None
    error: ErrorInfo | None = None
    meta: ResponseMeta
