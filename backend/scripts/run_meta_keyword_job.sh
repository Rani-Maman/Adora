#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <config-path> <job-name> [extra daily_meta_scrape args...]"
  exit 1
fi

CONFIG_PATH="$1"
shift
JOB_NAME="$1"
shift

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STATE_SCRIPT="${SCRIPT_DIR}/run_meta_storage_state.sh"

OUTPUT_DIR="${META_DAILY_OUTPUT_DIR:-/home/ubuntu/adora_ops/meta_daily_output}"
LOG_DIR="${META_DAILY_LOG_DIR:-/home/ubuntu/adora_ops/logs/meta_daily}"

if [[ -n "${META_DAILY_DOTENV_PATH:-}" ]]; then
  DOTENV_PATH="${META_DAILY_DOTENV_PATH}"
elif [[ -s "/home/ubuntu/.env" ]]; then
  DOTENV_PATH="/home/ubuntu/.env"
else
  DOTENV_PATH="/home/ubuntu/adora_ops/.env"
fi

mkdir -p "${OUTPUT_DIR}" "${LOG_DIR}"

LOCK_FILE="${META_DAILY_LOCK_FILE:-/tmp/meta_keyword_scrape.lock}"
exec 200>"${LOCK_FILE}"
if ! flock -n 200; then
  echo "[$(date -Is)] Another Meta keyword scrape is already running. Exiting."
  exit 0
fi

cleanup_playwright_orphans() {
  pkill -9 chrome-headless-shell >/dev/null 2>&1 || true
  pkill -9 chrome >/dev/null 2>&1 || true
  pkill -9 -f "/playwright/driver/package/cli.js run-driver" >/dev/null 2>&1 || true
}

# Protect low-memory VMs from stale browser processes between runs.
cleanup_playwright_orphans

DEFAULT_STORAGE_STATE="/home/ubuntu/adora_ops/meta_storage_state.json"
if [[ "${META_DAILY_HEADFUL:-0}" == "1" ]]; then
  # Meta often 403s headful Playwright unless an existing browser state (cookies) is provided.
  if [[ -z "${META_DAILY_STORAGE_STATE:-}" ]]; then
    export META_DAILY_STORAGE_STATE="${DEFAULT_STORAGE_STATE}"
  fi
  if [[ ! -s "${META_DAILY_STORAGE_STATE}" ]]; then
    echo "[$(date -Is)] storage_state missing; attempting refresh: ${META_DAILY_STORAGE_STATE}"
    if [[ -x "${STATE_SCRIPT}" ]]; then
      /bin/bash "${STATE_SCRIPT}" || true
    else
      echo "[$(date -Is)] Warning: state refresh script not found/executable: ${STATE_SCRIPT}"
    fi
  fi
fi

if [[ -n "${META_DAILY_PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="${META_DAILY_PYTHON_BIN}"
elif [[ -x "${REPO_ROOT}/sandbox/venv/bin/python" ]]; then
  PYTHON_BIN="${REPO_ROOT}/sandbox/venv/bin/python"
elif [[ -x "${REPO_ROOT}/venv/bin/python" ]]; then
  PYTHON_BIN="${REPO_ROOT}/venv/bin/python"
else
  PYTHON_BIN="python3"
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Config not found: ${CONFIG_PATH}"
  exit 2
fi

RUN_UTC="$(date -u +"%Y%m%d_%H%M%S")"
LOG_FILE="${LOG_DIR}/${JOB_NAME}_${RUN_UTC}.log"
HARD_TIMEOUT_SEC="${META_DAILY_HARD_TIMEOUT_SEC:-$(( ${META_DAILY_MAX_TOTAL_MINUTES:-32} * 60 + 900 ))}"

CMD=(
  "${PYTHON_BIN}"
  "${REPO_ROOT}/backend/scripts/daily_meta_scrape.py"
  "--config" "${CONFIG_PATH}"
  "--output-dir" "${OUTPUT_DIR}"
  "--dotenv-path" "${DOTENV_PATH}"
  "--job-name" "${JOB_NAME}"
  "--email-subject-prefix" "${META_DAILY_EMAIL_SUBJECT_PREFIX:-Meta Ads Keyword Nightly}"
  "--max-total-minutes" "${META_DAILY_MAX_TOTAL_MINUTES:-35}"
  "--per-link-timeout-sec" "${META_DAILY_PER_LINK_TIMEOUT_SEC:-1850}"
  "--retries" "${META_DAILY_RETRIES:-1}"
  "--min-selected-rows-per-link" "${META_DAILY_MIN_SELECTED_ROWS:-20}"
  "--max-runtime-sec" "${META_DAILY_MAX_RUNTIME_SEC:-1800}"
  "--target-ads-per-link" "${META_DAILY_TARGET_ADS:-7500}"
  "--max-advertisers-per-keyword" "${META_DAILY_MAX_ADVERTISERS:-0}"
  "--max-scrolls" "${META_DAILY_MAX_SCROLLS:-700}"
  "--idle-rounds" "${META_DAILY_IDLE_ROUNDS:-45}"
  "--scroll-delay-ms" "${META_DAILY_SCROLL_DELAY_MS:-700}"
  "--navigation-timeout-ms" "${META_DAILY_NAV_TIMEOUT_MS:-120000}"
  "--response-url-filter" "${META_DAILY_RESPONSE_FILTER:-facebook.com}"
  "--log-level" "${META_DAILY_LOG_LEVEL:-INFO}"
)

if [[ "${META_DAILY_IGNORE_DATE_FILTER:-1}" == "1" ]]; then
  CMD+=("--ignore-date-filter")
fi

if [[ "${META_DAILY_HEADFUL:-0}" == "1" ]]; then
  CMD+=("--headful")
fi

if [[ -n "${META_DAILY_STORAGE_STATE:-}" ]]; then
  CMD+=("--storage-state" "${META_DAILY_STORAGE_STATE}")
fi

if [[ -n "${META_DAILY_PROXY_SERVER:-}" ]]; then
  CMD+=("--proxy-server" "${META_DAILY_PROXY_SERVER}")
fi
if [[ -n "${META_DAILY_PROXY_USERNAME:-}" ]]; then
  CMD+=("--proxy-username" "${META_DAILY_PROXY_USERNAME}")
fi
if [[ -n "${META_DAILY_PROXY_BYPASS:-}" ]]; then
  CMD+=("--proxy-bypass" "${META_DAILY_PROXY_BYPASS}")
fi

if [[ $# -gt 0 ]]; then
  CMD+=("$@")
fi

RENDER_CMD=()
MASK_NEXT=0
for token in "${CMD[@]}"; do
  if [[ ${MASK_NEXT} -eq 1 ]]; then
    RENDER_CMD+=("***")
    MASK_NEXT=0
    continue
  fi
  if [[ "${token}" == "--proxy-password" ]]; then
    RENDER_CMD+=("--proxy-password")
    MASK_NEXT=1
    continue
  fi
  RENDER_CMD+=("${token}")
done

echo "[$(date -Is)] Starting ${JOB_NAME}"
echo "[$(date -Is)] Config: ${CONFIG_PATH}"
echo "[$(date -Is)] Output dir: ${OUTPUT_DIR}"
echo "[$(date -Is)] Log file: ${LOG_FILE}"
echo "[$(date -Is)] Hard timeout: ${HARD_TIMEOUT_SEC}s"
echo "[$(date -Is)] Command: ${RENDER_CMD[*]}"

RUNNER=( "${CMD[@]}" )
if [[ "${META_DAILY_USE_XVFB:-0}" == "1" ]]; then
  if command -v xvfb-run >/dev/null 2>&1; then
    RUNNER=( xvfb-run -a "${CMD[@]}" )
  else
    echo "[$(date -Is)] Warning: META_DAILY_USE_XVFB=1 but xvfb-run is not installed." | tee -a "${LOG_FILE}"
  fi
fi

set +e
timeout --signal=TERM "${HARD_TIMEOUT_SEC}" "${RUNNER[@]}" 2>&1 | tee -a "${LOG_FILE}"
status=${PIPESTATUS[0]}
if [[ ${status} -eq 124 ]]; then
  echo "[$(date -Is)] Hard timeout hit (${HARD_TIMEOUT_SEC}s)." | tee -a "${LOG_FILE}"
fi
set -e

cleanup_playwright_orphans

echo "[$(date -Is)] Finished ${JOB_NAME} with exit=${status}"
exit "${status}"
