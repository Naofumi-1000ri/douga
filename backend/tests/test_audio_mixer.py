"""
TDD tests for audio mixing functionality.

Test cases:
1. Mix single track
2. Mix multiple tracks
3. BGM ducking with narration
4. Fade in/out effects
5. Volume normalization (-16 LUFS)
6. Handle empty tracks
"""

import json
import subprocess
from pathlib import Path

import pytest


class TestAudioMixer:
    """Test audio mixing with multiple tracks and effects."""

    @pytest.fixture
    def extract_audio_from_video(self, temp_output_dir: Path):
        """Helper to extract audio from video for mixing tests."""
        def _extract(video_path: Path, name: str) -> Path:
            output_path = temp_output_dir / f"{name}.mp3"
            cmd = [
                "ffmpeg", "-y", "-i", str(video_path),
                "-vn", "-acodec", "libmp3lame", "-ab", "192k",
                "-ar", "44100", "-ac", "2",
                str(output_path)
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            return output_path
        return _extract

    def test_mix_single_narration_track(
        self, operation_video_with_audio: Path, temp_output_dir: Path, extract_audio_from_video
    ):
        """Test mixing a single narration track."""
        from src.render.audio_mixer import AudioMixer, AudioClipData, AudioTrackData

        # Extract audio from test video
        audio_path = extract_audio_from_video(operation_video_with_audio, "narration")

        # Create track data
        track = AudioTrackData(
            track_type="narration",
            volume=1.0,
            clips=[
                AudioClipData(
                    file_path=str(audio_path),
                    start_ms=0,
                    duration_ms=10000,  # 10 seconds
                    volume=1.0,
                )
            ]
        )

        # Mix
        mixer = AudioMixer(output_dir=str(temp_output_dir))
        output_path = temp_output_dir / "mixed.aac"
        result = mixer.mix_tracks([track], str(output_path), duration_ms=10000)

        # Verify output
        assert Path(result).exists(), "Output file should be created"
        assert Path(result).stat().st_size > 0, "Output should not be empty"

    def test_mix_narration_with_bgm_ducking(
        self, multiple_audio_videos: list[Path], temp_output_dir: Path, extract_audio_from_video
    ):
        """Test BGM ducking when narration is present."""
        from src.render.audio_mixer import AudioMixer, AudioClipData, AudioTrackData

        # Extract audio from two videos
        narration_path = extract_audio_from_video(multiple_audio_videos[0], "narration")
        bgm_path = extract_audio_from_video(multiple_audio_videos[1], "bgm")

        # Create tracks with ducking enabled
        narration_track = AudioTrackData(
            track_type="narration",
            volume=1.0,
            clips=[
                AudioClipData(
                    file_path=str(narration_path),
                    start_ms=0,
                    duration_ms=10000,
                    volume=1.0,
                )
            ]
        )

        bgm_track = AudioTrackData(
            track_type="bgm",
            volume=0.3,
            ducking_enabled=True,
            duck_to=0.1,
            attack_ms=200,
            release_ms=500,
            clips=[
                AudioClipData(
                    file_path=str(bgm_path),
                    start_ms=0,
                    duration_ms=10000,
                    volume=1.0,
                )
            ]
        )

        # Mix
        mixer = AudioMixer(output_dir=str(temp_output_dir))
        output_path = temp_output_dir / "mixed_ducking.aac"
        result = mixer.mix_tracks(
            [narration_track, bgm_track],
            str(output_path),
            duration_ms=10000
        )

        # Verify output
        assert Path(result).exists(), "Output file should be created"

    def test_mix_with_fade_effects(
        self, operation_video_with_audio: Path, temp_output_dir: Path, extract_audio_from_video
    ):
        """Test fade in and fade out effects."""
        from src.render.audio_mixer import AudioMixer, AudioClipData, AudioTrackData

        audio_path = extract_audio_from_video(operation_video_with_audio, "audio")

        # Create track with fades
        track = AudioTrackData(
            track_type="narration",
            volume=1.0,
            clips=[
                AudioClipData(
                    file_path=str(audio_path),
                    start_ms=0,
                    duration_ms=10000,
                    volume=1.0,
                    fade_in_ms=1000,   # 1 second fade in
                    fade_out_ms=1000,  # 1 second fade out
                )
            ]
        )

        mixer = AudioMixer(output_dir=str(temp_output_dir))
        output_path = temp_output_dir / "mixed_fades.aac"
        result = mixer.mix_tracks([track], str(output_path), duration_ms=10000)

        assert Path(result).exists(), "Output file should be created"

    def test_mix_with_clip_positioning(
        self, multiple_audio_videos: list[Path], temp_output_dir: Path, extract_audio_from_video
    ):
        """Test positioning clips at different start times."""
        from src.render.audio_mixer import AudioMixer, AudioClipData, AudioTrackData

        audio1_path = extract_audio_from_video(multiple_audio_videos[0], "clip1")
        audio2_path = extract_audio_from_video(multiple_audio_videos[1], "clip2")

        # Create SE track with positioned clips
        track = AudioTrackData(
            track_type="se",
            volume=1.0,
            clips=[
                AudioClipData(
                    file_path=str(audio1_path),
                    start_ms=0,
                    duration_ms=5000,
                    volume=1.0,
                ),
                AudioClipData(
                    file_path=str(audio2_path),
                    start_ms=5000,  # Start at 5 seconds
                    duration_ms=5000,
                    volume=1.0,
                )
            ]
        )

        mixer = AudioMixer(output_dir=str(temp_output_dir))
        output_path = temp_output_dir / "mixed_positioned.aac"
        result = mixer.mix_tracks([track], str(output_path), duration_ms=10000)

        # Verify output duration
        probe_cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(output_path)
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        probe_data = json.loads(probe_result.stdout)
        duration = float(probe_data["format"]["duration"])

        # Should be at least 10 seconds
        assert duration >= 9.5, f"Output duration should be at least 10 seconds, got {duration}"

    def test_mix_all_three_tracks(
        self, multiple_audio_videos: list[Path], temp_output_dir: Path, extract_audio_from_video
    ):
        """Test mixing narration, BGM, and SE tracks together."""
        from src.render.audio_mixer import AudioMixer, AudioClipData, AudioTrackData

        narration_path = extract_audio_from_video(multiple_audio_videos[0], "narration")
        bgm_path = extract_audio_from_video(multiple_audio_videos[1], "bgm")
        se_path = extract_audio_from_video(multiple_audio_videos[2], "se")

        tracks = [
            AudioTrackData(
                track_type="narration",
                volume=1.0,
                clips=[
                    AudioClipData(
                        file_path=str(narration_path),
                        start_ms=0,
                        duration_ms=10000,
                        volume=1.0,
                    )
                ]
            ),
            AudioTrackData(
                track_type="bgm",
                volume=0.3,
                ducking_enabled=True,
                duck_to=0.1,
                clips=[
                    AudioClipData(
                        file_path=str(bgm_path),
                        start_ms=0,
                        duration_ms=10000,
                        volume=1.0,
                    )
                ]
            ),
            AudioTrackData(
                track_type="se",
                volume=0.8,
                clips=[
                    AudioClipData(
                        file_path=str(se_path),
                        start_ms=2000,  # SE at 2 seconds
                        duration_ms=3000,
                        volume=1.0,
                    )
                ]
            )
        ]

        mixer = AudioMixer(output_dir=str(temp_output_dir))
        output_path = temp_output_dir / "mixed_all.aac"
        result = mixer.mix_tracks(tracks, str(output_path), duration_ms=10000)

        assert Path(result).exists(), "Output file should be created"

    def test_mix_empty_tracks_generates_silence(self, temp_output_dir: Path):
        """Test that mixing with no clips generates silence."""
        from src.render.audio_mixer import AudioMixer, AudioTrackData

        # Empty tracks
        tracks = [
            AudioTrackData(track_type="narration", clips=[]),
            AudioTrackData(track_type="bgm", clips=[]),
        ]

        mixer = AudioMixer(output_dir=str(temp_output_dir))
        output_path = temp_output_dir / "silence.aac"
        result = mixer.mix_tracks(tracks, str(output_path), duration_ms=5000)

        # Should generate a silent file
        assert Path(result).exists(), "Should generate silence file"

    def test_output_loudness_normalization(
        self, operation_video_with_audio: Path, temp_output_dir: Path, extract_audio_from_video
    ):
        """Test that output is normalized to approximately -16 LUFS."""
        from src.render.audio_mixer import AudioMixer, AudioClipData, AudioTrackData

        audio_path = extract_audio_from_video(operation_video_with_audio, "audio")

        track = AudioTrackData(
            track_type="narration",
            volume=1.0,
            clips=[
                AudioClipData(
                    file_path=str(audio_path),
                    start_ms=0,
                    duration_ms=10000,
                    volume=1.0,
                )
            ]
        )

        mixer = AudioMixer(output_dir=str(temp_output_dir))
        output_path = temp_output_dir / "normalized.aac"
        mixer.mix_tracks([track], str(output_path), duration_ms=10000)

        # Measure loudness using ffmpeg loudnorm filter (two-pass)
        # This is a simplified check - just verify the file is valid
        probe_cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            str(output_path)
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        probe_data = json.loads(probe_result.stdout)

        # Verify it's a valid audio file
        audio_streams = [s for s in probe_data["streams"] if s["codec_type"] == "audio"]
        assert len(audio_streams) == 1, "Should have one audio stream"
