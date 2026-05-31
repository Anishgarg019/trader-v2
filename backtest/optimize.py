"""Continuous improvement loop (Phase 11, RESEARCHER-SPEC §8.5) — guarded against
curve-fitting (safety invariant #7).

Mediocre-but-profitable incumbents get a few capped, logged variants (deterministic local
search and/or LLM-proposed). Each variant is re-gated through the SAME deterministic
`evaluate_spec`. A variant is accepted ONLY if it:
  - compiles/validates,
  - adds NO knobs (n_params ≤ the incumbent's),
  - clears every HARD gate (passes per-symbol → non-empty deployed_symbols), AND
  - beats the incumbent OUT-OF-SAMPLE by at least `IMPROVE_MARGIN`.
IS-only improvement never deploys. If no variant clears the bar, the incumbent is kept
unchanged — the common, correct outcome. Every variant tried is logged (no silent search;
each extra trial inflates multiple-comparisons risk).

This module NEVER touches the safety core (risk.py / governor.py) or the cost model — only
the strategy spec's entry/exit/atr_k.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.strategy_spec import PREDICATES, COMBINATORS, ATR_K_BOUNDS, validate_spec, SpecError
from backtest.research import evaluate_spec, ResearchVerdict

# caps & thresholds (also surfaced in scripts/researcher.py §8)
MAX_VARIANTS_PER_STRATEGY = 6
IMPROVE_MARGIN = 0.02            # OOS total-return margin a variant must beat the incumbent by
IMPROVE_IF_OOS_BELOW = 0.10      # "mediocre" = profitable but mean OOS return below this

_STEP = {"threshold": 5.0, "k": 0.5, "body_frac": 0.05, "atr_k": 0.5}


def oos_score(verdict: ResearchVerdict) -> float:
    """Scalar OOS quality: mean OOS total-return across the symbols it deploys on.

    Measured OUT-OF-SAMPLE only (params were tuned on IS) — this is the anti-overfit metric.
    Empty deployment → -inf (a non-deploying variant can never win).
    """
    deployed = [verdict.per_symbol[s] for s in verdict.deployed_symbols]
    if not deployed:
        return float("-inf")
    return sum(float(v.oos_metrics.get("total_return", 0.0)) for v in deployed) / len(deployed)


def is_mediocre(verdict: ResearchVerdict, *, oos_below: float = IMPROVE_IF_OOS_BELOW) -> bool:
    """A gate-passing strategy that's profitable but unremarkable (worth trying to improve)."""
    if not verdict.passed:
        return False
    s = oos_score(verdict)
    return 0.0 < s < oos_below


# ---- variant generation (deterministic local search) ------------------------
def _step_for(name: str) -> float:
    return _STEP.get(name, 1.0)


def _tunable_paths(spec: dict):
    """Yield (path, value, lo, hi, step) for every TUNABLE knob (atr_k + leaf thresholds/
    multipliers). Path is a list of keys/indices into the spec dict (survives deepcopy)."""
    yield (["atr_k"], float(spec["atr_k"]), ATR_K_BOUNDS[0], ATR_K_BOUNDS[1], _STEP["atr_k"])

    def walk(node, prefix):
        if not isinstance(node, dict):
            return
        if "pred" in node:
            for pname, desc in PREDICATES[node["pred"]]["params"].items():
                if desc[3] and pname in node:   # tunable
                    yield prefix + [pname], node[pname], desc[1], desc[2], _step_for(pname)
            return
        for comb in COMBINATORS:
            if comb in node:
                children = node[comb]
                if isinstance(children, list):
                    for i, c in enumerate(children):
                        yield from walk(c, prefix + [comb, i])
                else:
                    yield from walk(children, prefix + [comb])

    for key in ("entry", "exit"):
        if key in spec:
            yield from walk(spec[key], [key])


def _apply(spec: dict, path: list, value) -> dict:
    out = copy.deepcopy(spec)
    node = out
    for p in path[:-1]:
        node = node[p]
    node[path[-1]] = value
    return out


def _round(v: float) -> float:
    r = round(float(v), 4)
    return int(r) if r == int(r) else r


def local_search_variants(spec: dict, max_variants: int = MAX_VARIANTS_PER_STRATEGY) -> list[dict]:
    """Perturb each tunable knob ±one step within its bounds (coordinate search).

    Adds NO knobs (only mutates existing ones), reproducible, capped. Variants that fail
    validation (e.g. a perturbation breaking fast<slow) are dropped.
    """
    out: list[dict] = []
    seen = set()
    base_id = spec.get("id", "spec")
    for path, value, lo, hi, step in _tunable_paths(spec):
        for delta in (step, -step):
            nv = _round(min(hi, max(lo, value + delta)))
            if nv == value:
                continue
            cand = _apply(spec, path, nv)
            cand["id"] = f"{base_id}_v{len(out) + 1}"
            key = repr((path, nv))
            if key in seen:
                continue
            try:
                validate_spec(cand)
            except SpecError:
                continue
            seen.add(key)
            out.append(cand)
            if len(out) >= max_variants:
                return out
    return out


# ---- acceptance + orchestration ---------------------------------------------
@dataclass
class VariantTrial:
    spec_id: str
    source: str                 # 'local' | 'llm'
    n_params: int | None
    oos: float
    passed: bool
    accepted: bool
    reason: str


@dataclass
class OptimizeResult:
    spec_id: str
    incumbent_oos: float
    accepted: bool
    best_spec: dict
    best_verdict: ResearchVerdict
    trials: list[VariantTrial] = field(default_factory=list)
    dropped: int = 0
    note: str = ""


def _accepts(cand: ResearchVerdict, incumbent_oos: float, incumbent_nparams: int,
             margin: float) -> tuple[bool, str]:
    if not cand.passed:
        return False, "fails hard gate (no profitable symbol OOS)"
    if cand.n_params > incumbent_nparams:
        return False, f"adds knobs ({cand.n_params} > {incumbent_nparams}) — rejected"
    cs = oos_score(cand)
    if cs < incumbent_oos + margin:
        return False, (f"OOS gain {cs:.4f} < incumbent {incumbent_oos:.4f} + margin {margin} "
                       "(IS-only/noise improvement not deployed)")
    return True, f"OOS {cs:.4f} ≥ incumbent {incumbent_oos:.4f} + {margin}, no added knobs"


def optimize_strategy(incumbent_spec: dict,
                      frames: dict,
                      *,
                      evaluate: Callable = evaluate_spec,
                      incumbent_verdict: ResearchVerdict | None = None,
                      llm_variants: list[dict] | None = None,
                      max_variants: int = MAX_VARIANTS_PER_STRATEGY,
                      margin: float = IMPROVE_MARGIN,
                      eval_kwargs: dict | None = None) -> OptimizeResult:
    """Try to improve one incumbent. Returns the best (incumbent unless a variant clears the
    OOS-margin/no-extra-knobs bar). Every variant evaluated is logged; capped at
    `max_variants` with the overflow count recorded (no silent truncation)."""
    eval_kwargs = eval_kwargs or {}
    spec_id = incumbent_spec.get("id", "?")
    if incumbent_verdict is None:
        incumbent_verdict = evaluate(incumbent_spec, frames, **eval_kwargs)
    inc_oos = oos_score(incumbent_verdict)
    inc_np = incumbent_verdict.n_params

    # assemble candidate variants: LLM-proposed first (capped), then deterministic local
    # search ONLY to fill the remaining slots (so we never generate variants just to drop
    # them). `dropped` counts LLM proposals that overflowed the cap (logged, not silent).
    llm = llm_variants or []
    dropped = max(0, len(llm) - max_variants)
    tagged: list[tuple[str, dict]] = [("llm", v) for v in llm[:max_variants]]
    remaining = max_variants - len(tagged)
    if remaining > 0:
        tagged += [("local", v) for v in local_search_variants(incumbent_spec, remaining)]

    best_spec, best_verdict, best_oos = incumbent_spec, incumbent_verdict, inc_oos
    accepted_any = False
    trials: list[VariantTrial] = []

    for source, vspec in tagged:
        try:
            validate_spec(vspec)
        except SpecError as e:
            trials.append(VariantTrial(vspec.get("id", "?"), source, None, float("-inf"),
                                       False, False, f"invalid spec: {e}"))
            continue
        verdict = evaluate(vspec, frames, **eval_kwargs)
        ok, reason = _accepts(verdict, inc_oos, inc_np, margin)
        cs = oos_score(verdict)
        trials.append(VariantTrial(vspec.get("id", "?"), source, verdict.n_params, cs,
                                   verdict.passed, ok, reason))
        # accept the best improving variant (must beat the INCUMBENT, measured vs inc_oos)
        if ok and cs > best_oos:
            best_spec, best_verdict, best_oos = vspec, verdict, cs
            accepted_any = True

    note = (f"{spec_id}: {'accepted a variant' if accepted_any else 'no variant beat incumbent OOS — kept as-is'}; "
            f"{len(trials)} variant(s) evaluated"
            + (f", {dropped} dropped over cap {max_variants}" if dropped else ""))
    return OptimizeResult(spec_id=spec_id, incumbent_oos=inc_oos, accepted=accepted_any,
                          best_spec=best_spec, best_verdict=best_verdict, trials=trials,
                          dropped=dropped, note=note)
