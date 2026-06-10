"""Tests for apply_plan snapshot and rollback behaviour (Issue #266).

These are pure unit tests using mocks — no database required.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.ai_video import apply_plan
from src.schemas.ai_video import PlanApplyResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_simple_plan_dict() -> dict:
    """Minimal valid video_plan dict that plan_to_timeline can process."""
    return {
        "version": "1.0",
        "total_duration_ms": 5000,
        "status": "draft",
        "sections": [
            {
                "id": "sec_001",
                "type": "intro",
                "title": "Intro",
                "layout": "avatar_fullscreen",
                "start_ms": 0,
                "duration_ms": 5000,
                "elements": [],
                "audio": [],
            }
        ],
    }


def _make_original_timeline() -> dict:
    """Minimal existing timeline_data on the project."""
    return {
        "version": "1.0",
        "duration_ms": 3000,
        "layers": [
            {
                "id": str(uuid.uuid4()),
                "name": "Content",
                "type": "content",
                "order": 1,
                "visible": True,
                "locked": False,
                "clips": [
                    {
                        "id": str(uuid.uuid4()),
                        "asset_id": str(uuid.uuid4()),
                        "start_ms": 0,
                        "duration_ms": 3000,
                    }
                ],
            }
        ],
        "audio_tracks": [],
    }


def _make_project(project_id: uuid.UUID) -> MagicMock:
    project = MagicMock()
    project.id = project_id
    project.timeline_data = _make_original_timeline()
    project.duration_ms = 3000
    project.video_plan = _make_simple_plan_dict()
    return project


def _make_current_user(user_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(id=user_id)


def _make_db(default_seq_id: uuid.UUID | None) -> AsyncMock:
    """Build a mock async DB session.

    The first db.execute() call returns the default sequence id (for the
    snapshot lookup).  Subsequent calls (e.g. from _enrich_timeline_audio)
    return an empty result so they don't blow up.
    """
    db = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    # First execute → scalar_one_or_none returns default_seq_id
    first_execute_result = MagicMock()
    first_execute_result.scalar_one_or_none.return_value = default_seq_id

    # Subsequent executes → empty scalars
    subsequent_execute_result = MagicMock()
    subsequent_execute_result.scalars.return_value.all.return_value = []
    subsequent_execute_result.scalar_one_or_none.return_value = None

    db.execute = AsyncMock(
        side_effect=[
            first_execute_result,
            subsequent_execute_result,
            subsequent_execute_result,
            subsequent_execute_result,
            subsequent_execute_result,
        ]
    )
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestApplyPlanSnapshot:
    """Snapshot creation on successful apply_plan."""

    @pytest.mark.asyncio
    async def test_snapshot_created_on_success(self):
        """apply_plan should create a SequenceSnapshot before overwriting timeline."""
        project_id = uuid.uuid4()
        user_id = uuid.uuid4()
        default_seq_id = uuid.uuid4()
        snap_id = uuid.uuid4()

        project = _make_project(project_id)
        current_user = _make_current_user(user_id)
        db = _make_db(default_seq_id)

        # Capture the SequenceSnapshot passed to db.add()
        added_snapshots: list = []
        original_add = db.add.side_effect
        def _capture_add(obj):
            added_snapshots.append(obj)
            # Give the snapshot an id so snapshot_id can be read back
            obj.id = snap_id
        db.add.side_effect = _capture_add

        with (
            patch("src.api.ai_video._get_project", AsyncMock(return_value=project)),
            patch("src.api.ai_video._enrich_timeline_audio", AsyncMock()),
            patch("src.api.ai_video.flag_modified"),
        ):
            response = await apply_plan(project_id, current_user, db)

        assert isinstance(response, PlanApplyResponse)
        assert response.snapshot_id == snap_id, "snapshot_id should be returned in response"
        assert len(added_snapshots) == 1, "exactly one snapshot should be added to db"
        snap = added_snapshots[0]
        assert snap.sequence_id == default_seq_id
        assert snap.is_auto is True
        assert "Before apply_plan" in snap.name
        # The snapshot must contain the *original* timeline, not the new one
        assert snap.timeline_data["duration_ms"] == 3000

    @pytest.mark.asyncio
    async def test_snapshot_name_contains_apply_plan_label(self):
        """The snapshot name must be identifiable as coming from apply_plan."""
        project_id = uuid.uuid4()
        user_id = uuid.uuid4()
        default_seq_id = uuid.uuid4()

        project = _make_project(project_id)
        current_user = _make_current_user(user_id)
        db = _make_db(default_seq_id)

        added_snapshots: list = []
        def _capture_add(obj):
            added_snapshots.append(obj)
            obj.id = uuid.uuid4()
        db.add.side_effect = _capture_add

        with (
            patch("src.api.ai_video._get_project", AsyncMock(return_value=project)),
            patch("src.api.ai_video._enrich_timeline_audio", AsyncMock()),
            patch("src.api.ai_video.flag_modified"),
        ):
            await apply_plan(project_id, current_user, db)

        assert len(added_snapshots) == 1
        assert "apply_plan" in added_snapshots[0].name.lower()

    @pytest.mark.asyncio
    async def test_no_snapshot_when_no_default_sequence(self):
        """If the project has no default sequence, apply still succeeds (snapshot skipped)."""
        project_id = uuid.uuid4()
        user_id = uuid.uuid4()

        project = _make_project(project_id)
        current_user = _make_current_user(user_id)
        # Pass None → no default sequence found
        db = _make_db(default_seq_id=None)

        with (
            patch("src.api.ai_video._get_project", AsyncMock(return_value=project)),
            patch("src.api.ai_video._enrich_timeline_audio", AsyncMock()),
            patch("src.api.ai_video.flag_modified"),
        ):
            response = await apply_plan(project_id, current_user, db)

        assert isinstance(response, PlanApplyResponse)
        assert response.snapshot_id is None
        # db.add should not have been called for a snapshot
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_snapshot_when_empty_timeline(self):
        """If the project has no existing timeline, snapshot is skipped."""
        project_id = uuid.uuid4()
        user_id = uuid.uuid4()
        default_seq_id = uuid.uuid4()

        project = _make_project(project_id)
        project.timeline_data = None   # no existing timeline
        current_user = _make_current_user(user_id)
        db = _make_db(default_seq_id)

        with (
            patch("src.api.ai_video._get_project", AsyncMock(return_value=project)),
            patch("src.api.ai_video._enrich_timeline_audio", AsyncMock()),
            patch("src.api.ai_video.flag_modified"),
        ):
            response = await apply_plan(project_id, current_user, db)

        assert response.snapshot_id is None
        db.add.assert_not_called()


class TestApplyPlanRollback:
    """Timeline rollback on enrich failure."""

    @pytest.mark.asyncio
    async def test_rollback_on_enrich_failure(self):
        """When _enrich_timeline_audio raises, the original timeline must be restored."""
        project_id = uuid.uuid4()
        user_id = uuid.uuid4()
        default_seq_id = uuid.uuid4()

        original_timeline = _make_original_timeline()
        project = _make_project(project_id)
        project.timeline_data = original_timeline
        current_user = _make_current_user(user_id)
        db = _make_db(default_seq_id)

        snap_id = uuid.uuid4()
        added_snapshots: list = []
        def _capture_add(obj):
            added_snapshots.append(obj)
            obj.id = snap_id
        db.add.side_effect = _capture_add

        flag_calls: list[tuple] = []

        with (
            patch("src.api.ai_video._get_project", AsyncMock(return_value=project)),
            patch(
                "src.api.ai_video._enrich_timeline_audio",
                AsyncMock(side_effect=RuntimeError("audio extraction failed")),
            ),
            patch(
                "src.api.ai_video.flag_modified",
                side_effect=lambda obj, attr: flag_calls.append((obj, attr)),
            ),
        ):
            with pytest.raises(RuntimeError, match="audio extraction failed"):
                await apply_plan(project_id, current_user, db)

        # The project's timeline_data should be the *original* (rolled back).
        # Use == instead of `is` because MagicMock attribute tracking wraps the value.
        assert project.timeline_data == original_timeline, (
            "timeline_data must be restored to original on rollback"
        )
        # flag_modified should have been called for timeline_data (rollback)
        timeline_flag_calls = [a for _, a in flag_calls if a == "timeline_data"]
        assert timeline_flag_calls, "flag_modified('timeline_data') must be called during rollback"

    @pytest.mark.asyncio
    async def test_snapshot_retained_after_rollback(self):
        """The pre-apply snapshot must survive even when apply_plan raises."""
        project_id = uuid.uuid4()
        user_id = uuid.uuid4()
        default_seq_id = uuid.uuid4()

        project = _make_project(project_id)
        current_user = _make_current_user(user_id)
        db = _make_db(default_seq_id)

        snap_id = uuid.uuid4()
        added_snapshots: list = []
        def _capture_add(obj):
            added_snapshots.append(obj)
            obj.id = snap_id
        db.add.side_effect = _capture_add

        with (
            patch("src.api.ai_video._get_project", AsyncMock(return_value=project)),
            patch(
                "src.api.ai_video._enrich_timeline_audio",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch("src.api.ai_video.flag_modified"),
        ):
            with pytest.raises(RuntimeError):
                await apply_plan(project_id, current_user, db)

        # Snapshot must have been created before the failure
        assert len(added_snapshots) == 1, "pre-apply snapshot must be present"
        assert added_snapshots[0].id == snap_id

    @pytest.mark.asyncio
    async def test_plan_status_reverted_on_rollback(self):
        """video_plan status must be reverted from 'applied' back to 'ready' on failure."""
        project_id = uuid.uuid4()
        user_id = uuid.uuid4()
        default_seq_id = uuid.uuid4()

        project = _make_project(project_id)
        # Start with status "draft"
        project.video_plan["status"] = "draft"
        current_user = _make_current_user(user_id)
        db = _make_db(default_seq_id)

        def _capture_add(obj):
            obj.id = uuid.uuid4()
        db.add.side_effect = _capture_add

        with (
            patch("src.api.ai_video._get_project", AsyncMock(return_value=project)),
            patch(
                "src.api.ai_video._enrich_timeline_audio",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch("src.api.ai_video.flag_modified"),
        ):
            with pytest.raises(RuntimeError):
                await apply_plan(project_id, current_user, db)

        # plan status must be rolled back to "draft" so the user can retry
        assert project.video_plan["status"] == "draft", (
            "video_plan status must be reverted to 'draft' on failure"
        )

    @pytest.mark.asyncio
    async def test_snapshot_id_in_response_on_success(self):
        """PlanApplyResponse must include the snapshot_id field."""
        project_id = uuid.uuid4()
        user_id = uuid.uuid4()
        default_seq_id = uuid.uuid4()
        snap_id = uuid.uuid4()

        project = _make_project(project_id)
        current_user = _make_current_user(user_id)
        db = _make_db(default_seq_id)

        def _capture_add(obj):
            obj.id = snap_id
        db.add.side_effect = _capture_add

        with (
            patch("src.api.ai_video._get_project", AsyncMock(return_value=project)),
            patch("src.api.ai_video._enrich_timeline_audio", AsyncMock()),
            patch("src.api.ai_video.flag_modified"),
        ):
            response = await apply_plan(project_id, current_user, db)

        assert response.snapshot_id == snap_id
        # Other fields should still be present
        assert response.project_id == project_id
        assert isinstance(response.duration_ms, int)
        assert isinstance(response.layers_populated, int)
        assert isinstance(response.audio_clips_added, int)
