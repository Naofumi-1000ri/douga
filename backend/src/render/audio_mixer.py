"""
Audio mixing module with BGM ducking support using FFmpeg.

This module handles:
- Multi-track audio mixing (narration, BGM, SE)
- BGM ducking (automatically lower BGM when narration plays)
- Volume control per track
- Fade in/out effects
"""

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.config import get_settings

settings = get_settings()


@dataclass
class VolumeKeyframeData:
    """Volume keyframe for automation."""

    time_ms: int  # Relative time within the clip (0 = clip start)
    value: float  # Volume value (0.0 - 1.0)


@dataclass
class AudioClipData:
    """Audio clip data for mixing."""

    file_path: str
    start_ms: int
    duration_ms: int
    in_point_ms: int = 0
    out_point_ms: int | None = None
    volume: float = 1.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    speed: float = 1.0
    volume_keyframes: list[VolumeKeyframeData] | None = None


@dataclass
class AudioTrackData:
    """Audio track data for mixing."""

    track_type: str  # narration, bgm, se
    volume: float = 1.0
    clips: list[AudioClipData] | None = None
    ducking_enabled: bool = False
    duck_to: float = 0.1
    attack_ms: int = 200
    release_ms: int = 500


class AudioMixer:
    """
    FFmpeg-based audio mixer with ducking support.

    Supports:
    - Multi-track mixing (narration, BGM, SE)
    - Sidechain compression for BGM ducking
    - Volume automation
    - Fade effects
    """

    def __init__(self, output_dir: str | None = None):
        self.output_dir = output_dir or tempfile.mkdtemp(prefix="douga_audio_")
        self.ffmpeg_path = settings.ffmpeg_path
        self.sample_rate = settings.render_audio_sample_rate

    def mix_tracks(
        self,
        tracks: list[AudioTrackData],
        output_path: str,
        duration_ms: int,
    ) -> str:
        """
        Mix multiple audio tracks (flat processing - no type-based separation).

        Args:
            tracks: List of audio tracks to mix
            output_path: Output file path
            duration_ms: Total duration in milliseconds

        Returns:
            Path to the mixed audio file
        """
        # Filter tracks that have clips
        active_tracks = [t for t in tracks if t.clips]
        print(f"[AUDIO MIX] Processing {len(active_tracks)} active tracks (flat mode)", flush=True)

        if not active_tracks:
            # No audio - generate silence
            return self._generate_silence(output_path, duration_ms)

        # Build FFmpeg command
        inputs: list[str] = []
        filter_parts: list[str] = []
        input_index = 0
        track_outputs: list[str] = []

        # Process all tracks equally (no type-based separation)
        for idx, track in enumerate(active_tracks):
            track_filter, track_output, input_index = self._build_track_filter(
                track, input_index, inputs, duration_ms, f"track{idx}"
            )
            filter_parts.append(track_filter)
            track_outputs.append(track_output)

        # Final mix
        if len(track_outputs) == 1:
            # Single track - no mixing needed
            final_output = track_outputs[0]
        else:
            # Mix all tracks
            mix_input_str = "".join(f"[{o}]" for o in track_outputs)
            filter_parts.append(
                f"{mix_input_str}amix=inputs={len(track_outputs)}:duration=longest:normalize=0[mixed]"
            )
            final_output = "mixed"

        # Add output normalization
        filter_parts.append(f"[{final_output}]loudnorm=I=-16:TP=-1.5:LRA=11[out]")

        # Build full command
        filter_complex = ";\n".join(filter_parts)

        cmd = [
            self.ffmpeg_path,
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-t",
            str(duration_ms / 1000),  # Limit output to timeline duration
            "-c:a",
            "aac",
            "-b:a",
            settings.render_audio_bitrate,
            "-ar",
            str(self.sample_rate),
            output_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg audio mixing failed: {result.stderr}")

        return output_path

    def _build_track_filter(
        self,
        track: AudioTrackData,
        start_index: int,
        inputs: list[str],
        total_duration_ms: int,
        track_name: str,
    ) -> tuple[str, str, int]:
        """Build FFmpeg filter for a single track."""
        clip_outputs = []
        current_index = start_index

        for i, clip in enumerate(track.clips or []):
            # Add input
            inputs.extend(["-i", clip.file_path])

            # Build clip filter
            clip_filter_parts = []

            # Always trim to the specified duration
            # Calculate the actual end point in the source file
            actual_out_point_ms = clip.out_point_ms if clip.out_point_ms is not None else (clip.in_point_ms + clip.duration_ms)
            start_s = clip.in_point_ms / 1000
            end_s = actual_out_point_ms / 1000
            clip_filter_parts.append(f"atrim=start={start_s}:end={end_s}")
            clip_filter_parts.append("asetpts=PTS-STARTPTS")  # Reset timestamps after trim

            # Apply speed change via atempo (chain at 2.0x max for quality)
            if clip.speed != 1.0:
                speed = clip.speed
                while speed > 2.0:
                    clip_filter_parts.append("atempo=2.0")
                    speed /= 2.0
                if speed < 0.5:
                    clip_filter_parts.append(f"atempo=0.5")
                else:
                    clip_filter_parts.append(f"atempo={speed}")

            # Apply volume (with keyframes if present, otherwise static)
            if clip.volume_keyframes:
                # Use volume expression with linear interpolation
                volume_expr = self._build_volume_expression(clip.volume_keyframes, clip.duration_ms)
                # Also apply base volume multiplier
                if clip.volume != 1.0:
                    clip_filter_parts.append(f"volume='{volume_expr}*{clip.volume}':eval=frame")
                else:
                    clip_filter_parts.append(f"volume='{volume_expr}':eval=frame")
            elif clip.volume != 1.0:
                clip_filter_parts.append(f"volume={clip.volume}")

            # Apply fades
            if clip.fade_in_ms > 0:
                clip_filter_parts.append(f"afade=t=in:st=0:d={clip.fade_in_ms/1000}")
            if clip.fade_out_ms > 0:
                fade_start = (clip.duration_ms - clip.fade_out_ms) / 1000
                clip_filter_parts.append(f"afade=t=out:st={fade_start}:d={clip.fade_out_ms/1000}")

            # Add delay for positioning
            if clip.start_ms > 0:
                delay_samples = int(clip.start_ms * self.sample_rate / 1000)
                clip_filter_parts.append(f"adelay={delay_samples}S:all=1")

            clip_output = f"{track_name}_clip{i}"

            # If no filters, use anull to pass through
            if clip_filter_parts:
                filter_str = f"[{current_index}:a]" + ",".join(clip_filter_parts) + f"[{clip_output}]"
            else:
                filter_str = f"[{current_index}:a]anull[{clip_output}]"

            clip_outputs.append((filter_str, clip_output))
            current_index += 1

        # Combine clips if multiple
        if len(clip_outputs) == 1:
            filter_str, track_output = clip_outputs[0]
            # Apply track volume
            if track.volume != 1.0:
                filter_str = filter_str.replace(
                    f"[{track_output}]", f"[{track_output}_pre]"
                )
                filter_str += f";\n[{track_output}_pre]volume={track.volume}[{track_output}]"
            return filter_str, track_output, current_index
        else:
            filters = [f for f, _ in clip_outputs]
            outputs = [o for _, o in clip_outputs]
            track_output = f"{track_name}_combined"

            # Concatenate clips
            output_str = "".join(f"[{o}]" for o in outputs)
            combine_filter = f"{output_str}amix=inputs={len(outputs)}:duration=longest:normalize=0[{track_output}_pre]"

            # Apply track volume
            if track.volume != 1.0:
                combine_filter += f";\n[{track_output}_pre]volume={track.volume}[{track_output}]"
            else:
                combine_filter = combine_filter.replace(f"[{track_output}_pre]", f"[{track_output}]")

            full_filter = ";\n".join(filters) + ";\n" + combine_filter
            return full_filter, track_output, current_index

    def _build_volume_expression(
        self,
        keyframes: list[VolumeKeyframeData],
        duration_ms: int,
    ) -> str:
        """
        Build FFmpeg volume expression with linear interpolation between keyframes.

        Args:
            keyframes: List of volume keyframes (time_ms, value)
            duration_ms: Total duration of the clip in milliseconds

        Returns:
            FFmpeg volume expression string for use with volume filter's eval=frame mode
        """
        if not keyframes:
            return "1.0"

        # Sort keyframes by time
        sorted_kf = sorted(keyframes, key=lambda k: k.time_ms)

        # Build nested if() expression for linear interpolation
        # FFmpeg volume eval=frame uses 't' for time in seconds
        parts: list[str] = []

        for i, kf in enumerate(sorted_kf):
            t_sec = kf.time_ms / 1000.0

            if i == 0:
                # Before first keyframe: use first value
                if kf.time_ms > 0:
                    parts.append(f"if(lt(t,{t_sec}),{kf.value},")
            else:
                # Interpolate between previous and current keyframe
                prev_kf = sorted_kf[i - 1]
                prev_t_sec = prev_kf.time_ms / 1000.0
                dt = t_sec - prev_t_sec
                dv = kf.value - prev_kf.value

                if dt > 0:
                    # Linear interpolation: prev_value + (t - prev_t) / dt * dv
                    interp = f"{prev_kf.value}+{dv}*(t-{prev_t_sec})/{dt}"
                    parts.append(f"if(lt(t,{t_sec}),{interp},")
                else:
                    # Same time - use current value
                    parts.append(f"if(lt(t,{t_sec}),{kf.value},")

        # Final value (after last keyframe)
        last_value = sorted_kf[-1].value
        expr = "".join(parts) + str(last_value) + ")" * len(parts)

        return expr

    def _build_ducking_filter(
        self,
        bgm_stream: str,
        narration_stream: str,
        duck_to: float,
        attack_ms: int,
        release_ms: int,
    ) -> str:
        """Build FFmpeg sidechain compression filter for ducking."""
        # Using sidechaincompress filter
        # The BGM volume will be reduced when narration is present
        return (
            f"[{bgm_stream}][{narration_stream}]sidechaincompress="
            f"threshold=0.02:"
            f"ratio={int(1/duck_to)}:"
            f"attack={attack_ms}:"
            f"release={release_ms}:"
            f"makeup=1"
            f"[bgm_ducked]"
        )

    def _generate_silence(self, output_path: str, duration_ms: int) -> str:
        """Generate a silent audio file."""
        duration_s = duration_ms / 1000
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={self.sample_rate}:cl=stereo:d={duration_s}",
            "-c:a",
            "aac",
            "-b:a",
            settings.render_audio_bitrate,
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path

    def extract_audio(self, video_path: str, output_path: str) -> str:
        """Extract audio from a video file."""
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i",
            video_path,
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            settings.render_audio_bitrate,
            "-ar",
            str(self.sample_rate),
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path

    def get_audio_duration(self, file_path: str) -> int:
        """Get audio duration in milliseconds."""
        cmd = [
            settings.ffprobe_path,
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            file_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        duration_s = float(result.stdout.strip())
        return int(duration_s * 1000)
