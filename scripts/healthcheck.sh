#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME=${SERVICE_NAME:-hk-tick-collector}
DATA_ROOT=${DATA_ROOT:-/data/sqlite/HK}
DAY=${DAY:-$(TZ=Asia/Hong_Kong date +%Y%m%d)}
DB_PATH=${DB_PATH:-${DATA_ROOT}/${DAY}.db}
SAMPLE_SEC=${SAMPLE_SEC:-15}
MAX_LAG_SEC=${MAX_LAG_SEC:-30}
REQUIRE_GROWTH=${REQUIRE_GROWTH:-1}

if ! command -v systemctl >/dev/null 2>&1; then
  echo "[FAIL] systemctl not found" >&2
  exit 1
fi
if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "[FAIL] sqlite3 not found" >&2
  exit 1
fi

if [[ "$(systemctl is-active "${SERVICE_NAME}" || true)" != "active" ]]; then
  echo "[FAIL] service is not active: ${SERVICE_NAME}" >&2
  exit 1
fi

if [[ ! -f "${DB_PATH}" ]]; then
  echo "[FAIL] db not found: ${DB_PATH}" >&2
  exit 1
fi

rows_before=$(sqlite3 "file:${DB_PATH}?mode=ro" "SELECT COUNT(*) FROM ticks;")
max_ts_before=$(sqlite3 "file:${DB_PATH}?mode=ro" "SELECT COALESCE(MAX(ts_ms),0) FROM ticks;")

sleep "${SAMPLE_SEC}"

rows_after=$(sqlite3 "file:${DB_PATH}?mode=ro" "SELECT COUNT(*) FROM ticks;")
max_ts_after=$(sqlite3 "file:${DB_PATH}?mode=ro" "SELECT COALESCE(MAX(ts_ms),0) FROM ticks;")
lag_sec=$(sqlite3 "file:${DB_PATH}?mode=ro" "SELECT CAST(strftime('%s','now') - MAX(ts_ms)/1000.0 AS REAL) FROM ticks;")

echo "[INFO] service=${SERVICE_NAME}"
echo "[INFO] db=${DB_PATH}"
echo "[INFO] rows_before=${rows_before} rows_after=${rows_after}"
echo "[INFO] max_ts_before=${max_ts_before} max_ts_after=${max_ts_after}"
echo "[INFO] lag_sec=${lag_sec} threshold=${MAX_LAG_SEC}"

if [[ "${REQUIRE_GROWTH}" == "1" ]] && [[ "${rows_after}" -le "${rows_before}" ]]; then
  echo "[FAIL] rows did not grow during ${SAMPLE_SEC}s window" >&2
  exit 1
fi

if ! awk "BEGIN {exit !(${lag_sec} <= ${MAX_LAG_SEC})}"; then
  echo "[FAIL] lag_sec=${lag_sec} exceeds ${MAX_LAG_SEC}" >&2
  exit 1
fi

echo "[OK] healthcheck passed"
