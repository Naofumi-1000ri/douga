"""Event point detection service for timeline analysis.

Detects key moments in the timeline for AI visual inspection:
- Clip boundaries (start/end of visual and audio clips)
- Layer composition changes (when new layers become active)
- Audio events (narration start, BGM, SE triggers)
- Silence gaps (periods with no audio activity)
- Section boundaries (major transitions between content types)
"""

import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any

from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class DetectedEvent:
    """A detected event point."""

    time_ms: int
    event_type: str
    description: str = ""
    layer: str | None = None
    clip_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class EventDetector:
    """Detects key event points in a timeline for AI inspection."""

    def __init__(self, timeline_data: dict[str, Any]):
        self.timeline = timeline_data
        self.duration_ms = timeline_data.get("duration_ms", 0)

    def detect_all(
        self,
        include_visual: bool = True,
        include_audio: bool = True,
        min_gap_ms: int = 500,
    ) -> list[DetectedEvent]:
        """Detect all event points in the timeline.

        Args:
            include_visual: Include visual layer events
            include_audio: Include audio track events
            min_gap_ms: Minimum silence gap duration to detect

        Returns:
            Sorted list of detected events
        """
        events: list[DetectedEvent] = []

        if include_visual:
            events.extend(self._detect_visual_events())

        if include_audio:
            events.extend(self._detect_audio_events())
            events.extend(self._detect_silence_gaps(min_gap_ms))

        events.extend(self._detect_section_boundaries())

        # Sort by time, deduplicate nearby events
        events.sort(key=lambda e: e.time_ms)
        return self._deduplicate(events, tolerance_ms=100)

    def _detect_visual_events(self) -> list[DetectedEvent]:
        """Detect visual clip boundary events."""
        events: list[DetectedEvent] = []
        layers = self.timeline.get("layers", [])

        for layer in layers:
            layer_type = layer.get("type", "content")
            layer_name = layer.get("name", layer_type)

            if not layer.get("visible", True):
                continue

            clips = layer.get("clips", [])
            for clip in clips:
                clip_id = clip.get("id", "")
                start_ms = clip.get("start_ms", 0)
                duration_ms = clip.get("duration_ms", 0)
                end_ms = start_ms + duration_ms

                # Determine content description
                content_desc = self._describe_clip(clip, layer_type)

                # Clip start
                events.append(DetectedEvent(
                    time_ms=start_ms,
                    event_type=self._layer_start_event(layer_type),
                    description=f"{layer_name}: {content_desc} starts",
                    layer=layer_type,
                    clip_id=clip_id,
                    metadata={"start_ms": start_ms, "duration_ms": duration_ms},
                ))

                # Clip end
                events.append(DetectedEvent(
                    time_ms=end_ms,
                    event_type=self._layer_end_event(layer_type),
                    description=f"{layer_name}: {content_desc} ends",
                    layer=layer_type,
                    clip_id=clip_id,
                    metadata={"end_ms": end_ms},
                ))

        return events

    def _detect_audio_events(self) -> list[DetectedEvent]:
        """Detect audio clip boundary events."""
        events: list[DetectedEvent] = []
        audio_tracks = self.timeline.get("audio_tracks", [])

        for track in audio_tracks:
            track_type = track.get("type", "se")
            track_name = track.get("name", track_type)

            if track.get("muted", False):
                continue

            clips = track.get("clips", [])
            for clip in clips:
                clip_id = clip.get("id", "")
                start_ms = clip.get("start_ms", 0)
                duration_ms = clip.get("duration_ms", 0)
                end_ms = start_ms + duration_ms

                event_type = self._audio_event_type(track_type)

                # Audio clip start
                events.append(DetectedEvent(
                    time_ms=start_ms,
                    event_type=event_type,
                    description=f"{track_name} starts at {start_ms}ms",
                    clip_id=clip_id,
                    metadata={
                        "track_type": track_type,
                        "start_ms": start_ms,
                        "duration_ms": duration_ms,
                    },
                ))

                # Audio clip end (for narration, which is significant)
                if track_type == "narration":
                    events.append(DetectedEvent(
                        time_ms=end_ms,
                        event_type="narration_end",
                        description=f"Narration ends at {end_ms}ms",
                        clip_id=clip_id,
                        metadata={"end_ms": end_ms},
                    ))

        return events

    def _detect_silence_gaps(self, min_gap_ms: int = 500) -> list[DetectedEvent]:
        """Detect silence gaps in audio tracks.

        A silence gap is a period where no audio clips are playing
        across all non-muted audio tracks.

        Args:
            min_gap_ms: Minimum gap duration to report

        Returns:
            List of silence gap events
        """
        events: list[DetectedEvent] = []

        # Collect all audio clip intervals
        intervals: list[tuple[int, int]] = []
        audio_tracks = self.timeline.get("audio_tracks", [])

        for track in audio_tracks:
            if track.get("muted", False):
                continue
            for clip in track.get("clips", []):
                start = clip.get("start_ms", 0)
                dur = clip.get("duration_ms", 0)
                if dur > 0:
                    intervals.append((start, start + dur))

        if not intervals:
            # Entire timeline is silent
            if self.duration_ms > min_gap_ms:
                events.append(DetectedEvent(
                    time_ms=0,
                    event_type="silence_gap",
                    description=f"No audio for entire timeline ({self.duration_ms}ms)",
                    metadata={"gap_start_ms": 0, "gap_end_ms": self.duration_ms, "gap_duration_ms": self.duration_ms},
                ))
            return events

        # Merge overlapping intervals
        intervals.sort()
        merged: list[tuple[int, int]] = [intervals[0]]
        for start, end in intervals[1:]:
            if start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        # Find gaps
        # Check gap at start
        if merged[0][0] > min_gap_ms:
            gap_start = 0
            gap_end = merged[0][0]
            events.append(DetectedEvent(
                time_ms=gap_start,
                event_type="silence_gap",
                description=f"Silence gap: {gap_end - gap_start}ms at start",
                metadata={
                    "gap_start_ms": gap_start,
                    "gap_end_ms": gap_end,
                    "gap_duration_ms": gap_end - gap_start,
                },
            ))

        # Check gaps between intervals
        for i in range(len(merged) - 1):
            gap_start = merged[i][1]
            gap_end = merged[i + 1][0]
            gap_duration = gap_end - gap_start

            if gap_duration >= min_gap_ms:
                events.append(DetectedEvent(
                    time_ms=gap_start,
                    event_type="silence_gap",
                    description=f"Silence gap: {gap_duration}ms between audio clips",
                    metadata={
                        "gap_start_ms": gap_start,
                        "gap_end_ms": gap_end,
                        "gap_duration_ms": gap_duration,
                    },
                ))

        # Check gap at end
        if self.duration_ms > 0 and merged[-1][1] < self.duration_ms - min_gap_ms:
            gap_start = merged[-1][1]
            gap_end = self.duration_ms
            events.append(DetectedEvent(
                time_ms=gap_start,
                event_type="silence_gap",
                description=f"Silence gap: {gap_end - gap_start}ms at end",
                metadata={
                    "gap_start_ms": gap_start,
                    "gap_end_ms": gap_end,
                    "gap_duration_ms": gap_end - gap_start,
                },
            ))

        return events

    def _detect_section_boundaries(self) -> list[DetectedEvent]:
        """Detect major section boundaries in the timeline.

        Identifies moments where multiple layers change simultaneously,
        indicating a section transition (e.g., intro → main content).
        """
        events: list[DetectedEvent] = []

        # Collect all clip boundaries with their layers
        boundaries: dict[int, list[str]] = {}
        layers = self.timeline.get("layers", [])

        for layer in layers:
            layer_type = layer.get("type", "content")
            if not layer.get("visible", True):
                continue

            for clip in layer.get("clips", []):
                start_ms = clip.get("start_ms", 0)
                duration_ms = clip.get("duration_ms", 0)
                end_ms = start_ms + duration_ms

                boundaries.setdefault(start_ms, []).append(f"{layer_type}_start")
                boundaries.setdefault(end_ms, []).append(f"{layer_type}_end")

        # Find times where 2+ layers change simultaneously
        for time_ms, changes in boundaries.items():
            if len(changes) >= 2 and time_ms > 0 and time_ms < self.duration_ms:
                events.append(DetectedEvent(
                    time_ms=time_ms,
                    event_type="section_boundary",
                    description=f"Section boundary: {len(changes)} layer changes",
                    metadata={"changes": changes},
                ))

        return events

    @staticmethod
    def _describe_clip(clip: dict, layer_type: str) -> str:
        """Generate a human-readable description for a clip."""
        if clip.get("text_content"):
            text = clip["text_content"][:30]
            return f"Text: '{text}'"
        if clip.get("shape"):
            return f"Shape: {clip['shape'].get('type', 'unknown')}"
        asset_id = clip.get("asset_id", "")
        if asset_id:
            return f"{layer_type} clip ({str(asset_id)[:8]})"
        return f"{layer_type} clip"

    @staticmethod
    def _layer_start_event(layer_type: str) -> str:
        """Map layer type to start event type."""
        mapping = {
            "avatar": "avatar_enter",
            "content": "slide_change",
            "background": "clip_start",
            "effects": "effect_point",
            "text": "clip_start",
        }
        return mapping.get(layer_type, "clip_start")

    @staticmethod
    def _layer_end_event(layer_type: str) -> str:
        """Map layer type to end event type."""
        mapping = {
            "avatar": "avatar_exit",
            "content": "clip_end",
            "background": "clip_end",
            "effects": "clip_end",
            "text": "clip_end",
        }
        return mapping.get(layer_type, "clip_end")

    @staticmethod
    def _audio_event_type(track_type: str) -> str:
        """Map audio track type to event type."""
        mapping = {
            "narration": "narration_start",
            "bgm": "bgm_start",
            "se": "se_trigger",
        }
        return mapping.get(track_type, "clip_start")

    @staticmethod
    def _deduplicate(events: list[DetectedEvent], tolerance_ms: int = 100) -> list[DetectedEvent]:
        """Remove duplicate events that are very close in time."""
        if not events:
            return events

        result: list[DetectedEvent] = [events[0]]
        for event in events[1:]:
            last = result[-1]
            # Keep if different type or far enough apart
            if event.event_type != last.event_type or abs(event.time_ms - last.time_ms) > tolerance_ms:
                result.append(event)

        return result


def detect_audio_events_from_file(
    audio_path: str,
    silence_threshold_db: float = -30,
    min_silence_ms: int = 500,
) -> list[DetectedEvent]:
    """Detect audio events directly from an audio file using FFmpeg.

    Uses ffmpeg silencedetect to find silence gaps in actual audio data.
    This is more accurate than timeline-based detection for narration analysis.

    Args:
        audio_path: Path to audio file
        silence_threshold_db: Silence detection threshold in dB
        min_silence_ms: Minimum silence duration in ms

    Returns:
        List of detected silence events
    """
    events: list[DetectedEvent] = []

    min_silence_s = min_silence_ms / 1000

    cmd = [
        settings.ffmpeg_path,
        "-i", audio_path,
        "-af", f"silencedetect=noise={silence_threshold_db}dB:d={min_silence_s}",
        "-f", "null",
        "-",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        stderr = result.stderr

        # Parse silencedetect output
        # Format: [silencedetect @ 0x...] silence_start: 1.234
        # Format: [silencedetect @ 0x...] silence_end: 2.567 | silence_duration: 1.333
        import re

        silence_starts: list[float] = []
        silence_ends: list[float] = []

        for line in stderr.split("\n"):
            start_match = re.search(r"silence_start:\s*([\d.]+)", line)
            if start_match:
                silence_starts.append(float(start_match.group(1)))

            end_match = re.search(r"silence_end:\s*([\d.]+)", line)
            if end_match:
                silence_ends.append(float(end_match.group(1)))

        # Pair up silence periods
        for i in range(min(len(silence_starts), len(silence_ends))):
            start_ms = int(silence_starts[i] * 1000)
            end_ms = int(silence_ends[i] * 1000)
            duration_ms = end_ms - start_ms

            events.append(DetectedEvent(
                time_ms=start_ms,
                event_type="silence_gap",
                description=f"Audio silence: {duration_ms}ms",
                metadata={
                    "gap_start_ms": start_ms,
                    "gap_end_ms": end_ms,
                    "gap_duration_ms": duration_ms,
                    "source": "ffmpeg_silencedetect",
                },
            ))

            # The end of silence = start of audio = narration start candidate
            events.append(DetectedEvent(
                time_ms=end_ms,
                event_type="narration_start",
                description=f"Audio resumes at {end_ms}ms (after {duration_ms}ms silence)",
                metadata={"after_silence_ms": duration_ms},
            ))

    except Exception as e:
        logger.warning(f"Failed to detect audio events from file: {e}")

    return events
