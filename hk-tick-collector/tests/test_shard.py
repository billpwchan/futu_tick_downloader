from hk_tick_collector.sqlite_store import SQLiteStore
from hk_tick_collector.utils import parse_time_to_ms, trading_day_from_ts


def test_shard_path_and_trading_day(tmp_path):
    store = SQLiteStore(
        data_dir=str(tmp_path),
        journal_mode="WAL",
        synchronous="NORMAL",
        temp_store="MEMORY",
    )
    path = store.db_path("HK", "20240102")
    assert path.endswith("/sqlite/HK/20240102.db")

    ts_ms = parse_time_to_ms("2024-01-02 09:30:00.123")
    trading_day = trading_day_from_ts(ts_ms)
    assert trading_day == "20240102"
