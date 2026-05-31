"""Verify the Zerodha cost model against the rates published at zerodha.com/charges."""
import pytest

from backtest.costs import CostModel, CNC, MIS, BUY, SELL

CM = CostModel()


def test_delivery_buy_breakdown():
    # qty 100 @ ₹100 → turnover ₹10,000
    b = CM.charge(BUY, CNC, "NSE", 100, 100.0)
    assert b.brokerage == 0.0
    assert b.stt == pytest.approx(10.0)        # 0.1%
    assert b.transaction == pytest.approx(0.307)  # 0.00307% NSE
    assert b.sebi == pytest.approx(0.01)       # ₹10/crore
    assert b.stamp == pytest.approx(1.5)       # 0.015% buy
    assert b.dp == 0.0                          # no DP on buy
    assert b.gst == pytest.approx(0.18 * (0 + 0.01 + 0.307))


def test_delivery_sell_has_dp_and_no_stamp():
    s = CM.charge(SELL, CNC, "NSE", 100, 110.0)
    assert s.stt == pytest.approx(0.001 * 11000)  # 0.1% both sides
    assert s.stamp == 0.0                          # stamp on buy only
    assert s.dp == pytest.approx(15.34)            # delivery sell DP


def test_intraday_brokerage_capped_at_20():
    # turnover ₹10,00,000 → 0.03% = ₹300, capped to ₹20
    b = CM.charge(BUY, MIS, "NSE", 1000, 1000.0)
    assert b.brokerage == pytest.approx(20.0)
    assert b.stt == 0.0          # intraday STT on sell only
    assert b.stamp == pytest.approx(0.00003 * 1_000_000)  # 0.003% buy
    assert b.dp == 0.0           # no DP intraday


def test_intraday_small_order_brokerage_is_percentage():
    # turnover ₹10,000 → 0.03% = ₹3 (< ₹20 cap)
    b = CM.charge(BUY, MIS, "NSE", 100, 100.0)
    assert b.brokerage == pytest.approx(3.0)


def test_intraday_sell_stt():
    s = CM.charge(SELL, MIS, "NSE", 100, 100.0)
    assert s.stt == pytest.approx(0.00025 * 10000)  # 0.025% sell


def test_bse_transaction_rate_differs():
    nse = CM.charge(BUY, CNC, "NSE", 100, 100.0).transaction
    bse = CM.charge(BUY, CNC, "BSE", 100, 100.0).transaction
    assert bse > nse
    assert bse == pytest.approx(0.0000375 * 10000)


def test_round_trip_positive():
    total = CM.round_trip(CNC, "NSE", 100, 100.0, 110.0)
    assert total > 0


def test_unsupported_product_raises():
    with pytest.raises(ValueError):
        CM.charge(BUY, "NRML", "NSE", 100, 100.0)
