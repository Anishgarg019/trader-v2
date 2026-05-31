"""Publish performance data from the agent (Windows) to the dashboard store (cloud Postgres).

Reads the vault (source of truth) + the paper broker + the loop result and mirrors them into
the Store. Best-effort: callers wrap this so a dashboard/DB hiccup never affects trading.
Publishes ONLY performance data — no credentials, no order capability.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agent.trading_day import IST
from vault.writer import VaultWriter
from dashboard.store import Store


def _now() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _split_symbol(sym: str) -> tuple[str, str]:
    return tuple(sym.split(":", 1)) if ":" in sym else ("NSE", sym)  # type: ignore[return-value]


def _justification(body: str) -> str:
    """Pull the 'why' out of a trade note (text under ## Justification, before ## Review)."""
    if "## Justification" not in body:
        return ""
    after = body.split("## Justification", 1)[1]
    return after.split("## Review", 1)[0].strip()


class Publisher:
    def __init__(self, store: Store, vault: VaultWriter):
        self.store = store
        self.vault = vault

    # ---- individual syncs ----------------------------------------------------
    def sync_equity_and_daily(self, result, broker, *, ts: str | None = None) -> None:
        gov = result.governor
        daily_dd = gov.daily_drawdown_pct if gov else 0.0
        total_dd = gov.total_drawdown_pct if gov else 0.0
        self.store.record_equity(ts=ts or _now(), equity=float(result.equity),
                                 cash=float(broker.cash), daily_dd_pct=daily_dd,
                                 total_dd_pct=total_dd)
        st = result.state
        self.store.upsert_daily({
            "date": result.date,
            "day_open_equity": (st.day_open_equity if st else None),
            "day_close_equity": float(result.equity),
            "drawdown_day_pct": daily_dd, "drawdown_total_pct": total_dd,
            "halted": 1 if (gov and (gov.halt_new_entries or gov.full_stop)) else 0,
            "trades_today": len([a for a in result.actions if a.get("status") == "placed"]),
        })

    def sync_positions(self, broker, price_fn: Callable[[str], float] | None) -> None:
        rows = []
        for p in broker.get_positions():
            sym = f"{p['exchange']}:{p['tradingsymbol']}"
            last = float(price_fn(sym)) if price_fn else float(p.get("average_price", 0.0))
            rows.append({
                "symbol": sym, "exchange": p["exchange"], "qty": int(p["quantity"]),
                "avg_price": float(p.get("average_price", 0.0)), "last_price": last,
                "mtm": int(p["quantity"]) * last, "updated_at": _now(),
            })
        self.store.replace_positions(rows)

    def sync_trades(self) -> int:
        folder = self.vault.root / "Trades"
        if not folder.exists():
            return 0
        count = 0
        for path in sorted(folder.glob("*.md")):
            fm, body = self.vault.read_note(f"Trades/{path.name}")
            sym = fm.get("symbol", "")
            exch, _ = _split_symbol(sym)
            tag = str(fm.get("order_tag", ""))
            strat = tag.split("SYS-", 1)[1] if tag.startswith("SYS-") else path.stem.split("-")[-1]
            self.store.upsert_trade({
                "order_id": path.stem, "date": str(fm.get("date", "")), "symbol": sym,
                "exchange": exch, "strategy_id": strat, "direction": fm.get("direction"),
                "qty": fm.get("quantity"), "entry_price": fm.get("entry_price"),
                "stop_price": fm.get("stop_price"), "exit_price": fm.get("exit_price"),
                "status": fm.get("status"), "outcome": fm.get("outcome"),
                "pnl_rupees": fm.get("pnl_rupees"), "risk_rupees": fm.get("risk_rupees"),
                "justification": _justification(body), "updated_at": _now(),
            })
            count += 1
        return count

    def sync_alerts(self) -> int:
        folder = self.vault.root / "System" / "alerts"
        if not folder.exists():
            return 0
        count = 0
        for path in sorted(folder.glob("*.md")):
            fm, body = self.vault.read_note(f"System/alerts/{path.name}")
            self.store.record_alert({
                "uid": path.stem, "date": str(fm.get("date", "")),
                "kind": fm.get("kind", "alert"), "detail": body.strip()[:2000], "ts": _now(),
            })
            count += 1
        return count

    def sync_universe(self) -> None:
        rel = "Universe/current-universe.md"
        if not self.vault.exists(rel):
            return
        fm, _ = self.vault.read_note(rel)
        rows = []
        for name in fm.get("names", []) or []:
            exch, sym = _split_symbol(name)
            rows.append({"symbol": name, "exchange": exch, "sector": None,
                         "avg_traded_value": None, "atr_pct": None,
                         "as_of": str(fm.get("date", ""))})
        self.store.replace_universe(rows)

    # ---- one call from the loop ----------------------------------------------
    def publish(self, result, broker, price_fn: Callable[[str], float] | None = None) -> dict:
        """Publish everything for the current state. Returns a small summary."""
        self.sync_equity_and_daily(result, broker)
        self.sync_positions(broker, price_fn)
        n_trades = self.sync_trades()
        n_alerts = self.sync_alerts()
        self.sync_universe()
        return {"trades": n_trades, "alerts": n_alerts}
