"""Reconciliation (spec §6.1.3): broker (paper-book) state vs the vault's expected state.

Internal records must match broker records. Any break is investigated and logged before
proceeding — a silent mismatch is how phantom positions and bad risk math creep in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Break:
    instrument: str
    kind: str          # 'quantity_mismatch' | 'missing_in_vault' | 'missing_in_broker'
    expected: Any
    actual: Any


@dataclass
class ReconResult:
    ok: bool
    breaks: list[Break] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return "reconciliation OK — broker matches vault"
        return "; ".join(f"{b.instrument}: {b.kind} (vault={b.expected}, broker={b.actual})"
                         for b in self.breaks)


def _key(exchange: str, tradingsymbol: str) -> str:
    return f"{exchange}:{tradingsymbol}"


def reconcile_positions(broker_positions: list[dict],
                        expected_positions: list[dict]) -> ReconResult:
    """Compare net quantities per instrument.

    Each position dict needs: exchange, tradingsymbol, quantity. `expected_positions` is
    derived from the vault's open trade notes; `broker_positions` from PaperBroker.
    Positions with quantity 0 on either side are ignored.
    """
    broker = {_key(p["exchange"], p["tradingsymbol"]): int(p["quantity"])
              for p in broker_positions if int(p["quantity"]) != 0}
    expected = {_key(p["exchange"], p["tradingsymbol"]): int(p["quantity"])
                for p in expected_positions if int(p["quantity"]) != 0}

    breaks: list[Break] = []
    for inst in sorted(set(broker) | set(expected)):
        b = broker.get(inst)
        e = expected.get(inst)
        if b is None:
            breaks.append(Break(inst, "missing_in_broker", expected=e, actual=None))
        elif e is None:
            breaks.append(Break(inst, "missing_in_vault", expected=None, actual=b))
        elif b != e:
            breaks.append(Break(inst, "quantity_mismatch", expected=e, actual=b))

    return ReconResult(ok=not breaks, breaks=breaks)


def expected_positions_from_open_trades(open_trades: list[dict]) -> list[dict]:
    """Aggregate open trade notes into expected net positions per instrument.

    Each open trade needs: symbol ('EXCH:SYM'), quantity, direction ('long'|'short').
    """
    agg: dict[str, dict] = {}
    for t in open_trades:
        sym = t["symbol"]
        exchange, tradingsymbol = sym.split(":", 1) if ":" in sym else ("NSE", sym)
        signed = int(t["quantity"]) * (1 if t.get("direction", "long") == "long" else -1)
        key = _key(exchange, tradingsymbol)
        if key not in agg:
            agg[key] = {"exchange": exchange, "tradingsymbol": tradingsymbol, "quantity": 0}
        agg[key]["quantity"] += signed
    return list(agg.values())
