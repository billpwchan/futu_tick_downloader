#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-hk-tick-collector}"
DATA_ROOT="${DATA_ROOT:-/data/sqlite/HK}"
DAY="${DAY:-$(TZ=Asia/Hong_Kong date +%Y%m%d)}"
DB_PATH="${DB_PATH:-${DATA_ROOT}/${DAY}.db}"
TS_TOLERANCE_SEC="${TS_TOLERANCE_SEC:-5}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK_SCRIPT="${CHECK_SCRIPT:-${SCRIPT_DIR}/check_ts_semantics.py}"

echo "[INFO] service=${SERVICE_NAME}"
echo "[INFO] db_path=${DB_PATH}"

if [[ ! -f "${DB_PATH}" ]]; then
  echo "[FAIL] db not found: ${DB_PATH}" >&2
  exit 1
fi

python3 - <<PY
import sqlite3
import time
from datetime import datetime, timezone

db_path = "${DB_PATH}"
expected_busy_timeout = int("${SQLITE_BUSY_TIMEOUT_MS:-5000}")
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
try:
    conn.execute(f"PRAGMA busy_timeout={max(1, expected_busy_timeout)};")
    now_ms = int(time.time() * 1000)
    row = conn.execute("SELECT COUNT(*), MAX(ts_ms) FROM ticks").fetchone()
    rows = int(row[0] or 0)
    max_ts = row[1]
    if max_ts is None:
        max_ts = 0
    max_minus_now_sec = (int(max_ts) - now_ms) / 1000.0 if rows > 0 else 0.0
    now_utc = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc).isoformat()
    max_utc = datetime.fromtimestamp(int(max_ts) / 1000.0, tz=timezone.utc).isoformat() if rows > 0 else "none"

    journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    synchronous = conn.execute("PRAGMA synchronous;").fetchone()[0]
    busy_timeout = int(conn.execute("PRAGMA busy_timeout;").fetchone()[0])
    temp_store = conn.execute("PRAGMA temp_store;").fetchone()[0]
    wal_autocheckpoint = conn.execute("PRAGMA wal_autocheckpoint;").fetchone()[0]

    print(f"now_utc={now_utc}")
    print(f"max_ts_utc={max_utc}")
    print(f"max_minus_now_sec={max_minus_now_sec:.3f}")
    print(f"rows={rows}")
    print(f"journal_mode={journal_mode}")
    print(f"synchronous={synchronous}")
    print(f"busy_timeout={busy_timeout}")
    print(f"temp_store={temp_store}")
    print(f"wal_autocheckpoint={wal_autocheckpoint}")
    print(f"busy_timeout_check={'PASS' if busy_timeout >= 1000 else 'FAIL'}")
    print(f"journal_mode_check={'PASS' if str(journal_mode).lower() == 'wal' else 'FAIL'}")
finally:
    conn.close()
PY

python3 "${CHECK_SCRIPT}" --db "${DB_PATH}" --tolerance-sec "${TS_TOLERANCE_SEC}"

if command -v journalctl >/dev/null 2>&1; then
  echo "[INFO] recent watchdog logs (last 5 minutes)"
  LOG_OUTPUT="$(journalctl -u "${SERVICE_NAME}" --since "5 minutes ago" --no-pager || true)"
  WATCHDOG_COUNT="$(echo "${LOG_OUTPUT}" | grep -c "WATCHDOG persistent_stall" || true)"
  echo "watchdog_persistent_stall_last_5m=${WATCHDOG_COUNT}"
  echo "[INFO] latest sqlite_pragmas line"
  echo "${LOG_OUTPUT}" | grep "sqlite_pragmas" | tail -n 1 || true
  echo "[INFO] recent health/persist lines"
  echo "${LOG_OUTPUT}" | grep -E "health|persist_ticks|persist_loop_heartbeat|WATCHDOG" | tail -n 40 || true
else
  echo "[WARN] journalctl not found; skip log verification"
fi
