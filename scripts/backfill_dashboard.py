"""One-shot backfill of the dashboard's strategy roster + research-run log from the vault.

The live loop already publishes these on every pass (`Publisher.publish`); this script just
populates the dashboard store immediately (e.g. after deploying the Strategies/Research tab)
without waiting for the next loop. Read-only over the vault; no Kite, no orders.

Usage (PowerShell):
    $env:DASHBOARD_DB_URL = "postgres://..."   # or a local sqlite path
    python scripts/backfill_dashboard.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import load_settings
from dashboard.publisher import Publisher
from dashboard.store import open_store
from vault.writer import VaultWriter


def main() -> int:
    s = load_settings()          # loads .env → DASHBOARD_DB_URL, VAULT_PATH, etc.
    dsn = os.environ.get("DASHBOARD_DB_URL")
    if not dsn:
        print("DASHBOARD_DB_URL is not set (.env or environment) — nothing to backfill.")
        return 1
    vault = VaultWriter(s.vault_path)
    store = open_store(dsn)
    pub = Publisher(store, vault)
    n_strats = pub.sync_strategies()
    n_runs = pub.sync_research_runs()
    store.close()
    print(f"backfilled: {n_strats} strategies, {n_runs} research runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
