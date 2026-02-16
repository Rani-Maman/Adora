#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LOG_DIR="/home/ubuntu/adora_ops/logs/price_match"
DOTENV_PATH="/home/ubuntu/adora_ops/.env"
LOCK_FILE="/tmp/price_match.lock"
MAX_RUNTIME="${PRICE_MATCH_MAX_RUNTIME:-6600}"

mkdir -p "${LOG_DIR}"

# flock — prevent concurrent runs
exec 200>"${LOCK_FILE}"
if ! flock -n 200; then
  echo "[$(date -Is)] Price match already running. Exiting."
  exit 0
fi

# Find python
if [[ -x "/home/ubuntu/adora_ops/venv/bin/python" ]]; then
  PYTHON_BIN="/home/ubuntu/adora_ops/venv/bin/python"
elif [[ -x "${REPO_ROOT}/venv/bin/python" ]]; then
  PYTHON_BIN="${REPO_ROOT}/venv/bin/python"
else
  PYTHON_BIN="python3"
fi

RUN_UTC="$(date -u +"%Y%m%d_%H%M%S")"
LOG_FILE="${LOG_DIR}/price_match_${RUN_UTC}.log"

echo "[$(date -Is)] Starting price match (max-runtime=${MAX_RUNTIME}s)"
echo "[$(date -Is)] Log: ${LOG_FILE}"

set +e
timeout --signal=TERM "${MAX_RUNTIME}" \
  "${PYTHON_BIN}" "${SCRIPT_DIR}/batch_price_match.py" \
    --max-runtime "${MAX_RUNTIME}" \
    --dotenv-path "${DOTENV_PATH}" \
    "$@" \
  2>&1 | tee -a "${LOG_FILE}"
status=${PIPESTATUS[0]}

if [[ ${status} -eq 124 ]]; then
  echo "[$(date -Is)] Hard timeout hit (${MAX_RUNTIME}s)." | tee -a "${LOG_FILE}"
fi
set -e

echo "[$(date -Is)] Finished price match with exit=${status}"
exit "${status}"
