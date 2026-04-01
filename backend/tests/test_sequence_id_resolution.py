"""Focused tests for sequence_id query param resolution in get_edit_context.

Verifies the resolution priority:
  X-Edit-Session > sequence_id > default sequence

Uses importlib to load ONLY src.api.deps without triggering the full
src.api.__init__ import chain (which pulls in Firebase, GCS, etc.).
"""

import datetime
import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Compat: datetime.UTC is 3.11+
# ---------------------------------------------------------------------------
if not hasattr(datetime, "UTC"):
    datetime.UTC = datetime.timezone.utc  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Stub the minimal transitive deps of src.api.deps (not the full src.api.*)
# ---------------------------------------------------------------------------
_stubs: dict[str, types.ModuleType] = {}


def _ensure_stub(name: str, attrs: dict | None = None):
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for k, v in (attrs or {}).items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    _stubs[name] = mod


_ensure_stub("firebase_admin", {
    "App": type("App", (), {}),
    "get_app": lambda: None,
    "initialize_app": lambda *a, **kw: None,
})
_ensure_stub("firebase_admin.auth", {"verify_id_token": lambda *a, **kw: {}})
_ensure_stub("firebase_admin.credentials", {"ApplicationDefault": lambda: None})
_ensure_stub("firebase_admin.firestore", {"client": lambda: MagicMock()})
_ensure_stub("src.services.storage_service", {"StorageService": object, "get_storage_service": lambda: None})
_ensure_stub("src.services.event_manager", {"event_manager": MagicMock()})

# Import deps module directly (avoids src.api.__init__ import chain)
_deps = importlib.import_module("src.api.deps")
get_edit_context = _deps.get_edit_context


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PROJECT_ID = uuid4()
SEQ_DEFAULT_ID = uuid4()
SEQ_TARGET_ID = uuid4()
SEQ_BOGUS_ID = uuid4()
USER_ID = uuid4()


def _make_project():
    proj = MagicMock()
    proj.id = PROJECT_ID
    proj.timeline_data = {"layers": [], "duration_ms": 1000}
    return proj


def _make_sequence(sid, *, is_default=False):
    seq = MagicMock()
    seq.id = sid
    seq.project_id = PROJECT_ID
    seq.is_default = is_default
    seq.timeline_data = {"layers": [{"id": str(sid)}], "duration_ms": 5000}
    seq.version = 1
    seq.locked_by = None
    return seq


def _make_user():
    user = MagicMock()
    user.id = USER_ID
    return user


def _mock_db(sequences: dict):
    """Mock AsyncSession that resolves select(Sequence) queries.

    Uses a call-order approach: the first execute with Sequence filters
    returns the sequence matching the bound params. We inspect the
    compiled statement's bind parameters to find the sequence id.
    """
    call_count = [0]

    async def _execute(stmt):
        result = MagicMock()
        call_count[0] += 1

        # Try to extract bound params from the compiled statement
        matched = None
        try:
            compiled = stmt.compile()
            params = compiled.params
            # Look for sequence id in bind params (may be UUID or str)
            str_sequences = {str(k): v for k, v in sequences.items()}
            for val in params.values():
                val_str = str(val)
                if val_str in str_sequences:
                    matched = str_sequences[val_str]
                    break
        except Exception:
            pass

        # Fallback: string match on the full statement
        if matched is None:
            try:
                stmt_str = str(stmt)
                for sid, seq in sequences.items():
                    if str(sid) in stmt_str:
                        matched = seq
                        break
            except Exception:
                pass

        # Fallback: check for is_default query
        if matched is None:
            stmt_str = str(stmt) if not isinstance(stmt, str) else stmt
            if "is_default" in stmt_str:
                for seq in sequences.values():
                    if getattr(seq, "is_default", False):
                        matched = seq
                        break

        result.scalar_one_or_none = MagicMock(return_value=matched)
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSequenceIdResolution:
    """get_edit_context resolves sequence by the documented priority."""

    async def test_sequence_id_selects_target_sequence(self):
        """sequence_id query param resolves to the specified sequence."""
        target = _make_sequence(SEQ_TARGET_ID)
        default = _make_sequence(SEQ_DEFAULT_ID, is_default=True)
        db = _mock_db({SEQ_TARGET_ID: target, SEQ_DEFAULT_ID: default})

        with patch("src.api.access.get_accessible_project", new_callable=AsyncMock, return_value=_make_project()):
            ctx = await get_edit_context(PROJECT_ID, _make_user(), db, x_edit_session=None, sequence_id=SEQ_TARGET_ID)

        assert ctx.sequence is not None
        assert ctx.sequence.id == SEQ_TARGET_ID

    async def test_invalid_sequence_id_raises_404(self):
        """Non-existent sequence_id returns HTTP 404."""
        db = _mock_db({})

        with patch("src.api.access.get_accessible_project", new_callable=AsyncMock, return_value=_make_project()):
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await get_edit_context(PROJECT_ID, _make_user(), db, x_edit_session=None, sequence_id=SEQ_BOGUS_ID)

            assert exc_info.value.status_code == 404
            assert "not found" in str(exc_info.value.detail).lower()

    async def test_edit_session_beats_sequence_id(self):
        """X-Edit-Session header takes priority over sequence_id."""
        session_seq = _make_sequence(SEQ_DEFAULT_ID)
        target_seq = _make_sequence(SEQ_TARGET_ID)
        db = _mock_db({SEQ_DEFAULT_ID: session_seq, SEQ_TARGET_ID: target_seq})

        fake_claims = {"pid": str(PROJECT_ID), "sid": str(SEQ_DEFAULT_ID), "uid": str(USER_ID)}

        with (
            patch("src.api.access.get_accessible_project", new_callable=AsyncMock, return_value=_make_project()),
            patch("src.api.deps.decode_edit_token", return_value=fake_claims),
        ):
            ctx = await get_edit_context(
                PROJECT_ID, _make_user(), db,
                x_edit_session="valid-token",
                sequence_id=SEQ_TARGET_ID,
            )

        assert ctx.sequence.id == SEQ_DEFAULT_ID  # edit session wins

    async def test_no_params_falls_back_to_default_sequence(self):
        """Without X-Edit-Session or sequence_id, default sequence is used."""
        default = _make_sequence(SEQ_DEFAULT_ID, is_default=True)
        db = _mock_db({SEQ_DEFAULT_ID: default})

        with patch("src.api.access.get_accessible_project", new_callable=AsyncMock, return_value=_make_project()):
            ctx = await get_edit_context(PROJECT_ID, _make_user(), db, x_edit_session=None, sequence_id=None)

        assert ctx.sequence is not None
        assert ctx.sequence.id == SEQ_DEFAULT_ID

    async def test_timeline_data_comes_from_selected_sequence(self):
        """EditContext.timeline_data returns the resolved sequence's data."""
        target = _make_sequence(SEQ_TARGET_ID)
        db = _mock_db({SEQ_TARGET_ID: target})

        with patch("src.api.access.get_accessible_project", new_callable=AsyncMock, return_value=_make_project()):
            ctx = await get_edit_context(PROJECT_ID, _make_user(), db, x_edit_session=None, sequence_id=SEQ_TARGET_ID)

        assert ctx.timeline_data == target.timeline_data
        assert ctx.timeline_data != _make_project().timeline_data
