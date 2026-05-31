"""Paper-mode safety guard (spec §1.3, reconciled v1.1).

The agent must NEVER place a real order. There is no Kite sandbox; Kite is read-only.
This module enforces that structurally:

  1. MODE must be 'paper'.
  2. The object that handles orders must be the local PaperBroker.
  3. The Kite data client must expose NO order/write methods at all — you cannot call
     what does not exist.

Any failure raises SafetyViolation. Callers must treat that as a hard halt: write a
`system-alert` note and do nothing else (spec §1.3).
"""
from __future__ import annotations

import os

# Every order/write entry point Kite Connect exposes. The read-only data client must
# expose NONE of these. (Mirrors kiteconnect.KiteConnect's order surface.)
FORBIDDEN_ORDER_METHODS = frozenset({
    "place_order",
    "place_gtt",
    "place_gtt_order",
    "modify_order",
    "modify_gtt",
    "cancel_order",
    "delete_gtt",
    "exit_order",
    "place_autoslice_order",
    "convert_position",
    "place_mf_order",
    "cancel_mf_order",
    "place_mf_sip",
    "modify_mf_sip",
    "cancel_mf_sip",
})


class SafetyViolation(RuntimeError):
    """Raised when anything could let an order reach a live broker. Treat as a halt."""


def assert_no_order_methods(obj: object) -> None:
    """Fail if `obj` exposes any order/write method (i.e. could place a real order)."""
    leaked = sorted(name for name in FORBIDDEN_ORDER_METHODS if hasattr(obj, name))
    if leaked:
        raise SafetyViolation(
            f"{type(obj).__name__} exposes forbidden order method(s): {leaked}. "
            "The Kite client must be read-only; orders go only through PaperBroker."
        )


def assert_paper_mode(order_router: object,
                      kite_client: object | None = None,
                      mode: str | None = None) -> None:
    """Assert it is structurally impossible to place a live order.

    Args:
        order_router: the object that will receive order calls. Must be the PaperBroker.
        kite_client:  the Kite data client; if given, asserted to have no order methods.
        mode:         the run mode; defaults to env MODE. Must be 'paper'.
    """
    mode = (mode if mode is not None else os.environ.get("MODE", "paper")).strip().lower()
    if mode != "paper":
        raise SafetyViolation(f"MODE must be 'paper', got {mode!r}. Refusing to trade.")

    if not getattr(order_router, "IS_PAPER_BROKER", False):
        raise SafetyViolation(
            f"Order router is {type(order_router).__name__}, not the PaperBroker. "
            "Refusing to trade — orders must be simulated locally."
        )

    if kite_client is not None:
        assert_no_order_methods(kite_client)
