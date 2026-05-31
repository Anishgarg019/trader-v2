"""Structured logging (spec §6.4 hardening). Cross-platform stream logging, idempotent."""
from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str = "trading-agent", level: int = logging.INFO) -> logging.Logger:
    """Return a configured logger. Adds a single stdout handler once (no duplicates on
    repeated calls)."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not any(getattr(h, "_trading_agent", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        handler._trading_agent = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.propagate = False
    return logger
