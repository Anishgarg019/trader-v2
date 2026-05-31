"""Universe selection: liquidity gate → volatility band → sector cap (spec §2)."""
import numpy as np
import pandas as pd
import pytest

from agent.universe import compute_candidate_metrics, select_universe, write_universe_note
from vault.writer import VaultWriter


def cand(symbol, sector, atv, atr_pct, exchange="NSE", token=1):
    return {"symbol": symbol, "exchange": exchange, "token": token,
            "sector": sector, "avg_traded_value": atv, "atr_pct": atr_pct}


def test_compute_metrics():
    n = 40
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    df = pd.DataFrame({
        "open": [100] * n, "high": [102] * n, "low": [98] * n,
        "close": [100] * n, "volume": [1000] * n,
    }, index=idx)
    m = compute_candidate_metrics(df, liquidity_window=20, atr_len=14)
    assert m["avg_traded_value"] == pytest.approx(100 * 1000)
    assert m["last_close"] == 100
    # TR=4 each bar → ATR=4 → atr_pct = 4/100 = 0.04
    assert m["atr_pct"] == pytest.approx(0.04)


def test_liquidity_floor_rejects():
    cands = [cand("AAA", "IT", 1e8, 0.03), cand("BBB", "IT", 1e5, 0.03)]
    sel = select_universe(cands, size=10, min_traded_value=1e6)
    syms = [p["symbol"] for p in sel.picks]
    assert "AAA" in syms and "BBB" not in syms
    assert ("BBB", "below liquidity floor") in sel.rejected


def test_volatility_band_filters():
    cands = [cand("LOWVOL", "IT", 1e8, 0.002),   # too quiet
             cand("OK", "IT", 1e8, 0.03),
             cand("WILD", "IT", 1e8, 0.20)]       # too wild
    sel = select_universe(cands, atr_min_pct=0.01, atr_max_pct=0.06)
    syms = [p["symbol"] for p in sel.picks]
    assert syms == ["OK"]


def test_sector_cap_enforced():
    cands = [cand(f"BANK{i}", "BANK", 1e8 - i, 0.03) for i in range(5)]
    sel = select_universe(cands, size=10, max_per_sector=3)
    assert len(sel.picks) == 3   # only 3 from one sector
    assert any("sector cap" in r[1] for r in sel.rejected)


def test_size_and_liquidity_ordering():
    cands = [cand("A", "IT", 5e7, 0.03), cand("B", "ENERGY", 9e7, 0.03),
             cand("C", "FMCG", 7e7, 0.03)]
    sel = select_universe(cands, size=2, max_per_sector=3)
    assert [p["symbol"] for p in sel.picks] == ["B", "C"]  # highest liquidity first


def test_write_universe_note(tmp_path):
    w = VaultWriter(tmp_path)
    w.ensure_structure()
    sel = select_universe([cand("RELIANCE", "ENERGY", 1e9, 0.025, token=738561),
                           cand("INFY", "IT", 5e8, 0.03, token=408065)], size=10)
    write_universe_note(w, sel, d="2026-05-31")
    fm, body = w.read_note("Universe/current-universe.md")
    assert fm["type"] == "universe"
    assert "NSE:RELIANCE" in fm["names"]
    assert "ATR%" in body and "Changelog" in body
