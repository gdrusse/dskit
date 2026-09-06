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

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields

from dskit.production.base import ProductionError

__all__ = [
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
