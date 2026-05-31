"""Tests for the local paper-trading engine (Phase 1 seed)."""
from datetime import datetime

import pytest

from agent.broker.paper_broker import (
    PaperBroker, BUY, SELL, MARKET, LIMIT, SLM, CNC,
    STATUS_COMPLETE, STATUS_REJECTED, STATUS_OPEN, STATUS_CANCELLED, STATUS_TRIGGER_PENDING,
)

FIXED_NOW = lambda: datetime(2026, 5, 29, 10, 0, 0)


def make_broker(price=100.0):
    return PaperBroker(starting_cash=100000.0,
                       price_fn=lambda sym: price,
                       now_fn=FIXED_NOW)


def test_marker_attr_for_safety_guard():
    assert PaperBroker.IS_PAPER_BROKER is True


def test_market_buy_fills_and_updates_position_and_cash():
    pb = make_broker(price=100.0)
    oid = pb.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type=BUY,
                         quantity=10, product=CNC, order_type=MARKET, tag="SYS-s001")
    order = pb.get_order(oid)
    assert order["status"] == STATUS_COMPLETE
    assert order["filled_quantity"] == 10
    assert order["average_price"] == 100.0

    pos = pb.get_positions()
    assert len(pos) == 1
    assert pos[0]["quantity"] == 10
    assert pos[0]["average_price"] == 100.0
    assert pb.cash == pytest.approx(100000.0 - 10 * 100.0)
    assert len(pb.get_trades()) == 1


def test_realized_pnl_on_close():
    pb = make_broker(price=100.0)
    pb.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type=BUY,
                   quantity=10, product=CNC, order_type=MARKET)
    # Sell 10 at 110 → realized P&L = (110-100)*10 = 100
    pb.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type=SELL,
                   quantity=10, product=CNC, order_type=MARKET, last_price=110.0)
    pos = pb.get_positions()
    assert len(pos) == 1
    assert pos[0]["quantity"] == 0
    assert pos[0]["realized_pnl"] == pytest.approx(100.0)
    # cash: -1000 (buy) + 1100 (sell) = +100 over start
    assert pb.cash == pytest.approx(100000.0 + 100.0)


def test_average_price_on_scaling_in():
    pb = make_broker()
    pb.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type=BUY,
                   quantity=10, product=CNC, order_type=MARKET, last_price=100.0)
    pb.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type=BUY,
                   quantity=10, product=CNC, order_type=MARKET, last_price=120.0)
    pos = pb.get_positions()[0]
    assert pos["quantity"] == 20
    assert pos["average_price"] == pytest.approx(110.0)


def test_market_order_rejected_without_price():
    pb = PaperBroker(starting_cash=100000.0, now_fn=FIXED_NOW)  # no price_fn
    oid = pb.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type=BUY,
                         quantity=10, product=CNC, order_type=MARKET)
    assert pb.get_order(oid)["status"] == STATUS_REJECTED


def test_zero_quantity_rejected():
    pb = make_broker()
    oid = pb.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type=BUY,
                         quantity=0, product=CNC, order_type=MARKET)
    assert pb.get_order(oid)["status"] == STATUS_REJECTED


def test_limit_order_rests_when_not_marketable():
    pb = make_broker(price=100.0)
    # buy limit at 90 with market at 100 → not marketable → OPEN
    oid = pb.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type=BUY,
                         quantity=10, product=CNC, order_type=LIMIT, price=90.0)
    assert pb.get_order(oid)["status"] == STATUS_OPEN


def test_limit_order_fills_when_marketable():
    pb = make_broker(price=100.0)
    oid = pb.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type=BUY,
                         quantity=10, product=CNC, order_type=LIMIT, price=105.0)
    assert pb.get_order(oid)["status"] == STATUS_COMPLETE
    assert pb.get_order(oid)["average_price"] == 105.0


def test_sl_order_parks_as_trigger_pending():
    pb = make_broker(price=100.0)
    oid = pb.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type=SELL,
                         quantity=10, product=CNC, order_type=SLM, trigger_price=95.0)
    assert pb.get_order(oid)["status"] == STATUS_TRIGGER_PENDING


def test_cancel_open_order():
    pb = make_broker(price=100.0)
    oid = pb.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type=BUY,
                         quantity=10, product=CNC, order_type=LIMIT, price=90.0)
    pb.cancel_order(oid)
    assert pb.get_order(oid)["status"] == STATUS_CANCELLED


def test_gtt_stored():
    pb = make_broker()
    tid = pb.place_gtt_order(tradingsymbol="INFY", exchange="NSE", trigger_values=[95.0])
    assert any(g["trigger_id"] == tid for g in pb.get_gtts())


def test_slippage_hook_applied():
    pb = PaperBroker(starting_cash=100000.0, price_fn=lambda s: 100.0,
                     slippage_fn=lambda sym, side, px: px + 0.5 if side == BUY else px - 0.5,
                     now_fn=FIXED_NOW)
    oid = pb.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type=BUY,
                         quantity=10, product=CNC, order_type=MARKET)
    assert pb.get_order(oid)["average_price"] == pytest.approx(100.5)
