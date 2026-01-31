"""SSE Event Manager for project change notifications.

Provides a pub/sub mechanism for real-time project updates via Server-Sent Events.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class ProjectEvent:
    """Event data for project changes."""

    event_type: str  # e.g., "timeline_updated", "clip_added", "clip_deleted"
    project_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    data: dict | None = None

    def to_sse(self) -> str:
        """Format event for SSE transmission."""
        import json

        event_data = {
            "type": self.event_type,
            "project_id": self.project_id,
            "timestamp": self.timestamp,
        }
        if self.data:
            event_data["data"] = self.data

        return f"event: {self.event_type}\ndata: {json.dumps(event_data)}\n\n"


class ProjectEventManager:
    """Manages SSE subscriptions and event publishing for projects."""

    def __init__(self) -> None:
        # Map project_id -> set of asyncio.Queue for each subscriber
        self._subscribers: dict[str, set[asyncio.Queue[ProjectEvent]]] = defaultdict(
            set
        )
        self._lock = asyncio.Lock()

    async def subscribe(
        self, project_id: str | UUID
    ) -> AsyncGenerator[ProjectEvent, None]:
        """Subscribe to events for a specific project.

        Args:
            project_id: Project UUID to subscribe to

        Yields:
            ProjectEvent objects as they are published
        """
        project_id_str = str(project_id)
        queue: asyncio.Queue[ProjectEvent] = asyncio.Queue()

        async with self._lock:
            self._subscribers[project_id_str].add(queue)
            logger.info(
                f"New subscriber for project {project_id_str}. "
                f"Total: {len(self._subscribers[project_id_str])}"
            )

        try:
            while True:
                event = await queue.get()
                yield event
        except asyncio.CancelledError:
            logger.info(f"Subscriber cancelled for project {project_id_str}")
            raise
        finally:
            async with self._lock:
                self._subscribers[project_id_str].discard(queue)
                logger.info(
                    f"Subscriber removed for project {project_id_str}. "
                    f"Remaining: {len(self._subscribers[project_id_str])}"
                )
                # Clean up empty subscriber sets
                if not self._subscribers[project_id_str]:
                    del self._subscribers[project_id_str]

    async def publish(
        self,
        project_id: str | UUID,
        event_type: str,
        data: dict | None = None,
    ) -> int:
        """Publish an event to all subscribers of a project.

        Args:
            project_id: Project UUID
            event_type: Type of event (e.g., "timeline_updated")
            data: Optional additional event data

        Returns:
            Number of subscribers notified
        """
        project_id_str = str(project_id)
        event = ProjectEvent(
            event_type=event_type,
            project_id=project_id_str,
            data=data,
        )

        async with self._lock:
            subscribers = self._subscribers.get(project_id_str, set()).copy()

        if not subscribers:
            logger.debug(f"No subscribers for project {project_id_str}")
            return 0

        notified = 0
        for queue in subscribers:
            try:
                queue.put_nowait(event)
                notified += 1
            except asyncio.QueueFull:
                logger.warning(
                    f"Queue full for subscriber of project {project_id_str}"
                )

        logger.info(
            f"Published {event_type} to {notified} subscribers "
            f"for project {project_id_str}"
        )
        return notified

    def get_subscriber_count(self, project_id: str | UUID) -> int:
        """Get the number of active subscribers for a project."""
        return len(self._subscribers.get(str(project_id), set()))


# Global event manager instance
event_manager = ProjectEventManager()
