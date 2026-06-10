#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
BACKEND_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd -- "${BACKEND_DIR}/.." && pwd)"

PROJECT_ID="${PROJECT_ID:-douga-2f6f8}"
REGION="${REGION:-asia-northeast1}"
REPOSITORY="${REPOSITORY:-cloud-run-source-deploy}"
IMAGE_NAME="${IMAGE_NAME:-douga-api}"
SERVICE_NAME="${SERVICE_NAME:-douga-api}"
SERVICE_URL="${SERVICE_URL:-https://douga-api-344056413972.asia-northeast1.run.app}"
MAX_SCALE="${MAX_SCALE:-4}"
DRY_RUN="${DRY_RUN:-0}"
ALLOW_NON_DOUGA_TARGET="${ALLOW_NON_DOUGA_TARGET:-0}"
ALLOW_NON_MAIN_DEPLOY="${ALLOW_NON_MAIN_DEPLOY:-0}"
ALLOW_DIRTY_DEPLOY="${ALLOW_DIRTY_DEPLOY:-0}"

EXPECTED_REMOTE_HTTPS="https://github.com/Naofumi-1000ri/douga.git"
EXPECTED_REMOTE_HTTPS_NO_GIT="https://github.com/Naofumi-1000ri/douga"
EXPECTED_REMOTE_SSH="git@github.com:Naofumi-1000ri/douga.git"
EXPECTED_REMOTE_SSH_NO_GIT="git@github.com:Naofumi-1000ri/douga"
EXPECTED_BRANCH="main"
EXPECTED_PROJECT_ID="douga-2f6f8"
EXPECTED_REGION="asia-northeast1"
EXPECTED_REPOSITORY="cloud-run-source-deploy"
EXPECTED_IMAGE_NAME="douga-api"
EXPECTED_SERVICE_NAME="douga-api"
EXPECTED_SERVICE_URL="https://douga-api-344056413972.asia-northeast1.run.app"

assert_expected_target() {
  local name="$1"
  local actual="$2"
  local expected="$3"

  if [[ "${actual}" == "${expected}" ]]; then
    return 0
  fi

  if [[ "${DRY_RUN}" == "1" && "${ALLOW_NON_DOUGA_TARGET}" == "1" ]]; then
    echo "WARNING: ${name} is ${actual}; expected ${expected}; DRY_RUN=1 permits this override" >&2
    return 0
  fi

  echo "deploy blocked: ${name} must be ${expected}, got ${actual}" >&2
  echo "Target overrides are allowed only with DRY_RUN=1." >&2
  exit 1
}

assert_expected_target "PROJECT_ID" "${PROJECT_ID}" "${EXPECTED_PROJECT_ID}"
assert_expected_target "REGION" "${REGION}" "${EXPECTED_REGION}"
assert_expected_target "REPOSITORY" "${REPOSITORY}" "${EXPECTED_REPOSITORY}"
assert_expected_target "IMAGE_NAME" "${IMAGE_NAME}" "${EXPECTED_IMAGE_NAME}"
assert_expected_target "SERVICE_NAME" "${SERVICE_NAME}" "${EXPECTED_SERVICE_NAME}"
assert_expected_target "SERVICE_URL" "${SERVICE_URL}" "${EXPECTED_SERVICE_URL}"

if ! GIT_TOP_LEVEL="$(git -C "${REPO_ROOT}" rev-parse --show-toplevel 2>/dev/null)"; then
  echo "deploy blocked: failed to resolve git top-level from ${REPO_ROOT}" >&2
  exit 1
fi

if [[ "${GIT_TOP_LEVEL}" != "${REPO_ROOT}" ]]; then
  echo "deploy blocked: git top-level must be ${REPO_ROOT}, got ${GIT_TOP_LEVEL}" >&2
  exit 1
fi

if ! REMOTE_URL="$(git -C "${REPO_ROOT}" remote get-url origin 2>/dev/null)"; then
  echo "deploy blocked: failed to resolve git origin remote" >&2
  exit 1
fi

if [[ "${REMOTE_URL}" != "${EXPECTED_REMOTE_HTTPS}" \
  && "${REMOTE_URL}" != "${EXPECTED_REMOTE_HTTPS_NO_GIT}" \
  && "${REMOTE_URL}" != "${EXPECTED_REMOTE_SSH}" \
  && "${REMOTE_URL}" != "${EXPECTED_REMOTE_SSH_NO_GIT}" ]]; then
  echo "deploy blocked: git origin must be Naofumi-1000ri/douga, got ${REMOTE_URL}" >&2
  exit 1
fi

BRANCH="$(git -C "${REPO_ROOT}" branch --show-current)"
ALLOW_NON_MAIN_CHECK=0
if [[ "${DRY_RUN}" == "1" && "${ALLOW_NON_MAIN_DEPLOY}" == "1" ]]; then
  ALLOW_NON_MAIN_CHECK=1
fi

if [[ "${BRANCH}" != "${EXPECTED_BRANCH}" && "${ALLOW_NON_MAIN_CHECK}" != "1" ]]; then
  echo "deploy blocked: branch must be ${EXPECTED_BRANCH}, got ${BRANCH:-<detached>}" >&2
  echo "Non-main checks are allowed only with DRY_RUN=1 and ALLOW_NON_MAIN_DEPLOY=1." >&2
  exit 1
fi

ALLOW_DIRTY_CHECK=0
if [[ "${DRY_RUN}" == "1" && "${ALLOW_DIRTY_DEPLOY}" == "1" ]]; then
  ALLOW_DIRTY_CHECK=1
fi

if [[ -n "$(git -C "${REPO_ROOT}" status --porcelain)" && "${ALLOW_DIRTY_CHECK}" != "1" ]]; then
  echo "deploy blocked: working tree is dirty" >&2
  echo "Dirty-tree checks are allowed only with DRY_RUN=1 and ALLOW_DIRTY_DEPLOY=1." >&2
  exit 1
fi

if [[ "${BRANCH}" == "${EXPECTED_BRANCH}" ]]; then
  git -C "${REPO_ROOT}" fetch origin main
  HEAD_SHA="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
  ORIGIN_MAIN_SHA="$(git -C "${REPO_ROOT}" rev-parse origin/main)"
  if [[ "${HEAD_SHA}" != "${ORIGIN_MAIN_SHA}" ]]; then
    echo "deploy blocked: HEAD must match origin/main" >&2
    echo "  HEAD:        ${HEAD_SHA}" >&2
    echo "  origin/main: ${ORIGIN_MAIN_SHA}" >&2
    exit 1
  fi
fi

if ! GIT_HASH="$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null)"; then
  echo "failed to resolve git hash from ${REPO_ROOT}" >&2
  exit 1
fi

IMAGE_TAG="${IMAGE_TAG:-${GIT_HASH}}"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:${IMAGE_TAG}"

# ── Secret Manager injection ────────────────────────────────────────────────
# When USE_SECRET_MANAGER=1 (opt-in), secrets are injected via --set-secrets
# instead of (or in addition to) env.yaml variables.
#
# Transition strategy:
#   Phase 1 (current): env.yaml env vars still work; Secret Manager is additive.
#   Phase 2 (future):  remove env.yaml references after verifying Secret Manager
#                      injection is stable in production.
#
# Secrets must exist in Secret Manager before enabling this flag:
#   ai-key-encryption-key  → AI_KEY_ENCRYPTION_KEY
#   openai-api-key         → OPENAI_API_KEY
#   database-url           → DATABASE_URL
#   edit-token-secret      → EDIT_TOKEN_SECRET
#
# Grant Cloud Run SA access first:
#   SA="344056413972-compute@developer.gserviceaccount.com"
#   for s in ai-key-encryption-key openai-api-key database-url edit-token-secret; do
#     gcloud secrets add-iam-policy-binding "$s" \
#       --project=douga-2f6f8 --member="serviceAccount:${SA}" \
#       --role="roles/secretmanager.secretAccessor"
#   done
USE_SECRET_MANAGER="${USE_SECRET_MANAGER:-0}"

# NOTE: --set-secrets is a "set/replace" flag — repeating it makes gcloud keep
# only the LAST occurrence. All mappings MUST be passed in a single
# comma-separated flag, otherwise three of the four secrets would be dropped.
_SECRET_FLAGS=()
if [[ "${USE_SECRET_MANAGER}" == "1" ]]; then
  _SECRET_FLAGS+=(
    "--set-secrets=AI_KEY_ENCRYPTION_KEY=ai-key-encryption-key:latest,OPENAI_API_KEY=openai-api-key:latest,DATABASE_URL=database-url:latest,EDIT_TOKEN_SECRET=edit-token-secret:latest"
  )
fi

run() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '+'
    for arg in "$@"; do
      printf ' %q' "${arg}"
    done
    printf '\n'
    return 0
  fi

  "$@"
}

echo "backend deploy target"
echo "  repo: ${REPO_ROOT}"
echo "  branch: ${BRANCH}"
echo "  git hash: ${GIT_HASH}"
echo "  image: ${IMAGE_URI}"
echo "  service: ${SERVICE_NAME}"
echo "  max scale: ${MAX_SCALE}"

cd "${BACKEND_DIR}"

run docker build \
  --platform linux/amd64 \
  --build-arg "GIT_HASH=${GIT_HASH}" \
  -t "${IMAGE_URI}" \
  .

run docker push "${IMAGE_URI}"

run gcloud run services update "${SERVICE_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --image="${IMAGE_URI}" \
  --max="${MAX_SCALE}" \
  --update-env-vars "GIT_HASH=${GIT_HASH}" \
  "${_SECRET_FLAGS[@]+"${_SECRET_FLAGS[@]}"}"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  exit 0
fi

echo
echo "health"
HEALTH_JSON="$(curl -fsS "${SERVICE_URL}/health")"
printf '%s\n' "${HEALTH_JSON}"

EXPECTED_GIT_HASH="${GIT_HASH}" HEALTH_JSON="${HEALTH_JSON}" python3 - <<'PY'
import json
import os
import sys

payload = json.loads(os.environ["HEALTH_JSON"])
expected = os.environ["EXPECTED_GIT_HASH"]

if payload.get("status") != "healthy":
    print(f"unexpected health status: {payload.get('status')!r}", file=sys.stderr)
    sys.exit(1)

if payload.get("git_hash") != expected:
    print(
        "backend health git_hash does not match deployed commit: "
        f"expected {expected}, got {payload.get('git_hash')}",
        file=sys.stderr,
    )
    sys.exit(1)
PY
echo
