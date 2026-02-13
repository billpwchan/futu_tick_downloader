# 可觀測性策略（journald + Telegram）

目標：讓非工程使用者可快速判讀狀態，同時保留工程排障入口，不造成刷屏。

## 1. journald 分級策略

## 1.1 INFO（預設值班視角）

- `health ... sid=...`：每分鐘一條固定欄位摘要（不展開全量 symbol）
- `poll_stats_sample ...`：每分鐘一條抽樣（含 symbol/queue/persisted_per_min/lag/phase）
- `persist_summary ...`：每 5 秒一條聚合摘要
- `WATCHDOG ...`：恢復、失敗、停滯等關鍵事件
- `telegram_*`：通知送達/抑制/失敗

## 1.2 DEBUG（工程深挖）

- `poll_stats ...`：逐 symbol 輪詢細節
- `persist_ticks ...`：每個 batch 寫入細節
- `health_symbols_rollup ...`：symbol age/lag 分位數與 top5

建議：平時 `LOG_LEVEL=INFO`，僅在故障排查短時間切 `DEBUG`。

## 2. Telegram 降噪節奏

- `HEALTH OK`：盤前一次、盤中每 15-30 分鐘、午休/盤後一次（可調）
- `HEALTH WARN`：狀態切換即發；持續最多每 10 分鐘 1 條；恢復即發 OK
- `ALERT`：狀態切換即發；持續最多每 3 分鐘 1 條
- `RECOVERED`：事件恢復立即發（含原 `eid`）
- `DAILY DIGEST`：收盤後 1 條日報

所有事件告警都使用 fingerprint 去重 + cooldown + escalation ladder。

盤後/休市語義：

- 不把大幅度 `drift_sec` 直接視為異常（避免出現數萬秒延遲誤導）
- 改用 `距收盤`、`last_update_at`、`close_snapshot_ok`、`db_growth_today`
- 盤中時段若連續多個週期出現零流量且整體 age 高，轉為 `holiday-closed`

## 3. 健康摘要欄位（HEALTH）

Product 固定順序：`結論 -> KPI(最多3) -> 市況 -> 主機 -> sid`  
Ops 固定順序：`結論 -> 指標 -> 進度 -> 主機 -> sid`

- `ingest/min`：`push_rows_per_min + poll_accepted`
- `persist/min`：每分鐘落盤量
- `write_eff`：`persist/min / max(1, ingest/min)`
- `stale_symbols`：超過門檻數
- `stale_bucket(...)`：stale 分桶（盤中與非盤中門檻不同）
- `top5_stale`：最慢 5 個 symbol

## 4. sid/eid 關聯規範

- `sid`：每次 health snapshot 的短 ID，出現在：
  - `health` journal 摘要
  - HEALTH / ALERT / RECOVERED / DAILY DIGEST Telegram
- `eid`：每次事件告警短 ID，出現在：
  - `alert_event` 與 `WATCHDOG persistent_stall` journal
  - ALERT / RECOVERED Telegram

## 5. 實戰查詢

使用者視角：

```bash
scripts/hk-tickctl logs --since "20 minutes ago"
```

工程視角：

```bash
scripts/hk-tickctl logs --ops --since "20 minutes ago"
```

版本與部署驗證：

```bash
scripts/hk-tickctl doctor --since "6 hours ago"
```

用 `sid/eid` 反查：

```bash
sudo journalctl -u hk-tick-collector --since "2 hours ago" --no-pager | grep "sid=sid-xxxx"
sudo journalctl -u hk-tick-collector --since "2 hours ago" --no-pager | grep "eid=eid-xxxx"
```
