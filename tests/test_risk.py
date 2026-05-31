"""Exhaustive tests for ATR position sizing (spec §4). Safety core — test hard."""
import pytest

from agent.risk import size_position, total_open_risk


def base(**kw):
    args = dict(equity=100000.0, available_cash=100000.0, entry_price=100.0,
                atr=2.0, k=2.0)
    args.update(kw)
    return size_position(**args)


def test_basic_sizing_per_name_cap_binds():
    # R=5000, stop_dist=4 → raw=1250; cash_cap=1000; per_name=200 → qty=200 (per-name binds)
    r = base()
    assert r.allowed and r.qty == 200
    assert r.stop_distance == 4.0
    assert r.stop_price == 96.0
    assert r.risk_rupees == 5000.0
    assert r.trade_risk == 800.0
    assert r.notional == 20000.0


def test_cash_cap_binds_and_enforces_no_leverage():
    r = base(available_cash=5000.0)   # cash_cap = 50
    assert r.allowed and r.qty == 50
    assert r.notional <= 5000.0       # never buys more than cash → 1× only


def test_raw_qty_zero_when_stop_too_tight_for_budget():
    # tiny risk budget vs a 4-rupee stop → raw_qty floors to 0 → skip
    r = base(strategy_risk_budget=3.0)
    assert not r.allowed and r.qty == 0
    assert "stop too tight" in r.reason


def test_insufficient_cash_skips():
    r = base(available_cash=50.0)     # cash_cap_qty = 0
    assert not r.allowed and r.qty == 0
    assert "insufficient cash" in r.reason


def test_invalid_stop_distance_skips():
    assert not base(atr=0.0).allowed
    assert not base(k=0.0).allowed


def test_invalid_entry_price_skips():
    assert not base(entry_price=0.0).allowed


def test_lot_size_rounds_down():
    # force min() = 10, lot_size 4 → qty 8
    r = size_position(equity=10_000_000.0, available_cash=1000.0, entry_price=100.0,
                      atr=2.0, k=2.0, lot_size=4)
    assert r.qty == 8
    assert r.trade_risk == 8 * 4.0


def test_total_open_risk_cap_skips_when_breached():
    # existing 14600 + new 800 = 15400 > 15000 ceiling → skip
    r = base(existing_open_risk=14600.0)
    assert not r.allowed and r.qty == 0
    assert "total open-risk cap" in r.reason


def test_total_open_risk_cap_allows_when_fits():
    r = base(existing_open_risk=14000.0)   # 14000 + 800 = 14800 ≤ 15000
    assert r.allowed and r.qty == 200


def test_strategy_budget_below_5pct_is_used():
    # budget 400 < 5% (5000); raw = floor(400/4) = 100 (binds under per-name 200)
    r = base(strategy_risk_budget=400.0)
    assert r.allowed and r.qty == 100
    assert r.risk_rupees == 400.0


@pytest.mark.parametrize("cash,entry", [(100000, 100), (37000, 250), (5000, 100), (999, 33)])
def test_never_exceeds_cash_invariant(cash, entry):
    r = size_position(equity=100000.0, available_cash=float(cash), entry_price=float(entry),
                      atr=2.0, k=2.0, max_per_name_notional_pct=1.0)
    assert r.notional <= cash + 1e-9   # 1× leverage invariant always holds


def test_total_open_risk_helper():
    positions = [
        {"quantity": 100, "stop_distance": 4.0},
        {"quantity": 50, "stop_distance": 6.0},
    ]
    assert total_open_risk(positions) == pytest.approx(100 * 4 + 50 * 6)
