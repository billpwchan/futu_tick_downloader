# Runbook: Operations

## Scope

Business-as-usual operations for `hk-tick-collector` on Linux + systemd.

Quick command guide (single page): [`production-onepager.md`](production-onepager.md)

## Daily Checks

### Before Market Open

1. service status:

```bash
sudo systemctl status hk-tick-collector --no-pager
sudo systemctl status futu-opend --no-pager
```

2. config sanity:

```bash
grep -E '^(FUTU_HOST|FUTU_PORT|FUTU_SYMBOLS|DATA_ROOT)=' /etc/hk-tick-collector.env
```

3. disk capacity:

```bash
df -h /data/sqlite/HK
```

### During Market Hours

1. log heartbeat:

```bash
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager \
  | grep -E "health|persist_ticks|persist_loop_heartbeat|WATCHDOG"
```

2. freshness and row growth:

```bash
bash scripts/db_health_check.sh
```

3. queue/watchdog signal check:

- watch for repeated `sqlite_busy_backoff`
- watch for `WATCHDOG persistent_stall`

### After Market Close

1. final freshness snapshot:

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
bash scripts/db_health_check.sh "$DB"
```

2. backup daily DB.
3. record table stats if needed for capacity tracking.

## Backup Procedure (SQLite Snapshot While Running)

WAL-safe online backup:

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
SNAP=/data/sqlite/HK/${DAY}.snapshot.db
sqlite3 "$DB" ".backup '${SNAP}'"
```

Best practices:

- prefer `.backup` over raw file copy for online consistency.
- store snapshots on separate volume or remote storage.
- checksum backup artifacts.

## Copy DB to Local via SCP

From local machine:

```bash
scp user@server:/data/sqlite/HK/20260211.db ./20260211.db
```

If bandwidth limited, compress first on server:

```bash
gzip -c /data/sqlite/HK/20260211.db > /tmp/20260211.db.gz
scp user@server:/tmp/20260211.db.gz ./
```

## Rotation / Retention

Example retention policy (keep 30 daily DBs + weekly snapshots):

```bash
find /data/sqlite/HK -name '*.db' -type f -mtime +30 -print
```

Apply deletion only after backup verification and business approval.

Recommended:

- automate retention checks via cron/systemd timer.
- include disk watermark alerts.

## On-Call Quick Commands

```bash
sudo systemctl restart hk-tick-collector
sudo journalctl -u hk-tick-collector -f
bash scripts/db_health_check.sh
```

## References

- watchdog incident flow: [`incident-watchdog-stall.md`](incident-watchdog-stall.md)
- SQLite operational details: [`sqlite-wal.md`](sqlite-wal.md)
- data-quality checks: [`data-quality.md`](data-quality.md)
