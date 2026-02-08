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


class FrameSampler:
    """Renders single frames from a timeline for AI visual inspection."""

    def __init__(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],  # asset_id -> local file path
        project_width: int = 1920,
        project_height: int = 1080,
        project_fps: int = 30,
    ):
        self.timeline = timeline_data
        self.assets = assets
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
            await self._render_single_frame(
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
    ) -> None:
        """Render a single frame using FFmpeg.

        Optimized strategy:
        - Only include clips visible at time_ms (skip all others)
        - Use input-level seeking (-ss before -i) for fast keyframe-based access
        - Short canvas duration (0.1s) instead of full timeline
        - No output-level seeking needed
        """
        import time as time_mod
        t0 = time_mod.monotonic()

        layers = self.timeline.get("layers") or []

        inputs: list[str] = []
        filter_parts: list[str] = []
        input_idx = 0

        sorted_layers = list(reversed(layers))

        # Base canvas - very short duration (only 1 frame needed)
        inputs.extend([
            "-f", "lavfi",
            "-i", f"color=c=black:s={self.project_width}x{self.project_height}:r=1:d=0.1",
        ])
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
                clip_end = clip_start + clip_dur

                # Skip clips not visible at target time
                if clip_dur <= 0 or clip_start > time_ms or clip_end <= time_ms:
                    continue

                # Shape/text clips
                if clip.get("shape") or clip.get("text_content") is not None:
                    png_path = self._generate_simple_overlay(clip, shape_idx, temp_dir)
                    if png_path:
                        inputs.extend(["-i", png_path])
                        transform = clip.get("transform") or {}
                        center_x = transform.get("x") or 0
                        center_y = transform.get("y") or 0

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
                    continue

                # Asset-based clips
                asset_id = str(clip.get("asset_id") or "")
                if not asset_id or asset_id not in self.assets:
                    continue

                asset_path = self.assets[asset_id]

                # Calculate seek position within the asset
                in_point_ms = clip.get("in_point_ms") or 0
                offset_in_asset_ms = in_point_ms + (time_ms - clip_start)
                seek_s = max(0, offset_in_asset_ms / 1000)

                # Input-level seeking (fast keyframe-based access)
                inputs.extend(["-ss", str(seek_s), "-i", asset_path])

                # Build filter (no trim needed - already seeked)
                clip_filter = self._build_sample_clip_filter_fast(
                    input_idx, clip, layer_type, current_output
                )
                filter_parts.append(clip_filter)
                current_output = f"smp{input_idx}"
                input_idx += 1
                has_clips = True

        if not has_clips:
            cmd = [
                settings.ffmpeg_path, "-y",
                "-f", "lavfi",
                "-i", f"color=c=black:s={width}x{height}:r=1:d=0.1",
                "-frames:v", "1",
                "-q:v", "5",
                output_path,
            ]
            await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
            logger.info(f"[FRAME-SAMPLE] Blank frame at {time_ms}ms ({time_mod.monotonic() - t0:.1f}s)")
            return

        filter_parts.append(f"[{current_output}]scale={width}:{height}[smp_out]")
        filter_complex = ";\n".join(filter_parts)

        cmd = [
            settings.ffmpeg_path, "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[smp_out]",
            "-frames:v", "1",
            output_path,
        ]

        logger.info(f"[FRAME-SAMPLE] Rendering at {time_ms}ms with {input_idx - 1} inputs...")
        result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
        elapsed = time_mod.monotonic() - t0

        if result.returncode != 0:
            logger.warning(f"[FRAME-SAMPLE] Failed at {time_ms}ms ({elapsed:.1f}s): {result.stderr[:300]}")
            cmd = [
                settings.ffmpeg_path, "-y",
                "-f", "lavfi",
                "-i", f"color=c=black:s={width}x{height}:r=1:d=0.1",
                "-frames:v", "1",
                output_path,
            ]
            await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
        else:
            logger.info(f"[FRAME-SAMPLE] Done at {time_ms}ms ({elapsed:.1f}s)")

    def _build_sample_clip_filter_fast(
        self,
        input_idx: int,
        clip: dict[str, Any],
        layer_type: str,
        base_output: str,
    ) -> str:
        """Build FFmpeg filter for a single clip at a single frame.

        Optimized version: no trim needed (input-level seeking already done),
        no enable condition (clip visibility already checked).
        """
        transform = clip.get("transform") or {}
        effects = clip.get("effects") or {}
        output_label = f"smp{input_idx}"

        clip_filters: list[str] = []

        # Crop
        crop = clip.get("crop") or {}
        crop_top = crop.get("top") or 0
        crop_right = crop.get("right") or 0
        crop_bottom = crop.get("bottom") or 0
        crop_left = crop.get("left") or 0
        has_crop = crop_top > 0 or crop_right > 0 or crop_bottom > 0 or crop_left > 0

        # Scale
        x = transform.get("x") or 0
        y = transform.get("y") or 0
        scale = transform.get("scale") or 1.0
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
                similarity = 0.05
            blend = chroma_key.get("blend")
            if blend is None:
                blend = 0.0
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

        # Opacity
        opacity = effects.get("opacity")
        if opacity is None:
            opacity = 1.0
        if opacity < 1.0:
            target_list.append(f"format=rgba,colorchannelmixer=aa={opacity}")

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
        crop_offset_x = 0
        crop_offset_y = 0
        if has_crop:
            if w and h:
                sw = int(w * scale)
                sh = int(h * scale)
            else:
                sw = sh = 0
            crop_offset_x = int(sw * (crop_left - crop_right) / 2)
            crop_offset_y = int(sh * (crop_top - crop_bottom) / 2)
        overlay_x = f"(main_w/2)+({int(x) + crop_offset_x})-(overlay_w/2)"
        overlay_y = f"(main_h/2)+({int(y) + crop_offset_y})-(overlay_h/2)"

        filter_str += (
            f"[{base_output}][{clip_ref}]overlay="
            f"x={overlay_x}:y={overlay_y}"
            f"[{output_label}]"
        )

        return filter_str

    def _generate_simple_overlay(
        self,
        clip: dict[str, Any],
        idx: int,
        temp_dir: str,
    ) -> str | None:
        """Generate a simple PNG overlay for shape/text clips during sampling."""
        try:
            from PIL import Image, ImageDraw, ImageFont

            transform = clip.get("transform") or {}
            width = int(transform.get("width") or 100)
            height = int(transform.get("height") or 50)
            width = max(width, 1)
            height = max(height, 1)

            img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            shape = clip.get("shape")
            if shape:
                fill_color = shape.get("fillColor") or "#ffffff"
                filled = shape.get("filled")
                if filled is None:
                    filled = True
                stroke_color = shape.get("strokeColor") or "#000000"
                stroke_width = int(shape.get("strokeWidth") or 2)

                r, g, b = _parse_hex_color(fill_color, "ffffff")

                if shape.get("type") == "circle":
                    if filled:
                        draw.ellipse([(0, 0), (width - 1, height - 1)], fill=(r, g, b, 255))
                    else:
                        draw.ellipse([(0, 0), (width - 1, height - 1)], outline=(r, g, b, 255), width=stroke_width)
                else:
                    if filled:
                        draw.rectangle([(0, 0), (width - 1, height - 1)], fill=(r, g, b, 255))
                    else:
                        draw.rectangle([(0, 0), (width - 1, height - 1)], outline=(r, g, b, 255), width=stroke_width)

            elif clip.get("text_content") is not None:
                text = clip["text_content"]
                text_style = clip.get("text_style") or {}
                color = text_style.get("color") or "#ffffff"
                r, g, b = _parse_hex_color(color, "ffffff")

                font_size = int(text_style.get("fontSize") or 24)
                try:
                    font = ImageFont.truetype(
                        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc", font_size
                    )
                except Exception:
                    font = ImageFont.load_default()

                draw.text((4, 4), text, font=font, fill=(r, g, b, 255))

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
            settings.ffmpeg_path, "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:r=1:d=0.1",
            "-frames:v", "1",
            "-q:v", "5",
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
