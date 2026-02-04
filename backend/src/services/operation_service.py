"""Operation Service for tracking and managing operation history.

Provides functionality for:
- Recording operations after successful mutations
- Querying operation history
- Rolling back operations
- Computing diffs between states
"""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.exceptions import OperationNotFoundError, RollbackNotAvailableError
from src.models.operation import ProjectOperation
from src.models.project import Project
from src.schemas.operation import (
    ChangeDetail,
    HistoryQuery,
    HistoryResponse,
    OperationMeta,
    OperationRecord,
    OperationSummary,
    RequestSummary,
    ResultSummary,
    RollbackResponse,
    TimelineDiff,
)

logger = logging.getLogger(__name__)

# Operations that support rollback in Phase 2+3
# When recording operations, set rollback_available=False for operations not in this set
SUPPORTED_ROLLBACK_OPERATIONS = frozenset([
    "add_clip",
    "delete_clip",
    "move_clip",
    "update_transform",
    "add_layer",
    "add_audio_clip",
    "delete_audio_clip",
    "add_marker",
    "delete_marker",
])

# Operations that do NOT support rollback (for reference)
# - update_layer: Would need to store all changed properties
# - update_effects: Complex effect state
# - move_audio_clip: Not implemented
# - add_audio_track: Would need full track data
# - reorder_layers: Would need to store original order
# - batch: Would need recursive rollback
# - semantic: Operation-specific, complex


class OperationService:
    """Service for operation history management."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def is_rollback_supported(operation_type: str) -> bool:
        """Check if an operation type supports rollback.

        Args:
            operation_type: The operation type to check

        Returns:
            True if rollback is supported for this operation type
        """
        return operation_type in SUPPORTED_ROLLBACK_OPERATIONS

    # =========================================================================
    # Record Operations
    # =========================================================================

    async def record_operation(
        self,
        project: Project,
        operation_type: str,
        source: str,
        *,
        success: bool,
        affected_clips: list[str] | None = None,
        affected_layers: list[str] | None = None,
        affected_audio_clips: list[str] | None = None,
        diff: TimelineDiff | None = None,
        request_summary: RequestSummary | None = None,
        result_summary: ResultSummary | None = None,
        rollback_data: dict[str, Any] | None = None,
        rollback_available: bool = True,
        error_code: str | None = None,
        error_message: str | None = None,
        idempotency_key: str | None = None,
        user_id: UUID | None = None,
    ) -> ProjectOperation:
        """Record a new operation.

        Args:
            project: The project being modified
            operation_type: Type of operation (add_clip, move_clip, etc.)
            source: Source of operation (api_v1, ai_chat, batch, semantic)
            success: Whether the operation succeeded
            affected_clips: List of clip IDs affected
            affected_layers: List of layer IDs affected
            affected_audio_clips: List of audio clip IDs affected
            diff: Diff information (changes only)
            request_summary: Summary of the request
            result_summary: Summary of the result
            rollback_data: Data needed for rollback
            rollback_available: Whether rollback is possible
            error_code: Error code if failed
            error_message: Error message if failed
            idempotency_key: Idempotency key from request
            user_id: User who performed the operation

        Returns:
            The created ProjectOperation record
        """
        operation = ProjectOperation(
            project_id=project.id,
            operation_type=operation_type,
            source=source,
            success=success,
            affected_clips=affected_clips or [],
            affected_layers=affected_layers or [],
            affected_audio_clips=affected_audio_clips or [],
            diff=diff.model_dump() if diff else None,
            request_summary=request_summary.model_dump() if request_summary else None,
            result_summary=result_summary.model_dump() if result_summary else None,
            rollback_data=rollback_data,
            rollback_available=rollback_available,
            error_code=error_code,
            error_message=error_message,
            idempotency_key=idempotency_key,
            user_id=user_id,
        )

        self.db.add(operation)
        await self.db.flush()
        await self.db.refresh(operation)

        logger.info(
            f"Recorded operation {operation.id}: {operation_type} "
            f"(success={success}, rollback_available={rollback_available})"
        )

        return operation

    def get_operation_meta(
        self, operation: ProjectOperation
    ) -> OperationMeta:
        """Get operation metadata for response."""
        return OperationMeta(
            operation_id=operation.id,
            rollback_available=operation.rollback_available,
        )

    # =========================================================================
    # Query Operations
    # =========================================================================

    async def get_operation(
        self, project_id: UUID, operation_id: UUID
    ) -> ProjectOperation | None:
        """Get a single operation by ID.

        Args:
            project_id: Project ID (for access control)
            operation_id: Operation ID to retrieve

        Returns:
            ProjectOperation or None if not found
        """
        result = await self.db.execute(
            select(ProjectOperation).where(
                and_(
                    ProjectOperation.id == operation_id,
                    ProjectOperation.project_id == project_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def get_history(
        self, project_id: UUID, query: HistoryQuery
    ) -> HistoryResponse:
        """Query operation history.

        Args:
            project_id: Project ID
            query: Query parameters

        Returns:
            HistoryResponse with paginated operations
        """
        # Build base query
        stmt = select(ProjectOperation).where(
            ProjectOperation.project_id == project_id
        )

        # Apply filters
        if query.operation_type:
            stmt = stmt.where(ProjectOperation.operation_type == query.operation_type)
        if query.source:
            stmt = stmt.where(ProjectOperation.source == query.source)
        if query.since:
            stmt = stmt.where(ProjectOperation.created_at >= query.since)
        if query.until:
            stmt = stmt.where(ProjectOperation.created_at <= query.until)
        if query.success_only:
            stmt = stmt.where(ProjectOperation.success == True)  # noqa: E712
        if query.clip_id:
            # Use JSONB contains operator
            stmt = stmt.where(
                ProjectOperation.affected_clips.contains([query.clip_id])
            )

        # Get total count
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar() or 0

        # Apply pagination and ordering
        stmt = stmt.order_by(desc(ProjectOperation.created_at))
        stmt = stmt.offset((query.page - 1) * query.page_size)
        stmt = stmt.limit(query.page_size)

        result = await self.db.execute(stmt)
        operations = result.scalars().all()

        # Convert to summaries
        summaries = [
            OperationSummary(
                id=op.id,
                operation_type=op.operation_type,
                source=op.source,
                success=op.success,
                rollback_available=op.rollback_available and not op.rolled_back,
                rolled_back=op.rolled_back,
                created_at=op.created_at,
                result_summary=ResultSummary(**op.result_summary)
                if op.result_summary
                else None,
            )
            for op in operations
        ]

        return HistoryResponse(
            operations=summaries,
            total=total,
            page=query.page,
            page_size=query.page_size,
            has_more=(query.page * query.page_size) < total,
        )

    async def get_operation_record(
        self, project_id: UUID, operation_id: UUID
    ) -> OperationRecord:
        """Get full operation record.

        Args:
            project_id: Project ID
            operation_id: Operation ID

        Returns:
            Full OperationRecord

        Raises:
            OperationNotFoundError: If operation not found
        """
        operation = await self.get_operation(project_id, operation_id)
        if not operation:
            raise OperationNotFoundError(str(operation_id))

        return OperationRecord(
            id=operation.id,
            project_id=operation.project_id,
            operation_type=operation.operation_type,
            source=operation.source,
            affected_clips=operation.affected_clips,
            affected_layers=operation.affected_layers,
            affected_audio_clips=operation.affected_audio_clips,
            diff=TimelineDiff(**operation.diff) if operation.diff else None,
            request_summary=RequestSummary(**operation.request_summary)
            if operation.request_summary
            else None,
            result_summary=ResultSummary(**operation.result_summary)
            if operation.result_summary
            else None,
            rollback_available=operation.rollback_available and not operation.rolled_back,
            rolled_back=operation.rolled_back,
            rolled_back_at=operation.rolled_back_at,
            rolled_back_by=operation.rolled_back_by,
            success=operation.success,
            error_code=operation.error_code,
            error_message=operation.error_message,
            idempotency_key=operation.idempotency_key,
            user_id=operation.user_id,
            created_at=operation.created_at,
        )

    # =========================================================================
    # Rollback
    # =========================================================================

    async def rollback_operation(
        self,
        project: Project,
        operation_id: UUID,
        *,
        user_id: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[RollbackResponse, ProjectOperation]:
        """Rollback an operation.

        Args:
            project: The project
            operation_id: Operation ID to rollback
            user_id: User performing the rollback
            idempotency_key: Idempotency key for rollback operation

        Returns:
            Tuple of (RollbackResponse, new rollback operation record)

        Raises:
            OperationNotFoundError: If operation not found
            RollbackNotAvailableError: If rollback not available
        """
        # Get the original operation
        original = await self.get_operation(project.id, operation_id)
        if not original:
            raise OperationNotFoundError(str(operation_id))

        if not original.rollback_available:
            raise RollbackNotAvailableError(
                str(operation_id), "Rollback not available for this operation"
            )

        if original.rolled_back:
            raise RollbackNotAvailableError(
                str(operation_id), "Operation already rolled back"
            )

        # Guard: Can't rollback failed operations
        if not original.success:
            raise RollbackNotAvailableError(
                str(operation_id), "Cannot rollback a failed operation"
            )

        # Guard: Can't rollback without rollback_data
        if not original.rollback_data:
            raise RollbackNotAvailableError(
                str(operation_id), "No rollback data available for this operation"
            )

        # Apply the rollback using stored rollback_data
        reverted_changes = await self._apply_rollback(project, original)

        # Mark original operation as rolled back
        original.rolled_back = True
        original.rolled_back_at = datetime.utcnow()

        # Record the rollback operation
        rollback_result_summary = ResultSummary(
            success=True,
            modified_ids=[c.entity_id for c in reverted_changes if c.change_type == "modified"],
            created_ids=[c.entity_id for c in reverted_changes if c.change_type == "created"],
            deleted_ids=[c.entity_id for c in reverted_changes if c.change_type == "deleted"],
            message=f"Rolled back operation {operation_id}",
        )

        rollback_request_summary = RequestSummary(
            endpoint=f"/operations/{operation_id}/rollback",
            method="POST",
            target_ids=[str(operation_id)],
            key_params={},
        )

        rollback_op = await self.record_operation(
            project,
            operation_type=f"rollback_{original.operation_type}",
            source="api_v1",
            success=True,
            affected_clips=original.affected_clips,
            affected_layers=original.affected_layers,
            affected_audio_clips=original.affected_audio_clips,
            request_summary=rollback_request_summary,
            result_summary=rollback_result_summary,
            rollback_available=False,  # Rollback of rollback not supported
            user_id=user_id,
            idempotency_key=idempotency_key,
        )

        # Update original to reference the rollback operation
        original.rolled_back_by = rollback_op.id

        response = RollbackResponse(
            rollback_operation_id=rollback_op.id,
            reverted_changes=reverted_changes,
            success=True,
            message=f"Successfully rolled back {original.operation_type}",
        )

        return response, rollback_op

    async def _apply_rollback(
        self, project: Project, operation: ProjectOperation
    ) -> list[ChangeDetail]:
        """Apply rollback changes to the project.

        This method applies the inverse of the original operation using
        the stored rollback_data.

        Args:
            project: Project to modify
            operation: Operation to rollback

        Returns:
            List of changes made during rollback
        """
        reverted_changes: list[ChangeDetail] = []
        rollback_data = operation.rollback_data or {}
        timeline = project.timeline_data or {}

        # Rollback based on operation type
        if operation.operation_type == "add_clip":
            # Delete the added clip
            clip_id = rollback_data.get("clip_id")
            if clip_id:
                for layer in timeline.get("layers", []):
                    layer["clips"] = [
                        c for c in layer.get("clips", [])
                        if c.get("id") != clip_id
                    ]
                reverted_changes.append(ChangeDetail(
                    entity_type="clip",
                    entity_id=clip_id,
                    change_type="deleted",
                    before=rollback_data.get("clip_data"),
                    after=None,
                ))

        elif operation.operation_type == "delete_clip":
            # Restore the deleted clip
            clip_data = rollback_data.get("clip_data")
            layer_id = rollback_data.get("layer_id")
            if clip_data and layer_id:
                for layer in timeline.get("layers", []):
                    if layer.get("id") == layer_id:
                        layer.setdefault("clips", []).append(clip_data)
                        break
                reverted_changes.append(ChangeDetail(
                    entity_type="clip",
                    entity_id=clip_data.get("id"),
                    change_type="created",
                    before=None,
                    after=clip_data,
                ))

        elif operation.operation_type == "move_clip":
            # Restore original position and layer
            clip_id = rollback_data.get("clip_id")
            original_start_ms = rollback_data.get("original_start_ms")
            original_layer_id = rollback_data.get("original_layer_id")
            new_layer_id = rollback_data.get("new_layer_id")

            if clip_id and original_start_ms is not None:
                # Find and remove clip from current layer
                clip_data = None
                current_layer_id = None
                for layer in timeline.get("layers", []):
                    for i, clip in enumerate(layer.get("clips", [])):
                        if clip.get("id") == clip_id:
                            clip_data = layer["clips"].pop(i)
                            current_layer_id = layer.get("id")
                            break
                    if clip_data:
                        break

                if clip_data:
                    # Restore original start_ms
                    clip_data["start_ms"] = original_start_ms

                    # Find target layer and add clip
                    target_layer_id = original_layer_id or current_layer_id
                    for layer in timeline.get("layers", []):
                        if layer.get("id") == target_layer_id:
                            layer.setdefault("clips", []).append(clip_data)
                            break

                    change_detail = {
                        "start_ms": original_start_ms,
                    }
                    if original_layer_id and original_layer_id != current_layer_id:
                        change_detail["layer_id"] = original_layer_id

                    reverted_changes.append(ChangeDetail(
                        entity_type="clip",
                        entity_id=clip_id,
                        change_type="modified",
                        before={
                            "start_ms": rollback_data.get("new_start_ms"),
                            "layer_id": new_layer_id or current_layer_id,
                        },
                        after=change_detail,
                    ))

        elif operation.operation_type == "update_transform":
            # Restore original transform
            clip_id = rollback_data.get("clip_id")
            original_transform = rollback_data.get("original_transform")
            if clip_id and original_transform:
                for layer in timeline.get("layers", []):
                    for clip in layer.get("clips", []):
                        if clip.get("id") == clip_id:
                            for key, value in original_transform.items():
                                clip[key] = value
                            reverted_changes.append(ChangeDetail(
                                entity_type="clip",
                                entity_id=clip_id,
                                change_type="modified",
                                before=rollback_data.get("new_transform"),
                                after=original_transform,
                            ))
                            break

        elif operation.operation_type == "add_layer":
            # Delete the added layer
            layer_id = rollback_data.get("layer_id")
            if layer_id:
                timeline["layers"] = [
                    l for l in timeline.get("layers", [])
                    if l.get("id") != layer_id
                ]
                reverted_changes.append(ChangeDetail(
                    entity_type="layer",
                    entity_id=layer_id,
                    change_type="deleted",
                    before=rollback_data.get("layer_data"),
                    after=None,
                ))

        elif operation.operation_type == "add_audio_clip":
            # Delete the added audio clip
            clip_id = rollback_data.get("clip_id")
            if clip_id:
                for track in timeline.get("audio_tracks", []):
                    track["clips"] = [
                        c for c in track.get("clips", [])
                        if c.get("id") != clip_id
                    ]
                reverted_changes.append(ChangeDetail(
                    entity_type="audio_clip",
                    entity_id=clip_id,
                    change_type="deleted",
                    before=rollback_data.get("clip_data"),
                    after=None,
                ))

        elif operation.operation_type == "delete_audio_clip":
            # Restore the deleted audio clip
            clip_data = rollback_data.get("clip_data")
            track_id = rollback_data.get("track_id")
            if clip_data and track_id:
                for track in timeline.get("audio_tracks", []):
                    if track.get("id") == track_id:
                        track.setdefault("clips", []).append(clip_data)
                        break
                reverted_changes.append(ChangeDetail(
                    entity_type="audio_clip",
                    entity_id=clip_data.get("id"),
                    change_type="created",
                    before=None,
                    after=clip_data,
                ))

        elif operation.operation_type == "add_marker":
            # Delete the added marker
            marker_id = rollback_data.get("marker_id")
            if marker_id:
                timeline["markers"] = [
                    m for m in timeline.get("markers", [])
                    if m.get("id") != marker_id
                ]
                reverted_changes.append(ChangeDetail(
                    entity_type="marker",
                    entity_id=marker_id,
                    change_type="deleted",
                    before=rollback_data.get("marker_data"),
                    after=None,
                ))

        elif operation.operation_type == "delete_marker":
            # Restore the deleted marker
            marker_data = rollback_data.get("marker_data")
            if marker_data:
                timeline.setdefault("markers", []).append(marker_data)
                timeline["markers"].sort(key=lambda m: m.get("time_ms", 0))
                reverted_changes.append(ChangeDetail(
                    entity_type="marker",
                    entity_id=marker_data.get("id"),
                    change_type="created",
                    before=None,
                    after=marker_data,
                ))

        # Update project timeline and mark as modified
        project.timeline_data = timeline
        flag_modified(project, "timeline_data")

        # Recalculate project duration
        self._update_project_duration(project)

        return reverted_changes

    def _update_project_duration(self, project: Project) -> None:
        """Recalculate and update project duration based on timeline content.

        Updates both timeline_data.duration_ms and project.duration_ms.
        """
        timeline = project.timeline_data or {}
        max_end_ms = 0

        # Check video/image clips
        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                start_ms = clip.get("start_ms", 0)
                duration_ms = clip.get("duration_ms", 0)
                end_ms = start_ms + duration_ms
                if end_ms > max_end_ms:
                    max_end_ms = end_ms

        # Check audio clips
        for track in timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                start_ms = clip.get("start_ms", 0)
                duration_ms = clip.get("duration_ms", 0)
                end_ms = start_ms + duration_ms
                if end_ms > max_end_ms:
                    max_end_ms = end_ms

        # Update both timeline_data and project
        timeline["duration_ms"] = max_end_ms
        project.duration_ms = max_end_ms

    # =========================================================================
    # Diff Computation
    # =========================================================================

    def compute_diff(
        self,
        operation_id: UUID,
        operation_type: str,
        changes: list[ChangeDetail],
        duration_before_ms: int,
        duration_after_ms: int,
    ) -> TimelineDiff:
        """Compute a diff for an operation.

        Args:
            operation_id: ID of the operation
            operation_type: Type of operation
            changes: List of changes
            duration_before_ms: Duration before operation
            duration_after_ms: Duration after operation

        Returns:
            TimelineDiff with the changes
        """
        return TimelineDiff(
            operation_id=str(operation_id),
            operation_type=operation_type,
            changes=changes,
            duration_before_ms=duration_before_ms,
            duration_after_ms=duration_after_ms,
        )
