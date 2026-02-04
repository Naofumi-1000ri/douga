# AI v1 API 実装ステータス

**最終更新**: 2026-02-04
**最新コミット**: `eb5a6f5` - Priority 4 完了 + レビュー修正
**作業中**: Priority 5 (Advanced) 実装完了

## 概要

AI-Friendly API仕様に準拠したv1 APIの実装。薄いラッパーパターンで既存ai_serviceを再利用。

## 実装済み (✅)

### Phase 0: Contract Foundation
- [x] Envelope response format (`{request_id, data, meta}`)
- [x] `schemas/envelope.py` - EnvelopeResponse, ResponseMeta, ErrorInfo
- [x] `schemas/options.py` - OperationOptions (validate_only)
- [x] `middleware/request_context.py` - request_id, timing, header validation
- [x] Idempotency-Key必須 (validate_only=false時)
- [x] If-Match ソフト強制 (欠如→warnings、不一致→409)

### Phase 1: validate_only + Structured Errors
- [x] `constants/error_codes.py` - ERROR_CODES辞書
- [x] `exceptions.py` - DougaError例外クラス群
- [x] `services/validation_service.py` - dry-run検証ロジック
- [x] would_affect metrics (clips_created/modified/deleted, duration_change_ms, layers_affected)

### Schema Adapter (Option 2)
- [x] `schemas/clip_adapter.py`:
  - UnifiedClipInput (flat/nested両対応)
  - UnifiedMoveClipInput
  - UnifiedTransformInput (flat/nested両対応)
- [x] Flat takes precedence in mixed format
- [x] Conversion warnings (non-uniform scale, unsupported fields)
- [x] TextStyle extra="forbid" for unknown key passthrough

### Priority 1: Core Clip Operations (✅ Committed: 74087eb)
- [x] `POST /projects/{id}/clips` - クリップ追加
- [x] `PATCH /projects/{id}/clips/{clip_id}/move` - クリップ移動
- [x] `PATCH /projects/{id}/clips/{clip_id}/transform` - トランスフォーム更新
- [x] `DELETE /projects/{id}/clips/{clip_id}` - クリップ削除

### Priority 2: Layer Management (✅ Committed: 4347f00)
- [x] `POST /projects/{id}/layers` - レイヤー追加
- [x] `PATCH /projects/{id}/layers/{layer_id}` - レイヤー更新
- [x] `PUT /projects/{id}/layers/order` - レイヤー並び替え

### Priority 3: Audio Operations (✅ Committed: 34c0a80)
- [x] `POST /projects/{id}/audio-clips` - オーディオクリップ追加
- [x] `PATCH /projects/{id}/audio-clips/{clip_id}/move` - オーディオ移動
- [x] `DELETE /projects/{id}/audio-clips/{clip_id}` - オーディオ削除
- [x] `POST /projects/{id}/audio-tracks` - オーディオトラック追加

### Priority 4: Markers (✅ Committed: 265bd39)
- [x] `POST /projects/{id}/markers` - マーカー追加
- [x] `PATCH /projects/{id}/markers/{marker_id}` - マーカー更新
- [x] `DELETE /projects/{id}/markers/{marker_id}` - マーカー削除

### Read Endpoints
- [x] `GET /capabilities` - API機能一覧
- [x] `GET /version` - バージョン情報
- [x] `GET /projects/{id}/overview` (alias: /summary)
- [x] `GET /projects/{id}/structure`
- [x] `GET /projects/{id}/assets`

### Priority 5: Advanced (✅ 実装完了)
- [x] `GET /projects/{id}/clips/{clip_id}` - 単一クリップ詳細
- [x] `GET /projects/{id}/at-time/{ms}` - 特定時刻のタイムライン状態
- [x] `POST /projects/{id}/batch` - バッチ操作
- [x] `POST /projects/{id}/semantic` - セマンティック操作

## 未実装 (⏳)

### Phase 2+3: diff + rollback + history
- [ ] ProjectOperation DBモデル
- [ ] operation_id + rollback_available
- [ ] `POST /operations/{id}/rollback`
- [ ] `GET /history`

## 重要ファイル

| ファイル | 役割 |
|----------|------|
| `src/api/ai_v1.py` | v1ルーター (薄いラッパー) |
| `src/schemas/clip_adapter.py` | Unified入力アダプター |
| `src/schemas/envelope.py` | Envelope/Meta/Error schemas |
| `src/schemas/options.py` | OperationOptions |
| `src/services/ai_service.py` | コアサービス (DougaError対応済み) |
| `src/services/validation_service.py` | validate_only検証ロジック |
| `src/middleware/request_context.py` | request_id, warnings管理 |
| `src/constants/error_codes.py` | ERROR_CODES辞書 |
| `src/exceptions.py` | DougaError例外クラス |
| `tests/test_ai_v1_api.py` | v1 APIテスト (134 passing) |

## 設計原則

1. **Envelope統一**: 成功/失敗とも `{request_id, data|error, meta}` 形式
2. **Flat優先**: mixed format (flat + nested) では flat values が優先
3. **Warnings伝播**: 変換警告は `meta.warnings` に含まれる
4. **Error一貫性**: validate_only と apply で同じエラーコード
5. **薄いラッパー**: v1は検証→既存service呼び出し→Envelope化のみ
6. **部分更新安全**: nested transformで明示的に指定されたフィールドのみ適用 (model_fields_set使用)
7. **ID一致性**: validate_only と apply で同一のID照合ロジック (単方向prefix)
8. **セマンティック操作**: snap_to_previous, snap_to_next, close_gap, auto_duck_bgm, rename_layer
9. **バッチ操作**: add, move, trim, update_transform, update_effects, delete, update_layer

## テスト実行

```bash
cd backend

# 全テスト (DB不要)
pytest tests/test_ai_v1_api.py -v -m "not requires_db"

# ClipAdapterテストのみ
pytest tests/test_ai_v1_api.py::TestClipAdapter -v

# Priority 1関連テスト
pytest tests/test_ai_v1_api.py::TestMoveClipAdapter -v
pytest tests/test_ai_v1_api.py::TestTransformClipAdapter -v
pytest tests/test_ai_v1_api.py::TestV1RequestModels -v
```

## 次のステップ

1. **Phase 2+3** - diff/rollback/history (operation_id統合)
   - ProjectOperation DBモデル
   - operation_id + rollback_available
   - `POST /operations/{id}/rollback`
   - `GET /history`

## コミット履歴

| Hash | Description |
|------|-------------|
| `eb5a6f5` | fix(api): DELETE /markers validate_only via body |
| `3d3f18d` | fix(api): Priority 4 marker review fixes |
| `265bd39` | feat(api): Add v1 Priority 4 marker endpoints |
| `2357ff4` | fix(api): Add asset ownership check in validate_add_audio_clip |
| `fadf78d` | fix(api): Address Priority 3 expert review findings |
| `a5e9055` | fix(api): Priority 3 review fixes |
| `34c0a80` | feat(api): Add v1 Priority 3 audio endpoints |
| `1a59c53` | fix(api): Priority 2 review fixes (context param, partial ID, If-Match) |
| `8204f9a` | fix(test): close TestClient and dispose engines on shutdown |
| `4347f00` | feat(api): Add v1 Priority 2 layer endpoints |
| `c0f50a4` | fix(api): Handle partial nested position axes correctly |
| `d62ae12` | fix(api): Priority 1 review fixes (partial transform, ID matching, rotation) |
| `74087eb` | feat(api): Add v1 Priority 1 endpoints (move/transform/delete clip) |
| `4ea0093` | fix(api): Make flat values actually take precedence in mixed format |
| `ad30543` | feat(api): Add conversion warnings for unsupported nested fields |
| `b1f8831` | feat(api): Add unified schema adapter for flat/nested clip formats |
| `a067433` | fix(api): Use DougaError in AIService for consistent error codes |
| (earlier) | Phase 0 + Phase 1 foundation |

## 参照ドキュメント

- `/Users/hgs/.claude/plans/hashed-wondering-fog.md` - 詳細実装計画
- `/Users/hgs/devel/douga_root/ai-friendly-docs/CLAUDE.md` - プロジェクト概要
