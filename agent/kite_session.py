"""Hot-reloadable Kite session holder (read-only).

The `--watch` loop builds its Kite client once at startup (e.g. 08:15). If the daily login is
done AFTER that (the user logs in late), the running process would otherwise keep using the
stale, expired token for the whole session and never trade — the failure mode seen
2026-06-01. `KiteSession` fixes that: it proxies to the *current* read-only client and
re-mints it when the on-disk token changes (checked cheaply at the top of each watch pass).

The fresh token is read from `.kite_token.json` (which `kite_login.py` rewrites), NOT from
`load_settings()` — `python-dotenv` does not override an already-loaded `os.environ`, so a
re-read of `.env` in-process would still see the old token.

SAFETY: this is still a READ-ONLY data client. The proxy never adds order methods — a
forbidden-method check (`assert_no_order_methods`) sees nothing, because `__getattr__`
delegates to the underlying read-only client which has none.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


class KiteSession:
    def __init__(self, api_key: str, access_token: str, *,
                 client_factory: Callable[[str, str], Any],
                 token_loader: Callable[[], str | None] | None = None):
        self._api_key = api_key
        self._token = access_token
        self._client_factory = client_factory
        self._token_loader = token_loader
        self.client = client_factory(api_key, access_token)

    def reload(self) -> bool:
        """If the on-disk token differs from the one in use, rebuild the client. Returns True
        iff a fresh token was picked up (a late login). No-op if there's no loader or no change."""
        if self._token_loader is None:
            return False
        tok = self._token_loader()
        if tok and tok != self._token:
            self._token = tok
            self.client = self._client_factory(self._api_key, tok)
            return True
        return False

    def __getattr__(self, name: str) -> Any:
        # only reached for names not set on the instance → proxy to the live read-only client
        client = self.__dict__.get("client")
        if client is None:
            raise AttributeError(name)
        return getattr(client, name)


def token_from_file(path: str | Path) -> Callable[[], str | None]:
    """A `token_loader` that reads `access_token` from a kite_login.py token file
    (`.kite_token.json`). Returns None if absent/unreadable (→ no reload, keep current)."""
    p = Path(path)

    def _load() -> str | None:
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("access_token")
        except (json.JSONDecodeError, OSError):
            return None

    return _load
