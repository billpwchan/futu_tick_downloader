# Telegram 通知設定（Human-friendly）

本文件描述如何把 `hk-tick-collector` 通知發到 Telegram 群組，並維持「可讀、低噪音、可維運」。

## 通知模型

- A 類：`HEALTH` digest（定時 + 狀態變化）
- C 類：事件告警（`PERSIST_STALL` / `DISCONNECT` / `RESTART` / `SQLITE_BUSY`）

每則訊息採兩層結構：

1. 第一層（必讀）：6-10 行，快速回答「要不要處理」
2. 第二層（可展開）：`<blockquote expandable>` 技術細節與建議命令

## 1) 建立 Bot

1. 與 [@BotFather](https://t.me/BotFather) 對話。
2. 執行 `/newbot`。
3. 保存 token（例如 `123456:ABC...`）。

安全注意：

- token 視為密鑰，不可 commit 到 git。
- 建議放在私有 `.env` 或 secret manager。

## 2) 將 Bot 加入群組

1. 把 bot 拉進目標群組。
2. 允許 bot 發送訊息。
3. 若使用 forum topic，記錄 topic id（`message_thread_id`）。

## 3) 取得群組 `chat_id`

## 方法 A：`getUpdates`（推薦）

先在群組中送一條訊息，再執行：

```bash
TOKEN="<your_bot_token>"
curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates"
```

回應裡 `chat.id` 即目標 id（supergroup 常為 `-100...`）。

## 方法 B：暫時腳本

```python
import json
import urllib.request

TOKEN = "<your_bot_token>"
url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
print(json.loads(urllib.request.urlopen(url, timeout=10).read().decode()))
```

## 4) 設定環境變數

在 `/opt/futu_tick_downloader/.env`：

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

TG_DIGEST_QUEUE_CHANGE_PCT=20
TG_DIGEST_LAST_TICK_AGE_THRESHOLD_SEC=60
TG_DIGEST_DRIFT_THRESHOLD_SEC=60
TG_SQLITE_BUSY_ALERT_THRESHOLD=3

INSTANCE_ID=hk-prod-a1
```

相容性說明：

- 舊版 `TELEGRAM_*` 變數仍支援。
- 新部署建議統一使用 `TG_*` + `HEALTH_*` + `ALERT_*`。

## 5) 套用與驗證

```bash
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

查看 notifier 日誌：

```bash
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager \
  | grep -E "telegram_notifier_started|telegram_enqueue|telegram_send_ok|telegram_send_failed|telegram_alert_suppressed"
```

驗收點：

- 群組可收到 HEALTH 與 ALERT。
- 第一層可 5 秒內判讀是否需處理。
- 相同 fingerprint 不會每分鐘刷屏。

## 6) 常見錯誤

## `403 Forbidden`

常見原因：

- bot 不在群組
- bot 無發言權限

## `400 Bad Request`

常見原因：

- `TG_CHAT_ID` 錯誤
- `TG_MESSAGE_THREAD_ID` 錯誤

建議先清空 `TG_MESSAGE_THREAD_ID` 驗證主群可發，再加 topic。

## `429 Too Many Requests`

系統行為：

- 讀取 `retry_after` 並退避重試
- 本地 sender 有共用速率限制（`TG_RATE_LIMIT_PER_MIN`）
- 失敗只記錄日志，不影響主採集與寫庫

## 7) 降噪調參建議

- 摘要過多：調大 `HEALTH_INTERVAL_*_SEC`
- 告警重複：調大 `ALERT_COOLDOWN_SEC`
- 事件長時間持續但想補提醒：調整 `ALERT_ESCALATION_STEPS`
- 避免觸發 Telegram 限流：維持 `TG_RATE_LIMIT_PER_MIN <= 18`

## 8) 示例（截斷）

```text
✅ HK Tick Collector · HEALTH · OK
結論：正常，資料採集與寫入穩定
影響：目前不需人工介入
關鍵：freshness=1.2s persisted/min=24100 queue=0/50000
主機：collector-a (hk-prod-a1) day=20260212 mode=open
symbols:
- HK.00700: age=0.8s, lag=0
- HK.00981: age=1.0s, lag=0
<blockquote expandable>tech: db_path=... suggest: journalctl ...</blockquote>
```
