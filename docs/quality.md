# 資料品質與缺口檢測

本文件說明 `HK Tick Collector` 的資料品質機制：`Hard Gap`、`Soft Stall`、`quality_report`。

## 目標

- 回答交易者最關心的問題：今天資料能不能用？
- 低誤報：不把本來不活躍的票誤判成缺口。
- 低負擔：不影響既有單 writer 落庫穩定性。

## Hard Gap（強缺口）

只在以下條件成立時才記錄：

1. 該 `symbol` 在最近 `GAP_ACTIVE_WINDOW_SEC` 內 tick 數量 >= `GAP_ACTIVE_MIN_TICKS`
2. 相鄰兩筆 tick 的 `ts_ms` 差值 > `GAP_THRESHOLD_SEC`
3. 兩筆都在同一個交易時段（`TRADING_SESSIONS`）

Hard Gap 會落在 daily DB 的 `gaps` 表中，欄位重點：

- `trading_day`（`YYYY-MM-DD` 概念，實作內以 `YYYYMMDD` 對應 daily DB）
- `symbol`
- `gap_start_ts_ms` / `gap_end_ts_ms`
- `gap_sec`
- `reason=hard_gap`
- `meta_json`（門檻、活躍度、證據）

## Soft Stall（弱觀察）

- 條件：活躍票在交易時段內，兩筆 tick 間隔 > `GAP_STALL_WARN_SEC`
- 行為：只出現在 `quality_report` 的觀察區，不寫入 `gaps` 表

## 相關環境變數

```env
GAP_ENABLED=1
GAP_THRESHOLD_SEC=10
GAP_ACTIVE_WINDOW_SEC=300
GAP_ACTIVE_MIN_TICKS=50
GAP_STALL_WARN_SEC=30
TRADING_TZ=Asia/Hong_Kong
TRADING_SESSIONS=09:30-12:00,13:00-16:00
```

## quality_report 位置與內容

輸出位置：

- `{DATA_ROOT}/_reports/quality/YYYYMMDD.json`

重點欄位：

- 基本資訊：`trading_day`、`host`、`collector_version`、`db.path/size/wal_size`
- 覆蓋：`start_ts_ms`、`end_ts_ms`、`duration_sec`、`last_tick_age_sec`
- 數量：`total_rows`、`rows_per_symbol`
- 缺口：`hard_gaps_total`、`hard_gaps_total_sec`、`largest_gap_sec`
- 觀察：`soft_stalls_total`、`warnings`
- 結論：`quality_grade(A/B/C/D)`、`suggestions`

## 判讀建議（MVP）

- `A`：可直接用於下游分析
- `B`：有短暫停滯，建議抽查核心票
- `C`：出現 >60 秒缺口，建議盤後回補
- `D`：缺口嚴重或資料不足，應先修復再使用
