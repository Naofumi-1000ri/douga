"""AI Integration API Router.

Provides hierarchical data access endpoints optimized for AI assistants.
Follows L1 -> L2 -> L3 information hierarchy to minimize hallucination risk.

Endpoints:
- L1: Project overview (~300 tokens)
- L2: Timeline structure (~800 tokens)
- L3: Clip details (~400 tokens/clip)
- Write: Add/move/update/delete operations
- Semantic: High-level operations (snap, close gaps, etc.)
- Analysis: Gap and pacing analysis
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from src.api.deps import CurrentUser, DbSession
from src.models.project import Project
from src.schemas.ai import (
    AddAudioClipRequest,
    AddClipRequest,
    AddLayerRequest,
    AvailableSchemas,
    BatchOperationRequest,
    BatchOperationResult,
    ChatRequest,
    ChatResponse,
    GapAnalysisResult,
    L1ProjectOverview,
    L2AssetCatalog,
    L2TimelineAtTime,
    L2TimelineStructure,
    L3AudioClipDetails,
    L3ClipDetails,
    LayerSummary,
    MoveAudioClipRequest,
    MoveClipRequest,
    PacingAnalysisResult,
    ReorderLayersRequest,
    SchemaInfo,
    SemanticOperation,
    SemanticOperationResult,
    UpdateClipEffectsRequest,
    UpdateClipTransformRequest,
    UpdateLayerRequest,
)
from src.services.ai_service import AIService
from src.services.event_manager import event_manager

router = APIRouter()


# =============================================================================
# Helper: Get project with ownership check
# =============================================================================


async def get_user_project(
    project_id: UUID, current_user: CurrentUser, db: DbSession
) -> Project:
    """Get project with ownership verification."""
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


# =============================================================================
# Schema Discovery
# =============================================================================


@router.get("/schemas", response_model=AvailableSchemas)
async def list_schemas() -> AvailableSchemas:
    """List available AI schemas and their token budgets.

    AI assistants should call this first to understand available endpoints.
    """
    return AvailableSchemas(
        schemas=[
            # L1: Summary Level
            SchemaInfo(
                name="L1 Project Overview",
                description="High-level project summary with layer/track counts",
                level="L1",
                token_estimate="~300 tokens",
                endpoint="GET /api/ai/project/{id}/overview",
            ),
            # L2: Structure Level
            SchemaInfo(
                name="L2 Timeline Structure",
                description="Layer and track organization with time coverage",
                level="L2",
                token_estimate="~800 tokens",
                endpoint="GET /api/ai/project/{id}/structure",
            ),
            SchemaInfo(
                name="L2 Timeline at Time",
                description="What's active at a specific timestamp",
                level="L2",
                token_estimate="~400 tokens",
                endpoint="GET /api/ai/project/{id}/at-time/{ms}",
            ),
            SchemaInfo(
                name="L2 Asset Catalog",
                description="Available assets with usage counts",
                level="L2",
                token_estimate="~50 tokens/asset",
                endpoint="GET /api/ai/project/{id}/assets",
            ),
            # L3: Details Level
            SchemaInfo(
                name="L3 Video Clip Details",
                description="Full video clip details with neighboring context",
                level="L3",
                token_estimate="~400 tokens/clip",
                endpoint="GET /api/ai/project/{id}/clip/{clip_id}",
            ),
            SchemaInfo(
                name="L3 Audio Clip Details",
                description="Full audio clip details with neighboring context",
                level="L3",
                token_estimate="~400 tokens/clip",
                endpoint="GET /api/ai/project/{id}/audio-clip/{clip_id}",
            ),
            # Write: Layers
            SchemaInfo(
                name="Add Layer",
                description="Create a new layer",
                level="write",
                token_estimate="N/A",
                endpoint="POST /api/ai/project/{id}/layers",
            ),
            SchemaInfo(
                name="Reorder Layers",
                description="Reorder layers by providing new order of layer IDs",
                level="write",
                token_estimate="N/A",
                endpoint="PUT /api/ai/project/{id}/layers/order",
            ),
            SchemaInfo(
                name="Update Layer",
                description="Update layer name, visibility, or locked status",
                level="write",
                token_estimate="N/A",
                endpoint="PATCH /api/ai/project/{id}/layer/{layer_id}",
            ),
            # Write: Video Clips
            SchemaInfo(
                name="Add Video Clip",
                description="Add a new video clip to a layer",
                level="write",
                token_estimate="N/A",
                endpoint="POST /api/ai/project/{id}/clips",
            ),
            SchemaInfo(
                name="Move Video Clip",
                description="Move a video clip to new position or layer",
                level="write",
                token_estimate="N/A",
                endpoint="PATCH /api/ai/project/{id}/clip/{clip_id}/move",
            ),
            SchemaInfo(
                name="Update Clip Transform",
                description="Update clip position, scale, rotation",
                level="write",
                token_estimate="N/A",
                endpoint="PATCH /api/ai/project/{id}/clip/{clip_id}/transform",
            ),
            SchemaInfo(
                name="Update Clip Effects",
                description="Update clip opacity, blend mode, chroma key",
                level="write",
                token_estimate="N/A",
                endpoint="PATCH /api/ai/project/{id}/clip/{clip_id}/effects",
            ),
            SchemaInfo(
                name="Delete Video Clip",
                description="Delete a video clip",
                level="write",
                token_estimate="N/A",
                endpoint="DELETE /api/ai/project/{id}/clip/{clip_id}",
            ),
            # Write: Audio Clips
            SchemaInfo(
                name="Add Audio Clip",
                description="Add a new audio clip to a track",
                level="write",
                token_estimate="N/A",
                endpoint="POST /api/ai/project/{id}/audio-clips",
            ),
            SchemaInfo(
                name="Move Audio Clip",
                description="Move an audio clip to new position or track",
                level="write",
                token_estimate="N/A",
                endpoint="PATCH /api/ai/project/{id}/audio-clip/{clip_id}/move",
            ),
            SchemaInfo(
                name="Delete Audio Clip",
                description="Delete an audio clip",
                level="write",
                token_estimate="N/A",
                endpoint="DELETE /api/ai/project/{id}/audio-clip/{clip_id}",
            ),
            # Batch Operations
            SchemaInfo(
                name="Batch Operations",
                description="Execute multiple clip operations in a single request",
                level="write",
                token_estimate="N/A",
                endpoint="POST /api/ai/project/{id}/batch",
            ),
            # Semantic Operations
            SchemaInfo(
                name="Semantic Operation",
                description="High-level operations: snap_to_previous, snap_to_next, close_gap, auto_duck_bgm, rename_layer",
                level="write",
                token_estimate="N/A",
                endpoint="POST /api/ai/project/{id}/semantic",
            ),
            # Analysis
            SchemaInfo(
                name="Gap Analysis",
                description="Find gaps in timeline",
                level="analysis",
                token_estimate="~50 tokens/gap",
                endpoint="GET /api/ai/project/{id}/analysis/gaps",
            ),
            SchemaInfo(
                name="Pacing Analysis",
                description="Analyze clip density and pacing",
                level="analysis",
                token_estimate="~100 tokens/segment",
                endpoint="GET /api/ai/project/{id}/analysis/pacing",
            ),
        ]
    )


# =============================================================================
# L1: Summary Level Endpoints
# =============================================================================


@router.get("/project/{project_id}/overview", response_model=L1ProjectOverview)
async def get_project_overview(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> L1ProjectOverview:
    """Get L1 project overview (~300 tokens).

    Start here to understand the project scope before diving deeper.
    Returns: project metadata, layer/track counts, total clips, assets used.
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)
    return await service.get_project_overview(project)


# =============================================================================
# L2: Structure Level Endpoints
# =============================================================================


@router.get("/project/{project_id}/structure", response_model=L2TimelineStructure)
async def get_timeline_structure(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> L2TimelineStructure:
    """Get L2 timeline structure (~800 tokens).

    Shows layer and track organization with time coverage.
    Use this to find which layer/track to work with before fetching clip details.
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)
    return await service.get_timeline_structure(project)


@router.get("/project/{project_id}/at-time/{time_ms}", response_model=L2TimelineAtTime)
async def get_timeline_at_time(
    project_id: UUID,
    time_ms: int,
    current_user: CurrentUser,
    db: DbSession,
) -> L2TimelineAtTime:
    """Get L2 timeline state at a specific time.

    Shows what clips are active at the given timestamp.
    Useful for understanding the current playhead position.
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)
    return await service.get_timeline_at_time(project, time_ms)


@router.get("/project/{project_id}/assets", response_model=L2AssetCatalog)
async def get_asset_catalog(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> L2AssetCatalog:
    """Get L2 asset catalog.

    Lists available assets with usage counts.
    Use to find asset IDs for adding new clips.
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)
    return await service.get_asset_catalog(project)


# =============================================================================
# L3: Details Level Endpoints
# =============================================================================


@router.get("/project/{project_id}/clip/{clip_id}", response_model=L3ClipDetails)
async def get_clip_details(
    project_id: UUID,
    clip_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> L3ClipDetails:
    """Get L3 video clip details (~400 tokens).

    Returns full clip properties including transform, effects, transitions.
    Also includes neighboring clips for context (previous/next with gap info).
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)
    details = await service.get_clip_details(project, clip_id)

    if details is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Clip not found: {clip_id}",
        )

    return details


@router.get(
    "/project/{project_id}/audio-clip/{clip_id}", response_model=L3AudioClipDetails
)
async def get_audio_clip_details(
    project_id: UUID,
    clip_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> L3AudioClipDetails:
    """Get L3 audio clip details.

    Returns full audio clip properties including volume, fades.
    Also includes neighboring clips for context.
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)
    details = await service.get_audio_clip_details(project, clip_id)

    if details is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audio clip not found: {clip_id}",
        )

    return details


# =============================================================================
# Write Operations: Layers
# =============================================================================


@router.post("/project/{project_id}/layers", response_model=LayerSummary, status_code=201)
async def add_layer(
    project_id: UUID,
    request: AddLayerRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> LayerSummary:
    """Add a new layer to the project.

    Args:
        name: Layer name
        type: Layer type (background, content, avatar, effects, text)
        insert_at: Insert position (0=top, None=bottom)
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    flag_modified(project, "timeline_data")
    result = await service.add_layer(
        project,
        name=request.name,
        layer_type=request.type,
        insert_at=request.insert_at,
    )

    # Publish event for SSE subscribers
    await event_manager.publish(
        project_id=project_id,
        event_type="timeline_updated",
        data={"source": "ai_api", "operation": "add_layer"},
    )

    return result


@router.put("/project/{project_id}/layers/order", response_model=list[LayerSummary])
async def reorder_layers(
    project_id: UUID,
    request: ReorderLayersRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> list[LayerSummary]:
    """Reorder layers by providing the new order of layer IDs."""
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    try:
        flag_modified(project, "timeline_data")
        result = await service.reorder_layers(project, request.layer_ids)

        # Publish event for SSE subscribers
        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_api", "operation": "reorder_layers"},
        )

        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.patch("/project/{project_id}/layer/{layer_id}", response_model=LayerSummary)
async def update_layer(
    project_id: UUID,
    layer_id: str,
    request: UpdateLayerRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> LayerSummary:
    """Update layer properties (name, visibility, locked status)."""
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    flag_modified(project, "timeline_data")
    result = await service.update_layer(
        project, layer_id,
        name=request.name,
        visible=request.visible,
        locked=request.locked,
    )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Layer not found: {layer_id}",
        )

    # Publish event for SSE subscribers
    await event_manager.publish(
        project_id=project_id,
        event_type="timeline_updated",
        data={"source": "ai_api", "operation": "update_layer"},
    )

    return result


# =============================================================================
# Write Operations: Video Clips
# =============================================================================


@router.post("/project/{project_id}/clips", response_model=L3ClipDetails, status_code=201)
async def add_clip(
    project_id: UUID,
    request: AddClipRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> L3ClipDetails:
    """Add a new video clip to a layer.

    Validates:
    - Layer exists
    - Asset exists (if provided)
    - No overlap with existing clips

    Returns the created clip with full details.
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    try:
        # Mark timeline_data as modified for SQLAlchemy to detect the change
        flag_modified(project, "timeline_data")
        result = await service.add_clip(project, request)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create clip",
            )

        # Publish event for SSE subscribers
        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_api", "operation": "add_clip"},
        )

        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.patch("/project/{project_id}/clip/{clip_id}/move", response_model=L3ClipDetails)
async def move_clip(
    project_id: UUID,
    clip_id: str,
    request: MoveClipRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> L3ClipDetails:
    """Move a video clip to a new position or layer.

    Validates:
    - Clip exists
    - Target layer exists (if changing layers)
    - No overlap at new position
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    try:
        flag_modified(project, "timeline_data")
        result = await service.move_clip(project, clip_id, request)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Clip not found: {clip_id}",
            )

        # Publish event for SSE subscribers
        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_api", "operation": "move_clip"},
        )

        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.patch(
    "/project/{project_id}/clip/{clip_id}/transform", response_model=L3ClipDetails
)
async def update_clip_transform(
    project_id: UUID,
    clip_id: str,
    request: UpdateClipTransformRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> L3ClipDetails:
    """Update clip transform properties (position, scale, rotation)."""
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    try:
        flag_modified(project, "timeline_data")
        result = await service.update_clip_transform(project, clip_id, request)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Clip not found: {clip_id}",
            )

        # Publish event for SSE subscribers
        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_api", "operation": "update_clip_transform"},
        )

        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.patch(
    "/project/{project_id}/clip/{clip_id}/effects", response_model=L3ClipDetails
)
async def update_clip_effects(
    project_id: UUID,
    clip_id: str,
    request: UpdateClipEffectsRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> L3ClipDetails:
    """Update clip effects (opacity, blend mode, chroma key)."""
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    try:
        flag_modified(project, "timeline_data")
        result = await service.update_clip_effects(project, clip_id, request)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Clip not found: {clip_id}",
            )

        # Publish event for SSE subscribers
        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_api", "operation": "update_clip_effects"},
        )

        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.delete(
    "/project/{project_id}/clip/{clip_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_clip(
    project_id: UUID,
    clip_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Delete a video clip."""
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    flag_modified(project, "timeline_data")
    deleted = await service.delete_clip(project, clip_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Clip not found: {clip_id}",
        )

    # Publish event for SSE subscribers
    await event_manager.publish(
        project_id=project_id,
        event_type="timeline_updated",
        data={"source": "ai_api", "operation": "delete_clip"},
    )


# =============================================================================
# Write Operations: Audio Clips
# =============================================================================


@router.post(
    "/project/{project_id}/audio-clips", response_model=L3AudioClipDetails, status_code=201
)
async def add_audio_clip(
    project_id: UUID,
    request: AddAudioClipRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> L3AudioClipDetails:
    """Add a new audio clip to a track.

    Validates:
    - Track exists
    - Asset exists
    - No overlap with existing clips
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    try:
        flag_modified(project, "timeline_data")
        result = await service.add_audio_clip(project, request)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create audio clip",
            )

        # Publish event for SSE subscribers
        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_api", "operation": "add_audio_clip"},
        )

        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.patch(
    "/project/{project_id}/audio-clip/{clip_id}/move", response_model=L3AudioClipDetails
)
async def move_audio_clip(
    project_id: UUID,
    clip_id: str,
    request: MoveAudioClipRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> L3AudioClipDetails:
    """Move an audio clip to a new position or track."""
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    try:
        flag_modified(project, "timeline_data")
        result = await service.move_audio_clip(project, clip_id, request)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Audio clip not found: {clip_id}",
            )

        # Publish event for SSE subscribers
        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_api", "operation": "move_audio_clip"},
        )

        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.delete(
    "/project/{project_id}/audio-clip/{clip_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_audio_clip(
    project_id: UUID,
    clip_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Delete an audio clip."""
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    flag_modified(project, "timeline_data")
    deleted = await service.delete_audio_clip(project, clip_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audio clip not found: {clip_id}",
        )

    # Publish event for SSE subscribers
    await event_manager.publish(
        project_id=project_id,
        event_type="timeline_updated",
        data={"source": "ai_api", "operation": "delete_audio_clip"},
    )


# =============================================================================
# Semantic Operations
# =============================================================================


@router.post("/project/{project_id}/semantic", response_model=SemanticOperationResult)
async def execute_semantic_operation(
    project_id: UUID,
    operation: SemanticOperation,
    current_user: CurrentUser,
    db: DbSession,
) -> SemanticOperationResult:
    """Execute a high-level semantic operation.

    Available operations:
    - snap_to_previous: Move clip to end of previous clip (requires target_clip_id)
    - snap_to_next: Move next clip to end of this clip (requires target_clip_id)
    - close_gap: Remove gaps in a layer (requires target_layer_id)
    - auto_duck_bgm: Enable BGM ducking (optional parameters: duck_to, attack_ms, release_ms)
    - rename_layer: Rename a layer (requires target_layer_id, parameters: {"name": "new name"})

    These operations are safer than raw edits as they include validation.
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    flag_modified(project, "timeline_data")
    result = await service.execute_semantic_operation(project, operation)

    # Publish event for SSE subscribers
    await event_manager.publish(
        project_id=project_id,
        event_type="timeline_updated",
        data={"source": "ai_api", "operation": f"semantic_{operation.operation}"},
    )

    return result


# =============================================================================
# Batch Operations
# =============================================================================


@router.post("/project/{project_id}/batch", response_model=BatchOperationResult)
async def execute_batch_operations(
    project_id: UUID,
    request: BatchOperationRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> BatchOperationResult:
    """Execute multiple clip operations in a single request.

    Supports:
    - add: Add new clips
    - move: Move existing clips
    - update_transform: Update clip transforms
    - update_effects: Update clip effects
    - delete: Delete clips

    Operations are executed in order. If one fails, others may still succeed.
    Returns detailed results for each operation.
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    flag_modified(project, "timeline_data")
    result = await service.execute_batch_operations(project, request.operations)

    # Publish event for SSE subscribers
    await event_manager.publish(
        project_id=project_id,
        event_type="timeline_updated",
        data={"source": "ai_api", "operation": "batch_operations"},
    )

    return result


# =============================================================================
# Analysis Endpoints
# =============================================================================


@router.get("/project/{project_id}/analysis/gaps", response_model=GapAnalysisResult)
async def analyze_gaps(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> GapAnalysisResult:
    """Analyze gaps in the timeline.

    Finds empty spaces between clips in all layers and tracks.
    Use this to identify where content is missing.
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)
    return await service.analyze_gaps(project)


@router.get("/project/{project_id}/analysis/pacing", response_model=PacingAnalysisResult)
async def analyze_pacing(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    segment_duration_ms: int = 30000,
) -> PacingAnalysisResult:
    """Analyze timeline pacing.

    Divides the timeline into segments and calculates clip density for each.
    Provides suggestions for improving pacing (e.g., segments with low density).

    Args:
        segment_duration_ms: Duration of each analysis segment (default: 30 seconds)
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)
    return await service.analyze_pacing(project, segment_duration_ms)


# =============================================================================
# Chat: Natural Language Instructions
# =============================================================================


@router.post("/project/{project_id}/chat", response_model=ChatResponse)
async def chat(
    project_id: UUID,
    request: ChatRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> ChatResponse:
    """Process a natural language instruction via AI.

    Interprets the user's message, determines the appropriate timeline operations,
    executes them, and returns a response with applied actions.

    Supports multiple AI providers: openai, gemini, anthropic.
    The provider can be specified in the request, or the default from settings is used.
    """
    project = await get_user_project(project_id, current_user, db)
    service = AIService(db)

    flag_modified(project, "timeline_data")
    result = await service.handle_chat(
        project,
        request.message,
        request.history,
        request.provider,
    )

    # Publish event for SSE subscribers if actions were applied
    if result.actions_applied:
        await event_manager.publish(
            project_id=project_id,
            event_type="timeline_updated",
            data={"source": "ai_api", "operation": "chat"},
        )

    return result
