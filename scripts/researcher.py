"""The autonomous researcher (Phase 11, RESEARCHER-SPEC §8) — Task Scheduler target.

ONE run, hands-off:
  build context + coverage → (weekly) improve mediocre incumbents → propose strategies for
  UNCOVERED symbols via headless `claude -p` (prompts/research_desk_system.md as the
  constitution) → validate → per-symbol gate (`backtest/research.evaluate_spec`) → deploy
  passers to `forward-test` via the registry → write a research note → log + ntfy summary.

SAFETY (RESEARCHER-SPEC §1):
  - The LLM only ever emits a JSON *spec* (data). Deterministic Python validates, compiles,
    backtests, and gates it. LLM output is NEVER executed.
  - The overfit gate (`evaluate_spec`) and the improvement-acceptance rule
    (`backtest/optimize.py`) are CODE — never bypassed or softened here.
  - The researcher writes at most `status: forward-test`. There is NO `live` path.
  - Caps are enforced in Python (below), not the prompt. No silent truncation — drops are
    logged.

  python scripts/researcher.py                 # daily-light (default)
  python scripts/researcher.py --weekly         # weekly-deep (full proposals + improvement)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from agent.config import load_settings, REPO_ROOT
from agent.logging_setup import get_logger
from agent.notify import send_ntfy, notify_failure
from agent.registry import StrategyRegistry
from agent.strategy_spec import validate_spec, SpecError, novelty_key
from backtest.research import evaluate_spec
from backtest.optimize import (
    optimize_strategy, is_mediocre, MAX_VARIANTS_PER_STRATEGY,
)
from vault.writer import VaultWriter
from vault.digest import ResearchDigest, digest_diff

log = get_logger()

# ---- caps (enforced in Python, RESEARCHER-SPEC §8) --------------------------
MAX_ACTIVE_FORWARD_TESTS = 8
MAX_PROPOSALS_PER_RUN = 5
DAILY_LIGHT_PROPOSALS = 2          # daily-light: at most a small top-up
PROMPT_PATH = REPO_ROOT / "prompts" / "research_desk_system.md"
LOOKBACK_DAYS = 900                # ~600 trading bars → room for 200-len indicators + OOS


@dataclass
class ResearcherSummary:
    cadence: str
    proposed: int = 0
    valid: int = 0
    deployed: list[str] = field(default_factory=list)
    rejected: int = 0
    improved: list[str] = field(default_factory=list)
    coverage_before: int = 0
    coverage_after: int = 0
    uncovered_before: list[str] = field(default_factory=list)
    note_rel: str | None = None
    # fidelity telemetry (CONTEXT-DIGEST-SPEC §6.3): the digest under-informing shows up here
    re_proposed_rejected: int = 0     # proposed an idea already in the graveyard rollup
    near_dup_active: int = 0          # proposed a near-duplicate of an active strategy
    digest_drift: int = 0             # incremental-vs-rebuild divergences (weekly)
    digest_tokens: int = 0            # measured digest size (must stay ≤ cap)
    messages: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (f"researcher[{self.cadence}]: proposed={self.proposed} valid={self.valid} "
                f"deployed={self.deployed} improved={self.improved} rejected={self.rejected} "
                f"coverage {self.coverage_before}->{self.coverage_after} "
                f"digest={self.digest_tokens}tok re-proposed={self.re_proposed_rejected} "
                f"near-dup={self.near_dup_active} drift={self.digest_drift}")


# ---- data -------------------------------------------------------------------
def fetch_frames(kite: Any, universe: list[str], *, lookback_days: int = LOOKBACK_DAYS
                 ) -> dict[str, pd.DataFrame]:
    """Daily OHLCV frames for the whole universe, keyed by the universe name (EXCH:SYMBOL)."""
    from agent.live_strategies import build_history_fn
    hist = build_history_fn(kite, lookback_days=lookback_days)
    frames: dict[str, pd.DataFrame] = {}
    for name in universe:
        sym = name.split(":", 1)[1] if ":" in name else name
        try:
            df = hist(sym)
        except Exception as e:  # noqa: BLE001
            log.warning("history fetch failed for %s: %s", name, e)
            df = None
        if df is not None and not df.empty:
            frames[name] = df
    return frames


# ---- headless research desk (claude -p) -------------------------------------
def _fill_prompt(template: str, ctx: dict) -> str:
    out = template
    for k, v in ctx.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def _extract_json_array(text: str) -> list[dict]:
    """Defensively pull the JSON array out of a model response (tolerate fences/prose)."""
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def call_research_desk(ctx: dict, *, max_proposals: int, model: str | None = None,
                       timeout: int = 240) -> list[dict]:
    """Invoke headless `claude -p` with the research-desk constitution; return parsed specs.

    The model's output is DATA (a JSON array of specs). It is validated + gated downstream;
    nothing it returns is executed.
    """
    if not PROMPT_PATH.exists():
        log.error("research-desk prompt missing: %s", PROMPT_PATH)
        return []
    system = PROMPT_PATH.read_text(encoding="utf-8")
    ctx = {**ctx, "MAX_PROPOSALS_PER_RUN": max_proposals}
    prompt = _fill_prompt(system, ctx) + "\n\nReturn the JSON array of specs now."
    cmd = ["claude", "-p", prompt]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.error("claude -p invocation failed: %s", e)
        return []
    if proc.returncode != 0:
        log.error("claude -p returned %d: %s", proc.returncode, proc.stderr[:500])
        return []
    specs = _extract_json_array(proc.stdout)
    return specs[:max_proposals]


# ---- orchestration (testable: inject `proposer` + `frames`) -----------------
def run_researcher(*, registry: StrategyRegistry, universe: list[str],
                   frames: dict[str, pd.DataFrame],
                   proposer: Callable[[dict, int], list[dict]],
                   cadence: str = "daily",
                   eval_kwargs: dict | None = None,
                   regime: str = "n/a",
                   critic: Callable[[str, str], str] | None = None) -> ResearcherSummary:
    """One researcher run. Proposal CONTEXT is read ONLY from the maintained digest
    (`_context/RESEARCH-DIGEST.md`) — so the prompt size is decoupled from vault size and no
    full-vault scan happens in the proposal path. LOAD-BEARING reads (the specs to compile/
    improve, id-uniqueness, the per-symbol gate, deployment) still hit the REAL notes via the
    registry — so a stale/lossy digest can only waste a cycle, never cause a bad deploy.

    `proposer(ctx, max_proposals) -> list[spec]` and `critic(digest_text, sample) -> findings`
    are injected so the orchestration is testable without shelling out to claude.
    """
    eval_kwargs = eval_kwargs or {}
    weekly = cadence == "weekly"
    s = ResearcherSummary(cadence=cadence)
    digest = ResearchDigest(registry.vault.root)

    # --- heal/bootstrap the digest (notes stay truth; §5) --------------------------------
    if weekly:
        incremental = digest.load()
        rebuilt = digest.rebuild_from_vault()       # authoritative regeneration
        drift = digest_diff(incremental, rebuilt) if incremental.get("generated") else []
        s.digest_drift = len(drift)
        if drift:
            registry.vault.write_system_alert(
                d=str(date.today()), slug="digest-drift", kind="digest-drift",
                detail="Incremental digest diverged from rebuild:\n- " + "\n- ".join(drift))
            s.messages.append(f"digest DRIFT detected ({len(drift)}) — rebuilt + alerted")
    elif (not digest.exists()) or (not digest.load().get("universe")):
        digest.rebuild_from_vault()                  # one-time bootstrap on a fresh digest
        s.messages.append("digest bootstrapped via rebuild_from_vault")

    # the run's `universe` is the authoritative current universe — keep the digest in sync so
    # coverage is computed against it (idempotent; a no-op when already matching)
    if universe and digest.load().get("universe") != list(universe):
        digest.set_universe(list(universe))

    # 1) improve mediocre incumbents — WEEKLY only (LOAD-BEARING: real specs from notes) ---
    if weekly:
        for a in registry.load_active_specs():
            try:
                verdict = evaluate_spec(a.spec, frames, **eval_kwargs)
            except Exception as e:  # noqa: BLE001
                log.warning("incumbent re-eval failed for %s: %s", a.spec.get("id"), e)
                continue
            if not is_mediocre(verdict):
                continue
            res = optimize_strategy(a.spec, frames, incumbent_verdict=verdict,
                                    max_variants=MAX_VARIANTS_PER_STRATEGY, eval_kwargs=eval_kwargs)
            s.messages.append(res.note)
            if res.accepted:
                registry.write_spec_note(res.best_verdict, res.best_spec,
                                         status="forward-test", created=str(date.today()),
                                         thesis=a.spec.get("thesis", ""))
                s.improved.append(res.best_spec.get("id", a.spec.get("id")))
    else:
        s.messages.append("daily-light: skipping improvement pass (weekly-deep only)")

    # --- proposal CONTEXT comes from the DIGEST ONLY (no full-vault scan) -----------------
    dd = digest.load()
    uncovered = dd["coverage"]["uncovered"]
    s.coverage_before = len(dd["coverage"]["covered"])
    s.uncovered_before = list(uncovered)
    s.digest_tokens = dd.get("budget_tokens") or digest.measure(dd)
    # novelty keys already in play, to detect re-proposals / near-dups (telemetry §6.3)
    rejected_keys = {b.get("key") for b in dd.get("rejected", []) if b.get("key")}
    active_keys = {a.get("key") for a in dd.get("active", []) if a.get("key")}
    s.messages.append(f"{len(dd.get('active', []))} active; {len(uncovered)} uncovered: "
                      f"{uncovered}; {len(dd.get('rejected', []))} rejected buckets "
                      f"(digest {s.digest_tokens} tok)")

    # 2) propose — cap is LOAD-BEARING (real active count) --------------------------------
    active = registry.load_active_specs()
    if len(active) >= MAX_ACTIVE_FORWARD_TESTS:
        s.messages.append(f"at active cap ({MAX_ACTIVE_FORWARD_TESTS}) — no new proposals this run")
        s.coverage_after = len(digest.load()["coverage"]["covered"])
        if weekly and critic is not None:
            _run_critic(registry, digest, critic, s)
        s.note_rel = _write_research_note(registry.vault, s, regime)
        return s

    budget = DAILY_LIGHT_PROPOSALS if not weekly else MAX_PROPOSALS_PER_RUN
    max_props = min(budget, MAX_ACTIVE_FORWARD_TESTS - len(active))

    ctx = {
        "MODE": "propose", "UNIVERSE": dd.get("universe") or universe,
        "UNCOVERED_SYMBOLS": uncovered or "(none — all covered; diversify instead)",
        "ACTIVE_STRATEGIES": [{"id": a.get("id"), "deployed": a.get("deployed_symbols"),
                               "families": a.get("family"), "recent": a.get("recent")}
                              for a in dd.get("active", [])],
        "GRAVEYARD": [{"key": b.get("key"), "tried": b.get("tried"), "lesson": b.get("lesson")}
                      for b in dd.get("rejected", [])],
        "PARENT_SPEC": "", "REGIME": dd.get("regime") or regime, "CADENCE": cadence,
    }
    proposals = proposer(ctx, max_props) or []
    if len(proposals) > max_props:
        s.messages.append(f"proposer returned {len(proposals)}; capped to {max_props} "
                          f"(dropped {len(proposals) - max_props})")
        proposals = proposals[:max_props]
    s.proposed = len(proposals)

    used_ids = registry.existing_ids()   # LOAD-BEARING: real id-uniqueness (never collide)
    for spec in proposals:
        sid = spec.get("id")
        if not sid or sid in used_ids:
            sid = registry.next_strategy_id()
        spec["id"] = sid
        used_ids.add(sid)

        try:
            validate_spec(spec)
        except SpecError as e:
            s.messages.append(f"proposal {sid} invalid, dropped: {e}")
            s.rejected += 1
            continue
        s.valid += 1

        # telemetry: is the digest under-informing? (re-proposed dead / near-dup of active)
        nk = novelty_key(spec, None)
        if any(k and k.startswith(nk.rsplit("|", 1)[0]) for k in rejected_keys):
            s.re_proposed_rejected += 1
            s.messages.append(f"telemetry: {sid} re-proposes a graveyard family ({nk}) "
                              "— rejected: under-informing")
        if any(k and k.rsplit("|", 1)[0] == nk.rsplit("|", 1)[0] for k in active_keys):
            s.near_dup_active += 1
            s.messages.append(f"telemetry: {sid} near-duplicates an active family ({nk}) "
                              "— active: under-informing")

        # per-symbol gate (deterministic, the ONLY path to deployment — real frames/notes)
        verdict = evaluate_spec(spec, frames, **eval_kwargs)
        if verdict.passed and len(active) + len(s.deployed) < MAX_ACTIVE_FORWARD_TESTS:
            registry.write_spec_note(verdict, spec, status="forward-test",
                                     created=str(date.today()), thesis=spec.get("thesis", ""))
            s.deployed.append(sid)
            s.messages.append(f"deployed {sid} on {verdict.deployed_symbols}")
        else:
            reason = "no profitable symbol OOS" if not verdict.passed else "active cap reached"
            registry.write_spec_note(verdict, spec, status="rejected",
                                     created=str(date.today()), graveyard=True,
                                     thesis=spec.get("thesis", ""))
            s.rejected += 1
            s.messages.append(f"graveyard {sid}: {reason}")

    # coverage_after from the digest (kept current incrementally by the deploy writes)
    s.coverage_after = len(digest.load()["coverage"]["covered"])
    if weekly and critic is not None:
        _run_critic(registry, digest, critic, s)
    s.note_rel = _write_research_note(registry.vault, s, regime)
    return s


def _run_critic(registry: StrategyRegistry, digest: ResearchDigest,
                critic: Callable[[str, str], str], s: ResearcherSummary) -> None:
    """Completeness-critic (§6.2): a SEPARATE stateless step compares the digest against a
    sample of raw notes and answers 'is anything decision-relevant missing or misrepresented?'
    Its finding is LOGGED only (it never edits the digest or gates anything)."""
    try:
        digest_text = digest.render(digest.load())
        sample = _sample_raw_notes(registry.vault, limit=5)
        finding = critic(digest_text, sample)
        if finding and finding.strip():
            s.messages.append(f"completeness-critic: {finding.strip()[:400]}")
    except Exception as e:  # noqa: BLE001 — auditor must never break the run
        log.warning("completeness-critic failed (non-fatal): %s", e)


def _sample_raw_notes(vault: VaultWriter, *, limit: int = 5) -> str:
    """A small sample of raw strategy/graveyard notes for the critic to compare against."""
    out: list[str] = []
    for sub in ("Strategies", "Strategies/Graveyard"):
        folder = vault.root / sub
        if not folder.exists():
            continue
        for p in sorted(folder.glob("*.md"))[:limit]:
            out.append(f"--- {p.name} ---\n" + p.read_text(encoding="utf-8")[:800])
    return "\n\n".join(out)


def _write_research_note(vault: VaultWriter, s: ResearcherSummary, regime: str) -> str:
    d = str(date.today())
    rel = f"Research/{d}-researcher-{s.cadence}.md"
    fm = {"type": "research", "date": d, "cadence": s.cadence,
          "proposed": s.proposed, "valid": s.valid, "deployed": s.deployed,
          "improved": s.improved, "rejected": s.rejected,
          "coverage_before": s.coverage_before, "coverage_after": s.coverage_after,
          "digest_tokens": s.digest_tokens, "re_proposed_rejected": s.re_proposed_rejected,
          "near_dup_active": s.near_dup_active, "digest_drift": s.digest_drift,
          "tags": ["research", "researcher"]}
    body = (
        f"## Researcher run ({s.cadence})\n{s.line()}\n\n"
        f"Regime: {regime}\n\n"
        f"Uncovered at start ({len(s.uncovered_before)}): {s.uncovered_before}\n\n"
        f"Context source: `_context/RESEARCH-DIGEST.md` ({s.digest_tokens} tokens) — "
        "proposal context read from the digest only; gate/registry still read real notes.\n\n"
        "## Log\n" + "\n".join(f"- {m}" for m in s.messages) + "\n"
    )
    vault.write_note(rel, fm, body)
    return rel


def completeness_critic_claude(digest_text: str, sample: str, *, model: str | None = None,
                               timeout: int = 180) -> str:
    """A SEPARATE stateless `claude -p` auditor (§6.2): compares the digest against a sample
    of raw notes and answers one question. Output is LOGGED only — never edits the digest,
    never gates anything. Returns '' on any failure (best-effort)."""
    prompt = (
        "You are a completeness auditor for a research digest. The digest is a compressed "
        "view of the vault's strategy/graveyard notes, used to decide what new strategies to "
        "propose. Compare the DIGEST against the SAMPLE of raw notes and answer ONE question "
        "in 1-3 sentences: is anything DECISION-RELEVANT missing or misrepresented in the "
        "digest (e.g. a rejected family not reflected, a coverage error)? If it looks faithful, "
        f"say 'faithful'.\n\n=== DIGEST ===\n{digest_text[:6000]}\n\n=== SAMPLE NOTES ===\n{sample[:6000]}"
    )
    cmd = ["claude", "-p", prompt]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def main(weekly: bool = False) -> int:
    settings = load_settings()
    if not settings.is_paper:
        log.error("MODE is not 'paper' — refusing to run.")
        return 2
    if not (settings.kite_api_key and settings.kite_access_token):
        log.error("Missing Kite token. Run scripts/kite_login.py first.")
        return 1

    from agent.broker.kite_client import KiteDataClient
    kite = KiteDataClient(api_key=settings.kite_api_key, access_token=settings.kite_access_token)
    vault = VaultWriter(settings.vault_path); vault.ensure_structure()
    registry = StrategyRegistry(vault)

    universe = []
    if vault.exists("Universe/current-universe.md"):
        fm_u, _ = vault.read_note("Universe/current-universe.md")
        universe = list(fm_u.get("names") or [])
    if not universe:
        log.error("no universe found — run scripts/select_universe.py first.")
        return 1

    log.info("fetching %d-symbol universe history (%d days)...", len(universe), LOOKBACK_DAYS)
    frames = fetch_frames(kite, universe)
    if not frames:
        log.error("no historical frames fetched — aborting (token expired?).")
        return 1

    model = os.environ.get("RESEARCHER_MODEL") or None

    def proposer(ctx: dict, max_props: int) -> list[dict]:
        return call_research_desk(ctx, max_proposals=max_props, model=model)

    cadence = "weekly" if weekly else "daily"
    # completeness-critic runs in weekly-deep ONLY (LLM-as-auditor, logged, never gates)
    critic = (lambda dt, sm: completeness_critic_claude(dt, sm, model=model)) if weekly else None
    summary = run_researcher(registry=registry, universe=universe, frames=frames,
                             proposer=proposer, cadence=cadence, critic=critic)
    log.info(summary.line())
    for m in summary.messages:
        log.info("  %s", m)
    send_ntfy(summary.line(), title=f"Researcher {cadence}", tags="test_tube")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(weekly="--weekly" in sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 — alert on any unhandled failure, then re-raise
        notify_failure("researcher", e)
        raise
