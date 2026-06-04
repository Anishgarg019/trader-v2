-- Dashboard schema for Supabase / Postgres (prod).
-- Run once in the Supabase SQL editor (or it is auto-created by dashboard.store on first
-- connect). Performance data only — no credentials, no order capability.

CREATE TABLE IF NOT EXISTS equity_snapshots(
    ts TEXT PRIMARY KEY, equity REAL, cash REAL,
    daily_dd_pct REAL, total_dd_pct REAL);

CREATE TABLE IF NOT EXISTS trades(
    order_id TEXT PRIMARY KEY, date TEXT, symbol TEXT, exchange TEXT, strategy_id TEXT,
    direction TEXT, qty INTEGER, entry_price REAL, stop_price REAL, exit_price REAL,
    status TEXT, outcome TEXT, pnl_rupees REAL, risk_rupees REAL,
    justification TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS positions(
    symbol TEXT PRIMARY KEY, exchange TEXT, qty INTEGER, avg_price REAL,
    last_price REAL, mtm REAL, updated_at TEXT);

CREATE TABLE IF NOT EXISTS alerts(
    uid TEXT PRIMARY KEY, date TEXT, kind TEXT, detail TEXT, ts TEXT);

CREATE TABLE IF NOT EXISTS daily(
    date TEXT PRIMARY KEY, day_open_equity REAL, day_close_equity REAL,
    drawdown_day_pct REAL, drawdown_total_pct REAL, halted INTEGER, trades_today INTEGER);

CREATE TABLE IF NOT EXISTS universe(
    symbol TEXT PRIMARY KEY, exchange TEXT, sector TEXT,
    avg_traded_value REAL, atr_pct REAL, as_of TEXT);

-- Strategy roster: the research output (forward-test / rejected / retired) with the
-- reasoning + per-symbol backtest, mirrored from the vault's strategy notes + graveyard.
CREATE TABLE IF NOT EXISTS strategies(
    id TEXT PRIMARY KEY, name TEXT, status TEXT, families TEXT,
    deployed_symbols TEXT, created TEXT,
    oos_return REAL, oos_sharpe REAL, win_rate REAL, trades INTEGER,
    symbols_deployed INTEGER, symbols_tested INTEGER, n_params INTEGER,
    thesis TEXT, reasoning TEXT, detail TEXT, in_graveyard INTEGER, updated_at TEXT);

-- Researcher run log (the daily/weekly propose→gate→deploy/reject cadence).
CREATE TABLE IF NOT EXISTS research_runs(
    uid TEXT PRIMARY KEY, date TEXT, cadence TEXT, proposed INTEGER, valid INTEGER,
    deployed_n INTEGER, rejected INTEGER, coverage_before INTEGER, coverage_after INTEGER,
    summary TEXT, updated_at TEXT);
