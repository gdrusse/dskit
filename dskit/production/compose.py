"""The composition root: the one module that may read a rung (§5.13.1, D2).

Shadow, paper and live are not three code paths — they are three sets of
OBJECTS. D2 makes that structural rather than lexical: everything that
could differ by rung is an injected seam, so the loop, the tick and the
leg have nothing to ask. Somewhere, though, a rung has to select the
objects, and a decision with no named owner is a decision that relocates.
This module is that owner.

:data:`RUNG_TABLE` is the decision, written once as data: a closed row
per ``vocab.RUNGS`` member naming the executor, accounting, authority,
approval and coordination families that rung admits. A simulated rung
names its ONE core kind in each slot, so ``paper`` cannot select a
``LiveExecutor`` even by accident; a live rung names none, so every one
of the four must be a child class supplied by path, which is D9's
default-deny applied to composition rather than to the document alone.
:func:`bundles_for` reads that row, refuses every disagreement between it
and the document in one raise, and then builds the seven frozen bundles
of §5.16 in dependency order.

Two construction facts are worth knowing before reading it.

The first is a genuine cycle: a live executor is handed the submission
gate, and the gate is handed the executor. Neither can be built second.
:class:`_LateGate` is where the composition root resolves it — a thin
forwarder given to the executor and bound to the real
``SubmissionVerifier`` a few lines later, so there is exactly one gate and
one disable that every caller observes.

The second is that the feed cannot exist until the decider has prepared:
the feed binds ``decider.contract`` and ``decider.feed_spec``, and those
are products of the release verification, the served-document derivation
and the base pass. So composition, not the loop, runs
:meth:`Decider.prepare` — which is also why `Data` can promise both
members are usable.

:func:`handlers_for` is the other half of the composition root's job.
``CommandProcessor`` (§5.8) owns no verb logic: it appends the request,
dispatches to a handler, appends what the handler returned and barriers
once. The map from a ``CONTROL_PURPOSES`` member to the object that owns
that verb is therefore a composition fact, and it lives here beside the
objects it names.
"""

from __future__ import annotations

import hashlib
import inspect
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import MappingProxyType

from dskit.onboarding import OnboardingRoot
from dskit.production.accounting import ACCOUNTING_KINDS
from dskit.production.alerts import ALERT_SINK_KINDS, AlertRouter
from dskit.production.arming import (
    APPROVAL_KINDS,
    ArmApproval,
    ArmRequest,
    Arming,
    ReductionRights,
    approval_verifier,
    authority_record,
)
from dskit.production.base import (
    ProductionError,
    canonical_bytes,
    canonical_hash,
    pin_members,
    utc_iso,
)
from dskit.production.breaker import Breaker
from dskit.production.bundles import (
    Data,
    Decision,
    Execution,
    Observability,
    Recording,
    Safety,
    Schedule,
)
from dskit.production.cadence import CADENCE_KINDS, Overrun
from dskit.production.clock import CLOCK_KINDS
from dskit.production.control import ControlInbox
from dskit.production.coordination import LEASE_KINDS
from dskit.production.decider import PROPOSER_KINDS, Decider
from dskit.production.executor import EXECUTOR_KINDS, LiveExecutor
from dskit.production.feed import FEED_KINDS
from dskit.production.guards import GUARD_KINDS, GuardChain
from dskit.production.document import MIN_HEARTBEAT_EVERY_S
from dskit.production.health import (
    DEFAULT_IN_DEGRADED,
    HEARTBEAT_KINDS,
    PROBE_KINDS,
    Health,
    Heartbeat,
)
from dskit.production.ids import ReleaseIdSource
from dskit.production.ledger import Checkpoint, ledger_class
from dskit.production.leg import LiveAuthority, ReductionAuthority, SimulatedAuthority
from dskit.production.metrics import Metrics
from dskit.production.monitors import MONITOR_KINDS
from dskit.production.outcomes import OUTCOME_SOURCE_KINDS, OutcomeJoin
from dskit.production.policy import ActionPolicy, TransitionPolicy
from dskit.production.readiness import Readiness
from dskit.production.reconcile import LedgerHistory, Reconciler, enact
from dskit.production.records import ReductionPlan
from dskit.production.redact import get_logger, redact
from dskit.production.resilience import SIGNER_KINDS, resilience_from_document
from dskit.production.sessions import CALENDAR_KINDS
from dskit.production.state import SeriesState
from dskit.production.verifier import SubmissionVerifier
from dskit.production.vocab import (
    BREAKER_STATES,
    CASH_FLOW_KINDS,
    CONTROL_PURPOSES,
    LEG_ORIGINS,
    RUNGS,
    TRIP_REASONS,
)

__all__ = [
    "AuthorityTable",
    "DEFAULT_JITTER_SEED",
    "RUNG_TABLE",
    "bundles_for",
    "handlers_for",
    "outcome_join",
]

_LOG = get_logger("compose")

#: The seed the resilience jitter draws from. Jitter spreads retries; it
#: never reaches a decision, so a fixed seed costs nothing and buys a
#: replay that paces identically (D20).
DEFAULT_JITTER_SEED = 0

#: The breaker state a reduction authority is legal in (D12). Named once,
#: pinned against the vocabulary, and read by :class:`AuthorityTable`.
_REDUCING = pin_members("compose.py's reduction state", ("reducing",), BREAKER_STATES)[0]

#: The breaker's own vocabulary for an operator kill switch and for a
#: reconciliation that could not be explained (§5.6, §5.9).
_OPERATOR_REASON = pin_members("compose.py's operator trip", ("operator",), TRIP_REASONS)[0]

#: Who a control verb acts as, when the verb is an operator's.
_OPERATOR_ACTOR = "operator"

#: ``Breaker.trip``'s cause for the out-of-band kill switch (§5.6).
_HALT_CAUSE = "halt"

#: The recipe a reduction authority id derives from — one owner, so a
#: replayed approval mints the same id and the ledger dedups it.
_REDUCTION_AUTHORITY_TAG = "reduction-authority-v1"

_MS_PER_S = 1000


@dataclass(frozen=True)
class _NoHeartbeat:
    """The heartbeat section a document that declares none still gets.

    ``heartbeat`` is an OPTIONAL, non-identity section (D24), but
    ``Observability`` carries the object either way: the worker's
    dead-man check is what turns health ``unhealthy`` when
    ``dead_after_ms`` elapses without a completed tick (§5.11), and
    losing that because nobody configured an emitter would remove the
    one signal an external dead-man watches. So an undeclared section
    means "beat at the minimum cadence and emit nowhere", never "do not
    beat". ``every_s`` reuses ``document.MIN_HEARTBEAT_EVERY_S`` rather
    than naming a second bound.
    """

    every_s: int = MIN_HEARTBEAT_EVERY_S
    in_degraded: bool = DEFAULT_IN_DEGRADED
    emitters: object = None


# ---------------------------------------------------------------------------
# The closed rung -> collaborator table (§5.13.1)
# ---------------------------------------------------------------------------


class _Build(ABC):
    """How one family of rungs constructs the collaborators whose arity differs."""

    @abstractmethod
    def executor(self, cls, params, *, clock, scope, gate, lease, signer):
        """Return the executor this rung's family builds."""

    def signer(self, document, clock):
        """Return the signer this rung's family can use; none, for a simulated venue.

        Building one RESOLVES the venue key into the process (§5.12.1), so
        a rung with no venue must not build it: a shadow dry-run of a live
        document has no business holding a credential it can never use, and
        would otherwise refuse to start on a machine that rightly lacks it.
        """
        return None


class _SimulatedBuild(_Build):
    """Shadow and paper: the executor simulates a venue and needs no gate."""

    def executor(self, cls, params, *, clock, scope, gate, lease, signer):
        """Build a simulated executor over the document's declared scope.

        The signer is not offered — and, per :meth:`_Build.signer`, was
        never built: a simulated venue authenticates nothing.
        """
        return cls(params, clock=clock, scope=scope)


class _LiveBuild(_Build):
    """``live_limited`` and ``live``: the child venue wrapper holds the act gate."""

    def signer(self, document, clock):
        """Build ``execution.signer`` when the document declares one (§5.12.1)."""
        site = document.execution.signer
        if site is None:
            return None
        return SIGNER_KINDS.resolve(site.uses)(_selector(site), clock=clock)

    def executor(self, cls, params, *, clock, scope, gate, lease, signer):
        """Build the child ``LiveExecutor`` around the gate, the fenced lease and its signer.

        The signer is offered only to a venue whose own ``__init__``
        declares it (§5.12.1: core never calls ``sign``, because core ships
        no venue), which is the same rule ``_offered`` applies to probes,
        emitters and outcome sources.
        """
        return cls(
            params,
            clock=clock,
            verifier=gate,
            lease=lease,
            **_offered(cls, {"signer": signer}),
        )


@dataclass(frozen=True)
class _Rung:
    """One rung's collaborator set — the row §5.13.1 says the table holds.

    A ``None`` in ``executor``, ``accounting``, ``approval`` or
    ``coordination`` means "no core kind is admissible here": the document
    must name a child class by path. A string means the opposite — that
    exact core kind and no other, which is what makes "paper can never
    select a ``LiveExecutor``" a fact about the table rather than a
    promise about ``bundles_for``.

    Parameters
    ----------
    executor, accounting, approval, coordination : str or None
        The one admissible core kind, or ``None`` for "a child class only".
    authority : dict
        ``origin -> Authority`` subclass, one entry per ``LEG_ORIGINS``
        member, because the authority axis is ``(rung, origin)``.
    build : _Build
        How this rung's executor is constructed.

    Examples
    --------
    ::

        row = RUNG_TABLE["paper"]
        row.executor                  # 'paper'
        row.authority["reduction"]    # SimulatedAuthority
    """

    executor: str | None
    accounting: str | None
    authority: dict
    approval: str | None
    coordination: str | None
    build: _Build


def _simulated(executor):
    """One simulated rung's row: core kinds throughout, nothing live."""
    return _Rung(
        executor=executor,
        accounting="paper",
        authority=MappingProxyType(
            {"model": SimulatedAuthority, "reduction": SimulatedAuthority}
        ),
        approval="deny-all",
        coordination="process",
        build=_SimulatedBuild(),
    )


def _live():
    """One live rung's row: every child family open, both authorities live."""
    return _Rung(
        executor=None,
        accounting=None,
        authority=MappingProxyType({"model": LiveAuthority, "reduction": ReductionAuthority}),
        approval=None,
        coordination=None,
        build=_LiveBuild(),
    )


#: The closed rung -> {executor, accounting, authority, approval,
#: coordination} table D2 rests on. Reading it is the only rung read in
#: the package that selects an object; ``test_purity.py`` bans the
#: spelling everywhere else and ``test_compose.py`` bans this name
#: everywhere else.
RUNG_TABLE = pin_members(
    "compose.py's RUNG_TABLE",
    {
        "shadow": _simulated("shadow"),
        "paper": _simulated("paper"),
        "live_limited": _live(),
        "live": _live(),
    },
    RUNGS,
    exact=True,
)

for _row in RUNG_TABLE.values():
    pin_members("compose.py's authority origins", _row.authority, LEG_ORIGINS, exact=True)


# ---------------------------------------------------------------------------
# AuthorityTable — the (rung, origin) lookup the leg reads (§5.13.1)
# ---------------------------------------------------------------------------


class AuthorityTable:
    """The rung's authorities, answered by leg origin and breaker state.

    §5.13.1: "the authority axis is ``(rung, origin)``, not rung alone. A
    reduction is selected by where the intent came from, and it is legal
    only while the breaker is ``reducing``". `reducing` is a state the
    running loop ENTERS, so the table cannot decide it from the document
    — which is why :meth:`for_origin` takes the breaker at all. Whether a
    model leg MAY act in a given breaker state is ``ActionPolicy``'s
    answer, not this one's: giving that fact two owners is how they come
    to disagree.

    Parameters
    ----------
    rung : str
        A ``vocab.RUNGS`` member — the rung whose row built these.
    authorities : dict
        ``origin -> Authority`` instance, one per ``LEG_ORIGINS`` member.

    Raises
    ------
    ProductionError
        On a rung outside the vocabulary or a missing origin — an
        authority a leg could ask for and not get is a live leg that
        refuses at the moment it matters.

    Examples
    --------
    ::

        table = AuthorityTable("paper", {"model": model, "reduction": reduction})
        table.for_origin("model", "active") is model        # True
        table.for_origin("reduction", "active")             # -> ProductionError
    """

    def __init__(self, rung, authorities):
        problems = []
        if rung not in RUNG_TABLE:
            problems.append(f"rung must be one of {list(RUNGS)}, got {rung!r}")
        missing = sorted(set(LEG_ORIGINS) - set(authorities or {}))
        if missing:
            problems.append(f"AuthorityTable is missing an authority for origin(s) {missing}")
        unknown = sorted(set(authorities or {}) - set(LEG_ORIGINS))
        if unknown:
            problems.append(f"AuthorityTable carries unknown origin(s) {unknown}")
        if problems:
            raise ProductionError(problems)
        self._rung = rung
        self._authorities = MappingProxyType(dict(authorities))

    @property
    def rung(self):
        """Return the rung whose row selected these authorities (str)."""
        return self._rung

    def for_origin(self, origin, breaker):
        """Return the ``Authority`` that mints a permit for this leg.

        Parameters
        ----------
        origin : str
            A ``vocab.LEG_ORIGINS`` member — where the proposal came from.
        breaker : str
            A ``vocab.BREAKER_STATES`` member, read from the leg's FRESH
            fold so a trip raised inside this tick is visible.

        Returns
        -------
        Authority
            The rung's authority for that origin.

        Raises
        ------
        ProductionError
            On an origin or breaker state outside its vocabulary, or a
            reduction asked for outside ``reducing`` (D12).
        """
        problems = []
        if origin not in LEG_ORIGINS:
            problems.append(f"origin must be one of {list(LEG_ORIGINS)}, got {origin!r}")
        if breaker not in BREAKER_STATES:
            problems.append(f"breaker must be one of {list(BREAKER_STATES)}, got {breaker!r}")
        if not problems and origin == LEG_ORIGINS[1] and breaker != _REDUCING:
            problems.append(
                f"a {origin!r} authority is legal only while the breaker is {_REDUCING!r}, "
                f"and it is {breaker!r} (D12)"
            )
        if problems:
            raise ProductionError(problems)
        return self._authorities[origin]


# ---------------------------------------------------------------------------
# The two construction knots the composition root unties
# ---------------------------------------------------------------------------


class _LateGate:
    """The one construction cycle: a live executor needs the gate, the gate needs it.

    Both orders are impossible, so the executor is given this forwarder
    and it is bound to the real ``SubmissionVerifier`` once that exists.
    There is still exactly ONE gate — a second would mean a disable that
    only half the process observed.
    """

    def __init__(self):
        self._gate = None

    def bind(self, gate):
        """Bind the real gate; refuse a second binding."""
        if self._gate is not None:
            raise ProductionError(["the submission gate is already bound"])
        self._gate = gate

    @property
    def disabled(self):
        """Whether the bound gate has disabled further sends."""
        return self._bound().disabled

    def verify_and_call(self, intent, permit, state, native):
        """Forward the indivisible verify-and-call to the bound gate."""
        return self._bound().verify_and_call(intent, permit, state, native)

    def reset_after_reconcile(self):
        """Forward the re-enable to the bound gate, the disable's one owner."""
        return self._bound().reset_after_reconcile()

    def _bound(self):
        """Return the bound gate, or refuse and name the wiring defect."""
        if self._gate is None:
            raise ProductionError(["the submission gate was never bound by compose.bundles_for"])
        return self._gate


class _FeedAges:
    """The atomic snapshot a ``feed-age`` probe reads (§5.11, D23).

    A probe runs on its own worker and must never touch the feed, so it
    reads a published value instead: ``feed_ages()`` answers the last
    tick's ``tuple[records.FeedAge]``.

    **Phase 1 has no publisher.** ``Tick.coverage`` computes the ages and
    ``TickState.feed_ages`` carries them, but no §5.16 bundle member and no
    ``TickResult`` field exposes them to the loop, so nothing can hand them
    here. An unpublished holder answers ``()``, which the probe's own
    contract defines as "no ages yet is not a failure" — the probe passes
    rather than fabricating a verdict. Giving the ages a producer is a plan
    gap, not an implementation detail: it needs either an
    ``Observability`` member or a ``TickResult`` field.
    """

    def __init__(self):
        self._ages = ()

    def publish(self, ages):
        """Replace the published snapshot with one tick's ages."""
        self._ages = tuple(ages)

    def __call__(self):
        """Return the last published ages, or ``()`` before the first tick."""
        return self._ages


# ---------------------------------------------------------------------------
# bundles_for
# ---------------------------------------------------------------------------


def _selector(site):
    """Return a ``{uses, params}`` site's params as a plain dict."""
    params = getattr(site, "params", None)
    return {} if params is None else dict(params)


def _check_family(problems, where, uses, admitted, rung):
    """Refuse a selector that disagrees with its rung's row."""
    if admitted is None:
        if uses in _core_kinds(where):
            problems.append(
                f"{where}: rung {rung!r} admits no core kind here — {uses!r} is one of "
                f"{sorted(_core_kinds(where))}; name a child class as pkg.module:Class (D9)"
            )
    elif uses != admitted:
        problems.append(
            f"{where}: rung {rung!r} selects {admitted!r}, and the document names {uses!r} "
            "— an incompatible combination refuses at construction (§5.13.1)"
        )


def _core_kinds(where):
    """Return the registry kinds a family's core registry ships."""
    return set(_REGISTRIES[where].kinds())


#: Each rung-graded family's document path and its §4.3 registry.
_REGISTRIES = {
    "execution.uses": EXECUTOR_KINDS,
    "accounting.uses": ACCOUNTING_KINDS,
    "arming.approval.uses": APPROVAL_KINDS,
    "coordination.lease.uses": LEASE_KINDS,
}


def _check_live_executor(problems, cls, rung, admitted):
    """Refuse an executor class the rung cannot select (§5.7, §5.13.1)."""
    if admitted is None and not issubclass(cls, LiveExecutor):
        problems.append(
            f"execution.uses: rung {rung!r} needs a child LiveExecutor subclass, "
            f"got {cls.__name__} (§5.7)"
        )
    if admitted is not None and issubclass(cls, LiveExecutor):
        problems.append(
            f"execution.uses: rung {rung!r} can never select a LiveExecutor, got {cls.__name__} "
            "(§5.13.1)"
        )


def _check_live_lease(problems, cls, rung, admitted):
    """Refuse a lease that is not fenced where a live rung needs one (§5.7.2)."""
    if admitted is None and not getattr(cls, "LIVE_CAPABLE", False):
        problems.append(
            f"coordination.lease.uses: rung {rung!r} needs a fenced child lease whose class "
            f"declares LIVE_CAPABLE = True, got {cls.__name__} (§5.7.2)"
        )


def _resolved_families(problems, document, row):
    """Resolve the four rung-graded selectors, accumulating every disagreement."""
    rung = document.rung
    sites = {
        "execution.uses": (document.execution, row.executor),
        "accounting.uses": (document.accounting, row.accounting),
        "arming.approval.uses": (document.arming.approval, row.approval),
        "coordination.lease.uses": (document.coordination.lease, row.coordination),
    }
    resolved = {}
    for where, (site, admitted) in sites.items():
        _check_family(problems, where, site.uses, admitted, rung)
        try:
            resolved[where] = _REGISTRIES[where].resolve(site.uses)
        except ProductionError as exc:
            problems.extend(f"{where}: {problem}" for problem in exc.problems)
    if "execution.uses" in resolved:
        _check_live_executor(problems, resolved["execution.uses"], rung, row.executor)
    if "coordination.lease.uses" in resolved:
        _check_live_lease(problems, resolved["coordination.lease.uses"], rung, row.coordination)
    return resolved


def _schedule(document, sections, clock):
    """Build the ``Schedule`` bundle from the document's four selectors."""
    calendar = CALENDAR_KINDS.resolve(document.schedule.calendar.uses)(
        _selector(document.schedule.calendar)
    )
    cadence = CADENCE_KINDS.resolve(document.schedule.cadence.uses)(
        _selector(document.schedule.cadence)
    )
    overrun = Overrun(sections["schedule"].get("overrun") or {})
    return Schedule(clock=clock, calendar=calendar, cadence=cadence, overrun=overrun)


def _sinks(document, sections, resilience, clock, secrets):
    """Build the document's named alert sinks."""
    endpoints = document.alert_endpoints or {}
    built = {}
    for name, site in (document.alerting.sinks or {}).items():
        built[name] = ALERT_SINK_KINDS.resolve(site.uses)(
            _selector(site),
            endpoint=endpoints.get(name),
            transport=resilience.transport,
            clock=clock,
            secrets=secrets,
        )
    return built


def _accepted(cls):
    """Return the keyword-only collaborator names a class's ``__init__`` chain declares."""
    names = set()
    for ancestor in cls.__mro__:
        initialiser = ancestor.__dict__.get("__init__")
        if initialiser is None:
            continue
        names.update(
            name
            for name, parameter in inspect.signature(initialiser).parameters.items()
            if parameter.kind is inspect.Parameter.KEYWORD_ONLY
        )
    return names


def _offered(cls, offer):
    """Return the subset of ``offer`` the class declares — probes and emitters differ by kind."""
    accepted = _accepted(cls)
    return {name: value for name, value in offer.items() if name in accepted}


def _probes(document, *, serve_root, executor, feed_ages):
    """Build the document's named health probes with the collaborators each kind declares."""
    offer = {
        "serve_root": serve_root,
        "executor": executor,
        "config": document.execution,
        "feed_ages": feed_ages,
        "max_age_ms": document.schedule.max_staleness_ms,
    }
    built = {}
    for name, site in (document.health.probes or {}).items():
        cls = PROBE_KINDS.resolve(site.uses)
        built[name] = cls(
            _selector(site),
            name=name,
            scope=site.scope,
            timeout_s=site.timeout_s if site.timeout_s is not None else document.health.timeout_s,
            **_offered(cls, offer),
        )
    return built


def _emitters(document, *, serve_root, resilience, secrets):
    """Build the document's named heartbeat emitters with the collaborators each declares."""
    offer = {
        "path": serve_root.heartbeat_path,
        "transport": resilience.transport,
        "secrets": secrets,
    }
    built = {}
    sites = document.heartbeat.emitters if document.heartbeat is not None else {}
    for name, site in (sites or {}).items():
        cls = HEARTBEAT_KINDS.resolve(site.uses)
        built[name] = cls(_selector(site), **_offered(cls, offer))
    return built


def _outcome_sources(document, wiring):
    """Build the document's named outcome sources, in the order it declares them.

    The same shape as every other registry-resolved family: the document
    names WHAT, the §4.3 registry answers WHICH class, and each class is
    handed only the collaborators its own ``__init__`` declares. The
    onboarding root is opened only when a declared source asks for one — a
    document with no label stream never touches the store.
    """
    sites = _outcome_sites(document)
    resolved = {name: OUTCOME_SOURCE_KINDS.resolve(site.uses) for name, site in sites.items()}
    offer = {"executor": wiring.execution.executor}
    if any("root" in _accepted(cls) for cls in resolved.values()):
        root = OnboardingRoot(wiring.data.decider.contract.source_binding["root"])
        offer.update(root=root, registry=root.registry())
    return {
        name: resolved[name](_selector(site), **_offered(resolved[name], offer))
        for name, site in sites.items()
    }


def _outcome_sites(document):
    """Return the ``outcomes.sources`` selectors the document declares; none when absent."""
    section = document.outcomes
    if section is None or section.sources is None:
        return {}
    return dict(section.sources)


def _monitors(document):
    """Build the document's named monitors, each resolving its own strategies."""
    return {
        name: MONITOR_KINDS.resolve(site.uses)(_selector(site), name=name)
        for name, site in (document.monitors or {}).items()
    }


def _guards(document):
    """Build the document's named guards as one ordered chain."""
    return GuardChain(
        {
            name: GUARD_KINDS.resolve(site.uses)(_selector(site), name=name)
            for name, site in (document.guards or {}).items()
        }
    )


def bundles_for(
    document,
    release,
    registry,
    *,
    serve_root,
    secrets,
    invocation,
    process_id,
    clock=None,
    lock=None,
    journal_hook=None,
):
    """Build the seven collaborator bundles this document and release select.

    Parameters
    ----------
    document : ServeDocument
        The graded serve document; its ``rung`` is read here and nowhere
        else that selects an object.
    release : ReleaseManifest
        The immutable release being served.
    registry : NodeKindRegistry or None
        The node registry the served document is planned against; ``None``
        means the toolkit's.
    serve_root : ledger.ServeRoot
        The serve-series root, already bound to its genesis. It is a
        construction-time dependency rather than a bundle member (§5.16's
        one deliberate exception).
    secrets : dict
        The resolved ``env.require`` values; sinks and emitters read a URL
        from it at send time and keep it nowhere.
    invocation : bundles.Invocation
        ``--armed`` / ``DSKIT_PRODUCTION_ARM`` / ``--once`` / ``--max-ticks``.
    process_id : str
        This process's id — the ledger's envelope field and the heartbeat's.
    clock : Clock, optional
        Overrides ``schedule.clock``; how D20's replay and every test
        supply time without a wall clock.
    lock : health.InstanceLock, optional
        The lock the caller already holds. R18: ONE flock on ``serve.lock``,
        taken before the ledger opens and handed to it.
    journal_hook : callable, optional
        D22's injected seam; defaults to ``dskit.journal.hooks.record_production``,
        imported at function depth so the package stays importable without it.

    Returns
    -------
    tuple
        ``(Schedule, Data, Decision, Safety, Execution, Recording,
        Observability)`` — §5.16's order, which ``ServeLoop``, ``Tick`` and
        ``LegPipeline`` all take positionally.

    Raises
    ------
    ProductionError
        Naming every disagreement between the rung's row and the document
        at once, and every collaborator that refused its own params.
    """
    row = RUNG_TABLE[document.rung]
    problems = []
    resolved = _resolved_families(problems, document, row)
    if problems:
        raise ProductionError(problems)

    sections = document.to_obj()
    clock = clock if clock is not None else _clock_of(document)
    schedule = _schedule(document, sections, clock)
    resilience = resilience_from_document(
        sections["resilience"],
        clock=clock,
        sleeper=_sleeper(clock),
        rng=random.Random(DEFAULT_JITTER_SEED),
    )

    state = SeriesState(document.series_id)
    ledger = ledger_class(document)(
        serve_root,
        process_id,
        release.release_hash,
        clock=clock,
        fsync=sections["durability"]["fsync"],
        rotate=sections["placement"].get("rotate"),
        state=state,
        lock=lock,
    )
    inbox = ControlInbox(serve_root, clock)

    metrics = Metrics(log_dir=document.placement.log_dir)
    alerts = AlertRouter(
        document.alerting,
        _sinks(document, sections, resilience, clock, secrets),
        clock=clock,
        metrics=metrics,
        ledger=ledger,
    )

    lease = resolved["coordination.lease.uses"](_selector(document.coordination.lease), clock=clock)
    gate = _LateGate()
    executor = row.build.executor(
        resolved["execution.uses"],
        _selector(document.execution),
        clock=clock,
        scope=document.coordination.scope,
        gate=gate,
        lease=lease,
        signer=row.build.signer(document, clock),
    )
    accounting = resolved["accounting.uses"](
        _selector(document.accounting),
        clock=clock,
        history=LedgerHistory(ledger),
        max_valuation_age_ms=document.accounting.max_valuation_age_ms,
    )

    health = Health(
        document.health,
        _probes(
            document,
            serve_root=serve_root,
            executor=executor,
            feed_ages=_FeedAges(),
        ),
        clock=clock,
        alerts=alerts,
        ledger=ledger,
        metrics=metrics,
        in_degraded=(
            document.heartbeat.in_degraded
            if document.heartbeat is not None and document.heartbeat.in_degraded is not None
            else DEFAULT_IN_DEGRADED
        ),
    )
    beat = document.heartbeat if document.heartbeat is not None else _NoHeartbeat()
    heartbeat = Heartbeat(
        beat,
        _emitters(document, serve_root=serve_root, resilience=resilience, secrets=secrets),
        clock=clock,
        health=health,
        process_id=process_id,
        dead_after_ms=document.schedule.dead_after_ms,
    )

    guards = _guards(document)
    action_policy = ActionPolicy()
    transition_policy = TransitionPolicy()
    arming = Arming(
        document,
        release,
        serve_root=serve_root,
        verifier=approval_verifier(document),
        clock=clock,
    )
    breaker = Breaker(
        document,
        serve_root,
        ledger=ledger,
        state=state,
        clock=clock,
        transition_policy=transition_policy,
        executor=executor,
    )
    readiness = Readiness(
        document,
        release,
        ledger=ledger,
        state=state,
        clock=clock,
        checklist_path=document.readiness.checklist,
    )
    submission_verifier = SubmissionVerifier(
        executor,
        accounting,
        lease,
        arming,
        guards,
        action_policy,
        release,
        inbox,
        schedule.calendar,
        document,
        clock,
        health=health,
    )
    gate.bind(submission_verifier)
    authorities = AuthorityTable(
        document.rung,
        {
            origin: cls(
                clock,
                schedule.calendar,
                arming,
                lease,
                health,
                executor,
                document,
                release,
                ledger,
                inbox,
            )
            for origin, cls in row.authority.items()
        },
    )

    decider = Decider(
        document,
        release,
        registry=registry,
        adapter=document.serving.adapter,
        proposer=PROPOSER_KINDS.resolve(document.serving.proposer.uses)(
            _selector(document.serving.proposer)
        ),
        clock=clock,
    )
    decider.prepare(utc_iso(clock.now_ms())[:10], document.serving.run_dir)
    onboarding_root = OnboardingRoot(decider.contract.source_binding["root"])
    feed = FEED_KINDS.resolve(document.feed.uses)(
        _selector(document.feed),
        root=onboarding_root,
        registry=onboarding_root.registry(),
        contract=decider.contract,
        spec=decider.feed_spec,
        clock=clock,
        max_staleness_ms=document.schedule.max_staleness_ms,
        dead_after_ms=document.schedule.dead_after_ms,
    )

    head_seq, head_hash = ledger.head()
    recording = Recording(
        ledger=ledger,
        state=state,
        inbox=inbox,
        reconciler=Reconciler(document, release, ledger=ledger, state=state, clock=clock),
        checkpoint=Checkpoint(
            release_hash=release.release_hash,
            last_tick_at=None,
            last_completed_tick_at=None,
            pending=(),
            positions_snapshot_at=None,
            schema_version=1,
            head_seq=head_seq,
            head_hash=head_hash,
        ),
        journal_hook=journal_hook if journal_hook is not None else _journal_hook(),
        id_source=ReleaseIdSource(release.release_hash),
    )
    _LOG.info("composed %s at rung %s as process %s", document.series_id, document.rung, process_id)
    return (
        schedule,
        Data(feed=feed, decider=decider),
        Decision(guards=guards, monitors=MappingProxyType(_monitors(document))),
        Safety(
            breaker=breaker,
            arming=arming,
            authorities=authorities,
            readiness=readiness,
            invocation=invocation,
            action_policy=action_policy,
            transition_policy=transition_policy,
            submission_verifier=submission_verifier,
        ),
        Execution(
            executor=executor, accounting=accounting, lease=lease, resilience=resilience
        ),
        recording,
        Observability(metrics=metrics, alerts=alerts, health=health, heartbeat=heartbeat),
    )


def _never():
    """Answer that nothing wakes a retry backoff early.

    ``Clock.sleep_until`` polls a wake predicate, and the loop's stop
    flag is not reachable from the composition root — a backoff
    therefore runs to its deadline, bounded by
    ``resilience.retry.cap_s``, and the process stops at the next tick
    boundary rather than mid-wait.

    Returns
    -------
    bool
        Always ``False``.
    """
    return False


def _sleeper(clock):
    """Return the seconds-based sleeper the resilience policies wait through."""

    def sleep(seconds):
        return clock.sleep_until(clock.now_ms() + int(seconds * _MS_PER_S), _never)

    return sleep


def _clock_of(document):
    """Build the clock the document names; clocks take keywords, not a params dict."""
    return CLOCK_KINDS.resolve(document.schedule.clock.uses)(**_selector(document.schedule.clock))


def _journal_hook():
    """D22's default seam, imported at function depth (the package import rule)."""
    from dskit.journal.hooks import record_production

    return record_production


# ---------------------------------------------------------------------------
# handlers_for — CONTROL_PURPOSES -> the object that owns the verb (§5.8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Wiring:
    """What every control handler reads: the document, the seven bundles, and the join.

    ``join`` is the one phase-2 composite a control verb owns (§5.16: the
    outcome join is not a bundle member). It is ``None`` when
    :func:`handlers_for` was called without the release every outcome id is
    bound to, and the ``outcomes`` verb refuses rather than recording
    against an unnamed release.
    """

    document: object
    schedule: object
    data: object
    decision: object
    safety: object
    execution: object
    recording: object
    observability: object
    join: object = None


class _Verb(ABC):
    """One control verb: read the payload, call the owner, answer the processor.

    A handler NEVER builds a record the owner already builds, and never
    appends: ``CommandProcessor`` appends the ``control_request`` before
    dispatch and whatever the handler returns after it, inside one
    barrier. Owners that append for themselves (``Breaker``,
    ``Readiness``, ``Reconciler``) therefore return no records at all.
    """

    PURPOSE = None

    def __init__(self, wiring):
        self._w = wiring

    def __call__(self, command, view):
        """Run the verb; every answer is ``(records, status, reason)``."""
        return self.run(command, view)

    @abstractmethod
    def run(self, command, view):
        """Return ``(records, status, reason)`` for one consumed command."""

    def observable(self):
        """Return and CLEAR the §6 bodies this verb recorded for a monitor to observe.

        A verb that appends through an OWNER — the join, the breaker, the
        reconciler — never passes those bodies through the loop's own
        ``_bodies``, so a monitor would never see them. The verb reports
        them here and the LOOP feeds them (§5.10 keeps the driving there),
        which adds no second reader of the ledger. It is a hook on every
        verb rather than on one because ``adopt``'s ``cash_flow`` is the
        next body a monitor could want; the base records none.

        Returns
        -------
        tuple of dict
            Empty for every verb whose records no monitor observes. It is
            DRAINED rather than read: an observation counted twice moves
            every statistic.
        """
        return ()

    @staticmethod
    def applied(records=(), reason=""):
        """Answer the processor for a verb that took effect."""
        return tuple(records), "applied", reason

    @staticmethod
    def rejected(reason):
        """Answer the processor for a verb the payload does not support."""
        return (), "rejected", reason

    @staticmethod
    def record(kind, record_id, body):
        """One ``{kind, id, body}`` record for the processor to append (R9)."""
        return {"kind": kind, "id": f"{kind}:{record_id}", "body": body}


class _Halt(_Verb):
    """`halt`: the out-of-band kill switch's audit command (§5.6, D12)."""

    PURPOSE = "halt"

    def run(self, command, view):
        """Trip the breaker into ``halted``; it appends, barriers and cancels."""
        reason = command["payload"].get("reason", _OPERATOR_REASON)
        if reason not in TRIP_REASONS:
            return self.rejected(f"halt reason must be one of {list(TRIP_REASONS)}, got {reason!r}")
        self._w.safety.breaker.trip(
            reason,
            _OPERATOR_ACTOR,
            control_request_id=command["request_id"],
            cause=_HALT_CAUSE,
        )
        return self.applied()


class _Reduce(_Verb):
    """`reduce`: the authenticated transition into ``reducing`` (D12)."""

    PURPOSE = "reduce"

    def run(self, command, view):
        """Ask the breaker to enter ``reducing`` under the operator's proof."""
        principal, proof = _principal_and_proof(self._w, command)
        self._w.safety.breaker.reduce(
            _OPERATOR_ACTOR, command["request_id"], principal, proof
        )
        return self.applied()


class _Resume(_Verb):
    """`resume`: the authenticated reset after cooling-off (§5.6)."""

    PURPOSE = "resume"

    def run(self, command, view):
        """Reset the breaker against the trip the operator acknowledged."""
        acknowledged = command["payload"].get("acknowledges_trip_id")
        if not isinstance(acknowledged, str) or not acknowledged:
            return self.rejected("resume requires acknowledges_trip_id: a reset without a trip "
                                 "id is not a reset (§5.6)")
        principal, proof = _principal_and_proof(self._w, command)
        self._w.safety.breaker.reset(
            _OPERATOR_ACTOR, acknowledged, command["request_id"], principal, proof
        )
        return self.applied()


class _Disarm(_Verb):
    """`disarm`: the safe demotion an operator may always take."""

    PURPOSE = "disarm"

    def run(self, command, view):
        """Return the ``authority`` body that ends the current arm."""
        body = self._w.safety.arming.disarm(view, request_id=command["request_id"])
        return self.applied([authority_record(body)])


class _ArmRequestVerb(_Verb):
    """`arm_request`: the maker's half of D11's maker-checker arm."""

    PURPOSE = "arm_request"

    def run(self, command, view):
        """Verify the maker's proof through ``Arming``; append nothing.

        ``Arming.request`` RETURNS the §6 ``control_request`` body, and the
        processor has already appended that record on receipt — returning
        it again would put two records with one semantic id on the chain.
        """
        request = _arm_request(command["payload"], command["proof"])
        self._w.safety.arming.request(request, command["request_id"])
        return self.applied()


class _ArmApproval(_Verb):
    """`arm_approval`: the checker's half, and the one call that mints authority."""

    PURPOSE = "arm_approval"

    def run(self, command, view):
        """Approve the named request into an armable world (R23).

        The maker's raw proof comes back out of the maker command's own
        durable receipt, never out of a ledger record — §6 keeps proof
        bytes off the chain, and ``Arming.approve`` re-verifies the maker.
        """
        maker_id = command["payload"].get("request_id")
        receipt = None if not isinstance(maker_id, str) else self._w.recording.inbox.receipt(maker_id)
        if receipt is None or receipt.get("purpose") != _ArmRequestVerb.PURPOSE:
            return self.rejected(
                f"arm_approval names request {maker_id!r}, which this spool has no "
                f"{_ArmRequestVerb.PURPOSE} receipt for"
            )
        request = _arm_request(receipt["payload"], receipt["proof"])
        approval = ArmApproval(
            request_digest=command["payload"].get("request_digest"),
            approval_proof=command["proof"],
        )
        at_ms = self._w.schedule.clock.now_ms()
        body, _state = self._w.safety.arming.approve(
            approval,
            request,
            maker_id,
            command["request_id"],
            view=view,
            at_ms=at_ms,
            readiness_verdict=self._w.safety.readiness.verdict_for(view, at_ms),
            sentinel_present=self._w.safety.breaker.halt_sentinel_present(),
        )
        return self.applied(
            [
                self.record(
                    "control_approval",
                    command["request_id"],
                    {
                        "request_id": maker_id,
                        "purpose": self.PURPOSE,
                        "principal_digest": None,
                        "proof_digest": approval.request_digest,
                        "verified_payload_digest": approval.request_digest,
                    },
                ),
                authority_record(body),
            ]
        )


class _FlattenRequest(_Verb):
    """`flatten_request`: the maker's reduction plan, and the move to ``reducing``."""

    PURPOSE = "flatten_request"

    def run(self, command, view):
        """Enter ``reducing`` under the operator's proof; the plan rides the request."""
        principal, proof = _principal_and_proof(self._w, command)
        self._w.safety.breaker.flatten(
            _OPERATOR_ACTOR, command["request_id"], principal, proof
        )
        return self.applied()


class _FlattenApproval(_Verb):
    """`flatten_approval`: the checker's authorization of the stored plan (§5.6)."""

    PURPOSE = "flatten_approval"

    def run(self, command, view):
        """Grant one single-use right per unique intent digest in the stored plan.

        The maker's plan comes back out of its own durable receipt — the
        checker approves what the maker actually queued, not what the
        approval command restates — and the checker's proof is verified
        here, where the authority is issued.
        """
        maker_id = command["payload"].get("request_id")
        receipt = (
            None if not isinstance(maker_id, str) else self._w.recording.inbox.receipt(maker_id)
        )
        if receipt is None or receipt.get("purpose") != _FlattenRequest.PURPOSE:
            return self.rejected(
                f"flatten_approval names request {maker_id!r}, which this spool has no "
                f"{_FlattenRequest.PURPOSE} receipt for"
            )
        plan = ReductionPlan.from_obj(receipt["payload"].get("plan"))
        checker = approval_verifier(self._w.document).verify(
            canonical_bytes({"plan": plan.to_obj()}), command["proof"], self.PURPOSE
        )
        authority_id = canonical_hash(
            [_REDUCTION_AUTHORITY_TAG, canonical_hash(plan.to_obj()), maker_id,
             command["request_id"]]
        )
        grant = ReductionRights(clock=self._w.schedule.clock).from_plan(
            plan, checker, authority_id, plan.expires_ms
        )
        return self.applied(
            [
                self.record(
                    "control_approval",
                    command["request_id"],
                    {
                        "request_id": maker_id,
                        "purpose": self.PURPOSE,
                        "principal_digest": canonical_hash(checker.id),
                        "proof_digest": checker.proof_digest,
                        "verified_payload_digest": canonical_hash(plan.to_obj()),
                    },
                ),
                authority_record(
                    {
                        "authority_id": authority_id,
                        "event": "issue",
                        "role": "reduction",
                        "request_id": maker_id,
                        "approval_id": command["request_id"],
                        "reason": None,
                        "authorization": grant.to_obj(),
                    }
                ),
            ]
        )


class _ExecuteFlatten(_Verb):
    """`execute_flatten`: the queued cycle an active ready loop runs (§5.8)."""

    PURPOSE = "execute_flatten"

    def run(self, command, view):
        """Accept the cycle as QUEUED, not as completed.

        §5.8: it "is moved to `applied` when its cycle is *queued*, not
        when it completes — otherwise the pending-control gate of §5.8
        would block the cycle's own first leg". The in-flight cycle's
        durable marker is its ``authority_use`` reservations.
        """
        if view.reduction is None:
            return self.rejected(
                "execute_flatten needs a current reduction authority; the fold holds none"
            )
        authorization = command["payload"].get("authorization_id")
        if authorization != view.reduction.authority_id:
            return self.rejected(
                f"execute_flatten names authorization {authorization!r}, and the fold's is "
                f"{view.reduction.authority_id!r}"
            )
        return self.applied(reason="cycle queued")


class _Reconcile(_Verb):
    """`reconcile`: one run against the venue, and the document's mismatch policy."""

    PURPOSE = "reconcile"

    def run(self, command, view):
        """Run the reconciler over the document's scope and apply its answer."""
        report = self._w.recording.reconciler.run(
            view, self._w.execution.executor, self._w.document.coordination.scope
        )
        action = enact(
            self._w.recording.reconciler.apply_policy(report),
            breaker=self._w.safety.breaker,
            verifier=self._w.safety.submission_verifier,
            actor=self.PURPOSE,
            control_request_id=command["request_id"],
        )
        return self.applied(reason=action)


class _Outcomes(_Verb):
    """`outcomes`: collect what resolved and record it (§5.13.2).

    It is the one verb whose records a monitor observes — §5.10.1's outcome
    family needs a label — so it is also the one that reports them. The
    report is EXACTLY-ONCE without a watermark, because
    ``OutcomeJoin.collect`` already drops anything standing unsuperseded in
    the fold: what it returns is new by construction, so a replayed command
    collects nothing and therefore reports nothing.
    """

    PURPOSE = "outcomes"

    def __init__(self, wiring):
        super().__init__(wiring)
        self._observable = []

    def observable(self):
        """Return and clear the ``outcome`` bodies this verb has recorded since the last drain."""
        drained, self._observable = tuple(self._observable), []
        return drained

    def run(self, command, view):
        """Collect at the cut and record what is new; the join appends and barriers.

        The cut is the consumed command's ``queued_at_ms`` unless the
        operator named one, never the handler's clock: a crash-replayed
        ``outcomes`` must produce byte-identical payloads or
        ``Ledger.append`` refuses them as changed payloads under reused
        ids, and the operator's second attempt becomes a second arrival.
        """
        if self._w.join is None:
            raise ProductionError(
                [
                    "the outcomes verb needs the release every outcome id is bound to — "
                    "call handlers_for(document, bundles, release=release)"
                ]
            )
        at_ms = command["payload"].get("asof_ms")
        if at_ms is None:
            at_ms = command["queued_at_ms"]
        elif not isinstance(at_ms, int) or isinstance(at_ms, bool) or at_ms < 0:
            return self.rejected(
                f"outcomes asof_ms must be a non-negative epoch-ms int, got {at_ms!r}"
            )
        collected = self._w.join.collect(at_ms)
        recorded = self._w.join.record(collected)
        self._observable.extend(outcome.to_obj() for outcome in collected)
        return self.applied(reason=f"recorded {len(recorded)} outcome(s)")


class _Ready(_Verb):
    """`ready`: the release-bound GO / NO-GO the action matrix reads (§5.13)."""

    PURPOSE = "ready"

    def run(self, command, view):
        """Evaluate the checklist and record the verdict; ``record`` appends it."""
        result = self._w.safety.readiness.evaluate(self._w.schedule.clock.now_ms())
        self._w.safety.readiness.record(result)
        return self.applied(reason=str(getattr(result, "verdict", "")))


class _Adopt(_Verb):
    """`adopt`: the authenticated adoption of named venue breaks (§5.9, D13)."""

    PURPOSE = "adopt"

    def run(self, command, view):
        """Bank each cash break under the operator's proof.

        §6 stamps the ``cash_flow``'s ``known_at_ms`` from the CONSUMED
        COMMAND's ``queued_at_ms``, never from the handler's clock: a
        crash-replayed adopt must produce a byte-identical payload or
        ``Ledger.append`` refuses it as a changed payload under a reused
        id, and the operator's second attempt becomes a second bank.
        """
        payload = command["payload"]
        break_ids = payload.get("break_ids")
        flow_kind = payload.get("flow_kind")
        external = payload.get("external")
        if not isinstance(break_ids, (list, tuple)) or not break_ids:
            return self.rejected("adopt requires break_ids: the breaks being adopted (§5.9)")
        if flow_kind not in CASH_FLOW_KINDS:
            return self.rejected(
                f"adopt requires flow_kind, one of {list(CASH_FLOW_KINDS)}, got {flow_kind!r} "
                "— it comes from the operator's proof and never defaults (§6)"
            )
        if not isinstance(external, bool):
            return self.rejected(
                f"adopt requires a boolean external, got {external!r} — it never defaults to "
                "true (§6)"
            )
        principal, proof = _principal_and_proof(self._w, command)
        ids = self._w.recording.reconciler.adopt(
            view,
            tuple(break_ids),
            command["request_id"],
            principal,
            proof,
            command["release_hash"],
            flow_kind=flow_kind,
            external=external,
            known_at_ms=command["queued_at_ms"],
        )
        return self.applied(reason=f"adopted {len(tuple(ids))} record(s)")


#: One handler class per ``CONTROL_PURPOSES`` member, pinned exact: a
#: purpose with no handler is silently rejected by ``CommandProcessor``,
#: so a missing entry is an operator act that vanishes into a receipt.
_VERBS = pin_members(
    "compose.py's control handlers",
    {
        verb.PURPOSE: verb
        for verb in (
            _ArmRequestVerb,
            _ArmApproval,
            _Reduce,
            _FlattenRequest,
            _FlattenApproval,
            _ExecuteFlatten,
            _Resume,
            _Adopt,
            _Halt,
            _Disarm,
            _Reconcile,
            _Ready,
            _Outcomes,
        )
    },
    CONTROL_PURPOSES,
    exact=True,
)


def _principal_and_proof(wiring, command):
    """Return the ``(principal_digest, proof_digest)`` a transition binds.

    The proof digest is the sha256 of the proof BYTES — the same value
    ``CommandProcessor`` puts on the ``control_request`` record — so the
    transition and the request name one proof. The raw bytes never
    leave this function (§6).
    """
    principal = command["payload"].get("principal_digest")
    if not isinstance(principal, str):
        principal = canonical_hash(["control-principal-v1", command["request_id"]])
    return principal, hashlib.sha256(command["proof"]).hexdigest()


def _arm_request(payload, proof):
    """Rebuild an ``ArmRequest`` from a stored payload and its raw proof."""
    fields = dict(payload)
    fields.pop("request_proof", None)
    try:
        return ArmRequest(
            release_hash=fields.get("release_hash"),
            rung=fields.get("rung"),
            allowlist=tuple(fields.get("allowlist") or ()),
            limits_overlay=dict(fields.get("limits_overlay") or {}),
            requested_until_ms=fields.get("requested_until_ms"),
            request_proof=proof,
        )
    except ProductionError as exc:
        raise ProductionError([redact(problem) for problem in exc.problems]) from exc


def outcome_join(document, release, bundles):
    """Build the bitemporal outcome join this document declares (§5.13.2, §5.16).

    ``OutcomeJoin`` is a composite rather than a bundle member, so the
    composition root builds it here beside the objects it names: the
    ordered ``name -> OutcomeSource`` map comes from
    ``document.outcomes.sources`` through the §4.3 registry, and the join
    is handed the ledger, the fold and the clock the rest of the process
    already shares.

    Parameters
    ----------
    document : ServeDocument
        Read for ``outcomes.sources``; a document that declares none gets a
        join with no sources, which collects nothing.
    release : ReleaseManifest
        Binds ``release_hash``, a term of every outcome id.
    bundles : tuple
        The seven bundles :func:`bundles_for` returned, in §5.16's order.

    Returns
    -------
    OutcomeJoin
        Ready to ``collect``, ``record`` and answer at any cut.

    Raises
    ------
    ProductionError
        When ``bundles`` is not the seven-tuple ``bundles_for`` returns, or
        a declared source refuses its own params.

    Examples
    --------
    ::

        join = outcome_join(document, release, bundles)
        join.record(join.collect(clock.now_ms()))
    """
    wiring = _Wiring(document, *_seven(bundles))
    return OutcomeJoin(
        document,
        release,
        ledger=wiring.recording.ledger,
        state=wiring.recording.state,
        clock=wiring.schedule.clock,
        sources=_outcome_sources(document, wiring),
    )


def _seven(bundles):
    """Return ``bundles`` as the seven-tuple every composite takes, or refuse."""
    if not isinstance(bundles, (list, tuple)) or len(bundles) != 7:
        raise ProductionError(
            [f"this composite takes the seven bundles bundles_for returned, got {bundles!r}"]
        )
    return tuple(bundles)


def handlers_for(document, bundles, *, release=None):
    """Return the control-verb dispatch table ``CommandProcessor`` runs on.

    Parameters
    ----------
    document : ServeDocument
        Read for the reconcile scope; a handler never reads a document key
        except through this object (§5.16).
    bundles : tuple
        The seven bundles :func:`bundles_for` returned, in §5.16's order.
    release : ReleaseManifest, optional
        The release the ``outcomes`` verb binds every outcome id to. Every
        caller that can run that verb passes it; without it the verb
        refuses rather than recording against an unnamed release.

    Returns
    -------
    dict
        ``purpose -> handler(command, view) -> (records, status, reason)``,
        one entry per ``vocab.CONTROL_PURPOSES`` member.

    Raises
    ------
    ProductionError
        When ``bundles`` is not the seven-tuple ``bundles_for`` returns.

    Examples
    --------
    ::

        handlers = handlers_for(document, bundles, release=release)
        processor = CommandProcessor(inbox, ledger, state, handlers, clock)
        processor.process_pending(state.snapshot())
    """
    bundles = _seven(bundles)
    join = None if release is None else outcome_join(document, release, bundles)
    wiring = _Wiring(document, *bundles, join=join)
    return {purpose: verb(wiring) for purpose, verb in _VERBS.items()}
