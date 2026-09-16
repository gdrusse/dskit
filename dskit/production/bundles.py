"""The seven collaborator bundles the loop, the tick and the leg are built from (§5.13, §5.16).

``ServeLoop``, ``Tick`` and ``LegPipeline`` take two values plus seven
frozen bundles rather than thirty positional arguments. The bundles are
their own module because ``LegPipeline`` takes six of them while
``compose.py`` builds all seven — putting them in either module would make
the §10 build order cyclic.

That cycle is why a bundle validates PRESENCE ONLY. Checking that
``Safety.breaker`` is a ``Breaker`` would import ``breaker.py``, and doing
that for every member would import most of the package back into the
module that exists to break the cycle. So a member is refused only when it
is ``None`` — a falsy collaborator such as an empty guard chain is present
— every absent member is reported in one raise, and the only production
module imported here is ``base``. Type conformance is proved where the
collaborators are built (``compose.py``) and where they are used.

The member ORDER is the constructor contract: ``compose.bundles_for``
returns the seven positionally and ``LegPipeline`` takes six of them, so
a member that moved would silently swap two collaborators. The order is
§5.16's table, restated independently by ``tests/production/test_bundles.py``.

:class:`Invocation` is the one value object here — the frozen ``{armed,
env_release_hash, once, max_ticks}`` that ``__main__`` builds from
``--armed``, ``DSKIT_PRODUCTION_ARM``, ``--once`` and ``--max-ticks`` and
that ``Safety`` carries, so ``Arming.check_conjunction`` (§5.6) can see
all three of its inputs. Its knobs are stdlib-typed, so it does check
them.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from types import MappingProxyType

from dskit.production.base import (
    ProductionError,
    canonical_bytes as _canonical_bytes,
    canonical_hash as _canonical_hash,
    check_digest,
)

__all__ = [
    "CapturedReplayTape",
    "Data",
    "Decision",
    "Execution",
    "Invocation",
    "Observability",
    "Recording",
    "ReplayTape",
    "Safety",
    "Schedule",
]

CAPTURED_REPLAY_TAPE_SCHEMA = "dskit.captured-replay-tape/v1"
CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA = "dskit.event-envelope/v2"

_PLACEHOLDER = "0" * 64
_FIELDS = (
    "schema_version",
    "event_envelope_schema",
    "data_capture_root",
    "data_captured_receipt",
    "source_rank_policy_sha256",
    "envelope_count",
    "ordered_envelope_digests",
    "ordered_envelopes_sha256",
    "tape_digest",
)
_DIGEST_FIELDS = (
    "data_capture_root",
    "data_captured_receipt",
    "source_rank_policy_sha256",
)


def _is_digest(value):
    """Return True only for a non-placeholder 64-hex digest (via base.check_digest)."""
    if not isinstance(value, str) or value == _PLACEHOLDER or value == "self":
        return False
    problems = []
    check_digest(problems, "digest", value)
    return not problems


def _check_tape(value):
    """Accumulate every refusal for a ``CapturedReplayTape.v1`` object."""
    problems = []
    unknown = set(value) - set(_FIELDS)
    missing = set(_FIELDS) - set(value)
    for name in sorted(unknown):
        problems.append(f"unknown captured-replay-tape field {name!r}")
    for name in sorted(missing):
        problems.append(f"missing captured-replay-tape field {name!r}")
    if value.get("schema_version") != CAPTURED_REPLAY_TAPE_SCHEMA:
        problems.append(
            f"schema_version must be {CAPTURED_REPLAY_TAPE_SCHEMA!r}"
        )
    if value.get("event_envelope_schema") != CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA:
        problems.append(
            f"event_envelope_schema must be {CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA!r}"
        )
    for name in _DIGEST_FIELDS:
        if not _is_digest(value.get(name)):
            problems.append(f"{name} must be a 64-hex sha256 digest, not a placeholder")
    count = value.get("envelope_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        problems.append("envelope_count must be a non-negative int")
    envelopes = value.get("ordered_envelope_digests")
    if not isinstance(envelopes, list):
        problems.append("ordered_envelope_digests must be a list")
    else:
        for index, digest in enumerate(envelopes):
            if not _is_digest(digest):
                problems.append(
                    f"ordered_envelope_digests[{index}] must be a 64-hex sha256 digest"
                )
        if isinstance(count, int) and not isinstance(count, bool) and count != len(envelopes):
            problems.append("envelope_count must equal len(ordered_envelope_digests)")
        if value.get("ordered_envelopes_sha256") != _canonical_hash(envelopes):
            problems.append("ordered_envelopes_sha256 does not match the digest list")
    try:
        source = {key: item for key, item in value.items() if key != "tape_digest"}
        if value.get("tape_digest") != _canonical_hash(source):
            problems.append("tape_digest does not match the recomputed digest")
    except ProductionError:
        # A non-serializable member is already reported above; the digest
        # cannot be recomputed over it, so there is nothing more to say here.
        pass
    return problems


class CapturedReplayTape:
    """The default-deny ``dskit.captured-replay-tape/v1`` inner-manifest codec.

    The bytes a ``ReplayTapeManifestProducer`` publishes are one exact
    nine-field object. This value pins it: the two derived digests
    (``ordered_envelopes_sha256`` and ``tape_digest``) are recomputed from
    canonical bytes, so the replay consumer later trusts only bytes that
    verify — never a caller-supplied digest string. It has no public
    constructor; ``parse`` and the private factory are the only mints.

    Examples
    --------
    A value is parsed, never constructed::

        tape = CapturedReplayTape.parse(canonical_bytes)
        tape.tape_digest  # 64 lowercase hex characters
    """

    __slots__ = ("_value", "__weakref__")

    def __new__(cls, *args, **kwargs):
        """Refuse direct construction: parse the bytes or use the factory."""
        raise TypeError("CapturedReplayTape is parsed, never constructed")

    def __setattr__(self, name, value):
        """Refuse mutation: a captured tape is immutable."""
        raise AttributeError("CapturedReplayTape is frozen")

    def __copy__(self):
        """Refuse a shallow copy of the opaque value."""
        raise TypeError("CapturedReplayTape is opaque")

    def __deepcopy__(self, memo):
        """Refuse a deep copy of the opaque value."""
        raise TypeError("CapturedReplayTape is opaque")

    def __getstate__(self):
        """Refuse pickle state for the opaque value."""
        raise TypeError("CapturedReplayTape is opaque")

    def __reduce__(self):
        """Refuse pickle reduction for the opaque value."""
        raise TypeError("CapturedReplayTape is opaque")

    def __reduce_ex__(self, protocol):
        """Refuse protocol pickle reduction for the opaque value."""
        raise TypeError("CapturedReplayTape is opaque")

    @classmethod
    def _from_value(cls, value):
        """Build an immutable instance from an already-validated object."""
        frozen = MappingProxyType(
            dict(value, ordered_envelope_digests=tuple(value["ordered_envelope_digests"]))
        )
        self = object.__new__(cls)
        object.__setattr__(self, "_value", frozen)
        return self

    @classmethod
    def parse(cls, raw):
        """Parse canonical ``CapturedReplayTape.v1`` bytes, refusing everything else.

        Parameters
        ----------
        raw : bytes
            The exact canonical JSON bytes of the nine-field object.

        Returns
        -------
        CapturedReplayTape
            The immutable, verified value.

        Raises
        ------
        ProductionError
            Naming every field that is unknown, missing, mistyped, a
            placeholder digest, or a mismatched recomputed digest, and any
            bytes that are not the canonical form.
        """
        if not isinstance(raw, bytes):
            raise ProductionError(["CapturedReplayTape.parse takes canonical bytes"])
        try:
            value = json.loads(raw.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            raise ProductionError(["CapturedReplayTape bytes are not canonical JSON"])
        if not isinstance(value, dict):
            raise ProductionError(["CapturedReplayTape must decode to an object"])
        problems = _check_tape(value)
        if not problems and _canonical_bytes(value) != raw:
            problems = ["CapturedReplayTape bytes are not canonical"]
        if problems:
            raise ProductionError(problems)
        return cls._from_value(value)

    @classmethod
    def _build(cls, data_capture_root, data_captured_receipt,
               source_rank_policy_sha256, ordered_envelope_digests):
        """Derive the canonical value from its five supplied inputs."""
        if not isinstance(ordered_envelope_digests, (list, tuple)):
            raise ProductionError(["ordered_envelope_digests must be a list"])
        envelopes = list(ordered_envelope_digests)
        value = {
            "schema_version": CAPTURED_REPLAY_TAPE_SCHEMA,
            "event_envelope_schema": CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA,
            "data_capture_root": data_capture_root,
            "data_captured_receipt": data_captured_receipt,
            "source_rank_policy_sha256": source_rank_policy_sha256,
            "envelope_count": len(envelopes),
            "ordered_envelope_digests": envelopes,
        }
        value["ordered_envelopes_sha256"] = _canonical_hash(envelopes)
        source = {key: item for key, item in value.items() if key != "tape_digest"}
        value["tape_digest"] = _canonical_hash(source)
        problems = _check_tape(value)
        if problems:
            raise ProductionError(problems)
        return cls._from_value(value)

    @property
    def schema_version(self):
        """The fixed ``dskit.captured-replay-tape/v1`` literal."""
        return self._value["schema_version"]

    @property
    def event_envelope_schema(self):
        """The fixed ``dskit.event-envelope/v2`` literal."""
        return self._value["event_envelope_schema"]

    @property
    def data_capture_root(self):
        """The parent data capture root digest."""
        return self._value["data_capture_root"]

    @property
    def data_captured_receipt(self):
        """The manifest producer's parent CAPTURED receipt digest."""
        return self._value["data_captured_receipt"]

    @property
    def source_rank_policy_sha256(self):
        """The pre-document source-rank policy digest."""
        return self._value["source_rank_policy_sha256"]

    @property
    def envelope_count(self):
        """The number of ordered envelopes."""
        return self._value["envelope_count"]

    @property
    def ordered_envelope_digests(self):
        """The ordered envelope digests, as an immutable tuple."""
        return tuple(self._value["ordered_envelope_digests"])

    @property
    def ordered_envelopes_sha256(self):
        """The recomputed digest of the ordered envelope digest array."""
        return self._value["ordered_envelopes_sha256"]

    @property
    def tape_digest(self):
        """The recomputed digest of the object with ``tape_digest`` omitted."""
        return self._value["tape_digest"]

    def to_obj(self):
        """Return a plain copy of the nine-field object."""
        value = dict(self._value)
        value["ordered_envelope_digests"] = list(value["ordered_envelope_digests"])
        return value

    def canonical_bytes(self):
        """Return the exact canonical JSON bytes of the value."""
        return _canonical_bytes(self.to_obj())


class ReplayTape(ABC):
    """What a replay hands the composition root, so it can select D20's objects.

    ``compose.bundles_for(..., tape=tape)`` builds the replay collaborators
    from these three answers; the tape supplies DATA and never an object,
    which is what keeps "the rungs differ only by which objects were
    injected" (§5.15) a fact about ``compose.py`` rather than about
    whoever produced the tape. The ABC lives here, beside the bundles, for
    the same reason they do: ``report.py`` builds tapes and ``compose.py``
    consumes them, and a declaration in either would make §10's build order
    cyclic.

    Examples
    --------
    A tape that replays one tick of one leg::

        class OneTick(ReplayTape):
            def start_ms(self):
                return 1_767_268_800_000

            def feed_results(self):
                return (result,)

            def id_allocations(self):
                return (("next_tick_id", (1_767_268_800_000,), "tick-1"),)

        OneTick().start_ms()
        # -> 1767268800000
    """

    @abstractmethod
    def start_ms(self):
        """Return the instant the replay clock starts at.

        Returns
        -------
        int
            Epoch milliseconds — the recording's own first instant, so the
            cadence grid the replay walks is the grid it walked.
        """

    @abstractmethod
    def feed_results(self):
        """Return the recorded pulls, in tick order.

        Returns
        -------
        tuple of FeedResult
            One per recorded tick.
        """

    @abstractmethod
    def id_allocations(self):
        """Return the recorded id allocations, in the order they were asked.

        Returns
        -------
        tuple of tuple
            ``(method, args, id)`` triples, as ``RecordedIdSource`` takes
            them — and it refuses any call that is not the recorded one,
            which is what makes a replay that decided differently a
            refusal rather than a quiet re-derivation.
        """


class _Bundle:
    """Presence-only validation every bundle shares: a ``None`` member is absent, nothing else is."""

    def __post_init__(self):
        """Refuse every absent member in one raise, naming each."""
        absent = [field.name for field in fields(self) if getattr(self, field.name) is None]
        if absent:
            raise ProductionError(
                [f"{type(self).__name__}.{name} is absent (None)" for name in absent]
            )


@dataclass(frozen=True)
class Schedule(_Bundle):
    """When the loop ticks: the time source and the calendar, cadence and overrun policies (§5.1).

    Parameters
    ----------
    clock : Clock
        The injected time source; nothing else reads the wall clock.
    calendar : Calendar
        Open/closed sessions and the windows guards and cadences anchor on.
    cadence : Cadence
        The tick grid — the next due instant after a given one.
    overrun : Overrun
        What happens to ticks that fell due while one was running.

    Examples
    --------
    Bind the four collaborators ``compose.bundles_for`` resolved::

        schedule = Schedule(clock=clock, calendar=calendar, cadence=cadence, overrun=overrun)
        schedule.clock is clock  # True
    """

    clock: object
    calendar: object
    cadence: object
    overrun: object


@dataclass(frozen=True)
class Data(_Bundle):
    """Where a tick's rows and proposals come from (§5.2, §5.3).

    Parameters
    ----------
    feed : Feed
        Acquires and reads the entry batch.
    decider : Decider
        Runs the decision nodes and owns the configured ``Proposer``, which
        is how ``Tick.candidates`` / ``quotes`` / ``propose`` reach it.

    Examples
    --------
    ::

        data = Data(feed=feed, decider=decider)
        data.decider is decider  # True
    """

    feed: object
    decider: object


@dataclass(frozen=True)
class Decision(_Bundle):
    """What judges a proposal and what watches the stream of decisions (§5.5, §5.10).

    Parameters
    ----------
    guards : GuardChain
        The ordinary guards, run at leg steps (1) and (2).
    monitors : Mapping
        The configured monitors, observed after each tick.

    Examples
    --------
    ::

        decision = Decision(guards=guards, monitors=monitors)
        decision.guards is guards  # True
    """

    guards: object
    monitors: object


@dataclass(frozen=True)
class Safety(_Bundle):
    """Everything that may say no (§5.6, §5.13, §5.13.1, §5.14).

    Parameters
    ----------
    breaker : Breaker
        The series breaker — ``active | reducing | halted``.
    arming : Arming
        The maker-checker arming fold and scope application (D11).
    authorities : AuthorityTable
        ``for_origin(origin, breaker)`` — the ``Authority`` that mints a permit.
    readiness : Readiness
        The GO / NO-GO checklist evaluator.
    invocation : Invocation
        The ``--armed`` / env-hash / ``--once`` / ``--max-ticks`` values.
    action_policy : ActionPolicy
        Who may act — D10's matrix.
    transition_policy : TransitionPolicy
        How the breaker may move — D10's transitions.
    submission_verifier : SubmissionVerifier
        The final verify-and-call gate before native I/O.

    Examples
    --------
    ::

        safety = Safety(
            breaker=breaker, arming=arming, authorities=authorities, readiness=readiness,
            invocation=Invocation(armed=False, env_release_hash=None, once=False, max_ticks=None),
            action_policy=action_policy, transition_policy=transition_policy,
            submission_verifier=submission_verifier,
        )
        safety.invocation.armed  # False
    """

    breaker: object
    arming: object
    authorities: object
    readiness: object
    invocation: object
    action_policy: object
    transition_policy: object
    submission_verifier: object


@dataclass(frozen=True)
class Execution(_Bundle):
    """The venue side: the executor, its accounting, the lease and the resilience policies (§5.7, §5.12).

    Parameters
    ----------
    executor : Executor
        Read, query and cancel — and, for a ``SubmittingExecutor``, submit.
    accounting : Accounting
        Snapshots, valuation and the ``risk_effect`` classification.
    lease : Lease
        Single-writer coordination over the venue/account scope.
    resilience : ResiliencePolicies
        The ``Retry`` / ``CircuitBreakers`` / ``RateLimiter`` / ``Transport`` set.

    Examples
    --------
    ::

        execution = Execution(
            executor=executor, accounting=accounting, lease=lease, resilience=resilience
        )
        execution.executor is executor  # True
    """

    executor: object
    accounting: object
    lease: object
    resilience: object


@dataclass(frozen=True)
class Recording(_Bundle):
    """The durable side: the ledger, its fold, the control inbox, reconciliation and ids (§5.8, §5.9, §5.13).

    Parameters
    ----------
    ledger : Ledger
        The append-only, barriered series ledger.
    state : SeriesState
        The sole fold of that ledger; ``snapshot()`` is every ``state_view``.
    inbox : ControlInbox
        The durable spool of queued control commands.
    reconciler : Reconciler
        Startup and periodic reconciliation against the venue.
    checkpoint : Checkpoint
        The projection written last after each tick.
    journal_hook : callable
        Writes D22's one journal row per completed process.
    id_source : IdSource
        Allocates tick, leg, plan and client ids before ``tick_start``.

    Examples
    --------
    ::

        recording = Recording(
            ledger=ledger, state=state, inbox=inbox, reconciler=reconciler,
            checkpoint=checkpoint, journal_hook=journal_hook, id_source=id_source,
        )
        recording.id_source is id_source  # True
    """

    ledger: object
    state: object
    inbox: object
    reconciler: object
    checkpoint: object
    journal_hook: object
    id_source: object


@dataclass(frozen=True)
class Observability(_Bundle):
    """What the process reports about itself (§5.11, §5.11.1).

    Parameters
    ----------
    metrics : Metrics
        Counters, gauges and histograms flushed per tick.
    alerts : AlertRouter
        Routes alerts to the configured sinks.
    health : Health
        The health state machine the action policy reads.
    heartbeat : HeartbeatEmitter
        The liveness signal.

    Examples
    --------
    ::

        observability = Observability(
            metrics=metrics, alerts=alerts, health=health, heartbeat=heartbeat
        )
        observability.health is health  # True
    """

    metrics: object
    alerts: object
    health: object
    heartbeat: object


@dataclass(frozen=True)
class Invocation:
    """How this process was invoked: the four knobs ``__main__`` reads (§5.6, §5.13).

    Parameters
    ----------
    armed : bool
        ``--armed`` was given.
    env_release_hash : str or None
        The value of ``DSKIT_PRODUCTION_ARM``, or ``None`` when unset.
    once : bool
        ``--once`` was given: run one tick.
    max_ticks : int or None
        ``--max-ticks N`` (at least 1), or ``None`` for an unbounded serve.

    Raises
    ------
    ProductionError
        Naming every knob that is not the type above — ``--max-ticks 0``
        must refuse rather than serve forever or stop at once depending on
        how the loop reads it.

    Examples
    --------
    An unarmed, unbounded serve — the normal shadow case — and an armed one::

        Invocation(armed=False, env_release_hash=None, once=False, max_ticks=None)
        armed = Invocation(armed=True, env_release_hash="a" * 64, once=True, max_ticks=1)
        armed.once  # True
    """

    armed: bool
    env_release_hash: str | None
    once: bool
    max_ticks: int | None

    def __post_init__(self):
        """Refuse every knob of the wrong type in one raise."""
        problems = []
        if not isinstance(self.armed, bool):
            problems.append(f"Invocation.armed must be a bool, got {self.armed!r}")
        if self.env_release_hash is not None and not isinstance(self.env_release_hash, str):
            problems.append(
                f"Invocation.env_release_hash must be a str or None, got {self.env_release_hash!r}"
            )
        if not isinstance(self.once, bool):
            problems.append(f"Invocation.once must be a bool, got {self.once!r}")
        if self.max_ticks is not None and (
            isinstance(self.max_ticks, bool)
            or not isinstance(self.max_ticks, int)
            or self.max_ticks < 1
        ):
            problems.append(
                f"Invocation.max_ticks must be an int of at least 1 or None, got {self.max_ticks!r}"
            )
        if problems:
            raise ProductionError(problems)
