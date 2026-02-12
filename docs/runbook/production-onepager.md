# 生產 Runbook（一頁版）

適用對象：Linux + `systemd` 部署的 `hk-tick-collector` 值班／維運人員。
目標：遇到問題時可在 1-3 分鐘內完成「看狀態 -> 看日誌 -> 看資料 -> 處理」。

## 0) 成功標準（先看）

- `sudo systemctl is-active hk-tick-collector` 回傳 `active`
- 日誌持續出現 `health connected=True`，盤中 `persisted_rows_per_min` 持續大於 0
- 最新 `ts_ms` 與現在時間差通常在秒級到數十秒
- 無連續 `WATCHDOG persistent_stall`／無頻繁重啟

## 1) 30 秒健康檢查

```bash
sudo systemctl status hk-tick-collector --no-pager
sudo systemctl status futu-opend --no-pager

DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
bash scripts/db_health_check.sh "$DB"
```

## 2) 最常用命令（可直接複製）

### 服務管理

```bash
sudo systemctl start hk-tick-collector
sudo systemctl stop hk-tick-collector
sudo systemctl restart hk-tick-collector
sudo systemctl enable --now hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

### 日誌

```bash
sudo journalctl -u hk-tick-collector -f --no-pager
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager \
  | grep -E "health|persist_summary|persist_loop_heartbeat|WATCHDOG|sqlite_busy|ERROR|Traceback"
sudo journalctl -u hk-tick-collector -b --no-pager | tail -n 200
```

### 資料檢查（按香港交易日）

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db

sqlite3 "file:${DB}?mode=ro" \
  "SELECT ROUND(strftime('%s','now')-MAX(ts_ms)/1000.0,3) AS lag_sec, COUNT(*) AS rows FROM ticks;"

sqlite3 "file:${DB}?mode=ro" \
  "SELECT symbol, COUNT(*) AS rows, MAX(seq) AS max_seq FROM ticks GROUP BY symbol ORDER BY rows DESC;"
```

## 3) 修改設定後的正確操作

### 修改生產環境變數

生產 unit 預設讀取：

- `/opt/futu_tick_downloader/.env`

步驟：

```bash
sudo vim /opt/futu_tick_downloader/.env
sudo systemctl restart hk-tick-collector
```

### 修改 unit 檔

步驟：

```bash
sudo vim /etc/systemd/system/hk-tick-collector.service
sudo systemctl daemon-reload
sudo systemctl restart hk-tick-collector
```

## 4) 常見故障處置

### A. `WATCHDOG persistent_stall`

1. 查看最近 10-30 分鐘日誌：

```bash
sudo journalctl -u hk-tick-collector --since "30 minutes ago" --no-pager \
  | grep -E "WATCHDOG|health|persist_summary|persist_loop_heartbeat|sqlite_busy|Traceback"
```

2. 判斷是否為「佇列上升 + persisted_rows_per_min 持續 0」。
3. 確認 DB 是否仍在前進：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" "SELECT COUNT(*), MAX(ts_ms) FROM ticks;"
```

4. 先做最小恢復：`sudo systemctl restart hk-tick-collector`
5. 若反覆觸發，按需調整：

- `WATCHDOG_STALL_SEC` 適度加大
- 檢查 `WATCHDOG_QUEUE_THRESHOLD_ROWS` 是否過低
- 檢查磁碟／權限／鎖衝突

### B. `database is locked` / `sqlite_busy_backoff` 頻繁

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
lsof "$DB" "$DB-wal" "$DB-shm" || true
fuser "$DB" "$DB-wal" "$DB-shm" || true
lslocks | grep -E "$(basename "$DB")|sqlite" || true
```

檢查是否有其他寫入程序；必要時增大 `SQLITE_BUSY_TIMEOUT_MS`。

### C. OpenD 連線異常

```bash
sudo systemctl status futu-opend --no-pager
nc -vz 127.0.0.1 11111
sudo systemctl restart futu-opend
sudo systemctl restart hk-tick-collector
```

### D. 時間看起來「差 8 小時」

- `ts_ms` 是 UTC epoch ms（正確語義）
- SQL 建議同時看 UTC 與 localtime：

```bash
sqlite3 "file:${DB}?mode=ro" \
  "SELECT datetime(MAX(ts_ms)/1000,'unixepoch') AS ts_utc, datetime(MAX(ts_ms)/1000,'unixepoch','localtime') AS ts_local FROM ticks;"
```

## 5) 備份與匯出（線上）

### 線上快照（建議）

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
SNAP=/data/sqlite/HK/${DAY}.snapshot.db
sqlite3 "$DB" ".backup '${SNAP}'"
```

### SCP 到本機

先修正私鑰權限（本機）：

```bash
chmod 600 <your-key>.pem
```

再複製：

```bash
scp -i <your-key>.pem user@server:/data/sqlite/HK/${DAY}.snapshot.db ./
```

## 6) 需要升級處理（Escalate）的訊號

- 連續 2 次以上 Watchdog 觸發，且重啟後仍復現
- DB lag 持續超過門檻（例如 >300 秒）且持續擴大
- 發生磁碟滿、唯讀檔案系統、持續權限錯誤
- OpenD 無法恢復連線

---

詳細版文件：

- 日常維運：[`operations.md`](operations.md)
- Watchdog 事件：[`incident-watchdog-stall.md`](incident-watchdog-stall.md)
- SQLite/WAL：[`sqlite-wal.md`](sqlite-wal.md)
- 資料品質：[`data-quality.md`](data-quality.md)
