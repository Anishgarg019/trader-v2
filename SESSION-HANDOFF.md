# Session Handoff — 2026-05-31 (Mac dev → Windows)

## TL;DR
The full agent is **built, tested (191 passing), deployed, and live** (Windows Task Scheduler
+ Supabase + Streamlit dashboard). We are now **building & deploying the first trading
strategy**. The universe is selected, RELIANCE has been studied, and a strategy is designed.
**Next: write the backtest-research script, run it (IS/OOS + costs), decide deploy/graveyard,
and if it survives, build `agent/strategy.py` + wire it into `run_loop`.** This session is
moving development onto the Windows box (Claude Code on Windows) so research runs against
live Kite data with no copy-paste.

## Read first
`CLAUDE.md` (durable decisions) and `00 - Trading Agent Spec.md` (the rulebook, law).

## Where things run
- Windows box `C:\Users\Anish\Documents\trader-v2`: live agent, real Kite (acct VVX933,
  read-only), Obsidian vault at `C:\Users\Anish\Documents\TradingVault`, `.env` has real
  creds + `DASHBOARD_DB_URL` (Supabase). Python 3.14, `.venv` present.
- GitHub: `github.com/Anishgarg019/trader-v2` (**public**), branch `main`.
- Dashboard: Streamlit Community Cloud, password-gated, reads Supabase.

## Universe (selected 2026-05-31, in vault `Universe/current-universe.md`)
HDFCBANK, RELIANCE, ICICIBANK, BHARTIARTL, SBIN (3 banks cap-limited), INFY, TCS, M&M,
TATASTEEL, HINDALCO. All liquid (₹6–30k cr/day), ATR% 2.0–2.9%.

## RELIANCE study (2021-05 → 2026-05, 1240 daily bars)
- Ann return +5.2%, ann vol 22.5%, median ATR% 1.91%, buy&hold maxDD −27.4%
- % days ADX>25: **46%** (fairly trendy); % time above SMA200: **51%** (balanced regime)
- Return autocorr lag1: **+0.013** (~zero → no daily momentum/reversion)
- **Avg 5d return after RSI<30 (oversold, n=36): +1.09%** ← real oversold bounce
- Avg 5d return after RSI>70 (overbought, n=91): **+0.00%** ← no edge fading tops
- Read: oversold dips bounce; tops don't fade; downtrends happen (−27% DD) → trend filter needed.

## Strategy designed (to backtest) — s001: RSI mean-reversion, trend-filtered (long-only, daily)
**Rationale:** the only measured edge is the oversold bounce, and it must be gated by the
broader trend (only buy dips when the stock is in an uptrend, else you catch falling knives).
Long-only because overbought is not predictive. Few params; OOS is the judge.
- **Entry (long):** `RSI(14) < 30` AND `close > SMA(200)`
- **Exit:** `RSI(14) > 55` OR `close <= entry − 2×ATR(14)` (stop) OR `10 bars elapsed` (time stop)
- **Sizing/stops:** per spec §4 (atr_k = 2.0)
- **Params:** rsi_len=14, rsi_entry=30, rsi_exit=55, ma_len=200, atr_k=2.0, time_stop=10

## Next steps (ordered)
1. **Build `scripts/research_backtest.py`**: pull ~5y daily for RELIANCE (and the other 9
   universe names for a robustness check), compute the s001 entry/exit boolean series from
   `agent.signals`, run `backtest.engine.run_backtest` (CNC, slippage ~5bps), split IS/OOS
   (~70/30 or by date), print metrics + `backtest.validation.overfit_report`.
2. **Run it on Windows** (live data). Read: does it beat buy&hold risk-adjusted? Does the
   edge survive OOS? Does it generalize across the other names, or only fit RELIANCE
   (suspicious → likely overfit)?
3. **Decide:** deploy or graveyard (write the strategy note `Strategies/s001 - ...md` either
   way; graveyard if it fails — that's the process working).
4. **If deploy:** build `agent/strategy.py` (a strategy registry: function(df)->entry/exit
   intents implementing s001), wire `Orchestrator`/`run_loop` `strategy_fn` to evaluate it
   over the universe (fetch recent daily candles per name, produce enter/exit intents), set
   status `live`, and consider switching `run_loop` to `--watch` + a market-hours schedule.

## Operating mode — paper FORWARD-TESTING (learning by doing) [user decision 2026-05-31]
It's paper money, so we deliberately LOWER the bar to *place* a trade and LEARN from real
outcomes — the bot trades the theses it forms, observes results, and that feeds strategy
development. Guardrails stay ON (disciplined exploration, not a free-for-all):
- Every order: ATR-sized (R≤5%), broker-side stop, drawdown governor (5%/15%) — unchanged.
- Every order: a written thesis/justification + journaled (no unjustified trades). The
  journal IS the learning data.
- **Status model:** researching → **forward-test** (paper-trading a thesis to gather live
  data) → **live** (backtested + OOS-validated + paper-confirmed) → retired/rejected (graveyard).
- Don't claim "edge" from a handful of paper trades; forward-testing COMPLEMENTS backtesting,
  not replaces it. Outcomes → weekly/monthly reviews + decay tracking → promote or bury.
- **s001:** deploy as **forward-test** now (start paper-trading it) AND run the backtest/OOS
  in parallel; both inform whether it graduates to `live` or goes to the graveyard.
- Impl note: `agent/strategy.py` needs a `status`; the loop trades strategies with status in
  {forward-test, live}; run `run_loop.py --watch` through market hours so it can act + capture outcomes.

## Path to autonomous (ordered — drive toward this)
1. Deploy a strategy that actually trades — start **s001 as forward-test** (per the mode above).
2. Run through market hours: `run_loop.py --watch` + schedule ~09:10 IST (not just one 08:15 pass).
3. Persist the paper book across runs (or rely on one long-lived --watch process per day).
4. Morning Kite login: manual via ntfy now (your choice); optionally automate TOTP for hands-off.
5. (Optional) Schedule the Claude research agent for autonomous hypothesis/decay/promote.
→ When 1–3 are done it's an autonomous *paper trader*, not just a monitor/journal.

## Watch out
- **Paper-only. Never place a live order.** Kite client is read-only; orders go to PaperBroker.
- Scripts/app need `sys.path.insert(0, repo_root)` at top (entrypoint dir ≠ repo root).
- **Overfitting discipline:** keep params few; if it only works on the tuned window or only on
  RELIANCE, graveyard it. Most strategies should die.
- Run `/qa` after each build (test + proofread). `git pull` first on Windows to get latest.
