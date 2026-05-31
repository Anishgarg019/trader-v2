"""Periodic rebuild + drift detection (CONTEXT-DIGEST-SPEC §5). An intentionally-corrupted
incremental digest must be caught by the rebuild-and-diff."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from vault.digest import ResearchDigest, digest_diff
from vault.writer import VaultWriter

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "researcher.py"


def _load_researcher():
    spec = importlib.util.spec_from_file_location("researcher", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["researcher"] = mod
    spec.loader.exec_module(mod)
    return mod


def _spec(sid, families=("trend",)):
    return {"id": sid, "name": sid, "families": list(families), "timeframe": "day",
            "entry": {"all": [{"pred": "price_above_ma", "length": 5, "kind": "sma"}]},
            "exit": {"any": [{"pred": "price_below_ma", "length": 5, "kind": "sma"}]},
            "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0}


def _seed_vault(tmp_path):
    vault = VaultWriter(tmp_path / "vault"); vault.ensure_structure()
    vault.write_note("Universe/current-universe.md",
                     {"type": "universe", "names": ["NSE:UP", "NSE:DOWN"]}, "x")
    vault.write_strategy_note(strategy_id="s001", name="s001", status="forward-test",
                              families=["trend"],
                              frontmatter_extra={"spec": _spec("s001"),
                                                 "deployed_symbols": ["NSE:UP"],
                                                 "tested_symbols": ["NSE:UP", "NSE:DOWN"]})
    vault.write_strategy_note(strategy_id="s050", name="s050", status="rejected",
                              families=["trend"], graveyard=True,
                              backtest_log="failed gate",
                              frontmatter_extra={"spec": _spec("s050"), "deployed_symbols": [],
                                                 "tested_symbols": ["NSE:UP", "NSE:DOWN"]})
    return vault


def test_in_sync_digest_has_no_drift(tmp_path):
    vault = _seed_vault(tmp_path)
    dg = ResearchDigest(vault.root)
    incremental = dg.load()                 # maintained by the writer wiring
    rebuilt = dg.rebuild_from_vault()
    assert digest_diff(incremental, rebuilt) == []


def test_corrupted_incremental_is_caught(tmp_path):
    vault = _seed_vault(tmp_path)
    dg = ResearchDigest(vault.root)
    # corrupt the incremental digest: drop the active entry + zero a rejected count
    bad = dg.load()
    bad["active"] = []
    for b in bad["rejected"]:
        b["tried"] = 0
    rebuilt = dg.rebuild_from_vault()
    diffs = digest_diff(bad, rebuilt)
    assert diffs                                   # divergence detected
    assert any("active ids" in d for d in diffs)
    assert any("rejected[" in d for d in diffs)


def test_weekly_run_writes_system_alert_on_drift(tmp_path):
    researcher = _load_researcher()
    vault = _seed_vault(tmp_path)
    from agent.registry import StrategyRegistry
    reg = StrategyRegistry(vault)

    # corrupt the on-disk incremental digest BEFORE the weekly run
    dg = ResearchDigest(vault.root)
    bad = dg.load()
    bad["active"] = []                              # pretend an update was missed
    bad["generated"] = "2026-05-30"                 # non-empty so the diff path runs
    dg.save(bad)

    frames = {"NSE:UP": _trend(0.6, 2), "NSE:DOWN": _trend(-0.6, 3)}
    s = researcher.run_researcher(registry=reg, universe=["NSE:UP", "NSE:DOWN"], frames=frames,
                                  proposer=lambda c, n: [], cadence="weekly",
                                  eval_kwargs={"min_trades_oos": 1})
    assert s.digest_drift >= 1
    alerts = list((vault.root / "System" / "alerts").glob("*digest-drift*"))
    assert alerts, "a digest-drift system-alert note must be written"


def _trend(drift, seed, n=300):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(np.full(n, drift) + rng.normal(0, 0.4, n))
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5,
                         "close": close, "volume": np.full(n, 1e5)}, index=idx)
