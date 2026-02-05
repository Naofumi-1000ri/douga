"""AI Integration Schemas.

Hierarchical response schemas for AI assistants to minimize hallucination risk.
Designed with information hierarchy: L1 (Summary) -> L2 (Structure) -> L3 (Details)
"""

from datetime import datetime
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# L1: Summary Level (~300 tokens) - Project Overview
# =============================================================================


class ProjectSummary(BaseModel):
    """High-level project info for AI to grasp context quickly."""

    name: str
    duration_ms: int
    dimensions: str = Field(description="Format: WIDTHxHEIGHT (e.g., 1920x1080)")
    fps: int
    status: str


class TimelineSummary(BaseModel):
    """Aggregated timeline statistics."""

    layer_count: int
    audio_track_count: int
    total_video_clips: int
    total_audio_clips: int
    total_assets_used: int


class L1ProjectOverview(BaseModel):
    """L1: Lightweight overview for AI to understand project scope.

    Token budget: ~300 tokens
    Use case: Initial context, deciding what to explore next
    """

    project: ProjectSummary
    summary: TimelineSummary
    last_modified: datetime


# =============================================================================
# L2: Structure Level (~800 tokens) - Timeline Structure
# =============================================================================


class TimeRange(BaseModel):
    """Compact time range representation."""

    start_ms: int
    end_ms: int


class LayerSummary(BaseModel):
    """Summary of a single layer."""

    id: str
    name: str
    type: Literal["background", "content", "avatar", "effects", "text"]
    clip_count: int
    time_coverage: list[TimeRange] = Field(
        default_factory=list, description="Occupied time ranges"
    )
    visible: bool
    locked: bool


class AudioTrackSummary(BaseModel):
    """Summary of a single audio track."""

    id: str
    name: str
    type: Literal["narration", "bgm", "se", "video"]
    clip_count: int
    time_coverage: list[TimeRange] = Field(
        default_factory=list, description="Occupied time ranges"
    )
    volume: float
    muted: bool
    ducking_enabled: bool = False


class L2TimelineStructure(BaseModel):
    """L2: Timeline structure without clip details.

    Token budget: ~800 tokens
    Use case: Understanding layer/track organization, finding where to add content
    """

    project_id: UUID
    duration_ms: int
    layers: list[LayerSummary]
    audio_tracks: list[AudioTrackSummary]


# =============================================================================
# L3: Details Level (~400 tokens/clip) - Clip Details
# =============================================================================


class TransformDetails(BaseModel):
    """Clip transform properties."""

    x: float = 0
    y: float = 0
    width: float | None = None
    height: float | None = None
    scale: float = 1.0
    rotation: float = 0
    anchor: str = "center"


class CropDetails(BaseModel):
    """Clip crop properties.

    Values are fractional (0.0-0.5), representing the percentage of each edge to remove.
    """

    top: float = 0
    right: float = 0
    bottom: float = 0
    left: float = 0


class TextStyleDetails(BaseModel):
    """Text clip styling properties.

    Uses snake_case for API responses.
    """

    font_family: str = "Noto Sans JP"
    font_size: int = 48
    font_weight: int = 400
    color: str = "#ffffff"
    text_align: str = "center"
    background_color: str | None = None
    background_opacity: float = 0


class EffectsDetails(BaseModel):
    """Clip effects properties."""

    opacity: float = 1.0
    blend_mode: str = "normal"
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    chroma_key_enabled: bool = False
    chroma_key_color: str | None = None
    chroma_key_similarity: float = 0.4
    chroma_key_blend: float = 0.1


class TransitionDetails(BaseModel):
    """Transition properties."""

    type: str = "none"
    duration_ms: int = 0


class ClipTiming(BaseModel):
    """Clip timing information."""

    start_ms: int
    duration_ms: int
    end_ms: int = Field(description="Computed: start_ms + duration_ms")
    in_point_ms: int = 0
    out_point_ms: int | None = None


class ClipNeighbor(BaseModel):
    """Minimal info about neighboring clip."""

    id: str
    start_ms: int
    end_ms: int
    gap_ms: int = Field(description="Gap between this clip and the neighbor")


class L3ClipDetails(BaseModel):
    """L3: Full details for a single clip.

    Token budget: ~400 tokens per clip
    Use case: Modifying specific clips, understanding exact positioning
    """

    id: str
    layer_id: str
    layer_name: str
    asset_id: UUID | None = None
    asset_name: str | None = None

    timing: ClipTiming
    transform: TransformDetails
    effects: EffectsDetails
    crop: CropDetails | None = None
    transition_in: TransitionDetails
    transition_out: TransitionDetails

    # Text clip specific
    text_content: str | None = None
    text_style: TextStyleDetails | None = None

    # Grouping
    group_id: str | None = None

    # Context: neighboring clips for AI to understand relative positioning
    previous_clip: ClipNeighbor | None = None
    next_clip: ClipNeighbor | None = None


class L3AudioClipDetails(BaseModel):
    """L3: Full details for a single audio clip."""

    id: str
    track_id: str
    track_name: str
    asset_id: UUID
    asset_name: str | None = None

    timing: ClipTiming
    volume: float = 1.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0

    # Grouping
    group_id: str | None = None

    # Context
    previous_clip: ClipNeighbor | None = None
    next_clip: ClipNeighbor | None = None


# =============================================================================
# L2 Extended: Timeline at Specific Time
# =============================================================================


class ClipAtTime(BaseModel):
    """Clip information at a specific point in time."""

    id: str
    type: Literal["video", "audio"]
    layer_or_track_id: str
    layer_or_track_name: str
    start_ms: int
    end_ms: int
    progress_percent: float = Field(description="Playback progress at query time (0-100)")


class L2TimelineAtTime(BaseModel):
    """L2: What's happening at a specific moment.

    Use case: Understanding current playhead state, debugging overlaps
    """

    time_ms: int
    active_clips: list[ClipAtTime]
    next_event_ms: int | None = Field(
        default=None, description="Next clip start/end time"
    )


# =============================================================================
# Asset Catalog
# =============================================================================


class AssetInfo(BaseModel):
    """Asset information for AI reference."""

    id: UUID
    name: str
    type: Literal["video", "audio", "image"]
    subtype: str | None = None
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    usage_count: int = Field(description="How many clips reference this asset")


class L2AssetCatalog(BaseModel):
    """L2: Available assets in the project."""

    project_id: UUID
    assets: list[AssetInfo]
    total_count: int


# =============================================================================
# Write Operation Schemas
# =============================================================================


class AddClipRequest(BaseModel):
    """Request to add a new clip."""

    layer_id: str
    asset_id: UUID | None = None
    start_ms: int = Field(ge=0, description="Timeline position in milliseconds")
    duration_ms: int = Field(
        gt=0, le=3600000, description="Clip duration (max 1 hour)"
    )
    in_point_ms: int = Field(default=0, ge=0, description="Trim start in source asset")
    out_point_ms: int | None = Field(
        default=None, ge=0, description="Trim end in source asset"
    )

    # Optional transform
    x: float | None = Field(default=None, ge=-3840, le=3840)
    y: float | None = Field(default=None, ge=-2160, le=2160)
    scale: float | None = Field(default=None, ge=0.01, le=10.0)

    # For text clips
    text_content: str | None = None
    text_style: dict[str, Any] | None = None

    # Grouping
    group_id: str | None = None


class AddAudioClipRequest(BaseModel):
    """Request to add a new audio clip."""

    track_id: str
    asset_id: UUID
    start_ms: int = Field(ge=0, description="Timeline position in milliseconds")
    duration_ms: int = Field(
        gt=0, le=3600000, description="Clip duration (max 1 hour)"
    )
    in_point_ms: int = Field(default=0, ge=0, description="Trim start in source asset")
    out_point_ms: int | None = Field(
        default=None, ge=0, description="Trim end in source asset"
    )
    volume: float = Field(default=1.0, ge=0.0, le=2.0, description="Volume level")
    fade_in_ms: int = Field(default=0, ge=0, le=10000, description="Fade in duration")
    fade_out_ms: int = Field(default=0, ge=0, le=10000, description="Fade out duration")
    group_id: str | None = None


class AddAudioTrackRequest(BaseModel):
    """Request to add a new audio track."""

    name: str = Field(description="Track name")
    type: Literal["narration", "bgm", "se", "video"] = Field(
        default="bgm", description="Track type"
    )
    volume: float = Field(default=1.0, ge=0.0, le=2.0, description="Track volume")
    muted: bool = Field(default=False, description="Mute status")
    ducking_enabled: bool = Field(default=False, description="Enable ducking")
    insert_at: int | None = Field(
        default=None, description="Insert position (0=top, None=bottom)"
    )


class UpdateLayerRequest(BaseModel):
    """Request to update layer properties."""

    name: str | None = Field(default=None, description="Layer name")
    visible: bool | None = Field(default=None, description="Layer visibility")
    locked: bool | None = Field(default=None, description="Layer lock status")


class AddLayerRequest(BaseModel):
    """Request to add a new layer."""

    name: str = Field(description="Layer name")
    type: Literal["background", "content", "avatar", "effects", "text"] = Field(
        default="content", description="Layer type"
    )
    insert_at: int | None = Field(
        default=None, description="Insert position (0=top, None=bottom)"
    )


class ReorderLayersRequest(BaseModel):
    """Request to reorder layers."""

    layer_ids: list[str] = Field(description="Layer IDs in new order (top to bottom)")


class MoveClipRequest(BaseModel):
    """Request to move a clip."""

    new_start_ms: int = Field(ge=0, description="New timeline position in milliseconds")
    new_layer_id: str | None = Field(
        default=None, description="Target layer ID (if changing layers)"
    )


class MoveAudioClipRequest(BaseModel):
    """Request to move an audio clip."""

    new_start_ms: int = Field(ge=0, description="New timeline position in milliseconds")
    new_track_id: str | None = Field(
        default=None, description="Target track ID (if changing tracks)"
    )


class UpdateClipTransformRequest(BaseModel):
    """Request to update clip transform."""

    x: float | None = Field(default=None, ge=-3840, le=3840, description="X position")
    y: float | None = Field(default=None, ge=-2160, le=2160, description="Y position")
    width: float | None = Field(default=None, ge=1, le=7680, description="Width in pixels")
    height: float | None = Field(default=None, ge=1, le=4320, description="Height in pixels")
    scale: float | None = Field(default=None, ge=0.01, le=10.0, description="Scale factor")
    rotation: float | None = Field(
        default=None, ge=-360, le=360, description="Rotation in degrees"
    )
    anchor: Literal["center", "top-left", "top-right", "bottom-left", "bottom-right"] | None = None


class UpdateClipEffectsRequest(BaseModel):
    """Request to update clip effects."""

    opacity: float | None = Field(default=None, ge=0.0, le=1.0)
    blend_mode: str | None = None
    fade_in_ms: int | None = Field(default=None, ge=0, le=10000)
    fade_out_ms: int | None = Field(default=None, ge=0, le=10000)
    chroma_key_enabled: bool | None = None
    chroma_key_color: str | None = Field(
        default=None,
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="Chroma key color in hex format (#RRGGBB)"
    )
    chroma_key_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    chroma_key_blend: float | None = Field(default=None, ge=0.0, le=1.0)


class UpdateClipCropRequest(BaseModel):
    """Request to update clip crop.

    Crop values are fractional (0.0-0.5), representing the percentage of each edge to remove.
    For example, top=0.1 removes 10% from the top edge.
    Max 0.5 per edge to prevent removing more than half the frame.
    """

    top: float | None = Field(default=None, ge=0.0, le=0.5, description="Crop from top (0.0-0.5)")
    right: float | None = Field(default=None, ge=0.0, le=0.5, description="Crop from right (0.0-0.5)")
    bottom: float | None = Field(default=None, ge=0.0, le=0.5, description="Crop from bottom (0.0-0.5)")
    left: float | None = Field(default=None, ge=0.0, le=0.5, description="Crop from left (0.0-0.5)")


class UpdateClipTextStyleRequest(BaseModel):
    """Request to update text clip styling.

    All fields are optional for partial updates.
    Accepts snake_case input. camelCase aliases are accepted for compatibility.
    """

    model_config = ConfigDict(populate_by_name=True)

    font_family: str | None = Field(
        default=None,
        alias="fontFamily",
        description="Font family (e.g., 'Noto Sans JP')",
    )
    font_size: int | None = Field(
        default=None,
        alias="fontSize",
        ge=8,
        le=500,
        description="Font size in pixels",
    )
    font_weight: int | None = Field(
        default=None,
        alias="fontWeight",
        ge=100,
        le=900,
        description="Font weight (100-900)",
    )
    color: str | None = Field(
        default=None,
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="Text color in hex (#RRGGBB)",
    )
    text_align: Literal["left", "center", "right"] | None = Field(
        default=None,
        alias="textAlign",
        description="Text alignment",
    )
    background_color: str | None = Field(
        default=None,
        alias="backgroundColor",
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="Background color in hex (#RRGGBB)",
    )
    background_opacity: float | None = Field(
        default=None,
        alias="backgroundOpacity",
        ge=0.0,
        le=1.0,
        description="Background opacity (0-1)",
    )


class ChromaKeyBaseRequest(BaseModel):
    """Base request for chroma key preview/apply."""

    key_color: str = Field(
        default="auto",
        description='Key color ("auto" or HEX #RRGGBB)',
    )
    similarity: float = Field(default=0.4, ge=0.0, le=1.0)
    blend: float = Field(default=0.1, ge=0.0, le=1.0)

    @field_validator("key_color")
    @classmethod
    def validate_key_color(cls, v: str) -> str:
        if v.lower() == "auto":
            return "auto"
        if re.match(r"^#[0-9A-Fa-f]{6}$", v):
            return v
        raise ValueError('key_color must be "auto" or a HEX color like "#00FF00"')


class ChromaKeyPreviewRequest(ChromaKeyBaseRequest):
    """Request to generate 5-frame chroma key preview."""

    resolution: str = Field(
        default="640x360",
        pattern=r"^\d+x\d+$",
        description="Preview output size (e.g., 640x360)",
    )


class ChromaKeyApplyRequest(ChromaKeyBaseRequest):
    """Request to generate a processed chroma key clip asset."""


class SplitClipRequest(BaseModel):
    """Request to split a clip at a specific time."""

    split_at_ms: int = Field(gt=0, description="Time relative to clip start")


# =============================================================================
# Semantic Operations
# =============================================================================


class SemanticOperation(BaseModel):
    """High-level semantic operation request.

    Available operations:
    - snap_to_previous: Move clip to end of previous clip (requires target_clip_id)
    - snap_to_next: Move next clip to end of this clip (requires target_clip_id)
    - close_gap: Remove gaps in a layer (requires target_layer_id)
    - auto_duck_bgm: Enable BGM ducking (optional parameters: duck_to, attack_ms, release_ms)
    - rename_layer: Rename a layer (requires target_layer_id, parameters: {"name": "new name"})
    """

    operation: Literal[
        "snap_to_previous",
        "snap_to_next",
        "close_gap",
        "auto_duck_bgm",
        "rename_layer",
    ]
    target_clip_id: str | None = None
    target_layer_id: str | None = None
    target_track_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class SemanticOperationResult(BaseModel):
    """Result of a semantic operation."""

    success: bool
    operation: str
    changes_made: list[str] = Field(
        default_factory=list, description="Human-readable list of changes"
    )
    affected_clip_ids: list[str] = Field(default_factory=list)
    error_message: str | None = None


# =============================================================================
# Marker Operations
# =============================================================================


class AddMarkerRequest(BaseModel):
    """Request to add a marker to the timeline."""

    time_ms: int = Field(ge=0, description="Position on timeline in milliseconds")
    name: str = Field(default="", max_length=255, description="Marker name/label")
    color: str | None = Field(default=None, description="Marker color (hex or name)")


class UpdateMarkerRequest(BaseModel):
    """Request to update an existing marker."""

    time_ms: int | None = Field(default=None, ge=0, description="New position in ms")
    name: str | None = Field(default=None, max_length=255, description="New marker name")
    color: str | None = Field(default=None, description="New marker color")


# =============================================================================
# Batch Operations
# =============================================================================


class BatchClipOperation(BaseModel):
    """A single operation in a batch."""

    operation: Literal["add", "move", "trim", "update_transform", "update_effects", "delete", "update_layer"]
    clip_id: str | None = None  # Required for move/update/delete
    layer_id: str | None = None  # Required for update_layer
    clip_type: Literal["video", "audio"] = "video"
    data: dict[str, Any] = Field(default_factory=dict)


class BatchOperationRequest(BaseModel):
    """Batch operation request for multiple clips."""

    operations: list[BatchClipOperation]


class BatchOperationResult(BaseModel):
    """Result of batch operations."""

    success: bool
    total_operations: int
    successful_operations: int
    failed_operations: int
    results: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# =============================================================================
# Analysis Tools
# =============================================================================


class TimelineGap(BaseModel):
    """A gap in the timeline."""

    layer_or_track_id: str
    layer_or_track_name: str
    type: Literal["video", "audio"]
    start_ms: int
    end_ms: int
    duration_ms: int


class GapAnalysisResult(BaseModel):
    """Result of gap analysis."""

    total_gaps: int
    total_gap_duration_ms: int
    gaps: list[TimelineGap]


class PacingSegment(BaseModel):
    """A segment for pacing analysis."""

    start_ms: int
    end_ms: int
    clip_count: int
    avg_clip_duration_ms: float
    density: float = Field(description="Clips per second")


class PacingAnalysisResult(BaseModel):
    """Result of pacing analysis."""

    overall_avg_clip_duration_ms: float
    segments: list[PacingSegment]
    suggested_improvements: list[str] = Field(default_factory=list)


# =============================================================================
# Schema Discovery
# =============================================================================


class SchemaInfo(BaseModel):
    """Information about an available schema."""

    name: str
    description: str
    level: Literal["L1", "L2", "L3", "write", "analysis"]
    token_estimate: str
    endpoint: str


class AvailableSchemas(BaseModel):
    """List of available AI schemas."""

    schemas: list[SchemaInfo]


# =============================================================================
# Chat (Natural Language Instructions)
# =============================================================================


class ChatMessage(BaseModel):
    """A single chat message."""

    role: Literal["user", "assistant"]
    content: str


# AI Provider type
AIProvider = Literal["openai", "gemini", "anthropic"]


class ChatRequest(BaseModel):
    """Request to the AI chat endpoint."""

    message: str = Field(description="Natural language instruction from the user")
    history: list[ChatMessage] = Field(
        default_factory=list, description="Previous conversation messages for context"
    )
    provider: AIProvider | None = Field(
        default=None, description="AI provider to use (openai, gemini, anthropic). If not specified, uses default."
    )


class ChatAction(BaseModel):
    """An action taken by the AI in response to a chat message."""

    type: str = Field(description="Type of action (e.g., semantic, batch, analysis)")
    description: str = Field(description="Human-readable description of what was done")
    applied: bool = Field(description="Whether the action was successfully applied")


class ChatResponse(BaseModel):
    """Response from the AI chat endpoint."""

    message: str = Field(description="AI's response message in natural language")
    actions: list[ChatAction] = Field(
        default_factory=list, description="Actions taken during this interaction"
    )
    actions_applied: bool = Field(
        default=False, description="Whether any actions were successfully applied"
    )


class ChatStreamEvent(BaseModel):
    """A single Server-Sent Event for chat streaming."""

    event: Literal["chunk", "actions", "done", "error"] = Field(
        description="Event type: chunk (text), actions (executed actions), done (completion), error"
    )
    data: str = Field(
        default="", description="Event data: text chunk, JSON actions, or error message"
    )
