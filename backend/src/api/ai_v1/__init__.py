"""AI v1 API package.

Split from the monolithic ai_v1.py into submodules while preserving the public
``src.api.ai_v1`` import path and the original route registration order.
"""

from fastapi import APIRouter
from starlette.routing import BaseRoute

from src.api.ai_v1 import capabilities, clips, history, layers, projects
from src.api.ai_v1._helpers import (  # noqa: F401
    OPERATION_DETAILS,
    AddAudioClipV1Request,
    AddAudioTrackV1Request,
    AddKeyframeV1Request,
    AddLayerV1Request,
    AddMarkerV1Request,
    BatchOperationV1Request,
    CreateClipRequest,
    DeleteAudioClipV1Request,
    DeleteClipV1Request,
    DeleteKeyframeV1Request,
    DeleteMarkerV1Request,
    MoveAudioClipV1Request,
    MoveClipV1Request,
    ReorderLayersV1Request,
    SemanticOperationV1Request,
    TransformClipV1Request,
    UpdateAudioClipV1Request,
    UpdateClipShapeV1Request,
    UpdateClipTextV1Request,
    UpdateClipTimingV1Request,
    UpdateCropV1Request,
    UpdateEffectsV1Request,
    UpdateLayerV1Request,
    UpdateMarkerV1Request,
    UpdateTextStyleV1Request,
    _asset_to_response,
    _auto_wrap_flat_body,
    _compute_chroma_preview_times,
    _find_audio_clip_state,
    _find_clip_ref,
    _find_clip_state,
    _find_marker_state,
    _http_error_code,
    _match_id,
    _normalize_text_style_for_diff,
    _resolve_edit_session,
    _resolve_edit_session_for_write,
    _serialize_for_json,
    _sync_sequence_duration,
    _use_sequence_timeline,
    compute_project_etag,
    envelope_error,
    envelope_error_from_exception,
    envelope_success,
    get_user_project,
    idempotent_success,
    logger,
)
from src.api.ai_v1._helpers import (
    _find_allowed_methods as _find_allowed_methods_for_router,
)
from src.api.ai_v1.capabilities import (  # noqa: F401
    get_capabilities,
    get_schemas,
    get_version,
    v1_catch_all,
)
from src.api.ai_v1.clips import (  # noqa: F401
    SplitClipV1Request,
    UnlinkClipV1Request,
    add_clip,
    add_keyframe,
    apply_chroma_key,
    delete_clip,
    delete_keyframe,
    get_clip_details,
    move_clip,
    preview_chroma_key,
    preview_diff,
    split_clip,
    transform_clip,
    unlink_clip,
    update_clip_crop,
    update_clip_effects,
    update_clip_shape,
    update_clip_text,
    update_clip_text_style,
    update_clip_timing,
)
from src.api.ai_v1.history import (  # noqa: F401
    analyze_gaps,
    analyze_pacing,
    execute_batch,
    execute_semantic,
    get_history,
    get_operation,
    get_timeline_at_time,
    rollback_operation,
)
from src.api.ai_v1.layers import (  # noqa: F401
    add_audio_clip,
    add_audio_track,
    add_layer,
    add_marker,
    delete_audio_clip,
    delete_marker,
    get_audio_clip_details,
    move_audio_clip,
    reorder_layers,
    update_audio_clip,
    update_layer,
    update_marker,
)
from src.api.ai_v1.projects import (  # noqa: F401
    CreateProjectV1Request,
    create_project_v1,
    get_asset_catalog,
    get_project_overview,
    get_project_summary,
    get_timeline_overview,
    get_timeline_structure,
    list_projects_v1,
)
from src.api.deps import get_edit_context as get_edit_context
from src.services.storage_service import get_storage_service as get_storage_service

router = APIRouter()


def _include_routes_by_name(source: APIRouter, names: tuple[str, ...]) -> None:
    routes_by_name: dict[str, BaseRoute] = {}
    for route in source.routes:
        route_name = getattr(route, "name", None)
        if isinstance(route_name, str):
            routes_by_name[route_name] = route
    for name in names:
        router.routes.append(routes_by_name[name])


_include_routes_by_name(capabilities.router, ("get_capabilities", "get_version"))
_include_routes_by_name(
    projects.router,
    (
        "list_projects_v1",
        "create_project_v1",
        "get_project_overview",
        "get_project_summary",
        "get_timeline_structure",
        "get_timeline_overview",
        "get_asset_catalog",
    ),
)
_include_routes_by_name(
    clips.router,
    (
        "add_clip",
        "move_clip",
        "transform_clip",
        "update_clip_effects",
        "preview_chroma_key",
        "apply_chroma_key",
        "update_clip_crop",
        "update_clip_text_style",
        "delete_clip",
    ),
)
_include_routes_by_name(
    layers.router,
    (
        "add_layer",
        "update_layer",
        "reorder_layers",
        "add_audio_clip",
        "move_audio_clip",
        "delete_audio_clip",
        "add_audio_track",
        "add_marker",
        "update_marker",
        "delete_marker",
    ),
)
_include_routes_by_name(clips.router, ("get_clip_details",))
_include_routes_by_name(
    history.router,
    (
        "get_timeline_at_time",
        "execute_batch",
        "execute_semantic",
        "get_history",
        "get_operation",
        "rollback_operation",
    ),
)
_include_routes_by_name(layers.router, ("get_audio_clip_details",))
_include_routes_by_name(capabilities.router, ("get_schemas",))
_include_routes_by_name(history.router, ("analyze_gaps", "analyze_pacing"))
_include_routes_by_name(layers.router, ("update_audio_clip",))
_include_routes_by_name(
    clips.router,
    (
        "update_clip_timing",
        "update_clip_text",
        "update_clip_shape",
        "add_keyframe",
        "delete_keyframe",
        "split_clip",
        "unlink_clip",
        "preview_diff",
    ),
)
_include_routes_by_name(capabilities.catch_all_router, ("v1_catch_all",))


def _find_allowed_methods(request_path: str) -> set[str]:
    return _find_allowed_methods_for_router(router, request_path)
