"""Volatility indicators: ATR, Bollinger Bands (spec §3.1 Volatility).

ATR is used for sizing and stops (spec §4), NOT entries. Bollinger band touches/squeezes
are entry-relevant.
"""
from __future__ import annotations

import pandas as pd

from agent.signals._common import sma, rma, true_range, require_columns


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Average True Range (Wilder). Anchors stop distance = k×ATR (spec §4.1)."""
    return rma(true_range(df), length).rename(f"atr_{length}")


def bollinger_bands(close: pd.Series, length: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands. Returns columns: mid, upper, lower, bandwidth, pct_b.

    Uses population std (ddof=0), the conventional Bollinger definition.
    """
    mid = sma(close, length)
    sd = close.rolling(window=length, min_periods=length).std(ddof=0)
    upper = mid + k * sd
    lower = mid - k * sd
    bandwidth = (upper - lower) / mid
    pct_b = (close - lower) / (upper - lower)
    return pd.DataFrame({
        "mid": mid, "upper": upper, "lower": lower,
        "bandwidth": bandwidth, "pct_b": pct_b,
    })


def bollinger_squeeze(close: pd.Series, length: int = 20, k: float = 2.0,
                      lookback: int = 120) -> pd.Series:
    """Boolean: bandwidth at its narrowest over `lookback` (a 'squeeze' precedes expansion)."""
    bb = bollinger_bands(close, length, k)
    bw = bb["bandwidth"]
    min_bw = bw.rolling(window=lookback, min_periods=length).min()
    return (bw <= min_bw).rename("bollinger_squeeze")
