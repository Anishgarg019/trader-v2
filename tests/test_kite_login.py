"""The daily-login helper should accept a bare token OR the full redirect URL."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "kite_login.py"


def _load():
    spec = importlib.util.spec_from_file_location("kite_login", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kl = _load()


def test_bare_token():
    assert kl.extract_request_token("IdtFavJ2Vn7CakaZ6rOXUx72PVznFFAS") == "IdtFavJ2Vn7CakaZ6rOXUx72PVznFFAS"


def test_full_redirect_url():
    url = ("https://127.0.0.1/?action=login&type=login&status=success&"
           "request_token=IdtFavJ2Vn7CakaZ6rOXUx72PVznFFAS")
    assert kl.extract_request_token(url) == "IdtFavJ2Vn7CakaZ6rOXUx72PVznFFAS"


def test_bare_query_string():
    assert kl.extract_request_token("request_token=ABC123&status=success") == "ABC123"


def test_whitespace_trimmed():
    assert kl.extract_request_token("  ABC123  ") == "ABC123"
