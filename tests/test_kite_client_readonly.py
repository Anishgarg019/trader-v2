"""The Kite data client must be READ-ONLY: no order methods, reads delegate correctly."""
import pytest

from agent.broker.kite_client import KiteDataClient, FORBIDDEN_ORDER_METHODS


def test_wrapper_exposes_no_order_methods(fake_kite):
    client = KiteDataClient(kite=fake_kite)
    for method in FORBIDDEN_ORDER_METHODS:
        assert not hasattr(client, method), f"wrapper leaked order method: {method}"


def test_underlying_kite_is_private(fake_kite):
    client = KiteDataClient(kite=fake_kite)
    # No public attribute should hand back the raw KiteConnect (which has order methods).
    assert not hasattr(client, "kite")
    assert not hasattr(client, "_kite")


def test_read_methods_delegate(fake_kite):
    client = KiteDataClient(kite=fake_kite)
    assert client.profile()["user_id"] == "AB1234"
    assert client.ltp(["NSE:INFY"])["NSE:INFY"]["last_price"] == 100.0
    assert client.quote(["NSE:INFY"])["NSE:INFY"]["last_price"] == 100.0
    candles = client.historical_data(408065, "2026-05-01", "2026-05-29", "day")
    assert candles[-1]["close"] == 103
    assert client.holdings() == []


def test_search_instruments_filters_and_limits(fake_kite):
    client = KiteDataClient(kite=fake_kite)
    res = client.search_instruments("INFY", filter_on="tradingsymbol")
    assert len(res) == 1
    assert res[0]["instrument_token"] == 408065
    assert res[0]["exchange"] == "NSE"

    # RELIANCE exists on both NSE and BSE — confirm both surface (spec §1.3a caveat).
    both = client.search_instruments("RELIANCE", filter_on="tradingsymbol", limit=10)
    assert {r["exchange"] for r in both} == {"NSE", "BSE"}

    limited = client.search_instruments("RELIANCE", filter_on="tradingsymbol", limit=1)
    assert len(limited) == 1


def test_requires_api_key_when_no_kite_injected():
    with pytest.raises(ValueError):
        KiteDataClient()
