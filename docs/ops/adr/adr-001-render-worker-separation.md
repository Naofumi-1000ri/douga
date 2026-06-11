# ADR-001: レンダーワーカーの分離 — Cloud Run Jobs vs Cloud Tasks

- **Status**: Accepted
- **Date**: 2026-06-11
- **Issue**: #281

---

## 背景

現行のレンダー処理は API サーバーと同一インスタンス内の `asyncio.create_task`（`backend/src/api/render.py`）で実行されており、以下の構造的限界がある:

1. **タイムアウト制約**: Cloud Run のリクエストタイムアウト 900s はジョブ自体には影響しないが、同一インスタンスのスケールイン/再起動でジョブが消える（SIGKILL では finally も走らず /tmp も残る）。
2. **リソース競合**: API リクエスト処理とレンダーがメモリ 4Gi・CPU を奪い合う。FFmpeg は CPU バウンドで API のレイテンシを悪化させる。
3. **キャンセル不能**: 専用プロセスを kill できない（現在は DB フラグで協調キャンセルのみ）。
4. **セルフヒーリング不全**: インスタンス再起動時にジョブが孤児化する。

---

## 検討した選択肢

### 選択肢 A: Cloud Run Jobs

| 観点 | 評価 |
|---|---|
| 実行時間 | 最大 24h（タイムアウトなし） |
| リソース分離 | API と完全独立（CPU/MEM を別設定） |
| Docker イメージ | 既存 API イメージを `--image` 指定で流用可 |
| 起動オーバーヘッド | コールドスタート ~10–30s（ウォームプール不可） |
| コスト | 実行時間のみ課金（アイドル課金なし） |
| キャンセル | `gcloud run jobs executions cancel` で停止 |
| スケーリング | 1 ジョブ = 1 Task コンテナ、並列実行可 |
| インフラ設定 | Cloud Run Job リソース（yaml/gcloud）が必要 |
| 状態管理 | DB + Execution ID で管理 |

### 選択肢 B: Cloud Tasks + 専用 Cloud Run サービス

| 観点 | 評価 |
|---|---|
| 実行時間 | Cloud Tasks の最大 30m タスク（サービス側は無制限） |
| リソース分離 | 専用サービスで完全分離 |
| Docker イメージ | 同じイメージを別サービスとしてデプロイ |
| 起動オーバーヘッド | min-instances=1 設定でウォームアップ可 |
| コスト | アイドル課金あり（min-instances > 0 の場合） |
| キャンセル | Cloud Tasks でのタスクキャンセル（既に実行中は不可） |
| スケーリング | Cloud Run の自動スケーリングに依存 |
| インフラ設定 | Cloud Tasks キュー + 追加の Cloud Run サービス |
| 状態管理 | DB + Tasks ID で管理 |

### 選択肢 C: 現行維持（inline asyncio.create_task）

現行の inline モードを維持しつつ、ADR の設計のみ実施。

---

## 決定: **Cloud Run Jobs を推奨**

### 理由

1. **既存 Docker イメージの流用**: 新たなサービス定義不要。`--image` に同じイメージを指定し、エントリポイントを `python -m src.render_worker <job_id>` に切り替えるだけ。
2. **長時間ジョブに最適**: レンダーは最大 30 分以上かかる場合がある。Cloud Run Jobs は 24h まで対応し、タイムアウトの懸念がない。
3. **コスト最適化**: ジョブ実行時のみ課金。API サーバーのアイドル時間にレンダーコストが混入しない。
4. **完全なリソース分離**: Jobs の CPU/MEM を API とは別設定にでき、API のレイテンシ劣化を防止。
5. **シンプルな状態管理**: Execution ID を DB に保存し、`jobs executions cancel` でジョブ停止可能。
6. **段階的移行**: feature flag（`RENDER_EXECUTION_MODE`）で `inline`（デフォルト）と `jobs` を切り替え可能。本番は `inline` のまま維持でき、インフラ準備後に `jobs` へ切替。

### トレードオフ（許容できる）

- **コールドスタート ~10–30s**: レンダーは非同期でフロントエンドがポーリングするため、起動遅延はユーザー体験に影響しない。
- **インフラ作成が必要**: Cloud Run Job リソースの初期設定が必要。手順は `docs/ops/deploy.md` に記載。

---

## 状態機械

```
queued → running → succeeded
                 → failed
       → cancelled (before pickup)
running → cancelled (via executions cancel)
```

- `queued`: ジョブ DB 作成直後。`jobs` モードでは executor が Cloud Run Jobs を起動待ち。
- `running` (旧 `processing`): ワーカーコンテナ起動後。heartbeat を定期送信。
- `succeeded` (旧 `completed`): レンダー完了。output_key に GCS パスを格納。
- `failed`: エラー発生。error_message にスタックトレースを格納。retry_count でリトライ回数を追跡。
- `cancelled`: ユーザーまたはシステムによりキャンセル。

> **後方互換のため `status` 値は旧来の `processing`/`completed` を維持**。新しいコードでは `running`/`succeeded` として扱うが、DB の実値は変更なし。

---

## キャンセル方針

### inline モード（現行維持）

DB の `status = 'cancelled'` をワーカーが定期チェックし、協調的に停止。FFmpeg サブプロセスは `_kill_active_proc()` で終了。

### jobs モード

1. DB を `cancelled` に更新。
2. `celery_task_id`（リネーム後: `worker_job_execution_id`）から Cloud Run Jobs の Execution ID を取得。
3. `gcloud run jobs executions cancel <execution-id>` を呼び出してコンテナを強制停止。

---

## リトライ・デッドレター方針

### リトライ（jobs モード）

- `retry_count < 3` かつ `failed` 状態のジョブは API の `/render` への再リクエストで手動リトライ可能。
- 自動リトライ: Cloud Run Jobs の `--max-retries` オプションで設定（デフォルト: 0、本番設定で 1 に変更推奨）。

### デッドレター

- `retry_count >= 3` で失敗したジョブは `failed` 状態のままデッドレター扱い。
- Cloud Logging と Sentry でアラート。
- ジョブの `error_message` に詳細スタックトレースを保存。

---

## `celery_task_id` カラムの扱い

`render_job.celery_task_id` を `worker_job_execution_id` に論理的にリネーム（アプリコード上）。  
DBマイグレーションでのカラムリネームは破壊的変更になるため、このIssueでは**コードレベルの再利用**にとどめる（カラム名はそのまま `celery_task_id` として残し、jobs モードでは Cloud Run Execution ID を格納）。  
カラムの物理リネームは将来の独立したマイグレーション Issue で対応予定。

---

## インフラ分離（本番切替時のリソース設定変更）

| リソース | 現行 | 目標（jobs モード有効後） |
|---|---|---|
| API サービス CPU | 2 | 1 |
| API サービス MEM | 4Gi | 2Gi |
| Render Jobs CPU | — | 4 |
| Render Jobs MEM | — | 8Gi |
| Render Jobs タイムアウト | — | 3600s（1h） |

---

## 参考

- [Cloud Run Jobs ドキュメント](https://cloud.google.com/run/docs/create-jobs)
- Issue #268: ジョブライフサイクル機構（heartbeat 導入）
- Issue #278: 可観測性改善
