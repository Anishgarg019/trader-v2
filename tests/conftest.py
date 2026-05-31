"""Shared test fixtures: a FakeKite that mimics kiteconnect.KiteConnect's surface
(including order methods, to prove our read-only wrapper still exposes none of them)."""
from __future__ import annotations

import pytest


class FakeKite:
    """Stand-in for kiteconnect.KiteConnect with canned read data + order methods."""

    def __init__(self):
        self.access_token = None

    def set_access_token(self, token):
        self.access_token = token

    # --- reads ---
    def profile(self):
        return {"user_id": "AB1234", "user_name": "Test User"}

    def margins(self, segment=None):
        return {"equity": {"net": 100000.0}} if segment is None else {"net": 100000.0}

    def positions(self):
        return {"net": [], "day": []}

    def holdings(self):
        return []

    def orders(self):
        return []

    def order_history(self, order_id):
        return [{"order_id": order_id, "status": "COMPLETE"}]

    def order_trades(self, order_id):
        return []

    def trades(self):
        return []

    def quote(self, *instruments):
        return {ins: {"last_price": 100.0, "last_trade_time": "2026-05-29 15:29:00"} for ins in instruments}

    def ltp(self, *instruments):
        return {ins: {"last_price": 100.0} for ins in instruments}

    def ohlc(self, *instruments):
        return {ins: {"last_price": 100.0, "ohlc": {"open": 99, "high": 101, "low": 98, "close": 100}} for ins in instruments}

    def historical_data(self, instrument_token, from_date, to_date, interval, continuous=False, oi=False):
        return [
            {"date": "2026-05-28", "open": 100, "high": 105, "low": 99, "close": 104, "volume": 1000},
            {"date": "2026-05-29", "open": 104, "high": 106, "low": 102, "close": 103, "volume": 1200},
        ]

    def instruments(self, exchange=None):
        return [
            {"instrument_token": 738561, "exchange": "NSE", "tradingsymbol": "RELIANCE", "name": "RELIANCE INDUSTRIES"},
            {"instrument_token": 408065, "exchange": "NSE", "tradingsymbol": "INFY", "name": "INFOSYS"},
            {"instrument_token": 341249, "exchange": "NSE", "tradingsymbol": "HDFCBANK", "name": "HDFC BANK"},
            {"instrument_token": 500325, "exchange": "BSE", "tradingsymbol": "RELIANCE", "name": "RELIANCE INDUSTRIES"},
        ]

    # --- order/write surface that MUST NOT leak through the wrapper ---
    def place_order(self, *a, **k):
        raise AssertionError("FakeKite.place_order must never be called by the agent")

    def place_gtt(self, *a, **k):
        raise AssertionError("FakeKite.place_gtt must never be called by the agent")

    def modify_order(self, *a, **k):
        raise AssertionError("must never be called")

    def cancel_order(self, *a, **k):
        raise AssertionError("must never be called")


@pytest.fixture
def fake_kite():
    return FakeKite()
