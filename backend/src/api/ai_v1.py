"""AI v1 API Router.

Thin wrapper around existing AI service with envelope responses.
Implements AI-Friendly API spec with validate_only support.
"""

from datetime import datetime
import copy
import hashlib
import os
import shutil
import tempfile
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response, status


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

from src.api.deps import CurrentUser, DbSession
from src.exceptions import ChromaKeyAutoFailedError, DougaError, InvalidTimeRangeError
from src.middleware.request_context import (
    RequestContext,
    build_meta,
    create_request_context,
    validate_headers,
)
from src.models.asset import Asset
from src.models.project import Project
from src.schemas.ai import (
    AddAudioClipRequest,
    AddAudioTrackRequest,
    AddClipRequest,
    AddLayerRequest,
    AddMarkerRequest,
    AudioTrackSummary,
    BatchClipOperation,
    BatchOperationResult,
    L1ProjectOverview,
    L2AssetCatalog,
    L2TimelineAtTime,
    L2TimelineStructure,
    L3AudioClipDetails,
    L3ClipDetails,
    LayerSummary,
    MoveAudioClipRequest,
    MoveClipRequest,
    ReorderLayersRequest,
    SemanticOperation,
    SemanticOperationResult,
    UpdateClipCropRequest,
    UpdateClipEffectsRequest,
    ChromaKeyApplyRequest,
    ChromaKeyPreviewRequest,
    UpdateClipTextStyleRequest,
    UpdateClipTransformRequest,
    UpdateLayerRequest,
    UpdateMarkerRequest,
)
from src.schemas.asset import AssetResponse
from src.services.chroma_key_service import ChromaKeyService
from src.services.storage_service import get_storage_service
from src.utils.media_info import get_media_info
from src.schemas.clip_adapter import UnifiedClipInput, UnifiedMoveClipInput, UnifiedTransformInput
from src.schemas.envelope import EnvelopeResponse, ErrorInfo, ResponseMeta
from src.schemas.operation import (
    ChangeDetail,
    HistoryQuery,
    HistoryResponse,
    OperationRecord,
    RequestSummary,
    ResultSummary,
    RollbackRequest,
    RollbackResponse,
    TimelineDiff,
)
from src.schemas.options import OperationOptions
from src.services.ai_service import AIService
from src.services.operation_service import OperationService
from src.services.event_manager import event_manager
from src.services.validation_service import ValidationService
from src.utils.interpolation import EASING_FUNCTIONS

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
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.user_id == current_user.id,
        )
    )
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    return project


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


@router.get("/capabilities", response_model=EnvelopeResponse)
async def get_capabilities(
    current_user: CurrentUser,
) -> EnvelopeResponse:
    context = create_request_context()

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
            "GET /projects/{project_id}/assets",
            # Priority 5: Advanced read endpoints
            "GET /projects/{project_id}/clips/{clip_id}",  # Single clip details
            "GET /projects/{project_id}/at-time/{time_ms}",  # Timeline at specific time
            # History and operation endpoints
            "GET /projects/{project_id}/history",  # Operation history
            "GET /projects/{project_id}/operations/{operation_id}",  # Operation details
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
            "delete_audio_clip",  # DELETE /projects/{id}/audio-clips/{clip_id}
            "add_audio_track",  # POST /projects/{id}/audio-tracks
            # Priority 4: Markers
            "add_marker",  # POST /projects/{id}/markers
            "update_marker",  # PATCH /projects/{id}/markers/{marker_id}
            "delete_marker",  # DELETE /projects/{id}/markers/{marker_id}
            # Priority 5: Advanced operations
            "batch",  # POST /projects/{id}/batch
            "semantic",  # POST /projects/{id}/semantic
            # History and rollback
            "rollback",  # POST /projects/{id}/operations/{op_id}/rollback
        ],
        "planned_operations": [
            # All write operations are now implemented in v1
        ],
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
                "effects",
                "transition_in",
                "transition_out",
            ],
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
        "audio_track_types": ["narration", "bgm", "se", "video"],
        "effects": ["opacity", "blend_mode", "chroma_key"],
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
    }

    return envelope_success(context, capabilities)


@router.get("/version", response_model=EnvelopeResponse)
async def get_version(
    current_user: CurrentUser,
) -> EnvelopeResponse:
    context = create_request_context()
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
) -> EnvelopeResponse | JSONResponse:
    context = create_request_context()

    try:
        project = await get_user_project(project_id, current_user, db)
        response.headers["ETag"] = compute_project_etag(project)
        service = AIService(db)
        data: L1ProjectOverview = await service.get_project_overview(project)
        return envelope_success(context, data)
    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.get("/projects/{project_id}/structure", response_model=EnvelopeResponse)
async def get_timeline_structure(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
) -> EnvelopeResponse | JSONResponse:
    context = create_request_context()

    try:
        project = await get_user_project(project_id, current_user, db)
        response.headers["ETag"] = compute_project_etag(project)
        service = AIService(db)
        data: L2TimelineStructure = await service.get_timeline_structure(project)
        return envelope_success(context, data)
    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
            message=str(exc.detail),
            status_code=exc.status_code,
        )


@router.get("/projects/{project_id}/assets", response_model=EnvelopeResponse)
async def get_asset_catalog(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    response: Response,
) -> EnvelopeResponse | JSONResponse:
    context = create_request_context()

    try:
        project = await get_user_project(project_id, current_user, db)
        response.headers["ETag"] = compute_project_etag(project)
        service = AIService(db)
        data: L2AssetCatalog = await service.get_asset_catalog(project)
        return envelope_success(context, data)
    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    context = create_request_context()

    # Validate headers (Idempotency-Key required unless validate_only=true)
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project = await get_user_project(project_id, current_user, db)
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
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0

        try:
            flag_modified(project, "timeline_data")
            result = await service.add_clip(project, internal_clip)
        except DougaError as exc:
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

        return envelope_success(context, response_data)
    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Move a clip to a new timeline position or layer."""
    context = create_request_context()

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project = await get_user_project(project_id, current_user, db)
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

        return envelope_success(context, response_data)
    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Update clip transform properties (position, scale, rotation)."""
    context = create_request_context()

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project = await get_user_project(project_id, current_user, db)
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

        return envelope_success(context, response_data)
    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project = await get_user_project(project_id, current_user, db)
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

        return envelope_success(context, response_data)
    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Generate 5-frame chroma key preview for a clip."""
    context = create_request_context()

    try:
        project = await get_user_project(project_id, current_user, db)
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
            )
            return envelope_success(
                context,
                {
                    "resolved_key_color": resolved_color,
                    "frames": frames,
                },
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Generate a processed chroma key asset for a clip."""
    context = create_request_context()

    # Validate headers (mutation)
    validate_headers(http_request, context, validate_only=False)

    try:
        project = await get_user_project(project_id, current_user, db)
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
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Update clip crop (edge trimming).

    Supports:
    - top: 0.0-0.5 fraction of height to remove from top
    - right: 0.0-0.5 fraction of width to remove from right
    - bottom: 0.0-0.5 fraction of height to remove from bottom
    - left: 0.0-0.5 fraction of width to remove from left
    """
    context = create_request_context()

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project = await get_user_project(project_id, current_user, db)
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

        return envelope_success(context, response_data)
    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project = await get_user_project(project_id, current_user, db)
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

        return envelope_success(context, response_data)
    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Delete a clip from the timeline.

    Note: Request body is optional. If provided, supports validate_only mode.
    """
    context = create_request_context()

    # Determine validate_only from request body if present
    validate_only = request.options.validate_only if request else False

    # Validate headers
    headers = validate_headers(
        http_request,
        context,
        validate_only=validate_only,
    )

    try:
        project = await get_user_project(project_id, current_user, db)
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
            deleted_clip_id = await service.delete_clip(project, clip_id)
        except DougaError as exc:
            return envelope_error_from_exception(context, exc)

        # Use full clip ID from delete result or from state lookup
        actual_deleted_id = deleted_clip_id or full_clip_id
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
        if include_diff:
            response_data["diff"] = diff.model_dump()

        return envelope_success(context, response_data)
    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Add a new layer to the project.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project = await get_user_project(project_id, current_user, db)
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
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
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

        return envelope_success(context, response_data)

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Update layer properties.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project = await get_user_project(project_id, current_user, db)
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
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
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

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(context, {"layer": layer_summary.model_dump()})

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Reorder layers.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project = await get_user_project(project_id, current_user, db)
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
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        try:
            layer_summaries = await service.reorder_layers(
                project,
                layer_ids=body.order.layer_ids,
            )
        except DougaError as exc:
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
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

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(
            context,
            {"layers": [layer.model_dump() for layer in layer_summaries]},
        )

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Add a new audio clip to the project.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project = await get_user_project(project_id, current_user, db)
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
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0

        try:
            audio_clip = await service.add_audio_clip(project, body.clip)
        except DougaError as exc:
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
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

        return envelope_success(context, response_data)

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Move an audio clip to a new position or track.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project = await get_user_project(project_id, current_user, db)
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
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
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

        return envelope_success(context, response_data)

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Delete an audio clip.

    Note: Request body is optional. If provided, supports validate_only mode.
    """
    context = create_request_context()

    # Determine validate_only from request body if present
    validate_only = body.options.validate_only if body else False

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            http_request, context, validate_only=validate_only
        )

        project = await get_user_project(project_id, current_user, db)
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

        return envelope_success(context, response_data)

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Add a new audio track to the project.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project = await get_user_project(project_id, current_user, db)
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
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
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

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(context, {"audio_track": track_summary.model_dump()})

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Add a marker to the timeline.

    Supports validate_only mode for dry-run validation.
    """
    context = create_request_context()

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project = await get_user_project(project_id, current_user, db)
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
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (markers don't affect duration)
        duration_before = project.duration_ms or 0

        try:
            marker_data = await service.add_marker(project, body.marker)
        except DougaError as exc:
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

        return envelope_success(context, response_data)

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Update an existing marker.

    Supports validate_only mode for dry-run validation.
    Marker ID can be a partial prefix match.
    """
    context = create_request_context()

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project = await get_user_project(project_id, current_user, db)
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

        return envelope_success(context, response_data)

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Delete a marker from the timeline.

    Note: Request body is optional. If provided, supports validate_only mode.
    Marker ID can be a partial prefix match.
    """
    context = create_request_context()

    # Determine validate_only from request body if present
    validate_only = body.options.validate_only if body else False

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            http_request, context, validate_only=validate_only
        )

        project = await get_user_project(project_id, current_user, db)
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
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (markers don't affect duration)
        duration_before = project.duration_ms or 0

        try:
            marker_data = await service.delete_marker(project, marker_id)
        except DougaError as exc:
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

        return envelope_success(context, response_data)

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Get detailed information about a specific clip.

    Returns L3 clip details including timing, transform, effects,
    and neighboring clip context.
    """
    context = create_request_context()

    try:
        project = await get_user_project(project_id, current_user, db)
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
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Get timeline state at a specific time.

    Returns all active clips at the given timestamp with progress information.
    """
    context = create_request_context()

    try:
        project = await get_user_project(project_id, current_user, db)
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
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Execute multiple clip operations in a batch.

    Supports validate_only mode for dry-run validation.
    Operations are executed in order. If one fails, others may still succeed.
    """
    context = create_request_context()

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project = await get_user_project(project_id, current_user, db)
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
                return envelope_error_from_exception(context, exc)

        # Execute the actual batch operations
        service = AIService(db)
        try:
            result: BatchOperationResult = await service.execute_batch_operations(
                project, body.operations
            )
        except DougaError as exc:
            return envelope_error_from_exception(context, exc)

        # Only flag_modified after successful operation
        if result.successful_operations > 0:
            flag_modified(project, "timeline_data")

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "batch"},
        )

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(context, result.model_dump())

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(
            request, context, validate_only=body.options.validate_only
        )

        project = await get_user_project(project_id, current_user, db)
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
                return envelope_error_from_exception(context, exc)

        # Execute the actual semantic operation
        service = AIService(db)
        try:
            result: SemanticOperationResult = await service.execute_semantic_operation(
                project, body.operation
            )
        except DougaError as exc:
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

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(context, result.model_dump())

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Get operation history for a project.

    Returns a paginated list of operations with filtering options.

    Args:
        since: Return operations created after this timestamp (ISO 8601)
        until: Return operations created before this timestamp (ISO 8601)
    """
    context = create_request_context()

    try:
        project = await get_user_project(project_id, current_user, db)
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
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Get details of a specific operation.

    Returns full operation record including diff and rollback information.
    """
    context = create_request_context()

    try:
        project = await get_user_project(project_id, current_user, db)
        response.headers["ETag"] = compute_project_etag(project)

        operation_service = OperationService(db)
        try:
            record: OperationRecord = await operation_service.get_operation_record(
                project.id, operation_id
            )
            return envelope_success(context, record.model_dump())
        except DougaError as exc:
            return envelope_error_from_exception(context, exc)

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
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
) -> EnvelopeResponse | JSONResponse:
    """Rollback a previous operation.

    This creates a new operation that reverses the effects of the original.
    Not all operations can be rolled back - check rollback_available flag.
    """
    context = create_request_context()

    # Validate headers (Idempotency-Key required for mutations)
    headers = validate_headers(http_request, context, validate_only=False)

    try:
        project = await get_user_project(project_id, current_user, db)
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

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(context, rollback_response.model_dump())

    except HTTPException as exc:
        return envelope_error(
            context,
            code="PROJECT_NOT_FOUND",
            message=str(exc.detail),
            status_code=exc.status_code,
        )
