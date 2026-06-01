"""Maintained research digest: rollup dedup, idempotency, pointers, coverage, rebuild
(CONTEXT-DIGEST-SPEC §2/§3/§5)."""
import pytest

from vault.digest import ResearchDigest, DIGEST_TOKEN_CAP, MAX_REJECTED_EXAMPLES
from vault.writer import VaultWriter


def _spec(sid, entry_preds, exit_preds, families=("mean-reversion",)):
    return {"id": sid, "name": sid, "families": list(families), "timeframe": "day",
            "entry": {"all": entry_preds}, "exit": {"any": exit_preds},
            "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0}


RSI = lambda sid: _spec(sid, [{"pred": "rsi_below", "length": 14, "threshold": 30}],
                        [{"pred": "rsi_above", "length": 14, "threshold": 55}])
RSI_VARIANT = lambda sid: _spec(sid, [{"pred": "rsi_below", "length": 14, "threshold": 25}],
                                [{"pred": "rsi_above", "length": 14, "threshold": 60}])  # param-only
NEW_STRUCT = lambda sid: _spec(sid, [{"pred": "rsi_below", "length": 14, "threshold": 30},
                                     {"pred": "adx_above", "length": 14, "threshold": 25}],
                               [{"pred": "rsi_above", "length": 14, "threshold": 55}])


@pytest.fixture
def dg(tmp_path):
    root = tmp_path / "vault"
    VaultWriter(root).ensure_structure()
    return ResearchDigest(root)


# ---- active + coverage ------------------------------------------------------
def test_update_active_and_coverage(dg):
    dg.set_universe(["NSE:UP", "NSE:DOWN", "NSE:FLAT"])
    dg.update_active(spec=RSI("s001"), deployed_symbols=["NSE:UP"],
                     note_rel="Strategies/s001.md")
    data = dg.load()
    assert len(data["active"]) == 1
    assert data["coverage"]["covered"] == ["NSE:UP"]
    assert set(data["coverage"]["uncovered"]) == {"NSE:DOWN", "NSE:FLAT"}


def test_update_active_idempotent_by_id(dg):
    dg.set_universe(["NSE:UP"])
    for _ in range(3):
        dg.update_active(spec=RSI("s001"), deployed_symbols=["NSE:UP"],
                         note_rel="Strategies/s001.md")
    assert len(dg.load()["active"]) == 1   # upsert, not append


def test_remove_active(dg):
    dg.set_universe(["NSE:UP"])
    dg.update_active(spec=RSI("s001"), deployed_symbols=["NSE:UP"], note_rel="Strategies/s001.md")
    dg.remove_active("s001")
    data = dg.load()
    assert data["active"] == []
    assert data["coverage"]["covered"] == []


# ---- rejected rollup --------------------------------------------------------
def test_merge_rejected_rolls_up_by_key(dg):
    # two DIFFERENT rejected specs, same structure+family+symbols → ONE bucket, tried=2
    dg.merge_rejected(spec=RSI("s002"), symbols=["NSE:UP"],
                      note_rel="Strategies/Graveyard/s002.md", lesson="failed OOS", when="2026-05-01")
    dg.merge_rejected(spec=RSI_VARIANT("s003"), symbols=["NSE:UP"],
                      note_rel="Strategies/Graveyard/s003.md", lesson="failed OOS", when="2026-05-02")
    rej = dg.load()["rejected"]
    assert len(rej) == 1
    assert rej[0]["tried"] == 2
    assert rej[0]["last"] == "2026-05-02"
    assert len(rej[0]["examples"]) == 2


def test_merge_rejected_new_structure_new_bucket(dg):
    dg.merge_rejected(spec=RSI("s002"), symbols=["NSE:UP"], note_rel="g/s002.md", when="2026-05-01")
    dg.merge_rejected(spec=NEW_STRUCT("s003"), symbols=["NSE:UP"], note_rel="g/s003.md", when="2026-05-02")
    assert len(dg.load()["rejected"]) == 2   # genuinely-new structure is NOT suppressed


def test_merge_rejected_idempotent_on_same_note(dg):
    for _ in range(3):
        dg.merge_rejected(spec=RSI("s002"), symbols=["NSE:UP"],
                          note_rel="Strategies/Graveyard/s002.md", when="2026-05-01")
    rej = dg.load()["rejected"]
    assert len(rej) == 1 and rej[0]["tried"] == 1   # same note → counted once


def test_rejected_examples_capped(dg):
    for i in range(6):
        dg.merge_rejected(spec=RSI(f"s{i:03d}"), symbols=["NSE:UP"],
                          note_rel=f"Strategies/Graveyard/s{i:03d}.md", when="2026-05-01")
    b = dg.load()["rejected"][0]
    assert b["tried"] == 6
    assert len(b["examples"]) == MAX_REJECTED_EXAMPLES   # rolled up, bounded


def test_counts_monotonic(dg):
    counts = []
    for i in range(5):
        dg.merge_rejected(spec=RSI(f"s{i}"), symbols=["NSE:UP"],
                          note_rel=f"g/s{i}.md", when="2026-05-01")
        counts.append(dg.load()["rejected"][0]["tried"])
    assert counts == sorted(counts)   # never decreases


# ---- rebuild ----------------------------------------------------------------
def _write_strategy(vault, sid, status, deployed, graveyard=False, tested=None):
    spec = RSI(sid)
    vault.write_strategy_note(
        strategy_id=sid, name=sid, status=status, families=["mean-reversion"],
        graveyard=graveyard,
        frontmatter_extra={"spec": spec, "deployed_symbols": deployed,
                           "tested_symbols": tested or []})


def test_rebuild_from_vault_is_idempotent(tmp_path):
    root = tmp_path / "vault"
    vault = VaultWriter(root); vault.ensure_structure()
    vault.write_note("Universe/current-universe.md",
                     {"type": "universe", "names": ["NSE:UP", "NSE:DOWN"]}, "x")
    # avoid the wired auto-updates interfering: write raw notes then rebuild from scratch
    _write_strategy(vault, "s001", "forward-test", ["NSE:UP"])
    _write_strategy(vault, "s002", "rejected", [], graveyard=True, tested=["NSE:UP", "NSE:DOWN"])
    _write_strategy(vault, "s003", "rejected", [], graveyard=True, tested=["NSE:UP", "NSE:DOWN"])

    dg = ResearchDigest(root)
    first = dg.rebuild_from_vault()
    # strip volatile fields then compare a second rebuild
    second = dg.rebuild_from_vault()
    for d in (first, second):
        d.pop("generated", None); d.pop("rebuilt", None); d.pop("budget_tokens", None)
    assert first == second
    assert len(first["active"]) == 1
    assert first["active"][0]["id"] == "s001"
    assert len(first["rejected"]) == 1 and first["rejected"][0]["tried"] == 2
    assert first["coverage"]["covered"] == ["NSE:UP"]
    assert first["coverage"]["uncovered"] == ["NSE:DOWN"]


def test_pointers_resolve(tmp_path):
    root = tmp_path / "vault"
    vault = VaultWriter(root); vault.ensure_structure()
    vault.write_note("Universe/current-universe.md",
                     {"type": "universe", "names": ["NSE:UP"]}, "x")
    _write_strategy(vault, "s001", "forward-test", ["NSE:UP"])
    _write_strategy(vault, "s002", "rejected", [], graveyard=True, tested=["NSE:UP"])
    dg = ResearchDigest(root)
    data = dg.rebuild_from_vault()
    # every active note pointer + every rejected example pointer must exist on disk
    assert (root / data["active"][0]["note"]).exists()
    for b in data["rejected"]:
        for ptr in b["examples"]:
            assert (root / ptr).exists()


def test_render_and_measure(dg):
    dg.set_universe(["NSE:UP"])
    dg.update_active(spec=RSI("s001"), deployed_symbols=["NSE:UP"], note_rel="Strategies/s001.md")
    text = dg.render(dg.load())
    assert "research-digest" in text
    assert "Uncovered" in text
    assert dg.measure() > 0
    assert dg.measure() <= DIGEST_TOKEN_CAP


def test_retired_strategy_folds_into_rejected_rollup(tmp_path):
    """A retired strategy (stays in Strategies/, status=retired) must appear in the rejected
    rollup so its lesson isn't lost (completeness-critic finding 2026-06-01)."""
    root = tmp_path / "vault"
    vault = VaultWriter(root); vault.ensure_structure()
    vault.write_note("Universe/current-universe.md",
                     {"type": "universe", "names": ["NSE:UP"]}, "x")
    vault.write_strategy_note(strategy_id="s001", name="s001", status="retired",
                              families=["mean-reversion"],
                              frontmatter_extra={"spec": RSI("s001"), "deployed_symbols": [],
                                                 "tested_symbols": ["NSE:UP"]})
    data = ResearchDigest(root).rebuild_from_vault()
    assert data["active"] == []                       # retired → not active
    assert len(data["rejected"]) == 1                 # but recorded as a tried/failed family
    assert "retired" in data["rejected"][0]["lesson"]
    assert "Strategies/s001" in data["rejected"][0]["examples"][0]
