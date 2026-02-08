"""Director's Eye service — detects material/asset gaps.

Rule-based checks for each section type to ensure required elements are present.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MaterialGap:
    """A detected material/content gap."""
    section_id: str
    section_type: str
    description: str
    time_ms: int
    suggestions: list[str] = field(default_factory=list)


@dataclass
class DirectorsEyeResult:
    """Result of Director's Eye analysis."""
    gaps: list[MaterialGap] = field(default_factory=list)
    bgm_coverage_percent: float = 0.0
    has_section_transitions: bool = False


class DirectorsEyeService:
    """Detects material gaps and missing content elements."""

    # Required elements by section type
    SECTION_REQUIREMENTS: dict[str, list[dict[str, Any]]] = {
        "intro": [
            {"check": "avatar_clip", "description": "Avatar clip for intro greeting"},
            {"check": "text_clip", "description": "Title/greeting text"},
        ],
        "toc": [
            {"check": "content_clip", "description": "Table of contents slide"},
        ],
        "content": [
            {"check": "narration_audio", "description": "Narration audio track"},
            {"check": "content_clip", "description": "Visual content (slide/screen)"},
        ],
        "demo": [
            {"check": "screen_clip", "description": "Screen capture/operation video"},
        ],
        "summary": [
            {"check": "content_clip", "description": "Summary slide or content"},
        ],
        "outro": [
            {"check": "text_clip", "description": "Closing text/message"},
        ],
    }

    def __init__(
        self,
        video_plan: dict[str, Any] | None,
        timeline_data: dict[str, Any],
        asset_name_map: dict[str, str] | None = None,
    ):
        self.plan = video_plan
        self.timeline = timeline_data
        self.asset_name_map = asset_name_map or {}

    def analyze(self) -> DirectorsEyeResult:
        """Run Director's Eye analysis."""
        result = DirectorsEyeResult()

        # Check per-section requirements
        if self.plan:
            for section in self.plan.get("sections", []):
                self._check_section(section, result)

        # Check BGM coverage
        result.bgm_coverage_percent = self._check_bgm_coverage()

        # Check section transitions
        result.has_section_transitions = self._check_transitions()

        # Check narration gaps (narration playing but no content)
        self._check_narration_content_gaps(result)

        return result

    def _check_section(
        self, section: dict[str, Any], result: DirectorsEyeResult
    ) -> None:
        """Check a single section against its requirements."""
        section_type = section.get("type", "content")
        section_id = section.get("id", "")
        section_start = section.get("start_ms", 0)
        section_end = section_start + section.get("duration_ms", 0)

        requirements = self.SECTION_REQUIREMENTS.get(section_type, [])

        for req in requirements:
            check = req["check"]
            has_element = False

            if check == "avatar_clip":
                has_element = self._has_layer_clip("avatar", section_start, section_end)
            elif check == "text_clip":
                has_element = self._has_text_clip(section_start, section_end)
            elif check == "content_clip":
                has_element = self._has_layer_clip("content", section_start, section_end)
            elif check == "screen_clip":
                has_element = self._has_screen_clip(section_start, section_end)
            elif check == "narration_audio":
                has_element = self._has_audio_clip("narration", section_start, section_end)

            if not has_element:
                result.gaps.append(MaterialGap(
                    section_id=section_id,
                    section_type=section_type,
                    description=f"Missing {req['description']} in '{section_type}' section",
                    time_ms=section_start,
                    suggestions=self._suggest_fix(check, section_type),
                ))

    def _has_layer_clip(
        self, layer_type: str, start_ms: int, end_ms: int
    ) -> bool:
        """Check if a layer has clips in time range."""
        for layer in self.timeline.get("layers", []):
            if layer.get("type") != layer_type:
                continue
            for clip in layer.get("clips", []):
                clip_start = clip.get("start_ms", 0)
                clip_end = clip_start + clip.get("duration_ms", 0)
                if clip_end > start_ms and clip_start < end_ms:
                    return True
        return False

    def _has_text_clip(self, start_ms: int, end_ms: int) -> bool:
        """Check if there are text clips in time range."""
        for layer in self.timeline.get("layers", []):
            if layer.get("type") != "text":
                continue
            for clip in layer.get("clips", []):
                if clip.get("text_content") is None:
                    continue
                clip_start = clip.get("start_ms", 0)
                clip_end = clip_start + clip.get("duration_ms", 0)
                if clip_end > start_ms and clip_start < end_ms:
                    return True
        return False

    def _has_screen_clip(self, start_ms: int, end_ms: int) -> bool:
        """Check if there's a screen capture clip (content layer with screen asset)."""
        for layer in self.timeline.get("layers", []):
            if layer.get("type") != "content":
                continue
            for clip in layer.get("clips", []):
                clip_start = clip.get("start_ms", 0)
                clip_end = clip_start + clip.get("duration_ms", 0)
                if clip_end > start_ms and clip_start < end_ms:
                    asset_id = str(clip.get("asset_id", ""))
                    name = self.asset_name_map.get(asset_id, "")
                    # Screen clips typically have "screen" or "capture" in name
                    if any(kw in name.lower() for kw in ["screen", "capture", "操作", "demo"]):
                        return True
                    # Fallback: any content clip counts
                    if asset_id:
                        return True
        return False

    def _has_audio_clip(
        self, track_type: str, start_ms: int, end_ms: int
    ) -> bool:
        """Check if audio track has clips in time range."""
        for track in self.timeline.get("audio_tracks", []):
            if track.get("type") != track_type:
                continue
            for clip in track.get("clips", []):
                clip_start = clip.get("start_ms", 0)
                clip_end = clip_start + clip.get("duration_ms", 0)
                if clip_end > start_ms and clip_start < end_ms:
                    return True
        return False

    def _check_bgm_coverage(self) -> float:
        """Check what percentage of timeline is covered by BGM."""
        duration = self.timeline.get("duration_ms", 0)
        if duration <= 0:
            return 0.0

        bgm_ranges: list[tuple[int, int]] = []
        for track in self.timeline.get("audio_tracks", []):
            if track.get("type") != "bgm":
                continue
            for clip in track.get("clips", []):
                start = clip.get("start_ms", 0)
                end = start + clip.get("duration_ms", 0)
                bgm_ranges.append((start, end))

        if not bgm_ranges:
            return 0.0

        # Merge overlapping ranges
        bgm_ranges.sort()
        merged = [bgm_ranges[0]]
        for start, end in bgm_ranges[1:]:
            if start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        covered = sum(end - start for start, end in merged)
        return min(100.0, covered / duration * 100)

    def _check_transitions(self) -> bool:
        """Check if section boundaries have transitions (SE or effects)."""
        if not self.plan:
            return False

        sections = self.plan.get("sections", [])
        if len(sections) <= 1:
            return True  # Single section, no transitions needed

        # Check if effects layer has clips at section boundaries
        for i in range(len(sections) - 1):
            boundary_ms = sections[i].get("start_ms", 0) + sections[i].get("duration_ms", 0)
            # Check +-500ms around boundary
            has_effect = False
            for layer in self.timeline.get("layers", []):
                if layer.get("type") != "effects":
                    continue
                for clip in layer.get("clips", []):
                    clip_start = clip.get("start_ms", 0)
                    if abs(clip_start - boundary_ms) < 500:
                        has_effect = True
                        break

            if not has_effect:
                # Check SE track
                for track in self.timeline.get("audio_tracks", []):
                    if track.get("type") != "se":
                        continue
                    for clip in track.get("clips", []):
                        clip_start = clip.get("start_ms", 0)
                        if abs(clip_start - boundary_ms) < 500:
                            has_effect = True
                            break

            # If any boundary lacks transition, return False
            if not has_effect:
                return False

        return True

    def _check_narration_content_gaps(self, result: DirectorsEyeResult) -> None:
        """Check if narration plays during periods with no content-layer clips."""
        narration_ranges: list[tuple[int, int]] = []
        for track in self.timeline.get("audio_tracks", []):
            if track.get("type") != "narration":
                continue
            for clip in track.get("clips", []):
                start = clip.get("start_ms", 0)
                end = start + clip.get("duration_ms", 0)
                narration_ranges.append((start, end))

        for narr_start, narr_end in narration_ranges:
            has_content = self._has_layer_clip("content", narr_start, narr_end)
            has_avatar = self._has_layer_clip("avatar", narr_start, narr_end)
            has_bg = self._has_layer_clip("background", narr_start, narr_end)

            if not has_content and not has_avatar and not has_bg:
                result.gaps.append(MaterialGap(
                    section_id="",
                    section_type="content",
                    description=f"Narration playing at {narr_start}ms but no visual content",
                    time_ms=narr_start,
                    suggestions=[
                        "Add a slide or screen capture during this narration",
                        "Upload relevant visual material",
                    ],
                ))

    def _suggest_fix(self, check: str, section_type: str) -> list[str]:
        """Generate fix suggestions based on check type and section."""
        suggestions_map = {
            "avatar_clip": [
                "Upload an avatar video with green screen",
                "Add avatar clip to the avatar layer",
            ],
            "text_clip": [
                "Add a title/greeting text clip",
            ],
            "content_clip": [
                f"Upload a slide or visual for the '{section_type}' section",
                "Add content to the content layer",
            ],
            "screen_clip": [
                "Upload a screen capture/operation video",
                "Record a demo of the operation",
            ],
            "narration_audio": [
                "Record narration audio for this section",
                "Upload narration file and classify as 'narration'",
            ],
        }
        return suggestions_map.get(check, ["Review and add missing content"])
