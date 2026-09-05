"""`compose.py` — the composition root, and the one legal place a rung is read (§5.13.1).

Everything here is written from §5.13.1, §5.16 and §8's `test_compose.py`
line: *"every rung maps to exactly one collaborator set; the
AuthorityTable answers (origin, breaker) for every rung and refuses a
reduction outside reducing; paper cannot select LiveExecutor or
LiveAuthority; an incompatible combination refuses at construction; only
this module reads a rung"*.

Shadow and paper are composed against the REAL synthetic run in
`conftest.py` — a real `ServeDocument`, a real `ReleaseManifest` over a
real training run, a real ledger under `tmp_path`. Only the two live
rungs bring their own classes, because a live rung requires child
executor / accounting / approval / lease BY PATH and core ships none:
that is the point of the table.

`handlers_for` is tested here rather than in `test_main.py` because
`CommandProcessor` (§5.8) owns no verb logic and dispatches to handlers
`compose.py` injects — so the map from `CONTROL_PURPOSES` to owners is a
composition fact.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import pathlib
import uuid
from decimal import Decimal

import pytest

from dskit.production.accounting import Accounting, PaperAccounting
from dskit.production.alerts import AlertRouter
from dskit.production.arming import (
    ApprovalVerifier,
    Arming,
    ArmRequest,
    VerifiedPrincipal,
    authority_record,
)
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.breaker import Breaker
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
from dskit.production.clock import TestClock
from dskit.production.compose import (
    AuthorityTable,
    RUNG_TABLE,
    bundles_for,
    handlers_for,
)
from dskit.production.control import CommandProcessor, ControlInbox
from dskit.production.coordination import Lease, LeasePermit, ProcessLease
from dskit.production.decider import Decider, IntentRows
from dskit.production.document import ServeDocument
from dskit.production.executor import (
    Capabilities,
    LiveExecutor,
    PaperExecutor,
    ShadowExecutor,
    empty_ack,
)
from dskit.production.feed import EntrySourceFeed
from dskit.production.guards import GuardChain
from dskit.production.health import Health, Heartbeat, InstanceLock
from dskit.production.ids import ReleaseIdSource
from dskit.production.ledger import Checkpoint, JsonlLedger, ServeRoot
from dskit.production.leg import LiveAuthority, ReductionAuthority, SimulatedAuthority
from dskit.production.metrics import Metrics
from dskit.production.policy import ActionPolicy, TransitionPolicy
from dskit.production.readiness import Readiness
from dskit.production.reconcile import Reconciler
from dskit.production.records import ExecutionScope
from dskit.production.sessions import AlwaysOpen
from dskit.production.state import SeriesState
from dskit.production.verifier import SubmissionVerifier
from dskit.production.vocab import BREAKER_STATES, CONTROL_PURPOSES, LEG_ORIGINS, RUNGS
from tests.production.conftest import NOW_MS, SERIES_ID, UNIVERSE

PACKAGE_DIR = pathlib.Path(__file__).resolve().parents[2] / "dskit" / "production"

#: The rungs that reach a venue, restated here rather than imported from
#: `compose` — a test that sourced its expectation from its subject would
#: assert nothing (root CLAUDE.md, "deliberate independent restatement").
LIVE_RUNGS = ("live_limited", "live")

SIMULATED_RUNGS = ("shadow", "paper")

#: A 64-hex release hash for a synthetic command — the inbox refuses
#: anything that is not a sha256 digest.
COMMAND_RELEASE = canonical_hash("compose-command-release")

#: The core executor kind each simulated rung selects, and the class it
#: must resolve to. Restated, for the same reason.
EXPECTED_EXECUTOR = {"shadow": ("shadow", ShadowExecutor), "paper": ("paper", PaperExecutor)}

#: How a live document names its four child collaborators.
CHILD_EXECUTOR = "tests.production.test_compose:ChildExecutor"
CHILD_ACCOUNTING = "tests.production.test_compose:ChildAccounting"
CHILD_APPROVAL = "tests.production.test_compose:ChildApproval"
CHILD_LEASE = "tests.production.test_compose:ChildLease"


# --------------------------------------------------------------------------
# child collaborators — what a live rung requires and core cannot ship
# --------------------------------------------------------------------------


class ChildLease(Lease):
    """A fenced child lease: the only kind a live document may name."""

    LIVE_CAPABLE = True

    def __init__(self, params=None, *, clock=None):
        # `Lease` is the one seam ABC with no `cls(params)` __init__, so a
        # child keeps its own params rather than calling up.
        self.params = dict(params or {})
        self._clock = clock
        self._held = {}
        self._token = 0

    def acquire(self, scope, holder, ttl_ms):
        self._token += 1
        permit = LeasePermit(
            scope=scope, holder=holder, fencing_token=self._token, expires_ms=NOW_MS + ttl_ms
        )
        self._held[scope] = permit
        return permit

    def renew(self, permit):
        return self.acquire(permit.scope, permit.holder, 30_000)

    def current(self, scope):
        return self._held.get(scope)

    def release(self, permit):
        self._held.pop(permit.scope, None)


class ChildAccounting(Accounting):
    """A child accounting strategy — `paper` may not back a live document (D9)."""

    def __init__(self, params=None, **collaborators):
        super().__init__(params)
        self.collaborators = collaborators

    def value(self, state_view, quotes, at_ms):
        return Decimal(0)

    def classify(self, proposal, state):
        return "reduce"

    def snapshot(self, state_view, executor, quotes, at_ms, requirements, calendar):
        raise ProductionError(["ChildAccounting.snapshot is not exercised here"])


class ChildApproval(ApprovalVerifier):
    """A child verifier — `deny-all` may not back a live document (§5.6)."""

    LIVE_CAPABLE = True

    def verify(self, canonical_bytes, proof, purpose):
        self.check_purpose(purpose)
        return VerifiedPrincipal(
            id=f"principal-{proof.decode()}", proof_digest=hashlib.sha256(proof).hexdigest()
        )


class ChildExecutor(LiveExecutor):
    """A child venue subclass — the only constructible `LiveExecutor`."""

    _CAPS = Capabilities(
        tifs=("ioc",),
        market_orders=True,
        notional=False,
        positions="derived",
        settlements=False,
        stream=False,
        dedupe="rejects",
        units={"qty": "unit", "price": "quote", "cash": "quote"},
        position_model="netting",
        fencing="submit_token",
    )

    def capabilities(self):
        return self._CAPS

    def check(self, config):
        return ()

    def execution_scope(self):
        return ExecutionScope(venue="paper", account="strategy-a")

    def cancel(self, ref):
        return empty_ack(ref, NOW_MS, "not_sent", "test")

    def order(self, ref):
        return None

    def open_orders(self):
        return ()

    def fills(self, since_ms, cursor=None):
        return ((), None)

    def balances(self):
        return ()

    def _submit_native(self, intent, permit, timeout_ms):
        return empty_ack(intent.client_ref, NOW_MS, "not_sent", "test")


# --------------------------------------------------------------------------
# documents and composition
# --------------------------------------------------------------------------

#: The two guards D9 requires of a live-capable document.
LIVE_GUARDS = {
    "size": {
        "uses": "limit",
        "params": {"measure": "quantity", "bound": {"max": "100"}, "on_breach": "refuse"},
    },
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


def document_obj(serve_document, rung, tmp_path, overrides=None):
    """The conftest serve document re-graded to `rung`, rooted under `tmp_path`.

    `placement` is a non-identity section (D24), so re-rooting the ledger
    costs no identity; `rung` is graded, and `Decider.prepare` does not
    compare the release's placeholder `doc_hash`, so re-grading one real
    run is legitimate here.
    """
    obj = serve_document.to_obj()
    obj["rung"] = rung
    obj["placement"] = {"ledger_root": str(tmp_path / "serve")}
    # The conftest document declares `fixed-interval` with no params, and
    # `period_ms` has no default (§4.1: code holds no threshold), so a
    # composable document has to state one.
    obj["schedule"]["cadence"] = {"uses": "fixed-interval", "params": {"period_ms": 60_000}}
    if rung in LIVE_RUNGS:
        obj["guards"] = json.loads(json.dumps(LIVE_GUARDS))
        obj["accounting"]["uses"] = CHILD_ACCOUNTING
        obj["arming"]["approval"]["uses"] = CHILD_APPROVAL
        obj["coordination"]["lease"]["uses"] = CHILD_LEASE
        obj["execution"]["uses"] = CHILD_EXECUTOR
    else:
        obj["execution"]["uses"] = rung
    for path, value in (overrides or {}).items():
        cursor = obj
        keys = path.split(".")
        for key in keys[:-1]:
            cursor = cursor[key]
        cursor[keys[-1]] = value
    return obj


def document_at(serve_document, rung, tmp_path, overrides=None):
    """`document_obj`, parsed."""
    return ServeDocument.from_obj(document_obj(serve_document, rung, tmp_path, overrides))


class Composer:
    """Builds bundles and releases every lock and ledger it opened."""

    def __init__(self, release, clock):
        self.release = release
        self.clock = clock
        self.locks = []
        self.ledgers = []

    def build(self, document, **overrides):
        """Compose `document` over a freshly locked serve root."""
        serve = ServeRoot(document.placement.ledger_root, document.series_id)
        lock = InstanceLock(serve.lock_path)
        lock.acquire()
        self.locks.append(lock)
        kwargs = {
            "serve_root": serve,
            "secrets": {},
            "invocation": Invocation(
                armed=False, env_release_hash=None, once=True, max_ticks=1
            ),
            "process_id": f"proc-{len(self.locks)}",
            "clock": self.clock,
            "lock": lock,
            "journal_hook": lambda **kw: None,
        }
        kwargs.update(overrides)
        try:
            bundles = bundles_for(document, self.release, None, **kwargs)
        except BaseException:
            lock.release()
            self.locks.remove(lock)
            raise
        self.ledgers.append(bundles[5].ledger)
        return bundles

    def close(self):
        """Close every ledger and release every lock, in reverse order."""
        for ledger in reversed(self.ledgers):
            ledger.close()
        for lock in reversed(self.locks):
            lock.release()
        self.ledgers.clear()
        self.locks.clear()


@pytest.fixture
def composer(release_manifest, clock):
    """A `Composer` over the real synthetic release, cleaned up at teardown."""
    made = Composer(release_manifest, clock)
    yield made
    made.close()


@pytest.fixture
def shadow_document(serve_document, tmp_path):
    """The conftest document at `shadow`, rooted under `tmp_path`."""
    return document_at(serve_document, "shadow", tmp_path)


@pytest.fixture
def shadow_bundles(shadow_document, composer):
    """The seven bundles for the real synthetic run at shadow."""
    return composer.build(shadow_document)


# ==========================================================================
# The table — §5.13.1's closed rung -> collaborator set
# ==========================================================================


def test_the_rung_table_has_exactly_one_row_per_rung():
    """§5.13.1: the table is CLOSED over `vocab.RUNGS`. A rung with no row
    falls through to whatever `bundles_for` happened to do; a row with no
    rung is a collaborator set nothing can select."""
    assert tuple(sorted(RUNG_TABLE)) == tuple(sorted(RUNGS))


def test_every_row_names_the_five_collaborator_families_section_5_13_1_lists():
    """The table is `rung -> {executor, accounting, authority, approval,
    coordination}`. A family missing from a row is a family nothing
    selects by rung — exactly the hole `paper` selecting a `LiveExecutor`
    would come through."""
    for rung, row in RUNG_TABLE.items():
        for family in ("executor", "accounting", "authority", "approval", "coordination"):
            assert hasattr(row, family), f"{rung} row has no {family}"


def test_every_rows_authority_answers_both_leg_origins():
    """The authority axis is `(rung, origin)`, not rung alone (§5.13.1), so
    every row answers both `LEG_ORIGINS` members."""
    for rung, row in RUNG_TABLE.items():
        assert tuple(sorted(row.authority)) == tuple(sorted(LEG_ORIGINS)), rung


@pytest.mark.parametrize("rung", SIMULATED_RUNGS)
def test_a_simulated_rung_selects_its_own_executor_paper_accounting_deny_all_and_a_process_lease(
    rung,
):
    """Naming the exact core kind, rather than leaving the slot open, is
    what makes "paper can never select a `LiveExecutor`" structural rather
    than a convention `bundles_for` has to remember."""
    row = RUNG_TABLE[rung]
    assert row.executor == EXPECTED_EXECUTOR[rung][0]
    assert row.accounting == "paper"
    assert row.approval == "deny-all"
    assert row.coordination == "process"


@pytest.mark.parametrize("rung", SIMULATED_RUNGS)
def test_a_simulated_rung_mints_through_a_simulated_authority_for_both_origins(rung):
    """D10: below `live_limited` no live permit exists to mint, for either
    origin — a proven reduction at paper is still simulated."""
    row = RUNG_TABLE[rung]
    assert row.authority["model"] is SimulatedAuthority
    assert row.authority["reduction"] is SimulatedAuthority


@pytest.mark.parametrize("rung", LIVE_RUNGS)
def test_a_live_rung_names_no_core_kind_for_any_of_the_four_child_families(rung):
    """§5.6/D9: a live document names a child executor, accounting,
    approval verifier and lease BY PATH. A core kind in any of those four
    slots at a live rung is the default-deny hole D9 closed."""
    row = RUNG_TABLE[rung]
    assert row.executor is None
    assert row.accounting is None
    assert row.approval is None
    assert row.coordination is None


@pytest.mark.parametrize("rung", LIVE_RUNGS)
def test_a_live_rung_mints_live_for_model_and_reduction_for_reduction(rung):
    """§5.13.1: `LiveAuthority` for a model leg; `ReductionAuthority` for a
    reduction leg, which reserves its single-use right first."""
    row = RUNG_TABLE[rung]
    assert row.authority["model"] is LiveAuthority
    assert row.authority["reduction"] is ReductionAuthority


def test_no_simulated_rung_can_reach_a_live_class_through_the_table():
    """§8's line, directly: paper cannot select `LiveExecutor` or
    `LiveAuthority`. Reading the table is the whole proof, because the
    table is the only route from a rung to a collaborator."""
    for rung in SIMULATED_RUNGS:
        row = RUNG_TABLE[rung]
        assert LiveAuthority not in row.authority.values()
        assert ReductionAuthority not in row.authority.values()
        assert row.executor in ("shadow", "paper")


# ==========================================================================
# bundles_for — the real synthetic run at shadow and paper
# ==========================================================================


def test_bundles_for_returns_the_seven_bundles_in_section_5_16s_order(shadow_bundles):
    """§5.16 states the order and `LegPipeline` takes six of them
    positionally, so a member that moved would silently swap two
    collaborators."""
    assert len(shadow_bundles) == 7
    kinds = (Schedule, Data, Decision, Safety, Execution, Recording, Observability)
    assert tuple(type(bundle) for bundle in shadow_bundles) == kinds


def test_the_schedule_bundle_carries_the_documents_calendar_cadence_and_overrun(
    shadow_bundles, clock
):
    """`Schedule{clock, calendar, cadence, overrun}` (§5.16), each resolved
    through its §4.3 registry from the document's own selector."""
    schedule = shadow_bundles[0]
    assert schedule.clock is clock
    assert isinstance(schedule.calendar, AlwaysOpen)
    assert isinstance(schedule.cadence, FixedInterval)
    assert isinstance(schedule.overrun, Overrun)


def test_the_data_bundle_carries_a_prepared_decider_and_the_feed_it_binds(shadow_bundles):
    """`Data{feed, decider}`. The feed cannot be built before the decider
    has prepared — it binds `decider.contract` and `decider.feed_spec` —
    so composition, not the loop, is what runs the base pass."""
    data = shadow_bundles[1]
    assert isinstance(data.decider, Decider)
    assert isinstance(data.feed, EntrySourceFeed)
    assert data.decider.contract is not None, "bundles_for must call Decider.prepare"
    assert data.decider.feed_spec is not None
    assert data.decider.serving_hash is not None


def test_the_deciders_proposer_is_the_documents_and_is_reachable_through_data(shadow_bundles):
    """§5.16: "the `Decider` owns the configured `Proposer`, which is how
    `Tick.candidates`/`quotes`/`propose` reach it"."""
    assert isinstance(shadow_bundles[1].decider.proposer, IntentRows)


def test_the_decision_bundle_carries_a_guard_chain_and_the_monitor_map(shadow_bundles):
    """`Decision{guards, monitors}`. An empty guard map is a PRESENT chain,
    not an absent one — `bundles.py` refuses only `None`."""
    decision = shadow_bundles[2]
    assert isinstance(decision.guards, GuardChain)
    assert dict(decision.monitors) == {}


def test_the_safety_bundle_carries_all_eight_members_section_5_16_names(shadow_bundles):
    """`Safety{breaker, arming, authorities, readiness, invocation,
    action_policy, transition_policy, submission_verifier}` — every one,
    because a `LegPipeline` reads six of the eight."""
    safety = shadow_bundles[3]
    assert isinstance(safety.breaker, Breaker)
    assert isinstance(safety.arming, Arming)
    assert isinstance(safety.authorities, AuthorityTable)
    assert isinstance(safety.readiness, Readiness)
    assert isinstance(safety.invocation, Invocation)
    assert isinstance(safety.action_policy, ActionPolicy)
    assert isinstance(safety.transition_policy, TransitionPolicy)
    assert isinstance(safety.submission_verifier, SubmissionVerifier)


def test_shadow_composes_a_shadow_executor_paper_accounting_and_a_process_lease(shadow_bundles):
    """The table's shadow row, realised: the objects, not the names."""
    execution = shadow_bundles[4]
    assert isinstance(execution.executor, ShadowExecutor)
    assert isinstance(execution.accounting, PaperAccounting)
    assert isinstance(execution.lease, ProcessLease)
    assert execution.resilience.transport is not None


def test_the_recording_bundle_carries_the_ledger_fold_inbox_reconciler_checkpoint_hook_and_ids(
    shadow_bundles,
):
    """`Recording{ledger, state, inbox, reconciler, checkpoint,
    journal_hook, id_source}` — the seven §5.16 names."""
    recording = shadow_bundles[5]
    assert isinstance(recording.ledger, JsonlLedger)
    assert isinstance(recording.state, SeriesState)
    assert isinstance(recording.inbox, ControlInbox)
    assert isinstance(recording.reconciler, Reconciler)
    assert isinstance(recording.checkpoint, Checkpoint)
    assert callable(recording.journal_hook)
    assert isinstance(recording.id_source, ReleaseIdSource)


def test_the_fold_is_attached_to_the_ledger_so_every_append_folds_once(shadow_bundles):
    """§5.8.1: `SeriesState` is the sole fold, and it is the LEDGER that
    feeds it — a loop that folded appends itself would be a second folder
    and the two would drift on the first record it forgot."""
    recording = shadow_bundles[5]
    before = recording.state.head()
    recording.ledger.append({"kind": "process", "id": "process:probe", "body": {"event": "start"}})
    assert recording.state.head()[0] == before[0] + 1


def test_the_observability_bundle_carries_metrics_alerts_health_and_the_heartbeat(shadow_bundles):
    """`Observability{metrics, alerts, health, heartbeat}` (§5.16)."""
    observability = shadow_bundles[6]
    assert isinstance(observability.metrics, Metrics)
    assert isinstance(observability.alerts, AlertRouter)
    assert isinstance(observability.health, Health)
    assert isinstance(observability.heartbeat, Heartbeat)


def test_the_alert_router_is_wired_to_the_ledger_so_only_it_appends_alert_records(shadow_bundles):
    """R19/§5.11: `AlertRouter.process` is the sole appender of the §6
    `alert` record, so composition must hand it the ledger — and then
    `start()` must refuse, because a worker thread that could append would
    make D23 a convention rather than a structure."""
    with pytest.raises(ProductionError):
        shadow_bundles[6].alerts.start()


def test_the_ledger_uses_the_held_instance_lock_rather_than_taking_a_second_one(
    shadow_document, shadow_bundles
):
    """R18: ONE lock. It is taken before the ledger opens and handed to
    `JsonlLedger(..., lock=)`; a ledger that took its own flock would make
    the single-instance guarantee two guarantees that can disagree."""
    serve = ServeRoot(shadow_document.placement.ledger_root, shadow_document.series_id)
    assert shadow_bundles[5].ledger is not None
    second = InstanceLock(serve.lock_path)
    with pytest.raises(ProductionError):
        second.acquire()


def test_paper_composes_a_paper_executor(serve_document, tmp_path, composer):
    """The paper row, realised on the same real run — the rung is the only
    thing that changed, which is D2's whole claim."""
    bundles = composer.build(document_at(serve_document, "paper", tmp_path))
    assert isinstance(bundles[4].executor, PaperExecutor)
    assert isinstance(bundles[3].authorities.for_origin("model", "active"), SimulatedAuthority)


def test_the_composed_authority_table_is_the_rungs_and_answers_by_origin(shadow_bundles):
    """`Safety.authorities` is a table, not one object: the leg reads it by
    `origin` and never asks what rung it is."""
    table = shadow_bundles[3].authorities
    assert isinstance(table.for_origin("model", "active"), SimulatedAuthority)
    assert isinstance(table.for_origin("reduction", "reducing"), SimulatedAuthority)


# ==========================================================================
# bundles_for — an incompatible combination refuses at construction
# ==========================================================================


def test_a_shadow_document_naming_the_paper_executor_refuses(
    serve_document, tmp_path, composer
):
    """§5.13.1: an incompatible combination refuses AT CONSTRUCTION. The
    rung selects the executor; a document that names another one is two
    declarations that disagree, and serving the wrong one silently is how
    a paper fill becomes a shadow non-event."""
    document = document_at(serve_document, "shadow", tmp_path, {"execution.uses": "paper"})
    with pytest.raises(ProductionError) as excinfo:
        composer.build(document)
    assert any("execution" in problem for problem in excinfo.value.problems)


def test_a_paper_document_naming_a_live_executor_class_refuses(
    serve_document, tmp_path, composer
):
    """"`paper` can never select a `LiveExecutor`" (§5.13.1), stated as the
    test that would fail if the table were consulted only for the
    authority."""
    document = document_at(serve_document, "paper", tmp_path, {"execution.uses": CHILD_EXECUTOR})
    with pytest.raises(ProductionError) as excinfo:
        composer.build(document)
    assert any("execution" in problem for problem in excinfo.value.problems)


def test_every_incompatible_selector_is_reported_in_one_raise(
    serve_document, tmp_path, composer
):
    """Validation ACCUMULATES (root CLAUDE.md): an operator fixing a
    composition should see all the disagreements at once, not one per
    run."""
    document = document_at(
        serve_document,
        "shadow",
        tmp_path,
        {
            "execution.uses": "paper",
            "accounting.uses": "recorded",
            "coordination.lease.uses": CHILD_LEASE,
        },
    )
    with pytest.raises(ProductionError) as excinfo:
        composer.build(document)
    assert len(excinfo.value.problems) >= 3


@pytest.mark.parametrize(
    "path,value", [("execution.uses", "paper"), ("coordination.lease.uses", "process")]
)
def test_a_live_rung_refuses_the_two_core_defaults_the_document_cannot_catch(
    serve_document, tmp_path, composer, path, value
):
    """D9 makes the DOCUMENT refuse `paper` accounting and the `deny-all`
    verifier at a live rung, but it checks neither the executor nor the
    lease. Composition is the object that would otherwise BUILD them, so
    it is where a simulated executor and an unfenced `process` lease have
    to refuse (§5.7.2: "a local lock or unfenced lease can never be
    configured as sufficient")."""
    document = document_at(serve_document, "live_limited", tmp_path, {path: value})
    with pytest.raises(ProductionError) as excinfo:
        composer.build(document)
    assert any(path.split(".")[0] in problem for problem in excinfo.value.problems)


@pytest.mark.parametrize("path,value", [("accounting.uses", "paper"), ("arming.approval.uses", "deny-all")])
def test_the_other_two_core_defaults_never_reach_composition_at_a_live_rung(
    serve_document, tmp_path, path, value
):
    """The same default-deny, one layer earlier: D9 refuses these while the
    document is being read, so a live document carrying them cannot even
    be constructed to compose."""
    obj = document_obj(serve_document, "live_limited", tmp_path, {path: value})
    with pytest.raises(ProductionError):
        ServeDocument.from_obj(obj)


@pytest.mark.parametrize("rung", LIVE_RUNGS)
def test_a_live_rung_composes_the_child_classes_and_the_live_authorities(
    serve_document, tmp_path, composer, rung
):
    """The whole live path except the socket: child executor, accounting,
    verifier and fenced lease by path, and the two live authorities the
    table selected."""
    bundles = composer.build(document_at(serve_document, rung, tmp_path))
    assert isinstance(bundles[4].executor, ChildExecutor)
    assert isinstance(bundles[4].accounting, ChildAccounting)
    assert isinstance(bundles[4].lease, ChildLease)
    assert isinstance(bundles[3].authorities.for_origin("model", "active"), LiveAuthority)
    assert isinstance(
        bundles[3].authorities.for_origin("reduction", "reducing"), ReductionAuthority
    )


def test_the_live_executor_and_the_verifier_are_one_gate(serve_document, tmp_path, composer):
    """`LiveExecutor.submit` delegates to `SubmissionVerifier.verify_and_call`
    and `Safety.submission_verifier` is what the leg's gate reads; two
    gates would mean a disable only half the process observed. Composition
    is where that cycle is resolved — the executor needs the gate, and the
    gate needs the executor."""
    bundles = composer.build(document_at(serve_document, "live_limited", tmp_path))
    bundles[4].executor.reset_after_reconcile()
    assert bundles[3].submission_verifier.disabled is False


# ==========================================================================
# AuthorityTable — (origin, breaker) for every rung
# ==========================================================================


def authorities_for(rung):
    """One `AuthorityTable` per rung, over stand-in authority instances."""
    row = RUNG_TABLE[rung]
    built = {origin: cls.__new__(cls) for origin, cls in row.authority.items()}
    return AuthorityTable(rung, built), row


@pytest.mark.parametrize("rung", RUNGS)
@pytest.mark.parametrize("breaker", BREAKER_STATES)
def test_the_table_answers_a_model_origin_in_every_breaker_state(rung, breaker):
    """A model leg's authority is a property of the rung; whether it MAY
    act in this breaker state is `ActionPolicy`'s answer, not the table's.
    Refusing here too would give one fact two owners."""
    table, row = authorities_for(rung)
    assert isinstance(table.for_origin("model", breaker), row.authority["model"])


@pytest.mark.parametrize("rung", RUNGS)
def test_the_table_answers_a_reduction_origin_while_reducing(rung):
    """D12: a reduction is legal while `reducing`, at every rung — a paper
    flatten mints a simulated permit, a live one reserves a right."""
    table, row = authorities_for(rung)
    assert isinstance(table.for_origin("reduction", "reducing"), row.authority["reduction"])


@pytest.mark.parametrize("rung", RUNGS)
@pytest.mark.parametrize("breaker", ("active", "halted"))
def test_the_table_refuses_a_reduction_outside_reducing(rung, breaker):
    """§5.13.1: "refuses a reduction outside `reducing`". `reducing` is a
    state the running loop ENTERS, so the table cannot decide it from the
    document — which is why `for_origin` takes the breaker at all."""
    table, _row = authorities_for(rung)
    with pytest.raises(ProductionError):
        table.for_origin("reduction", breaker)


def test_the_table_refuses_an_origin_outside_the_vocabulary():
    """`origin` is a declared value; a lookup on an undeclared one must
    refuse rather than answer `None` and be minted from later."""
    table, _row = authorities_for("shadow")
    with pytest.raises(ProductionError):
        table.for_origin("operator", "active")


def test_the_table_refuses_a_breaker_state_outside_the_vocabulary():
    """A misspelled breaker state must not read as "not reducing" and
    quietly refuse the one path that de-risks a live book."""
    table, _row = authorities_for("live")
    with pytest.raises(ProductionError):
        table.for_origin("reduction", "REDUCING")


def test_the_table_refuses_construction_for_a_rung_outside_the_vocabulary():
    """Composition is the one place a rung is read; an unknown one must
    refuse where it is written."""
    with pytest.raises(ProductionError):
        AuthorityTable("live_unlimited", {})


def test_the_table_refuses_construction_missing_an_origin():
    """Every origin the leg may ask for must be answerable at construction,
    not at the moment a live leg needs one."""
    with pytest.raises(ProductionError):
        AuthorityTable("shadow", {"model": SimulatedAuthority.__new__(SimulatedAuthority)})


# ==========================================================================
# handlers_for — every CONTROL_PURPOSES member, wired to its owner
# ==========================================================================


class Owner:
    """Records `(name, args, kwargs)` for a stand-in collaborator."""

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            answer = self.answer
            return answer(name, *args, **kwargs) if callable(answer) else answer

        return call

    def named(self, name):
        """Every recorded call under `name`, as `(args, kwargs)` pairs."""
        return [(a, k) for n, a, k in self.calls if n == name]


def refusing(*args, **kwargs):
    """A stand-in owner that refuses every call."""
    raise ProductionError(["refused by the owner"])


def command(purpose, payload=None, proof=b"proof", queued_at_ms=NOW_MS - 5_000):
    """One consumed inbox command, in `ControlInbox`'s stored shape."""
    body = dict(payload or {})
    return {
        "request_id": str(uuid.uuid4()),
        "purpose": purpose,
        "payload": body,
        "payload_digest": canonical_hash(body),
        "release_hash": COMMAND_RELEASE,
        "proof": proof,
        "queued_at_ms": queued_at_ms,
    }


def owner_bundles(bundles, **owners):
    """The seven bundles with named `Safety`/`Execution`/`Recording` members replaced."""
    schedule, data, decision, safety, execution, recording, observability = bundles
    for name, value in owners.items():
        if name in {f.name for f in dataclasses.fields(Safety)}:
            safety = dataclasses.replace(safety, **{name: value})
        elif name in {f.name for f in dataclasses.fields(Execution)}:
            execution = dataclasses.replace(execution, **{name: value})
        else:
            recording = dataclasses.replace(recording, **{name: value})
    return (schedule, data, decision, safety, execution, recording, observability)


def test_handlers_for_covers_every_control_purpose(shadow_document, shadow_bundles):
    """§5.8: the writer is the sole applier of every control verb. A
    purpose with no handler is silently `rejected` by `CommandProcessor`,
    so a missing entry is an operator act that vanishes into a receipt."""
    handlers = handlers_for(shadow_document, shadow_bundles)
    assert tuple(sorted(handlers)) == tuple(sorted(CONTROL_PURPOSES))


def test_every_handler_is_callable_and_accepted_by_the_command_processor(
    shadow_document, shadow_bundles, clock
):
    """`CommandProcessor` refuses a key outside `CONTROL_PURPOSES` at
    construction, so composing the two proves the map is the one the
    processor will dispatch through."""
    handlers = handlers_for(shadow_document, shadow_bundles)
    recording = shadow_bundles[5]
    processor = CommandProcessor(
        recording.inbox, recording.ledger, recording.state, handlers, clock
    )
    assert isinstance(processor, CommandProcessor)


def test_a_handler_answers_the_processors_three_part_contract(shadow_document, shadow_bundles):
    """`handler(command, view) -> (records, status, reason)` — the shape
    `CommandProcessor` enforces on every answer."""
    handlers = handlers_for(shadow_document, shadow_bundles)
    view = shadow_bundles[5].state.snapshot()
    records, status, reason = handlers["reconcile"](command("reconcile"), view)
    assert isinstance(records, tuple)
    assert status in ("applied", "rejected")
    assert isinstance(reason, str)


def test_the_halt_handler_trips_the_breaker_with_a_halting_cause(shadow_document, shadow_bundles):
    """§5.6/D12: `halt` is a transition into `halted`, and the breaker is
    its one owner — a handler that appended a `trip` body itself would
    bypass the cancel sweep and the transition policy."""
    breaker = Owner(answer=lambda *a, **k: 1)
    handlers = handlers_for(shadow_document, owner_bundles(shadow_bundles, breaker=breaker))
    _records, status, _reason = handlers["halt"](
        command("halt", {"reason": "operator"}), shadow_bundles[5].state.snapshot()
    )
    assert status == "applied"
    assert breaker.named("trip"), "the halt handler must call Breaker.trip"
    _args, kwargs = breaker.named("trip")[0]
    assert kwargs.get("cause") == "halt"


def test_the_reduce_handler_calls_breaker_reduce_with_the_operators_credentials(
    shadow_document, shadow_bundles
):
    """D12: `reduce` is authenticated — an unsigned reduce is not a
    transition, so the request id and both digests must reach the owner."""
    breaker = Owner(answer=lambda *a, **k: 1)
    handlers = handlers_for(shadow_document, owner_bundles(shadow_bundles, breaker=breaker))
    cmd = command("reduce")
    handlers["reduce"](cmd, shadow_bundles[5].state.snapshot())
    args, kwargs = breaker.named("reduce")[0]
    assert cmd["request_id"] in args + tuple(kwargs.values())


def test_the_resume_handler_acknowledges_a_named_trip(shadow_document, shadow_bundles):
    """§5.6: `reset` is refused without a trip id, so the handler must pass
    the operator's `--acknowledge TRIP` through rather than inventing one."""
    breaker = Owner(answer=lambda *a, **k: 1)
    handlers = handlers_for(shadow_document, owner_bundles(shadow_bundles, breaker=breaker))
    handlers["resume"](
        command("resume", {"acknowledges_trip_id": "trip:operator:1"}),
        shadow_bundles[5].state.snapshot(),
    )
    args, kwargs = breaker.named("reset")[0]
    assert "trip:operator:1" in args + tuple(kwargs.values())


def test_the_disarm_handler_returns_the_armings_authority_record(shadow_document, shadow_bundles):
    """`Arming.disarm` RETURNS an `authority` body (it appends nothing), so
    the handler is what turns it into a record for the processor to append
    inside the command's one barrier."""
    arming = Owner(answer=lambda *a, **k: {"authority_id": "a" * 64, "event": "disarm"})
    handlers = handlers_for(shadow_document, owner_bundles(shadow_bundles, arming=arming))
    records, status, _reason = handlers["disarm"](
        command("disarm"), shadow_bundles[5].state.snapshot()
    )
    assert status == "applied"
    assert [record["kind"] for record in records] == ["authority"]
    assert arming.named("disarm")


def test_an_issue_and_the_disarm_that_ends_it_are_two_record_ids(
    shadow_document, shadow_bundles
):
    """§6 keys the idempotency index by `id` ALONE, and R9 makes every
    producer qualify its id with the kind. Both events of one authority
    therefore have to name the EVENT too: sharing `authority:<id>` would
    make the disarm an append of a changed payload under a reused id, which
    `Ledger.append` refuses — an operator's safe demotion would be
    impossible for the life of the arm."""
    authority_id = "a" * 64
    disarmed = Owner(answer=lambda *a, **k: {"authority_id": authority_id, "event": "disarm"})
    handlers = handlers_for(shadow_document, owner_bundles(shadow_bundles, arming=disarmed))
    records, _status, _reason = handlers["disarm"](
        command("disarm"), shadow_bundles[5].state.snapshot()
    )
    issue = authority_record({"authority_id": authority_id, "event": "issue"})
    assert issue["id"] != records[0]["id"]
    assert (issue["id"], records[0]["id"]) == (
        f"authority:{authority_id}:issue",
        f"authority:{authority_id}:disarm",
    )


def test_the_reconcile_handler_runs_the_reconciler_over_the_documents_scope(
    shadow_document, shadow_bundles
):
    """§5.9: `run(state_view, executor, scope)` — and the scope is the
    document's `coordination.scope`, never the executor's own answer,
    which is the thing being checked."""
    reconciler = Owner(answer=lambda name, *a, **k: "none")
    handlers = handlers_for(shadow_document, owner_bundles(shadow_bundles, reconciler=reconciler))
    handlers["reconcile"](command("reconcile"), shadow_bundles[5].state.snapshot())
    args, _kwargs = reconciler.named("run")[0]
    assert args[2] == shadow_document.coordination.scope


def test_the_reconcile_handler_applies_the_documents_mismatch_policy(
    shadow_document, shadow_bundles
):
    """§5.9: `on_mismatch` admits only `halt | refuse`, and `apply_policy`
    is its one owner — the handler asks it and, on `halt`, trips."""
    breaker = Owner(answer=lambda *a, **k: 1)
    reconciler = Owner(answer=lambda name, *a, **k: "halt" if name == "apply_policy" else None)
    handlers = handlers_for(
        shadow_document, owner_bundles(shadow_bundles, reconciler=reconciler, breaker=breaker)
    )
    handlers["reconcile"](command("reconcile"), shadow_bundles[5].state.snapshot())
    assert reconciler.named("apply_policy")
    assert breaker.named("trip"), "an on_mismatch halt must reach the breaker"


def test_the_reconcile_handler_refuses_sends_on_refuse_without_halting(
    shadow_document, shadow_bundles
):
    """§5.9: `on_mismatch` admits `halt | refuse`, and `refuse` is not a
    spelling of `halt` — it stops submissions and leaves the series
    active. The operator verb takes the same one path the loop does, or
    `refuse` is computed and dropped here exactly as it was there."""
    breaker, verifier = Owner(answer=lambda *a, **k: 1), Owner()
    reconciler = Owner(answer=lambda name, *a, **k: "refuse" if name == "apply_policy" else None)
    handlers = handlers_for(
        shadow_document,
        owner_bundles(
            shadow_bundles, reconciler=reconciler, breaker=breaker, submission_verifier=verifier
        ),
    )
    handlers["reconcile"](command("reconcile"), shadow_bundles[5].state.snapshot())
    assert verifier.named("refuse_until_reconciled"), "on_mismatch refuse must stop sends"
    assert not breaker.named("trip"), "refuse is not a halt"


def test_the_reconcile_handler_re_enables_sends_when_the_run_is_clean(
    shadow_document, shadow_bundles
):
    """D13/§5.14: reconciliation is what resolves an ambiguous reference,
    so an operator `reconcile` that comes back clean is what re-enables a
    gate an `unknown` disabled — the operator's whole reason for running
    one by hand."""
    breaker, verifier = Owner(answer=lambda *a, **k: 1), Owner()
    reconciler = Owner(answer=lambda name, *a, **k: "none" if name == "apply_policy" else None)
    handlers = handlers_for(
        shadow_document,
        owner_bundles(
            shadow_bundles, reconciler=reconciler, breaker=breaker, submission_verifier=verifier
        ),
    )
    handlers["reconcile"](command("reconcile"), shadow_bundles[5].state.snapshot())
    assert verifier.named("reset_after_reconcile")
    assert not breaker.named("trip")


def test_the_ready_handler_evaluates_and_records_the_readiness_verdict(
    shadow_document, shadow_bundles
):
    """§5.13: `ready` is the only verb that writes a `readiness` record and
    `Readiness.record` is what appends it — so the handler evaluates then
    records, and never builds the body itself."""
    readiness = Owner(answer=lambda name, *a, **k: 7 if name == "record" else object())
    handlers = handlers_for(shadow_document, owner_bundles(shadow_bundles, readiness=readiness))
    handlers["ready"](command("ready"), shadow_bundles[5].state.snapshot())
    assert readiness.named("evaluate")
    assert readiness.named("record")


def test_the_adopt_handler_stamps_known_at_ms_from_the_consumed_commands_queued_at(
    shadow_document, shadow_bundles
):
    """§6, verbatim: "`known_at_ms` is the CONSUMED COMMAND's
    `queued_at_ms`, never `clock.now_ms()` at the handler — a
    crash-replayed `adopt` must produce a byte-identical payload or
    `Ledger.append` refuses it as a changed payload under a reused id". A
    handler that let the clock stamp it turns a safe replay into a refusal
    and an operator's second attempt into a second bank."""
    reconciler = Owner(answer=lambda *a, **k: ("cash_flow:1", "adoption:1"))
    handlers = handlers_for(shadow_document, owner_bundles(shadow_bundles, reconciler=reconciler))
    cmd = command(
        "adopt",
        {"break_ids": ["b1"], "flow_kind": "deposit", "external": True},
        queued_at_ms=NOW_MS - 60_000,
    )
    handlers["adopt"](cmd, shadow_bundles[5].state.snapshot())
    _args, kwargs = reconciler.named("adopt")[0]
    assert kwargs.get("known_at_ms") == cmd["queued_at_ms"]


def test_the_adopt_handler_passes_the_operators_flow_kind_and_external_flag(
    shadow_document, shadow_bundles
):
    """§6: `flow_kind` and `external` "come from the operator's proof and
    never default to `external: true`" — an adopted deposit that defaulted
    would be indistinguishable from trading profit for the series' life."""
    reconciler = Owner(answer=lambda *a, **k: ())
    handlers = handlers_for(shadow_document, owner_bundles(shadow_bundles, reconciler=reconciler))
    cmd = command("adopt", {"break_ids": ["b1"], "flow_kind": "withdrawal", "external": False})
    handlers["adopt"](cmd, shadow_bundles[5].state.snapshot())
    _args, kwargs = reconciler.named("adopt")[0]
    assert kwargs.get("flow_kind") == "withdrawal"
    assert kwargs.get("external") is False


def test_an_adopt_command_that_omits_its_flow_kind_is_rejected(shadow_document, shadow_bundles):
    """Defaulting the kind is the defect §6 names; refusing is the fix."""
    reconciler = Owner(answer=lambda *a, **k: ())
    handlers = handlers_for(shadow_document, owner_bundles(shadow_bundles, reconciler=reconciler))
    _records, status, _reason = handlers["adopt"](
        command("adopt", {"break_ids": ["b1"]}), shadow_bundles[5].state.snapshot()
    )
    assert status == "rejected"
    assert not reconciler.named("adopt")


def test_the_arm_request_handler_verifies_through_arming_and_appends_no_second_request(
    shadow_document, shadow_bundles
):
    """`Arming.request` RETURNS a `control_request` body and appends
    nothing; `CommandProcessor` has already appended that record on
    receipt. A handler that returned it again would put two records with
    one semantic id on the chain."""
    arming = Owner(answer=lambda *a, **k: {"request_id": "x", "purpose": "arm_request"})
    handlers = handlers_for(shadow_document, owner_bundles(shadow_bundles, arming=arming))
    payload = ArmRequest(
        release_hash=COMMAND_RELEASE,
        rung="shadow",
        allowlist=tuple(UNIVERSE),
        limits_overlay={},
        requested_until_ms=NOW_MS + 3_600_000,
        request_proof=b"maker",
    ).to_obj()
    records, status, _reason = handlers["arm_request"](
        command("arm_request", payload, proof=b"maker"), shadow_bundles[5].state.snapshot()
    )
    assert status == "applied"
    assert records == ()
    assert arming.named("request")


def test_the_arm_approval_handler_asks_readiness_and_the_sentinel_for_the_world(
    shadow_document, shadow_bundles
):
    """R23: `Arming.approve` refuses unless the breaker is active, the HALT
    sentinel is absent and — at a live rung — readiness says GO. The
    verdict comes from `Readiness.verdict_for`, its ONE owner, and the
    sentinel from `Breaker.halt_sentinel_present`; the handler is what
    asks them, at one instant, so the three answers describe one moment.

    The maker's raw proof comes back out of the consumed command's own
    receipt, never out of a ledger record — §6 keeps proof bytes off the
    chain, and `approve` re-verifies the maker."""
    arming = Owner(
        answer=lambda *a, **k: ({"authority_id": "a" * 64, "event": "issue"}, object())
    )
    readiness = Owner(answer=lambda *a, **k: "go")
    breaker = Owner(answer=lambda *a, **k: False)
    handlers = handlers_for(
        shadow_document,
        owner_bundles(shadow_bundles, arming=arming, readiness=readiness, breaker=breaker),
    )
    inbox = shadow_bundles[5].inbox
    maker = str(uuid.uuid4())
    request = ArmRequest(
        release_hash=COMMAND_RELEASE,
        rung="shadow",
        allowlist=tuple(UNIVERSE),
        limits_overlay={},
        requested_until_ms=NOW_MS + 3_600_000,
        request_proof=b"maker",
    )
    inbox.queue(
        {
            "request_id": maker,
            "purpose": "arm_request",
            "payload": request.to_obj(),
            "payload_digest": canonical_hash(request.to_obj()),
            "release_hash": COMMAND_RELEASE,
            "proof": b"maker",
        }
    )
    inbox.mark_applied(maker, {"status": "applied", "reason": "", "emitted_record_ids": []})
    cmd = command(
        "arm_approval",
        {"request_id": maker, "request_digest": request.request_digest()},
        proof=b"checker",
    )
    handlers["arm_approval"](cmd, shadow_bundles[5].state.snapshot())
    assert readiness.named("verdict_for"), "the verdict has one owner"
    assert breaker.named("halt_sentinel_present")
    _args, kwargs = arming.named("approve")[0]
    assert kwargs.get("readiness_verdict") == "go"
    assert kwargs.get("sentinel_present") is False


def test_an_arm_approval_naming_an_unknown_request_is_rejected(shadow_document, shadow_bundles):
    """A checker cannot approve a request the spool never accepted, and the
    handler must say so rather than construct an `ArmRequest` out of the
    approval's own payload — that would be a checker approving itself."""
    arming = Owner(answer=lambda *a, **k: ({"authority_id": "a" * 64}, object()))
    handlers = handlers_for(shadow_document, owner_bundles(shadow_bundles, arming=arming))
    _records, status, _reason = handlers["arm_approval"](
        command("arm_approval", {"request_id": str(uuid.uuid4()), "request_digest": "d" * 64}),
        shadow_bundles[5].state.snapshot(),
    )
    assert status == "rejected"
    assert not arming.named("approve")


def test_a_handlers_production_error_reaches_the_processor_rather_than_being_swallowed(
    shadow_document, shadow_bundles
):
    """§5.8: "A handler that raises `ProductionError` rejects its command
    and appends nothing of its own". `CommandProcessor` owns that
    translation, so composition must let the error out rather than
    catching it and answering `applied`."""
    breaker = Owner(answer=refusing)
    handlers = handlers_for(shadow_document, owner_bundles(shadow_bundles, breaker=breaker))
    with pytest.raises(ProductionError):
        handlers["halt"](
            command("halt", {"reason": "operator"}), shadow_bundles[5].state.snapshot()
        )


def test_the_flatten_and_execute_purposes_have_handlers(shadow_document, shadow_bundles):
    """§5.8: `execute-flatten` "requires an active ready loop, and is moved
    to `applied` when its cycle is *queued*, not when it completes". A
    missing handler would reject it and remove the emergency de-risking
    path entirely."""
    handlers = handlers_for(shadow_document, shadow_bundles)
    for purpose in ("flatten_request", "flatten_approval", "execute_flatten"):
        assert callable(handlers[purpose])


# ==========================================================================
# Only this module reads a rung
# ==========================================================================


def _module_source(name):
    return (PACKAGE_DIR / name).read_text(encoding="utf-8")


def test_the_rung_table_lives_in_compose_and_no_other_module_names_it():
    """D2's structural half: the branch has a NAMED OWNER rather than being
    relocated. `test_purity.py` bans the spelling everywhere but here;
    this bans the mechanism everywhere but here, which is the half a
    spelling ban cannot reach."""
    offenders = [
        path.name
        for path in sorted(PACKAGE_DIR.glob("*.py"))
        if path.name != "compose.py" and "RUNG_TABLE" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"the rung table leaked into {offenders}"


def test_compose_holds_a_rung_keyed_table_rather_than_a_chain_of_ifs():
    """"A table lookup keyed by a declared value is not the branch D2
    forbids" — but only while it IS a table. An `if rung ==` chain inside
    `bundles_for` would still pass the purity gate's exemption, so the
    positive claim is asserted here."""
    tree = ast.parse(_module_source("compose.py"))
    assigned = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "RUNG_TABLE" in assigned


def test_loop_py_never_reads_the_rung_table_or_compares_a_rung():
    """§5.13.1: "`ServeLoop` is therefore the scheduler, not the
    composition root". The loop receives bundles already built, so it has
    nothing to select and nothing to ask."""
    source = _module_source("loop.py")
    assert "RUNG_TABLE" not in source
    compared = [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Compare)
        for operand in [node.left, *node.comparators]
        if (isinstance(operand, ast.Name) and operand.id == "rung")
        or (isinstance(operand, ast.Attribute) and operand.attr == "rung")
    ]
    assert not compared, f"loop.py compares a rung at line(s) {compared}"


def test_composes_public_surface_is_exactly_what_main_needs():
    """Root CLAUDE.md: `__all__` plus the `_` prefix IS the API contract, and
    a composition root nothing may import is one `__main__` re-implements."""
    import dskit.production.compose as module

    assert set(module.__all__) >= {"AuthorityTable", "RUNG_TABLE", "bundles_for", "handlers_for"}
    assert not [name for name in module.__all__ if name.startswith("_")]


def test_the_seven_bundles_are_frozen_so_a_collaborator_cannot_be_swapped_after_composition(
    shadow_bundles,
):
    """D2 rests on the objects being chosen ONCE, at construction. A
    mutable bundle would let a later caller put a `LiveExecutor` into a
    paper process without the table ever being consulted."""
    for bundle in shadow_bundles:
        assert dataclasses.is_dataclass(bundle)
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(bundle, dataclasses.fields(bundle)[0].name, None)


def test_composition_binds_the_series_genesis_before_the_first_append(
    shadow_bundles, shadow_document
):
    """§5.8/D15: `series.json` binds the series before the first append and
    a mismatch refuses, so the serve root must exist and be bound by the
    time the ledger is handed over."""
    serve = ServeRoot(shadow_document.placement.ledger_root, shadow_document.series_id)
    assert pathlib.Path(serve.series_path).is_dir()
    assert json.loads(pathlib.Path(serve.genesis_path).read_text())["series_id"] == SERIES_ID


def test_a_second_composition_over_the_same_series_reuses_the_genesis(
    serve_document, release_manifest, tmp_path, clock
):
    """§5.8/D15: process starts and stops continue ONE chain, so composing
    a second process over the same series must not rewrite the genesis it
    is bound to."""
    document = document_at(serve_document, "shadow", tmp_path)
    first = Composer(release_manifest, clock)
    bundles = first.build(document)
    serve = ServeRoot(document.placement.ledger_root, document.series_id)
    genesis = pathlib.Path(serve.genesis_path).read_text()
    assert bundles[5].ledger is not None
    first.close()
    second = Composer(release_manifest, TestClock(start_ms=NOW_MS))
    try:
        second.build(document)
        assert pathlib.Path(serve.genesis_path).read_text() == genesis
    finally:
        second.close()
