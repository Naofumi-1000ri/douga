"""
Pytest fixtures for Douga backend tests.

Uses test data from /Users/hgs/devel/douga/test_data/
- 操作動画: Screen recording videos with audio
- 動画2_絵コンテ: Storyboard videos (no audio)
- 動画2_動画 2: Final rendered videos

CI/CD Note:
Tests that require local test data files are marked with @pytest.mark.requires_test_data
Run `pytest -m "not requires_test_data"` to skip these tests in CI.
"""

import os
import tempfile
from pathlib import Path

import pytest

# Test data paths - can be overridden via environment variable
TEST_DATA_ROOT = Path(os.environ.get("DOUGA_TEST_DATA_ROOT", "/Users/hgs/devel/douga/test_data"))
OPERATION_VIDEOS_DIR = TEST_DATA_ROOT / "操作動画"
STORYBOARD_DIR = TEST_DATA_ROOT / "動画2_絵コンテ"
FINAL_VIDEOS_DIR = TEST_DATA_ROOT / "動画2_動画 2"


def pytest_configure(config):
    """Register custom markers for CI/CD test filtering."""
    config.addinivalue_line(
        "markers",
        "requires_test_data: mark test as requiring local test data files (skipped in CI)"
    )


# Check if test data is available
def _test_data_available() -> bool:
    return TEST_DATA_ROOT.exists() and OPERATION_VIDEOS_DIR.exists()


# Skip decorator for tests requiring test data
requires_test_data = pytest.mark.skipif(
    not _test_data_available(),
    reason="Test data not available (run locally with test_data directory)"
)


@pytest.fixture
@requires_test_data
def test_data_root() -> Path:
    """Root directory for test data."""
    assert TEST_DATA_ROOT.exists(), f"Test data not found: {TEST_DATA_ROOT}"
    return TEST_DATA_ROOT


@pytest.fixture
@requires_test_data
def operation_video_with_audio() -> Path:
    """A short operation video with audio (6.5MB, ~50s)."""
    path = OPERATION_VIDEOS_DIR / "動画2_セクション2" / "sec2_rec1_検索画面差し替え.mp4"
    assert path.exists(), f"Test video not found: {path}"
    return path


@pytest.fixture
@requires_test_data
def operation_video_long() -> Path:
    """A longer operation video with audio (120MB, ~3min)."""
    path = OPERATION_VIDEOS_DIR / "動画2_セクション2" / "sec2_rec2-4_WCMC設定.mp4"
    assert path.exists(), f"Test video not found: {path}"
    return path


@pytest.fixture
@requires_test_data
def storyboard_video_no_audio() -> Path:
    """A storyboard video without audio (4.5MB, 100s)."""
    path = STORYBOARD_DIR / "動画2_絵コンテ_セクション2.mp4"
    assert path.exists(), f"Test video not found: {path}"
    return path


@pytest.fixture
@requires_test_data
def sample_video() -> Path:
    """A sample final video (124MB)."""
    path = FINAL_VIDEOS_DIR / "動画2_サンプル.mp4"
    assert path.exists(), f"Test video not found: {path}"
    return path


@pytest.fixture
def temp_output_dir():
    """Temporary directory for test outputs."""
    with tempfile.TemporaryDirectory(prefix="douga_test_") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
@requires_test_data
def multiple_audio_videos() -> list[Path]:
    """Multiple operation videos for mixing tests."""
    section2_dir = OPERATION_VIDEOS_DIR / "動画2_セクション2"
    videos = [
        section2_dir / "sec2_rec1_検索画面差し替え.mp4",
        section2_dir / "sec2_rec3_VRMファイルを開く.mp4",
        section2_dir / "sec2_rec4_サブスク.mp4",
    ]
    for v in videos:
        assert v.exists(), f"Test video not found: {v}"
    return videos


@pytest.fixture
@requires_test_data
def storyboard_images() -> list[Path]:
    """PNG images from storyboard for overlay tests."""
    images = [
        STORYBOARD_DIR / "動画2_追加1.png",
        STORYBOARD_DIR / "動画2_追加2.png",
        STORYBOARD_DIR / "動画2_追加3.png",
    ]
    for img in images:
        assert img.exists(), f"Test image not found: {img}"
    return images


# Aliases for clearer test naming
@pytest.fixture
def test_video_with_audio(operation_video_with_audio) -> Path:
    """Alias for operation video with audio."""
    return operation_video_with_audio


@pytest.fixture
def test_video_no_audio(storyboard_video_no_audio) -> Path:
    """Alias for storyboard video without audio."""
    return storyboard_video_no_audio


@pytest.fixture
def test_audio_with_audio(operation_video_with_audio, temp_output_dir) -> Path:
    """Extract audio from video and return audio file path (WAV format)."""
    import subprocess

    output_path = temp_output_dir / "extracted_audio.wav"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(operation_video_with_audio),
            "-vn",  # no video
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            "-ac", "2",
            str(output_path),
        ],
        capture_output=True,
        check=True,
    )
    return output_path
