#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${SCRIPT_DIR}/configs/meta_keywords"
RUN_SCRIPT="${SCRIPT_DIR}/run_meta_keyword_job.sh"
STATE_SCRIPT="${SCRIPT_DIR}/run_meta_storage_state.sh"
STORAGE_STATE_PATH="/home/ubuntu/adora_ops/meta_storage_state.json"

if [[ ! -x "${RUN_SCRIPT}" ]]; then
  chmod +x "${RUN_SCRIPT}"
fi
if [[ ! -x "${STATE_SCRIPT}" ]]; then
  chmod +x "${STATE_SCRIPT}"
fi

declare -a CONFIG_FILES=(
  "${CONFIG_DIR}/01_mivtsa.json"
  "${CONFIG_DIR}/02_mugbal.json"
  "${CONFIG_DIR}/03_hanaha.json"
  "${CONFIG_DIR}/04_shaot.json"
  "${CONFIG_DIR}/05_achshav.json"
)

for file in "${CONFIG_FILES[@]}"; do
  if [[ ! -f "${file}" ]]; then
    echo "Missing config file: ${file}"
    exit 2
  fi
done

mkdir -p /home/ubuntu/adora_ops/logs/meta_daily
mkdir -p /home/ubuntu/adora_ops/meta_daily_output

TMP_CRON="$(mktemp)"
(
  crontab -l 2>/dev/null || true
) | grep -v "adora_meta_kw_" | grep -v "adora_meta_storage_state" > "${TMP_CRON}"

cat >> "${TMP_CRON}" <<EOF
# Headless is dramatically more stable on small VMs; storage_state is required to avoid Meta 403s.
1 0 * * * META_DAILY_STORAGE_STATE=${STORAGE_STATE_PATH} /bin/bash "${RUN_SCRIPT}" "${CONFIG_DIR}/01_mivtsa.json" "meta_kw_01_mivtsa" # adora_meta_kw_01
0 1 * * * META_DAILY_STORAGE_STATE=${STORAGE_STATE_PATH} /bin/bash "${RUN_SCRIPT}" "${CONFIG_DIR}/02_mugbal.json" "meta_kw_02_mugbal" # adora_meta_kw_02
0 2 * * * META_DAILY_STORAGE_STATE=${STORAGE_STATE_PATH} /bin/bash "${RUN_SCRIPT}" "${CONFIG_DIR}/03_hanaha.json" "meta_kw_03_hanaha" # adora_meta_kw_03
0 3 * * * META_DAILY_STORAGE_STATE=${STORAGE_STATE_PATH} /bin/bash "${RUN_SCRIPT}" "${CONFIG_DIR}/04_shaot.json" "meta_kw_04_shaot" # adora_meta_kw_04
0 4 * * * META_DAILY_STORAGE_STATE=${STORAGE_STATE_PATH} /bin/bash "${RUN_SCRIPT}" "${CONFIG_DIR}/05_achshav.json" "meta_kw_05_achshav" # adora_meta_kw_05
EOF

crontab "${TMP_CRON}"
rm -f "${TMP_CRON}"

echo "Installed Meta keyword cron jobs:"
crontab -l | grep "adora_meta_kw_"
