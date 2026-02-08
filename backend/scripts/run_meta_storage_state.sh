#!/usr/bin/env bash
set -euo pipefail

# Refresh Meta Ads Library storage_state (cookies/localStorage) used by Playwright scraping.
# This is important because Meta often returns 403 for automated sessions unless a prior
# browser state exists (even without logging in).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

LOG_DIR="${META_DAILY_LOG_DIR:-/home/ubuntu/adora_ops/logs/meta_daily}"
mkdir -p "${LOG_DIR}"

RUN_UTC="$(date -u +"%Y%m%d_%H%M%S")"
LOG_FILE="${LOG_DIR}/meta_storage_state_${RUN_UTC}.log"

cleanup_playwright_orphans() {
  pkill -9 chrome-headless-shell >/dev/null 2>&1 || true
  pkill -9 chrome >/dev/null 2>&1 || true
  pkill -9 -f "/playwright/driver/package/cli.js run-driver" >/dev/null 2>&1 || true
}

cleanup_playwright_orphans

if [[ -n "${META_DAILY_PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="${META_DAILY_PYTHON_BIN}"
elif [[ -x "${REPO_ROOT}/sandbox/venv/bin/python" ]]; then
  PYTHON_BIN="${REPO_ROOT}/sandbox/venv/bin/python"
elif [[ -x "${REPO_ROOT}/venv/bin/python" ]]; then
  PYTHON_BIN="${REPO_ROOT}/venv/bin/python"
else
  PYTHON_BIN="python3"
fi

STORAGE_STATE_PATH="${META_DAILY_STORAGE_STATE:-/home/ubuntu/adora_ops/meta_storage_state.json}"
MAX_WAIT_SEC="${META_DAILY_STORAGE_STATE_MAX_WAIT_SEC:-45}"

CMD=(
  "${PYTHON_BIN}"
  "${REPO_ROOT}/backend/scripts/create_meta_storage_state.py"
  "--output" "${STORAGE_STATE_PATH}"
  "--max-wait-sec" "${MAX_WAIT_SEC}"
  "--poll-sec" "2"
)

echo "[$(date -Is)] Refreshing Meta storage_state" | tee -a "${LOG_FILE}"
echo "[$(date -Is)] Output: ${STORAGE_STATE_PATH}" | tee -a "${LOG_FILE}"
echo "[$(date -Is)] Max wait: ${MAX_WAIT_SEC}s" | tee -a "${LOG_FILE}"
echo "[$(date -Is)] Command: ${CMD[*]}" | tee -a "${LOG_FILE}"

RUNNER=( "${CMD[@]}" )
if [[ "${META_DAILY_USE_XVFB:-0}" == "1" ]]; then
  if command -v xvfb-run >/dev/null 2>&1; then
    RUNNER=( xvfb-run -a "${CMD[@]}" )
  else
    echo "[$(date -Is)] Warning: META_DAILY_USE_XVFB=1 but xvfb-run is not installed." | tee -a "${LOG_FILE}"
  fi
fi

# Hard cap so this can't stall the nightly pipeline.
timeout --signal=TERM 180 "${RUNNER[@]}" 2>&1 | tee -a "${LOG_FILE}"

cleanup_playwright_orphans

if [[ -s "${STORAGE_STATE_PATH}" ]]; then
  echo "[$(date -Is)] storage_state refreshed (bytes=$(wc -c < "${STORAGE_STATE_PATH}"))" | tee -a "${LOG_FILE}"
else
  echo "[$(date -Is)] Warning: storage_state not created or empty: ${STORAGE_STATE_PATH}" | tee -a "${LOG_FILE}"
  exit 3
fi

