-- hk-tick-collector common SQL snippets

-- 1) Global freshness
SELECT
  datetime(strftime('%s','now'),'unixepoch') AS now_utc,
  datetime(MAX(ts_ms)/1000,'unixepoch') AS max_tick_utc,
  ROUND(strftime('%s','now') - MAX(ts_ms)/1000.0, 3) AS lag_sec,
  COUNT(*) AS rows
FROM ticks;

-- 2) Row count and max seq per symbol
SELECT symbol, COUNT(*) AS rows, MAX(seq) AS max_seq
FROM ticks
GROUP BY symbol
ORDER BY rows DESC;

-- 3) Last tick per symbol
SELECT t.symbol, t.seq, t.ts_ms, t.price, t.volume
FROM ticks t
JOIN (
  SELECT symbol, MAX(ts_ms) AS max_ts_ms
  FROM ticks
  GROUP BY symbol
) m ON m.symbol = t.symbol AND m.max_ts_ms = t.ts_ms
ORDER BY t.symbol;

-- 4) Duplicate groups
SELECT 'dup_symbol_seq' AS check_name, COUNT(*) AS groups
FROM (
  SELECT symbol, seq FROM ticks WHERE seq IS NOT NULL GROUP BY symbol, seq HAVING COUNT(*) > 1
)
UNION ALL
SELECT 'dup_composite_when_seq_null' AS check_name, COUNT(*) AS groups
FROM (
  SELECT symbol, ts_ms, price, volume, turnover
  FROM ticks
  WHERE seq IS NULL
  GROUP BY symbol, ts_ms, price, volume, turnover
  HAVING COUNT(*) > 1
);

-- 5) Event-receive drift
SELECT
  ROUND(AVG((recv_ts_ms - ts_ms) / 1000.0), 3) AS avg_recv_minus_event_sec,
  ROUND(MAX((recv_ts_ms - ts_ms) / 1000.0), 3) AS max_recv_minus_event_sec
FROM ticks;
