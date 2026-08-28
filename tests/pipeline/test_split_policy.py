"""The split-assignment POLICY seam: which INSTANT a record is cut on.

``TimeSplitConfig.split_of`` cut every record on its own ``asof_ms``, so an
event whose leads span a cut landed in TWO splits. The event is the
toolkit's independence unit, so a ``val|test`` straddle puts the same event
in both model selection and scoring. Measured on one project's real store
at a shipped document's pinned cuts, a long-horizon series straddled on 5
of 7 events — every one of them ``val|test``.

The properties this file pins:

* ``record`` is the default and is BYTE-IDENTICAL to the old behaviour,
  including its serialized form — adding the knob must not re-hash runs
  that already happened;
* a DECLARED policy IS hash-material, because assigning by event is a
  different experiment;
* ``event-close`` and ``event-open`` both make an event ATOMIC (zero
  straddles), and they move a straddler in OPPOSITE directions;
* an event policy with no bounds REFUSES rather than falling back to the
  record instant — a silent fallback would be the leak, restored;
* a bounds map that misses an event REFUSES for the same reason;
* ``event_bounds`` is derived, never identity, never declarable;
* a third policy is a REGISTRATION, not a rewrite;
* ``straddle_report`` counts the leak and names the boundary it crosses;
* the driver binds bounds from the data nodes' ``event_bounds()``, unions
  several sources, and refuses when none can answer.

PURE-TOOLKIT suite: stdlib plus ``dskit.pipeline`` only.
"""

import pathlib
import re
from types import SimpleNamespace

import pytest

import dskit.pipeline
from dskit.pipeline.base import (
    ConfigError,
    OutputsConfig,
    TimeSplitConfig,
    config_hash,
)
from dskit.pipeline.document import TrailingSplitSpec, doc_split_from_obj
from dskit.pipeline.driver import _bind_event_bounds, run_document
from dskit.pipeline.node import Node
from dskit.pipeline.split_policy import (
    SPLIT_NAMES,
    SPLIT_POLICIES,
    EventBounds,
    event_bounds_from_records,
    merge_event_bounds,
    policy_instant,
    register_split_policy,
    straddle_report,
)
from tests.pipeline.dochelpers import banking_document, make_registry

CUTS = dict(train_end_ms=1_000, val_end_ms=2_000, test_end_ms=3_000)


def rec(asof_ms, cluster):
    return SimpleNamespace(asof_ms=asof_ms, cluster=cluster)


#: One event wholly in train, one wholly in test, and STRADDLER spanning the
#: val|test cut — the shape that matters.
RECORDS = [
    rec(500, "EARLY"),
    rec(900, "EARLY"),
    rec(1_900, "STRADDLER"),
    rec(2_100, "STRADDLER"),
    rec(2_500, "LATE"),
]
BOUNDS = event_bounds_from_records(RECORDS)


# --- the default must not move anything -----------------------------------


def test_record_is_the_default_and_keeps_the_old_behaviour():
    s = TimeSplitConfig(**CUTS)
    assert s.policy == "record"
    assert s.needs_event_bounds is False
    assert s.split_of(rec(500, "E")) == "train"
    assert s.split_of(rec(1_500, "E")) == "val"
    assert s.split_of(rec(2_500, "E")) == "test"
    assert s.split_of(rec(9_999, "E")) is None
    # boundaries are inclusive-below, exactly as before
    assert s.split_of(rec(1_000, "E")) == "train"
    assert s.split_of(rec(2_000, "E")) == "val"


def test_default_policy_is_absent_from_the_serialized_form():
    """The knob must not re-hash every run that already ran."""
    s = TimeSplitConfig(**CUTS)
    assert "policy" not in s.to_obj()
    assert "event_bounds" not in s.to_obj()
    assert config_hash(s) == config_hash(TimeSplitConfig.from_obj({**CUTS, "kind": "time"}))
    # declaring the default explicitly is also hash-neutral
    assert config_hash(TimeSplitConfig(**CUTS, policy="record")) == config_hash(s)


def test_declared_policy_is_hash_material():
    """Assigning by event is a DIFFERENT experiment."""
    base = TimeSplitConfig(**CUTS)
    ev = TimeSplitConfig(**CUTS, policy="event-close")
    assert ev.to_obj()["policy"] == "event-close"
    assert config_hash(ev) != config_hash(base)


# --- the event policies ----------------------------------------------------


def test_event_close_moves_a_straddler_wholly_forward():
    s = TimeSplitConfig(**CUTS, policy="event-close").with_event_bounds(BOUNDS)
    assert s.needs_event_bounds is True
    assert {s.split_of(r) for r in RECORDS if r.cluster == "STRADDLER"} == {"test"}
    assert s.split_of(rec(500, "EARLY")) == "train"


def test_event_open_moves_a_straddler_wholly_backward():
    s = TimeSplitConfig(**CUTS, policy="event-open").with_event_bounds(BOUNDS)
    assert {s.split_of(r) for r in RECORDS if r.cluster == "STRADDLER"} == {"val"}


def test_both_event_policies_make_the_event_atomic():
    for policy in ("event-close", "event-open"):
        s = TimeSplitConfig(**CUTS, policy=policy).with_event_bounds(BOUNDS)
        assert straddle_report(s, RECORDS)["events_straddling"] == 0, policy


# --- refusals: a silent fallback would restore the leak --------------------


def test_event_policy_without_bounds_refuses():
    s = TimeSplitConfig(**CUTS, policy="event-close")
    with pytest.raises(ValueError, match="no event-bounds map was resolved"):
        s.split_of(rec(1_900, "STRADDLER"))


def test_event_policy_with_a_missing_cluster_refuses():
    s = TimeSplitConfig(**CUTS, policy="event-close").with_event_bounds(BOUNDS)
    with pytest.raises(ValueError, match="no bounds for cluster"):
        s.split_of(rec(1_900, "NEVER_SEEN"))


def test_unknown_policy_is_refused_by_name():
    with pytest.raises(ConfigError, match="unknown policy 'nope'"):
        TimeSplitConfig(**CUTS, policy="nope")


def test_event_bounds_is_not_declarable():
    with pytest.raises(ConfigError, match="unknown key"):
        TimeSplitConfig.from_obj({**CUTS, "kind": "time", "event_bounds": {}})


def test_bound_map_is_not_mutable_through_the_caller():
    live = dict(BOUNDS)
    s = TimeSplitConfig(**CUTS, policy="event-close").with_event_bounds(live)
    live.clear()
    assert s.split_of(rec(1_900, "STRADDLER")) == "test"


# --- bounds derivation -----------------------------------------------------


def test_bounds_are_the_observed_extent():
    assert BOUNDS["STRADDLER"] == EventBounds(open_ms=1_900, close_ms=2_100)
    assert BOUNDS["STRADDLER"].span_ms == 200


def test_bounds_read_dicts_and_objects_alike():
    assert event_bounds_from_records([{"asof_ms": 5, "cluster": "A"}])["A"].open_ms == 5


def test_merge_unions_and_widens():
    a = {"X": EventBounds(10, 20), "ONLY_A": EventBounds(1, 2)}
    b = {"X": EventBounds(5, 30), "ONLY_B": EventBounds(3, 4)}
    merged = merge_event_bounds(a, b)
    assert merged["X"] == EventBounds(5, 30)
    assert set(merged) == {"X", "ONLY_A", "ONLY_B"}


def test_bounds_must_not_run_backward():
    with pytest.raises(ValueError, match="must not run backward"):
        EventBounds(open_ms=10, close_ms=5)


# --- the audit -------------------------------------------------------------


def test_straddle_report_counts_the_leak_and_names_the_boundary():
    r = straddle_report(TimeSplitConfig(**CUTS), RECORDS)
    assert r["policy"] == "record"
    assert r["event_atomic"] is False
    assert r["events"] == 3
    assert r["events_straddling"] == 1
    assert r["boundaries"] == {"val|test": 1}
    assert r["straddling_events"] == ["STRADDLER"]


def test_straddle_report_shows_where_the_rows_went():
    """Fixing the leak MOVES rows; a fix that empties train is not a fix."""
    before = straddle_report(TimeSplitConfig(**CUTS), RECORDS)["rows_by_split"]
    after = straddle_report(
        TimeSplitConfig(**CUTS, policy="event-close").with_event_bounds(BOUNDS),
        RECORDS,
    )["rows_by_split"]
    assert before == {"train": 2, "val": 1, "test": 2}
    assert after == {"train": 2, "test": 3}  # the straddler's val row moved to test


def test_straddle_report_sees_the_cal_band():
    """ADR-0034: a straddler across the cal boundaries is named like any
    other — the leakage ledger cannot be blind to the fourth split."""
    cuts = TimeSplitConfig(**CUTS, cal_start_ms=1_950)
    r = straddle_report(cuts, RECORDS)
    # STRADDLER's rows land at 1900 (val) and 2100 (test): with the cal
    # band [1950, 2000] between them the boundary key spans it.
    assert r["rows_by_split"] == {"train": 2, "val": 1, "test": 2}
    with_cal_row = RECORDS + [rec(1_975, "STRADDLER")]
    r2 = straddle_report(cuts, with_cal_row)
    assert r2["rows_by_split"] == {"train": 2, "val": 1, "cal": 1, "test": 2}
    assert r2["boundaries"] == {"val|cal": 1, "cal|test": 1}
    # A no-cal config over the same records reports byte-identically to
    # the pre-ADR-0034 shape.
    assert straddle_report(TimeSplitConfig(**CUTS), RECORDS)["boundaries"] == {
        "val|test": 1
    }


# --- extension: a third policy is a registration ---------------------------


def test_a_third_policy_is_a_registration_not_a_rewrite():
    name = "event-midpoint-test-only"
    register_split_policy(
        name,
        lambda frame, bounds: (bounds[frame.cluster].open_ms + bounds[frame.cluster].close_ms) // 2,
        needs_bounds=True,
        doc="test-only",
    )
    try:
        s = TimeSplitConfig(**CUTS, policy=name).with_event_bounds(BOUNDS)
        # midpoint of (1900, 2100) is 2000 == val_end_ms, so the whole event
        # lands in val — a THIRD direction, and still atomic.
        assert {s.split_of(r) for r in RECORDS if r.cluster == "STRADDLER"} == {"val"}
        assert straddle_report(s, RECORDS)["events_straddling"] == 0
        with pytest.raises(ValueError, match="already registered"):
            register_split_policy(name, lambda f, b: 0, needs_bounds=True)
    finally:
        SPLIT_POLICIES.pop(name, None)


# --- the document grammar --------------------------------------------------


def test_trailing_carries_policy_through_materialization():
    spec = doc_split_from_obj(
        {"kind": "trailing", "test_days": 14, "val_days": 28, "policy": "event-close"}
    )
    assert spec.to_obj()["policy"] == "event-close"
    assert spec.materialize(10_000_000_000).policy == "event-close"


def test_trailing_default_policy_stays_out_of_the_hash():
    spec = TrailingSplitSpec(test_days=14, val_days=28)
    assert "policy" not in spec.to_obj()
    assert spec.materialize(10_000_000_000).policy == "record"


def test_document_refuses_an_unknown_trailing_policy():
    with pytest.raises(ConfigError, match="unknown policy"):
        doc_split_from_obj(
            {"kind": "trailing", "test_days": 14, "val_days": 28, "policy": "nope"}
        )


# --- the driver binding seam ----------------------------------------------


class _Src(Node):
    """A data node that can answer 'where does each event start and end'."""

    def __init__(self, key, bounds):
        super().__init__(key, {})
        self._bounds = bounds

    def event_bounds(self):
        return self._bounds

    def run(self, ctx, inputs):
        return {}


class _Mute(Node):
    def run(self, ctx, inputs):
        return {}


def test_record_policy_never_pays_for_the_scan():
    s = TimeSplitConfig(**CUTS)
    assert _bind_event_bounds(s, {"a": _Mute("a", {})}, lambda k: "data") is s


def test_driver_unions_bounds_from_several_sources():
    alpha = {"A1": EventBounds(1_900, 2_100)}
    beta = {"B1": EventBounds(400, 900)}
    bound = _bind_event_bounds(
        TimeSplitConfig(**CUTS, policy="event-close"),
        {"a": _Src("a", alpha), "b": _Src("b", beta)},
        lambda key: "data",
    )
    assert set(bound.event_bounds) == {"A1", "B1"}
    assert bound.split_of(rec(1_900, "A1")) == "test"
    assert bound.split_of(rec(400, "B1")) == "train"


def test_driver_refuses_when_no_source_can_answer():
    with pytest.raises(ConfigError, match="do not implement event_bounds"):
        _bind_event_bounds(
            TimeSplitConfig(**CUTS, policy="event-close"),
            {"a": _Mute("a", {})},
            lambda key: "data",
        )


def test_driver_ignores_non_data_nodes():
    with pytest.raises(ConfigError, match="no source supplied one"):
        _bind_event_bounds(
            TimeSplitConfig(**CUTS, policy="event-close"),
            {"k": _Src("k", {"K1": EventBounds(1, 2)})},
            lambda key: "report",
        )


def test_the_embargo_band_applies_to_the_policy_instant():
    """ADR-0024 x ADR-0027 composition, as base.py promises: under an
    event policy the ``val_start_ms`` embargo drops records by their
    EVENT's instant, never their own. X's record sits inside the band by
    asof but its event closes past val_start -> val; Y's record sits
    past the band by asof but its event closes inside -> dropped
    (merge-review gap: this pairing had zero coverage)."""
    split = TimeSplitConfig(
        train_end_ms=1_000,
        val_start_ms=1_500,
        val_end_ms=2_000,
        test_end_ms=3_000,
        policy="event-close",
    ).with_event_bounds(
        {"X": EventBounds(900, 1_600), "Y": EventBounds(400, 1_200)}
    )
    assert split.split_of(rec(1_200, "X")) == "val"
    assert split.split_of(rec(1_600, "Y")) is None


def test_policy_instant_refuses_an_unknown_name_directly():
    # The REGISTRY's own refusal (review F2): the config layer's
    # membership check shadows this lookup on the normal route, so a
    # silent fallback to the record instant here — the leak, restored —
    # would survive every config-path test. Pin the lookup itself.
    with pytest.raises(ValueError, match="unknown policy 'nope'"):
        policy_instant("nope", rec(1_500, "X"), {})


def test_run_document_binds_at_resolve_and_refuses_loudly(tmp_path):
    # The CALL SITE in run_document (review F6): the driver tests above
    # invoke _bind_event_bounds directly, so a driver that stopped
    # calling it would quietly degrade this resolve-time ConfigError to
    # a per-record ValueError deep in EXECUTE. The refusal must also
    # land BEFORE any run directory exists.
    doc = banking_document(
        splits=TimeSplitConfig(**CUTS, policy="event-close"),
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )
    with pytest.raises(ConfigError, match="do not implement event_bounds"):
        run_document(doc, asof="2026-01-01", registry=make_registry())
    assert not any(tmp_path.iterdir())


def test_the_split_vocabulary_is_spelled_ONCE_in_the_tree():
    """One tuple, one home — the rest IMPORT it.

    ``cal`` was added to this vocabulary by ADR-0034, and the shape that
    made that a risky edit is a literal copy of the tuple in a module
    that never hears about the change: the planner and the fitted family
    would accept a document that ``sb3-eval``, ``validate`` and
    ``SynthScore`` then refuse, with nothing comparing the tuples. The
    scan is over source because that IS the defect — a second copy is
    invisible at runtime until the day the two disagree.
    """
    root = pathlib.Path(dskit.pipeline.__file__).parent
    quoted = [f'"{name}"' for name in ("train", "val", "cal", "test")]
    literal = re.compile(r"\s*,\s*".join(quoted))
    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in sorted(root.rglob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if literal.search(line) and "SPLIT_NAMES =" not in line
    ]
    assert offenders == [], offenders
    assert SPLIT_NAMES == ("train", "val", "cal", "test"), "the value itself"
