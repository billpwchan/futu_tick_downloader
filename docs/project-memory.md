# Project Memory（BAU）

本檔記錄日常維護必知事項，供值班與新同仁快速接手。

## 1. 核心運行模型

- systemd 服務：`hk-tick-collector.service`
- 日誌來源：stdout/stderr -> journald
- Telegram 來源：進程內狀態（非轉發 journal 原文）

## 2. 現行訊號設計

- `health`：每分鐘摘要（含 `sid`）
- `persist_summary`：每 5 秒聚合寫入品質
- `WATCHDOG`：停滯恢復與致命退出
- Telegram：`HEALTH OK/WARN`、`ALERT`、`RECOVERED`、`DAILY DIGEST`

## 3. 排障入口

- 快速看狀態：`scripts/hk-tickctl logs`
- 工程深挖：`scripts/hk-tickctl logs --ops`
- DB 速查：`scripts/hk-tickctl db stats`
- 用 `sid/eid` 對齊 Telegram 與 journal

## 4. 不可破壞約束

- 不可讓 notifier 阻塞寫入主流程
- 不可移除 watchdog 自動恢復與退出策略
- 非必要不在 INFO 輸出逐 batch / 逐 symbol 明細

