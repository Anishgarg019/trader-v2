"""Structure: support/resistance, breakouts, higher-highs/lower-lows (spec §3.1).

The grammar of trend: higher-highs/higher-lows (uptrend) and the inverse. Breakouts clear a
level (often retested as new S/R).
"""
from __future__ import annotations

import pandas as pd


def pivot_highs(high: pd.Series, left: int = 3, right: int = 3) -> pd.Series:
    """Boolean: confirmed swing high (strictly highest in the [-left, +right] window)."""
    vals = high.to_numpy()
    out = pd.Series(False, index=high.index)
    for i in range(left, len(vals) - right):
        w = vals[i - left:i + right + 1]
        if vals[i] == w.max() and (w == vals[i]).sum() == 1:
            out.iloc[i] = True
    return out.rename("pivot_high")


def pivot_lows(low: pd.Series, left: int = 3, right: int = 3) -> pd.Series:
    """Boolean: confirmed swing low (strictly lowest in the [-left, +right] window)."""
    vals = low.to_numpy()
    out = pd.Series(False, index=low.index)
    for i in range(left, len(vals) - right):
        w = vals[i - left:i + right + 1]
        if vals[i] == w.min() and (w == vals[i]).sum() == 1:
            out.iloc[i] = True
    return out.rename("pivot_low")


def breakout_up(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """Boolean: close breaks above the highest high of the PRIOR `length` bars."""
    prior_high = df["high"].shift(1).rolling(window=length, min_periods=length).max()
    return (df["close"] > prior_high).rename("breakout_up")


def breakout_down(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """Boolean: close breaks below the lowest low of the PRIOR `length` bars."""
    prior_low = df["low"].shift(1).rolling(window=length, min_periods=length).min()
    return (df["close"] < prior_low).rename("breakout_down")


def higher_highs(high: pd.Series, length: int = 20) -> pd.Series:
    """Boolean: current high is the highest over the trailing `length` window (new high)."""
    roll_max = high.rolling(window=length, min_periods=length).max()
    return (high >= roll_max).rename("higher_high")


def lower_lows(low: pd.Series, length: int = 20) -> pd.Series:
    """Boolean: current low is the lowest over the trailing `length` window (new low)."""
    roll_min = low.rolling(window=length, min_periods=length).min()
    return (low <= roll_min).rename("lower_low")
