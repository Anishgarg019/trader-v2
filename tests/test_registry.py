"""Strategy registry: spec round-trip, forward-test-only load, bad-spec skip+alert,
coverage (Phase 11, RESEARCHER-SPEC §6/§10)."""
import numpy as np
import pandas as pd
import pytest

from agent.registry import StrategyRegistry, flatten_params, FORWARD_TEST
from backtest.research import evaluate_spec
from vault.writer import VaultWriter


def _trend(n=300, drift=0.6, seed=2, start=100.0):
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(np.full(n, drift) + rng.normal(0, 0.4, n))
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5,
                         "close": close, "volume": np.full(n, 1e5)}, index=idx)


LONGBIAS = {
    "id": "tb", "name": "Trend Bias", "families": ["trend"], "timeframe": "day",
    "entry": {"all": [{"pred": "price_above_ma", "length": 5, "kind": "sma"}]},
    "exit": {"any": [{"pred": "price_below_ma", "length": 5, "kind": "sma"}]},
    "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0,
}


@pytest.fixture
def vault(tmp_path):
    v = VaultWriter(tmp_path / "vault")
    v.ensure_structure()
    return v


def test_flatten_params():
    fp = flatten_params(LONGBIAS)
    assert fp["atr_k"] == 2.0 and fp["atr_len"] == 14
    assert fp["entry.price_above_ma.length"] == 5
    assert fp["exit.price_below_ma.kind"] == "sma"


def test_spec_round_trips_through_frontmatter(vault):
    reg = StrategyRegistry(vault)
    frames = {"NSE:UP": _trend(drift=0.6, seed=2), "NSE:DOWN": _trend(drift=-0.6, seed=3)}
    verdict = evaluate_spec(LONGBIAS, frames, min_trades_oos=1)
    assert verdict.deployed_symbols == ["NSE:UP"]
    note_rel = reg.write_spec_note(verdict, LONGBIAS, created="2026-05-31")

    fm, body = vault.read_note(note_rel)
    assert fm["status"] == FORWARD_TEST
    assert fm["deployed_symbols"] == ["NSE:UP"]
    assert fm["spec"]["id"] == "tb"
    assert fm["spec"]["entry"] == LONGBIAS["entry"]
    assert "Win/loss by symbol" in body

    active = reg.load_active_specs()
    assert len(active) == 1
    assert active[0].spec["id"] == "tb"
    assert active[0].deployed_symbols == ["NSE:UP"]


def test_load_filters_by_status(vault):
    reg = StrategyRegistry(vault)
    # a 'researching' note must NOT load as active
    vault.write_strategy_note(strategy_id="r1", name="Researching One", status="researching",
                              frontmatter_extra={"spec": LONGBIAS, "deployed_symbols": ["NSE:UP"]})
    # a 'live' note (should never exist, but if hand-created) must NOT load (invariant #3)
    vault.write_strategy_note(strategy_id="l1", name="Live One", status="live",
                              frontmatter_extra={"spec": LONGBIAS, "deployed_symbols": ["NSE:UP"]})
    # a forward-test note SHOULD load
    vault.write_strategy_note(strategy_id="f1", name="Forward One", status=FORWARD_TEST,
                              frontmatter_extra={"spec": {**LONGBIAS, "id": "f1"},
                                                 "deployed_symbols": ["NSE:UP"]})
    active = reg.load_active_specs()
    ids = {a.spec["id"] for a in active}
    assert ids == {"f1"}


def test_bad_handedited_spec_skipped_and_alerts(vault):
    reg = StrategyRegistry(vault)
    bad_spec = {"id": "bad", "timeframe": "day",
                "entry": {"all": [{"pred": "teleport"}]},  # invalid predicate
                "exit": {"any": [{"pred": "rsi_above", "length": 14, "threshold": 60}]},
                "atr_k": 2.0}
    vault.write_strategy_note(strategy_id="bad", name="Bad Spec", status=FORWARD_TEST,
                              frontmatter_extra={"spec": bad_spec, "deployed_symbols": ["NSE:UP"]})
    active = reg.load_active_specs()
    assert active == []  # skipped, not run
    # a system-alert was written
    alerts = list((vault.root / "System" / "alerts").glob("*.md"))
    assert any("bad-spec" in p.name for p in alerts)


def test_coverage_returns_uncovered(vault):
    reg = StrategyRegistry(vault)
    universe = ["NSE:UP", "NSE:DOWN", "NSE:FLAT", "NSE:OTHER"]
    frames = {"NSE:UP": _trend(drift=0.6, seed=2), "NSE:DOWN": _trend(drift=-0.6, seed=3)}
    verdict = evaluate_spec(LONGBIAS, frames, min_trades_oos=1)
    reg.write_spec_note(verdict, LONGBIAS, created="2026-05-31")
    active = reg.load_active_specs()
    cov = reg.coverage(active, universe)
    assert cov.covered == {"NSE:UP"}
    assert set(cov.uncovered) == {"NSE:DOWN", "NSE:FLAT", "NSE:OTHER"}


def test_registry_refuses_live_status(vault):
    reg = StrategyRegistry(vault)
    frames = {"NSE:UP": _trend(drift=0.6, seed=2)}
    verdict = evaluate_spec(LONGBIAS, frames, min_trades_oos=1)
    with pytest.raises(ValueError, match="live"):
        reg.write_spec_note(verdict, LONGBIAS, status="live")


def test_graveyard_note_not_loaded_as_active(vault):
    reg = StrategyRegistry(vault)
    frames = {"NSE:UP": _trend(drift=0.6, seed=2)}
    verdict = evaluate_spec(LONGBIAS, frames, min_trades_oos=1)
    # write to graveyard with forward-test status — still excluded (it's in Graveyard/)
    reg.write_spec_note(verdict, LONGBIAS, status=FORWARD_TEST, graveyard=True)
    active = reg.load_active_specs()
    assert active == []
