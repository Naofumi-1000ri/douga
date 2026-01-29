"""Click detector for screen recordings.

Detects mouse click events by finding localized visual changes between
consecutive frames. When a user clicks in a screen recording, a small area
changes significantly (button press, menu open, etc.) while the rest stays
the same.

Usage:
    events = await detect_clicks("/path/to/screen.mp4", 113620)
    # Returns: [ClickEvent(5000, 400, 300, 200, 100, 0.3), ...]
"""

import asyncio
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class ClickEvent:
    """A detected click-like event in the video."""

    source_ms: int  # Timestamp in the source video
    x: int  # Center x in source video pixels (at extraction scale)
    y: int  # Center y in source video pixels (at extraction scale)
    width: int  # Bounding box width of changed area
    height: int  # Bounding box height of changed area
    intensity: float  # Average change intensity in the bounding box (0-1)
    frame_width: int  # Width of the analyzed frame
    frame_height: int  # Height of the analyzed frame


def _extract_frames_for_clicks(
    video_path: str,
    output_dir: str,
    fps: float = 4.0,
) -> int:
    """Extract frames from video at given FPS using FFmpeg."""
    cmd = [
        settings.ffmpeg_path,
        "-i", video_path,
        "-vf", f"fps={fps},scale=640:-1",  # 640px wide for speed + accuracy
        "-q:v", "3",
        "-y",
        f"{output_dir}/frame_%06d.jpg",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Frame extraction failed: {result.stderr}")

    frames = list(Path(output_dir).glob("frame_*.jpg"))
    return len(frames)


def _find_localized_change(
    arr1: np.ndarray,
    arr2: np.ndarray,
    change_threshold: int = 30,
    min_change_pixels: int = 30,
    max_change_fraction: float = 0.15,
) -> tuple[int, int, int, int, float] | None:
    """Find a localized area of change between two frames using numpy.

    Args:
        arr1, arr2: RGB image arrays of shape (H, W, 3), dtype uint8.

    Returns (center_x, center_y, bbox_w, bbox_h, intensity) or None.
    """
    h, w = arr1.shape[:2]
    total_pixels = h * w

    # Compute per-pixel mean absolute difference across RGB channels
    diff = np.mean(np.abs(arr1.astype(np.int16) - arr2.astype(np.int16)), axis=2)

    # Threshold to find changed pixels
    changed_mask = diff >= change_threshold
    n_changed = np.count_nonzero(changed_mask)

    if n_changed < min_change_pixels:
        return None

    change_fraction = n_changed / total_pixels
    if change_fraction > max_change_fraction:
        return None  # Too widespread (scrolling/transition)

    # Find bounding box of changed pixels
    ys, xs = np.where(changed_mask)
    min_x, max_x = int(xs.min()), int(xs.max())
    min_y, max_y = int(ys.min()), int(ys.max())

    bbox_w = max_x - min_x + 1
    bbox_h = max_y - min_y + 1

    if bbox_w / w > 0.5 or bbox_h / h > 0.5:
        return None  # Bounding box too large

    if bbox_w < 5 or bbox_h < 5:
        return None  # Too tiny, noise

    center_x = (min_x + max_x) // 2
    center_y = (min_y + max_y) // 2
    avg_intensity = float(diff[changed_mask].mean()) / 255.0

    return center_x, center_y, bbox_w, bbox_h, avg_intensity


def _analyze_clicks(
    frame_dir: str,
    interval_ms: int,
    total_duration_ms: int,
    change_threshold: int,
    min_change_pixels: int,
    max_change_fraction: float,
    merge_distance_ms: int,
) -> list[ClickEvent]:
    """Analyze extracted frames to detect click events."""
    frame_files = sorted(Path(frame_dir).glob("frame_*.jpg"))
    if len(frame_files) < 2:
        return []

    raw_events: list[ClickEvent] = []

    prev_arr = np.array(Image.open(frame_files[0]).convert("RGB"))
    frame_h, frame_w = prev_arr.shape[:2]

    for i, frame_path in enumerate(frame_files[1:], start=1):
        curr_arr = np.array(Image.open(frame_path).convert("RGB"))
        timestamp_ms = i * interval_ms

        result = _find_localized_change(
            prev_arr,
            curr_arr,
            change_threshold=change_threshold,
            min_change_pixels=min_change_pixels,
            max_change_fraction=max_change_fraction,
        )

        if result is not None:
            cx, cy, bw, bh, intensity = result
            raw_events.append(ClickEvent(
                source_ms=timestamp_ms,
                x=cx,
                y=cy,
                width=bw,
                height=bh,
                intensity=intensity,
                frame_width=frame_w,
                frame_height=frame_h,
            ))

        prev_arr = curr_arr

    if not raw_events:
        return []

    # Merge events close in time and position (same click, multiple frame changes)
    merged: list[ClickEvent] = [raw_events[0]]

    for event in raw_events[1:]:
        prev = merged[-1]
        time_diff = event.source_ms - prev.source_ms
        spatial_dist = ((event.x - prev.x) ** 2 + (event.y - prev.y) ** 2) ** 0.5

        if time_diff <= merge_distance_ms and spatial_dist < 50:
            # Merge: keep the one with higher intensity
            if event.intensity > prev.intensity:
                merged[-1] = event
        else:
            merged.append(event)

    logger.info(
        "[CLICK_DETECT] Found %d raw events, merged to %d click events",
        len(raw_events),
        len(merged),
    )

    return merged


async def detect_clicks(
    video_path: str,
    total_duration_ms: int,
    sample_fps: float = 4.0,
    change_threshold: int = 30,
    min_change_pixels: int = 30,
    max_change_fraction: float = 0.15,
    merge_distance_ms: int = 750,
) -> list[ClickEvent]:
    """Detect click-like events in a screen recording.

    Args:
        video_path: Path to the screen recording video.
        total_duration_ms: Total video duration in ms.
        sample_fps: Frames per second to analyze (4.0 = every 250ms).
        change_threshold: Per-pixel change threshold (0-255).
        min_change_pixels: Minimum changed pixels to register.
        max_change_fraction: Max fraction of frame that can change.
        merge_distance_ms: Merge clicks within this time window.

    Returns:
        List of ClickEvent ordered by timestamp.
    """
    interval_ms = int(1000 / sample_fps)
    tmp_dir = tempfile.mkdtemp(prefix="douga_clicks_")

    try:
        frame_count = await asyncio.to_thread(
            _extract_frames_for_clicks, video_path, tmp_dir, sample_fps,
        )
        logger.info(
            "[CLICK_DETECT] Extracted %d frames from %s (%.1f fps)",
            frame_count, video_path, sample_fps,
        )

        events = await asyncio.to_thread(
            _analyze_clicks,
            tmp_dir,
            interval_ms,
            total_duration_ms,
            change_threshold,
            min_change_pixels,
            max_change_fraction,
            merge_distance_ms,
        )

        logger.info(
            "[CLICK_DETECT] Detected %d click events in %dms video",
            len(events),
            total_duration_ms,
        )

        return events

    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
