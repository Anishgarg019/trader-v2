"""Profile one stock's character on real daily history, to inform strategy design (spec §6.4).

Pulls ~5 years of daily candles and reports: returns/vol, trendiness (ADX), mean-reversion
(return autocorrelation + behaviour after RSI extremes), and regime stats. This tells us
WHICH kind of strategy fits — we keep params few and let OOS validation be the real judge.

  python scripts/study_stock.py [SYMBOL]      # default RELIANCE
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent.config import load_settings
from agent.broker.kite_client import KiteDataClient
from agent.signals import momentum, trend, volatility
from agent.signals._common import sma


def main(symbol: str = "RELIANCE", years: int = 5) -> int:
    s = load_settings()
    if not (s.kite_api_key and s.kite_access_token):
        print("Missing creds. Run scripts/kite_login.py first.")
        return 1
    kite = KiteDataClient(api_key=s.kite_api_key, access_token=s.kite_access_token)

    matches = kite.search_instruments(symbol, filter_on="tradingsymbol", exchange="NSE", limit=10)
    row = next((m for m in matches if m["tradingsymbol"].upper() == symbol.upper()
                and m["exchange"] == "NSE"), None)
    if not row:
        print(f"Could not resolve {symbol} on NSE.")
        return 1
    token = row["instrument_token"]

    to_d = date.today()
    from_d = to_d - timedelta(days=int(years * 365.25))
    candles = kite.historical_data(token, str(from_d), str(to_d), "day")
    if len(candles) < 250:
        print(f"Only {len(candles)} candles — need more history.")
        return 1

    df = pd.DataFrame(candles)
    close = df["close"].astype(float)
    ret = close.pct_change()
    n = len(df)

    ann_return = (close.iloc[-1] / close.iloc[0]) ** (252 / n) - 1
    ann_vol = ret.std() * np.sqrt(252)
    atr_pct_median = (volatility.atr(df, 14) / close).median()
    adx = trend.adx(df, 14)["adx"]
    pct_trending = float((adx > 25).mean())
    autocorr1 = float(ret.autocorr(lag=1))
    pct_above_sma200 = float((close > sma(close, 200)).mean())
    max_dd = float((close / close.cummax() - 1).min())

    rsi = momentum.rsi(close, 14)
    fwd5 = close.shift(-5) / close - 1
    oversold_fwd5 = float(fwd5[rsi < 30].mean())
    overbought_fwd5 = float(fwd5[rsi > 70].mean())
    n_oversold = int((rsi < 30).sum())
    n_overbought = int((rsi > 70).sum())

    print(f"\n=== {symbol} — {n} daily bars, {df['date'].iloc[0]} → {df['date'].iloc[-1]} ===")
    print(f"  Annualized return     : {ann_return:+.1%}")
    print(f"  Annualized volatility : {ann_vol:.1%}")
    print(f"  Median ATR%           : {atr_pct_median:.2%}")
    print(f"  Buy&hold max drawdown : {max_dd:.1%}")
    print(f"  % days ADX>25 (trend) : {pct_trending:.0%}")
    print(f"  % time above SMA(200) : {pct_above_sma200:.0%}")
    print(f"  Return autocorr (lag1): {autocorr1:+.3f}  (negative ⇒ mean-reverting)")
    print(f"  Avg 5d return after RSI<30 (oversold, n={n_oversold}): {oversold_fwd5:+.2%}")
    print(f"  Avg 5d return after RSI>70 (overbought, n={n_overbought}): {overbought_fwd5:+.2%}")

    print("\n--- quick character read ---")
    reads = []
    reads.append("trendy" if pct_trending > 0.30 else "choppy/range-y")
    reads.append("mean-reverting (oversold bounces)" if oversold_fwd5 > 0.005
                 else "no clear oversold bounce")
    reads.append("momentum-ish (autocorr≥0)" if autocorr1 >= 0 else "reversal-ish (autocorr<0)")
    print("  " + " · ".join(reads))
    print("(Paste this whole block back to design the strategy.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"))
