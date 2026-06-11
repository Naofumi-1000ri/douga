"""Batch, semantic, history, rollback, timeline-at-time, and analysis endpoints for ai_v1 API."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm.attributes import flag_modified

from src.api.ai_v1._helpers import (
    BatchOperationV1Request,
    SemanticOperationV1Request,
    _http_error_code,
    _resolve_edit_session,
    _resolve_edit_session_for_write,
    _serialize_for_json,
    _sync_sequence_duration,
    compute_project_etag,
    envelope_error,
    envelope_error_from_exception,
    envelope_success,
    idempotent_success,
    logger,
)
from src.api.deps import CurrentUser, DbSession
from src.exceptions import DougaError
from src.middleware.request_context import (
    create_request_context,
    enforce_idempotency,
    validate_headers,
)
from src.schemas.ai import (
    BatchOperationResult,
    GapAnalysisResult,
    L2TimelineAtTime,
    PacingAnalysisResult,
    SemanticOperationResult,
)
from src.schemas.envelope import EnvelopeResponse
from src.schemas.operation import (
    HistoryQuery,
    HistoryResponse,
    OperationRecord,
    RequestSummary,
    ResultSummary,
    RollbackRequest,
)
from src.services.ai_service import AIService
from src.services.event_manager import event_manager
from src.services.operation_service import OperationService
from src.services.validation_service import ValidationService

router = APIRouter()


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
        project, _seq = await _resolve_edit_session(
            project_id, current_user, db, x_edit_session, read_only=True
        )
        if _seq:
            project.timeline_data = _seq.timeline_data
            project.duration_ms = _seq.duration_ms
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
        logger.warning(
            "v1.get_timeline_at_time failed project=%s time_ms=%s: %s",
            project_id,
            time_ms,
            exc.detail,
        )
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
        header_result = validate_headers(request, context, validate_only=body.options.validate_only)

        # DB-backed idempotency gate
        if not body.options.validate_only:
            cached = await enforce_idempotency(
                header_result.get("idempotency_key"), db, current_user.id
            )
            if cached is not None:
                return JSONResponse(status_code=cached.status_code, content=cached.body)

        project, _seq = await _resolve_edit_session_for_write(
            project_id, current_user, db, x_edit_session
        )
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
        max_batch_ops = 20
        if len(body.operations) > max_batch_ops:
            return envelope_error(
                context,
                code="VALIDATION_ERROR",
                message=f"Batch contains {len(body.operations)} operations, exceeds limit of {max_batch_ops}",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if body.options.validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                validate_result = await validation_service.validate_batch_operations(
                    project, body.operations
                )
                return envelope_success(context, validate_result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.execute_batch failed project=%s code=%s: %s",
                    project_id,
                    exc.code,
                    exc.message,
                )
                return envelope_error_from_exception(context, exc)

        # Execute the actual batch operations
        service = AIService(db)
        operation_service = OperationService(db)
        try:
            result: BatchOperationResult = await service.execute_batch_operations(
                project,
                body.operations,
                rollback_on_failure=body.options.rollback_on_failure,
                continue_on_error=body.options.continue_on_error,
                include_audio=body.options.include_audio,
            )
        except DougaError as exc:
            logger.warning(
                "v1.execute_batch failed project=%s code=%s: %s", project_id, exc.code, exc.message
            )
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
                key_params=_serialize_for_json(
                    {
                        "total_operations": result.total_operations,
                        "operation_types": [op.operation for op in body.operations],
                    }
                ),
            ),
            result_summary=ResultSummary(
                success=result.success,
                created_ids=created_ids,
                message=f"Batch: {result.successful_operations}/{result.total_operations} succeeded",
            ),
            rollback_data=None,
            rollback_available=False,
            idempotency_key=header_result.get("idempotency_key"),
            user_id=current_user.id,
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
        logger.info(
            "v1.execute_batch ok project=%s success=%s fail=%s",
            project_id,
            result.successful_operations,
            result.failed_operations,
        )

        # Include operation_id in response
        response_data = result.model_dump()
        response_data["operation_id"] = str(operation.id)
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )

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
    - rename_layer: Rename a layer
    """
    context = create_request_context()
    sem_op = body.resolved_operation
    logger.info("v1.execute_semantic project=%s op=%s", project_id, sem_op.operation)

    try:
        # Validate headers (Idempotency-Key required for mutations)
        header_result = validate_headers(request, context, validate_only=body.options.validate_only)

        # DB-backed idempotency gate
        if not body.options.validate_only:
            cached = await enforce_idempotency(
                header_result.get("idempotency_key"), db, current_user.id
            )
            if cached is not None:
                return JSONResponse(status_code=cached.status_code, content=cached.body)

        project, _seq = await _resolve_edit_session_for_write(
            project_id, current_user, db, x_edit_session
        )
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
                validate_result = await validation_service.validate_semantic_operation(
                    project, sem_op
                )
                return envelope_success(context, validate_result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.execute_semantic failed project=%s code=%s: %s",
                    project_id,
                    exc.code,
                    exc.message,
                )
                return envelope_error_from_exception(context, exc)

        # Execute the actual semantic operation
        service = AIService(db)
        operation_service = OperationService(db)
        try:
            result: SemanticOperationResult = await service.execute_semantic_operation(
                project, sem_op
            )
        except DougaError as exc:
            logger.warning(
                "v1.execute_semantic failed project=%s code=%s: %s",
                project_id,
                exc.code,
                exc.message,
            )
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
                key_params=_serialize_for_json(
                    {"operation": sem_op.operation, "parameters": sem_op.parameters}
                ),
            ),
            result_summary=ResultSummary(
                success=True,
                modified_ids=result.affected_clip_ids,
                message="; ".join(result.changes_made) if result.changes_made else None,
            ),
            rollback_data=None,
            rollback_available=False,
            idempotency_key=header_result.get("idempotency_key"),
            user_id=current_user.id,
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
        response_data["rollback_url"] = (
            f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None
        )
        if not operation.rollback_available:
            response_data.setdefault("hints", []).append(
                "To undo semantic ops: use DELETE or PATCH on affected individual clips"
            )
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )

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
        project, _seq = await _resolve_edit_session(
            project_id, current_user, db, x_edit_session, read_only=True
        )
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
        history: HistoryResponse = await operation_service.get_history(project.id, query)

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
        project, _seq = await _resolve_edit_session(
            project_id, current_user, db, x_edit_session, read_only=True
        )
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
            logger.warning(
                "v1.get_operation failed project=%s operation=%s code=%s: %s",
                project_id,
                operation_id,
                exc.code,
                exc.message,
            )
            return envelope_error_from_exception(context, exc)

    except HTTPException as exc:
        logger.warning(
            "v1.get_operation failed project=%s operation=%s: %s",
            project_id,
            operation_id,
            exc.detail,
        )
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

    # DB-backed idempotency gate: replay stored response on duplicate key
    cached = await enforce_idempotency(headers.get("idempotency_key"), db, current_user.id)
    if cached is not None:
        return JSONResponse(status_code=cached.status_code, content=cached.body)

    try:
        project, _seq = await _resolve_edit_session_for_write(
            project_id, current_user, db, x_edit_session
        )
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
            logger.warning(
                "v1.rollback_operation failed project=%s operation=%s code=%s: %s",
                project_id,
                operation_id,
                exc.code,
                exc.message,
            )
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
        return await idempotent_success(
            context,
            rollback_response.model_dump(),
            idempotency_key=headers.get("idempotency_key"),
            operation_id=rollback_op.id,
            db=db,
        )

    except HTTPException as exc:
        logger.warning(
            "v1.rollback_operation failed project=%s operation=%s: %s",
            project_id,
            operation_id,
            exc.detail,
        )
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
        project, _seq = await _resolve_edit_session(
            project_id, current_user, db, x_edit_session, read_only=True
        )
        if _seq:
            project.timeline_data = _seq.timeline_data
            project.duration_ms = _seq.duration_ms
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
    strategy: Annotated[
        str, Query(description="Segmentation strategy: 'fixed_interval' or 'content_aware'")
    ] = "content_aware",
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
    logger.info(
        "v1.analyze_pacing project=%s segment=%s strategy=%s",
        project_id,
        segment_duration_ms,
        strategy,
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
        result: PacingAnalysisResult = await service.analyze_pacing(
            project,
            segment_duration_ms=segment_duration_ms,
            strategy=strategy,
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
