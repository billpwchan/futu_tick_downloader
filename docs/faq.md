# FAQ

## Is this only for HK symbols?

Primary target is HK tick collection, but symbol handling follows Futu code format and can be extended.

## Why one SQLite DB per trading day?

Operational simplicity: easier retention, backups, and bounded file sizes.

## Is duplicate data expected?

At ingest level yes (push + poll overlap). Final table is deduped via unique indexes and `INSERT OR IGNORE`.

## Can I run without poll fallback?

Yes (`FUTU_POLL_ENABLED=0`), but production reliability decreases if push stream goes stale.

## Does this project redistribute market data?

No. It collects/stores data under your own Futu/OpenD entitlement and terms.

## When should I move off SQLite?

When you need multi-host writes, large-scale analytics, or centralized multi-tenant access. Consider Postgres or columnar lakehouse patterns.
