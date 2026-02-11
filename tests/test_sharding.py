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
        recv_ts_ms=1704161400000,
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
        recv_ts_ms=1704247800000,
        inserted_at_ms=1704247800000,
    )

    store.insert_ticks(day1, [row1])
    store.insert_ticks(day2, [row2])

    assert db_path_for_trading_day(tmp_path, day1).exists()
    assert db_path_for_trading_day(tmp_path, day2).exists()


def test_fetch_max_seq_by_symbol_recent_across_multiple_days(tmp_path):
    store = SQLiteTickStore(tmp_path)

    store.insert_ticks(
        "20240102",
        [
            TickRow(
                market="HK",
                symbol="HK.00700",
                ts_ms=1704161400000,
                price=10.0,
                volume=100,
                turnover=1000.0,
                direction="BUY",
                seq=120,
                tick_type="",
                push_type="push",
                provider="futu",
                trading_day="20240102",
                recv_ts_ms=1704161400000,
                inserted_at_ms=1704161400000,
            )
        ],
    )
    store.insert_ticks(
        "20240103",
        [
            TickRow(
                market="HK",
                symbol="HK.00700",
                ts_ms=1704247800000,
                price=11.0,
                volume=200,
                turnover=2200.0,
                direction="SELL",
                seq=150,
                tick_type="",
                push_type="push",
                provider="futu",
                trading_day="20240103",
                recv_ts_ms=1704247800000,
                inserted_at_ms=1704247800000,
            )
        ],
    )

    seeded = store.fetch_max_seq_by_symbol_recent(
        symbols=["HK.00700"],
        trading_days=["20240104", "20240103", "20240102"],
        max_db_files=3,
    )
    assert seeded == {"HK.00700": 150}
