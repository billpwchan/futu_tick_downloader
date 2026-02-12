# 快速開始

## 目的

用最短時間完成 `hk-tick-collector` 安裝與驗證，確認資料可正確匯入並落盤。

## 前置條件

- Linux/macOS，Python 3.10+
- 用於維運檢查的 SQLite CLI（`sqlite3`）
- （選用）可連線的 Futu OpenD（即時匯入驗證）

## 步驟

### 1) 幾分鐘內完成安裝

```bash
git clone <YOUR_FORK_OR_REPO_URL>
cd futu_tick_downloader
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .[dev]
```

### 2) 本機驗證（無 OpenD）

只跑單元／smoke 測試：

```bash
pytest -q
```

會驗證以下項目：

- 環境變數解析與預設值
- SQLite schema + WAL PRAGMA
- 去重行為
- Watchdog 邏輯
- 採集器落盤管線

### 3) 即時驗證（有 OpenD）

1. 設定 `.env`：

```dotenv
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
FUTU_SYMBOLS=HK.00700,HK.00981
DATA_ROOT=/tmp/hk_ticks
```

2. 前景啟動服務：

```bash
hk-tick-collector
```

3. 驗證資料寫入：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/tmp/hk_ticks/${DAY}.db
bash scripts/db_health_check.sh "$DB"
```

## 如何驗證

範例健康查詢輸出：

```text
now_utc              max_tick_utc          lag_sec  rows
-------------------  -------------------   -------  --------
2026-02-11 08:51:00  2026-02-11 08:50:59   1.042    1875521
```

範例日誌：

```text
INFO persist_summary window_sec=5.0 inserted_per_min=24120 ignored_per_min=320 commit_latency_ms_p50=5 commit_latency_ms_p95=12 queue=0/50000 batches=24
INFO persist_loop_heartbeat worker_alive=True queue=0/50000 total_rows_committed=2013450 busy_locked_count=0
INFO health sid=sid-a1b2c3d4 connected=True queue=0/50000 persisted_rows_per_min=22340 ...
```

## 常見問題

- 服務可啟動但無資料：請先確認 `FUTU_SYMBOLS` 非空且 OpenD 可連線。
- `DB` 檔案不存在：請確認 `DATA_ROOT` 路徑有寫入權限。

## 下一步

- 生產部署：[`docs/deploy.md`](deploy.md)
- Telegram 設定：[`docs/telegram-notify.md`](telegram-notify.md)
- 操作手冊：[`docs/runbook.md`](runbook.md)
- 故障排除：[`docs/troubleshooting.md`](troubleshooting.md)
