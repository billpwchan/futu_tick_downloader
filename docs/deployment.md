# Deployment Guide (systemd)

This guide is the shortest production path for `hk-tick-collector` on Linux.

## 1) Unit and EnvironmentFile

Service entrypoint:

- unit: `hk-tick-collector.service`
- start command: `python -m hk_tick_collector.main`
- env file (recommended): `/opt/futu_tick_downloader/.env`

Example service block:

```ini
[Service]
WorkingDirectory=/opt/futu_tick_downloader
EnvironmentFile=/opt/futu_tick_downloader/.env
ExecStart=/opt/futu_tick_downloader/.venv/bin/python -m hk_tick_collector.main
Restart=always
RestartSec=5
```

Reference template: [`deploy/systemd/hk-tick-collector.service`](../deploy/systemd/hk-tick-collector.service)

## 2) Initial Setup

```bash
sudo mkdir -p /opt/futu_tick_downloader /data/sqlite/HK
sudo rsync -a --delete ./ /opt/futu_tick_downloader/
sudo python3 -m venv /opt/futu_tick_downloader/.venv
sudo /opt/futu_tick_downloader/.venv/bin/pip install -U pip
sudo /opt/futu_tick_downloader/.venv/bin/pip install -e /opt/futu_tick_downloader
sudo cp /opt/futu_tick_downloader/deploy/systemd/hk-tick-collector.service /etc/systemd/system/
sudo cp /opt/futu_tick_downloader/.env.example /opt/futu_tick_downloader/.env
sudo chmod 640 /opt/futu_tick_downloader/.env
```

## 3) Enable Telegram Group Notifications

Edit `/opt/futu_tick_downloader/.env`:

```dotenv
TELEGRAM_ENABLED=1
TELEGRAM_BOT_TOKEN=<secret>
TELEGRAM_CHAT_ID=-1001234567890
TELEGRAM_THREAD_ID=
TELEGRAM_DIGEST_INTERVAL_SEC=600
TELEGRAM_ALERT_COOLDOWN_SEC=600
TELEGRAM_RATE_LIMIT_PER_MIN=18
TELEGRAM_INCLUDE_SYSTEM_METRICS=1
INSTANCE_ID=hk-prod-a1
```

Token safety:

- never commit bot tokens to git.
- use private env files / secret managers.
- logs mask token values (only prefix/suffix are shown).

## 4) Activate and Verify

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

Logs:

```bash
sudo journalctl -u hk-tick-collector -f --no-pager
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager \
  | grep -E "health|persist_ticks|WATCHDOG|telegram|sqlite_busy"
```

## 5) Rolling Env Update

When only env values change:

```bash
sudo vim /opt/futu_tick_downloader/.env
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

Optional quick DB check:

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
sqlite3 "file:/data/sqlite/HK/${DAY}.db?mode=ro" "SELECT COUNT(*), MAX(ts_ms) FROM ticks;"
```

## 6) Related Docs

- Telegram setup details: [`docs/telegram.md`](telegram.md)
- Operational SOP: [`docs/runbook.md`](runbook.md)
- Full systemd hardening details: [`docs/deployment/systemd.md`](deployment/systemd.md)
