"""AI Integration Service.

Provides hierarchical data access for AI assistants with minimal hallucination risk.
Follows L1 -> L2 -> L3 information hierarchy pattern.
"""

import json
import logging
import re
import uuid
from typing import Any

import httpx

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.config import get_settings
from src.exceptions import (
    AssetNotFoundError,
    AudioClipNotFoundError,
    AudioTrackNotFoundError,
    ClipNotFoundError,
    InvalidClipTypeError,
    InvalidTimeRangeError,
    KeyframeNotFoundError,
    LayerNotFoundError,
    MarkerNotFoundError,
    MissingRequiredFieldError,
)
from src.models.asset import Asset
from src.models.project import Project
from src.schemas.ai import (
    AddAudioClipRequest,
    AddClipRequest,
    AddKeyframeRequest,
    AddMarkerRequest,
    AssetInfo,
    BatchClipOperation,
    BatchOperationResult,
    ChatAction,
    ChatMessage,
    ChatResponse,
    ClipAtTime,
    ClipNeighbor,
    ClipTiming,
    CropDetails,
    EffectsDetails,
    TextStyleDetails,
    GapAnalysisResult,
    L1ProjectOverview,
    L2AssetCatalog,
    L2TimelineAtTime,
    L2TimelineStructure,
    L25TimelineOverview,
    L3AudioClipDetails,
    L3ClipDetails,
    LayerSummary,
    AudioTrackSummary,
    MoveAudioClipRequest,
    MoveClipRequest,
    OverviewAudioTrack,
    OverviewClip,
    OverviewGap,
    OverviewLayer,
    OverviewOverlap,
    PacingAnalysisResult,
    PacingSegment,
    PreviewDiffRequest,
    ProjectSummary,
    SemanticOperation,
    SemanticOperationResult,
    TimelineGap,
    TimelineSummary,
    TimeRange,
    TransformDetails,
    TransitionDetails,
    UpdateAudioClipRequest,
    VolumeKeyframeResponse,
    UpdateClipCropRequest,
    UpdateClipEffectsRequest,
    UpdateClipShapeRequest,
    UpdateClipTextRequest,
    UpdateClipTextStyleRequest,
    UpdateClipTimingRequest,
    UpdateClipTransformRequest,
    UpdateMarkerRequest,
)
from src.schemas.clip_adapter import UnifiedClipInput, UnifiedTransformInput

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

        # Build linked audio lookup: video_asset_id → audio Asset
        # We need the full Asset object to read asset_metadata (classification/transcription).
        linked_audio_result = await self.db.execute(
            select(Asset)
            .where(
                Asset.project_id == project.id,
                Asset.source_asset_id.isnot(None),
                Asset.type == "audio",
            )
        )
        linked_audio_map: dict[str, Asset] = {}
        for audio_asset in linked_audio_result.scalars().all():
            linked_audio_map[str(audio_asset.source_asset_id)] = audio_asset

        asset_infos = []
        for asset in assets:
            # For video assets, audio classification and transcription data live
            # on the linked internal audio asset (created by _auto_extract_audio_background).
            # For audio assets, the data lives on the asset itself.
            linked_audio = linked_audio_map.get(str(asset.id))
            if linked_audio is not None:
                audio_meta = linked_audio.asset_metadata or {}
            else:
                audio_meta = asset.asset_metadata or {}

            audio_classification = audio_meta.get("audio_classification")
            has_transcription = "transcription" in audio_meta

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
                    linked_audio_id=linked_audio.id if linked_audio else None,
                    audio_classification=audio_classification,
                    has_transcription=has_transcription,
                )
            )

        return L2AssetCatalog(
            project_id=project.id,
            assets=asset_infos,
            total_count=len(asset_infos),
        )

    # =========================================================================
    # L2.5: Timeline Overview
    # =========================================================================

    async def get_timeline_overview(
        self, project: Project
    ) -> L25TimelineOverview:
        """Get L2.5 timeline overview (~2000 tokens).

        Full timeline snapshot: clips with asset names, gap/overlap detection.
        One request gives AI everything it needs to understand the timeline.
        """
        timeline = project.timeline_data or {}
        layers_data = timeline.get("layers", [])
        audio_tracks_data = timeline.get("audio_tracks", [])

        # Bulk resolve asset_id -> asset_name
        all_asset_ids: set[str] = set()
        for layer in layers_data:
            for clip in layer.get("clips", []):
                aid = clip.get("asset_id")
                if aid:
                    all_asset_ids.add(aid)
        for track in audio_tracks_data:
            for clip in track.get("clips", []):
                aid = clip.get("asset_id")
                if aid:
                    all_asset_ids.add(aid)

        asset_name_map: dict[str, str] = {}
        if all_asset_ids:
            from uuid import UUID as _UUID
            valid_ids: list[_UUID] = []
            for aid in all_asset_ids:
                try:
                    valid_ids.append(_UUID(aid))
                except (ValueError, AttributeError):
                    pass  # Skip malformed asset_ids in timeline data
            if valid_ids:
                result = await self.db.execute(
                    select(Asset.id, Asset.name).where(
                        Asset.id.in_(valid_ids)
                    )
                )
                for row in result:
                    asset_name_map[str(row[0])] = row[1]

        warnings: list[str] = []

        # Process layers
        overview_layers: list[OverviewLayer] = []
        for layer in layers_data:
            clips_data = layer.get("clips", [])
            sorted_clips = sorted(clips_data, key=lambda c: c.get("start_ms", 0))

            overview_clips: list[OverviewClip] = []
            for clip in sorted_clips:
                start = clip.get("start_ms", 0)
                dur = clip.get("duration_ms", 0)
                aid = clip.get("asset_id")

                # Build effects summary (non-default effects only)
                effects_parts: list[str] = []
                effects = clip.get("effects", {})
                if effects:
                    opacity = effects.get("opacity", 1.0)
                    if opacity != 1.0:
                        effects_parts.append(f"opacity({opacity})")
                    ck = effects.get("chroma_key")
                    if ck and ck.get("enabled"):
                        effects_parts.append(f"chroma_key({ck.get('color', '?')})")
                    blend = effects.get("blend_mode", "normal")
                    if blend != "normal":
                        effects_parts.append(f"blend({blend})")

                text = clip.get("text_content")
                if text and len(text) > 100:
                    text = text[:100] + "..."

                overview_clips.append(OverviewClip(
                    id=clip.get("id", "")[:8],
                    asset_name=asset_name_map.get(aid) if aid else None,
                    start_ms=start,
                    end_ms=start + dur,
                    text_content=text,
                    effects_summary=", ".join(effects_parts) if effects_parts else None,
                    group_id=clip.get("group_id"),
                ))

            # Detect gaps
            gaps: list[OverviewGap] = []
            for i in range(len(sorted_clips) - 1):
                end_a = sorted_clips[i].get("start_ms", 0) + sorted_clips[i].get("duration_ms", 0)
                start_b = sorted_clips[i + 1].get("start_ms", 0)
                if start_b > end_a:
                    gaps.append(OverviewGap(
                        start_ms=end_a,
                        end_ms=start_b,
                        duration_ms=start_b - end_a,
                    ))

            # Detect overlaps
            overlaps: list[OverviewOverlap] = []
            for i in range(len(sorted_clips)):
                end_i = sorted_clips[i].get("start_ms", 0) + sorted_clips[i].get("duration_ms", 0)
                for j in range(i + 1, len(sorted_clips)):
                    start_j = sorted_clips[j].get("start_ms", 0)
                    if start_j >= end_i:
                        break  # No more overlaps possible (sorted by start)
                    end_j = sorted_clips[j].get("start_ms", 0) + sorted_clips[j].get("duration_ms", 0)
                    overlap_start = start_j
                    overlap_end = min(end_i, end_j)
                    overlaps.append(OverviewOverlap(
                        clip_a_id=sorted_clips[i].get("id", "")[:8],
                        clip_b_id=sorted_clips[j].get("id", "")[:8],
                        overlap_start_ms=overlap_start,
                        overlap_end_ms=overlap_end,
                        overlap_duration_ms=overlap_end - overlap_start,
                    ))

            if gaps:
                warnings.append(
                    f"Layer '{layer.get('name', '')}' has {len(gaps)} gap(s)"
                )

            overview_layers.append(OverviewLayer(
                id=layer.get("id", ""),
                name=layer.get("name", ""),
                type=layer.get("type", "content"),
                visible=layer.get("visible", True),
                locked=layer.get("locked", False),
                clips=overview_clips,
                gaps=gaps,
                overlaps=overlaps,
            ))

        # Process audio tracks
        overview_audio: list[OverviewAudioTrack] = []
        for track in audio_tracks_data:
            clips_data = track.get("clips", [])
            sorted_clips = sorted(clips_data, key=lambda c: c.get("start_ms", 0))

            overview_clips = []
            for clip in sorted_clips:
                start = clip.get("start_ms", 0)
                dur = clip.get("duration_ms", 0)
                aid = clip.get("asset_id")

                overview_clips.append(OverviewClip(
                    id=clip.get("id", "")[:8],
                    asset_name=asset_name_map.get(aid) if aid else None,
                    start_ms=start,
                    end_ms=start + dur,
                    group_id=clip.get("group_id"),
                ))

            overview_audio.append(OverviewAudioTrack(
                id=track.get("id", ""),
                name=track.get("name", ""),
                type=track.get("type", "narration"),
                volume=track.get("volume", 1.0),
                muted=track.get("muted", False),
                clips=overview_clips,
            ))

        # Generate visual snapshot
        snapshot_base64: str | None = None
        try:
            from src.services.timeline_snapshot import generate_timeline_snapshot

            snapshot_base64 = generate_timeline_snapshot(
                layers=layers_data,
                audio_tracks=audio_tracks_data,
                duration_ms=project.duration_ms or 0,
                asset_name_map=asset_name_map,
            )
        except Exception:
            logger.warning(
                "Failed to generate timeline snapshot for project=%s",
                project.id,
                exc_info=True,
            )

        return L25TimelineOverview(
            project_id=project.id,
            duration_ms=project.duration_ms or 0,
            layers=overview_layers,
            audio_tracks=overview_audio,
            warnings=warnings,
            snapshot_base64=snapshot_base64,
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

        # Find the clip (supports partial ID)
        clip, layer, full_clip_id = self._find_clip_by_id(timeline, clip_id)

        if clip is not None and layer is not None:
            # Found the clip
            asset_name = None
            if clip.get("asset_id"):
                asset = await self._get_asset(clip["asset_id"])
                if asset:
                    asset_name = asset.name

            # Get neighbors
            clips = layer.get("clips", [])
            sorted_clips = sorted(clips, key=lambda c: c.get("start_ms", 0))
            clip_index = next(
                (i for i, c in enumerate(sorted_clips) if c.get("id") == full_clip_id),
                None,
            )

            previous_clip = None
            next_clip = None

            if clip_index is not None:
                if clip_index > 0:
                    prev = sorted_clips[clip_index - 1]
                    prev_end = round(prev.get("start_ms", 0) + prev.get("duration_ms", 0))
                    gap = round(clip.get("start_ms", 0) - prev_end)
                    previous_clip = ClipNeighbor(
                        id=prev.get("id", ""),
                        start_ms=round(prev.get("start_ms", 0)),
                        end_ms=prev_end,
                        gap_ms=max(0, gap),
                    )

                if clip_index < len(sorted_clips) - 1:
                    nxt = sorted_clips[clip_index + 1]
                    clip_end = round(clip.get("start_ms", 0) + clip.get("duration_ms", 0))
                    gap = round(nxt.get("start_ms", 0) - clip_end)
                    next_clip = ClipNeighbor(
                        id=nxt.get("id", ""),
                        start_ms=round(nxt.get("start_ms", 0)),
                        end_ms=round(nxt.get("start_ms", 0) + nxt.get("duration_ms", 0)),
                        gap_ms=max(0, gap),
                    )

            # Build response
            transform = clip.get("transform", {})
            effects = clip.get("effects", {})
            crop_data = clip.get("crop", {})
            text_style_data = clip.get("text_style", {})
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
                    fade_in_ms=effects.get("fade_in_ms", 0),
                    fade_out_ms=effects.get("fade_out_ms", 0),
                    chroma_key_enabled=chroma.get("enabled", False) if chroma else False,
                    chroma_key_color=chroma.get("color", "#00FF00") if chroma else "#00FF00",
                    chroma_key_similarity=chroma.get("similarity", 0.4) if chroma else 0.4,
                    chroma_key_blend=chroma.get("blend", 0.1) if chroma else 0.1,
                ),
                crop=CropDetails(
                    top=crop_data.get("top", 0),
                    right=crop_data.get("right", 0),
                    bottom=crop_data.get("bottom", 0),
                    left=crop_data.get("left", 0),
                    resize_mode=crop_data.get("resize_mode"),
                ) if crop_data else None,
                transition_in=TransitionDetails(
                    type=transition_in.get("type", "none"),
                    duration_ms=transition_in.get("duration_ms", 0),
                ),
                transition_out=TransitionDetails(
                    type=transition_out.get("type", "none"),
                    duration_ms=transition_out.get("duration_ms", 0),
                ),
                text_content=clip.get("text_content"),
                text_style=self._build_text_style_details(text_style_data, clip),
                group_id=clip.get("group_id"),
                previous_clip=previous_clip,
                next_clip=next_clip,
            )

        return None

    @staticmethod
    def _build_text_style_details(
        text_style_data: dict[str, Any],
        clip: dict[str, Any],
    ) -> TextStyleDetails | None:
        """Normalize stored text_style (camelCase) into API response (snake_case)."""
        if not text_style_data and not clip.get("text_content"):
            return None

        def _get_style_value(*keys: str, default: Any = None) -> Any:
            for key in keys:
                if key in text_style_data:
                    return text_style_data.get(key)
            return default

        def _normalize_font_weight(value: Any) -> int:
            if value is None:
                return 400
            if isinstance(value, str):
                lower = value.lower()
                if lower == "bold":
                    return 700
                if lower == "normal":
                    return 400
                try:
                    return int(lower)
                except ValueError:
                    return 400
            if isinstance(value, (int, float)):
                return int(value)
            return 400

        font_weight_value = _get_style_value("fontWeight", "font_weight", default=400)

        return TextStyleDetails(
            font_family=_get_style_value("fontFamily", "font_family", default="Noto Sans JP"),
            font_size=_get_style_value("fontSize", "font_size", default=48),
            font_weight=_normalize_font_weight(font_weight_value),
            color=_get_style_value("color", default="#ffffff"),
            text_align=_get_style_value("textAlign", "text_align", default="center"),
            background_color=_get_style_value("backgroundColor", "background_color"),
            background_opacity=_get_style_value("backgroundOpacity", "background_opacity", default=0),
            line_height=_get_style_value("lineHeight", "line_height"),
            letter_spacing=_get_style_value("letterSpacing", "letter_spacing"),
        )

    async def get_audio_clip_details(
        self, project: Project, clip_id: str
    ) -> L3AudioClipDetails | None:
        """Get L3 audio clip details."""
        timeline = project.timeline_data or {}

        # Find the audio clip (supports partial ID)
        clip, track, full_clip_id = self._find_audio_clip_by_id(timeline, clip_id)

        if clip is not None and track is not None:
            asset_name = None
            if clip.get("asset_id"):
                asset = await self._get_asset(clip["asset_id"])
                if asset:
                    asset_name = asset.name

            # Get neighbors
            clips = track.get("clips", [])
            sorted_clips = sorted(clips, key=lambda c: c.get("start_ms", 0))
            clip_index = next(
                (i for i, c in enumerate(sorted_clips) if c.get("id") == full_clip_id),
                None,
            )

            previous_clip = None
            next_clip = None

            if clip_index is not None:
                if clip_index > 0:
                    prev = sorted_clips[clip_index - 1]
                    prev_end = round(prev.get("start_ms", 0) + prev.get("duration_ms", 0))
                    gap = round(clip.get("start_ms", 0) - prev_end)
                    previous_clip = ClipNeighbor(
                        id=prev.get("id", ""),
                        start_ms=round(prev.get("start_ms", 0)),
                        end_ms=prev_end,
                        gap_ms=max(0, gap),
                    )

                if clip_index < len(sorted_clips) - 1:
                    nxt = sorted_clips[clip_index + 1]
                    clip_end = round(clip.get("start_ms", 0) + clip.get("duration_ms", 0))
                    gap = round(nxt.get("start_ms", 0) - clip_end)
                    next_clip = ClipNeighbor(
                        id=nxt.get("id", ""),
                        start_ms=round(nxt.get("start_ms", 0)),
                        end_ms=round(nxt.get("start_ms", 0) + nxt.get("duration_ms", 0)),
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
                volume_keyframes=[
                    VolumeKeyframeResponse(time_ms=kf.get("time_ms", 0), value=kf.get("value", 1.0))
                    for kf in (clip.get("volume_keyframes") or [])
                ],
                previous_clip=previous_clip,
                next_clip=next_clip,
            )

        return None

    # =========================================================================
    # Linked Audio Helpers
    # =========================================================================

    async def _find_linked_audio_asset(self, video_asset_id: str) -> Asset | None:
        """Find the auto-extracted audio asset linked to a video asset."""
        result = await self.db.execute(
            select(Asset).where(
                Asset.source_asset_id == video_asset_id,
                Asset.type == "audio",
            ).limit(1)
        )
        return result.scalar_one_or_none()

    def _find_or_create_narration_track(self, timeline: dict) -> dict:
        """Find existing narration track or create one."""
        if "audio_tracks" not in timeline:
            timeline["audio_tracks"] = []

        for track in timeline["audio_tracks"]:
            if track.get("type") == "narration":
                return track

        # Create narration track
        narration_track = {
            "id": str(uuid.uuid4()),
            "name": "Narration",
            "type": "narration",
            "volume": 1.0,
            "muted": False,
            "clips": [],
        }
        timeline["audio_tracks"].insert(0, narration_track)
        return narration_track

    def _find_clips_by_group_id(
        self, timeline: dict, group_id: str, exclude_clip_id: str | None = None
    ) -> list[tuple[dict, dict, str]]:
        """Find all clips with matching group_id across layers and audio tracks.

        Returns: list of (clip_data, container (layer or track), "video" | "audio")
        """
        results: list[tuple[dict, dict, str]] = []

        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                if clip.get("group_id") == group_id:
                    if exclude_clip_id and clip.get("id") == exclude_clip_id:
                        continue
                    results.append((clip, layer, "video"))

        for track in timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                if clip.get("group_id") == group_id:
                    if exclude_clip_id and clip.get("id") == exclude_clip_id:
                        continue
                    results.append((clip, track, "audio"))

        return results

    # =========================================================================
    # Preview Diff (read-only simulation)
    # =========================================================================

    async def preview_diff(self, project: Project, request: PreviewDiffRequest) -> dict:
        """Simulate an operation and return the diff without applying changes.

        This is read-only: no timeline data is modified, no DB flush occurs.
        """
        import copy

        timeline = project.timeline_data or {}
        changes: list[dict] = []
        conflicts: list[str] = []

        if request.operation_type == "move":
            if not request.clip_id:
                return {"operation_type": request.operation_type, "change_count": 0, "changes": [], "conflicts": ["clip_id is required for move"]}

            clip_data, _layer, full_clip_id = self._find_clip_by_id(timeline, request.clip_id)
            if not clip_data:
                return {"operation_type": request.operation_type, "change_count": 0, "changes": [], "conflicts": [f"Clip not found: {request.clip_id}"]}

            old_start = clip_data.get("start_ms", 0)
            new_start = request.parameters.get("new_start_ms", old_start)
            delta_ms = new_start - old_start

            if delta_ms != 0:
                changes.append({
                    "entity_type": "clip",
                    "entity_id": full_clip_id,
                    "field": "start_ms",
                    "before": old_start,
                    "after": new_start,
                })

            # Show linked audio changes via group_id
            group_id = clip_data.get("group_id")
            if group_id and delta_ms != 0:
                linked = self._find_clips_by_group_id(timeline, group_id, exclude_clip_id=full_clip_id)
                for linked_clip, _container, clip_type in linked:
                    linked_start = linked_clip.get("start_ms", 0)
                    changes.append({
                        "entity_type": "audio_clip" if clip_type == "audio" else "clip",
                        "entity_id": linked_clip.get("id", ""),
                        "field": "start_ms",
                        "before": linked_start,
                        "after": max(0, linked_start + delta_ms),
                    })

        elif request.operation_type == "trim":
            if not request.clip_id:
                return {"operation_type": request.operation_type, "change_count": 0, "changes": [], "conflicts": ["clip_id is required for trim"]}

            clip_data, _layer, full_clip_id = self._find_clip_by_id(timeline, request.clip_id)
            if not clip_data:
                return {"operation_type": request.operation_type, "change_count": 0, "changes": [], "conflicts": [f"Clip not found: {request.clip_id}"]}

            for field in ["duration_ms", "in_point_ms", "out_point_ms"]:
                if field in request.parameters:
                    changes.append({
                        "entity_type": "clip",
                        "entity_id": full_clip_id,
                        "field": field,
                        "before": clip_data.get(field),
                        "after": request.parameters[field],
                    })

        elif request.operation_type == "delete":
            if not request.clip_id:
                return {"operation_type": request.operation_type, "change_count": 0, "changes": [], "conflicts": ["clip_id is required for delete"]}

            clip_data, _layer, full_clip_id = self._find_clip_by_id(timeline, request.clip_id)
            if not clip_data:
                return {"operation_type": request.operation_type, "change_count": 0, "changes": [], "conflicts": [f"Clip not found: {request.clip_id}"]}

            changes.append({
                "entity_type": "clip",
                "entity_id": full_clip_id,
                "action": "delete",
                "before": {"start_ms": clip_data.get("start_ms", 0), "duration_ms": clip_data.get("duration_ms", 0)},
                "after": None,
            })

            group_id = clip_data.get("group_id")
            if group_id:
                linked = self._find_clips_by_group_id(timeline, group_id, exclude_clip_id=full_clip_id)
                for linked_clip, _container, clip_type in linked:
                    changes.append({
                        "entity_type": "audio_clip" if clip_type == "audio" else "clip",
                        "entity_id": linked_clip.get("id", ""),
                        "action": "delete",
                        "before": {"start_ms": linked_clip.get("start_ms", 0), "duration_ms": linked_clip.get("duration_ms", 0)},
                        "after": None,
                    })

        elif request.operation_type in ("close_all_gaps", "distribute_evenly"):
            target_layer_id = request.layer_id
            if not target_layer_id:
                return {"operation_type": request.operation_type, "change_count": 0, "changes": [], "conflicts": ["layer_id is required for " + request.operation_type]}

            layer, full_layer_id = self._find_layer_by_id(timeline, target_layer_id)
            if not layer:
                return {"operation_type": request.operation_type, "change_count": 0, "changes": [], "conflicts": [f"Layer not found: {target_layer_id}"]}

            clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
            if not clips:
                return {"operation_type": request.operation_type, "change_count": 0, "changes": [], "conflicts": []}

            if request.operation_type == "close_all_gaps":
                current_pos = clips[0].get("start_ms", 0)
                gap_ms = 0
            else:
                # distribute_evenly
                current_pos = request.parameters.get("start_ms", clips[0].get("start_ms", 0))
                gap_ms = request.parameters.get("gap_ms", 0)

            for clip in clips:
                old_start = clip.get("start_ms", 0)
                if old_start != current_pos:
                    delta = current_pos - old_start
                    changes.append({
                        "entity_type": "clip",
                        "entity_id": clip.get("id", ""),
                        "field": "start_ms",
                        "before": old_start,
                        "after": current_pos,
                    })
                    # Also show linked audio changes
                    group_id = clip.get("group_id")
                    if group_id and delta != 0:
                        linked = self._find_clips_by_group_id(timeline, group_id, exclude_clip_id=clip.get("id"))
                        for linked_clip, _container, clip_type in linked:
                            if clip_type == "audio":
                                linked_start = linked_clip.get("start_ms", 0)
                                changes.append({
                                    "entity_type": "audio_clip",
                                    "entity_id": linked_clip.get("id", ""),
                                    "field": "start_ms",
                                    "before": linked_start,
                                    "after": max(0, linked_start + delta),
                                })
                current_pos = current_pos + clip.get("duration_ms", 0) + gap_ms

        elif request.operation_type == "add_text_with_timing":
            if not request.clip_id:
                return {"operation_type": request.operation_type, "change_count": 0, "changes": [], "conflicts": ["clip_id is required for add_text_with_timing"]}

            clip_data, _layer, full_clip_id = self._find_clip_by_id(timeline, request.clip_id)
            if not clip_data:
                return {"operation_type": request.operation_type, "change_count": 0, "changes": [], "conflicts": [f"Clip not found: {request.clip_id}"]}

            changes.append({
                "entity_type": "clip",
                "action": "create",
                "before": None,
                "after": {
                    "type": "text",
                    "start_ms": clip_data.get("start_ms", 0),
                    "duration_ms": clip_data.get("duration_ms", 0),
                    "text_content": request.parameters.get("text", ""),
                    "position": request.parameters.get("position", "bottom"),
                },
            })

        return {
            "operation_type": request.operation_type,
            "change_count": len(changes),
            "changes": changes,
            "conflicts": conflicts,
        }

    # =========================================================================
    # Write Operations
    # =========================================================================

    async def add_clip(
        self, project: Project, request: AddClipRequest,
        include_audio: bool = True,
        _skip_flush: bool = False,
    ) -> L3ClipDetails | None:
        """Add a new video clip to a layer."""
        timeline = project.timeline_data or {}

        # Find the target layer (supports partial ID)
        layer, full_layer_id = self._find_layer_by_id(timeline, request.layer_id)

        if layer is None:
            raise LayerNotFoundError(request.layer_id)

        # Validate asset and timing if provided
        if request.asset_id:
            await self._validate_clip_timing(
                str(request.asset_id),
                request.in_point_ms,
                request.out_point_ms,
                request.duration_ms,
            )
        else:
            # Clips must have either asset_id OR text_content
            if not request.text_content:
                raise MissingRequiredFieldError(
                    "Clip must have either asset_id or text_content"
                )

        # Note: Overlap check removed to allow AI-driven clip placement at any position
        # Overlapping clips are now allowed and handled by frontend visualization

        # Determine layer type for smart defaults
        layer_type = layer.get("type", "content")

        # Smart defaults based on layer type
        default_x: float = 960
        default_y: float = 540
        default_scale: float = 1.0
        default_text_style: dict[str, Any] = {}

        if layer_type == "text":
            default_x = 960
            default_y = 800  # Lower center for text
            default_text_style = {"font_size": 48}
        elif layer_type == "background":
            default_x = 960
            default_y = 540
            default_scale = 1.0  # scale to fill 1920x1080
        elif layer_type == "content":
            default_x = 960
            default_y = 540
            default_scale = 1.0  # scale to fill
        # avatar, effects: center (960, 540) with default scale 1.0

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
                "x": request.x if request.x is not None else default_x,
                "y": request.y if request.y is not None else default_y,
                "scale": request.scale if request.scale is not None else default_scale,
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

        # Bug 3 fix: merge request effects if provided
        if request.effects:
            new_clip["effects"].update(request.effects)

        if request.text_content:
            new_clip["text_content"] = request.text_content
            # Merge smart default text_style with request overrides
            merged_text_style = {**default_text_style, **(request.text_style or {})}
            new_clip["text_style"] = merged_text_style

        if request.group_id:
            new_clip["group_id"] = request.group_id

        # Add to layer
        if "clips" not in layer:
            layer["clips"] = []
        layer["clips"].append(new_clip)

        # Auto-place linked audio clip
        linked_audio_clip = None
        if include_audio and request.asset_id and not request.text_content:
            audio_asset = await self._find_linked_audio_asset(str(request.asset_id))
            if audio_asset:
                group_id = request.group_id or str(uuid.uuid4())
                new_clip["group_id"] = group_id

                narration_track = self._find_or_create_narration_track(timeline)
                audio_clip_id = str(uuid.uuid4())
                linked_audio_clip = {
                    "id": audio_clip_id,
                    "asset_id": str(audio_asset.id),
                    "start_ms": request.start_ms,
                    "duration_ms": request.duration_ms,
                    "in_point_ms": request.in_point_ms,
                    "out_point_ms": request.out_point_ms,
                    "volume": 1.0,
                    "fade_in_ms": 0,
                    "fade_out_ms": 0,
                    "group_id": group_id,
                }
                if "clips" not in narration_track:
                    narration_track["clips"] = []
                narration_track["clips"].append(linked_audio_clip)

        # Update project duration
        self._update_project_duration(project)

        # Mark as modified (skip in batch mode)
        if not _skip_flush:
            flag_modified(project, "timeline_data")
            await self.db.flush()

        result = await self.get_clip_details(project, new_clip_id)
        if result is not None and linked_audio_clip:
            result._linked_audio_clip_id = linked_audio_clip["id"]

        # Detect overlaps with other clips on the same layer
        if result is not None:
            overlap_warnings = self._detect_overlaps_in_layer(
                layer, new_clip_id, request.start_ms, request.duration_ms
            )
            result._overlap_warnings = overlap_warnings

        return result

    async def add_audio_clip(
        self, project: Project, request: AddAudioClipRequest,
        _skip_flush: bool = False,
    ) -> L3AudioClipDetails | None:
        """Add a new audio clip to a track."""
        timeline = project.timeline_data or {}

        # Find the target track (supports partial ID)
        track, full_track_id = self._find_audio_track_by_id(timeline, request.track_id)

        if track is None:
            raise AudioTrackNotFoundError(request.track_id)

        # Validate asset and timing
        await self._validate_clip_timing(
            str(request.asset_id),
            request.in_point_ms,
            request.out_point_ms,
            request.duration_ms,
        )

        # Validate asset belongs to this project
        asset = await self._get_asset(str(request.asset_id))
        if asset and asset.project_id != project.id:
            raise AssetNotFoundError(str(request.asset_id))

        # Note: Overlap check removed to allow AI-driven clip placement at any position
        # Overlapping clips are now allowed and handled by frontend visualization

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

        if not _skip_flush:
            flag_modified(project, "timeline_data")
            await self.db.flush()

        return await self.get_audio_clip_details(project, new_clip_id)

    def _find_clip_by_id(self, timeline: dict, clip_id: str) -> tuple[dict | None, dict | None, str | None]:
        """Find a video clip by full or partial ID.

        Returns: (clip_data, source_layer, full_clip_id)
        """
        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                full_id = clip.get("id", "")
                # Match by full ID or partial ID (prefix match)
                if full_id == clip_id or full_id.startswith(clip_id):
                    return clip, layer, full_id
        return None, None, None

    def _find_audio_clip_by_id(self, timeline: dict, clip_id: str) -> tuple[dict | None, dict | None, str | None]:
        """Find an audio clip by full or partial ID.

        Returns: (clip_data, source_track, full_clip_id)
        """
        for track in timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                full_id = clip.get("id", "")
                if full_id == clip_id or full_id.startswith(clip_id):
                    return clip, track, full_id
        return None, None, None

    def _find_layer_by_id(self, timeline: dict, layer_id: str) -> tuple[dict | None, str | None]:
        """Find a layer by full or partial ID.

        Returns: (layer_data, full_layer_id)
        """
        for layer in timeline.get("layers", []):
            full_id = layer.get("id", "")
            if full_id == layer_id or full_id.startswith(layer_id):
                return layer, full_id
        return None, None

    def _find_audio_track_by_id(self, timeline: dict, track_id: str) -> tuple[dict | None, str | None]:
        """Find an audio track by full or partial ID.

        Returns: (track_data, full_track_id)
        """
        for track in timeline.get("audio_tracks", []):
            full_id = track.get("id", "")
            if full_id == track_id or full_id.startswith(track_id):
                return track, full_id
        return None, None

    def _detect_overlaps_in_layer(
        self, layer: dict, clip_id: str, start_ms: int, duration_ms: int
    ) -> list[str]:
        """Detect overlaps between a clip and other clips on the same layer.

        Returns a list of warning strings for each overlap found.
        """
        warnings: list[str] = []
        end_ms = start_ms + duration_ms
        for other_clip in layer.get("clips", []):
            other_id = other_clip.get("id", "")
            if other_id == clip_id:
                continue
            other_start = other_clip.get("start_ms", 0)
            other_end = other_start + other_clip.get("duration_ms", 0)
            # Check overlap
            if start_ms < other_end and end_ms > other_start:
                warnings.append(
                    f"Clip overlaps with clip {other_id} on the same layer ({other_start}-{other_end}ms)"
                )
        return warnings

    async def move_clip(
        self, project: Project, clip_id: str, request: MoveClipRequest,
        _skip_flush: bool = False,
    ) -> L3ClipDetails | None:
        """Move a video clip to a new position or layer. Linked clips move in sync."""
        timeline = project.timeline_data or {}

        # Find the clip (supports partial ID)
        clip_data, source_layer, full_clip_id = self._find_clip_by_id(timeline, clip_id)

        if clip_data is None:
            raise ClipNotFoundError(clip_id)

        # Calculate move delta for group propagation
        old_start_ms = clip_data.get("start_ms", 0)
        delta_ms = request.new_start_ms - old_start_ms

        # Determine target layer (supports partial ID)
        target_layer = source_layer
        if request.new_layer_id:
            found_layer, full_layer_id = self._find_layer_by_id(timeline, request.new_layer_id)
            if found_layer and full_layer_id != source_layer.get("id"):
                target_layer = found_layer
            elif not found_layer:
                raise LayerNotFoundError(request.new_layer_id)

        # Move the clip
        if target_layer != source_layer:
            source_layer["clips"].remove(clip_data)
            if "clips" not in target_layer:
                target_layer["clips"] = []
            target_layer["clips"].append(clip_data)

        clip_data["start_ms"] = request.new_start_ms

        # Propagate move to group-linked clips
        linked_moved_ids: list[str] = []
        group_id = clip_data.get("group_id")
        if group_id and delta_ms != 0:
            linked = self._find_clips_by_group_id(timeline, group_id, exclude_clip_id=full_clip_id)
            for linked_clip, _container, _clip_type in linked:
                linked_clip["start_ms"] = max(0, linked_clip.get("start_ms", 0) + delta_ms)
                linked_moved_ids.append(linked_clip.get("id", ""))

        # Update project duration
        self._update_project_duration(project)

        if not _skip_flush:
            flag_modified(project, "timeline_data")
            await self.db.flush()

        result = await self.get_clip_details(project, full_clip_id or clip_id)
        if result is not None:
            result._linked_clips_moved = linked_moved_ids

            # Detect overlaps with other clips on the target layer
            overlap_warnings = self._detect_overlaps_in_layer(
                target_layer, full_clip_id or clip_id,
                request.new_start_ms, clip_data.get("duration_ms", 0)
            )
            result._overlap_warnings = overlap_warnings

        return result

    async def move_audio_clip(
        self, project: Project, clip_id: str, request: MoveAudioClipRequest,
        _skip_flush: bool = False,
    ) -> L3AudioClipDetails | None:
        """Move an audio clip to a new position or track."""
        timeline = project.timeline_data or {}

        # Find the clip (supports partial ID)
        clip_data, source_track, full_clip_id = self._find_audio_clip_by_id(timeline, clip_id)

        if clip_data is None:
            raise AudioClipNotFoundError(clip_id)

        # Determine target track (supports partial ID)
        target_track = source_track
        if request.new_track_id:
            found_track, full_track_id = self._find_audio_track_by_id(timeline, request.new_track_id)
            if found_track and full_track_id != source_track.get("id"):
                target_track = found_track
            elif not found_track:
                raise AudioTrackNotFoundError(request.new_track_id)

        # Note: Overlap check removed to allow AI-driven clip placement at any position
        # Overlapping clips are now allowed and handled by frontend visualization

        # Move the clip
        if target_track != source_track:
            source_track["clips"].remove(clip_data)
            if "clips" not in target_track:
                target_track["clips"] = []
            target_track["clips"].append(clip_data)

        clip_data["start_ms"] = request.new_start_ms

        # Update project duration
        self._update_project_duration(project)

        if not _skip_flush:
            flag_modified(project, "timeline_data")
            await self.db.flush()

        return await self.get_audio_clip_details(project, full_clip_id or clip_id)

    async def update_clip_transform(
        self, project: Project, clip_id: str, request: UpdateClipTransformRequest,
        _skip_flush: bool = False,
    ) -> L3ClipDetails | None:
        """Update clip transform properties."""
        timeline = project.timeline_data or {}

        # Find the clip (supports partial ID)
        clip, _, full_clip_id = self._find_clip_by_id(timeline, clip_id)

        if clip is None:
            raise ClipNotFoundError(clip_id)

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

        if not _skip_flush:
            flag_modified(project, "timeline_data")
            await self.db.flush()
        return await self.get_clip_details(project, full_clip_id or clip_id)

    async def update_clip_effects(
        self, project: Project, clip_id: str, request: UpdateClipEffectsRequest,
        _skip_flush: bool = False,
    ) -> L3ClipDetails | None:
        """Update clip effects properties."""
        timeline = project.timeline_data or {}

        # Find the clip (supports partial ID)
        clip, _, full_clip_id = self._find_clip_by_id(timeline, clip_id)

        if clip is None:
            raise ClipNotFoundError(clip_id)

        if "effects" not in clip:
            clip["effects"] = {}

        if request.opacity is not None:
            clip["effects"]["opacity"] = request.opacity
        if request.blend_mode is not None:
            clip["effects"]["blend_mode"] = request.blend_mode

        # Store fade in effects (single source of truth for API)
        if request.fade_in_ms is not None:
            clip["effects"]["fade_in_ms"] = request.fade_in_ms
            # Internal sync to transition for renderer (not exposed in contract)
            if request.fade_in_ms > 0:
                clip["transition_in"] = {"type": "fade", "duration_ms": request.fade_in_ms}
            else:
                clip["transition_in"] = {"type": "none", "duration_ms": 0}

        if request.fade_out_ms is not None:
            clip["effects"]["fade_out_ms"] = request.fade_out_ms
            # Internal sync to transition for renderer (not exposed in contract)
            if request.fade_out_ms > 0:
                clip["transition_out"] = {"type": "fade", "duration_ms": request.fade_out_ms}
            else:
                clip["transition_out"] = {"type": "none", "duration_ms": 0}

        # Initialize chroma_key sub-object once if any chroma_key field is set
        has_chroma_key_update = any([
            request.chroma_key_enabled is not None,
            request.chroma_key_color is not None,
            request.chroma_key_similarity is not None,
            request.chroma_key_blend is not None,
        ])
        if has_chroma_key_update:
            if "chroma_key" not in clip["effects"]:
                clip["effects"]["chroma_key"] = {}

            if request.chroma_key_enabled is not None:
                clip["effects"]["chroma_key"]["enabled"] = request.chroma_key_enabled
            if request.chroma_key_color is not None:
                clip["effects"]["chroma_key"]["color"] = request.chroma_key_color
            if request.chroma_key_similarity is not None:
                clip["effects"]["chroma_key"]["similarity"] = request.chroma_key_similarity
            if request.chroma_key_blend is not None:
                clip["effects"]["chroma_key"]["blend"] = request.chroma_key_blend

        if not _skip_flush:
            flag_modified(project, "timeline_data")
            await self.db.flush()
        return await self.get_clip_details(project, full_clip_id or clip_id)

    async def update_clip_crop(
        self, project: Project, clip_id: str, request: "UpdateClipCropRequest"
    ) -> L3ClipDetails | None:
        """Update clip crop properties.

        Crop values are fractional (0.0-0.5), representing the percentage of each edge to remove.
        """
        timeline = project.timeline_data or {}

        # Find the clip (supports partial ID)
        clip, _, full_clip_id = self._find_clip_by_id(timeline, clip_id)

        if clip is None:
            raise ClipNotFoundError(clip_id)

        if "crop" not in clip:
            clip["crop"] = {}

        if request.top is not None:
            clip["crop"]["top"] = request.top
        if request.right is not None:
            clip["crop"]["right"] = request.right
        if request.bottom is not None:
            clip["crop"]["bottom"] = request.bottom
        if request.left is not None:
            clip["crop"]["left"] = request.left
        if request.resize_mode is not None:
            clip["crop"]["resize_mode"] = request.resize_mode

        flag_modified(project, "timeline_data")
        await self.db.flush()
        return await self.get_clip_details(project, full_clip_id or clip_id)

    async def update_clip_text_style(
        self, project: Project, clip_id: str, request: "UpdateClipTextStyleRequest"
    ) -> L3ClipDetails | None:
        """Update text clip styling properties.

        Only applies to text clips. Allows partial updates.
        """
        timeline = project.timeline_data or {}

        # Find the clip (supports partial ID)
        clip, _, full_clip_id = self._find_clip_by_id(timeline, clip_id)

        if clip is None:
            raise ClipNotFoundError(clip_id)

        # Verify this is a text clip
        if clip.get("text_content") is None:
            raise InvalidClipTypeError(clip_id, expected_type="text")

        if "text_style" not in clip:
            clip["text_style"] = {}

        def _normalize_font_weight_for_storage(value: int | str | None) -> str | None:
            if value is None:
                return None
            if isinstance(value, str):
                lower = value.lower()
                if lower in {"bold", "normal"}:
                    return lower
                try:
                    value = int(lower)
                except ValueError:
                    return "normal"
            return "bold" if int(value) >= 600 else "normal"

        # Use snake_case field access (Pydantic internal names)
        # Store with camelCase keys to match frontend/renderer expectations.
        # Remove legacy snake_case keys to avoid duplication.
        def _set_style(camel_key: str, snake_key: str, value: any) -> None:
            clip["text_style"][camel_key] = value
            clip["text_style"].pop(snake_key, None)  # Remove legacy key if exists

        if request.font_family is not None:
            _set_style("fontFamily", "font_family", request.font_family)
        if request.font_size is not None:
            _set_style("fontSize", "font_size", request.font_size)
        if request.font_weight is not None:
            _set_style(
                "fontWeight",
                "font_weight",
                _normalize_font_weight_for_storage(request.font_weight),
            )
        if request.color is not None:
            clip["text_style"]["color"] = request.color
        if request.text_align is not None:
            _set_style("textAlign", "text_align", request.text_align)
        if request.background_color is not None:
            _set_style("backgroundColor", "background_color", request.background_color)
        if request.background_opacity is not None:
            _set_style("backgroundOpacity", "background_opacity", request.background_opacity)
        if request.line_height is not None:
            _set_style("lineHeight", "line_height", request.line_height)
        if request.letter_spacing is not None:
            _set_style("letterSpacing", "letter_spacing", request.letter_spacing)

        flag_modified(project, "timeline_data")
        await self.db.flush()
        return await self.get_clip_details(project, full_clip_id or clip_id)

    async def update_audio_clip(
        self, project: Project, clip_id: str, request: "UpdateAudioClipRequest"
    ) -> L3AudioClipDetails | None:
        """Update audio clip properties (volume, fades).

        Finds audio clip in timeline_data.audio_tracks[*].clips and applies updates.
        """
        timeline = project.timeline_data or {}

        # Find the audio clip (supports partial ID)
        clip, track, full_clip_id = self._find_audio_clip_by_id(timeline, clip_id)

        if clip is None:
            raise AudioClipNotFoundError(clip_id)

        if request.volume is not None:
            clip["volume"] = request.volume
        if request.fade_in_ms is not None:
            clip["fade_in_ms"] = request.fade_in_ms
        if request.fade_out_ms is not None:
            clip["fade_out_ms"] = request.fade_out_ms
        if request.volume_keyframes is not None:
            clip["volume_keyframes"] = [
                {"time_ms": kf.time_ms, "value": kf.value}
                for kf in request.volume_keyframes
            ]

        flag_modified(project, "timeline_data")
        await self.db.flush()
        return await self.get_audio_clip_details(project, full_clip_id or clip_id)

    async def update_clip_timing(
        self, project: Project, clip_id: str, request: "UpdateClipTimingRequest"
    ) -> L3ClipDetails | None:
        """Update clip timing properties (duration, speed, in/out points). Propagates to linked clips."""
        timeline = project.timeline_data or {}

        # Find the clip (supports partial ID)
        clip, _, full_clip_id = self._find_clip_by_id(timeline, clip_id)

        if clip is None:
            raise ClipNotFoundError(clip_id)

        if request.duration_ms is not None:
            clip["duration_ms"] = request.duration_ms
        if request.speed is not None:
            clip["speed"] = request.speed
        if request.in_point_ms is not None:
            clip["in_point_ms"] = request.in_point_ms
        if request.out_point_ms is not None:
            clip["out_point_ms"] = request.out_point_ms

        # Propagate timing to group-linked clips
        linked_updated_ids: list[str] = []
        group_id = clip.get("group_id")
        if group_id:
            linked = self._find_clips_by_group_id(timeline, group_id, exclude_clip_id=full_clip_id)
            for linked_clip, _container, _clip_type in linked:
                if request.duration_ms is not None:
                    linked_clip["duration_ms"] = request.duration_ms
                if request.in_point_ms is not None:
                    linked_clip["in_point_ms"] = request.in_point_ms
                if request.out_point_ms is not None:
                    linked_clip["out_point_ms"] = request.out_point_ms
                linked_updated_ids.append(linked_clip.get("id", ""))

        # Update project duration
        self._update_project_duration(project)

        flag_modified(project, "timeline_data")
        await self.db.flush()
        result = await self.get_clip_details(project, full_clip_id or clip_id)
        if result is not None:
            result._linked_clips_updated = linked_updated_ids
        return result

    async def update_clip_text(
        self, project: Project, clip_id: str, request: "UpdateClipTextRequest"
    ) -> L3ClipDetails | None:
        """Update text clip content.

        Only applies to text clips (clips that have text_content).
        """
        timeline = project.timeline_data or {}

        # Find the clip (supports partial ID)
        clip, _, full_clip_id = self._find_clip_by_id(timeline, clip_id)

        if clip is None:
            raise ClipNotFoundError(clip_id)

        # Verify this is a text clip
        if clip.get("text_content") is None:
            raise InvalidClipTypeError(clip_id, expected_type="text")

        clip["text_content"] = request.text_content

        flag_modified(project, "timeline_data")
        await self.db.flush()
        return await self.get_clip_details(project, full_clip_id or clip_id)

    async def update_clip_shape(
        self, project: Project, clip_id: str, request: "UpdateClipShapeRequest"
    ) -> L3ClipDetails | None:
        """Update shape clip properties.

        Only applies to shape clips (clips that have shape_type).
        """
        timeline = project.timeline_data or {}

        # Find the clip (supports partial ID)
        clip, _, full_clip_id = self._find_clip_by_id(timeline, clip_id)

        if clip is None:
            raise ClipNotFoundError(clip_id)

        # Verify this is a shape clip (has shape_type or shape properties)
        if clip.get("shape_type") is None and clip.get("type") != "shape":
            raise InvalidClipTypeError(clip_id, expected_type="shape")

        if request.filled is not None:
            clip["filled"] = request.filled
        if request.fill_color is not None:
            clip["fillColor"] = request.fill_color
        if request.stroke_color is not None:
            clip["strokeColor"] = request.stroke_color
        if request.stroke_width is not None:
            clip["strokeWidth"] = request.stroke_width
        if request.width is not None:
            if "transform" not in clip:
                clip["transform"] = {}
            clip["transform"]["width"] = request.width
        if request.height is not None:
            if "transform" not in clip:
                clip["transform"] = {}
            clip["transform"]["height"] = request.height
        if request.corner_radius is not None:
            clip["cornerRadius"] = request.corner_radius
        if request.fade is not None:
            if "effects" not in clip:
                clip["effects"] = {}
            clip["effects"]["fade_in_ms"] = request.fade
            clip["effects"]["fade_out_ms"] = request.fade

        flag_modified(project, "timeline_data")
        await self.db.flush()
        return await self.get_clip_details(project, full_clip_id or clip_id)

    async def delete_clip(
        self, project: Project, clip_id: str,
        _skip_flush: bool = False,
    ) -> dict[str, Any]:
        """Delete a video clip and any group-linked clips.

        Returns:
            Dict with 'deleted_id' and 'deleted_linked_ids'.

        Raises:
            ClipNotFoundError: If clip not found.
        """
        timeline = project.timeline_data or {}

        # Find the clip (supports partial ID)
        clip_data, source_layer, full_clip_id = self._find_clip_by_id(timeline, clip_id)

        if clip_data is None:
            raise ClipNotFoundError(clip_id)

        # Delete group-linked clips
        deleted_linked_ids: list[str] = []
        group_id = clip_data.get("group_id")
        if group_id:
            linked = self._find_clips_by_group_id(timeline, group_id, exclude_clip_id=full_clip_id)
            for linked_clip, container, clip_type in linked:
                container["clips"].remove(linked_clip)
                deleted_linked_ids.append(linked_clip.get("id", ""))

        source_layer["clips"].remove(clip_data)
        self._update_project_duration(project)
        if not _skip_flush:
            flag_modified(project, "timeline_data")
            await self.db.flush()
        return {
            "deleted_id": full_clip_id or clip_id,
            "deleted_linked_ids": deleted_linked_ids,
        }

    async def delete_audio_clip(
        self, project: Project, clip_id: str,
        _skip_flush: bool = False,
    ) -> bool:
        """Delete an audio clip."""
        timeline = project.timeline_data or {}

        # Find the audio clip (supports partial ID)
        clip_data, source_track, full_clip_id = self._find_audio_clip_by_id(timeline, clip_id)

        if clip_data is None:
            raise AudioClipNotFoundError(clip_id)

        source_track["clips"].remove(clip_data)
        self._update_project_duration(project)
        if not _skip_flush:
            flag_modified(project, "timeline_data")
            await self.db.flush()
        return True

    async def trim_clip(
        self, project: Project, clip_id: str, duration_ms: int, clip_type: str = "video",
        _skip_flush: bool = False,
    ) -> bool:
        """Change the duration of a clip."""
        timeline = project.timeline_data or {}

        if clip_type == "video":
            clip_data, _, full_clip_id = self._find_clip_by_id(timeline, clip_id)
        else:
            clip_data, _, full_clip_id = self._find_audio_clip_by_id(timeline, clip_id)

        if clip_data is None:
            raise ValueError(f"Clip not found: {clip_id}")

        clip_data["duration_ms"] = duration_ms
        self._update_project_duration(project)
        if not _skip_flush:
            flag_modified(project, "timeline_data")
            await self.db.flush()
        return True

    async def split_clip(
        self, project: Project, clip_id: str, split_at_ms: int
    ) -> dict[str, Any]:
        """Split a video clip at a specific time position.

        Also splits all group-linked clips at the same position.
        Both halves maintain linkage via new group_ids.

        Args:
            project: The target project
            clip_id: Clip to split
            split_at_ms: Split position relative to clip start_ms on timeline

        Returns:
            Dict with left_clip, right_clip, and linked split info.
        """
        timeline = project.timeline_data or {}

        clip_data, source_layer, full_clip_id = self._find_clip_by_id(timeline, clip_id)
        if clip_data is None:
            raise ClipNotFoundError(clip_id)

        clip_start = clip_data.get("start_ms", 0)
        clip_duration = clip_data.get("duration_ms", 0)
        clip_in_point = clip_data.get("in_point_ms", 0)

        # split_at_ms is absolute timeline position
        relative_split = split_at_ms - clip_start
        if relative_split <= 0 or relative_split >= clip_duration:
            raise InvalidTimeRangeError(
                f"Split position {split_at_ms}ms must be within clip range "
                f"({clip_start}ms - {clip_start + clip_duration}ms)"
            )

        old_group_id = clip_data.get("group_id")
        left_group_id = str(uuid.uuid4())
        right_group_id = str(uuid.uuid4())

        # --- Split the primary clip ---
        # Left half: adjust duration, clear fade_out
        clip_data["duration_ms"] = relative_split
        if "effects" in clip_data:
            clip_data["effects"].pop("fade_out_ms", None)
        clip_data["group_id"] = left_group_id

        # Right half: new clip
        right_clip_id = str(uuid.uuid4())
        right_clip = {
            **{k: v for k, v in clip_data.items() if k != "id"},
            "id": right_clip_id,
            "start_ms": clip_start + relative_split,
            "duration_ms": clip_duration - relative_split,
            "in_point_ms": clip_in_point + relative_split,
            "group_id": right_group_id,
        }
        # Clear fade_in on right half
        if "effects" in right_clip:
            right_clip["effects"] = {k: v for k, v in right_clip["effects"].items() if k != "fade_in_ms"}
        source_layer["clips"].append(right_clip)

        # --- Split group-linked clips ---
        linked_splits: list[dict[str, str]] = []
        if old_group_id:
            linked = self._find_clips_by_group_id(timeline, old_group_id, exclude_clip_id=full_clip_id)
            for linked_clip, container, clip_type in linked:
                l_start = linked_clip.get("start_ms", 0)
                l_duration = linked_clip.get("duration_ms", 0)
                l_in_point = linked_clip.get("in_point_ms", 0)
                l_relative = split_at_ms - l_start

                if l_relative <= 0 or l_relative >= l_duration:
                    # Linked clip doesn't overlap split point, just update group
                    linked_clip["group_id"] = left_group_id
                    continue

                # Split linked clip
                linked_clip["duration_ms"] = l_relative
                linked_clip["group_id"] = left_group_id
                # Clear fade_out for audio
                if clip_type == "audio":
                    linked_clip.pop("fade_out_ms", None)

                linked_right_id = str(uuid.uuid4())
                linked_right: dict[str, Any] = {
                    **{k: v for k, v in linked_clip.items() if k != "id"},
                    "id": linked_right_id,
                    "start_ms": l_start + l_relative,
                    "duration_ms": l_duration - l_relative,
                    "in_point_ms": l_in_point + l_relative,
                    "group_id": right_group_id,
                }
                # Audio: add micro-fade at cut point
                if clip_type == "audio":
                    linked_clip["fade_out_ms"] = 10
                    linked_right["fade_in_ms"] = 10
                    linked_right.pop("fade_out_ms", None) if "fade_out_ms" not in linked_clip else None

                container["clips"].append(linked_right)
                linked_splits.append({
                    "original_id": linked_clip.get("id", ""),
                    "left_id": linked_clip.get("id", ""),
                    "right_id": linked_right_id,
                })

        self._update_project_duration(project)
        flag_modified(project, "timeline_data")
        await self.db.flush()

        left_details = await self.get_clip_details(project, full_clip_id or clip_id)
        right_details = await self.get_clip_details(project, right_clip_id)

        return {
            "left_clip": left_details,
            "right_clip": right_details,
            "left_group_id": left_group_id,
            "right_group_id": right_group_id,
            "linked_splits": linked_splits,
        }

    async def unlink_clip(self, project: Project, clip_id: str) -> dict[str, Any]:
        """Remove group_id from a clip, unlinking it from any group.

        Returns:
            Dict with clip_id and previous group_id.
        """
        timeline = project.timeline_data or {}

        # Try video clips first, then audio clips
        clip_data, _, full_clip_id = self._find_clip_by_id(timeline, clip_id)
        if clip_data is None:
            clip_data, _, full_clip_id = self._find_audio_clip_by_id(timeline, clip_id)

        if clip_data is None:
            raise ClipNotFoundError(clip_id)

        old_group_id = clip_data.get("group_id")
        clip_data.pop("group_id", None)

        flag_modified(project, "timeline_data")
        await self.db.flush()

        return {
            "clip_id": full_clip_id or clip_id,
            "previous_group_id": old_group_id,
        }

    async def add_layer(
        self,
        project: Project,
        name: str,
        layer_type: str = "content",
        insert_at: int | None = None,
    ) -> LayerSummary:
        """Add a new layer to the project.

        New layers are inserted at the top of the layer list (array index 0) by default,
        or at the specified insert_at position. After insertion, all layers' order values
        are recalculated so that index 0 = highest order (renders on top).

        Args:
            project: The target project
            name: Layer name
            layer_type: Layer type (content, avatar, background, etc.)
            insert_at: Position to insert (0=top, None=top by default)

        Returns:
            LayerSummary of the created layer
        """
        import uuid as uuid_module

        timeline = project.timeline_data or {}
        if "layers" not in timeline:
            timeline["layers"] = []

        # Higher order = renders on top; new layers go above existing ones
        existing_orders = [layer.get("order", 0) for layer in timeline["layers"]]
        new_order = max(existing_orders, default=-1) + 1

        new_layer = {
            "id": str(uuid_module.uuid4()),
            "name": name,
            "type": layer_type,
            "order": new_order,
            "clips": [],
            "visible": True,
            "locked": False,
        }

        if insert_at is not None and 0 <= insert_at <= len(timeline["layers"]):
            timeline["layers"].insert(insert_at, new_layer)
        else:
            # Default: insert at index 0 (top of layer list = renders on top)
            timeline["layers"].insert(0, new_layer)

        # Recalculate order values for all layers: index 0 = top = highest order
        for i, layer in enumerate(timeline["layers"]):
            layer["order"] = len(timeline["layers"]) - 1 - i

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

        # Recalculate order values: index 0 = top = highest order
        for i, layer in enumerate(new_layers):
            layer["order"] = len(new_layers) - 1 - i

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
        visible: bool | None = None, locked: bool | None = None,
        _skip_flush: bool = False,
    ) -> LayerSummary | None:
        """Update layer properties."""
        timeline = project.timeline_data or {}

        layer, _ = self._find_layer_by_id(timeline, layer_id)
        if layer is None:
            return None

        if name is not None:
            layer["name"] = name
        if visible is not None:
            layer["visible"] = visible
        if locked is not None:
            layer["locked"] = locked

        if not _skip_flush:
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

    # =========================================================================
    # Audio Track Operations
    # =========================================================================

    async def add_audio_track(
        self,
        project: Project,
        name: str,
        track_type: str = "bgm",
        volume: float = 1.0,
        muted: bool = False,
        ducking_enabled: bool = False,
        insert_at: int | None = None,
    ) -> AudioTrackSummary:
        """Add a new audio track to the project."""
        import uuid as uuid_module

        timeline = project.timeline_data or {}
        if "audio_tracks" not in timeline:
            timeline["audio_tracks"] = []

        new_track = {
            "id": str(uuid_module.uuid4()),
            "name": name,
            "type": track_type,
            "clips": [],
            "volume": volume,
            "muted": muted,
            "ducking_enabled": ducking_enabled,
        }

        if insert_at is not None and 0 <= insert_at <= len(timeline["audio_tracks"]):
            timeline["audio_tracks"].insert(insert_at, new_track)
        else:
            # Default: insert at end (bottom of track list)
            timeline["audio_tracks"].append(new_track)

        project.timeline_data = timeline
        flag_modified(project, "timeline_data")
        await self.db.flush()

        return AudioTrackSummary(
            id=new_track["id"],
            name=new_track["name"],
            type=new_track["type"],
            clip_count=0,
            time_coverage=[],
            volume=new_track["volume"],
            muted=new_track["muted"],
            ducking_enabled=new_track["ducking_enabled"],
        )

    # =========================================================================
    # Marker Operations
    # =========================================================================

    def _find_marker_by_id(
        self, timeline: dict[str, Any], marker_id: str
    ) -> tuple[dict[str, Any] | None, str | None, int | None]:
        """Find a marker by ID (supports partial prefix match).

        Returns:
            Tuple of (marker_dict, full_marker_id, index) or (None, None, None).
        """
        markers = timeline.get("markers", [])
        for idx, marker in enumerate(markers):
            mid = marker.get("id", "")
            if mid == marker_id or mid.startswith(marker_id):
                return marker, mid, idx
        return None, None, None

    async def add_marker(
        self,
        project: Project,
        request: AddMarkerRequest,
    ) -> dict[str, Any]:
        """Add a new marker to the timeline.

        Args:
            project: The target project
            request: The add marker request

        Returns:
            The created marker data
        """
        import uuid as uuid_module

        timeline = project.timeline_data or {}
        if "markers" not in timeline:
            timeline["markers"] = []

        marker_name = request.name or getattr(request, "label", None) or ""
        new_marker = {
            "id": str(uuid_module.uuid4()),
            "time_ms": request.time_ms,
            "name": marker_name,
        }
        if request.color:
            new_marker["color"] = request.color

        timeline["markers"].append(new_marker)

        # Sort markers by time_ms
        timeline["markers"].sort(key=lambda m: m.get("time_ms", 0))

        project.timeline_data = timeline
        flag_modified(project, "timeline_data")
        await self.db.flush()

        return new_marker

    async def update_marker(
        self,
        project: Project,
        marker_id: str,
        request: UpdateMarkerRequest,
    ) -> dict[str, Any]:
        """Update an existing marker.

        Args:
            project: The target project
            marker_id: ID of the marker to update (supports partial prefix)
            request: The update request

        Returns:
            The updated marker data

        Raises:
            MarkerNotFoundError: If marker not found
        """
        timeline = project.timeline_data or {}

        marker, full_marker_id, idx = self._find_marker_by_id(timeline, marker_id)
        if marker is None:
            raise MarkerNotFoundError(marker_id)

        # Track if any changes were made
        changed = False

        # Apply updates only if different from current value
        if request.time_ms is not None and marker.get("time_ms") != request.time_ms:
            marker["time_ms"] = request.time_ms
            changed = True
        if request.name is not None and marker.get("name") != request.name:
            marker["name"] = request.name
            changed = True
        if request.color is not None and marker.get("color") != request.color:
            marker["color"] = request.color
            changed = True

        # Only persist if changes were made
        if changed:
            # Re-sort markers if time changed
            if request.time_ms is not None:
                timeline["markers"].sort(key=lambda m: m.get("time_ms", 0))

            project.timeline_data = timeline
            flag_modified(project, "timeline_data")
            await self.db.flush()

        return marker

    async def delete_marker(
        self,
        project: Project,
        marker_id: str,
    ) -> dict[str, Any]:
        """Delete a marker from the timeline.

        Args:
            project: The target project
            marker_id: ID of the marker to delete (supports partial prefix)

        Returns:
            The deleted marker data

        Raises:
            MarkerNotFoundError: If marker not found
        """
        timeline = project.timeline_data or {}

        marker, full_marker_id, idx = self._find_marker_by_id(timeline, marker_id)
        if marker is None:
            raise MarkerNotFoundError(marker_id)

        # Remove the marker
        timeline["markers"].pop(idx)

        project.timeline_data = timeline
        flag_modified(project, "timeline_data")
        await self.db.flush()

        return marker

    # =========================================================================
    # Keyframe Operations
    # =========================================================================

    async def add_keyframe(
        self,
        project: Project,
        clip_id: str,
        request: AddKeyframeRequest,
    ) -> dict[str, Any]:
        """Add a keyframe to a clip.

        If a keyframe already exists within 100ms of the specified time,
        it will be updated instead.

        Args:
            project: The target project
            clip_id: ID of the clip (supports partial prefix match)
            request: The add keyframe request

        Returns:
            The created/updated keyframe data including generated ID

        Raises:
            ClipNotFoundError: If clip not found
        """
        import uuid as uuid_module

        timeline = project.timeline_data or {}
        clip, layer, full_clip_id = self._find_clip_by_id(timeline, clip_id)
        if clip is None:
            raise ClipNotFoundError(clip_id)

        # Validate time_ms is within clip duration
        clip_duration = clip.get("duration_ms", 0)
        if clip_duration > 0 and request.time_ms > clip_duration:
            logger.warning(
                f"Keyframe time {request.time_ms}ms exceeds clip duration {clip_duration}ms"
            )

        # Ensure keyframes list exists
        if clip.get("keyframes") is None:
            clip["keyframes"] = []

        keyframes: list[dict[str, Any]] = clip["keyframes"]

        # Build keyframe data -- handle transform as object or dict
        try:
            transform = request.transform
            if isinstance(transform, dict):
                transform_data = {
                    "x": transform.get("x", 0),
                    "y": transform.get("y", 0),
                    "scale": transform.get("scale", 1.0),
                    "rotation": transform.get("rotation", 0),
                }
            else:
                transform_data = {
                    "x": transform.x,
                    "y": transform.y,
                    "scale": transform.scale,
                    "rotation": transform.rotation,
                }
        except (AttributeError, TypeError) as exc:
            raise MissingRequiredFieldError(
                f"Invalid transform data: {exc}. "
                "Expected object with x, y, scale, rotation fields."
            ) from exc

        new_keyframe: dict[str, Any] = {
            "id": str(uuid_module.uuid4()),
            "time_ms": request.time_ms,
            "transform": transform_data,
        }
        if request.opacity is not None:
            new_keyframe["opacity"] = request.opacity
        if request.easing is not None:
            new_keyframe["easing"] = request.easing

        # Check if a keyframe exists at this time (within 100ms tolerance)
        existing_idx = None
        for idx, kf in enumerate(keyframes):
            if abs(kf.get("time_ms", 0) - request.time_ms) < 100:
                existing_idx = idx
                break

        if existing_idx is not None:
            # Update existing keyframe, preserve its ID
            new_keyframe["id"] = keyframes[existing_idx].get("id", new_keyframe["id"])
            keyframes[existing_idx] = new_keyframe
        else:
            # Add new keyframe
            keyframes.append(new_keyframe)

        # Sort keyframes by time_ms
        keyframes.sort(key=lambda kf: kf.get("time_ms", 0))
        clip["keyframes"] = keyframes

        project.timeline_data = timeline
        flag_modified(project, "timeline_data")
        await self.db.flush()

        return new_keyframe

    async def delete_keyframe(
        self,
        project: Project,
        clip_id: str,
        keyframe_id: str,
    ) -> dict[str, Any]:
        """Delete a keyframe from a clip.

        Args:
            project: The target project
            clip_id: ID of the clip (supports partial prefix match)
            keyframe_id: ID of the keyframe to delete (supports partial prefix match)

        Returns:
            The deleted keyframe data

        Raises:
            ClipNotFoundError: If clip not found
            KeyframeNotFoundError: If keyframe not found
        """
        timeline = project.timeline_data or {}
        clip, layer, full_clip_id = self._find_clip_by_id(timeline, clip_id)
        if clip is None:
            raise ClipNotFoundError(clip_id)

        keyframes: list[dict[str, Any]] = clip.get("keyframes") or []
        if not keyframes:
            raise KeyframeNotFoundError(keyframe_id)

        # Find the keyframe by ID (supports partial prefix match)
        found_idx = None
        found_keyframe = None
        for idx, kf in enumerate(keyframes):
            kf_id = kf.get("id", "")
            if kf_id == keyframe_id or kf_id.startswith(keyframe_id):
                found_idx = idx
                found_keyframe = kf
                break

        if found_idx is None or found_keyframe is None:
            raise KeyframeNotFoundError(keyframe_id)

        # Remove the keyframe
        keyframes.pop(found_idx)

        # Update clip keyframes (set to None if empty for consistency)
        clip["keyframes"] = keyframes if keyframes else None

        project.timeline_data = timeline
        flag_modified(project, "timeline_data")
        await self.db.flush()

        return found_keyframe

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
            elif operation.operation == "rename_layer":
                return await self._rename_layer(project, operation)
            elif operation.operation == "replace_clip":
                return await self._replace_clip(project, operation)
            elif operation.operation == "close_all_gaps":
                return await self._close_all_gaps(project, operation)
            elif operation.operation == "add_text_with_timing":
                return await self._add_text_with_timing(project, operation)
            elif operation.operation == "distribute_evenly":
                return await self._distribute_evenly(project, operation)
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

        # Find the clip using prefix matching (consistent with validate_only)
        clip_data, layer, full_clip_id = self._find_clip_by_id(
            timeline, operation.target_clip_id
        )
        if clip_data is None or layer is None:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message=f"Clip not found: {operation.target_clip_id}",
            )

        # Find the clip's position in the sorted clips list
        clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
        clip_index = next(
            (i for i, c in enumerate(clips) if c.get("id") == full_clip_id), None
        )

        if clip_index is None or clip_index == 0:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="No previous clip to snap to",
            )

        prev_clip = clips[clip_index - 1]
        prev_end = prev_clip.get("start_ms", 0) + prev_clip.get("duration_ms", 0)
        old_start = clip_data.get("start_ms", 0)

        clip_data["start_ms"] = prev_end
        self._update_project_duration(project)
        await self.db.flush()

        return SemanticOperationResult(
            success=True,
            operation=operation.operation,
            changes_made=[
                f"Moved clip from {old_start}ms to {prev_end}ms (snapped to previous)"
            ],
            affected_clip_ids=[full_clip_id],
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

        # Find the clip using prefix matching (consistent with validate_only)
        clip_data, layer, full_clip_id = self._find_clip_by_id(
            timeline, operation.target_clip_id
        )
        if clip_data is None or layer is None:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message=f"Clip not found: {operation.target_clip_id}",
            )

        # Find the clip's position in the sorted clips list
        clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
        clip_index = next(
            (i for i, c in enumerate(clips) if c.get("id") == full_clip_id), None
        )

        if clip_index is None or clip_index >= len(clips) - 1:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="No next clip to snap",
            )

        next_clip = clips[clip_index + 1]
        clip_end = clip_data.get("start_ms", 0) + clip_data.get("duration_ms", 0)
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

        # Find the layer using prefix matching (consistent with validate_only)
        layer, full_layer_id = self._find_layer_by_id(timeline, target_layer_id)
        if layer is None:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message=f"Layer not found: {target_layer_id}",
            )

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

    async def _rename_layer(
        self, project: Project, operation: SemanticOperation
    ) -> SemanticOperationResult:
        """Rename a layer."""
        target_layer_id = operation.target_layer_id
        new_name = operation.parameters.get("name")

        if not target_layer_id:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="target_layer_id required",
            )

        if not new_name:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="parameters.name required (new layer name)",
            )

        timeline = project.timeline_data or {}

        # Find the layer using prefix matching (consistent with validate_only)
        layer, full_layer_id = self._find_layer_by_id(timeline, target_layer_id)
        if layer is None:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message=f"Layer not found: {target_layer_id}",
            )

        old_name = layer.get("name", "")
        layer["name"] = new_name

        flag_modified(project, "timeline_data")
        await self.db.flush()

        return SemanticOperationResult(
            success=True,
            operation=operation.operation,
            changes_made=[
                f"Renamed layer from '{old_name}' to '{new_name}'"
            ],
        )

    async def _replace_clip(
        self, project: Project, operation: SemanticOperation
    ) -> SemanticOperationResult:
        """Replace a clip's asset while preserving timing and position."""
        if not operation.target_clip_id:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="target_clip_id required",
            )

        new_asset_id = operation.parameters.get("new_asset_id")
        if not new_asset_id:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="parameters.new_asset_id required",
            )

        timeline = project.timeline_data or {}

        # Find the target clip
        clip_data, layer, full_clip_id = self._find_clip_by_id(
            timeline, operation.target_clip_id
        )
        if clip_data is None or layer is None:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message=f"Clip not found: {operation.target_clip_id}",
            )

        old_asset_id = clip_data.get("asset_id")
        changes = []
        affected_ids = [full_clip_id]

        # Replace asset_id on the video clip
        clip_data["asset_id"] = str(new_asset_id)
        changes.append(f"Replaced asset on clip {full_clip_id[:8]}... from {old_asset_id} to {new_asset_id}")

        # Adjust duration if new_duration_ms is provided
        new_duration_ms = operation.parameters.get("new_duration_ms")
        if new_duration_ms is not None:
            old_duration = clip_data.get("duration_ms", 0)
            clip_data["duration_ms"] = new_duration_ms
            changes.append(f"Adjusted duration from {old_duration}ms to {new_duration_ms}ms")

        # Handle linked audio clips via group_id
        group_id = clip_data.get("group_id")
        if group_id:
            linked = self._find_clips_by_group_id(timeline, group_id, exclude_clip_id=full_clip_id)
            for linked_clip, _container, clip_type in linked:
                if clip_type == "audio":
                    # Try to find linked audio for the new asset
                    new_linked_audio_id = operation.parameters.get("new_audio_asset_id")
                    if new_linked_audio_id:
                        linked_clip["asset_id"] = str(new_linked_audio_id)
                        changes.append(f"Replaced linked audio asset on clip {linked_clip.get('id', '')[:8]}...")
                        affected_ids.append(linked_clip.get("id", ""))
                    if new_duration_ms is not None:
                        linked_clip["duration_ms"] = new_duration_ms
                        if linked_clip.get("id", "") not in affected_ids:
                            affected_ids.append(linked_clip.get("id", ""))

        self._update_project_duration(project)
        flag_modified(project, "timeline_data")
        await self.db.flush()

        return SemanticOperationResult(
            success=True,
            operation=operation.operation,
            changes_made=changes,
            affected_clip_ids=affected_ids,
        )

    async def _close_all_gaps(
        self, project: Project, operation: SemanticOperation
    ) -> SemanticOperationResult:
        """Close all gaps in a layer by packing clips tightly from the first clip's start.

        Respects max_end_ms (defaults to project duration_ms) to prevent clips
        from exceeding project boundaries after packing.
        """
        target_layer_id = operation.target_layer_id
        if not target_layer_id:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="target_layer_id required",
            )

        timeline = project.timeline_data or {}

        layer, full_layer_id = self._find_layer_by_id(timeline, target_layer_id)
        if layer is None:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message=f"Layer not found: {target_layer_id}",
            )

        clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
        if not clips:
            return SemanticOperationResult(
                success=True,
                operation=operation.operation,
                changes_made=["No clips in layer"],
            )

        # max_end_ms defaults to project's original duration
        max_end_ms = operation.parameters.get("max_end_ms", project.duration_ms or 0)

        changes = []
        affected_ids = []
        warnings: list[str] = []

        # Start from the first clip's start_ms
        current_end = clips[0].get("start_ms", 0)

        for clip in clips:
            old_start = clip.get("start_ms", 0)
            duration = clip.get("duration_ms", 0)

            if old_start != current_end:
                clip["start_ms"] = current_end
                changes.append(
                    f"Moved clip {clip.get('id', '')[:8]}... from {old_start}ms to {current_end}ms"
                )
                affected_ids.append(clip.get("id", ""))

                # Sync linked audio clips via group_id
                group_id = clip.get("group_id")
                if group_id:
                    linked = self._find_clips_by_group_id(
                        timeline, group_id, exclude_clip_id=clip.get("id")
                    )
                    for linked_clip, _container, clip_type in linked:
                        if clip_type == "audio":
                            linked_clip["start_ms"] = current_end
                            linked_id = linked_clip.get("id", "")
                            if linked_id not in affected_ids:
                                affected_ids.append(linked_id)
                            changes.append(
                                f"Synced linked audio {linked_id[:8]}... to {current_end}ms"
                            )

            current_end = clip.get("start_ms", 0) + duration

        # Check if the last clip exceeds project boundary
        if max_end_ms > 0 and clips:
            last_clip = clips[-1]
            last_end = last_clip.get("start_ms", 0) + last_clip.get("duration_ms", 0)
            if last_end > max_end_ms:
                overflow_ms = last_end - max_end_ms
                # Trim the last clip's duration to fit within project boundary
                old_duration = last_clip.get("duration_ms", 0)
                new_duration = max(0, old_duration - overflow_ms)
                if new_duration > 0:
                    last_clip["duration_ms"] = new_duration
                    clip_id = last_clip.get("id", "")
                    changes.append(
                        f"Trimmed last clip {clip_id[:8]}... duration from {old_duration}ms to {new_duration}ms to fit within project boundary ({max_end_ms}ms)"
                    )
                    if clip_id not in affected_ids:
                        affected_ids.append(clip_id)
                    warnings.append(
                        f"Last clip exceeded project boundary by {overflow_ms}ms and was trimmed"
                    )
                else:
                    warnings.append(
                        f"Last clip would be 0ms after trimming to fit project boundary ({max_end_ms}ms). "
                        f"Consider increasing project duration or removing the clip."
                    )

        if changes:
            self._update_project_duration(project)
            flag_modified(project, "timeline_data")
            await self.db.flush()

        result = SemanticOperationResult(
            success=True,
            operation=operation.operation,
            changes_made=changes if changes else ["No gaps found"],
            affected_clip_ids=affected_ids,
        )
        # Attach warnings if any
        if warnings:
            result.changes_made.extend([f"WARNING: {w}" for w in warnings])
        return result

    async def _add_text_with_timing(
        self, project: Project, operation: SemanticOperation
    ) -> SemanticOperationResult:
        """Add a text clip synced to an existing clip's timing."""
        if not operation.target_clip_id:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="target_clip_id required",
            )

        text = operation.parameters.get("text")
        if not text:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="parameters.text required",
            )

        timeline = project.timeline_data or {}

        # Find the target clip to sync timing
        clip_data, _layer, full_clip_id = self._find_clip_by_id(
            timeline, operation.target_clip_id
        )
        if clip_data is None:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message=f"Clip not found: {operation.target_clip_id}",
            )

        start_ms = clip_data.get("start_ms", 0)
        duration_ms = clip_data.get("duration_ms", 0)

        # Find or create a text layer
        text_layer = None
        for layer in timeline.get("layers", []):
            if layer.get("type") == "text":
                text_layer = layer
                break

        if text_layer is None:
            # Create a new text layer at the top
            text_layer_id = str(uuid.uuid4())
            text_layer = {
                "id": text_layer_id,
                "name": "Text",
                "type": "text",
                "visible": True,
                "locked": False,
                "clips": [],
            }
            timeline.setdefault("layers", []).insert(0, text_layer)

        # Determine y position based on "position" parameter
        position = operation.parameters.get("position", "bottom")
        position_map = {"top": 200, "center": 540, "bottom": 800}
        y_pos = position_map.get(position, 800)

        font_size = operation.parameters.get("font_size", 48)

        # Create text clip
        new_clip_id = str(uuid.uuid4())
        new_clip = {
            "id": new_clip_id,
            "asset_id": None,
            "start_ms": start_ms,
            "duration_ms": duration_ms,
            "in_point_ms": 0,
            "out_point_ms": None,
            "transform": {
                "x": 960,
                "y": y_pos,
                "scale": 1.0,
                "rotation": 0,
                "anchor": "center",
            },
            "effects": {
                "opacity": 1.0,
                "blend_mode": "normal",
            },
            "transition_in": {"type": "none", "duration_ms": 0},
            "transition_out": {"type": "none", "duration_ms": 0},
            "text_content": text,
            "text_style": {"font_size": font_size},
        }

        if "clips" not in text_layer:
            text_layer["clips"] = []
        text_layer["clips"].append(new_clip)

        self._update_project_duration(project)
        flag_modified(project, "timeline_data")
        await self.db.flush()

        return SemanticOperationResult(
            success=True,
            operation=operation.operation,
            changes_made=[
                f"Added text clip '{text[:30]}...' at {start_ms}ms for {duration_ms}ms (position={position}, y={y_pos})"
            ],
            affected_clip_ids=[new_clip_id],
        )

    async def _distribute_evenly(
        self, project: Project, operation: SemanticOperation
    ) -> SemanticOperationResult:
        """Distribute clips evenly in a layer with optional gap."""
        target_layer_id = operation.target_layer_id
        if not target_layer_id:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="target_layer_id required",
            )

        timeline = project.timeline_data or {}

        layer, full_layer_id = self._find_layer_by_id(timeline, target_layer_id)
        if layer is None:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message=f"Layer not found: {target_layer_id}",
            )

        clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
        if not clips:
            return SemanticOperationResult(
                success=True,
                operation=operation.operation,
                changes_made=["No clips in layer"],
            )

        start_ms = operation.parameters.get("start_ms", clips[0].get("start_ms", 0))
        gap_ms = operation.parameters.get("gap_ms", 0)

        changes = []
        affected_ids = []
        current_pos = start_ms

        for clip in clips:
            old_start = clip.get("start_ms", 0)
            duration = clip.get("duration_ms", 0)

            if old_start != current_pos:
                clip["start_ms"] = current_pos
                changes.append(
                    f"Moved clip {clip.get('id', '')[:8]}... from {old_start}ms to {current_pos}ms"
                )
                affected_ids.append(clip.get("id", ""))

                # Sync linked audio clips via group_id
                group_id = clip.get("group_id")
                if group_id:
                    linked = self._find_clips_by_group_id(
                        timeline, group_id, exclude_clip_id=clip.get("id")
                    )
                    for linked_clip, _container, clip_type in linked:
                        if clip_type == "audio":
                            linked_clip["start_ms"] = current_pos
                            linked_id = linked_clip.get("id", "")
                            if linked_id not in affected_ids:
                                affected_ids.append(linked_id)
                            changes.append(
                                f"Synced linked audio {linked_id[:8]}... to {current_pos}ms"
                            )

            current_pos += duration + gap_ms

        if changes:
            self._update_project_duration(project)
            flag_modified(project, "timeline_data")
            await self.db.flush()

        return SemanticOperationResult(
            success=True,
            operation=operation.operation,
            changes_made=changes if changes else ["No clips needed repositioning"],
            affected_clip_ids=affected_ids,
        )

    # =========================================================================
    # Batch Operations
    # =========================================================================

    def _classify_batch_error(self, e: Exception, op: BatchClipOperation) -> dict[str, str]:
        """Classify a batch operation error into structured error info for AI consumption."""
        error_code = "UNKNOWN_ERROR"
        suggestion = "Check the operation parameters and retry."

        if isinstance(e, ClipNotFoundError):
            error_code = "CLIP_NOT_FOUND"
            suggestion = "Use GET /timeline-overview to find valid clip_ids."
        elif isinstance(e, AudioClipNotFoundError):
            error_code = "AUDIO_CLIP_NOT_FOUND"
            suggestion = "Use GET /timeline-overview to find valid audio clip_ids."
        elif isinstance(e, LayerNotFoundError):
            error_code = "LAYER_NOT_FOUND"
            suggestion = "Use GET /timeline-structure to find valid layer_ids."
        elif isinstance(e, AudioTrackNotFoundError):
            error_code = "AUDIO_TRACK_NOT_FOUND"
            suggestion = "Use GET /timeline-structure to find valid track_ids."
        elif isinstance(e, InvalidTimeRangeError):
            error_code = "INVALID_TIMING"
            suggestion = "Check start_ms, duration_ms, in_point_ms, out_point_ms values."
        elif isinstance(e, InvalidClipTypeError):
            error_code = "INVALID_CLIP_TYPE"
            suggestion = f"This operation does not support {op.clip_type} clips."
        elif isinstance(e, MissingRequiredFieldError):
            error_code = "MISSING_REQUIRED_FIELD"
            suggestion = str(e)
        elif isinstance(e, AssetNotFoundError):
            error_code = "ASSET_NOT_FOUND"
            suggestion = "Use GET /asset-catalog to find valid asset_ids."
        elif isinstance(e, ValueError):
            error_code = "INVALID_PARAMETER"
            suggestion = str(e)

        return {
            "error_code": error_code,
            "message": str(e),
            "suggestion": suggestion,
        }

    async def execute_batch_operations(
        self, project: Project, operations: list[BatchClipOperation],
        *, rollback_on_failure: bool = False, continue_on_error: bool = True,
    ) -> BatchOperationResult:
        """Execute multiple clip operations in a batch.

        All individual operations skip flag_modified/flush; a single
        flag_modified + flush is performed at the end for efficiency.

        Args:
            rollback_on_failure: If True, save a deep copy of timeline_data before
                executing. On first failure, restore from the copy and return.
            continue_on_error: If False (and rollback_on_failure is also False),
                stop execution on first failure. Completed operations remain applied.
        """
        import copy

        results: list[dict[str, Any]] = []
        errors: list[str] = []
        successful = 0
        rolled_back = False
        stopped_at_index: int | None = None

        # Save snapshot for potential rollback
        timeline_snapshot = None
        if rollback_on_failure:
            timeline_snapshot = copy.deepcopy(project.timeline_data)

        for idx, op in enumerate(operations):
            try:
                if op.operation == "add":
                    if op.clip_type == "video":
                        # Use UnifiedClipInput for unified format support
                        unified = UnifiedClipInput.model_validate(op.data)
                        req = AddClipRequest(**unified.to_flat_dict())
                        result = await self.add_clip(project, req, _skip_flush=True)
                    else:
                        req = AddAudioClipRequest(**op.data)
                        result = await self.add_audio_clip(project, req, _skip_flush=True)
                    results.append({"operation": "add", "clip_id": result.id if result else None})
                    successful += 1

                elif op.operation == "move":
                    if not op.clip_id:
                        raise ValueError("clip_id required for move operation")
                    move_data = dict(op.data)
                    if "new_start_ms" not in move_data:
                        raise ValueError(
                            "new_start_ms is required for move operation. "
                            "Place it inside the 'data' field: "
                            '{"operation": "move", "clip_id": "...", "data": {"new_start_ms": 5000}}'
                        )
                    if op.clip_type == "video":
                        req = MoveClipRequest(**move_data)
                        await self.move_clip(project, op.clip_id, req, _skip_flush=True)
                    else:
                        req = MoveAudioClipRequest(**move_data)
                        await self.move_audio_clip(project, op.clip_id, req, _skip_flush=True)
                    results.append({"operation": "move", "clip_id": op.clip_id})
                    successful += 1

                elif op.operation == "update_transform":
                    if not op.clip_id:
                        raise ValueError("clip_id required for update_transform")
                    # update_transform only supports video clips
                    if op.clip_type == "audio":
                        raise ValueError("update_transform does not support audio clips")
                    # Use UnifiedTransformInput for unified format support
                    unified = UnifiedTransformInput.model_validate(op.data)
                    req = UpdateClipTransformRequest(**unified.to_flat_dict())
                    await self.update_clip_transform(project, op.clip_id, req, _skip_flush=True)
                    results.append({"operation": "update_transform", "clip_id": op.clip_id})
                    successful += 1

                elif op.operation == "update_effects":
                    if not op.clip_id:
                        raise ValueError("clip_id required for update_effects")
                    # update_effects only supports video clips
                    if op.clip_type == "audio":
                        raise ValueError("update_effects does not support audio clips")
                    req = UpdateClipEffectsRequest(**op.data)
                    await self.update_clip_effects(project, op.clip_id, req, _skip_flush=True)
                    results.append({"operation": "update_effects", "clip_id": op.clip_id})
                    successful += 1

                elif op.operation == "trim":
                    if not op.clip_id:
                        raise ValueError("clip_id required for trim operation")
                    duration_ms = op.data.get("duration_ms")
                    if duration_ms is None:
                        raise ValueError("duration_ms required for trim operation")
                    await self.trim_clip(project, op.clip_id, duration_ms, op.clip_type, _skip_flush=True)
                    results.append({"operation": "trim", "clip_id": op.clip_id})
                    successful += 1

                elif op.operation == "delete":
                    if not op.clip_id:
                        raise ValueError("clip_id required for delete operation")
                    if op.clip_type == "video":
                        await self.delete_clip(project, op.clip_id, _skip_flush=True)
                    else:
                        await self.delete_audio_clip(project, op.clip_id, _skip_flush=True)
                    results.append({"operation": "delete", "clip_id": op.clip_id})
                    successful += 1

                elif op.operation == "update_layer":
                    layer_id = op.layer_id or op.data.get("layer_id")
                    if not layer_id:
                        raise ValueError("layer_id required for update_layer operation")
                    result = await self.update_layer(
                        project,
                        layer_id,
                        name=op.data.get("name"),
                        visible=op.data.get("visible"),
                        locked=op.data.get("locked"),
                        _skip_flush=True,
                    )
                    if result is None:
                        raise ValueError(f"Layer not found: {layer_id}")
                    results.append({"operation": "update_layer", "layer_id": layer_id})
                    successful += 1

            except Exception as e:
                error_info = self._classify_batch_error(e, op)
                errors.append(
                    f"Operation {op.operation} failed [{error_info['error_code']}]: "
                    f"{error_info['message']} (suggestion: {error_info['suggestion']})"
                )
                results.append({
                    "operation": op.operation,
                    "error": error_info["message"],
                    "error_code": error_info["error_code"],
                    "suggestion": error_info["suggestion"],
                })

                if rollback_on_failure and timeline_snapshot is not None:
                    # Restore timeline to pre-batch state
                    project.timeline_data = timeline_snapshot
                    rolled_back = True
                    stopped_at_index = idx
                    # Reset counts: nothing was actually applied
                    successful = 0
                    break
                elif not continue_on_error:
                    # Stop execution but keep completed operations
                    stopped_at_index = idx
                    break

        # Single flag_modified + flush for the entire batch
        flag_modified(project, "timeline_data")
        await self.db.flush()

        return BatchOperationResult(
            success=len(errors) == 0,
            total_operations=len(operations),
            successful_operations=successful,
            failed_operations=len(errors),
            results=results,
            errors=errors,
            rolled_back=rolled_back,
            stopped_at_index=stopped_at_index,
        )

    # =========================================================================
    # Chat (OpenAI Integration)
    # =========================================================================

    async def chat(
        self,
        project: Project,
        message: str,
        history: list[dict[str, str]],
    ) -> ChatResponse:
        """Process a natural language chat message using OpenAI.

        Gathers project context, sends to OpenAI Chat Completions API,
        parses any proposed actions, and optionally executes them.
        """
        settings = get_settings()

        if not settings.openai_api_key:
            return ChatResponse(
                message="OpenAI APIキーが設定されていません。バックエンドの環境変数 OPENAI_API_KEY を設定してください。",
                actions=[],
            )

        # Gather project context
        project_context = self._build_project_context(project)

        # Build system prompt
        system_prompt = self._build_chat_system_prompt(project_context)

        # Build messages for OpenAI
        openai_messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]
        for h in history:
            openai_messages.append({"role": h["role"], "content": h["content"]})
        openai_messages.append({"role": "user", "content": message})

        try:
            import openai

            client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=openai_messages,  # type: ignore[arg-type]
                temperature=0.7,
                max_tokens=16384,
            )

            ai_content = response.choices[0].message.content or ""

            # Try to parse structured actions from the response
            actions = await self._parse_and_execute_actions(project, ai_content)

            # Clean the message (remove JSON action blocks if present)
            clean_message = self._clean_ai_message(ai_content)

            return ChatResponse(
                message=clean_message,
                actions=actions,
            )

        except openai.AuthenticationError:
            return ChatResponse(
                message="OpenAI APIキーが無効です。正しいキーを設定してください。",
                actions=[],
            )
        except openai.RateLimitError:
            return ChatResponse(
                message="OpenAI APIのレート制限に達しました。しばらく待ってから再試行してください。",
                actions=[],
            )
        except openai.APITimeoutError:
            return ChatResponse(
                message="OpenAI APIがタイムアウトしました。再試行してください。",
                actions=[],
            )
        except Exception as e:
            logger.exception("Chat API call failed")
            return ChatResponse(
                message=f"AIとの通信中にエラーが発生しました: {str(e)}",
                actions=[],
            )

    def _build_project_context(self, project: Project) -> str:
        """Build a concise project context string for the AI."""
        timeline = project.timeline_data or {}
        layers = timeline.get("layers", [])
        audio_tracks = timeline.get("audio_tracks", [])

        total_video_clips = sum(len(layer.get("clips", [])) for layer in layers)
        total_audio_clips = sum(len(track.get("clips", [])) for track in audio_tracks)

        context_parts = [
            f"プロジェクト名: {project.name}",
            f"解像度: {project.width}x{project.height}",
            f"FPS: {project.fps}",
            f"長さ: {project.duration_ms}ms ({project.duration_ms / 1000:.1f}秒)",
            f"レイヤー数: {len(layers)}",
            f"オーディオトラック数: {len(audio_tracks)}",
            f"ビデオクリップ合計: {total_video_clips}",
            f"オーディオクリップ合計: {total_audio_clips}",
        ]

        # Layer details
        for layer in layers:
            clips = layer.get("clips", [])
            clip_info = []
            for clip in sorted(clips, key=lambda c: c.get("start_ms", 0)):
                start = clip.get("start_ms", 0)
                dur = clip.get("duration_ms", 0)
                clip_info.append(
                    f"    clip_id={clip.get('id', '?')[:8]}... "
                    f"start={start}ms dur={dur}ms"
                )
            context_parts.append(
                f"レイヤー '{layer.get('name', '')}' (id={layer.get('id', '')[:8]}..., "
                f"type={layer.get('type', 'content')}, clips={len(clips)}):"
            )
            if clip_info:
                context_parts.extend(clip_info[:10])  # Limit to 10 clips per layer
                if len(clip_info) > 10:
                    context_parts.append(f"    ... 他 {len(clip_info) - 10} クリップ")

        # Audio track details
        for track in audio_tracks:
            clips = track.get("clips", [])
            context_parts.append(
                f"オーディオトラック '{track.get('name', '')}' "
                f"(id={track.get('id', '')[:8]}..., type={track.get('type', 'narration')}, "
                f"clips={len(clips)})"
            )

        return "\n".join(context_parts)

    def _build_chat_system_prompt(self, project_context: str) -> str:
        """Build the system prompt for the chat."""
        return f"""あなたはUdemy講座制作のための動画編集AIアシスタントです。
ユーザーのタイムライン編集を自然言語で支援します。

## 現在のプロジェクト情報
{project_context}

## 利用可能な操作
以下の操作をユーザーの指示に基づいて提案・実行できます:

1. **snap_to_previous**: クリップを前のクリップの末尾に合わせる (target_clip_id必須)
2. **snap_to_next**: 次のクリップを対象クリップの末尾に合わせる (target_clip_id必須)
3. **close_gap**: レイヤー内のギャップを詰める (target_layer_id必須)
4. **auto_duck_bgm**: BGMのダッキングを有効化
5. **rename_layer**: レイヤー名変更 (target_layer_id必須, parameters: {{"name": "新しい名前"}})

## アクションの提案方法
操作を実行する場合は、応答テキストの最後に以下のJSON形式でアクションブロックを含めてください:

```actions
[
  {{
    "operation": "操作名",
    "target_clip_id": "クリップID（必要な場合）",
    "target_layer_id": "レイヤーID（必要な場合）",
    "parameters": {{}}
  }}
]
```

## 注意事項
- 日本語で応答してください
- 操作できない場合やクリップ/レイヤーが特定できない場合は、ユーザーに確認してください
- プロジェクト情報を参照して具体的なIDを使ってください
- 簡潔でわかりやすい応答を心がけてください
"""

    async def _parse_and_execute_actions(
        self, project: Project, ai_content: str
    ) -> list[ChatAction]:
        """Parse action blocks from AI response and execute them."""
        actions: list[ChatAction] = []

        # Look for ```actions ... ``` block
        action_match = re.search(r"```actions\s*\n(.*?)```", ai_content, re.DOTALL)
        if not action_match:
            return actions

        try:
            action_list = json.loads(action_match.group(1))
        except json.JSONDecodeError:
            logger.warning("Failed to parse action JSON from AI response")
            return actions

        if not isinstance(action_list, list):
            return actions

        for action_data in action_list:
            if not isinstance(action_data, dict):
                continue

            operation_name = action_data.get("operation", "")

            try:
                op = SemanticOperation(
                    operation=operation_name,
                    target_clip_id=action_data.get("target_clip_id"),
                    target_layer_id=action_data.get("target_layer_id"),
                    target_track_id=action_data.get("target_track_id"),
                    parameters=action_data.get("parameters", {}),
                )
                result = await self.execute_semantic_operation(project, op)

                if result.success:
                    desc = ", ".join(result.changes_made) if result.changes_made else operation_name
                    actions.append(
                        ChatAction(
                            type="semantic",
                            description=desc,
                            applied=True,
                        )
                    )
                else:
                    actions.append(
                        ChatAction(
                            type="semantic",
                            description=f"{operation_name}: {result.error_message}",
                            applied=False,
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to execute action {operation_name}: {e}")
                actions.append(
                    ChatAction(
                        type="semantic",
                        description=f"{operation_name}: {str(e)}",
                        applied=False,
                    )
                )

        return actions

    def _clean_ai_message(self, ai_content: str) -> str:
        """Remove action JSON blocks from AI message for clean display."""
        cleaned = re.sub(r"```actions\s*\n.*?```", "", ai_content, flags=re.DOTALL)
        return cleaned.strip()

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
            raise AssetNotFoundError(asset_id)

        # Validate against asset duration if known
        if asset.duration_ms:
            effective_out = out_point_ms if out_point_ms is not None else asset.duration_ms

            if effective_out > asset.duration_ms:
                raise InvalidTimeRangeError(
                    message=f"out_point_ms ({effective_out}) exceeds asset duration ({asset.duration_ms})",
                    field="out_point_ms",
                )

            if in_point_ms >= effective_out:
                raise InvalidTimeRangeError(
                    message=f"in_point_ms ({in_point_ms}) must be less than out_point_ms ({effective_out})",
                    start_ms=in_point_ms,
                    end_ms=effective_out,
                    field="in_point_ms",
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

    # =========================================================================
    # Chat: Natural Language Instructions via Multiple AI Providers
    # =========================================================================

    async def handle_chat(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        provider: str | None = None,
    ) -> ChatResponse:
        """Process a natural language chat message using the specified AI provider.

        Supports OpenAI, Gemini, and Anthropic APIs.
        Uses project-level API key if available, otherwise falls back to environment settings.
        """
        settings = get_settings()

        # Determine which provider to use (project setting > request > default)
        project_provider = getattr(project, 'ai_provider', None)
        active_provider = project_provider or provider or settings.default_ai_provider

        # Use project-level API key if available, otherwise use environment settings
        project_api_key = getattr(project, 'ai_api_key', None)

        # Build timeline context with assets for filename → UUID mapping
        timeline = project.timeline_data or {}
        assets = await self._get_project_assets(project.id)
        context = self._build_chat_context(project, timeline, assets)
        system_prompt = self._build_chat_system_prompt(context)

        # Route to the appropriate provider (project API key takes priority)
        if active_provider == "openai":
            api_key = project_api_key or settings.openai_api_key
            return await self._chat_with_openai(project, message, history, system_prompt, api_key)
        elif active_provider == "gemini":
            api_key = project_api_key or settings.gemini_api_key
            return await self._chat_with_gemini(project, message, history, system_prompt, api_key)
        elif active_provider == "anthropic":
            api_key = project_api_key or settings.anthropic_api_key
            return await self._chat_with_anthropic(project, message, history, system_prompt, api_key)
        else:
            return ChatResponse(
                message=f"不明なAIプロバイダーです: {active_provider}",
                actions=[],
            )

    async def _chat_with_openai(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
    ) -> ChatResponse:
        """Process chat using OpenAI API."""
        if not api_key:
            return ChatResponse(
                message="OpenAI APIキーが設定されていません。backend/.env に OPENAI_API_KEY を設定してください。",
                actions=[],
            )

        messages = [{"role": "system", "content": system_prompt}]
        for msg in history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": message})

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4o",
                        "max_tokens": 16384,
                        "messages": messages,
                    },
                )

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"OpenAI API error: {response.status_code} - {error_detail}")
                return ChatResponse(
                    message=f"OpenAI APIエラー (HTTP {response.status_code})",
                    actions=[],
                )

            result = response.json()
            assistant_text = result["choices"][0]["message"]["content"]
            return await self._process_ai_response(project, assistant_text)

        except httpx.TimeoutException:
            logger.error("OpenAI API timeout")
            return ChatResponse(
                message="OpenAI APIがタイムアウトしました。もう一度お試しください。",
                actions=[],
            )
        except Exception as e:
            logger.exception("OpenAI chat processing error")
            return ChatResponse(
                message=f"OpenAI エラー: {str(e)}",
                actions=[],
            )

    async def _chat_with_gemini(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
    ) -> ChatResponse:
        """Process chat using Google Gemini API."""
        if not api_key:
            return ChatResponse(
                message="Gemini APIキーが設定されていません。backend/.env に GEMINI_API_KEY を設定してください。",
                actions=[],
            )

        # Build Gemini-formatted messages with system instruction
        contents = []
        if history:
            for i, msg in enumerate(history[-10:]):
                role = "user" if msg.role == "user" else "model"
                text = msg.content
                if i == 0 and msg.role == "user":
                    text = f"[System Instructions]\n{system_prompt}\n\n[User Message]\n{msg.content}"
                contents.append({"role": role, "parts": [{"text": text}]})

        # Add current message
        if not contents:
            contents.append({
                "role": "user",
                "parts": [{"text": f"[System Instructions]\n{system_prompt}\n\n[User Message]\n{message}"}]
            })
        else:
            contents.append({"role": "user", "parts": [{"text": message}]})

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-preview:generateContent?key={api_key}",
                    headers={"Content-Type": "application/json"},
                    json={
                        "contents": contents,
                        "generationConfig": {
                            "maxOutputTokens": 8192,
                            "temperature": 0.7,
                        },
                    },
                )

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"Gemini API error: {response.status_code} - {error_detail}")
                return ChatResponse(
                    message=f"Gemini APIエラー (HTTP {response.status_code})",
                    actions=[],
                )

            result = response.json()
            logger.info(f"[Gemini] Response keys: {result.keys()}")
            candidates = result.get("candidates", [])
            if not candidates:
                logger.error(f"[Gemini] No candidates. Full response: {result}")
                return ChatResponse(
                    message="Geminiからの応答がありませんでした。",
                    actions=[],
                )

            # Check for finish reason
            finish_reason = candidates[0].get("finishReason", "UNKNOWN")
            logger.info(f"[Gemini] Finish reason: {finish_reason}")

            assistant_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            logger.info(f"[Gemini] Assistant text length: {len(assistant_text)}")
            if not assistant_text:
                logger.error(f"[Gemini] Empty text. Candidate: {candidates[0]}")
            return await self._process_ai_response(project, assistant_text)

        except httpx.TimeoutException:
            logger.error("Gemini API timeout")
            return ChatResponse(
                message="Gemini APIがタイムアウトしました。もう一度お試しください。",
                actions=[],
            )
        except Exception as e:
            logger.exception("Gemini chat processing error")
            return ChatResponse(
                message=f"Gemini エラー: {str(e)}",
                actions=[],
            )

    async def _chat_with_anthropic(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
    ) -> ChatResponse:
        """Process chat using Anthropic Claude API."""
        if not api_key:
            return ChatResponse(
                message="Anthropic APIキーが設定されていません。backend/.env に ANTHROPIC_API_KEY を設定してください。",
                actions=[],
            )

        # Build Anthropic-formatted messages
        messages = []
        for msg in history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": message})

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 16384,
                        "system": system_prompt,
                        "messages": messages,
                    },
                )

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"Anthropic API error: {response.status_code} - {error_detail}")
                return ChatResponse(
                    message=f"Anthropic APIエラー (HTTP {response.status_code})",
                    actions=[],
                )

            result = response.json()
            content_blocks = result.get("content", [])
            assistant_text = "".join(
                block.get("text", "") for block in content_blocks if block.get("type") == "text"
            )
            return await self._process_ai_response(project, assistant_text)

        except httpx.TimeoutException:
            logger.error("Anthropic API timeout")
            return ChatResponse(
                message="Anthropic APIがタイムアウトしました。もう一度お試しください。",
                actions=[],
            )
        except Exception as e:
            logger.exception("Anthropic chat processing error")
            return ChatResponse(
                message=f"Anthropic エラー: {str(e)}",
                actions=[],
            )

    async def _process_ai_response(
        self, project: Project, assistant_text: str
    ) -> ChatResponse:
        """Process AI response and extract/execute operations."""
        logger.info(f"[AI Response] Raw text length: {len(assistant_text)}")
        logger.info(f"[AI Response] First 500 chars: {assistant_text[:500]}")

        actions = []
        operations_json = self._extract_json_block(assistant_text)
        logger.info(f"[AI Response] Extracted JSON: {operations_json is not None}")
        if operations_json:
            actions = await self._execute_chat_operations(project, operations_json)
            clean_message = self._remove_json_block(assistant_text)
        else:
            clean_message = assistant_text

        # Check if any actions were successfully applied
        any_applied = any(action.applied for action in actions) if actions else False

        logger.info(f"[AI Response] Clean message length: {len(clean_message.strip())}")
        logger.info(f"[AI Response] Actions count: {len(actions)}, any_applied: {any_applied}")

        return ChatResponse(
            message=clean_message.strip(),
            actions=actions,
            actions_applied=any_applied,
        )

    async def _get_project_assets(self, project_id: uuid.UUID) -> list[Asset]:
        """Fetch all assets for a project."""
        result = await self.db.execute(
            select(Asset)
            .where(Asset.project_id == project_id)
            .order_by(Asset.type, Asset.name)
        )
        return list(result.scalars().all())

    def _build_chat_context(
        self, project: Project, timeline: dict, assets: list[Asset] | None = None
    ) -> str:
        """Build a compact timeline context string for Claude."""
        # Build assets section
        assets_info = []
        if assets:
            for asset in assets:
                assets_info.append(
                    f"  - name=\"{asset.name}\" type={asset.type} "
                    f"asset_id={asset.id}"
                )

        layers_info = []
        for layer in timeline.get("layers", []):
            clips = layer.get("clips", [])
            clip_summaries = []
            for c in clips:
                clip_summaries.append(
                    f"  - id={c.get('id','?')[:8]} start={c.get('start_ms',0)}ms "
                    f"dur={c.get('duration_ms',0)}ms "
                    f"asset={c.get('asset_id','none')[:8] if c.get('asset_id') else 'shape/text'}"
                )
            layers_info.append(
                f"Layer '{layer.get('name','')}' (id={layer.get('id','')[:8]}, "
                f"clips={len(clips)}, locked={layer.get('locked',False)}):\n"
                + "\n".join(clip_summaries)
            )

        tracks_info = []
        for track in timeline.get("audio_tracks", []):
            clips = track.get("clips", [])
            clip_summaries = []
            for c in clips:
                clip_summaries.append(
                    f"  - id={c.get('id','?')[:8]} start={c.get('start_ms',0)}ms "
                    f"dur={c.get('duration_ms',0)}ms"
                )
            tracks_info.append(
                f"Audio '{track.get('type','')}' (id={track.get('id','')[:8]}, clips={len(clips)}):\n"
                + "\n".join(clip_summaries)
            )

        context_parts = [
            f"Project: {project.name}",
            f"Duration: {project.duration_ms}ms",
            f"Resolution: {project.width}x{project.height}",
        ]

        if assets_info:
            context_parts.append("\n## Available Assets (use asset_id for operations)")
            context_parts.append("\n".join(assets_info))

        context_parts.append("\n## Video Layers")
        context_parts.append("\n".join(layers_info) if layers_info else "  (empty)")

        context_parts.append("\n## Audio Tracks")
        context_parts.append("\n".join(tracks_info) if tracks_info else "  (empty)")

        return "\n".join(context_parts)

    def _build_chat_system_prompt(self, context: str) -> str:
        """Build the system prompt for Claude."""
        return f"""あなたは動画編集アプリ「douga」のAIアシスタントです。
ユーザーのタイムライン編集指示を理解し、実行可能な操作に変換します。

## 現在のプロジェクト状態
{context}

## 実行可能な操作
操作を実行する場合は、応答の最後に以下のJSON形式で記述してください:

```operations
[
  {{
    "type": "batch",
    "operations": [
      {{
        "operation": "add",
        "clip_type": "video",
        "data": {{
          "layer_id": "配置先レイヤーID",
          "start_ms": 0,
          "duration_ms": 1000,
          "text_content": "表示するテキスト（テキストクリップの場合）",
          "asset_id": "アセットのUUID（動画/画像の場合、必須）"
        }}
      }},
      {{
        "operation": "move",
        "clip_id": "クリップID",
        "clip_type": "video",
        "data": {{"new_start_ms": 0, "new_layer_id": "レイヤーID（省略可）"}}
      }},
      {{
        "operation": "move",
        "clip_id": "クリップID",
        "clip_type": "audio",
        "data": {{"new_start_ms": 0, "new_track_id": "トラックID（省略可）"}}
      }},
      {{
        "operation": "trim",
        "clip_id": "クリップID",
        "clip_type": "video|audio",
        "data": {{"duration_ms": 1000}}
      }},
      {{
        "operation": "delete",
        "clip_id": "クリップID",
        "clip_type": "video|audio"
      }},
      {{
        "operation": "update_transform",
        "clip_id": "クリップID",
        "data": {{"x": 0, "y": 0, "scale": 1.0, "rotation": 0}}
      }}
    ]
  }}
]
```

## 重要: asset_id について
- **asset_id は必ず UUID 形式で指定してください**（例: "6d591866-a838-46ff-a356-442b2bf2afeb"）
- ファイル名（例: "video.mp4"）は使用できません
- 上記「Available Assets」セクションからファイル名に対応する asset_id を確認してください
- ユーザーがファイル名で指定した場合は、対応する asset_id を使用してください

## 重要: dataフィールドの形式
- add操作: {{"layer_id": "ID", "start_ms": ミリ秒, "duration_ms": ミリ秒, "asset_id": "UUID"}}
- move操作: {{"new_start_ms": ミリ秒, "new_layer_id": "ID"}} ※new_start_msは必須
- trim操作: {{"duration_ms": ミリ秒}} ※クリップの長さを変更
- update_transform: {{"x": 数値, "y": 数値, "scale": 数値, "rotation": 数値}}
- delete操作: dataは不要

## ルール
- 日本語で応答してください
- 操作を実行する場合は必ずJSON形式で出力してください
- 情報の質問のみの場合はJSONは不要です
- ユーザーの指示が曖昧な場合は確認してください
- クリップIDは短縮形（先頭8文字）で表示されていますが、操作時はフルIDが必要です
  短縮IDしかない場合はそのまま使用してください"""

    def _extract_json_block(self, text: str) -> list | None:
        """Extract JSON operations block from Claude's response."""
        import re
        match = re.search(r"```operations\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                logger.warning("Failed to parse operations JSON")
                return None
        return None

    def _remove_json_block(self, text: str) -> str:
        """Remove JSON operations block from the response text."""
        import re
        return re.sub(r"```operations\s*\n.*?\n```", "", text, flags=re.DOTALL)

    async def _execute_chat_operations(
        self, project: Project, operations: list[dict]
    ) -> list[ChatAction]:
        """Execute parsed operations from Claude's response."""
        actions = []
        for op in operations:
            op_type = op.get("type", "")
            try:
                if op_type == "semantic":
                    sem_op = SemanticOperation(
                        operation=op.get("operation", ""),
                        target_clip_id=op.get("target_clip_id"),
                        target_layer_id=op.get("target_layer_id"),
                        target_track_id=op.get("target_track_id"),
                        parameters=op.get("parameters", {}),
                    )
                    result = await self.execute_semantic_operation(project, sem_op)
                    actions.append(ChatAction(
                        type="semantic",
                        description=", ".join(result.changes_made) if result.changes_made else result.error_message or op.get("operation", ""),
                        applied=result.success,
                    ))
                elif op_type == "batch":
                    batch_ops = []
                    for batch_op in op.get("operations", []):
                        logger.info(f"[AI Batch] Preparing operation: {batch_op}")
                        batch_ops.append(BatchClipOperation(
                            operation=batch_op.get("operation", ""),
                            clip_id=batch_op.get("clip_id"),
                            clip_type=batch_op.get("clip_type", "video"),
                            data=batch_op.get("data", {}),
                        ))
                    if batch_ops:
                        result = await self.execute_batch_operations(project, batch_ops)
                        logger.info(f"[AI Batch] Result: success={result.success}, {result.successful_operations}/{result.total_operations}")
                        if result.errors:
                            logger.error(f"[AI Batch] Errors: {result.errors}")
                        if result.results:
                            logger.info(f"[AI Batch] Details: {result.results}")
                        # Include error in description for debugging
                        if result.errors:
                            desc = f"{result.successful_operations}/{result.total_operations} 操作完了: {result.errors[0]}"
                        else:
                            desc = f"{result.successful_operations}/{result.total_operations} 操作完了"
                        actions.append(ChatAction(
                            type="batch",
                            description=desc,
                            applied=result.success,
                        ))
                else:
                    actions.append(ChatAction(
                        type=op_type,
                        description=f"不明な操作タイプ: {op_type}",
                        applied=False,
                    ))
            except Exception as e:
                logger.exception(f"Failed to execute chat operation: {op_type}")
                actions.append(ChatAction(
                    type=op_type,
                    description=f"実行エラー: {str(e)}",
                    applied=False,
                ))
        return actions

    # =========================================================================
    # Chat Streaming: Server-Sent Events
    # =========================================================================

    async def handle_chat_stream(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        provider: str | None = None,
    ):
        """Process a chat message with streaming response.

        Yields SSE-formatted events:
        - chunk: Text chunks as they arrive
        - actions: JSON-encoded actions after execution
        - done: Completion signal
        - error: Error message if something fails

        Returns an async generator for use with StreamingResponse.
        """
        settings = get_settings()

        # Determine which provider to use
        project_provider = getattr(project, 'ai_provider', None)
        active_provider = project_provider or provider or settings.default_ai_provider

        # Use project-level API key if available
        project_api_key = getattr(project, 'ai_api_key', None)

        # Build timeline context with assets for filename → UUID mapping
        timeline = project.timeline_data or {}
        assets = await self._get_project_assets(project.id)
        context = self._build_chat_context(project, timeline, assets)
        system_prompt = self._build_chat_system_prompt(context)

        # Route to the appropriate provider
        if active_provider == "openai":
            api_key = project_api_key or settings.openai_api_key
            async for event in self._stream_openai(project, message, history, system_prompt, api_key):
                yield event
        elif active_provider == "gemini":
            api_key = project_api_key or settings.gemini_api_key
            async for event in self._stream_gemini(project, message, history, system_prompt, api_key):
                yield event
        elif active_provider == "anthropic":
            api_key = project_api_key or settings.anthropic_api_key
            async for event in self._stream_anthropic(project, message, history, system_prompt, api_key):
                yield event
        else:
            yield f"event: error\ndata: {json.dumps({'message': f'不明なAIプロバイダーです: {active_provider}'})}\n\n"
            yield "event: done\ndata: {}\n\n"

    async def _stream_openai(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
    ):
        """Stream chat response from OpenAI."""
        if not api_key:
            yield f"event: error\ndata: {json.dumps({'message': 'OpenAI APIキーが設定されていません。'})}\n\n"
            yield "event: done\ndata: {}\n\n"
            return

        messages = [{"role": "system", "content": system_prompt}]
        for msg in history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": message})

        try:
            full_response = ""
            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream(
                    "POST",
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4o",
                        "max_tokens": 16384,
                        "messages": messages,
                        "stream": True,
                    },
                ) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        logger.error(f"OpenAI API error: {response.status_code} - {error_text}")
                        yield f"event: error\ndata: {json.dumps({'message': f'OpenAI APIエラー (HTTP {response.status_code})'})}\n\n"
                        yield "event: done\ndata: {}\n\n"
                        return

                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    full_response += content
                                    yield f"event: chunk\ndata: {json.dumps({'text': content})}\n\n"
                            except json.JSONDecodeError:
                                continue

            # Process actions from the full response
            actions_event = await self._process_stream_actions(project, full_response)
            if actions_event:
                yield actions_event

        except httpx.TimeoutException:
            logger.error("OpenAI API timeout during streaming")
            yield f"event: error\ndata: {json.dumps({'message': 'OpenAI APIがタイムアウトしました。'})}\n\n"
        except Exception as e:
            logger.exception("OpenAI streaming error")
            yield f"event: error\ndata: {json.dumps({'message': f'OpenAI エラー: {str(e)}'})}\n\n"
        finally:
            yield "event: done\ndata: {}\n\n"

    async def _stream_gemini(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
    ):
        """Stream chat response from Gemini."""
        if not api_key:
            yield f"event: error\ndata: {json.dumps({'message': 'Gemini APIキーが設定されていません。'})}\n\n"
            yield "event: done\ndata: {}\n\n"
            return

        # Build Gemini-formatted messages
        contents = []
        if history:
            for i, msg in enumerate(history[-10:]):
                role = "user" if msg.role == "user" else "model"
                text = msg.content
                if i == 0 and msg.role == "user":
                    text = f"[System Instructions]\n{system_prompt}\n\n[User Message]\n{msg.content}"
                contents.append({"role": role, "parts": [{"text": text}]})

        if not contents:
            contents.append({
                "role": "user",
                "parts": [{"text": f"[System Instructions]\n{system_prompt}\n\n[User Message]\n{message}"}]
            })
        else:
            contents.append({"role": "user", "parts": [{"text": message}]})

        try:
            full_response = ""
            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream(
                    "POST",
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-preview:streamGenerateContent?key={api_key}&alt=sse",
                    headers={"Content-Type": "application/json"},
                    json={
                        "contents": contents,
                        "generationConfig": {
                            "maxOutputTokens": 8192,
                            "temperature": 0.7,
                        },
                    },
                ) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        logger.error(f"Gemini API error: {response.status_code} - {error_text}")
                        yield f"event: error\ndata: {json.dumps({'message': f'Gemini APIエラー (HTTP {response.status_code})'})}\n\n"
                        yield "event: done\ndata: {}\n\n"
                        return

                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            try:
                                chunk = json.loads(data)
                                candidates = chunk.get("candidates", [])
                                if candidates:
                                    text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                                    if text:
                                        full_response += text
                                        yield f"event: chunk\ndata: {json.dumps({'text': text})}\n\n"
                            except json.JSONDecodeError:
                                continue

            # Process actions from the full response
            actions_event = await self._process_stream_actions(project, full_response)
            if actions_event:
                yield actions_event

        except httpx.TimeoutException:
            logger.error("Gemini API timeout during streaming")
            yield f"event: error\ndata: {json.dumps({'message': 'Gemini APIがタイムアウトしました。'})}\n\n"
        except Exception as e:
            logger.exception("Gemini streaming error")
            yield f"event: error\ndata: {json.dumps({'message': f'Gemini エラー: {str(e)}'})}\n\n"
        finally:
            yield "event: done\ndata: {}\n\n"

    async def _stream_anthropic(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
    ):
        """Stream chat response from Anthropic Claude."""
        if not api_key:
            yield f"event: error\ndata: {json.dumps({'message': 'Anthropic APIキーが設定されていません。'})}\n\n"
            yield "event: done\ndata: {}\n\n"
            return

        messages = []
        for msg in history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": message})

        try:
            full_response = ""
            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream(
                    "POST",
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 16384,
                        "system": system_prompt,
                        "messages": messages,
                        "stream": True,
                    },
                ) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        logger.error(f"Anthropic API error: {response.status_code} - {error_text}")
                        yield f"event: error\ndata: {json.dumps({'message': f'Anthropic APIエラー (HTTP {response.status_code})'})}\n\n"
                        yield "event: done\ndata: {}\n\n"
                        return

                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            try:
                                chunk = json.loads(data)
                                event_type = chunk.get("type", "")
                                if event_type == "content_block_delta":
                                    delta = chunk.get("delta", {})
                                    if delta.get("type") == "text_delta":
                                        text = delta.get("text", "")
                                        if text:
                                            full_response += text
                                            yield f"event: chunk\ndata: {json.dumps({'text': text})}\n\n"
                            except json.JSONDecodeError:
                                continue

            # Process actions from the full response
            actions_event = await self._process_stream_actions(project, full_response)
            if actions_event:
                yield actions_event

        except httpx.TimeoutException:
            logger.error("Anthropic API timeout during streaming")
            yield f"event: error\ndata: {json.dumps({'message': 'Anthropic APIがタイムアウトしました。'})}\n\n"
        except Exception as e:
            logger.exception("Anthropic streaming error")
            yield f"event: error\ndata: {json.dumps({'message': f'Anthropic エラー: {str(e)}'})}\n\n"
        finally:
            yield "event: done\ndata: {}\n\n"

    async def _process_stream_actions(self, project: Project, full_response: str) -> str | None:
        """Process and execute actions from streamed response, returning action event string."""
        operations_json = self._extract_json_block(full_response)
        if operations_json:
            actions = await self._execute_chat_operations(project, operations_json)
            if actions:
                actions_data = [
                    {"type": a.type, "description": a.description, "applied": a.applied}
                    for a in actions
                ]
                logger.info(f"[Stream] Executed {len(actions)} actions")
                return f"event: actions\ndata: {json.dumps(actions_data)}\n\n"
        return None
