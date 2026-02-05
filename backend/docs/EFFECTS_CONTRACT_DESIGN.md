# Effects Contract Design（サクラダ化防止）

## 問題

v1 APIにeffects関連機能（chroma_key等）を追加する際、アドホックな実装が積み重なり「サクラダファミリア化」するリスク。

## 解決策：契約→生成→実装の一方向フロー

```
effects_spec.yaml (SSOT)
        ↓ 自動生成
   ┌────┼────┐
   ↓    ↓    ↓
Schema Docs Capabilities
   ↓
実装 + テスト
```

## 1. 単一ソース（SSOT）: effects_spec.yaml

```yaml
# backend/specs/effects_spec.yaml
version: "1.0"
effects:
  chroma_key:
    description: "クロマキー合成（緑/青背景除去）"
    params:
      enabled:
        type: boolean
        default: false
      color:
        type: string
        format: hex_color  # #RRGGBB
        default: "#00FF00"
        x-constraint: "^#[0-9A-Fa-f]{6}$"
      similarity:
        type: number
        minimum: 0.0
        maximum: 1.0
        default: 0.4
      blend:
        type: number
        minimum: 0.0
        maximum: 1.0
        default: 0.1
    since: "1.0"

  blur:
    description: "ぼかし効果"
    params:
      enabled:
        type: boolean
        default: false
      radius:
        type: integer
        minimum: 0
        maximum: 100
        default: 10
    since: "1.1"  # 将来バージョン
```

## 2. 自動生成ターゲット

### JSON Schema (バリデーション用)
```json
{
  "type": "object",
  "properties": {
    "chroma_key": {
      "type": "object",
      "properties": {
        "enabled": {"type": "boolean"},
        "color": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
        "similarity": {"type": "number", "minimum": 0, "maximum": 1},
        "blend": {"type": "number", "minimum": 0, "maximum": 1}
      }
    }
  }
}
```

### Capabilities レスポンス
```json
{
  "supported_effects": ["chroma_key"],
  "effect_params": {
    "chroma_key": {
      "color": {"type": "string", "format": "hex_color"},
      "similarity": {"type": "number", "min": 0, "max": 1},
      "blend": {"type": "number", "min": 0, "max": 1}
    }
  }
}
```

### llms-full.txt (AIドキュメント)
自動生成セクションとして effects パラメータ一覧を挿入。

## 3. Patch Semantics（部分更新）

```python
# PATCH /clips/{clip_id}/effects
{
  "effects_patch": {
    "set": {
      "chroma_key.enabled": true,
      "chroma_key.color": "#00FF00"
    },
    "unset": ["blur"]  # blur効果を削除
  }
}

# または明示的モード
{
  "effects": {"chroma_key": {"enabled": true}},
  "update_mode": "merge"  # "merge" | "replace"
}
```

## 4. Contract Tests

```python
# tests/contract/test_effects_contract.py

def test_validate_only_matches_apply():
    """validate_only=trueの結果がapply結果と一致"""

def test_unknown_effect_warning():
    """未対応effectは警告（エラーではない）"""

def test_out_of_range_rejected():
    """範囲外パラメータは400"""

def test_capabilities_lists_supported():
    """capabilitiesに全supported_effectsが含まれる"""
```

## 5. Effect追加のDoD（Definition of Done）

新しいeffectを追加する際のチェックリスト：

- [ ] `effects_spec.yaml` に定義追加
- [ ] `since` バージョンを指定
- [ ] Schema自動生成を実行
- [ ] Docs自動生成を実行
- [ ] `/capabilities` に反映確認
- [ ] `validate_only` テスト追加
- [ ] `apply` 実装
- [ ] Contract test追加
- [ ] llms-full.txt 更新

## 6. 運用ルール

| ルール | 理由 |
|--------|------|
| 新effect = 1PR | レビューしやすさ |
| Spec先→実装後 | 契約駆動を強制 |
| capabilities=false中は使用不可 | 未完成機能の露出防止 |
| 未対応param = warning | 後方互換性 |
| 範囲外 = 400 error | データ整合性 |

## 7. 現状の課題（#069, #078）

1. `/clips/{clip_id}/effects` エンドポイントが未実装（#069）
2. effects_spec.yaml が存在しない
3. 自動生成パイプラインがない

## 8. 実装優先順

1. **Phase 1**: chroma_key を正式仕様化（既存形式を契約に昇格）
2. **Phase 2**: effects_spec.yaml + 生成スクリプト
3. **Phase 3**: Contract test追加
4. **Phase 4**: 新effect追加時のテンプレート化
