"""Phase 3: verify each indicator against a known-good / independent reference.

Strategy: re-derive each indicator a DIFFERENT way in the test (or use an analytically
known input) so a bug in the implementation can't hide behind a matching bug in the test.
"""
import numpy as np
import pandas as pd
import pytest

from agent.signals._common import sma, ema, rma, true_range
from agent.signals import volatility, trend, momentum, volume, structure, patterns


def make_df(highs, lows, opens, closes, volumes=None):
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": volumes if volumes is not None else [1000] * n,
    }, index=idx)


# ---------------- _common ----------------
def test_sma_matches_manual():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    res = sma(s, 3)
    assert np.isnan(res.iloc[0]) and np.isnan(res.iloc[1])
    assert res.iloc[2] == 2.0 and res.iloc[3] == 3.0 and res.iloc[4] == 4.0


def test_ema_recurrence():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    res = ema(s, 3)  # alpha = 2/(3+1) = 0.5, adjust=False, seed = first value
    alpha = 2 / (3 + 1)
    expected = [1.0]
    for x in s.iloc[1:]:
        expected.append(alpha * x + (1 - alpha) * expected[-1])
    assert res.tolist() == pytest.approx(expected)


def test_rma_equals_ewm_alpha():
    s = pd.Series(np.arange(1, 21), dtype=float)
    res = rma(s, 5)
    ref = s.ewm(alpha=1 / 5, adjust=False).mean()
    pd.testing.assert_series_equal(res, ref, check_names=False)


def test_true_range_manual():
    df = make_df(highs=[10, 12], lows=[8, 9], opens=[9, 11], closes=[9, 11])
    tr = true_range(df)
    # bar0: high-low=2 (no prev close). bar1: max(12-9=3, |12-9|=3, |9-9|=0)=3
    assert tr.iloc[0] == 2.0
    assert tr.iloc[1] == 3.0


# ---------------- volatility ----------------
def test_atr_constant_range_converges():
    n = 30
    df = make_df(highs=[102] * n, lows=[100] * n, opens=[101] * n, closes=[101] * n)
    a = volatility.atr(df, 14)
    # Every TR = 2 → ATR = 2 everywhere.
    assert a.iloc[-1] == pytest.approx(2.0)


def test_bollinger_definition():
    close = pd.Series(np.linspace(100, 120, 40))
    bb = volatility.bollinger_bands(close, 20, 2.0)
    mid_ref = close.rolling(20).mean()
    sd_ref = close.rolling(20).std(ddof=0)
    pd.testing.assert_series_equal(bb["mid"], mid_ref, check_names=False)
    pd.testing.assert_series_equal(bb["upper"], (mid_ref + 2 * sd_ref), check_names=False)
    pd.testing.assert_series_equal(bb["lower"], (mid_ref - 2 * sd_ref), check_names=False)


# ---------------- momentum ----------------
def test_rsi_extremes():
    up = pd.Series(np.arange(1, 30), dtype=float)
    down = pd.Series(np.arange(30, 1, -1), dtype=float)
    assert momentum.rsi(up, 14).iloc[-1] == pytest.approx(100.0)
    assert momentum.rsi(down, 14).iloc[-1] == pytest.approx(0.0)


def test_rsi_transform_consistency():
    rng = np.random.default_rng(0)
    close = pd.Series(100 + np.cumsum(rng.standard_normal(200)))
    r = momentum.rsi(close, 14)
    # Independent re-derivation of the SAME transform: 100*ag/(ag+al)
    delta = close.diff()
    ag = rma(delta.clip(lower=0), 14)
    al = rma((-delta).clip(lower=0), 14)
    ref = 100 * ag / (ag + al)
    pd.testing.assert_series_equal(r, ref, check_names=False)
    assert r.dropna().between(0, 100).all()


def test_macd_definition():
    close = pd.Series(100 + np.cumsum(np.sin(np.arange(100))))
    m = momentum.macd(close, 12, 26, 9)
    macd_ref = ema(close, 12) - ema(close, 26)
    pd.testing.assert_series_equal(m["macd"], macd_ref, check_names=False)
    pd.testing.assert_series_equal(m["signal"], ema(macd_ref, 9), check_names=False)
    pd.testing.assert_series_equal(m["hist"], (macd_ref - ema(macd_ref, 9)), check_names=False)


def test_stochastic_formula():
    df = make_df(highs=[10, 11, 12, 13, 14], lows=[5, 6, 7, 8, 9],
                 opens=[7, 8, 9, 10, 11], closes=[9, 10, 11, 12, 13])
    st = momentum.stochastic(df, k_len=3, d_len=2)
    # last bar: low_min over [12,13,14]→ lows [7,8,9]→7; high_max→14; close 13
    expected_k = 100 * (13 - 7) / (14 - 7)
    assert st["k"].iloc[-1] == pytest.approx(expected_k)


def test_bullish_divergence_detected():
    # price: lower low on the 2nd trough; oscillator: higher low on the 2nd trough
    close = pd.Series([10, 8, 10, 12, 9, 7, 9, 11], dtype=float)  # troughs at idx1(8) & idx5(7) → lower low
    osc = pd.Series([50, 30, 50, 60, 45, 35, 55, 65], dtype=float)  # troughs 30 then 35 → higher low
    div = momentum.bullish_divergence(close, osc, left=1, right=1)
    assert div.iloc[5]  # confirmed at the second (lower) trough


# ---------------- volume ----------------
def test_obv_manual():
    close = pd.Series([10, 11, 10, 10, 12], dtype=float)
    vol = pd.Series([100, 200, 300, 400, 500], dtype=float)
    o = volume.obv(close, vol)
    assert o.tolist() == pytest.approx([0, 200, -100, -100, 400])


def test_vwap_manual():
    df = make_df(highs=[11, 13], lows=[9, 11], opens=[10, 12], closes=[10, 12],
                 volumes=[100, 300])
    v = volume.vwap(df)
    tp0 = (11 + 9 + 10) / 3      # 10
    tp1 = (13 + 11 + 12) / 3     # 12
    assert v.iloc[0] == pytest.approx(tp0)
    assert v.iloc[1] == pytest.approx((tp0 * 100 + tp1 * 300) / 400)


def test_volume_spike():
    vol = pd.Series([100] * 20 + [500])
    spike = volume.volume_spike(vol, 20, 2.0)
    assert spike.iloc[-1]
    assert not spike.iloc[-2]


# ---------------- trend ----------------
def test_adx_strong_uptrend_high():
    n = 60
    base = np.arange(n, dtype=float)
    df = make_df(highs=base + 2, lows=base, opens=base + 0.5, closes=base + 1.5)
    a = trend.adx(df, 14)
    assert a["adx"].iloc[-1] > 40
    assert a["plus_di"].iloc[-1] > a["minus_di"].iloc[-1]


def test_ma_crossover_golden_cross():
    # flat-then-rising series produces a fast-over-slow cross
    close = pd.Series(list(np.full(50, 100.0)) + list(np.linspace(100, 200, 50)))
    x = trend.ma_crossover(close, 5, 20, "sma")
    assert x["golden_cross"].any()
    assert not x["death_cross"].iloc[60:].any()


# ---------------- structure ----------------
def test_breakout_up():
    highs = [10] * 20 + [9, 15]
    df = make_df(highs=highs, lows=[h - 2 for h in highs],
                 opens=highs, closes=[10] * 20 + [9, 15])
    b = structure.breakout_up(df, 20)
    assert b.iloc[-1]       # close 15 > prior 20-bar high (10)
    assert not b.iloc[-2]   # close 9 does not break out


# ---------------- patterns ----------------
def test_doji():
    df = make_df(highs=[110], lows=[90], opens=[100], closes=[100.5])
    assert patterns.doji(df, body_frac=0.1).iloc[0]


def test_hammer():
    # small body near top, long lower shadow
    df = make_df(highs=[101], lows=[90], opens=[100], closes=[100.5])
    assert patterns.hammer(df).iloc[0]


def test_bullish_engulfing():
    df = make_df(highs=[11, 12], lows=[8, 7], opens=[10, 8], closes=[9, 11])
    # bar0 bearish (10→9); bar1 bullish (8→11) engulfs prior body [9,10]
    assert patterns.bullish_engulfing(df).iloc[1]


def test_reproducible_from_params():
    rng = np.random.default_rng(42)
    close = pd.Series(100 + np.cumsum(rng.standard_normal(100)))
    a = momentum.rsi(close, 14)
    b = momentum.rsi(close, 14)
    pd.testing.assert_series_equal(a, b)
