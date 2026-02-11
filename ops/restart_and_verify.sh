#!/usr/bin/env bash
set -euo pipefail

# Legacy entrypoint kept for compatibility.
# For day-to-day checks prefer: scripts/healthcheck.sh + scripts/verify_db.sh

SERVICE_NAME="${SERVICE_NAME:-hk-tick-collector}"
DATA_ROOT="${DATA_ROOT:-/data/sqlite/HK}"
VERIFY_TIMEOUT_SEC="${VERIFY_TIMEOUT_SEC:-30}"
TS_TOLERANCE_SEC="${TS_TOLERANCE_SEC:-5}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK_SCRIPT="${CHECK_SCRIPT:-${ROOT_DIR}/scripts/check_ts_semantics.py}"
DAY="${DAY:-$(TZ=Asia/Hong_Kong date +%Y%m%d)}"
DB_PATH="${DB_PATH:-${DATA_ROOT}/${DAY}.db}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "[FAIL] please run as root"
  exit 1
fi

if [[ ! -f "${CHECK_SCRIPT}" ]]; then
  echo "[FAIL] missing check script: ${CHECK_SCRIPT}"
  exit 1
fi

echo "[INFO] service=${SERVICE_NAME}"
echo "[INFO] db_path=${DB_PATH}"
echo "[STEP] stop ${SERVICE_NAME}"
systemctl stop "${SERVICE_NAME}" || true

echo "[STEP] start ${SERVICE_NAME}"
START_UTC="$(date -u +'%Y-%m-%d %H:%M:%S')"
systemctl start "${SERVICE_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}" | sed -n '1,20p'

echo "[STEP] wait for persist_ticks (<=${VERIFY_TIMEOUT_SEC}s)"
persist_seen=0
for ((i = 0; i < VERIFY_TIMEOUT_SEC; i++)); do
  LOGS="$(journalctl -u "${SERVICE_NAME}" --since "${START_UTC}" --no-pager || true)"
  if echo "${LOGS}" | grep -q "persist_ticks"; then
    persist_seen=1
    break
  fi
  sleep 1
done

if [[ "${persist_seen}" != "1" ]]; then
  echo "[FAIL] persist_ticks not found within ${VERIFY_TIMEOUT_SEC}s"
  exit 1
fi

echo "[STEP] verify ts semantics (max_minus_now_sec within +/-${TS_TOLERANCE_SEC}s)"
python3 "${CHECK_SCRIPT}" --db "${DB_PATH}" --tolerance-sec "${TS_TOLERANCE_SEC}"

echo "[STEP] verify watchdog is clean"
WATCHDOG_COUNT="$(journalctl -u "${SERVICE_NAME}" --since "${START_UTC}" --no-pager | grep -c "WATCHDOG persistent_stall" || true)"
echo "watchdog_persistent_stall_since_restart=${WATCHDOG_COUNT}"
if [[ "${WATCHDOG_COUNT}" != "0" ]]; then
  echo "[FAIL] detected WATCHDOG persistent_stall after restart"
  exit 1
fi

echo "[OK] restart and verify passed"
