"""MCP Tools for Douga Video Editor.

ツールは現在 server.py にインラインで定義されています。
このモジュールは将来的にツールを分割する場合の拡張用です。

拡張例:
    # tools/my_tools.py
    from src.mcp.server import mcp_server, _call_api, _format_response

    @mcp_server.tool()
    async def my_custom_tool(project_id: str) -> str:
        '''カスタムツールの実装'''
        result = await _call_api("GET", f"/api/ai/project/{project_id}/custom")
        return _format_response(result)

    # server.py でインポート
    from src.mcp.tools import my_tools  # noqa: F401
"""
