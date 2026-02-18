"""AI v1 API Router.

Thin wrapper around existing AI service with envelope responses.
Implements AI-Friendly API spec with validate_only support.
"""

import hashlib
import logging
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status


def _serialize_for_json(obj: Any) -> Any:
    """Recursively convert UUIDs to strings for JSON serialization."""
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize_for_json(item) for item in obj]
    return obj
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm.attributes import flag_modified

from src.api.access import get_accessible_project
from src.api.deps import CurrentUser, DbSession, OptionalUser, get_edit_context
from src.exceptions import ChromaKeyAutoFailedError, DougaError, InvalidTimeRangeError
from src.middleware.request_context import (
    RequestContext,
    build_meta,
    create_request_context,
    validate_headers,
)
from src.models.asset import Asset
from src.models.project import Project
from src.models.project_member import ProjectMember
from src.models.sequence import Sequence, _default_timeline_data
from src.schemas.ai import (
    AddAudioClipRequest,
    AddAudioTrackRequest,
    AddClipRequest,
    AddKeyframeRequest,
    AddLayerRequest,
    AddMarkerRequest,
    BatchClipOperation,
    BatchOperationResult,
    ChromaKeyApplyRequest,
    ChromaKeyPreviewRequest,
    GapAnalysisResult,
    L1ProjectOverview,
    L2AssetCatalog,
    L2TimelineAtTime,
    L2TimelineStructure,
    L3AudioClipDetails,
    L3ClipDetails,
    L25TimelineOverview,
    MoveAudioClipRequest,
    MoveClipRequest,
    PacingAnalysisResult,
    PreviewDiffRequest,
    ReorderLayersRequest,
    SemanticOperation,
    SemanticOperationResult,
    UpdateAudioClipRequest,
    UpdateClipCropRequest,
    UpdateClipEffectsRequest,
    UpdateClipShapeRequest,
    UpdateClipTextRequest,
    UpdateClipTextStyleRequest,
    UpdateClipTimingRequest,
    UpdateClipTransformRequest,
    UpdateLayerRequest,
    UpdateMarkerRequest,
)
from src.schemas.asset import AssetResponse
from src.schemas.clip_adapter import UnifiedClipInput, UnifiedMoveClipInput, UnifiedTransformInput
from src.schemas.effects_generated import EFFECTS_CAPABILITIES
from src.schemas.envelope import EnvelopeResponse, ErrorInfo, ResponseMeta
from src.schemas.operation import (
    ChangeDetail,
    HistoryQuery,
    HistoryResponse,
    OperationRecord,
    RequestSummary,
    ResultSummary,
    RollbackRequest,
)
from src.schemas.options import OperationOptions
from src.services.ai_service import AIService, _sanitize_timeline_ms
from src.services.chroma_key_service import ChromaKeyService
from src.services.event_manager import event_manager
from src.services.operation_service import OperationService
from src.services.storage_service import get_storage_service
from src.services.validation_service import ValidationService
from src.utils.interpolation import EASING_FUNCTIONS
from src.utils.media_info import get_media_info

logger = logging.getLogger(__name__)

router = APIRouter()

# Valid fields for the transform endpoint (used for unknown field detection)
_VALID_TRANSFORM_FIELDS: set[str] = {
    "x", "y", "scale", "rotation", "opacity",
    "width", "height", "anchor", "transform",
}


# =============================================================================
# Sequence duration sync helper
# =============================================================================


def _sync_sequence_duration(seq: Any, timeline_data: dict) -> None:
    """Update sequence.duration_ms from its timeline_data after modifications."""
    if seq is None:
        return
    max_end = 0
    for layer in timeline_data.get("layers", []):
        for clip in layer.get("clips", []):
            end = (clip.get("start_ms", 0) or 0) + (clip.get("duration_ms", 0) or 0)
            if end > max_end:
                max_end = end
    for track in timeline_data.get("audio_tracks", []):
        for clip in track.get("clips", []):
            end = (clip.get("start_ms", 0) or 0) + (clip.get("duration_ms", 0) or 0)
            if end > max_end:
                max_end = end
    seq.duration_ms = int(max_end)


# =============================================================================
# Flat body auto-wrap helper
# =============================================================================


def _auto_wrap_flat_body(data: dict, wrapper_key: str) -> dict:
    """Auto-wrap a flat request body into the expected wrapper structure.

    If the wrapper_key is already present in data, return as-is (standard format).
    Otherwise, collect all keys except 'options' and 'auto_wrapped' and wrap them
    under wrapper_key.

    Examples:
        # Standard format (no change):
        {"timing": {"duration_ms": 5000}, "options": {}}
        -> {"timing": {"duration_ms": 5000}, "options": {}}

        # Flat format (auto-wrapped):
        {"duration_ms": 5000}
        -> {"timing": {"duration_ms": 5000}, "options": {}, "auto_wrapped": True}

    Returns:
        Modified data dict with wrapper applied if needed.
    """
    if not isinstance(data, dict):
        return data

    # If wrapper key already exists, no wrapping needed
    if wrapper_key in data:
        return data

    # Collect fields that are not 'options' or 'auto_wrapped'
    reserved_keys = {"options", "auto_wrapped"}
    inner_fields = {k: v for k, v in data.items() if k not in reserved_keys}
    if not inner_fields:
        return data

    wrapped: dict = {
        wrapper_key: inner_fields,
        "options": data.get("options", {}),
        "auto_wrapped": True,
    }
    return wrapped


class CreateClipRequest(BaseModel):
    """Request to create a clip.

    Accepts wrapped, flat (transitional), and fully-flat body formats.

    Wrapped format:
        {"clip": {"layer_id": "...", "asset_id": "...", "start_ms": 0, "duration_ms": 1000}, "options": {}}

    Flat clip format (transitional):
        {"clip": {"layer_id": "...", "x": 0, "y": 0, "scale": 1}, "options": {}}

    Fully-flat body (auto-wrapped):
        {"layer_id": "...", "asset_id": "...", "start_ms": 0, "duration_ms": 1000}
        -> auto-wrapped to {"clip": {...}, "options": {}, "auto_wrapped": true}

    Nested format (spec-compliant):
        {"clip": {"type": "video", "layer_id": "...", "transform": {...}}, "options": {}}
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    clip: UnifiedClipInput
    auto_wrapped: bool = Field(default=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _wrap_flat_body(cls, data: Any) -> Any:
        return _auto_wrap_flat_body(data, "clip")

    def to_internal_clip(self) -> AddClipRequest:
        """Convert unified clip input to internal AddClipRequest format."""
        flat_data = self.clip.to_flat_dict()
        return AddClipRequest.model_validate(flat_data)


class MoveClipV1Request(BaseModel):
    """Request to move a clip to a new timeline position or layer.

    Accepts both wrapped and flat formats:
        Wrapped: {"move": {"new_start_ms": 1000}, "options": {}}
        Flat:    {"new_start_ms": 1000}
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    move: UnifiedMoveClipInput
    auto_wrapped: bool = Field(default=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _wrap_flat_body(cls, data: Any) -> Any:
        return _auto_wrap_flat_body(data, "move")

    def to_internal_request(self) -> MoveClipRequest:
        """Convert to internal MoveClipRequest."""
        return MoveClipRequest(
            new_start_ms=self.move.new_start_ms,
            new_layer_id=self.move.new_layer_id,
        )


class TransformClipV1Request(BaseModel):
    """Request to update clip transform properties.

    Accepts both flat and nested formats:

    Flat format:
        {"options": {...}, "transform": {"x": 100, "y": 200, "scale": 1.5}}

    Nested format:
        {"options": {...}, "transform": {"transform": {"position": {...}, "scale": {...}}}}

    Also accepts fully flat body (auto-wrapped):
        {"x": 100, "y": 200, "scale": 1.5}
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    transform: UnifiedTransformInput
    auto_wrapped: bool = Field(default=False, exclude=True)
    unknown_transform_fields: list[str] = Field(default_factory=list, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _wrap_flat_body(cls, data: Any) -> Any:
        wrapped = _auto_wrap_flat_body(data, "transform")
        # Detect unknown fields in the transform dict
        if isinstance(wrapped, dict):
            transform_data = wrapped.get("transform")
            if isinstance(transform_data, dict):
                unknown = [k for k in transform_data if k not in _VALID_TRANSFORM_FIELDS]
                if unknown:
                    wrapped["unknown_transform_fields"] = unknown
        return wrapped

    def get_unknown_field_warnings(self) -> list[str]:
        """Get warnings for unknown transform fields detected during parsing."""
        if not self.unknown_transform_fields:
            return []
        return [
            f"Unknown transform fields ignored: {', '.join(self.unknown_transform_fields)}. "
            f"Valid transform fields: x, y, scale, rotation, opacity. "
            f"Note: opacity can also be set via PATCH /clips/{{id}}/effects"
        ]

    def to_internal_request(self) -> UpdateClipTransformRequest:
        """Convert to internal UpdateClipTransformRequest."""
        flat_dict = self.transform.to_flat_dict()
        return UpdateClipTransformRequest.model_validate(flat_dict)


class UpdateEffectsV1Request(BaseModel):
    """Request to update clip effects.

    Supports:
    - opacity: 0.0-1.0
    - blend_mode: "normal", "multiply", etc.
    - fade_in_ms: 0-10000ms fade in duration
    - fade_out_ms: 0-10000ms fade out duration
    - chroma_key_enabled: bool
    - chroma_key_color: hex color (#RRGGBB)
    - chroma_key_similarity: 0.0-1.0
    - chroma_key_blend: 0.0-1.0

    Also accepts flat body (auto-wrapped):
        {"opacity": 0.5} -> {"effects": {"opacity": 0.5}, "options": {}}
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    effects: UpdateClipEffectsRequest
    auto_wrapped: bool = Field(default=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _wrap_flat_body(cls, data: Any) -> Any:
        return _auto_wrap_flat_body(data, "effects")

    def to_internal_request(self) -> UpdateClipEffectsRequest:
        """Return the internal request (already in correct format)."""
        return self.effects


class DeleteClipV1Request(BaseModel):
    """Request to delete a clip."""

    options: OperationOptions = Field(default_factory=OperationOptions)


class UpdateCropV1Request(BaseModel):
    """Request to update clip crop.

    Crop values are fractional (0.0-0.5), representing the percentage of each edge to remove.
    For example, top=0.1 removes 10% from the top edge.

    Also accepts flat body (auto-wrapped):
        {"top": 0.1} -> {"crop": {"top": 0.1}, "options": {}}
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    crop: UpdateClipCropRequest
    auto_wrapped: bool = Field(default=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _wrap_flat_body(cls, data: Any) -> Any:
        return _auto_wrap_flat_body(data, "crop")

    def to_internal_request(self) -> UpdateClipCropRequest:
        """Return the internal request (already in correct format)."""
        return self.crop


class UpdateTextStyleV1Request(BaseModel):
    """Request to update text clip styling.

    Uses snake_case input; camelCase aliases are accepted for compatibility.
    Supports:
    - font_family: Font family name (e.g., "Noto Sans JP")
    - font_size: 8-500 pixels
    - font_weight: 100-900 (integer) or "bold"/"normal" (string)
    - color: Text color in hex (#RRGGBB)
    - text_align: "left", "center", or "right"
    - background_color: Background color in hex (#RRGGBB)
    - background_opacity: 0.0-1.0

    Also accepts flat body (auto-wrapped):
        {"font_size": 24} -> {"text_style": {"font_size": 24}, "options": {}}
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    text_style: UpdateClipTextStyleRequest
    auto_wrapped: bool = Field(default=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _wrap_flat_body(cls, data: Any) -> Any:
        return _auto_wrap_flat_body(data, "text_style")

    def to_internal_request(self) -> UpdateClipTextStyleRequest:
        """Return the internal request (already in correct format)."""
        return self.text_style


class UpdateAudioClipV1Request(BaseModel):
    """Request to update audio clip properties (volume, fades).

    Supports:
    - volume: 0.0-2.0
    - fade_in_ms: 0-10000ms
    - fade_out_ms: 0-10000ms
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    audio: UpdateAudioClipRequest

    def to_internal_request(self) -> UpdateAudioClipRequest:
        """Return the internal request (already in correct format)."""
        return self.audio


class UpdateClipTimingV1Request(BaseModel):
    """Request to update clip timing properties.

    Supports:
    - duration_ms: New clip duration (1-3600000)
    - speed: Playback speed multiplier (0.1-10.0)
    - in_point_ms: Trim start in source
    - out_point_ms: Trim end in source

    Also accepts flat body (auto-wrapped):
        {"duration_ms": 5000} -> {"timing": {"duration_ms": 5000}, "options": {}}
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    timing: UpdateClipTimingRequest
    auto_wrapped: bool = Field(default=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _wrap_flat_body(cls, data: Any) -> Any:
        return _auto_wrap_flat_body(data, "timing")

    def to_internal_request(self) -> UpdateClipTimingRequest:
        """Return the internal request (already in correct format)."""
        return self.timing


class UpdateClipTextV1Request(BaseModel):
    """Request to update text clip content.

    Supports:
    - text_content: New text content string

    Also accepts flat body (auto-wrapped):
        {"text_content": "Hello"} -> {"text": {"text_content": "Hello"}, "options": {}}
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    text: UpdateClipTextRequest
    auto_wrapped: bool = Field(default=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _wrap_flat_body(cls, data: Any) -> Any:
        return _auto_wrap_flat_body(data, "text")

    def to_internal_request(self) -> UpdateClipTextRequest:
        """Return the internal request (already in correct format)."""
        return self.text


class UpdateClipShapeV1Request(BaseModel):
    """Request to update shape clip properties.

    Supports:
    - filled: Whether shape is filled
    - fillColor / fill_color: Fill color hex
    - strokeColor / stroke_color: Stroke color hex
    - strokeWidth / stroke_width: Stroke width (0-50)
    - width: Shape width (1-7680)
    - height: Shape height (1-4320)
    - cornerRadius / corner_radius: Corner radius
    - fade: Fade duration in ms (0-10000)

    Also accepts flat body (auto-wrapped):
        {"filled": true} -> {"shape": {"filled": true}, "options": {}}
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    shape: UpdateClipShapeRequest
    auto_wrapped: bool = Field(default=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _wrap_flat_body(cls, data: Any) -> Any:
        return _auto_wrap_flat_body(data, "shape")

    def to_internal_request(self) -> UpdateClipShapeRequest:
        """Return the internal request (already in correct format)."""
        return self.shape


# =============================================================================
# Layer Request Models
# =============================================================================


class AddLayerV1Request(BaseModel):
    """Request to add a new layer."""

    options: OperationOptions = Field(default_factory=OperationOptions)
    layer: AddLayerRequest

    def to_internal_request(self) -> AddLayerRequest:
        """Return the internal request (already in correct format)."""
        return self.layer


class UpdateLayerV1Request(BaseModel):
    """Request to update layer properties."""

    options: OperationOptions = Field(default_factory=OperationOptions)
    layer: UpdateLayerRequest

    def to_internal_request(self) -> UpdateLayerRequest:
        """Return the internal request (already in correct format)."""
        return self.layer


class ReorderLayersV1Request(BaseModel):
    """Request to reorder layers."""

    options: OperationOptions = Field(default_factory=OperationOptions)
    order: ReorderLayersRequest

    def to_internal_request(self) -> ReorderLayersRequest:
        """Return the internal request (already in correct format)."""
        return self.order


# =============================================================================
# Audio Request Models
# =============================================================================


class AddAudioClipV1Request(BaseModel):
    """Request to add a new audio clip."""

    options: OperationOptions = Field(default_factory=OperationOptions)
    clip: AddAudioClipRequest

    def to_internal_request(self) -> AddAudioClipRequest:
        """Return the internal request (already in correct format)."""
        return self.clip


class MoveAudioClipV1Request(BaseModel):
    """Request to move an audio clip."""

    options: OperationOptions = Field(default_factory=OperationOptions)
    new_start_ms: int = Field(ge=0, description="New timeline position in milliseconds")
    new_track_id: str | None = Field(
        default=None, description="Target track ID (if changing tracks)"
    )

    def to_internal_request(self) -> MoveAudioClipRequest:
        """Convert to internal MoveAudioClipRequest."""
        return MoveAudioClipRequest(
            new_start_ms=self.new_start_ms,
            new_track_id=self.new_track_id,
        )


class DeleteAudioClipV1Request(BaseModel):
    """Request to delete an audio clip."""

    options: OperationOptions = Field(default_factory=OperationOptions)


class AddAudioTrackV1Request(BaseModel):
    """Request to add a new audio track."""

    options: OperationOptions = Field(default_factory=OperationOptions)
    track: AddAudioTrackRequest

    def to_internal_request(self) -> AddAudioTrackRequest:
        """Return the internal request (already in correct format)."""
        return self.track


# =============================================================================
# Priority 4: Marker Request Models
# =============================================================================


class AddMarkerV1Request(BaseModel):
    """Request to add a marker."""

    options: OperationOptions = Field(default_factory=OperationOptions)
    marker: AddMarkerRequest


class UpdateMarkerV1Request(BaseModel):
    """Request to update a marker."""

    options: OperationOptions = Field(default_factory=OperationOptions)
    marker: UpdateMarkerRequest


class DeleteMarkerV1Request(BaseModel):
    """Request to delete a marker."""

    options: OperationOptions = Field(default_factory=OperationOptions)


# =============================================================================
# Keyframe Request Models
# =============================================================================


class AddKeyframeV1Request(BaseModel):
    """Request to add a keyframe to a clip.

    Keyframes define animation control points for transform interpolation.
    The time_ms is relative to clip start (0 = beginning of clip).
    If a keyframe already exists within 100ms of the specified time, it will be updated.

    Supports:
    - time_ms: Time relative to clip start in ms
    - transform: {x, y, scale, rotation}
    - opacity: Optional opacity override (0.0-1.0)
    - easing: Optional easing function name (e.g., 'linear', 'ease_in_out')
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    keyframe: AddKeyframeRequest

    def to_internal_request(self) -> AddKeyframeRequest:
        """Return the internal request (already in correct format)."""
        return self.keyframe


class DeleteKeyframeV1Request(BaseModel):
    """Request to delete a keyframe from a clip."""

    options: OperationOptions = Field(default_factory=OperationOptions)


# =============================================================================
# Priority 5: Batch and Semantic Request Models
# =============================================================================


class BatchOperationV1Request(BaseModel):
    """Request to execute multiple clip operations in a batch.

    Supports validate_only mode for dry-run validation.
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    operations: list[BatchClipOperation] = Field(
        description="List of operations to execute in order"
    )


class SemanticOperationV1Request(BaseModel):
    """Request to execute a semantic operation.

    Supports validate_only mode for dry-run validation.

    The semantic operation body can be provided under either key:
    - "semantic" (recommended): {"semantic": {"operation": "close_all_gaps", ...}}
    - "operation" (legacy):     {"operation": {"operation": "close_all_gaps", ...}}
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    semantic: SemanticOperation | None = Field(
        default=None,
        description="Semantic operation body (recommended key)",
    )
    operation: SemanticOperation | None = Field(
        default=None,
        description="Semantic operation body (legacy key, use 'semantic' instead)",
    )

    @model_validator(mode="after")
    def check_semantic_or_operation(self) -> "SemanticOperationV1Request":
        if self.semantic is None and self.operation is None:
            raise ValueError("Either 'semantic' or 'operation' field is required")
        return self

    @property
    def resolved_operation(self) -> SemanticOperation:
        """Return the semantic operation from whichever key was provided."""
        return self.semantic if self.semantic is not None else self.operation  # type: ignore[return-value]


async def get_user_project(
    project_id: UUID, current_user: CurrentUser, db: DbSession
) -> Project:
    """Get project with access verification (ownership or membership)."""
    return await get_accessible_project(project_id, current_user.id, db)


async def _resolve_edit_session(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: str | None = None,
) -> tuple["Project", "Sequence | None"]:
    """Resolve project and optional sequence from X-Edit-Session token.

    Always resolves to default sequence when no token is provided.
    """
    ctx = await get_edit_context(project_id, current_user, db, x_edit_session)
    return ctx.project, ctx.sequence


@contextmanager
def _use_sequence_timeline(project: "Project", sequence: "Sequence | None"):
    """Proxy project.timeline_data to sequence.timeline_data during the block.

    During the with-block, project.timeline_data points to sequence's data.
    After the block, any mutations are written back to sequence and flagged.
    If sequence is None, this is a no-op pass-through.
    """
    if sequence is None:
        yield
        return
    original = project.timeline_data
    project.timeline_data = sequence.timeline_data
    try:
        yield
    finally:
        _sanitize_timeline_ms(project.timeline_data)
        sequence.timeline_data = project.timeline_data
        flag_modified(sequence, "timeline_data")
        project.timeline_data = original


def compute_project_etag(project: Project) -> str:
    updated_at = project.updated_at
    if updated_at is None:
        return f'W/"{project.id}"'
    return f'W/"{project.id}:{updated_at.isoformat()}"'


def _match_id(full_id: str | None, search_id: str) -> bool:
    """Check if full_id matches search_id (exact match or prefix match).

    Supports partial ID matching like AIService.
    """
    if full_id is None:
        return False
    return full_id == search_id or full_id.startswith(search_id)


def _find_clip_state(project: Project, clip_id: str) -> tuple[dict | None, str | None]:
    """Find a clip's current state in the timeline.

    Supports partial ID matching like AIService.

    Returns tuple of (clip_data_with_layer_id, full_clip_id) or (None, None) if not found.
    """
    timeline = project.timeline_data or {}
    for layer in timeline.get("layers", []):
        for clip in layer.get("clips", []):
            full_id = clip.get("id")
            if _match_id(full_id, clip_id):
                return {**clip, "layer_id": layer.get("id")}, full_id
    return None, None


def _find_audio_clip_state(project: Project, clip_id: str) -> tuple[dict | None, str | None]:
    """Find an audio clip's current state in the timeline.

    Supports partial ID matching like AIService.

    Returns tuple of (clip_data_with_track_id, full_clip_id) or (None, None) if not found.
    """
    timeline = project.timeline_data or {}
    for track in timeline.get("audio_tracks", []):
        for clip in track.get("clips", []):
            full_id = clip.get("id")
            if _match_id(full_id, clip_id):
                return {**clip, "track_id": track.get("id")}, full_id
    return None, None


def _find_marker_state(project: Project, marker_id: str) -> tuple[dict | None, str | None]:
    """Find a marker's current state in the timeline.

    Supports partial ID matching like AIService.

    Returns tuple of (marker_data, full_marker_id) or (None, None) if not found.
    """
    timeline = project.timeline_data or {}
    for marker in timeline.get("markers", []):
        full_id = marker.get("id")
        if _match_id(full_id, marker_id):
            return marker, full_id
    return None, None


def _find_clip_ref(
    timeline: dict[str, Any],
    clip_id: str,
) -> tuple[dict | None, str | None]:
    """Find a clip reference in timeline for in-place updates."""
    for layer in timeline.get("layers", []):
        for clip in layer.get("clips", []):
            full_id = clip.get("id")
            if _match_id(full_id, clip_id):
                return clip, full_id
    return None, None


def _compute_chroma_preview_times(start_ms: int, duration_ms: int) -> list[int]:
    """Compute fixed 5-point preview times within a clip."""
    if duration_ms <= 0:
        raise ValueError("duration_ms must be > 0")

    ratios = (0.1, 0.3, 0.5, 0.7, 0.9)
    end_ms = start_ms + duration_ms
    last_ms = max(start_ms, end_ms - 1)

    times: list[int] = []
    for ratio in ratios:
        t = start_ms + int(duration_ms * ratio)
        if t < start_ms:
            t = start_ms
        if t > last_ms:
            t = last_ms
        times.append(t)
    return times


async def _asset_to_response(asset: Asset) -> AssetResponse:
    """Build AssetResponse with signed URL."""
    storage = get_storage_service()
    try:
        signed_url = await storage.get_signed_url(asset.storage_key, 15)
    except Exception:
        signed_url = asset.storage_url

    return AssetResponse(
        id=asset.id,
        project_id=asset.project_id,
        name=asset.name,
        type=asset.type,
        subtype=asset.subtype,
        storage_key=asset.storage_key,
        storage_url=signed_url,
        thumbnail_url=asset.thumbnail_url,
        duration_ms=asset.duration_ms,
        width=asset.width,
        height=asset.height,
        file_size=asset.file_size,
        mime_type=asset.mime_type,
        sample_rate=asset.sample_rate,
        channels=asset.channels,
        has_alpha=asset.has_alpha,
        chroma_key_color=asset.chroma_key_color,
        hash=asset.hash,
        is_internal=asset.is_internal,
        folder_id=asset.folder_id,
        created_at=asset.created_at,
        metadata=asset.asset_metadata,
    )


def _normalize_text_style_for_diff(text_style: dict | None) -> dict[str, Any]:
    """Normalize text_style keys to snake_case for diff payloads."""
    if not text_style:
        return {}

    key_map = {
        "fontFamily": "font_family",
        "fontSize": "font_size",
        "fontWeight": "font_weight",
        "textAlign": "text_align",
        "backgroundColor": "background_color",
        "backgroundOpacity": "background_opacity",
    }

    def _normalize_font_weight(value: Any) -> Any:
        if isinstance(value, str):
            lower = value.lower()
            if lower == "bold":
                return 700
            if lower == "normal":
                return 400
            try:
                return int(lower)
            except ValueError:
                return value
        return value

    normalized: dict[str, Any] = {}
    for key, value in text_style.items():
        out_key = key_map.get(key, key)
        if out_key == "font_weight":
            normalized[out_key] = _normalize_font_weight(value)
        else:
            normalized[out_key] = value
    return normalized


def envelope_success(context: RequestContext, data: object) -> EnvelopeResponse:
    meta: ResponseMeta = build_meta(context)
    return EnvelopeResponse(
        request_id=context.request_id,
        data=data,
        meta=meta,
    )


def envelope_error(
    context: RequestContext,
    *,
    code: str,
    message: str,
    status_code: int,
) -> JSONResponse:
    from src.constants.error_codes import get_error_spec

    meta: ResponseMeta = build_meta(context)
    spec = get_error_spec(code)
    error = ErrorInfo(
        code=code,
        message=message,
        retryable=spec.get("retryable", False),
        suggested_fix=spec.get("suggested_fix"),
    )
    envelope = EnvelopeResponse(
        request_id=context.request_id,
        error=error,
        meta=meta,
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(envelope.model_dump(exclude_none=True)),
    )


def envelope_error_from_exception(
    context: RequestContext,
    exc: DougaError,
) -> JSONResponse:
    """Convert a DougaError to an envelope error response."""
    meta: ResponseMeta = build_meta(context)
    error_info = exc.to_error_info()
    envelope = EnvelopeResponse(
        request_id=context.request_id,
        error=error_info,
        meta=meta,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(envelope.model_dump(exclude_none=True)),
    )


def _http_error_code(status_code: int, detail: str = "") -> str:
    """Map HTTP status code to V1 error code.

    For 400 errors, inspects the detail message to return more specific
    error codes (e.g. IDEMPOTENCY_MISSING) when possible.
    """
    if status_code == 400 and "Idempotency-Key" in detail:
        return "IDEMPOTENCY_MISSING"

    _mapping = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "PROJECT_NOT_FOUND",
        409: "CONCURRENT_MODIFICATION",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
    }
    return _mapping.get(status_code, "HTTP_ERROR")


@router.get("/capabilities", response_model=EnvelopeResponse)
async def get_capabilities(
    current_user: OptionalUser,
    response: Response,
    include: str = "all",
) -> EnvelopeResponse:
    """Get API capabilities.

    Args:
        include: Detail level.
                 "all" (default) returns full capabilities (~53KB).
                 "overview" returns a lightweight summary (~15KB) with semantic_operations
                 as names only and request_formats omitted.
                 "minimal" returns ultra-compact version (~5KB) with endpoint list,
                 semantic operation names, recommended_workflow, and authentication only.
                 Note: "minimal" is accessible without authentication.
    """
    # Unauthenticated access is only allowed for include=minimal
    if current_user is None and include != "minimal":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Authentication required for full capabilities. "
                "Use ?include=minimal for unauthenticated access, or provide "
                "an 'X-API-Key: douga_sk_...' or 'Authorization: Bearer <token>' header."
            ),
        )

    context = create_request_context()
    logger.info("v1.get_capabilities include=%s authenticated=%s", include, current_user is not None)

    capabilities = {
        "api_version": "1.0",
        "schema_version": "1.0-unified",  # Accepts both flat and nested clip formats
        "CRITICAL_HEADERS": {
            "X-API-Key": "REQUIRED on every request. Your API key (douga_sk_...).",
            "Idempotency-Key": (
                "REQUIRED on ALL write/mutation requests (POST, PATCH, DELETE, PUT). "
                "Must be a UUID v4 string (e.g., '550e8400-e29b-41d4-a716-446655440000'). "
                "Generate a new UUID for each distinct operation. "
                "If omitted, write requests will fail with IDEMPOTENCY_MISSING error."
            ),
            "Content-Type": "application/json (for all POST/PATCH/PUT requests)",
        },
        "authentication": {
            "methods": [
                {
                    "type": "api_key",
                    "header": "X-API-Key",
                    "format": "douga_sk_...",
                    "description": "Production API key (set via project settings)",
                },
                {
                    "type": "bearer",
                    "header": "Authorization",
                    "format": "Bearer <firebase_token>",
                    "description": "Firebase auth token",
                },
            ],
            "dev_mode": "In development mode, 'Bearer dev-token' is accepted",
        },
        "supported_read_endpoints": [
            # All read endpoints are implemented and available
            "GET /capabilities",
            "GET /version",
            "GET /projects",  # List all projects (id, name, created_at, ...)
            "POST /projects",  # Create a new project (name, width, height, fps)
            "GET /projects/{project_id}/overview",
            "GET /projects/{project_id}/structure",
            "GET /projects/{project_id}/timeline-overview",  # L2.5: Full overview
            "GET /projects/{project_id}/assets",
            # Priority 5: Advanced read endpoints
            "GET /projects/{project_id}/clips/{clip_id}",  # Single clip details
            "GET /projects/{project_id}/audio-clips/{clip_id}",  # Single audio clip details
            "GET /projects/{project_id}/at-time/{time_ms}",  # Timeline at specific time
            # Analysis endpoints (read)
            "GET /projects/{project_id}/analysis/gaps",  # Find gaps across layers/tracks
            "GET /projects/{project_id}/analysis/pacing",  # Clip density & pacing analysis
            # Schema definitions
            "GET /schemas",  # All available schema definitions with levels and endpoints
            # History and operation endpoints
            "GET /projects/{project_id}/history",  # Operation history
            "GET /projects/{project_id}/operations/{operation_id}",  # Operation details
            # Preview / visual inspection (POST but read-only, outside /api/ai/v1 — see preview_api section)
            "POST /api/projects/{project_id}/preview/event-points",  # Detect key events
            "POST /api/projects/{project_id}/preview/sample-frame",  # Render single frame (Base64 JPEG)
            "POST /api/projects/{project_id}/preview/sample-event-points",  # Events + frames in one call
            "POST /api/projects/{project_id}/preview/validate",  # Composition validation
        ],
        "supported_operations": [
            # Write operations currently implemented in v1
            # Priority 1: Clips
            "add_clip",  # POST /projects/{id}/clips
            "move_clip",  # PATCH /projects/{id}/clips/{clip_id}/move
            "transform_clip",  # PATCH /projects/{id}/clips/{clip_id}/transform
            "update_effects",  # PATCH /projects/{id}/clips/{clip_id}/effects
            "chroma_key_preview",  # POST /projects/{id}/clips/{clip_id}/chroma-key/preview
            "chroma_key_apply",  # POST /projects/{id}/clips/{clip_id}/chroma-key/apply
            "update_crop",  # PATCH /projects/{id}/clips/{clip_id}/crop
            "update_text_style",  # PATCH /projects/{id}/clips/{clip_id}/text-style
            "delete_clip",  # DELETE /projects/{id}/clips/{clip_id}
            # Priority 2: Layers
            "add_layer",  # POST /projects/{id}/layers
            "update_layer",  # PATCH /projects/{id}/layers/{layer_id}
            "reorder_layers",  # PUT /projects/{id}/layers/order
            # Priority 3: Audio
            "add_audio_clip",  # POST /projects/{id}/audio-clips
            "move_audio_clip",  # PATCH /projects/{id}/audio-clips/{clip_id}/move
            "update_audio_clip",  # PATCH /projects/{id}/audio-clips/{clip_id} (volume, fades, volume_keyframes)
            "delete_audio_clip",  # DELETE /projects/{id}/audio-clips/{clip_id}
            "add_audio_track",  # POST /projects/{id}/audio-tracks
            # Priority 4: Markers
            "add_marker",  # POST /projects/{id}/markers
            "update_marker",  # PATCH /projects/{id}/markers/{marker_id}
            "delete_marker",  # DELETE /projects/{id}/markers/{marker_id}
            # Priority 5: Advanced operations
            "batch",  # POST /projects/{id}/batch
            "semantic",  # POST /projects/{id}/semantic
            # Clip property updates
            "update_timing",  # PATCH /projects/{id}/clips/{clip_id}/timing (duration, speed, in/out points)
            "update_text",  # PATCH /projects/{id}/clips/{clip_id}/text (text content for text clips)
            "update_shape",  # PATCH /projects/{id}/clips/{clip_id}/shape (fill, stroke, dimensions)
            # Keyframe animation
            "add_keyframe",  # POST /projects/{id}/clips/{clip_id}/keyframes
            "delete_keyframe",  # DELETE /projects/{id}/clips/{clip_id}/keyframes/{keyframe_id}
            # Linked audio operations
            "split_clip",  # POST /projects/{id}/clips/{clip_id}/split
            "unlink_clip",  # POST /projects/{id}/clips/{clip_id}/unlink
            # History and rollback
            "rollback",  # POST /projects/{id}/operations/{op_id}/rollback
            # Preview diff
            "preview-diff",  # POST /projects/{id}/preview-diff
        ],
        "planned_operations": [
            # All write operations are now implemented in v1
        ],
        "planned_endpoints": [
            "POST /projects/{project_id}/analysis/composition",  # Full report: gaps, pacing, audio, layers, suggestions, score
            "POST /projects/{project_id}/analysis/suggestions",  # Lightweight: suggestions + quality_score only
            "POST /projects/{project_id}/analysis/sections",  # Detect logical sections/segments
            "POST /projects/{project_id}/analysis/audio-balance",  # Detailed audio balance analysis
        ],
        "operation_details": {
            "add_clip": {
                "description": "Add a clip to a layer. For video assets with linked audio, an audio clip is automatically placed on the narration track (set include_audio=false in options to skip).",
                "auto_behaviors": [
                    "Video clips: linked audio auto-placed on narration track (if available)",
                    "Smart positioning: clips get default position based on layer type",
                    "Group linking: video and audio clips share group_id for synchronized editing",
                ],
                "IMPORTANT_duplicate_audio_warning": "When adding a VIDEO clip, its linked audio is AUTO-PLACED on the narration track (unless include_audio=false in batch options or clip options). Additionally, when a video asset is REGISTERED (POST /assets step 3), an audio asset is auto-extracted and linked. This means the video asset's 'linked_audio_id' field points to an audio asset that was auto-created. To avoid DUPLICATE audio: (1) use include_audio=false in batch options when adding the video clip, AND (2) add the narration audio clip separately with POST /audio-clips. Always check GET /timeline-overview after adding clips to verify no duplicates exist.",
            },
        },
        "features": {
            "validate_only": True,
            "return_diff": True,  # Use options.include_diff=true to get diff in response
            "rollback": True,  # POST /operations/{id}/rollback
            "history": True,  # GET /history, GET /operations/{id}
        },
        "schema_notes": {
            "coordinate_system": {
                "description": "Clip position uses center-relative coordinates. (0,0) = canvas center (pixel 960,540 for 1920x1080).",
                "x": "Horizontal offset from center. 0=center, positive=right, negative=left. Range: -960 to +960 for on-screen.",
                "y": "Vertical offset from center. 0=center, positive=down, negative=up. Range: -540 to +540 for on-screen.",
                "safe_zone": "5% margin from edges. Safe x range: -864 to +864. Safe y range: -486 to +486.",
                "examples": {
                    "center": {"x": 0, "y": 0},
                    "top_left": {"x": -480, "y": -270},
                    "bottom_right": {"x": 480, "y": 270},
                    "bottom_subtitle": {"x": 0, "y": 380},
                },
            },
            "clip_format": "unified",  # Accepts both flat and nested formats
            "clip_id_format": (
                "Clip IDs in timeline-overview are short IDs (first 8 chars of UUID). "
                "Both short IDs and full UUIDs are accepted by all clip endpoints."
            ),
            "transform_formats": ["flat", "nested"],  # x/y/scale or transform.position/scale
            "flat_example": {"layer_id": "...", "x": 0, "y": 0, "scale": 1.0},
            "nested_example": {
                "type": "video",
                "layer_id": "...",
                "transform": {"position": {"x": 0, "y": 0}, "scale": {"x": 1, "y": 1}},
            },
            "transform_field_reference": {
                "description": "Complete list of supported fields for PATCH /clips/{id}/transform. "
                "All fields are optional (PATCH semantics: only provided fields are updated).",
                "flat_format_fields": {
                    "x": "float, -3840..3840 — X offset from canvas center in pixels (0 = center, positive = right)",
                    "y": "float, -2160..2160 — Y offset from canvas center in pixels (0 = center, positive = down)",
                    "scale": "float, 0.01..10.0 — Uniform scale factor (1.0 = original size)",
                    "width": "float, 1..7680 — Width in pixels (alternative to scale)",
                    "height": "float, 1..4320 — Height in pixels (alternative to scale)",
                    "rotation": "float, -360..360 — Rotation in degrees",
                    "anchor": "enum: center | top-left | top-right | bottom-left | bottom-right",
                },
                "nested_format_fields": {
                    "transform.position.x": "float — same as flat 'x'",
                    "transform.position.y": "float — same as flat 'y'",
                    "transform.scale.x": "float — used as uniform scale (scale.y is ignored, coerced to scale.x)",
                    "transform.rotation": "float — same as flat 'rotation'",
                },
                "important_notes": [
                    "scale_x and scale_y are NOT valid flat-format fields. Use 'scale' for uniform scaling.",
                    "Non-uniform scaling (different X/Y) is NOT supported. Nested scale.y is coerced to scale.x.",
                    "Use 'width'/'height' if you need to set exact pixel dimensions instead of a scale factor.",
                    "Flat fields take precedence over nested transform fields when both are provided.",
                ],
            },
            "supported_transform_fields": [
                "x", "y", "scale", "width", "height", "rotation", "anchor",
                "transform.position.x (nested)", "transform.position.y (nested)",
                "transform.scale.x (nested)", "transform.rotation (nested)",
            ],
            "chroma_key_preview_samples": [0.1, 0.3, 0.5, 0.7, 0.9],
            "unsupported_transform_fields": [
                "scale_x (use 'scale' instead)",
                "scale_y (use 'scale' instead)",
                "opacity (use PATCH /clips/{id}/effects instead)",
                "transform.opacity (not supported)",
                "transform.anchor (not yet supported in nested format; use flat 'anchor' field)",
                "Non-uniform scale (scale.y coerced to scale.x in nested format)",
            ],
            "unsupported_clip_fields": [
                "transition_in",
                "transition_out",
            ],
            "transitions_note": (
                "Transitions (fade, slide, wipe, etc.) between clips are NOT currently supported. "
                "The transition_in and transition_out fields in clip responses are always 'none'. "
                "To achieve fade effects, use the 'effects' endpoint: "
                "PATCH /clips/{id}/effects with {\"fade_in_ms\": 500, \"fade_out_ms\": 500}."
            ),
            "batch_operation_names": (
                "IMPORTANT: Batch operations use short names, NOT the endpoint names. "
                "Use: 'add' (not 'add_clip'), 'move' (not 'move_clip'), 'trim' (not 'update_timing'), "
                "'update_transform' (not 'transform_clip'), 'update_effects', 'delete' (not 'delete_clip'), "
                "'update_layer', 'update_text_style'. Data goes in the 'data' field. "
                "Example: {\"operation\": \"add\", \"data\": {\"layer_id\": \"...\", \"asset_id\": \"...\", "
                "\"start_ms\": 0, \"duration_ms\": 5000}}"
            ),
            "batch_add_transform_note": (
                "Batch 'add' operations support inline transform fields (x, y, scale) in the clip data. "
                "This avoids a separate update_transform call after adding. Example: "
                '{"operation": "add", "data": {"asset_id": "...", "layer_id": "...", '
                '"start_ms": 0, "duration_ms": 5000, "x": 0, "y": 0, "scale": 1.0}}'
            ),
            "effects_note": "Effects (opacity, fade, chroma_key, blend_mode) cannot be set directly in add_clip. Use PATCH /clips/{clip_id}/effects after adding the clip.",
            "text_style_note": "Unknown text_style keys preserved as-is (passthrough)",
            "text_style_color_format": "All color fields (color, background_color) must use hex format: #RRGGBB or #RRGGBBAA (with alpha). Example: '#FFFFFF' for white, '#00000080' for 50% transparent black. Do NOT use rgba(), rgb(), or named colors.",
            "semantic_operations": [
                {
                    "operation": "snap_to_previous",
                    "description": "Move a clip so it starts exactly where the previous clip ends (no gap).",
                    "required_fields": {
                        "target_clip_id": "ID of the clip to snap (at semantic level)",
                    },
                    "optional_fields": {},
                    "example": {
                        "semantic": {
                            "operation": "snap_to_previous",
                            "target_clip_id": "<clip-id>",
                            "parameters": {},
                        }
                    },
                },
                {
                    "operation": "snap_to_next",
                    "description": "Move the next clip so it starts exactly where this clip ends.",
                    "required_fields": {
                        "target_clip_id": "ID of the reference clip (at semantic level)",
                    },
                    "optional_fields": {},
                    "example": {
                        "semantic": {
                            "operation": "snap_to_next",
                            "target_clip_id": "<clip-id>",
                            "parameters": {},
                        }
                    },
                },
                {
                    "operation": "close_gap",
                    "description": "Close all gaps in a layer by shifting clips forward to remove spaces between them. Starts packing from time 0.",
                    "required_fields": {
                        "target_layer_id": "ID of the layer to close gaps in (at semantic level)",
                    },
                    "optional_fields": {},
                    "example": {
                        "semantic": {
                            "operation": "close_gap",
                            "target_layer_id": "<layer-id>",
                            "parameters": {},
                        }
                    },
                },
                {
                    "operation": "auto_duck_bgm",
                    "description": "Enable automatic volume ducking on all BGM tracks when narration is playing.",
                    "required_fields": {},
                    "optional_fields": {
                        "parameters.duck_to": "Target volume during ducking (float, default 0.1)",
                        "parameters.attack_ms": "Fade-down duration in ms (default 200)",
                        "parameters.release_ms": "Fade-up duration in ms (default 500)",
                    },
                    "example": {
                        "semantic": {
                            "operation": "auto_duck_bgm",
                            "parameters": {
                                "duck_to": 0.1,
                                "attack_ms": 200,
                                "release_ms": 500,
                            },
                        }
                    },
                },
                {
                    "operation": "rename_layer",
                    "description": "Rename a layer.",
                    "required_fields": {
                        "target_layer_id": "ID of the layer to rename (at semantic level)",
                        "parameters.name": "New name for the layer",
                    },
                    "optional_fields": {},
                    "example": {
                        "semantic": {
                            "operation": "rename_layer",
                            "target_layer_id": "<layer-id>",
                            "parameters": {"name": "Background Video"},
                        }
                    },
                },
                {
                    "operation": "replace_clip",
                    "description": "Replace a clip's asset while preserving timing and position. Linked audio clips are also updated if new_audio_asset_id is provided.",
                    "required_fields": {
                        "target_clip_id": "ID of the clip to replace (at semantic level)",
                        "parameters.new_asset_id": "UUID of the replacement asset",
                    },
                    "optional_fields": {
                        "parameters.new_audio_asset_id": "UUID of the replacement audio asset for linked audio clips",
                        "parameters.new_duration_ms": "New duration in ms if the asset has a different length",
                    },
                    "example": {
                        "semantic": {
                            "operation": "replace_clip",
                            "target_clip_id": "<clip-id>",
                            "parameters": {"new_asset_id": "<asset-uuid>"},
                        }
                    },
                },
                {
                    "operation": "close_all_gaps",
                    "description": "Remove all gaps in a layer by packing clips tightly from the first clip's position. Linked audio clips are synced automatically. Clips exceeding project boundary are trimmed.",
                    "required_fields": {
                        "target_layer_id": "ID of the layer to pack (at semantic level)",
                    },
                    "optional_fields": {
                        "parameters.max_end_ms": "Maximum allowed end position in ms (default: project duration_ms). Clips exceeding this are trimmed.",
                    },
                    "example": {
                        "semantic": {
                            "operation": "close_all_gaps",
                            "target_layer_id": "<layer-id>",
                            "parameters": {},
                        }
                    },
                },
                {
                    "operation": "add_text_with_timing",
                    "description": "Add a text/telop clip synced to an existing clip's timing (same start_ms and duration_ms). Automatically finds or creates a text layer.",
                    "required_fields": {
                        "target_clip_id": "ID of the clip to sync timing with (at semantic level)",
                        "parameters.text_content": "Text content to display",
                    },
                    "optional_fields": {
                        "parameters.text": "Text content (legacy alias for text_content)",
                        "parameters.font_size": "Font size in pixels (default 48)",
                        "parameters.position": "Vertical position: 'top' (y=200), 'center' (y=540), or 'bottom' (y=800). Default 'bottom'.",
                    },
                    "example": {
                        "semantic": {
                            "operation": "add_text_with_timing",
                            "target_clip_id": "<clip-id>",
                            "parameters": {"text_content": "Hello World"},
                        }
                    },
                },
                {
                    "operation": "distribute_evenly",
                    "description": "Distribute clips evenly in a layer with optional gap between them. Linked audio clips are synced automatically.",
                    "required_fields": {
                        "target_layer_id": "ID of the layer to distribute clips in (at semantic level)",
                    },
                    "optional_fields": {
                        "parameters.start_ms": "Starting position in ms (default: first clip's current start_ms)",
                        "parameters.gap_ms": "Gap in ms between clips (default 0)",
                    },
                    "example": {
                        "semantic": {
                            "operation": "distribute_evenly",
                            "target_layer_id": "<layer-id>",
                            "parameters": {"gap_ms": 500},
                        }
                    },
                },
            ],
            "batch_operation_types": [
                "add",
                "move",
                "trim",
                "update_transform",
                "update_effects",
                "delete",
                "update_layer",
                "update_text_style",
            ],
            "batch_add_example": {
                "description": (
                    "Add multiple clips at once using batch. Each 'add' operation needs a 'clip' key "
                    "with the same fields as POST /clips. Transform fields (x, y, scale) can be included "
                    "inline to position clips in a single operation without a separate update_transform call."
                ),
                "body": {
                    "operations": [
                        {
                            "operation": "add",
                            "clip": {
                                "asset_id": "uuid-of-asset",
                                "layer_id": "uuid-of-layer",
                                "start_ms": 0,
                                "duration_ms": 5000,
                                "x": 0,
                                "y": 0,
                                "scale": 1.0,
                            },
                        },
                        {
                            "operation": "add",
                            "clip": {
                                "asset_id": "uuid-of-asset",
                                "layer_id": "uuid-of-layer",
                                "start_ms": 5000,
                                "duration_ms": 3000,
                            },
                        },
                    ],
                    "options": {},
                },
            },
            "asset_notes": {
                "duration_ms": (
                    "All asset types have duration_ms populated. Images default to 5000ms (same as suggested_display_duration_ms). "
                    "Video and audio durations are auto-detected via server-side probing after upload (~15s). "
                    "You can use duration_ms directly for all asset types when creating clips."
                ),
                "suggested_display_duration_ms": (
                    "Image assets include a suggested_display_duration_ms field (default 5000ms / 5 seconds) "
                    "as a recommended slide display time. Video and audio assets return null for this field."
                ),
            },
            "options_requirement": "All mutation endpoints recommend an 'options' field in the request body. If omitted, options defaults to an empty object {}. It can contain: validate_only (bool), include_audio (bool).",
        },
        "limits": {
            "max_duration_ms": 3600000,
            "max_file_size_mb": 500,
            "max_layers": 5,
            "max_clips_per_layer": 100,
            "max_audio_tracks": 10,
            "max_batch_ops": 20,
        },
        "default_clip_values": {
            "effects": {
                "opacity": 1.0,
                "blend_mode": "normal",
                "fade_in_ms": 0,
                "fade_out_ms": 0,
                "chroma_key": {
                    "description": "These are the DEFAULT values shown for reference only. "
                    "IMPORTANT: To SET chroma key via PATCH /clips/{id}/effects, use FLAT fields, NOT this nested format. "
                    "See the chroma_key_usage example below.",
                    "enabled": False, "color": "#00FF00", "similarity": 0.3, "smoothness": 0.1,
                },
            },
            "transform": {
                "coordinate_system": "(0,0) = canvas center. Positive x = right, positive y = down.",
                "text_layer": {"x": 0, "y": 380, "scale": 1.0, "rotation": 0},
                "content_layer": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0},
                "background_layer": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0},
            },
        },
        "audio_features": {
            "volume_envelope": True,
            "volume_keyframe_format": {"time_ms": "int (relative to clip start)", "value": "float 0.0-1.0"},
            "interpolation": "linear",
        },
        "audio_track_types": ["narration", "bgm", "se", "video"],
        "effects": EFFECTS_CAPABILITIES["supported_effects"],
        "effect_params": EFFECTS_CAPABILITIES["effect_params"],
        "easings": sorted(EASING_FUNCTIONS.keys()),
        "blend_modes": ["normal"],
        "transitions": ["none"],
        "font_families": [
            "Noto Sans JP",
            "Noto Serif JP",
            "M PLUS 1p",
            "M PLUS Rounded 1c",
            "Kosugi Maru",
            "Sawarabi Gothic",
            "Sawarabi Mincho",
            "BIZ UDPGothic",
            "Zen Maru Gothic",
            "Shippori Mincho",
        ],
        "shape_types": ["rectangle", "circle", "line"],
        "text_aligns": ["left", "center", "right"],
        "track_types": ["narration", "bgm", "se", "video"],
        "preview_api": {
            "description": "Visual inspection APIs for AI-driven timeline verification without full renders. "
            "Use these to check composition visually before exporting.",
            "base_path": "/api/projects/{project_id}/preview",
            "note": "These endpoints are outside the /api/ai/v1 prefix. Use /api/projects/{project_id}/preview/... directly.",
            "endpoints": {
                "event_points": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/preview/event-points",
                    "description": "Detect key event points (clip boundaries, audio starts, section changes, silence gaps) for targeted inspection.",
                    "request_body": {
                        "include_audio": "bool (default true) — include audio events",
                        "include_visual": "bool (default true) — include visual layer events",
                        "min_gap_ms": "int (default 500) — minimum silence gap to detect",
                    },
                    "response": {
                        "event_points": "[{time_ms, event_type, description, layer?, clip_id?, metadata}]",
                        "total_events": "int",
                        "duration_ms": "int",
                    },
                    "event_types": [
                        "clip_start", "clip_end", "slide_change", "section_boundary",
                        "avatar_enter", "avatar_exit", "narration_start", "narration_end",
                        "bgm_start", "se_trigger", "silence_gap", "effect_point", "layer_change",
                    ],
                },
                "sample_frame": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/preview/sample-frame",
                    "description": "Render a single preview frame at a specific time. Returns a Base64-encoded JPEG image (~30-80KB at 640x360).",
                    "request_body": {
                        "time_ms": "int (required) — time position in milliseconds",
                        "resolution": "str (default '640x360') — output resolution WxH",
                    },
                    "response": {
                        "time_ms": "int",
                        "resolution": "str",
                        "frame_base64": "str — Base64-encoded JPEG",
                        "size_bytes": "int",
                        "active_clips": "[{clip_id, layer_name, asset_name, clip_type, transform, progress_percent}]",
                    },
                },
                "sample_event_points": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/preview/sample-event-points",
                    "description": "Auto-detect event points and render preview frames at each in one call. "
                    "Best for getting an overview of the entire timeline.",
                    "request_body": {
                        "max_samples": "int (default 10) — maximum frames to sample",
                        "resolution": "str (default '640x360') — output resolution WxH",
                        "include_audio": "bool (default true) — include audio events",
                        "min_gap_ms": "int (default 500) — minimum silence gap",
                    },
                    "response": {
                        "samples": "[{time_ms, event_type, description, frame_base64, active_clips}]",
                        "total_events": "int",
                        "sampled_count": "int",
                    },
                },
                "validate": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/preview/validate",
                    "description": "Check composition rules without rendering. Detects overlapping clips, missing assets, safe zone violations, and audio-visual sync issues.",
                    "request_body": {
                        "rules": "list[str] | null (default null = all rules)",
                    },
                    "response": {
                        "is_valid": "bool — true if no errors",
                        "issues": "[{rule, severity, message, time_ms?, clip_id?, suggestion?}]",
                        "total_issues": "int",
                        "errors": "int",
                        "warnings": "int",
                    },
                },
            },
            "workflow_tips": [
                "1. Call validate first to check for structural issues",
                "2. Call sample-event-points for a visual overview of key moments",
                "3. Call sample-frame for targeted inspection at specific times",
                "4. Use X-Edit-Session header to preview unsaved sequence edits",
            ],
        },
        "ai_video_api": {
            "description": "AI-driven video production pipeline. Handles asset upload, plan generation, "
            "and automated skills (silence trimming, telop, layout, sync, click highlights, avatar dodge).",
            "base_path": "/api/ai-video",
            "note": "Outside /api/ai/v1 prefix. Use /api/ai-video/... directly.",
            "endpoints": {
                "capabilities": {
                    "method": "GET",
                    "path": "/api/ai-video/capabilities",
                    "description": "Full workflow guide with skill specs and dependency graph.",
                },
                "batch_upload": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/assets/batch-upload",
                    "description": "Upload multiple files with auto-classification and metadata probing (multipart).",
                },
                "asset_catalog": {
                    "method": "GET",
                    "path": "/api/ai-video/projects/{project_id}/asset-catalog",
                    "description": "AI-oriented asset catalog with type/subtype summary.",
                },
                "reclassify": {
                    "method": "PUT",
                    "path": "/api/ai-video/projects/{project_id}/assets/{asset_id}/reclassify",
                    "description": "Manually fix asset type/subtype classification.",
                },
                "transcription": {
                    "method": "GET",
                    "path": "/api/ai-video/projects/{project_id}/assets/{asset_id}/transcription",
                    "description": "Get STT transcription for an audio asset. "
                    "Auto-generated on upload for assets with speech (check has_transcription in asset catalog). "
                    "Returns {language, full_text, segments: [{text, start_ms, end_ms, confidence, type}], "
                    "total_segments, speech_segments, silence_segments}.",
                },
                "generate_plan": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/plan/generate",
                    "description": "Generate a VideoPlan from brief + asset catalog using AI (GPT-4o).",
                },
                "get_plan": {
                    "method": "GET",
                    "path": "/api/ai-video/projects/{project_id}/plan",
                    "description": "Get current video plan.",
                },
                "update_plan": {
                    "method": "PUT",
                    "path": "/api/ai-video/projects/{project_id}/plan",
                    "description": "Replace the video plan.",
                },
                "apply_plan": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/plan/apply",
                    "description": "Convert plan to timeline_data with audio extraction and chroma key.",
                },
                "skill_trim_silence": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/trim-silence",
                    "description": "Trim leading/trailing silence from narration and linked avatar clips.",
                },
                "skill_add_telop": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/add-telop",
                    "description": "Transcribe narration (Whisper STT) and place text clips on text layer.",
                },
                "skill_layout": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/layout",
                    "description": "Apply layout transforms. Accepts optional avatar_position, avatar_size, screen_position.",
                },
                "skill_sync_content": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/sync-content",
                    "description": "Variable-speed sync of operation screen to narration timing.",
                },
                "skill_click_highlight": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/click-highlight",
                    "description": "Detect clicks in operation screen and add highlight shapes.",
                },
                "skill_avatar_dodge": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/avatar-dodge",
                    "description": "Add dodge keyframes to avatar when click highlights overlap.",
                },
                "skill_run_all": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/run-all",
                    "description": "Run all 6 skills in dependency order in one call. Stops on first failure.",
                },
                "check": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/check",
                    "description": "Quality check: structure, plan-vs-actual, sync, gaps. Levels: quick/standard/deep.",
                },
            },
            "skill_order": [
                "trim-silence", "add-telop", "layout",
                "sync-content", "click-highlight", "avatar-dodge",
            ],
        },
        "render_api": {
            "description": "Async video rendering with progress tracking and download.",
            "base_path": "/api/projects/{project_id}/render",
            "note": "Outside /api/ai/v1 prefix. Use /api/projects/{project_id}/render/... directly.",
            "endpoints": {
                "start": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/render",
                    "description": "Start a render job. Supports start_ms/end_ms for partial export and X-Edit-Session for sequence rendering.",
                },
                "status": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/render/status",
                    "description": "Poll latest render job progress (status, progress %, stage).",
                },
                "cancel": {
                    "method": "DELETE",
                    "path": "/api/projects/{project_id}/render",
                    "description": "Cancel an active render job.",
                },
                "history": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/render/history",
                    "description": "List recent completed renders (up to 10) with signed download URLs.",
                },
                "download": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/render/download",
                    "description": "Get signed download URL for the latest completed render.",
                },
                "package": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/render/package",
                    "description": "Generate a client-side render package (ZIP with assets + FFmpeg scripts). "
                    "No FFmpeg execution on server — download ZIP and run locally with 'bash render.sh'. "
                    "Returns {download_url, package_size, expires_at}.",
                },
            },
        },
        "sequences_api": {
            "description": "Multi-sequence timeline editing with optimistic locking and snapshots.",
            "base_path": "/api/projects/{project_id}/sequences",
            "note": "Outside /api/ai/v1 prefix. Use /api/projects/{project_id}/sequences/... directly. "
            "V1 endpoints support X-Edit-Session header to target a specific sequence.",
            "endpoints": {
                "list": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/sequences",
                    "description": "List all sequences for a project.",
                },
                "create": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences",
                    "description": "Create a new sequence with empty timeline.",
                },
                "copy": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/copy",
                    "description": "Copy a sequence with its timeline data.",
                },
                "get_default": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/sequences/default",
                    "description": "Get the default sequence ID.",
                },
                "get": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}",
                    "description": "Get sequence with full timeline data.",
                },
                "update": {
                    "method": "PUT",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}",
                    "description": "Save timeline data (requires lock + version match).",
                },
                "delete": {
                    "method": "DELETE",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}",
                    "description": "Delete a sequence (cannot delete default).",
                },
                "lock": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/lock",
                    "description": "Acquire edit lock. Returns edit_token for X-Edit-Session header.",
                },
                "heartbeat": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/heartbeat",
                    "description": "Keep lock alive (call every 30s). Lock expires after 2 min without heartbeat.",
                },
                "unlock": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/unlock",
                    "description": "Release edit lock.",
                },
                "list_snapshots": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/snapshots",
                    "description": "List checkpoints (snapshots) for a sequence.",
                },
                "create_snapshot": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/snapshots",
                    "description": "Create a checkpoint of current sequence state.",
                },
                "restore_snapshot": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/snapshots/{snapshot_id}/restore",
                    "description": "Restore sequence from a checkpoint (requires lock).",
                },
                "delete_snapshot": {
                    "method": "DELETE",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/snapshots/{snapshot_id}",
                    "description": "Delete a checkpoint.",
                },
            },
        },
        "workflow_examples": {
            "add_title_text": {
                "description": "テロップ/タイトルテキストを追加",
                "steps": [
                    "POST /clips with layer_id=text-layer, start_ms, duration_ms, text_content",
                    "PATCH /clips/{id}/text-style with font_size, font_color, background_color",
                    "PATCH /clips/{id}/effects with fade_in_ms=200, fade_out_ms=200",
                ],
            },
            "add_video_with_audio": {
                "description": "動画を音声付きで配置",
                "steps": [
                    "POST /clips with layer_id, asset_id, start_ms, duration_ms (audio auto-placed)",
                    "Verify with GET /timeline-overview",
                ],
            },
            "improve_pacing": {
                "description": "ペーシングの改善",
                "steps": [
                    "GET /analysis/pacing to identify slow sections",
                    "GET /analysis/gaps to find empty spaces",
                    "Add section markers (text clips) at transition points",
                    "Add fade effects to smoothen transitions",
                ],
            },
        },
        "recommended_workflow": [
            "1. GET /api/ai/v1/capabilities?include=minimal — discover API (use ?include=all for full details)",
            "2. POST /api/ai/v1/projects — create a new project (or GET /projects to list existing)",
            "3. Upload assets via /api/projects/{id}/assets/upload-url + PUT + POST /api/projects/{id}/assets",
            "4. GET /api/ai/v1/projects/{id}/assets — list available assets (wait ~15s after upload for probing)",
            "5. GET /api/ai/v1/projects/{id}/timeline-overview — full timeline (add ?include_snapshot=true for visual snapshot)",
            "6. POST /api/projects/{id}/preview/sample-event-points — key frame images",
            "7. Use add_clip, move_clip, batch, semantic etc. to edit",
            "8. POST /api/projects/{id}/preview/validate — check composition",
            "9. POST /api/projects/{id}/render — export final video",
        ],
        "asset_layer_mapping": {
            "slide": {"recommended_layer": "content", "description": "Slide images go on the Content layer"},
            "avatar": {"recommended_layer": "avatar", "description": "Avatar videos go on the Avatar layer (supports chroma key)"},
            "background": {"recommended_layer": "background", "description": "Background images/videos go on the Background layer"},
            "screen_recording": {"recommended_layer": "content", "description": "Screen recordings go on the Content layer"},
            "other": {"recommended_layer": "content", "description": "General assets default to the Content layer"},
        },
        "duration_tip": (
            "All assets have duration_ms populated: video/audio assets are probed server-side after upload; "
            "image assets default to 5000ms (matching suggested_display_duration_ms). "
            "If video/audio duration_ms is null, wait ~15 seconds and re-fetch GET /assets. "
            "Use the duration_ms value directly when creating clips."
        ),
        "metadata_probing": (
            "All uploaded assets are automatically probed server-side: "
            "video/audio -> duration_ms, width, height, sample_rate, channels; "
            "image -> width, height. Auto-extracted audio from video also gets duration. "
            "Probing takes 3-10 seconds after upload."
        ),
        "asset_upload_guide": {
            "description": "3-step process to upload and register an asset.",
            "steps": [
                {
                    "step": 1,
                    "action": "Get signed upload URL",
                    "method": "POST",
                    "path": "/api/projects/{project_id}/assets/upload-url?filename={url_encoded_filename}&content_type={mime_type}",
                    "response_fields": {
                        "upload_url": "Signed URL to PUT the file to",
                        "storage_key": "SAVE THIS — needed for step 3 registration",
                        "expires_at": "URL expiration time",
                    },
                },
                {
                    "step": 2,
                    "action": "Upload file binary to the signed URL",
                    "method": "PUT",
                    "url": "The upload_url from step 1",
                    "headers": {"Content-Type": "the same mime_type used in step 1"},
                    "body": "Raw file bytes (binary upload, NOT multipart form)",
                },
                {
                    "step": 3,
                    "action": "Register asset metadata in the database",
                    "method": "POST",
                    "path": "/api/projects/{project_id}/assets",
                    "body_fields": {
                        "name": "(string, REQUIRED) Display name for the asset",
                        "type": "(string, REQUIRED) One of: 'video', 'audio', 'image'",
                        "subtype": "(string, REQUIRED) One of: 'avatar', 'background', 'slide', 'narration', 'bgm', 'se', 'effect', 'other'. NOTE: field is 'subtype' (NOT 'sub_type')",
                        "storage_key": "(string, REQUIRED) The storage_key value from step 1 response. NOTE: field is 'storage_key' (NOT 'blob_name')",
                        "storage_url": "(string, REQUIRED) Use the same value as storage_key — server resolves it",
                        "file_size": "(int, REQUIRED) File size in bytes",
                        "mime_type": "(string, REQUIRED) MIME type (e.g., 'video/mp4', 'image/png', 'audio/mpeg')",
                    },
                    "example_body": {
                        "name": "intro_avatar.mp4",
                        "type": "video",
                        "subtype": "avatar",
                        "storage_key": "projects/abc123/assets/def456.mp4",
                        "storage_url": "projects/abc123/assets/def456.mp4",
                        "file_size": 4642385,
                        "mime_type": "video/mp4",
                    },
                    "common_mistakes": [
                        "Using 'blob_name' instead of 'storage_key' — the field is 'storage_key'",
                        "Using 'sub_type' instead of 'subtype' — the field is 'subtype' (no underscore)",
                        "Forgetting to wait 15s after registration for server-side probing to complete",
                    ],
                },
            ],
        },
        "idempotency": {
            "description": (
                "IMPORTANT: All write/mutation requests (POST, PATCH, DELETE, PUT that modify data) "
                "REQUIRE an Idempotency-Key header. Requests without this header will be REJECTED "
                "with a 400 IDEMPOTENCY_MISSING error. This is not optional."
            ),
            "header": "Idempotency-Key",
            "format": "UUID v4 string",
            "example": "550e8400-e29b-41d4-a716-446655440000",
            "behavior": "If the same key is sent twice, the second request returns the cached result.",
            "when_required": "Every POST/PATCH/DELETE/PUT that modifies project data (clips, layers, audio, batch, semantic, markers, etc.)",
            "how_to_generate": "Use any UUID v4 generator. Each distinct operation needs a unique key.",
        },
        "request_formats": {
            "note": "All mutation endpoints recommend an 'options' field (defaults to empty {} if omitted). Write endpoints should include an 'Idempotency-Key' header for safe retries.",
            "common_headers": {
                "X-API-Key": "Required. Your API key.",
                "Idempotency-Key": "Recommended for all write operations. UUID string to prevent duplicate operations. If omitted, the operation is not idempotent.",
                "Content-Type": "application/json",
                "If-Match": "Optional. ETag value for optimistic concurrency control.",
            },
            "endpoints": {
                "POST /clips": {
                    "body": {"clip": {"asset_id": "uuid", "layer_id": "uuid", "start_ms": 0, "duration_ms": 5000}, "options": {}},
                    "notes": "For text clips, use 'text_content' (not 'text') and omit asset_id. Flat body (without 'clip' wrapper) is auto-wrapped.",
                    "text_clip_example": {"clip": {"layer_id": "uuid", "start_ms": 0, "duration_ms": 5000, "text_content": "Your text here"}, "options": {}},
                },
                "PATCH /clips/{id}/move": {
                    "body": {"move": {"new_start_ms": 5000}, "options": {}},
                },
                "PATCH /clips/{id}/timing": {
                    "body": {"timing": {"duration_ms": 5000, "in_point_ms": 0, "out_point_ms": 5000}, "options": {}},
                    "notes": "Cannot change start_ms here. Use /move endpoint instead.",
                },
                "PATCH /clips/{id}/effects": {
                    "body": {"effects": {"opacity": 0.8, "fade_in_ms": 500, "fade_out_ms": 500}, "options": {}},
                    "chroma_key_example": {
                        "body": {
                            "effects": {
                                "chroma_key_enabled": True,
                                "chroma_key_color": "#00FF00",
                                "chroma_key_similarity": 0.3,
                                "chroma_key_blend": 0.1,
                            },
                            "options": {},
                        },
                    },
                    "common_mistakes": [
                        "Using nested format {\"chroma_key\": {\"enabled\": true, \"color\": \"#00FF00\"}} -- this is SILENTLY IGNORED. "
                        "Use FLAT fields: {\"chroma_key_enabled\": true, \"chroma_key_color\": \"#00FF00\", ...}",
                    ],
                },
                "PATCH /clips/{id}/transform": {
                    "body": {"transform": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0}, "options": {}},
                    "notes": "Coordinate system: (0,0) = canvas center. Positive x = right, positive y = down. "
                    "Supported fields: x, y, scale, width, height, rotation, anchor. "
                    "scale_x/scale_y are NOT valid — use 'scale' (uniform) or 'width'/'height' for sizing.",
                },
                "PATCH /clips/{id}/text": {
                    "body": {"text": {"text_content": "Hello"}, "options": {}},
                },
                "PATCH /clips/{id}/text-style": {
                    "body": {"text_style": {"font_size": 48, "font_family": "Noto Sans JP", "color": "#FFFFFF"}, "options": {}},
                },
                "DELETE /clips/{id}": {
                    "body": {"options": {}},
                },
                "POST /clips/{id}/split": {
                    "body": {"split_at_ms": 5000, "options": {}},
                },
                "POST /clips/{id}/unlink": {
                    "body": {"options": {}},
                },
                "POST /audio-clips": {
                    "body": {"clip": {"asset_id": "uuid", "track_id": "uuid", "start_ms": 0, "duration_ms": 5000}, "options": {}},
                },
                "PATCH /audio-clips/{id}/move": {
                    "body": {"new_start_ms": 5000, "options": {}},
                    "notes": "Audio move uses flat format (not nested in 'move' key).",
                },
                "POST /layers": {
                    "body": {"layer": {"name": "My Layer", "type": "content"}, "options": {}},
                },
                "POST /audio-tracks": {
                    "body": {"track": {"name": "BGM", "type": "bgm"}, "options": {}},
                },
                "POST /markers": {
                    "body": {"marker": {"name": "Section Start", "time_ms": 5000, "color": "#FF0000"}, "options": {}},
                    "notes": "Use 'name' field (not 'label').",
                },
                "POST /semantic": {
                    "body": {"semantic": {"operation": "close_all_gaps", "target_layer_id": "uuid", "parameters": {}}, "options": {}},
                    "notes": "Recommended key is 'semantic'. Legacy key 'operation' is also accepted for backward compatibility.",
                },
                "POST /batch": {
                    "body": {
                        "operations": [
                            {"operation": "move", "clip_id": "uuid", "move": {"new_start_ms": 5000}},
                            {"operation": "update_effects", "clip_id": "uuid", "effects": {"opacity": 0.5}},
                            {"operation": "update_text_style", "clip_id": "uuid", "text_style": {"font_size": 48, "color": "#FFFFFF"}},
                        ],
                        "options": {"validate_only": False, "rollback_on_failure": False},
                    },
                    "notes": "Operation parameters can use endpoint-specific keys (effects, timing, transform, "
                    "text_style, move, text, clip) or the generic 'data' key. "
                    "Endpoint-specific keys are recommended as they match the direct API endpoints. "
                    "clip_id stays at top level.",
                },
                "POST /preview-diff": {
                    "body": {
                        "operation_type": "move",
                        "clip_id": "uuid-prefix",
                        "parameters": {"new_start_ms": 5000},
                    },
                    "notes": "Simulates an operation and returns before/after diff without modifying timeline. "
                    "Supported operation_types: move, trim, delete, close_all_gaps, distribute_evenly, add_text_with_timing.",
                },
            },
        },
    }

    # Promote semantic details to top level for AI discoverability
    capabilities["semantic_operations"] = capabilities["schema_notes"]["semantic_operations"]

    if include == "overview":
        # Lightweight mode: reduce semantic_operations to name list,
        # replace request_formats with compact body skeletons
        capabilities["semantic_operations"] = [
            op["operation"] for op in capabilities["schema_notes"]["semantic_operations"]
        ]
        capabilities["schema_notes"]["semantic_operations"] = capabilities["semantic_operations"]
        # Replace verbose request_formats with compact body skeletons
        capabilities.pop("request_formats", None)
        capabilities["request_formats_compact"] = {
            "IMPORTANT": "All write requests REQUIRE 'Idempotency-Key: <uuid>' header. Omitting causes 400 error.",
            "note": "Body skeletons for each mutation endpoint. "
            "All bodies optionally accept an 'options' field (defaults to {}). Use ?include=all for full details.",
            "POST /clips": {"clip": {"layer_id": "uuid", "asset_id": "uuid", "start_ms": 0, "duration_ms": 1000}, "options": {}},
            "PATCH /clips/{id}/move": {"move": {"new_start_ms": 0}, "options": {}},
            "PATCH /clips/{id}/timing": {"timing": {"duration_ms": 5000, "in_point_ms": 0, "out_point_ms": 5000}, "options": {}},
            "PATCH /clips/{id}/effects": {
                "effects": {"opacity": 1.0, "fade_in_ms": 0, "fade_out_ms": 0},
                "options": {},
                "_chroma_key_note": "For chroma key, use FLAT fields in effects: chroma_key_enabled, chroma_key_color, chroma_key_similarity, chroma_key_blend. Do NOT use nested {chroma_key: {enabled: ...}} format.",
            },
            "PATCH /clips/{id}/transform": {"transform": {"x": 0, "y": 0, "scale": 1.0}, "options": {}},
            "PATCH /clips/{id}/text": {"text": {"text_content": "Hello"}, "options": {}},
            "PATCH /clips/{id}/text-style": {"text_style": {"font_size": 48, "font_family": "Noto Sans JP", "color": "#FFFFFF"}, "options": {}},
            "DELETE /clips/{id}": {"options": {}},
            "POST /clips/{id}/split": {"split_at_ms": 5000, "options": {}},
            "POST /audio-clips": {"clip": {"asset_id": "uuid", "track_id": "uuid", "start_ms": 0, "duration_ms": 5000}, "options": {}},
            "PATCH /audio-clips/{id}": {"audio": {"volume": 0.3, "fade_in_ms": 500, "fade_out_ms": 1000}, "options": {}},
            "POST /layers": {"layer": {"name": "My Layer", "type": "content"}, "options": {}},
            "POST /audio-tracks": {"track": {"name": "BGM", "type": "bgm"}, "options": {}},
            "POST /markers": {"marker": {"name": "Section Start", "time_ms": 5000, "color": "#FF0000"}, "options": {}},
            "POST /semantic (snap_to_previous)": {"semantic": {"operation": "snap_to_previous", "target_clip_id": "<clip-id>"}},
            "POST /semantic (snap_to_next)": {"semantic": {"operation": "snap_to_next", "target_clip_id": "<clip-id>"}},
            "POST /semantic (close_gap)": {"semantic": {"operation": "close_gap", "target_layer_id": "<layer-id>"}},
            "POST /semantic (close_all_gaps)": {"semantic": {"operation": "close_all_gaps", "target_layer_id": "<layer-id>"}},
            "POST /semantic (add_text_with_timing)": {"semantic": {"operation": "add_text_with_timing", "target_clip_id": "<clip-id>", "parameters": {"text_content": "Your text here"}}},
            "POST /semantic (rename_layer)": {"semantic": {"operation": "rename_layer", "target_layer_id": "<layer-id>", "parameters": {"name": "New Layer Name"}}},
            "POST /semantic (distribute_evenly)": {"semantic": {"operation": "distribute_evenly", "target_layer_id": "<layer-id>"}},
            "POST /semantic (replace_clip)": {"semantic": {"operation": "replace_clip", "target_clip_id": "<clip-id>", "parameters": {"new_asset_id": "<asset-id>"}}},
            "POST /semantic (auto_duck_bgm)": {"semantic": {"operation": "auto_duck_bgm"}},
            "POST /batch": {
                "operations": [
                    {"operation": "update_effects", "clip_id": "uuid", "effects": {"fade_in_ms": 500}},
                    {"operation": "move", "clip_id": "uuid", "move": {"new_start_ms": 5000}},
                    {"operation": "update_text_style", "clip_id": "uuid", "text_style": {"font_size": 48, "color": "#FFFFFF"}},
                ],
                "options": {},
            },
            "POST /preview-diff": {"operation_type": "move", "clip_id": "uuid", "parameters": {"new_start_ms": 5000}},
        }
        # Trim preview_api endpoint details to just method+path+description
        if "preview_api" in capabilities and "endpoints" in capabilities["preview_api"]:
            for _ep_key, ep_val in capabilities["preview_api"]["endpoints"].items():
                for verbose_key in ("request_body", "response", "event_types"):
                    ep_val.pop(verbose_key, None)
        # Trim ai_video_api endpoint details
        if "ai_video_api" in capabilities and "endpoints" in capabilities["ai_video_api"]:
            for _ep_key, ep_val in capabilities["ai_video_api"]["endpoints"].items():
                for verbose_key in ("request_body", "response"):
                    ep_val.pop(verbose_key, None)
        # Trim workflow_examples to just descriptions
        if "workflow_examples" in capabilities:
            capabilities["workflow_examples"] = {
                k: v.get("description", k) for k, v in capabilities["workflow_examples"].items()
            }
        context.warnings.append(
            "Overview mode: request_formats replaced with compact body skeletons. "
            "Use ?include=all for full details."
        )

    elif include == "minimal":
        # Ultra-compact mode (<5KB target): only what an agent needs to get started.
        # Endpoints as compact strings, semantic ops as name-only list,
        # workflow as 3-line summary, limits trimmed to essentials.
        read_endpoints = [
            "GET /capabilities",
            "GET /version",
            "GET /projects",
            "POST /projects",  # Create a new project
            "GET /projects/{id}/overview",
            "GET /projects/{id}/structure",
            "GET /projects/{id}/timeline-overview",
            "GET /projects/{id}/assets",
            "GET /projects/{id}/clips/{cid}",
            "GET /projects/{id}/audio-clips/{cid}",
            "GET /projects/{id}/at-time/{ms}",
            "GET /projects/{id}/analysis/gaps",
            "GET /projects/{id}/analysis/pacing",
            "GET /projects/{id}/history",
            "GET /schemas",
        ]
        write_endpoints = [
            "POST /projects/{id}/clips",
            "PATCH /projects/{id}/clips/{cid}/move",
            "PATCH /projects/{id}/clips/{cid}/transform",
            "PATCH /projects/{id}/clips/{cid}/effects",
            "PATCH /projects/{id}/clips/{cid}/timing",
            "PATCH /projects/{id}/clips/{cid}/text",
            "PATCH /projects/{id}/clips/{cid}/text-style",
            "PATCH /projects/{id}/clips/{cid}/crop",
            "DELETE /projects/{id}/clips/{cid}",
            "POST /projects/{id}/clips/{cid}/split",
            "POST /projects/{id}/layers",
            "PATCH /projects/{id}/layers/{lid}",
            "PUT /projects/{id}/layers/order",
            "POST /projects/{id}/audio-clips",
            "PATCH /projects/{id}/audio-clips/{cid}/move",
            "PATCH /projects/{id}/audio-clips/{cid}",
            "DELETE /projects/{id}/audio-clips/{cid}",
            "POST /projects/{id}/audio-tracks",
            "POST /projects/{id}/markers",
            "POST /projects/{id}/batch",
            "POST /projects/{id}/semantic",
            "POST /projects/{id}/preview-diff",
        ]
        # Name-only list (use ?include=all for descriptions)
        semantic_ops = [
            op["operation"] for op in capabilities["schema_notes"]["semantic_operations"]
        ]

        capabilities = {
            "api_version": capabilities["api_version"],
            "schema_version": capabilities["schema_version"],
            "auth": {"header": "X-API-Key or Authorization: Bearer <token>"},
            "CRITICAL_HEADERS": {
                "Idempotency-Key": "REQUIRED on ALL write requests (UUID v4). Omitting causes 400 IDEMPOTENCY_MISSING error.",
            },
            "endpoints": {
                "read": read_endpoints,
                "write": write_endpoints,
            },
            "semantic_operations": semantic_ops,
            "workflow": "1) GET /capabilities?include=minimal 2) POST /projects to create or GET /projects to list 3) Upload assets → GET /assets (wait 15s for probing) 4) GET /timeline-overview 5) Edit via clips/semantic/batch endpoints",
            "limits": {
                "max_layers": 5,
                "max_clips_per_layer": 100,
                "max_batch_ops": 20,
            },
            "note": "Use ?include=all for full details.",
        }
        context.warnings.append(
            "Minimal mode: most details omitted. "
            "Use ?include=all or ?include=overview for more details."
        )

    # Version-based ETag for semi-static capabilities (changes only on deploy)
    from src.config import get_settings as _get_settings
    _settings = _get_settings()
    response.headers["ETag"] = f'W/"capabilities:{_settings.app_version}:{include}"'

    return envelope_success(context, capabilities)


@router.get("/version", response_model=EnvelopeResponse)
async def get_version(
    current_user: CurrentUser,
) -> EnvelopeResponse:
    context = create_request_context()
    logger.info("v1.get_version")
    data = {
        "api_version": "1.0",
        "schema_version": "1.0-unified",  # Must match /capabilities
    }
    return envelope_success(context, data)


@router.get("/projects", response_model=EnvelopeResponse)
async def list_projects_v1(
    current_user: CurrentUser,
    db: DbSession,
) -> EnvelopeResponse:
    """List all projects accessible to the current user.

    Returns a lightweight list with id, name, and created_at for each project.
    Includes both owned and shared (accepted) projects, ordered by most recently updated.
    """
    context = create_request_context()
    logger.info("v1.list_projects user=%s", current_user.id)

    result = await db.execute(
        select(Project)
        .where(
            or_(
                Project.user_id == current_user.id,
                Project.id.in_(
                    select(ProjectMember.project_id).where(
                        ProjectMember.user_id == current_user.id,
                        ProjectMember.accepted_at.isnot(None),
                    )
                ),
            )
        )
        .order_by(Project.updated_at.desc())
    )
    projects = result.scalars().all()

    projects_data = [
        {
            "id": str(p.id),
            "name": p.name,
            "description": p.description,
            "status": p.status,
            "duration_ms": p.duration_ms,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in projects
    ]

    return envelope_success(context, {"projects": projects_data, "total": len(projects_data)})


# =============================================================================
# V1 Project Creation (mirrors /api/projects for AI-native workflow)
# =============================================================================


class CreateProjectV1Request(BaseModel):
    """Request to create a project via V1 API."""

    name: str = Field(..., min_length=1, max_length=255, description="Project name")
    description: str | None = Field(default=None, description="Project description")
    width: int = Field(default=1920, ge=256, le=4096, description="Canvas width in pixels (must be even)")
    height: int = Field(default=1080, ge=256, le=4096, description="Canvas height in pixels (must be even)")
    fps: int = Field(default=30, ge=15, le=60, description="Frames per second")


@router.post(
    "/projects",
    response_model=EnvelopeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new project",
    description="Create a new video editing project with default layers and audio tracks.",
)
async def create_project_v1(
    request: CreateProjectV1Request,
    current_user: CurrentUser,
    db: DbSession,
) -> EnvelopeResponse:
    """Create a new project within the V1 API namespace.

    Creates a project with default timeline structure (5 layers + 3 audio tracks).
    Returns the project data including its ID for subsequent operations.
    """
    context = create_request_context()
    logger.info("v1.create_project name=%s user=%s", request.name, current_user.id)

    # Validate even dimensions
    if request.width % 2 != 0:
        return envelope_error(
            context,
            code="VALIDATION_ERROR",
            message=f"width must be an even number (got {request.width})",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if request.height % 2 != 0:
        return envelope_error(
            context,
            code="VALIDATION_ERROR",
            message=f"height must be an even number (got {request.height})",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    project = Project(
        user_id=current_user.id,
        name=request.name,
        description=request.description,
        width=request.width,
        height=request.height,
        fps=request.fps,
    )
    db.add(project)
    await db.flush()

    # Create default sequence for the project
    default_sequence = Sequence(
        project_id=project.id,
        name="Main",
        timeline_data=_default_timeline_data(),
        version=1,
        duration_ms=0,
        is_default=True,
    )
    db.add(default_sequence)
    await db.flush()
    await db.refresh(project)

    project_data = {
        "id": str(project.id),
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "width": project.width,
        "height": project.height,
        "fps": project.fps,
        "duration_ms": project.duration_ms,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        "hint": "Use GET /api/ai/v1/projects/{id}/assets to list assets, POST /api/ai/v1/projects/{id}/clips to add clips.",
    }

    context.warnings.append(
        "Project created with default layers (Text, Effects, Avatar, Content, Background) "
        "and audio tracks (Narration, BGM, SE). Use GET /timeline-overview to see the full structure."
    )

    return envelope_success(context, project_data)


@router.get("/projects/{project_id}/overview", response_model=EnvelopeResponse)
async def get_project_overview(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    context = create_request_context()
    logger.info("v1.get_project_overview project=%s", project_id)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)
        service = AIService(db)
        data: L1ProjectOverview = await service.get_project_overview(project)
        return envelope_success(context, data)
    except HTTPException as exc:
        logger.warning("v1.get_project_overview failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.get("/projects/{project_id}/summary", response_model=EnvelopeResponse)
async def get_project_summary(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Alias for /overview. Use /overview instead."""
    context = create_request_context()
    context.warnings.append(
        "This endpoint is an alias for /overview. Use /overview instead."
    )
    logger.info("v1.get_project_summary (alias) project=%s", project_id)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)
        service = AIService(db)
        data: L1ProjectOverview = await service.get_project_overview(project)
        return envelope_success(context, data)
    except HTTPException as exc:
        logger.warning("v1.get_project_summary failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.get("/projects/{project_id}/structure", response_model=EnvelopeResponse)
async def get_timeline_structure(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    context = create_request_context()
    logger.info("v1.get_timeline_structure project=%s", project_id)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)
        service = AIService(db)
        data: L2TimelineStructure = await service.get_timeline_structure(project)
        return envelope_success(context, data)
    except HTTPException as exc:
        logger.warning("v1.get_timeline_structure failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.get("/projects/{project_id}/timeline-overview", response_model=EnvelopeResponse)
async def get_timeline_overview(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
    include_snapshot: bool = False,
) -> EnvelopeResponse | JSONResponse:
    """L2.5: Full timeline overview with clips, gaps, and overlaps in one request.

    The snapshot_base64 field is omitted by default to reduce response size.
    Pass ?include_snapshot=true to include the visual timeline snapshot (~65K tokens).
    """
    context = create_request_context()
    logger.info("v1.get_timeline_overview project=%s include_snapshot=%s", project_id, include_snapshot)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)
        service = AIService(db)
        data: L25TimelineOverview = await service.get_timeline_overview(
            project, include_snapshot=include_snapshot
        )
        return envelope_success(context, data)
    except HTTPException as exc:
        logger.warning("v1.get_timeline_overview failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.get("/projects/{project_id}/assets", response_model=EnvelopeResponse)
async def get_asset_catalog(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    context = create_request_context()
    logger.info("v1.get_asset_catalog project=%s", project_id)

    # Known contradictory type/subtype combinations
    _type_subtype_contradictions: dict[str, set[str]] = {
        "bgm": {"narration", "se"},
        "narration": {"bgm", "se"},
        "se": {"bgm", "narration"},
    }

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)
        service = AIService(db)
        data: L2AssetCatalog = await service.get_asset_catalog(project)

        # Detect type/subtype contradictions and add warnings
        for asset in data.assets:
            if asset.type and asset.subtype:
                contradictions = _type_subtype_contradictions.get(asset.type)
                if contradictions and asset.subtype in contradictions:
                    context.warnings.append(
                        f"Asset '{asset.name}' (id={asset.id}) has contradictory "
                        f"type='{asset.type}' and subtype='{asset.subtype}'. "
                        f"Consider reclassifying via PUT /api/ai-video/projects/{{project_id}}/assets/{{asset_id}}/reclassify."
                    )

        return envelope_success(context, data)
    except HTTPException as exc:
        logger.warning("v1.get_asset_catalog failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.post(
    "/projects/{project_id}/clips",
    response_model=EnvelopeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_clip(
    project_id: UUID,
    request: CreateClipRequest,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    context = create_request_context()
    logger.info("v1.add_clip project=%s layer=%s", project_id, request.clip.layer_id)

    # Validate headers (Idempotency-Key required unless validate_only=true)
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if headers["if_match"] and headers["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Convert unified clip input to internal format
        internal_clip = request.to_internal_clip()

        # Add conversion warnings (e.g., unsupported fields, non-uniform scale)
        context.warnings.extend(request.clip.get_conversion_warnings())

        # Handle validate_only mode (dry-run)
        if request.options.validate_only:
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_add_clip(project, internal_clip)
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.add_clip failed project=%s code=%s: %s", project_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0

        include_audio = request.options.include_audio

        try:
            flag_modified(project, "timeline_data")
            result = await service.add_clip(project, internal_clip, include_audio=include_audio)
        except DougaError as exc:
            logger.warning("v1.add_clip failed project=%s code=%s: %s", project_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        if result is None:
            return envelope_error(
                context,
                code="INTERNAL_ERROR",
                message="Failed to create clip",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Calculate duration after
        duration_after = project.duration_ms or 0

        # Get full clip ID and data from result (Pydantic model)
        full_clip_id = result.id
        result_dict = _serialize_for_json(result.model_dump())

        # Check for linked audio clip
        linked_audio_clip_id = getattr(result, "_linked_audio_clip_id", None)
        linked_audio_clip_details = None
        if linked_audio_clip_id:
            try:
                linked_audio_clip_details = await service.get_audio_clip_details(project, linked_audio_clip_id)
            except Exception:
                logger.warning("Failed to get linked audio clip details for %s", linked_audio_clip_id)

        # Build diff
        changes = [
            ChangeDetail(
                entity_type="clip",
                entity_id=full_clip_id,
                change_type="created",
                before=None,
                after=result_dict,
            )
        ]

        # Record operation first to get operation_id
        # Use result.layer_id (full ID from L3ClipDetails) for consistency
        full_layer_id = result.layer_id

        operation = await operation_service.record_operation(
            project=project,
            operation_type="add_clip",
            source="api_v1",
            success=True,
            affected_clips=[full_clip_id],
            affected_layers=[full_layer_id],
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint="/clips",
                method="POST",
                target_ids=[full_layer_id],
                key_params=_serialize_for_json({"asset_id": internal_clip.asset_id, "start_ms": internal_clip.start_ms}),
            ),
            result_summary=ResultSummary(
                success=True,
                created_ids=[full_clip_id],
            ),
            rollback_data=_serialize_for_json({"clip_id": full_clip_id, "clip_data": result_dict}),
            rollback_available=True,
            idempotency_key=headers.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="add_clip",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "add_clip"},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if linked_audio_clip_details:
            response_data["linked_audio_clip"] = linked_audio_clip_details
        elif include_audio and internal_clip.asset_id:
            # Only warn about linked audio for asset types that actually have audio
            # (video, audio). Image assets never have linked audio, so skip the warning.
            asset_result = await db.execute(
                select(Asset.type).where(
                    Asset.id == internal_clip.asset_id,
                    Asset.project_id == project_id,
                )
            )
            asset_type_value = asset_result.scalar_one_or_none()
            if asset_type_value in ("video", "audio"):
                response_data["linked_audio_clip"] = None
                context.warnings.append("Linked audio not yet available (extraction may still be in progress)")
        if request.auto_wrapped:
            response_data["auto_wrapped"] = True
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            {"type": "preview_seek", "seek_to_ms": result.timing.start_ms, "reason": "Start of added clip"},
            "Use PATCH /clips/{clip_id}/effects to add fade transitions",
            "Use PATCH /clips/{clip_id}/transform to adjust position",
            "Use GET /timeline-overview to see the updated layout",
        ]

        # Add overlap warnings to response context
        overlap_warnings = getattr(result, "_overlap_warnings", [])
        if overlap_warnings:
            context.warnings.extend(overlap_warnings)

        logger.info("v1.add_clip ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.add_clip failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.patch(
    "/projects/{project_id}/clips/{clip_id}/move",
    response_model=EnvelopeResponse,
)
async def move_clip(
    project_id: UUID,
    clip_id: str,
    request: MoveClipV1Request,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Move a clip to a new timeline position or layer."""
    context = create_request_context()
    logger.info("v1.move_clip project=%s clip=%s", project_id, clip_id)

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if headers["if_match"] and headers["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Convert to internal request
        internal_request = request.to_internal_request()

        # Handle validate_only mode
        if request.options.validate_only:
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_move_clip(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.move_clip failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (supports partial ID)
        duration_before = project.duration_ms or 0
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_start_ms = original_clip_state.get("start_ms") if original_clip_state else None
        original_layer_id = original_clip_state.get("layer_id") if original_clip_state else None

        try:
            flag_modified(project, "timeline_data")
            result = await service.move_clip(project, clip_id, internal_request)
        except DougaError as exc:
            logger.warning("v1.move_clip failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        if result is None:
            return envelope_error(
                context,
                code="INTERNAL_ERROR",
                message="Failed to move clip",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and values from result (Pydantic model)
        # Use result.layer_id as source of truth (full ID after move)
        full_clip_id = result.id
        duration_after = project.duration_ms or 0
        new_start_ms = result.timing.start_ms
        new_layer_id = result.layer_id  # Full ID from L3ClipDetails

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="clip",
                entity_id=full_clip_id,
                change_type="modified",
                before={"start_ms": original_start_ms, "layer_id": original_layer_id},
                after={"start_ms": new_start_ms, "layer_id": new_layer_id},
            )
        ]

        # Record operation first to get operation_id
        operation = await operation_service.record_operation(
            project=project,
            operation_type="move_clip",
            source="api_v1",
            success=True,
            affected_clips=[full_clip_id],
            affected_layers=[layer for layer in [original_layer_id, new_layer_id] if layer],
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint=f"/clips/{full_clip_id}/move",
                method="PATCH",
                target_ids=[full_clip_id],
                key_params={"new_start_ms": internal_request.new_start_ms, "new_layer_id": internal_request.new_layer_id},
            ),
            result_summary=ResultSummary(
                success=True,
                modified_ids=[full_clip_id],
            ),
            rollback_data={
                "clip_id": full_clip_id,
                "original_start_ms": original_start_ms,
                "original_layer_id": original_layer_id,
                "new_start_ms": new_start_ms,
                "new_layer_id": new_layer_id,
            },
            rollback_available=True,
            idempotency_key=headers.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="move_clip",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "move_clip", "clip_id": clip_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        linked_clips_moved = getattr(result, "_linked_clips_moved", [])
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if linked_clips_moved:
            response_data["linked_clips_moved"] = linked_clips_moved
        if request.auto_wrapped:
            response_data["auto_wrapped"] = True
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            {"type": "preview_seek", "seek_to_ms": new_start_ms, "reason": "Start of moved clip"},
            "Use GET /timeline-overview to verify the new position",
            "Use POST /preview/validate to check for overlapping clips",
        ]

        # Add overlap warnings to response context
        overlap_warnings = getattr(result, "_overlap_warnings", [])
        if overlap_warnings:
            context.warnings.extend(overlap_warnings)

        logger.info("v1.move_clip ok project=%s clip=%s linked_moved=%s", project_id, full_clip_id, linked_clips_moved)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.move_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.patch(
    "/projects/{project_id}/clips/{clip_id}/transform",
    response_model=EnvelopeResponse,
)
async def transform_clip(
    project_id: UUID,
    clip_id: str,
    request: TransformClipV1Request,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Update clip transform properties (position, scale, rotation)."""
    context = create_request_context()
    logger.info("v1.transform_clip project=%s clip=%s", project_id, clip_id)

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if headers["if_match"] and headers["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Convert to internal request and add conversion warnings
        internal_request = request.to_internal_request()
        context.warnings.extend(request.transform.get_conversion_warnings())
        context.warnings.extend(request.get_unknown_field_warnings())

        # Handle validate_only mode
        if request.options.validate_only:
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_transform_clip(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.transform_clip failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (supports partial ID)
        duration_before = project.duration_ms or 0
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_transform = original_clip_state.get("transform", {}).copy() if original_clip_state else {}

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_clip_transform(project, clip_id, internal_request)
        except DougaError as exc:
            logger.warning("v1.transform_clip failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        if result is None:
            return envelope_error(
                context,
                code="INTERNAL_ERROR",
                message="Failed to transform clip",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and transform from result (Pydantic model)
        full_clip_id = result.id
        duration_after = project.duration_ms or 0
        new_transform = result.transform.model_dump()

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="clip",
                entity_id=full_clip_id,
                change_type="modified",
                before=original_transform,
                after=new_transform,
            )
        ]

        # Record operation first to get operation_id
        operation = await operation_service.record_operation(
            project=project,
            operation_type="update_transform",
            source="api_v1",
            success=True,
            affected_clips=[full_clip_id],
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint=f"/clips/{full_clip_id}/transform",
                method="PATCH",
                target_ids=[full_clip_id],
                key_params=_serialize_for_json(internal_request.model_dump(exclude_none=True)),
            ),
            result_summary=ResultSummary(
                success=True,
                modified_ids=[full_clip_id],
            ),
            rollback_data={
                "clip_id": full_clip_id,
                "original_transform": original_transform,
                "new_transform": new_transform,
            },
            rollback_available=True,
            idempotency_key=headers.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="update_transform",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "transform_clip", "clip_id": clip_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if request.auto_wrapped:
            response_data["auto_wrapped"] = True
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            "Use POST /preview/sample-frame to visually verify the new position",
            "Use GET /timeline-overview to see the updated layout",
        ]

        logger.info("v1.transform_clip ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.transform_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.patch(
    "/projects/{project_id}/clips/{clip_id}/effects",
    response_model=EnvelopeResponse,
)
async def update_clip_effects(
    project_id: UUID,
    clip_id: str,
    request: UpdateEffectsV1Request,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Update clip effects (opacity, fade, chroma key).

    Supports:
    - opacity: 0.0-1.0
    - blend_mode: "normal", "multiply", etc.
    - fade_in_ms: 0-10000ms fade in duration
    - fade_out_ms: 0-10000ms fade out duration
    - chroma_key_enabled: bool
    - chroma_key_color: hex color (#RRGGBB)
    - chroma_key_similarity: 0.0-1.0
    - chroma_key_blend: 0.0-1.0
    """
    context = create_request_context()
    logger.info("v1.update_clip_effects project=%s clip=%s", project_id, clip_id)

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if headers["if_match"] and headers["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Convert to internal request
        internal_request = request.to_internal_request()

        # Handle validate_only mode
        if request.options.validate_only:
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_update_effects(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.update_clip_effects failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (supports partial ID)
        duration_before = project.duration_ms or 0
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_effects = original_clip_state.get("effects", {}).copy() if original_clip_state else {}
        original_transition_in = original_clip_state.get("transition_in", {}).copy() if original_clip_state else {}
        original_transition_out = original_clip_state.get("transition_out", {}).copy() if original_clip_state else {}

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_clip_effects(project, clip_id, internal_request)
        except DougaError as exc:
            logger.warning("v1.update_clip_effects failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        if result is None:
            return envelope_error(
                context,
                code="INTERNAL_ERROR",
                message="Failed to update clip effects",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and new state from result
        full_clip_id = result.id
        duration_after = project.duration_ms or 0

        # Get new effects state
        new_clip_state, _ = _find_clip_state(project, full_clip_id)
        new_effects = new_clip_state.get("effects", {}) if new_clip_state else {}
        new_transition_in = new_clip_state.get("transition_in", {}) if new_clip_state else {}
        new_transition_out = new_clip_state.get("transition_out", {}) if new_clip_state else {}

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="clip",
                entity_id=full_clip_id,
                change_type="modified",
                before={
                    "effects": original_effects,
                    "transition_in": original_transition_in,
                    "transition_out": original_transition_out,
                },
                after={
                    "effects": new_effects,
                    "transition_in": new_transition_in,
                    "transition_out": new_transition_out,
                },
            )
        ]

        # Record operation first to get operation_id
        operation = await operation_service.record_operation(
            project=project,
            operation_type="update_effects",
            source="api_v1",
            success=True,
            affected_clips=[full_clip_id],
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint=f"/clips/{full_clip_id}/effects",
                method="PATCH",
                target_ids=[full_clip_id],
                key_params=_serialize_for_json(internal_request.model_dump(exclude_none=True)),
            ),
            result_summary=ResultSummary(
                success=True,
                modified_ids=[full_clip_id],
            ),
            rollback_data={
                "clip_id": full_clip_id,
                "original_effects": original_effects,
                "original_transition_in": original_transition_in,
                "original_transition_out": original_transition_out,
                "new_effects": new_effects,
                "new_transition_in": new_transition_in,
                "new_transition_out": new_transition_out,
            },
            rollback_available=True,
            idempotency_key=headers.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="update_effects",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "update_effects", "clip_id": clip_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if request.auto_wrapped:
            response_data["auto_wrapped"] = True
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            "Use POST /preview/sample-frame to visually verify the effect",
            "Use GET /clips/{clip_id} to see the full clip state",
        ]

        logger.info("v1.update_clip_effects ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.update_clip_effects failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.post(
    "/projects/{project_id}/clips/{clip_id}/chroma-key/preview",
    response_model=EnvelopeResponse,
)
async def preview_chroma_key(
    project_id: UUID,
    clip_id: str,
    request: ChromaKeyPreviewRequest,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Generate 5-frame chroma key preview for a clip."""
    context = create_request_context()
    logger.info("v1.preview_chroma_key project=%s clip=%s", project_id, clip_id)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        timeline = project.timeline_data
        if not timeline:
            return envelope_error(
                context,
                code="INVALID_FIELD_VALUE",
                message="No timeline data in project",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        clip_ref, full_clip_id = _find_clip_ref(timeline, clip_id)
        if clip_ref is None:
            return envelope_error(
                context,
                code="CLIP_NOT_FOUND",
                message=f"Clip {clip_id} not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        asset_id = clip_ref.get("asset_id")
        if not asset_id:
            return envelope_error(
                context,
                code="ASSET_NOT_FOUND",
                message="Clip has no asset_id",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        result = await db.execute(
            select(Asset).where(
                Asset.id == UUID(str(asset_id)),
                Asset.project_id == project_id,
            )
        )
        asset = result.scalar_one_or_none()
        if asset is None:
            return envelope_error(
                context,
                code="ASSET_NOT_FOUND",
                message=f"Asset {asset_id} not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if asset.type not in {"video", "image"}:
            return envelope_error(
                context,
                code="INVALID_ASSET_TYPE",
                message=f"Asset {asset.id} is not a video/image asset",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        duration_ms = int(clip_ref.get("duration_ms", 0) or 0)
        in_point_ms = int(clip_ref.get("in_point_ms", 0) or 0)
        if duration_ms <= 0:
            out_point_ms = clip_ref.get("out_point_ms")
            if out_point_ms is not None:
                duration_ms = max(0, int(out_point_ms) - in_point_ms)

        if duration_ms <= 0:
            return envelope_error_from_exception(
                context,
                InvalidTimeRangeError(
                    message="duration_ms must be > 0 for preview sampling",
                    start_ms=clip_ref.get("start_ms", 0),
                    end_ms=clip_ref.get("start_ms", 0) + duration_ms,
                    field="duration_ms",
                ),
            )

        storage = get_storage_service()
        input_url = await storage.get_signed_url(asset.storage_key)
        start_ms = int(clip_ref.get("start_ms", 0) or 0)
        # If time_ms is provided, use single frame at playhead position; otherwise 5-frame legacy
        if request.time_ms is not None:
            # Clamp time_ms to clip range
            end_ms = start_ms + duration_ms
            clamped_time = max(start_ms, min(request.time_ms, end_ms - 1))
            times = [clamped_time]
        else:
            times = _compute_chroma_preview_times(start_ms, duration_ms)
        chroma_service = ChromaKeyService()
        try:
            resolved_color = chroma_service.resolve_key_color(
                input_url,
                request.key_color,
                sample_times_ms=times,
                clip_start_ms=start_ms,
                in_point_ms=in_point_ms,
            )
        except RuntimeError:
            logger.warning("v1.preview_chroma_key runtime_error project=%s clip=%s", project_id, clip_id)
            return envelope_error_from_exception(
                context,
                ChromaKeyAutoFailedError(str(asset_id)),
            )

        temp_dir = tempfile.mkdtemp(prefix="douga_chroma_preview_")
        try:
            frames = await chroma_service.render_preview_frames(
                input_url=input_url,
                output_dir=temp_dir,
                times_ms=times,
                clip_start_ms=start_ms,
                in_point_ms=in_point_ms,
                resolution=request.resolution,
                key_color=resolved_color,
                similarity=request.similarity,
                blend=request.blend,
                skip_chroma_key=request.skip_chroma_key,
                return_transparent_png=request.return_transparent_png,
            )
            logger.info("v1.preview_chroma_key ok project=%s clip=%s", project_id, clip_id)
            return envelope_success(
                context,
                {
                    "resolved_key_color": resolved_color,
                    "frames": frames,
                    "debug": {
                        "request_time_ms": request.time_ms,
                        "clip_start_ms": start_ms,
                        "clip_duration_ms": duration_ms,
                        "in_point_ms": in_point_ms,
                        "times_ms_used": times,
                        "asset_duration_ms": asset.duration_ms,
                    },
                },
            )
        except RuntimeError as exc:
            logger.warning("v1.preview_chroma_key runtime_error project=%s clip=%s", project_id, clip_id)
            return envelope_error(
                context,
                code="INTERNAL_ERROR",
                message=str(exc),
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except HTTPException as exc:
        logger.warning("v1.preview_chroma_key failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.post(
    "/projects/{project_id}/clips/{clip_id}/chroma-key/apply",
    response_model=EnvelopeResponse,
)
async def apply_chroma_key(
    project_id: UUID,
    clip_id: str,
    request: ChromaKeyApplyRequest,
    current_user: CurrentUser,
    db: DbSession,
    http_request: Request,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Generate a processed chroma key asset for a clip."""
    context = create_request_context()
    logger.info("v1.apply_chroma_key project=%s clip=%s", project_id, clip_id)

    # Validate headers (mutation)
    validate_headers(http_request, context, validate_only=False)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        timeline = project.timeline_data
        if not timeline:
            return envelope_error(
                context,
                code="INVALID_FIELD_VALUE",
                message="No timeline data in project",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        clip_ref, full_clip_id = _find_clip_ref(timeline, clip_id)
        if clip_ref is None:
            return envelope_error(
                context,
                code="CLIP_NOT_FOUND",
                message=f"Clip {clip_id} not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        asset_id = clip_ref.get("asset_id")
        if not asset_id:
            return envelope_error(
                context,
                code="ASSET_NOT_FOUND",
                message="Clip has no asset_id",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        result = await db.execute(
            select(Asset).where(
                Asset.id == UUID(str(asset_id)),
                Asset.project_id == project_id,
            )
        )
        asset = result.scalar_one_or_none()
        if asset is None:
            return envelope_error(
                context,
                code="ASSET_NOT_FOUND",
                message=f"Asset {asset_id} not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if asset.type not in {"video", "image"}:
            return envelope_error(
                context,
                code="INVALID_ASSET_TYPE",
                message=f"Asset {asset.id} is not a video/image asset",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        temp_dir = tempfile.mkdtemp(prefix="douga_chroma_apply_")
        storage = get_storage_service()
        try:
            ext = asset.storage_key.rsplit(".", 1)[-1] if "." in asset.storage_key else "mp4"
            input_path = os.path.join(temp_dir, f"input.{ext}")
            await storage.download_file(asset.storage_key, input_path)

            chroma_service = ChromaKeyService()
            start_ms = int(clip_ref.get("start_ms", 0) or 0)
            duration_ms = int(clip_ref.get("duration_ms", 0) or 0)
            in_point_ms = int(clip_ref.get("in_point_ms", 0) or 0)
            if duration_ms <= 0:
                out_point_ms = clip_ref.get("out_point_ms")
                if out_point_ms is not None:
                    duration_ms = max(0, int(out_point_ms) - in_point_ms)

            times = _compute_chroma_preview_times(start_ms, duration_ms) if duration_ms > 0 else None
            try:
                resolved_color = chroma_service.resolve_key_color(
                    input_path,
                    request.key_color,
                    sample_times_ms=times,
                    clip_start_ms=start_ms,
                    in_point_ms=in_point_ms,
                )
            except RuntimeError:
                logger.warning("v1.apply_chroma_key runtime_error project=%s clip=%s", project_id, clip_id)
                return envelope_error_from_exception(
                    context,
                    ChromaKeyAutoFailedError(str(asset.id)),
                )

            hash_source = f"{asset.id}:{resolved_color}:{request.similarity}:{request.blend}"
            hash_value = hashlib.sha256(hash_source.encode("utf-8")).hexdigest()

            existing_result = await db.execute(
                select(Asset).where(
                    Asset.project_id == project_id,
                    Asset.hash == hash_value,
                )
            )
            existing_asset = existing_result.scalar_one_or_none()
            if existing_asset:
                asset_response = await _asset_to_response(existing_asset)
                logger.info("v1.apply_chroma_key ok project=%s clip=%s cached=True", project_id, clip_id)
                return envelope_success(
                    context,
                    {
                        "resolved_key_color": resolved_color,
                        "asset_id": str(existing_asset.id),
                        "asset": asset_response,
                    },
                )

            output_path = os.path.join(temp_dir, "output.webm")
            await chroma_service.apply_to_video(
                input_path,
                output_path,
                key_color=resolved_color,
                similarity=request.similarity,
                blend=request.blend,
            )

            file_size = os.path.getsize(output_path)
            media_info = get_media_info(output_path)

            base_name = os.path.splitext(asset.name)[0]
            output_name = f"{base_name}_chroma.webm"
            storage_key = f"projects/{project_id}/assets/{uuid4()}.webm"
            storage_url = await storage.upload_file(output_path, storage_key, "video/webm")

            new_asset = Asset(
                project_id=project.id,
                name=output_name,
                type="video",
                subtype=asset.subtype,
                storage_key=storage_key,
                storage_url=storage_url,
                thumbnail_url=None,
                duration_ms=media_info.get("duration_ms") or asset.duration_ms,
                width=media_info.get("width") or asset.width,
                height=media_info.get("height") or asset.height,
                file_size=file_size,
                mime_type="video/webm",
                sample_rate=media_info.get("sample_rate"),
                channels=media_info.get("channels"),
                has_alpha=True,
                chroma_key_color=resolved_color,
                hash=hash_value,
                is_internal=False,
                asset_metadata={
                    "derived_from_asset_id": str(asset.id),
                    "chroma_key_params": {
                        "key_color": request.key_color,
                        "resolved_key_color": resolved_color,
                        "similarity": request.similarity,
                        "blend": request.blend,
                    },
                    "source_clip_id": str(full_clip_id or clip_id),
                },
            )
            db.add(new_asset)
            await db.flush()
            await db.refresh(new_asset)

            asset_response = await _asset_to_response(new_asset)
            logger.info("v1.apply_chroma_key ok project=%s clip=%s asset=%s", project_id, clip_id, new_asset.id)
            return envelope_success(
                context,
                {
                    "resolved_key_color": resolved_color,
                    "asset_id": str(new_asset.id),
                    "asset": asset_response,
                },
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except HTTPException as exc:
        logger.warning("v1.apply_chroma_key failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.patch(
    "/projects/{project_id}/clips/{clip_id}/crop",
    response_model=EnvelopeResponse,
)
async def update_clip_crop(
    project_id: UUID,
    clip_id: str,
    request: UpdateCropV1Request,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Update clip crop (edge trimming).

    Supports:
    - top: 0.0-0.5 fraction of height to remove from top
    - right: 0.0-0.5 fraction of width to remove from right
    - bottom: 0.0-0.5 fraction of height to remove from bottom
    - left: 0.0-0.5 fraction of width to remove from left
    """
    context = create_request_context()
    logger.info("v1.update_clip_crop project=%s clip=%s", project_id, clip_id)

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if headers["if_match"] and headers["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Convert to internal request
        internal_request = request.to_internal_request()

        # Handle validate_only mode
        if request.options.validate_only:
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_update_crop(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.update_clip_crop failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (supports partial ID)
        duration_before = project.duration_ms or 0
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_crop = original_clip_state.get("crop", {}).copy() if original_clip_state else {}

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_clip_crop(project, clip_id, internal_request)
        except DougaError as exc:
            logger.warning("v1.update_clip_crop failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        if result is None:
            return envelope_error(
                context,
                code="INTERNAL_ERROR",
                message="Failed to update clip crop",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get the full clip ID from result
        full_clip_id = result.id

        # Capture state after
        duration_after = project.duration_ms or 0
        new_clip_state, _ = _find_clip_state(project, full_clip_id)
        new_crop = new_clip_state.get("crop", {}).copy() if new_clip_state else {}

        # Build change details
        changes = [
            ChangeDetail(
                entity_type="clip",
                entity_id=full_clip_id,
                change_type="modified",
                before={"crop": original_crop},
                after={"crop": new_crop},
            )
        ]

        # Record operation first to get operation_id
        operation = await operation_service.record_operation(
            project=project,
            operation_type="update_crop",
            source="api_v1",
            success=True,
            affected_clips=[full_clip_id],
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint=f"/clips/{full_clip_id}/crop",
                method="PATCH",
                target_ids=[full_clip_id],
                key_params=_serialize_for_json(internal_request.model_dump(exclude_none=True)),
            ),
            result_summary=ResultSummary(
                success=True,
                modified_ids=[full_clip_id],
            ),
            rollback_data=None,  # Rollback not implemented for update_crop
            rollback_available=False,
            idempotency_key=headers.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="update_crop",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "update_crop", "clip_id": clip_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Rollback not yet implemented for crop updates; re-apply previous values manually" if not operation.rollback_available else "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo",
        }
        if request.auto_wrapped:
            response_data["auto_wrapped"] = True
        if not operation.rollback_available:
            response_data.setdefault("hints", []).append(
                "To undo: re-apply previous crop values via PATCH /clips/{clip_id}/crop"
            )
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.update_clip_crop ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.update_clip_crop failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.patch(
    "/projects/{project_id}/clips/{clip_id}/text-style",
    response_model=EnvelopeResponse,
)
async def update_clip_text_style(
    project_id: UUID,
    clip_id: str,
    request: UpdateTextStyleV1Request,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Update text clip styling.

    Only applies to text clips. Uses camelCase to match frontend/renderer.
    Supports:
    - fontFamily: Font family name (e.g., "Noto Sans JP")
    - fontSize: 8-500 pixels
    - fontWeight: "normal" or "bold"
    - color: Text color in hex (#RRGGBB)
    - textAlign: "left", "center", or "right"
    - backgroundColor: Background color in hex (#RRGGBB)
    - backgroundOpacity: 0.0-1.0
    """
    context = create_request_context()
    logger.info("v1.update_clip_text_style project=%s clip=%s", project_id, clip_id)

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if headers["if_match"] and headers["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Convert to internal request
        internal_request = request.to_internal_request()

        # Handle validate_only mode
        if request.options.validate_only:
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_update_text_style(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.update_clip_text_style failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (supports partial ID)
        duration_before = project.duration_ms or 0
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_text_style = _normalize_text_style_for_diff(
            original_clip_state.get("text_style") if original_clip_state else {}
        )

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_clip_text_style(project, clip_id, internal_request)
        except DougaError as exc:
            logger.warning("v1.update_clip_text_style failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        if result is None:
            return envelope_error(
                context,
                code="INTERNAL_ERROR",
                message="Failed to update clip text style",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get the full clip ID from result
        full_clip_id = result.id

        # Capture state after
        duration_after = project.duration_ms or 0
        new_clip_state, _ = _find_clip_state(project, full_clip_id)
        new_text_style = _normalize_text_style_for_diff(
            new_clip_state.get("text_style") if new_clip_state else {}
        )

        # Build change details
        changes = [
            ChangeDetail(
                entity_type="clip",
                entity_id=full_clip_id,
                change_type="modified",
                before={"text_style": original_text_style},
                after={"text_style": new_text_style},
            )
        ]

        # Record operation first to get operation_id
        operation = await operation_service.record_operation(
            project=project,
            operation_type="update_text_style",
            source="api_v1",
            success=True,
            affected_clips=[full_clip_id],
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint=f"/clips/{full_clip_id}/text-style",
                method="PATCH",
                target_ids=[full_clip_id],
                key_params=_serialize_for_json(internal_request.model_dump(exclude_none=True)),
            ),
            result_summary=ResultSummary(
                success=True,
                modified_ids=[full_clip_id],
            ),
            rollback_data={
                "clip_id": full_clip_id,
                "original_text_style": original_text_style,
                "new_text_style": new_text_style,
            },
            rollback_available=True,
            idempotency_key=headers.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="update_text_style",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "update_text_style", "clip_id": clip_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if request.auto_wrapped:
            response_data["auto_wrapped"] = True
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            "Use PATCH /clips/{clip_id}/effects to add fade transitions to this text",
            "Use POST /preview/sample-frame to visually verify text appearance",
        ]

        logger.info("v1.update_clip_text_style ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.update_clip_text_style failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.delete(
    "/projects/{project_id}/clips/{clip_id}",
    response_model=EnvelopeResponse,
)
async def delete_clip(
    project_id: UUID,
    clip_id: str,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    request: DeleteClipV1Request | None = None,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Delete a clip from the timeline.

    Note: Request body is optional. If provided, supports validate_only mode.
    """
    context = create_request_context()
    logger.info("v1.delete_clip project=%s clip=%s", project_id, clip_id)

    # Determine validate_only from request body if present
    validate_only = request.options.validate_only if request else False

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=validate_only,
    )

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if headers["if_match"] and headers["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Handle validate_only mode
        if validate_only:
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_delete_clip(project, clip_id)
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.delete_clip failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (supports partial ID)
        duration_before = project.duration_ms or 0
        original_clip_state, full_clip_id = _find_clip_state(project, clip_id)
        if not original_clip_state or not full_clip_id:
            return envelope_error(
                context,
                code="CLIP_NOT_FOUND",
                message=f"Clip not found: {clip_id}",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        original_layer_id = original_clip_state.get("layer_id")
        # Remove layer_id from clip_data (it's stored separately)
        clip_data = {k: v for k, v in original_clip_state.items() if k != "layer_id"}

        try:
            flag_modified(project, "timeline_data")
            delete_result = await service.delete_clip(project, clip_id)
        except DougaError as exc:
            logger.warning("v1.delete_clip failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        # Use full clip ID from delete result or from state lookup
        actual_deleted_id = delete_result["deleted_id"] if isinstance(delete_result, dict) else (delete_result or full_clip_id)
        deleted_linked_ids = delete_result.get("deleted_linked_ids", []) if isinstance(delete_result, dict) else []
        duration_after = project.duration_ms or 0

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="clip",
                entity_id=actual_deleted_id,
                change_type="deleted",
                before=clip_data,
                after=None,
            )
        ]

        # Record operation first to get operation_id
        operation = await operation_service.record_operation(
            project=project,
            operation_type="delete_clip",
            source="api_v1",
            success=True,
            affected_clips=[actual_deleted_id],
            affected_layers=[original_layer_id] if original_layer_id else [],
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint=f"/clips/{actual_deleted_id}",
                method="DELETE",
                target_ids=[actual_deleted_id],
                key_params={},
            ),
            result_summary=ResultSummary(
                success=True,
                deleted_ids=[actual_deleted_id],
            ),
            rollback_data={
                "clip_id": actual_deleted_id,
                "clip_data": clip_data,
                "layer_id": original_layer_id,
            },
            rollback_available=True,
            idempotency_key=headers.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="delete_clip",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "delete_clip", "clip_id": actual_deleted_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        include_diff = request.options.include_diff if request else False
        response_data: dict = {
            "deleted": True,
            "clip_id": actual_deleted_id,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if deleted_linked_ids:
            response_data["deleted_linked_ids"] = deleted_linked_ids
        if include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            "Use GET /timeline-overview to see the updated layout",
            "Use GET /analysis/gaps to check for newly created gaps",
        ]

        logger.info("v1.delete_clip ok project=%s clip=%s linked=%s", project_id, actual_deleted_id, deleted_linked_ids)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.delete_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# Layer Endpoints (Priority 2)
# =============================================================================


@router.post(
    "/projects/{project_id}/layers",
    response_model=EnvelopeResponse,
    summary="Add a new layer",
    description="Add a new layer to the project timeline.",
)
async def add_layer(
    project_id: UUID,
    body: AddLayerV1Request,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Add a new layer to the project.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()
    logger.info("v1.add_layer project=%s name=%s type=%s", project_id, body.layer.name, body.layer.type)

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        if body.options.validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_add_layer(
                    project, body.layer
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.add_layer failed project=%s code=%s: %s", project_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0

        try:
            layer_summary = await service.add_layer(
                project,
                name=body.layer.name,
                layer_type=body.layer.type,
                insert_at=body.layer.insert_at,
            )
        except DougaError as exc:
            logger.warning("v1.add_layer failed project=%s code=%s: %s", project_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
            logger.warning("v1.add_layer failed project=%s: %s", project_id, e)
            return envelope_error(
                context,
                code="VALIDATION_ERROR",
                message=str(e),
                status_code=400,
            )

        # Calculate duration after
        duration_after = project.duration_ms or 0
        layer_id = layer_summary.id
        layer_data = _serialize_for_json(layer_summary.model_dump())

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="layer",
                entity_id=layer_id,
                change_type="created",
                before=None,
                after=layer_data,
            )
        ]

        # Record operation first to get operation_id
        operation = await operation_service.record_operation(
            project=project,
            operation_type="add_layer",
            source="api_v1",
            success=True,
            affected_layers=[layer_id],
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint="/layers",
                method="POST",
                target_ids=[],
                key_params={"name": body.layer.name, "type": body.layer.type},
            ),
            result_summary=ResultSummary(
                success=True,
                created_ids=[layer_id],
            ),
            rollback_data={
                "layer_id": layer_id,
                "layer_data": layer_data,
            },
            rollback_available=True,
            idempotency_key=header_result.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="add_layer",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "add_layer", "layer_id": layer_summary.id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "layer": layer_summary.model_dump(),
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            "Use POST /clips to add clips to this layer",
            "Use PUT /layers/order to adjust layer stacking order",
        ]

        logger.info("v1.add_layer ok project=%s layer=%s", project_id, layer_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.add_layer failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.patch(
    "/projects/{project_id}/layers/{layer_id}",
    response_model=EnvelopeResponse,
    summary="Update layer properties",
    description="Update layer name, visibility, or lock status.",
)
async def update_layer(
    project_id: UUID,
    layer_id: str,
    body: UpdateLayerV1Request,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Update layer properties.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()
    logger.info("v1.update_layer project=%s layer=%s", project_id, layer_id)

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        if body.options.validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_update_layer(
                    project, layer_id, body.layer
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.update_layer failed project=%s layer=%s code=%s: %s", project_id, layer_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        try:
            layer_summary = await service.update_layer(
                project,
                layer_id=layer_id,
                name=body.layer.name,
                visible=body.layer.visible,
                locked=body.layer.locked,
            )
        except DougaError as exc:
            logger.warning("v1.update_layer failed project=%s layer=%s code=%s: %s", project_id, layer_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
            logger.warning("v1.update_layer failed project=%s layer=%s: %s", project_id, layer_id, e)
            return envelope_error(
                context,
                code="VALIDATION_ERROR",
                message=str(e),
                status_code=400,
            )

        if layer_summary is None:
            return envelope_error(
                context,
                code="LAYER_NOT_FOUND",
                message=f"Layer not found: {layer_id}",
                status_code=404,
            )

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "update_layer", "layer_id": layer_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        logger.info("v1.update_layer ok project=%s layer=%s", project_id, layer_id)
        return envelope_success(context, {"layer": layer_summary.model_dump()})

    except HTTPException as exc:
        logger.warning("v1.update_layer failed project=%s layer=%s: %s", project_id, layer_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.put(
    "/projects/{project_id}/layers/order",
    response_model=EnvelopeResponse,
    summary="Reorder layers",
    description="Reorder layers by providing the new order of layer IDs.",
)
async def reorder_layers(
    project_id: UUID,
    body: ReorderLayersV1Request,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Reorder layers.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()
    logger.info("v1.reorder_layers project=%s", project_id)

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        if body.options.validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_reorder_layers(
                    project, body.order.layer_ids
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.reorder_layers failed project=%s code=%s: %s", project_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        try:
            layer_summaries = await service.reorder_layers(
                project,
                layer_ids=body.order.layer_ids,
            )
        except DougaError as exc:
            logger.warning("v1.reorder_layers failed project=%s code=%s: %s", project_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
            logger.warning("v1.reorder_layers failed project=%s: %s", project_id, e)
            return envelope_error(
                context,
                code="LAYER_NOT_FOUND",
                message=str(e),
                status_code=404,
            )

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "reorder_layers"},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        logger.info("v1.reorder_layers ok project=%s", project_id)
        return envelope_success(
            context,
            {"layers": [layer.model_dump() for layer in layer_summaries]},
        )

    except HTTPException as exc:
        logger.warning("v1.reorder_layers failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# Audio Endpoints (Priority 3)
# =============================================================================


@router.post(
    "/projects/{project_id}/audio-clips",
    response_model=EnvelopeResponse,
    summary="Add a new audio clip",
    description="Add a new audio clip to an audio track.",
)
async def add_audio_clip(
    project_id: UUID,
    body: AddAudioClipV1Request,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Add a new audio clip to the project.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()
    logger.info("v1.add_audio_clip project=%s track=%s", project_id, body.clip.track_id)

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        if body.options.validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_add_audio_clip(
                    project, body.clip
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.add_audio_clip failed project=%s code=%s: %s", project_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0

        try:
            audio_clip = await service.add_audio_clip(project, body.clip)
        except DougaError as exc:
            logger.warning("v1.add_audio_clip failed project=%s code=%s: %s", project_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
            logger.warning("v1.add_audio_clip failed project=%s: %s", project_id, e)
            return envelope_error(
                context,
                code="VALIDATION_ERROR",
                message=str(e),
                status_code=400,
            )

        if audio_clip is None:
            return envelope_error(
                context,
                code="AUDIO_TRACK_NOT_FOUND",
                message=f"Audio track not found: {body.clip.track_id}",
                status_code=404,
            )

        # Calculate duration after
        duration_after = project.duration_ms or 0
        clip_id = audio_clip.id
        clip_data = _serialize_for_json(audio_clip.model_dump())

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="audio_clip",
                entity_id=clip_id,
                change_type="created",
                before=None,
                after=clip_data,
            )
        ]

        # Record operation first to get operation_id
        # Use audio_clip.track_id (full ID from L3AudioClipDetails) for consistency
        full_track_id = audio_clip.track_id

        operation = await operation_service.record_operation(
            project=project,
            operation_type="add_audio_clip",
            source="api_v1",
            success=True,
            affected_audio_clips=[clip_id],
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint="/audio-clips",
                method="POST",
                target_ids=[full_track_id],
                key_params=_serialize_for_json({"asset_id": body.clip.asset_id, "start_ms": body.clip.start_ms}),
            ),
            result_summary=ResultSummary(
                success=True,
                created_ids=[clip_id],
            ),
            rollback_data={
                "clip_id": clip_id,
                "clip_data": clip_data,
                "track_id": full_track_id,
            },
            rollback_available=True,
            idempotency_key=header_result.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="add_audio_clip",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "add_audio_clip", "clip_id": audio_clip.id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "audio_clip": audio_clip.model_dump(),
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            {"type": "preview_seek", "seek_to_ms": audio_clip.timing.start_ms, "reason": "Start of added audio clip"},
            "Use PATCH /audio-clips/{clip_id} to adjust volume and fades",
            "Use GET /timeline-overview to see the updated audio layout",
        ]

        logger.info("v1.add_audio_clip ok project=%s clip=%s", project_id, clip_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.add_audio_clip failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.patch(
    "/projects/{project_id}/audio-clips/{clip_id}/move",
    response_model=EnvelopeResponse,
    summary="Move an audio clip",
    description="Move an audio clip to a new position or track.",
)
async def move_audio_clip(
    project_id: UUID,
    clip_id: str,
    body: MoveAudioClipV1Request,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Move an audio clip to a new position or track.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()
    logger.info("v1.move_audio_clip project=%s clip=%s", project_id, clip_id)

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        if body.options.validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_move_audio_clip(
                    project, clip_id, body.to_internal_request()
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.move_audio_clip failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (supports partial ID)
        duration_before = project.duration_ms or 0
        original_clip_state, full_clip_id = _find_audio_clip_state(project, clip_id)
        if not original_clip_state or not full_clip_id:
            return envelope_error(
                context,
                code="AUDIO_CLIP_NOT_FOUND",
                message=f"Audio clip not found: {clip_id}",
                status_code=404,
            )
        original_start_ms = original_clip_state.get("start_ms")
        original_track_id = original_clip_state.get("track_id")

        try:
            audio_clip = await service.move_audio_clip(
                project, clip_id, body.to_internal_request()
            )
        except DougaError as exc:
            logger.warning("v1.move_audio_clip failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
            logger.warning("v1.move_audio_clip failed project=%s clip=%s: %s", project_id, clip_id, e)
            return envelope_error(
                context,
                code="VALIDATION_ERROR",
                message=str(e),
                status_code=400,
            )

        if audio_clip is None:
            return envelope_error(
                context,
                code="AUDIO_CLIP_NOT_FOUND",
                message=f"Audio clip not found: {clip_id}",
                status_code=404,
            )

        # Get full clip ID and values from result (Pydantic model)
        result_clip_id = audio_clip.id
        duration_after = project.duration_ms or 0
        new_start_ms = audio_clip.timing.start_ms
        new_track_id = audio_clip.track_id  # Full ID from L3AudioClipDetails

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="audio_clip",
                entity_id=result_clip_id,
                change_type="modified",
                before={"start_ms": original_start_ms, "track_id": original_track_id},
                after={"start_ms": new_start_ms, "track_id": new_track_id},
            )
        ]

        # Record operation first to get operation_id
        internal_request = body.to_internal_request()
        operation = await operation_service.record_operation(
            project=project,
            operation_type="move_audio_clip",
            source="api_v1",
            success=True,
            affected_audio_clips=[result_clip_id],
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint=f"/audio-clips/{result_clip_id}/move",
                method="PATCH",
                target_ids=[result_clip_id],
                key_params={"new_start_ms": internal_request.new_start_ms, "new_track_id": internal_request.new_track_id},
            ),
            result_summary=ResultSummary(
                success=True,
                modified_ids=[result_clip_id],
            ),
            rollback_data={
                "clip_id": result_clip_id,
                "original_start_ms": original_start_ms,
                "original_track_id": original_track_id,
                "new_start_ms": new_start_ms,
                "new_track_id": new_track_id,
            },
            rollback_available=True,
            idempotency_key=header_result.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="move_audio_clip",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "move_audio_clip", "clip_id": result_clip_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "audio_clip": audio_clip.model_dump(),
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.move_audio_clip ok project=%s clip=%s", project_id, result_clip_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.move_audio_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.delete(
    "/projects/{project_id}/audio-clips/{clip_id}",
    response_model=EnvelopeResponse,
    summary="Delete an audio clip",
    description="Delete an audio clip from the timeline.",
)
async def delete_audio_clip(
    project_id: UUID,
    clip_id: str,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    body: DeleteAudioClipV1Request | None = None,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Delete an audio clip.

    Note: Request body is optional. If provided, supports validate_only mode.
    """
    context = create_request_context()
    logger.info("v1.delete_audio_clip project=%s clip=%s", project_id, clip_id)

    # Determine validate_only from request body if present
    validate_only = body.options.validate_only if body else False

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            http_request, context, validate_only=validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        if validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_delete_audio_clip(
                    project, clip_id
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.delete_audio_clip failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (supports partial ID)
        duration_before = project.duration_ms or 0
        original_clip_state, full_clip_id = _find_audio_clip_state(project, clip_id)
        if not original_clip_state or not full_clip_id:
            return envelope_error(
                context,
                code="AUDIO_CLIP_NOT_FOUND",
                message=f"Audio clip not found: {clip_id}",
                status_code=404,
            )
        original_track_id = original_clip_state.get("track_id")
        clip_data = {k: v for k, v in original_clip_state.items() if k != "track_id"}

        try:
            deleted = await service.delete_audio_clip(project, clip_id)
        except DougaError as exc:
            logger.warning("v1.delete_audio_clip failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        if not deleted:
            return envelope_error(
                context,
                code="AUDIO_CLIP_NOT_FOUND",
                message=f"Audio clip not found: {clip_id}",
                status_code=404,
            )

        # Calculate duration after
        duration_after = project.duration_ms or 0

        # Build diff changes using full clip ID
        changes = [
            ChangeDetail(
                entity_type="audio_clip",
                entity_id=full_clip_id,
                change_type="deleted",
                before=clip_data,
                after=None,
            )
        ]

        # Record operation first to get operation_id
        operation = await operation_service.record_operation(
            project=project,
            operation_type="delete_audio_clip",
            source="api_v1",
            success=True,
            affected_audio_clips=[full_clip_id],
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint=f"/audio-clips/{full_clip_id}",
                method="DELETE",
                target_ids=[full_clip_id],
                key_params={},
            ),
            result_summary=ResultSummary(
                success=True,
                deleted_ids=[full_clip_id],
            ),
            rollback_data={
                "clip_id": full_clip_id,
                "clip_data": clip_data,
                "track_id": original_track_id,
            },
            rollback_available=True,
            idempotency_key=header_result.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="delete_audio_clip",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "delete_audio_clip", "clip_id": full_clip_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info (use full_clip_id for consistency)
        include_diff = body.options.include_diff if body else False
        response_data: dict = {
            "deleted": True,
            "clip_id": full_clip_id,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.delete_audio_clip ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.delete_audio_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.post(
    "/projects/{project_id}/audio-tracks",
    response_model=EnvelopeResponse,
    summary="Add a new audio track",
    description="Add a new audio track to the project.",
)
async def add_audio_track(
    project_id: UUID,
    body: AddAudioTrackV1Request,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Add a new audio track to the project.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()
    logger.info("v1.add_audio_track project=%s name=%s type=%s", project_id, body.track.name, body.track.type)

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        if body.options.validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_add_audio_track(
                    project, body.track
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.add_audio_track failed project=%s code=%s: %s", project_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        try:
            track_summary = await service.add_audio_track(
                project,
                name=body.track.name,
                track_type=body.track.type,
                volume=body.track.volume,
                muted=body.track.muted,
                ducking_enabled=body.track.ducking_enabled,
                insert_at=body.track.insert_at,
            )
        except DougaError as exc:
            logger.warning("v1.add_audio_track failed project=%s code=%s: %s", project_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
            logger.warning("v1.add_audio_track failed project=%s: %s", project_id, e)
            return envelope_error(
                context,
                code="VALIDATION_ERROR",
                message=str(e),
                status_code=400,
            )

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "add_audio_track", "track_id": track_summary.id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        logger.info("v1.add_audio_track ok project=%s track=%s", project_id, track_summary.id)
        return envelope_success(context, {"audio_track": track_summary.model_dump()})

    except HTTPException as exc:
        logger.warning("v1.add_audio_track failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# Priority 4: Marker Endpoints
# =============================================================================


@router.post(
    "/projects/{project_id}/markers",
    response_model=EnvelopeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a marker",
    description="Add a marker to the timeline.",
)
async def add_marker(
    project_id: UUID,
    body: AddMarkerV1Request,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Add a marker to the timeline.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()
    logger.info("v1.add_marker project=%s time_ms=%s name=%s", project_id, body.marker.time_ms, body.marker.name)

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        if body.options.validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_add_marker(
                    project, body.marker
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.add_marker failed project=%s code=%s: %s", project_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (markers don't affect duration)
        duration_before = project.duration_ms or 0

        try:
            marker_data = await service.add_marker(project, body.marker)
        except DougaError as exc:
            logger.warning("v1.add_marker failed project=%s code=%s: %s", project_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        # Calculate duration after
        duration_after = project.duration_ms or 0
        marker_id = marker_data["id"]

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="marker",
                entity_id=marker_id,
                change_type="created",
                before=None,
                after=marker_data,
            )
        ]

        # Record operation first to get operation_id
        operation = await operation_service.record_operation(
            project=project,
            operation_type="add_marker",
            source="api_v1",
            success=True,
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint="/markers",
                method="POST",
                target_ids=[],
                key_params={"time_ms": body.marker.time_ms, "name": body.marker.name},
            ),
            result_summary=ResultSummary(
                success=True,
                created_ids=[marker_id],
            ),
            rollback_data={
                "marker_id": marker_id,
                "marker_data": marker_data,
            },
            rollback_available=True,
            idempotency_key=header_result.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="add_marker",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "add_marker", "marker_id": marker_data["id"]},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "marker": marker_data,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.add_marker ok project=%s marker=%s", project_id, marker_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.add_marker failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.patch(
    "/projects/{project_id}/markers/{marker_id}",
    response_model=EnvelopeResponse,
    summary="Update a marker",
    description="Update an existing marker. Supports partial ID matching.",
)
async def update_marker(
    project_id: UUID,
    marker_id: str,
    body: UpdateMarkerV1Request,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Update an existing marker.

    Supports validate_only mode for dry-run validation.
    Marker ID can be a partial prefix match.
    """
    context = create_request_context()
    logger.info("v1.update_marker project=%s marker=%s", project_id, marker_id)

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        if body.options.validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_update_marker(
                    project, marker_id, body.marker
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.update_marker failed project=%s marker=%s code=%s: %s", project_id, marker_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (supports partial ID)
        duration_before = project.duration_ms or 0
        original_marker_state, full_marker_id = _find_marker_state(project, marker_id)
        if not original_marker_state or not full_marker_id:
            return envelope_error(
                context,
                code="MARKER_NOT_FOUND",
                message=f"Marker not found: {marker_id}",
                status_code=404,
            )
        # Save original state for rollback
        original_state = original_marker_state.copy()

        try:
            marker_data = await service.update_marker(project, marker_id, body.marker)
        except DougaError as exc:
            logger.warning("v1.update_marker failed project=%s marker=%s code=%s: %s", project_id, marker_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        # Get actual marker ID from result
        actual_marker_id = marker_data["id"]
        duration_after = project.duration_ms or 0

        # Build diff changes - only include changed fields
        before_changes = {}
        after_changes = {}
        if body.marker.time_ms is not None:
            before_changes["time_ms"] = original_state.get("time_ms")
            after_changes["time_ms"] = marker_data.get("time_ms")
        if body.marker.name is not None:
            before_changes["name"] = original_state.get("name")
            after_changes["name"] = marker_data.get("name")
        if body.marker.color is not None:
            before_changes["color"] = original_state.get("color")
            after_changes["color"] = marker_data.get("color")

        changes = [
            ChangeDetail(
                entity_type="marker",
                entity_id=actual_marker_id,
                change_type="modified",
                before=before_changes,
                after=after_changes,
            )
        ]

        # Record operation first to get operation_id
        operation = await operation_service.record_operation(
            project=project,
            operation_type="update_marker",
            source="api_v1",
            success=True,
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint=f"/markers/{actual_marker_id}",
                method="PATCH",
                target_ids=[actual_marker_id],
                key_params=_serialize_for_json(body.marker.model_dump(exclude_none=True)),
            ),
            result_summary=ResultSummary(
                success=True,
                modified_ids=[actual_marker_id],
            ),
            rollback_data={
                "marker_id": actual_marker_id,
                "original_state": original_state,
                "new_state": marker_data,
            },
            rollback_available=True,
            idempotency_key=header_result.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="update_marker",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "update_marker", "marker_id": actual_marker_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "marker": marker_data,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.update_marker ok project=%s marker=%s", project_id, actual_marker_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.update_marker failed project=%s marker=%s: %s", project_id, marker_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.delete(
    "/projects/{project_id}/markers/{marker_id}",
    response_model=EnvelopeResponse,
    summary="Delete a marker",
    description="Delete a marker from the timeline. Supports partial ID matching.",
)
async def delete_marker(
    project_id: UUID,
    marker_id: str,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    body: DeleteMarkerV1Request | None = None,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Delete a marker from the timeline.

    Note: Request body is optional. If provided, supports validate_only mode.
    Marker ID can be a partial prefix match.
    """
    context = create_request_context()
    logger.info("v1.delete_marker project=%s marker=%s", project_id, marker_id)

    # Determine validate_only from request body if present
    validate_only = body.options.validate_only if body else False

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            http_request, context, validate_only=validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        if validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_delete_marker(
                    project, marker_id
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.delete_marker failed project=%s marker=%s code=%s: %s", project_id, marker_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (markers don't affect duration)
        duration_before = project.duration_ms or 0

        try:
            marker_data = await service.delete_marker(project, marker_id)
        except DougaError as exc:
            logger.warning("v1.delete_marker failed project=%s marker=%s code=%s: %s", project_id, marker_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        # Calculate duration after
        duration_after = project.duration_ms or 0
        actual_marker_id = marker_data["id"]

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="marker",
                entity_id=actual_marker_id,
                change_type="deleted",
                before=marker_data,
                after=None,
            )
        ]

        # Record operation first to get operation_id
        operation = await operation_service.record_operation(
            project=project,
            operation_type="delete_marker",
            source="api_v1",
            success=True,
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint=f"/markers/{actual_marker_id}",
                method="DELETE",
                target_ids=[actual_marker_id],
                key_params={},
            ),
            result_summary=ResultSummary(
                success=True,
                deleted_ids=[actual_marker_id],
            ),
            rollback_data={
                "marker_id": actual_marker_id,
                "marker_data": marker_data,
            },
            rollback_available=True,
            idempotency_key=header_result.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="delete_marker",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "delete_marker", "marker_id": marker_data["id"]},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        include_diff = body.options.include_diff if body else False
        response_data: dict = {
            "marker": marker_data,
            "deleted": True,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.delete_marker ok project=%s marker=%s", project_id, actual_marker_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.delete_marker failed project=%s marker=%s: %s", project_id, marker_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# Priority 5: Advanced Read Endpoints
# =============================================================================


@router.get(
    "/projects/{project_id}/clips/{clip_id}",
    response_model=EnvelopeResponse,
    summary="Get single clip details",
    description="Get detailed information about a specific clip. Supports partial ID matching.",
)
async def get_clip_details(
    project_id: UUID,
    clip_id: str,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Get detailed information about a specific clip.

    Returns L3 clip details including timing, transform, effects,
    and neighboring clip context.
    """
    context = create_request_context()
    logger.info("v1.get_clip_details project=%s clip=%s", project_id, clip_id)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)

        service = AIService(db)
        clip_details: L3ClipDetails | None = await service.get_clip_details(
            project, clip_id
        )

        if clip_details is None:
            return envelope_error(
                context,
                code="CLIP_NOT_FOUND",
                message=f"Clip not found: {clip_id}",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return envelope_success(context, clip_details.model_dump())

    except HTTPException as exc:
        logger.warning("v1.get_clip_details failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.get(
    "/projects/{project_id}/at-time/{time_ms}",
    response_model=EnvelopeResponse,
    summary="Get timeline state at specific time",
    description="Get what clips are active at a specific point in time.",
)
async def get_timeline_at_time(
    project_id: UUID,
    time_ms: int,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Get timeline state at a specific time.

    Returns all active clips at the given timestamp with progress information.
    """
    context = create_request_context()
    logger.info("v1.get_timeline_at_time project=%s time_ms=%s", project_id, time_ms)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)

        # Validate time range
        if time_ms < 0:
            return envelope_error(
                context,
                code="INVALID_TIME_RANGE",
                message=f"time_ms must be >= 0, got {time_ms}",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        service = AIService(db)
        data: L2TimelineAtTime = await service.get_timeline_at_time(project, time_ms)
        return envelope_success(context, data.model_dump())

    except HTTPException as exc:
        logger.warning("v1.get_timeline_at_time failed project=%s time_ms=%s: %s", project_id, time_ms, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# Priority 5: Batch Operations
# =============================================================================


@router.post(
    "/projects/{project_id}/batch",
    response_model=EnvelopeResponse,
    summary="Execute batch operations",
    description="Execute multiple clip operations in a single request.",
)
async def execute_batch(
    project_id: UUID,
    body: BatchOperationV1Request,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Execute multiple clip operations in a batch.

    Supports validate_only mode for dry-run validation.
    Operations are executed in order. If one fails, others may still succeed.
    """
    context = create_request_context()
    logger.info("v1.execute_batch project=%s ops=%s", project_id, len(body.operations))

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Check max_batch_ops limit (20, matches capabilities)
        MAX_BATCH_OPS = 20
        if len(body.operations) > MAX_BATCH_OPS:
            return envelope_error(
                context,
                code="VALIDATION_ERROR",
                message=f"Batch contains {len(body.operations)} operations, exceeds limit of {MAX_BATCH_OPS}",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if body.options.validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_batch_operations(
                    project, body.operations
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.execute_batch failed project=%s code=%s: %s", project_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual batch operations
        service = AIService(db)
        operation_service = OperationService(db)
        try:
            result: BatchOperationResult = await service.execute_batch_operations(
                project, body.operations,
                rollback_on_failure=body.options.rollback_on_failure,
                continue_on_error=body.options.continue_on_error,
                include_audio=body.options.include_audio,
            )
        except DougaError as exc:
            logger.warning("v1.execute_batch failed project=%s code=%s: %s", project_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)
        except Exception as exc:
            logger.error("v1.execute_batch unexpected error project=%s: %s", project_id, exc)
            return envelope_error(
                context,
                code="BATCH_EXECUTION_ERROR",
                message=f"Batch execution failed: {exc}",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Only flag_modified after successful operation
        if result.successful_operations > 0:
            flag_modified(project, "timeline_data")

        # Collect created_ids from individual operation results
        created_ids: list[str] = []
        affected_clips: list[str] = []
        for op_result in result.results:
            if isinstance(op_result, dict):
                if "clip_id" in op_result:
                    created_ids.append(str(op_result["clip_id"]))
                    affected_clips.append(str(op_result["clip_id"]))
                elif "id" in op_result:
                    created_ids.append(str(op_result["id"]))
                    affected_clips.append(str(op_result["id"]))

        # Record batch as a single operation in history
        operation = await operation_service.record_operation(
            project=project,
            operation_type="batch",
            source="api_v1",
            success=result.success,
            affected_clips=affected_clips,
            affected_layers=[],
            diff=None,
            request_summary=RequestSummary(
                endpoint="/batch",
                method="POST",
                target_ids=affected_clips,
                key_params=_serialize_for_json({
                    "total_operations": result.total_operations,
                    "operation_types": [op.operation for op in body.operations],
                }),
            ),
            result_summary=ResultSummary(
                success=result.success,
                created_ids=created_ids,
                message=f"Batch: {result.successful_operations}/{result.total_operations} succeeded",
            ),
            rollback_data=None,
            rollback_available=False,
            idempotency_key=header_result.get("idempotency_key"),
        )

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "batch"},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        logger.info("v1.execute_batch ok project=%s success=%s fail=%s", project_id, result.successful_operations, result.failed_operations)

        # Include operation_id in response
        response_data = result.model_dump()
        response_data["operation_id"] = str(operation.id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.execute_batch failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# Priority 5: Semantic Operations
# =============================================================================


@router.post(
    "/projects/{project_id}/semantic",
    response_model=EnvelopeResponse,
    summary="Execute semantic operation",
    description="Execute a high-level semantic operation.",
)
async def execute_semantic(
    project_id: UUID,
    body: SemanticOperationV1Request,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Execute a semantic operation.

    Supports validate_only mode for dry-run validation.

    Available operations:
    - snap_to_previous: Move clip to end of previous clip
    - snap_to_next: Move next clip to end of this clip
    - close_gap: Remove gaps in a layer
    - auto_duck_bgm: Enable BGM ducking
    - rename_layer: Rename a layer
    """
    context = create_request_context()
    sem_op = body.resolved_operation
    logger.info("v1.execute_semantic project=%s op=%s", project_id, sem_op.operation)

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        if body.options.validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_semantic_operation(
                    project, sem_op
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.execute_semantic failed project=%s code=%s: %s", project_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual semantic operation
        service = AIService(db)
        operation_service = OperationService(db)
        try:
            result: SemanticOperationResult = await service.execute_semantic_operation(
                project, sem_op
            )
        except DougaError as exc:
            logger.warning("v1.execute_semantic failed project=%s code=%s: %s", project_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        # If semantic operation failed, return structured error
        if not result.success:
            return envelope_error(
                context,
                code="SEMANTIC_OPERATION_FAILED",
                message=result.error_message or f"Semantic operation '{sem_op.operation}' failed",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Only flag_modified after successful operation with changes
        if result.changes_made:
            flag_modified(project, "timeline_data")

        # Record operation for history and rollback
        operation = await operation_service.record_operation(
            project=project,
            operation_type=f"semantic_{sem_op.operation}",
            source="api_v1",
            success=True,
            affected_clips=result.affected_clip_ids,
            affected_layers=[sem_op.target_layer_id] if sem_op.target_layer_id else [],
            diff=None,
            request_summary=RequestSummary(
                endpoint="/semantic",
                method="POST",
                target_ids=[sem_op.target_clip_id or sem_op.target_layer_id or ""],
                key_params=_serialize_for_json({"operation": sem_op.operation, "parameters": sem_op.parameters}),
            ),
            result_summary=ResultSummary(
                success=True,
                modified_ids=result.affected_clip_ids,
                message="; ".join(result.changes_made) if result.changes_made else None,
            ),
            rollback_data=None,
            rollback_available=False,
            idempotency_key=header_result.get("idempotency_key"),
        )

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={
                "source": "ai_v1",
                "operation": f"semantic_{sem_op.operation}",
            },
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        logger.info("v1.execute_semantic ok project=%s op=%s", project_id, sem_op.operation)

        # Build response with operation info
        response_data = result.model_dump()
        response_data["operation_id"] = str(operation.id)
        response_data["rollback_available"] = operation.rollback_available
        response_data["rollback_url"] = f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None
        if not operation.rollback_available:
            response_data.setdefault("hints", []).append(
                "To undo semantic ops: use DELETE or PATCH on affected individual clips"
            )
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.execute_semantic failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# Phase 2+3: History and Rollback Endpoints
# =============================================================================


@router.get(
    "/projects/{project_id}/history",
    response_model=EnvelopeResponse,
    summary="Get operation history",
    description="Get paginated list of operations performed on this project.",
)
async def get_history(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    page: int = 1,
    page_size: int = 20,
    operation_type: str | None = None,
    source: str | None = None,
    success_only: bool = False,
    clip_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Get operation history for a project.

    Returns a paginated list of operations with filtering options.

    Args:
        since: Return operations created after this timestamp (ISO 8601)
        until: Return operations created before this timestamp (ISO 8601)
    """
    context = create_request_context()
    logger.info("v1.get_history project=%s page=%s", project_id, page)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)

        operation_service = OperationService(db)
        query = HistoryQuery(
            page=page,
            page_size=page_size,
            operation_type=operation_type,
            source=source,
            success_only=success_only,
            clip_id=clip_id,
            since=since,
            until=until,
        )
        history: HistoryResponse = await operation_service.get_history(
            project.id, query
        )

        # Populate rollback_url for each operation
        for op in history.operations:
            if op.rollback_available:
                op.rollback_url = f"/api/ai/v1/projects/{project_id}/operations/{op.id}/rollback"
            else:
                op.rollback_url = None

        return envelope_success(context, history.model_dump())

    except HTTPException as exc:
        logger.warning("v1.get_history failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.get(
    "/projects/{project_id}/operations/{operation_id}",
    response_model=EnvelopeResponse,
    summary="Get operation details",
    description="Get detailed information about a specific operation.",
)
async def get_operation(
    project_id: UUID,
    operation_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Get details of a specific operation.

    Returns full operation record including diff and rollback information.
    """
    context = create_request_context()
    logger.info("v1.get_operation project=%s operation=%s", project_id, operation_id)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)

        operation_service = OperationService(db)
        try:
            record: OperationRecord = await operation_service.get_operation_record(
                project.id, operation_id
            )
            return envelope_success(context, record.model_dump())
        except DougaError as exc:
            logger.warning("v1.get_operation failed project=%s operation=%s code=%s: %s", project_id, operation_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

    except HTTPException as exc:
        logger.warning("v1.get_operation failed project=%s operation=%s: %s", project_id, operation_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.post(
    "/projects/{project_id}/operations/{operation_id}/rollback",
    response_model=EnvelopeResponse,
    summary="Rollback an operation",
    description="Rollback a previous operation to restore the timeline state.",
)
async def rollback_operation(
    project_id: UUID,
    operation_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    body: RollbackRequest | None = None,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Rollback a previous operation.

    This creates a new operation that reverses the effects of the original.
    Not all operations can be rolled back - check rollback_available flag.
    """
    context = create_request_context()
    logger.info("v1.rollback_operation project=%s operation=%s", project_id, operation_id)

    # Validate headers (Idempotency-Key required for mutations)
    headers = validate_headers(http_request, context, validate_only=False)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if headers["if_match"] and headers["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        operation_service = OperationService(db)
        try:
            rollback_response, rollback_op = await operation_service.rollback_operation(
                project,
                operation_id,
                user_id=current_user.id,
                idempotency_key=headers["idempotency_key"],
            )
        except DougaError as exc:
            logger.warning("v1.rollback_operation failed project=%s operation=%s code=%s: %s", project_id, operation_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={
                "source": "ai_v1",
                "operation": "rollback",
                "original_operation_id": str(operation_id),
                "rollback_operation_id": str(rollback_op.id),
            },
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        logger.info("v1.rollback_operation ok project=%s operation=%s", project_id, operation_id)
        return envelope_success(context, rollback_response.model_dump())

    except HTTPException as exc:
        logger.warning("v1.rollback_operation failed project=%s operation=%s: %s", project_id, operation_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# Wave 1: Read-Only + Small Extension Endpoints
# =============================================================================


@router.get(
    "/projects/{project_id}/audio-clips/{clip_id}",
    response_model=EnvelopeResponse,
    summary="Get single audio clip details",
    description="Get detailed information about a specific audio clip. Supports partial ID matching.",
)
async def get_audio_clip_details(
    project_id: UUID,
    clip_id: str,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Get detailed information about a specific audio clip.

    Returns L3 audio clip details including timing, volume, fades,
    and neighboring clip context.
    """
    context = create_request_context()
    logger.info("v1.get_audio_clip_details project=%s clip=%s", project_id, clip_id)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)

        service = AIService(db)
        clip_details: L3AudioClipDetails | None = await service.get_audio_clip_details(
            project, clip_id
        )

        if clip_details is None:
            return envelope_error(
                context,
                code="AUDIO_CLIP_NOT_FOUND",
                message=f"Audio clip not found: {clip_id}",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return envelope_success(context, clip_details.model_dump())

    except HTTPException as exc:
        logger.warning("v1.get_audio_clip_details failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.get(
    "/schemas",
    response_model=EnvelopeResponse,
    summary="Get available schema definitions",
    description=(
        "Returns a list of all available AI API schemas with their descriptions and endpoints. "
        "Use ?detail=full to include full JSON Schema field definitions for each schema."
    ),
)
async def get_schemas(
    current_user: CurrentUser,
    response: Response,
    detail: str = "summary",
) -> EnvelopeResponse:
    """Get available schema definitions.

    Returns information about all schema levels (L1, L2, L2.5, L3)
    and write/analysis schemas.

    Query params:
        detail: "summary" (default) returns names and descriptions only.
                "full" includes json_schema with field definitions for each schema.
    """
    context = create_request_context()
    logger.info("v1.get_schemas detail=%s", detail)

    # Each entry: (name, description, level, token_estimate, endpoint, model_class_or_None)
    _schema_entries: list[dict] = [
        {
            "name": "L1ProjectOverview",
            "description": "Lightweight project overview with summary statistics",
            "level": "L1",
            "token_estimate": "~300 tokens",
            "endpoint": "GET /projects/{project_id}/overview",
            "model": L1ProjectOverview,
        },
        {
            "name": "L2TimelineStructure",
            "description": "Timeline layer/track structure without clip details",
            "level": "L2",
            "token_estimate": "~800 tokens",
            "endpoint": "GET /projects/{project_id}/structure",
            "model": L2TimelineStructure,
        },
        {
            "name": "L2AssetCatalog",
            "description": "Available assets with usage counts",
            "level": "L2",
            "token_estimate": "~500 tokens",
            "endpoint": "GET /projects/{project_id}/assets",
            "model": L2AssetCatalog,
        },
        {
            "name": "L2TimelineAtTime",
            "description": "Active clips at a specific timestamp",
            "level": "L2",
            "token_estimate": "~400 tokens",
            "endpoint": "GET /projects/{project_id}/at-time/{time_ms}",
            "model": L2TimelineAtTime,
        },
        {
            "name": "L25TimelineOverview",
            "description": "Full timeline overview with clip summaries, gaps, and overlaps",
            "level": "L2",
            "token_estimate": "~2000 tokens",
            "endpoint": "GET /projects/{project_id}/timeline-overview",
            "model": L25TimelineOverview,
        },
        {
            "name": "L3ClipDetails",
            "description": "Full details for a single video clip with neighbors",
            "level": "L3",
            "token_estimate": "~400 tokens/clip",
            "endpoint": "GET /projects/{project_id}/clips/{clip_id}",
            "model": L3ClipDetails,
        },
        {
            "name": "L3AudioClipDetails",
            "description": "Full details for a single audio clip with neighbors",
            "level": "L3",
            "token_estimate": "~300 tokens/clip",
            "endpoint": "GET /projects/{project_id}/audio-clips/{clip_id}",
            "model": L3AudioClipDetails,
        },
        {
            "name": "AddClipRequest",
            "description": "Add a new video clip to a layer",
            "level": "write",
            "token_estimate": "~200 tokens",
            "endpoint": "POST /projects/{project_id}/clips",
            "model": AddClipRequest,
            "example_body": {
                "clip": {
                    "asset_id": "uuid-here",
                    "layer_id": "uuid-here",
                    "start_ms": 0,
                    "duration_ms": 5000,
                },
            },
        },
        {
            "name": "MoveClipRequest",
            "description": "Move a clip to a different layer or position",
            "level": "write",
            "token_estimate": "~150 tokens",
            "endpoint": "PATCH /projects/{project_id}/clips/{clip_id}/move",
            "model": MoveClipRequest,
            "example_body": {
                "move": {
                    "new_start_ms": 5000,
                },
            },
        },
        {
            "name": "UpdateClipTimingRequest",
            "description": "Update clip timing (duration, speed, in/out points)",
            "level": "write",
            "token_estimate": "~150 tokens",
            "endpoint": "PATCH /projects/{project_id}/clips/{clip_id}/timing",
            "model": UpdateClipTimingRequest,
            "example_body": {
                "timing": {
                    "duration_ms": 5000,
                    "in_point_ms": 0,
                    "out_point_ms": 5000,
                },
            },
        },
        {
            "name": "UpdateClipTransformRequest",
            "description": "Update clip transform (position, scale, rotation, opacity)",
            "level": "write",
            "token_estimate": "~150 tokens",
            "endpoint": "PATCH /projects/{project_id}/clips/{clip_id}/transform",
            "model": UpdateClipTransformRequest,
            "example_body": {
                "transform": {
                    "x": 0,
                    "y": 0,
                    "scale": 1.0,
                },
            },
        },
        {
            "name": "UpdateClipEffectsRequest",
            "description": "Update clip visual effects (filters, color correction, etc.)",
            "level": "write",
            "token_estimate": "~200 tokens",
            "endpoint": "PATCH /projects/{project_id}/clips/{clip_id}/effects",
            "model": UpdateClipEffectsRequest,
            "example_body": {
                "effects": {
                    "opacity": 1.0,
                    "fade_in_ms": 500,
                    "fade_out_ms": 500,
                },
            },
        },
        {
            "name": "UpdateClipTextRequest",
            "description": "Update text content for text clips",
            "level": "write",
            "token_estimate": "~100 tokens",
            "endpoint": "PATCH /projects/{project_id}/clips/{clip_id}/text",
            "model": UpdateClipTextRequest,
            "example_body": {
                "text": {
                    "text_content": "Hello World",
                },
            },
        },
        {
            "name": "UpdateClipTextStyleRequest",
            "description": "Update text style (font, size, color, alignment, etc.)",
            "level": "write",
            "token_estimate": "~200 tokens",
            "endpoint": "PATCH /projects/{project_id}/clips/{clip_id}/text-style",
            "model": UpdateClipTextStyleRequest,
            "example_body": {
                "text_style": {
                    "font_size": 48,
                    "font_family": "Noto Sans JP",
                    "color": "#FFFFFF",
                },
            },
        },
        {
            "name": "AddAudioClipRequest",
            "description": "Add a new audio clip to a track",
            "level": "write",
            "token_estimate": "~200 tokens",
            "endpoint": "POST /projects/{project_id}/audio-clips",
            "model": AddAudioClipRequest,
            "example_body": {
                "clip": {
                    "asset_id": "uuid-here",
                    "track_id": "uuid-here",
                    "start_ms": 0,
                    "duration_ms": 5000,
                },
            },
        },
        {
            "name": "SemanticOperation",
            "description": "High-level semantic operations (snap, close gap, auto duck, etc.)",
            "level": "write",
            "token_estimate": "~150 tokens",
            "endpoint": "POST /projects/{project_id}/semantic",
            "model": SemanticOperation,
            "example_body": {
                "semantic": {
                    "operation": "close_all_gaps",
                    "target_layer_id": "uuid-here",
                },
            },
        },
        {
            "name": "BatchClipOperation",
            "description": "Batch multiple clip operations in a single request",
            "level": "write",
            "token_estimate": "~300 tokens",
            "endpoint": "POST /projects/{project_id}/batch",
            "model": BatchClipOperation,
            "example_body": {
                "operations": [
                    {"operation": "move", "clip_id": "uuid-here", "move": {"new_start_ms": 5000}},
                    {"operation": "update_effects", "clip_id": "uuid-here", "effects": {"fade_in_ms": 500}},
                    {"operation": "update_text_style", "clip_id": "uuid-here", "text_style": {"font_size": 48, "color": "#FFFFFF"}},
                ],
            },
        },
        {
            "name": "OperationOptions",
            "description": "Common options for write operations (dry_run, skip_validation, etc.)",
            "level": "write",
            "token_estimate": "~100 tokens",
            "endpoint": "(included in request body of write endpoints)",
            "model": OperationOptions,
        },
        {
            "name": "GapAnalysisResult",
            "description": "Find gaps in the timeline across layers and tracks",
            "level": "analysis",
            "token_estimate": "~500 tokens",
            "endpoint": "GET /projects/{project_id}/analysis/gaps",
            "model": GapAnalysisResult,
        },
        {
            "name": "PacingAnalysisResult",
            "description": "Analyze clip density and pacing across timeline segments",
            "level": "analysis",
            "token_estimate": "~600 tokens",
            "endpoint": "GET /projects/{project_id}/analysis/pacing",
            "model": PacingAnalysisResult,
        },
    ]

    # Version-based ETag for semi-static schemas (changes only on deploy)
    from src.config import get_settings as _get_settings
    _settings = _get_settings()
    response.headers["ETag"] = f'W/"schemas:{_settings.app_version}:{detail}"'

    if detail == "full":
        # Return full JSON Schema field definitions for each schema
        full_schemas: dict[str, dict] = {}
        for entry in _schema_entries:
            schema_dict: dict = {
                "description": entry["description"],
                "level": entry["level"],
                "token_estimate": entry["token_estimate"],
                "endpoint": entry["endpoint"],
                "json_schema": entry["model"].model_json_schema(),
            }
            if "example_body" in entry:
                schema_dict["example_body"] = entry["example_body"]
            full_schemas[entry["name"]] = schema_dict
        return envelope_success(context, {"schemas": full_schemas})

    # Default: summary mode (backward-compatible list format)
    summary_list: list[dict] = []
    for entry in _schema_entries:
        item: dict = {
            "name": entry["name"],
            "description": entry["description"],
            "level": entry["level"],
            "token_estimate": entry["token_estimate"],
            "endpoint": entry["endpoint"],
        }
        if "example_body" in entry:
            item["example_body"] = entry["example_body"]
        summary_list.append(item)

    return envelope_success(context, {"schemas": summary_list})


@router.get(
    "/projects/{project_id}/analysis/gaps",
    response_model=EnvelopeResponse,
    summary="Analyze timeline gaps",
    description="Find gaps in the timeline across all layers and audio tracks.",
)
async def analyze_gaps(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Analyze gaps in the timeline.

    Returns a list of all gaps (empty spaces between clips) across
    video layers and audio tracks, with total gap count and duration.
    """
    context = create_request_context()
    logger.info("v1.analyze_gaps project=%s", project_id)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)

        service = AIService(db)
        result: GapAnalysisResult = await service.analyze_gaps(project)
        return envelope_success(context, result.model_dump())

    except HTTPException as exc:
        logger.warning("v1.analyze_gaps failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.get(
    "/projects/{project_id}/analysis/pacing",
    response_model=EnvelopeResponse,
    summary="Analyze timeline pacing",
    description="Analyze clip density and pacing across timeline segments.",
)
async def analyze_pacing(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    segment_duration_ms: int = 30000,
    strategy: Annotated[str, Query(description="Segmentation strategy: 'fixed_interval' or 'content_aware'")] = "content_aware",
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Analyze timeline pacing.

    Divides the timeline into segments and analyzes clip density,
    average clip duration, and suggests improvements.

    The `strategy` parameter controls how segments are determined:
    - `content_aware` (default): segments derived from natural clip boundaries.
    - `fixed_interval`: uniform segments of `segment_duration_ms` width.
    """
    if strategy not in ("fixed_interval", "content_aware"):
        strategy = "content_aware"

    context = create_request_context()
    logger.info("v1.analyze_pacing project=%s segment=%s strategy=%s", project_id, segment_duration_ms, strategy)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)

        service = AIService(db)
        result: PacingAnalysisResult = await service.analyze_pacing(
            project, segment_duration_ms=segment_duration_ms, strategy=strategy,
        )
        return envelope_success(context, result.model_dump())

    except HTTPException as exc:
        logger.warning("v1.analyze_pacing failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# #080: PATCH /audio-clips/{clip_id} - Update audio clip properties
# =============================================================================


@router.patch(
    "/projects/{project_id}/audio-clips/{clip_id}",
    response_model=EnvelopeResponse,
    summary="Update audio clip properties",
    description="Update audio clip volume, fade_in_ms, fade_out_ms, and volume_keyframes.",
)
async def update_audio_clip(
    project_id: UUID,
    clip_id: str,
    request: UpdateAudioClipV1Request,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Update audio clip properties (volume, fades).

    Supports:
    - volume: 0.0-2.0
    - fade_in_ms: 0-10000ms fade in duration
    - fade_out_ms: 0-10000ms fade out duration
    - volume_keyframes: List of {time_ms, value} keyframes for volume envelope
    """
    context = create_request_context()
    logger.info("v1.update_audio_clip project=%s clip=%s", project_id, clip_id)

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if headers["if_match"] and headers["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Convert to internal request
        internal_request = request.to_internal_request()

        # Handle validate_only mode
        if request.options.validate_only:
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_update_audio_clip(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.update_audio_clip failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0
        original_clip_state, _ = _find_audio_clip_state(project, clip_id)
        original_audio_props = {
            "volume": original_clip_state.get("volume", 1.0),
            "fade_in_ms": original_clip_state.get("fade_in_ms", 0),
            "fade_out_ms": original_clip_state.get("fade_out_ms", 0),
            "volume_keyframes": original_clip_state.get("volume_keyframes", []),
        } if original_clip_state else {}

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_audio_clip(project, clip_id, internal_request)
        except DougaError as exc:
            logger.warning("v1.update_audio_clip failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        if result is None:
            return envelope_error(
                context,
                code="INTERNAL_ERROR",
                message="Failed to update audio clip",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and new state from result
        full_clip_id = result.id
        duration_after = project.duration_ms or 0

        # Get new state
        new_clip_state, _ = _find_audio_clip_state(project, full_clip_id)
        new_audio_props = {
            "volume": new_clip_state.get("volume", 1.0),
            "fade_in_ms": new_clip_state.get("fade_in_ms", 0),
            "fade_out_ms": new_clip_state.get("fade_out_ms", 0),
            "volume_keyframes": new_clip_state.get("volume_keyframes", []),
        } if new_clip_state else {}

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="audio_clip",
                entity_id=full_clip_id,
                change_type="modified",
                before=original_audio_props,
                after=new_audio_props,
            )
        ]

        # Record operation
        operation = await operation_service.record_operation(
            project=project,
            operation_type="update_audio_clip",
            source="api_v1",
            success=True,
            affected_clips=[full_clip_id],
            diff=None,
            request_summary=RequestSummary(
                endpoint=f"/audio-clips/{full_clip_id}",
                method="PATCH",
                target_ids=[full_clip_id],
                key_params=_serialize_for_json(internal_request.model_dump(exclude_none=True)),
            ),
            result_summary=ResultSummary(
                success=True,
                modified_ids=[full_clip_id],
            ),
            rollback_data=None,
            rollback_available=False,
            idempotency_key=headers.get("idempotency_key"),
        )

        # Compute diff
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="update_audio_clip",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "update_audio_clip", "clip_id": clip_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Rollback not yet implemented for audio clip property updates; re-apply previous values manually" if not operation.rollback_available else "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo",
        }
        if not operation.rollback_available:
            response_data.setdefault("hints", []).append(
                "To undo: re-apply previous values via PATCH /audio-clips/{clip_id}"
            )
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.update_audio_clip ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.update_audio_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# #083: PATCH /clips/{clip_id}/timing - Update clip timing
# =============================================================================


@router.patch(
    "/projects/{project_id}/clips/{clip_id}/timing",
    response_model=EnvelopeResponse,
    summary="Update clip timing",
    description="Update clip duration, speed, in/out points.",
)
async def update_clip_timing(
    project_id: UUID,
    clip_id: str,
    request: UpdateClipTimingV1Request,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Update clip timing properties.

    Supports:
    - duration_ms: New clip duration (1-3600000)
    - speed: Playback speed multiplier (0.1-10.0)
    - in_point_ms: Trim start in source
    - out_point_ms: Trim end in source
    """
    context = create_request_context()
    logger.info("v1.update_clip_timing project=%s clip=%s", project_id, clip_id)

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if headers["if_match"] and headers["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Convert to internal request
        internal_request = request.to_internal_request()

        # Handle validate_only mode
        if request.options.validate_only:
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_update_clip_timing(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.update_clip_timing failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_timing = {
            "start_ms": original_clip_state.get("start_ms", 0),
            "duration_ms": original_clip_state.get("duration_ms", 0),
            "speed": original_clip_state.get("speed"),
            "in_point_ms": original_clip_state.get("in_point_ms", 0),
            "out_point_ms": original_clip_state.get("out_point_ms"),
        } if original_clip_state else {}

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_clip_timing(project, clip_id, internal_request)
        except DougaError as exc:
            logger.warning("v1.update_clip_timing failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        if result is None:
            return envelope_error(
                context,
                code="INTERNAL_ERROR",
                message="Failed to update clip timing",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and new state from result
        full_clip_id = result.id
        duration_after = project.duration_ms or 0

        # Get new state
        new_clip_state, _ = _find_clip_state(project, full_clip_id)
        new_timing = {
            "start_ms": new_clip_state.get("start_ms", 0),
            "duration_ms": new_clip_state.get("duration_ms", 0),
            "speed": new_clip_state.get("speed"),
            "in_point_ms": new_clip_state.get("in_point_ms", 0),
            "out_point_ms": new_clip_state.get("out_point_ms"),
        } if new_clip_state else {}

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="clip",
                entity_id=full_clip_id,
                change_type="modified",
                before={"timing": original_timing},
                after={"timing": new_timing},
            )
        ]

        # Record operation
        operation = await operation_service.record_operation(
            project=project,
            operation_type="update_clip_timing",
            source="api_v1",
            success=True,
            affected_clips=[full_clip_id],
            diff=None,
            request_summary=RequestSummary(
                endpoint=f"/clips/{full_clip_id}/timing",
                method="PATCH",
                target_ids=[full_clip_id],
                key_params=_serialize_for_json(internal_request.model_dump(exclude_none=True)),
            ),
            result_summary=ResultSummary(
                success=True,
                modified_ids=[full_clip_id],
            ),
            rollback_data={
                "clip_id": full_clip_id,
                "original_timing": original_timing,
                "new_timing": new_timing,
            },
            rollback_available=True,
            idempotency_key=headers.get("idempotency_key"),
        )

        # Compute diff
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="update_clip_timing",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "update_clip_timing", "clip_id": clip_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        linked_clips_updated = getattr(result, "_linked_clips_updated", [])
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if linked_clips_updated:
            response_data["linked_clips_updated"] = linked_clips_updated
        if request.auto_wrapped:
            response_data["auto_wrapped"] = True
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            {"type": "preview_seek", "seek_to_ms": result.timing.start_ms, "reason": "Start of trimmed clip"},
        ]

        logger.info("v1.update_clip_timing ok project=%s clip=%s linked_updated=%s", project_id, full_clip_id, linked_clips_updated)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.update_clip_timing failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# #085: PATCH /clips/{clip_id}/text - Update text clip content
# =============================================================================


@router.patch(
    "/projects/{project_id}/clips/{clip_id}/text",
    response_model=EnvelopeResponse,
    summary="Update text clip content",
    description="Update the text content of a text clip.",
)
async def update_clip_text(
    project_id: UUID,
    clip_id: str,
    request: UpdateClipTextV1Request,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Update text clip content.

    Only applies to text clips. Updates the text_content field.
    """
    context = create_request_context()
    logger.info("v1.update_clip_text project=%s clip=%s", project_id, clip_id)

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if headers["if_match"] and headers["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Convert to internal request
        internal_request = request.to_internal_request()

        # Handle validate_only mode
        if request.options.validate_only:
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_update_clip_text(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.update_clip_text failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_text_content = original_clip_state.get("text_content", "") if original_clip_state else ""

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_clip_text(project, clip_id, internal_request)
        except DougaError as exc:
            logger.warning("v1.update_clip_text failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        if result is None:
            return envelope_error(
                context,
                code="INTERNAL_ERROR",
                message="Failed to update clip text content",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and new state from result
        full_clip_id = result.id
        duration_after = project.duration_ms or 0

        # Get new state
        new_clip_state, _ = _find_clip_state(project, full_clip_id)
        new_text_content = new_clip_state.get("text_content", "") if new_clip_state else ""

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="clip",
                entity_id=full_clip_id,
                change_type="modified",
                before={"text_content": original_text_content},
                after={"text_content": new_text_content},
            )
        ]

        # Record operation
        operation = await operation_service.record_operation(
            project=project,
            operation_type="update_clip_text",
            source="api_v1",
            success=True,
            affected_clips=[full_clip_id],
            diff=None,
            request_summary=RequestSummary(
                endpoint=f"/clips/{full_clip_id}/text",
                method="PATCH",
                target_ids=[full_clip_id],
                key_params=_serialize_for_json(internal_request.model_dump(exclude_none=True)),
            ),
            result_summary=ResultSummary(
                success=True,
                modified_ids=[full_clip_id],
            ),
            rollback_data=None,
            rollback_available=False,
            idempotency_key=headers.get("idempotency_key"),
        )

        # Compute diff
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="update_clip_text",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "update_clip_text", "clip_id": clip_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Rollback not yet implemented for text content updates; re-apply previous values manually" if not operation.rollback_available else "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo",
        }
        if request.auto_wrapped:
            response_data["auto_wrapped"] = True
        if not operation.rollback_available:
            response_data.setdefault("hints", []).append(
                "To undo: re-apply previous text via PATCH /clips/{clip_id}/text"
            )
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.update_clip_text ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.update_clip_text failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# #087: PATCH /clips/{clip_id}/shape - Update shape clip properties
# =============================================================================


@router.patch(
    "/projects/{project_id}/clips/{clip_id}/shape",
    response_model=EnvelopeResponse,
    summary="Update shape clip properties",
    description="Update shape clip visual properties (fill, stroke, dimensions, etc.).",
)
async def update_clip_shape(
    project_id: UUID,
    clip_id: str,
    request: UpdateClipShapeV1Request,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Update shape clip properties.

    Only applies to shape clips. Supports:
    - filled: Whether shape is filled
    - fillColor: Fill color hex (#RRGGBB)
    - strokeColor: Stroke color hex (#RRGGBB)
    - strokeWidth: Stroke width (0-50)
    - width/height: Shape dimensions
    - cornerRadius: Corner radius for rounded shapes
    - fade: Fade duration in ms
    """
    context = create_request_context()
    logger.info("v1.update_clip_shape project=%s clip=%s", project_id, clip_id)

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if headers["if_match"] and headers["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Convert to internal request
        internal_request = request.to_internal_request()

        # Handle validate_only mode
        if request.options.validate_only:
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_update_clip_shape(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.update_clip_shape failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_shape_props = {}
        if original_clip_state:
            original_shape_props = {
                "filled": original_clip_state.get("filled"),
                "fillColor": original_clip_state.get("fillColor"),
                "strokeColor": original_clip_state.get("strokeColor"),
                "strokeWidth": original_clip_state.get("strokeWidth"),
                "cornerRadius": original_clip_state.get("cornerRadius"),
                "transform": original_clip_state.get("transform", {}).copy(),
                "effects": original_clip_state.get("effects", {}).copy(),
            }

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_clip_shape(project, clip_id, internal_request)
        except DougaError as exc:
            logger.warning("v1.update_clip_shape failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        if result is None:
            return envelope_error(
                context,
                code="INTERNAL_ERROR",
                message="Failed to update clip shape",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and new state from result
        full_clip_id = result.id
        duration_after = project.duration_ms or 0

        # Get new state
        new_clip_state, _ = _find_clip_state(project, full_clip_id)
        new_shape_props = {}
        if new_clip_state:
            new_shape_props = {
                "filled": new_clip_state.get("filled"),
                "fillColor": new_clip_state.get("fillColor"),
                "strokeColor": new_clip_state.get("strokeColor"),
                "strokeWidth": new_clip_state.get("strokeWidth"),
                "cornerRadius": new_clip_state.get("cornerRadius"),
                "transform": new_clip_state.get("transform", {}),
                "effects": new_clip_state.get("effects", {}),
            }

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="clip",
                entity_id=full_clip_id,
                change_type="modified",
                before=original_shape_props,
                after=new_shape_props,
            )
        ]

        # Record operation
        operation = await operation_service.record_operation(
            project=project,
            operation_type="update_clip_shape",
            source="api_v1",
            success=True,
            affected_clips=[full_clip_id],
            diff=None,
            request_summary=RequestSummary(
                endpoint=f"/clips/{full_clip_id}/shape",
                method="PATCH",
                target_ids=[full_clip_id],
                key_params=_serialize_for_json(internal_request.model_dump(exclude_none=True)),
            ),
            result_summary=ResultSummary(
                success=True,
                modified_ids=[full_clip_id],
            ),
            rollback_data=None,
            rollback_available=False,
            idempotency_key=headers.get("idempotency_key"),
        )

        # Compute diff
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="update_clip_shape",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "update_clip_shape", "clip_id": clip_id},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Rollback not yet implemented for shape updates; re-apply previous values manually" if not operation.rollback_available else "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo",
        }
        if request.auto_wrapped:
            response_data["auto_wrapped"] = True
        if not operation.rollback_available:
            response_data.setdefault("hints", []).append(
                "To undo: re-apply previous values via PATCH /clips/{clip_id}/shape"
            )
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.update_clip_shape ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.update_clip_shape failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# Keyframe Endpoints
# =============================================================================


@router.post(
    "/projects/{project_id}/clips/{clip_id}/keyframes",
    response_model=EnvelopeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a keyframe to a clip",
    description=(
        "Add an animation keyframe to a clip. Keyframes define transform control points "
        "for position, scale, rotation, and opacity interpolation over time. "
        "Time is relative to clip start (0 = beginning of clip). "
        "If a keyframe already exists within 100ms, it will be updated."
    ),
)
async def add_keyframe(
    project_id: UUID,
    clip_id: str,
    body: AddKeyframeV1Request,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Add a keyframe to a clip.

    Supports validate_only mode for dry-run validation.
    Supports partial clip ID matching.
    """
    context = create_request_context()
    logger.info("v1.add_keyframe project=%s clip=%s time_ms=%s", project_id, clip_id, body.keyframe.time_ms)

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        internal_request = body.to_internal_request()

        if body.options.validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_add_keyframe(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.add_keyframe failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0
        original_clip_state, full_clip_id_before = _find_clip_state(project, clip_id)
        original_keyframes = None
        if original_clip_state:
            original_keyframes = original_clip_state.get("keyframes")

        try:
            keyframe_data = await service.add_keyframe(project, clip_id, internal_request)
        except DougaError as exc:
            logger.warning("v1.add_keyframe failed project=%s clip=%s code=%s: %s", project_id, clip_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        # Calculate duration after
        duration_after = project.duration_ms or 0
        keyframe_id = keyframe_data["id"]

        # Get full clip ID
        new_clip_state, full_clip_id = _find_clip_state(project, clip_id)
        actual_clip_id = full_clip_id or clip_id

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="keyframe",
                entity_id=keyframe_id,
                change_type="created",
                before=None,
                after=_serialize_for_json(keyframe_data),
            )
        ]

        # Record operation first to get operation_id
        operation = await operation_service.record_operation(
            project=project,
            operation_type="add_keyframe",
            source="api_v1",
            success=True,
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint=f"/clips/{actual_clip_id}/keyframes",
                method="POST",
                target_ids=[actual_clip_id],
                key_params={
                    "time_ms": internal_request.time_ms,
                    "clip_id": actual_clip_id,
                },
            ),
            result_summary=ResultSummary(
                success=True,
                created_ids=[keyframe_id],
            ),
            rollback_data={
                "clip_id": actual_clip_id,
                "keyframe_id": keyframe_id,
                "keyframe_data": _serialize_for_json(keyframe_data),
                "original_keyframes": _serialize_for_json(original_keyframes),
            },
            rollback_available=True,
            idempotency_key=header_result.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="add_keyframe",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={
                "source": "ai_v1",
                "operation": "add_keyframe",
                "clip_id": actual_clip_id,
                "keyframe_id": keyframe_id,
            },
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "keyframe": _serialize_for_json(keyframe_data),
            "clip_id": actual_clip_id,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.add_keyframe ok project=%s clip=%s keyframe=%s", project_id, actual_clip_id, keyframe_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.add_keyframe failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.delete(
    "/projects/{project_id}/clips/{clip_id}/keyframes/{keyframe_id}",
    response_model=EnvelopeResponse,
    summary="Delete a keyframe from a clip",
    description=(
        "Delete an animation keyframe from a clip. "
        "Supports partial ID matching for both clip and keyframe IDs."
    ),
)
async def delete_keyframe(
    project_id: UUID,
    clip_id: str,
    keyframe_id: str,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    body: DeleteKeyframeV1Request | None = None,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Delete a keyframe from a clip.

    Note: Request body is optional. If provided, supports validate_only mode.
    Both clip ID and keyframe ID support partial prefix matching.
    """
    context = create_request_context()
    logger.info("v1.delete_keyframe project=%s clip=%s keyframe=%s", project_id, clip_id, keyframe_id)

    # Determine validate_only from request body if present
    validate_only = body.options.validate_only if body else False

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            http_request, context, validate_only=validate_only
        )

        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data
        current_etag = compute_project_etag(project)

        # Check If-Match for concurrency control
        if header_result["if_match"] and header_result["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        if validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_delete_keyframe(
                    project, clip_id, keyframe_id
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.delete_keyframe failed project=%s clip=%s keyframe=%s code=%s: %s", project_id, clip_id, keyframe_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0

        try:
            keyframe_data = await service.delete_keyframe(project, clip_id, keyframe_id)
        except DougaError as exc:
            logger.warning("v1.delete_keyframe failed project=%s clip=%s keyframe=%s code=%s: %s", project_id, clip_id, keyframe_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        # Calculate duration after
        duration_after = project.duration_ms or 0
        actual_keyframe_id = keyframe_data.get("id", keyframe_id)

        # Get full clip ID
        clip_state, full_clip_id = _find_clip_state(project, clip_id)
        actual_clip_id = full_clip_id or clip_id

        # Build diff changes
        changes = [
            ChangeDetail(
                entity_type="keyframe",
                entity_id=actual_keyframe_id,
                change_type="deleted",
                before=_serialize_for_json(keyframe_data),
                after=None,
            )
        ]

        # Record operation first to get operation_id
        operation = await operation_service.record_operation(
            project=project,
            operation_type="delete_keyframe",
            source="api_v1",
            success=True,
            diff=None,  # Will update after we have operation_id
            request_summary=RequestSummary(
                endpoint=f"/clips/{actual_clip_id}/keyframes/{actual_keyframe_id}",
                method="DELETE",
                target_ids=[actual_clip_id, actual_keyframe_id],
                key_params={},
            ),
            result_summary=ResultSummary(
                success=True,
                deleted_ids=[actual_keyframe_id],
            ),
            rollback_data={
                "clip_id": actual_clip_id,
                "keyframe_id": actual_keyframe_id,
                "keyframe_data": _serialize_for_json(keyframe_data),
            },
            rollback_available=True,
            idempotency_key=header_result.get("idempotency_key"),
        )

        # Now compute diff with actual operation_id
        diff = operation_service.compute_diff(
            operation_id=operation.id,
            operation_type="delete_keyframe",
            changes=changes,
            duration_before_ms=duration_before,
            duration_after_ms=duration_after,
        )

        # Update operation record with diff
        await operation_service.update_operation_diff(operation, diff)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={
                "source": "ai_v1",
                "operation": "delete_keyframe",
                "clip_id": actual_clip_id,
                "keyframe_id": actual_keyframe_id,
            },
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        include_diff = body.options.include_diff if body else False
        response_data: dict = {
            "keyframe": _serialize_for_json(keyframe_data),
            "clip_id": actual_clip_id,
            "deleted": True,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback" if operation.rollback_available else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo" if operation.rollback_available else "Rollback data not available for this operation",
        }
        if include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.delete_keyframe ok project=%s clip=%s keyframe=%s", project_id, actual_clip_id, actual_keyframe_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.delete_keyframe failed project=%s clip=%s keyframe=%s: %s", project_id, clip_id, keyframe_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# Split Clip
# =============================================================================


class SplitClipV1Request(BaseModel):
    """Request to split a clip at a specific timeline position."""

    options: OperationOptions = Field(default_factory=OperationOptions)
    split_at_ms: int = Field(gt=0, description="Absolute timeline position to split at (ms)")


@router.post(
    "/projects/{project_id}/clips/{clip_id}/split",
    response_model=EnvelopeResponse,
)
async def split_clip(
    project_id: UUID,
    clip_id: str,
    request: SplitClipV1Request,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Split a clip at a specific timeline position.

    Splits the clip into two halves. If the clip has a group_id,
    all linked clips are also split at the same position.
    """
    context = create_request_context()
    logger.info("v1.split_clip project=%s clip=%s at=%d", project_id, clip_id, request.split_at_ms)

    validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data

        if request.options.validate_only:
            return envelope_success(context, {"valid": True, "message": "Split operation would succeed"})

        service = AIService(db)

        try:
            flag_modified(project, "timeline_data")
            result = await service.split_clip(project, clip_id, request.split_at_ms)
        except DougaError as exc:
            logger.warning("v1.split_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.message)
            return envelope_error_from_exception(context, exc)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "split_clip", "clip_id": clip_id},
        )

        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        response_data = _serialize_for_json({
            "left_clip": result["left_clip"],
            "right_clip": result["right_clip"],
            "left_group_id": result["left_group_id"],
            "right_group_id": result["right_group_id"],
            "linked_splits": result["linked_splits"],
        })

        logger.info("v1.split_clip ok project=%s clip=%s", project_id, clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.split_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# Unlink Clip
# =============================================================================


class UnlinkClipV1Request(BaseModel):
    """Request to unlink a clip from its group."""

    options: OperationOptions = Field(default_factory=OperationOptions)


@router.post(
    "/projects/{project_id}/clips/{clip_id}/unlink",
    response_model=EnvelopeResponse,
)
async def unlink_clip(
    project_id: UUID,
    clip_id: str,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
    http_request: Request,
    request: UnlinkClipV1Request | None = None,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Unlink a clip from its group, making it independent."""
    context = create_request_context()
    logger.info("v1.unlink_clip project=%s clip=%s", project_id, clip_id)

    validate_only = request.options.validate_only if request else False

    validate_headers(
        http_request,
        context,
        validate_only=validate_only,
    )

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        _orig_tl = project.timeline_data
        if _seq:
            project.timeline_data = _seq.timeline_data

        if validate_only:
            return envelope_success(context, {"valid": True, "message": "Unlink operation would succeed"})

        service = AIService(db)

        try:
            flag_modified(project, "timeline_data")
            result = await service.unlink_clip(project, clip_id)
        except DougaError as exc:
            logger.warning("v1.unlink_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.message)
            return envelope_error_from_exception(context, exc)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "unlink_clip", "clip_id": clip_id},
        )

        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            _sync_sequence_duration(_seq, _seq.timeline_data)
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        response_data: dict = {
            "clip_id": result["clip_id"],
            "unlinked": True,
            "previous_group_id": result["previous_group_id"],
        }

        logger.info("v1.unlink_clip ok project=%s clip=%s", project_id, clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.unlink_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =========================================================================
# Preview Diff
# =========================================================================


@router.post(
    "/projects/{project_id}/preview-diff",
    response_model=EnvelopeResponse,
    summary="Preview changes before applying",
    description="Simulate an operation and return what would change without modifying the timeline.",
)
async def preview_diff(
    project_id: UUID,
    body: PreviewDiffRequest,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    context = create_request_context()
    logger.info(
        "v1.preview_diff project=%s op=%s",
        project_id,
        body.operation_type,
    )

    try:
        project, _seq = await _resolve_edit_session(
            project_id, current_user, db, x_edit_session
        )
        if _seq:
            project.timeline_data = _seq.timeline_data

        service = AIService(db)
        result = await service.preview_diff(project, body)
        return envelope_success(context, result)
    except HTTPException as exc:
        logger.warning(
            "v1.preview_diff failed project=%s: %s",
            project_id,
            exc.detail,
        )
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# Catch-all: envelope-formatted 404/405 for unknown V1 endpoints
# =============================================================================


def _find_allowed_methods(request_path: str) -> set[str]:
    """Check if a path matches any registered route and return allowed methods.

    Iterates over router.routes to find routes whose path regex matches
    the given path. Returns the union of HTTP methods for all matching routes.
    Excludes the catch-all route itself.
    """
    from starlette.routing import Route

    allowed: set[str] = set()
    # Normalize: ensure path starts with /
    if not request_path.startswith("/"):
        request_path = f"/{request_path}"
    for route in router.routes:
        if not isinstance(route, Route):
            continue
        # Skip the catch-all route itself
        if getattr(route, "path", "") == "/{path:path}":
            continue
        if route.path_regex.match(request_path):
            allowed |= route.methods or set()
    return allowed


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def v1_catch_all(path: str, request: Request) -> JSONResponse:
    """Return 405 if the path exists but method is wrong, else 404."""
    context = create_request_context()
    allowed_methods = _find_allowed_methods(path)
    if allowed_methods:
        allow_header = ", ".join(sorted(allowed_methods))
        return JSONResponse(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            content=jsonable_encoder(
                EnvelopeResponse(
                    request_id=context.request_id,
                    error=ErrorInfo(
                        code="METHOD_NOT_ALLOWED",
                        message=(
                            f"Method {request.method} is not allowed for '/{path}'. "
                            f"Allowed methods: {allow_header}. "
                            "Use GET /capabilities for available endpoints."
                        ),
                        retryable=False,
                        suggested_fix=f"Use one of the allowed methods: {allow_header}",
                    ),
                    meta=build_meta(context),
                ).model_dump(exclude_none=True)
            ),
            headers={"Allow": allow_header},
        )
    return envelope_error(
        context,
        code="NOT_FOUND",
        message=f"V1 endpoint '/{path}' does not exist. Use GET /capabilities for available endpoints.",
        status_code=status.HTTP_404_NOT_FOUND,
    )
