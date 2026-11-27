"""Focused contract checks for the static formulation declaration."""

from copy import deepcopy
from decimal import Decimal
import itertools
import json
from pathlib import Path
import subprocess
import sys

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.formulation import validate_formulation


EXAMPLE = Path(__file__).resolve().parents[2] / "examples/pipeline/formulation-facilities.json"


def expr(text="0", *, refs=(), over=()):
    return {"text": text, "refs": list(refs), "over": list(over)}


def decision(name="x", *, axes=(), domain="real", lower=0, upper=None):
    return {
        "id": name, "axes": list(axes), "domain": domain, "units": "1",
        "lower": lower, "upper": upper, "definition": "A variable to choose.",
    }


def derived(name, refs=(), *, axes=(), over=()):
    return {
        "id": name, "axes": list(axes), "units": "1",
        "expression": expr(name, refs=refs, over=over),
    }


def row(name="cap", *, forall=(), lhs=None, relation="<=", rhs=None):
    return {
        "id": name, "forall": list(forall), "lhs": lhs if lhs is not None else expr(),
        "relation": relation, "rhs": rhs if rhs is not None else expr(),
        "definition": "An admissible row.",
    }


@pytest.fixture
def minimal():
    return {
        "schema_version": 1,
        "purpose": "Choose one scalar.",
        "sets": [],
        "given": [],
        "decisions": [decision()],
        "derived": [],
        "objective": {
            "sense": "min", "units": "1", "expression": expr(), "definition": "Zero cost.",
        },
        "constraints": [],
    }


def check_invalid(obj, *locations):
    with pytest.raises(ConfigError) as caught:
        validate_formulation(obj)
    for location in locations:
        assert any(location in message for message in caught.value.errors), caught.value.errors
    return caught.value.errors


def test_accumulates_independent_problems_without_mutation(minimal):
    minimal["schema_version"] = True
    minimal["purpose"] = " \t"
    minimal["decisions"][0].update(domain="continuous", lower=3, upper=2)
    minimal["objective"]["sense"] = "argmin"
    minimal["constraints"].append(row(relation="!="))
    before = deepcopy(minimal)
    errors = check_invalid(
        minimal, "schema_version", "purpose", "decisions[0].domain",
        "decisions[0].lower", "objective.sense", "constraints[0].relation",
    )
    assert len(errors) == 6
    assert minimal == before


def test_worked_example_with_independent_activation_oracle():
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    original = deepcopy(document)
    assert validate_formulation(document) is None
    assert document == original
    givens = {entry["id"]: entry["definition"] for entry in document["given"]}
    assert givens == {
        "demand": "The facilities must supply 3.5 kg in the example instance.",
        "capacity": "Example capacities (A, B): (4, 3) kg.",
        "fixed_cost": "Example fixed charges (A, B): (1, 4) credits.",
        "unit_cost": "Example variable costs (A, B): (2, 1) credits/kg.",
    }
    assert [(item["id"], item["axes"], item["domain"], item["lower"])
            for item in document["decisions"]] == [
        ("active", ["f"], "binary", None), ("output", ["f"], "real", 0),
    ]
    assert document["objective"]["sense"] == "min"
    assert document["objective"]["expression"]["refs"] == ["facilities", "facility_cost"]
    demand_row, capacity_row = document["constraints"]
    assert (demand_row["forall"], demand_row["relation"], demand_row["lhs"]["refs"],
            demand_row["rhs"]["refs"]) == ([], "=", ["total_output"], ["demand"])
    assert (capacity_row["forall"], capacity_row["relation"], capacity_row["lhs"]["refs"],
            capacity_row["rhs"]["refs"]) == (["f"], "<=", ["output"], ["capacity", "active"])
    assert document["sets"] == [{
        "id": "facilities", "indices": ["f"],
        "definition": "The two facilities A and B. The example data below are listed in that order.",
    }]
    assert [(g["id"], g["axes"], g["domain"], g["units"]) for g in document["given"]] == [
        ("demand", [], "nonnegative real", "kg"),
        ("capacity", ["f"], "nonnegative real", "kg"),
        ("fixed_cost", ["f"], "nonnegative real", "credits"),
        ("unit_cost", ["f"], "nonnegative real", "credits/kg"),
    ]
    assert [(d["id"], d["units"], d["upper"]) for d in document["decisions"]] == [
        ("active", "1", None), ("output", "kg", None),
    ]
    assert document["derived"] == [
        {"id": "total_output", "axes": [], "units": "kg",
         "expression": expr("∑_{f ∈ facilities} output_f",
                            refs=["facilities", "output"], over=["f"])},
        {"id": "facility_cost", "axes": ["f"], "units": "credits",
         "expression": expr("fixed_cost_f × active_f + unit_cost_f × output_f",
                            refs=["fixed_cost", "active", "unit_cost", "output"])},
    ]
    assert document["objective"] == {
        "sense": "min", "units": "credits",
        "expression": expr("∑_{f ∈ facilities} facility_cost_f",
                           refs=["facilities", "facility_cost"], over=["f"]),
        "definition": "Minimize total operating and supply costs in credits.",
    }
    assert [(r["id"], r["lhs"], r["rhs"]) for r in document["constraints"]] == [
        ("meet_demand", expr("total_output", refs=["total_output"]),
         expr("demand", refs=["demand"])),
        ("activation_capacity", expr("output_f", refs=["output"]),
         expr("capacity_f × active_f", refs=["capacity", "active"])),
    ]
    demand, capacity, fixed, unit = 3.5, (4, 3), (1, 4), (2, 1)
    costs = {}
    splits = {}
    for active in itertools.product((0, 1), repeat=2):
        available = [cap * enabled for cap, enabled in zip(capacity, active, strict=True)]
        if sum(available) < demand:
            costs[active] = None
            continue
        # Allocate to B first since its marginal cost is lower. Both
        # active facilities incur their charges even at zero output.
        output_b = min(available[1], demand)
        output = (demand - output_b, output_b)
        assert all(0 <= value <= cap for value, cap in zip(output, available, strict=True))
        splits[active] = output
        costs[active] = sum(
            charge * enabled + price * value
            for charge, enabled, price, value in zip(fixed, active, unit, output, strict=True)
        )
    assert costs == {(0, 0): None, (0, 1): None, (1, 0): 8, (1, 1): 9}
    assert splits == {(1, 0): (3.5, 0), (1, 1): (0.5, 3)}


def test_accepts_constant_scalar_integer_and_opaque_text(minimal, tmp_path):
    sentinel = tmp_path / "never-created"
    minimal["decisions"][0].update(domain="integer", upper=0)
    minimal["objective"]["expression"]["text"] = f"open({str(sentinel)!r}, 'w')"
    assert validate_formulation(minimal) is None
    assert not sentinel.exists()


def test_accepts_aliases_dense_sparse_and_unordered_derived(minimal):
    minimal["sets"] = [
        {"id": "I", "indices": ["i", "j"], "definition": "All facilities."},
        {"id": "arcs", "indices": [], "definition": "Admissible pairs of I elements."},
    ]
    minimal["given"] = [
        {"id": "input", "axes": ["j"], "units": "1", "domain": "positive real",
         "definition": "Supplied values."},
    ]
    minimal["derived"] = [
        derived("forward", ["fixed"], axes=["i"]),
        derived("fixed", ["input", "I"], over=["j"]),
    ]
    minimal["decisions"] = [decision("x", axes=["i"], upper=expr("forward[i]", refs=["forward"]))]
    minimal["objective"]["expression"] = expr("sum(x[i])", refs=["x"], over=["i"])
    minimal["constraints"] = [
        row("allowed", forall=["i", "j"], lhs=expr("(i, j)"), relation="in",
            rhs=expr("arcs", refs=["arcs"])),
        row("separate", lhs=expr(over=["i"]), rhs=expr(over=["i"])),
    ]
    assert validate_formulation(dict(reversed(list(minimal.items())))) is None


@pytest.mark.parametrize("version", [0, 2, 1.0, False, "1", None, []])
def test_version_requires_integer_one(minimal, version):
    minimal["schema_version"] = version
    check_invalid(minimal, "schema_version")


@pytest.mark.parametrize("obj", [None, True, 1, [], "formulation"])
def test_requires_object(obj):
    check_invalid(obj, "object")


def test_requires_all_and_only_named_fields(minimal):
    del minimal["derived"]
    minimal["notes"] = "unknown"
    minimal["sets"] = [{"id": "I", "indices": [], "other": "unknown"}]
    del minimal["decisions"][0]["upper"]
    minimal["objective"]["expression"]["extra"] = 2
    check_invalid(
        minimal, "derived", "notes", "sets[0].definition", "sets[0].other",
        "decisions[0].upper", "objective.expression.extra",
    )


def test_decision_objective_and_constraint_definitions_are_required(minimal):
    del minimal["decisions"][0]["definition"]
    minimal["objective"]["definition"] = "  "
    minimal["constraints"] = [row()]
    minimal["constraints"][0]["definition"] = []
    check_invalid(
        minimal, "decisions[0].definition", "objective.definition",
        "constraints[0].definition",
    )


def test_invalid_parents_do_not_hide_siblings(minimal):
    minimal["sets"] = {}
    minimal["given"] = [2]
    minimal["decisions"] = []
    minimal["constraints"] = [row(), None]
    minimal["constraints"][0]["lhs"] = []
    minimal["objective"] = False
    check_invalid(minimal, "sets", "given[0]", "decisions", "constraints[0].lhs",
                  "constraints[1]", "objective")


@pytest.mark.parametrize("name", ["", "é", "9id", "a-b", "a\n", "a b", "a.b", None, []])
def test_identifiers_are_ascii(minimal, name):
    minimal["decisions"][0]["id"] = name
    check_invalid(minimal, "decisions[0].id")


def test_global_namespace_includes_set_index_and_constraint_ids(minimal):
    minimal["sets"] = [
        {"id": "I", "indices": ["i", "i"], "definition": "First set."},
        {"id": "J", "indices": ["i", "J"], "definition": "Second set."},
    ]
    minimal["given"] = [
        {"id": "I", "axes": [], "domain": "any", "units": "1", "definition": "Given."},
    ]
    minimal["derived"] = [derived("i")]
    minimal["constraints"] = [row("x"), row("x")]
    check_invalid(
        minimal, "sets[0].indices[1]", "sets[1].indices[0]", "sets[1].indices[1]",
        "given[0].id", "derived[0].id", "constraints[0].id", "constraints[1].id",
    )


def test_bad_index_scopes_and_reference_kinds(minimal):
    minimal["sets"] = [{"id": "I", "indices": ["i"], "definition": "Set."}]
    minimal["decisions"][0]["axes"] = ["i", "missing", "i"]
    minimal["derived"] = [derived("y", ["i", "x", "x", "ghost"], axes=["i"], over=["i"])]
    minimal["objective"]["expression"] = expr(refs=["cap"], over=["x"])
    minimal["constraints"] = [
        row(forall=["i", "I"], lhs=expr(over=["i"]), rhs=expr(over=["unknown"])),
    ]
    check_invalid(
        minimal, "decisions[0].axes[1]", "decisions[0].axes[2]",
        "derived[0].expression.refs[0]", "derived[0].expression.refs[2]",
        "derived[0].expression.refs[3]", "derived[0].expression.over[0]",
        "objective.expression.refs[0]", "objective.expression.over[0]",
        "constraints[0].forall[1]", "constraints[0].lhs.over[0]",
        "constraints[0].rhs.over[0]",
    )


def test_cycles_and_independent_bound_errors_accumulate(minimal):
    minimal["derived"] = [
        derived("tail", ["a"]), derived("a", ["b"]), derived("b", ["a", "x"]),
        derived("self", ["self"]), derived("fixed"),
    ]
    minimal["decisions"][0].update(lower=expr(refs=["x", "tail"]), upper=expr(refs=["fixed"]))
    errors = check_invalid(
        minimal, "derived[1].expression.refs", "derived[2].expression.refs",
        "derived[3].expression.refs", "decisions[0].lower.refs[0]", "decisions[0].lower.refs[1]",
    )
    assert any("cycle" in message for message in errors)
    assert all("derived[4]" not in message for message in errors)


def test_acyclic_transitive_decision_dependency_is_invalid_in_a_bound(minimal):
    minimal["derived"] = [derived("first", ["second"]), derived("second", ["x"])]
    minimal["decisions"][0]["upper"] = expr(refs=["first"])
    errors = check_invalid(minimal, "decisions[0].upper.refs[0]")
    assert len(errors) == 1
    assert "decision" in errors[0]


@pytest.mark.parametrize("value", [True, "3", float("nan"), float("inf"), float("-inf"), []])
def test_refuses_nonfinite_or_non_numeric_bounds(minimal, value):
    minimal["decisions"][0]["lower"] = value
    check_invalid(minimal, "decisions[0].lower")


def test_accepts_finite_ints_larger_than_float(minimal, tmp_path):
    bound = 10**900
    minimal["decisions"][0].update(lower=-bound, upper=bound)
    assert validate_formulation(minimal) is None
    path = tmp_path / "formulation.json"
    path.write_text(json.dumps(minimal), encoding="utf-8")
    assert cli(path).returncode == 0


@pytest.mark.parametrize("relation", ["<=", "=", ">=", "<", ">", "in"])
def test_relation_vocabulary(minimal, relation):
    minimal["constraints"] = [row(relation=relation)]
    assert validate_formulation(minimal) is None


@pytest.mark.parametrize("field", ["sense", "units", "expression", "definition"])
def test_bad_objective_field(minimal, field):
    minimal["objective"][field] = []
    check_invalid(minimal, f"objective.{field}")


def cli(path):
    return subprocess.run(
        [sys.executable, "-B", "-m", "dskit.pipeline.formulation", str(path)],
        capture_output=True, text=True, check=False,
    )


def test_cli_accepts_example():
    result = cli(EXAMPLE)
    assert result.returncode == 0, result.stderr
    assert "valid" in result.stdout.lower()
    assert result.stderr == ""


@pytest.mark.parametrize(("body", "diagnostic"), [
    (b"\xff", "utf-8"),
    (b"{]", "json"),
    (b'{"sets":[{"id":"I","id":"J"}]}', "duplicate"),
    (b'{"schema_version":1,"schema_version":1}', "duplicate"),
    (b'{"objective":{"raw":NaN}}', "non-finite"),
    (b'{"objective":{"raw":Infinity}}', "non-finite"),
    (b'{"objective":{"raw":-Infinity}}', "non-finite"),
    (b'{"objective":{"raw":1e999}}', "non-finite"),
])
def test_cli_refuses_invalid_json_bytes(tmp_path, body, diagnostic):
    path = tmp_path / "invalid.json"
    path.write_bytes(body)
    result = cli(path)
    assert result.returncode == 1
    assert str(path) in result.stderr
    assert diagnostic in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()


def test_cli_reports_read_and_accumulated_shape_failures(tmp_path, minimal):
    path = tmp_path / "not-present.json"
    result = cli(path)
    assert result.returncode == 1 and str(path) in result.stderr
    minimal["purpose"] = ""
    minimal["objective"]["sense"] = "later"
    path.write_text(json.dumps(minimal), encoding="utf-8")
    result = cli(path)
    assert result.returncode == 1 and str(path) in result.stderr
    assert "purpose" in result.stderr and "objective.sense" in result.stderr


def test_large_integer_cli_and_shared_loader(minimal, tmp_path):
    from dskit.pipeline.document import _load_strict_json

    digits = "1" + "0" * 5000
    bound = int(Decimal(digits))
    limit = sys.get_int_max_str_digits()
    path = tmp_path / "large.json"
    for lower, upper, valid in [(-bound, bound, True), (bound, bound, True),
                                (bound + 1, bound, False)]:
        minimal["decisions"][0].update(lower=lower, upper=upper)
        if valid:
            assert validate_formulation(minimal) is None
        else:
            check_invalid(minimal, "decisions[0].lower")
        minimal["decisions"][0].update(lower=0, upper=0)
        lower_text = ("-" + digits if lower < 0 else
                      "1" + "0" * 4999 + "1" if lower > bound else digits)
        body = json.dumps(minimal).replace('"lower": 0, "upper": 0',
                                            f'"lower": {lower_text}, "upper": {digits}')
        path.write_text(body, encoding="utf-8")
        result = cli(path)
        assert (result.returncode == 0) is valid, result.stderr
    assert sys.get_int_max_str_digits() == limit
    with pytest.raises(ValueError):
        _load_strict_json(path)
    parsed = _load_strict_json(path, parse_int=lambda token: int(Decimal(token)))
    assert parsed["decisions"][0]["lower"] == bound + 1
    path.write_text('{"nodes": [1, -2, 0], "float": 1.5}', encoding="utf-8")
    assert _load_strict_json(path) == {"nodes": [1, -2, 0], "float": 1.5}
    assert sys.get_int_max_str_digits() == limit
