# HK Data User Guide

## 时间字段语义

- `ts_ms`: tick 事件时间，UTC epoch 毫秒。
- `recv_ts_ms`: 采集程序接收时间，UTC epoch 毫秒。
- `trading_day`: 交易日（`YYYYMMDD`，按港股时区语义）。

注意：SQLite `datetime(ts_ms/1000,'unixepoch')` 默认显示 UTC，不是本地时间。

## 快速查询

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${DAY}.db
```

最新 5 条（UTC + 本地）：

```sql
SELECT
  symbol,
  seq,
  datetime(ts_ms/1000,'unixepoch') AS ts_utc,
  datetime(ts_ms/1000,'unixepoch','localtime') AS ts_local,
  price,
  volume
FROM ticks
ORDER BY ts_ms DESC
LIMIT 5;
```

最新 lag：

```sql
SELECT
  (strftime('%s','now') - MAX(ts_ms)/1000.0) AS lag_sec,
  datetime(MAX(ts_ms)/1000,'unixepoch') AS max_ts_utc
FROM ticks;
```

按 symbol 看最近 10 分钟：

```sql
SELECT symbol, COUNT(*) AS rows_10m
FROM ticks
WHERE ts_ms >= (strftime('%s','now') - 600) * 1000
GROUP BY symbol
ORDER BY rows_10m DESC;
```

## 推荐脚本

```bash
bash scripts/verify_db.sh "$DB"
bash scripts/healthcheck.sh
```

更多故障场景：[`docs/operations/runbook-hk-tick-collector.md`](../operations/runbook-hk-tick-collector.md)
