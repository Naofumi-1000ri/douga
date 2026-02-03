# Data Constraints (Target)

最終更新: 2026-02-03

このドキュメントは**AIが推測しない**ための制約定義です。
すべての入力はここで定義された範囲・型・単位に従う必要があります。

---

## 1. 共通ルール

- **時間単位**: `time_base = ms` 固定（frame値は受け付けない）
- **座標単位**: ピクセル（px）
- **色**: `#RRGGBB` または `rgba(r,g,b,a)`（r,g,b=0〜255 / a=0〜1）
- **ID**: UUID v4（文字列）
- **負値禁止**: start_ms / duration_ms / scale 等は原則 >= 0
- **丸め**: すべて `round_half_up`（`floor(x + 0.5)`）で整数化
- **時間量子化**: 1ms（整数）

---

## 2. Project

| フィールド | 型 | 制約 | 例 |
|---|---|---|---|
| id | string(UUID) | 必須 | "..." |
| name | string | 1-200文字 | "Lesson 01" |
| time_base | const | `ms` 固定 | "ms" |
| frame_rate | const | 30 固定 | 30 |
| resolution | const | 1920x1080 固定 | {"w":1920,"h":1080} |
| duration_ms | int | >= 0 | 60000 |
| overlap_policy | const | `disallow` 固定 | "disallow" |

**不変**: `time_base / frame_rate / resolution` は作成後に変更不可。

---

## 3. Layer

| フィールド | 型 | 制約 |
|---|---|---|
| id | string(UUID) | 必須 |
| name | string | 1-100文字 |
| order | int | 1以上 |
| visible | bool | - |
| locked | bool | - |
| color | string | `#RRGGBB` |

---

## 4. Clip (Video/Image/Shape/Text)

| フィールド | 型 | 制約 |
|---|---|---|
| id | string(UUID) | 必須 |
| layer_id | string(UUID) | 必須 |
| asset_id | string(UUID) | video/imageなら必須 |
| type | enum | `video`/`image`/`shape`/`text` |
| start_ms | int | >= 0 |
| duration_ms | int | 1以上 |
| in_point_ms | int | >= 0 |
| out_point_ms | int | in_point_ms < out_point_ms |
| transform.position | object | px単位 |
| transform.scale.x | number | 0.01〜100 |
| transform.scale.y | number | 0.01〜100 |
| transform.rotation | number | -360〜360（度） |
| transform.opacity | number | 0〜1 |
| transform.anchor | object | 0〜1（正規化） |
| transition_in | object | 任意（transition参照） |
| transition_out | object | 任意（transition参照） |

### Effects

| フィールド | 型 | 制約 |
|---|---|---|
| effects.blend_mode | enum | normal / multiply / screen / overlay |
| effects.chroma_key.enabled | bool | - |
| effects.chroma_key.color | string | Color |
| effects.chroma_key.similarity | number | 0〜1 |
| effects.chroma_key.blend | number | 0〜1 |

### Transitions

| フィールド | 型 | 制約 |
|---|---|---|
| transition_in.type / transition_out.type | enum | cut / fade / crossfade / dip_to_black / dip_to_white / wipe_left / wipe_right / wipe_up / wipe_down |
| transition_in.duration_ms / transition_out.duration_ms | int | cut は 0、その他は 100〜2000 |

---

## 5. TextStyle

| フィールド | 型 | 制約 |
|---|---|---|
| text | string | 1-2000文字 |
| font_family | string | `Capabilities.font_families` に含まれるもの |
| font_size | int | 6〜300 |
| font_weight | int | 100〜900 |
| line_height | number | 0.5〜3.0 |
| color | string | Color |
| align | enum | left/center/right |
| shadow.color | string | Color |
| shadow.blur | number | 0〜200 |
| shadow.offset_x | number | px |
| shadow.offset_y | number | px |

---

## 6. Shape

| フィールド | 型 | 制約 |
|---|---|---|
| shape_type | enum | rect/ellipse/triangle/line |
| fill | string | Color |
| stroke_color | string | Color |
| stroke_width | int | 0〜100 |
| radius | int | 0〜300 (rect) |

---

## 7. AudioClip

| フィールド | 型 | 制約 |
|---|---|---|
| id | string(UUID) | 必須 |
| track_id | string(UUID) | 必須 |
| asset_id | string(UUID) | 必須 |
| start_ms | int | >= 0 |
| duration_ms | int | 1以上 |
| in_point_ms | int | >= 0 |
| out_point_ms | int | in_point_ms < out_point_ms |
| volume | number | 0〜2.0 |
| fade_in_ms | int | 0〜600000 |
| fade_out_ms | int | 0〜600000 |

---

## 8. Keyframe

| フィールド | 型 | 制約 |
|---|---|---|
| id | string(UUID) | 必須 |
| time_ms | int | clip範囲内 |
| property | enum | position/scale/rotation/opacity |
| value | number or object | propertyに依存 |
| easing | enum | linear / ease_in / ease_out / ease_in_out / ease_in_quad / ease_out_quad / ease_in_out_quad / ease_in_sine / ease_out_sine / ease_in_out_sine / ease_in_expo / ease_out_expo / ease_in_out_expo / ease_in_back / ease_out_back / ease_in_out_back |

**注記**: 音量は `VolumeKeyframe`（audio-clipのvolume-keyframes）として別扱い。`value` は 0〜2.0。

## 9. VolumeKeyframe

| フィールド | 型 | 制約 |
|---|---|---|
| id | string(UUID) | 必須 |
| time_ms | int | audio clip 範囲内 |
| value | number | 0〜2.0 |
| easing | enum | linear / ease_in / ease_out / ease_in_out / ease_in_quad / ease_out_quad / ease_in_out_quad / ease_in_sine / ease_out_sine / ease_in_out_sine / ease_in_expo / ease_out_expo / ease_in_out_expo / ease_in_back / ease_out_back / ease_in_out_back |

---

## 10. Marker

| フィールド | 型 | 制約 |
|---|---|---|
| id | string(UUID) | 必須 |
| time_ms | int | >= 0 |
| name | string | 1-100文字 |
| color | string | Color |

---

## 11. バリデーション必須チェック

- 参照IDの存在
- clipの時間範囲が asset.duration 内
- duration_ms > 0
- overlap_policy = disallow の場合、同一レイヤー/トラック内の重複禁止
- transform値のNaN/Infinity禁止
