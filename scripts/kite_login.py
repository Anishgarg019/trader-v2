"""Mint a Kite Connect daily access token (read-only data use).

Kite access tokens expire ~6 AM IST and require a one-time interactive login each day.
Run:  python scripts/kite_login.py

Flow: open the printed login URL → log in → Kite redirects to your app's redirect URL
with `?request_token=...` → paste that token here. We exchange it for an access_token,
write it to `.kite_token.json` and update `KITE_ACCESS_TOKEN` in `.env`.

This script only authenticates and reads `profile()` to confirm. It places NO orders.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from agent.kite_auth import extract_request_token, persist_session

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
TOKEN_PATH = REPO_ROOT / ".kite_token.json"


def main() -> int:
    load_dotenv(ENV_PATH)
    import os
    api_key = os.environ.get("KITE_API_KEY")
    api_secret = os.environ.get("KITE_API_SECRET")
    if not api_key or not api_secret:
        print("ERROR: set KITE_API_KEY and KITE_API_SECRET in .env first.", file=sys.stderr)
        return 1

    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)

    print("\n1) Open this URL, log in, and authorize:\n")
    print("   " + kite.login_url() + "\n")
    print("2) After redirect, copy the `request_token` value from the URL.\n")
    request_token = extract_request_token(
        input("Paste the request_token (or the full redirect URL) here: "))
    if not request_token:
        print("ERROR: no request_token provided.", file=sys.stderr)
        return 1

    # Exchange + persist (shared with scripts/auto_login.py). ASCII only — a unicode ✅
    # crashes the Windows cp1252 console, making a SUCCESSFUL login look like a failure.
    data = persist_session(api_key=api_key, api_secret=api_secret,
                           request_token=request_token, env_path=ENV_PATH,
                           token_path=TOKEN_PATH, kite=kite)
    print(f"\n[OK] Logged in as {data.get('user_name')} ({data.get('user_id')}).")
    print(f"   Access token written to {TOKEN_PATH.name} and .env (KITE_ACCESS_TOKEN).")
    print("   Token expires ~6 AM IST tomorrow.  Unattended? scripts/auto_login.py mints it for you.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
