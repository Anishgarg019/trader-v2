"""Persist LoopState across runs (cross-platform JSON). The scheduled run reads it at
startup and writes it at the end so day-open equity and the high-water mark survive
between invocations (spec §5)."""
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
