"""Execution (spec §4.3, §6.2, §8). FIRST module that places orders — PAPER only.

The non-negotiable sequence for every entry:  order → protective stop → trade note.
Order-without-note is a process failure. Everything routes through PaperBroker (never a
live broker). Before any order: the paper-mode safety guard (§1.3), the drawdown governor
(§5), risk sizing (§4), and the no-leverage assertion all must pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agent.broker.paper_broker import PaperBroker, BUY, SELL, MARKET, SLM
from agent.broker.safety import assert_paper_mode, SafetyViolation
from agent.governor import GovernorDecision, assert_no_leverage, OverLeverageError
from agent.risk import size_position
from agent.trading_day import IST
from vault.writer import VaultWriter


@dataclass
class ExecutionResult:
    status: str           # 'placed' | 'skipped' | 'halted' | 'error'
    reason: str
    order_id: str | None = None
    stop_trigger_id: int | None = None
    trade_note: str | None = None
    qty: int = 0
    fill_price: float = 0.0
    stop_price: float = 0.0


class ExecutionEngine:
    def __init__(self, broker: PaperBroker, vault: VaultWriter,
                 kite_client: Any | None = None, mode: str = "paper"):
        self.broker = broker
        self.vault = vault
        self.kite_client = kite_client
        self.mode = mode

    def _today(self) -> str:
        return datetime.now(IST).date().isoformat()

    def execute_entry(self, *,
                      symbol: str,
                      exchange: str,
                      strategy_id: str,
                      strategy_link: str,
                      last_price: float,
                      atr: float,
                      equity: float,
                      available_cash: float,
                      k: float = 2.0,
                      risk_pct: float = 0.05,
                      strategy_risk_budget: float | None = None,
                      existing_open_risk: float = 0.0,
                      current_gross_exposure: float = 0.0,
                      governor_decision: GovernorDecision | None = None,
                      regime: str | None = None,
                      justification: str = "",
                      product: str = "CNC",
                      d: str | None = None) -> ExecutionResult:
        """Place a long entry with its protective stop and write the trade note."""
        d = d or self._today()

        # 1) Safety guard — orders must be impossible to send live (§1.3).
        try:
            assert_paper_mode(self.broker, self.kite_client, self.mode)
        except SafetyViolation as e:
            self.vault.write_system_alert(d=d, slug="paper-guard", kind="safety",
                                          detail=str(e))
            return ExecutionResult("halted", f"safety guard: {e}")

        # 2) Drawdown governor (§5).
        if governor_decision is not None and not governor_decision.can_enter:
            return ExecutionResult("skipped", f"governor: {governor_decision.reason}")

        # 3) Risk sizing (§4).
        sizing = size_position(
            equity=equity, available_cash=available_cash, entry_price=last_price,
            atr=atr, k=k, direction="long", risk_pct=risk_pct,
            strategy_risk_budget=strategy_risk_budget,
            existing_open_risk=existing_open_risk,
        )
        if not sizing.allowed:
            return ExecutionResult("skipped", f"sizing: {sizing.reason}",
                                   stop_price=sizing.stop_price)

        # 4) No-leverage assertion (§5).
        try:
            assert_no_leverage(current_gross_exposure + sizing.notional, equity)
        except OverLeverageError as e:
            self.vault.write_system_alert(d=d, slug="leverage", kind="safety", detail=str(e))
            return ExecutionResult("error", f"leverage: {e}")

        # 5) ORDER (paper).
        order_id = self.broker.place_order(
            exchange=exchange, tradingsymbol=symbol, transaction_type=BUY,
            quantity=sizing.qty, product=product, order_type=MARKET,
            tag=f"SYS-{strategy_id}", last_price=last_price,
        )
        order = self.broker.get_order(order_id)
        if not order or order["status"] != "COMPLETE":
            msg = (order or {}).get("status_message", "order not filled")
            self.vault.write_system_alert(d=d, slug=f"order-{order_id}", kind="execution",
                                          detail=f"entry order {order_id} status "
                                                 f"{(order or {}).get('status')}: {msg}")
            return ExecutionResult("error", f"order not filled: {msg}", order_id=order_id)
        fill_price = order["average_price"]

        # 6) STOP — immediately, broker-side (§4.3). SL-M sell at the protective stop.
        stop_trigger_id = self.broker.place_gtt_order(
            tradingsymbol=symbol, exchange=exchange, transaction_type=SELL,
            product=product, quantity=sizing.qty, order_type=SLM,
            trigger_values=[sizing.stop_price], last_price=fill_price,
            tag=f"SYS-{strategy_id}",
        )

        # 7) TRADE NOTE — immediately (§6.2). order → stop → note, no exceptions.
        note_path = self.vault.write_trade_note(
            d=d, symbol=symbol, strategy_id=strategy_id, strategy_link=strategy_link,
            frontmatter_extra={
                "direction": "long", "product": product, "entry_price": fill_price,
                "stop_price": sizing.stop_price, "quantity": sizing.qty,
                "risk_rupees": round(sizing.trade_risk, 2), "atr_at_entry": atr,
                "size_pct_equity": round(sizing.notional / equity, 4) if equity else 0,
                "regime": regime,
            },
            justification=justification,
        )

        return ExecutionResult(
            status="placed", reason="ok", order_id=order_id,
            stop_trigger_id=stop_trigger_id, trade_note=str(note_path),
            qty=sizing.qty, fill_price=fill_price, stop_price=sizing.stop_price,
        )

    def close_position(self, *, symbol: str, exchange: str, quantity: int,
                       last_price: float, trade_note_rel: str | None = None,
                       entry_price: float | None = None, product: str = "CNC",
                       reason: str = "exit-signal", d: str | None = None) -> ExecutionResult:
        """Close (sell) an open paper position and update its trade note (status → closed)."""
        d = d or self._today()
        try:
            assert_paper_mode(self.broker, self.kite_client, self.mode)
        except SafetyViolation as e:
            self.vault.write_system_alert(d=d, slug="paper-guard", kind="safety", detail=str(e))
            return ExecutionResult("halted", f"safety guard: {e}")

        order_id = self.broker.place_order(
            exchange=exchange, tradingsymbol=symbol, transaction_type=SELL,
            quantity=quantity, product=product, order_type=MARKET, last_price=last_price,
        )
        order = self.broker.get_order(order_id)
        fill_price = order["average_price"] if order else last_price

        if trade_note_rel and self.vault.exists(trade_note_rel):
            updates: dict[str, Any] = {"status": "closed", "exit_price": fill_price}
            if entry_price is not None:
                pnl = (fill_price - entry_price) * quantity   # gross (paper; see spec §1.4)
                updates["pnl_rupees"] = round(pnl, 2)
                updates["outcome"] = "win" if pnl > 0 else "loss" if pnl < 0 else "scratch"
            self.vault.update_frontmatter(trade_note_rel, updates)

        return ExecutionResult("placed", reason, order_id=order_id, qty=quantity,
                               fill_price=fill_price)
