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
