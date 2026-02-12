# 部署：Linux + systemd

## 目的

說明 `hk-tick-collector` 在 Linux + `systemd` 的標準生產部署流程。

> 若你要快速上手，優先閱讀新版精簡部署文：[`docs/deploy.md`](../deploy.md)。

## 前置條件

- Linux 主機（建議 Ubuntu）
- Futu OpenD 已安裝且可運行
- Python 3.10+
- 可寫入資料目錄（預設 `/data/sqlite/HK`）

## 步驟

### 1) 安裝服務

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

### 2) 準備 unit 檔

以 `deploy/systemd/hk-tick-collector.service` 為唯一來源。

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
EnvironmentFile=/opt/futu_tick_downloader/.env
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

關鍵行為說明：

- `Requires=futu-opend.service`：讓 collector 生命週期依附 OpenD。
- `EnvironmentFile=`：可透過 env 更新設定而不改程式碼。
- `Restart=always`：Watchdog 非零退出時可由 systemd 自動拉起。
- `KillSignal=SIGINT` + `TimeoutStopSec`：保障優雅停機與佇列排空。
- 強化旗標（`NoNewPrivileges`、`ProtectSystem` 等）可降低風險面。

### 3) 啟用服務

```bash
sudo cp /opt/futu_tick_downloader/deploy/systemd/hk-tick-collector.service /etc/systemd/system/hk-tick-collector.service
sudo cp /opt/futu_tick_downloader/.env.example /opt/futu_tick_downloader/.env
sudo chown root:hkcollector /opt/futu_tick_downloader/.env
sudo chmod 640 /opt/futu_tick_downloader/.env

sudo systemctl daemon-reload
sudo systemctl enable --now hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

### 4) 安全更新 env

1. 編輯 `/opt/futu_tick_downloader/.env`。
2. 若僅 env 變更，`daemon-reload` 可省略（執行也安全）。
3. 重啟服務：

```bash
sudo systemctl restart hk-tick-collector
```

4. 驗證：

```bash
sudo journalctl -u hk-tick-collector --since "5 minutes ago" --no-pager | tail -n 100
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
bash /opt/futu_tick_downloader/scripts/db_health_check.sh "$DB"
```

### 5) 安全重啟與停機

安全重啟：

```bash
sudo systemctl restart hk-tick-collector
```

維護停機：

```bash
sudo systemctl stop hk-tick-collector
```

服務會在 `TimeoutStopSec` 視窗內進行優雅排空。

## 如何驗證

系統檢查：

```bash
sudo systemctl is-active hk-tick-collector
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager \
  | grep -E "health|persist_summary|persist_loop_heartbeat|WATCHDOG"
```

DB 新鮮度查詢：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" "SELECT ROUND(strftime('%s','now') - MAX(ts_ms)/1000.0,3) AS lag_sec, COUNT(*) AS rows FROM ticks;"
```

## 常見問題

- 服務頻繁重啟：優先檢查 Watchdog 門檻、OpenD 穩定性與磁碟權限。
- `database is locked`：參考 [`docs/runbook/sqlite-wal.md`](../runbook/sqlite-wal.md)。

## 日誌與磁碟管理建議

- 控制 journald 保留量（`journald.conf` 的 `SystemMaxUse=`）。
- 定期監控 DB 與 WAL 成長：

```bash
sudo du -sh /data/sqlite/HK/* | sort -h | tail -n 20
```

- 僅在受控維護時段需要時執行 WAL checkpoint：

```bash
sqlite3 /data/sqlite/HK/$(TZ=Asia/Hong_Kong date +%Y%m%d).db "PRAGMA wal_checkpoint(TRUNCATE);"
```
