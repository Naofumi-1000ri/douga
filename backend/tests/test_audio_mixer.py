"""
TDD tests for audio mixing functionality.

Test cases:
1. Mix single track
2. Mix multiple tracks
3. Fade in/out effects
4. Master output limiting
5. Handle empty tracks
6. No auto-ducking in BGM mix command
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
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-vn",
                "-acodec",
                "libmp3lame",
                "-ab",
                "192k",
                "-ar",
                "44100",
                "-ac",
                "2",
                str(output_path),
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            return output_path

        return _extract

    def test_mix_single_narration_track(
        self, operation_video_with_audio: Path, temp_output_dir: Path, extract_audio_from_video
    ):
        """Test mixing a single narration track."""
        from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData

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
            ],
        )

        # Mix
        mixer = AudioMixer(output_dir=str(temp_output_dir))
        output_path = temp_output_dir / "mixed.aac"
        result = mixer.mix_tracks([track], str(output_path), duration_ms=10000)

        # Verify output
        assert Path(result).exists(), "Output file should be created"
        assert Path(result).stat().st_size > 0, "Output should not be empty"

    def test_mix_narration_with_bgm_no_auto_ducking(
        self, multiple_audio_videos: list[Path], temp_output_dir: Path, extract_audio_from_video
    ):
        """Test BGM and narration mix without auto-ducking (manual volume keyframes only)."""
        from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData

        # Extract audio from two videos
        narration_path = extract_audio_from_video(multiple_audio_videos[0], "narration")
        bgm_path = extract_audio_from_video(multiple_audio_videos[1], "bgm")

        # Create tracks without ducking fields
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
            ],
        )

        bgm_track = AudioTrackData(
            track_type="bgm",
            volume=0.3,
            clips=[
                AudioClipData(
                    file_path=str(bgm_path),
                    start_ms=0,
                    duration_ms=10000,
                    volume=1.0,
                )
            ],
        )

        # Mix
        mixer = AudioMixer(output_dir=str(temp_output_dir))
        output_path = temp_output_dir / "mixed_no_ducking.aac"
        result = mixer.mix_tracks([narration_track, bgm_track], str(output_path), duration_ms=10000)

        # Verify output
        assert Path(result).exists(), "Output file should be created"

    def test_mix_with_fade_effects(
        self, operation_video_with_audio: Path, temp_output_dir: Path, extract_audio_from_video
    ):
        """Test fade in and fade out effects."""
        from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData

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
                    fade_in_ms=1000,  # 1 second fade in
                    fade_out_ms=1000,  # 1 second fade out
                )
            ],
        )

        mixer = AudioMixer(output_dir=str(temp_output_dir))
        output_path = temp_output_dir / "mixed_fades.aac"
        result = mixer.mix_tracks([track], str(output_path), duration_ms=10000)

        assert Path(result).exists(), "Output file should be created"

    def test_mix_with_clip_positioning(
        self, multiple_audio_videos: list[Path], temp_output_dir: Path, extract_audio_from_video
    ):
        """Test positioning clips at different start times."""
        from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData

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
                ),
            ],
        )

        mixer = AudioMixer(output_dir=str(temp_output_dir))
        output_path = temp_output_dir / "mixed_positioned.aac"
        mixer.mix_tracks([track], str(output_path), duration_ms=10000)

        # Verify output duration
        probe_cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            str(output_path),
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
        from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData

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
                ],
            ),
            AudioTrackData(
                track_type="bgm",
                volume=0.3,
                clips=[
                    AudioClipData(
                        file_path=str(bgm_path),
                        start_ms=0,
                        duration_ms=10000,
                        volume=1.0,
                    )
                ],
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
                ],
            ),
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

    def test_build_mix_command_uses_static_master_limiter(self):
        """Export mix should avoid dynamic loudness normalization."""
        from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData

        mixer = AudioMixer()
        cmd = mixer.build_mix_command(
            [
                AudioTrackData(
                    track_type="narration",
                    clips=[
                        AudioClipData(
                            file_path="/tmp/test-audio.wav",
                            start_ms=0,
                            duration_ms=5000,
                            volume=1.0,
                        )
                    ],
                )
            ],
            output_path="/tmp/out.aac",
            duration_ms=5000,
        )

        assert cmd is not None
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        assert "loudnorm" not in filter_complex
        assert "alimiter=limit=0.95:level=false[out]" in filter_complex

    def test_build_mix_command_no_auto_ducking_for_bgm(self):
        """BGM tracks must NOT receive auto-ducking volume filter even when narration exists."""
        from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData

        mixer = AudioMixer()
        cmd = mixer.build_mix_command(
            [
                AudioTrackData(
                    track_type="narration",
                    clips=[
                        AudioClipData(
                            file_path="/tmp/narration.wav",
                            start_ms=0,
                            duration_ms=5000,
                        )
                    ],
                ),
                AudioTrackData(
                    track_type="bgm",
                    clips=[
                        AudioClipData(
                            file_path="/tmp/bgm.wav",
                            start_ms=0,
                            duration_ms=5000,
                        )
                    ],
                ),
            ],
            output_path="/tmp/out.aac",
            duration_ms=5000,
        )

        assert cmd is not None
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        # No auto-ducking filters should be present
        assert "sidechaincompress=" not in filter_complex
        assert "_ducked" not in filter_complex
        # Standard limiter still applied
        assert "alimiter=limit=0.95:level=false[out]" in filter_complex

    def test_output_audio_file_is_valid(
        self, operation_video_with_audio: Path, temp_output_dir: Path, extract_audio_from_video
    ):
        """Mixed output should still produce a valid audio file."""
        from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData

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
            ],
        )

        mixer = AudioMixer(output_dir=str(temp_output_dir))
        output_path = temp_output_dir / "normalized.aac"
        mixer.mix_tracks([track], str(output_path), duration_ms=10000)

        probe_cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            str(output_path),
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        probe_data = json.loads(probe_result.stdout)

        # Verify it's a valid audio file
        audio_streams = [s for s in probe_data["streams"] if s["codec_type"] == "audio"]
        assert len(audio_streams) == 1, "Should have one audio stream"


# ------------------------------------------------------------------
# atempo filter chaining (fix for #269)
# ------------------------------------------------------------------


class TestAtempoChaining:
    """Unit tests for atempo filter generation (#269 bug fix).

    atempo accepts values in [0.5, 2.0] only.  For extreme speeds we must
    chain multiple atempo filters rather than emitting a single out-of-range
    value.  The old code only prepended one atempo=0.5 for slow speeds, which
    silently clamped speed=0.25 to 0.5x instead of the correct 0.25x.
    """

    def _get_filter_string(self, speed: float) -> str:
        """Return the filter_complex string for a single-clip track with given speed."""
        from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData

        mixer = AudioMixer()
        cmd = mixer.build_mix_command(
            [
                AudioTrackData(
                    track_type="narration",
                    clips=[
                        AudioClipData(
                            file_path="/tmp/test.wav",
                            start_ms=0,
                            duration_ms=5000,
                            volume=1.0,
                            speed=speed,
                        )
                    ],
                )
            ],
            output_path="/tmp/out.aac",
            duration_ms=5000,
        )
        assert cmd is not None
        return cmd[cmd.index("-filter_complex") + 1]

    def test_speed_0_25_produces_double_atempo_0_5(self):
        """speed=0.25 must chain atempo=0.5,atempo=0.5 (fix for #269)."""
        f = self._get_filter_string(0.25)
        assert "atempo=0.5" in f
        # Two occurrences of atempo=0.5 must appear (chained)
        assert f.count("atempo=0.5") == 2, f"Expected 2x atempo=0.5, got: {f}"

    def test_speed_0_3_produces_chain_plus_remainder(self):
        """speed=0.3 → atempo=0.5 (for the 0.5 step) + atempo=0.6 (remainder: 0.3/0.5=0.6)."""
        f = self._get_filter_string(0.3)
        assert "atempo=0.5" in f
        assert "atempo=0.6" in f

    def test_speed_4_0_produces_double_atempo_2_0(self):
        """speed=4.0 must chain atempo=2.0,atempo=2.0.

        The upper-direction chain (speed > 2.0) already existed before #269;
        this test is a regression guard for that pre-existing behavior.
        """
        f = self._get_filter_string(4.0)
        assert f.count("atempo=2.0") == 2, f"Expected 2x atempo=2.0, got: {f}"

    def test_speed_1_0_produces_no_atempo(self):
        """speed=1.0 must not insert any atempo filter."""
        f = self._get_filter_string(1.0)
        assert "atempo" not in f, f"Unexpected atempo in filter: {f}"

    def test_speed_0_5_produces_single_atempo_0_5(self):
        """speed=0.5 (boundary) must insert exactly one atempo=0.5."""
        f = self._get_filter_string(0.5)
        assert f.count("atempo=0.5") == 1, f"Expected 1x atempo=0.5, got: {f}"

    def test_speed_2_0_produces_single_atempo_2_0(self):
        """speed=2.0 (boundary) must insert exactly one atempo=2.0."""
        f = self._get_filter_string(2.0)
        assert f.count("atempo=2.0") == 1, f"Expected 1x atempo=2.0, got: {f}"
