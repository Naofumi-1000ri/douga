<div align="center">

# atsurae (あつらえ)

### AI crafts it for you.

AI-powered video editing SaaS that transforms text instructions into professionally edited videos.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Build](https://github.com/Naofumi-1000ri/douga/actions/workflows/pr-check.yml/badge.svg)](https://github.com/Naofumi-1000ri/douga/actions/workflows/pr-check.yml)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![AI Friendly](https://img.shields.io/badge/AI_Friendly-9.2%2F10-blueviolet.svg)]()
[![TypeScript](https://img.shields.io/badge/TypeScript-5.6-blue.svg)]()
[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-009688.svg)]()

</div>

---

## What is atsurae?

**atsurae** is an AI-native video editor designed from the ground up for programmatic control. Describe what you want in natural language, and the AI assembles broadcast-quality video using a deterministic 5-layer compositing engine powered by FFmpeg. No timeline dragging. No manual keyframing. Just results.

> 🎬 **[Live Demo](https://douga-2f6f8.web.app)** — Try it in your browser

---

## ✨ Features

- **🤖 AI-Powered Editing** — Describe your edit in plain text; the AI plans, validates, and executes it via a structured API pipeline
- **🎞️ 5-Layer Compositing** — Background → Screen Capture → Avatar (chroma key) → Effects → Text/Telop, rendered with FFmpeg `filter_complex`
- **📐 Deterministic Rendering** — Same input always produces the same output. Integer millisecond timeline, center-origin coordinates, zero ambiguity
- **🔍 Validate Before Apply** — Every operation supports `validate_only` mode: preview the diff before committing changes
- **🎯 AI-Friendly API (9.2/10)** — Scored by 3 independent AI experts. Structured errors with `suggested_fix`, idempotent operations, L1/L2/L3 information hierarchy
- **🔄 Rollback & History** — Full operation history with one-click rollback via `operation_id`
- **🎬 One-Click Export** — 1920×1080, 30fps, H.264 + AAC — Udemy-ready MP4 out of the box
- **📦 Local Render Package** — Download assets + generated overlays + FFmpeg scripts and reproduce the same final video as Export on your own machine
- **🧩 MCP Integration** — Model Context Protocol server for seamless Claude/AI assistant integration
- **📊 Visual QA Loop** — Event point detection, frame sampling, and composition validation — all before final render

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Frontend (React)                   │
│            Vite + TypeScript + Tailwind               │
│         Firebase Hosting + Authentication             │
└──────────────────────┬──────────────────────────────┘
                       │ REST API
                       ▼
┌─────────────────────────────────────────────────────┐
│                  Backend (FastAPI)                     │
│               Cloud Run (asia-northeast1)             │
│                                                       │
│  ┌─────────┐  ┌──────────┐  ┌────────────────────┐  │
│  │ AI API  │  │ Semantic │  │  Render Engine     │  │
│  │ v1      │  │ Ops      │  │  (FFmpeg)          │  │
│  └────┬────┘  └─────┬────┘  └────────┬───────────┘  │
│       │             │                │               │
│       ▼             ▼                ▼               │
│  ┌──────────────────────────────────────────────┐   │
│  │           PostgreSQL (Cloud SQL)              │   │
│  └──────────────────────────────────────────────┘   │
│                       │                              │
│                       ▼                              │
│  ┌──────────────────────────────────────────────┐   │
│  │        Google Cloud Storage (Assets)          │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│              5-Layer Composition Model                │
│                                                       │
│  L5  ████████████████  Text / Telop                  │
│  L4  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓  Effects (particles, etc.)     │
│  L3  ▒▒▒▒▒▒▒▒▒▒▒▒▒▒  Avatar (chroma-keyed)         │
│  L2  ░░░░░░░░░░░░░░░  Screen Capture / Slides       │
│  L1  ··············  Background (3D / gradient)      │
└─────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

- Node.js 18+
- npm or yarn

### Frontend Development

```bash
# Clone the repository
git clone https://github.com/Naofumi-1000ri/douga.git
cd douga/frontend

# Install dependencies
npm install

# Start development server (API points to Cloud Run)
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

> **Note:** The backend runs on Cloud Run — no local server setup required. The frontend `.env` is pre-configured to use the production API.

---

## ✅ CI

- Pull requests run the `PR Check` workflow against `main`.
- Frontend Checks cache Playwright browser binaries under `~/.cache/ms-playwright`, keyed by `frontend/package-lock.json`, so Chromium is only downloaded again when the Playwright dependency set changes.
- Linux browser dependencies are still installed during CI because they are not part of the cached browser bundle.

---

## 🛠️ Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Frontend** | React 18 + TypeScript + Vite | Editor UI with timeline, preview canvas, layer management |
| **Styling** | Tailwind CSS | Responsive, utility-first design |
| **State** | Zustand | Lightweight state management with undo/redo history |
| **Backend** | Python 3.11 + FastAPI | REST API with AI-friendly endpoints |
| **Database** | PostgreSQL (Cloud SQL) | Project, layer, clip, and asset persistence |
| **Storage** | Google Cloud Storage | Video/audio/image asset storage |
| **Auth** | Firebase Authentication | User authentication and session management |
| **Hosting** | Firebase Hosting | Frontend static deployment |
| **Compute** | Cloud Run | Auto-scaling backend containers |
| **Rendering** | FFmpeg (`filter_complex`) | Deterministic multi-layer video composition |
| **AI Integration** | MCP Server + OpenAI / Gemini / Claude | Multi-provider AI editing pipeline |

---

## 🤖 API — Built for AI

atsurae's API is designed with a single philosophy: **AI should never guess.**

### Information Hierarchy

```
L1: Overview  (~300 tokens)  →  Project metadata, layer counts
L2: Structure (~800 tokens)  →  Layer/track layout, time coverage
L3: Details   (~400 tokens)  →  Individual clip properties, neighbors
```

### Key Design Principles

- **Validate/Apply separation** — Every mutation can be dry-run with `validate_only: true`
- **Structured errors** — Every error includes `error_code`, `suggested_fix`, and `retryable` flag
- **Idempotent operations** — `Idempotency-Key` header prevents duplicate side effects
- **Semantic operations** — High-level intents like `snap_to_previous`, `close_gap`, `auto_duck_bgm`
- **Diff on demand** — `include_diff: true` returns before/after state for every mutation
- **Rollback** — Any operation can be undone via `operation_id`

### AI Friendliness Score: 9.2 / 10

Evaluated by 3 independent AI integration experts across determinism, observability, error quality, and safety dimensions.

```
GET  /api/ai/v1/projects/{id}/overview          # What am I working with?
GET  /api/ai/v1/projects/{id}/structure          # Where are the layers and clips?
POST /api/ai/v1/projects/{id}/semantic           # Do something smart
POST /api/ai/v1/projects/{id}/batch              # Do many things atomically
GET  /api/ai/v1/capabilities                     # What can I do?
```

> 📖 Full API reference: [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) | AI guide: [`docs/llms.txt`](docs/llms.txt) | Editing foundation roadmap: [`docs/EDITING_ARCHITECTURE_ROADMAP.md`](docs/EDITING_ARCHITECTURE_ROADMAP.md) | Render/package parity plan: [`docs/RENDER_PACKAGE_PARITY_PLAN.md`](docs/RENDER_PACKAGE_PARITY_PLAN.md)

### For AI/API Operators

If an AI agent is expected to actually edit a project through the API, README alone is not enough. Read these docs in this order before attempting timeline or preview operations:

1. [`docs/ai-developer-guide.md`](docs/ai-developer-guide.md)
   End-to-end operator guide for project editing, preview sampling, validation, and timeline inspection.
2. [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md)
   Canonical request and response contract for AI v1, preview, export, render package, and mutation endpoints.
3. [`docs/API_EXAMPLES.md`](docs/API_EXAMPLES.md)
   Copyable examples for `validate_only`, semantic operations, batch operations, chroma key, and export flows.
4. [`docs/E2E_VIDEO_WORKFLOW.md`](docs/E2E_VIDEO_WORKFLOW.md)
   Recommended execution order for real editing tasks: inspect, validate, apply, verify, then render/export.

Practical rule:

- Use browser AI only for the currently open editor context.
- Use the API workflow when operating on projects or sequences outside the currently open browser session.

---

## 🇯🇵 日本語

### atsurae（あつらえ）とは

**atsurae** は、テキスト指示だけでプロ品質の動画を自動編集するAI動画編集SaaSです。

Udemy講座制作のワークフローから生まれ、「タイムラインをドラッグする」従来の編集作業を、「やりたいことを言葉で伝える」体験に変えます。

### 主な特徴

- **🤖 AI編集** — テキストで指示するだけ。AIが計画→検証→実行まで自動で行います
- **🎞️ 5層レイヤー合成** — 背景・画面キャプチャ・アバター（クロマキー）・エフェクト・テロップの5層をFFmpegで合成
- **📐 決定性レンダリング** — 同じ入力は必ず同じ出力。ミリ秒単位の整数タイムライン
- **🔍 適用前に検証** — すべての操作で `validate_only` モード対応。変更をコミットする前にdiffを確認
- **🎯 AI Friendly API（9.2/10）** — 3人の専門家が評価。構造化エラー、冪等操作、L1/L2/L3情報階層
- **🔄 ロールバック** — 全操作の履歴管理とワンクリック復元
- **🎬 ワンクリックエクスポート** — 1920×1080, 30fps, H.264+AAC（Udemy推奨形式）
- **🧩 MCP対応** — Claude等のAIアシスタントとシームレスに連携

### 無料プラン

| | Free | Pro |
|---|---|---|
| プロジェクト数 | 3件 | 無制限 |
| エクスポート | 月5本 | 無制限 |
| 解像度 | 1080p | 1080p / 4K |
| 動画最大長 | 10分 | 無制限 |
| ストレージ | 1GB | 無制限 |
| ウォーターマーク | あり | なし |

> 🔗 **[無料で始める](https://douga-2f6f8.web.app)**

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

---

## 🤝 Contributing

Contributions are welcome! Whether it's bug reports, feature requests, or pull requests — all are appreciated.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

> 💡 **For AI framework developers:** atsurae's API design philosophy is open source. Check [`docs/AI_FRIENDLY_SPEC.md`](docs/AI_FRIENDLY_SPEC.md) for our AI-friendly API design principles that you can adopt in your own projects.

---

<div align="center">

**[Live Demo](https://douga-2f6f8.web.app)** · **[API Docs](docs/API_REFERENCE.md)** · **[AI Guide](docs/llms.txt)**

Made with ❤️ for creators who'd rather describe than drag.

</div>
