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

Readings taken where §5.10.1 is silent about the phase-2 stream detectors
(each reported to the orchestrator as a plan gap):

* DDM's `s_min` can be ZERO — a stream with no errors has `p = s = 0` — and
  §5.10.1's `(p + s − p_min − s_min)/s_min` is then undefined. The pair
  minimising `p + s` is therefore only REMEMBERED when its sigma is real
  (and once the stream is `min_n` long, which is what §5.10.1 means by
  "`min_n` gates the early stream where `s_t` is meaningless"); until such a
  pair exists the statistic is `None` and the verdict is `insufficient`,
  which is D16's answer for a question that cannot yet be asked. It also
  makes the statistic finite always, so no alarm can rest on a division by
  a sigma of zero.
* ADWIN's `eps_cut` is named but not spelled out; it is the classic
  Bifet-Gavaldà bound `sqrt(ln(4n/δ) / 2m)` over the harmonic mean `m` of
  the two sides, restated in `hoeffding_cut` below.
* ADWIN shrinks INSIDE the fold, not on the observation after an alarm as
  `PageHinkley` does, and it reports the statistic it measured BEFORE the
  drop. The adaptive window is the statistic's own (§5.10.1), so it must not
  depend on the document's `response` — a `log` monitor adapts exactly as a
  `halt` one does — and a detector that shrank before reporting could never
  show the breach that made it shrink.
* `min_sub` has no default in §5.10.1 the way `delta` does; `DEFAULT_MIN_SUB`
  is one here, and this file asserts a monitor that omits both reads them.
"""

import ast
import dataclasses
import inspect
import json
import math
import statistics

import pytest

import dskit.production.monitors as monitors
from dskit.production.base import ProductionError
from dskit.production.monitors import (
    ADWIN,
    CHUNKER_KINDS,
    DDM,
    DEFAULT_ADWIN_DELTA,
    DEFAULT_MIN_N,
    DEFAULT_MIN_SUB,
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
    JensenShannon,
    LInf,
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

#: §5.10.1's four additions to the two phase-1 families — two change
#: detectors on the stream family, two distances on the distribution one.
#: The outcome and parity families are the rest of phase 2 and are NOT here.
PHASE_TWO_KINDS = (
    "adwin",
    "ddm",
    "jensen_shannon",
    "linf",
)

#: Every registered monitor after §5.10.1. The generic rules below are
#: parametrised over ALL of them: a phase-2 member that broke `min_n`, the
#: partial-chunk rule or the state round-trip would be a monitor the loop
#: cannot trust, exactly like a phase-1 one.
ALL_KINDS = tuple(sorted(KINDS + PHASE_TWO_KINDS))

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
        "jensen_shannon": {"field": "prediction", "bins": 2, "reference": leading(2)},
        "linf": {"field": "prediction", "bins": 2, "reference": leading(2)},
        "ks": {"field": "prediction", "reference": leading(2)},
        "page_hinkley": {"field": "prediction"},
        "ddm": {"field": "error"},
        "adwin": {"field": "prediction", "min_sub": 1},
        "tracking_signal": {"field": "prediction", "target_field": "realised"},
    }
    params.update(extra.get(kind, {}))
    params.update(overrides)
    return params


def kind_record(kind, i):
    """One record the given kind counts as an observation, varying with `i`."""
    if kind in ("psi", "ks", "page_hinkley", "adwin", "jensen_shannon", "linf"):
        return prediction_record(float(i))
    if kind == "ddm":
        return flat_record(error=float(i % 2))
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


def test_the_registry_lists_the_nine_phase_one_kinds_and_no_more():
    """§5.10's nine members are all still there and still resolve to their own
    classes; §5.10.1's four are pinned separately, so a phase-2 member that
    displaced a phase-1 one fails here rather than in a serve document."""
    assert set(KINDS) <= set(MONITOR_KINDS.kinds())
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


def test_the_registry_lists_exactly_the_thirteen_kinds_of_the_two_families():
    """§5.10.1 adds four members to the two existing families and nothing
    else: the registry is EXACTLY the nine plus the four until the outcome
    and parity families land."""
    assert MONITOR_KINDS.kinds() == ALL_KINDS
    for name, cls in (
        ("ddm", DDM),
        ("adwin", ADWIN),
        ("jensen_shannon", JensenShannon),
        ("linf", LInf),
    ):
        assert MONITOR_KINDS.resolve(name) is cls


def test_the_outcome_and_parity_families_are_not_registered_yet():
    """§5.10.1's other two families need a label and a replay tape; neither
    has a producer until `outcomes` and `report` land."""
    for name in ("calibration", "brier", "skill", "prediction_bias", "parity"):
        assert name not in MONITOR_KINDS
        with pytest.raises(ProductionError):
            MONITOR_KINDS.resolve(name)


def test_the_three_strategy_registries_name_their_kinds():
    # `REFERENCE_KINDS` is the one family a tier-2 pack registers into
    # (§4.3, §5.10.2: `libs/parquet.py` adds `run` when it is imported), and
    # a registry is process-global — so what is pinned here is what
    # `monitors.py` ITSELF contributes: the three core kinds, and nothing
    # else of this module's making. An extra kind may only come from a pack.
    assert set(("leading", "rolling", "snapshot")) <= set(REFERENCE_KINDS.kinds())
    from_this_module = {
        name
        for name in REFERENCE_KINDS.kinds()
        if REFERENCE_KINDS.resolve(name).__module__ == monitors.__name__
    }
    assert from_this_module == {"leading", "rolling", "snapshot"}
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
        (REFERENCE_KINDS, "warehouse"),
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
    for cls in (PageHinkley, TrackingSignal, DDM, ADWIN):
        assert issubclass(cls, StreamMonitor)
    for cls in (PSI, KS, JensenShannon, LInf):
        assert issubclass(cls, DistributionMonitor)
    assert not issubclass(StreamMonitor, OperationalMonitor)
    assert not issubclass(DistributionMonitor, StreamMonitor)


# ---------------------------------------------------------------------------
# Construction — default-deny at every level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_every_monitor_accepts_the_four_common_knobs(kind):
    monitor = build(kind, window=count(3), threshold=at_most(5.0), response="halt", min_n=3)
    assert isinstance(monitor, Monitor)


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_an_unknown_top_level_param_refuses_naming_it(kind):
    with pytest.raises(ProductionError) as exc:
        build(kind, treshold=at_most(1.0))
    assert "treshold" in str(exc.value)


@pytest.mark.parametrize("kind", ALL_KINDS)
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
# StreamMonitor phase 2 — DDM and ADWIN (§5.10.1)
# ---------------------------------------------------------------------------
#
# Both are reduced to a DIMENSIONLESS statistic so the document's ordinary
# `constant` threshold carries the literature's rule and the code carries no
# constant of its own: DDM reports SIGMAS above the best the stream has been
# (so 2σ warning / 3σ drift are `{"max": 2}` and `{"max": 3}`), ADWIN reports
# a mean gap in units of ITS OWN Hoeffding bound (so the classic test is
# `{"max": 1}`). Every expected number below is walked here from §5.10.1's
# formulas, never read back out of the monitor.


def ddm(**overrides):
    """A DDM over an error field, with a sliding window so it is always full."""
    params = {
        "field": "error",
        "window": sliding(30),
        "threshold": at_most(3.0),
        "min_n": 30,
        "response": "halt",
    }
    params.update(overrides)
    return MONITOR_KINDS.resolve("ddm")(params, name="err")


def sigma_of(rate, count):
    """§5.10.1's `s_t = sqrt(p_t(1 − p_t)/t)`."""
    return math.sqrt(rate * (1.0 - rate) / count)


def ddm_sigmas(values, min_n):
    """§5.10.1's DDM statistic per observation, walked independently here.

    `(p_t + s_t − p_min − s_min) / s_min`, where the remembered pair is the
    one minimising `p + s` — considered only once the stream has `min_n`
    observations and its sigma is real, since a sigma of zero measures
    nothing. `None` while no such pair exists.
    """
    count, rate, best = 0, 0.0, None
    out = []
    for value in values:
        count += 1
        rate += (value - rate) / count
        sigma = sigma_of(rate, count)
        if count >= min_n and sigma > 0.0 and (best is None or rate + sigma <= sum(best)):
            best = (rate, sigma)
        out.append(None if best is None else ((rate + sigma) - sum(best)) / best[1])
    return out


#: Thirty clean observations, then four errors — a stream whose sigma is
#: meaningless while it is clean and which then crosses two and three sigmas
#: one observation apart.
DDM_STREAM = [0.0] * 30 + [1.0] * 4


def errors(monitor, values):
    """Observe an error stream."""
    for value in values:
        monitor.observe(flat_record(error=value))
    return monitor


def test_ddm_reports_the_sigmas_above_the_best_the_stream_has_been():
    monitor = errors(ddm(threshold=at_most(NEVER)), DDM_STREAM)
    best_rate = 1 / 31
    best_sigma = sigma_of(best_rate, 31)
    rate = 4 / 34
    expected = ((rate + sigma_of(rate, 34)) - (best_rate + best_sigma)) / best_sigma
    assert monitor.verdict().statistic == pytest.approx(expected)
    assert expected == pytest.approx(ddm_sigmas(DDM_STREAM, 30)[-1])


def test_ddm_walks_the_whole_stream_exactly_as_the_recursion_does():
    monitor = ddm(threshold=at_most(NEVER))
    for index, (value, want) in enumerate(zip(DDM_STREAM, ddm_sigmas(DDM_STREAM, 30))):
        monitor.observe(flat_record(error=value))
        if index < 29:  # the sliding window is not full yet
            continue
        got = monitor.verdict().statistic
        assert got is None if want is None else got == pytest.approx(want)


def test_ddm_holds_no_constant_of_its_own_so_the_document_carries_the_rule():
    """§5.10.1: "The literature's warning at 2σ and drift at 3σ are then two
    ordinary `constant` thresholds in the document, not two numbers in the
    code." A 2 or a 3 in DDM's body would be that rule leaking back in."""
    tree = ast.parse(inspect.getsource(monitors.DDM))
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    }
    assert literals <= {0, 1, 0.0, 1.0}, literals
    assert set(DDM._PARAMS) == set(StreamMonitor._PARAMS)


def test_ddm_expresses_the_two_and_three_sigma_rules_as_ordinary_thresholds():
    warning = errors(ddm(threshold=at_most(2.0), response="warn"), DDM_STREAM[:33])
    drift = errors(ddm(threshold=at_most(3.0), response="halt"), DDM_STREAM[:33])
    assert 2.0 < warning.verdict().statistic <= 3.0
    assert warning.verdict().status == "warn"
    assert drift.verdict().status == "ok"
    assert drift.should_trip() is False

    errors(drift, DDM_STREAM[33:])
    assert drift.verdict().statistic > 3.0
    assert drift.verdict().status == "alarm"
    assert drift.should_trip() is True


def test_ddm_resets_its_whole_recursion_on_the_observation_after_an_alarm():
    monitor = errors(ddm(threshold=at_most(3.0)), DDM_STREAM)
    assert monitor.verdict().status == "alarm"

    monitor.observe(flat_record(error=1.0))
    stream = monitor.state()["stream"]
    assert stream["count"] == 1
    assert stream["error_rate"] == 1.0
    assert stream["best_rate"] is None
    assert stream["best_sigma"] is None
    assert monitor.verdict().statistic is None
    assert monitor.verdict().status == "insufficient"


def test_min_n_gates_the_early_stream_where_ddms_sigma_is_meaningless():
    """The pair minimising `p + s` is only remembered once the stream is
    `min_n` long: a sigma from three observations is noise, and anchoring the
    detector to it would make every later observation look like drift."""
    monitor = ddm(window=sliding(10), min_n=10, threshold=at_most(NEVER))
    errors(monitor, [float(i % 2) for i in range(9)])
    assert monitor.state()["stream"]["best_rate"] is None
    assert monitor.verdict().status == "insufficient"

    monitor.observe(flat_record(error=1.0))
    assert monitor.state()["stream"]["best_rate"] == pytest.approx(0.5)
    assert monitor.verdict().statistic == 0.0
    assert monitor.verdict().status == "ok"


def test_a_clean_stream_never_anchors_ddm_because_a_zero_sigma_measures_nothing():
    monitor = errors(ddm(threshold=at_most(NEVER)), [0.0] * 40)
    assert monitor.state()["stream"]["best_sigma"] is None
    assert monitor.verdict().statistic is None
    assert monitor.verdict().status == "insufficient"


def test_ddm_refuses_an_error_value_outside_the_unit_interval():
    monitor = ddm()
    with pytest.raises(ProductionError) as exc:
        monitor.observe(flat_record(error=1.5))
    assert "error" in str(exc.value)


def test_ddm_state_round_trips_through_json():
    live = errors(ddm(threshold=at_most(NEVER)), DDM_STREAM)
    restored = ddm(threshold=at_most(NEVER))
    restored.restore(json.loads(json.dumps(live.state())))
    assert json.loads(json.dumps(live.state())) == json.loads(
        json.dumps(restored.state())
    )
    live.observe(flat_record(error=1.0))
    restored.observe(flat_record(error=1.0))
    assert live.verdict() == restored.verdict()
    assert live.verdict().statistic is not None


def adwin(**overrides):
    """An ADWIN whose declared window is one observation, so the verdict is
    always answerable and the ADAPTIVE window is the only thing moving."""
    params = {
        "field": "prediction",
        "window": sliding(1),
        "threshold": at_most(1.0),
        "min_n": 1,
        "response": "halt",
    }
    params.update(overrides)
    return MONITOR_KINDS.resolve("adwin")(params, name="shift")


def hoeffding_cut(n_left, n_right, n, delta):
    """§5.10.1's `eps_cut`: the Hoeffding bound at `delta` over one cut,
    `sqrt(ln(4n/delta) / 2m)` with `m` the harmonic mean of the two sides."""
    m = 1.0 / (1.0 / n_left + 1.0 / n_right)
    return math.sqrt(math.log(4.0 * n / delta) / (2.0 * m))


def adwin_cuts(window, delta, min_sub):
    """Every legal cut's `|mean_left − mean_right| / eps_cut`, with its index."""
    n = len(window)
    out = []
    for cut in range(min_sub, n - min_sub + 1):
        left, right = window[:cut], window[cut:]
        gap = abs(statistics.fmean(left) - statistics.fmean(right))
        out.append((gap / hoeffding_cut(len(left), len(right), n, delta), cut))
    return out


def adwin_walk(values, delta, min_sub):
    """The statistic per observation and the surviving adaptive window."""
    window, seen = [], []
    for value in values:
        window.append(value)
        cuts = adwin_cuts(window, delta, min_sub)
        seen.append(max(cuts, key=lambda pair: pair[0])[0] if cuts else None)
        while cuts:
            worst, at = max(cuts, key=lambda pair: pair[0])
            if worst <= 1.0:
                break
            window = window[at:]
            cuts = adwin_cuts(window, delta, min_sub)
    return seen, window


#: Twenty observations at one level, then twenty at another.
ADWIN_STREAM = [0.0] * 20 + [1.0] * 20


def test_adwin_reports_the_largest_mean_gap_in_units_of_its_own_bound():
    expected, survivors = adwin_walk(ADWIN_STREAM, DEFAULT_ADWIN_DELTA, DEFAULT_MIN_SUB)
    monitor = adwin(threshold=at_most(NEVER))
    for value, want in zip(ADWIN_STREAM, expected):
        monitor.observe(prediction_record(value))
        got = monitor.verdict().statistic
        assert got is None if want is None else got == pytest.approx(want)
    assert monitor.state()["stream"]["window"] == pytest.approx(survivors)


def test_a_constant_threshold_at_one_is_the_classic_adwin_test():
    expected, _ = adwin_walk(ADWIN_STREAM, DEFAULT_ADWIN_DELTA, DEFAULT_MIN_SUB)
    monitor = adwin(threshold=at_most(1.0))
    seen = []
    for value in ADWIN_STREAM:
        monitor.observe(prediction_record(value))
        seen.append(monitor.verdict().status == "alarm")
    assert seen == [want is not None and want > 1.0 for want in expected]
    assert any(seen)


def test_adwin_drops_the_older_sub_window_on_a_breach_so_it_shrinks_itself():
    monitor = adwin(threshold=at_most(NEVER))
    lengths = []
    for value in ADWIN_STREAM:
        monitor.observe(prediction_record(value))
        lengths.append(len(monitor.state()["stream"]["window"]))
    assert any(after < before for before, after in zip(lengths, lengths[1:]))
    # what survives is the NEWER sub-window: the level the stream moved TO.
    assert set(monitor.state()["stream"]["window"]) == {1.0}
    assert len(monitor.state()["stream"]["window"]) < len(ADWIN_STREAM)


def test_the_declared_window_labels_the_verdict_while_the_adaptive_one_is_the_statistics():
    """§5.10.1: "the adaptive window is the statistic's, the declared one is
    the verdict's". Two different lengths, and only one of them reaches the
    §6 monitor record."""
    monitor = adwin(window=count(10), min_n=10, threshold=at_most(NEVER))
    for value in ADWIN_STREAM[:20]:
        monitor.observe(prediction_record(value))
    verdict = monitor.verdict()
    assert verdict.window == "count:10"
    assert verdict.n_cur == 10
    assert len(monitor.state()["stream"]["window"]) == 20


def test_the_declared_window_gates_min_n_even_when_the_statistic_is_ready():
    monitor = adwin(window=count(20), min_n=20, threshold=at_most(NEVER))
    for value in ADWIN_STREAM[:15]:
        monitor.observe(prediction_record(value))
    assert monitor.state()["stream"]["statistic"] is not None
    assert monitor.verdict().status == "insufficient"
    assert monitor.verdict().n_cur == 15


def test_adwin_takes_delta_and_min_sub_and_nothing_else():
    assert set(ADWIN._PARAMS) == set(StreamMonitor._PARAMS) | {"delta", "min_sub"}
    with pytest.raises(ProductionError) as exc:
        adwin(lam=1.0)
    assert "lam" in str(exc.value)


def test_the_adwin_defaults_are_named_constants_the_monitor_reads():
    assert 0.0 < DEFAULT_ADWIN_DELTA < 1.0
    assert isinstance(DEFAULT_MIN_SUB, int) and DEFAULT_MIN_SUB >= 1
    bare = adwin(threshold=at_most(NEVER))
    spelled = adwin(threshold=at_most(NEVER), delta=DEFAULT_ADWIN_DELTA, min_sub=DEFAULT_MIN_SUB)
    for value in ADWIN_STREAM:
        bare.observe(prediction_record(value))
        spelled.observe(prediction_record(value))
    assert bare.verdict() == spelled.verdict()


def test_adwin_refuses_a_delta_outside_the_open_unit_interval_and_a_zero_sub_window():
    for overrides in ({"delta": 0.0}, {"delta": 1.0}, {"min_sub": 0}):
        with pytest.raises(ProductionError):
            adwin(**overrides)


def test_a_wider_min_sub_leaves_a_short_window_with_no_cut_to_take():
    monitor = adwin(min_sub=5, threshold=at_most(NEVER))
    for value in ADWIN_STREAM[:9]:
        monitor.observe(prediction_record(value))
    assert monitor.state()["stream"]["statistic"] is None
    assert monitor.verdict().status == "insufficient"


def test_adwin_state_round_trips_through_json():
    live = adwin(threshold=at_most(NEVER))
    for value in ADWIN_STREAM:
        live.observe(prediction_record(value))
    restored = adwin(threshold=at_most(NEVER))
    restored.restore(json.loads(json.dumps(live.state())))
    assert json.loads(json.dumps(live.state())) == json.loads(
        json.dumps(restored.state())
    )
    live.observe(prediction_record(0.0))
    restored.observe(prediction_record(0.0))
    assert live.verdict() == restored.verdict()
    assert live.state()["stream"]["window"] == restored.state()["stream"]["window"]


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
# DistributionMonitor phase 2 — Jensen-Shannon and L-infinity (§5.10.1)
# ---------------------------------------------------------------------------
#
# Both distances are taken over the REFERENCE's own quantile bins, so all
# three binned monitors cut the same way and none re-derives the binning.
# Both statistics are proportions, so they are bounded in [0, 1] and a
# document can reason about them without knowing the sample size.


def entropy(distribution):
    """Base-2 Shannon entropy, with the `0 log 0 = 0` convention."""
    return -sum(x * math.log2(x) for x in distribution if x > 0)


def js_of(ref_counts, cur_counts):
    """Base-2 Jensen-Shannon divergence `H(M) − (H(P) + H(Q))/2`."""
    n_ref, n_cur = sum(ref_counts), sum(cur_counts)
    p = [count / n_ref for count in ref_counts]
    q = [count / n_cur for count in cur_counts]
    mid = [(a + b) / 2.0 for a, b in zip(p, q)]
    return entropy(mid) - (entropy(p) + entropy(q)) / 2.0


def linf_of(ref_counts, cur_counts):
    """The largest absolute bin-proportion gap."""
    n_ref, n_cur = sum(ref_counts), sum(cur_counts)
    return max(abs(cur / n_cur - ref / n_ref) for ref, cur in zip(ref_counts, cur_counts))


def js_benchmark(alpha, bins, n_ref, n_cur):
    """§5.10.1: PSI's own χ² benchmark divided by `2 ln 2`."""
    return psi_benchmark(alpha, bins, n_ref, n_cur) / (2.0 * math.log(2.0))


def linf_benchmark(alpha, bins, n_ref, n_cur):
    """§5.10.1's two-sided Hoeffding bound with the Bonferroni factor over B
    bins: `sqrt(−ln(alpha/(2B)) · (1/n_ref + 1/n_cur) / 2)`."""
    return math.sqrt(-math.log(alpha / (2.0 * bins)) * (1.0 / n_ref + 1.0 / n_cur) / 2.0)


def binned(kind, **overrides):
    """A binned distribution monitor over two bins, like `psi_monitor`."""
    params = {
        "field": "prediction",
        "bins": 2,
        "reference": leading(10),
        "window": count(10),
        "threshold": at_most(NEVER),
        "min_n": 10,
        "response": "warn",
    }
    params.update(overrides)
    return MONITOR_KINDS.resolve(kind)(params, name=kind)


#: A window entirely above the anchor's median: p = (1/2, 1/2), q = (0, 1).
SHIFTED = [prediction_record(float(v)) for v in range(11, 21)]

#: An anchor with no spread at all, and a window that shares none of its
#: bins: p = (0, 1), q = (1, 0) — the only way two quantile-binned samples
#: can be disjoint, and the case that pins the top of the [0, 1] range.
DISJOINT_ANCHOR = [prediction_record(1.0) for _ in range(10)]
DISJOINT_WINDOW = [prediction_record(0.0) for _ in range(10)]


def test_jensen_shannon_is_exactly_zero_on_identical_samples():
    monitor = binned("jensen_shannon")
    monitor.fit(ANCHOR)
    feed(monitor, ANCHOR)
    assert monitor.verdict().statistic == 0.0


def test_jensen_shannon_is_the_base_two_divergence_over_the_references_bins():
    monitor = binned("jensen_shannon")
    monitor.fit(ANCHOR)
    feed(monitor, SHIFTED)
    assert monitor.verdict().statistic == pytest.approx(js_of([5, 5], [0, 10]))
    assert monitor.verdict().statistic == pytest.approx(0.31127812445913283)


def test_jensen_shannon_is_bounded_by_one_and_reaches_it_on_disjoint_bins():
    monitor = binned("jensen_shannon")
    monitor.fit(DISJOINT_ANCHOR)
    feed(monitor, DISJOINT_WINDOW)
    assert monitor.verdict().statistic == pytest.approx(1.0)
    assert monitor.verdict().statistic == pytest.approx(js_of([0, 10], [10, 0]))


@pytest.mark.parametrize(
    "window",
    [ANCHOR, SHIFTED, DISJOINT_WINDOW, [prediction_record(5.5) for _ in range(10)]],
)
def test_the_jensen_shannon_statistic_never_leaves_the_unit_interval(window):
    monitor = binned("jensen_shannon")
    monitor.fit(ANCHOR)
    feed(monitor, window)
    assert 0.0 <= monitor.verdict().statistic <= 1.0


def test_the_jensen_shannon_benchmark_is_the_chi_square_one_over_two_ln_two():
    monitor = binned("jensen_shannon", bins=4)
    assert monitor.critical_value(0.05, 500, 300) == pytest.approx(
        js_benchmark(0.05, 4, 500, 300)
    )
    assert monitor.critical_value(0.01, 40, 40) == pytest.approx(
        js_benchmark(0.01, 4, 40, 40)
    )


def test_both_chi_square_benchmarks_come_from_one_owner(monkeypatch):
    """§5.10.1: the Jensen-Shannon benchmark is "imported from that one
    owner, never restated, so loosening one benchmark cannot leave the other
    behind". Moving the owner must move BOTH."""
    monkeypatch.setattr(monitors, "_chi2_benchmark", lambda *args: 8.0)
    assert binned("psi").critical_value(0.05, 500, 300) == 8.0
    assert binned("jensen_shannon").critical_value(0.05, 500, 300) == pytest.approx(
        8.0 / (2.0 * math.log(2.0))
    )


def test_an_alpha_threshold_on_jensen_shannon_reports_that_benchmark():
    monitor = binned(
        "jensen_shannon", threshold={"kind": "alpha", "alpha": 0.05}, response="warn"
    )
    monitor.fit(ANCHOR)
    feed(monitor, SHIFTED)
    verdict = monitor.verdict()
    assert verdict.threshold == pytest.approx(js_benchmark(0.05, 2, 10, 10))
    assert verdict.status == ("warn" if verdict.statistic > verdict.threshold else "ok")


def test_linf_is_the_largest_bin_proportion_gap():
    monitor = binned("linf")
    monitor.fit(ANCHOR)
    feed(monitor, SHIFTED)
    assert monitor.verdict().statistic == pytest.approx(linf_of([5, 5], [0, 10]))
    assert monitor.verdict().statistic == pytest.approx(0.5)


def test_linf_is_zero_on_identical_samples_and_one_on_disjoint_bins():
    same = binned("linf")
    same.fit(ANCHOR)
    feed(same, ANCHOR)
    assert same.verdict().statistic == 0.0

    apart = binned("linf")
    apart.fit(DISJOINT_ANCHOR)
    feed(apart, DISJOINT_WINDOW)
    assert apart.verdict().statistic == pytest.approx(1.0)


def test_linf_refuses_fewer_than_two_bins():
    for bins in (1, 0, -3):
        with pytest.raises(ProductionError) as exc:
            binned("linf", bins=bins)
        assert "bins" in str(exc.value)


def test_the_linf_critical_value_carries_the_bonferroni_factor_over_the_bins():
    """§5.10.1: "taking a maximum over B comparisons and testing it at alpha
    without the correction is how a drift monitor learns to cry wolf". The
    corrected bound is strictly the wider one, and it widens with B."""
    monitor = binned("linf", bins=10)
    assert monitor.critical_value(0.05, 500, 300) == pytest.approx(
        linf_benchmark(0.05, 10, 500, 300)
    )
    uncorrected = math.sqrt(-math.log(0.05 / 2.0) * (1 / 500 + 1 / 300) / 2.0)
    assert monitor.critical_value(0.05, 500, 300) > uncorrected
    assert binned("linf", bins=20).critical_value(0.05, 500, 300) > monitor.critical_value(
        0.05, 500, 300
    )


def test_the_three_binned_monitors_share_one_bins_knob_and_one_binning():
    """§5.10.1: both new distances are taken "over the reference's own
    quantile bins, so both share PSI's binning and neither re-derives it".
    One reference, one window, three statistics over the SAME bin counts."""
    # two of the window's ten values fall below the anchor's median, so every
    # bin is populated and PSI's epsilon floor cannot mask a binning difference
    window = [prediction_record(float(v)) for v in (1, 2, 11, 12, 13, 14, 15, 16, 17, 18)]
    reference_counts, window_counts = [5, 5], [2, 8]
    expected = {
        "psi": psi_of(reference_counts, window_counts),
        "jensen_shannon": js_of(reference_counts, window_counts),
        "linf": linf_of(reference_counts, window_counts),
    }
    for kind, want in expected.items():
        monitor = binned(kind)
        monitor.fit(ANCHOR)
        feed(monitor, window)
        assert monitor.verdict().statistic == pytest.approx(want), kind
        assert "bins" in type(monitor)._PARAMS


# ---------------------------------------------------------------------------
# The rules §5.10 pins by test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_every_registered_kind_answers_ok_and_is_never_provisional(kind):
    monitor = feed(build(kind), [kind_record(kind, i) for i in range(4)])
    verdict = monitor.verdict()
    assert verdict.status in MONITOR_STATUSES
    assert verdict.status == "ok"
    assert verdict.provisional is False
    assert verdict.slice == "all"
    assert verdict.window == "count:2"
    assert verdict.n_cur == 2


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_below_min_n_the_verdict_is_insufficient_and_never_ok(kind):
    monitor = feed(build(kind, min_n=5), [kind_record(kind, i) for i in range(4)])
    verdict = monitor.verdict()
    assert verdict.status == "insufficient"
    assert monitor.should_trip() is False


@pytest.mark.parametrize("kind", ALL_KINDS)
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


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_every_kind_round_trips_its_state(kind):
    live = feed(build(kind), [kind_record(kind, i) for i in range(4)])
    restored = build(kind)
    restored.restore(json.loads(json.dumps(live.state())))
    assert live.verdict() == restored.verdict()


# ---------------------------------------------------------------------------
# Determinism — the same tape gives the same verdict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ALL_KINDS)
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
