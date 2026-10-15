"""ComputeFields (ADR-0265 Amendment 5): per-row arithmetic over declared fields, null in, null out.

Every expectation is written out by hand. The node is wired by import path and is not a registered
kind. A value that cannot be computed is None and COUNTED, never filled or guessed.
"""

import math

import pytest

from dskit.pipeline.kinds_compute import ComputeFields
from dskit.pipeline.node import DEFAULT_NODE_KINDS, ConfigError, NodeContext


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="calc", asof="2025-12-31", run_dir=str(tmp_path))


def run(ctx, rows, fields, **over):
    return ComputeFields("calc", {"fields": fields, **over}).run(ctx, {"records": rows})


def f(name):
    return {"field": name}


def test_the_four_operations_and_a_constant_by_hand(ctx):
    rows = [{"a": 6.0, "b": 3.0}]
    out = run(ctx, rows, {
        "sum": {"op": "add", "args": [f("a"), f("b"), {"const": 1}]},
        "diff": {"op": "sub", "args": [f("a"), f("b")]},
        "prod": {"op": "mul", "args": [f("a"), f("b"), {"const": 0.5}]},
        "ratio": {"op": "div", "args": [f("a"), f("b")]}})
    assert out["records"] == [{"a": 6.0, "b": 3.0, "sum": 10.0, "diff": 3.0, "prod": 9.0, "ratio": 2.0}]


def test_the_unary_functions_and_the_extremes(ctx):
    rows = [{"x": 4.0, "y": -2.0}]
    out = run(ctx, rows, {
        "log": {"op": "log", "args": [f("x")]}, "exp": {"op": "exp", "args": [{"const": 0}]},
        "sqrt": {"op": "sqrt", "args": [f("x")]}, "abs": {"op": "abs", "args": [f("y")]},
        "neg": {"op": "neg", "args": [f("y")]}, "lo": {"op": "min", "args": [f("x"), f("y")]},
        "hi": {"op": "max", "args": [f("x"), f("y"), {"const": 9}]}})
    row = out["records"][0]
    assert row["log"] == pytest.approx(math.log(4.0)) and row["exp"] == 1.0 and row["sqrt"] == 2.0
    assert (row["abs"], row["neg"], row["lo"], row["hi"]) == (2.0, 2.0, -2.0, 9.0)


def test_a_nested_expression_is_evaluated_inside_out(ctx):
    # log(a / b) - 0.5 * c
    rows = [{"a": 8.0, "b": 2.0, "c": 4.0}]
    out = run(ctx, rows, {"z": {"op": "sub", "args": [
        {"op": "log", "args": [{"op": "div", "args": [f("a"), f("b")]}]},
        {"op": "mul", "args": [{"const": 0.5}, f("c")]}]}})
    assert out["records"][0]["z"] == pytest.approx(math.log(4.0) - 2.0)


@pytest.mark.parametrize("row", [{"a": None, "b": 1.0}, {"a": float("nan"), "b": 1.0}, {"b": 1.0},
                                 {"a": True, "b": 1.0}, {"a": "x", "b": 1.0},
                                 {"a": float("inf"), "b": 1.0}])
def test_a_missing_null_nonfinite_boolean_or_text_input_gives_none_and_is_counted(ctx, row):
    out = run(ctx, [row], {"z": {"op": "add", "args": [f("a"), f("b")]}})
    assert out["records"][0]["z"] is None
    assert out["summary"]["fields"]["z"] == {"computed": 0, "null_input": 1, "domain": 0}


@pytest.mark.parametrize("op, a, b", [("div", 1.0, 0.0), ("log", 0.0, None), ("log", -1.0, None),
                                      ("sqrt", -1.0, None), ("exp", 1000.0, None)])
def test_a_domain_error_gives_none_and_is_counted_apart_from_a_null(ctx, op, a, b):
    args = [{"const": a}] + ([] if b is None else [{"const": b}])
    out = run(ctx, [{"k": 1}], {"z": {"op": op, "args": args}})
    assert out["records"][0]["z"] is None
    assert out["summary"]["fields"]["z"] == {"computed": 0, "null_input": 0, "domain": 1}


def test_a_bare_field_copies_its_value_whatever_its_type_so_a_rename_is_a_copy(ctx):
    out = run(ctx, [{"entry_date": "2020-01-02"}, {"entry_date": None}], {"date": f("entry_date")})
    assert [r["date"] for r in out["records"]] == ["2020-01-02", None]
    assert out["summary"]["fields"]["date"] == {"computed": 1, "null_input": 1, "domain": 0}


def test_the_input_rows_are_not_mutated_and_order_is_kept(ctx):
    rows = [{"a": 1.0}, {"a": 2.0}]
    out = run(ctx, rows, {"b": {"op": "mul", "args": [f("a"), {"const": 2}]}})
    assert rows == [{"a": 1.0}, {"a": 2.0}]
    assert [r["b"] for r in out["records"]] == [2.0, 4.0]


def test_a_field_already_on_the_row_refuses_unless_overwrite_is_declared(ctx):
    rows = [{"a": 1.0, "b": 5.0}]
    with pytest.raises(ValueError, match="already carries"):
        run(ctx, rows, {"b": f("a")})
    assert run(ctx, rows, {"b": f("a")}, overwrite=True)["records"][0]["b"] == 1.0


def test_a_row_that_is_not_a_mapping_refuses(ctx):
    with pytest.raises(ValueError, match="mapping"):
        run(ctx, [1.0], {"b": {"const": 1}})


def test_the_summary_counts_rows_and_each_field(ctx):
    rows = [{"a": 1.0}, {"a": 0.0}, {"a": None}]
    out = run(ctx, rows, {"inv": {"op": "div", "args": [{"const": 1}, f("a")]}})
    assert out["summary"]["rows_in"] == 3
    assert out["summary"]["fields"]["inv"] == {"computed": 1, "null_input": 1, "domain": 1}


@pytest.mark.parametrize("bad, text", [
    ({}, "non-empty dict"),
    ({"z": {"op": "pow", "args": [{"const": 1}, {"const": 2}]}}, "unknown op"),
    ({"z": {"op": "div", "args": [{"const": 1}]}}, "exactly 2"),
    ({"z": {"op": "log", "args": []}}, "exactly 1"),
    ({"z": {"op": "add", "args": [{"const": 1}]}}, "at least 2"),
    ({"z": {"const": True}}, "finite number"),
    ({"z": {"field": ""}}, "non-empty"),
    ({"z": {"field": "a", "const": 1}}, "exactly one"),
    ({"z": {"op": "add", "args": [{"const": 1}, {"const": 2}], "x": 1}}, "unknown key"),
    ({"": {"const": 1}}, "output name"),
])
def test_a_bad_expression_is_refused_by_name(bad, text):
    problems = ComputeFields.validate_params({"fields": bad})
    assert any(text in p for p in problems), problems


def test_unknown_knobs_and_a_non_boolean_overwrite_are_refused():
    problems = ComputeFields.validate_params({"fields": {"z": {"const": 1}}, "extra": 1, "overwrite": "yes"})
    assert any("extra" in p for p in problems) and any("overwrite" in p for p in problems)


def test_it_is_not_a_registered_kind_and_is_pure_for_serving():
    assert "compute-fields" not in DEFAULT_NODE_KINDS.kinds()
    assert ComputeFields.serving_effect({}, {}) == "pure"


def test_a_bad_declaration_refuses_at_construction():
    with pytest.raises(ConfigError):
        ComputeFields("calc", {"fields": {"z": {"op": "pow", "args": []}}})


def test_keep_projects_the_rows_to_exactly_the_named_fields_plus_the_computed_ones(ctx):
    rows = [{"a": 1.0, "b": 2.0, "junk": "x"}]
    out = run(ctx, rows, {"c": {"op": "add", "args": [f("a"), f("b")]}}, keep=["b", "a"])
    assert out["records"] == [{"b": 2.0, "a": 1.0, "c": 3.0}]
    assert list(out["records"][0]) == ["b", "a", "c"]


def test_keep_refuses_a_row_missing_a_kept_field_and_a_clash_with_a_computed_name(ctx):
    with pytest.raises(ValueError, match="keep"):
        run(ctx, [{"a": 1.0}], {"c": {"const": 1}}, keep=["a", "zz"])
    with pytest.raises(ValueError, match="already carries"):
        run(ctx, [{"a": 1.0}], {"a": {"const": 1}}, keep=["a"])
    assert run(ctx, [{"a": 1.0}], {"a": {"const": 2}}, keep=["a"], overwrite=True)["records"] == [{"a": 2.0}]


def test_a_bad_keep_is_refused_by_name():
    for bad in ([], "a", [1], ["a", "a"]):
        assert any("keep" in p for p in ComputeFields.validate_params({"fields": {"z": {"const": 1}}, "keep": bad}))


def test_a_text_constant_and_a_list_of_numbers_can_be_whole_expressions(ctx):
    rows = [{"k": 100.0, "j": None}, {"k": 90.0, "j": 5.0}]
    out = run(ctx, rows, {
        "structure": {"text": "long_straddle"},
        "strikes": {"list": [f("k"), f("k")]},
        "wide": {"list": [f("k"), {"op": "add", "args": [f("k"), f("j")]}]}})
    assert [r["structure"] for r in out["records"]] == ["long_straddle"] * 2
    assert [r["strikes"] for r in out["records"]] == [[100.0, 100.0], [90.0, 90.0]]
    assert [r["wide"] for r in out["records"]] == [None, [90.0, 95.0]]
    assert out["summary"]["fields"]["wide"] == {"computed": 1, "null_input": 1, "domain": 0}


@pytest.mark.parametrize("bad, text", [
    ({"z": {"text": 3}}, "text"), ({"z": {"text": ""}}, "text"), ({"z": {"list": []}}, "list"),
    ({"z": {"op": "add", "args": [{"const": 1}, {"list": [{"const": 1}]}]}}, "list")])
def test_a_bad_text_or_list_is_refused_by_name(bad, text):
    assert any(text in p for p in ComputeFields.validate_params({"fields": bad})), bad


def test_item_reads_one_number_out_of_a_list_field(ctx):
    rows = [{"strikes": [97.0, 104.0]}, {"strikes": [90.0]}, {"strikes": None}]
    out = run(ctx, rows, {"low": {"item": {"field": "strikes", "index": 0}},
                          "high": {"op": "sub", "args": [{"item": {"field": "strikes", "index": 1}}, {"const": 4}]}})
    assert [r["low"] for r in out["records"]] == [97.0, 90.0, None]
    assert [r["high"] for r in out["records"]] == [100.0, None, None]


@pytest.mark.parametrize("bad", [{"item": {"field": "s"}}, {"item": {"field": "s", "index": -1}},
                                 {"item": {"field": "", "index": 0}}, {"item": {"field": "s", "index": 0, "x": 1}}, {"item": 3}])
def test_a_bad_item_is_refused(bad):
    assert any("item" in p for p in ComputeFields.validate_params({"fields": {"z": bad}})), bad


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_bare_field_that_is_nan_or_infinite_is_missing_and_counted(ctx, value):
    out = run(ctx, [{"a": value}, {"a": 2.0}], {"b": f("a")})
    assert [r["b"] for r in out["records"]] == [None, 2.0]
    assert out["summary"]["fields"]["b"] == {"computed": 1, "null_input": 1, "domain": 0}


def test_coalesce_takes_the_first_expression_with_a_value(ctx):
    rows = [{"bar": 0.02, "vendor": 0.05}, {"bar": None, "vendor": 0.05}, {"bar": float("nan"), "vendor": None}]
    out = run(ctx, rows, {"move": {"coalesce": [{"op": "abs", "args": [f("bar")]}, {"op": "abs", "args": [f("vendor")]}]}})
    assert [r["move"] for r in out["records"]] == [0.02, 0.05, None]
    assert out["summary"]["fields"]["move"] == {"computed": 2, "null_input": 1, "domain": 0}
    assert any("coalesce" in p for p in ComputeFields.validate_params({"fields": {"z": {"coalesce": [{"const": 1}]}}}))
