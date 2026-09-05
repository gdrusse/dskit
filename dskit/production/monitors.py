"""``monitors.py`` — the watch over the decision stream (§5.10, D16).

A monitor is not a guard measure (§5.15). A measure answers about ONE
proposal at decision time; a monitor answers about a WINDOW of decisions
and ticks the ledger has already recorded, and its answer may be
``insufficient`` — "I cannot say yet" — which D16 keeps as a first-class
status because a drift monitor that says ``ok`` from thirty samples is
worse than one that says nothing. Two rules carry that weight and every
family honours them through the one ``_judge`` on the base: below
``min_n`` a verdict is never ``ok``, and the trailing partial chunk is
never ``ok``.

The parts a document selects are strategy objects behind three
registries (§4.3). A ``Reference`` is the population a window is compared
against: ``leading`` freezes the first ``n`` values as a fixed anchor,
``rolling`` keeps the last ``window`` values, ``snapshot`` reads a saved
``Profile``, and a monitor may declare several and keep them all. A
``Chunker`` cuts observations into windows (``count``, ``sliding``,
``period``). A ``Threshold`` turns a statistic into one bit (``constant``,
``reference_std``, ``alpha``). ``response`` is not a strategy: it is the
closed vocabulary ``RESPONSES`` and says what the loop does with a breach.
The monitor only reports — an ``alarm``, which is a breach under
``response: halt``, is what trips the breaker, and ``should_trip`` says
exactly that.

Readings taken where §5.10 is silent, each reported to the orchestrator:
a breach is ``alarm`` under ``halt`` and ``warn`` otherwise; references are
fed by ``fit`` and by observations as they LEAVE the current window, so a
window is never part of its own reference; a distribution monitor with
several references reports the one that drifted most; a ``period`` window
is judged as it fills, because nothing but a clock could close it and no
monitor holds one; a ``snapshot`` reference reconstructs a bin-midpoint
sample from its profile because the ``Reference`` seam speaks in samples;
``PageHinkley`` resets its recursion on the observation after an alarm.
Every statistic is stdlib arithmetic — ``statistics.NormalDist`` for the
χ² benchmark, ``math`` for the Kolmogorov series — and nothing here reads
a clock, so the same tape always yields the same verdict.
"""

import bisect
import copy
import json
import math
import statistics
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dskit.pipeline.node import check_int_param
from dskit.pipeline.records import number_ok
from dskit.production.base import (
    ProductionError,
    Registry,
    _check_dict,
    _check_str,
    _check_unknown,
    reject_unknown_params,
)
from dskit.production.records import Verdict
from dskit.production.release import parse_iso_duration
from dskit.production.vocab import MONITOR_STATUSES, RESPONSES, SIDES, TICK_STATUSES

__all__ = [
    "ALL_SLICE",
    "Alpha",
    "CHUNKER_KINDS",
    "Chunker",
    "Constant",
    "Count",
    "Coverage",
    "DEFAULT_BINS",
    "DEFAULT_DELTA",
    "DEFAULT_MIN_N",
    "DEFAULT_PERCENTILE",
    "DEFAULT_RESPONSE",
    "DEFAULT_STEP",
    "DEFAULT_TIME_FIELD",
    "DecisionRate",
    "DistributionMonitor",
    "KS",
    "LatencyPercentiles",
    "Leading",
    "MONITOR_KINDS",
    "Monitor",
    "OperationalMonitor",
    "PSI",
    "PSI_EPSILON",
    "PageHinkley",
    "Period",
    "Profile",
    "REFERENCE_KINDS",
    "Reference",
    "ReferenceStd",
    "RefusalCount",
    "Rolling",
    "Sliding",
    "Snapshot",
    "Staleness",
    "StreamMonitor",
    "THRESHOLD_KINDS",
    "Threshold",
    "TrackingSignal",
]

#: The fewest observations a window — and values a reference — needs
#: before a verdict may be anything but ``insufficient`` (D16). A document
#: overrides it per monitor with ``min_n``.
DEFAULT_MIN_N = 30

#: What the loop does with a breach when a monitor declares no
#: ``response``: it is logged — the least a breach can do. ``halt`` must be
#: written down.
DEFAULT_RESPONSE = "log"

#: ``latency``'s default percentile (p95) and ``psi``'s default bin count
#: (deciles, the classic PSI binning).
DEFAULT_PERCENTILE = 0.95
DEFAULT_BINS = 10

#: ``page_hinkley``'s default tolerance δ — the magnitude of change the
#: recursion ignores.
DEFAULT_DELTA = 0.005

#: ``sliding``'s default step and ``period``'s default time field (the
#: §6 tick record's data instant).
DEFAULT_STEP = 1
DEFAULT_TIME_FIELD = "data_asof_ms"

#: The floor a zero bin proportion is lifted to so PSI stays finite when a
#: window misses a reference bin entirely — a numerical guard, not a knob.
PSI_EPSILON = 1e-4

#: The one slice phase 1 reports; per-key slices are phase-2 work.
ALL_SLICE = "all"

# Vocabulary members by name, unpacked from the closed sets so a reorder
# in vocab.py fails here at import instead of drifting silently.
_OK, _WARN, _ALARM, _INSUFFICIENT = MONITOR_STATUSES
_LOG, _WARN_RESPONSE, _HALT = RESPONSES

#: A breach's status per declared response: only ``halt`` earns ``alarm``.
_BREACH_STATUS = {_LOG: _WARN, _WARN_RESPONSE: _WARN, _HALT: _ALARM}

#: ``SIDES``' abstaining member and ``TICK_STATUSES``' decided member, by
#: their pinned positions.
_ABSTAIN = SIDES[-1]
_DECIDED = TICK_STATUSES[0]

_NOTES = ("notes",)
_KIND = "kind"
_USES = "uses"
_PARAMS_KEY = "params"
_LEGS = "legs"
_VALUE = "value"
_TARGET = "target"


# ---------------------------------------------------------------------------
# Small rules with one owner in this module
# ---------------------------------------------------------------------------


def _number(value, path):
    """Return ``value`` when it is a finite number (a Decimal widens); refuse otherwise."""
    if isinstance(value, Decimal) and value.is_finite():
        return float(value)
    if number_ok(value):
        return value
    raise ProductionError([f"{path}: expected a finite number, got {value!r}"])


def _check_number(problems, name, value, *, ge=None, gt=None, lt=None):
    """Append a problem unless ``value`` is a finite number inside the stated bounds."""
    if not number_ok(value):
        problems.append(f"{name} must be a finite number, got {value!r}")
        return
    bounds = []
    if ge is not None and value < ge:
        bounds.append(f">= {ge}")
    if gt is not None and value <= gt:
        bounds.append(f"> {gt}")
    if lt is not None and value >= lt:
        bounds.append(f"< {lt}")
    if bounds:
        problems.append(f"{name} must be {' and '.join(bounds)}, got {value!r}")


def _prefixed(problems, name, inner):
    """Append every problem in ``inner`` to ``problems``, prefixed with the site name."""
    problems.extend(f"{name}: {problem}" for problem in inner)


def _resolve(problems, name, registry, uses):
    """Resolve ``uses`` through ``registry`` for the site ``name``; None on a problem."""
    try:
        return registry.resolve(uses)
    except ProductionError as exc:
        _prefixed(problems, name, exc.problems)
        return None


def _kind_site(problems, name, site, registry):
    """Read a ``{"kind": k, ...}`` selector site as ``(class, params)``; None on a problem."""
    if not isinstance(site, dict) or not isinstance(site.get(_KIND), str):
        problems.append(
            f'{name} must be {{"kind": <{registry.family} kind>, ...}} with kind one of '
            f"{list(registry.kinds())} or a pkg.module:Class reference, got {site!r}"
        )
        return None
    cls = _resolve(problems, name, registry, site[_KIND])
    if cls is None:
        return None
    params = {key: value for key, value in site.items() if key != _KIND}
    _prefixed(problems, name, cls.validate_params(params))
    return cls, params


def _uses_site(problems, name, site, registry):
    """Read a ``{"uses": u, "params": {...}}`` site as ``(class, params)``; None on a problem."""
    if not isinstance(site, dict) or not isinstance(site.get(_USES), str):
        problems.append(
            f'{name} must be {{"uses": <{registry.family} kind>, "params": {{...}}}} '
            f"with uses one of {list(registry.kinds())} or a pkg.module:Class "
            f"reference, got {site!r}"
        )
        return None
    inner = []
    reject_unknown_params(inner, site, (_USES, _PARAMS_KEY) + _NOTES)
    params = site.get(_PARAMS_KEY) or {}
    _check_dict(inner, _PARAMS_KEY, params)
    _prefixed(problems, name, inner)
    cls = _resolve(problems, name, registry, site[_USES]) if not inner else None
    if cls is None:
        return None
    _prefixed(problems, name, cls.validate_params(params))
    return cls, params


def _reference_sites(problems, site):
    """Read the ``reference`` knob — one ``{uses, params}`` site or a list — as ``(class, params)`` pairs."""
    if site is None:
        problems.append("reference is required: one {uses, params} site or a list of them")
        return ()
    sites = site if isinstance(site, list) else [site]
    if not sites:
        problems.append("reference must name at least one {uses, params} site")
        return ()
    resolved = [_uses_site(problems, "reference", member, REFERENCE_KINDS) for member in sites]
    return tuple(pair for pair in resolved if pair is not None)


def _quantile_edges(values, bins):
    """Return the ``bins - 1`` interior quantile cut points of ``values`` (none below two values)."""
    if len(values) < 2 or bins < 2:
        return []
    return statistics.quantiles(values, n=bins)


def _bin_counts(values, interior):
    """Count ``values`` into the bins the interior cut points delimit (a cut point belongs above)."""
    counts = [0] * (len(interior) + 1)
    for value in values:
        counts[bisect.bisect_right(interior, value)] += 1
    return counts


def _percentile(values, fraction):
    """Return the nearest-rank percentile of ``values`` — an observed value, never interpolated."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _psi(reference_counts, window_counts):
    """Return Σ (q − p)·ln(q / p) over matching bins, proportions floored at ``PSI_EPSILON``."""
    n_ref, n_cur = sum(reference_counts), sum(window_counts)
    total = 0.0
    for ref, cur in zip(reference_counts, window_counts):
        p = max(ref / n_ref, PSI_EPSILON)
        q = max(cur / n_cur, PSI_EPSILON)
        total += (q - p) * math.log(q / p)
    return total


def _ks(reference, window):
    """Return the largest gap between the two samples' empirical distribution functions."""
    ref, cur = sorted(reference), sorted(window)
    gap = 0.0
    for point in sorted(set(ref) | set(cur)):
        ref_cdf = bisect.bisect_right(ref, point) / len(ref)
        cur_cdf = bisect.bisect_right(cur, point) / len(cur)
        gap = max(gap, abs(ref_cdf - cur_cdf))
    return gap


def _top(values, top_k):
    """Return the ``top_k`` most frequent values as ``[value, count]`` lists, ties by value."""
    ranked = sorted(
        Counter(values).items(), key=lambda pair: (-pair[1], type(pair[0]).__name__, pair[0])
    )
    return [[value, count] for value, count in ranked[:top_k]]


class _Configured(ABC):
    """``cls(params)`` construction: default-deny over ``_PARAMS``, validate, configure."""

    _PARAMS = ()

    def __init__(self, params=None):
        params = dict(params or {})
        problems = self.validate_params(params)
        if problems:
            raise ProductionError(problems)
        self._configure(params)

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when it is acceptable.

        The base refuses any key outside ``_PARAMS`` and ``notes``, then
        asks the class's own ``_check`` hook; nothing here raises.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending key.
        """
        problems = []
        reject_unknown_params(problems, params, tuple(cls._PARAMS) + _NOTES)
        cls._check(problems, params)
        return problems

    @classmethod
    def _check(cls, problems, params):
        """Append this class's own problems with ``params``; the base has none."""

    def _configure(self, params):
        """Read validated params; the base has none to read."""


# ---------------------------------------------------------------------------
# Reference — the comparison population
# ---------------------------------------------------------------------------


class Reference(_Configured, ABC):
    """The population a window is compared against (§5.10).

    A reference is fed twice: by the owning monitor's ``fit`` (the
    training-time population) and, one value at a time through ``add``,
    by each observation that has LEFT the monitor's current window. So the
    window under judgement is never part of its own reference — a rolling
    reference that contained the window it is compared against would
    report no drift, ever.

    Parameters
    ----------
    params : dict
        The site's ``params`` — the subclass's ``_PARAMS`` plus ``notes``.
    field : str or None, keyword-only
        The owning monitor's field, supplied by the monitor at
        construction; only ``snapshot`` reads it, to pick a profile.

    Examples
    --------
    An anchor frozen at the first three values it is offered::

        anchor = Leading({"n": 3})
        anchor.fit([1.0, 2.0, 3.0, 4.0])
        anchor.sample()  # (1.0, 2.0, 3.0)
    """

    def __init__(self, params=None, *, field=None):
        self._field = field
        self._values = []
        super().__init__(params)

    def fit(self, values):
        """Prime the population with training-time values.

        Parameters
        ----------
        values : iterable of number
            The monitor's field, read from the records it was fitted on.

        Returns
        -------
        None
            Each value is offered through ``add``.
        """
        for value in values:
            self.add(value)

    def add(self, value):
        """Offer one value that has left the monitor's window; the base keeps every value.

        Parameters
        ----------
        value : number
            The observation's value.

        Returns
        -------
        None
            The population grew by one.
        """
        self._values.append(value)

    @abstractmethod
    def sample(self):
        """Return the comparison population.

        Returns
        -------
        tuple of number
            Every value the reference currently stands for; empty before
            it has been primed.
        """

    def state(self):
        """Return the population as a JSON-able dict for the §6 snapshot.

        Returns
        -------
        dict
            ``{"values": [...]}``.
        """
        return {"values": list(self._values)}

    def restore(self, state):
        """Take the population back from a ``state()`` payload.

        Parameters
        ----------
        state : dict
            Exactly the ``state()`` shape.

        Returns
        -------
        None
            The population is replaced.

        Raises
        ------
        ProductionError
            On an unknown or missing key, or a non-list ``values``.
        """
        problems = []
        _check_dict(problems, "reference state", state)
        if not problems:
            _check_unknown(problems, state, ("values",), where="reference state")
            if not isinstance(state.get("values"), list):
                problems.append("reference state: values must be a list")
        if problems:
            raise ProductionError(problems)
        self._values = list(state["values"])


class Leading(Reference):
    """The fixed anchor: the first ``n`` values, then frozen (D16).

    Parameters
    ----------
    params : dict
        ``n`` (int >= 1, required): how many values the anchor keeps.

    Examples
    --------
    ::

        anchor = Leading({"n": 2})
        for value in (1.0, 2.0, 99.0):
            anchor.add(value)
        anchor.sample()  # (1.0, 2.0)
    """

    _PARAMS = ("n",)

    @classmethod
    def _check(cls, problems, params):
        """Require a positive anchor size."""
        check_int_param(problems, "n", params.get("n"), ge=1)

    def _configure(self, params):
        """Take the anchor size."""
        self._n = int(params["n"])

    def add(self, value):
        """Keep ``value`` only while the anchor is still filling.

        Parameters
        ----------
        value : number
            The observation's value.

        Returns
        -------
        None
            Ignored once ``n`` values are held.
        """
        if len(self._values) < self._n:
            self._values.append(value)

    def sample(self):
        """Return the frozen head.

        Returns
        -------
        tuple of number
            The first ``n`` values offered, fewer while filling.
        """
        return tuple(self._values)


class Rolling(Reference):
    """The recent past: the last ``window`` values, always moving (D16).

    Parameters
    ----------
    params : dict
        ``window`` (int >= 1, required): how many trailing values to keep.

    Examples
    --------
    ::

        recent = Rolling({"window": 2})
        for value in (1.0, 2.0, 3.0):
            recent.add(value)
        recent.sample()  # (2.0, 3.0)
    """

    _PARAMS = ("window",)

    @classmethod
    def _check(cls, problems, params):
        """Require a positive window."""
        check_int_param(problems, "window", params.get("window"), ge=1)

    def _configure(self, params):
        """Take the window length."""
        self._window = int(params["window"])

    def add(self, value):
        """Append ``value`` and drop whatever fell out of the window.

        Parameters
        ----------
        value : number
            The observation's value.

        Returns
        -------
        None
            The population holds at most ``window`` values.
        """
        self._values.append(value)
        del self._values[: -self._window]

    def sample(self):
        """Return the trailing window.

        Returns
        -------
        tuple of number
            The last ``window`` values offered.
        """
        return tuple(self._values)


class Snapshot(Reference):
    """A saved ``Profile`` on disk, read at ``fit`` and never at construction.

    The ``Reference`` seam speaks in samples and a profile holds bins, so
    the population this reference stands for is reconstructed from the
    profile's quantile bins — each bin contributes ``count`` copies of its
    midpoint — which reproduces the profile's bin proportions exactly and
    approximates everything finer.

    Parameters
    ----------
    params : dict
        ``path`` (str, required): the JSON file holding ``Profile.to_obj()``.
    field : str, keyword-only
        The owning monitor's field; the profile must carry a summary for it.

    Examples
    --------
    ::

        reference = Snapshot({"path": "profiles/train.json"}, field="prediction")
        reference.fit(())  # reads the file now; a missing file refuses here
        len(reference.sample())  # the profile's count for "prediction"
    """

    _PARAMS = ("path",)

    @classmethod
    def _check(cls, problems, params):
        """Require a path; the file is not touched until ``fit``."""
        _check_str(problems, "path", params.get("path"))

    def _configure(self, params):
        """Take the path."""
        self._path = params["path"]

    def add(self, value):
        """Ignore ``value``: a saved population never moves.

        Parameters
        ----------
        value : number
            Unused.

        Returns
        -------
        None
            Nothing changes.
        """

    def fit(self, values):
        """Read the profile and take its bins for the monitor's field as the population.

        Parameters
        ----------
        values : iterable of number
            Unused — the file is the population.

        Returns
        -------
        None
            ``sample()`` now reproduces the profile's bin proportions.

        Raises
        ------
        ProductionError
            If the file is missing or not a profile, the monitor supplied
            no field, or the profile has no bins for that field.
        """
        try:
            obj = json.loads(Path(self._path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ProductionError([f"snapshot reference: {self._path} is missing"]) from None
        except (OSError, ValueError) as exc:
            raise ProductionError([f"snapshot reference: {self._path} is unreadable: {exc}"]) from exc
        profile = Profile.from_obj(obj)
        if self._field is None:
            raise ProductionError(["snapshot reference needs the owning monitor's field"])
        summary = profile.fields.get(self._field)
        if summary is None:
            raise ProductionError(
                [f"snapshot reference: {self._path} has no profile for field {self._field!r}"]
            )
        edges, counts = summary["bins"]["edges"], summary["bins"]["counts"]
        if not edges:
            raise ProductionError(
                [f"snapshot reference: {self._path} has no bins for field {self._field!r}"]
            )
        self._values = [
            (edges[index] + edges[index + 1]) / 2
            for index, count in enumerate(counts)
            for _ in range(count)
        ]

    def sample(self):
        """Return the reconstructed population.

        Returns
        -------
        tuple of number
            One bin midpoint per profiled value; empty before ``fit``.
        """
        return tuple(self._values)


# ---------------------------------------------------------------------------
# Chunker — how observations are cut into windows
# ---------------------------------------------------------------------------


class Chunker(_Configured, ABC):
    """How a monitor's observations are cut into windows (§5.10).

    ``chunks`` yields every window in order; the monitor judges the LAST
    one, the current window, and asks ``complete`` whether it is full —
    a trailing partial chunk is never ``ok`` (D16). ``observation_fields``
    names the record fields the monitor must carry into each observation
    for this chunker to place it; only ``period`` needs any.

    Parameters
    ----------
    params : dict
        The ``window`` site minus its ``kind`` — the subclass's ``_PARAMS``
        plus ``notes``.

    Examples
    --------
    Disjoint pairs, the last one partial::

        chunker = Count({"n": 2})
        tuple(chunker.chunks([1, 2, 3]))  # ((1, 2), (3,))
        chunker.complete((3,))  # False
    """

    @abstractmethod
    def chunks(self, records):
        """Cut ``records`` into windows.

        Parameters
        ----------
        records : sequence
            The observations, oldest first.

        Returns
        -------
        iterator of tuple
            Each window in order; the last is the current one.
        """

    def complete(self, chunk):
        """Say whether ``chunk`` is a full window; the base calls every chunk full.

        Parameters
        ----------
        chunk : tuple
            A window ``chunks`` yielded.

        Returns
        -------
        bool
            False only when the chunker knows the window is still filling.
        """
        return True

    def label(self):
        """Return the window label a verdict reports.

        Returns
        -------
        str
            ``kind:argument`` for the core kinds; the class name otherwise.
        """
        return type(self).__name__.lower()

    def observation_fields(self):
        """Return the record fields an observation must carry for this chunker.

        Returns
        -------
        tuple of str
            Empty for a count-based chunker.
        """
        return ()


class Count(Chunker):
    """Disjoint windows of ``n`` observations; the trailing partial one is yielded too.

    Parameters
    ----------
    params : dict
        ``n`` (int >= 1, required): the window size.

    Examples
    --------
    ::

        tuple(Count({"n": 2}).chunks([1, 2, 3, 4, 5]))  # ((1, 2), (3, 4), (5,))
    """

    _PARAMS = ("n",)

    @classmethod
    def _check(cls, problems, params):
        """Require a positive window size."""
        check_int_param(problems, "n", params.get("n"), ge=1)

    def _configure(self, params):
        """Take the window size."""
        self._n = int(params["n"])

    def chunks(self, records):
        """Cut ``records`` into consecutive windows of ``n``.

        Parameters
        ----------
        records : sequence
            The observations, oldest first.

        Returns
        -------
        iterator of tuple
            Full windows, then whatever is left.
        """
        for start in range(0, len(records), self._n):
            yield tuple(records[start : start + self._n])

    def complete(self, chunk):
        """Say whether ``chunk`` holds ``n`` observations.

        Parameters
        ----------
        chunk : tuple
            A window ``chunks`` yielded.

        Returns
        -------
        bool
            False for the trailing partial window.
        """
        return len(chunk) >= self._n

    def label(self):
        """Return ``count:<n>``.

        Returns
        -------
        str
            The label a verdict reports.
        """
        return f"count:{self._n}"


class Sliding(Chunker):
    """Overlapping windows of ``n`` observations every ``step``; only full ones are yielded.

    Parameters
    ----------
    params : dict
        ``n`` (int >= 1, required): the window size; ``step`` (int >= 1,
        default ``DEFAULT_STEP``): how far each window advances.

    Examples
    --------
    ::

        tuple(Sliding({"n": 3, "step": 2}).chunks([1, 2, 3, 4, 5]))  # ((1, 2, 3), (3, 4, 5))
        tuple(Sliding({"n": 3}).chunks([1, 2]))  # ()
    """

    _PARAMS = ("n", "step")

    @classmethod
    def _check(cls, problems, params):
        """Require a positive size and step."""
        check_int_param(problems, "n", params.get("n"), ge=1)
        check_int_param(problems, "step", params.get("step", DEFAULT_STEP), ge=1)

    def _configure(self, params):
        """Take the size and the step."""
        self._n = int(params["n"])
        self._step = int(params.get("step", DEFAULT_STEP))

    def chunks(self, records):
        """Yield every full window, ``step`` observations apart.

        Parameters
        ----------
        records : sequence
            The observations, oldest first.

        Returns
        -------
        iterator of tuple
            Nothing until the first window is full.
        """
        for start in range(0, len(records) - self._n + 1, self._step):
            yield tuple(records[start : start + self._n])

    def label(self):
        """Return ``sliding:<n>/<step>``.

        Returns
        -------
        str
            The label a verdict reports.
        """
        return f"sliding:{self._n}/{self._step}"


class Period(Chunker):
    """Epoch-aligned time buckets of one ISO-8601 duration each.

    Observations are grouped by ``time_field // period``, so a bucket is a
    calendar-free grid from the epoch, never from the first observation.
    Nothing here can know a bucket has closed — that takes a clock, which
    no monitor holds — so the open bucket is the current window and is
    judged as it fills, with ``min_n`` as its only floor.

    Parameters
    ----------
    params : dict
        ``iso`` (str, required): a day/time duration such as ``"PT1M"``
        (calendar units refuse, as ``release.parse_iso_duration`` rules);
        ``time_field`` (str, default ``DEFAULT_TIME_FIELD``): the record
        field holding the epoch-ms instant.

    Examples
    --------
    ::

        chunker = Period({"iso": "PT1M"})
        ticks = [{"data_asof_ms": 0}, {"data_asof_ms": 59_999}, {"data_asof_ms": 60_000}]
        [len(chunk) for chunk in chunker.chunks(ticks)]  # [2, 1]
    """

    _PARAMS = ("iso", "time_field")

    @classmethod
    def _check(cls, problems, params):
        """Require a positive day/time duration and a field name."""
        try:
            period_ms = parse_iso_duration(params.get("iso"))
        except ProductionError as exc:
            _prefixed(problems, "iso", exc.problems)
        else:
            if period_ms < 1:
                problems.append(f"iso must be a positive duration, got {params.get('iso')!r}")
        _check_str(problems, "time_field", params.get("time_field", DEFAULT_TIME_FIELD))

    def _configure(self, params):
        """Take the period and the time field."""
        self._iso = params["iso"]
        self._period_ms = parse_iso_duration(self._iso)
        self._time_field = params.get("time_field", DEFAULT_TIME_FIELD)

    def chunks(self, records):
        """Group consecutive ``records`` by the bucket their time field falls in.

        Parameters
        ----------
        records : sequence of dict
            The observations, oldest first, each carrying the time field.

        Returns
        -------
        iterator of tuple
            One window per bucket, in order.

        Raises
        ------
        ProductionError
            If a record lacks an integer time field.
        """
        current, bucket = [], None
        for record in records:
            stamp = record.get(self._time_field) if isinstance(record, dict) else None
            if isinstance(stamp, bool) or not isinstance(stamp, int):
                raise ProductionError(
                    [f"period window: record lacks an integer {self._time_field!r}: {record!r}"]
                )
            key = stamp // self._period_ms
            if current and key != bucket:
                yield tuple(current)
                current = []
            bucket = key
            current.append(record)
        if current:
            yield tuple(current)

    def label(self):
        """Return ``period:<iso>``.

        Returns
        -------
        str
            The label a verdict reports.
        """
        return f"period:{self._iso}"

    def observation_fields(self):
        """Return the time field every observation must carry.

        Returns
        -------
        tuple of str
            ``(time_field,)``.
        """
        return (self._time_field,)


# ---------------------------------------------------------------------------
# Threshold — one bit, plus the number the verdict reports
# ---------------------------------------------------------------------------


class Threshold(_Configured, ABC):
    """When a statistic is a breach (§5.10).

    ``breached`` is the one abstract hook and returns one bit; ``bound``
    publishes the number a verdict reports as its ``threshold``. Which
    status a breach earns is not the threshold's business — it follows
    from the monitor's declared ``response``.

    Parameters
    ----------
    params : dict
        The ``threshold`` site minus its ``kind`` — the subclass's
        ``_PARAMS`` plus ``notes``.
    benchmark : callable or None, keyword-only
        ``benchmark(alpha, n_ref, n_cur) -> float``, the statistic's
        critical value under the null; supplied by the owning monitor and
        needed only by ``alpha``.

    Examples
    --------
    ::

        threshold = Constant({"max": 0.25})
        threshold.breached(0.3, 500, 300)  # True
        threshold.bound(500, 300)  # 0.25
    """

    def __init__(self, params=None, *, benchmark=None):
        self._benchmark = benchmark
        super().__init__(params)

    @abstractmethod
    def breached(self, statistic, n_ref, n_cur):
        """Say whether ``statistic`` breaches.

        Parameters
        ----------
        statistic : float
            The monitor's reduced window.
        n_ref, n_cur : int
            The reference and window sizes, for size-dependent bounds.

        Returns
        -------
        bool
            True on a breach.
        """

    def bound(self, n_ref, n_cur):
        """Return the number the verdict reports; the base has none.

        Parameters
        ----------
        n_ref, n_cur : int
            The reference and window sizes.

        Returns
        -------
        float or None
            None when no bound can be stated yet.
        """
        return None

    def state(self):
        """Return this threshold's JSON-able state; the base has none.

        Returns
        -------
        dict
            Empty for a stateless threshold.
        """
        return {}

    def restore(self, state):
        """Take state back from a ``state()`` payload; the base accepts only an empty dict.

        Parameters
        ----------
        state : dict
            Exactly the ``state()`` shape.

        Returns
        -------
        None
            Nothing to restore on the base.

        Raises
        ------
        ProductionError
            On a non-dict or an unknown key.
        """
        problems = []
        _check_dict(problems, "threshold state", state)
        if not problems:
            _check_unknown(problems, state, (), where="threshold state")
        if problems:
            raise ProductionError(problems)


class Constant(Threshold):
    """Fixed bounds: a breach is a statistic above ``max`` or below ``min``.

    Parameters
    ----------
    params : dict
        ``max`` and/or ``min`` (finite numbers, at least one required,
        ``min <= max``). A statistic ON a bound is inside it.

    Examples
    --------
    ::

        Constant({"min": 0.5}).breached(0.4, 10, 10)  # True
        Constant({"min": 0.0, "max": 1.0}).breached(0.5, 10, 10)  # False
    """

    _PARAMS = ("min", "max")

    @classmethod
    def _check(cls, problems, params):
        """Require at least one finite bound, ordered when both are given."""
        declared = [key for key in cls._PARAMS if key in params]
        if not declared:
            problems.append("constant threshold needs min and/or max")
        for key in declared:
            _check_number(problems, key, params[key])
        if len(declared) == 2 and not problems and params["min"] > params["max"]:
            problems.append(f"min {params['min']!r} exceeds max {params['max']!r}")

    def _configure(self, params):
        """Take the bounds."""
        self._min = params.get("min")
        self._max = params.get("max")

    def breached(self, statistic, n_ref, n_cur):
        """Say whether ``statistic`` lies outside the bounds.

        Parameters
        ----------
        statistic : float
            The monitor's reduced window.
        n_ref, n_cur : int
            Unused: the bounds are fixed.

        Returns
        -------
        bool
            True strictly outside ``[min, max]``.
        """
        above = self._max is not None and statistic > self._max
        below = self._min is not None and statistic < self._min
        return above or below

    def bound(self, n_ref, n_cur):
        """Return ``max`` when declared, else ``min``.

        Parameters
        ----------
        n_ref, n_cur : int
            Unused.

        Returns
        -------
        float
            The bound the verdict reports.
        """
        return self._max if self._max is not None else self._min


class ReferenceStd(Threshold):
    """A breach is ``k`` standard deviations from the statistics seen so far.

    Each ``breached`` call first judges against the history of prior
    statistics, then records the new one, so the current statistic never
    contributes to its own bound and nothing can breach before two prior
    statistics exist.

    Parameters
    ----------
    params : dict
        ``k`` (finite number > 0, required): the width in standard
        deviations (population, ``statistics.pstdev``).

    Examples
    --------
    ::

        threshold = ReferenceStd({"k": 2})
        for statistic in (1.0, 2.0, 1.5):
            threshold.breached(statistic, 10, 10)  # False, False, False
        threshold.breached(30.0, 10, 10)  # True
    """

    _PARAMS = ("k",)

    def __init__(self, params=None, *, benchmark=None):
        self._seen = []
        super().__init__(params, benchmark=benchmark)

    @classmethod
    def _check(cls, problems, params):
        """Require a positive width."""
        _check_number(problems, "k", params.get("k"), gt=0)

    def _configure(self, params):
        """Take the width."""
        self._k = params["k"]

    def breached(self, statistic, n_ref, n_cur):
        """Judge ``statistic`` against the prior history, then record it.

        Parameters
        ----------
        statistic : float
            The monitor's reduced window.
        n_ref, n_cur : int
            Unused: the bound comes from the history.

        Returns
        -------
        bool
            True when farther than ``k`` prior standard deviations from
            the prior mean; never before two prior statistics.
        """
        bound = self.bound(n_ref, n_cur)
        hit = bound is not None and abs(statistic - statistics.fmean(self._seen)) > bound
        self._seen.append(statistic)
        return hit

    def bound(self, n_ref, n_cur):
        """Return ``k`` population standard deviations of the history.

        Parameters
        ----------
        n_ref, n_cur : int
            Unused.

        Returns
        -------
        float or None
            None before two statistics have been seen.
        """
        if len(self._seen) < 2:
            return None
        return self._k * statistics.pstdev(self._seen)

    def state(self):
        """Return the history.

        Returns
        -------
        dict
            ``{"seen": [...]}``.
        """
        return {"seen": list(self._seen)}

    def restore(self, state):
        """Take the history back from a ``state()`` payload.

        Parameters
        ----------
        state : dict
            Exactly the ``state()`` shape.

        Returns
        -------
        None
            The history is replaced.

        Raises
        ------
        ProductionError
            On an unknown or missing key, or a non-list ``seen``.
        """
        problems = []
        _check_dict(problems, "threshold state", state)
        if not problems:
            _check_unknown(problems, state, ("seen",), where="threshold state")
            if not isinstance(state.get("seen"), list):
                problems.append("threshold state: seen must be a list")
        if problems:
            raise ProductionError(problems)
        self._seen = list(state["seen"])


class Alpha(Threshold):
    """A breach is a statistic above its critical value at significance ``alpha``.

    A threshold cannot know a statistic's null distribution, so the OWNING
    monitor injects it as ``benchmark`` — ``DistributionMonitor`` supplies
    its ``critical_value`` — and a monitor with none refuses an ``alpha``
    threshold at construction.

    Parameters
    ----------
    params : dict
        ``alpha`` (finite number strictly between 0 and 1, required).
    benchmark : callable, keyword-only
        ``benchmark(alpha, n_ref, n_cur) -> float``; required.

    Examples
    --------
    ::

        threshold = Alpha({"alpha": 0.05}, benchmark=lambda alpha, n_ref, n_cur: 2 * alpha)
        threshold.bound(100, 100)  # 0.1
        threshold.breached(0.2, 100, 100)  # True
    """

    _PARAMS = ("alpha",)

    @classmethod
    def _check(cls, problems, params):
        """Require a significance level inside the open unit interval."""
        _check_number(problems, "alpha", params.get("alpha"), gt=0, lt=1)

    def _configure(self, params):
        """Take the level and refuse without the benchmark only a monitor can supply."""
        self._alpha = params["alpha"]
        if self._benchmark is None:
            raise ProductionError(
                [
                    "alpha threshold needs a benchmark: only a monitor that publishes "
                    "critical_value(alpha, n_ref, n_cur) — a distribution monitor — can "
                    "declare one"
                ]
            )

    def breached(self, statistic, n_ref, n_cur):
        """Say whether ``statistic`` exceeds the benchmark at these sizes.

        Parameters
        ----------
        statistic : float
            The monitor's reduced window.
        n_ref, n_cur : int
            The reference and window sizes the benchmark scales with.

        Returns
        -------
        bool
            True strictly above the critical value.
        """
        return statistic > self.bound(n_ref, n_cur)

    def bound(self, n_ref, n_cur):
        """Return the critical value the benchmark gives at these sizes.

        Parameters
        ----------
        n_ref, n_cur : int
            The reference and window sizes.

        Returns
        -------
        float
            ``benchmark(alpha, n_ref, n_cur)``.
        """
        return self._benchmark(self._alpha, n_ref, n_cur)


# ---------------------------------------------------------------------------
# Profile — the saved reference population
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    """A saved summary of a population, one summary per field (§5.10).

    A summary holds what merges exactly — ``count``, ``missing``, ``min``,
    ``max``, ``sum``, ``sumsq`` and, when two profiles share their bin
    edges, the bin ``counts`` — plus what only approximates under a merge:
    the ``top_k`` most frequent values. Bins are quantile bins of the
    values the profile saw, so two profiles of different populations have
    different edges; merging those keeps every exact member and drops the
    bins to empty rather than pretend a re-binning is exact.

    Parameters
    ----------
    bins : int
        How many quantile bins each numeric field was cut into.
    top_k : int
        How many most-frequent values each field keeps.
    fields : dict
        ``field name -> summary``, where a summary is ``{"count", "missing",
        "min", "max", "sum", "sumsq", "bins": {"edges": [min, cut points...,
        max], "counts": [...]}, "top_k": [[value, count], ...]}``; the
        numeric members are ``None`` and the bins empty for a field whose
        values are not all numbers.

    Examples
    --------
    Profile one field of three rows, then merge a fourth row's profile::

        profile = Profile.from_records([{"p": 1.0}, {"p": 2.0}, {"p": 3.0}], ("p",), 2, 2)
        profile.to_obj()["fields"]["p"]["count"]  # 3
        more = Profile.from_records([{"p": 4.0}], ("p",), 2, 2)
        profile.merge(more).to_obj()["fields"]["p"]["max"]  # 4.0
    """

    bins: int
    top_k: int
    fields: dict

    _KEYS = ("bins", "top_k", "fields")
    _SUMMARY_KEYS = ("count", "missing", "min", "max", "sum", "sumsq", "bins", "top_k")
    _BIN_KEYS = ("edges", "counts")

    @classmethod
    def from_records(cls, records, fields, bins, top_k):
        """Summarise the named fields of ``records``.

        Parameters
        ----------
        records : sequence of dict
            The population, one dict per row (a leg, a tick, a row).
        fields : sequence of str
            The fields to summarise; a record lacking one, or holding
            ``None``, counts as ``missing`` for it.
        bins : int
            Quantile bins per numeric field (>= 1).
        top_k : int
            Most-frequent values kept per field (>= 0).

        Returns
        -------
        Profile
            The summary.

        Raises
        ------
        ProductionError
            On a bad ``bins``, ``top_k`` or ``fields``, or a value that is
            not a JSON scalar.
        """
        problems = []
        check_int_param(problems, "bins", bins, ge=1)
        check_int_param(problems, "top_k", top_k, ge=0)
        if not isinstance(fields, (list, tuple)) or not all(isinstance(f, str) for f in fields):
            problems.append(f"fields must be a sequence of field names, got {fields!r}")
        if problems:
            raise ProductionError(problems)
        summaries = {name: cls._summarise(records, name, int(bins), int(top_k)) for name in fields}
        return cls(int(bins), int(top_k), summaries)

    @classmethod
    def _summarise(cls, records, name, bins, top_k):
        """Build one field's summary."""
        values = []
        for record in records:
            value = record.get(name) if isinstance(record, dict) else None
            if value is None:
                continue
            if isinstance(value, Decimal):
                value = _number(value, name)
            if not isinstance(value, (str, bool, int, float)) or not (
                isinstance(value, (str, bool)) or number_ok(value)
            ):
                raise ProductionError([f"{name}: {value!r} is not a JSON scalar"])
            values.append(value)
        summary = {
            "count": len(values),
            "missing": len(records) - len(values),
            "min": None,
            "max": None,
            "sum": None,
            "sumsq": None,
            "bins": {"edges": [], "counts": []},
            "top_k": _top(values, top_k),
        }
        if values and all(number_ok(value) for value in values):
            interior = _quantile_edges(values, bins)
            summary.update(
                min=min(values),
                max=max(values),
                sum=math.fsum(values),
                sumsq=math.fsum(value * value for value in values),
                bins={"edges": [min(values), *interior, max(values)], "counts": _bin_counts(values, interior)},
            )
        return summary

    def merge(self, other):
        """Combine two profiles of the same shape into one.

        Parameters
        ----------
        other : Profile
            A profile with the same ``bins`` and ``top_k``.

        Returns
        -------
        Profile
            Over the union of both field sets; exact members add, bin
            counts add only when the edges agree, ``top_k`` re-ranks the
            union of both lists.

        Raises
        ------
        ProductionError
            If ``other`` is not a profile of the same shape.
        """
        if not isinstance(other, Profile):
            raise ProductionError([f"merge expects a Profile, got {other!r}"])
        if (other.bins, other.top_k) != (self.bins, self.top_k):
            raise ProductionError(
                [
                    f"cannot merge profiles of different shape: bins {self.bins} / {other.bins}, "
                    f"top_k {self.top_k} / {other.top_k}"
                ]
            )
        merged = {}
        for name in dict.fromkeys([*self.fields, *other.fields]):
            left, right = self.fields.get(name), other.fields.get(name)
            if left is None or right is None:
                merged[name] = copy.deepcopy(left if right is None else right)
            else:
                merged[name] = self._merge_summary(left, right)
        return Profile(self.bins, self.top_k, merged)

    def _merge_summary(self, left, right):
        """Combine two summaries of one field."""

        def added(key):
            if left[key] is None and right[key] is None:
                return None
            return (left[key] or 0) + (right[key] or 0)

        def extreme(key, pick):
            present = [side[key] for side in (left, right) if side[key] is not None]
            return pick(present) if present else None

        counter = Counter()
        for side in (left, right):
            for value, count in side["top_k"]:
                counter[value] += count
        ranked = sorted(counter.items(), key=lambda pair: (-pair[1], type(pair[0]).__name__, pair[0]))
        return {
            "count": left["count"] + right["count"],
            "missing": left["missing"] + right["missing"],
            "min": extreme("min", min),
            "max": extreme("max", max),
            "sum": added("sum"),
            "sumsq": added("sumsq"),
            "bins": self._merge_bins(left, right),
            "top_k": [[value, count] for value, count in ranked[: self.top_k]],
        }

    @staticmethod
    def _merge_bins(left, right):
        """Add bin counts when the edges agree; an empty side is the identity; else drop the bins."""
        if left["count"] == 0:
            return copy.deepcopy(right["bins"])
        if right["count"] == 0:
            return copy.deepcopy(left["bins"])
        edges = left["bins"]["edges"]
        if edges and edges == right["bins"]["edges"]:
            counts = [a + b for a, b in zip(left["bins"]["counts"], right["bins"]["counts"])]
            return {"edges": list(edges), "counts": counts}
        return {"edges": [], "counts": []}

    def to_obj(self):
        """Return the profile as a JSON-ready dict.

        Returns
        -------
        dict
            ``{"bins", "top_k", "fields"}``, a fresh deep copy.
        """
        return {"bins": self.bins, "top_k": self.top_k, "fields": copy.deepcopy(self.fields)}

    @classmethod
    def from_obj(cls, obj):
        """Rebuild a profile from its ``to_obj()`` form, default-deny.

        Parameters
        ----------
        obj : dict
            Exactly the ``to_obj()`` shape.

        Returns
        -------
        Profile
            Equal to the one that produced ``obj``.

        Raises
        ------
        ProductionError
            On an unknown or missing key at any level, or a member of the
            wrong shape — every problem listed.
        """
        problems = []
        _check_dict(problems, "profile", obj)
        if problems:
            raise ProductionError(problems)
        _check_unknown(problems, obj, cls._KEYS, where="profile")
        check_int_param(problems, "profile.bins", obj.get("bins"), ge=1)
        check_int_param(problems, "profile.top_k", obj.get("top_k"), ge=0)
        fields = obj.get("fields")
        _check_dict(problems, "profile.fields", fields)
        if isinstance(fields, dict):
            for name, summary in fields.items():
                cls._check_summary(problems, f"profile.fields.{name}", summary)
        if problems:
            raise ProductionError(problems)
        return cls(int(obj["bins"]), int(obj["top_k"]), copy.deepcopy(fields))

    @classmethod
    def _check_summary(cls, problems, where, summary):
        """Append every shape problem with one field summary."""
        _check_dict(problems, where, summary)
        if not isinstance(summary, dict):
            return
        _check_unknown(problems, summary, cls._SUMMARY_KEYS, where=where)
        missing = [key for key in cls._SUMMARY_KEYS if key not in summary]
        if missing:
            problems.append(f"{where}: missing {missing}")
            return
        for key in ("count", "missing"):
            check_int_param(problems, f"{where}.{key}", summary[key], ge=0)
        for key in ("min", "max", "sum", "sumsq"):
            if summary[key] is not None:
                _check_number(problems, f"{where}.{key}", summary[key])
        bins = summary["bins"]
        _check_dict(problems, f"{where}.bins", bins)
        if isinstance(bins, dict):
            _check_unknown(problems, bins, cls._BIN_KEYS, where=f"{where}.bins")
            for key in cls._BIN_KEYS:
                if not isinstance(bins.get(key), list):
                    problems.append(f"{where}.bins.{key} must be a list")
        top = summary["top_k"]
        if not isinstance(top, list) or not all(
            isinstance(pair, list) and len(pair) == 2 for pair in top
        ):
            problems.append(f"{where}.top_k must be a list of [value, count] pairs")


# ---------------------------------------------------------------------------
# Monitor — the ABC and the mechanics every family shares
# ---------------------------------------------------------------------------


class Monitor(_Configured, ABC):
    """The watch over a stream of recorded decisions and ticks (§5.10, D16).

    The loop calls ``observe(record)`` with each §6 ``tick`` and
    ``decision`` body it appends, then ``verdict()`` for the ``monitor``
    record and ``should_trip()`` for the breaker, and folds ``state()``
    into the §6 ``snapshot`` so a restart ``restore``s the window instead
    of refilling it. A record that carries none of a monitor's fields is
    not an observation; a decision yields one observation per ``legs[]``
    entry, any other record one for itself.

    Observations accumulate until they leave the current window, when they
    are retired into the references and dropped, so memory is bounded by
    the window and a window is never in its own reference. A verdict is
    computed once per observation and remembered, so reading it twice
    never re-consults a threshold that keeps history.

    Parameters
    ----------
    params : dict
        ``window`` (``{"kind": <CHUNKER_KINDS>, ...}``, required): how
        observations are cut into windows; ``threshold`` (``{"kind":
        <THRESHOLD_KINDS>, ...}``, required): when the statistic is a
        breach; ``response`` (one of ``RESPONSES``, default
        ``DEFAULT_RESPONSE``): what the loop does with a breach — a breach
        under ``halt`` is an ``alarm``, under anything else a ``warn``;
        ``min_n`` (int >= 1, default ``DEFAULT_MIN_N``): the fewest
        observations, and reference values, a verdict may rest on.
        Subclasses add their own knobs; ``notes`` is allowed at every site.
    name : str or None, keyword-only
        The owner's name for this instance — the document key — which the
        loop writes as the §6 ``monitor`` record's ``monitor`` field.

    Attributes
    ----------
    name : str or None
        As given.
    response : str
        The declared response, for the loop to act on.

    Examples
    --------
    A coverage watch that warns when fewer than half the legs abstain::

        monitor = Coverage(
            {
                "window": {"kind": "count", "n": 2},
                "threshold": {"kind": "constant", "min": 0.5},
                "response": "warn",
                "min_n": 1,
            },
            name="coverage",
        )
        monitor.observe({"kind": "decision", "legs": [{"final": "buy"}, {"final": "buy"}]})
        monitor.verdict().status  # 'warn'
        monitor.should_trip()  # False: only an alarm under response: halt trips
    """

    _PARAMS = ("window", "threshold", "response", "min_n")
    _STATE_KEYS = ("observations", "references", "threshold")

    def __init__(self, params=None, *, name=None):
        self.name = name
        self._observations = []
        self._references = ()
        self._version = 0
        self._memo = None
        super().__init__(params)

    @classmethod
    def _check(cls, problems, params):
        """Validate the four common knobs and both nested selector sites."""
        _kind_site(problems, "window", params.get("window"), CHUNKER_KINDS)
        _kind_site(problems, "threshold", params.get("threshold"), THRESHOLD_KINDS)
        response = params.get("response", DEFAULT_RESPONSE)
        if response not in RESPONSES:
            problems.append(f"response must be one of {list(RESPONSES)}, got {response!r}")
        check_int_param(problems, "min_n", params.get("min_n", DEFAULT_MIN_N), ge=1)

    def _configure(self, params):
        """Build the chunker and the threshold, take the response and the floor."""
        chunker_cls, chunker_params = _kind_site([], "window", params["window"], CHUNKER_KINDS)
        self._chunker = chunker_cls(chunker_params)
        threshold_cls, threshold_params = _kind_site(
            [], "threshold", params["threshold"], THRESHOLD_KINDS
        )
        self._threshold = threshold_cls(threshold_params, benchmark=self._benchmark())
        self.response = params.get("response", DEFAULT_RESPONSE)
        self._min_n = int(params.get("min_n", DEFAULT_MIN_N))

    def fit(self, records):
        """Prime the references from training-time records; the window stays empty.

        Parameters
        ----------
        records : iterable of dict
            §6 record bodies; the monitor reads its own fields from them.

        Returns
        -------
        None
            Every reference is ``fit`` with the extracted values; a
            monitor with no reference ignores the records.
        """
        values = [observation[_VALUE] for record in records for observation in self._extract(record)]
        for reference in self._references:
            reference.fit(values)
        self._touch()

    @abstractmethod
    def observe(self, record):
        """Take one recorded ``tick`` or ``decision`` body into the window.

        Parameters
        ----------
        record : dict
            A §6 record body.

        Returns
        -------
        None
            Zero or more observations were added.
        """

    @abstractmethod
    def verdict(self):
        """Answer over the current window.

        Returns
        -------
        Verdict
            ``insufficient`` below ``min_n``, on a partial window or with
            no ready reference; otherwise ``ok``, ``warn`` or ``alarm``.
        """

    def should_trip(self):
        """Say whether the current verdict is an ``alarm`` — a breach under ``response: halt``.

        Returns
        -------
        bool
            True exactly when the breaker should trip on this monitor.
        """
        return self.verdict().status == _ALARM

    def provisional(self):
        """Say whether the verdict still awaits its labels; phase-1 monitors never do.

        Returns
        -------
        bool
            False. The phase-2 outcome family answers from ``label_coverage``.
        """
        return False

    def state(self):
        """Return the window and strategy state as a JSON-able dict.

        Returns
        -------
        dict
            ``{"observations": [...], "references": [...], "threshold":
            {...}}``, what the §6 ``snapshot`` carries for this monitor.
        """
        return {
            "observations": [dict(observation) for observation in self._observations],
            "references": [reference.state() for reference in self._references],
            "threshold": self._threshold.state(),
        }

    def restore(self, state):
        """Take the window and strategy state back from a ``state()`` payload.

        Parameters
        ----------
        state : dict
            Exactly the ``state()`` shape of a monitor configured the same.

        Returns
        -------
        None
            The next verdict is what the saved monitor's would have been.

        Raises
        ------
        ProductionError
            On an unknown or missing key, a member of the wrong shape, or
            a reference count that does not match the configuration.
        """
        problems = []
        _check_dict(problems, "monitor state", state)
        if problems:
            raise ProductionError(problems)
        _check_unknown(problems, state, self._STATE_KEYS, where="monitor state")
        observations = state.get("observations")
        if not isinstance(observations, list) or not all(isinstance(o, dict) for o in observations):
            problems.append("monitor state: observations must be a list of dicts")
        references = state.get("references")
        if not isinstance(references, list) or len(references) != len(self._references):
            problems.append(
                f"monitor state: references must be a list of {len(self._references)}, "
                f"got {references!r}"
            )
        _check_dict(problems, "monitor state.threshold", state.get("threshold"))
        self._check_extra(problems, state)
        if problems:
            raise ProductionError(problems)
        self._observations = [dict(observation) for observation in observations]
        for reference, saved in zip(self._references, references):
            reference.restore(saved)
        self._threshold.restore(state["threshold"])
        self._restore_extra(state)
        self._touch()

    def _benchmark(self):
        """Return the critical-value callable an ``alpha`` threshold needs; the base has none."""
        return None

    def _check_extra(self, problems, state):
        """Append problems with a family's own state members; the base has none."""

    def _restore_extra(self, state):
        """Take back a family's own state members; the base has none."""

    def _candidates(self, record):
        """Return the dicts a record offers as observations: its legs, else itself."""
        if not isinstance(record, dict):
            return ()
        legs = record.get(_LEGS)
        if isinstance(legs, list):
            return tuple(leg for leg in legs if isinstance(leg, dict))
        return (record,)

    def _observation(self, candidate):
        """Return the observation dict one candidate yields, or None; the base reads nothing."""
        return None

    def _extract(self, record):
        """Return every observation ``record`` yields, carrying the chunker's fields."""
        out = []
        for candidate in self._candidates(record):
            observation = self._observation(candidate)
            if observation is None:
                continue
            for field in self._chunker.observation_fields():
                value = candidate.get(field, record.get(field))
                if value is not None:
                    observation[field] = value
            out.append(observation)
        return out

    def _absorb(self, record):
        """Add every observation ``record`` yields."""
        for observation in self._extract(record):
            self._add(observation)

    def _add(self, observation):
        """Append one observation and retire what left the window into the references."""
        self._observations.append(observation)
        window, _complete = self._window()
        start = self._start_of(window) if window else 0
        for departed in self._observations[:start]:
            for reference in self._references:
                reference.add(departed[_VALUE])
        del self._observations[:start]
        self._touch()

    def _start_of(self, window):
        """Return the index at which the current window begins in the retained observations."""
        first = window[0]
        return next(
            index for index, observation in enumerate(self._observations) if observation is first
        )

    def _window(self):
        """Return the current window and whether the chunker calls it complete."""
        chunks = tuple(self._chunker.chunks(self._observations))
        if not chunks:
            return (), False
        return chunks[-1], self._chunker.complete(chunks[-1])

    def _touch(self):
        """Mark the state changed so the next verdict is computed afresh."""
        self._version += 1
        self._memo = None

    def _judge(self, statistic, n_ref, n_cur, answerable):
        """Build the verdict for a reduced window, consulting the threshold once per state."""
        if self._memo is not None and self._memo[0] == self._version:
            return self._memo[1]
        if answerable and statistic is not None and n_cur >= self._min_n:
            bound = self._threshold.bound(n_ref, n_cur)
            hit = self._threshold.breached(statistic, n_ref, n_cur)
            status = _BREACH_STATUS[self.response] if hit else _OK
        else:
            bound, status = None, _INSUFFICIENT
        verdict = Verdict(
            status=status,
            statistic=statistic,
            threshold=bound,
            n_ref=n_ref,
            n_cur=n_cur,
            window=self._chunker.label(),
            slice=ALL_SLICE,
            provisional=self.provisional(),
        )
        self._memo = (self._version, verdict)
        return verdict


# ---------------------------------------------------------------------------
# OperationalMonitor — one record field each
# ---------------------------------------------------------------------------


class OperationalMonitor(Monitor):
    """A monitor over one named field of the tick or decision record (§5.10).

    Each member is a subclass because each reads a different record field
    and reduces its window differently — there is no shared numeric
    parameter to lift. A candidate that lacks any of ``_FIELDS`` is not an
    observation; ``_value`` turns one that has them into a number and
    ``_reduce`` folds the window's numbers into the statistic.

    Parameters
    ----------
    params : dict
        The four common knobs (see ``Monitor``).
    name : str or None, keyword-only
        The owner's name for this instance.

    Examples
    --------
    The worst data staleness over the last four ticks::

        monitor = Staleness(
            {"window": {"kind": "count", "n": 4}, "threshold": {"kind": "constant", "max": 8_000},
             "response": "halt", "min_n": 1}
        )
        for age in (1_000, 9_000, 2_000, 3_000):
            monitor.observe({"kind": "tick", "data_asof_ms": 0, "observed_at_ms": age})
        monitor.verdict().statistic  # 9000.0
        monitor.should_trip()  # True
    """

    _FIELDS = ()

    def observe(self, record):
        """Take one tick or decision body into the window.

        Parameters
        ----------
        record : dict
            A §6 record body.

        Returns
        -------
        None
            One observation per candidate carrying every field this
            monitor reads.
        """
        self._absorb(record)

    def verdict(self):
        """Reduce the current window with this monitor's own statistic.

        Returns
        -------
        Verdict
            The family's verdict; ``n_ref`` is always 0.
        """
        window, complete = self._window()
        values = [observation[_VALUE] for observation in window]
        statistic = self._reduce(values) if values else None
        return self._judge(statistic, 0, len(window), complete)

    def _observation(self, candidate):
        """Return ``{"value": ...}`` when the candidate carries every field this monitor reads."""
        if not all(field in candidate for field in self._FIELDS):
            return None
        value = self._value(candidate)
        return None if value is None else {_VALUE: value}

    @abstractmethod
    def _value(self, candidate):
        """Return the number one candidate contributes, or None to skip it."""

    @abstractmethod
    def _reduce(self, values):
        """Fold a non-empty window of numbers into the statistic."""


class Staleness(OperationalMonitor):
    """How far behind its data each tick ran: ``observed_at_ms - data_asof_ms``, the window's worst.

    Parameters
    ----------
    params : dict
        The four common knobs (see ``Monitor``).
    name : str or None, keyword-only
        The owner's name for this instance.

    Examples
    --------
    ::

        monitor = Staleness(
            {"window": {"kind": "count", "n": 1}, "threshold": {"kind": "constant", "max": 5_000},
             "min_n": 1}
        )
        monitor.observe({"kind": "tick", "data_asof_ms": 1_000, "observed_at_ms": 7_000})
        monitor.verdict().status  # 'warn'
    """

    _FIELDS = ("data_asof_ms", "observed_at_ms")

    def _value(self, candidate):
        """Return the gap in ms, or None when either instant is null."""
        asof, observed = candidate["data_asof_ms"], candidate["observed_at_ms"]
        if asof is None or observed is None:
            return None
        return _number(observed, "observed_at_ms") - _number(asof, "data_asof_ms")

    def _reduce(self, values):
        """Take the worst staleness in the window — the safety question."""
        return max(values)


class DecisionRate(OperationalMonitor):
    """How many ticks in the window were ``decided`` — a count, floored with ``constant.min``.

    Parameters
    ----------
    params : dict
        The four common knobs (see ``Monitor``).
    name : str or None, keyword-only
        The owner's name for this instance.

    Examples
    --------
    ::

        monitor = DecisionRate(
            {"window": {"kind": "count", "n": 2}, "threshold": {"kind": "constant", "min": 2},
             "min_n": 1}
        )
        for status in ("decided", "skipped:closed"):
            monitor.observe({"kind": "tick", "status": status})
        monitor.verdict().statistic  # 1.0
        monitor.verdict().status  # 'warn'
    """

    _FIELDS = ("status",)

    def _value(self, candidate):
        """Return one for a decided tick, zero otherwise."""
        return 1 if candidate["status"] == _DECIDED else 0

    def _reduce(self, values):
        """Count the decided ticks."""
        return sum(values)


class Coverage(OperationalMonitor):
    """The abstaining fraction: legs whose ``final`` side is ``none``, over every leg in the window.

    A decision with no legs abstained once, so it counts as one abstaining
    observation rather than vanishing.

    Parameters
    ----------
    params : dict
        The four common knobs (see ``Monitor``).
    name : str or None, keyword-only
        The owner's name for this instance.

    Examples
    --------
    ::

        monitor = Coverage(
            {"window": {"kind": "count", "n": 2}, "threshold": {"kind": "constant", "max": 0.5},
             "min_n": 1}
        )
        monitor.observe({"kind": "decision", "legs": [{"final": "none"}, {"final": "buy"}]})
        monitor.verdict().statistic  # 0.5
    """

    _FIELDS = ("final",)

    def _candidates(self, record):
        """Treat a decision with an empty ``legs`` as one abstention; otherwise the base rule."""
        if isinstance(record, dict) and record.get(_LEGS) == []:
            return ({"final": _ABSTAIN},)
        return super()._candidates(record)

    def _value(self, candidate):
        """Return one for an abstaining leg, zero otherwise."""
        return 1 if candidate["final"] == _ABSTAIN else 0

    def _reduce(self, values):
        """Return the fraction of legs that abstained."""
        return statistics.fmean(values)


class LatencyPercentiles(OperationalMonitor):
    """A percentile of tick latency: the §6 ``latency_ms`` phase map summed to one number per tick.

    Parameters
    ----------
    params : dict
        The four common knobs (see ``Monitor``) plus ``percentile`` (finite
        number strictly between 0 and 1, default ``DEFAULT_PERCENTILE``),
        reported by nearest rank so the statistic is always an observed
        latency.
    name : str or None, keyword-only
        The owner's name for this instance.

    Examples
    --------
    ::

        monitor = LatencyPercentiles(
            {"window": {"kind": "count", "n": 2}, "threshold": {"kind": "constant", "max": 500},
             "min_n": 1, "percentile": 0.5}
        )
        monitor.observe({"kind": "tick", "latency_ms": {"gate": 10, "propose": 20}})
        monitor.observe({"kind": "tick", "latency_ms": {"gate": 5, "propose": 5}})
        monitor.verdict().statistic  # 10.0
    """

    _PARAMS = OperationalMonitor._PARAMS + ("percentile",)
    _FIELDS = ("latency_ms",)

    @classmethod
    def _check(cls, problems, params):
        """Require a percentile inside the open unit interval."""
        super()._check(problems, params)
        _check_number(problems, "percentile", params.get("percentile", DEFAULT_PERCENTILE), gt=0, lt=1)

    def _configure(self, params):
        """Take the percentile."""
        super()._configure(params)
        self._percentile = params.get("percentile", DEFAULT_PERCENTILE)

    def _value(self, candidate):
        """Return the phase map summed, or a bare number as is; None when null."""
        latency = candidate["latency_ms"]
        if latency is None:
            return None
        if isinstance(latency, dict):
            return sum(_number(value, f"latency_ms.{phase}") for phase, value in latency.items())
        return _number(latency, "latency_ms")

    def _reduce(self, values):
        """Return the declared percentile by nearest rank."""
        return _percentile(values, self._percentile)


class RefusalCount(OperationalMonitor):
    """How many ticks in the window carried a ``refusal_reason``.

    Parameters
    ----------
    params : dict
        The four common knobs (see ``Monitor``).
    name : str or None, keyword-only
        The owner's name for this instance.

    Examples
    --------
    ::

        monitor = RefusalCount(
            {"window": {"kind": "count", "n": 2}, "threshold": {"kind": "constant", "max": 0},
             "min_n": 1}
        )
        monitor.observe({"kind": "tick", "refusal_reason": None})
        monitor.observe({"kind": "tick", "refusal_reason": "stale"})
        monitor.verdict().statistic  # 1.0
    """

    _FIELDS = ("refusal_reason",)

    def _value(self, candidate):
        """Return one for a refused tick, zero otherwise."""
        return 0 if candidate["refusal_reason"] is None else 1

    def _reduce(self, values):
        """Count the refusals."""
        return sum(values)


# ---------------------------------------------------------------------------
# StreamMonitor — a statistic over the stream of one field
# ---------------------------------------------------------------------------


class StreamMonitor(Monitor):
    """A monitor whose statistic runs over the stream of one numeric field (§5.10).

    The window and ``min_n`` gate when a verdict may be given; the
    statistic itself may be a recursion over every observation since a
    reset (``PageHinkley``) or a reduction of the window (``TrackingSignal``).
    ``_fold`` sees each observation before it enters the window, and the
    family's own recursion state rides in ``state()`` under ``stream``.

    Parameters
    ----------
    params : dict
        The four common knobs (see ``Monitor``) plus ``field`` (str,
        required): the numeric field read from each leg or record.
    name : str or None, keyword-only
        The owner's name for this instance.

    Examples
    --------
    ::

        monitor = PageHinkley(
            {"field": "prediction", "window": {"kind": "sliding", "n": 2},
             "threshold": {"kind": "constant", "max": 0.5}, "min_n": 2, "response": "halt"}
        )
        for value in (0.0, 0.0, 1.0):
            monitor.observe({"kind": "decision", "legs": [{"prediction": value}]})
        monitor.verdict().status  # 'alarm'
    """

    _PARAMS = Monitor._PARAMS + ("field",)
    _STATE_KEYS = Monitor._STATE_KEYS + ("stream",)

    @classmethod
    def _check(cls, problems, params):
        """Require the field."""
        super()._check(problems, params)
        _check_str(problems, "field", params.get("field"))

    def _configure(self, params):
        """Take the field."""
        super()._configure(params)
        self._field = params["field"]

    def observe(self, record):
        """Fold each observation into the stream statistic, then into the window.

        Parameters
        ----------
        record : dict
            A §6 record body.

        Returns
        -------
        None
            One observation per candidate carrying the field.
        """
        for observation in self._extract(record):
            self._fold(observation)
            self._add(observation)

    def state(self):
        """Return the window, strategy and stream state.

        Returns
        -------
        dict
            The ``Monitor`` shape plus ``"stream"``.
        """
        state = super().state()
        state["stream"] = self._stream_state()
        return state

    def _observation(self, candidate):
        """Return ``{"value": x}`` for a candidate carrying a non-null field."""
        value = candidate.get(self._field)
        return None if value is None else {_VALUE: _number(value, self._field)}

    def _fold(self, observation):
        """Advance the stream statistic by one observation; the base keeps none."""

    def _stream_state(self):
        """Return the recursion state; the base keeps none."""
        return {}

    def _check_extra(self, problems, state):
        """Require a ``stream`` dict."""
        _check_dict(problems, "monitor state.stream", state.get("stream"))

    def _restore_extra(self, state):
        """Hand the ``stream`` member to the recursion."""
        self._restore_stream(state["stream"])

    def _restore_stream(self, state):
        """Take the recursion state back; the base accepts only an empty dict."""
        problems = []
        _check_unknown(problems, state, (), where="monitor state.stream")
        if problems:
            raise ProductionError(problems)


class PageHinkley(StreamMonitor):
    """The Page–Hinkley change detector on one field's level.

    The classic recursion ``PH_t = max(0, PH_{t-1} + (x_t - mean_t - delta))``
    with ``mean_t`` the running mean INCLUDING ``x_t``; the bound λ comes
    from the ``threshold`` strategy like every other monitor's, so there is
    one bound mechanism, not two. After an ``alarm`` the next observation
    resets the whole recursion — accumulator AND running mean — so it
    scores exactly 0 instead of re-alarming forever.

    Parameters
    ----------
    params : dict
        The ``StreamMonitor`` knobs plus ``delta`` (finite number >= 0,
        default ``DEFAULT_DELTA``): the tolerated magnitude of change.
    name : str or None, keyword-only
        The owner's name for this instance.

    Examples
    --------
    A level shift from 0 to 1 alarms on the first shifted value::

        monitor = PageHinkley(
            {"field": "prediction", "delta": 0.05, "window": {"kind": "sliding", "n": 2},
             "threshold": {"kind": "constant", "max": 0.5}, "min_n": 2, "response": "halt"}
        )
        for value in (0.0, 0.0, 1.0):
            monitor.observe({"kind": "decision", "legs": [{"prediction": value}]})
        monitor.verdict().status  # 'alarm'
        monitor.observe({"kind": "decision", "legs": [{"prediction": 1.0}]})
        monitor.verdict().statistic  # 0.0
    """

    _PARAMS = StreamMonitor._PARAMS + ("delta",)
    _STREAM_KEYS = ("count", "mean", "accumulator")

    @classmethod
    def _check(cls, problems, params):
        """Require a non-negative tolerance."""
        super()._check(problems, params)
        _check_number(problems, "delta", params.get("delta", DEFAULT_DELTA), ge=0)

    def _configure(self, params):
        """Take the tolerance and start the recursion at rest."""
        super()._configure(params)
        self._delta = params.get("delta", DEFAULT_DELTA)
        self._reset()

    def verdict(self):
        """Report the accumulator once the window can carry a verdict.

        Returns
        -------
        Verdict
            ``statistic`` is ``PH_t``; ``n_ref`` is always 0.
        """
        window, complete = self._window()
        statistic = self._accumulator if window else None
        return self._judge(statistic, 0, len(window), complete)

    def _reset(self):
        """Forget the running mean and the accumulator."""
        self._count = 0
        self._mean = 0.0
        self._accumulator = 0.0

    def _fold(self, observation):
        """Reset after an alarm, then walk the recursion one step."""
        if self.verdict().status == _ALARM:
            self._reset()
        value = observation[_VALUE]
        self._count += 1
        self._mean += (value - self._mean) / self._count
        self._accumulator = max(0.0, self._accumulator + (value - self._mean - self._delta))

    def _stream_state(self):
        """Return the recursion state."""
        return {"count": self._count, "mean": self._mean, "accumulator": self._accumulator}

    def _restore_stream(self, state):
        """Take the recursion state back."""
        problems = []
        _check_unknown(problems, state, self._STREAM_KEYS, where="monitor state.stream")
        check_int_param(problems, "monitor state.stream.count", state.get("count"), ge=0)
        for key in ("mean", "accumulator"):
            _check_number(problems, f"monitor state.stream.{key}", state.get(key))
        if problems:
            raise ProductionError(problems)
        self._count = int(state["count"])
        self._mean = float(state["mean"])
        self._accumulator = float(state["accumulator"])


class TrackingSignal(StreamMonitor):
    """Forecast bias over the window: ``|sum of errors| / mean absolute error``.

    Absolute, so one ``constant.max`` bound reads as the classic
    ``|TS| > k`` and catches both directions; exactly 0 when every error is
    zero, so it is always finite.

    Parameters
    ----------
    params : dict
        The ``StreamMonitor`` knobs plus ``target_field`` (str, required):
        the realised value the field is compared against, read from the
        same leg or record.
    name : str or None, keyword-only
        The owner's name for this instance.

    Examples
    --------
    Six forecasts each one unit low::

        monitor = TrackingSignal(
            {"field": "prediction", "target_field": "realised", "window": {"kind": "count", "n": 6},
             "threshold": {"kind": "constant", "max": 4.0}, "min_n": 6}
        )
        for _ in range(6):
            monitor.observe({"kind": "outcome", "prediction": 10.0, "realised": 11.0})
        monitor.verdict().statistic  # 6.0
    """

    _PARAMS = StreamMonitor._PARAMS + ("target_field",)

    @classmethod
    def _check(cls, problems, params):
        """Require the target field."""
        super()._check(problems, params)
        _check_str(problems, "target_field", params.get("target_field"))

    def _configure(self, params):
        """Take the target field."""
        super()._configure(params)
        self._target_field = params["target_field"]

    def verdict(self):
        """Reduce the window's errors to the tracking signal.

        Returns
        -------
        Verdict
            ``statistic`` is ``|Σe| / MAD``; ``n_ref`` is always 0.
        """
        window, complete = self._window()
        errors = [observation[_TARGET] - observation[_VALUE] for observation in window]
        statistic = None
        if errors:
            mad = statistics.fmean(abs(error) for error in errors)
            statistic = abs(sum(errors)) / mad if mad else 0.0
        return self._judge(statistic, 0, len(window), complete)

    def _observation(self, candidate):
        """Return ``{"value", "target"}`` for a candidate carrying both fields."""
        value, target = candidate.get(self._field), candidate.get(self._target_field)
        if value is None or target is None:
            return None
        return {_VALUE: _number(value, self._field), _TARGET: _number(target, self._target_field)}


# ---------------------------------------------------------------------------
# DistributionMonitor — a window against a reference population
# ---------------------------------------------------------------------------


class DistributionMonitor(Monitor):
    """A monitor comparing the window's distribution of one field to a reference (§5.10).

    Every declared reference is kept and judged (D16: a fixed anchor AND a
    rolling reference), and the verdict reports the reference the window
    drifted from most. A reference below ``min_n`` values is not ready; with
    none ready the verdict is ``insufficient`` and ``n_ref`` reports the
    first reference's size. ``critical_value`` is the benchmark an
    ``alpha`` threshold is built with.

    Parameters
    ----------
    params : dict
        The four common knobs (see ``Monitor``) plus ``field`` (str,
        required) and ``reference`` (one ``{"uses": <REFERENCE_KINDS>,
        "params": {...}}`` site or a list of them, required).
    name : str or None, keyword-only
        The owner's name for this instance.

    Examples
    --------
    ::

        monitor = KS(
            {"field": "prediction", "reference": {"uses": "leading", "params": {"n": 4}},
             "window": {"kind": "count", "n": 4}, "threshold": {"kind": "alpha", "alpha": 0.05},
             "min_n": 4}
        )
        monitor.fit([{"legs": [{"prediction": float(v)}]} for v in (1, 2, 3, 4)])
        for v in (3, 4, 5, 6):
            monitor.observe({"kind": "decision", "legs": [{"prediction": float(v)}]})
        monitor.verdict().statistic  # 0.5
    """

    _PARAMS = Monitor._PARAMS + ("field", "reference")

    @classmethod
    def _check(cls, problems, params):
        """Require the field and at least one reference site."""
        super()._check(problems, params)
        _check_str(problems, "field", params.get("field"))
        _reference_sites(problems, params.get("reference"))

    def _configure(self, params):
        """Take the field and build every reference with it."""
        super()._configure(params)
        self._field = params["field"]
        self._references = tuple(
            reference_cls(reference_params, field=self._field)
            for reference_cls, reference_params in _reference_sites([], params["reference"])
        )

    def observe(self, record):
        """Take one record body's field values into the window.

        Parameters
        ----------
        record : dict
            A §6 record body.

        Returns
        -------
        None
            One observation per candidate carrying a non-null field.
        """
        self._absorb(record)

    def verdict(self):
        """Compare the current window against every ready reference.

        Returns
        -------
        Verdict
            The largest statistic across the ready references, with that
            reference's size as ``n_ref``.
        """
        window, complete = self._window()
        values = [observation[_VALUE] for observation in window]
        samples = [reference.sample() for reference in self._references]
        ready = [sample for sample in samples if len(sample) >= self._min_n]
        if not ready or not values:
            return self._judge(None, len(samples[0]), len(window), False)
        scored = [(self._statistic(sample, values), sample) for sample in ready]
        statistic, sample = max(scored, key=lambda pair: pair[0])
        return self._judge(statistic, len(sample), len(window), complete)

    @abstractmethod
    def critical_value(self, alpha, n_ref, n_cur):
        """Return the statistic's critical value under the null.

        Parameters
        ----------
        alpha : float
            The significance level.
        n_ref, n_cur : int
            The reference and window sizes.

        Returns
        -------
        float
            The value an ``alpha`` threshold compares against.
        """

    def _benchmark(self):
        """Hand ``critical_value`` to an ``alpha`` threshold."""
        return self.critical_value

    def _observation(self, candidate):
        """Return ``{"value": x}`` for a candidate carrying a non-null field."""
        value = candidate.get(self._field)
        return None if value is None else {_VALUE: _number(value, self._field)}

    @abstractmethod
    def _statistic(self, reference, window):
        """Return the distance between a reference sample and the window's values."""


class PSI(DistributionMonitor):
    """The population stability index over bins cut at the reference's quantiles.

    ``Σ (q_b − p_b)·ln(q_b / p_b)`` with ``p`` the reference proportions and
    ``q`` the window's over the same ``bins`` quantile bins of the
    reference; a proportion of zero is floored at ``PSI_EPSILON`` so the
    index stays finite when the window misses a bin. Each reference bins
    on its own quantiles. The ``alpha`` benchmark is the χ² approximation
    ``(1/n + 1/m)·(B − 1 + z_α·√(2(B − 1)))``.

    Parameters
    ----------
    params : dict
        The ``DistributionMonitor`` knobs plus ``bins`` (int >= 2, default
        ``DEFAULT_BINS``).
    name : str or None, keyword-only
        The owner's name for this instance.

    Examples
    --------
    Identical samples score exactly zero::

        monitor = PSI(
            {"field": "prediction", "bins": 2, "reference": {"uses": "leading", "params": {"n": 4}},
             "window": {"kind": "count", "n": 4}, "threshold": {"kind": "constant", "max": 0.1},
             "min_n": 4}
        )
        rows = [{"legs": [{"prediction": float(v)}]} for v in (1, 2, 3, 4)]
        monitor.fit(rows)
        for row in rows:
            monitor.observe(row)
        monitor.verdict().statistic  # 0.0
    """

    _PARAMS = DistributionMonitor._PARAMS + ("bins",)

    @classmethod
    def _check(cls, problems, params):
        """Require at least two bins."""
        super()._check(problems, params)
        check_int_param(problems, "bins", params.get("bins", DEFAULT_BINS), ge=2)

    def _configure(self, params):
        """Take the bin count."""
        super()._configure(params)
        self._bins = int(params.get("bins", DEFAULT_BINS))

    def critical_value(self, alpha, n_ref, n_cur):
        """Return the χ² benchmark for PSI at these sizes.

        Parameters
        ----------
        alpha : float
            The significance level.
        n_ref, n_cur : int
            The reference and window sizes.

        Returns
        -------
        float
            ``(1/n_ref + 1/n_cur)·(B − 1 + z_α·√(2(B − 1)))``.
        """
        z = statistics.NormalDist().inv_cdf(1.0 - alpha)
        degrees = self._bins - 1
        return (1.0 / n_ref + 1.0 / n_cur) * (degrees + z * math.sqrt(2 * degrees))

    def _statistic(self, reference, window):
        """Return the PSI of the window against bins cut at this reference's quantiles."""
        interior = _quantile_edges(reference, self._bins)
        return _psi(_bin_counts(reference, interior), _bin_counts(window, interior))


class KS(DistributionMonitor):
    """The two-sample Kolmogorov–Smirnov distance, distribution-free so it takes no bins.

    The statistic is the largest gap between the reference's and the
    window's empirical distribution functions; the ``alpha`` benchmark is
    the Kolmogorov series' large-sample value ``√(−ln(α/2)/2)·√((n+m)/(n·m))``.

    Parameters
    ----------
    params : dict
        The ``DistributionMonitor`` knobs.
    name : str or None, keyword-only
        The owner's name for this instance.

    Examples
    --------
    Two samples that half-overlap are half a step apart::

        monitor = KS(
            {"field": "prediction", "reference": {"uses": "leading", "params": {"n": 4}},
             "window": {"kind": "count", "n": 4}, "threshold": {"kind": "constant", "max": 0.9},
             "min_n": 4}
        )
        monitor.fit([{"legs": [{"prediction": float(v)}]} for v in (1, 2, 3, 4)])
        for v in (3, 4, 5, 6):
            monitor.observe({"kind": "decision", "legs": [{"prediction": float(v)}]})
        monitor.verdict().statistic  # 0.5
    """

    def critical_value(self, alpha, n_ref, n_cur):
        """Return the Kolmogorov critical value at these sizes.

        Parameters
        ----------
        alpha : float
            The significance level.
        n_ref, n_cur : int
            The reference and window sizes.

        Returns
        -------
        float
            ``√(−ln(α/2)/2)·√((n_ref + n_cur)/(n_ref·n_cur))``.
        """
        return math.sqrt(-math.log(alpha / 2.0) / 2.0) * math.sqrt((n_ref + n_cur) / (n_ref * n_cur))

    def _statistic(self, reference, window):
        """Return the largest ECDF gap between this reference and the window."""
        return _ks(reference, window)


# ---------------------------------------------------------------------------
# Registries — import is registration (§4.3)
# ---------------------------------------------------------------------------

MONITOR_KINDS = Registry("monitor", Monitor)
MONITOR_KINDS.register("staleness", Staleness)
MONITOR_KINDS.register("decision_rate", DecisionRate)
MONITOR_KINDS.register("coverage", Coverage)
MONITOR_KINDS.register("latency", LatencyPercentiles)
MONITOR_KINDS.register("refusals", RefusalCount)
MONITOR_KINDS.register("page_hinkley", PageHinkley)
MONITOR_KINDS.register("tracking_signal", TrackingSignal)
MONITOR_KINDS.register("psi", PSI)
MONITOR_KINDS.register("ks", KS)

REFERENCE_KINDS = Registry("reference", Reference)
REFERENCE_KINDS.register("leading", Leading)
REFERENCE_KINDS.register("rolling", Rolling)
REFERENCE_KINDS.register("snapshot", Snapshot)

CHUNKER_KINDS = Registry("chunker", Chunker)
CHUNKER_KINDS.register("count", Count)
CHUNKER_KINDS.register("period", Period)
CHUNKER_KINDS.register("sliding", Sliding)

THRESHOLD_KINDS = Registry("threshold", Threshold)
THRESHOLD_KINDS.register("constant", Constant)
THRESHOLD_KINDS.register("reference_std", ReferenceStd)
THRESHOLD_KINDS.register("alpha", Alpha)
