# Runbook: SQLite WAL

## Why WAL Here

WAL mode allows readers and writer to coexist with better throughput for append-heavy workloads.

## Active Settings

Configured via env and applied per writer connection:

- `SQLITE_JOURNAL_MODE` (default `WAL`)
- `SQLITE_SYNCHRONOUS` (default `NORMAL`)
- `SQLITE_BUSY_TIMEOUT_MS` (default `5000`)
- `SQLITE_WAL_AUTOCHECKPOINT` (default `1000`)

## Check Runtime PRAGMAs

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" "PRAGMA journal_mode; PRAGMA synchronous; PRAGMA wal_autocheckpoint;"
```

## Busy/Locked Troubleshooting

Symptoms:

- `database is locked`
- repeated `sqlite_busy_backoff`

Actions:

1. inspect lock holders (`lsof`, `lslocks`, `fuser`)
2. verify no rogue writer processes
3. check disk latency and free space
4. tune `SQLITE_BUSY_TIMEOUT_MS` if needed

## WAL Growth Management

Large `*.db-wal` may indicate high write volume or checkpoint lag.

Checks:

```bash
ls -lh /data/sqlite/HK/*.db-wal | tail
```

Manual checkpoint (during controlled maintenance):

```bash
sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);"
```

Do not aggressively checkpoint in tight loops; let auto-checkpoint handle steady state unless troubleshooting.
