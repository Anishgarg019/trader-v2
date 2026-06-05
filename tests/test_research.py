"""Per-symbol research gate (Phase 11, RESEARCHER-SPEC §5/§10)."""
import numpy as np
import pandas as pd

from backtest.research import evaluate_spec


def _trend(n=300, drift=0.5, seed=1, start=100.0):
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(np.full(n, drift) + rng.normal(0, 0.4, n))
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5,
                         "close": close, "volume": np.full(n, 1e5)}, index=idx)


# A "mostly-long when above a short MA" strategy: profits riding an uptrend, bleeds in a
# downtrend. Lets us steer per-symbol pass/fail deterministically.
LONGBIAS = {
    "id": "tb", "name": "trend-bias", "families": ["trend"], "timeframe": "day",
    "entry": {"all": [{"pred": "price_above_ma", "length": 5, "kind": "sma"}]},
    "exit": {"any": [{"pred": "price_below_ma", "length": 5, "kind": "sma"}]},
    "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0,
}


def test_wins_on_uptrend_loses_on_downtrend():
    frames = {"UP": _trend(drift=0.6, seed=2), "DOWN": _trend(drift=-0.6, seed=3)}
    v = evaluate_spec(LONGBIAS, frames, min_trades_oos=1)
    assert v.deployed_symbols == ["UP"]
    assert v.passed is True
    assert v.per_symbol["UP"].passed is True
    assert v.per_symbol["DOWN"].passed is False
    assert v.per_symbol["UP"].oos_metrics["total_return"] > 0


def test_all_losers_rejected_to_graveyard():
    frames = {"D1": _trend(drift=-0.6, seed=4), "D2": _trend(drift=-0.7, seed=5)}
    v = evaluate_spec(LONGBIAS, frames, min_trades_oos=1)
    assert v.deployed_symbols == []
    assert v.passed is False
    assert "graveyard" in v.notes.lower()


def test_known_overfit_spec_fails():
    # too_few_trades_oos: a rarely-trading spec on a short OOS window can't clear the
    # trade-count flag → rejected even if it happens to be green. Pin the floor explicitly
    # (decoupled from the gate's default, which is calibrated for multi-year daily windows).
    frames = {"UP": _trend(drift=0.6, seed=6)}
    v = evaluate_spec(LONGBIAS, frames, min_trades_oos=30)
    assert v.per_symbol["UP"].overfit is not None
    assert "too_few_trades_oos" in v.per_symbol["UP"].overfit.flags
    assert v.per_symbol["UP"].passed is False
    assert v.passed is False


def test_min_symbols_floor():
    frames = {"UP": _trend(drift=0.6, seed=2), "DOWN": _trend(drift=-0.6, seed=3)}
    # require 2 profitable symbols but only 1 wins → spec fails overall, but the winner is
    # still recorded as a per-symbol pass.
    v = evaluate_spec(LONGBIAS, frames, min_trades_oos=1, min_symbols=2)
    assert v.deployed_symbols == ["UP"]
    assert v.passed is False


def test_compile_error_does_not_escape():
    bad = {"id": "bad", "timeframe": "day", "entry": {"all": [{"pred": "teleport"}]},
           "exit": {"any": [{"pred": "rsi_above", "length": 14, "threshold": 60}]},
           "atr_k": 2.0}
    v = evaluate_spec(bad, {"UP": _trend()})
    assert v.passed is False
    assert v.deployed_symbols == []
    assert "error" in v.notes.lower()


def test_exchange_prefixed_symbols_normalize():
    frames = {"NSE:UP": _trend(drift=0.6, seed=2)}
    v = evaluate_spec(LONGBIAS, frames, min_trades_oos=1)
    assert v.deployed_symbols == ["NSE:UP"]
    assert v.per_symbol["NSE:UP"].symbol == "UP"


def test_summary_is_auditable():
    frames = {"UP": _trend(drift=0.6, seed=2), "DOWN": _trend(drift=-0.6, seed=3)}
    v = evaluate_spec(LONGBIAS, frames, min_trades_oos=1)
    s = v.summary()
    assert s["deployed_symbols"] == ["UP"]
    assert set(s["per_symbol"]) == {"UP", "DOWN"}
    assert "oos_return" in s["per_symbol"]["UP"]
