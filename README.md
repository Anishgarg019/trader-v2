# Trading Agent — systematic paper-trading for NSE/BSE

A systematic **paper-trading** agent for Indian equities. It uses **Zerodha Kite Connect
for read-only market/account data only** and routes **every order to a local in-process
paper-trading engine** — because **Zerodha has no API sandbox** (Kite Connect is live-only).
No order ever reaches a live broker.

- Runtime rulebook (source of truth): [`00 - Trading Agent Spec.md`](./00%20-%20Trading%20Agent%20Spec.md) (v1.1)
- Build order: [`BUILD-BRIEF.md`](./BUILD-BRIEF.md)
- Build decisions & architecture: [`CLAUDE.md`](./CLAUDE.md)

## Safety model (non-negotiable)
1. `MODE=paper` always.
2. `agent/broker/kite_client.py` is **read-only** and exposes **no** order methods.
3. All orders go through `agent/broker/paper_broker.py` (`PaperBroker`).
4. `agent/broker/safety.py` asserts all three before any trading (spec §1.3).

## Quickstart (dev — macOS)
```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"   # or: pip install pandas numpy kiteconnect pyyaml python-dotenv pytest
cp .env.example .env                            # then fill KITE_API_KEY / KITE_API_SECRET
./.venv/bin/python scripts/kite_login.py        # one-time daily login → writes KITE_ACCESS_TOKEN
./.venv/bin/python -m pytest                     # run tests
```

`.env` and tokens are git-ignored. The production host is **Windows** (where the Obsidian
vault lives) — set `VAULT_PATH` there; all code is cross-platform (`pathlib`).

## Status — build complete (all phases) ✅
- Phase 0 — docs reconciled to the paper-engine reality (spec → v1.1).
- Phase 1 — broker plumbing & paper guard (read-only Kite client, paper engine, safety guard).
- Phase 2 — data & two-layer trading-day gate (calendar + universe probe).
- Phase 3 — indicators & signals (trend/momentum/volume/volatility/structure/patterns).
- Phase 4 — backtester + verified Zerodha cost model + OOS/overfit validation.
- Phase 5 — ATR risk sizing + drawdown governor (safety core).
- Phase 6 — vault writer (spec §7 schemas) + universe selection.
- Phase 7 — execution (order→stop→note, paper only) + reconciliation.
- Phase 8 — daily loop orchestrator (gate→pre→market→post→research).
- Phase 9 — reviews, decay monitoring, hardening, Windows setup.

**177 tests passing.** Next: deploy to Windows — see [`SETUP-WINDOWS.md`](./SETUP-WINDOWS.md).
The live read-only checkpoint runs once you add Kite creds (`scripts/kite_login.py` →
`scripts/verify_phase1.py`).

## Layout
```
agent/
  config.py            # .env + config.yaml loader (cross-platform)
  broker/
    kite_client.py     # READ-ONLY Kite wrapper — no order methods
    paper_broker.py    # local order simulator (fills, GTT, positions, cash)
    safety.py          # paper-mode guard (spec §1.3)
config/config.yaml     # capital, risk %, drawdown limits, universe, market hours
scripts/kite_login.py  # mint daily Kite access token (read-only use)
tests/                 # safety + read-only + paper-engine unit tests
```
