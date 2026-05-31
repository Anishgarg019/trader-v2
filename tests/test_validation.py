"""OOS split + overfit rejection (spec §6.4.2)."""
import numpy as np
import pandas as pd
import pytest

from backtest.validation import train_test_split, overfit_report


def _df(n=100):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({"close": np.arange(n)}, index=idx)


def test_split_fraction():
    df = _df(100)
    is_, oos = train_test_split(df, 0.7)
    assert len(is_) == 70 and len(oos) == 30
    assert is_.index[-1] < oos.index[0]


def test_split_by_date():
    df = _df(100)
    is_, oos = train_test_split(df, "2020-02-01")
    assert is_.index[-1] <= pd.Timestamp("2020-02-01")
    assert oos.index[0] > pd.Timestamp("2020-02-01")


def test_split_bad_fraction_raises():
    with pytest.raises(ValueError):
        train_test_split(_df(10), 1.5)


def test_overfit_rejected_when_oos_dies():
    is_m = {"sharpe_like": 2.5, "trades": 120, "total_return": 0.8}
    oos_m = {"sharpe_like": -0.4, "trades": 50, "total_return": -0.2}
    rep = overfit_report(is_m, oos_m, n_params=4)
    assert rep.rejected is True
    assert "negative_oos_return" in rep.flags
    assert "sharpe_collapse" in rep.flags


def test_overfit_rejected_too_few_trades():
    is_m = {"sharpe_like": 1.5, "trades": 40, "total_return": 0.3}
    oos_m = {"sharpe_like": 1.2, "trades": 5, "total_return": 0.1}
    rep = overfit_report(is_m, oos_m, min_trades_oos=30)
    assert rep.rejected is True
    assert "too_few_trades_oos" in rep.flags


def test_healthy_strategy_not_rejected():
    is_m = {"sharpe_like": 1.2, "trades": 120, "total_return": 0.35}
    oos_m = {"sharpe_like": 0.95, "trades": 60, "total_return": 0.22}
    rep = overfit_report(is_m, oos_m, n_params=3)
    assert rep.rejected is False
    assert rep.flags == []


def test_many_params_is_soft_warning_only():
    is_m = {"sharpe_like": 1.2, "trades": 120, "total_return": 0.35}
    oos_m = {"sharpe_like": 1.0, "trades": 60, "total_return": 0.25}
    rep = overfit_report(is_m, oos_m, n_params=9, max_params=5)
    assert "many_params" in rep.flags
    assert rep.rejected is False  # soft flag does not auto-reject
