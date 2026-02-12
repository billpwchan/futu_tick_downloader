# 可觀測性策略（journald + Telegram）

目標：讓非工程使用者可快速判讀狀態，同時保留工程排障入口，不造成刷屏。

## 1. journald 分級策略

## 1.1 INFO（預設值班視角）

- `health ... sid=...`：每分鐘一條固定欄位摘要
- `persist_summary ...`：每 5 秒一條聚合摘要
- `WATCHDOG ...`：恢復、失敗、停滯等關鍵事件
- `telegram_*`：通知送達/抑制/失敗

## 1.2 DEBUG（工程深挖）

- `poll_stats ...`：逐 symbol 輪詢細節
- `persist_ticks ...`：每個 batch 寫入細節

建議：平時 `LOG_LEVEL=INFO`，僅在故障排查短時間切 `DEBUG`。

## 2. Telegram 降噪節奏

- `HEALTH OK`：啟動後 1 條、開盤前 1 條、收盤後 1 條；其餘僅狀態切換
- `HEALTH WARN`：狀態切換即發；持續最多每 10 分鐘 1 條；恢復即發 OK
- `ALERT`：狀態切換即發；持續最多每 3 分鐘 1 條
- `RECOVERED`：事件恢復立即發（含原 `eid`）
- `DAILY DIGEST`：收盤後 1 條日報

所有事件告警都使用 fingerprint 去重 + cooldown。

## 3. sid/eid 關聯規範

- `sid`：每次 health snapshot 的短 ID，出現在：
  - `health` journal 摘要
  - HEALTH / ALERT / RECOVERED / DAILY DIGEST Telegram
- `eid`：每次事件告警短 ID，出現在：
  - `alert_event` 與 `WATCHDOG persistent_stall` journal
  - ALERT / RECOVERED Telegram

## 4. 實戰查詢

使用者視角：

```bash
scripts/hk-tickctl logs --since "20 minutes ago"
```

工程視角：

```bash
scripts/hk-tickctl logs --ops --since "20 minutes ago"
```

用 `sid/eid` 反查：

```bash
sudo journalctl -u hk-tick-collector --since "2 hours ago" --no-pager | grep "sid=sid-xxxx"
sudo journalctl -u hk-tick-collector --since "2 hours ago" --no-pager | grep "eid=eid-xxxx"
```

