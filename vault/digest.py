"""Maintained research digest (CONTEXT-DIGEST-SPEC) — the researcher's decision inputs as a
single, bounded, Python-maintained materialized view over the vault.

`<VAULT>/_context/RESEARCH-DIGEST.md` carries EXACTLY the six inputs a proposal decision
consumes (universe, coverage, active, rejected-rollup, regime, perf), each entry pointing
back to its source note. The researcher reads ONLY this file for proposal context, so its
prompt size is decoupled from vault size.

SAFETY (the truth-vs-view boundary): the digest is an EFFICIENCY view, NEVER truth. The
strategy/daily notes remain the source of truth; the overfit gate and
`registry.load_active_specs` still read the real notes. A stale/lossy digest can only waste
a research cycle — it can never cause a bad deploy. It is maintained by deterministic Python
only; no LLM ever edits it.

Bounded by ROLLUP (CONTEXT-DIGEST-SPEC §3): `rejected:` merges into buckets keyed by
`novelty_key` (family + predicate-structure + symbol-target). Graveyard entries are
INFORMATIONAL facts/lessons, never prohibitive verdicts (§3.2 failure asymmetry).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from agent.logging_setup import get_logger
from agent.strategy_spec import novelty_key

log = get_logger()

DIGEST_REL = "_context/RESEARCH-DIGEST.md"
DIGEST_TOKEN_CAP = 3000          # ~chars/4; far below the context window — keeps prompt flat
MAX_REJECTED_EXAMPLES = 3        # ≤3 pointers per rejected bucket
_LESSON_CAP = 140                # chars, before budget compaction shrinks further


def _today() -> str:
    return date.today().isoformat()


def _bare(symbol: str) -> str:
    return symbol.split(":", 1)[1] if ":" in symbol else symbol


def _est_tokens(text: str) -> int:
    return (len(text) + 3) // 4


class ResearchDigest:
    """Read/maintain the digest file. Every public mutator is load→mutate→save (cheap; the
    file is small and bounded) so concurrent processes don't clobber each other, and so the
    periodic rebuild (which writes a fresh file) is always safe."""

    def __init__(self, vault_root: str | Path):
        self.root = Path(vault_root)
        self.path = self.root / "_context" / "RESEARCH-DIGEST.md"

    # ---- skeleton / io -------------------------------------------------------
    @staticmethod
    def blank() -> dict:
        return {
            "type": "research-digest",
            "generated": "", "rebuilt": "",
            "universe": [],
            "coverage": {"covered": [], "uncovered": []},
            "active": [],
            "rejected": [],
            "regime": "",
            "perf": {"window_days": 30, "equity_change_pct": None, "open_positions": 0},
            "budget_tokens": 0,
        }

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict:
        if not self.path.exists():
            return self.blank()
        text = self.path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return self.blank()
        parts = text.split("---", 2)
        try:
            fm = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            return self.blank()
        base = self.blank()
        base.update(fm)
        return base

    def save(self, data: dict) -> Path:
        """Enforce the token budget, stamp `generated`, render, and write. Returns the path."""
        data.setdefault("type", "research-digest")
        data["generated"] = _today()
        data = enforce_budget(data)                 # may drop oldest rejected buckets (logged)
        data["budget_tokens"] = self.measure(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.render(data), encoding="utf-8")
        return self.path

    # ---- incremental updates (idempotent, O(active)) -------------------------
    def update_active(self, *, spec: dict, deployed_symbols: list[str], note_rel: str,
                      recent: dict | None = None) -> dict:
        """Upsert a forward-test into `active:` (idempotent by id)."""
        data = self.load()
        entry = {
            "id": spec.get("id"),
            "family": list(spec.get("families") or []),
            "key": novelty_key(spec, deployed_symbols),   # for near-dup telemetry (§6.3)
            "deployed_symbols": list(deployed_symbols or []),
            "recent": recent or {"trades_30d": None, "win_rate_30d": None, "oos_sharpe": None},
            "note": note_rel,
        }
        data["active"] = [a for a in data["active"] if a.get("id") != entry["id"]]
        data["active"].append(entry)
        data["active"].sort(key=lambda a: str(a.get("id")))
        self._recompute_coverage(data)
        return self.save(data) and data

    def remove_active(self, spec_id: str) -> dict:
        data = self.load()
        data["active"] = [a for a in data["active"] if a.get("id") != spec_id]
        self._recompute_coverage(data)
        self.save(data)
        return data

    def merge_rejected(self, *, spec: dict, symbols: list[str] | None, note_rel: str,
                       lesson: str = "", when: str | None = None) -> dict:
        """Roll a rejection into its novelty bucket: tried += 1, keep ≤3 example pointers,
        keep one factual lesson. Idempotent on re-applying the same note (deduped on the
        note pointer). Graveyard entries are INFORMATIONAL — store facts, never 'never works'."""
        data = self.load()
        key = novelty_key(spec, symbols)
        when = when or _today()
        bucket = next((b for b in data["rejected"] if b.get("key") == key), None)
        if bucket is None:
            bucket = {"key": key, "tried": 0, "last": when, "lesson": "", "examples": []}
            data["rejected"].append(bucket)
        if note_rel in bucket["examples"]:
            return data                               # already counted this exact note → no-op
        bucket["tried"] += 1
        bucket["last"] = max(str(bucket.get("last") or ""), when) or when
        if lesson:
            bucket["lesson"] = lesson[:_LESSON_CAP]
        bucket["examples"].append(note_rel)
        if len(bucket["examples"]) > MAX_REJECTED_EXAMPLES:
            bucket["examples"] = bucket["examples"][-MAX_REJECTED_EXAMPLES:]
        data["rejected"].sort(key=lambda b: str(b.get("last") or ""), reverse=True)
        self.save(data)
        return data

    def set_universe(self, names: list[str]) -> dict:
        data = self.load()
        data["universe"] = list(names or [])
        self._recompute_coverage(data)
        self.save(data)
        return data

    def refresh_coverage(self) -> dict:
        data = self.load()
        self._recompute_coverage(data)
        self.save(data)
        return data

    def refresh_perf(self, *, window_days: int = 30, equity_change_pct: float | None = None,
                     open_positions: int | None = None) -> dict:
        """Refresh the rolling perf snapshot. `None` args preserve the existing value (so a
        caller that only knows the equity change doesn't clobber `open_positions`)."""
        data = self.load()
        prev = data.get("perf") or {}
        data["perf"] = {
            "window_days": window_days,
            "equity_change_pct": equity_change_pct if equity_change_pct is not None
            else prev.get("equity_change_pct"),
            "open_positions": open_positions if open_positions is not None
            else prev.get("open_positions", 0),
        }
        self.save(data)
        return data

    def set_regime(self, line: str) -> dict:
        data = self.load()
        data["regime"] = line or ""
        self.save(data)
        return data

    @staticmethod
    def _recompute_coverage(data: dict) -> None:
        universe = list(data.get("universe") or [])
        covered: set[str] = set()
        for a in data.get("active", []):
            covered |= set(a.get("deployed_symbols") or [])
        covered &= set(universe)
        data["coverage"] = {
            "covered": sorted(covered),
            "uncovered": [s for s in universe if s not in covered],
        }

    # ---- rebuild from vault (self-healing, §5) -------------------------------
    def rebuild_from_vault(self) -> dict:
        """Rescan all notes and regenerate the digest from a blank slate. Each note is applied
        exactly once, so the result is deterministic and idempotent (re-running yields the
        same digest). This is the authoritative correction for any incremental drift."""
        data = self.blank()
        data["rebuilt"] = _today()

        # universe
        uni = self._read_fm(self.root / "Universe" / "current-universe.md")
        if uni:
            data["universe"] = list(uni.get("names") or [])

        buckets: dict[str, dict] = {}

        def _into_rejected(fm: dict, rel: str, lesson: str) -> None:
            spec = fm["spec"]
            symbols = fm.get("tested_symbols") or fm.get("deployed_symbols") or []
            key = novelty_key(spec, symbols)
            when = str(fm.get("created") or "")
            b = buckets.get(key)
            if b is None:
                b = {"key": key, "tried": 0, "last": when, "lesson": "", "examples": []}
                buckets[key] = b
            b["tried"] += 1
            b["last"] = max(str(b["last"] or ""), when) or when
            if lesson:
                b["lesson"] = lesson[:_LESSON_CAP]
            if len(b["examples"]) < MAX_REJECTED_EXAMPLES:
                b["examples"].append(rel)

        # top-level Strategies/*.md: forward-test → active; retired/rejected → rejected rollup
        # (so a retired strategy's lesson is NOT lost — completeness-critic finding 2026-06-01).
        sdir = self.root / "Strategies"
        if sdir.exists():
            for p in sorted(sdir.glob("*.md")):
                fm = self._read_fm(p)
                if not fm or fm.get("type") != "strategy" or not fm.get("spec"):
                    continue
                status = fm.get("status")
                rel = self._rel(p)
                if status == "forward-test":
                    bt = fm.get("backtest") or {}
                    deployed = list(fm.get("deployed_symbols") or [])
                    data["active"].append({
                        "id": fm.get("id"), "family": list(fm.get("families") or []),
                        "key": novelty_key(fm["spec"], deployed),
                        "deployed_symbols": deployed,
                        "recent": {"trades_30d": None, "win_rate_30d": None,
                                   "oos_sharpe": bt.get("sharpe_like")},
                        "note": rel})
                elif status in ("retired", "rejected"):
                    _into_rejected(fm, rel, f"{status}: " + self._lesson_from_fm(fm))
            data["active"].sort(key=lambda a: str(a.get("id")))

        # Graveyard/*.md → rejected rollup (one pass → exact counts)
        gdir = self.root / "Strategies" / "Graveyard"
        if gdir.exists():
            for p in sorted(gdir.glob("*.md")):
                fm = self._read_fm(p)
                if fm and fm.get("spec"):
                    _into_rejected(fm, self._rel(p), self._lesson_from_fm(fm))

        data["rejected"] = sorted(buckets.values(), key=lambda b: str(b.get("last") or ""),
                                  reverse=True)
        self._recompute_coverage(data)
        self.save(data)
        return data

    @staticmethod
    def _lesson_from_fm(fm: dict) -> str:
        """Lift a FACTUAL one-liner from a graveyard note (never a 'never works' verdict)."""
        bt = fm.get("backtest") or {}
        dep, tested = bt.get("symbols_deployed"), bt.get("symbols_tested")
        if dep is not None and tested is not None:
            return f"failed gate: profitable on {dep}/{tested} symbols OOS"
        return "rejected by overfit/per-symbol gate"

    # ---- render / measure ----------------------------------------------------
    def render(self, data: dict) -> str:
        fm = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
        cov = data.get("coverage", {})
        active = data.get("active", [])
        rejected = data.get("rejected", [])
        body_lines = [
            "## Research digest (derived view — notes remain truth)",
            "",
            f"Universe ({len(data.get('universe', []))}): {data.get('universe')}",
            f"Uncovered (PRIORITY): {cov.get('uncovered')}",
            f"Covered: {cov.get('covered')}",
            f"Regime: {data.get('regime') or 'n/a'}",
            f"Perf: {data.get('perf')}",
            "",
            f"### Active forward-tests ({len(active)})",
        ]
        for a in active:
            body_lines.append(f"- {a.get('id')} {a.get('family')} → {a.get('deployed_symbols')} "
                              f"({a.get('note')})")
        body_lines += ["", f"### Rejected (rolled up by novelty key, {len(rejected)} buckets)"]
        for b in rejected:
            body_lines.append(f"- {b.get('key')} — tried {b.get('tried')}× (last {b.get('last')}): "
                              f"{b.get('lesson')}  e.g. {b.get('examples')}")
        body = "\n".join(body_lines)
        return f"---\n{fm}---\n\n{body}\n"

    def measure(self, data: dict | None = None) -> int:
        data = data if data is not None else self.load()
        return _est_tokens(self.render(data))

    # ---- helpers -------------------------------------------------------------
    def _rel(self, path: Path) -> str:
        return str(path.relative_to(self.root)).replace("\\", "/")

    @staticmethod
    def _read_fm(path: Path) -> dict | None:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return {}
        parts = text.split("---", 2)
        try:
            return yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            return None


def enforce_budget(data: dict, *, cap: int = DIGEST_TOKEN_CAP) -> dict:
    """Keep the rendered digest ≤ `cap` tokens by compacting in PRIORITY order (never silent
    truncation — every drop is logged):
      1. shrink `rejected:` lessons,
      2. drop the OLDEST rejected buckets, replacing them with a single pointer line.
    Uncovered symbols / active strategies are decision-critical and are never dropped (§7)."""
    tmp = ResearchDigest(".")   # render-only helper; no I/O

    def size(d):
        return _est_tokens(tmp.render(d))

    if size(data) <= cap:
        return data

    # 1) shrink lessons
    for b in data.get("rejected", []):
        if b.get("lesson"):
            b["lesson"] = b["lesson"][:60]
    if size(data) <= cap:
        log.info("digest: compacted by shrinking rejected lessons (still ≤ cap)")
        return data

    # 2) keep the NEWEST rejected buckets that fit; collapse the rest to one pointer line.
    # Binary-search the keep-count so this stays O(n log n) renders, not O(n²).
    rejected = sorted(data.get("rejected", []), key=lambda b: str(b.get("last") or ""),
                      reverse=True)  # newest first
    total = len(rejected)
    total_tried = sum(int(b.get("tried") or 0) for b in rejected)

    def fits(keep: int) -> bool:
        kept = rejected[:keep]
        dropped_n = total - keep
        if dropped_n:
            dropped_tried = total_tried - sum(int(b.get("tried") or 0) for b in kept)
            kept = kept + [_omitted_marker(dropped_n, dropped_tried)]
        return size({**data, "rejected": kept}) <= cap

    lo, hi, best = 0, total, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if fits(mid):
            best, lo = mid, mid + 1
        else:
            hi = mid - 1

    kept = rejected[:best]
    dropped = total - best
    if dropped:
        dropped_tried = total_tried - sum(int(b.get("tried") or 0) for b in kept)
        kept.append(_omitted_marker(dropped, dropped_tried))
        log.info("digest: kept %d newest rejected bucket(s); collapsed %d older (%d total "
                 "rejections) to a pointer line to hold the %d-token budget — see "
                 "Strategies/Graveyard/", best, dropped, dropped_tried, cap)
    data["rejected"] = kept
    return data


def _omitted_marker(n: int, tried: int) -> dict:
    return {"key": f"(+{n} older rejected families omitted)", "tried": tried,
            "last": "", "lesson": "see Strategies/Graveyard/ for the full record", "examples": []}


def digest_diff(incremental: dict, rebuilt: dict) -> list[str]:
    """Compare an incrementally-maintained digest against a fresh rebuild (CONTEXT-DIGEST-SPEC
    §5). Returns a list of human-readable divergences (empty ⇒ in sync). Drift means a write
    bypassed `VaultWriter` (e.g. a hand edit in Obsidian) or a digest update was swallowed —
    the rebuild is authoritative; the caller writes a `system-alert` on any divergence."""
    diffs: list[str] = []

    def _set(d, *path):
        cur = d
        for p in path:
            cur = (cur or {}).get(p) if isinstance(cur, dict) else None
        return set(cur or [])

    if set(incremental.get("universe") or []) != set(rebuilt.get("universe") or []):
        diffs.append(f"universe: incremental={incremental.get('universe')} "
                     f"rebuilt={rebuilt.get('universe')}")

    inc_active = {a.get("id") for a in incremental.get("active", [])}
    reb_active = {a.get("id") for a in rebuilt.get("active", [])}
    if inc_active != reb_active:
        diffs.append(f"active ids: only-incremental={sorted(inc_active - reb_active)} "
                     f"only-rebuilt={sorted(reb_active - inc_active)}")

    if _set(incremental, "coverage", "covered") != _set(rebuilt, "coverage", "covered"):
        diffs.append("coverage.covered diverges")

    inc_rej = {b.get("key"): int(b.get("tried") or 0) for b in incremental.get("rejected", [])
               if not str(b.get("key", "")).startswith("(+")}
    reb_rej = {b.get("key"): int(b.get("tried") or 0) for b in rebuilt.get("rejected", [])
               if not str(b.get("key", "")).startswith("(+")}
    for key in set(inc_rej) | set(reb_rej):
        a, b = inc_rej.get(key, 0), reb_rej.get(key, 0)
        if a != b:
            diffs.append(f"rejected[{key}]: incremental tried={a} rebuilt tried={b}")
    return diffs
