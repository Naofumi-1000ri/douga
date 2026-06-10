#!/usr/bin/env bash
# rollback_prod.sh — Roll back Cloud Run douga-api to a previous revision.
#
# Usage:
#   ./rollback_prod.sh                   # auto-detect the previous revision and prompt
#   ./rollback_prod.sh --yes             # auto-detect, no confirmation prompt
#   ./rollback_prod.sh <REVISION_NAME>   # explicit revision, prompt
#   ./rollback_prod.sh <REVISION_NAME> --yes  # explicit revision, no prompt
#
# The script sends 100% of traffic to the target revision.
# It does NOT redeploy a new image; it only adjusts traffic weights.
#
# Requirements: gcloud CLI, authenticated and pointing at douga-2f6f8.
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-douga-api}"
REGION="${REGION:-asia-northeast1}"
PROJECT_ID="${PROJECT_ID:-douga-2f6f8}"

YES=0
TARGET_REVISION=""

for arg in "$@"; do
  case "${arg}" in
    --yes|-y)
      YES=1
      ;;
    --*)
      echo "Unknown flag: ${arg}" >&2
      exit 1
      ;;
    *)
      if [[ -n "${TARGET_REVISION}" ]]; then
        echo "Too many positional arguments (got '${TARGET_REVISION}' and '${arg}')" >&2
        exit 1
      fi
      TARGET_REVISION="${arg}"
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Auto-detect the previous revision when none is supplied.
# "Previous" = the second-most-recent revision (i.e. one step before latest).
# ---------------------------------------------------------------------------
if [[ -z "${TARGET_REVISION}" ]]; then
  echo "No revision specified — detecting the previous revision..."

  LATEST_REVISION="$(
    gcloud run revisions list \
      --service="${SERVICE_NAME}" \
      --region="${REGION}" \
      --project="${PROJECT_ID}" \
      --format="value(metadata.name)" \
      --sort-by="~metadata.creationTimestamp" \
      --limit=2 \
    | tail -n 1
  )"

  if [[ -z "${LATEST_REVISION}" ]]; then
    echo "ERROR: Could not determine the previous revision." >&2
    echo "Please specify the target revision explicitly:" >&2
    echo "  $(basename "$0") <REVISION_NAME>" >&2
    exit 1
  fi

  TARGET_REVISION="${LATEST_REVISION}"
  echo "Auto-detected previous revision: ${TARGET_REVISION}"
fi

# ---------------------------------------------------------------------------
# Confirmation prompt (skip with --yes).
# ---------------------------------------------------------------------------
echo ""
echo "======================================================="
echo "  ROLLBACK PLAN"
echo "======================================================="
echo "  Service  : ${SERVICE_NAME}"
echo "  Region   : ${REGION}"
echo "  Project  : ${PROJECT_ID}"
echo "  Target   : ${TARGET_REVISION} → 100% traffic"
echo "======================================================="
echo ""

if [[ "${YES}" -eq 0 ]]; then
  read -rp "Proceed? [y/N] " CONFIRM
  if [[ "${CONFIRM}" != "y" && "${CONFIRM}" != "Y" ]]; then
    echo "Aborted."
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# Execute rollback.
# ---------------------------------------------------------------------------
echo "Rolling back to ${TARGET_REVISION}..."

gcloud run services update-traffic "${SERVICE_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --to-revisions="${TARGET_REVISION}=100"

echo ""
echo "Rollback complete. Current traffic routing:"
gcloud run services describe "${SERVICE_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format="table(status.traffic[].revisionName,status.traffic[].percent)"
