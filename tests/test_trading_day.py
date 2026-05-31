"""Tests for the trading-day gate (spec §6.0): both layers + combined decision."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from agent.trading_day import (
    IST, calendar_check, classify_quote, probe_universe, decide_trading_day,
)

UNIVERSE = ["NSE:RELIANCE", "NSE:INFY", "NSE:HDFCBANK"]

# Reference times (2026-05-29 is a Friday; 2026-05-30 a Saturday).
DURING = datetime(2026, 5, 29, 11, 0, tzinfo=IST)
PREOPEN = datetime(2026, 5, 29, 8, 30, tzinfo=IST)


def live_quote(when="2026-05-29 10:55:00", price=100.0):
    return {"last_price": price, "last_trade_time": when}


# ---------- Layer 1: calendar ----------
def test_calendar_known_holiday_closed():
    res = calendar_check(date(2026, 1, 26))  # Republic Day (Monday)
    assert res.is_open is False
    assert "Republic Day" in res.reason


def test_calendar_weekend_closed():
    res = calendar_check(date(2026, 5, 30))  # Saturday
    assert res.is_open is False
    assert "weekend" in res.reason


def test_calendar_normal_weekday_open():
    res = calendar_check(date(2026, 5, 29))  # Friday, not a holiday
    assert res.is_open is True


def test_calendar_muhurat_flagged_and_closed():
    res = calendar_check(date(2026, 11, 8))  # Sunday Muhurat
    assert res.is_open is False
    assert res.is_muhurat is True


def test_calendar_wrong_year_unsupported():
    res = calendar_check(date(2027, 1, 4))
    assert res.is_open is False
    assert res.year_supported is False


# ---------- Layer 2: classify_quote ----------
def test_classify_live():
    assert classify_quote(live_quote(), DURING, date(2026, 5, 29)) == "live"


def test_classify_empty_variants():
    assert classify_quote(None, DURING, date(2026, 5, 29)) == "empty"
    assert classify_quote({}, DURING, date(2026, 5, 29)) == "empty"
    assert classify_quote({"last_price": 0}, DURING, date(2026, 5, 29)) == "empty"
    assert classify_quote({"foo": 1}, DURING, date(2026, 5, 29)) == "empty"


def test_classify_stale_old_date():
    q = live_quote(when="2026-05-28 15:29:00")
    assert classify_quote(q, DURING, date(2026, 5, 29)) == "stale"


def test_classify_stale_too_old_during_session():
    q = live_quote(when="2026-05-29 10:40:00")  # 20 min before 11:00 → stale
    assert classify_quote(q, DURING, date(2026, 5, 29)) == "stale"


def test_classify_price_without_timestamp_is_live():
    assert classify_quote({"last_price": 100.0}, DURING, date(2026, 5, 29)) == "live"


# ---------- Layer 2: probe_universe ----------
def test_probe_all_dark():
    quotes = {name: None for name in UNIVERSE}
    res = probe_universe(quotes, UNIVERSE, DURING, date(2026, 5, 29))
    assert res.all_dark is True
    assert set(res.dark_names) == set(UNIVERSE)


def test_probe_some_dark_is_not_closure():
    quotes = {"NSE:RELIANCE": live_quote(), "NSE:INFY": None, "NSE:HDFCBANK": live_quote()}
    res = probe_universe(quotes, UNIVERSE, DURING, date(2026, 5, 29))
    assert res.all_dark is False
    assert res.some_dark is True
    assert res.dark_names == ["NSE:INFY"]


# ---------- Combined decision ----------
def test_decide_holiday_research_only():
    dec = decide_trading_day(date(2026, 1, 26), DURING, UNIVERSE, {})
    assert dec.is_trading_day is False and dec.research_only is True
    assert dec.layer == "calendar"


def test_decide_weekend_research_only():
    dec = decide_trading_day(date(2026, 5, 30), datetime(2026, 5, 30, 11, 0, tzinfo=IST),
                             UNIVERSE, {})
    assert dec.is_trading_day is False and dec.layer == "calendar"


def test_decide_normal_day_with_live_tape_is_trading_day():
    quotes = {name: live_quote() for name in UNIVERSE}
    dec = decide_trading_day(date(2026, 5, 29), DURING, UNIVERSE, quotes)
    assert dec.is_trading_day is True and dec.research_only is False
    assert dec.layer == "probe"


def test_decide_all_dark_after_open_is_closed_with_alert():
    quotes = {name: None for name in UNIVERSE}
    dec = decide_trading_day(date(2026, 5, 29), DURING, UNIVERSE, quotes)
    assert dec.is_trading_day is False
    assert dec.system_alert is True
    assert set(dec.dark_names) == set(UNIVERSE)


def test_decide_all_dark_pre_open_needs_reprobe_not_closed():
    quotes = {name: None for name in UNIVERSE}
    dec = decide_trading_day(date(2026, 5, 29), PREOPEN, UNIVERSE, quotes)
    assert dec.is_trading_day is False
    assert dec.needs_reprobe is True
    assert dec.system_alert is False  # pre-open quiet is NOT an alert


def test_decide_calendar_open_without_quotes_needs_probe():
    dec = decide_trading_day(date(2026, 5, 29), DURING, universe=None, quotes=None)
    assert dec.needs_probe is True


def test_decide_wrong_year_halts():
    dec = decide_trading_day(date(2027, 1, 4), datetime(2027, 1, 4, 11, 0, tzinfo=IST),
                             UNIVERSE, {})
    assert dec.halt is True and dec.layer == "maintenance"


def test_decide_some_dark_still_trades_and_lists_exclusions():
    quotes = {"NSE:RELIANCE": live_quote(), "NSE:INFY": None, "NSE:HDFCBANK": live_quote()}
    dec = decide_trading_day(date(2026, 5, 29), DURING, UNIVERSE, quotes)
    assert dec.is_trading_day is True
    assert dec.dark_names == ["NSE:INFY"]
