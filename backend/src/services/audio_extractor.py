"""Audio extraction service for converting video to audio."""
import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

from src.config import get_settings


def extract_audio_from_video(
    input_path: str,
    output_path: str,
    bitrate: str = "192k",
) -> str:
    """
    Extract audio from video file and convert to MP3 format.

    Args:
        input_path: Path to input video file
        output_path: Path to output audio file
        bitrate: Audio bitrate for output

    Returns:
        Path to extracted audio file

    Raises:
        FileNotFoundError: If input file doesn't exist
        RuntimeError: If video has no audio track or FFmpeg fails
    """
    settings = get_settings()

    # Verify input file exists
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Check if video has audio track
    probe_cmd = [
        settings.ffprobe_path,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "a",
        input_path,
    ]
    probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
    try:
        probe_data = json.loads(probe_result.stdout)
        if not probe_data.get("streams"):
            raise RuntimeError(f"No audio track in video: {input_path}")
    except json.JSONDecodeError:
        raise RuntimeError(f"Failed to probe video: {input_path}")

    # FFmpeg command to extract audio
    cmd = [
        settings.ffmpeg_path,
        "-i", str(input_path),
        "-vn",  # No video
        "-acodec", "libmp3lame",
        "-ab", bitrate,
        "-ar", "44100",  # Sample rate
        "-ac", "2",  # Stereo
        "-y",  # Overwrite
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg error: {result.stderr}")

    return str(output_path)


async def extract_audio_from_video_async(
    input_path: str,
    output_path: str,
    bitrate: str = "192k",
) -> str:
    """
    Async version of extract_audio_from_video.
    """
    settings = get_settings()

    # Verify input file exists
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Check if video has audio track
    probe_cmd = [
        settings.ffprobe_path,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "a",
        input_path,
    ]
    probe_process = await asyncio.create_subprocess_exec(
        *probe_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    probe_stdout, _ = await probe_process.communicate()

    try:
        probe_data = json.loads(probe_stdout.decode())
        if not probe_data.get("streams"):
            raise RuntimeError(f"No audio track in video: {input_path}")
    except json.JSONDecodeError:
        raise RuntimeError(f"Failed to probe video: {input_path}")

    # FFmpeg command to extract audio
    cmd = [
        settings.ffmpeg_path,
        "-i", str(input_path),
        "-vn",  # No video
        "-acodec", "libmp3lame",
        "-ab", bitrate,
        "-ar", "44100",  # Sample rate
        "-ac", "2",  # Stereo
        "-y",  # Overwrite
        str(output_path),
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg error: {stderr.decode()}")

    return str(output_path)


async def extract_audio_from_gcs(
    storage_service,
    source_key: str,
    project_id: str,
    output_filename: str,
) -> tuple[str, int]:
    """
    Download video from GCS, extract audio, upload back to GCS.

    Returns:
        Tuple of (new_storage_key, file_size)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Download video
        video_path = os.path.join(tmpdir, "input_video")
        storage_service.download_file(source_key, video_path)

        # Extract audio
        audio_path = await extract_audio_from_video(video_path)

        # Get file size
        file_size = os.path.getsize(audio_path)

        # Generate new storage key
        import uuid
        audio_key = f"projects/{project_id}/assets/{uuid.uuid4()}.mp3"

        # Upload to GCS
        storage_service.upload_file(audio_path, audio_key, "audio/mpeg")

        return audio_key, file_size
