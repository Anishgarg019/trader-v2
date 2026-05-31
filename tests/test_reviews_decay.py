"""Tests for reviews, decay monitoring, retry/backoff, and state persistence (Phase 9)."""
import pytest

from agent.reviews import summarize_trades, write_weekly_review, append_lesson
from agent.decay import rolling_win_rate, decay_check
from agent.retry import call_with_retries
from agent.state import load_state, save_state
from agent.loop import LoopState
from vault.writer import VaultWriter


def trade(pnl, strategy="s001", status="closed"):
    return {"pnl_rupees": pnl, "strategy": strategy, "status": status, "symbol": "NSE:X"}


# ---- reviews ----
def test_summarize_trades():
    s = summarize_trades([trade(100, "s1"), trade(-50, "s1"), trade(200, "s2")])
    assert s["trades"] == 3 and s["wins"] == 2 and s["losses"] == 1
    assert s["win_rate"] == pytest.approx(2 / 3)
    assert s["total_pnl"] == 250.0
    assert s["by_strategy"]["s1"]["pnl"] == 50.0
    assert s["by_strategy"]["s2"]["wins"] == 1


def test_summarize_ignores_open_trades():
    s = summarize_trades([trade(100), trade(50, status="open")])
    assert s["trades"] == 1


def test_write_weekly_review(tmp_path):
    w = VaultWriter(tmp_path); w.ensure_structure()
    write_weekly_review(w, year=2026, week=22, trades=[trade(100), trade(-30)])
    fm, body = w.read_note("Reviews/Weekly/2026-W22.md")
    assert fm["type"] == "review-weekly" and fm["period"] == "2026-W22"
    assert fm["trades"] == 2
    assert "By strategy" in body


def test_append_lesson_creates_then_appends(tmp_path):
    w = VaultWriter(tmp_path); w.ensure_structure()
    append_lesson(w, "don't widen stops", d="2026-05-29")
    append_lesson(w, "respect the governor", d="2026-05-30")
    _, body = w.read_note("Reviews/Lessons Learned.md")
    assert "don't widen stops" in body and "respect the governor" in body


# ---- decay ----
def test_rolling_win_rate_window():
    trades = [trade(10), trade(-10), trade(10), trade(10), trade(-10)]
    # only the last 4 count: [-10, 10, 10, -10] → 2 wins → 0.5
    assert rolling_win_rate(trades, window=4) == pytest.approx(0.5)
    # the early win is dropped by the window, proving it's truly rolling
    assert rolling_win_rate(trades, window=5) == pytest.approx(3 / 5)


def test_rolling_win_rate_insufficient_sample():
    assert rolling_win_rate([trade(10)], window=60) is None


def test_decay_retires_below_threshold():
    trades = [trade(-10)] * 40 + [trade(10)] * 20   # last-60 win rate = 20/60 ≈ 0.33
    v = decay_check(trades, window=60, retire_below=0.40)
    assert v.should_retire is True and v.rolling_win_rate == pytest.approx(20 / 60)


def test_decay_healthy_above_threshold():
    trades = [trade(10)] * 40 + [trade(-10)] * 20   # 40/60 ≈ 0.67
    v = decay_check(trades, window=60, retire_below=0.40)
    assert v.should_retire is False


def test_decay_insufficient_sample_no_retire():
    v = decay_check([trade(-10)] * 10, window=60)
    assert v.should_retire is False and v.rolling_win_rate is None


# ---- retry ----
def test_retry_succeeds_after_failures():
    calls = {"n": 0}
    delays = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return "ok"

    out = call_with_retries(flaky, retries=3, base_delay=0.5,
                            sleep=lambda d: delays.append(d))
    assert out == "ok" and calls["n"] == 3
    assert delays == [0.5, 1.0]   # exponential backoff


def test_retry_reraises_after_exhaustion():
    def always_fail():
        raise RuntimeError("nope")
    with pytest.raises(RuntimeError):
        call_with_retries(always_fail, retries=2, sleep=lambda d: None)


# ---- state ----
def test_state_round_trip(tmp_path):
    p = tmp_path / "state.json"
    save_state(p, LoopState(day_open_equity=98000.0, high_water_mark=105000.0,
                            current_date="2026-05-29"))
    s = load_state(p)
    assert s.day_open_equity == 98000.0 and s.high_water_mark == 105000.0
    assert s.current_date == "2026-05-29"


def test_state_default_when_missing(tmp_path):
    s = load_state(tmp_path / "nope.json")
    assert s.day_open_equity is None and s.high_water_mark == 100000.0
