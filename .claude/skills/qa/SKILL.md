---
name: qa
description: Post-build QA pass for the trading-agent. Invoke after EVERY major change or completed build/phase. Runs the test suite, proofreads the entire codebase for correctness/consistency/safety, and — if the project has a web UI — opens it, screenshots it, and drives it via Chrome DevTools. Reports findings honestly; never claims green when it isn't.
---

# QA pass (run after every major change)

A standing rule from the user: **after every major change or build, test what was built
and proofread the whole codebase.** Be rigorous and honest — if something fails or is
half-done, say so with the evidence. Do not report success you haven't verified.

## 1. Test what was built
- Run the test suite: `python -m pytest -q` (and any phase-specific tests). Report
  pass/fail counts and paste real failure output, not a summary.
- Smoke-run the thing you just built end-to-end where possible (e.g. import the module,
  call its entrypoint with safe/mock inputs). For read-only Kite calls, only if creds +
  `MODE=paper` are present.
- **Safety invariants (this project, non-negotiable — check every time):**
  - `kite_client.py` exposes **no** `place_order`/`place_gtt_order`/`modify_order`/
    `cancel_order` (grep to prove it).
  - No code path sends an order to a live Kite endpoint; all orders go through `PaperBroker`.
  - The paper-mode guard (`MODE=paper`, router is `PaperBroker`) still passes.
  - Risk/governor math is computed in Python, not hand-derived; their unit tests pass.

## 2. Proofread the entire codebase
- Review the diff thoroughly, then do a consistency pass over the whole tree (not just
  changed files). Look for:
  - Correctness bugs, off-by-one, wrong units (₹ vs %, bps), timezone (IST) mistakes.
  - Leftover TODOs, dead code, debug prints, hardcoded tokens/paths, secrets in source.
  - Cross-platform issues (Mac dev → Windows run): `pathlib` not `/`-joined strings, no
    POSIX-only calls, no Mac-only deps.
  - Drift from the spec (`00 - Trading Agent Spec.md` is law) and from `CLAUDE.md` decisions.
  - Any reintroduced "sandbox" assumption that implies a real Kite sandbox exists.
- For a focused correctness sweep you may also run `/code-review` on the diff.

## 3. If (and only if) the project has a web UI
This trading agent currently has **no website** — skip this section unless a web UI exists.
When one does exist:
- Launch it, open it in the browser, and **take screenshots** for visual confirmation.
- Use **Chrome DevTools** (via the chrome-devtools MCP / Playwright if connected; if no
  browser-control tool is available, say so and stop — don't fake it) to:
  - inspect the rendered DOM/console for errors,
  - interact with key flows and screenshot before/after,
  - check network calls and responses.
- Attach screenshots and note any console/network errors.

## 4. Report
- A short verdict: ✅ green / ⚠️ issues / ❌ broken, with the evidence.
- List concrete findings with file:line and a recommended fix.
- If you fixed trivial issues during QA, say what you changed; leave judgment calls to the user.
