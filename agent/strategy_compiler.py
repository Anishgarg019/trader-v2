"""Strategy-spec compiler (Phase 11, RESEARCHER-SPEC §4).

Turns a validated JSON spec (data) into:
  - `entries(df)` / `exits(df)` boolean Series — exactly the arguments `run_backtest`
    expects (backtest/engine.py), and
  - a live `strategy_fn(ctx)` (via `strategy_fn_factory`) matching the loop contract
    (agent/loop.py:147 `_market`).

SAFETY (invariant #1): the compiler NEVER `eval`s or `getattr`s a name from the spec. Every
predicate is dispatched through an explicit handler table mapped to a real, tested function
in `agent.signals`. An unknown `pred` raises (it was already rejected by the validator). No
look-ahead: every signal function is causal (rolling / shift over data ≤ the current bar);
the backtest engine fills at the next open.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from agent.signals import trend, momentum, structure, volatility, volume, patterns
from agent.signals._common import sma, ema
from agent.strategy_spec import validate_spec, count_params, COMBINATORS
from agent.trading_day import IST


# ---- leaf predicate handlers (pred -> boolean Series aligned to df) ----------
def _ma(close: pd.Series, length: int, kind: str) -> pd.Series:
    return sma(close, length) if kind == "sma" else ema(close, length)


# Divergence is confirmed by a swing pivot that needs `right` future bars (momentum.py uses
# left=right=3). The raw signal marks the divergence at the pivot bar `b`, but `b` isn't
# KNOWABLE until `b + right` bars have printed. Shifting the signal forward by this lag makes
# the predicate causal (no look-ahead) so the backtest/overfit gate stays honest.
_DIVERGENCE_CONFIRM_LAG = 3


def _osc_series(close: pd.Series, osc: str, length: int) -> pd.Series:
    if osc == "rsi":
        return momentum.rsi(close, length)
    return momentum.macd(close)["macd"]   # macd line; length parameterizes only the rsi osc


def _divergence(fn, close: pd.Series, osc: str, length: int) -> pd.Series:
    """Causal divergence: compute the raw (look-ahead) pivot signal, then delay it to the
    bar where the confirming pivot is actually knowable."""
    raw = fn(close, _osc_series(close, osc, length))
    return raw.shift(_DIVERGENCE_CONFIRM_LAG).fillna(False)


_HANDLERS: dict[str, Callable[[pd.DataFrame, dict], pd.Series]] = {
    "price_above_ma": lambda df, n: trend.price_above_ma(df["close"], n["length"], n.get("kind", "sma")),
    "price_below_ma": lambda df, n: ~trend.price_above_ma(df["close"], n["length"], n.get("kind", "sma")),
    "ma_cross_up": lambda df, n: trend.cross_up(_ma(df["close"], n["fast"], n.get("kind", "sma")),
                                                _ma(df["close"], n["slow"], n.get("kind", "sma"))),
    "ma_cross_down": lambda df, n: trend.cross_down(_ma(df["close"], n["fast"], n.get("kind", "sma")),
                                                    _ma(df["close"], n["slow"], n.get("kind", "sma"))),
    "adx_above": lambda df, n: trend.adx(df, n["length"])["adx"] > n["threshold"],
    "rsi_below": lambda df, n: momentum.rsi(df["close"], n["length"]) < n["threshold"],
    "rsi_above": lambda df, n: momentum.rsi(df["close"], n["length"]) > n["threshold"],
    "macd_cross_up": lambda df, n: trend.cross_up(
        *(lambda m: (m["macd"], m["signal"]))(momentum.macd(df["close"], n["fast"], n["slow"], n["signal"]))),
    "macd_cross_down": lambda df, n: trend.cross_down(
        *(lambda m: (m["macd"], m["signal"]))(momentum.macd(df["close"], n["fast"], n["slow"], n["signal"]))),
    "stoch_below": lambda df, n: momentum.stochastic(df, n["k_len"], n["d_len"])["k"] < n["threshold"],
    "stoch_above": lambda df, n: momentum.stochastic(df, n["k_len"], n["d_len"])["k"] > n["threshold"],
    "bullish_divergence": lambda df, n: _divergence(
        momentum.bullish_divergence, df["close"], n.get("osc", "rsi"), n["length"]),
    "bearish_divergence": lambda df, n: _divergence(
        momentum.bearish_divergence, df["close"], n.get("osc", "rsi"), n["length"]),
    "breakout_up": lambda df, n: structure.breakout_up(df, n["length"]),
    "breakout_down": lambda df, n: structure.breakout_down(df, n["length"]),
    "higher_highs": lambda df, n: structure.higher_highs(df["high"], n["length"]),
    "lower_lows": lambda df, n: structure.lower_lows(df["low"], n["length"]),
    "bollinger_break_up": lambda df, n: df["close"] > volatility.bollinger_bands(df["close"], n["length"], n["k"])["upper"],
    "bollinger_break_dn": lambda df, n: df["close"] < volatility.bollinger_bands(df["close"], n["length"], n["k"])["lower"],
    "bollinger_squeeze": lambda df, n: volatility.bollinger_squeeze(df["close"], n["length"], n["k"], n["lookback"]),
    "volume_spike": lambda df, n: volume.volume_spike(df["volume"], n["length"], n["k"]),
    "volume_confirms": lambda df, n: volume.volume_confirms(df["volume"], n["length"]),
    "doji": lambda df, n: patterns.doji(df, n["body_frac"]),
    "hammer": lambda df, n: patterns.hammer(df, n["body_frac"]),
    "bullish_engulfing": lambda df, n: patterns.bullish_engulfing(df),
    "bearish_engulfing": lambda df, n: patterns.bearish_engulfing(df),
}

# length-like keys that determine how much history a predicate needs (for min_bars)
_LENGTH_KEYS = ("length", "fast", "slow", "signal", "k_len", "d_len", "lookback")


def _to_bool(series: pd.Series, index: pd.Index) -> pd.Series:
    """Align to `index`, treat NaN/undefined as False, return a clean boolean Series."""
    return series.reindex(index).fillna(False).astype(bool)


def _eval_node(node: dict, df: pd.DataFrame) -> pd.Series:
    """Evaluate a predicate tree to a boolean Series aligned to df.index."""
    if "pred" in node:
        handler = _HANDLERS.get(node["pred"])
        if handler is None:  # unreachable after validation — defensive (no eval/getattr)
            raise KeyError(f"no compiler handler for predicate {node['pred']!r}")
        return _to_bool(handler(df, node), df.index)

    comb = next(k for k in node if k in COMBINATORS)
    if comb == "not":
        child = node["not"][0] if isinstance(node["not"], list) else node["not"]
        return ~_eval_node(child, df)

    children = [_eval_node(c, df) for c in node[comb]]
    result = children[0]
    for c in children[1:]:
        result = (result & c) if comb == "all" else (result | c)
    return result


def _bar_dates(df: pd.DataFrame) -> pd.DatetimeIndex:
    """Index normalized to tz-naive calendar dates (Kite candles are tz-aware IST)."""
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


@dataclass
class CompiledStrategy:
    spec: dict
    n_params: int

    @property
    def id(self) -> str:
        return self.spec.get("id", "")

    @property
    def atr_k(self) -> float:
        return float(self.spec["atr_k"])

    @property
    def atr_len(self) -> int:
        return int(self.spec.get("atr_len", 14))

    def min_bars(self) -> int:
        """Minimum bars of history needed to evaluate the latest signal."""
        lengths = [self.atr_len]
        for key in ("entry", "exit"):
            for node in _iter_leaves(self.spec.get(key, {})):
                for k in _LENGTH_KEYS:
                    if k in node:
                        lengths.append(int(node[k]))
        return max(lengths) + 5

    def entries(self, df: pd.DataFrame) -> pd.Series:
        return _eval_node(self.spec["entry"], df).rename("entries")

    def exits(self, df: pd.DataFrame) -> pd.Series:
        return _eval_node(self.spec["exit"], df).rename("exits")

    # ---- live loop adapter ---------------------------------------------------
    def strategy_fn_factory(self, *, history_fn: Callable[[str], pd.DataFrame | None],
                            deployed_symbols: list[str],
                            broker: Any | None = None,
                            vault: Any | None = None,
                            strategy_link: str = "",
                            state_path: str | Path | None = None,
                            exchange: str = "NSE") -> Callable[[dict], list[dict]]:
        """Build a `strategy_fn(ctx)` for the live loop (agent/loop.py `_market`).

        Iterates over `deployed_symbols` ONLY (the gate-proven subset — never a name the
        strategy lost on, safety invariant #8). For each, pulls daily candles via
        `history_fn(symbol)`, drops any forming candle dated today, evaluates the compiled
        entry/exit trees on the last CLOSED bar, and emits the enter/exit item dicts the
        loop consumes. `broker` (if given) is the source of truth for open positions, so
        exits and the protective ATR stop can be managed; without it, only entries fire
        (useful for smoke tests). Per-position entry context (entry date/price, ATR, stop)
        persists to `state_path` so stops survive across runs.
        """
        spec_id = self.spec.get("id", "spec")
        link = strategy_link or self.spec.get("note_rel", "")
        sp = Path(state_path) if state_path else Path(f".{spec_id}_positions.json")
        k = self.atr_k
        atr_len = self.atr_len
        need = self.min_bars()
        families = ", ".join(self.spec.get("families", [])) or "n/a"

        def _closed(symbol: str) -> pd.DataFrame | None:
            df = history_fn(symbol)
            if df is None or df.empty:
                return None
            today = pd.Timestamp(datetime.now(IST).date())
            closed = df[_bar_dates(df) < today]
            return closed if len(closed) >= need else None

        def _positions() -> dict[str, dict]:
            if broker is None:
                return {}
            return {p["tradingsymbol"]: p for p in broker.get_positions()
                    if p.get("quantity", 0) > 0}

        def strategy_fn(ctx: dict) -> list[dict]:
            equity = float(ctx.get("equity") or 0.0)
            price_fn = ctx.get("price_fn")
            d = ctx.get("date") or date.today().isoformat()
            state = _load_state(sp)
            positions = _positions()

            # existing open risk (fraction of equity) for the §4 total-risk cap in sizing
            existing_open_risk = 0.0
            for sym, pos in positions.items():
                st = state.get(sym)
                if st and equity:
                    risk_rs = max(0.0, (st.get("entry_price", pos["average_price"])
                                        - st.get("stop_price", 0.0))) * pos["quantity"]
                    existing_open_risk += risk_rs / equity

            intents: list[dict] = []
            for name in deployed_symbols:
                # deployed symbols may be 'EXCH:SYMBOL' (universe form) or bare
                if ":" in name:
                    exch, symbol = name.split(":", 1)
                else:
                    exch, symbol = exchange, name
                closed = _closed(symbol)
                if closed is None:
                    continue
                ent = self.entries(closed)
                ext = self.exits(closed)
                atr_series = volatility.atr(closed, atr_len)
                bar_date = closed.index[-1]
                close_px = float(closed["close"].iloc[-1])
                atr_val = float(atr_series.iloc[-1])
                if atr_val != atr_val:  # NaN guard
                    continue

                if price_fn is not None:
                    try:
                        last_price = float(price_fn(f"{exch}:{symbol}"))
                    except Exception:  # noqa: BLE001 — fall back to last closed bar
                        last_price = close_px
                else:
                    last_price = close_px

                pos = positions.get(symbol)
                if pos:  # manage open long: signal exit OR protective ATR stop
                    st = state.get(symbol, {})
                    entry_price = st.get("entry_price", pos["average_price"])
                    stop_price = st.get("stop_price", entry_price - k * atr_val)
                    reason = None
                    if bool(ext.iloc[-1]):
                        reason = f"{spec_id} signal exit on bar {pd.Timestamp(bar_date).date()}"
                    elif last_price <= stop_price:
                        reason = f"price {last_price:.2f} <= stop {stop_price:.2f} ({k}xATR)"
                    if reason:
                        intents.append({
                            "action": "exit", "symbol": symbol, "exchange": exch,
                            "quantity": pos["quantity"], "last_price": last_price,
                            "entry_price": entry_price,
                            "trade_note_rel": (vault.trade_rel(st.get("entry_date", d), symbol, spec_id)
                                               if vault is not None else None),
                            "reason": reason,
                        })
                        state.pop(symbol, None)
                elif bool(ent.iloc[-1]):  # flat → enter
                    stop_price = round(last_price - k * atr_val, 2)
                    just = (
                        f"{spec_id} ({self.spec.get('name', '')}) forward-test entry. "
                        f"Families: {families}. Last closed bar {pd.Timestamp(bar_date).date()}: "
                        f"compiled entry tree True; close {close_px:.2f}. "
                        f"Stop = entry - {k}xATR({atr_len}) = {last_price:.2f} - {k}x{atr_val:.2f} "
                        f"= {stop_price:.2f}. Per-symbol gate-proven deployment."
                    )
                    intents.append({
                        "action": "enter", "symbol": symbol, "exchange": exch,
                        "strategy_id": spec_id, "strategy_link": link,
                        "last_price": last_price, "atr": atr_val, "k": k,
                        "existing_open_risk": existing_open_risk,
                        "regime": ",".join(self.spec.get("families", [])) or None,
                        "justification": just,
                    })
                    state[symbol] = {"entry_date": d, "entry_price": last_price,
                                     "atr_at_entry": atr_val, "stop_price": stop_price}

            _save_state(sp, state)
            return intents

        return strategy_fn


def _iter_leaves(node: Any):
    if isinstance(node, dict):
        if "pred" in node:
            yield node
            return
        for k in node:
            if k in COMBINATORS:
                children = node[k]
                children = children if isinstance(children, list) else [children]
                for c in children:
                    yield from _iter_leaves(c)


def _load_state(path: Path) -> dict[str, dict]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(path: Path, state: dict[str, dict]) -> None:
    try:
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def compile_spec(spec: dict) -> CompiledStrategy:
    """Validate (raises SpecError) then compile a spec to a CompiledStrategy."""
    validate_spec(spec)
    return CompiledStrategy(spec=spec, n_params=count_params(spec))
