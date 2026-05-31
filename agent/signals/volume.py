"""Volume indicators: OBV, VWAP, volume confirmation/spikes (spec §3.1 Volume).

A breakout on high volume beats one on thin volume; volume spikes can mark exhaustion.
OBV is cumulative volume flow; VWAP is an intraday/session reference level.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from agent.signals._common import sma, require_columns


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume. +volume on up-closes, -volume on down-closes, 0 on flat;
    cumulative, seeded at 0."""
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum().rename("obv")


def vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-Weighted Average Price, cumulative over the given frame.

    Note: VWAP is conventionally session-anchored (resets each day). As a pure function over
    one frame it accumulates across the frame — callers wanting an intraday VWAP should pass
    a single session's bars (or reset per day upstream).
    """
    require_columns(df, ("high", "low", "close", "volume"))
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_vol = df["volume"].cumsum()
    return ((typical * df["volume"]).cumsum() / cum_vol).rename("vwap")


def volume_spike(volume: pd.Series, length: int = 20, k: float = 2.0) -> pd.Series:
    """Boolean: volume exceeds k × its `length`-bar average (a spike/climax candidate)."""
    avg = sma(volume, length)
    return (volume > k * avg).rename("volume_spike")


def volume_confirms(volume: pd.Series, length: int = 20) -> pd.Series:
    """Boolean: volume above its `length`-bar average (confirms a move)."""
    return (volume > sma(volume, length)).rename("volume_confirms")
