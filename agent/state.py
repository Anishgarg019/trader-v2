"""Persist state across runs (cross-platform JSON).

Two things must survive between scheduled invocations:
  - `LoopState` (day-open equity, high-water mark, current date) — spec §5; and
  - the PAPER BOOK (positions, cash, GTT stops, orders, trades) — so each trading morning
    resumes real multi-day state instead of starting flat (Phase 11 autonomy piece).
The scheduled run reads both at startup and writes both at the end.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from agent.loop import LoopState


def load_state(path: str | Path) -> LoopState:
    p = Path(path)
    if not p.exists():
        return LoopState()
    data = json.loads(p.read_text(encoding="utf-8"))
    return LoopState(
        day_open_equity=data.get("day_open_equity"),
        high_water_mark=data.get("high_water_mark", 100000.0),
        current_date=data.get("current_date"),
    )


def save_state(path: str | Path, state: LoopState) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
    return p


# ---- paper-book persistence (multi-day continuity) --------------------------
def save_paper_book(path: str | Path, broker) -> Path:
    """Persist the PaperBroker's full book (positions/cash/GTTs/orders/trades)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(broker.snapshot(), indent=2, default=str), encoding="utf-8")
    return p


def load_paper_book(path: str | Path, broker) -> bool:
    """Restore a persisted paper book into `broker` if the file exists. Returns True if
    restored, False if there was nothing to restore (fresh start). A corrupt file is left
    in place and treated as a fresh start (fail safe — never crash the morning run)."""
    p = Path(path)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    broker.restore(data)
    return True
