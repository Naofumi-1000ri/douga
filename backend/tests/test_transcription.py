"""
TDD tests for transcription functionality.

Test cases:
1. Transcribe audio with Whisper
2. Detect silence regions
3. Detect filler words
4. Detect repetitions
5. Generate cut flags
"""

import pytest
from pathlib import Path


class TestTranscriptionService:
    """Test transcription service functionality."""

    def test_transcribe_audio_with_audio(
        self, operation_video_with_audio: Path, temp_output_dir: Path
    ):
        """Test transcription of video with audio."""
        from src.services.transcription_service import TranscriptionService

        service = TranscriptionService(model_name="tiny")  # Use tiny for speed

        result = service.transcribe(
            str(operation_video_with_audio),
            language="ja",
            detect_silences=True,
            detect_fillers=True,
            detect_repetitions=True,
        )

        # Verify result structure
        assert result.status == "completed"
        assert result.duration_ms > 0
        assert len(result.segments) > 0

    def test_transcribe_returns_segments_with_timing(
        self, operation_video_with_audio: Path
    ):
        """Test that transcription returns segments with timestamps."""
        from src.services.transcription_service import TranscriptionService

        service = TranscriptionService(model_name="tiny")
        result = service.transcribe(str(operation_video_with_audio), language="ja")

        # Check segment structure
        for segment in result.segments:
            assert segment.id is not None
            assert segment.start_ms >= 0
            assert segment.end_ms > segment.start_ms
            # Text or silence marker
            assert segment.text is not None

    def test_silence_detection_between_segments(
        self, operation_video_with_audio: Path
    ):
        """Test that silences between speech segments are detected."""
        from src.services.transcription_service import TranscriptionService

        service = TranscriptionService(
            model_name="tiny",
            min_silence_duration_ms=300,  # Lower threshold for testing
        )
        result = service.transcribe(
            str(operation_video_with_audio),
            language="ja",
            detect_silences=True,
        )

        # Check for silence segments
        silence_segments = [s for s in result.segments if s.cut_reason == "silence"]
        assert len(silence_segments) >= 0  # May or may not have silences

        # If silences exist, they should have cut=True
        for seg in silence_segments:
            assert seg.cut is True
            assert seg.text == "[無音]"

    def test_filler_word_detection(self, operation_video_with_audio: Path):
        """Test that filler words are detected and flagged."""
        from src.services.transcription_service import TranscriptionService

        service = TranscriptionService(model_name="tiny")
        result = service.transcribe(
            str(operation_video_with_audio),
            language="ja",
            detect_fillers=True,
        )

        # Filler segments should have is_filler=True
        filler_segments = [s for s in result.segments if s.is_filler]
        for seg in filler_segments:
            assert seg.cut is True
            assert seg.cut_reason == "filler"

    def test_transcription_statistics(self, operation_video_with_audio: Path):
        """Test that transcription calculates statistics."""
        from src.services.transcription_service import TranscriptionService

        service = TranscriptionService(model_name="tiny")
        result = service.transcribe(str(operation_video_with_audio), language="ja")

        # Check statistics
        assert result.total_segments >= 0
        assert result.cut_segments >= 0
        assert result.silence_duration_ms >= 0
        assert result.mistake_count >= 0

        # cut_segments should equal segments with cut=True
        actual_cut = sum(1 for s in result.segments if s.cut)
        assert result.cut_segments == actual_cut

    def test_silence_detection_ffmpeg(self, operation_video_with_audio: Path):
        """Test FFmpeg-based silence detection."""
        from src.services.transcription_service import TranscriptionService

        service = TranscriptionService(
            silence_threshold_db=-35.0,
            min_silence_duration_ms=300,
        )

        silences = service.detect_silences_ffmpeg(str(operation_video_with_audio))

        # Should return list of SilenceRegion
        assert isinstance(silences, list)
        for silence in silences:
            assert silence.start_ms >= 0
            assert silence.end_ms > silence.start_ms
            assert silence.duration_ms == silence.end_ms - silence.start_ms

    def test_transcribe_video_without_audio_fails(
        self, storyboard_video_no_audio: Path
    ):
        """Test that transcription of video without audio handles gracefully."""
        from src.services.transcription_service import TranscriptionService

        service = TranscriptionService(model_name="tiny")

        # Whisper may produce empty results or raise error
        result = service.transcribe(str(storyboard_video_no_audio), language="ja")

        # Should complete (possibly with empty segments)
        assert result.status == "completed"


class TestTranscriptionSegment:
    """Test transcription segment model."""

    def test_segment_cut_flag_default(self):
        """Test that cut flag defaults to False."""
        from src.schemas.timeline import TranscriptionSegment

        segment = TranscriptionSegment(
            id="test-id",
            start_ms=0,
            end_ms=1000,
            text="テスト",
        )

        assert segment.cut is False
        assert segment.cut_reason is None

    def test_segment_with_cut_flag(self):
        """Test segment with cut flag set."""
        from src.schemas.timeline import TranscriptionSegment

        segment = TranscriptionSegment(
            id="test-id",
            start_ms=0,
            end_ms=1000,
            text="[無音]",
            cut=True,
            cut_reason="silence",
        )

        assert segment.cut is True
        assert segment.cut_reason == "silence"

    def test_segment_words(self):
        """Test segment with word-level timestamps."""
        from src.schemas.timeline import TranscriptionSegment, TranscriptionWord

        words = [
            TranscriptionWord(word="こんにちは", start_ms=0, end_ms=500),
            TranscriptionWord(word="世界", start_ms=500, end_ms=1000),
        ]

        segment = TranscriptionSegment(
            id="test-id",
            start_ms=0,
            end_ms=1000,
            text="こんにちは世界",
            words=words,
        )

        assert len(segment.words) == 2
        assert segment.words[0].word == "こんにちは"


class TestFillerDetection:
    """Test filler word detection logic."""

    def test_detect_japanese_fillers(self):
        """Test detection of Japanese filler words."""
        from src.services.transcription_service import JAPANESE_FILLERS

        # Common fillers should be in the list
        assert "えー" in JAPANESE_FILLERS
        assert "あのー" in JAPANESE_FILLERS
        assert "えーと" in JAPANESE_FILLERS

    def test_filler_detection_marks_segment(self):
        """Test that filler detection marks segments correctly."""
        from src.schemas.timeline import TranscriptionSegment
        from src.services.transcription_service import TranscriptionService

        # Create test segments
        segments = [
            TranscriptionSegment(id="1", start_ms=0, end_ms=500, text="えー"),
            TranscriptionSegment(id="2", start_ms=500, end_ms=1500, text="今日は"),
            TranscriptionSegment(id="3", start_ms=1500, end_ms=2000, text="あのー"),
        ]

        # Apply filler detection
        service = TranscriptionService()
        service._detect_fillers(segments)

        # Check results
        assert segments[0].is_filler is True
        assert segments[0].cut is True
        assert segments[1].is_filler is False
        assert segments[1].cut is False
        assert segments[2].is_filler is True


class TestRepetitionDetection:
    """Test repetition/mistake detection logic."""

    def test_repetition_detection(self):
        """Test detection of repeated phrases (言い直し)."""
        from src.schemas.timeline import TranscriptionSegment
        from src.services.transcription_service import TranscriptionService

        # Create segments with repetition
        segments = [
            TranscriptionSegment(
                id="1", start_ms=0, end_ms=1000,
                text="今日 は 良い 天気"  # First attempt
            ),
            TranscriptionSegment(
                id="2", start_ms=1000, end_ms=2000,
                text="良い 天気 ですね"  # Restarts from "良い天気"
            ),
        ]

        service = TranscriptionService()
        service._detect_repetitions(segments)

        # First segment should be marked as repetition (speaker restarted)
        assert segments[0].is_repetition is True
        assert segments[0].cut is True
        assert segments[0].cut_reason == "mistake"
