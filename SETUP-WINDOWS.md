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
Kite access tokens expire ~6 AM IST, so a fresh one is needed **once each trading day**.

**Recommended — fully automatic (no daily action):** set `KITE_USER_ID`, `KITE_PASSWORD`,
`KITE_TOTP_SECRET` in `.env` (see `.env.example`). The scheduled `scripts/auto_login.py`
(07:00 IST) then mints the token headlessly via TOTP every morning — you never log in by
hand. `KITE_TOTP_SECRET` is the **base32 seed** behind your Zerodha 2FA (the "enter this key
manually" string when setting up the authenticator app), not a 6-digit code. Test it once:
```powershell
python scripts\auto_login.py        # should print "auto-login OK — token refreshed ..."
```
A 07:30 watchdog (`scripts/notify_login.py`) re-checks the token and only pings your phone
(ntfy) if it's still invalid — i.e. auto-login failed — so a healthy morning is silent.

**Fallback — manual:** if you don't set those creds, log in by hand each trading day:
```powershell
python scripts\kite_login.py
```
Open the printed URL, log in, authorize; paste the `request_token`/redirect URL back. Either
path writes `KITE_ACCESS_TOKEN` into `.env` and `.kite_token.json`. (The `--watch` loop also
hot-reloads the token mid-session, so a late login is picked up within ~60s without a restart.)

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
It writes daily/trade/alert notes into your Obsidian vault and persists `.loop_state.json`
**and `.paper_book.json`** (positions/cash/GTT stops — restored at the start of every pass,
so multi-day paper state survives restarts). Deployed strategies are read from the vault
**registry** (Phase 11): every `Strategies/*.md` note with `status: forward-test` and a
`spec:` block is compiled and trades only its gate-proven `deployed_symbols`. The autonomous
researcher (§8) writes those notes; the loop just executes the compiled DSL. s001 is the seed
forward-test.

## 8. Schedule it (Task Scheduler) — all IST (this box's clock = IST)

Five scheduled tasks make the service **fully hands-off** once `KITE_USER_ID`/`KITE_PASSWORD`/
`KITE_TOTP_SECRET` are set (§5) — including the daily login:

| Task | When (IST) | Command |
|------|-----------|---------|
| `Trader - auto login` | 07:00 daily | `python scripts\auto_login.py` (mints today's token via TOTP — no human action) |
| `Trader - login reminder` | 07:30 daily | `python scripts\notify_login.py` (WATCHDOG: pings only if the token is still invalid) |
| `Trader - daily loop` | 08:15 daily | `python scripts\run_loop.py --watch 60` (T-60m; watches through the session) |
| `Trader - researcher daily` | 16:15 Mon–Sat | `python scripts\researcher.py` (daily-light: decay/coverage + small top-up) |
| `Trader - researcher weekly` | 16:15 Sun | `python scripts\researcher.py --weekly` (full proposals + improvement pass) |

⚠️ **Create tasks with a SPACE between the exe and the script** (a malformed `python.exeC:\...py`
with no space fails silently with exit code 2 — the bug that caused missed logins on
2026-06-01/02). All tasks were (re)created cleanly like this:
```powershell
$py = "C:\Users\Anish\Documents\trader-v2\.venv\Scripts\python.exe"
schtasks /create /tn "Trader - auto login"      /tr "`"$py`" `"...\scripts\auto_login.py`""           /sc DAILY  /st 07:00 /f
schtasks /create /tn "Trader - login reminder"  /tr "`"$py`" `"...\scripts\notify_login.py`""          /sc DAILY  /st 07:30 /f
schtasks /create /tn "Trader - daily loop"      /tr "`"$py`" `"...\scripts\run_loop.py`" --watch 60"   /sc DAILY  /st 08:15 /f
schtasks /create /tn "Trader - researcher daily"  /tr "`"$py`" `"...\scripts\researcher.py`""          /sc WEEKLY /d MON,TUE,WED,THU,FRI,SAT /st 16:15 /f
schtasks /create /tn "Trader - researcher weekly" /tr "`"$py`" `"...\scripts\researcher.py`" --weekly" /sc WEEKLY /d SUN                  /st 16:15 /f
```
(Daily-light runs Mon–Sat and weekly-deep runs Sun, so they never double-fire on Sunday.)

**Resilience built in:**
- *Retry/backoff* — Kite quote/history calls go through `agent/retry.py` (exponential backoff).
- *ntfy on failure* — `run_loop.py` and `researcher.py` push a high-priority ntfy alert on any
  unhandled exception (set `NTFY_TOPIC` in `.env`). The researcher also pushes a run summary.

**"Run whether user is logged on or not" (recommended, needs your password):** the tasks above
run **only while you're logged on** (the default, matching the existing tasks). To make them run
on the locked-screen/headless box, open each task in **Task Scheduler → Properties → General →
"Run whether user is logged on or not"** and click OK (Windows prompts for your account
password — which can't be supplied non-interactively, so this is a one-time manual step per
task). Do this for all four if you want the box to trade without an interactive session.

**Daily Kite login (the one unavoidable manual step):** the token expires ~6 AM IST and the
interactive login (with TOTP) can't be fully automated without storing credentials. The 07:30
reminder pings your phone; tap it and run `python scripts\kite_login.py`. Then the day's loop +
researcher work.

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
- **Paper-book persistence (resolved, Phase 11)**: `run_loop.py` persists both `LoopState`
  (day-open equity, high-water mark) and the full paper book (`.paper_book.json`:
  positions/cash/GTT stops/orders/trades), reloaded at the start of every pass — multi-day
  paper state survives process restarts.
- **Backtest ≠ paper ≠ live**: backtest models friction; paper fills are approximate; real
  fills would differ again (spec §1.4). Report edge with its caveats.
- Token, `.env`, `.venv`, and `vault-dev/` are git-ignored — never commit secrets.
