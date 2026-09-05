"""`loop.py` — `Tick`'s fixed phase walk and `ServeLoop`'s lifecycle (§5.13).

Two objects, two contracts.

`Tick.run` is concrete and FINAL: it walks `vocab.TICK_PHASES` in order,
times each into `latency_ms`, assembles the `TickState` every guard
receives and the `LegBindings` every leg is bound to, and returns a
`TickResult`. No subclass may reorder or skip a phase, which is what
makes D23's fixed phase order and §6's pinned latency keys structural
rather than advisory.

`ServeLoop` is the SCHEDULER, not the composition root (§5.13.1): the
lifecycle FSM, cadence and overrun, control-inbox consumption, `Tick`
construction, monitor `observe`, metrics flush, checkpoint, the D22
journal row and the exit code. It contains no submission sequence and
asks no rung.

The ledger, its fold, the control spool and the checkpoint are REAL here
— they are what "record before act, checkpoint last" is a claim about.
Everything a tick decides FROM (feed, decider, guards, executor,
accounting, authorities) is a fake, because those seams have their own
suites and a loop test that also exercised them would not say which one
broke. `LegPipeline` is faked at the one seam `Tick` constructs it
through, so the loop's leg ORCHESTRATION — order, refusal propagation,
bindings — is what is under test rather than the leg's eight steps.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import pathlib
from decimal import Decimal

import pytest

import dskit.production.loop as loop_module
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.bundles import (
    Data,
    Decision,
    Execution,
    Invocation,
    Observability,
    Recording,
    Safety,
    Schedule,
)
from dskit.production.cadence import FixedInterval, Overrun
from dskit.production.health import InstanceLock
from dskit.production.ids import ReleaseIdSource
from dskit.production.ledger import Checkpoint, JsonlLedger, ServeRoot
from dskit.production.leg import LegResult
from dskit.production.loop import ServeLoop, Tick
from dskit.production.policy import ActionPolicy, TransitionPolicy
from dskit.production.records import (
    AccountState,
    Candidate,
    EntryBatch,
    ExecutionScope,
    FeedAge,
    FeedResult,
    Finding,
    InputWatermark,
    Proposal,
    Quote,
    QuoteSet,
    ReductionIntent,
    ReductionPlan,
    RiskVersion,
    TickResult,
)
from dskit.production.sessions import AlwaysOpen
from dskit.production.state import SeriesState, TickState
from dskit.production.vocab import (
    EXIT_CODES,
    LEG_LATENCY_BUCKETS,
    LOOP_STATES,
    TICK_PHASES,
    TICK_STATUSES,
)
from tests.production.conftest import NOW_MS, UNIVERSE

PACKAGE_DIR = pathlib.Path(__file__).resolve().parents[2] / "dskit" / "production"

#: The ten phase methods and their declared signatures (§5.13), restated
#: here rather than read from `vocab` — a phase whose arguments changed
#: silently is exactly what this pins.
PHASE_SIGNATURES = {
    "gate": ("tick_at_ms",),
    "verify_release": (),
    "fetch": ("tick_at_ms",),
    "read_entry": ("tick_at_ms",),
    "coverage": ("batch",),
    "evaluate": ("batch",),
    "candidates": ("head_outputs",),
    "quotes": ("head_outputs",),
    "account": ("candidates", "quotes", "at_ms"),
    "propose": ("head_outputs", "candidates", "account", "provenance"),
}

#: The six `TickState` members `Tick.run` assembles (§5.8.1, R20).
TICK_STATE_MEMBERS = ("view", "account", "feed_status", "feed_ages", "calendar", "entry_batch")

#: The thirteen `LegBindings` members, in §5.13.1's order.
LEG_BINDING_MEMBERS = (
    "proposal",
    "origin",
    "entry_batch",
    "head_digest",
    "quotes",
    "state",
    "requirements",
    "reduction",
    "release",
    "rung",
    "tick_id",
    "leg_id",
    "leg_index",
)

RELEASE_HASH = "a" * 64


# ==========================================================================
# Fakes — everything a tick decides FROM
# ==========================================================================


class Calls:
    """An ordered log of `(name, args, kwargs)` shared by every fake."""

    def __init__(self):
        self.log = []

    def add(self, name, *args, **kwargs):
        """Record one call and return the log's length."""
        self.log.append((name, args, kwargs))
        return len(self.log)

    def names(self):
        """Every recorded name, in order."""
        return [name for name, _a, _k in self.log]

    def first(self, name):
        """The first `(args, kwargs)` recorded under `name`."""
        for recorded, args, kwargs in self.log:
            if recorded == name:
                return args, kwargs
        raise AssertionError(f"{name} was never called; saw {self.names()}")

    def count(self, name):
        """How many times `name` was called."""
        return self.names().count(name)


def a_batch(asof_ms=NOW_MS - 60_000):
    """One frozen `EntryBatch` covering the conftest universe."""
    watermarks = {
        key: InputWatermark(key=key, latest_asof_ms=asof_ms, source_digest=canonical_hash(key))
        for key in UNIVERSE
    }
    return EntryBatch(
        outputs={"records": [{"instrument": key} for key in UNIVERSE]},
        watermarks_by_key=watermarks,
        required_keys_digest=canonical_hash(list(UNIVERSE)),
        coverage_digest="c" * 64,
        data_asof_ms=asof_ms,
        inputs_digest="d" * 64,
        source_config_hash="e" * 64,
    )


def a_quote_set(asof_ms=NOW_MS - 1_000):
    """A `QuoteSet` over the universe."""
    quotes = tuple(
        Quote(
            instrument=key,
            bid=Decimal("10"),
            ask=Decimal("11"),
            mid=Decimal("10.5"),
            asof_ms=asof_ms,
        )
        for key in UNIVERSE
    )
    return QuoteSet(quotes=quotes, quote_digest="q" * 64, min_asof_ms=asof_ms)


def an_account(asof_ms=NOW_MS):
    """An `AccountState` with nothing in it but a risk version."""
    return AccountState(
        risk_version=RiskVersion(economic_seq=0, executor_token=None, accounting_tokens=None),
        asof_ms=asof_ms,
        evidence_digest="v" * 64,
        balances=(),
        positions=(),
        working=(),
        measure_evidence={},
        source_digests={},
    )


def a_proposal(candidate_id, instrument, side="buy"):
    """A proposal shaped like the conftest head's rows."""
    return Proposal(
        id=candidate_id,
        instrument=instrument,
        side=side,
        qty=Decimal(3),
        notional=Decimal(30),
        limit=None,
        tif="ioc",
        expires_ms=NOW_MS + 60_000,
        reference_price=Decimal("10.5"),
        exposure=Decimal(30),
        direction="long",
        confidence=0.6,
        prediction=0.58,
        baseline=0.5,
        expected_value=0.08,
        inputs_asof_ms=NOW_MS - 60_000,
        inputs_digest="d" * 64,
        coverage_digest="c" * 64,
        quote_asof_ms=NOW_MS - 1_000,
        quote_digest="q" * 64,
        extra={},
    )


class FakeFeed:
    """`pull(tick_at_ms) -> FeedResult`, with a settable status."""

    def __init__(self, calls, status="live"):
        self.calls = calls
        self.status = status

    def pull(self, tick_at_ms):
        self.calls.add("feed.pull", tick_at_ms)
        return FeedResult(
            status=self.status,
            acq_id="acq-1",
            records_added=4,
            source_config_hash="e" * 64,
            at_ms=tick_at_ms,
        )


class FakeProposer:
    """The `Proposer` the decider owns."""

    def __init__(self, calls, proposals=None, candidates=None):
        self.calls = calls
        self._proposals = proposals
        self._candidates = candidates

    def candidates(self, head_outputs):
        self.calls.add("proposer.candidates", head_outputs)
        if self._candidates is not None:
            return self._candidates
        return tuple(
            Candidate(id=f"cand-{key}", instrument=key, scope_keys=(key,)) for key in UNIVERSE
        )

    def quotes(self, head_outputs):
        self.calls.add("proposer.quotes", head_outputs)
        # §5.3: `Proposer.quotes(head_outputs) -> list[Quote]`. Assembling
        # them into §5.13's `QuoteSet` — with its digest and oldest instant —
        # is the TICK's job, so the fake answers what the real base answers.
        return list(a_quote_set().quotes)

    def proposals(self, head_outputs, candidates, state, provenance):
        self.calls.add("proposer.proposals", head_outputs, candidates, state, provenance)
        if self._proposals is not None:
            return self._proposals
        return tuple(a_proposal(c.id, c.instrument) for c in candidates)


class FakeDecider:
    """`read_entry` / `evaluate`, and the proposer it owns."""

    def __init__(self, calls, proposer=None, batch=None):
        self.calls = calls
        self.proposer = proposer or FakeProposer(calls)
        self._batch = batch or a_batch()
        self.serving_hash = "s" * 64

    def read_entry(self, tick_at_ms):
        self.calls.add("decider.read_entry", tick_at_ms)
        return self._batch

    def evaluate(self, batch):
        self.calls.add("decider.evaluate", batch)
        return {"picks": {"records": []}}, "h" * 64


class FakeGuards:
    """The tick only asks the chain for the evidence union."""

    def __init__(self, calls, requirements=()):
        self.calls = calls
        self._requirements = requirements

    def requirements(self, candidates, at_ms, calendar):
        self.calls.add("guards.requirements", candidates, at_ms, calendar)
        return self._requirements


class FakeAccounting:
    """`snapshot` for the evidence, `value` for the tick's `nav`."""

    def __init__(self, calls, nav=Decimal("1000"), account=None):
        self.calls = calls
        self.nav = nav
        self._account = account or an_account()

    def snapshot(self, state_view, executor, quotes, at_ms, requirements, calendar):
        self.calls.add("accounting.snapshot", at_ms, requirements)
        return dataclasses.replace(self._account, asof_ms=at_ms)

    def value(self, state_view, quotes, at_ms):
        self.calls.add("accounting.value", at_ms)
        return self.nav

    def classify(self, proposal, state):
        return "increase"


class FakeExecutor:
    """Read/query only: the loop never submits, the leg does."""

    def __init__(self, calls, venue_time_ms=None):
        self.calls = calls
        self._venue_time = venue_time_ms

    def venue_time_ms(self):
        self.calls.add("executor.venue_time_ms")
        return self._venue_time

    def execution_scope(self):
        return ExecutionScope(venue="paper", account="strategy-a")

    def order(self, ref):
        self.calls.add("executor.order", ref)
        return None

    def cancel_all(self):
        self.calls.add("executor.cancel_all")
        return ()

    def open_orders(self):
        return ()


class FakeAuthorities:
    """`for_origin(origin, breaker)` — the table `compose.py` builds."""

    def __init__(self, calls):
        self.calls = calls

    def for_origin(self, origin, breaker):
        self.calls.add("authorities.for_origin", origin, breaker)
        return object()


class FakeBreaker:
    """The breaker fold plus the HALT sentinel the loop re-checks."""

    def __init__(self, calls, state="active", sentinel=False):
        self.calls = calls
        self.state = state
        self.sentinel = sentinel

    def current(self, view):
        return self.state

    def halt_sentinel_present(self):
        self.calls.add("breaker.halt_sentinel_present")
        return self.sentinel

    def trip(self, reason, actor, control_request_id=None, principal_digest=None,
             proof_digest=None, cause="trip"):
        self.calls.add("breaker.trip", reason, actor, cause=cause)
        self.state = "halted"
        return 1


class FakeArming:
    """The loop never mints; it only expires an arm that fell due."""

    def __init__(self, calls):
        self.calls = calls

    def current(self, view, at_ms):
        return None

    def expire_if_due(self, view, at_ms):
        self.calls.add("arming.expire_if_due", at_ms)
        return None


class FakeReadiness:
    """`verdict_for` — the ONE owner of "an expired GO is a `no_go`"."""

    def __init__(self, calls, verdict="go"):
        self.calls = calls
        self.verdict = verdict

    def verdict_for(self, view, at_ms):
        self.calls.add("readiness.verdict_for", at_ms)
        return self.verdict


class FakeHealth:
    """The health state machine the loop evaluates once per tick."""

    def __init__(self, calls, state="ready"):
        self.calls = calls
        self._state = state

    @property
    def state(self):
        return self._state

    def evaluate(self, now_ms):
        self.calls.add("health.evaluate", now_ms)
        return self._state

    def can_act(self):
        return self._state == "ready"

    def can_heartbeat(self):
        return self._state != "unhealthy"

    def mark_unhealthy(self, cause, now_ms):
        self.calls.add("health.mark_unhealthy", cause, now_ms)
        self._state = "unhealthy"

    def stop(self):
        self.calls.add("health.stop")
        self._state = "stopping"


class FakeRouter:
    """`process(now_ms)` is the sole appender of the §6 `alert` record."""

    def __init__(self, calls):
        self.calls = calls

    def raise_alert(self, alert):
        self.calls.add("alerts.raise_alert", alert)
        return True

    def process(self, now_ms):
        self.calls.add("alerts.process", now_ms)
        return ()

    def close(self):
        self.calls.add("alerts.close")


class FakeMetrics:
    """Counters the loop declares, and the per-tick flush."""

    def __init__(self, calls):
        self.calls = calls
        self.flush_failures = 0

    def counter(self, name, labels=()):
        return _Handle(self.calls, name)

    def gauge(self, name, labels=()):
        return _Handle(self.calls, name)

    def histogram(self, name, labels=(), buckets=None):
        return _Handle(self.calls, name)

    def flush(self, at_ms, tick_id):
        self.calls.add("metrics.flush", at_ms, tick_id)
        return True


class _Handle:
    """A metric handle that records what was recorded through it."""

    def __init__(self, calls, name):
        self.calls = calls
        self.name = name

    def inc(self, n=1, **labels):
        self.calls.add(f"metric.{self.name}", n, **labels)

    def set(self, value, **labels):
        self.calls.add(f"metric.{self.name}", value, **labels)

    def observe(self, value, **labels):
        self.calls.add(f"metric.{self.name}", value, **labels)


class FakeHeartbeat:
    """The liveness signal, noted after each completed tick."""

    def __init__(self, calls):
        self.calls = calls

    def note_tick_completed(self, monotonic_s):
        self.calls.add("heartbeat.note_tick_completed", monotonic_s)

    def beat(self, now_ms):
        self.calls.add("heartbeat.beat", now_ms)
        return True

    def start(self):
        self.calls.add("heartbeat.start")

    def close(self):
        self.calls.add("heartbeat.close")


class FakeLease:
    """A lease that grants the scope it is asked for."""

    LIVE_CAPABLE = False

    def __init__(self, calls):
        self.calls = calls
        self.permit = None

    def acquire(self, scope, holder, ttl_ms):
        from dskit.production.coordination import LeasePermit

        self.calls.add("lease.acquire", scope, holder, ttl_ms)
        self.permit = LeasePermit(
            scope=scope, holder=holder, fencing_token=1, expires_ms=NOW_MS + ttl_ms
        )
        return self.permit

    def renew(self, permit):
        self.calls.add("lease.renew", permit)
        return permit

    def current(self, scope):
        return self.permit

    def release(self, permit):
        self.calls.add("lease.release", permit)
        self.permit = None


class FakeReconciler:
    """`due` is the ONE owner of `on_start`/`every_s` (§5.9)."""

    def __init__(self, calls, due=False, action="none"):
        self.calls = calls
        self._due = due
        self.action = action

    def due(self, now_ms, last_run_ms=None):
        self.calls.add("reconciler.due", now_ms, last_run_ms)
        return self._due

    def run(self, view, executor, scope):
        self.calls.add("reconciler.run", view, executor, scope)
        return object()

    def apply_policy(self, report):
        self.calls.add("reconciler.apply_policy", report)
        return self.action


class FakeMonitor:
    """One monitor: what it observed, what it says, and its own state."""

    def __init__(self, calls, name, status="ok", response="log"):
        self.calls = calls
        self.name = name
        self.status = status
        self.response = response
        self.observed = []

    def observe(self, record):
        self.calls.add("monitor.observe", self.name, record)
        self.observed.append(record)

    def verdict(self):
        from dskit.production.records import Verdict

        return Verdict(
            status=self.status,
            statistic=None,
            threshold=None,
            n_ref=0,
            n_cur=len(self.observed),
            window="count:1",
            slice="all",
            provisional=False,
        )

    def should_trip(self):
        return self.status == "alarm" and self.response == "halt"

    def state(self):
        return {"observations": len(self.observed)}


class FakeLeg:
    """Stands in for `LegPipeline` at the seam `Tick` constructs it through."""

    built = []

    def __init__(self, document, release, bindings, schedule, decision, safety, execution,
                 recording, observability):
        self.bindings = bindings
        FakeLeg.built.append(self)

    def run(self):
        """Return the answer this leg's proposal was configured to give."""
        proposal = self.bindings.proposal
        return LegResult(
            result=FakeLeg.answers.get(proposal.id, "filled"),
            leg_id=self.bindings.leg_id,
            plan_id=f"plan-{self.bindings.leg_index}",
            plan_digest="p" * 64,
            final=proposal,
            client_ref=f"ref-{self.bindings.leg_index}",
            intent=None,
            ack=None,
            findings=(Finding(
                guard="size",
                measure="quantity",
                value=Decimal(3),
                bound=Decimal(100),
                window="none",
                scope_key="*",
                verdict="allow",
                reason="",
            ),),
            leg_latency_ms={bucket: 1 for bucket in LEG_LATENCY_BUCKETS},
        )


FakeLeg.answers = {}


# ==========================================================================
# Harness
# ==========================================================================


class Harness:
    """The seven bundles: real recording, faked decision inputs."""

    def __init__(self, tmp_path, document, release, clock, calls, **overrides):
        self.calls = calls
        self.clock = clock
        self.document = document
        self.release = release
        self.serve = ServeRoot(str(tmp_path / "serve"), document.series_id)
        self.lock = InstanceLock(self.serve.lock_path)
        self.lock.acquire()
        self.state = SeriesState(document.series_id)
        self.ledger = JsonlLedger(
            self.serve,
            "proc-1",
            release.release_hash,
            clock=clock,
            state=self.state,
            lock=self.lock,
        )
        self.journal_rows = []
        self.journal = self._journal
        self.monitors = overrides.pop("monitors", {})
        parts = {
            "feed": FakeFeed(calls),
            "decider": FakeDecider(calls),
            "guards": FakeGuards(calls),
            "accounting": FakeAccounting(calls),
            "executor": FakeExecutor(calls),
            "authorities": FakeAuthorities(calls),
            "breaker": FakeBreaker(calls),
            "arming": FakeArming(calls),
            "readiness": FakeReadiness(calls),
            "health": FakeHealth(calls),
            "alerts": FakeRouter(calls),
            "metrics": FakeMetrics(calls),
            "heartbeat": FakeHeartbeat(calls),
            "lease": FakeLease(calls),
            "reconciler": FakeReconciler(calls),
            "invocation": Invocation(
                armed=False, env_release_hash=None, once=True, max_ticks=None
            ),
            "cadence": FixedInterval({"period_ms": 60_000}),
            "calendar": AlwaysOpen({}),
            "overrun": Overrun({}),
            "recovery": None,
        }
        parts.update(overrides)
        self.parts = parts
        self.schedule = Schedule(
            clock=clock,
            calendar=parts["calendar"],
            cadence=parts["cadence"],
            overrun=parts["overrun"],
        )
        self.data = Data(feed=parts["feed"], decider=parts["decider"])
        self.decision = Decision(guards=parts["guards"], monitors=self.monitors)
        self.safety = Safety(
            breaker=parts["breaker"],
            arming=parts["arming"],
            authorities=parts["authorities"],
            readiness=parts["readiness"],
            invocation=parts["invocation"],
            action_policy=ActionPolicy(),
            transition_policy=TransitionPolicy(),
            submission_verifier=object(),
        )
        self.execution = Execution(
            executor=parts["executor"],
            accounting=parts["accounting"],
            lease=parts["lease"],
            resilience=object(),
        )
        from dskit.production.control import ControlInbox

        self.inbox = ControlInbox(self.serve, clock)
        self.recording = Recording(
            ledger=self.ledger,
            state=self.state,
            inbox=self.inbox,
            reconciler=parts["reconciler"],
            checkpoint=Checkpoint(
                release_hash=release.release_hash,
                last_tick_at=None,
                last_completed_tick_at=None,
                pending=(),
                positions_snapshot_at=None,
                schema_version=1,
                head_seq=0,
                head_hash="0" * 64,
            ),
            journal_hook=self.journal,
            id_source=ReleaseIdSource(release.release_hash),
        )
        self.observability = Observability(
            metrics=parts["metrics"],
            alerts=parts["alerts"],
            health=parts["health"],
            heartbeat=parts["heartbeat"],
        )

    def _journal(self, **kwargs):
        """The injected D22 seam: record the row rather than writing one."""
        self.calls.add("journal", **kwargs)
        self.journal_rows.append(kwargs)
        return None

    @property
    def bundles(self):
        """The seven, in §5.16's order."""
        return (
            self.schedule,
            self.data,
            self.decision,
            self.safety,
            self.execution,
            self.recording,
            self.observability,
        )

    def tick(self, tick_id="tick-1", **kwargs):
        """One `Tick` over these bundles."""
        return Tick(self.document, self.release, *self.bundles, tick_id, **kwargs)

    def loop(self, **kwargs):
        """One `ServeLoop` over these bundles."""
        kwargs.setdefault("process_id", "proc-1")
        return ServeLoop(self.document, self.release, *self.bundles, lock=self.lock, **kwargs)

    def records(self, kind=None):
        """Every appended envelope, optionally of one kind."""
        return [
            envelope
            for envelope in self.ledger.scan(kind=kind)
        ]

    def close(self):
        """Close the ledger and release the lock."""
        self.ledger.close()
        self.lock.release()


@pytest.fixture
def calls():
    """The shared ordered call log."""
    return Calls()


@pytest.fixture
def harness(tmp_path, serve_document, release_manifest, clock, calls):
    """The default harness: one live feed, two proposals, nothing refusing."""
    made = Harness(tmp_path, serve_document, release_manifest, clock, calls)
    yield made
    made.close()


@pytest.fixture(autouse=True)
def fake_leg(monkeypatch):
    """Every `Tick` builds `FakeLeg`s, so leg ORCHESTRATION is what is tested."""
    FakeLeg.built = []
    FakeLeg.answers = {}
    monkeypatch.setattr(loop_module, "LegPipeline", FakeLeg)
    return FakeLeg


def make_harness(tmp_path, serve_document, release_manifest, clock, calls, **overrides):
    """A harness with named collaborators replaced."""
    return Harness(tmp_path, serve_document, release_manifest, clock, calls, **overrides)


# ==========================================================================
# Tick — the walk no subclass may replace
# ==========================================================================


def test_tick_run_is_final(harness):
    """§5.13: "`run(tick_at_ms) -> TickResult` is **concrete and final**:
    it walks `vocab.TICK_PHASES` in order ... and no subclass can reorder
    or skip a phase". A subclass that replaced the walk would make the
    fixed phase order advisory, which is the whole thing D23 rules."""
    with pytest.raises(ProductionError):

        class Reordered(Tick):
            def run(self, tick_at_ms):
                return None


def test_a_subclass_may_still_override_any_phase(harness):
    """The steps are the seam, the order is not: the ten phase methods are
    "concrete, overridable methods"."""

    class Quieter(Tick):
        def fetch(self, tick_at_ms):
            return FeedResult(
                status="live", acq_id=None, records_added=0, source_config_hash=None,
                at_ms=tick_at_ms,
            )

    assert issubclass(Quieter, Tick)


@pytest.mark.parametrize("phase", TICK_PHASES)
def test_every_phase_is_a_method_with_the_signature_section_5_13_declares(harness, phase):
    """§5.13 writes each phase's arguments and return, "so the dataflow
    between phases is part of the contract". A phase that quietly grew an
    argument would make `run`'s threading unfixable from outside."""
    method = getattr(Tick, phase)
    parameters = tuple(inspect.signature(method).parameters)[1:]
    assert parameters == PHASE_SIGNATURES[phase]


def test_run_walks_the_ten_phases_in_tick_phases_order(harness):
    """The order IS `vocab.TICK_PHASES` — coverage before evaluate, account
    before propose. Fetching after evaluating would decide on rows the
    coverage gate never saw."""
    seen = []
    tick = harness.tick()
    for phase in TICK_PHASES:
        original = getattr(tick, phase)

        def recorded(*args, _phase=phase, _original=original, **kwargs):
            seen.append(_phase)
            return _original(*args, **kwargs)

        setattr(tick, phase, recorded)
    tick.run(NOW_MS)
    assert seen == list(TICK_PHASES)


def test_the_tick_result_carries_a_latency_for_every_phase(harness):
    """§6: `latency_ms{gate, verify_release, fetch, read_entry, coverage,
    evaluate, candidates, quotes, account, propose}` — "one key per §5.13
    `Tick` phase method, pinned by a test"."""
    result = harness.tick().run(NOW_MS)
    assert tuple(sorted(result.latency_ms)) == tuple(sorted(TICK_PHASES))
    assert all(isinstance(value, int) for value in result.latency_ms.values())


def test_a_refused_tick_still_carries_every_phase_key(tmp_path, serve_document, release_manifest,
                                                      clock, calls):
    """A phase that never ran is 0 milliseconds, not an absent key: §6's
    latency block is a fixed shape, and a monitor reducing over it cannot
    tell a missing key from a fast one."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        breaker=FakeBreaker(calls, state="halted"),
    )
    try:
        result = made.tick().run(NOW_MS)
        assert result.status == "skipped:halted"
        assert tuple(sorted(result.latency_ms)) == tuple(sorted(TICK_PHASES))
    finally:
        made.close()


def test_a_decided_tick_reports_its_status_and_the_batchs_provenance(harness):
    """`data_asof_ms`, `coverage_digest` and `inputs_digest` come from the
    frozen `EntryBatch`, not from anything the loop recomputed."""
    batch = a_batch()
    result = harness.tick().run(NOW_MS)
    assert result.status == "decided"
    assert result.data_asof_ms == batch.data_asof_ms
    assert result.coverage_digest == batch.coverage_digest
    assert result.inputs_digest == batch.inputs_digest


def test_the_feed_block_carries_section_6s_seven_members(harness):
    """§6: `feed{status, acq_id, records_added, source_config_hash,
    required_keys_digest, watermarks_by_key, coverage_digest}` — three
    from `fetch`'s `FeedResult`, four from `read_entry`'s `EntryBatch`,
    "which is why `feed` is a `TickResult` member and not something the
    loop adds"."""
    result = harness.tick().run(NOW_MS)
    assert set(result.feed) == {
        "status",
        "acq_id",
        "records_added",
        "source_config_hash",
        "required_keys_digest",
        "watermarks_by_key",
        "coverage_digest",
    }
    assert result.feed["status"] == "live"
    assert result.feed["acq_id"] == "acq-1"


def test_nav_comes_from_accounting_during_the_account_phase(harness):
    """§5.16: `nav` is `execution.accounting.value(view, quotes, at_ms)`,
    `null` when valuation was unavailable — "a recorded fact, not a gap,
    since an equity curve with a hole in it must say so"."""
    result = harness.tick().run(NOW_MS)
    assert result.nav == Decimal("1000")
    assert harness.calls.count("accounting.value") == 1


def test_an_unavailable_valuation_is_recorded_as_a_null_nav_not_a_refusal(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """A missing mark is a fact about the tick, not a reason to abandon a
    decision the data supported."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        accounting=FakeAccounting(calls, nav=None),
    )
    try:
        result = made.tick().run(NOW_MS)
        assert result.nav is None
        assert result.status == "decided"
    finally:
        made.close()


# ==========================================================================
# Tick — refusals map to TICK_STATUSES
# ==========================================================================


def test_a_closed_calendar_skips_the_tick_before_it_fetches(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """The gate is the FIRST phase for this reason: a shut market must not
    cost an acquisition, and `skipped:closed` is the recorded fact."""

    class Shut(AlwaysOpen):
        def is_open(self, at_ms):
            return False

    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls, calendar=Shut({})
    )
    try:
        result = made.tick().run(NOW_MS)
        assert result.status == "skipped:closed"
        assert calls.count("feed.pull") == 0
    finally:
        made.close()


def test_the_halt_sentinel_is_re_checked_at_the_tick_boundary(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.6: the `HALT` sentinel "is polled by the independent control
    worker at subsecond cadence and re-checked at every tick boundary".
    Present, no phase after the gate runs — stopping must not depend on
    the decision path, the inbox or the ledger."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        breaker=FakeBreaker(calls, sentinel=True),
    )
    try:
        result = made.tick().run(NOW_MS)
        assert result.status == "skipped:halted"
        assert calls.count("feed.pull") == 0
        assert calls.count("decider.read_entry") == 0
        assert calls.count("breaker.halt_sentinel_present") >= 1
    finally:
        made.close()


def test_a_halted_breaker_skips_the_tick(tmp_path, serve_document, release_manifest, clock, calls):
    """D12: halt refuses submissions. A halted series still ticks — the
    record says `skipped:halted` — but decides nothing."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        breaker=FakeBreaker(calls, state="halted"),
    )
    try:
        assert made.tick().run(NOW_MS).status == "skipped:halted"
    finally:
        made.close()


def test_degraded_health_skips_the_tick(tmp_path, serve_document, release_manifest, clock, calls):
    """§5.11: "`degraded` observes and refuses acts". `skipped:degraded` is
    the tick that says so."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        health=FakeHealth(calls, state="degraded"),
    )
    try:
        assert made.tick().run(NOW_MS).status == "skipped:degraded"
    finally:
        made.close()


def test_venue_skew_beyond_the_documents_bound_skips_the_tick(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§4.1: `schedule.max_venue_skew_ms` is a document knob and code holds
    no threshold; a venue whose clock has drifted past it cannot be
    reasoned about, so the tick is `skipped:skew`."""
    obj = serve_document.to_obj()
    obj["schedule"]["max_venue_skew_ms"] = 1_000
    from dskit.production.document import ServeDocument

    made = make_harness(
        tmp_path, ServeDocument.from_obj(obj), release_manifest, clock, calls,
        executor=FakeExecutor(calls, venue_time_ms=NOW_MS + 60_000),
    )
    try:
        assert made.tick().run(NOW_MS).status == "skipped:skew"
    finally:
        made.close()


def test_a_stale_feed_skips_the_tick(tmp_path, serve_document, release_manifest, clock, calls):
    """§5.2's freshness ladder decides the status; the tick records it.
    Deciding on stale rows is the failure this skip exists to prevent."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        feed=FakeFeed(calls, status="stale"),
    )
    try:
        result = made.tick().run(NOW_MS)
        assert result.status == "skipped:stale"
        assert result.feed["status"] == "stale"
    finally:
        made.close()


def test_a_dead_feed_refuses_the_tick(tmp_path, serve_document, release_manifest, clock, calls):
    """D4: "Zero new records is not a dead feed" — but a dead one is a
    refusal, not a skip: something is wrong rather than merely late."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        feed=FakeFeed(calls, status="dead"),
    )
    try:
        result = made.tick().run(NOW_MS)
        assert result.status == "refused"
        assert result.refusal_reason
    finally:
        made.close()


def test_a_coverage_gap_skips_the_tick_with_no_coverage(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """D6: freshness is coverage-wide. A batch missing a required key is
    the case "one fresh instrument cannot hide a stale input" rules out,
    and `coverage` is the phase that refuses on any gap."""
    batch = a_batch()
    short = dataclasses.replace(
        batch,
        watermarks_by_key={UNIVERSE[0]: batch.watermarks_by_key[UNIVERSE[0]]},
    )
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        decider=FakeDecider(calls, batch=short),
    )
    try:
        result = made.tick().run(NOW_MS)
        assert result.status == "skipped:no_coverage"
        assert calls.count("decider.evaluate") == 0
    finally:
        made.close()


def test_coverage_returns_a_feed_age_per_required_key(harness):
    """§5.13: `coverage(batch) -> tuple[FeedAge]` "returns the per-key ages
    — `clock.now_ms()` minus each `EntryBatch.watermarks_by_key` entry —
    because `feed_age_ms` is a registered measure and nothing else
    computes them"."""
    tick = harness.tick()
    ages = tick.coverage(a_batch())
    assert tuple(sorted(age.key for age in ages)) == tuple(sorted(UNIVERSE))
    assert all(isinstance(age, FeedAge) for age in ages)
    assert all(age.age_ms == NOW_MS - age.watermark_ms for age in ages)


def test_a_production_error_inside_a_decision_phase_refuses_the_tick(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """A refusal is a RESULT: the tick records why and the loop keeps
    scheduling. Letting it escape would end the process on a bad row."""

    class Refusing(FakeDecider):
        def evaluate(self, batch):
            raise ProductionError(["the head produced nothing usable"])

    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        decider=Refusing(calls),
    )
    try:
        result = made.tick().run(NOW_MS)
        assert result.status == "refused"
        assert "usable" in result.refusal_reason
        assert result.error is None
    finally:
        made.close()


def test_an_unexpected_exception_fails_the_tick_and_records_its_class_and_text(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§6: a `failed` tick "carries an `error` instead" of a refusal
    reason, `{class, text}`. Swallowing it into `refused` would make a
    bug indistinguishable from a policy decision."""

    class Broken(FakeDecider):
        def evaluate(self, batch):
            raise ZeroDivisionError("nodes disagree")

    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls, decider=Broken(calls)
    )
    try:
        result = made.tick().run(NOW_MS)
        assert result.status == "failed"
        assert result.error["class"] == "ZeroDivisionError"
        assert "disagree" in result.error["text"]
    finally:
        made.close()


def test_every_tick_status_the_loop_can_produce_is_a_vocabulary_member(harness):
    """`TICK_STATUSES` is closed; a status outside it would be a record
    nothing downstream can classify."""
    assert harness.tick().run(NOW_MS).status in TICK_STATUSES


def test_a_release_that_no_longer_verifies_refuses_the_tick(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """D24: "Startup, every tick and immediately before submit re-verify
    all hashes, artifact age and runtime fingerprint". `verify_release` is
    the second phase for that reason, before a single row is fetched."""

    class Refusing(Tick):
        def verify_release(self):
            raise ProductionError(["artifact_expired: artifacts/scaled/fitted.json"])

    made = make_harness(tmp_path, serve_document, release_manifest, clock, calls)
    try:
        tick = Refusing(
            made.document, made.release, *made.bundles, "tick-1"
        )
        result = tick.run(NOW_MS)
        assert result.status == "refused"
        assert "artifact_expired" in result.refusal_reason
        assert calls.count("feed.pull") == 0
    finally:
        made.close()


# ==========================================================================
# Tick — TickState, LegBindings and the legs
# ==========================================================================


def test_run_assembles_one_tick_state_and_gives_every_leg_the_same_one(harness):
    """§5.13: the `TickState` is "a tick product, not something a leg can
    reconstruct: two of its five members are this tick's fetch result and
    exist nowhere else". Every leg of the tick shares it; step (2) is what
    re-snapshots."""
    harness.tick().run(NOW_MS)
    states = {id(leg.bindings.state) for leg in FakeLeg.built}
    assert len(FakeLeg.built) == len(UNIVERSE)
    assert len(states) == 1


def test_the_tick_state_carries_its_six_members(harness):
    """R20 added `entry_batch` as the sixth, so the verifier rehashes the
    same object the plan bound."""
    harness.tick().run(NOW_MS)
    state = FakeLeg.built[0].bindings.state
    assert isinstance(state, TickState)
    for member in TICK_STATE_MEMBERS:
        assert getattr(state, member) is not None or member == "entry_batch"
    assert state.feed_status == "live"
    assert tuple(sorted(age.key for age in state.feed_ages)) == tuple(sorted(UNIVERSE))
    assert state.calendar is harness.schedule.calendar
    assert state.entry_batch is not None


def test_the_tick_states_account_is_the_account_phases_answer(harness):
    """§5.13: "`account` from the `account` phase" — the tick's one
    snapshot, taken with the requirement union every candidate produced."""
    harness.tick().run(NOW_MS)
    assert FakeLeg.built[0].bindings.state.account.asof_ms == NOW_MS


def test_every_leg_gets_the_thirteen_bindings_in_section_5_13_1s_order(harness):
    """A member that moved would silently swap two collaborators, and the
    leg reads `release` and `rung` at step (3)."""
    harness.tick().run(NOW_MS)
    bindings = FakeLeg.built[0].bindings
    assert tuple(f.name for f in dataclasses.fields(bindings)) == LEG_BINDING_MEMBERS
    assert bindings.origin == "model"
    assert bindings.release is harness.release
    assert bindings.rung == harness.document.rung
    assert bindings.head_digest == "h" * 64
    assert bindings.reduction is None


def test_the_requirement_union_reaches_every_leg_because_a_leg_cannot_rebuild_it(harness):
    """§5.13.1: "`requirements` is the deduplicated `EvidenceRequirement`
    tuple `GuardChain.requirements` produced for the whole tick — the leg
    cannot recompute it, because that call needs every candidate and a leg
    holds one proposal"."""
    harness.tick().run(NOW_MS)
    args, _kwargs = harness.calls.first("guards.requirements")
    assert len(args[0]) == len(UNIVERSE), "requirements is asked for EVERY candidate"
    assert all(leg.bindings.requirements == () for leg in FakeLeg.built)


def test_model_proposals_run_in_stable_candidate_id_order(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.13: "Model proposals are sorted by stable candidate id and never
    pre-authorized as a batch". Without a total order, cumulative exposure
    across legs depends on dict iteration."""
    proposals = (
        a_proposal("cand-zzz", "ZZZ"),
        a_proposal("cand-aaa", "AAA"),
        a_proposal("cand-mmm", "MMM"),
    )
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        decider=FakeDecider(calls, proposer=FakeProposer(calls, proposals=proposals)),
    )
    try:
        made.tick().run(NOW_MS)
        assert [leg.bindings.proposal.id for leg in FakeLeg.built] == [
            "cand-aaa",
            "cand-mmm",
            "cand-zzz",
        ]
        assert [leg.bindings.leg_index for leg in FakeLeg.built] == [0, 1, 2]
    finally:
        made.close()


def test_each_leg_gets_its_own_leg_id_from_the_id_source(harness):
    """D20: ids derive from stable semantic inputs before append, so a
    replay allocates the same ones. Two legs sharing an id would make a
    decision record unreadable."""
    harness.tick().run(NOW_MS)
    ids = [leg.bindings.leg_id for leg in FakeLeg.built]
    assert len(set(ids)) == len(ids)
    assert all(len(leg_id) == 64 for leg_id in ids)


def test_an_unknown_leg_stops_every_later_leg(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.13 step (8): "an ambiguous outcome stops all later legs until
    reconciliation". Continuing would size the next proposal against a
    position that may or may not exist."""
    proposals = (
        a_proposal("cand-1", "INS1"),
        a_proposal("cand-2", "INS2"),
        a_proposal("cand-3", "INS3"),
    )
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        decider=FakeDecider(calls, proposer=FakeProposer(calls, proposals=proposals)),
    )
    FakeLeg.answers = {"cand-2": "unknown"}
    try:
        result = made.tick().run(NOW_MS)
        assert [leg.bindings.proposal.id for leg in FakeLeg.built] == ["cand-1", "cand-2"]
        assert len(result.legs) == 2
    finally:
        made.close()


def test_a_tick_with_no_proposals_is_decided_with_zero_legs(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§6: "a no-op tick has `final: none` per leg or zero legs with
    `reason`". Abstaining is a decision, not a refusal."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        decider=FakeDecider(calls, proposer=FakeProposer(calls, proposals=())),
    )
    try:
        result = made.tick().run(NOW_MS)
        assert result.status == "decided"
        assert result.legs == ()
        assert FakeLeg.built == []
    finally:
        made.close()


def test_the_legs_the_tick_reports_carry_section_6s_decision_members(harness):
    """§6 `decision.legs[]{leg_id, instrument, prediction, confidence,
    baseline, expected_value, reference_price, proposal, findings[], final,
    client_ref}` — `final` is the final proposal's SIDE, which is what
    `Coverage` reduces to an abstaining fraction (§5.10)."""
    result = harness.tick().run(NOW_MS)
    entry = result.legs[0]
    assert set(entry) >= {
        "leg_id",
        "instrument",
        "prediction",
        "confidence",
        "baseline",
        "expected_value",
        "reference_price",
        "proposal",
        "findings",
        "final",
        "client_ref",
    }
    assert entry["final"] == "buy"
    assert entry["instrument"] in UNIVERSE


def test_the_leg_latency_buckets_are_summed_over_the_ticks_legs(harness):
    """§6: `leg_latency_ms` is "keyed by `vocab.LEG_LATENCY_BUCKETS` and
    summed over the tick's legs"."""
    result = harness.tick().run(NOW_MS)
    assert tuple(sorted(result.leg_latency_ms)) == tuple(sorted(LEG_LATENCY_BUCKETS))
    assert all(value == len(UNIVERSE) for value in result.leg_latency_ms.values())


def test_every_legs_findings_reach_the_tick_result(harness):
    """D9: every finding is recorded. A finding that only the leg saw is a
    guard decision with no evidence on the chain."""
    result = harness.tick().run(NOW_MS)
    assert len(result.findings) == len(UNIVERSE)


def test_the_decision_plan_ids_name_one_plan_per_leg(harness):
    """§6: `decision_plan_ids[]` is written for every leg, including the
    ones that terminalised at step (4) without an intent."""
    result = harness.tick().run(NOW_MS)
    assert len(result.decision_plan_ids) == len(FakeLeg.built)


# ==========================================================================
# Tick — the reduction cycle
# ==========================================================================


def a_reduction_cycle():
    """A stored `ReductionPlan` and its authorization, in maker order."""
    intents = []
    for index, key in enumerate(reversed(UNIVERSE)):
        intents.append(
            ReductionIntent(
                release_hash=RELEASE_HASH,
                request_id="req-1",
                index=index,
                candidate=Candidate(id=f"red-{key}", instrument=key, scope_keys=(key,)),
                proposal=a_proposal(f"red-{key}", key, side="sell"),
                risk_state_digest="r" * 64,
                expires_ms=NOW_MS + 600_000,
            )
        )
    digests = tuple(intent.reduction_intent_digest() for intent in intents)
    plan = ReductionPlan(
        release_hash=RELEASE_HASH,
        risk_state_digest="r" * 64,
        intents=tuple(intents),
        reduction_intent_digests=digests,
        expires_ms=NOW_MS + 600_000,
    )
    return plan, digests


def test_a_reduction_cycle_carries_only_the_plans_legs(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.13: "A reduction cycle is its own tick: it carries only the
    plan's legs, in maker-approved `index` order, never interleaved with
    model legs". No proposer runs — "the candidate is signed, not
    derived"."""
    plan, _digests = a_reduction_cycle()
    made = make_harness(tmp_path, serve_document, release_manifest, clock, calls)
    try:
        made.tick(reduction_cycle=plan).run(NOW_MS)
        assert [leg.bindings.origin for leg in FakeLeg.built] == ["reduction"] * len(plan.intents)
        assert calls.count("proposer.proposals") == 0
    finally:
        made.close()


def test_a_reduction_cycles_legs_run_in_the_makers_index_order_not_by_candidate_id(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.13: the plan's own order "is what makes D12's 'execution stops on
    the first refusal' mean the plan's order rather than a candidate-id
    sort" — the reversed universe is deliberately anti-sorted here."""
    plan, _digests = a_reduction_cycle()
    made = make_harness(tmp_path, serve_document, release_manifest, clock, calls)
    try:
        made.tick(reduction_cycle=plan).run(NOW_MS)
        assert [leg.bindings.leg_index for leg in FakeLeg.built] == [0, 1]
        assert [leg.bindings.proposal.id for leg in FakeLeg.built] == [
            intent.proposal.id for intent in plan.intents
        ]
    finally:
        made.close()


def test_a_reduction_legs_binding_names_the_signed_intent_and_its_digest(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.13.1: the `reduction` binding "makes 'the digest the right names
    is the digest that reaches the venue' checkable rather than
    aspirational"."""
    plan, digests = a_reduction_cycle()
    made = make_harness(tmp_path, serve_document, release_manifest, clock, calls)
    try:
        made.tick(reduction_cycle=plan).run(NOW_MS)
        binding = FakeLeg.built[0].bindings.reduction
        assert binding is not None
        assert binding.signed is plan.intents[0]
        assert binding.digest == digests[0]
    finally:
        made.close()


def test_a_reduction_cycles_candidates_reach_the_account_phase(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.13.1: the stored candidates are contributed "through
    `Tick(reduction_cycle=…)` so they are in the candidate set before
    `Tick.account` runs, so their scope keys are in the requirement union
    and their guards find the evidence they demand" — "without that a
    reduction leg refuses for missing evidence, which is the opposite of
    what the path is for"."""
    plan, _digests = a_reduction_cycle()
    made = make_harness(tmp_path, serve_document, release_manifest, clock, calls)
    try:
        made.tick(reduction_cycle=plan).run(NOW_MS)
        args, _kwargs = calls.first("guards.requirements")
        assert sorted(c.id for c in args[0]) == sorted(i.candidate.id for i in plan.intents)
    finally:
        made.close()


# ==========================================================================
# ServeLoop — the lifecycle
# ==========================================================================


def test_serve_loop_run_returns_an_exit_code_from_the_closed_map(harness):
    """§5.13: "Exit codes are 0 stopped · 1 error · 3 halted · 4 already
    running · 5 refused", and `vocab.EXIT_CODES` is "the one place 0/1/3/4/5
    are named"."""
    assert harness.loop().run() in set(EXIT_CODES.values())


def test_a_graceful_stop_exits_zero(harness):
    """`--once` runs one tick and stops; a completed serve is exit 0."""
    assert harness.loop().run() == EXIT_CODES["stopped"]


def test_the_loop_walks_lock_then_lease_then_recovery_then_reconcile_then_ready(harness):
    """§5.13/D13: the lock is taken before the ledger, the lease before
    reconciliation, `Recovery` before scheduling, and startup "reconciles
    before `READY`". Reconciling against a venue before the fold has been
    replayed compares a live book to an empty one."""
    made = harness
    made.parts["reconciler"]._due = True
    made.loop().run()
    order = made.calls.names()
    assert order.index("lease.acquire") < order.index("reconciler.run")
    assert order.index("reconciler.run") < order.index("feed.pull")


def test_the_loop_reaches_the_stopped_state_and_reports_it(harness):
    """The FSM is `init → locked → leased → reconciling → ready →
    {waiting ⇄ ticking} → stopping → stopped`; every name is a
    `LOOP_STATES` member."""
    loop = harness.loop()
    loop.run()
    assert loop.state in LOOP_STATES
    assert loop.state == "stopped"


def test_a_second_process_holding_the_lock_exits_already_running(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.11/D15: `flock(LOCK_EX | LOCK_NB)` "prevents a second process on
    the same filesystem"; 4 is the exit that says so rather than 1, so an
    operator can tell a duplicate start from a failure."""
    made = make_harness(tmp_path, serve_document, release_manifest, clock, calls)
    try:
        rival = InstanceLock(made.serve.lock_path)
        loop = ServeLoop(
            made.document, made.release, *made.bundles, lock=rival, process_id="proc-2"
        )
        made.lock.release()
        made.lock.acquire()
        assert loop.run() == EXIT_CODES["already_running"]
    finally:
        made.close()


def test_a_readiness_no_go_at_a_live_rung_refuses_with_exit_five(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.13: "Every live rung requires a current GO record bound to the
    exact release before arming or submit" and NO-GO exits 5 — kept apart
    from 3 because "a readiness NO-GO means nothing is wrong and the
    checklist is simply not yet satisfied"."""
    obj = serve_document.to_obj()
    obj["rung"] = "live_limited"
    obj["guards"] = {
        "size": {"uses": "limit", "params": {"measure": "quantity", "bound": {"max": "100"}}},
        "day_loss": {
            "uses": "limit",
            "params": {
                "measure": "pnl",
                "window": {"calendar": "session"},
                "bound": {"min": "-500"},
                "on_breach": "halt",
            },
        },
    }
    obj["accounting"]["uses"] = "yourproject.accounting:VenueAccounting"
    obj["arming"]["approval"]["uses"] = "yourproject.approvals:HmacVerifier"
    from dskit.production.document import ServeDocument

    made = make_harness(
        tmp_path, ServeDocument.from_obj(obj), release_manifest, clock, calls,
        readiness=FakeReadiness(calls, verdict="no_go"),
    )
    try:
        assert made.loop().run() == EXIT_CODES["refused"]
        assert calls.count("feed.pull") == 0
    finally:
        made.close()


def test_a_readiness_no_go_below_a_live_rung_does_not_refuse(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """The GO gates LIVE rungs. Refusing a shadow serve for a checklist it
    does not need would stop the rung whose whole purpose is to run
    before the checklist is satisfiable."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        readiness=FakeReadiness(calls, verdict="no_go"),
    )
    try:
        assert made.loop().run() == EXIT_CODES["stopped"]
    finally:
        made.close()


def test_a_halted_series_stops_with_exit_three(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.13: 3 "keeps halted" — "a breaker-halted series needs operator
    action and refuses submissions", which is a different fact from a
    NO-GO and gets a different code."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        breaker=FakeBreaker(calls, state="halted"),
    )
    try:
        assert made.loop().run() == EXIT_CODES["halted"]
    finally:
        made.close()


def test_a_reconcile_mismatch_that_says_halt_trips_the_breaker_before_ready(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """D13: "Startup reconciles before `READY`; mismatches halt or refuse",
    and §5.9 makes `apply_policy` the one owner of `on_mismatch`."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        reconciler=FakeReconciler(calls, due=True, action="halt"),
    )
    try:
        made.loop().run()
        assert calls.count("breaker.trip") >= 1
        _args, kwargs = calls.first("breaker.trip")
        assert kwargs.get("cause", "trip") == "trip"
    finally:
        made.close()


def test_a_fault_stops_the_process_with_exit_one_and_records_it(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """`faulted` is restartable, and the `process` stop record is what says
    the chain was left deliberately: "the ledger never claims its own
    final hash", so the code is recorded rather than inferred."""

    class BrokenCadence(FixedInterval):
        def next_tick(self, after_ms, calendar):
            raise RuntimeError("the grid collapsed")

    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        cadence=BrokenCadence({"period_ms": 60_000}),
    )
    try:
        loop = made.loop()
        assert loop.run() == EXIT_CODES["error"]
        assert loop.state == "faulted"
        stops = [
            envelope
            for envelope in made.records("process")
            if envelope["body"].get("event") == "stop"
        ]
        assert stops and stops[-1]["body"]["exit_code"] == EXIT_CODES["error"]
    finally:
        made.close()


def test_once_runs_exactly_one_tick(harness):
    """§7: only operational flags live on `serve`; `--once` runs one tick."""
    harness.loop().run()
    assert harness.calls.count("feed.pull") == 1


def test_max_ticks_bounds_completed_ticks(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.13: "`--max-ticks N` bounds completed ticks"."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        invocation=Invocation(armed=False, env_release_hash=None, once=False, max_ticks=3),
    )
    try:
        made.loop().run()
        assert calls.count("feed.pull") == 3
    finally:
        made.close()


# ==========================================================================
# ServeLoop — one tick's records, in order
# ==========================================================================


def test_the_tick_id_is_allocated_before_the_tick_start_record(harness):
    """D20: "Tick ids are allocated before `tick_start`"; R9: a record id is
    unique across the SERIES, so the producer qualifies it with the kind —
    `tick_start:<tick_id>` and `tick:<tick_id>` are two records of one
    tick and must not collide."""
    harness.loop().run()
    starts = harness.records("tick_start")
    terminals = harness.records("tick")
    assert len(starts) == 1
    tick_id = starts[0]["body"]["tick_id"]
    assert starts[0]["id"] == f"tick_start:{tick_id}"
    assert terminals[0]["id"] == f"tick:{tick_id}"


def test_the_tick_start_carries_section_6s_three_members(harness):
    """§6: `tick_start` is `{tick_id, tick_at_ms, release_hash}`."""
    harness.loop().run()
    body = harness.records("tick_start")[0]["body"]
    assert set(body) == {"tick_id", "tick_at_ms", "release_hash"}
    assert body["release_hash"] == harness.release.release_hash


def test_the_tick_start_crosses_a_barrier_before_any_phase_runs(harness, monkeypatch):
    """D13: "The `tick_start` crosses a mandatory `ledger.barrier()` before
    work". A tick whose start was still in a write buffer when the feed
    was pulled could be lost by a crash that the fetch already caused."""
    seen = []
    real_barrier = harness.ledger.barrier
    monkeypatch.setattr(
        harness.ledger, "barrier", lambda: (seen.append(("barrier", harness.ledger.head()[0])), real_barrier())[1]
    )
    original = harness.parts["feed"].pull
    monkeypatch.setattr(
        harness.parts["feed"], "pull", lambda at: (seen.append(("pull", None)), original(at))[1]
    )
    harness.loop().run()
    first_pull = [i for i, (name, _seq) in enumerate(seen) if name == "pull"][0]
    barriers_before = [seq for name, seq in seen[:first_pull] if name == "barrier"]
    assert barriers_before, "no barrier crossed before the first fetch"


def test_exactly_one_decision_and_one_tick_close_each_tick_start(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.13: "every started tick eventually has exactly one terminal
    `tick` and one `decision`". Two would double-count every monitor;
    none would leave the tick open forever."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        invocation=Invocation(armed=False, env_release_hash=None, once=False, max_ticks=3),
    )
    try:
        made.loop().run()
        assert len(made.records("tick_start")) == 3
        assert len(made.records("decision")) == 3
        assert len(made.records("tick")) == 3
    finally:
        made.close()


def test_the_decision_is_appended_before_the_terminal_tick(harness):
    """R17 pins the LIVE order so a recovered tick reads on the chain
    exactly as a tick that ran does; recovery matches it."""
    harness.loop().run()
    kinds = [envelope["kind"] for envelope in harness.records()]
    assert kinds.index("decision") < kinds.index("tick")


def test_the_terminal_tick_carries_what_only_the_loop_holds(harness):
    """§5.16: "The loop adds only what it alone holds — `tick_at`,
    `calendar`, `overrun_absorbed[]`, `health`, `breaker`, `rung` — when it
    writes §6's `tick`"."""
    harness.loop().run()
    body = harness.records("tick")[0]["body"]
    for member in ("tick_at", "calendar", "overrun_absorbed", "health", "breaker", "rung"):
        assert member in body, member
    assert body["rung"] == harness.document.rung
    assert body["breaker"] == "active"
    assert body["health"] == "ready"


def test_the_terminal_tick_carries_the_phase_latencies_and_the_leg_spans(harness):
    """§6 pins both blocks by a test, precisely so a renamed phase is
    caught here rather than by a monitor that quietly reduces over
    nothing."""
    harness.loop().run()
    body = harness.records("tick")[0]["body"]
    assert tuple(sorted(body["latency_ms"])) == tuple(sorted(TICK_PHASES))
    assert tuple(sorted(body["leg_latency_ms"])) == tuple(sorted(LEG_LATENCY_BUCKETS))


def test_a_tick_that_never_fetched_carries_a_null_feed_block(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§6 exempts a tick whose fetch never completed from the seven-member
    `feed` block: "inventing a feed status for it would be a fabricated
    observation"."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        breaker=FakeBreaker(calls, state="halted"),
    )
    try:
        made.loop().run()
        body = made.records("tick")[0]["body"]
        assert body["status"] == "skipped:halted"
        assert body["feed"] is None
    finally:
        made.close()


def test_the_decision_names_its_tick_and_its_plans(harness):
    """§6: `decision` is `{tick_id, decision_plan_ids[],
    decision_plan_digests[], legs[]}`."""
    harness.loop().run()
    body = harness.records("decision")[0]["body"]
    assert body["tick_id"] == harness.records("tick_start")[0]["body"]["tick_id"]
    assert len(body["legs"]) == len(UNIVERSE)
    assert len(body["decision_plan_ids"]) == len(UNIVERSE)


def test_the_control_inbox_is_consumed_before_the_tick(harness, monkeypatch):
    """§5.8: "The loop consumes non-HALT commands before a tick or between
    completed legs, never between an intent and its outcome", and §5.8's
    pending-control gate blocks the next pre-submit action gate — which is
    only true if the command was consumed first."""
    seen = []
    original = harness.inbox.pending
    monkeypatch.setattr(
        harness.inbox, "pending", lambda: (seen.append(len(harness.calls.log)), original())[1]
    )
    harness.loop().run()
    first_pull = harness.calls.names().index("feed.pull")
    assert seen and min(seen) <= first_pull


def test_an_overrun_absorbed_instant_is_recorded_on_the_tick_that_absorbed_it(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§6: `overrun_absorbed[]` is "the tick instants this tick coalesced
    or skipped"; `Overrun.resolve` is the one owner of the policy, and the
    loop records what it was told rather than deciding again."""

    class Coalescing(Overrun):
        def resolve(self, due_ticks, now_ms):
            calls.add("overrun.resolve", tuple(due_ticks), now_ms)
            ticks = tuple(due_ticks)
            return (ticks[-1], ticks[:-1]) if ticks else (None, ())

    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        overrun=Coalescing({"policy": "coalesce"}),
    )
    try:
        made.loop().run()
        assert calls.count("overrun.resolve") >= 1
        body = made.records("tick")[0]["body"]
        assert isinstance(body["overrun_absorbed"], list)
    finally:
        made.close()


# ==========================================================================
# ServeLoop — observe, alert, flush, heartbeat, checkpoint LAST
# ==========================================================================


def test_the_tail_of_a_tick_runs_observe_then_alerts_then_metrics_then_the_heartbeat(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.11/§5.11.1: `AlertRouter.process` and `Metrics.flush` are called
    "after `observe`, outside every barrier" and only from the loop
    thread. That ordering is what makes D23's single-writer rule
    structural."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        monitors={"coverage": FakeMonitor(calls, "coverage")},
    )
    try:
        made.loop().run()
        order = calls.names()
        assert order.index("monitor.observe") < order.index("alerts.process")
        assert order.index("alerts.process") < order.index("metrics.flush")
        assert order.index("metrics.flush") < order.index("heartbeat.note_tick_completed")
    finally:
        made.close()


def test_every_monitor_observes_both_the_decision_and_the_tick_body(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.10: the operational monitors read named fields "of the decision
    AND tick records" — `Coverage` reads `legs[].final`, `Staleness` reads
    `data_asof_ms`, so both bodies must reach `observe`."""
    monitor = FakeMonitor(calls, "coverage")
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls, monitors={"coverage": monitor}
    )
    try:
        made.loop().run()
        kinds = [set(body) for body in monitor.observed]
        assert any("legs" in body for body in kinds)
        assert any("latency_ms" in body for body in kinds)
    finally:
        made.close()


def test_a_monitor_verdict_is_appended_as_a_monitor_record(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§6: `monitor` records a verdict change or window close, body
    `{monitor, slice, window, statistic, threshold, status, provisional}`."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        monitors={"coverage": FakeMonitor(calls, "coverage")},
    )
    try:
        made.loop().run()
        bodies = [envelope["body"] for envelope in made.records("monitor")]
        assert bodies, "no monitor verdict was recorded"
        assert set(bodies[0]) >= {
            "monitor", "slice", "window", "statistic", "threshold", "status", "provisional"
        }
        assert bodies[0]["monitor"] == "coverage"
    finally:
        made.close()


def test_a_monitor_alarm_declared_halt_trips_the_breaker(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.10, pinned by a test: "`alarm` with `response: halt` trips the
    breaker" — and `TRIP_REASONS` carries `monitor_alarm` for exactly
    that."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        monitors={"drift": FakeMonitor(calls, "drift", status="alarm", response="halt")},
    )
    try:
        made.loop().run()
        assert calls.count("breaker.trip") >= 1
        args, _kwargs = calls.first("breaker.trip")
        assert args[0] == "monitor_alarm"
    finally:
        made.close()


def test_a_monitor_alarm_declared_warn_never_trips(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """`RESPONSES` is `(log, warn, halt)` and only `halt` trips; a drift
    statistic must not stop a series the operator declared advisory."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        monitors={"drift": FakeMonitor(calls, "drift", status="alarm", response="warn")},
    )
    try:
        made.loop().run()
        assert calls.count("breaker.trip") == 0
    finally:
        made.close()


def test_metrics_are_flushed_once_per_tick_with_that_ticks_id(harness):
    """§5.11.1: `flush()` appends one JSON object PER TICK and "is called
    by the loop after `observe`, outside every barrier"."""
    harness.loop().run()
    assert harness.calls.count("metrics.flush") == 1
    args, _kwargs = harness.calls.first("metrics.flush")
    assert args[1] == harness.records("tick_start")[0]["body"]["tick_id"]


def test_a_flush_that_fails_can_never_fail_a_tick(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.11.1: "A flush failure is counted and swallowed like a sink
    failure; it can never fail a tick"."""

    class Failing(FakeMetrics):
        def flush(self, at_ms, tick_id):
            calls.add("metrics.flush", at_ms, tick_id)
            return False

    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls, metrics=Failing(calls)
    )
    try:
        assert made.loop().run() == EXIT_CODES["stopped"]
        assert made.records("tick")[0]["body"]["status"] == "decided"
    finally:
        made.close()


def test_the_checkpoint_is_written_last(harness):
    """D13: "the checkpoint is written last". A cache written before the
    records it projects would claim a head the chain does not have, and
    §5.8 makes that refuse rather than rebuild."""
    harness.loop().run()
    order = harness.calls.names()
    checkpoint = Checkpoint.load(harness.serve.checkpoint_cache)
    assert checkpoint is not None
    assert checkpoint.head_seq == harness.ledger.head()[0]
    assert checkpoint.validate_against(harness.ledger) == "current"
    assert order.index("heartbeat.note_tick_completed") < len(order)


def test_the_checkpoint_names_the_release_and_the_last_tick(harness):
    """§5.8: `Checkpoint{release_hash, last_tick_at, last_completed_tick_at,
    pending, positions_snapshot_at, schema_version, head_seq, head_hash}`."""
    harness.loop().run()
    checkpoint = Checkpoint.load(harness.serve.checkpoint_cache)
    assert checkpoint.release_hash == harness.release.release_hash
    assert checkpoint.last_tick_at == NOW_MS
    assert checkpoint.last_completed_tick_at == NOW_MS


def test_the_snapshot_payload_carries_every_monitors_own_state(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§6: the `snapshot` record carries every `StateView` member "plus
    monitor state, which §5.10 requires the snapshot to carry and which is
    not a `StateView` member — dropping it would reset every drift window
    on restart, and a monitor below `min_n` cannot alarm until it
    refills"."""
    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        monitors={"coverage": FakeMonitor(calls, "coverage")},
    )
    try:
        made.loop().run()
        snapshots = made.records("snapshot")
        assert snapshots, "the loop wrote no snapshot"
        payload = snapshots[-1]["body"]["state"]
        assert payload["monitor_state"]["coverage"] == {"observations": 2}
    finally:
        made.close()


# ==========================================================================
# ServeLoop — process records and the D22 journal row
# ==========================================================================


def test_the_process_start_record_carries_section_6s_bindings(harness):
    """§6: `process` start binds `series_id`, `release_hash`, `doc_hash`,
    `serving_hash`, `run_hash`, `artifact_digests`, `source_config_hash`,
    `runtime_fingerprint`, `rung`, `executor_kind` and `code_version` — the
    whole of D24's content-and-runtime binding, on the chain."""
    harness.loop().run()
    starts = [
        envelope for envelope in harness.records("process")
        if envelope["body"]["event"] == "start"
    ]
    assert len(starts) == 1
    body = starts[0]["body"]
    for member in (
        "series_id",
        "release_hash",
        "doc_hash",
        "serving_hash",
        "run_hash",
        "artifact_digests",
        "source_config_hash",
        "runtime_fingerprint",
        "rung",
        "executor_kind",
        "code_version",
    ):
        assert member in body, member
    assert body["rung"] == harness.document.rung


def test_the_process_stop_record_is_appended_and_carries_its_exit_code(harness):
    """D15: "On graceful stop the writer appends/barriers a `process`
    record with `event: stop`, obtains the resulting final head, then
    writes one journal row"."""
    harness.loop().run()
    stops = [
        envelope for envelope in harness.records("process")
        if envelope["body"]["event"] == "stop"
    ]
    assert len(stops) == 1
    assert stops[0]["body"]["exit_code"] == EXIT_CODES["stopped"]


def test_exactly_one_journal_row_is_written_per_completed_process(harness):
    """D22: "one production row per normally completed process". Serve
    "never journals a tick or consumed command"."""
    harness.loop().run()
    assert len(harness.journal_rows) == 1


def test_the_journal_row_renders_the_process_id_and_final_head_in_the_d22_form(harness):
    """D22 fixes the field set — there is no `process_id` or
    `final_head_hash` column and this plan adds none — so both are
    rendered into `notes` in the one documented `production-v1
    process=<id> head=<seq>:<hash>` form that `verify` parses back."""
    harness.loop().run()
    row = harness.journal_rows[0]
    seq, head_hash = harness.ledger.head()
    assert row["notes"].startswith("production-v1 ")
    assert f"process={harness.ledger._process_id}" in row["notes"] or "process=" in row["notes"]
    assert f"head={seq}:{head_hash}" in row["notes"]


def test_the_journal_step_names_the_verb_and_the_rung_within_eighty_characters(harness):
    """D22: `step` names "the verb and rung (within the 80-character
    `_STEP_MAX`)". A longer step is refused by the journal itself, so a
    process that ran would fail at the last line."""
    harness.loop().run()
    step = harness.journal_rows[0]["step"]
    assert len(step) <= 80
    assert "serve" in step
    assert harness.document.rung in step


def test_the_journal_row_locates_the_serve_series_root(harness):
    """D22: `db_location` is "the serve-series root", which is what makes
    the row findable from the series rather than from the run."""
    harness.loop().run()
    assert harness.journal_rows[0]["db_location"] == harness.serve.series_path


def test_the_journal_row_is_written_after_the_stop_record_not_before(harness):
    """D15: the writer appends and barriers the stop record, THEN obtains
    the final head — "the ledger never claims its own final hash"."""
    harness.loop().run()
    order = harness.calls.names()
    assert order[-1] == "journal"


def test_a_faulted_process_writes_no_journal_row(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """D22: one row per NORMALLY completed process. A fault is the case
    `verify` reports as a gap, and claiming a clean anchor for it would
    make that report unreachable."""

    class BrokenCadence(FixedInterval):
        def next_tick(self, after_ms, calendar):
            raise RuntimeError("the grid collapsed")

    made = make_harness(
        tmp_path, serve_document, release_manifest, clock, calls,
        cadence=BrokenCadence({"period_ms": 60_000}),
    )
    try:
        made.loop().run()
        assert made.journal_rows == []
    finally:
        made.close()


def test_the_journal_hook_is_the_injected_seam_not_the_real_recorder(harness):
    """D22: "Production therefore calls the journal through one injected
    `journal_hook` seam defaulting to `record_production`", so a test can
    assert against a recording fake and never touch a store."""
    harness.loop().run()
    assert harness.recording.journal_hook is harness.journal


# ==========================================================================
# Structure
# ==========================================================================


def test_loop_exports_exactly_the_two_classes_section_8_names():
    """§8: `loop.py` ships `ServeLoop` and `Tick` — the scheduler and the
    phase walk — and nothing else; `__all__` plus the `_` prefix is the
    API contract."""
    assert set(loop_module.__all__) >= {"ServeLoop", "Tick"}
    assert not [name for name in loop_module.__all__ if name.startswith("_")]


def test_the_loop_holds_no_submission_sequence():
    """§5.13.1: "It does not contain the submission sequence". A `submit`
    call in `loop.py` would be a second place money leaves the process."""
    source = (PACKAGE_DIR / "loop.py").read_text(encoding="utf-8")
    calls_made = {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "submit" not in calls_made
    assert "verify_and_call" not in calls_made
    assert "mint" not in calls_made


def test_the_loop_never_reads_the_wall_clock():
    """Every instant comes from the injected `Clock`; reaching for
    `time.time()` would break the D20 replay parity the whole package
    rests on."""
    source = (PACKAGE_DIR / "loop.py").read_text(encoding="utf-8")
    assert "time.time(" not in source
    assert "datetime.now" not in source


def test_serve_loop_is_never_subclassed_in_tree():
    """§8's `test_oop.py` line: `ServeLoop` is not a seam. Its variation is
    which objects were injected (D2), and a subclass would be a second
    mechanism for that."""
    offenders = []
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if isinstance(base, ast.Name) and base.id == "ServeLoop":
                        offenders.append(f"{path.name}:{node.name}")
    assert not offenders, offenders


def test_there_is_no_replay_tick():
    """§5.13: "There is deliberately **no** `ReplayTick`: replay is already
    the five-object swap D2 and D20 rest on"."""
    names = [name for name in dir(loop_module) if "Replay" in name]
    assert names == []


def test_the_tick_result_is_the_only_thing_run_returns(harness):
    """§5.16 makes `TickResult` the phases' one product "so a phase never
    writes a record itself"."""
    assert isinstance(harness.tick().run(NOW_MS), TickResult)


def test_the_serve_root_layout_is_never_built_by_concatenation():
    """§5.8: `ServeRoot` "is the only thing that knows the directory
    shape — no other module builds a serve path by concatenation"."""
    source = (PACKAGE_DIR / "loop.py").read_text(encoding="utf-8")
    assert "os.path.join" not in source
    assert 'commands/' not in source


def test_the_metrics_the_loop_emits_are_declared_names(
    tmp_path, serve_document, release_manifest, clock, calls
):
    """§5.11.1: names are declared at construction from a closed table and
    "asking for an undeclared name raises `ProductionError`" — so a loop
    that invented one would fail where it is written."""
    made = make_harness(tmp_path, serve_document, release_manifest, clock, calls)
    try:
        made.loop().run()
        emitted = {name.split(".", 1)[1] for name in calls.names() if name.startswith("metric.")}
        from dskit.production.vocab import METRIC_NAMES

        assert emitted <= set(METRIC_NAMES)
        assert "ticks_total" in emitted
    finally:
        made.close()


def test_the_loop_writes_json_serialisable_bodies_only(harness):
    """Every §6 body is canonical JSON; a `Decimal` or a record object that
    reached one would refuse at append, and a body that refuses at append
    is a tick with no terminal record."""
    harness.loop().run()
    for envelope in harness.records():
        json.dumps(envelope["body"])
