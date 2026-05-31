"""Streamlit dashboard — dark / minimal / modern, indigo-on-charcoal.

Layout: a status band (equity · today · return · drawdown · safety) over tabs
(Performance · Positions · Trades · Alerts), with the equity curve as the hero.
Read-only performance view — it cannot trade and holds no Kite credentials.

Secrets (Streamlit Cloud settings, or local env / .streamlit/secrets.toml):
  DASHBOARD_DB_URL    postgres://...  (Supabase)  [or a sqlite path locally]
  DASHBOARD_PASSWORD  shared view password

Run locally:  DASHBOARD_DB_URL=dashboard_data.sqlite streamlit run dashboard/app.py
"""
from __future__ import annotations

import os

import pandas as pd

INDIGO = "#6366F1"
GREEN = "#22C55E"
RED = "#EF4444"
AMBER = "#F59E0B"
STARTING_CAPITAL = 100000.0
REFRESH_SECONDS = 30

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown { font-family:'Inter',sans-serif; }
[data-testid="stSidebar"]{display:none;}
[data-testid="stHeader"]{background:transparent;}
.block-container{padding-top:1.1rem; padding-bottom:2rem; max-width:1180px;}
#MainMenu, footer {visibility:hidden;}

.app-title{font-size:1.05rem;font-weight:600;color:#E6EDF3;margin:0 0 2px 0;letter-spacing:.01em;}
.app-sub{font-size:.78rem;color:#8B949E;margin:0 0 14px 0;}

.band{display:flex;gap:12px;flex-wrap:wrap;margin:0 0 6px 0;}
.card{flex:1;min-width:130px;background:#161B22;border:1px solid #232A33;border-radius:14px;padding:13px 16px;}
.card .label{font-size:.68rem;color:#8B949E;text-transform:uppercase;letter-spacing:.06em;margin-bottom:5px;}
.card .value{font-size:1.4rem;font-weight:600;color:#E6EDF3;line-height:1.1;}
.card .sub{font-size:.72rem;color:#8B949E;margin-top:2px;}
.pos{color:#22C55E;} .neg{color:#EF4444;}
.badge{display:inline-flex;align-items:center;gap:7px;font-weight:600;padding:6px 12px;border-radius:999px;font-size:.82rem;}
.badge.ok{background:rgba(34,197,94,.12);color:#22C55E;}
.badge.warn{background:rgba(245,158,11,.14);color:#F59E0B;}
.badge.stop{background:rgba(239,68,68,.14);color:#EF4444;}
.dot{width:8px;height:8px;border-radius:50%;background:currentColor;display:inline-block;}

/* segmented-control tabs */
.stTabs [data-baseweb="tab-list"]{
  gap:6px; background:#12161C; border:1px solid #232A33; border-radius:12px;
  padding:5px; width:fit-content; max-width:100%; flex-wrap:wrap; margin:6px 0 14px 0;
}
.stTabs [data-baseweb="tab"]{
  height:auto; padding:9px 20px; border-radius:9px; color:#8B949E; font-weight:500;
  background:transparent; transition:background .12s ease,color .12s ease;
}
.stTabs [data-baseweb="tab"]:hover{color:#E6EDF3; background:#1B222B;}
.stTabs [aria-selected="true"]{background:#6366F1 !important; color:#FFFFFF !important;}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"]{display:none;}

.stDataFrame{border:1px solid #232A33;border-radius:12px;}
div[data-testid="stExpander"]{border:1px solid #232A33;border-radius:12px;background:#12161C;margin-bottom:6px;}
.empty{color:#8B949E;text-align:center;padding:48px 0;font-size:.95rem;}
</style>
"""


# ---- secrets ------------------------------------------------------------------
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
        return pd.DataFrame(columns=["equity", "cash"])
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    return df.set_index("ts")[["equity", "cash"]]


def drawdown_dataframe(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["daily_dd_%", "total_dd_%"])
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    out = pd.DataFrame({"daily_dd_%": df["daily_dd_pct"] * 100,
                        "total_dd_%": df["total_dd_pct"] * 100})
    out.index = df["ts"]
    return out


def kpi_summary(equity_rows: list[dict], positions: list[dict]) -> dict:
    latest = equity_rows[-1] if equity_rows else {}
    return {"equity": latest.get("equity"), "cash": latest.get("cash"),
            "daily_dd_pct": latest.get("daily_dd_pct"),
            "total_dd_pct": latest.get("total_dd_pct"),
            "open_positions": len(positions),
            "open_mtm": sum(p.get("mtm", 0) or 0 for p in positions)}


def band_metrics(equity_rows: list[dict], daily_rows: list[dict]) -> dict:
    """Compute the top-band numbers + a safety status. Safe on empty data."""
    last = equity_rows[-1] if equity_rows else {}
    equity = last.get("equity")
    daily_dd = last.get("daily_dd_pct")
    total_dd = last.get("total_dd_pct")
    total_return = None
    if equity_rows:
        first = equity_rows[0].get("equity") or STARTING_CAPITAL
        total_return = (equity / first - 1) if first else None
    today_inr = None
    if daily_rows:
        d = daily_rows[0]
        if d.get("day_open_equity") and d.get("day_close_equity") is not None:
            today_inr = d["day_close_equity"] - d["day_open_equity"]

    if total_dd is not None and total_dd <= -0.15:
        status, cls = "Full stop", "stop"
    elif daily_dd is not None and daily_dd <= -0.05:
        status, cls = "Halted today", "warn"
    elif equity is None:
        status, cls = "Awaiting first run", "warn"
    else:
        status, cls = "Within limits", "ok"
    return {"equity": equity, "daily_dd": daily_dd, "total_dd": total_dd,
            "total_return": total_return, "today_inr": today_inr,
            "status": status, "status_cls": cls}


def open_risk(trades: list[dict]) -> float:
    """Sum of qty×(entry−stop) across OPEN long trades — current capital at risk."""
    risk = 0.0
    for t in trades:
        if t.get("status") == "open" and t.get("entry_price") and t.get("stop_price"):
            risk += (t["entry_price"] - t["stop_price"]) * (t.get("qty") or 0)
    return risk


# ---- charts (plotly) ----------------------------------------------------------
def _style(fig, height=320):
    fig.update_layout(template="plotly_dark", height=height,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      margin=dict(l=0, r=6, t=12, b=0),
                      font=dict(family="Inter", color="#E6EDF3", size=12),
                      showlegend=False)
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(gridcolor="#232A33", zeroline=False)
    return fig


def equity_fig(rows: list[dict]):
    import plotly.graph_objects as go
    df = equity_dataframe(rows)
    fig = go.Figure(go.Scatter(x=df.index, y=df["equity"], mode="lines",
                               line=dict(color=INDIGO, width=2.5),
                               fill="tozeroy", fillcolor="rgba(99,102,241,0.12)",
                               hovertemplate="₹%{y:,.0f}<extra></extra>"))
    if len(df):
        lo, hi = float(df["equity"].min()), float(df["equity"].max())
        pad = (hi - lo) * 0.12 or max(hi * 0.002, 1)
        fig.update_yaxes(range=[lo - pad, hi + pad], tickprefix="₹", tickformat=",.0f")
    return _style(fig, 340)


def drawdown_fig(rows: list[dict]):
    import plotly.graph_objects as go
    dd = drawdown_dataframe(rows)
    fig = go.Figure(go.Scatter(x=dd.index, y=dd["total_dd_%"], mode="lines",
                               line=dict(color=RED, width=1.8),
                               fill="tozeroy", fillcolor="rgba(239,68,68,0.10)",
                               hovertemplate="%{y:.2f}%<extra></extra>"))
    fig.update_yaxes(ticksuffix="%")
    return _style(fig, 220)


def winrate_fig(wr: list[dict]):
    import plotly.graph_objects as go
    wr = sorted(wr, key=lambda x: x["win_rate"])
    fig = go.Figure(go.Bar(x=[w["win_rate"] * 100 for w in wr],
                           y=[w["strategy_id"] for w in wr], orientation="h",
                           marker_color=INDIGO,
                           hovertemplate="%{x:.0f}%<extra></extra>"))
    fig.update_xaxes(range=[0, 100], ticksuffix="%")
    return _style(fig, max(160, 60 + 38 * len(wr)))


# ---- UI -----------------------------------------------------------------------
def _check_password(st) -> bool:
    pw = get_secret("DASHBOARD_PASSWORD")
    if not pw:
        return True
    if st.session_state.get("authed"):
        return True
    entered = st.text_input("Password", type="password")
    if entered and entered == pw:
        st.session_state["authed"] = True
        return True
    if entered:
        st.error("Wrong password.")
    return False


def _fmt_pct(v):
    return f"{v*100:+.2f}%" if v is not None else "—"


def render_band(st, m: dict, positions_count: int) -> None:
    def signed(v, money=False):
        if v is None:
            return '<span class="value">—</span>'
        cls = "pos" if v >= 0 else "neg"
        txt = (f"₹{v:,.0f}" if money else f"{v*100:+.2f}%")
        if money and v >= 0:
            txt = f"+{txt}"
        return f'<span class="value {cls}">{txt}</span>'

    eq = f'₹{m["equity"]:,.0f}' if m["equity"] is not None else "—"
    today_sub = (f'<div class="sub">{("+" if (m["today_inr"] or 0)>=0 else "")}₹{m["today_inr"]:,.0f}</div>'
                 if m["today_inr"] is not None else "")
    st.markdown(f"""
    <div class="band">
      <div class="card"><div class="label">Status</div>
        <span class="badge {m['status_cls']}"><span class="dot"></span>{m['status']}</span></div>
      <div class="card"><div class="label">Equity</div><div class="value">{eq}</div></div>
      <div class="card"><div class="label">Today</div>{signed(m['daily_dd'])}{today_sub}</div>
      <div class="card"><div class="label">Return</div>{signed(m['total_return'])}</div>
      <div class="card"><div class="label">Drawdown</div>{signed(m['total_dd'])}</div>
      <div class="card"><div class="label">Open positions</div><div class="value">{positions_count}</div></div>
    </div>
    """, unsafe_allow_html=True)


def main() -> None:
    import streamlit as st
    from dashboard.store import open_store

    st.set_page_config(page_title="Paper-Trading Agent", layout="wide",
                       initial_sidebar_state="collapsed")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">',
                unsafe_allow_html=True)

    if not _check_password(st):
        st.stop()

    st.markdown('<div class="app-title">Systematic Paper-Trading Agent</div>'
                '<div class="app-sub">Read-only · paper money · NSE/BSE · '
                f'auto-refresh {REFRESH_SECONDS}s</div>', unsafe_allow_html=True)

    store = open_store(get_secret("DASHBOARD_DB_URL"))
    eq = store.equity_series()
    positions = store.open_positions()
    trades = store.trades(200)
    m = band_metrics(eq, store.daily(1))

    render_band(st, m, len(positions))

    tab_perf, tab_pos, tab_trades, tab_alerts = st.tabs(
        ["Performance", "Positions", "Trades", "Alerts"])

    with tab_perf:
        if not eq:
            st.markdown('<div class="empty">Waiting for the agent\'s first run — '
                        'the equity curve appears once it starts trading.</div>',
                        unsafe_allow_html=True)
        else:
            st.caption("Equity")
            st.plotly_chart(equity_fig(eq), use_container_width=True,
                            config={"displayModeBar": False})
            c1, c2 = st.columns([1, 1])
            with c1:
                st.caption("Drawdown")
                st.plotly_chart(drawdown_fig(eq), use_container_width=True,
                                config={"displayModeBar": False})
            with c2:
                st.caption("Win rate by strategy")
                wr = store.strategy_winrates()
                if wr:
                    st.plotly_chart(winrate_fig(wr), use_container_width=True,
                                    config={"displayModeBar": False})
                else:
                    st.markdown('<div class="empty">No closed trades yet.</div>',
                                unsafe_allow_html=True)

    with tab_pos:
        risk = open_risk(trades)
        cap = (m["equity"] or STARTING_CAPITAL)
        deployed = sum(p.get("mtm", 0) or 0 for p in positions)
        k1, k2, k3 = st.columns(3)
        k1.metric("Open positions", len(positions))
        k2.metric("Capital deployed", f"{deployed/cap*100:.0f}%" if cap else "—")
        k3.metric("Open risk", f"₹{risk:,.0f}", help="vs 15% cap = "
                  f"₹{0.15*cap:,.0f}")
        st.progress(min(risk / (0.15 * cap), 1.0) if cap else 0.0,
                    text=f"Open risk {risk:,.0f} / {0.15*cap:,.0f} (15% cap)")
        if positions:
            st.dataframe(pd.DataFrame(positions), use_container_width=True, hide_index=True)
        else:
            st.markdown('<div class="empty">No open positions.</div>', unsafe_allow_html=True)

    with tab_trades:
        if not trades:
            st.markdown('<div class="empty">No trades yet.</div>', unsafe_allow_html=True)
        for t in trades:
            pnl = t.get("pnl_rupees")
            tag = ("·" if pnl is None else
                   f'<span style="color:{GREEN if pnl>0 else RED}">₹{pnl:,.0f}</span>')
            with st.expander(f"{t['date']} · {t['symbol']} · {t.get('status','')} "
                             f"· {t.get('outcome') or 'open'}"):
                cols = st.columns(4)
                cols[0].metric("Qty", t.get("qty") or "—")
                cols[1].metric("Entry", f"₹{t['entry_price']:,.1f}" if t.get("entry_price") else "—")
                cols[2].metric("Stop", f"₹{t['stop_price']:,.1f}" if t.get("stop_price") else "—")
                cols[3].metric("P&L", f"₹{pnl:,.0f}" if pnl is not None else "—")
                st.markdown("**Why this trade**")
                st.markdown(t.get("justification") or "_(no justification recorded)_")

    with tab_alerts:
        alerts = store.alerts(50)
        if alerts:
            st.dataframe(pd.DataFrame(alerts)[["date", "kind", "detail"]],
                         use_container_width=True, hide_index=True)
        else:
            st.markdown('<div class="empty">No alerts. All quiet.</div>',
                        unsafe_allow_html=True)
        with st.expander("Universe"):
            uni = store.universe()
            st.dataframe(pd.DataFrame(uni), use_container_width=True, hide_index=True) \
                if uni else st.caption("Universe not yet selected.")


if __name__ == "__main__":
    main()
