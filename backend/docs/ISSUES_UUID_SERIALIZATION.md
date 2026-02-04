# UUID シリアライゼーション問題

## 問題概要

v1 API の operation recording において、JSONB フィールドに UUID オブジェクトが直接格納されると `TypeError: Object of type UUID is not JSON serializable` エラーが発生する。

## 影響範囲

以下の JSONB フィールドに影響:
- `affected_clips` / `affected_layers` / `affected_audio_clips`
- `request_summary` - リクエスト要約（key_params含む）
- `result_summary` - 結果要約
- `rollback_data` - ロールバック用データ
- `diff` - 変更前後のスナップショット

## 根本原因

1. **Pydantic model_dump() の挙動**: `model_dump()` は UUID フィールドを `UUID` オブジェクトとして返す（文字列ではない）
2. **SQLAlchemy JSONB**: デフォルトの JSON シリアライザは UUID を処理できない
3. **影響するスキーマ**:
   - `L3ClipDetails.asset_id: UUID | None`
   - `L3AudioClipDetails.asset_id: UUID`
   - その他 UUID フィールドを持つモデル

## 解決策（実装済み）

### Option B: record_operation で一括シリアライズ ✅ 採用

`operation_service.py` の `record_operation()` と `update_operation_diff()` で全 JSONB フィールドを一括シリアライズ。
呼び出し側の漏れがあっても防げる中央防御。

```python
# operation_service.py

def _serialize_for_json(obj: Any) -> Any:
    """Recursively convert UUIDs to strings for JSON serialization."""
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize_for_json(item) for item in obj]
    return obj

# record_operation() 内で全 JSONB フィールドに適用
operation = ProjectOperation(
    ...
    affected_clips=_serialize_for_json(affected_clips or []),
    affected_layers=_serialize_for_json(affected_layers or []),
    affected_audio_clips=_serialize_for_json(affected_audio_clips or []),
    diff=_serialize_for_json(diff.model_dump()) if diff else None,
    request_summary=_serialize_for_json(request_summary.model_dump()) if request_summary else None,
    result_summary=_serialize_for_json(result_summary.model_dump()) if result_summary else None,
    rollback_data=_serialize_for_json(rollback_data) if rollback_data else None,
    ...
)
```

## 補助対処（ai_v1.py でも適用）

`ai_v1.py` にも `_serialize_for_json()` を追加し、呼び出し側の健康度を上げる:

```python
def _serialize_for_json(obj: Any) -> Any:
    """Recursively convert UUIDs to strings for JSON serialization."""
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize_for_json(item) for item in obj]
    return obj
```

適用箇所:
- `result_dict = _serialize_for_json(result.model_dump())`
- `key_params=_serialize_for_json({...})`
- `clip_data = _serialize_for_json(audio_clip.model_dump())`
- `layer_data = _serialize_for_json(layer_summary.model_dump())`
- `rollback_data=_serialize_for_json({...})`

## 根本的な解決策（推奨）

### Option A: Pydantic model_dump() で mode='json' を使用

```python
# Before
result_dict = result.model_dump()

# After
result_dict = result.model_dump(mode='json')  # UUID を文字列に変換
```

**メリット**: シンプル、Pydantic の標準機能
**デメリット**: 全ての model_dump() 呼び出しを変更する必要がある

### Option B: operation_service で一括シリアライズ

`record_operation()` メソッド内で JSONB フィールドを一括シリアライズ:

```python
async def record_operation(self, ..., rollback_data: dict | None = None, ...):
    operation = ProjectOperation(
        ...
        rollback_data=_serialize_for_json(rollback_data) if rollback_data else None,
        ...
    )
```

**メリット**: 呼び出し側の変更が不要
**デメリット**: 二重シリアライズの可能性

### Option C: SQLAlchemy カスタム JSON シリアライザ

```python
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import TypeDecorator

class JSONB_UUID(TypeDecorator):
    impl = JSONB

    def process_bind_param(self, value, dialect):
        return _serialize_for_json(value)
```

**メリット**: モデルレベルで解決、漏れがない
**デメリット**: 設定の複雑さ

## 現状

✅ **Option B 実装完了** - `operation_service.py` で中央防御

全 JSONB フィールドが `record_operation()` と `update_operation_diff()` でシリアライズされる。
呼び出し側の漏れがあっても UUID エラーは発生しない。

## 今後のアクション（オプション）

Option A (`mode='json'`) を段階的に適用して呼び出し側の健康度を上げる:
- `grep -n "\.model_dump()" src/` で対象を特定
- `.model_dump()` → `.model_dump(mode='json')` に置換
- 別PRで段階的に整理

## テスト

```bash
# ユニットテスト（168件パス）
python -m pytest tests/test_ai_v1_api.py -v

# E2E テスト（動画アセットを使用）
curl -X POST ".../api/ai/v1/projects/{id}/clips" \
  -H "X-API-Key: ..." \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"clip": {"layer_id": "...", "asset_id": "..."}}'
```
