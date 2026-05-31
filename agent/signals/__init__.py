"""Signal library: pure, parameterized indicator/signal functions over OHLCV data (spec §3).

No trading logic here — just signal computation. Ensemble/combination logic lives in
agent/strategy.py (Phase later). Import families directly, e.g.:

    from agent.signals import momentum, trend, volatility
    rsi14 = momentum.rsi(df["close"], 14)
"""
from agent.signals import trend, momentum, volume, volatility, structure, patterns
from agent.signals._common import sma, ema, rma, true_range

__all__ = [
    "trend", "momentum", "volume", "volatility", "structure", "patterns",
    "sma", "ema", "rma", "true_range",
]
