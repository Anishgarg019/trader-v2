# Build Brief — Systematic Trading Agent

> **Read this first, then read `00 - Trading Agent Spec.md`.** This brief tells you
> *what to build and in what order*. The spec is the *runtime rulebook* the agent
> obeys once built — it is the source of truth for all behavior, risk, and safety
> rules. When this brief and the spec ever seem to differ on agent behavior, **the
> spec wins**; come back and ask before diverging.

> **⚠️ Reality note (2026-05-31 reconciliation).** **Zerodha offers no Kite API
> sandbox** (verified — Kite Connect is live-only). So "sandbox" throughout this brief
> means the **local in-process paper-trading engine**: Kite is used for **read-only
> market/account data only**, and every order is simulated locally and never sent to a
> Kite order endpoint. The Kite data client (`kite_client.py`) carries **no order
> methods**; the simulator (`paper_broker.py`) owns all order/fill logic. All risk,
> drawdown, sizing, and journaling rules are unchanged. See spec v1.1 Reality note.

---

## 0. What we're building, in one paragraph

A **systematic paper-trading agent** for Indian equities (NSE/BSE) that runs as an
autonomous Claude Code loop. It trades a **local paper-trading engine** fed by
**read-only Zerodha Kite data** (paper money, never live — Kite has no sandbox),
starting from ₹1,00,000, cash-only (no leverage). It picks ~10
liquid/volatile stocks itself, encodes technical signals into precise rules,
backtests them on real Kite historical data with realistic costs, and trades only
the combinations that survive out-of-sample validation. Every trade is risk-sized by
ATR, protected by a broker-side stop, justified in writing, and journaled to an
**Obsidian vault** (plain Markdown files). Hard drawdown limits halt it
automatically. The real work is research — most ideas get rejected, and the agent
keeps a searchable graveyard of killed strategies.

**The end state is not "lots of trades." It's a small, documented, backtested
strategy roster the agent runs and continuously refreshes as edges decay.**

---

## 1. Ground rules for you, the builder

- **Paper only — never live.** Every order routes to the local paper engine; Kite is
  read-only data. If you cannot verify paper mode (router is `PaperBroker`, data client
  has no order methods), the system must refuse to trade. Never wire up a live account.
- **The spec is law for runtime behavior.** Risk math (§4 of spec), drawdown halts
  (§5), the daily loop (§6), the trading-day gate (§6.0), and the vault formats (§7)
  are all defined there. Build to them exactly; don't reinvent them here.
- **Confirm tools before assuming.** The Kite MCP tools available include
  `search_instruments`, `get_quotes`, `get_ltp`, `get_historical_data`,
  `place_order`, `place_gtt_order`, `modify_order`, `cancel_order`, `get_orders`,
  `get_positions`, `get_holdings`, `get_margins`, `get_profile`. Verify the exact
  schema of each before calling (the spec documents the key ones in §1.3a).
- **Fail safe, not silent.** Any ambiguity about whether an order could reach the live
  broker, dark data, or a
  risk breach → stop, write a `system-alert` note, do nothing risky.
- **Everything is auditable.** No order without a journal note. No strategy goes live
  without a backtest note. No drawdown halt without a post-mortem.
- **Be honest about limits.** Kite historical minute data is rate-limited and
  lookback-capped; paper fills are approximate. Backtest, paper, and live results
  will diverge — the code should surface this, not hide it.

---

## 2. Tech stack & layout to create

**Language:** Python 3.11+. **Why:** the backtester, indicators, and risk math are
all numerical; pandas + numpy are the right tools, and TA libraries exist.

**Suggested repo layout** (create this; adjust names if you have a good reason):

```
trading-agent/
├── README.md                      # quickstart: env vars, how to run, safety notes
├── pyproject.toml / requirements.txt
├── .env.example                   # KITE creds placeholders, VAULT_PATH, MODE=paper
├── config/
│   └── config.yaml                # capital, risk %, dd limits, universe size, paths
├── agent/
│   ├── __init__.py
│   ├── loop.py                    # the daily loop orchestrator (spec §6)
│   ├── trading_day.py             # §6.0 gate: holiday calendar + full-universe probe
│   ├── universe.py                # §2 selection: liquidity + volatility + sector cap
│   ├── signals/                   # one module per signal family (spec §3)
│   │   ├── trend.py  momentum.py  volume.py  volatility.py  structure.py  patterns.py
│   ├── strategy.py                # ensemble logic: combine/confirm signals (spec §3.3)
│   ├── risk.py                    # ATR sizing, per-trade R, exposure caps (spec §4)
│   ├── governor.py                # daily 5% / total 15% drawdown kill switch (spec §5)
│   ├── execution.py               # order placement + mandatory stop (spec §4.3)
│   ├── reconcile.py               # broker-vs-vault position/P&L reconciliation
│   └── broker/
│       ├── kite_client.py         # READ-ONLY Kite wrapper; NO order methods
│       └── paper_broker.py        # local order simulator (fills, stops, book); paper guard
├── backtest/
│   ├── engine.py                  # vectorized pandas backtester (spec §6.4.1)
│   ├── costs.py                   # Zerodha charges + slippage model
│   └── validation.py              # in-sample/out-of-sample split, overfitting flags
├── vault/
│   └── writer.py                  # read/write Obsidian .md notes w/ YAML frontmatter
├── data/
│   └── holidays_2026.py           # the hardcoded NSE/BSE calendar (from spec §6.0)
└── tests/
    └── ...                        # unit tests, esp. for risk + governor + costs
```

**Key dependencies:** `pandas`, `numpy`, a TA library (e.g. `pandas-ta` or hand-roll
indicators), `pyyaml`, `python-dotenv`. Keep it lean.

**The Obsidian vault is separate from the repo** — it's a folder of `.md` files at
`VAULT_PATH` (e.g. `~/Documents/TradingVault/`). The agent writes notes there; the
spec file itself (`00 - Trading Agent Spec.md`) lives at the vault root. `vault/writer.py`
just reads/writes Markdown — no plugin or API needed (Dataview/Templater are
human-side Obsidian plugins).

---

## 3. Build it in phases (this is the order)

Each phase ends in a **checkpoint** you can verify before moving on. Don't build
later phases on an unverified earlier one. **Phases 1–6 must NOT place any orders** —
they're built and tested against historical/read-only data first. Paper-engine
order placement only switches on in Phase 7. (No order ever reaches a live broker.)

### Phase 1 — Broker plumbing & paper guard
Build `broker/kite_client.py`: a **read-only** wrapper around the Kite data tools, with
**no order/write methods**. Build `broker/paper_broker.py`: the in-process order
simulator. Implement the **paper-mode safety check** (spec §1.3): assert `MODE=paper`,
the data client exposes no order methods, and the order router is `PaperBroker`; if
uncertain, raise and refuse. Implement read helpers: quotes, LTP, historical OHLC,
positions, holdings, orders, instrument search.
**Checkpoint:** can resolve a symbol → token via `search_instruments`, pull day
candles for it, and fetch a live quote — all read-only, paper guard passing, no orders
placed; the guard rejects any attempt to reach a live order path.

### Phase 2 — Data & the trading-day gate
Build `data/holidays_2026.py` (copy the calendar verbatim from spec §6.0) and
`agent/trading_day.py` implementing **both layers**: the hardcoded calendar + weekend
check, AND the **full-universe live data probe** that treats the day as CLOSED if the
whole universe returns stale/empty quotes (spec §6.0, including the precise
stale/empty definition and the pre-open re-probe guard). Add the 2027 hard-stop.
**Checkpoint:** gate correctly returns open/closed for a known holiday, a weekend, a
normal day, and a simulated all-dark-universe response.

### Phase 3 — Indicators & signals
Build `agent/signals/*`: implement the indicators the spec lists (MA/EMA, ADX, RSI,
MACD, stochastic, divergence, OBV, VWAP, Bollinger, ATR, S/R levels, basic
candlestick/chart patterns). Each signal is a **pure function over a price
DataFrame** returning entry/exit booleans — parameterized, no hidden constants
(spec §3.2). No trading logic here yet, just signal computation.
**Checkpoint:** each indicator matches a known-good reference on sample data; signals
are reproducible from their parameters.

### Phase 4 — Backtester with realistic costs & validation
Build `backtest/engine.py` (vectorized: candles → indicators → entry/exit series →
positions → equity curve + trade list), `backtest/costs.py` (Zerodha CNC vs MIS
charge schedules — STT, exchange txn, SEBI, stamp, GST, DP on sells; brokerage rules
— plus a slippage model; **verify current rates** against Zerodha's published list),
and `backtest/validation.py` (in-sample/out-of-sample split, overfitting flags:
too-few trades, fragile parameters, in-sample-only edge). Spec §6.4.1–§6.4.3.
**Checkpoint:** can backtest a simple rule (e.g. the spec's RSI+MA example) end to
end, output CAGR/maxDD/Sharpe-like/win-rate/trade-count, and correctly flag an
obviously overfit rule as rejected.

### Phase 5 — Risk sizing & the drawdown governor
Build `agent/risk.py` (ATR-based position sizing, per-trade R = min(5% equity,
ATR-implied), `qty` formula with the cash cap so 1x leverage is enforced, total open
risk ≤15%, per-name ≤20% notional — spec §4) and `agent/governor.py` (daily 5% and
total 15% drawdown halts off current/day-open/high-water equity — spec §5).
**These two modules are the safety core — unit-test them hard.**
**Checkpoint:** sizing returns 0 when the stop is too tight or cash insufficient;
governor trips at exactly the right equity thresholds; an order that would exceed
cash (i.e. use leverage) is rejected.

### Phase 6 — Vault writer & universe selection
Build `vault/writer.py` (create/read/update `.md` notes with the **exact YAML
frontmatter schemas** from spec §7.2–§7.4 — trade, strategy, daily notes — plus the
folder tree in §7) and `agent/universe.py` (pick ~10 names by liquidity gate →
volatility suitability → sector cap, write `Universe/current-universe.md` with the
justifying metrics — spec §2).
**Checkpoint:** running universe selection writes a valid, Dataview-queryable
universe note; a sample trade/strategy/daily note round-trips (write → read back →
fields intact).

### Phase 7 — Execution (FIRST paper orders) & reconciliation
Build `agent/execution.py` (place order in the paper engine → **immediately** place the
protective stop via simulated GTT/SL-M → **immediately** write the trade journal note;
the order→stop→note sequence is non-negotiable, spec §4.3) and `agent/reconcile.py`
(paper book positions/P&L vs vault expected state; flag breaks). **This is the first
phase that places orders — and only in the local paper engine, never a live broker.**
Start with a single tiny test order to confirm the full order→stop→note chain works
before enabling the strategy.
**Checkpoint:** one paper order places, gets a (simulated) protective stop, and produces
a complete trade note; reconciliation detects an intentionally introduced mismatch.

### Phase 8 — The daily loop orchestrator
Build `agent/loop.py` tying it together in the spec's cadence: **trading-day gate →**
pre-market (overnight review, data integrity, reconcile, risk preflight) → market
hours (run strategies, place trades w/ stops + notes, monitor execution & system
health, intervene only on exceptions) → post-market (reconcile, P&L attribution, log
anomalies) → research (hypothesize, backtest, OOS-validate, check live-strategy
decay). Wire the startup checklist from spec §8. Honor "research-only" on
closed/halted days.
**Checkpoint:** a full dry-run loop on a closed day does research-only; a dry-run on
an open day walks every block in order and respects the governor.

### Phase 9 — Reviews, decay monitoring & hardening
Add weekly/monthly/lessons-learned review note generation (spec §6.5), live-strategy
decay tracking (retire when rolling performance degrades past threshold — spec
§6.4), and general hardening: retries/backoff for rate limits, structured logging,
graceful handling of partial data.
**Checkpoint:** a week of simulated runs produces a weekly review note linking that
week's trades; a decaying strategy gets flagged for retirement.

---

## 4. Definition of done

- The agent runs the full daily loop autonomously against the **paper engine**, end to
  end, without manual steps — and refuses to place an order anywhere but the paper engine.
- Risk sizing and the drawdown governor are unit-tested and provably enforce 5%/trade,
  5% daily, 15% total, and 1x leverage.
- The backtester produces costed, out-of-sample-validated results and rejects overfit
  rules.
- Every order has a stop and a journal note; every live strategy has a backtest note;
  every drawdown halt forces a post-mortem.
- The Obsidian vault fills with valid, Dataview-queryable notes in the spec's schema.
- At least one full strategy has gone through the pipeline: hypothesis → backtest →
  OOS validation → (deploy or graveyard), with the decision documented.

---

## 5. First things to do when you start

1. Read `00 - Trading Agent Spec.md` top to bottom. It is the rulebook.
2. Confirm the Kite **read-only** data tools and their schemas; confirm Kite auth works
   for data, and that the paper engine — not Kite — owns all order placement.
3. Scaffold the repo (§2) and `.env.example`; put `00 - Trading Agent Spec.md` at the
   vault root.
4. Build Phase 1, hit its checkpoint, and **only then** continue.
5. Whenever a runtime-behavior question arises that this brief doesn't answer, the
   spec answers it — and if neither does, stop and ask rather than assume.
