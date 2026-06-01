"""Strategy registry (Phase 11, RESEARCHER-SPEC §6) — specs live in note frontmatter.

The vault's strategy notes ARE the registry. Each Phase-11 note carries a `spec:` block
(the JSON DSL) and a `deployed_symbols:` list (the per-symbol allowlist the gate proved).
This module reads/writes that, loads the active forward-tests for the live loop, and reports
coverage (which universe symbols a profitable strategy already trades vs which are
uncovered).

SAFETY:
  - `load_active_specs` returns ONLY `status: forward-test` specs. `live` is never loaded,
    never written (invariant #3). It re-validates every spec on load (`compile_spec`); a
    hand-edited bad spec is SKIPPED and raises a `system-alert`, never run (invariant #6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from agent.strategy_compiler import compile_spec
from agent.strategy_spec import SpecError
from vault.writer import VaultWriter

FORWARD_TEST = "forward-test"


@dataclass
class ActiveSpec:
    spec: dict
    deployed_symbols: list[str]
    note_rel: str
    status: str = FORWARD_TEST


@dataclass
class Coverage:
    covered: set[str] = field(default_factory=set)
    uncovered: list[str] = field(default_factory=list)


def flatten_params(spec: dict) -> dict[str, Any]:
    """Flatten a spec's tunable + structural params into a flat dict for the note's
    human-readable `params:` field (the authoritative copy is the `spec:` block)."""
    out: dict[str, Any] = {"atr_k": spec.get("atr_k"), "atr_len": spec.get("atr_len", 14),
                           "size_fraction": spec.get("size_fraction", 1.0)}
    counter: dict[str, int] = {}

    def walk(node: Any, where: str):
        if not isinstance(node, dict):
            return
        if "pred" in node:
            pred = node["pred"]
            counter[pred] = counter.get(pred, 0) + 1
            tag = f"{where}.{pred}" + (f"#{counter[pred]}" if counter[pred] > 1 else "")
            for k, v in node.items():
                if k != "pred":
                    out[f"{tag}.{k}"] = v
            return
        for key in ("all", "any", "not"):
            if key in node:
                children = node[key]
                children = children if isinstance(children, list) else [children]
                for c in children:
                    walk(c, where)

    walk(spec.get("entry", {}), "entry")
    walk(spec.get("exit", {}), "exit")
    return out


def _win_loss_table(verdict) -> str:
    rows = ["| symbol | deployed | OOS return | OOS trades | flags |",
            "| --- | :---: | ---: | ---: | --- |"]
    for name, v in verdict.per_symbol.items():
        ret = v.oos_metrics.get("total_return")
        trades = v.oos_metrics.get("trades")
        flags = ",".join(v.overfit.flags) if v.overfit else "error"
        # a symbol that errored / had too little data has empty metrics → render '-', don't crash
        ret_s = f"{ret:.4f}" if ret is not None else "-"
        trades_s = str(int(trades)) if trades is not None else "-"
        rows.append(f"| {name} | {'✅' if v.passed else '—'} | "
                    f"{ret_s} | {trades_s} | {flags or 'clean'} |")
    return "\n".join(rows)


def _backtest_summary(verdict) -> dict[str, Any]:
    deployed = [verdict.per_symbol[s] for s in verdict.deployed_symbols]
    if deployed:
        rets = [float(v.oos_metrics.get("total_return", 0.0)) for v in deployed]
        oos_ret = sum(rets) / len(rets)
        sharpes = [float(v.oos_metrics.get("sharpe_like")) for v in deployed
                   if v.oos_metrics.get("sharpe_like") == v.oos_metrics.get("sharpe_like")]
        sharpe = (sum(sharpes) / len(sharpes)) if sharpes else None
        trades = sum(int(v.oos_metrics.get("trades", 0)) for v in deployed)
    else:
        oos_ret = sharpe = None
        trades = 0
    return {
        "period_in_sample": None, "period_out_sample": None,
        "return_pct": round(float(oos_ret), 4) if oos_ret is not None else None,
        "max_dd_pct": None,
        "sharpe_like": round(float(sharpe), 4) if sharpe is not None else None,
        "win_rate": None, "trades": int(trades), "friction_modeled": True,
        "symbols_deployed": len(verdict.deployed_symbols),
        "symbols_tested": len(verdict.per_symbol),
    }


class StrategyRegistry:
    def __init__(self, vault: VaultWriter):
        self.vault = vault

    # ---- write ---------------------------------------------------------------
    def write_spec_note(self, verdict, spec: dict, *, status: str = FORWARD_TEST,
                        created: date | str = "", thesis: str = "",
                        graveyard: bool = False) -> str:
        """Persist a gated spec as a strategy note (the registry entry).

        `deployed_symbols` (the per-symbol allowlist from the verdict) and the full `spec:`
        block go into the frontmatter. Status caps at `forward-test` — never `live`.
        """
        if status == "live":
            raise ValueError("registry refuses status 'live' — forward-test is the ceiling "
                             "(RESEARCHER-SPEC §7).")
        spec_id = spec.get("id", verdict.spec_id)
        name = spec.get("name", spec_id)
        note_rel = self.vault.strategy_rel(spec_id, name, graveyard=graveyard)
        spec_with_loc = {**spec, "note_rel": note_rel}

        rules = (
            f"- Entry tree: `{spec.get('entry')}`\n"
            f"- Exit tree: `{spec.get('exit')}`\n"
            f"- Protective stop: {spec.get('atr_k')}×ATR({spec.get('atr_len', 14)}) "
            f"(risk engine, not in the exit tree)\n"
            f"- n_params (tunable knobs) = {verdict.n_params}\n"
            f"- Deployed on (gate-proven profitable): {verdict.deployed_symbols}"
        )
        conditions = (
            "Per-symbol deployment: trades ONLY the symbols above; never the ones it lost on. "
            "Multiple-comparisons caveat applies — paper forward-test + decay monitor are the "
            "final arbiters.\n\n### Win/loss by symbol\n" + _win_loss_table(verdict)
        )
        self.vault.write_strategy_note(
            strategy_id=spec_id, name=name, status=status,
            families=spec.get("families", []), timeframe=spec.get("timeframe", "day"),
            created=created, params=flatten_params(spec),
            backtest=_backtest_summary(verdict), decay_check="forward-test: watch paper fills",
            thesis=thesis or spec.get("thesis", ""), rules=rules, conditions=conditions,
            backtest_log=verdict.notes, graveyard=graveyard,
            frontmatter_extra={"spec": spec_with_loc,
                               "deployed_symbols": list(verdict.deployed_symbols),
                               # the full set the spec was gate-tested on — lets the digest
                               # bucket rejects by their true symbol-target (novelty key §3.1)
                               "tested_symbols": sorted(verdict.per_symbol.keys())},
        )
        return note_rel

    # ---- read ----------------------------------------------------------------
    def _strategy_note_paths(self):
        """Top-level Strategies/*.md (excludes Graveyard/)."""
        folder = self.vault.root / "Strategies"
        if not folder.exists():
            return []
        return sorted(p for p in folder.glob("*.md") if p.is_file())

    def load_active_specs(self) -> list[ActiveSpec]:
        """Return validated forward-test specs (live is never loaded — invariant #3).

        Re-validates each spec via `compile_spec`; a bad/hand-edited spec is skipped and a
        `system-alert` is written, never run.
        """
        active: list[ActiveSpec] = []
        for path in self._strategy_note_paths():
            rel = str(path.relative_to(self.vault.root)).replace("\\", "/")
            try:
                fm, _ = self.vault.read_note(rel)
            except Exception:  # noqa: BLE001
                continue
            if fm.get("type") != "strategy":
                continue
            if fm.get("status") != FORWARD_TEST:   # NEVER load 'live' or anything else
                continue
            spec = fm.get("spec")
            if not spec:
                continue  # not a Phase-11 spec note (e.g. legacy hand-written)
            try:
                compile_spec(spec)   # re-validate on load
            except SpecError as e:
                self.vault.write_system_alert(
                    d=str(date.today()), slug=f"bad-spec-{spec.get('id', 'unknown')}",
                    kind="invalid-spec",
                    detail=f"Spec in {rel} failed re-validation on load: {e}. Skipped (not run).")
                continue
            active.append(ActiveSpec(
                spec=spec, deployed_symbols=list(fm.get("deployed_symbols", [])),
                note_rel=rel, status=FORWARD_TEST))
        return active

    def coverage(self, active_specs: list[ActiveSpec], universe: list[str]) -> Coverage:
        """Which universe symbols a profitable strategy already trades (covered) vs not."""
        covered: set[str] = set()
        for a in active_specs:
            covered |= set(a.deployed_symbols)
        uncovered = [s for s in universe if s not in covered]
        return Coverage(covered=covered & set(universe), uncovered=uncovered)

    def existing_ids(self) -> set[str]:
        """All strategy ids already used (Strategies/ AND Graveyard/), for id assignment
        and so the researcher never re-proposes a known id."""
        ids: set[str] = set()
        folder = self.vault.root / "Strategies"
        if folder.exists():
            for path in folder.rglob("*.md"):
                rel = str(path.relative_to(self.vault.root)).replace("\\", "/")
                try:
                    fm, _ = self.vault.read_note(rel)
                except Exception:  # noqa: BLE001
                    continue
                if fm.get("type") == "strategy" and fm.get("id"):
                    ids.add(str(fm["id"]))
        return ids

    def next_strategy_id(self, *, prefix: str = "s") -> str:
        """Next free sNNN id (e.g. s002) given what's already in the vault."""
        used = self.existing_ids()
        n = 1
        while f"{prefix}{n:03d}" in used:
            n += 1
        return f"{prefix}{n:03d}"

    def graveyard_ids(self) -> set[str]:
        """Ids parked in Strategies/Graveyard (rejected — don't re-propose)."""
        ids: set[str] = set()
        folder = self.vault.root / "Strategies" / "Graveyard"
        if folder.exists():
            for path in folder.glob("*.md"):
                rel = str(path.relative_to(self.vault.root)).replace("\\", "/")
                try:
                    fm, _ = self.vault.read_note(rel)
                except Exception:  # noqa: BLE001
                    continue
                if fm.get("id"):
                    ids.add(str(fm["id"]))
        return ids
