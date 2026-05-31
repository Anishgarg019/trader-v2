"""VaultWriter write paths update the digest atomically (CONTEXT-DIGEST-SPEC §2/§8.3).
A write produces the expected digest delta; digest errors never break the note write."""
import pytest

from vault.writer import VaultWriter
from vault.digest import ResearchDigest


def _spec(sid, families=("mean-reversion",)):
    return {"id": sid, "name": sid, "families": list(families), "timeframe": "day",
            "entry": {"all": [{"pred": "rsi_below", "length": 14, "threshold": 30}]},
            "exit": {"any": [{"pred": "rsi_above", "length": 14, "threshold": 55}]},
            "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0}


@pytest.fixture
def vault(tmp_path):
    v = VaultWriter(tmp_path / "vault"); v.ensure_structure()
    return v


def test_forward_test_write_adds_active(vault):
    vault.write_note("Universe/current-universe.md",
                     {"type": "universe", "names": ["NSE:UP", "NSE:DOWN"]}, "x")
    vault.write_strategy_note(strategy_id="s001", name="s001", status="forward-test",
                              families=["mean-reversion"],
                              frontmatter_extra={"spec": _spec("s001"),
                                                 "deployed_symbols": ["NSE:UP"],
                                                 "tested_symbols": ["NSE:UP", "NSE:DOWN"]})
    data = ResearchDigest(vault.root).load()
    assert [a["id"] for a in data["active"]] == ["s001"]
    assert data["coverage"]["covered"] == ["NSE:UP"]
    assert data["coverage"]["uncovered"] == ["NSE:DOWN"]


def test_graveyard_write_merges_rejected(vault):
    vault.write_strategy_note(strategy_id="s002", name="s002", status="rejected",
                              families=["mean-reversion"], graveyard=True,
                              backtest_log="failed gate: profitable on 0/10 symbols OOS",
                              frontmatter_extra={"spec": _spec("s002"),
                                                 "deployed_symbols": [],
                                                 "tested_symbols": ["NSE:UP", "NSE:DOWN"]})
    rej = ResearchDigest(vault.root).load()["rejected"]
    assert len(rej) == 1 and rej[0]["tried"] == 1
    assert "Graveyard/s002" in rej[0]["examples"][0]
    assert rej[0]["lesson"]   # factual lesson lifted from backtest_log


def test_universe_write_sets_universe(vault):
    vault.write_note("Universe/current-universe.md",
                     {"type": "universe", "names": ["NSE:A", "NSE:B"]}, "x")
    assert ResearchDigest(vault.root).load()["universe"] == ["NSE:A", "NSE:B"]


def test_daily_write_refreshes_perf(vault):
    vault.write_daily_note(d="2026-05-29", trading_day=True, day_open_equity=100000,
                           frontmatter_extra={"day_close_equity": 101000})
    perf = ResearchDigest(vault.root).load()["perf"]
    assert perf["equity_change_pct"] == pytest.approx(0.01)


def test_legacy_note_without_spec_does_not_touch_digest(vault):
    # a non-Phase-11 strategy note (no spec:) must NOT create an active entry
    vault.write_strategy_note(strategy_id="old", name="old", status="forward-test")
    assert ResearchDigest(vault.root).load()["active"] == []


def test_digest_error_never_breaks_note_write(vault, monkeypatch):
    # force the digest mutator to blow up; the note must still be written
    def boom(*a, **k):
        raise RuntimeError("digest exploded")
    monkeypatch.setattr(vault.digest, "update_active", boom)
    p = vault.write_strategy_note(strategy_id="s009", name="s009", status="forward-test",
                                  frontmatter_extra={"spec": _spec("s009"),
                                                     "deployed_symbols": ["NSE:UP"]})
    assert p.exists()   # truth-write survived the digest failure
    fm, _ = vault.read_note(vault.strategy_rel("s009", "s009"))
    assert fm["status"] == "forward-test"
