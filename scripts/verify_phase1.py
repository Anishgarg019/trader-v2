"""Phase 1 live checkpoint (read-only).

Confirms, against real Kite data: resolve a symbol -> instrument_token, pull day candles,
fetch a live quote — all read-only, paper guard passing, NO order placed.

Run after `python scripts/kite_login.py`:
    python scripts/verify_phase1.py [SYMBOL]   # default RELIANCE
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

from agent.config import load_settings
from agent.broker.kite_client import KiteDataClient
from agent.broker.paper_broker import PaperBroker
from agent.broker.safety import assert_paper_mode


def main(symbol: str = "RELIANCE") -> int:
    s = load_settings()
    if not (s.kite_api_key and s.kite_access_token):
        print("ERROR: set KITE_API_KEY in .env and run scripts/kite_login.py first.")
        return 1

    client = KiteDataClient(api_key=s.kite_api_key, access_token=s.kite_access_token)

    # Safety guard must pass before we do anything tradeable (we never trade here anyway).
    assert_paper_mode(PaperBroker(), kite_client=client, mode=s.mode)
    print("✅ paper-mode guard passed; Kite client is read-only (no order methods).")

    # 1) resolve symbol -> token (NSE)
    matches = client.search_instruments(symbol, filter_on="tradingsymbol", exchange="NSE", limit=5)
    matches = [m for m in matches if m["tradingsymbol"] == symbol] or matches
    if not matches:
        print(f"ERROR: could not resolve {symbol} on NSE.")
        return 1
    inst = matches[0]
    token = inst["instrument_token"]
    exch_symbol = f"{inst['exchange']}:{inst['tradingsymbol']}"
    print(f"✅ resolved {exch_symbol} -> instrument_token {token}")

    # 2) pull ~10 day candles
    to_d = date.today()
    from_d = to_d - timedelta(days=20)
    candles = client.historical_data(token, str(from_d), str(to_d), "day")
    print(f"✅ pulled {len(candles)} day candles; last close = "
          f"{candles[-1]['close'] if candles else 'n/a'}")

    # 3) live quote
    q = client.quote([exch_symbol])
    lp = q.get(exch_symbol, {}).get("last_price")
    print(f"✅ live quote last_price = {lp}")

    print("\nPhase 1 checkpoint: PASS ✅ (read-only, no orders placed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"))
