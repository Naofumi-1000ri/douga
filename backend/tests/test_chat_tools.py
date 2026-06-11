"""Unit tests for chat_tools.py provider adapters and tool definitions.

Tests cover:
- CHAT_TOOLS structure validity
- OpenAIToolAdapter: build_tools and parse_tool_calls
- AnthropicToolAdapter: build_tools, parse_tool_calls, extract_text
- GeminiToolAdapter: build_tools, parse_tool_calls, extract_text
- AIService._execute_tool_calls integration (mock LLM)
"""

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.chat_tools import (
    CHAT_TOOLS,
    AnthropicToolAdapter,
    GeminiToolAdapter,
    OpenAIToolAdapter,
)

# ---------------------------------------------------------------------------
# CHAT_TOOLS structure tests
# ---------------------------------------------------------------------------


class TestChatToolsDefinitions:
    """CHAT_TOOLS must be a valid list of tool dicts."""

    def test_all_tools_have_required_keys(self):
        for t in CHAT_TOOLS:
            assert "name" in t, f"Missing 'name' in {t}"
            assert "description" in t, f"Missing 'description' in {t}"
            assert "parameters" in t, f"Missing 'parameters' in {t}"

    def test_all_tool_names_are_unique(self):
        names = [t["name"] for t in CHAT_TOOLS]
        assert len(names) == len(set(names)), "Duplicate tool names found"

    def test_expected_tools_present(self):
        names = {t["name"] for t in CHAT_TOOLS}
        expected = {
            "execute_operations",
            "add_layer",
            "update_layer",
            "reorder_layers",
            "delete_layer",
            "snap_to_previous",
            "snap_to_next",
            "close_gap",
        }
        assert expected.issubset(names), f"Missing tools: {expected - names}"

    def test_parameters_are_json_schema_objects(self):
        for t in CHAT_TOOLS:
            params = t["parameters"]
            assert params.get("type") == "object", f"Non-object parameters in {t['name']}"
            assert "properties" in params, f"No properties in {t['name']}"


# ---------------------------------------------------------------------------
# OpenAIToolAdapter
# ---------------------------------------------------------------------------


class TestOpenAIToolAdapter:
    def test_build_tools_format(self):
        tools = OpenAIToolAdapter.build_tools()
        assert len(tools) == len(CHAT_TOOLS)
        for tool in tools:
            assert tool["type"] == "function"
            fn = tool["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_build_tools_names_match(self):
        tools = OpenAIToolAdapter.build_tools()
        adapter_names = [t["function"]["name"] for t in tools]
        original_names = [t["name"] for t in CHAT_TOOLS]
        assert adapter_names == original_names

    def test_parse_tool_calls_empty(self):
        result = OpenAIToolAdapter.parse_tool_calls({})
        assert result == []

    def test_parse_tool_calls_single(self):
        msg = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {
                        "name": "add_layer",
                        "arguments": '{"name": "テキスト", "layer_type": "text"}',
                    },
                }
            ],
        }
        result = OpenAIToolAdapter.parse_tool_calls(msg)
        assert len(result) == 1
        assert result[0]["name"] == "add_layer"
        assert result[0]["arguments"] == {"name": "テキスト", "layer_type": "text"}

    def test_parse_tool_calls_multiple(self):
        msg = {
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {
                        "name": "execute_operations",
                        "arguments": '{"operations": []}',
                    },
                },
                {
                    "id": "c2",
                    "function": {
                        "name": "snap_to_previous",
                        "arguments": '{"clip_id": "abc"}',
                    },
                },
            ]
        }
        result = OpenAIToolAdapter.parse_tool_calls(msg)
        assert len(result) == 2
        assert result[0]["name"] == "execute_operations"
        assert result[1]["name"] == "snap_to_previous"

    def test_parse_tool_calls_invalid_json(self):
        """Invalid JSON in arguments should return empty dict without raising."""
        msg = {
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {
                        "name": "add_layer",
                        "arguments": "NOT_JSON",
                    },
                }
            ]
        }
        result = OpenAIToolAdapter.parse_tool_calls(msg)
        assert result[0]["arguments"] == {}


# ---------------------------------------------------------------------------
# AnthropicToolAdapter
# ---------------------------------------------------------------------------


class TestAnthropicToolAdapter:
    def test_build_tools_format(self):
        tools = AnthropicToolAdapter.build_tools()
        assert len(tools) == len(CHAT_TOOLS)
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool

    def test_build_tools_uses_input_schema_key(self):
        """Anthropic requires 'input_schema' not 'parameters'."""
        tools = AnthropicToolAdapter.build_tools()
        for tool in tools:
            assert "input_schema" in tool
            assert "parameters" not in tool

    def test_parse_tool_calls_empty(self):
        result = AnthropicToolAdapter.parse_tool_calls([])
        assert result == []

    def test_parse_tool_calls_text_block_only(self):
        blocks = [{"type": "text", "text": "操作します。"}]
        result = AnthropicToolAdapter.parse_tool_calls(blocks)
        assert result == []

    def test_parse_tool_calls_tool_use_block(self):
        blocks = [
            {"type": "text", "text": "レイヤーを追加します。"},
            {
                "type": "tool_use",
                "id": "toolu_abc",
                "name": "add_layer",
                "input": {"name": "テスト", "layer_type": "content"},
            },
        ]
        result = AnthropicToolAdapter.parse_tool_calls(blocks)
        assert len(result) == 1
        assert result[0]["name"] == "add_layer"
        assert result[0]["arguments"] == {"name": "テスト", "layer_type": "content"}

    def test_extract_text_from_blocks(self):
        blocks = [
            {"type": "text", "text": "Hello "},
            {"type": "tool_use", "id": "x", "name": "add_layer", "input": {}},
            {"type": "text", "text": "world"},
        ]
        text = AnthropicToolAdapter.extract_text(blocks)
        assert text == "Hello world"

    def test_extract_text_empty(self):
        assert AnthropicToolAdapter.extract_text([]) == ""


# ---------------------------------------------------------------------------
# GeminiToolAdapter
# ---------------------------------------------------------------------------


class TestGeminiToolAdapter:
    def test_build_tools_format(self):
        tools = GeminiToolAdapter.build_tools()
        # Gemini wraps in functionDeclarations array
        assert len(tools) == 1
        assert "functionDeclarations" in tools[0]
        decls = tools[0]["functionDeclarations"]
        assert len(decls) == len(CHAT_TOOLS)
        for d in decls:
            assert "name" in d
            assert "description" in d
            assert "parameters" in d

    def test_build_tools_names_match(self):
        tools = GeminiToolAdapter.build_tools()
        decl_names = [d["name"] for d in tools[0]["functionDeclarations"]]
        orig_names = [t["name"] for t in CHAT_TOOLS]
        assert decl_names == orig_names

    def test_parse_tool_calls_empty_candidates(self):
        result = GeminiToolAdapter.parse_tool_calls([])
        assert result == []

    def test_parse_tool_calls_text_only(self):
        candidates = [{"content": {"parts": [{"text": "テキスト応答"}]}}]
        result = GeminiToolAdapter.parse_tool_calls(candidates)
        assert result == []

    def test_parse_tool_calls_function_call(self):
        candidates = [
            {
                "content": {
                    "parts": [
                        {
                            "functionCall": {
                                "name": "delete_layer",
                                "args": {"layer_id": "layer-001"},
                            }
                        }
                    ]
                }
            }
        ]
        result = GeminiToolAdapter.parse_tool_calls(candidates)
        assert len(result) == 1
        assert result[0]["name"] == "delete_layer"
        assert result[0]["arguments"] == {"layer_id": "layer-001"}

    def test_parse_tool_calls_mixed_parts(self):
        candidates = [
            {
                "content": {
                    "parts": [
                        {"text": "操作します"},
                        {
                            "functionCall": {
                                "name": "close_gap",
                                "args": {"layer_id": "layer-bg"},
                            }
                        },
                    ]
                }
            }
        ]
        result = GeminiToolAdapter.parse_tool_calls(candidates)
        assert len(result) == 1
        assert result[0]["name"] == "close_gap"

    def test_extract_text_from_candidates(self):
        candidates = [
            {
                "content": {
                    "parts": [
                        {"text": "Hello "},
                        {"functionCall": {"name": "close_gap", "args": {}}},
                        {"text": "world"},
                    ]
                }
            }
        ]
        text = GeminiToolAdapter.extract_text(candidates)
        assert text == "Hello world"

    def test_extract_text_empty(self):
        assert GeminiToolAdapter.extract_text([]) == ""


# ---------------------------------------------------------------------------
# AIService._execute_tool_calls integration with mocked DB
# ---------------------------------------------------------------------------


def _make_project_mock(timeline_data: dict | None = None) -> MagicMock:
    project = MagicMock()
    project.id = uuid.uuid4()
    project.name = "テストプロジェクト"
    project.duration_ms = 60000
    project.width = 1920
    project.height = 1080
    project.timeline_data = timeline_data or {
        "layers": [
            {
                "id": "layer-001",
                "name": "コンテンツ",
                "type": "content",
                "locked": False,
                "visible": True,
                "clips": [],
            }
        ],
        "audio_tracks": [],
    }
    return project


@pytest.fixture
def ai_service_mock():
    """AIService instance with fully mocked DB session."""
    from src.services.ai_service import AIService

    db = AsyncMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()

    # Make db.execute return an empty scalars result by default
    scalars_result = MagicMock()
    scalars_result.all.return_value = []
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result
    db.execute.return_value = execute_result

    return AIService(db)


class TestExecuteToolCallsDispatch:
    """_execute_tool_calls should dispatch each tool name to the correct handler."""

    @pytest.mark.asyncio
    async def test_add_layer_dispatches(self, ai_service_mock):
        project = _make_project_mock()

        # Patch add_layer to succeed
        result_layer = MagicMock()
        result_layer.name = "新しいレイヤー"
        result_layer.id = "layer-new-001"
        ai_service_mock.add_layer = AsyncMock(return_value=result_layer)

        tool_calls = [{"name": "add_layer", "arguments": {"name": "新しいレイヤー"}}]
        actions = await ai_service_mock._execute_tool_calls(project, tool_calls)

        assert len(actions) == 1
        assert actions[0].applied is True
        assert "新しいレイヤー" in actions[0].description
        ai_service_mock.add_layer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_snap_to_previous_dispatches(self, ai_service_mock):
        project = _make_project_mock()

        sem_result = MagicMock()
        sem_result.success = True
        sem_result.changes_made = ["クリップをスナップしました"]
        ai_service_mock.execute_semantic_operation = AsyncMock(return_value=sem_result)

        tool_calls = [{"name": "snap_to_previous", "arguments": {"clip_id": "clip-001"}}]
        actions = await ai_service_mock._execute_tool_calls(project, tool_calls)

        assert len(actions) == 1
        assert actions[0].applied is True
        assert actions[0].type == "snap_to_previous"

    @pytest.mark.asyncio
    async def test_snap_to_next_dispatches(self, ai_service_mock):
        project = _make_project_mock()

        sem_result = MagicMock()
        sem_result.success = True
        sem_result.changes_made = ["次クリップをスナップしました"]
        ai_service_mock.execute_semantic_operation = AsyncMock(return_value=sem_result)

        tool_calls = [{"name": "snap_to_next", "arguments": {"clip_id": "clip-002"}}]
        actions = await ai_service_mock._execute_tool_calls(project, tool_calls)

        assert len(actions) == 1
        assert actions[0].applied is True
        assert actions[0].type == "snap_to_next"

    @pytest.mark.asyncio
    async def test_close_gap_dispatches(self, ai_service_mock):
        project = _make_project_mock()

        sem_result = MagicMock()
        sem_result.success = True
        sem_result.changes_made = ["ギャップを詰めました"]
        ai_service_mock.execute_semantic_operation = AsyncMock(return_value=sem_result)

        tool_calls = [{"name": "close_gap", "arguments": {"layer_id": "layer-001"}}]
        actions = await ai_service_mock._execute_tool_calls(project, tool_calls)

        assert len(actions) == 1
        assert actions[0].applied is True
        assert actions[0].type == "close_gap"

    @pytest.mark.asyncio
    async def test_rename_layer_dispatches_to_semantic_op(self, ai_service_mock):
        """rename_layer must dispatch to execute_semantic_operation with name in parameters."""
        project = _make_project_mock()

        sem_result = MagicMock()
        sem_result.success = True
        sem_result.changes_made = ["Renamed layer from 'old' to 'new'"]
        ai_service_mock.execute_semantic_operation = AsyncMock(return_value=sem_result)

        tool_calls = [
            {"name": "rename_layer", "arguments": {"layer_id": "layer-001", "name": "new"}}
        ]
        actions = await ai_service_mock._execute_tool_calls(project, tool_calls)

        assert len(actions) == 1
        assert actions[0].applied is True
        assert actions[0].type == "rename_layer"

        # Verify the SemanticOperation was constructed correctly
        call_args = ai_service_mock.execute_semantic_operation.await_args
        sem_op = call_args.args[1]
        assert sem_op.operation == "rename_layer"
        assert sem_op.target_layer_id == "layer-001"
        assert sem_op.parameters == {"name": "new"}

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_not_applied(self, ai_service_mock):
        project = _make_project_mock()

        tool_calls = [{"name": "non_existent_tool", "arguments": {}}]
        actions = await ai_service_mock._execute_tool_calls(project, tool_calls)

        assert len(actions) == 1
        assert actions[0].applied is False
        assert "non_existent_tool" in actions[0].description

    @pytest.mark.asyncio
    async def test_execute_operations_empty_list(self, ai_service_mock):
        project = _make_project_mock()

        tool_calls = [{"name": "execute_operations", "arguments": {"operations": []}}]
        actions = await ai_service_mock._execute_tool_calls(project, tool_calls)

        assert len(actions) == 1
        assert actions[0].applied is False

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_processed(self, ai_service_mock):
        project = _make_project_mock()

        layer1 = MagicMock()
        layer1.name = "レイヤー1"
        layer1.id = "layer-new-1"
        layer2 = MagicMock()
        layer2.name = "レイヤー2"
        layer2.id = "layer-new-2"
        ai_service_mock.add_layer = AsyncMock(side_effect=[layer1, layer2])

        tool_calls = [
            {"name": "add_layer", "arguments": {"name": "レイヤー1"}},
            {"name": "add_layer", "arguments": {"name": "レイヤー2"}},
        ]
        actions = await ai_service_mock._execute_tool_calls(project, tool_calls)

        assert len(actions) == 2
        assert all(a.applied for a in actions)

    @pytest.mark.asyncio
    async def test_execute_operations_wraps_real_ops_into_batch(self, ai_service_mock):
        """execute_operations must convert each op into a BatchClipOperation and run a batch.

        Verifies the double-wrapping (tool args -> BatchClipOperation -> {"type": "batch"})
        passes operation fields through to execute_batch_operations intact.
        """
        from src.schemas.ai import BatchOperationResult

        project = _make_project_mock()

        captured: dict[str, Any] = {}

        async def fake_execute_batch(_project, batch_ops):
            captured["batch_ops"] = batch_ops
            return BatchOperationResult(
                success=True,
                total_operations=len(batch_ops),
                successful_operations=len(batch_ops),
                failed_operations=0,
            )

        ai_service_mock.execute_batch_operations = AsyncMock(side_effect=fake_execute_batch)

        full_clip_id = str(uuid.uuid4())
        tool_calls = [
            {
                "name": "execute_operations",
                "arguments": {
                    "operations": [
                        {
                            "operation": "move",
                            "clip_id": full_clip_id,
                            "clip_type": "video",
                            "data": {"new_start_ms": 5000},
                        },
                        {
                            "operation": "update_text",
                            "clip_id": "clip-text-1",
                            "data": {"text_content": "新しいテロップ"},
                        },
                    ]
                },
            }
        ]

        actions = await ai_service_mock._execute_tool_calls(project, tool_calls)

        # The batch executed successfully
        assert len(actions) == 1
        assert actions[0].applied is True
        assert actions[0].type == "batch"

        # Verify the real ops survived the double-wrapping
        batch_ops = captured["batch_ops"]
        assert len(batch_ops) == 2

        move_op = batch_ops[0]
        assert move_op.operation == "move"
        assert move_op.clip_id == full_clip_id
        assert move_op.clip_type == "video"
        assert move_op.data == {"new_start_ms": 5000}

        text_op = batch_ops[1]
        assert text_op.operation == "update_text"
        assert text_op.clip_id == "clip-text-1"
        # clip_type defaults to "video" when omitted
        assert text_op.clip_type == "video"
        assert text_op.data == {"text_content": "新しいテロップ"}


class TestBuildChatResponse:
    """_build_chat_response should build a proper ChatResponse."""

    def test_no_actions(self):
        from src.services.ai_service import AIService

        resp = AIService._build_chat_response("hello", [])
        assert resp.message == "hello"
        assert resp.actions == []
        assert resp.actions_applied is False

    def test_with_applied_action(self):
        from src.schemas.ai import ChatAction
        from src.services.ai_service import AIService

        actions = [ChatAction(type="add_layer", description="追加", applied=True)]
        resp = AIService._build_chat_response("操作しました", actions)
        assert resp.actions_applied is True
        assert "⚠️" not in resp.message

    def test_failed_action_appends_warning(self):
        from src.schemas.ai import ChatAction
        from src.services.ai_service import AIService

        actions = [ChatAction(type="add_layer", description="失敗した操作", applied=False)]
        resp = AIService._build_chat_response("処理しました", actions)
        assert "⚠️" in resp.message
        assert "失敗した操作" in resp.message
        assert resp.actions_applied is False


# ---------------------------------------------------------------------------
# Streaming helpers: mock httpx SSE stream
# ---------------------------------------------------------------------------


class _FakeStreamResponse:
    """Mimics an httpx streaming response yielding SSE lines via aiter_lines()."""

    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self.status_code = status_code

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return b"error body"


class _FakeStreamCM:
    """Async context manager returned by client.stream(...)."""

    def __init__(self, response: _FakeStreamResponse):
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._response

    async def __aexit__(self, *args) -> None:
        return None


def _make_streaming_client(sse_lines: list[str], status_code: int = 200):
    """Build a mock object usable as ``httpx.AsyncClient(...)`` for streaming.

    The returned object supports ``async with`` and exposes ``.stream(...)`` which
    returns an async context manager yielding the given SSE lines.
    """
    response = _FakeStreamResponse(sse_lines, status_code=status_code)

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.stream = MagicMock(return_value=_FakeStreamCM(response))
    return client


async def _collect_events(agen) -> list[str]:
    """Drain an async generator of SSE event strings into a list."""
    events = []
    async for ev in agen:
        events.append(ev)
    return events


def _parse_actions_event(events: list[str]) -> list[dict] | None:
    """Find and JSON-decode the ``event: actions`` payload, if present."""
    for ev in events:
        if ev.startswith("event: actions"):
            # format: "event: actions\ndata: <json>\n\n"
            data_line = ev.split("data: ", 1)[1].strip()
            return json.loads(data_line)
    return None


class TestStreamingToolCalls:
    """Streaming providers must accumulate tool-call deltas and execute them."""

    # -- OpenAI ------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_stream_openai_accumulates_split_tool_call(self, ai_service_mock):
        """OpenAI streams tool_call name + arguments across multiple deltas."""
        project = _make_project_mock()

        captured: dict[str, Any] = {}

        async def fake_exec(_project, tool_calls, *, timeline_target=None):
            from src.schemas.ai import ChatAction

            captured["tool_calls"] = tool_calls
            return [ChatAction(type="add_layer", description="追加しました", applied=True)]

        ai_service_mock._execute_tool_calls = AsyncMock(side_effect=fake_exec)

        # arguments are split across two deltas to test reassembly
        sse_lines = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
            '"function":{"name":"add_layer","arguments":"{\\"name\\":"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":"\\"テロップ\\"}"}}]}}]}',
            "data: [DONE]",
        ]
        client = _make_streaming_client(sse_lines)

        with patch("src.services.ai_service.httpx.AsyncClient", return_value=client):
            events = await _collect_events(
                ai_service_mock._stream_openai(project, "msg", [], "sys", "fake-key")
            )

        # Tool call was reassembled correctly
        assert captured["tool_calls"] == [{"name": "add_layer", "arguments": {"name": "テロップ"}}]
        # An actions event was emitted
        actions_payload = _parse_actions_event(events)
        assert actions_payload is not None
        assert actions_payload[0]["type"] == "add_layer"
        assert actions_payload[0]["applied"] is True
        # Stream always ends with done
        assert events[-1].startswith("event: done")

    @pytest.mark.asyncio
    async def test_stream_openai_text_only_no_actions(self, ai_service_mock):
        """Plain text streaming (no tool_calls) must not emit an actions event."""
        project = _make_project_mock()
        ai_service_mock._execute_tool_calls = AsyncMock()

        sse_lines = [
            'data: {"choices":[{"delta":{"content":"こんにちは"}}]}',
            'data: {"choices":[{"delta":{"content":"です"}}]}',
            "data: [DONE]",
        ]
        client = _make_streaming_client(sse_lines)

        with patch("src.services.ai_service.httpx.AsyncClient", return_value=client):
            events = await _collect_events(
                ai_service_mock._stream_openai(project, "msg", [], "sys", "fake-key")
            )

        # No tool calls executed
        ai_service_mock._execute_tool_calls.assert_not_awaited()
        assert _parse_actions_event(events) is None
        # Text chunks were streamed
        chunk_events = [e for e in events if e.startswith("event: chunk")]
        assert len(chunk_events) == 2

    @pytest.mark.asyncio
    async def test_stream_openai_missing_api_key(self, ai_service_mock):
        """No API key yields an error event and done."""
        project = _make_project_mock()
        events = await _collect_events(
            ai_service_mock._stream_openai(project, "msg", [], "sys", "")
        )
        assert any(e.startswith("event: error") for e in events)
        assert events[-1].startswith("event: done")

    # -- Anthropic ---------------------------------------------------------

    @pytest.mark.asyncio
    async def test_stream_anthropic_accumulates_tool_use(self, ai_service_mock):
        """Anthropic streams a tool_use block with input_json_delta partials."""
        project = _make_project_mock()

        captured: dict[str, Any] = {}

        async def fake_exec(_project, tool_calls, *, timeline_target=None):
            from src.schemas.ai import ChatAction

            captured["tool_calls"] = tool_calls
            return [ChatAction(type="close_gap", description="詰めました", applied=True)]

        ai_service_mock._execute_tool_calls = AsyncMock(side_effect=fake_exec)

        sse_lines = [
            'data: {"type":"content_block_start","index":0,'
            '"content_block":{"type":"tool_use","id":"toolu_1","name":"close_gap"}}',
            'data: {"type":"content_block_delta","index":0,'
            '"delta":{"type":"input_json_delta","partial_json":"{\\"layer_id\\":"}}',
            'data: {"type":"content_block_delta","index":0,'
            '"delta":{"type":"input_json_delta","partial_json":"\\"layer-001\\"}"}}',
            'data: {"type":"content_block_stop","index":0}',
        ]
        client = _make_streaming_client(sse_lines)

        with patch("src.services.ai_service.httpx.AsyncClient", return_value=client):
            events = await _collect_events(
                ai_service_mock._stream_anthropic(project, "msg", [], "sys", "fake-key")
            )

        assert captured["tool_calls"] == [
            {"name": "close_gap", "arguments": {"layer_id": "layer-001"}}
        ]
        actions_payload = _parse_actions_event(events)
        assert actions_payload is not None
        assert actions_payload[0]["type"] == "close_gap"
        assert events[-1].startswith("event: done")

    @pytest.mark.asyncio
    async def test_stream_anthropic_text_delta_only(self, ai_service_mock):
        """Anthropic text_delta streaming must emit chunks and no actions."""
        project = _make_project_mock()
        ai_service_mock._execute_tool_calls = AsyncMock()

        sse_lines = [
            'data: {"type":"content_block_delta","index":0,'
            '"delta":{"type":"text_delta","text":"処理"}}',
            'data: {"type":"content_block_delta","index":0,'
            '"delta":{"type":"text_delta","text":"します"}}',
        ]
        client = _make_streaming_client(sse_lines)

        with patch("src.services.ai_service.httpx.AsyncClient", return_value=client):
            events = await _collect_events(
                ai_service_mock._stream_anthropic(project, "msg", [], "sys", "fake-key")
            )

        ai_service_mock._execute_tool_calls.assert_not_awaited()
        assert _parse_actions_event(events) is None
        chunk_events = [e for e in events if e.startswith("event: chunk")]
        assert len(chunk_events) == 2

    # -- Gemini ------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_stream_gemini_accumulates_function_call(self, ai_service_mock):
        """Gemini streams a functionCall part that must be executed."""
        project = _make_project_mock()

        captured: dict[str, Any] = {}

        async def fake_exec(_project, tool_calls, *, timeline_target=None):
            from src.schemas.ai import ChatAction

            captured["tool_calls"] = tool_calls
            return [ChatAction(type="rename_layer", description="改名しました", applied=True)]

        ai_service_mock._execute_tool_calls = AsyncMock(side_effect=fake_exec)

        sse_lines = [
            'data: {"candidates":[{"content":{"parts":[{"functionCall":'
            '{"name":"rename_layer","args":{"layer_id":"layer-001","name":"新名称"}}}]}}]}',
        ]
        client = _make_streaming_client(sse_lines)

        with patch("src.services.ai_service.httpx.AsyncClient", return_value=client):
            events = await _collect_events(
                ai_service_mock._stream_gemini(project, "msg", [], "sys", "fake-key")
            )

        assert captured["tool_calls"] == [
            {
                "name": "rename_layer",
                "arguments": {"layer_id": "layer-001", "name": "新名称"},
            }
        ]
        actions_payload = _parse_actions_event(events)
        assert actions_payload is not None
        assert actions_payload[0]["type"] == "rename_layer"
        assert events[-1].startswith("event: done")

    @pytest.mark.asyncio
    async def test_stream_gemini_text_and_function_call_mixed(self, ai_service_mock):
        """Gemini may emit text chunks and a function call in the same stream."""
        project = _make_project_mock()

        captured: dict[str, Any] = {}

        async def fake_exec(_project, tool_calls, *, timeline_target=None):
            from src.schemas.ai import ChatAction

            captured["tool_calls"] = tool_calls
            return [ChatAction(type="close_gap", description="詰めました", applied=True)]

        ai_service_mock._execute_tool_calls = AsyncMock(side_effect=fake_exec)

        sse_lines = [
            'data: {"candidates":[{"content":{"parts":[{"text":"ギャップを詰めます"}]}}]}',
            'data: {"candidates":[{"content":{"parts":[{"functionCall":'
            '{"name":"close_gap","args":{"layer_id":"layer-bg"}}}]}}]}',
        ]
        client = _make_streaming_client(sse_lines)

        with patch("src.services.ai_service.httpx.AsyncClient", return_value=client):
            events = await _collect_events(
                ai_service_mock._stream_gemini(project, "msg", [], "sys", "fake-key")
            )

        # Both a text chunk and a function call were handled
        chunk_events = [e for e in events if e.startswith("event: chunk")]
        assert len(chunk_events) == 1
        assert captured["tool_calls"][0]["name"] == "close_gap"
        assert _parse_actions_event(events) is not None
