"""
Main render pipeline for video compositing.

This module orchestrates the entire rendering process:
1. Parse timeline data
2. Download assets from GCS
3. Process audio (mixing, ducking)
4. Composite video layers
5. Encode final output
6. Upload to GCS
"""

import os
import shutil
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

from src.config import get_settings
from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData

settings = get_settings()


# ============================================================================
# Enums
# ============================================================================


class RenderStatus(Enum):
    """Render job status."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass
class RenderProgress:
    """Progress information for a render job."""

    job_id: str
    status: RenderStatus
    percent: float = 0.0
    current_step: Optional[str] = None
    elapsed_ms: int = 0
    error_message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "percent": self.percent,
            "current_step": self.current_step,
            "elapsed_ms": self.elapsed_ms,
            "error_message": self.error_message,
        }


@dataclass
class RenderConfig:
    """Configuration for video rendering."""

    width: int = 1920
    height: int = 1080
    fps: int = 30
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    crf: int = 18
    preset: str = "medium"
    audio_bitrate: str = "192k"


@dataclass
class RenderJob:
    """Render job information."""

    id: str
    project_id: str
    status: RenderStatus
    config: Optional[RenderConfig] = None
    output_path: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "status": self.status.value,
            "output_path": self.output_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
        }


@dataclass
class TimelineData:
    """Timeline data for rendering."""

    project_id: str
    duration_ms: int
    layers: list[dict[str, Any]] = field(default_factory=list)
    audio_tracks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "project_id": self.project_id,
            "duration_ms": self.duration_ms,
            "layers": self.layers,
            "audio_tracks": self.audio_tracks,
        }


@dataclass
class UndoableAction:
    """Represents an action that can be undone/redone."""

    id: str
    action_type: str
    description: str
    data: dict[str, Any]
    reverse_data: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================================
# Undo Manager
# ============================================================================


class UndoManager:
    """Manages undo/redo history for timeline editing."""

    def __init__(self, max_history: int = 50):
        self.max_history = max_history
        self._undo_stack: deque[UndoableAction] = deque(maxlen=max_history)
        self._redo_stack: list[UndoableAction] = []

    def execute(self, action: UndoableAction) -> None:
        """Execute an action and add to undo stack."""
        self._undo_stack.append(action)
        self._redo_stack.clear()

    def undo(self) -> Optional[UndoableAction]:
        """Undo the last action."""
        if not self._undo_stack:
            return None
        action = self._undo_stack.pop()
        self._redo_stack.append(action)
        return action

    def redo(self) -> Optional[UndoableAction]:
        """Redo the last undone action."""
        if not self._redo_stack:
            return None
        action = self._redo_stack.pop()
        self._undo_stack.append(action)
        return action

    def can_undo(self) -> bool:
        """Check if undo is available."""
        return len(self._undo_stack) > 0

    def can_redo(self) -> bool:
        """Check if redo is available."""
        return len(self._redo_stack) > 0

    def get_undo_description(self) -> Optional[str]:
        """Get description of the next undo action."""
        if not self._undo_stack:
            return None
        return self._undo_stack[-1].description

    def get_redo_description(self) -> Optional[str]:
        """Get description of the next redo action."""
        if not self._redo_stack:
            return None
        return self._redo_stack[-1].description

    def clear(self) -> None:
        """Clear all history."""
        self._undo_stack.clear()
        self._redo_stack.clear()


class RenderPipeline:
    """
    Main render pipeline for compositing Udemy course videos.

    Handles:
    - 5-layer video compositing
    - Chroma key processing
    - Text overlays
    - Audio mixing with BGM ducking
    - Final encoding to H.264/AAC
    """

    def __init__(
        self,
        job_id: Optional[str] = None,
        project_id: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: Optional[int] = None,
    ):
        self.job_id = job_id
        self.project_id = project_id
        # Use project dimensions if provided, otherwise fall back to settings
        self.width = width or settings.render_output_width
        self.height = height or settings.render_output_height
        self.fps = fps or settings.render_fps

        # Job management storage
        self._jobs: dict[str, RenderJob] = {}
        self._timelines: dict[str, TimelineData] = {}
        self._progress: dict[str, RenderProgress] = {}
        self._progress_callbacks: dict[str, Callable[[RenderProgress], None]] = {}

        # Work directories (only created if job_id provided)
        if job_id:
            self.work_dir = tempfile.mkdtemp(prefix=f"douga_render_{job_id}_")
            self.assets_dir = os.path.join(self.work_dir, "assets")
            self.output_dir = os.path.join(self.work_dir, "output")
            os.makedirs(self.assets_dir, exist_ok=True)
            os.makedirs(self.output_dir, exist_ok=True)
            self.audio_mixer = AudioMixer(self.output_dir)
        else:
            self.work_dir = ""
            self.assets_dir = ""
            self.output_dir = ""
            self.audio_mixer = None

        self.ffmpeg_path = settings.ffmpeg_path
        self._progress_callback: Any = None

    def set_progress_callback(self, callback: Any) -> None:
        """Set callback for progress updates."""
        self._progress_callback = callback

    def _update_progress(self, progress: int, stage: str) -> None:
        """Update render progress."""
        if self._progress_callback:
            self._progress_callback(progress, stage)

    async def render(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],  # asset_id -> local file path
        output_path: str,
    ) -> str:
        """
        Execute the full render pipeline.

        Args:
            timeline_data: Project timeline data
            assets: Map of asset IDs to local file paths
            output_path: Output video file path

        Returns:
            Path to rendered video
        """
        self._update_progress(5, "Preparing render")

        duration_ms = timeline_data.get("duration_ms", 0)
        if duration_ms <= 0:
            raise ValueError("Timeline duration must be greater than 0")

        # Step 1: Mix audio
        self._update_progress(10, "Mixing audio")
        audio_path = await self._mix_audio(timeline_data, assets, duration_ms)

        # Step 2: Composite video layers
        self._update_progress(30, "Compositing video")
        video_path = await self._composite_video(timeline_data, assets, duration_ms)

        # Step 3: Combine audio and video
        self._update_progress(80, "Encoding final video")
        await self._encode_final(video_path, audio_path, output_path, duration_ms)

        # Step 4: Cleanup
        self._update_progress(95, "Cleaning up")
        self._cleanup()

        self._update_progress(100, "Complete")
        return output_path

    async def _mix_audio(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],
        duration_ms: int,
    ) -> str:
        """Mix all audio tracks."""
        audio_tracks = timeline_data.get("audio_tracks", [])
        tracks: list[AudioTrackData] = []

        for track_data in audio_tracks:
            clips: list[AudioClipData] = []

            for clip_data in track_data.get("clips", []):
                asset_id = clip_data.get("asset_id")
                if asset_id and asset_id in assets:
                    clips.append(
                        AudioClipData(
                            file_path=assets[asset_id],
                            start_ms=clip_data.get("start_ms", 0),
                            duration_ms=clip_data.get("duration_ms", 0),
                            in_point_ms=clip_data.get("in_point_ms", 0),
                            out_point_ms=clip_data.get("out_point_ms"),
                            volume=clip_data.get("volume", 1.0),
                            fade_in_ms=clip_data.get("fade_in_ms", 0),
                            fade_out_ms=clip_data.get("fade_out_ms", 0),
                        )
                    )

            ducking = track_data.get("ducking", {})
            tracks.append(
                AudioTrackData(
                    track_type=track_data.get("type", "se"),
                    volume=track_data.get("volume", 1.0),
                    clips=clips if clips else None,
                    ducking_enabled=ducking.get("enabled", False),
                    duck_to=ducking.get("duck_to", 0.1),
                    attack_ms=ducking.get("attack_ms", 200),
                    release_ms=ducking.get("release_ms", 500),
                )
            )

        output_path = os.path.join(self.output_dir, "mixed_audio.aac")
        return self.audio_mixer.mix_tracks(tracks, output_path, duration_ms)

    async def _composite_video(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],
        duration_ms: int,
    ) -> str:
        """
        Composite all video layers.

        Layer order (bottom to top):
        1. Background
        2. Content (slides, screen capture)
        3. Avatar (with chroma key)
        4. Effects (particles, etc.)
        5. Text overlays
        """
        layers = timeline_data.get("layers", [])
        output_path = os.path.join(self.output_dir, "composite.mp4")

        # For MVP, create a simple black background if no layers
        if not layers or all(not layer.get("clips") for layer in layers):
            return self._create_blank_video(output_path, duration_ms)

        # Build FFmpeg filter complex for layer compositing
        inputs = []
        filter_parts = []
        input_idx = 0

        # Sort layers by order
        sorted_layers = sorted(layers, key=lambda x: x.get("order", 0))

        # Create base canvas (black background)
        width = self.width
        height = self.height
        fps = self.fps
        duration_s = duration_ms / 1000

        # Add color source as base
        inputs.extend([
            "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:r={fps}:d={duration_s}"
        ])
        current_output = f"{input_idx}:v"
        input_idx += 1

        # Process each layer
        for layer in sorted_layers:
            if not layer.get("visible", True):
                continue

            clips = layer.get("clips", [])
            if not clips:
                continue

            layer_type = layer.get("type", "content")

            for clip in clips:
                asset_id = str(clip.get("asset_id", ""))
                if not asset_id or asset_id not in assets:
                    continue

                asset_path = assets[asset_id]
                inputs.extend(["-i", asset_path])

                # Build clip filter
                clip_filter = self._build_clip_filter(
                    input_idx,
                    clip,
                    layer_type,
                    current_output,
                    duration_ms,
                )
                filter_parts.append(clip_filter)

                current_output = f"layer{input_idx}"
                input_idx += 1

        if not filter_parts:
            return self._create_blank_video(output_path, duration_ms)

        # Add final output label
        filter_complex = ";\n".join(filter_parts)
        filter_complex = filter_complex.replace(f"[{current_output}]", "[vout]")

        # Build FFmpeg command
        cmd = [
            self.ffmpeg_path,
            "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-r", str(fps),
            "-pix_fmt", "yuv420p",
            "-t", str(duration_s),
            output_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # Fallback to blank video on error
            return self._create_blank_video(output_path, duration_ms)

        return output_path

    def _build_clip_filter(
        self,
        input_idx: int,
        clip: dict[str, Any],
        layer_type: str,
        base_output: str,
        total_duration_ms: int,
    ) -> str:
        """Build FFmpeg filter for a single clip."""
        transform = clip.get("transform", {})
        effects = clip.get("effects", {})

        clip_filters = []
        output_label = f"layer{input_idx}"

        # Scale/position
        x = transform.get("x", 0)
        y = transform.get("y", 0)
        scale = transform.get("scale", 1.0)
        width = transform.get("width")
        height = transform.get("height")

        if width and height:
            clip_filters.append(f"scale={int(width*scale)}:{int(height*scale)}")
        elif scale != 1.0:
            clip_filters.append(f"scale=iw*{scale}:ih*{scale}")

        # Chroma key for avatar layer
        if layer_type == "avatar":
            chroma_key = effects.get("chroma_key", {})
            if chroma_key.get("enabled", False):
                color = chroma_key.get("color", "#00FF00").replace("#", "0x")
                similarity = chroma_key.get("similarity", 0.3)
                blend = chroma_key.get("blend", 0.1)
                clip_filters.append(f"colorkey={color}:{similarity}:{blend}")

        # Opacity
        opacity = effects.get("opacity", 1.0)
        if opacity < 1.0:
            clip_filters.append(f"format=rgba,colorchannelmixer=aa={opacity}")

        # Build the filter string
        if clip_filters:
            filter_str = f"[{input_idx}:v]" + ",".join(clip_filters) + f"[clip{input_idx}];\n"
            clip_ref = f"clip{input_idx}"
        else:
            filter_str = ""
            clip_ref = f"{input_idx}:v"

        # Overlay on base
        filter_str += f"[{base_output}][{clip_ref}]overlay={int(x)}:{int(y)}:enable='between(t,{clip.get('start_ms', 0)/1000},{(clip.get('start_ms', 0) + clip.get('duration_ms', total_duration_ms))/1000})'[{output_label}]"

        return filter_str

    def _create_blank_video(self, output_path: str, duration_ms: int) -> str:
        """Create a blank black video."""
        duration_s = duration_ms / 1000
        width = self.width
        height = self.height
        fps = self.fps

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:r={fps}:d={duration_s}",
            "-c:v", "libx264",
            "-preset", "medium",
            "-pix_fmt", "yuv420p",
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path

    async def _encode_final(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
        duration_ms: int,
    ) -> str:
        """Combine video and audio into final output."""
        duration_s = duration_ms / 1000

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", settings.render_audio_bitrate,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Final encoding failed: {result.stderr}")

        return output_path

    def _cleanup(self) -> None:
        """Clean up temporary files."""
        try:
            shutil.rmtree(self.work_dir)
        except Exception:
            pass  # Ignore cleanup errors

    # ========================================================================
    # Job Management Methods (for new API)
    # ========================================================================

    def create_job(self, timeline: TimelineData, config: Optional[RenderConfig] = None) -> RenderJob:
        """Create a new render job from timeline data."""
        job_id = str(uuid4())
        job = RenderJob(
            id=job_id,
            project_id=timeline.project_id,
            status=RenderStatus.PENDING,
            config=config or RenderConfig(),
            created_at=datetime.now(timezone.utc),
        )
        self._jobs[job_id] = job
        self._timelines[job_id] = timeline
        self._progress[job_id] = RenderProgress(
            job_id=job_id,
            status=RenderStatus.PENDING,
            percent=0.0,
        )
        return job

    def get_job(self, job_id: str) -> Optional[RenderJob]:
        """Get a render job by ID."""
        return self._jobs.get(job_id)

    def list_jobs(self, project_id: str) -> list[RenderJob]:
        """List all jobs for a project."""
        return [job for job in self._jobs.values() if job.project_id == project_id]

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a pending or processing job."""
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.status in (RenderStatus.PENDING, RenderStatus.PROCESSING):
            job.status = RenderStatus.CANCELLED
            return True
        return False

    def get_progress(self, job_id: str) -> Optional[RenderProgress]:
        """Get progress for a render job."""
        return self._progress.get(job_id)

    def register_progress_callback(
        self, job_id: str, callback: Callable[[RenderProgress], None]
    ) -> None:
        """Register a callback for progress updates."""
        self._progress_callbacks[job_id] = callback

    def _notify_progress(self, progress: RenderProgress) -> None:
        """Notify registered callbacks of progress update."""
        callback = self._progress_callbacks.get(progress.job_id)
        if callback:
            callback(progress)
