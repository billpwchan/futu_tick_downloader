# Runbook: Data Quality

## Timestamp Semantics

- `ticks.ts_ms`: event time, UTC epoch milliseconds.
- `ticks.recv_ts_ms`: collector receive time, UTC epoch milliseconds.
- HK local source timestamps are converted to UTC epoch during mapping.

## Common Checks

Freshness:

```sql
SELECT ROUND(strftime('%s','now') - MAX(ts_ms)/1000.0, 3) AS lag_sec FROM ticks;
```

Receive-event gap:

```sql
SELECT ROUND(AVG((recv_ts_ms - ts_ms) / 1000.0), 3) AS avg_recv_minus_event_sec FROM ticks;
```

Duplicate groups:

```sql
SELECT COUNT(*) FROM (
  SELECT symbol, seq
  FROM ticks
  WHERE seq IS NOT NULL
  GROUP BY symbol, seq
  HAVING COUNT(*) > 1
);
```

## Clock / Timezone Confusion

Symptoms:

- max timestamp appears ahead/behind by around 8 hours in dashboards
- SQL render differs between UTC and localtime expectations

Actions:

- verify queries use UTC explicitly
- compare `datetime(ts_ms/1000,'unixepoch')` vs `datetime(ts_ms/1000,'unixepoch','localtime')`
- use `scripts/check_ts_semantics.py` for drift checks

## Drift Investigation

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
python3 scripts/check_ts_semantics.py --db /data/sqlite/HK/${DAY}.db --tolerance-sec 30
```

If historical data has known future-shift issue, evaluate:

```bash
python3 scripts/repair_future_ts_ms.py --data-root /data/sqlite/HK --day <YYYYMMDD>
```

Run on backup copy first.
