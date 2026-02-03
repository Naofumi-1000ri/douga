# Coordinate Systems (Target)

最終更新: 2026-02-03

AIが位置やサイズを誤解しないための座標系定義。
**全ての座標・角度・スケールの意味はここで一意に固定する。**

---

## 1. Timeline 座標系

- 単位: ms
- `time_ms = 0` がタイムライン開始
- すべての clip は `start_ms` から `start_ms + duration_ms` の区間で有効
- 時間は **整数ms**（小数禁止）

---

## 2. Canvas 座標系（正規化なし）

- 原点: **キャンバス中心 (0, 0)**
- +X: 右方向
- +Y: 下方向
- 単位: px
- 解像度: 1920x1080 固定

### 2.1 変換

- 左上: `(-W/2, -H/2)`
- 右下: `(W/2, H/2)`

---

## 3. Transform

### 3.1 position
- `position.x`, `position.y` はキャンバス中心基準
- 例: 画面右上に配置する場合
  - `x = W/2 - margin`
  - `y = -H/2 + margin`

### 3.2 scale
- 1.0 = 100%
- 0.5 = 50%
- scale は 0.01〜100

### 3.3 rotation
- 単位: **度**
- 正方向: 時計回り
- 0度は水平

### 3.4 anchor
- `anchor` は clip 内ローカル座標
- 必須（省略不可）
- 推奨: 中心 `(0.5, 0.5)`
- 範囲: 0〜1

---

## 4. Clip内部座標（ローカル）

- clip原点はクリップ領域の左上
- 画像・動画は元のアスペクト比を保持

---

## 5. Safe Zone

- safe_zone_ratio: 0.9 固定
- テロップ/字幕は safe zone 内に配置

---

## 6. 例

### 6.1 中央にテキストを配置
```json
{
  "transform": {
    "position": {"x": 0, "y": 0},
    "scale": {"x": 1, "y": 1},
    "rotation": 0,
    "opacity": 1,
    "anchor": {"x": 0.5, "y": 0.5}
  }
}
```

### 6.2 左下に小さなロゴ
```json
{
  "transform": {
    "position": {"x": -860, "y": 480},
    "scale": {"x": 0.2, "y": 0.2},
    "rotation": 0,
    "opacity": 1,
    "anchor": {"x": 0.5, "y": 0.5}
  }
}
```
