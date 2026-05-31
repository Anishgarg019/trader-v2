"""Dashboard: store round-trips (same SQL runs on Postgres), publisher sync, app helpers."""
import pandas as pd
import pytest

from dashboard.store import open_store
from dashboard.publisher import Publisher, _justification
from dashboard import app
from agent.broker.paper_broker import PaperBroker, BUY, CNC, MARKET
from agent.governor import evaluate_drawdown
from agent.loop import LoopResult, LoopState
from vault.writer import VaultWriter


@pytest.fixture
def store(tmp_path):
    return open_store(str(tmp_path / "d.sqlite"))


# ---- store ----
def test_store_round_trips(store):
    store.record_equity(ts="2026-05-29T09:15:00", equity=100500.0, cash=40000.0,
                        daily_dd_pct=0.005, total_dd_pct=0.0)
    store.upsert_trade({"order_id": "t1", "date": "2026-05-29", "symbol": "NSE:INFY",
                        "exchange": "NSE", "strategy_id": "s001", "direction": "long",
                        "qty": 10, "entry_price": 100.0, "stop_price": 96.0,
                        "exit_price": None, "status": "open", "outcome": None,
                        "pnl_rupees": None, "risk_rupees": 40.0, "justification": "why",
                        "updated_at": "x"})
    store.replace_positions([{"symbol": "NSE:INFY", "exchange": "NSE", "qty": 10,
                              "avg_price": 100.0, "last_price": 101.0, "mtm": 1010.0,
                              "updated_at": "x"}])
    store.record_alert({"uid": "a1", "date": "2026-05-29", "kind": "data", "detail": "d",
                        "ts": "x"})
    assert len(store.equity_series()) == 1
    assert store.trades()[0]["justification"] == "why"
    assert store.open_positions()[0]["symbol"] == "NSE:INFY"
    assert store.alerts()[0]["kind"] == "data"


def test_upsert_updates_in_place(store):
    row = {"order_id": "t1", "status": "open", "pnl_rupees": None}
    store.upsert_trade(row)
    store.upsert_trade({"order_id": "t1", "status": "closed", "pnl_rupees": 250.0})
    trades = store.trades()
    assert len(trades) == 1 and trades[0]["status"] == "closed" and trades[0]["pnl_rupees"] == 250.0


def test_strategy_winrates(store):
    store.upsert_trade({"order_id": "a", "strategy_id": "s1", "status": "closed", "pnl_rupees": 100})
    store.upsert_trade({"order_id": "b", "strategy_id": "s1", "status": "closed", "pnl_rupees": -50})
    store.upsert_trade({"order_id": "c", "strategy_id": "s2", "status": "closed", "pnl_rupees": 30})
    wr = {w["strategy_id"]: w for w in store.strategy_winrates()}
    assert wr["s1"]["trades"] == 2 and wr["s1"]["wins"] == 1
    assert wr["s1"]["win_rate"] == pytest.approx(0.5)
    assert wr["s2"]["pnl"] == 30


def test_replace_positions_clears_old(store):
    store.replace_positions([{"symbol": "NSE:A", "exchange": "NSE", "qty": 5}])
    store.replace_positions([{"symbol": "NSE:B", "exchange": "NSE", "qty": 7}])
    syms = [p["symbol"] for p in store.open_positions()]
    assert syms == ["NSE:B"]


# ---- publisher ----
def test_justification_extraction():
    body = "## Justification (x)\n- Ensemble: RSI<30\n\n## Review (after close)\n- stuff"
    assert "RSI<30" in _justification(body)
    assert "Review" not in _justification(body)


def test_publisher_syncs_from_vault(tmp_path):
    store = open_store(str(tmp_path / "d.sqlite"))
    vault = VaultWriter(tmp_path / "vault"); vault.ensure_structure()
    vault.write_trade_note(d="2026-05-29", symbol="NSE:RELIANCE", strategy_id="s001",
                           strategy_link="[[s001]]",
                           frontmatter_extra={"entry_price": 1400.0, "quantity": 6,
                                              "stop_price": 1370.0, "risk_rupees": 195.0},
                           justification="- Ensemble: RSI(14)=28 + close>SMA200")
    vault.write_system_alert(d="2026-05-29", slug="data", kind="data-anomaly", detail="stale")
    vault.write_note("Universe/current-universe.md",
                     {"type": "universe", "names": ["NSE:RELIANCE"], "date": "2026-05-29"}, "x")

    broker = PaperBroker(starting_cash=100000.0, price_fn=lambda s: 100.0)
    broker.place_order(exchange="NSE", tradingsymbol="RELIANCE", transaction_type=BUY,
                       quantity=6, product=CNC, order_type=MARKET, last_price=1400.0)

    gov = evaluate_drawdown(99000, 100000, 100000)
    result = LoopResult(date="2026-05-29", phase="full-day", research_only=False,
                        trading_day=True, equity=99000.0, governor=gov,
                        actions=[{"symbol": "NSE:RELIANCE", "status": "placed"}],
                        state=LoopState(day_open_equity=100000.0, high_water_mark=100000.0,
                                        current_date="2026-05-29"))

    pub = Publisher(store, vault)
    summary = pub.publish(result, broker, price_fn=lambda s: 1410.0)

    assert summary["trades"] == 1 and summary["alerts"] == 1
    t = store.trades()[0]
    assert "RSI(14)=28" in t["justification"]   # the "logic behind the call" made it through
    assert store.open_positions()[0]["symbol"] == "NSE:RELIANCE"
    assert store.equity_series()[0]["equity"] == 99000.0
    assert store.daily()[0]["trades_today"] == 1
    assert store.universe()[0]["symbol"] == "NSE:RELIANCE"


# ---- app pure helpers ----
def test_equity_dataframe():
    rows = [{"ts": "2026-05-29T09:15:00", "equity": 100000, "cash": 40000,
             "daily_dd_pct": 0.0, "total_dd_pct": 0.0},
            {"ts": "2026-05-29T09:20:00", "equity": 100500, "cash": 40000,
             "daily_dd_pct": 0.005, "total_dd_pct": 0.0}]
    df = app.equity_dataframe(rows)
    assert list(df.columns) == ["equity", "cash"]
    assert len(df) == 2 and df["equity"].iloc[-1] == 100500


def test_drawdown_dataframe_scales_to_percent():
    rows = [{"ts": "2026-05-29T09:15:00", "equity": 95000, "cash": 0,
             "daily_dd_pct": -0.05, "total_dd_pct": -0.05}]
    df = app.drawdown_dataframe(rows)
    assert df["daily_dd_%"].iloc[0] == pytest.approx(-5.0)


def test_kpi_summary():
    eq = [{"equity": 100000, "cash": 40000, "daily_dd_pct": -0.01, "total_dd_pct": -0.02}]
    k = app.kpi_summary(eq, [{"mtm": 1000}, {"mtm": 500}])
    assert k["equity"] == 100000 and k["open_positions"] == 2 and k["open_mtm"] == 1500


def test_empty_helpers_dont_crash():
    assert app.equity_dataframe([]).empty
    assert app.kpi_summary([], [])["open_positions"] == 0


# ---- full app render (catches runtime bugs the data helpers miss) ----
import os
from pathlib import Path

APP_PATH = str(Path(__file__).resolve().parent.parent / "dashboard" / "app.py")


def _seed(db):
    s = open_store(db)
    s.record_equity(ts="2026-05-29T09:15:00", equity=100000.0, cash=40000.0,
                    daily_dd_pct=0.0, total_dd_pct=0.0)
    s.upsert_trade({"order_id": "t1", "date": "2026-05-29", "symbol": "NSE:INFY",
                    "exchange": "NSE", "strategy_id": "s001", "direction": "long", "qty": 10,
                    "entry_price": 100.0, "stop_price": 96.0, "exit_price": None,
                    "status": "open", "outcome": None, "pnl_rupees": None,
                    "risk_rupees": 40.0, "justification": "RSI<30 + above SMA200",
                    "updated_at": "x"})
    s.replace_positions([{"symbol": "NSE:INFY", "exchange": "NSE", "qty": 10,
                          "avg_price": 100.0, "last_price": 101.0, "mtm": 1010.0,
                          "updated_at": "x"}])
    s.replace_universe([{"symbol": "NSE:INFY", "exchange": "NSE", "sector": "IT",
                         "avg_traded_value": 1e8, "atr_pct": 0.03, "as_of": "2026-05-29"}])
    s.record_alert({"uid": "a1", "date": "2026-05-29", "kind": "data", "detail": "d", "ts": "x"})


def test_app_renders_with_data(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    db = str(tmp_path / "d.sqlite")
    _seed(db)
    monkeypatch.setenv("DASHBOARD_DB_URL", db)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert not at.exception, f"app raised: {at.exception}"


def test_app_renders_with_empty_store(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    db = str(tmp_path / "empty.sqlite")
    open_store(db)  # schema only, no rows
    monkeypatch.setenv("DASHBOARD_DB_URL", db)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert not at.exception, f"app raised on empty store: {at.exception}"
