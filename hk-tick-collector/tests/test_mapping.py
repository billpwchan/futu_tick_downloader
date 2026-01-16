from datetime import datetime

import pandas as pd

from hk_tick_collector.mapping import ticker_df_to_rows


def test_ticker_mapping_ts_ms():
    df = pd.DataFrame(
        [
            {
                "code": "HK.00700",
                "time": "2024-01-02 09:30:00",
                "price": 300.5,
                "volume": 100,
                "turnover": 30050.0,
                "ticker_direction": "BUY",
                "sequence": 123,
            }
        ]
    )
    rows = ticker_df_to_rows(df, provider="futu", push_type="push")
    assert len(rows) == 1
    expected_ts = int(datetime(2024, 1, 2, 9, 30, 0).timestamp() * 1000)
    assert rows[0].ts_ms == expected_ts
    assert rows[0].symbol == "HK.00700"
    assert rows[0].seq == 123
