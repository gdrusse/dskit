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
only on counters, seconds, bytes and ``_ratio`` as the base units (a
fraction of one is the dimensionless base; ``_percent`` is a scaled
restatement of it and is refused). ``flush(at_ms, tick_id)`` appends one
JSON object per tick to ``<log_dir>/metrics.jsonl`` and is told the instant
by the loop — nothing here reads a clock; a flush that cannot write is
counted in ``flush_failures`` and swallowed.

Phase 6 adds the EVENT contract on top of the registry (ADR-0117).
:class:`EventCatalogue` is the versioned schema a replay and a paper run
both record into; :class:`EventAdapter` is the one seam a layer subclasses
to map its own records onto it; :class:`EventReadings` reduces one event
into the bounded series an exporter may carry. Two limits are structural,
not stylistic: money never becomes a series (a ``Decimal`` refuses, and
every ``MONEY_FIELDS`` name is one), and ``symbol``/``lead`` never become a
label (``vocab.UNBOUNDED_LABEL_FIELDS`` refuses them at declaration). Full
per-name detail belongs in the event body and the artifacts built from it.
"""

import copy
import json
import math
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from dskit.pipeline.node import check_int_param, class_ref, reject_unknown_params
from dskit.production import vocab
from dskit.production.base import ProductionError, Registry, pin_members
from dskit.production.redact import get_logger

__all__ = [
    "COUNTER_SUFFIX",
    "Counter",
    "DEFAULT_BUCKETS",
    "DEFAULT_LABELS_MAX_CARDINALITY",
    "EventAdapter",
    "EventCatalogue",
    "EventReadings",
    "Gauge",
    "Histogram",
    "INF_BUCKET",
    "METRICS_FILENAME",
    "METRIC_SINK_KINDS",
    "MetricSink",
    "Metrics",
    "RESERVED_LABEL_VALUE",
    "Reading",
    "SERIES_JOIN",
    "SERIES_PAIR",
    "readings",
    "series_key",
    "series_labels",
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
#: What joins a label name to its value inside a series key, and what
#: joins the pairs. Named here because :func:`series_key` and
#: :func:`series_labels` are one recipe read from both ends.
SERIES_PAIR = "="
SERIES_JOIN = ","
#: Prometheus's default latency buckets, in seconds (the base unit).
DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
#: The bound on a metric's series count: the product over its labels of
#: the declared values plus the reserved one. Wide enough for the whole
#: phase-1 table, small enough that a label bound to a free-text field
#: could never be declared.
DEFAULT_LABELS_MAX_CARDINALITY = 1000

#: The three families of :data:`~dskit.production.vocab.METRIC_FAMILIES`,
#: spelled here because :func:`readings` decides between them — pinned to
#: the vocabulary, and EXACTLY, since a family the rule cannot name is a
#: metric no exporter could carry.
_COUNTER, _GAUGE, _HISTOGRAM = pin_members(
    "metrics.py's families", vocab.METRIC_FAMILIES, vocab.METRIC_FAMILIES, exact=True
)

#: The registry's own counter (§5.11.1), declared for itself at construction.
_DROPPED = "metrics_label_cardinality_dropped_total"
#: The exporter counter (§5.11.3), declared for itself for the same reason:
#: a swallowed publish leaves no other evidence.
_SINK_FAILURES = "metric_sink_failures_total"
#: The one params key every seam site may carry beside its knobs.
_NOTES = ("notes",)
#: The key an event body stamps its schema version under — the one name in
#: a body that is not a category (ADR-0117).
_SCHEMA_VERSION = "schema_version"
#: The bound the registry's OWN two counters are declared under. They come
#: from the closed table, so their cardinality is fixed by that table and
#: cannot grow; an operator's ``labels_max_cardinality`` is about the
#: metrics the LOOP declares, and a tight one must not make the registry
#: itself unconstructable — least of all by refusing the counter that
#: reports a swallowed failure.
_OWN_CEILING = DEFAULT_LABELS_MAX_CARDINALITY

_log = get_logger("metrics")


@dataclass(frozen=True)
class Reading:
    """One series of one metric, as an exporter needs to see it (§5.11.3).

    :func:`readings` is the only thing that builds one, so the rule that
    decides ``family`` has a single owner: an exporter that re-derived it
    from the name would be the second copy of the declaration rule, and
    the first one to change would export a counter as a gauge.

    Parameters
    ----------
    family : str
        A :data:`~dskit.production.vocab.METRIC_FAMILIES` member.
    name : str
        The declared metric name, unchanged.
    labels : dict
        ``{label name: value}``, as :func:`series_labels` reads them.
    value : int or float or dict
        The reading; a histogram's is its ``{count, sum, buckets}``.

    Examples
    --------
    ::

        reading = Reading("counter", "ticks_total", {"status": "decided"}, 3)
        reading.family   # 'counter'
    """

    family: str
    name: str
    labels: dict
    value: object


def _family_of(name, value):
    """Return the family a series belongs to, by the rule that declared it."""
    if isinstance(value, dict):
        return _HISTOGRAM
    return _COUNTER if name.endswith(COUNTER_SUFFIX) else _GAUGE


def readings(snapshot):
    """Return one :class:`Reading` per recorded series of a snapshot.

    Every exporter needs the same decomposition — which metric, which
    labels, which family, which value — so it lives here rather than once
    per pack.

    Parameters
    ----------
    snapshot : dict
        A :meth:`Metrics.snapshot` result.

    Returns
    -------
    tuple of Reading
        Sorted by metric name then series key. A declared metric with no
        recorded series contributes nothing: there is no reading to
        export.
    """
    return tuple(
        Reading(_family_of(name, value), name, series_labels(key), value)
        for name, series in sorted(snapshot.items())
        for key, value in sorted(series.items())
    )


def series_key(labels):
    """Return the series key a metric's label values select.

    The recipe has ONE owner because an exporter must read it back
    (§5.11.3): a pack that parsed ``status=decided`` with its own split
    would be the second copy of a rule, and the first one to change would
    silently export the wrong label. Label values come from closed tables
    or from :data:`RESERVED_LABEL_VALUE`, so neither separator can appear
    inside one.

    Parameters
    ----------
    labels : dict
        ``{label name: value}``; empty for an unlabelled metric.

    Returns
    -------
    str
        ``name=value`` pairs sorted by name and joined by ``,`` — ``""``
        when there are no labels.
    """
    return SERIES_JOIN.join(f"{name}{SERIES_PAIR}{labels[name]}" for name in sorted(labels))


def series_labels(key):
    """Return the labels a series key holds — the inverse of :func:`series_key`.

    Parameters
    ----------
    key : str
        A key as :meth:`Metrics.snapshot` reports it.

    Returns
    -------
    dict
        ``{label name: value}``; empty for an unlabelled metric's ``""``.
    """
    if not key:
        return {}
    return dict(pair.split(SERIES_PAIR, 1) for pair in key.split(SERIES_JOIN))


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
    for label in sorted(set(wanted) & set(vocab.UNBOUNDED_LABEL_FIELDS)):
        problems.append(
            f"{name}: {label!r} names an unbounded field and may never be a metric "
            "label — its full detail belongs in the ledger and report artifacts"
        )
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

    @abstractmethod
    def record(self, value, **labels):
        """Take one reading, however this family takes one.

        The three families record differently — a counter adds, a gauge
        replaces, a histogram bins — so a caller that already knows which
        it holds says ``inc``, ``set`` or ``observe``. A caller driven by
        a TABLE (``vocab.EVENT_READINGS``) does not, and this is the verb
        it says instead of asking.

        Parameters
        ----------
        value : int or float
            The reading, in the metric's base unit.
        **labels
            One value per declared label name.

        Returns
        -------
        None
            The reading was taken by this family's own rule.
        """

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
        admitted = {}
        for label in self._labels:
            value = labels[label]
            if not (isinstance(value, str) and value in self._allowed[label]):
                value = RESERVED_LABEL_VALUE
                self._registry._count_drop()
            admitted[label] = value
        key = series_key(admitted)
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

    def record(self, value, **labels):
        """Add ``value`` — a counter takes a reading by incrementing.

        Parameters
        ----------
        value : int or float
            The increment, ``>= 0``.
        **labels
            One value per declared label name.

        Returns
        -------
        None
            As for :meth:`inc`, which this is.
        """
        self.inc(value, **labels)


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

    def record(self, value, **labels):
        """Set the series to ``value`` — a gauge takes a reading by replacing.

        Parameters
        ----------
        value : int or float
            The reading.
        **labels
            One value per declared label name.

        Returns
        -------
        None
            As for :meth:`set`, which this is.
        """
        self.set(value, **labels)


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

    def record(self, value, **labels):
        """Observe ``value`` — a histogram takes a reading by binning it.

        Parameters
        ----------
        value : int or float
            The observation, in the metric's base unit.
        **labels
            One value per declared label name.

        Returns
        -------
        None
            As for :meth:`observe`, which this is.
        """
        self.observe(value, **labels)


class EventCatalogue:
    """The versioned event schema: which categories, which fields, what is safe.

    Replay and paper production differ in feed, clock, executor and
    account source and in nothing else, so the body they record has to be
    ONE shape or a divergence between them is unfalsifiable. This is that
    shape, read from :data:`~dskit.production.vocab.EVENT_FIELDS` and
    stamped with :data:`~dskit.production.vocab.EVENT_SCHEMA_VERSION`.

    Validation is default-deny on NAMES and permissive on ABSENCE: an
    unknown category or field is a problem, because it is a value nobody
    can read back, while a missing one is not, because no single event
    reaches every phase — a tick that never got to a solver has no ``mio``
    reading, and demanding one would make the schema lie.

    Parameters
    ----------
    None
        The catalogue is the vocabulary's; there is nothing to configure.

    Examples
    --------
    ::

        catalogue = EventCatalogue()
        catalogue.validate({"data": {"stale_age": 4.0}})   # []
        catalogue.label_safe("symbol")   # False
    """

    def __init__(self):
        self._fields = {
            category: frozenset(fields) for category, fields in vocab.EVENT_FIELDS.items()
        }

    @property
    def version(self):
        """Return the schema version a stamped body carries."""
        return vocab.EVENT_SCHEMA_VERSION

    @property
    def categories(self):
        """Return the catalogue's categories, in catalogue order."""
        return vocab.EVENT_CATEGORIES

    def fields(self, category):
        """Return the field names one category declares.

        Parameters
        ----------
        category : str
            A :data:`~dskit.production.vocab.EVENT_CATEGORIES` member.

        Returns
        -------
        tuple of str
            The declared fields, in catalogue order.

        Raises
        ------
        ProductionError
            If ``category`` is not a declared category.
        """
        if category not in self._fields:
            raise ProductionError(
                [f"{category!r} is not an event category — {list(self.categories)}"]
            )
        return vocab.EVENT_FIELDS[category]

    def label_safe(self, field):
        """Return whether a catalogue field may become a telemetry label.

        Parameters
        ----------
        field : str
            A catalogue field name.

        Returns
        -------
        bool
            ``False`` for a
            :data:`~dskit.production.vocab.UNBOUNDED_LABEL_FIELDS` member,
            whose value set no vocabulary bounds; ``True`` otherwise.
        """
        return field not in vocab.UNBOUNDED_LABEL_FIELDS

    def validate(self, body):
        """Return every problem with a candidate body; empty when it fits.

        Parameters
        ----------
        body : dict
            ``{category: {field: value}}``; ``schema_version`` is allowed
            beside the categories so a stamped body validates unchanged.

        Returns
        -------
        list of str
            One problem per unknown category, non-mapping category, and
            unknown field.
        """
        problems = []
        if not isinstance(body, dict):
            return [f"an event body is a mapping, got {type(body).__name__}"]
        for category, fields in body.items():
            if category == _SCHEMA_VERSION:
                continue
            if category not in self._fields:
                problems.append(f"{category!r} is not an event category")
                continue
            if not isinstance(fields, dict):
                problems.append(f"{category}: a category holds a mapping of fields")
                continue
            for field in sorted(set(fields) - self._fields[category]):
                problems.append(f"{category}.{field} is not a catalogue field")
        return problems

    def stamp(self, fields):
        """Return the event body ``fields`` becomes once it names its version.

        Parameters
        ----------
        fields : dict
            ``{category: {field: value}}``.

        Returns
        -------
        dict
            A new mapping carrying ``schema_version`` beside the
            categories; the caller's mapping is not modified.
        """
        return {_SCHEMA_VERSION: self.version, **fields}


class EventAdapter(ABC):
    """The seam that turns one layer's own records into a catalogue event.

    A domain knows what a bar, a lead and a cap are; the catalogue knows
    what an event is. This is where the two meet, and it is deliberately
    the ONLY place: a subclass supplies :meth:`fields` and gets stamping
    and validation for free, so an adapter cannot invent a field, skip the
    version, or emit a body a reader has no schema for.

    Replay and paper production share one adapter instance or two of the
    same class; either way the body is identical by construction rather
    than by review, which is what makes a replay-versus-paper divergence a
    real finding.

    ``cls(params)`` construction, default-deny over the subclass's
    ``_PARAMS`` plus ``notes``.

    Parameters
    ----------
    params : dict, optional
        The adapter's own knobs; ``None`` means ``{}``.

    Examples
    --------
    An adapter over a record that already speaks the catalogue's names::

        class PassThrough(EventAdapter):
            def fields(self, record):
                return {"data": {"stale_age": record["age_s"]}}

        adapter = PassThrough({})
        adapter.event({"age_s": 4.0})
        # -> {'schema_version': 1, 'data': {'stale_age': 4.0}}
    """

    _PARAMS = ()

    def __init__(self, params=None):
        params = dict(params or {})
        problems = self.validate_params(params)
        if problems:
            raise ProductionError(problems)
        self._catalogue = EventCatalogue()
        self._configure(params)

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when it is acceptable.

        Parameters
        ----------
        params : dict
            The params block as written by the caller.

        Returns
        -------
        list of str
            One problem per unknown key; subclasses extend the list.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + _NOTES)
        return problems

    def _configure(self, params):
        """Read validated params; the base has none to read."""

    @property
    def catalogue(self):
        """Return the catalogue this adapter validates and stamps against."""
        return self._catalogue

    @abstractmethod
    def fields(self, record):
        """Return the catalogue fields one of this layer's records carries.

        Parameters
        ----------
        record : object
            Whatever the layer records — a §6 body, a solver result, a
            fill. The subclass knows; the catalogue does not.

        Returns
        -------
        dict
            ``{category: {field: value}}``, using catalogue names only.
            A category the record says nothing about is left out.
        """

    def event(self, record):
        """Return the stamped, validated event body for one record.

        Parameters
        ----------
        record : object
            As :meth:`fields` takes it.

        Returns
        -------
        dict
            The body, carrying ``schema_version`` and the categories the
            adapter supplied.

        Raises
        ------
        ProductionError
            If :meth:`fields` returned a category or a field the catalogue
            does not declare — a value nobody could read back.
        """
        fields = self.fields(record)
        problems = self._catalogue.validate(fields)
        if problems:
            raise ProductionError(problems)
        return self._catalogue.stamp(fields)


class EventReadings:
    """The generic reducer: a catalogue event in, the safe aggregates out.

    :data:`~dskit.production.vocab.EVENT_READINGS` binds each exported
    series to exactly ONE catalogue field, so a reading has a single owner
    on both sides and this class holds no rule of its own — it declares
    what the table says and records what the event carries. The full
    per-symbol, per-lead detail stays in the event body and in the ledger
    and report artifacts built from it; an exporter sees only what the
    table bounds.

    It records and stops there. No threshold is compared, no verdict is
    reached and no alert is raised: those wait on a ruling this class must
    not pre-empt.

    Parameters
    ----------
    metrics : Metrics
        The registry to declare on; every binding is declared at
        construction, so a name the table carries and no event ever fills
        is visibly zero rather than absent.
    catalogue : EventCatalogue or None, optional
        The schema to read fields through; ``None`` builds one.

    Examples
    --------
    ::

        registry = Metrics()
        readings = EventReadings(registry)
        readings.record({"schema_version": 1, "data": {"stale_age": 12.0}})
        registry.snapshot()["stale_age_seconds"]   # {'': 12.0}
    """

    def __init__(self, metrics, catalogue=None):
        self._catalogue = catalogue if catalogue is not None else EventCatalogue()
        self._bound = {}
        for name, (category, field, family) in vocab.EVENT_READINGS.items():
            declare = _DECLARE[family]
            labels = tuple(vocab.METRIC_LABEL_VALUES[name])
            self._bound[name] = (category, field, declare(metrics, name, labels))

    def record(self, event):
        """Record every bound field the event carries; ignore what it omits.

        Parameters
        ----------
        event : dict
            A body as :meth:`EventAdapter.event` builds one.

        Returns
        -------
        None
            Each present field was recorded through its metric's own
            family verb; an absent one contributes nothing, because a
            phase that did not happen is not a reading of zero.
        """
        for category, field, handle in self._bound.values():
            fields = event.get(category)
            if isinstance(fields, dict) and field in fields:
                self._apply(handle, fields[field])

    def _apply(self, handle, value):
        """Record one field: a mapping per label value, or a bare number."""
        if not handle.labels:
            handle.record(value)
            return
        label = handle.labels[0]
        for label_value, number in value.items():
            handle.record(number, **{label: label_value})


class MetricSink(ABC):
    """The exporter seam (§5.11.3): ``publish(snapshot, at_ms)``, and nothing else.

    An exporter is told what the registry holds and hands it on. It is
    never asked a question, never consulted on the hot path, and never
    allowed to fail a tick: :meth:`Metrics.flush` swallows whatever
    ``publish`` raises and counts it under
    ``metric_sink_failures_total{sink}``, the same rule a JSONL flush that
    cannot write already gets. That is what lets a pack reach a network
    from inside a serve process at all.

    ``cls(params)`` construction, default-deny over the subclass's
    ``_PARAMS`` plus ``notes``. Subclasses live in ``libs/`` and name
    their library only inside a method, so importing one costs nothing.

    Parameters
    ----------
    params : dict, optional
        The ``placement.metric_sinks.<name>.params`` block; ``None`` means
        ``{}``.

    Attributes
    ----------
    KIND : str or None
        The registry name a core kind reports; ``None`` on the ABC, so a
        child exporter reports its class reference and its failures fall
        to the reserved label value by the ordinary cardinality rule.

    Examples
    --------
    An exporter that keeps the last reading in memory::

        class LastReading(MetricSink):
            def publish(self, snapshot, at_ms):
                self.last = (snapshot, at_ms)

        sink = LastReading({})
        sink.publish({"ticks_total": {"status=decided": 1}}, 1_767_225_600_000)
        sink.kind   # 'tests...:LastReading' — its class reference
    """

    _PARAMS = ()
    KIND = None

    def __init__(self, params=None):
        params = dict(params or {})
        problems = self.validate_params(params)
        if problems:
            raise ProductionError(problems)
        self._configure(params)

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when it is acceptable.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            One problem per unknown key; subclasses extend the list.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + _NOTES)
        return problems

    def _configure(self, params):
        """Read validated params; the base has none to read."""

    @property
    def kind(self):
        """Return the registry name of a core kind, else the class reference."""
        return self.KIND if self.KIND is not None else class_ref(type(self))

    @abstractmethod
    def publish(self, snapshot, at_ms):
        """Hand one flush's readings to whatever this exporter exports to.

        Parameters
        ----------
        snapshot : dict
            ``{name: {series key: value}}`` — the caller's own copy, free
            to keep.
        at_ms : int
            The instant the loop told the registry to flush at; never read
            from a clock here.

        Returns
        -------
        None
            The answer is the export; a failure is raised and swallowed.
        """

    def close(self):
        """Release whatever this exporter holds open.

        Concrete because most exporters hold nothing: a pull endpoint and
        a stateless push both have nothing to close, and a hook that
        raised would make them all write an empty override.

        Returns
        -------
        None
            Nothing is held by default.
        """


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
        self._sinks = []
        self._dropped = self._declare(Counter, _DROPPED, (), None, _OWN_CEILING)
        self._sink_failures = self._declare(
            Counter, _SINK_FAILURES, ("sink",), None, _OWN_CEILING
        )

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

    def _declare(self, handle_cls, name, labels, buckets, ceiling=None):
        """Validate against the table, then create or hand back the one handle."""
        problems = []
        labels = _declared_labels(
            problems, handle_cls, name, labels,
            self._max_cardinality if ceiling is None else ceiling,
        )
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

    def subscribe(self, sink):
        """Register an exporter to be published to on every flush (§5.11.3).

        Parameters
        ----------
        sink : MetricSink
            The exporter; ``compose.py`` builds one per
            ``placement.metric_sinks`` entry.

        Returns
        -------
        MetricSink
            The sink, so a caller may keep the handle it just registered.

        Raises
        ------
        ProductionError
            If ``sink`` is not a :class:`MetricSink`, or is already
            subscribed — publishing to one exporter twice per flush would
            double every counter it exports.
        """
        if not isinstance(sink, MetricSink):
            raise ProductionError([f"a metric sink must be a MetricSink, got {sink!r}"])
        if any(known is sink for known in self._sinks):
            raise ProductionError([f"{sink.kind} is already subscribed"])
        self._sinks.append(sink)
        return sink

    def close(self):
        """Close every subscriber, swallowing and counting what each raises.

        Returns
        -------
        None
            A close is best-effort by the same argument a publish is: an
            exporter that cannot let go must not stop a process from
            shutting down.
        """
        for sink in self._sinks:
            self._attempt(sink, sink.close)

    def _publish(self, at_ms):
        """Hand a fresh snapshot to every subscriber, swallowing each failure."""
        for sink in self._sinks:
            self._attempt(sink, lambda sink=sink: sink.publish(self.snapshot(), at_ms))

    def _attempt(self, sink, call):
        """Run one exporter call; count and log whatever it raises."""
        try:
            call()
        except Exception as error:  # noqa: BLE001 — an exporter may raise anything
            self._sink_failures.inc(sink=sink.kind)
            _log.warning("metric sink %s failed: %s", sink.kind, error)

    def flush(self, at_ms, tick_id):
        """Publish to every subscriber, then append one line to the flush file.

        Counters are cumulative: a flush is a reading, not a reset. A
        failure to write is counted in ``flush_failures``, logged and
        swallowed — it can never fail a tick — and so is a failure to
        publish, counted under ``metric_sink_failures_total{sink}``. The
        two halves are independent: a document may declare exporters and
        no ``log_dir``, or a ``log_dir`` and no exporters, and neither
        half may silence the other. Publishing happens FIRST so a sink
        failure appears in the same line it happened on rather than a tick
        later.

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
            or when the write failed. What the exporters did is in their
            own counter, not in this answer.
        """
        self._publish(at_ms)
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


#: Which :class:`Metrics` verb declares which family — a table keyed by the
#: declared family, so :class:`EventReadings` walks
#: :data:`~dskit.production.vocab.EVENT_READINGS` without ever asking what
#: it is holding. Defined here because it names ``Metrics`` itself.
_DECLARE = {
    _COUNTER: Metrics.counter,
    _GAUGE: Metrics.gauge,
    _HISTOGRAM: Metrics.histogram,
}

#: The metric-exporter family's open doorway (§4.3, §5.11.3): a registered
#: name or a ``pkg.module:Class`` reference, both subclasses of
#: :class:`MetricSink`. Core registers NOTHING into it — an exporter is a
#: library, and this package may import none — so every member arrives with
#: a tier-2 pack (``libs/prometheus.py``, ``libs/opentelemetry.py``) or a
#: child's own class. It is a third sink registry, separate from the
#: pipeline's tracking sinks and from ``ALERT_SINK_KINDS``, because a
#: document that could select an alert sink as an exporter would deliver
#: pages to a metrics endpoint.
METRIC_SINK_KINDS = Registry("metric_sink", MetricSink)
