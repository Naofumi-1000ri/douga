"""Preview and playback service for media assets.

Provides:
- Waveform data generation for audio visualization
- Thumbnail generation for video files
- Signed URL generation for GCS assets
- Preview clip generation
"""

import json
import subprocess
import struct
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Optional

from google.cloud import storage


@dataclass
class WaveformData:
    """Waveform data for audio visualization."""

    peaks: list[float]
    duration_ms: int
    sample_rate: int = 44100

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "peaks": self.peaks,
            "duration_ms": self.duration_ms,
            "sample_rate": self.sample_rate,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WaveformData":
        """Deserialize from dictionary."""
        return cls(
            peaks=data["peaks"],
            duration_ms=data["duration_ms"],
            sample_rate=data.get("sample_rate", 44100),
        )


class PreviewService:
    """Service for generating preview data from media files."""

    def __init__(self):
        self._gcs_client: Optional[storage.Client] = None

    def _get_gcs_client(self) -> storage.Client:
        """Get or create GCS client."""
        if self._gcs_client is None:
            self._gcs_client = storage.Client()
        return self._gcs_client

    def _has_audio_track(self, file_path: str) -> bool:
        """Check if file has an audio track."""
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index",
                "-of", "json",
                file_path,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False

        try:
            data = json.loads(result.stdout)
            return len(data.get("streams", [])) > 0
        except json.JSONDecodeError:
            return False

    def _get_duration_ms(self, file_path: str) -> int:
        """Get media duration in milliseconds."""
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                file_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        duration_seconds = float(data["format"]["duration"])
        return int(duration_seconds * 1000)

    def generate_waveform(self, file_path: str, samples: int = 100) -> WaveformData:
        """Generate waveform data for audio visualization.

        Args:
            file_path: Path to audio or video file
            samples: Number of peak samples to generate

        Returns:
            WaveformData with normalized peak values

        Raises:
            ValueError: If file has no audio track
        """
        if not self._has_audio_track(file_path):
            raise ValueError(f"No audio track in file: {file_path}")

        duration_ms = self._get_duration_ms(file_path)
        peaks = self._extract_audio_peaks(file_path, samples)

        return WaveformData(
            peaks=peaks,
            duration_ms=duration_ms,
        )

    def _extract_audio_peaks(self, file_path: str, num_samples: int) -> list[float]:
        """Extract peak values from audio.

        Uses FFmpeg to extract raw PCM data and computes peaks.
        """
        # Get duration to calculate samples per peak
        duration_ms = self._get_duration_ms(file_path)
        duration_seconds = duration_ms / 1000

        # Extract raw PCM audio data (mono, 16-bit signed, 8000 Hz for efficiency)
        sample_rate = 8000
        result = subprocess.run(
            [
                "ffmpeg",
                "-i", file_path,
                "-ac", "1",  # mono
                "-ar", str(sample_rate),
                "-f", "s16le",  # 16-bit signed little-endian
                "-acodec", "pcm_s16le",
                "-v", "error",
                "-",
            ],
            capture_output=True,
        )

        if result.returncode != 0:
            # Return empty peaks if extraction fails
            return [0.0] * num_samples

        raw_audio = result.stdout
        if len(raw_audio) < 2:
            return [0.0] * num_samples

        # Parse 16-bit signed samples
        num_raw_samples = len(raw_audio) // 2
        samples_data = struct.unpack(f"<{num_raw_samples}h", raw_audio)

        # Calculate peaks for each segment
        samples_per_peak = max(1, num_raw_samples // num_samples)
        peaks = []

        for i in range(num_samples):
            start_idx = i * samples_per_peak
            end_idx = min(start_idx + samples_per_peak, num_raw_samples)

            if start_idx >= num_raw_samples:
                peaks.append(0.0)
                continue

            segment = samples_data[start_idx:end_idx]
            if segment:
                # Get max absolute value and normalize to [-1, 1]
                max_val = max(abs(min(segment)), abs(max(segment)))
                normalized = max_val / 32768.0  # 16-bit max
                peaks.append(min(1.0, normalized))
            else:
                peaks.append(0.0)

        return peaks

    def generate_thumbnail(
        self,
        video_path: str,
        output_path: str,
        time_ms: int = 0,
        width: int = 320,
        height: int = 180,
    ) -> Path:
        """Generate thumbnail from video at specific time.

        Args:
            video_path: Path to video file
            output_path: Path for output thumbnail
            time_ms: Time position in milliseconds
            width: Thumbnail width
            height: Thumbnail height

        Returns:
            Path to generated thumbnail
        """
        time_seconds = time_ms / 1000

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss", str(time_seconds),
                "-i", video_path,
                "-vframes", "1",
                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                "-q:v", "2",
                output_path,
            ],
            capture_output=True,
            check=True,
        )

        return Path(output_path)

    def generate_preview_clip(
        self,
        video_path: str,
        output_path: str,
        max_width: int = 640,
        max_height: int = 360,
        crf: int = 28,
    ) -> Path:
        """Generate low-resolution preview clip.

        Args:
            video_path: Path to video file
            output_path: Path for output preview
            max_width: Maximum width
            max_height: Maximum height
            crf: CRF value (higher = smaller file)

        Returns:
            Path to generated preview
        """
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", video_path,
                "-vf", f"scale='min({max_width},iw)':min'({max_height},ih)':force_original_aspect_ratio=decrease",
                "-c:v", "libx264",
                "-crf", str(crf),
                "-preset", "fast",
                "-c:a", "aac",
                "-b:a", "64k",
                output_path,
            ],
            capture_output=True,
            check=True,
        )

        return Path(output_path)

    def generate_signed_url(
        self,
        bucket_name: str,
        blob_path: str,
        expiration_minutes: int = 15,
    ) -> str:
        """Generate signed URL for GCS asset.

        Args:
            bucket_name: GCS bucket name
            blob_path: Path to blob in bucket
            expiration_minutes: URL expiration time in minutes

        Returns:
            Signed URL string
        """
        client = self._get_gcs_client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=expiration_minutes),
            method="GET",
        )

        return url
