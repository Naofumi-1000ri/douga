from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.api.projects import _resolve_last_edited_at, _sort_project_responses_by_last_edited
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
    project_updated_at = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
    sequence_updated_at = datetime(2026, 3, 10, 9, 30, tzinfo=timezone.utc)

    resolved = _resolve_last_edited_at(project_updated_at, sequence_updated_at)

    assert resolved == sequence_updated_at


def test_sort_project_responses_by_last_edited_uses_canonical_value() -> None:
    base = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
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
