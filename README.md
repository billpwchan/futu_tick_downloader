# hk-tick-collector

[![CI](https://github.com/billpwchan/futu_tick_downloader/actions/workflows/ci.yml/badge.svg)](https://github.com/billpwchan/futu_tick_downloader/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/billpwchan/futu_tick_downloader)](https://github.com/billpwchan/futu_tick_downloader/releases)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

Production-grade HK tick collector for Futu OpenD.

It ingests push + poll fallback ticks, deduplicates safely, and persists to SQLite WAL with systemd-friendly operations.

- For operators: fast deploy, clear runbooks, one-page incident commands.
- For developers: clean env config, tests, lint, packaging, CI.

[中文文档 (README.zh-CN)](README.zh-CN.md)

## Table Of Contents

- [Why This Project](#why-this-project)
- [Feature Highlights](#feature-highlights)
- [Architecture](#architecture)
- [3-Minute Quickstart](#3-minute-quickstart)
- [Production Deployment (systemd)](#production-deployment-systemd)
- [Telegram Notifications](#telegram-notifications)
- [Data Model And Guarantees](#data-model-and-guarantees)
- [Operations Cheat Sheet](#operations-cheat-sheet)
- [Troubleshooting](#troubleshooting)
- [Documentation Map](#documentation-map)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Security License Disclaimer](#security-license-disclaimer)

## Why This Project

Most market-data collectors fail in production for one of these reasons: unclear timestamp semantics, weak dedupe, poor incident tooling, or fragile restarts.

`hk-tick-collector` focuses on operational correctness first:

- Explicit UTC timestamp semantics for storage.
- Idempotent writes via unique indexes + `INSERT OR IGNORE`.
- Watchdog recovery for persist stalls.
- Linux systemd deployment and runbooks included.

## Feature Highlights

- Push-first ingestion with poll fallback (`FUTU_POLL_*`).
- Per-trading-day SQLite files (`DATA_ROOT/YYYYMMDD.db`).
- WAL mode, configurable busy timeout, auto-checkpoint.
- Durable dedupe for `seq` and non-`seq` rows.
- Health heartbeat logs with queue, commit, drift, and watchdog context.
- Low-noise Telegram group notifications (digest + key alerts, rate-limited + cooldown).
- Production docs: deployment, operations, incident response, data quality.

## Architecture

```mermaid
flowchart LR
    A["Futu OpenD"] --> B["Push Ticker Stream"]
    A --> C["Poll Fallback"]
    B --> D["Mapping + Validation"]
    C --> D
    D --> E["Async Queue"]
    E --> F["Persist Worker"]
    F --> G["SQLite WAL\nDATA_ROOT/YYYYMMDD.db"]
    F --> H["Health Logs + Watchdog"]
    H --> I["Telegram Notifier\nDigest + Alerts"]
```

Detailed design: [`docs/architecture.md`](docs/architecture.md)

## 3-Minute Quickstart

### Option A: Validate locally (no live OpenD required)

```bash
git clone <YOUR_FORK_OR_REPO_URL>
cd futu_tick_downloader
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .[dev]
pytest -q
```

### Option B: Live run with OpenD

```bash
cp .env.example .env
# set FUTU_HOST/FUTU_PORT/FUTU_SYMBOLS/DATA_ROOT

. .venv/bin/activate
hk-tick-collector
# existing production entrypoint also works:
python -m hk_tick_collector.main
```

Verify writes:

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
bash scripts/db_health_check.sh "$DB"
```

## Production Deployment (systemd)

- Unit template: [`deploy/systemd/hk-tick-collector.service`](deploy/systemd/hk-tick-collector.service)
- Deployment guide: [`docs/deployment/systemd.md`](docs/deployment/systemd.md)
- One-page production runbook (CN): [`docs/runbook/production-onepager.md`](docs/runbook/production-onepager.md)

Enable service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

## Telegram Notifications

Enable in your env file (`.env` local or systemd `EnvironmentFile=` in production):

```dotenv
TELEGRAM_ENABLED=1
TELEGRAM_BOT_TOKEN=<secret>
TELEGRAM_CHAT_ID=-1001234567890
TELEGRAM_THREAD_ID=
TELEGRAM_DIGEST_INTERVAL_SEC=600
TELEGRAM_ALERT_COOLDOWN_SEC=600
TELEGRAM_RATE_LIMIT_PER_MIN=18
TELEGRAM_INCLUDE_SYSTEM_METRICS=1
INSTANCE_ID=hk-prod-a1
```

Design goals:

- readable messages with hostname + instance context.
- low noise: digest interval + change-driven suppression + alert cooldown.
- reliability: async queue worker, Telegram `429 retry_after` handling, sender rate limit.
- safety: notifier failures never block ingest/persist.

Digest sample:

```text
📈 HK Tick Collector · HEALTH
host=ip-10-0-1-12 instance=hk-prod-a1 pid=7821 uptime=06:12:05 day=20260212 tz=UTC+8
db=/data/sqlite/HK/20260212.db rows=2843001 max_ts=2026-02-12T03:15:59+00:00 drift_sec=1.0
queue=0/50000 push_per_min=24100 poll_fetched=300 accepted=220 persisted_per_min=24310 dup_drop=80
symbols:
- HK.00700 age=0.8 last_persisted_seq=884102 max_seq_lag=0
- HK.00981 age=1.0 last_persisted_seq=553011 max_seq_lag=0
sys: load1=0.42 rss_mb=186.5 disk_free_gb=327.44
```

Alert sample:

```text
🚨 HK Tick Collector · PERSIST STALL
host=ip-10-0-1-12 instance=hk-prod-a1 day=20260212
stall_sec=242.3/180
queue=8542/50000 max_seq_lag=812 persisted_per_min=0
last_persisted_seq: HK.00700=884102 HK.00981=553011
suggest: journalctl -u hk-tick-collector -n 200 --no-pager
suggest: sqlite3 /data/sqlite/HK/20260212.db 'select count(*) from ticks;'
```

Setup guide: [`docs/telegram.md`](docs/telegram.md)

## Data Model And Guarantees

Core table (`ticks`) is append-only from collector perspective.

```sql
CREATE TABLE ticks (
  market TEXT NOT NULL,
  symbol TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,
  price REAL,
  volume INTEGER,
  turnover REAL,
  direction TEXT,
  seq INTEGER,
  tick_type TEXT,
  push_type TEXT,
  provider TEXT,
  trading_day TEXT NOT NULL,
  recv_ts_ms INTEGER NOT NULL,
  inserted_at_ms INTEGER NOT NULL
);
```

### Dedupe guarantees

- `uniq_ticks_symbol_seq` when `seq IS NOT NULL`.
- `uniq_ticks_symbol_ts_price_vol_turnover` when `seq IS NULL`.
- `INSERT OR IGNORE` makes ingestion idempotent under retries and push/poll overlap.

### Timestamp guarantees

- `ts_ms`: event timestamp in UTC epoch milliseconds.
- `recv_ts_ms`: collector receive timestamp in UTC epoch milliseconds.
- HK local source times are interpreted as `Asia/Hong_Kong`, then converted to UTC epoch.

## Operations Cheat Sheet

Service:

```bash
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

Logs:

```bash
sudo journalctl -u hk-tick-collector -f --no-pager
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager \
  | grep -E "health|persist_ticks|persist_loop_heartbeat|WATCHDOG|sqlite_busy|ERROR"
```

Freshness:

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" \
  "SELECT ROUND(strftime('%s','now')-MAX(ts_ms)/1000.0,3) AS lag_sec, COUNT(*) AS rows FROM ticks;"
```

More SQL snippets: [`scripts/query_examples.sql`](scripts/query_examples.sql)

## Troubleshooting

- WATCHDOG stall: [`docs/runbook/incident-watchdog-stall.md`](docs/runbook/incident-watchdog-stall.md)
- SQLite WAL / locked: [`docs/runbook/sqlite-wal.md`](docs/runbook/sqlite-wal.md)
- Timestamp and drift checks: [`docs/runbook/data-quality.md`](docs/runbook/data-quality.md)
- General troubleshooting: [`docs/troubleshooting.md`](docs/troubleshooting.md)

## Documentation Map

- Quickstart: [`docs/getting-started.md`](docs/getting-started.md)
- Configuration (full env reference): [`docs/configuration.md`](docs/configuration.md)
- Architecture: [`docs/architecture.md`](docs/architecture.md)
- Deployment (systemd): [`docs/deployment/systemd.md`](docs/deployment/systemd.md)
- Deployment quick guide: [`docs/deployment.md`](docs/deployment.md)
- Telegram setup: [`docs/telegram.md`](docs/telegram.md)
- Operations runbook: [`docs/runbook.md`](docs/runbook.md)
- Extended operations runbook: [`docs/runbook/operations.md`](docs/runbook/operations.md)
- One-page runbook (CN): [`docs/runbook/production-onepager.md`](docs/runbook/production-onepager.md)
- Release process: [`docs/releasing.md`](docs/releasing.md)
- FAQ: [`docs/faq.md`](docs/faq.md)

## Roadmap

- Optional metrics endpoint for external observability stacks.
- Optional Parquet export flow for analytics pipelines.
- Additional integration tests for larger symbol universes.

## Contributing

- Guide: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Code of conduct: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
- PR template: [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md)

## Security License Disclaimer

- Security policy: [`SECURITY.md`](SECURITY.md)
- Support channels: [`SUPPORT.md`](SUPPORT.md)
- License: Apache-2.0 ([`LICENSE`](LICENSE))

Futu OpenD and market data usage must comply with Futu terms and local regulations. This repository is a collector/persistence service and does not grant redistribution rights for proprietary data.
