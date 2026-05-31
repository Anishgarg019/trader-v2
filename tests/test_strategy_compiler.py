"""Compiler: predicate re-derivation, combinator truth tables, no look-ahead, live factory
(Phase 11, RESEARCHER-SPEC §4/§10)."""
import numpy as np
import pandas as pd
import pytest

from agent.strategy_compiler import compile_spec
from agent.strategy_spec import SpecError
from agent.signals import trend, momentum, structure, volatility, volume, patterns
from agent.signals._common import sma, ema


@pytest.fixture
def df():
    """A noisy-but-trending OHLCV frame long enough for 200-length indicators."""
    rng = np.random.default_rng(42)
    n = 400
    idx = pd.date_range("2022-01-01", periods=n, freq="D")
    steps = rng.normal(0.3, 2.0, n).cumsum()
    close = 100 + steps
    close = np.maximum(close, 5.0)
    high = close + rng.uniform(0.2, 2.0, n)
    low = close - rng.uniform(0.2, 2.0, n)
    open_ = close + rng.normal(0, 0.5, n)
    vol = rng.uniform(1e5, 5e5, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": vol}, index=idx)


def _spec(entry, exit_=None):
    return {"id": "tx", "name": "tx", "timeframe": "day", "entry": entry,
            "exit": exit_ or {"any": [{"pred": "rsi_above", "length": 14, "threshold": 60}]},
            "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0}


def _leaf_entries(node, df):
    return compile_spec(_spec({"all": [node]})).entries(df)


# ---- per-predicate re-derivation --------------------------------------------
def test_rsi_below_matches_signal(df):
    got = _leaf_entries({"pred": "rsi_below", "length": 14, "threshold": 40}, df)
    want = (momentum.rsi(df["close"], 14) < 40).fillna(False)
    pd.testing.assert_series_equal(got.rename(None), want.rename(None))


def test_price_above_ma_matches_signal(df):
    got = _leaf_entries({"pred": "price_above_ma", "length": 50, "kind": "ema"}, df)
    want = trend.price_above_ma(df["close"], 50, "ema").fillna(False)
    pd.testing.assert_series_equal(got.rename(None), want.astype(bool).rename(None))


def test_price_below_ma_is_complement(df):
    above = _leaf_entries({"pred": "price_above_ma", "length": 50, "kind": "sma"}, df)
    below = _leaf_entries({"pred": "price_below_ma", "length": 50, "kind": "sma"}, df)
    # below == NOT above where the MA is defined
    want = (~trend.price_above_ma(df["close"], 50, "sma")).fillna(False)
    pd.testing.assert_series_equal(below.rename(None), want.astype(bool).rename(None))


def test_ma_cross_up_matches_signal(df):
    got = _leaf_entries({"pred": "ma_cross_up", "fast": 10, "slow": 30, "kind": "sma"}, df)
    want = trend.cross_up(sma(df["close"], 10), sma(df["close"], 30)).fillna(False)
    pd.testing.assert_series_equal(got.rename(None), want.astype(bool).rename(None))


def test_adx_above_matches_signal(df):
    got = _leaf_entries({"pred": "adx_above", "length": 14, "threshold": 25}, df)
    want = (trend.adx(df, 14)["adx"] > 25).fillna(False)
    pd.testing.assert_series_equal(got.rename(None), want.astype(bool).rename(None))


def test_macd_cross_up_matches_signal(df):
    got = _leaf_entries({"pred": "macd_cross_up", "fast": 12, "slow": 26, "signal": 9}, df)
    m = momentum.macd(df["close"], 12, 26, 9)
    want = trend.cross_up(m["macd"], m["signal"]).fillna(False)
    pd.testing.assert_series_equal(got.rename(None), want.astype(bool).rename(None))


def test_breakout_up_matches_signal(df):
    got = _leaf_entries({"pred": "breakout_up", "length": 20}, df)
    want = structure.breakout_up(df, 20).fillna(False)
    pd.testing.assert_series_equal(got.rename(None), want.astype(bool).rename(None))


def test_bollinger_break_up_matches_signal(df):
    got = _leaf_entries({"pred": "bollinger_break_up", "length": 20, "k": 2.0}, df)
    want = (df["close"] > volatility.bollinger_bands(df["close"], 20, 2.0)["upper"]).fillna(False)
    pd.testing.assert_series_equal(got.rename(None), want.astype(bool).rename(None))


def test_volume_spike_matches_signal(df):
    got = _leaf_entries({"pred": "volume_spike", "length": 20, "k": 2.0}, df)
    want = volume.volume_spike(df["volume"], 20, 2.0).fillna(False)
    pd.testing.assert_series_equal(got.rename(None), want.astype(bool).rename(None))


def test_hammer_matches_signal(df):
    got = _leaf_entries({"pred": "hammer", "body_frac": 0.35}, df)
    want = patterns.hammer(df, 0.35).fillna(False)
    pd.testing.assert_series_equal(got.rename(None), want.astype(bool).rename(None))


def test_bullish_engulfing_matches_signal(df):
    got = _leaf_entries({"pred": "bullish_engulfing"}, df)
    want = patterns.bullish_engulfing(df).fillna(False)
    pd.testing.assert_series_equal(got.rename(None), want.astype(bool).rename(None))


# ---- combinator truth tables ------------------------------------------------
def test_all_is_and(df):
    a = {"pred": "rsi_below", "length": 14, "threshold": 45}
    b = {"pred": "price_above_ma", "length": 50, "kind": "sma"}
    got = compile_spec(_spec({"all": [a, b]})).entries(df)
    want = _leaf_entries(a, df) & _leaf_entries(b, df)
    pd.testing.assert_series_equal(got.rename(None), want.rename(None))


def test_any_is_or(df):
    a = {"pred": "rsi_below", "length": 14, "threshold": 45}
    b = {"pred": "breakout_up", "length": 20}
    got = compile_spec(_spec({"any": [a, b]})).entries(df)
    want = _leaf_entries(a, df) | _leaf_entries(b, df)
    pd.testing.assert_series_equal(got.rename(None), want.rename(None))


def test_not_is_complement(df):
    a = {"pred": "rsi_below", "length": 14, "threshold": 45}
    got = compile_spec(_spec({"not": a})).entries(df)
    want = ~_leaf_entries(a, df)
    pd.testing.assert_series_equal(got.rename(None), want.rename(None))


# ---- no look-ahead ----------------------------------------------------------
def test_no_lookahead(df):
    """Entry value at bar i must depend only on data up to bar i — truncating the frame
    after i must not change the value at i."""
    spec = _spec({"all": [
        {"pred": "rsi_below", "length": 14, "threshold": 45},
        {"pred": "breakout_up", "length": 20},
    ]})
    c = compile_spec(spec)
    full = c.entries(df)
    for i in (120, 250, 399):
        truncated = c.entries(df.iloc[: i + 1])
        assert bool(truncated.iloc[i]) == bool(full.iloc[i]), f"look-ahead at bar {i}"


def test_divergence_is_causal(df):
    """Divergence predicates rely on a swing pivot that needs future bars to confirm; the
    compiler must delay them so they carry NO look-ahead (the gate stays honest)."""
    spec = _spec({"all": [{"pred": "bullish_divergence", "length": 14, "osc": "rsi"}]})
    c = compile_spec(spec)
    full = c.entries(df)
    trues = [i for i in range(len(df)) if bool(full.iloc[i])]
    assert trues, "expected at least one divergence signal to make the test meaningful"
    for i in trues:
        truncated = c.entries(df.iloc[: i + 1])
        assert bool(truncated.iloc[i]) == bool(full.iloc[i]), f"divergence look-ahead at bar {i}"


# ---- compile errors ---------------------------------------------------------
def test_compile_validates_first():
    with pytest.raises(SpecError):
        compile_spec(_spec({"all": [{"pred": "nope"}]}))


def test_n_params_attached():
    # entry rsi threshold + exit (default) rsi threshold + atr_k = 3
    c = compile_spec(_spec({"all": [{"pred": "rsi_below", "length": 14, "threshold": 30}]}))
    assert c.n_params == 3


# ---- live strategy_fn_factory ------------------------------------------------
class _FakeBroker:
    IS_PAPER_BROKER = True

    def __init__(self, positions=None):
        self._positions = positions or []

    def get_positions(self):
        return list(self._positions)


def _history_factory(frames):
    def history_fn(symbol):
        return frames.get(symbol)
    return history_fn


def test_factory_emits_only_for_deployed_symbols(df, tmp_path, monkeypatch):
    # force "today" far in the future so all bars are 'closed'
    import agent.strategy_compiler as sc

    class _FixedDT:
        @staticmethod
        def now(tz=None):
            return pd.Timestamp("2099-01-01").to_pydatetime()
    monkeypatch.setattr(sc, "datetime", _FixedDT)

    # spec that always enters (rsi_below 100 → always true), exit never
    spec = {"id": "always", "name": "always", "timeframe": "day",
            "entry": {"all": [{"pred": "rsi_below", "length": 14, "threshold": 50}]},
            "exit": {"any": [{"pred": "rsi_above", "length": 14, "threshold": 95}]},
            "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0}
    c = compile_spec(spec)
    frames = {"AAA": df, "BBB": df, "CCC": df}
    fn = c.strategy_fn_factory(history_fn=_history_factory(frames),
                               deployed_symbols=["AAA"],  # only AAA deployed
                               broker=_FakeBroker(),
                               state_path=tmp_path / "s.json")
    intents = fn({"equity": 100000.0, "available_cash": 100000.0,
                  "price_fn": lambda s: 100.0, "date": "2099-01-02"})
    symbols = {it["symbol"] for it in intents}
    assert symbols <= {"AAA"}            # never BBB/CCC (not deployed)
    assert all(it["action"] == "enter" for it in intents)
    if intents:
        it = intents[0]
        assert it["strategy_id"] == "always"
        assert it["k"] == 2.0 and it["atr"] > 0
        assert "justification" in it and it["justification"]


def test_factory_emits_exit_when_position_open(df, tmp_path, monkeypatch):
    import agent.strategy_compiler as sc

    class _FixedDT:
        @staticmethod
        def now(tz=None):
            return pd.Timestamp("2099-01-01").to_pydatetime()
    monkeypatch.setattr(sc, "datetime", _FixedDT)

    # exit always true → an open position should produce an exit intent
    spec = {"id": "exiter", "name": "exiter", "timeframe": "day",
            "entry": {"all": [{"pred": "rsi_below", "length": 14, "threshold": 10}]},
            "exit": {"any": [{"pred": "rsi_above", "length": 14, "threshold": 50}]},
            "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0}
    c = compile_spec(spec)
    broker = _FakeBroker(positions=[{"tradingsymbol": "AAA", "exchange": "NSE",
                                     "quantity": 10, "average_price": 100.0}])
    fn = c.strategy_fn_factory(history_fn=_history_factory({"AAA": df}),
                               deployed_symbols=["AAA"], broker=broker,
                               state_path=tmp_path / "s.json")
    intents = fn({"equity": 100000.0, "available_cash": 100000.0,
                  "price_fn": lambda s: float(df["close"].iloc[-1]), "date": "2099-01-02"})
    exits = [it for it in intents if it["action"] == "exit"]
    assert len(exits) == 1
    assert exits[0]["symbol"] == "AAA" and exits[0]["quantity"] == 10
