"""AI v1 API Router.

Thin wrapper around existing AI service with envelope responses.
Implements AI-Friendly API spec with validate_only support.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
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
    AddClipRequest,
    L1ProjectOverview,
    L2AssetCatalog,
    L2TimelineStructure,
    L3ClipDetails,
    MoveClipRequest,
    UpdateClipTransformRequest,
)
from src.schemas.clip_adapter import UnifiedClipInput, UnifiedMoveClipInput, UnifiedTransformInput
from src.schemas.envelope import EnvelopeResponse, ErrorInfo, ResponseMeta
from src.schemas.options import OperationOptions
from src.services.ai_service import AIService
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
        ],
        "supported_operations": [
            # Write operations currently implemented in v1
            "add_clip",  # POST /projects/{id}/clips
            "move_clip",  # PATCH /projects/{id}/clips/{clip_id}/move
            "transform_clip",  # PATCH /projects/{id}/clips/{clip_id}/transform
            "delete_clip",  # DELETE /projects/{id}/clips/{clip_id}
        ],
        "planned_operations": [
            # Write operations available via legacy /api/ai/project/... endpoints
            "add_audio_clip",
            "move_audio_clip",
            "delete_audio_clip",
            "add_layer",
            "reorder_layers",
            "update_layer",
            "add_audio_track",
            "add_marker",
            "update_marker",
            "delete_marker",
            "batch",
            "semantic",
        ],
        "features": {
            "validate_only": True,
            "return_diff": False,  # Phase 2+3 (alias: include_diff)
            "rollback": False,  # Phase 2+3
            "history": False,  # Phase 2+3
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
            "supported_transform_fields": ["position.x", "position.y", "scale.x"],
            "unsupported_transform_fields": [
                "rotation",
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
        },
        "limits": {
            "max_duration_ms": 3600000,
            "max_file_size_mb": 500,
            "max_layers": 5,
            "max_clips_per_layer": 100,
            "max_batch_ops": 20,
        },
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
