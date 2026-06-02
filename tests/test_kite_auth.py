"""Headless auth: request-token extraction, TOTP login flow, session persistence
(mocked — no live Kite, no network)."""
import json

import pytest

from agent.kite_auth import extract_request_token, auto_request_token, persist_session


# ---- extract_request_token --------------------------------------------------
def test_extract_bare_token():
    assert extract_request_token("ABC123") == "ABC123"


def test_extract_from_full_redirect_url():
    url = "https://127.0.0.1/?action=login&status=success&request_token=XYZ789"
    assert extract_request_token(url) == "XYZ789"


def test_extract_from_query_string():
    assert extract_request_token("request_token=Q1&action=login") == "Q1"


def test_extract_empty_is_none():
    assert extract_request_token("") is None
    assert extract_request_token("   ") is None


# ---- auto_request_token (TOTP flow) -----------------------------------------
class _Resp:
    def __init__(self, json_data=None, location=None, url=""):
        self._json = json_data or {}
        self.headers = {"Location": location} if location else {}
        self.url = url

    def json(self):
        return self._json


class _Session:
    def __init__(self, login_json, twofa_json, get_results):
        self._login, self._twofa = login_json, twofa_json
        self._get = list(get_results)
        self.posts = []

    def post(self, url, data=None):
        self.posts.append((url, data))
        return _Resp(self._login if url.endswith("/login") else self._twofa)

    def get(self, url, allow_redirects=False, timeout=None):
        r = self._get.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _ok(extra=None):
    return {"status": "success", "data": (extra or {})}


def test_auto_request_token_happy_path():
    sess = _Session(
        login_json=_ok({"request_id": "rid-1"}),
        twofa_json=_ok(),
        get_results=[_Resp(location="https://127.0.0.1/?status=success&request_token=GOOD123")],
    )
    tok = auto_request_token(api_key="k", user_id="u", password="p", totp_secret="s",
                             session=sess, login_url="https://kite/connect/login",
                             totp_now=lambda: "123456")
    assert tok == "GOOD123"
    # the TOTP value we generated was submitted in the 2FA POST
    twofa_post = [p for p in sess.posts if p[0].endswith("/twofa")][0]
    assert twofa_post[1]["twofa_value"] == "123456"
    assert twofa_post[1]["request_id"] == "rid-1"


def test_auto_request_token_follows_redirect_chain():
    sess = _Session(
        login_json=_ok({"request_id": "rid"}), twofa_json=_ok(),
        get_results=[
            _Resp(location="https://kite/connect/finish?x=1"),          # intermediate hop
            _Resp(location="https://127.0.0.1/?request_token=AFTER2HOPS"),
        ])
    tok = auto_request_token(api_key="k", user_id="u", password="p", totp_secret="s",
                             session=sess, login_url="https://kite/connect/login",
                             totp_now=lambda: "000000")
    assert tok == "AFTER2HOPS"


def test_auto_request_token_recovers_from_redirect_connection_error():
    class _Req:  # mimics requests' exception carrying the attempted URL
        url = "https://127.0.0.1/?request_token=FROMERR"
    err = ConnectionError("refused"); err.request = _Req()
    sess = _Session(_ok({"request_id": "r"}), _ok(), get_results=[err])
    tok = auto_request_token(api_key="k", user_id="u", password="p", totp_secret="s",
                             session=sess, login_url="https://kite/connect/login",
                             totp_now=lambda: "1")
    assert tok == "FROMERR"


def test_auto_request_token_raises_on_bad_password():
    sess = _Session({"status": "error", "message": "bad password"}, _ok(), [])
    with pytest.raises(RuntimeError, match="password login failed"):
        auto_request_token(api_key="k", user_id="u", password="p", totp_secret="s",
                           session=sess, login_url="x", totp_now=lambda: "1")


def test_auto_request_token_raises_on_bad_totp():
    sess = _Session(_ok({"request_id": "r"}), {"status": "error", "message": "wrong totp"}, [])
    with pytest.raises(RuntimeError, match="TOTP 2FA failed"):
        auto_request_token(api_key="k", user_id="u", password="p", totp_secret="s",
                           session=sess, login_url="x", totp_now=lambda: "1")


# ---- persist_session --------------------------------------------------------
class _FakeKite:
    def __init__(self):
        self.token = None

    def generate_session(self, request_token, api_secret):
        assert request_token == "REQ" and api_secret == "secret"
        return {"access_token": "ACCESS-TOK", "user_id": "VVX1", "user_name": "Tester",
                "login_time": "2026-06-02 09:00:00"}

    def set_access_token(self, t):
        self.token = t

    def profile(self):
        assert self.token == "ACCESS-TOK"   # must confirm with the new token
        return {"user_name": "Tester", "user_id": "VVX1"}


def test_persist_session_writes_token(tmp_path):
    env = tmp_path / ".env"; env.write_text("MODE=paper\nKITE_ACCESS_TOKEN=old\n", encoding="utf-8")
    tok = tmp_path / ".kite_token.json"
    data = persist_session(api_key="k", api_secret="secret", request_token="REQ",
                           env_path=env, token_path=tok, kite=_FakeKite())
    assert data["user_name"] == "Tester"
    saved = json.loads(tok.read_text())
    assert saved["access_token"] == "ACCESS-TOK" and saved["user_id"] == "VVX1"
    assert "ACCESS-TOK" in env.read_text() and "KITE_ACCESS_TOKEN" in env.read_text()  # .env updated
