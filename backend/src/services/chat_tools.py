"""Chat tool definitions and provider adapters for Tool-use / Function Calling.

This module provides:
- CHAT_TOOLS: typed list of shared tool definitions for all V1 operations
- OpenAIToolAdapter: converts shared defs to OpenAI Function Calling format
- AnthropicToolAdapter: converts to Anthropic Tool-use format (raw JSON / httpx)
- GeminiToolAdapter: converts to Gemini function declarations format
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Shared tool definitions (provider-agnostic)
# ---------------------------------------------------------------------------
# Each entry is a dict with:
#   name: str           -- snake_case function name
#   description: str    -- natural-language description for the LLM
#   parameters: dict    -- JSON Schema object describing the arguments
# ---------------------------------------------------------------------------

CHAT_TOOLS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # Clip operations (batch wrapper)
    # ------------------------------------------------------------------
    {
        "name": "execute_operations",
        "description": (
            "タイムライン編集操作を実行します。"
            "add/move/trim/delete/update_text/update_text_style/split/update_transform"
            "の各操作をまとめて指定できます。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "description": "実行する操作のリスト",
                    "items": {
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": [
                                    "add",
                                    "move",
                                    "trim",
                                    "delete",
                                    "update_text",
                                    "update_text_style",
                                    "split",
                                    "update_transform",
                                ],
                                "description": "操作の種別",
                            },
                            "clip_id": {
                                "type": "string",
                                "description": "対象クリップID (add以外で必須)",
                            },
                            "clip_type": {
                                "type": "string",
                                "enum": ["video", "audio"],
                                "description": "クリップ種別 (デフォルト: video)",
                            },
                            "data": {
                                "type": "object",
                                "description": (
                                    "操作データ。"
                                    "add: {layer_id, start_ms, duration_ms, asset_id?, text_content?}; "
                                    "move: {new_start_ms, new_layer_id?}; "
                                    "trim: {duration_ms}; "
                                    "update_text: {text_content}; "
                                    "update_text_style: {font_size?, color?, background_color?, background_opacity?}; "
                                    "split: {split_at_ms, left_text_content?, right_text_content?}; "
                                    "update_transform: {x?, y?, scale?, rotation?}; "
                                    "delete: 不要"
                                ),
                            },
                        },
                        "required": ["operation"],
                    },
                }
            },
            "required": ["operations"],
        },
    },
    # ------------------------------------------------------------------
    # Layer operations
    # ------------------------------------------------------------------
    {
        "name": "add_layer",
        "description": "新しいレイヤーをタイムラインに追加します。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "レイヤー名"},
                "layer_type": {
                    "type": "string",
                    "enum": ["content", "background", "avatar", "effects", "text"],
                    "description": "レイヤー種別",
                },
                "insert_at": {
                    "type": ["integer", "null"],
                    "description": "挿入位置 (0=先頭, null=先頭デフォルト)",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "update_layer",
        "description": "既存レイヤーのプロパティ(名前/表示/ロック)を更新します。",
        "parameters": {
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "更新対象レイヤーID"},
                "name": {"type": "string", "description": "新しいレイヤー名 (省略可)"},
                "visible": {"type": "boolean", "description": "表示状態 (省略可)"},
                "locked": {"type": "boolean", "description": "ロック状態 (省略可)"},
            },
            "required": ["layer_id"],
        },
    },
    {
        "name": "reorder_layers",
        "description": "レイヤーの並び順を変更します。",
        "parameters": {
            "type": "object",
            "properties": {
                "layer_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "全レイヤーIDを新しい順序で指定",
                }
            },
            "required": ["layer_ids"],
        },
    },
    {
        "name": "delete_layer",
        "description": "レイヤーを削除します。",
        "parameters": {
            "type": "object",
            "properties": {"layer_id": {"type": "string", "description": "削除対象レイヤーID"}},
            "required": ["layer_id"],
        },
    },
    # ------------------------------------------------------------------
    # Semantic / snap operations
    # ------------------------------------------------------------------
    {
        "name": "snap_to_previous",
        "description": "指定クリップを直前クリップの末尾にスナップ(隙間なく配置)します。",
        "parameters": {
            "type": "object",
            "properties": {"clip_id": {"type": "string", "description": "スナップ対象クリップID"}},
            "required": ["clip_id"],
        },
    },
    {
        "name": "snap_to_next",
        "description": "指定クリップの末尾に次クリップをスナップ(隙間なく配置)します。",
        "parameters": {
            "type": "object",
            "properties": {"clip_id": {"type": "string", "description": "基準クリップID"}},
            "required": ["clip_id"],
        },
    },
    {
        "name": "close_gap",
        "description": "指定レイヤー内の全ギャップを詰めます。",
        "parameters": {
            "type": "object",
            "properties": {"layer_id": {"type": "string", "description": "対象レイヤーID"}},
            "required": ["layer_id"],
        },
    },
]

# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------


class OpenAIToolAdapter:
    """Convert shared tool definitions to OpenAI Function Calling format."""

    @staticmethod
    def build_tools(tools: list[dict[str, Any]] = CHAT_TOOLS) -> list[dict[str, Any]]:
        """Return the list in OpenAI ``tools`` array format.

        OpenAI expects:
        [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}]
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in tools
        ]

    @staticmethod
    def parse_tool_calls(
        response_message: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Extract tool calls from an OpenAI response message dict.

        Returns a list of ``{"name": str, "arguments": dict}`` entries.
        """
        import json as _json

        raw_calls = response_message.get("tool_calls") or []
        result = []
        for tc in raw_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args_str = fn.get("arguments", "{}")
            try:
                arguments = _json.loads(args_str)
            except _json.JSONDecodeError:
                arguments = {}
            result.append({"name": name, "arguments": arguments})
        return result


class AnthropicToolAdapter:
    """Convert shared tool definitions to Anthropic Tool-use format (raw JSON)."""

    @staticmethod
    def build_tools(tools: list[dict[str, Any]] = CHAT_TOOLS) -> list[dict[str, Any]]:
        """Return the list in Anthropic ``tools`` array format.

        Anthropic expects:
        [{"name": ..., "description": ..., "input_schema": {...}}]
        """
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
            }
            for t in tools
        ]

    @staticmethod
    def parse_tool_calls(
        content_blocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Extract tool-use blocks from Anthropic content array.

        Returns a list of ``{"name": str, "arguments": dict}`` entries.
        """
        result = []
        for block in content_blocks:
            if block.get("type") == "tool_use":
                result.append(
                    {
                        "name": block.get("name", ""),
                        "arguments": block.get("input", {}),
                    }
                )
        return result

    @staticmethod
    def extract_text(content_blocks: list[dict[str, Any]]) -> str:
        """Extract concatenated text from Anthropic content blocks."""
        return "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )


class GeminiToolAdapter:
    """Convert shared tool definitions to Gemini function declarations format."""

    @staticmethod
    def build_tools(tools: list[dict[str, Any]] = CHAT_TOOLS) -> list[dict[str, Any]]:
        """Return the list in Gemini ``tools`` array format.

        Gemini expects:
        [{"functionDeclarations": [{"name": ..., "description": ..., "parameters": ...}]}]
        """
        declarations = [
            {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            }
            for t in tools
        ]
        return [{"functionDeclarations": declarations}]

    @staticmethod
    def parse_tool_calls(
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Extract function calls from Gemini candidates array.

        Returns a list of ``{"name": str, "arguments": dict}`` entries.
        """
        result: list[dict[str, Any]] = []
        if not candidates:
            return result
        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            fc = part.get("functionCall")
            if fc:
                result.append(
                    {
                        "name": fc.get("name", ""),
                        "arguments": fc.get("args", {}),
                    }
                )
        return result

    @staticmethod
    def extract_text(candidates: list[dict[str, Any]]) -> str:
        """Extract concatenated text from Gemini candidates."""
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(part.get("text", "") for part in parts if "text" in part)
