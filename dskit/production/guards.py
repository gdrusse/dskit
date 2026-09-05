"""Guards — one hook, a closed verdict lattice, every finding recorded (D9, §5.5).

A guard is the last thing between a number a model produced and money
leaving the process, so this module is mostly refusals, and its shape is
the one D9 fixes: ``Guard.check(proposal, state) -> Finding``, verdicts
composing by ``max`` over ``allow < warn < amend < refuse < hold < halt``,
and every finding recorded with its value, bound and reason whether or
not it bound. :class:`Limit` is ONE class parameterised by measure x
window x bound x scope over a registry of :class:`Measure` objects — the
reuse win §5.15 names — plus the ``pkg.module:Class`` doorway a child
uses for its own exposure formula; :class:`RangeGuard` is the sanity
check on a raw proposal field. Default-deny reaches every knob and every
knob PAIR: a measure declares which windows and which scopes it can
answer, and a ``Limit`` that asks a question its measure cannot answer
refuses at construction rather than measuring zero forever.

Three rules of the evaluation are structural rather than configurable.

* **Evidence is the account's.** A ``Measure`` reads ``state.account`` —
  the correction-aware snapshot with prior legs' reservations folded —
  and never ``state.view.positions/working/balances``, the fold at head
  (§5.8.1 ``ECONOMIC_ATTRS``; an AST test pins it). History-backed
  measures (``pnl``, ``drawdown``, ...) declare an
  ``EvidenceRequirement`` up front, :meth:`GuardChain.requirements`
  unions and deduplicates them for ``Accounting.snapshot``, and at check
  time the measure rebuilds the same digest from ``state.account.asof_ms``
  and ``state.calendar`` (ruling R8) and refuses when the answer is
  absent, stale, or learned after the snapshot.
* **A cash flow is not profit** (§6). ``pnl``, ``drawdown``,
  ``consecutive_losses`` and ``error_vs_realised`` read
  ``measure_evidence`` only; ``bankroll_fraction`` and ``exposure`` read
  the capital base including an external flow. The partition is pinned
  by value and by AST.
* **Amendments only reduce.** Every guard first judges the ORIGINAL
  proposal; amendments on one scalable field compose to the smaller
  value, amendments on two fields refuse, and the final candidate is
  re-run through every ``hard`` guard — which, because ``hard`` excludes
  ``amend``, IS the "amendment disabled" second pass. ``amend`` rounds
  toward zero and is postcondition-checked; an infeasible amendment is a
  ``refuse``.

No branch here reads a kind: windows dispatch through :class:`Window`'s
hook table, breach responses through :data:`BREACH_VERDICTS`, scopes
through :class:`Scope`'s hook table, and measures are classes in
:data:`MEASURE_KINDS`. Nothing reads a clock — ``state.account.asof_ms``
is "now" for a hold — and nothing reads the permission ladder.

Import cost: stdlib plus ``dskit.production.{base, records, redact,
release, vocab}`` and ``dskit.pipeline.node`` (the parameter checkers).
"""

import dataclasses
import math
import typing
from abc import ABC, abstractmethod
from decimal import ROUND_DOWN, Decimal, InvalidOperation, localcontext
from types import MappingProxyType

from dskit.pipeline.node import check_int_param
from dskit.production.base import (
    ProductionError,
    Registry,
    _check_dict,
    _check_str,
    _check_unknown,
    reject_unknown_params,
)
from dskit.production.records import EvidenceRequirement, Finding, Proposal, ScopeVerdict
from dskit.production.redact import redact
from dskit.production.release import parse_iso_duration
from dskit.production.vocab import (
    CALENDAR_WINDOWS,
    GUARD_STATE_KINDS,
    LIMIT_SCOPES,
    NAN_POLICY,
    ON_BREACH,
    SIDES,
    VERDICT_ORDER,
    VERDICTS,
    WINDOW_KINDS,
)

__all__ = [
    "AGGREGATE_SCOPE_KEY",
    "BREACH_VERDICTS",
    "BankrollFraction",
    "Bound",
    "Confidence",
    "ConsecutiveLosses",
    "DEFAULT_INCLUDE_WORKING",
    "DEFAULT_NAN_POLICY",
    "DEFAULT_SCOPE",
    "DecisionCount",
    "DirectionChanges",
    "Drawdown",
    "ErrorVsRealised",
    "Exposure",
    "ExposureAfter",
    "FeedAgeMs",
    "GUARD_KINDS",
    "Guard",
    "GuardChain",
    "IdenticalCount",
    "InputAgeMs",
    "Limit",
    "MEASURE_KINDS",
    "Measure",
    "Notional",
    "OpenOrders",
    "Pnl",
    "PriceDeviation",
    "Quantity",
    "RangeGuard",
    "Scope",
    "Window",
    "max_verdict",
]

#: The scope key an ``aggregate`` limit measures under, and the key
#: accounting answers an aggregate requirement with.
AGGREGATE_SCOPE_KEY = "*"

#: A ``Limit``'s defaults — each named once, read by validation and the
#: run alike.
DEFAULT_SCOPE = "aggregate"
DEFAULT_INCLUDE_WORKING = True

#: A ``RangeGuard``'s default NaN policy.
DEFAULT_NAN_POLICY = "refuse"

#: D9's breach responses as the verdict each produces — a table, so
#: ``pause`` becoming ``hold`` is a fact and not a branch.
BREACH_VERDICTS = {
    "refuse": "refuse",
    "amend": "amend",
    "pause": "hold",
    "hold": "hold",
    "halt": "halt",
}
if set(BREACH_VERDICTS) != set(ON_BREACH) or not set(BREACH_VERDICTS.values()) <= set(VERDICTS):
    raise ProductionError(["guards.py: BREACH_VERDICTS does not cover ON_BREACH"])

#: The verdicts that mean "the bound bound" — everything below them
#: admits the proposal at the authority-scope gate.
_BREACHING = frozenset(BREACH_VERDICTS.values())

#: The one breach response that reduces instead of stopping.
_AMEND = BREACH_VERDICTS["amend"]

#: The breach responses that leave a ``guard_state`` record behind, with
#: the params key that shapes each. Their names ARE the record's
#: ``state_kind`` (§6), which the check below pins.
_HOLD_SHAPES = {"hold": "hold", "pause": "pause"}
if not set(_HOLD_SHAPES) <= set(GUARD_STATE_KINDS):
    raise ProductionError(["guards.py: a hold shape is not a GUARD_STATE_KINDS member"])

#: ``notes`` is documentation on every config object and never a knob.
_NOTES = ("notes",)

#: The signed direction of a side, for ``exposure_after``.
_SIDE_SIGNS = {"buy": Decimal(1), "sell": Decimal(-1), "none": Decimal(0)}
if set(_SIDE_SIGNS) != set(SIDES):
    raise ProductionError(["guards.py: _SIDE_SIGNS does not cover SIDES"])

#: The finding a chain records when two guards amend different fields.
_CHAIN = "chain"


# ---------------------------------------------------------------------------
# The lattice (D9)
# ---------------------------------------------------------------------------


def _verdict_of(finding):
    """Return the verdict of a ``Finding`` or a serialized finding dict, or refuse."""
    if isinstance(finding, dict):
        verdict = finding.get("verdict")
    else:
        verdict = getattr(finding, "verdict", None)
    if verdict not in VERDICT_ORDER:
        raise ProductionError([f"verdict {verdict!r} is outside the lattice {list(VERDICTS)}"])
    return verdict


def max_verdict(findings):
    """Return the strictest verdict among ``findings`` — the leg's composite.

    ``allow < warn < amend < refuse < hold < halt`` over
    ``vocab.VERDICT_ORDER``; the composite is the maximum, never the last
    finding's. An empty sequence is ``allow``.

    Parameters
    ----------
    findings : iterable of Finding or dict
        Findings, or their ``to_obj()`` dicts (a ledger reader has only
        those).

    Returns
    -------
    str
        A ``vocab.VERDICTS`` member.

    Raises
    ------
    ProductionError
        If any finding carries a verdict outside the lattice.
    """
    verdicts = [_verdict_of(finding) for finding in findings]
    return max(verdicts, key=VERDICT_ORDER.__getitem__, default=VERDICTS[0])


# ---------------------------------------------------------------------------
# Numbers — Decimal everywhere a finding records one
# ---------------------------------------------------------------------------


def _is_nan(value):
    """Say whether ``value`` is a float or Decimal NaN."""
    if isinstance(value, float):
        return math.isnan(value)
    return isinstance(value, Decimal) and value.is_nan()


def _as_decimal(value, what):
    """Return a measured value as a finite Decimal (a ratio via ``str``), or refuse."""
    if isinstance(value, bool):
        raise ProductionError([f"{what}: expected a number, got {value!r}"])
    if isinstance(value, Decimal):
        out = value
    elif isinstance(value, float):
        out = Decimal(str(value))
    elif isinstance(value, int):
        out = Decimal(value)
    else:
        raise ProductionError([f"{what}: expected a number, got {value!r}"])
    if not out.is_finite():
        raise ProductionError([f"{what}: value {value!r} is not finite"])
    return out


def _decimal_param(problems, name, value):
    """Return a knob written as an int or decimal string as a Decimal; a float refuses."""
    if isinstance(value, bool) or isinstance(value, float):
        problems.append(f"{name} must be an int or a decimal string, got {value!r}")
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            out = Decimal(value)
        except InvalidOperation:
            problems.append(f"{name}: {value!r} is not a decimal string")
            return None
        if not out.is_finite():
            problems.append(f"{name}: {value!r} is not finite")
            return None
        return out
    problems.append(f"{name} must be an int or a decimal string, got {value!r}")
    return None


# ---------------------------------------------------------------------------
# Window — {} | {duration} | {count} | {calendar} (§5.5)
# ---------------------------------------------------------------------------


def _duration_arg(value, problems):
    """Return a duration as milliseconds: an int >= 0, or an ISO-8601 day/time string."""
    if isinstance(value, bool):
        problems.append(f"window.duration must be milliseconds or an ISO duration, got {value!r}")
        return None
    if isinstance(value, int):
        if value < 0:
            problems.append(f"window.duration must be >= 0 ms, got {value!r}")
            return None
        return value
    if isinstance(value, str):
        try:
            return parse_iso_duration(value)
        except ProductionError as exc:
            problems.append(f"window.duration: {exc}")
            return None
    problems.append(f"window.duration must be milliseconds or an ISO duration, got {value!r}")
    return None


def _count_arg(value, problems):
    """Return a count window's size as an int > 0."""
    before = len(problems)
    check_int_param(problems, "window.count", value, ge=1)
    return int(value) if len(problems) == before else None


def _calendar_arg(value, problems):
    """Return a calendar window's kind, one of ``CALENDAR_WINDOWS``."""
    if value not in CALENDAR_WINDOWS:
        problems.append(f"window.calendar must be one of {list(CALENDAR_WINDOWS)}, got {value!r}")
        return None
    return value


#: The declared key of a window IS its kind; each key has one normaliser.
_WINDOW_ARGS = {"duration": _duration_arg, "count": _count_arg, "calendar": _calendar_arg}
if set(_WINDOW_ARGS) | {"none"} != set(WINDOW_KINDS):
    raise ProductionError(["guards.py: the window normalisers do not cover WINDOW_KINDS"])


@dataclasses.dataclass(frozen=True)
class Window:
    """A ``Limit``'s window, normalised to a frozen ``(kind, arg)`` pair.

    ``{}`` is ``none``; ``{"duration": "PT1H" | 3600000}`` is a duration
    in milliseconds; ``{"count": 50}`` the last N decisions; ``{"calendar":
    "session" | "day" | "event"}`` a calendar window resolved through the
    injected calendar. Two windows declared alike compare equal, which is
    what lets two limits asking the same question deduplicate.

    Parameters
    ----------
    kind : str
        A ``vocab.WINDOW_KINDS`` member.
    arg : int or str or None
        Milliseconds, a count, a ``CALENDAR_WINDOWS`` member, or None.

    Examples
    --------
    ::

        window = Window.from_params({"duration": "PT1H"})
        window  # -> Window(kind='duration', arg=3600000)
        window.label  # 'duration:3600000'
        window.resolve(7_200_000, calendar)  # -> (3600000, 7200000, 3600000, 3600000)
    """

    kind: str
    arg: object

    _RESOLVE_HOOKS = {
        "none": "_resolve_none",
        "duration": "_resolve_duration",
        "count": "_resolve_count",
        "calendar": "_resolve_calendar",
    }
    _LABEL_HOOKS = {
        "none": "_label_none",
        "duration": "_label_arg",
        "count": "_label_arg",
        "calendar": "_label_calendar",
    }
    _TAIL_HOOKS = {"none": "_tail_all", "count": "_tail_count"}
    _RESUME_HOOKS = {"duration": "_resume_duration", "calendar": "_resume_calendar"}

    @classmethod
    def from_params(cls, declared):
        """Normalise a document's ``window`` block.

        Parameters
        ----------
        declared : dict
            ``{}``, or exactly one of ``duration`` / ``count`` /
            ``calendar`` (plus ``notes``).

        Returns
        -------
        Window
            The frozen kind and normalised argument.

        Raises
        ------
        ProductionError
            If ``declared`` is not a dict, names two keys, an unknown key,
            or an argument outside its grammar.
        """
        if not isinstance(declared, dict):
            raise ProductionError([f"window must be a dict, got {declared!r}"])
        keys = [key for key in declared if key not in _NOTES]
        if not keys:
            return cls("none", None)
        if len(keys) != 1:
            raise ProductionError(
                [f"window declares exactly one of {sorted(_WINDOW_ARGS)}, got {sorted(keys)}"]
            )
        normalise = _WINDOW_ARGS.get(keys[0])
        if normalise is None:
            raise ProductionError(
                [f"window: unknown key {keys[0]!r} — one of {sorted(_WINDOW_ARGS)}"]
            )
        problems = []
        arg = normalise(declared[keys[0]], problems)
        if problems:
            raise ProductionError(problems)
        return cls(keys[0], arg)

    @property
    def label(self):
        """Return the label a ``Finding.window`` records (``none``, ``count:50``, ``session``)."""
        return getattr(self, self._LABEL_HOOKS[self.kind])()

    def resolve(self, at_ms, calendar):
        """Resolve the window at an instant to the bounds a requirement digests.

        Parameters
        ----------
        at_ms : int
            The anchoring instant, epoch milliseconds.
        calendar : Calendar
            Answers ``window(kind, at_ms) -> (start_ms, end_ms)`` for a
            calendar window; unused otherwise.

        Returns
        -------
        tuple
            ``(start_ms, end_ms, baseline_at_ms, window_arg)`` — the
            ``EvidenceRequirement`` members in that order; a calendar
            window's ``window_arg`` is its resolved ``(start, end)``.
        """
        return getattr(self, self._RESOLVE_HOOKS[self.kind])(at_ms, calendar)

    def tail(self, entries):
        """Return the entries of a history this window covers (all, or the last N).

        Parameters
        ----------
        entries : sequence
            Decision-history entries, oldest first.

        Returns
        -------
        list
            The covered entries.

        Raises
        ------
        ProductionError
            For a duration or calendar window, which a history cannot
            answer without a clock.
        """
        hook = self._TAIL_HOOKS.get(self.kind)
        if hook is None:
            raise ProductionError([f"a {self.label} window cannot select history entries"])
        return getattr(self, hook)(list(entries))

    def resume_at(self, at_ms, calendar):
        """Return when a ``pause`` shaped like this window resumes.

        Parameters
        ----------
        at_ms : int
            The instant the pause starts.
        calendar : Calendar
            For a calendar pause, the start of the NEXT window of that
            kind after the current one ends.

        Returns
        -------
        int
            Epoch milliseconds.

        Raises
        ------
        ProductionError
            For a ``none`` or ``count`` window, which are not durations.
        """
        hook = self._RESUME_HOOKS.get(self.kind)
        if hook is None:
            raise ProductionError([f"a {self.label} window is not a pause duration"])
        return getattr(self, hook)(at_ms, calendar)

    def _resolve_none(self, at_ms, calendar):
        return (0, at_ms, 0, None)

    def _resolve_duration(self, at_ms, calendar):
        return (at_ms - self.arg, at_ms, at_ms - self.arg, self.arg)

    def _resolve_count(self, at_ms, calendar):
        return (0, at_ms, 0, self.arg)

    def _resolve_calendar(self, at_ms, calendar):
        start, end = calendar.window(self.arg, at_ms)
        return (start, end, start, (start, end))

    def _label_none(self):
        return "none"

    def _label_arg(self):
        return f"{self.kind}:{self.arg}"

    def _label_calendar(self):
        return self.arg

    def _tail_all(self, entries):
        return entries

    def _tail_count(self, entries):
        return entries[-self.arg :]

    def _resume_duration(self, at_ms, calendar):
        return at_ms + self.arg

    def _resume_calendar(self, at_ms, calendar):
        _start, end = calendar.window(self.arg, at_ms)
        return calendar.window(self.arg, end)[0]


# ---------------------------------------------------------------------------
# Bound and Scope (§5.5)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Bound:
    """An inclusive ``min`` and/or ``max`` a measured value is held to.

    One side may be absent, never both. Written as decimal strings or
    ints in the document; a float refuses (money never touches float).

    Parameters
    ----------
    min, max : Decimal or None

    Examples
    --------
    ::

        bound = Bound.from_params({"max": "100"}, "bound")
        bound  # -> Bound(min=None, max=Decimal('100'))
        bound.breached_by(Decimal("101"))  # 'max'
        bound.breached_by(Decimal("100"))  # None
    """

    min: object
    max: object

    _SIDES = ("min", "max")

    @classmethod
    def from_params(cls, declared, where):
        """Read a ``{min, max}`` block.

        Parameters
        ----------
        declared : dict
            ``min`` and/or ``max`` as ints or decimal strings, plus
            ``notes``.
        where : str
            The knob's name for the messages (``"bound"``).

        Returns
        -------
        Bound
            The parsed bound.

        Raises
        ------
        ProductionError
            If the block is not a dict, names an unknown key, has neither
            side, a side that is not an int or decimal string, or
            ``min > max``.
        """
        problems = []
        _check_dict(problems, where, declared)
        if problems:
            raise ProductionError(problems)
        _check_unknown(problems, declared, cls._SIDES + _NOTES, where=where)
        sides = {
            side: _decimal_param(problems, f"{where}.{side}", declared[side])
            for side in cls._SIDES
            if side in declared
        }
        if not sides:
            problems.append(f"{where} must declare min and/or max")
        low, high = sides.get("min"), sides.get("max")
        if low is not None and high is not None and low > high:
            problems.append(f"{where}: min {low} is above max {high}")
        if problems:
            raise ProductionError(problems)
        return cls(min=low, max=high)

    def to_params(self):
        """Return the bound in its document form (decimal strings).

        Returns
        -------
        dict
            The present sides only.
        """
        return {
            side: str(getattr(self, side))
            for side in self._SIDES
            if getattr(self, side) is not None
        }

    def breached_by(self, value):
        """Return the side ``value`` lies outside of, or None when inside.

        Parameters
        ----------
        value : Decimal

        Returns
        -------
        str or None
            ``"max"``, ``"min"``, or None (bounds are inclusive).
        """
        if self.max is not None and value > self.max:
            return "max"
        if self.min is not None and value < self.min:
            return "min"
        return None

    def approached_by(self, value, warn_at):
        """Say whether ``value`` has consumed ``warn_at`` of a bound, measured from zero.

        A positive maximum or a negative minimum is a budget, and
        ``warn_at`` is the fraction of it used: ``value >= warn_at * max``
        or ``value <= warn_at * min``. For a bound whose sign puts zero
        outside the budget (a positive minimum) the fraction is never
        reached and the knob is inert.

        Parameters
        ----------
        value : Decimal
        warn_at : float or None
            The fraction; None never warns.

        Returns
        -------
        bool
        """
        if warn_at is None:
            return False
        fraction = Decimal(str(warn_at))
        if self.max is not None and value >= fraction * self.max:
            return True
        return self.min is not None and value <= fraction * self.min

    def recorded(self, side):
        """Return the bound value a finding records: the breached side, else max then min.

        Parameters
        ----------
        side : str or None
            The result of :meth:`breached_by`.

        Returns
        -------
        Decimal or None
        """
        if side is not None:
            return getattr(self, side)
        return self.max if self.max is not None else self.min

    def tightened_by(self, overlay, where):
        """Return this bound with an overlay applied — only ever tighter (D11).

        An overlay may lower a declared ``max`` or raise a declared
        ``min``; setting a side the document did not declare, or a looser
        value, refuses rather than silently widening a limit.

        Parameters
        ----------
        overlay : Bound
        where : str
            The guard's name for the messages.

        Returns
        -------
        Bound

        Raises
        ------
        ProductionError
            If the overlay adds a side or loosens one.
        """
        problems = []
        low, high = self.min, self.max
        if overlay.max is not None:
            if high is None:
                problems.append(f"{where}: the overlay sets a max the document does not declare")
            elif overlay.max > high:
                problems.append(
                    f"{where}: overlay max {overlay.max} is looser than the document's {high}"
                )
            else:
                high = overlay.max
        if overlay.min is not None:
            if low is None:
                problems.append(f"{where}: the overlay sets a min the document does not declare")
            elif overlay.min < low:
                problems.append(
                    f"{where}: overlay min {overlay.min} is looser than the document's {low}"
                )
            else:
                low = overlay.min
        if problems:
            raise ProductionError(problems)
        return Bound(min=low, max=high)

    def describe(self):
        """Return the bound as prose for a reason (``max 100``, ``min -500``)."""
        return ", ".join(
            f"{side} {getattr(self, side)}"
            for side in self._SIDES
            if getattr(self, side) is not None
        )


#: The scopes a document spells as a bare string; ``group`` takes a field.
_STRING_SCOPES = tuple(scope for scope in LIMIT_SCOPES if scope != "group")


@dataclasses.dataclass(frozen=True)
class Scope:
    """What a ``Limit`` measures over: everything, one instrument, or a group.

    ``aggregate`` measures under :data:`AGGREGATE_SCOPE_KEY`; ``per_key``
    under the proposal's instrument; ``{"group": field}`` under the value
    of that field in the proposal's ``extra``. For requirements the scope
    expands over ``Candidate.scope_keys``, so the decider must put the
    very keys a check will derive there.

    Parameters
    ----------
    kind : str
        A ``vocab.LIMIT_SCOPES`` member.
    field : str or None
        The ``extra`` field of a group scope.

    Examples
    --------
    ::

        scope = Scope.from_params({"group": "sector"})
        scope  # -> Scope(kind='group', field='sector')
        scope.key_of(proposal)  # 'tech'  (proposal.extra["sector"])
        Scope.from_params("aggregate").key_of(proposal)  # '*'
    """

    kind: str
    field: object

    _KEY_HOOKS = {"aggregate": "_key_aggregate", "per_key": "_key_per_key", "group": "_key_group"}
    _KEYS_HOOKS = {
        "aggregate": "_keys_aggregate",
        "per_key": "_keys_candidate",
        "group": "_keys_candidate",
    }

    @classmethod
    def from_params(cls, declared):
        """Normalise a document's ``scope`` knob.

        Parameters
        ----------
        declared : str or dict
            ``"aggregate"``, ``"per_key"``, or ``{"group": "<field>"}``.

        Returns
        -------
        Scope

        Raises
        ------
        ProductionError
            If the value is neither, names an unknown key, or the group
            field is not a non-empty string.
        """
        if isinstance(declared, str):
            if declared not in _STRING_SCOPES:
                raise ProductionError(
                    [
                        f"scope must be one of {list(_STRING_SCOPES)} or {{'group': field}}, got {declared!r}"
                    ]
                )
            return cls(declared, None)
        problems = []
        _check_dict(problems, "scope", declared)
        if problems:
            raise ProductionError(problems)
        _check_unknown(problems, declared, ("group",) + _NOTES, where="scope")
        _check_str(problems, "scope.group", declared.get("group"))
        if problems:
            raise ProductionError(problems)
        return cls("group", declared["group"])

    def key_of(self, proposal):
        """Return the scope key a proposal is measured under.

        Parameters
        ----------
        proposal : Proposal

        Returns
        -------
        str

        Raises
        ------
        ProductionError
            If a group scope's field is absent from ``proposal.extra``.
        """
        return getattr(self, self._KEY_HOOKS[self.kind])(proposal)

    def keys_of(self, candidate):
        """Return the scope keys a candidate needs evidence for.

        Parameters
        ----------
        candidate : Candidate

        Returns
        -------
        tuple of str
            ``("*",)`` for aggregate; ``candidate.scope_keys`` otherwise.
        """
        return getattr(self, self._KEYS_HOOKS[self.kind])(candidate)

    def _key_aggregate(self, proposal):
        return AGGREGATE_SCOPE_KEY

    def _key_per_key(self, proposal):
        return proposal.instrument

    def _key_group(self, proposal):
        extra = proposal.extra or {}
        key = extra.get(self.field)
        if not isinstance(key, str) or not key:
            raise ProductionError(
                [
                    f"proposal {proposal.id!r} does not declare extra[{self.field!r}] for the group scope"
                ]
            )
        return key

    def _keys_aggregate(self, candidate):
        return (AGGREGATE_SCOPE_KEY,)

    def _keys_candidate(self, candidate):
        return tuple(candidate.scope_keys)


def _in_scope(instrument, scope_key):
    """Say whether an account item's instrument falls under ``scope_key``."""
    return scope_key == AGGREGATE_SCOPE_KEY or instrument == scope_key


# ---------------------------------------------------------------------------
# Measure — the question a Limit asks (§5.5)
# ---------------------------------------------------------------------------


class Measure(ABC):
    """A question about ONE proposal against a snapshotted account (§5.5).

    Deterministic and clockless: ``requirements`` declares what accounting
    must snapshot before sizing, ``value`` answers from ``state.account``
    (never the fold at head, §5.8.1). Deliberately NOT a monitor — it has
    no stream hook, because it answers about a proposal and an account,
    not a record stream (§5.15).

    Attributes
    ----------
    kind : str
        The name the registry and every finding spell.
    scalable : bool
        Whether an ``amend`` breach may reduce this measure.
    scalable_field : str or None
        The one ``Proposal`` field an amendment reduces.
    window_kinds : tuple of str
        The ``WINDOW_KINDS`` this measure can answer.
    scope_kinds : tuple of str
        The ``LIMIT_SCOPES`` this measure can answer.

    Examples
    --------
    A child measure, reachable as ``mypkg.measures:Edge``::

        class Edge(Measure):
            kind = "edge"

            def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
                return ()

            def value(self, proposal, state, window, scope_key, include_working):
                return Decimal(str(proposal.expected_value))

        Edge().value(proposal, state, Window.from_params({}), "*", True)  # Decimal('0.03')
    """

    kind = None
    scalable = False
    scalable_field = None
    window_kinds = ("none",)
    scope_kinds = LIMIT_SCOPES

    @abstractmethod
    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare the evidence accounting must snapshot for one scope key.

        Parameters
        ----------
        candidate : Candidate
        window : Window
        scope_key : str
        at_ms : int
            The instant the window resolves at.
        calendar : Calendar
        include_working : bool
            The limit's knob, passed in so the requirement is born
            complete.

        Returns
        -------
        tuple of EvidenceRequirement
            Empty for a measure the state already answers.
        """

    @abstractmethod
    def value(self, proposal, state, window, scope_key, include_working):
        """Measure ``proposal`` against ``state.account``.

        Parameters
        ----------
        proposal : Proposal
        state : TickState
        window : Window
        scope_key : str
        include_working : bool

        Returns
        -------
        Decimal or float
            Decimal for every money or quantity; float only for a
            dimensionless ratio.

        Raises
        ------
        ProductionError
            When the answer is not available — declared evidence absent,
            stale, or learned after the snapshot.
        """

    def amended_field_value(self, proposal, state, window, scope_key, include_working, target):
        """Return the ``scalable_field`` value that lands this measure exactly on ``target``.

        Parameters
        ----------
        proposal : Proposal
        state : TickState
        window : Window
        scope_key : str
        include_working : bool
        target : Decimal
            The bound to land on.

        Returns
        -------
        Decimal

        Raises
        ------
        ProductionError
            Always, for a measure that is not scalable.
        """
        raise ProductionError([f"measure {self.kind!r} is not scalable"])


MEASURE_KINDS = Registry("measure", Measure)


def _proposal_notional(proposal):
    """Return the proposal's declared notional, else its size at the reference price."""
    if proposal.notional is not None:
        return proposal.notional
    if proposal.qty is None:
        raise ProductionError([f"proposal {proposal.id!r} declares neither notional nor qty"])
    return proposal.qty * proposal.reference_price


class Quantity(Measure):
    """The proposal's own size, ``qty``.

    Examples
    --------
    ::

        Quantity().value(proposal, state, Window.from_params({}), "*", True)  # Decimal('10')
    """

    kind = "quantity"
    scalable = True
    scalable_field = "qty"

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare nothing: the proposal already answers."""
        return ()

    def value(self, proposal, state, window, scope_key, include_working):
        """Return ``proposal.qty``; a proposal without one refuses."""
        if proposal.qty is None:
            raise ProductionError([f"proposal {proposal.id!r} declares no qty"])
        return proposal.qty

    def amended_field_value(self, proposal, state, window, scope_key, include_working, target):
        """Return ``target`` — the size IS the measure."""
        return target


class Notional(Measure):
    """The proposal's declared notional, else ``qty * reference_price``.

    Examples
    --------
    ::

        Notional().value(proposal, state, Window.from_params({}), "*", True)  # Decimal('100')
    """

    kind = "notional"
    scalable = True
    scalable_field = "notional"

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare nothing: the proposal already answers."""
        return ()

    def value(self, proposal, state, window, scope_key, include_working):
        """Return the declared notional, else size times reference price."""
        return _proposal_notional(proposal)

    def amended_field_value(self, proposal, state, window, scope_key, include_working, target):
        """Return ``target`` — the notional IS the measure."""
        return target


class Exposure(Measure):
    """The account's absolute position value under the scope, plus working orders.

    Positions are valued at ``|qty * avg_cost|`` (the account carries no
    marks); a working order at ``|remaining_qty * limit|``. A working
    order with no limit (a market order) is valued at the proposal's
    ``reference_price`` when it is on the proposal's instrument; on any
    other instrument its value is unknown and the evidence is treated as
    missing — the guard refuses. Reads ``state.account`` only.

    Examples
    --------
    ::

        Exposure().value(proposal, state, Window.from_params({}), "*", True)  # Decimal('150')
        Exposure().value(proposal, state, Window.from_params({}), "*", False)  # Decimal('100')
    """

    kind = "exposure"
    scope_kinds = _STRING_SCOPES

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare nothing: the account snapshot already answers."""
        return ()

    def value(self, proposal, state, window, scope_key, include_working):
        """Return the scoped absolute exposure, with working orders when asked."""
        return self._before(proposal, state, scope_key, include_working)

    def _before(self, proposal, state, scope_key, include_working):
        """Return the account's exposure under ``scope_key`` before this proposal."""
        account = state.account
        total = sum(
            (
                abs(position.qty * position.avg_cost)
                for position in account.positions
                if _in_scope(position.instrument, scope_key)
            ),
            Decimal(0),
        )
        if include_working:
            total += sum(
                (
                    abs(order.remaining_qty * self._order_price(order, proposal))
                    for order in account.working
                    if _in_scope(order.instrument, scope_key)
                ),
                Decimal(0),
            )
        return total

    def _order_price(self, order, proposal):
        """Return a working order's valuation price, per the class docstring."""
        if order.limit is not None:
            return order.limit
        if order.instrument == proposal.instrument:
            return proposal.reference_price
        raise ProductionError(
            [
                f"no evidence to value working order {order.client_ref!r} ({order.instrument}) with no limit"
            ]
        )


class ExposureAfter(Exposure):
    """Exposure once this proposal is added: ``exposure + sign * qty * reference_price``.

    Affine in ``qty``, so an amendment solves for the size that lands
    exactly on the bound. A child's own formula subclasses this and is
    referenced by path.

    Examples
    --------
    ::

        ExposureAfter().value(buy_10_at_10, state, Window.from_params({}), "*", True)  # Decimal('250')
    """

    kind = "exposure_after"
    scalable = True
    scalable_field = "qty"

    def value(self, proposal, state, window, scope_key, include_working):
        """Return exposure before plus this proposal's signed notional."""
        if proposal.qty is None:
            raise ProductionError([f"proposal {proposal.id!r} declares no qty"])
        before = self._before(proposal, state, scope_key, include_working)
        return before + _SIDE_SIGNS[proposal.side] * proposal.qty * proposal.reference_price

    def amended_field_value(self, proposal, state, window, scope_key, include_working, target):
        """Solve ``before + sign * qty * reference_price == target`` for ``qty``."""
        before = self._before(proposal, state, scope_key, include_working)
        per_unit = _SIDE_SIGNS[proposal.side] * proposal.reference_price
        if per_unit == 0:
            raise ProductionError([f"proposal {proposal.id!r} has no signed size to reduce"])
        return (target - before) / per_unit


class PriceDeviation(Measure):
    """``|limit - reference_price| / reference_price``; a market order deviates by zero.

    Examples
    --------
    ::

        PriceDeviation().value(limit_10_50_ref_10, state, Window.from_params({}), "*", True)  # Decimal('0.05')
    """

    kind = "price_deviation"

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare nothing: the proposal already answers."""
        return ()

    def value(self, proposal, state, window, scope_key, include_working):
        """Return the absolute fractional gap from the reference."""
        if proposal.limit is None:
            return Decimal(0)
        if proposal.reference_price == 0:
            raise ProductionError([f"proposal {proposal.id!r} has a zero reference_price"])
        return abs(proposal.limit - proposal.reference_price) / proposal.reference_price


class OpenOrders(Measure):
    """How many working orders the account holds under the scope.

    Examples
    --------
    ::

        OpenOrders().value(proposal, state, Window.from_params({}), "*", True)  # Decimal('1')
    """

    kind = "open_orders"
    scope_kinds = _STRING_SCOPES

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare nothing: the account snapshot already answers."""
        return ()

    def value(self, proposal, state, window, scope_key, include_working):
        """Return the count of scoped working orders."""
        return Decimal(
            sum(1 for order in state.account.working if _in_scope(order.instrument, scope_key))
        )


class InputAgeMs(Measure):
    """The age of the OLDEST required feed key this tick (D6).

    Examples
    --------
    ::

        InputAgeMs().value(proposal, state, Window.from_params({}), "*", True)  # Decimal('45000')
    """

    kind = "input_age_ms"

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare nothing: this tick's coverage already answers."""
        return ()

    def value(self, proposal, state, window, scope_key, include_working):
        """Return the maximum ``age_ms`` over this tick's feed ages."""
        ages = [age.age_ms for age in state.feed_ages]
        if not ages:
            raise ProductionError(["no feed coverage this tick — input age is unknown"])
        return Decimal(max(ages))


class FeedAgeMs(Measure):
    """The age of the one feed key the scope names.

    Examples
    --------
    ::

        FeedAgeMs().value(proposal, state, Window.from_params({}), "INS2", True)  # Decimal('45000')
    """

    kind = "feed_age_ms"
    scope_kinds = tuple(scope for scope in LIMIT_SCOPES if scope != "aggregate")

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare nothing: this tick's coverage already answers."""
        return ()

    def value(self, proposal, state, window, scope_key, include_working):
        """Return the age of the key ``scope_key`` names; an uncovered key refuses."""
        for age in state.feed_ages:
            if age.key == scope_key:
                return Decimal(age.age_ms)
        raise ProductionError([f"no feed age for key {scope_key!r} in this tick's coverage"])


class Confidence(Measure):
    """The proposal's own confidence — a dimensionless ratio, so a float.

    Examples
    --------
    ::

        Confidence().value(proposal, state, Window.from_params({}), "*", True)  # 0.61
    """

    kind = "confidence"

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare nothing: the proposal already answers."""
        return ()

    def value(self, proposal, state, window, scope_key, include_working):
        """Return ``proposal.confidence``."""
        return proposal.confidence


class BankrollFraction(Measure):
    """The proposal's notional over the account's total balance — a ratio, so a float.

    The capital base INCLUDES an external cash flow (§6): a deposit
    changes what you have. Single-currency accounts only; balances that
    span currencies are an ambiguous conversion and refuse.

    Examples
    --------
    ::

        BankrollFraction().value(notional_100, state_with_1000, Window.from_params({}), "*", True)  # 0.1
    """

    kind = "bankroll_fraction"
    scalable = True
    scalable_field = "notional"

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare nothing: the account snapshot already answers."""
        return ()

    def value(self, proposal, state, window, scope_key, include_working):
        """Return notional over the total balance, as a float."""
        return float(_proposal_notional(proposal) / self._capital(state))

    def amended_field_value(self, proposal, state, window, scope_key, include_working, target):
        """Return the notional that is exactly ``target`` of the capital base."""
        return target * self._capital(state)

    def _capital(self, state):
        """Return the account's one total balance; none or several refuse."""
        totals = [balance.total for balance in state.account.balances]
        if len(totals) != 1:
            raise ProductionError(
                [f"bankroll needs exactly one currency balance, got {len(totals)}"]
            )
        if totals[0] <= 0:
            raise ProductionError([f"bankroll total {totals[0]} is not positive"])
        return totals[0]


def _history(state, window, scope_key):
    """Return the view's decision history under ``scope_key``, trimmed to ``window``."""
    entries = [
        entry
        for entry in state.view.decision_history
        if _in_scope(_field(entry, "instrument"), scope_key)
    ]
    return window.tail(entries)


def _field(entry, name):
    """Return a decision-history entry's field, refusing a malformed entry."""
    try:
        return entry[name]
    except (KeyError, TypeError) as exc:
        raise ProductionError([f"decision history entry lacks {name!r}: {entry!r}"]) from exc


class DecisionCount(Measure):
    """How many decisions the history holds under the scope (the last N with a count window).

    Examples
    --------
    ::

        DecisionCount().value(proposal, state, Window.from_params({"count": 2}), "*", True)  # Decimal('2')
    """

    kind = "decision_count"
    window_kinds = ("none", "count")
    scope_kinds = _STRING_SCOPES

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare nothing: the view's history already answers."""
        return ()

    def value(self, proposal, state, window, scope_key, include_working):
        """Return the count of scoped, windowed history entries."""
        return Decimal(len(_history(state, window, scope_key)))


class IdenticalCount(Measure):
    """How many past decisions match this proposal's instrument and side.

    Examples
    --------
    ::

        IdenticalCount().value(buy_ins1, state, Window.from_params({}), "*", True)  # Decimal('2')
    """

    kind = "identical_count"
    window_kinds = ("none", "count")

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare nothing: the view's history already answers."""
        return ()

    def value(self, proposal, state, window, scope_key, include_working):
        """Return the count of entries with this instrument and this side."""
        return Decimal(
            sum(
                1
                for entry in _history(state, window, scope_key)
                if _field(entry, "instrument") == proposal.instrument
                and _field(entry, "final") == proposal.side
            )
        )


class DirectionChanges(Measure):
    """How many times the decided side flipped between buy and sell under the scope.

    Examples
    --------
    ::

        DirectionChanges().value(proposal, state, Window.from_params({}), "INS1", True)  # Decimal('2')
    """

    kind = "direction_changes"
    window_kinds = ("none", "count")
    scope_kinds = _STRING_SCOPES

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare nothing: the view's history already answers."""
        return ()

    def value(self, proposal, state, window, scope_key, include_working):
        """Return the number of buy/sell flips in the scoped, windowed history."""
        sides = [
            _field(entry, "final")
            for entry in _history(state, window, scope_key)
            if _SIDE_SIGNS.get(_field(entry, "final"), Decimal(0)) != 0
        ]
        return Decimal(sum(1 for earlier, later in zip(sides, sides[1:]) if earlier != later))


class _EvidenceMeasure(Measure):
    """A measure accounting answers: declares one requirement, reads its evidence back."""

    window_kinds = WINDOW_KINDS

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        """Declare the one requirement accounting must snapshot for this scope key."""
        return (self._requirement(window, scope_key, at_ms, calendar, include_working),)

    def value(self, proposal, state, window, scope_key, include_working):
        """Return the snapshotted evidence value; absent, stale or future evidence refuses."""
        account = state.account
        requirement = self._requirement(
            window, scope_key, account.asof_ms, state.calendar, include_working
        )
        answers = account.measure_evidence.get(requirement.requirement_digest) or {}
        evidence = answers.get(scope_key)
        if evidence is None:
            raise ProductionError(
                [
                    f"no evidence for {self.kind} over window {window.label!r} at scope "
                    f"{scope_key!r} — accounting did not snapshot it"
                ]
            )
        problems = []
        if (
            evidence.window_end_ms < requirement.window_end_ms
            or evidence.window_start_ms > requirement.window_start_ms
        ):
            problems.append(
                f"{self.kind} evidence is stale: covers [{evidence.window_start_ms}, "
                f"{evidence.window_end_ms}) but the requirement is [{requirement.window_start_ms}, "
                f"{requirement.window_end_ms})"
            )
        if evidence.known_at_ms > account.asof_ms:
            problems.append(
                f"{self.kind} evidence known at {evidence.known_at_ms} is after the account "
                f"snapshot at {account.asof_ms}"
            )
        if evidence.scope_key != scope_key:
            problems.append(
                f"{self.kind} evidence answers scope {evidence.scope_key!r}, not {scope_key!r}"
            )
        if problems:
            raise ProductionError(problems)
        return evidence.value

    def _requirement(self, window, scope_key, at_ms, calendar, include_working):
        """Build the requirement — the same digest at declaration and at check (R8)."""
        start, end, baseline, arg = window.resolve(at_ms, calendar)
        return EvidenceRequirement(
            measure=self.kind,
            window_kind=window.kind,
            window_arg=arg,
            scope_key=scope_key,
            window_start_ms=start,
            window_end_ms=end,
            baseline_at_ms=baseline,
            include_working=include_working,
        )


class Pnl(_EvidenceMeasure):
    """Realised-plus-marked trading profit over the window — trading records only, never a cash flow.

    Examples
    --------
    ::

        Pnl().requirements(candidate, Window.from_params({"calendar": "session"}), "*", at_ms, calendar, True)
        # -> (EvidenceRequirement(measure='pnl', window_kind='calendar', ...),)
    """

    kind = "pnl"


class Drawdown(_EvidenceMeasure):
    """Peak-to-trough decline of trading equity over the window — trading records only.

    Examples
    --------
    ::

        Drawdown().value(proposal, state, Window.from_params({"duration": "PT1H"}), "*", True)
        # -> the snapshotted Decimal, or ProductionError when accounting did not answer
    """

    kind = "drawdown"


class ConsecutiveLosses(_EvidenceMeasure):
    """The current run of losing outcomes over the window — trading records only.

    Examples
    --------
    ::

        ConsecutiveLosses().value(proposal, state, Window.from_params({"count": 20}), "INS1", True)
        # -> Decimal('3')  (as accounting snapshotted it)
    """

    kind = "consecutive_losses"


class ErrorVsRealised(_EvidenceMeasure):
    """The model's prediction error against realised outcomes over the window — trading only.

    Examples
    --------
    ::

        ErrorVsRealised().value(proposal, state, Window.from_params({"calendar": "day"}), "*", True)
        # -> Decimal('0.12')  (as accounting snapshotted it)
    """

    kind = "error_vs_realised"


# ---------------------------------------------------------------------------
# Guard — the one hook (D9, §5.5)
# ---------------------------------------------------------------------------


class Guard(ABC):
    """The seam: ``check(proposal, state) -> Finding`` and nothing else touches money.

    Constructed as ``cls(params, name=key)`` from a document's ``guards``
    map; ``validate_params`` is default-deny over ``_PARAMS`` and every
    problem is raised at once. ``state`` is the frozen ``TickState`` of
    §5.8.1 — a guard cannot mutate what it judges.

    Parameters
    ----------
    params : dict or None
        The document's params block; ``notes`` is documentation.
    name : str or None
        The document key; a guard in a chain must carry one.

    Attributes
    ----------
    name : str or None
    hard : bool
        Whether the final candidate is re-run through this guard: true
        unless its breach response amends.

    Examples
    --------
    A guard that refuses every abstaining proposal::

        class NoAbstain(Guard):
            _PARAMS = ("notes",)

            def check(self, proposal, state):
                verdict = "refuse" if proposal.side == "none" else "allow"
                return Finding(guard=self.name, measure="side", value=None, bound=None,
                               window="none", scope_key=proposal.instrument,
                               verdict=verdict, reason=f"side {proposal.side}")

        NoAbstain({}, name="no_abstain").check(proposal, state).verdict  # 'allow'
    """

    _PARAMS = ()

    def __init__(self, params=None, name=None):
        params = dict(params or {})
        problems = self._problems(params)
        if problems:
            raise ProductionError(problems)
        self.name = name
        self._configure(params)

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when acceptable.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        return problems

    @property
    def hard(self):
        """Return whether a breach here can stop the leg (true unless it amends)."""
        return True

    @abstractmethod
    def check(self, proposal, state):
        """Judge one proposal against the tick's state.

        Parameters
        ----------
        proposal : Proposal
        state : TickState

        Returns
        -------
        Finding
            Always — inside the bound as much as outside it.
        """

    def requirements(self, candidate, at_ms, calendar):
        """Declare the evidence this guard needs for one candidate.

        Parameters
        ----------
        candidate : Candidate
        at_ms : int
        calendar : Calendar

        Returns
        -------
        tuple of EvidenceRequirement
            Empty: the base guard asks accounting for nothing.
        """
        return ()

    def tightened(self, overlay):
        """Return this guard with an authority overlay applied.

        Parameters
        ----------
        overlay : dict
            The overlay entry for this guard.

        Returns
        -------
        Guard

        Raises
        ------
        ProductionError
            Always, for a guard that has no bound to tighten.
        """
        raise ProductionError(
            [f"guard {self.name!r} ({type(self).__name__}) cannot be tightened by an overlay"]
        )

    def _problems(self, params):
        """Validate for construction; a subclass with extra collaborators overrides."""
        return type(self).validate_params(params)

    def _configure(self, params):
        """Read validated params; the base has none."""

    def _finding(self, measure, value, bound, window, scope_key, verdict, reason):
        """Build a redacted finding under this guard's name."""
        return Finding(
            guard=self.name,
            measure=measure,
            value=value,
            bound=bound,
            window=window,
            scope_key=scope_key,
            verdict=verdict,
            reason=redact(reason),
        )


def _hold_problems(params):
    """Return the problems with a ``hold: {ttl}`` block."""
    problems = []
    _check_dict(problems, "hold", params)
    if problems:
        return problems
    _check_unknown(problems, params, ("ttl",) + _NOTES, where="hold")
    check_int_param(problems, "hold.ttl", params.get("ttl"), ge=1)
    return problems


def _pause_problems(params):
    """Return the problems with a ``pause: {duration | calendar}`` block."""
    try:
        window = Window.from_params(params)
    except ProductionError as exc:
        return [f"pause: {problem}" for problem in exc.problems]
    family = window.kind
    if family not in Window._RESUME_HOOKS:
        return [f"pause needs {{duration}} or {{calendar}}, got {params!r}"]
    if window.arg == 0:
        return ["pause.duration must be positive"]
    return []


#: How each breach response's shape is validated; a response not listed
#: takes no shape.
_SHAPE_RULES = {"hold": _hold_problems, "pause": _pause_problems}


class Limit(Guard):
    """ONE class parameterised by measure x window x bound x scope (D9, §5.5).

    A ``Limit`` measures a proposal with its :class:`Measure`, over its
    :class:`Window`, under its :class:`Scope`'s key, and holds the value
    to its :class:`Bound`; a breach takes the verdict
    :data:`BREACH_VERDICTS` names for ``on_breach``. It refuses before
    measuring while its own ``(name, scope_key)`` is held in
    ``state.view.guard_holds`` past ``state.account.asof_ms``, and it
    refuses — with ``value`` None and the reason recorded — when the
    measure cannot answer.

    Parameters
    ----------
    params : dict
        ``measure`` (a ``MEASURE_KINDS`` name or ``pkg.module:Class``,
        required); ``window`` (``{}`` | ``{duration}`` | ``{count}`` |
        ``{calendar}``, default ``{}``); ``bound`` (``{min, max}``,
        required); ``warn_at`` (float in ``(0, 1)``, optional); ``scope``
        (``aggregate`` | ``per_key`` | ``{group: field}``, default
        ``aggregate``); ``include_working`` (bool, default true);
        ``on_breach`` (an ``ON_BREACH`` member, required; ``amend`` only
        for a scalable measure with a ``max``); ``pause`` (``{duration |
        calendar}``, required iff ``on_breach`` is ``pause``); ``hold``
        (``{ttl}`` in ms, required iff ``on_breach`` is ``hold``);
        ``notes``.
    name : str or None
        The document key.
    measure_registry : Registry
        Where ``measure`` resolves; :data:`MEASURE_KINDS` by default.

    Attributes
    ----------
    measure : Measure
    window : Window
    bound : Bound
    warn_at : float or None
    scope : Scope
    include_working : bool
    on_breach : str
    amended_field : str or None
        The field an ``amend`` breach reduces; None otherwise.

    Examples
    --------
    The §4.1 ``size`` guard::

        size = Limit({"measure": "quantity", "bound": {"max": "100"}, "on_breach": "refuse"}, name="size")
        size.check(proposal_qty_10, state).verdict  # 'allow'
        size.check(proposal_qty_101, state).verdict  # 'refuse'
    """

    _PARAMS = (
        "measure",
        "window",
        "bound",
        "warn_at",
        "scope",
        "include_working",
        "on_breach",
        "pause",
        "hold",
        "notes",
    )

    def __init__(self, params=None, name=None, measure_registry=MEASURE_KINDS):
        self._registry = measure_registry
        super().__init__(params, name)

    @classmethod
    def validate_params(cls, params, measure_registry=MEASURE_KINDS):
        """Return every problem with a limit's params; empty when acceptable.

        Parameters
        ----------
        params : dict
            The params block as written in the document.
        measure_registry : Registry
            Where ``measure`` resolves.

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending knob.
        """
        problems = super().validate_params(params)
        measure_cls = cls._measure_class(problems, params.get("measure"), measure_registry)
        window = _collect(problems, "window", Window.from_params, params.get("window", {}))
        bound = _collect(problems, "bound", Bound.from_params, params.get("bound"), "bound")
        scope = _collect(problems, "scope", Scope.from_params, params.get("scope", DEFAULT_SCOPE))
        cls._check_warn_at(problems, params.get("warn_at"))
        if not isinstance(params.get("include_working", DEFAULT_INCLUDE_WORKING), bool):
            problems.append(f"include_working must be a bool, got {params['include_working']!r}")
        response = params.get("on_breach")
        if response not in BREACH_VERDICTS:
            problems.append(f"on_breach must be one of {list(ON_BREACH)}, got {response!r}")
        cls._check_shapes(problems, params, response)
        if measure_cls is not None:
            cls._check_pairs(problems, measure_cls, window, bound, scope, response)
        return problems

    @staticmethod
    def _measure_class(problems, uses, registry):
        """Resolve the ``measure`` knob, or append why it does not."""
        if not isinstance(uses, str):
            problems.append(f"measure must be a registered name or pkg.module:Class, got {uses!r}")
            return None
        try:
            return registry.resolve(uses)
        except ProductionError as exc:
            problems.append(f"measure: {exc}")
            return None

    @staticmethod
    def _check_warn_at(problems, warn_at):
        """Append a problem unless ``warn_at`` is absent or a number in (0, 1)."""
        if warn_at is None:
            return
        if (
            isinstance(warn_at, bool)
            or not isinstance(warn_at, (int, float))
            or not 0 < warn_at < 1
        ):
            problems.append(f"warn_at must be a number strictly between 0 and 1, got {warn_at!r}")

    @staticmethod
    def _check_shapes(problems, params, response):
        """Require the shape ``on_breach`` needs and refuse any it does not."""
        for shape, rule in _SHAPE_RULES.items():
            present = shape in params
            if shape == response:
                if not present:
                    problems.append(f"on_breach {response!r} needs a {shape} block")
                else:
                    problems.extend(rule(params[shape]))
            elif present:
                problems.append(f"{shape} is only legal with on_breach {shape!r}, not {response!r}")

    @staticmethod
    def _check_pairs(problems, measure_cls, window, bound, scope, response):
        """Refuse a measure x window, measure x scope or measure x amend pair that cannot bind."""
        if window is not None:
            family = window.kind
            if family not in measure_cls.window_kinds:
                problems.append(
                    f"window {window.label!r} cannot be answered by measure {measure_cls.kind!r} "
                    f"(answers {list(measure_cls.window_kinds)})"
                )
        if scope is not None:
            family = scope.kind
            if family not in measure_cls.scope_kinds:
                problems.append(
                    f"scope {family!r} cannot be answered by measure {measure_cls.kind!r} "
                    f"(answers {list(measure_cls.scope_kinds)})"
                )
        if response == _AMEND:
            if not measure_cls.scalable:
                problems.append(
                    f"on_breach amend needs a scalable measure; {measure_cls.kind!r} is not"
                )
            if bound is not None and bound.max is None:
                problems.append("on_breach amend needs a max: an amendment can only reduce")

    def _problems(self, params):
        return self.validate_params(params, self._registry)

    def _configure(self, params):
        self._params = dict(params)
        self.measure = self._registry.resolve(params["measure"])()
        self.window = Window.from_params(params.get("window", {}))
        self.bound = Bound.from_params(params["bound"], "bound")
        self.warn_at = params.get("warn_at")
        self.scope = Scope.from_params(params.get("scope", DEFAULT_SCOPE))
        self.include_working = params.get("include_working", DEFAULT_INCLUDE_WORKING)
        self.on_breach = params["on_breach"]
        self.amended_field = self.measure.scalable_field if self.on_breach == _AMEND else None
        self._ttl_ms = params.get("hold", {}).get("ttl")
        self._pause = Window.from_params(params["pause"]) if "pause" in params else None

    @property
    def hard(self):
        """Return whether a breach here stops the leg: every response but ``amend``."""
        return BREACH_VERDICTS[self.on_breach] != _AMEND

    def requirements(self, candidate, at_ms, calendar):
        """Declare the measure's evidence for every scope key of one candidate.

        Parameters
        ----------
        candidate : Candidate
        at_ms : int
        calendar : Calendar

        Returns
        -------
        tuple of EvidenceRequirement
            One per scope key for an evidence-backed measure; empty for a
            proposal-local one.
        """
        declared = []
        for scope_key in self.scope.keys_of(candidate):
            declared.extend(
                self.measure.requirements(
                    candidate, self.window, scope_key, at_ms, calendar, self.include_working
                )
            )
        return tuple(declared)

    def check(self, proposal, state):
        """Judge one proposal: held, unanswerable, inside, approaching, or breached.

        Parameters
        ----------
        proposal : Proposal
        state : TickState

        Returns
        -------
        Finding
            ``value`` is None when held or when the measure could not
            answer; the verdict is then ``refuse``.

        Raises
        ------
        ProductionError
            If a group scope's field is absent from the proposal.
        """
        scope_key = self.scope.key_of(proposal)
        hold = state.view.guard_holds.get((self.name, scope_key))
        if hold is not None and hold["held_until_ms"] > state.account.asof_ms:
            return self._limit_finding(
                None,
                self.bound.recorded(None),
                scope_key,
                BREACH_VERDICTS["refuse"],
                f"held until {hold['held_until_ms']} ({hold['state_kind']}): {hold['reason']}",
            )
        try:
            value = _as_decimal(
                self.measure.value(proposal, state, self.window, scope_key, self.include_working),
                self.measure.kind,
            )
        except ProductionError as exc:
            return self._limit_finding(
                None,
                self.bound.recorded(None),
                scope_key,
                BREACH_VERDICTS["refuse"],
                f"{self.measure.kind}: {exc}",
            )
        return self._judge(proposal, state, value, scope_key)

    def amend(self, proposal, state, value):
        """Reduce the scalable field so the measure lands on ``bound.max``.

        The new field value is computed rounding toward zero and the
        result is postcondition-checked: it must be strictly positive,
        strictly below the current field value, and re-measure at or
        below the bound. A value already inside the bound refuses — an
        amendment never raises a value.

        Parameters
        ----------
        proposal : Proposal
            The original.
        state : TickState
        value : Decimal
            The measured value of ``proposal``.

        Returns
        -------
        Proposal
            A new proposal differing in exactly ``amended_field``.

        Raises
        ------
        ProductionError
            If this limit does not amend, the value is inside the bound,
            or no reducing value lands inside it.
        """
        if self.amended_field is None:
            raise ProductionError(
                [f"guard {self.name!r} does not amend (on_breach {self.on_breach!r})"]
            )
        value = _as_decimal(value, self.measure.kind)
        ceiling = self.bound.max
        if value <= ceiling:
            raise ProductionError(
                [f"an amendment never raises a value: {value} is within max {ceiling}"]
            )
        scope_key = self.scope.key_of(proposal)
        with localcontext() as context:
            context.rounding = ROUND_DOWN
            target = self.measure.amended_field_value(
                proposal, state, self.window, scope_key, self.include_working, ceiling
            )
        current = getattr(proposal, self.amended_field)
        current = value if current is None else current
        if not Decimal(0) < target < current:
            raise ProductionError(
                [
                    f"amendment infeasible: {self.amended_field} {current} -> {target} is not a reduction"
                ]
            )
        amended = dataclasses.replace(proposal, **{self.amended_field: target})
        after = _as_decimal(
            self.measure.value(amended, state, self.window, scope_key, self.include_working),
            self.measure.kind,
        )
        if after > ceiling:
            raise ProductionError(
                [f"amendment postcondition failed: {after} is above max {ceiling}"]
            )
        return amended

    def guard_state_body(self, finding, at_ms, calendar):
        """Build the §6 ``guard_state`` record body for a ``hold`` finding.

        Parameters
        ----------
        finding : Finding
            A finding this guard produced with verdict ``hold``.
        at_ms : int
            The instant the hold starts.
        calendar : Calendar
            For a calendar pause, where the next window starts.

        Returns
        -------
        dict
            ``guard, scope_key, state_kind, reason, held_until_ms,
            resume_at_ms, finding`` — ``state_kind`` is ``on_breach``;
            a pause resumes at ``resume_at_ms == held_until_ms``.

        Raises
        ------
        ProductionError
            If the finding is not this guard's or is not a hold.
        """
        problems = []
        if finding.guard != self.name:
            problems.append(f"finding belongs to guard {finding.guard!r}, not {self.name!r}")
        if finding.verdict != BREACH_VERDICTS["hold"]:
            problems.append(f"only a hold finding leaves a guard_state, not {finding.verdict!r}")
        if problems:
            raise ProductionError(problems)
        held_until_ms, resume_at_ms = getattr(self, self._UNTIL_HOOKS[self.on_breach])(
            at_ms, calendar
        )
        return {
            "guard": self.name,
            "scope_key": finding.scope_key,
            "state_kind": _HOLD_SHAPES[self.on_breach],
            "reason": finding.reason,
            "held_until_ms": held_until_ms,
            "resume_at_ms": resume_at_ms,
            "finding": finding.to_obj(),
        }

    _UNTIL_HOOKS = {"hold": "_until_hold", "pause": "_until_pause"}

    def tightened(self, overlay):
        """Return a copy of this limit with an overlay's tighter bound (D11).

        Parameters
        ----------
        overlay : dict
            ``{"bound": {min, max}}`` — the only overlayable knob.

        Returns
        -------
        Limit
            Same name, measure, window, scope and breach response.

        Raises
        ------
        ProductionError
            If the overlay names another knob or is looser than the
            document.
        """
        problems = []
        _check_dict(problems, f"overlay for {self.name!r}", overlay)
        if problems:
            raise ProductionError(problems)
        _check_unknown(problems, overlay, ("bound",) + _NOTES, where=f"overlay for {self.name!r}")
        if "bound" not in overlay:
            problems.append(f"overlay for {self.name!r} declares no bound")
        if problems:
            raise ProductionError(problems)
        tighter = self.bound.tightened_by(
            Bound.from_params(overlay["bound"], "overlay.bound"), self.name
        )
        return Limit(
            dict(self._params, bound=tighter.to_params()),
            name=self.name,
            measure_registry=self._registry,
        )

    def _judge(self, proposal, state, value, scope_key):
        """Turn a measured value into the finding its bound and breach response dictate."""
        side = self.bound.breached_by(value)
        if side is None:
            approached = self.bound.approached_by(value, self.warn_at)
            verdict = "warn" if approached else "allow"
            reason = f"{self.measure.kind} {value} within {self.bound.describe()}"
            reason += f" (warn_at {self.warn_at} reached)" if approached else ""
            return self._limit_finding(value, self.bound.recorded(None), scope_key, verdict, reason)
        verdict = BREACH_VERDICTS[self.on_breach]
        reason = f"{self.measure.kind} {value} outside {side} {getattr(self.bound, side)}"
        if self.amended_field is not None:
            try:
                amended = self.amend(proposal, state, value)
                reason += f": amend {self.amended_field} -> {getattr(amended, self.amended_field)}"
            except ProductionError as exc:
                verdict = BREACH_VERDICTS["refuse"]
                reason += f": {exc}"
        return self._limit_finding(value, self.bound.recorded(side), scope_key, verdict, reason)

    def _limit_finding(self, value, bound, scope_key, verdict, reason):
        """Build this limit's finding under its measure and window labels."""
        return self._finding(
            self.measure.kind, value, bound, self.window.label, scope_key, verdict, reason
        )

    def _until_hold(self, at_ms, calendar):
        """Return ``(held_until, resume_at)`` for a hold: ``at_ms + ttl``, no resume."""
        return (at_ms + self._ttl_ms, None)

    def _until_pause(self, at_ms, calendar):
        """Return ``(held_until, resume_at)`` for a pause: both its resume instant."""
        resume_at_ms = self._pause.resume_at(at_ms, calendar)
        return (resume_at_ms, resume_at_ms)


def _collect(problems, knob, parse, *args):
    """Run a value parser, folding its refusal into ``problems``; return the value or None."""
    try:
        return parse(*args)
    except ProductionError as exc:
        problems.extend(f"{knob}: {problem}" for problem in exc.problems)
        return None


def _numeric_fields(cls):
    """Return the dataclass fields of ``cls`` typed as a number (Decimal, float or int)."""
    hints = typing.get_type_hints(cls)
    names = []
    for field in dataclasses.fields(cls):
        members = typing.get_args(hints[field.name]) or (hints[field.name],)
        if any(member in (Decimal, float, int) for member in members):
            names.append(field.name)
    return tuple(names)


#: The proposal fields a ``RangeGuard`` may bound — derived, never restated.
_RANGE_FIELDS = _numeric_fields(Proposal)

#: The NaN policies as the verdict each produces.
_NAN_VERDICTS = {"refuse": "refuse", "allow": "allow"}
if set(_NAN_VERDICTS) != set(NAN_POLICY):
    raise ProductionError(["guards.py: _NAN_VERDICTS does not cover NAN_POLICY"])


class RangeGuard(Guard):
    """A sanity check on one raw proposal field: inclusive ``min``/``max`` plus a NaN policy.

    Always hard, never amends, declares no evidence. The field is
    measured under the proposal's instrument.

    Parameters
    ----------
    params : dict
        ``field`` (a numeric ``Proposal`` field, required); ``min`` /
        ``max`` (ints or decimal strings, at least one); ``nan``
        (``refuse`` | ``allow``, default ``refuse``); ``notes``.
    name : str or None

    Attributes
    ----------
    field : str
    bound : Bound
    nan : str

    Examples
    --------
    The §4.1 ``sane`` guard::

        sane = RangeGuard({"field": "confidence", "min": 0, "max": 1, "nan": "refuse"}, name="sane")
        sane.check(proposal_conf_0_61, state).verdict  # 'allow'
        sane.check(proposal_conf_1_5, state).verdict  # 'refuse'
    """

    _PARAMS = ("field", "min", "max", "nan", "notes")

    @classmethod
    def validate_params(cls, params):
        """Return every problem with a range guard's params; empty when acceptable.

        Parameters
        ----------
        params : dict

        Returns
        -------
        list of str
        """
        problems = super().validate_params(params)
        field = params.get("field")
        if field not in _RANGE_FIELDS:
            problems.append(
                f"field must be a numeric Proposal field {list(_RANGE_FIELDS)}, got {field!r}"
            )
        _collect(problems, "range", Bound.from_params, cls._bound_params(params), "range")
        policy = params.get("nan", DEFAULT_NAN_POLICY)
        if policy not in _NAN_VERDICTS:
            problems.append(f"nan must be one of {list(NAN_POLICY)}, got {policy!r}")
        return problems

    @staticmethod
    def _bound_params(params):
        """Return the ``min``/``max`` knobs as a bound block."""
        return {side: params[side] for side in Bound._SIDES if side in params}

    def _configure(self, params):
        self.field = params["field"]
        self.bound = Bound.from_params(self._bound_params(params), "range")
        self.nan = params.get("nan", DEFAULT_NAN_POLICY)

    def check(self, proposal, state):
        """Hold ``proposal.<field>`` inside the inclusive bounds.

        Parameters
        ----------
        proposal : Proposal
        state : TickState
            Unused: a range reads the proposal alone.

        Returns
        -------
        Finding
            ``measure`` is the field name; ``value`` None for a NaN or a
            non-number.
        """
        raw = getattr(proposal, self.field)
        scope_key = proposal.instrument
        if _is_nan(raw):
            return self._range_finding(
                None, scope_key, _NAN_VERDICTS[self.nan], f"{self.field} is nan ({self.nan} policy)"
            )
        try:
            value = _as_decimal(raw, self.field)
        except ProductionError as exc:
            return self._range_finding(None, scope_key, BREACH_VERDICTS["refuse"], str(exc))
        side = self.bound.breached_by(value)
        if side is None:
            return self._range_finding(
                value, scope_key, "allow", f"{self.field} {value} within {self.bound.describe()}"
            )
        return self._range_finding(
            value,
            scope_key,
            BREACH_VERDICTS["refuse"],
            f"{self.field} {value} outside {side} {getattr(self.bound, side)}",
        )

    def _range_finding(self, value, scope_key, verdict, reason):
        """Build this range's finding under its field name."""
        return self._finding(
            self.field,
            value,
            self.bound.recorded(self.bound.breached_by(value) if value is not None else None),
            "none",
            scope_key,
            verdict,
            reason,
        )


# ---------------------------------------------------------------------------
# GuardChain — the composition rules (D9, §5.5)
# ---------------------------------------------------------------------------


class GuardChain:
    """Every configured guard, in document order, and D9's composition rules.

    ``requirements`` is the union ``Accounting.snapshot`` receives — this
    chain is its only producer; ``check_all`` judges the original
    proposal with every guard, composes amendments, and re-runs the hard
    guards on the final candidate; ``check_authority_scope`` is the last
    gate before a permit. Cancels never pass through: no verb takes one.

    Parameters
    ----------
    guards : mapping of str to Guard
        Document key to guard, in document order; each key must equal
        its guard's ``name``.

    Attributes
    ----------
    guards : mapping of str to Guard
        Read-only, in order.

    Examples
    --------
    ::

        chain = GuardChain({"size": size_limit, "sane": sane_range})
        final, findings = chain.check_all(proposal, state)
        max_verdict(findings)  # 'allow'
        chain.requirements((candidate,), at_ms, calendar)  # -> ()
    """

    def __init__(self, guards):
        problems = []
        if not isinstance(guards, typing.Mapping):
            raise ProductionError([f"GuardChain takes a mapping of name to Guard, got {guards!r}"])
        for key, guard in guards.items():
            if not isinstance(guard, Guard):
                problems.append(f"guards[{key!r}] is not a Guard: {guard!r}")
            elif guard.name is None:
                problems.append(f"guards[{key!r}] carries no name — construct it with name={key!r}")
            elif guard.name != key:
                problems.append(
                    f"guards[{key!r}] is named {guard.name!r}: the key must equal the name"
                )
        if problems:
            raise ProductionError(problems)
        self._guards = MappingProxyType(dict(guards))

    @property
    def guards(self):
        """Return the read-only name-to-guard mapping, in document order."""
        return self._guards

    def requirements(self, candidates, at_ms, calendar):
        """Union every guard's evidence requirements over every candidate.

        Parameters
        ----------
        candidates : iterable of Candidate
        at_ms : int
            The instant windows resolve at — the tick's account instant.
        calendar : Calendar

        Returns
        -------
        tuple of EvidenceRequirement
            Deduplicated by ``requirement_digest``, first occurrence
            first.
        """
        seen = {}
        for guard in self._guards.values():
            for candidate in candidates:
                for requirement in guard.requirements(candidate, at_ms, calendar):
                    seen.setdefault(requirement.requirement_digest, requirement)
        return tuple(seen.values())

    def check_all(self, proposal, state):
        """Judge the original with every guard, compose amendments, re-run the hard guards.

        Parameters
        ----------
        proposal : Proposal
            The original; anything else refuses.
        state : TickState

        Returns
        -------
        tuple
            ``(final, findings)`` — the final candidate (the original, or
            the one amended proposal) and every finding recorded, first
            pass then second.

        Raises
        ------
        ProductionError
            If ``proposal`` is not a ``Proposal``.
        """
        self._require_proposal(proposal)
        findings = [guard.check(proposal, state) for guard in self._guards.values()]
        reductions = self._reductions(proposal, state, findings)
        if len(reductions) > 1:
            findings.append(self._conflict(proposal, reductions))
            return proposal, tuple(findings)
        if not reductions:
            return proposal, tuple(findings)
        ((field, values),) = reductions.items()
        final = dataclasses.replace(proposal, **{field: min(value for value, _guard in values)})
        findings.extend(guard.check(final, state) for guard in self._guards.values() if guard.hard)
        return final, tuple(findings)

    def check_authority_scope(self, proposal, state, scope):
        """Re-apply the active authority's allowlist and overlay to the exact final proposal.

        Parameters
        ----------
        proposal : Proposal
            The final candidate about to be permitted.
        state : TickState
        scope : object
            Carries ``allowlist`` (a sequence of instruments; empty admits
            all) and ``limits_overlay`` (``{guard: {"bound": {min,
            max}}}``) — an ``ArmingProjection`` does.

        Returns
        -------
        ScopeVerdict
            ``scope_key`` is the proposal's instrument.

        Raises
        ------
        ProductionError
            If the overlay names a guard the chain lacks, a guard without
            a bound, or a bound looser than the document's.
        """
        self._require_proposal(proposal)
        instrument = proposal.instrument
        allowlist = tuple(scope.allowlist or ())
        if allowlist and instrument not in allowlist:
            return ScopeVerdict(
                allowed=False,
                scope_key=instrument,
                reason=f"instrument {instrument!r} is outside the allowlist",
            )
        for guard_name, overlay in dict(scope.limits_overlay or {}).items():
            guard = self._guards.get(guard_name)
            if guard is None:
                raise ProductionError(
                    [f"overlay names guard {guard_name!r}, which the chain does not have"]
                )
            finding = guard.tightened(overlay).check(proposal, state)
            if finding.verdict in _BREACHING:
                return ScopeVerdict(
                    allowed=False, scope_key=instrument, reason=f"{guard_name}: {finding.reason}"
                )
        return ScopeVerdict(allowed=True, scope_key=instrument, reason="")

    @staticmethod
    def _require_proposal(proposal):
        """Refuse anything but a ``Proposal`` — a cancel has no seam here."""
        if not isinstance(proposal, Proposal):
            raise ProductionError(
                [f"a guard chain judges a Proposal, got {type(proposal).__name__}"]
            )

    def _reductions(self, proposal, state, findings):
        """Return ``{field: [(value, guard_name), ...]}`` for every amend finding."""
        reductions = {}
        for finding in findings:
            if finding.verdict != _AMEND:
                continue
            guard = self._guards[finding.guard]
            amended = guard.amend(proposal, state, finding.value)
            reductions.setdefault(guard.amended_field, []).append(
                (getattr(amended, guard.amended_field), guard.name)
            )
        return reductions

    def _conflict(self, proposal, reductions):
        """Build the refusing finding for amendments on two fields."""
        named = ", ".join(
            f"{field} ({', '.join(guard for _value, guard in values)})"
            for field, values in reductions.items()
        )
        return Finding(
            guard=_CHAIN,
            measure="amendment",
            value=None,
            bound=None,
            window="none",
            scope_key=AGGREGATE_SCOPE_KEY,
            verdict=BREACH_VERDICTS["refuse"],
            reason=redact(f"conflicting_amendments: {named} — two fields cannot both be reduced"),
        )


# ---------------------------------------------------------------------------
# Registries — import is registration (§4.3)
# ---------------------------------------------------------------------------

for _measure in (
    Quantity,
    Notional,
    Exposure,
    ExposureAfter,
    PriceDeviation,
    Pnl,
    Drawdown,
    ConsecutiveLosses,
    DecisionCount,
    IdenticalCount,
    DirectionChanges,
    OpenOrders,
    InputAgeMs,
    FeedAgeMs,
    Confidence,
    BankrollFraction,
    ErrorVsRealised,
):
    MEASURE_KINDS.register(_measure.kind, _measure)

GUARD_KINDS = Registry("guard", Guard)
GUARD_KINDS.register("limit", Limit)
GUARD_KINDS.register("range", RangeGuard)
