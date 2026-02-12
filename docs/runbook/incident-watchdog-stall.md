# 事件操作手冊：WATCHDOG Persistent Stall

## 目的

處理 `WATCHDOG persistent_stall` 事件，快速判斷根因並恢復落盤。

## 前置條件

- 可讀取 `journalctl`
- 可查詢當日 SQLite DB
- 可執行 `systemctl` 重啟服務

## 症狀模式

常見日誌模式：

- `WATCHDOG persistent_stall ...`
- 重複 `sqlite_busy_backoff ...`
- 佇列持續增長但 `persist_summary` 不再前進
- `worker_alive=False` 或 commit age 持續上升

## 判斷樹

```text
1) Is upstream active? (push/poll moving)
   - No -> upstream/OpenD issue path
   - Yes -> go to 2
2) Is queue growing beyond threshold?
   - No -> likely duplicate-only window, monitor
   - Yes -> go to 3
3) Is persist thread committing?
   - Yes -> temporary pressure, tune thresholds
   - No -> go to 4
4) Is SQLite locked/busy or readonly?
   - Yes -> SQLite contention/permission path
   - No -> worker/writer recovery path
```

## 步驟

### 1) 先收斂日誌

```bash
sudo journalctl -u hk-tick-collector --since "30 minutes ago" --no-pager \
  | grep -E "WATCHDOG|persist_loop_heartbeat|persist_summary|sqlite_busy|health"
```

### 2) 查 DB 與 PRAGMA

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" "PRAGMA journal_mode; PRAGMA synchronous; PRAGMA wal_autocheckpoint;"
sqlite3 "file:${DB}?mode=ro" "SELECT COUNT(*), MAX(ts_ms) FROM ticks;"
```

### 3) 查鎖

```bash
lsof "$DB" "$DB-wal" "$DB-shm" || true
fuser "$DB" "$DB-wal" "$DB-shm" || true
lslocks | grep -E "$(basename "$DB")|sqlite" || true
```

### 4) 緩解措施

1. 若是 readonly/權限問題：

```bash
sudo chown -R hkcollector:hkcollector /data/sqlite/HK
sudo chmod -R 750 /data/sqlite/HK
```

2. 若是暫時性鎖壓力：重啟 collector（OpenD 保持運行）：

```bash
sudo systemctl restart hk-tick-collector
```

3. 若反覆 Watchdog 退出：

- 適度調高 `WATCHDOG_STALL_SEC`
- 確認 `WATCHDOG_QUEUE_THRESHOLD_ROWS` 不過低
- 檢查儲存延遲與 WAL 成長

4. 若 OpenD 不穩定：

```bash
sudo systemctl status futu-opend --no-pager
sudo systemctl restart futu-opend
```

5. 重新驗證：

```bash
bash scripts/db_health_check.sh
```

## 如何驗證

- `persist_summary` 恢復輸出。
- `MAX(ts_ms)` 與 row 數持續前進。
- 無連續 `WATCHDOG persistent_stall`。

## 常見問題

- 重啟後短時間再次觸發：多半為底層鎖競爭或磁碟延遲未解決。

## Postmortem 範本

```markdown
# Incident: WATCHDOG persistent_stall

- Date/Time (UTC):
- Impact window:
- Affected symbols/services:
- Detection signal:
- Upstream status (OpenD/network):
- Queue behavior:
- SQLite symptoms (busy/locked/readonly):
- Immediate mitigation:
- Root cause:
- Corrective action:
- Preventive action:
- Follow-up owner and ETA:
```
