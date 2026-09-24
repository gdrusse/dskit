"""The evaluation event log: schema v1, its validating kinds, and the census.

One append-only log is the source of truth for a backtest; every report is
a pure function of it (ADR-0183). This module owns the log's shape and
nothing else: an event kind is a class declaring its fields, every object
is validated at construction with EVERY problem listed at once, and a field
the kind does not declare is refused rather than carried — default-deny,
so a producer's typo is an error instead of a silently absent column.

Three rules are the log's, not any one event's, and :class:`EventLog`
enforces them on every append: ``seq`` strictly increases, ``ts_ms`` never
goes backwards, and an id an event references must already have been
logged (an order cannot cite a decision from the future). The look-ahead
rule — a decision's ``known_ms <= ts_ms`` — is the decision's own. The
census is different in kind: a decision marked ``refuse`` with no refusal
event is a finding about the producer, not a malformed log, so
:class:`Census` REPORTS it and the report renders it on the first page.

Lines are written in the canonical JSON spelling the production records
use (:func:`dskit.production.base.canonical_bytes`: sorted keys, compact,
ASCII) and landed with :func:`dskit.pipeline.node.atomic_write`, so two
renders of one run write byte-identical ``events.jsonl`` files.

Import cost: stdlib plus ``dskit.pipeline`` and ``dskit.production``.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dskit.evaluation.criteria import Criterion
from dskit.evaluation.units import SCORE_UNITS
from dskit.pipeline.node import atomic_write
from dskit.pipeline.records import number_ok
from dskit.production.base import canonical_bytes
from dskit.production.release import RuntimeFingerprint
from dskit.production.vocab import VERDICTS

__all__ = [
    "ACTIONS",
    "EVENT_KINDS",
    "SCHEMA",
    "SIDES",
    "Cashflow",
    "Census",
    "Decision",
    "Event",
    "EvaluationError",
    "EventLog",
    "Fill",
    "Links",
    "LocalTime",
    "Mark",
    "Order",
    "Outcome",
    "Refusal",
    "RunEnd",
    "RunStart",
    "Skip",
    "Solve",
]

#: The schema tag every v1 event carries. A new REQUIRED field or a changed
#: meaning moves it; an optional additive field (``run_start.units``,
#: ``run_start.sources`` — ADR-0183 amendment) does not, so every older v1
#: log still reads.
SCHEMA = "dskit-eval-v1"

#: A decision's closed action set.
ACTIONS = ("enter", "exit", "hold", "skip", "refuse")

#: An order's or a fill's side.
SIDES = ("buy", "sell")

#: The envelope every event carries, in rendering order.
_ENVELOPE = ("schema", "seq", "kind", "ts_ms", "known_ms", "instrument")

#: A candidate row's closed field set (``instrument`` required).
_CANDIDATE_FIELDS = ("instrument", "score", "rank", "eligible", "reason")

#: A guard finding's fields on an ``order`` (ADR-0183 phase 2): the
#: production ledger's ``Finding`` with ``value``/``bound`` as numbers.
_FINDING_FIELDS = ("guard", "measure", "value", "bound", "verdict", "reason", "window",
                   "scope_key")

#: A side's sign on quantity — a table, never a side branch.
_SIGN = {"buy": 1, "sell": -1}


class EvaluationError(ValueError):
    """Every problem an event or a log has, reported at once.

    Parameters
    ----------
    problems : list of str
        One line per problem, each naming where it lives.

    Examples
    --------
    ::

        error = EvaluationError(["event[3].seq must increase"])
        error.problems
        # -> ['event[3].seq must increase']
    """

    def __init__(self, problems):
        self.problems = list(problems)
        count = len(self.problems)
        super().__init__(
            f"invalid event log ({count} problem{'s' if count != 1 else ''}):\n  "
            + "\n  ".join(self.problems)
        )


# ---------------------------------------------------------------------------
# Field rules
# ---------------------------------------------------------------------------


def _is_int(value):
    """Say whether ``value`` is an int and not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


class _Field:
    """One declared body field: its name, its check, whether it must appear."""

    def __init__(self, name, check, what, *, required=True, nullable=False):
        self.name, self.check, self.what = name, check, what
        self.required, self.nullable = required, nullable

    def problem(self, where, obj):
        """Return the problem with this field of ``obj``, or None."""
        if self.name not in obj:
            return f"{where}: missing required field {self.name!r}" if self.required else None
        value = obj[self.name]
        if value is None and self.nullable:
            return None
        if not self.check(value):
            return f"{where}.{self.name} must be {self.what}, got {value!r}"
        return None


def _id(name, **flags):
    return _Field(name, lambda v: isinstance(v, str) and v != "", "a non-empty string", **flags)


def _text(name, **flags):
    return _Field(name, lambda v: isinstance(v, str), "a string", **flags)


def _number(name, **flags):
    return _Field(name, number_ok, "a finite number", **flags)


def _positive(name, **flags):
    return _Field(name, lambda v: number_ok(v) and v > 0, "a finite number > 0", **flags)


def _count(name, minimum, **flags):
    return _Field(name, lambda v: _is_int(v) and v >= minimum, f"an int >= {minimum}", **flags)


def _mapping(name, **flags):
    return _Field(
        name,
        lambda v: isinstance(v, dict) and all(isinstance(k, str) for k in v),
        "an object with string keys",
        **flags,
    )


def _sequence(name, **flags):
    return _Field(name, lambda v: isinstance(v, list), "a list", **flags)


def _choice(name, options, **flags):
    return _Field(name, lambda v: v in options, f"one of {list(options)}", **flags)


def _flag(name, **flags):
    return _Field(name, lambda v: isinstance(v, bool), "a boolean", **flags)


# ---------------------------------------------------------------------------
# Event kinds
# ---------------------------------------------------------------------------


class Event:
    """One schema-v1 event: the envelope plus a kind's declared body.

    A subclass declares ``kind``, its ``FIELDS`` and whether its envelope
    ``instrument`` is required; it may add cross-field rules in
    :meth:`extra_problems`. Construction validates, so an invalid event
    cannot exist; :meth:`from_obj` on the base picks the class by
    ``kind``.

    Parameters
    ----------
    obj : dict
        The event as its JSON object.

    Examples
    --------
    ::

        mark = Event.from_obj({"schema": "dskit-eval-v1", "seq": 4, "kind": "mark",
                               "ts_ms": 60000, "known_ms": 60000,
                               "instrument": "AAA", "price": 101.5})
        mark.get("price")  # 101.5
    """

    #: The kind tag this class answers to.
    kind = ""
    #: The body fields, in rendering order.
    FIELDS = ()
    #: Whether the envelope's ``instrument`` must be a non-empty string.
    NEEDS_INSTRUMENT = False

    def __init__(self, obj, where="event"):
        problems = type(self).problems(obj, where)
        if problems:
            raise EvaluationError(problems)
        self.seq = obj["seq"]
        self.ts_ms = obj["ts_ms"]
        self.known_ms = obj["known_ms"]
        self.instrument = obj.get("instrument")
        self._body = {f.name: obj[f.name] for f in type(self).FIELDS if f.name in obj}

    @classmethod
    def from_obj(cls, obj, where="event"):
        """Build the event ``obj`` describes, choosing the class by its ``kind``.

        Parameters
        ----------
        obj : dict
            The event object.
        where : str
            How a refusal names the event.

        Returns
        -------
        Event
            An instance of the kind's class.

        Raises
        ------
        EvaluationError
            Listing every problem with the object.
        """
        if not isinstance(obj, dict):
            raise EvaluationError([f"{where} must be a JSON object, got {obj!r}"])
        kind_class = EVENT_KINDS.get(obj.get("kind"))
        if kind_class is None:
            raise EvaluationError(
                [f"{where}.kind must be one of {sorted(EVENT_KINDS)}, got {obj.get('kind')!r}"]
            )
        if cls is not Event and kind_class is not cls:
            raise EvaluationError([f"{where}.kind {obj['kind']!r} is not a {cls.kind!r} event"])
        return kind_class(obj, where)

    @classmethod
    def problems(cls, obj, where="event"):
        """List every problem with ``obj`` as an event of this kind.

        Parameters
        ----------
        obj : dict
            The event object.
        where : str
            The prefix naming the event in each problem.

        Returns
        -------
        list of str
            Empty when ``obj`` is a valid event of this kind.
        """
        if not isinstance(obj, dict):
            return [f"{where} must be a JSON object, got {obj!r}"]
        problems = cls._envelope_problems(obj, where)
        declared = set(_ENVELOPE) | {f.name for f in cls.FIELDS}
        unknown = sorted(set(obj) - declared)
        if unknown:
            problems.append(
                f"{where}: unknown field(s) {unknown} for kind {cls.kind!r} — "
                f"allowed: {sorted(declared)}"
            )
        problems.extend(p for f in cls.FIELDS if (p := f.problem(where, obj)) is not None)
        if not problems:
            problems.extend(cls.extra_problems(obj, where))
        return problems

    @classmethod
    def _envelope_problems(cls, obj, where):
        """Problems with the six envelope fields."""
        problems = []
        if obj.get("schema") != SCHEMA:
            problems.append(f"{where}.schema must be {SCHEMA!r}, got {obj.get('schema')!r}")
        if obj.get("kind") != cls.kind:
            problems.append(f"{where}.kind must be {cls.kind!r}, got {obj.get('kind')!r}")
        for name in ("seq", "ts_ms", "known_ms"):
            if not _is_int(obj.get(name)) or obj[name] < 0:
                problems.append(f"{where}.{name} must be an int >= 0, got {obj.get(name)!r}")
        instrument = obj.get("instrument")
        if instrument is not None and (not isinstance(instrument, str) or not instrument):
            problems.append(f"{where}.instrument must be a non-empty string or null")
        if cls.NEEDS_INSTRUMENT and instrument is None:
            problems.append(f"{where}: a {cls.kind!r} event needs an instrument")
        return problems

    @classmethod
    def extra_problems(cls, obj, where):
        """Cross-field rules a kind adds; the base has none.

        Parameters
        ----------
        obj : dict
            An object whose envelope and fields already passed.
        where : str
            The prefix naming the event.

        Returns
        -------
        list of str
        """
        return []

    def get(self, name, default=None):
        """Return body field ``name``, or ``default`` when the event omits it.

        Parameters
        ----------
        name : str
            A declared field.
        default : object
            What an absent field reads as.

        Returns
        -------
        object
        """
        return self._body.get(name, default)

    def to_obj(self):
        """Return the event as its JSON object, optional fields only when present.

        Returns
        -------
        dict
        """
        return {
            "schema": SCHEMA,
            "seq": self.seq,
            "kind": self.kind,
            "ts_ms": self.ts_ms,
            "known_ms": self.known_ms,
            "instrument": self.instrument,
            **self._body,
        }

    def __repr__(self):
        """Name the kind and its position in the log."""
        return f"<{type(self).__name__} seq={self.seq} ts_ms={self.ts_ms}>"


class RunStart(Event):
    """The run's identity, configuration, provenance and pre-registered criteria.

    ``tz`` is the IANA zone the report RENDERS in; every instant stays UTC
    epoch ms. ``criteria`` are validated as :class:`Criterion` objects,
    and ``trials`` (configurations tried) feeds the deflated Sharpe.

    Examples
    --------
    ::

        start = Event.from_obj({"schema": "dskit-eval-v1", "seq": 0, "kind": "run_start",
                                "ts_ms": 0, "known_ms": 0, "instrument": None,
                                "run_id": "r1", "tz": "America/New_York"})
        start.get("tz")  # 'America/New_York'
    """

    kind = "run_start"
    FIELDS = (
        _id("run_id"),
        _text("title", required=False),
        _text("project", required=False),
        _id("tz"),
        _text("config_hash", required=False),
        _mapping("config", required=False),
        _mapping("code", required=False),
        _mapping("data", required=False),
        _mapping("env", required=False),
        _sequence("criteria", required=False),
        _count("trials", 1, required=False),
        _mapping("units", required=False),
        _mapping("sources", required=False),
    )

    #: The keys ``units`` may declare.
    UNIT_KEYS = ("score", "money")

    @classmethod
    def extra_problems(cls, obj, where):
        """Refuse an unknown zone, malformed criteria, units or sources."""
        problems = []
        units = obj.get("units", {})
        unknown = sorted(set(units) - set(cls.UNIT_KEYS))
        if unknown:
            problems.append(f"{where}.units: unknown key(s) {unknown} — allowed: "
                            f"{list(cls.UNIT_KEYS)}")
        if "score" in units and units["score"] not in SCORE_UNITS:
            problems.append(f"{where}.units.score must be one of {sorted(SCORE_UNITS)}, "
                            f"got {units['score']!r}")
        if "money" in units and not (isinstance(units["money"], str) and units["money"]):
            problems.append(f"{where}.units.money must be a currency code, "
                            f"got {units['money']!r}")
        bad = sorted(k for k, v in obj.get("sources", {}).items() if not isinstance(v, str))
        if bad:
            problems.append(f"{where}.sources values must be strings; not for {bad}")
        try:
            ZoneInfo(obj["tz"])
        except (ZoneInfoNotFoundError, ValueError):
            problems.append(f"{where}.tz {obj['tz']!r} is not an IANA time zone")
        for position, item in enumerate(obj.get("criteria", ())):
            problems.extend(Criterion.problems(item, f"{where}.criteria[{position}]"))
        return problems

    @property
    def criteria(self):
        """The pre-registered criteria, as :class:`Criterion` objects."""
        return tuple(Criterion.from_obj(item) for item in self.get("criteria", ()))

    @staticmethod
    def capture_env(packages=()):
        """Capture this interpreter for the ``env`` field.

        Reads :class:`dskit.production.release.RuntimeFingerprint` — the
        one owner of runtime capture — and keeps the python version, the
        platform and the versions of the named distributions.

        Parameters
        ----------
        packages : iterable of str
            Distribution names to pin; empty keeps every installed one.

        Returns
        -------
        dict
            ``{"python", "platform", "packages": {name: version}}``.
        """
        fingerprint = RuntimeFingerprint.capture()
        wanted = {name.lower() for name in packages}
        versions = {
            dist.name: dist.version
            for dist in fingerprint.distributions
            if not wanted or dist.name.lower() in wanted
        }
        return {
            "python": fingerprint.python_version,
            "platform": fingerprint.platform,
            "packages": dict(sorted(versions.items())),
        }


class Decision(Event):
    """One decision: what was scored, what was chosen, and why.

    ``known_ms <= ts_ms`` is enforced — a decision may not rest on
    information that arrived after it was taken. ``candidates`` are the
    point-in-time universe with each name's score, rank, eligibility and
    ineligibility reason; ``chosen`` must be one of them when both are
    present.

    Examples
    --------
    ::

        decision = Event.from_obj({"schema": "dskit-eval-v1", "seq": 1, "kind": "decision",
                                   "ts_ms": 60000, "known_ms": 60000, "instrument": None,
                                   "decision_id": "d1", "action": "hold",
                                   "reason": "below_threshold"})
        decision.get("action")  # 'hold'
    """

    kind = "decision"
    FIELDS = (
        _id("decision_id"),
        _sequence("candidates", required=False),
        _id("chosen", required=False, nullable=True),
        _number("threshold", required=False, nullable=True),
        _number("edge", required=False, nullable=True),
        _choice("action", ACTIONS),
        _id("reason"),
        _text("detail", required=False),
        _text("model", required=False),
    )

    @classmethod
    def extra_problems(cls, obj, where):
        """Refuse look-ahead, malformed candidates and a chosen name outside them."""
        problems = []
        if obj["known_ms"] > obj["ts_ms"]:
            problems.append(
                f"{where}: look-ahead — known_ms {obj['known_ms']} is after the "
                f"decision instant ts_ms {obj['ts_ms']}"
            )
        names = []
        for position, row in enumerate(obj.get("candidates", ())):
            problems.extend(_candidate_problems(row, f"{where}.candidates[{position}]"))
            if isinstance(row, dict):
                names.append(row.get("instrument"))
        chosen = obj.get("chosen")
        if chosen is not None and names and chosen not in names:
            problems.append(f"{where}.chosen {chosen!r} is not among the candidates")
        return problems

    @property
    def candidates(self):
        """The candidate rows, ordered by rank then score (best first)."""
        rows = list(self.get("candidates", ()))
        return sorted(rows, key=_candidate_order)

    def chosen_row(self):
        """Return the chosen candidate's row, or None.

        Returns
        -------
        dict or None
        """
        chosen = self.get("chosen")
        return next((row for row in self.candidates if row["instrument"] == chosen), None)

    def runner_up(self):
        """Return the best candidate that was not chosen, or None.

        Returns
        -------
        dict or None
        """
        chosen = self.get("chosen")
        return next((row for row in self.candidates if row["instrument"] != chosen), None)


def _candidate_problems(row, where):
    """Problems with one candidate row (default-deny, instrument required)."""
    if not isinstance(row, dict):
        return [f"{where} must be an object, got {row!r}"]
    problems = []
    unknown = sorted(set(row) - set(_CANDIDATE_FIELDS))
    if unknown:
        problems.append(f"{where}: unknown field(s) {unknown} — allowed: {list(_CANDIDATE_FIELDS)}")
    rules = (
        _id("instrument"),
        _number("score", required=False, nullable=True),
        _count("rank", 1, required=False, nullable=True),
        _flag("eligible", required=False),
        _text("reason", required=False, nullable=True),
    )
    problems.extend(p for rule in rules if (p := rule.problem(where, row)) is not None)
    return problems


def _finding_problems(row, where):
    """Problems with one guard finding (default-deny; guard, measure, verdict required)."""
    if not isinstance(row, dict):
        return [f"{where} must be an object, got {row!r}"]
    problems = []
    unknown = sorted(set(row) - set(_FINDING_FIELDS))
    if unknown:
        problems.append(f"{where}: unknown field(s) {unknown} — allowed: {list(_FINDING_FIELDS)}")
    rules = (
        _id("guard"),
        _id("measure"),
        _number("value", required=False, nullable=True),
        _number("bound", required=False, nullable=True),
        _choice("verdict", VERDICTS),
        _text("reason", required=False, nullable=True),
        _text("window", required=False, nullable=True),
        _text("scope_key", required=False, nullable=True),
    )
    problems.extend(p for rule in rules if (p := rule.problem(where, row)) is not None)
    return problems


def _candidate_order(row):
    """Sort key: ranked rows first by rank, then by descending score."""
    rank = row.get("rank")
    score = row.get("score")
    return (rank is None, rank or 0, score is None, -(score or 0.0))


class Order(Event):
    """An order a decision sent: side, quantity, the reference price it was sized on.

    Examples
    --------
    ::

        order = Event.from_obj({"schema": "dskit-eval-v1", "seq": 2, "kind": "order",
                                "ts_ms": 60000, "known_ms": 60000, "instrument": "AAA",
                                "order_id": "o1", "decision_id": "d1", "side": "buy",
                                "qty": 10, "ref_price": 100.0})
        order.get("qty")  # 10
    """

    kind = "order"
    NEEDS_INSTRUMENT = True
    FIELDS = (
        _id("order_id"),
        _id("decision_id", required=False, nullable=True),
        _choice("side", SIDES),
        _positive("qty"),
        _positive("ref_price", required=False, nullable=True),
        _sequence("legs", required=False),
        _sequence("findings", required=False),
    )

    @classmethod
    def extra_problems(cls, obj, where):
        """Refuse a malformed guard finding (phase 2: the ledger's pre-trade checks)."""
        return [p for position, row in enumerate(obj.get("findings") or ())
                for p in _finding_problems(row, f"{where}.findings[{position}]")]


class _Rejection(Event):
    """The shared shape of a refusal and a skip: what it rejects and why."""

    FIELDS = (
        _id("decision_id", required=False, nullable=True),
        _id("order_id", required=False, nullable=True),
        _id("reason"),
        _text("detail", required=False),
    )

    @classmethod
    def extra_problems(cls, obj, where):
        """Refuse a rejection that names neither a decision nor an order."""
        if obj.get("decision_id") is None and obj.get("order_id") is None:
            return [f"{where}: a {cls.kind!r} must name a decision_id or an order_id"]
        return []


class Refusal(_Rejection):
    """Something refused a decision or an order (a guard, the venue, a limit).

    Examples
    --------
    ::

        refusal = Event.from_obj({"schema": "dskit-eval-v1", "seq": 3, "kind": "refusal",
                                  "ts_ms": 60000, "known_ms": 60000, "instrument": "AAA",
                                  "order_id": "o1", "reason": "max_exposure"})
        refusal.get("reason")  # 'max_exposure'
    """

    kind = "refusal"


class Skip(_Rejection):
    """A decision or order deliberately not acted on (no bar, no liquidity, ...).

    Examples
    --------
    ::

        skip = Event.from_obj({"schema": "dskit-eval-v1", "seq": 3, "kind": "skip",
                               "ts_ms": 60000, "known_ms": 60000, "instrument": None,
                               "decision_id": "d1", "reason": "no_bar"})
        skip.get("reason")  # 'no_bar'
    """

    kind = "skip"


class Fill(Event):
    """An execution: quantity at a price, with its fee.

    Examples
    --------
    ::

        fill = Event.from_obj({"schema": "dskit-eval-v1", "seq": 3, "kind": "fill",
                               "ts_ms": 60000, "known_ms": 60000, "instrument": "AAA",
                               "fill_id": "f1", "order_id": "o1", "side": "buy",
                               "qty": 10, "price": 100.05, "fee": 0.5})
        fill.signed_qty  # 10
    """

    kind = "fill"
    NEEDS_INSTRUMENT = True
    FIELDS = (
        _id("fill_id"),
        _id("order_id", required=False, nullable=True),
        _choice("side", SIDES),
        _positive("qty"),
        _positive("price"),
        _number("fee", required=False),
        _positive("ref_price", required=False, nullable=True),
        _text("tag", required=False),
    )

    @property
    def signed_qty(self):
        """The quantity with the side's sign: ``+qty`` bought, ``-qty`` sold."""
        return _SIGN[self.get("side")] * self.get("qty")

    @property
    def fee(self):
        """The fee, 0 when the event omits it."""
        return self.get("fee", 0)

    def slippage_bp(self, ref_price):
        """Return the fill's cost against ``ref_price`` in basis points.

        Positive is a cost: paying above the reference on a buy, receiving
        below it on a sell.

        Parameters
        ----------
        ref_price : float or None
            The price the decision was sized on.

        Returns
        -------
        float or None
            ``None`` without a reference.
        """
        if ref_price is None:
            return None
        return _SIGN[self.get("side")] * (self.get("price") - ref_price) / ref_price * 1e4


class Mark(Event):
    """A price observation (bar close or mid) that marks open positions.

    Examples
    --------
    ::

        mark = Event.from_obj({"schema": "dskit-eval-v1", "seq": 5, "kind": "mark",
                               "ts_ms": 120000, "known_ms": 120000, "instrument": "AAA",
                               "price": 100.2})
        mark.get("price")  # 100.2
    """

    kind = "mark"
    NEEDS_INSTRUMENT = True
    FIELDS = (_positive("price"),)


class Cashflow(Event):
    """External cash in (+) or out (-): a deposit is never a return.

    Examples
    --------
    ::

        flow = Event.from_obj({"schema": "dskit-eval-v1", "seq": 1, "kind": "cashflow",
                               "ts_ms": 0, "known_ms": 0, "instrument": None,
                               "amount": 10000, "rule": "initial"})
        flow.get("amount")  # 10000
    """

    kind = "cashflow"
    FIELDS = (_number("amount"), _text("rule", required=False), _text("detail", required=False))


class Outcome(Event):
    """What a decision's forecast turned into, logged when it became known.

    Never read by a section that renders the decision it scores; it must
    come strictly after that decision (checked by :class:`EventLog`).

    Examples
    --------
    ::

        outcome = Event.from_obj({"schema": "dskit-eval-v1", "seq": 9, "kind": "outcome",
                                  "ts_ms": 360000, "known_ms": 360000, "instrument": "AAA",
                                  "decision_id": "d1", "horizon": "5m", "realized": 0.0012})
        outcome.get("realized")  # 0.0012
    """

    kind = "outcome"
    FIELDS = (
        _id("decision_id"),
        _Field("horizon", lambda v: isinstance(v, str) or number_ok(v), "a string or number",
               required=False),
        _number("realized"),
    )


class Solve(Event):
    """An optimizer call's status (ADR-0183 phase 2; accepted, not yet rendered).

    Examples
    --------
    ::

        solve = Event.from_obj({"schema": "dskit-eval-v1", "seq": 2, "kind": "solve",
                                "ts_ms": 60000, "known_ms": 60000, "instrument": None,
                                "solver": "highs", "status": "optimal"})
        solve.get("status")  # 'optimal'
    """

    kind = "solve"
    FIELDS = (
        _id("solver"),
        _id("status"),
        _number("objective", required=False, nullable=True),
        _number("bound", required=False, nullable=True),
        _number("gap", required=False, nullable=True),
        _sequence("binding", required=False),
        _number("seconds", required=False),
    )


class RunEnd(Event):
    """The run's terminal status and wall time.

    Examples
    --------
    ::

        end = Event.from_obj({"schema": "dskit-eval-v1", "seq": 99, "kind": "run_end",
                              "ts_ms": 900000, "known_ms": 900000, "instrument": None,
                              "status": "ok", "wall_s": 12.5})
        end.get("wall_s")  # 12.5
    """

    kind = "run_end"
    FIELDS = (_id("status"), _number("wall_s", required=False))


#: Every v1 kind, keyed by its tag — a table, filled by literal, never by
#: an import-time registration call.
EVENT_KINDS = {
    cls.kind: cls
    for cls in (RunStart, Decision, Order, Refusal, Skip, Fill, Mark, Cashflow, Outcome,
                Solve, RunEnd)
}


# ---------------------------------------------------------------------------
# Time rendering
# ---------------------------------------------------------------------------


class LocalTime:
    """Render UTC epoch-ms instants in the run's display zone.

    The one place an instant becomes wall-clock text or a session day;
    storage never leaves UTC.

    Parameters
    ----------
    tz : str
        An IANA zone name.

    Examples
    --------
    ::

        LocalTime("America/New_York").stamp(0)  # '1969-12-31 19:00:00'
    """

    def __init__(self, tz="UTC"):
        self.tz = tz
        self.zone = ZoneInfo(tz)

    def at(self, ms):
        """Return the aware local datetime of ``ms``.

        Parameters
        ----------
        ms : int
            Epoch milliseconds, UTC.

        Returns
        -------
        datetime.datetime
        """
        return datetime.fromtimestamp(ms / 1000.0, timezone.utc).astimezone(self.zone)

    def day(self, ms):
        """Return the local calendar day of ``ms`` as ``YYYY-MM-DD``."""
        return self.at(ms).strftime("%Y-%m-%d")

    def stamp(self, ms):
        """Return ``ms`` as local ``YYYY-MM-DD HH:MM:SS``."""
        return self.at(ms).strftime("%Y-%m-%d %H:%M:%S")

    def offset_ms(self, ms):
        """Return the zone's UTC offset at ``ms``, in milliseconds."""
        return int(self.at(ms).utcoffset().total_seconds() * 1000)

    def label(self, ms, fmt):
        """Return ``ms`` formatted with the ``strftime`` pattern ``fmt``."""
        return self.at(ms).strftime(fmt)


# ---------------------------------------------------------------------------
# Links and the census
# ---------------------------------------------------------------------------


class Links:
    """The id graph of a log: decision -> orders -> fills, and what rejected each.

    Built once per log; every section that joins a fill to its "why" reads
    it rather than re-scanning.

    Parameters
    ----------
    events : sequence of Event
        A validated log's events, in order.

    Examples
    --------
    ::

        links = Links(log.events)
        links.decision_of_fill(fill).get("reason")  # 'edge_above_threshold'
    """

    def __init__(self, events):
        self.decisions, self.orders = {}, {}
        self.orders_of = defaultdict(list)
        self.fills_of = defaultdict(list)
        self.rejections_of_decision = defaultdict(list)
        self.rejections_of_order = defaultdict(list)
        self.outcomes_of = defaultdict(list)
        for event in events:
            self._link(event)

    def _link(self, event):
        """File one event under the ids it carries."""
        if isinstance(event, Decision):
            self.decisions[event.get("decision_id")] = event
        elif isinstance(event, Order):
            self.orders[event.get("order_id")] = event
            if event.get("decision_id") is not None:
                self.orders_of[event.get("decision_id")].append(event)
        elif isinstance(event, Fill):
            self.fills_of[event.get("order_id")].append(event)
        elif isinstance(event, _Rejection):
            if event.get("decision_id") is not None:
                self.rejections_of_decision[event.get("decision_id")].append(event)
            if event.get("order_id") is not None:
                self.rejections_of_order[event.get("order_id")].append(event)
        elif isinstance(event, Outcome):
            self.outcomes_of[event.get("decision_id")].append(event)

    def decision_of_fill(self, fill):
        """Return the decision behind ``fill`` (through its order), or None.

        Parameters
        ----------
        fill : Fill

        Returns
        -------
        Decision or None
        """
        order = self.orders.get(fill.get("order_id"))
        return None if order is None else self.decisions.get(order.get("decision_id"))

    def decision_of_rejection(self, rejection):
        """Return the decision a refusal or skip rejects, directly or via its order.

        Parameters
        ----------
        rejection : Refusal or Skip

        Returns
        -------
        Decision or None
        """
        decision_id = rejection.get("decision_id")
        if decision_id is None:
            order = self.orders.get(rejection.get("order_id"))
            decision_id = None if order is None else order.get("decision_id")
        return self.decisions.get(decision_id)

    def fills_of_decision(self, decision_id):
        """Return every fill of every order the decision sent.

        Parameters
        ----------
        decision_id : str

        Returns
        -------
        list of Fill
        """
        return [
            fill
            for order in self.orders_of.get(decision_id, ())
            for fill in self.fills_of.get(order.get("order_id"), ())
        ]

    def rejections_of(self, decision_id):
        """Return every refusal or skip of the decision, direct or via its orders.

        Parameters
        ----------
        decision_id : str

        Returns
        -------
        list of Event
        """
        found = list(self.rejections_of_decision.get(decision_id, ()))
        for order in self.orders_of.get(decision_id, ()):
            found.extend(self.rejections_of_order.get(order.get("order_id"), ()))
        return found

    def ref_price(self, fill):
        """Return the reference a fill is judged against: its own, else its order's.

        Parameters
        ----------
        fill : Fill

        Returns
        -------
        float or None
        """
        if fill.get("ref_price") is not None:
            return fill.get("ref_price")
        order = self.orders.get(fill.get("order_id"))
        return None if order is None else order.get("ref_price")


class Census:
    """Does every decision and order account for itself? Reported, never raised.

    The identity ``decisions == entered + exited + held + skipped +
    refused`` holds by the closed action set; what can FAIL is the
    evidence behind each action: a ``refuse`` decision with no refusal
    event, a ``skip`` with no skip event, an ``enter``/``exit`` with no
    order and no rejection, and an order that was neither filled nor
    rejected. Each is a silently dropped outcome the producer owes.

    Parameters
    ----------
    events : sequence of Event
    links : Links

    Examples
    --------
    ::

        census = Census(log.events, log.links)
        census.ok  # True when nothing is unaccounted for
    """

    def __init__(self, events, links):
        self.kinds = Counter(event.kind for event in events)
        decisions = [e for e in events if isinstance(e, Decision)]
        self.actions = Counter(d.get("action") for d in decisions)
        self.decisions = len(decisions)
        self.problems = []
        self._check_decisions(decisions, links)
        self.order_states = self._order_states(links)
        self.unlinked_fills = len(links.fills_of.get(None, ()))

    def _check_decisions(self, decisions, links):
        """Name every decision whose action lacks the evidence it implies."""
        missing = defaultdict(list)
        for decision in decisions:
            decision_id, action = decision.get("decision_id"), decision.get("action")
            rejections = links.rejections_of(decision_id)
            kinds = {r.kind for r in rejections}
            if action == "refuse" and "refusal" not in kinds:
                missing["refuse decision(s) with no refusal event"].append(decision_id)
            elif action == "skip" and "skip" not in kinds:
                missing["skip decision(s) with no skip event"].append(decision_id)
            elif action in ("enter", "exit") and not (links.orders_of.get(decision_id)
                                                      or rejections):
                missing["enter/exit decision(s) with no order and no rejection"].append(
                    decision_id
                )
        for what, ids in missing.items():
            shown = ", ".join(ids[:5]) + (" ..." if len(ids) > 5 else "")
            self.problems.append(f"{len(ids)} {what}: {shown}")

    def _order_states(self, links):
        """Classify every order: filled, partial, rejected or unaccounted."""
        states = Counter()
        over = []
        for order_id, order in links.orders.items():
            filled = sum(f.get("qty") for f in links.fills_of.get(order_id, ()))
            if filled > order.get("qty") * (1 + 1e-9):
                over.append(order_id)
            if filled >= order.get("qty") * (1 - 1e-9):
                states["filled"] += 1
            elif filled > 0:
                states["partial"] += 1
            elif links.rejections_of_order.get(order_id):
                states["rejected"] += 1
            else:
                states["unaccounted"] += 1
        if states["unaccounted"]:
            self.problems.append(
                f"{states['unaccounted']} order(s) with no fill and no refusal or skip"
            )
        if over:
            self.problems.append(f"{len(over)} order(s) filled beyond their quantity: "
                                 + ", ".join(over[:5]))
        return states

    @property
    def ok(self):
        """True when nothing is unaccounted for."""
        return not self.problems

    def to_obj(self):
        """Return the census as a JSON-ready dict.

        Returns
        -------
        dict
            ``decisions``, ``actions`` (every member of :data:`ACTIONS`),
            ``identity`` (bool), ``orders``, ``fills``, ``refusals``,
            ``skips``, ``order_states``, ``unlinked_fills`` (fills naming
            no order), ``problems``, ``ok``.
        """
        actions = {action: self.actions.get(action, 0) for action in ACTIONS}
        return {
            "decisions": self.decisions,
            "actions": actions,
            "identity": self.decisions == sum(actions.values()),
            "orders": self.kinds.get("order", 0),
            "fills": self.kinds.get("fill", 0),
            "refusals": self.kinds.get("refusal", 0),
            "skips": self.kinds.get("skip", 0),
            "order_states": dict(sorted(self.order_states.items())),
            "unlinked_fills": self.unlinked_fills,
            "problems": list(self.problems),
            "ok": self.ok,
        }


# ---------------------------------------------------------------------------
# The log
# ---------------------------------------------------------------------------

#: What each referencing kind's id fields must point at: (field, kind of the target).
_REFERENCES = {
    "order": (("decision_id", "decision"),),
    "fill": (("order_id", "order"),),
    "refusal": (("decision_id", "decision"), ("order_id", "order")),
    "skip": (("decision_id", "decision"), ("order_id", "order")),
    "outcome": (("decision_id", "decision"),),
}

#: The field that identifies an event of each kind that has one.
_IDENTITIES = {"decision": "decision_id", "order": "order_id", "fill": "fill_id"}


class EventLog:
    """An ordered, validated sequence of events — the report's only input.

    Every append is checked against the log so far: the first event is
    ``run_start`` and appears once, nothing follows ``run_end``, ``seq``
    strictly increases, ``ts_ms`` never decreases, ids are unique per
    kind, a referenced id was logged earlier, and an outcome comes after
    the decision it scores. Construction from many objects checks them
    ALL and raises once with every problem.

    Parameters
    ----------
    events : iterable of Event or dict
        The events in log order.

    Examples
    --------
    ::

        log = EventLog()
        log.emit("run_start", 0, 0, run_id="r1", tz="UTC")
        log.emit("cashflow", 0, 0, amount=10000, rule="initial")
        len(log)  # 2
    """

    def __init__(self, events=()):
        self._events = []
        self._ids = defaultdict(dict)
        self._links = None
        problems = []
        for position, item in enumerate(events):
            try:
                self.append(item, where=f"event[{position}]")
            except EvaluationError as exc:
                problems.extend(exc.problems)
        if problems:
            raise EvaluationError(problems)

    # -- building ----------------------------------------------------------

    def append(self, item, where=None):
        """Validate ``item`` against the log so far and append it.

        Parameters
        ----------
        item : Event or dict
            The next event.
        where : str or None
            How a refusal names it; defaults to its position.

        Returns
        -------
        Event
            The appended event.

        Raises
        ------
        EvaluationError
            Listing every problem; nothing is appended.
        """
        where = where or f"event[{len(self._events)}]"
        event = item if isinstance(item, Event) else Event.from_obj(item, where)
        problems = self._order_problems(event, where) + self._reference_problems(event, where)
        if problems:
            raise EvaluationError(problems)
        self._events.append(event)
        identity = _IDENTITIES.get(event.kind)
        if identity is not None:
            self._ids[event.kind][event.get(identity)] = event
        self._links = None
        return event

    def emit(self, kind, ts_ms, known_ms, instrument=None, **fields):
        """Append a ``kind`` event with the next ``seq`` and the schema tag.

        Parameters
        ----------
        kind : str
            A member of :data:`EVENT_KINDS`.
        ts_ms, known_ms : int
            The event instant and when its information was available.
        instrument : str or None
            The envelope instrument.
        **fields
            The kind's body fields.

        Returns
        -------
        Event

        Raises
        ------
        EvaluationError
            As :meth:`append`.
        """
        seq = self._events[-1].seq + 1 if self._events else 0
        return self.append({
            "schema": SCHEMA, "seq": seq, "kind": kind, "ts_ms": ts_ms,
            "known_ms": known_ms, "instrument": instrument, **fields,
        })

    def _order_problems(self, event, where):
        """Check log position: run_start first, run_end last, seq and ts order."""
        problems = []
        if not self._events and event.kind != "run_start":
            problems.append(f"{where}: the first event must be run_start, got {event.kind!r}")
        if self._events and event.kind == "run_start":
            problems.append(f"{where}: a log has exactly one run_start")
        if not self._events:
            return problems
        last = self._events[-1]
        if last.kind == "run_end":
            problems.append(f"{where}: nothing may follow run_end")
        if event.seq <= last.seq:
            problems.append(f"{where}.seq {event.seq} must exceed the previous seq {last.seq}")
        if event.ts_ms < last.ts_ms:
            problems.append(
                f"{where}.ts_ms {event.ts_ms} goes backwards (previous {last.ts_ms})"
            )
        return problems

    def _reference_problems(self, event, where):
        """Check unique ids, earlier-logged references, outcomes strictly after their decision."""
        problems = []
        identity = _IDENTITIES.get(event.kind)
        if identity is not None and event.get(identity) in self._ids[event.kind]:
            problems.append(f"{where}.{identity} {event.get(identity)!r} is already logged")
        for field, target in _REFERENCES.get(event.kind, ()):
            value = event.get(field)
            if value is not None and value not in self._ids[target]:
                problems.append(f"{where}.{field} {value!r} names no earlier {target}")
        if isinstance(event, Outcome):
            decision = self._ids["decision"].get(event.get("decision_id"))
            if decision is not None and event.ts_ms <= decision.ts_ms:
                problems.append(f"{where}: an outcome must come after its decision's ts_ms")
        return problems

    # -- reading -------------------------------------------------------------

    @property
    def events(self):
        """Every event, in log order, as a tuple."""
        return tuple(self._events)

    def __len__(self):
        """Return the number of events."""
        return len(self._events)

    def __iter__(self):
        """Iterate the events in log order."""
        return iter(self._events)

    def of_kind(self, *kinds):
        """Return the events of the given kinds, in log order.

        Parameters
        ----------
        *kinds : str

        Returns
        -------
        list of Event
        """
        return [event for event in self._events if event.kind in kinds]

    @property
    def run_start(self):
        """The ``run_start`` event, or None for an empty log."""
        return self._events[0] if self._events else None

    @property
    def run_end(self):
        """The ``run_end`` event, or None when the run did not close."""
        return self._events[-1] if self._events and self._events[-1].kind == "run_end" else None

    @property
    def local_time(self):
        """The run's display zone as a :class:`LocalTime` (UTC for an empty log)."""
        start = self.run_start
        return LocalTime(start.get("tz") if start is not None else "UTC")

    @property
    def links(self):
        """The id graph, built once per log state."""
        if self._links is None:
            self._links = Links(self._events)
        return self._links

    def census(self):
        """Return the census of decisions, orders, fills and rejections.

        Returns
        -------
        Census
        """
        return Census(self._events, self.links)

    def span(self, *kinds):
        """Return the first and last instant of the given kinds, or None when there are none.

        Parameters
        ----------
        *kinds : str
            Event kinds; none means every event but ``run_start`` / ``run_end``.

        Returns
        -------
        tuple of (int, int) or None

        Examples
        --------
        ::

            log.span("decision")  # the decision window, (first_ms, last_ms)
        """
        if kinds:
            stamps = [e.ts_ms for e in self._events if e.kind in kinds]
        else:
            stamps = [e.ts_ms for e in self._events if e.kind not in ("run_start", "run_end")]
        return (min(stamps), max(stamps)) if stamps else None

    def instruments(self):
        """Return every instrument the log names, sorted.

        Returns
        -------
        list of str
        """
        return sorted({e.instrument for e in self._events if e.instrument is not None})

    # -- files ---------------------------------------------------------------

    def to_jsonl(self):
        """Return the log as canonical newline-JSON bytes.

        Returns
        -------
        bytes
        """
        return b"".join(canonical_bytes(event.to_obj()) + b"\n" for event in self._events)

    def write(self, path):
        """Write the log to ``path`` atomically.

        Parameters
        ----------
        path : str
        """
        atomic_write(path, self.to_jsonl())

    @classmethod
    def read(cls, path):
        """Read and validate a JSONL event file, refusing with every problem.

        Parameters
        ----------
        path : str

        Returns
        -------
        EventLog

        Raises
        ------
        EvaluationError
            Naming every unparseable line and every invalid event.
        """
        objs, problems = [], []
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    objs.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    problems.append(f"{path}:{number}: not JSON ({exc.msg})")
        if problems:
            raise EvaluationError(problems)
        return cls(objs)

