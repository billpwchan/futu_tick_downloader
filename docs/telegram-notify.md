# Telegram 通知設定（產品化 v2.2）

本文件說明 `hk-tick-collector` 的 Telegram 產品化通知：

- 兩種模式：`Product Mode`（預設）/ `Ops Mode`
- Topic 路由：健康訊息與事件訊息分流
- 低噪音策略：phase 邊界 + cadence + fingerprint dedupe
- 互動按鈕：`Details` / `Runbook` / `DB`

## 1. 通知模式

## 1.1 Product Mode（預設）

- 每則訊息最多 6 行
- 固定包含：`結論`、最多 3 個 KPI（新鮮度延遲/寫入吞吐/佇列 backlog）、`市場階段`、`主機/instance`
- 午休/收盤後避免顯示 `stale_symbols=all`，改為 `market idle (normal)`

## 1.2 Ops Mode（按需展開）

- 保留工程細節：`p95/p99`、stale bucket、topN、診斷上下文
- 由 Product 訊息按 `Details` 後，在同一個 topic 回覆 Ops 快照

## 2. Bot / chat_id / topic id

## 2.1 建立 bot

1. 找 [@BotFather](https://t.me/BotFather)
2. `/newbot`
3. 保存 token

## 2.2 取得 chat_id 與 topic id

```bash
TOKEN="<your_bot_token>"
curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates"
```

- `chat.id`：群組 ID（通常 `-100...`）
- `message_thread_id`：forum topic id

## 2.3 webhook 衝突（常見）

```bash
curl -s "https://api.telegram.org/bot${TOKEN}/getWebhookInfo"
```

若使用 long polling，清 webhook：

```bash
curl -s "https://api.telegram.org/bot${TOKEN}/deleteWebhook"
```

## 3. 環境變數

```dotenv
TG_ENABLED=1
TG_TOKEN=<secret>
TG_CHAT_ID=-1001234567890

# optional topic routing
TG_THREAD_HEALTH_ID=1234
TG_THREAD_OPS_ID=5678
# legacy fallback (single thread)
TG_MESSAGE_THREAD_ID=

TG_MODE_DEFAULT=product
TG_PARSE_MODE=HTML

# HEALTH cadence
HEALTH_TRADING_INTERVAL_SEC=900
HEALTH_OFFHOURS_INTERVAL_SEC=1800
TG_HEALTH_LUNCH_ONCE=1
TG_HEALTH_AFTER_CLOSE_ONCE=1
TG_HEALTH_HOLIDAY_MODE=daily

# ALERT policy
ALERT_COOLDOWN_SEC=600
ALERT_ESCALATION_STEPS=0,600,1800
TG_RATE_LIMIT_PER_MIN=18
INSTANCE_ID=hk-prod-a1
```

向後相容：`TG_BOT_TOKEN` 仍可用，建議改用 `TG_TOKEN`。

## 4. Topic 路由規則

- `HEALTH` / `DAILY_DIGEST` -> `TG_THREAD_HEALTH_ID`
- `WARN` / `ALERT` / `RECOVERED` -> `TG_THREAD_OPS_ID`
- 若 topic id 未設，回退到 `TG_MESSAGE_THREAD_ID` 或直接送 `TG_CHAT_ID`

## 5. 按鈕與回應

每則訊息都附 InlineKeyboard：

- `Details`：同 topic 發送 Ops 快照
- `Runbook`：短 SOP + 最多 3 條命令
- `DB`：DB 摘要（rows / last_update / queue / db path）

## 6. 降噪策略

- `HEALTH OK`
  - `pre-open`：每 phase 一次
  - `open`：每 15-30 分鐘（可調）
  - `lunch-break` / `after-hours`：預設每 phase 一次（可調）
  - `holiday-closed`：每日一次或停用
- `WARN`：狀態切換即發；持續最多每 10 分鐘
- `ALERT`：觸發即發；持續最多每 3 分鐘
- `RECOVERED`：立即發送
- 所有事件告警使用 `fingerprint dedupe + cooldown + escalation ladder`

## 7. Before / After 範例

Before（舊訊息，午休易誤判）：

```text
🟢 HK Tick Collector 正常
結論：正常：午休狀態平穩
指標：狀態=午休 | symbols=1000 | stale_symbols=1000 | queue=0/50000 | last_update_at=...
進度：... stale_bucket(>=120s/>=300s/>=900s)=1000/1000/0 ...
```

After（Product Mode）：

```text
🟢 HK Tick 健康摘要
結論：正常：午休狀態平穩
KPI：新鮮度延遲=2.1s | 寫入吞吐=0/min | 佇列=0/50000
市況：午休 (market idle, normal)
主機：ip-10-0-1-12 / hk-prod-a1
sid=sid-12ab34cd
```

After（Ops Mode via `Details`）：

```text
🟢 HK Tick Collector 正常
結論：正常：午休狀態平穩
指標：狀態=午休 | symbols=1000 | stale_symbols=1000 | ...
進度：ingest/min=0 | persist/min=0 | write_eff=0.0% | stale_bucket(...) | top5_stale=...
主機：ip-10-0-1-12 / hk-prod-a1
sid=sid-12ab34cd
```

## 8. 快速驗證

```bash
sudo systemctl restart hk-tick-collector
scripts/hk-tickctl doctor --since "6 hours ago"
scripts/hk-tickctl status
```

預期：

- 有 `telegram_notifier_started notify_schema=v2.2`
- 有 `telegram_enqueue kind=HEALTH ... sid=...`
- WARN/ALERT 有 `eid`，且 thread 路由符合設定

## 9. Troubleshooting

## 9.1 沒有任何更新

1. `TG_ENABLED=1`、`TG_TOKEN`、`TG_CHAT_ID` 是否正確
2. `scripts/hk-tickctl status` 是否有最新 `health`
3. 檢查 `telegram_send_failed` / `telegram_rate_limited`

## 9.2 Bot 看不到群組訊息 / 無法互動

- Bot 必須在群組內，並有發言權限
- topic 模式請確認 `message_thread_id`
- 某些隱私設定會讓 bot 收不到 callback query

## 9.3 webhook conflict

- `getWebhookInfo` 若顯示已有 webhook，先 `deleteWebhook`
- 之後再重啟 collector

## 9.4 常見 API 錯誤

- `400 Bad Request`：`chat_id`/`thread_id` 錯
- `403 Forbidden`：bot 無權限
- `429 Too Many Requests`：限流；系統會按 `retry_after` 重試
