"""Improvement loop = the curve-fitting guard (Phase 11, RESEARCHER-SPEC §8.5/§10).

These tests are deliberately STRICT. The acceptance rule must reject IS-only gains and any
variant that adds a knob, and must keep the incumbent when nothing beats it OOS. We inject a
deterministic fake `evaluate` so the acceptance LOGIC is tested in isolation from backtest
noise; one test also exercises the real `evaluate_spec` end-to-end.
"""
import numpy as np
import pandas as pd
import pytest

from backtest.optimize import (
    optimize_strategy, oos_score, is_mediocre, local_search_variants,
    MAX_VARIANTS_PER_STRATEGY, IMPROVE_MARGIN,
)
from backtest.research import ResearchVerdict, SymbolVerdict


INCUMBENT = {
    "id": "inc", "name": "Incumbent", "families": ["momentum"], "timeframe": "day",
    "entry": {"all": [{"pred": "rsi_below", "length": 14, "threshold": 30}]},
    "exit": {"any": [{"pred": "rsi_above", "length": 14, "threshold": 55}]},
    "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0,
}


def _verdict(spec_id, n_params, oos_return, *, passed=True, symbol="NSE:UP"):
    """Build a ResearchVerdict with one deployed symbol carrying the given OOS return."""
    if passed:
        per = {symbol: SymbolVerdict(symbol, True, {"total_return": 0.5},
                                     {"total_return": oos_return, "trades": 40}, None, "")}
        deployed = [symbol]
    else:
        per = {symbol: SymbolVerdict(symbol, False, {}, {"total_return": oos_return}, None, "")}
        deployed = []
    return ResearchVerdict(spec_id=spec_id, n_params=n_params, per_symbol=per,
                           deployed_symbols=deployed, passed=passed)


def _fake_eval(verdicts_by_id):
    """Return an `evaluate(spec, frames, **kw)` that looks the verdict up by spec id."""
    def evaluate(spec, frames, **kw):
        return verdicts_by_id[spec["id"]]
    return evaluate


# ---- IS-only improvement is rejected ----------------------------------------
def test_is_only_improvement_rejected():
    # incumbent OOS 0.05; the only variant has BETTER IS but SAME/worse OOS → must reject.
    inc_v = _verdict("inc", 2, 0.05)
    variant = {**INCUMBENT, "id": "inc_v1", "entry":
               {"all": [{"pred": "rsi_below", "length": 14, "threshold": 35}]}}
    var_v = _verdict("inc_v1", 2, 0.04)  # OOS no better (IS gain is irrelevant to acceptance)
    res = optimize_strategy(INCUMBENT, frames={}, evaluate=_fake_eval({"inc": inc_v, "inc_v1": var_v}),
                            llm_variants=[variant], max_variants=1)
    assert res.accepted is False
    assert res.best_spec["id"] == "inc"
    assert res.trials[0].accepted is False


# ---- knob-adding variant rejected even if OOS improves ----------------------
def test_knob_adding_variant_rejected_even_if_oos_improves():
    inc_v = _verdict("inc", 2, 0.05)
    # variant adds a knob (n_params 3) AND improves OOS a lot — must STILL be rejected.
    variant = {**INCUMBENT, "id": "inc_k",
               "entry": {"all": [{"pred": "rsi_below", "length": 14, "threshold": 30},
                                 {"pred": "adx_above", "length": 14, "threshold": 25}]}}
    var_v = _verdict("inc_k", 3, 0.50)   # big OOS gain
    res = optimize_strategy(INCUMBENT, frames={}, evaluate=_fake_eval({"inc": inc_v, "inc_k": var_v}),
                            llm_variants=[variant], max_variants=1)
    assert res.accepted is False
    assert "adds knobs" in res.trials[0].reason
    assert res.best_spec["id"] == "inc"


# ---- a clean OOS improvement with no extra knobs IS accepted ----------------
def test_oos_improvement_no_extra_knobs_accepted():
    inc_v = _verdict("inc", 2, 0.05)
    variant = {**INCUMBENT, "id": "inc_better",
               "exit": {"any": [{"pred": "rsi_above", "length": 14, "threshold": 60}]}}
    var_v = _verdict("inc_better", 2, 0.05 + IMPROVE_MARGIN + 0.01)  # clears margin
    res = optimize_strategy(INCUMBENT, frames={}, evaluate=_fake_eval({"inc": inc_v, "inc_better": var_v}),
                            llm_variants=[variant], max_variants=1)
    assert res.accepted is True
    assert res.best_spec["id"] == "inc_better"
    assert res.trials[0].accepted is True


# ---- sub-margin (noise-sized) OOS gain is rejected --------------------------
def test_submargin_gain_rejected():
    inc_v = _verdict("inc", 2, 0.05)
    variant = {**INCUMBENT, "id": "inc_noise"}
    var_v = _verdict("inc_noise", 2, 0.05 + IMPROVE_MARGIN / 2)  # below margin
    res = optimize_strategy(INCUMBENT, frames={}, evaluate=_fake_eval({"inc": inc_v, "inc_noise": var_v}),
                            llm_variants=[variant], max_variants=1)
    assert res.accepted is False


# ---- no variant beats incumbent → unchanged ---------------------------------
def test_no_variant_beats_incumbent_keeps_it():
    inc_v = _verdict("inc", 2, 0.20)  # already strong
    variants = [{**INCUMBENT, "id": f"v{i}"} for i in range(3)]
    vmap = {"inc": inc_v}
    for i, v in enumerate(variants):
        vmap[v["id"]] = _verdict(v["id"], 2, 0.10)  # all worse OOS
    res = optimize_strategy(INCUMBENT, frames={}, evaluate=_fake_eval(vmap),
                            llm_variants=variants, max_variants=3)
    assert res.accepted is False
    assert res.best_spec["id"] == "inc"
    assert "kept as-is" in res.note


# ---- variant count is capped and the drop is logged -------------------------
def test_variant_count_capped_and_logged():
    inc_v = _verdict("inc", 2, 0.05)
    variants = [{**INCUMBENT, "id": f"v{i}"} for i in range(10)]  # 10 > cap
    vmap = {"inc": inc_v}
    for v in variants:
        vmap[v["id"]] = _verdict(v["id"], 2, 0.04)
    res = optimize_strategy(INCUMBENT, frames={}, evaluate=_fake_eval(vmap),
                            llm_variants=variants, max_variants=4)
    assert len(res.trials) == 4          # only the cap evaluated
    assert res.dropped == 6              # the rest counted, not silently dropped
    assert "dropped" in res.note


# ---- non-deploying variant rejected (hard gate) -----------------------------
def test_nondeploying_variant_rejected():
    inc_v = _verdict("inc", 2, 0.05)
    variant = {**INCUMBENT, "id": "inc_dead"}
    var_v = _verdict("inc_dead", 2, 9.99, passed=False)  # huge "OOS" but deploys on nothing
    res = optimize_strategy(INCUMBENT, frames={}, evaluate=_fake_eval({"inc": inc_v, "inc_dead": var_v}),
                            llm_variants=[variant], max_variants=1)
    assert res.accepted is False
    assert "hard gate" in res.trials[0].reason


# ---- local search produces valid, knob-free variants ------------------------
def test_local_search_no_added_knobs():
    from agent.strategy_spec import count_params
    base = count_params(INCUMBENT)
    variants = local_search_variants(INCUMBENT, max_variants=MAX_VARIANTS_PER_STRATEGY)
    assert variants, "local search should produce variants"
    assert len(variants) <= MAX_VARIANTS_PER_STRATEGY
    for v in variants:
        assert count_params(v) <= base   # never adds a knob


# ---- helpers ----------------------------------------------------------------
def test_oos_score_and_mediocre():
    assert oos_score(_verdict("x", 2, 0.05)) == pytest.approx(0.05)
    assert oos_score(_verdict("x", 2, 9.0, passed=False)) == float("-inf")
    assert is_mediocre(_verdict("x", 2, 0.05)) is True       # profitable but < 0.10
    assert is_mediocre(_verdict("x", 2, 0.30)) is False      # strong, leave alone
    assert is_mediocre(_verdict("x", 2, -0.1, passed=False)) is False  # not passing


# ---- end-to-end with the REAL gate ------------------------------------------
def _trend(n=300, drift=0.6, seed=2, start=100.0):
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(np.full(n, drift) + rng.normal(0, 0.4, n))
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5,
                         "close": close, "volume": np.full(n, 1e5)}, index=idx)


def test_real_evaluate_does_not_crash_and_is_honest():
    spec = {"id": "lb", "name": "lb", "families": ["trend"], "timeframe": "day",
            "entry": {"all": [{"pred": "price_above_ma", "length": 5, "kind": "sma"}]},
            "exit": {"any": [{"pred": "price_below_ma", "length": 5, "kind": "sma"}]},
            "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0}
    frames = {"NSE:UP": _trend(drift=0.6, seed=2)}
    res = optimize_strategy(spec, frames, max_variants=3, eval_kwargs={"min_trades_oos": 1})
    # honest result either way; just must not crash and must log its trials
    assert isinstance(res.accepted, bool)
    assert len(res.trials) <= 3
