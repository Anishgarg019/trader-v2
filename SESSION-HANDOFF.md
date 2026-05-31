# Session Handoff — 2026-05-31 (Mac architect session, late)

## TL;DR
System is **built, deployed, and live** (Windows Task Scheduler + Supabase + Streamlit).
This Mac session turned the Phase 11 design (previously trapped in chat) into **durable,
build-ready files** and locked four user decisions about how the autonomous researcher
deploys and improves strategies. **Nothing built in code this session** (Phase 11 is a
Windows build); **nothing pushed from Mac.** The single most important next thing: **build
Phase 11 on Windows from `RESEARCHER-SPEC.md`**, and **fix s001's status (`live` →
`forward-test`)**.

## ⚠️ Where the durable artifacts now live (carry these to Windows)
- **`RESEARCHER-SPEC.md`** (repo root) — the full, build-ready Phase 11 spec, grounded
  against the real code (signal signatures, `run_backtest`, `overfit_report`,
  `write_strategy_note`, the `strategy_fn(ctx)` loop contract).
- **`prompts/research_desk_system.md`** — the headless-Claude "constitution" (system prompt).
- Both are **untracked on Mac** (not committed, not pushed). `CLAUDE.md` §8 was also edited
  (uncommitted). The s002 thesis and Phase 11 design are **no longer chat-only** — they're
  in these files.

## Decisions locked this session (user, 2026-05-31)
1. **No `live`, don't even wire it in.** Researcher deploys to `forward-test` only; nothing
   reads/writes `live` until the user explicitly asks. No `promote.py`, no promotion path.
2. **No s002.** The autonomous researcher invents its own strategies. **s001 is the only
   hand-written seed**, kept purely to validate the DSL/compiler against a known result.
3. **Per-symbol deployment.** Backtest each strategy on every universe stock independently;
   deploy it **only on the stocks it's profitable on** (`deployed_symbols`), never the ones
   it lost on. Empty set → reject to graveyard. **`MIN_SYMBOLS = 1`** (locked) — deploy on
   even a single profitable stock; the paper forward-test + decay monitor catch flukes.
4. **Cover the rest + keep improving.** Stocks with no profitable strategy ("uncovered")
   are the priority target for new strategies. Profitable-but-mediocre strategies get an
   **improvement loop** (`backtest/optimize.py`, §8.5) — variants accepted **only on
   out-of-sample gain, with no added knobs** (guarded against curve-fitting; it's safety
   invariant #7).
5. **Param-count rule:** count only deliberately-tunable knobs (thresholds, `atr_k`);
   conventional lengths (14/20/50/200) are fixed structure, excluded from `n_params`.

## Carried-over earlier decisions (still hold)
- **Dev is on Windows; Mac is secondary.** Commit/push from Windows; **don't push from Mac.**
- **Safety model for Phase 11:** Claude proposes strategies as a constrained JSON DSL over
  the signal library; deterministic Python compiles + backtests + gates; **the execution
  path only ever runs the compiled DSL, never LLM-written code; the overfit gate is code.**
- **Auto-deploy ceiling is `forward-test`** (paper). Guardrails (ATR sizing, stops, drawdown
  governor) stay ON; every order justified + journaled.

## Current phase & status
- **Phase 11 (autonomous researcher): DESIGNED + spec'd, NOT built.** Build it on Windows
  per `RESEARCHER-SPEC.md` §11 checklist.
- **s001: deployed on Windows, UNCOMMITTED/UNPUSHED, mislabeled `status: live`.** It FAILED
  OOS (0/10) → must be **`forward-test`** (pipeline proof, not edge; rarely opens — by design).
- Tests on Windows last seen: 191 passed / 2 skipped (pre-Phase-11).

## Phase 11 build order (from RESEARCHER-SPEC.md §2/§11)
`agent/strategy_spec.py` (DSL + validator + `count_params`) → `agent/strategy_compiler.py`
(entries/exits + `strategy_fn_factory(deployed_symbols=…)`) → `backtest/research.py`
(`evaluate_spec(spec, frames)` → per-symbol gate → `deployed_symbols`) → `agent/registry.py`
(`spec:` + `deployed_symbols:` frontmatter, `load_active_specs` forward-test only,
`coverage()`) → re-express s001 as a spec (then retire the hand-written version) → wire
`scripts/run_loop.py` `strategy_fn` from the registry → `scripts/researcher.py`
(coverage-targeted proposals + caps) → `backtest/optimize.py` (improvement loop §8.5) →
drop `prompts/research_desk_system.md` in place → Task Scheduler (daily ~16:15 + weekly Sun,
improvement pass weekly only) → tests (incl. `test_optimize.py` curve-fitting guard) → `/qa`.

## Next steps (ordered, actionable)
1. **On Windows:** set s001 `status: forward-test`, then **commit + push** the s001 work so
   origin/Mac catch up (resolves the divergence below). Also pull in the Mac-authored
   `RESEARCHER-SPEC.md`, `prompts/research_desk_system.md`, and the `CLAUDE.md` edit.
2. **Build Phase 11** per the build order above / `RESEARCHER-SPEC.md` §11.
3. **Re-express s001 as a DSL spec** (validate compiler reproduces known result).
4. **Reconcile spec §7.3:** add `forward-test` status + the `spec:` + `deployed_symbols:`
   frontmatter block (leave `live` reserved/unwired).

## Open questions / waiting on user
- *(None blocking.)* Univ-pass fraction is GONE — superseded by per-symbol deployment; don't
  reintroduce it. Resolved this session: research-desk prompt DRAFTED; live-gating NOT BUILT;
  **`MIN_SYMBOLS = 1`** (locked).

## Watch out for
- **GIT DIVERGENCE:** origin + Mac are behind; **Windows has uncommitted, unpushed s001 work.**
  The Mac-authored Phase 11 files + CLAUDE.md edit are **untracked/uncommitted on Mac**. Do
  NOT push from Mac (would risk conflicting with Windows's uncommitted work). Reconcile on
  Windows: push s001 first, then bring the Mac files over.
- **NEVER place a real order.** Paper guard holds. Phase 11 boundary: execution runs ONLY the
  compiled DSL — never code the LLM writes. The overfit gate + the improvement-acceptance
  rule are code, not LLM judgment.
- **The improvement loop is a curve-fitting trap if done wrong** (safety invariant #7):
  accept a variant ONLY on out-of-sample gain, no extra knobs, all hard gates pass.
  IS-only improvement never deploys. `test_optimize.py` must enforce this.
- **Per-symbol:** a strategy trades ONLY its `deployed_symbols`; never the names it lost on.
- **s001 `live` is wrong** → `forward-test`.
- `sys.path.insert(0, repo_root)` needed atop scripts/app (entrypoint dir ≠ repo root).
- Paper book is in-memory; `run_loop --watch` must be launched each trading morning.
- Run `/qa` after each build; honesty rule applies.
