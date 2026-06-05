"""Per-symbol research gate (Phase 11, RESEARCHER-SPEC §5) — the mandatory checkpoint.

`evaluate_spec` is the ONLY path to deployment. It compiles a spec, then for EACH symbol in
the universe independently runs an in-sample/out-of-sample backtest with real costs and the
overfit gate, and returns the subset of symbols the strategy is gate-proven profitable on
(`deployed_symbols`). A strategy trades ONLY those symbols — never the ones it lost on
(safety invariant #8). Empty set → the whole spec is rejected to the graveyard.

This is deterministic Python. No LLM judgment touches the verdict (invariant #2). The
overfit decision is `backtest.validation.overfit_report`; this module never softens it.

⚠️ Multiple-comparisons caveat: keeping the best-of-N symbols is itself a fluke generator.
The OOS requirement mitigates but does not eliminate it; the paper forward-test record +
`agent/decay.py` are the final arbiters. `min_symbols` can be raised if single-symbol passes
prove noisy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from agent.strategy_compiler import compile_spec, CompiledStrategy
from agent.strategy_spec import SpecError
from backtest.costs import CostModel
from backtest.engine import run_backtest
from backtest.validation import train_test_split, overfit_report, OverfitReport

# OOS trade-count floor for the research gate. Calibrated for DAILY bars over a multi-year
# window (~1950d lookback → ~400-bar OOS): a daily swing strategy fires every few weeks, so
# the old intraday-style floor of 30 was mathematically unreachable and auto-rejected every
# proposal before its edge was evaluated. 12 keeps statistical weight while letting genuine
# daily edges clear; the per-symbol gate + paper forward-test + decay monitor remain the
# false-positive backstops (user decision 2026-06-05).
DEFAULT_MIN_TRADES_OOS = 12


@dataclass
class SymbolVerdict:
    symbol: str
    passed: bool
    is_metrics: dict[str, float]
    oos_metrics: dict[str, float]
    overfit: OverfitReport | None
    notes: str = ""


@dataclass
class ResearchVerdict:
    spec_id: str
    n_params: int
    per_symbol: dict[str, SymbolVerdict] = field(default_factory=dict)
    deployed_symbols: list[str] = field(default_factory=list)
    passed: bool = False
    notes: str = ""

    def summary(self) -> dict[str, Any]:
        """Compact, auditable summary (which stocks it won/lost on + OOS stats)."""
        return {
            "spec_id": self.spec_id, "n_params": self.n_params,
            "passed": self.passed, "deployed_symbols": list(self.deployed_symbols),
            "per_symbol": {
                s: {"passed": v.passed,
                    "oos_return": v.oos_metrics.get("total_return"),
                    "oos_trades": v.oos_metrics.get("trades"),
                    "oos_sharpe": v.oos_metrics.get("sharpe_like"),
                    "flags": v.overfit.flags if v.overfit else ["error"]}
                for s, v in self.per_symbol.items()
            },
            "notes": self.notes,
        }


def _symbol_of(name: str) -> str:
    """Normalize 'NSE:RELIANCE' → 'RELIANCE' for note paths; pass through bare symbols."""
    return name.split(":", 1)[1] if ":" in name else name


def _exchange_of(name: str, default: str = "NSE") -> str:
    return name.split(":", 1)[0] if ":" in name else default


def evaluate_spec(spec: dict,
                  frames: dict[str, pd.DataFrame],
                  *,
                  split: float = 0.7,
                  cost_model: CostModel | None = None,
                  slippage_bps: float = 5.0,
                  min_trades_oos: int = DEFAULT_MIN_TRADES_OOS,
                  min_symbols: int = 1) -> ResearchVerdict:
    """Compile → per-symbol IS/OOS backtest → overfit gate → ResearchVerdict.

    `frames`: dict[symbol -> OHLCV DataFrame] for the whole universe. A symbol passes iff its
    overfit report is not rejected AND its OOS total_return > 0. `deployed_symbols` = the
    passers; the spec passes iff there are at least `min_symbols` of them.

    A compile error (or any per-symbol exception) is caught and recorded — the verdict is
    `passed=False` with an explanatory note; no exception escapes (safety invariant #6).
    """
    cost_model = cost_model or CostModel()
    spec_id = spec.get("id", "?")

    try:
        compiled: CompiledStrategy = compile_spec(spec)
    except SpecError as e:
        return ResearchVerdict(spec_id=spec_id, n_params=0, passed=False,
                               notes=f"compile/validation error: {e}")

    n_params = compiled.n_params
    per_symbol: dict[str, SymbolVerdict] = {}

    for name, df in frames.items():
        sym = _symbol_of(name)
        exch = _exchange_of(name)
        try:
            is_df, oos_df = train_test_split(df, split)
            if is_df.empty or oos_df.empty:
                per_symbol[name] = SymbolVerdict(sym, False, {}, {}, None,
                                                 "insufficient data for IS/OOS split")
                continue
            is_res = run_backtest(is_df, compiled.entries(is_df), compiled.exits(is_df),
                                  cost_model=cost_model, slippage_bps=slippage_bps,
                                  size_fraction=float(spec.get("size_fraction", 1.0)),
                                  product="CNC", exchange=exch)
            oos_res = run_backtest(oos_df, compiled.entries(oos_df), compiled.exits(oos_df),
                                   cost_model=cost_model, slippage_bps=slippage_bps,
                                   size_fraction=float(spec.get("size_fraction", 1.0)),
                                   product="CNC", exchange=exch)
            report = overfit_report(is_res.metrics, oos_res.metrics, n_params=n_params,
                                    min_trades_oos=min_trades_oos)
            passed = (not report.rejected) and oos_res.metrics.get("total_return", 0.0) > 0
            per_symbol[name] = SymbolVerdict(
                sym, passed, is_res.metrics, oos_res.metrics, report,
                f"{'PASS' if passed else 'FAIL'} flags={report.flags} "
                f"oos_ret={oos_res.metrics.get('total_return'):.4f}")
        except Exception as e:  # noqa: BLE001 — never let one symbol crash the gate
            per_symbol[name] = SymbolVerdict(sym, False, {}, {}, None,
                                             f"backtest error: {e}")

    deployed = [name for name, v in per_symbol.items() if v.passed]
    passed = len(deployed) >= min_symbols
    notes = (f"deployed on {len(deployed)}/{len(frames)} symbols "
             f"(min_symbols={min_symbols}). n_params={n_params}.")
    if not deployed:
        notes += " No profitable symbol → reject to graveyard."

    return ResearchVerdict(spec_id=spec_id, n_params=n_params, per_symbol=per_symbol,
                           deployed_symbols=deployed, passed=passed, notes=notes)
