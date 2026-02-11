# Project Memory (Agents)

## 2026-02-11: hk-tick-collector persistent stall + future ts_ms

### Failure signature

- `WATCHDOG persistent_stall` recurring with systemd restart (`exit code 2`).
- SQLite `MAX(ts_ms)` ahead of UTC now by about `+8h` (`~28800s`).
- Queue backlog persists while `persisted_rows_per_min=0`.

### Reliable checks

```bash
bash scripts/verify_hk_tick_collector.sh
journalctl -u hk-tick-collector --since \"30 minutes ago\" --no-pager | grep -E \"WATCHDOG|persist_loop_heartbeat|health|persist_ticks\"
```

### Permanent remediation summary

- Enforce HK-local-to-UTC conversion in mapping.
- Seed sequence from recent DB files by `max(seq)` (not `ts<=now`).
- Persist loop: exception traceback always on, connection reset + retry.
- Watchdog: heartbeat-based (`last_dequeue_monotonic`, `last_commit_monotonic`), self-heal first, exit only after repeated heal failures.
- Repair script for historical +8h rows:
  - `python3 scripts/repair_future_ts_ms.py --data-root /data/sqlite/HK --day <YYYYMMDD>`
- Manual rollback script:
  - `ROLLBACK_REF=<commit> bash scripts/rollback_hk_tick_collector.sh`

### Common mistakes

- Treating market-local tick time as UTC epoch source.
- Assuming `PRAGMA busy_timeout` is DB-persisted (it is connection-level).
- Triggering restart immediately without attempting writer recovery.
