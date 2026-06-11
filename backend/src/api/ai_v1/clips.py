"""Clip CRUD, chroma-key, keyframe, split, unlink, and preview-diff endpoints for ai_v1 API."""

import hashlib
import os
import shutil
import tempfile
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from src.api.ai_v1._helpers import (
    AddKeyframeV1Request,
    CreateClipRequest,
    DeleteClipV1Request,
    DeleteKeyframeV1Request,
    MoveClipV1Request,
    TransformClipV1Request,
    UpdateClipShapeV1Request,
    UpdateClipTextV1Request,
    UpdateClipTimingV1Request,
    UpdateCropV1Request,
    UpdateEffectsV1Request,
    UpdateTextStyleV1Request,
    _asset_to_response,
    _compute_chroma_preview_times,
    _find_clip_ref,
    _find_clip_state,
    _http_error_code,
    _normalize_text_style_for_diff,
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
from src.exceptions import ChromaKeyAutoFailedError, DougaError, InvalidTimeRangeError
from src.middleware.request_context import (
    create_request_context,
    enforce_idempotency,
    validate_headers,
)
from src.models.asset import Asset
from src.schemas.ai import (
    ChromaKeyApplyRequest,
    ChromaKeyPreviewRequest,
    L3ClipDetails,
    PreviewDiffRequest,
)
from src.schemas.envelope import EnvelopeResponse
from src.schemas.operation import ChangeDetail, RequestSummary, ResultSummary
from src.schemas.options import OperationOptions
from src.services.ai_service import AIService
from src.services.chroma_key_service import ChromaKeyService
from src.services.event_manager import event_manager
from src.services.operation_service import OperationService
from src.services.storage_service import get_storage_service
from src.services.validation_service import ValidationService
from src.utils.media_info import get_media_info

router = APIRouter()


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

    # DB-backed idempotency gate: replay stored response on duplicate key
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
                logger.warning(
                    "v1.add_clip failed project=%s code=%s: %s", project_id, exc.code, exc.message
                )
                return envelope_error_from_exception(context, exc)

        # Execute the actual operation
        service = AIService(db)
        operation_service = OperationService(db)

        # Capture state before operation
        duration_before = project.duration_ms or 0

        include_audio = request.options.include_audio

        try:
            flag_modified(project, "timeline_data")
            result = await service.add_clip(project, internal_clip, include_audio=include_audio)  # type: ignore[assignment]
        except DougaError as exc:
            logger.warning(
                "v1.add_clip failed project=%s code=%s: %s", project_id, exc.code, exc.message
            )
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
        full_clip_id = result.id  # type: ignore[attr-defined]
        result_dict = _serialize_for_json(result.model_dump())  # type: ignore[attr-defined]

        # Check for linked audio clip
        linked_audio_clip_id = getattr(result, "_linked_audio_clip_id", None)
        linked_audio_clip_details = None
        if linked_audio_clip_id:
            try:
                linked_audio_clip_details = await service.get_audio_clip_details(
                    project, linked_audio_clip_id
                )
            except Exception:
                logger.warning(
                    "Failed to get linked audio clip details for %s", linked_audio_clip_id
                )

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
        full_layer_id = result.layer_id  # type: ignore[attr-defined]

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
                key_params=_serialize_for_json(
                    {"asset_id": internal_clip.asset_id, "start_ms": internal_clip.start_ms}
                ),
            ),
            result_summary=ResultSummary(
                success=True,
                created_ids=[full_clip_id],
            ),
            rollback_data=_serialize_for_json({"clip_id": full_clip_id, "clip_data": result_dict}),
            rollback_available=True,
            idempotency_key=headers.get("idempotency_key"),
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
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
                context.warnings.append(
                    "Linked audio not yet available (extraction may still be in progress)"
                )
        if request.auto_wrapped:
            response_data["auto_wrapped"] = True
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            {
                "type": "preview_seek",
                "seek_to_ms": result.timing.start_ms,  # type: ignore[attr-defined]
                "reason": "Start of added clip",
            },
            "Use PATCH /clips/{clip_id}/effects to add fade transitions",
            "Use PATCH /clips/{clip_id}/transform to adjust position",
            "Use GET /timeline-overview to see the updated layout",
        ]

        # Add overlap warnings to response context
        overlap_warnings = getattr(result, "_overlap_warnings", [])
        if overlap_warnings:
            context.warnings.extend(overlap_warnings)

        logger.info("v1.add_clip ok project=%s clip=%s", project_id, full_clip_id)
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=headers.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
            http_status=status.HTTP_201_CREATED,
        )
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
                result = await validation_service.validate_move_clip(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.move_clip failed project=%s clip=%s code=%s: %s",
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
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_start_ms = original_clip_state.get("start_ms") if original_clip_state else None
        original_layer_id = original_clip_state.get("layer_id") if original_clip_state else None

        try:
            flag_modified(project, "timeline_data")
            result = await service.move_clip(project, clip_id, internal_request)  # type: ignore[assignment]
        except DougaError as exc:
            logger.warning(
                "v1.move_clip failed project=%s clip=%s code=%s: %s",
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
                message="Failed to move clip",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and values from result (Pydantic model)
        # Use result.layer_id as source of truth (full ID after move)
        full_clip_id = result.id  # type: ignore[attr-defined]
        duration_after = project.duration_ms or 0
        new_start_ms = result.timing.start_ms  # type: ignore[attr-defined]
        new_layer_id = result.layer_id  # type: ignore[attr-defined]  # Full ID from L3ClipDetails

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
                key_params={
                    "new_start_ms": internal_request.new_start_ms,
                    "new_layer_id": internal_request.new_layer_id,
                },
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
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

        logger.info(
            "v1.move_clip ok project=%s clip=%s linked_moved=%s",
            project_id,
            full_clip_id,
            linked_clips_moved,
        )
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=headers.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )
    except HTTPException as exc:
        logger.warning(
            "v1.move_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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
                logger.warning(
                    "v1.transform_clip failed project=%s clip=%s code=%s: %s",
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
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_transform = (
            original_clip_state.get("transform", {}).copy() if original_clip_state else {}
        )

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_clip_transform(project, clip_id, internal_request)  # type: ignore[assignment]
        except DougaError as exc:
            logger.warning(
                "v1.transform_clip failed project=%s clip=%s code=%s: %s",
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
                message="Failed to transform clip",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and transform from result (Pydantic model)
        full_clip_id = result.id  # type: ignore[attr-defined]
        duration_after = project.duration_ms or 0
        new_transform = result.transform.model_dump()  # type: ignore[attr-defined]

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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
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
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=headers.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )
    except HTTPException as exc:
        logger.warning(
            "v1.transform_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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
                result = await validation_service.validate_update_effects(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.update_clip_effects failed project=%s clip=%s code=%s: %s",
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
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_effects = (
            original_clip_state.get("effects", {}).copy() if original_clip_state else {}
        )
        original_transition_in = (
            original_clip_state.get("transition_in", {}).copy() if original_clip_state else {}
        )
        original_transition_out = (
            original_clip_state.get("transition_out", {}).copy() if original_clip_state else {}
        )

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_clip_effects(project, clip_id, internal_request)  # type: ignore[assignment]
        except DougaError as exc:
            logger.warning(
                "v1.update_clip_effects failed project=%s clip=%s code=%s: %s",
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
                message="Failed to update clip effects",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and new state from result
        full_clip_id = result.id  # type: ignore[attr-defined]
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
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
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=headers.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )
    except HTTPException as exc:
        logger.warning(
            "v1.update_clip_effects failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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
        project, _seq = await _resolve_edit_session(
            project_id, current_user, db, x_edit_session, read_only=True
        )
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
            logger.warning(
                "v1.preview_chroma_key runtime_error project=%s clip=%s", project_id, clip_id
            )
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
            logger.warning(
                "v1.preview_chroma_key runtime_error project=%s clip=%s", project_id, clip_id
            )
            return envelope_error(
                context,
                code="INTERNAL_ERROR",
                message=str(exc),
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except HTTPException as exc:
        logger.warning(
            "v1.preview_chroma_key failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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
    headers = validate_headers(http_request, context, validate_only=False)

    # DB-backed idempotency gate: replay stored response on duplicate key
    cached = await enforce_idempotency(headers.get("idempotency_key"), db, current_user.id)
    if cached is not None:
        return JSONResponse(status_code=cached.status_code, content=cached.body)

    try:
        project, _seq = await _resolve_edit_session_for_write(
            project_id, current_user, db, x_edit_session
        )
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

            times = (
                _compute_chroma_preview_times(start_ms, duration_ms) if duration_ms > 0 else None
            )
            try:
                resolved_color = chroma_service.resolve_key_color(
                    input_path,
                    request.key_color,
                    sample_times_ms=times,
                    clip_start_ms=start_ms,
                    in_point_ms=in_point_ms,
                )
            except RuntimeError:
                logger.warning(
                    "v1.apply_chroma_key runtime_error project=%s clip=%s", project_id, clip_id
                )
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
                logger.info(
                    "v1.apply_chroma_key ok project=%s clip=%s cached=True", project_id, clip_id
                )
                operation_service = OperationService(db)
                operation = await operation_service.record_operation(
                    project=project,
                    operation_type="apply_chroma_key",
                    source="api_v1",
                    success=True,
                    affected_clips=[str(full_clip_id or clip_id)],
                    request_summary=RequestSummary(
                        endpoint="/clips/{clip_id}/chroma-key/apply",
                        method="POST",
                        target_ids=[str(full_clip_id or clip_id)],
                        key_params=_serialize_for_json(
                            {"key_color": request.key_color, "cached": True}
                        ),
                    ),
                    result_summary=ResultSummary(
                        success=True, created_ids=[str(existing_asset.id)]
                    ),
                    rollback_available=False,
                    idempotency_key=headers.get("idempotency_key"),
                    user_id=current_user.id,
                )
                await db.flush()
                return await idempotent_success(
                    context,
                    {
                        "resolved_key_color": resolved_color,
                        "asset_id": str(existing_asset.id),
                        "asset": asset_response,
                    },
                    idempotency_key=headers.get("idempotency_key"),
                    operation_id=operation.id,
                    db=db,
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
            logger.info(
                "v1.apply_chroma_key ok project=%s clip=%s asset=%s",
                project_id,
                clip_id,
                new_asset.id,
            )
            operation_service = OperationService(db)
            try:
                operation = await operation_service.record_operation(
                    project=project,
                    operation_type="apply_chroma_key",
                    source="api_v1",
                    success=True,
                    affected_clips=[str(full_clip_id or clip_id)],
                    request_summary=RequestSummary(
                        endpoint="/clips/{clip_id}/chroma-key/apply",
                        method="POST",
                        target_ids=[str(full_clip_id or clip_id)],
                        key_params=_serialize_for_json({"key_color": request.key_color}),
                    ),
                    result_summary=ResultSummary(success=True, created_ids=[str(new_asset.id)]),
                    rollback_available=False,
                    idempotency_key=headers.get("idempotency_key"),
                    user_id=current_user.id,
                )
            except HTTPException as conflict_exc:
                # record_operation rolled back the DB session on 409 conflict, so the
                # new_asset row is gone. Delete the just-uploaded GCS file to avoid
                # leaving an orphan blob. Failure to delete is non-fatal; log and
                # re-raise the original 409 so the client can retry. (Issue #292)
                if conflict_exc.status_code == status.HTTP_409_CONFLICT:
                    try:
                        await storage.delete_file(storage_key)
                        logger.info(
                            "v1.apply_chroma_key orphan GCS blob deleted key=%s", storage_key
                        )
                    except Exception as del_exc:
                        logger.warning(
                            "v1.apply_chroma_key failed to delete orphan GCS blob key=%s: %s",
                            storage_key,
                            del_exc,
                        )
                raise
            await db.flush()
            return await idempotent_success(
                context,
                {
                    "resolved_key_color": resolved_color,
                    "asset_id": str(new_asset.id),
                    "asset": asset_response,
                },
                idempotency_key=headers.get("idempotency_key"),
                operation_id=operation.id,
                db=db,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except HTTPException as exc:
        logger.warning(
            "v1.apply_chroma_key failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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
                result = await validation_service.validate_update_crop(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.update_clip_crop failed project=%s clip=%s code=%s: %s",
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
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_crop = original_clip_state.get("crop", {}).copy() if original_clip_state else {}

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_clip_crop(project, clip_id, internal_request)  # type: ignore[assignment]
        except DougaError as exc:
            logger.warning(
                "v1.update_clip_crop failed project=%s clip=%s code=%s: %s",
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
                message="Failed to update clip crop",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get the full clip ID from result
        full_clip_id = result.id  # type: ignore[attr-defined]

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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Rollback not yet implemented for crop updates; re-apply previous values manually"
            if not operation.rollback_available
            else "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo",
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
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=headers.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )
    except HTTPException as exc:
        logger.warning(
            "v1.update_clip_crop failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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
                result = await validation_service.validate_update_text_style(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.update_clip_text_style failed project=%s clip=%s code=%s: %s",
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
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_text_style = _normalize_text_style_for_diff(
            original_clip_state.get("text_style") if original_clip_state else {}
        )

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_clip_text_style(project, clip_id, internal_request)  # type: ignore[assignment]
        except DougaError as exc:
            logger.warning(
                "v1.update_clip_text_style failed project=%s clip=%s code=%s: %s",
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
                message="Failed to update clip text style",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get the full clip ID from result
        full_clip_id = result.id  # type: ignore[attr-defined]

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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
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
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=headers.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )
    except HTTPException as exc:
        logger.warning(
            "v1.update_clip_text_style failed project=%s clip=%s: %s",
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

    # DB-backed idempotency gate
    if not validate_only:
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

        # Handle validate_only mode
        if validate_only:
            validation_service = ValidationService(db)
            try:
                result = await validation_service.validate_delete_clip(project, clip_id)
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.delete_clip failed project=%s clip=%s code=%s: %s",
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
            logger.warning(
                "v1.delete_clip failed project=%s clip=%s code=%s: %s",
                project_id,
                clip_id,
                exc.code,
                exc.message,
            )
            return envelope_error_from_exception(context, exc)

        # Use full clip ID from delete result or from state lookup
        actual_deleted_id = (
            delete_result["deleted_id"]
            if isinstance(delete_result, dict)
            else (delete_result or full_clip_id)
        )
        deleted_linked_ids = (
            delete_result.get("deleted_linked_ids", []) if isinstance(delete_result, dict) else []
        )
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "deleted": True,
            "clip_id": actual_deleted_id,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
        }
        if deleted_linked_ids:
            response_data["deleted_linked_ids"] = deleted_linked_ids
        if include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            "Use GET /timeline-overview to see the updated layout",
            "Use GET /analysis/gaps to check for newly created gaps",
        ]

        logger.info(
            "v1.delete_clip ok project=%s clip=%s linked=%s",
            project_id,
            actual_deleted_id,
            deleted_linked_ids,
        )
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=headers.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )
    except HTTPException as exc:
        logger.warning(
            "v1.delete_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# Layer Endpoints (Priority 2)
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
        project, _seq = await _resolve_edit_session(
            project_id, current_user, db, x_edit_session, read_only=True
        )
        if _seq:
            project.timeline_data = _seq.timeline_data
        response.headers["ETag"] = compute_project_etag(project)

        service = AIService(db)
        clip_details: L3ClipDetails | None = await service.get_clip_details(project, clip_id)

        if clip_details is None:
            return envelope_error(
                context,
                code="CLIP_NOT_FOUND",
                message=f"Clip not found: {clip_id}",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return envelope_success(context, clip_details.model_dump())

    except HTTPException as exc:
        logger.warning(
            "v1.get_clip_details failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
        return envelope_error(
            context,
            code=_http_error_code(exc.status_code, str(exc.detail)),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


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
                result = await validation_service.validate_update_clip_timing(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.update_clip_timing failed project=%s clip=%s code=%s: %s",
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
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_timing = (
            {
                "start_ms": original_clip_state.get("start_ms", 0),
                "duration_ms": original_clip_state.get("duration_ms", 0),
                "speed": original_clip_state.get("speed"),
                "in_point_ms": original_clip_state.get("in_point_ms", 0),
                "out_point_ms": original_clip_state.get("out_point_ms"),
            }
            if original_clip_state
            else {}
        )

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_clip_timing(project, clip_id, internal_request)  # type: ignore[assignment]
        except DougaError as exc:
            logger.warning(
                "v1.update_clip_timing failed project=%s clip=%s code=%s: %s",
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
                message="Failed to update clip timing",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and new state from result
        full_clip_id = result.id  # type: ignore[attr-defined]
        duration_after = project.duration_ms or 0

        # Get new state
        new_clip_state, _ = _find_clip_state(project, full_clip_id)
        new_timing = (
            {
                "start_ms": new_clip_state.get("start_ms", 0),
                "duration_ms": new_clip_state.get("duration_ms", 0),
                "speed": new_clip_state.get("speed"),
                "in_point_ms": new_clip_state.get("in_point_ms", 0),
                "out_point_ms": new_clip_state.get("out_point_ms"),
            }
            if new_clip_state
            else {}
        )

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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo"
            if operation.rollback_available
            else "Rollback data not available for this operation",
        }
        if linked_clips_updated:
            response_data["linked_clips_updated"] = linked_clips_updated
        if request.auto_wrapped:
            response_data["auto_wrapped"] = True
        if request.options.include_diff:
            response_data["diff"] = diff.model_dump()
        response_data["hints"] = [
            {
                "type": "preview_seek",
                "seek_to_ms": result.timing.start_ms,  # type: ignore[attr-defined]
                "reason": "Start of trimmed clip",
            },
        ]

        logger.info(
            "v1.update_clip_timing ok project=%s clip=%s linked_updated=%s",
            project_id,
            full_clip_id,
            linked_clips_updated,
        )
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=headers.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )
    except HTTPException as exc:
        logger.warning(
            "v1.update_clip_timing failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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
                result = await validation_service.validate_update_clip_text(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.update_clip_text failed project=%s clip=%s code=%s: %s",
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
        original_clip_state, _ = _find_clip_state(project, clip_id)
        original_text_content = (
            original_clip_state.get("text_content", "") if original_clip_state else ""
        )

        try:
            flag_modified(project, "timeline_data")
            result = await service.update_clip_text(project, clip_id, internal_request)  # type: ignore[assignment]
        except DougaError as exc:
            logger.warning(
                "v1.update_clip_text failed project=%s clip=%s code=%s: %s",
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
                message="Failed to update clip text content",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and new state from result
        full_clip_id = result.id  # type: ignore[attr-defined]
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Rollback not yet implemented for text content updates; re-apply previous values manually"
            if not operation.rollback_available
            else "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo",
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
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=headers.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )
    except HTTPException as exc:
        logger.warning(
            "v1.update_clip_text failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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
                result = await validation_service.validate_update_clip_shape(
                    project, clip_id, internal_request
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.update_clip_shape failed project=%s clip=%s code=%s: %s",
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
            result = await service.update_clip_shape(project, clip_id, internal_request)  # type: ignore[assignment]
        except DougaError as exc:
            logger.warning(
                "v1.update_clip_shape failed project=%s clip=%s code=%s: %s",
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
                message="Failed to update clip shape",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get full clip ID and new state from result
        full_clip_id = result.id  # type: ignore[attr-defined]
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "clip": result,
            "operation_id": str(operation.id),
            "rollback_available": operation.rollback_available,
            "rollback_url": f"/api/ai/v1/projects/{project_id}/operations/{operation.id}/rollback"
            if operation.rollback_available
            else None,
            "rollback_reason": "Rollback not yet implemented for shape updates; re-apply previous values manually"
            if not operation.rollback_available
            else "Full state snapshot stored; use POST /operations/{op_id}/rollback to undo",
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
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=headers.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )
    except HTTPException as exc:
        logger.warning(
            "v1.update_clip_shape failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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
    logger.info(
        "v1.add_keyframe project=%s clip=%s time_ms=%s", project_id, clip_id, body.keyframe.time_ms
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
                logger.warning(
                    "v1.add_keyframe failed project=%s clip=%s code=%s: %s",
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
        original_clip_state, full_clip_id_before = _find_clip_state(project, clip_id)
        original_keyframes = None
        if original_clip_state:
            original_keyframes = original_clip_state.get("keyframes")

        try:
            keyframe_data = await service.add_keyframe(project, clip_id, internal_request)
        except DougaError as exc:
            logger.warning(
                "v1.add_keyframe failed project=%s clip=%s code=%s: %s",
                project_id,
                clip_id,
                exc.code,
                exc.message,
            )
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "keyframe": _serialize_for_json(keyframe_data),
            "clip_id": actual_clip_id,
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

        logger.info(
            "v1.add_keyframe ok project=%s clip=%s keyframe=%s",
            project_id,
            actual_clip_id,
            keyframe_id,
        )
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
            http_status=201,
        )

    except HTTPException as exc:
        logger.warning(
            "v1.add_keyframe failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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
    logger.info(
        "v1.delete_keyframe project=%s clip=%s keyframe=%s", project_id, clip_id, keyframe_id
    )

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
                result = await validation_service.validate_delete_keyframe(
                    project, clip_id, keyframe_id
                )
                return envelope_success(context, result.to_dict())
            except DougaError as exc:
                logger.warning(
                    "v1.delete_keyframe failed project=%s clip=%s keyframe=%s code=%s: %s",
                    project_id,
                    clip_id,
                    keyframe_id,
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
            keyframe_data = await service.delete_keyframe(project, clip_id, keyframe_id)
        except DougaError as exc:
            logger.warning(
                "v1.delete_keyframe failed project=%s clip=%s keyframe=%s code=%s: %s",
                project_id,
                clip_id,
                keyframe_id,
                exc.code,
                exc.message,
            )
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
            user_id=current_user.id,
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
        response_data: dict[str, Any] = {
            "keyframe": _serialize_for_json(keyframe_data),
            "clip_id": actual_clip_id,
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

        logger.info(
            "v1.delete_keyframe ok project=%s clip=%s keyframe=%s",
            project_id,
            actual_clip_id,
            actual_keyframe_id,
        )
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=header_result.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )

    except HTTPException as exc:
        logger.warning(
            "v1.delete_keyframe failed project=%s clip=%s keyframe=%s: %s",
            project_id,
            clip_id,
            keyframe_id,
            exc.detail,
        )
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

    headers = validate_headers(
        http_request,
        context,
        validate_only=request.options.validate_only,
    )

    # DB-backed idempotency gate: replay stored response on duplicate key
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

        if request.options.validate_only:
            return envelope_success(
                context, {"valid": True, "message": "Split operation would succeed"}
            )

        service = AIService(db)

        try:
            flag_modified(project, "timeline_data")
            result = await service.split_clip(project, clip_id, request.split_at_ms)
        except DougaError as exc:
            logger.warning(
                "v1.split_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.message
            )
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

        # Record the operation so the idempotency key is persisted under the
        # UNIQUE (user_id, idempotency_key) index (concurrent retries -> 409)
        # and the response can be replayed on a later retry.
        operation_service = OperationService(db)
        operation = await operation_service.record_operation(
            project=project,
            operation_type="split_clip",
            source="api_v1",
            success=True,
            affected_clips=[clip_id],
            request_summary=RequestSummary(
                endpoint="/clips/{clip_id}/split",
                method="POST",
                target_ids=[clip_id],
                key_params=_serialize_for_json({"split_at_ms": request.split_at_ms}),
            ),
            result_summary=ResultSummary(success=True),
            rollback_available=False,
            idempotency_key=headers.get("idempotency_key"),
            user_id=current_user.id,
        )

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        response_data = _serialize_for_json(
            {
                "left_clip": result["left_clip"],
                "right_clip": result["right_clip"],
                "left_group_id": result["left_group_id"],
                "right_group_id": result["right_group_id"],
                "linked_splits": result["linked_splits"],
            }
        )

        logger.info("v1.split_clip ok project=%s clip=%s", project_id, clip_id)
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=headers.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )
    except HTTPException as exc:
        logger.warning(
            "v1.split_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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

    headers = validate_headers(
        http_request,
        context,
        validate_only=validate_only,
    )

    # DB-backed idempotency gate: replay stored response on duplicate key
    if not validate_only:
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

        if validate_only:
            return envelope_success(
                context, {"valid": True, "message": "Unlink operation would succeed"}
            )

        service = AIService(db)

        try:
            flag_modified(project, "timeline_data")
            result = await service.unlink_clip(project, clip_id)
        except DougaError as exc:
            logger.warning(
                "v1.unlink_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.message
            )
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

        # Record the operation so the idempotency key is persisted under the
        # UNIQUE (user_id, idempotency_key) index (concurrent retries -> 409)
        # and the response can be replayed on a later retry.
        operation_service = OperationService(db)
        operation = await operation_service.record_operation(
            project=project,
            operation_type="unlink_clip",
            source="api_v1",
            success=True,
            affected_clips=[clip_id],
            request_summary=RequestSummary(
                endpoint="/clips/{clip_id}/unlink",
                method="POST",
                target_ids=[clip_id],
                key_params={},
            ),
            result_summary=ResultSummary(success=True),
            rollback_available=False,
            idempotency_key=headers.get("idempotency_key"),
            user_id=current_user.id,
        )

        await db.flush()
        await db.refresh(project)
        response.headers["ETag"] = compute_project_etag(project)

        response_data: dict[str, Any] = {
            "clip_id": result["clip_id"],
            "unlinked": True,
            "previous_group_id": result["previous_group_id"],
        }

        logger.info("v1.unlink_clip ok project=%s clip=%s", project_id, clip_id)
        return await idempotent_success(
            context,
            response_data,
            idempotency_key=headers.get("idempotency_key"),
            operation_id=operation.id,
            db=db,
        )
    except HTTPException as exc:
        logger.warning(
            "v1.unlink_clip failed project=%s clip=%s: %s", project_id, clip_id, exc.detail
        )
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
            project_id, current_user, db, x_edit_session, read_only=True
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
