# 05-Telegram 通知（產品化）

> 互動按鈕與新版產品化 IA 已移至：[`/docs/telegram.md`](telegram.md)

## 配置步驟

1. 建立 Bot，拿到 `TG_TOKEN`
2. 把 Bot 加入群組或頻道，取得 `TG_CHAT_ID`
3. 在 `.env` 設定：

```dotenv
TG_ENABLED=1
TG_TOKEN=<bot-token>
TG_CHAT_ID=<chat-id>
TG_MODE_DEFAULT=product
TG_PARSE_MODE=HTML
ALERT_COOLDOWN_SEC=600
TG_RATE_LIMIT_PER_MIN=18
```

4. 驗證：

```bash
scripts/hk-tickctl tg test
```

## 訊息語義

- 先結論：正常 / 注意 / 異常 / 已恢復
- 再指標：延遲、寫入吞吐、佇列
- 最後建議：下一步排查命令

## 降噪策略

- 同 fingerprint 套用 cooldown（避免刷屏）
- 盤前/盤後自動降頻
- 狀態切換（OK->WARN、WARN->ALERT、ALERT->RECOVERED）即時發送

## getUpdates / webhook 排查

### 先看是否有 webhook 佔用

```bash
curl -s "https://api.telegram.org/bot${TG_TOKEN}/getWebhookInfo"
```

若 `url` 非空，先清掉：

```bash
curl -s "https://api.telegram.org/bot${TG_TOKEN}/deleteWebhook"
```

### 再看最近 update

```bash
curl -s "https://api.telegram.org/bot${TG_TOKEN}/getUpdates"
```

### 常見錯誤

- `chat not found`：`TG_CHAT_ID` 錯或 bot 不在群組
- `Forbidden: bot was blocked by the user`：bot 被封鎖
- `429 Too Many Requests`：降低發送頻率或提高 cooldown
