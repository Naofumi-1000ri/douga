"""LLM Gateway — provider call layer extracted from ai_service.py (Issue #284).

Handles:
- Non-streaming chat calls to OpenAI / Gemini / Anthropic
- Streaming SSE calls to OpenAI / Gemini / Anthropic
- Project context serialization (clips → compact prompt text)
- System-prompt construction
- _build_chat_response helper

Public surface used by AIService.handle_chat / handle_chat_stream is unchanged.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.asset import Asset
from src.schemas.ai import ChatAction, ChatMessage, ChatResponse
from src.services.ai.utils import _escape_user_string
from src.services.chat_tools import (
    AnthropicToolAdapter,
    GeminiToolAdapter,
    OpenAIToolAdapter,
)

if TYPE_CHECKING:
    from src.models.project import Project

logger = logging.getLogger(__name__)


class LLMGateway:
    """Thin wrapper around OpenAI / Gemini / Anthropic APIs.

    Handles context construction, message formatting, HTTP calls and SSE
    streaming.  All provider-specific logic is isolated here so AIService
    can focus on timeline operations.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Non-streaming calls
    # ------------------------------------------------------------------

    async def call_openai(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
        *,
        execute_tool_calls_fn: Any,
        timeline_target: Any | None = None,
    ) -> ChatResponse:
        """Process chat using OpenAI Function Calling API."""
        if not api_key:
            return ChatResponse(
                message="OpenAI APIキーが設定されていません。backend/.env に OPENAI_API_KEY を設定してください。",
                actions=[],
            )

        settings = get_settings()
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for msg in history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": message})

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.openai_chat_model,
                        "max_tokens": 16384,
                        "messages": messages,
                        "tools": OpenAIToolAdapter.build_tools(),
                        "tool_choice": "auto",
                    },
                )

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"OpenAI API error: {response.status_code} - {error_detail}")
                return ChatResponse(
                    message=f"OpenAI APIエラー (HTTP {response.status_code})",
                    actions=[],
                )

            result = response.json()
            msg_obj = result["choices"][0]["message"]
            assistant_text = msg_obj.get("content") or ""
            tool_calls = OpenAIToolAdapter.parse_tool_calls(msg_obj)

            if tool_calls:
                actions = await execute_tool_calls_fn(
                    project, tool_calls, timeline_target=timeline_target
                )
                return self._build_chat_response(assistant_text, actions)
            else:
                return ChatResponse(message=assistant_text.strip(), actions=[])

        except httpx.TimeoutException:
            logger.error("OpenAI API timeout")
            return ChatResponse(
                message="OpenAI APIがタイムアウトしました。もう一度お試しください。",
                actions=[],
            )
        except Exception as e:
            logger.exception("OpenAI chat processing error")
            return ChatResponse(
                message=f"OpenAI エラー: {str(e)}",
                actions=[],
            )

    async def call_gemini(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
        *,
        execute_tool_calls_fn: Any,
        timeline_target: Any | None = None,
    ) -> ChatResponse:
        """Process chat using Google Gemini function declarations API."""
        if not api_key:
            return ChatResponse(
                message="Gemini APIキーが設定されていません。backend/.env に GEMINI_API_KEY を設定してください。",
                actions=[],
            )

        settings = get_settings()

        # Build Gemini-formatted messages with system instruction embedded in first user turn
        contents: list[dict[str, Any]] = []
        if history:
            for i, msg in enumerate(history[-10:]):
                role = "user" if msg.role == "user" else "model"
                text = msg.content
                if i == 0 and msg.role == "user":
                    text = (
                        f"[System Instructions]\n{system_prompt}\n\n[User Message]\n{msg.content}"
                    )
                contents.append({"role": role, "parts": [{"text": text}]})

        # Add current message
        if not contents:
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": f"[System Instructions]\n{system_prompt}\n\n[User Message]\n{message}"
                        }
                    ],
                }
            )
        else:
            contents.append({"role": "user", "parts": [{"text": message}]})

        model_name = settings.gemini_chat_model
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}",
                    headers={"Content-Type": "application/json"},
                    json={
                        "contents": contents,
                        "tools": GeminiToolAdapter.build_tools(),
                        "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
                        "generationConfig": {
                            "maxOutputTokens": 8192,
                            "temperature": 0.7,
                        },
                    },
                )

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"Gemini API error: {response.status_code} - {error_detail}")
                return ChatResponse(
                    message=f"Gemini APIエラー (HTTP {response.status_code})",
                    actions=[],
                )

            result = response.json()
            logger.info(f"[Gemini] Response keys: {result.keys()}")
            candidates = result.get("candidates", [])
            if not candidates:
                logger.error(f"[Gemini] No candidates. Full response: {result}")
                return ChatResponse(
                    message="Geminiからの応答がありませんでした。",
                    actions=[],
                )

            finish_reason = candidates[0].get("finishReason", "UNKNOWN")
            logger.info(f"[Gemini] Finish reason: {finish_reason}")

            tool_calls = GeminiToolAdapter.parse_tool_calls(candidates)
            assistant_text = GeminiToolAdapter.extract_text(candidates)
            logger.info(f"[Gemini] tool_calls={len(tool_calls)}, text_len={len(assistant_text)}")

            if tool_calls:
                actions = await execute_tool_calls_fn(
                    project, tool_calls, timeline_target=timeline_target
                )
                return self._build_chat_response(assistant_text, actions)
            else:
                return ChatResponse(message=assistant_text.strip(), actions=[])

        except httpx.TimeoutException:
            logger.error("Gemini API timeout")
            return ChatResponse(
                message="Gemini APIがタイムアウトしました。もう一度お試しください。",
                actions=[],
            )
        except Exception as e:
            logger.exception("Gemini chat processing error")
            return ChatResponse(
                message=f"Gemini エラー: {str(e)}",
                actions=[],
            )

    async def call_anthropic(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
        *,
        execute_tool_calls_fn: Any,
        timeline_target: Any | None = None,
    ) -> ChatResponse:
        """Process chat using Anthropic Tool-use API (raw httpx)."""
        if not api_key:
            return ChatResponse(
                message="Anthropic APIキーが設定されていません。backend/.env に ANTHROPIC_API_KEY を設定してください。",
                actions=[],
            )

        settings = get_settings()

        # Build Anthropic-formatted messages
        messages: list[dict[str, Any]] = []
        for msg in history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": message})

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.anthropic_chat_model,
                        "max_tokens": 16384,
                        "system": system_prompt,
                        "messages": messages,
                        "tools": AnthropicToolAdapter.build_tools(),
                        "tool_choice": {"type": "auto"},
                    },
                )

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"Anthropic API error: {response.status_code} - {error_detail}")
                return ChatResponse(
                    message=f"Anthropic APIエラー (HTTP {response.status_code})",
                    actions=[],
                )

            result = response.json()
            content_blocks = result.get("content", [])
            assistant_text = AnthropicToolAdapter.extract_text(content_blocks)
            tool_calls = AnthropicToolAdapter.parse_tool_calls(content_blocks)

            if tool_calls:
                actions = await execute_tool_calls_fn(
                    project, tool_calls, timeline_target=timeline_target
                )
                return self._build_chat_response(assistant_text, actions)
            else:
                return ChatResponse(message=assistant_text.strip(), actions=[])

        except httpx.TimeoutException:
            logger.error("Anthropic API timeout")
            return ChatResponse(
                message="Anthropic APIがタイムアウトしました。もう一度お試しください。",
                actions=[],
            )
        except Exception as e:
            logger.exception("Anthropic chat processing error")
            return ChatResponse(
                message=f"Anthropic エラー: {str(e)}",
                actions=[],
            )

    # ------------------------------------------------------------------
    # Streaming calls
    # ------------------------------------------------------------------

    async def stream_openai(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
        *,
        execute_tool_calls_fn: Any,
        timeline_target: Any | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream chat response from OpenAI with Function Calling (tool_calls in stream)."""
        if not api_key:
            yield f"event: error\ndata: {json.dumps({'message': 'OpenAI APIキーが設定されていません。'})}\n\n"
            yield "event: done\ndata: {}\n\n"
            return

        settings = get_settings()
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for msg in history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": message})

        try:
            full_text = ""
            # Accumulate streamed tool_call deltas: index -> {id, name, arguments_parts}
            tool_call_acc: dict[int, dict[str, Any]] = {}

            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream(
                    "POST",
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.openai_chat_model,
                        "max_tokens": 16384,
                        "messages": messages,
                        "stream": True,
                        "tools": OpenAIToolAdapter.build_tools(),
                        "tool_choice": "auto",
                    },
                ) as response:
                    if response.status_code != 200:
                        error_text = (await response.aread()).decode("utf-8", errors="replace")
                        logger.error(f"OpenAI API error: {response.status_code} - {error_text}")
                        yield f"event: error\ndata: {json.dumps({'message': f'OpenAI APIエラー (HTTP {response.status_code})'})}\n\n"
                        yield "event: done\ndata: {}\n\n"
                        return

                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                choice = (chunk.get("choices") or [{}])[0]
                                delta = choice.get("delta", {})
                                # Stream text content
                                content = delta.get("content", "")
                                if content:
                                    full_text += content
                                    yield f"event: chunk\ndata: {json.dumps({'text': content})}\n\n"
                                # Accumulate tool_calls deltas
                                for tc in delta.get("tool_calls") or []:
                                    idx = tc.get("index", 0)
                                    if idx not in tool_call_acc:
                                        tool_call_acc[idx] = {
                                            "id": "",
                                            "name": "",
                                            "arguments_parts": [],
                                        }
                                    acc = tool_call_acc[idx]
                                    fn = tc.get("function", {})
                                    if tc.get("id"):
                                        acc["id"] = tc["id"]
                                    if fn.get("name"):
                                        acc["name"] += fn["name"]
                                    if fn.get("arguments"):
                                        acc["arguments_parts"].append(fn["arguments"])
                            except json.JSONDecodeError:
                                continue

            # Reassemble and execute tool calls
            if tool_call_acc:
                assembled: list[dict[str, Any]] = []
                for acc in tool_call_acc.values():
                    args_str = "".join(acc["arguments_parts"])
                    try:
                        arguments = json.loads(args_str)
                    except json.JSONDecodeError:
                        arguments = {}
                    assembled.append({"name": acc["name"], "arguments": arguments})

                actions = await execute_tool_calls_fn(
                    project, assembled, timeline_target=timeline_target
                )
                if actions:
                    actions_data = [
                        {"type": a.type, "description": a.description, "applied": a.applied}
                        for a in actions
                    ]
                    yield f"event: actions\ndata: {json.dumps(actions_data)}\n\n"

        except httpx.TimeoutException:
            logger.error("OpenAI API timeout during streaming")
            yield f"event: error\ndata: {json.dumps({'message': 'OpenAI APIがタイムアウトしました。'})}\n\n"
        except Exception as e:
            logger.exception("OpenAI streaming error")
            yield f"event: error\ndata: {json.dumps({'message': f'OpenAI エラー: {str(e)}'})}\n\n"
        finally:
            yield "event: done\ndata: {}\n\n"

    async def stream_gemini(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
        *,
        execute_tool_calls_fn: Any,
        timeline_target: Any | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream chat response from Gemini with function declarations."""
        if not api_key:
            yield f"event: error\ndata: {json.dumps({'message': 'Gemini APIキーが設定されていません。'})}\n\n"
            yield "event: done\ndata: {}\n\n"
            return

        settings = get_settings()

        # Build Gemini-formatted messages
        contents: list[dict[str, Any]] = []
        if history:
            for i, msg in enumerate(history[-10:]):
                role = "user" if msg.role == "user" else "model"
                text = msg.content
                if i == 0 and msg.role == "user":
                    text = (
                        f"[System Instructions]\n{system_prompt}\n\n[User Message]\n{msg.content}"
                    )
                contents.append({"role": role, "parts": [{"text": text}]})

        if not contents:
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": f"[System Instructions]\n{system_prompt}\n\n[User Message]\n{message}"
                        }
                    ],
                }
            )
        else:
            contents.append({"role": "user", "parts": [{"text": message}]})

        model_name = settings.gemini_chat_model
        try:
            full_text = ""
            # Accumulate function calls across stream chunks
            accumulated_fc: list[dict[str, Any]] = []

            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream(
                    "POST",
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:streamGenerateContent?key={api_key}&alt=sse",
                    headers={"Content-Type": "application/json"},
                    json={
                        "contents": contents,
                        "tools": GeminiToolAdapter.build_tools(),
                        "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
                        "generationConfig": {
                            "maxOutputTokens": 8192,
                            "temperature": 0.7,
                        },
                    },
                ) as response:
                    if response.status_code != 200:
                        error_text = (await response.aread()).decode("utf-8", errors="replace")
                        logger.error(f"Gemini API error: {response.status_code} - {error_text}")
                        yield f"event: error\ndata: {json.dumps({'message': f'Gemini APIエラー (HTTP {response.status_code})'})}\n\n"
                        yield "event: done\ndata: {}\n\n"
                        return

                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            try:
                                chunk = json.loads(data)
                                candidates = chunk.get("candidates", [])
                                if candidates:
                                    parts = candidates[0].get("content", {}).get("parts", [])
                                    for part in parts:
                                        if "text" in part:
                                            text = part["text"]
                                            if text:
                                                full_text += text
                                                yield f"event: chunk\ndata: {json.dumps({'text': text})}\n\n"
                                        if "functionCall" in part:
                                            fc = part["functionCall"]
                                            accumulated_fc.append(
                                                {
                                                    "name": fc.get("name", ""),
                                                    "arguments": fc.get("args", {}),
                                                }
                                            )
                            except json.JSONDecodeError:
                                continue

            # Execute accumulated function calls
            if accumulated_fc:
                actions = await execute_tool_calls_fn(
                    project, accumulated_fc, timeline_target=timeline_target
                )
                if actions:
                    actions_data = [
                        {"type": a.type, "description": a.description, "applied": a.applied}
                        for a in actions
                    ]
                    yield f"event: actions\ndata: {json.dumps(actions_data)}\n\n"

        except httpx.TimeoutException:
            logger.error("Gemini API timeout during streaming")
            yield f"event: error\ndata: {json.dumps({'message': 'Gemini APIがタイムアウトしました。'})}\n\n"
        except Exception as e:
            logger.exception("Gemini streaming error")
            yield f"event: error\ndata: {json.dumps({'message': f'Gemini エラー: {str(e)}'})}\n\n"
        finally:
            yield "event: done\ndata: {}\n\n"

    async def stream_anthropic(
        self,
        project: Project,
        message: str,
        history: list[ChatMessage],
        system_prompt: str,
        api_key: str,
        *,
        execute_tool_calls_fn: Any,
        timeline_target: Any | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream chat response from Anthropic with Tool-use (raw SSE)."""
        if not api_key:
            yield f"event: error\ndata: {json.dumps({'message': 'Anthropic APIキーが設定されていません。'})}\n\n"
            yield "event: done\ndata: {}\n\n"
            return

        settings = get_settings()
        messages: list[dict[str, Any]] = []
        for msg in history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": message})

        try:
            full_text = ""
            # Accumulate tool_use blocks: index -> {id, name, input_parts}
            tool_block_acc: dict[int, dict[str, Any]] = {}
            current_block_index: int = -1
            current_block_type: str = ""

            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream(
                    "POST",
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.anthropic_chat_model,
                        "max_tokens": 16384,
                        "system": system_prompt,
                        "messages": messages,
                        "stream": True,
                        "tools": AnthropicToolAdapter.build_tools(),
                        "tool_choice": {"type": "auto"},
                    },
                ) as response:
                    if response.status_code != 200:
                        error_text = (await response.aread()).decode("utf-8", errors="replace")
                        logger.error(f"Anthropic API error: {response.status_code} - {error_text}")
                        yield f"event: error\ndata: {json.dumps({'message': f'Anthropic APIエラー (HTTP {response.status_code})'})}\n\n"
                        yield "event: done\ndata: {}\n\n"
                        return

                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            try:
                                chunk = json.loads(data)
                                event_type = chunk.get("type", "")

                                if event_type == "content_block_start":
                                    block = chunk.get("content_block", {})
                                    current_block_index = chunk.get("index", -1)
                                    current_block_type = block.get("type", "")
                                    if current_block_type == "tool_use":
                                        tool_block_acc[current_block_index] = {
                                            "id": block.get("id", ""),
                                            "name": block.get("name", ""),
                                            "input_parts": [],
                                        }

                                elif event_type == "content_block_delta":
                                    delta = chunk.get("delta", {})
                                    delta_type = delta.get("type", "")
                                    if delta_type == "text_delta":
                                        text = delta.get("text", "")
                                        if text:
                                            full_text += text
                                            yield f"event: chunk\ndata: {json.dumps({'text': text})}\n\n"
                                    elif delta_type == "input_json_delta":
                                        partial = delta.get("partial_json", "")
                                        if current_block_index in tool_block_acc:
                                            tool_block_acc[current_block_index][
                                                "input_parts"
                                            ].append(partial)

                            except json.JSONDecodeError:
                                continue

            # Reassemble and execute tool calls
            if tool_block_acc:
                assembled: list[dict[str, Any]] = []
                for acc in tool_block_acc.values():
                    input_str = "".join(acc["input_parts"])
                    try:
                        arguments = json.loads(input_str) if input_str else {}
                    except json.JSONDecodeError:
                        arguments = {}
                    assembled.append({"name": acc["name"], "arguments": arguments})

                actions = await execute_tool_calls_fn(
                    project, assembled, timeline_target=timeline_target
                )
                if actions:
                    actions_data = [
                        {"type": a.type, "description": a.description, "applied": a.applied}
                        for a in actions
                    ]
                    yield f"event: actions\ndata: {json.dumps(actions_data)}\n\n"

        except httpx.TimeoutException:
            logger.error("Anthropic API timeout during streaming")
            yield f"event: error\ndata: {json.dumps({'message': 'Anthropic APIがタイムアウトしました。'})}\n\n"
        except Exception as e:
            logger.exception("Anthropic streaming error")
            yield f"event: error\ndata: {json.dumps({'message': f'Anthropic エラー: {str(e)}'})}\n\n"
        finally:
            yield "event: done\ndata: {}\n\n"

    # ------------------------------------------------------------------
    # Context / prompt builders
    # ------------------------------------------------------------------

    async def get_project_assets(self, project_id: uuid.UUID) -> list[Asset]:
        """Fetch all assets for a project."""
        result = await self.db.execute(
            select(Asset).where(Asset.project_id == project_id).order_by(Asset.type, Asset.name)
        )
        return list(result.scalars().all())

    @staticmethod
    def detect_clip_type(clip: dict[str, Any]) -> str:
        """Classify a timeline clip so AI can reason about text objects explicitly."""
        clip_type = clip.get("type")
        if isinstance(clip_type, str) and clip_type:
            return clip_type
        if clip.get("text_content") is not None:
            return "text"
        if clip.get("shape"):
            return "shape"
        if clip.get("asset_id"):
            return "asset"
        return "unknown"

    @classmethod
    def summarize_text_content(
        cls, clip: dict[str, Any], *, max_length: int | None = None
    ) -> tuple[str, str | None]:
        """Return text availability state and preview text for AI-facing responses."""
        if cls.detect_clip_type(clip) != "text":
            return "not_text", None

        raw_text = clip.get("text_content")
        if raw_text is None or not isinstance(raw_text, str):
            return "unavailable", None
        if raw_text.strip() == "":
            return "empty", ""
        if max_length is not None and len(raw_text) > max_length:
            return "present", raw_text[:max_length] + "..."
        return "present", raw_text

    @classmethod
    def summarize_text_background_for_context(cls, clip: dict[str, Any]) -> list[str]:
        """Expose text background state so AI can decide if opacity edits are meaningful."""
        if cls.detect_clip_type(clip) != "text":
            return []

        text_style = clip.get("text_style")
        if not isinstance(text_style, dict):
            return ["bg_state=unset"]

        background_color = text_style.get("backgroundColor", text_style.get("background_color"))
        background_opacity = text_style.get(
            "backgroundOpacity", text_style.get("background_opacity")
        )

        summary_parts: list[str] = []

        if isinstance(background_color, str):
            normalized_color = background_color.strip()
            if normalized_color == "" or normalized_color.lower() == "transparent":
                summary_parts.append("bg_state=none")
            else:
                summary_parts.append(f"bg_color={json.dumps(normalized_color)}")
        elif background_color is None:
            summary_parts.append("bg_state=unset")

        if isinstance(background_opacity, (int, float)):
            summary_parts.append(f"bg_opacity={float(background_opacity):.2f}")

        return summary_parts

    @classmethod
    def build_context_clip_summary(cls, clip: dict[str, Any]) -> str:
        """Serialize a clip into a compact line for the browser AI prompt."""
        clip_id = str(clip.get("id", "?"))[:8]
        clip_type = cls.detect_clip_type(clip)
        start_ms = clip.get("start_ms", 0)
        duration_ms = clip.get("duration_ms", 0)

        summary_parts = [
            f"id={clip_id}",
            f"type={clip_type}",
            f"start={start_ms}ms",
            f"dur={duration_ms}ms",
        ]

        asset_id = clip.get("asset_id")
        if asset_id:
            summary_parts.append(f"asset={str(asset_id)[:8]}")

        text_state, text_preview = cls.summarize_text_content(clip, max_length=160)
        if text_state != "not_text":
            summary_parts.append(f"text_state={text_state}")
            if text_state == "unavailable":
                summary_parts.append("text=<unavailable>")
            elif text_preview is not None:
                summary_parts.append(f"text={json.dumps(text_preview, ensure_ascii=False)}")
            summary_parts.extend(cls.summarize_text_background_for_context(clip))

        shape = clip.get("shape")
        if clip_type == "shape" and isinstance(shape, dict) and shape.get("type"):
            summary_parts.append(f"shape={shape.get('type')}")

        return "  - " + " ".join(summary_parts)

    def build_chat_context(
        self, project: Project, timeline: dict[str, Any], assets: list[Asset] | None = None
    ) -> str:
        """Build a compact timeline context string for Claude."""
        # Build assets section
        assets_info = []
        if assets:
            for asset in assets:
                assets_info.append(
                    f"  - name={_escape_user_string(asset.name)} type={asset.type} asset_id={asset.id}"
                )

        layers_info = []
        for layer in timeline.get("layers", []):
            clips = layer.get("clips", [])
            clip_summaries = [self.build_context_clip_summary(c) for c in clips]
            layers_info.append(
                f"Layer {_escape_user_string(layer.get('name', ''))} (id={layer.get('id', '')[:8]}, "
                f"clips={len(clips)}, locked={layer.get('locked', False)}):\n"
                + "\n".join(clip_summaries)
            )

        tracks_info = []
        for track in timeline.get("audio_tracks", []):
            clips = track.get("clips", [])
            clip_summaries = []
            for c in clips:
                clip_summaries.append(
                    f"  - id={c.get('id', '?')[:8]} start={c.get('start_ms', 0)}ms "
                    f"dur={c.get('duration_ms', 0)}ms"
                )
            tracks_info.append(
                f"Audio {_escape_user_string(track.get('type', ''))} (id={track.get('id', '')[:8]}, clips={len(clips)}):\n"
                + "\n".join(clip_summaries)
            )

        context_parts = [
            f"Project: {_escape_user_string(project.name)}",
            f"Duration: {project.duration_ms}ms",
            f"Resolution: {project.width}x{project.height}",
        ]

        if assets_info:
            context_parts.append("\n## Available Assets (use asset_id for operations)")
            context_parts.append("\n".join(assets_info))

        context_parts.append("\n## Video Layers")
        context_parts.append("\n".join(layers_info) if layers_info else "  (empty)")

        context_parts.append("\n## Audio Tracks")
        context_parts.append("\n".join(tracks_info) if tracks_info else "  (empty)")

        return "\n".join(context_parts)

    def build_chat_system_prompt(self, context: str) -> str:
        """Build the system prompt for the tool-use chat architecture."""
        return f"""あなたは動画編集アプリ「douga」のAIアシスタントです。
ユーザーのタイムライン編集指示を理解し、提供されたツールを使って操作を実行します。

## 現在のプロジェクト状態
{context}

## ルール
- 日本語で応答してください
- 編集操作が必要な場合は必ずツールを呼び出してください（テキストにJSONを出力しないでください）
- 情報の質問のみの場合はツール呼び出し不要です
- ユーザーの指示が曖昧な場合は確認してください

## 重要: asset_id について
- asset_id は必ず UUID 形式で指定してください（例: "6d591866-a838-46ff-a356-442b2bf2afeb"）
- ファイル名（例: "video.mp4"）は使用できません
- 上記「Available Assets」セクションからファイル名に対応する asset_id を確認してください

## 重要: テキストオブジェクトの読み方
- `type=text` の行がテキストオブジェクトです
- `text_state=present` のときだけ `text="..."` を本文として扱ってください
- `text_state=empty` は空文字、`text_state=unavailable` は取得不能です。推測で補完しないでください

## 重要: execute_operations の使い方
- 既存テキストの本文変更は `update_text` を使ってください（`delete` + `add` ではなく）
- 既存テロップの色・背景色・背景透明度の変更は `update_text_style` を使ってください
- 背景透明度の指定は 0.0-1.0 です（0%=0.0、50%=0.5、100%=1.0）
- コンテキストに `bg_state=none` や `bg_state=unset` が出ているテキストは背景が見えません
- 1つのテキストを2つに分ける場合は `split` を使い、`left_text_content` / `right_text_content` も指定できます
- `clip_id` にはコンテキストに表示された `id=` の値をそのまま使ってください（短縮・変形しないこと）
- move操作: `new_start_ms` は必須です
- add操作の `data`: layer_id, start_ms, duration_ms が必須（アセットクリップには asset_id も必須）

## レイヤー・配置の操作
- レイヤー追加: `add_layer`、削除: `delete_layer`、並べ替え: `reorder_layers`
- レイヤー名の変更だけなら `rename_layer`、表示/ロック等もまとめて変えるなら `update_layer`
- クリップを前後に隙間なく寄せる: `snap_to_previous` / `snap_to_next`
- レイヤー内のギャップを詰める: `close_gap`"""

    # ------------------------------------------------------------------
    # Response builder
    # ------------------------------------------------------------------

    @staticmethod
    def build_chat_response(assistant_text: str, actions: list[ChatAction]) -> ChatResponse:
        """Build ChatResponse from assistant text and executed actions."""
        any_applied = any(a.applied for a in actions) if actions else False
        failed = [a for a in actions if not a.applied]
        message = assistant_text.strip()
        if failed:
            descs = [a.description for a in failed]
            message = (message + "\n\n⚠️ 実行できなかった操作: " + ", ".join(descs)).strip()
        return ChatResponse(
            message=message,
            actions=actions,
            actions_applied=any_applied,
        )

    # Alias used internally from AIService
    _build_chat_response = build_chat_response
