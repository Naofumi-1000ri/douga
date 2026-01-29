"""Schemas for AI video production feature."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


# =============================================================================
# Asset Classification
# =============================================================================

AssetSubtypeAI = Literal[
    "avatar", "background", "slide", "narration", "bgm", "se", "screen", "effect", "other"
]


class ReclassifyAssetRequest(BaseModel):
    """Request to reclassify an asset."""

    type: Literal["video", "audio", "image"]
    subtype: AssetSubtypeAI


class AssetCatalogEntry(BaseModel):
    """Single asset in the AI-oriented catalog."""

    id: UUID
    name: str
    type: str
    subtype: str
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    has_audio: bool | None = None
    file_size_mb: float | None = None


class AssetCatalogSummary(BaseModel):
    """Summary statistics for the asset catalog."""

    total: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    by_subtype: dict[str, int] = Field(default_factory=dict)
    total_video_duration_ms: int = 0
    total_audio_duration_ms: int = 0


class AssetCatalogResponse(BaseModel):
    """AI-oriented asset catalog for a project."""

    project_id: UUID
    assets: list[AssetCatalogEntry]
    summary: AssetCatalogSummary


# =============================================================================
# Video Brief
# =============================================================================

SectionType = Literal["intro", "toc", "content", "demo", "summary", "cta", "outro"]
VideoStyle = Literal["tutorial", "lecture", "demo", "mixed"]


class BriefSection(BaseModel):
    """A section in the video brief."""

    type: SectionType
    title: str
    description: str = ""
    estimated_duration_seconds: int = 30
    assets_hint: list[str] = Field(default_factory=list)


class BriefPreferences(BaseModel):
    """User preferences for video generation."""

    use_avatar: bool = True
    avatar_position: str = "bottom-right"
    bgm_style: str = "calm"
    include_intro: bool = True
    include_outro: bool = True
    chroma_key_avatar: bool = True
    text_style: str = "modern"


class VideoBrief(BaseModel):
    """Structured user brief for video production."""

    title: str
    description: str = ""
    style: VideoStyle = "tutorial"
    target_duration_seconds: int = 300
    language: str = "ja"
    sections: list[BriefSection] = Field(default_factory=list)
    preferences: BriefPreferences = Field(default_factory=BriefPreferences)


# =============================================================================
# Video Plan
# =============================================================================

LayoutType = Literal[
    "avatar_fullscreen",
    "slide_with_avatar",
    "screen_capture",
    "text_only",
    "image_fullscreen",
]


class ElementTransform(BaseModel):
    """Transform for a plan element."""

    x: float = 0
    y: float = 0
    scale: float = 1.0
    rotation: float = 0


class ElementEffects(BaseModel):
    """Effects for a plan element."""

    chroma_key: dict[str, Any] | None = None
    fade_in_ms: int = 0
    fade_out_ms: int = 0


class TextStylePlan(BaseModel):
    """Text style in the plan."""

    fontSize: int = 48
    fontWeight: str = "bold"
    color: str = "#FFFFFF"
    textAlign: str = "center"
    strokeColor: str = "#000000"
    strokeWidth: int = 2
    backgroundColor: str = ""
    backgroundOpacity: float = 0.0


class PlanElement(BaseModel):
    """A visual element in a plan section."""

    id: str
    layer: Literal["background", "content", "avatar", "effects", "text"]
    asset_id: str | None = None
    text_content: str | None = None
    start_ms: int = 0
    duration_ms: int = 0
    transform: ElementTransform = Field(default_factory=ElementTransform)
    effects: ElementEffects = Field(default_factory=ElementEffects)
    text_style: TextStylePlan | None = None


class PlanAudioElement(BaseModel):
    """An audio element in a plan section."""

    id: str
    track: Literal["narration", "bgm", "se"]
    asset_id: str
    start_ms: int = 0
    duration_ms: int = 0
    volume: float = 1.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0


class PlanSection(BaseModel):
    """A section in the video plan."""

    id: str
    type: SectionType
    title: str
    layout: LayoutType = "avatar_fullscreen"
    start_ms: int = 0
    duration_ms: int = 0
    elements: list[PlanElement] = Field(default_factory=list)
    audio: list[PlanAudioElement] = Field(default_factory=list)


class VideoPlan(BaseModel):
    """AI-generated video plan (timeline design document)."""

    version: str = "1.0"
    total_duration_ms: int = 0
    status: Literal["draft", "approved", "applied"] = "draft"
    sections: list[PlanSection] = Field(default_factory=list)
    asset_assignments: dict[str, str] = Field(default_factory=dict)


# =============================================================================
# API Request/Response
# =============================================================================

class GeneratePlanRequest(BaseModel):
    """Request to generate a video plan."""

    brief: VideoBrief


class UpdatePlanRequest(BaseModel):
    """Request to update/modify the video plan."""

    plan: VideoPlan


class BatchUploadResult(BaseModel):
    """Result of a single file in batch upload."""

    filename: str
    asset_id: UUID | None = None
    type: str
    subtype: str
    confidence: float
    error: str | None = None


class BatchUploadResponse(BaseModel):
    """Response for batch upload."""

    project_id: UUID
    results: list[BatchUploadResult]
    total: int
    success: int
    failed: int


class PlanApplyResponse(BaseModel):
    """Response for applying a plan to timeline."""

    project_id: UUID
    duration_ms: int
    layers_populated: int
    audio_clips_added: int


class SkillResponse(BaseModel):
    """Response from a skill endpoint."""

    project_id: UUID
    skill: str
    success: bool
    message: str
    changes: dict[str, Any]
    duration_ms: int
