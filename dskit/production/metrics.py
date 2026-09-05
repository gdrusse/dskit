"""The metrics registry: the one place a swallowed failure leaves a count (§5.11.1).

§5.11 requires an alert sink that times out, a bounded queue that drops and
a flush that cannot write to be SWALLOWED, so a tick survives them — which
makes a count the only evidence any of it happened. This registry holds
those counts, and three rules keep it from becoming a hazard of its own:

* **The tables live in ``vocab.py``.** ``METRIC_NAMES`` and
  ``METRIC_LABEL_VALUES`` are the closed tables; this module reads them
  and declares nothing of its own. A name outside the table refuses at
  declaration, and so does a label NAME the table does not give the
  metric — both are written by us, and refusing at startup costs nothing.
* **The hot path never raises.** A label VALUE arrives from a venue, a
  monitor or an operator; refusing it would let telemetry kill a tick. An
  undeclared value drops to the reserved value ``other`` and increments
  ``metrics_label_cardinality_dropped_total``, which the registry declares
  for itself at construction so a drop is visible with no setup. A
  ``labels_max_cardinality`` bound on the product of declared values (the
  reserved value counted) refuses an unbounded metric at declaration.
* **Nothing here is a decision input.** Values are process-local ints and
  floats; a ``Decimal`` refuses, because a ``Decimal`` in a counter is
  money that took a wrong turn. ``policy.py``, ``guards.py`` and
  ``accounting.py`` never read a metric.

Naming is Prometheus-shaped: ``snake_case``, ``_total`` on counters and
only on counters, seconds and bytes as the base units. ``flush(at_ms,
tick_id)`` appends one JSON object per tick to ``<log_dir>/metrics.jsonl``
and is told the instant by the loop — nothing here reads a clock; a flush
that cannot write is counted in ``flush_failures`` and swallowed.
"""

import copy
import json
import math
import os
from abc import ABC, abstractmethod
from pathlib import Path

from dskit.pipeline.node import check_int_param
from dskit.production import vocab
from dskit.production.base import ProductionError
from dskit.production.redact import get_logger

__all__ = [
    "COUNTER_SUFFIX",
    "Counter",
    "DEFAULT_BUCKETS",
    "DEFAULT_LABELS_MAX_CARDINALITY",
    "Gauge",
    "Histogram",
    "INF_BUCKET",
    "METRICS_FILENAME",
    "Metrics",
    "RESERVED_LABEL_VALUE",
]

#: The suffix that makes a name a counter — and a counter the only thing
#: it may be declared as.
COUNTER_SUFFIX = "_total"
#: Where an undeclared label value lands; no table in ``vocab.py`` may
#: contain it, or a dropped sample would pass for a real one.
RESERVED_LABEL_VALUE = "other"
#: The flush file under ``placement.log_dir``.
METRICS_FILENAME = "metrics.jsonl"
#: The histogram's closing bucket, which every observation falls into.
INF_BUCKET = "+Inf"
#: Prometheus's default latency buckets, in seconds (the base unit).
DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
#: The bound on a metric's series count: the product over its labels of
#: the declared values plus the reserved one. Wide enough for the whole
#: phase-1 table, small enough that a label bound to a free-text field
#: could never be declared.
DEFAULT_LABELS_MAX_CARDINALITY = 1000

#: The registry's own counter (§5.11.1), declared for itself at construction.
_DROPPED = "metrics_label_cardinality_dropped_total"

_log = get_logger("metrics")


def _is_number(value):
    """Return whether ``value`` is an int or a float and not a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_value(value):
    """Refuse anything but a finite int or float — a ``Decimal`` is money astray."""
    if not _is_number(value):
        raise ProductionError(
            [f"a metric value is an int or a float, got {value!r} ({type(value).__name__})"]
        )
    if not math.isfinite(value):
        raise ProductionError([f"a metric value must be finite, got {value!r}"])


def _declared_labels(problems, handle_cls, name, labels, max_cardinality):
    """Validate a declaration against the closed table; return the sorted label names."""
    if not isinstance(name, str) or name not in vocab.METRIC_LABEL_VALUES:
        problems.append(
            f"{name!r} is not a declared metric name — vocab.METRIC_NAMES is the closed table"
        )
        return ()
    if name.endswith(COUNTER_SUFFIX) and not handle_cls._COUNTS:
        problems.append(
            f"{name}: a name ending in {COUNTER_SUFFIX!r} is a counter; declare it with counter()"
        )
    elif handle_cls._COUNTS and not name.endswith(COUNTER_SUFFIX):
        problems.append(f"{name}: a counter's name ends in {COUNTER_SUFFIX!r}")
    declared = vocab.METRIC_LABEL_VALUES[name]
    try:
        wanted = tuple(labels)
    except TypeError:
        problems.append(f"{name}: labels must be a sequence of names, got {labels!r}")
        return ()
    if any(not isinstance(label, str) for label in wanted):
        problems.append(f"{name}: every label is a string, got {wanted!r}")
        return ()
    for label in sorted(set(wanted) - set(declared)):
        problems.append(f"{name}: label {label!r} is not declared for this metric")
    if len(set(wanted)) != len(wanted):
        problems.append(f"{name}: duplicate label names in {wanted!r}")
    if set(wanted) != set(declared):
        problems.append(f"{name}: labels must be exactly {sorted(declared)}, got {sorted(wanted)}")
    product = 1
    for values in declared.values():
        product *= len(values) + 1
    if product > max_cardinality:
        problems.append(
            f"{name}: label cardinality {product} (declared values plus "
            f"{RESERVED_LABEL_VALUE!r} per label) exceeds labels_max_cardinality {max_cardinality}"
        )
    return tuple(sorted(wanted))


def _declared_buckets(problems, name, buckets):
    """Validate histogram bounds — finite, non-empty, strictly ascending — as floats."""
    try:
        values = tuple(buckets)
    except TypeError:
        problems.append(f"{name}: buckets must be a sequence of numbers, got {buckets!r}")
        return ()
    if not values:
        problems.append(f"{name}: buckets must not be empty")
        return ()
    if any(not _is_number(value) or not math.isfinite(value) for value in values):
        problems.append(f"{name}: every bucket bound must be a finite number, got {values!r}")
        return ()
    if any(later <= earlier for earlier, later in zip(values, values[1:])):
        problems.append(f"{name}: buckets must be strictly ascending, got {values!r}")
    return tuple(float(value) for value in values)


class _Metric(ABC):
    """A declared metric's handle: label names checked, values dropped, series kept."""

    #: Whether the name must carry ``COUNTER_SUFFIX`` (and only then).
    _COUNTS = False

    def __init__(self, registry, name, labels, buckets=None):
        self._registry = registry
        self._name = name
        self._labels = tuple(labels)
        self._allowed = {
            label: frozenset(vocab.METRIC_LABEL_VALUES[name][label]) for label in labels
        }
        self._buckets = buckets
        self._series = {}
        if not self._labels:
            self._series[""] = self._zero()

    @property
    def name(self):
        """Return the declared metric name."""
        return self._name

    @property
    def labels(self):
        """Return the declared label names, sorted."""
        return self._labels

    def series(self):
        """Return a deep copy of every series: ``{series key: value}``.

        Returns
        -------
        dict
            The series key is ``k=v`` pairs sorted by name and joined by
            ``,`` — ``""`` for an unlabelled metric.
        """
        return copy.deepcopy(self._series)

    @abstractmethod
    def _zero(self):
        """Return a fresh series value."""

    def _shape(self):
        """Return what a redeclaration must match exactly."""
        return (type(self), self._labels, self._buckets)

    def _series_for(self, labels):
        """Return the key the label values select, creating the series; drop undeclared values."""
        unknown = sorted(set(labels) - set(self._labels))
        missing = sorted(set(self._labels) - set(labels))
        if unknown or missing:
            raise ProductionError(
                [f"{self._name}: labels are exactly {list(self._labels)}; unknown {unknown}, missing {missing}"]
            )
        parts = []
        for label in self._labels:
            value = labels[label]
            if not (isinstance(value, str) and value in self._allowed[label]):
                value = RESERVED_LABEL_VALUE
                self._registry._count_drop()
            parts.append(f"{label}={value}")
        key = ",".join(parts)
        if key not in self._series:
            self._series[key] = self._zero()
        return key


class Counter(_Metric):
    """A monotonic counter: ``inc(n=1, **labels)`` and never backwards.

    Obtained from :meth:`Metrics.counter`; the same name always hands back
    the same handle, so two callers share one series.

    Examples
    --------
    ::

        registry = Metrics()
        ticks = registry.counter("ticks_total", labels=("status",))
        ticks.inc(status="decided")
        ticks.inc(2, status="decided")
        registry.snapshot()["ticks_total"]   # {'status=decided': 3}
    """

    _COUNTS = True

    def _zero(self):
        """Start at zero."""
        return 0

    def inc(self, n=1, **labels):
        """Add ``n`` to the series the label values select.

        Parameters
        ----------
        n : int or float
            The increment, ``>= 0``.
        **labels
            One value per declared label name; an undeclared VALUE drops
            to the reserved value.

        Raises
        ------
        ProductionError
            If ``n`` is negative, non-finite or not a number, or the label
            NAMES are not exactly the declared ones.
        """
        _check_value(n)
        if n < 0:
            raise ProductionError([f"{self._name}: a counter never decreases, got {n!r}"])
        self._series[self._series_for(labels)] += n


class Gauge(_Metric):
    """A gauge: ``set(value, **labels)`` holds the last value it was given.

    Examples
    --------
    ::

        registry = Metrics()
        gauge = registry.gauge("ledger_append_seconds")
        gauge.set(0.25)
        registry.snapshot()["ledger_append_seconds"]   # {'': 0.25}
    """

    def _zero(self):
        """Start at zero."""
        return 0.0

    def set(self, value, **labels):
        """Set the series the label values select to ``value``.

        Parameters
        ----------
        value : int or float
            The reading.
        **labels
            One value per declared label name; an undeclared VALUE drops
            to the reserved value.

        Raises
        ------
        ProductionError
            If ``value`` is non-finite or not a number, or the label NAMES
            are not exactly the declared ones.
        """
        _check_value(value)
        self._series[self._series_for(labels)] = value


class Histogram(_Metric):
    """A histogram: ``observe(value, **labels)`` into cumulative buckets.

    Each series is ``{"count", "sum", "buckets"}``; ``buckets`` maps each
    bound (as ``str(float)``) to how many observations were ``<=`` it,
    closing with ``INF_BUCKET``, which holds them all.

    Examples
    --------
    ::

        registry = Metrics()
        latency = registry.histogram("ledger_append_seconds", buckets=(0.1, 1.0))
        latency.observe(0.05)
        latency.observe(5.0)
        registry.snapshot()["ledger_append_seconds"][""]["buckets"]
        # -> {'0.1': 1, '1.0': 1, '+Inf': 2}
    """

    def _zero(self):
        """Start with empty buckets."""
        counts = {str(bound): 0 for bound in self._buckets}
        counts[INF_BUCKET] = 0
        return {"count": 0, "sum": 0, "buckets": counts}

    def observe(self, value, **labels):
        """Record one observation in the series the label values select.

        Parameters
        ----------
        value : int or float
            The observation, in the metric's base unit.
        **labels
            One value per declared label name; an undeclared VALUE drops
            to the reserved value.

        Raises
        ------
        ProductionError
            If ``value`` is non-finite or not a number, or the label NAMES
            are not exactly the declared ones.
        """
        _check_value(value)
        series = self._series[self._series_for(labels)]
        series["count"] += 1
        series["sum"] += value
        counts = series["buckets"]
        for bound in self._buckets:
            if value <= bound:
                counts[str(bound)] += 1
        counts[INF_BUCKET] += 1


class Metrics:
    """The registry: declare from the closed table, record, snapshot, flush.

    Parameters
    ----------
    log_dir : str or pathlib.Path or None, optional
        ``placement.log_dir``; :meth:`flush` appends to
        ``<log_dir>/metrics.jsonl``. ``None`` flushes nothing.
    labels_max_cardinality : int, optional
        The bound on any one metric's series count, judged at declaration
        over the closed value table; default ``DEFAULT_LABELS_MAX_CARDINALITY``.

    Attributes
    ----------
    flush_failures : int
        How many flushes could not write — counted and swallowed, never
        raised.

    Examples
    --------
    ::

        registry = Metrics()
        ticks = registry.counter("ticks_total", labels=("status",))
        ticks.inc(status="decided")
        ticks.inc(status="a_status_nobody_declared")
        registry.snapshot()["ticks_total"]
        # -> {'status=decided': 1, 'status=other': 1}
        registry.snapshot()["metrics_label_cardinality_dropped_total"]   # {'': 1}
        registry.flush(1_767_225_600_000, "tick-1")   # False: no log_dir
    """

    def __init__(self, log_dir=None, labels_max_cardinality=DEFAULT_LABELS_MAX_CARDINALITY):
        problems = []
        check_int_param(problems, "labels_max_cardinality", labels_max_cardinality, ge=1)
        if problems:
            raise ProductionError(problems)
        self._log_dir = None if log_dir is None else Path(log_dir)
        self._max_cardinality = int(labels_max_cardinality)
        self._metrics = {}
        self._flush_failures = 0
        self._dropped = self.counter(_DROPPED)

    @property
    def flush_failures(self):
        """Return how many flushes failed to write."""
        return self._flush_failures

    def counter(self, name, labels=()):
        """Declare (or fetch) a counter; the name must end in ``_total``.

        Parameters
        ----------
        name : str
            A ``vocab.METRIC_NAMES`` member ending in ``COUNTER_SUFFIX``.
        labels : sequence of str, optional
            Exactly the label names the table declares for ``name``.

        Returns
        -------
        Counter
            The one handle for ``name``.

        Raises
        ------
        ProductionError
            If the name is undeclared or not a counter's, the labels are
            not the declared set, the cardinality bound is exceeded, or the
            name is already declared with a different shape.
        """
        return self._declare(Counter, name, labels, None)

    def gauge(self, name, labels=()):
        """Declare (or fetch) a gauge; the name must not end in ``_total``.

        Parameters
        ----------
        name : str
            A ``vocab.METRIC_NAMES`` member that is not a counter's.
        labels : sequence of str, optional
            Exactly the label names the table declares for ``name``.

        Returns
        -------
        Gauge
            The one handle for ``name``.

        Raises
        ------
        ProductionError
            As for :meth:`counter`.
        """
        return self._declare(Gauge, name, labels, None)

    def histogram(self, name, labels=(), buckets=None):
        """Declare (or fetch) a histogram; the name must not end in ``_total``.

        Parameters
        ----------
        name : str
            A ``vocab.METRIC_NAMES`` member that is not a counter's.
        labels : sequence of str, optional
            Exactly the label names the table declares for ``name``.
        buckets : sequence of float or None, optional
            Finite, strictly ascending bounds; ``None`` means
            ``DEFAULT_BUCKETS``.

        Returns
        -------
        Histogram
            The one handle for ``name``.

        Raises
        ------
        ProductionError
            As for :meth:`counter`, or if the buckets are empty, non-finite
            or not strictly ascending.
        """
        return self._declare(Histogram, name, labels, DEFAULT_BUCKETS if buckets is None else buckets)

    def _declare(self, handle_cls, name, labels, buckets):
        """Validate against the table, then create or hand back the one handle."""
        problems = []
        labels = _declared_labels(problems, handle_cls, name, labels, self._max_cardinality)
        if buckets is not None:
            buckets = _declared_buckets(problems, name, buckets)
        if problems:
            raise ProductionError(problems)
        existing = self._metrics.get(name)
        if existing is not None:
            if existing._shape() != (handle_cls, labels, buckets):
                raise ProductionError([f"{name} is already declared with a different shape"])
            return existing
        handle = handle_cls(self, name, labels, buckets)
        self._metrics[name] = handle
        return handle

    def _count_drop(self):
        """Count one label value collapsed into the reserved value."""
        self._dropped.inc()

    def snapshot(self):
        """Return every declared metric's series as a copy the caller may keep.

        Returns
        -------
        dict
            ``{name: {series key: value}}``; a labelled metric with no
            recording yet maps to ``{}``.
        """
        return {name: handle.series() for name, handle in self._metrics.items()}

    def flush(self, at_ms, tick_id):
        """Append one ``{at_ms, tick_id, metrics}`` line to the flush file.

        Counters are cumulative: a flush is a reading, not a reset. A
        failure to write is counted in ``flush_failures``, logged and
        swallowed — it can never fail a tick.

        Parameters
        ----------
        at_ms : int
            The instant, told by the loop.
        tick_id : str
            The tick the reading closes.

        Returns
        -------
        bool
            ``True`` if the line was written; ``False`` with no ``log_dir``
            or when the write failed.
        """
        if self._log_dir is None:
            return False
        record = {"at_ms": at_ms, "tick_id": tick_id, "metrics": self.snapshot()}
        try:
            line = json.dumps(record, allow_nan=False)
            os.makedirs(self._log_dir, exist_ok=True)
            with open(self._log_dir / METRICS_FILENAME, "a", encoding="utf-8") as sink:
                sink.write(line + "\n")
        except (OSError, TypeError, ValueError) as error:
            self._flush_failures += 1
            _log.warning("metrics flush failed (%d so far): %s", self._flush_failures, error)
            return False
        return True
