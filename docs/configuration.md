# Configuration Reference

Configuration is environment-driven from either:

- `.env` in repository root (local/dev)
- `EnvironmentFile=` in systemd unit (production)

Parser implementation: `hk_tick_collector/config.py`

## Parsing Rules

- Integer/float vars: invalid values raise `ValueError` at startup.
- Boolean vars (`FUTU_POLL_ENABLED`): accepts `1/0,true/false,yes/no,on/off`.
- CSV vars (`FUTU_SYMBOLS`): split by comma and trim whitespace.

## Environment Variables

| Variable | Default | Meaning | Recommended Range | Impact | When to Change |
|---|---|---|---|---|---|
| `FUTU_HOST` | `127.0.0.1` | OpenD host | local/private IP | Wrong value breaks connectivity | Change only if OpenD is remote |
| `FUTU_PORT` | `11111` | OpenD API port | valid TCP port | Wrong value breaks subscribe/poll | Match OpenD `api_port` |
| `FUTU_SYMBOLS` | empty | Comma-separated symbols to subscribe | explicit list | Empty list fails startup | Set to your HK universe |
| `DATA_ROOT` | `/data/sqlite/HK` | Root directory for daily SQLite files | dedicated disk/path | Permission/full-disk causes write failures | Change for storage layout |
| `BATCH_SIZE` | `500` | Rows per DB flush batch | `300-1000` | Too small increases write overhead; too large increases flush latency | Tune for throughput/latency |
| `MAX_WAIT_MS` | `1000` | Max wait before flush | `500-1500` | Larger values increase end-to-end latency | Tune for latency objectives |
| `MAX_QUEUE_SIZE` | `20000` | In-memory queue cap | `20k-100k` | Too low may drop enqueue; too high uses memory | Tune by traffic burst |
| `BACKFILL_N` | `0` | Rows to backfill after reconnect | `0-500` | Higher values increase startup/reconnect load | Use only if replay window needed |
| `RECONNECT_MIN_DELAY` | `1` | Reconnect lower bound (sec) | `1-3` | Too low can flap | Adjust if network unstable |
| `RECONNECT_MAX_DELAY` | `60` | Reconnect upper bound (sec) | `30-60` | Too high delays recovery | Tune reconnect profile |
| `CHECK_INTERVAL_SEC` | `5` | OpenD connection health check interval | `3-10` | Too low increases noise | Tune monitoring cadence |
| `FUTU_POLL_ENABLED` | `true` | Enable poll fallback | `true/false` | Disabling removes fallback during push stalls | Keep enabled in production |
| `FUTU_POLL_INTERVAL_SEC` | `3` | Poll loop interval (sec) | `2-5` | Too low adds load/duplicates | Tune with OpenD capacity |
| `FUTU_POLL_NUM` | `100` | Poll fetch size per request | `50-200` | Too high increases request/parse cost | Tune by symbol activity |
| `FUTU_POLL_STALE_SEC` | `10` | Poll only when push stale for this window | `8-15` | Too low causes unnecessary polling | Tune if push intermittency changes |
| `WATCHDOG_STALL_SEC` | `180` | Commit stall threshold (sec) | `120-300` | Too low false positives; too high slow recovery | Tune by acceptable failover latency |
| `WATCHDOG_UPSTREAM_WINDOW_SEC` | `60` | Upstream activity lookback window | `30-120` | Too low may miss active upstream | Tune for market burst patterns |
| `WATCHDOG_QUEUE_THRESHOLD_ROWS` | `100` | Minimum backlog before watchdog stall logic | `100-1000` | Too low false alarms; too high delays detection | Tune by queue scale |
| `WATCHDOG_RECOVERY_MAX_FAILURES` | `3` | Max recovery failures before exit | `3-5` | Too low triggers frequent restarts | Tune restart aggressiveness |
| `WATCHDOG_RECOVERY_JOIN_TIMEOUT_SEC` | `3.0` | Wait time for old writer thread during recovery | `2-5` | Too short may fail recovery | Increase if thread teardown is slow |
| `DRIFT_WARN_SEC` | `120` | Alert threshold for timestamp drift | `60-180` | Too low raises noisy warnings | Tune for monitoring sensitivity |
| `STOP_FLUSH_TIMEOUT_SEC` | `60` | Graceful shutdown flush timeout | `60-180` | Too low risks unflushed queue on stop | Increase for larger queues |
| `SEED_RECENT_DB_DAYS` | `3` | Days scanned on startup for seq seed | `3-5` | Higher values increase startup time | Increase if long downtime replay is common |
| `PERSIST_RETRY_MAX_ATTEMPTS` | `0` | Persist retry budget per batch (`0` means retry until success) | `0` or small positive | Small values may escalate transient failures | Keep `0` for resilient write path |
| `PERSIST_RETRY_BACKOFF_SEC` | `1.0` | Initial persist retry backoff (sec) | `0.1-1.0` | Too low may hammer SQLite | Tune for lock contention |
| `PERSIST_RETRY_BACKOFF_MAX_SEC` | `2.0` | Max persist retry backoff (sec) | `1-5` | Too high lowers throughput recovery | Tune retry pacing |
| `PERSIST_HEARTBEAT_INTERVAL_SEC` | `30.0` | Persist heartbeat log interval | `10-30` | Too small increases log volume | Tune observability/detail |
| `SQLITE_BUSY_TIMEOUT_MS` | `5000` | SQLite busy timeout per connection | `3000-10000` | Too low increases `locked` failures | Increase on lock-heavy hosts |
| `SQLITE_JOURNAL_MODE` | `WAL` | SQLite journal mode | `WAL` recommended | Non-WAL hurts concurrent read/write | Change only for special constraints |
| `SQLITE_SYNCHRONOUS` | `NORMAL` | SQLite fsync safety mode | `NORMAL`/`FULL` | `OFF` increases corruption risk on crash | Use `FULL` for stricter durability |
| `SQLITE_WAL_AUTOCHECKPOINT` | `1000` | WAL auto-checkpoint pages | `500-2000` | Too high grows WAL file | Tune by write volume/disk IO |
| `TELEGRAM_ENABLED` | `false` | Enable Telegram notifier worker | `0/1` | Disabled keeps runtime behavior unchanged | Set to `1` only after bot/chat validation |
| `TELEGRAM_BOT_TOKEN` | empty | Telegram bot token | secret | Invalid/missing token disables notifier | Store in private env/secret manager |
| `TELEGRAM_CHAT_ID` | empty | Destination group/channel chat id | group id (often negative) | Wrong id causes `400/403` send failures | Fill with real group/topic target |
| `TELEGRAM_THREAD_ID` | empty | Optional group topic id | positive int | Wrong topic id causes `400` | Set only when using forum topics |
| `TELEGRAM_DIGEST_INTERVAL_SEC` | `600` | Digest evaluation interval | `300-1800` | Too small increases noise | Tune by ops preference |
| `TELEGRAM_ALERT_COOLDOWN_SEC` | `600` | Same alert-key cooldown window | `300-1800` | Too small may spam repeated incidents | Tune by incident cadence |
| `TELEGRAM_RATE_LIMIT_PER_MIN` | `18` | Local sender cap across all message types | `1-20` | Too high risks Telegram throttling | Keep below Telegram soft cap |
| `TELEGRAM_INCLUDE_SYSTEM_METRICS` | `true` | Include `load1/rss/disk` in digest | `0/1` | Off reduces payload detail | Disable for minimal digest |
| `TELEGRAM_DIGEST_QUEUE_CHANGE_PCT` | `20` | Queue utilization change threshold for "meaningful change" | `5-50` | Low values raise extra digests | Tune noise/sensitivity |
| `TELEGRAM_DIGEST_LAST_TICK_AGE_THRESHOLD_SEC` | `60` | `last_tick_age` threshold for digest change detection | `30-300` | Low values increase digest churn | Tune by symbol activity |
| `TELEGRAM_DIGEST_DRIFT_THRESHOLD_SEC` | `60` | `|drift_sec|` threshold for digest change detection | `30-300` | Low values increase digest churn | Tune with drift policy |
| `TELEGRAM_DIGEST_SEND_ALIVE_WHEN_IDLE` | `false` | Send compact alive digest when no meaningful change | `0/1` | On can add background noise | Default off for low-noise mode |
| `TELEGRAM_SQLITE_BUSY_ALERT_THRESHOLD` | `3` | Per-minute busy/locked backoff alert threshold | `1-20` | Low values may alert on transient lock | Tune by storage contention |
| `INSTANCE_ID` | empty | Human-readable instance label in messages | short text | Empty falls back to hostname only | Use for multi-node collectors |
| `LOG_LEVEL` | `INFO` | App log verbosity | `INFO` for prod | `DEBUG` can be very noisy | Use debug only transiently |

## Production Baseline Template

```dotenv
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
FUTU_SYMBOLS=HK.00700,HK.00981,HK.01810
DATA_ROOT=/data/sqlite/HK

BATCH_SIZE=500
MAX_WAIT_MS=1000
MAX_QUEUE_SIZE=50000

FUTU_POLL_ENABLED=1
FUTU_POLL_INTERVAL_SEC=3
FUTU_POLL_NUM=100
FUTU_POLL_STALE_SEC=10

WATCHDOG_STALL_SEC=180
WATCHDOG_UPSTREAM_WINDOW_SEC=60
WATCHDOG_QUEUE_THRESHOLD_ROWS=100
WATCHDOG_RECOVERY_MAX_FAILURES=3
WATCHDOG_RECOVERY_JOIN_TIMEOUT_SEC=3

PERSIST_RETRY_MAX_ATTEMPTS=0
PERSIST_RETRY_BACKOFF_SEC=0.5
PERSIST_RETRY_BACKOFF_MAX_SEC=2
PERSIST_HEARTBEAT_INTERVAL_SEC=30
STOP_FLUSH_TIMEOUT_SEC=120

SQLITE_BUSY_TIMEOUT_MS=5000
SQLITE_JOURNAL_MODE=WAL
SQLITE_SYNCHRONOUS=NORMAL
SQLITE_WAL_AUTOCHECKPOINT=1000

TELEGRAM_ENABLED=1
TELEGRAM_BOT_TOKEN=<secret>
TELEGRAM_CHAT_ID=-1001234567890
TELEGRAM_THREAD_ID=
TELEGRAM_DIGEST_INTERVAL_SEC=600
TELEGRAM_ALERT_COOLDOWN_SEC=600
TELEGRAM_RATE_LIMIT_PER_MIN=18
TELEGRAM_INCLUDE_SYSTEM_METRICS=1
TELEGRAM_DIGEST_QUEUE_CHANGE_PCT=20
TELEGRAM_DIGEST_LAST_TICK_AGE_THRESHOLD_SEC=60
TELEGRAM_DIGEST_DRIFT_THRESHOLD_SEC=60
TELEGRAM_DIGEST_SEND_ALIVE_WHEN_IDLE=0
TELEGRAM_SQLITE_BUSY_ALERT_THRESHOLD=3
INSTANCE_ID=hk-a1

DRIFT_WARN_SEC=120
SEED_RECENT_DB_DAYS=3
LOG_LEVEL=INFO
```

## Change Control

After changing env values in production:

```bash
sudo systemctl daemon-reload
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
bash scripts/db_health_check.sh
```
