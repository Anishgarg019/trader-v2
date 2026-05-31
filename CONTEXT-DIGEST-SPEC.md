# CONTEXT-DIGEST-SPEC.md — the maintained research digest (Phase 11 addendum)

> **Authored on Mac (architect session), 2026-05-31.** Addendum to `RESEARCHER-SPEC.md` —
> it **replaces the "summarize-on-read" context-assembly step in §8** with a single,
> incrementally-maintained digest file. Build on Windows. Runtime rulebook
> (`00 - Trading Agent Spec.md`) still wins on any behavior conflict.
>
> **Why:** decouple the researcher's prompt size from the (ever-growing) vault size. The
> graveyard grows faster than the live roster, so re-summarizing the whole vault every run
> is O(vault) and re-processes dead strategies forever. A maintained digest is O(1) per run
> and summarizes each item **once**. Same proven pattern as this repo's `MEMORY.md`.

---

## 0. The one-line principle

**Don't summarize the vault — materialize the researcher's decision inputs, as an index
*into* the notes (which remain the source of truth), bounded by rollup, maintained by
deterministic Python, and continuously verified.**

---

## 1. The digest file

- Path: `<VAULT>/_context/RESEARCH-DIGEST.md` (Obsidian-visible; regenerable; cross-platform
  via `pathlib`). It is **derived data** — never hand-authored as truth; the strategy/daily
  notes are truth.
- Format: YAML frontmatter (machine-read by the researcher orchestrator) + a human-readable
  body. One file. Bounded (see §4 token budget).

### 1.1 Schema = the researcher's closed list of decision inputs (§3 of RESEARCHER-SPEC.md §8)

The digest carries **exactly** what making a strategy consumes — nothing the decision
touches is summarized away, because the schema *is* the decision's inputs:

```yaml
---
type: research-digest
generated: <date>            # last update
rebuilt: <date>              # last full rebuild from scratch (§5)
universe: [SYM, ...]         # current, bounded
coverage:
  covered: [SYM, ...]        # ≥1 profitable strategy trades these
  uncovered: [SYM, ...]      # PRIORITY targets — no profitable strategy
active:                      # forward-tests; bounded by MAX_ACTIVE_FORWARD_TESTS (≤8)
  - id: s001
    family: [mean-reversion]
    deployed_symbols: [SYM, ...]
    recent: { trades_30d: N, win_rate_30d: 0.xx, oos_sharpe: 0.xx }
    note: "Strategies/s001 - ….md"        # POINTER to full record
rejected:                    # the graveyard digest — INFORMATIONAL, not prohibitive (§3)
  - key: "mean-reversion|rsi_below+rsi_above|banks"   # novelty-matched dedup key (§3.1)
    tried: 47
    last: <date>
    lesson: "failed OOS — banks mean-revert weakly intraday"   # facts, not 'never works'
    examples: ["Strategies/Graveyard/s0xx - ….md", ...]        # ≤3 POINTERS
regime: "<one compact line: trend/vol/breadth summary>"
perf:                        # rolling, NOT all history
  window_days: 30
  equity_change_pct: 0.xx
  open_positions: N
budget_tokens: <measured size of this digest>   # asserted ≤ cap (§4)
---

## (human body: same content, readable; for Obsidian/audit)
```

> The researcher orchestrator reads **only this file** for proposal context. Anything
> *load-bearing* (the actual `spec:` to compile, a strategy's full `deployed_symbols`) is
> still read from the real note by `registry.load_active_specs` — so a stale/lossy digest
> can never cause a bad deploy; it only informs *what to propose*.

---

## 2. Maintained by deterministic Python (in `VaultWriter`), never by an LLM

Wire digest updates into the existing `vault/writer.py` write paths so the digest is a
**materialized view** updated atomically with every vault write. No LLM edits it (keeps the
system's spine: Python owns state, the LLM is a stateless step; the digest can't rot).

| Vault write (existing `VaultWriter` method) | Digest update |
|---|---|
| `write_strategy_note(status="forward-test", …)` | add/update an `active:` entry (id, family, deployed_symbols, note pointer) |
| `write_strategy_note(graveyard=True, …)` (rejection) | **merge** into a `rejected:` bucket by novelty key (§3.1): `tried += 1`, update `last`, keep ≤3 example pointers, set/keep the one-line `lesson` |
| status → retired / `decay.py` retire | remove from `active:`; optionally note in `rejected:` with reason "decayed" |
| `write_daily_note(…)` | refresh `perf:` rolling window + `coverage:` recompute |
| universe refresh (`universe.py`) | refresh `universe:` + recompute `coverage:` |

Implementation: a `vault/digest.py` module (`update_active`, `merge_rejected`,
`refresh_perf`, `refresh_coverage`, `rebuild_from_vault`) called by `VaultWriter`. Writes are
**idempotent** (re-applying the same note doesn't double-count) so the periodic rebuild (§5)
is safe.

> If you want an LLM to distill *why* a strategy died into the one-line `lesson`, do it
> **once** at rejection time (a single bounded call, or just lift the reason from the note
> frontmatter) and store the result. **Never** re-summarize on read.

---

## 3. Bounded by ROLLUP, not append-only (the make-or-break rule)

A "single file" that gains a line per note just relocates the bloat. Updates **merge into
buckets**:

- ❌ append: `s047 rsi-banks rejected`, `s048 rsi-banks rejected`, … (grows forever)
- ✅ merge: `key: mean-reversion|rsi_below+rsi_above|banks → tried 48×, lesson …`

`active:` is naturally bounded (≤8). `perf:` is a rolling window. `regime:`/`universe:` are
current-only. The **only** unbounded source is `rejected:`, which §3.1 bounds by keying.

### 3.1 The dedup key must match the researcher's *novelty* granularity

This is the fidelity crux. Bucket by **family + predicate-structure + symbol-target**, e.g.
`"mean-reversion|rsi_below+rsi_above|banks"` — derived deterministically from the rejected
spec (sorted predicate names + family + the symbols it was tried on). Rationale:

- **Too coarse** (e.g. bucket by family only) → silently suppresses a genuinely *new* variant
  in that family. This is the dangerous failure (§ failure asymmetry below).
- **Too fine** (e.g. every param value distinct) → the digest bloats and dedup does nothing.

The key is computed by the **same logic the researcher would use to judge "is this idea
new?"** Keep it in `agent/strategy_spec.py` (e.g. `novelty_key(spec, symbols)`) so the
researcher's novelty check and the graveyard's dedup use one shared definition.

### 3.2 Failure asymmetry — phrase the graveyard INFORMATIONALLY

Lossy compaction fails two ways, **not** equally dangerous:

- **Re-proposing a dead idea** (digest under-informed) → *harmless*: the deterministic gate
  re-backtests and rejects it again. Cost = one wasted cycle.
- **Suppressing a good idea** (digest says "family X is hopeless") → *dangerous, and the gate
  CANNOT catch it* (it only filters what's proposed, never surfaces what wasn't).

⇒ Store `lesson` as **facts** ("failed OOS on banks 2024–25") not **verdicts** ("RSI never
works"). Keep example note pointers so the researcher can drill in. Bias the whole `rejected:`
section toward *informing*, never *forbidding*.

---

## 4. Token-budget assertion (tested)

`vault/digest.py` measures the rendered digest size and **asserts ≤ `DIGEST_TOKEN_CAP`**
(e.g. a few thousand tokens — pick a value far below the context window). If a section would
exceed, compact harder in priority order: shrink `rejected:` lessons → drop oldest rejected
buckets to "(N older families omitted — see Graveyard/)" with a pointer → never silently
truncate (log what was dropped). A unit test builds a digest from a large synthetic vault and
asserts the cap holds. **This is the structural guarantee that prompt size stays flat as the
vault grows.**

---

## 5. Notes stay truth → periodic full rebuild (self-healing)

Incremental-on-write is fast but can drift (a missed update, or a human editing notes
directly in Obsidian, bypassing `VaultWriter`). So:

- `rebuild_from_vault()` rescans all notes and regenerates the digest from scratch.
- Run it in the **weekly-deep** researcher cadence, and on demand.
- **Rebuild-and-diff:** diff the rebuilt digest against the incremental one; divergence beyond
  tolerance → write a `system-alert` note (something is mis-maintaining the digest).

Incremental + periodic-rebuild = the standard materialized-view recipe: fast normally,
self-healing periodically.

---

## 6. Verifying fidelity (the "are we delivering everything the researcher needs?" checks)

Don't assert fidelity — measure it:

1. **Invariant tests (CI):** every `active:` entry has a live forward-test note; `coverage`
   reconciles with the union of `deployed_symbols` across notes; `rejected` counts are
   monotonic; budget ≤ cap.
2. **Completeness-critic (periodic, LLM-as-auditor):** occasionally (weekly-deep, NOT every
   run) a *separate stateless* `claude -p` compares the digest against a sample of raw notes
   and answers one question — *"is anything decision-relevant missing or misrepresented?"*
   Findings adjust the schema. One shot, discarded.
3. **Outcome telemetry (empirical loop):** log how often the researcher (a) re-proposes an
   already-rejected idea → `rejected:` is under-informing; (b) proposes a near-duplicate of an
   `active:` strategy → `active:` is under-informing. Rising rates ⇒ expand that section. The
   system tells you where compaction is too aggressive.

---

## 7. Allocate fidelity by decision-sensitivity

Spend the budget where the decision is most sensitive, not uniformly:

- **Uncovered symbols** (priority targets): full list; optionally per-symbol "what's been
  tried here" so the researcher tailors theses — these are where new edge must come from.
- **Active strategies** (few, high dedup/diversify value): fuller detail + pointers.
- **Graveyard**: most compressed — but novelty-keyed (§3.1) + informational (§3.2) + pointers.
- **Regime/perf**: one compact line / rolling window each.

---

## 8. Integration & build checklist (Windows)

- [ ] `agent/strategy_spec.py::novelty_key(spec, symbols)` — shared novelty/dedup key (§3.1)
- [ ] `vault/digest.py` — `update_active`, `merge_rejected`, `refresh_perf`,
      `refresh_coverage`, `rebuild_from_vault`, `render`/`measure` (idempotent updates)
- [ ] Wire `vault/writer.py` write paths to call the digest updaters atomically (§2)
- [ ] `RESEARCHER-SPEC.md` §8: researcher reads **only** `_context/RESEARCH-DIGEST.md` for
      proposal context (replaces summarize-on-read); load-bearing reads still hit the notes
- [ ] `DIGEST_TOKEN_CAP` + the budget-cap unit test (§4)
- [ ] Weekly-deep: `rebuild_from_vault()` + rebuild-and-diff → `system-alert` on drift (§5)
- [ ] Fidelity checks: invariant tests (§6.1); completeness-critic wired into weekly-deep
      (§6.2); re-proposal/duplication telemetry in `scripts/researcher.py` logs (§6.3)
- [ ] Tests + `/qa` (honest report)

---

## 9. Safety backstop (why this is safe even when lossy)

The digest is an **efficiency** optimization; its failures are efficiency failures, never
safety failures:
- A lossy digest can make the researcher waste a cycle (re-propose → gate rejects) or, if
  mis-keyed, miss an idea (guarded by §3.1/§3.2 + §6).
- It can **never** cause a bad deploy: the deterministic gate and `registry.load_active_specs`
  read the real notes, not the digest. forward-test ceiling, per-symbol deploy, overfit gate —
  all unchanged. The digest only shapes *what gets proposed*, which the gate then filters.
