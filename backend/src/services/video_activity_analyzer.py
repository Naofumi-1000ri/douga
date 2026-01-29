"""Video activity analyzer using frame differencing.

Detects active (mouse moving, screen changing) vs inactive (idle, no change)
segments in a screen recording by comparing consecutive frames.

Usage:
    segments = await analyze_video_activity("/path/to/screen.mp4")
    # Returns: [ActivitySegment(0, 5000, True, 0.05), ActivitySegment(5000, 8000, False, 0.001), ...]
"""

import asyncio
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class ActivitySegment:
    """A segment of video classified as active or inactive."""

    start_ms: int
    end_ms: int
    is_active: bool
    activity_score: float  # Average normalized frame difference (0.0-1.0)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def _extract_frames(
    video_path: str,
    output_dir: str,
    fps: float = 2.0,
) -> int:
    """Extract frames from video at given FPS using FFmpeg.

    Returns:
        Number of frames extracted.
    """
    cmd = [
        settings.ffmpeg_path,
        "-i", video_path,
        "-vf", f"fps={fps},scale=480:-1",  # Low-res for speed
        "-q:v", "5",  # Medium quality JPEG
        "-y",
        f"{output_dir}/frame_%06d.jpg",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Frame extraction failed: {result.stderr}")

    frames = list(Path(output_dir).glob("frame_*.jpg"))
    return len(frames)


def _compute_frame_difference(img1: Image.Image, img2: Image.Image) -> float:
    """Compute normalized mean absolute difference between two images.

    Returns:
        Value between 0.0 (identical) and 1.0 (completely different).
    """
    if img1.size != img2.size:
        img2 = img2.resize(img1.size)

    pixels1 = list(img1.getdata())
    pixels2 = list(img2.getdata())

    total_diff = 0
    n = len(pixels1)
    for p1, p2 in zip(pixels1, pixels2):
        # RGB channels
        if isinstance(p1, int):
            total_diff += abs(p1 - p2)
        else:
            total_diff += sum(abs(a - b) for a, b in zip(p1, p2))

    # Normalize: max diff per pixel = 255*3, total pixels = n
    max_diff = 255 * 3 * n
    return total_diff / max_diff if max_diff > 0 else 0.0


def _analyze_frames(
    frame_dir: str,
    interval_ms: int,
    activity_threshold: float,
    min_inactive_ms: int,
    total_duration_ms: int,
) -> list[ActivitySegment]:
    """Analyze extracted frames and produce activity segments."""
    frame_files = sorted(Path(frame_dir).glob("frame_*.jpg"))
    if len(frame_files) < 2:
        return [ActivitySegment(0, total_duration_ms, True, 1.0)]

    # Compute per-interval activity scores
    scores: list[tuple[int, float]] = []  # (start_ms, score)
    prev_img = Image.open(frame_files[0])

    for i, frame_path in enumerate(frame_files[1:], start=1):
        curr_img = Image.open(frame_path)
        score = _compute_frame_difference(prev_img, curr_img)
        start_ms = (i - 1) * interval_ms
        scores.append((start_ms, score))
        prev_img = curr_img

    if not scores:
        return [ActivitySegment(0, total_duration_ms, True, 1.0)]

    # Classify each interval as active or inactive
    raw_segments: list[tuple[int, int, bool, float]] = []
    for start_ms, score in scores:
        is_active = score >= activity_threshold
        end_ms = min(start_ms + interval_ms, total_duration_ms)
        raw_segments.append((start_ms, end_ms, is_active, score))

    # Merge consecutive segments of same type
    merged: list[ActivitySegment] = []
    if not raw_segments:
        return [ActivitySegment(0, total_duration_ms, True, 1.0)]

    curr_start, _, curr_active, curr_score_sum = raw_segments[0]
    curr_count = 1
    curr_end = raw_segments[0][1]

    for start_ms, end_ms, is_active, score in raw_segments[1:]:
        if is_active == curr_active:
            curr_end = end_ms
            curr_score_sum += score
            curr_count += 1
        else:
            merged.append(ActivitySegment(
                start_ms=curr_start,
                end_ms=curr_end,
                is_active=curr_active,
                activity_score=curr_score_sum / curr_count,
            ))
            curr_start = start_ms
            curr_end = end_ms
            curr_active = is_active
            curr_score_sum = score
            curr_count = 1

    # Final segment
    merged.append(ActivitySegment(
        start_ms=curr_start,
        end_ms=curr_end,
        is_active=curr_active,
        activity_score=curr_score_sum / curr_count,
    ))

    # Post-process: short inactive segments (< min_inactive_ms) → active
    result: list[ActivitySegment] = []
    for seg in merged:
        if not seg.is_active and seg.duration_ms < min_inactive_ms:
            # Too short to be meaningful idle, mark as active
            seg = ActivitySegment(seg.start_ms, seg.end_ms, True, seg.activity_score)
        result.append(seg)

    # Re-merge after post-processing
    final: list[ActivitySegment] = [result[0]]
    for seg in result[1:]:
        prev = final[-1]
        if prev.is_active == seg.is_active:
            # Merge
            avg_score = (
                (prev.activity_score * prev.duration_ms + seg.activity_score * seg.duration_ms)
                / (prev.duration_ms + seg.duration_ms)
            )
            final[-1] = ActivitySegment(
                prev.start_ms, seg.end_ms, seg.is_active, avg_score,
            )
        else:
            final.append(seg)

    return final


async def analyze_video_activity(
    video_path: str,
    total_duration_ms: int,
    sample_fps: float = 2.0,
    activity_threshold: float = 0.005,
    min_inactive_duration_ms: int = 2000,
) -> list[ActivitySegment]:
    """Analyze a screen recording for active/inactive segments.

    Args:
        video_path: Path to the screen recording video.
        total_duration_ms: Total video duration in ms.
        sample_fps: Frames per second to sample (2.0 = every 500ms).
        activity_threshold: Normalized difference threshold (0.005 = 0.5%).
        min_inactive_duration_ms: Minimum duration to consider as inactive.

    Returns:
        Ordered list of ActivitySegment covering the full video.
    """
    interval_ms = int(1000 / sample_fps)
    tmp_dir = tempfile.mkdtemp(prefix="douga_activity_")

    try:
        # Extract frames in background thread
        frame_count = await asyncio.to_thread(
            _extract_frames, video_path, tmp_dir, sample_fps,
        )
        logger.info(
            "[ACTIVITY] Extracted %d frames from %s (%.1f fps)",
            frame_count, video_path, sample_fps,
        )

        # Analyze frame differences
        segments = await asyncio.to_thread(
            _analyze_frames,
            tmp_dir,
            interval_ms,
            activity_threshold,
            min_inactive_duration_ms,
            total_duration_ms,
        )

        # Log summary
        active_ms = sum(s.duration_ms for s in segments if s.is_active)
        inactive_ms = sum(s.duration_ms for s in segments if not s.is_active)
        logger.info(
            "[ACTIVITY] Result: %d segments, active=%dms (%.0f%%), inactive=%dms (%.0f%%)",
            len(segments),
            active_ms, 100 * active_ms / total_duration_ms if total_duration_ms else 0,
            inactive_ms, 100 * inactive_ms / total_duration_ms if total_duration_ms else 0,
        )

        return segments

    finally:
        # Cleanup extracted frames
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
