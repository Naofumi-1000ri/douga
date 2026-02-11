"""Timeline Snapshot Image Generator.

Generates a visual overview image of the timeline as a horizontal bar chart.
Each layer/track is rendered as a row, with clips shown as colored rectangles.
"""

import base64
import io
import logging
from typing import Any

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# --- Color palette ---
BG_COLOR = (26, 26, 46)  # #1a1a2e
RULER_BG = (30, 30, 55)
RULER_TEXT = (180, 180, 200)
LABEL_BG = (35, 35, 60)
LABEL_TEXT = (220, 220, 240)
GRID_LINE = (50, 50, 80)
CLIP_TEXT_COLOR = (255, 255, 255)
TRACK_SEPARATOR = (60, 60, 90)

# Layer type -> color
LAYER_COLORS: dict[str, tuple[int, int, int]] = {
    "text": (59, 130, 246),       # #3b82f6 blue
    "effects": (34, 197, 94),     # #22c55e green
    "avatar": (249, 115, 22),     # #f97316 orange
    "content": (234, 179, 8),     # #eab308 yellow
    "background": (239, 68, 68),  # #ef4444 red
}
AUDIO_CLIP_COLOR = (16, 185, 129)  # #10b981 emerald

# Layout constants
IMAGE_WIDTH = 1200
IMAGE_HEIGHT = 400
RULER_HEIGHT = 28
LABEL_WIDTH = 110
TRACK_AREA_LEFT = LABEL_WIDTH
TRACK_AREA_WIDTH = IMAGE_WIDTH - LABEL_WIDTH
ROW_PADDING = 2
CLIP_CORNER_RADIUS = 3


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color string to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try to load a good font, fall back to default.

    Priority: Noto Sans CJK (Japanese support) > DejaVu > Pillow default.
    """
    font_candidates = [
        # macOS (Japanese-capable first)
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        # Linux / Cloud Run (Debian) – Noto Sans CJK first for Japanese support
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        # Fallback Latin fonts
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in font_candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    # Ultimate fallback
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    fill: tuple[int, int, int],
    radius: int = 3,
) -> None:
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = xy
    # Ensure minimum size
    if x1 - x0 < 2 or y1 - y0 < 2:
        draw.rectangle(xy, fill=fill)
        return
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def _ms_to_label(ms: int) -> str:
    """Convert milliseconds to a human-readable label (e.g. '1:30' or '45s')."""
    total_sec = ms / 1000
    if total_sec < 60:
        return f"{int(total_sec)}s"
    minutes = int(total_sec // 60)
    seconds = int(total_sec % 60)
    return f"{minutes}:{seconds:02d}"


def generate_timeline_snapshot(
    layers: list[dict[str, Any]],
    audio_tracks: list[dict[str, Any]],
    duration_ms: int,
    asset_name_map: dict[str, str] | None = None,
) -> str | None:
    """Generate a timeline snapshot image and return as base64-encoded JPEG.

    Args:
        layers: List of layer dicts from timeline_data (each with clips).
        audio_tracks: List of audio track dicts from timeline_data.
        duration_ms: Total project duration in milliseconds.
        asset_name_map: Optional mapping of asset_id -> asset_name.

    Returns:
        Base64-encoded JPEG string, or None if generation fails.
    """
    if asset_name_map is None:
        asset_name_map = {}

    # Safeguard: need at least some duration
    if duration_ms <= 0:
        duration_ms = 1000  # fallback 1 second

    num_video_rows = len(layers)
    num_audio_rows = len(audio_tracks)
    total_rows = num_video_rows + num_audio_rows

    if total_rows == 0:
        # Nothing to render
        return None

    # Calculate row height dynamically
    usable_height = IMAGE_HEIGHT - RULER_HEIGHT
    row_height = max(20, min(50, usable_height // total_rows))

    # Adjust image height if rows overflow
    needed_height = RULER_HEIGHT + total_rows * row_height
    img_height = max(IMAGE_HEIGHT, needed_height)

    img = Image.new("RGB", (IMAGE_WIDTH, img_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    font_small = _get_font(11)   # clip name
    font_label = _get_font(14)   # track name label
    font_ruler = _get_font(10)   # time ruler

    # --- Draw ruler ---
    draw.rectangle((0, 0, IMAGE_WIDTH, RULER_HEIGHT), fill=RULER_BG)

    # Determine tick interval
    duration_sec = duration_ms / 1000
    if duration_sec <= 10:
        tick_interval_sec = 1
    elif duration_sec <= 60:
        tick_interval_sec = 5
    elif duration_sec <= 300:
        tick_interval_sec = 15
    elif duration_sec <= 600:
        tick_interval_sec = 30
    else:
        tick_interval_sec = 60

    tick_ms = int(tick_interval_sec * 1000)
    t = 0
    while t <= duration_ms:
        x = TRACK_AREA_LEFT + int(t / duration_ms * TRACK_AREA_WIDTH)
        draw.line([(x, 0), (x, RULER_HEIGHT)], fill=GRID_LINE, width=1)
        label = _ms_to_label(t)
        draw.text((x + 2, 2), label, fill=RULER_TEXT, font=font_ruler)
        t += tick_ms

    # --- Draw rows ---
    y_offset = RULER_HEIGHT

    def _draw_row(
        row_index: int,
        label: str,
        clips: list[dict[str, Any]],
        color: tuple[int, int, int],
    ) -> None:
        nonlocal y_offset
        y_top = y_offset
        y_bottom = y_top + row_height

        # Track separator line
        draw.line([(0, y_top), (IMAGE_WIDTH, y_top)], fill=TRACK_SEPARATOR, width=1)

        # Label background
        draw.rectangle((0, y_top, LABEL_WIDTH, y_bottom), fill=LABEL_BG)
        # Truncate label if too long
        display_label = label if len(label) <= 12 else label[:11] + ".."
        # Center label vertically
        label_y = y_top + (row_height - 12) // 2
        draw.text((4, label_y), display_label, fill=LABEL_TEXT, font=font_label)

        # Grid lines (vertical, extending through the track area)
        t_grid = 0
        while t_grid <= duration_ms:
            gx = TRACK_AREA_LEFT + int(t_grid / duration_ms * TRACK_AREA_WIDTH)
            draw.line([(gx, y_top), (gx, y_bottom)], fill=GRID_LINE, width=1)
            t_grid += tick_ms

        # Clips
        for clip in clips:
            start = clip.get("start_ms", 0)
            dur = clip.get("duration_ms", 0)
            end = start + dur
            if dur <= 0:
                continue

            cx0 = TRACK_AREA_LEFT + int(start / duration_ms * TRACK_AREA_WIDTH)
            cx1 = TRACK_AREA_LEFT + int(end / duration_ms * TRACK_AREA_WIDTH)
            # Ensure minimum visible width
            if cx1 - cx0 < 2:
                cx1 = cx0 + 2
            # Clamp to track area
            cx0 = max(cx0, TRACK_AREA_LEFT)
            cx1 = min(cx1, IMAGE_WIDTH - 1)

            cy0 = y_top + ROW_PADDING
            cy1 = y_bottom - ROW_PADDING

            _draw_rounded_rect(draw, (cx0, cy0, cx1, cy1), fill=color, radius=CLIP_CORNER_RADIUS)

            # Clip label (asset name or text content)
            clip_label = None
            aid = clip.get("asset_id")
            if aid and aid in asset_name_map:
                clip_label = asset_name_map[aid]
            elif clip.get("text_content"):
                text = clip["text_content"]
                clip_label = text[:30] + ".." if len(text) > 30 else text

            if clip_label and (cx1 - cx0) > 20:
                # Truncate to fit
                max_chars = max(1, (cx1 - cx0 - 4) // 6)
                if len(clip_label) > max_chars:
                    clip_label = clip_label[: max_chars - 1] + ".."
                text_y = cy0 + (cy1 - cy0 - 10) // 2
                draw.text(
                    (cx0 + 3, text_y),
                    clip_label,
                    fill=CLIP_TEXT_COLOR,
                    font=font_small,
                )

        y_offset = y_bottom

    # Video layers (render top to bottom: text, effects, avatar, content, background)
    for layer in layers:
        layer_type = layer.get("type", "content")
        color = LAYER_COLORS.get(layer_type, (100, 100, 140))
        label = layer.get("name", layer_type.capitalize())
        clips = layer.get("clips", [])
        _draw_row(0, label, clips, color)

    # Audio separator
    if num_audio_rows > 0 and num_video_rows > 0:
        draw.line(
            [(0, y_offset), (IMAGE_WIDTH, y_offset)],
            fill=(80, 80, 120),
            width=2,
        )

    # Audio tracks
    for track in audio_tracks:
        label = track.get("name", track.get("type", "Audio"))
        clips = track.get("clips", [])
        _draw_row(0, label, clips, AUDIO_CLIP_COLOR)

    # --- Encode to JPEG base64 ---
    buf = io.BytesIO()
    # Crop to actual used height
    actual_height = y_offset if y_offset > RULER_HEIGHT else img_height
    if actual_height < img_height:
        img = img.crop((0, 0, IMAGE_WIDTH, actual_height))
    img.save(buf, format="JPEG", quality=80)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")
