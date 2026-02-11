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
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

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

PY_OUT=$("${PYTHON_BIN}" - <<'PY'
import os
from futu import OpenQuoteContext, RET_OK, Session, SubType

host = os.getenv("FUTU_HOST", "127.0.0.1")
port = int(os.getenv("FUTU_PORT", "11111"))
symbols = [s.strip() for s in os.getenv("FUTU_SYMBOLS", "").split(",") if s.strip()]
market_open = 0

if not symbols:
    print("FUTU_SYMBOLS is empty, skip subscribe check")
    print("MARKET_OPEN=0")
    raise SystemExit(0)

ctx = OpenQuoteContext(host=host, port=port)
try:
    ret, data = ctx.subscribe(symbols, [SubType.TICKER], subscribe_push=False, session=Session.ALL)
    if ret != RET_OK:
        raise SystemExit(f"subscribe failed: {data}")

    ret, df = ctx.get_rt_ticker(symbols[0], num=1)
    if ret != RET_OK:
        raise SystemExit(f"get_rt_ticker failed: {df}")

    print(f"subscribe ok, sample ticker rows: {len(df)}")

    ret, sub_df = ctx.query_subscription()
    if ret == RET_OK:
        print(sub_df)
        if "own_used" in sub_df.columns:
            ticker_mask = sub_df["sub_type"].astype(str).str.contains("TICKER")
            own_used = int(sub_df.loc[ticker_mask, "own_used"].sum())
            if own_used <= 0:
                raise SystemExit("subscription check failed: own_used=0 for TICKER")
    else:
        raise SystemExit(f"query_subscription failed: {sub_df}")

    ret, state_df = ctx.get_market_state(symbols)
    if ret == RET_OK:
        print(state_df)
        if "market_state" in state_df.columns:
            states = state_df["market_state"].astype(str).str.upper().tolist()
            market_open = 1 if any("OPEN" in state or "TRADING" in state for state in states) else 0
finally:
    ctx.close()

print(f"MARKET_OPEN={market_open}")
PY
)

echo "$PY_OUT"
MARKET_OPEN=$(echo "$PY_OUT" | tail -n 1 | cut -d= -f2)

today=$(TZ=Asia/Hong_Kong date +%Y%m%d)
db_file="$DATA_ROOT/${today}.db"
if [ ! -f "$db_file" ]; then
  echo "db not found: $db_file"
  exit 1
fi

"${PYTHON_BIN}" - <<PY
import sqlite3
import time

symbols = [s.strip() for s in "${SYMBOLS}".split(",") if s.strip()]
strict = int("${MARKET_OPEN:-0}")
db_file = "$db_file"

def fetch_max_seq(conn, symbols):
    if not symbols:
        return {}
    rows = conn.execute(
        "SELECT symbol, MAX(seq) FROM ticks WHERE seq IS NOT NULL GROUP BY symbol"
    ).fetchall()
    return {symbol: seq for symbol, seq in rows if symbol in symbols and seq is not None}

conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
count = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
seq_before = fetch_max_seq(conn, symbols)
conn.close()
print(f"db ok: {count} rows in {db_file}")
print(f"max seq before: {seq_before}")

time.sleep(5)

conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
seq_after = fetch_max_seq(conn, symbols)
conn.close()
print(f"max seq after: {seq_after}")

advanced = any(
    symbol in seq_after and seq_after[symbol] > seq_before.get(symbol, -1)
    for symbol in seq_after
)
if strict and not advanced:
    raise SystemExit("db seq check failed: max(seq) did not advance")
if not advanced:
    print("db seq check warning: max(seq) did not advance")
PY
