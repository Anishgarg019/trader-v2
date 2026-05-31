# RESEARCHER-SPEC.md — Phase 11: the Autonomous Researcher (build spec)

> **Authored on Mac (architect session), 2026-05-31.** This is the durable, build-ready
> version of the Phase 11 spec that previously lived only in chat / prose in
> `SESSION-HANDOFF.md`. **Build it on Windows** (primary dev box). Grounded against the
> real code in this repo (signal signatures, `run_backtest`, `overfit_report`,
> `write_strategy_note`, `Orchestrator._market`'s `strategy_fn(ctx)` contract). The runtime
> rulebook (`00 - Trading Agent Spec.md`) still wins on any behavior conflict.

---

## 0. One-paragraph goal

A scheduled, **headless Claude** ("research desk") that invents candidate trading
strategies and emits each one as a **constrained JSON DSL** (`spec:`) over the existing
`agent/signals` library. **Deterministic Python** compiles each spec to boolean entry/exit
series, backtests it **per stock** with real costs, splits IS/OOS, and runs the **overfit
gate**. A spec auto-deploys to **`forward-test`** (paper only) **on just the stocks it's
profitable on** (`deployed_symbols`); uncovered stocks get new strategies; mediocre ones get
a guarded improvement loop. **`forward-test` is the ceiling — there is no `live` path.** The
vault's strategy notes (with the `spec:` + `deployed_symbols:` frontmatter) are the
**registry** the live loop reads. **The execution path only ever runs the compiled DSL —
never code the LLM writes, never LLM arithmetic in the safety core.**

---

## 1. Non-negotiable safety invariants (do not relax)

1. **No LLM code on the execution path.** Claude outputs *data* (a JSON spec). Python
   compiles it. If a spec fails schema/whitelist validation → reject, never execute.
2. **The overfit gate is code, not judgment.** `backtest/research.py` decides pass/fail
   via `overfit_report(...)`. Claude cannot bypass, soften, or argue past it.
3. **Auto-deploy stops at `forward-test`. `live` is NOT built yet — do not wire it in**
   (user decision 2026-05-31). The researcher writes at most `status: forward-test`. No
   promotion path, no `promote.py`, no `live`-handling in the loop. The `live` status stays
   reserved in the spec for later; nothing reads or writes it until a human asks for it.
4. **Paper guard holds.** Everything routes through `PaperBroker`; `kite_client` has no
   order methods (CLAUDE.md §2). The researcher touches read-only data + the vault + the
   paper book only. Never place a real order.
5. **Caps are enforced in Python, not the prompt** (§8): max active forward-tests, max
   proposals per run, per-run token budget, param-count ceiling.
6. **Fail safe, not silent.** Any compile error, gate exception, or ambiguity → skip that
   candidate, log it, and (for systemic failures) write a `system-alert` note. Never
   deploy on uncertainty.
7. **Optimization must not become curve-fitting (user decision 2026-05-31).** The
   "keep improving" loop (§8.5) tunes params on the **in-sample** segment and accepts a
   variant ONLY if it beats the incumbent on the **out-of-sample** segment by a meaningful
   margin AND still clears every hard gate AND adds no knobs (5-param ceiling holds).
   Variant count per strategy per run is capped and every variant tried is logged (no
   silent search — each extra trial inflates false-positive risk). IS-only improvement is
   never a reason to deploy.
8. **Deployment is per (strategy × symbol).** A strategy trades ONLY the symbols it was
   gate-proven profitable on; it is never applied to symbols it lost on. The paper
   forward-test record is the final arbiter — a strategy that survives backtest but bleeds
   in paper gets retired by `agent/decay.py`.

---

## 2. Components & file map (what to build)

| # | File | Responsibility |
|---|------|----------------|
| A | `agent/strategy_spec.py` | DSL schema + validator (whitelist, param bounds, structural rules). Pure, no I/O. |
| B | `agent/strategy_compiler.py` | `compile_spec(spec) → CompiledStrategy` with `entries(df)`/`exits(df)` boolean Series **and** a live `strategy_fn(ctx)` factory. Uses ONLY `agent/signals`. |
| C | `backtest/research.py` | `evaluate_spec(spec, frames, ...)` = compile → **per-symbol** (`run_backtest`→`train_test_split`→`overfit_report`) → `ResearchVerdict` with `deployed_symbols`. The mandatory gate. |
| C2 | `backtest/optimize.py` | Improvement loop (§8.5): generate capped variants of a mediocre incumbent, re-gate each, accept only on OOS-margin improvement with no extra knobs. |
| D | `agent/registry.py` | Read/write specs + `deployed_symbols` to/from vault note frontmatter; `load_active_specs` (forward-test only); `coverage()` (covered/uncovered symbols). |
| E | `scripts/researcher.py` | One run: build context+coverage → improve incumbents (§8.5) → propose for uncovered symbols → validate → per-symbol evaluate → deploy passers to `forward-test` → write notes → enforce caps. Task Scheduler target. |
| F | `prompts/research_desk_system.md` | The headless-Claude "constitution" (✅ drafted alongside this file). |
| G | wiring in `scripts/run_loop.py` | Replace `strategy_fn=None` with a registry-backed `strategy_fn` built from active compiled specs. |
| H | `tests/test_strategy_spec.py`, `tests/test_strategy_compiler.py`, `tests/test_research.py`, `tests/test_registry.py` | Unit tests; then `/qa`. |

Build order: **A → B → C → D → (re-express s001 as a spec, §9) → G → E → C2 (optimizer) → scheduler → tests → /qa.**

---

## 3. The Strategy Spec DSL (grounded in `agent/signals`)

A spec is JSON. It is **declarative data**, validated against a whitelist before anything runs.

### 3.1 Top-level shape

```json
{
  "id": "s003",
  "name": "Trend Breakout",
  "families": ["trend", "breakout"],
  "timeframe": "day",
  "entry": { "all": [ <predicate>, ... ] },
  "exit":  { "any": [ <predicate>, ... ] },
  "atr_k": 2.5,
  "atr_len": 14,
  "size_fraction": 1.0
}
```

- `entry`, `exit` are **predicate trees** (§3.2). `timeframe` ∈ {`day`} for now (intraday
  later — minute history is lookback-capped per CLAUDE.md §5).
- `atr_k` / `atr_len` parameterize the **ATR stop** handled by `agent/risk.py` +
  `execution` (GTT) — **NOT** part of the predicate tree. The exit tree is the *signal*
  exit; the ATR stop is the *protective* exit. Both coexist (matches today's behavior).
- `size_fraction` ∈ (0, 1.0]; real sizing is still `agent/risk.py` (ATR R, 1× cash cap,
  per-name ≤20%, total open risk ≤15%). The spec never overrides the safety core.

### 3.2 Predicate trees

**Combinators** (any depth, but bounded — see §3.4):
- `{"all": [p1, p2, ...]}` — logical AND of child predicates (boolean Series & )
- `{"any": [p1, p2, ...]}` — logical OR
- `{"not": p}` — logical NOT

**Leaf predicates** — each maps to a whitelisted `agent/signals` call. Every leaf returns a
boolean Series aligned to `df`. **This table IS the whitelist** — anything not listed is a
hard reject.

| `pred` | Params (with bounds) | Maps to | Meaning (True when…) |
|--------|----------------------|---------|----------------------|
| `price_above_ma` | `length` 5–250, `kind` `sma`\|`ema` | `trend.price_above_ma(close, length, kind)` | close > MA |
| `price_below_ma` | `length` 5–250, `kind` | `~price_above_ma` | close < MA |
| `ma_cross_up` | `fast` 3–100, `slow` 5–250 (`fast<slow`) | `trend.cross_up(ema/sma(fast), ...(slow))` | fast MA crosses above slow |
| `ma_cross_down` | `fast`, `slow` | `trend.cross_down(...)` | fast crosses below slow |
| `adx_above` | `length` 5–50, `threshold` 10–60 | `trend.adx(df, length)['adx'] > threshold` | trend strength > thr |
| `rsi_below` | `length` 5–50, `threshold` 5–50 | `momentum.rsi(close, length) < threshold` | oversold |
| `rsi_above` | `length` 5–50, `threshold` 50–95 | `momentum.rsi(...) > threshold` | overbought / momentum |
| `macd_cross_up` | `fast` 5–20, `slow` 15–40, `signal` 5–15 | `macd` line crosses signal | bullish MACD |
| `macd_cross_down` | same | `cross_down` | bearish MACD |
| `stoch_below` | `k_len`,`d_len`,`threshold` 5–30 | `momentum.stochastic` %K < thr | oversold |
| `stoch_above` | `threshold` 70–95 | %K > thr | overbought |
| `bullish_divergence` | `length` 10–60, osc=`rsi`\|`macd` | `momentum.bullish_divergence(close, osc)` | price LL, osc HL |
| `bearish_divergence` | same | `momentum.bearish_divergence(...)` | price HH, osc LH |
| `breakout_up` | `length` 5–100 | `structure.breakout_up(df, length)` | close > prior `length`-bar high |
| `breakout_down` | `length` 5–100 | `structure.breakout_down(df, length)` | close < prior `length`-bar low |
| `higher_highs` | `length` 5–100 | `structure.higher_highs(high, length)` | making HHs |
| `lower_lows` | `length` 5–100 | `structure.lower_lows(low, length)` | making LLs |
| `bollinger_break_up` | `length` 5–60, `k` 1.0–3.5 | close > `bollinger_bands(...).upper` | upper-band breakout |
| `bollinger_break_dn` | `length`, `k` | close < lower band | lower-band breakdown |
| `bollinger_squeeze` | `length`, `k`, `lookback` 10–120 | `volatility.bollinger_squeeze(...)` | volatility compression |
| `volume_spike` | `length` 5–60, `k` 1.2–4.0 | `volume.volume_spike(volume, length, k)` | volume > k×SMA(vol) |
| `volume_confirms` | `length` 5–60 | `volume.volume_confirms(volume, length)` | volume > SMA(vol) |
| `doji` | `body_frac` 0.05–0.2 | `patterns.doji(df, body_frac)` | doji candle |
| `hammer` | `body_frac` 0.2–0.5 | `patterns.hammer(df, body_frac)` | hammer |
| `bullish_engulfing` | — | `patterns.bullish_engulfing(df)` | bull engulf |
| `bearish_engulfing` | — | `patterns.bearish_engulfing(df)` | bear engulf |

> Extend the table only by adding a whitelisted entry mapped to a real, tested signal fn.
> Never let the compiler `eval`/`getattr` an arbitrary name from the spec — dispatch
> through an explicit `dict[str, handler]` so an unknown `pred` raises, not executes.

### 3.3 Validation rules (`agent/strategy_spec.py`)

Hard-reject (raise `SpecError`) if any of:
- unknown `pred` or combinator key; unknown param key for a pred;
- a param outside its bound, wrong type, or `fast >= slow` where ordered;
- `entry` or `exit` missing/empty; tree depth > 4 or total leaf count > 8 (§3.4);
- total tunable params (sum across leaves + `atr_k`) > **5** → this is the `n_params`
  fed to `overfit_report` and a HARD cap here (spec §6.4.2 fragility discipline);
- `timeframe` not in the allowed set; `atr_k` ∉ [0.5, 5.0]; `size_fraction` ∉ (0, 1.0].

### 3.4 Param-count discipline

`n_params` = count of *numeric thresholds/lengths that were chosen* (not structural). Keep
≤ 5. This both (a) is the `max_params` soft/hard input to the gate and (b) structurally
prevents the LLM from curve-fitting a 12-knob monster. Log the computed `n_params` on
every verdict.

---

## 4. Compiler contract (`agent/strategy_compiler.py`)

```python
@dataclass
class CompiledStrategy:
    spec: dict
    n_params: int
    def entries(self, df: pd.DataFrame) -> pd.Series   # bool, indexed like df
    def exits(self, df: pd.DataFrame) -> pd.Series      # bool, indexed like df
    def strategy_fn_factory(self, *, history_fn, deployed_symbols, atr_fn=None) -> Callable  # ctx -> [item dicts]

def compile_spec(spec: dict) -> CompiledStrategy   # validates first, then compiles
```

- `entries`/`exits` evaluate the predicate tree to a boolean Series — **exactly the
  `entries`/`exits` arguments `run_backtest` expects** (`backtest/engine.py:85`). No
  look-ahead: predicates use only data up to each bar; the engine fills `next_open`.
- `strategy_fn_factory` returns a callable matching the **live loop contract**
  (`agent/loop.py:147` `_market`): given `ctx = {equity, available_cash, governor,
  universe, price_fn, date}`, it iterates over **`deployed_symbols` only** (the gate-proven
  subset for this spec — NOT the whole universe; see §5/§6), pulls recent candles via
  `history_fn(symbol)`, evaluates `entries`/`exits` on the **latest** bar, and emits the
  item dicts the loop consumes:
  - enter → `{"action":"enter","symbol","exchange","strategy_id","strategy_link",
    "last_price","atr","k":atr_k,"justification","regime"}`
  - exit → `{"action":"exit","symbol","exchange","quantity","last_price",
    "trade_note_rel","entry_price"}`
  - `atr` comes from `volatility.atr(df, atr_len)` on the latest bar; `k` = `spec.atr_k`.
- The compiler imports **only** from `agent.signals.*` and `_common`. No network, no file
  I/O, no `eval`.

---

## 5. Research gate (`backtest/research.py`) — the mandatory checkpoint

```python
@dataclass
class SymbolVerdict:
    symbol: str
    passed: bool                # cleared the gate on THIS symbol
    is_metrics: dict
    oos_metrics: dict
    overfit: OverfitReport      # from backtest.validation
    notes: str

@dataclass
class ResearchVerdict:
    spec_id: str
    n_params: int
    per_symbol: dict[str, SymbolVerdict]   # one entry per universe symbol tested
    deployed_symbols: list[str]            # the subset that passed → what it will trade
    passed: bool                           # True iff deployed_symbols is non-empty
    notes: str

def evaluate_spec(spec, frames, *, split=0.7, cost_model=None, slippage_bps=5.0,
                  min_trades_oos=30, min_symbols=1) -> ResearchVerdict
    # frames: dict[symbol -> OHLCV DataFrame] for the whole universe
```

Flow (deterministic, no LLM) — **per-symbol gate & deployment (user decision 2026-05-31):**
1. `c = compile_spec(spec)` (raises → `passed=False`, empty `deployed_symbols`, note error).
2. For **each** `symbol, df` in `frames`:
   a. `is_df, oos_df = train_test_split(df, split)` (`validation.py:16`).
   b. each segment: `run_backtest(seg, c.entries(seg), c.exits(seg), cost_model=...,
      slippage_bps=..., product="CNC", exchange=...)` → `.metrics`.
   c. `report = overfit_report(is_metrics, oos_metrics, n_params=c.n_params,
      min_trades_oos=min_trades_oos)` (`validation.py:38`).
   d. `passed_on_symbol = (not report.rejected) and oos_metrics["total_return"] > 0`.
      → build a `SymbolVerdict`.
3. `deployed_symbols = [s for s,v in per_symbol.items() if v.passed]`.
4. `passed = len(deployed_symbols) >= min_symbols` (default 1 — **apply it only to the
   stocks where it's profitable**; the rest are left for other/new strategies, §8.5).
   If empty → reject the whole spec to the graveyard.
5. Record every symbol's IS/OOS metrics + pass flag in `per_symbol`/`notes` so the verdict
   is fully auditable (which stocks it won/lost on).

⚠️ **Multiple-comparisons caveat (do not hide it):** keeping the best-of-N symbols is itself
a fluke generator — a strategy that "passed" on 1 of 10 stocks may just be noise. The gate's
OOS requirement mitigates but does not eliminate this. The **paper forward-test record is the
final arbiter**: a single-symbol pass is low-confidence until live paper evidence accrues,
and `agent/decay.py` retires (spec × symbol) pairs that bleed in paper. `min_symbols` can be
raised (e.g. 2) if single-symbol passes prove too noisy — log the choice.

`evaluate_spec` is the ONLY path to deployment. `scripts/researcher.py` must call it and
honor the verdict verbatim.

---

## 6. Vault registry (`agent/registry.py`) — specs live in strategy-note frontmatter

The strategy note (`vault/writer.py:137 write_strategy_note`) already carries `params`,
`status`, `families`, `timeframe`, `backtest`. **Add a `spec:` block AND a
`deployed_symbols:` list** to the frontmatter (this is the §7.3 reconciliation TODO).
`deployed_symbols` is the per-symbol allowlist from the verdict — the stocks the strategy
is gate-proven on and the ONLY ones it trades. `registry.py`:

- `write_spec_note(verdict, spec, status="forward-test")` → `write_strategy_note(...,
  status=status, params=<flattened spec params>, backtest=<per-symbol oos/is summary>,
  frontmatter_extra={"spec": spec, "deployed_symbols": verdict.deployed_symbols})` plus a
  thesis/rules body generated from the spec, including the win/loss-by-symbol table.
- `load_active_specs()` → scan `Strategies/` (not graveyard), parse frontmatter, return
  validated specs whose `status == "forward-test"` **only** (`live` is not wired — §7),
  each paired with its `deployed_symbols`. **Re-validate on load** (`compile_spec`) — a
  hand-edited bad spec must be skipped + `system-alert`, never run.
- `coverage(active_specs, universe)` → `{covered: set, uncovered: list}` where `covered` =
  union of all `deployed_symbols`; `uncovered` = universe minus covered. Feeds §8.5.
- The loop's `strategy_fn` is built by compiling every active spec and calling
  `strategy_fn_factory(history_fn=..., deployed_symbols=<that spec's list>)`, then merging
  their item lists (§G wiring). A symbol may be covered by >1 strategy — dedupe entries by
  symbol (first/highest-confidence wins) so two specs don't both open the same name; risk
  caps downstream still apply as today.

**Status model (reconcile spec §7.3 to match):**
`researching → forward-test → retired/rejected`. `forward-test` = passed gate, trading
paper to gather live evidence. The researcher writes only up to `forward-test`. `live`
stays a reserved future status — **not built, not loaded, not written** (user decision
2026-05-31); revisit only when the user explicitly asks to promote something.

---

## 7. `live` promotion — NOT BUILT (user decision 2026-05-31)

There is **no `live` path in Phase 11.** Don't build a promotion mechanism, don't add a
`promote.py`, don't have the loop or registry read/write `status: live`. Everything the
researcher deploys stays `forward-test` (paper) indefinitely until a human revisits it.
`live` remains a reserved word in the status model for a future, human-requested feature —
when that day comes, decide the gating mechanism then. Until then: ignore it entirely.

---

## 8. Orchestration & caps (`scripts/researcher.py`)

One run:
1. **Build context**: current universe (`agent/universe.py`), active forward-tests + their
   `deployed_symbols` + recent paper P&L, **coverage** (`registry.coverage` →
   covered/uncovered symbols), the strategy graveyard (what's been rejected — don't
   repropose), recent market regime summary. Assemble into the research-desk prompt input.
2. **Improve incumbents first (§8.5)**: run the optimization pass on existing forward-tests
   that are profitable-but-mediocre before inventing anything new.
3. **Propose — targeted at uncovered symbols.** Invoke headless `claude -p` with
   `prompts/research_desk_system.md`; pass the **uncovered symbols** as the priority target
   ("these stocks have no profitable strategy — invent ones for them"). Ask for **≤ 5**
   candidate specs as JSON.
4. **Validate** each (`agent/strategy_spec.py`); drop invalid ones with a logged reason.
5. **Evaluate** each survivor (`backtest/research.py`) per-symbol on real Kite day-history
   across the universe → `deployed_symbols`.
6. **Deploy passers** (non-empty `deployed_symbols`) to `forward-test` via
   `registry.write_spec_note` — only up to the **active cap**.
7. **Log + (optional) ntfy** a summary (incl. coverage delta — which previously-uncovered
   symbols are now covered); write a research daily note.

**Caps (enforced in Python):**
- `MAX_ACTIVE_FORWARD_TESTS = 8` — if at cap, deploy nothing new this run (or only replace
  a decayed one via `agent/decay.py`).
- `MAX_PROPOSALS_PER_RUN = 5`.
- `MAX_PARAMS = 5` (also enforced in spec validation, §3.4).
- `MAX_VARIANTS_PER_STRATEGY = 6` and `IMPROVE_IF_OOS_SHARPE_BELOW` (improvement loop, §8.5).
- `MIN_SYMBOLS = 1` (per-symbol deploy floor, §5) and `IMPROVE_MARGIN` (OOS accept margin).
- **Token budget** per run (cap the `claude -p` calls, proposals + variants); abort
  gracefully if exceeded.
- **No silent truncation**: if caps drop candidates/variants, `log()` exactly what was dropped.

**Cadence (Windows Task Scheduler):**
- **Daily-light** ~16:15 IST (after close): review forward-tests, decay check
  (`agent/decay.py` → retire), coverage check, at most a small top-up of proposals.
- **Weekly-deep** Sunday: improvement pass (§8.5) + full proposal round (up to the caps),
  broader backtests.

---

## 8.5 Continuous improvement loop (`backtest/optimize.py`) — "keep improving"

> Implements your "some strategies may be profitable but not optimised, keep improving"
> directive — **without** turning into curve-fitting (invariant #7). Runs in the
> **weekly-deep** cadence only (improvement is expensive and noisy; don't do it daily).

**What it operates on:** active forward-tests that pass the gate but are **mediocre** —
e.g. positive but low OOS Sharpe/return, or profitable on few symbols. Define "mediocre"
with an explicit threshold (`IMPROVE_IF_OOS_SHARPE_BELOW`, tunable), not vibes.

**How a variant is generated (two allowed sources, both end at the deterministic gate):**
1. **LLM-proposed mutation** — ask the research desk for ≤ `MAX_VARIANTS_PER_STRATEGY`
   targeted tweaks of the parent spec (e.g. shift a threshold, swap one predicate, change
   `atr_k`). Output is a *spec* (data), never code.
2. **Deterministic local search** — perturb tunable knobs within their bounds (small grid /
   coordinate search). Reproducible; no tokens.

**Acceptance rule (this is the anti-overfit core — non-negotiable, invariant #7):**
```
accept variant V over incumbent I  ⟺
    compile(V) ok
  ∧ V.n_params ≤ I.n_params            (never add knobs to "improve")
  ∧ V passes every HARD gate flag      (overfit_report not rejected, per-symbol)
  ∧ V.oos_score ≥ I.oos_score + MARGIN  (improvement measured OUT-OF-SAMPLE only)
  ∧ V.deployed_symbols ⊇ a meaningful subset (not a lateral shuffle)
```
where `oos_score` is computed on the **out-of-sample** segment (params were tuned on IS).
For extra robustness prefer a **walk-forward** score (roll the IS/OOS split forward a few
times and require the median to improve) — this is the strongest defense against tuning to
one OOS window. `MARGIN > 0` so noise-sized "improvements" are rejected.

**Caps & honesty:**
- `MAX_VARIANTS_PER_STRATEGY` per run (default small, e.g. 6). Each variant evaluated is
  **logged** — every extra trial is a multiple-comparisons risk; no silent search.
- If no variant clears the acceptance rule, **keep the incumbent unchanged** (a non-result
  is the common, correct outcome — say so in the log).
- On accept: write the improved spec as a **new version** of the note (bump an internal
  `version`), move the old spec body to `## Status history` / lineage, and send clearly
  inferior variants to the graveyard so they're not re-proposed.
- Never let optimization touch the safety core (`agent/risk.py`, `governor.py`) or the
  cost model — only the strategy spec's entry/exit/atr_k.

**Pseudocode:**
```python
for strat in active_forward_tests:
    if not is_mediocre(strat): continue          # leave good/dead ones alone
    incumbent = ResearchVerdict for strat
    variants = propose_variants(strat.spec)      # LLM and/or local search, capped+logged
    best = incumbent
    for v in variants:
        verdict = evaluate_spec(v, frames, ...)   # SAME deterministic gate, per-symbol
        if accepts(verdict, best):                # OOS-margin + no extra knobs + hard gates
            best = verdict
    if best is not incumbent:
        registry.replace_with_new_version(strat, best)   # lineage + graveyard losers
    else:
        log(f"{strat.id}: no variant beat incumbent OOS — kept as-is")
```

---

## 9. Re-express s001 as a spec (the one seed example)

s001 is the only hand-written strategy to carry over — and only to **validate the DSL +
compiler against a known result**. There is **no s002**: the autonomous researcher invents
its own strategies (user decision 2026-05-31). Don't hand-build a second strategy.

**s001 — RSI mean-reversion** (currently the mislabeled forward-test; re-express, keep
`status: forward-test`):
```json
{ "id":"s001","name":"RSI Mean-Reversion","families":["mean-reversion","momentum"],
  "timeframe":"day",
  "entry":{"all":[{"pred":"rsi_below","length":14,"threshold":30}]},
  "exit":{"any":[{"pred":"rsi_above","length":14,"threshold":50}]},
  "atr_k":2.5,"atr_len":14,"size_fraction":1.0 }
```
n_params = 3 (rsi_len, two thresholds) + atr_k = 4. (Backtest still rejects it 0/10 OOS —
that's expected; it stays `forward-test` as pipeline proof, not edge.) Use it to confirm
the compiler reproduces the existing `build_s001_strategy_fn` behavior, then retire the
hand-written version in favor of the spec.

### Param-counting rule (`count_params`) — applies to all researcher specs

Count only **deliberately-tunable knobs** — thresholds and `atr_k` the researcher is free
to move. Treat conventional indicator **lengths** (e.g. 14, 20, 50, 200) as fixed
structure, excluded from `n_params`. Encode this deterministically in
`agent/strategy_spec.py::count_params` so it's not interpretation. This keeps the 5-knob
overfit ceiling meaningful (it bites on tuned thresholds, not on standard window lengths).

---

## 10. Test plan (then `/qa`)

- `test_strategy_spec.py`: every whitelist pred validates; each bound rejects out-of-range;
  unknown pred/combinator rejects; depth/leaf/param caps reject; s001 spec validates.
- `test_strategy_compiler.py`: each pred compiles to the *same* Series as calling the
  signal fn directly (re-derivation); `all`/`any`/`not` truth tables; no look-ahead
  (entries at bar i use only ≤ i); `strategy_fn_factory` emits well-formed loop item dicts
  **and only for `deployed_symbols`** (never a non-deployed name).
- `test_research.py`: `evaluate_spec` returns `passed=False` for a known-overfit spec
  (reuse the Phase 4 overfit example); **per-symbol** — a spec that wins on symbol A and
  loses on symbol B yields `deployed_symbols == [A]`; empty deployed set → whole spec
  rejected; compile error → `passed=False`, not an exception escaping.
- `test_optimize.py`: a variant that improves **IS only** is **rejected**; a variant that
  improves OOS by ≥ `IMPROVE_MARGIN` with no extra knobs is accepted; a variant adding a
  knob is rejected even if OOS improves; "no variant beats incumbent" leaves it unchanged;
  variant count is capped and logged. (This test IS the curve-fitting guard — make it strict.)
- `test_registry.py`: spec + `deployed_symbols` round-trip through note frontmatter; bad/
  hand-edited spec is skipped on load + emits `system-alert`; `load_active_specs` filters by
  status; `coverage()` returns the right uncovered set.
- Wiring smoke test: `run_loop` with a registry of one trivial spec (one deployed symbol)
  builds a `strategy_fn`, emits entries only for that symbol, and walks a day without
  placing a real order (PaperBroker only).
- Then run **`/qa`** (standing rule, CLAUDE.md §6) — honest report, no green-unverified.

---

## 11. Build checklist (copy into Windows session)

- [ ] A `agent/strategy_spec.py` (schema + validator + `count_params`)
- [ ] B `agent/strategy_compiler.py` (entries/exits + `strategy_fn_factory`)
- [ ] C `backtest/research.py` (`evaluate_spec` = compile→**per-symbol** backtest→split→gate→`deployed_symbols`)
- [ ] D `agent/registry.py` (`spec:` + `deployed_symbols` frontmatter; `load_active_specs` forward-test only; `coverage()`)
- [ ] Re-express s001 as a spec (keep `forward-test`); confirm it matches the hand-written version, then retire the hand-written one. **No s002 — researcher invents its own.**
- [ ] G wire `scripts/run_loop.py` `strategy_fn` from registry (per-spec `deployed_symbols`; was `None`)
- [ ] E `scripts/researcher.py` + coverage-targeted proposals + caps (§8)
- [ ] C2 `backtest/optimize.py` improvement loop (§8.5) — OOS-margin acceptance, no extra knobs
- [ ] F drop `prompts/research_desk_system.md` in place (already drafted)
- [ ] Task Scheduler: daily ~16:15 + weekly Sun (improvement pass weekly only)
- [ ] Tests (§10, incl. `test_optimize.py` curve-fitting guard) + `/qa`
- [ ] Reconcile spec §7.3: add `forward-test` status + `spec:` frontmatter block (leave `live` reserved/unwired)
- [ ] Fix s001 note `status: live → forward-test`, then **commit + push from Windows**

---

## 12. Resolved decisions (user, 2026-05-31)

1. **`live` (§7): NOT built, not wired.** Researcher deploys to `forward-test` only;
   nothing reads/writes `live` until the user asks.
2. **No s002 (§9):** the autonomous researcher invents its own strategies. s001 is the
   only seed, used purely to validate the DSL/compiler.
3. **Param-count rule (§9):** count only deliberately-tunable knobs (thresholds, `atr_k`);
   conventional lengths are fixed structure, excluded from `n_params`.
4. **Per-symbol deployment (§5), supersedes the old universe-pass-fraction rule:** backtest
   each strategy on every stock independently; deploy it **only on the stocks it's profitable
   on** (`deployed_symbols`), never on the ones it lost on. Empty set → whole spec rejected.
   **`MIN_SYMBOLS = 1` (locked, user 2026-05-31)** — deploy on even a single profitable stock;
   paper forward-test + decay monitor are the flukes backstop.
5. **Cover the rest (§8):** uncovered stocks (no profitable strategy) are the priority
   target the researcher invents new strategies for.
6. **Keep improving (§8.5) — guarded against curve-fitting (invariant #7):** mediocre
   incumbents get capped, logged variants; a variant is accepted ONLY if it beats the
   incumbent **out-of-sample** by `IMPROVE_MARGIN`, clears every hard gate, and adds no
   knobs. IS-only improvement never deploys. Weekly cadence only.
