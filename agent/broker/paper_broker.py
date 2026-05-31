"""Local paper-trading engine.

Simulates order placement and fills against live Kite quotes (read-only data). It NEVER
contacts a Kite order endpoint — this is where orders go *instead of* a real broker, since
Zerodha has no sandbox. The order/GTT/position/trade book lives in memory.

This Phase-1 seed covers MARKET/LIMIT/SL fills, GTT storage, modify/cancel, position
aggregation and cash tracking. Realistic friction (brokerage, STT, slippage) and stop
triggering are layered on in later phases (spec §6.4.1 / §4.3 / Phase 4 & 7); a slippage
hook is provided here so the interface is stable.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable

# Kite-compatible constants (so the rest of the app speaks one vocabulary).
BUY, SELL = "BUY", "SELL"
MARKET, LIMIT, SL, SLM = "MARKET", "LIMIT", "SL", "SL-M"
CNC, MIS = "CNC", "MIS"
STATUS_COMPLETE = "COMPLETE"
STATUS_OPEN = "OPEN"
STATUS_CANCELLED = "CANCELLED"
STATUS_REJECTED = "REJECTED"
STATUS_TRIGGER_PENDING = "TRIGGER PENDING"


@dataclass
class PaperOrder:
    order_id: str
    exchange: str
    tradingsymbol: str
    transaction_type: str
    quantity: int
    product: str
    order_type: str
    price: float | None = None
    trigger_price: float | None = None
    average_price: float = 0.0
    filled_quantity: int = 0
    status: str = STATUS_OPEN
    variety: str = "regular"
    validity: str = "DAY"
    tag: str | None = None
    order_timestamp: str | None = None
    status_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Position:
    exchange: str
    tradingsymbol: str
    product: str
    quantity: int = 0          # signed net quantity
    average_price: float = 0.0  # average price of the open position
    realized_pnl: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _instrument_key(exchange: str, tradingsymbol: str) -> str:
    return f"{exchange}:{tradingsymbol}"


class PaperBroker:
    """In-process order simulator. Marker attr `IS_PAPER_BROKER` is what the safety
    guard checks (spec §1.3)."""

    IS_PAPER_BROKER = True

    def __init__(self,
                 starting_cash: float = 100000.0,
                 price_fn: Callable[[str], float] | None = None,
                 slippage_fn: Callable[[str, str, float], float] | None = None,
                 now_fn: Callable[[], datetime] | None = None) -> None:
        """
        Args:
            starting_cash: opening cash (spec: ₹1,00,000).
            price_fn: resolves "EXCH:SYMBOL" -> last price, for MARKET fills. Usually
                wired to KiteDataClient.ltp. Optional; a per-order `last_price` overrides.
            slippage_fn: (exch_symbol, side, ref_price) -> adjusted fill price. Defaults
                to no slippage in Phase 1; realistic model arrives in Phase 4.
            now_fn: clock (injected for deterministic tests).
        """
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        self._price_fn = price_fn
        self._slippage_fn = slippage_fn or (lambda sym, side, px: px)
        self._now = now_fn or datetime.now
        self._orders: dict[str, PaperOrder] = {}
        self._gtts: dict[int, dict[str, Any]] = {}
        self._trades: list[dict[str, Any]] = []
        self._positions: dict[str, Position] = {}
        self._order_seq = 0
        self._gtt_seq = 0

    # ---- helpers -------------------------------------------------------------
    def _next_order_id(self) -> str:
        self._order_seq += 1
        return f"PAPER{self._order_seq:06d}"

    def _resolve_price(self, exch_symbol: str, last_price: float | None) -> float | None:
        if last_price is not None:
            return float(last_price)
        if self._price_fn is not None:
            return float(self._price_fn(exch_symbol))
        return None

    # ---- order placement -----------------------------------------------------
    def place_order(self,
                    *,
                    exchange: str,
                    tradingsymbol: str,
                    transaction_type: str,
                    quantity: int,
                    product: str,
                    order_type: str,
                    price: float | None = None,
                    trigger_price: float | None = None,
                    tag: str | None = None,
                    validity: str = "DAY",
                    variety: str = "regular",
                    last_price: float | None = None) -> str:
        """Place a simulated order. Returns the order_id. MARKET fills immediately at the
        resolved last price (with slippage); LIMIT fills if marketable; SL/SL-M park as
        TRIGGER PENDING until a future tick triggers them (Phase 7)."""
        oid = self._next_order_id()
        order = PaperOrder(
            order_id=oid, exchange=exchange, tradingsymbol=tradingsymbol,
            transaction_type=transaction_type.upper(), quantity=int(quantity),
            product=product, order_type=order_type.upper(), price=price,
            trigger_price=trigger_price, tag=tag, validity=validity, variety=variety,
            order_timestamp=self._now().isoformat(),
        )
        self._orders[oid] = order

        if order.quantity <= 0:
            order.status = STATUS_REJECTED
            order.status_message = "quantity must be positive"
            return oid

        key = _instrument_key(exchange, tradingsymbol)
        ref = self._resolve_price(key, last_price)

        if order.order_type == MARKET:
            if ref is None:
                order.status = STATUS_REJECTED
                order.status_message = "no price available to fill MARKET order"
                return oid
            self._fill(order, ref)
        elif order.order_type == LIMIT:
            if ref is not None and self._limit_marketable(order, ref):
                self._fill(order, order.price)
            # else stays OPEN (resting limit)
        elif order.order_type in (SL, SLM):
            order.status = STATUS_TRIGGER_PENDING  # triggered by a later tick (Phase 7)
        else:
            order.status = STATUS_REJECTED
            order.status_message = f"unsupported order_type {order.order_type!r}"
        return oid

    def _limit_marketable(self, order: PaperOrder, ref: float) -> bool:
        if order.price is None:
            return False
        return ref <= order.price if order.transaction_type == BUY else ref >= order.price

    def _fill(self, order: PaperOrder, ref_price: float) -> None:
        key = _instrument_key(order.exchange, order.tradingsymbol)
        fill_price = self._slippage_fn(key, order.transaction_type, float(ref_price))
        order.average_price = fill_price
        order.filled_quantity = order.quantity
        order.status = STATUS_COMPLETE
        self._apply_fill(order, fill_price)
        self._trades.append({
            "order_id": order.order_id, "exchange": order.exchange,
            "tradingsymbol": order.tradingsymbol, "transaction_type": order.transaction_type,
            "quantity": order.quantity, "average_price": fill_price,
            "product": order.product, "tag": order.tag,
            "fill_timestamp": self._now().isoformat(),
        })

    def _apply_fill(self, order: PaperOrder, fill_price: float) -> None:
        key = _instrument_key(order.exchange, order.tradingsymbol)
        pos = self._positions.get(key) or Position(order.exchange, order.tradingsymbol, order.product)
        signed = order.quantity if order.transaction_type == BUY else -order.quantity
        old_qty = pos.quantity
        new_qty = old_qty + signed

        if old_qty == 0 or (old_qty > 0) == (signed > 0):
            # opening or increasing in the same direction → weighted average price
            total_cost = abs(old_qty) * pos.average_price + abs(signed) * fill_price
            pos.average_price = total_cost / abs(new_qty) if new_qty != 0 else 0.0
        else:
            # reducing/closing/flipping → realize P&L on the closed portion
            closed = min(abs(signed), abs(old_qty))
            direction = 1 if old_qty > 0 else -1
            pos.realized_pnl += direction * (fill_price - pos.average_price) * closed
            if abs(signed) > abs(old_qty):  # flipped through zero
                pos.average_price = fill_price
            elif new_qty == 0:
                pos.average_price = 0.0
            # else average_price of remaining position is unchanged

        pos.quantity = new_qty
        self._positions[key] = pos
        # Cash: pay for buys, receive for sells (charges added in Phase 4).
        self.cash += -signed * fill_price

    # ---- GTT (two-leg stop/target, spec §4.3) --------------------------------
    def place_gtt_order(self, **gtt: Any) -> int:
        """Store a GTT (used for protective stops). Triggering is simulated in Phase 7."""
        self._gtt_seq += 1
        trigger_id = self._gtt_seq
        self._gtts[trigger_id] = {"trigger_id": trigger_id, "status": "active", **gtt}
        return trigger_id

    def modify_order(self, order_id: str, **changes: Any) -> str:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"unknown order_id {order_id!r}")
        for k, v in changes.items():
            if hasattr(order, k):
                setattr(order, k, v)
        return order_id

    def cancel_order(self, order_id: str) -> str:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"unknown order_id {order_id!r}")
        if order.status in (STATUS_OPEN, STATUS_TRIGGER_PENDING):
            order.status = STATUS_CANCELLED
        return order_id

    # ---- reads ---------------------------------------------------------------
    def get_orders(self) -> list[dict[str, Any]]:
        return [o.as_dict() for o in self._orders.values()]

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        o = self._orders.get(order_id)
        return o.as_dict() if o else None

    def get_gtts(self) -> list[dict[str, Any]]:
        return list(self._gtts.values())

    def get_trades(self) -> list[dict[str, Any]]:
        return list(self._trades)

    def get_positions(self) -> list[dict[str, Any]]:
        return [p.as_dict() for p in self._positions.values() if p.quantity != 0 or p.realized_pnl]
