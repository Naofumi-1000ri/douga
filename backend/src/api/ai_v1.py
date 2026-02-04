"""AI v1 API Router.

Thin wrapper around existing AI service with envelope responses.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from src.api.deps import CurrentUser, DbSession
from src.middleware.request_context import (
    RequestContext,
    build_meta,
    create_request_context,
    validate_headers,
)
from src.models.project import Project
from src.schemas.ai import AddClipRequest, L1ProjectOverview, L2AssetCatalog, L2TimelineStructure, L3ClipDetails
from src.schemas.envelope import EnvelopeResponse, ErrorInfo, ResponseMeta
from src.schemas.options import OperationOptions
from src.services.ai_service import AIService
from src.services.event_manager import event_manager
from src.utils.interpolation import EASING_FUNCTIONS

router = APIRouter()


class CreateClipRequest(BaseModel):
    options: OperationOptions
    clip: AddClipRequest


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
    meta: ResponseMeta = build_meta(context)
    error = ErrorInfo(code=code, message=message)
    envelope = EnvelopeResponse(
        request_id=context.request_id,
        error=error,
        meta=meta,
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(envelope.model_dump(exclude_none=True)),
    )


@router.get("/capabilities", response_model=EnvelopeResponse)
async def get_capabilities(
    current_user: CurrentUser,
) -> EnvelopeResponse:
    context = create_request_context()

    capabilities = {
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
        "max_layers": 5,
        "max_duration_ms": 3600000,
        "max_batch_ops": 20,
    }

    return envelope_success(context, capabilities)


@router.get("/version", response_model=EnvelopeResponse)
async def get_version(
    current_user: CurrentUser,
) -> EnvelopeResponse:
    context = create_request_context()
    data = {
        "api_version": "1.0",
        "schema_version": "1.0",
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

    if request.options.validate_only:
        return envelope_error(
            context,
            code="FEATURE_NOT_SUPPORTED",
            message="validate_only is not supported yet",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    try:
        project = await get_user_project(project_id, current_user, db)
        current_etag = compute_project_etag(project)
        if headers["if_match"] and headers["if_match"] != current_etag:
            return envelope_error(
                context,
                code="CONCURRENT_MODIFICATION",
                message="If-Match does not match current project version",
                status_code=status.HTTP_409_CONFLICT,
            )

        service = AIService(db)
        try:
            flag_modified(project, "timeline_data")
            result = await service.add_clip(project, request.clip)
        except ValueError as exc:
            return envelope_error(
                context,
                code="BAD_REQUEST",
                message=str(exc),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

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
