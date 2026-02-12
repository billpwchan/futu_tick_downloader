# Getting Started

## Prerequisites

- Linux/macOS with Python 3.10+
- SQLite CLI (`sqlite3`) for operational checks
- Optional: running Futu OpenD for live ingestion

## Install in Minutes

```bash
git clone <YOUR_FORK_OR_REPO_URL>
cd futu_tick_downloader
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .[dev]
```

## Local Validation (No OpenD)

Run unit/smoke tests only:

```bash
pytest -q
```

This validates:

- env parsing/defaults
- SQLite schema + WAL pragmas
- dedupe behavior
- watchdog logic
- collector persistence pipeline

## Live Validation (With OpenD)

1. Configure `.env`:

```dotenv
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
FUTU_SYMBOLS=HK.00700,HK.00981
DATA_ROOT=/tmp/hk_ticks
```

2. Start service in foreground:

```bash
hk-tick-collector
```

3. Verify writes:

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/tmp/hk_ticks/${DAY}.db
bash scripts/db_health_check.sh "$DB"
```

## Demo Output

Sample health query output:

```text
now_utc              max_tick_utc          lag_sec  rows
-------------------  -------------------   -------  --------
2026-02-11 08:51:00  2026-02-11 08:50:59   1.042    1875521
```

Sample logs:

```text
INFO persist_ticks trading_day=20260211 batch=500 inserted=498 ignored=2 commit_latency_ms=6 queue=0/50000
INFO persist_loop_heartbeat worker_alive=True queue=0/50000 total_rows_committed=2013450 busy_locked_count=0
INFO health queue=0/50000 persisted_rows_per_min=22340 lag_sec=1.4 watchdog_failures=0
```

## Next Steps

- Production deployment: [`docs/deployment.md`](deployment.md)
- Telegram setup: [`docs/telegram.md`](telegram.md)
- Runbook: [`docs/runbook.md`](runbook.md)
- Troubleshooting: [`docs/troubleshooting.md`](troubleshooting.md)
