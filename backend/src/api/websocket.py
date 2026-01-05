"""WebSocket support for real-time render progress notifications.

This module provides:
- WebSocketManager: Manages WebSocket connections per render job
- RenderProgressNotifier: High-level API for sending progress updates
- Message creation helpers: Standardized message formats
"""

from typing import Any, Optional

from fastapi import WebSocket


class WebSocketManager:
    """Manages WebSocket connections for render progress updates.

    Supports multiple clients watching the same render job.
    """

    def __init__(self):
        # job_id -> list of connected websockets
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, job_id: str) -> None:
        """Accept and register a WebSocket connection for a job."""
        await websocket.accept()
        if job_id not in self._connections:
            self._connections[job_id] = []
        self._connections[job_id].append(websocket)

    def disconnect(self, websocket: WebSocket, job_id: str) -> None:
        """Remove a WebSocket connection."""
        if job_id in self._connections:
            if websocket in self._connections[job_id]:
                self._connections[job_id].remove(websocket)
            # Clean up empty lists
            if not self._connections[job_id]:
                del self._connections[job_id]

    async def broadcast(self, job_id: str, message: dict[str, Any]) -> None:
        """Broadcast a message to all clients watching a job."""
        if job_id not in self._connections:
            return

        disconnected = []
        for websocket in self._connections[job_id]:
            try:
                await websocket.send_json(message)
            except Exception:
                # Client disconnected
                disconnected.append(websocket)

        # Clean up disconnected clients
        for ws in disconnected:
            self.disconnect(ws, job_id)

    def get_connection_count(self, job_id: str) -> int:
        """Get the number of connected clients for a job."""
        return len(self._connections.get(job_id, []))


class RenderProgressNotifier:
    """High-level API for sending render progress notifications."""

    def __init__(self, manager: WebSocketManager):
        self._manager = manager

    async def notify_progress(
        self,
        job_id: str,
        percent: float,
        status: str,
        current_step: Optional[str] = None,
        elapsed_ms: int = 0,
    ) -> None:
        """Send a progress update to all connected clients."""
        message = create_progress_message(
            job_id=job_id,
            status=status,
            percent=percent,
            current_step=current_step,
            elapsed_ms=elapsed_ms,
        )
        await self._manager.broadcast(job_id, message)

    async def notify_complete(
        self,
        job_id: str,
        output_url: str,
        duration_ms: int = 0,
        file_size_bytes: int = 0,
    ) -> None:
        """Send a completion notification to all connected clients."""
        message = create_complete_message(
            job_id=job_id,
            output_url=output_url,
            duration_ms=duration_ms,
            file_size_bytes=file_size_bytes,
        )
        await self._manager.broadcast(job_id, message)

    async def notify_error(
        self,
        job_id: str,
        error_message: str,
        error_code: Optional[str] = None,
    ) -> None:
        """Send an error notification to all connected clients."""
        message = create_error_message(
            job_id=job_id,
            error_message=error_message,
            error_code=error_code,
        )
        await self._manager.broadcast(job_id, message)

    async def notify_cancelled(self, job_id: str) -> None:
        """Send a cancellation notification to all connected clients."""
        message = {
            "type": "cancelled",
            "job_id": job_id,
            "status": "cancelled",
        }
        await self._manager.broadcast(job_id, message)


def create_progress_message(
    job_id: str,
    status: str,
    percent: float,
    current_step: Optional[str] = None,
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    """Create a standardized progress message."""
    return {
        "type": "progress",
        "job_id": job_id,
        "status": status,
        "percent": percent,
        "current_step": current_step,
        "elapsed_ms": elapsed_ms,
    }


def create_complete_message(
    job_id: str,
    output_url: str,
    duration_ms: int = 0,
    file_size_bytes: int = 0,
) -> dict[str, Any]:
    """Create a standardized completion message."""
    return {
        "type": "complete",
        "job_id": job_id,
        "status": "completed",
        "percent": 100.0,
        "output_url": output_url,
        "duration_ms": duration_ms,
        "file_size_bytes": file_size_bytes,
    }


def create_error_message(
    job_id: str,
    error_message: str,
    error_code: Optional[str] = None,
) -> dict[str, Any]:
    """Create a standardized error message."""
    return {
        "type": "error",
        "job_id": job_id,
        "status": "failed",
        "error_message": error_message,
        "error_code": error_code,
    }


# Global WebSocket manager instance
websocket_manager = WebSocketManager()
progress_notifier = RenderProgressNotifier(websocket_manager)
