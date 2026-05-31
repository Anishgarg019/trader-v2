"""Universe selection (spec §2): pick ~10 liquid, suitably-volatile, diversified names.

Pure/deterministic over provided candidate metrics so it's testable; the loop (Phase 8)
fetches day candles via the read-only Kite client and feeds them in. Order of gates:
  1. Liquidity gate (non-negotiable): 20-day avg traded value (close×volume).
  2. Volatility suitability: ATR% within a band (enough range to pay off, not so wild a
     sane stop forces a trivial position).
  3. Diversification: ≤ ~3 of 10 from any one sector.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from agent.signals.volatility import atr


def compute_candidate_metrics(df: pd.DataFrame, *, liquidity_window: int = 20,
                              atr_len: int = 14) -> dict[str, float]:
    """From day OHLCV, compute avg traded value (₹), ATR%, last close."""
    traded_value = (df["close"] * df["volume"]).tail(liquidity_window).mean()
    last_close = float(df["close"].iloc[-1])
    atr_val = float(atr(df, atr_len).iloc[-1])
    atr_pct = atr_val / last_close if last_close > 0 else float("nan")
    return {"avg_traded_value": float(traded_value), "atr_pct": atr_pct,
            "last_close": last_close}


@dataclass
class UniverseSelection:
    picks: list[dict[str, Any]]
    considered: int
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (symbol, reason)

    @property
    def is_full(self) -> bool:
        return len(self.picks) >= 1


def select_universe(candidates: list[dict[str, Any]], *,
                    size: int = 10,
                    max_per_sector: int = 3,
                    atr_min_pct: float = 0.01,
                    atr_max_pct: float = 0.06,
                    min_traded_value: float | None = None) -> UniverseSelection:
    """Select up to `size` names. Each candidate needs keys: symbol, exchange, token,
    sector, avg_traded_value, atr_pct."""
    considered = len(candidates)
    rejected: list[tuple[str, str]] = []

    # 1. Liquidity gate
    pool = []
    for c in candidates:
        if min_traded_value is not None and c["avg_traded_value"] < min_traded_value:
            rejected.append((c["symbol"], "below liquidity floor"))
            continue
        pool.append(c)

    # 2. Volatility suitability
    eligible = []
    for c in pool:
        ap = c["atr_pct"]
        if ap != ap or not (atr_min_pct <= ap <= atr_max_pct):  # NaN-safe band check
            rejected.append((c["symbol"], f"atr% {ap:.3f} outside [{atr_min_pct},{atr_max_pct}]"))
            continue
        eligible.append(c)

    # Liquidity is the priority ranking within the eligible set.
    eligible.sort(key=lambda c: c["avg_traded_value"], reverse=True)

    # 3. Diversification (sector cap), greedy by liquidity
    picks: list[dict[str, Any]] = []
    sector_count: dict[str, int] = {}
    for c in eligible:
        if len(picks) >= size:
            break
        sec = c.get("sector") or "UNKNOWN"
        if sector_count.get(sec, 0) >= max_per_sector:
            rejected.append((c["symbol"], f"sector cap reached ({sec})"))
            continue
        picks.append(c)
        sector_count[sec] = sector_count.get(sec, 0) + 1

    return UniverseSelection(picks=picks, considered=considered, rejected=rejected)


def write_universe_note(writer, selection: UniverseSelection, *, d: str,
                        changelog: list[str] | None = None) -> "Path":  # noqa: F821
    """Write Universe/current-universe.md with the metrics that justified each pick (spec §2.2)."""
    names = [f"{p['exchange']}:{p['symbol']}" for p in selection.picks]
    fm = {
        "type": "universe", "date": d, "size": len(selection.picks),
        "names": names, "tags": ["universe"],
    }
    lines = ["## Selected universe (why these names)", "",
             "| symbol | exch | token | sector | 20d avg traded value (₹) | ATR% |",
             "| --- | --- | --- | --- | ---: | ---: |"]
    for p in selection.picks:
        lines.append(
            f"| {p['symbol']} | {p['exchange']} | {p['token']} | {p.get('sector','—')} "
            f"| {p['avg_traded_value']:,.0f} | {p['atr_pct']:.2%} |"
        )
    lines += ["", f"_Considered {selection.considered} candidates; "
                  f"{len(selection.rejected)} rejected._", "", "## Changelog"]
    for entry in (changelog or [f"- {d} initial selection"]):
        lines.append(entry if entry.startswith("-") else f"- {entry}")
    return writer.write_note("Universe/current-universe.md", fm, "\n".join(lines))
