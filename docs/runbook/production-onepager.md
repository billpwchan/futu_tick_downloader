# 生产 Runbook（一页版）

适用对象：Linux + systemd 部署的 `hk-tick-collector` 值班/运维人员。  
目标：遇到问题时能在 1-3 分钟内完成“看状态 -> 看日志 -> 看数据 -> 处理”。

## 0) 成功标准（先看这个）

- `sudo systemctl is-active hk-tick-collector` 返回 `active`
- 日志持续出现 `health connected=True`，盘中 `persisted_rows_per_min` 持续大于 0
- 最新 `ts_ms` 与当前时间差通常在秒级到几十秒
- 无连续 `WATCHDOG persistent_stall` / 无频繁重启

## 1) 30 秒健康检查

```bash
sudo systemctl status hk-tick-collector --no-pager
sudo systemctl status futu-opend --no-pager

DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
bash scripts/db_health_check.sh "$DB"
```

## 2) 最常用命令（可直接复制）

### 服务管理

```bash
sudo systemctl start hk-tick-collector
sudo systemctl stop hk-tick-collector
sudo systemctl restart hk-tick-collector
sudo systemctl enable --now hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

### 日志

```bash
sudo journalctl -u hk-tick-collector -f --no-pager
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager \
  | grep -E "health|persist_ticks|persist_loop_heartbeat|WATCHDOG|sqlite_busy|ERROR|Traceback"
sudo journalctl -u hk-tick-collector -b --no-pager | tail -n 200
```

### 数据检查（按香港交易日）

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db

sqlite3 "file:${DB}?mode=ro" \
  "SELECT ROUND(strftime('%s','now')-MAX(ts_ms)/1000.0,3) AS lag_sec, COUNT(*) AS rows FROM ticks;"

sqlite3 "file:${DB}?mode=ro" \
  "SELECT symbol, COUNT(*) AS rows, MAX(seq) AS max_seq FROM ticks GROUP BY symbol ORDER BY rows DESC;"
```

## 3) 修改配置后的正确操作

### 修改环境变量（生产）

生产 unit 默认读取：

- `/etc/hk-tick-collector.env`

步骤：

```bash
sudo vim /etc/hk-tick-collector.env
sudo systemctl restart hk-tick-collector
```

### 修改 unit 文件

步骤：

```bash
sudo vim /etc/systemd/system/hk-tick-collector.service
sudo systemctl daemon-reload
sudo systemctl restart hk-tick-collector
```

## 4) 常见故障处置

### A. `WATCHDOG persistent_stall`

1. 看最近 10-30 分钟日志：

```bash
sudo journalctl -u hk-tick-collector --since "30 minutes ago" --no-pager \
  | grep -E "WATCHDOG|health|persist_ticks|persist_loop_heartbeat|sqlite_busy|Traceback"
```

2. 看是否“队列上涨 + persisted_rows_per_min 持续 0”。
3. 看 DB 是否前进：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" "SELECT COUNT(*), MAX(ts_ms) FROM ticks;"
```

4. 先做最小恢复：`sudo systemctl restart hk-tick-collector`
5. 若反复触发，按需调整：
- `WATCHDOG_STALL_SEC` 适度增大
- 检查 `WATCHDOG_QUEUE_THRESHOLD_ROWS` 是否过低
- 检查磁盘/权限/锁冲突

### B. `database is locked` / `sqlite_busy_backoff` 频繁

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
lsof "$DB" "$DB-wal" "$DB-shm" || true
fuser "$DB" "$DB-wal" "$DB-shm" || true
lslocks | grep -E "$(basename "$DB")|sqlite" || true
```

检查是否有其他写进程；必要时增大 `SQLITE_BUSY_TIMEOUT_MS`。

### C. OpenD 连接异常

```bash
sudo systemctl status futu-opend --no-pager
nc -vz 127.0.0.1 11111
sudo systemctl restart futu-opend
sudo systemctl restart hk-tick-collector
```

### D. 时间看起来“差 8 小时”

- `ts_ms` 是 UTC epoch ms（这是正确语义）
- SQL 展示请同时看 UTC 与 localtime：

```bash
sqlite3 "file:${DB}?mode=ro" \
  "SELECT datetime(MAX(ts_ms)/1000,'unixepoch') AS ts_utc, datetime(MAX(ts_ms)/1000,'unixepoch','localtime') AS ts_local FROM ticks;"
```

## 5) 备份与导出（在线）

### 在线快照（推荐）

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
SNAP=/data/sqlite/HK/${DAY}.snapshot.db
sqlite3 "$DB" ".backup '${SNAP}'"
```

### SCP 到本地

先修正私钥权限（本地）：

```bash
chmod 600 <your-key>.pem
```

再拷贝：

```bash
scp -i <your-key>.pem user@server:/data/sqlite/HK/${DAY}.snapshot.db ./
```

## 6) 需要升级处理（Escalate）的信号

- 连续 2 次以上 watchdog 触发且重启后仍复现
- DB lag 持续超过阈值（例如 >300 秒）并继续扩大
- 出现磁盘满、只读文件系统、持续权限错误
- OpenD 无法恢复连接

---

详细版文档：

- 日常运维：[`operations.md`](operations.md)
- WATCHDOG 事件：[`incident-watchdog-stall.md`](incident-watchdog-stall.md)
- SQLite/WAL：[`sqlite-wal.md`](sqlite-wal.md)
- 数据质量：[`data-quality.md`](data-quality.md)
