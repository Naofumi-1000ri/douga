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

# --------------------------------------------------------------------------
# Test environment defaults (must run before any `src.*` import).
#
# The production-safe default for `use_local_storage` is False (Issue #259),
# which makes `src.services.storage_service` instantiate GCSStorageService at
# import time. In CI there are no GCP Application Default Credentials, so that
# import would raise DefaultCredentialsError during test collection.
#
# Tests never talk to real GCS (they stub the storage service), so force the
# local-storage backend here. setdefault keeps an explicit override intact.
# This is a test-only shim and does NOT change the product default.
# --------------------------------------------------------------------------
os.environ.setdefault("USE_LOCAL_STORAGE", "true")

import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

# Test data paths - can be overridden via environment variable
TEST_DATA_ROOT = Path(os.environ.get("DOUGA_TEST_DATA_ROOT", "/Users/hgs/devel/douga/test_data"))
OPERATION_VIDEOS_DIR = TEST_DATA_ROOT / "操作動画"
STORYBOARD_DIR = TEST_DATA_ROOT / "動画2_絵コンテ"
FINAL_VIDEOS_DIR = TEST_DATA_ROOT / "動画2_動画 2"


def pytest_configure(config):
    """Register custom markers for CI/CD test filtering."""
    config.addinivalue_line(
        "markers",
        "requires_test_data: mark test as requiring local test data files (skipped in CI)",
    )


def _run_alembic_upgrade_head(database_url: str) -> None:
    """Apply the test DB schema the same way deploys do: alembic upgrade head."""
    import subprocess
    import sys

    backend_dir = Path(__file__).parent.parent

    def _run_alembic(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            capture_output=True,
            text=True,
            cwd=backend_dir,
            env={**os.environ, "DATABASE_URL": database_url},
        )

    result = _run_alembic(["upgrade", "head"])
    if result.returncode != 0 and "DuplicateTable" in f"{result.stdout}\n{result.stderr}":
        stamp = _run_alembic(["stamp", "0001_baseline"])
        if stamp.returncode != 0:
            raise RuntimeError(f"alembic stamp failed:\n{stamp.stdout}\n{stamp.stderr}")
        result = _run_alembic(["upgrade", "head"])

    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture(scope="session", autouse=True)
def migrated_test_database(request: pytest.FixtureRequest) -> None:
    """Ensure requires_db tests run against an Alembic-managed schema.

    Issue #282 removed startup DDL from the FastAPI lifespan. DB tests that use
    the real app now need the same pre-start migration step as deployment.
    """
    if not any(item.get_closest_marker("requires_db") for item in request.session.items):
        return

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return

    _run_alembic_upgrade_head(database_url)


# Check if test data is available
def _test_data_available() -> bool:
    return TEST_DATA_ROOT.exists() and OPERATION_VIDEOS_DIR.exists()


def _skip_if_test_data_unavailable() -> None:
    if not _test_data_available():
        pytest.skip("Test data not available (run locally with test_data directory)")


# Skip decorator for tests requiring test data
requires_test_data = pytest.mark.skipif(
    not _test_data_available(),
    reason="Test data not available (run locally with test_data directory)",
)


@pytest.fixture
def test_data_root() -> Path:
    """Root directory for test data."""
    _skip_if_test_data_unavailable()
    return TEST_DATA_ROOT


@pytest.fixture
def operation_video_with_audio() -> Path:
    """A short operation video with audio (6.5MB, ~50s)."""
    _skip_if_test_data_unavailable()
    path = OPERATION_VIDEOS_DIR / "動画2_セクション2" / "sec2_rec1_検索画面差し替え.mp4"
    assert path.exists(), f"Test video not found: {path}"
    return path


@pytest.fixture
def operation_video_long() -> Path:
    """A longer operation video with audio (120MB, ~3min)."""
    _skip_if_test_data_unavailable()
    path = OPERATION_VIDEOS_DIR / "動画2_セクション2" / "sec2_rec2-4_WCMC設定.mp4"
    assert path.exists(), f"Test video not found: {path}"
    return path


@pytest.fixture
def storyboard_video_no_audio() -> Path:
    """A storyboard video without audio (4.5MB, 100s)."""
    _skip_if_test_data_unavailable()
    path = STORYBOARD_DIR / "動画2_絵コンテ_セクション2.mp4"
    assert path.exists(), f"Test video not found: {path}"
    return path


@pytest.fixture
def sample_video() -> Path:
    """A sample final video (124MB)."""
    _skip_if_test_data_unavailable()
    path = FINAL_VIDEOS_DIR / "動画2_サンプル.mp4"
    assert path.exists(), f"Test video not found: {path}"
    return path


@pytest.fixture
def temp_output_dir():
    """Temporary directory for test outputs."""
    with tempfile.TemporaryDirectory(prefix="douga_test_") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def multiple_audio_videos() -> list[Path]:
    """Multiple operation videos for mixing tests."""
    _skip_if_test_data_unavailable()
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
def storyboard_images() -> list[Path]:
    """PNG images from storyboard for overlay tests."""
    _skip_if_test_data_unavailable()
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
            "ffmpeg",
            "-y",
            "-i",
            str(operation_video_with_audio),
            "-vn",  # no video
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(output_path),
        ],
        capture_output=True,
        check=True,
    )
    return output_path
