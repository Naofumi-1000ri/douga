"""Firestore Event Manager for project change notifications.

Publishes project update events to Firestore for real-time sync with frontend.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from firebase_admin import firestore

from src.api.deps import get_firebase_app

logger = logging.getLogger(__name__)


class ProjectEventManager:
    """Manages event publishing for projects via Firestore."""

    def __init__(self) -> None:
        self._db = None

    def _get_db(self):
        """Lazy initialization of Firestore client."""
        if self._db is None:
            get_firebase_app()  # Ensure Firebase is initialized
            self._db = firestore.client()
        return self._db

    async def publish(
        self,
        project_id: str | UUID,
        event_type: str,
        data: dict | None = None,
    ) -> None:
        """Publish an event to Firestore for frontend to pick up.

        Args:
            project_id: Project UUID
            event_type: Type of event (e.g., "timeline_updated")
            data: Optional additional event data
        """
        project_id_str = str(project_id)

        try:
            db = self._get_db()
            doc_ref = db.collection("project_updates").document(project_id_str)

            update_data = {
                "updated_at": datetime.now(UTC),
                "source": data.get("source", "api") if data else "api",
                "operation": event_type,
            }

            doc_ref.set(update_data)

            logger.info(
                f"Published {event_type} to Firestore for project {project_id_str}"
            )
        except Exception as e:
            # Log error but don't fail the main operation
            logger.error(f"Failed to publish event to Firestore: {e}")


# Global event manager instance
event_manager = ProjectEventManager()
