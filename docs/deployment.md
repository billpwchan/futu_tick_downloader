# 部署指南（systemd）

## 目的

提供 Linux 上 `hk-tick-collector` 的最短生產部署路徑。

## 前置條件

- 目標主機可使用 `systemd`
- 已準備 Python 3.10+
- 已規劃資料目錄（預設 `/data/sqlite/HK`）

## 步驟

### 1) Unit 與 EnvironmentFile

服務入口：

- unit：`hk-tick-collector.service`
- 啟動命令：`python -m hk_tick_collector.main`
- 環境檔（建議）：`/opt/futu_tick_downloader/.env`

範例 service 區塊：

```ini
[Service]
WorkingDirectory=/opt/futu_tick_downloader
EnvironmentFile=/opt/futu_tick_downloader/.env
ExecStart=/opt/futu_tick_downloader/.venv/bin/python -m hk_tick_collector.main
Restart=always
RestartSec=5
```

參考範本：[`deploy/systemd/hk-tick-collector.service`](../deploy/systemd/hk-tick-collector.service)

### 2) 初始化部署

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

### 3) 啟用 Telegram 群組通知

編輯 `/opt/futu_tick_downloader/.env`：

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

Token 安全注意事項：

- 不可將 bot token commit 到 git。
- 請使用私有環境檔或 secret manager。
- 日誌只顯示遮罩值（前後綴），不顯示完整 token。

### 4) 啟用服務

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

### 5) 滾動更新 env

當僅調整環境變數時：

```bash
sudo vim /opt/futu_tick_downloader/.env
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

可選 DB 快速檢查：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
sqlite3 "file:/data/sqlite/HK/${DAY}.db?mode=ro" "SELECT COUNT(*), MAX(ts_ms) FROM ticks;"
```

## 如何驗證

日誌檢查：

```bash
sudo journalctl -u hk-tick-collector -f --no-pager
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager \
  | grep -E "health|persist_ticks|WATCHDOG|telegram|sqlite_busy"
```

驗證通過條件：

- 服務為 `active`。
- `persist_ticks` 與 `health` 日誌持續出現。
- DB row 數量與 `MAX(ts_ms)` 持續前進。

## 常見問題

- 啟動後立即退出：優先檢查 `.env` 格式錯誤與 `FUTU_SYMBOLS` 是否為空。
- Telegram 無訊息：檢查 bot 是否在群組內，並確認 `TELEGRAM_CHAT_ID` 正確。

## 相關文件

- Telegram 設定細節：[`docs/telegram.md`](telegram.md)
- 維運 SOP：[`docs/runbook.md`](runbook.md)
- 完整 systemd 強化配置：[`docs/deployment/systemd.md`](deployment/systemd.md)
