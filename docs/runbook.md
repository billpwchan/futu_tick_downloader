# 操作手冊（BAU + Incident SOP）

## 目的

提供 `hk-tick-collector` 生產值班人員日常巡檢與事件處置的標準步驟。

## 前置條件

- 主機以 Linux + `systemd` 部署
- 可使用 `sudo` 讀取服務狀態與日誌
- 可存取當日資料庫路徑（預設 `/data/sqlite/HK`）

## 步驟

### 1) 健康檢查清單

建議每日或 on-call 巡檢時執行。

服務狀態：

```bash
sudo systemctl status hk-tick-collector --no-pager
```

近期日誌（心跳 + 落盤 + Watchdog + notifier）：

```bash
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager \
  | grep -E "health|persist_ticks|persist_loop_heartbeat|WATCHDOG|telegram|sqlite_busy"
```

DB 新鮮度／漂移：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" \
  "SELECT COUNT(*) AS rows, MAX(ts_ms) AS max_ts_ms, ROUND(strftime('%s','now')-MAX(ts_ms)/1000.0,3) AS drift_sec FROM ticks;"
```

健康基線：

- `health` 與 `persist_ticks` 持續出現。
- 盤中 `persisted_rows_per_min` 通常大於 0。
- `drift_sec` 不應持續異常擴大。

### 2) 告警 SOP：`PERSIST_STALL`

Telegram 告警範例：

```text
🚨 HK Tick Collector · PERSIST STALL
...
suggest: journalctl -u hk-tick-collector -n 200 --no-pager
suggest: sqlite3 /data/sqlite/HK/<day>.db 'select count(*) from ticks;'
```

處置步驟：

1. 立即保存上下文：

```bash
sudo journalctl -u hk-tick-collector -n 300 --no-pager
```

2. 檢查 queue／watchdog／persist 訊號：

- 是否反覆 `WATCHDOG` recovery failure
- 是否長時間無 `persist_ticks`
- queue 指標是否持續上升

3. 驗證 DB 仍可讀寫：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" "SELECT COUNT(*), MAX(ts_ms) FROM ticks;"
```

4. 若仍停滯，重啟：

```bash
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

5. 在 5 分鐘內確認恢復：

- `persist_ticks` 已恢復
- `persisted_rows_per_min` 回升
- 無新的 `PERSIST_STALL` 連發告警（受 cooldown 保護）

### 3) 資料匯出（SCP）與權限

建議先做線上快照：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
SNAP=/data/sqlite/HK/${DAY}.snapshot.db
sqlite3 "$DB" ".backup '${SNAP}'"
```

複製到本機：

```bash
scp user@server:/data/sqlite/HK/${DAY}.snapshot.db ./
```

權限原則：

- 資料目錄由服務帳號（`hkcollector`）持有。
- 避免將市場資料檔案設為 world-readable。
- 避免直接複製仍在寫入中的 `.db-wal`／`.db-shm`。

### 4) 災難復原

#### A) DB 毀損或無法開啟

1. 停止服務：

```bash
sudo systemctl stop hk-tick-collector
```

2. 保留故障 DB 供鑑識。
3. 從最近快照回復。
4. 啟動服務並驗證匯入流程。

#### B) 磁碟已滿

1. 確認：

```bash
df -h /data/sqlite/HK
```

2. 釋放空間（清理保留策略、移動舊快照）。
3. 重啟 collector。
4. 觀察 busy/locked 與 WAL 成長是否異常。

#### C) 節點重開機

1. 確認 `hk-tick-collector` 自動啟動。
2. 確認 Telegram 摘要在下一個週期恢復。
3. 驗證當日 `YYYYMMDD` DB row 持續增加。

### 5) 輕量資源監控

最低建議監控：

- CPU load（`load1`）
- Process RSS（`rss_mb`）
- 磁碟剩餘（`disk_free_gb`）

當 `TELEGRAM_INCLUDE_SYSTEM_METRICS=1` 時，以上指標已包含在摘要訊息。

## 如何驗證

- 巡檢命令可在 1-3 分鐘內完成。
- 事件處置後可觀察到 `persist_ticks` 恢復與 DB 持續前進。

## 常見問題

- 一重啟就恢復但很快再發：需追根因（磁碟延遲、權限、鎖競爭、OpenD 穩定性），不要只做重啟。
- 告警太多：先調整 `TELEGRAM_*` 噪音門檻，再檢查實際系統壓力。

## 參考文件

- 部署：[`docs/deployment.md`](deployment.md)
- Telegram 設定：[`docs/telegram.md`](telegram.md)
- 詳細維運：[`docs/runbook/operations.md`](runbook/operations.md)
