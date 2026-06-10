# douga-mcp — DEPRECATED

> **このパッケージは廃止予定です。**
> Issue #279 による MCP 二重実装一本化のため、このパッケージは非推奨になりました。

## 移行先

**`backend/src/mcp/server.py` を使用してください。**

### 変更点

| 廃止パッケージ (`douga-mcp`) | 移行先 (`backend/src/mcp/server.py`) |
|---|---|
| `edit_timeline` (GET→PUT 直書き込み、楽観ロックなし) | `move_clip` / `update_clip_transform` / `update_clip_effects` / `delete_clip` 等の個別クリップ操作 (V1 API 経由、ETag による 409 競合検出あり) |
| 全ツール (旧 API) | 全ツール (V1 API `/api/ai/v1/...` 経由、Idempotency-Key 自動付与) |

### claude_desktop_config.json の更新

```json
{
  "mcpServers": {
    "douga": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/douga/backend", "python", "-m", "src.mcp.server"],
      "env": {
        "DOUGA_API_URL": "http://localhost:8000",
        "DOUGA_API_KEY": "douga_sk_..."
      }
    }
  }
}
```

### アーカイブ

このパッケージのソースコードは `_archive/douga-mcp/` に保管されています（git 履歴あり）。

---

**廃止日**: 2026-06-11
**関連 Issue**: #279
**ADR**: `docs/ops/adr/0001-mcp-consolidation-v1-migration.md`
