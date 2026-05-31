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
from agent.strategy_spec import validate_spec, SpecError, MAX_PARAMS
from backtest.research import evaluate_spec
from backtest.optimize import (
    optimize_strategy, is_mediocre, MAX_VARIANTS_PER_STRATEGY,
)
from vault.writer import VaultWriter

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
    messages: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (f"researcher[{self.cadence}]: proposed={self.proposed} valid={self.valid} "
                f"deployed={self.deployed} improved={self.improved} rejected={self.rejected} "
                f"coverage {self.coverage_before}->{self.coverage_after}")


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
                   regime: str = "n/a") -> ResearcherSummary:
    """One researcher run. `proposer(ctx, max_proposals) -> list[spec]` is injected so the
    orchestration is testable without shelling out to claude."""
    eval_kwargs = eval_kwargs or {}
    weekly = cadence == "weekly"
    s = ResearcherSummary(cadence=cadence)

    active = registry.load_active_specs()
    cov = registry.coverage(active, universe)
    s.coverage_before = len(cov.covered)
    s.uncovered_before = list(cov.uncovered)
    s.messages.append(f"{len(active)} active forward-test(s); "
                      f"{len(cov.uncovered)} uncovered: {cov.uncovered}")

    # 1) improve mediocre incumbents — WEEKLY only (improvement is expensive + noisy) ------
    if weekly:
        for a in active:
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

    # 2) propose for UNCOVERED symbols (priority target) ----------------------------------
    active = registry.load_active_specs()   # re-load (improvements may have changed things)
    if len(active) >= MAX_ACTIVE_FORWARD_TESTS:
        s.messages.append(f"at active cap ({MAX_ACTIVE_FORWARD_TESTS}) — no new proposals this run")
        cov_after = registry.coverage(active, universe)
        s.coverage_after = len(cov_after.covered)
        s.note_rel = _write_research_note(registry.vault, s, regime)
        return s

    budget = DAILY_LIGHT_PROPOSALS if not weekly else MAX_PROPOSALS_PER_RUN
    remaining_slots = MAX_ACTIVE_FORWARD_TESTS - len(active)
    max_props = min(budget, remaining_slots)

    ctx = {
        "MODE": "propose", "UNIVERSE": universe,
        "UNCOVERED_SYMBOLS": cov.uncovered or "(none — all covered; diversify instead)",
        "ACTIVE_STRATEGIES": [{"id": a.spec.get("id"), "deployed": a.deployed_symbols,
                               "families": a.spec.get("families")} for a in active],
        "GRAVEYARD": sorted(registry.graveyard_ids()),
        "PARENT_SPEC": "", "REGIME": regime, "CADENCE": cadence,
    }
    proposals = proposer(ctx, max_props) or []
    if len(proposals) > max_props:
        s.messages.append(f"proposer returned {len(proposals)}; capped to {max_props} "
                          f"(dropped {len(proposals) - max_props})")
        proposals = proposals[:max_props]
    s.proposed = len(proposals)

    used_ids = registry.existing_ids()
    for spec in proposals:
        # assign/repair id (never collide with an existing one)
        sid = spec.get("id")
        if not sid or sid in used_ids:
            sid = registry.next_strategy_id()
        spec["id"] = sid
        used_ids.add(sid)

        # validate (param ceiling MAX_PARAMS enforced inside validate_spec)
        try:
            validate_spec(spec)
        except SpecError as e:
            s.messages.append(f"proposal {sid} invalid, dropped: {e}")
            s.rejected += 1
            continue
        s.valid += 1

        # per-symbol gate (deterministic, the ONLY path to deployment)
        verdict = evaluate_spec(spec, frames, **eval_kwargs)
        if verdict.passed and len(active) + len(s.deployed) < MAX_ACTIVE_FORWARD_TESTS:
            registry.write_spec_note(verdict, spec, status="forward-test",
                                     created=str(date.today()), thesis=spec.get("thesis", ""))
            s.deployed.append(sid)
            s.messages.append(f"deployed {sid} on {verdict.deployed_symbols}")
        else:
            # rejected (or at cap) → graveyard so it isn't re-proposed
            reason = "no profitable symbol OOS" if not verdict.passed else "active cap reached"
            registry.write_spec_note(verdict, spec, status="rejected",
                                     created=str(date.today()), graveyard=True,
                                     thesis=spec.get("thesis", ""))
            s.rejected += 1
            s.messages.append(f"graveyard {sid}: {reason}")

    cov_after = registry.coverage(registry.load_active_specs(), universe)
    s.coverage_after = len(cov_after.covered)
    s.note_rel = _write_research_note(registry.vault, s, regime)
    return s


def _write_research_note(vault: VaultWriter, s: ResearcherSummary, regime: str) -> str:
    d = str(date.today())
    rel = f"Research/{d}-researcher-{s.cadence}.md"
    fm = {"type": "research", "date": d, "cadence": s.cadence,
          "proposed": s.proposed, "valid": s.valid, "deployed": s.deployed,
          "improved": s.improved, "rejected": s.rejected,
          "coverage_before": s.coverage_before, "coverage_after": s.coverage_after,
          "tags": ["research", "researcher"]}
    body = (
        f"## Researcher run ({s.cadence})\n{s.line()}\n\n"
        f"Regime: {regime}\n\n"
        f"Uncovered at start ({len(s.uncovered_before)}): {s.uncovered_before}\n\n"
        "## Log\n" + "\n".join(f"- {m}" for m in s.messages) + "\n"
    )
    vault.write_note(rel, fm, body)
    return rel


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
    summary = run_researcher(registry=registry, universe=universe, frames=frames,
                             proposer=proposer, cadence=cadence)
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
