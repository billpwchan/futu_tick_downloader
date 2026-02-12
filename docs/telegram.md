# Telegram 群組通知設定

## 目的

說明如何為本專案啟用 Telegram Bot 通知，包含摘要訊息與關鍵告警。

本專案透過 Telegram Bot API `sendMessage` 發送通知。

支援訊息型態：

- `HEALTH` 摘要（預設每 10 分鐘，含變更抑制）
- `ALERT` 關鍵事件（`PERSIST_STALL`、sqlite busy/locked 異常、服務致命退出）

## 前置條件

- 已建立 Telegram 帳號
- 有可用群組或頻道
- 可修改 `.env` 並重啟服務

## 步驟

### 1) 建立 Bot（@BotFather）

1. 在 Telegram 與 [@BotFather](https://t.me/BotFather) 對話。
2. 執行 `/newbot` 並依提示完成。
3. 保存 token（格式類似 `123456:ABC...`）。

安全注意事項：

- token 只存放在私有 env／secret。
- 不要把 token 貼到公開 issue/PR/chat 日誌。

### 2) 將 Bot 加入群組

1. 將 bot 帳號加入目標群組。
2. 給予可發送訊息權限。
3. 若使用 forum topics，記下目標 topic/thread id（`message_thread_id`）。

隱私模式補充：

- 對本專案（只發送不讀取）通常不受隱私模式限制。
- 若未來要讓 bot 讀群組訊息，需考慮 `/setprivacy`。

### 3) 取得 `chat_id`（2-3 種方式）

#### 方法 A：`getUpdates`（建議）

1. 在 bot 已加入的群組先發一則訊息。
2. 執行：

```bash
TOKEN="<your_bot_token>"
curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates"
```

3. 在回應中找到 `chat.id`（supergroup 通常為 `-100...`）。

#### 方法 B：暫時本機腳本

```python
import json
import urllib.request

TOKEN = "<your_bot_token>"
url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
print(json.loads(urllib.request.urlopen(url, timeout=10).read().decode()))
```

#### 方法 C：Bot 查詢工具

部分 helper bots 可在轉傳群組訊息後顯示 chat id；使用前請先評估信任度。

### 4) 設定 `.env`

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

可選低噪音調校：

```dotenv
TELEGRAM_DIGEST_QUEUE_CHANGE_PCT=20
TELEGRAM_DIGEST_LAST_TICK_AGE_THRESHOLD_SEC=60
TELEGRAM_DIGEST_DRIFT_THRESHOLD_SEC=60
TELEGRAM_DIGEST_SEND_ALIVE_WHEN_IDLE=0
TELEGRAM_SQLITE_BUSY_ALERT_THRESHOLD=3
```

## 如何驗證

1. 啟動／重啟服務：

```bash
sudo systemctl restart hk-tick-collector
```

2. 檢查 notifier 啟動與錯誤日誌：

```bash
sudo journalctl -u hk-tick-collector --since "5 minutes ago" --no-pager \
  | grep -E "telegram|health|WATCHDOG|sqlite_busy"
```

3. 確認群組收到：

- 依設定週期收到一則 `HEALTH` 摘要（預設 600 秒）
- 只有在有意義事件時才收到 `ALERT`

## 常見問題

### `403 Forbidden`

常見原因：

- bot 不在群組
- bot 沒有發送權限
- 群組／頻道策略限制 bot

處理：

- 檢查 bot 成員身分與權限
- 再次確認 `TELEGRAM_CHAT_ID`

### `400 Bad Request`

常見原因：

- `chat_id` 錯誤
- `message_thread_id` 錯誤
- payload 格式錯誤

處理：

- 重新執行 `getUpdates` 並複製正確 id
- 先清空 `TELEGRAM_THREAD_ID` 驗證主群組可發送

### `429 Too Many Requests`

代表 Telegram 觸發限流，回應會帶 `retry_after`。

collector 行為：

- notifier 會等待 `retry_after` 秒後重試（有上限）
- 本地 sender rate limiter 會限制總送信（`TELEGRAM_RATE_LIMIT_PER_MIN`）
- 發送失敗只記錄日誌，不會阻塞匯入／落盤

## 低噪音調校速查

- 降低摘要頻率：調大 `TELEGRAM_DIGEST_INTERVAL_SEC`（例如 `900`）
- 關閉 idle alive：保持 `TELEGRAM_DIGEST_SEND_ALIVE_WHEN_IDLE=0`（預設）
- 降低重複事件噪音：調大 `TELEGRAM_ALERT_COOLDOWN_SEC`
- 保留 Telegram 限流緩衝：保持 `TELEGRAM_RATE_LIMIT_PER_MIN <= 18`
