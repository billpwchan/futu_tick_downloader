# 操作手冊：日常維運

## 目的

定義 Linux + `systemd` 下 `hk-tick-collector` 的日常維運流程（Business-as-usual）。

## 前置條件

- 可使用 `sudo` 檢查服務與日誌
- 可存取資料目錄 `/data/sqlite/HK`

單頁速查：[`production-onepager.md`](production-onepager.md)

## 步驟

### 1) 開盤前檢查

1. 服務狀態：

```bash
sudo systemctl status hk-tick-collector --no-pager
sudo systemctl status futu-opend --no-pager
```

2. 設定合理性：

```bash
grep -E '^(FUTU_HOST|FUTU_PORT|FUTU_SYMBOLS|DATA_ROOT)=' /opt/futu_tick_downloader/.env
```

3. 磁碟容量：

```bash
df -h /data/sqlite/HK
```

### 2) 盤中檢查

1. 心跳日誌：

```bash
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager \
  | grep -E "health|persist_ticks|persist_loop_heartbeat|WATCHDOG"
```

2. 新鮮度與 row 成長：

```bash
bash scripts/db_health_check.sh
```

3. 佇列／Watchdog 訊號：

- 觀察是否重複 `sqlite_busy_backoff`
- 觀察是否出現 `WATCHDOG persistent_stall`

### 3) 收盤後檢查

1. 最終新鮮度快照：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
bash scripts/db_health_check.sh "$DB"
```

2. 備份當日 DB。
3. 需要時記錄 table 統計，供容量追蹤。

### 4) 備份流程（服務運行中）

WAL 安全線上快照：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
SNAP=/data/sqlite/HK/${DAY}.snapshot.db
sqlite3 "$DB" ".backup '${SNAP}'"
```

最佳實務：

- 線上備份優先使用 `.backup`，避免直接複製原始檔。
- 快照存放在不同磁碟或遠端儲存。
- 對備份檔做 checksum。

### 5) 透過 SCP 匯出到本機

本機執行：

```bash
scp user@server:/data/sqlite/HK/20260211.db ./20260211.db
```

若頻寬受限，先在伺服器壓縮：

```bash
gzip -c /data/sqlite/HK/20260211.db > /tmp/20260211.db.gz
scp user@server:/tmp/20260211.db.gz ./
```

### 6) 保留與輪替

範例策略（保留 30 天 DB + 每週快照）：

```bash
find /data/sqlite/HK -name '*.db' -type f -mtime +30 -print
```

執行刪除前，需先完成備份驗證並取得業務核准。

建議：

- 以 cron 或 systemd timer 自動檢查 retention。
- 設定磁碟水位告警。

### 7) 值班常用命令

```bash
sudo systemctl restart hk-tick-collector
sudo journalctl -u hk-tick-collector -f
bash scripts/db_health_check.sh
```

## 如何驗證

- 所有巡檢命令可順利執行。
- 盤中 `persisted_rows_per_min` 維持 > 0。
- 快照檔可被成功匯出並可查詢。

## 常見問題

- 快照過大：可先壓縮後傳輸。
- 盤中頻繁 `sqlite_busy_backoff`：需檢查是否有額外寫入程序。

## 參考

- Watchdog 事件流程：[`incident-watchdog-stall.md`](incident-watchdog-stall.md)
- SQLite 維運細節：[`sqlite-wal.md`](sqlite-wal.md)
- 資料品質檢查：[`data-quality.md`](data-quality.md)
