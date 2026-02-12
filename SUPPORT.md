# 支援說明

## 問題應該發在哪裡

- GitHub Discussions（建議，適合一般使用與維運問題）
- GitHub Issues（僅限可重現 bug）

## 建議提供資訊

- 部署模式（`systemd`、本機執行、container）
- 作業系統與 Python 版本
- 與問題相關、已去敏的 `.env` 內容
- 近期日誌：

```bash
sudo journalctl -u hk-tick-collector --since "30 minutes ago" --no-pager
```

- DB 診斷：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" "PRAGMA journal_mode; PRAGMA synchronous; PRAGMA wal_autocheckpoint;"
ls -lh "$DB" "$DB-wal" "$DB-shm"
```
