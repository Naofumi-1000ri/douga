"""AI v1 API Router.

Thin wrapper around existing AI service with envelope responses.
Implements AI-Friendly API spec with validate_only support.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from src.api.deps import CurrentUser, DbSession
from src.exceptions import DougaError
from src.middleware.request_context import (
    RequestContext,
    build_meta,
    create_request_context,
    validate_headers,
)
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
    UpdateClipTransformRequest,
    UpdateLayerRequest,
    UpdateMarkerRequest,
)
from src.schemas.clip_adapter import UnifiedClipInput, UnifiedMoveClipInput, UnifiedTransformInput
from src.schemas.envelope import EnvelopeResponse, ErrorInfo, ResponseMeta
from src.schemas.operation import HistoryQuery, HistoryResponse, OperationRecord, RollbackResponse
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


class DeleteClipV1Request(BaseModel):
    """Request to delete a clip."""

    options: OperationOptions


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
            # NOTE: history/operations endpoints exist but disabled (features.history=false)
        ],
        "supported_operations": [
            # Write operations currently implemented in v1
            # Priority 1: Clips
            "add_clip",  # POST /projects/{id}/clips
            "move_clip",  # PATCH /projects/{id}/clips/{clip_id}/move
            "transform_clip",  # PATCH /projects/{id}/clips/{clip_id}/transform
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
            # NOTE: rollback endpoint exists but disabled (features.rollback=false)
        ],
        "planned_operations": [
            # All write operations are now implemented in v1
        ],
        "features": {
            "validate_only": True,
            "return_diff": False,  # Requires operation recording in mutations
            "rollback": False,  # Requires operation recording in mutations
            "history": False,  # Requires operation recording in mutations
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

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "add_clip"},
        )

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        data: L3ClipDetails = result
        return envelope_success(context, data)
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

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "move_clip", "clip_id": clip_id},
        )

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(context, result)
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

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "transform_clip", "clip_id": clip_id},
        )

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(context, result)
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
        try:
            flag_modified(project, "timeline_data")
            deleted_clip_id = await service.delete_clip(project, clip_id)
        except DougaError as exc:
            return envelope_error_from_exception(context, exc)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "delete_clip", "clip_id": clip_id},
        )

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(
            context,
            {"deleted": True, "clip_id": deleted_clip_id},
        )
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

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "add_layer", "layer_id": layer_summary.id},
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

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "add_audio_clip", "clip_id": audio_clip.id},
        )

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(context, {"audio_clip": audio_clip.model_dump()})

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

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "move_audio_clip", "clip_id": audio_clip.id},
        )

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(context, {"audio_clip": audio_clip.model_dump()})

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

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "delete_audio_clip", "clip_id": clip_id},
        )

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(context, {"deleted": True, "clip_id": clip_id})

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
        try:
            marker_data = await service.add_marker(project, body.marker)
        except DougaError as exc:
            return envelope_error_from_exception(context, exc)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "add_marker", "marker_id": marker_data["id"]},
        )

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(context, {"marker": marker_data})

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
        try:
            marker_data = await service.update_marker(project, marker_id, body.marker)
        except DougaError as exc:
            return envelope_error_from_exception(context, exc)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "update_marker", "marker_id": marker_data["id"]},
        )

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(context, {"marker": marker_data})

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
        try:
            marker_data = await service.delete_marker(project, marker_id)
        except DougaError as exc:
            return envelope_error_from_exception(context, exc)

        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_v1", "operation": "delete_marker", "marker_id": marker_data["id"]},
        )

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)
        return envelope_success(context, {"marker": marker_data, "deleted": True})

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


class RollbackV1Request(BaseModel):
    """Request to rollback an operation."""

    pass  # No body needed - operation_id is in path


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
    body: RollbackV1Request | None = None,
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