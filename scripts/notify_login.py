"""Token WATCHDOG + fallback login nudge (ntfy).

The permanent fix for missed logins is unattended auto-login (scripts/auto_login.py ~07:00).
This runs AFTER it (~07:30) as a backstop: it checks whether today's token is actually valid
and only pings your phone (high priority) if it is NOT — i.e. auto-login failed or never ran.
So on a healthy day it's silent; on a broken day you get one loud nudge to run kite_login.py.

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

MESSAGE = ("Kite token is NOT valid for today (auto-login failed/skipped). Run  "
           "python scripts\\kite_login.py  to refresh it, or the agent stays blind today.")


def build_request(topic: str, server: str = "https://ntfy.sh", message: str = MESSAGE):
    server = server.rstrip("/")
    return urllib.request.Request(
        f"{server}/{topic}", data=message.encode("utf-8"),
        headers={"Title": "Kite daily login", "Priority": "high",
                 "Tags": "key"},
    )


def token_is_valid(settings) -> bool:
    """Read-only probe: is today's access token live? (no orders placed)."""
    if not (settings.kite_api_key and settings.kite_access_token):
        return False
    try:
        from agent.broker.kite_client import KiteDataClient
        KiteDataClient(api_key=settings.kite_api_key,
                       access_token=settings.kite_access_token).profile()
        return True
    except Exception:  # noqa: BLE001 — any failure ⇒ treat as invalid (alert)
        return False


def main() -> int:
    s = load_settings()  # loads .env into the environment
    if token_is_valid(s):
        print("token is valid for today — no reminder needed.")
        return 0

    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("ERROR: token invalid AND no NTFY_TOPIC set — cannot alert.", file=sys.stderr)
        return 1
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    # Retry with backoff: at 07:30 the box may have just woken and the network may not be up
    # yet. Without this, a slow-network morning silently kills the nudge (failure seen
    # 2026-06-01). ~5,10,20,40,60s ≈ 2 min.
    try:
        call_with_retries(
            lambda: urllib.request.urlopen(build_request(topic, server), timeout=10),
            retries=5, base_delay=5.0, max_delay=60.0,
            on_retry=lambda n, e, d: print(
                f"ntfy attempt {n} failed ({e}); retrying in {d:.0f}s", file=sys.stderr))
    except Exception as e:  # noqa: BLE001 — exhausted retries; reminder failure is non-fatal
        print(f"ERROR: ntfy send failed after retries: {e}", file=sys.stderr)
        return 1
    print(f"token INVALID — sent login reminder to {server.rstrip('/')}/{topic}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
