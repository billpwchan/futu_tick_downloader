import pandas as pd

from hk_tick_collector.normalizer import normalize_futu_df


def test_normalize_futu_df():
    df = pd.DataFrame(
        [
            {
                "code": "00700",
                "name": "Tencent",
                "sequence": 123,
                "time": "2024-01-02 09:30:00.123",
                "price": 300.5,
                "volume": 200,
                "turnover": 60100.0,
                "ticker_direction": "BUY",
                "type": "NORMAL",
                "push_data_type": "REALTIME",
            }
        ]
    )
    ticks = normalize_futu_df(df, market="HK", provider="futu")
    assert len(ticks) == 1
    tick = ticks[0]
    assert tick.market == "HK"
    assert tick.symbol == "HK.00700"
    assert tick.seq == 123
    assert tick.price == 300.5
    assert tick.volume == 200
    assert tick.turnover == 60100.0
    assert tick.direction == "BUY"
    assert tick.tick_type == "NORMAL"
    assert tick.push_type == "REALTIME"
    assert tick.provider == "futu"
    assert tick.trading_day == "20240102"
