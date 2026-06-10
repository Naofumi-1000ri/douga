# Deployment Operations Guide

This document covers production deployment, rollback, Sentry setup, and related ops procedures for the Douga backend.

For the full step-by-step deploy workflow see [`.claude/commands/deploy.md`](../../.claude/commands/deploy.md).

---

## Table of Contents

1. [Normal Deployment](#normal-deployment)
2. [Rollback](#rollback)
3. [Sentry Error Tracking](#sentry-error-tracking)
4. [Health Checks](#health-checks)
5. [Troubleshooting](#troubleshooting)

---

## Normal Deployment

Production deploys are always performed through `backend/scripts/deploy_prod.sh`.

```bash
cd /path/to/douga_root/main/backend
./scripts/deploy_prod.sh
```

The script guards against deploying from the wrong branch, a dirty tree, or pointing at a non-production target. See [`.claude/commands/deploy.md`](../../.claude/commands/deploy.md) for the full procedure.

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

## Sentry Error Tracking

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
