"""Send an ntfy.sh push reminder to do the daily Kite login.

Kite tokens expire ~6 AM IST. Schedule this at 07:30 IST (Task Scheduler) to ping your
phone; tap it, run `python scripts\\kite_login.py`, and the day's scheduled loop will work.

Config in .env:
  NTFY_TOPIC    your ntfy topic (e.g. trader-anish-7f3k) — subscribe to it in the ntfy app
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
    try:
        urllib.request.urlopen(build_request(topic, server), timeout=10)
    except Exception as e:  # noqa: BLE001 — a reminder failing must not be fatal
        print(f"ERROR: ntfy send failed: {e}", file=sys.stderr)
        return 1
    print(f"sent login reminder to {server.rstrip('/')}/{topic}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
