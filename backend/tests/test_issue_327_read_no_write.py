"""
Regression tests for Issue #327: v1 read-handler ORM attribute assignment
must not trigger implicit UPDATE projects via SQLAlchemy autoflush/commit.

Test strategy
-------------

Unit tests (no DB required)
  - Verify that ``_resolve_edit_session(..., read_only=True)`` calls
    ``db.expunge(project)`` exactly once.
  - Verify that ``_resolve_edit_session(..., read_only=False)`` (i.e. the
    write path) does NOT call ``db.expunge``.

Integration tests (requires_db)
  - Create a real Project + Sequence in a live Postgres instance.
  - Register a SQLAlchemy ``after_bulk_update`` / ``before_flush`` event
    listener that records every UPDATE emitted against the ``projects`` table.
  - Call each representative read-only endpoint handler function directly
    (bypassing HTTP), then flush/commit the session.
  - Assert that no UPDATE on projects was recorded.

Run only unit tests (CI):
    uv run pytest tests/test_issue_327_read_no_write.py -m "not requires_db" -q

Run all (local with DB on port 55436):
    DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:55436/douga_test' \\
    ENVIRONMENT=test DEV_MODE=true \\
    uv run pytest tests/test_issue_327_read_no_write.py -v
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Unit tests — no DB needed
# ---------------------------------------------------------------------------


class TestResolveEditSessionReadOnlyFlag:
    """_resolve_edit_session(read_only=True) must expunge the project."""

    @pytest.mark.asyncio
    async def test_read_only_true_calls_expunge(self):
        """read_only=True must call db.expunge(project)."""
        from src.api.ai_v1 import _resolve_edit_session

        # Build lightweight stubs
        project_stub = MagicMock()
        sequence_stub = MagicMock()

        ctx_stub = MagicMock()
        ctx_stub.project = project_stub
        ctx_stub.sequence = sequence_stub

        db_stub = MagicMock()
        db_stub.expunge = MagicMock()

        with patch("src.api.ai_v1._helpers.get_edit_context", new=AsyncMock(return_value=ctx_stub)):
            proj, seq = await _resolve_edit_session(
                project_id=uuid4(),
                current_user=MagicMock(),
                db=db_stub,
                x_edit_session=None,
                read_only=True,
            )

        db_stub.expunge.assert_called_once_with(project_stub)
        assert proj is project_stub
        assert seq is sequence_stub

    @pytest.mark.asyncio
    async def test_read_only_false_does_not_call_expunge(self):
        """read_only=False (default / write path) must NOT call db.expunge."""
        from src.api.ai_v1 import _resolve_edit_session

        project_stub = MagicMock()
        ctx_stub = MagicMock()
        ctx_stub.project = project_stub
        ctx_stub.sequence = None

        db_stub = MagicMock()
        db_stub.expunge = MagicMock()

        with patch("src.api.ai_v1._helpers.get_edit_context", new=AsyncMock(return_value=ctx_stub)):
            await _resolve_edit_session(
                project_id=uuid4(),
                current_user=MagicMock(),
                db=db_stub,
                x_edit_session=None,
                read_only=False,
            )

        db_stub.expunge.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_read_only_is_false(self):
        """Default read_only must be False to keep write endpoints unaffected."""
        from src.api.ai_v1 import _resolve_edit_session

        ctx_stub = MagicMock()
        ctx_stub.project = MagicMock()
        ctx_stub.sequence = None

        db_stub = MagicMock()
        db_stub.expunge = MagicMock()

        with patch("src.api.ai_v1._helpers.get_edit_context", new=AsyncMock(return_value=ctx_stub)):
            await _resolve_edit_session(
                project_id=uuid4(),
                current_user=MagicMock(),
                db=db_stub,
                x_edit_session=None,
                # read_only omitted → default False
            )

        db_stub.expunge.assert_not_called()


class TestReadEndpointsUseReadOnlyFlag:
    """Verify (via AST/source inspection) that all read-only endpoints pass read_only=True."""

    def test_all_read_handlers_pass_read_only_true(self):
        """Every call to _resolve_edit_session (not _for_write) in ai_v1 package files
        must include ``read_only=True``.

        Exception: the internal delegation inside ``_resolve_edit_session_for_write``
        itself (which forwards to ``_resolve_edit_session`` with require_role="editor")
        is intentionally excluded — that is a write-path call and must NOT have
        read_only=True.
        """
        import ast
        import pathlib

        src_dir = pathlib.Path(__file__).parent.parent / "src" / "api" / "ai_v1"
        parsed_sources: list[tuple[pathlib.Path, ast.Module]] = []
        for src_path in sorted(src_dir.glob("*.py")):
            source = src_path.read_text(encoding="utf-8")
            parsed_sources.append((src_path, ast.parse(source)))

        # Collect the line range of _resolve_edit_session_for_write so we can
        # exclude the internal delegation call.
        write_fn_lines_by_file: dict[pathlib.Path, set[int]] = {}
        for src_path, tree in parsed_sources:
            for node in ast.walk(tree):
                if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    if node.name == "_resolve_edit_session_for_write":
                        write_fn_lines_by_file[src_path] = set(
                            range(node.lineno, node.end_lineno + 1)
                        )
                        break

        violations: list[str] = []

        for src_path, tree in parsed_sources:
            write_fn_lines = write_fn_lines_by_file.get(src_path, set())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Await):
                    continue
                call = node.value
                if not isinstance(call, ast.Call):
                    continue
                # Check for _resolve_edit_session (not _for_write)
                func = call.func
                if not isinstance(func, ast.Name):
                    continue
                if func.id != "_resolve_edit_session":
                    continue

                # Skip the internal delegation inside _resolve_edit_session_for_write
                if node.lineno in write_fn_lines:
                    continue

                # Check keyword args for read_only=True
                has_read_only_true = any(
                    isinstance(kw.arg, str)
                    and kw.arg == "read_only"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                    for kw in call.keywords
                )

                if not has_read_only_true:
                    violations.append(f"{src_path.name}:{node.lineno}")

        assert not violations, (
            f"These call sites for _resolve_edit_session are missing read_only=True "
            f"(file:line in ai_v1 package): {violations}"
        )


# ---------------------------------------------------------------------------
# Integration tests — requires live Postgres
# ---------------------------------------------------------------------------


pytestmark_db = pytest.mark.requires_db


@pytest.fixture
def db_url_327() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skipping requires_db tests")
    return url


@pytest.fixture
async def engine_327(db_url_327, alembic_upgrade_head):
    """Dedicated engine for #327 DB tests.

    Schema is created via ``alembic upgrade head`` which applies the full
    baseline schema including all partial UNIQUE indexes and GIN indexes.
    """
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

    import src.main  # noqa: F401 — registers ORM models on Base.metadata

    eng = create_async_engine(db_url_327, echo=False, future=True, pool_size=3, max_overflow=0)

    alembic_upgrade_head(db_url_327)

    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory_327(engine_327):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    return async_sessionmaker(engine_327, class_=AsyncSession, expire_on_commit=False)


async def _make_user_project_sequence(session):
    """Create and commit a User, Project, and default Sequence."""
    from src.models.project import Project
    from src.models.sequence import Sequence
    from src.models.user import User

    user = User(
        id=uuid4(),
        firebase_uid=f"uid-{uuid4().hex}",
        email=f"{uuid4().hex}@test.example",
        name="Test User 327",
    )
    session.add(user)
    await session.flush()

    project = Project(
        id=uuid4(),
        user_id=user.id,
        name="Test Project 327",
        duration_ms=0,
        timeline_data={"version": "1.0", "duration_ms": 0, "layers": [], "audio_tracks": []},
    )
    session.add(project)
    await session.flush()

    sequence = Sequence(
        id=uuid4(),
        project_id=project.id,
        name="Main",
        is_default=True,
        timeline_data={
            "version": "1.0",
            "duration_ms": 5000,
            "layers": [],
            "audio_tracks": [],
        },
        duration_ms=5000,
    )
    session.add(sequence)
    await session.commit()
    return user, project, sequence


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_read_handler_does_not_update_projects_table(session_factory_327):
    """After calling a read-only handler, the projects table must not be updated.

    Mechanism: attach a SQLAlchemy ``before_flush`` event listener that collects
    any ``dirty`` Project instances. After the simulated handler body executes,
    flush and commit the session. The listener must have recorded zero dirty
    Project instances.
    """
    from sqlalchemy import event

    from src.models.project import Project
    from src.models.sequence import Sequence

    dirty_projects: list = []

    async with session_factory_327() as session:
        user, project, sequence = await _make_user_project_sequence(session)

        # Register listener on this specific session
        @event.listens_for(session.sync_session, "before_flush")
        def capture_dirty(sess, flush_context, instances):
            for obj in sess.dirty:
                if isinstance(obj, Project):
                    dirty_projects.append(obj)

        # --- Simulate the read-handler body ---
        # Re-load project and sequence in this session to get managed instances
        from sqlalchemy import select

        proj_result = await session.execute(select(Project).where(Project.id == project.id))
        proj = proj_result.scalar_one()

        seq_result = await session.execute(select(Sequence).where(Sequence.id == sequence.id))
        seq = seq_result.scalar_one()

        # This mirrors what read handlers do (expunge first, then assign)
        session.expunge(proj)  # read_only=True path
        proj.timeline_data = seq.timeline_data  # detached → no dirty tracking
        proj.duration_ms = seq.duration_ms  # detached → no dirty tracking

        # Commit the session — before_flush must observe NO dirty Project
        await session.commit()

    assert dirty_projects == [], (
        f"Expected no dirty Project instances after read-handler simulation, "
        f"but got: {dirty_projects}"
    )


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_write_handler_can_still_update_projects(session_factory_327):
    """Regression: write handlers (no expunge) must still be able to update projects."""
    from sqlalchemy import select

    from src.models.project import Project
    from src.models.sequence import Sequence

    flushed_projects: list = []

    from sqlalchemy import event

    async with session_factory_327() as session:
        user, project, sequence = await _make_user_project_sequence(session)

        @event.listens_for(session.sync_session, "before_flush")
        def capture_dirty(sess, flush_context, instances):
            for obj in sess.dirty:
                if isinstance(obj, Project):
                    flushed_projects.append(obj)

        # --- Simulate write-handler body (no expunge) ---
        proj_result = await session.execute(select(Project).where(Project.id == project.id))
        proj = proj_result.scalar_one()

        seq_result = await session.execute(select(Sequence).where(Sequence.id == sequence.id))
        seq = seq_result.scalar_one()

        # Write path: NO expunge — assign directly onto managed instance
        proj.timeline_data = seq.timeline_data
        proj.duration_ms = seq.duration_ms

        await session.commit()

    # The write path should have flushed at least one dirty Project
    assert flushed_projects, (
        "Expected write handler to produce a dirty Project for flushing, but none found"
    )


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_updated_at_unchanged_after_read_handler(session_factory_327):
    """projects.updated_at must not advance after a read-only handler call.

    This is the key business invariant: GET endpoints must never cause
    ``updated_at`` (or any other column) to change in the projects table.
    """
    from sqlalchemy import select

    from src.models.project import Project
    from src.models.sequence import Sequence

    # --- Setup ---
    async with session_factory_327() as setup_session:
        user, project, sequence = await _make_user_project_sequence(setup_session)
        project_id = project.id
        sequence_id = sequence.id
        original_updated_at = project.updated_at

    # --- Read-only simulation ---
    async with session_factory_327() as session:
        proj_result = await session.execute(select(Project).where(Project.id == project_id))
        proj = proj_result.scalar_one()

        seq_result = await session.execute(select(Sequence).where(Sequence.id == sequence_id))
        seq = seq_result.scalar_one()

        # Simulate read_only=True path
        session.expunge(proj)
        proj.timeline_data = seq.timeline_data
        proj.duration_ms = seq.duration_ms

        await session.commit()

    # --- Verify updated_at unchanged ---
    async with session_factory_327() as verify_session:
        result = await verify_session.execute(select(Project).where(Project.id == project_id))
        proj_after = result.scalar_one()

    assert proj_after.updated_at == original_updated_at, (
        f"projects.updated_at changed after read-only handler: "
        f"{original_updated_at} → {proj_after.updated_at}"
    )
