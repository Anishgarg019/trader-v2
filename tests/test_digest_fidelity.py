"""Fidelity checks (CONTEXT-DIGEST-SPEC §6): invariants, re-proposal/duplication telemetry,
and the periodic completeness-critic (weekly only, logged, never gates)."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from vault.digest import ResearchDigest, DIGEST_TOKEN_CAP
from vault.writer import VaultWriter

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "researcher.py"


def _load():
    spec = importlib.util.spec_from_file_location("researcher", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["researcher"] = mod
    spec.loader.exec_module(mod)
    return mod


researcher = _load()


def _spec(sid, families=("trend",)):
    return {"id": sid, "name": sid, "families": list(families), "timeframe": "day",
            "entry": {"all": [{"pred": "price_above_ma", "length": 5, "kind": "sma"}]},
            "exit": {"any": [{"pred": "price_below_ma", "length": 5, "kind": "sma"}]},
            "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0}


def _trend(drift, seed, n=300):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(np.full(n, drift) + rng.normal(0, 0.4, n))
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5,
                         "close": close, "volume": np.full(n, 1e5)}, index=idx)


@pytest.fixture
def vault(tmp_path):
    v = VaultWriter(tmp_path / "vault"); v.ensure_structure()
    v.write_note("Universe/current-universe.md",
                 {"type": "universe", "names": ["NSE:UP", "NSE:DOWN"]}, "x")
    return v


# ---- §6.1 invariants --------------------------------------------------------
def test_invariant_every_active_has_live_note(vault):
    vault.write_strategy_note(strategy_id="s001", name="s001", status="forward-test",
                              families=["trend"],
                              frontmatter_extra={"spec": _spec("s001"),
                                                 "deployed_symbols": ["NSE:UP"],
                                                 "tested_symbols": ["NSE:UP"]})
    data = ResearchDigest(vault.root).rebuild_from_vault()
    for a in data["active"]:
        p = vault.root / a["note"]
        assert p.exists()
        fm, _ = vault.read_note(a["note"])
        assert fm["status"] == "forward-test"   # active ⇒ a live forward-test note


def test_invariant_coverage_reconciles_with_deployed(vault):
    vault.write_strategy_note(strategy_id="s001", name="s001", status="forward-test",
                              families=["trend"],
                              frontmatter_extra={"spec": _spec("s001"),
                                                 "deployed_symbols": ["NSE:UP"],
                                                 "tested_symbols": ["NSE:UP"]})
    data = ResearchDigest(vault.root).rebuild_from_vault()
    union = set()
    for a in data["active"]:
        union |= set(a["deployed_symbols"])
    union &= set(data["universe"])
    assert set(data["coverage"]["covered"]) == union
    assert set(data["coverage"]["uncovered"]) == set(data["universe"]) - union


def test_invariant_budget_under_cap_after_rebuild(vault):
    data = ResearchDigest(vault.root).rebuild_from_vault()
    assert data["budget_tokens"] <= DIGEST_TOKEN_CAP


# ---- §6.3 telemetry ---------------------------------------------------------
def test_telemetry_flags_reproposed_graveyard_family(vault):
    from agent.registry import StrategyRegistry
    reg = StrategyRegistry(vault)
    # seed a rejected family in the graveyard (writer rolls it into the digest)
    vault.write_strategy_note(strategy_id="s050", name="s050", status="rejected",
                              families=["trend"], graveyard=True, backtest_log="failed",
                              frontmatter_extra={"spec": _spec("s050"), "deployed_symbols": [],
                                                 "tested_symbols": ["NSE:UP", "NSE:DOWN"]})
    frames = {"NSE:UP": _trend(0.6, 2), "NSE:DOWN": _trend(-0.6, 3)}
    # propose the SAME family+structure → telemetry should flag a re-proposal
    s = researcher.run_researcher(registry=reg, universe=["NSE:UP", "NSE:DOWN"], frames=frames,
                                  proposer=lambda c, n: [_spec("ignored", families=("trend",))],
                                  cadence="daily", eval_kwargs={"min_trades_oos": 1})
    assert s.re_proposed_rejected >= 1


def test_telemetry_flags_near_dup_of_active(vault):
    from agent.registry import StrategyRegistry
    reg = StrategyRegistry(vault)
    vault.write_strategy_note(strategy_id="s001", name="s001", status="forward-test",
                              families=["trend"],
                              frontmatter_extra={"spec": _spec("s001"),
                                                 "deployed_symbols": ["NSE:UP"],
                                                 "tested_symbols": ["NSE:UP"]})
    frames = {"NSE:UP": _trend(0.6, 2), "NSE:DOWN": _trend(-0.6, 3)}
    s = researcher.run_researcher(registry=reg, universe=["NSE:UP", "NSE:DOWN"], frames=frames,
                                  proposer=lambda c, n: [_spec("ignored", families=("trend",))],
                                  cadence="daily", eval_kwargs={"min_trades_oos": 1})
    assert s.near_dup_active >= 1


# ---- §6.2 completeness-critic ----------------------------------------------
def test_critic_runs_weekly_only(vault):
    from agent.registry import StrategyRegistry
    reg = StrategyRegistry(vault)
    calls = {"n": 0}

    def critic(digest_text, sample):
        calls["n"] += 1
        assert "research-digest" in digest_text   # it gets the rendered digest
        return "faithful"

    frames = {"NSE:UP": _trend(0.6, 2), "NSE:DOWN": _trend(-0.6, 3)}
    # daily → critic NOT called
    researcher.run_researcher(registry=reg, universe=["NSE:UP", "NSE:DOWN"], frames=frames,
                              proposer=lambda c, n: [], cadence="daily", critic=critic,
                              eval_kwargs={"min_trades_oos": 1})
    assert calls["n"] == 0
    # weekly → critic called once, finding logged
    s = researcher.run_researcher(registry=reg, universe=["NSE:UP", "NSE:DOWN"], frames=frames,
                                  proposer=lambda c, n: [], cadence="weekly", critic=critic,
                                  eval_kwargs={"min_trades_oos": 1})
    assert calls["n"] == 1
    assert any("completeness-critic" in m for m in s.messages)
