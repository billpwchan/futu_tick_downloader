from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from hk_tick_collector.mapping import parse_time_to_ts_ms, ticker_df_to_rows

HK_TZ = ZoneInfo("Asia/Hong_Kong")


def _expected_ts_ms(day: str, hhmmss: str) -> int:
    dt = datetime.strptime(f"{day} {hhmmss}", "%Y%m%d %H:%M:%S").replace(tzinfo=HK_TZ)
    return int(dt.timestamp() * 1000)


def test_ticker_mapping_ts_ms_uses_hk_market_timezone():
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
                "trading_day": "20240102",
            }
        ]
    )
    rows = ticker_df_to_rows(df, provider="futu", push_type="push")
    assert len(rows) == 1
    assert rows[0].ts_ms == _expected_ts_ms("20240102", "09:30:00")
    assert rows[0].symbol == "HK.00700"
    assert rows[0].seq == 123


@pytest.mark.parametrize(
    ("time_text", "expected"),
    [
        ("09:30:00", "09:30:00"),
        ("12:00:00", "12:00:00"),
        ("12:30:00", "12:30:00"),
        ("13:00:00", "13:00:00"),
        ("16:00:00", "16:00:00"),
    ],
)
def test_parse_time_to_ts_ms_market_session_edges(time_text: str, expected: str):
    assert parse_time_to_ts_ms(time_text, "20240102") == _expected_ts_ms("20240102", expected)


def test_parse_time_to_ts_ms_cross_day_midnight():
    assert parse_time_to_ts_ms("00:05:00", "20240103") == _expected_ts_ms("20240103", "00:05:00")


def test_parse_time_to_ts_ms_compact_hhmmss():
    assert parse_time_to_ts_ms("093000", "20240102") == _expected_ts_ms("20240102", "09:30:00")


def test_parse_time_to_ts_ms_compact_hhmmss_numeric():
    assert parse_time_to_ts_ms(93000, "20240102") == _expected_ts_ms("20240102", "09:30:00")


def test_parse_time_to_ts_ms_epoch_seconds_numeric():
    expected = _expected_ts_ms("20240102", "09:30:00")
    assert parse_time_to_ts_ms(expected // 1000, "20240102") == expected


def test_parse_time_to_ts_ms_with_timezone_string():
    value = "2024-01-02T01:30:00+00:00"
    assert parse_time_to_ts_ms(value, "20240102") == _expected_ts_ms("20240102", "09:30:00")


def test_parse_time_to_ts_ms_corrects_obvious_future_plus_8h(monkeypatch):
    expected = _expected_ts_ms("20240102", "09:30:00")
    raw_future = expected + (8 * 3600 * 1000)
    monkeypatch.setattr("hk_tick_collector.mapping.time.time", lambda: expected / 1000.0)
    assert parse_time_to_ts_ms(raw_future, "20240102") == expected


def test_ticker_mapping_sets_recv_ts_ms(monkeypatch):
    recv_ts_ms = 1704161400123
    monkeypatch.setattr("hk_tick_collector.mapping.time.time", lambda: recv_ts_ms / 1000.0)
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
                "trading_day": "20240102",
            }
        ]
    )
    rows = ticker_df_to_rows(df, provider="futu", push_type="push")
    assert rows[0].recv_ts_ms == recv_ts_ms
    assert rows[0].inserted_at_ms == recv_ts_ms
