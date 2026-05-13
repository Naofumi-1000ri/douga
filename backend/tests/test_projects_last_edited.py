from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from src.api import projects as projects_api
from src.api.projects import (
    _get_thumbnail_url,
    _resolve_last_edited_at,
    _sort_project_responses_by_last_edited,
)
from src.constants.media_urls import SIGNED_MEDIA_URL_EXPIRES_MINUTES
from src.schemas.project import ProjectListResponse


def _project_response(
    *, updated_at: datetime, last_edited_at: datetime | None = None, name: str
) -> ProjectListResponse:
    return ProjectListResponse(
        id=uuid4(),
        name=name,
        description=None,
        status="draft",
        duration_ms=0,
        thumbnail_url=None,
        created_at=updated_at,
        updated_at=updated_at,
        last_edited_at=last_edited_at,
        is_shared=False,
        role="owner",
        owner_name=None,
    )


def test_resolve_last_edited_at_prefers_sequence_timestamp() -> None:
    project_updated_at = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
    sequence_updated_at = datetime(2026, 3, 10, 9, 30, tzinfo=UTC)

    resolved = _resolve_last_edited_at(project_updated_at, sequence_updated_at, None)

    assert resolved == sequence_updated_at


def test_resolve_last_edited_at_prefers_latest_session_save_when_newest() -> None:
    project_updated_at = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
    sequence_updated_at = datetime(2026, 3, 10, 9, 30, tzinfo=UTC)
    session_updated_at = datetime(2026, 3, 10, 10, 45, tzinfo=UTC)

    resolved = _resolve_last_edited_at(
        project_updated_at,
        sequence_updated_at,
        session_updated_at,
    )

    assert resolved == session_updated_at


def test_sort_project_responses_by_last_edited_uses_canonical_value() -> None:
    base = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)
    project_only = _project_response(
        name="project-only",
        updated_at=base - timedelta(hours=1),
        last_edited_at=None,
    )
    sequence_recent = _project_response(
        name="sequence-recent",
        updated_at=base - timedelta(days=2),
        last_edited_at=base,
    )

    ordered = _sort_project_responses_by_last_edited([project_only, sequence_recent])

    assert [project.name for project in ordered] == ["sequence-recent", "project-only"]


def test_project_thumbnail_legacy_gcs_url_is_resigned(monkeypatch) -> None:
    storage = MagicMock()
    storage.generate_download_url.return_value = "https://signed.example.com/legacy.jpg"
    monkeypatch.setattr(projects_api, "get_storage_service", lambda: storage)
    project = SimpleNamespace(
        id=uuid4(),
        thumbnail_storage_key=None,
        thumbnail_url=(
            "https://storage.googleapis.com/douga-assets/thumbnails/projects/project-id/"
            "thumbnail.jpg?X-Goog-Date=20240101T000000Z"
        ),
    )

    assert _get_thumbnail_url(project) == "https://signed.example.com/legacy.jpg"
    storage.generate_download_url.assert_called_once_with(
        "thumbnails/projects/project-id/thumbnail.jpg",
        expires_minutes=SIGNED_MEDIA_URL_EXPIRES_MINUTES,
    )


def test_project_thumbnail_unknown_legacy_url_is_preserved() -> None:
    legacy_url = "https://cdn.example.com/legacy-project-thumbnail.jpg"
    project = SimpleNamespace(
        id=uuid4(),
        thumbnail_storage_key=None,
        thumbnail_url=legacy_url,
    )

    assert _get_thumbnail_url(project) == legacy_url


def test_project_thumbnail_is_signed_from_storage_key(monkeypatch) -> None:
    storage = MagicMock()
    storage.generate_download_url.return_value = "https://signed.example.com/thumb.jpg"
    monkeypatch.setattr(projects_api, "get_storage_service", lambda: storage)
    project = SimpleNamespace(
        id=uuid4(),
        thumbnail_storage_key="thumbnails/projects/project-id/thumbnail.jpg",
        thumbnail_url="https://storage.googleapis.com/bucket/stale.jpg",
    )

    assert _get_thumbnail_url(project) == "https://signed.example.com/thumb.jpg"
    storage.generate_download_url.assert_called_once_with(
        "thumbnails/projects/project-id/thumbnail.jpg",
        expires_minutes=SIGNED_MEDIA_URL_EXPIRES_MINUTES,
    )
