"""Build the live loop's `strategy_fn` from the registry (Phase 11, RESEARCHER-SPEC §G).

Replaces the single hand-wired s001 `strategy_fn` with a registry-backed merge: load every
ACTIVE forward-test spec, compile it, build its per-symbol factory over ITS OWN
`deployed_symbols`, and merge the emitted intents. Risk caps / governor / stops downstream
are unchanged — this only decides *what* the loop is asked to enter/exit.

SAFETY: every spec is re-validated (`compile_spec`) before it can emit anything; a strategy
only ever acts on its gate-proven `deployed_symbols` (invariant #8); orders still flow solely
through the PaperBroker via the execution engine.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

import pandas as pd

from agent.registry import ActiveSpec
from agent.strategy_compiler import compile_spec


def build_history_fn(kite: Any, *, lookback_days: int = 500,
                     exchange: str = "NSE") -> Callable[[str], pd.DataFrame | None]:
    """Return `history_fn(symbol)` → daily OHLCV DataFrame (read-only Kite), token-cached.

    Accepts a bare tradingsymbol (e.g. 'RELIANCE'); resolves its instrument token once and
    pulls `lookback_days` of day candles. Returns None if the symbol/token can't be resolved
    or no candles come back.
    """
    token_cache: dict[str, int] = {}

    def _token(symbol: str) -> int | None:
        if symbol in token_cache:
            return token_cache[symbol]
        matches = kite.search_instruments(symbol, filter_on="tradingsymbol",
                                          exchange=exchange, limit=20)
        row = next((m for m in matches if m["tradingsymbol"].upper() == symbol.upper()
                    and m["exchange"] == exchange), None)
        if not row:
            return None
        token_cache[symbol] = row["instrument_token"]
        return row["instrument_token"]

    def history_fn(symbol: str) -> pd.DataFrame | None:
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

    return history_fn


def build_strategy_fn(active_specs: list[ActiveSpec], *,
                      history_fn: Callable[[str], pd.DataFrame | None],
                      broker: Any, vault: Any,
                      state_dir: str = ".") -> Callable[[dict], list[dict]] | None:
    """Compile all active forward-test specs and merge their `strategy_fn`s into one.

    Returns None if there are no active specs (the loop treats `strategy_fn=None` as
    "nothing to trade"). Entries are de-duplicated by symbol (first active spec wins) so two
    strategies never both open the same name; exit intents always pass through.
    """
    fns: list[Callable[[dict], list[dict]]] = []
    for a in active_specs:
        try:
            c = compile_spec(a.spec)   # already validated on load, but stay defensive
        except Exception:  # noqa: BLE001 — a bad spec must not break the whole loop
            continue
        spec_id = a.spec.get("id", "spec")
        fns.append(c.strategy_fn_factory(
            history_fn=history_fn, deployed_symbols=a.deployed_symbols,
            broker=broker, vault=vault, strategy_link=a.note_rel,
            state_path=f"{state_dir}/.{spec_id}_positions.json"))

    if not fns:
        return None

    def strategy_fn(ctx: dict) -> list[dict]:
        merged: list[dict] = []
        entered: set[str] = set()
        for fn in fns:
            for it in (fn(ctx) or []):
                if it["action"] == "enter":
                    if it["symbol"] in entered:
                        continue   # another active spec already opened this name
                    entered.add(it["symbol"])
                merged.append(it)
        return merged

    return strategy_fn
