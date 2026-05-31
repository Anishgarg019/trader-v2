"""Trading-day gate (spec §6.0).

The first decision of every loop: is the market open today? Resolved with TWO independent
layers, either of which can mark the day closed:

  Layer 1 — hardcoded NSE/BSE calendar + weekend check (fast, deterministic).
  Layer 2 — live full-universe data probe (catches unscheduled closures / outages): if the
            WHOLE universe returns stale/empty quotes, the day is treated as CLOSED.

A day is a trading day only if BOTH agree it's open. The safe default whenever they
disagree, or the tape is dark, is: don't trade (research-only).

This module is pure/deterministic: it accepts an already-fetched `quotes` dict and the
current IST time, so it is fully testable. The loop (Phase 8) does the actual I/O and the
pre-open re-probe.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from data.holidays_2026 import (
    CALENDAR_YEAR, NSE_BSE_HOLIDAYS_2026, MUHURAT_2026,
)

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
STALE_MINUTES_DEFAULT = 15


# --------------------------------------------------------------------------- #
# Layer 1 — calendar + weekend
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CalendarResult:
    is_open: bool
    reason: str
    holiday_name: str | None = None
    is_muhurat: bool = False
    year_supported: bool = True


def calendar_check(d: date) -> CalendarResult:
    """Layer 1: weekend / hardcoded-holiday / year-coverage check."""
    if d.year != CALENDAR_YEAR:
        return CalendarResult(
            is_open=False, year_supported=False,
            reason=(f"calendar is hardcoded for {CALENDAR_YEAR}; today is {d.year}. "
                    "HALT and obtain the fresh exchange-published calendar (spec §6.0)."),
        )
    iso = d.isoformat()
    is_muhurat = iso == MUHURAT_2026
    # Saturday=5, Sunday=6
    if d.weekday() >= 5:
        reason = "weekend (non-trading)"
        if is_muhurat:
            reason = "Muhurat Trading day (Diwali) — symbolic session; agent does not trade it → research-only"
        return CalendarResult(is_open=False, reason=reason, is_muhurat=is_muhurat)
    if iso in NSE_BSE_HOLIDAYS_2026:
        return CalendarResult(is_open=False, reason=f"holiday: {NSE_BSE_HOLIDAYS_2026[iso]}",
                              holiday_name=NSE_BSE_HOLIDAYS_2026[iso], is_muhurat=is_muhurat)
    return CalendarResult(is_open=True, reason="weekday, not a hardcoded holiday")


# --------------------------------------------------------------------------- #
# Layer 2 — live full-universe data probe
# --------------------------------------------------------------------------- #
def _to_ist(value) -> datetime | None:
    """Parse a Kite timestamp (datetime or 'YYYY-MM-DD HH:MM:SS') to an aware IST datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)


def _in_session(now_ist: datetime) -> bool:
    return MARKET_OPEN <= now_ist.timetz().replace(tzinfo=None) <= MARKET_CLOSE


def classify_quote(quote: dict | None,
                   now_ist: datetime,
                   trading_date: date,
                   stale_minutes: int = STALE_MINUTES_DEFAULT) -> str:
    """Classify a single quote as 'live' | 'stale' | 'empty' (spec §6.0 Layer 2 #3).

    empty: key absent (quote is None) or last_price missing/zero/null.
    stale: last trade timestamp not from the current trading date, OR — during live hours —
           older than `stale_minutes`.
    live:  otherwise.
    """
    if not quote:
        return "empty"
    lp = quote.get("last_price")
    if lp is None or lp == 0:
        return "empty"
    ltt = _to_ist(quote.get("last_trade_time") or quote.get("timestamp"))
    if ltt is None:
        # price present but no usable timestamp → can't prove staleness; treat as live.
        return "live"
    if ltt.date() != trading_date:
        return "stale"
    if _in_session(now_ist) and (now_ist - ltt) > timedelta(minutes=stale_minutes):
        return "stale"
    return "live"


@dataclass(frozen=True)
class ProbeResult:
    classes: dict[str, str]
    all_dark: bool          # every universe name stale/empty (→ universe-wide closure signal)
    dark_names: list[str]   # the stale/empty names (for system-alert / per-name exclusion)

    @property
    def some_dark(self) -> bool:
        return bool(self.dark_names) and not self.all_dark


def probe_universe(quotes: dict[str, dict],
                   universe: list[str],
                   now_ist: datetime,
                   trading_date: date,
                   stale_minutes: int = STALE_MINUTES_DEFAULT) -> ProbeResult:
    """Layer 2: classify every universe name. Closure determinant is universe-WIDE:
    closed only if ALL names are stale/empty. Some-but-not-all dark = per-name data
    integrity problem (exclude those names per spec §6.1.2), not a market closure."""
    classes = {
        name: classify_quote(quotes.get(name), now_ist, trading_date, stale_minutes)
        for name in universe
    }
    dark = [n for n, c in classes.items() if c in ("stale", "empty")]
    all_dark = bool(universe) and len(dark) == len(universe)
    return ProbeResult(classes=classes, all_dark=all_dark, dark_names=dark)


# --------------------------------------------------------------------------- #
# Combined decision
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TradingDayDecision:
    is_trading_day: bool
    research_only: bool
    reason: str
    layer: str                      # 'maintenance' | 'calendar' | 'probe' | 'pre-open'
    needs_reprobe: bool = False     # booted pre-open with a quiet tape → re-probe after 09:15
    needs_probe: bool = False       # calendar open but no quotes supplied yet
    system_alert: bool = False      # write a system-alert note
    halt: bool = False              # hard halt (e.g. wrong calendar year)
    holiday_name: str | None = None
    is_muhurat: bool = False
    dark_names: list[str] = field(default_factory=list)


def decide_trading_day(d: date,
                       now_ist: datetime,
                       universe: list[str] | None = None,
                       quotes: dict[str, dict] | None = None,
                       stale_minutes: int = STALE_MINUTES_DEFAULT) -> TradingDayDecision:
    """Combine both layers into a single decision (spec §6.0 #4).

    - Wrong calendar year → halt (research-only) + system-alert.
    - Calendar closed (weekend/holiday/Muhurat) → research-only; skip the probe.
    - Calendar open but no quotes/universe → needs_probe (loop must fetch and re-call).
    - Calendar open + universe all-dark:
        * before 09:15 IST → pre-open quiet, NOT closed → needs_reprobe.
        * at/after 09:15   → CLOSED (unscheduled closure / outage) + system-alert.
    - Calendar open + live tape across universe → TRADING DAY.
    """
    cal = calendar_check(d)
    if not cal.year_supported:
        return TradingDayDecision(False, True, cal.reason, layer="maintenance",
                                  halt=True, system_alert=True)
    if not cal.is_open:
        return TradingDayDecision(False, True, cal.reason, layer="calendar",
                                  holiday_name=cal.holiday_name, is_muhurat=cal.is_muhurat)

    if quotes is None or universe is None:
        return TradingDayDecision(False, True, "calendar open; awaiting Layer-2 universe probe",
                                  layer="calendar", needs_probe=True)

    probe = probe_universe(quotes, universe, now_ist, d, stale_minutes)
    before_open = now_ist.timetz().replace(tzinfo=None) < MARKET_OPEN

    if probe.all_dark:
        if before_open:
            return TradingDayDecision(
                False, True,
                "booted pre-open with a quiet universe — not a closure; re-probe after 09:15",
                layer="pre-open", needs_reprobe=True, dark_names=probe.dark_names)
        return TradingDayDecision(
            False, True,
            "whole universe stale/empty during/after market open — treating day as CLOSED "
            "(unscheduled closure or data outage)",
            layer="probe", system_alert=True, dark_names=probe.dark_names)

    # Live tape across (at least part of) the universe → trading day. Some-dark names are a
    # per-name data-integrity issue to exclude downstream (spec §6.1.2), not a closure.
    reason = "calendar open and live tape seen across universe"
    if probe.some_dark:
        reason += f"; exclude stale/empty names: {probe.dark_names}"
    return TradingDayDecision(True, False, reason, layer="probe", dark_names=probe.dark_names)
