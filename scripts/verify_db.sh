#!/usr/bin/env bash
set -euo pipefail

DATA_ROOT=${DATA_ROOT:-/data/sqlite/HK}
DAY=${DAY:-$(TZ=Asia/Hong_Kong date +%Y%m%d)}
DB_PATH=${1:-${DB_PATH:-${DATA_ROOT}/${DAY}.db}}
LAG_WARN_SEC=${LAG_WARN_SEC:-30}

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
echo "== Lag (UTC + localtime) =="
sqlite3 "file:${DB_PATH}?mode=ro" <<'SQL'
.headers on
.mode column
SELECT
  datetime(strftime('%s','now'),'unixepoch') AS now_utc,
  datetime(MAX(ts_ms)/1000,'unixepoch') AS max_ts_utc,
  datetime(MAX(ts_ms)/1000,'unixepoch','localtime') AS max_ts_local,
  ROUND(strftime('%s','now') - MAX(ts_ms)/1000.0, 3) AS lag_sec,
  COUNT(*) AS rows
FROM ticks;
SQL

echo
echo "== Latest 5 rows =="
sqlite3 "file:${DB_PATH}?mode=ro" <<'SQL'
.headers on
.mode column
SELECT
  symbol,
  seq,
  ts_ms,
  datetime(ts_ms/1000,'unixepoch') AS ts_utc,
  datetime(ts_ms/1000,'unixepoch','localtime') AS ts_local,
  price,
  volume,
  push_type,
  provider
FROM ticks
ORDER BY ts_ms DESC
LIMIT 5;
SQL

echo
echo "== Symbol distribution (latest 10 minutes) =="
sqlite3 "file:${DB_PATH}?mode=ro" <<'SQL'
.headers on
.mode column
SELECT
  symbol,
  COUNT(*) AS rows_10m,
  MAX(seq) AS max_seq,
  datetime(MAX(ts_ms)/1000,'unixepoch') AS latest_ts_utc
FROM ticks
WHERE ts_ms >= (strftime('%s','now') - 600) * 1000
GROUP BY symbol
ORDER BY rows_10m DESC, symbol;
SQL

echo
echo "== Duplicate check =="
sqlite3 "file:${DB_PATH}?mode=ro" <<'SQL'
.headers on
.mode column
SELECT 'dup_by_symbol_seq' AS check_name, COUNT(*) AS duplicate_groups
FROM (
  SELECT symbol, seq
  FROM ticks
  WHERE seq IS NOT NULL
  GROUP BY symbol, seq
  HAVING COUNT(*) > 1
)
UNION ALL
SELECT 'dup_by_composite_when_seq_null' AS check_name, COUNT(*) AS duplicate_groups
FROM (
  SELECT symbol, ts_ms, price, volume, turnover
  FROM ticks
  WHERE seq IS NULL
  GROUP BY symbol, ts_ms, price, volume, turnover
  HAVING COUNT(*) > 1
);
SQL

echo
echo "== PRAGMA =="
sqlite3 "file:${DB_PATH}?mode=ro" <<'SQL'
.headers on
.mode column
PRAGMA journal_mode;
PRAGMA busy_timeout;
PRAGMA synchronous;
PRAGMA wal_autocheckpoint;
SQL

lag_sec=$(sqlite3 "file:${DB_PATH}?mode=ro" "SELECT CAST(strftime('%s','now') - MAX(ts_ms)/1000.0 AS REAL) FROM ticks;")
if [[ -z "${lag_sec}" ]]; then
  echo "[FAIL] no rows in ticks" >&2
  exit 1
fi

if awk "BEGIN {exit !(${lag_sec} <= ${LAG_WARN_SEC})}"; then
  echo "[OK] lag_sec=${lag_sec} <= ${LAG_WARN_SEC}"
else
  echo "[WARN] lag_sec=${lag_sec} > ${LAG_WARN_SEC}" >&2
fi
