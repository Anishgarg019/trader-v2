"""Broker layer: read-only Kite data client + local paper-trading engine + safety guard."""

from agent.broker.kite_client import KiteDataClient, FORBIDDEN_ORDER_METHODS
from agent.broker.paper_broker import PaperBroker, PaperOrder
from agent.broker.safety import (
    SafetyViolation,
    assert_paper_mode,
    assert_no_order_methods,
)

__all__ = [
    "KiteDataClient",
    "FORBIDDEN_ORDER_METHODS",
    "PaperBroker",
    "PaperOrder",
    "SafetyViolation",
    "assert_paper_mode",
    "assert_no_order_methods",
]
