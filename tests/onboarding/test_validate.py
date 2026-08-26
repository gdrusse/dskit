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
