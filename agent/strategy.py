"""Strategy registry — turns market data into enter/exit intents for the Orchestrator.

A `strategy_fn(ctx) -> list[intent]` is what `Orchestrator._market` consumes (agent/loop.py).
`ctx` carries {equity, available_cash, governor, universe, price_fn, date}; the data access
(Kite read-only) and the paper book (PaperBroker) are captured in the closure built here.

s001 — RSI mean-reversion, trend-filtered (long-only, daily). Backtested in
scripts/research_backtest.py and GRAVEYARD'd (the entry conditions co-occur ~once in 5y, so
it almost never trades). It is wired here as a **forward-test** to exercise the live paper
pipeline end-to-end (signal → loop → PaperBroker → stop → trade note → dashboard), NOT as a
proven edge. Expect it to sit flat almost always. See the strategy note for the kill record.

Daily-bar discipline: signals are evaluated on the last CLOSED daily bar (any forming
candle dated today is dropped), and acted on today at the live LTP — mirroring the
backtest's "signal at bar i, fill next bar" with no look-ahead.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from agent.signals.momentum import rsi
from agent.signals.volatility import atr
from agent.signals._common import sma
from agent.trading_day import IST

STRATEGY_ID = "s001"
STRATEGY_NOTE = "Strategies/s001 - RSI mean-reversion trend-filtered (long-only daily).md"

PARAMS = dict(rsi_len=14, rsi_entry=30.0, rsi_exit=55.0,
              ma_len=200, atr_k=2.0, time_stop=10)

# --- Phase 11 re-expression (RESEARCHER-SPEC §9) -----------------------------
# s001 as a DSL spec. The compiled spec REPLACES build_s001_strategy_fn below (now retired;
# kept only for the equivalence test in tests/test_s001_spec.py). The compiler reproduces the
# RSI-entry / trend-filter / RSI-exit signal logic exactly; the time-stop is intentionally
# dropped (not expressible in the DSL), and the ATR protective stop (atr_k×ATR) is handled
# uniformly by the risk engine + the compiled factory — same as the hand-written version did.
# n_params = rsi-entry threshold + rsi-exit threshold + atr_k = 3. Stays status: forward-test
# (failed OOS 0/10 — pipeline proof, not edge). deployed_symbols = the live universe so it
# keeps exercising the paper pipeline exactly as the hand-written version did.
S001_SPEC = {
    "id": "s001",
    "name": "RSI mean-reversion trend-filtered (long-only daily)",
    "families": ["mean-reversion", "trend-filter"],
    "timeframe": "day",
    "thesis": ("Oversold bounce (RSI(14)<30) gated by a long-term uptrend (close>SMA200) to "
               "avoid catching falling knives. Long-only; exit when RSI recovers past 55."),
    "entry": {"all": [
        {"pred": "rsi_below", "length": 14, "threshold": 30},
        {"pred": "price_above_ma", "length": 200, "kind": "sma"},
    ]},
    "exit": {"any": [{"pred": "rsi_above", "length": 14, "threshold": 55}]},
    "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0,
}

_LOOKBACK_DAYS = 500  # calendar days → ~340 trading bars (comfortably > ma_len=200)


def _bar_dates(df: pd.DataFrame) -> pd.DatetimeIndex:
    """Index normalized to tz-naive calendar dates (Kite candles are tz-aware IST)."""
    idx = df.index
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def _load_state(path: Path) -> dict[str, dict]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(path: Path, state: dict[str, dict]) -> None:
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def latest_signals(df: pd.DataFrame, params: dict) -> dict | None:
    """Indicator values on the last CLOSED daily bar. Returns None if insufficient history.

    `df` is daily OHLCV with a datetime index, ascending. Any bar dated today (a forming
    candle) is dropped so we only ever act on completed bars.
    """
    if df.empty:
        return None
    today = pd.Timestamp(datetime.now(IST).date())  # market calendar is IST
    closed = df[_bar_dates(df) < today]              # drop any forming candle dated today
    if len(closed) < params["ma_len"] + 1:
        return None
    close = closed["close"].astype(float)
    r = rsi(close, params["rsi_len"])
    ma = sma(close, params["ma_len"])
    a = atr(closed, params["rsi_len"])
    last = closed.index[-1]
    return {
        "bar_date": last,
        "close": float(close.iloc[-1]),
        "rsi": float(r.iloc[-1]),
        "sma": float(ma.iloc[-1]),
        "atr": float(a.iloc[-1]),
    }


def build_s001_strategy_fn(*, kite, broker, vault, params: dict | None = None,
                           strategy_link: str = STRATEGY_NOTE,
                           state_path: Path | str = ".s001_positions.json",
                           lookback_days: int = _LOOKBACK_DAYS) -> Callable[[dict], list[dict]]:
    """Build the s001 `strategy_fn(ctx)` closure.

    Captures the read-only Kite client (for daily candles), the PaperBroker (source of truth
    for open positions), and the vault (to reconstruct deterministic trade-note paths for
    exits). Supplementary per-position state (entry date + ATR-at-entry, for the time/price
    stop) is persisted to `state_path`.
    """
    p = {**PARAMS, **(params or {})}
    state_path = Path(state_path)
    token_cache: dict[str, int] = {}

    def _token(symbol: str) -> int | None:
        if symbol in token_cache:
            return token_cache[symbol]
        matches = kite.search_instruments(symbol, filter_on="tradingsymbol",
                                          exchange="NSE", limit=20)
        row = next((m for m in matches if m["tradingsymbol"].upper() == symbol.upper()
                    and m["exchange"] == "NSE"), None)
        if not row:
            return None
        token_cache[symbol] = row["instrument_token"]
        return row["instrument_token"]

    def _daily(symbol: str) -> pd.DataFrame | None:
        tok = _token(symbol)
        if tok is None:
            return None
        to_d = date.today()
        from_d = to_d - timedelta(days=lookback_days)
        candles = kite.historical_data(tok, str(from_d), str(to_d), "day")
        if not candles:
            return None
        df = pd.DataFrame(candles)
        df.index = pd.to_datetime(df["date"])
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    def _bars_held(df: pd.DataFrame, entry_date: str, upto: pd.Timestamp) -> int:
        ed = pd.Timestamp(entry_date).normalize()
        up = pd.Timestamp(upto)
        up = (up.tz_localize(None) if up.tz is not None else up).normalize()
        dates = _bar_dates(df)
        return int(((dates > ed) & (dates <= up)).sum())

    def strategy_fn(ctx: dict) -> list[dict]:
        universe = ctx.get("universe") or []
        equity = float(ctx.get("equity") or 0.0)
        price_fn = ctx.get("price_fn")
        d = ctx.get("date") or date.today().isoformat()
        state = _load_state(state_path)

        positions = {pos["tradingsymbol"]: pos for pos in broker.get_positions()
                     if pos.get("quantity", 0) > 0}

        # existing open risk (fraction of equity) for the total-risk cap in sizing (§4).
        existing_open_risk = 0.0
        for sym, pos in positions.items():
            st = state.get(sym)
            if st and equity:
                risk_rs = max(0.0, (st.get("entry_price", pos["average_price"])
                                    - st.get("stop_price", 0.0))) * pos["quantity"]
                existing_open_risk += risk_rs / equity

        intents: list[dict] = []
        for symbol in universe:
            df = _daily(symbol)
            if df is None:
                continue
            sig = latest_signals(df, p)
            if sig is None:
                continue

            exch = "NSE"
            if price_fn is not None:
                try:
                    last_price = float(price_fn(f"{exch}:{symbol}"))
                except Exception:  # noqa: BLE001 — fall back to last closed bar
                    last_price = sig["close"]
            else:
                last_price = sig["close"]

            pos = positions.get(symbol)
            if pos:  # --- manage an open long: exit on RSI / ATR-stop / time-stop ---
                st = state.get(symbol, {})
                entry_price = st.get("entry_price", pos["average_price"])
                atr_at_entry = st.get("atr_at_entry", sig["atr"])
                stop_price = st.get("stop_price", entry_price - p["atr_k"] * atr_at_entry)
                entry_date = st.get("entry_date", d)
                held = _bars_held(df, entry_date, sig["bar_date"])

                reason = None
                if sig["rsi"] > p["rsi_exit"]:
                    reason = f"RSI {sig['rsi']:.1f} > {p['rsi_exit']:.0f}"
                elif last_price <= stop_price:
                    reason = f"price {last_price:.2f} <= stop {stop_price:.2f} ({p['atr_k']}xATR)"
                elif held >= p["time_stop"]:
                    reason = f"time stop: {held} bars held >= {p['time_stop']}"

                if reason:
                    intents.append({
                        "action": "exit", "symbol": symbol, "exchange": exch,
                        "quantity": pos["quantity"], "last_price": last_price,
                        "entry_price": entry_price,
                        "trade_note_rel": vault.trade_rel(entry_date, symbol, STRATEGY_ID),
                        "reason": reason,
                    })
                    state.pop(symbol, None)

            else:  # --- flat: enter on oversold-in-uptrend ---
                if sig["rsi"] < p["rsi_entry"] and sig["close"] > sig["sma"]:
                    stop_price = round(last_price - p["atr_k"] * sig["atr"], 2)
                    just = (
                        f"s001 forward-test entry. Last closed bar {sig['bar_date'].date()}: "
                        f"RSI({p['rsi_len']})={sig['rsi']:.1f} < {p['rsi_entry']:.0f} (oversold) "
                        f"AND close {sig['close']:.2f} > SMA({p['ma_len']}) {sig['sma']:.2f} "
                        f"(long-term uptrend). Stop = entry - {p['atr_k']}xATR({p['rsi_len']}) "
                        f"= {last_price:.2f} - {p['atr_k']}x{sig['atr']:.2f} = {stop_price:.2f}. "
                        f"Exits: RSI>{p['rsi_exit']:.0f} | stop hit | {p['time_stop']}-bar time "
                        f"stop. NOTE: backtest-GRAVEYARD strategy run as a pipeline forward-test, "
                        f"not a proven edge."
                    )
                    intents.append({
                        "action": "enter", "symbol": symbol, "exchange": exch,
                        "strategy_id": STRATEGY_ID, "strategy_link": strategy_link,
                        "last_price": last_price, "atr": sig["atr"], "k": p["atr_k"],
                        "existing_open_risk": existing_open_risk,
                        "regime": "oversold-in-uptrend", "justification": just,
                    })
                    state[symbol] = {
                        "entry_date": d, "entry_price": last_price,
                        "atr_at_entry": sig["atr"], "stop_price": stop_price,
                    }

        _save_state(state_path, state)
        return intents

    return strategy_fn
