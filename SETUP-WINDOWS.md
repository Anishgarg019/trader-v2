# Windows setup guide

Run the systematic **paper-trading** agent on Windows (where your Obsidian vault lives).
Reminder: **Zerodha has no API sandbox** — Kite is used for **read-only data only**; every
order is simulated locally and never sent to a broker. Nothing here can place a real trade.

> I (Claude) will walk you through these steps interactively after you pull the repo. This
> file is the reference.

## 1. Prerequisites
- **Python 3.11+** — install from python.org, tick **"Add python.exe to PATH"**.
  Verify in PowerShell:
  ```powershell
  py --version
  ```
- A **Zerodha Kite Connect** app (api_key + api_secret) with the **historical data**
  add-on, from https://developers.kite.trade/. 2FA TOTP must be enabled on the account.

## 2. Get the code
```powershell
git clone <your-repo-url> "Trader V2"
cd "Trader V2"
```

## 3. Virtual environment + dependencies
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1        # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Configure `.env`
```powershell
copy .env.example .env
notepad .env
```
Fill in:
- `MODE=paper`  (must stay `paper` — the safety guard refuses anything else)
- `KITE_API_KEY=...`
- `KITE_API_SECRET=...`
- `KITE_ACCESS_TOKEN=`  (leave blank — the login step fills it)
- `VAULT_PATH=C:\Users\<you>\Documents\TradingVault`  ← your real Obsidian vault folder

Copy the spec into the vault root so the agent loads it at startup:
```powershell
copy "00 - Trading Agent Spec.md" "%VAULT_PATH%\"
```

## 5. Daily Kite login (mints the access token)
Kite access tokens expire ~6 AM IST, so this runs **once each trading day**:
```powershell
python scripts\kite_login.py
```
Open the printed URL, log in, authorize; copy the `request_token` from the redirect URL and
paste it back. It writes `KITE_ACCESS_TOKEN` into `.env` and `.kite_token.json`.

## 6. Verify (read-only, no orders)
```powershell
python scripts\verify_phase1.py        # resolves a symbol, pulls candles, fetches a quote
python -m pytest                       # full test suite should be all green
```

## 7. Run the loop
```powershell
python scripts\run_loop.py             # runs the clock-appropriate block for now (IST)
python scripts\run_loop.py --day       # walk every block (simulation/catch-up)
```
It writes daily/trade/alert notes into your Obsidian vault and persists `.loop_state.json`.
With no strategy deployed yet it runs the gate/governor/daily-note/research skeleton and
places **no** trades (safe by design).

## 8. Schedule it (Task Scheduler) — ~08:15 IST, T-60m before the 09:15 open
1. Open **Task Scheduler → Create Task**.
2. **Triggers → New → Daily**, set the time to your local equivalent of **08:15 IST**
   (adjust for your timezone; the agent itself uses IST internally).
3. **Actions → New → Start a program**:
   - Program/script: `C:\path\to\Trader V2\.venv\Scripts\python.exe`
   - Arguments: `scripts\run_loop.py`
   - Start in: `C:\path\to\Trader V2`
4. Because the Kite token expires daily, also schedule (or run manually each morning)
   `scripts\kite_login.py` before the loop — the interactive login can't be fully automated
   without storing credentials/TOTP.

## 9. Cloud dashboard (optional but you wanted it)
A Streamlit app on Streamlit Community Cloud shows performance + the logic behind each trade,
fed by a Postgres (Supabase) DB the agent pushes to. Full steps in
[`dashboard/README.md`](dashboard/README.md). Short version:
1. Create a free **Supabase** project; run `dashboard/schema.sql`; copy the connection string.
2. In `.env` set `DASHBOARD_DB_URL=postgresql://...` — the agent then publishes each run
   (`python scripts\run_loop.py`), or continuously with `python scripts\run_loop.py --watch`.
3. Deploy `dashboard/app.py` on **Streamlit Community Cloud** (requirements:
   `dashboard/requirements.txt`); set `DASHBOARD_DB_URL` and `DASHBOARD_PASSWORD` in its Secrets.

Preview locally first: `python -m dashboard.seed_demo` then
`DASHBOARD_DB_URL=dashboard_data.sqlite streamlit run dashboard/app.py`.

**Safety**: the dashboard receives performance data only — never Kite keys, never order
ability. Publishing is best-effort; a DB outage never affects trading.

## Notes & honest limitations
- **Paper-book persistence**: `run_loop.py` persists `LoopState` (day-open equity, high-water
  mark) but the in-memory paper book resets each process. For continuous multi-day paper
  trading, keep one long-lived process or add paper-book persistence (future work).
- **Backtest ≠ paper ≠ live**: backtest models friction; paper fills are approximate; real
  fills would differ again (spec §1.4). Report edge with its caveats.
- Token, `.env`, `.venv`, and `vault-dev/` are git-ignored — never commit secrets.
