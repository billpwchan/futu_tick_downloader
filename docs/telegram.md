# Telegram 互動通知（產品化）

本文件說明如何啟用 Telegram 互動按鈕（Inline Keyboard）、權限控制、以及常見排查。

## 1) 啟用互動按鈕

在 `.env`（或 systemd env 檔）加入：

```dotenv
TG_ENABLED=1
TG_TOKEN=<bot-token>
TG_CHAT_ID=<chat-id>
TG_INTERACTIVE_ENABLED=1
TG_ADMIN_USER_IDS=1001,1002
TG_ACTION_CONTEXT_TTL_SEC=43200
TG_ACTION_LOG_MAX_LINES=20
TG_ACTION_REFRESH_MIN_INTERVAL_SEC=15
TG_ACTION_COMMAND_RATE_LIMIT_PER_MIN=8
TG_ACTION_TIMEOUT_SEC=3.0
```

重啟服務：

```bash
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

## 2) 群組/Topic 建議

- 一般健康訊息：`TG_THREAD_HEALTH_ID`
- 告警與處置：`TG_THREAD_OPS_ID`
- 若未拆 thread，全部走 `TG_MESSAGE_THREAD_ID`

## 3) 目前支援按鈕

- `🔎 詳情`：同一則訊息展開/收合（`editMessageText`）
- `🧾 近20分鐘日誌`：只回重點（ERROR/WARN/WATCHDOG/persist/sqlite_busy）
- `🗃 DB 狀態`：rows/max_ts/drift/db path 等
- `🧯 建議/處置`：短版 SOP
- `🔕 靜音 1h`：暫停 HEALTH/WARN 心跳（ALERT 不靜音）
- `🔄 刷新`：重算最新 health（有最小間隔保護）

## 4) 文字指令（管理員）

- `/help`：顯示可用指令
- `/db_stats [YYYYMMDD]`：DB 摘要
- `/top_symbols [limit] [minutes] [rows|turnover|volume]`：近期 Top symbols
- `/symbol HK.00700 [last]`：指定 symbol 最新 ticks
- 所有文字指令都會套用 `TG_ACTION_TIMEOUT_SEC` 與 `TG_ACTION_COMMAND_RATE_LIMIT_PER_MIN`

## 5) 常見問題

### Q1. 按鈕沒反應

先看服務日誌：

```bash
sudo journalctl -u hk-tick-collector --since "15 minutes ago" --no-pager \
  | grep -E "telegram_callback|telegram_rate_limited|telegram_send_failed|webhook"
```

確認：

1. `TG_INTERACTIVE_ENABLED=1`
2. 你的操作帳號在 `TG_ADMIN_USER_IDS` 內（若有設定）
3. 群組 `chat_id` 與 `TG_CHAT_ID` 一致

### Q2. getUpdates 收不到 callback

檢查是否被 webhook 佔用：

```bash
curl -s "https://api.telegram.org/bot${TG_TOKEN}/getWebhookInfo"
```

若 `url` 非空，清掉 webhook：

```bash
curl -s "https://api.telegram.org/bot${TG_TOKEN}/deleteWebhook"
```

再驗證：

```bash
curl -s "https://api.telegram.org/bot${TG_TOKEN}/getUpdates"
```

### Q3. 日誌/DB 查詢很慢

- `TG_ACTION_TIMEOUT_SEC` 預設 3 秒，超時會回「逾時，請稍後再試」
- `TG_ACTION_LOG_MAX_LINES` 建議 20~40，避免刷屏

## 6) 安全設計

- callback_data 使用短路由（<=64 bytes）
- 僅允許白名單命令（journalctl / `scripts/hk-tickctl db stats|top-symbols|symbol`）
- 命令有 timeout，且失敗時會優雅降級
- 互動處理在 notifier callback/task 與 worker，不阻塞採集主鏈路
