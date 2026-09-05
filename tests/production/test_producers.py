"""§5.16's producer table, as a check rather than as prose.

Four rounds of design review found the same defect four times: a sentence
naming a collaborator no bundle carried, or a record field nothing produced.
This file is the closure check §5.16 asks for, and its second assertion is the
one that bites.

* **Resolution.** Every producer below is a dotted path — `recording.id_source.
  plan_id`, `bindings.entry_batch.data_asof_ms` — resolved segment by segment
  against the BUILT classes. A renamed collaborator, a bundle member that
  moved, a verb that was rewritten: each fails here, in the file that claims
  the field has a producer.
* **Completeness.** For every record the table walks, the `PRODUCERS` keys for
  that record equal `{f.name for f in dataclasses.fields(R)}` exactly. A field
  added to a record without a producer fails; a producer naming a field that
  no longer exists fails. That is the half an earlier draft of §5.16 lacked,
  and the half that catches the defect class it was written for.
* **The spelling rule.** Nine names are simultaneously a document section and
  a bundle member (`accounting`, `arming`, `feed`, `guards`, `health`,
  `heartbeat`, `monitors`, `readiness`, `resilience`), plus `schedule` and
  `execution`, which name a bundle TYPE. That collision is what let a leg be
  specified to read `schedule.max_venue_skew_ms` while holding a `Schedule`
  bundle with no such member. §5.16 enforces the rule on the PACKAGE SOURCE:
  no module reads a document key except through the `ServeDocument` object. It
  is deliberately not enforced on the proposal document — a shipped test must
  never depend on a file under `docs/`, which would make the proposal a
  permanent build artifact of the package.

Two things this file is careful about. It resolves against CLASSES, never
against a live serve, so it needs no fixture and cannot be satisfied by a
mock: `member_of` accepts a class attribute, a dataclass field, or a
`self.<name>` the class assigns, which is what an instance of that class
actually carries. And the sentinel is used only where §5.16 names a step
rather than an attribute (`result` is "step 4's own verdict"); every sentinel
is listed in one place below so the count cannot creep.
"""

import ast
import dataclasses
import inspect
import pathlib
import textwrap

import pytest

import dskit.production as production
from dskit.production import bundles, document as document_module
from dskit.production.accounting import Accounting
from dskit.production.arming import Arming, ArmingState
from dskit.production.breaker import Breaker
from dskit.production.clock import Clock
from dskit.production.control import ControlInbox
from dskit.production.coordination import Lease, LeasePermit
from dskit.production.decider import Decider, Proposer
from dskit.production.document import ServeDocument
from dskit.production.executor import SubmittingExecutor
from dskit.production.feed import Feed, FeedResult
from dskit.production.guards import GuardChain
from dskit.production.health import Health, Heartbeat
from dskit.production.ids import IdSource
from dskit.production.ledger import JsonlLedger
from dskit.production.leg import (
    LegBindings,
    LegEvaluation,
    LegResult,
    ReductionBinding,
)
from dskit.production.loop import Tick
from dskit.production.readiness import Readiness
from dskit.production.records import (
    AccountState,
    ActPermit,
    Candidate,
    DecisionPlan,
    EntryBatch,
    Intent,
    PolicyRequest,
    Proposal,
    Provenance,
    QuoteSet,
    ReductionIntent,
    TickResult,
)
from dskit.production.release import ReleaseManifest
from dskit.production.resilience import ResiliencePolicies
from dskit.production.sessions import Calendar
from dskit.production.state import (
    ArmingProjection,
    ReadinessProjection,
    ReductionProjection,
    SeriesState,
    StateView,
    TickState,
)

#: The producer of a field the table names a STEP for rather than an
#: attribute: "step 4's own verdict", "`run`, keyed by `LEG_LATENCY_BUCKETS`",
#: "whichever phase refused". Resolution skips these; completeness does not,
#: so the field still has to be accounted for.
STEP = "<step>"

#: The first segment of every path: the seven bundles of §5.16, the leg's
#: bindings, the two values every constructor takes, and the records the table
#: names directly.
ROOTS = {
    "schedule": bundles.Schedule,
    "data": bundles.Data,
    "decision": bundles.Decision,
    "safety": bundles.Safety,
    "execution": bundles.Execution,
    "recording": bundles.Recording,
    "observability": bundles.Observability,
    "bindings": LegBindings,
    "document": ServeDocument,
    "release": ReleaseManifest,
    "AccountState": AccountState,
    "Candidate": Candidate,
    "DecisionPlan": DecisionPlan,
    "EntryBatch": EntryBatch,
    "Intent": Intent,
    "LegEvaluation": LegEvaluation,
    "LegResult": LegResult,
    "Provenance": Provenance,
    "QuoteSet": QuoteSet,
    "ReductionBinding": ReductionBinding,
    "ReductionIntent": ReductionIntent,
    "TickState": TickState,
    "Tick": Tick,
}

#: What a prefix HOLDS, where the holder cannot say. A bundle annotates every
#: member `object` on purpose (`bundles.py` exists to break the §10 build
#: cycle and so validates presence only), and a verb's answer has no
#: annotation at all — so the table states it, and the walk continues into
#: the named class. Every entry is itself checked: the prefix must resolve.
TYPES = {
    "schedule.clock": Clock,
    "schedule.calendar": Calendar,
    "data.feed": Feed,
    "data.feed.pull": FeedResult,
    "data.decider": Decider,
    "data.decider.proposer": Proposer,
    "data.decider.read_entry": EntryBatch,
    "decision.guards": GuardChain,
    "safety.arming": Arming,
    "safety.arming.current": ArmingState,
    "safety.readiness": Readiness,
    # Every rung's row names a venue that can submit (§5.13.1): the read and
    # cancel base is what a RECOVERING caller holds, not what a leg does.
    "execution.executor": SubmittingExecutor,
    "execution.accounting": Accounting,
    "execution.accounting.snapshot": AccountState,
    "execution.lease": Lease,
    "execution.lease.current": LeasePermit,
    "recording.id_source": IdSource,
    "recording.inbox": ControlInbox,
    "recording.state": SeriesState,
    "recording.state.snapshot": StateView,
    "recording.state.snapshot.arming": ArmingProjection,
    "recording.state.snapshot.readiness": ReadinessProjection,
    "recording.state.snapshot.reduction": ReductionProjection,
    "observability.health": Health,
    "bindings.entry_batch": EntryBatch,
    "bindings.proposal": Proposal,
    "bindings.quotes": QuoteSet,
    "bindings.reduction": ReductionBinding,
    "bindings.release": ReleaseManifest,
    "bindings.state": TickState,
    "bindings.state.view": StateView,
    "Intent.proposal": Proposal,
    "LegEvaluation.account": AccountState,
    "ReductionBinding.signed": ReductionIntent,
}

#: The paths that appear in several rows, named once so a rename moves one
#: line. `client_ref` and `authority_id` genuinely have two producers — a
#: model leg's and a reduction leg's — and the walk resolves both.
_BATCH = "bindings.entry_batch"
_QUOTES = "bindings.quotes"
_ACCOUNT = "LegEvaluation.account"
_CLIENT_REF = "recording.id_source.client_ref + recording.id_source.flatten_client_ref"
_AUTHORITY_ID = (
    "safety.arming.current.authority_id + recording.state.snapshot.reduction.authority_id"
)
_PROPOSE = "data.decider.proposer.proposals"

#: §5.16's table, field by field. `(record, field) -> dotted producer path`;
#: several producers are joined by " + " where the table names more than one
#: (a dict assembled from three sources, or a model leg's producer and a
#: reduction leg's).
PRODUCERS = {
    # --- DecisionPlan — all eighteen fields ------------------------------
    ("DecisionPlan", "plan_id"): "recording.id_source.plan_id",
    ("DecisionPlan", "inputs_asof_ms"): f"{_BATCH}.data_asof_ms",
    ("DecisionPlan", "inputs_digest"): f"{_BATCH}.inputs_digest",
    ("DecisionPlan", "coverage_digest"): f"{_BATCH}.coverage_digest",
    ("DecisionPlan", "quote_asof_ms"): f"{_QUOTES}.min_asof_ms",
    ("DecisionPlan", "quote_digest"): f"{_QUOTES}.quote_digest",
    ("DecisionPlan", "evidence_asof_ms"): f"{_ACCOUNT}.asof_ms",
    ("DecisionPlan", "evidence_digest"): f"{_ACCOUNT}.evidence_digest",
    ("DecisionPlan", "provenance_digests"): (
        f"{_BATCH}.inputs_digest + bindings.head_digest + bindings.proposal.id"
    ),
    ("DecisionPlan", "original"): "bindings.proposal",
    ("DecisionPlan", "final"): "LegEvaluation.final",
    ("DecisionPlan", "findings"): "LegEvaluation.findings",
    ("DecisionPlan", "gate_results"): "LegEvaluation.gate_results",
    ("DecisionPlan", "scope_verdict"): "LegEvaluation.scope_verdict",
    ("DecisionPlan", "risk_effect"): "LegEvaluation.risk_effect",
    ("DecisionPlan", "risk_version"): "LegEvaluation.risk_version",
    ("DecisionPlan", "risk_state_digest"): "LegEvaluation.risk_state_digest",
    ("DecisionPlan", "result"): STEP,
    # --- LegResult — §6's decision.legs[] is written from it -------------
    ("LegResult", "result"): STEP,
    ("LegResult", "leg_id"): "bindings.leg_id",
    ("LegResult", "plan_id"): "DecisionPlan.plan_id",
    ("LegResult", "plan_digest"): "DecisionPlan.decision_plan_digest",
    ("LegResult", "final"): "DecisionPlan.final",
    ("LegResult", "client_ref"): _CLIENT_REF,
    ("LegResult", "intent"): "Intent",
    ("LegResult", "ack"): "execution.executor.submit",
    ("LegResult", "findings"): "LegEvaluation.findings",
    ("LegResult", "leg_latency_ms"): STEP,
    # --- ActPermit — copies, and the bindings that are not copies --------
    ("ActPermit", "plan_id"): "DecisionPlan.plan_id",
    ("ActPermit", "decision_plan_digest"): "DecisionPlan.decision_plan_digest",
    ("ActPermit", "client_ref"): "Intent.client_ref",
    ("ActPermit", "valid_until_ms"): STEP,
    ("ActPermit", "authority_id"): _AUTHORITY_ID,
    ("ActPermit", "release_hash"): "Intent.release_hash",
    ("ActPermit", "intent_digest"): "Intent.intent_digest",
    ("ActPermit", "instrument"): "Intent.proposal.instrument",
    ("ActPermit", "risk_effect"): "DecisionPlan.risk_effect",
    ("ActPermit", "inputs_asof_ms"): "Intent.inputs_asof_ms",
    ("ActPermit", "inputs_digest"): "Intent.inputs_digest",
    ("ActPermit", "coverage_digest"): "Intent.coverage_digest",
    ("ActPermit", "quote_asof_ms"): "Intent.quote_asof_ms",
    ("ActPermit", "quote_digest"): "Intent.quote_digest",
    ("ActPermit", "evidence_asof_ms"): "Intent.evidence_asof_ms",
    ("ActPermit", "evidence_digest"): "Intent.evidence_digest",
    ("ActPermit", "authority_scope_digest"): (
        "safety.arming.current.allowlist + safety.arming.effective_bounds"
        " + recording.state.snapshot.reduction.rights"
    ),
    ("ActPermit", "reduction_right_digest"): "bindings.reduction.digest",
    ("ActPermit", "risk_version"): "Intent.risk_version",
    ("ActPermit", "risk_state_digest"): "Intent.risk_state_digest",
    ("ActPermit", "readiness_digest"): "recording.state.snapshot.readiness.readiness_digest",
    ("ActPermit", "readiness_until_ms"): "recording.state.snapshot.readiness.valid_until_ms",
    ("ActPermit", "lease_scope"): "execution.lease.current.scope",
    ("ActPermit", "fencing_token"): "execution.lease.current.fencing_token",
    ("ActPermit", "safety_epoch_digest"): STEP,
    ("ActPermit", "checked_at_ms"): "schedule.clock.now_ms",
    # --- TickResult ------------------------------------------------------
    ("TickResult", "tick_id"): "recording.id_source.next_tick_id",
    ("TickResult", "status"): STEP,
    ("TickResult", "data_asof_ms"): "EntryBatch.data_asof_ms",
    ("TickResult", "coverage_digest"): "EntryBatch.coverage_digest",
    ("TickResult", "inputs_digest"): "EntryBatch.inputs_digest",
    ("TickResult", "decision_plan_ids"): "LegResult.plan_id",
    ("TickResult", "legs"): "LegResult",
    ("TickResult", "findings"): "LegResult.findings",
    ("TickResult", "observed_at_ms"): "schedule.clock.now_ms",
    ("TickResult", "nav"): "execution.accounting.value",
    ("TickResult", "latency_ms"): STEP,
    ("TickResult", "leg_latency_ms"): "LegResult.leg_latency_ms",
    ("TickResult", "refusal_reason"): STEP,
    ("TickResult", "error"): STEP,
    ("TickResult", "feed"): (
        "data.feed.pull.status + data.feed.pull.acq_id + data.feed.pull.records_added"
        " + data.feed.pull.source_config_hash + EntryBatch.required_keys_digest"
        " + EntryBatch.watermarks_by_key + EntryBatch.coverage_digest"
    ),
    # --- TickState — assembled by Tick.run after the account phase -------
    ("TickState", "view"): "recording.state.snapshot",
    ("TickState", "account"): "execution.accounting.snapshot",
    ("TickState", "feed_status"): "data.feed.pull.status",
    ("TickState", "feed_ages"): "Tick.coverage",
    ("TickState", "calendar"): "schedule.calendar",
    ("TickState", "entry_batch"): "data.decider.read_entry",
    # --- LegBindings — assembled by Tick.run per proposal ----------------
    ("LegBindings", "proposal"): f"{_PROPOSE} + ReductionIntent.proposal",
    ("LegBindings", "origin"): STEP,
    ("LegBindings", "entry_batch"): "data.decider.read_entry",
    ("LegBindings", "head_digest"): "data.decider.evaluate",
    ("LegBindings", "quotes"): "Tick.quotes",
    ("LegBindings", "state"): "TickState",
    ("LegBindings", "requirements"): "decision.guards.requirements",
    ("LegBindings", "reduction"): "ReductionBinding",
    ("LegBindings", "release"): "release",
    ("LegBindings", "rung"): "document.rung",
    ("LegBindings", "tick_id"): "recording.id_source.next_tick_id",
    ("LegBindings", "leg_id"): "recording.id_source.leg_id",
    ("LegBindings", "leg_index"): STEP,
    # --- Intent — step 5 --------------------------------------------------
    ("Intent", "client_ref"): _CLIENT_REF,
    ("Intent", "decision_plan_id"): "DecisionPlan.plan_id",
    ("Intent", "decision_plan_digest"): "DecisionPlan.decision_plan_digest",
    ("Intent", "proposal"): "LegEvaluation.final",
    ("Intent", "created_ms"): "schedule.clock.now_ms",
    ("Intent", "authority_id"): _AUTHORITY_ID,
    ("Intent", "release_hash"): "bindings.release.release_hash",
    ("Intent", "inputs_asof_ms"): f"{_BATCH}.data_asof_ms",
    ("Intent", "inputs_digest"): f"{_BATCH}.inputs_digest",
    ("Intent", "coverage_digest"): f"{_BATCH}.coverage_digest",
    ("Intent", "quote_asof_ms"): f"{_QUOTES}.min_asof_ms",
    ("Intent", "quote_digest"): f"{_QUOTES}.quote_digest",
    ("Intent", "evidence_asof_ms"): f"{_ACCOUNT}.asof_ms",
    ("Intent", "evidence_digest"): f"{_ACCOUNT}.evidence_digest",
    ("Intent", "risk_version"): f"{_ACCOUNT}.risk_version",
    ("Intent", "risk_state_digest"): f"{_ACCOUNT}.risk_digest",
    # --- PolicyRequest — assembled by the caller of ActionPolicy.permits --
    ("PolicyRequest", "operation"): STEP,
    ("PolicyRequest", "risk_effect"): "LegEvaluation.risk_effect",
    ("PolicyRequest", "rung"): "bindings.rung",
    ("PolicyRequest", "breaker"): "recording.state.snapshot.breaker",
    ("PolicyRequest", "health"): "observability.health.state",
    ("PolicyRequest", "readiness"): "safety.readiness.verdict_for",
    ("PolicyRequest", "authority"): "recording.state.snapshot.arming + bindings.reduction",
    ("PolicyRequest", "origin"): "bindings.origin",
    ("PolicyRequest", "pending_control"): (
        "recording.state.snapshot.pending_control + recording.inbox.pending"
    ),
    # --- LegEvaluation — steps 1-3, threaded ------------------------------
    ("LegEvaluation", "original"): "bindings.proposal",
    ("LegEvaluation", "final"): "decision.guards.check_all",
    ("LegEvaluation", "findings"): "decision.guards.check_all",
    ("LegEvaluation", "gate_results"): STEP,
    ("LegEvaluation", "scope_verdict"): (
        "safety.arming.apply_scope + decision.guards.check_authority_scope"
    ),
    ("LegEvaluation", "account"): "execution.accounting.snapshot",
    ("LegEvaluation", "risk_effect"): "execution.accounting.classify",
    ("LegEvaluation", "risk_version"): "execution.accounting.snapshot.risk_version",
    ("LegEvaluation", "risk_state_digest"): "execution.accounting.snapshot.risk_digest",
    # --- StateView — every member is a projection of the fold -------------
    **{
        ("StateView", name): "recording.state.snapshot"
        for name in (
            "positions", "working", "pending", "balances", "decision_history", "breaker",
            "arming", "readiness", "guard_holds", "reduction", "pending_control",
            "risk_version", "head_seq", "head_hash",
        )
    },
    # --- Proposal — the proposer's own decision, plus its provenance ------
    ("Proposal", "id"): "Candidate.id",
    **{
        ("Proposal", name): _PROPOSE
        for name in (
            "instrument", "side", "qty", "notional", "limit", "tif", "expires_ms",
            "reference_price", "exposure", "direction", "confidence", "prediction",
            "baseline", "expected_value", "extra",
        )
    },
    **{
        ("Proposal", name): f"Provenance.{name}"
        for name in (
            "inputs_asof_ms", "inputs_digest", "coverage_digest", "quote_asof_ms",
            "quote_digest",
        )
    },
    # --- Provenance — from the EntryBatch and the QuoteSet -----------------
    ("Provenance", "inputs_asof_ms"): "EntryBatch.data_asof_ms",
    ("Provenance", "inputs_digest"): "EntryBatch.inputs_digest",
    ("Provenance", "coverage_digest"): "EntryBatch.coverage_digest",
    ("Provenance", "quote_asof_ms"): "QuoteSet.min_asof_ms",
    ("Provenance", "quote_digest"): "QuoteSet.quote_digest",
}

#: The twelve records §5.16 walks in phase 1. Extending the table extends the
#: test, which is the intended way to add one — the phase-2 rows
#: (`Outcome`, `Report`, `ParityDiff`, the alert-state records) join this
#: tuple when the classes they name exist.
WALKED = (
    DecisionPlan,
    LegResult,
    ActPermit,
    TickResult,
    TickState,
    LegBindings,
    Intent,
    PolicyRequest,
    LegEvaluation,
    StateView,
    Proposal,
    Provenance,
)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def self_assigned(cls):
    """Return the names a class assigns to `self` in its own body."""
    try:
        source = inspect.getsource(cls)
    except (OSError, TypeError):  # pragma: no cover - every class here has source
        return set()
    tree = ast.parse(textwrap.dedent(source))
    found = set()
    for node in ast.walk(tree):
        targets = getattr(node, "targets", []) or ([node.target] if hasattr(node, "target") else [])
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                found.add(target.attr)
    return found


def member_of(cls, name):
    """Say whether an instance of `cls` carries `name`: attribute, field or assignment."""
    if cls is ServeDocument:
        # `ServeDocument.__getattr__` reads the validated view, so §4.1's
        # grammar — not the class — is what says a document carries a key.
        return name in document_module._GRAMMAR.keys
    if hasattr(cls, name):
        return True
    if dataclasses.is_dataclass(cls) and name in {f.name for f in dataclasses.fields(cls)}:
        return True
    return name in self_assigned(cls)


def resolve(path):
    """Walk one dotted path, returning the problem it hit or None.

    Parameters
    ----------
    path : str
        A dotted producer path whose first segment is a `ROOTS` key.

    Returns
    -------
    str or None
        The first problem, naming the segment and the class it was not a
        member of, or None when the whole path resolved.
    """
    segments = path.split(".")
    root = segments[0]
    if root not in ROOTS:
        return f"{path}: {root!r} is not a declared producer root"
    current, prefix = ROOTS[root], root
    for segment in segments[1:]:
        if current is None:
            return f"{path}: nothing declares what {prefix!r} holds, so {segment!r} cannot resolve"
        if not member_of(current, segment):
            return f"{path}: {current.__name__} has no member {segment!r}"
        prefix = f"{prefix}.{segment}"
        current = TYPES.get(prefix)
    return None


@pytest.mark.parametrize(
    "record,field",
    sorted(key for key, path in PRODUCERS.items() if path != STEP),
    ids=lambda value: value,
)
def test_every_producer_path_resolves_against_the_built_classes(record, field):
    """§5.16's resolution half: "every dotted path resolves by `getattr` chain
    against the built classes, so a renamed collaborator fails". A row whose
    producer moved is a row that no longer says where the field comes from."""
    problems = [resolve(part) for part in PRODUCERS[(record, field)].split(" + ")]
    assert [problem for problem in problems if problem] == []


def test_every_declared_type_hangs_off_a_path_that_resolves():
    """`TYPES` states what a bundle member or a verb's answer holds, because
    neither can say for itself. An entry whose own prefix stopped resolving
    would silently make every path through it resolve against nothing."""
    for prefix in TYPES:
        head, _, last = prefix.rpartition(".")
        assert resolve(head) is None, prefix
        holder = ROOTS[head] if head in ROOTS else TYPES.get(head)
        assert holder is not None, prefix
        assert member_of(holder, last), prefix


def test_the_resolver_refuses_a_renamed_collaborator():
    """The detector is the assertion above, so it is checked on a rename:
    a resolver that stopped walking would pass every row forever."""
    assert resolve("recording.id_source.plan_id") is None
    assert "no member" in resolve("recording.id_source.plan_identifier")
    assert "no member" in resolve("recording.identity_source")
    assert "not a declared producer root" in resolve("scheduler.clock")


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("record", WALKED, ids=lambda cls: cls.__name__)
def test_every_field_of_every_walked_record_has_exactly_one_producer(record):
    """§5.16's completeness half, "the half an earlier draft lacked": a field
    added to a record without a producer fails, and a producer naming a field
    that no longer exists fails. Prose arithmetic in that section has been
    wrong three times, which is why nothing here counts."""
    named = {field for (rec, field) in PRODUCERS if rec == record.__name__}
    assert named == {f.name for f in dataclasses.fields(record)}


def test_the_table_walks_exactly_the_records_it_declares():
    """No row may name a record the walk does not cover: a record with a
    partial table would look complete while proving nothing."""
    assert {rec for rec, _field in PRODUCERS} == {cls.__name__ for cls in WALKED}


def test_the_sentinel_is_used_only_where_the_table_names_a_step():
    """The sentinel is the one way to satisfy completeness without resolving,
    so its uses are listed here: each is a field §5.16 attributes to a step's
    own verdict rather than to a collaborator."""
    assert {key for key, path in PRODUCERS.items() if path == STEP} == {
        ("DecisionPlan", "result"),
        ("LegResult", "result"),
        ("LegResult", "leg_latency_ms"),
        ("ActPermit", "valid_until_ms"),
        ("ActPermit", "safety_epoch_digest"),
        ("TickResult", "status"),
        ("TickResult", "latency_ms"),
        ("TickResult", "refusal_reason"),
        ("TickResult", "error"),
        ("LegBindings", "origin"),
        ("LegBindings", "leg_index"),
        ("LegEvaluation", "gate_results"),
        ("PolicyRequest", "operation"),
    }


def test_serve_root_is_the_one_producer_that_is_not_a_bundle_member():
    """§5.16 names the exception so it stays a decision: `ServeRoot` is a
    construction-time dependency of `Ledger`, `Breaker` and `ControlInbox`
    rather than a bundle member."""
    members = {
        field.name
        for cls in ROOTS.values()
        if dataclasses.is_dataclass(cls) and cls.__module__.endswith("bundles")
        for field in dataclasses.fields(cls)
    }
    assert "serve_root" not in members
    for cls in (ControlInbox, JsonlLedger, Breaker):
        assert "serve_root" in inspect.signature(cls.__init__).parameters, cls.__name__


# ---------------------------------------------------------------------------
# The spelling rule, on the package source
# ---------------------------------------------------------------------------

#: The package's own source tree.
PACKAGE_DIR = pathlib.Path(production.__file__).parent

#: `document.py` builds the `ServeDocument`, so it is the one module that
#: reads the raw grammar; every other module goes through the object.
DOCUMENT_MODULE = "document.py"

#: A name that holds the document itself. `self._document`, `self.document`
#: and a local `doc` are the three spellings the package uses.
DOCUMENT_NAMES = ("document", "_document", "doc")

#: Where a section name and a collaborator member collide, the scan cannot
#: tell a document read from a bundle read, so those keys are not enforced —
#: `schedule.clock` is both `document.schedule.clock` (a selector) and
#: `schedule.clock` (the injected `Clock`). Naming the classes here keeps the
#: exclusion mechanical: it shrinks the moment a collaborator drops a member.
COLLABORATORS = {
    "accounting": Accounting,
    "arming": Arming,
    "feed": Feed,
    "guards": GuardChain,
    "health": Health,
    "heartbeat": Heartbeat,
    "monitors": dict,
    "readiness": Readiness,
    "resilience": ResiliencePolicies,
    "schedule": bundles.Schedule,
    "execution": bundles.Execution,
}


def document_pairs():
    """Return every `(section, key)` the document grammar declares, minus the collisions."""
    grammar = document_module._GRAMMAR
    pairs = {
        (section, key)
        for section in grammar.keys
        for key in getattr(grammar.shapes[section], "keys", ())
    }
    for section, cls in COLLABORATORS.items():
        members = set(dir(cls))
        if dataclasses.is_dataclass(cls):
            members |= {field.name for field in dataclasses.fields(cls)}
        pairs -= {(section, key) for key in members}
    return pairs


PAIRS = document_pairs()


def _holder(node):
    """Return the name an expression is read off, or None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _document_locals(tree):
    """Return locals bound to a document section (`schedule = self._document.schedule`)."""
    bound = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if isinstance(node.value, ast.Attribute) and _holder(node.value.value) in DOCUMENT_NAMES:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound[target.id] = node.value.attr
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "to_obj"
            and _holder(node.value.func.value) in DOCUMENT_NAMES
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound[target.id] = "*"
    return bound


def document_key_reads(tree, pairs=None):
    """Return `(line, section, key)` for every document key read outside a `ServeDocument`."""
    pairs = PAIRS if pairs is None else pairs
    bound = _document_locals(tree)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and (_holder(node.value), node.attr) in pairs:
            section = _holder(node.value)
            if isinstance(node.value, ast.Name) and bound.get(node.value.id) == section:
                continue
            root = _holder(node.value.value) if isinstance(node.value, ast.Attribute) else None
            if root in DOCUMENT_NAMES:
                continue
            hits.append((node.lineno, section, node.attr))
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.value.slice, ast.Constant)
            and (node.value.slice.value, node.slice.value) in pairs
        ):
            base = node.value.value
            if isinstance(base, ast.Name) and bound.get(base.id) == "*":
                continue
            if (
                isinstance(base, ast.Call)
                and isinstance(base.func, ast.Attribute)
                and base.func.attr == "to_obj"
            ):
                continue
            hits.append((node.lineno, node.value.slice.value, node.slice.value))
    return sorted(hits)


def test_no_module_reads_a_document_key_except_through_the_document():
    """§5.16's spelling rule, on the package source: a document read is always
    `document.<section>.<key>`, a bundle read is always the bare member. The
    collision between the two is what let a leg be specified to read
    `schedule.max_venue_skew_ms` off a `Schedule` bundle that has no such
    member."""
    found = []
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        if path.name == DOCUMENT_MODULE:
            continue
        tree = ast.parse(path.read_text())
        found += [
            f"{path.name}:{line}: {section}.{key}"
            for line, section, key in document_key_reads(tree)
        ]
    assert found == []


def test_the_spelling_scan_catches_a_document_key_read_off_a_bundle():
    """The detector, on the exact defect §5.16 names — and on the JSON form,
    where a module reads the raw config instead of the object."""
    bundle_read = ast.parse("def gate(self):\n    return self.schedule.max_venue_skew_ms\n")
    assert document_key_reads(bundle_read) == [(2, "schedule", "max_venue_skew_ms")]
    raw_read = ast.parse('def gate(path):\n    return json.load(path)["schedule"]["max_staleness_ms"]\n')
    assert raw_read and document_key_reads(raw_read) == [(2, "schedule", "max_staleness_ms")]


def test_the_spelling_scan_passes_a_read_through_the_document():
    """Both sanctioned spellings: the attribute walk, and handing a whole
    section to a builder from `document.to_obj()`."""
    through = ast.parse(
        "def gate(self):\n"
        "    schedule = self._document.schedule\n"
        "    return (self._document.schedule.max_venue_skew_ms, schedule.max_staleness_ms)\n"
    )
    assert document_key_reads(through) == []
    sections = ast.parse(
        "def build(document):\n"
        "    sections = document.to_obj()\n"
        '    return sections["durability"]["fsync"]\n'
    )
    assert document_key_reads(sections) == []


def test_the_spelling_rule_still_covers_the_keys_that_matter():
    """The collision exclusion above is mechanical, so this pins that it did
    not empty the rule: the freshness budgets, the submit timeout and the
    valuation age are exactly the keys a leg or a verifier reads."""
    assert {
        ("schedule", "max_staleness_ms"),
        ("schedule", "max_quote_age_ms"),
        ("schedule", "max_venue_skew_ms"),
        ("schedule", "dead_after_ms"),
        ("execution", "submit_timeout_ms"),
        ("accounting", "max_valuation_age_ms"),
        ("coordination", "scope"),
        ("readiness", "valid_for_s"),
    } <= PAIRS
