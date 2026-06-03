# Session Handoff — 2026-06-03 ~03:05 UTC (Windows prod box)

## TL;DR
This session built the **context digest** (Phase 11 §8 addendum) and then spent the rest
firefighting **why the live agent kept going blind each morning**. Root cause found + fixed:
the `Trader - login reminder` scheduled task had a **malformed command** (python exe glued to
the script path, no space → exit 2), so the user was never nudged → never did the manual Kite
login → token expired → loop ran blind. All fixed; manual-login-only is the locked choice.
**Next session's first job: BUILD THE "Strategies / Research" DASHBOARD TAB** (designed below,
user asked for it, awaiting go-ahead — they implicitly greenlit via the `/handoff` arg).

## Decisions made this session (not yet fully in CLAUDE.md → promote)
- **Manual 07:30 Kite login ONLY.** User explicitly rejected unattended TOTP auto-login
  ("no, i only want the 730am manual login with no failures"). The auto-login work
  (auto_login.py, kite_auth.py, pyotp, KITE_USER_ID/PASSWORD/TOTP_SECRET) was BUILT then
  fully REVERTED (commit 1396e1b). Do **not** re-propose auto-login. → promote to CLAUDE.md.
- **s001 RETIRED** (reversible). It was deployed on all 10 symbols as a "pipeline proof" but
  never fires and falsely marked the whole universe "covered," starving the researcher of
  uncovered targets. Set `status: retired`, `deployed_symbols: []`. Now 0 active forward-tests;
  all 10 names uncovered → researcher targets them. → promote to CLAUDE.md.
- **Scheduler gotcha (durable):** always create tasks with a SPACE between the exe and script
  (`"py.exe" "script.py"`). A glued `py.exeC:\...py` fails silently with exit code 2. → promote.

## Current phase & status
- **Phase 11 + context-digest: BUILT, TESTED, LIVE.** 336 passed / 2 skipped. Tree clean,
  in sync with origin/main @ `1396e1b`.
- **Live service running** on Task Scheduler (all IST; box clock = IST):
  - `Trader - login reminder` 07:30 daily — RECREATED clean (was the bug); unconditional ntfy
    nudge + retry/backoff. Verified result 0.
  - `Trader - daily loop` 08:15 daily — `run_loop.py --watch 60` (recreated; the old one ran
    without --watch so only did pre-market). Currently RUNNING today on a valid token.
  - `Trader - researcher daily` 16:15 Mon–Sat, `Trader - researcher weekly` 16:15 Sun.
- **Today (06-03): logged in, token valid, loop running + publishing**, pre-market at handoff.
- Vault: **0 active forward-tests**, graveyard = s002–s013 (researcher has been proposing +
  gate-rejecting; nothing has passed the strict OOS gate — expected/by design).

## In-flight / partially done
- **NOTHING half-written.** All code committed + green. The only OPEN WORK is the new feature
  below (not started).
- **"Strategies / Research" dashboard tab — DESIGNED, NOT BUILT (the next task).** User asked:
  "display strategies currently testing, accepted, rejected, reasoning, backtesting on the
  dashboard." Plan (all source data already exists in the vault registry + digest + notes):
  1. Schema (`dashboard/schema.sql`): add `strategies` table (id, name, status, families,
     deployed_symbols, oos_return, oos_sharpe, symbols_deployed, symbols_tested, reasoning/
     lesson, created, updated_at) + optional `research_runs` (date, cadence, proposed, valid,
     deployed, rejected).
  2. `dashboard/store.py`: add upsert + read methods for those tables.
  3. `dashboard/publisher.py`: currently publishes PERF data only — extend to also push from
     `StrategyRegistry.load_active_specs()` + graveyard notes + the research digest
     (`_context/RESEARCH-DIGEST.md` rejected rollup) + research notes (`Research/*-researcher-*.md`).
  4. `dashboard/app.py`: add a **"Strategies / Research"** tab — Testing (active), Rejected
     (graveyard rollup with tried-count + lesson), per-strategy backtest (IS/OOS + win/loss-by-
     symbol), and a research-run timeline.
  5. **Backfill** the existing graveyard (s002–s013) so Rejected is populated immediately.
  6. Tests in `tests/test_dashboard.py`; run `/qa`; verify it renders.
  Note: this touches the LIVE dashboard (schema+publisher+app) — proceed per the user's
  go-ahead (the `/handoff` arg "keep the strategies tab concept in mind" = build it next).

## Next steps (ordered)
1. **Build the Strategies/Research dashboard tab** (plan above). Backfill s002–s013.
2. Re-run `/qa`; confirm the tab renders against the live Supabase data.
3. Commit + push from Windows.
4. (Ongoing) Each trading day: user does the manual 07:30 login (now reliably reminded);
   loop hot-reloads the token within ~60s if the login is late.

## Open questions / waiting on user
- Final explicit go-ahead to modify the live dashboard (assumed yes from the handoff arg).
- Whether to widen the DSL/signal space later so strategies can actually pass the gate and
  trade (currently everything is correctly gate-rejected; deployments are rare by design).
- s001 is retired but reversible — restore as a 1-symbol canary only if the user asks.

## Watch out for (gotchas)
- **NEVER place a real order.** Read-only Kite (no order methods); orders only to PaperBroker.
  The KiteSession hot-reload proxy is still read-only (assert_no_order_methods passes).
- **Truth-vs-view boundary (digest):** the digest only shapes WHAT the researcher proposes;
  the overfit gate + `registry.load_active_specs` read the REAL notes. A stale digest can
  never cause a bad deploy. Never let the digest gate a deployment.
- **No `live` status** — forward-test is the ceiling; registry refuses `live`.
- **Scheduler:** SPACE between exe and script (exit-2 bug). Tasks are "Interactive only" (box
  must be logged on) — user declined fully-headless (no stored creds).
- **Daily login is the one manual step** — interactive `kite_login.py` can't take the pasted
  token via the `!` runner (EOF); use the flow: generate login URL → user authorizes → pastes
  redirect URL → complete with `echo "<redirect-url>" | python scripts/kite_login.py`.
- **Token expires ~6 AM IST**; the `--watch` loop reads it once at startup but `session.reload()`
  re-reads `.kite_token.json` each pass, so a late login is picked up (don't "fix" by restarting).
- Researcher needs a valid token at 16:15 to fetch frames (else exits 1) — same login dependency.
