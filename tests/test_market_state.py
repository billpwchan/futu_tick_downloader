from __future__ import annotations

from datetime import datetime

from hk_tick_collector.market_state import HK_TZ, MarketCalendar, resolve_market_state


def test_market_state_open_session() -> None:
    state = resolve_market_state(datetime(2026, 2, 13, 10, 0, tzinfo=HK_TZ))
    assert state.trading_day == "20260213"
    assert state.mode == "open"
    assert state.is_trading_day is True
    assert state.is_trading_session is True


def test_market_state_pre_open() -> None:
    state = resolve_market_state(datetime(2026, 2, 13, 9, 10, tzinfo=HK_TZ))
    assert state.mode == "pre-open"
    assert state.is_trading_day is True
    assert state.is_trading_session is False


def test_market_state_weekend_closed() -> None:
    state = resolve_market_state(datetime(2026, 2, 14, 10, 0, tzinfo=HK_TZ))
    assert state.mode == "after-hours"
    assert state.is_trading_day is False
    assert state.is_trading_session is False


def test_market_state_configured_holiday_closed(tmp_path) -> None:
    holiday_file = tmp_path / "hk_holidays.txt"
    holiday_file.write_text("20260213\n", encoding="utf-8")
    calendar = MarketCalendar(holidays=["20260101"], holiday_file=str(holiday_file))

    state = resolve_market_state(
        datetime(2026, 2, 13, 10, 0, tzinfo=HK_TZ),
        calendar=calendar,
    )
    assert state.mode == "holiday-closed"
    assert state.is_trading_day is False
    assert state.is_trading_session is False
