"""AI service public implementation assembled from focused mixins."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.ai.chat import ChatMixin
from src.services.ai.llm_gateway import LLMGateway
from src.services.ai.project_queries import ProjectQueryMixin
from src.services.ai.timeline_editor import TimelineEditorMixin


class AIService(ProjectQueryMixin, TimelineEditorMixin, ChatMixin):
    """Service for AI-optimized project data access."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._llm = LLMGateway(db)
