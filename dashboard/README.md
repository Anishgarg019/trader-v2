# Cloud dashboard

A **Streamlit** app (hosted free on **Streamlit Community Cloud**) that shows the agent's
paper-trading performance in near-real-time: equity & drawdown graphs, open positions, the
**trade log with the logic behind each call** (the justification), win-rate by strategy,
alerts, and the universe. **Read-only** — it cannot trade and never holds Kite credentials.

## Data flow
```
Windows agent (run_loop.py)  --POST performance-->  Postgres (Supabase)  <--read--  Streamlit app
```
The agent publishes after each loop pass (use `--watch` for near-real-time during market
hours). Only performance data crosses the wire — no API keys, no order ability.

## Preview locally (SQLite, no cloud)
```bash
python -m dashboard.seed_demo                              # writes ./dashboard_data.sqlite
DASHBOARD_DB_URL=dashboard_data.sqlite streamlit run dashboard/app.py
```

## Deploy (one-time)
1. **Supabase**: create a free project → SQL editor → run `dashboard/schema.sql` (or let the
   app auto-create on first connect) → copy the **connection string** (`postgresql://...`).
2. **Agent (Windows `.env`)**: set `DASHBOARD_DB_URL=postgresql://...`. The agent now
   publishes on every run (`run_loop.py`, and continuously with `--watch`).
3. **Streamlit Community Cloud**: connect your GitHub repo → New app → main file
   `dashboard/app.py`, requirements `dashboard/requirements.txt`. In the app's **Secrets**:
   ```toml
   DASHBOARD_DB_URL = "postgresql://..."
   DASHBOARD_PASSWORD = "choose-a-view-password"
   ```
4. Open the app URL, enter the password, watch it update.

## Notes
- Auto-refresh interval is a slider in the sidebar (default 30s).
- `dashboard_data.sqlite` and `.streamlit/secrets.toml` are git-ignored.
- Publishing is best-effort in the agent: a DB outage logs a warning and never affects trading.
