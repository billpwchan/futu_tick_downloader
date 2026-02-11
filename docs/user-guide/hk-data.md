# HK Data Guide

## Time Baseline

- `ticks.ts_ms` is always **UTC epoch milliseconds**.
- `ticks.recv_ts_ms` is collector receive time in **UTC epoch milliseconds**.
- When source tick time is HK local market time, collector converts:
  - `Asia/Hong_Kong local time` -> `UTC epoch ms`

## Quick Query Examples

Assume:

```bash
TODAY_HK=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/data/sqlite/HK/${TODAY_HK}.db
```

Check latest timestamp drift:

```sql
SELECT
  datetime(strftime('%s','now'),'unixepoch') AS now_utc,
  datetime(MAX(ts_ms)/1000,'unixepoch') AS max_ts_utc,
  (MAX(ts_ms)/1000.0 - strftime('%s','now')) AS max_minus_now_sec,
  COUNT(*) AS rows
FROM ticks;
```

Quick command (default tolerance `±5s`):

```bash
python3 scripts/check_ts_semantics.py --db "$DB" --tolerance-sec 5
```

View recent rows in UTC:

```sql
SELECT
  symbol,
  seq,
  datetime(ts_ms/1000,'unixepoch') AS ts_utc,
  price,
  volume
FROM ticks
ORDER BY ts_ms DESC
LIMIT 20;
```

Check recent 10-minute ingestion:

```sql
SELECT
  COUNT(*) AS n,
  datetime(MIN(ts_ms)/1000,'unixepoch') AS min_utc,
  datetime(MAX(ts_ms)/1000,'unixepoch') AS max_utc
FROM ticks
WHERE ts_ms >= (strftime('%s','now') - 600) * 1000;
```

## Common Pitfall

- If `max_minus_now_sec` is close to `+28800`, data is likely stored with local time treated as UTC.
- Use:
  - `scripts/repair_future_ts_ms.py`
  - `scripts/verify_hk_tick_collector.sh`
  to repair and validate.
