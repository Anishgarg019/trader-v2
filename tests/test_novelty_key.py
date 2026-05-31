"""Shared novelty/dedup key (CONTEXT-DIGEST-SPEC §3.1)."""
from agent.strategy_spec import novelty_key


def _spec(entry_preds, exit_preds, families=("mean-reversion",)):
    return {
        "id": "x", "families": list(families), "timeframe": "day",
        "entry": {"all": entry_preds},
        "exit": {"any": exit_preds},
        "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0,
    }


RSI = _spec([{"pred": "rsi_below", "length": 14, "threshold": 30}],
            [{"pred": "rsi_above", "length": 14, "threshold": 55}])


def test_identical_structure_specs_collide():
    a = novelty_key(RSI, ["NSE:HDFCBANK", "NSE:SBIN"])
    b = novelty_key(RSI, ["NSE:HDFCBANK", "NSE:SBIN"])
    assert a == b


def test_param_only_difference_collides():
    # different thresholds, SAME predicate structure → same bucket (param tweak ≠ new idea)
    other = _spec([{"pred": "rsi_below", "length": 14, "threshold": 25}],
                  [{"pred": "rsi_above", "length": 14, "threshold": 60}])
    assert novelty_key(RSI, ["NSE:SBIN"]) == novelty_key(other, ["NSE:SBIN"])


def test_new_predicate_structure_does_not_collide():
    # genuinely new variant in the same family (adds a predicate) → DIFFERENT bucket
    variant = _spec([{"pred": "rsi_below", "length": 14, "threshold": 30},
                     {"pred": "adx_above", "length": 14, "threshold": 25}],
                    [{"pred": "rsi_above", "length": 14, "threshold": 55}])
    assert novelty_key(RSI, ["NSE:SBIN"]) != novelty_key(variant, ["NSE:SBIN"])


def test_different_family_does_not_collide():
    trend = _spec([{"pred": "rsi_below", "length": 14, "threshold": 30}],
                  [{"pred": "rsi_above", "length": 14, "threshold": 55}],
                  families=("trend",))
    assert novelty_key(RSI, ["NSE:SBIN"]) != novelty_key(trend, ["NSE:SBIN"])


def test_different_symbol_target_does_not_collide():
    assert novelty_key(RSI, ["NSE:SBIN"]) != novelty_key(RSI, ["NSE:TCS"])


def test_symbol_order_and_exchange_normalized():
    # bare symbols, sorted → order- and exchange-insensitive
    k1 = novelty_key(RSI, ["NSE:SBIN", "NSE:HDFCBANK"])
    k2 = novelty_key(RSI, ["HDFCBANK", "SBIN"])
    assert k1 == k2


def test_no_symbols_is_wildcard():
    assert novelty_key(RSI).endswith("|*")
    assert novelty_key(RSI, []).endswith("|*")


def test_key_shape():
    k = novelty_key(RSI, ["NSE:HDFCBANK"])
    assert k == "mean-reversion|rsi_above+rsi_below|HDFCBANK"
