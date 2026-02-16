#!/usr/bin/env bash
# Dispatcher: run batch_analyze or price_match based on mode file.
# Called by cron */10 13-23. Mode file switches when no unscored ads remain.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE_FILE="/tmp/adora_mode"
MODE=$(cat "$MODE_FILE" 2>/dev/null || echo "analyze")

if [[ "$MODE" == "price_match" ]]; then
    echo "[$(date -Is)] Dispatch: price_match mode"
    exec bash "${SCRIPT_DIR}/run_price_match.sh"
else
    echo "[$(date -Is)] Dispatch: analyze mode"
    exec /usr/bin/python3 "${SCRIPT_DIR}/batch_analyze_ads.py"
fi
