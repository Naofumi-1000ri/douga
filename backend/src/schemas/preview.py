"""Schemas for preview/sampling API endpoints."""

from typing import Any, Literal

from pydantic import BaseModel, Field


# =============================================================================
# Event Point Detection
# =============================================================================

EventType = Literal[
    "clip_start",
    "clip_end",
    "slide_change",
    "section_boundary",
    "avatar_enter",
    "avatar_exit",
    "narration_start",
    "narration_end",
    "bgm_start",
    "se_trigger",
    "silence_gap",
    "effect_point",
    "layer_change",
]


class EventPoint(BaseModel):
    """A detected event point in the timeline."""

    time_ms: int = Field(..., description="Time position in milliseconds")
    event_type: EventType = Field(..., description="Type of event")
    description: str = Field("", description="Human-readable description")
    layer: str | None = Field(None, description="Layer type if applicable")
    clip_id: str | None = Field(None, description="Related clip ID if applicable")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class EventPointsRequest(BaseModel):
    """Request for event point detection."""

    include_audio: bool = Field(True, description="Include audio events")
    include_visual: bool = Field(True, description="Include visual layer events")
    min_gap_ms: int = Field(500, description="Minimum silence gap to detect (ms)")


class EventPointsResponse(BaseModel):
    """Response with detected event points."""

    project_id: str
    event_points: list[EventPoint]
    total_events: int
    duration_ms: int


# =============================================================================
# Frame Sampling
# =============================================================================


class SampleFrameRequest(BaseModel):
    """Request to render a single preview frame."""

    time_ms: int = Field(..., description="Time to sample (ms)")
    resolution: str = Field("640x360", description="Output resolution (WxH)")


class SampleFrameResponse(BaseModel):
    """Response with a rendered preview frame."""

    time_ms: int
    resolution: str
    frame_base64: str = Field(..., description="Base64-encoded JPEG image")
    size_bytes: int


# =============================================================================
# Event Point Sampling (Combined)
# =============================================================================


class SampleEventPointsRequest(BaseModel):
    """Request to auto-detect and sample event point frames."""

    max_samples: int = Field(10, description="Maximum number of frames to sample")
    resolution: str = Field("640x360", description="Output resolution (WxH)")
    include_audio: bool = Field(True, description="Include audio events")
    min_gap_ms: int = Field(500, description="Minimum silence gap to detect (ms)")


class SampledEventPoint(BaseModel):
    """An event point with its rendered frame."""

    time_ms: int
    event_type: EventType
    description: str
    frame_base64: str = Field(..., description="Base64-encoded JPEG image")


class SampleEventPointsResponse(BaseModel):
    """Response with sampled event point frames."""

    project_id: str
    samples: list[SampledEventPoint]
    total_events: int
    sampled_count: int


# =============================================================================
# Composition Validation
# =============================================================================


class ValidationSeverity(BaseModel):
    """Severity of a validation issue."""

    level: Literal["error", "warning", "info"] = "warning"


class ValidationIssue(BaseModel):
    """A detected composition issue."""

    rule: str = Field(..., description="Rule that was violated")
    severity: Literal["error", "warning", "info"] = "warning"
    message: str = Field(..., description="Human-readable description")
    time_ms: int | None = Field(None, description="Time position if applicable")
    clip_id: str | None = Field(None, description="Related clip ID")
    layer: str | None = Field(None, description="Related layer type")
    suggestion: str | None = Field(None, description="Suggested fix")


class ValidateCompositionRequest(BaseModel):
    """Request for composition validation."""

    rules: list[str] | None = Field(
        None,
        description="Specific rules to check (None = all rules)",
    )


class ValidateCompositionResponse(BaseModel):
    """Response with validation results."""

    project_id: str
    is_valid: bool
    issues: list[ValidationIssue]
    total_issues: int
    errors: int
    warnings: int
