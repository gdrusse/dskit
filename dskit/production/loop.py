"""The scheduler and the phase walk: `ServeLoop` and `Tick` (§5.13).

Two objects with two jobs, and the split is the point.

:class:`Tick` is one scheduled instant's decision. Its ``run`` is concrete
and FINAL: it walks ``vocab.TICK_PHASES`` in order, times each phase into
``latency_ms``, assembles the one ``TickState`` every guard sees and the
``LegBindings`` every leg is bound to, runs a ``LegPipeline`` per proposal
and returns a ``TickResult``. No subclass may reorder or skip a phase,
which is what makes D23's fixed phase order and §6's pinned latency keys
structural rather than advisory; the ten phase methods stay overridable,
because the STEPS are the seam and the ORDER is not. Each phase takes and
returns declared values rather than mutating ``self``, so the dataflow
between them is part of the contract and a phase holds no scratch state
between calls.

:class:`ServeLoop` is the scheduler, NOT the composition root (§5.13.1):
the lifecycle FSM, cadence and overrun, control-inbox consumption, `Tick`
construction, monitor ``observe``, the metrics flush, the checkpoint, the
D22 journal row and the exit code. It contains no submission sequence —
the one place money leaves the process is ``leg.py`` — and it asks no
rung, because ``compose.py`` already chose every object it holds.

Three orderings in here are load-bearing and are asserted rather than
described. Startup is lock, then lease, then :class:`~state.Recovery`,
then the ``process`` start record, then reconciliation, then READY: the
fold has to be replayed before anything appends, and a venue cannot be
reconciled against a fold that has not been rebuilt. Each tick is
``tick_start`` appended and barriered before any phase (D13), the phases,
then ``decision`` and ``tick`` — one of each per ``tick_start`` — and only
then ``observe``, ``AlertRouter.process``, ``Metrics.flush``, the
heartbeat and the checkpoint LAST. And a graceful stop appends and
barriers the ``process`` stop record, reads the resulting head, and only
then writes D22's one journal row: the ledger never claims its own final
hash.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from dskit.production.base import ProductionError, canonical_hash, pin_members
from dskit.production.compose import handlers_for
from dskit.production.control import EXECUTING_PURPOSES, CommandProcessor
from dskit.production.decider import DEFAULT_MAX_ARTIFACT_AGE
from dskit.production.health import SignalHandler
from dskit.production.leg import LegBindings, LegPipeline, ReductionBinding
from dskit.production.ledger import Checkpoint
from dskit.production.records import (
    FeedAge,
    PolicyRequest,
    Provenance,
    QuoteSet,
    ReductionPlan,
    TickResult,
    TickStart,
)
from dskit.production.redact import get_logger, redact
from dskit.production.release import parse_iso_duration, verify_release
from dskit.production.state import Recovery, TickState
from dskit.production.vocab import (
    EXIT_CODES,
    FEED_STATUSES,
    HEALTH_STATES,
    LEG_LATENCY_BUCKETS,
    LOOP_STATES,
    METRIC_LABEL_VALUES,
    TICK_PHASES,
    TICK_STATUSES,
    TRIP_REASONS,
)

__all__ = ["JOURNAL_NOTES", "JOURNAL_STEP", "SERVE_VERB", "ServeLoop", "Tick"]

_LOG = get_logger("loop")

#: The tick statuses this module spells for itself, pinned to the closed
#: vocabulary so a renamed member refuses at import rather than producing
#: a record nothing downstream can classify.
_DECIDED, _SKIPPED_CLOSED, _SKIPPED_STALE, _SKIPPED_SKEW, _SKIPPED_HALTED, _SKIPPED_DEGRADED, \
    _SKIPPED_NO_COVERAGE, _REFUSED, _FAILED = pin_members(
        "loop.py's tick statuses",
        (
            "decided",
            "skipped:closed",
            "skipped:stale",
            "skipped:skew",
            "skipped:halted",
            "skipped:degraded",
            "skipped:no_coverage",
            "refused",
            "failed",
        ),
        TICK_STATUSES,
        exact=True,
    )

#: The health state that may act. §5.11: `degraded` observes and refuses
#: acts; `unhealthy` stops acting and heartbeating.
_READY_HEALTH = pin_members("loop.py's acting health state", ("ready",), HEALTH_STATES)[0]

#: The breaker state that refuses submissions (D12).
_HALTED_BREAKER = "halted"

#: Why the loop trips: a monitor alarm whose declared response is `halt`,
#: a reconciliation the document said to halt on, and the kill switch.
_MONITOR_TRIP = pin_members("loop.py's monitor trip", ("monitor_alarm",), TRIP_REASONS)[0]
_RECONCILE_TRIP = pin_members("loop.py's reconcile trip", ("reconcile_mismatch",), TRIP_REASONS)[0]
_OPERATOR_TRIP = pin_members("loop.py's operator trip", ("operator",), TRIP_REASONS)[0]

#: `Breaker.trip`'s cause for the out-of-band kill switch (§5.6).
_HALT_CAUSE = "halt"

#: What `Reconciler.apply_policy` answers when the document says to halt.
_RECON_HALTS = "halt"

#: The feed status a tick that never fetched reports in memory. The §6
#: `tick` record carries `feed: null` for such a tick instead — inventing
#: an observation nobody took is what §6 forbids — and this value is what
#: the loop recognises to do that: `records_added` is an int on every real
#: fetch, so `None` cannot be produced by one.
_UNFETCHED_STATUS = pin_members("loop.py's unfetched feed status", ("closed",), FEED_STATUSES)[0]

#: The three lifecycle states a serve process can finish in, and the four
#: it passes through. Pinned exact so a renamed state refuses at import.
_INIT, _LOCKED, _LEASED, _RECONCILING, _READY, _WAITING, _TICKING, _STOPPING, _STOPPED, _HALTED, _FAULTED = \
    pin_members(
        "loop.py's lifecycle states",
        (
            "init",
            "locked",
            "leased",
            "reconciling",
            "ready",
            "waiting",
            "ticking",
            "stopping",
            "stopped",
            "halted",
            "faulted",
        ),
        LOOP_STATES,
        exact=True,
    )

#: D22's `notes` form, which `verify` parses back. One place, because a
#: renderer and a parser that drift are a journal anchor nobody can use.
JOURNAL_NOTES = "production-v1 process={process_id} head={seq}:{head_hash}"

#: D22's `step`: the verb and the rung, inside the journal's 80-character
#: bound.
JOURNAL_STEP = "{verb} {rung}"

#: The verb this module journals as.
SERVE_VERB = "serve"

_MS_PER_S = 1000


def _unfetched_feed():
    """Return the §6 feed block of a tick that refused before it fetched."""
    return {
        "status": _UNFETCHED_STATUS,
        "acq_id": None,
        "records_added": None,
        "source_config_hash": None,
        "required_keys_digest": None,
        "watermarks_by_key": None,
        "coverage_digest": None,
    }


class _Refused(Exception):
    """A tick refusing itself: a recorded status and reason, never an error."""

    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status
        self.reason = reason


# ---------------------------------------------------------------------------
# Where a tick's proposals come from: the proposer, or a stored plan
# ---------------------------------------------------------------------------


class _Cycle(ABC):
    """What a tick decides from — the model's proposer, or a signed plan (§5.13)."""

    @abstractmethod
    def candidates(self, tick, head_outputs):
        """Return the candidate set this tick accounts for."""

    @abstractmethod
    def proposals(self, tick, head_outputs, candidates, account, provenance):
        """Return the proposals this tick runs, in the order it runs them."""

    @abstractmethod
    def binding(self, tick, proposal, view):
        """Return ``(origin, reduction)`` for one proposal's ``LegBindings``."""


class _ModelCycle(_Cycle):
    """An ordinary tick: the configured ``Proposer`` decides."""

    ORIGIN = "model"

    def candidates(self, tick, head_outputs):
        """Ask the decider's proposer for this tick's candidates."""
        return tuple(tick.data.decider.proposer.candidates(head_outputs))

    def proposals(self, tick, head_outputs, candidates, account, provenance):
        """Size the candidates, then order by stable candidate id (§5.13)."""
        proposals = tick.data.decider.proposer.proposals(
            head_outputs, candidates, account, provenance
        )
        return tuple(sorted(proposals, key=lambda proposal: proposal.id))

    def binding(self, tick, proposal, view):
        """Answer ``model`` and no reduction binding."""
        return self.ORIGIN, None


class _ReductionCycle(_Cycle):
    """`execute-flatten`'s own tick: the plan's signed intents, in maker order.

    §5.13: a reduction cycle "carries only the plan's legs, in
    maker-approved ``index`` order, never interleaved with model legs".
    No proposer runs — the candidate is signed, not derived — and the
    candidates reach ``Tick.account`` so their scope keys are in the
    requirement union and their guards find the evidence they demand.
    """

    ORIGIN = "reduction"

    def __init__(self, plan):
        self._plan = plan
        self._by_id = {intent.proposal.id: intent for intent in plan.intents}

    def candidates(self, tick, head_outputs):
        """Contribute the plan's signed candidates, in the maker's order."""
        return tuple(intent.candidate for intent in self._plan.intents)

    def proposals(self, tick, head_outputs, candidates, account, provenance):
        """Return the plan's stored proposals, never a proposer's."""
        return tuple(intent.proposal for intent in self._plan.intents)

    def binding(self, tick, proposal, view):
        """Bind the signed intent, its digest and the right being consumed."""
        intent = self._by_id[proposal.id]
        digest = intent.reduction_intent_digest()
        grant = view.reduction
        return self.ORIGIN, ReductionBinding(
            signed=intent,
            digest=digest,
            right=None if grant is None else grant.authority_id,
        )


# ---------------------------------------------------------------------------
# Tick
# ---------------------------------------------------------------------------


@dataclass
class _Walk:
    """What ``Tick.run`` threads between the ten phases — never state on ``self``."""

    tick_at_ms: int
    latency: dict
    status: str = _DECIDED
    reason: str = ""
    error: object = None
    feed: object = None
    batch: object = None
    ages: tuple = ()
    head_outputs: object = None
    head_digest: object = None
    candidates: tuple = ()
    quotes: object = None
    account: object = None
    requirements: tuple = ()
    nav: object = None
    proposals: tuple = ()
    legs: list = field(default_factory=list)
    plan_ids: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    leg_latency: dict = field(default_factory=lambda: dict.fromkeys(LEG_LATENCY_BUCKETS, 0))


def _phase_gate(tick, walk):
    """Run the gate and refuse the tick when it did not pass."""
    result = tick.gate(walk.tick_at_ms)
    if not result.passed:
        raise _Refused(_GATE_STATUSES[result.gate], result.reason)


def _phase_verify_release(tick, walk):
    """Re-verify the release; a refusal is the tick's."""
    tick.verify_release()


def _phase_fetch(tick, walk):
    """Pull the feed and refuse on a status the ladder says not to decide from."""
    walk.feed = tick.fetch(walk.tick_at_ms)
    refusal = _FEED_STATUSES.get(walk.feed.status)
    if refusal is not None:
        raise _Refused(refusal, f"feed status {walk.feed.status!r}")


def _phase_read_entry(tick, walk):
    """Read the frozen entry batch this tick decides from."""
    walk.batch = tick.read_entry(walk.tick_at_ms)


def _phase_coverage(tick, walk):
    """Prove exact uniform coverage and take every key's age."""
    walk.ages = tick.coverage(walk.batch)


def _phase_evaluate(tick, walk):
    """Run the served subgraph and keep its head outputs and digest."""
    walk.head_outputs, walk.head_digest = tick.evaluate(walk.batch)


def _phase_candidates(tick, walk):
    """Take the candidate set this tick accounts for."""
    walk.candidates = tick.candidates(walk.head_outputs)


def _phase_quotes(tick, walk):
    """Take the quotes every proposal and permit binds."""
    walk.quotes = tick.quotes(walk.head_outputs)


def _phase_account(tick, walk):
    """Snapshot the account against the requirement union, and mark the book."""
    walk.account, walk.requirements = tick.account(
        walk.candidates, walk.quotes, walk.tick_at_ms
    )
    walk.nav = tick.execution.accounting.value(
        tick.recording.state.snapshot(), walk.quotes, walk.tick_at_ms
    )


def _phase_propose(tick, walk):
    """Size the candidates into the ordered proposals this tick runs."""
    walk.proposals = tick.propose(
        walk.head_outputs,
        walk.candidates,
        walk.account,
        Provenance(
            inputs_asof_ms=walk.batch.data_asof_ms,
            inputs_digest=walk.batch.inputs_digest,
            coverage_digest=walk.batch.coverage_digest,
            quote_asof_ms=walk.quotes.min_asof_ms,
            quote_digest=walk.quotes.quote_digest,
        ),
    )


#: One caller per ``TICK_PHASES`` member, in that order. A phase with no
#: caller would silently never run, which is why the table is pinned exact.
_PHASE_CALLS = pin_members(
    "loop.py's phase table",
    {
        "gate": _phase_gate,
        "verify_release": _phase_verify_release,
        "fetch": _phase_fetch,
        "read_entry": _phase_read_entry,
        "coverage": _phase_coverage,
        "evaluate": _phase_evaluate,
        "candidates": _phase_candidates,
        "quotes": _phase_quotes,
        "account": _phase_account,
        "propose": _phase_propose,
    },
    TICK_PHASES,
    exact=True,
)

#: Which gate refused -> the status §6 records for it.
_GATE_STATUSES = {
    "breaker": _SKIPPED_HALTED,
    "calendar": _SKIPPED_CLOSED,
    "health": _SKIPPED_DEGRADED,
    "venue_skew": _SKIPPED_SKEW,
}

#: A feed status the ladder says not to decide from -> the tick's status.
#: `live` and `degraded` are absent: both decide.
_FEED_STATUSES = {"stale": _SKIPPED_STALE, "dead": _REFUSED, "closed": _SKIPPED_CLOSED}

#: A phase whose `ProductionError` means something more specific than
#: `refused`. `coverage` refuses only on a gap, by construction (D6).
_PHASE_REFUSALS = {"coverage": _SKIPPED_NO_COVERAGE}


class Tick:
    """One scheduled instant: ten phases, then one leg per proposal (§5.13).

    A concrete template-method class, not an ABC — nothing subclasses it in
    core, and the invariant it carries is the ORDER ``run`` walks, not the
    abstractness of its steps. ``reduction_cycle`` is how
    ``execute-flatten``'s stored ``ReductionPlan`` reaches the tick: its
    signed candidates are contributed before ``account`` runs, so their
    scope keys are in the requirement union and their guards find the
    evidence they demand.

    Parameters
    ----------
    document : ServeDocument
        The graded serve document; §4.1 rules that code holds no threshold.
    release : ReleaseManifest
        The release being served, re-verified every tick (D24).
    schedule, data, decision, safety, execution, recording, observability
        The seven bundles of §5.16, in that order.
    tick_id : str
        Allocated by the loop from ``recording.id_source`` BEFORE the
        ``tick_start`` record (D20).
    reduction_cycle : ReductionPlan or None, optional
        The stored plan for a reduction cycle; ``None`` for a model tick.

    Raises
    ------
    ProductionError
        From ``__init_subclass__``, when a subclass overrides ``run``.

    Examples
    --------
    ::

        tick = Tick(document, release, *bundles, tick_id)
        result = tick.run(tick_at_ms)
        result.status  # 'decided'
    """

    def __init__(self, document, release, schedule, data, decision, safety, execution,
                 recording, observability, tick_id, reduction_cycle=None):
        self.document = document
        self.release = release
        self.schedule = schedule
        self.data = data
        self.decision = decision
        self.safety = safety
        self.execution = execution
        self.recording = recording
        self.observability = observability
        self.tick_id = tick_id
        self.reduction_cycle = reduction_cycle
        self._cycle = (
            _ModelCycle() if reduction_cycle is None else _ReductionCycle(reduction_cycle)
        )

    def __init_subclass__(cls, **kwargs):
        """Refuse a subclass that replaces the walk; every PHASE stays overridable."""
        super().__init_subclass__(**kwargs)
        if "run" in vars(cls):
            raise ProductionError(
                [
                    f"{cls.__name__} overrides run, which is final (§5.13): a tick's phases "
                    "are the seam, their order is not — override a phase instead"
                ]
            )

    # -- the walk -----------------------------------------------------------

    def run(self, tick_at_ms):
        """Walk ``vocab.TICK_PHASES`` in order, then run one leg per proposal.

        Parameters
        ----------
        tick_at_ms : int
            The grid instant this tick is for.

        Returns
        -------
        TickResult
            Always — a refusal is a value, and only the loop decides what
            to do with it.
        """
        walk = _Walk(tick_at_ms=tick_at_ms, latency=dict.fromkeys(TICK_PHASES, 0))
        try:
            self._phases(walk)
            self._legs(walk)
        except _Refused as refusal:
            walk.status, walk.reason = refusal.status, redact(refusal.reason)
        except ProductionError as exc:
            walk.status, walk.reason = _REFUSED, redact(str(exc))
        except Exception as exc:  # noqa: BLE001 - a failed tick is a recorded fact
            walk.status = _FAILED
            walk.error = {"class": type(exc).__name__, "text": redact(str(exc))}
        return self._result(walk)

    def _phases(self, walk):
        """Call the ten phases in order, timing each into ``latency_ms``."""
        clock = self.schedule.clock
        for phase in TICK_PHASES:
            started = clock.monotonic()
            try:
                _PHASE_CALLS[phase](self, walk)
            except ProductionError as exc:
                raise _Refused(_PHASE_REFUSALS.get(phase, _REFUSED), str(exc)) from exc
            finally:
                walk.latency[phase] = int((clock.monotonic() - started) * _MS_PER_S)

    def _legs(self, walk):
        """Run one ``LegPipeline`` per proposal; an ambiguous outcome stops the rest."""
        state = TickState(
            view=self.recording.state.snapshot(),
            account=walk.account,
            feed_status=walk.feed.status,
            feed_ages=walk.ages,
            calendar=self.schedule.calendar,
            entry_batch=walk.batch,
        )
        for index, proposal in enumerate(walk.proposals):
            result = LegPipeline(
                self.document,
                self.release,
                self._bindings(walk, state, proposal, index),
                self.schedule,
                self.decision,
                self.safety,
                self.execution,
                self.recording,
                self.observability,
            ).run()
            self._fold_leg(walk, result)
            if result.result == _AMBIGUOUS:
                _LOG.warning("leg %s is %s; later legs stop until reconciliation",
                             result.leg_id, _AMBIGUOUS)
                return

    def _bindings(self, walk, state, proposal, index):
        """Assemble one leg's thirteen bindings (§5.13.1)."""
        origin, reduction = self._cycle.binding(self, proposal, state.view)
        return LegBindings(
            proposal=proposal,
            origin=origin,
            entry_batch=walk.batch,
            head_digest=walk.head_digest,
            quotes=walk.quotes,
            state=state,
            requirements=walk.requirements,
            reduction=reduction,
            release=self.release,
            rung=self.document.rung,
            tick_id=self.tick_id,
            leg_id=self.recording.id_source.leg_id(self.tick_id, index),
            leg_index=index,
        )

    def _fold_leg(self, walk, result):
        """Fold one ``LegResult`` into the tick's decision, findings and spans."""
        walk.legs.append(_leg_entry(result))
        walk.plan_ids.append(result.plan_id)
        walk.findings.extend(result.findings)
        for bucket, elapsed in (result.leg_latency_ms or {}).items():
            walk.leg_latency[bucket] = walk.leg_latency.get(bucket, 0) + elapsed

    def _result(self, walk):
        """Assemble the one ``TickResult`` the phases produced (§5.16)."""
        return TickResult(
            tick_id=self.tick_id,
            status=walk.status,
            data_asof_ms=None if walk.batch is None else walk.batch.data_asof_ms,
            coverage_digest=None if walk.batch is None else walk.batch.coverage_digest,
            inputs_digest=None if walk.batch is None else walk.batch.inputs_digest,
            decision_plan_ids=tuple(walk.plan_ids),
            legs=tuple(walk.legs),
            findings=tuple(walk.findings),
            observed_at_ms=self.schedule.clock.now_ms(),
            nav=walk.nav,
            latency_ms=walk.latency,
            leg_latency_ms=walk.leg_latency,
            refusal_reason=walk.reason,
            error=walk.error,
            feed=_feed_block(walk),
        )

    # -- the ten phases -----------------------------------------------------

    def gate(self, tick_at_ms):
        """Answer whether this tick may run at all (§5.6, §5.11, D12).

        The gate is FIRST so a shut market, a halted series, a degraded
        process or a drifted venue clock costs no acquisition. The HALT
        sentinel is re-checked here because §5.6 puts it at every tick
        boundary and stopping must not depend on the decision path.

        Parameters
        ----------
        tick_at_ms : int

        Returns
        -------
        GateResult
            ``passed`` with gate ``"gate"``, or the first gate that
            refused, whose name selects the §6 status.
        """
        for gate, check in _GATE_CHECKS.items():
            passed, reason = check(self, tick_at_ms)
            if not passed:
                return _gate_result(gate, False, reason, tick_at_ms)
        return _gate_result(_PASSED_GATE, True, "", tick_at_ms)

    def _breaker_open(self, tick_at_ms):
        """Whether the series is neither halted nor kill-switched (§5.6, D12)."""
        view = self.recording.state.snapshot()
        if self.safety.breaker.current(view) == _HALTED_BREAKER:
            return False, "the series breaker is halted"
        if self.safety.breaker.halt_sentinel_present():
            return False, "the HALT sentinel is present"
        return True, ""

    def _calendar_open(self, tick_at_ms):
        """Whether the calendar is open at this instant."""
        if self.schedule.calendar.is_open(tick_at_ms):
            return True, ""
        return False, "the calendar is closed at this instant"

    def _health_ready(self, tick_at_ms):
        """Whether health permits acting (§5.11)."""
        state = self.observability.health.state
        if state == _READY_HEALTH:
            return True, ""
        return False, f"health is {state!r}, not {_READY_HEALTH!r}"

    def _skew(self, tick_at_ms):
        """Whether the venue's clock is inside ``schedule.max_venue_skew_ms``."""
        bound = self.document.schedule.max_venue_skew_ms
        if bound is None:
            return True, ""
        venue = self.execution.executor.venue_time_ms()
        if venue is None:
            return True, ""
        skew = abs(venue - self.schedule.clock.now_ms())
        return skew <= bound, f"venue clock is {skew} ms away (max {bound})"

    def verify_release(self):
        """Re-verify content hashes, artifact age and the runtime fingerprint (D24).

        Returns
        -------
        None

        Raises
        ------
        ProductionError
            On drift, a missing or expired artifact, or a runtime that no
            longer matches the release — each of which requires a new
            release and a new arm, never a quiet continuation.
        """
        verify_release(
            self.release,
            self.document.serving.run_dir,
            self.schedule.clock.now_ms(),
            parse_iso_duration(self.document.serving.max_artifact_age or DEFAULT_MAX_ARTIFACT_AGE),
        )

    def fetch(self, tick_at_ms):
        """Pull the feed for this tick.

        Parameters
        ----------
        tick_at_ms : int

        Returns
        -------
        FeedResult
            Its ``status`` is the §5.2 freshness ladder's answer.
        """
        return self.data.feed.pull(tick_at_ms)

    def read_entry(self, tick_at_ms):
        """Execute the entry once and freeze what it read.

        Parameters
        ----------
        tick_at_ms : int

        Returns
        -------
        EntryBatch
            The frozen batch the plan, intent and permit all bind, and the
            verifier rehashes without rereading a row.
        """
        return self.data.decider.read_entry(tick_at_ms)

    def coverage(self, batch):
        """Prove exact uniform coverage and take every required key's age (D6).

        Parameters
        ----------
        batch : EntryBatch

        Returns
        -------
        tuple of FeedAge
            One per required key: ``clock.now_ms()`` minus its watermark.
            They are returned because ``feed_age_ms`` is a registered
            measure and nothing else computes them.

        Raises
        ------
        ProductionError
            On a missing, duplicate or extra key — one fresh instrument
            must never hide a stale input.
        """
        required = set(self.release.feed_spec["required_keys"])
        covered = set(batch.watermarks_by_key)
        problems = []
        if covered - required:
            problems.append(f"the batch covers key(s) the release does not require: "
                            f"{sorted(covered - required)}")
        if required - covered:
            problems.append(f"the batch is missing required key(s) {sorted(required - covered)}")
        if problems:
            raise ProductionError(problems)
        now = self.schedule.clock.now_ms()
        return tuple(
            FeedAge(
                key=key,
                age_ms=now - batch.watermarks_by_key[key].latest_asof_ms,
                watermark_ms=batch.watermarks_by_key[key].latest_asof_ms,
            )
            for key in sorted(covered)
        )

    def evaluate(self, batch):
        """Run the served subgraph on the frozen batch.

        Parameters
        ----------
        batch : EntryBatch

        Returns
        -------
        tuple
            ``(head_outputs, head_digest)`` — the digest is RETURNED, not
            reconstructed, because the outputs never leave the tick and
            ``DecisionPlan.provenance_digests`` requires it.
        """
        return self.data.decider.evaluate(batch)

    def candidates(self, head_outputs):
        """Return this tick's candidate set.

        Parameters
        ----------
        head_outputs : dict

        Returns
        -------
        tuple of Candidate
            The proposer's for a model tick; the plan's signed candidates
            for a reduction cycle.
        """
        return self._cycle.candidates(self, head_outputs)

    def quotes(self, head_outputs):
        """Return the quotes every proposal, plan and permit binds.

        The proposer EXTRACTS quotes — §5.3 declares
        ``Proposer.quotes(head_outputs) -> list[Quote]``, pure and
        state-independent. Assembling them into §5.13's ``QuoteSet`` is the
        TICK's job, because the digest and the oldest instant are tick
        facts: ``DecisionPlan``, ``Intent`` and ``ActPermit`` each bind
        both, and a leg could not rebuild them from its own proposal.

        Parameters
        ----------
        head_outputs : dict

        Returns
        -------
        QuoteSet
            Empty when no head row is quote-shaped; its ``min_asof_ms`` is
            then this instant, since a set with no quote has nothing that
            can be stale — and nothing prices against it either, so an
            executor answers ``no_quote`` and accounting refuses a held
            instrument with no mark.
        """
        found = tuple(self.data.decider.proposer.quotes(head_outputs))
        return QuoteSet(
            quotes=found,
            quote_digest=canonical_hash([quote.to_obj() for quote in found]),
            min_asof_ms=min(
                (quote.asof_ms for quote in found), default=self.schedule.clock.now_ms()
            ),
        )

    def account(self, candidates, quotes, at_ms):
        """Snapshot the account against the whole tick's evidence requirements.

        Parameters
        ----------
        candidates : tuple of Candidate
        quotes : QuoteSet
        at_ms : int

        Returns
        -------
        tuple
            ``(AccountState, tuple[EvidenceRequirement])`` — the union is
            returned because ``LegPipeline.refresh`` must call
            ``Accounting.snapshot`` with it and cannot rebuild it from one
            proposal.
        """
        requirements = self.decision.guards.requirements(
            candidates, at_ms, self.schedule.calendar
        )
        account = self.execution.accounting.snapshot(
            self.recording.state.snapshot(),
            self.execution.executor,
            quotes,
            at_ms,
            requirements,
            self.schedule.calendar,
        )
        return account, tuple(requirements)

    def propose(self, head_outputs, candidates, account, provenance):
        """Return the proposals this tick runs, in the order it runs them.

        Parameters
        ----------
        head_outputs : dict
        candidates : tuple of Candidate
        account : AccountState
        provenance : Provenance

        Returns
        -------
        tuple of Proposal
            Model proposals sorted by stable candidate id; a reduction
            cycle's in the maker-approved index order.
        """
        return self._cycle.proposals(self, head_outputs, candidates, account, provenance)


#: A leg result the venue could not answer for; it stops every later leg.
_AMBIGUOUS = "unknown"

#: The gate name a tick that passed every check reports.
_PASSED_GATE = "gate"

#: The gate checks, in the order they are asked. Each answers
#: ``(passed, reason)`` and is only asked when every earlier one passed,
#: so a halted tick touches no collaborator beyond the breaker.
_GATE_CHECKS = {
    "breaker": Tick._breaker_open,
    "calendar": Tick._calendar_open,
    "health": Tick._health_ready,
    "venue_skew": Tick._skew,
}
if set(_GATE_CHECKS) != set(_GATE_STATUSES):
    raise ProductionError(["loop.py: every gate check owes a §6 tick status"])


def _gate_result(gate, passed, reason, at_ms):
    """One `GateResult`, built where the gate is judged."""
    from dskit.production.records import GateResult

    return GateResult(gate=gate, passed=passed, reason=reason, at_ms=at_ms)


def _feed_block(walk):
    """Return the §6 seven-member feed block, or the unfetched placeholder."""
    if walk.feed is None:
        return _unfetched_feed()
    batch = walk.batch
    return {
        "status": walk.feed.status,
        "acq_id": walk.feed.acq_id,
        "records_added": walk.feed.records_added,
        "source_config_hash": walk.feed.source_config_hash,
        "required_keys_digest": None if batch is None else batch.required_keys_digest,
        "watermarks_by_key": (
            None
            if batch is None
            else {key: mark.latest_asof_ms for key, mark in batch.watermarks_by_key.items()}
        ),
        "coverage_digest": None if batch is None else batch.coverage_digest,
    }


def _leg_entry(result):
    """§6's `decision.legs[]` entry, written from one `LegResult`."""
    proposal = result.final.to_obj()
    return {
        "leg_id": result.leg_id,
        "instrument": proposal["instrument"],
        "prediction": proposal["prediction"],
        "confidence": proposal["confidence"],
        "baseline": proposal["baseline"],
        "expected_value": proposal["expected_value"],
        "reference_price": proposal["reference_price"],
        "proposal": proposal,
        "findings": [finding.to_obj() for finding in result.findings],
        "final": proposal["side"],
        "client_ref": result.client_ref,
    }


# ---------------------------------------------------------------------------
# ServeLoop
# ---------------------------------------------------------------------------


class _Exit(Exception):
    """A deliberate, handled end to the process, carrying its exit code."""

    def __init__(self, code, state, reason):
        super().__init__(reason)
        self.code = code
        self.state = state
        self.reason = reason


class ServeLoop:
    """The scheduler: lifecycle, cadence, control, observation and the exit code (§5.13).

    It is NOT the composition root — ``compose.bundles_for`` already chose
    every object it holds, so there is no rung to ask and no mode to
    branch on — and it contains no submission sequence: the eight steps
    where money leaves the process are ``LegPipeline``'s.

    Parameters
    ----------
    document : ServeDocument
    release : ReleaseManifest
    schedule, data, decision, safety, execution, recording, observability
        The seven bundles of §5.16, in that order.
    lock : health.InstanceLock, optional
        The single-instance lock. R18 makes it the writer lock too, so the
        loop takes it (idempotently, when the caller already holds it)
        before anything else and answers ``already_running`` when another
        process has it.
    process_id : str, optional
        The id the caller also composed the ledger with. D22's journal
        row renders it into ``notes``, and the loop cannot read it back
        off the chain: scanning the ledger belongs to the fold and its
        named readers (§5.8.1).

    Attributes
    ----------
    state : str
        A ``vocab.LOOP_STATES`` member; ``stopped``, ``halted`` or
        ``faulted`` once ``run`` returns.

    Examples
    --------
    ::

        loop = ServeLoop(document, release, *bundles, lock=lock)
        loop.run()    # -> 0
        loop.state    # 'stopped'
    """

    def __init__(self, document, release, schedule, data, decision, safety, execution,
                 recording, observability, *, lock=None, process_id=None):
        self.document = document
        self.release = release
        self.schedule = schedule
        self.data = data
        self.decision = decision
        self.safety = safety
        self.execution = execution
        self.recording = recording
        self.observability = observability
        self._lock = lock
        self._pid = process_id
        self._state = _INIT
        self._stop_flag = threading.Event()
        self._completed = 0
        self._last_recon_ms = None
        self._last_tick_at = None
        self._permit = None
        self._cycles = []
        self._after = None
        self._processor = CommandProcessor(
            recording.inbox,
            recording.ledger,
            recording.state,
            handlers_for(document, self.bundles),
            schedule.clock,
        )
        self._metrics = _LoopMetrics(observability.metrics)

    @property
    def bundles(self):
        """Return the seven bundles this loop holds, in §5.16's order (tuple)."""
        return (
            self.schedule,
            self.data,
            self.decision,
            self.safety,
            self.execution,
            self.recording,
            self.observability,
        )

    @property
    def state(self):
        """Return the lifecycle state (a ``vocab.LOOP_STATES`` member, str)."""
        return self._state

    # -- the lifecycle ------------------------------------------------------

    def run(self):
        """Serve until the invocation, an operator or a refusal ends it.

        Returns
        -------
        int
            A ``vocab.EXIT_CODES`` value: 0 stopped, 1 error, 3 halted,
            4 already running, 5 refused.
        """
        try:
            self._acquire()
        except ProductionError as exc:
            _LOG.error("cannot take the instance lock: %s", redact(str(exc)))
            return EXIT_CODES["already_running"]
        try:
            self._start()
            self._serve()
            return self._finish()
        except _Exit as ending:
            return self._close(ending.code, ending.state, ending.reason)
        except Exception as exc:  # noqa: BLE001 - a fault is recorded, not raised at a caller
            _LOG.exception("serve faulted: %s", redact(str(exc)))
            return self._close(EXIT_CODES["error"], _FAULTED, redact(str(exc)))

    def _acquire(self):
        """Take the one flock on ``serve.lock`` before anything writes (R18)."""
        if self._lock is not None:
            self._lock.acquire()
        self._state = _LOCKED

    def _start(self):
        """Lease, recover, announce, reconcile and gate readiness — in that order."""
        self._lease()
        self._recover()
        self._announce()
        self._reconcile_on_start()
        self._gate_readiness()
        self._state = _READY
        self.observability.heartbeat.start()

    def _lease(self):
        """Take the venue/account lease this process acts under (§5.7.2)."""
        self._permit = self.execution.lease.acquire(
            self.document.coordination.scope,
            f"{self.release.release_hash}/{self._process_id()}",
            self.document.coordination.ttl_ms,
        )
        self._state = _LEASED

    def _recover(self):
        """Replay the fold and close what a crash left open, before anything appends."""
        self._state = _RECONCILING
        report = Recovery(
            self.recording.ledger,
            self.recording.state,
            self.recording.id_source,
            self.execution.executor,
        ).run(self.schedule.clock)
        _LOG.info("recovered %d record(s), closed %d tick(s)",
                  report.replayed, len(report.closed_ticks))

    def _announce(self):
        """Append and barrier this process's §6 ``process`` start record (D24)."""
        self._append("process", f"start:{self._process_id()}", self._process_body("start"))
        self.recording.ledger.barrier()

    def _reconcile_on_start(self):
        """Reconcile before READY when the document says to; mismatches halt (D13)."""
        now = self.schedule.clock.now_ms()
        if not self.recording.reconciler.due(now, self._last_recon_ms):
            return
        report = self.recording.reconciler.run(
            self.recording.state.snapshot(),
            self.execution.executor,
            self.document.coordination.scope,
        )
        self._last_recon_ms = now
        if self.recording.reconciler.apply_policy(report) == _RECON_HALTS:
            self.safety.breaker.trip(_RECONCILE_TRIP, SERVE_VERB)

    def _gate_readiness(self):
        """Refuse a live serve without a current release-bound GO (§5.13).

        The rung is not asked here. ``ActionPolicy`` owns the rung axis, so
        the loop puts a maximally permissive submit to it with the real
        readiness verdict: only the readiness rule can refuse such a
        request, and it refuses only where a GO is required.
        """
        view = self.recording.state.snapshot()
        at_ms = self.schedule.clock.now_ms()
        verdict = self.safety.readiness.verdict_for(view, at_ms)
        probe = PolicyRequest(
            operation="submit",
            risk_effect="increase",
            rung=self.document.rung,
            breaker="active",
            health=_READY_HEALTH,
            readiness=verdict,
            authority="ordinary",
            origin=_ModelCycle.ORIGIN,
            pending_control=False,
        )
        decision = self.safety.action_policy.permits(probe)
        if not decision.allowed:
            raise _Exit(
                EXIT_CODES["refused"], _STOPPED, f"readiness {verdict}: {decision.reason}"
            )

    # -- the schedule -------------------------------------------------------

    def _serve(self):
        """Tick on the cadence's grid until the invocation or an operator ends it."""
        with SignalHandler(self._stop_flag):
            self._after = self.schedule.clock.now_ms() - 1
            while not self._stopping():
                due = self._due()
                if due is None:
                    _LOG.info("the calendar never reopens; stopping")
                    return
                if not due:
                    return
                tick_at, absorbed = self.schedule.overrun.resolve(
                    due, self.schedule.clock.now_ms()
                )
                if tick_at is None:
                    _LOG.warning("every due tick was dropped: %d instant(s) too stale",
                                 len(absorbed))
                    continue
                self._tick(tick_at, tuple(absorbed))
                self._completed += 1
                for plan in self._drain_cycles():
                    self._tick(tick_at, (), reduction_cycle=plan)

    def _due(self):
        """Return every grid instant now due, waiting for the next when none is.

        A tick that ran long leaves a BACKLOG, and ``Overrun`` is the one
        owner of what happens to it — so the whole backlog is collected
        before it is asked, never one instant at a time.

        Returns
        -------
        tuple or None
            The due instants in ascending order; empty when a signal ended
            the wait, and ``None`` when the calendar never reopens.
        """
        due = []
        nxt = self.schedule.cadence.next_tick(self._after, self.schedule.calendar)
        while nxt is not None and nxt <= self.schedule.clock.now_ms():
            due.append(nxt)
            self._after = nxt
            nxt = self.schedule.cadence.next_tick(self._after, self.schedule.calendar)
        if due:
            return tuple(due)
        if nxt is None:
            return None
        self._after = nxt
        self._state = _WAITING
        self.schedule.clock.sleep_until(nxt, self._stop_flag.is_set)
        return () if self._stop_flag.is_set() else (nxt,)

    def _stopping(self):
        """Whether the invocation's bound or a signal has ended the serve."""
        if self._stop_flag.is_set():
            return True
        invocation = self.safety.invocation
        limit = invocation.max_ticks if invocation.max_ticks is not None else (
            1 if invocation.once else None
        )
        return limit is not None and self._completed >= limit

    def _drain_cycles(self):
        """Return and clear the reduction plans a consumed command queued."""
        cycles, self._cycles = tuple(self._cycles), []
        return cycles

    # -- one tick -----------------------------------------------------------

    def _tick(self, tick_at_ms, absorbed, reduction_cycle=None):
        """Run one tick: start record, phases, decision, tick, observe, checkpoint."""
        self._state = _TICKING
        self._before_tick(tick_at_ms)
        tick_id = self.recording.id_source.next_tick_id(tick_at_ms)
        self._append("tick_start", tick_id, TickStart(
            tick_id=tick_id, tick_at_ms=tick_at_ms, release_hash=self.release.release_hash
        ).to_obj())
        self.recording.ledger.barrier()
        result = Tick(
            self.document,
            self.release,
            *self.bundles,
            tick_id,
            reduction_cycle=reduction_cycle,
        ).run(tick_at_ms)
        decision_body, tick_body = self._bodies(result, tick_at_ms, absorbed)
        self._append("decision", tick_id, decision_body)
        self._append("tick", tick_id, tick_body)
        self.recording.ledger.barrier()
        self._last_tick_at = tick_at_ms
        self._after_tick(result, decision_body, tick_body)

    def _before_tick(self, tick_at_ms):
        """Consume the control inbox, re-check the kill switch and evaluate health."""
        self.observability.health.evaluate(self.schedule.clock.now_ms())
        self._check_sentinel()
        pending = {command["request_id"]: command for command in self.recording.inbox.pending()}
        for receipt in self._processor.process_pending(self.recording.state.snapshot()):
            command = pending.get(receipt["request_id"])
            if (
                receipt["status"] == "applied"
                and command is not None
                and command["purpose"] in EXECUTING_PURPOSES
            ):
                self._queue_cycle(command)

    def _check_sentinel(self):
        """§5.6: the HALT sentinel is re-checked at every tick boundary."""
        if not self.safety.breaker.halt_sentinel_present():
            return
        view = self.recording.state.snapshot()
        if self.safety.breaker.current(view) != _HALTED_BREAKER:
            self.safety.breaker.trip(_OPERATOR_TRIP, SERVE_VERB, cause=_HALT_CAUSE)

    def _queue_cycle(self, command):
        """Remember the reduction plan an applied ``execute_flatten`` queued (§5.8)."""
        stored = command["payload"].get("plan")
        if stored is not None:
            self._cycles.append(ReductionPlan.from_obj(stored))

    def _bodies(self, result, tick_at_ms, absorbed):
        """Return the §6 ``decision`` and ``tick`` bodies for one tick result."""
        view = self.recording.state.snapshot()
        decision = {
            "tick_id": result.tick_id,
            "decision_plan_ids": list(result.decision_plan_ids),
            "decision_plan_digests": [],
            "legs": [dict(leg) for leg in result.legs],
        }
        tick = {
            "tick_id": result.tick_id,
            "tick_at": tick_at_ms,
            "data_asof_ms": result.data_asof_ms,
            "observed_at_ms": result.observed_at_ms,
            "status": result.status,
            "feed": _recorded_feed(result),
            "inputs_digest": result.inputs_digest,
            "nav": None if result.nav is None else str(result.nav),
            "calendar": {
                "tz": self.schedule.calendar.tz_name,
                "open": bool(self.schedule.calendar.is_open(tick_at_ms)),
            },
            "overrun_absorbed": [int(instant) for instant in absorbed],
            "latency_ms": dict(result.latency_ms),
            "leg_latency_ms": dict(result.leg_latency_ms),
            "health": self.observability.health.state,
            "breaker": self.safety.breaker.current(view),
            "rung": self.document.rung,
            "refusal_reason": result.refusal_reason,
            "error": result.error,
        }
        return decision, tick

    def _after_tick(self, result, decision_body, tick_body):
        """Observe, alert, flush, beat and checkpoint — in that order, LAST (D13)."""
        self._observe(result, decision_body, tick_body)
        self.observability.alerts.process(self.schedule.clock.now_ms())
        self.observability.metrics.flush(self.schedule.clock.now_ms(), result.tick_id)
        self.observability.heartbeat.note_tick_completed(self.schedule.clock.monotonic())
        self._checkpoint(result)
        self._metrics.tick(result)

    def _observe(self, result, decision_body, tick_body):
        """Feed both appended bodies to every monitor and record what changed (§5.10)."""
        for name, monitor in (self.decision.monitors or {}).items():
            monitor.observe(decision_body)
            monitor.observe(tick_body)
            verdict = monitor.verdict()
            body = {"monitor": name, **verdict.to_obj()}
            self._append("monitor", f"{name}:{result.tick_id}", body)
            self._metrics.monitor(name, verdict.status)
            if monitor.should_trip():
                self.safety.breaker.trip(_MONITOR_TRIP, f"monitors.{name}")

    def _checkpoint(self, result):
        """Snapshot the fold with every monitor's own state, then write the cache LAST.

        §6 requires the ``snapshot`` record to carry monitor state, which
        is not a ``StateView`` member: dropping it would reset every drift
        window on restart, and a monitor below ``min_n`` cannot alarm until
        it refills. The fold cannot derive it — only the live monitors
        hold their windows — so the loop is what puts it there.
        """
        payload = dict(self.recording.state.to_snapshot_obj())
        payload["monitor_state"] = {
            name: monitor.state() for name, monitor in (self.decision.monitors or {}).items()
        }
        self.recording.ledger.snapshot(payload)
        self.recording.ledger.barrier()
        self._write_checkpoint(result.observed_at_ms)

    def _write_checkpoint(self, positions_snapshot_at):
        """Replace the cache with one bound to the head it projects (§5.8)."""
        head_seq, head_hash = self.recording.ledger.head()
        view = self.recording.state.snapshot()
        Checkpoint(
            release_hash=self.release.release_hash,
            last_tick_at=self._last_tick_at,
            last_completed_tick_at=self._last_tick_at,
            pending=tuple(view.pending),
            positions_snapshot_at=positions_snapshot_at,
            schema_version=1,
            head_seq=head_seq,
            head_hash=head_hash,
        ).write(self.recording.inbox.serve_root.checkpoint_cache)

    # -- stopping -----------------------------------------------------------

    def _finish(self):
        """Return the exit code a completed serve earned: halted, or stopped."""
        view = self.recording.state.snapshot()
        halted = self.safety.breaker.current(view) == _HALTED_BREAKER
        code = EXIT_CODES["halted"] if halted else EXIT_CODES["stopped"]
        return self._close(code, _HALTED if halted else _STOPPED, "")

    def _close(self, code, state, reason):
        """Append the ``process`` stop record, then D22's one journal row (D15).

        The checkpoint is replaced AFTER the observability workers stop,
        because stopping them appends: ``Health.stop`` writes the deferred
        ``health`` record D23 keeps off the worker thread. Writing the cache
        before that would leave every cleanly stopped series' checkpoint one
        record behind its own head — "checkpoint last" (D13) means last.
        """
        self._state = _STOPPING
        self._release_lease()
        try:
            self._append("process", f"stop:{self._process_id()}", self._process_body("stop", code))
            self.recording.ledger.barrier()
        except Exception as exc:  # noqa: BLE001 - a chain we cannot extend is still a stop
            _LOG.error("could not record the process stop: %s", redact(str(exc)))
        self.observability.health.stop()
        self.observability.heartbeat.close()
        self.observability.alerts.close()
        try:
            self._write_checkpoint(self._last_tick_at)
        except Exception as exc:  # noqa: BLE001 - a cache we cannot replace is still a stop
            _LOG.error("could not replace the checkpoint: %s", redact(str(exc)))
        if state is not _FAULTED:
            self._journal(code)
        self._state = state
        if reason:
            _LOG.info("serve stopping: %s", reason)
        return code

    def _release_lease(self):
        """Give the venue/account lease back; a failure never changes the exit code."""
        if self._permit is None:
            return
        try:
            self.execution.lease.release(self._permit)
        except Exception as exc:  # noqa: BLE001 - releasing is best effort
            _LOG.warning("could not release the lease: %s", redact(str(exc)))
        self._permit = None

    def _journal(self, code):
        """Write D22's one production row, rendering the process id and final head.

        The journal's field set is fixed and this plan adds none, so the
        two facts that have no column are rendered into ``notes`` in the
        one documented form ``verify`` parses back. The head is read AFTER
        the stop record's barrier — the ledger never claims its own final
        hash.
        """
        seq, head_hash = self.recording.ledger.head()
        try:
            self.recording.journal_hook(
                # Every field is a STRING: the journal's `Action` row refuses
                # anything else (`dskit/journal/model.py`), and a hook that
                # raised would lose D22's one row for the whole process —
                # silently, since a journal failure never changes the exit.
                step=JOURNAL_STEP.format(verb=SERVE_VERB, rung=self.document.rung),
                inputs=self.release.release_hash,
                outputs=str(code),
                db_location=self.recording.inbox.serve_root.series_path,
                notes=JOURNAL_NOTES.format(
                    process_id=self._process_id(), seq=seq, head_hash=head_hash
                ),
            )
        except Exception as exc:  # noqa: BLE001 - the ledger is authoritative, not the journal
            _LOG.error("could not write the journal row: %s", redact(str(exc)))

    # -- shared -------------------------------------------------------------

    def _append(self, kind, semantic_id, body):
        """Append one record, its id qualified by its kind (R9)."""
        return self.recording.ledger.append(
            {"kind": kind, "id": f"{kind}:{semantic_id}", "body": body}
        )

    def _process_id(self):
        """Return this process's id — the one the caller also gave the ledger.

        It is a caller fact, not a ledger read: the loop is not one of
        the modules that may scan the chain (§5.8.1), and D22's anchor
        has to name the id ``__main__`` composed the ledger with.
        """
        return self._pid or ""

    def _process_body(self, event, exit_code=None):
        """Return the §6 ``process`` body: D24's whole content-and-runtime binding."""
        manifest = self.release.to_obj()
        body = {
            "event": event,
            "series_id": self.document.series_id,
            "release_hash": self.release.release_hash,
            "doc_hash": manifest["doc_hash"],
            "serving_hash": manifest["serving_hash"],
            "run_hash": manifest["run_hash"],
            "artifact_digests": {
                name: entry["digest"] for name, entry in manifest["artifacts"].items()
            },
            "source_config_hash": manifest["source_config"]["hash"],
            "runtime_fingerprint": manifest["runtime_fingerprint"],
            "rung": self.document.rung,
            "executor_kind": self.document.execution.uses,
            "code_version": manifest["adapter"]["digest"],
        }
        if exit_code is not None:
            body["exit_code"] = exit_code
        return body


class _LoopMetrics:
    """The counters the loop owns, declared once from ``vocab``'s closed table."""

    def __init__(self, metrics):
        self._ticks = metrics.counter("ticks_total", labels=_labels("ticks_total"))
        self._phase = metrics.histogram("tick_seconds", labels=_labels("tick_seconds"))
        self._refusals = metrics.counter("refusals_total", labels=_labels("refusals_total"))
        self._verdicts = metrics.counter(
            "monitor_verdicts_total", labels=_labels("monitor_verdicts_total")
        )
        self._refusal_reasons = set(METRIC_LABEL_VALUES["refusals_total"]["reason"])

    def tick(self, result):
        """Count one completed tick, its phases and its refusal, if it refused."""
        self._ticks.inc(status=result.status)
        for phase, elapsed in result.latency_ms.items():
            self._phase.observe(elapsed / _MS_PER_S, phase=phase)
        if result.status in self._refusal_reasons:
            self._refusals.inc(reason=result.status)

    def monitor(self, name, status):
        """Count one monitor verdict."""
        self._verdicts.inc(monitor=name, status=status)


def _labels(name):
    """Return one metric's declared label NAMES, from ``vocab``'s closed table."""
    return tuple(sorted(METRIC_LABEL_VALUES[name]))


def _recorded_feed(result):
    """Return the §6 ``feed`` block, or ``null`` when the fetch never completed."""
    return None if result.feed == _unfetched_feed() else dict(result.feed)


