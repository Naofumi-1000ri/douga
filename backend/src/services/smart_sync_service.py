"""Smart Sync: auto-adjust operation screen playback to match narration.

Two strategies:
  1. Smart Cut (preferred): analyze video for inactive segments, cut them,
     then mildly speed-up remaining active parts to fit narration duration.
  2. Smart Sync (fallback): STT-based variable speed — speed up during speech,
     keep normal speed during silence.

Usage:
    segments = await compute_smart_cut(op_video_path, op_ms, nar_ms)
    # Returns: [SpeedSegment(source=0..5000, timeline=0..4000, speed=1.25), ...]
"""

import asyncio
import logging
from dataclasses import dataclass

from src.services.transcription_service import TranscriptionService
from src.services.video_activity_analyzer import ActivitySegment, analyze_video_activity

logger = logging.getLogger(__name__)


@dataclass
class SpeedSegment:
    """One speed segment of the operation screen clip."""

    source_start_ms: int    # Start position in source video
    source_end_ms: int      # End position in source video
    timeline_start_ms: int  # Start position on timeline
    timeline_duration_ms: int  # Duration on timeline
    speed: float            # Playback speed
    segment_type: str       # "speech" or "silence"


@dataclass
class _Interval:
    """Internal: a narration interval (speech or silence)."""

    start_ms: int
    end_ms: int
    is_speech: bool

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


async def compute_smart_sync(
    narration_audio_path: str,
    operation_duration_ms: int,
    narration_duration_ms: int,
    max_speed: float = 3.0,
    default_silence_speed: float = 1.0,
) -> list[SpeedSegment]:
    """Compute variable-speed segments for the operation screen.

    Args:
        narration_audio_path: Local path to narration audio file.
        operation_duration_ms: Duration of the operation screen source video.
        narration_duration_ms: Duration of the narration audio.
        max_speed: Maximum allowed playback speed.
        default_silence_speed: Speed during silence intervals.

    Returns:
        List of SpeedSegment ordered by timeline position.
        Total source consumed equals operation_duration_ms.
    """
    # Edge case: narration >= operation → no speed-up needed
    if narration_duration_ms >= operation_duration_ms:
        logger.info(
            "[SMART_SYNC] Narration (%dms) >= operation (%dms), using 1.0x",
            narration_duration_ms,
            operation_duration_ms,
        )
        return [
            SpeedSegment(
                source_start_ms=0,
                source_end_ms=operation_duration_ms,
                timeline_start_ms=0,
                timeline_duration_ms=operation_duration_ms,
                speed=1.0,
                segment_type="speech",
            )
        ]

    # Run STT to get speech segments
    try:
        intervals = await _get_speech_intervals(
            narration_audio_path, narration_duration_ms
        )
    except Exception:
        logger.warning(
            "[SMART_SYNC] STT failed, falling back to uniform speed",
            exc_info=True,
        )
        return _uniform_speed_fallback(
            operation_duration_ms, narration_duration_ms
        )

    if not intervals:
        return _uniform_speed_fallback(
            operation_duration_ms, narration_duration_ms
        )

    # Calculate totals
    t_speech = sum(iv.duration_ms for iv in intervals if iv.is_speech)
    t_silence = sum(iv.duration_ms for iv in intervals if not iv.is_speech)

    logger.info(
        "[SMART_SYNC] T_op=%dms, T_nar=%dms, T_speech=%dms, T_silence=%dms",
        operation_duration_ms,
        narration_duration_ms,
        t_speech,
        t_silence,
    )

    # Edge case: no speech detected → uniform speed
    if t_speech <= 0:
        return _uniform_speed_fallback(
            operation_duration_ms, narration_duration_ms
        )

    # Calculate speeds
    silence_speed = default_silence_speed
    speech_speed = (operation_duration_ms - silence_speed * t_silence) / t_speech

    # Cap speech_speed and redistribute to silence
    if speech_speed > max_speed:
        speech_speed = max_speed
        remaining = operation_duration_ms - speech_speed * t_speech
        if t_silence > 0:
            silence_speed = remaining / t_silence
        else:
            # All speech, no silence — clamp and accept partial coverage
            silence_speed = default_silence_speed

    # Ensure speeds are at least 0.5x (below that quality is bad)
    speech_speed = max(speech_speed, 0.5)
    silence_speed = max(silence_speed, 0.5)

    logger.info(
        "[SMART_SYNC] speech_speed=%.2f, silence_speed=%.2f",
        speech_speed,
        silence_speed,
    )

    # Build SpeedSegments by walking through intervals
    segments: list[SpeedSegment] = []
    source_pos_ms = 0

    for iv in intervals:
        speed = speech_speed if iv.is_speech else silence_speed
        source_consumed = int(round(speed * iv.duration_ms))
        source_start = source_pos_ms
        source_end = source_pos_ms + source_consumed

        # Clamp to operation duration
        source_end = min(source_end, operation_duration_ms)

        segments.append(
            SpeedSegment(
                source_start_ms=source_start,
                source_end_ms=source_end,
                timeline_start_ms=iv.start_ms,
                timeline_duration_ms=iv.duration_ms,
                speed=round(speed, 3),
                segment_type="speech" if iv.is_speech else "silence",
            )
        )
        source_pos_ms += source_consumed

    return segments


async def compute_smart_cut(
    operation_video_path: str,
    operation_duration_ms: int,
    narration_duration_ms: int,
    max_speed: float = 2.0,
) -> list[SpeedSegment]:
    """Cut inactive segments from operation video to match narration duration.

    Strategy:
    1. Analyze video for active/inactive segments via frame differencing
    2. Remove inactive segments (longest first) until remaining fits narration
    3. If remaining active > narration, apply mild speed-up (capped at max_speed)
    4. If cutting ALL inactive gives less than narration, keep some inactive

    Args:
        operation_video_path: Local path to the operation screen video.
        operation_duration_ms: Duration of the operation video in ms.
        narration_duration_ms: Target duration (narration audio length) in ms.
        max_speed: Maximum playback speed for remaining segments.

    Returns:
        Ordered list of SpeedSegment with inactive gaps removed.
    """
    # Edge case: operation fits within narration already
    if operation_duration_ms <= narration_duration_ms:
        logger.info(
            "[SMART_CUT] Operation (%dms) <= narration (%dms), no cut needed",
            operation_duration_ms,
            narration_duration_ms,
        )
        return [
            SpeedSegment(
                source_start_ms=0,
                source_end_ms=operation_duration_ms,
                timeline_start_ms=0,
                timeline_duration_ms=operation_duration_ms,
                speed=1.0,
                segment_type="active",
            )
        ]

    # Step 1: Analyze video activity
    activity_segments = await analyze_video_activity(
        operation_video_path,
        operation_duration_ms,
        sample_fps=2.0,
        activity_threshold=0.005,
        min_inactive_duration_ms=2000,
    )

    excess_ms = operation_duration_ms - narration_duration_ms
    total_inactive_ms = sum(
        s.duration_ms for s in activity_segments if not s.is_active
    )
    total_active_ms = sum(
        s.duration_ms for s in activity_segments if s.is_active
    )

    logger.info(
        "[SMART_CUT] T_op=%dms, T_nar=%dms, excess=%dms, "
        "active=%dms, inactive=%dms (%d segments)",
        operation_duration_ms,
        narration_duration_ms,
        excess_ms,
        total_active_ms,
        total_inactive_ms,
        len(activity_segments),
    )

    # Step 2: Sort inactive segments by duration (longest first) for greedy removal
    indexed_inactive = [
        (i, s) for i, s in enumerate(activity_segments) if not s.is_active
    ]
    indexed_inactive.sort(key=lambda x: x[1].duration_ms, reverse=True)

    # Step 3: Greedily cut inactive segments until excess is covered
    # Smart: skip a segment if cutting it would over-cut (content < narration)
    # and the remaining can be handled by mild speed-up instead.
    cut_indices: set[int] = set()
    cut_total_ms = 0

    for idx, seg in indexed_inactive:
        if cut_total_ms >= excess_ms:
            break
        # Check: would cutting this make content shorter than narration?
        remaining_after = operation_duration_ms - (cut_total_ms + seg.duration_ms)
        if remaining_after < narration_duration_ms and cut_total_ms > 0:
            # Skipping this cut — check if speed-up alone can close the gap
            remaining_without = operation_duration_ms - cut_total_ms
            speed_needed = remaining_without / narration_duration_ms
            if speed_needed <= max_speed:
                continue  # Speed-up handles the rest, no need to over-cut
        cut_indices.add(idx)
        cut_total_ms += seg.duration_ms

    # Step 4: Build kept segments (preserve original order)
    kept = [s for i, s in enumerate(activity_segments) if i not in cut_indices]
    kept_total_ms = sum(s.duration_ms for s in kept)

    logger.info(
        "[SMART_CUT] Cut %d inactive segments (%dms), kept %d segments (%dms)",
        len(cut_indices),
        cut_total_ms,
        len(kept),
        kept_total_ms,
    )

    # Step 5: Calculate playback speed for remaining segments
    if kept_total_ms <= narration_duration_ms:
        # We cut enough (or more than needed) — play at normal speed
        speed = 1.0
    else:
        # Still too long — speed up remaining segments
        speed = kept_total_ms / narration_duration_ms
        if speed > max_speed:
            speed = max_speed
            logger.warning(
                "[SMART_CUT] Speed %.2f exceeds max %.2f, capping",
                speed,
                max_speed,
            )

    # Ensure minimum speed
    speed = max(speed, 0.5)

    logger.info("[SMART_CUT] Final speed=%.3f for kept segments", speed)

    # Step 6: Merge consecutive kept segments that are adjacent in source time
    # (avoids creating many tiny sub-clips at activity boundaries)
    merged_kept = [kept[0]]
    for seg in kept[1:]:
        prev = merged_kept[-1]
        if seg.start_ms == prev.end_ms:
            # Adjacent in source — merge into one larger segment
            merged_kept[-1] = ActivitySegment(
                start_ms=prev.start_ms,
                end_ms=seg.end_ms,
                is_active=prev.is_active or seg.is_active,
                activity_score=max(prev.activity_score, seg.activity_score),
            )
        else:
            merged_kept.append(seg)

    logger.info(
        "[SMART_CUT] Merged %d kept segments into %d contiguous segments",
        len(kept),
        len(merged_kept),
    )

    # Step 7: Map merged segments to timeline positions
    segments: list[SpeedSegment] = []
    timeline_pos_ms = 0

    for seg in merged_kept:
        timeline_dur = int(round(seg.duration_ms / speed))
        segments.append(
            SpeedSegment(
                source_start_ms=seg.start_ms,
                source_end_ms=seg.end_ms,
                timeline_start_ms=timeline_pos_ms,
                timeline_duration_ms=timeline_dur,
                speed=round(speed, 3),
                segment_type="active" if seg.is_active else "inactive",
            )
        )
        timeline_pos_ms += timeline_dur

    return segments


async def _get_speech_intervals(
    audio_path: str,
    duration_ms: int,
) -> list[_Interval]:
    """Run STT and return ordered speech/silence intervals covering [0, duration_ms]."""
    svc = TranscriptionService(min_silence_duration_ms=300)
    transcription = await asyncio.to_thread(
        svc.transcribe,
        audio_path,
        language="ja",
        detect_silences=True,
        detect_fillers=False,
        detect_repetitions=False,
    )

    if transcription.status != "completed" or not transcription.segments:
        raise RuntimeError(
            f"Transcription failed: {transcription.error_message or 'no segments'}"
        )

    # Extract speech segment boundaries (non-cut segments)
    speech_ranges: list[tuple[int, int]] = []
    for seg in transcription.segments:
        if not seg.cut:
            speech_ranges.append((seg.start_ms, seg.end_ms))

    if not speech_ranges:
        raise RuntimeError("No speech segments detected")

    # Merge overlapping speech ranges
    speech_ranges.sort()
    merged: list[tuple[int, int]] = [speech_ranges[0]]
    for start, end in speech_ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Build alternating speech/silence intervals covering [0, duration_ms]
    intervals: list[_Interval] = []
    pos = 0

    for speech_start, speech_end in merged:
        # Silence gap before this speech segment
        if speech_start > pos:
            intervals.append(_Interval(start_ms=pos, end_ms=speech_start, is_speech=False))
        # Speech segment
        intervals.append(_Interval(start_ms=max(pos, speech_start), end_ms=speech_end, is_speech=True))
        pos = speech_end

    # Trailing silence
    if pos < duration_ms:
        intervals.append(_Interval(start_ms=pos, end_ms=duration_ms, is_speech=False))

    # Filter out zero-duration intervals
    intervals = [iv for iv in intervals if iv.duration_ms > 0]

    return intervals


def _uniform_speed_fallback(
    operation_duration_ms: int,
    narration_duration_ms: int,
) -> list[SpeedSegment]:
    """Fallback: uniform speed across the entire narration duration."""
    speed = operation_duration_ms / narration_duration_ms
    speed = max(speed, 0.5)  # Floor at 0.5x

    logger.info("[SMART_SYNC] Uniform fallback speed=%.2f", speed)
    return [
        SpeedSegment(
            source_start_ms=0,
            source_end_ms=operation_duration_ms,
            timeline_start_ms=0,
            timeline_duration_ms=narration_duration_ms,
            speed=round(speed, 3),
            segment_type="speech",
        )
    ]
