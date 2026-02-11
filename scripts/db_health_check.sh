#!/usr/bin/env bash
set -euo pipefail

DATA_ROOT=${DATA_ROOT:-/data/sqlite/HK}
DAY=${DAY:-$(TZ=Asia/Hong_Kong date +%Y%m%d)}
DB_PATH=${1:-${DB_PATH:-${DATA_ROOT}/${DAY}.db}}
TOP_N=${TOP_N:-20}

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "[FAIL] sqlite3 not found" >&2
  exit 1
fi

if [[ ! -f "${DB_PATH}" ]]; then
  echo "[FAIL] db not found: ${DB_PATH}" >&2
  exit 1
fi

echo "[INFO] db=${DB_PATH}"

echo
printf '%s\n' "== Global freshness =="
sqlite3 "file:${DB_PATH}?mode=ro" <<'SQL'
.headers on
.mode column
SELECT
  datetime(strftime('%s','now'),'unixepoch') AS now_utc,
  datetime(MAX(ts_ms)/1000,'unixepoch') AS max_ts_utc,
  ROUND(strftime('%s','now') - MAX(ts_ms)/1000.0, 3) AS lag_sec,
  COUNT(*) AS rows
FROM ticks;
SQL

echo
printf '%s\n' "== Symbol freshness =="
sqlite3 "file:${DB_PATH}?mode=ro" <<SQL
.headers on
.mode column
SELECT
  symbol,
  COUNT(*) AS rows,
  MAX(seq) AS max_seq,
  datetime(MAX(ts_ms)/1000,'unixepoch') AS latest_ts_utc,
  ROUND(strftime('%s','now') - MAX(ts_ms)/1000.0, 3) AS lag_sec
FROM ticks
GROUP BY symbol
ORDER BY lag_sec DESC, symbol
LIMIT ${TOP_N};
SQL

echo
printf '%s\n' "== Duplicate checks =="
sqlite3 "file:${DB_PATH}?mode=ro" <<'SQL'
.headers on
.mode column
SELECT 'dup_symbol_seq' AS check_name, COUNT(*) AS duplicate_groups
FROM (
  SELECT symbol, seq
  FROM ticks
  WHERE seq IS NOT NULL
  GROUP BY symbol, seq
  HAVING COUNT(*) > 1
)
UNION ALL
SELECT 'dup_composite_when_seq_null' AS check_name, COUNT(*) AS duplicate_groups
FROM (
  SELECT symbol, ts_ms, price, volume, turnover
  FROM ticks
  WHERE seq IS NULL
  GROUP BY symbol, ts_ms, price, volume, turnover
  HAVING COUNT(*) > 1
);
SQL

echo
printf '%s\n' "== PRAGMA snapshot =="
sqlite3 "file:${DB_PATH}?mode=ro" <<'SQL'
.headers on
.mode column
PRAGMA journal_mode;
PRAGMA synchronous;
PRAGMA wal_autocheckpoint;
SQL
