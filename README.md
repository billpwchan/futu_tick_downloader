# hk-tick-collector

[![CI](https://github.com/billpwchan/futu_tick_downloader/actions/workflows/ci.yml/badge.svg)](https://github.com/billpwchan/futu_tick_downloader/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/billpwchan/futu_tick_downloader)](https://github.com/billpwchan/futu_tick_downloader/releases)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

面向生產環境的港股 Tick 採集服務，專為 Futu OpenD 設計。

本專案使用 Push 為主、Poll 為備援的資料匯入策略，提供安全去重，並將資料落盤至 SQLite WAL，同時具備適合 `systemd` 長時間運行的維運能力。

- 給維運人員：快速部署、清楚操作手冊、一頁式事件指令。
- 給開發人員：乾淨的環境設定、測試、lint、封裝與 CI 流程。

## 目錄

- [為什麼要做這個專案](#why-this-project)
- [功能亮點](#feature-highlights)
- [架構](#architecture)
- [3 分鐘快速開始](#3-minute-quickstart)
- [生產部署（systemd）](#production-deployment-systemd)
- [Telegram 通知](#telegram-notifications)
- [產品化通知示例](#notification-examples)
- [資料模型與保證](#data-model-and-guarantees)
- [維運速查](#operations-cheat-sheet)
- [FAQ（常見問題）](#faq-section)
- [故障排除](#troubleshooting)
- [文件導覽](#documentation-map)
- [路線圖](#roadmap)
- [如何貢獻](#contributing)
- [安全、授權與免責聲明](#security-license-disclaimer)

<a id="why-this-project"></a>
## 為什麼要做這個專案

多數市場資料採集器在生產環境失效，常見原因包含：時間戳語義不清、去重策略脆弱、事件處置工具不足，或重啟流程不穩定。

`hk-tick-collector` 先解決維運正確性：

- 以明確 UTC 語義定義落盤時間戳。
- 透過唯一索引 + `INSERT OR IGNORE` 提供冪等寫入。
- 具備 Watchdog 偵測與停滯恢復機制。
- 內建 Linux `systemd` 部署與操作手冊。

<a id="feature-highlights"></a>
## 功能亮點

- Push 優先的資料匯入，並提供 Poll 備援（`FUTU_POLL_*`）。
- 以交易日切分 SQLite 檔案（`DATA_ROOT/YYYYMMDD.db`）。
- WAL 模式、可調 `busy_timeout`、自動 checkpoint。
- 對 `seq` 與無 `seq` 資料都提供可持續去重。
- 心跳日誌包含佇列、commit、drift、Watchdog 等關鍵訊號。
- 低噪音 Telegram 群組通知（摘要 + 重要告警，含 rate limit 與 cooldown）。
- 提供完整生產文件：部署、維運、事件處置、資料品質檢查。

<a id="architecture"></a>
## 架構

```mermaid
flowchart LR
    A["Futu OpenD"] --> B["Push Ticker Stream"]
    A --> C["Poll Fallback"]
    B --> D["Mapping + Validation"]
    C --> D
    D --> E["Async Queue"]
    E --> F["Persist Worker"]
    F --> G["SQLite WAL\nDATA_ROOT/YYYYMMDD.db"]
    F --> H["Health Logs + Watchdog"]
    H --> I["Telegram Notifier\nDigest + Alerts"]
```

完整設計請見：[`docs/architecture.md`](docs/architecture.md)

<a id="3-minute-quickstart"></a>
## 3 分鐘快速開始

### 選項 A：本機驗證（不需要即時 OpenD）

```bash
git clone <YOUR_FORK_OR_REPO_URL>
cd futu_tick_downloader
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .[dev]
pytest -q
```

### 選項 B：連接 OpenD 即時執行

```bash
cp .env.example .env
# set FUTU_HOST/FUTU_PORT/FUTU_SYMBOLS/DATA_ROOT

. .venv/bin/activate
hk-tick-collector
# existing production entrypoint also works:
python -m hk_tick_collector.main
```

驗證是否有成功寫入：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
bash scripts/db_health_check.sh "$DB"
```

<a id="production-deployment-systemd"></a>
## 生產部署（systemd）

- Unit 範本：[`deploy/systemd/hk-tick-collector.service`](deploy/systemd/hk-tick-collector.service)
- 部署指南（新版）：[`docs/deploy.md`](docs/deploy.md)
- 相容舊版部署文：[`docs/deployment/systemd.md`](docs/deployment/systemd.md)
- 一頁式生產操作手冊：[`docs/runbook/production-onepager.md`](docs/runbook/production-onepager.md)

啟用服務：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

<a id="telegram-notifications"></a>
## Telegram 通知

請在環境設定檔啟用（本機 `.env` 或生產 `systemd` `EnvironmentFile=`）：

```dotenv
TG_ENABLED=1
TG_BOT_TOKEN=<secret>
TG_CHAT_ID=-1001234567890
TG_MESSAGE_THREAD_ID=
TG_PARSE_MODE=HTML
HEALTH_INTERVAL_SEC=600
HEALTH_TRADING_INTERVAL_SEC=600
HEALTH_OFFHOURS_INTERVAL_SEC=1800
ALERT_COOLDOWN_SEC=600
ALERT_ESCALATION_STEPS=0,600,1800
TG_RATE_LIMIT_PER_MIN=18
TG_INCLUDE_SYSTEM_METRICS=1
INSTANCE_ID=hk-prod-a1
```

目前通知策略：

- `HEALTH OK`：盤前每 30 分鐘、盤中每 10 分鐘、午休每 30 分鐘、盤後每 60 分鐘
- `HEALTH WARN`：切換即發；持續最多每 10 分鐘 1 條；恢復即發 OK
- `ALERT`：切換即發；持續最多每 3 分鐘 1 條；恢復即發 `RECOVERED`
- `DAILY DIGEST`：收盤後 1 條日報
- 每條訊息都會帶 `sid`，事件告警另帶 `eid`

設定與排障細節請見：[`docs/telegram-notify.md`](docs/telegram-notify.md)

<a id="notification-examples"></a>
## 產品化通知示例

```text
🟢 HK Tick Collector 正常
結論：正常：盤中採集與寫入穩定
指標：狀態=盤中 | ingest_lag=1.2s | persisted=24100/min | queue=0/50000 | symbols=1000 | stale_symbols=2 | p95_age=1.8s
主機：ip-10-0-1-12 / hk-prod-a1
資源：load1=0.22 rss=145.3MB disk_free=87.30GB
sid=sid-12ab34cd
```

```text
🟡 注意
結論：注意：盤中品質指標退化
指標：狀態=盤中 | ingest_lag=48.2s | persisted=9200/min | queue=3200/50000 | symbols=1000 | stale_symbols=127 | p95_age=26.1s
建議：journalctl -u hk-tick-collector -n 120 --no-pager
主機：ip-10-0-1-12 / hk-prod-a1
sid=sid-34de56fa
```

```text
🔴 異常
結論：異常：持久化停滯，資料可能未落庫
指標：事件=PERSIST_STALL | 持續=242s/180s | 影響=新資料可能無法寫入 SQLite，時序會持續落後 | write=0/min | queue=8542/50000 | lag=412
建議1：journalctl -u hk-tick-collector -n 200 --no-pager
建議2：sqlite3 /data/sqlite/HK/20260212.db 'select count(*), max(ts_ms) from ticks;'
主機：ip-10-0-1-12 / hk-prod-a1
eid=eid-a1b2c3d4 sid=sid-34de56fa
```

```text
✅ 已恢復
結論：DISCONNECT 已恢復正常
指標：status=reconnected | queue=0/50000
主機：ip-10-0-1-12 / hk-prod-a1
eid=eid-a1b2c3d4 sid=sid-34de56fa
```

```text
📊 日報
結論：20260212 收盤摘要
指標：今日總量=18100234 | 峰值=39800/min | 最大延遲=4.2s | 告警次數=3 | 恢復次數=3
db：/data/sqlite/HK/20260212.db rows=321001245
主機：ip-10-0-1-12 / hk-prod-a1
sid=sid-9f8e7d6c
```

<a id="data-model-and-guarantees"></a>
## 資料模型與保證

核心資料表（`ticks`）在採集器視角為 append-only。

```sql
CREATE TABLE ticks (
  market TEXT NOT NULL,
  symbol TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,
  price REAL,
  volume INTEGER,
  turnover REAL,
  direction TEXT,
  seq INTEGER,
  tick_type TEXT,
  push_type TEXT,
  provider TEXT,
  trading_day TEXT NOT NULL,
  recv_ts_ms INTEGER NOT NULL,
  inserted_at_ms INTEGER NOT NULL
);
```

### 去重保證

- `uniq_ticks_symbol_seq`：當 `seq IS NOT NULL`。
- `uniq_ticks_symbol_ts_price_vol_turnover`：當 `seq IS NULL`。
- `INSERT OR IGNORE` 讓重試與 push/poll 重疊場景保持冪等。

### 時間戳保證

- `ts_ms`：事件時間（UTC epoch 毫秒）。
- `recv_ts_ms`：採集器接收時間（UTC epoch 毫秒）。
- 港股本地時間來源先以 `Asia/Hong_Kong` 解讀，再轉為 UTC epoch。

<a id="operations-cheat-sheet"></a>
## 維運速查

服務管理：

```bash
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

查看日誌：

```bash
sudo journalctl -u hk-tick-collector -f --no-pager
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager \
  | grep -E "health|persist_summary|persist_loop_heartbeat|WATCHDOG|sqlite_busy|ERROR"
```

新鮮度檢查：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
sqlite3 "file:${DB}?mode=ro" \
  "SELECT ROUND(strftime('%s','now')-MAX(ts_ms)/1000.0,3) AS lag_sec, COUNT(*) AS rows FROM ticks;"
```

更多 SQL 範例：[`scripts/query_examples.sql`](scripts/query_examples.sql)

<a id="faq-section"></a>
## FAQ（常見問題）

Q1. 為什麼 journal 看不到 `poll_stats`？  
A1. `poll_stats` 已降為 `DEBUG`；預設 `INFO` 只看 `health` 與 `persist_summary` 聚合訊號。

Q2. 為什麼正常時 Telegram 幾乎不發訊息？  
A2. 這是設計目標。正常態只在啟動、開盤前、收盤後與狀態切換發送，避免群組噪音。

Q3. 收到告警後第一步該做什麼？  
A3. 先執行 `scripts/hk-tickctl logs --ops --since "20 minutes ago"`，再用訊息內的 `eid/sid` 反查 journal。

Q4. Telegram 沒收到訊息怎麼查？  
A4. 依序檢查 `TG_BOT_TOKEN`、`TG_CHAT_ID`、群組權限、`getUpdates`/`getWebhookInfo`。

<a id="troubleshooting"></a>
## 故障排除

- WATCHDOG 停滯：[`docs/runbook/incident-watchdog-stall.md`](docs/runbook/incident-watchdog-stall.md)
- SQLite WAL / locked：[`docs/runbook/sqlite-wal.md`](docs/runbook/sqlite-wal.md)
- 時間戳與 drift 檢查：[`docs/runbook/data-quality.md`](docs/runbook/data-quality.md)
- 一般故障排除：[`docs/troubleshooting.md`](docs/troubleshooting.md)

<a id="documentation-map"></a>
## 文件導覽

- 快速開始：[`docs/getting-started.md`](docs/getting-started.md)
- 部署（新版）：[`docs/deploy.md`](docs/deploy.md)
- Runbook（新版）：[`docs/runbook.md`](docs/runbook.md)
- 可觀測性：[`docs/observability.md`](docs/observability.md)
- 設定參考（完整環境變數）：[`docs/configuration.md`](docs/configuration.md)
- 架構：[`docs/architecture.md`](docs/architecture.md)
- 部署（systemd）：[`docs/deployment/systemd.md`](docs/deployment/systemd.md)
- Telegram 設定：[`docs/telegram-notify.md`](docs/telegram-notify.md)
- 延伸維運操作手冊：[`docs/runbook/operations.md`](docs/runbook/operations.md)
- 一頁式 Runbook：[`docs/runbook/production-onepager.md`](docs/runbook/production-onepager.md)
- 發版流程：[`docs/releasing.md`](docs/releasing.md)
- FAQ：[`docs/faq.md`](docs/faq.md)

<a id="roadmap"></a>
## 路線圖

- 可選的 metrics endpoint，供外部可觀測平台使用。
- 可選的 Parquet 匯出流程，供分析管線使用。
- 補強更大 symbol 規模下的整合測試。

<a id="contributing"></a>
## 如何貢獻

- 指南：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- 行為準則：[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
- PR 範本：[`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md)

<a id="security-license-disclaimer"></a>
## 安全、授權與免責聲明

- 安全政策：[`SECURITY.md`](SECURITY.md)
- 支援管道：[`SUPPORT.md`](SUPPORT.md)
- 授權：Apache-2.0（[`LICENSE`](LICENSE)）

Futu OpenD 與市場資料使用必須符合 Futu 條款與在地法規。本 repo 提供採集與落盤能力，不授予任何專有資料再散布權利。
