"""
TDD tests for audio extraction functionality.

Test cases:
1. Extract audio from video with audio track
2. Handle video without audio track
3. Verify output format (MP3, 192kbps, 44100Hz, stereo)
4. Verify duration matches source
"""

import json
import subprocess
from pathlib import Path

import pytest


class TestAudioExtraction:
    """Test audio extraction from video files."""

    def test_extract_audio_from_video_with_audio(
        self, operation_video_with_audio: Path, temp_output_dir: Path
    ):
        """Test extracting audio from a video that has an audio track."""
        from src.services.audio_extractor import extract_audio_from_video

        output_path = temp_output_dir / "extracted.mp3"

        # Extract audio
        result = extract_audio_from_video(
            str(operation_video_with_audio),
            str(output_path)
        )

        # Verify output file exists
        assert output_path.exists(), "Output file should be created"
        assert output_path.stat().st_size > 0, "Output file should not be empty"

        # Verify audio format using ffprobe
        probe_cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            str(output_path)
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        probe_data = json.loads(probe_result.stdout)

        # Check audio stream exists
        audio_streams = [s for s in probe_data["streams"] if s["codec_type"] == "audio"]
        assert len(audio_streams) == 1, "Should have exactly one audio stream"

        audio_stream = audio_streams[0]
        assert audio_stream["codec_name"] == "mp3", "Output should be MP3 format"
        assert int(audio_stream["sample_rate"]) == 44100, "Sample rate should be 44100Hz"
        assert audio_stream["channels"] == 2, "Should be stereo"

    def test_extract_audio_duration_matches_source(
        self, operation_video_with_audio: Path, temp_output_dir: Path
    ):
        """Test that extracted audio duration matches source video."""
        from src.services.audio_extractor import extract_audio_from_video

        output_path = temp_output_dir / "extracted.mp3"

        # Get source duration
        probe_cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(operation_video_with_audio)
        ]
        source_probe = subprocess.run(probe_cmd, capture_output=True, text=True)
        source_data = json.loads(source_probe.stdout)
        source_duration = float(source_data["format"]["duration"])

        # Extract audio
        extract_audio_from_video(str(operation_video_with_audio), str(output_path))

        # Get output duration
        probe_cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(output_path)
        ]
        output_probe = subprocess.run(probe_cmd, capture_output=True, text=True)
        output_data = json.loads(output_probe.stdout)
        output_duration = float(output_data["format"]["duration"])

        # Allow 1 second tolerance
        assert abs(source_duration - output_duration) < 1.0, \
            f"Duration mismatch: source={source_duration}, output={output_duration}"

    def test_extract_audio_from_video_without_audio(
        self, storyboard_video_no_audio: Path, temp_output_dir: Path
    ):
        """Test handling video that has no audio track."""
        from src.services.audio_extractor import extract_audio_from_video

        output_path = temp_output_dir / "extracted.mp3"

        # Should raise an error or return None
        with pytest.raises(Exception):
            extract_audio_from_video(
                str(storyboard_video_no_audio),
                str(output_path)
            )

    def test_extract_audio_invalid_input(self, temp_output_dir: Path):
        """Test handling invalid input file."""
        from src.services.audio_extractor import extract_audio_from_video

        output_path = temp_output_dir / "extracted.mp3"

        with pytest.raises(Exception):
            extract_audio_from_video(
                "/nonexistent/video.mp4",
                str(output_path)
            )

    def test_extract_audio_output_bitrate(
        self, operation_video_with_audio: Path, temp_output_dir: Path
    ):
        """Test that output bitrate is approximately 192kbps."""
        from src.services.audio_extractor import extract_audio_from_video

        output_path = temp_output_dir / "extracted.mp3"
        extract_audio_from_video(str(operation_video_with_audio), str(output_path))

        # Get bitrate
        probe_cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(output_path)
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        probe_data = json.loads(probe_result.stdout)
        bitrate = int(probe_data["format"]["bit_rate"])

        # Allow 10% tolerance (192kbps = 192000bps)
        expected_bitrate = 192000
        tolerance = expected_bitrate * 0.1
        assert abs(bitrate - expected_bitrate) < tolerance, \
            f"Bitrate {bitrate} should be approximately 192kbps"
