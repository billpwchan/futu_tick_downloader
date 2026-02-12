# 操作手冊：資料品質

## 目的

統一時間戳語義與常用 SQL 檢查，避免「時間差 8 小時」等常見判讀錯誤。

## 前置條件

- 可讀取 `ticks` 資料表
- 已確認資料來源為港股並使用 `Asia/Hong_Kong` 語義

## 核心語義

- `ticks.ts_ms`：事件時間，UTC epoch 毫秒。
- `ticks.recv_ts_ms`：採集器接收時間，UTC epoch 毫秒。
- 港股本地來源時間在 mapping 階段會轉為 UTC epoch。

## 步驟

### 1) 新鮮度檢查

```sql
SELECT ROUND(strftime('%s','now') - MAX(ts_ms)/1000.0, 3) AS lag_sec FROM ticks;
```

### 2) 接收時間與事件時間差

```sql
SELECT ROUND(AVG((recv_ts_ms - ts_ms) / 1000.0), 3) AS avg_recv_minus_event_sec FROM ticks;
```

### 3) 重複群組檢查

```sql
SELECT COUNT(*) FROM (
  SELECT symbol, seq
  FROM ticks
  WHERE seq IS NOT NULL
  GROUP BY symbol, seq
  HAVING COUNT(*) > 1
);
```

### 4) 時鐘／時區混淆排查

症狀：

- dashboard 顯示時間相差約 8 小時
- SQL 顯示的 UTC 與 localtime 不一致

處理：

- 明確用 UTC 查詢
- 比較 `datetime(ts_ms/1000,'unixepoch')` 與 `datetime(ts_ms/1000,'unixepoch','localtime')`
- 使用 `scripts/check_ts_semantics.py` 進行 drift 檢查

### 5) drift 調查

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
python3 scripts/check_ts_semantics.py --db /data/sqlite/HK/${DAY}.db --tolerance-sec 30
```

若歷史資料已知有 future-shift 問題，可評估：

```bash
python3 scripts/repair_future_ts_ms.py --data-root /data/sqlite/HK --day <YYYYMMDD>
```

先在備份副本上執行。

## 如何驗證

- `lag_sec` 在合理範圍內。
- 無異常重複群組。
- drift 檢查落在容忍範圍。

## 常見問題

- 查詢顯示時間「不對」：通常是查詢層時區處理不一致，而非落盤錯誤。
