"""
Unit tests for DB-backed idempotency enforcement (Issue #264).

These tests do NOT require a real database — they use lightweight fakes to
verify the logic of:
  - check_idempotency_db: cache hit / miss
  - enforce_idempotency: 409 on in-flight duplicate
  - idempotent_success helper in ai_v1
  - transcription _save_transcription / _load_transcription persistence

Tests that need a real Postgres DB are marked ``requires_db`` and excluded
from the default CI run.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from src.middleware.request_context import (
    CachedResponse,
    check_idempotency_db,
    enforce_idempotency,
    save_idempotency_db,
)
from src.services.idempotency_store import _NoopIdempotencyStore, idempotency_store

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_db(operation_rows: list[Any] = None, *, no_rows: bool = False):
    """Build a minimal async DB fake for idempotency tests."""

    class FakeScalar:
        def __init__(self, rows):
            self._rows = rows

        def one_or_none(self):
            return self._rows[0] if self._rows else None

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def one_or_none(self):
            return self._rows[0] if self._rows else None

        def scalar_one_or_none(self):
            return self._rows[0] if self._rows else None

    class FakeDb:
        def __init__(self, rows):
            self._rows = rows
            self.flushed = False

        async def execute(self, _stmt):
            return FakeResult(self._rows)

        async def flush(self):
            self.flushed = True

        async def get(self, model, pk):
            if self._rows:
                return self._rows[0]
            return None

    return FakeDb(operation_rows or [])


# ---------------------------------------------------------------------------
# Legacy noop store tests
# ---------------------------------------------------------------------------


def test_noop_store_get_returns_none():
    store = _NoopIdempotencyStore()
    assert store.get("any-key") is None


def test_noop_store_set_is_harmless():
    store = _NoopIdempotencyStore()
    store.set("k", 200, {"ok": True})
    # Still returns None after set (noop)
    assert store.get("k") is None


def test_module_singleton_is_noop():
    """The module-level idempotency_store is the noop variant."""
    assert isinstance(idempotency_store, _NoopIdempotencyStore)


# ---------------------------------------------------------------------------
# check_idempotency_db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_idempotency_db_returns_none_for_none_key():
    db = _make_fake_db()
    result = await check_idempotency_db(None, db)
    assert result is None


@pytest.mark.asyncio
async def test_check_idempotency_db_returns_none_when_no_row():
    """No matching operation row → cache miss."""
    db = _make_fake_db(no_rows=True)
    result = await check_idempotency_db("some-key", db)
    assert result is None


@pytest.mark.asyncio
async def test_check_idempotency_db_returns_cached_response():
    """Matching row with response_body → CachedResponse replay."""
    row = SimpleNamespace(
        response_status_code=201,
        response_body={"data": {"clip": {"id": "clip-1"}}},
    )
    db = _make_fake_db([row])
    result = await check_idempotency_db("key-1", db)
    assert isinstance(result, CachedResponse)
    assert result.status_code == 201
    assert result.body["data"]["clip"]["id"] == "clip-1"


# ---------------------------------------------------------------------------
# enforce_idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enforce_idempotency_none_key_returns_none():
    db = _make_fake_db()
    result = await enforce_idempotency(None, db)
    assert result is None


@pytest.mark.asyncio
async def test_enforce_idempotency_new_key_returns_none():
    """No row in DB for this key → proceed with operation."""
    db = _make_fake_db(no_rows=True)
    result = await enforce_idempotency("new-unique-key", db)
    assert result is None


@pytest.mark.asyncio
async def test_enforce_idempotency_completed_key_returns_cached():
    """Row with response_body already set → replay."""
    row = SimpleNamespace(
        response_status_code=200,
        response_body={"data": {"clip_id": "clip-xyz"}},
    )
    db = _make_fake_db([row])
    result = await enforce_idempotency("existing-key", db)
    assert isinstance(result, CachedResponse)
    assert result.body["data"]["clip_id"] == "clip-xyz"


@pytest.mark.asyncio
async def test_enforce_idempotency_inflight_raises_409():
    """Row exists but response_body is None → 409 Conflict (in-flight)."""
    from fastapi import HTTPException

    # First execute call (check_idempotency_db): returns no completed row (response_body IS NULL)
    # Second execute call (in-flight check): returns a row (id only)
    in_flight_row = SimpleNamespace(id=uuid.uuid4())

    call_count = 0

    class FakeDb:
        async def execute(self, _stmt):
            nonlocal call_count
            call_count += 1

            class Result:
                def __init__(self, row):
                    self._row = row

                def one_or_none(self):
                    return self._row

                def scalar_one_or_none(self):
                    return self._row

            # First call: check for completed response → no row
            # Second call: check for any row (in-flight) → row exists
            if call_count == 1:
                return Result(None)
            return Result(in_flight_row)

    with pytest.raises(HTTPException) as exc_info:
        await enforce_idempotency("inflight-key", FakeDb())  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# save_idempotency_db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_idempotency_db_noop_for_none_key():
    db = _make_fake_db()
    await save_idempotency_db(None, 200, {"ok": True}, uuid.uuid4(), db)
    # No exception → success


@pytest.mark.asyncio
async def test_save_idempotency_db_sets_fields_on_operation():
    """save_idempotency_db updates response_status_code and response_body."""
    op_id = uuid.uuid4()
    operation = SimpleNamespace(
        id=op_id,
        response_status_code=None,
        response_body=None,
    )
    db = _make_fake_db([operation])

    await save_idempotency_db("my-key", 201, {"data": "payload"}, op_id, db)

    assert operation.response_status_code == 201
    assert operation.response_body == {"data": "payload"}
    assert db.flushed


@pytest.mark.asyncio
async def test_save_idempotency_db_noop_when_operation_not_found():
    """When the operation row doesn't exist, no error is raised."""
    db = _make_fake_db(no_rows=True)
    await save_idempotency_db("key", 200, {}, uuid.uuid4(), db)
    # No exception


# ---------------------------------------------------------------------------
# Transcription persistence helpers
# ---------------------------------------------------------------------------


def test_load_transcription_returns_none_for_no_metadata():
    from src.api.transcription import _load_transcription

    asset = SimpleNamespace(asset_metadata=None)
    assert _load_transcription(asset) is None  # type: ignore[arg-type]


def test_load_transcription_returns_none_for_missing_key():
    from src.api.transcription import _load_transcription

    asset = SimpleNamespace(asset_metadata={"other": "data"})
    assert _load_transcription(asset) is None  # type: ignore[arg-type]


def test_save_and_load_transcription_roundtrip():
    from src.api.transcription import _load_transcription, _save_transcription
    from src.schemas.timeline import Transcription

    asset = SimpleNamespace(asset_metadata=None)
    transcription = Transcription(
        asset_id=uuid.uuid4(),
        language="ja",
        status="processing",
    )

    _save_transcription(asset, transcription)  # type: ignore[arg-type]

    loaded = _load_transcription(asset)  # type: ignore[arg-type]
    assert loaded is not None
    assert loaded.status == "processing"
    assert loaded.asset_id == transcription.asset_id


def test_save_transcription_overwrites_previous():
    from src.api.transcription import _load_transcription, _save_transcription
    from src.schemas.timeline import Transcription

    asset = SimpleNamespace(asset_metadata=None)
    asset_id = uuid.uuid4()

    t1 = Transcription(asset_id=asset_id, status="processing")
    _save_transcription(asset, t1)  # type: ignore[arg-type]

    t2 = Transcription(asset_id=asset_id, status="completed")
    _save_transcription(asset, t2)  # type: ignore[arg-type]

    loaded = _load_transcription(asset)  # type: ignore[arg-type]
    assert loaded is not None
    assert loaded.status == "completed"


def test_save_transcription_preserves_other_metadata_keys():
    from src.api.transcription import _save_transcription
    from src.schemas.timeline import Transcription

    asset = SimpleNamespace(asset_metadata={"app_version": "1.2.3"})
    t = Transcription(asset_id=uuid.uuid4(), status="completed")
    _save_transcription(asset, t)  # type: ignore[arg-type]

    assert asset.asset_metadata.get("app_version") == "1.2.3"
    assert "transcription" in asset.asset_metadata
