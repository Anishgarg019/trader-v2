"""Trend indicators & signals: SMA/EMA bias, MA crossovers, ADX (spec §3.1 Trend).

ADX measures trend STRENGTH, not direction — used to gate other signals (most behave
differently in trending vs choppy regimes). MA crosses are classic but lagging.
"""
from __future__ import annotations

import pandas as pd

from agent.signals._common import sma, ema, rma, true_range, require_columns


def adx(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    """Average Directional Index (Wilder). Returns columns: plus_di, minus_di, adx."""
    require_columns(df, ("high", "low", "close"))
    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move.clip(lower=0)
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move.clip(lower=0)

    atr_ = rma(true_range(df), length)
    plus_di = 100 * rma(plus_dm, length) / atr_
    minus_di = 100 * rma(minus_dm, length) / atr_

    di_sum = (plus_di + minus_di).replace(0, pd.NA)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx_ = rma(dx.astype(float), length)
    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "adx": adx_})


def cross_up(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """Boolean: `fast` crosses ABOVE `slow` this bar (was <=, now >)."""
    prev = (fast.shift(1) <= slow.shift(1))
    now = (fast > slow)
    return (prev & now).rename("cross_up")


def cross_down(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """Boolean: `fast` crosses BELOW `slow` this bar (was >=, now <)."""
    prev = (fast.shift(1) >= slow.shift(1))
    now = (fast < slow)
    return (prev & now).rename("cross_down")


def ma_crossover(close: pd.Series, fast_len: int = 50, slow_len: int = 200,
                 kind: str = "sma") -> pd.DataFrame:
    """Golden/death cross of two MAs. Returns fast, slow, golden_cross, death_cross."""
    ma = sma if kind == "sma" else ema
    fast = ma(close, fast_len)
    slow = ma(close, slow_len)
    return pd.DataFrame({
        "fast": fast, "slow": slow,
        "golden_cross": cross_up(fast, slow),
        "death_cross": cross_down(fast, slow),
    })


def price_above_ma(close: pd.Series, length: int = 200, kind: str = "sma") -> pd.Series:
    """Directional bias: close above a (long) MA → bullish regime filter."""
    ma = sma if kind == "sma" else ema
    return (close > ma(close, length)).rename(f"above_{kind}_{length}")
