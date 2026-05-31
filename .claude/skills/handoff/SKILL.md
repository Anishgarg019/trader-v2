---
name: handoff
description: Summarize the current session into SESSION-HANDOFF.md so work can continue in a fresh session without losing context. Invoke when the context window is getting large (≈60%+), before /clear, or when ending a work session on the trading-agent build. Produces a self-contained handoff a new session can resume from by reading CLAUDE.md + SESSION-HANDOFF.md.
---

# Session Handoff

You are writing a **continuation brief** for the next session (possibly a fresh one after
`/clear` or compaction). Goal: a new Claude session, given `CLAUDE.md` + `SESSION-HANDOFF.md`,
can pick up *exactly* where this one left off with zero re-discovery.

## Why this exists
Claude Code has no native "trigger at 60% context" event — skills are invoked, not auto-fired,
and hooks cannot read context usage. So this is the **manual + reliable** path: when context
fills, run `/handoff`, then start fresh (`/clear` or a new session) and the SessionStart hook +
this file restore continuity.

## Steps

1. **Read `CLAUDE.md`** in the project root first — it is the durable decision record. Do not
   duplicate its content; the handoff *references* it and captures only what's NEW or in-flight.

2. **Write `SESSION-HANDOFF.md`** at the project root with this structure:

   ```markdown
   # Session Handoff — <UTC timestamp>

   ## TL;DR (3–5 lines)
   <What this session did and the single most important thing the next session should do first.>

   ## Decisions made this session (not yet in CLAUDE.md)
   - <decision> — <why>
   <If any belong in CLAUDE.md permanently, note "→ promote to CLAUDE.md".>

   ## Current phase & status
   - Build phase: <e.g. Phase 1 — in progress>
   - Done this session: <bullets>
   - Checkpoint state: <passed / not yet / blocked on X>

   ## In-flight / partially done (BE SPECIFIC)
   - Files created/edited: <path — what's done, what's missing>
   - Any code that won't run yet / TODOs left mid-edit: <exact location + intent>

   ## Next steps (ordered, actionable)
   1. <the very next concrete action>
   2. ...

   ## Open questions / waiting on user
   - <e.g. real Windows VAULT_PATH, .env creds, an approval>

   ## Watch out for (gotchas surfaced this session)
   - <e.g. NO Kite sandbox — never place real orders; Python owns safety core; cross-platform paths>
   ```

3. **Verify accuracy against the actual repo state** — list files, check what truly exists vs
   what's planned. Do not claim work is done that isn't (the build's honesty rule applies here too).

4. **If durable decisions emerged**, also update `CLAUDE.md` (§8 Current status, and the relevant
   section) so the permanent record stays correct — the handoff is transient, CLAUDE.md is not.

5. **Tell the user** the handoff is written and give the exact resume instruction:
   > "To continue fresh: `/clear` (or open a new session), then say *'read CLAUDE.md and
   > SESSION-HANDOFF.md and continue.'*"

## Rules
- Be concrete, not vague — "Phase 1 broker guard half-written, missing the no-order-methods
  assertion in kite_client.py:42" beats "working on broker."
- Never fabricate progress. If a checkpoint didn't pass, say so.
- Keep it tight; this is a baton pass, not a diary.
