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

## 2026-02-10: timestamp drift and persist-loop hardening

### Incident summary

- `ts_ms` 出现与 UTC epoch 偏离约 8 小时，导致基于 `strftime('%s','now')` 的窗口查询失真。
- 线上出现 `WATCHDOG persistent_stall`，表现为上游仍活跃但 `persisted_rows_per_min=0` 且队列增长。

### Root cause

- 时间解析对 naive datetime 依赖系统时区，未显式按 `Asia/Hong_Kong` 解释市场时间。
- persist loop 对落库异常仅记录后继续，缺少“重试上限 + fatal 信号”，可能形成长期停摆风险。

### Permanent fix

- `mapping.parse_time_to_ts_ms` 统一为 `Asia/Hong_Kong -> UTC epoch ms` 转换。
- `trading_day_from_ts` 改为 UTC->HK 反推，摆脱系统时区依赖。
- collector 持久化新增重试与 fatal 机制：
  - `persist_flush_failed` 带上下文（queue/db path/last seq）日志。
  - 超过重试上限触发 `persist_loop_exited`，主流程非零退出。
- health/poll 增加 `queue_in/queue_out`、`last_commit_monotonic_age_sec`、`db_write_rate`、`ts_drift_sec`。
- watchdog stall 仅基于 monotonic commit 时间计算。

### Ops assets

- 新增 `scripts/redeploy_hk_tick_collector.sh`（拉代码/装依赖/重启/SQL+日志验收）。
- 新增 `docs/ops/hk_tick_collector_runbook.md`（时间规则、watchdog、排障 SQL）。
