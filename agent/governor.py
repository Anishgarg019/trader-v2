"""Drawdown governor — the kill switch (spec §5). Non-overridable, deterministic.

Checked at the start of every loop and after every fill. Two absolute limits:
  - Daily drawdown ≥ 5% (from the day's opening equity)  → HALT new entries for the day.
  - Total drawdown ≥ 15% (from the high-water mark)        → FULL STOP + reassessment mode
    (next session research-only until a post-mortem note exists, spec §5/§6.5).
Plus an over-leverage assertion: gross exposure after any trade must never exceed available
cash (1× only); if it would, sizing is wrong → skip + system-alert.
"""
from __future__ import annotations

from dataclasses import dataclass


class OverLeverageError(RuntimeError):
    """Raised when a trade would push gross exposure beyond available cash (forbidden)."""


@dataclass(frozen=True)
class GovernorDecision:
    daily_drawdown_pct: float    # negative when down (e.g. -0.05 = down 5%)
    total_drawdown_pct: float    # negative when below the high-water mark
    halt_new_entries: bool       # daily limit tripped
    full_stop: bool              # total limit tripped → reassessment mode
    reason: str

    @property
    def can_enter(self) -> bool:
        """New entries allowed only if neither limit is tripped."""
        return not (self.halt_new_entries or self.full_stop)


def evaluate_drawdown(current_equity: float,
                      day_open_equity: float,
                      high_water_mark: float,
                      *,
                      daily_limit_pct: float = 0.05,
                      total_limit_pct: float = 0.15) -> GovernorDecision:
    """Evaluate both drawdown limits. Limits are absolute and trip at >= the threshold."""
    if day_open_equity <= 0 or high_water_mark <= 0:
        raise ValueError("day_open_equity and high_water_mark must be positive")

    daily_dd = (current_equity - day_open_equity) / day_open_equity
    total_dd = (current_equity - high_water_mark) / high_water_mark

    halt = daily_dd <= -daily_limit_pct
    full_stop = total_dd <= -total_limit_pct

    if full_stop:
        reason = (f"TOTAL drawdown {total_dd:.2%} ≤ -{total_limit_pct:.0%} from high-water "
                  "→ FULL STOP, reassessment mode (post-mortem required before resuming)")
    elif halt:
        reason = (f"DAILY drawdown {daily_dd:.2%} ≤ -{daily_limit_pct:.0%} from day-open "
                  "→ halt new entries for the day; manage open positions per their stops only")
    else:
        reason = "within drawdown limits"

    return GovernorDecision(
        daily_drawdown_pct=daily_dd, total_drawdown_pct=total_dd,
        halt_new_entries=halt, full_stop=full_stop, reason=reason,
    )


def update_high_water_mark(high_water_mark: float, current_equity: float) -> float:
    """High-water mark is peak equity (spec §5 uses peak, not just starting capital)."""
    return max(high_water_mark, current_equity)


def assert_no_leverage(gross_exposure_after_trade: float, available_capital: float) -> None:
    """Assert gross exposure after a trade ≤ available capital (1× only, spec §5).

    `available_capital` is the cash you can deploy (equity available for new exposure).
    Raises OverLeverageError if violated — the sizing in §4 produced something impossible.
    """
    if gross_exposure_after_trade > available_capital + 1e-6:
        raise OverLeverageError(
            f"gross exposure {gross_exposure_after_trade:.2f} would exceed available capital "
            f"{available_capital:.2f} — that is leverage, which is forbidden (skip + system-alert)"
        )
