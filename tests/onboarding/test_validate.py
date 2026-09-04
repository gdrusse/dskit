"""validate.py: suites, the rule engine, thresholds, and gating."""

import os

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import (
    Rule,
    ValidationSuite,
    load_suite,
    run_acquisition,
    run_suite,
    suite_hash,
)

from .fake_connector import FakeConnector, record


@pytest.fixture
def snapshot(root, registry, fake_source):
    """One acquired snapshot with knowable rows."""
    FakeConnector.script = [
        record("prices", "2026-01-02", {"close": 10.5, "sym": "A"}),
        record("prices", "2026-01-03", {"close": None, "sym": "A"}),
        record("prices", "2026-01-04", {"close": 11.0, "sym": "B"}),
    ]
    return run_acquisition(root, registry, "fake", "prices", "live")["snapshot"]


def _suite(*rules):
    return ValidationSuite(name="s", rules=tuple(rules))


# -- suite / rule shape -----------------------------------------------------


def test_rule_default_deny_kwargs():
    with pytest.raises(AssetError, match="unknown key"):
        Rule(id="r", target="prices", rule="not_null", kwargs={"feild": "x"})
    with pytest.raises(AssetError, match="missing required kwarg"):
        Rule(id="r", target="prices", rule="accepted_values", kwargs={"field": "x"})
    with pytest.raises(AssetError, match="min/max"):
        Rule(id="r", target="prices", rule="row_count")


def test_threshold_grammar_and_severity_matching():
    with pytest.raises(AssetError, match="threshold"):
        Rule(id="r", target="t", rule="bitemporal", threshold="lots")
    with pytest.raises(AssetError, match="use warn_if"):
        ValidationSuite.from_obj({"name": "s", "rules": [
            {"id": "r", "target": "t", "rule": "bitemporal",
             "severity": "warn", "error_if": "> 0"}]})


def test_duplicate_rule_ids_refused():
    r = Rule(id="dup", target="t", rule="bitemporal")
    with pytest.raises(AssetError, match="unique"):
        _suite(r, r)


def test_suite_hash_ignores_notes(tmp_path):
    a = _suite(Rule(id="r", target="t", rule="bitemporal"))
    b = ValidationSuite(name="s", notes="documented",
                        rules=(Rule(id="r", target="t", rule="bitemporal",
                                    notes="why"),))
    assert suite_hash(a) == suite_hash(b)


def test_load_suite_round_trip(tmp_path):
    path = tmp_path / "suite.json"
    path.write_text(
        '{"name": "basic", "rules": [{"id": "r", "target": "prices",'
        ' "rule": "not_null", "kwargs": {"field": "close"}}]}'
    )
    suite = load_suite(str(path))
    assert suite.rules[0].severity == "error"  # the defaults
    assert suite.rules[0].threshold == "!= 0"


# -- the rule engine over a real snapshot -----------------------------------


def test_rules_count_failures_correctly(root, registry, snapshot):
    out = run_suite(root, registry, _suite(
        Rule(id="nn", target="prices", rule="not_null", kwargs={"field": "close"}),
        Rule(id="uq", target="prices", rule="unique", kwargs={"field": "sym"}),
        Rule(id="av", target="prices", rule="accepted_values",
             kwargs={"field": "sym", "values": ["A"]}),
        Rule(id="ir", target="prices", rule="in_range",
             kwargs={"field": "close", "min": 10.6}),
        Rule(id="rc", target="prices", rule="row_count", kwargs={"min": 5}),
        Rule(id="bt", target="prices", rule="bitemporal"),
    ), snapshot)
    failing = {r["id"]: r["failing"] for r in out["statistics"]["results"]}
    assert failing == {"nn": 1,   # one null close
                       "uq": 2,   # 'A' appears twice — both rows fail
                       "av": 1,   # 'B' not accepted
                       "ir": 1,   # 10.5 < 10.6; null skipped
                       "rc": 1,   # 3 rows < min 5
                       "bt": 0}   # by construction (acquire asserted it)
    assert out["gating"] == "block"


def test_gating_pass_warn_block_and_warn_never_blocks(root, registry, snapshot):
    passing = run_suite(root, registry, _suite(
        Rule(id="bt", target="prices", rule="bitemporal")), snapshot)
    assert passing["gating"] == "pass"

    warn_only = run_suite(root, registry, _suite(
        Rule(id="nn", target="prices", rule="not_null",
             kwargs={"field": "close"}, severity="warn")), snapshot)
    assert warn_only["gating"] == "warn"  # tripped, but warn NEVER blocks

    threshold_holds = run_suite(root, registry, _suite(
        Rule(id="nn", target="prices", rule="not_null",
             kwargs={"field": "close"}, threshold="> 1")), snapshot)
    assert threshold_holds["gating"] == "pass"  # 1 failure, gate is > 1


def test_result_registered_content_addressed(root, registry, snapshot):
    suite = _suite(Rule(id="bt", target="prices", rule="bitemporal"))
    a = run_suite(root, registry, suite, snapshot)
    b = run_suite(root, registry, suite, snapshot)  # idempotent re-run
    assert a["result"] == b["result"]
    rec = registry.get(a["result"])
    assert rec.kind == "validation_result"
    assert rec.refs["snapshot"] == snapshot
    assert rec.payload["suite_hash"] == suite_hash(suite)


def test_missing_target_stream_is_zero_rows_not_a_crash(root, registry, snapshot):
    out = run_suite(root, registry, _suite(
        Rule(id="rc", target="ghost", rule="row_count", kwargs={"min": 1})),
        snapshot)
    assert out["gating"] == "block"
    assert out["statistics"]["rows"] == {"ghost": 0}


def test_wrong_kind_refused(root, registry, snapshot, fake_source):
    with pytest.raises(AssetError, match="not a snapshot"):
        run_suite(root, registry,
                  _suite(Rule(id="r", target="t", rule="bitemporal")),
                  fake_source)


# -- compressed observations (ADR-0036) ---------------------------------------


@pytest.fixture
def gz_snapshot(root, registry, gz_source):
    """One acquired snapshot whose normalized rows landed gzipped."""
    FakeConnector.script = [
        record("prices", "2026-01-02", {"close": 10.5, "sym": "A"}),
        record("prices", "2026-01-04", {"close": 11.0, "sym": "B"}),
    ]
    return run_acquisition(root, registry, "gz", "prices", "live")["snapshot"]


def test_suite_runs_over_gz_observations(root, registry, gz_snapshot):
    out = run_suite(root, registry, _suite(
        Rule(id="nn", target="prices", rule="not_null",
             kwargs={"field": "close"}),
        Rule(id="rc", target="prices", rule="row_count",
             kwargs={"min": 2, "max": 2})),
        gz_snapshot)
    assert out["gating"] == "pass"
    assert out["statistics"]["rows"] == {"prices": 2}


def test_corrupt_gz_observations_refuse_as_asset_error(
    root, registry, gz_snapshot
):
    # Find the committed gz observations file and truncate it: the suite
    # must refuse with a typed error, never a raw zlib/OSError.
    obs_root = os.path.join(root.root, "observations", "gz")
    (acq_id,) = os.listdir(obs_root)
    path = os.path.join(obs_root, acq_id, "prices.jsonl.gz")
    blob = open(path, "rb").read()
    open(path, "wb").write(blob[: len(blob) - 6])  # chop the trailer
    with pytest.raises(AssetError, match="corrupt or unreadable"):
        run_suite(root, registry, _suite(
            Rule(id="rc", target="prices", rule="row_count",
                 kwargs={"min": 1})),
            gz_snapshot)


# -- distinct_count: cardinality per group (ADR-0084) -------------------------


@pytest.fixture
def ladder_snapshot(root, registry, fake_source):
    """Two events with unequal strike counts, one null strike, one row
    that names no event at all."""
    FakeConnector.script = [
        record("ladder", "2026-01-02", {"event": "E1", "strike": 10, "venue": "x"}),
        record("ladder", "2026-01-02", {"event": "E1", "strike": 20, "venue": "x"}),
        record("ladder", "2026-01-02", {"event": "E1", "strike": 20, "venue": "y"}),
        record("ladder", "2026-01-03", {"event": "E2", "strike": 10, "venue": "x"}),
        record("ladder", "2026-01-03", {"event": "E2", "strike": None, "venue": "x"}),
        record("ladder", "2026-01-03", {"strike": 30, "venue": "x"}),
    ]
    return run_acquisition(root, registry, "fake", "ladder", "live")["snapshot"]


def _distinct(rid, **kwargs):
    return Rule(id=rid, target="ladder", rule="distinct_count", kwargs=kwargs)


def test_distinct_count_kwargs_are_default_deny_and_bounded():
    with pytest.raises(AssetError, match="missing required kwarg"):
        Rule(id="r", target="t", rule="distinct_count", kwargs={"min": 1})
    with pytest.raises(AssetError, match="min/max"):
        Rule(id="r", target="t", rule="distinct_count", kwargs={"field": "x"})
    with pytest.raises(AssetError, match="unknown key"):
        Rule(id="r", target="t", rule="distinct_count",
             kwargs={"field": "x", "min": 1, "by": "e"})
    for bad in (5, "", [], ["e", ""], [1], {"e": 1}):
        with pytest.raises(AssetError, match="group_by"):
            Rule(id="r", target="t", rule="distinct_count",
                 kwargs={"field": "x", "min": 1, "group_by": bad})
    # a field name and a list of them both name the grouping
    _distinct("s", field="x", min=1, group_by="e")
    _distinct("l", field="x", min=1, group_by=["e", "v"])


def test_every_bounded_rule_needs_at_least_one_bound():
    """The min/max check is DERIVED from the rule table: every rule whose
    kwargs carry both bounds refuses when neither is declared."""
    for rule, kwargs in (
        ("in_range", {"field": "x"}),
        ("row_count", {}),
        ("distinct_count", {"field": "x"}),
    ):
        with pytest.raises(AssetError, match="min/max"):
            Rule(id="r", target="t", rule=rule, kwargs=kwargs)


def test_distinct_count_fails_one_per_group_out_of_bounds(root, registry, ladder_snapshot):
    out = run_suite(root, registry, _suite(
        # per event: E1 -> {10, 20}; E2 -> {10} (the null strike is skipped);
        # the row naming no event belongs to no group and is skipped
        _distinct("exact", field="strike", group_by="event", min=2, max=2),
        # per (event, venue): (E1,x) -> 2 ok; (E1,y) -> 1; (E2,x) -> 1
        _distinct("pair", field="strike", group_by=["event", "venue"], min=2),
        # ungrouped: the stream carries two events
        _distinct("events-lo", field="event", min=3),
        _distinct("events-hi", field="event", max=2),
    ), ladder_snapshot)
    failing = {r["id"]: r["failing"] for r in out["statistics"]["results"]}
    assert failing == {"exact": 1, "pair": 2, "events-lo": 1, "events-hi": 0}
    assert out["statistics"]["rows"] == {"ladder": 6}
    assert out["gating"] == "block"


def test_distinct_count_over_an_empty_stream(root, registry, snapshot):
    """Ungrouped, the whole stream is the one group and it EXISTS while
    empty — a min fails, as row_count's does. Grouped, an empty stream has
    no groups to fail; emptiness is row_count's assertion."""
    out = run_suite(root, registry, _suite(
        Rule(id="whole", target="ghost", rule="distinct_count",
             kwargs={"field": "x", "min": 1}),
        Rule(id="grouped", target="ghost", rule="distinct_count",
             kwargs={"field": "x", "group_by": "g", "min": 1}),
    ), snapshot)
    failing = {r["id"]: r["failing"] for r in out["statistics"]["results"]}
    assert failing == {"whole": 1, "grouped": 0}


def test_cardinality_rules_accept_structured_values_and_keep_json_types_distinct(
    root, registry, fake_source
):
    values = [True, 1, 1.0, [1], [1], {"x": 1}, {"x": 1}]
    FakeConnector.script = [
        record("json-values", "2026-01-02", {"value": value})
        for value in values
    ]
    snapshot = run_acquisition(
        root, registry, "fake", "json-values", "live"
    )["snapshot"]
    out = run_suite(
        root,
        registry,
        _suite(
            Rule(
                id="unique",
                target="json-values",
                rule="unique",
                kwargs={"field": "value"},
            ),
            Rule(
                id="distinct",
                target="json-values",
                rule="distinct_count",
                kwargs={"field": "value", "min": 5, "max": 5},
            ),
        ),
        snapshot,
    )
    failing = {r["id"]: r["failing"] for r in out["statistics"]["results"]}
    assert failing == {"unique": 4, "distinct": 0}


def test_distinct_count_reads_from_a_json_suite():
    suite = ValidationSuite.from_obj({"name": "s", "rules": [
        {"id": "strikes", "target": "ladder", "rule": "distinct_count",
         "kwargs": {"field": "strike", "group_by": ["event"], "min": 2},
         "severity": "warn", "warn_if": "> 0"}]})
    rule = suite.rules[0]
    assert rule.kwargs == {"field": "strike", "group_by": ["event"], "min": 2}
    assert suite.to_obj()["rules"][0]["kwargs"] == rule.kwargs
