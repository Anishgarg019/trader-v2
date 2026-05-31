"""End-to-end: the spec's RSI+MA example through indicators → backtest → OOS validation."""
import numpy as np
import pandas as pd

from agent.signals import momentum, trend
from backtest.engine import run_backtest
from backtest.costs import CostModel
from backtest.validation import train_test_split, overfit_report


def _synthetic_ohlc(n=600, seed=7):
    """Trending series with cycles + noise so RSI crossings and MA filters both trigger."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100 + 0.05 * t + 8 * np.sin(t / 15.0) + np.cumsum(rng.standard_normal(n) * 0.3)
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2019-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": close, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": 1000 + rng.integers(0, 500, n),
    }, index=idx)


def rsi_ma_signals(df, rsi_len=14, rsi_entry=35, rsi_exit=55, ma_len=200):
    """Spec §3.2 example shape: long when RSI low AND price above long MA; exit when RSI high."""
    r = momentum.rsi(df["close"], rsi_len)
    above = trend.price_above_ma(df["close"], ma_len, "sma")
    entries = (r < rsi_entry) & above
    exits = r > rsi_exit
    return entries.fillna(False), exits.fillna(False)


def test_rsi_ma_backtest_runs_end_to_end():
    df = _synthetic_ohlc()
    entries, exits = rsi_ma_signals(df)
    res = run_backtest(df, entries, exits, initial_cash=100000.0,
                       cost_model=CostModel(), slippage_bps=5.0,
                       fill="next_open", product="CNC", exchange="NSE")
    # Produces a full metrics dict and a sane equity curve.
    assert len(res.equity_curve) == len(df)
    assert res.metrics["trades"] >= 1
    assert np.isfinite(res.metrics["max_drawdown"])
    assert -1.0 <= res.metrics["max_drawdown"] <= 0.0
    assert res.equity_curve.iloc[0] == 100000.0
    # round-trip trades carry charges (friction is modelled, not free)
    assert all(t.charges > 0 for t in res.trades)


def test_oos_split_and_overfit_check_on_real_pipeline():
    df = _synthetic_ohlc()
    is_df, oos_df = train_test_split(df, 0.7)
    cm = CostModel()

    def run(d):
        e, x = rsi_ma_signals(d)
        return run_backtest(d, e, x, cost_model=cm, slippage_bps=5.0).metrics

    rep = overfit_report(run(is_df), run(oos_df), n_params=4)
    # Whatever the verdict, the report must be well-formed and decisive.
    assert isinstance(rep.rejected, bool)
    assert set(rep.detail) >= {"is_sharpe", "oos_sharpe", "oos_trades", "oos_return"}


def test_obviously_overfit_rule_is_rejected():
    """A 'rule' fitted to one lucky window: many params, dies OOS → must be rejected."""
    is_m = {"sharpe_like": 3.0, "trades": 80, "total_return": 1.2}
    oos_m = {"sharpe_like": -0.8, "trades": 25, "total_return": -0.3}
    rep = overfit_report(is_m, oos_m, n_params=12, min_trades_oos=30)
    assert rep.rejected is True
    assert "too_few_trades_oos" in rep.flags
    assert "negative_oos_return" in rep.flags
    assert "many_params" in rep.flags
