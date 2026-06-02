"""Daily 07:30 IST push reminder to do the manual Kite login (ntfy).

Kite tokens expire ~6 AM IST and the login is done by hand (no stored credentials). This
fires at 07:30 to ping your phone; tap it, run `python scripts\\kite_login.py`, and the
08:15 loop trades with a fresh token.

Reliability: the ntfy send retries with backoff (~2 min) in case the box's network isn't up
yet at 07:30 — so a slow-network morning still gets through. (The task must also be created
with a SPACE between the python exe and this script path; a glued `python.exeC:\\...py`
silently fails with exit code 2 — the bug that caused the missed nudges on 2026-06-01/02.)

Config in .env:
  NTFY_TOPIC    your ntfy topic — subscribe to it in the ntfy app
  NTFY_SERVER   optional, default https://ntfy.sh

  python scripts/notify_login.py
"""
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import load_settings
from agent.retry import call_with_retries

MESSAGE = "Run  python scripts\\kite_login.py  to refresh today's Kite token — then the agent trades."


def build_request(topic: str, server: str = "https://ntfy.sh", message: str = MESSAGE):
    server = server.rstrip("/")
    return urllib.request.Request(
        f"{server}/{topic}", data=message.encode("utf-8"),
        headers={"Title": "Kite daily login", "Priority": "high",
                 "Tags": "key"},
    )


def main() -> int:
    load_settings()  # loads .env into the environment
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("ERROR: set NTFY_TOPIC in .env first.", file=sys.stderr)
        return 1
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    # Retry with backoff: at 07:30 the box may have just woken and the network may not be up
    # yet. Without this, a slow-network morning silently kills the only nudge and the day
    # quietly doesn't trade (failure seen 2026-06-01). ~5,10,20,40,60s ≈ 2 min.
    try:
        call_with_retries(
            lambda: urllib.request.urlopen(build_request(topic, server), timeout=10),
            retries=5, base_delay=5.0, max_delay=60.0,
            on_retry=lambda n, e, d: print(
                f"ntfy attempt {n} failed ({e}); retrying in {d:.0f}s", file=sys.stderr))
    except Exception as e:  # noqa: BLE001 — exhausted retries; reminder failure is non-fatal
        print(f"ERROR: ntfy send failed after retries: {e}", file=sys.stderr)
        return 1
    print(f"sent login reminder to {server.rstrip('/')}/{topic}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
