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

## 2026-02-11: watchdog self-heal first + future-ts repair toolkit

### Incident pattern

- 仍出现 `WATCHDOG persistent_stall` + `status=2/INVALIDARGUMENT` 循环重启。
- 核验 SQL 发现 `MAX(ts_ms)` 超前 `now_utc` 约 +8h。
- `lsof` 未稳定复现 DB 锁，说明“仅以锁冲突解释”不充分，watchdog 判据与恢复路径需要加强。

### Final fixes

- 时间戳:
  - `mapping.parse_time_to_ts_ms` 强制 `Asia/Hong_Kong -> UTC epoch ms`。
  - 增加 compact 时间解析（`HHMMSS` / `YYYYMMDDHHMMSS`）。
  - 对“明显 +8h 未来值”自动纠偏并告警日志。
- seed:
  - `main` 改为跨最近交易日 DB 取 `max(seq)`，不依赖 `ts<=now`。
- persist:
  - 新增 writer 自愈接口：请求重建 worker/writer（关闭连接并重启线程）。
  - 所有落库异常都带 traceback 日志，且会重置 sqlite connection。
  - heartbeat 默认 30s，增加 `wal_bytes`、`last_commit_rows`、`recovery_count`。
- watchdog:
  - 基于 `last_dequeue_monotonic`/`last_commit_monotonic` + 持续 backlog 判定 stall。
  - 触发时先 dump 全线程栈并执行 writer 自愈。
  - 连续自愈失败 N 次后才 `exit(2)` 交给 systemd。

### New Ops scripts

- `scripts/repair_future_ts_ms.py`
  - 只修 `ts_ms > now + 2h`，默认 `-8h`，并同步修正 `trading_day`。
- `scripts/verify_hk_tick_collector.sh`
  - 输出 `now_utc/max_ts_utc/max_minus_now_sec/rows` + recent watchdog + pragma。
- `scripts/redeploy_hk_tick_collector.sh`
  - stop -> deploy -> test -> repair -> start -> log acceptance -> verify -> (失败自动回滚)。
- `scripts/rollback_hk_tick_collector.sh`
  - 手动指定 `ROLLBACK_REF` 一键回滚并拉起服务。

### Verification commands

- `bash scripts/verify_hk_tick_collector.sh`
- `python3 scripts/repair_future_ts_ms.py --data-root /data/sqlite/HK --day $(TZ=Asia/Hong_Kong date +%Y%m%d)`
- `journalctl -u hk-tick-collector --since \"30 minutes ago\" --no-pager | grep -E \"WATCHDOG|persist_loop_heartbeat|health|persist_ticks\"`

### Common pitfalls

- 将 HK 本地时间直接当 UTC 存储，导致 `ts_ms` 偏移 +28800 秒。
- 只看 `persisted_rows_per_min` 判停写，忽略 dequeue/commit heartbeat。
- 新连接直接查 `PRAGMA busy_timeout` 时读到 0（连接级参数），应结合服务配置/日志判断。
