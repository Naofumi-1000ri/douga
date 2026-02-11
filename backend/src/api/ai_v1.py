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

from fastapi import APIRouter, Header, HTTPException, Request, Response, status


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
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from src.api.access import get_accessible_project
from src.api.deps import CurrentUser, DbSession, get_edit_context
from src.exceptions import ChromaKeyAutoFailedError, DougaError, InvalidTimeRangeError
from src.middleware.request_context import (
    RequestContext,
    build_meta,
    create_request_context,
    validate_headers,
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
    AvailableSchemas,
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
    ReorderLayersRequest,
    SchemaInfo,
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
from src.services.ai_service import AIService
from src.services.chroma_key_service import ChromaKeyService
from src.services.event_manager import event_manager
from src.services.operation_service import OperationService
from src.services.storage_service import get_storage_service
from src.services.validation_service import ValidationService
from src.utils.interpolation import EASING_FUNCTIONS
from src.utils.media_info import get_media_info

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateClipRequest(BaseModel):
    """Request to create a clip.

    Accepts both flat (transitional) and nested (spec) clip formats.

    Flat format:
        {"options": {...}, "clip": {"layer_id": "...", "x": 0, "y": 0, "scale": 1}}

    Nested format (spec-compliant):
        {"options": {...}, "clip": {"type": "video", "layer_id": "...", "transform": {...}}}
    """

    options: OperationOptions
    clip: UnifiedClipInput

    def to_internal_clip(self) -> AddClipRequest:
        """Convert unified clip input to internal AddClipRequest format."""
        flat_data = self.clip.to_flat_dict()
        return AddClipRequest.model_validate(flat_data)


class MoveClipV1Request(BaseModel):
    """Request to move a clip to a new timeline position or layer."""

    options: OperationOptions
    move: UnifiedMoveClipInput

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
    """

    options: OperationOptions
    transform: UnifiedTransformInput

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
    """

    options: OperationOptions
    effects: UpdateClipEffectsRequest

    def to_internal_request(self) -> UpdateClipEffectsRequest:
        """Return the internal request (already in correct format)."""
        return self.effects


class DeleteClipV1Request(BaseModel):
    """Request to delete a clip."""

    options: OperationOptions


class UpdateCropV1Request(BaseModel):
    """Request to update clip crop.

    Crop values are fractional (0.0-0.5), representing the percentage of each edge to remove.
    For example, top=0.1 removes 10% from the top edge.
    """

    options: OperationOptions
    crop: UpdateClipCropRequest

    def to_internal_request(self) -> UpdateClipCropRequest:
        """Return the internal request (already in correct format)."""
        return self.crop


class UpdateTextStyleV1Request(BaseModel):
    """Request to update text clip styling.

    Uses snake_case input; camelCase aliases are accepted for compatibility.
    Supports:
    - font_family: Font family name (e.g., "Noto Sans JP")
    - font_size: 8-500 pixels
    - font_weight: 100-900
    - color: Text color in hex (#RRGGBB)
    - text_align: "left", "center", or "right"
    - background_color: Background color in hex (#RRGGBB)
    - background_opacity: 0.0-1.0
    """

    options: OperationOptions
    text_style: UpdateClipTextStyleRequest

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

    options: OperationOptions
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
    """

    options: OperationOptions
    timing: UpdateClipTimingRequest

    def to_internal_request(self) -> UpdateClipTimingRequest:
        """Return the internal request (already in correct format)."""
        return self.timing


class UpdateClipTextV1Request(BaseModel):
    """Request to update text clip content.

    Supports:
    - text_content: New text content string
    """

    options: OperationOptions
    text: UpdateClipTextRequest

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
    """

    options: OperationOptions
    shape: UpdateClipShapeRequest

    def to_internal_request(self) -> UpdateClipShapeRequest:
        """Return the internal request (already in correct format)."""
        return self.shape


# =============================================================================
# Layer Request Models
# =============================================================================


class AddLayerV1Request(BaseModel):
    """Request to add a new layer."""

    options: OperationOptions
    layer: AddLayerRequest

    def to_internal_request(self) -> AddLayerRequest:
        """Return the internal request (already in correct format)."""
        return self.layer


class UpdateLayerV1Request(BaseModel):
    """Request to update layer properties."""

    options: OperationOptions
    layer: UpdateLayerRequest

    def to_internal_request(self) -> UpdateLayerRequest:
        """Return the internal request (already in correct format)."""
        return self.layer


class ReorderLayersV1Request(BaseModel):
    """Request to reorder layers."""

    options: OperationOptions
    order: ReorderLayersRequest

    def to_internal_request(self) -> ReorderLayersRequest:
        """Return the internal request (already in correct format)."""
        return self.order


# =============================================================================
# Audio Request Models
# =============================================================================


class AddAudioClipV1Request(BaseModel):
    """Request to add a new audio clip."""

    options: OperationOptions
    clip: AddAudioClipRequest

    def to_internal_request(self) -> AddAudioClipRequest:
        """Return the internal request (already in correct format)."""
        return self.clip


class MoveAudioClipV1Request(BaseModel):
    """Request to move an audio clip."""

    options: OperationOptions
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

    options: OperationOptions


class AddAudioTrackV1Request(BaseModel):
    """Request to add a new audio track."""

    options: OperationOptions
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
    """

    options: OperationOptions = Field(default_factory=OperationOptions)
    operation: SemanticOperation


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


def _http_error_code(status_code: int) -> str:
    """Map HTTP status code to V1 error code."""
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
    current_user: CurrentUser,
) -> EnvelopeResponse:
    context = create_request_context()
    logger.info("v1.get_capabilities")

    capabilities = {
        "api_version": "1.0",
        "schema_version": "1.0-unified",  # Accepts both flat and nested clip formats
        "supported_read_endpoints": [
            # All read endpoints are implemented and available
            "GET /capabilities",
            "GET /version",
            "GET /projects/{project_id}/overview",
            "GET /projects/{project_id}/summary",  # Alias for overview
            "GET /projects/{project_id}/structure",
            "GET /projects/{project_id}/timeline-overview",  # L2.5: Full overview
            "GET /projects/{project_id}/assets",
            # Priority 5: Advanced read endpoints
            "GET /projects/{project_id}/clips/{clip_id}",  # Single clip details
            "GET /projects/{project_id}/audio-clips/{clip_id}",  # Single audio clip details
            "GET /projects/{project_id}/at-time/{time_ms}",  # Timeline at specific time
            # Analysis endpoints
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
        ],
        "planned_operations": [
            # All write operations are now implemented in v1
        ],
        "operation_details": {
            "add_clip": {
                "description": "Add a clip to a layer. For video assets with linked audio, an audio clip is automatically placed on the narration track (set include_audio=false in options to skip).",
                "auto_behaviors": [
                    "Video clips: linked audio auto-placed on narration track (if available)",
                    "Smart positioning: clips get default position based on layer type",
                    "Group linking: video and audio clips share group_id for synchronized editing",
                ],
            },
        },
        "features": {
            "validate_only": True,
            "return_diff": True,  # Use options.include_diff=true to get diff in response
            "rollback": True,  # POST /operations/{id}/rollback
            "history": True,  # GET /history, GET /operations/{id}
        },
        "schema_notes": {
            "clip_format": "unified",  # Accepts both flat and nested formats
            "transform_formats": ["flat", "nested"],  # x/y/scale or transform.position/scale
            "flat_example": {"layer_id": "...", "x": 0, "y": 0, "scale": 1.0},
            "nested_example": {
                "type": "video",
                "layer_id": "...",
                "transform": {"position": {"x": 0, "y": 0}, "scale": {"x": 1, "y": 1}},
            },
            "supported_transform_fields": [
                "position.x",
                "position.y",
                "scale.x",
                "rotation (transform_clip only, not add_clip)",
            ],
            "chroma_key_preview_samples": [0.1, 0.3, 0.5, 0.7, 0.9],
            "unsupported_transform_fields": [
                "opacity",
                "anchor",
                "scale.y (non-uniform scale coerced to scale.x)",
            ],
            "unsupported_clip_fields": [
                "transition_in",
                "transition_out",
            ],
            "effects_note": "Effects (opacity, fade, chroma_key, blend_mode) cannot be set directly in add_clip. Use PATCH /clips/{clip_id}/effects after adding the clip.",
            "text_style_note": "Unknown text_style keys preserved as-is (passthrough)",
            "semantic_operations": [
                "snap_to_previous",
                "snap_to_next",
                "close_gap",
                "auto_duck_bgm",
                "rename_layer",
            ],
            "batch_operation_types": [
                "add",
                "move",
                "trim",
                "update_transform",
                "update_effects",
                "delete",
                "update_layer",
            ],
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
                "chroma_key": {"enabled": False, "color": "#00FF00", "similarity": 0.3, "smoothness": 0.1},
            },
            "transform": {
                "text_layer": {"x": 960, "y": 800, "scale_x": 1.0, "scale_y": 1.0, "rotation": 0},
                "content_layer": {"x": 960, "y": 540, "scale_x": 1.0, "scale_y": 1.0, "rotation": 0},
                "background_layer": {"x": 960, "y": 540, "scale_x": 1.0, "scale_y": 1.0, "rotation": 0},
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
            "1. GET /api/ai/v1/capabilities — discover all available operations",
            "2. GET /api/ai/v1/projects/{id}/assets — list available assets",
            "3. GET /api/ai/v1/projects/{id}/timeline-overview — full timeline with snapshot_base64",
            "4. POST /api/projects/{id}/preview/sample-event-points — key frame images",
            "5. Use add_clip, move_clip, batch, semantic etc. to edit",
            "6. POST /api/projects/{id}/preview/validate — check composition",
            "7. POST /api/projects/{id}/render — export final video",
        ],
    }

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


@router.get("/projects/{project_id}/overview", response_model=EnvelopeResponse)
@router.get("/projects/{project_id}/summary", response_model=EnvelopeResponse)
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
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
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
) -> EnvelopeResponse | JSONResponse:
    """L2.5: Full timeline overview with clips, gaps, and overlaps in one request."""
    context = create_request_context()
    logger.info("v1.get_timeline_overview project=%s", project_id)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)
        service = AIService(db)
        data: L25TimelineOverview = await service.get_timeline_overview(project)
        return envelope_success(context, data)
    except HTTPException as exc:
        logger.warning("v1.get_timeline_overview failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)
        service = AIService(db)
        data: L2AssetCatalog = await service.get_asset_catalog(project)
        return envelope_success(context, data)
    except HTTPException as exc:
        logger.warning("v1.get_asset_catalog failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
        }
        if linked_audio_clip_details:
            response_data["linked_audio_clip"] = linked_audio_clip_details
        elif include_audio and internal_clip.asset_id:
            response_data["linked_audio_clip"] = None
            context.warnings.append("Linked audio not yet available (extraction may still be in progress)")
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            "Use PATCH /clips/{clip_id}/effects to add fade transitions",
            "Use PATCH /clips/{clip_id}/transform to adjust position",
            "Use GET /timeline-overview to see the updated layout",
        ]

        logger.info("v1.add_clip ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.add_clip failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
        }
        if linked_clips_moved:
            response_data["linked_clips_moved"] = linked_clips_moved
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            "Use GET /timeline-overview to verify the new position",
            "Use POST /preview/validate to check for overlapping clips",
        ]

        logger.info("v1.move_clip ok project=%s clip=%s linked_moved=%s", project_id, full_clip_id, linked_clips_moved)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.move_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
        }
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
            code=_http_error_code(exc.status_code),
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
            rollback_data=None,  # Rollback not implemented for update_effects
            rollback_available=False,
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
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
        }
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
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
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
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
        }
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.update_clip_crop ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.update_clip_crop failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
            rollback_data=None,  # Rollback not implemented for update_text_style
            rollback_available=False,
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
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
        }
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
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
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
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "layer": layer_summary.model_dump(),
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
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
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
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
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "audio_clip": audio_clip.model_dump(),
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            "Use PATCH /audio-clips/{clip_id} to adjust volume and fades",
            "Use GET /timeline-overview to see the updated audio layout",
        ]

        logger.info("v1.add_audio_clip ok project=%s clip=%s", project_id, clip_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.add_audio_clip failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "audio_clip": audio_clip.model_dump(),
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.move_audio_clip ok project=%s clip=%s", project_id, result_clip_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.move_audio_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
        }
        if include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.delete_audio_clip ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.delete_audio_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
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
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "marker": marker_data,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.add_marker ok project=%s marker=%s", project_id, marker_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.add_marker failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "marker": marker_data,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.update_marker ok project=%s marker=%s", project_id, actual_marker_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.update_marker failed project=%s marker=%s: %s", project_id, marker_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
        }
        if include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.delete_marker ok project=%s marker=%s", project_id, actual_marker_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.delete_marker failed project=%s marker=%s: %s", project_id, marker_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
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
        try:
            result: BatchOperationResult = await service.execute_batch_operations(
                project, body.operations
            )
        except DougaError as exc:
            logger.warning("v1.execute_batch failed project=%s code=%s: %s", project_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        # Only flag_modified after successful operation
        if result.successful_operations > 0:
            flag_modified(project, "timeline_data")

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "batch"},
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        logger.info("v1.execute_batch ok project=%s success=%s fail=%s", project_id, result.successful_operations, result.failed_operations)
        return envelope_success(context, result.model_dump())

    except HTTPException as exc:
        logger.warning("v1.execute_batch failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
    logger.info("v1.execute_semantic project=%s op=%s", project_id, body.operation.operation)

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
                    project, body.operation
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning("v1.execute_semantic failed project=%s code=%s: %s", project_id, exc.code, exc.message)
                return envelope_error_from_exception(context, exc)

        # Execute the actual semantic operation
        service = AIService(db)
        try:
            result: SemanticOperationResult = await service.execute_semantic_operation(
                project, body.operation
            )
        except DougaError as exc:
            logger.warning("v1.execute_semantic failed project=%s code=%s: %s", project_id, exc.code, exc.message)
            return envelope_error_from_exception(context, exc)

        # If semantic operation failed, return structured error
        if not result.success:
            return envelope_error(
                context,
                code="SEMANTIC_OPERATION_FAILED",
                message=result.error_message or f"Semantic operation '{body.operation.operation}' failed",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Only flag_modified after successful operation with changes
        if result.changes_made:
            flag_modified(project, "timeline_data")

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={
                "source": "ai_v1",
                "operation": f"semantic_{body.operation.operation}",
            },
        )

        # Write back to sequence if applicable
        if _seq:
            _seq.timeline_data = project.timeline_data
            flag_modified(_seq, "timeline_data")
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        logger.info("v1.execute_semantic ok project=%s op=%s", project_id, body.operation.operation)
        return envelope_success(context, result.model_dump())

    except HTTPException as exc:
        logger.warning("v1.execute_semantic failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
        return envelope_success(context, history.model_dump())

    except HTTPException as exc:
        logger.warning("v1.get_history failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.get(
    "/schemas",
    response_model=EnvelopeResponse,
    summary="Get available schema definitions",
    description="Returns a list of all available AI API schemas with their descriptions and endpoints.",
)
async def get_schemas(
    current_user: CurrentUser,
) -> EnvelopeResponse:
    """Get available schema definitions.

    Returns information about all schema levels (L1, L2, L2.5, L3)
    and write/analysis schemas.
    """
    context = create_request_context()
    logger.info("v1.get_schemas")

    schemas = AvailableSchemas(
        schemas=[
            SchemaInfo(
                name="L1ProjectOverview",
                description="Lightweight project overview with summary statistics",
                level="L1",
                token_estimate="~300 tokens",
                endpoint="GET /projects/{project_id}/overview",
            ),
            SchemaInfo(
                name="L2TimelineStructure",
                description="Timeline layer/track structure without clip details",
                level="L2",
                token_estimate="~800 tokens",
                endpoint="GET /projects/{project_id}/structure",
            ),
            SchemaInfo(
                name="L2AssetCatalog",
                description="Available assets with usage counts",
                level="L2",
                token_estimate="~500 tokens",
                endpoint="GET /projects/{project_id}/assets",
            ),
            SchemaInfo(
                name="L2TimelineAtTime",
                description="Active clips at a specific timestamp",
                level="L2",
                token_estimate="~400 tokens",
                endpoint="GET /projects/{project_id}/at-time/{time_ms}",
            ),
            SchemaInfo(
                name="L25TimelineOverview",
                description="Full timeline overview with clip summaries, gaps, and overlaps",
                level="L2",
                token_estimate="~2000 tokens",
                endpoint="GET /projects/{project_id}/timeline-overview",
            ),
            SchemaInfo(
                name="L3ClipDetails",
                description="Full details for a single video clip with neighbors",
                level="L3",
                token_estimate="~400 tokens/clip",
                endpoint="GET /projects/{project_id}/clips/{clip_id}",
            ),
            SchemaInfo(
                name="L3AudioClipDetails",
                description="Full details for a single audio clip with neighbors",
                level="L3",
                token_estimate="~300 tokens/clip",
                endpoint="GET /projects/{project_id}/audio-clips/{clip_id}",
            ),
            SchemaInfo(
                name="AddClipRequest",
                description="Add a new video clip to a layer",
                level="write",
                token_estimate="~200 tokens",
                endpoint="POST /projects/{project_id}/clips",
            ),
            SchemaInfo(
                name="AddAudioClipRequest",
                description="Add a new audio clip to a track",
                level="write",
                token_estimate="~200 tokens",
                endpoint="POST /projects/{project_id}/audio-clips",
            ),
            SchemaInfo(
                name="SemanticOperation",
                description="High-level semantic operations (snap, close gap, auto duck, etc.)",
                level="write",
                token_estimate="~150 tokens",
                endpoint="POST /projects/{project_id}/semantic",
            ),
            SchemaInfo(
                name="BatchClipOperation",
                description="Batch multiple clip operations in a single request",
                level="write",
                token_estimate="~300 tokens",
                endpoint="POST /projects/{project_id}/batch",
            ),
            SchemaInfo(
                name="GapAnalysisResult",
                description="Find gaps in the timeline across layers and tracks",
                level="analysis",
                token_estimate="~500 tokens",
                endpoint="GET /projects/{project_id}/analysis/gaps",
            ),
            SchemaInfo(
                name="PacingAnalysisResult",
                description="Analyze clip density and pacing across timeline segments",
                level="analysis",
                token_estimate="~600 tokens",
                endpoint="GET /projects/{project_id}/analysis/pacing",
            ),
        ]
    )

    return envelope_success(context, schemas.model_dump())


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
            code=_http_error_code(exc.status_code),
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
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Analyze timeline pacing.

    Divides the timeline into segments and analyzes clip density,
    average clip duration, and suggests improvements.
    """
    context = create_request_context()
    logger.info("v1.analyze_pacing project=%s segment=%s", project_id, segment_duration_ms)

    try:
        project, _seq = await _resolve_edit_session(project_id, current_user, db, x_edit_session)
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)

        service = AIService(db)
        result: PacingAnalysisResult = await service.analyze_pacing(
            project, segment_duration_ms=segment_duration_ms
        )
        return envelope_success(context, result.model_dump())

    except HTTPException as exc:
        logger.warning("v1.analyze_pacing failed project=%s: %s", project_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
        }
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.update_audio_clip ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.update_audio_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
            rollback_data=None,
            rollback_available=False,
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
        }
        if linked_clips_updated:
            response_data["linked_clips_updated"] = linked_clips_updated
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.update_clip_timing ok project=%s clip=%s linked_updated=%s", project_id, full_clip_id, linked_clips_updated)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.update_clip_timing failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
        }
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.update_clip_text ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.update_clip_text failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
            project.timeline_data = _orig_tl

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        # Build response with operation info
        response_data: dict = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
        }
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.update_clip_shape ok project=%s clip=%s", project_id, full_clip_id)
        return envelope_success(context, response_data)
    except HTTPException as exc:
        logger.warning("v1.update_clip_shape failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.add_keyframe ok project=%s clip=%s keyframe=%s", project_id, actual_clip_id, keyframe_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.add_keyframe failed project=%s clip=%s: %s", project_id, clip_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
        }
        if include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.delete_keyframe ok project=%s clip=%s keyframe=%s", project_id, actual_clip_id, actual_keyframe_id)
        return envelope_success(context, response_data)

    except HTTPException as exc:
        logger.warning("v1.delete_keyframe failed project=%s clip=%s keyframe=%s: %s", project_id, clip_id, keyframe_id, exc.detail)
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
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
            code=_http_error_code(exc.status_code),
            message=str(exc.detail),
            status_code=exc.status_code,
        )
