"""Migrate the s001 strategy note to the Phase-11 spec format (RESEARCHER-SPEC §9).

Idempotent. Updates the existing note in place (preserving its rich body/history):
  - status: live → forward-test  (forward-test is the ceiling; `live` is never used)
  - adds the `spec:` block (the DSL re-expression, agent/strategy.S001_SPEC)
  - adds `deployed_symbols:` = the current live universe (s001 keeps exercising the paper
    pipeline exactly as the hand-written version did; it failed OOS 0/10 — pipeline proof,
    not edge), and appends a status-history line.

  python scripts/deploy_s001.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import load_settings
from agent.strategy import S001_SPEC, STRATEGY_NOTE
from vault.writer import VaultWriter


def main() -> int:
    s = load_settings()
    vault = VaultWriter(s.vault_path)
    if not vault.exists(STRATEGY_NOTE):
        print(f"ERROR: s001 note not found at {STRATEGY_NOTE} (vault={vault.root})",
              file=sys.stderr)
        return 1

    # universe = deployed_symbols (reproduce the hand-written whole-universe behavior)
    universe = []
    if vault.exists("Universe/current-universe.md"):
        fm_u, _ = vault.read_note("Universe/current-universe.md")
        universe = list(fm_u.get("names") or [])

    fm, body = vault.read_note(STRATEGY_NOTE)
    spec = {**S001_SPEC, "note_rel": STRATEGY_NOTE}
    fm["status"] = "forward-test"
    fm["spec"] = spec
    fm["deployed_symbols"] = universe

    history_line = (
        f"- {date.today()} migrated to Phase-11 spec format: status live→forward-test, "
        f"added spec: DSL block + deployed_symbols ({len(universe)} universe names). "
        f"Compiled spec reproduces the hand-written RSI/SMA200 signal logic "
        f"(time-stop dropped; ATR stop now via the risk engine)."
    )
    if "## Status history" in body and history_line not in body:
        body = body.rstrip() + "\n" + history_line + "\n"

    vault.write_note(STRATEGY_NOTE, fm, body)
    print(f"updated {STRATEGY_NOTE}: status={fm['status']} "
          f"deployed_symbols={len(universe)} spec.id={spec['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
