"""NSE/BSE equity-segment trading holidays — 2026 ONLY (spec §6.0, copied verbatim).

This calendar is hardcoded for 2026. Before the first trading day of 2027 — or any time
the running year is not 2026 — the agent must HALT and write a `system-alert` asking for
the fresh exchange-published calendar (spec §6.0 "Annual maintenance"). Never extrapolate
holidays into a year we don't have data for.

Source: NSE/BSE official circulars (verify against nseindia.com / bseindia.com if a date
looks off).
"""
from __future__ import annotations

CALENDAR_YEAR = 2026

# Full weekday closures for the equity segment (spec §6.0 Layer 1). Weekends (Sat/Sun)
# are non-trading too and handled separately by a weekday check.
NSE_BSE_HOLIDAYS_2026: dict[str, str] = {
    "2026-01-15": "Municipal Corp. Election (Maharashtra)",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}

# Special session: Muhurat Trading (Diwali Laxmi Pujan), Sunday 2026-11-08. A ~1-hour
# symbolic evening session. The agent does NOT trade Muhurat — research-only, log that it
# occurred. (Falls on a Sunday, so the weekend check already closes the day.)
MUHURAT_2026 = "2026-11-08"

# Holidays that already fall on weekends in 2026 (covered by the weekend check; listed for
# the audit trail only): Mahashivratri 02-15 (Sun), Id-Ul-Fitr 03-21 (Sat),
# Independence Day 08-15 (Sat), Diwali Laxmi Pujan 11-08 (Sun).
