"""Backtest engine tests: hand-computable P&L, costs, no-lookahead, metrics."""
import numpy as np
import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.costs import CostModel

ZERO_COST = CostModel(
    txn_rate_nse=0, txn_rate_bse=0, sebi_rate=0, ipft_rate=0, gst_rate=0,
    dp_charge=0, stt_delivery=0, stt_intraday_sell=0,
    stamp_delivery_buy=0, stamp_intraday_buy=0,
)


def make_df(closes, opens=None):
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    opens = opens if opens is not None else closes
    return pd.DataFrame({
        "open": opens, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
        "close": closes, "volume": [1000] * n,
    }, index=idx)


def test_simple_round_trip_pnl_close_fill():
    df = make_df([10, 11, 12, 13, 14])
    entries = pd.Series([True, False, False, False, False], index=df.index)
    exits = pd.Series([False, False, False, True, False], index=df.index)
    res = run_backtest(df, entries, exits, initial_cash=100000.0,
                       cost_model=ZERO_COST, slippage_bps=0.0, fill="close",
                       size_fraction=1.0)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.quantity == 10000          # floor(100000/10)
    assert t.entry_price == 10.0 and t.exit_price == 13.0
    assert t.pnl == pytest.approx(30000.0)
    assert res.equity_curve.iloc[-1] == pytest.approx(130000.0)
    assert res.metrics["win_rate"] == 1.0
    assert res.metrics["trades"] == 1.0


def test_next_open_fill_avoids_lookahead():
    # signal at i=0 fills at open[1]; signal at last bar cannot fill
    df = make_df(closes=[10, 11, 12], opens=[10, 11, 12])
    entries = pd.Series([True, False, False], index=df.index)
    exits = pd.Series([False, True, False], index=df.index)
    res = run_backtest(df, entries, exits, cost_model=ZERO_COST, slippage_bps=0.0,
                       fill="next_open", size_fraction=1.0)
    t = res.trades[0]
    assert t.entry_price == 11.0   # open[1], not close[0]
    assert t.exit_price == 12.0    # exit signal at i=1 → open[2]


def test_open_position_closed_at_end_of_data():
    df = make_df([10, 11, 12, 13])
    entries = pd.Series([True, False, False, False], index=df.index)
    exits = pd.Series([False, False, False, False], index=df.index)  # never exits
    res = run_backtest(df, entries, exits, cost_model=ZERO_COST, slippage_bps=0.0,
                       fill="close", size_fraction=1.0)
    assert len(res.trades) == 1
    assert res.trades[0].reason == "end-of-data"


def test_costs_reduce_pnl():
    df = make_df([100, 100, 110, 110])
    entries = pd.Series([True, False, False, False], index=df.index)
    exits = pd.Series([False, False, True, False], index=df.index)
    gross = run_backtest(df, entries, exits, cost_model=ZERO_COST, slippage_bps=0.0,
                         fill="close", size_fraction=1.0)
    costed = run_backtest(df, entries, exits, cost_model=CostModel(), slippage_bps=10.0,
                          fill="close", size_fraction=1.0)
    assert costed.trades[0].pnl < gross.trades[0].pnl
    assert costed.trades[0].charges > 0


def test_slippage_adverse_on_both_sides():
    df = make_df([100, 100, 100])
    entries = pd.Series([True, False, False], index=df.index)
    exits = pd.Series([False, True, False], index=df.index)
    res = run_backtest(df, entries, exits, cost_model=ZERO_COST, slippage_bps=100.0,
                       fill="close", size_fraction=1.0)
    t = res.trades[0]
    assert t.entry_price == pytest.approx(101.0)  # buy 1% worse
    assert t.exit_price == pytest.approx(99.0)    # sell 1% worse


def test_metrics_keys_present():
    df = make_df(list(range(10, 40)))
    entries = pd.Series([i == 0 for i in range(30)], index=df.index)
    exits = pd.Series([i == 29 for i in range(30)], index=df.index)
    res = run_backtest(df, entries, exits, cost_model=ZERO_COST)
    for key in ("total_return", "cagr", "max_drawdown", "sharpe_like",
                "win_rate", "trades", "exposure"):
        assert key in res.metrics


def test_invalid_fill_raises():
    df = make_df([10, 11])
    s = pd.Series([False, False], index=df.index)
    with pytest.raises(ValueError):
        run_backtest(df, s, s, fill="bogus")
