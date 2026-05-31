"""Live-strategy decay monitoring (spec §6.4 / §6.4.2).

Edges decay as more people trade them. Track rolling performance of each live strategy;
when it degrades past a pre-set threshold, move it toward `retired` and replace it. The
research never stops precisely because of this.
"""
from __future__ import annotations

from dataclasses import dataclass


def _pnl(t: dict) -> float:
    v = t.get("pnl_rupees", t.get("pnl"))
    return float(v) if v is not None else 0.0


def rolling_win_rate(trades: list[dict], window: int = 60) -> float | None:
    """Win rate over the most recent `window` closed trades (chronological order assumed).
    Returns None if there aren't enough trades to judge."""
    closed = [t for t in trades if t.get("status", "closed") == "closed"]
    if len(closed) < window:
        return None
    recent = closed[-window:]
    wins = sum(1 for t in recent if _pnl(t) > 0)
    return wins / window


@dataclass
class DecayVerdict:
    should_retire: bool
    rolling_win_rate: float | None
    sample: int
    reason: str


def decay_check(trades: list[dict], *, window: int = 60,
                retire_below: float = 0.40) -> DecayVerdict:
    """Retire a live strategy if its rolling `window`-trade win rate falls below
    `retire_below`. Not enough trades yet → no decision (keep researching)."""
    closed = [t for t in trades if t.get("status", "closed") == "closed"]
    wr = rolling_win_rate(trades, window)
    if wr is None:
        return DecayVerdict(False, None, len(closed),
                            f"only {len(closed)} closed trades (< {window}); insufficient sample")
    if wr < retire_below:
        return DecayVerdict(True, wr, window,
                            f"rolling {window}-trade win rate {wr:.0%} < {retire_below:.0%} "
                            "→ retire and replace")
    return DecayVerdict(False, wr, window,
                        f"rolling {window}-trade win rate {wr:.0%} ≥ {retire_below:.0%} → healthy")
