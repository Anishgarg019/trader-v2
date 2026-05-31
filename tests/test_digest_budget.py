"""Token-budget guarantee: a large synthetic vault still yields a digest ≤ cap, by compacting
rejected buckets in priority order with NO silent truncation (CONTEXT-DIGEST-SPEC §4)."""
from vault.digest import (
    ResearchDigest, enforce_budget, DIGEST_TOKEN_CAP, _est_tokens,
)
from vault.writer import VaultWriter


def _big_data(n_rejected=800, n_active=8, n_universe=10):
    universe = [f"NSE:SYM{i:02d}" for i in range(n_universe)]
    active = [{"id": f"s{i:03d}", "family": ["trend"], "key": f"trend|ma_cross_up|SYM{i}",
               "deployed_symbols": [universe[i % n_universe]],
               "recent": {"trades_30d": 12, "win_rate_30d": 0.5, "oos_sharpe": 0.4},
               "note": f"Strategies/s{i:03d} - x.md"} for i in range(n_active)]
    rejected = [{"key": f"family{i}|rsi_above+rsi_below|SYM{i % n_universe}",
                 "tried": (i % 9) + 1, "last": f"2026-{(i % 12) + 1:02d}-15",
                 "lesson": "failed OOS — a fairly long-winded lesson string " * 2,
                 "examples": [f"Strategies/Graveyard/s{i:03d} - x.md"]} for i in range(n_rejected)]
    return {
        "type": "research-digest", "generated": "2026-05-31", "rebuilt": "2026-05-31",
        "universe": universe,
        "coverage": {"covered": [universe[0]], "uncovered": universe[1:]},
        "active": active, "rejected": rejected,
        "regime": "trend up, vol normal", "perf": {"window_days": 30},
        "budget_tokens": 0,
    }


def test_large_vault_digest_stays_under_cap():
    data = _big_data(n_rejected=800)
    assert _est_tokens(ResearchDigest(".").render(data)) > DIGEST_TOKEN_CAP  # genuinely over
    compacted = enforce_budget({**data, "rejected": list(data["rejected"])})
    rendered = ResearchDigest(".").render(compacted)
    assert _est_tokens(rendered) <= DIGEST_TOKEN_CAP


def test_compaction_preserves_decision_critical_sections():
    data = _big_data(n_rejected=800)
    compacted = enforce_budget({**data, "rejected": list(data["rejected"])})
    # uncovered (priority targets) + active (dedup value) must NOT be dropped (§7)
    assert compacted["coverage"]["uncovered"] == data["coverage"]["uncovered"]
    assert len(compacted["active"]) == len(data["active"])


def test_drop_is_logged_not_silent():
    data = _big_data(n_rejected=800)
    compacted = enforce_budget({**data, "rejected": list(data["rejected"])})
    markers = [b for b in compacted["rejected"] if str(b.get("key", "")).startswith("(+")]
    assert len(markers) == 1                      # the omitted-pointer line is present
    assert "omitted" in markers[0]["key"]
    assert markers[0]["lesson"]                    # points at the full record


def test_small_digest_not_modified():
    data = _big_data(n_rejected=3)
    before = list(data["rejected"])
    compacted = enforce_budget({**data, "rejected": list(data["rejected"])})
    assert compacted["rejected"] == before        # under cap → untouched, no compaction


def test_save_enforces_cap_and_records_tokens(tmp_path):
    root = tmp_path / "vault"
    VaultWriter(root).ensure_structure()
    dg = ResearchDigest(root)
    dg.save(_big_data(n_rejected=800))
    saved = dg.load()
    assert saved["budget_tokens"] <= DIGEST_TOKEN_CAP
    assert dg.measure(saved) <= DIGEST_TOKEN_CAP
