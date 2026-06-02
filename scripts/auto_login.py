"""Unattended daily Kite login (the permanent fix for missed logins).

Kite access tokens expire ~6 AM IST and normally need an interactive login. This script mints
the daily token HEADLESSLY using user_id + password + a TOTP secret (`pyotp`), so the agent
never goes blind because nobody logged in. Schedule it ~07:00 IST (before the 08:15 loop).

  python scripts/auto_login.py

Requires in `.env` (gitignored; one-time setup):
  KITE_USER_ID, KITE_PASSWORD, KITE_TOTP_SECRET   (+ the existing KITE_API_KEY / KITE_API_SECRET)
KITE_TOTP_SECRET is the base32 seed behind your Zerodha 2FA TOTP (the "manual entry" /
"can't scan" string shown when you set up the authenticator), NOT a 6-digit code.

SAFETY: mints only the READ-ONLY data token; never places orders (the Kite data client has no
order methods; orders go only to PaperBroker). On ANY failure it pings ntfy so you can fall
back to the manual `kite_login.py`. Idempotent — safe to run more than once a day.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import load_settings, REPO_ROOT
from agent.logging_setup import get_logger
from agent.kite_auth import auto_request_token, persist_session
from agent.notify import send_ntfy, notify_failure

log = get_logger()
ENV_PATH = REPO_ROOT / ".env"
TOKEN_PATH = REPO_ROOT / ".kite_token.json"


def main() -> int:
    s = load_settings()
    if not s.is_paper:
        log.error("MODE is not 'paper' — refusing to run.")
        return 2
    if not s.can_auto_login:
        msg = ("auto-login not configured: set KITE_USER_ID, KITE_PASSWORD, KITE_TOTP_SECRET "
               "in .env (plus KITE_API_KEY/SECRET). Falling back to manual kite_login.py.")
        log.error(msg)
        send_ntfy(msg, title="Kite auto-login not configured", tags="warning", priority="high")
        return 1

    try:
        request_token = auto_request_token(
            api_key=s.kite_api_key, user_id=s.kite_user_id,
            password=s.kite_password, totp_secret=s.kite_totp_secret)
        data = persist_session(api_key=s.kite_api_key, api_secret=s.kite_api_secret,
                               request_token=request_token, env_path=ENV_PATH,
                               token_path=TOKEN_PATH)
    except Exception as e:  # noqa: BLE001 — alert + nonzero so the watchdog/manual path kicks in
        log.error("auto-login FAILED: %s", e)
        notify_failure("Kite auto-login", e)
        return 1

    log.info("auto-login OK — token refreshed for %s (%s)",
             data.get("user_name"), data.get("user_id"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
