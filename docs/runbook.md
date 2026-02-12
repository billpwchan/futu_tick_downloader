# Runbook（值班與故障處理）

本文件給值班同仁使用，原則是先看「結論與影響」，再執行最小修復動作。

## 1. 日常操作

### 1.1 看服務狀態

```bash
sudo systemctl status hk-tick-collector --no-pager
sudo systemctl status futu-opend --no-pager
```

### 1.2 看日誌（用戶視圖）

```bash
scripts/hk-tickctl logs
```

### 1.3 看日誌（工程視圖）

```bash
scripts/hk-tickctl logs --ops --since "30 minutes ago"
```

### 1.4 看 DB 即時狀態

```bash
scripts/hk-tickctl db stats
scripts/hk-tickctl db symbols --minutes 10
```

## 2. 告警 SOP

## 2.1 `PERSIST_STALL`

症狀：Telegram 出現 `🔴 異常`，指標含 `write=0/min`、queue 持續上升。

處置：

1. 先確認 service 是否重啟中

```bash
sudo systemctl status hk-tick-collector --no-pager
```

2. 看最近 watchdog 與 persist 摘要

```bash
scripts/hk-tickctl logs --ops --since "20 minutes ago"
```

3. 確認 DB 仍可讀與最新時間戳

```bash
scripts/hk-tickctl db stats
```

4. 若仍卡住，執行最小恢復

```bash
sudo systemctl restart hk-tick-collector
```

## 2.2 `SQLITE_BUSY`

症狀：Telegram 告警提到 busy backoff 持續升高。

處置：

1. 確認是否有其他程序同時寫入同一 DB
2. 觀察 queue 與 drift 是否惡化

```bash
scripts/hk-tickctl db stats
scripts/hk-tickctl logs --ops --since "15 minutes ago"
```

3. 若只是短暫尖峰，待 `✅ 已恢復` 訊息即可；若連續 10 分鐘以上未恢復，安排重啟與排程錯峰。

## 2.3 `DISCONNECT`

症狀：與 OpenD 連線中斷。

處置：

1. 先看 OpenD 狀態

```bash
sudo systemctl status futu-opend --no-pager
```

2. 看 collector 重連狀態

```bash
scripts/hk-tickctl logs --ops --since "15 minutes ago"
```

3. 若 OpenD 不健康，先修 OpenD，再確認 collector 出現 `✅ 已恢復`。

## 3. Telegram 排查

## 3.1 驗證 token

```bash
TOKEN="<your_bot_token>"
curl -s "https://api.telegram.org/bot${TOKEN}/getMe"
```

## 3.2 查 `chat_id` / topic id

```bash
curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates"
```

- 群組 `chat.id` 通常是 `-100...`
- forum topic 請確認 `message_thread_id`

## 3.3 檢查 webhook / long polling 衝突

```bash
curl -s "https://api.telegram.org/bot${TOKEN}/getWebhookInfo"
```

若有不預期 webhook，先清除：

```bash
curl -s "https://api.telegram.org/bot${TOKEN}/deleteWebhook"
```

## 3.4 權限檢查

- Bot 必須在群組內
- Bot 必須有發言權限
- chat_id / thread_id 必須與目標群組一致

## 4. sid/eid 關聯排障

- health 摘要會帶 `sid`
- 事件告警會帶 `eid sid`

收到 Telegram 告警後，可直接用 `eid` 或 `sid` 反查 journal：

```bash
sudo journalctl -u hk-tick-collector --since "2 hours ago" --no-pager | grep "eid=eid-xxxx"
sudo journalctl -u hk-tick-collector --since "2 hours ago" --no-pager | grep "sid=sid-xxxx"
```

