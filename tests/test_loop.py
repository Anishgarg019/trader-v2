"""Daily loop orchestrator: cadence, research-only gating, governor respect (spec §6/§8)."""
from datetime import datetime

import pytest

from agent.broker.paper_broker import PaperBroker
from agent.broker.kite_client import KiteDataClient
from agent.execution import ExecutionEngine
from agent.loop import Orchestrator, LoopState
from agent.trading_day import IST
from vault.writer import VaultWriter

UNIVERSE = ["NSE:RELIANCE"]
LIVE_QUOTES = {"NSE:RELIANCE": {"last_price": 100.0, "last_trade_time": "2026-05-29 10:55:00"}}


def build(tmp_path, fake_kite, cash=100000.0, mode="paper"):
    broker = PaperBroker(starting_cash=cash, price_fn=lambda s: 100.0)
    vault = VaultWriter(tmp_path); vault.ensure_structure()
    kite = KiteDataClient(kite=fake_kite)
    execu = ExecutionEngine(broker, vault, kite_client=kite, mode=mode)
    orch = Orchestrator(broker=broker, vault=vault, execution=execu, kite_client=kite,
                        universe=UNIVERSE, price_fn=lambda s: 100.0, mode=mode)
    return orch, broker, vault


def enter_strategy(ctx):
    return [{"action": "enter", "symbol": "RELIANCE", "exchange": "NSE",
             "strategy_id": "s001", "strategy_link": "[[s001 - rsi-meanrev]]",
             "last_price": 100.0, "atr": 2.0, "justification": "- Ensemble fired"}]


# ---- closed day → research-only ----
def test_closed_day_research_only(tmp_path, fake_kite):
    orch, broker, vault = build(tmp_path, fake_kite)
    now = datetime(2026, 5, 30, 11, 0, tzinfo=IST)  # Saturday
    res = orch.run_day(now, state=LoopState(), strategy_fn=enter_strategy)
    assert res.research_only is True
    assert res.blocks_run == ["research"]
    assert res.trading_day is False
    assert broker.get_orders() == []   # market block never ran
    fm, _ = vault.read_note(vault.daily_rel("2026-05-30"))
    assert fm["trading_day"] is False


# ---- open day → walks every block in order ----
def test_open_day_walks_all_blocks_and_trades(tmp_path, fake_kite):
    orch, broker, vault = build(tmp_path, fake_kite)
    now = datetime(2026, 5, 29, 11, 0, tzinfo=IST)  # Friday, during session
    res = orch.run_day(now, state=LoopState(), strategy_fn=enter_strategy,
                       quotes=LIVE_QUOTES)
    assert res.trading_day is True and res.research_only is False
    assert res.blocks_run == ["pre-market", "market", "post-market", "research"]
    assert res.actions and res.actions[0]["status"] == "placed"
    assert broker.get_positions()[0]["quantity"] == 200
    fm, _ = vault.read_note(vault.daily_rel("2026-05-29"))
    assert fm["trading_day"] is True
    assert "RELIANCE" in fm["trades_today"]


# ---- governor respected: daily halt blocks new entries (but day still walks) ----
def test_daily_halt_blocks_entries(tmp_path, fake_kite):
    orch, broker, vault = build(tmp_path, fake_kite, cash=94000.0)  # equity 94k
    state = LoopState(day_open_equity=100000.0, high_water_mark=100000.0,
                      current_date="2026-05-29")  # -6% on the day
    now = datetime(2026, 5, 29, 11, 0, tzinfo=IST)
    res = orch.run_day(now, state=state, strategy_fn=enter_strategy, quotes=LIVE_QUOTES)
    assert res.governor.halt_new_entries is True
    assert "market" in res.blocks_run            # market block still runs
    assert res.actions[0]["status"] == "skipped" and "governor" in res.actions[0]["reason"]
    assert broker.get_orders() == []             # no order placed


# ---- full stop → research-only ----
def test_total_drawdown_full_stop_research_only(tmp_path, fake_kite):
    orch, broker, vault = build(tmp_path, fake_kite, cash=84000.0)  # -16% from HWM
    state = LoopState(day_open_equity=84000.0, high_water_mark=100000.0,
                      current_date="2026-05-29")
    now = datetime(2026, 5, 29, 11, 0, tzinfo=IST)
    res = orch.run_day(now, state=state, strategy_fn=enter_strategy, quotes=LIVE_QUOTES)
    assert res.governor.full_stop is True
    assert res.research_only is True
    assert res.blocks_run == ["research"]


# ---- run_once picks the clock-appropriate block ----
@pytest.mark.parametrize("hour,expected", [(8, "pre-market"), (11, "market"), (16, "post-market")])
def test_run_once_phase_by_clock(tmp_path, fake_kite, hour, expected):
    orch, broker, vault = build(tmp_path, fake_kite)
    quotes = {"NSE:RELIANCE": {"last_price": 100.0}}  # price present, no ts → live
    now = datetime(2026, 5, 29, hour, 0, tzinfo=IST)
    res = orch.run_once(now, state=LoopState(), quotes=quotes)
    assert res.phase == expected


# ---- safety violation halts the whole loop ----
def test_safety_violation_halts(tmp_path, fake_kite):
    orch, broker, vault = build(tmp_path, fake_kite, mode="live")
    now = datetime(2026, 5, 29, 11, 0, tzinfo=IST)
    res = orch.run_day(now, state=LoopState(), strategy_fn=enter_strategy, quotes=LIVE_QUOTES)
    assert res.phase == "halted"
    assert vault.exists(vault.alert_rel("2026-05-29", "paper-guard"))
