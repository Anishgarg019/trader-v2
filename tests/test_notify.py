"""The ntfy login-reminder builds a correct request (no network in tests)."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "notify_login.py"


def _load():
    spec = importlib.util.spec_from_file_location("notify_login", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nl = _load()


def test_build_request_default_server():
    req = nl.build_request("my-topic")
    assert req.full_url == "https://ntfy.sh/my-topic"
    assert b"kite_login" in req.data
    assert req.headers["Title"] == "Kite daily login"


def test_build_request_custom_server_strips_slash():
    req = nl.build_request("t", server="https://ntfy.example.com/")
    assert req.full_url == "https://ntfy.example.com/t"
