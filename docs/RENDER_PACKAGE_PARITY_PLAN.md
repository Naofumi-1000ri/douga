# Render Package Parity Plan

Last updated: 2026-03-31

## Goal

`Export` と `render package` は別仕様ではなく、同じ timeline / assets / export range に対して同じ最終動画を生成しなければならない。

このドキュメントでは、その状態を「parity」と呼ぶ。

## Canonical Definition

`Export` を正解仕様とする。

`render package` は以下の違いだけを持つ別実行経路であるべき:

- 実行場所がサーバーではなくローカル
- 出力生成前に ZIP 化とパス書き換えが入る

それ以外の timeline 解釈、時間範囲、レイヤー順序、エフェクト、音声処理は `Export` と一致する必要がある。

## Acceptance Criteria

同じ入力に対して:

- server `Export` の最終 MP4
- package 展開後に `bash render.sh` を実行して得た最終 MP4

がサポート対象環境で `framemd5` 一致すること。

補足:

- 画面上の見た目一致ではなく、動画フレーム列の一致を基準にする
- 少なくとも CI で固定した FFmpeg 環境では一致していること
- 追加機能は parity fixture を増やしてからマージする

## Milestones

### 1. Export を唯一の正解仕様として固定する

Status: completed

Done when:

- チーム内で `Export` を唯一の正解仕様として扱う
- `render package` を簡易代替出力として扱わない
- Issue / PR / docs にこの前提が明記されている

### 2. package と Export の前処理を完全共有化する

Status: completed

Done when:

- `export_start_ms` / `export_end_ms` / `duration_ms` の解釈が一致する
- timeline 正規化が package と Export で別々に分岐しない
- asset 解決と audio track 構築の差分が ZIP 化都合のみに限定される

Current state:

- `start_render` / `render package` API / package builder が同じ normalize helper を使う
- `export_start_ms` / `export_end_ms` / `duration_ms` の解釈は shared helper で一本化済み
- range validation は helper と unit test で保護される

### 3. parity テストを render 機能単位で拡張する

Status: completed

Done when:

- 機能カテゴリごとに `Export` vs `render package` parity fixture がある
- 新機能追加時は対応 fixture が追加される

Current coverage:

- static overlay parity
- partial export parity
- media clip parity
- crop
- chroma key
- freeze frame
- text / shape overlay
- audio fade
- volume keyframes
- visual keyframes: x / y / scale / rotation / opacity interpolation
- slide transition path
- avatar dodge / generated keyframes
- multi-track audio interactions
- deterministic BGM ducking envelope
- package runtime diagnostics / Docker wrapper presence
- longer timelines that trigger chunked render

### 4. 未実装の render 機能を RenderPipeline に実装する

Status: completed

Done when:

- `clip.keyframes` が実レンダーに反映される
- parity fixture で keyframe animation が一致する
- text effect / transition を仕様として残すなら package/export 両方で同じ経路に接続される
- layer semantics が必要なら `layer_type` が単なるラベルではなく実装上も意味を持つ

Current state:

- `clip.keyframes` は active render path に接続済みで parity fixture も追加済み
- generic slide transitions は active render path に接続済み
- avatar dodge は generated keyframes として parity fixture に含めた
- BGM ducking は sidechaincompress ではなく narration clip timing から作る deterministic envelope に置き換えた
- `text_renderer.py` の sparkle / glow / pulse helper は未使用コードで、`rg` 上も active render path / API / frontend から到達しない
- `transition_in` / `transition_out` は product-facing API では unsupported と明記されているため、dead helper は parity 対象外とした
- `layer_type` は依然として主に重ね順ラベルとして扱われるが、現在の product-supported render feature set では parity fixture で保護されている

### 5. package 実行環境の再現性を固める

Status: completed

Done when:

- サポート対象の FFmpeg バージョンが定義される
- package 実行環境差で parity が崩れにくい
- 必要なら Docker などの固定 runtime が用意される

Candidate approaches:

- README に推奨 FFmpeg バージョンを明記する
- `render.sh` に runtime diagnostics を追加する
- Docker 実行オプションを package に同梱する

Current state:

- package に server-side FFmpeg version を埋め込み済み
- `render.sh` が local FFmpeg version と expected version を表示・警告する
- `render-docker.sh` を package に同梱済み
- shell quoting drift を避けるため package scripts は `filter_complex_script` を使う
- audio intermediate は lossy AAC ではなく WAV に固定し、final MP4 でのみ AAC 化する
- package scripts は server-side `-threads 2` を引き継がず、local FFmpeg の
  thread auto-selection に委ねる

### 6. parity を release gate にする

Status: completed

Done when:

- PR チェックで parity テストが独立して走る
- parity が壊れた変更はマージできない
- render 関連のレビューで parity fixture 更新が必須になる

Current state:

- PR workflow に render parity step を追加済み
- render parity / audio mixer / export-range normalization が独立 step で走る
- covered feature set の parity regressions は PR gate で止まる

## Current CI Gate

The current PR gate must run:

```bash
uv run --python 3.11 pytest tests/test_audio_mixer.py -q
uv run --python 3.11 pytest tests/test_render_package_builder.py -q
uv run --python 3.11 pytest tests/test_render_pipeline.py -q
uv run --python 3.11 pytest tests/test_timeline_normalization.py -q
```

This is the current release gate for the product-supported render feature set.

## Working Rules

When modifying render behavior:

1. Decide whether the change affects canonical `Export` behavior
2. If yes, add or update a parity fixture first or in the same PR
3. Verify both server `Export` and package execution
4. Do not ship package-only rendering behavior unless explicitly accepted as temporary debt

Execution-policy boundary:

- Output parity is the goal for the encoded result, not for Cloud Run resource caps
- Server-only execution limits such as FFmpeg `-threads` may be omitted from package scripts
  when they are documented in package README / manifest and covered by tests
- `render-docker.sh` pins FFmpeg runtime/version, but it is not a guarantee of matching
  server CPU or memory limits

## Exit Condition

This plan is complete only when milestones 1 through 6 are all satisfied and parity coverage includes the product-supported render feature set.
