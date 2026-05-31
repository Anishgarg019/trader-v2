"""Researcher reads proposal context from the digest only — no full-vault scan in the
proposal path (CONTEXT-DIGEST-SPEC §8 / refactor smoke)."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agent.registry import StrategyRegistry
from vault.digest import ResearchDigest
from vault.writer import VaultWriter

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "researcher.py"


def _load():
    spec = importlib.util.spec_from_file_location("researcher", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["researcher"] = mod
    spec.loader.exec_module(mod)
    return mod


researcher = _load()


def _trend(n=300, drift=0.6, seed=2):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(np.full(n, drift) + rng.normal(0, 0.4, n))
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


def test_proposal_context_comes_from_digest(setup):
    vault, reg, universe, frames = setup
    captured = {}

    def proposer(ctx, n):
        captured["ctx"] = ctx
        return []   # propose nothing — we only care about the context source

    researcher.run_researcher(registry=reg, universe=universe, frames=frames,
                              proposer=proposer, cadence="daily",
                              eval_kwargs={"min_trades_oos": 1})
    ctx = captured["ctx"]
    # context fields are exactly the digest's decision inputs
    assert set(ctx["UNCOVERED_SYMBOLS"]) == {"NSE:UP", "NSE:DOWN"}
    assert ctx["UNIVERSE"] == universe
    assert ctx["ACTIVE_STRATEGIES"] == []
    assert ctx["GRAVEYARD"] == []   # rolled-up buckets (none yet), not a list of note ids


def test_graveyard_context_is_rolled_up_not_raw_ids(setup):
    vault, reg, universe, frames = setup
    # seed a rejection (writer wiring rolls it into the digest)
    rej_spec = {**LONGBIAS, "id": "s050"}
    vault.write_strategy_note(strategy_id="s050", name="s050", status="rejected",
                              families=["trend"], graveyard=True,
                              backtest_log="failed gate: profitable on 0/2 symbols OOS",
                              frontmatter_extra={"spec": rej_spec, "deployed_symbols": [],
                                                 "tested_symbols": universe})
    captured = {}
    researcher.run_researcher(registry=reg, universe=universe, frames=frames,
                              proposer=lambda c, n: captured.setdefault("ctx", c) and [],
                              cadence="daily", eval_kwargs={"min_trades_oos": 1})
    gy = captured["ctx"]["GRAVEYARD"]
    assert len(gy) == 1
    assert "key" in gy[0] and "tried" in gy[0] and "lesson" in gy[0]   # rollup, not raw ids


def test_no_full_vault_scan_in_proposal_path(setup, monkeypatch):
    """The proposal path must NOT call the O(vault) graveyard scan; coverage/graveyard come
    from the digest. (Load-bearing existing_ids() is only hit at deploy time.)"""
    vault, reg, universe, frames = setup
    called = {"graveyard_ids": 0, "coverage": 0}
    orig_gy = reg.graveyard_ids
    orig_cov = reg.coverage
    monkeypatch.setattr(reg, "graveyard_ids",
                        lambda *a, **k: called.__setitem__("graveyard_ids", called["graveyard_ids"] + 1) or orig_gy())
    monkeypatch.setattr(reg, "coverage",
                        lambda *a, **k: called.__setitem__("coverage", called["coverage"] + 1) or orig_cov(*a, **k))
    researcher.run_researcher(registry=reg, universe=universe, frames=frames,
                              proposer=lambda c, n: [], cadence="daily",
                              eval_kwargs={"min_trades_oos": 1})
    assert called["graveyard_ids"] == 0   # graveyard scan replaced by the digest
    assert called["coverage"] == 0        # coverage replaced by the digest


def test_digest_token_count_surfaced(setup):
    vault, reg, universe, frames = setup
    s = researcher.run_researcher(registry=reg, universe=universe, frames=frames,
                                  proposer=lambda c, n: [], cadence="daily",
                                  eval_kwargs={"min_trades_oos": 1})
    assert s.digest_tokens > 0
