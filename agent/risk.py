"""Position sizing & risk caps (spec §4) — deterministic, mechanical, unit-tested.

Sizing is NOT a judgment call (spec §4: "you do not pick share counts by feel"). Every
number here is computed in Python so the agent can never fat-finger the capital-preservation
math. The governor (agent/governor.py) is the matching kill switch.

Rules implemented:
  R (risk per trade)   = min(5% of equity, strategy_risk_budget)         §4.1
  stop_distance        = k × ATR(14)                                     §4.1
  raw_qty              = floor(R / stop_distance)                        §4.2
  cash_cap_qty         = floor(available_cash / entry_price)  (1× only)  §4.2
  per_name_cap_qty     = floor(20% × equity / entry_price)               §4.4
  qty                  = round_to_lot(min(raw, cash_cap, per_name))      §4.2
  → if adding this trade's risk would push total open risk > 15% → SKIP  §4.4
  → if qty == 0 (stop too tight / cash short) → SKIP, log reason         §4.2
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SizingResult:
    qty: int
    allowed: bool
    reason: str
    risk_rupees: float        # intended R (budget for this trade)
    stop_distance: float      # k × ATR, rupees/share
    stop_price: float         # broker-side protective stop (spec §4.3)
    trade_risk: float         # qty × stop_distance (actual risk taken)
    notional: float           # qty × entry_price


def size_position(*,
                  equity: float,
                  available_cash: float,
                  entry_price: float,
                  atr: float,
                  k: float = 2.0,
                  direction: str = "long",
                  risk_pct: float = 0.05,
                  strategy_risk_budget: float | None = None,
                  existing_open_risk: float = 0.0,
                  max_total_open_risk_pct: float = 0.15,
                  max_per_name_notional_pct: float = 0.20,
                  lot_size: int = 1) -> SizingResult:
    """Compute the share quantity for a new entry, or 0 (with a reason) if it must be skipped."""
    risk_rupees = risk_pct * equity
    if strategy_risk_budget is not None:
        risk_rupees = min(risk_rupees, strategy_risk_budget)

    stop_distance = k * atr

    def skip(reason: str, stop_px: float = 0.0) -> SizingResult:
        return SizingResult(0, False, reason, risk_rupees, stop_distance, stop_px, 0.0, 0.0)

    if entry_price <= 0:
        return skip("invalid entry price")
    if stop_distance <= 0:
        return skip("invalid stop distance (atr or k <= 0)")
    if available_cash <= 0:
        return skip("no available cash")

    stop_price = (entry_price - stop_distance if direction == "long"
                  else entry_price + stop_distance)

    raw_qty = math.floor(risk_rupees / stop_distance)
    cash_cap_qty = math.floor(available_cash / entry_price)          # 1× leverage cap
    per_name_cap_qty = math.floor(max_per_name_notional_pct * equity / entry_price)

    qty = min(raw_qty, cash_cap_qty, per_name_cap_qty)
    qty = (qty // lot_size) * lot_size  # round down to a whole lot
    qty = max(0, qty)

    if qty == 0:
        binding = min(raw_qty, cash_cap_qty, per_name_cap_qty)
        why = ("stop too tight / risk budget too small" if raw_qty == binding
               else "insufficient cash" if cash_cap_qty == binding
               else "per-name notional cap" if per_name_cap_qty == binding
               else "below one lot")
        return skip(f"qty rounds to 0 ({why})", stop_price)

    # Total open-risk cap (§4.4): skip if the intended trade would breach 15%.
    trade_risk = qty * stop_distance
    risk_ceiling = max_total_open_risk_pct * equity
    if existing_open_risk + trade_risk > risk_ceiling + 1e-9:
        return SizingResult(
            0, False,
            f"would breach total open-risk cap "
            f"(existing {existing_open_risk:.2f} + new {trade_risk:.2f} > {risk_ceiling:.2f})",
            risk_rupees, stop_distance, stop_price, 0.0, 0.0,
        )

    return SizingResult(
        qty=qty, allowed=True, reason="ok",
        risk_rupees=risk_rupees, stop_distance=stop_distance, stop_price=stop_price,
        trade_risk=trade_risk, notional=qty * entry_price,
    )


def total_open_risk(open_positions: list[dict]) -> float:
    """Sum of per-position risk = qty × stop_distance across open positions.

    Each position dict needs 'quantity' and 'stop_distance' (rupees/share).
    """
    return float(sum(abs(p["quantity"]) * p["stop_distance"] for p in open_positions))
