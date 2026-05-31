"""Candlestick patterns (spec §3.1 Patterns): doji, hammer, engulfing.

Short-term reversal / indecision hints. Parameterized thresholds — no magic constants
baked in (spec §3.2).
"""
from __future__ import annotations

import pandas as pd

from agent.signals._common import require_columns


def _body(df: pd.DataFrame) -> pd.Series:
    return (df["close"] - df["open"]).abs()


def _range(df: pd.DataFrame) -> pd.Series:
    return (df["high"] - df["low"])


def doji(df: pd.DataFrame, body_frac: float = 0.1) -> pd.Series:
    """Boolean: body <= body_frac × range (indecision). Excludes zero-range bars."""
    require_columns(df)
    rng = _range(df)
    return ((rng > 0) & (_body(df) <= body_frac * rng)).rename("doji")


def hammer(df: pd.DataFrame, body_frac: float = 0.35,
           lower_wick_mult: float = 2.0, upper_wick_frac: float = 0.25) -> pd.Series:
    """Boolean: small body, long lower shadow (>= lower_wick_mult × body), small upper
    shadow — a bullish reversal hint."""
    require_columns(df)
    rng = _range(df)
    body = _body(df)
    upper = df["high"] - df[["open", "close"]].max(axis=1)
    lower = df[["open", "close"]].min(axis=1) - df["low"]
    return (
        (rng > 0)
        & (body <= body_frac * rng)
        & (lower >= lower_wick_mult * body)
        & (upper <= upper_wick_frac * rng)
    ).rename("hammer")


def bullish_engulfing(df: pd.DataFrame) -> pd.Series:
    """Boolean: prior candle bearish, current candle bullish and its body fully engulfs the
    prior body."""
    require_columns(df)
    o, c = df["open"], df["close"]
    po, pc = o.shift(1), c.shift(1)
    prev_bear = pc < po
    curr_bull = c > o
    engulf = (o <= pc) & (c >= po)
    return (prev_bear & curr_bull & engulf).rename("bullish_engulfing")


def bearish_engulfing(df: pd.DataFrame) -> pd.Series:
    """Boolean: prior candle bullish, current candle bearish and its body fully engulfs the
    prior body."""
    require_columns(df)
    o, c = df["open"], df["close"]
    po, pc = o.shift(1), c.shift(1)
    prev_bull = pc > po
    curr_bear = c < o
    engulf = (o >= pc) & (c <= po)
    return (prev_bull & curr_bear & engulf).rename("bearish_engulfing")
