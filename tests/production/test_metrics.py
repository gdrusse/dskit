"""`metrics.py` — the registry that exists because §5.11 swallows failures.

An alert sink that times out, a bounded queue that drops, a flush that cannot
write: §5.11 requires every one of those to be swallowed so a tick survives
them, which means the ONLY evidence they happened is a count. That is the whole
argument for this module, and it sets what these tests care about.

- **A name is either in the closed table or it is a refusal.** `METRIC_NAMES`
  and `METRIC_LABEL_VALUES` live in `vocab.py` (§5.0 admits no exception), so
  the tests below read the table rather than restating it, and assert what
  `metrics.py` DOES with it: an undeclared name refuses at declaration, and
  every name the table carries can actually be declared.
- **The hot path never raises.** An undeclared label VALUE arrives from a
  venue, a monitor or an operator, and refusing it would let telemetry kill a
  tick — so it drops to the reserved value `other` and increments
  `metrics_label_cardinality_dropped_total`. An undeclared label NAME is a
  programming error and refuses at declaration, where nothing is at stake.
- **Nothing here is a decision input.** Values are ints and floats; a
  `Decimal` refuses, because a `Decimal` in a counter is money that took a
  wrong turn (§5.11.1: 'never an input to a decision, a guard or a record').

Naming is Prometheus-shaped and pinned: `snake_case`, `_total` only on
counters, seconds as the base unit. `tests/production/test_vocab.py` pins the
table's CONTENT; this file pins the registry's BEHAVIOUR over it.

No clock is injected because `flush(at_ms, tick_id)` is told the instant by the
loop; nothing in this module reads time.
"""

import json
import math
import re
from decimal import Decimal

import pytest

from dskit.production import metrics as metrics_module
from dskit.production import vocab
from dskit.production.base import ProductionError
from dskit.production.metrics import (
    DEFAULT_BUCKETS,
    DEFAULT_LABELS_MAX_CARDINALITY,
    INF_BUCKET,
    METRICS_FILENAME,
    RESERVED_LABEL_VALUE,
    Metrics,
)

#: The registry's own counter — §5.11.1 names it, and it is the one metric
#: `Metrics` declares for itself so a drop is visible without setup.
DROPPED = "metrics_label_cardinality_dropped_total"

#: Prometheus naming: snake_case, lowercase, starting with a letter.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Suffixes that are a unit in the wrong base — §5.11.1 fixes seconds and
#: bytes as the bases, so any of these in a name is the defect.
WRONG_UNIT_SUFFIXES = (
    "_ms", "_us", "_ns", "_s", "_sec", "_secs", "_millis", "_micros",
    "_kb", "_mb", "_gb", "_percent", "_pct", "_count",
)

OBSERVED_NAMES = tuple(n for n in vocab.METRIC_NAMES if not n.endswith("_total"))


def label_names(metric):
    """The label NAMES the closed table declares for a metric."""
    return tuple(sorted(vocab.METRIC_LABEL_VALUES[metric]))


def a_value(metric, label):
    """One permitted value for a label, taken from the closed table."""
    return vocab.METRIC_LABEL_VALUES[metric][label][0]


def sample_labels(metric):
    """A legal label set for a metric, straight from the closed table."""
    return {label: a_value(metric, label) for label in label_names(metric)}


def key(**labels):
    """The canonical series key: `k=v` pairs sorted by name, joined by `,`."""
    return ",".join(f"{name}={labels[name]}" for name in sorted(labels))


def declare(registry, metric, **kwargs):
    """Declare a metric the way its suffix says it must be declared."""
    labels = label_names(metric)
    if metric.endswith("_total"):
        return registry.counter(metric, labels=labels, **kwargs)
    return registry.histogram(metric, labels=labels, **kwargs)


def refusal(callable_):
    """Run something expected to refuse; return the accumulated problems."""
    with pytest.raises(ProductionError) as excinfo:
        callable_()
    assert excinfo.value.problems, "ProductionError carries a LIST of problems"
    return "; ".join(excinfo.value.problems)


# ---------------------------------------------------------------------------
# The module's boundary
# ---------------------------------------------------------------------------


def test_module_exports_only_public_names_and_every_name_it_declares():
    assert metrics_module.__all__, "`__all__` IS the API contract (CLAUDE.md)"
    for name in metrics_module.__all__:
        assert not name.startswith("_"), f"{name} is private and must not export"
        assert hasattr(metrics_module, name), f"{name} is exported but not defined"


def test_the_closed_tables_are_not_restated_here():
    """§5.0/§5.11.1: `METRIC_NAMES` and `METRIC_LABEL_VALUES` live in
    `vocab.py`; a second copy in `metrics.py` is the copy that diverges."""
    for name in ("METRIC_NAMES", "METRIC_LABEL_VALUES"):
        assert not hasattr(metrics_module, name), (
            f"{name} belongs to vocab.py, not metrics.py"
        )


def test_the_reserved_value_and_the_flush_filename_are_named_constants():
    assert RESERVED_LABEL_VALUE == "other"
    assert METRICS_FILENAME == "metrics.jsonl"
    assert INF_BUCKET == "+Inf"


def test_other_is_genuinely_reserved_and_no_label_declares_it():
    """The drop target must not collide with a real value, or a dropped
    sample would be indistinguishable from a legitimate one."""
    for metric, labels in vocab.METRIC_LABEL_VALUES.items():
        for label, values in labels.items():
            assert RESERVED_LABEL_VALUE not in values, f"{metric}.{label}"


# ---------------------------------------------------------------------------
# Naming — Prometheus shape, base units, `_total` only on counters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", vocab.METRIC_NAMES)
def test_every_declared_name_is_snake_case(name):
    assert NAME_RE.match(name), name


@pytest.mark.parametrize("name", vocab.METRIC_NAMES)
def test_no_declared_name_carries_a_unit_outside_the_base_units(name):
    for suffix in WRONG_UNIT_SUFFIXES:
        assert not name.endswith(suffix), f"{name}: seconds and bytes are the bases"


@pytest.mark.parametrize("name", vocab.METRIC_NAMES)
def test_a_name_is_declarable_as_exactly_the_kind_its_suffix_promises(name):
    """§5.11.1: '`_total` for monotonic counters'. The suffix is the contract,
    so a `_total` histogram and a non-`_total` counter both refuse."""
    labels = label_names(name)
    if name.endswith("_total"):
        assert Metrics().counter(name, labels=labels) is not None
        with pytest.raises(ProductionError):
            Metrics().histogram(name, labels=labels)
        with pytest.raises(ProductionError):
            Metrics().gauge(name, labels=labels)
    else:
        assert Metrics().histogram(name, labels=labels) is not None
        with pytest.raises(ProductionError):
            Metrics().counter(name, labels=labels)


@pytest.mark.parametrize("name", OBSERVED_NAMES)
def test_every_non_counter_name_carries_a_base_unit(name):
    assert name.endswith("_seconds") or name.endswith("_bytes"), name


@pytest.mark.parametrize("name", vocab.METRIC_NAMES)
def test_every_name_in_the_phase_one_table_can_be_declared(name):
    """§8's `test_metrics.py` line: 'every §5.11 counter name exists'."""
    handle = declare(Metrics(), name)
    assert handle is not None


def test_the_two_counters_section_five_eleven_swallows_failures_into_exist():
    """§5.11 counts a swallowed sink failure and a suppressed alert; §5.11.1
    says those two names 'resolve here and nowhere else'."""
    registry = Metrics()
    assert registry.counter(
        "alert_sink_failures_total", labels=label_names("alert_sink_failures_total")
    ) is not None
    assert registry.counter(
        "alerts_suppressed_total", labels=label_names("alerts_suppressed_total")
    ) is not None


# ---------------------------------------------------------------------------
# Declaration — the closed table refuses everything outside it
# ---------------------------------------------------------------------------


def test_an_undeclared_metric_name_refuses_at_declaration():
    assert "cpu_total" in refusal(lambda: Metrics().counter("cpu_total"))


def test_an_undeclared_metric_name_refuses_for_every_kind():
    registry = Metrics()
    for build in (registry.counter, registry.gauge, registry.histogram):
        with pytest.raises(ProductionError):
            build("nonsuch_seconds")


def test_an_undeclared_label_name_refuses_at_declaration():
    """A label NAME is written by us, not by a venue — the refusal costs
    nothing at startup and stops an unbounded dimension at the source."""
    text = refusal(lambda: Metrics().counter("ticks_total", labels=("colour",)))
    assert "colour" in text


def test_a_label_set_that_is_not_the_declared_one_refuses():
    """`submits_total` declares three labels; two is a different metric."""
    text = refusal(lambda: Metrics().counter("submits_total", labels=("rung",)))
    assert "submits_total" in text


def test_declaring_an_unlabelled_metric_with_labels_refuses():
    with pytest.raises(ProductionError):
        Metrics().histogram("ledger_append_seconds", labels=("phase",))


def test_redeclaring_the_same_metric_hands_back_the_same_handle():
    """The loop and the leg both reach for `submits_total`; two handles over
    one series would be two truths."""
    registry = Metrics()
    labels = label_names("submits_total")
    first = registry.counter("submits_total", labels=labels)
    assert registry.counter("submits_total", labels=labels) is first


def test_redeclaring_a_metric_with_a_different_shape_refuses():
    registry = Metrics()
    registry.counter("ticks_total", labels=("status",))
    with pytest.raises(ProductionError):
        registry.counter("ticks_total", labels=())


def test_labels_max_cardinality_bounds_the_declared_product():
    """§5.11.1: '`labels_max_cardinality` bounds the product'. The values are
    closed, so the product is knowable at declaration and refused there."""
    labels = label_names("submits_total")
    text = refusal(
        lambda: Metrics(labels_max_cardinality=2).counter("submits_total", labels=labels)
    )
    assert "submits_total" in text


def test_the_default_cardinality_bound_is_one_named_constant_that_fits_the_table():
    assert isinstance(DEFAULT_LABELS_MAX_CARDINALITY, int)
    assert DEFAULT_LABELS_MAX_CARDINALITY >= 1
    registry = Metrics()
    for name in vocab.METRIC_NAMES:
        declare(registry, name)


def test_the_registry_declares_its_own_dropped_counter_at_construction():
    """Nothing has to remember to declare it, or the first drop would be the
    thing that raises."""
    assert Metrics().snapshot()[DROPPED] == {"": 0}


# ---------------------------------------------------------------------------
# Recording — counters, gauges, histograms
# ---------------------------------------------------------------------------


def test_a_counter_starts_with_no_series_and_increments_by_one_by_default():
    registry = Metrics()
    counter = registry.counter("ticks_total", labels=("status",))
    status = a_value("ticks_total", "status")
    assert registry.snapshot()["ticks_total"] == {}, (
        "a labelled metric has no series until one is recorded"
    )
    counter.inc(status=status)
    counter.inc(status=status)
    assert registry.snapshot()["ticks_total"][key(status=status)] == 2


def test_a_counter_increments_by_the_amount_it_is_given():
    registry = Metrics()
    counter = registry.counter("ticks_total", labels=("status",))
    status = a_value("ticks_total", "status")
    counter.inc(3, status=status)
    counter.inc(0, status=status)
    assert registry.snapshot()["ticks_total"][key(status=status)] == 3


def test_a_counter_refuses_to_go_backwards():
    """`_total` means monotonic; a decrement is the bug the suffix promises
    cannot happen."""
    registry = Metrics()
    counter = registry.counter("ticks_total", labels=("status",))
    with pytest.raises(ProductionError):
        counter.inc(-1, status=a_value("ticks_total", "status"))


def test_an_unlabelled_counter_records_under_the_empty_key():
    registry = Metrics()
    counter = registry.counter(DROPPED)
    counter.inc()
    assert registry.snapshot()[DROPPED] == {"": 1}


def test_every_label_is_part_of_the_series_key_sorted_by_name():
    registry = Metrics()
    counter = registry.counter("submits_total", labels=label_names("submits_total"))
    labels = sample_labels("submits_total")
    counter.inc(**labels)
    assert registry.snapshot()["submits_total"] == {key(**labels): 1}


def test_two_label_sets_are_two_series_on_one_metric():
    registry = Metrics()
    counter = registry.counter("monitor_verdicts_total",
                              labels=label_names("monitor_verdicts_total"))
    values = vocab.METRIC_LABEL_VALUES["monitor_verdicts_total"]["status"]
    monitor = a_value("monitor_verdicts_total", "monitor")
    counter.inc(monitor=monitor, status=values[0])
    counter.inc(monitor=monitor, status=values[1])
    counter.inc(monitor=monitor, status=values[1])
    series = registry.snapshot()["monitor_verdicts_total"]
    assert series[key(monitor=monitor, status=values[0])] == 1
    assert series[key(monitor=monitor, status=values[1])] == 2


def test_a_missing_label_value_at_record_time_refuses():
    """An absent label is a programming error, not a hostile input: refusing
    it is what stops one series silently absorbing every other."""
    registry = Metrics()
    counter = registry.counter("ticks_total", labels=("status",))
    with pytest.raises(ProductionError):
        counter.inc()


def test_a_label_name_that_was_never_declared_refuses_at_record_time():
    registry = Metrics()
    counter = registry.counter("ticks_total", labels=("status",))
    with pytest.raises(ProductionError):
        counter.inc(status=a_value("ticks_total", "status"), colour="red")


def test_a_gauge_holds_the_last_value_it_was_set_to():
    registry = Metrics()
    gauge = registry.gauge("ledger_append_seconds")
    gauge.set(0.5)
    gauge.set(0.25)
    assert registry.snapshot()["ledger_append_seconds"][""] == pytest.approx(0.25)


def test_a_histogram_counts_and_sums_what_it_observed():
    registry = Metrics()
    histogram = registry.histogram("tick_seconds", labels=("phase",))
    phase = a_value("tick_seconds", "phase")
    histogram.observe(0.25, phase=phase)
    histogram.observe(0.75, phase=phase)
    series = registry.snapshot()["tick_seconds"][key(phase=phase)]
    assert series["count"] == 2
    assert series["sum"] == pytest.approx(1.0)


def test_histogram_buckets_are_cumulative_and_end_at_the_infinity_bucket():
    registry = Metrics()
    histogram = registry.histogram("ledger_append_seconds", buckets=(0.1, 1.0))
    histogram.observe(0.05)
    histogram.observe(5.0)
    series = registry.snapshot()["ledger_append_seconds"][""]
    assert series["buckets"] == {
        str(float(0.1)): 1,
        str(float(1.0)): 1,
        INF_BUCKET: 2,
    }
    assert series["count"] == 2
    assert series["sum"] == pytest.approx(5.05)


def test_the_default_buckets_are_one_named_ascending_tuple():
    assert isinstance(DEFAULT_BUCKETS, tuple)
    assert len(DEFAULT_BUCKETS) >= 2
    assert list(DEFAULT_BUCKETS) == sorted(DEFAULT_BUCKETS)
    assert len(set(DEFAULT_BUCKETS)) == len(DEFAULT_BUCKETS)
    assert all(math.isfinite(b) for b in DEFAULT_BUCKETS)


def test_a_histogram_declared_without_buckets_uses_the_named_default():
    registry = Metrics()
    registry.histogram("ledger_append_seconds").observe(0.0)
    series = registry.snapshot()["ledger_append_seconds"][""]
    assert set(series["buckets"]) == {str(float(b)) for b in DEFAULT_BUCKETS} | {
        INF_BUCKET
    }


@pytest.mark.parametrize(
    "buckets", ((1.0, 0.5), (0.5, 0.5), (), (0.5, float("inf")), (0.5, float("nan")))
)
def test_a_bucket_list_that_is_not_finite_and_strictly_ascending_refuses(buckets):
    with pytest.raises(ProductionError):
        Metrics().histogram("ledger_append_seconds", buckets=buckets)


# ---------------------------------------------------------------------------
# Values — telemetry, never money
# ---------------------------------------------------------------------------


def test_a_decimal_value_refuses_everywhere():
    """§5.11.1: values are process-local ints/floats, not `Decimal`. A
    `Decimal` here is money that wandered out of `records.py`."""
    registry = Metrics()
    counter = registry.counter(DROPPED)
    gauge = Metrics().gauge("ledger_append_seconds")
    histogram = Metrics().histogram("ledger_append_seconds")
    with pytest.raises(ProductionError):
        counter.inc(Decimal("1"))
    with pytest.raises(ProductionError):
        gauge.set(Decimal("0.5"))
    with pytest.raises(ProductionError):
        histogram.observe(Decimal("0.5"))


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_a_non_finite_value_refuses(value):
    with pytest.raises(ProductionError):
        Metrics().histogram("ledger_append_seconds").observe(value)


def test_recorded_values_are_plain_ints_and_floats():
    registry = Metrics()
    registry.counter(DROPPED).inc()
    registry.gauge("tick_seconds", labels=("phase",)).set(
        0.5, phase=a_value("tick_seconds", "phase")
    )
    snapshot = registry.snapshot()
    assert isinstance(snapshot[DROPPED][""], int)
    value = snapshot["tick_seconds"][key(phase=a_value("tick_seconds", "phase"))]
    assert isinstance(value, float)


# ---------------------------------------------------------------------------
# The hot path never raises: an undeclared VALUE drops to `other`
# ---------------------------------------------------------------------------


def test_an_undeclared_label_value_drops_to_the_reserved_value():
    """§5.11.1: 'never unbounded growth, never a raise on the hot path'."""
    registry = Metrics()
    counter = registry.counter("recon_breaks_total", labels=("class",))
    counter.inc(**{"class": "a_break_class_nobody_declared"})
    series = registry.snapshot()["recon_breaks_total"]
    assert series == {key(**{"class": RESERVED_LABEL_VALUE}): 1}


def test_the_drop_increments_the_registrys_own_dropped_counter():
    registry = Metrics()
    counter = registry.counter("recon_breaks_total", labels=("class",))
    assert registry.snapshot()[DROPPED][""] == 0
    counter.inc(**{"class": "nonsuch"})
    counter.inc(**{"class": "another_nonsuch"})
    assert registry.snapshot()[DROPPED][""] == 2


def test_the_drop_is_per_label_and_keeps_the_values_that_were_declared():
    """Dropping the whole series would lose the rung that was perfectly
    good; only the offending dimension collapses."""
    registry = Metrics()
    counter = registry.counter("submits_total", labels=label_names("submits_total"))
    labels = sample_labels("submits_total")
    labels["outcome"] = "nonsuch_outcome"
    counter.inc(**labels)
    expected = dict(labels, outcome=RESERVED_LABEL_VALUE)
    assert registry.snapshot()["submits_total"] == {key(**expected): 1}
    assert registry.snapshot()[DROPPED][""] == 1


def test_repeated_drops_share_one_series_rather_than_growing():
    registry = Metrics()
    counter = registry.counter("refusals_total", labels=("reason",))
    for i in range(50):
        counter.inc(reason=f"nonsuch_{i}")
    series = registry.snapshot()["refusals_total"]
    assert series == {key(reason=RESERVED_LABEL_VALUE): 50}


def test_a_drop_on_a_histogram_never_raises_either():
    registry = Metrics()
    histogram = registry.histogram("tick_seconds", labels=("phase",))
    histogram.observe(0.5, phase="nonsuch_phase")
    series = registry.snapshot()["tick_seconds"]
    assert series[key(phase=RESERVED_LABEL_VALUE)]["count"] == 1
    assert registry.snapshot()[DROPPED][""] == 1


# ---------------------------------------------------------------------------
# Snapshot and the JSONL flush
# ---------------------------------------------------------------------------


def test_a_snapshot_lists_every_declared_metric_and_nothing_else():
    registry = Metrics()
    registry.counter("ticks_total", labels=("status",))
    registry.histogram("ledger_append_seconds")
    assert set(registry.snapshot()) == {"ticks_total", "ledger_append_seconds", DROPPED}


def test_a_snapshot_is_a_copy_the_caller_cannot_mutate_back_in():
    registry = Metrics()
    registry.counter(DROPPED).inc()
    snapshot = registry.snapshot()
    snapshot[DROPPED][""] = 99
    snapshot["invented_total"] = {}
    assert registry.snapshot()[DROPPED][""] == 1
    assert "invented_total" not in registry.snapshot()


def test_flush_appends_one_json_object_per_tick(tmp_path):
    registry = Metrics(log_dir=tmp_path)
    counter = registry.counter("ticks_total", labels=("status",))
    status = a_value("ticks_total", "status")
    counter.inc(status=status)
    assert registry.flush(1_767_225_600_000, "tick-1") is True
    counter.inc(status=status)
    assert registry.flush(1_767_225_660_000, "tick-2") is True

    lines = (tmp_path / METRICS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert set(first) == {"at_ms", "tick_id", "metrics"}
    assert first["at_ms"] == 1_767_225_600_000
    assert first["tick_id"] == "tick-1"
    assert first["metrics"]["ticks_total"][key(status=status)] == 1
    assert second["tick_id"] == "tick-2"
    assert second["metrics"]["ticks_total"][key(status=status)] == 2, (
        "counters are cumulative; a flush is a reading, not a reset"
    )


def test_flush_writes_the_whole_snapshot(tmp_path):
    registry = Metrics(log_dir=tmp_path)
    registry.histogram("tick_seconds", labels=("phase",)).observe(
        0.5, phase=a_value("tick_seconds", "phase")
    )
    registry.flush(1, "tick-1")
    line = json.loads((tmp_path / METRICS_FILENAME).read_text(encoding="utf-8"))
    assert line["metrics"] == json.loads(json.dumps(registry.snapshot()))


def test_a_flush_that_cannot_write_is_counted_and_swallowed(tmp_path):
    """§5.11.1: 'a flush failure is counted and swallowed like a sink
    failure; it can never fail a tick'."""
    not_a_directory = tmp_path / "log_dir"
    not_a_directory.write_text("i am a file", encoding="utf-8")
    registry = Metrics(log_dir=not_a_directory)
    registry.counter(DROPPED).inc()
    assert registry.flush(1, "tick-1") is False
    assert registry.flush(2, "tick-2") is False
    assert registry.flush_failures == 2


def test_a_registry_with_no_log_dir_flushes_nothing_and_counts_no_failure(tmp_path):
    registry = Metrics()
    assert registry.flush(1, "tick-1") is False
    assert registry.flush_failures == 0
    assert not list(tmp_path.iterdir())


def test_the_flush_file_is_the_named_constant_under_the_log_dir(tmp_path):
    registry = Metrics(log_dir=tmp_path)
    registry.flush(1, "tick-1")
    assert [p.name for p in tmp_path.iterdir()] == [METRICS_FILENAME]
