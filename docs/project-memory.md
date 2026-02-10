# Project Memory

## 2026-02-10: HK tick pipeline hidden stall fix

### Incident summary

- Symptom: `poll_stats fetched=100 enqueued=0` repeated while `persist_ticks` disappeared for hours.
- Impact: SQLite file stopped growing; measured max gap reached ~3.27 hours.
- Temporary recovery: restart service and clear stale WAL/SHM side files.

### Root cause

- Single `last_seq` mixed multiple semantics.
- `poll` dedupe used this in-memory progress, which could run ahead of durable DB progress when persist path stalled.
- During queue backpressure / flush stall, pipeline could keep seeing upstream activity but stop durable writes, becoming a silent failure.

### Permanent fix

- Split sequence state:
  - `last_seen_seq`: upstream observed max seq (observability only)
  - `last_accepted_seq`: successfully enqueued max seq
  - `last_persisted_seq`: successfully committed max seq
- Poll dedupe baseline now uses `max(last_accepted_seq, last_persisted_seq)`; no longer depends on seen-only seq.
- Enqueue failure never advances accepted/persisted seq.
- Added watchdog: if upstream remains active and persist is stalled beyond threshold, process logs `WATCHDOG` and exits with code `2` for systemd auto-restart.

### Observability upgrades

- `poll_stats` now includes queue utilization, accepted/enqueued counts, drop reasons, and all three seq states.
- `persist_ticks` includes commit latency and ignored counts.
- `health` emits per-minute rollups for push/poll/persist/drop counters.

### Tests added

- Enqueue failure does not advance accepted/persisted seq.
- Push updates seen seq but does not poison poll dedupe baseline.
- Watchdog exits when upstream is active but persist remains stalled.
