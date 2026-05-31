"""Read-only Zerodha Kite Connect client.

Zerodha has NO API sandbox (verified 2026-05-31); Kite Connect is live-only. To honor
the spec's "never touch a live account," this client wraps a PRIVATE KiteConnect instance
and exposes ONLY read methods. It deliberately defines none of place_order / place_gtt /
modify_order / cancel_order / etc. — orders are the PaperBroker's job. A self-check at
construction proves the wrapper exposes no order surface (defence in depth).

Auth: an access_token is minted daily (expires ~6 AM IST) by `scripts/kite_login.py`.
This client just consumes it; it does not place orders, so it can never trade.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Sequence

from agent.broker.safety import FORBIDDEN_ORDER_METHODS, assert_no_order_methods

# Re-exported for callers/tests via the package __init__.
__all__ = ["KiteDataClient", "FORBIDDEN_ORDER_METHODS"]


class KiteDataClient:
    """Thin READ-ONLY wrapper over kiteconnect.KiteConnect.

    Pass a real KiteConnect by giving api_key (+access_token), or inject a fake `kite`
    object for testing. The underlying client is kept private and never returned.
    """

    def __init__(self,
                 api_key: str | None = None,
                 access_token: str | None = None,
                 kite: Any | None = None) -> None:
        if kite is not None:
            self.__kite = kite
        else:
            if not api_key:
                raise ValueError("KiteDataClient needs an api_key (or an injected kite).")
            from kiteconnect import KiteConnect  # imported lazily so tests don't require it
            self.__kite = KiteConnect(api_key=api_key)
            if access_token:
                self.__kite.set_access_token(access_token)

        self._instruments_cache: dict[str | None, list[dict[str, Any]]] = {}
        # Defence in depth: prove THIS wrapper exposes no order/write method.
        assert_no_order_methods(self)

    # --- Account reads (anchor reconciliation; not an authorization to trade) ---
    def profile(self) -> dict[str, Any]:
        return self.__kite.profile()

    def margins(self, segment: str | None = None) -> dict[str, Any]:
        return self.__kite.margins(segment) if segment else self.__kite.margins()

    def positions(self) -> dict[str, Any]:
        return self.__kite.positions()

    def holdings(self) -> list[dict[str, Any]]:
        return self.__kite.holdings()

    def orders(self) -> list[dict[str, Any]]:
        return self.__kite.orders()

    def order_history(self, order_id: str) -> list[dict[str, Any]]:
        return self.__kite.order_history(order_id)

    def order_trades(self, order_id: str) -> list[dict[str, Any]]:
        return self.__kite.order_trades(order_id)

    def trades(self) -> list[dict[str, Any]]:
        return self.__kite.trades()

    # --- Market data reads ---
    def quote(self, instruments: Sequence[str]) -> dict[str, Any]:
        """Full snapshot for "EXCH:SYMBOL" keys (up to 500)."""
        return self.__kite.quote(*list(instruments))

    def ltp(self, instruments: Sequence[str]) -> dict[str, Any]:
        return self.__kite.ltp(*list(instruments))

    def ohlc(self, instruments: Sequence[str]) -> dict[str, Any]:
        return self.__kite.ohlc(*list(instruments))

    def historical_data(self,
                        instrument_token: int,
                        from_date: str | datetime,
                        to_date: str | datetime,
                        interval: str,
                        continuous: bool = False,
                        oi: bool = False) -> list[dict[str, Any]]:
        """Candles for one instrument. interval in
        {minute,3minute,5minute,10minute,15minute,30minute,60minute,day}.
        Returns a list of {date, open, high, low, close, volume[, oi]} dicts.
        """
        return self.__kite.historical_data(
            instrument_token, from_date, to_date, interval,
            continuous=continuous, oi=oi,
        )

    # --- Instrument resolution (spec §1.3a) ---
    def instruments(self, exchange: str | None = None) -> list[dict[str, Any]]:
        """Full instrument dump for an exchange (cached). Heavy call — cache and reuse."""
        if exchange not in self._instruments_cache:
            self._instruments_cache[exchange] = (
                self.__kite.instruments(exchange) if exchange else self.__kite.instruments()
            )
        return self._instruments_cache[exchange]

    def search_instruments(self,
                           query: str,
                           filter_on: str = "tradingsymbol",
                           exchange: str | None = None,
                           limit: int = 10) -> list[dict[str, Any]]:
        """Resolve a query to instrument records (token + exch:tradingsymbol).

        filter_on: 'tradingsymbol' | 'name' | 'isin' | 'instrument_token' | 'exchange'.
        Always confirm the `exchange` field on the result before trading it (spec §1.3a).
        Equity cash only: NSE/BSE results are what we want; ignore F&O segments.
        """
        q = str(query).strip().lower()
        field = "instrument_token" if filter_on == "id" else filter_on
        out: list[dict[str, Any]] = []
        for row in self.instruments(exchange):
            value = row.get(field)
            if value is None:
                continue
            if q in str(value).strip().lower():
                out.append(row)
                if len(out) >= limit:
                    break
        return out
