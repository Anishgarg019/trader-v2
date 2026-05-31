"""Exhaustive tests for the drawdown governor (spec §5). Safety core — test hard."""
import pytest

from agent.governor import (
    evaluate_drawdown, update_high_water_mark, assert_no_leverage, OverLeverageError,
)


def test_within_limits_allows_entry():
    d = evaluate_drawdown(100000, 100000, 100000)
    assert d.can_enter is True
    assert not d.halt_new_entries and not d.full_stop


def test_daily_limit_trips_at_exactly_5pct():
    d = evaluate_drawdown(95000, 100000, 100000)
    assert d.daily_drawdown_pct == pytest.approx(-0.05)
    assert d.halt_new_entries is True
    assert d.full_stop is False
    assert d.can_enter is False


def test_daily_just_under_5pct_does_not_trip():
    d = evaluate_drawdown(95100, 100000, 100000)
    assert d.halt_new_entries is False
    assert d.can_enter is True


def test_total_limit_trips_at_15pct():
    # down 15% from HWM; day flat so only the total limit fires
    d = evaluate_drawdown(85000, 85000, 100000)
    assert d.total_drawdown_pct == pytest.approx(-0.15)
    assert d.full_stop is True
    assert d.can_enter is False


def test_total_just_under_15pct_does_not_full_stop():
    d = evaluate_drawdown(85100, 85100, 100000)
    assert d.full_stop is False


def test_full_stop_takes_precedence_in_reason():
    # down 16% total AND down a lot on the day → full stop wins the message
    d = evaluate_drawdown(84000, 90000, 100000)
    assert d.full_stop is True
    assert d.halt_new_entries is True   # also breached daily
    assert "FULL STOP" in d.reason


def test_high_water_mark_updates_on_new_peak():
    assert update_high_water_mark(100000, 105000) == 105000
    assert update_high_water_mark(105000, 102000) == 105000


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        evaluate_drawdown(95000, 0, 100000)
    with pytest.raises(ValueError):
        evaluate_drawdown(95000, 100000, 0)


def test_assert_no_leverage_ok_at_exactly_cash():
    assert_no_leverage(100000.0, 100000.0)   # no raise


def test_assert_no_leverage_raises_above_cash():
    with pytest.raises(OverLeverageError):
        assert_no_leverage(100000.01, 100000.0)


def test_assert_no_leverage_ok_below_cash():
    assert_no_leverage(50000.0, 100000.0)
