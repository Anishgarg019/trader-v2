"""Mint a Kite Connect daily access token (read-only data use).

Kite access tokens expire ~6 AM IST and require a one-time interactive login each day.
Run:  python scripts/kite_login.py

Flow: open the printed login URL → log in → Kite redirects to your app's redirect URL
with `?request_token=...` → paste that token here. We exchange it for an access_token,
write it to `.kite_token.json` and update `KITE_ACCESS_TOKEN` in `.env`.

This script only authenticates and reads `profile()` to confirm. It places NO orders.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

from dotenv import load_dotenv, set_key


def extract_request_token(raw: str) -> str:
    """Accept the bare request_token OR the full redirect URL / query string and return
    just the token. Kite redirects to e.g.
    https://127.0.0.1/?action=login&status=success&request_token=ABC123 — pasting that
    whole thing should work."""
    raw = raw.strip()
    if "request_token=" not in raw:
        return raw
    parsed = urllib.parse.urlparse(raw)
    query = parsed.query or raw  # bare "request_token=..." has no scheme → query is empty
    return urllib.parse.parse_qs(query).get("request_token", [raw])[0]

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
    pasted = input("Paste the request_token (or the full redirect URL) here: ").strip()
    request_token = extract_request_token(pasted)
    if not request_token:
        print("ERROR: no request_token provided.", file=sys.stderr)
        return 1

    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]

    TOKEN_PATH.write_text(json.dumps({
        "access_token": access_token,
        "user_id": data.get("user_id"),
        "login_time": str(data.get("login_time")),
    }, indent=2))
    if ENV_PATH.exists():
        set_key(str(ENV_PATH), "KITE_ACCESS_TOKEN", access_token)

    # Confirm read access (no orders placed).
    kite.set_access_token(access_token)
    profile = kite.profile()
    print(f"\n✅ Logged in as {profile.get('user_name')} ({profile.get('user_id')}).")
    print(f"   Access token written to {TOKEN_PATH.name} and .env (KITE_ACCESS_TOKEN).")
    print("   Token expires ~6 AM IST tomorrow — rerun this script daily.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
