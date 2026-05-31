"""Execution: the order → stop → note sequence, with governor/sizing/safety gates."""
import pytest

from agent.broker.paper_broker import PaperBroker
from agent.broker.kite_client import KiteDataClient
from agent.execution import ExecutionEngine
from agent.governor import evaluate_drawdown
from vault.writer import VaultWriter


@pytest.fixture
def engine(tmp_path, fake_kite):
    broker = PaperBroker(starting_cash=100000.0, price_fn=lambda s: 100.0)
    vault = VaultWriter(tmp_path)
    vault.ensure_structure()
    kite = KiteDataClient(kite=fake_kite)
    return ExecutionEngine(broker, vault, kite_client=kite, mode="paper")


def entry_kwargs(**over):
    base = dict(symbol="RELIANCE", exchange="NSE", strategy_id="s001",
                strategy_link="[[s001 - rsi-meanrev]]", last_price=100.0, atr=2.0,
                equity=100000.0, available_cash=100000.0, k=2.0,
                justification="- Ensemble: RSI<30 + price>SMA200\n- Risk: R=...",
                d="2026-05-29")
    base.update(over)
    return base


def test_entry_places_order_stop_and_note(engine):
    res = engine.execute_entry(**entry_kwargs())
    assert res.status == "placed"
    assert res.order_id and res.stop_trigger_id is not None and res.trade_note
    assert res.qty == 200 and res.fill_price == 100.0 and res.stop_price == 96.0

    # order filled in the paper book
    assert engine.broker.get_order(res.order_id)["status"] == "COMPLETE"
    # protective stop present
    assert any(g["trigger_id"] == res.stop_trigger_id for g in engine.broker.get_gtts())
    # position opened
    pos = engine.broker.get_positions()
    assert pos[0]["quantity"] == 200
    # trade note written with the right fields
    fm, body = engine.vault.read_note(
        engine.vault.trade_rel("2026-05-29", "RELIANCE", "s001"))
    assert fm["type"] == "trade" and fm["status"] == "open"
    assert fm["entry_price"] == 100.0 and fm["quantity"] == 200 and fm["stop_price"] == 96.0
    assert fm["order_tag"] == "SYS-s001"
    assert "Ensemble" in body


def test_governor_halt_blocks_entry(engine):
    gov = evaluate_drawdown(94000, 100000, 100000)  # -6% daily → halt
    res = engine.execute_entry(**entry_kwargs(governor_decision=gov))
    assert res.status == "skipped" and "governor" in res.reason
    assert engine.broker.get_orders() == []  # nothing placed


def test_sizing_skip_blocks_entry(engine):
    res = engine.execute_entry(**entry_kwargs(available_cash=50.0))  # cash_cap_qty = 0
    assert res.status == "skipped" and "sizing" in res.reason
    assert engine.broker.get_orders() == []


def test_safety_violation_halts_and_alerts(tmp_path, fake_kite):
    broker = PaperBroker(price_fn=lambda s: 100.0)
    vault = VaultWriter(tmp_path); vault.ensure_structure()
    eng = ExecutionEngine(broker, vault, kite_client=KiteDataClient(kite=fake_kite),
                          mode="live")  # not paper → must halt
    res = eng.execute_entry(**entry_kwargs())
    assert res.status == "halted"
    assert vault.exists(vault.alert_rel("2026-05-29", "paper-guard"))


def test_close_position_updates_note(engine):
    opened = engine.execute_entry(**entry_kwargs())
    rel = engine.vault.trade_rel("2026-05-29", "RELIANCE", "s001")
    res = engine.close_position(symbol="RELIANCE", exchange="NSE", quantity=opened.qty,
                                last_price=110.0, trade_note_rel=rel,
                                entry_price=opened.fill_price, d="2026-05-29")
    assert res.status == "placed" and res.fill_price == 110.0
    fm, _ = engine.vault.read_note(rel)
    assert fm["status"] == "closed" and fm["exit_price"] == 110.0
    assert fm["outcome"] == "win"
    assert fm["pnl_rupees"] == pytest.approx((110 - 100) * 200)
