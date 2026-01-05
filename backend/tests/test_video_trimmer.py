"""Tests for video trimming and processing functionality."""

import tempfile
from pathlib import Path

import pytest

from src.services.video_trimmer import VideoTrimmer, TrimConfig, VideoOutput


class TestVideoTrimmer:
    """Tests for VideoTrimmer class."""

    def test_trim_video_start_to_end(self, test_video_with_audio, temp_output_dir):
        """Test trimming video from start time to end time."""
        trimmer = VideoTrimmer()
        output_path = temp_output_dir / "trimmed.mp4"

        config = TrimConfig(
            start_ms=1000,
            end_ms=5000,
        )

        result = trimmer.trim(
            str(test_video_with_audio),
            str(output_path),
            config,
        )

        assert result.path.exists()
        assert result.duration_ms > 0
        # Duration should be approximately 4 seconds (5000 - 1000)
        assert 3500 <= result.duration_ms <= 4500

    def test_trim_video_from_start(self, test_video_with_audio, temp_output_dir):
        """Test trimming from start (no start_ms specified)."""
        trimmer = VideoTrimmer()
        output_path = temp_output_dir / "trimmed_start.mp4"

        config = TrimConfig(
            start_ms=0,
            end_ms=3000,
        )

        result = trimmer.trim(
            str(test_video_with_audio),
            str(output_path),
            config,
        )

        assert result.path.exists()
        assert 2500 <= result.duration_ms <= 3500

    def test_trim_video_to_end(self, test_video_with_audio, temp_output_dir):
        """Test trimming to end (no end_ms specified)."""
        trimmer = VideoTrimmer()
        output_path = temp_output_dir / "trimmed_end.mp4"

        # Get original duration first
        from src.utils.media_info import get_media_duration
        original_duration = get_media_duration(str(test_video_with_audio))

        config = TrimConfig(
            start_ms=5000,
            end_ms=None,  # To end of video
        )

        result = trimmer.trim(
            str(test_video_with_audio),
            str(output_path),
            config,
        )

        assert result.path.exists()
        # Should be original duration minus 5 seconds
        expected_duration = original_duration - 5000
        assert abs(result.duration_ms - expected_duration) < 1000

    def test_trim_preserves_audio(self, test_video_with_audio, temp_output_dir):
        """Test that trimming preserves audio track."""
        trimmer = VideoTrimmer()
        output_path = temp_output_dir / "trimmed_audio.mp4"

        config = TrimConfig(start_ms=0, end_ms=5000)

        result = trimmer.trim(
            str(test_video_with_audio),
            str(output_path),
            config,
        )

        # Check that output has audio
        from src.utils.media_info import has_audio_track
        assert has_audio_track(str(result.path))

    def test_trim_without_audio(self, test_video_no_audio, temp_output_dir):
        """Test trimming video without audio track."""
        trimmer = VideoTrimmer()
        output_path = temp_output_dir / "trimmed_no_audio.mp4"

        config = TrimConfig(start_ms=0, end_ms=5000)

        result = trimmer.trim(
            str(test_video_no_audio),
            str(output_path),
            config,
        )

        assert result.path.exists()
        assert result.duration_ms > 0

    def test_trim_with_reencode(self, test_video_with_audio, temp_output_dir):
        """Test trimming with re-encoding for precise cuts."""
        trimmer = VideoTrimmer()
        output_path = temp_output_dir / "trimmed_reencode.mp4"

        config = TrimConfig(
            start_ms=1500,  # Non-keyframe time
            end_ms=4500,
            reencode=True,
            crf=23,
        )

        result = trimmer.trim(
            str(test_video_with_audio),
            str(output_path),
            config,
        )

        assert result.path.exists()
        # Re-encoded should have precise duration
        assert 2500 <= result.duration_ms <= 3500

    def test_trim_with_resolution_change(self, test_video_with_audio, temp_output_dir):
        """Test trimming with resolution scaling."""
        trimmer = VideoTrimmer()
        output_path = temp_output_dir / "trimmed_scaled.mp4"

        config = TrimConfig(
            start_ms=0,
            end_ms=3000,
            width=640,
            height=360,
            reencode=True,
        )

        result = trimmer.trim(
            str(test_video_with_audio),
            str(output_path),
            config,
        )

        assert result.path.exists()
        assert result.width == 640
        assert result.height == 360


class TestTrimConfig:
    """Tests for TrimConfig dataclass."""

    def test_trim_config_defaults(self):
        """Test default TrimConfig values."""
        config = TrimConfig(start_ms=0, end_ms=5000)

        assert config.start_ms == 0
        assert config.end_ms == 5000
        assert config.reencode is False
        assert config.crf == 18
        assert config.width is None
        assert config.height is None

    def test_trim_config_with_reencode(self):
        """Test TrimConfig with re-encoding options."""
        config = TrimConfig(
            start_ms=1000,
            end_ms=6000,
            reencode=True,
            crf=28,
            width=1280,
            height=720,
        )

        assert config.reencode is True
        assert config.crf == 28
        assert config.width == 1280
        assert config.height == 720

    def test_trim_config_duration_calculation(self):
        """Test duration calculation from config."""
        config = TrimConfig(start_ms=2000, end_ms=7000)

        assert config.expected_duration_ms == 5000


class TestVideoOutput:
    """Tests for VideoOutput dataclass."""

    def test_video_output_creation(self, temp_output_dir):
        """Test VideoOutput creation."""
        path = temp_output_dir / "test.mp4"
        path.touch()

        output = VideoOutput(
            path=path,
            duration_ms=5000,
            width=1920,
            height=1080,
            file_size=1024000,
        )

        assert output.path == path
        assert output.duration_ms == 5000
        assert output.width == 1920
        assert output.height == 1080
        assert output.file_size == 1024000

    def test_video_output_to_dict(self, temp_output_dir):
        """Test VideoOutput serialization."""
        path = temp_output_dir / "test.mp4"
        path.touch()

        output = VideoOutput(
            path=path,
            duration_ms=5000,
            width=1920,
            height=1080,
            file_size=1024000,
        )

        data = output.to_dict()

        assert data["path"] == str(path)
        assert data["duration_ms"] == 5000
        assert data["width"] == 1920
        assert data["height"] == 1080


class TestVideoConcat:
    """Tests for video concatenation."""

    def test_concat_two_videos(self, multiple_audio_videos, temp_output_dir):
        """Test concatenating two video files."""
        trimmer = VideoTrimmer()
        output_path = temp_output_dir / "concat.mp4"

        # Use first two videos
        videos = [str(v) for v in multiple_audio_videos[:2]]

        result = trimmer.concat(videos, str(output_path))

        assert result.path.exists()
        assert result.duration_ms > 0

    def test_concat_preserves_audio(self, multiple_audio_videos, temp_output_dir):
        """Test that concatenation preserves audio tracks."""
        trimmer = VideoTrimmer()
        output_path = temp_output_dir / "concat_audio.mp4"

        videos = [str(v) for v in multiple_audio_videos[:2]]

        result = trimmer.concat(videos, str(output_path))

        from src.utils.media_info import has_audio_track
        assert has_audio_track(str(result.path))


class TestVideoExport:
    """Tests for video export with various codecs and formats."""

    def test_export_h264(self, test_video_with_audio, temp_output_dir):
        """Test exporting with H.264 codec."""
        trimmer = VideoTrimmer()
        output_path = temp_output_dir / "export_h264.mp4"

        result = trimmer.export(
            str(test_video_with_audio),
            str(output_path),
            video_codec="libx264",
            audio_codec="aac",
            crf=23,
        )

        assert result.path.exists()
        assert result.path.suffix == ".mp4"

    def test_export_with_audio_only(self, test_video_with_audio, temp_output_dir):
        """Test extracting audio only from video."""
        trimmer = VideoTrimmer()
        output_path = temp_output_dir / "audio_only.mp3"

        result = trimmer.export_audio(
            str(test_video_with_audio),
            str(output_path),
            audio_codec="libmp3lame",
            bitrate="192k",
        )

        assert result.path.exists()
        assert result.path.suffix == ".mp3"
        assert result.duration_ms > 0
