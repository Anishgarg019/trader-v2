"""Production loop entry — the Windows Task Scheduler target (spec §6/§8).

Wires the real (read-only) Kite data client + local PaperBroker + vault + execution into the
Orchestrator, runs the loop, and (best-effort) publishes performance data to the cloud
dashboard. Persists LoopState across runs.

  python scripts/run_loop.py                 # run the clock-appropriate block once
  python scripts/run_loop.py --day           # walk every block (simulation/catch-up)
  python scripts/run_loop.py --watch [secs]  # near-real-time: re-run+publish through market hours

SAFETY: orders only ever go to the local PaperBroker; the Kite client is read-only; the
dashboard receives performance data only (no credentials, no order capability). Dashboard
publishing is best-effort — a DB hiccup never affects trading. Deployed strategies are read
from the vault REGISTRY (Phase 11): every forward-test strategy note with a `spec:` block is
compiled and trades only its gate-proven `deployed_symbols`. The autonomous researcher
(scripts/researcher.py) writes those notes; this loop just executes the compiled DSL.

The paper book (positions/cash/GTT) persists across runs via agent/state.py, so each morning
resumes real multi-day state.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # make repo root importable

from agent.config import load_settings, REPO_ROOT
from agent.logging_setup import get_logger
from agent.broker.kite_client import KiteDataClient
from agent.broker.paper_broker import PaperBroker
from agent.execution import ExecutionEngine
from agent.loop import Orchestrator
from agent.registry import StrategyRegistry
from agent.live_strategies import build_history_fn, build_strategy_fn
from agent.state import load_state, save_state, load_paper_book, save_paper_book
from agent.retry import call_with_retries
from agent.trading_day import IST, MARKET_OPEN, MARKET_CLOSE
from vault.writer import VaultWriter

log = get_logger()
STATE_PATH = REPO_ROOT / ".loop_state.json"
PAPER_BOOK_PATH = REPO_ROOT / ".paper_book.json"


def _load_universe(vault: VaultWriter) -> list[str] | None:
    if not vault.exists("Universe/current-universe.md"):
        return None
    fm, _ = vault.read_note("Universe/current-universe.md")
    return fm.get("names")


def _maybe_publish(result, vault, broker, price_fn) -> None:
    """Best-effort: mirror performance data to the cloud dashboard store. Never fatal."""
    dsn = os.environ.get("DASHBOARD_DB_URL")
    if not dsn:
        return
    try:
        from dashboard.store import open_store
        from dashboard.publisher import Publisher
        store = open_store(dsn)
        summary = Publisher(store, vault).publish(result, broker, price_fn)
        store.close()
        log.info("dashboard published: %s", summary)
    except Exception as e:  # noqa: BLE001 — dashboard must never break trading
        log.warning("dashboard publish failed (non-fatal): %s", e)


def main(walk_day: bool = False, watch: bool = False, interval: int = 60) -> int:
    s = load_settings()
    if not s.is_paper:
        log.error("MODE is not 'paper' — refusing to run. Set MODE=paper.")
        return 2
    if not (s.kite_api_key and s.kite_access_token):
        log.error("Missing KITE_API_KEY / KITE_ACCESS_TOKEN. Run scripts/kite_login.py first.")
        return 1

    kite = KiteDataClient(api_key=s.kite_api_key, access_token=s.kite_access_token)
    starting = float(s.config.get("capital", {}).get("starting_inr", 100000))

    def price_fn(exch_symbol: str) -> float:
        data = call_with_retries(lambda: kite.ltp([exch_symbol]))
        return float(data[exch_symbol]["last_price"])

    broker = PaperBroker(starting_cash=starting, price_fn=price_fn)
    if load_paper_book(PAPER_BOOK_PATH, broker):
        log.info("paper book restored: cash=%.2f open_positions=%d",
                 broker.cash, len(broker.get_positions()))
    vault = VaultWriter(s.vault_path)
    vault.ensure_structure()
    execu = ExecutionEngine(broker, vault, kite_client=kite, mode=s.mode)
    orch = Orchestrator(broker=broker, vault=vault, execution=execu, kite_client=kite,
                        universe=_load_universe(vault), price_fn=price_fn, mode=s.mode)
    runner = orch.run_day if walk_day else orch.run_once

    # Deployed strategies come from the vault registry (Phase 11): every forward-test note
    # with a compiled `spec:` block, trading only its gate-proven `deployed_symbols`.
    registry = StrategyRegistry(vault)
    active = registry.load_active_specs()
    history_fn = build_history_fn(kite)
    strategy_fn = build_strategy_fn(active, history_fn=history_fn, broker=broker,
                                    vault=vault, state_dir=str(REPO_ROOT))
    log.info("registry: %d active forward-test spec(s): %s", len(active),
             [a.spec.get("id") for a in active])

    def fetch_quotes():
        if not orch.universe:
            return None
        try:
            return call_with_retries(lambda: kite.quote(orch.universe))
        except Exception as e:  # noqa: BLE001
            log.warning("universe quote probe failed: %s", e)
            return None

    def one_pass() -> None:
        state = load_state(STATE_PATH)
        now = datetime.now(IST)
        result = runner(now, state=state, strategy_fn=strategy_fn, quotes=fetch_quotes())
        save_state(STATE_PATH, result.state)
        save_paper_book(PAPER_BOOK_PATH, broker)  # persist positions/cash/GTTs across runs
        _maybe_publish(result, vault, broker, price_fn)
        log.info("date=%s phase=%s trading_day=%s research_only=%s equity=%.2f",
                 result.date, result.phase, result.trading_day, result.research_only,
                 result.equity)
        if result.governor:
            log.info("daily_dd=%.2f%% total_dd=%.2f%% halt=%s full_stop=%s",
                     result.governor.daily_drawdown_pct * 100,
                     result.governor.total_drawdown_pct * 100,
                     result.governor.halt_new_entries, result.governor.full_stop)
        for r in result.reasons:
            log.info("reason: %s", r)

    if not watch:
        one_pass()
        return 0

    log.info("watch mode: re-running every %ds through market hours (Ctrl-C to stop)", interval)
    while True:
        one_pass()
        now_t = datetime.now(IST).timetz().replace(tzinfo=None)
        if now_t > MARKET_CLOSE:
            log.info("past market close — final pass done, exiting watch.")
            return 0
        time.sleep(max(5, interval))


if __name__ == "__main__":
    args = sys.argv[1:]
    iv = 60
    if "--watch" in args:
        idx = args.index("--watch")
        if idx + 1 < len(args) and args[idx + 1].isdigit():
            iv = int(args[idx + 1])
    try:
        raise SystemExit(main(walk_day="--day" in args, watch="--watch" in args, interval=iv))
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 — alert on any unhandled failure, then re-raise
        from agent.notify import notify_failure
        notify_failure("run_loop", e)
        raise
