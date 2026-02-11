# HK Tick Collector Engineering Notes

## 1. Incident Summary

Production repeatedly hit:

- `WATCHDOG persistent_stall ... queue>0 persisted_rows_per_min=0`
- systemd restart with non-zero status
- SQLite `ticks.ts_ms` ahead of UTC `now` by about `+8h` (`max_minus_now_sec ~ 27979~28760`)

and DB row growth stayed low/interrupted.

## 2. Root Cause

### 2.1 `ts_ms` timezone drift (+8h)

- Futu ticker `time` is market-local time for HK equities (`Asia/Hong_Kong`) and is often timezone-naive.
- Bad paths treated timezone-naive values (or epoch-like numerics) as UTC directly.
- Result: persisted `ts_ms` moved to the future by roughly 8 hours.

### 2.2 Watchdog false-positive/fail-open mix

- Old watchdog was tied to coarse per-minute counters and queue pressure.
- Persist thread could stall (or become unhealthy) while counters were not sufficient for robust diagnosis.
- On trigger, process exited immediately, skipping self-heal.

## 3. Code Fixes

## 3.1 Timestamp conversion hardening

- `hk_tick_collector/mapping.py`
  - Enforced timezone-aware conversion path: `Asia/Hong_Kong -> UTC epoch ms`.
  - Added compact numeric time parsing support (`HHMMSS`, `YYYYMMDDHHMMSS`).
  - Added guardrail: for obvious `+8h` future epoch values, auto-correct by `-8h` and log warning.

## 3.2 Robust seq seeding

- `hk_tick_collector/db.py`, `hk_tick_collector/main.py`
  - Added recent-day seed strategy: collect max `seq` across recent DB files, not only one day.
  - Seed remains `seq`-first and does not depend on `ts_ms <= now` filters.

## 3.3 Persist loop resilience and observability

- `hk_tick_collector/collector.py`
  - Added explicit writer recovery API (`request_writer_recovery`) that closes/rebuilds worker/writer.
  - Persist exceptions always log traceback (`exc_info=True`), not silent failures.
  - On any DB write exception, reset sqlite connection and continue with backoff.
  - Added runtime heartbeats (`persist_loop_heartbeat`, default every 30s) including:
    - queue size
    - dequeue/commit rates
    - last exception counters
    - `last_commit_rows`
    - WAL size (`wal_bytes`)
    - recovery count
  - Runtime state now exports:
    - `last_dequeue_monotonic`
    - `last_commit_monotonic`
    - `last_commit_rows`
    - `last_exception_monotonic`

## 3.4 Watchdog redesign (self-heal first)

- `hk_tick_collector/futu_client.py`
  - Trigger now requires:
    - upstream active window
    - queue backlog above threshold and sustained
    - dequeue/commit heartbeat stale (or worker dead)
  - On stall:
    1. dump all thread stacks (`faulthandler.dump_traceback(all_threads=True)`)
    2. attempt collector writer recovery
    3. only exit `1` after consecutive recovery failures (`WATCHDOG_RECOVERY_MAX_FAILURES`)

## 3.5 Fault diagnostics

- `hk_tick_collector/main.py`
  - Keep `faulthandler.enable(..., all_threads=True)`
  - Keep `SIGUSR1` thread dump registration for on-demand diagnostics.

## 4. Data Repair Tool

- `scripts/repair_future_ts_ms.py`
  - Repairs rows where `ts_ms > now + 2h` (default threshold).
  - Default correction: `ts_ms -= 8h`.
  - Also recalculates `trading_day` from corrected timestamp (`Asia/Hong_Kong` perspective).
  - Prints before/after sample rows and repaired row count.

## 5. Deploy / Rollback / Verify

- Deploy: `scripts/redeploy_hk_tick_collector.sh`
  - stop service
  - git sync to target ref/branch
  - install deps
  - run tests
  - run future-ts repair
  - start service
  - collect acceptance logs
  - run verify script
  - auto rollback on failure (optional, default enabled)

- Manual rollback: `scripts/rollback_hk_tick_collector.sh`
  - required input: `ROLLBACK_REF=<previous_commit>`
  - stop service -> checkout rollback ref -> reinstall runtime deps -> start service

- Verify: `scripts/verify_hk_tick_collector.sh`
  - outputs `now_utc`, `max_ts_utc`, `max_minus_now_sec`, `rows`
  - outputs sqlite pragma checks and recent watchdog count
  - outputs recent `health/persist_ticks/persist_loop_heartbeat` logs

## 6. Acceptance Targets

- `max_minus_now_sec` should stay near `[-60, +60]` in normal market flow.
- `journal_mode=wal`, `busy_timeout>=1000`.
- no `WATCHDOG persistent_stall` for continuous run.
- rows keep growing and health logs show persist activity.
