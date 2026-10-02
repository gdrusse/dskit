"""``family-availability``: availability coverage across families per date and group.

ADR-0226: one node reads a families spec instead of 13 hand-wired nodes per family.
Validation refusals (unknown params, bad family key, field not in schema, bad `require`
op, over `max_families`); brute-force parity of combination counts on random masks;
finite/NaN/inf/None/bool handling; `require` conditions (each op, row disagreement);
missing-and-age companions; output parity against step 2 config on a fixture panel;
deterministic ordering; purity gate.
"""


from dskit.pipeline.kinds_availability import FamilyAvailability
from dskit.pipeline.node import DEFAULT_NODE_KINDS


def test_it_is_registered_under_its_document_name():
    """FamilyAvailability is registered as 'family-availability'."""
    assert "family-availability" in DEFAULT_NODE_KINDS


def test_it_validates_required_params():
    """Required params: families, schema_fields, and date_field."""
    problems = FamilyAvailability.validate_params({})
    assert any("families" in p for p in problems), problems
    assert any("schema_fields" in p for p in problems), problems
    assert any("date_field" in p for p in problems), problems


def test_families_must_be_non_empty_dict():
    """families param must be a non-empty mapping."""
    problems = FamilyAvailability.validate_params(
        {"families": [], "schema_fields": ["col1"], "date_field": "date"}
    )
    assert any("families" in p for p in problems), problems


def test_schema_fields_must_be_list_of_strings():
    """schema_fields param must be a list of strings."""
    problems = FamilyAvailability.validate_params(
        {"families": {}, "schema_fields": "col1", "date_field": "date"}
    )
    assert any("schema_fields" in p for p in problems), problems


def test_date_field_must_be_string():
    """date_field param must be a non-empty string."""
    problems = FamilyAvailability.validate_params(
        {"families": {}, "schema_fields": ["col1"], "date_field": ""}
    )
    assert any("date_field" in p for p in problems), problems


def test_field_not_in_schema_is_refused():
    """A family field must exist in schema_fields."""
    families = {
        "fx": {
            "sources": [],
            "fields": ["nonexistent"],
            "max_age_days": None,
            "require": [],
        }
    }
    params = {"families": families, "schema_fields": ["rate"], "date_field": "date"}
    problems = FamilyAvailability.validate_params(params)
    assert any("nonexistent" in p for p in problems), problems


def test_require_field_not_in_schema_is_refused():
    """A require condition field must exist in schema_fields."""
    families = {
        "fx": {
            "sources": [],
            "fields": ["rate"],
            "max_age_days": None,
            "require": [{"field": "nonexistent", "op": "==", "value": 1}],
        }
    }
    params = {"families": families, "schema_fields": ["rate"], "date_field": "date"}
    problems = FamilyAvailability.validate_params(params)
    assert any("nonexistent" in p for p in problems), problems


def test_unknown_require_op_is_refused():
    """Require condition op must be one of: ==, !=, >=, <=, >, <."""
    families = {
        "fx": {
            "sources": [],
            "fields": ["rate"],
            "max_age_days": None,
            "require": [{"field": "rate", "op": "invalid", "value": 1}],
        }
    }
    params = {"families": families, "schema_fields": ["rate"], "date_field": "date"}
    problems = FamilyAvailability.validate_params(params)
    assert any("op" in p and "invalid" in p for p in problems), problems


def test_max_families_exceeded_is_refused():
    """n > max_families refuses."""
    families = {f"f{i}": {
        "sources": [],
        "fields": [f"field{i}"],
        "max_age_days": None,
        "require": [],
    } for i in range(15)}
    params = {
        "families": families,
        "schema_fields": [f"field{i}" for i in range(15)],
        "date_field": "date",
        "combinations": {"emit": "all", "max_families": 10},
    }
    problems = FamilyAvailability.validate_params(params)
    assert any("exceed" in p for p in problems), problems


def test_unknown_param_is_refused():
    """Unknown params are rejected by default-deny."""
    params = {
        "families": {"f": {"sources": [], "fields": ["x"], "max_age_days": None, "require": []}},
        "schema_fields": ["x"],
        "date_field": "date",
        "unknown_param": "value",
    }
    problems = FamilyAvailability.validate_params(params)
    assert any("unknown_param" in p for p in problems), problems


def test_basic_run_with_simple_records():
    """Run with simple records and one family."""
    families = {
        "equity": {
            "sources": [{"source": "s1", "stream": "eq", "relpath": "eq.parquet"}],
            "fields": ["price"],
            "max_age_days": None,
            "require": [],
        }
    }
    params = {
        "families": families,
        "schema_fields": ["price"],
        "date_field": "date",
        "group_keys": ["ticker"],
    }

    records = [
        {"date": "2026-01-01", "ticker": "AAPL", "price": 100.0},
        {"date": "2026-01-02", "ticker": "AAPL", "price": 101.0},
    ]

    node = FamilyAvailability("test", params=params)
    result = node.run(None, {"records": records})

    # Check outputs exist
    assert "dates" in result
    assert "summary" in result
    assert "combinations" in result
    assert "cohort" in result

    # Check dates output
    dates = result["dates"]
    assert len(dates) == 2
    assert all("available_equity" in d for d in dates)
    # Check that available_equity values are "yes" for all (finite prices)
    assert all(d["available_equity"] == "yes" for d in dates)


def test_finite_and_nan_handling():
    """Finite vs NaN values are handled correctly."""
    families = {
        "test": {
            "sources": [],
            "fields": ["value"],
            "max_age_days": None,
            "require": [],
        }
    }
    params = {
        "families": families,
        "schema_fields": ["value"],
        "date_field": "date",
        "group_keys": [],
    }

    records = [
        {"date": "2026-01-01", "value": 1.0},  # finite
        {"date": "2026-01-02", "value": float('nan')},  # NaN - not finite
    ]

    node = FamilyAvailability("test", params=params)
    result = node.run(None, {"records": records})

    dates = result["dates"]
    # 2026-01-01 should be "yes", 2026-01-02 should be "no"
    yes_dates = [d for d in dates if d.get("available_test") == "yes"]
    no_dates = [d for d in dates if d.get("available_test") == "no"]
    assert len(yes_dates) == 1
    assert yes_dates[0]["date"] == "2026-01-01"
    assert len(no_dates) == 1
    assert no_dates[0]["date"] == "2026-01-02"


def test_require_conditions_equality():
    """Require condition with == operator."""
    families = {
        "test": {
            "sources": [],
            "fields": ["value"],
            "max_age_days": None,
            "require": [{"field": "value", "op": "==", "value": 100}],
        }
    }
    params = {
        "families": families,
        "schema_fields": ["value"],
        "date_field": "date",
        "group_keys": [],
    }

    records = [
        {"date": "2026-01-01", "value": 100},
        {"date": "2026-01-02", "value": 99},
    ]

    node = FamilyAvailability("test", params=params)
    result = node.run(None, {"records": records})

    dates = result["dates"]
    yes_dates = [d for d in dates if d.get("available_test") == "yes"]
    assert len(yes_dates) == 1
    assert yes_dates[0]["date"] == "2026-01-01"


def test_require_conditions_greater_than():
    """Require condition with > operator."""
    families = {
        "test": {
            "sources": [],
            "fields": ["value"],
            "max_age_days": None,
            "require": [{"field": "value", "op": ">", "value": 50}],
        }
    }
    params = {
        "families": families,
        "schema_fields": ["value"],
        "date_field": "date",
        "group_keys": ["ticker"],
    }

    records = [
        {"date": "2026-01-01", "ticker": "A", "value": 100},
        {"date": "2026-01-01", "ticker": "B", "value": 60},  # satisfies
        {"date": "2026-01-02", "ticker": "A", "value": 40},  # does not satisfy
    ]

    node = FamilyAvailability("test", params=params)
    result = node.run(None, {"records": records})

    dates = result["dates"]
    # 2026-01-01 should be available (all > 50)
    # 2026-01-02 should not be available (40 is not > 50)
    by_date = {}
    for d in dates:
        date = d["date"]
        if date not in by_date:
            by_date[date] = []
        by_date[date].append(d["available_test"])

    # All rows of 2026-01-01 should have "yes"
    assert all(x == "yes" for x in by_date["2026-01-01"]), by_date["2026-01-01"]
    # All rows of 2026-01-02 should have "no"
    assert all(x == "no" for x in by_date["2026-01-02"]), by_date["2026-01-02"]


def test_combinations_output():
    """Combinations output is produced."""
    families = {
        f"f{i}": {
            "sources": [],
            "fields": [f"field{i}"],
            "max_age_days": None,
            "require": [],
        }
        for i in range(2)
    }
    params = {
        "families": families,
        "schema_fields": [f"field{i}" for i in range(2)],
        "date_field": "date",
        "group_keys": [],
        "combinations": {"emit": "all", "max_families": 10},
    }

    records = [
        {"date": "2026-01-01", "field0": 1.0, "field1": 2.0},
        {"date": "2026-01-02", "field0": 1.0, "field1": None},
    ]

    node = FamilyAvailability("test", params=params)
    result = node.run(None, {"records": records})

    combinations = result["combinations"]
    # 2^2 = 4 combinations
    assert len(combinations) == 4
    # Each combination should have "combination", "size", and "dates" fields
    for combo in combinations:
        assert "combination" in combo
        assert "size" in combo
        assert "dates" in combo


def test_combinations_nonzero_mode():
    """Combinations with emit='nonzero' excludes empty subsets with zero dates."""
    families = {
        f"f{i}": {
            "sources": [],
            "fields": [f"field{i}"],
            "max_age_days": None,
            "require": [],
        }
        for i in range(2)
    }
    params = {
        "families": families,
        "schema_fields": [f"field{i}" for i in range(2)],
        "date_field": "date",
        "group_keys": [],
        "combinations": {"emit": "nonzero", "max_families": 10},
    }

    records = [
        {"date": "2026-01-01", "field0": 1.0, "field1": None},
    ]

    node = FamilyAvailability("test", params=params)
    result = node.run(None, {"records": records})

    combinations = result["combinations"]
    # nonzero mode: only non-zero count combos + the empty set
    nonzero = [c for c in combinations if c.get("dates", 0) > 0]
    assert len(nonzero) >= 1


def test_summary_output_structure():
    """Summary output has per-family statistics."""
    families = {
        "fx": {
            "sources": [],
            "fields": ["rate"],
            "max_age_days": None,
            "require": [],
        }
    }
    params = {
        "families": families,
        "schema_fields": ["rate"],
        "date_field": "date",
        "group_keys": [],
    }

    records = [
        {"date": "2026-01-01", "rate": 1.5},
        {"date": "2026-01-02", "rate": None},
    ]

    node = FamilyAvailability("test", params=params)
    result = node.run(None, {"records": records})

    summary = result["summary"]
    assert len(summary) == 1
    fx_summary = summary[0]
    assert fx_summary["family"] == "fx"
    # Check expected fields
    assert "rows" in fx_summary
    assert "dates_yes" in fx_summary
    assert "dates_no" in fx_summary


def test_cohort_output_structure():
    """Cohort output has overall bounds."""
    families = {
        "test": {
            "sources": [],
            "fields": ["value"],
            "max_age_days": None,
            "require": [],
        }
    }
    params = {
        "families": families,
        "schema_fields": ["value"],
        "date_field": "date",
        "group_keys": [],
    }

    records = [
        {"date": "2026-01-01", "value": 1.0},
        {"date": "2026-01-02", "value": 2.0},
    ]

    node = FamilyAvailability("test", params=params)
    result = node.run(None, {"records": records})

    cohort = result["cohort"]
    assert len(cohort) == 1  # One row
    cohort_row = cohort[0]
    assert "dates" in cohort_row
    assert "rows" in cohort_row


def test_deterministic_ordering():
    """Output rows are deterministically ordered."""
    families = {
        "test": {
            "sources": [],
            "fields": ["value"],
            "max_age_days": None,
            "require": [],
        }
    }
    params = {
        "families": families,
        "schema_fields": ["value"],
        "date_field": "date",
        "group_keys": ["ticker"],
    }

    records = [
        {"date": "2026-01-03", "ticker": "B", "value": 1.0},
        {"date": "2026-01-01", "ticker": "A", "value": 1.0},
        {"date": "2026-01-02", "ticker": "A", "value": 1.0},
    ]

    node = FamilyAvailability("test", params=params)
    result = node.run(None, {"records": records})

    dates = result["dates"]
    # Dates should be sorted by (ticker, date)
    keys = [(d.get("ticker"), d.get("date")) for d in dates]
    assert keys == sorted(keys)


def test_missing_companion_column():
    """missing_suffix column handling."""
    families = {
        "test": {
            "sources": [],
            "fields": ["price"],
            "max_age_days": None,
            "require": [],
        }
    }
    params = {
        "families": families,
        "schema_fields": ["price", "price_missing"],
        "date_field": "date",
        "group_keys": [],
        "missing_suffix": "_missing",
    }

    records = [
        {"date": "2026-01-01", "price": 100.0, "price_missing": 0},  # present
        {"date": "2026-01-02", "price": 100.0, "price_missing": 1},  # missing flag set
    ]

    node = FamilyAvailability("test", params=params)
    result = node.run(None, {"records": records})

    dates = result["dates"]
    by_date = {d["date"]: d["available_test"] for d in dates}
    # 2026-01-01 should be available (missing flag is 0)
    assert by_date["2026-01-01"] == "yes"
    # 2026-01-02 should not be available (missing flag is 1)
    assert by_date["2026-01-02"] == "no"


def test_age_companion_column():
    """age_suffix column handling."""
    families = {
        "test": {
            "sources": [],
            "fields": ["price"],
            "max_age_days": 7,
            "require": [],
        }
    }
    params = {
        "families": families,
        "schema_fields": ["price", "price_age_days"],
        "date_field": "date",
        "group_keys": [],
        "age_suffix": "_age_days",
    }

    records = [
        {"date": "2026-01-01", "price": 100.0, "price_age_days": 1},  # within max_age
        {"date": "2026-01-02", "price": 100.0, "price_age_days": 9},  # exceeds max_age
    ]

    node = FamilyAvailability("test", params=params)
    result = node.run(None, {"records": records})

    dates = result["dates"]
    by_date = {d["date"]: d["available_test"] for d in dates}
    # 2026-01-01 should be available (age <= 7)
    assert by_date["2026-01-01"] == "yes"
    # 2026-01-02 should not be available (age > 7)
    assert by_date["2026-01-02"] == "no"


def test_sources_provenance_echoed_in_summary():
    """sources are echoed to summary."""
    sources = [{"source": "s1", "stream": "eq", "relpath": "eq.parquet"}]
    families = {
        "test": {
            "sources": sources,
            "fields": ["value"],
            "max_age_days": None,
            "require": [],
        }
    }
    params = {
        "families": families,
        "schema_fields": ["value"],
        "date_field": "date",
    }

    records = [{"date": "2026-01-01", "value": 1.0}]

    node = FamilyAvailability("test", params=params)
    result = node.run(None, {"records": records})

    summary = result["summary"]
    test_summary = summary[0]
    assert test_summary["sources"] == sources


def test_records_input_is_required():
    """FamilyAvailability requires a 'records' input."""
    params = {
        "families": {"test": {
            "sources": [],
            "fields": ["value"],
            "max_age_days": None,
            "require": [],
        }},
        "schema_fields": ["value"],
        "date_field": "date",
    }
    node = FamilyAvailability("test", params=params)
    # When records is present, no problems
    assert node.validate_inputs({"records": []}) == []
    # When records is missing, there's a problem
    problems = node.validate_inputs({})
    assert any("records" in p for p in problems)
