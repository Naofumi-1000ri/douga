"""
Transcription service using OpenAI Whisper API.

Features:
- Audio/video transcription with word-level timestamps
- Silence detection with configurable thresholds
- Filler word detection (えー, あのー, etc.)
- Repetition/mistake detection
"""

import json
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

from src.config import get_settings

from src.schemas.timeline import (
    Transcription,
    TranscriptionSegment,
    TranscriptionWord,
)

# Japanese filler words to detect
JAPANESE_FILLERS = [
    "えー", "えーと", "えっと", "あー", "あのー", "あの", "うーん", "うん",
    "ま", "まあ", "その", "なんか", "こう", "ちょっと",
]


@dataclass
class SilenceRegion:
    """A detected silence region."""
    start_ms: int
    end_ms: int
    duration_ms: int


class TranscriptionService:
    """
    Service for transcribing audio/video files using OpenAI Whisper API.
    """

    def __init__(
        self,
        model_name: str = "whisper-1",
        silence_threshold_db: float = -40.0,
        min_silence_duration_ms: int = 500,
    ):
        """
        Initialize transcription service.

        Args:
            model_name: OpenAI Whisper model (whisper-1)
            silence_threshold_db: Audio level threshold for silence detection
            min_silence_duration_ms: Minimum silence duration to flag
        """
        self.model_name = model_name
        self.silence_threshold_db = silence_threshold_db
        self.min_silence_duration_ms = min_silence_duration_ms
        self.settings = get_settings()

    def _extract_audio(self, input_path: str) -> str:
        """Extract audio from video file to temporary mp3 file."""
        temp_audio = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        temp_audio.close()

        cmd = [
            self.settings.ffmpeg_path,
            "-i", input_path,
            "-vn",  # No video
            "-acodec", "libmp3lame",
            "-ar", "16000",  # 16kHz sample rate
            "-ac", "1",  # Mono
            "-y",  # Overwrite
            temp_audio.name,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to extract audio: {result.stderr}")

        return temp_audio.name

    def transcribe(
        self,
        audio_path: str,
        language: str = "ja",
        detect_silences: bool = True,
        detect_fillers: bool = True,
        detect_repetitions: bool = True,
    ) -> Transcription:
        """
        Transcribe an audio file and detect issues.

        Args:
            audio_path: Path to audio/video file
            language: Language code (ja, en, etc.)
            detect_silences: Whether to detect and flag silent regions
            detect_fillers: Whether to detect filler words
            detect_repetitions: Whether to detect repetitions/mistakes

        Returns:
            Transcription object with segments and cut flags
        """
        # Check if file has audio track first
        if not self._has_audio_track(audio_path):
            return Transcription(
                asset_id=uuid.uuid4(),
                language=language,
                status="completed",
                error_message="No audio track found in file",
            )

        # Extract audio if needed (video files)
        audio_file = audio_path
        temp_file = None
        if not audio_path.lower().endswith(('.mp3', '.wav', '.m4a', '.flac', '.ogg')):
            try:
                temp_file = self._extract_audio(audio_path)
                audio_file = temp_file
            except RuntimeError as e:
                return Transcription(
                    asset_id=uuid.uuid4(),
                    language=language,
                    status="completed",
                    error_message=str(e),
                )

        # Call OpenAI Whisper API
        try:
            result = self._call_openai_api(audio_file, language)
        except Exception as e:
            if temp_file:
                Path(temp_file).unlink(missing_ok=True)
            return Transcription(
                asset_id=uuid.uuid4(),
                language=language,
                status="completed",
                error_message=str(e),
            )
        finally:
            if temp_file:
                Path(temp_file).unlink(missing_ok=True)

        # Get audio duration
        duration_ms = self._get_duration_ms(audio_path)

        # Convert API response to our format
        segments: list[TranscriptionSegment] = []
        for seg in result.get("segments", []):
            segment = self._convert_segment(seg)
            segments.append(segment)

        # Detect silences between segments
        silence_regions: list[SilenceRegion] = []
        if detect_silences:
            silence_regions = self._detect_silences(segments, duration_ms)

        # Insert silence segments with cut flags
        all_segments = self._merge_segments_with_silences(segments, silence_regions)

        # Detect fillers
        if detect_fillers:
            self._detect_fillers(all_segments)

        # Detect repetitions
        if detect_repetitions:
            self._detect_repetitions(all_segments)

        # Calculate statistics
        total_segments = len(all_segments)
        cut_segments = sum(1 for s in all_segments if s.cut)
        silence_duration = sum(r.duration_ms for r in silence_regions)
        mistake_count = sum(1 for s in all_segments if s.is_repetition or s.is_filler)

        return Transcription(
            asset_id=uuid.uuid4(),  # Will be set by caller
            language=language,
            segments=all_segments,
            duration_ms=duration_ms,
            status="completed",
            total_segments=total_segments,
            cut_segments=cut_segments,
            silence_duration_ms=silence_duration,
            mistake_count=mistake_count,
        )

    def _call_openai_api(self, audio_path: str, language: str) -> dict:
        """Call OpenAI Whisper API for transcription."""
        api_key = self.settings.openai_api_key
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")

        with open(audio_path, "rb") as audio_file:
            response = httpx.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": audio_file},
                data={
                    "model": self.model_name,
                    "language": language,
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "segment",
                },
                timeout=300.0,
            )

        if response.status_code != 200:
            raise RuntimeError(f"OpenAI API error: {response.status_code} - {response.text}")

        return response.json()

    def _convert_segment(self, api_seg: dict) -> TranscriptionSegment:
        """Convert an OpenAI API segment to our format."""
        words = []
        for w in api_seg.get("words", []):
            words.append(TranscriptionWord(
                word=w["word"],
                start_ms=int(w["start"] * 1000),
                end_ms=int(w["end"] * 1000),
                confidence=1.0,  # OpenAI API doesn't provide word-level confidence
            ))

        return TranscriptionSegment(
            id=str(uuid.uuid4()),
            start_ms=int(api_seg["start"] * 1000),
            end_ms=int(api_seg["end"] * 1000),
            text=api_seg["text"].strip(),
            words=words if words else None,
            confidence=api_seg.get("avg_logprob", 0.0),
        )

    def _detect_silences(
        self,
        segments: list[TranscriptionSegment],
        duration_ms: int,
    ) -> list[SilenceRegion]:
        """Detect silent regions between transcription segments."""
        silences = []

        # Check silence at start
        if segments and segments[0].start_ms > self.min_silence_duration_ms:
            silences.append(SilenceRegion(
                start_ms=0,
                end_ms=segments[0].start_ms,
                duration_ms=segments[0].start_ms,
            ))

        # Check silences between segments
        for i in range(len(segments) - 1):
            gap_start = segments[i].end_ms
            gap_end = segments[i + 1].start_ms
            gap_duration = gap_end - gap_start

            if gap_duration >= self.min_silence_duration_ms:
                silences.append(SilenceRegion(
                    start_ms=gap_start,
                    end_ms=gap_end,
                    duration_ms=gap_duration,
                ))

        # Check silence at end
        if segments and (duration_ms - segments[-1].end_ms) > self.min_silence_duration_ms:
            silences.append(SilenceRegion(
                start_ms=segments[-1].end_ms,
                end_ms=duration_ms,
                duration_ms=duration_ms - segments[-1].end_ms,
            ))

        return silences

    def _merge_segments_with_silences(
        self,
        segments: list[TranscriptionSegment],
        silences: list[SilenceRegion],
    ) -> list[TranscriptionSegment]:
        """Merge speech segments and silence regions, sorted by time."""
        all_segments = list(segments)

        # Add silence segments with cut=True
        for silence in silences:
            silence_segment = TranscriptionSegment(
                id=str(uuid.uuid4()),
                start_ms=silence.start_ms,
                end_ms=silence.end_ms,
                text="[無音]",
                cut=True,
                cut_reason="silence",
            )
            all_segments.append(silence_segment)

        # Sort by start time
        all_segments.sort(key=lambda s: s.start_ms)
        return all_segments

    def _detect_fillers(self, segments: list[TranscriptionSegment]) -> None:
        """Detect and flag filler words in segments."""
        for segment in segments:
            if segment.cut:
                continue

            text_lower = segment.text.lower().strip()

            # Check if entire segment is a filler
            for filler in JAPANESE_FILLERS:
                if text_lower == filler or text_lower.startswith(filler + " "):
                    segment.is_filler = True
                    segment.cut = True
                    segment.cut_reason = "filler"
                    break

    def _detect_repetitions(self, segments: list[TranscriptionSegment]) -> None:
        """
        Detect repetitions/mistakes where speaker restarts a sentence.

        Pattern: Same or similar text appears twice in adjacent segments.
        """
        for i in range(len(segments) - 1):
            seg1 = segments[i]
            seg2 = segments[i + 1]

            if seg1.cut or seg2.cut:
                continue

            # Check if seg1 text is repeated at start of seg2
            text1 = seg1.text.strip()
            text2 = seg2.text.strip()

            # Simple repetition detection: seg1 ends with same words that seg2 starts with
            words1 = text1.split()
            words2 = text2.split()

            if len(words1) >= 2 and len(words2) >= 2:
                # Check if last 2-3 words of seg1 match first 2-3 words of seg2
                for overlap in [3, 2]:
                    if len(words1) >= overlap and len(words2) >= overlap:
                        if words1[-overlap:] == words2[:overlap]:
                            seg1.is_repetition = True
                            seg1.cut = True
                            seg1.cut_reason = "mistake"
                            seg1.corrected_text = text2
                            break

    def _get_duration_ms(self, audio_path: str) -> int:
        """Get audio duration in milliseconds using ffprobe."""
        cmd = [
            self.settings.ffprobe_path,
            "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return 0
        return int(float(result.stdout.strip()) * 1000)

    def _has_audio_track(self, file_path: str) -> bool:
        """Check if file has an audio track."""
        cmd = [
            self.settings.ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "a",
            file_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return False
        try:
            data = json.loads(result.stdout)
            return len(data.get("streams", [])) > 0
        except json.JSONDecodeError:
            return False

    def detect_silences_ffmpeg(self, audio_path: str) -> list[SilenceRegion]:
        """
        Detect silences using FFmpeg silencedetect filter.
        More accurate than gap-based detection.
        """
        cmd = [
            self.settings.ffmpeg_path,
            "-i", audio_path,
            "-af", f"silencedetect=noise={self.silence_threshold_db}dB:d={self.min_silence_duration_ms/1000}",
            "-f", "null", "-",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        output = result.stderr

        silences = []
        current_start = None

        for line in output.split("\n"):
            if "silence_start:" in line:
                start_str = line.split("silence_start:")[1].strip().split()[0]
                current_start = int(float(start_str) * 1000)
            elif "silence_end:" in line and current_start is not None:
                parts = line.split("silence_end:")[1].strip().split()
                end_ms = int(float(parts[0]) * 1000)
                silences.append(SilenceRegion(
                    start_ms=current_start,
                    end_ms=end_ms,
                    duration_ms=end_ms - current_start,
                ))
                current_start = None

        return silences


def transcribe_file(
    file_path: str,
    model_name: str = "whisper-1",
    language: str = "ja",
) -> Transcription:
    """
    Convenience function to transcribe a single file.

    Args:
        file_path: Path to audio/video file
        model_name: OpenAI Whisper model
        language: Language code

    Returns:
        Transcription result
    """
    service = TranscriptionService(model_name=model_name)
    return service.transcribe(file_path, language=language)
