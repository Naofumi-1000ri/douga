"""
TDD tests for media info extraction functionality.

Test cases:
1. Get video duration
2. Get video dimensions
3. Get audio info
4. Handle files without audio
"""

import pytest
from pathlib import Path


class TestMediaInfo:
    """Test media info extraction using ffprobe."""

    def test_get_video_duration(self, operation_video_with_audio: Path):
        """Test getting video duration in milliseconds."""
        from src.utils.media_info import get_media_duration

        duration_ms = get_media_duration(str(operation_video_with_audio))

        # sec2_rec1_検索画面差し替え.mp4 is ~50.7 seconds
        assert duration_ms is not None
        assert 50000 <= duration_ms <= 53000, f"Expected ~50700ms, got {duration_ms}"

    def test_get_video_dimensions(self, operation_video_with_audio: Path):
        """Test getting video width and height."""
        from src.utils.media_info import get_video_dimensions

        width, height = get_video_dimensions(str(operation_video_with_audio))

        assert width == 1920, f"Expected width 1920, got {width}"
        assert height == 1080, f"Expected height 1080, got {height}"

    def test_get_storyboard_dimensions(self, storyboard_video_no_audio: Path):
        """Test getting storyboard video dimensions."""
        from src.utils.media_info import get_video_dimensions

        width, height = get_video_dimensions(str(storyboard_video_no_audio))

        assert width == 1920, f"Expected width 1920, got {width}"
        assert height == 1080, f"Expected height 1080, got {height}"

    def test_has_audio_track_true(self, operation_video_with_audio: Path):
        """Test detecting audio track in video with audio."""
        from src.utils.media_info import has_audio_track

        result = has_audio_track(str(operation_video_with_audio))
        assert result is True, "Should detect audio track"

    def test_has_audio_track_false(self, storyboard_video_no_audio: Path):
        """Test detecting no audio track in video without audio."""
        from src.utils.media_info import has_audio_track

        result = has_audio_track(str(storyboard_video_no_audio))
        assert result is False, "Should not detect audio track"

    def test_get_audio_info(self, operation_video_with_audio: Path):
        """Test getting audio stream information."""
        from src.utils.media_info import get_audio_info

        info = get_audio_info(str(operation_video_with_audio))

        assert info is not None, "Should return audio info"
        assert info["codec"] == "aac", f"Expected codec aac, got {info['codec']}"
        assert info["sample_rate"] == 48000, f"Expected 48000Hz, got {info['sample_rate']}"
        assert info["channels"] == 1, f"Expected 1 channel (mono), got {info['channels']}"

    def test_get_full_media_info(self, operation_video_with_audio: Path):
        """Test getting complete media info."""
        from src.utils.media_info import get_media_info

        info = get_media_info(str(operation_video_with_audio))

        assert "duration_ms" in info
        assert "width" in info
        assert "height" in info
        assert "has_audio" in info
        assert "video_codec" in info
        assert "fps" in info

        assert info["width"] == 1920
        assert info["height"] == 1080
        assert info["has_audio"] is True
        assert info["fps"] == 30

    def test_invalid_file_raises_error(self):
        """Test that invalid file raises error."""
        from src.utils.media_info import get_media_duration

        with pytest.raises(Exception):
            get_media_duration("/nonexistent/file.mp4")

    def test_get_duration_storyboard(self, storyboard_video_no_audio: Path):
        """Test duration of storyboard video."""
        from src.utils.media_info import get_media_duration

        duration_ms = get_media_duration(str(storyboard_video_no_audio))

        # 動画2_絵コンテ_セクション2.mp4 is 100 seconds
        assert 99000 <= duration_ms <= 101000, f"Expected ~100000ms, got {duration_ms}"
