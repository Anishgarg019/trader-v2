"""Streamlit dashboard (Streamlit Community Cloud).

Reads the cloud Postgres store and shows: equity & drawdown graphs, current positions, the
trade log WITH the logic behind each call (the justification), win-rate by strategy, alerts,
and the universe. Password-gated. Read-only — it cannot trade.

Secrets (Streamlit Cloud → app settings, or local env / .streamlit/secrets.toml):
  DASHBOARD_DB_URL   postgres://...  (Supabase connection string)  [or a sqlite path locally]
  DASHBOARD_PASSWORD shared view password

Run locally:  streamlit run dashboard/app.py
"""
from __future__ import annotations

import os

import pandas as pd

# ---- secrets / config (pure-ish) ----------------------------------------------
def get_secret(key: str, default: str | None = None) -> str | None:
    try:
        import streamlit as st
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, default)


# ---- pure data shaping (unit-tested) ------------------------------------------
def equity_dataframe(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["equity"])
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    return df.set_index("ts")[["equity", "cash"]]


def drawdown_dataframe(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["daily_dd_%", "total_dd_%"])
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    out = pd.DataFrame({
        "daily_dd_%": df["daily_dd_pct"] * 100,
        "total_dd_%": df["total_dd_pct"] * 100,
    })
    out.index = df["ts"]
    return out


def kpi_summary(equity_rows: list[dict], positions: list[dict]) -> dict:
    latest = equity_rows[-1] if equity_rows else {}
    return {
        "equity": latest.get("equity"),
        "cash": latest.get("cash"),
        "daily_dd_pct": latest.get("daily_dd_pct"),
        "total_dd_pct": latest.get("total_dd_pct"),
        "open_positions": len(positions),
        "open_mtm": sum(p.get("mtm", 0) or 0 for p in positions),
    }


# ---- UI -----------------------------------------------------------------------
def _check_password(st) -> bool:
    pw = get_secret("DASHBOARD_PASSWORD")
    if not pw:
        return True  # no password configured (e.g. local dev)
    if st.session_state.get("authed"):
        return True
    entered = st.text_input("Password", type="password")
    if entered and entered == pw:
        st.session_state["authed"] = True
        return True
    if entered:
        st.error("Wrong password.")
    return False


def main() -> None:
    import streamlit as st
    from dashboard.store import open_store

    st.set_page_config(page_title="Trading Agent — Paper Dashboard", layout="wide")
    st.title("📈 Systematic Paper-Trading Agent")
    st.caption("Read-only performance view · paper money · NSE/BSE")

    if not _check_password(st):
        st.stop()

    # auto-refresh every 30s (near-real-time during market hours); hide the sidebar entirely
    REFRESH_SECONDS = 30
    st.markdown(
        f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">'
        "<style>[data-testid='stSidebar']{display:none;}</style>",
        unsafe_allow_html=True,
    )

    dsn = get_secret("DASHBOARD_DB_URL")
    store = open_store(dsn)

    eq = store.equity_series()
    positions = store.open_positions()
    kpi = kpi_summary(eq, positions)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Equity", f"₹{kpi['equity']:,.0f}" if kpi["equity"] else "—")
    c2.metric("Cash", f"₹{kpi['cash']:,.0f}" if kpi["cash"] else "—")
    c3.metric("Daily drawdown", f"{kpi['daily_dd_pct']*100:.2f}%" if kpi["daily_dd_pct"] is not None else "—")
    c4.metric("Total drawdown", f"{kpi['total_dd_pct']*100:.2f}%" if kpi["total_dd_pct"] is not None else "—")
    c5.metric("Open positions", kpi["open_positions"])

    st.subheader("Equity curve")
    st.line_chart(equity_dataframe(eq)["equity"] if eq else pd.DataFrame())

    st.subheader("Drawdown (%)")
    st.line_chart(drawdown_dataframe(eq) if eq else pd.DataFrame())

    st.subheader("Open positions")
    st.dataframe(pd.DataFrame(positions) if positions else pd.DataFrame(), use_container_width=True)

    st.subheader("Trades — and the logic behind each")
    trades = store.trades(200)
    if trades:
        for t in trades:
            head = (f"{t['date']} · {t['symbol']} · {t['status']} "
                    f"· {t.get('outcome') or ''} · P&L ₹{t.get('pnl_rupees') or 0:,.0f}")
            with st.expander(head):
                st.write({k: t[k] for k in ("strategy_id", "direction", "qty",
                          "entry_price", "stop_price", "exit_price", "risk_rupees")})
                st.markdown("**Why this trade:**")
                st.markdown(t.get("justification") or "_(no justification recorded)_")
    else:
        st.info("No trades yet.")

    st.subheader("Win rate by strategy")
    wr = store.strategy_winrates()
    if wr:
        df = pd.DataFrame(wr)
        df["win_rate"] = (df["win_rate"] * 100).round(1)
        st.dataframe(df[["strategy_id", "trades", "wins", "win_rate", "pnl"]],
                     use_container_width=True)

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Universe")
        st.dataframe(pd.DataFrame(store.universe()), use_container_width=True)
    with col_b:
        st.subheader("Alerts")
        st.dataframe(pd.DataFrame(store.alerts(50)), use_container_width=True)


if __name__ == "__main__":
    main()
