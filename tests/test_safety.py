"""Tests for the paper-mode safety guard (spec §1.3). This is safety-critical."""
import pytest

from agent.broker.safety import (
    FORBIDDEN_ORDER_METHODS,
    SafetyViolation,
    assert_no_order_methods,
    assert_paper_mode,
)
from agent.broker.paper_broker import PaperBroker


def test_assert_no_order_methods_passes_for_clean_object():
    class Clean:
        def profile(self):
            return {}
    assert_no_order_methods(Clean())  # no raise


@pytest.mark.parametrize("method", sorted(FORBIDDEN_ORDER_METHODS))
def test_assert_no_order_methods_rejects_each_forbidden_method(method):
    obj = type("Leaky", (), {method: lambda self, *a, **k: None})()
    with pytest.raises(SafetyViolation):
        assert_no_order_methods(obj)


def test_assert_paper_mode_happy_path():
    pb = PaperBroker()
    assert_paper_mode(pb, kite_client=None, mode="paper")  # no raise


def test_assert_paper_mode_rejects_non_paper_mode():
    pb = PaperBroker()
    with pytest.raises(SafetyViolation):
        assert_paper_mode(pb, mode="live")


def test_assert_paper_mode_rejects_non_paper_router():
    class NotPaper:
        pass
    with pytest.raises(SafetyViolation):
        assert_paper_mode(NotPaper(), mode="paper")


def test_assert_paper_mode_rejects_kite_with_order_methods():
    pb = PaperBroker()

    class LeakyKite:
        def place_order(self, *a, **k):
            return "oops"

    with pytest.raises(SafetyViolation):
        assert_paper_mode(pb, kite_client=LeakyKite(), mode="paper")
