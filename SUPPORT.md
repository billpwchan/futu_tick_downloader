# Support

## Where to Ask Questions

- GitHub Discussions (recommended)
- GitHub Issues (for reproducible bugs)

## What to Include

- deployment mode (`systemd`, local run, container)
- OS and Python version
- sanitized `.env` values relevant to issue
- logs from:

```bash
sudo journalctl -u hk-tick-collector --since "30 minutes ago" --no-pager
```

- DB diagnostics:

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" "PRAGMA journal_mode; PRAGMA synchronous; PRAGMA wal_autocheckpoint;"
ls -lh "$DB" "$DB-wal" "$DB-shm"
```
