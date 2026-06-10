"""
Regression integration tests for DB-backed idempotency enforcement (Issue #264).

These tests require a live Postgres 16 database and are marked ``requires_db``
so they are excluded from the default CI run (which has no DB).

Run with:
    DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:55432/douga_test' \
    ENVIRONMENT=test DEV_MODE=true \
    uv run pytest tests/test_idempotency_db.py -m requires_db -v

Key design notes
----------------
1. **Non-null user_id is mandatory for conflict tests.**
   The partial UNIQUE index ``idx_project_operations_idempotency_key_unique`` is
   defined as ``ON project_operations(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL``.
   Postgres treats NULL as distinct in a UNIQUE index, so two rows with
   ``user_id=NULL`` and the same ``idempotency_key`` do NOT conflict.  That is
   exactly why the existing in-memory tests (which pass ``user_id=None``) never
   exercised the UNIQUE violation path.  All conflict/scoping tests here use
   concrete non-null user_ids.

2. **Separate sessions / transactions for the duplicate insert.**
   ``record_operation`` calls ``db.flush()`` to trigger the constraint check.
   Because the FIRST operation must already be committed and visible to Postgres
   before the second insert violates the UNIQUE index, the duplicate must come
   from a fresh session/transaction.  Reusing the same session after commit is
   not sufficient — a rollback in one test would poison later tests.

3. **Schema setup via alembic upgrade head.**
   Previously used ``Base.metadata.create_all`` + ``run_migrations()``.
   Now uses ``alembic upgrade head`` which creates the full schema including
   all indexes (GIN indexes, partial UNIQUE indexes, etc.) in one step.
"""

from __future__ import annotations

import os
import types
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Module-level marker helpers — NO engine creation at import time so that
# the CI "broad suite" can collect and deselect this file without a DB.
# ---------------------------------------------------------------------------
pytestmark = [pytest.mark.requires_db, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_url() -> str:
    """Return the DATABASE_URL env var, or skip the test if absent."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skipping requires_db tests")
    return url


@pytest.fixture
async def engine(db_url):
    """Create a dedicated async engine for the test.

    Uses a separate engine (not the app's module-level engine) so we can
    control pool sizing and lifecycle independently.  Function-scoped so each
    test gets a fresh engine — avoids cross-loop conflicts with asyncio_mode=auto.

    Schema is created via ``alembic upgrade head`` which applies the full
    baseline schema including all partial UNIQUE indexes and GIN indexes.
    """
    # Import src.main INSIDE the fixture (never at module level) so that
    # all SQLAlchemy models are registered on Base.metadata.
    import src.main  # noqa: F401  — side-effect: registers all ORM models

    eng = create_async_engine(db_url, echo=False, future=True, pool_size=3, max_overflow=0)

    # Apply the full schema via Alembic (replaces create_all + run_migrations).
    # If the DB was previously set up without Alembic (DuplicateTableError),
    # stamp it as baseline and retry.
    import subprocess

    def _run_alembic(cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["uv", "run", "alembic"] + cmd,
            capture_output=True,
            text=True,
            cwd=str(__import__("pathlib").Path(__file__).parent.parent),
            env={**__import__("os").environ, "DATABASE_URL": db_url},
        )

    result = _run_alembic(["upgrade", "head"])
    if result.returncode != 0 and "DuplicateTable" in result.stderr:
        # DB has existing schema from the old create_all approach but no
        # alembic_version table — stamp it as baseline first.
        stamp = _run_alembic(["stamp", "0001_baseline"])
        if stamp.returncode != 0:
            raise RuntimeError(
                f"alembic stamp failed:\n{stamp.stdout}\n{stamp.stderr}"
            )
        result = _run_alembic(["upgrade", "head"])

    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
        )

    yield eng

    await eng.dispose()


@pytest.fixture
def session_factory(engine):
    """Return an async_sessionmaker bound to the test engine."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _make_user_and_project(session: AsyncSession) -> tuple:
    """INSERT and COMMIT a User + a Project owned by that user.

    Returns (user, project) — both committed and detached-safe because
    expire_on_commit=False is set on the sessionmaker.
    """
    from src.models.project import Project
    from src.models.user import User

    user = User(
        id=uuid4(),
        firebase_uid=f"test-uid-{uuid4().hex}",
        email=f"test-{uuid4().hex}@example.com",
        name="Test User",
    )
    session.add(user)
    await session.flush()

    project = Project(
        id=uuid4(),
        user_id=user.id,
        name="Test Project",
    )
    session.add(project)
    await session.commit()

    return user, project


# ---------------------------------------------------------------------------
# Test A — duplicate (user_id, idempotency_key) → 409; only one row persists
# ---------------------------------------------------------------------------


async def test_duplicate_idempotency_key_raises_409_and_only_one_row_persists(
    session_factory,
):
    """Inserting the same (user_id, idempotency_key) pair twice raises HTTP 409.

    Scenario:
        S1: commits an operation with KEY and user_id=U.id  (succeeds)
        S2: attempts a SECOND operation with the SAME KEY → must raise 409
        S3: verifies exactly ONE row with KEY exists in the DB

    Why a separate session for S2?
        S1 must commit before S2 inserts so the UNIQUE constraint can see the
        existing row.  After S1.commit() the session is still usable but we use
        a fresh session to mirror the real concurrent-request scenario and to
        avoid any SQLAlchemy identity-map interference.
    """
    from src.services.operation_service import OperationService

    key = str(uuid4())

    # --- Setup: create user + project (committed) ---
    async with session_factory() as s0:
        user, project = await _make_user_and_project(s0)

    # --- S1: first operation commits successfully ---
    async with session_factory() as s1:
        # Provide a lightweight proxy so record_operation can read project.id
        # without needing a live ORM session binding.
        project_proxy = types.SimpleNamespace(id=project.id)
        op = await OperationService(s1).record_operation(
            project_proxy,  # type: ignore[arg-type]
            operation_type="add_clip",
            source="api_v1",
            success=True,
            idempotency_key=key,
            user_id=user.id,
        )
        await s1.commit()
        first_op_id = op.id

    # --- S2: duplicate key in a fresh transaction → must raise 409 ---
    async with session_factory() as s2:
        project_proxy2 = types.SimpleNamespace(id=project.id)
        with pytest.raises(HTTPException) as exc_info:
            await OperationService(s2).record_operation(
                project_proxy2,  # type: ignore[arg-type]
                operation_type="add_clip",
                source="api_v1",
                success=True,
                idempotency_key=key,
                user_id=user.id,
            )
        assert exc_info.value.status_code == 409, (
            f"Expected 409 Conflict, got {exc_info.value.status_code}"
        )
        # record_operation calls db.rollback() internally on conflict;
        # session is now clean but we exit without commit.

    # --- S3: assert exactly ONE row for this key ---
    from sqlalchemy import func, select

    from src.models.operation import ProjectOperation

    async with session_factory() as s3:
        result = await s3.execute(
            select(func.count()).where(
                ProjectOperation.idempotency_key == key,
                ProjectOperation.user_id == user.id,
            )
        )
        count = result.scalar()
        assert count == 1, f"Expected exactly 1 operation row for key={key!r}, found {count}"

        # Also verify the surviving row is the FIRST insert.
        row_result = await s3.execute(
            select(ProjectOperation).where(
                ProjectOperation.idempotency_key == key,
                ProjectOperation.user_id == user.id,
            )
        )
        surviving_op = row_result.scalar_one()
        assert surviving_op.id == first_op_id


# ---------------------------------------------------------------------------
# Test B — non-idempotency IntegrityError is NOT converted to 409
# ---------------------------------------------------------------------------


async def test_non_idempotency_integrity_error_is_reraised(
    session_factory,
):
    """A FK violation (unknown project_id) with a non-None idempotency_key
    must propagate as IntegrityError, NOT as HTTPException(409).

    Why this matters:
        record_operation's guard is:
            if idempotency_key is None OR not _is_idempotency_conflict(exc): raise
        This test exercises the ``not _is_idempotency_conflict(exc)`` branch
        by supplying a non-None key alongside a foreign-key violation (which
        is NOT the idempotency UNIQUE index).

    Technique:
        Pass a project proxy whose .id does not exist in the projects table.
        Postgres will raise IntegrityError for the FK constraint on
        project_operations.project_id → projects.id.
    """
    from src.services.operation_service import OperationService

    # Create a real committed user so user_id FK is satisfied.
    async with session_factory() as s0:
        user, _ = await _make_user_and_project(s0)

    # Phantom project: id never inserted → FK violation on flush
    phantom_project = types.SimpleNamespace(id=uuid4())
    non_none_key = str(uuid4())

    async with session_factory() as s_fk:
        with pytest.raises(IntegrityError):
            await OperationService(s_fk).record_operation(
                phantom_project,  # type: ignore[arg-type]
                operation_type="add_clip",
                source="api_v1",
                success=True,
                idempotency_key=non_none_key,  # non-None so we hit _is_idempotency_conflict check
                user_id=user.id,
            )
        # Session is poisoned after the failed flush; roll back so teardown is clean.
        await s_fk.rollback()


# ---------------------------------------------------------------------------
# Test C — user-scoping: user B cannot read user A's stored response
# ---------------------------------------------------------------------------


async def test_user_scoping_prevents_cross_user_response_leak(
    session_factory,
):
    """User B cannot replay User A's stored idempotency response.

    Steps:
        1. Create User A, User B, and a Project owned by A.
        2. Record an operation for User A with KEY; store a response body on it.
        3. In a fresh session, assert:
           a. check_idempotency_db(KEY, session, user_id=A.id) → CachedResponse
              (positive control: proves the row IS reachable by its owner)
           b. check_idempotency_db(KEY, session, user_id=B.id) → None
              (B must not see A's response)
           c. enforce_idempotency(KEY, session, user_id=B.id) → None
              (no false 409 for B; no information leak)
           d. enforce_idempotency(KEY, session, user_id=A.id) → CachedResponse
              (owner replay works correctly)
    """
    from src.middleware.request_context import (
        CachedResponse,
        check_idempotency_db,
        enforce_idempotency,
        save_idempotency_db,
    )
    from src.models.project import Project
    from src.models.user import User
    from src.services.operation_service import OperationService

    key = str(uuid4())
    stored_body = {"data": {"clip": {"id": str(uuid4())}}, "sentinel": "user-a-only"}

    # --- Setup: User A, User B, Project owned by A ---
    async with session_factory() as s0:
        user_a = User(
            id=uuid4(),
            firebase_uid=f"test-uid-a-{uuid4().hex}",
            email=f"test-a-{uuid4().hex}@example.com",
            name="User A",
        )
        user_b = User(
            id=uuid4(),
            firebase_uid=f"test-uid-b-{uuid4().hex}",
            email=f"test-b-{uuid4().hex}@example.com",
            name="User B",
        )
        s0.add(user_a)
        s0.add(user_b)
        await s0.flush()

        project = Project(
            id=uuid4(),
            user_id=user_a.id,
            name="User A Project",
        )
        s0.add(project)
        await s0.commit()

        user_a_id = user_a.id
        user_b_id = user_b.id
        project_id = project.id

    # --- S1: Record operation for User A + persist response body ---
    async with session_factory() as s1:
        project_proxy = types.SimpleNamespace(id=project_id)
        op = await OperationService(s1).record_operation(
            project_proxy,  # type: ignore[arg-type]
            operation_type="add_clip",
            source="api_v1",
            success=True,
            idempotency_key=key,
            user_id=user_a_id,
        )
        # save_idempotency_db writes response_status_code + response_body.
        await save_idempotency_db(key, 201, stored_body, op.id, s1)
        await s1.commit()

    # --- S2: Assertions in a fresh read session ---
    async with session_factory() as s2:
        # (a) Positive control: owner can replay
        cached_a = await check_idempotency_db(key, s2, user_id=user_a_id)
        assert cached_a is not None, "Owner (User A) should get a CachedResponse"
        assert isinstance(cached_a, CachedResponse)
        assert cached_a.body == stored_body, (
            f"CachedResponse body mismatch: {cached_a.body!r} != {stored_body!r}"
        )
        assert cached_a.status_code == 201

        # (b) User B must NOT see User A's response
        cached_b = await check_idempotency_db(key, s2, user_id=user_b_id)
        assert cached_b is None, f"User B must not see User A's response, got: {cached_b!r}"

        # (c) enforce_idempotency for User B → None (no 409, no leak)
        # If scoping were broken, this would return cached_a or raise 409.
        enforce_b = await enforce_idempotency(key, s2, user_id=user_b_id)
        assert enforce_b is None, (
            f"enforce_idempotency must return None for User B (no row under B's scope), "
            f"got: {enforce_b!r}"
        )

        # (d) enforce_idempotency for User A → cached replay
        enforce_a = await enforce_idempotency(key, s2, user_id=user_a_id)
        assert enforce_a is not None, (
            "enforce_idempotency must return CachedResponse for User A (owner replay)"
        )
        assert isinstance(enforce_a, CachedResponse)
        assert enforce_a.body == stored_body
