"""ntfy.sh push notifications (best-effort). Used for run summaries and FAILURE alerts on
the scheduled tasks (Phase 11). Config in .env: NTFY_TOPIC (+ optional NTFY_SERVER).

A notification failing must NEVER be fatal — every call swallows its own errors.
"""
from __future__ import annotations

import os
import urllib.request


def send_ntfy(message: str, *, title: str = "Trader", tags: str = "robot",
              priority: str | None = None) -> bool:
    """Send a push to the configured ntfy topic. Returns True if sent, False otherwise.
    No-op (returns False) if NTFY_TOPIC isn't set. Never raises."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return False
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {"Title": title, "Tags": tags}
    if priority:
        headers["Priority"] = priority
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"{server}/{topic}", data=message.encode("utf-8"), headers=headers), timeout=10)
        return True
    except Exception:  # noqa: BLE001 — a notification must never break the run
        return False


def notify_failure(task: str, error: BaseException) -> None:
    """Push a high-priority failure alert for a scheduled task."""
    send_ntfy(f"{task} FAILED: {type(error).__name__}: {error}",
              title=f"⚠️ {task} failed", tags="rotating_light", priority="high")
