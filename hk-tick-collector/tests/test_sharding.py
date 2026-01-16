from hk_tick_collector.db import SQLiteTickStore, db_path_for_trading_day
from hk_tick_collector.models import TickRow


def test_db_path_and_trading_day_switch(tmp_path):
    store = SQLiteTickStore(tmp_path)

    day1 = "20240102"
    day2 = "20240103"

    row1 = TickRow(
        market="HK",
        symbol="HK.00001",
        ts_ms=1704161400000,
        price=10.0,
        volume=100,
        turnover=1000.0,
        direction="BUY",
        seq=1,
        tick_type="",
        push_type="push",
        provider="futu",
        trading_day=day1,
        inserted_at_ms=1704161400000,
    )
    row2 = TickRow(
        market="HK",
        symbol="HK.00001",
        ts_ms=1704247800000,
        price=11.0,
        volume=200,
        turnover=2200.0,
        direction="SELL",
        seq=2,
        tick_type="",
        push_type="push",
        provider="futu",
        trading_day=day2,
        inserted_at_ms=1704247800000,
    )

    store.insert_ticks(day1, [row1])
    store.insert_ticks(day2, [row2])

    assert db_path_for_trading_day(tmp_path, day1).exists()
    assert db_path_for_trading_day(tmp_path, day2).exists()
