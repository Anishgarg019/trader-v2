"""s001 re-expressed as a DSL spec must reproduce the hand-written signal logic
(Phase 11, RESEARCHER-SPEC §9)."""
import numpy as np
import pandas as pd

from agent.strategy import S001_SPEC
from agent.strategy_compiler import compile_spec
from agent.signals.momentum import rsi
from agent.signals._common import sma


def _ohlc(n=600, seed=11):
    """Trending+cyclic series so RSI dips below 30 while above/below SMA200 both occur."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100 + 0.05 * t + 12 * np.sin(t / 18.0) + np.cumsum(rng.standard_normal(n) * 0.4)
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2019-01-01", periods=n, freq="B")
    return pd.DataFrame({"open": close, "high": close + 1.0, "low": close - 1.0,
                         "close": close, "volume": 1000 + rng.integers(0, 500, n)}, index=idx)


def test_s001_spec_validates_and_counts():
    c = compile_spec(S001_SPEC)
    assert c.n_params == 3  # rsi-entry threshold + rsi-exit threshold + atr_k


def test_compiled_entries_match_handwritten_signal():
    df = _ohlc()
    c = compile_spec(S001_SPEC)
    close = df["close"]
    # hand-written s001 entry: RSI(14) < 30 AND close > SMA(200)
    want_entry = ((rsi(close, 14) < 30) & (close > sma(close, 200))).fillna(False)
    pd.testing.assert_series_equal(c.entries(df).rename(None), want_entry.rename(None))


def test_compiled_exits_match_handwritten_signal():
    df = _ohlc()
    c = compile_spec(S001_SPEC)
    # hand-written s001 signal exit: RSI(14) > 55 (the ATR/time stops are protective, not signal)
    want_exit = (rsi(df["close"], 14) > 55).fillna(False)
    pd.testing.assert_series_equal(c.exits(df).rename(None), want_exit.rename(None))


def test_entry_actually_fires_somewhere():
    # sanity: on this series the entry condition does occur (so the test isn't vacuous)
    df = _ohlc()
    c = compile_spec(S001_SPEC)
    assert int(c.entries(df).sum()) >= 1
