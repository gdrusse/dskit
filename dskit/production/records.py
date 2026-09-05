"""The value objects every ``dskit.production`` module passes around (§5.4).

A guard, a permit and a ledger row can all name the same quantity and
mean it because every record here obeys one contract, enforced by one
private base:

* **Frozen.** A guard receives records and cannot edit what it judges.
* **Coerced and validated at construction.** Every field is checked
  against its declared type and, where the plan closes it, against its
  vocabulary; a record's own invariants (``filled_qty + remaining_qty ==
  qty``, ``data_asof_ms`` is the oldest watermark) hold from the moment
  it exists. Problems accumulate into one :class:`ProductionError`.
* **Money never touches float.** Every money, quantity and price field
  is a ``Decimal`` — a decimal string in JSON, an int or decimal string
  on the way in, a float refused — and an opaque venue payload is walked
  by :func:`~dskit.production.base.reject_money_floats`, the same rule
  ``ledger.py`` applies to a record body, so a float under a money name
  refuses at any depth here too. Instants are epoch-ms ``int``s.
  Only dimensionless ratios (``confidence``, a monitor ``statistic``) are
  floats, and a non-finite number refuses everywhere.
* **JSON both ways.** ``to_obj()`` is JSON-ready with no custom encoder
  (which is why money is a string); ``from_obj(obj)`` is default-deny —
  an unknown key is an error, not a silent default — and rebuilds nested
  records, tuples and ``Decimal``s so the round trip is equality.

The field ORDER of a dataclass is the canonical order its digest is
computed over, and every digest is :func:`~dskit.production.base.canonical_hash`
over ``to_obj()`` or the stated subset: ``Intent.intent_digest()`` drops
``client_ref`` (an identifier of the intent, not part of it),
``DecisionPlan.decision_plan_digest()`` and
``ReductionIntent.reduction_intent_digest()`` take every field,
``EvidenceRequirement.requirement_digest`` is born with the record, and
``AccountState.risk_digest()`` hashes the economic content while excluding
observation-only timestamps and venue-native payloads. A digest without a
stated recipe is not a binding, which is why they live beside the records.

Scope: what §8 places here. ``LegBindings``/``LegEvaluation``/``LegResult``
belong to ``leg.py``, ``StateView``/``TickState`` to ``state.py``,
``LeasePermit`` to ``coordination.py``, ``ReadinessResult`` to
``readiness.py``.

Import cost: stdlib plus ``dskit.production.base`` and ``vocab``.
"""

from __future__ import annotations

import functools
import math
import types
import typing
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation

from dskit.production.base import (
    ProductionError,
    _check_unknown,
    canonical_hash,
    reject_money_floats,
)
from dskit.production.vocab import (
    ALERT_STATUSES,
    AUTHORITY_ROLES,
    BREAKER_STATES,
    FEED_STATUSES,
    FILL_STATUSES,
    HEALTH_STATES,
    LEG_LATENCY_BUCKETS,
    LEG_ORIGINS,
    LIQUIDITY,
    MONITOR_STATUSES,
    OPERATIONS,
    PLAN_RESULTS,
    POSITION_SOURCES,
    READINESS_VERDICTS,
    RISK_EFFECTS,
    RUNGS,
    SEVERITIES,
    SIDES,
    STATUSES,
    TICK_PHASES,
    TICK_STATUSES,
    TIFS,
    VERDICTS,
    WINDOW_KINDS,
)

__all__ = [
    "Ack",
    "AccountState",
    "ActPermit",
    "Alert",
    "Balance",
    "Candidate",
    "DecisionPlan",
    "EntryBatch",
    "EvidenceRequirement",
    "ExecutionScope",
    "FeedAge",
    "FeedResult",
    "Fill",
    "Finding",
    "GateResult",
    "InputWatermark",
    "Intent",
    "MeasureEvidence",
    "OrderState",
    "Permit",
    "PolicyRequest",
    "Position",
    "Proposal",
    "Provenance",
    "Quote",
    "QuoteSet",
    "ReductionAuthorization",
    "ReductionIntent",
    "ReductionPlan",
    "RiskVersion",
    "ScopeVerdict",
    "Settlement",
    "SimulatedPermit",
    "TickResult",
    "TickStart",
    "Verdict",
]

_NONE = type(None)
_UNIONS = (typing.Union, types.UnionType)

#: The seven members §6's ``tick`` record requires of its ``feed`` block.
_FEED_MEMBERS = (
    "status",
    "acq_id",
    "records_added",
    "source_config_hash",
    "required_keys_digest",
    "watermarks_by_key",
    "coverage_digest",
)

#: The two members of a ``tick`` record's ``error`` block (§6).
_ERROR_MEMBERS = ("class", "text")

#: What ``AccountState.risk_digest`` leaves out of every nested record:
#: observation-only timestamps and the venue's native payload. Window
#: bounds (``window_start_ms``/``window_end_ms``) stay in — they are what
#: the evidence measures, not when it was seen.
_RISK_EXCLUDED = ("native", "ts_ms", "created_ms", "updated_ms", "effective_at_ms", "known_at_ms")


# ---------------------------------------------------------------------------
# Coercion — one type-directed walk, shared by construction and from_obj
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def _hints(cls):
    """Resolve a record class's field annotations, cached per class."""
    return typing.get_type_hints(cls)


def _decimal(value, path, problems):
    """Return ``value`` as a finite Decimal; a float or bool refuses."""
    if isinstance(value, (bool, float)):
        problems.append(
            f"{path}: money never touches float, got {value!r} — use a Decimal, "
            "an int or a decimal string"
        )
        return value
    if isinstance(value, int):
        value = Decimal(value)
    elif isinstance(value, str):
        try:
            value = Decimal(value)
        except InvalidOperation:
            problems.append(f"{path}: {value!r} is not a decimal string")
            return value
    if not isinstance(value, Decimal):
        problems.append(f"{path}: expected a Decimal, got {value!r}")
        return value
    if not value.is_finite():
        problems.append(f"{path}: non-finite number {value} refused")
    return value


def _int(value, path, problems):
    """Return ``value`` if it is an int (a bool is not)."""
    if isinstance(value, bool) or not isinstance(value, int):
        problems.append(f"{path}: expected an int, got {value!r}")
    return value


def _float(value, path, problems):
    """Return ``value`` as a finite float (an int is widened)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.append(f"{path}: expected a number, got {value!r}")
        return value
    value = float(value)
    if not math.isfinite(value):
        problems.append(f"{path}: non-finite number {value!r} refused")
    return value


def _bool(value, path, problems):
    """Return ``value`` if it is a bool."""
    if not isinstance(value, bool):
        problems.append(f"{path}: expected a bool, got {value!r}")
    return value


def _str(value, path, problems):
    """Return ``value`` if it is a str."""
    if not isinstance(value, str):
        problems.append(f"{path}: expected a str, got {value!r}")
    return value


def _none(value, path, problems):
    """Return ``value`` if it is None."""
    if value is not None:
        problems.append(f"{path}: expected null, got {value!r}")
    return value


_SCALARS = {Decimal: _decimal, int: _int, float: _float, bool: _bool, str: _str, _NONE: _none}


def _json_value(value, path, problems):
    """Return an opaque JSON value canonicalised (tuples to lists), finite."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            problems.append(f"{path}: non-finite number {value!r} refused")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            problems.append(f"{path}: non-finite number {value} refused")
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(v, f"{path}[{i}]", problems) for i, v in enumerate(value)]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                problems.append(f"{path}: key {key!r} is not a string")
                continue
            out[key] = _json_value(item, f"{path}.{key}", problems)
        return out
    problems.append(f"{path}: {type(value).__name__} is not a JSON value")
    return value


def _opaque(value, path, problems):
    """Apply the money rule to a venue payload, then canonicalise it."""
    reject_money_floats(problems, value, path)
    return _json_value(value, path, problems)


def _coerce_union(members, value, path, problems):
    """Coerce against the first member that accepts ``value``."""
    if value is None and _NONE in members:
        return None
    real = [member for member in members if member is not _NONE]
    if len(real) == 1:
        return _coerce(real[0], value, path, problems)
    for member in real:
        attempt = []
        out = _coerce(member, value, path, attempt)
        if not attempt:
            return out
    names = [getattr(member, "__name__", str(member)) for member in members]
    problems.append(f"{path}: {value!r} is none of {names}")
    return value


def _coerce_tuple(args, value, path, problems):
    """Coerce a sequence to a tuple, homogeneous (``X, ...``) or fixed-arity."""
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        problems.append(f"{path}: expected a sequence, got {value!r}")
        return value
    if len(args) == 2 and args[1] is Ellipsis:
        return tuple(_coerce(args[0], v, f"{path}[{i}]", problems) for i, v in enumerate(value))
    if len(value) != len(args):
        problems.append(f"{path}: expected {len(args)} items, got {len(value)}")
        return tuple(value)
    return tuple(
        _coerce(hint, v, f"{path}[{i}]", problems) for i, (hint, v) in enumerate(zip(args, value))
    )


def _coerce_dict(args, value, path, problems):
    """Coerce a ``dict[str, X]`` value by value."""
    if not isinstance(value, dict):
        problems.append(f"{path}: expected a mapping, got {value!r}")
        return value
    out = {}
    for key, item in value.items():
        if not isinstance(key, str):
            problems.append(f"{path}: key {key!r} is not a string")
            continue
        out[key] = _coerce(args[1], item, f"{path}.{key}", problems)
    return out


def _coerce_record(cls, value, path, problems):
    """Accept an instance of ``cls`` or rebuild one from its JSON object."""
    if isinstance(value, cls):
        return value
    if isinstance(value, dict):
        try:
            return cls.from_obj(value)
        except ProductionError as exc:
            problems.extend(f"{path}: {problem}" for problem in exc.problems)
        return value
    problems.append(f"{path}: expected {cls.__name__}, got {type(value).__name__}")
    return value


def _coerce(hint, value, path, problems):
    """Return ``value`` in canonical form for ``hint``, appending problems."""
    origin = typing.get_origin(hint)
    if origin in _UNIONS:
        return _coerce_union(typing.get_args(hint), value, path, problems)
    if origin is tuple:
        return _coerce_tuple(typing.get_args(hint), value, path, problems)
    if origin is dict:
        return _coerce_dict(typing.get_args(hint), value, path, problems)
    if hint is dict:
        if not isinstance(value, dict):
            problems.append(f"{path}: expected a mapping, got {value!r}")
            return value
        return _opaque(value, path, problems)
    if hint is object:
        return _opaque(value, path, problems)
    if isinstance(hint, type) and issubclass(hint, _Record):
        return _coerce_record(hint, value, path, problems)
    rule = _SCALARS.get(hint)
    if rule is None:
        problems.append(f"{path}: unsupported field type {hint!r}")
        return value
    return rule(value, path, problems)


def _encode(value):
    """Render a validated field value as JSON-ready data."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, _Record):
        return value.to_obj()
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    return value


def _digest(record, exclude=()):
    """Digest ``record.to_obj()`` minus ``exclude`` — the one digest recipe."""
    return canonical_hash({k: v for k, v in record.to_obj().items() if k not in exclude})


class _Record:
    """The shared contract: coerced and validated at construction, JSON both ways."""

    #: ``(field, vocabulary)`` pairs whose value must be a member; None passes.
    _CLOSED = ()

    def __post_init__(self):
        """Coerce every field, check the closed sets, then the record's invariants."""
        name = type(self).__name__
        problems = []
        hints = _hints(type(self))
        for field in fields(self):
            value = getattr(self, field.name)
            object.__setattr__(
                self, field.name, _coerce(hints[field.name], value, f"{name}.{field.name}", problems)
            )
        for field_name, allowed in self._CLOSED:
            value = getattr(self, field_name)
            if value is not None and value not in allowed:
                problems.append(f"{name}.{field_name}: {value!r} is not one of {list(allowed)}")
        if not problems:
            self._check(problems)
        if problems:
            raise ProductionError(problems)

    def _check(self, problems):
        """Append this record's own invariant problems; a hook, empty by default."""

    def to_obj(self):
        """Return the record as a JSON-ready dict of exactly its declared fields.

        Returns
        -------
        dict
            Money as decimal strings, nested records as dicts, tuples as
            lists; survives ``json.dumps`` with no custom encoder.
        """
        return {field.name: _encode(getattr(self, field.name)) for field in fields(self)}

    @classmethod
    def from_obj(cls, obj):
        """Rebuild a record from its ``to_obj()`` form, default-deny.

        Parameters
        ----------
        obj : dict
            Exactly the declared fields; nested records may be dicts,
            tuples lists, money decimal strings.

        Returns
        -------
        _Record
            An instance of ``cls`` equal to the one that produced ``obj``.

        Raises
        ------
        ProductionError
            On an unknown or missing key, a float where money belongs, a
            non-finite number, a value outside a closed set, or a broken
            invariant — every problem listed.
        """
        if not isinstance(obj, dict):
            raise ProductionError([f"{cls.__name__}.from_obj expects a dict, got {obj!r}"])
        names = tuple(field.name for field in fields(cls))
        problems = []
        _check_unknown(problems, obj, names, where=cls.__name__)
        missing = [name for name in names if name not in obj]
        if missing:
            problems.append(f"{cls.__name__}: missing key(s) {missing}")
        if problems:
            raise ProductionError(problems)
        return cls(**obj)


# ---------------------------------------------------------------------------
# Scope, quotes, candidates, proposals, findings (§5.4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionScope(_Record):
    """The ownership domain two releases contend for (§5.7.2) — canonical, non-secret.

    Compares by value, which is what makes "exact scope equality"
    checkable between the document, the release and the executor.

    Parameters
    ----------
    venue, account : str
        Non-empty names; never a credential.

    Examples
    --------
    ::

        scope = ExecutionScope(venue="paper-venue", account="acct-1")
        scope == ExecutionScope.from_obj(scope.to_obj())  # True
    """

    venue: str
    account: str

    def _check(self, problems):
        for name in ("venue", "account"):
            if not getattr(self, name):
                problems.append(f"ExecutionScope.{name} must be non-empty")


@dataclass(frozen=True)
class Quote(_Record):
    """One instrument's market at one instant.

    Parameters
    ----------
    instrument : str
    bid, ask, mid : Decimal
        Prices — decimal strings in JSON.
    asof_ms : int
        When the venue published it, epoch ms.

    Examples
    --------
    ::

        quote = Quote(
            instrument="INS1", bid=Decimal("0.40"), ask=Decimal("0.42"),
            mid=Decimal("0.41"), asof_ms=1_757_030_400_000,
        )
        quote.to_obj()["bid"]  # '0.40'
    """

    instrument: str
    bid: Decimal
    ask: Decimal
    mid: Decimal
    asof_ms: int


@dataclass(frozen=True)
class QuoteSet(_Record):
    """The quotes a tick priced its candidates against, with their digest.

    Parameters
    ----------
    quotes : tuple of Quote
    quote_digest : str
        The digest the tick binds into every permit.
    min_asof_ms : int
        The oldest quote's instant — the freshness the deadline checks.

    Examples
    --------
    ::

        quotes = QuoteSet(quotes=(quote,), quote_digest="a" * 64, min_asof_ms=quote.asof_ms)
        len(quotes.quotes)  # 1
    """

    quotes: tuple[Quote, ...]
    quote_digest: str
    min_asof_ms: int


@dataclass(frozen=True)
class Candidate(_Record):
    """An instrument the decider may propose on, with the scope keys guards measure over.

    Parameters
    ----------
    id, instrument : str
    scope_keys : tuple of str
        The keys a ``Limit`` with ``scope: per_key`` evaluates against.

    Examples
    --------
    ::

        candidate = Candidate(id="cand-1", instrument="INS1", scope_keys=("INS1",))
    """

    id: str
    instrument: str
    scope_keys: tuple[str, ...]


@dataclass(frozen=True)
class Proposal(_Record):
    """What the proposer wants to do, with the provenance it was decided from.

    Parameters
    ----------
    id, instrument : str
    side : str
        One of ``SIDES``; ``none`` abstains.
    qty, notional : Decimal or None
        The size, by quantity or by notional.
    limit : Decimal or None
        The limit price; None for a market order.
    tif : str
        One of ``TIFS``.
    expires_ms : int
    reference_price, exposure : Decimal
    direction : str
    confidence, prediction, baseline, expected_value : float
        Dimensionless — the one place floats are legitimate.
    inputs_asof_ms, quote_asof_ms : int
    inputs_digest, coverage_digest, quote_digest : str
    extra : dict
        Proposer-specific JSON; a float under a money name refuses.

    Examples
    --------
    ::

        proposal = Proposal(
            id="cand-1", instrument="INS1", side="buy", qty=Decimal("10"),
            notional=Decimal("4.10"), limit=Decimal("0.41"), tif="ioc",
            expires_ms=1_757_030_405_000, reference_price=Decimal("0.41"),
            exposure=Decimal("4.10"), direction="long", confidence=0.61,
            prediction=0.58, baseline=0.50, expected_value=0.03,
            inputs_asof_ms=1_757_030_400_000, inputs_digest="a" * 64,
            coverage_digest="b" * 64, quote_asof_ms=1_757_030_400_000,
            quote_digest="c" * 64, extra={},
        )
        proposal.to_obj()["qty"]  # '10'
    """

    id: str
    instrument: str
    side: str
    qty: Decimal | None
    notional: Decimal | None
    limit: Decimal | None
    tif: str
    expires_ms: int
    reference_price: Decimal
    exposure: Decimal
    direction: str
    confidence: float
    prediction: float
    baseline: float
    expected_value: float
    inputs_asof_ms: int
    inputs_digest: str
    coverage_digest: str
    quote_asof_ms: int
    quote_digest: str
    extra: dict

    _CLOSED = (("side", SIDES), ("tif", TIFS))


@dataclass(frozen=True)
class Finding(_Record):
    """One guard's judgement of one proposal.

    Parameters
    ----------
    guard, measure : str
    value, bound : Decimal or None
        What was measured and the bound it was held to; a ratio measure
        records its value as a Decimal too, so a finding never carries a
        float.
    window, scope_key : str
    verdict : str
        One of ``VERDICTS``.
    reason : str
        Free text, already redacted.

    Examples
    --------
    ::

        finding = Finding(
            guard="notional_limit", measure="notional", value=Decimal("4.10"),
            bound=Decimal("25.00"), window="session", scope_key="INS1",
            verdict="allow", reason="notional 4.10 within bound 25.00",
        )
    """

    guard: str
    measure: str
    value: Decimal | None
    bound: Decimal | None
    window: str
    scope_key: str
    verdict: str
    reason: str

    _CLOSED = (("verdict", VERDICTS),)


@dataclass(frozen=True)
class GateResult(_Record):
    """One checked gate of a tick — the element of ``DecisionPlan.gate_results``.

    Parameters
    ----------
    gate : str
    passed : bool
    reason : str
    at_ms : int

    Examples
    --------
    ::

        gate = GateResult(gate="watermark_age", passed=True, reason="", at_ms=1_757_030_400_000)
    """

    gate: str
    passed: bool
    reason: str
    at_ms: int


@dataclass(frozen=True)
class ScopeVerdict(_Record):
    """Whether the active authority's scope admits the final proposal.

    Parameters
    ----------
    allowed : bool
    scope_key : str
    reason : str

    Examples
    --------
    ::

        verdict = ScopeVerdict(allowed=True, scope_key="INS1", reason="")
    """

    allowed: bool
    scope_key: str
    reason: str


@dataclass(frozen=True)
class RiskVersion(_Record):
    """The versions a permit binds about the account (§5.7.1).

    Parameters
    ----------
    economic_seq : int
        The fold's economic sequence.
    executor_token : str or None
        The executor's session token; None until a session acquires one
        (a fresh or restored fold — §6's snapshot never restores it).
    accounting_tokens : tuple of str or None
        The accounting sources' monotonic tokens; None until acquired.

    Examples
    --------
    ::

        version = RiskVersion(economic_seq=41, executor_token="etok-7", accounting_tokens=("atok-3",))
    """

    economic_seq: int
    executor_token: str | None
    accounting_tokens: tuple[str, ...] | None


# ---------------------------------------------------------------------------
# Evidence (§5.4, §5.5)
# ---------------------------------------------------------------------------


def _arg_none(value, problems):
    """Refuse any argument on a ``none`` window."""
    if value is not None:
        problems.append(f"EvidenceRequirement.window_arg: a 'none' window takes no argument, got {value!r}")


def _arg_duration(value, problems):
    """Refuse a ``duration`` argument that is not milliseconds (int >= 0)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        problems.append(
            f"EvidenceRequirement.window_arg: a duration is milliseconds (int >= 0), got {value!r}"
        )


def _arg_count(value, problems):
    """Refuse a ``count`` argument that is not a positive int."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        problems.append(f"EvidenceRequirement.window_arg: a count is an int > 0, got {value!r}")


def _arg_calendar(value, problems):
    """Refuse a ``calendar`` argument that is not resolved ``(start, end)`` bounds."""
    if not isinstance(value, tuple) or value[0] > value[1]:
        problems.append(
            f"EvidenceRequirement.window_arg: a calendar window is its resolved "
            f"(start_ms, end_ms) bounds, got {value!r}"
        )


#: How ``window_arg`` is normalised, per ``WINDOW_KINDS`` member.
_WINDOW_ARG_RULES = {
    "none": _arg_none,
    "duration": _arg_duration,
    "count": _arg_count,
    "calendar": _arg_calendar,
}
if set(_WINDOW_ARG_RULES) != set(WINDOW_KINDS):
    raise ProductionError(["records.py: the window_arg rules do not cover WINDOW_KINDS"])


@dataclass(frozen=True)
class EvidenceRequirement(_Record):
    """What a ``Measure`` declares it needs before sizing — the question, not the answer.

    Born complete and frozen: ``requirement_digest`` is computed in
    ``__post_init__`` over exactly the eight fields in declared order, so
    two measures asking the same question produce one digest and
    accounting fetches the evidence once. ``window_arg`` is normalised by
    ``window_kind``: a duration to milliseconds, a count to an int, a
    calendar window to its resolved ``(start_ms, end_ms)`` bounds, a
    ``none`` window to None.

    Parameters
    ----------
    measure : str
    window_kind : str
        One of ``WINDOW_KINDS``.
    window_arg : int or tuple of (int, int) or None
    scope_key : str
    window_start_ms, window_end_ms, baseline_at_ms : int
    include_working : bool

    Attributes
    ----------
    requirement_digest : str
        ``canonical_hash(to_obj())``; not a field, so never serialized.

    Examples
    --------
    ::

        requirement = EvidenceRequirement(
            measure="pnl", window_kind="duration", window_arg=86_400_000,
            scope_key="INS1", window_start_ms=1_756_944_000_000,
            window_end_ms=1_757_030_400_000, baseline_at_ms=1_756_944_000_000,
            include_working=True,
        )
        len(requirement.requirement_digest)  # 64
    """

    measure: str
    window_kind: str
    window_arg: int | tuple[int, int] | None
    scope_key: str
    window_start_ms: int
    window_end_ms: int
    baseline_at_ms: int
    include_working: bool

    _CLOSED = (("window_kind", WINDOW_KINDS),)

    def __post_init__(self):
        """Validate, then stamp the digest the record is born with."""
        super().__post_init__()
        object.__setattr__(self, "requirement_digest", _digest(self))

    def _check(self, problems):
        _WINDOW_ARG_RULES[self.window_kind](self.window_arg, problems)


@dataclass(frozen=True)
class MeasureEvidence(_Record):
    """The answer to an ``EvidenceRequirement``, as accounting snapshotted it.

    Parameters
    ----------
    requirement_digest : str
    value : Decimal
        Always a Decimal, even for a ratio measure.
    sample_count : int
    window_start_ms, window_end_ms : int
    scope_key : str
    effective_at_ms, known_at_ms : int
        Bitemporal (D21): when it was true, when we learned it.
    source_digests : dict of str to str

    Examples
    --------
    ::

        evidence = MeasureEvidence(
            requirement_digest=requirement.requirement_digest, value=Decimal("-12.50"),
            sample_count=37, window_start_ms=1_756_944_000_000,
            window_end_ms=1_757_030_400_000, scope_key="INS1",
            effective_at_ms=1_757_030_400_000, known_at_ms=1_757_030_400_000,
            source_digests={"fills": "a" * 64},
        )
    """

    requirement_digest: str
    value: Decimal
    sample_count: int
    window_start_ms: int
    window_end_ms: int
    scope_key: str
    effective_at_ms: int
    known_at_ms: int
    source_digests: dict[str, str]


# ---------------------------------------------------------------------------
# Account, orders, fills (§5.4, §5.7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Balance(_Record):
    """One currency's balance at the venue.

    Parameters
    ----------
    currency : str
    total, available : Decimal
    native : dict or None
        The venue's own record, opaque; a float under a money name refuses.

    Examples
    --------
    ::

        balance = Balance(currency="USD", total=Decimal("1000.00"), available=Decimal("900.00"), native={})
    """

    currency: str
    total: Decimal
    available: Decimal
    native: dict | None


@dataclass(frozen=True)
class Position(_Record):
    """One instrument's position, from our fold or the venue's report.

    Parameters
    ----------
    instrument : str
    qty, avg_cost : Decimal
    source : str
        One of ``POSITION_SOURCES``.
    native : dict or None

    Examples
    --------
    ::

        position = Position(
            instrument="INS1", qty=Decimal("5"), avg_cost=Decimal("0.39"), source="derived", native={},
        )
    """

    instrument: str
    qty: Decimal
    avg_cost: Decimal
    source: str
    native: dict | None

    _CLOSED = (("source", POSITION_SOURCES),)


@dataclass(frozen=True)
class Ack(_Record):
    """What ``submit`` always returns — including a refusal (§5.15).

    A venue that rejects a re-used ``client_ref`` yields
    ``Ack(status="rejected", reason="duplicate_ref")``: ``DuplicateRef`` is a
    ``reason`` value, never an exception.

    Parameters
    ----------
    client_ref : str
    venue_ref : str or None
        None when nothing reached the venue.
    status : str
        One of ``STATUSES``.
    ts_ms : int
    filled_qty : Decimal
    avg_price : Decimal or None
    fee : Decimal
    reason : str
    native : dict or None

    Examples
    --------
    ::

        ack = Ack(
            client_ref="cref-1", venue_ref="vref-1", status="filled", ts_ms=1_757_030_400_000,
            filled_qty=Decimal("10"), avg_price=Decimal("0.41"), fee=Decimal("0.02"),
            reason="", native={},
        )
        not_sent = Ack(
            client_ref="cref-2", venue_ref=None, status="not_sent", ts_ms=1_757_030_400_000,
            filled_qty=Decimal("0"), avg_price=None, fee=Decimal("0"), reason="shadow", native=None,
        )
    """

    client_ref: str
    venue_ref: str | None
    status: str
    ts_ms: int
    filled_qty: Decimal
    avg_price: Decimal | None
    fee: Decimal
    reason: str
    native: dict | None

    _CLOSED = (("status", STATUSES),)


@dataclass(frozen=True)
class OrderState(Ack):
    """An ``Ack`` plus the order it describes; ``filled_qty + remaining_qty == qty``.

    Parameters
    ----------
    instrument : str
    side : str
        One of ``SIDES``.
    qty, remaining_qty : Decimal
    limit : Decimal or None
    tif : str
        One of ``TIFS``.
    created_ms, updated_ms : int

    Examples
    --------
    ::

        order = OrderState(
            client_ref="cref-1", venue_ref="vref-1", status="partial", ts_ms=1_757_030_400_000,
            filled_qty=Decimal("4"), avg_price=Decimal("0.41"), fee=Decimal("0.01"),
            reason="", native={}, instrument="INS1", side="buy", qty=Decimal("10"),
            remaining_qty=Decimal("6"), limit=Decimal("0.41"), tif="gtc",
            created_ms=1_757_030_400_000, updated_ms=1_757_030_400_010,
        )
    """

    instrument: str
    side: str
    qty: Decimal
    remaining_qty: Decimal
    limit: Decimal | None
    tif: str
    created_ms: int
    updated_ms: int

    _CLOSED = (("status", STATUSES), ("side", SIDES), ("tif", TIFS))

    def _check(self, problems):
        if self.filled_qty + self.remaining_qty != self.qty:
            problems.append(
                f"OrderState: filled_qty {self.filled_qty} + remaining_qty "
                f"{self.remaining_qty} != qty {self.qty}"
            )


@dataclass(frozen=True)
class Fill(_Record):
    """One execution, as the venue reported it.

    Parameters
    ----------
    fill_id, venue_ref, client_ref, instrument : str
    side : str
        One of ``SIDES``.
    qty, price, fee : Decimal
    fee_currency : str
    liquidity : str
        One of ``LIQUIDITY``.
    status : str
        One of ``FILL_STATUSES``.
    ts_ms : int
    native : dict or None

    Examples
    --------
    ::

        fill = Fill(
            fill_id="fill-1", venue_ref="vref-1", client_ref="cref-1", instrument="INS1",
            side="buy", qty=Decimal("4"), price=Decimal("0.41"), fee=Decimal("0.01"),
            fee_currency="USD", liquidity="taker", status="final",
            ts_ms=1_757_030_400_000, native={},
        )
    """

    fill_id: str
    venue_ref: str
    client_ref: str
    instrument: str
    side: str
    qty: Decimal
    price: Decimal
    fee: Decimal
    fee_currency: str
    liquidity: str
    status: str
    ts_ms: int
    native: dict | None

    _CLOSED = (("side", SIDES), ("liquidity", LIQUIDITY), ("status", FILL_STATUSES))


@dataclass(frozen=True)
class Settlement(_Record):
    """A resolved instrument's payout.

    Parameters
    ----------
    instrument : str
    outcome : str
        The venue's resolution label, opaque.
    qty, payout, fee : Decimal
    settled_ms : int
    native : dict or None

    Examples
    --------
    ::

        settlement = Settlement(
            instrument="INS1", outcome="yes", qty=Decimal("10"), payout=Decimal("10.00"),
            fee=Decimal("0.05"), settled_ms=1_757_030_400_000, native={},
        )
    """

    instrument: str
    outcome: str
    qty: Decimal
    payout: Decimal
    fee: Decimal
    settled_ms: int
    native: dict | None


def _risk_view(record):
    """Return a nested record's economic content: its fields minus ``_RISK_EXCLUDED``."""
    return {k: v for k, v in record.to_obj().items() if k not in _RISK_EXCLUDED}


@dataclass(frozen=True)
class AccountState(_Record):
    """The account as accounting snapshotted it for one decision (§5.7.1).

    Parameters
    ----------
    risk_version : RiskVersion
    asof_ms : int
        When it was observed — excluded from ``risk_digest``.
    evidence_digest : str
    balances : tuple of Balance
    positions : tuple of Position
    working : tuple of OrderState
    measure_evidence : dict of str to dict of str to MeasureEvidence
        ``{requirement_digest: {scope_key: evidence}}``.
    source_digests : dict of str to str

    Examples
    --------
    ::

        account = AccountState(
            risk_version=version, asof_ms=1_757_030_400_000, evidence_digest="a" * 64,
            balances=(balance,), positions=(position,), working=(order,),
            measure_evidence={requirement.requirement_digest: {"INS1": evidence}},
            source_digests={"fills": "a" * 64},
        )
        len(account.risk_digest())  # 64
    """

    risk_version: RiskVersion
    asof_ms: int
    evidence_digest: str
    balances: tuple[Balance, ...]
    positions: tuple[Position, ...]
    working: tuple[OrderState, ...]
    measure_evidence: dict[str, dict[str, MeasureEvidence]]
    source_digests: dict[str, str]

    def risk_digest(self):
        """Digest the economic content a permit binds.

        Balances, positions, working orders and evidence values with their
        window bounds and source digests, each minus observation-only
        timestamps and venue-native payloads (``_RISK_EXCLUDED``); the
        observation instant ``asof_ms`` and the versions are excluded, so
        re-observing the same account is not an economic change while
        every economic correction moves a value or a source digest.

        Returns
        -------
        str
            64 hex characters.
        """
        return canonical_hash(
            {
                "balances": [_risk_view(b) for b in self.balances],
                "positions": [_risk_view(p) for p in self.positions],
                "working": [_risk_view(w) for w in self.working],
                "measure_evidence": {
                    requirement: {scope: _risk_view(e) for scope, e in by_scope.items()}
                    for requirement, by_scope in self.measure_evidence.items()
                },
                "source_digests": dict(self.source_digests),
            }
        )


# ---------------------------------------------------------------------------
# Feed, provenance, tick (§5.2, §5.13, §6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InputWatermark(_Record):
    """How fresh one required key's input is.

    Parameters
    ----------
    key : str
    latest_asof_ms : int
    source_digest : str

    Examples
    --------
    ::

        watermark = InputWatermark(key="INS1", latest_asof_ms=1_757_030_399_000, source_digest="a" * 64)
    """

    key: str
    latest_asof_ms: int
    source_digest: str


@dataclass(frozen=True)
class EntryBatch(_Record):
    """What the feed read for one tick; ``data_asof_ms`` is the OLDEST watermark (D6).

    One fresh instrument cannot hide a stale input: the batch's as-of is
    the minimum over every required key, and at least one key is required.

    Parameters
    ----------
    outputs : dict
        The entry node's outputs, JSON-shaped.
    watermarks_by_key : dict of str to InputWatermark
    required_keys_digest, coverage_digest : str
    data_asof_ms : int
    inputs_digest, source_config_hash : str

    Examples
    --------
    ::

        batch = EntryBatch(
            outputs={"rows": []}, watermarks_by_key={"INS1": watermark},
            required_keys_digest="a" * 64, coverage_digest="b" * 64,
            data_asof_ms=watermark.latest_asof_ms, inputs_digest="c" * 64,
            source_config_hash="a" * 64,
        )
    """

    outputs: dict
    watermarks_by_key: dict[str, InputWatermark]
    required_keys_digest: str
    coverage_digest: str
    data_asof_ms: int
    inputs_digest: str
    source_config_hash: str

    def _check(self, problems):
        if not self.watermarks_by_key:
            problems.append("EntryBatch.watermarks_by_key: at least one required key is needed")
            return
        oldest = min(w.latest_asof_ms for w in self.watermarks_by_key.values())
        if self.data_asof_ms != oldest:
            problems.append(
                f"EntryBatch.data_asof_ms {self.data_asof_ms} must be the oldest watermark {oldest}"
            )


@dataclass(frozen=True)
class Provenance(_Record):
    """The five bindings a tick freezes before any leg runs.

    Parameters
    ----------
    inputs_asof_ms, quote_asof_ms : int
    inputs_digest, coverage_digest, quote_digest : str

    Examples
    --------
    ::

        provenance = Provenance(
            inputs_asof_ms=1_757_030_400_000, inputs_digest="a" * 64, coverage_digest="b" * 64,
            quote_asof_ms=1_757_030_400_000, quote_digest="c" * 64,
        )
    """

    inputs_asof_ms: int
    inputs_digest: str
    coverage_digest: str
    quote_asof_ms: int
    quote_digest: str


@dataclass(frozen=True)
class FeedResult(_Record):
    """What one ``fetch`` came to.

    Parameters
    ----------
    status : str
        One of ``FEED_STATUSES``.
    acq_id : str or None
    records_added : int
    source_config_hash : str or None
    at_ms : int

    Examples
    --------
    ::

        result = FeedResult(
            status="live", acq_id="acq-9", records_added=12, source_config_hash="a" * 64,
            at_ms=1_757_030_400_000,
        )
    """

    status: str
    acq_id: str | None
    records_added: int
    source_config_hash: str | None
    at_ms: int

    _CLOSED = (("status", FEED_STATUSES),)


@dataclass(frozen=True)
class FeedAge(_Record):
    """One key's staleness at a tick.

    Parameters
    ----------
    key : str
    age_ms, watermark_ms : int

    Examples
    --------
    ::

        age = FeedAge(key="INS1", age_ms=1_000, watermark_ms=1_757_030_399_000)
    """

    key: str
    age_ms: int
    watermark_ms: int


@dataclass(frozen=True)
class TickStart(_Record):
    """The record appended before any tick work (§6).

    Parameters
    ----------
    tick_id : str
    tick_at_ms : int
    release_hash : str

    Examples
    --------
    ::

        start = TickStart(tick_id="tick-1", tick_at_ms=1_757_030_400_000, release_hash="d" * 64)
    """

    tick_id: str
    tick_at_ms: int
    release_hash: str


@dataclass(frozen=True)
class TickResult(_Record):
    """Everything the phases produced, so a phase never writes a record itself (§5.4).

    The loop adds only what it alone holds (``tick_at``, ``calendar``,
    ``overrun_absorbed``, ``health``, ``breaker``, ``rung``) when it writes
    §6's terminal ``tick`` and ``decision``. The ``feed`` block is a member
    because five of its seven members live only in phase-local records.

    Parameters
    ----------
    tick_id : str
    status : str
        One of ``TICK_STATUSES``.
    data_asof_ms : int or None
    coverage_digest, inputs_digest : str or None
    decision_plan_ids : tuple of str
    legs : tuple of dict
        The serialized ``LegResult``s.
    findings : tuple of Finding
    observed_at_ms : int
    nav : Decimal or None
        The marked portfolio value; None when a mark is missing or
        balances span currencies — a recorded fact, not a gap.
    latency_ms : dict of str to int
        Keys are ``TICK_PHASES`` members.
    leg_latency_ms : dict of str to int
        Keys are ``LEG_LATENCY_BUCKETS`` members.
    refusal_reason : str
    error : dict or None
        ``{class, text}`` when the tick failed.
    feed : dict
        Exactly §6's seven members; ``status`` is a ``FEED_STATUSES`` member.

    Examples
    --------
    ::

        result = TickResult(
            tick_id="tick-1", status="decided", data_asof_ms=1_757_030_399_000,
            coverage_digest="b" * 64, inputs_digest="c" * 64, decision_plan_ids=("plan-1",),
            legs=({"leg_id": "leg-1", "result": "submitted"},), findings=(finding,),
            observed_at_ms=1_757_030_400_040, nav=Decimal("1004.10"),
            latency_ms={"gate": 1}, leg_latency_ms={"guard": 2}, refusal_reason="", error=None,
            feed={
                "status": "live", "acq_id": "acq-9", "records_added": 12,
                "source_config_hash": "a" * 64, "required_keys_digest": "a" * 64,
                "watermarks_by_key": {"INS1": 1_757_030_399_000}, "coverage_digest": "b" * 64,
            },
        )
    """

    tick_id: str
    status: str
    data_asof_ms: int | None
    coverage_digest: str | None
    inputs_digest: str | None
    decision_plan_ids: tuple[str, ...]
    legs: tuple[dict, ...]
    findings: tuple[Finding, ...]
    observed_at_ms: int
    nav: Decimal | None
    latency_ms: dict[str, int]
    leg_latency_ms: dict[str, int]
    refusal_reason: str
    error: dict | None
    feed: dict

    _CLOSED = (("status", TICK_STATUSES),)

    def _check(self, problems):
        for name, allowed in (("latency_ms", TICK_PHASES), ("leg_latency_ms", LEG_LATENCY_BUCKETS)):
            unknown = sorted(set(getattr(self, name)) - set(allowed))
            if unknown:
                problems.append(f"TickResult.{name}: unknown key(s) {unknown} — allowed: {list(allowed)}")
        if set(self.feed) != set(_FEED_MEMBERS):
            problems.append(
                f"TickResult.feed must carry exactly {list(_FEED_MEMBERS)}, got {sorted(self.feed)}"
            )
        elif self.feed["status"] not in FEED_STATUSES:
            problems.append(
                f"TickResult.feed.status: {self.feed['status']!r} is not one of {list(FEED_STATUSES)}"
            )
        if self.error is not None and set(self.error) != set(_ERROR_MEMBERS):
            problems.append(
                f"TickResult.error must carry exactly {list(_ERROR_MEMBERS)}, got {sorted(self.error)}"
            )


# ---------------------------------------------------------------------------
# Plans, intents, reductions, permits (§5.4, §5.13.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionPlan(_Record):
    """A proposal after complete pre-submit evaluation — eighteen fields, digest order.

    Parameters
    ----------
    plan_id : str
    inputs_asof_ms, quote_asof_ms, evidence_asof_ms : int
    inputs_digest, coverage_digest, quote_digest, evidence_digest : str
    provenance_digests : dict of str to str
        Entry, head and candidate provenance.
    original : Proposal
    final : Proposal or None
        The proposal as amended; None when nothing survived the chain.
    findings : tuple of Finding
    gate_results : tuple of GateResult
    scope_verdict : ScopeVerdict
    risk_effect : str
        One of ``RISK_EFFECTS``.
    risk_version : RiskVersion
    risk_state_digest : str
    result : str
        One of ``PLAN_RESULTS``.

    Examples
    --------
    ::

        plan = DecisionPlan(
            plan_id="plan-1", inputs_asof_ms=1_757_030_400_000, inputs_digest="a" * 64,
            coverage_digest="b" * 64, quote_asof_ms=1_757_030_400_000, quote_digest="c" * 64,
            evidence_asof_ms=1_757_030_400_000, evidence_digest="a" * 64,
            provenance_digests={"entry": "a" * 64, "head": "b" * 64, "candidate": "cand-1"},
            original=proposal, final=proposal, findings=(finding,), gate_results=(gate,),
            scope_verdict=verdict, risk_effect="increase", risk_version=version,
            risk_state_digest="b" * 64, result="submit",
        )
        len(plan.decision_plan_digest())  # 64
    """

    plan_id: str
    inputs_asof_ms: int
    inputs_digest: str
    coverage_digest: str
    quote_asof_ms: int
    quote_digest: str
    evidence_asof_ms: int
    evidence_digest: str
    provenance_digests: dict[str, str]
    original: Proposal
    final: Proposal | None
    findings: tuple[Finding, ...]
    gate_results: tuple[GateResult, ...]
    scope_verdict: ScopeVerdict
    risk_effect: str
    risk_version: RiskVersion
    risk_state_digest: str
    result: str

    _CLOSED = (("risk_effect", RISK_EFFECTS), ("result", PLAN_RESULTS))

    def decision_plan_digest(self):
        """Digest all eighteen fields in declared order.

        Returns
        -------
        str
            ``canonical_hash(to_obj())``.
        """
        return _digest(self)


@dataclass(frozen=True)
class Intent(_Record):
    """The sole canonical intent: what the leg built to submit (§5.4).

    Parameters
    ----------
    client_ref : str
        The deterministic reference — an identifier of the intent, not
        part of it, so ``intent_digest`` excludes it.
    decision_plan_id, decision_plan_digest : str
    proposal : Proposal
    created_ms : int
    authority_id : str or None
        Included in the digest: two intents authorised under different
        arms must not hash alike.
    release_hash : str
    inputs_asof_ms, quote_asof_ms, evidence_asof_ms : int
    inputs_digest, coverage_digest, quote_digest, evidence_digest : str
    risk_version : RiskVersion
    risk_state_digest : str

    Examples
    --------
    ::

        intent = Intent(
            client_ref="cref-1", decision_plan_id="plan-1",
            decision_plan_digest=plan.decision_plan_digest(), proposal=proposal,
            created_ms=1_757_030_400_000, authority_id="auth-1", release_hash="d" * 64,
            inputs_asof_ms=1_757_030_400_000, inputs_digest="a" * 64, coverage_digest="b" * 64,
            quote_asof_ms=1_757_030_400_000, quote_digest="c" * 64,
            evidence_asof_ms=1_757_030_400_000, evidence_digest="a" * 64,
            risk_version=version, risk_state_digest="b" * 64,
        )
        len(intent.intent_digest())  # 64
    """

    client_ref: str
    decision_plan_id: str
    decision_plan_digest: str
    proposal: Proposal
    created_ms: int
    authority_id: str | None
    release_hash: str
    inputs_asof_ms: int
    inputs_digest: str
    coverage_digest: str
    quote_asof_ms: int
    quote_digest: str
    evidence_asof_ms: int
    evidence_digest: str
    risk_version: RiskVersion
    risk_state_digest: str

    def intent_digest(self):
        """Digest the intent minus ``client_ref``.

        Returns
        -------
        str
            ``canonical_hash(to_obj() minus client_ref)``.
        """
        return _digest(self, exclude=("client_ref",))


@dataclass(frozen=True)
class ReductionIntent(_Record):
    """What a maker signs at ``flatten-request`` time — seven fields, all signed.

    ``candidate`` is signed with the rest because scope keys live on the
    candidate: the maker approves the scope the limits will be measured
    over, not just the order. Its digest is a different hash of a
    different object from ``Intent.intent_digest`` and is never spelled
    the same way.

    Parameters
    ----------
    release_hash, request_id : str
    index : int
        The entry's position in the request; two byte-identical proposals
        still differ.
    candidate : Candidate
    proposal : Proposal
    risk_state_digest : str
        The state the maker inspected; deliberately not re-verified at
        execution (§5.4).
    expires_ms : int

    Examples
    --------
    ::

        reduction = ReductionIntent(
            release_hash="d" * 64, request_id="req-1", index=0, candidate=candidate,
            proposal=proposal, risk_state_digest="b" * 64, expires_ms=1_757_031_000_000,
        )
        len(reduction.reduction_intent_digest())  # 64
    """

    release_hash: str
    request_id: str
    index: int
    candidate: Candidate
    proposal: Proposal
    risk_state_digest: str
    expires_ms: int

    def reduction_intent_digest(self):
        """Digest exactly the seven signed fields in declared order.

        Returns
        -------
        str
            ``canonical_hash(to_obj())``.
        """
        return _digest(self)


@dataclass(frozen=True)
class ReductionPlan(_Record):
    """The stored flatten request: its intents and their digests, pinned to agree.

    Parameters
    ----------
    release_hash, risk_state_digest : str
    intents : tuple of ReductionIntent
    reduction_intent_digests : tuple of str
        Must equal each intent's ``reduction_intent_digest()`` in order.
    expires_ms : int

    Examples
    --------
    ::

        plan = ReductionPlan(
            release_hash="d" * 64, risk_state_digest="b" * 64, intents=(reduction,),
            reduction_intent_digests=(reduction.reduction_intent_digest(),),
            expires_ms=1_757_031_000_000,
        )
    """

    release_hash: str
    risk_state_digest: str
    intents: tuple[ReductionIntent, ...]
    reduction_intent_digests: tuple[str, ...]
    expires_ms: int

    def _check(self, problems):
        expected = tuple(intent.reduction_intent_digest() for intent in self.intents)
        if self.reduction_intent_digests != expected:
            problems.append(
                "ReductionPlan.reduction_intent_digests must be each intent's "
                "reduction_intent_digest() in order"
            )


@dataclass(frozen=True)
class ReductionAuthorization(_Record):
    """The checker's grant of one single-use right per reduction intent digest.

    Parameters
    ----------
    authority_id, release_hash, request_id : str
    reduction_intent_digests : tuple of str
    expires_ms : int

    Examples
    --------
    ::

        grant = ReductionAuthorization(
            authority_id="auth-2", release_hash="d" * 64, request_id="req-1",
            reduction_intent_digests=(reduction.reduction_intent_digest(),),
            expires_ms=1_757_031_000_000,
        )
    """

    authority_id: str
    release_hash: str
    request_id: str
    reduction_intent_digests: tuple[str, ...]
    expires_ms: int


@dataclass(frozen=True)
class Permit(_Record):
    """The frozen dataclass base every ``submit`` accepts — deliberately not an ABC (§5.15).

    Parameters
    ----------
    plan_id, decision_plan_digest, client_ref : str
    valid_until_ms : int

    Examples
    --------
    ::

        permit = Permit(
            plan_id="plan-1", decision_plan_digest=plan.decision_plan_digest(),
            client_ref="cref-1", valid_until_ms=1_757_030_430_000,
        )
    """

    plan_id: str
    decision_plan_digest: str
    client_ref: str
    valid_until_ms: int


@dataclass(frozen=True)
class SimulatedPermit(Permit):
    """What shadow, paper and recorded executors receive: nothing outward-authorising.

    Adds no field to ``Permit``; ``LiveExecutor`` refuses it by type.

    Examples
    --------
    ::

        permit = SimulatedPermit(
            plan_id="plan-1", decision_plan_digest=plan.decision_plan_digest(),
            client_ref="cref-1", valid_until_ms=1_757_030_430_000,
        )
    """


@dataclass(frozen=True)
class ActPermit(Permit):
    """The live binding only an ``Authority`` constructs (§5.13.1).

    Binds both ``intent_digest`` (the verifier recomputes it to prove the
    order is the one planned) and ``reduction_right_digest`` (the right
    being consumed; None for a model leg), plus every version, digest,
    scope and fence the safety epoch covers.

    Parameters
    ----------
    authority_id, release_hash, intent_digest, instrument : str
    risk_effect : str
        One of ``RISK_EFFECTS``.
    inputs_asof_ms, quote_asof_ms, evidence_asof_ms : int
    inputs_digest, coverage_digest, quote_digest, evidence_digest : str
    authority_scope_digest : str
    reduction_right_digest : str or None
    risk_version : RiskVersion
    risk_state_digest, readiness_digest : str
    readiness_until_ms : int
    lease_scope : ExecutionScope
    fencing_token : int
    safety_epoch_digest : str
    checked_at_ms : int

    Examples
    --------
    ::

        permit = ActPermit(
            plan_id="plan-1", decision_plan_digest=plan.decision_plan_digest(),
            client_ref="cref-1", valid_until_ms=1_757_030_430_000, authority_id="auth-1",
            release_hash="d" * 64, intent_digest=intent.intent_digest(), instrument="INS1",
            risk_effect="increase", inputs_asof_ms=1_757_030_400_000, inputs_digest="a" * 64,
            coverage_digest="b" * 64, quote_asof_ms=1_757_030_400_000, quote_digest="c" * 64,
            evidence_asof_ms=1_757_030_400_000, evidence_digest="a" * 64,
            authority_scope_digest="c" * 64, reduction_right_digest=None, risk_version=version,
            risk_state_digest="b" * 64, readiness_digest="a" * 64,
            readiness_until_ms=1_757_034_000_000, lease_scope=scope, fencing_token=17,
            safety_epoch_digest="c" * 64, checked_at_ms=1_757_030_400_000,
        )
    """

    authority_id: str
    release_hash: str
    intent_digest: str
    instrument: str
    risk_effect: str
    inputs_asof_ms: int
    inputs_digest: str
    coverage_digest: str
    quote_asof_ms: int
    quote_digest: str
    evidence_asof_ms: int
    evidence_digest: str
    authority_scope_digest: str
    reduction_right_digest: str | None
    risk_version: RiskVersion
    risk_state_digest: str
    readiness_digest: str
    readiness_until_ms: int
    lease_scope: ExecutionScope
    fencing_token: int
    safety_epoch_digest: str
    checked_at_ms: int

    _CLOSED = (("risk_effect", RISK_EFFECTS),)


# ---------------------------------------------------------------------------
# Monitoring and policy values (§5.10, §5.11, §5.14)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Alert(_Record):
    """One alert, firing or resolved.

    Parameters
    ----------
    fingerprint : str
        The dedup key.
    severity : str
        One of ``SEVERITIES``.
    status : str
        One of ``ALERT_STATUSES``.
    summary, source : str
    tick_id : str or None
    at_ms : int
    labels : dict of str to str

    Examples
    --------
    ::

        alert = Alert(
            fingerprint="a" * 64, severity="warning", status="firing", summary="feed degraded",
            source="feed", tick_id="tick-1", at_ms=1_757_030_400_000, labels={"scope": "INS1"},
        )
    """

    fingerprint: str
    severity: str
    status: str
    summary: str
    source: str
    tick_id: str | None
    at_ms: int
    labels: dict[str, str]

    _CLOSED = (("severity", SEVERITIES), ("status", ALERT_STATUSES))


@dataclass(frozen=True)
class Verdict(_Record):
    """A monitor's answer over its current window.

    Parameters
    ----------
    status : str
        One of ``MONITOR_STATUSES``.
    statistic, threshold : float or None
        Operational telemetry, never an input to a decision.
    n_ref, n_cur : int
    window, slice : str
    provisional : bool

    Examples
    --------
    ::

        verdict = Verdict(
            status="ok", statistic=0.02, threshold=0.25, n_ref=500, n_cur=120,
            window="count:120", slice="all", provisional=False,
        )
    """

    status: str
    statistic: float | None
    threshold: float | None
    n_ref: int
    n_cur: int
    window: str
    slice: str
    provisional: bool

    _CLOSED = (("status", MONITOR_STATUSES),)


@dataclass(frozen=True)
class PolicyRequest(_Record):
    """What ``Rule.veto`` receives — nine closed facts about the moment (§5.14).

    Parameters
    ----------
    operation : str
        One of ``OPERATIONS``.
    risk_effect : str
        One of ``RISK_EFFECTS``.
    rung : str
        One of ``RUNGS``.
    breaker : str
        One of ``BREAKER_STATES``.
    health : str
        One of ``HEALTH_STATES``.
    readiness : str
        One of ``READINESS_VERDICTS``.
    authority : str or None
        One of ``AUTHORITY_ROLES``, or ``None`` when no authority is in force
        (the request must be able to say so — D11 refuses such a submit).
    origin : str
        One of ``LEG_ORIGINS``.
    pending_control : bool

    Examples
    --------
    ::

        request = PolicyRequest(
            operation="submit", risk_effect="increase", rung="paper", breaker="active",
            health="ready", readiness="go", authority="ordinary", origin="model",
            pending_control=False,
        )
    """

    operation: str
    risk_effect: str
    rung: str
    breaker: str
    health: str
    readiness: str
    authority: str | None
    origin: str
    pending_control: bool

    _CLOSED = (
        ("operation", OPERATIONS),
        ("risk_effect", RISK_EFFECTS),
        ("rung", RUNGS),
        ("breaker", BREAKER_STATES),
        ("health", HEALTH_STATES),
        ("readiness", READINESS_VERDICTS),
        ("authority", AUTHORITY_ROLES),
        ("origin", LEG_ORIGINS),
    )
