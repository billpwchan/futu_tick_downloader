# Runbook: hk-tick-collector

适用服务：`hk-tick-collector.service`

## 0. 已修复的关键根因（2026-02-11）

- 根因：`WATCHDOG_QUEUE_THRESHOLD_ROWS` 过去未接入 watchdog 判定，导致小 backlog 时也可能触发恢复流程。
- 修复：watchdog 现在仅在 `queue_size >= WATCHDOG_QUEUE_THRESHOLD_ROWS` 时才进入 stall 判定。
- 回归保护：`tests/test_futu_client.py::test_watchdog_honors_queue_threshold`。

## 1. SLO / 成功标准

建议把以下作为日常健康基线（可按业务调整）：

- 数据延迟（lag）：
  - 交易时段 `MAX(ts_ms)` 距离当前时间建议 `< 15s`
  - 非交易时段允许无增长
- 写入速率：`persist_ticks` 持续出现，`persisted_rows_per_min > 0`（交易时段）
- 队列水位：`queue_size / queue_maxsize < 0.7`
- 重复率：
  - `dropped_duplicate` 高不必然异常
  - 若 `dropped_duplicate` 高且 `persist_ticks` 停止、lag 增长，才视为异常
- watchdog：
  - 先自愈（`WATCHDOG recovery_triggered`）
  - 连续失败才会 `WATCHDOG persistent_stall` 并退出交给 systemd

## 2. 日常巡检

### 2.1 看服务状态

```bash
sudo systemctl status hk-tick-collector --no-pager
sudo systemctl status futu-opend --no-pager
```

### 2.2 看关键日志

```bash
bash scripts/tail_logs.sh
```

重点关键字：

- `health`
- `persist_ticks`
- `persist_loop_heartbeat`
- `poll_stats`
- `WATCHDOG`
- `sqlite_busy_backoff`

### 2.3 看 DB 基线

```bash
bash scripts/verify_db.sh
bash scripts/healthcheck.sh
```

## 3. 标准验证 SQL（可复制）

默认变量：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
```

### 3.1 最新 5 条（UTC 与 localtime 对照）

```sql
SELECT
  symbol,
  seq,
  ts_ms,
  datetime(ts_ms/1000,'unixepoch') AS ts_utc,
  datetime(ts_ms/1000,'unixepoch','localtime') AS ts_local,
  recv_ts_ms,
  datetime(recv_ts_ms/1000,'unixepoch') AS recv_utc,
  price,
  volume,
  push_type,
  provider
FROM ticks
ORDER BY ts_ms DESC
LIMIT 5;
```

### 3.2 最新 lag（UTC 展示）

```sql
SELECT
  datetime(strftime('%s','now'),'unixepoch') AS now_utc,
  datetime(MAX(ts_ms)/1000,'unixepoch') AS max_ts_utc,
  (strftime('%s','now') - MAX(ts_ms)/1000.0) AS lag_sec
FROM ticks;
```

### 3.3 最新 lag（localtime 展示）

```sql
SELECT
  datetime(strftime('%s','now'),'unixepoch','localtime') AS now_local,
  datetime(MAX(ts_ms)/1000,'unixepoch','localtime') AS max_ts_local,
  (strftime('%s','now') - MAX(ts_ms)/1000.0) AS lag_sec
FROM ticks;
```

### 3.4 最近 N 分钟按 symbol 统计

```sql
-- 例：最近 10 分钟
SELECT
  symbol,
  COUNT(*) AS rows_10m,
  MAX(seq) AS max_seq,
  datetime(MAX(ts_ms)/1000,'unixepoch') AS latest_ts_utc
FROM ticks
WHERE ts_ms >= (strftime('%s','now') - 600) * 1000
GROUP BY symbol
ORDER BY rows_10m DESC;
```

### 3.5 重复检查

有唯一索引时：

- `INSERT OR IGNORE` 会把重复记录计入 `ignored`，这是预期幂等行为。

手工检查（理论应为 0）：

```sql
-- seq 非空的重复
SELECT symbol, seq, COUNT(*) AS dup_cnt
FROM ticks
WHERE seq IS NOT NULL
GROUP BY symbol, seq
HAVING COUNT(*) > 1
LIMIT 20;

-- seq 为空时按组合键检查
SELECT symbol, ts_ms, price, volume, turnover, COUNT(*) AS dup_cnt
FROM ticks
WHERE seq IS NULL
GROUP BY symbol, ts_ms, price, volume, turnover
HAVING COUNT(*) > 1
LIMIT 20;
```

### 3.6 PRAGMA 校验

```sql
PRAGMA journal_mode;
PRAGMA busy_timeout;
PRAGMA synchronous;
PRAGMA wal_autocheckpoint;
```

说明：`busy_timeout` 是连接级参数，新开只读连接读到的值不一定等于服务进程连接值；请结合服务配置和 `sqlite_pragmas` 日志判断。

## 4. 故障处置手册（场景化）

以下每节均按“现象 -> 判断 -> 命令 -> 结论 -> 修复 -> 复盘要点”。

### 场景 1：`WATCHDOG persistent_stall` 反复触发

- 现象：日志出现 `WATCHDOG persistent_stall`，服务频繁重启。
- 判断：这是“上游活跃 + backlog 超阈值 + commit 停滞/worker_dead + 自愈连续失败”的信号。
- 命令：

```bash
sudo journalctl -u hk-tick-collector --since "30 minutes ago" --no-pager | grep -E "WATCHDOG|persist_loop_heartbeat|persist_ticks|sqlite_busy|persist_flush_failed"
bash scripts/verify_db.sh
bash scripts/healthcheck.sh
```

- 结论：
  - 若有大量 `sqlite_busy_backoff` / `readonly` / `disk I/O`，优先按 SQLite 路径处理。
  - 若 queue 持续增长但 `persist_ticks` 消失，说明落库链路阻塞。
- 修复：
  - 校验目录权限、磁盘容量、WAL 文件状态；
  - 必要时重启服务：`sudo systemctl restart hk-tick-collector`；
  - 持续复发时先修根因（锁冲突/磁盘/权限），不要只靠重启。
- 复盘要点：
  - 记录触发时段 `queue/commit_age/last_exception_type`；
  - 归档对应 `WATCHDOG diagnostic_dump` 线程栈。

### 场景 2：SQLite `locked/busy/readonly`

- 现象：日志有 `sqlite_busy_backoff`、`database is locked` 或 `attempt to write a readonly database`。
- 判断：
  - `locked/busy` 常见于并发写或 checkpoint 压力；
  - `readonly` 常见于权限或 `ProtectSystem` 限制。
- 命令：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
ls -l /data/sqlite/HK
ls -l ${DB}*
# 只读打开验证
sqlite3 "file:${DB}?mode=ro" "PRAGMA journal_mode; PRAGMA busy_timeout;"
```

- 结论：
  - 权限异常：修 owner/group/mode；
  - 锁冲突：检查是否有额外写入进程。
- 修复：

```bash
sudo chown -R hkcollector:hkcollector /data/sqlite/HK
sudo chmod -R 750 /data/sqlite
sudo systemctl restart hk-tick-collector
```

- 复盘要点：
  - 记录 busy 峰值与 backoff 频次；
  - 评估是否需要提高 `SQLITE_BUSY_TIMEOUT_MS`。

### 场景 3：大量 `dropped_duplicate`、`poll_accepted=0` 导致误判

- 现象：`poll_fetched` 高，但 `poll_accepted=0`，`dropped_duplicate` 很高。
- 判断：
  - 若 `queue` 不涨、`persist_ticks` 持续、lag 正常，这是“重复数据被成功去重”，不是故障。
  - 仅当 lag 持续恶化且无 `persist_ticks` 才处理为异常。
- 命令：

```bash
sudo journalctl -u hk-tick-collector --since "15 minutes ago" --no-pager | grep -E "poll_stats|health|persist_ticks"
bash scripts/verify_db.sh
```

- 结论：
  - 健康场景可忽略重复率高；
  - 异常场景通常伴随 queue backlog 与 commit 停滞。
- 修复：
  - 健康场景无需动作；
  - 异常场景按场景 1 / 2 继续排查。
- 复盘要点：
  - 区分“去重有效”与“落库停滞”。

### 场景 4：`ts_ms` 与 now 差 8 小时或大幅漂移

- 现象：SQL 看起来晚/早 8 小时，或 `ts_drift_warn` 高频告警。
- 判断：
  - `datetime(...,'unixepoch')` 是 UTC 显示，不是本地时间；
  - 若 `lag_sec` 本身正常，通常只是展示时区误解；
  - 若 `lag_sec` 实际接近 `±28800`，才是时间戳异常。
- 命令：

```bash
bash scripts/verify_db.sh
python3 scripts/check_ts_semantics.py --db /data/sqlite/HK/$(TZ=Asia/Hong_Kong date +%Y%m%d).db --tolerance-sec 30
```

- 结论：
  - 展示误差：改用 UTC/localtime 双列展示；
  - 真异常：可能历史数据曾用错误时区写入。
- 修复：
  - 历史修复工具：`python3 scripts/repair_future_ts_ms.py --data-root /data/sqlite/HK --day <YYYYMMDD>`；
  - 修复后再次执行语义校验脚本。
- 复盘要点：
  - 在报表层明确 `ts_ms` 是 UTC epoch。

### 场景 5：停止服务时 flush timeout（`STOP_FLUSH_TIMEOUT_SEC`）

- 现象：停机日志出现 `collector_stop_timeout` 或 `collector flush timed out during shutdown`。
- 判断：
  - 通常是 backlog 较大、写盘慢或锁冲突导致无法在超时内排空。
- 命令：

```bash
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager | grep -E "collector_stop_timeout|persist_ticks|sqlite_busy|WATCHDOG"
```

- 结论：
  - 若 backlog 常见，需增大停机窗口；
  - 若伴随 busy/readonly，先解锁或修权限。
- 修复：
  - 调大 `.env` 中 `STOP_FLUSH_TIMEOUT_SEC`（如 `60 -> 120`）；
  - 同步增大 unit 的 `TimeoutStopSec`（应 >= `STOP_FLUSH_TIMEOUT_SEC`）。
- 复盘要点：
  - 记录停机时 queue 水位和 commit 速率，评估容量参数。

## 5. 灾备与恢复

### 5.1 DB 备份

在线热备（WAL 模式推荐）：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "$DB" ".backup '/data/sqlite/HK/${DAY}.backup.db'"
```

### 5.2 磁盘满

- 现象：写入报错、lag 持续恶化。
- 处理：

```bash
df -h
sudo du -sh /data/sqlite/HK/* | sort -h | tail
```

清理历史归档后重启服务并验证。

### 5.3 WAL 文件异常

- 现象：`*.db-wal` 异常膨胀或写入抖动。
- 处理：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);"
```

注意：请在低峰执行，避免影响在线写入。

### 5.4 恢复步骤（最小）

1. 保留现场日志（至少最近 30 分钟）。
2. 备份当前 DB。
3. 修权限/磁盘/配置。
4. 重启服务。
5. 执行 `scripts/healthcheck.sh` 与 `scripts/verify_db.sh`。
6. 记录根因与修复动作到项目 memory。

## 6. 启动后 3 分钟验收清单

- `health` 日志持续输出；
- `persist_ticks` 持续出现；
- DB `lag_sec` 在阈值内；
- 断开 SSH 后服务仍在（systemd 托管）。

```bash
# 3 分钟窗口验证
sudo journalctl -u hk-tick-collector --since "3 minutes ago" --no-pager | grep -E "health|persist_ticks|WATCHDOG"

# 服务是否与 SSH 会话无关
systemctl is-active hk-tick-collector
```

## 7. 变更后标准验证

- 执行：`bash scripts/healthcheck.sh`
- 执行：`bash scripts/verify_db.sh`
- 执行：`bash scripts/tail_logs.sh`

若三项均通过，可判定本次变更可用。
