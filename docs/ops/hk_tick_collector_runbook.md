# hk-tick-collector 运维 Runbook

## 1. 关键规则（时间戳与交易日）

- `ticks.ts_ms` 必须是 UTC epoch milliseconds。
- Futu `time` 字段按港股市场本地时间（`Asia/Hong_Kong`）解释，再转换到 UTC。
- 若仅有 `HH:MM:SS`，必须与 `trading_day(YYYYMMDD)` 拼接后再做时区转换。
- `trading_day` 从 `ts_ms` 反推时也使用 `Asia/Hong_Kong`，不依赖服务器系统时区。

## 2. watchdog 触发条件

watchdog 每分钟在 `health` 周期内检查，满足以下全部条件即触发 `WATCHDOG persistent_stall` 并非 0 退出，交给 systemd 拉起：

- 上游近期活跃：`push` 或 `poll` 仍有推进；
- `persisted_rows_per_min == 0`；
- `persist_stall_sec >= WATCHDOG_STALL_SEC`；
- `persist_stall_sec` 以 monotonic 时钟计算，起点是最近一次成功 commit。

## 3. 关键可观测字段

- `poll_stats`:
  - `queue_in` / `queue_out`
  - `last_commit_monotonic_age_sec`
  - `db_write_rate`
  - `ts_drift_sec`
  - `last_seen_seq` / `last_accepted_seq` / `last_persisted_seq`
- `health`:
  - `persisted_rows_per_min` / `ignored_rows_per_min`
  - `queue_in` / `queue_out` / `db_commits_per_min`
  - `last_commit_monotonic_age_sec`
  - `ts_drift_sec` / `max_ts_utc`

当 `|ts_drift_sec| > DRIFT_WARN_SEC` 会打印 `ts_drift_warn`。

## 4. 验证 SQL（线上排障）

假设：

```bash
TODAY_HK=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${TODAY_HK}.db
```

检查 drift：

```sql
SELECT
  datetime(strftime('%s','now'),'unixepoch') AS now_utc,
  datetime(MAX(ts_ms)/1000,'unixepoch') AS max_ts_utc,
  (strftime('%s','now') - MAX(ts_ms)/1000.0) AS drift_sec
FROM ticks;
```

检查最近 10 分钟窗口：

```sql
SELECT
  COUNT(*) AS n,
  datetime(MIN(ts_ms)/1000,'unixepoch') AS min_utc,
  datetime(MAX(ts_ms)/1000,'unixepoch') AS max_utc
FROM ticks
WHERE ts_ms >= (strftime('%s','now') - 600) * 1000;
```

检查最大间隔（近 24h）：

```sql
WITH x AS (
  SELECT ts_ms, LAG(ts_ms) OVER (ORDER BY ts_ms) AS prev
  FROM ticks
  WHERE ts_ms >= (strftime('%s','now') - 86400) * 1000
)
SELECT
  MAX((ts_ms - prev) / 1000.0) AS max_gap_sec
FROM x
WHERE prev IS NOT NULL;
```

## 5. 常见故障与处理

### 5.1 时间轴错乱（典型偏移 +28800 秒）

- 现象：`drift_sec` 长期接近 `-28800` 或 `+28800`，最近 10 分钟窗口查不到新数据。
- 处理：
  1. 确认服务已升级到本次修复版本（`mapping.py` 使用 `ZoneInfo('Asia/Hong_Kong')`）。
  2. 运行 redeploy 验收脚本，核对 `now_utc/max_ts_utc/drift_sec`。

### 5.2 persistent_stall

- 现象：日志出现 `WATCHDOG persistent_stall`，systemd 重启次数上升。
- 处理：
  1. 检查同时间窗是否存在 `persist_loop_exited`、`persist_flush_failed`、`OperationalError`。
  2. 检查 DB 文件与 WAL/SHM 权限是否归属 `hkcollector`。
  3. 检查 `queue` 是否持续增长且 `queue_out` 不变。

### 5.3 停机 flush 超时

- 现象：出现 `collector_stop_timeout`。
- 处理：
  1. 调大 `STOP_FLUSH_TIMEOUT_SEC`（例如 60 -> 120）。
  2. 同步调大 unit `TimeoutStopSec`（建议 >= `STOP_FLUSH_TIMEOUT_SEC`）。

## 6. 一键重部署与验收

```bash
sudo scripts/redeploy_hk_tick_collector.sh
```

可选参数：

- `TARGET_DIR`（默认 `/opt/futu_tick_downloader`）
- `BRANCH`（默认 `main`）
- `SERVICE_NAME`（默认 `hk-tick-collector`）
- `ENV_FILE`（默认 `/etc/hk-tick-collector.env`）
- `LOG_SCAN_SECONDS`（默认 `120`）
- `REPO_URL`（目标目录不存在时必填）
