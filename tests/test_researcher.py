"""Researcher orchestration: propose → gate → deploy / honest zero, caps, no live
(Phase 11, RESEARCHER-SPEC §8/§10). Proposer + frames injected; no Kite, no claude."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agent.registry import StrategyRegistry, FORWARD_TEST
from vault.writer import VaultWriter

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "researcher.py"


def _load():
    spec = importlib.util.spec_from_file_location("researcher", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["researcher"] = mod   # dataclass field resolution needs the module registered
    spec.loader.exec_module(mod)
    return mod


researcher = _load()


def _trend(n=300, drift=0.6, seed=2, start=100.0):
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(np.full(n, drift) + rng.normal(0, 0.4, n))
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5,
                         "close": close, "volume": np.full(n, 1e5)}, index=idx)


LONGBIAS = {
    "name": "Trend Bias", "families": ["trend"], "timeframe": "day",
    "entry": {"all": [{"pred": "price_above_ma", "length": 5, "kind": "sma"}]},
    "exit": {"any": [{"pred": "price_below_ma", "length": 5, "kind": "sma"}]},
    "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0,
}


@pytest.fixture
def setup(tmp_path):
    vault = VaultWriter(tmp_path / "vault"); vault.ensure_structure()
    reg = StrategyRegistry(vault)
    universe = ["NSE:UP", "NSE:DOWN"]
    frames = {"NSE:UP": _trend(drift=0.6, seed=2), "NSE:DOWN": _trend(drift=-0.6, seed=3)}
    return vault, reg, universe, frames


def test_proposes_gates_and_deploys(setup):
    vault, reg, universe, frames = setup
    proposer = lambda ctx, n: [dict(LONGBIAS)]
    s = researcher.run_researcher(registry=reg, universe=universe, frames=frames,
                                  proposer=proposer, cadence="daily",
                                  eval_kwargs={"min_trades_oos": 1})
    assert s.proposed == 1 and s.valid == 1
    assert len(s.deployed) == 1
    # the deployed note is forward-test, deployed only on the winning symbol
    active = reg.load_active_specs()
    assert len(active) == 1
    assert active[0].deployed_symbols == ["NSE:UP"]
    fm, _ = vault.read_note(active[0].note_rel)
    assert fm["status"] == FORWARD_TEST
    # coverage improved; a research note was written
    assert s.coverage_after >= 1
    assert vault.exists(s.note_rel)


def test_honest_zero_when_no_passers(setup):
    vault, reg, _universe, _frames = setup
    # a long-biased strategy run on TWO downtrends loses on both → zero passers
    universe = ["NSE:D1", "NSE:D2"]
    frames = {"NSE:D1": _trend(drift=-0.6, seed=4), "NSE:D2": _trend(drift=-0.7, seed=5)}
    proposer = lambda ctx, n: [dict(LONGBIAS)]
    s = researcher.run_researcher(registry=reg, universe=universe, frames=frames,
                                  proposer=proposer, cadence="daily",
                                  eval_kwargs={"min_trades_oos": 1})
    assert s.deployed == []
    assert s.rejected == 1
    assert reg.load_active_specs() == []        # nothing deployed
    assert reg.graveyard_ids()                  # rejected proposal parked in graveyard


def test_invalid_spec_dropped(setup):
    vault, reg, universe, frames = setup
    bad = {"name": "Bad", "timeframe": "day",
           "entry": {"all": [{"pred": "teleport"}]},
           "exit": {"any": [{"pred": "rsi_above", "length": 14, "threshold": 60}]},
           "atr_k": 2.0}
    proposer = lambda ctx, n: [dict(bad)]
    s = researcher.run_researcher(registry=reg, universe=universe, frames=frames,
                                  proposer=proposer, cadence="daily",
                                  eval_kwargs={"min_trades_oos": 1})
    assert s.valid == 0 and s.deployed == []
    assert s.rejected == 1


def test_active_cap_blocks_new_proposals(setup, monkeypatch):
    vault, reg, universe, frames = setup
    monkeypatch.setattr(researcher, "MAX_ACTIVE_FORWARD_TESTS", 1)
    # deploy one first
    proposer = lambda ctx, n: [dict(LONGBIAS)]
    researcher.run_researcher(registry=reg, universe=universe, frames=frames,
                              proposer=proposer, cadence="daily",
                              eval_kwargs={"min_trades_oos": 1})
    assert len(reg.load_active_specs()) == 1
    # now at cap: proposer should not even be consulted / nothing new deployed
    called = {"n": 0}

    def counting_proposer(ctx, n):
        called["n"] += 1
        return [dict(LONGBIAS)]
    s2 = researcher.run_researcher(registry=reg, universe=universe, frames=frames,
                                   proposer=counting_proposer, cadence="daily",
                                   eval_kwargs={"min_trades_oos": 1})
    assert called["n"] == 0           # short-circuited at the cap
    assert s2.proposed == 0
    assert len(reg.load_active_specs()) == 1


def test_daily_skips_improvement(setup):
    vault, reg, universe, frames = setup
    # deploy a mediocre incumbent first
    proposer = lambda ctx, n: [dict(LONGBIAS)]
    researcher.run_researcher(registry=reg, universe=universe, frames=frames,
                              proposer=lambda c, n: [], cadence="daily")
    s = researcher.run_researcher(registry=reg, universe=universe, frames=frames,
                                  proposer=lambda c, n: [], cadence="daily")
    assert any("skipping improvement" in m for m in s.messages)


def test_never_writes_live(setup):
    vault, reg, universe, frames = setup
    proposer = lambda ctx, n: [dict(LONGBIAS)]
    researcher.run_researcher(registry=reg, universe=universe, frames=frames,
                              proposer=proposer, cadence="weekly",
                              eval_kwargs={"min_trades_oos": 1})
    for path in (vault.root / "Strategies").rglob("*.md"):
        rel = str(path.relative_to(vault.root)).replace("\\", "/")
        fm, _ = vault.read_note(rel)
        assert fm.get("status") != "live"


def test_extract_json_array_tolerates_fences():
    txt = "Here you go:\n```json\n[{\"id\":\"s9\"}]\n```\nThanks"
    assert researcher._extract_json_array(txt) == [{"id": "s9"}]
    assert researcher._extract_json_array("no array here") == []
