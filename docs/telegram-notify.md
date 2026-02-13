# Telegram 通知設定（產品化 v2.1）

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
- 交易時段若連續多個週期呈現「零流量 + 零佇列 + 全局高齡資料」，會判定為 `holiday-closed`（休市日降噪）
- 訂閱 1000+ 標的時，Telegram 只發聚合摘要（分位數 + stale 分桶 + `top5_stale`）

## 5. 字段詞典（重點）

- `ingest/min`：每分鐘流入量，等於 `push_rows_per_min + poll_accepted`
- `persist/min`：每分鐘落盤量（SQLite 寫入）
- `write_eff`：寫入效率，`persist/min / max(1, ingest/min)`
- `stale_symbols`：超過 stale 門檻的 symbol 數
  - 盤中門檻：`>=10s`
  - 非盤中門檻：`>=120s`
- `stale_bucket(...)`：stale 分桶計數
  - 盤中：`>=10s / >=30s / >=60s`
  - 非盤中：`>=120s / >=300s / >=900s`
- `top5_stale`：最慢 5 個 symbol 的 age（不展開全部）
- `close_snapshot_ok`：是否已排空（`queue=0`）
- `db_growth_today`：相對當日啟動時的 DB row 變化
- `sid/eid`：Telegram 與 journal 關聯鍵

## 6. 1000 標的通知示例

### 6.1 盤前（HEALTH OK）

```text
🟢 HK Tick Collector 正常
結論：正常：開盤前系統就緒
指標：狀態=開盤前 | 距開盤=28m | symbols=1000 | stale_symbols=0 | queue=0/50000 | last_update_at=2026-02-13T01:59:58+00:00
進度：ingest/min=0 | persist/min=0 | write_eff=0.0% | stale_symbols=0 | stale_bucket(>=120s/>=300s/>=900s)=0/0/0 | top5_stale=n/a
主機：ip-10-0-1-12 / hk-prod-a1
資源：load1=0.20 rss=144.1MB disk_free=86.90GB
sid=sid-0abc1234
```

### 6.2 盤中（HEALTH OK）

```text
🟢 HK Tick Collector 正常
結論：正常：盤中採集與寫入穩定
指標：狀態=盤中 | ingest_lag=1.1s | persisted=24100/min | queue=0/50000 | symbols=1000 | stale_symbols=3 | p95_age=1.9s | p99_age=3.2s
進度：ingest/min=24320 | persist/min=24100 | write_eff=99.1% | stale_symbols=3 | stale_bucket(>=10s/>=30s/>=60s)=3/0/0 | top5_stale=HK.01234(12.3s),HK.00981(11.7s),HK.00700(10.2s),HK.09988(8.6s),HK.00175(8.2s)
主機：ip-10-0-1-12 / hk-prod-a1
資源：load1=0.24 rss=146.8MB disk_free=86.82GB
sid=sid-1def5678
```

### 6.3 午休（HEALTH OK）

```text
🟢 HK Tick Collector 正常
結論：正常：午休狀態平穩
指標：狀態=午休 | symbols=1000 | stale_symbols=1000 | queue=0/50000 | last_update_at=2026-02-13T04:00:01+00:00
進度：ingest/min=0 | persist/min=0 | write_eff=0.0% | stale_symbols=1000 | stale_bucket(>=120s/>=300s/>=900s)=1000/1000/0 | top5_stale=HK.00700(182.1s),HK.00981(181.9s),HK.01398(181.8s),HK.09988(181.8s),HK.00005(181.7s)
主機：ip-10-0-1-12 / hk-prod-a1
資源：load1=0.17 rss=141.5MB disk_free=86.76GB
sid=sid-2abc89ef
```

### 6.4 盤後（HEALTH OK）

```text
🟢 HK Tick Collector 正常
結論：正常：收盤後服務平穩
指標：狀態=收盤後 | 距收盤=5h40m | symbols=1000 | close_snapshot_ok=true | db_growth_today=+18,100,234 rows | last_update_at=2026-02-13T08:00:02+00:00
進度：ingest/min=0 | persist/min=0 | write_eff=0.0% | stale_symbols=1000 | stale_bucket(>=120s/>=300s/>=900s)=1000/1000/1000 | top5_stale=HK.00700(20410.5s),HK.00981(20409.8s),HK.01398(20409.6s),HK.09988(20409.6s),HK.00005(20409.4s)
主機：ip-10-0-1-12 / hk-prod-a1
資源：load1=0.15 rss=139.9MB disk_free=86.55GB
sid=sid-3fedc210
```

### 6.5 休市日（HEALTH OK）

```text
🟢 HK Tick Collector 正常
結論：正常：休市日服務平穩
指標：狀態=休市日 | market=holiday-closed | symbols=1000 | close_snapshot_ok=true | db_growth_today=+0 rows | last_update_at=2026-02-14T01:00:00+00:00 | p50_age=1240.0s
進度：ingest/min=0 | persist/min=0 | write_eff=0.0% | stale_symbols=1000 | stale_bucket(>=120s/>=300s/>=900s)=1000/1000/1000 | top5_stale=HK.00700(1880.1s),HK.00981(1879.9s),HK.01398(1879.7s),HK.09988(1879.6s),HK.00005(1879.3s)
主機：ip-10-0-1-12 / hk-prod-a1
資源：load1=0.09 rss=132.2MB disk_free=86.40GB
sid=sid-4fff2233
```

## 7. 快速驗證（新舊版本）

```bash
sudo systemctl restart hk-tick-collector
scripts/hk-tickctl doctor --since "6 hours ago"
```

驗收重點：

- 可看到 `telegram_notifier_started notify_schema=v2.1 ...`
- `HEALTH` enqueue 含 `sid`
- WARN/ALERT enqueue 含 `eid sid`

## 8. 常見錯誤

- `403 Forbidden`：bot 不在群組或無發言權
- `400 Bad Request`：`TG_CHAT_ID` 或 `TG_MESSAGE_THREAD_ID` 錯
- `429 Too Many Requests`：Telegram 限流；系統會依 `retry_after` 退避重試
