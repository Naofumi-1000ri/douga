"""Frame sampling service for AI visual inspection.

Renders single frames from the timeline at specified times using FFmpeg.
Produces low-resolution JPEG images for quick AI analysis.
"""

import asyncio
import base64
import logging
import os
import subprocess
import tempfile
from typing import Any

from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _parse_hex_color(color_str: str, default: str = "ffffff") -> tuple[int, int, int]:
    """Parse hex color string to (r, g, b) tuple. Falls back to default for invalid colors."""
    hex_c = color_str.lstrip("#")
    if len(hex_c) == 3:
        hex_c = "".join([c * 2 for c in hex_c])
    if len(hex_c) < 6 or not all(c in "0123456789abcdefABCDEF" for c in hex_c):
        hex_c = default
    return int(hex_c[0:2], 16), int(hex_c[2:4], 16), int(hex_c[4:6], 16)


def _interpolate_transform_at(clip: dict[str, Any], time_ms: int) -> dict[str, Any]:
    """Interpolate clip transform at timeline time, matching frontend getInterpolatedTransform()."""
    transform = clip.get("transform") or {}
    effects = clip.get("effects") or {}
    base = {
        "x": transform.get("x") or 0,
        "y": transform.get("y") or 0,
        "scale": transform.get("scale") if transform.get("scale") is not None else 1.0,
        "rotation": transform.get("rotation") or 0,
        "opacity": effects.get("opacity") if effects.get("opacity") is not None else 1.0,
    }
    keyframes = clip.get("keyframes") or []
    if not keyframes:
        return base
    clip_start = clip.get("start_ms") or 0
    time_in_clip_ms = time_ms - clip_start
    sorted_kfs = sorted(keyframes, key=lambda k: k.get("time_ms", 0))

    def _kf_val(kf):
        kf_t = kf.get("transform") or {}
        kf_o = kf.get("opacity")
        return {
            "x": kf_t.get("x") or 0,
            "y": kf_t.get("y") or 0,
            "scale": kf_t.get("scale") if kf_t.get("scale") is not None else 1.0,
            "rotation": kf_t.get("rotation") or 0,
            "opacity": kf_o if kf_o is not None else base["opacity"],
        }

    if time_in_clip_ms <= sorted_kfs[0].get("time_ms", 0):
        return _kf_val(sorted_kfs[0])
    if time_in_clip_ms >= sorted_kfs[-1].get("time_ms", 0):
        return _kf_val(sorted_kfs[-1])
    for i in range(len(sorted_kfs) - 1):
        t0 = sorted_kfs[i].get("time_ms", 0)
        t1 = sorted_kfs[i + 1].get("time_ms", 0)
        if t0 <= time_in_clip_ms < t1:
            duration = t1 - t0
            t = (time_in_clip_ms - t0) / duration if duration > 0 else 0.0
            pv, nv = _kf_val(sorted_kfs[i]), _kf_val(sorted_kfs[i + 1])
            return {k: pv[k] + (nv[k] - pv[k]) * t for k in pv}
    return _kf_val(min(sorted_kfs, key=lambda k: abs(k.get("time_ms", 0) - time_in_clip_ms)))


def _calculate_fade_opacity(time_in_clip_ms, duration_ms, fade_in_ms, fade_out_ms) -> float:
    """Match frontend calculateFadeOpacity()."""
    mult = 1.0
    if fade_in_ms > 0 and time_in_clip_ms < fade_in_ms:
        mult = min(mult, time_in_clip_ms / fade_in_ms)
    time_from_end = duration_ms - time_in_clip_ms
    if fade_out_ms > 0 and time_from_end < fade_out_ms:
        mult = min(mult, time_from_end / fade_out_ms)
    return max(0.0, min(1.0, mult))


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_clip_fade_durations_ms(clip: dict[str, Any]) -> tuple[int, int]:
    effects = clip.get("effects") or {}
    ti = clip.get("transition_in") or {}
    to = clip.get("transition_out") or {}

    if clip.get("shape"):
        fade_in_sources = (
            clip.get("fade_in_ms"),
            effects.get("fade_in_ms"),
            ti.get("duration_ms") if ti.get("type") == "fade" else None,
        )
        fade_out_sources = (
            clip.get("fade_out_ms"),
            effects.get("fade_out_ms"),
            to.get("duration_ms") if to.get("type") == "fade" else None,
        )
    else:
        fade_in_sources = (
            effects.get("fade_in_ms"),
            clip.get("fade_in_ms"),
            ti.get("duration_ms") if ti.get("type") == "fade" else None,
        )
        fade_out_sources = (
            effects.get("fade_out_ms"),
            clip.get("fade_out_ms"),
            to.get("duration_ms") if to.get("type") == "fade" else None,
        )

    fi = next((value for value in fade_in_sources if value is not None), 0)
    fo = next((value for value in fade_out_sources if value is not None), 0)
    return max(0, int(fi or 0)), max(0, int(fo or 0))


def _normalize_font_weight(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "bold":
            return "bold"
        if normalized == "normal":
            return "normal"
        parsed = _coerce_int(normalized, default=-1)
        if parsed >= 0:
            return "bold" if parsed >= 600 else "normal"
    if isinstance(value, (int, float)):
        return "bold" if value >= 600 else "normal"
    return "normal"


_FONT_CANDIDATES: dict[str, dict[str, list[str]]] = {
    "sans": {
        "normal": [
            "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "DejaVuSans.ttf",
        ],
        "bold": [
            "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "DejaVuSans-Bold.ttf",
            "DejaVuSans.ttf",
        ],
    },
    "serif": {
        "normal": [
            "/System/Library/Fonts/ヒラギノ明朝 ProN.ttc",
            "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
            "DejaVuSerif.ttf",
            "DejaVuSans.ttf",
        ],
        "bold": [
            "/System/Library/Fonts/ヒラギノ明朝 ProN.ttc",
            "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSerifCJK-Bold.ttc",
            "DejaVuSerif-Bold.ttf",
            "DejaVuSerif.ttf",
            "DejaVuSans.ttf",
        ],
    },
    "rounded": {
        "normal": [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "DejaVuSans.ttf",
        ],
        "bold": [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
            "DejaVuSans-Bold.ttf",
            "DejaVuSans.ttf",
        ],
    },
}

_FONT_FAMILY_GROUPS = {
    "Noto Sans JP": "sans",
    "M PLUS 1p": "sans",
    "Sawarabi Gothic": "sans",
    "BIZ UDPGothic": "sans",
    "Noto Serif JP": "serif",
    "Sawarabi Mincho": "serif",
    "Shippori Mincho": "serif",
    "Kosugi Maru": "rounded",
    "M PLUS Rounded 1c": "rounded",
    "Zen Maru Gothic": "rounded",
}


def _resolve_font(font_size: int, font_family: Any = None, font_weight: Any = "normal"):
    """Load a preview font close to the browser's selected family/weight."""
    from PIL import ImageFont

    family = str(font_family or "Noto Sans JP").strip() or "Noto Sans JP"
    family_group = _FONT_FAMILY_GROUPS.get(family, "sans")
    weight = _normalize_font_weight(font_weight)
    candidates = _FONT_CANDIDATES[family_group][weight]

    if family_group != "sans":
        fallback_candidates = _FONT_CANDIDATES["sans"][weight]
        candidates = candidates + [path for path in fallback_candidates if path not in candidates]

    for path in candidates:
        try:
            return ImageFont.truetype(path, font_size)
        except Exception:
            continue
    return ImageFont.load_default()


def _get_text_bbox(
    font: Any, text: str, stroke_width: int = 0
) -> tuple[float, float, float, float]:
    sample_text = text or " "
    try:
        bbox = font.getbbox(sample_text, stroke_width=stroke_width)
    except TypeError:
        bbox = font.getbbox(sample_text)
    return tuple(float(value) for value in bbox)


def _get_text_line_width(
    font: Any,
    text: str,
    stroke_width: int = 0,
    letter_spacing: float = 0.0,
) -> float:
    bbox_left, _, bbox_right, _ = _get_text_bbox(font, text, stroke_width=stroke_width)
    extra_spacing = max(len(text) - 1, 0) * letter_spacing
    return max(0.0, (bbox_right - bbox_left) + extra_spacing)


def _measure_text_lines(
    text: str,
    font: Any,
    font_size: int,
    line_height: float,
    stroke_width: int,
    letter_spacing: float,
) -> tuple[list[dict[str, float | str]], float]:
    lines = text.split("\n") or [""]
    metrics: list[dict[str, float | str]] = []
    max_visual_height = 0.0

    for line in lines:
        bbox_left, bbox_top, bbox_right, bbox_bottom = _get_text_bbox(
            font,
            line,
            stroke_width=stroke_width,
        )
        metrics.append(
            {
                "line": line,
                "bbox_left": bbox_left,
                "bbox_top": bbox_top,
                "width": _get_text_line_width(
                    font,
                    line,
                    stroke_width=stroke_width,
                    letter_spacing=letter_spacing,
                ),
            }
        )
        max_visual_height = max(max_visual_height, bbox_bottom - bbox_top)

    line_box_height = max(float(font_size) * line_height, max_visual_height, 1.0)
    return metrics, line_box_height


class FrameSampler:
    """Renders single frames from a timeline for AI visual inspection."""

    def __init__(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],  # asset_id -> local file path
        asset_name_map: dict[str, str] | None = None,
        project_width: int = 1920,
        project_height: int = 1080,
        project_fps: int = 30,
    ):
        self.timeline = timeline_data
        self.assets = assets
        self.asset_name_map = asset_name_map or {}
        self.project_width = project_width
        self.project_height = project_height
        self.project_fps = project_fps
        self.duration_ms = timeline_data.get("duration_ms") or 0

    async def sample_frame(
        self,
        time_ms: int,
        resolution: str = "640x360",
    ) -> dict[str, Any]:
        """Render a single frame at the specified time.

        Uses a two-step approach:
        1. Render a short segment (single frame) using the existing pipeline
        2. Extract the frame as JPEG

        Args:
            time_ms: Time position in milliseconds
            resolution: Output resolution (WxH string)

        Returns:
            Dict with frame_base64, time_ms, resolution, size_bytes
        """
        # Parse resolution
        width, height = self._parse_resolution(resolution)

        # Create temp directory for this sample
        temp_dir = tempfile.mkdtemp(prefix="douga_sample_")

        try:
            # Step 1: Build FFmpeg command for single-frame render
            frame_path = os.path.join(temp_dir, "frame.png")

            # Use the pipeline to build the filter_complex, then extract a single frame
            active_clips = await self._render_single_frame(
                time_ms, width, height, frame_path, temp_dir
            )

            # Step 2: Read and encode the frame
            if os.path.exists(frame_path):
                with open(frame_path, "rb") as f:
                    frame_data = f.read()

                frame_base64 = base64.b64encode(frame_data).decode("utf-8")

                return {
                    "time_ms": time_ms,
                    "resolution": f"{width}x{height}",
                    "frame_base64": frame_base64,
                    "size_bytes": len(frame_data),
                    "active_clips": active_clips,
                }
            else:
                # Fallback: generate a blank frame
                return await self._blank_frame(time_ms, width, height, temp_dir)

        finally:
            # Cleanup temp directory
            import shutil

            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    async def _render_single_frame(
        self,
        time_ms: int,
        width: int,
        height: int,
        output_path: str,
        temp_dir: str,
    ) -> list[dict[str, Any]]:
        """Render a single frame using FFmpeg.

        Optimized strategy:
        - Only include clips visible at time_ms (skip all others)
        - Hybrid seeking: coarse input-level seek (-ss 5s before target)
          plus fine trim+setpts in filter for exact frame position
        - Short canvas duration (0.5s) instead of full timeline
        - No output-level seeking needed

        Returns:
            List of active clip metadata dicts for clips visible at time_ms.
        """
        import time as time_mod

        t0 = time_mod.monotonic()

        layers = self.timeline.get("layers") or []

        inputs: list[str] = []
        filter_parts: list[str] = []
        input_idx = 0
        active_clips_info: list[dict[str, Any]] = []

        sorted_layers = list(reversed(layers))

        # Base canvas - short duration (needs enough for trim+setpts)
        inputs.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s={self.project_width}x{self.project_height}:r=1:d=0.5",
            ]
        )
        current_output = f"{input_idx}:v"
        input_idx += 1

        shape_idx = 0
        has_clips = False

        for layer in sorted_layers:
            visible = layer.get("visible")
            if visible is None:
                visible = True
            if not visible:
                continue

            clips = layer.get("clips") or []
            if not clips:
                continue

            layer_type = layer.get("type") or "content"

            for clip in clips:
                clip_start = clip.get("start_ms") or 0
                clip_dur = clip.get("duration_ms") or 0
                freeze_frame_ms = clip.get("freeze_frame_ms") or 0
                clip_end = clip_start + clip_dur + freeze_frame_ms

                # Skip clips not visible at target time
                if clip_dur <= 0 or clip_start > time_ms or clip_end <= time_ms:
                    continue

                # Shape/text clips
                if clip.get("shape") or clip.get("text_content") is not None:
                    png_path = self._generate_simple_overlay(clip, shape_idx, temp_dir, time_ms)
                    if png_path:
                        inputs.extend(["-i", png_path])
                        interp = _interpolate_transform_at(clip, time_ms)
                        center_x = interp["x"]
                        center_y = interp["y"]

                        overlay_x = f"(main_w/2)+({int(center_x)})-(overlay_w/2)"
                        overlay_y = f"(main_h/2)+({int(center_y)})-(overlay_h/2)"

                        output_label = f"smp_shape{shape_idx}"
                        filter_parts.append(
                            f"[{current_output}][{input_idx}:v]overlay="
                            f"x={overlay_x}:y={overlay_y}"
                            f"[{output_label}]"
                        )
                        current_output = output_label
                        input_idx += 1
                        shape_idx += 1
                        has_clips = True

                        # Collect metadata for shape/text clip
                        clip_type = "text" if clip.get("text_content") is not None else "shape"
                        active_clips_info.append(
                            {
                                "clip_id": clip.get("id", ""),
                                "layer_name": layer.get("name", layer.get("type", "unknown")),
                                "asset_id": None,
                                "asset_name": None,
                                "clip_type": clip_type,
                                "transform": clip.get("transform", {}),
                                "text_content": clip.get("text_content"),
                                "progress_percent": round(
                                    ((time_ms - clip_start) / clip_dur * 100)
                                    if clip_dur > 0
                                    else 0,
                                    1,
                                ),
                            }
                        )
                    continue

                # Asset-based clips
                asset_id = str(clip.get("asset_id") or "")
                if not asset_id or asset_id not in self.assets:
                    continue

                asset_path = self.assets[asset_id]

                # Calculate seek position within the asset
                in_point_ms = clip.get("in_point_ms") or 0
                time_in_clip = time_ms - clip_start
                if freeze_frame_ms > 0 and time_ms >= clip_start + clip_dur:
                    offset_in_asset_ms = in_point_ms + clip_dur - 1
                else:
                    offset_in_asset_ms = in_point_ms + time_in_clip
                seek_s = max(0, offset_in_asset_ms / 1000)

                # Hybrid seeking: coarse input-level seek + fine trim filter
                # Input-level -ss seeks to nearest keyframe which can be
                # seconds away, causing black frames with short durations.
                # So we seek 5s before target and use trim for exact position.
                coarse_seek = max(0, seek_s - 5.0)
                fine_offset = seek_s - coarse_seek
                inputs.extend(["-ss", str(coarse_seek), "-t", "6", "-i", asset_path])

                # Build filter with fine trim to get exact frame
                clip_filter = self._build_sample_clip_filter_fast(
                    input_idx,
                    clip,
                    layer_type,
                    current_output,
                    fine_offset=fine_offset,
                    time_ms=time_ms,
                )
                filter_parts.append(clip_filter)
                current_output = f"smp{input_idx}"
                input_idx += 1
                has_clips = True

                # Collect metadata for asset-based clip
                active_clips_info.append(
                    {
                        "clip_id": clip.get("id", ""),
                        "layer_name": layer.get("name", layer.get("type", "unknown")),
                        "asset_id": asset_id,
                        "asset_name": self.asset_name_map.get(asset_id),
                        "clip_type": "video",
                        "transform": clip.get("transform", {}),
                        "text_content": None,
                        "progress_percent": round(
                            ((time_ms - clip_start) / clip_dur * 100) if clip_dur > 0 else 0, 1
                        ),
                    }
                )

        if not has_clips:
            cmd = [
                settings.ffmpeg_path,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s={width}x{height}:r=1:d=0.5",
                "-frames:v",
                "1",
                "-q:v",
                "5",
                output_path,
            ]
            await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
            print(
                f"[FRAME-SAMPLE] Blank frame at {time_ms}ms ({time_mod.monotonic() - t0:.1f}s)",
                flush=True,
            )
            return []

        filter_parts.append(f"[{current_output}]scale={width}:{height}[smp_out]")
        filter_complex = ";\n".join(filter_parts)

        cmd = [
            settings.ffmpeg_path,
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[smp_out]",
            "-frames:v",
            "1",
            output_path,
        ]

        print(f"[FRAME-SAMPLE] Rendering at {time_ms}ms with {input_idx - 1} inputs...", flush=True)
        result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
        elapsed = time_mod.monotonic() - t0

        if result.returncode != 0:
            logger.warning(
                f"[FRAME-SAMPLE] Failed at {time_ms}ms ({elapsed:.1f}s): {result.stderr[:300]}"
            )
            cmd = [
                settings.ffmpeg_path,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s={width}x{height}:r=1:d=0.5",
                "-frames:v",
                "1",
                output_path,
            ]
            await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
            return []
        else:
            print(f"[FRAME-SAMPLE] Done at {time_ms}ms ({elapsed:.1f}s)", flush=True)
            return active_clips_info

    def _build_sample_clip_filter_fast(
        self,
        input_idx: int,
        clip: dict[str, Any],
        layer_type: str,
        base_output: str,
        fine_offset: float = 0.0,
        time_ms: int = 0,
    ) -> str:
        """Build FFmpeg filter for a single clip at a single frame.

        Uses hybrid seeking: input-level coarse seek already done, then
        trim+setpts for exact frame positioning. Clip visibility already checked.
        """
        effects = clip.get("effects") or {}
        output_label = f"smp{input_idx}"

        clip_filters: list[str] = []

        # Fine trim to exact position (hybrid seeking: coarse via -ss, fine via trim)
        clip_filters.append(f"trim=start={fine_offset}:end={fine_offset + 0.5}")
        clip_filters.append("setpts=PTS-STARTPTS")

        # Crop
        crop = clip.get("crop") or {}
        crop_top = crop.get("top") or 0
        crop_right = crop.get("right") or 0
        crop_bottom = crop.get("bottom") or 0
        crop_left = crop.get("left") or 0
        has_crop = crop_top > 0 or crop_right > 0 or crop_bottom > 0 or crop_left > 0

        # Interpolated transform
        interp = _interpolate_transform_at(clip, time_ms)
        x, y, scale, rotation = interp["x"], interp["y"], interp["scale"], interp["rotation"]
        opacity = interp["opacity"]

        # Fade
        clip_dur = clip.get("duration_ms") or 0
        clip_start = clip.get("start_ms") or 0
        time_in_clip = max(0, time_ms - clip_start)
        fade_in_ms, fade_out_ms = _get_clip_fade_durations_ms(clip)
        fade_mult = _calculate_fade_opacity(time_in_clip, clip_dur, fade_in_ms, fade_out_ms)
        opacity = max(0.0, min(1.0, opacity * fade_mult))

        transform = clip.get("transform") or {}
        w = transform.get("width") or 0
        h = transform.get("height") or 0

        if w and h:
            clip_filters.append(f"scale={int(w * scale)}:{int(h * scale)}")
        elif scale != 1.0:
            clip_filters.append(f"scale=iw*{scale}:ih*{scale}")

        # Chroma key
        chroma_key = effects.get("chroma_key") or {}
        chroma_key_enabled = chroma_key.get("enabled") or False
        if chroma_key_enabled:
            color = (chroma_key.get("color") or "#00FF00").replace("#", "0x")
            similarity = chroma_key.get("similarity")
            if similarity is None:
                # Default matches effects_spec.yaml (SSOT)
                similarity = 0.4
            blend = chroma_key.get("blend")
            if blend is None:
                # Default matches effects_spec.yaml (SSOT)
                blend = 0.1
            clip_filters.append(f"colorkey={color}:{similarity}:{blend}")
            r, g, b = _parse_hex_color(chroma_key.get("color") or "#00FF00", "00FF00")
            despill_type = "blue" if (b > g and b > r) else "green"
            clip_filters.append(f"despill=type={despill_type}")

        post_chroma_filters: list[str] = []
        target_list = post_chroma_filters if chroma_key_enabled else clip_filters

        if has_crop:
            target_list.append(
                f"crop=iw*{1 - crop_left - crop_right:.4f}:ih*{1 - crop_top - crop_bottom:.4f}"
                f":iw*{crop_left:.4f}:ih*{crop_top:.4f}"
            )

        # Rotation
        if abs(rotation) > 0.01:
            target_list.append("format=rgba")
            target_list.append(
                f"rotate=({rotation})*PI/180:ow=hypot(iw,ih):oh=hypot(iw,ih):fillcolor=none"
            )

        # Opacity with fade
        if opacity < 1.0:
            target_list.append(f"format=rgba,colorchannelmixer=aa={opacity:.6f}")

        # Build filter string
        if chroma_key_enabled and clip_filters:
            ck_m = f"sck{input_idx}_m"
            ck_a = f"sck{input_idx}_a"
            ck_e = f"sck{input_idx}_e"
            pre_str = ",".join(clip_filters)
            post_str = ("," + ",".join(post_chroma_filters)) if post_chroma_filters else ""
            filter_str = (
                f"[{input_idx}:v]{pre_str},split[{ck_m}][{ck_a}];\n"
                f"[{ck_a}]alphaextract,erosion,gblur=sigma=1.5[{ck_e}];\n"
                f"[{ck_m}][{ck_e}]alphamerge{post_str}[smp_clip{input_idx}];\n"
            )
            clip_ref = f"smp_clip{input_idx}"
        elif clip_filters:
            filter_str = f"[{input_idx}:v]" + ",".join(clip_filters) + f"[smp_clip{input_idx}];\n"
            clip_ref = f"smp_clip{input_idx}"
        else:
            filter_str = ""
            clip_ref = f"{input_idx}:v"

        # Overlay (no enable needed - clip visibility already confirmed)
        crop_offset_x_expr = "0"
        crop_offset_y_expr = "0"
        if has_crop:
            width_ratio = 1 - crop_left - crop_right
            height_ratio = 1 - crop_top - crop_bottom
            if width_ratio > 0 and (w and h):
                sw = int(w * scale)
                crop_offset_x_expr = str(int(sw * (crop_left - crop_right) / (2 * width_ratio)))
            elif width_ratio > 0:
                crop_offset_x_expr = (
                    f"(overlay_w*{(crop_left - crop_right) / (2 * width_ratio):.6f})"
                )
            if height_ratio > 0 and (w and h):
                sh = int(h * scale)
                crop_offset_y_expr = str(int(sh * (crop_top - crop_bottom) / (2 * height_ratio)))
            elif height_ratio > 0:
                crop_offset_y_expr = (
                    f"(overlay_h*{(crop_top - crop_bottom) / (2 * height_ratio):.6f})"
                )
        overlay_x = f"(main_w/2)+({int(x)})+({crop_offset_x_expr})-(overlay_w/2)"
        overlay_y = f"(main_h/2)+({int(y)})+({crop_offset_y_expr})-(overlay_h/2)"

        filter_str += (
            f"[{base_output}][{clip_ref}]overlay=x={overlay_x}:y={overlay_y}[{output_label}]"
        )

        return filter_str

    def _generate_simple_overlay(
        self,
        clip: dict[str, Any],
        idx: int,
        temp_dir: str,
        time_ms: int = 0,
    ) -> str | None:
        """Generate a simple PNG overlay for shape/text clips during sampling."""
        try:
            from PIL import Image, ImageDraw

            transform = clip.get("transform") or {}
            shape = clip.get("shape")

            # Interpolate transform
            interp = _interpolate_transform_at(clip, time_ms)
            scale = interp["scale"]
            rotation = interp["rotation"]
            opacity = interp["opacity"]

            # Fade
            clip_dur = clip.get("duration_ms") or 0
            clip_start = clip.get("start_ms") or 0
            time_in_clip = max(0, time_ms - clip_start)
            fade_in_ms, fade_out_ms = _get_clip_fade_durations_ms(clip)
            fade_mult = _calculate_fade_opacity(time_in_clip, clip_dur, fade_in_ms, fade_out_ms)
            opacity = max(0.0, min(1.0, opacity * fade_mult))

            # Dimensions: shape data > transform > auto-size for text
            if shape and shape.get("width") and shape.get("height"):
                width = int(shape["width"])
                height = int(shape["height"])
            elif transform.get("width") and transform.get("height"):
                width = int(transform["width"])
                height = int(transform["height"])
            else:
                width = 0
                height = 0

            if shape:
                width = max(width, 1)
                height = max(height, 1)
                rw = max(1, int(width * scale))
                rh = max(1, int(height * scale))
                img = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)

                fill_color = shape.get("fillColor") or "#ffffff"
                filled = shape.get("filled") if shape.get("filled") is not None else True
                stroke_width = int(shape.get("strokeWidth") or 2)
                stroke_color_str = shape.get("strokeColor") or "#000000"
                sr, sg, sb = _parse_hex_color(stroke_color_str, "000000")
                stroke_rgba = (sr, sg, sb, 255)

                if fill_color.lower() in ("transparent", "none", ""):
                    fill_rgba = (0, 0, 0, 0)
                else:
                    fr, fg, fb = _parse_hex_color(fill_color, "ffffff")
                    fill_rgba = (fr, fg, fb, 255)

                if shape.get("type") == "circle":
                    if filled and fill_rgba[3] > 0:
                        draw.ellipse([(0, 0), (rw - 1, rh - 1)], fill=fill_rgba)
                    if not filled or stroke_width > 0:
                        draw.ellipse(
                            [(0, 0), (rw - 1, rh - 1)], outline=stroke_rgba, width=stroke_width
                        )
                else:
                    if filled and fill_rgba[3] > 0:
                        draw.rectangle([(0, 0), (rw - 1, rh - 1)], fill=fill_rgba)
                    if not filled or stroke_width > 0:
                        draw.rectangle(
                            [(0, 0), (rw - 1, rh - 1)], outline=stroke_rgba, width=stroke_width
                        )

            elif clip.get("text_content") is not None:
                text = clip["text_content"]
                text_style = clip.get("text_style") or {}
                color = text_style.get("color") or "#ffffff"
                r, g, b = _parse_hex_color(color, "ffffff")
                font_family = text_style.get("fontFamily") or "Noto Sans JP"
                font_size = _coerce_int(text_style.get("fontSize"), default=48)
                font_weight = text_style.get("fontWeight") or "bold"
                stroke_width_text = max(0, _coerce_int(text_style.get("strokeWidth"), default=2))
                stroke_color_str = text_style.get("strokeColor") or "#000000"
                s_r, s_g, s_b = _parse_hex_color(stroke_color_str, "000000")
                line_height = _coerce_float(text_style.get("lineHeight"), default=1.4)
                letter_spacing = _coerce_float(text_style.get("letterSpacing"), default=0.0)
                text_align = str(text_style.get("textAlign") or "center")
                bg_color_str = text_style.get("backgroundColor") or "#000000"
                bg_opacity = max(
                    0.0,
                    min(1.0, _coerce_float(text_style.get("backgroundOpacity"), default=0.4)),
                )
                has_bg = bg_color_str.lower() not in ("transparent", "none", "") and bg_opacity > 0

                font = _resolve_font(font_size, font_family, font_weight)

                # Auto-size if no explicit dimensions
                if width <= 0 or height <= 0:
                    pad_x = 16 if has_bg else 0
                    pad_y = 8 if has_bg else 0
                    line_metrics, line_box_height = _measure_text_lines(
                        text,
                        font,
                        font_size,
                        line_height,
                        stroke_width_text,
                        letter_spacing,
                    )
                    max_line_w = max(
                        (float(metric["width"]) for metric in line_metrics),
                        default=0.0,
                    )
                    width = max(
                        50,
                        int(round(max_line_w + pad_x * 2 + stroke_width_text * 2)),
                    )
                    height = max(
                        1,
                        int(
                            round(
                                line_box_height * max(len(line_metrics), 1)
                                + pad_y * 2
                                + stroke_width_text * 2
                            )
                        ),
                    )

                width = max(width, 1)
                height = max(height, 1)
                rw = max(1, int(width * scale))
                rh = max(1, int(height * scale))
                img = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)

                # Background
                if has_bg:
                    br, bg_c, bb = _parse_hex_color(bg_color_str, "000000")
                    bg_alpha = int(255 * bg_opacity)
                    draw.rectangle([(0, 0), (rw - 1, rh - 1)], fill=(br, bg_c, bb, bg_alpha))

                # Text positioning
                text_pad_x = int(16 * scale) if has_bg else 0
                text_pad_y = int(8 * scale) if has_bg else 0
                scaled_stroke_width = max(0, int(round(stroke_width_text * scale)))
                scaled_letter_spacing = letter_spacing * scale
                scaled_font_size = max(1, int(font_size * scale))
                font = _resolve_font(scaled_font_size, font_family, font_weight)

                line_metrics, line_box_height = _measure_text_lines(
                    text,
                    font,
                    scaled_font_size,
                    line_height,
                    scaled_stroke_width,
                    scaled_letter_spacing,
                )

                for line_index, metric in enumerate(line_metrics):
                    line = str(metric["line"])
                    visual_width = float(metric["width"])
                    bbox_left = float(metric["bbox_left"])
                    bbox_top = float(metric["bbox_top"])

                    if text_align == "right":
                        line_left = rw - text_pad_x - scaled_stroke_width - visual_width
                    elif text_align == "left":
                        line_left = text_pad_x + scaled_stroke_width
                    else:
                        line_left = (rw - visual_width) / 2

                    line_top = text_pad_y + scaled_stroke_width + line_index * line_box_height
                    line_draw_x = line_left - bbox_left
                    line_draw_y = line_top - bbox_top

                    draw_kwargs: dict[str, Any] = {
                        "font": font,
                        "fill": (r, g, b, 255),
                    }
                    if scaled_stroke_width > 0:
                        draw_kwargs["stroke_width"] = scaled_stroke_width
                        draw_kwargs["stroke_fill"] = (s_r, s_g, s_b, 255)

                    if abs(scaled_letter_spacing) < 0.01 or len(line) <= 1:
                        draw.text((line_draw_x, line_draw_y), line, **draw_kwargs)
                        continue

                    cursor_x = line_left
                    for char in line:
                        char_bbox_left, _, char_bbox_right, _ = _get_text_bbox(
                            font,
                            char,
                            stroke_width=scaled_stroke_width,
                        )
                        draw.text(
                            (cursor_x - char_bbox_left, line_draw_y),
                            char,
                            **draw_kwargs,
                        )
                        cursor_x += (
                            max(0.0, char_bbox_right - char_bbox_left) + scaled_letter_spacing
                        )
            else:
                return None

            # Rotation
            if abs(rotation) > 0.01:
                img = img.rotate(-rotation, expand=True, fillcolor=(0, 0, 0, 0))

            # Opacity
            if opacity < 1.0:
                alpha = img.getchannel("A")
                alpha = alpha.point(lambda x: int(x * opacity))
                img.putalpha(alpha)

            output_path = os.path.join(temp_dir, f"smp_overlay_{idx}.png")
            img.save(output_path, "PNG")
            return output_path

        except Exception as e:
            logger.warning(f"Failed to generate sample overlay: {e}")
            return None

    async def _blank_frame(
        self,
        time_ms: int,
        width: int,
        height: int,
        temp_dir: str,
    ) -> dict[str, Any]:
        """Generate a blank black frame."""
        frame_path = os.path.join(temp_dir, "blank.jpg")
        cmd = [
            settings.ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={width}x{height}:r=1:d=0.5",
            "-frames:v",
            "1",
            "-q:v",
            "5",
            frame_path,
        ]
        await asyncio.to_thread(subprocess.run, cmd, capture_output=True)

        with open(frame_path, "rb") as f:
            frame_data = f.read()

        return {
            "time_ms": time_ms,
            "resolution": f"{width}x{height}",
            "frame_base64": base64.b64encode(frame_data).decode("utf-8"),
            "size_bytes": len(frame_data),
        }

    @staticmethod
    def _parse_resolution(resolution: str) -> tuple[int, int]:
        """Parse resolution string 'WxH' into (width, height)."""
        parts = resolution.lower().split("x")
        if len(parts) != 2:
            return 640, 360
        try:
            w = int(parts[0])
            h = int(parts[1])
            # Ensure even dimensions for FFmpeg
            w = w if w % 2 == 0 else w + 1
            h = h if h % 2 == 0 else h + 1
            return w, h
        except ValueError:
            return 640, 360
