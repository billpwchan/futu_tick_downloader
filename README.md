# HK Tick Collector

[![CI](https://github.com/billpwchan/futu_tick_downloader/actions/workflows/ci.yml/badge.svg)](https://github.com/billpwchan/futu_tick_downloader/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/billpwchan/futu_tick_downloader)](https://github.com/billpwchan/futu_tick_downloader/releases)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

一個面向生產環境的港股逐筆採集器：從 Futu OpenD 接收行情，經佇列（queue）與批次持久化（persist）安全寫入 SQLite（WAL），並提供 systemd 維運與 Telegram 產品化告警，讓陌生人可以從 0 到可驗證地跑起來。

![架構總覽](docs/assets/overview-architecture.svg)

## 使用場景

- 量化研究：逐筆落庫、回放查核、策略前資料完整性驗證。
- SRE/運維：watchdog 自癒、低噪音告警、值班 runbook。
- 個人玩家：在 AWS Lightsail 低成本長期運行。

## 30 秒快速開始

### 路徑 A：本機 Docker（含可選 mock）

```bash
git clone https://github.com/billpwchan/futu_tick_downloader.git
cd futu_tick_downloader
cp .env.example .env
docker compose --profile mock up -d --build mock-replay
make db-stats
```

### 路徑 B：伺服器 systemd（Ubuntu/Lightsail）

```bash
git clone https://github.com/billpwchan/futu_tick_downloader.git
cd futu_tick_downloader
cp deploy/env/.env.example /etc/hk-tick-collector.env
sudo bash deploy/scripts/install.sh
sudo systemctl status hk-tick-collector --no-pager
```

## Demo：三件事就知道有跑起來

### 1) 看 DB 是否持續成長

```bash
make db-stats
# 或
DATA_ROOT=./data/sqlite scripts/hk-tickctl db symbols --minutes 5
```

### 2) 看關鍵運維日誌

```bash
scripts/hk-tickctl logs --ops --since "20 minutes ago"
```

### 3) 看 Telegram 測試通知

```bash
TG_TOKEN='<your-token>' TG_CHAT_ID='<your-chat-id>' scripts/hk-tickctl tg test
```

![Telegram 訊息示例](docs/assets/telegram-sample.svg)

## 架構圖（資料流、模組邊界、線程/佇列）

```mermaid
flowchart LR
    subgraph Source[資料來源]
      A[Futu OpenD Push]
      B[Poll Fallback]
      M[Mock Replay]
    end

    subgraph Collector[採集器程序]
      C[Mapper/Validator]
      Q[(In-Memory Queue)]
      W[Persist Worker Thread]
      H[Health/Watchdog]
      N[Telegram Notifier]
    end

    subgraph Storage[持久層]
      S[(SQLite WAL<br/>YYYYMMDD.db)]
    end

    A --> C
    B --> C
    M --> S
    C --> Q --> W --> S
    W --> H --> N
    H --> N
```

## 常用命令（最少必要）

```bash
make setup
make lint
make test
make run
make logs
make db-stats
scripts/hk-tickctl export --day 20260213 --out /tmp/hk-20260213.tar.gz
scripts/hk-tickctl tg test
```

其餘操作請看：[`docs/04-運維 Runbook.md`](docs/04-%E9%81%8B%E7%B6%AD%20Runbook.md)

## FAQ（常見坑）

1. 時區怎麼看？
   `ts_ms`/`recv_ts_ms` 都是 UTC epoch 毫秒；交易日切分用 `Asia/Hong_Kong`。
2. 為什麼堅持 WAL？
   WAL 讓讀寫並行更穩定，降低寫入尖峰時讀取阻塞。
3. `busy_timeout` 要設多少？
   建議先用 `5000ms`，高併發下可視磁碟 I/O 調到 `7000-10000ms`。
4. OpenD 常斷線怎麼辦？
   先看 `hk-tickctl status` 與 `logs --ops`，確認 reconnect 與 watchdog 是否正常觸發。
5. 盤前/盤後零流量算異常嗎？
   不一定。通知策略會依 market mode（開盤前/盤中/午休/收盤後）降噪。
6. 非交易日為什麼會看到 `YYYYMMDD.db`？
   新版行為改為「首筆 tick 才建庫」，非交易日不會因服務啟動自動建立空 DB。
7. 收盤後有 `.db-wal` 是不是還在持續寫入？
   不一定。WAL 檔在程序存活期間存在屬正常；請以 `db rows`、`persisted_rows_per_min`、`queue` 判斷是否仍有實際寫入。

## 文件入口

- 文件總入口：[`docs/_index.md`](docs/_index.md)
- 快速開始（本機）：[`docs/01-快速開始（本機）.md`](docs/01-%E5%BF%AB%E9%80%9F%E9%96%8B%E5%A7%8B%EF%BC%88%E6%9C%AC%E6%A9%9F%EF%BC%89.md)
- Lightsail 部署：[`docs/02-部署到 AWS Lightsail（Ubuntu）.md`](docs/02-%E9%83%A8%E7%BD%B2%E5%88%B0%20AWS%20Lightsail%EF%BC%88Ubuntu%EF%BC%89.md)
- Runbook：[`docs/04-運維 Runbook.md`](docs/04-%E9%81%8B%E7%B6%AD%20Runbook.md)

## Roadmap

- `v0.1`: 穩定採集 + WAL + Telegram 產品化通知 + 基礎 runbook。
- `v0.2`: 壓縮存儲、日終歸檔、自動匯出校驗包。
- `v1.0`: topic 細分路由、symbol 規模擴展、可選多儲存後端。

## 社群與治理

- 貢獻指南：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- 行為準則：[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
- 安全政策：[`SECURITY.md`](SECURITY.md)
- 支援方式：[`SUPPORT.md`](SUPPORT.md)
- 授權：Apache-2.0（[`LICENSE`](LICENSE)）
