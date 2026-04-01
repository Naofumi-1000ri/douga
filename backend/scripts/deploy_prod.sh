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

if ! GIT_HASH="$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null)"; then
  echo "failed to resolve git hash from ${REPO_ROOT}" >&2
  exit 1
fi

IMAGE_TAG="${IMAGE_TAG:-${GIT_HASH}}"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:${IMAGE_TAG}"

run() {
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
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
  --update-env-vars "GIT_HASH=${GIT_HASH}"

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
