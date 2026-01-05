"""Tests for WebSocket progress notifications.

Features:
- WebSocket connection management
- Real-time render progress updates
- Connection lifecycle handling
"""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.api.websocket import (
    WebSocketManager,
    RenderProgressNotifier,
)


class TestWebSocketManager:
    """Tests for WebSocket connection manager."""

    @pytest.fixture
    def manager(self):
        """Create a WebSocket manager."""
        return WebSocketManager()

    @pytest.mark.asyncio
    async def test_connect_websocket(self, manager):
        """Test connecting a WebSocket client."""
        websocket = AsyncMock(spec=WebSocket)
        job_id = "job123"

        await manager.connect(websocket, job_id)

        assert job_id in manager._connections
        assert websocket in manager._connections[job_id]
        websocket.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_websocket(self, manager):
        """Test disconnecting a WebSocket client."""
        websocket = AsyncMock(spec=WebSocket)
        job_id = "job123"

        await manager.connect(websocket, job_id)
        manager.disconnect(websocket, job_id)

        assert websocket not in manager._connections.get(job_id, [])

    @pytest.mark.asyncio
    async def test_broadcast_to_job(self, manager):
        """Test broadcasting message to all clients for a job."""
        ws1 = AsyncMock(spec=WebSocket)
        ws2 = AsyncMock(spec=WebSocket)
        job_id = "job123"

        await manager.connect(ws1, job_id)
        await manager.connect(ws2, job_id)

        message = {"status": "processing", "percent": 50}
        await manager.broadcast(job_id, message)

        ws1.send_json.assert_called_once_with(message)
        ws2.send_json.assert_called_once_with(message)

    @pytest.mark.asyncio
    async def test_broadcast_handles_disconnected_client(self, manager):
        """Test that broadcast handles disconnected clients gracefully."""
        ws1 = AsyncMock(spec=WebSocket)
        ws2 = AsyncMock(spec=WebSocket)
        job_id = "job123"

        # ws1 will raise exception on send
        ws1.send_json.side_effect = RuntimeError("Connection closed")

        await manager.connect(ws1, job_id)
        await manager.connect(ws2, job_id)

        message = {"status": "processing", "percent": 50}
        await manager.broadcast(job_id, message)

        # ws2 should still receive the message
        ws2.send_json.assert_called_once_with(message)

    @pytest.mark.asyncio
    async def test_get_connection_count(self, manager):
        """Test getting connection count for a job."""
        ws1 = AsyncMock(spec=WebSocket)
        ws2 = AsyncMock(spec=WebSocket)
        job_id = "job123"

        assert manager.get_connection_count(job_id) == 0

        await manager.connect(ws1, job_id)
        assert manager.get_connection_count(job_id) == 1

        await manager.connect(ws2, job_id)
        assert manager.get_connection_count(job_id) == 2

        manager.disconnect(ws1, job_id)
        assert manager.get_connection_count(job_id) == 1

    @pytest.mark.asyncio
    async def test_multiple_jobs(self, manager):
        """Test managing connections for multiple jobs."""
        ws1 = AsyncMock(spec=WebSocket)
        ws2 = AsyncMock(spec=WebSocket)

        await manager.connect(ws1, "job1")
        await manager.connect(ws2, "job2")

        message1 = {"job_id": "job1", "percent": 25}
        message2 = {"job_id": "job2", "percent": 75}

        await manager.broadcast("job1", message1)
        await manager.broadcast("job2", message2)

        ws1.send_json.assert_called_once_with(message1)
        ws2.send_json.assert_called_once_with(message2)


class TestRenderProgressNotifier:
    """Tests for render progress notifier service."""

    @pytest.fixture
    def manager(self):
        """Create a mock WebSocket manager."""
        return MagicMock(spec=WebSocketManager)

    @pytest.fixture
    def notifier(self, manager):
        """Create a progress notifier."""
        return RenderProgressNotifier(manager)

    @pytest.mark.asyncio
    async def test_notify_progress(self, notifier, manager):
        """Test sending progress notification."""
        manager.broadcast = AsyncMock()

        await notifier.notify_progress(
            job_id="job123",
            percent=50.0,
            status="processing",
            current_step="レイヤー合成中",
        )

        manager.broadcast.assert_called_once()
        call_args = manager.broadcast.call_args
        assert call_args[0][0] == "job123"
        message = call_args[0][1]
        assert message["percent"] == 50.0
        assert message["status"] == "processing"
        assert message["current_step"] == "レイヤー合成中"

    @pytest.mark.asyncio
    async def test_notify_complete(self, notifier, manager):
        """Test sending completion notification."""
        manager.broadcast = AsyncMock()

        await notifier.notify_complete(
            job_id="job123",
            output_url="https://storage.example.com/output.mp4",
        )

        manager.broadcast.assert_called_once()
        call_args = manager.broadcast.call_args
        message = call_args[0][1]
        assert message["status"] == "completed"
        assert message["percent"] == 100.0
        assert message["output_url"] == "https://storage.example.com/output.mp4"

    @pytest.mark.asyncio
    async def test_notify_error(self, notifier, manager):
        """Test sending error notification."""
        manager.broadcast = AsyncMock()

        await notifier.notify_error(
            job_id="job123",
            error_message="FFmpeg encoding failed",
        )

        manager.broadcast.assert_called_once()
        call_args = manager.broadcast.call_args
        message = call_args[0][1]
        assert message["status"] == "failed"
        assert message["error_message"] == "FFmpeg encoding failed"

    @pytest.mark.asyncio
    async def test_notify_cancelled(self, notifier, manager):
        """Test sending cancellation notification."""
        manager.broadcast = AsyncMock()

        await notifier.notify_cancelled(job_id="job123")

        manager.broadcast.assert_called_once()
        call_args = manager.broadcast.call_args
        message = call_args[0][1]
        assert message["status"] == "cancelled"


class TestProgressMessageFormat:
    """Tests for progress message format."""

    def test_progress_message_structure(self):
        """Test that progress messages have correct structure."""
        from src.api.websocket import create_progress_message

        message = create_progress_message(
            job_id="job123",
            status="processing",
            percent=75.5,
            current_step="音声ミキシング中",
            elapsed_ms=5000,
        )

        assert message["type"] == "progress"
        assert message["job_id"] == "job123"
        assert message["status"] == "processing"
        assert message["percent"] == 75.5
        assert message["current_step"] == "音声ミキシング中"
        assert message["elapsed_ms"] == 5000

    def test_complete_message_structure(self):
        """Test that completion messages have correct structure."""
        from src.api.websocket import create_complete_message

        message = create_complete_message(
            job_id="job123",
            output_url="https://storage.example.com/video.mp4",
            duration_ms=120000,
            file_size_bytes=50000000,
        )

        assert message["type"] == "complete"
        assert message["job_id"] == "job123"
        assert message["status"] == "completed"
        assert message["output_url"] == "https://storage.example.com/video.mp4"
        assert message["duration_ms"] == 120000
        assert message["file_size_bytes"] == 50000000

    def test_error_message_structure(self):
        """Test that error messages have correct structure."""
        from src.api.websocket import create_error_message

        message = create_error_message(
            job_id="job123",
            error_message="Encoding failed",
            error_code="RENDER_ERROR",
        )

        assert message["type"] == "error"
        assert message["job_id"] == "job123"
        assert message["status"] == "failed"
        assert message["error_message"] == "Encoding failed"
        assert message["error_code"] == "RENDER_ERROR"
