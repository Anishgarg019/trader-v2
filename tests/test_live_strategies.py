"""Registry-backed strategy_fn wiring + Orchestrator smoke test
(Phase 11, RESEARCHER-SPEC §G/§10)."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from agent.broker.paper_broker import PaperBroker
from agent.broker.kite_client import KiteDataClient
from agent.execution import ExecutionEngine
from agent.live_strategies import build_strategy_fn
from agent.loop import Orchestrator, LoopState
from agent.registry import ActiveSpec
from agent.trading_day import IST
from vault.writer import VaultWriter


def _frame(n=300, seed=1):
    # strictly rising series (tiny noise) → last close is reliably above a short MA, so the
    # trivial entry below fires on the final bar deterministically.
    rng = np.random.default_rng(seed)
    close = 100 + np.arange(n) * 0.5 + rng.normal(0, 0.05, n)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                         "close": close, "volume": np.full(n, 1e5)}, index=idx)


# trivial entry that fires on a rising series (close above a 5-bar SMA); exit ~never
TRIVIAL = {
    "id": "tz", "name": "Trivial", "families": ["trend"], "timeframe": "day",
    "entry": {"all": [{"pred": "price_above_ma", "length": 5, "kind": "sma"}]},
    "exit": {"any": [{"pred": "rsi_above", "length": 14, "threshold": 95}]},
    "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0,
}


@pytest.fixture(autouse=True)
def _future_today(monkeypatch):
    """Make every historical bar 'closed' regardless of the real clock."""
    import agent.strategy_compiler as sc

    class _FixedDT:
        @staticmethod
        def now(tz=None):
            return pd.Timestamp("2099-01-01").to_pydatetime()
    monkeypatch.setattr(sc, "datetime", _FixedDT)


def test_build_strategy_fn_none_when_no_specs():
    assert build_strategy_fn([], history_fn=lambda s: None, broker=None, vault=None) is None


def test_merged_fn_entries_only_for_deployed_symbol(tmp_path):
    frames = {"AAA": _frame(seed=1)}
    active = [ActiveSpec(spec=TRIVIAL, deployed_symbols=["NSE:AAA"], note_rel="Strategies/tz.md")]

    broker = PaperBroker(starting_cash=100000.0, price_fn=lambda s: 120.0)
    vault = VaultWriter(tmp_path / "vault"); vault.ensure_structure()
    fn = build_strategy_fn(active, history_fn=lambda s: frames.get(s),
                           broker=broker, vault=vault, state_dir=str(tmp_path))
    intents = fn({"equity": 100000.0, "available_cash": 100000.0,
                  "price_fn": lambda s: 120.0, "date": "2099-01-02"})
    assert intents, "trivial spec should emit an entry"
    assert {it["symbol"] for it in intents} == {"AAA"}
    assert all(it["exchange"] == "NSE" for it in intents)


def test_dedupes_entries_by_symbol(tmp_path):
    frames = {"AAA": _frame(seed=1)}
    s2 = {**TRIVIAL, "id": "tz2"}
    active = [
        ActiveSpec(spec=TRIVIAL, deployed_symbols=["NSE:AAA"], note_rel="Strategies/tz.md"),
        ActiveSpec(spec=s2, deployed_symbols=["NSE:AAA"], note_rel="Strategies/tz2.md"),
    ]
    broker = PaperBroker(starting_cash=100000.0, price_fn=lambda s: 120.0)
    vault = VaultWriter(tmp_path / "vault"); vault.ensure_structure()
    fn = build_strategy_fn(active, history_fn=lambda s: frames.get(s),
                           broker=broker, vault=vault, state_dir=str(tmp_path))
    intents = fn({"equity": 100000.0, "available_cash": 100000.0,
                  "price_fn": lambda s: 120.0, "date": "2099-01-02"})
    enters = [it for it in intents if it["action"] == "enter" and it["symbol"] == "AAA"]
    assert len(enters) == 1  # second spec deduped


def test_walks_a_day_and_places_only_paperbroker_orders(tmp_path, fake_kite):
    frames = {"AAA": _frame(seed=1)}
    active = [ActiveSpec(spec=TRIVIAL, deployed_symbols=["NSE:AAA"], note_rel="Strategies/tz.md")]

    broker = PaperBroker(starting_cash=100000.0, price_fn=lambda s: 120.0)
    vault = VaultWriter(tmp_path / "vault"); vault.ensure_structure()
    kite = KiteDataClient(kite=fake_kite)
    execu = ExecutionEngine(broker, vault, kite_client=kite, mode="paper")
    orch = Orchestrator(broker=broker, vault=vault, execution=execu, kite_client=kite,
                        universe=["NSE:AAA"], price_fn=lambda s: 120.0, mode="paper")
    fn = build_strategy_fn(active, history_fn=lambda s: frames.get(s),
                           broker=broker, vault=vault, state_dir=str(tmp_path))

    now = datetime(2026, 5, 29, 11, 0, tzinfo=IST)  # Friday session
    quotes = {"NSE:AAA": {"last_price": 120.0, "last_trade_time": "2026-05-29 10:55:00"}}
    res = orch.run_day(now, state=LoopState(), strategy_fn=fn, quotes=quotes)

    assert res.trading_day is True and res.research_only is False
    # an order was placed and it lives in the PaperBroker (the only router)
    orders = broker.get_orders()
    assert orders, "expected a paper order"
    assert all(o["order_id"].startswith("PAPER") for o in orders)
    positions = broker.get_positions()
    assert positions and positions[0]["tradingsymbol"] == "AAA"
