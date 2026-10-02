"""``family-availability`` admission (ADR-0226 amendment): ``admit`` and ``flag`` families.

``admit {min_rate}`` adds an ``admission`` port (rate_/required_ per family, the
sorted ``admitted`` list) and a ``complete`` flag on ``dates``; a ``flag`` family
reads a previous availability output's yes/no column instead of raw fields.
"""

import random

import pytest

from dskit.pipeline.kinds_availability import FamilyAvailability

NAMES = ("alpha", "beta", "gamma")


COUNT = "n_days"


def _node(min_rate=None, groups=("grp",), **extra):
    families = {n: {"flag": "available_" + n} for n in NAMES}
    params = {
        "families": families,
        "schema_fields": ["grp", "day", *("available_" + n for n in NAMES)],
        "date_field": "day",
        "group_keys": list(groups),
        "dates_count_field": COUNT,
        **extra,
    }
    if min_rate is not None:
        params["admit"] = {"min_rate": min_rate}
    return FamilyAvailability("fa", params)


def _rows(patterns, group="g1"):
    return [
        {"grp": group, "day": f"d{i:03d}", **{"available_" + n: "yes" if b else "no"
                                              for n, b in zip(NAMES, bits)}}
        for i, bits in enumerate(patterns)
    ]


def test_without_admit_behaviour_is_unchanged():
    out = _node().run(None, {"records": _rows([(1, 1, 1)])})
    assert out["admission"] == []
    assert "complete" not in out["dates"][0]


def test_rates_match_brute_force_on_random_panels():
    rng = random.Random(7)
    patterns = [tuple(rng.random() < 0.7 for _ in NAMES) for _ in range(61)]
    out = _node(0.6).run(None, {"records": _rows(patterns)})
    (rec,) = out["admission"]
    assert rec["grp"] == "g1" and rec[COUNT] == 61
    for i, n in enumerate(NAMES):
        want = sum(p[i] for p in patterns) / 61
        assert rec["rate_" + n] == pytest.approx(want)
        assert rec["required_" + n] == (1 if want >= 0.6 else 0)
    assert rec["admitted"] == sorted(n for n in NAMES if rec["required_" + n])


def test_a_rate_equal_to_min_rate_admits():
    patterns = [(1, 1, 1)] * 9 + [(0, 1, 1)]
    (rec,) = _node(0.9).run(None, {"records": _rows(patterns)})["admission"]
    assert rec["rate_alpha"] == pytest.approx(0.9) and rec["required_alpha"] == 1


def test_just_below_min_rate_does_not_admit():
    patterns = [(1, 1, 1)] * 8 + [(0, 1, 1)] * 2
    (rec,) = _node(0.9).run(None, {"records": _rows(patterns)})["admission"]
    assert rec["required_alpha"] == 0 and rec["admitted"] == ["beta", "gamma"]


def test_complete_requires_every_admitted_family_only():
    patterns = [(1, 1, 1), (0, 1, 1), (1, 1, 0), (1, 0, 1), (1, 1, 1)]
    out = _node(0.7).run(None, {"records": _rows(patterns)})
    (rec,) = out["admission"]
    admitted = set(rec["admitted"])
    for row, bits in zip(out["dates"], patterns):
        want = all(b for n, b in zip(NAMES, bits) if n in admitted)
        assert row["complete"] == ("yes" if want else "no")


def test_complete_is_yes_for_every_date_when_nothing_is_admitted():
    patterns = [(0, 0, 0), (1, 0, 0)]
    out = _node(1.0).run(None, {"records": _rows(patterns)})
    assert out["admission"][0]["admitted"] == []
    assert [d["complete"] for d in out["dates"]] == ["yes", "yes"]


def test_one_admission_record_per_group():
    rows = _rows([(1, 1, 1)] * 2, "g1") + _rows([(0, 1, 1)] * 4, "g2")
    out = _node(0.5).run(None, {"records": rows})
    by = {r["grp"]: r for r in out["admission"]}
    assert by["g1"][COUNT] == 2 and by["g2"][COUNT] == 4
    assert by["g1"]["admitted"] == list(NAMES) and by["g2"]["admitted"] == ["beta", "gamma"]


def test_admission_counts_distinct_dates_not_rows():
    rows = _rows([(1, 1, 1)] * 2) + _rows([(1, 1, 1)] * 2)
    (rec,) = _node(0.5).run(None, {"records": rows})["admission"]
    assert rec[COUNT] == 2


@pytest.mark.parametrize("bad", [0, 0.0, -0.1, 1.01, True, "0.9", None, float("nan")])
def test_min_rate_out_of_range_refuses(bad):
    problems = FamilyAvailability.validate_params(_node().params | {"admit": {"min_rate": bad}})
    assert any("min_rate" in p for p in problems), problems


def test_min_rate_one_is_valid_and_it_is_required():
    assert FamilyAvailability.validate_params(_node(1).params) == []
    problems = FamilyAvailability.validate_params(_node().params | {"admit": {}})
    assert any("min_rate" in p for p in problems)


def test_admit_rejects_unknown_keys_and_non_mappings():
    base = _node().params
    assert FamilyAvailability.validate_params(base | {"admit": {"min_rate": 0.5, "x": 1}})
    assert FamilyAvailability.validate_params(base | {"admit": 0.5})


def test_a_node_reference_is_accepted_for_min_rate():
    params = _node().params | {"admit": {"min_rate": "$tau.merged.tau"}}
    assert FamilyAvailability.validate_params(params) == []


def test_admission_is_a_declared_port():
    assert "admission" in FamilyAvailability.outputs


def test_flag_family_reads_the_yes_no_column():
    out = _node().run(None, {"records": _rows([(1, 0, 1)])})
    row = out["dates"][0]
    assert (row["available_alpha"], row["available_beta"], row["available_gamma"]) == (
        "yes", "no", "yes")


def test_flag_family_refuses_any_other_value():
    rows = _rows([(1, 1, 1)])
    rows[0]["available_beta"] = "maybe"
    with pytest.raises(ValueError, match="available_beta"):
        _node().run(None, {"records": rows})


def test_flag_family_refuses_a_missing_cell():
    rows = _rows([(1, 1, 1)])
    del rows[0]["available_gamma"]
    with pytest.raises(ValueError, match="available_gamma"):
        _node().run(None, {"records": rows})


def test_flag_cannot_be_mixed_with_raw_field_keys():
    params = _node().params
    params["families"] = {"alpha": {"flag": "available_alpha", "fields": ["grp"]}}
    assert any("flag" in p for p in FamilyAvailability.validate_params(params))


def test_flag_column_must_be_in_schema():
    params = _node().params
    params["families"] = {"alpha": {"flag": "nope"}}
    assert any("nope" in p for p in FamilyAvailability.validate_params(params))


def test_count_field_defaults_and_is_validated():
    from dskit.pipeline.kinds_availability import DEFAULT_DATES_FIELD

    node = _node(0.5)
    del node.params["dates_count_field"]
    (rec,) = node.run(None, {"records": _rows([(1, 1, 1)])})["admission"]
    assert rec[DEFAULT_DATES_FIELD] == 1
    base = dict(_node(0.5).params)
    for bad in ("", 3, "grp"):
        assert FamilyAvailability.validate_params({**base, "dates_count_field": bad})
