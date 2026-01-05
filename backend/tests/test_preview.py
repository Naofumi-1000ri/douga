"""Tests for preview and playback functionality."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.preview_service import PreviewService, WaveformData


class TestPreviewService:
    """Tests for PreviewService class."""

    def test_generate_waveform_data_returns_peaks(self, test_audio_with_audio):
        """Test that waveform generation returns peak data."""
        service = PreviewService()
        waveform = service.generate_waveform(str(test_audio_with_audio), samples=100)

        assert isinstance(waveform, WaveformData)
        assert len(waveform.peaks) == 100
        assert waveform.duration_ms > 0
        assert all(-1.0 <= p <= 1.0 for p in waveform.peaks)

    def test_generate_waveform_with_custom_samples(self, test_audio_with_audio):
        """Test waveform generation with custom sample count."""
        service = PreviewService()
        waveform = service.generate_waveform(str(test_audio_with_audio), samples=50)

        assert len(waveform.peaks) == 50

    def test_generate_waveform_for_video_with_audio(self, test_video_with_audio):
        """Test waveform generation from video file with audio track."""
        service = PreviewService()
        waveform = service.generate_waveform(str(test_video_with_audio), samples=100)

        assert isinstance(waveform, WaveformData)
        assert len(waveform.peaks) == 100
        assert waveform.duration_ms > 0

    def test_generate_waveform_for_video_without_audio_raises(self, test_video_no_audio):
        """Test waveform generation fails for video without audio."""
        service = PreviewService()

        with pytest.raises(ValueError, match="No audio track"):
            service.generate_waveform(str(test_video_no_audio), samples=100)

    def test_generate_thumbnail_at_time(self, test_video_with_audio):
        """Test thumbnail generation at specific time."""
        service = PreviewService()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "thumbnail.jpg"
            result = service.generate_thumbnail(
                str(test_video_with_audio),
                str(output_path),
                time_ms=1000
            )

            assert result.exists()
            assert result.stat().st_size > 0

    def test_generate_thumbnail_at_start(self, test_video_with_audio):
        """Test thumbnail generation at video start."""
        service = PreviewService()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "thumbnail.jpg"
            result = service.generate_thumbnail(
                str(test_video_with_audio),
                str(output_path),
                time_ms=0
            )

            assert result.exists()

    def test_generate_preview_clip(self, test_video_with_audio):
        """Test generating a low-res preview clip."""
        service = PreviewService()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "preview.mp4"
            result = service.generate_preview_clip(
                str(test_video_with_audio),
                str(output_path),
                max_width=640,
                max_height=360
            )

            assert result.exists()
            assert result.stat().st_size > 0


class TestWaveformData:
    """Tests for WaveformData dataclass."""

    def test_waveform_data_to_dict(self):
        """Test WaveformData serialization."""
        waveform = WaveformData(
            peaks=[0.1, 0.5, -0.3, 0.8],
            duration_ms=5000,
            sample_rate=44100
        )

        data = waveform.to_dict()

        assert data["peaks"] == [0.1, 0.5, -0.3, 0.8]
        assert data["duration_ms"] == 5000
        assert data["sample_rate"] == 44100

    def test_waveform_data_from_dict(self):
        """Test WaveformData deserialization."""
        data = {
            "peaks": [0.2, -0.4, 0.6],
            "duration_ms": 3000,
            "sample_rate": 48000
        }

        waveform = WaveformData.from_dict(data)

        assert waveform.peaks == [0.2, -0.4, 0.6]
        assert waveform.duration_ms == 3000
        assert waveform.sample_rate == 48000


class TestPreviewAPIEndpoints:
    """Tests for preview API endpoints - placeholder for integration tests."""

    def test_api_endpoints_placeholder(self):
        """Placeholder - API integration tests would go here."""
        # These would use TestClient with FastAPI
        # For now, the service-level tests cover the core functionality
        assert True


class TestSignedURLGeneration:
    """Tests for signed URL generation."""

    def test_generate_signed_url_for_gcs_asset(self):
        """Test signed URL generation for GCS stored asset."""
        service = PreviewService()

        with patch.object(service, '_get_gcs_client') as mock_gcs:
            mock_blob = MagicMock()
            mock_blob.generate_signed_url.return_value = "https://storage.googleapis.com/bucket/file?signature=xxx"
            mock_bucket = MagicMock()
            mock_bucket.blob.return_value = mock_blob
            mock_gcs.return_value.bucket.return_value = mock_bucket

            url = service.generate_signed_url(
                bucket_name="test-bucket",
                blob_path="assets/test.mp4",
                expiration_minutes=15
            )

            assert url.startswith("https://storage.googleapis.com")
            mock_blob.generate_signed_url.assert_called_once()

    def test_signed_url_expiration(self):
        """Test that signed URL has correct expiration."""
        service = PreviewService()

        with patch.object(service, '_get_gcs_client') as mock_gcs:
            mock_blob = MagicMock()
            mock_bucket = MagicMock()
            mock_bucket.blob.return_value = mock_blob
            mock_gcs.return_value.bucket.return_value = mock_bucket

            service.generate_signed_url(
                bucket_name="test-bucket",
                blob_path="assets/test.mp4",
                expiration_minutes=30
            )

            call_kwargs = mock_blob.generate_signed_url.call_args[1]
            assert "expiration" in call_kwargs


class TestAudioPeakExtraction:
    """Tests for audio peak extraction algorithm."""

    def test_extract_peaks_from_raw_audio(self, test_audio_with_audio):
        """Test extracting peaks from raw audio data."""
        service = PreviewService()
        peaks = service._extract_audio_peaks(str(test_audio_with_audio), num_samples=200)

        assert len(peaks) == 200
        assert all(isinstance(p, float) for p in peaks)
        # Peaks should be normalized
        assert all(-1.0 <= p <= 1.0 for p in peaks)

    def test_peaks_reflect_audio_content(self, sample_video):
        """Test that peaks reflect actual audio content."""
        service = PreviewService()
        # Use sample video which has real audio content (~317kbps)
        peaks = service._extract_audio_peaks(str(sample_video), num_samples=100)

        # Audio should have some non-zero values
        non_zero_peaks = [p for p in peaks if p > 0.001]
        assert len(non_zero_peaks) > 0, "Audio should have some audible content"

    def test_silent_audio_has_low_peaks(self):
        """Test that silent audio has very low peak values."""
        service = PreviewService()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            # Generate silent audio using ffmpeg
            import subprocess
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=mono",
                "-t", "1", "-q:a", "9",
                f.name
            ], capture_output=True, check=True)

            peaks = service._extract_audio_peaks(f.name, num_samples=50)

            # Silent audio should have very low peaks
            max_peak = max(abs(p) for p in peaks)
            assert max_peak < 0.01, "Silent audio should have near-zero peaks"

            Path(f.name).unlink()
