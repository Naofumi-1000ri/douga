"""Timeline editing and analysis methods extracted from AIService."""

from __future__ import annotations

import copy
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

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
    AudioTrackSummary,
    BatchClipOperation,
    BatchOperationResult,
    GapAnalysisResult,
    L3AudioClipDetails,
    L3ClipDetails,
    LayerSummary,
    MoveAudioClipRequest,
    MoveClipRequest,
    PacingAnalysisResult,
    PacingSegment,
    PreviewDiffRequest,
    SemanticOperation,
    SemanticOperationResult,
    SplitClipRequest,
    TimelineGap,
    TimeRange,
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
from src.schemas.clip_adapter import UnifiedClipInput, UnifiedTransformInput
from src.services.ai.utils import (
    _sanitize_timeline_ms,
    normalize_text_style_for_storage,
)

logger = logging.getLogger(__name__)


class TimelineEditorMixin:
    """Timeline mutation, semantic operation, batch, and analysis helpers."""

    # =========================================================================
    # Preview Diff (read-only simulation)
    # =========================================================================

    async def preview_diff(self: Any, project: Project, request: PreviewDiffRequest) -> dict:
        """Simulate an operation and return the diff without applying changes.

        This is read-only: no timeline data is modified, no DB flush occurs.
        """

        timeline = project.timeline_data or {}
        changes: list[dict] = []
        conflicts: list[str] = []

        if request.operation_type == "move":
            if not request.clip_id:
                return {
                    "operation_type": request.operation_type,
                    "change_count": 0,
                    "changes": [],
                    "conflicts": ["clip_id is required for move"],
                }

            clip_data, _layer, full_clip_id = self._find_clip_by_id(timeline, request.clip_id)
            if not clip_data:
                return {
                    "operation_type": request.operation_type,
                    "change_count": 0,
                    "changes": [],
                    "conflicts": [f"Clip not found: {request.clip_id}"],
                }

            old_start = clip_data.get("start_ms", 0)
            new_start = request.parameters.get("new_start_ms", old_start)
            delta_ms = new_start - old_start

            if delta_ms != 0:
                changes.append(
                    {
                        "entity_type": "clip",
                        "entity_id": full_clip_id,
                        "field": "start_ms",
                        "before": old_start,
                        "after": new_start,
                    }
                )

            # Show linked audio changes via group_id
            group_id = clip_data.get("group_id")
            if group_id and delta_ms != 0:
                linked = self._find_clips_by_group_id(
                    timeline, group_id, exclude_clip_id=full_clip_id
                )
                for linked_clip, _container, clip_type in linked:
                    linked_start = linked_clip.get("start_ms", 0)
                    changes.append(
                        {
                            "entity_type": "audio_clip" if clip_type == "audio" else "clip",
                            "entity_id": linked_clip.get("id", ""),
                            "field": "start_ms",
                            "before": linked_start,
                            "after": max(0, linked_start + delta_ms),
                        }
                    )

        elif request.operation_type == "trim":
            if not request.clip_id:
                return {
                    "operation_type": request.operation_type,
                    "change_count": 0,
                    "changes": [],
                    "conflicts": ["clip_id is required for trim"],
                }

            clip_data, _layer, full_clip_id = self._find_clip_by_id(timeline, request.clip_id)
            if not clip_data:
                return {
                    "operation_type": request.operation_type,
                    "change_count": 0,
                    "changes": [],
                    "conflicts": [f"Clip not found: {request.clip_id}"],
                }

            for field in ["duration_ms", "in_point_ms", "out_point_ms"]:
                if field in request.parameters:
                    changes.append(
                        {
                            "entity_type": "clip",
                            "entity_id": full_clip_id,
                            "field": field,
                            "before": clip_data.get(field),
                            "after": request.parameters[field],
                        }
                    )

        elif request.operation_type == "delete":
            if not request.clip_id:
                return {
                    "operation_type": request.operation_type,
                    "change_count": 0,
                    "changes": [],
                    "conflicts": ["clip_id is required for delete"],
                }

            clip_data, _layer, full_clip_id = self._find_clip_by_id(timeline, request.clip_id)
            if not clip_data:
                return {
                    "operation_type": request.operation_type,
                    "change_count": 0,
                    "changes": [],
                    "conflicts": [f"Clip not found: {request.clip_id}"],
                }

            changes.append(
                {
                    "entity_type": "clip",
                    "entity_id": full_clip_id,
                    "action": "delete",
                    "before": {
                        "start_ms": clip_data.get("start_ms", 0),
                        "duration_ms": clip_data.get("duration_ms", 0),
                    },
                    "after": None,
                }
            )

            group_id = clip_data.get("group_id")
            if group_id:
                linked = self._find_clips_by_group_id(
                    timeline, group_id, exclude_clip_id=full_clip_id
                )
                for linked_clip, _container, clip_type in linked:
                    changes.append(
                        {
                            "entity_type": "audio_clip" if clip_type == "audio" else "clip",
                            "entity_id": linked_clip.get("id", ""),
                            "action": "delete",
                            "before": {
                                "start_ms": linked_clip.get("start_ms", 0),
                                "duration_ms": linked_clip.get("duration_ms", 0),
                            },
                            "after": None,
                        }
                    )

        elif request.operation_type in ("close_all_gaps", "distribute_evenly"):
            target_layer_id = request.layer_id
            if not target_layer_id:
                return {
                    "operation_type": request.operation_type,
                    "change_count": 0,
                    "changes": [],
                    "conflicts": ["layer_id is required for " + request.operation_type],
                }

            layer, full_layer_id = self._find_layer_by_id(timeline, target_layer_id)
            if not layer:
                return {
                    "operation_type": request.operation_type,
                    "change_count": 0,
                    "changes": [],
                    "conflicts": [f"Layer not found: {target_layer_id}"],
                }

            clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
            if not clips:
                return {
                    "operation_type": request.operation_type,
                    "change_count": 0,
                    "changes": [],
                    "conflicts": [],
                }

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
                    changes.append(
                        {
                            "entity_type": "clip",
                            "entity_id": clip.get("id", ""),
                            "field": "start_ms",
                            "before": old_start,
                            "after": current_pos,
                        }
                    )
                    # Also show linked audio changes
                    group_id = clip.get("group_id")
                    if group_id and delta != 0:
                        linked = self._find_clips_by_group_id(
                            timeline, group_id, exclude_clip_id=clip.get("id")
                        )
                        for linked_clip, _container, clip_type in linked:
                            if clip_type == "audio":
                                linked_start = linked_clip.get("start_ms", 0)
                                changes.append(
                                    {
                                        "entity_type": "audio_clip",
                                        "entity_id": linked_clip.get("id", ""),
                                        "field": "start_ms",
                                        "before": linked_start,
                                        "after": max(0, linked_start + delta),
                                    }
                                )
                current_pos = current_pos + clip.get("duration_ms", 0) + gap_ms

        elif request.operation_type == "add_text_with_timing":
            if not request.clip_id:
                return {
                    "operation_type": request.operation_type,
                    "change_count": 0,
                    "changes": [],
                    "conflicts": ["clip_id is required for add_text_with_timing"],
                }

            clip_data, _layer, full_clip_id = self._find_clip_by_id(timeline, request.clip_id)
            if not clip_data:
                return {
                    "operation_type": request.operation_type,
                    "change_count": 0,
                    "changes": [],
                    "conflicts": [f"Clip not found: {request.clip_id}"],
                }

            changes.append(
                {
                    "entity_type": "clip",
                    "action": "create",
                    "before": None,
                    "after": {
                        "type": "text",
                        "start_ms": clip_data.get("start_ms", 0),
                        "duration_ms": clip_data.get("duration_ms", 0),
                        "text_content": request.parameters.get("text_content")
                        or request.parameters.get("text", ""),
                        "position": request.parameters.get("position", "bottom"),
                    },
                }
            )

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
        self,
        project: Project,
        request: AddClipRequest,
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
                    "For text clips, use 'text_content' (not 'text'). For video/image clips, provide 'asset_id'."
                )

        # Note: Overlap check removed to allow AI-driven clip placement at any position
        # Overlapping clips are now allowed and handled by frontend visualization

        # Determine layer type for smart defaults
        layer_type = layer.get("type", "content")

        # Smart defaults based on layer type
        # Coordinate system: (0, 0) = canvas center, positive x = right, positive y = down
        default_x: float = 0
        default_y: float = 0
        default_scale: float = 1.0

        if layer_type == "text":
            default_x = 0
            default_y = 380  # Lower area for text (within safe zone)
        elif layer_type == "background":
            default_x = 0
            default_y = 0
            default_scale = 1.0  # scale to fill 1920x1080
        elif layer_type == "content":
            default_x = 0
            default_y = 0
            default_scale = 1.0  # scale to fill
        # avatar, effects: center (0, 0) with default scale 1.0

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
            new_clip["text_style"] = normalize_text_style_for_storage(request.text_style)

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
        self,
        project: Project,
        request: AddAudioClipRequest,
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

    def _find_clip_by_id(
        self, timeline: dict, clip_id: str
    ) -> tuple[dict | None, dict | None, str | None]:
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

    def _find_audio_clip_by_id(
        self, timeline: dict, clip_id: str
    ) -> tuple[dict | None, dict | None, str | None]:
        """Find an audio clip by full or partial ID.

        Returns: (clip_data, source_track, full_clip_id)
        """
        for track in timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                full_id = clip.get("id", "")
                if full_id == clip_id or full_id.startswith(clip_id):
                    return clip, track, full_id
        return None, None, None

    def _find_layer_by_id(
        self: Any, timeline: dict, layer_id: str
    ) -> tuple[dict | None, str | None]:
        """Find a layer by full or partial ID.

        Returns: (layer_data, full_layer_id)
        """
        for layer in timeline.get("layers", []):
            full_id = layer.get("id", "")
            if full_id == layer_id or full_id.startswith(layer_id):
                return layer, full_id
        return None, None

    def _find_audio_track_by_id(
        self, timeline: dict, track_id: str
    ) -> tuple[dict | None, str | None]:
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
                overlap_ms = min(end_ms, other_end) - max(start_ms, other_start)
                warnings.append(
                    f"Clip overlaps with clip {other_id} on the same layer "
                    f"({other_start}-{other_end}ms, overlap: {overlap_ms}ms). "
                    f"To resolve: move one clip with PATCH /clips/{{id}}/move, "
                    f"trim with PATCH /clips/{{id}}/timing, or delete with DELETE /clips/{{id}}"
                )
        return warnings

    async def move_clip(
        self,
        project: Project,
        clip_id: str,
        request: MoveClipRequest,
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
                target_layer,
                full_clip_id or clip_id,
                request.new_start_ms,
                clip_data.get("duration_ms", 0),
            )
            result._overlap_warnings = overlap_warnings

        return result

    async def move_audio_clip(
        self,
        project: Project,
        clip_id: str,
        request: MoveAudioClipRequest,
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
            found_track, full_track_id = self._find_audio_track_by_id(
                timeline, request.new_track_id
            )
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
        self,
        project: Project,
        clip_id: str,
        request: UpdateClipTransformRequest,
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
        self,
        project: Project,
        clip_id: str,
        request: UpdateClipEffectsRequest,
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
        has_chroma_key_update = any(
            [
                request.chroma_key_enabled is not None,
                request.chroma_key_color is not None,
                request.chroma_key_similarity is not None,
                request.chroma_key_blend is not None,
            ]
        )
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
        self, project: Project, clip_id: str, request: UpdateClipCropRequest
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
        self,
        project: Project,
        clip_id: str,
        request: UpdateClipTextStyleRequest,
        *,
        _skip_flush: bool = False,
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

        clip["text_style"] = normalize_text_style_for_storage(clip.get("text_style"))

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

        clip["text_style"] = normalize_text_style_for_storage(clip.get("text_style"))

        flag_modified(project, "timeline_data")
        if not _skip_flush:
            await self.db.flush()
        return await self.get_clip_details(project, full_clip_id or clip_id)

    async def update_audio_clip(
        self, project: Project, clip_id: str, request: UpdateAudioClipRequest
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
                {"time_ms": kf.time_ms, "value": kf.value} for kf in request.volume_keyframes
            ]

        flag_modified(project, "timeline_data")
        await self.db.flush()
        return await self.get_audio_clip_details(project, full_clip_id or clip_id)

    async def update_clip_timing(
        self, project: Project, clip_id: str, request: UpdateClipTimingRequest
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
        self,
        project: Project,
        clip_id: str,
        request: UpdateClipTextRequest,
        _skip_flush: bool = False,
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

        if not _skip_flush:
            flag_modified(project, "timeline_data")
            await self.db.flush()
        return await self.get_clip_details(project, full_clip_id or clip_id)

    async def update_clip_shape(
        self, project: Project, clip_id: str, request: UpdateClipShapeRequest
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
        self,
        project: Project,
        clip_id: str,
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
        self,
        project: Project,
        clip_id: str,
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
        self,
        project: Project,
        clip_id: str,
        duration_ms: int,
        clip_type: str = "video",
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
        self,
        project: Project,
        clip_id: str,
        split_at_ms: int,
        left_text_content: str | None = None,
        right_text_content: str | None = None,
        _skip_flush: bool = False,
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
        original_text_content = clip_data.get("text_content")

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
        if original_text_content is not None and left_text_content is not None:
            clip_data["text_content"] = left_text_content

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
        if original_text_content is not None:
            right_clip["text_content"] = (
                right_text_content if right_text_content is not None else original_text_content
            )
        # Clear fade_in on right half
        if "effects" in right_clip:
            right_clip["effects"] = {
                k: v for k, v in right_clip["effects"].items() if k != "fade_in_ms"
            }
        source_layer["clips"].append(right_clip)

        # --- Split group-linked clips ---
        linked_splits: list[dict[str, str]] = []
        if old_group_id:
            linked = self._find_clips_by_group_id(
                timeline, old_group_id, exclude_clip_id=full_clip_id
            )
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
                    linked_right.pop(
                        "fade_out_ms", None
                    ) if "fade_out_ms" not in linked_clip else None

                container["clips"].append(linked_right)
                linked_splits.append(
                    {
                        "original_id": linked_clip.get("id", ""),
                        "left_id": linked_clip.get("id", ""),
                        "right_id": linked_right_id,
                    }
                )

        self._update_project_duration(project)
        if not _skip_flush:
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

    async def unlink_clip(self: Any, project: Project, clip_id: str) -> dict[str, Any]:
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
        self: Any, project: Project, layer_ids: list[str]
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
        self,
        project: Project,
        layer_id: str,
        name: str | None = None,
        visible: bool | None = None,
        locked: bool | None = None,
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
                f"Invalid transform data: {exc}. Expected object with x, y, scale, rotation fields."
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
        clip_data, layer, full_clip_id = self._find_clip_by_id(timeline, operation.target_clip_id)
        if clip_data is None or layer is None:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message=f"Clip not found: {operation.target_clip_id}",
            )

        # Find the clip's position in the sorted clips list
        clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
        clip_index = next((i for i, c in enumerate(clips) if c.get("id") == full_clip_id), None)

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
            changes_made=[f"Moved clip from {old_start}ms to {prev_end}ms (snapped to previous)"],
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
        clip_data, layer, full_clip_id = self._find_clip_by_id(timeline, operation.target_clip_id)
        if clip_data is None or layer is None:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message=f"Clip not found: {operation.target_clip_id}",
            )

        # Find the clip's position in the sorted clips list
        clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
        clip_index = next((i for i, c in enumerate(clips) if c.get("id") == full_clip_id), None)

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
                changes.append(
                    f"Moved clip {clip.get('id', '')[:8]}... from {old_start}ms to {current_end}ms"
                )
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
            changes_made=[f"Renamed layer from '{old_name}' to '{new_name}'"],
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
        clip_data, layer, full_clip_id = self._find_clip_by_id(timeline, operation.target_clip_id)
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
        changes.append(
            f"Replaced asset on clip {full_clip_id[:8]}... from {old_asset_id} to {new_asset_id}"
        )

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
                        changes.append(
                            f"Replaced linked audio asset on clip {linked_clip.get('id', '')[:8]}..."
                        )
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
                clip_id = last_clip.get("id", "")
                if new_duration <= 0:
                    # Remove 0ms clip - it's useless after trimming
                    layer["clips"] = [c for c in layer.get("clips", []) if c.get("id") != clip_id]
                    changes.append(
                        f"Removed clip {clip_id[:8]}... (duration would be 0ms after trimming to fit project boundary {max_end_ms}ms)"
                    )
                    if clip_id not in affected_ids:
                        affected_ids.append(clip_id)
                    # Also remove linked audio clips via group_id
                    group_id = last_clip.get("group_id")
                    if group_id:
                        linked = self._find_clips_by_group_id(
                            timeline, group_id, exclude_clip_id=clip_id
                        )
                        for linked_clip, container, clip_type in linked:
                            linked_id = linked_clip.get("id", "")
                            container["clips"] = [
                                c for c in container.get("clips", []) if c.get("id") != linked_id
                            ]
                            if linked_id not in affected_ids:
                                affected_ids.append(linked_id)
                            changes.append(
                                f"Removed linked {clip_type} clip {linked_id[:8]}... (parent clip was removed)"
                            )
                else:
                    last_clip["duration_ms"] = new_duration
                    changes.append(
                        f"Trimmed last clip {clip_id[:8]}... duration from {old_duration}ms to {new_duration}ms to fit within project boundary ({max_end_ms}ms)"
                    )
                    if clip_id not in affected_ids:
                        affected_ids.append(clip_id)
                    warnings.append(
                        f"Last clip exceeded project boundary by {overflow_ms}ms and was trimmed"
                    )
                    if new_duration < 100:
                        warnings.append(
                            f"Clip {clip_id[:8]}... has very short duration ({new_duration}ms) after trimming"
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

        text = operation.parameters.get("text_content") or operation.parameters.get("text")
        if not text:
            return SemanticOperationResult(
                success=False,
                operation=operation.operation,
                error_message="parameters.text_content (or text) is required",
            )

        timeline = project.timeline_data or {}

        # Find the target clip to sync timing
        clip_data, _layer, full_clip_id = self._find_clip_by_id(timeline, operation.target_clip_id)
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
        # Coordinate system: (0,0) = canvas center, x/y are offsets from center
        # Safe zone: 5% margin → safe y range is approx -486 to +486
        position = operation.parameters.get("position", "bottom")
        position_map = {"top": -380, "center": 0, "bottom": 380}
        y_pos = position_map.get(position, 380)

        font_size = operation.parameters.get("font_size", 48)

        # Create text clip — x=0 for horizontal center, y from position_map
        new_clip_id = str(uuid.uuid4())
        new_clip = {
            "id": new_clip_id,
            "asset_id": None,
            "start_ms": start_ms,
            "duration_ms": duration_ms,
            "in_point_ms": 0,
            "out_point_ms": None,
            "transform": {
                "x": 0,
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
            "text_style": normalize_text_style_for_storage({"font_size": font_size}),
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

    def _classify_batch_error(self: Any, e: Exception, op: BatchClipOperation) -> dict[str, str]:
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
            suggestion = "Use GET /projects/{id}/structure to find valid layer_ids."
        elif isinstance(e, AudioTrackNotFoundError):
            error_code = "AUDIO_TRACK_NOT_FOUND"
            suggestion = "Use GET /projects/{id}/structure to find valid track_ids."
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
            suggestion = "Use GET /projects/{id}/assets to find valid asset_ids."
        elif isinstance(e, ValueError):
            error_code = "INVALID_PARAMETER"
            suggestion = str(e)

        return {
            "error_code": error_code,
            "message": str(e),
            "suggestion": suggestion,
        }

    async def execute_batch_operations(
        self,
        project: Project,
        operations: list[BatchClipOperation],
        *,
        rollback_on_failure: bool = False,
        continue_on_error: bool = True,
        include_audio: bool = True,
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
                        result = await self.add_clip(
                            project, req, include_audio=include_audio, _skip_flush=True
                        )
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
                    await self.trim_clip(
                        project, op.clip_id, duration_ms, op.clip_type, _skip_flush=True
                    )
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

                elif op.operation == "update_text_style":
                    if not op.clip_id:
                        raise ValueError("clip_id required for update_text_style")
                    if op.clip_type == "audio":
                        raise ValueError("update_text_style does not support audio clips")
                    # Support both nested {"text_style": {...}} and flat {"font_size": 48}
                    style_data = op.data.get("text_style", op.data)
                    req = UpdateClipTextStyleRequest(**style_data)
                    await self.update_clip_text_style(project, op.clip_id, req, _skip_flush=True)
                    results.append({"operation": "update_text_style", "clip_id": op.clip_id})
                    successful += 1

                elif op.operation == "update_text":
                    if not op.clip_id:
                        raise ValueError("clip_id required for update_text")
                    if op.clip_type == "audio":
                        raise ValueError("update_text does not support audio clips")
                    req = UpdateClipTextRequest(**op.data)
                    await self.update_clip_text(project, op.clip_id, req, _skip_flush=True)
                    results.append({"operation": "update_text", "clip_id": op.clip_id})
                    successful += 1

                elif op.operation == "split":
                    if not op.clip_id:
                        raise ValueError("clip_id required for split")
                    if op.clip_type == "audio":
                        raise ValueError("split does not support audio clips")
                    req = SplitClipRequest(**op.data)
                    split_result = await self.split_clip(
                        project,
                        op.clip_id,
                        req.split_at_ms,
                        left_text_content=req.left_text_content,
                        right_text_content=req.right_text_content,
                        _skip_flush=True,
                    )
                    right_clip = split_result.get("right_clip")
                    results.append(
                        {
                            "operation": "split",
                            "clip_id": op.clip_id,
                            "right_clip_id": getattr(right_clip, "id", None),
                        }
                    )
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
                results.append(
                    {
                        "operation": op.operation,
                        "error": error_info["message"],
                        "error_code": error_info["error_code"],
                        "suggestion": error_info["suggestion"],
                    }
                )

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
        # Only flush if there were successful operations (avoid flushing broken state)
        if successful > 0:
            flag_modified(project, "timeline_data")
            try:
                await self.db.flush()
            except Exception as flush_err:
                logger.error("Batch flush failed: %s", flush_err)
                # If flush fails, none of the operations were persisted
                errors.append(f"Database flush failed: {flush_err}")
                successful = 0

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
    # Analysis Tools
    # =========================================================================

    async def analyze_gaps(self: Any, project: Project) -> GapAnalysisResult:
        """Find gaps in the timeline with cross-layer awareness."""
        timeline = project.timeline_data or {}
        gaps: list[TimelineGap] = []

        # Pre-build per-layer merged coverage intervals for cross-layer checks
        layers = timeline.get("layers", [])
        layer_intervals: dict[str, list[tuple[int, int]]] = {}
        layer_names: dict[str, str] = {}
        for layer in layers:
            lid = layer.get("id", "")
            layer_names[lid] = layer.get("name", "")
            clips = layer.get("clips", [])
            if clips:
                intervals = sorted(
                    [
                        (c.get("start_ms", 0), c.get("start_ms", 0) + c.get("duration_ms", 0))
                        for c in clips
                    ],
                    key=lambda x: x[0],
                )
                merged: list[tuple[int, int]] = [intervals[0]]
                for start, end in intervals[1:]:
                    if start <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                    else:
                        merged.append((start, end))
                layer_intervals[lid] = merged
            else:
                layer_intervals[lid] = []

        def _find_covering(gap_start: int, gap_end: int, own_id: str) -> list[str]:
            """Return names of other video layers whose clips fully cover [gap_start, gap_end)."""
            covering: list[str] = []
            for lid, ivs in layer_intervals.items():
                if lid == own_id or not ivs:
                    continue
                cursor = gap_start
                for iv_start, iv_end in ivs:
                    if iv_start > cursor:
                        break
                    if iv_end > cursor:
                        cursor = iv_end
                    if cursor >= gap_end:
                        break
                if cursor >= gap_end:
                    covering.append(layer_names.get(lid, lid))
            return covering

        # Analyze video layers
        for layer in layers:
            layer_id = layer.get("id", "")
            clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
            current_end = 0

            for clip in clips:
                start = clip.get("start_ms", 0)
                if start > current_end:
                    covered = _find_covering(current_end, start, layer_id)
                    gaps.append(
                        TimelineGap(
                            layer_or_track_id=layer_id,
                            layer_or_track_name=layer.get("name", ""),
                            type="video",
                            start_ms=current_end,
                            end_ms=start,
                            duration_ms=start - current_end,
                            covered_by=covered,
                            is_intentional=len(covered) > 0,
                        )
                    )
                current_end = max(current_end, start + clip.get("duration_ms", 0))

        # Analyze audio tracks (no cross-layer coverage)
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
        uncovered_gap_duration = sum(g.duration_ms for g in gaps if not g.is_intentional)

        return GapAnalysisResult(
            total_gaps=len(gaps),
            total_gap_duration_ms=total_gap_duration,
            uncovered_gap_duration_ms=uncovered_gap_duration,
            gaps=gaps,
        )

    async def analyze_pacing(
        self,
        project: Project,
        segment_duration_ms: int = 30000,
        strategy: str = "content_aware",
    ) -> PacingAnalysisResult:
        """Analyze timeline pacing (clip density over time).

        Args:
            strategy: 'fixed_interval' for uniform segments, 'content_aware'
                      for segments derived from clip boundaries.
        """
        timeline = project.timeline_data or {}
        duration = project.duration_ms

        if duration == 0:
            return PacingAnalysisResult(
                overall_avg_clip_duration_ms=0,
                segments=[],
                suggested_improvements=[],
                segment_strategy=strategy,
            )

        # Collect all clip durations
        all_durations: list[int] = []
        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                all_durations.append(clip.get("duration_ms", 0))

        overall_avg = sum(all_durations) / len(all_durations) if all_durations else 0

        use_content_aware = strategy == "content_aware"
        actual_strategy = strategy
        pacing_warnings: list[str] = []

        if use_content_aware:
            segments = self._build_content_aware_segments(timeline, duration)
            if len(segments) < 2:
                # Instead of falling back, subdivide sparse content into time-based segments
                # while keeping content_aware label to indicate we analyzed the content first.
                segments = self._build_fixed_interval_segments(
                    timeline, duration, segment_duration_ms
                )
                # Keep content_aware strategy name but add informational note
                pacing_warnings.append(
                    "Fewer than 2 content boundaries detected — segments were auto-subdivided by time intervals. "
                    "This is normal for timelines with few clips. Add more clips for richer content-aware segmentation."
                )

        if not use_content_aware:
            segments = self._build_fixed_interval_segments(timeline, duration, segment_duration_ms)
            actual_strategy = "fixed_interval"

        # Generate suggestions
        suggestions: list[str] = []
        if segments:
            densities = [s.density for s in segments]
            avg_density = sum(densities) / len(densities)

            for seg in segments:
                seg_duration_s = (seg.end_ms - seg.start_ms) / 1000
                if seg.density < avg_density * 0.5 and seg.clip_count > 0:
                    suggestions.append(
                        f"Segment {seg.start_ms // 1000}s-{seg.end_ms // 1000}s has low clip density "
                        f"({seg.clip_count} clip(s) over {seg_duration_s:.0f}s) - consider adding overlay clips or splitting long clips"
                    )
                if overall_avg > 0 and seg.avg_clip_duration_ms > overall_avg * 2:
                    suggestions.append(
                        f"Segment {seg.start_ms // 1000}s-{seg.end_ms // 1000}s has long clips "
                        f"(avg {seg.avg_clip_duration_ms:.0f}ms vs overall {overall_avg:.0f}ms) - consider using split_clip to break them up"
                    )
                if seg.clip_count == 1 and seg_duration_s > 15:
                    suggestions.append(
                        f"Segment {seg.start_ms // 1000}s-{seg.end_ms // 1000}s is a single {seg_duration_s:.0f}s clip - "
                        "consider adding text overlays, markers, or splitting for better pacing"
                    )

            # Empty segments (no clips at all)
            for seg in segments:
                if seg.clip_count == 0:
                    suggestions.append(
                        f"Segment {seg.start_ms // 1000}s-{seg.end_ms // 1000}s has no clips - dead air"
                    )

        return PacingAnalysisResult(
            overall_avg_clip_duration_ms=round(overall_avg, 1),
            segments=segments,
            suggested_improvements=suggestions,
            segment_strategy=actual_strategy,
            warnings=pacing_warnings,
        )

    # -- pacing helpers -------------------------------------------------------

    def _build_fixed_interval_segments(
        self,
        timeline: dict,
        duration: int,
        segment_duration_ms: int,
    ) -> list[PacingSegment]:
        """Build pacing segments using fixed-width intervals."""
        segments: list[PacingSegment] = []
        for seg_start in range(0, duration, segment_duration_ms):
            seg_end = min(seg_start + segment_duration_ms, duration)
            seg_clips: list[int] = []

            for layer in timeline.get("layers", []):
                for clip in layer.get("clips", []):
                    clip_start = clip.get("start_ms", 0)
                    clip_end = clip_start + clip.get("duration_ms", 0)
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
        return segments

    def _build_content_aware_segments(
        self,
        timeline: dict,
        duration: int,
    ) -> list[PacingSegment]:
        """Build pacing segments based on natural clip boundaries.

        1. Collect all clip boundaries across all layers.
        2. Build segments from unique boundary points on the primary (most-clips) layer.
           Falls back to merge-based approach if boundaries are sparse.
        3. Group consecutive small scenes (< min_segment_ms) together.
        4. Split huge scenes (> max_segment_ms).
        5. Label each segment based on its dominant content.
        """
        min_segment_ms = 5000
        max_segment_ms = 45000
        merge_tolerance_ms = 1000

        # Step 1: collect all clip intervals with layer info
        intervals: list[tuple[int, int, str]] = []  # (start, end, layer_name)
        layer_clip_counts: dict[str, int] = {}
        for layer in timeline.get("layers", []):
            layer_name = layer.get("name", layer.get("type", "unknown"))
            for clip in layer.get("clips", []):
                clip_start = clip.get("start_ms", 0)
                clip_dur = clip.get("duration_ms", 0)
                if clip_dur > 0:
                    intervals.append((clip_start, clip_start + clip_dur, layer_name))
                    layer_clip_counts[layer_name] = layer_clip_counts.get(layer_name, 0) + 1

        if not intervals:
            return []

        # Step 2a: Try boundary-based segmentation using all clip edges
        # Collect boundaries from ALL layers for richer segmentation
        boundary_points: set[int] = set()
        for start, end, layer_name in intervals:
            boundary_points.add(start)
            boundary_points.add(end)

        sorted_boundaries = sorted(boundary_points)

        # If we have enough boundary points, use them to create segments
        if len(sorted_boundaries) >= 3:
            # Build scenes from consecutive boundary pairs
            scenes: list[dict] = []
            for i in range(len(sorted_boundaries) - 1):
                seg_start = sorted_boundaries[i]
                seg_end = sorted_boundaries[i + 1]
                if seg_end - seg_start < 500:  # Skip very tiny segments
                    continue
                # Count clips from each layer that overlap this segment
                lc: dict[str, int] = {}
                for start, end, layer_name in intervals:
                    if start < seg_end and end > seg_start:
                        lc[layer_name] = lc.get(layer_name, 0) + 1
                scenes.append({"start": seg_start, "end": seg_end, "layer_counts": lc})
        else:
            # Step 2b: Fall back to merge-based approach
            intervals.sort(key=lambda x: x[0])

            scenes = []
            cur_start, cur_end = intervals[0][0], intervals[0][1]
            layer_counts_merge: dict[str, int] = {intervals[0][2]: 1}

            for start, end, layer_name in intervals[1:]:
                if start <= cur_end + merge_tolerance_ms:
                    cur_end = max(cur_end, end)
                    layer_counts_merge[layer_name] = layer_counts_merge.get(layer_name, 0) + 1
                else:
                    scenes.append(
                        {"start": cur_start, "end": cur_end, "layer_counts": layer_counts_merge}
                    )
                    cur_start, cur_end = start, end
                    layer_counts_merge = {layer_name: 1}
            scenes.append({"start": cur_start, "end": cur_end, "layer_counts": layer_counts_merge})

        # Step 3: group small consecutive scenes
        grouped: list[dict] = []
        acc_start = scenes[0]["start"]
        acc_end = scenes[0]["end"]
        acc_layers: dict[str, int] = dict(scenes[0]["layer_counts"])

        for scene in scenes[1:]:
            merged_duration = scene["end"] - acc_start
            if acc_end - acc_start < min_segment_ms and merged_duration <= max_segment_ms:
                # Extend accumulator
                acc_end = scene["end"]
                for ln, cnt in scene["layer_counts"].items():
                    acc_layers[ln] = acc_layers.get(ln, 0) + cnt
            else:
                grouped.append({"start": acc_start, "end": acc_end, "layer_counts": acc_layers})
                acc_start = scene["start"]
                acc_end = scene["end"]
                acc_layers = dict(scene["layer_counts"])
        grouped.append({"start": acc_start, "end": acc_end, "layer_counts": acc_layers})

        # Step 4: split huge segments
        final_scenes: list[dict] = []
        for g in grouped:
            scene_dur = g["end"] - g["start"]
            if scene_dur > max_segment_ms:
                # Split into roughly equal parts
                n_parts = (scene_dur + max_segment_ms - 1) // max_segment_ms
                part_dur = scene_dur // n_parts
                for i in range(n_parts):
                    p_start = g["start"] + i * part_dur
                    p_end = g["start"] + (i + 1) * part_dur if i < n_parts - 1 else g["end"]
                    final_scenes.append(
                        {"start": p_start, "end": p_end, "layer_counts": g["layer_counts"]}
                    )
            else:
                final_scenes.append(g)

        # Step 5: build PacingSegment list with labels
        segments: list[PacingSegment] = []
        for fs in final_scenes:
            seg_start = fs["start"]
            seg_end = fs["end"]
            seg_clips: list[int] = []

            for layer in timeline.get("layers", []):
                for clip in layer.get("clips", []):
                    clip_start = clip.get("start_ms", 0)
                    clip_end = clip_start + clip.get("duration_ms", 0)
                    if clip_start < seg_end and clip_end > seg_start:
                        seg_clips.append(clip.get("duration_ms", 0))

            clip_count = len(seg_clips)
            avg_duration = sum(seg_clips) / clip_count if seg_clips else 0
            seg_duration_sec = (seg_end - seg_start) / 1000
            density = clip_count / seg_duration_sec if seg_duration_sec > 0 else 0

            # Label from dominant layer
            lc = fs.get("layer_counts", {})
            dominant_layer = max(lc, key=lc.get) if lc else "unknown"  # type: ignore[arg-type]
            label = f"{dominant_layer} section"

            segments.append(
                PacingSegment(
                    start_ms=seg_start,
                    end_ms=seg_end,
                    clip_count=clip_count,
                    avg_clip_duration_ms=round(avg_duration, 1),
                    density=round(density, 2),
                    segment_label=label,
                )
            )

        return segments

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _calculate_time_coverage(self: Any, clips: list[dict]) -> list[TimeRange]:
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

    def _check_overlap(self: Any, clips: list[dict], new_start: int, new_duration: int) -> bool:
        """Check if a new clip would overlap with existing clips."""
        new_end = new_start + new_duration

        for clip in clips:
            clip_start = clip.get("start_ms", 0)
            clip_end = clip_start + clip.get("duration_ms", 0)

            # Check for overlap
            if new_start < clip_end and new_end > clip_start:
                return True

        return False

    async def _get_asset(self: Any, asset_id: str) -> Asset | None:
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

    def _update_project_duration(self: Any, project: Project) -> None:
        """Update project duration based on timeline content."""
        timeline = project.timeline_data or {}

        # Sanitize all ms fields to integers before persisting
        _sanitize_timeline_ms(timeline)

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
