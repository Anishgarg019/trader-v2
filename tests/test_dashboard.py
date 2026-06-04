"""Dashboard: store round-trips (same SQL runs on Postgres), publisher sync, app helpers."""
import pandas as pd
import pytest

from dashboard.store import open_store
from dashboard.publisher import Publisher, _justification, _section, _first_line
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


def test_strategies_and_research_runs_round_trip(store):
    store.replace_strategies([
        {"id": "s001", "name": "RSI MR", "status": "retired", "families": "mean-reversion",
         "deployed_symbols": "", "created": "2026-05-31", "oos_return": None,
         "oos_sharpe": None, "win_rate": None, "trades": 1, "symbols_deployed": 0,
         "symbols_tested": 10, "n_params": 2, "thesis": "buy dips", "reasoning": "0/10 OOS",
         "detail": "| sym |", "in_graveyard": 0, "updated_at": "x"}])
    store.replace_research_runs([
        {"uid": "2026-06-02-researcher-daily", "date": "2026-06-02", "cadence": "daily",
         "proposed": 2, "valid": 2, "deployed_n": 0, "rejected": 2, "coverage_before": 0,
         "coverage_after": 0, "summary": "proposed=2 rejected=2", "updated_at": "x"}])
    assert store.strategies()[0]["status"] == "retired"
    assert store.strategies()[0]["symbols_tested"] == 10
    assert store.research_runs()[0]["cadence"] == "daily"


def test_replace_strategies_clears_old(store):
    store.replace_strategies([{"id": "s001", "name": "a"}])
    store.replace_strategies([{"id": "s002", "name": "b"}])
    assert [s["id"] for s in store.strategies()] == ["s002"]


# ---- publisher ----
def test_section_and_first_line():
    body = ("## Thesis\nbuy oversold\n\n## Backtest log\ndeployed 2/2 OOS\nmore\n\n"
            "## Status history\n- created")
    assert _section(body, "Thesis") == "buy oversold"
    assert _first_line(_section(body, "Backtest log")) == "deployed 2/2 OOS"
    assert _section(body, "Nonexistent") == ""


_SPEC = {"id": "s900", "name": "Test MR", "families": ["mean-reversion"], "timeframe": "day",
         "thesis": "buy oversold bounces",
         "entry": {"all": [{"pred": "rsi_below", "length": 14, "threshold": 30}]},
         "exit": {"any": [{"pred": "rsi_above", "length": 14, "threshold": 55}]},
         "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0}


def test_sync_strategies_and_research(tmp_path):
    store = open_store(str(tmp_path / "d.sqlite"))
    vault = VaultWriter(tmp_path / "vault"); vault.ensure_structure()
    # an active forward-test
    vault.write_strategy_note(
        strategy_id="s900", name="Test MR", status="forward-test",
        families=["mean-reversion"], created="2026-06-01",
        backtest={"return_pct": 0.12, "sharpe_like": 0.8, "win_rate": 0.6, "trades": 14,
                  "symbols_deployed": 2, "symbols_tested": 3},
        thesis="buy oversold bounces", backtest_log="deployed on 2/3 symbols. Edge holds OOS.",
        conditions="caveats\n\n### Win/loss by symbol\n| sym | x |\n| --- | --- |\n| INFY | ok |",
        frontmatter_extra={"spec": _SPEC, "deployed_symbols": ["NSE:INFY", "NSE:TCS"],
                           "tested_symbols": ["NSE:INFY", "NSE:TCS", "NSE:SBIN"]})
    # a rejected graveyard strategy
    vault.write_strategy_note(
        strategy_id="s901", name="Dead Idea", status="rejected", families=["trend"],
        created="2026-06-01", graveyard=True,
        backtest={"return_pct": None, "trades": 0, "symbols_deployed": 0, "symbols_tested": 10},
        thesis="golden cross", backtest_log="deployed on 0/10 symbols. No edge → graveyard.",
        frontmatter_extra={"spec": {**_SPEC, "id": "s901", "families": ["trend"]},
                           "deployed_symbols": [], "tested_symbols": ["NSE:INFY"]})
    # a research run note
    vault.write_note("Research/2026-06-02-researcher-daily.md",
                     {"type": "research", "date": "2026-06-02", "cadence": "daily",
                      "proposed": 2, "valid": 2, "deployed": [], "rejected": 2,
                      "coverage_before": 0, "coverage_after": 0},
                     "## Researcher run (daily)\nproposed=2 rejected=2\n")

    pub = Publisher(store, vault)
    assert pub.sync_strategies() == 2
    assert pub.sync_research_runs() == 1

    by_id = {s["id"]: s for s in store.strategies()}
    ft = by_id["s900"]
    assert ft["status"] == "forward-test" and ft["in_graveyard"] == 0
    assert ft["n_params"] == 3                         # entry+exit rsi thresholds + atr_k
    assert ft["symbols_deployed"] == 2 and ft["symbols_tested"] == 3
    assert ft["deployed_symbols"] == "NSE:INFY, NSE:TCS"
    assert "Edge holds OOS" in ft["reasoning"]
    assert "INFY" in ft["detail"] and "| sym |" in ft["detail"]
    grave = by_id["s901"]
    assert grave["in_graveyard"] == 1 and grave["status"] == "rejected"

    run = store.research_runs()[0]
    assert run["cadence"] == "daily" and run["rejected"] == 2 and run["deployed_n"] == 0


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


def _eqrow(eq, daily=0.0, total=0.0, ts="2026-05-29T09:15:00"):
    return {"ts": ts, "equity": eq, "cash": 0, "daily_dd_pct": daily, "total_dd_pct": total}


def test_band_metrics_status_levels():
    ok = app.band_metrics([_eqrow(106000, 0.01, -0.02)], [])
    assert ok["status"] == "Within limits" and ok["status_cls"] == "ok"
    halted = app.band_metrics([_eqrow(94000, -0.06, -0.06)], [])
    assert halted["status"] == "Halted today" and halted["status_cls"] == "warn"
    stop = app.band_metrics([_eqrow(84000, -0.02, -0.16)], [])
    assert stop["status"] == "Full stop" and stop["status_cls"] == "stop"


def test_band_metrics_empty_is_awaiting():
    m = app.band_metrics([], [])
    assert m["status"] == "Awaiting first run" and m["equity"] is None


def test_band_metrics_total_return_and_today_inr():
    rows = [_eqrow(100000, 0.0, 0.0, "2026-05-29T09:15:00"),
            _eqrow(110000, 0.10, 0.0, "2026-05-29T15:30:00")]
    daily = [{"day_open_equity": 100000.0, "day_close_equity": 110000.0}]
    m = app.band_metrics(rows, daily)
    assert m["total_return"] == pytest.approx(0.10)
    assert m["today_inr"] == pytest.approx(10000.0)


def test_group_strategies_splits_active_vs_graveyard():
    strategies = [{"id": "s1", "status": "forward-test"}, {"id": "s2", "status": "rejected"},
                  {"id": "s3", "status": "retired"}, {"id": "s4", "status": "researching"}]
    g = app.group_strategies(strategies)
    assert [s["id"] for s in g["active"]] == ["s1"]
    assert [s["id"] for s in g["graveyard"]] == ["s2", "s3"]


def test_open_risk_sums_open_long_trades():
    trades = [
        {"status": "open", "entry_price": 100.0, "stop_price": 96.0, "qty": 10},   # 40
        {"status": "open", "entry_price": 50.0, "stop_price": 47.0, "qty": 20},     # 60
        {"status": "closed", "entry_price": 100.0, "stop_price": 90.0, "qty": 5},   # ignored
    ]
    assert app.open_risk(trades) == pytest.approx(100.0)


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
    s.replace_strategies([
        {"id": "s001", "name": "RSI MR", "status": "forward-test", "families": "mean-reversion",
         "deployed_symbols": "NSE:INFY", "created": "2026-05-31", "oos_return": 0.12,
         "oos_sharpe": 0.8, "win_rate": 0.6, "trades": 14, "symbols_deployed": 1,
         "symbols_tested": 3, "n_params": 2, "thesis": "buy dips", "reasoning": "edge holds",
         "detail": "| sym | x |\n| --- | --- |\n| INFY | ok |", "in_graveyard": 0,
         "updated_at": "x"},
        {"id": "s002", "name": "Dead", "status": "rejected", "families": "trend",
         "deployed_symbols": "", "created": "2026-05-31", "oos_return": None, "oos_sharpe": None,
         "win_rate": None, "trades": 0, "symbols_deployed": 0, "symbols_tested": 10,
         "n_params": 2, "thesis": "golden cross", "reasoning": "0/10 OOS → graveyard",
         "detail": "", "in_graveyard": 1, "updated_at": "x"}])
    s.replace_research_runs([
        {"uid": "2026-06-02-researcher-daily", "date": "2026-06-02", "cadence": "daily",
         "proposed": 2, "valid": 2, "deployed_n": 0, "rejected": 2, "coverage_before": 0,
         "coverage_after": 0, "summary": "proposed=2 rejected=2", "updated_at": "x"}])


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
