#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=${ENV_FILE:-/etc/hk-tick-collector.env}
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  set +a
fi

HOST=${FUTU_HOST:-127.0.0.1}
PORT=${FUTU_PORT:-11111}
DATA_ROOT=${DATA_ROOT:-/data/sqlite/HK}
SYMBOLS=${FUTU_SYMBOLS:-}

if command -v ss >/dev/null 2>&1; then
  if ! ss -lnt | grep -q "${HOST}:${PORT}"; then
    echo "port check failed: ${HOST}:${PORT} not listening"
    exit 1
  fi
else
  if ! nc -z "$HOST" "$PORT"; then
    echo "port check failed: ${HOST}:${PORT} not listening"
    exit 1
  fi
fi

echo "port check ok: ${HOST}:${PORT}"

python3 - <<'PY'
import os
from futu import OpenQuoteContext, RET_OK, Session, SubType

host = os.getenv("FUTU_HOST", "127.0.0.1")
port = int(os.getenv("FUTU_PORT", "11111"))
symbols = [s.strip() for s in os.getenv("FUTU_SYMBOLS", "").split(",") if s.strip()]

if not symbols:
    print("FUTU_SYMBOLS is empty, skip subscribe check")
    raise SystemExit(0)

ctx = OpenQuoteContext(host=host, port=port)
ret, data = ctx.subscribe(symbols, [SubType.TICKER], subscribe_push=False, session=Session.ALL)
if ret != RET_OK:
    raise SystemExit(f"subscribe failed: {data}")

ret, df = ctx.get_rt_ticker(symbols[0], num=1)
if ret != RET_OK:
    raise SystemExit(f"get_rt_ticker failed: {df}")

print(f"subscribe ok, sample ticker rows: {len(df)}")
ctx.close()
PY

today=$(date +%Y%m%d)
db_file="$DATA_ROOT/${today}.db"
if [ ! -f "$db_file" ]; then
  echo "db not found: $db_file"
  exit 1
fi

python3 - <<PY
import sqlite3
conn = sqlite3.connect("$db_file")
count = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
conn.close()
print(f"db ok: {count} rows in {db_file}")
PY

