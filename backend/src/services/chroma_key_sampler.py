"""Chroma key background color auto-sampling from avatar videos.

Extracts a frame from a video, samples edge pixels, and determines the
dominant background color for chroma key compositing.
"""

import logging
import subprocess
import tempfile
from collections import Counter

from src.config import get_settings

logger = logging.getLogger(__name__)


def _quantize(value: int, step: int = 8) -> int:
    """Quantize a color channel value to reduce noise."""
    return (value // step) * step


def _is_green_or_blue(r: int, g: int, b: int) -> bool:
    """Check if an RGB color is green-ish or blue-ish (common chroma key colors)."""
    # Green: G channel dominant
    if g > r and g > b and g >= 80:
        return True
    # Blue: B channel dominant
    if b > r and b > g and b >= 80:
        return True
    return False


def sample_chroma_key_color(
    file_path: str,
    *,
    sample_times_ms: list[int] | None = None,
    clip_start_ms: int = 0,
    in_point_ms: int = 0,
) -> str | None:
    """Sample the dominant background color from a video file or URL.

    Algorithm:
    1. Extract frames at specified times (default: 1s)
    2. Open with Pillow and sample corner region pixels (4 corners, each 20% size)
    3. Apply inner margin (10px or 2% of image size) to avoid compression noise
    4. Sample every 4th pixel for efficiency
    5. Quantize RGB channels (step=8) to reduce noise
    6. Find the most frequent color and compute coverage ratio
    7. Return hex color if: green/blue with >50% coverage, or any color with >70% coverage
    8. Return None if conditions not met (caller should use default #00FF00)
    """
    try:
        from PIL import Image

        settings = get_settings()
        times = sample_times_ms or [clip_start_ms + 1000]

        all_pixels: list[tuple[int, int, int]] = []
        with tempfile.TemporaryDirectory(prefix="douga_chroma_sample_") as temp_dir:
            for idx, time_ms in enumerate(times):
                relative_ms = max(0, time_ms - clip_start_ms)
                seek_ms = max(0, in_point_ms + relative_ms)
                seek_s = seek_ms / 1000.0
                frame_path = f"{temp_dir}/frame_{idx}.png"

                cmd = [
                    settings.ffmpeg_path,
                    "-rw_timeout", "20000000",
                    "-ss", f"{seek_s:.3f}",
                    "-i", file_path,
                    "-frames:v", "1",
                    "-y",
                    frame_path,
                ]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=20
                )
                if result.returncode != 0:
                    logger.warning(
                        "FFmpeg frame extraction failed: %s", result.stderr[:200]
                    )
                    continue

                img = Image.open(frame_path).convert("RGB")
                w, h = img.size
                if w == 0 or h == 0:
                    continue

                # Inner margin: 10px or 2% of image size, whichever is larger
                margin_x = max(10, int(w * 0.02))
                margin_y = max(10, int(h * 0.02))

                # Corner region size: 20% of image dimensions
                corner_w = int(w * 0.20)
                corner_h = int(h * 0.20)

                # Define 4 corner regions (with inner margin applied)
                corners = [
                    # Top-left corner
                    (margin_x, margin_y, margin_x + corner_w, margin_y + corner_h),
                    # Top-right corner
                    (w - margin_x - corner_w, margin_y, w - margin_x, margin_y + corner_h),
                    # Bottom-left corner
                    (margin_x, h - margin_y - corner_h, margin_x + corner_w, h - margin_y),
                    # Bottom-right corner
                    (w - margin_x - corner_w, h - margin_y - corner_h, w - margin_x, h - margin_y),
                ]

                # Sample pixels from each corner region (every 4th pixel for efficiency)
                for x1, y1, x2, y2 in corners:
                    for y in range(y1, y2, 4):
                        for x in range(x1, x2, 4):
                            if 0 <= x < w and 0 <= y < h:
                                all_pixels.append(img.getpixel((x, y)))

        if not all_pixels:
            return None

        # Quantize to reduce noise
        quantized = [
            (_quantize(r), _quantize(g), _quantize(b))
            for r, g, b in all_pixels
        ]

        # Find most frequent color
        counter = Counter(quantized)
        most_common_color, most_common_count = counter.most_common(1)[0]
        coverage = most_common_count / len(quantized)

        r, g, b = most_common_color

        # Decision: green/blue with >50% coverage, or any color with >70%
        if _is_green_or_blue(r, g, b) and coverage > 0.50:
            hex_color = f"#{r:02x}{g:02x}{b:02x}"
            logger.info(
                "Chroma key detected: %s (coverage=%.1f%%)",
                hex_color, coverage * 100,
            )
            return hex_color

        if coverage > 0.70:
            hex_color = f"#{r:02x}{g:02x}{b:02x}"
            logger.info(
                "Chroma key detected (high coverage): %s (coverage=%.1f%%)",
                hex_color, coverage * 100,
            )
            return hex_color

        # No valid chroma key color detected - return None
        # Caller should use default #00FF00
        logger.warning(
            "No valid chroma key color detected (dominant: #%02x%02x%02x, coverage=%.1f%%)",
            r, g, b, coverage * 100,
        )
        return None

    except Exception:
        logger.exception("Chroma key sampling failed for %s", file_path)
        return None
