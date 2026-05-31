"""Momentum / oscillator indicators: RSI, MACD, Stochastic, divergence (spec §3.1).

In strong trends RSI stays "overbought" for ages → prefer it for DIVERGENCE rather than
literal thresholds. Divergence (price new high, oscillator not) is among the more respected
signals — momentum fading.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from agent.signals._common import ema, rma


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder smoothing). 0..100."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    # When avg_loss == 0 → RSI 100; when avg_gain == 0 → RSI 0.
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(avg_gain != 0, 0.0)
    return out.rename(f"rsi_{length}")


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD. Returns columns: macd, signal, hist."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def stochastic(df: pd.DataFrame, k_len: int = 14, d_len: int = 3,
               smooth_k: int = 1) -> pd.DataFrame:
    """Stochastic oscillator. Returns columns: k, d (0..100).

    smooth_k=1 → fast stochastic; smooth_k=3 → slow stochastic.
    """
    low_min = df["low"].rolling(window=k_len, min_periods=k_len).min()
    high_max = df["high"].rolling(window=k_len, min_periods=k_len).max()
    raw_k = 100 * (df["close"] - low_min) / (high_max - low_min)
    k = raw_k.rolling(window=smooth_k, min_periods=smooth_k).mean() if smooth_k > 1 else raw_k
    d = k.rolling(window=d_len, min_periods=d_len).mean()
    return pd.DataFrame({"k": k, "d": d})


def _pivot_lows(series: pd.Series, left: int, right: int) -> pd.Series:
    """Boolean: local minimum with `left` lower bars before and `right` after (confirmed)."""
    n = len(series)
    out = pd.Series(False, index=series.index)
    vals = series.to_numpy()
    for i in range(left, n - right):
        window = vals[i - left:i + right + 1]
        if vals[i] == window.min() and (window == vals[i]).sum() == 1:
            out.iloc[i] = True
    return out


def _pivot_highs(series: pd.Series, left: int, right: int) -> pd.Series:
    n = len(series)
    out = pd.Series(False, index=series.index)
    vals = series.to_numpy()
    for i in range(left, n - right):
        window = vals[i - left:i + right + 1]
        if vals[i] == window.max() and (window == vals[i]).sum() == 1:
            out.iloc[i] = True
    return out


def bullish_divergence(close: pd.Series, oscillator: pd.Series,
                       left: int = 3, right: int = 3) -> pd.Series:
    """Boolean (marked at the confirming bar): price makes a LOWER low while the oscillator
    makes a HIGHER low between the two most recent confirmed pivot lows → fading downside
    momentum. Compares consecutive pivot lows.
    """
    piv = _pivot_lows(close, left, right)
    out = pd.Series(False, index=close.index)
    idxs = [i for i, v in enumerate(piv.to_numpy()) if v]
    for a, b in zip(idxs, idxs[1:]):
        if close.iloc[b] < close.iloc[a] and oscillator.iloc[b] > oscillator.iloc[a]:
            out.iloc[b] = True
    return out.rename("bullish_divergence")


def bearish_divergence(close: pd.Series, oscillator: pd.Series,
                       left: int = 3, right: int = 3) -> pd.Series:
    """Boolean: price makes a HIGHER high while the oscillator makes a LOWER high → fading
    upside momentum."""
    piv = _pivot_highs(close, left, right)
    out = pd.Series(False, index=close.index)
    idxs = [i for i, v in enumerate(piv.to_numpy()) if v]
    for a, b in zip(idxs, idxs[1:]):
        if close.iloc[b] > close.iloc[a] and oscillator.iloc[b] < oscillator.iloc[a]:
            out.iloc[b] = True
    return out.rename("bearish_divergence")
