"""Pricing a binary from any CDF curve (ADR-0249 part 2).

The equivalence test builds a curve that IS the averaged-lognormal law (window 0) sampled densely
and requires ``CurveBinaryFairValue`` to agree with ``BinaryFairValue`` to 1e-6.
"""

import enum
import inspect
import math
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dskit.pipeline import binary_curve
from dskit.pipeline.binary_curve import (
    STATUS_OK,
    STATUSES,
    CurveBinaryFairValue,
    CurveSurvival,
    name_ok,
    named_payoff,
    output_collision_problems,
)
from dskit.pipeline.binary_pricing import PAYOFFS, BinaryFairValue
from dskit.pipeline.conformance import NodeProbe, conformance_suite

PARAMS = {
    "payoff_field": "payoff", "lower_field": "lo", "upper_field": "hi", "curve_key_fields": ["curve"],
    "curve_id_fields": ["id"], "values_field": "log_returns", "probabilities_field": "probabilities",
    "axis": "log_return", "reference_field": "spot", "fair_field": "fair",
}


def phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# -- CurveSurvival ---------------------------------------------------------------------------------


def test_survival_interpolates_linearly_between_level_knots():
    curve = CurveSurvival([90.0, 100.0, 110.0], [0.0, 0.4, 1.0])
    assert curve.survival(100.0) == pytest.approx(0.6)
    assert curve.survival(95.0) == pytest.approx(0.8)    # F = 0.2 halfway up the first cell
    assert curve.survival(107.5) == pytest.approx(0.15)  # F = 0.4 + 0.75 * 0.6 = 0.85
    assert curve(90.0) == pytest.approx(1.0) and curve(110.0) == pytest.approx(0.0)


def test_a_log_return_axis_is_read_against_its_reference_level():
    # OptionPriceCDF's knots are log(K / spot): at spot 200, log 0 is K = 200
    curve = CurveSurvival([-0.1, 0.0, 0.1], [0.0, 0.5, 1.0], axis="log_return", reference=200.0)
    assert curve.survival(200.0) == pytest.approx(0.5)
    assert curve.survival(200.0 * math.exp(0.05)) == pytest.approx(0.25)


def test_outside_the_knot_support_is_refused_never_extrapolated():
    curve = CurveSurvival([90.0, 100.0, 110.0], [0.0, 0.4, 1.0])
    assert not curve.covers(89.0) and not curve.covers(111.0) and curve.covers(90.0)
    with pytest.raises(ValueError, match="outside"):
        curve.survival(111.0)


def test_an_atom_counts_toward_survival_at_its_own_level():
    # a tied knot is a jump: F(100-) = 0.3, F(100) = 0.7, so P(S >= 100) = 1 - 0.3
    curve = CurveSurvival([90.0, 100.0, 100.0, 110.0], [0.0, 0.3, 0.7, 1.0])
    assert curve.survival(100.0) == pytest.approx(0.7)
    assert curve.survival(105.0) == pytest.approx(0.15)


@pytest.mark.parametrize("values, probabilities, kwargs", [
    ([1.0], [0.5], {}),
    ([1.0, 0.5], [0.0, 1.0], {}),
    ([1.0, 2.0], [0.6, 0.4], {}),
    ([1.0, 2.0], [0.0, 1.2], {}),
    ([1.0, float("nan")], [0.0, 1.0], {}),
    ([1.0, 2.0, 3.0], [0.0, 1.0], {}),
    ([0.0, 1.0], [0.0, 1.0], {"axis": "log_return"}),
    ([0.0, 1.0], [0.0, 1.0], {"axis": "log_return", "reference": -1.0}),
    ([0.0, 1.0], [0.0, 1.0], {"axis": "level", "reference": 5.0}),
    ([0.0, 1.0], [0.0, 1.0], {"axis": "sideways"}),
])
def test_a_malformed_curve_is_refused(values, probabilities, kwargs):
    assert CurveSurvival.problems(values, probabilities, **kwargs)
    with pytest.raises(ValueError):
        CurveSurvival(values, probabilities, **kwargs)


# -- the node equals BinaryFairValue when the curve IS the averaged lognormal ------------------------

SIGMA, LAG = 0.01, 5
FAIR = {"vol_field": "rv", "spot_field": "spot", "payoff_field": "payoff", "lower_field": "lo",
        "upper_field": "hi", "decision_field": "d", "settle_field": "s", "fair_field": "fair",
        "exec_lag_s": LAG, "averaging_window_s": 0}


def lognormal_curve(variance, n=40001, width=8.0):
    """The CDF of ln(A / spot) ~ Normal(-v/2, v), sampled on n knots over +-width sd."""
    sd = math.sqrt(variance)
    xs = [-variance / 2 + sd * width * (2 * i / (n - 1) - 1) for i in range(n)]
    return xs, [phi((x + variance / 2) / sd) for x in xs]


def test_curve_fair_value_equals_the_closed_form_on_a_densely_sampled_lognormal():
    contracts = [
        {"payoff": "above", "lo": 101.0, "hi": None},
        {"payoff": "below", "lo": None, "hi": 98.5},
        {"payoff": "between", "lo": 99.0, "hi": 102.0},
        {"payoff": "above", "lo": 100.0, "hi": None},
    ]
    base = [{**c, "rv": SIGMA, "spot": 100.0, "d": 0, "s": 405_000, "curve": "k"} for c in contracts]
    closed = BinaryFairValue("f", FAIR).run(None, {"records": base})["records"]
    variance = closed[0]["fair_var"]
    assert variance == pytest.approx(SIGMA**2 * 400.0)
    xs, ps = lognormal_curve(variance)
    curves = [{"id": "k", "log_returns": xs, "probabilities": ps, "spot": 100.0}]
    priced = CurveBinaryFairValue("c", PARAMS).run(None, {"records": base, "curves": curves})["records"]
    for a, b in zip(closed, priced):
        assert b["fair_status"] == STATUS_OK
        assert b["fair"] == pytest.approx(a["fair"], abs=1e-6)


# -- statuses ------------------------------------------------------------------------------------------

CURVE = {"id": "k", "log_returns": [-0.1, 0.0, 0.1], "probabilities": [0.0, 0.5, 1.0], "spot": 100.0,
         "eligible": True}


def run(contracts, curves=(CURVE,), **overrides):
    return CurveBinaryFairValue("c", {**PARAMS, **overrides}).run(None, {"records": contracts, "curves": list(curves)})


def test_rows_say_why_they_were_not_priced():
    bad = {**CURVE, "id": "bad", "probabilities": [0.0, 0.7, 0.5]}
    off = {**CURVE, "id": "off", "eligible": False}
    out = run([
        {"payoff": "above", "lo": 100.0, "curve": "k"},
        {"payoff": "above", "lo": 100.0, "curve": "missing"},
        {"payoff": "above", "lo": 100.0, "curve": "bad"},
        {"payoff": "sideways", "lo": 100.0, "curve": "k"},
        {"payoff": "between", "lo": 101.0, "hi": 99.0, "curve": "k"},
        {"payoff": "above", "lo": 150.0, "curve": "k"},
        {"payoff": "above", "lo": 100.0, "curve": "off"},
    ], curves=(CURVE, bad, off), eligible_field="eligible")
    got = [r["fair_status"] for r in out["records"]]
    assert got == ["ok", "no_curve", "bad_curve", "unknown_payoff", "bad_bounds", "outside_support",
                   "ineligible_curve"]
    assert out["records"][0]["fair"] == pytest.approx(0.5)
    assert all(r["fair"] is None for r in out["records"][1:])
    assert out["census"]["priced"] == 1 and set(out["census"]["by_status"]) == set(STATUSES)


def test_a_level_axis_needs_no_reference_and_refuses_one():
    level = {"id": "k", "levels": [90.0, 100.0, 110.0], "probabilities": [0.0, 0.4, 1.0]}
    params = {**PARAMS, "values_field": "levels", "axis": "level"}
    params.pop("reference_field")
    out = CurveBinaryFairValue("c", params).run(None, {"records": [{"payoff": "below", "hi": 100.0, "curve": "k"}],
                                                       "curves": [level]})
    assert out["records"][0]["fair"] == pytest.approx(0.4)
    assert any("reference_field" in p for p in CurveBinaryFairValue.validate_params({**params, "reference_field": "s"}))
    assert any("reference_field" in p for p in CurveBinaryFairValue.validate_params({**PARAMS, "reference_field": None}))


def test_duplicate_curve_ids_are_refused_at_the_input():
    node = CurveBinaryFairValue("c", PARAMS)
    assert node.validate_inputs({"records": [], "curves": [CURVE, dict(CURVE)]})
    assert node.validate_inputs({"records": [], "curves": [CURVE]}) == []


@pytest.mark.parametrize("name", sorted(PARAMS))
def test_every_required_knob_is_named_when_missing(name):
    assert any(name in p for p in CurveBinaryFairValue.validate_params({k: v for k, v in PARAMS.items() if k != name}))


def test_unknown_knobs_and_overwrites_are_refused():
    assert any("unknown" in p for p in CurveBinaryFairValue.validate_params({**PARAMS, "extrapolate": True}))
    assert any("overwrite" in p for p in CurveBinaryFairValue.validate_params({**PARAMS, "fair_field": "lo"}))


def test_an_atom_at_the_first_knot_counts_toward_survival_there():
    # F jumps 0 -> 0.4 at 90: P(S >= 90) = 1 - F(90-) = 1, not 1 - 0.4
    curve = CurveSurvival([90.0, 90.0, 110.0], [0.0, 0.4, 1.0])
    assert curve.survival(90.0) == pytest.approx(1.0)
    shifted = CurveSurvival([90.0, 110.0], [0.2, 1.0])
    assert shifted.survival(90.0) == pytest.approx(0.8)


def test_a_between_whose_upper_bound_leaves_the_support_is_outside_support():
    # support is spot * exp(+-0.1) = [90.48, 110.52]: lower 100 is inside, upper 115 is not
    out = run([{"payoff": "between", "lo": 100.0, "hi": 115.0, "curve": "k"},
               {"payoff": "between", "lo": 85.0, "hi": 100.0, "curve": "k"}])
    assert [r["fair_status"] for r in out["records"]] == ["outside_support", "outside_support"]


@pytest.mark.parametrize("flag", [1, "yes", [True]])
def test_only_a_literal_true_makes_a_curve_eligible(flag):
    out = run([{"payoff": "above", "lo": 100.0, "curve": "k"}], curves=({**CURVE, "eligible": flag},),
              eligible_field="eligible")
    assert out["records"][0]["fair_status"] == "ineligible_curve"


# -- row_key: the one id/chain/right key rule, scalars only (ADR-0249 R1/R2) -------------------------
#
# The SPEC table. Each row is (a, b, expected): ``same`` and ``different`` say whether two keyable
# cells name one thing; ``refused`` and ``missing`` describe ``a`` (``b`` is then a look-alike that a
# looser rule would have matched it with). The same table runs against ``row_key`` here and end to
# end through every node that keys rows (here, test_digital_bounds.py and test_binary_coherence.py).


class _Right(enum.StrEnum):
    C = "C"


class _LyingRight(enum.StrEnum):
    """A str enum whose ``__str__`` lies: the key is its characters, never ``str(x)``."""

    C = "C"

    def __str__(self):
        return "P"


class _Liar(str):
    """A str subclass whose own ``__str__`` lies."""

    def __str__(self):
        return "P"


class _LyingInt(int):
    """An int subclass whose own ``__index__`` and ``__int__`` lie: the key is its value, never theirs."""

    def __index__(self):
        return 9

    def __int__(self):
        return 9


class Three(enum.IntEnum):
    THREE = 3


#: Stands for a cell whose column the row does not have at all.
ABSENT = object()

try:
    import pandas as _pd
except ImportError:  # pragma: no cover - pandas is optional
    _pd = None

SPEC = [
    ("C", np.str_("C"), "same"), ("C", _Right.C, "same"), (_LyingRight.C, "C", "same"), (_Liar("C"), "C", "same"),
    (7, np.int64(7), "same"), (3, Three.THREE, "same"), (np.int32(5), np.uint8(5), "same"),
    (10 ** 40, 10 ** 40, "same"), (_LyingInt(3), 3, "same"), (" ", " ", "same"),
    (1, "1", "different"), (_LyingRight.C, "P", "different"), (_Liar("C"), "P", "different"), ("a", "A", "different"),
    ("é", "é", "different"),   # NFC vs NFD: pinned DIFFERENT, no Unicode normalisation
    (10 ** 40, 10 ** 40 + 1, "different"), (_LyingInt(3), 9, "different"),
    ("C ", "C", "different"), (" C", "C", "different"),   # read by its characters: no stripping
    (True, 1, "refused"), (np.bool_(True), 1, "refused"), (False, 0, "refused"), (1.0, 1, "refused"),
    (float("nan"), "nan", "refused"), (-0.0, 0, "refused"), (np.float64(2.0), 2, "refused"),
    (Decimal("1"), 1, "refused"), (["X"], "X", "refused"), (("X",), "X", "refused"), ({"k": 1}, "k", "refused"),
    ({"X"}, "X", "refused"), (b"X", "X", "refused"), (datetime(2026, 10, 7), "2026-10-07", "refused"),
    (date(2026, 10, 7), "2026-10-07", "refused"), (object(), "x", "refused"),
    (None, "None", "missing"), ("", "", "missing"), (ABSENT, None, "missing"),
] + ([(_pd.NA, "NA", "refused"), (_pd.NaT, "NaT", "refused")] if _pd is not None else [])

SPEC_IDS = [f"{n}-{e}" for n, (_, _, e) in enumerate(SPEC)]


def spec_cell(value):
    return None if value is ABSENT else value


@pytest.mark.parametrize("a, b, expected", SPEC, ids=SPEC_IDS)
def test_row_key_follows_the_spec_table(a, b, expected):
    key_a, problem_a = binary_curve.row_key(spec_cell(a))
    assert (key_a is None) != (problem_a is None), "exactly one of key and problem is None"
    if expected == "missing":
        assert problem_a == binary_curve.KEY_MISSING == "missing"
        return
    if expected == "refused":
        assert problem_a.startswith("refused ") and type(a).__qualname__ in problem_a
        return
    key_b, problem_b = binary_curve.row_key(b)
    assert problem_a is None and problem_b is None
    assert type(key_a) in (str, int) and type(key_b) in (str, int), "a key is a PLAIN str or int"
    assert (key_a == key_b and type(key_a) is type(key_b)) is (expected == "same")


def _contract_with_key(value, column="curve"):
    row = {"payoff": "above", "lo": 100.0}
    if value is not ABSENT:
        row[column] = value
    return row


@pytest.mark.parametrize("a, b, expected", SPEC, ids=SPEC_IDS)
def test_the_spec_table_end_to_end_through_curve_binary_fair_value(a, b, expected):
    # the contract's key cell is a, the only curve's id is b
    curve = dict(CURVE)
    del curve["id"]
    if b is not ABSENT:
        curve["id"] = b
    status = run([_contract_with_key(a)], curves=(curve,))["records"][0]["fair_status"]
    assert status == {"same": STATUS_OK, "different": "no_curve", "refused": "bad_curve_key",
                      "missing": "no_curve"}[expected]


@pytest.mark.parametrize("a, b, expected", [r for r in SPEC if r[2] in ("same", "different")], ids=[
    i for i, r in zip(SPEC_IDS, SPEC) if r[2] in ("same", "different")])
def test_the_spec_table_decides_duplicate_curve_ids(a, b, expected):
    node = CurveBinaryFairValue("c", PARAMS)
    problems = node.validate_inputs({"records": [], "curves": [{**CURVE, "id": a}, {**CURVE, "id": b}]})
    assert bool(problems) is (expected == "same")


@pytest.mark.parametrize("a, b, expected", [r for r in SPEC if r[2] == "refused"],
                         ids=[i for i, r in zip(SPEC_IDS, SPEC) if r[2] == "refused"])
def test_a_refused_curve_id_is_an_input_problem_named_by_type(a, b, expected):
    problems = CurveBinaryFairValue("c", PARAMS).validate_inputs({"records": [], "curves": [{**CURVE, "id": a}]})
    assert problems and "refused" in problems[0] and type(a).__qualname__ in problems[0]


def test_a_contract_with_no_curve_cell_never_prices_from_a_curve_with_no_id():
    # the (None, None) case: two absences are not one key (canonical JSON once priced this as ok)
    curve = {k: v for k, v in CURVE.items() if k != "id"}
    out = run([{"payoff": "above", "lo": 100.0}, {"payoff": "above", "lo": 100.0, "curve": None},
               {"payoff": "above", "lo": 100.0, "curve": ""}],
              curves=(curve, {**CURVE, "id": None}, {**CURVE, "id": ""}))
    assert [r["fair_status"] for r in out["records"]] == ["no_curve"] * 3
    assert all(r["fair"] is None for r in out["records"])
    assert CurveBinaryFairValue("c", PARAMS).validate_inputs({"records": [], "curves": [curve, dict(curve)]}) == [], (
        "curves with no id are never read, so two of them are not a duplicate")


# declared orders that sorting would NOT keep aligned: the key side sorts reversed, the id side does not
COMPOSITE = {"curve_key_fields": ["und", "day"], "curve_id_fields": ["root", "when"]}


def test_a_composite_curve_key_is_matched_column_by_column_in_declared_order():
    curves = ({**CURVE, "root": "X", "when": 20261016},)
    rows = [{"payoff": "above", "lo": 100.0, "und": np.str_("X"), "day": np.int64(20261016)},
            {"payoff": "above", "lo": 100.0, "und": 20261016, "day": "X"},
            {"payoff": "above", "lo": 100.0, "und": "X", "day": "20261016"},
            {"payoff": "above", "lo": 100.0, "und": "X"},
            {"payoff": "above", "lo": 100.0, "und": "X", "day": 2.0261016e7},
            {"payoff": "above", "lo": 100.0, "und": "", "day": True}]
    out = run(rows, curves=curves, **COMPOSITE)
    # a refused cell outranks a missing one: the last row is bad_curve_key, not no_curve
    assert [r["fair_status"] for r in out["records"]] == [STATUS_OK, "no_curve", "no_curve", "no_curve",
                                                          "bad_curve_key", "bad_curve_key"]


def test_fields_key_names_the_refused_column_and_keeps_declared_order():
    assert binary_curve.fields_key({"a": "X", "b": 2}, ["b", "a"]) == ((2, "X"), None)
    key, problem = binary_curve.fields_key({"a": None, "b": 1.5}, ["a", "b"])
    assert key is None and problem.startswith("column 'b' refused builtins.float")
    assert binary_curve.fields_key({"a": "X"}, ["a", "b"]) == (None, "missing")


def test_a_fair_field_that_would_overwrite_a_key_column_is_refused():
    params = {**PARAMS, "curve_key_fields": ["fair_status"], "curve_id_fields": ["id"]}
    assert any("overwrite" in p for p in CurveBinaryFairValue.validate_params(params))


@pytest.mark.parametrize("bad, needle", [
    ({"curve_key_fields": "curve"}, "curve_key_fields"), ({"curve_id_fields": []}, "curve_id_fields"),
    # equal-length sides, so only the list's own rule can refuse them (a length mismatch would mask it)
    ({"curve_key_fields": [], "curve_id_fields": []}, "non-empty list"),
    ({"curve_key_fields": ["a", ""], "curve_id_fields": ["x", "y"]}, "non-empty list"),
    ({"curve_key_fields": ["a", "b"]}, "same number"), ({"curve_id_fields": ["id", ""]}, "curve_id_fields"),
    ({"curve_key_fields": ["a", "a"], "curve_id_fields": ["x", "y"]}, "more than once"),
    ({"curve_key_field": "curve"}, "unknown")])
def test_curve_key_field_lists_are_refused_by_name_when_malformed(bad, needle):
    assert any(needle in p for p in CurveBinaryFairValue.validate_params({**PARAMS, **bad}))


def test_the_nested_key_machinery_is_gone():
    assert not hasattr(binary_curve, "canonical_key") and not hasattr(binary_curve, "_key_or_problem")
    assert {"row_key", "fields_key", "KEY_MISSING"} <= set(binary_curve.__all__)
    source = inspect.getsource(binary_curve)
    assert "_canonical_bytes" not in source and "dskit.pipeline.trust" not in source


def _spellings(base):
    """Strategies for ways one base scalar may be spelled in a cell."""
    if isinstance(base, str):
        kinds = [st.just(base), st.just(np.str_(base)), st.just(_Liar(base)),
                 st.just(enum.StrEnum("S", {"V": base}).V)]
    else:
        kinds = [st.just(base), st.just(type("Sub", (int,), {})(base)), st.just(enum.IntEnum("I", {"V": base}).V)]
        if -(2 ** 63) <= base < 2 ** 63:
            kinds.append(st.just(np.int64(base)))
    return st.one_of(kinds)


_BASES = st.one_of(st.text(min_size=1), st.integers(min_value=-(10 ** 30), max_value=10 ** 30))


@settings(max_examples=300, deadline=None)
@given(data=st.data(), x=_BASES, y=_BASES)
def test_values_equal_by_the_spec_share_a_key_and_different_ones_never_do(data, x, y):
    a1, a2 = data.draw(_spellings(x)), data.draw(_spellings(x))
    b = data.draw(_spellings(y))
    key_a1, key_a2, key_b = (binary_curve.row_key(v)[0] for v in (a1, a2, b))
    assert key_a1 is not None and key_a1 == key_a2 and type(key_a1) is type(key_a2)
    same = type(x) is type(y) and x == y
    assert (key_a1 == key_b and type(key_a1) is type(key_b)) is same


def test_the_module_declares_its_public_api():
    assert CurveBinaryFairValue.role == "transform"
    assert CurveBinaryFairValue.outputs == ("records", "census")
    assert not [n for n in binary_curve.__all__ if n.startswith("_")]


class _SameHashAsAbove:
    """Not a str, yet it hashes and compares equal to the payoff name ``"above"``."""

    def __hash__(self):
        return hash("above")

    def __eq__(self, other):
        return other == "above"


class _ShoutingName(enum.StrEnum):
    ABOVE = "above"


@pytest.mark.parametrize("value", ["fair", "x", np.str_("fair"), _Liar("fair"), _ShoutingName.ABOVE])
def test_name_ok_accepts_a_non_empty_string_of_any_str_type(value):
    assert name_ok(value) is True


@pytest.mark.parametrize("value", ["", None, 0, 3, True, 1.5, b"fair", ["fair"], ("fair",), {"fair"}, {}, object()])
def test_name_ok_refuses_everything_else(value):
    assert name_ok(value) is False


@pytest.mark.parametrize("value, expected", [
    ("above", "above"), ("below", "below"), ("between", "between"),
    (np.str_("above"), "above"), (_Liar("below"), "below"), (_ShoutingName.ABOVE, "above")])
def test_named_payoff_finds_the_geometry_a_str_cell_names(value, expected):
    assert named_payoff(value) is PAYOFFS[expected]


@pytest.mark.parametrize("value", ["sideways", "", "ABOVE", None, 3, 1.5, True, ["above"], {"above"}, {}, b"above",
                                   np.array("above"), _SameHashAsAbove()])
def test_named_payoff_finds_none_for_anything_that_is_not_a_known_str(value):
    assert named_payoff(value) is None


def test_named_payoff_refuses_an_unhashable_str_instead_of_raising():
    class Unhashable(str):
        __hash__ = None

    assert named_payoff(Unhashable("above")) is None
    out = run([{"payoff": Unhashable("above"), "lo": 100.0, "curve": "k"}])
    assert out["records"][0]["fair_status"] == "unknown_payoff"


def test_named_payoff_defers_to_the_pricing_owner_for_the_lookup():
    from dskit.pipeline.binary_pricing import payoff

    assert all(named_payoff(name) is payoff(name) for name in PAYOFFS)
    with pytest.raises(ValueError, match="unknown payoff"):
        payoff("sideways")


_ABOUT_NAMES = ["payoff", "lo", "hi", "curve", "fair_status", None, 3, ["curve"]]


def test_output_collision_problems_names_the_clashing_columns_in_the_one_message():
    got = output_collision_problems("fair_field", "lo", ("lo", "lo_status"), ["payoff", "lo", "hi"])
    assert got == ["fair_field 'lo' writes ['lo', 'lo_status'], which would overwrite the input column(s) ['lo']"]
    both = output_collision_problems("bound_field", "x", {"x_status", "x", "x_lower"}, ["x_lower", "x", "y"])
    assert both == ["bound_field 'x' writes ['x', 'x_lower', 'x_status'], which would overwrite the input "
                    "column(s) ['x', 'x_lower']"]


def test_output_collision_problems_is_empty_when_no_input_is_overwritten():
    assert output_collision_problems("fair_field", "fair", ("fair", "fair_status"), ["payoff", "lo", "hi"]) == []
    assert output_collision_problems("fair_field", "fair", ("fair", "fair_status"), []) == []


def test_output_collision_problems_ignores_a_value_that_is_not_a_column_name():
    # an unusable knob is its own problem; here it must neither collide nor raise (a list is unhashable)
    got = output_collision_problems("fair_field", "fair", ("fair", "fair_status"), _ABOUT_NAMES)
    assert got == ["fair_field 'fair' writes ['fair', 'fair_status'], which would overwrite the input column(s) "
                   "['fair_status']"]
    assert output_collision_problems("fair_field", "fair", ("fair",), [None, 3, ["fair"], ("fair",), b"fair"]) == []


def test_each_node_reports_its_collision_through_the_one_message():
    got = CurveBinaryFairValue.validate_params({**PARAMS, "curve_key_fields": ["fair_status"],
                                                "curve_id_fields": ["id"]})
    assert ["fair_field 'fair' writes ['fair', 'fair_status'], which would overwrite the input column(s) "
            "['fair_status']"] == [p for p in got if "overwrite" in p]
    got = CurveBinaryFairValue.validate_params({**PARAMS, "fair_field": "lo"})
    assert ["fair_field 'lo' writes ['lo', 'lo_status'], which would overwrite the input column(s) ['lo']"
            ] == [p for p in got if "overwrite" in p]


def _all_order(name):
    """Sort key of ruff's ``__all__`` order: CONSTANTS, then Classes, then functions."""
    return (0 if name.isupper() else 1 if name[0].isupper() else 2, name)


def test_the_curve_node_uses_the_owners_of_the_name_payoff_and_collision_rules():
    source = inspect.getsource(binary_curve)
    assert "_name_ok" not in source and "PAYOFFS.get" not in source
    assert source.count("def name_ok") == 1 and source.count("def named_payoff") == 1
    assert source.count("def output_collision_problems") == 1
    assert source.count("overwrite the input column(s) {clash}") == 1 and source.count("clash =") == 1
    assert {"name_ok", "named_payoff", "output_collision_problems"} <= set(binary_curve.__all__)
    assert list(binary_curve.__all__) == sorted(binary_curve.__all__, key=_all_order)


def _probes(tmp_path):
    return {"curve-binary-fair-value": NodeProbe(
        params=dict(PARAMS), required=tuple(PARAMS),
        inputs={"records": [{"payoff": "above", "lo": 100.0, "curve": "k"}], "curves": [dict(CURVE)]},
        stream_ports=("records", "curves"), runnable=True)}


TestCurveBinaryFairValueConformance = conformance_suite(
    registry=(("curve-binary-fair-value", CurveBinaryFairValue),), module="dskit.pipeline.binary_curve",
    probes=_probes, expected_roles={"curve-binary-fair-value": "transform"},
    name="TestCurveBinaryFairValueConformance")
