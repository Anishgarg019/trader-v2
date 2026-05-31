"""Daily loop orchestrator (spec §6 cadence + §8 startup checklist).

Sequence: trading-day gate → pre-market → market hours → post-market → research. On
closed/halted days it runs research-only. This is the deterministic Python skeleton the
scheduled Claude Code agent drives; the agent supplies the *judgment* (hypotheses, P&L
narrative) via the `strategy_fn` / `research_fn` callbacks and the prose it writes into the
daily note. All numbers (gate, governor, sizing) are computed here in Python.

Two entry points:
  run_once(now_ist) — production: startup checklist + the single clock-appropriate block.
  run_day(now_ist)  — simulation/dry-run: startup + walk every block in order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Callable

from agent.broker.paper_broker import PaperBroker
from agent.broker.safety import assert_paper_mode, SafetyViolation
from agent.execution import ExecutionEngine
from agent.governor import (
    evaluate_drawdown, update_high_water_mark, GovernorDecision,
)
from agent.reconcile import reconcile_positions
from agent.trading_day import (
    decide_trading_day, IST, MARKET_OPEN, MARKET_CLOSE,
)
from vault.writer import VaultWriter


@dataclass
class LoopState:
    """Persisted across runs (e.g. to a JSON file or the daily note)."""
    day_open_equity: float | None = None
    high_water_mark: float = 100000.0
    current_date: str | None = None


@dataclass
class LoopResult:
    date: str
    phase: str
    research_only: bool
    trading_day: bool
    equity: float
    governor: GovernorDecision | None
    actions: list[dict] = field(default_factory=list)
    blocks_run: list[str] = field(default_factory=list)
    reconciliation_ok: bool | None = None
    daily_note: str | None = None
    reasons: list[str] = field(default_factory=list)
    state: LoopState | None = None


def _t(now_ist: datetime) -> time:
    return now_ist.timetz().replace(tzinfo=None)


class Orchestrator:
    def __init__(self, *, broker: PaperBroker, vault: VaultWriter,
                 execution: ExecutionEngine, kite_client: Any | None = None,
                 universe: list[str] | None = None,
                 price_fn: Callable[[str], float] | None = None,
                 mode: str = "paper"):
        self.broker = broker
        self.vault = vault
        self.execution = execution
        self.kite_client = kite_client
        self.universe = universe
        self.price_fn = price_fn
        self.mode = mode

    # ---- equity (spec §1.2) --------------------------------------------------
    def compute_equity(self) -> float:
        mtm = 0.0
        if self.price_fn is not None:
            for p in self.broker.get_positions():
                sym = f"{p['exchange']}:{p['tradingsymbol']}"
                mtm += p["quantity"] * self.price_fn(sym)
        return self.broker.cash + mtm

    def determine_phase(self, now_ist: datetime, trading_day: bool,
                        research_only: bool) -> str:
        if research_only or not trading_day:
            return "research"
        t = _t(now_ist)
        if t < MARKET_OPEN:
            return "pre-market"
        if MARKET_OPEN <= t <= MARKET_CLOSE:
            return "market"
        return "post-market"

    # ---- startup checklist (spec §8) -----------------------------------------
    def _startup(self, now_ist: datetime, state: LoopState, *,
                 quotes: dict | None, expected_positions: list[dict] | None):
        d = now_ist.date().isoformat()
        reasons: list[str] = []

        # §8.2 paper-mode safety check
        assert_paper_mode(self.broker, self.kite_client, self.mode)  # raises → caller halts

        # equity / day-open / high-water (§1.2, §5)
        equity = self.compute_equity()
        if state.current_date != d:
            state.day_open_equity = equity
            state.current_date = d
        state.high_water_mark = update_high_water_mark(state.high_water_mark, equity)

        # §8.3 trading-day gate (calendar + Layer-2 probe if data supplied)
        decision = decide_trading_day(now_ist.date(), now_ist,
                                      universe=self.universe, quotes=quotes)

        # §5 governor
        gov = evaluate_drawdown(equity, state.day_open_equity, state.high_water_mark)

        # §8.5 reconciliation (if expected positions supplied)
        recon_ok = None
        if expected_positions is not None:
            recon = reconcile_positions(self.broker.get_positions(), expected_positions)
            recon_ok = recon.ok
            if not recon.ok:
                self.vault.write_system_alert(d=d, slug="reconcile", kind="reconciliation",
                                              detail=recon.summary())
                reasons.append(f"reconciliation break: {recon.summary()}")

        # research-only when closed or full-stop (§5/§8.7); daily halt blocks entries only
        research_only = (not decision.is_trading_day) or gov.full_stop
        if decision.needs_reprobe:
            research_only = True
            reasons.append("pre-open quiet universe — re-probe after 09:15 (not a closure)")
        if decision.system_alert:
            self.vault.write_system_alert(d=d, slug="gate", kind="trading-day",
                                          detail=decision.reason)
        if not decision.is_trading_day:
            reasons.append(decision.reason)
        if gov.full_stop:
            reasons.append(gov.reason)

        return d, equity, decision, gov, research_only, recon_ok, reasons

    # ---- blocks (spec §6.1–§6.4) ---------------------------------------------
    def _premarket(self, d: str, equity: float, gov: GovernorDecision) -> dict:
        return {"block": "pre-market", "day_open_equity": equity,
                "five_pct_line": round(equity * 0.95, 2), "can_enter": gov.can_enter}

    def _market(self, d: str, equity: float, gov: GovernorDecision,
                strategy_fn: Callable | None) -> list[dict]:
        actions: list[dict] = []
        if strategy_fn is None:
            return actions
        # existing open risk from the paper book (stop_distance tracked on GTTs is Phase 9;
        # here we conservatively pass 0 and rely on per-trade + total caps in sizing).
        ctx = {"equity": equity, "available_cash": self.broker.cash, "governor": gov,
               "universe": self.universe, "price_fn": self.price_fn, "date": d}
        for it in (strategy_fn(ctx) or []):
            if it["action"] == "enter":
                res = self.execution.execute_entry(
                    symbol=it["symbol"], exchange=it["exchange"],
                    strategy_id=it["strategy_id"], strategy_link=it.get("strategy_link", ""),
                    last_price=it["last_price"], atr=it["atr"], equity=equity,
                    available_cash=self.broker.cash, k=it.get("k", 2.0),
                    existing_open_risk=it.get("existing_open_risk", 0.0),
                    governor_decision=gov, justification=it.get("justification", ""),
                    regime=it.get("regime"), d=d,
                )
            else:  # exit
                res = self.execution.close_position(
                    symbol=it["symbol"], exchange=it["exchange"], quantity=it["quantity"],
                    last_price=it["last_price"], trade_note_rel=it.get("trade_note_rel"),
                    entry_price=it.get("entry_price"), d=d,
                )
            actions.append({"intent": it["action"], "symbol": it["symbol"],
                            "status": res.status, "reason": res.reason,
                            "order_id": res.order_id})
        return actions

    def _postmarket(self, d: str) -> dict:
        return {"block": "post-market", "orders": len(self.broker.get_orders()),
                "open_positions": len(self.broker.get_positions())}

    def _research(self, d: str, research_fn: Callable | None) -> dict:
        out = {"block": "research", "ran": False}
        if research_fn is not None:
            out["result"] = research_fn({"date": d, "broker": self.broker})
            out["ran"] = True
        return out

    # ---- daily note ----------------------------------------------------------
    def _write_daily(self, d: str, decision, gov: GovernorDecision, equity: float,
                     state: LoopState, actions: list[dict]) -> str:
        day_open = state.day_open_equity or equity
        holiday = decision.holiday_name
        placed = [a for a in actions if a.get("status") == "placed"]
        self.vault.write_daily_note(
            d=d, trading_day=decision.is_trading_day, holiday=holiday,
            day_open_equity=round(day_open, 2),
            frontmatter_extra={
                "day_close_equity": round(equity, 2),
                "drawdown_day_pct": round(gov.daily_drawdown_pct, 4),
                "drawdown_total_pct": round(gov.total_drawdown_pct, 4),
                "halted": bool(gov.halt_new_entries or gov.full_stop),
                "trades_today": [a["symbol"] for a in placed],
            },
        )
        return self.vault.daily_rel(d)

    # ---- entry points --------------------------------------------------------
    def run_day(self, now_ist: datetime | None = None, *, state: LoopState,
                strategy_fn: Callable | None = None, research_fn: Callable | None = None,
                quotes: dict | None = None,
                expected_positions: list[dict] | None = None) -> LoopResult:
        """Dry-run / simulation: startup, then walk every applicable block in order."""
        now_ist = now_ist or datetime.now(IST)
        try:
            d, equity, decision, gov, research_only, recon_ok, reasons = self._startup(
                now_ist, state, quotes=quotes, expected_positions=expected_positions)
        except SafetyViolation as e:
            d = now_ist.date().isoformat()
            self.vault.write_system_alert(d=d, slug="paper-guard", kind="safety", detail=str(e))
            return LoopResult(d, "halted", True, False, 0.0, None,
                              reasons=[f"safety: {e}"], state=state)

        actions: list[dict] = []
        blocks: list[str] = []
        if research_only:
            blocks = ["research"]
            self._research(d, research_fn)
        else:
            blocks = ["pre-market", "market", "post-market", "research"]
            self._premarket(d, equity, gov)
            actions = self._market(d, equity, gov, strategy_fn)
            self._postmarket(d)
            self._research(d, research_fn)

        equity = self.compute_equity()  # re-mark after any fills
        note = self._write_daily(d, decision, gov, equity, state, actions)
        return LoopResult(d, "full-day" if not research_only else "research",
                          research_only, decision.is_trading_day, equity, gov,
                          actions=actions, blocks_run=blocks, reconciliation_ok=recon_ok,
                          daily_note=note, reasons=reasons, state=state)

    def run_once(self, now_ist: datetime | None = None, *, state: LoopState,
                 strategy_fn: Callable | None = None, research_fn: Callable | None = None,
                 quotes: dict | None = None,
                 expected_positions: list[dict] | None = None) -> LoopResult:
        """Production: startup, then run only the clock-appropriate block."""
        now_ist = now_ist or datetime.now(IST)
        try:
            d, equity, decision, gov, research_only, recon_ok, reasons = self._startup(
                now_ist, state, quotes=quotes, expected_positions=expected_positions)
        except SafetyViolation as e:
            d = now_ist.date().isoformat()
            self.vault.write_system_alert(d=d, slug="paper-guard", kind="safety", detail=str(e))
            return LoopResult(d, "halted", True, False, 0.0, None,
                              reasons=[f"safety: {e}"], state=state)

        phase = self.determine_phase(now_ist, decision.is_trading_day, research_only)
        actions: list[dict] = []
        if phase == "pre-market":
            self._premarket(d, equity, gov)
        elif phase == "market":
            actions = self._market(d, equity, gov, strategy_fn)
        elif phase == "post-market":
            self._postmarket(d)
        else:
            self._research(d, research_fn)

        equity = self.compute_equity()
        note = self._write_daily(d, decision, gov, equity, state, actions)
        return LoopResult(d, phase, research_only, decision.is_trading_day, equity, gov,
                          actions=actions, blocks_run=[phase], reconciliation_ok=recon_ok,
                          daily_note=note, reasons=reasons, state=state)
