"""Seed a local SQLite store with demo data so the dashboard can be previewed offline.

    python dashboard/seed_demo.py            # writes ./dashboard_data.sqlite
    DASHBOARD_DB_URL=./dashboard_data.sqlite streamlit run dashboard/app.py
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from dashboard.store import open_store


def main(path: str = "dashboard_data.sqlite") -> None:
    store = open_store(path)
    base = datetime(2026, 5, 29, 9, 15)
    equity = 100000.0
    hwm = 100000.0
    for i in range(60):
        ts = base + timedelta(minutes=5 * i)
        equity += 200 * math.sin(i / 6) + (i - 20) * 8
        hwm = max(hwm, equity)
        store.record_equity(ts=ts.isoformat(timespec="seconds"), equity=round(equity, 2),
                            cash=round(equity * 0.4, 2),
                            daily_dd_pct=round((equity - 100000) / 100000, 4),
                            total_dd_pct=round((equity - hwm) / hwm, 4))

    store.upsert_trade({
        "order_id": "2026-05-29-NSE_RELIANCE-s001", "date": "2026-05-29",
        "symbol": "NSE:RELIANCE", "exchange": "NSE", "strategy_id": "s001",
        "direction": "long", "qty": 6, "entry_price": 1402.5, "stop_price": 1370.0,
        "exit_price": 1455.0, "status": "closed", "outcome": "win", "pnl_rupees": 315.0,
        "risk_rupees": 195.0,
        "justification": ("- Ensemble: RSI(14)=28 crossed up from oversold AND close > SMA(200)\n"
                          "- Confirmation: ADX(14)=27 (trend present); volume > 20d avg\n"
                          "- Risk: R=₹195, stop = 2×ATR = ₹65/sh, qty per §4 = 6"),
        "updated_at": base.isoformat(),
    })
    store.upsert_trade({
        "order_id": "2026-05-29-NSE_INFY-s001", "date": "2026-05-29", "symbol": "NSE:INFY",
        "exchange": "NSE", "strategy_id": "s001", "direction": "long", "qty": 13,
        "entry_price": 1500.0, "stop_price": 1470.0, "exit_price": None, "status": "open",
        "outcome": None, "pnl_rupees": None, "risk_rupees": 390.0,
        "justification": "- Ensemble: MACD bullish cross + price > SMA(50); ADX rising",
        "updated_at": base.isoformat(),
    })
    store.replace_positions([{"symbol": "NSE:INFY", "exchange": "NSE", "qty": 13,
                              "avg_price": 1500.0, "last_price": 1512.0, "mtm": 19656.0,
                              "updated_at": base.isoformat()}])
    store.upsert_daily({"date": "2026-05-29", "day_open_equity": 100000.0,
                        "day_close_equity": round(equity, 2), "drawdown_day_pct": 0.0,
                        "drawdown_total_pct": round((equity - hwm) / hwm, 4),
                        "halted": 0, "trades_today": 2})
    store.record_alert({"uid": "2026-05-29-data", "date": "2026-05-29", "kind": "data-anomaly",
                        "detail": "NSE:XYZ printed a frozen timestamp at 11:05; excluded.",
                        "ts": base.isoformat()})
    store.replace_universe([
        {"symbol": "NSE:RELIANCE", "exchange": "NSE", "sector": "ENERGY",
         "avg_traded_value": 9.2e9, "atr_pct": 0.021, "as_of": "2026-05-29"},
        {"symbol": "NSE:INFY", "exchange": "NSE", "sector": "IT",
         "avg_traded_value": 5.1e9, "atr_pct": 0.028, "as_of": "2026-05-29"},
        {"symbol": "NSE:HDFCBANK", "exchange": "NSE", "sector": "BANK",
         "avg_traded_value": 7.4e9, "atr_pct": 0.018, "as_of": "2026-05-29"},
    ])
    print(f"seeded demo data → {path}")


if __name__ == "__main__":
    main()
