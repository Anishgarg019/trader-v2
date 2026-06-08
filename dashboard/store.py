"""Dashboard data store — one Store over a DB-API 2.0 connection.

Works on both SQLite (local dev/tests) and Postgres/Supabase (prod) by swapping the
placeholder style; the SQL (incl. `ON CONFLICT ... DO UPDATE SET x = excluded.x`) is valid
on both, so the SQLite tests exercise the exact prod statements.

Contains ONLY performance data (equity, trades + their justification, positions, alerts,
daily summary, universe). NEVER credentials or anything that can place an order.
"""
from __future__ import annotations

from typing import Any

DDL = [
    """CREATE TABLE IF NOT EXISTS equity_snapshots(
        ts TEXT PRIMARY KEY, equity REAL, cash REAL,
        daily_dd_pct REAL, total_dd_pct REAL)""",
    """CREATE TABLE IF NOT EXISTS trades(
        order_id TEXT PRIMARY KEY, date TEXT, symbol TEXT, exchange TEXT, strategy_id TEXT,
        direction TEXT, qty INTEGER, entry_price REAL, stop_price REAL, exit_price REAL,
        status TEXT, outcome TEXT, pnl_rupees REAL, risk_rupees REAL,
        justification TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS positions(
        symbol TEXT PRIMARY KEY, exchange TEXT, qty INTEGER, avg_price REAL,
        last_price REAL, mtm REAL, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS alerts(
        uid TEXT PRIMARY KEY, date TEXT, kind TEXT, detail TEXT, ts TEXT)""",
    """CREATE TABLE IF NOT EXISTS daily(
        date TEXT PRIMARY KEY, day_open_equity REAL, day_close_equity REAL,
        drawdown_day_pct REAL, drawdown_total_pct REAL, halted INTEGER, trades_today INTEGER)""",
    """CREATE TABLE IF NOT EXISTS universe(
        symbol TEXT PRIMARY KEY, exchange TEXT, sector TEXT,
        avg_traded_value REAL, atr_pct REAL, as_of TEXT)""",
    """CREATE TABLE IF NOT EXISTS strategies(
        id TEXT PRIMARY KEY, name TEXT, status TEXT, families TEXT,
        deployed_symbols TEXT, created TEXT,
        oos_return REAL, oos_sharpe REAL, win_rate REAL, trades INTEGER,
        symbols_deployed INTEGER, symbols_tested INTEGER, n_params INTEGER,
        thesis TEXT, reasoning TEXT, detail TEXT, in_graveyard INTEGER, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS research_runs(
        uid TEXT PRIMARY KEY, date TEXT, cadence TEXT, proposed INTEGER, valid INTEGER,
        deployed_n INTEGER, rejected INTEGER, coverage_before INTEGER, coverage_after INTEGER,
        summary TEXT, updated_at TEXT)""",
]


class Store:
    def __init__(self, conn, placeholder: str = "?"):
        self.conn = conn
        self.ph = placeholder
        self.init_schema()

    def init_schema(self) -> None:
        cur = self.conn.cursor()
        for stmt in DDL:
            cur.execute(stmt)
        self.conn.commit()

    # ---- writes --------------------------------------------------------------
    def _upsert(self, table: str, pk: str, row: dict[str, Any]) -> None:
        cols = list(row)
        placeholders = ", ".join(self.ph for _ in cols)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != pk)
        clause = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
               f"ON CONFLICT({pk}) {clause}")
        cur = self.conn.cursor()
        cur.execute(sql, [row[c] for c in cols])
        self.conn.commit()

    def record_equity(self, *, ts: str, equity: float, cash: float,
                      daily_dd_pct: float, total_dd_pct: float) -> None:
        self._upsert("equity_snapshots", "ts", {
            "ts": ts, "equity": equity, "cash": cash,
            "daily_dd_pct": daily_dd_pct, "total_dd_pct": total_dd_pct})

    def upsert_trade(self, trade: dict[str, Any]) -> None:
        self._upsert("trades", "order_id", trade)

    def upsert_daily(self, daily: dict[str, Any]) -> None:
        self._upsert("daily", "date", daily)

    def record_alert(self, alert: dict[str, Any]) -> None:
        self._upsert("alerts", "uid", alert)

    def replace_positions(self, positions: list[dict[str, Any]]) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM positions")
        self.conn.commit()
        for p in positions:
            self._upsert("positions", "symbol", p)

    def replace_universe(self, names: list[dict[str, Any]]) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM universe")
        self.conn.commit()
        for n in names:
            self._upsert("universe", "symbol", n)

    def replace_strategies(self, rows: list[dict[str, Any]]) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM strategies")
        self.conn.commit()
        for r in rows:
            self._upsert("strategies", "id", r)

    def replace_research_runs(self, rows: list[dict[str, Any]]) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM research_runs")
        self.conn.commit()
        for r in rows:
            self._upsert("research_runs", "uid", r)

    # ---- reads (for the app) -------------------------------------------------
    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def equity_series(self) -> list[dict]:
        return self._rows("SELECT * FROM equity_snapshots ORDER BY ts")

    def trades(self, limit: int = 200) -> list[dict]:
        return self._rows(f"SELECT * FROM trades ORDER BY date DESC, order_id DESC LIMIT {int(limit)}")

    def open_positions(self) -> list[dict]:
        return self._rows("SELECT * FROM positions WHERE qty != 0 ORDER BY symbol")

    def alerts(self, limit: int = 50) -> list[dict]:
        return self._rows(f"SELECT * FROM alerts ORDER BY ts DESC LIMIT {int(limit)}")

    def daily(self, limit: int = 90) -> list[dict]:
        return self._rows(f"SELECT * FROM daily ORDER BY date DESC LIMIT {int(limit)}")

    def universe(self) -> list[dict]:
        return self._rows("SELECT * FROM universe ORDER BY avg_traded_value DESC")

    def strategies(self) -> list[dict]:
        return self._rows("SELECT * FROM strategies ORDER BY id")

    def research_runs(self, limit: int = 60) -> list[dict]:
        return self._rows(f"SELECT * FROM research_runs ORDER BY date DESC, uid DESC "
                          f"LIMIT {int(limit)}")

    def strategy_winrates(self) -> list[dict]:
        rows = self._rows("SELECT strategy_id, outcome, pnl_rupees FROM trades "
                          "WHERE status = 'closed'")
        agg: dict[str, dict] = {}
        for r in rows:
            s = r["strategy_id"] or "unknown"
            a = agg.setdefault(s, {"strategy_id": s, "trades": 0, "wins": 0, "pnl": 0.0})
            a["trades"] += 1
            a["wins"] += 1 if (r["pnl_rupees"] or 0) > 0 else 0
            a["pnl"] += r["pnl_rupees"] or 0.0
        for a in agg.values():
            a["win_rate"] = a["wins"] / a["trades"] if a["trades"] else 0.0
        return sorted(agg.values(), key=lambda x: x["strategy_id"])

    def close(self) -> None:
        self.conn.close()


def open_store(dsn: str | None = None):
    """Open a Store. Postgres if `dsn` looks like a Postgres URL, else SQLite file path
    (default ./dashboard_data.sqlite)."""
    if dsn and dsn.startswith(("postgres://", "postgresql://")):
        import psycopg
        from psycopg.rows import tuple_row
        # prepare_threshold=None disables psycopg3 server-side prepared statements, which is
        # REQUIRED for the Supabase transaction-mode pooler (port 6543) — it rotates the
        # backend per transaction, so a prepared statement from one txn is gone in the next.
        # Harmless on the session pooler (5432) / direct connections too.
        # connect_timeout + statement_timeout are LOAD-BEARING: without them a slow/dropped
        # pooler backend makes connect (or a query on a dead socket) block forever, hanging
        # the dashboard with no error. Bounding both turns a hang into a catchable exception.
        conn = psycopg.connect(dsn, row_factory=tuple_row, autocommit=False,
                               prepare_threshold=None, connect_timeout=10,
                               options="-c statement_timeout=15000")  # ms
        return Store(conn, placeholder="%s")
    import sqlite3
    path = dsn or "dashboard_data.sqlite"
    conn = sqlite3.connect(path, check_same_thread=False)
    return Store(conn, placeholder="?")
