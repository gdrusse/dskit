"""D21's bitemporal join: what happened to a leg, as far as anyone knew at time T (§5.13.2).

This module is the only producer of the ``outcome`` record §6 defines, and
it answers one question the same way twice — which is what makes an
attribution number auditable rather than merely current. Four rules carry
it, and every one of them is a refusal somewhere below.

**The forward as-of rule is strict.** :func:`forward_asof` matches a leg to
the FIRST event whose key is the leg's and whose instant is *strictly
greater* than the leg's ``decided_at_ms``. An event at the very instant of
the decision is one the decision could itself have seen, so admitting it
would score a forecast against its own input. The rule is a module
function rather than a method because both sources need exactly it, and a
second copy is the defect this repository cares most about.

**A source finds; the join stamps.** An :class:`OutcomeSource` reads its
own world — the executor's settlements, or a derived label stream on the
onboarding root the run was trained from — and reads no ledger. The join
stamps every answer's ``known_at_ms`` with the ONE instant its caller read
from the clock, so a source cannot back-date what it found, and a
crash-replayed :meth:`OutcomeJoin.collect` at the same instant produces
byte-identical payloads that ``Ledger.append`` dedups instead of refusing
as a changed payload under a reused id (§6's rule for
``cash_flow.known_at_ms``, for the same reason).

**Correction is a chain, and a link may be replaced once.**
:meth:`OutcomeJoin.record` refuses a ``supersedes`` that names anything but
an ``outcome`` of this series, and refuses one that has already been
superseded. §6's ``outcome`` body carries no id of its own, so
:func:`outcome_record_id` is the ONE recipe that MINTS one — and the only
place it is used. The chain walk reads the id the LEDGER stored, never a
re-derivation: ``release_hash`` is a term of the recipe, a serve series
outlives the release it was started under, and a join that re-derived an
id would compute a different one for every record written before the last
deployment and silently lose the chain there.

**Vintage reproducibility.** :meth:`OutcomeJoin.current_outcome` re-asked
at an earlier ``at_ms`` reproduces exactly what was knowable then. That is
what D21 exists for, and the reason ``report.py`` never reads a settlement
directly.

Two places where §5.13.2 could not be implemented as written are resolved
here and marked in the docstrings that carry them: :meth:`OutcomeSource.poll`
takes the standing outcomes as a third argument (without them no source can
tell a correction from a first arrival, and §5.13.2 requires
``SettlementOutcomes`` to produce ``corrected``), and the fold carries no
outcome projection — ``SeriesState.apply`` folds an ``outcome`` to nothing —
so "already standing in the fold" is read through
:class:`~dskit.production.reconcile.LedgerHistory`, the one reader of that
history.

Import cost: stdlib plus ``dskit.onboarding`` (the observations read seam
and the source registry), ``dskit.pipeline.node`` and this package.
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from types import MappingProxyType

import dskit.onboarding.observations as observations
from dskit.onboarding.acquire import find_active_source
from dskit.onboarding.base import AssetError
from dskit.pipeline.node import check_int_param, reject_unknown_params
from dskit.production.base import (
    ProductionError,
    Registry,
    _check_str,
    canonical_hash,
    pin_members,
)
from dskit.production.reconcile import LedgerHistory
from dskit.production.records import DecidedLeg, Outcome, Settlement
from dskit.production.redact import get_logger
from dskit.production.vocab import OUTCOME_KINDS, OUTCOME_SOURCES, RECORD_KINDS

__all__ = [
    "DEFAULT_OUTCOME_LOOKBACK_MS",
    "LabelOutcomes",
    "OUTCOME_ID_TAG",
    "OUTCOME_SOURCE_KINDS",
    "OutcomeJoin",
    "OutcomeSource",
    "SettlementOutcomes",
    "forward_asof",
    "outcome_record_id",
]

_LOG = get_logger("outcomes")

#: The first term of the ``outcome`` id derivation (the ``ids.py``
#: tagged-tuple idiom, so two recipes cannot collide).
OUTCOME_ID_TAG = "outcome-v1"

#: How far back a source looks for events by default: seven days, long
#: enough that a weekend settlement is not missed and short enough that a
#: poll is bounded. ONE name, read by ``validate_params`` and the run alike.
DEFAULT_OUTCOME_LOOKBACK_MS = 604_800_000

_NOTES = ("notes",)
_ZERO = Decimal(0)
_ONE = Decimal(1)

#: The record kind this module produces, pinned to §6's vocabulary.
_OUTCOME = pin_members("outcomes.py's record kind", ("outcome",), RECORD_KINDS)[0]

_SETTLED, _MARKED, _VOIDED, _PARTIAL, _CORRECTED = pin_members(
    "outcomes.py's outcome kinds",
    ("settled", "marked", "voided", "partial", "corrected"),
    OUTCOME_KINDS,
    exact=True,
)

_SETTLEMENT_SOURCE, _LABEL_SOURCE = pin_members(
    "outcomes.py's sources", ("settlement", "label"), OUTCOME_SOURCES
)

#: The two kinds a label stream may declare itself (§5.13.2): a terminal
#: resolution, or a non-terminal mark. A subset of the vocabulary, pinned
#: to it at import rather than restated.
_LABEL_KIND_CHOICES = (_SETTLED, _MARKED)


# ---------------------------------------------------------------------------
# The forward as-of rule — one owner, because both sources need exactly it
# ---------------------------------------------------------------------------


def forward_asof(legs, events, key, at):
    """Match each leg to the first event strictly later than its decision.

    The ONE forward as-of rule. For each leg, the FIRST event whose
    ``key(event)`` equals the leg's and whose ``at(event)`` is **strictly
    greater** than that leg's ``decided_at_ms``; events are consumed in
    ``at`` order, a leg matches at most one event, and a leg with no such
    event is DROPPED rather than matched to something earlier. The strict
    ``>`` is the whole anti-leak property: an event stamped at the
    decision's own instant is one the decision could itself have seen.

    Parameters
    ----------
    legs : iterable of DecidedLeg
        The legs to resolve. They are walked in ``(decided_at_ms, leg_id)``
        order, so the earlier decision claims the earlier event.
    events : iterable
        The candidate events, of whatever shape the caller's ``key`` and
        ``at`` understand.
    key : callable
        ``key(item)`` for a leg AND for an event; two items join when their
        keys are equal. One callable, because the join is symmetric.
    at : callable
        ``at(event)`` — the event's own epoch-ms instant.

    Returns
    -------
    tuple
        ``(leg, event)`` pairs in ``(decided_at_ms, leg_id)`` order; a leg
        with no later event contributes nothing.

    Raises
    ------
    ProductionError
        If an event's instant is not an int.
    """
    ordered = sorted(_indexed_events(events, at), key=lambda pair: (pair[0], pair[1]))
    taken, matched = set(), []
    for leg in sorted(legs, key=lambda item: (item.decided_at_ms, item.leg_id)):
        wanted = key(leg)
        for instant, position, event in ordered:
            if position in taken or instant <= leg.decided_at_ms or key(event) != wanted:
                continue
            taken.add(position)
            matched.append((leg, event))
            break
    return tuple(matched)


def _indexed_events(events, at):
    """Return ``(instant, position, event)`` for each event, refusing a bad instant."""
    indexed, problems = [], []
    for position, event in enumerate(events):
        instant = at(event)
        if not isinstance(instant, int) or isinstance(instant, bool):
            problems.append(f"event[{position}]: instant must be an epoch-ms int, got {instant!r}")
            continue
        indexed.append((instant, position, event))
    if problems:
        raise ProductionError(problems)
    return indexed


def outcome_record_id(release_hash, outcome):
    """Return the §6 record id one outcome is appended under.

    The ONE recipe: ``record`` mints it and the chain walk re-derives it,
    because §6's ``outcome`` body carries no id of its own. The release
    hash is a term, so two releases never share an outcome id — and a
    ``supersedes`` written under one release does not resolve under
    another, which is a refusal rather than a silent mismatch.

    Parameters
    ----------
    release_hash : str
        The release the record is bound to.
    outcome : Outcome
        The record whose id is wanted.

    Returns
    -------
    str
        ``outcome:<sha256>`` over ``("outcome-v1", release_hash, leg_id,
        source, effective_at_ms, known_at_ms)``.

    Raises
    ------
    ProductionError
        If ``outcome`` is not an :class:`~dskit.production.records.Outcome`.
    """
    if not isinstance(outcome, Outcome):
        raise ProductionError([f"outcome_record_id takes an Outcome, got {outcome!r}"])
    return f"{_OUTCOME}:" + canonical_hash(
        (
            OUTCOME_ID_TAG,
            release_hash,
            outcome.leg_id,
            outcome.source,
            outcome.effective_at_ms,
            outcome.known_at_ms,
        )
    )


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


class OutcomeSource(ABC):
    """One place outcomes are found, and nothing else (§5.13.2).

    A source reads its own world and answers what it found. It reads NO
    ledger and stamps no ``known_at_ms`` of its own choosing — the join
    supplies the instant and re-stamps every answer with it — so a source
    cannot back-date what it found.

    Parameters
    ----------
    params : dict, optional
        The site's ``params``: the subclass's ``_PARAMS`` plus ``notes``.
        Any other key refuses.

    Raises
    ------
    ProductionError
        On an unknown or malformed param, every problem accumulated.

    Examples
    --------
    A source is selected by a document and built by ``compose.py``::

        source = OUTCOME_SOURCE_KINDS.resolve("settlement")(
            {"lookback_ms": 604_800_000}, executor=executor
        )
        found = source.poll((leg,), 1_767_268_800_000, {})
        # -> (Outcome(leg_id='leg-1', outcome_kind='settled', ...),)
    """

    #: The knobs this source admits; every other key refuses (default-deny).
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

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending key. Nothing
            here raises.
        """
        problems = []
        reject_unknown_params(problems, params, tuple(cls._PARAMS) + _NOTES)
        cls._check(problems, params)
        return problems

    @classmethod
    def _check(cls, problems, params):
        """Append this source's own problems with ``params``; the base has none."""

    def _configure(self, params):
        """Read validated params; the base has none to read."""

    @abstractmethod
    def poll(self, legs, at_ms, standing):
        """Return the outcomes this source finds for ``legs`` at the cut.

        Parameters
        ----------
        legs : tuple of DecidedLeg
            The legs the join supplies.
        at_ms : int
            The cut. Nothing later than this instant is knowable, and every
            answer carries it as ``known_at_ms``.
        standing : Mapping
            ``leg_id`` -> the unsuperseded head :class:`Outcome` at the cut,
            for the legs that have one. §5.13.2 describes ``poll`` as taking
            only the legs and the cut, but a source that cannot see what
            already stands cannot tell a ``corrected`` outcome from a first
            arrival — and §5.13.2 requires ``SettlementOutcomes`` to produce
            ``corrected``. The mapping is the JOIN's own knowledge, so the
            source still reads no ledger.

        Returns
        -------
        tuple of Outcome
            One per resolved leg, in leg order.
        """


#: The §4.3 registry behind ``document.outcomes.sources[*].uses``. Its keys
#: are a subset of ``vocab.OUTCOME_SOURCES``: ``operator`` names an outcome
#: a human recorded, and a hand-recorded outcome polls nothing.
OUTCOME_SOURCE_KINDS = Registry("outcome_source", OutcomeSource)


# ---------------------------------------------------------------------------
# The venue's own settlements
# ---------------------------------------------------------------------------


def _is_voided(settled, requested):
    """Say whether a settlement of nothing resolves the leg to nothing."""
    return settled == _ZERO


def _is_partial(settled, requested):
    """Say whether the settled quantity falls below the leg's own."""
    return requested is not None and settled < requested


def _is_settled(settled, requested):
    """Accept everything else as a full resolution."""
    return True


#: How a settlement's quantity names the outcome kind, asked in order. A
#: table rather than a chain, so a new rule is a row and a settlement that
#: matches none is impossible by construction.
_SETTLEMENT_RULES = (
    (_is_voided, _VOIDED),
    (_is_partial, _PARTIAL),
    (_is_settled, _SETTLED),
)


class SettlementOutcomes(OutcomeSource):
    """The venue's own settlements, joined forward onto the legs (§5.13.2).

    ``value`` is the PER-UNIT resolution — ``payout / qty`` — so legs of
    different size are comparable; the payout itself would make a ten-lot
    look ten times better than a one-lot at the same price. ``weight`` is
    the settled quantity. A settled quantity below the leg's is
    ``partial``; a settlement of zero quantity is ``voided``; and a
    settlement that disagrees with a terminal outcome already standing is
    ``corrected``, which the join links to what it replaces.

    Parameters
    ----------
    params : dict, optional
        ``lookback_ms`` (int >= 0, default
        :data:`DEFAULT_OUTCOME_LOOKBACK_MS`) — how far back
        ``executor.settlements`` is asked.
    executor : Executor, keyword-only
        Read through ``settlements(since_ms)`` and nothing else.

    Raises
    ------
    ProductionError
        On an unknown param, a bad lookback, or an executor that answers
        something other than ``Settlement`` objects.

    Examples
    --------
    ::

        source = SettlementOutcomes({"lookback_ms": 86_400_000}, executor=executor)
        found = source.poll((leg,), 1_767_268_800_000, {})
        found[0].outcome_kind   # 'settled'
    """

    _PARAMS = ("lookback_ms",)

    def __init__(self, params=None, *, executor):
        self._executor = executor
        super().__init__(params)

    @classmethod
    def _check(cls, problems, params):
        """Refuse a lookback that is not a non-negative int: no window is no window."""
        if "lookback_ms" in params:
            check_int_param(problems, "lookback_ms", params["lookback_ms"], ge=0)

    def _configure(self, params):
        """Keep the one knob, defaulted from its single name."""
        self._lookback_ms = params.get("lookback_ms", DEFAULT_OUTCOME_LOOKBACK_MS)

    def poll(self, legs, at_ms, standing):
        """Answer one outcome per leg the settlements resolve (see :meth:`OutcomeSource.poll`)."""
        legs = tuple(legs)
        found = self._settlements(at_ms)
        return tuple(
            self._outcome(leg, item, at_ms, standing)
            for leg, item in forward_asof(legs, found, _instrument_of, _settled_at)
        )

    def _settlements(self, at_ms):
        """Every settlement in the window, refusing an executor that answers otherwise."""
        answered = tuple(self._executor.settlements(at_ms - self._lookback_ms))
        problems = [
            f"executor.settlements[{position}] is {item!r}, not a Settlement"
            for position, item in enumerate(answered)
            if not isinstance(item, Settlement)
        ]
        if problems:
            raise ProductionError(problems)
        return tuple(item for item in answered if item.settled_ms <= at_ms)

    def _outcome(self, leg, item, at_ms, standing):
        """Turn one matched settlement into the outcome it resolves the leg to."""
        kind = _first_rule(item.qty, leg.qty)
        value = (item.payout / item.qty) if item.qty != _ZERO else _ZERO
        head = standing.get(leg.leg_id)
        if head is not None and head.terminal and head.value != value:
            kind = _CORRECTED
        return Outcome(
            leg_id=leg.leg_id,
            outcome_kind=kind,
            effective_at_ms=item.settled_ms,
            known_at_ms=at_ms,
            value=value,
            weight=item.qty,
            terminal=True,
            source=_SETTLEMENT_SOURCE,
            supersedes=None,
        )


def _first_rule(settled, requested):
    """Name the outcome kind the first matching settlement rule gives."""
    for rule, kind in _SETTLEMENT_RULES:
        if rule(settled, requested):
            return kind
    raise ProductionError(  # pragma: no cover - the last rule always matches
        [f"no settlement rule covers a settled quantity of {settled}"]
    )


def _instrument_of(item):
    """Return the join key of a leg or a settlement: both name an instrument."""
    return item.instrument


def _settled_at(item):
    """Return a settlement's own instant."""
    return item.settled_ms


# ---------------------------------------------------------------------------
# A derived label stream on the onboarding root the run was trained from
# ---------------------------------------------------------------------------


class LabelOutcomes(OutcomeSource):
    """Outcomes read back from a derived label stream (§5.13.2).

    D4's rule holds here too: the labels are READ from the onboarding root
    the run was trained from, through the observations read seam, so there
    is no second vendor fetch and no path by which serving sees data
    training never did. A stream that carries a running mark rather than a
    resolution declares itself ``marked`` and its outcomes are not
    terminal.

    Parameters
    ----------
    params : dict, optional
        ``source``, ``stream``, ``key_fields``, ``time_field`` and
        ``value_field`` (required); ``weight_field`` (optional; the weight
        is 1 without it), ``outcome_kind`` (``settled`` or ``marked``,
        default ``settled``) and ``lookback_ms`` (int >= 0, default
        :data:`DEFAULT_OUTCOME_LOOKBACK_MS`). ``key_fields`` is the
        stream's DEDUP key, as ``scan_stream`` means it; the join key is
        that key with ``time_field`` projected out — the same projection
        ``ServingContract`` makes to get an entity — and it must leave
        exactly one field, because a leg contributes only its instrument.
    root : OnboardingRoot, keyword-only
        The root holding ``observations/``.
    registry : Registry, keyword-only
        That root's P2 registry. The named source must have exactly one
        ACTIVE ``source_config`` there, so a typo is a refusal rather than
        an empty answer that reads as "nothing settled yet".

    Raises
    ------
    ProductionError
        On an unknown or malformed param; and, at poll time, on a source
        the registry does not hold active.

    Examples
    --------
    A non-terminal mark stream keyed by instrument::

        source = LabelOutcomes(
            {"source": "labels", "stream": "resolutions", "key_fields": ["instrument"],
             "time_field": "asof", "value_field": "value", "outcome_kind": "marked"},
            root=root, registry=root.registry(),
        )
        source.poll((leg,), 1_767_268_800_000, {})[0].terminal   # False
    """

    _PARAMS = (
        "source",
        "stream",
        "key_fields",
        "time_field",
        "value_field",
        "weight_field",
        "outcome_kind",
        "lookback_ms",
    )

    #: The field ``scan_stream`` derives each row's epoch-ms instant into.
    _TS_OUT = "asof_ms"

    def __init__(self, params=None, *, root, registry):
        self._root = root
        self._registry = registry
        super().__init__(params)

    @classmethod
    def _check(cls, problems, params):
        """Every name the stream is read by must be declared and well shaped."""
        for name in ("source", "stream", "time_field", "value_field"):
            _check_str(problems, name, params.get(name))
        if "weight_field" in params:
            _check_str(problems, "weight_field", params["weight_field"])
        fields = params.get("key_fields")
        if not isinstance(fields, (list, tuple)) or not fields:
            problems.append(f"key_fields must be a non-empty list of field names, got {fields!r}")
        else:
            for position, field in enumerate(fields):
                _check_str(problems, f"key_fields[{position}]", field)
        entity = _entity_fields(params.get("key_fields"), params.get("time_field"))
        if fields and entity is not None and len(entity) != 1:
            problems.append(
                f"key_fields {list(fields)} less time_field {params.get('time_field')!r} leaves "
                f"{list(entity)}: the join key must be the one field a leg can answer with, "
                "its instrument"
            )
        declared = params.get("outcome_kind", _SETTLED)
        if declared not in _LABEL_KIND_CHOICES:
            problems.append(
                f"outcome_kind must be one of {list(_LABEL_KIND_CHOICES)}, got {declared!r} — a "
                "label stream declares a resolution or a running mark, and nothing else"
            )
        if "lookback_ms" in params:
            check_int_param(problems, "lookback_ms", params["lookback_ms"], ge=0)

    def _configure(self, params):
        """Read the declared field names once."""
        self._source = params["source"]
        self._stream = params["stream"]
        self._time_field = params["time_field"]
        self._key_fields = tuple(params["key_fields"])
        self._entity_field = _entity_fields(self._key_fields, self._time_field)[0]
        self._value_field = params["value_field"]
        self._weight_field = params.get("weight_field")
        self._outcome_kind = params.get("outcome_kind", _SETTLED)
        self._lookback_ms = params.get("lookback_ms", DEFAULT_OUTCOME_LOOKBACK_MS)

    def poll(self, legs, at_ms, standing):
        """Answer one outcome per leg the label stream resolves (see :meth:`OutcomeSource.poll`)."""
        rows = self._rows(at_ms)
        return tuple(
            self._outcome(leg, row, at_ms)
            for leg, row in forward_asof(tuple(legs), rows, self._key, self._at)
        )

    def _rows(self, at_ms):
        """Read the stream once, bounded below by the lookback and above by the cut."""
        try:
            find_active_source(self._registry, self._source)
            rows = observations.scan_stream(
                self._root.root,
                self._source,
                self._stream,
                key_fields=list(self._key_fields),
                ts_field=self._time_field,
                ts_out=self._TS_OUT,
                since_ms=max(0, at_ms - self._lookback_ms),
            )
        except AssetError as exc:
            raise ProductionError(
                [f"label source {self._source!r}: {problem}" for problem in exc.errors]
            ) from exc
        return tuple(row for row in rows if self._at(row) <= at_ms)

    def _key(self, item):
        """Return the join key of a leg (its instrument) or a label row (its entity)."""
        if isinstance(item, DecidedLeg):
            return item.instrument
        return item.get(self._entity_field)

    def _at(self, row):
        """Return a label row's own instant, as ``scan_stream`` derived it."""
        return row.get(self._TS_OUT)

    def _outcome(self, leg, row, at_ms):
        """Turn one matched label row into the outcome the stream declares."""
        return Outcome(
            leg_id=leg.leg_id,
            outcome_kind=self._outcome_kind,
            effective_at_ms=row[self._TS_OUT],
            known_at_ms=at_ms,
            value=_decimal_of(row, self._value_field, self._value_field),
            weight=_ONE if self._weight_field is None else _decimal_of(
                row, self._weight_field, self._weight_field
            ),
            terminal=self._outcome_kind != _MARKED,
            source=_LABEL_SOURCE,
            supersedes=None,
        )


def _entity_fields(key_fields, time_field):
    """Project the time field out of a dedup key, leaving the entity it identifies."""
    if not isinstance(key_fields, (list, tuple)):
        return None
    return tuple(field for field in key_fields if field != time_field)


def _decimal_of(row, field, where):
    """Read one label field as an exact Decimal — the store's JSON is not money-typed."""
    value = row.get(field)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ProductionError([f"label row field {where!r} is {value!r}, not a number"])
    try:
        return Decimal(str(value))
    except ArithmeticError as exc:
        raise ProductionError([f"label row field {where!r} is {value!r}, not a number"]) from exc


OUTCOME_SOURCE_KINDS.register(_SETTLEMENT_SOURCE, SettlementOutcomes)
OUTCOME_SOURCE_KINDS.register(_LABEL_SOURCE, LabelOutcomes)


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------


class OutcomeJoin:
    """The composite that asks, stamps, records and re-answers (§5.13.2, D21).

    :meth:`collect` reads and writes nothing; :meth:`record` is the only
    writer of the ``outcome`` record; :meth:`current_outcome` and
    :meth:`as_of` answer at any cut, which is the vintage reproducibility
    D21 exists for.

    Parameters
    ----------
    document : ServeDocument
        Read for ``outcomes.sources``: the names it declares must be
        exactly the names ``compose.py`` built, so a composition that
        served an undeclared source refuses here rather than silently.
    release : ReleaseManifest
        Binds ``release_hash``, which is a term of every outcome id.
    ledger : Ledger
        Appended to by :meth:`record`, and read through the one reader of
        outcome history.
    state : SeriesState
        The fold. It carries no outcome projection — ``apply`` folds an
        ``outcome`` to nothing — so what stands is read from the ledger;
        the fold is asked only whether it has seen every record the
        history was read from, so an outcome is never recorded against a
        chain that has already moved.
    clock : Clock
        Answers ``current_outcome``/``as_of`` when no cut is given. It is
        never read to stamp a record: that instant is the caller's.
    sources : Mapping
        Ordered ``name -> OutcomeSource``, as built from
        ``document.outcomes.sources``.

    Raises
    ------
    ProductionError
        When the source map is not what the document declares, or an entry
        is not an :class:`OutcomeSource`.

    Examples
    --------
    ::

        join = OutcomeJoin(document, release, ledger=ledger, state=state,
                           clock=clock, sources={"settle": source})
        found = join.collect(clock.now_ms())
        join.record(found)
        join.current_outcome("leg-1")
        # -> the head Outcome of that leg's chain, or None
    """

    def __init__(self, document, release, *, ledger, state, clock, sources):
        sources = dict(sources or {})
        declared = _declared_sources(document)
        problems = []
        if set(declared) != set(sources):
            problems.append(
                f"outcomes.sources declares {sorted(declared)} and compose built "
                f"{sorted(sources)}: the join serves exactly what the document declares"
            )
        problems.extend(
            f"outcomes.sources.{name} is {source!r}, not an OutcomeSource"
            for name, source in sources.items()
            if not isinstance(source, OutcomeSource)
        )
        if problems:
            raise ProductionError(problems)
        self._document = document
        self._release_hash = release.release_hash
        self._ledger = ledger
        self._history = LedgerHistory(ledger)
        self._state = state
        self._clock = clock
        self._sources = MappingProxyType(sources)

    # -- reading ------------------------------------------------------------

    def collect(self, at_ms):
        """Ask every source what it has found for the open legs, as of ``at_ms``.

        Every answer is re-stamped with ``at_ms`` — the run's ONE instant,
        read from the clock once by the CALLER and never per record — so a
        crash-replayed collect at the same instant produces byte-identical
        payloads. Anything already standing unsuperseded is dropped, and a
        correction is linked to the record it replaces.

        Parameters
        ----------
        at_ms : int
            The cut, epoch ms.

        Returns
        -------
        tuple of Outcome
            Ordered by ``(known_at_ms, leg_id, source)``. It reads; it
            writes nothing.

        Notes
        -----
        §5.13.2 describes the legs handed to a source as "the not-yet-
        terminal" ones, but a source that never sees a resolved leg can
        never correct one — and the same section requires
        ``SettlementOutcomes`` to produce ``corrected``. Every leg decided
        at or before the cut is therefore supplied, and it is the DROP
        below that keeps a resolved leg free: an answer that says exactly
        what already stands is dropped, and only one that disagrees
        survives, as a correction.

        Raises
        ------
        ProductionError
            On a cut that is not a non-negative int, or a source that
            answered something other than ``Outcome`` objects.
        """
        _check_instant(at_ms, "collect.at_ms")
        heads = self._standing(at_ms)
        # A source is handed the head OUTCOME only. The record id it was
        # appended under is the join's business — a source reads no ledger,
        # so it has no id to compare and nothing to do with one.
        standing = MappingProxyType({leg_id: head for leg_id, (_id, head) in heads.items()})
        legs = tuple(leg for leg in self._history.legs(0) if leg.decided_at_ms <= at_ms)
        found = []
        for name, source in self._sources.items():
            for answer in source.poll(legs, at_ms, standing):
                found.append(self._stamped(name, answer, at_ms, heads))
        kept = [
            outcome
            for outcome in found
            if not _same_finding(standing.get(outcome.leg_id), outcome)
        ]
        return tuple(sorted(kept, key=lambda item: (item.known_at_ms, item.leg_id, item.source)))

    def current_outcome(self, leg_id, at_ms=None):
        """Return one leg's head outcome at the cut, or None.

        Parameters
        ----------
        leg_id : str
            The leg to answer for.
        at_ms : int, optional
            The ``known_at_ms <= at_ms`` cut; the clock's now when absent.

        Returns
        -------
        Outcome or None
            The head of the supersede chain as it stood at the cut.

        Raises
        ------
        ProductionError
            On a cut that is not a non-negative int.
        """
        head = self._standing(self._cut(at_ms)).get(leg_id)
        return None if head is None else head[1]

    def as_of(self, at_ms):
        """Return every leg's head outcome at the cut.

        Parameters
        ----------
        at_ms : int
            The ``known_at_ms <= at_ms`` cut.

        Returns
        -------
        Mapping
            A read-only ``leg_id -> Outcome``; re-asking at an earlier cut
            reproduces exactly what was knowable then.

        Raises
        ------
        ProductionError
            On a cut that is not a non-negative int.
        """
        return MappingProxyType(
            {leg_id: head for leg_id, (_id, head) in self._standing(self._cut(at_ms)).items()}
        )

    # -- writing ------------------------------------------------------------

    def record(self, outcomes):
        """Append each outcome and barrier once after the batch.

        Parameters
        ----------
        outcomes : iterable of Outcome
            As :meth:`collect` answered them.

        Returns
        -------
        tuple of str
            The record ids, in the order they were appended.

        Raises
        ------
        ProductionError
            On anything that is not an ``Outcome``; on a ``supersedes``
            naming anything that is not an ``outcome`` record of this
            series; or on one that has already been superseded — a chain
            link may be replaced once.
        """
        outcomes = tuple(outcomes)
        self._check_folded()
        problems = [
            f"record[{position}] is {item!r}, not an Outcome"
            for position, item in enumerate(outcomes)
            if not isinstance(item, Outcome)
        ]
        if problems:
            raise ProductionError(problems)
        self._check_supersedes(problems, outcomes)
        if problems:
            raise ProductionError(problems)
        ids = tuple(outcome_record_id(self._release_hash, outcome) for outcome in outcomes)
        for record_id, outcome in zip(ids, outcomes):
            self._ledger.append({"kind": _OUTCOME, "id": record_id, "body": outcome.to_obj()})
        self._ledger.barrier()
        _LOG.info("recorded %d outcome(s)", len(ids))
        return ids

    def _check_folded(self):
        """Refuse to extend a chain the fold has not seen the whole of."""
        folded, written = self._state.head()[0], self._ledger.head()[0]
        if folded != written:
            raise ProductionError(
                [
                    f"the fold has folded {folded} record(s) and the ledger holds {written}: "
                    "an outcome is never recorded against a chain that has already moved"
                ]
            )

    # -- the chain ----------------------------------------------------------

    def _check_supersedes(self, problems, outcomes):
        """Refuse a chain link that names nothing, or one already replaced."""
        known = self._by_id()
        replaced = {
            outcome.supersedes for outcome in known.values() if outcome.supersedes is not None
        }
        for position, outcome in enumerate(outcomes):
            named = outcome.supersedes
            if named is None:
                continue
            if named not in known:
                problems.append(
                    f"record[{position}].supersedes names {named!r}, which is not an outcome "
                    "record of this series"
                )
            elif named in replaced:
                problems.append(
                    f"record[{position}].supersedes names {named!r}, which has already been "
                    "superseded — a chain link may be replaced once"
                )
            replaced.add(named)

    def _by_id(self):
        """Every recorded outcome of this series, under the id the LEDGER stored."""
        return dict(self._history.outcomes(0))

    def _standing(self, at_ms):
        """Return ``leg_id -> (record_id, Outcome)`` for the unsuperseded head at the cut."""
        known = {
            record_id: outcome
            for record_id, outcome in self._by_id().items()
            if outcome.known_at_ms <= at_ms
        }
        superseded = {
            outcome.supersedes for outcome in known.values() if outcome.supersedes is not None
        }
        heads = {}
        for record_id, outcome in sorted(
            known.items(), key=lambda pair: (pair[1].known_at_ms, pair[1].effective_at_ms, pair[0])
        ):
            if record_id not in superseded:
                heads[outcome.leg_id] = (record_id, outcome)
        return heads

    def _stamped(self, name, answer, at_ms, heads):
        """Re-stamp one answer with the run's instant, linking a correction to its head."""
        if not isinstance(answer, Outcome):
            raise ProductionError(
                [f"outcomes.sources.{name} answered {answer!r}, not an Outcome"]
            )
        head = heads.get(answer.leg_id)
        supersedes = (
            head[0]
            if head is not None and answer.outcome_kind == _CORRECTED
            else answer.supersedes
        )
        return Outcome(
            leg_id=answer.leg_id,
            outcome_kind=answer.outcome_kind,
            effective_at_ms=answer.effective_at_ms,
            known_at_ms=at_ms,
            value=answer.value,
            weight=answer.weight,
            terminal=answer.terminal,
            source=answer.source,
            supersedes=supersedes,
        )

    def _cut(self, at_ms):
        """Return the cut a reader asked for, or the clock's now when it asked for none."""
        cut = self._clock.now_ms() if at_ms is None else at_ms
        _check_instant(cut, "at_ms")
        return cut


def _declared_sources(document):
    """Return the source names ``document.outcomes`` declares; none when it is absent."""
    section = document.outcomes
    if section is None or section.sources is None:
        return ()
    return tuple(section.sources)


def _same_finding(head, answer):
    """Whether an answer says exactly what already stands, ignoring when it was learned."""
    if head is None:
        return False
    kept = ("outcome_kind", "effective_at_ms", "value", "weight", "terminal", "source")
    return all(getattr(head, name) == getattr(answer, name) for name in kept)


def _check_instant(value, where):
    """Refuse a cut that is not a non-negative epoch-ms int."""
    problems = []
    check_int_param(problems, where, value, ge=0)
    if problems:
        raise ProductionError(problems)
