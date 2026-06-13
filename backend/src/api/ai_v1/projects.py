"""Project-level read/write endpoints for ai_v1 API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from src.api.ai_v1._helpers import (
    _http_error_code,
    _resolve_edit_session,
    _serialize_for_json,
    compute_project_etag,
    envelope_error,
    envelope_success,
    idempotent_success,
    logger,
)
from src.api.deps import CurrentUser, DbSession
from src.middleware.request_context import (
    create_request_context,
    enforce_idempotency,
    validate_headers,
)
from src.models.project import Project
from src.models.project_member import ProjectMember
from src.models.sequence import Sequence, _default_timeline_data
from src.schemas.ai import (
    L1ProjectOverview,
    L2AssetCatalog,
    L2TimelineStructure,
    L25TimelineOverview,
)
from src.schemas.envelope import EnvelopeResponse
from src.schemas.operation import RequestSummary, ResultSummary
from src.services.ai_service import AIService
from src.services.event_manager import event_manager
from src.services.operation_service import OperationService

router = APIRouter()


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
    width: int = Field(
        default=1920, ge=256, le=4096, description="Canvas width in pixels (must be even)"
    )
    height: int = Field(
        default=1080, ge=256, le=4096, description="Canvas height in pixels (must be even)"
    )
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
    http_request: Request,
) -> EnvelopeResponse | JSONResponse:
    """Create a new project within the V1 API namespace.

    Creates a project with default timeline structure (5 layers + 3 audio tracks).
    Returns the project data including its ID for subsequent operations.
    """
    context = create_request_context()
    logger.info("v1.create_project name=%s user=%s", request.name, current_user.id)

    # Idempotency-Key is OPTIONAL here (project creation predates the key contract),
    # but when supplied we honor it: validate_only=True skips the "key required" error
    # while still validating the UUID format if a key is present.
    headers = validate_headers(http_request, context, validate_only=True)
    idem_key = headers.get("idempotency_key")
    if idem_key:
        cached = await enforce_idempotency(idem_key, db, current_user.id)
        if cached is not None:
            return JSONResponse(status_code=cached.status_code, content=cached.body)

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

    # Match legacy /api/projects creation semantics: initialize the realtime
    # Firestore access document for the owner. Failures are logged by the manager.
    await event_manager.set_allowed_users(
        project_id=project.id,
        firebase_uids=[current_user.firebase_uid],
    )

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

    # When an Idempotency-Key was supplied, record the operation (persisting the key
    # under the UNIQUE (user_id, idempotency_key) index) and store the response so a
    # retry replays it instead of creating a duplicate project.
    if idem_key:
        operation_service = OperationService(db)
        operation = await operation_service.record_operation(
            project=project,
            operation_type="create_project",
            source="api_v1",
            success=True,
            request_summary=RequestSummary(
                endpoint="/projects",
                method="POST",
                target_ids=[str(project.id)],
                key_params=_serialize_for_json({"name": request.name}),
            ),
            result_summary=ResultSummary(success=True, created_ids=[str(project.id)]),
            rollback_available=False,
            idempotency_key=idem_key,
            user_id=current_user.id,
        )
        await db.flush()
        return await idempotent_success(
            context,
            project_data,
            idempotency_key=idem_key,
            operation_id=operation.id,
            db=db,
            http_status=status.HTTP_201_CREATED,
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
        project, _seq = await _resolve_edit_session(
            project_id, current_user, db, x_edit_session, read_only=True
        )
        if _seq:
            project.timeline_data = _seq.timeline_data
            project.duration_ms = _seq.duration_ms
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
    context.warnings.append("This endpoint is an alias for /overview. Use /overview instead.")
    logger.info("v1.get_project_summary (alias) project=%s", project_id)

    try:
        project, _seq = await _resolve_edit_session(
            project_id, current_user, db, x_edit_session, read_only=True
        )
        if _seq:
            project.timeline_data = _seq.timeline_data
            project.duration_ms = _seq.duration_ms
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
        project, _seq = await _resolve_edit_session(
            project_id, current_user, db, x_edit_session, read_only=True
        )
        if _seq:
            project.timeline_data = _seq.timeline_data
            project.duration_ms = _seq.duration_ms
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
    logger.info(
        "v1.get_timeline_overview project=%s include_snapshot=%s", project_id, include_snapshot
    )

    try:
        project, _seq = await _resolve_edit_session(
            project_id, current_user, db, x_edit_session, read_only=True
        )
        if _seq:
            project.timeline_data = _seq.timeline_data
            project.duration_ms = _seq.duration_ms
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
        project, _seq = await _resolve_edit_session(
            project_id, current_user, db, x_edit_session, read_only=True
        )
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
