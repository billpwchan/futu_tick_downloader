# 盤後歸檔（backup → zstd → checksum → retention）

`archive` 的設計目標是「不中斷服務也能拿到一致性快照」。

## 核心流程

1. 對 daily DB 執行 SQLite backup API（先做一致性備份檔）
2. 以 `zstd` 壓縮備份檔成 `.db.zst`
3. 產生 `.sha256` 校驗檔
4. 產生 `manifest/YYYYMMDD.json`
5. （可選）驗證壓縮與解壓後 SQLite 可讀
6. （可選）依 retention 清理過舊原始 `.db/.db-wal/.db-shm`

## CLI 用法

```bash
scripts/hk-tickctl archive --data-root /data/sqlite/HK \
  --day 20260216 \
  --archive-dir /data/sqlite/HK/_archive \
  --keep-days 14 \
  --delete-original 1 \
  --verify 1
```

## 產物

- `{ARCHIVE_DIR}/YYYYMMDD.db.zst`
- `{ARCHIVE_DIR}/YYYYMMDD.db.zst.sha256`
- `{ARCHIVE_DIR}/manifest/YYYYMMDD.json`
- `{DATA_ROOT}/_reports/quality/YYYYMMDD.json`（歸檔時一併生成/更新）

## 依賴

- `zstd`（壓縮與 `zstd -t` 驗證）

若缺少：

```bash
sudo apt-get update
sudo apt-get install -y zstd
```

## retention 安全規則

- 只清理 `DATA_ROOT` 底下檔案
- 只清理已「成功歸檔且 verify 通過」的日期
- 只刪 `YYYYMMDD.db/.db-wal/.db-shm`

## 常見錯誤

- `db not found`：日期錯誤或 `DATA_ROOT` 設定不符
- `zstd not found`：未安裝 zstd
- `archive verify failed`：壓縮檔壞掉或解壓後 SQLite 不可讀，請重新歸檔
