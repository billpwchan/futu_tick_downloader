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
TG_ACTION_COMMAND_TIMEOUT_SEC=10.0
TG_ACTION_COMMAND_ALLOWLIST=help,db_stats,top_symbols,symbol
TG_ACTION_COMMAND_MAX_LOOKBACK_DAYS=30
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
- `/db_stats [YYYYMMDD]` 或 `/db_stats --day YYYYMMDD`：DB 摘要
- `/top_symbols [limit] [minutes] [rows|turnover|volume] [YYYYMMDD]`（也支援 `--limit/--minutes/--metric/--day`）
- `/symbol HK.00700 [last] [YYYYMMDD]`（也支援 `--last/--day`）
- 日期允許 `YYYYMMDD` 或 `YYYY-MM-DD`，並受 `TG_ACTION_COMMAND_MAX_LOOKBACK_DAYS` 限制
- 文字指令會套用 `TG_ACTION_COMMAND_TIMEOUT_SEC` 與 `TG_ACTION_COMMAND_RATE_LIMIT_PER_MIN`

### 指令矩陣（一期）

- `/help`
  - 參數：無
  - 用途：顯示當前可用命令（受 allowlist 影響）
- `/db_stats`
  - 參數：`[YYYYMMDD]` 或 `--day YYYYMMDD`
  - 用途：看指定交易日 DB 行數、大小、索引等摘要
- `/top_symbols`
  - 參數：`[limit] [minutes] [metric] [day]`
  - 等價旗標：`--limit --minutes --metric --day`
  - `metric`：`rows|turnover|volume`
  - 用途：看近期窗口 Top symbol 排行
- `/symbol`
  - 參數：`<symbol> [last] [day]`
  - 等價旗標：`--last --day`
  - 用途：看指定 symbol 最近 N 筆 ticks

### 推薦用法（可直接貼到群內）

```text
/help
/db_stats --day 20260220
/top_symbols --limit 10 --minutes 15 --metric rows --day 20260220
/symbol HK.00700 --last 20 --day 20260220
```

## 5) 驗收清單（功能是否生效）

### A. 互動主鏈路

```bash
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager -l | grep -E "telegram_notifier_started|COMMAND|callback|telegram_send_ok|telegram_send_failed"
```

應看到：

- `telegram_notifier_started ... interactive_enabled=True`
- 發命令後有 `telegram_enqueue kind=COMMAND`
- 隨後有 `telegram_send_ok kind=COMMAND`

### B. 權限控制

- 若設了 `TG_ADMIN_USER_IDS`，非管理員執行命令應被拒絕
- 若設了 `TG_ACTION_COMMAND_ALLOWLIST`，不在白名單的命令應回 `指令未啟用`

### C. 日期限制

- `TG_ACTION_COMMAND_MAX_LOOKBACK_DAYS=30` 時，超過 30 天應回 `日期超出範圍`

## 6) 常見問題

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

- `TG_ACTION_TIMEOUT_SEC` 預設 3 秒，主要給按鈕/快捷查詢
- `TG_ACTION_COMMAND_TIMEOUT_SEC` 預設 10 秒，主要給文字指令（`/db_stats` 等）
- `TG_ACTION_LOG_MAX_LINES` 建議 20~40，避免刷屏

### Q4. 群裡發指令沒任何回應

優先排查：

1. 是否在正確 chat（`TG_CHAT_ID`）
2. bot 是否有讀取群訊息權限
3. 是否命中 `TG_ADMIN_USER_IDS` 限制
4. 日誌有沒有 `telegram_send_failed`

### Q5. `telegram_send_failed ... Unsupported start tag`

通常是 HTML parse_mode 下有未跳脫字元。  
目前命令回覆已做 escape；若你擴展自訂模板，請務必 escape `<` `>` `&`。

## 7) 安全設計

- callback_data 使用短路由（<=64 bytes）
- 僅允許白名單命令（journalctl / `scripts/hk-tickctl db stats|top-symbols|symbol`）
- 命令有 timeout，且失敗時會優雅降級
- 文字指令可用 `TG_ACTION_COMMAND_ALLOWLIST` 精確啟用
- 互動處理在 notifier callback/task 與 worker，不阻塞採集主鏈路
