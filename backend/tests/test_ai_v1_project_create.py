"""Focused tests for AI v1 project creation."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.api.ai_v1 import projects as projects_module


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        now = datetime.now(UTC)
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()
            if obj.__class__.__name__ == "Project":
                if getattr(obj, "duration_ms", None) is None:
                    obj.duration_ms = 0
                if getattr(obj, "status", None) is None:
                    obj.status = "draft"
                if getattr(obj, "created_at", None) is None:
                    obj.created_at = now
                if getattr(obj, "updated_at", None) is None:
                    obj.updated_at = now

    async def refresh(self, obj: object) -> None:
        await self.flush()


@pytest.mark.asyncio
async def test_create_project_v1_initializes_firestore_allowed_users(monkeypatch):
    """V1 project creation must preserve legacy realtime access initialization."""
    calls: list[dict[str, object]] = []

    async def fake_set_allowed_users(project_id, firebase_uids):
        calls.append({"project_id": project_id, "firebase_uids": firebase_uids})
        return True

    monkeypatch.setattr(
        projects_module.event_manager,
        "set_allowed_users",
        fake_set_allowed_users,
    )

    current_user = SimpleNamespace(id=uuid4(), firebase_uid="firebase-user-1")
    http_request = SimpleNamespace(headers={})
    response = await projects_module.create_project_v1(
        projects_module.CreateProjectV1Request(name="Created via MCP"),
        current_user,
        _FakeDb(),
        http_request,
    )

    assert len(calls) == 1
    assert str(calls[0]["project_id"]) == response.data["id"]
    assert calls[0]["firebase_uids"] == ["firebase-user-1"]
