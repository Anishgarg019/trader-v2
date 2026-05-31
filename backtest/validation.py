"""Out-of-sample & overfitting discipline (spec §6.4.2).

Split data into in-sample (design/tune) and out-of-sample (validate, untouched during
tuning). A strategy that shines in-sample and dies out-of-sample is overfit → reject.
Rejection is the DEFAULT outcome; a healthy process kills far more strategies than it
deploys.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd


def train_test_split(df: pd.DataFrame, split: float | str | date | datetime = 0.7):
    """Split a time-indexed DataFrame chronologically.

    `split` as a float in (0,1) → fraction in-sample; as a date/str → cutoff date
    (rows <= cutoff are in-sample). Returns (in_sample, out_of_sample).
    """
    if isinstance(split, float):
        if not 0 < split < 1:
            raise ValueError("fractional split must be in (0, 1)")
        cut = int(len(df) * split)
        return df.iloc[:cut], df.iloc[cut:]
    cutoff = pd.Timestamp(split)
    return df.loc[df.index <= cutoff], df.loc[df.index > cutoff]


@dataclass
class OverfitReport:
    flags: list[str] = field(default_factory=list)
    rejected: bool = False
    detail: dict = field(default_factory=dict)


def overfit_report(is_metrics: dict[str, float],
                   oos_metrics: dict[str, float],
                   *,
                   n_params: int = 0,
                   min_trades_oos: int = 30,
                   max_sharpe_degradation: float = 0.5,
                   max_params: int = 5) -> OverfitReport:
    """Flag overfitting. HARD flags (any → reject):
      - too_few_trades_oos : OOS trade count below `min_trades_oos` (no statistical weight)
      - negative_oos_return: OOS lost money despite (presumably) positive IS
      - sharpe_collapse    : OOS Sharpe fell more than `max_sharpe_degradation` vs a
                             positive IS Sharpe (edge didn't survive)
    SOFT flag (warn, no auto-reject):
      - many_params        : more knobs than `max_params` (fragility risk)
    """
    flags: list[str] = []

    oos_trades = oos_metrics.get("trades", 0)
    if oos_trades < min_trades_oos:
        flags.append("too_few_trades_oos")

    if oos_metrics.get("total_return", 0.0) <= 0:
        flags.append("negative_oos_return")

    is_sharpe = is_metrics.get("sharpe_like")
    oos_sharpe = oos_metrics.get("sharpe_like")
    if is_sharpe and is_sharpe > 0 and oos_sharpe is not None:
        if oos_sharpe < (1 - max_sharpe_degradation) * is_sharpe:
            flags.append("sharpe_collapse")

    soft = []
    if n_params > max_params:
        soft.append("many_params")

    hard_flags = [f for f in flags]
    return OverfitReport(
        flags=hard_flags + soft,
        rejected=bool(hard_flags),
        detail={
            "is_sharpe": is_sharpe, "oos_sharpe": oos_sharpe,
            "oos_trades": oos_trades, "oos_return": oos_metrics.get("total_return"),
            "n_params": n_params,
        },
    )
