"""MCP (Model Context Protocol) Server for Douga Video Editor.

このモジュールは、Claude等のAIアシスタントがDouga動画編集APIと
対話するためのMCPサーバーを提供します。

階層的データアクセスパターン（L1→L2→L3）により、トークン効率を
最適化しながらプロジェクトデータを取得・操作できます。

Requirements:
    pip install mcp[cli] httpx

Environment Variables:
    DOUGA_API_URL: バックエンドAPIのURL（デフォルト: http://localhost:8000）
    DOUGA_API_KEY: API認証キー（推奨）
    DOUGA_API_TOKEN: Firebaseトークン（レガシー）

Usage:
    # スタンドアロンサーバーとして起動
    python -m src.mcp.server

    # MCP CLIで起動
    mcp run src.mcp.server:mcp_server

    # 他のモジュールからインポート
    from src.mcp import mcp_server

See Also:
    - src/mcp/README.md: 詳細なドキュメント
    - src/mcp/server.py: サーバー実装とツール定義
"""

try:
    from src.mcp.server import mcp_server
except ImportError:
    mcp_server = None

__all__ = ["mcp_server"]
