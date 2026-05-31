"""Vault writer: structure, round-trip, and exact spec §7.2–§7.4 schemas."""
import pytest

from vault.writer import VaultWriter, SUBDIRS


@pytest.fixture
def vault(tmp_path):
    w = VaultWriter(tmp_path)
    w.ensure_structure()
    return w


def test_structure_created(vault):
    for sub in SUBDIRS:
        assert (vault.root / sub).is_dir()


def test_generic_round_trip(vault):
    fm = {"type": "x", "n": 3, "ratio": 1.5, "tags": ["a", "b"], "blank": None}
    vault.write_note("Research/topic.md", fm, "## Body\nhello")
    got_fm, body = vault.read_note("Research/topic.md")
    assert got_fm == fm
    assert "hello" in body


def test_none_renders_blank_not_null(vault):
    vault.write_note("Research/n.md", {"a": None}, "x")
    text = (vault.root / "Research/n.md").read_text()
    assert "a: null" not in text
    assert "a:" in text


def test_update_frontmatter_preserves_body(vault):
    vault.write_note("Daily/d.md", {"status": "open"}, "## Section\nkeep me")
    vault.update_frontmatter("Daily/d.md", {"status": "closed", "pnl": 100})
    fm, body = vault.read_note("Daily/d.md")
    assert fm["status"] == "closed" and fm["pnl"] == 100
    assert "keep me" in body


def test_trade_note_schema(vault):
    p = vault.write_trade_note(d="2026-05-30", symbol="NSE:RELIANCE", strategy_id="s001",
                               strategy_link="[[s001 - rsi-meanrev]]",
                               frontmatter_extra={"entry_price": 1402.5, "quantity": 50,
                                                  "stop_price": 1380.0})
    assert p.name == "2026-05-30-NSE_RELIANCE-s001.md"
    fm, body = vault.read_note(vault.trade_rel("2026-05-30", "NSE:RELIANCE", "s001"))
    assert fm["type"] == "trade"
    assert fm["order_tag"] == "SYS-s001"
    assert fm["status"] == "open"
    assert fm["entry_price"] == 1402.5 and fm["quantity"] == 50
    assert "Justification" in body and "Review" in body


def test_strategy_note_schema(vault):
    vault.write_strategy_note(strategy_id="s001", name="rsi-meanrev", created="2026-05-30",
                              families=["momentum", "trend"],
                              params={"rsi_len": 14, "rsi_entry": 30, "atr_k": 1.5},
                              thesis="mean reversion in uptrends")
    fm, body = vault.read_note(vault.strategy_rel("s001", "rsi-meanrev"))
    assert fm["type"] == "strategy" and fm["id"] == "s001"
    assert fm["params"]["rsi_len"] == 14
    assert fm["backtest"]["friction_modeled"] is True
    assert "Thesis" in body and "Exact rules" in body


def test_daily_note_schema(vault):
    vault.write_daily_note(d="2026-05-30", day_open_equity=100000)
    fm, body = vault.read_note(vault.daily_rel("2026-05-30"))
    assert fm["type"] == "daily" and fm["trading_day"] is True
    assert fm["day_open_equity"] == 100000 and fm["halted"] is False
    assert "Pre-market" in body and "Research today" in body


def test_strategy_graveyard_path(vault):
    p = vault.write_strategy_note(strategy_id="s009", name="dead-idea",
                                  created="2026-05-30", status="rejected", graveyard=True)
    assert "Graveyard" in str(p)


def test_system_alert(vault):
    vault.write_system_alert(d="2026-05-30", slug="dark-universe", kind="data-anomaly",
                             detail="all 10 names stale at 10:05 IST")
    fm, body = vault.read_note(vault.alert_rel("2026-05-30", "dark-universe"))
    assert fm["type"] == "system-alert" and fm["kind"] == "data-anomaly"
    assert "stale" in body
