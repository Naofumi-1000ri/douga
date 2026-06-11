"""Read/query helpers extracted from AIService."""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from sqlalchemy import select

from src.models.asset import Asset
from src.models.project import Project
from src.schemas.ai import (
    AssetInfo,
    AudioTrackSummary,
    ClipAtTime,
    ClipNeighbor,
    ClipTiming,
    CropDetails,
    EffectsDetails,
    L1ProjectOverview,
    L2AssetCatalog,
    L2TimelineAtTime,
    L2TimelineStructure,
    L3AudioClipDetails,
    L3ClipDetails,
    L25TimelineOverview,
    LayerSummary,
    OverviewAudioTrack,
    OverviewClip,
    OverviewGap,
    OverviewLayer,
    OverviewOverlap,
    ProjectSummary,
    TextStyleDetails,
    TimelineSummary,
    TransformDetails,
    TransitionDetails,
    VolumeKeyframeResponse,
)

logger = logging.getLogger(__name__)


class ProjectQueryMixin:
    """L1/L2/L3 read helpers for AIService."""

    # =========================================================================
    # L1: Summary Level
    # =========================================================================

    async def get_project_overview(self: Any, project: Project) -> L1ProjectOverview:
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

    async def get_timeline_structure(self: Any, project: Project) -> L2TimelineStructure:
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

    async def get_timeline_at_time(self: Any, project: Project, time_ms: int) -> L2TimelineAtTime:
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

    async def get_asset_catalog(self: Any, project: Project) -> L2AssetCatalog:
        """Get L2 asset catalog.

        Lists available assets with usage counts.
        """
        # Query assets for this project
        result = await self.db.execute(
            select(Asset).where(Asset.project_id == project.id).where(Asset.is_internal == False)  # noqa: E712
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
            select(Asset).where(
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

            # For images, populate duration_ms with suggested_display_duration_ms
            # so naive agents always see a non-null duration_ms for every asset.
            suggested_display = 5000 if asset.type == "image" else None
            effective_duration = asset.duration_ms
            if asset.type == "image" and effective_duration is None:
                effective_duration = suggested_display

            asset_infos.append(
                AssetInfo(
                    id=asset.id,
                    name=asset.name,
                    type=asset.type,
                    subtype=asset.subtype,
                    duration_ms=effective_duration,
                    width=asset.width,
                    height=asset.height,
                    usage_count=asset_usage.get(str(asset.id), 0),
                    linked_audio_id=linked_audio.id if linked_audio else None,
                    audio_classification=audio_classification,
                    has_transcription=has_transcription,
                    suggested_display_duration_ms=suggested_display,
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
        self: Any, project: Project, *, include_snapshot: bool = False
    ) -> L25TimelineOverview:
        """Get L2.5 timeline overview (~2000 tokens).

        Full timeline snapshot: clips with asset names, gap/overlap detection.
        One request gives AI everything it needs to understand the timeline.

        Args:
            project: The project to generate the overview for.
            include_snapshot: If True, include a base64-encoded JPEG snapshot
                of the timeline layout. Defaults to False to keep response compact.
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
                    select(Asset.id, Asset.name).where(Asset.id.in_(valid_ids))
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
                clip_type = self._detect_clip_type(clip)
                text_state, text_preview = self._summarize_text_content(clip, max_length=100)

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

                overview_clips.append(
                    OverviewClip(
                        id=clip.get("id", "")[:8],
                        clip_type=clip_type,
                        asset_name=asset_name_map.get(aid) if aid else None,
                        start_ms=start,
                        end_ms=start + dur,
                        text_state=text_state,
                        text_content=text_preview,
                        effects_summary=", ".join(effects_parts) if effects_parts else None,
                        group_id=clip.get("group_id"),
                    )
                )

            # Detect gaps
            gaps: list[OverviewGap] = []
            for i in range(len(sorted_clips) - 1):
                end_a = sorted_clips[i].get("start_ms", 0) + sorted_clips[i].get("duration_ms", 0)
                start_b = sorted_clips[i + 1].get("start_ms", 0)
                if start_b > end_a:
                    gaps.append(
                        OverviewGap(
                            start_ms=end_a,
                            end_ms=start_b,
                            duration_ms=start_b - end_a,
                        )
                    )

            # Detect overlaps
            overlaps: list[OverviewOverlap] = []
            for i in range(len(sorted_clips)):
                end_i = sorted_clips[i].get("start_ms", 0) + sorted_clips[i].get("duration_ms", 0)
                for j in range(i + 1, len(sorted_clips)):
                    start_j = sorted_clips[j].get("start_ms", 0)
                    if start_j >= end_i:
                        break  # No more overlaps possible (sorted by start)
                    end_j = sorted_clips[j].get("start_ms", 0) + sorted_clips[j].get(
                        "duration_ms", 0
                    )
                    overlap_start = start_j
                    overlap_end = min(end_i, end_j)
                    overlaps.append(
                        OverviewOverlap(
                            clip_a_id=sorted_clips[i].get("id", "")[:8],
                            clip_b_id=sorted_clips[j].get("id", "")[:8],
                            overlap_start_ms=overlap_start,
                            overlap_end_ms=overlap_end,
                            overlap_duration_ms=overlap_end - overlap_start,
                        )
                    )

            if gaps:
                warnings.append(f"Layer '{layer.get('name', '')}' has {len(gaps)} gap(s)")

            overview_layers.append(
                OverviewLayer(
                    id=layer.get("id", ""),
                    name=layer.get("name", ""),
                    type=layer.get("type", "content"),
                    visible=layer.get("visible", True),
                    locked=layer.get("locked", False),
                    clips=overview_clips,
                    gaps=gaps,
                    overlaps=overlaps,
                )
            )

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
                clip_type = self._detect_clip_type(clip)
                text_state, text_preview = self._summarize_text_content(clip, max_length=100)

                overview_clips.append(
                    OverviewClip(
                        id=clip.get("id", "")[:8],
                        clip_type=clip_type,
                        asset_name=asset_name_map.get(aid) if aid else None,
                        start_ms=start,
                        end_ms=start + dur,
                        text_state=text_state,
                        text_content=text_preview,
                        group_id=clip.get("group_id"),
                    )
                )

            overview_audio.append(
                OverviewAudioTrack(
                    id=track.get("id", ""),
                    name=track.get("name", ""),
                    type=track.get("type", "narration"),
                    volume=track.get("volume", 1.0),
                    muted=track.get("muted", False),
                    clips=overview_clips,
                )
            )

        # Generate visual snapshot (only when explicitly requested)
        snapshot_base64: str | None = None
        if include_snapshot:
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

    async def get_clip_details(self: Any, project: Project, clip_id: str) -> L3ClipDetails | None:
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
                )
                if crop_data
                else None,
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
            background_opacity=_get_style_value(
                "backgroundOpacity", "background_opacity", default=0
            ),
            line_height=_get_style_value("lineHeight", "line_height"),
            letter_spacing=_get_style_value("letterSpacing", "letter_spacing"),
        )

    async def get_audio_clip_details(
        self: Any, project: Project, clip_id: str
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

    async def _find_linked_audio_asset(self: Any, video_asset_id: str) -> Asset | None:
        """Find the auto-extracted audio asset linked to a video asset."""
        result = await self.db.execute(
            select(Asset)
            .where(
                Asset.source_asset_id == video_asset_id,
                Asset.type == "audio",
            )
            .limit(1)
        )
        return cast(Asset | None, result.scalar_one_or_none())

    def _find_or_create_narration_track(self: Any, timeline: dict[str, Any]) -> dict[str, Any]:
        """Find existing narration track or create one."""
        audio_tracks = cast(list[dict[str, Any]], timeline.setdefault("audio_tracks", []))

        for track in audio_tracks:
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
        audio_tracks.insert(0, narration_track)
        return narration_track

    def _find_clips_by_group_id(
        self,
        timeline: dict[str, Any],
        group_id: str,
        exclude_clip_id: str | None = None,
    ) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
        """Find all clips with matching group_id across layers and audio tracks.

        Returns: list of (clip_data, container (layer or track), "video" | "audio")
        """
        results: list[tuple[dict[str, Any], dict[str, Any], str]] = []

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
