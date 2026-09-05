"""`monitors.py` — the watch over the decision stream (§5.10, D16).

A `Monitor` is not a `Measure` (§5.15): a measure answers about ONE proposal
against a snapshotted account at decision time, a monitor answers about a
WINDOW of already-recorded decisions, and its statuses include
`insufficient` — "I cannot answer yet" — which is the first-class thing D16
exists to keep. So the two rules that carry the safety weight are pinned
first and hardest: below `min_n` the verdict is never `ok`, and the trailing
partial chunk is never `ok` either. A drift monitor that answers `ok` from
thirty samples is worse than one that says nothing.

Every statistic here is hand-computed in this file — PSI from `math.log`
over proportions, the χ² benchmark from `statistics.NormalDist`, the
Kolmogorov value from `math.sqrt`, the KS case small enough to read off two
ECDFs, PageHinkley walked through its recursion. A statistic asserted
against the code that produced it asserts nothing (CLAUDE.md).

Readings taken where §5.10 is silent (each is reported to the orchestrator
as a plan gap; each is the reading this module's neighbours imply):

* A breach yields `alarm` when `response` is `halt` and `warn` otherwise —
  `Threshold.breached` returns one bit, so the severity can only come from
  the declared response. `should_trip()` is then exactly `status == alarm`.
* `Threshold` publishes the number the verdict reports through a concrete
  `bound(n_ref, n_cur)`; only `breached` is abstract.
* `alpha` cannot know a statistic's null distribution, so the OWNING
  monitor injects it: `Alpha(params, benchmark=...)`, filled from
  `DistributionMonitor.critical_value(alpha, n_ref, n_cur)`. An `alpha`
  threshold on a monitor with no benchmark refuses at construction.
* References are fed by `fit(records)` and by each chunk as it CLOSES, so
  the current window is never part of its own reference (a rolling
  reference that contained the window it is compared against would report
  no drift, ever).
* `verdict()` reads the CURRENT window — the last chunk the chunker yields —
  and a window that is not yet full is `insufficient`. `period` is therefore
  pinned at the chunker only: with no injected clock a monitor cannot know
  its open period has closed, and §5.10 does not say what closes one.
* A record that carries none of a monitor's fields is not an observation.
* `Staleness` reduces its window with `max` (the worst staleness in the
  window is the safety question); `LatencyPercentiles` reports p95 by
  default and sums a §6 `latency_ms` phase map to one tick latency;
  `TrackingSignal` reports |Σe|/MAD so a `constant.max` bound reads as the
  classic |TS| > k.
* `PageHinkley` is the classic recursion `PH_t = max(0, PH_{t−1} + (x_t −
  x̄_t − δ))` with x̄_t the running mean INCLUDING x_t, its bound λ supplied
  by the `threshold` strategy (one bound mechanism, not two), and a full
  reset — accumulator AND running mean — on alarm, so the observation after
  an alarm scores exactly 0 instead of re-alarming forever.
"""

import dataclasses
import json
import math
import statistics

import pytest

import dskit.production.monitors as monitors
from dskit.production.base import ProductionError
from dskit.production.monitors import (
    CHUNKER_KINDS,
    DEFAULT_MIN_N,
    MONITOR_KINDS,
    REFERENCE_KINDS,
    THRESHOLD_KINDS,
    KS,
    PSI,
    Alpha,
    Chunker,
    Constant,
    Count,
    Coverage,
    DecisionRate,
    DistributionMonitor,
    LatencyPercentiles,
    Leading,
    Monitor,
    OperationalMonitor,
    PageHinkley,
    Period,
    Profile,
    Reference,
    ReferenceStd,
    RefusalCount,
    Rolling,
    Sliding,
    Snapshot,
    Staleness,
    StreamMonitor,
    Threshold,
    TrackingSignal,
)
from dskit.production.vocab import MONITOR_STATUSES, RESPONSES, TICK_PHASES

#: §5.10's phase-1 members, one registry name each.
KINDS = (
    "coverage",
    "decision_rate",
    "ks",
    "latency",
    "page_hinkley",
    "psi",
    "refusals",
    "staleness",
    "tracking_signal",
)

#: §6's `monitor` record body. `monitor` is the owner's name for the
#: instance; every other field comes off the `Verdict`.
MONITOR_BODY = (
    "monitor",
    "slice",
    "window",
    "statistic",
    "threshold",
    "status",
    "provisional",
)


# ---------------------------------------------------------------------------
# Records — the §6 shapes a monitor observes
# ---------------------------------------------------------------------------


def tick_record(
    tick_id="t-1",
    status="decided",
    data_asof_ms=1_000,
    observed_at_ms=1_000,
    latency_ms=0,
    refusal_reason=None,
):
    """A §6 `tick` body with every field an operational monitor reads."""
    latency = dict.fromkeys(TICK_PHASES, 0)
    latency["propose"] = latency_ms
    return {
        "kind": "tick",
        "id": tick_id,
        "tick_id": tick_id,
        "status": status,
        "data_asof_ms": data_asof_ms,
        "observed_at_ms": observed_at_ms,
        "latency_ms": latency,
        "refusal_reason": refusal_reason,
    }


def leg(final="buy", **fields):
    """One element of a §6 `decision` record's `legs[]`."""
    body = {"leg_id": "l-1", "instrument": "INS1", "final": final}
    body.update(fields)
    return body


def decision_record(legs, tick_id="t-1"):
    """A §6 `decision` body carrying the given legs."""
    return {
        "kind": "decision",
        "id": "d-" + tick_id,
        "tick_id": tick_id,
        "decision_plan_ids": ["p-" + tick_id],
        "legs": list(legs),
    }


def prediction_record(prediction, **fields):
    """A decision whose one leg carries `prediction` (§6 puts it on the leg)."""
    return decision_record([leg(prediction=prediction, **fields)])


def flat_record(**fields):
    """A record carrying the named fields at its top level, not on a leg."""
    body = {"kind": "outcome", "id": "o-1"}
    body.update(fields)
    return body


def fill_record():
    """A record no §5.10 monitor reads."""
    return {"kind": "fill", "id": "f-1", "qty": "1", "price": "10.00"}


# ---------------------------------------------------------------------------
# Params — the §4.1 selector sites, small enough to read
# ---------------------------------------------------------------------------


def count(n):
    """A `{kind, ...}` window site over `CHUNKER_KINDS`."""
    return {"kind": "count", "n": n}


def sliding(n, step=1):
    return {"kind": "sliding", "n": n, "step": step}


def at_most(value):
    """A `{kind, ...}` threshold site over `THRESHOLD_KINDS`."""
    return {"kind": "constant", "max": value}


def at_least(value):
    return {"kind": "constant", "min": value}


def leading(n):
    """A `{uses, params}` reference site over `REFERENCE_KINDS`."""
    return {"uses": "leading", "params": {"n": n}}


def rolling(window):
    return {"uses": "rolling", "params": {"window": window}}


NEVER = 1e9  # a bound no statistic in this file reaches


def kind_params(kind, **overrides):
    """Minimal valid params for one registry kind, plus the common knobs."""
    params = {
        "window": count(2),
        "threshold": at_most(NEVER),
        "response": "log",
        "min_n": 2,
    }
    extra = {
        "psi": {"field": "prediction", "bins": 2, "reference": leading(2)},
        "ks": {"field": "prediction", "reference": leading(2)},
        "page_hinkley": {"field": "prediction"},
        "tracking_signal": {"field": "prediction", "target_field": "realised"},
    }
    params.update(extra.get(kind, {}))
    params.update(overrides)
    return params


def kind_record(kind, i):
    """One record the given kind counts as an observation, varying with `i`."""
    if kind in ("psi", "ks", "page_hinkley"):
        return prediction_record(float(i))
    if kind == "tracking_signal":
        return prediction_record(float(i), realised=float(i) + 1.0)
    if kind == "coverage":
        return decision_record([leg(final="none")], tick_id="t-%d" % i)
    if kind == "latency":
        return tick_record(tick_id="t-%d" % i, latency_ms=10 + i)
    if kind == "refusals":
        return tick_record(tick_id="t-%d" % i, status="refused", refusal_reason="stale")
    return tick_record(
        tick_id="t-%d" % i, data_asof_ms=1_000 * i, observed_at_ms=1_000 * i + 5_000
    )


def build(kind, **overrides):
    """Construct a monitor through its registry, the way `compose` does."""
    return MONITOR_KINDS.resolve(kind)(kind_params(kind, **overrides), name=kind)


def feed(monitor, records):
    """Observe a sequence and return the monitor."""
    for record in records:
        monitor.observe(record)
    return monitor


# ---------------------------------------------------------------------------
# Statistics restated here — never imported from the subject
# ---------------------------------------------------------------------------


def psi_of(ref_counts, cur_counts):
    """Σ (cur% − ref%)·ln(cur%/ref%) over matching bins, with `math.log`."""
    n_ref, n_cur = sum(ref_counts), sum(cur_counts)
    total = 0.0
    for ref, cur in zip(ref_counts, cur_counts):
        ref_p, cur_p = ref / n_ref, cur / n_cur
        total += (cur_p - ref_p) * math.log(cur_p / ref_p)
    return total


def psi_benchmark(alpha, bins, n_ref, n_cur):
    """§5.10's PSI benchmark `(1/n + 1/m)·(B−1 + z_α·√(2(B−1)))`."""
    z = statistics.NormalDist().inv_cdf(1.0 - alpha)
    return (1.0 / n_ref + 1.0 / n_cur) * ((bins - 1) + z * math.sqrt(2 * (bins - 1)))


def ks_benchmark(alpha, n_ref, n_cur):
    """The Kolmogorov critical value `√(−ln(α/2)/2)·√((n+m)/(n·m))`."""
    return math.sqrt(-math.log(alpha / 2.0) / 2.0) * math.sqrt(
        (n_ref + n_cur) / (n_ref * n_cur)
    )


def fed(threshold, seen):
    """Push a history of statistics through a threshold and return it."""
    for statistic in seen:
        threshold.breached(statistic, 10, 10)
    return threshold


# ---------------------------------------------------------------------------
# The seam: four ABCs, four registries
# ---------------------------------------------------------------------------


def test_observe_and_verdict_are_abstract_so_an_incomplete_monitor_cannot_construct():
    assert Monitor.__abstractmethods__ == frozenset({"observe", "verdict"})
    with pytest.raises(TypeError):
        Monitor({})


def test_fit_state_and_restore_are_concrete_on_the_monitor_base():
    for hook in ("fit", "state", "restore", "should_trip"):
        assert hook not in Monitor.__abstractmethods__
        assert callable(getattr(Monitor, hook))


def test_each_strategy_is_an_abc_with_exactly_one_abstract_hook():
    assert Reference.__abstractmethods__ == frozenset({"sample"})
    assert Chunker.__abstractmethods__ == frozenset({"chunks"})
    assert Threshold.__abstractmethods__ == frozenset({"breached"})
    for abc in (Reference, Chunker, Threshold):
        with pytest.raises(TypeError):
            abc({})


def test_response_is_a_closed_vocabulary_not_a_strategy_object():
    assert not hasattr(monitors, "Response")
    assert not hasattr(monitors, "RESPONSE_KINDS")
    assert RESPONSES == ("log", "warn", "halt")


def test_the_registry_lists_exactly_the_nine_phase_one_monitor_kinds():
    assert MONITOR_KINDS.kinds() == KINDS
    assert MONITOR_KINDS.family == "monitor"
    for name, cls in (
        ("staleness", Staleness),
        ("decision_rate", DecisionRate),
        ("coverage", Coverage),
        ("latency", LatencyPercentiles),
        ("refusals", RefusalCount),
        ("page_hinkley", PageHinkley),
        ("tracking_signal", TrackingSignal),
        ("psi", PSI),
        ("ks", KS),
    ):
        assert MONITOR_KINDS.resolve(name) is cls


def test_the_phase_two_families_are_not_registered_yet():
    for name in ("calibration", "brier", "skill", "prediction_bias", "ddm", "adwin"):
        assert name not in MONITOR_KINDS
        with pytest.raises(ProductionError):
            MONITOR_KINDS.resolve(name)


def test_the_three_strategy_registries_name_their_kinds():
    assert REFERENCE_KINDS.kinds() == ("leading", "rolling", "snapshot")
    assert CHUNKER_KINDS.kinds() == ("count", "period", "sliding")
    assert THRESHOLD_KINDS.kinds() == ("alpha", "constant", "reference_std")
    assert REFERENCE_KINDS.family == "reference"
    assert CHUNKER_KINDS.family == "chunker"
    assert THRESHOLD_KINDS.family == "threshold"
    assert REFERENCE_KINDS.resolve("leading") is Leading
    assert REFERENCE_KINDS.resolve("rolling") is Rolling
    assert REFERENCE_KINDS.resolve("snapshot") is Snapshot
    assert CHUNKER_KINDS.resolve("count") is Count
    assert CHUNKER_KINDS.resolve("period") is Period
    assert CHUNKER_KINDS.resolve("sliding") is Sliding
    assert THRESHOLD_KINDS.resolve("constant") is Constant
    assert THRESHOLD_KINDS.resolve("reference_std") is ReferenceStd
    assert THRESHOLD_KINDS.resolve("alpha") is Alpha


def test_the_strategy_registries_refuse_an_unregistered_name():
    for registry, name in (
        (REFERENCE_KINDS, "run"),
        (CHUNKER_KINDS, "cron"),
        (THRESHOLD_KINDS, "quantile"),
    ):
        with pytest.raises(ProductionError):
            registry.resolve(name)


def test_the_families_are_is_a_hierarchies_under_monitor():
    for family in (OperationalMonitor, StreamMonitor, DistributionMonitor):
        assert issubclass(family, Monitor)
    for cls in (Staleness, DecisionRate, Coverage, LatencyPercentiles, RefusalCount):
        assert issubclass(cls, OperationalMonitor)
    for cls in (PageHinkley, TrackingSignal):
        assert issubclass(cls, StreamMonitor)
    for cls in (PSI, KS):
        assert issubclass(cls, DistributionMonitor)
    assert not issubclass(StreamMonitor, OperationalMonitor)
    assert not issubclass(DistributionMonitor, StreamMonitor)


# ---------------------------------------------------------------------------
# Construction — default-deny at every level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_every_monitor_accepts_the_four_common_knobs(kind):
    monitor = build(kind, window=count(3), threshold=at_most(5.0), response="halt", min_n=3)
    assert isinstance(monitor, Monitor)


@pytest.mark.parametrize("kind", KINDS)
def test_an_unknown_top_level_param_refuses_naming_it(kind):
    with pytest.raises(ProductionError) as exc:
        build(kind, treshold=at_most(1.0))
    assert "treshold" in str(exc.value)


@pytest.mark.parametrize("kind", KINDS)
def test_notes_are_allowed_beside_a_monitors_knobs(kind):
    build(kind, notes="why this monitor exists and what to change")


def test_notes_are_allowed_inside_every_nested_selector_site():
    Count({"n": 4, "notes": "why four"})
    Constant({"max": 1.0, "notes": "why one"})
    Leading({"n": 4, "notes": "the fixed anchor"})
    build(
        "psi",
        window={"kind": "count", "n": 4, "notes": "why four"},
        threshold={"kind": "constant", "max": 1.0, "notes": "why one"},
        reference={"uses": "leading", "params": {"n": 4, "notes": "the anchor"}},
    )


def test_an_unknown_window_kind_refuses_naming_the_selector():
    with pytest.raises(ProductionError) as exc:
        build("coverage", window={"kind": "hourly", "n": 4})
    assert "window" in str(exc.value)
    assert "hourly" in str(exc.value)


def test_an_unknown_threshold_kind_refuses_naming_the_selector():
    with pytest.raises(ProductionError) as exc:
        build("coverage", threshold={"kind": "quantile", "q": 0.9})
    assert "threshold" in str(exc.value)
    assert "quantile" in str(exc.value)


def test_an_unknown_reference_uses_refuses_naming_the_selector():
    with pytest.raises(ProductionError) as exc:
        build("psi", reference={"uses": "run", "params": {}})
    assert "reference" in str(exc.value)
    assert "run" in str(exc.value)


def test_an_unknown_param_inside_a_nested_selector_site_refuses():
    with pytest.raises(ProductionError) as exc:
        build("coverage", window={"kind": "count", "n": 4, "step": 2})
    assert "step" in str(exc.value)


def test_a_selector_site_missing_its_kind_or_uses_key_refuses():
    with pytest.raises(ProductionError):
        build("coverage", window={"n": 4})
    with pytest.raises(ProductionError):
        build("coverage", threshold={"max": 1.0})
    with pytest.raises(ProductionError):
        build("psi", reference={"params": {"n": 4}})


def test_a_response_outside_the_vocabulary_refuses():
    for response in RESPONSES:
        build("coverage", response=response)
    with pytest.raises(ProductionError) as exc:
        build("coverage", response="page")
    assert "page" in str(exc.value)


@pytest.mark.parametrize("bad", (0, -1, 1.5, "10"))
def test_min_n_must_be_a_positive_integer(bad):
    with pytest.raises(ProductionError):
        build("coverage", min_n=bad)


def test_psi_takes_a_bins_knob_and_ks_which_is_distribution_free_does_not():
    build("psi", bins=10)
    with pytest.raises(ProductionError) as exc:
        build("ks", bins=10)
    assert "bins" in str(exc.value)


@pytest.mark.parametrize("bad", (1, 0, -2, 2.5))
def test_psi_refuses_a_bin_count_below_two(bad):
    with pytest.raises(ProductionError):
        build("psi", bins=bad)


def test_a_distribution_monitor_without_a_reference_refuses():
    for kind in ("psi", "ks"):
        params = kind_params(kind)
        del params["reference"]
        with pytest.raises(ProductionError) as exc:
            MONITOR_KINDS.resolve(kind)(params)
        assert "reference" in str(exc.value)


@pytest.mark.parametrize("kind", ("psi", "ks", "page_hinkley", "tracking_signal"))
def test_a_field_reading_monitor_without_a_field_refuses(kind):
    params = kind_params(kind)
    del params["field"]
    with pytest.raises(ProductionError) as exc:
        MONITOR_KINDS.resolve(kind)(params)
    assert "field" in str(exc.value)


def test_an_operational_monitor_has_no_field_or_reference_knob():
    for kind in ("staleness", "decision_rate", "coverage", "refusals"):
        with pytest.raises(ProductionError):
            build(kind, field="prediction")
        with pytest.raises(ProductionError):
            build(kind, reference=leading(4))


def test_the_monitor_name_is_supplied_by_its_owner_and_defaults_to_none():
    assert MONITOR_KINDS.resolve("psi")(kind_params("psi")).name is None
    named = MONITOR_KINDS.resolve("psi")(kind_params("psi"), name="pred_shift")
    assert named.name == "pred_shift"


def test_the_document_example_constructs_through_the_registry():
    """§4.1's `monitors` block, verbatim."""
    site = {
        "uses": "psi",
        "params": {
            "field": "prediction",
            "bins": 10,
            "reference": {"uses": "leading", "params": {"n": 500}},
            "window": {"kind": "count", "n": 300},
            "threshold": {"kind": "alpha", "alpha": 0.01},
            "response": "warn",
        },
    }
    monitor = MONITOR_KINDS.resolve(site["uses"])(site["params"], name="pred_shift")
    assert isinstance(monitor, PSI)
    assert monitor.name == "pred_shift"

    site = {
        "uses": "coverage",
        "params": {
            "window": {"kind": "count", "n": 50},
            "threshold": {"kind": "constant", "min": 0.5},
            "response": "warn",
        },
    }
    assert isinstance(
        MONITOR_KINDS.resolve(site["uses"])(site["params"], name="coverage"), Coverage
    )


# ---------------------------------------------------------------------------
# Chunker — how observations are cut into windows
# ---------------------------------------------------------------------------


def test_count_cuts_disjoint_chunks_and_yields_the_trailing_partial_one():
    assert tuple(Count({"n": 2}).chunks([1, 2, 3, 4, 5])) == ((1, 2), (3, 4), (5,))
    assert tuple(Count({"n": 2}).chunks([1, 2, 3, 4])) == ((1, 2), (3, 4))
    assert tuple(Count({"n": 2}).chunks([])) == ()


@pytest.mark.parametrize("bad", (0, -1, 2.5, "4"))
def test_count_refuses_a_non_positive_size(bad):
    with pytest.raises(ProductionError):
        Count({"n": bad})


def test_sliding_yields_a_full_window_at_every_step():
    assert tuple(Sliding({"n": 3, "step": 2}).chunks([1, 2, 3, 4, 5, 6, 7])) == (
        (1, 2, 3),
        (3, 4, 5),
        (5, 6, 7),
    )
    assert tuple(Sliding({"n": 3, "step": 1}).chunks([1, 2, 3, 4])) == (
        (1, 2, 3),
        (2, 3, 4),
    )


def test_sliding_yields_nothing_until_the_first_window_is_full():
    assert tuple(Sliding({"n": 3, "step": 1}).chunks([1, 2])) == ()


def test_period_groups_records_into_epoch_aligned_buckets():
    records = [
        tick_record(tick_id="a", data_asof_ms=0),
        tick_record(tick_id="b", data_asof_ms=30_000),
        tick_record(tick_id="c", data_asof_ms=59_999),
        tick_record(tick_id="d", data_asof_ms=60_000),
        tick_record(tick_id="e", data_asof_ms=180_000),
    ]
    chunks = tuple(Period({"iso": "PT1M"}).chunks(records))
    assert [[r["tick_id"] for r in chunk] for chunk in chunks] == [
        ["a", "b", "c"],
        ["d"],
        ["e"],
    ]


def test_period_reads_a_declared_time_field_instead_of_data_asof_ms():
    records = [
        {"kind": "tick", "id": "a", "data_asof_ms": 0, "observed_at_ms": 0},
        {"kind": "tick", "id": "b", "data_asof_ms": 0, "observed_at_ms": 60_000},
    ]
    same = tuple(Period({"iso": "PT1M"}).chunks(records))
    split = tuple(Period({"iso": "PT1M", "time_field": "observed_at_ms"}).chunks(records))
    assert len(same) == 1
    assert len(split) == 2


@pytest.mark.parametrize("bad", ("1 minute", "PT", "P1M", "", "pt1m"))
def test_period_refuses_a_malformed_or_calendar_iso_duration(bad):
    with pytest.raises(ProductionError):
        Period({"iso": bad})


# ---------------------------------------------------------------------------
# Reference — the comparison population
# ---------------------------------------------------------------------------


def test_leading_freezes_after_its_first_n_values():
    anchor = Leading({"n": 3})
    assert anchor.sample() == ()
    for value in (1.0, 2.0, 3.0, 99.0, 98.0):
        anchor.add(value)
    assert anchor.sample() == (1.0, 2.0, 3.0)


def test_rolling_keeps_the_last_window_values_and_moves():
    window = Rolling({"window": 3})
    for value in (1.0, 2.0, 3.0, 4.0, 5.0):
        window.add(value)
    assert window.sample() == (3.0, 4.0, 5.0)


def test_a_snapshot_reference_reads_no_file_at_construction(tmp_path):
    Snapshot({"path": str(tmp_path / "never-written.json")})


def test_a_snapshot_reference_reads_its_profile_at_fit(tmp_path):
    records = [prediction_record(float(i)) for i in range(1, 11)]
    profile = Profile.from_records(
        [r["legs"][0] for r in records], ("prediction",), 2, 3
    )
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile.to_obj()), encoding="utf-8")

    monitor = MONITOR_KINDS.resolve("psi")(
        {
            "field": "prediction",
            "bins": 2,
            "reference": {"uses": "snapshot", "params": {"path": str(path)}},
            "window": count(4),
            "threshold": at_most(NEVER),
            "min_n": 4,
        }
    )
    monitor.fit(())
    feed(monitor, [prediction_record(float(i)) for i in (1, 2, 3, 4)])
    assert monitor.verdict().n_ref == 10


def test_a_missing_snapshot_refuses_at_fit(tmp_path):
    monitor = MONITOR_KINDS.resolve("psi")(
        {
            "field": "prediction",
            "bins": 2,
            "reference": {
                "uses": "snapshot",
                "params": {"path": str(tmp_path / "gone.json")},
            },
            "window": count(4),
            "threshold": at_most(NEVER),
        }
    )
    with pytest.raises(ProductionError) as exc:
        monitor.fit(())
    assert "gone.json" in str(exc.value)


# ---------------------------------------------------------------------------
# Threshold — one bit, plus the number the verdict reports
# ---------------------------------------------------------------------------


def test_constant_breaches_outside_its_bounds_and_not_on_them():
    upper = Constant({"max": 0.5})
    assert upper.breached(0.6, 10, 10) is True
    assert upper.breached(0.5, 10, 10) is False
    lower = Constant({"min": 0.5})
    assert lower.breached(0.4, 10, 10) is True
    assert lower.breached(0.5, 10, 10) is False
    both = Constant({"min": 0.0, "max": 1.0})
    assert both.breached(-0.1, 10, 10) is True
    assert both.breached(1.1, 10, 10) is True
    assert both.breached(0.5, 10, 10) is False


def test_constant_without_a_bound_refuses():
    with pytest.raises(ProductionError):
        Constant({})


def test_constant_publishes_the_bound_the_verdict_reports():
    assert Constant({"max": 0.25}).bound(10, 10) == 0.25
    assert Constant({"min": 0.5}).bound(10, 10) == 0.5


def test_reference_std_breaches_beyond_k_sigma_of_the_statistics_it_has_seen():
    seen = [1.0, 2.0, 3.0, 4.0, 5.0]
    bound = 2 * statistics.pstdev(seen)
    assert fed(ReferenceStd({"k": 2}), seen).bound(10, 10) == pytest.approx(bound)
    assert fed(ReferenceStd({"k": 2}), seen).breached(3.0 + bound + 0.1, 10, 10) is True
    assert fed(ReferenceStd({"k": 2}), seen).breached(3.0 - bound - 0.1, 10, 10) is True
    assert fed(ReferenceStd({"k": 2}), seen).breached(3.0 + bound - 0.1, 10, 10) is False


def test_reference_std_cannot_breach_before_it_has_two_statistics():
    threshold = ReferenceStd({"k": 2})
    assert threshold.breached(1e9, 10, 10) is False
    assert threshold.breached(1e9, 10, 10) is False


def test_alpha_refuses_without_the_benchmark_its_owner_injects():
    with pytest.raises(ProductionError) as exc:
        Alpha({"alpha": 0.01})
    assert "benchmark" in str(exc.value)


def test_alpha_breaches_above_the_benchmark_its_owner_supplies():
    threshold = Alpha({"alpha": 0.01}, benchmark=lambda alpha, n_ref, n_cur: alpha * n_ref)
    assert threshold.bound(100, 50) == pytest.approx(1.0)
    assert threshold.breached(1.01, 100, 50) is True
    assert threshold.breached(0.99, 100, 50) is False


def test_an_operational_monitor_cannot_declare_an_alpha_threshold():
    with pytest.raises(ProductionError):
        build("coverage", threshold={"kind": "alpha", "alpha": 0.01})


# ---------------------------------------------------------------------------
# OperationalMonitor — one record field each
# ---------------------------------------------------------------------------


def test_staleness_reports_the_gap_between_observation_and_data_in_ms():
    monitor = build("staleness", window=count(4), threshold=at_most(NEVER), min_n=1)
    feed(
        monitor,
        [
            tick_record(tick_id="t-%d" % i, data_asof_ms=i * 1_000, observed_at_ms=i * 1_000 + 5_000)
            for i in range(4)
        ],
    )
    verdict = monitor.verdict()
    assert verdict.statistic == 5_000
    assert verdict.n_cur == 4


def test_staleness_reduces_its_window_to_the_worst_observation():
    monitor = build("staleness", window=count(4), threshold=at_most(8_000), min_n=1)
    for age in (1_000, 9_000, 2_000, 3_000):
        monitor.observe(tick_record(data_asof_ms=0, observed_at_ms=age))
    verdict = monitor.verdict()
    assert verdict.statistic == 9_000
    assert verdict.status == "warn"
    assert verdict.threshold == 8_000


def test_a_record_a_monitor_does_not_read_is_not_an_observation():
    monitor = build("staleness", window=count(4), threshold=at_most(NEVER), min_n=1)
    feed(monitor, [fill_record(), fill_record()])
    assert monitor.verdict().n_cur == 0
    assert monitor.verdict().status == "insufficient"
    monitor.observe(tick_record(data_asof_ms=0, observed_at_ms=1_000))
    assert monitor.verdict().n_cur == 1


def test_decision_rate_counts_the_decided_ticks_in_its_window():
    monitor = build("decision_rate", window=count(5), threshold=at_least(2), min_n=1)
    for status in ("decided", "decided", "skipped:closed", "refused", "decided"):
        monitor.observe(tick_record(status=status))
    verdict = monitor.verdict()
    assert verdict.statistic == 3
    assert verdict.n_cur == 5
    assert verdict.status == "ok"


def test_decision_rate_below_its_floor_breaches():
    monitor = build("decision_rate", window=count(2), threshold=at_least(2), min_n=1)
    feed(monitor, [tick_record(status="skipped:closed"), tick_record(status="decided")])
    assert monitor.verdict().statistic == 1
    assert monitor.verdict().status == "warn"


def test_coverage_reports_the_abstaining_fraction_of_the_legs_in_its_window():
    monitor = build("coverage", window=count(4), threshold=at_least(0.5), min_n=1)
    for final in ("none", "buy", "none", "none"):
        monitor.observe(decision_record([leg(final=final)]))
    verdict = monitor.verdict()
    assert verdict.statistic == pytest.approx(0.75)
    assert verdict.n_cur == 4


def test_coverage_counts_a_decision_with_zero_legs_as_one_abstention():
    monitor = build("coverage", window=count(2), threshold=at_least(0.5), min_n=1)
    monitor.observe(decision_record([]))
    monitor.observe(decision_record([leg(final="buy")]))
    verdict = monitor.verdict()
    assert verdict.statistic == pytest.approx(0.5)
    assert verdict.n_cur == 2


def test_coverage_counts_every_leg_of_a_multi_leg_decision():
    monitor = build("coverage", window=count(4), threshold=at_least(0.5), min_n=1)
    monitor.observe(
        decision_record([leg(final="buy"), leg(final="none"), leg(final="none")])
    )
    assert monitor.verdict().n_cur == 3


def test_latency_reports_the_ninety_fifth_percentile_of_its_window_by_default():
    values = [5] * 50 + [10] * 47 + [1_000] * 3
    monitor = build("latency", window=count(100), threshold=at_most(NEVER), min_n=1)
    feed(monitor, [tick_record(latency_ms=v) for v in values])
    assert monitor.verdict().statistic == 10


def test_latency_reads_a_declared_percentile():
    values = [5] * 50 + [10] * 47 + [1_000] * 3
    monitor = build(
        "latency", window=count(100), threshold=at_most(NEVER), min_n=1, percentile=0.99
    )
    feed(monitor, [tick_record(latency_ms=v) for v in values])
    assert monitor.verdict().statistic == 1_000


def test_latency_sums_the_tick_phase_map_into_one_tick_latency():
    monitor = build("latency", window=count(1), threshold=at_most(NEVER), min_n=1)
    record = tick_record(latency_ms=0)
    record["latency_ms"] = dict.fromkeys(TICK_PHASES, 3)
    monitor.observe(record)
    assert monitor.verdict().statistic == 3 * len(TICK_PHASES)


@pytest.mark.parametrize("bad", (0, 1, 1.5, -0.5, "95"))
def test_latency_refuses_a_percentile_outside_the_open_unit_interval(bad):
    with pytest.raises(ProductionError):
        build("latency", percentile=bad)


def test_refusals_count_the_ticks_that_carried_a_reason():
    monitor = build("refusals", window=count(4), threshold=at_most(1), min_n=1)
    for reason in (None, "stale", None, "guard"):
        monitor.observe(tick_record(refusal_reason=reason))
    verdict = monitor.verdict()
    assert verdict.statistic == 2
    assert verdict.n_cur == 4
    assert verdict.status == "warn"


# ---------------------------------------------------------------------------
# StreamMonitor — PageHinkley and the tracking signal
# ---------------------------------------------------------------------------


def page_hinkley(**overrides):
    """A PH monitor over a sliding window, so the window is full every tick."""
    params = {
        "field": "prediction",
        "delta": 0.05,
        "window": sliding(20),
        "threshold": at_most(0.5),
        "min_n": 20,
        "response": "halt",
    }
    params.update(overrides)
    return MONITOR_KINDS.resolve("page_hinkley")(params, name="drift")


def test_page_hinkley_never_alarms_on_a_level_stream_with_small_noise():
    monitor = page_hinkley()
    statuses = set()
    for i in range(200):
        monitor.observe(prediction_record(0.01 if i % 2 else -0.01))
        statuses.add(monitor.verdict().status)
    assert statuses == {"insufficient", "ok"}
    assert monitor.verdict().statistic == 0.0


def test_page_hinkley_alarms_within_a_few_observations_of_a_level_shift():
    monitor = page_hinkley()
    for i in range(100):
        monitor.observe(prediction_record(0.01 if i % 2 else -0.01))
    assert monitor.verdict().status == "ok"

    seen = 0
    while monitor.verdict().status != "alarm" and seen < 5:
        monitor.observe(prediction_record(1.0))
        seen += 1
    verdict = monitor.verdict()
    assert verdict.status == "alarm"
    assert seen <= 5
    assert verdict.statistic > 0.5
    assert monitor.should_trip() is True


def test_page_hinkley_resets_its_accumulator_after_an_alarm():
    monitor = page_hinkley()
    for i in range(100):
        monitor.observe(prediction_record(0.01 if i % 2 else -0.01))
    while monitor.verdict().status != "alarm":
        monitor.observe(prediction_record(1.0))
    monitor.observe(prediction_record(1.0))
    verdict = monitor.verdict()
    assert verdict.statistic == 0.0
    assert verdict.status == "ok"


def test_page_hinkley_cannot_alarm_before_min_n_observations():
    monitor = page_hinkley()
    for value in (0.0, 0.0, 0.0, 0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 100.0):
        monitor.observe(prediction_record(value))
    assert monitor.verdict().status == "insufficient"
    assert monitor.should_trip() is False


def tracking_signal(**overrides):
    params = {
        "field": "prediction",
        "target_field": "realised",
        "window": count(6),
        "threshold": at_most(4.0),
        "min_n": 6,
        "response": "warn",
    }
    params.update(overrides)
    return MONITOR_KINDS.resolve("tracking_signal")(params, name="bias")


def test_tracking_signal_is_the_cumulative_error_over_the_mean_absolute_one():
    monitor = tracking_signal()
    feed(monitor, [flat_record(prediction=10.0, realised=11.0) for _ in range(6)])
    verdict = monitor.verdict()
    assert verdict.statistic == pytest.approx(6.0)
    assert verdict.status == "warn"


def test_tracking_signal_is_absolute_so_one_bound_catches_both_directions():
    monitor = tracking_signal()
    feed(monitor, [flat_record(prediction=10.0, realised=9.0) for _ in range(6)])
    assert monitor.verdict().statistic == pytest.approx(6.0)
    assert monitor.verdict().status == "warn"


def test_tracking_signal_is_zero_when_the_errors_cancel():
    monitor = tracking_signal()
    feed(
        monitor,
        [
            flat_record(prediction=10.0, realised=11.0 if i % 2 else 9.0)
            for i in range(6)
        ],
    )
    assert monitor.verdict().statistic == pytest.approx(0.0)
    assert monitor.verdict().status == "ok"


def test_tracking_signal_is_finite_when_every_error_is_zero():
    monitor = tracking_signal()
    feed(monitor, [flat_record(prediction=10.0, realised=10.0) for _ in range(6)])
    verdict = monitor.verdict()
    assert verdict.statistic == 0.0
    assert verdict.status == "ok"


def test_a_stream_monitor_skips_a_record_missing_either_of_its_fields():
    monitor = tracking_signal()
    feed(monitor, [flat_record(prediction=10.0), flat_record(realised=11.0)])
    assert monitor.verdict().n_cur == 0
    assert monitor.verdict().status == "insufficient"


# ---------------------------------------------------------------------------
# DistributionMonitor — PSI and KS
# ---------------------------------------------------------------------------


def psi_monitor(**overrides):
    """PSI over two bins, so the reference quantile is a plain median."""
    params = {
        "field": "prediction",
        "bins": 2,
        "reference": leading(10),
        "window": count(10),
        "threshold": at_most(0.1),
        "min_n": 10,
        "response": "warn",
    }
    params.update(overrides)
    return MONITOR_KINDS.resolve("psi")(params, name="pred_shift")


ANCHOR = [prediction_record(float(v)) for v in range(1, 11)]  # 1..10, median 5.5


def test_psi_is_exactly_zero_on_identical_samples():
    monitor = psi_monitor()
    monitor.fit(ANCHOR)
    feed(monitor, ANCHOR)
    verdict = monitor.verdict()
    assert verdict.statistic == 0.0
    assert verdict.status == "ok"
    assert verdict.n_ref == 10
    assert verdict.n_cur == 10
    assert verdict.threshold == 0.1
    assert verdict.window == "count:10"
    assert verdict.slice == "all"
    assert verdict.provisional is False


def test_psi_over_shifted_bin_proportions_is_the_hand_computed_sum():
    monitor = psi_monitor()
    monitor.fit(ANCHOR)
    # six of ten below the reference median of 5.5, four above.
    feed(monitor, [prediction_record(float(v)) for v in (1, 2, 3, 4, 5, 5, 6, 7, 8, 9)])
    expected = psi_of((5, 5), (6, 4))
    assert expected == pytest.approx(
        0.1 * math.log(0.6 / 0.5) - 0.1 * math.log(0.4 / 0.5)
    )
    assert monitor.verdict().statistic == pytest.approx(expected)
    assert monitor.verdict().status == "ok"


def test_psi_is_scale_free_while_its_alpha_benchmark_scales_like_chi_squared():
    """Doubling both samples leaves the statistic; the benchmark halves."""
    small = psi_monitor(threshold={"kind": "alpha", "alpha": 0.05})
    small.fit(ANCHOR)
    feed(small, [prediction_record(float(v)) for v in (1, 2, 3, 4, 5, 5, 6, 7, 8, 9)])

    doubled = psi_monitor(
        reference=leading(20),
        window=count(20),
        min_n=20,
        threshold={"kind": "alpha", "alpha": 0.05},
    )
    doubled.fit([prediction_record(float(v)) for v in range(1, 11) for _ in (0, 1)])
    feed(
        doubled,
        [
            prediction_record(float(v))
            for v in (1, 2, 3, 4, 5, 5, 6, 7, 8, 9)
            for _ in (0, 1)
        ],
    )

    assert doubled.verdict().statistic == pytest.approx(small.verdict().statistic)
    assert small.verdict().threshold == pytest.approx(psi_benchmark(0.05, 2, 10, 10))
    assert doubled.verdict().threshold == pytest.approx(psi_benchmark(0.05, 2, 20, 20))
    assert doubled.verdict().threshold == pytest.approx(small.verdict().threshold / 2)


def test_psi_publishes_the_chi_squared_benchmark_as_its_critical_value():
    monitor = psi_monitor(bins=10)
    assert monitor.critical_value(0.01, 500, 300) == pytest.approx(
        psi_benchmark(0.01, 10, 500, 300)
    )


def test_psi_stays_finite_when_the_window_misses_a_reference_bin():
    monitor = psi_monitor(threshold=at_most(0.02))
    monitor.fit(ANCHOR)
    feed(monitor, [prediction_record(float(v)) for v in range(11, 21)])
    verdict = monitor.verdict()
    assert math.isfinite(verdict.statistic)
    assert verdict.statistic > 0.1
    assert verdict.status == "warn"


def test_ks_is_the_hand_computed_ecdf_distance():
    monitor = MONITOR_KINDS.resolve("ks")(
        {
            "field": "prediction",
            "reference": leading(4),
            "window": count(4),
            "threshold": {"kind": "alpha", "alpha": 0.05},
            "min_n": 4,
            "response": "warn",
        },
        name="ks",
    )
    monitor.fit([prediction_record(float(v)) for v in (1, 2, 3, 4)])
    feed(monitor, [prediction_record(float(v)) for v in (3, 4, 5, 6)])
    verdict = monitor.verdict()
    assert verdict.statistic == pytest.approx(0.5)
    assert verdict.threshold == pytest.approx(ks_benchmark(0.05, 4, 4))
    assert verdict.status == "ok"


def test_ks_publishes_the_kolmogorov_value_as_its_critical_value():
    monitor = MONITOR_KINDS.resolve("ks")(
        {
            "field": "prediction",
            "reference": leading(4),
            "window": count(4),
            "threshold": at_most(0.9),
        }
    )
    assert monitor.critical_value(0.01, 500, 300) == pytest.approx(
        ks_benchmark(0.01, 500, 300)
    )


def test_a_distribution_monitor_is_insufficient_until_its_reference_fills():
    monitor = psi_monitor()
    feed(monitor, ANCHOR)
    assert monitor.verdict().status == "insufficient"
    assert monitor.verdict().n_ref == 0
    # the eleventh observation closes chunk one, which populates the anchor.
    feed(monitor, [prediction_record(float(v)) for v in range(1, 11)])
    verdict = monitor.verdict()
    assert verdict.n_ref == 10
    assert verdict.statistic == 0.0
    assert verdict.status == "ok"


# ---------------------------------------------------------------------------
# Both references at once (D16: a fixed anchor AND a rolling reference)
# ---------------------------------------------------------------------------

DRIFTED = [prediction_record(float(v)) for v in range(4, 14)]  # 4..13, median 8.5


def test_the_fixed_anchor_still_reports_drift_the_rolling_reference_adapted_to():
    both = psi_monitor(reference=[leading(10), rolling(10)], threshold=at_most(0.02))
    both.fit(ANCHOR)
    feed(both, DRIFTED)  # chunk one: drifted, closes on the next observation
    feed(both, DRIFTED)  # chunk two: the same drifted level

    # two of ten below the anchor's median of 5.5, eight above.
    expected = psi_of((5, 5), (2, 8))
    verdict = both.verdict()
    assert verdict.statistic == pytest.approx(expected)
    assert verdict.status == "warn"
    assert verdict.n_ref == 10

    only_rolling = psi_monitor(reference=rolling(10), threshold=at_most(0.02))
    only_rolling.fit(ANCHOR)
    feed(only_rolling, DRIFTED)
    feed(only_rolling, DRIFTED)
    assert only_rolling.verdict().statistic == pytest.approx(0.0)
    assert only_rolling.verdict().status == "ok"


def test_the_rolling_reference_reports_a_shift_away_from_the_recent_past():
    recent = [prediction_record(v) for v in (1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.2)]
    both = psi_monitor(reference=[leading(10), rolling(10)], threshold=at_most(0.02))
    both.fit(ANCHOR)
    feed(both, recent)  # chunk one, all below the anchor median
    feed(both, ANCHOR)  # chunk two, back to the anchor's own distribution

    # against the rolling reference's own median of 3.25: three of ten below.
    expected = psi_of((5, 5), (3, 7))
    verdict = both.verdict()
    assert verdict.statistic == pytest.approx(expected)
    assert verdict.status == "warn"

    only_anchor = psi_monitor(reference=leading(10), threshold=at_most(0.02))
    only_anchor.fit(ANCHOR)
    feed(only_anchor, recent)
    feed(only_anchor, ANCHOR)
    assert only_anchor.verdict().statistic == pytest.approx(0.0)
    assert only_anchor.verdict().status == "ok"


def test_fit_primes_the_references_and_never_the_current_window():
    monitor = psi_monitor()
    monitor.fit(ANCHOR)
    verdict = monitor.verdict()
    assert verdict.n_cur == 0
    assert verdict.status == "insufficient"


def test_fit_on_a_monitor_with_no_reference_is_harmless():
    monitor = build("coverage", window=count(2), threshold=at_least(0.0), min_n=1)
    monitor.fit([decision_record([leg(final="none")])])
    assert monitor.verdict().n_cur == 0


# ---------------------------------------------------------------------------
# The rules §5.10 pins by test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_every_phase_one_kind_answers_ok_and_is_never_provisional(kind):
    monitor = feed(build(kind), [kind_record(kind, i) for i in range(4)])
    verdict = monitor.verdict()
    assert verdict.status in MONITOR_STATUSES
    assert verdict.status == "ok"
    assert verdict.provisional is False
    assert verdict.slice == "all"
    assert verdict.window == "count:2"
    assert verdict.n_cur == 2


@pytest.mark.parametrize("kind", KINDS)
def test_below_min_n_the_verdict_is_insufficient_and_never_ok(kind):
    monitor = feed(build(kind, min_n=5), [kind_record(kind, i) for i in range(4)])
    verdict = monitor.verdict()
    assert verdict.status == "insufficient"
    assert monitor.should_trip() is False


@pytest.mark.parametrize("kind", KINDS)
def test_a_monitor_that_has_seen_nothing_is_insufficient(kind):
    verdict = build(kind).verdict()
    assert verdict.status == "insufficient"
    assert verdict.n_cur == 0


def test_the_trailing_partial_chunk_is_never_ok_until_it_fills():
    monitor = build("coverage", window=count(4), threshold=at_least(0.0), min_n=1)
    for _ in range(4):
        monitor.observe(decision_record([leg(final="none")]))
    assert monitor.verdict().status == "ok"

    monitor.observe(decision_record([leg(final="none")]))
    verdict = monitor.verdict()
    assert verdict.status == "insufficient"
    assert verdict.n_cur == 1

    for _ in range(3):
        monitor.observe(decision_record([leg(final="none")]))
    assert monitor.verdict().status == "ok"


def test_the_default_min_n_is_a_named_constant_the_monitor_reads():
    assert isinstance(DEFAULT_MIN_N, int)
    assert 1 <= DEFAULT_MIN_N <= 10_000
    params = kind_params("coverage", window=sliding(DEFAULT_MIN_N))
    del params["min_n"]
    monitor = MONITOR_KINDS.resolve("coverage")(params)
    feed(
        monitor,
        [decision_record([leg(final="none")]) for _ in range(DEFAULT_MIN_N - 1)],
    )
    assert monitor.verdict().status == "insufficient"
    monitor.observe(decision_record([leg(final="none")]))
    assert monitor.verdict().status != "insufficient"


def test_an_alarm_under_a_halt_response_is_what_trips_the_breaker():
    halting = build("coverage", window=count(2), threshold=at_least(0.5), response="halt", min_n=1)
    feed(halting, [decision_record([leg(final="buy")]) for _ in range(2)])
    assert halting.verdict().statistic == pytest.approx(0.0)
    assert halting.verdict().status == "alarm"
    assert halting.should_trip() is True


def test_the_same_breach_under_a_warn_response_never_trips():
    warning = build("coverage", window=count(2), threshold=at_least(0.5), response="warn", min_n=1)
    feed(warning, [decision_record([leg(final="buy")]) for _ in range(2)])
    assert warning.verdict().status == "warn"
    assert warning.should_trip() is False


def test_a_monitor_inside_its_bound_never_trips():
    quiet = build("coverage", window=count(2), threshold=at_least(0.5), response="halt", min_n=1)
    feed(quiet, [decision_record([leg(final="none")]) for _ in range(2)])
    assert quiet.verdict().status == "ok"
    assert quiet.should_trip() is False


def test_the_window_label_names_the_chunker_that_cut_it():
    monitor = build("coverage", window=count(2), min_n=1)
    feed(monitor, [decision_record([leg(final="none")]) for _ in range(2)])
    assert monitor.verdict().window == "count:2"

    monitor = build("coverage", window=sliding(2, 1), min_n=1)
    feed(monitor, [decision_record([leg(final="none")]) for _ in range(2)])
    assert monitor.verdict().window.startswith("sliding:")


def test_a_verdict_supplies_every_field_of_the_ledger_monitor_body():
    monitor = MONITOR_KINDS.resolve("psi")(kind_params("psi"), name="pred_shift")
    feed(monitor, [kind_record("psi", i) for i in range(4)])
    verdict = monitor.verdict()
    fields = {f.name for f in dataclasses.fields(verdict)}
    assert set(MONITOR_BODY) - {"monitor"} <= fields
    assert monitor.name == "pred_shift"


# ---------------------------------------------------------------------------
# State — folded into the §6 snapshot, so a restart keeps the window
# ---------------------------------------------------------------------------


def test_monitor_state_is_json_able_with_no_non_finite_number():
    monitor = feed(psi_monitor(), ANCHOR)
    json.dumps(monitor.state(), allow_nan=False)


def test_state_round_trips_through_json_and_reproduces_the_next_verdict():
    live = psi_monitor()
    live.fit(ANCHOR)
    feed(live, [prediction_record(float(v)) for v in range(1, 16)])

    restored = psi_monitor()
    restored.restore(json.loads(json.dumps(live.state())))

    later = [prediction_record(float(v)) for v in (6, 7, 8, 9, 10)]
    feed(live, later)
    feed(restored, later)
    assert live.verdict() == restored.verdict()
    assert live.verdict().status != "insufficient"
    assert json.loads(json.dumps(live.state())) == json.loads(
        json.dumps(restored.state())
    )


def test_a_restored_stream_monitor_keeps_the_accumulator_that_detects_drift():
    live = page_hinkley()
    for i in range(100):
        live.observe(prediction_record(0.01 if i % 2 else -0.01))

    restored = page_hinkley()
    restored.restore(json.loads(json.dumps(live.state())))

    live.observe(prediction_record(1.0))
    restored.observe(prediction_record(1.0))
    assert live.verdict() == restored.verdict()
    assert live.verdict().status == "alarm"


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_round_trips_its_state(kind):
    live = feed(build(kind), [kind_record(kind, i) for i in range(4)])
    restored = build(kind)
    restored.restore(json.loads(json.dumps(live.state())))
    assert live.verdict() == restored.verdict()


# ---------------------------------------------------------------------------
# Determinism — the same tape gives the same verdict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_the_same_record_sequence_produces_identical_verdicts(kind):
    records = [kind_record(kind, i) for i in range(6)]
    first = feed(build(kind), records)
    second = feed(build(kind), list(records))
    assert first.verdict() == second.verdict()


def test_reading_a_verdict_twice_never_changes_it():
    monitor = feed(psi_monitor(), ANCHOR + ANCHOR)
    assert monitor.verdict() == monitor.verdict()
    assert monitor.state() == monitor.state()


# ---------------------------------------------------------------------------
# Profile — the saved reference population
# ---------------------------------------------------------------------------


def rows(values, field="prediction"):
    """Records for `Profile.from_records`, one field each."""
    return [{field: v} for v in values]


def test_a_profile_summarises_each_field_it_was_asked_for():
    profile = Profile.from_records(
        rows([1.0, 2.0, 3.0, 4.0]) + [{"other": 9}], ("prediction",), 2, 2
    )
    field = profile.to_obj()["fields"]["prediction"]
    assert field["count"] == 4
    assert field["missing"] == 1
    assert field["min"] == 1.0
    assert field["max"] == 4.0
    assert field["sum"] == 10.0
    assert field["sumsq"] == 30.0


def test_profile_bins_are_the_quantile_edges_of_the_values_it_saw():
    profile = Profile.from_records(rows([1.0, 2.0, 3.0, 4.0]), ("prediction",), 2, 2)
    field = profile.to_obj()["fields"]["prediction"]
    assert list(field["bins"]["edges"]) == [1.0, 2.5, 4.0]
    assert list(field["bins"]["counts"]) == [2, 2]


def test_a_profile_keeps_the_k_most_frequent_values():
    profile = Profile.from_records(
        rows(["a", "a", "a", "b", "b", "c"], field="side"), ("side",), 2, 2
    )
    field = profile.to_obj()["fields"]["side"]
    assert [list(pair) for pair in field["top_k"]] == [["a", 3], ["b", 2]]
    assert field["sum"] is None


def test_merging_profiles_sums_the_counts_exactly():
    left = Profile.from_records(rows([1.0, 2.0]), ("prediction",), 2, 2)
    right = Profile.from_records(rows([3.0, 4.0, 5.0]), ("prediction",), 2, 2)
    merged = left.merge(right).to_obj()["fields"]["prediction"]
    assert merged["count"] == 5
    assert merged["sum"] == 15.0
    assert merged["sumsq"] == 55.0
    assert merged["min"] == 1.0
    assert merged["max"] == 5.0


def test_merging_profiles_is_associative():
    a = Profile.from_records(rows([1.0, 2.0]), ("prediction",), 2, 2)
    b = Profile.from_records(rows([3.0, 4.0]), ("prediction",), 2, 2)
    c = Profile.from_records(rows([5.0, 6.0, 7.0]), ("prediction",), 2, 2)
    assert a.merge(b).merge(c).to_obj() == a.merge(b.merge(c)).to_obj()


def test_a_profile_round_trips_through_its_object_form():
    profile = Profile.from_records(
        rows([1.0, 2.0, 3.0, 4.0]) + rows(["x"], field="side"), ("prediction", "side"), 2, 2
    )
    obj = profile.to_obj()
    assert json.loads(json.dumps(obj)) == obj
    assert Profile.from_obj(obj).to_obj() == obj


def test_a_profile_refuses_an_unknown_key_on_the_way_back_in():
    obj = Profile.from_records(rows([1.0, 2.0]), ("prediction",), 2, 2).to_obj()
    obj["surprise"] = 1
    with pytest.raises(ProductionError):
        Profile.from_obj(obj)
