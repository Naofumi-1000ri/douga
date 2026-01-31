# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

**douga** - Udemy講座制作のためのAI動画編集アプリケーション（社内ツール）

### 目的
- Udemy講座用動画の制作時間短縮
- 品質の一貫性確保

## アーキテクチャ構成（予定）

### コンポーネント構成

```
[ユーザー素材] → [douga アプリ] → [完成動画]
                      │
        ┌─────────────┼─────────────┐
        ↓             ↓             ↓
   [音声処理]    [映像合成]    [エフェクト]
```

### 主要機能モジュール

1. **音声・オーディオ処理**
   - ナレーション管理
   - BGMダッキング（ナレーション時に自動音量調整）
   - SE（効果音）配置
   - 音声ミキシング（ナレーション・BGM・SEのバランス調整）

2. **ビジュアル素材管理**
   - アバター動画（グリーンバック）
   - スライド素材
   - 背景画像・動画
   - フレーム・枠素材（PC画面風など）

3. **映像合成エンジン**
   - クロマキー合成（緑背景透過）
   - レイヤー管理（背景 → 操作画面 → アバター → テロップ）
   - トランジション（フェード、スライド等）

4. **演出・エフェクト**
   - パーティクル・エフェクト（キラキラ、集中線）
   - テロップ・画像アニメーション（ポップイン、スライドイン）

5. **講座構造テンプレート**
   - イントロ・アウトロ（定型のタイトルロゴ、挨拶）
   - セクション間トランジション

## 技術スタック

| 領域 | 技術 |
|------|------|
| Frontend | React + TypeScript + Vite |
| Frontend Hosting | Firebase Hosting |
| Backend | Python + FastAPI |
| Backend Hosting | Cloud Run |
| Database | Cloud SQL (PostgreSQL) |
| Storage | Google Cloud Storage |
| Auth | Firebase Authentication |
| Rendering | FFmpeg (filter_complex) |

## 重要: 開発方針

### ローカルでバックエンドを起動しない

**バックエンドは常にCloud Run上のものを使用する。ローカルでuvicornやdocker-composeでバックエンドを起動してはいけない。**

理由:
- Cloud SQLへの接続設定が本番環境前提
- ローカル環境との差異によるバグを防ぐ
- 開発フローの簡略化

フロントエンドの`.env`は常に本番APIを指す:
```
VITE_API_URL=https://douga-api-344056413972.asia-northeast1.run.app
```

## 開発コマンド

### バックエンド（コード編集・テスト・デプロイのみ）
```bash
cd backend

# 依存関係インストール
uv pip install -e .

# テスト実行
pytest

# Lint
ruff check src/

# デプロイ（Cloud Runへ）
# git push origin main で自動デプロイ、または手動デプロイ（下記参照）
```

**注意: `uvicorn src.main:app --reload` はローカルで実行しない**

### フロントエンド
```bash
cd frontend

# 依存関係インストール
npm install

# 開発サーバー起動（APIはCloud Runを使用）
npm run dev

# ビルド
npm run build

# Firebase Hostingデプロイ
npm run deploy
```

## デプロイ

### 自動デプロイ（CI/CD）
- **Backend (Cloud Run)**: `git push origin main` で自動デプロイ
- **Frontend (Firebase Hosting)**: `cd frontend && npm run deploy`

### Cloud Runログ確認
```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=douga-api" \
  --project=douga-2f6f8 --limit=50 --format="table(timestamp,textPayload)"
```

## 入力素材形式

ユーザーが準備する素材:
- ナレーション音声（WAV/MP3）
- BGM・SE（WAV/MP3）
- アバター動画（グリーンバック、MP4）
- スライド画像（PNG/JPEG）または動画
- 操作画面キャプチャ（MP4）
- 背景素材（画像/動画）

## 出力形式

- MP4 (H.264) - Udemy推奨形式
- 解像度: 1920x1080 (フルHD)
- フレームレート: 30fps
- 音声: AAC

## 開発言語

主にPythonで開発予定。理由:
- MoviePy、OpenCV等の動画処理ライブラリが充実
- FFmpegとの連携が容易
- AI/ML統合が将来的に容易

---

## リファレンス動画分析（sample.mp4）

### 動画仕様
- 解像度: 1920x1080 (Full HD)
- フレームレート: 30fps
- コーデック: H.264 + AAC
- 再生時間: 60秒

### 画面構成パターン（4種類）

| パターン | 説明 | 使用場面 |
|----------|------|----------|
| アバター全画面 | 3D空間にアバター配置 | イントロ、実演 |
| スライド＋ナビキャラ | 情報スライド右下にキャラ | 目次、説明 |
| 操作画面＋アバター | スクリーンキャプチャ＋右下アバター | チュートリアル |
| フルスクリーンテキスト | メッセージ＋エフェクト | CTA、アウトロ |

### レイヤー構成（5層）

```
L5: テロップ・テキスト
L4: エフェクト（キラキラ等）
L3: アバター（クロマキー合成後）
L2: 操作画面・スライド
L1: 背景（3D空間/グラデーション）
```

### 使用アバター
1. **3D VRMアバター（赤衣装）**: メインキャラクター、モーションキャプチャ対応
2. **3D VRMアバター（紫髪）**: 操作説明時のナビゲーター
3. **イラストアバター**: スライド用シンプルキャラクター

### エフェクト要素
- キラキラパーティクル（星型、複数サイズ）
- フェードイン/アウト
- スライドインアニメーション
- 白枠フレーム装飾

### テロップスタイル
- 大見出し: 白文字、太字、中央配置
- 説明テロップ: 画面下部、半透明背景または影付き
- 箇条書き: 左揃え、ドット付き

### タイムライン構成例

| 時間 | セクション | 内容 |
|------|-----------|------|
| 0:00-0:09 | イントロ | アバター＋挨拶テロップ |
| 0:09-0:16 | 目次 | 講座内容スライド |
| 0:17-0:28 | 操作説明 | ソフトウェア操作キャプチャ |
| 0:29-0:30 | トランジション | CTAメッセージ＋エフェクト |
| 0:31-0:41 | 実演 | Unity操作デモ |
| 0:42-0:52 | 活用事例 | カードアニメーション |
| 0:53-1:00 | アウトロ | 締めメッセージ |

詳細分析: `reference/sample_analysis.md`

---

## 実装上の注意点

### プレビューキャンバスの座標系
図形リサイズ等でCSS transformとscaleが絡む場合、座標計算に注意。
詳細は `frontend/src/pages/Editor.tsx` の "SHAPE RESIZE COORDINATE SYSTEM" コメントを参照。
