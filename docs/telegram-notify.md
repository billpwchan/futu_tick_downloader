# Telegram 通知設定（產品化）

本文件描述 `hk-tick-collector` 在 Telegram 的「低噪音 + 可行動」通知模型。

## 1. 通知類型

- `HEALTH OK`：正常摘要（低頻）
- `HEALTH WARN`：品質退化提醒
- `ALERT`：事件告警（如 `PERSIST_STALL`、`DISCONNECT`、`SQLITE_BUSY`）
- `RECOVERED`：事件恢復
- `DAILY DIGEST`：收盤日報

每則訊息固定遵循：

1. 先結論
2. 後指標
3. 再建議（僅 WARN/ALERT）

並附上關聯 ID：

- `sid`：health snapshot id
- `eid`：event id（事件告警/恢復）

## 2. 建立 bot 與取得 chat_id

## 2.1 建立 bot

1. 找 [@BotFather](https://t.me/BotFather)
2. `/newbot`
3. 保存 token

## 2.2 查 `chat_id` / `thread_id`

先在群組送一則訊息，再執行：

```bash
TOKEN="<your_bot_token>"
curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates"
```

- `chat.id`：群組 ID（多為 `-100...`）
- `message_thread_id`：forum topic id

## 2.3 webhook 衝突排查

```bash
curl -s "https://api.telegram.org/bot${TOKEN}/getWebhookInfo"
```

若使用 pull 模式，建議清 webhook：

```bash
curl -s "https://api.telegram.org/bot${TOKEN}/deleteWebhook"
```

## 3. 設定環境變數

```dotenv
TG_ENABLED=1
TG_BOT_TOKEN=<secret>
TG_CHAT_ID=-1001234567890
TG_MESSAGE_THREAD_ID=
TG_PARSE_MODE=HTML
INSTANCE_ID=hk-prod-a1
```

## 4. 降噪節奏（內建）

- OK（定時心跳）：盤前每 30 分鐘、盤中每 10 分鐘、午休每 30 分鐘、盤後每 60 分鐘
- WARN：狀態切換即發；持續最多每 10 分鐘 1 條；恢復即發 OK
- ALERT：狀態切換即發；持續最多每 3 分鐘 1 條；恢復即發 RECOVERED
- 全部事件告警都用 fingerprint 去重

補充：

- 盤後不再把巨大 `drift_sec` 當成核心判斷指標，改為 `距收盤`、`last_update_at`、`close_snapshot_ok`、`db_growth_today`
- 訂閱 1000+ 標的時，Telegram 只發聚合摘要（`symbols/stale_symbols/p95_age`），不展開逐標的清單

## 5. 驗證

```bash
sudo systemctl restart hk-tick-collector
scripts/hk-tickctl logs --ops --since "10 minutes ago"
```

驗收重點：

- 正常時不刷屏
- 告警含建議命令（WARN 最多 1 條，ALERT 最多 2 條）
- 日誌可用 `sid/eid` 快速 grep 對齊

## 6. 常見錯誤

- `403 Forbidden`：bot 不在群組或無發言權
- `400 Bad Request`：`TG_CHAT_ID` 或 `TG_MESSAGE_THREAD_ID` 錯
- `429 Too Many Requests`：Telegram 限流；系統會依 `retry_after` 退避重試
