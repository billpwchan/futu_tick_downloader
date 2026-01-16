# Architecture

## Goals
- 24/7 HK tick-by-tick collection with callback -> async queue -> SQLite
- SQLite schema matches existing JChart layout, sharded by market/trading_day
- Auto reconnect + resubscribe + optional backfill on reconnect

## Components
- OpenD (Futu OpenAPI): maintains the connection and pushes TICKER data
- Collector process:
  - FutuTickerClient: connect, subscribe, reconnect, backfill
  - FutuTickerHandler: callback -> normalize DataFrame -> enqueue
  - TickPersistQueue: asyncio queue + batch aggregation + shard routing
  - SQLiteStore: per-day DB writer with WAL + relaxed sync
  - HealthServer: optional /healthz endpoint

## Data Flow
1. OpenD pushes ticks -> FutuTickerHandler
2. Handler normalizes fields -> TickPersistQueue
3. Queue batches by size/timeout and buckets by (market, trading_day)
4. SQLiteStore writes using INSERT OR IGNORE for idempotency

## Failure and Recovery
- Disconnect: exponential backoff reconnect
- Reconnect: auto resubscribe + optional backfill of recent N ticks
- Process crash: WAL recovery; unique indexes dedupe on restart

## Sharding
- Path: data/sqlite/{market}/{YYYYMMDD}.db
- trading_day is derived from tick time in Asia/Hong_Kong

## Capacity Notes
- Tick volume varies by symbol; monitor DB growth and disk usage
- One DB per trading day makes backup and retention straightforward
