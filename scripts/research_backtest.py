"""Backtest strategy s001 on real Kite daily data, with IS/OOS split and real Zerodha
costs — to decide deploy vs graveyard (spec §6.4).

s001 — RSI mean-reversion, trend-filtered (long-only, daily):
  Entry (long): RSI(14) < 30  AND  close > SMA(200)
  Exit        : RSI(14) > 55  OR  close <= entry_close - atr_k*ATR(14)  OR  time_stop bars
  Params      : rsi_len=14, rsi_entry=30, rsi_exit=55, ma_len=200, atr_k=2.0, time_stop=10

Why a derived exit series: the engine (backtest/engine.py) consumes precomputed boolean
entry/exit series and is the single source of truth for fills, costs, slippage, and
metrics. But s001's ATR stop and time stop are PATH-DEPENDENT (they reference the entry
bar/price), so they can't be a static precomputed series. We therefore derive the `exits`
series with a stateful pass that mirrors the engine's own position logic exactly
(open on the first entry while flat; close on the first exit while long; earliest re-entry
is the bar after an exit). The engine still owns every number that touches money.

Modeling note: the ATR stop level is computed off the entry-signal bar's CLOSE and ATR
(the price/vol known at the moment of the entry decision). The engine fills the entry at
the next bar's open with slippage, so the realized entry price differs marginally from the
stop reference — an accepted, documented approximation for a research backtest.

  python scripts/research_backtest.py            # RELIANCE detail + 10-name robustness
  python scripts/research_backtest.py RELIANCE    # single symbol, detailed
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which can't encode ₹/→ — force UTF-8 output.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import numpy as np
import pandas as pd

from agent.config import load_settings
from agent.broker.kite_client import KiteDataClient
from agent.signals.momentum import rsi
from agent.signals.volatility import atr
from agent.signals._common import sma
from backtest.engine import run_backtest
from backtest.validation import train_test_split, overfit_report

# --- strategy parameters (few by design; OOS is the judge) ---
PARAMS = dict(rsi_len=14, rsi_entry=30.0, rsi_exit=55.0,
              ma_len=200, atr_k=2.0, time_stop=10)
N_PARAMS = 6

# universe selected 2026-05-31 (vault Universe/current-universe.md)
UNIVERSE = ["HDFCBANK", "RELIANCE", "ICICIBANK", "BHARTIARTL", "SBIN",
            "INFY", "TCS", "M&M", "TATASTEEL", "HINDALCO"]

YEARS = 6              # ~6y so that, after the 200-bar SMA warmup, both IS and OOS are meaty
SPLIT = 0.70           # 70% in-sample / 30% out-of-sample, chronological
SLIPPAGE_BPS = 5.0     # adverse slippage per fill


def compute_s001_signals(df: pd.DataFrame, p: dict) -> tuple[pd.Series, pd.Series]:
    """Return (entries, exits) boolean Series for s001, aligned to df.index.

    `exits` is derived with a stateful walk that matches the engine's position semantics,
    so each exit bar pairs with the correct entry (incl. the path-dependent ATR/time stop).
    """
    close = df["close"].astype(float)
    r = rsi(close, p["rsi_len"])
    ma = sma(close, p["ma_len"])
    a = atr(df, p["rsi_len"])  # ATR length tied to rsi_len (14) per design

    entries = (r < p["rsi_entry"]) & (close > ma)
    entries = entries.fillna(False)

    rsi_exit = (r > p["rsi_exit"]).fillna(False)

    close_v = close.to_numpy()
    atr_v = a.to_numpy()
    rsi_exit_v = rsi_exit.to_numpy()
    entries_v = entries.to_numpy()
    n = len(df)

    exits_v = np.zeros(n, dtype=bool)
    i = 0
    while i < n:
        if not entries_v[i]:
            i += 1
            continue
        # entered (long) at bar i; engine fills at i+1 open. Stop level off entry-bar close.
        stop_level = close_v[i] - p["atr_k"] * atr_v[i]
        exit_idx = None
        for j in range(i + 1, n):
            hit = (rsi_exit_v[j]
                   or (not np.isnan(stop_level) and close_v[j] <= stop_level)
                   or (j - i >= p["time_stop"]))
            if hit:
                exit_idx = j
                break
        if exit_idx is None:
            break  # open at end of data — engine closes it at the final close
        exits_v[exit_idx] = True
        i = exit_idx + 1  # earliest re-entry is the bar after the exit (mirrors engine)

    exits = pd.Series(exits_v, index=df.index, name="exits")
    return entries, exits


def fetch_daily(kite: KiteDataClient, symbol: str, years: int) -> pd.DataFrame | None:
    matches = kite.search_instruments(symbol, filter_on="tradingsymbol",
                                       exchange="NSE", limit=20)
    row = next((m for m in matches if m["tradingsymbol"].upper() == symbol.upper()
                and m["exchange"] == "NSE"), None)
    if not row:
        print(f"  ! could not resolve {symbol} on NSE")
        return None
    to_d = date.today()
    from_d = to_d - timedelta(days=int(years * 365.25))
    # Kite caps daily candles at 2000 days/request — fetch in <2000-day windows.
    token = row["instrument_token"]
    candles: list[dict] = []
    win_start = from_d
    while win_start <= to_d:
        win_end = min(win_start + timedelta(days=1900), to_d)
        candles.extend(kite.historical_data(token, str(win_start), str(win_end), "day"))
        win_start = win_end + timedelta(days=1)
    if len(candles) < 300:
        print(f"  ! {symbol}: only {len(candles)} candles")
        return None
    df = pd.DataFrame(candles)
    df.index = pd.to_datetime(df["date"])
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def buy_hold_metrics(df: pd.DataFrame) -> dict:
    close = df["close"]
    total_return = float(close.iloc[-1] / close.iloc[0] - 1)
    rets = close.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else float("nan")
    max_dd = float((close / close.cummax() - 1).min())
    return {"total_return": total_return, "sharpe_like": sharpe, "max_drawdown": max_dd}


def _fmt(m: dict) -> str:
    return (f"ret {m['total_return']:+7.1%} | CAGR {m.get('cagr', float('nan')):+6.1%} | "
            f"Sharpe {m['sharpe_like']:+5.2f} | maxDD {m['max_drawdown']:6.1%} | "
            f"win {m.get('win_rate', float('nan')):5.0%} | trades {int(m.get('trades', 0)):3d} | "
            f"expo {m.get('exposure', float('nan')):4.0%}")


def run_symbol(df: pd.DataFrame, label: str, *, verbose: bool) -> dict:
    entries, exits = compute_s001_signals(df, PARAMS)
    is_df, oos_df = train_test_split(df, SPLIT)

    full = run_backtest(df, entries, exits, slippage_bps=SLIPPAGE_BPS, product="CNC")
    is_res = run_backtest(is_df, entries.reindex(is_df.index),
                          exits.reindex(is_df.index), slippage_bps=SLIPPAGE_BPS, product="CNC")
    oos_res = run_backtest(oos_df, entries.reindex(oos_df.index),
                           exits.reindex(oos_df.index), slippage_bps=SLIPPAGE_BPS, product="CNC")
    rep = overfit_report(is_res.metrics, oos_res.metrics, n_params=N_PARAMS)
    bh = buy_hold_metrics(df)

    if verbose:
        span = f"{df.index[0].date()} → {df.index[-1].date()} ({len(df)} bars)"
        print(f"\n{'='*78}\n  {label}  —  {span}\n{'='*78}")
        print(f"  FULL     : {_fmt(full.metrics)}")
        print(f"  IN-SAMPLE: {_fmt(is_res.metrics)}   [{is_df.index[0].date()}→{is_df.index[-1].date()}]")
        print(f"  OUT-SAMPL: {_fmt(oos_res.metrics)}   [{oos_df.index[0].date()}→{oos_df.index[-1].date()}]")
        print(f"  BUY&HOLD : ret {bh['total_return']:+7.1%} | "
              f"Sharpe {bh['sharpe_like']:+5.2f} | maxDD {bh['max_drawdown']:6.1%}")
        verdict = "REJECT → graveyard" if rep.rejected else "survives OOS gate"
        print(f"  OVERFIT  : flags={rep.flags or 'none'}  →  {verdict}")
        if full.trades:
            tdf = full.trades_df
            print(f"  trade pnl: total ₹{tdf['pnl'].sum():,.0f} | "
                  f"avg ₹{tdf['pnl'].mean():,.0f} | best ₹{tdf['pnl'].max():,.0f} | "
                  f"worst ₹{tdf['pnl'].min():,.0f} | avg bars held {tdf['bars_held'].mean():.1f}")

    return {"label": label, "full": full.metrics, "is": is_res.metrics,
            "oos": oos_res.metrics, "bh": bh, "report": rep}


def main(symbol: str | None = None) -> int:
    s = load_settings()
    if not (s.kite_api_key and s.kite_access_token):
        print("Missing creds. Run scripts/kite_login.py first.")
        return 1
    kite = KiteDataClient(api_key=s.kite_api_key, access_token=s.kite_access_token)

    print(f"\n### s001 backtest — RSI({PARAMS['rsi_len']})<{PARAMS['rsi_entry']:.0f} & "
          f"close>SMA({PARAMS['ma_len']}); exit RSI>{PARAMS['rsi_exit']:.0f} | "
          f"{PARAMS['atr_k']}xATR stop | {PARAMS['time_stop']}-bar time stop")
    print(f"### costs: Zerodha CNC (verified 2026-05-31) + {SLIPPAGE_BPS:.0f}bps slippage | "
          f"IS/OOS {SPLIT:.0%}/{1-SPLIT:.0%} chronological\n")

    if symbol:
        df = fetch_daily(kite, symbol, YEARS)
        if df is None:
            return 1
        run_symbol(df, symbol.upper(), verbose=True)
        return 0

    # RELIANCE in detail, then robustness across the full universe
    primary = "RELIANCE"
    df = fetch_daily(kite, primary, YEARS)
    if df is not None:
        run_symbol(df, primary, verbose=True)

    print(f"\n{'='*78}\n  ROBUSTNESS — same rules, no per-name tuning (generalize or overfit?)\n{'='*78}")
    print(f"  {'symbol':<10} {'OOS ret':>8} {'OOS Shp':>8} {'OOS trd':>8} "
          f"{'IS Shp':>7} {'verdict':>20}")
    rows = []
    for sym in UNIVERSE:
        d = df if sym == primary else fetch_daily(kite, sym, YEARS)
        if d is None:
            continue
        res = run_symbol(d, sym, verbose=False)
        rows.append(res)
        oos, is_m, rep = res["oos"], res["is"], res["report"]
        verdict = "REJECT" if rep.rejected else "survives"
        print(f"  {sym:<10} {oos['total_return']:>+8.1%} {oos['sharpe_like']:>+8.2f} "
              f"{int(oos['trades']):>8d} {is_m['sharpe_like']:>+7.2f} {verdict:>20}")

    survivors = [r["label"] for r in rows if not r["report"].rejected]
    print(f"\n  survives OOS gate: {survivors or 'NONE'}  ({len(survivors)}/{len(rows)})")
    print("  (Few/zero survivors with this rare-firing rule is the expected, honest outcome.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
