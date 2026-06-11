"""Layer, audio-clip, audio-track, and marker endpoints for ai_v1 API."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm.attributes import flag_modified

from src.api.ai_v1._helpers import (
    AddAudioClipV1Request,
    AddAudioTrackV1Request,
    AddLayerV1Request,
    AddMarkerV1Request,
    DeleteAudioClipV1Request,
    DeleteMarkerV1Request,
    MoveAudioClipV1Request,
    ReorderLayersV1Request,
    UpdateAudioClipV1Request,
    UpdateLayerV1Request,
    UpdateMarkerV1Request,
    _find_audio_clip_state,
    _find_marker_state,
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
    L3AudioClipDetails,
)
from src.schemas.envelope import EnvelopeResponse
from src.schemas.operation import ChangeDetail, RequestSummary, ResultSummary
from src.services.ai_service import AIService
from src.services.event_manager import event_manager
from src.services.operation_service import OperationService
from src.services.validation_service import ValidationService

router = APIRouter()


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
    logger.info(
        "v1.add_layer project=%s name=%s type=%s", project_id, body.layer.name, body.layer.type
    )

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
                result = await validation_service.validate_add_layer(project, body.layer)
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.add_layer failed project=%s code=%s: %s", project_id, exc.code, exc.message
                )
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
            logger.warning(
                "v1.add_layer failed project=%s code=%s: %s", project_id, exc.code, exc.message
            )
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "layer": layer_summary.model_dump(),
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            "Use POST /clips to add clips to this layer",
            "Use PUT /layers/order to adjust layer stacking order",
        ]

        logger.info("v1.add_layer ok project=%s layer=%s", project_id, layer_id)
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )

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
                result = await validation_service.validate_update_layer(
                    project, layer_id, body.layer
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.update_layer failed project=%s layer=%s code=%s: %s",
                    project_id,
                    layer_id,
                    exc.code,
                    exc.message,
                )
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
            logger.warning(
                "v1.update_layer failed project=%s layer=%s code=%s: %s",
                project_id,
                layer_id,
                exc.code,
                exc.message,
            )
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
            logger.warning(
                "v1.update_layer failed project=%s layer=%s: %s", project_id, layer_id, e
            )
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
        return await idempotent_success(
            context,
            {"layer": layer_summary.model_dump()},
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=None,
            db=db,
        )

    except HTTPException as exc:
        logger.warning(
            "v1.update_layer failed project=%s layer=%s: %s", project_id, layer_id, exc.detail
        )
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
                result = await validation_service.validate_reorder_layers(
                    project, body.order.layer_ids
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.reorder_layers failed project=%s code=%s: %s",
                    project_id,
                    exc.code,
                    exc.message,
                )
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        try:
            layer_summaries = await service.reorder_layers(
                project,
                layer_ids=body.order.layer_ids,
            )
        except DougaError as exc:
            logger.warning(
                "v1.reorder_layers failed project=%s code=%s: %s", project_id, exc.code, exc.message
            )
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
        return await idempotent_success(
            context,
            {"layers": [layer.model_dump() for layer in layer_summaries]},
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=None,
            db=db,
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
                result = await validation_service.validate_add_audio_clip(project, body.clip)
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.add_audio_clip failed project=%s code=%s: %s",
                    project_id,
                    exc.code,
                    exc.message,
                )
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0

        try:
            audio_clip = await service.add_audio_clip(project, body.clip)
        except DougaError as exc:
            logger.warning(
                "v1.add_audio_clip failed project=%s code=%s: %s", project_id, exc.code, exc.message
            )
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
                key_params=_serialize_for_json(
                    {"asset_id": body.clip.asset_id, "start_ms": body.clip.start_ms}
                ),
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "audio_clip": audio_clip.model_dump(),
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            {
                "type": "preview_seek",
                "seek_to_ms": audio_clip.timing.start_ms,
                "reason": "Start of added audio clip",
            },
            "Use PATCH /audio-clips/{clip_id} to adjust volume and fades",
            "Use GET /timeline-overview to see the updated audio layout",
        ]

        logger.info("v1.add_audio_clip ok project=%s clip=%s", project_id, clip_id)
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
            http_status=201,
        )

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
                result = await validation_service.validate_move_audio_clip(
                    project, clip_id, body.to_internal_request()
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.move_audio_clip failed project=%s clip=%s code=%s: %s",
                    project_id,
                    clip_id,
                    exc.code,
                    exc.message,
                )
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
            audio_clip = await service.move_audio_clip(project, clip_id, body.to_internal_request())
        except DougaError as exc:
            logger.warning(
                "v1.move_audio_clip failed project=%s clip=%s code=%s: %s",
                project_id,
                clip_id,
                exc.code,
                exc.message,
            )
            return envelope_error_from_exception(context, exc)
        except ValueError as e:
            logger.warning(
                "v1.move_audio_clip failed project=%s clip=%s: %s", project_id, clip_id, e
            )
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
                key_params={
                    "new_start_ms": internal_request.new_start_ms,
                    "new_track_id": internal_request.new_track_id,
                },
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "audio_clip": audio_clip.model_dump(),
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.move_audio_clip ok project=%s clip=%s", project_id, result_clip_id)
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )

    except HTTPException as exc:
        logger.warning(
            "v1.move_audio_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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
        header_result = validate_headers(http_request, context, validate_only=validate_only)

        # DB-backed idempotency gate
        if not validate_only:
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

        if validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_delete_audio_clip(project, clip_id)
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.delete_audio_clip failed project=%s clip=%s code=%s: %s",
                    project_id,
                    clip_id,
                    exc.code,
                    exc.message,
                )
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
            logger.warning(
                "v1.delete_audio_clip failed project=%s clip=%s code=%s: %s",
                project_id,
                clip_id,
                exc.code,
                exc.message,
            )
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "deleted": True,
            "clip_id": full_clip_id,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
        }
        if include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.delete_audio_clip ok project=%s clip=%s", project_id, full_clip_id)
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )

    except HTTPException as exc:
        logger.warning(
            "v1.delete_audio_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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
    logger.info(
        "v1.add_audio_track project=%s name=%s type=%s",
        project_id,
        body.track.name,
        body.track.type,
    )

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
                result = await validation_service.validate_add_audio_track(project, body.track)
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.add_audio_track failed project=%s code=%s: %s",
                    project_id,
                    exc.code,
                    exc.message,
                )
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
            logger.warning(
                "v1.add_audio_track failed project=%s code=%s: %s",
                project_id,
                exc.code,
                exc.message,
            )
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
        return await idempotent_success(
            context,
            {"audio_track": track_summary.model_dump()},
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=None,
            db=db,
        )

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
    logger.info(
        "v1.add_marker project=%s time_ms=%s name=%s",
        project_id,
        body.marker.time_ms,
        body.marker.name,
    )

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
                result = await validation_service.validate_add_marker(project, body.marker)
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.add_marker failed project=%s code=%s: %s", project_id, exc.code, exc.message
                )
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (markers don't affect duration)
        duration_before = project.duration_ms or 0

        try:
            marker_data = await service.add_marker(project, body.marker)
        except DougaError as exc:
            logger.warning(
                "v1.add_marker failed project=%s code=%s: %s", project_id, exc.code, exc.message
            )
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "marker": marker_data,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.add_marker ok project=%s marker=%s", project_id, marker_id)
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )

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
                result = await validation_service.validate_update_marker(
                    project, marker_id, body.marker
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.update_marker failed project=%s marker=%s code=%s: %s",
                    project_id,
                    marker_id,
                    exc.code,
                    exc.message,
                )
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
            logger.warning(
                "v1.update_marker failed project=%s marker=%s code=%s: %s",
                project_id,
                marker_id,
                exc.code,
                exc.message,
            )
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "marker": marker_data,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
        }
        if body.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.update_marker ok project=%s marker=%s", project_id, actual_marker_id)
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )

    except HTTPException as exc:
        logger.warning(
            "v1.update_marker failed project=%s marker=%s: %s", project_id, marker_id, exc.detail
        )
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
        header_result = validate_headers(http_request, context, validate_only=validate_only)

        # DB-backed idempotency gate
        if not validate_only:
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

        if validate_only:
            # Dry-run validation
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_delete_marker(project, marker_id)
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.delete_marker failed project=%s marker=%s code=%s: %s",
                    project_id,
                    marker_id,
                    exc.code,
                    exc.message,
                )
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation (markers don't affect duration)
        duration_before = project.duration_ms or 0

        try:
            marker_data = await service.delete_marker(project, marker_id)
        except DougaError as exc:
            logger.warning(
                "v1.delete_marker failed project=%s marker=%s code=%s: %s",
                project_id,
                marker_id,
                exc.code,
                exc.message,
            )
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "marker": marker_data,
            "deleted": True,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
        }
        if include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.delete_marker ok project=%s marker=%s", project_id, actual_marker_id)
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )

    except HTTPException as exc:
        logger.warning(
            "v1.delete_marker failed project=%s marker=%s: %s", project_id, marker_id, exc.detail
        )
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
        project, _seq = await _resolve_edit_session(
            project_id, current_user, db, x_edit_session, read_only=True
        )
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
        logger.warning(
            "v1.get_audio_clip_details failed project=%s clip=%s: %s",
            project_id,
            clip_id,
            exc.detail,
        )
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


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

    # DB-backed idempotency gate
    if not request.options.validate_only:
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
                logger.warning(
                    "v1.update_audio_clip failed project=%s clip=%s code=%s: %s",
                    project_id,
                    clip_id,
                    exc.code,
                    exc.message,
                )
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0
        original_clip_state, _ = _find_audio_clip_state(project, clip_id)
        original_audio_props = (
            {
                "volume": original_clip_state.get("volume", 1.0),
                "fade_in_ms": original_clip_state.get("fade_in_ms", 0),
                "fade_out_ms": original_clip_state.get("fade_out_ms", 0),
                "volume_keyframes": original_clip_state.get("volume_keyframes", []),
            }
            if original_clip_state
            else {}
        )

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_audio_clip(project, clip_id, internal_request)  # type: ignore[assignment]
        except DougaError as exc:
            logger.warning(
                "v1.update_audio_clip failed project=%s clip=%s code=%s: %s",
                project_id,
                clip_id,
                exc.code,
                exc.message,
            )
            return envelope_error_from_exception(context, exc)

        if result is None:
            return envelope_error(
                context,
                code="INTERNAL_ERROR",
                message="Failed to update audio clip",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and new state from result
        full_clip_id = result.id  # type: ignore[attr-defined]
        duration_after = project.duration_ms or 0

        # Get new state
        new_clip_state, _ = _find_audio_clip_state(project, full_clip_id)
        new_audio_props = (
            {
                "volume": new_clip_state.get("volume", 1.0),
                "fade_in_ms": new_clip_state.get("fade_in_ms", 0),
                "fade_out_ms": new_clip_state.get("fade_out_ms", 0),
                "volume_keyframes": new_clip_state.get("volume_keyframes", []),
            }
            if new_clip_state
            else {}
        )

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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Rollback not yet implemented for audio clip property updates; re-apply previous values manually"
            if not operation.rollback_available
            else "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo",
        }
        if not operation.rollback_available:
            response_data.setdefault("hints", []).append(
                "To undo: re-apply previous values via PATCH /audio-clips/{clip_id}"
            )
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()

        logger.info("v1.update_audio_clip ok project=%s clip=%s", project_id, full_clip_id)
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=headers.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )
    except HTTPException as exc:
        logger.warning(
            "v1.update_audio_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# #083: PATCH /clips/{clip_id}/timing - Update clip timing
# =============================================================================
