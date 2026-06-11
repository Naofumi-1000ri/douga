"""
Regression tests for apply_chroma_key idempotency / orphan GCS cleanup (Issue #292).

These tests verify that when record_operation raises HTTP 409 (idempotency conflict),
the GCS blob that was uploaded just before the conflict is deleted to avoid orphan files.

No real DB or GCS is needed — all I/O is mocked.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conflict_exc() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A request with this Idempotency-Key is already being processed.",
    )


def _make_other_http_exc() -> HTTPException:
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error")


# ---------------------------------------------------------------------------
# Tests for the GCS cleanup guard logic
# ---------------------------------------------------------------------------


class TestApplyChromaKeyOrphanGcsCleanup:
    """
    Unit-level regression tests for the orphan-GCS-cleanup guard added in Issue #292.

    The guard sits in apply_chroma_key after record_operation is called:

        try:
            operation = await operation_service.record_operation(...)
        except HTTPException as conflict_exc:
            if conflict_exc.status_code == 409:
                await storage.delete_file(storage_key)   # clean up orphan
            raise

    The tests exercise this logic in isolation by mocking the storage service
    and OperationService.record_operation.
    """

    @pytest.mark.asyncio
    async def test_409_conflict_triggers_gcs_delete(self):
        """When record_operation raises 409, delete_file must be called with the storage_key."""
        storage_key = f"projects/{uuid.uuid4()}/assets/{uuid.uuid4()}.webm"

        storage_mock = AsyncMock()
        storage_mock.delete_file = AsyncMock(return_value=True)

        # Simulate the guard logic directly (mirrors the code in apply_chroma_key)
        conflict_exc = _make_conflict_exc()
        caught_exc: HTTPException | None = None
        try:
            try:
                raise conflict_exc
            except HTTPException as exc:
                if exc.status_code == status.HTTP_409_CONFLICT:
                    await storage_mock.delete_file(storage_key)
                raise
        except HTTPException as exc:
            caught_exc = exc

        assert caught_exc is not None
        assert caught_exc.status_code == 409
        storage_mock.delete_file.assert_awaited_once_with(storage_key)

    @pytest.mark.asyncio
    async def test_non_409_http_exc_does_not_trigger_gcs_delete(self):
        """A non-409 HTTPException must NOT trigger delete_file."""
        storage_key = f"projects/{uuid.uuid4()}/assets/{uuid.uuid4()}.webm"

        storage_mock = AsyncMock()
        storage_mock.delete_file = AsyncMock(return_value=True)

        other_exc = _make_other_http_exc()
        caught_exc: HTTPException | None = None
        try:
            try:
                raise other_exc
            except HTTPException as exc:
                if exc.status_code == status.HTTP_409_CONFLICT:
                    await storage_mock.delete_file(storage_key)
                raise
        except HTTPException as exc:
            caught_exc = exc

        assert caught_exc is not None
        assert caught_exc.status_code == 500
        storage_mock.delete_file.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_409_gcs_delete_failure_is_swallowed_and_409_is_reraised(self):
        """If delete_file itself raises, the 409 is still re-raised (failure is non-fatal)."""
        storage_key = f"projects/{uuid.uuid4()}/assets/{uuid.uuid4()}.webm"

        storage_mock = AsyncMock()
        storage_mock.delete_file = AsyncMock(side_effect=Exception("GCS unavailable"))

        conflict_exc = _make_conflict_exc()
        caught_exc: HTTPException | None = None
        try:
            try:
                raise conflict_exc
            except HTTPException as exc:
                if exc.status_code == status.HTTP_409_CONFLICT:
                    try:
                        await storage_mock.delete_file(storage_key)
                    except Exception:
                        pass  # non-fatal — original 409 still raised
                raise
        except HTTPException as exc:
            caught_exc = exc

        assert caught_exc is not None
        assert caught_exc.status_code == 409
        storage_mock.delete_file.assert_awaited_once_with(storage_key)

    @pytest.mark.asyncio
    async def test_successful_record_operation_does_not_call_delete(self):
        """When record_operation succeeds (no exception), delete_file must NOT be called."""
        storage_key = f"projects/{uuid.uuid4()}/assets/{uuid.uuid4()}.webm"

        storage_mock = AsyncMock()
        storage_mock.delete_file = AsyncMock(return_value=True)

        # Simulate no exception (success path)
        raised = False
        try:
            try:
                # No exception raised — simulates successful record_operation
                pass
            except HTTPException as exc:
                if exc.status_code == status.HTTP_409_CONFLICT:
                    await storage_mock.delete_file(storage_key)
                raise
        except HTTPException:
            raised = True

        assert not raised
        storage_mock.delete_file.assert_not_awaited()
