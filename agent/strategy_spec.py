"""Strategy-spec DSL: schema + validator + param counter (Phase 11, RESEARCHER-SPEC §3).

A *strategy spec* is plain JSON data — never code. The research desk (headless Claude)
emits specs; this module validates them against a hard whitelist BEFORE anything compiles
or runs (safety invariant #1: no LLM code on the execution path). An unknown predicate, an
out-of-bounds param, or a tree that's too deep/wide raises `SpecError` — it is never
executed.

`count_params` implements the locked param-counting rule (user decision 2026-05-31): count
only *deliberately-tunable knobs* — thresholds, std-dev/volume multipliers, candle body
fractions, and the ATR stop multiplier `atr_k`. Conventional indicator *lengths*
(14/20/50/200, fast/slow/signal windows, lookbacks) are fixed structure and are EXCLUDED
from `n_params`. This keeps the 5-knob overfit ceiling biting on tuned thresholds rather
than on standard window lengths (RESEARCHER-SPEC §3.4 / §9 / §12.3).
"""
from __future__ import annotations

from typing import Any

# ---- caps (RESEARCHER-SPEC §3.3/§3.4) ---------------------------------------
MAX_PARAMS = 5          # tunable-knob ceiling (also the overfit gate's max_params)
MAX_DEPTH = 4           # predicate-tree nesting depth
MAX_LEAVES = 8          # total leaf predicates across entry + exit
ALLOWED_TIMEFRAMES = frozenset({"day"})
ATR_K_BOUNDS = (0.5, 5.0)
ATR_LEN_BOUNDS = (5, 50)
SIZE_FRACTION_BOUNDS = (0.0, 1.0)   # (0, 1.0]  — lower exclusive, upper inclusive

COMBINATORS = frozenset({"all", "any", "not"})


class SpecError(ValueError):
    """A spec failed schema/whitelist/bounds validation. Never compiled, never run."""


# ---- parameter descriptors --------------------------------------------------
# Each entry: name -> (kind, lo, hi, tunable)
#   kind: "int" | "num" | "enum"
#   lo/hi: numeric bounds (inclusive); for "enum", lo is the frozenset of allowed values
#   tunable: True if the param counts toward n_params (a chosen knob, not a fixed length)
def _int(lo, hi, tunable=False):  return ("int", lo, hi, tunable)
def _num(lo, hi, tunable=False):  return ("num", lo, hi, tunable)
def _enum(values):                return ("enum", frozenset(values), None, False)

_KIND = _enum({"sma", "ema"})
_OSC = _enum({"rsi", "macd"})

# The predicate whitelist (RESEARCHER-SPEC §3.2). Anything not here is a hard reject.
# `ordered` lists (a, b) param pairs that must satisfy a < b.
PREDICATES: dict[str, dict[str, Any]] = {
    "price_above_ma": {"params": {"length": _int(5, 250), "kind": _KIND}},
    "price_below_ma": {"params": {"length": _int(5, 250), "kind": _KIND}},
    "ma_cross_up":    {"params": {"fast": _int(3, 100), "slow": _int(5, 250), "kind": _KIND},
                       "ordered": [("fast", "slow")]},
    "ma_cross_down":  {"params": {"fast": _int(3, 100), "slow": _int(5, 250), "kind": _KIND},
                       "ordered": [("fast", "slow")]},
    "adx_above":      {"params": {"length": _int(5, 50), "threshold": _num(10, 60, True)}},
    "rsi_below":      {"params": {"length": _int(5, 50), "threshold": _num(5, 50, True)}},
    "rsi_above":      {"params": {"length": _int(5, 50), "threshold": _num(50, 95, True)}},
    "macd_cross_up":  {"params": {"fast": _int(5, 20), "slow": _int(15, 40), "signal": _int(5, 15)},
                       "ordered": [("fast", "slow")]},
    "macd_cross_down": {"params": {"fast": _int(5, 20), "slow": _int(15, 40), "signal": _int(5, 15)},
                        "ordered": [("fast", "slow")]},
    "stoch_below":    {"params": {"k_len": _int(5, 30), "d_len": _int(2, 10),
                                  "threshold": _num(5, 30, True)}},
    "stoch_above":    {"params": {"k_len": _int(5, 30), "d_len": _int(2, 10),
                                  "threshold": _num(70, 95, True)}},
    "bullish_divergence": {"params": {"length": _int(10, 60), "osc": _OSC}},
    "bearish_divergence": {"params": {"length": _int(10, 60), "osc": _OSC}},
    "breakout_up":    {"params": {"length": _int(5, 100)}},
    "breakout_down":  {"params": {"length": _int(5, 100)}},
    "higher_highs":   {"params": {"length": _int(5, 100)}},
    "lower_lows":     {"params": {"length": _int(5, 100)}},
    "bollinger_break_up": {"params": {"length": _int(5, 60), "k": _num(1.0, 3.5, True)}},
    "bollinger_break_dn": {"params": {"length": _int(5, 60), "k": _num(1.0, 3.5, True)}},
    "bollinger_squeeze":  {"params": {"length": _int(5, 60), "k": _num(1.0, 3.5, True),
                                      "lookback": _int(10, 120)}},
    "volume_spike":   {"params": {"length": _int(5, 60), "k": _num(1.2, 4.0, True)}},
    "volume_confirms": {"params": {"length": _int(5, 60)}},
    "doji":           {"params": {"body_frac": _num(0.05, 0.2, True)}},
    "hammer":         {"params": {"body_frac": _num(0.2, 0.5, True)}},
    "bullish_engulfing": {"params": {}},
    "bearish_engulfing": {"params": {}},
}


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_param(pred: str, name: str, value: Any, desc: tuple) -> None:
    kind = desc[0]
    if kind == "enum":
        allowed = desc[1]
        if value not in allowed:
            raise SpecError(f"{pred}.{name}={value!r} not in {sorted(allowed)}")
        return
    if not _is_number(value):
        raise SpecError(f"{pred}.{name} must be numeric, got {value!r}")
    if kind == "int" and int(value) != value:
        raise SpecError(f"{pred}.{name} must be an integer, got {value!r}")
    lo, hi = desc[1], desc[2]
    if not (lo <= value <= hi):
        raise SpecError(f"{pred}.{name}={value} out of bounds [{lo}, {hi}]")


def _validate_leaf(node: dict) -> int:
    """Validate one leaf predicate. Returns the count of TUNABLE params it contributes."""
    pred = node.get("pred")
    if pred not in PREDICATES:
        raise SpecError(f"unknown predicate {pred!r}")
    spec = PREDICATES[pred]
    allowed = spec["params"]
    extra = set(node) - {"pred"} - set(allowed)
    if extra:
        raise SpecError(f"{pred}: unknown param key(s) {sorted(extra)}")

    tunable = 0
    for name, desc in allowed.items():
        if name in node:
            _check_param(pred, name, node[name], desc)
            if desc[3]:
                tunable += 1
        elif desc[0] != "enum":
            # numeric params are required (no implicit defaults — specs must be explicit)
            raise SpecError(f"{pred}: missing required param {name!r}")
    for a, b in spec.get("ordered", []):
        if a in node and b in node and not (node[a] < node[b]):
            raise SpecError(f"{pred}: requires {a} < {b}, got {node[a]} >= {node[b]}")
    return tunable


def _walk(node: Any, depth: int) -> tuple[int, int, int]:
    """Recursively validate a predicate tree.

    Returns (leaf_count, tunable_param_count, max_depth_seen). Raises SpecError on any
    structural or whitelist violation. `depth` is the current nesting level (root = 1).
    """
    if not isinstance(node, dict):
        raise SpecError(f"predicate node must be an object, got {type(node).__name__}")
    combs = [k for k in node if k in COMBINATORS]
    is_leaf = "pred" in node

    if combs and is_leaf:
        raise SpecError("node mixes a combinator with a 'pred' leaf")
    if len(combs) > 1:
        raise SpecError(f"node has multiple combinators {combs}")

    if is_leaf:
        return 1, _validate_leaf(node), depth

    if not combs:
        raise SpecError(f"node has no predicate or combinator: {sorted(node)}")

    comb = combs[0]
    children = node[comb]
    if comb == "not":
        # 'not' takes a single child tree (accept a 1-element list too)
        if isinstance(children, list):
            if len(children) != 1:
                raise SpecError("'not' takes exactly one child")
        else:
            children = [children]
    else:
        if not isinstance(children, list) or not children:
            raise SpecError(f"'{comb}' must be a non-empty list of predicates")

    leaves = params = 0
    max_depth = depth
    for child in children:
        lc, pc, md = _walk(child, depth + 1)
        leaves += lc
        params += pc
        max_depth = max(max_depth, md)
    return leaves, params, max_depth


def count_params(spec: dict) -> int:
    """Number of deliberately-tunable knobs in a spec (the `n_params` fed to the gate).

    Counts leaf thresholds/multipliers/body-fractions plus the spec-level `atr_k`. Excludes
    all conventional lengths and categorical choices (kind/osc). Assumes the spec is at
    least structurally walkable; pairs with `validate_spec` which enforces the ≤5 ceiling.
    """
    total = 0
    for key in ("entry", "exit"):
        tree = spec.get(key)
        if isinstance(tree, dict):
            _, p, _ = _walk(tree, 1)
            total += p
    if "atr_k" in spec:   # the ATR stop multiplier is always a tunable knob
        total += 1
    return total


def _collect_preds(node: Any, out: list[str]) -> None:
    """Collect every leaf predicate name in a tree (combinators ignored — STRUCTURE only)."""
    if not isinstance(node, dict):
        return
    if "pred" in node:
        out.append(node["pred"])
        return
    for comb in COMBINATORS:
        if comb in node:
            children = node[comb]
            children = children if isinstance(children, list) else [children]
            for c in children:
                _collect_preds(c, out)


def _bare(symbol: str) -> str:
    return symbol.split(":", 1)[1] if ":" in symbol else symbol


def novelty_key(spec: dict, symbols: Any = None) -> str:
    """The shared dedup/novelty key (CONTEXT-DIGEST-SPEC §3.1).

    `family | sorted-predicate-structure | symbol-target`, e.g.
    `"mean-reversion|rsi_above+rsi_below|HDFCBANK+SBIN"`. The SAME definition the researcher
    uses to judge "is this idea new?" and the graveyard uses to bucket rejects, so the two
    can never disagree.

    Granularity (the fidelity crux): keyed on the **set of predicate names** (structure),
    NOT their params — so two specs that differ only in thresholds collide (a param tweak is
    the improvement loop's job, not a "new idea"), while a genuinely new variant that changes
    the predicate structure gets its own bucket (never silently suppressed). `symbols` is the
    set the spec was tried on / deployed on; None/empty → `*` (any).
    """
    families = spec.get("families") or []
    fam = "+".join(sorted(str(f) for f in families)) or "none"

    preds: list[str] = []
    for key in ("entry", "exit"):
        _collect_preds(spec.get(key, {}), preds)
    structure = "+".join(sorted(set(preds))) or "empty"

    if symbols:
        syms = "+".join(sorted({_bare(str(s)) for s in symbols}))
    else:
        syms = "*"
    return f"{fam}|{structure}|{syms}"


def validate_spec(spec: dict) -> dict:
    """Validate a strategy spec against the DSL whitelist + structural caps.

    Returns the spec unchanged on success; raises `SpecError` on any violation. This is the
    ONLY gate between LLM-emitted JSON and the compiler — be strict.
    """
    if not isinstance(spec, dict):
        raise SpecError(f"spec must be an object, got {type(spec).__name__}")

    tf = spec.get("timeframe", "day")
    if tf not in ALLOWED_TIMEFRAMES:
        raise SpecError(f"timeframe {tf!r} not in {sorted(ALLOWED_TIMEFRAMES)}")

    atr_k = spec.get("atr_k")
    if atr_k is None or not _is_number(atr_k):
        raise SpecError("atr_k is required and must be numeric")
    if not (ATR_K_BOUNDS[0] <= atr_k <= ATR_K_BOUNDS[1]):
        raise SpecError(f"atr_k={atr_k} out of bounds {list(ATR_K_BOUNDS)}")

    atr_len = spec.get("atr_len", 14)
    if not _is_number(atr_len) or int(atr_len) != atr_len:
        raise SpecError("atr_len must be an integer")
    if not (ATR_LEN_BOUNDS[0] <= atr_len <= ATR_LEN_BOUNDS[1]):
        raise SpecError(f"atr_len={atr_len} out of bounds {list(ATR_LEN_BOUNDS)}")

    sf = spec.get("size_fraction", 1.0)
    if not _is_number(sf) or not (SIZE_FRACTION_BOUNDS[0] < sf <= SIZE_FRACTION_BOUNDS[1]):
        raise SpecError(f"size_fraction={sf} must be in (0, 1.0]")

    total_leaves = total_params = 0
    for key in ("entry", "exit"):
        tree = spec.get(key)
        if not isinstance(tree, dict) or not tree:
            raise SpecError(f"'{key}' is required and must be a non-empty predicate tree")
        leaves, params, depth = _walk(tree, 1)
        if leaves == 0:
            raise SpecError(f"'{key}' tree has no leaf predicates")
        if depth > MAX_DEPTH:
            raise SpecError(f"'{key}' tree depth {depth} > {MAX_DEPTH}")
        total_leaves += leaves
        total_params += params

    if total_leaves > MAX_LEAVES:
        raise SpecError(f"total leaf count {total_leaves} > {MAX_LEAVES}")

    n = total_params + 1  # + atr_k (validated present above)
    if n > MAX_PARAMS:
        raise SpecError(f"tunable param count {n} > {MAX_PARAMS} (overfit ceiling)")

    return spec
