"""Select the ~10-name trading universe from a pool of liquid NSE large-caps (spec §2).

Pulls ~3 months of daily data per candidate, scores liquidity (20d avg traded value) and
volatility (ATR%), applies the ≤3-per-sector diversification cap, picks 10, and writes
Universe/current-universe.md.

  python scripts/select_universe.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from agent.config import load_settings
from agent.broker.kite_client import KiteDataClient
from agent.universe import compute_candidate_metrics, select_universe, write_universe_note
from agent.retry import call_with_retries
from agent.logging_setup import get_logger
from vault.writer import VaultWriter

log = get_logger("universe")

# Diversified pool of liquid NSE large-caps by sector (the ≤3/sector cap trims to 10).
POOL: dict[str, list[str]] = {
    "BANK": ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK"],
    "IT": ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM"],
    "ENERGY": ["RELIANCE", "NTPC", "POWERGRID", "ONGC", "BPCL"],
    "FMCG": ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA"],
    "AUTO": ["MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO"],
    "PHARMA": ["SUNPHARMA", "DRREDDY", "CIPLA"],
    "METAL": ["TATASTEEL", "JSWSTEEL", "HINDALCO"],
    "INFRA": ["LT", "BHARTIARTL", "ASIANPAINT"],
}


def main() -> int:
    s = load_settings()
    if not (s.kite_api_key and s.kite_access_token):
        log.error("Missing creds. Run scripts/kite_login.py first.")
        return 1
    kite = KiteDataClient(api_key=s.kite_api_key, access_token=s.kite_access_token)
    vault = VaultWriter(s.vault_path)
    vault.ensure_structure()

    to_d = date.today()
    from_d = to_d - timedelta(days=150)
    candidates: list[dict] = []

    for sector, symbols in POOL.items():
        for sym in symbols:
            try:
                matches = call_with_retries(
                    lambda: kite.search_instruments(sym, filter_on="tradingsymbol",
                                                    exchange="NSE", limit=10))
                row = next((m for m in matches
                            if m["tradingsymbol"].upper() == sym.upper()
                            and m["exchange"] == "NSE"), None)
                if not row:
                    log.warning("could not resolve %s on NSE — skipping", sym)
                    continue
                token = row["instrument_token"]
                candles = call_with_retries(
                    lambda: kite.historical_data(token, str(from_d), str(to_d), "day"))
                if len(candles) < 30:
                    log.warning("%s: only %d candles — skipping", sym, len(candles))
                    continue
                df = pd.DataFrame(candles)
                m = compute_candidate_metrics(df)
                candidates.append({"symbol": sym, "exchange": "NSE", "token": token,
                                   "sector": sector, **m})
                log.info(f"{sym:<12} atv=₹{m['avg_traded_value']:,.0f} "
                         f"atr%={m['atr_pct']*100:.2f}%")
            except Exception as e:  # noqa: BLE001
                log.warning("%s failed: %s", sym, e)

    if not candidates:
        log.error("no candidates resolved — check creds/connectivity.")
        return 1

    sel = select_universe(candidates, size=10, max_per_sector=3,
                          atr_min_pct=0.005, atr_max_pct=0.06)
    write_universe_note(vault, sel, d=str(to_d))

    print("\n=== Selected universe (10) ===")
    for p in sel.picks:
        print(f"  {p['exchange']}:{p['symbol']:<12} {p['sector']:<7} "
              f"atv=₹{p['avg_traded_value']:,.0f}  atr%={p['atr_pct']*100:.2f}%")
    print(f"\nConsidered {sel.considered}; wrote Universe/current-universe.md to your vault.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
