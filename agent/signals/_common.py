"""Shared primitives for indicators (spec §3).

All indicators are PURE functions over price data — no hidden state, no I/O. Parameters are
explicit so a backtest can reproduce a rule exactly (spec §3.2). Input convention: a pandas
DataFrame with lowercase columns {open, high, low, close, volume} and a datetime index, or a
pandas Series of prices where noted.
"""
from __future__ import annotations

import pandas as pd

OHLC = ("open", "high", "low", "close")
OHLCV = OHLC + ("volume",)


def require_columns(df: pd.DataFrame, cols=OHLC) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"DataFrame missing required columns: {missing}")


def sma(series: pd.Series, length: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=length, min_periods=length).mean().rename(f"sma_{length}")


def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential moving average (span-based, the standard MACD/MA flavour)."""
    return series.ewm(span=length, adjust=False).mean().rename(f"ema_{length}")


def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothed moving average (RMA) — used by RSI/ATR/ADX.

    Equivalent to an EMA with alpha = 1/length.
    """
    return series.ewm(alpha=1.0 / length, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range = max(high-low, |high-prev_close|, |low-prev_close|)."""
    require_columns(df, ("high", "low", "close"))
    prev_close = df["close"].shift(1)
    hl = df["high"] - df["low"]
    hc = (df["high"] - prev_close).abs()
    lc = (df["low"] - prev_close).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).rename("true_range")
