"""AI Integration Service.

Provides hierarchical data access for AI assistants with minimal hallucination risk.
Follows L1 -> L2 -> L3 information hierarchy pattern.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.models.asset import Asset
from src.models.project import Project
from src.schemas.ai import (
    AddAudioClipRequest,
    AddClipRequest,
    AssetInfo,
    BatchClipOperation,
    BatchOperationResult,
    ClipAtTime,
    ClipNeighbor,
    ClipTiming,
    EffectsDetails,
    GapAnalysisResult,
    L1ProjectOverview,
    L2AssetCatalog,
    L2TimelineAtTime,
    L2TimelineStructure,
    L3AudioClipDetails,
    L3ClipDetails,
    LayerSummary,
    AudioTrackSummary,
    MoveAudioClipRequest,
    MoveClipRequest,
    PacingAnalysisResult,
    PacingSegment,
    ProjectSummary,
    SemanticOperation,
    SemanticOperationResult,
    TimelineGap,
    TimelineSummary,
    TimeRange,
    TransformDetails,
    TransitionDetails,
    UpdateClipEffectsRequest,
    UpdateClipTransformRequest,
)

logger = logging.getLogger(__name__)


class AIService:
    """Service for AI-optimized project data access."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # =========================================================================
    # L1: Summary Level
    # =========================================================================

    async def get_project_overview(
        self, project: Project
    ) -> L1ProjectOverview:
        """Get L1 project overview (~300 tokens).

        Provides high-level summary for AI to grasp project scope quickly.
        """
        timeline = project.timeline_data or {}
        layers = timeline.get("layers", [])
        audio_tracks = timeline.get("audio_tracks", [])

        # Count clips
        total_video_clips = sum(len(layer.get("clips", [])) for layer in layers)
        total_audio_clips = sum(len(track.get("clips", [])) for track in audio_tracks)

        # Count unique assets used
        asset_ids = set()
        for layer in layers:
            for clip in layer.get("clips", []):
                if clip.get("asset_id"):
                    asset_ids.add(clip["asset_id"])
        for track in audio_tracks:
            for clip in track.get("clips", []):
                if clip.get("asset_id"):
                    asset_ids.add(clip["asset_id"])

        return L1ProjectOverview(
            project=ProjectSummary(
                name=project.name,
                duration_ms=project.duration_ms,
                dimensions=f"{project.width}x{project.height}",
                fps=project.fps,
                status=project.status,
            ),
            summary=TimelineSummary(
                layer_count=len(layers),
                audio_track_count=len(audio_tracks),
                total_video_clips=total_video_clips,
                total_audio_clips=total_audio_clips,
                total_assets_used=len(asset_ids),
            ),
            last_modified=project.updated_at,
        )

    # =========================================================================
    # L2: Structure Level
    # =========================================================================

    async def get_timeline_structure(
        self, project: Project
    ) -> L2TimelineStructure:
        """Get L2 timeline structure (~800 tokens).

        Provides layer/track organization without individual clip details.
        """
        timeline = project.timeline_data or {}
        layers = timeline.get("layers", [])
        audio_tracks = timeline.get("audio_tracks", [])

        layer_summaries = []
        for layer in layers:
            clips = layer.get("clips", [])
            time_coverage = self._calculate_time_coverage(clips)

            layer_summaries.append(
                LayerSummary(
                    id=layer.get("id", ""),
                    name=layer.get("name", ""),
                    type=layer.get("type", "content"),
                    clip_count=len(clips),
                    time_coverage=time_coverage,
                    visible=layer.get("visible", True),
                    locked=layer.get("locked", False),
                )
            )

        audio_track_summaries = []
        for track in audio_tracks:
            clips = track.get("clips", [])
            time_coverage = self._calculate_time_coverage(clips)
            ducking = track.get("ducking", {})

            audio_track_summaries.append(
                AudioTrackSummary(
                    id=track.get("id", ""),
                    name=track.get("name", ""),
                    type=track.get("type", "narration"),
                    clip_count=len(clips),
                    time_coverage=time_coverage,
                    volume=track.get("volume", 1.0),
                    muted=track.get("muted", False),
                    ducking_enabled=ducking.get("enabled", False) if ducking else False,
                )
            )

        return L2TimelineStructure(
            project_id=project.id,
            duration_ms=project.duration_ms,
            layers=layer_summaries,
            audio_tracks=audio_track_summaries,
        )

    async def get_timeline_at_time(
        self, project: Project, time_ms: int
    ) -> L2TimelineAtTime:
        """Get L2 timeline state at specific time.

        Shows what's active at a given moment.
        """
        timeline = project.timeline_data or {}
        active_clips = []
        all_events = []  # Track all clip boundaries

        # Check video clips
        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                start_ms = clip.get("start_ms", 0)
                duration_ms = clip.get("duration_ms", 0)
                end_ms = start_ms + duration_ms

                all_events.extend([start_ms, end_ms])

                if start_ms <= time_ms < end_ms:
                    progress = ((time_ms - start_ms) / duration_ms * 100) if duration_ms > 0 else 0
                    active_clips.append(
                        ClipAtTime(
                            id=clip.get("id", ""),
                            type="video",
                            layer_or_track_id=layer.get("id", ""),
                            layer_or_track_name=layer.get("name", ""),
                            start_ms=start_ms,
                            end_ms=end_ms,
                            progress_percent=round(progress, 1),
                        )
                    )

        # Check audio clips
        for track in timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                start_ms = clip.get("start_ms", 0)
                duration_ms = clip.get("duration_ms", 0)
                end_ms = start_ms + duration_ms

                all_events.extend([start_ms, end_ms])

                if start_ms <= time_ms < end_ms:
                    progress = ((time_ms - start_ms) / duration_ms * 100) if duration_ms > 0 else 0
                    active_clips.append(
                        ClipAtTime(
                            id=clip.get("id", ""),
                            type="audio",
                            layer_or_track_id=track.get("id", ""),
                            layer_or_track_name=track.get("name", ""),
                            start_ms=start_ms,
                            end_ms=end_ms,
                            progress_percent=round(progress, 1),
                        )
                    )

        # Find next event after current time
        next_event_ms = None
        future_events = [e for e in all_events if e > time_ms]
        if future_events:
            next_event_ms = min(future_events)

        return L2TimelineAtTime(
            time_ms=time_ms,
            active_clips=active_clips,
            next_event_ms=next_event_ms,
        )

    async def get_asset_catalog(self, project: Project) -> L2AssetCatalog:
        """Get L2 asset catalog.

        Lists available assets with usage counts.
        """
        # Query assets for this project
        result = await self.db.execute(
            select(Asset)
            .where(Asset.project_id == project.id)
            .where(Asset.is_internal == False)  # noqa: E712
        )
        assets = result.scalars().all()

        # Count asset usage in timeline
        timeline = project.timeline_data or {}
        asset_usage: dict[str, int] = {}

        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                asset_id = clip.get("asset_id")
                if asset_id:
                    asset_usage[asset_id] = asset_usage.get(asset_id, 0) + 1

        for track in timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                asset_id = clip.get("asset_id")
                if asset_id:
                    asset_usage[asset_id] = asset_usage.get(asset_id, 0) + 1

        asset_infos = []
        for asset in assets:
            asset_infos.append(
                AssetInfo(
                    id=asset.id,
                    name=asset.name,
                    type=asset.type,
                    subtype=asset.subtype,
                    duration_ms=asset.duration_ms,
                    width=asset.width,
                    height=asset.height,
                    usage_count=asset_usage.get(str(asset.id), 0),
                )
            )

        return L2AssetCatalog(
            project_id=project.id,
            assets=asset_infos,
            total_count=len(asset_infos),
        )

    # =========================================================================
    # L3: Details Level
    # =========================================================================

    async def get_clip_details(
        self, project: Project, clip_id: str
    ) -> L3ClipDetails | None:
        """Get L3 clip details (~400 tokens).

        Provides full details for a single clip with neighboring context.
        """
        timeline = project.timeline_data or {}

        for layer in timeline.get("layers", []):
            clips = layer.get("clips", [])
            for i, clip in enumerate(clips):
                if clip.get("id") == clip_id:
                    # Found the clip
                    asset_name = None
                    if clip.get("asset_id"):
                        asset = await self._get_asset(clip["asset_id"])
                        if asset:
                            asset_name = asset.name

                    # Get neighbors
                    sorted_clips = sorted(clips, key=lambda c: c.get("start_ms", 0))
                    clip_index = next(
                        (i for i, c in enumerate(sorted_clips) if c.get("id") == clip_id),
                        None,
                    )

                    previous_clip = None
                    next_clip = None

                    if clip_index is not None:
                        if clip_index > 0:
                            prev = sorted_clips[clip_index - 1]
                            prev_end = prev.get("start_ms", 0) + prev.get("duration_ms", 0)
                            gap = clip.get("start_ms", 0) - prev_end
                            previous_clip = ClipNeighbor(
                                id=prev.get("id", ""),
                                start_ms=prev.get("start_ms", 0),
                                end_ms=prev_end,
                                gap_ms=max(0, gap),
                            )

                        if clip_index < len(sorted_clips) - 1:
                            nxt = sorted_clips[clip_index + 1]
                            clip_end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
                            gap = nxt.get("start_ms", 0) - clip_end
                            next_clip = ClipNeighbor(
                                id=nxt.get("id", ""),
                                start_ms=nxt.get("start_ms", 0),
                                end_ms=nxt.get("start_ms", 0) + nxt.get("duration_ms", 0),
                                gap_ms=max(0, gap),
                            )

                    # Build response
                    transform = clip.get("transform", {})
                    effects = clip.get("effects", {})
                    transition_in = clip.get("transition_in", {})
                    transition_out = clip.get("transition_out", {})
                    chroma = effects.get("chroma_key", {})

                    return L3ClipDetails(
                        id=clip.get("id", ""),
                        layer_id=layer.get("id", ""),
                        layer_name=layer.get("name", ""),
                        asset_id=clip.get("asset_id"),
                        asset_name=asset_name,
                        timing=ClipTiming(
                            start_ms=clip.get("start_ms", 0),
                            duration_ms=clip.get("duration_ms", 0),
                            end_ms=clip.get("start_ms", 0) + clip.get("duration_ms", 0),
                            in_point_ms=clip.get("in_point_ms", 0),
                            out_point_ms=clip.get("out_point_ms"),
                        ),
                        transform=TransformDetails(
                            x=transform.get("x", 0),
                            y=transform.get("y", 0),
                            width=transform.get("width"),
                            height=transform.get("height"),
                            scale=transform.get("scale", 1.0),
                            rotation=transform.get("rotation", 0),
                            anchor=transform.get("anchor", "center"),
                        ),
                        effects=EffectsDetails(
                            opacity=effects.get("opacity", 1.0),
                            blend_mode=effects.get("blend_mode", "normal"),
                            chroma_key_enabled=chroma.get("enabled", False) if chroma else False,
                            chroma_key_color=chroma.get("color") if chroma else None,
                        ),
                        transition_in=TransitionDetails(
                            type=transition_in.get("type", "none"),
                            duration_ms=transition_in.get("duration_ms", 0),
                        ),
                        transition_out=TransitionDetails(
                            type=transition_out.get("type", "none"),
                            duration_ms=transition_out.get("duration_ms", 0),
                        ),
                        text_content=clip.get("text_content"),
                        group_id=clip.get("group_id"),
                        previous_clip=previous_clip,
                        next_clip=next_clip,
                    )

        return None

    async def get_audio_clip_details(
        self, project: Project, clip_id: str
    ) -> L3AudioClipDetails | None:
        """Get L3 audio clip details."""
        timeline = project.timeline_data or {}

        for track in timeline.get("audio_tracks", []):
            clips = track.get("clips", [])
            for clip in clips:
                if clip.get("id") == clip_id:
                    asset_name = None
                    if clip.get("asset_id"):
                        asset = await self._get_asset(clip["asset_id"])
                        if asset:
                            asset_name = asset.name

                    # Get neighbors
                    sorted_clips = sorted(clips, key=lambda c: c.get("start_ms", 0))
                    clip_index = next(
                        (i for i, c in enumerate(sorted_clips) if c.get("id") == clip_id),
                        None,
                    )

                    previous_clip = None
                    next_clip = None

                    if clip_index is not None:
                        if clip_index > 0:
                            prev = sorted_clips[clip_index - 1]
                            prev_end = prev.get("start_ms", 0) + prev.get("duration_ms", 0)
                            gap = clip.get("start_ms", 0) - prev_end
                            previous_clip = ClipNeighbor(
                                id=prev.get("id", ""),
                                start_ms=prev.get("start_ms", 0),
                                end_ms=prev_end,
                                gap_ms=max(0, gap),
                            )

                        if clip_index < len(sorted_clips) - 1:
                            nxt = sorted_clips[clip_index + 1]
                            clip_end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
                            gap = nxt.get("start_ms", 0) - clip_end
                            next_clip = ClipNeighbor(
                                id=nxt.get("id", ""),
                                start_ms=nxt.get("start_ms", 0),
                                end_ms=nxt.get("start_ms", 0) + nxt.get("duration_ms", 0),
                                gap_ms=max(0, gap),
                            )

                    return L3AudioClipDetails(
                        id=clip.get("id", ""),
                        track_id=track.get("id", ""),
                        track_name=track.get("name", ""),
                        asset_id=clip.get("asset_id"),
                        asset_name=asset_name,
                        timing=ClipTiming(
                            start_ms=clip.get("start_ms", 0),
                            duration_ms=clip.get("duration_ms", 0),
                            end_ms=clip.get("start_ms", 0) + clip.get("duration_ms", 0),
                            in_point_ms=clip.get("in_point_ms", 0),
                            out_point_ms=clip.get("out_point_ms"),
                        ),
                        volume=clip.get("volume", 1.0),
                        fade_in_ms=clip.get("fade_in_ms", 0),
                        fade_out_ms=clip.get("fade_out_ms", 0),
                        group_id=clip.get("group_id"),
                        previous_clip=previous_clip,
                        next_clip=next_clip,
                    )

        return None

    # =========================================================================
    # Write Operations
    # =========================================================================

    async def add_clip(
        self, project: Project, request: AddClipRequest
    ) -> L3ClipDetails | None:
        """Add a new video clip to a layer."""
        timeline = project.timeline_data or {}

        # Find the target layer
        layer = None
        for l in timeline.get("layers", []):
            if l.get("id") == request.layer_id:
                layer = l
                break

        if layer is None:
            raise ValueError(f"Layer not found: {request.layer_id}")

        # Validate asset and timing if provided
        if request.asset_id:
            await self._validate_clip_timing(
                str(request.asset_id),
                request.in_point_ms,
                request.out_point_ms,
                request.duration_ms,
            )

        # Check for overlaps
        if self._check_overlap(layer.get("clips", []), request.start_ms, request.duration_ms):
            raise ValueError("Clip would overlap with existing clip")

        # Create new clip
        new_clip_id = str(uuid.uuid4())
        new_clip: dict[str, Any] = {
            "id": new_clip_id,
            "asset_id": str(request.asset_id) if request.asset_id else None,
            "start_ms": request.start_ms,
            "duration_ms": request.duration_ms,
            "in_point_ms": request.in_point_ms,
            "out_point_ms": request.out_point_ms,
            "transform": {
                "x": request.x or 0,
                "y": request.y or 0,
                "scale": request.scale or 1.0,
                "rotation": 0,
                "anchor": "center",
            },
            "effects": {
                "opacity": 1.0,
                "blend_mode": "normal",
            },
            "transition_in": {"type": "none", "duration_ms": 0},
            "transition_out": {"type": "none", "duration_ms": 0},
        }

        if request.text_content:
            new_clip["text_content"] = request.text_content
            new_clip["text_style"] = request.text_style or {}

        if request.group_id:
            new_clip["group_id"] = request.group_id

        # Add to layer
        if "clips" not in layer:
            layer["clips"] = []
        layer["clips"].append(new_clip)

        # Update project duration
        self._update_project_duration(project)

        # Mark as modified
        await self.db.flush()

        return await self.get_clip_details(project, new_clip_id)

    async def add_audio_clip(
        self, project: Project, request: AddAudioClipRequest
    ) -> L3AudioClipDetails | None:
        """Add a new audio clip to a track."""
        timeline = project.timeline_data or {}

        # Find the target track
        track = None
        for t in timeline.get("audio_tracks", []):
            if t.get("id") == request.track_id:
                track = t
                break

        if track is None:
            raise ValueError(f"Track not found: {request.track_id}")

        # Validate asset and timing
        await self._validate_clip_timing(
            str(request.asset_id),
            request.in_point_ms,
            request.out_point_ms,
            request.duration_ms,
        )

        # Check for overlaps
        if self._check_overlap(track.get("clips", []), request.start_ms, request.duration_ms):
            raise ValueError("Clip would overlap with existing clip")

        # Create new clip
        new_clip_id = str(uuid.uuid4())
        new_clip: dict[str, Any] = {
            "id": new_clip_id,
            "asset_id": str(request.asset_id),
            "start_ms": request.start_ms,
            "duration_ms": request.duration_ms,
            "in_point_ms": request.in_point_ms,
            "out_point_ms": request.out_point_ms,
            "volume": request.volume,
            "fade_in_ms": request.fade_in_ms,
            "fade_out_ms": request.fade_out_ms,
        }

        if request.group_id:
            new_clip["group_id"] = request.group_id

        # Add to track
        if "clips" not in track:
            track["clips"] = []
        track["clips"].append(new_clip)

        # Update project duration
        self._update_project_duration(project)

        await self.db.flush()

        return await self.get_audio_clip_details(project, new_clip_id)

    async def move_clip(
        self, project: Project, clip_id: str, request: MoveClipRequest
    ) -> L3ClipDetails | None:
        """Move a video clip to a new position or layer."""
        timeline = project.timeline_data or {}
        clip_data = None
        source_layer = None

        # Find the clip
        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                if clip.get("id") == clip_id:
                    clip_data = clip
                    source_layer = layer
                    break
            if clip_data:
                break

        if clip_data is None:
            raise ValueError(f"Clip not found: {clip_id}")

        # Determine target layer
        target_layer = source_layer
        if request.new_layer_id and request.new_layer_id != source_layer.get("id"):
            target_layer = None
            for layer in timeline.get("layers", []):
                if layer.get("id") == request.new_layer_id:
                    target_layer = layer
                    break
            if target_layer is None:
                raise ValueError(f"Target layer not found: {request.new_layer_id}")

        # Check for overlaps in target layer (excluding self)
        other_clips = [c for c in target_layer.get("clips", []) if c.get("id") != clip_id]
        if self._check_overlap(other_clips, request.new_start_ms, clip_data.get("duration_ms", 0)):
            raise ValueError("Move would cause overlap")

        # Move the clip
        if target_layer != source_layer:
            source_layer["clips"].remove(clip_data)
            if "clips" not in target_layer:
                target_layer["clips"] = []
            target_layer["clips"].append(clip_data)

        clip_data["start_ms"] = request.new_start_ms

        # Update project duration
        self._update_project_duration(project)

        await self.db.flush()

        return await self.get_clip_details(project, clip_id)

    async def move_audio_clip(
        self, project: Project, clip_id: str, request: MoveAudioClipRequest
    ) -> L3AudioClipDetails | None:
        """Move an audio clip to a new position or track."""
        timeline = project.timeline_data or {}
        clip_data = None
        source_track = None

        # Find the clip
        for track in timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                if clip.get("id") == clip_id:
                    clip_data = clip
                    source_track = track
                    break
            if clip_data:
                break

        if clip_data is None:
            raise ValueError(f"Audio clip not found: {clip_id}")

        # Determine target track
        target_track = source_track
        if request.new_track_id and request.new_track_id != source_track.get("id"):
            target_track = None
            for track in timeline.get("audio_tracks", []):
                if track.get("id") == request.new_track_id:
                    target_track = track
                    break
            if target_track is None:
                raise ValueError(f"Target track not found: {request.new_track_id}")

        # Check for overlaps (excluding self)
        other_clips = [c for c in target_track.get("clips", []) if c.get("id") != clip_id]
        if self._check_overlap(other_clips, request.new_start_ms, clip_data.get("duration_ms", 0)):
            raise ValueError("Move would cause overlap")

        # Move the clip
        if target_track != source_track:
            source_track["clips"].remove(clip_data)
            if "clips" not in target_track:
                target_track["clips"] = []
            target_track["clips"].append(clip_data)

        clip_data["start_ms"] = request.new_start_ms

        # Update project duration
        self._update_project_duration(project)

        await self.db.flush()

        return await self.get_audio_clip_details(project, clip_id)

    async def update_clip_transform(
        self, project: Project, clip_id: str, request: UpdateClipTransformRequest
    ) -> L3ClipDetails | None:
        """Update clip transform properties."""
        timeline = project.timeline_data or {}

        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                if clip.get("id") == clip_id:
                    if "transform" not in clip:
                        clip["transform"] = {}

                    if request.x is not None:
                        clip["transform"]["x"] = request.x
                    if request.y is not None:
                        clip["transform"]["y"] = request.y
                    if request.width is not None:
                        clip["transform"]["width"] = request.width
                    if request.height is not None:
                        clip["transform"]["height"] = request.height
                    if request.scale is not None:
                        clip["transform"]["scale"] = request.scale
                    if request.rotation is not None:
                        clip["transform"]["rotation"] = request.rotation
                    if request.anchor is not None:
                        clip["transform"]["anchor"] = request.anchor

                    flag_modified(project, "timeline_data")
                    await self.db.flush()
                    return await self.get_clip_details(project, clip_id)

        raise ValueError(f"Clip not found: {clip_id}")

    async def update_clip_effects(
        self, project: Project, clip_id: str, request: UpdateClipEffectsRequest
    ) -> L3ClipDetails | None:
        """Update clip effects properties."""
        timeline = project.timeline_data or {}

        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                if clip.get("id") == clip_id:
                    if "effects" not in clip:
                        clip["effects"] = {}

                    if request.opacity is not None:
                        clip["effects"]["opacity"] = request.opacity
                    if request.blend_mode is not None:
                        clip["effects"]["blend_mode"] = request.blend_mode

                    if request.chroma_key_enabled is not None:
                        if "chroma_key" not in clip["effects"]:
                            clip["effects"]["chroma_key"] = {}
                        clip["effects"]["chroma_key"]["enabled"] = request.chroma_key_enabled

                    if request.chroma_key_color is not None:
                        if "chroma_key" not in clip["effects"]:
                            clip["effects"]["chroma_key"] = {}
                        clip["effects"]["chroma_key"]["color"] = request.chroma_key_color

                    if request.chroma_key_similarity is not None:
                        if "chroma_key" not in clip["effects"]:
                            clip["effects"]["chroma_key"] = {}
                        clip["effects"]["chroma_key"]["similarity"] = request.chroma_key_similarity

                    if request.chroma_key_blend is not None:
                        if "chroma_key" not in clip["effects"]:
                            clip["effects"]["chroma_key"] = {}
                        clip["effects"]["chroma_key"]["blend"] = request.chroma_key_blend

                    flag_modified(project, "timeline_data")
                    await self.db.flush()
                    return await self.get_clip_details(project, clip_id)

        raise ValueError(f"Clip not found: {clip_id}")

    async def delete_clip(self, project: Project, clip_id: str) -> bool:
        """Delete a video clip."""
        timeline = project.timeline_data or {}

        for layer in timeline.get("layers", []):
            clips = layer.get("clips", [])
            for i, clip in enumerate(clips):
                if clip.get("id") == clip_id:
                    clips.pop(i)
                    self._update_project_duration(project)
                    await self.db.flush()
                    return True

        return False

    async def delete_audio_clip(self, project: Project, clip_id: str) -> bool:
        """Delete an audio clip."""
        timeline = project.timeline_data or {}

        for track in timeline.get("audio_tracks", []):
            clips = track.get("clips", [])
            for i, clip in enumerate(clips):
                if clip.get("id") == clip_id:
                    clips.pop(i)
                    self._update_project_duration(project)
                    await self.db.flush()
                    return True

        return False

    async def add_layer(
        self,
        project: Project,
        name: str,
        layer_type: str = "content",
        insert_at: int | None = None,
    ) -> LayerSummary:
        """Add a new layer to the project."""
        import uuid as uuid_module

        timeline = project.timeline_data or {}
        if "layers" not in timeline:
            timeline["layers"] = []

        new_layer = {
            "id": str(uuid_module.uuid4()),
            "name": name,
            "type": layer_type,
            "clips": [],
            "visible": True,
            "locked": False,
        }

        if insert_at is not None and 0 <= insert_at <= len(timeline["layers"]):
            timeline["layers"].insert(insert_at, new_layer)
        else:
            timeline["layers"].append(new_layer)

        project.timeline_data = timeline
        flag_modified(project, "timeline_data")
        await self.db.flush()

        return LayerSummary(
            id=new_layer["id"],
            name=new_layer["name"],
            type=new_layer["type"],
            clip_count=0,
            time_coverage=[],
            visible=True,
            locked=False,
        )

    async def reorder_layers(
        self, project: Project, layer_ids: list[str]
    ) -> list[LayerSummary]:
        """Reorder layers by providing the new order of layer IDs."""
        timeline = project.timeline_data or {}
        layers = timeline.get("layers", [])

        # Build a map of layer_id -> layer
        layer_map = {layer.get("id"): layer for layer in layers}

        # Validate all layer_ids exist
        for layer_id in layer_ids:
            if layer_id not in layer_map:
                raise ValueError(f"Layer not found: {layer_id}")

        # Reorder layers
        new_layers = [layer_map[layer_id] for layer_id in layer_ids]

        # Add any layers not in the provided list to the end
        for layer in layers:
            if layer.get("id") not in layer_ids:
                new_layers.append(layer)

        timeline["layers"] = new_layers
        project.timeline_data = timeline
        flag_modified(project, "timeline_data")
        await self.db.flush()

        # Return updated layer summaries
        result = []
        for layer in new_layers:
            clips = layer.get("clips", [])
            time_coverage = self._calculate_time_coverage(clips)
            result.append(
                LayerSummary(
                    id=layer.get("id", ""),
                    name=layer.get("name", ""),
                    type=layer.get("type", "content"),
                    clip_count=len(clips),
                    time_coverage=time_coverage,
                    visible=layer.get("visible", True),
                    locked=layer.get("locked", False),
                )
            )
        return result

    async def update_layer(
        self, project: Project, layer_id: str, name: str | None = None,
        visible: bool | None = None, locked: bool | None = None
    ) -> LayerSummary | None:
        """Update layer properties."""
        timeline = project.timeline_data or {}

        for layer in timeline.get("layers", []):
            if layer.get("id") == layer_id:
                if name is not None:
                    layer["name"] = name
                if visible is not None:
                    layer["visible"] = visible
                if locked is not None:
                    layer["locked"] = locked

                flag_modified(project, "timeline_data")
                await self.db.flush()

                # Return updated layer summary
                clips = layer.get("clips", [])
                time_coverage = self._calculate_time_coverage(clips)
                return LayerSummary(
                    id=layer.get("id", ""),
                    name=layer.get("name", ""),
                    type=layer.get("type", "content"),
                    clip_count=len(clips),
                    time_coverage=time_coverage,
                    visible=layer.get("visible", True),
                    locked=layer.get("locked", False),
                )

        return None

    # =========================================================================
    # Semantic Operations
    # =========================================================================

    async def execute_semantic_operation(
        self, project: Project, operation: SemanticOperation
    ) -> SemanticOperationResult:
        """Execute a high-level semantic operation."""
        try:
            if operation.operation == "snap_to_previous":
                return await self._snap_to_previous(project, operation)
            elif operation.operation == "snap_to_next":
                return await self._snap_to_next(project, operation)
            elif operation.operation == "close_gap":
                return await self._close_gap(project, operation)
            elif operation.operation == "auto_duck_bgm":
                return await self._auto_duck_bgm(project, operation)
            else:
                return SemanticOperationResult(
                    success=False,
                    operation=operation.operation,
                    error_message=f"Unknown operation: {operation.operation}",
                )
        except Exception as e:
            logger.exception(f"Semantic operation failed: {operation.operation}")
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message=str(e),
            )

    async def _snap_to_previous(
        self, project: Project, operation: SemanticOperation
    ) -> SemanticOperationResult:
        """Snap a clip to the end of the previous clip."""
        if not operation.target_clip_id:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="target_clip_id required",
            )

        timeline = project.timeline_data or {}

        # Find the clip and its neighbors
        for layer in timeline.get("layers", []):
            clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
            for i, clip in enumerate(clips):
                if clip.get("id") == operation.target_clip_id:
                    if i == 0:
                        return SemanticOperationResult(
                            success=False,
                            operation=operation.operation,
                            error_message="No previous clip to snap to",
                        )

                    prev_clip = clips[i - 1]
                    prev_end = prev_clip.get("start_ms", 0) + prev_clip.get("duration_ms", 0)
                    old_start = clip.get("start_ms", 0)

                    clip["start_ms"] = prev_end
                    self._update_project_duration(project)
                    await self.db.flush()

                    return SemanticOperationResult(
                        success=True,
                        operation=operation.operation,
                        changes_made=[
                            f"Moved clip from {old_start}ms to {prev_end}ms (snapped to previous)"
                        ],
                        affected_clip_ids=[operation.target_clip_id],
                    )

        return SemanticOperationResult(
            success=False,
            operation=operation.operation,
            error_message=f"Clip not found: {operation.target_clip_id}",
        )

    async def _snap_to_next(
        self, project: Project, operation: SemanticOperation
    ) -> SemanticOperationResult:
        """Snap the next clip to the end of this clip."""
        if not operation.target_clip_id:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="target_clip_id required",
            )

        timeline = project.timeline_data or {}

        for layer in timeline.get("layers", []):
            clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
            for i, clip in enumerate(clips):
                if clip.get("id") == operation.target_clip_id:
                    if i >= len(clips) - 1:
                        return SemanticOperationResult(
                            success=False,
                            operation=operation.operation,
                            error_message="No next clip to snap",
                        )

                    next_clip = clips[i + 1]
                    clip_end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
                    old_start = next_clip.get("start_ms", 0)

                    next_clip["start_ms"] = clip_end
                    self._update_project_duration(project)
                    await self.db.flush()

                    return SemanticOperationResult(
                        success=True,
                        operation=operation.operation,
                        changes_made=[
                            f"Moved next clip from {old_start}ms to {clip_end}ms (snapped to this clip)"
                        ],
                        affected_clip_ids=[next_clip.get("id", "")],
                    )

        return SemanticOperationResult(
            success=False,
            operation=operation.operation,
            error_message=f"Clip not found: {operation.target_clip_id}",
        )

    async def _close_gap(
        self, project: Project, operation: SemanticOperation
    ) -> SemanticOperationResult:
        """Close gaps in a layer by shifting clips forward."""
        target_layer_id = operation.target_layer_id
        if not target_layer_id:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="target_layer_id required",
            )

        timeline = project.timeline_data or {}

        for layer in timeline.get("layers", []):
            if layer.get("id") == target_layer_id:
                clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
                changes = []
                affected_ids = []
                current_end = 0

                for clip in clips:
                    old_start = clip.get("start_ms", 0)
                    if old_start > current_end:
                        clip["start_ms"] = current_end
                        changes.append(f"Moved clip {clip.get('id', '')[:8]}... from {old_start}ms to {current_end}ms")
                        affected_ids.append(clip.get("id", ""))

                    current_end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)

                if changes:
                    self._update_project_duration(project)
                    await self.db.flush()

                return SemanticOperationResult(
                    success=True,
                    operation=operation.operation,
                    changes_made=changes if changes else ["No gaps found"],
                    affected_clip_ids=affected_ids,
                )

        return SemanticOperationResult(
            success=False,
            operation=operation.operation,
            error_message=f"Layer not found: {target_layer_id}",
        )

    async def _auto_duck_bgm(
        self, project: Project, operation: SemanticOperation
    ) -> SemanticOperationResult:
        """Enable BGM ducking when narration is playing."""
        timeline = project.timeline_data or {}
        changes = []

        # Find BGM track
        for track in timeline.get("audio_tracks", []):
            if track.get("type") == "bgm":
                if "ducking" not in track:
                    track["ducking"] = {}

                track["ducking"]["enabled"] = True
                track["ducking"]["duck_to"] = operation.parameters.get("duck_to", 0.1)
                track["ducking"]["attack_ms"] = operation.parameters.get("attack_ms", 200)
                track["ducking"]["release_ms"] = operation.parameters.get("release_ms", 500)
                track["ducking"]["trigger_track"] = "narration"

                changes.append(f"Enabled ducking on {track.get('name', 'BGM')} track")

        if changes:
            flag_modified(project, "timeline_data")
            await self.db.flush()
            return SemanticOperationResult(
                success=True,
                operation=operation.operation,
                changes_made=changes,
            )

        return SemanticOperationResult(
            success=False,
            operation=operation.operation,
            error_message="No BGM track found",
        )

    # =========================================================================
    # Batch Operations
    # =========================================================================

    async def execute_batch_operations(
        self, project: Project, operations: list[BatchClipOperation]
    ) -> BatchOperationResult:
        """Execute multiple clip operations in a batch."""
        results = []
        errors = []
        successful = 0

        for op in operations:
            try:
                if op.operation == "add":
                    if op.clip_type == "video":
                        req = AddClipRequest(**op.data)
                        result = await self.add_clip(project, req)
                    else:
                        req = AddAudioClipRequest(**op.data)
                        result = await self.add_audio_clip(project, req)
                    results.append({"operation": "add", "clip_id": result.id if result else None})
                    successful += 1

                elif op.operation == "move":
                    if not op.clip_id:
                        raise ValueError("clip_id required for move operation")
                    if op.clip_type == "video":
                        req = MoveClipRequest(**op.data)
                        await self.move_clip(project, op.clip_id, req)
                    else:
                        req = MoveAudioClipRequest(**op.data)
                        await self.move_audio_clip(project, op.clip_id, req)
                    results.append({"operation": "move", "clip_id": op.clip_id})
                    successful += 1

                elif op.operation == "update_transform":
                    if not op.clip_id:
                        raise ValueError("clip_id required for update_transform")
                    req = UpdateClipTransformRequest(**op.data)
                    await self.update_clip_transform(project, op.clip_id, req)
                    results.append({"operation": "update_transform", "clip_id": op.clip_id})
                    successful += 1

                elif op.operation == "update_effects":
                    if not op.clip_id:
                        raise ValueError("clip_id required for update_effects")
                    req = UpdateClipEffectsRequest(**op.data)
                    await self.update_clip_effects(project, op.clip_id, req)
                    results.append({"operation": "update_effects", "clip_id": op.clip_id})
                    successful += 1

                elif op.operation == "delete":
                    if not op.clip_id:
                        raise ValueError("clip_id required for delete operation")
                    if op.clip_type == "video":
                        await self.delete_clip(project, op.clip_id)
                    else:
                        await self.delete_audio_clip(project, op.clip_id)
                    results.append({"operation": "delete", "clip_id": op.clip_id})
                    successful += 1

            except Exception as e:
                errors.append(f"Operation {op.operation} failed: {str(e)}")
                results.append({"operation": op.operation, "error": str(e)})

        return BatchOperationResult(
            success=len(errors) == 0,
            total_operations=len(operations),
            successful_operations=successful,
            failed_operations=len(errors),
            results=results,
            errors=errors,
        )

    # =========================================================================
    # Analysis Tools
    # =========================================================================

    async def analyze_gaps(self, project: Project) -> GapAnalysisResult:
        """Find gaps in the timeline."""
        timeline = project.timeline_data or {}
        gaps: list[TimelineGap] = []

        # Analyze video layers
        for layer in timeline.get("layers", []):
            clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
            current_end = 0

            for clip in clips:
                start = clip.get("start_ms", 0)
                if start > current_end:
                    gaps.append(
                        TimelineGap(
                            layer_or_track_id=layer.get("id", ""),
                            layer_or_track_name=layer.get("name", ""),
                            type="video",
                            start_ms=current_end,
                            end_ms=start,
                            duration_ms=start - current_end,
                        )
                    )
                current_end = max(current_end, start + clip.get("duration_ms", 0))

        # Analyze audio tracks
        for track in timeline.get("audio_tracks", []):
            clips = sorted(track.get("clips", []), key=lambda c: c.get("start_ms", 0))
            current_end = 0

            for clip in clips:
                start = clip.get("start_ms", 0)
                if start > current_end:
                    gaps.append(
                        TimelineGap(
                            layer_or_track_id=track.get("id", ""),
                            layer_or_track_name=track.get("name", ""),
                            type="audio",
                            start_ms=current_end,
                            end_ms=start,
                            duration_ms=start - current_end,
                        )
                    )
                current_end = max(current_end, start + clip.get("duration_ms", 0))

        total_gap_duration = sum(g.duration_ms for g in gaps)

        return GapAnalysisResult(
            total_gaps=len(gaps),
            total_gap_duration_ms=total_gap_duration,
            gaps=gaps,
        )

    async def analyze_pacing(
        self, project: Project, segment_duration_ms: int = 30000
    ) -> PacingAnalysisResult:
        """Analyze timeline pacing (clip density over time)."""
        timeline = project.timeline_data or {}
        duration = project.duration_ms

        if duration == 0:
            return PacingAnalysisResult(
                overall_avg_clip_duration_ms=0,
                segments=[],
                suggested_improvements=[],
            )

        # Collect all clip durations
        all_durations = []
        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                all_durations.append(clip.get("duration_ms", 0))

        overall_avg = sum(all_durations) / len(all_durations) if all_durations else 0

        # Analyze segments
        segments = []
        for seg_start in range(0, duration, segment_duration_ms):
            seg_end = min(seg_start + segment_duration_ms, duration)
            seg_clips = []

            for layer in timeline.get("layers", []):
                for clip in layer.get("clips", []):
                    clip_start = clip.get("start_ms", 0)
                    clip_end = clip_start + clip.get("duration_ms", 0)
                    # Check if clip overlaps with segment
                    if clip_start < seg_end and clip_end > seg_start:
                        seg_clips.append(clip.get("duration_ms", 0))

            clip_count = len(seg_clips)
            avg_duration = sum(seg_clips) / clip_count if seg_clips else 0
            seg_duration_sec = (seg_end - seg_start) / 1000
            density = clip_count / seg_duration_sec if seg_duration_sec > 0 else 0

            segments.append(
                PacingSegment(
                    start_ms=seg_start,
                    end_ms=seg_end,
                    clip_count=clip_count,
                    avg_clip_duration_ms=round(avg_duration, 1),
                    density=round(density, 2),
                )
            )

        # Generate suggestions
        suggestions = []
        if segments:
            densities = [s.density for s in segments]
            avg_density = sum(densities) / len(densities)

            for seg in segments:
                if seg.density < avg_density * 0.5 and seg.clip_count > 0:
                    suggestions.append(
                        f"Segment {seg.start_ms // 1000}s-{seg.end_ms // 1000}s has low clip density"
                    )
                if seg.avg_clip_duration_ms > overall_avg * 2:
                    suggestions.append(
                        f"Segment {seg.start_ms // 1000}s-{seg.end_ms // 1000}s has long clips - consider splitting"
                    )

        return PacingAnalysisResult(
            overall_avg_clip_duration_ms=round(overall_avg, 1),
            segments=segments,
            suggested_improvements=suggestions,
        )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _calculate_time_coverage(self, clips: list[dict]) -> list[TimeRange]:
        """Calculate time ranges covered by clips."""
        if not clips:
            return []

        # Sort by start time
        sorted_clips = sorted(clips, key=lambda c: c.get("start_ms", 0))

        ranges = []
        current_start = None
        current_end = None

        for clip in sorted_clips:
            start = clip.get("start_ms", 0)
            end = start + clip.get("duration_ms", 0)

            if current_start is None:
                current_start = start
                current_end = end
            elif start <= current_end:
                # Overlapping or adjacent, extend current range
                current_end = max(current_end, end)
            else:
                # Gap, start new range
                ranges.append(TimeRange(start_ms=current_start, end_ms=current_end))
                current_start = start
                current_end = end

        if current_start is not None:
            ranges.append(TimeRange(start_ms=current_start, end_ms=current_end))

        return ranges

    def _check_overlap(
        self, clips: list[dict], new_start: int, new_duration: int
    ) -> bool:
        """Check if a new clip would overlap with existing clips."""
        new_end = new_start + new_duration

        for clip in clips:
            clip_start = clip.get("start_ms", 0)
            clip_end = clip_start + clip.get("duration_ms", 0)

            # Check for overlap
            if new_start < clip_end and new_end > clip_start:
                return True

        return False

    async def _get_asset(self, asset_id: str) -> Asset | None:
        """Get asset by ID."""
        try:
            asset_uuid = uuid.UUID(asset_id)
            result = await self.db.execute(select(Asset).where(Asset.id == asset_uuid))
            return result.scalar_one_or_none()
        except (ValueError, TypeError):
            return None

    async def _validate_clip_timing(
        self,
        asset_id: str | None,
        in_point_ms: int,
        out_point_ms: int | None,
        duration_ms: int,
    ) -> None:
        """Validate clip timing against asset duration.

        Raises ValueError if timing is invalid.
        """
        if asset_id is None:
            # Text clips don't have an asset
            return

        asset = await self._get_asset(asset_id)
        if asset is None:
            raise ValueError(f"Asset not found: {asset_id}")

        # Validate against asset duration if known
        if asset.duration_ms:
            effective_out = out_point_ms if out_point_ms is not None else asset.duration_ms

            if effective_out > asset.duration_ms:
                raise ValueError(
                    f"out_point_ms ({effective_out}) exceeds asset duration ({asset.duration_ms})"
                )

            if in_point_ms >= effective_out:
                raise ValueError(
                    f"in_point_ms ({in_point_ms}) must be less than out_point_ms ({effective_out})"
                )

            # Check if requested duration is valid (cannot exceed available content)
            available_duration = effective_out - in_point_ms
            if duration_ms > available_duration:
                logger.warning(
                    f"Requested duration ({duration_ms}) exceeds available content "
                    f"({available_duration}), using available content"
                )

    def _update_project_duration(self, project: Project) -> None:
        """Update project duration based on timeline content."""
        timeline = project.timeline_data or {}
        max_end = 0

        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
                max_end = max(max_end, end)

        for track in timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
                max_end = max(max_end, end)

        project.duration_ms = max_end
        timeline["duration_ms"] = max_end
        # Mark JSONB field as modified for SQLAlchemy to detect in-place changes
        flag_modified(project, "timeline_data")
