"""Backtest engine (spec §6.4.1).

Flow: OHLCV candles + boolean entry/exit series → simulate long positions → equity curve
and trade list, with realistic costs (backtest/costs.py) and slippage applied to fills.

Long-only, single instrument, one position at a time (the building block; portfolio-level
combination is the strategy layer). Signals are evaluated at bar i and FILLED at bar i+1's
open by default (`fill='next_open'`) to avoid look-ahead bias; `fill='close'` fills same-bar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from backtest.costs import CostModel, BUY, SELL

ANNUALIZATION = 252  # trading days/year for the Sharpe-like ratio


@dataclass
class Trade:
    entry_date: Any
    exit_date: Any
    entry_price: float
    exit_price: float
    quantity: int
    charges: float
    pnl: float          # net of charges
    pnl_pct: float      # net pnl / entry notional
    bars_held: int
    reason: str         # 'signal' | 'end-of-data'


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list[Trade]
    metrics: dict[str, float]
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def trades_df(self) -> pd.DataFrame:
        return pd.DataFrame([t.__dict__ for t in self.trades])


def _metrics(equity: pd.Series, trades: list[Trade], bars_in_market: int,
             n_bars: int) -> dict[str, float]:
    initial, final = float(equity.iloc[0]), float(equity.iloc[-1])
    total_return = final / initial - 1.0

    # CAGR from the actual calendar span of the equity curve
    days = max((equity.index[-1] - equity.index[0]).days, 1)
    years = days / 365.25
    cagr = (final / initial) ** (1 / years) - 1.0 if final > 0 and years > 0 else float("nan")

    rets = equity.pct_change().dropna()
    sharpe = (float(rets.mean()) / float(rets.std()) * np.sqrt(ANNUALIZATION)
              if rets.std() > 0 else float("nan"))

    running_max = equity.cummax()
    max_dd = float((equity / running_max - 1.0).min())

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    win_rate = len(wins) / len(trades) if trades else float("nan")
    avg_win = float(np.mean([t.pnl for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([t.pnl for t in losses])) if losses else 0.0

    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "sharpe_like": sharpe,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "trades": float(len(trades)),
        "exposure": bars_in_market / n_bars if n_bars else 0.0,
    }


def run_backtest(df: pd.DataFrame,
                 entries: pd.Series,
                 exits: pd.Series,
                 *,
                 initial_cash: float = 100000.0,
                 cost_model: CostModel | None = None,
                 slippage_bps: float = 5.0,
                 fill: str = "next_open",
                 size_fraction: float = 1.0,
                 product: str = "CNC",
                 exchange: str = "NSE") -> BacktestResult:
    """Run a long-only backtest. `entries`/`exits` are boolean Series aligned to `df`.

    A position opens on the first entry while flat and closes on the first exit while long.
    Fills use next-bar open (default) with `slippage_bps` adverse slippage. Any open
    position at the end is closed at the last close ('end-of-data') for clean accounting.
    """
    if fill not in ("next_open", "close"):
        raise ValueError("fill must be 'next_open' or 'close'")
    cost_model = cost_model or CostModel()
    entries = entries.reindex(df.index).fillna(False).to_numpy()
    exits = exits.reindex(df.index).fillna(False).to_numpy()
    closes = df["close"].to_numpy(dtype=float)
    fill_prices = (df["open"].to_numpy(dtype=float) if fill == "next_open"
                   else df["close"].to_numpy(dtype=float))
    offset = 1 if fill == "next_open" else 0
    n = len(df)
    slip = slippage_bps / 10000.0

    cash = float(initial_cash)
    qty = 0
    entry_price = 0.0
    entry_idx = -1
    entry_charge = 0.0
    trades: list[Trade] = []
    equity = np.empty(n, dtype=float)
    bars_in_market = 0

    def buy_fill(p):  return p * (1 + slip)
    def sell_fill(p): return p * (1 - slip)

    for i in range(n):
        # --- act on signals from bar i, filling at i+offset ---
        j = i + offset
        if qty == 0 and entries[i] and j < n:
            price = buy_fill(fill_prices[j])
            shares = int((cash * size_fraction) // price)
            if shares > 0:
                ch = cost_model.charge(BUY, product, exchange, shares, price)
                qty = shares
                entry_price = price
                entry_idx = i
                entry_charge = ch.total
                cash -= shares * price + ch.total
        elif qty > 0 and exits[i] and j < n:
            price = sell_fill(fill_prices[j])
            ch = cost_model.charge(SELL, product, exchange, qty, price)
            proceeds = qty * price - ch.total
            cash += proceeds
            total_charge = entry_charge + ch.total
            pnl = (price - entry_price) * qty - total_charge
            trades.append(Trade(
                entry_date=df.index[entry_idx], exit_date=df.index[i],
                entry_price=entry_price, exit_price=price, quantity=qty,
                charges=total_charge, pnl=pnl,
                pnl_pct=pnl / (entry_price * qty), bars_held=i - entry_idx,
                reason="signal",
            ))
            qty = 0

        if qty > 0:
            bars_in_market += 1
        # mark-to-market at this bar's close
        equity[i] = cash + qty * closes[i]

    # close any open position at the final close
    if qty > 0:
        price = sell_fill(closes[-1])
        ch = cost_model.charge(SELL, product, exchange, qty, price)
        cash += qty * price - ch.total
        total_charge = entry_charge + ch.total
        pnl = (price - entry_price) * qty - total_charge
        trades.append(Trade(
            entry_date=df.index[entry_idx], exit_date=df.index[-1],
            entry_price=entry_price, exit_price=price, quantity=qty,
            charges=total_charge, pnl=pnl, pnl_pct=pnl / (entry_price * qty),
            bars_held=(n - 1) - entry_idx, reason="end-of-data",
        ))
        equity[-1] = cash
        qty = 0

    equity_curve = pd.Series(equity, index=df.index, name="equity")
    metrics = _metrics(equity_curve, trades, bars_in_market, n)
    return BacktestResult(
        equity_curve=equity_curve, trades=trades, metrics=metrics,
        params={"initial_cash": initial_cash, "slippage_bps": slippage_bps,
                "fill": fill, "size_fraction": size_fraction,
                "product": product, "exchange": exchange},
    )
