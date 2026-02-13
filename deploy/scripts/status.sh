#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-hk-tick-collector}"
FUTU_SERVICE_NAME="${FUTU_SERVICE_NAME:-futu-opend}"
DATA_ROOT="${DATA_ROOT:-/data/sqlite/HK}"
DAY_DEFAULT="$(TZ=Asia/Hong_Kong date +%Y%m%d)"
DB_PATH="${DB_PATH:-${DATA_ROOT}/${DAY_DEFAULT}.db}"

echo "== HK Tick 一鍵狀態 =="

if command -v systemctl >/dev/null 2>&1; then
  echo "[服務] ${SERVICE_NAME}: $(systemctl is-active "${SERVICE_NAME}" 2>/dev/null || echo unknown)"
  echo "[服務] ${FUTU_SERVICE_NAME}: $(systemctl is-active "${FUTU_SERVICE_NAME}" 2>/dev/null || echo unknown)"
fi

if command -v df >/dev/null 2>&1; then
  echo "[磁碟]"
  df -h "${DATA_ROOT}" | sed -n '1,2p'
fi

if [[ -f "${DB_PATH}" ]] && command -v sqlite3 >/dev/null 2>&1; then
  echo "[資料庫] ${DB_PATH}"
  sqlite3 "file:${DB_PATH}?mode=ro" "SELECT COUNT(*) AS rows, datetime(MAX(ts_ms)/1000,'unixepoch') AS latest_utc, ROUND(strftime('%s','now') - MAX(ts_ms)/1000.0, 3) AS drift_sec FROM ticks;"
else
  echo "[資料庫] 找不到 ${DB_PATH}"
fi

if command -v journalctl >/dev/null 2>&1; then
  echo "[近期告警]"
  journalctl -u "${SERVICE_NAME}" --since "20 minutes ago" --no-pager \
    | grep -E "WATCHDOG|ALERT|telegram_send_failed|sqlite_busy|ERROR" \
    | tail -n 20 || true
fi
