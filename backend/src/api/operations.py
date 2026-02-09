"""Operations API for collaborative timeline editing.

Enables clients to send granular timeline operations instead of full timeline replacements.
Supports optimistic locking via project version checking.
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from src.api.access import get_accessible_project
from src.api.deps import CurrentUser, DbSession
from src.exceptions import DougaError
from src.models.operation import ProjectOperation
from src.models.project import Project
from src.models.project_member import ProjectMember
from src.models.user import User
from src.schemas.ai import (
    AddAudioClipRequest,
    AddClipRequest,
    AddMarkerRequest,
    MoveAudioClipRequest,
    MoveClipRequest,
    UpdateAudioClipRequest,
    UpdateClipCropRequest,
    UpdateClipEffectsRequest,
    UpdateClipShapeRequest,
    UpdateClipTextRequest,
    UpdateClipTextStyleRequest,
    UpdateClipTimingRequest,
    UpdateClipTransformRequest,
    UpdateMarkerRequest,
)
from src.schemas.operations_api import (
    ApplyOperationsRequest,
    ApplyOperationsResponse,
    OperationHistoryItem,
    OperationHistoryResponse,
    OperationItem,
)
from src.services.ai_service import AIService
from src.services.event_manager import event_manager

logger = logging.getLogger(__name__)

router = APIRouter()


async def _dispatch_operation(
    service: AIService,
    project: Project,
    op: OperationItem,
) -> None:
    """Dispatch a single operation to the appropriate ai_service method.

    Modifies project.timeline_data in place. Raises on error.
    """
    op_type = op.type
    data = op.data

    # ── Clip operations ──
    if op_type == "clip.add":
        # Frontend diff sends { clip: {...full clip object...} } or flat AddClipRequest fields
        if "clip" in data:
            # Direct insertion of full clip object from frontend diff
            clip_obj = data["clip"]
            layer_id = op.layer_id or data.get("layer_id")
            timeline = project.timeline_data or {}
            inserted = False
            for layer in timeline.get("layers", []):
                if layer.get("id") == layer_id:
                    layer.setdefault("clips", []).append(clip_obj)
                    inserted = True
                    break
            if not inserted:
                raise ValueError(f"Layer not found for clip.add: {layer_id}")
            flag_modified(project, "timeline_data")
        else:
            await service.add_clip(project, AddClipRequest(**data))

    elif op_type == "clip.move":
        if not op.clip_id:
            raise ValueError("clip.move requires clip_id")
        await service.move_clip(project, op.clip_id, MoveClipRequest(**data))

    elif op_type == "clip.delete":
        if not op.clip_id:
            raise ValueError("clip.delete requires clip_id")
        await service.delete_clip(project, op.clip_id)

    elif op_type == "clip.trim":
        if not op.clip_id:
            raise ValueError("clip.trim requires clip_id")
        # Handle start_ms separately (not in UpdateClipTimingRequest)
        if "start_ms" in data:
            timeline = project.timeline_data or {}
            clip_found = False
            for layer in timeline.get("layers", []):
                for clip in layer.get("clips", []):
                    if clip.get("id") == op.clip_id:
                        clip["start_ms"] = data["start_ms"]
                        clip_found = True
                        break
                if clip_found:
                    break
            if not clip_found:
                raise ValueError(f"Clip not found: {op.clip_id}")
            flag_modified(project, "timeline_data")
        # Apply remaining timing fields
        timing_data = {k: v for k, v in data.items() if k != "start_ms"}
        if timing_data:
            await service.update_clip_timing(
                project, op.clip_id, UpdateClipTimingRequest(**timing_data)
            )

    elif op_type == "clip.transform":
        if not op.clip_id:
            raise ValueError("clip.transform requires clip_id")
        # Frontend may send { transform: {...} } — unwrap if nested
        transform_data = data.get("transform", data) if "transform" in data else data
        await service.update_clip_transform(
            project, op.clip_id, UpdateClipTransformRequest(**transform_data)
        )

    elif op_type == "clip.effects":
        if not op.clip_id:
            raise ValueError("clip.effects requires clip_id")
        effects_data = data.get("effects", data) if "effects" in data else data
        await service.update_clip_effects(
            project, op.clip_id, UpdateClipEffectsRequest(**effects_data)
        )

    elif op_type == "clip.text":
        if not op.clip_id:
            raise ValueError("clip.text requires clip_id")
        await service.update_clip_text(project, op.clip_id, UpdateClipTextRequest(**data))

    elif op_type == "clip.text_style":
        if not op.clip_id:
            raise ValueError("clip.text_style requires clip_id")
        style_data = data.get("text_style", data) if "text_style" in data else data
        await service.update_clip_text_style(
            project, op.clip_id, UpdateClipTextStyleRequest(**style_data)
        )

    elif op_type == "clip.shape":
        if not op.clip_id:
            raise ValueError("clip.shape requires clip_id")
        shape_data = data.get("shape", data) if "shape" in data else data
        await service.update_clip_shape(
            project, op.clip_id, UpdateClipShapeRequest(**shape_data)
        )

    elif op_type == "clip.crop":
        if not op.clip_id:
            raise ValueError("clip.crop requires clip_id")
        crop_data = data.get("crop", data) if "crop" in data else data
        await service.update_clip_crop(
            project, op.clip_id, UpdateClipCropRequest(**crop_data)
        )

    elif op_type == "clip.update":
        if not op.clip_id:
            raise ValueError("clip.update requires clip_id")
        # Generic clip property update (group_id, asset_id, etc.)
        timeline = project.timeline_data or {}
        clip_found = False
        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                if clip.get("id") == op.clip_id:
                    for key, value in data.items():
                        clip[key] = value
                    clip_found = True
                    break
            if clip_found:
                break
        if not clip_found:
            raise ValueError(f"Clip not found: {op.clip_id}")
        flag_modified(project, "timeline_data")

    elif op_type == "clip.keyframes":
        if not op.clip_id:
            raise ValueError("clip.keyframes requires clip_id")
        # Direct timeline mutation
        timeline = project.timeline_data or {}
        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                if clip.get("id") == op.clip_id:
                    clip["keyframes"] = data.get("keyframes", [])
                    flag_modified(project, "timeline_data")
                    return
        raise ValueError(f"Clip not found: {op.clip_id}")

    # ── Layer operations ──
    elif op_type == "layer.add":
        await service.add_layer(
            project,
            name=data.get("name", "Layer"),
            layer_type=data.get("type", "content"),
            insert_at=data.get("insert_at"),
        )

    elif op_type == "layer.delete":
        layer_id = op.layer_id
        if not layer_id:
            raise ValueError("layer.delete requires layer_id")
        timeline = project.timeline_data or {}
        layers = timeline.get("layers", [])
        original_len = len(layers)
        timeline["layers"] = [layer for layer in layers if layer.get("id") != layer_id]
        if len(timeline["layers"]) == original_len:
            raise ValueError(f"Layer not found: {layer_id}")
        flag_modified(project, "timeline_data")

    elif op_type == "layer.reorder":
        layer_ids = data.get("layer_ids", [])
        await service.reorder_layers(project, layer_ids)

    elif op_type == "layer.update":
        if not op.layer_id:
            raise ValueError("layer.update requires layer_id")
        await service.update_layer(
            project,
            layer_id=op.layer_id,
            name=data.get("name"),
            visible=data.get("visible"),
            locked=data.get("locked"),
        )

    # ── Audio clip operations ──
    elif op_type == "audio_clip.add":
        if "clip" in data:
            # Direct insertion of full audio clip object from frontend diff
            clip_obj = data["clip"]
            track_id = op.track_id or data.get("track_id")
            timeline = project.timeline_data or {}
            inserted = False
            for track in timeline.get("audio_tracks", []):
                if track.get("id") == track_id:
                    track.setdefault("clips", []).append(clip_obj)
                    inserted = True
                    break
            if not inserted:
                raise ValueError(f"Audio track not found for audio_clip.add: {track_id}")
            flag_modified(project, "timeline_data")
        else:
            await service.add_audio_clip(project, AddAudioClipRequest(**data))

    elif op_type == "audio_clip.move":
        if not op.clip_id:
            raise ValueError("audio_clip.move requires clip_id")
        await service.move_audio_clip(project, op.clip_id, MoveAudioClipRequest(**data))

    elif op_type == "audio_clip.delete":
        if not op.clip_id:
            raise ValueError("audio_clip.delete requires clip_id")
        await service.delete_audio_clip(project, op.clip_id)

    elif op_type == "audio_clip.update":
        if not op.clip_id:
            raise ValueError("audio_clip.update requires clip_id")
        # Handle fields not in UpdateAudioClipRequest (start_ms, duration_ms, etc.)
        direct_fields = {"start_ms", "duration_ms", "in_point_ms", "out_point_ms", "group_id"}
        direct_data = {k: v for k, v in data.items() if k in direct_fields}
        api_data = {k: v for k, v in data.items() if k not in direct_fields}

        if direct_data:
            timeline = project.timeline_data or {}
            clip_found = False
            for track in timeline.get("audio_tracks", []):
                for clip in track.get("clips", []):
                    if clip.get("id") == op.clip_id:
                        clip.update(direct_data)
                        clip_found = True
                        break
                if clip_found:
                    break
            if not clip_found:
                raise ValueError(f"Audio clip not found: {op.clip_id}")
            flag_modified(project, "timeline_data")

        if api_data:
            await service.update_audio_clip(
                project, op.clip_id, UpdateAudioClipRequest(**api_data)
            )

    # ── Audio track operations ──
    elif op_type == "audio_track.add":
        timeline = project.timeline_data or {}
        if "audio_tracks" not in timeline:
            timeline["audio_tracks"] = []
        new_track = {
            "id": str(uuid.uuid4()),
            "name": data.get("name", "Track"),
            "type": data.get("type", "bgm"),
            "volume": data.get("volume", 1.0),
            "muted": data.get("muted", False),
            "clips": [],
        }
        timeline["audio_tracks"].append(new_track)
        project.timeline_data = timeline
        flag_modified(project, "timeline_data")

    elif op_type == "audio_track.delete":
        track_id = op.track_id
        if not track_id:
            raise ValueError("audio_track.delete requires track_id")
        timeline = project.timeline_data or {}
        tracks = timeline.get("audio_tracks", [])
        original_len = len(tracks)
        timeline["audio_tracks"] = [t for t in tracks if t.get("id") != track_id]
        if len(timeline["audio_tracks"]) == original_len:
            raise ValueError(f"Audio track not found: {track_id}")
        flag_modified(project, "timeline_data")

    elif op_type == "audio_track.update":
        track_id = op.track_id
        if not track_id:
            raise ValueError("audio_track.update requires track_id")
        timeline = project.timeline_data or {}
        track_found = False
        for track in timeline.get("audio_tracks", []):
            if track.get("id") == track_id:
                for key, value in data.items():
                    track[key] = value
                track_found = True
                break
        if not track_found:
            raise ValueError(f"Audio track not found: {track_id}")
        flag_modified(project, "timeline_data")

    elif op_type == "audio_track.reorder":
        track_ids = data.get("track_ids", [])
        timeline = project.timeline_data or {}
        tracks = timeline.get("audio_tracks", [])
        track_map = {t.get("id"): t for t in tracks}
        reordered = []
        for tid in track_ids:
            if tid in track_map:
                reordered.append(track_map[tid])
        # Keep any tracks not in the reorder list at the end
        for t in tracks:
            if t.get("id") not in track_ids:
                reordered.append(t)
        timeline["audio_tracks"] = reordered
        project.timeline_data = timeline
        flag_modified(project, "timeline_data")

    # ── Marker operations ──
    elif op_type == "marker.add":
        await service.add_marker(project, AddMarkerRequest(**data))

    elif op_type == "marker.update":
        if not op.marker_id:
            raise ValueError("marker.update requires marker_id")
        await service.update_marker(project, op.marker_id, UpdateMarkerRequest(**data))

    elif op_type == "marker.delete":
        if not op.marker_id:
            raise ValueError("marker.delete requires marker_id")
        await service.delete_marker(project, op.marker_id)

    # ── Timeline full replace ──
    elif op_type == "timeline.full_replace":
        project.timeline_data = data.get("timeline_data", {})
        flag_modified(project, "timeline_data")

    else:
        raise ValueError(f"Unknown operation type: {op_type}")


@router.post("/{project_id}/operations")
async def apply_operations(
    project_id: str,
    request: ApplyOperationsRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> ApplyOperationsResponse:
    """Apply a batch of operations atomically.

    - Validates version match (optimistic locking)
    - Applies all operations in order
    - Returns 409 on version conflict
    - Rolls back all changes if any operation fails
    """
    # Fetch project with row-level lock
    result = await db.execute(select(Project).where(Project.id == project_id).with_for_update())
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Access control: check user has access
    if project.user_id != current_user.id:
        member_result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == current_user.id,
                ProjectMember.accepted_at.isnot(None),
            )
        )
        if member_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Project not found")

    # Version check (optimistic locking)
    if request.version != project.version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONCURRENT_MODIFICATION",
                "message": f"Version conflict: expected {request.version}, current {project.version}",
                "server_version": project.version,
            },
        )

    # Apply operations
    service = AIService(db)
    affected_clips: list[str] = []
    affected_layers: list[str] = []
    op_types: list[str] = []

    try:
        for op in request.operations:
            await _dispatch_operation(service, project, op)
            op_types.append(op.type)
            if op.clip_id:
                affected_clips.append(op.clip_id)
            if op.layer_id:
                affected_layers.append(op.layer_id)
    except DougaError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Increment version
    project.version += 1
    new_version = project.version

    # Record operation
    operation_record = ProjectOperation(
        project_id=project.id,
        operation_type="batch" if len(request.operations) > 1 else op_types[0],
        source="editor",
        affected_clips=affected_clips,
        affected_layers=affected_layers,
        affected_audio_clips=[],
        diff={"operations": [op.model_dump() for op in request.operations]},
        request_summary={"operation_count": len(request.operations), "types": op_types},
        result_summary={"new_version": new_version},
        success=True,
        user_id=current_user.id,
        project_version=new_version,
    )
    db.add(operation_record)
    await db.flush()

    # Publish event for real-time sync
    await event_manager.publish(
        project_id=str(project.id),
        event_type="timeline_updated",
        data={
            "source": "editor",
            "version": new_version,
            "user_id": str(current_user.id),
            "user_name": current_user.name,
        },
    )

    return ApplyOperationsResponse(
        version=new_version,
        timeline_data=project.timeline_data or {},
    )


@router.get("/{project_id}/operations")
async def get_operations(
    project_id: str,
    current_user: CurrentUser,
    db: DbSession,
    since_version: int = Query(0, ge=0, description="Return operations since this version"),
) -> OperationHistoryResponse:
    """Get operation history since a given version.

    Used for polling-based sync between clients.
    """
    # Access control
    project = await get_accessible_project(project_id, current_user.id, db)

    # Query operations since the given version
    result = await db.execute(
        select(ProjectOperation, User)
        .outerjoin(User, ProjectOperation.user_id == User.id)
        .where(
            ProjectOperation.project_id == project.id,
            ProjectOperation.project_version.isnot(None),
            ProjectOperation.project_version > since_version,
        )
        .order_by(ProjectOperation.project_version.asc())
    )

    items: list[OperationHistoryItem] = []
    for op, user in result.all():
        items.append(
            OperationHistoryItem(
                id=op.id,
                version=op.project_version,
                type=op.operation_type,
                user_id=op.user_id,
                user_name=user.name if user else None,
                data=op.diff or {},
                created_at=op.created_at,
            )
        )

    return OperationHistoryResponse(
        current_version=project.version,
        operations=items,
    )
