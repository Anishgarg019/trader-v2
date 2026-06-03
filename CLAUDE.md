# CLAUDE.md — Systematic Paper-Trading Agent (build context)

> This file is the **durable decision record** for building the agent. It is reloaded
> automatically after compaction, so it must stay accurate. The runtime *rulebook* is
> `00 - Trading Agent Spec.md` (the spec) — **the spec wins on any runtime-behavior
> conflict.** This file records architecture/build decisions and the deviations we made
> from the original docs after verifying reality.

---

## 1. What we're building (one paragraph)

A **systematic paper-trading agent for Indian equities (NSE/BSE)**, ₹1,00,000 starting
capital, cash-only, **1.0x leverage (never leveraged)**. It selects ~10 liquid/volatile
stocks itself, encodes technical signals into precise rules, backtests them on **real
Kite historical data** with realistic costs, trades only ensembles that survive
out-of-sample validation, sizes every trade by ATR, protects every entry with a stop,
justifies every trade in writing, and journals everything to an **Obsidian vault** (plain
Markdown). Hard drawdown limits halt it automatically. The real job is **research** —
most ideas are rejected; a graveyard of killed strategies grows faster than the live
roster. **End state = a small, documented, backtested strategy roster, not a pile of
trades.**

---

## 2. 🔴 The critical reality: there is NO Kite sandbox

The original spec/brief assume `broker: zerodha-kite-sandbox` and "every order routes to
the Kite sandbox." **This was verified false (May 2026):** Zerodha's own support page
states *"No, Zerodha does not offer an API sandbox environment."* Kite Connect has exactly
**one mode: live**, against a real account with real money. There was no sandbox launched
in 2025 or 2026.

**Decision: build a local paper-trading engine.** This honors the spec's #1 directive
(capital preservation / never touch a live account) *literally and more safely* than a
vendor sandbox would.

- **Market data:** real, from Kite Connect (read-only) — `get_quotes`, `get_ltp`,
  `get_historical_data`, `get_profile`, `get_margins`, `get_positions`, `get_holdings`,
  `get_orders`. Historical-data add-on is **enabled** (₹500/mo plan). Access token
  expires daily (~6 AM IST) → auto-refresh.
- **Orders:** routed **only** to an in-process `PaperBroker` simulator that fills against
  live quotes and models the spec's friction/slippage. The simulator implements
  `place_order` / `place_gtt_order` / `modify_order` / `cancel_order`.
- **The Kite client class has ZERO order/write methods** — you cannot call what does not
  exist. This is the structural enforcement of "never touch live."
- **Redefined §1.3 safety guard:** assert (a) active order router is `PaperBroker`,
  (b) the Kite client exposes no order/write methods, (c) `MODE=paper` env is set. Any
  ambiguity → halt + write a `system-alert` note, do nothing else.

**Hard rule for Claude:** never write code that sends an order to Kite, and never place a
real order. If anything would, stop and ask.

---

## 3. Architecture (decided)

**Hybrid. Conductor = a scheduled Claude Code agent; safety/numeric core = Python.**

- A **scheduled Claude Code agent** wakes ~08:15 IST (T-60m) on trading days, runs the
  spec's loop, invokes the Python core, performs the *judgment* steps itself, and writes
  the vault. Scheduling on the Windows runtime = **Windows Task Scheduler** (wired in
  Phase 9).
- **Python core** = deterministic, unit-tested modules the agent calls.

### Python ↔ Claude boundary (decided)
- **Python OWNS the safety/numeric core (single source of truth, unit-tested,
  non-overridable):** ATR position sizing, the 5%/15% drawdown governor, leverage/cash-cap
  check, paper fills, cost/slippage math, indicator computation, backtest engine. The
  governor is **code, not a judgment call.** Claude must never hand-compute these by
  reasoning — LLM arithmetic errors in capital-preservation logic are the exact failure
  the spec forbids.
- **Claude DRIVES everything else (judgment):** overnight/news review, forming
  hypotheses, deciding what to backtest, reading backtest output to promote→live or
  send→graveyard, P&L attribution narrative, reconciliation review, universe-pick
  rationale, weekly/monthly/lessons notes, and triggering each loop block. Claude is *in
  the loop on every stage* — it runs and interprets the Python; it just doesn't recompute
  the safety math.

---

## 4. Stack & layout

- **Python 3.11+**, `pandas`, `numpy`, `kiteconnect`, `pyyaml`, `python-dotenv`, `pytest`.
- **Indicators hand-rolled** (pure parameterized functions), not `pandas-ta`, for exact
  reproducibility per spec §3.2. (Revisit only if a library is explicitly requested.)
- Repo layout per `BUILD-BRIEF.md` §2, adapted: `broker/kite_client.py` (read-only) +
  `broker/paper_broker.py` (simulator); rest as in the brief.

### Cross-platform: built on Mac, RUN on Windows
- Dev happens on this Mac; **production runs on a Windows machine where the Obsidian vault
  lives.** Code must be cross-platform: use `pathlib`, no POSIX-only assumptions, no
  Mac-only deps, forward-compatible line endings.
- **`VAULT_PATH`** env var points at the Obsidian vault. On Mac dev → a throwaway local
  dev vault (e.g. `./vault-dev/`). On Windows → the user's real Obsidian vault path (TBD;
  user provides). The spec file lives at the vault root.
- Kite `api_key`/`api_secret` come from a **`.env`** file (scaffold `.env.example`; never
  put live secrets in chat or commits). `MODE=paper` always.

---

## 5. Build plan — phases (each ends in a verifiable checkpoint)

- **Phase 0 — Reconcile the docs** ✅ *DONE (2026-05-31)*: spec bumped to v1.1 and
  `BUILD-BRIEF.md` reconciled — "Kite sandbox" replaced with read-only-data +
  local-paper-engine reality, Reality-note banners added to both, every risk/safety rule
  preserved. Remaining "sandbox" mentions are intentional (the banners).
- **Phase 1 — Broker plumbing & paper guard:** `kite_client.py` (read-only, daily token
  refresh, no order methods) + `paper_broker.py` + redefined safety guard.
- **Phase 2 — Data & trading-day gate:** `holidays_2026.py` (verbatim) + `trading_day.py`
  (calendar+weekend AND full-universe live-data probe; stale/empty rules; pre-open
  re-probe; 2027 hard-stop).
- **Phase 3 — Indicators & signals:** `signals/*` pure functions (MA/EMA, ADX, RSI, MACD,
  stochastic, divergence, OBV, VWAP, Bollinger, ATR, S/R, basic patterns).
- **Phase 4 — Backtester + real costs + validation:** vectorized engine; verified 2026
  Zerodha charges (CNC ₹0 brokerage; MIS ₹20/0.03%; STT, exchange txn, SEBI ₹10/cr,
  stamp, 18% GST, DP on sells) + slippage; IS/OOS split + overfit flags.
- **Phase 5 — Risk sizing & governor (safety core, unit-tested hard):** `risk.py`
  (R=min(5% equity, ATR-implied), cash-cap=1x, total open risk ≤15%, per-name ≤20%) +
  `governor.py` (5% daily / 15% total off day-open & high-water).
- **Phase 6 — Vault writer & universe:** `vault/writer.py` (exact §7.2–§7.4 YAML schemas +
  folder tree) + `universe.py` (liquidity gate → volatility → sector cap).
- **Phase 7 — Execution (first PAPER orders) & reconcile:** `execution.py`
  (order→stop→trade-note, non-negotiable order, all via `PaperBroker`) + `reconcile.py`.
- **Phase 8 — Daily loop orchestrator:** `loop.py` in spec cadence + §8 startup checklist
  + research-only on closed/halted days.
- **Phase 9 — Reviews, decay monitoring, hardening + Windows scheduling:**
  weekly/monthly/lessons notes, decay→retire, retries/backoff, logging, Task Scheduler.

**Verified facts to honor at build time:** Kite historical endpoint
`GET /instruments/historical/:token/:interval`, intervals `minute|3|5|10|15|30|60minute|day`,
candle = `[ts, o, h, l, c, v]`. Minute history is lookback-capped/rate-limited; day candles
reach back years (spec §1.4). Verify exact Zerodha charge rates against zerodha.com/charges
when coding Phase 4.

---

## 6. Working agreements

- **No order without a journal note. No strategy live without a backtest note. No drawdown
  halt without a post-mortem.** (Spec §0/§4.3/§5/§6.5.)
- Fail safe, not silent: any sandbox-vs-live ambiguity, dark data, or risk breach → stop,
  write `system-alert`, do nothing risky.
- Be honest about uncertainty: backtest, paper, and (hypothetical) live results diverge —
  report edge **and** caveats.
- Pause at each phase checkpoint for review unless told to run straight through.
- **Paper forward-testing mode (user decision 2026-05-31):** since it's paper money, the bot
  MAY trade theses it forms (status `forward-test`) to learn from live outcomes — guardrails
  stay ON (ATR sizing, stops, drawdown governor) and every order is justified + journaled.
  Status model: researching → forward-test → live (backtested+OOS+paper-confirmed) →
  retired/rejected. Forward-testing complements, never replaces, backtest+OOS validation.
- **QA after every major change (standing user rule):** run the **`/qa`** skill — test
  what was built, proofread the *entire* codebase, and (if a web UI ever exists) open it,
  screenshot it, and drive it via Chrome DevTools. Report honestly; never claim green
  unverified.

---

## 7. Context continuity

True "trigger at 60% context" is **not possible** in Claude Code (no context-percentage
hook/event; skills are invoked, not auto-fired). Instead:
- This `CLAUDE.md` is reloaded automatically after compaction — keep it current.
- Run **`/handoff`** (project skill) when context gets large to write `SESSION-HANDOFF.md`
  (decisions + phase status + next steps), then continue in a fresh session seeded by it.
- A `SessionStart` hook re-points Claude at `CLAUDE.md` + `SESSION-HANDOFF.md` after
  compaction/new sessions.

---

## 7a. Deployment plan (agreed)

Build & test on Mac → user pushes to **git** → pulls onto the **Windows** box (where the
Obsidian vault lives) → Claude guides the user through Windows setup interactively. So:
keep everything cross-platform (done), keep the repo git-clean (`.gitignore` excludes
`.env`, tokens, `.venv`, `vault-dev/`), and produce a **`SETUP-WINDOWS.md`** guide as a
late-phase deliverable (venv, deps, `.env`, `kite_login.py`, `VAULT_PATH`, Task Scheduler).

## 8. Current status

- ✅ Requirements understood; key decisions made (this file).
- ✅ Verified: no Kite sandbox → local paper engine; historical add-on enabled; Mac-dev /
  Windows-run; Python owns safety core; plan approved through Phase 9.
- ✅ Phase 0 done: spec→v1.1, brief reconciled to paper-engine reality.
- ✅ Phase 1 CODE done: repo scaffolded (venv, pyproject, config); `kite_client.py`
  (read-only, no order methods), `paper_broker.py` (fills/GTT/positions/cash),
  `safety.py` guard, `scripts/kite_login.py`, `scripts/verify_phase1.py`. **37 tests pass;**
  QA pass green (no order-method leak, cross-platform, no secrets).
- ⏳ **Phase 1 live checkpoint pending creds:** user fills `KITE_API_KEY`/`KITE_API_SECRET`
  in `.env`, runs `python scripts/kite_login.py` then `python scripts/verify_phase1.py`.
- ✅ Phase 2 done: `data/holidays_2026.py` (16 dates — verified identical to spec table,
  weekdays match, Muhurat 2026-11-08) + `agent/trading_day.py` (Layer 1 calendar/weekend/
  2027-halt + Layer 2 universe stale/empty probe + pre-open re-probe + combined decision).
  **57 tests total pass;** QA green.
- ✅ Phase 3 done: `agent/signals/` — pure parameterized indicators: `_common` (sma/ema/
  rma/true_range), `trend` (adx, MA crossover, cross_up/down), `momentum` (rsi, macd,
  stochastic, bull/bear divergence), `volume` (obv, vwap, spike), `volatility` (atr,
  bollinger), `structure` (pivots, breakouts, HH/LL), `patterns` (doji, hammer, engulfing).
  **78 tests total pass** (each indicator verified vs an independent re-derivation or
  analytic input); QA green.
- ✅ Phase 4 done: `backtest/costs.py` (Zerodha rates **verified vs zerodha.com/charges
  2026-05-31**: CNC ₹0 brokerage + 0.1% STT + 0.00307%/0.00375% txn + ₹10/cr SEBI + 18% GST
  + 0.015% stamp + ₹15.34 DP; MIS 0.03%/₹20 cap + 0.025% sell STT + 0.003% stamp, no DP),
  `backtest/engine.py` (vectorized long-only, next-open fill / no look-ahead, slippage,
  costed trades, equity curve + CAGR/maxDD/Sharpe-like/win-rate/exposure), `validation.py`
  (IS/OOS split + overfit flags → reject). **103 tests pass;** RSI+MA example runs
  end-to-end; overfit rule correctly rejected. QA green.
- ✅ Phase 5 done: `agent/risk.py` (ATR sizing — R=min(5% equity, budget), cash-cap=1×,
  per-name ≤20%, total open risk ≤15% → skip; returns qty+reason+stop_price) and
  `agent/governor.py` (daily 5% / total 15% drawdown halts off day-open & high-water +
  `assert_no_leverage`). **129 tests pass;** 20k-case fuzz confirms the 1× invariant never
  breaks. QA green.
- ✅ Phase 6 done: `vault/writer.py` (`VaultWriter`: ensure_structure for the §7 folder
  tree, generic write/read/update_frontmatter, and trade/strategy/daily/system-alert
  builders matching §7.2–§7.4 exactly; None→blank YAML) and `agent/universe.py`
  (`compute_candidate_metrics`, `select_universe` liquidity→ATR-band→sector-cap, and
  `write_universe_note`). **144 tests pass;** notes round-trip; QA green.
- ✅ Phase 7 done: `agent/execution.py` (`ExecutionEngine.execute_entry` — guard→governor→
  sizing→no-leverage→**order→stop(GTT/SL-M)→trade-note**, all via PaperBroker; plus
  `close_position` updating the note) and `agent/reconcile.py` (`reconcile_positions` +
  `expected_positions_from_open_trades`). **156 tests pass;** order→stop→note sequence
  verified, governor/sizing/safety gates block correctly, reconcile catches mismatches. QA green.
- ✅ Phase 8 done: `agent/loop.py` (`Orchestrator` + `LoopState`): §8 startup checklist
  (safety→gate→equity/day-open/HWM→governor→reconcile) then `run_day` (walk all blocks) /
  `run_once` (clock-appropriate block); research-only on closed/full-stop; daily-halt blocks
  entries; writes the daily note. **164 tests pass;** closed→research-only, open→walks every
  block, governor respected. QA green.
- ✅ Phase 9 done: `agent/reviews.py` (weekly/monthly/lessons), `agent/decay.py` (rolling
  win-rate → retire), `agent/retry.py` (backoff), `agent/logging_setup.py`, `agent/state.py`
  (persist LoopState), `scripts/run_loop.py` (Task Scheduler target), `SETUP-WINDOWS.md`,
  `requirements.txt`. **177 tests pass;** git-clean (no secrets tracked). QA green.
- ✅ Phase 10 (cloud dashboard) done: `dashboard/store.py` (one Store over DB-API; SQLite
  local + Postgres/Supabase prod, same SQL), `dashboard/publisher.py` (agent→DB, perf data
  only, best-effort), `dashboard/app.py` (Streamlit: equity/drawdown graphs, positions,
  **trade log with the "why"/justification**, win-rate by strategy, alerts, universe;
  password-gated; auto-refresh), `seed_demo.py`, `schema.sql`, `dashboard/README.md`.
  `run_loop.py` publishes best-effort + `--watch` near-real-time mode. **187 tests pass;**
  app boots (health 200) & render data path verified; dashboard has NO order methods/creds.
- ✅ **BUILD COMPLETE (Phase 0 + phases 1–10).** Next: user pushes to git → pulls on
  Windows → Claude guides Windows setup (`SETUP-WINDOWS.md`), then `kite_login.py` +
  `verify_phase1.py` for the first live read-only checkpoint, then deploy first strategy.
- ~~Known limitation: paper-book in-memory~~ **RESOLVED (Phase 11):** the paper book
  (positions/cash/GTTs/orders/trades) now persists to `.paper_book.json` via
  `agent/state.py` (`save_paper_book`/`load_paper_book`), reloaded at the start of every
  `run_loop.py` pass — multi-day continuity holds across process restarts.
- ✅ **DEPLOYED & LIVE (2026-05-31):** running on Windows (Task Scheduler 07:30 ntfy login +
  08:15 loop), Supabase + Streamlit dashboard live. **Dev moved to Windows — commit/push from
  there; Mac is secondary, don't push from Mac.**
- ✅ s001 (RSI mean-reversion) re-expressed as a DSL spec (`agent/strategy.S001_SPEC`;
  compiler reproduces the hand-written RSI/SMA200 signal logic) and migrated in the vault to
  `status: forward-test` with `spec:` + `deployed_symbols:` (all 10 universe names) via
  `scripts/deploy_s001.py`. The hand-written `build_s001_strategy_fn` is retired (kept only for
  the equivalence test). Failed OOS 0/10 — pipeline proof, not edge.
- ✅ **Phase 11 — Autonomous Researcher: BUILT, TESTED, DEPLOYED (2026-05-31, on Windows).**
  - `agent/strategy_spec.py` (DSL validator + `count_params`, tuned-knobs-only, 5-knob ceiling),
    `agent/strategy_compiler.py` (compile→entries/exits + `strategy_fn_factory`; divergence
    predicates made causal — fixed a real look-ahead), `backtest/research.py`
    (`evaluate_spec` per-symbol gate → `deployed_symbols`), `agent/registry.py`
    (`spec:`+`deployed_symbols:` frontmatter; `load_active_specs` forward-test-only + re-validate;
    `coverage`; refuses `live`), `agent/live_strategies.py` + `run_loop.py` wiring (registry-backed
    `strategy_fn`), `backtest/optimize.py` (improvement loop — OOS-margin + no-added-knobs accept,
    the curve-fitting guard), `scripts/researcher.py` (propose via headless `claude -p` → validate →
    gate → deploy forward-test; caps in Python; ntfy).
  - **`live` is NOT built/wired** — forward-test is the ceiling (registry refuses `live`).
  - Paper-book persistence added (`.paper_book.json`); `agent/notify.py` failure alerts.
  - **284 tests pass (2 skipped); /qa green (both blocks).** Verified live: loop runs
    registry-backed (research-only on weekends, PaperBroker-only); researcher run end-to-end
    proposed 2 → both graveyarded (honest zero); dashboard data path current in Supabase.
  - Task Scheduler: `Trader - researcher daily` (Mon–Sat 16:15 IST) + `Trader - researcher
    weekly` (Sun 16:15, `--weekly`), created. To run when logged-off, flip each task to
    "Run whether user is logged on or not" in the GUI (needs the account password — manual).
- ✅ Spec **§7.3 reconciled** (repo + vault copies): added `forward-test` status +
  `spec:`/`deployed_symbols:` frontmatter block; `live` left reserved/unwired.
- ✅ **Context digest (CONTEXT-DIGEST-SPEC addendum) BUILT, TESTED, LIVE:** `vault/digest.py`
  maintains `<VAULT>/_context/RESEARCH-DIGEST.md` — the researcher's six decision inputs
  (universe, coverage, active, rejected-rollup, regime, perf) as a Python-maintained
  materialized VIEW, bounded by novelty-keyed rollup (`agent/strategy_spec.novelty_key`) +
  a token cap (`DIGEST_TOKEN_CAP`, binary-search compaction, drops logged). `VaultWriter`
  updates it atomically on every write (fail-safe — a digest error never breaks the note
  write). `scripts/researcher.py` reads ONLY the digest for proposal context (prompt size
  decoupled from vault size), while the gate + `registry.load_active_specs` still read the
  REAL notes — **the digest shapes WHAT is proposed, never WHAT deploys** (truth-vs-view
  boundary; a stale digest can only waste a cycle). Weekly-deep does `rebuild_from_vault` +
  rebuild-and-diff → `system-alert` on drift, plus a stateless completeness-critic
  (`claude -p`, logged, never gates) and re-proposal/near-dup telemetry. **+40 tests; /qa
  green; verified live** (digest bootstrapped, proposal from digest at ~700 tok, proposed→
  gated→graveyarded with no human step).
- **Only recurring manual step:** the ~30s daily Kite login (`kite_login.py`) — token expires
  ~6 AM IST; the 07:30 ntfy reminder pings the phone.
- ✅ **Live-ops hardening (2026-06-01→03):**
  - **Root-caused the recurring "agent went blind each morning":** the `Trader - login
    reminder` task command was MALFORMED (python exe glued to the script path, no space →
    exit 2), so no nudge was ever sent → no manual login → token expired → loop ran blind.
    **Gotcha (durable): always create Task Scheduler tasks with a SPACE between the exe and
    the script.** All tasks recreated cleanly.
  - `Trader - daily loop` recreated as `run_loop.py --watch 60` (the old one ran without
    `--watch`, so it only did a pre-market pass and never traded the session).
  - `run_loop.py` hot-reloads the token mid-`--watch` (`agent/kite_session.py` — read-only
    proxy that re-reads `.kite_token.json` each pass), so a login done after 08:15 is picked
    up within ~60s with no restart. `notify_login.py` retries the ntfy send (slow-network
    boot) + `kite_login.py` prints ASCII `[OK]` (the ✅ emoji crashed the cp1252 console,
    making a successful login exit non-zero).
  - **Manual 07:30 login is the LOCKED choice (user 2026-06-02):** unattended TOTP auto-login
    was built then fully REVERTED at the user's request ("no, i only want the 730am manual
    login with no failures"). Do not re-propose auto-login / stored credentials.
- ✅ **s001 RETIRED (2026-06-02, reversible):** it never fires and was deployed on all 10
  symbols as a "pipeline proof," falsely marking the whole universe "covered" and starving the
  researcher of uncovered targets. Now `status: retired`, `deployed_symbols: []` → 0 active
  forward-tests, all 10 names uncovered (the researcher's priority targets). Graveyard =
  s002–s013 (all gate-rejected — deployments are rare BY DESIGN; nothing has cleared the strict
  OOS gate yet). Restore s001 as a 1-symbol canary only if the user asks.
- ⏳ **NEXT: "Strategies / Research" dashboard tab (DESIGNED, NOT BUILT).** User wants the
  dashboard to show strategies testing / accepted / rejected + reasoning + backtest. Plan in
  `SESSION-HANDOFF.md` (add `strategies` table → extend publisher to push from registry +
  graveyard + digest → new Streamlit tab → backfill s002–s013). All source data already exists
  in the vault; no new data sources needed.
