"""Reconciliation: broker (paper-book) vs vault expected state (spec §6.1.3)."""
from agent.reconcile import (
    reconcile_positions, expected_positions_from_open_trades,
)


def pos(exchange, sym, qty):
    return {"exchange": exchange, "tradingsymbol": sym, "quantity": qty}


def test_match_is_ok():
    broker = [pos("NSE", "INFY", 50), pos("NSE", "RELIANCE", 10)]
    expected = [pos("NSE", "INFY", 50), pos("NSE", "RELIANCE", 10)]
    r = reconcile_positions(broker, expected)
    assert r.ok and r.breaks == []


def test_quantity_mismatch_flagged():
    r = reconcile_positions([pos("NSE", "INFY", 50)], [pos("NSE", "INFY", 40)])
    assert not r.ok
    assert r.breaks[0].kind == "quantity_mismatch"
    assert r.breaks[0].expected == 40 and r.breaks[0].actual == 50


def test_missing_in_vault():
    r = reconcile_positions([pos("NSE", "INFY", 50)], [])
    assert not r.ok and r.breaks[0].kind == "missing_in_vault"


def test_missing_in_broker():
    r = reconcile_positions([], [pos("NSE", "INFY", 50)])
    assert not r.ok and r.breaks[0].kind == "missing_in_broker"


def test_zero_quantity_ignored():
    r = reconcile_positions([pos("NSE", "INFY", 0)], [])
    assert r.ok


def test_expected_from_open_trades_aggregates():
    trades = [
        {"symbol": "NSE:INFY", "quantity": 30, "direction": "long"},
        {"symbol": "NSE:INFY", "quantity": 20, "direction": "long"},
        {"symbol": "NSE:RELIANCE", "quantity": 10, "direction": "long"},
    ]
    exp = expected_positions_from_open_trades(trades)
    by = {f"{p['exchange']}:{p['tradingsymbol']}": p["quantity"] for p in exp}
    assert by["NSE:INFY"] == 50 and by["NSE:RELIANCE"] == 10


def test_reconcile_detects_introduced_mismatch_end_to_end():
    trades = [{"symbol": "NSE:INFY", "quantity": 50, "direction": "long"}]
    expected = expected_positions_from_open_trades(trades)
    # broker shows a different quantity than the vault expects → break
    broker = [pos("NSE", "INFY", 60)]
    r = reconcile_positions(broker, expected)
    assert not r.ok and r.breaks[0].kind == "quantity_mismatch"
    assert "INFY" in r.summary()
