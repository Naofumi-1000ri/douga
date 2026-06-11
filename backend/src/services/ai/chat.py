"""Chat orchestration methods extracted from AIService."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from src.config import get_settings
from src.models.asset import Asset
from src.models.project import Project
from src.schemas.ai import (
    ChatAction,
    ChatMessage,
    ChatResponse,
)
from src.services.ai import timeline_ops as _timeline_ops
from src.services.ai.llm_gateway import LLMGateway
from src.utils.field_encryption import decrypt_field

logger = logging.getLogger(__name__)


class ChatMixin:
    """Provider routing and streaming wrappers for AIService."""

    # =========================================================================
    # Chat: Natural Language Instructions via Multiple AI Providers
    # =========================================================================

    async def handle_chat(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        provider: str | None = None,
        *,
        context_project: Any | None = None,
        timeline_target: Any | None = None,
    ) -> ChatResponse:
        """Process a natural language chat message using the specified AI provider.

        Supports OpenAI, Gemini, and Anthropic APIs.
        Uses project-level API key if available, otherwise falls back to environment settings.

        Provider calls are delegated to LLMGateway; tool dispatch to timeline_ops.
        """
        settings = get_settings()
        context_source = context_project or project

        # Determine which provider to use (project setting > request > default)
        project_provider = getattr(project, "ai_provider", None)
        active_provider = project_provider or provider or settings.default_ai_provider

        # Use project-level API key if available, otherwise use environment settings
        project_api_key = decrypt_field(getattr(project, "ai_api_key", None))

        # Build timeline context with assets for filename → UUID mapping
        timeline = context_source.timeline_data or {}
        assets = await self._get_project_assets(project.id)
        context = self._build_chat_context(context_source, timeline, assets)
        system_prompt = self._build_chat_system_prompt(context)

        # Route to the appropriate provider (project API key takes priority)
        if active_provider == "openai":
            api_key = project_api_key or settings.openai_api_key
            return await self._chat_with_openai(
                project, message, history, system_prompt, api_key, timeline_target=timeline_target
            )
        elif active_provider == "gemini":
            api_key = project_api_key or settings.gemini_api_key
            return await self._chat_with_gemini(
                project, message, history, system_prompt, api_key, timeline_target=timeline_target
            )
        elif active_provider == "anthropic":
            api_key = project_api_key or settings.anthropic_api_key
            return await self._chat_with_anthropic(
                project,
                message,
                history,
                system_prompt,
                api_key,
                timeline_target=timeline_target,
            )
        else:
            return ChatResponse(
                message=f"不明なAIプロバイダーです: {active_provider}",
                actions=[],
            )

    async def _chat_with_openai(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
        *,
        timeline_target: Any | None = None,
    ) -> ChatResponse:
        """Process chat using OpenAI Function Calling API.

        Delegates to LLMGateway.call_openai.
        """
        return await self._llm.call_openai(
            project,
            message,
            history,
            system_prompt,
            api_key,
            execute_tool_calls_fn=self._execute_tool_calls,
            timeline_target=timeline_target,
        )

    async def _chat_with_gemini(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
        *,
        timeline_target: Any | None = None,
    ) -> ChatResponse:
        """Process chat using Google Gemini function declarations API.

        Delegates to LLMGateway.call_gemini.
        """
        return await self._llm.call_gemini(
            project,
            message,
            history,
            system_prompt,
            api_key,
            execute_tool_calls_fn=self._execute_tool_calls,
            timeline_target=timeline_target,
        )

    async def _chat_with_anthropic(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
        *,
        timeline_target: Any | None = None,
    ) -> ChatResponse:
        """Process chat using Anthropic Tool-use API (raw httpx).

        Delegates to LLMGateway.call_anthropic.
        """
        return await self._llm.call_anthropic(
            project,
            message,
            history,
            system_prompt,
            api_key,
            execute_tool_calls_fn=self._execute_tool_calls,
            timeline_target=timeline_target,
        )

    async def _execute_tool_calls(
        self,
        project: Project,
        tool_calls: list[dict[str, Any]],
        *,
        timeline_target: Any | None = None,
    ) -> list[ChatAction]:
        """Dispatch tool calls returned by any provider to the appropriate executor.

        Delegates to ``src.services.ai.timeline_ops.execute_tool_calls``.
        Each entry in ``tool_calls`` is ``{"name": str, "arguments": dict}``.
        """
        return await _timeline_ops.execute_tool_calls(
            self, project, tool_calls, timeline_target=timeline_target
        )

    @staticmethod
    def _build_chat_response(assistant_text: str, actions: list[ChatAction]) -> ChatResponse:
        """Build ChatResponse from assistant text and executed actions.

        Delegates to LLMGateway.build_chat_response.
        """
        return LLMGateway.build_chat_response(assistant_text, actions)

    async def _get_project_assets(self: Any, project_id: uuid.UUID) -> list[Asset]:
        """Fetch all assets for a project. Delegates to LLMGateway."""
        return await self._llm.get_project_assets(project_id)

    @staticmethod
    def _detect_clip_type(clip: dict[str, Any]) -> str:
        """Classify a timeline clip. Delegates to LLMGateway."""
        return LLMGateway.detect_clip_type(clip)

    @classmethod
    def _summarize_text_content(
        cls, clip: dict[str, Any], *, max_length: int | None = None
    ) -> tuple[str, str | None]:
        """Return text availability state. Delegates to LLMGateway."""
        return LLMGateway.summarize_text_content(clip, max_length=max_length)

    @classmethod
    def _summarize_text_background_for_context(cls, clip: dict[str, Any]) -> list[str]:
        """Expose text background state. Delegates to LLMGateway."""
        return LLMGateway.summarize_text_background_for_context(clip)

    @classmethod
    def _build_context_clip_summary(cls, clip: dict[str, Any]) -> str:
        """Serialize a clip into a compact line. Delegates to LLMGateway."""
        return LLMGateway.build_context_clip_summary(clip)

    def _build_chat_context(
        self, project: Project, timeline: dict, assets: list[Asset] | None = None
    ) -> str:
        """Build a compact timeline context string. Delegates to LLMGateway."""
        return self._llm.build_chat_context(project, timeline, assets)

    def _build_chat_system_prompt(self: Any, context: str) -> str:
        """Build the system prompt. Delegates to LLMGateway."""
        return self._llm.build_chat_system_prompt(context)

    async def _execute_chat_operations_on_project(
        self, project: Project, operations: list[dict]
    ) -> list[ChatAction]:
        """Execute parsed operations from Claude's response.

        Delegates to ``src.services.ai.timeline_ops.execute_chat_operations_on_project``.
        """
        return await _timeline_ops.execute_chat_operations_on_project(self, project, operations)

    async def _execute_chat_operations(
        self,
        project: Project,
        operations: list[dict],
        *,
        timeline_target: Any | None = None,
    ) -> list[ChatAction]:
        """Execute chat operations against the active timeline target.

        Delegates to ``src.services.ai.timeline_ops.execute_chat_operations``.
        """
        return await _timeline_ops.execute_chat_operations(
            self, project, operations, timeline_target=timeline_target
        )

    # =========================================================================
    # Chat Streaming: Server-Sent Events
    # =========================================================================

    async def handle_chat_stream(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        provider: str | None = None,
        *,
        context_project: Any | None = None,
        timeline_target: Any | None = None,
    ):
        """Process a chat message with streaming response.

        Yields SSE-formatted events:
        - chunk: Text chunks as they arrive
        - actions: JSON-encoded actions after execution
        - done: Completion signal
        - error: Error message if something fails

        Returns an async generator for use with StreamingResponse.
        """
        settings = get_settings()
        context_source = context_project or project

        # Determine which provider to use
        project_provider = getattr(project, "ai_provider", None)
        active_provider = project_provider or provider or settings.default_ai_provider

        # Use project-level API key if available
        project_api_key = decrypt_field(getattr(project, "ai_api_key", None))

        # Build timeline context with assets for filename → UUID mapping
        timeline = context_source.timeline_data or {}
        assets = await self._get_project_assets(project.id)
        context = self._build_chat_context(context_source, timeline, assets)
        system_prompt = self._build_chat_system_prompt(context)

        # Route to the appropriate provider
        if active_provider == "openai":
            api_key = project_api_key or settings.openai_api_key
            async for event in self._stream_openai(
                project,
                message,
                history,
                system_prompt,
                api_key,
                timeline_target=timeline_target,
            ):
                yield event
        elif active_provider == "gemini":
            api_key = project_api_key or settings.gemini_api_key
            async for event in self._stream_gemini(
                project,
                message,
                history,
                system_prompt,
                api_key,
                timeline_target=timeline_target,
            ):
                yield event
        elif active_provider == "anthropic":
            api_key = project_api_key or settings.anthropic_api_key
            async for event in self._stream_anthropic(
                project,
                message,
                history,
                system_prompt,
                api_key,
                timeline_target=timeline_target,
            ):
                yield event
        else:
            yield f"event: error\ndata: {json.dumps({'message': f'不明なAIプロバイダーです: {active_provider}'})}\n\n"
            yield "event: done\ndata: {}\n\n"

    async def _stream_openai(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
        *,
        timeline_target: Any | None = None,
    ):
        """Stream chat response from OpenAI. Delegates to LLMGateway.stream_openai."""
        async for event in self._llm.stream_openai(
            project,
            message,
            history,
            system_prompt,
            api_key,
            execute_tool_calls_fn=self._execute_tool_calls,
            timeline_target=timeline_target,
        ):
            yield event

    async def _stream_gemini(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
        *,
        timeline_target: Any | None = None,
    ):
        """Stream chat response from Gemini. Delegates to LLMGateway.stream_gemini."""
        async for event in self._llm.stream_gemini(
            project,
            message,
            history,
            system_prompt,
            api_key,
            execute_tool_calls_fn=self._execute_tool_calls,
            timeline_target=timeline_target,
        ):
            yield event

    async def _stream_anthropic(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
        *,
        timeline_target: Any | None = None,
    ):
        """Stream chat response from Anthropic. Delegates to LLMGateway.stream_anthropic."""
        async for event in self._llm.stream_anthropic(
            project,
            message,
            history,
            system_prompt,
            api_key,
            execute_tool_calls_fn=self._execute_tool_calls,
            timeline_target=timeline_target,
        ):
            yield event
