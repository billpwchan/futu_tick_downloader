# 故障排除

## 目的

快速定位 `hk-tick-collector` 在生產環境常見異常，並提供最短修復路徑。

## 前置條件

- 可使用 `sudo` 查看 `systemd` 與 `journalctl`
- 可讀取當日 SQLite 檔案

## 步驟

### 1) 服務無法啟動

檢查命令：

```bash
sudo systemctl status hk-tick-collector --no-pager
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager
```

常見原因：

- `FUTU_SYMBOLS` 為空
- 數值型環境變數格式錯誤
- 資料目錄權限不足

### 2) OpenD 連線失敗

症狀：

- subscribe 失敗
- 日誌持續重連

處理：

```bash
sudo systemctl status futu-opend --no-pager
nc -vz 127.0.0.1 11111
```

確認 `FUTU_HOST/FUTU_PORT` 與 OpenD 設定一致。

### 3) `WATCHDOG persistent_stall`

請使用專用事件操作手冊：

- [`docs/runbook/incident-watchdog-stall.md`](runbook/incident-watchdog-stall.md)

### 4) SQLite Busy / Locked

- 檢查鎖持有者與重試狀況
- 觀察 `sqlite_busy_backoff` 頻率
- 參考 [`docs/runbook/sqlite-wal.md`](runbook/sqlite-wal.md)

### 5) WAL 檔案持續膨脹

- 確認 auto-checkpoint 設定
- 確認 writer 是否持續推進
- 必要時在受控維護時段手動 checkpoint

### 6) 時區／時間戳看起來不一致

- `ts_ms` 為 UTC epoch ms
- SQLite `datetime(...,'unixepoch')` 預設為 UTC
- 若要顯示本地時間，應在查詢層或視覺化層轉換

詳見 [`docs/runbook/data-quality.md`](runbook/data-quality.md)。

## 如何驗證

- `health`、`persist_summary` 日誌恢復連續輸出。
- DB 查詢的 `MAX(ts_ms)` 持續前進。

## 常見問題

- 重啟後仍異常：先確認 OpenD 與磁碟權限，再看 Watchdog 事件手冊。
- SQL 顯示時間差 8 小時：多半是展示層時區轉換方式不一致。
