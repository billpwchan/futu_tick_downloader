# Deployment: Linux + systemd

This is the primary production deployment target.

## Prerequisites

- Linux host (Ubuntu recommended)
- Futu OpenD installed and running
- Python 3.10+
- writable data directory (default `/data/sqlite/HK`)

## Install Service

```bash
sudo useradd --system --home /opt/futu_tick_downloader --shell /usr/sbin/nologin hkcollector || true
sudo mkdir -p /opt/futu_tick_downloader /data/sqlite/HK
sudo chown -R hkcollector:hkcollector /opt/futu_tick_downloader /data/sqlite/HK

# deploy code
sudo rsync -a --delete ./ /opt/futu_tick_downloader/

sudo -u hkcollector python3 -m venv /opt/futu_tick_downloader/.venv
sudo -u hkcollector /opt/futu_tick_downloader/.venv/bin/pip install -U pip
sudo -u hkcollector /opt/futu_tick_downloader/.venv/bin/pip install -e /opt/futu_tick_downloader
```

## Sample Unit File

Use `deploy/systemd/hk-tick-collector.service` as source of truth.

```ini
[Unit]
Description=HK Tick Collector (Futu OpenD to SQLite)
After=network-online.target futu-opend.service
Wants=network-online.target
Requires=futu-opend.service

[Service]
Type=simple
User=hkcollector
Group=hkcollector
WorkingDirectory=/opt/futu_tick_downloader
EnvironmentFile=/etc/hk-tick-collector.env
ExecStart=/opt/futu_tick_downloader/.venv/bin/python -m hk_tick_collector.main
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=180
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/data/sqlite/HK /opt/futu_tick_downloader

[Install]
WantedBy=multi-user.target
```

### Why these lines matter

- `Requires=futu-opend.service`: collector lifecycle tied to OpenD availability.
- `EnvironmentFile=`: env-driven config update without code changes.
- `Restart=always`: watchdog exits can be recovered by systemd.
- `KillSignal=SIGINT` + `TimeoutStopSec`: allows graceful queue flush.
- hardening flags (`NoNewPrivileges`, `ProtectSystem`, etc.) reduce blast radius.

## Activate

```bash
sudo cp /opt/futu_tick_downloader/deploy/systemd/hk-tick-collector.service /etc/systemd/system/hk-tick-collector.service
sudo cp /opt/futu_tick_downloader/.env.example /etc/hk-tick-collector.env
sudo chown root:root /etc/hk-tick-collector.env
sudo chmod 640 /etc/hk-tick-collector.env

sudo systemctl daemon-reload
sudo systemctl enable --now hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

## Updating Env Safely

1. Edit `/etc/hk-tick-collector.env`.
2. If only env changed, `daemon-reload` is optional but safe.
3. Restart service:

```bash
sudo systemctl restart hk-tick-collector
```

4. Verify:

```bash
sudo journalctl -u hk-tick-collector --since "5 minutes ago" --no-pager | tail -n 100
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
bash /opt/futu_tick_downloader/scripts/db_health_check.sh "$DB"
```

## Safe Restart and Stop

Safe restart:

```bash
sudo systemctl restart hk-tick-collector
```

Safe stop (for maintenance):

```bash
sudo systemctl stop hk-tick-collector
```

Graceful flush occurs during `TimeoutStopSec` window.

## Health Verification

System checks:

```bash
sudo systemctl is-active hk-tick-collector
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager \
  | grep -E "health|persist_ticks|persist_loop_heartbeat|WATCHDOG"
```

DB freshness query:

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" "SELECT ROUND(strftime('%s','now') - MAX(ts_ms)/1000.0,3) AS lag_sec, COUNT(*) AS rows FROM ticks;"
```

## Log and Disk Management Tips

- keep journald retention bounded (`SystemMaxUse=` in `journald.conf`).
- monitor DB and WAL growth:

```bash
sudo du -sh /data/sqlite/HK/* | sort -h | tail -n 20
```

- checkpoint WAL only during controlled operations if needed:

```bash
sqlite3 /data/sqlite/HK/$(TZ=Asia/Hong_Kong date +%Y%m%d).db "PRAGMA wal_checkpoint(TRUNCATE);"
```
