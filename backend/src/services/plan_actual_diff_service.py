"""Plan vs Actual comparison service.

Compares the video_plan (design document) against the actual timeline_data
to find missing/extra elements and timing drift.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DiffResult:
    """Result of plan vs actual comparison."""
    match_percentage: float = 0.0
    missing_elements: list[dict[str, Any]] = field(default_factory=list)
    extra_elements: list[dict[str, Any]] = field(default_factory=list)
    timing_drift_ms: list[dict[str, Any]] = field(default_factory=list)


class PlanActualDiffService:
    """Compares video plan against actual timeline data."""

    def __init__(
        self,
        video_plan: dict[str, Any] | None,
        timeline_data: dict[str, Any],
    ):
        self.plan = video_plan
        self.timeline = timeline_data

    def compare(self) -> DiffResult:
        """Run the comparison. Returns DiffResult."""
        if not self.plan:
            return DiffResult(match_percentage=0.0, missing_elements=[{
                "type": "plan",
                "description": "No video plan found — cannot compare",
            }])

        result = DiffResult()
        sections = self.plan.get("sections", [])
        if not sections:
            result.match_percentage = 0.0
            result.missing_elements.append({
                "type": "sections",
                "description": "Video plan has no sections",
            })
            return result

        total_checks = 0
        matched = 0

        for section in sections:
            section_start = section.get("start_ms", 0)
            section_end = section_start + section.get("duration_ms", 0)
            section_type = section.get("type", "content")

            # Check visual elements
            for element in section.get("elements", []):
                total_checks += 1
                layer = element.get("layer", "content")
                asset_id = element.get("asset_id")
                elem_start = element.get("start_ms", 0)

                # Find matching clip in timeline
                found = self._find_matching_clip(
                    layer=layer,
                    asset_id=asset_id,
                    time_range=(section_start, section_end),
                )

                if found:
                    matched += 1
                    # Check timing drift
                    actual_start = found.get("start_ms", 0)
                    drift = abs(actual_start - elem_start)
                    if drift > 1000:  # More than 1 second drift
                        result.timing_drift_ms.append({
                            "element_id": element.get("id", ""),
                            "layer": layer,
                            "plan_start_ms": elem_start,
                            "actual_start_ms": actual_start,
                            "drift_ms": drift,
                        })
                else:
                    result.missing_elements.append({
                        "section_id": section.get("id", ""),
                        "section_type": section_type,
                        "element_id": element.get("id", ""),
                        "layer": layer,
                        "asset_id": asset_id,
                        "description": f"Plan element on '{layer}' layer not found in timeline",
                    })

            # Check audio elements
            for audio_elem in section.get("audio", []):
                total_checks += 1
                track_type = audio_elem.get("track", "narration")
                audio_asset_id = audio_elem.get("asset_id")

                found_audio = self._find_matching_audio_clip(
                    track_type=track_type,
                    asset_id=audio_asset_id,
                    time_range=(section_start, section_end),
                )

                if found_audio:
                    matched += 1
                else:
                    result.missing_elements.append({
                        "section_id": section.get("id", ""),
                        "section_type": section_type,
                        "element_id": audio_elem.get("id", ""),
                        "track": track_type,
                        "asset_id": audio_asset_id,
                        "description": f"Plan audio element on '{track_type}' track not found",
                    })

        # Check for extra clips not in plan
        planned_asset_ids = set()
        for section in sections:
            for elem in section.get("elements", []):
                if elem.get("asset_id"):
                    planned_asset_ids.add(str(elem["asset_id"]))
            for audio in section.get("audio", []):
                if audio.get("asset_id"):
                    planned_asset_ids.add(str(audio["asset_id"]))

        for layer in self.timeline.get("layers", []):
            for clip in layer.get("clips", []):
                clip_asset = str(clip.get("asset_id", ""))
                if clip_asset and clip_asset not in planned_asset_ids:
                    # Skip text/shape clips (generated by skills, not in plan)
                    if clip.get("text_content") is not None or clip.get("shape"):
                        continue
                    result.extra_elements.append({
                        "layer": layer.get("type", "content"),
                        "clip_id": clip.get("id", ""),
                        "asset_id": clip_asset,
                        "start_ms": clip.get("start_ms", 0),
                        "description": "Clip in timeline but not in plan",
                    })

        result.match_percentage = (matched / total_checks * 100) if total_checks > 0 else 100.0
        return result

    def _find_matching_clip(
        self,
        layer: str,
        asset_id: str | None,
        time_range: tuple[int, int],
    ) -> dict[str, Any] | None:
        """Find a clip matching the plan element."""
        for tl_layer in self.timeline.get("layers", []):
            if tl_layer.get("type") != layer:
                continue
            for clip in tl_layer.get("clips", []):
                clip_start = clip.get("start_ms", 0)
                clip_end = clip_start + clip.get("duration_ms", 0)

                # Check time overlap
                if clip_end <= time_range[0] or clip_start >= time_range[1]:
                    continue

                # Check asset match (if specified)
                if asset_id and str(clip.get("asset_id", "")) != str(asset_id):
                    continue

                return clip
        return None

    def _find_matching_audio_clip(
        self,
        track_type: str,
        asset_id: str | None,
        time_range: tuple[int, int],
    ) -> dict[str, Any] | None:
        """Find an audio clip matching the plan element."""
        for track in self.timeline.get("audio_tracks", []):
            if track.get("type") != track_type:
                continue
            for clip in track.get("clips", []):
                clip_start = clip.get("start_ms", 0)
                clip_end = clip_start + clip.get("duration_ms", 0)

                if clip_end <= time_range[0] or clip_start >= time_range[1]:
                    continue

                if asset_id and str(clip.get("asset_id", "")) != str(asset_id):
                    continue

                return clip
        return None
