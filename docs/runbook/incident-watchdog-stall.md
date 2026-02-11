# Incident Runbook: WATCHDOG Persistent Stall

## Symptom Patterns

Typical log patterns:

- `WATCHDOG persistent_stall ...`
- repeated `sqlite_busy_backoff ...`
- queue size grows while `persist_ticks` stops progressing
- `worker_alive=False` or commit age continuously increasing

## Triage Decision Tree

```text
1) Is upstream active? (push/poll moving)
   - No -> upstream/OpenD issue path
   - Yes -> go to 2
2) Is queue growing beyond threshold?
   - No -> likely duplicate-only window, monitor
   - Yes -> go to 3
3) Is persist thread committing?
   - Yes -> temporary pressure, tune thresholds
   - No -> go to 4
4) Is SQLite locked/busy or readonly?
   - Yes -> SQLite contention/permission path
   - No -> worker/writer recovery path
```

## Commands to Run

### Logs

```bash
sudo journalctl -u hk-tick-collector --since "30 minutes ago" --no-pager \
  | grep -E "WATCHDOG|persist_loop_heartbeat|persist_ticks|sqlite_busy|health"
```

### DB checks and PRAGMA

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" "PRAGMA journal_mode; PRAGMA synchronous; PRAGMA wal_autocheckpoint;"
sqlite3 "file:${DB}?mode=ro" "SELECT COUNT(*), MAX(ts_ms) FROM ticks;"
```

### Lock inspection

```bash
lsof "$DB" "$DB-wal" "$DB-shm" || true
fuser "$DB" "$DB-wal" "$DB-shm" || true
lslocks | grep -E "$(basename "$DB")|sqlite" || true
```

## Mitigation Steps

1. If readonly/permission issue:

```bash
sudo chown -R hkcollector:hkcollector /data/sqlite/HK
sudo chmod -R 750 /data/sqlite/HK
```

2. If transient lock pressure: restart collector (keep OpenD up):

```bash
sudo systemctl restart hk-tick-collector
```

3. If repeated watchdog exits:

- increase `WATCHDOG_STALL_SEC` moderately
- verify `WATCHDOG_QUEUE_THRESHOLD_ROWS` is not too low
- inspect storage latency and WAL growth

4. If OpenD unstable:

```bash
sudo systemctl status futu-opend --no-pager
sudo systemctl restart futu-opend
```

5. Re-verify freshness:

```bash
bash scripts/db_health_check.sh
```

## Postmortem Template

```markdown
# Incident: WATCHDOG persistent_stall

- Date/Time (UTC):
- Impact window:
- Affected symbols/services:
- Detection signal:
- Upstream status (OpenD/network):
- Queue behavior:
- SQLite symptoms (busy/locked/readonly):
- Immediate mitigation:
- Root cause:
- Corrective action:
- Preventive action:
- Follow-up owner and ETA:
```
