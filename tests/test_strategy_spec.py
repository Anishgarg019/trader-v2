"""DSL validator + param counter (Phase 11, RESEARCHER-SPEC §3/§10)."""
import pytest

from agent.strategy_spec import (
    PREDICATES, SpecError, validate_spec, count_params,
    MAX_PARAMS, MAX_DEPTH, MAX_LEAVES,
)


S001 = {
    "id": "s001", "name": "RSI Mean-Reversion",
    "families": ["mean-reversion", "momentum"], "timeframe": "day",
    "entry": {"all": [
        {"pred": "rsi_below", "length": 14, "threshold": 30},
        {"pred": "price_above_ma", "length": 200, "kind": "sma"},
    ]},
    "exit": {"any": [{"pred": "rsi_above", "length": 14, "threshold": 55}]},
    "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0,
}


def _minimal(pred_node, exit_node=None):
    return {
        "id": "t", "name": "t", "timeframe": "day",
        "entry": {"all": [pred_node]},
        "exit": exit_node or {"any": [{"pred": "rsi_above", "length": 14, "threshold": 60}]},
        "atr_k": 2.0, "atr_len": 14, "size_fraction": 1.0,
    }


# ---- s001 + whitelist coverage ----------------------------------------------
def test_s001_validates():
    assert validate_spec(S001) is S001


def test_s001_param_count():
    # tunable: rsi threshold 30 + rsi threshold 55 + atr_k = 3 (lengths excluded)
    assert count_params(S001) == 3


def _example_value(kind, lo, hi):
    if kind == "int":
        return lo
    if kind == "num":
        return (lo + hi) / 2.0
    return sorted(lo)[0]  # enum


def test_every_whitelisted_predicate_validates():
    for pred, spec in PREDICATES.items():
        node = {"pred": pred}
        for name, desc in spec["params"].items():
            node[name] = _example_value(desc[0], desc[1], desc[2])
        # fix ordered constraints (fast<slow)
        for a, b in spec.get("ordered", []):
            if node.get(a, 0) >= node.get(b, 1):
                node[a] = spec["params"][a][1]
                node[b] = spec["params"][b][2]
        s = _minimal(node)
        assert validate_spec(s) is s, f"{pred} should validate"


# ---- rejections -------------------------------------------------------------
def test_unknown_predicate_rejected():
    with pytest.raises(SpecError, match="unknown predicate"):
        validate_spec(_minimal({"pred": "teleport"}))


def test_unknown_param_key_rejected():
    with pytest.raises(SpecError, match="unknown param"):
        validate_spec(_minimal({"pred": "rsi_below", "length": 14, "threshold": 30, "wat": 1}))


def test_unknown_combinator_rejected():
    s = _minimal({"pred": "rsi_below", "length": 14, "threshold": 30})
    s["entry"] = {"maybe": [{"pred": "rsi_below", "length": 14, "threshold": 30}]}
    with pytest.raises(SpecError):
        validate_spec(s)


def test_param_below_bound_rejected():
    with pytest.raises(SpecError, match="out of bounds"):
        validate_spec(_minimal({"pred": "rsi_below", "length": 14, "threshold": 2}))


def test_param_above_bound_rejected():
    with pytest.raises(SpecError, match="out of bounds"):
        validate_spec(_minimal({"pred": "rsi_below", "length": 14, "threshold": 80}))


def test_wrong_type_rejected():
    with pytest.raises(SpecError, match="integer"):
        validate_spec(_minimal({"pred": "rsi_below", "length": 14.5, "threshold": 30}))


def test_enum_rejected():
    with pytest.raises(SpecError, match="not in"):
        validate_spec(_minimal({"pred": "price_above_ma", "length": 50, "kind": "wma"}))


def test_fast_not_less_than_slow_rejected():
    with pytest.raises(SpecError, match="fast < slow"):
        validate_spec(_minimal({"pred": "ma_cross_up", "fast": 100, "slow": 50}))


def test_missing_required_numeric_param_rejected():
    with pytest.raises(SpecError, match="missing required"):
        validate_spec(_minimal({"pred": "rsi_below", "length": 14}))


def test_empty_entry_rejected():
    s = _minimal({"pred": "rsi_below", "length": 14, "threshold": 30})
    s["entry"] = {}
    with pytest.raises(SpecError, match="entry"):
        validate_spec(s)


def test_missing_exit_rejected():
    s = _minimal({"pred": "rsi_below", "length": 14, "threshold": 30})
    del s["exit"]
    with pytest.raises(SpecError, match="exit"):
        validate_spec(s)


def test_atr_k_out_of_bounds_rejected():
    s = _minimal({"pred": "rsi_below", "length": 14, "threshold": 30})
    s["atr_k"] = 9.0
    with pytest.raises(SpecError, match="atr_k"):
        validate_spec(s)


def test_atr_k_required():
    s = _minimal({"pred": "rsi_below", "length": 14, "threshold": 30})
    del s["atr_k"]
    with pytest.raises(SpecError, match="atr_k"):
        validate_spec(s)


def test_size_fraction_out_of_bounds_rejected():
    s = _minimal({"pred": "rsi_below", "length": 14, "threshold": 30})
    s["size_fraction"] = 0.0
    with pytest.raises(SpecError, match="size_fraction"):
        validate_spec(s)


def test_bad_timeframe_rejected():
    s = _minimal({"pred": "rsi_below", "length": 14, "threshold": 30})
    s["timeframe"] = "minute"
    with pytest.raises(SpecError, match="timeframe"):
        validate_spec(s)


# ---- structural caps --------------------------------------------------------
def test_too_many_leaves_rejected():
    # 9 leaves total > MAX_LEAVES (8)
    leaves = [{"pred": "rsi_below", "length": 14, "threshold": 30} for _ in range(9)]
    s = _minimal({"pred": "rsi_below", "length": 14, "threshold": 30})
    s["entry"] = {"all": leaves}
    with pytest.raises(SpecError, match="leaf count"):
        validate_spec(s)


def test_too_deep_rejected():
    # nest 'all' deeper than MAX_DEPTH
    node = {"pred": "rsi_below", "length": 14, "threshold": 30}
    for _ in range(MAX_DEPTH + 1):
        node = {"all": [node]}
    s = _minimal({"pred": "rsi_below", "length": 14, "threshold": 30})
    s["entry"] = node
    with pytest.raises(SpecError, match="depth"):
        validate_spec(s)


def test_too_many_tunable_params_rejected():
    # 5 threshold leaves + atr_k = 6 tunable knobs > MAX_PARAMS (5)
    leaves = [
        {"pred": "rsi_below", "length": 14, "threshold": 30},
        {"pred": "adx_above", "length": 14, "threshold": 25},
        {"pred": "volume_spike", "length": 20, "k": 2.0},
        {"pred": "bollinger_break_up", "length": 20, "k": 2.0},
        {"pred": "stoch_below", "k_len": 14, "d_len": 3, "threshold": 20},
    ]
    s = _minimal({"pred": "rsi_below", "length": 14, "threshold": 30})
    s["entry"] = {"all": leaves}
    with pytest.raises(SpecError, match="param count"):
        validate_spec(s)


def test_count_params_excludes_lengths_and_categoricals():
    # price_above_ma (length + kind) contributes 0; ma_cross_up (fast/slow/kind) 0
    s = _minimal({"all": [
        {"pred": "price_above_ma", "length": 50, "kind": "ema"},
        {"pred": "ma_cross_up", "fast": 20, "slow": 50, "kind": "sma"},
    ]} if False else {"pred": "price_above_ma", "length": 50, "kind": "ema"})
    s["entry"] = {"all": [
        {"pred": "price_above_ma", "length": 50, "kind": "ema"},
        {"pred": "ma_cross_up", "fast": 20, "slow": 50, "kind": "sma"},
        {"pred": "breakout_up", "length": 20},
    ]}
    s["exit"] = {"any": [{"pred": "breakout_down", "length": 20}]}
    # only atr_k counts
    assert count_params(s) == 1
    assert validate_spec(s) is s


def test_not_combinator_validates_and_counts():
    s = _minimal({"not": {"pred": "rsi_above", "length": 14, "threshold": 70}})
    assert validate_spec(s) is s
    # rsi threshold (entry) + rsi threshold (exit default 60) + atr_k = 3
    assert count_params(s) == 3
