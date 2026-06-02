"""Kite authentication helpers (read-only token minting).

Two things, shared by the manual (`scripts/kite_login.py`) and the UNATTENDED
(`scripts/auto_login.py`) login paths:

  - `persist_session(...)` — exchange a request_token for an access_token and write it to
    `.kite_token.json` + `.env` (the single source of truth both run_loop and the researcher
    read).
  - `auto_request_token(...)` — drive the Kite web login headlessly with user_id + password
    + a TOTP secret (`pyotp`) and return the `request_token`, so the daily login needs NO
    human action. This is the permanent fix for "the token expired because nobody logged in."

SAFETY: this only MINTS the daily **read-only data** access token. It does not — and cannot —
place orders: the Kite *data* client exposes no order methods, and all orders still route
through the local PaperBroker. Automating login does not touch the capital-preservation
rails. The credentials it needs live only in `.env` (gitignored), on the user's own box.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs

KITE_LOGIN_API = "https://kite.zerodha.com/api/login"
KITE_TWOFA_API = "https://kite.zerodha.com/api/twofa"


def extract_request_token(raw: str) -> str | None:
    """Pull `request_token` from a bare token, a query string, or a full redirect URL."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "request_token=" not in raw:
        return raw
    query = urlparse(raw).query or raw
    vals = parse_qs(query).get("request_token")
    return vals[0] if vals else None


def persist_session(*, api_key: str, api_secret: str, request_token: str,
                    env_path: Path, token_path: Path, kite=None) -> dict:
    """Exchange `request_token` → access_token, confirm read access, persist. Returns the
    session data dict (incl. user_name). `kite` is injectable for tests."""
    if kite is None:
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=api_key)
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]

    token_path.write_text(json.dumps({
        "access_token": access_token,
        "user_id": data.get("user_id"),
        "login_time": str(data.get("login_time")),
    }, indent=2), encoding="utf-8")
    if env_path.exists():
        from dotenv import set_key
        set_key(str(env_path), "KITE_ACCESS_TOKEN", access_token)

    kite.set_access_token(access_token)
    kite.profile()   # confirm the token works (read-only call; raises if bad)
    return data


def auto_request_token(*, api_key: str, user_id: str, password: str, totp_secret: str,
                       session=None, login_url: str | None = None, totp_now=None) -> str:
    """Headless Kite web login (user_id + password + TOTP) → `request_token`.

    Follows the login redirect chain manually and grabs `request_token` from the first
    redirect that carries it — before the hop to the app's redirect URL (e.g. 127.0.0.1,
    which isn't listening). `session`/`login_url`/`totp_now` are injectable for tests.
    """
    if session is None:
        import requests
        session = requests.Session()
    if totp_now is None:
        import pyotp
        totp_now = lambda: pyotp.TOTP(totp_secret).now()
    if login_url is None:
        from kiteconnect import KiteConnect
        login_url = KiteConnect(api_key=api_key).login_url()

    # 1) password login → request_id
    r1 = session.post(KITE_LOGIN_API, data={"user_id": user_id, "password": password})
    body1 = r1.json()
    if body1.get("status") != "success":
        raise RuntimeError(f"password login failed: {body1.get('message') or body1}")
    request_id = body1["data"]["request_id"]

    # 2) TOTP 2FA
    r2 = session.post(KITE_TWOFA_API, data={
        "user_id": user_id, "request_id": request_id,
        "twofa_value": totp_now(), "twofa_type": "totp"})
    body2 = r2.json()
    if body2.get("status") != "success":
        raise RuntimeError(f"TOTP 2FA failed: {body2.get('message') or body2}")

    # 3) follow the connect-login redirects; capture request_token before the 127.0.0.1 hop
    url = login_url
    for _ in range(10):
        try:
            resp = session.get(url, allow_redirects=False, timeout=15)
        except Exception as e:  # noqa: BLE001 — the redirect_url (127.0.0.1) may refuse
            tok = extract_request_token(getattr(getattr(e, "request", None), "url", "") or "")
            if tok:
                return tok
            raise RuntimeError(f"login redirect failed before yielding a request_token: {e}")
        for candidate in (resp.headers.get("Location", ""), getattr(resp, "url", "")):
            if candidate and "request_token=" in candidate:
                tok = extract_request_token(candidate)
                if tok:
                    return tok
        loc = resp.headers.get("Location")
        if not loc:
            break
        url = loc if loc.startswith("http") else _join(url, loc)
    raise RuntimeError("could not obtain a request_token from the Kite login flow")


def _join(base: str, loc: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, loc)
