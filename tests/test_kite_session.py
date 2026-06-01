"""Hot-reloadable Kite session: late-login token pickup + read-only safety."""
import json

from agent.kite_session import KiteSession, token_from_file
from agent.broker.safety import assert_no_order_methods


class _FakeClient:
    """Minimal read-only stand-in; records which token it was built with."""
    def __init__(self, api_key, access_token):
        self.api_key = api_key
        self.access_token = access_token

    def ltp(self, instruments):
        return {i: {"last_price": 100.0, "_tok": self.access_token} for i in instruments}


def _factory(key, tok):
    return _FakeClient(key, tok)


def test_proxy_delegates_to_current_client():
    sess = KiteSession("k", "tok1", client_factory=_factory)
    out = sess.ltp(["NSE:RELIANCE"])      # proxied to the underlying client
    assert out["NSE:RELIANCE"]["_tok"] == "tok1"


def test_reload_picks_up_changed_token():
    holder = {"tok": "tok1"}
    sess = KiteSession("k", "tok1", client_factory=_factory,
                       token_loader=lambda: holder["tok"])
    assert sess.reload() is False                 # unchanged → no rebuild
    holder["tok"] = "tok2"                          # simulate a late login
    assert sess.reload() is True                    # picked up
    assert sess.ltp(["X"])["X"]["_tok"] == "tok2"   # now using the fresh token


def test_reload_noop_when_no_loader():
    sess = KiteSession("k", "tok1", client_factory=_factory)
    assert sess.reload() is False


def test_reload_ignores_missing_token():
    sess = KiteSession("k", "tok1", client_factory=_factory, token_loader=lambda: None)
    assert sess.reload() is False
    assert sess.client.access_token == "tok1"       # kept the working token


def test_token_from_file(tmp_path):
    p = tmp_path / ".kite_token.json"
    p.write_text(json.dumps({"access_token": "abc123", "user_id": "AB1"}), encoding="utf-8")
    assert token_from_file(p)() == "abc123"
    assert token_from_file(tmp_path / "nope.json")() is None       # missing → None
    p.write_text("{ broken", encoding="utf-8")
    assert token_from_file(p)() is None                            # corrupt → None


def test_session_exposes_no_order_methods():
    # the safety guard must see NO order methods through the proxy (read-only invariant)
    sess = KiteSession("k", "tok1", client_factory=_factory)
    assert_no_order_methods(sess)   # must not raise


def test_real_kite_client_through_session(fake_kite):
    # end-to-end with the real read-only wrapper proxied behind the session
    from agent.broker.kite_client import KiteDataClient
    sess = KiteSession("k", "tok1",
                       client_factory=lambda key, tok: KiteDataClient(kite=fake_kite))
    assert_no_order_methods(sess)
    assert sess.profile()["user_id"] == "AB1234"
