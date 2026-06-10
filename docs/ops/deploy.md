# Deployment Operations Guide

This document covers production deployment, rollback, Sentry setup, and related ops procedures for the Douga backend.

For the full step-by-step deploy workflow see [`.claude/commands/deploy.md`](../../.claude/commands/deploy.md).

---

## Table of Contents

1. [Normal Deployment](#normal-deployment)
2. [Database Migrations (Alembic)](#database-migrations-alembic)
3. [Rollback](#rollback)
4. [Secret Manager Operations](#secret-manager-operations)
5. [Sentry Error Tracking](#sentry-error-tracking)
6. [Health Checks](#health-checks)
7. [Troubleshooting](#troubleshooting)

---

## Normal Deployment

Production deploys are always performed through `backend/scripts/deploy_prod.sh`.

```bash
cd /path/to/douga_root/main/backend
./scripts/deploy_prod.sh
```

The script guards against deploying from the wrong branch, a dirty tree, or pointing at a non-production target. See [`.claude/commands/deploy.md`](../../.claude/commands/deploy.md) for the full procedure.

---

## Database Migrations (Alembic)

> **Issue #282**: Schema management has been migrated from the startup-time `run_migrations()` / `create_all()` calls to Alembic.
> The app no longer runs DDL on startup. All schema changes are applied explicitly via `alembic upgrade head` **before** deploying the new app revision.

### First-time production baseline stamp (one-time, before the first Alembic deploy)

The production database already has the full schema created by the legacy `run_migrations()` approach.
Do **NOT** run `upgrade head` on the existing production database — instead, stamp it as baseline:

```bash
cd /path/to/douga_root/main/backend

# Set the production DATABASE_URL (Cloud SQL socket format)
export DATABASE_URL="postgresql+asyncpg://USER:PASS@/DB?host=/cloudsql/PROJECT:REGION:INSTANCE"

# Stamp the DB: tells Alembic "everything up to 0001_baseline is already applied"
uv run alembic stamp 0001_baseline

# Verify
uv run alembic current
# Expected output: 0001_baseline (head)
```

After stamping, follow the normal upgrade procedure for all future deployments.

### Normal upgrade procedure (every deploy after baseline stamp)

```bash
cd /path/to/douga_root/main/backend
export DATABASE_URL="..."

# 1. Apply pending migrations BEFORE deploying the new app image
uv run alembic upgrade head

# 2. Deploy the app image (Cloud Run)
./scripts/deploy_prod.sh
```

> The deploy script does **not** run `alembic upgrade head` automatically yet.
> Until it is integrated into the pipeline, run the migration step manually first.

### Creating a new migration

```bash
# Generate a revision from model changes (inspect the diff carefully before committing)
cd backend
uv run alembic revision --autogenerate -m "short_description"

# Review the generated file under alembic/versions/
# Edit if necessary (autogenerate cannot detect all change types)

# Test locally
DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:55438/douga_test" \
  uv run alembic upgrade head
DATABASE_URL="..." uv run alembic downgrade -1   # verify reversibility
DATABASE_URL="..." uv run alembic upgrade head   # re-apply
```

### Downgrade procedure

To roll back one migration:

```bash
export DATABASE_URL="..."
uv run alembic downgrade -1
```

To roll back to a specific revision:

```bash
uv run alembic downgrade <revision_id>
```

> **Warning**: Downgrading in production drops columns or tables. Always back up first and test the downgrade in a staging environment.

### View migration history

```bash
# Current state
uv run alembic current

# Full history
uv run alembic history --verbose
```

---

## Rollback

When a deployment causes a regression, roll back to the previous revision without re-deploying an image.

### Using the rollback script (recommended)

```bash
cd /path/to/douga_root/main/backend
./scripts/rollback_prod.sh           # auto-detects the previous revision and prompts
./scripts/rollback_prod.sh --yes     # auto-detects, no confirmation prompt
```

To roll back to a specific named revision:

```bash
./scripts/rollback_prod.sh douga-api-00042-abc --yes
```

### Manual rollback via gcloud

List recent revisions to identify the target:

```bash
gcloud run revisions list \
  --service=douga-api \
  --region=asia-northeast1 \
  --project=douga-2f6f8 \
  --format="table(metadata.name,metadata.creationTimestamp,status.conditions[0].status)" \
  --sort-by="~metadata.creationTimestamp" \
  --limit=10
```

Send 100% of traffic to the chosen revision:

```bash
gcloud run services update-traffic douga-api \
  --region=asia-northeast1 \
  --project=douga-2f6f8 \
  --to-revisions=<REVISION_NAME>=100
```

Verify traffic is now routing to the target:

```bash
gcloud run services describe douga-api \
  --region=asia-northeast1 \
  --project=douga-2f6f8 \
  --format="table(status.traffic[].revisionName,status.traffic[].percent)"
```

### After rollback

1. Check health: `curl -s https://douga-api-344056413972.asia-northeast1.run.app/health`
2. Investigate the root cause in Cloud Logging or Sentry before redeploying.
3. When the fix is ready, deploy normally via `./scripts/deploy_prod.sh`.

---

## Secret Manager Operations

> **Created 2026-06-10 (Issue #262)**  
> Secret Manager は GCP プロジェクト `douga-2f6f8` を使用。  
> Cloud Run への注入は `deploy_prod.sh` の `USE_SECRET_MANAGER=1` フラグで有効化。

### 登録済みシークレット一覧

| Secret Manager 名 | 注入先の環境変数 | 説明 |
|---|---|---|
| `ai-key-encryption-key` | `AI_KEY_ENCRYPTION_KEY` | `project.ai_api_key` AES-GCM 暗号化鍵（32byte base64）|
| `openai-api-key` | `OPENAI_API_KEY` | OpenAI API キー（要バージョン追加）|
| `database-url` | `DATABASE_URL` | Cloud SQL 接続文字列（要バージョン追加）|
| `edit-token-secret` | `EDIT_TOKEN_SECRET` | X-Edit-Session HMAC 署名鍵（要バージョン追加）|

> `openai-api-key`・`database-url`・`edit-token-secret` はシークレット名のみ作成済み（バージョン未追加）。  
> 実際の値を登録する前に OpenAI キーのローテーションを行うこと（下記参照）。

---

### ステップ 0 — Cloud Run サービスアカウントへのアクセス権付与

Secret Manager から値を読むには Cloud Run の実行 SA (`344056413972-compute@developer.gserviceaccount.com`) に `roles/secretmanager.secretAccessor` を付与する必要があります。

```bash
SA="344056413972-compute@developer.gserviceaccount.com"
for SECRET in ai-key-encryption-key openai-api-key database-url edit-token-secret; do
  gcloud secrets add-iam-policy-binding "${SECRET}" \
    --project=douga-2f6f8 \
    --member="serviceAccount:${SA}" \
    --role="roles/secretmanager.secretAccessor"
done
```

---

### ステップ 1 — シークレットに値を登録（バージョン追加）

> **注意**: シェル履歴に値を残さないよう、必ず `--data-file=-` + パイプ または 一時ファイル方式を使うこと。

#### AI_KEY_ENCRYPTION_KEY（既に登録済み）

```bash
# 確認のみ — 既にバージョン 1 が存在する
gcloud secrets versions list ai-key-encryption-key --project=douga-2f6f8
```

#### OpenAI API キー（キーローテーション後に実施）

```bash
# 新しいキーを取得してからパイプで流す（履歴に残らない）
printf '%s' "sk-..." | \
  gcloud secrets versions add openai-api-key \
    --project=douga-2f6f8 \
    --data-file=-
```

#### DATABASE_URL

```bash
# Cloud SQL のソケット形式: postgresql+asyncpg://USER:PASS@/DB?host=/cloudsql/PROJECT:REGION:INSTANCE
printf '%s' "postgresql+asyncpg://..." | \
  gcloud secrets versions add database-url \
    --project=douga-2f6f8 \
    --data-file=-
```

#### EDIT_TOKEN_SECRET

```bash
# 32 文字以上のランダム値を生成して登録
openssl rand -base64 48 | tr -d '\n' | \
  gcloud secrets versions add edit-token-secret \
    --project=douga-2f6f8 \
    --data-file=-
```

---

### ステップ 2 — Secret Manager 注入を有効化して Cloud Run に切り替え

1. ステップ 0 の IAM バインディング付与が完了していること。
2. 全シークレットにバージョンが追加されていること（`gcloud secrets versions list` で確認）。
3. 以下のコマンドで `USE_SECRET_MANAGER=1` を指定してデプロイ:

```bash
cd /path/to/douga_root/main/backend
USE_SECRET_MANAGER=1 ./scripts/deploy_prod.sh
```

`deploy_prod.sh` は `--set-secrets` フラグで 4 つのシークレットを注入します。  
既存の env var（`env.yaml` 由来）との並立は問題ありません（Secret Manager 値が優先）。

4. デプロイ後にヘルスチェックが通ったら `env.yaml` から同名の変数を削除してフェーズ 2 完了。

---

### シークレットのローテーション手順

#### AI_KEY_ENCRYPTION_KEY のローテーション

> **重要**: 鍵を変更すると暗号化済みの `ai_api_key` が復号できなくなります。  
> ローテーション前に `scripts/encrypt_ai_keys.py --dry-run` を実行して影響範囲を確認してください。

> **⚠️ 未実装・将来対応**: 旧鍵→新鍵の再暗号化スクリプト (`scripts/reencrypt_ai_keys.py`) は
> 現時点で **未実装** です。鍵ローテーションを実施する前に必ずこのスクリプトを実装してください。
> （旧鍵で復号 → 新鍵で再暗号化する一括処理。`encrypt_ai_keys.py` をベースに
> `AI_KEY_ENCRYPTION_KEY_OLD` / `AI_KEY_ENCRYPTION_KEY` の 2 鍵対応にする想定）
> それまでの間、`ai-key-encryption-key` のバージョンを増やしても **旧バージョンを無効化しない** こと。

```bash
# 1. 新しい 32byte 鍵を生成
NEW_KEY=$(openssl rand -base64 32)

# 2. Secret Manager に新バージョンを追加（古いバージョンは残す）
printf '%s' "${NEW_KEY}" | \
  gcloud secrets versions add ai-key-encryption-key \
    --project=douga-2f6f8 \
    --data-file=-

# 3. 旧鍵 → 新鍵で全行を再暗号化（scripts/reencrypt_ai_keys.py — 未実装、上記注意参照）

# 4. Cloud Run に新鍵を適用（デプロイ）
USE_SECRET_MANAGER=1 ./scripts/deploy_prod.sh

# 5. 古いバージョンを無効化（再暗号化完了を確認してから実施すること）
gcloud secrets versions disable <VERSION_ID> \
  --secret=ai-key-encryption-key \
  --project=douga-2f6f8
```

#### OpenAI API キーのローテーション（人間が実施）

> この作業は **人間（プロジェクトオーナー）のみ** が実施できます。エージェントは新キーを発行できません。

1. OpenAI Platform (https://platform.openai.com/api-keys) にログイン
2. 旧キーを **Revoke** する前に新キーを発行（サービス断ゼロ）
3. 新キーを Secret Manager に登録:

```bash
printf '%s' "sk-new-key-here" | \
  gcloud secrets versions add openai-api-key \
    --project=douga-2f6f8 \
    --data-file=-
```

4. `USE_SECRET_MANAGER=1` でデプロイしてヘルスチェック確認
5. 旧バージョンを無効化:

```bash
gcloud secrets versions disable 1 \
  --secret=openai-api-key \
  --project=douga-2f6f8
```

6. 旧キーを OpenAI Platform で Revoke

---

### ai_api_key 暗号化について（アプリケーション層）

`project.ai_api_key` (ユーザーが設定するプロジェクト別 AI キー) は DB 保存時に AES-256-GCM で暗号化されます。

- 暗号化モジュール: `backend/src/utils/field_encryption.py`
- フォーマット: `enc:v1:<base64(4byte-nonce-len + 12byte-nonce + ciphertext)>`
- 鍵: `AI_KEY_ENCRYPTION_KEY` 環境変数（32byte base64）
- 未設定時: 平文保存（警告ログを出力）。ローカル開発用後方互換。

**既存平文データの透過移行**:
- 読み出し時: `enc:v1:` プレフィックスを検出 → 復号、それ以外 → 平文として使用
- 次回保存時: 暗号化して永続化（書き込み透過移行）
- 一括移行: `scripts/encrypt_ai_keys.py`（`--dry-run` 付きで実行して確認してから live 実行）

```bash
# ドライラン
DATABASE_URL=... AI_KEY_ENCRYPTION_KEY=... \
  uv run python scripts/encrypt_ai_keys.py --dry-run

# 実行
DATABASE_URL=... AI_KEY_ENCRYPTION_KEY=... \
  uv run python scripts/encrypt_ai_keys.py
```

---

The backend initialises Sentry **only when the `SENTRY_DSN` environment variable is set**. When the variable is absent (local development, CI) Sentry is completely disabled — no network calls are made.

### Step 1 — Create a Sentry project and obtain the DSN

1. Log in to https://sentry.io and open (or create) the `douga` organisation.
2. Create a new project → **Python**.
3. Copy the DSN from the project settings (format: `https://<key>@o<id>.ingest.sentry.io/<id>`).

### Step 2 — Store the DSN in Secret Manager

```bash
echo -n "https://YOUR_DSN_HERE" | \
  gcloud secrets create sentry-dsn \
    --project=douga-2f6f8 \
    --replication-policy=automatic \
    --data-file=-
```

If the secret already exists, add a new version:

```bash
echo -n "https://YOUR_DSN_HERE" | \
  gcloud secrets versions add sentry-dsn \
    --project=douga-2f6f8 \
    --data-file=-
```

Grant the Cloud Run service account read access:

```bash
gcloud secrets add-iam-policy-binding sentry-dsn \
  --project=douga-2f6f8 \
  --member="serviceAccount:344056413972-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### Step 3 — Inject the DSN into Cloud Run

Add `--set-secrets` to the `gcloud run services update` call inside `deploy_prod.sh`, or apply it once manually:

```bash
gcloud run services update douga-api \
  --region=asia-northeast1 \
  --project=douga-2f6f8 \
  --set-secrets="SENTRY_DSN=sentry-dsn:latest"
```

After this, the next deploy will automatically pick up the secret because `deploy_prod.sh` uses `--update-env-vars` (which does not clear secrets set separately).

### Configuration

| Setting | Value | Notes |
|---|---|---|
| `traces_sample_rate` | `0.1` | 10% of requests traced (adjust in `main.py` if needed) |
| `profiles_sample_rate` | `0.1` | 10% of sampled transactions profiled |
| `environment` | from `ENVIRONMENT` env var | `production` in Cloud Run |
| `release` | from `GIT_HASH` env var | set automatically by `deploy_prod.sh` |

---

## Health Checks

The backend exposes two health endpoints:

| Endpoint | Purpose | DB check | Use for |
|---|---|---|---|
| `GET /health/live` | Liveness | No | HTTP liveness probe (never fails due to DB) |
| `GET /health` | Readiness/smoke | Yes (2s timeout) | Uptime monitoring, smoke test after deploy |

### Current Cloud Run probe configuration

The Cloud Run startup probe is **TCP-only** on port 8000. No HTTP liveness probe is configured.

**Recommendation**: If an HTTP liveness probe is added in the future, point it at `/health/live`. Do **not** use `/health` as a liveness probe — a DB hiccup would cause Cloud Run to restart all instances in a loop.

`/health` should be used as a readiness or uptime check, where a 503 response triggers an alert but not a restart.

### Example responses

Healthy:

```json
{"status":"healthy","version":"0.1.0","git_hash":"abc123","db":true}
```

DB unreachable (503):

```json
{"detail":{"status":"degraded","db":"unreachable","error":"..."}}
```

---

## Troubleshooting

### View recent Cloud Run logs

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="douga-api"' \
  --project=douga-2f6f8 \
  --limit=50 \
  --freshness=30m \
  --format="table(timestamp,jsonPayload.severity,jsonPayload.message)"
```

### Check the currently active revision

```bash
gcloud run services describe douga-api \
  --region=asia-northeast1 \
  --project=douga-2f6f8 \
  --format="value(status.traffic[].revisionName,status.traffic[].percent)"
```
