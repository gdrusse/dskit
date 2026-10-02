"""The evaluation-protocol kinds: ``holdout-cut`` and ``rolling-origin-plan``.

Two kinds, one story (ADR-0215, numbered 0222 in the decision log). A dated
cohort becomes an evaluation protocol in a fixed order: the HOLDOUT is
locked first, and only then are the remaining dates cut into folds. The
order is the point. A holdout carved after folds were tuned is a holdout
somebody has already seen, and a fold grid anchored at the start of
history moves every time a date is appended.

``holdout-cut`` takes the last ``ceil(fraction x dates)`` dates of the
FULL cohort and emits only what lies before them: the dev rows, minus
every dev row whose label ends at or after the holdout's first date (the
purge), and dates-only metrics. No holdout row, and no value from one,
leaves the node.

``rolling-origin-plan`` turns the dev dates into rolling-origin folds
sized by DATE COUNT, not calendar span: a validation window of ``val_n``
dates, a fold every ``step_n`` dates, a train window of the last
``train_n`` dates that settled before the validation window opens (the
strict-before ``driver._fold_splits`` applies to a calendar embargo). The
grid is anchored at the NEWEST end, so whatever remainder does not divide
evenly is dropped from the oldest history, never from the edge beside the
holdout. The oldest ``warmup_folds`` folds are the WARM-UP, the rest are
SCORED. All selection (features, models, hyper-parameters) is meant to run
on warm-up folds only; scored folds refit parameters per fold with every
choice frozen and are the only evidence. The fold table marks each fold's
role, and ``scored_start`` names the seam between the two.

The plan REFUSES a row on or past ``holdout_start`` (its lock is
mechanical, not a wiring convention) and a row whose label is longer than
``embargo_days`` (an embargo that does not cover its labels is a leak).
Every knob is required and none has a default in code: the numbers are
empirical, and they belong to the child's config, with notes.

Both kinds read rows as mappings carrying an ISO ``date_field`` and an ISO
``end_field`` (the day the row's label is known). A date is a calendar
DAY: a number, a date-time or ``YYYYMMDD`` is refused, the same way
``weekday-onehot`` refuses it. Refusals name node, row and field.

``label_reaches`` is the one purge rule. Both kinds call it, and a later
slice can import it rather than restate it.

They are registered with ``owned=False`` (any project may shadow them with
its own class via an import path), under the ``"forbidden"`` serving
class: they are development-time protocol nodes, and a served tick never
re-cuts a history.

Validator convention: params may legally arrive as unmaterialized
``$``-references at plan time (``holdout_start`` and ``embargo_days`` are
designed to be wired); validators tolerate the ``$``-form and check the
real value when construction sees the materialized params.

No import-time side effects: nothing registers until :func:`register` is
called.

Import cost: stdlib only.
"""

import json
import math
from abc import abstractmethod
from bisect import bisect_left
from collections import namedtuple
from datetime import date
from fractions import Fraction

from dskit.pipeline.document import date_problem, is_node_ref
from dskit.pipeline.kinds_flow import _MISSING, Derive, WeekdayOneHot, _field
from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    Node,
    check_int_param,
    reject_unknown_params,
)
from dskit.pipeline.records import number_ok

__all__ = [
    "HoldoutCut",
    "RollingOriginPlan",
    "label_reaches",
    "register",
]

#: A fold's ``role`` for the oldest ``warmup_folds`` folds: the only folds
#: a selection step (features, models, hyper-parameters) may read. A
#: consumer pins its set by ``role == "warmup"``, never by fold count, so
#: the literal is part of the contract and a test pins it.
_ROLE_WARMUP = "warmup"

#: A fold's ``role`` for every later fold: a clean simulation with every
#: choice frozen, and the only evidence.
_ROLE_SCORED = "scored"

#: One input row, read and checked: its position in the input, its entry
#: day and label-end day as ``date`` objects, and the row itself.
_Dated = namedtuple("_Dated", "index day end row")

#: One fold's windows, as slices of the sorted cohort dates: the train
#: dates, the validation dates and the cohort dates the embargo excluded
#: between them.
_Window = namedtuple("_Window", "train val purged")


def label_reaches(end, start):
    """Say whether a label that settles at ``end`` reaches ``start``.

    The one purge rule of the evaluation protocol: a label whose end is on
    or after the first date of a later window leaks that window's future
    into the earlier one. The holdout purge and the plan's lock both ask
    it, so the boundary (``end == start`` reaches) has one owner.

    Parameters
    ----------
    end : str or datetime.date
        The day the label is known (an ISO ``YYYY-MM-DD`` string, or a
        ``date``).
    start : str or datetime.date
        The first day of the later window, of the same type as ``end``.

    Returns
    -------
    bool
        True when ``end >= start``.

    Examples
    --------
    The boundary day reaches; the day before does not::

        label_reaches("2026-01-05", "2026-01-05")  # True
        label_reaches("2026-01-04", "2026-01-05")  # False
    """
    return end >= start


class _DatedCohort(Node):
    """The rules the two protocol kinds share: rows, dates, params, weeks.

    Role ``transform``; outputs ``records`` and ``metrics``; never served.
    A subclass supplies its own knobs (``_PARAMS``) and their problems
    (:meth:`_own_problems`, ABSTRACT: a subclass without it refuses to
    construct) and its own ``run``.
    """

    role = "transform"
    outputs = ("records", "metrics")

    #: The knob table; a subclass names every knob it reads.
    _PARAMS = ()

    #: The container-shape check IS derive's: same port, same rule, one copy.
    validate_inputs = Derive.validate_inputs

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the kind for serving: ``"forbidden"`` — a protocol node, never a served tick (ADR-0091).

        The answer is deliberate, not inherited: cutting a holdout or a
        fold table is a development-time act over the whole history, and a
        served tick that re-ran it would move the protocol under a release.

        Parameters
        ----------
        params : dict
            The declared params; unused — the answer holds for every document.
        verified_run_evidence : dict
            The release's evidence; unused — no evidence widens it.

        Returns
        -------
        str
            ``"forbidden"``.
        """
        return "forbidden"

    @classmethod
    @abstractmethod
    def _own_problems(cls, problems, params):
        """Append the problems with the knobs only this kind owns."""

    @classmethod
    def validate_params(cls, params):
        """Problems with this node's declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying unmaterialized
            ``$``-references.

        Returns
        -------
        list of str
            One message per problem; empty when the params are legal. An
            unknown knob, a missing required knob and each knob's own rule
            are named.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        WeekdayOneHot._name_problems(problems, params, "date_field")
        WeekdayOneHot._name_problems(problems, params, "end_field")
        cls._own_problems(problems, params)
        return problems

    def _read_date(self, index, row, field):
        """Read one ISO day off the row, or refuse naming node, row and field."""
        value = _field(row, field)
        if value is _MISSING:
            raise ValueError(f"{self.key}: row {index} carries no {field!r} field")
        if date_problem(value):
            raise ValueError(
                f"{self.key}: row {index} field {field!r} is {value!r}, not a "
                "real ISO YYYY-MM-DD date (a number, a date-time or YYYYMMDD is "
                "refused — derive the date first)"
            )
        return date.fromisoformat(value)

    def _read_row(self, index, row):
        """Read and check one row: a mapping with a real day and a label end on or after it."""
        if not isinstance(row, dict):
            raise ValueError(
                f"{self.key}: row {index} is a {type(row).__name__}, not a mapping"
            )
        date_field, end_field = self.params["date_field"], self.params["end_field"]
        day = self._read_date(index, row, date_field)
        end = self._read_date(index, row, end_field)
        if end < day:
            raise ValueError(
                f"{self.key}: row {index} field {end_field!r} is {row[end_field]!r}, "
                f"before its entry {date_field!r} {row[date_field]!r} — a label "
                "cannot settle before it starts (a same-day label is lawful)"
            )
        return _Dated(index, day, end, row)

    def _read_cohort(self, records):
        """Read every input row, in input order, refusing the first bad one."""
        return [self._read_row(index, row) for index, row in enumerate(records)]

    @staticmethod
    def _iso_weeks(days):
        """Count the distinct (ISO year, ISO week) pairs among ``days``."""
        return len({day.isocalendar()[:2] for day in days})


class HoldoutCut(_DatedCohort):
    """Lock the last dates of a cohort as the holdout — the ``holdout-cut`` kind.

    Role ``transform``. Sizes the holdout on the FULL cohort's distinct
    dates, purges the dev rows whose label reaches it, and emits only the
    dev rows and dates-only metrics: no holdout row, and no value from
    one, leaves the node.

    The size is ``ceil(fraction x dates)`` computed on the DECIMAL
    spelling of ``fraction`` (``repr`` of its ``float``), never on the
    binary float: ``0.07 x 100`` is 7 dates, though the float product
    rounds up to 8. REFUSES when the purge leaves no dev rows.

    Parameters
    ----------
    params : dict
        ``date_field`` (str, required) — the row field holding the ISO
        entry date; ``end_field`` (str, required) — the ISO day the row's
        label is known, which must not precede the entry date;
        ``fraction`` (number, ``0 < fraction < 1``, required) — the share
        of distinct dates to hold out, rounded UP.

    Examples
    --------
    Hold out the last fifth of the dates of a cohort::

        node = HoldoutCut(
            "cut",
            {"date_field": "quote_date", "end_field": "settle", "fraction": 0.2},
        )
        out = node.run(ctx, {"records": rows})
        out["metrics"]["holdout_start"]  # the first held-out date
    """

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("date_field", "end_field", "fraction")

    @classmethod
    def _own_problems(cls, problems, params):
        """Append the ``fraction`` problems: required, a number in the open unit interval."""
        if "fraction" not in params:
            problems.append(
                "fraction is required — the share of dates to hold out has no default"
            )
            return
        fraction = params["fraction"]
        if is_node_ref(fraction):
            return
        if not number_ok(fraction) or not 0 < fraction < 1:
            problems.append(
                f"fraction must be a number with 0 < fraction < 1, got {fraction!r}"
            )

    def _holdout_size(self, n_dates):
        """Dates to hold out: ``ceil`` of the DECIMAL fraction times ``n_dates``."""
        decimal = Fraction(repr(float(self.params["fraction"])))
        return math.ceil(decimal * n_dates)

    @staticmethod
    def _dev_rows(rows, start):
        """Split off the dev rows before ``start``; return them and the purged count."""
        before = [r for r in rows if r.day < start]
        kept = [r for r in before if not label_reaches(r.end, start)]
        return kept, len(before) - len(kept)

    def _canonical(self, dated):
        """Return the row's canonical JSON, the tie-break of the emitted order."""
        try:
            return json.dumps(dated.row, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{self.key}: row {dated.index} has no canonical JSON form, so "
                f"the emitted rows cannot be put in a total order ({exc})"
            ) from exc

    @staticmethod
    def _holdout_rows(rows, start):
        """Count the cohort rows dated on or after ``start`` (a count, no value)."""
        return sum(1 for r in rows if r.day >= start)

    def _metrics(self, held, dev, purged, rows):
        """Return the dates-and-counts metrics of one cut."""
        return {
            "holdout_start": held[0].isoformat(),
            "holdout_end": held[-1].isoformat(),
            "holdout_dates": len(held),
            "holdout_weeks": self._iso_weeks(held),
            "dev_dates": len({r.day for r in dev}),
            "purged_rows": purged,
            "holdout_rows": self._holdout_rows(rows, held[0]),
            "panel_rows": len(rows),
        }

    def run(self, ctx, inputs):
        """Lock the holdout, purge the dev rows that reach it, emit the rest.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused here beyond the node's own logging.
        inputs : dict
            ``records`` — the FULL cohort (list or tuple of mappings).

        Returns
        -------
        dict
            ``records`` — the dev rows (list), the input row objects
            untouched, sorted by date then canonical row JSON, a total
            order; ``metrics`` — ``holdout_start`` / ``holdout_end`` (ISO
            dates), ``holdout_dates`` (int), ``holdout_weeks`` (distinct
            ISO year-and-week pairs among the holdout dates), ``dev_dates``
            (distinct dates among the emitted rows), ``purged_rows``
            (dev rows dropped because their label reaches the holdout),
            ``holdout_rows`` (input rows dated in the holdout) and
            ``panel_rows`` (all input rows); the last two are counts only.

        Raises
        ------
        ValueError
            On a bad row (naming node, row and field), an empty cohort, a
            purge that leaves no dev rows, or a row with no canonical JSON.
        """
        rows = self._read_cohort(inputs["records"])
        dates = sorted({r.day for r in rows})
        if not dates:
            raise ValueError(f"{self.key}: no rows — there is no cohort to cut")
        held = dates[-self._holdout_size(len(dates)) :]
        dev, purged = self._dev_rows(rows, held[0])
        if not dev:
            raise ValueError(
                f"{self.key}: the cut leaves no dev rows — {len(held)} of "
                f"{len(dates)} date(s) are held out from {held[0].isoformat()} and "
                f"the purge dropped {purged} row(s) whose label reaches it"
            )
        ordered = sorted(dev, key=lambda r: (r.day, self._canonical(r)))
        metrics = self._metrics(held, dev, purged, rows)
        self.log.info(
            "holdout-cut locked %d of %d date(s) from %s; %d dev row(s), %d purged",
            len(held),
            len(dates),
            metrics["holdout_start"],
            len(ordered),
            purged,
        )
        return {"records": [r.row for r in ordered], "metrics": metrics}


class RollingOriginPlan(_DatedCohort):
    """Cut dev dates into count-sized rolling-origin folds — the ``rolling-origin-plan`` kind.

    Role ``transform``. Windows count cohort DATES (a date carried by
    several rows counts once): the validation window holds ``val_n``
    dates, a fold starts every ``step_n`` dates, and the train window is
    the last ``train_n`` dates strictly before ``val_start`` less
    ``embargo_days`` (the strict-before of ``driver._fold_splits``). The
    grid is END-anchored: the newest validation window ends at the last
    date, and the remainder that does not divide evenly is the OLDEST
    history. A window with fewer than ``train_n`` train dates is not a
    fold.

    The oldest ``warmup_folds`` folds are the WARM-UP, the rest SCORED:
    all selection runs on warm-up folds only, and scored folds refit
    parameters per fold with every choice frozen. REFUSES a row dated on
    or past ``holdout_start``, a row whose label reaches it, a row whose
    label is longer than ``embargo_days``, and a cohort that makes fewer
    than ``warmup_folds + 1`` folds (no scored fold would remain).

    Parameters
    ----------
    params : dict
        All required; none has a default in code. ``date_field`` and
        ``end_field`` (str) — the ISO entry date and label-end day;
        ``holdout_start`` (ISO date, designed to be wired from
        ``$cut.metrics.holdout_start``); ``embargo_days`` (int >= 0,
        designed to be wired from the keyed label horizon);
        ``val_n``, ``step_n``, ``train_n`` (int >= 1, ``step_n >= val_n``
        so validation windows never overlap); ``warmup_folds`` (int >= 0).
        An integral float such as ``7.0`` is the int 7.

    Examples
    --------
    Folds of 40 validation dates over 450 train dates, the oldest four of
    them the warm-up::

        node = RollingOriginPlan(
            "plan",
            {
                "date_field": "quote_date",
                "end_field": "settle",
                "holdout_start": "2025-01-02",
                "embargo_days": 7,
                "val_n": 40,
                "step_n": 40,
                "train_n": 450,
                "warmup_folds": 4,
            },
        )
        out = node.run(ctx, {"records": dev_rows})
        out["metrics"]["scored_start"]  # the first scored fold's val_start
    """

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = (
        "date_field",
        "embargo_days",
        "end_field",
        "holdout_start",
        "step_n",
        "train_n",
        "val_n",
        "warmup_folds",
    )

    #: The count knobs and the floor each must meet.
    _COUNT_FLOORS = (
        ("embargo_days", 0),
        ("step_n", 1),
        ("train_n", 1),
        ("val_n", 1),
        ("warmup_folds", 0),
    )

    @classmethod
    def _count_problems(cls, problems, params, name, floor):
        """Append one count knob's problems: required, an int at or above its floor."""
        if name not in params:
            problems.append(f"{name} is required — it has no default")
        elif not is_node_ref(params[name]):
            check_int_param(problems, name, params[name], ge=floor)

    @classmethod
    def _step_problems(cls, problems, params):
        """Append a problem when ``step_n < val_n``, once both are usable counts."""
        counts = []
        for name in ("step_n", "val_n"):
            scratch = []
            if name not in params or is_node_ref(params[name]):
                return
            check_int_param(scratch, name, params[name], ge=1)
            if scratch:
                return
            counts.append(int(params[name]))
        if counts[0] < counts[1]:
            problems.append(
                f"step_n ({counts[0]}) must be >= val_n ({counts[1]}) so "
                "validation windows never overlap"
            )

    @classmethod
    def _own_problems(cls, problems, params):
        """Append the problems with the counts, their order and ``holdout_start``."""
        for name, floor in cls._COUNT_FLOORS:
            cls._count_problems(problems, params, name, floor)
        cls._step_problems(problems, params)
        if "holdout_start" not in params:
            problems.append("holdout_start is required — the lock has no default")
        elif not is_node_ref(params["holdout_start"]) and date_problem(
            params["holdout_start"]
        ):
            problems.append(
                "holdout_start must be a real ISO YYYY-MM-DD date, got "
                f"{params['holdout_start']!r}"
            )

    def _count(self, name):
        """One validated count knob as an int (an integral float is its int)."""
        return int(self.params[name])

    def _check_locked(self, rows, start):
        """Refuse a row on or past the holdout, or whose label outruns the embargo."""
        embargo = self._count("embargo_days")
        for r in rows:
            if r.day >= start:
                raise ValueError(
                    f"{self.key}: row {r.index} field {self.params['date_field']!r} "
                    f"is {r.day.isoformat()}, on or after holdout_start "
                    f"{start.isoformat()} — the holdout is locked and the plan "
                    "never sees it"
                )
            if label_reaches(r.end, start):
                raise ValueError(
                    f"{self.key}: row {r.index} field {self.params['end_field']!r} "
                    f"is {r.end.isoformat()}, on or after holdout_start "
                    f"{start.isoformat()} — its label reaches the holdout"
                )
            if (r.end - r.day).days > embargo:
                raise ValueError(
                    f"{self.key}: row {r.index} label runs {(r.end - r.day).days} "
                    f"day(s) from {self.params['date_field']!r} to "
                    f"{self.params['end_field']!r}, longer than embargo_days "
                    f"{embargo} — the embargo would not cover its label"
                )

    def _windows(self, dates):
        """Every fold's windows over the sorted cohort ``dates``, oldest first."""
        train_n, val_n = self._count("train_n"), self._count("val_n")
        step_n, embargo = self._count("step_n"), self._count("embargo_days")
        ordinals = [d.toordinal() for d in dates]
        windows = []
        val_first = len(dates) - val_n
        while val_first >= 0:
            # train takes only dates STRICTLY before val_start - embargo_days;
            # ordinals keep the subtraction clear of the calendar's range
            eligible = bisect_left(ordinals, ordinals[val_first] - embargo)
            if eligible < train_n:
                break  # older windows have fewer train dates still
            windows.append(
                _Window(
                    dates[eligible - train_n : eligible],
                    dates[val_first : val_first + val_n],
                    val_first - eligible,
                )
            )
            val_first -= step_n
        windows.reverse()
        return windows

    def _check_scored_remains(self, windows, n_dates):
        """Refuse a plan with no scored fold to follow its warm-up."""
        warmup = self._count("warmup_folds")
        if len(windows) < warmup + 1:
            raise ValueError(
                f"{self.key}: {n_dates} date(s) make {len(windows)} fold(s) with "
                f"train_n {self._count('train_n')}, val_n {self._count('val_n')}, "
                f"step_n {self._count('step_n')} and embargo_days "
                f"{self._count('embargo_days')}; warmup_folds {warmup} needs at "
                f"least {warmup + 1} (one fold must be scored)"
            )

    def _fold_row(self, number, window):
        """One row of the fold table: ISO edges, counts, weeks, role."""
        train, val = window.train, window.val
        warmup = number <= self._count("warmup_folds")
        return {
            "fold": number,
            "role": _ROLE_WARMUP if warmup else _ROLE_SCORED,
            "train_start": train[0].isoformat(),
            "train_end": train[-1].isoformat(),
            "val_start": val[0].isoformat(),
            "val_end": val[-1].isoformat(),
            "train_dates": len(train),
            "val_dates": len(val),
            "train_weeks": self._iso_weeks(train),
            "val_weeks": self._iso_weeks(val),
            "purged": window.purged,
        }

    def _metrics(self, windows):
        """Return the plan's metrics: counts, warm-up weeks over the UNION, the seam."""
        warmup = self._count("warmup_folds")
        union = {d for w in windows[:warmup] for d in w.val}
        return {
            "folds": len(windows),
            "scored": len(windows) - warmup,
            "warmup_weeks": self._iso_weeks(union),
            "scored_start": windows[warmup].val[0].isoformat(),
        }

    def run(self, ctx, inputs):
        """Cut the dev dates into folds and mark each warm-up or scored.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused here beyond the node's own logging.
        inputs : dict
            ``records`` — the dev rows (list or tuple of mappings), the
            holdout already cut away.

        Returns
        -------
        dict
            ``records`` — the fold table (list of dict), oldest fold
            first: ``fold`` (1-based), ``role`` (``"warmup"`` or
            ``"scored"``), ISO ``train_start`` / ``train_end`` /
            ``val_start`` / ``val_end``, ``train_dates`` / ``val_dates``,
            ``train_weeks`` / ``val_weeks`` (distinct ISO year-and-week
            pairs) and ``purged`` (the cohort dates the embargo excluded
            between train and val); ``metrics`` — ``folds``, ``scored``,
            ``warmup_weeks`` (distinct ISO year-and-week pairs over the
            UNION of the warm-up validation windows, 0 at no warm-up) and
            ``scored_start`` (the first scored fold's ``val_start``: the
            warm-up/scored seam, always defined).

        Raises
        ------
        ValueError
            On a bad row (naming node, row and field), a row dated on or
            past ``holdout_start``, a label that reaches it or outruns
            ``embargo_days``, or too few folds for ``warmup_folds``.
        """
        rows = self._read_cohort(inputs["records"])
        self._check_locked(rows, date.fromisoformat(self.params["holdout_start"]))
        dates = sorted({r.day for r in rows})
        windows = self._windows(dates)
        self._check_scored_remains(windows, len(dates))
        table = [self._fold_row(i, w) for i, w in enumerate(windows, start=1)]
        metrics = self._metrics(windows)
        self.log.info(
            "rolling-origin-plan cut %d date(s) into %d fold(s): %d warm-up over "
            "%d week(s), %d scored from %s",
            len(dates),
            metrics["folds"],
            metrics["folds"] - metrics["scored"],
            metrics["warmup_weeks"],
            metrics["scored"],
            metrics["scored_start"],
        )
        return {"records": table, "metrics": metrics}


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

#: The kinds this module ships, in registration order — the holdout is
#: locked before the folds are cut.
_KINDS = (
    ("holdout-cut", HoldoutCut),
    ("rolling-origin-plan", RollingOriginPlan),
)


def register(registry=None):
    """Register the two protocol kinds, ``owned=False``.

    Idempotent by SKIPPING any name already present — never shadowing an
    existing registration (deliberate re-binding goes through the registry
    itself, which refuses duplicates loudly). Nothing registers at import
    time; calling this is the explicit opt-in.

    Parameters
    ----------
    registry : NodeKindRegistry or None
        Where to register; ``None`` means
        :data:`~dskit.pipeline.node.DEFAULT_NODE_KINDS`.

    Returns
    -------
    NodeKindRegistry
        The same registry, for chaining.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in _KINDS:
        if name not in registry:
            registry.register(name, cls, owned=False)
    return registry
