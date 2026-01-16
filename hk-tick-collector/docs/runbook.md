# Runbook

## Deploy
1. Copy `.env.example` to `.env` and set `HKTC_SYMBOLS`
2. Ensure host directory `/data/sqlite` is writable (or edit `HOST_SQLITE_DIR`)
3. Start: `docker compose up -d --build`

## 2FA / OpenD Login
- First run may require OpenD login or 2FA
- Follow the OpenD container logs and complete authorization:
  `docker logs -f futu-opend`

## Logs
- Collector: `docker logs -f hk-tick-collector`
- OpenD: `docker logs -f futu-opend`

## Backup
- SQLite is sharded per day at `/data/sqlite/HK/YYYYMMDD.db`
- Prefer off-peak backups or filesystem snapshots

## Common Issues
- Subscribe fails: verify `HKTC_SYMBOLS` and OpenD login state
- No data on disk: check `/data/sqlite` mount and permissions
- Frequent reconnects: check network stability and OpenD health
