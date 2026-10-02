"""Gates for ``family-availability`` (ADR-0226): no literals, wiring, step-2 parity.

Companion to ``test_family_availability.py`` (the behaviour contract). Three
things are pinned here:

* the kind's module names no family, schema field or family count as a literal
  (behaviour comes from the document, never the code);
* the kind is wired the way ``kinds_table`` is (registry, default kinds,
  package export, idempotent ``register``, stdlib-only imports);
* the new ``families`` spec reproduces the semantics of the ``check_*`` rules in
  ``run-step2-feature-availability.json`` (finite fields, companion
  ``_missing == 0``, age ``0 <= age <= 7``, ``rn_proxy_eligible == 1``,
  ``chain_node_NN_mask == 1``) on a small fixture panel. The oracle below is
  an independent restatement of those rules, deliberately not read from the
  node, and the old graph is never executed.
"""

import ast
import json
import math
import pathlib
import random
import sys

import pytest

import dskit.pipeline as pipeline_pkg
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node, NodeKindRegistry

ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE = ROOT / "dskit" / "pipeline" / "kinds_availability.py"
OLD_CONFIG = ROOT / "children" / "index_options" / "configs" / "run-step2-feature-availability.json"
KIND = "family-availability"

# Names the contract tests and the fixtures below use; none may be typed in code.
FIXTURE_NAMES = {
    "alpha", "beta", "gamma", "zeta", "sym", "date", "g1", "a1", "a2", "b1",
    "a1_missing", "a1_age", "b1_missing", "b1_age",
}  # fmt: skip
# The old step's family count, and the 2**count combination rows per ticker.
FORBIDDEN_INTS = {13, 8192}


def _old_contract():
    doc = json.loads(OLD_CONFIG.read_text())
    return doc["pipeline"]["family_contracts"]["params"]["tables"]["families"]


def _forbidden_strings():
    names = set(FIXTURE_NAMES)
    for fam, body in _old_contract().items():
        names.add(fam)
        names.update(body["fields"])
        names.update(q["field"] for q in body["quality_checks"])
    names.update({"symbol", "quote_date", "available_implied_cdf"})
    return names


def _code_constants(tree):
    """Yield every Constant node that is not a docstring."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in docstrings:
            yield node


# -- (1) no literals ------------------------------------------------------------


def test_module_exists():
    assert MODULE.is_file(), "dskit/pipeline/kinds_availability.py is missing"


def test_no_family_field_or_count_literal_in_the_module():
    tree = ast.parse(MODULE.read_text())
    bad_strings = _forbidden_strings()
    hits = []
    for const in _code_constants(tree):
        v = const.value
        if isinstance(v, str) and v in bad_strings:
            hits.append((const.lineno, v))
        if isinstance(v, int) and not isinstance(v, bool) and v in FORBIDDEN_INTS:
            hits.append((const.lineno, v))
    assert hits == []


def test_module_imports_stdlib_or_dskit_only():
    tree = ast.parse(MODULE.read_text())
    allowed = set(sys.stdlib_module_names) | {"dskit"}
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add((node.module or "").split(".")[0])
    assert roots <= allowed, roots - allowed


def test_module_declares_its_public_api():
    tree = ast.parse(MODULE.read_text())
    names = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            names = {e.value for e in node.value.elts}
    assert names is not None and {"FamilyAvailability", "register"} <= names
    assert not any(n.startswith("_") for n in names)


# -- (2) wiring, as kinds_table is wired ---------------------------------------


def test_kind_is_in_the_default_registry():
    from dskit.pipeline.kinds_availability import FamilyAvailability

    assert KIND in DEFAULT_NODE_KINDS
    assert DEFAULT_NODE_KINDS.get(KIND) == (FamilyAvailability, False)
    assert issubclass(FamilyAvailability, Node)


def test_register_adds_the_kind_and_is_idempotent():
    from dskit.pipeline.kinds_availability import FamilyAvailability, register

    reg = register(NodeKindRegistry())
    assert reg.get(KIND) == (FamilyAvailability, False)
    assert KIND in set(reg.kinds())
    register(reg)  # idempotent, never shadowing
    assert reg.get(KIND)[0] is FamilyAvailability


def test_register_does_not_shadow_an_existing_kind():
    from dskit.pipeline.kinds_availability import register

    class Mine(Node):
        role = "transform"

        def run(self, ctx, inputs):
            return {}

    reg = NodeKindRegistry()
    reg.register(KIND, Mine)
    register(reg)
    assert reg.get(KIND)[0] is Mine


def test_package_exports_the_node():
    from dskit.pipeline.kinds_availability import FamilyAvailability

    assert pipeline_pkg.FamilyAvailability is FamilyAvailability


def test_kind_is_resolvable_by_document_name():
    from dskit.pipeline.kinds_availability import FamilyAvailability

    cls, _ = DEFAULT_NODE_KINDS.get(KIND)
    node = cls(
        "fa",
        params={
            "families": {"f": {"fields": ["x"]}},
            "schema_fields": ["d", "x"],
            "date_field": "d",
        },
    )
    assert isinstance(node, FamilyAvailability)
    assert node.run(None, {"records": [{"d": "2026-01-02", "x": 1.0}]})["dates"] == [
        {"d": "2026-01-02", "available_f": "yes"}
    ]


# -- (3) parity with the step-2 check_* rules ------------------------------------

FLOAT_MAX = sys.float_info.max
NODES = 3
CHAIN = [f"chain_node_{i:02d}_{leaf}" for i in range(NODES) for leaf in ("iv", "log_oi")]
MASKS = [f"chain_node_{i:02d}_mask" for i in range(NODES)]
CDF = ["rn_q_0100", "rn_q_5000", "rn_q_9900"]
VOL = ["own_iv", "market_vix9d", "market_gvz"]
COMPANIONED = ["market_vix9d", "market_gvz"]  # own_iv has no _missing/_age_days column
RET = ["ret_lag_0", "ret_lag_1"]
KEYS = ["symbol", "quote_date"]
SCHEMA = (
    KEYS + CDF + ["rn_proxy_eligible"] + RET + VOL + CHAIN + MASKS
    + [f"{c}_missing" for c in COMPANIONED] + [f"{c}_age_days" for c in COMPANIONED]
)  # fmt: skip
MAX_AGE = 7  # the old config's "last observation within 7 calendar days"

FAMILIES = {
    "implied_cdf": {
        "fields": CDF,
        "require": [{"field": "rn_proxy_eligible", "op": "==", "value": 1}],
    },
    "return_history": {"fields": RET},
    "volatility_context": {"fields": VOL, "max_age_days": MAX_AGE},
    "chain_nodes": {
        "fields": CHAIN,
        "require": [{"field": m, "op": "==", "value": 1} for m in MASKS],
    },
}


def _finite_when(field):
    """The old rule: ``field >= -max`` and ``field <= max`` (a derive `when` list)."""
    return [
        {"field": field, "op": ">=", "value": -FLOAT_MAX},
        {"field": field, "op": "<=", "value": FLOAT_MAX},
    ]


def old_rules():
    """The old graph's per-family ``when`` conditions, restated independently."""
    rules = {"implied_cdf": [], "return_history": [], "volatility_context": [], "chain_nodes": []}
    for f in CDF:
        rules["implied_cdf"] += _finite_when(f)
    rules["implied_cdf"].append({"field": "rn_proxy_eligible", "op": "==", "value": 1})
    for f in RET:
        rules["return_history"] += _finite_when(f)
    for f in VOL:
        rules["volatility_context"] += _finite_when(f)
    for c in COMPANIONED:
        rules["volatility_context"].append({"field": c + "_missing", "op": "==", "value": 0})
    for c in COMPANIONED:
        rules["volatility_context"] += [
            {"field": c + "_age_days", "op": ">=", "value": 0},
            {"field": c + "_age_days", "op": "<=", "value": MAX_AGE},
        ]
    for f in CHAIN:
        rules["chain_nodes"] += _finite_when(f)
    for m in MASKS:
        rules["chain_nodes"].append({"field": m, "op": "==", "value": 1})
    return rules


def _holds(cond, rec):
    v = rec.get(cond["field"])
    if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v):
        return False
    t = cond["value"]
    return {
        "==": v == t, ">=": v >= t, "<=": v <= t, ">": v > t, "<": v < t, "!=": v != t,
    }[cond["op"]]  # fmt: skip


def oracle(rows):
    """Per (symbol, quote_date) and family: yes only if every row passes every rule."""
    rules = old_rules()
    seen = {}
    for rec in rows:
        key = (rec["symbol"], rec["quote_date"])
        out = seen.setdefault(key, {f: True for f in rules})
        for fam, conds in rules.items():
            out[fam] = out[fam] and all(_holds(c, rec) for c in conds)
    return {k: {f: "yes" if ok else "no" for f, ok in v.items()} for k, v in sorted(seen.items())}


def good_row(symbol="AAA", date="2026-03-02"):
    r = {"symbol": symbol, "quote_date": date, "rn_proxy_eligible": 1}
    for f in CDF + RET + VOL + CHAIN:
        r[f] = 0.5
    for m in MASKS:
        r[m] = 1
    for c in COMPANIONED:
        r[c + "_missing"] = 0
        r[c + "_age_days"] = 2
    return r


def run_node(rows, **over):
    from dskit.pipeline.kinds_availability import FamilyAvailability

    p = {
        "families": FAMILIES,
        "schema_fields": SCHEMA,
        "date_field": "quote_date",
        "group_keys": ["symbol"],
        "missing_suffix": "_missing",
        "age_suffix": "_age_days",
    }
    p.update(over)
    return FamilyAvailability("step2", params=p).run(None, {"records": rows})


def node_flags(rows):
    out = {}
    for r in run_node(rows)["dates"]:
        out[(r["symbol"], r["quote_date"])] = {
            f: r["available_" + f] for f in FAMILIES
        }
    return out


def test_old_contract_is_what_this_parity_test_assumes():
    """Pins the spec the oracle restates: if the old config changes, this fails."""
    contract = _old_contract()
    assert {"rn_proxy_eligible"} == {q["field"] for q in contract["implied_cdf"]["quality_checks"]}
    vc = {(q["op"], q["value"]) for q in contract["volatility_context"]["quality_checks"]}
    assert vc == {("==", 0), (">=", 0), ("<=", MAX_AGE)}
    chain = _old_derive("check_chain_nodes")
    assert {w["field"] for w in chain if w["field"].endswith("_mask")} >= {MASKS[0]}
    assert {w["value"] for w in chain if w["field"].endswith("_mask")} == {1}
    assert FLOAT_MAX == _old_derive("check_return_history")[1]["value"]


def _old_derive(name):
    doc = json.loads(OLD_CONFIG.read_text())
    node = doc["foreach"]["pipeline"][name]
    return node["params"]["cases"][0]["when"]


def test_clean_row_is_available_everywhere():
    got = node_flags([good_row()])
    assert got == {("AAA", "2026-03-02"): dict.fromkeys(FAMILIES, "yes")}
    assert got == oracle([good_row()])


@pytest.mark.parametrize(
    "mutation, family",
    [
        ({"rn_q_5000": float("nan")}, "implied_cdf"),
        ({"rn_q_0100": float("inf")}, "implied_cdf"),
        ({"rn_q_9900": None}, "implied_cdf"),
        ({"rn_proxy_eligible": 0}, "implied_cdf"),
        ({"rn_proxy_eligible": None}, "implied_cdf"),
        ({"ret_lag_1": float("-inf")}, "return_history"),
        ({"own_iv": float("nan")}, "volatility_context"),
        ({"market_vix9d_missing": 1}, "volatility_context"),
        ({"market_gvz_age_days": 8}, "volatility_context"),
        ({"market_gvz_age_days": -1}, "volatility_context"),
        ({"market_gvz_age_days": MAX_AGE}, None),
        ({"market_gvz_age_days": 0}, None),
        ({"chain_node_01_mask": 0}, "chain_nodes"),
        ({"chain_node_02_iv": float("nan")}, "chain_nodes"),
    ],
)
def test_single_rule_breaks_exactly_its_family(mutation, family):
    row = {**good_row(), **mutation}
    got = node_flags([row])[("AAA", "2026-03-02")]
    want = dict.fromkeys(FAMILIES, "yes")
    if family:
        want[family] = "no"
    assert got == want
    assert node_flags([row]) == oracle([row])


def test_own_iv_without_companion_columns_is_only_finite_checked():
    row = {**good_row(), "own_iv": 3.0}
    assert node_flags([row])[("AAA", "2026-03-02")]["volatility_context"] == "yes"


def test_a_failing_row_fails_the_whole_date():
    rows = [good_row(), {**good_row(), "chain_node_00_mask": 0}, good_row(date="2026-03-03")]
    got = node_flags(rows)
    assert got[("AAA", "2026-03-02")]["chain_nodes"] == "no"
    assert got[("AAA", "2026-03-03")]["chain_nodes"] == "yes"
    assert got == oracle(rows)


def _random_row(rng, symbol, date):
    r = good_row(symbol, date)
    bad_values = [float("nan"), float("inf"), None]
    for f in CDF + RET + VOL + CHAIN:
        if rng.random() < 0.04:
            r[f] = rng.choice(bad_values)
    if rng.random() < 0.15:
        r["rn_proxy_eligible"] = rng.choice([0, 1, None])
    for m in MASKS:
        if rng.random() < 0.05:
            r[m] = 0
    for c in COMPANIONED:
        if rng.random() < 0.1:
            r[c + "_missing"] = rng.choice([0, 1])
        if rng.random() < 0.2:
            r[c + "_age_days"] = rng.choice([-2, 0, 3, 7, 8, 30])
    return r


def test_random_panel_matches_the_old_rules():
    rng = random.Random(226)
    rows = [
        _random_row(rng, sym, f"2026-04-{d:02d}")
        for sym in ("AAA", "BBB")
        for d in range(1, 29)
        for _ in range(rng.choice([1, 2, 3]))
    ]
    rng.shuffle(rows)
    got = node_flags(rows)
    want = oracle(rows)
    assert got == want
    seen = {v for flags in want.values() for v in flags.values()}
    assert seen == {"yes", "no"}  # the panel exercises both outcomes


def test_combinations_count_cohort_dates_per_old_pattern_rule():
    rng = random.Random(7)
    rows = [_random_row(rng, "AAA", f"2026-05-{d:02d}") for d in range(1, 29)]
    out = run_node(rows)
    want = oracle(rows)
    names = sorted(FAMILIES)
    assert len(out["combinations"]) == 2 ** len(names)
    for rec in out["combinations"]:
        dates = sum(all(flags[n] == "yes" for n in rec["combination"]) for flags in want.values())
        assert rec["dates"] == dates
    assert out["cohort"][0]["dates"] == len(want)
