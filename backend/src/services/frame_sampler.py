"""Frame sampling service for AI visual inspection.

Renders single frames from the timeline at specified times using FFmpeg.
Produces low-resolution JPEG images for quick AI analysis.
"""

import asyncio
import base64
import logging
import math
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
    """Interpolate clip transform values at a given timeline time (in ms).

    Mirrors the frontend ``getInterpolatedTransform()`` from keyframes.ts.

    Args:
        clip: Clip dict containing transform, effects, and optional keyframes list.
        time_ms: Absolute timeline time in milliseconds.

    Returns:
        Dict with keys: x, y, scale, rotation, opacity
    """
    transform = clip.get("transform") or {}
    effects = clip.get("effects") or {}

    base: dict[str, Any] = {
        "x": transform.get("x") or 0,
        "y": transform.get("y") or 0,
        "scale": transform.get("scale") if transform.get("scale") is not None else 1.0,
        "rotation": transform.get("rotation") or 0,
        "opacity": effects.get("opacity") if effects.get("opacity") is not None else 1.0,
    }

    keyframes = clip.get("keyframes") or []
    if not keyframes:
        return base

    # time_in_clip = time relative to clip start
    clip_start = clip.get("start_ms") or 0
    time_in_clip_ms = time_ms - clip_start

    # Sort keyframes by time
    sorted_kfs = sorted(keyframes, key=lambda k: k.get("time_ms", 0))

    def _kf_val(kf: dict[str, Any]) -> dict[str, Any]:
        kf_transform = kf.get("transform") or {}
        kf_opacity = kf.get("opacity")
        return {
            "x": kf_transform.get("x") or 0,
            "y": kf_transform.get("y") or 0,
            "scale": kf_transform.get("scale") if kf_transform.get("scale") is not None else 1.0,
            "rotation": kf_transform.get("rotation") or 0,
            "opacity": kf_opacity if kf_opacity is not None else base["opacity"],
        }

    # Before first keyframe
    if time_in_clip_ms <= sorted_kfs[0].get("time_ms", 0):
        return _kf_val(sorted_kfs[0])

    # After last keyframe
    if time_in_clip_ms >= sorted_kfs[-1].get("time_ms", 0):
        return _kf_val(sorted_kfs[-1])

    # Find surrounding keyframes
    prev_kf = None
    next_kf = None
    for i in range(len(sorted_kfs) - 1):
        t0 = sorted_kfs[i].get("time_ms", 0)
        t1 = sorted_kfs[i + 1].get("time_ms", 0)
        if time_in_clip_ms >= t0 and time_in_clip_ms < t1:
            prev_kf = sorted_kfs[i]
            next_kf = sorted_kfs[i + 1]
            break

    if prev_kf is None or next_kf is None:
        # Fallback: closest keyframe
        closest = min(sorted_kfs, key=lambda k: abs(k.get("time_ms", 0) - time_in_clip_ms))
        return _kf_val(closest)

    # Linear interpolation
    duration = next_kf.get("time_ms", 0) - prev_kf.get("time_ms", 0)
    elapsed = time_in_clip_ms - prev_kf.get("time_ms", 0)
    t = elapsed / duration if duration > 0 else 0.0

    pv = _kf_val(prev_kf)
    nv = _kf_val(next_kf)

    def lerp(a: float, b: float) -> float:
        return a + (b - a) * t

    return {
        "x": lerp(pv["x"], nv["x"]),
        "y": lerp(pv["y"], nv["y"]),
        "scale": lerp(pv["scale"], nv["scale"]),
        "rotation": lerp(pv["rotation"], nv["rotation"]),
        "opacity": lerp(pv["opacity"], nv["opacity"]),
    }


def _calculate_fade_opacity(
    time_in_clip_ms: int | float,
    duration_ms: int | float,
    fade_in_ms: int | float,
    fade_out_ms: int | float,
) -> float:
    """Calculate fade opacity multiplier at a given time within a clip.

    Mirrors the frontend ``calculateFadeOpacity()`` from editorPreviewStageShared.ts.
    """
    multiplier = 1.0
    if fade_in_ms > 0 and time_in_clip_ms < fade_in_ms:
        multiplier = min(multiplier, time_in_clip_ms / fade_in_ms)
    time_from_end = duration_ms - time_in_clip_ms
    if fade_out_ms > 0 and time_from_end < fade_out_ms:
        multiplier = min(multiplier, time_from_end / fade_out_ms)
    return max(0.0, min(1.0, multiplier))


def _get_clip_fade_durations_ms(clip: dict[str, Any]) -> tuple[int, int]:
    """Return (fade_in_ms, fade_out_ms) for the clip, matching pipeline logic."""
    transition_in = clip.get("transition_in") or {}
    transition_out = clip.get("transition_out") or {}

    if transition_in.get("type") == "fade":
        fade_in_ms = transition_in.get("duration_ms", 0)
    else:
        fade_in_ms = clip.get("fade_in_ms", 0)

    if transition_out.get("type") == "fade":
        fade_out_ms = transition_out.get("duration_ms", 0)
    else:
        fade_out_ms = clip.get("fade_out_ms", 0)

    return max(0, int(fade_in_ms or 0)), max(0, int(fade_out_ms or 0))


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

                # freeze_frame: if we are in the freeze zone, seek to last frame of clip content
                if freeze_frame_ms > 0 and time_ms >= clip_start + clip_dur:
                    # In freeze zone: always sample the last frame of playback content
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

        # Interpolate transform at current time (handles keyframes)
        interp = _interpolate_transform_at(clip, time_ms)
        x = interp["x"]
        y = interp["y"]
        scale = interp["scale"]
        rotation = interp["rotation"]
        opacity = interp["opacity"]

        # Apply fade on top of (possibly keyframe-interpolated) opacity
        clip_dur = clip.get("duration_ms") or 0
        clip_start = clip.get("start_ms") or 0
        time_in_clip = max(0, time_ms - clip_start)
        fade_in_ms, fade_out_ms = _get_clip_fade_durations_ms(clip)
        fade_mult = _calculate_fade_opacity(time_in_clip, clip_dur, fade_in_ms, fade_out_ms)
        opacity = max(0.0, min(1.0, opacity * fade_mult))

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

        # Scale
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

        # Rotation (after scale/crop, before opacity)
        if abs(rotation) > 0.01:
            target_list.append("format=rgba")
            target_list.append(
                f"rotate=({rotation})*PI/180:ow=hypot(iw,ih):oh=hypot(iw,ih):fillcolor=none"
            )

        # Opacity (combined base opacity + fade)
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
        # x/y are CENTER offsets from canvas CENTER; overlay expects TOP-LEFT.
        # Adjust for crop offset so visible content stays in the correct position.
        crop_offset_x_expr = "0"
        crop_offset_y_expr = "0"
        if has_crop:
            width_ratio = 1 - crop_left - crop_right
            height_ratio = 1 - crop_top - crop_bottom
            if width_ratio > 0 and (w and h):
                # explicit size: integer pixel offset
                sw = int(w * scale)
                crop_offset_x = int(sw * (crop_left - crop_right) / (2 * width_ratio))
                crop_offset_x_expr = str(crop_offset_x)
            elif width_ratio > 0:
                # unknown size: use overlay_w expression
                crop_offset_x_expr = (
                    f"(overlay_w*{(crop_left - crop_right) / (2 * width_ratio):.6f})"
                )
            if height_ratio > 0 and (w and h):
                sh = int(h * scale)
                crop_offset_y = int(sh * (crop_top - crop_bottom) / (2 * height_ratio))
                crop_offset_y_expr = str(crop_offset_y)
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
            from PIL import Image, ImageDraw, ImageFont

            transform = clip.get("transform") or {}
            effects = clip.get("effects") or {}
            shape = clip.get("shape")

            # Determine base dimensions: prefer shape dimensions, then transform, then auto-size
            if shape and shape.get("width") and shape.get("height"):
                width = int(shape["width"])
                height = int(shape["height"])
            elif transform.get("width") and transform.get("height"):
                width = int(transform["width"])
                height = int(transform["height"])
            else:
                width = 0  # will auto-size for text
                height = 0

            # Interpolate transform at current time
            interp = _interpolate_transform_at(clip, time_ms)
            scale = interp["scale"]
            rotation = interp["rotation"]
            opacity = interp["opacity"]

            # Apply fade
            clip_dur = clip.get("duration_ms") or 0
            clip_start = clip.get("start_ms") or 0
            time_in_clip = max(0, time_ms - clip_start)
            fade_in_ms, fade_out_ms = _get_clip_fade_durations_ms(clip)
            fade_mult = _calculate_fade_opacity(time_in_clip, clip_dur, fade_in_ms, fade_out_ms)
            opacity = max(0.0, min(1.0, opacity * fade_mult))

            # For text clips with no explicit dimensions, we need to auto-size
            # after loading the font. For shapes, dimensions come from shape data.
            if shape:
                width = max(width, 1)
                height = max(height, 1)
                render_width = max(1, int(width * scale))
                render_height = max(1, int(height * scale))

                img = Image.new("RGBA", (render_width, render_height), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)

                fill_color = shape.get("fillColor") or "#ffffff"
                filled = shape.get("filled")
                if filled is None:
                    filled = True
                stroke_width = int(shape.get("strokeWidth") or 2)

                # Handle "transparent" fill color
                if fill_color.lower() in ("transparent", "none", ""):
                    fill_rgba = (0, 0, 0, 0)
                else:
                    r, g, b = _parse_hex_color(fill_color, "ffffff")
                    fill_rgba = (r, g, b, 255)

                # Stroke color
                stroke_color_str = shape.get("strokeColor") or "#000000"
                sr, sg, sb = _parse_hex_color(stroke_color_str, "000000")
                stroke_rgba = (sr, sg, sb, 255)

                if shape.get("type") == "circle":
                    if filled and fill_rgba[3] > 0:
                        draw.ellipse(
                            [(0, 0), (render_width - 1, render_height - 1)], fill=fill_rgba
                        )
                    if not filled or stroke_width > 0:
                        draw.ellipse(
                            [(0, 0), (render_width - 1, render_height - 1)],
                            outline=stroke_rgba,
                            width=stroke_width,
                        )
                else:
                    if filled and fill_rgba[3] > 0:
                        draw.rectangle(
                            [(0, 0), (render_width - 1, render_height - 1)], fill=fill_rgba
                        )
                    if not filled or stroke_width > 0:
                        draw.rectangle(
                            [(0, 0), (render_width - 1, render_height - 1)],
                            outline=stroke_rgba,
                            width=stroke_width,
                        )

            elif clip.get("text_content") is not None:
                text = clip["text_content"]
                text_style = clip.get("text_style") or {}
                color = text_style.get("color") or "#ffffff"
                r, g, b = _parse_hex_color(color, "ffffff")

                font_size = int(text_style.get("fontSize") or 24)
                stroke_width_text = int(text_style.get("strokeWidth") or 0)
                stroke_color_str = text_style.get("strokeColor") or "#000000"
                sr, sg, sb = _parse_hex_color(stroke_color_str, "000000")

                font = None
                for font_path in [
                    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Debian/Docker
                    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
                    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",  # macOS
                ]:
                    try:
                        font = ImageFont.truetype(font_path, font_size)
                        break
                    except Exception:
                        continue
                if font is None:
                    font = ImageFont.load_default()

                # Auto-size text if no explicit dimensions
                if width <= 0 or height <= 0:
                    line_height = float(text_style.get("lineHeight") or 1.4)
                    padding = 16  # match frontend padding
                    bbox = draw_tmp_bbox = font.getbbox("A")  # noqa: F841
                    char_h = bbox[3] - bbox[1] if bbox else font_size

                    lines = text.split("\n")
                    max_line_w = 0
                    for line in lines:
                        lbbox = font.getbbox(line)
                        lw = (lbbox[2] - lbbox[0]) if lbbox else len(line) * font_size
                        max_line_w = max(max_line_w, lw)

                    text_h = int(char_h * line_height * len(lines))
                    width = int(max_line_w + padding * 2 + stroke_width_text * 2)
                    height = int(text_h + padding * 2)

                width = max(width, 1)
                height = max(height, 1)
                render_width = max(1, int(width * scale))
                render_height = max(1, int(height * scale))

                img = Image.new("RGBA", (render_width, render_height), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)

                # Background color
                bg_color_str = text_style.get("backgroundColor")
                if bg_color_str:
                    bg_opacity = float(text_style.get("backgroundOpacity", 1.0))
                    br, bg_c, bb = _parse_hex_color(bg_color_str, "000000")
                    bg_alpha = int(255 * bg_opacity)
                    draw.rectangle(
                        [(0, 0), (render_width - 1, render_height - 1)],
                        fill=(br, bg_c, bb, bg_alpha),
                    )

                # Text alignment — PIL anchor is single-line only;
                # use (x, y) positioning + align param for multiline support.
                text_align = text_style.get("textAlign") or "left"
                is_multiline = "\n" in text
                draw_y = 4

                if is_multiline:
                    # multiline: use align param, position x by alignment
                    if text_align == "center":
                        draw_x = render_width // 2
                    elif text_align == "right":
                        draw_x = render_width - 4
                    else:
                        draw_x = 4
                    draw_kwargs: dict[str, Any] = {"align": text_align}
                    # For center/right multiline, we need anchor on first line
                    # but PIL doesn't support anchor+multiline, so we manually
                    # compute x offset per the align setting.
                    if text_align == "center":
                        # Draw from left but use align="center"
                        draw_x = 0
                        draw_kwargs["align"] = "center"
                    elif text_align == "right":
                        draw_x = 0
                        draw_kwargs["align"] = "right"
                    else:
                        draw_x = 4
                        draw_kwargs["align"] = "left"
                else:
                    # single-line: use anchor for precise positioning
                    if text_align == "center":
                        draw_x = render_width // 2
                        draw_kwargs = {"anchor": "mt"}
                    elif text_align == "right":
                        draw_x = render_width - 4
                        draw_kwargs = {"anchor": "rt"}
                    else:
                        draw_x = 4
                        draw_kwargs = {"anchor": "lt"}

                if stroke_width_text > 0:
                    draw.text(
                        (draw_x, draw_y),
                        text,
                        font=font,
                        fill=(r, g, b, 255),
                        stroke_width=stroke_width_text,
                        stroke_fill=(sr, sg, sb, 255),
                        **draw_kwargs,
                    )
                else:
                    draw.text(
                        (draw_x, draw_y),
                        text,
                        font=font,
                        fill=(r, g, b, 255),
                        **draw_kwargs,
                    )

            # Apply rotation (CSS is clockwise, PIL rotate is counter-clockwise)
            if abs(rotation) > 0.01:
                img = img.rotate(-rotation, expand=True, fillcolor=(0, 0, 0, 0))

            # Apply opacity to alpha channel
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
