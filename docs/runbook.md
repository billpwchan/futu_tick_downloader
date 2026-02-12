# Runbook (BAU + Incident SOP)

This runbook is for operators of `hk-tick-collector` in production.

## 1) Health Checklist

Run every day (or during on-call checks).

Service/process:

```bash
sudo systemctl status hk-tick-collector --no-pager
```

Recent logs (heartbeat + persist + watchdog + notifier):

```bash
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager \
  | grep -E "health|persist_ticks|persist_loop_heartbeat|WATCHDOG|telegram|sqlite_busy"
```

DB freshness/drift:

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" \
  "SELECT COUNT(*) AS rows, MAX(ts_ms) AS max_ts_ms, ROUND(strftime('%s','now')-MAX(ts_ms)/1000.0,3) AS drift_sec FROM ticks;"
```

Healthy baseline:

- `health` and `persist_ticks` logs continue.
- `persisted_rows_per_min` usually > 0 during active market.
- DB `drift_sec` does not keep growing abnormally.

## 2) Alert SOP: `PERSIST_STALL`

Telegram alert example:

```text
🚨 HK Tick Collector · PERSIST STALL
...
suggest: journalctl -u hk-tick-collector -n 200 --no-pager
suggest: sqlite3 /data/sqlite/HK/<day>.db 'select count(*) from ticks;'
```

Steps:

1. Capture context immediately:

```bash
sudo journalctl -u hk-tick-collector -n 300 --no-pager
```

2. Check queue/watchdog/persist signals:

- repeated `WATCHDOG` recovery failures
- no recent `persist_ticks`
- increasing queue indicators

3. Validate DB is still writable:

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" "SELECT COUNT(*), MAX(ts_ms) FROM ticks;"
```

4. If still stalled, restart:

```bash
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

5. Verify recovery in next 5 minutes:

- `persist_ticks` resumed
- `persisted_rows_per_min` resumed
- no new `PERSIST_STALL` alert burst (cooldown is applied)

## 3) Data Export (SCP) + Permissions

Preferred: create online snapshot first.

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
SNAP=/data/sqlite/HK/${DAY}.snapshot.db
sqlite3 "$DB" ".backup '${SNAP}'"
```

Copy to local:

```bash
scp user@server:/data/sqlite/HK/${DAY}.snapshot.db ./
```

Permissions:

- keep data dir owned by service account (`hkcollector`).
- avoid world-readable market data files.
- avoid copying live `.db-wal`/`.db-shm` files directly for ad-hoc exports.

## 4) Disaster Recovery

## A) DB corruption / open failure

1. Stop service:

```bash
sudo systemctl stop hk-tick-collector
```

2. Preserve damaged DB for forensics.
3. Restore from latest snapshot backup.
4. Start service and verify ingest path.

## B) Disk full

1. Confirm:

```bash
df -h /data/sqlite/HK
```

2. Free space (retention cleanup, move old snapshots).
3. Restart collector.
4. Watch for busy/locked or WAL growth anomalies.

## C) Instance reboot / restart

1. Confirm `hk-tick-collector` auto-started.
2. Confirm Telegram digest resumes at next interval.
3. Validate DB row growth for current `YYYYMMDD`.

## 5) Resource Monitoring (lightweight)

Recommended minimum:

- CPU load (`load1`)
- process RSS memory (`rss_mb`)
- disk free space (`disk_free_gb`)

These are already included in Telegram digest when `TELEGRAM_INCLUDE_SYSTEM_METRICS=1`.

## 6) Reference Docs

- Deployment: [`docs/deployment.md`](deployment.md)
- Telegram setup: [`docs/telegram.md`](telegram.md)
- Full operations detail: [`docs/runbook/operations.md`](runbook/operations.md)
