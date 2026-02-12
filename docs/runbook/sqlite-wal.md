# 操作手冊：SQLite WAL

## 目的

說明本專案為何使用 WAL、如何驗證 PRAGMA，以及遇到 busy/locked 時的處置方式。

## 前置條件

- 可讀取當日 DB
- 可使用 `sqlite3` 與系統鎖檢查工具

## 為何使用 WAL

WAL 模式可讓讀取與寫入共存，較適合 append-heavy 工作負載。

## 生效設定

透過 env 設定，並在每個 writer connection 套用：

- `SQLITE_JOURNAL_MODE`（預設 `WAL`）
- `SQLITE_SYNCHRONOUS`（預設 `NORMAL`）
- `SQLITE_BUSY_TIMEOUT_MS`（預設 `5000`）
- `SQLITE_WAL_AUTOCHECKPOINT`（預設 `1000`）

## 步驟

### 1) 檢查執行期 PRAGMA

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" "PRAGMA journal_mode; PRAGMA synchronous; PRAGMA wal_autocheckpoint;"
```

### 2) Busy/Locked 排查

症狀：

- `database is locked`
- 重複 `sqlite_busy_backoff`

處理：

1. 檢查鎖持有者（`lsof`、`lslocks`、`fuser`）
2. 確認沒有 rogue writer process
3. 檢查磁碟延遲與可用空間
4. 必要時調整 `SQLITE_BUSY_TIMEOUT_MS`

### 3) WAL 膨脹管理

`*.db-wal` 偏大可能代表寫入量高或 checkpoint 落後。

檢查：

```bash
ls -lh /data/sqlite/HK/*.db-wal | tail
```

受控維護時段可手動 checkpoint：

```bash
sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);"
```

不要在緊密迴圈中反覆強制 checkpoint；一般情況交由 auto-checkpoint 即可。

## 如何驗證

- PRAGMA 值符合預期。
- `sqlite_busy_backoff` 頻率下降。
- WAL 大小回到穩定範圍。

## 常見問題

- `busy_timeout` 查到 0：該值為連線層級，請結合服務設定與日誌判讀。
