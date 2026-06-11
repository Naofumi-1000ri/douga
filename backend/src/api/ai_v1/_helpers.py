"""Shared helpers, request models, and utility functions for ai_v1 package.

This module is imported by all sub-modules in api/ai_v1/.
"""

import logging
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.api.access import get_accessible_project
from src.api.deps import CurrentUser, DbSession, get_edit_context
from src.exceptions import DougaError
from src.middleware.request_context import (
    RequestContext,
    build_meta,
    save_idempotency_db,
)
from src.models.asset import Asset
from src.models.project import Project
from src.models.sequence import Sequence
from src.schemas.ai import (
    AddAudioClipRequest,
    AddAudioTrackRequest,
    AddClipRequest,
    AddKeyframeRequest,
    AddLayerRequest,
    AddMarkerRequest,
    BatchClipOperation,
    MoveAudioClipRequest,
    MoveClipRequest,
    ReorderLayersRequest,
    SemanticOperation,
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
from src.schemas.envelope import EnvelopeResponse, ErrorInfo, ResponseMeta
from src.schemas.options import OperationOptions
from src.services.ai_service import _sanitize_timeline_ms
from src.services.storage_service import get_storage_service


def _serialize_for_json(obj: Any) -> Any:
    """Recursively convert UUIDs to strings for JSON serialization."""
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize_for_json(item) for item in obj]
    return obj


logger = logging.getLogger(__name__)

router = APIRouter()

# Valid fields for the transform endpoint (used for unknown field detection)
_VALID_TRANSFORM_FIELDS: set[str] = {
    "x",
    "y",
    "scale",
    "rotation",
    "opacity",
    "width",
    "height",
    "anchor",
    "transform",
}


# =============================================================================
# Sequence duration sync helper
# =============================================================================


def _sync_sequence_duration(seq: Any, timeline_data: dict[str, Any]) -> None:
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


def _auto_wrap_flat_body(data: dict[str, Any], wrapper_key: str) -> dict[str, Any]:
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

    wrapped: dict[str, Any] = {
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


async def get_user_project(project_id: UUID, current_user: CurrentUser, db: DbSession) -> Project:
    """Get project with access verification (ownership or membership)."""
    return await get_accessible_project(project_id, current_user.id, db)


async def _resolve_edit_session(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: str | None = None,
    require_role: str | None = None,
    *,
    read_only: bool = False,
) -> tuple["Project", "Sequence | None"]:
    """Resolve project and optional sequence from X-Edit-Session token.

    Always resolves to default sequence when no token is provided.

    Args:
        require_role: Minimum project role required. All V1 mutation endpoints
            MUST pass require_role="editor" so that viewer members (and viewer
            API keys — X-API-Key resolves to a User whose project role applies
            identically) cannot write. Read/preview endpoints pass None.
        read_only: When True, detach the project from the ORM session via
            ``db.expunge(project)`` before returning. This prevents SQLAlchemy
            autoflush from issuing an implicit ``UPDATE projects`` when
            subsequent attribute assignments (e.g. ``project.timeline_data =
            _seq.timeline_data``) mark the instance dirty.  Scalar attributes
            (id, name, timeline_data, duration_ms, updated_at, …) remain
            accessible on detached instances.  Read/preview endpoints MUST pass
            ``read_only=True``; mutation endpoints use the default False.
    """
    ctx = await get_edit_context(
        project_id, current_user, db, x_edit_session, require_role=require_role
    )
    if read_only:
        db.expunge(ctx.project)
    return ctx.project, ctx.sequence


async def _resolve_edit_session_for_write(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: str | None = None,
) -> tuple["Project", "Sequence | None"]:
    """Resolve edit session for V1 mutation endpoints — enforces editor role.

    Identical to _resolve_edit_session but requires the caller to have at
    least 'editor' role on the project (issue #261). viewer members get 403.
    This applies to both Firebase-token and X-API-Key authentication, because
    the API key resolves to its owning User whose project membership role is
    evaluated the same way.
    """
    return await _resolve_edit_session(
        project_id, current_user, db, x_edit_session, require_role="editor"
    )


@contextmanager
def _use_sequence_timeline(project: "Project", sequence: "Sequence | None") -> "Any":
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


def _find_clip_state(project: Project, clip_id: str) -> tuple[dict[str, Any] | None, str | None]:
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


def _find_audio_clip_state(
    project: Project, clip_id: str
) -> tuple[dict[str, Any] | None, str | None]:
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


def _find_marker_state(
    project: Project, marker_id: str
) -> tuple[dict[str, Any] | None, str | None]:
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
) -> tuple[dict[str, Any] | None, str | None]:
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
        logger.exception(
            "Failed to sign storage URL for asset %s (storage_key=%s)",
            asset.id,
            asset.storage_key,
        )
        # assets.py の _asset_to_response_with_signed_url と同じ fallback 方式に統一 (#254 item1)。
        # asset.storage_url は永続化方針変更後 storage_key 文字列が入るため、
        # そのまま返すと storage_key が URL としてクライアントに渡ってしまう。
        signed_url = storage.get_public_url(asset.storage_key)

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


def _normalize_text_style_for_diff(text_style: dict[str, Any] | None) -> dict[str, Any]:
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


async def idempotent_success(
    context: RequestContext,
    data: object,
    *,
    idempotency_key: str | None,
    operation_id: "UUID | None",
    db: "AsyncSession",
    http_status: int = 200,
) -> EnvelopeResponse | JSONResponse:
    """Build an envelope success response and persist it for idempotency replay.

    Drop-in replacement for ``return envelope_success(context, data)`` on write
    endpoints that record an operation.  After building the response, the payload
    is stored in the matching ``project_operations`` row so that retries with the
    same Idempotency-Key receive the exact same body from any Cloud Run instance.

    When ``operation_id`` is None (e.g. validate_only paths) the call is a no-op
    for persistence and behaves identically to ``envelope_success``.
    """
    envelope = envelope_success(context, data)

    if idempotency_key and operation_id:
        body_dict = jsonable_encoder(envelope.model_dump(exclude_none=True))
        await save_idempotency_db(
            key=idempotency_key,
            status_code=http_status,
            body=body_dict,
            operation_id=operation_id,
            db=db,
        )

    return envelope


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

    Canonical implementation (unified from main.py and ai_v1.py).
    Change history:
    - ai_v1.py had 404="PROJECT_NOT_FOUND", no 500 entry
    - main.py had 404="NOT_FOUND", 500="INTERNAL_ERROR"
    - Unified to main.py version: NOT_FOUND is more general (clips/layers/etc.
      can also return 404, not just projects), and 500 coverage prevents fallback
      to the generic "HTTP_ERROR" string.
    """
    if status_code == 400 and "Idempotency-Key" in detail:
        return "IDEMPOTENCY_MISSING"

    mapping = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONCURRENT_MODIFICATION",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
        500: "INTERNAL_ERROR",
    }
    return mapping.get(status_code, "HTTP_ERROR")


# Module-level constant so tests can verify the dict without an HTTP request.
# Keeps capabilities readable and avoids DB connections in unit tests.
OPERATION_DETAILS: dict[str, Any] = {
    "add_clip": {
        "description": "Add a clip to a layer. For video assets with linked audio, an audio clip is automatically placed on the narration track (set include_audio=false in options to skip).",
        "auto_behaviors": [
            "Video clips: linked audio auto-placed on narration track (if available)",
            "Smart positioning: clips get default position based on layer type",
            "Group linking: video and audio clips share group_id for synchronized editing",
        ],
        "IMPORTANT_duplicate_audio_warning": "When adding a VIDEO clip, its linked audio is AUTO-PLACED on the narration track (unless include_audio=false in batch options or clip options). Additionally, when a video asset is REGISTERED (POST /assets step 3), an audio asset is auto-extracted and linked. This means the video asset's 'linked_audio_id' field points to an audio asset that was auto-created. To avoid DUPLICATE audio: (1) use include_audio=false in batch options when adding the video clip, AND (2) add the narration audio clip separately with POST /audio-clips. Always check GET /timeline-overview after adding clips to verify no duplicates exist.",
    },
    "add_audio_clip": {
        "description": (
            "Add an audio clip to an existing audio track. "
            "The track must already exist — create it first with POST /audio-tracks if needed. "
            "Supports narration, BGM, SE, and video-linked audio tracks."
        ),
        "required_fields": {
            "track_id": "ID of the target audio track (from GET /timeline-overview audio_tracks[].id)",
            "asset_id": "UUID of the audio asset to place",
            "start_ms": "Timeline position in milliseconds (>= 0)",
            "duration_ms": "Clip duration in milliseconds (1..3600000)",
        },
        "optional_fields": {
            "in_point_ms": "Trim start within the source asset (default: 0)",
            "out_point_ms": "Trim end within the source asset (default: full asset length)",
            "volume": "Volume multiplier 0.0..2.0 (default: 1.0; 1.0 = original level)",
            "fade_in_ms": "Fade-in duration in milliseconds 0..10000 (default: 0)",
            "fade_out_ms": "Fade-out duration in milliseconds 0..10000 (default: 0)",
            "group_id": "Optional group ID to link with a video clip for synchronized editing",
        },
        "auto_behaviors": [
            "No automatic behaviors — audio clips are placed exactly where specified",
        ],
        "IMPORTANT_duplicate_audio_warning": (
            "When a VIDEO clip is added via add_clip, its linked audio is AUTO-PLACED on the "
            "narration track unless include_audio=false. Do NOT also manually add_audio_clip "
            "for the same asset — this creates duplicate audio. "
            "Use add_audio_clip only for: (a) BGM/SE tracks, (b) narration audio when "
            "include_audio=false was used for the corresponding video clip, or "
            "(c) audio-only assets with no linked video clip."
        ),
    },
    "add_audio_track": {
        "description": (
            "Create a new audio track in the timeline. "
            "Returns the new track's ID, which is then used as track_id in add_audio_clip."
        ),
        "required_fields": {
            "name": "Human-readable track name (e.g. 'BGM', 'SE layer 1')",
        },
        "optional_fields": {
            "type": "Track type: 'narration' | 'bgm' | 'se' | 'video' (default: 'bgm'). "
            "Use 'narration' for voice-over, 'bgm' for background music, 'se' for sound effects.",
            "volume": "Track-level volume multiplier 0.0..2.0 (default: 1.0). "
            "Multiplied with individual clip volumes.",
            "muted": "Whether the entire track is muted (default: false)",
            "ducking_enabled": "Enable auto-ducking of BGM under narration (default: false)",
            "insert_at": "Insert position index (0 = top of track list, None = append at bottom)",
        },
        "auto_behaviors": [
            "No automatic clip placement — the track is created empty",
            "Track ID is returned in the response; use it for subsequent add_audio_clip calls",
        ],
    },
}


def _find_allowed_methods(router_obj: "APIRouter", request_path: str) -> "set[str]":
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
    for route in router_obj.routes:
        if not isinstance(route, Route):
            continue
        # Skip the catch-all route itself
        if getattr(route, "path", "") == "/{path:path}":
            continue
        if route.path_regex.match(request_path):
            allowed |= route.methods or set()
    return allowed
