"""`breaker.py` — the series breaker: trips, cooling-off, kill switch (§5.6).

The breaker state is the ledger fold and nothing else: `current(view)`
reads `StateView.breaker`, every transition is a §6 `trip` record that
crosses a barrier, and `breaker.json` is only a head-bound cache of that
projection — validated before `READY`, rebuilt when behind, refused when
ahead or divergent (D15).

Three rulings shape these tests:

* **D10/D12 — `TransitionPolicy` is the only door.** The breaker never
  decides for itself whether a transition is legal; it asks. So the
  policy is injected and the tests assert both what the breaker *asks*
  (from-state, to-state, cause, proof) and that a veto stops it dead
  with nothing appended. The table itself belongs to `test_policy.py`
  and is deliberately not restated here.
* **D12 — halt never flattens, and resume never re-arms.** A halt
  refuses submissions and best-effort cancels working orders, recording
  one of `CANCEL_OUTCOMES`. Leaving `active` revokes the ordinary arm,
  so a series that resumes is `active` **and unarmed** — a fresh
  maker-checker arm is required before it can act.
* **D12 — only a verified resume or flatten may retire `HALT`.** The
  sentinel is retired *before* the transition barrier, so a crash in
  between leaves the ledger folded `halted` and therefore unable to act.

Nothing here touches wall time: the clock is injected, cooling-off is
measured against the acknowledged trip's `recorded_at_ms`, and the fake
ledger folds into a real `SeriesState` so the assertions are about the
fold, not about a stub.
"""

import hashlib
import json
import os
from decimal import Decimal

import pytest

from dskit.production import vocab
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.breaker import Breaker
from dskit.production.document import ServeDocument
from dskit.production.ledger import ServeRoot
from dskit.production.records import Ack
from dskit.production.state import SeriesState
from tests.production.test_document import example_document, set_path

# ---------------------------------------------------------------------------
# Fixed material
# ---------------------------------------------------------------------------

SERIES_ID = "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1"
RELEASE_HASH = "b" * 64
GENESIS_HASH = "0" * 64
BASE_MS = 1_767_268_800_000
PROCESS_ID = "proc-1"

DIGEST_INPUTS = "1" * 64
DIGEST_COVERAGE = "2" * 64
DIGEST_QUOTE = "3" * 64
DIGEST_EVIDENCE = "4" * 64
DIGEST_RISK = "5" * 64
DIGEST_PLAN = "6" * 64
PRINCIPAL_DIGEST = "7" * 64
PROOF_DIGEST = "8" * 64

#: `document.lifecycle.cooling_off_s` in §4.1's illustration.
COOLING_OFF_S = 900
COOLING_OFF_MS = COOLING_OFF_S * 1000

#: The nine keys §6's `trip` body carries.
TRIP_BODY_KEYS = {
    "from",
    "to",
    "reason",
    "actor",
    "control_request_id",
    "principal_digest",
    "proof_digest",
    "acknowledged_trip_id",
    "cancel_outcome",
}

#: What a caller hands the ledger; the other nine are assigned (§6).
CALLER_KEYS = ("kind", "id", "body")

ACTOR = "operator"
HALT_REQUEST = "req-halt-1"
REDUCE_REQUEST = "req-reduce-1"
FLATTEN_REQUEST = "req-flatten-1"
RESUME_REQUEST = "req-resume-1"


# ---------------------------------------------------------------------------
# Local fakes — the collaborators the breaker is handed
# ---------------------------------------------------------------------------


class FakeClock:
    """The two `Clock` methods the breaker needs; no wall time."""

    def __init__(self, ms=BASE_MS):
        self._ms = int(ms)

    def now_ms(self):
        return self._ms

    def monotonic(self):
        return self._ms / 1000.0

    def advance(self, ms):
        self._ms += int(ms)
        return self._ms


class FoldingLedger:
    """The `Ledger` surface the breaker uses, folding into a real state.

    `append` assigns the nine §6 fields, chains the hash and calls
    `SeriesState.apply` — which is what the real ledger does — so a test
    can assert against the fold rather than against a stub. `calls`
    records the order of `append` and `barrier` so "barriers after the
    append" is checkable.
    """

    def __init__(self, state, clock, series_id=SERIES_ID):
        self.state = state
        self.clock = clock
        self.series_id = series_id
        self.records = []
        self.calls = []
        self.seq = 0
        self.head_hash = GENESIS_HASH

    def append(self, record):
        assert isinstance(record, dict), record
        assert set(record) == set(CALLER_KEYS), sorted(record)
        assert isinstance(record["body"], dict), record["body"]
        self.seq += 1
        prev = self.head_hash
        digest = canonical_hash(record)
        env = {
            **record,
            "body": dict(record["body"]),
            "payload_digest": digest,
            "seq": self.seq,
            "series_id": self.series_id,
            "process_id": PROCESS_ID,
            "release_hash": RELEASE_HASH,
            "recorded_at_ms": self.clock.now_ms(),
            "schema_version": 1,
            "prev_hash": prev,
            "hash": hashlib.sha256((prev + digest).encode()).hexdigest(),
        }
        self.head_hash = env["hash"]
        self.records.append(env)
        self.calls.append(("append", record["kind"]))
        if self.state is not None:
            self.state.apply(env)
        return env["seq"]

    def append_many(self, records):
        return [self.append(record) for record in records]

    def barrier(self):
        self.calls.append(("barrier", None))

    def scan(self, kind=None, since_seq=0):
        return tuple(
            env
            for env in self.records
            if env["seq"] > since_seq and (kind is None or env["kind"] == kind)
        )

    def head(self):
        return (self.seq, self.head_hash)

    def kinds(self):
        return [env["kind"] for env in self.records]

    def trips(self):
        return [env for env in self.records if env["kind"] == "trip"]

    def last_trip(self):
        return self.trips()[-1]


class RecordingPolicy:
    """A `TransitionPolicy` stand-in that records what it was asked."""

    def __init__(self, allowed=True, reason=""):
        self.allowed = allowed
        self.reason = reason or ("permitted" if allowed else "transition_not_permitted")
        self.calls = []

    def permits(self, from_state, to_state, cause, proof):
        self.calls.append((from_state, to_state, cause, proof))
        return _Decision(self.allowed, self.reason)


class _Decision:
    """The `{allowed, reason}` pair `TransitionPolicy.permits` returns."""

    def __init__(self, allowed, reason):
        self.allowed = allowed
        self.reason = reason


class FakeExecutor:
    """`cancel_all()` answers with acks, or raises when told to."""

    def __init__(self, acks=(), raises=False):
        self.acks = tuple(acks)
        self.raises = raises
        self.cancel_all_calls = 0
        self.sentinel_seen = []

    def cancel_all(self):
        self.cancel_all_calls += 1
        if self.raises:
            raise RuntimeError("venue unreachable")
        return self.acks


def ack(status, client_ref="ref-1"):
    """An `Ack` carrying only the status the cancel outcome reads."""
    return Ack(
        client_ref=client_ref,
        venue_ref="v-1",
        status=status,
        ts_ms=BASE_MS + 10,
        filled_qty=Decimal("0"),
        avg_price=Decimal("0"),
        fee=Decimal("0"),
        reason="",
        native={},
    )


# ---------------------------------------------------------------------------
# §6 bodies used to drive the fold into the shapes the breaker reads
# ---------------------------------------------------------------------------


def proposal_obj(instrument="AAPL", qty="10"):
    return {
        "id": "cand-1",
        "instrument": instrument,
        "side": "buy",
        "qty": qty,
        "notional": None,
        "limit": "100",
        "tif": "gtc",
        "expires_ms": BASE_MS + 60_000,
        "reference_price": "100",
        "exposure": "1000",
        "direction": "long",
        "confidence": "0.5",
        "prediction": "0.01",
        "baseline": "0.0",
        "expected_value": "5",
        "inputs_asof_ms": BASE_MS,
        "inputs_digest": DIGEST_INPUTS,
        "coverage_digest": DIGEST_COVERAGE,
        "quote_asof_ms": BASE_MS,
        "quote_digest": DIGEST_QUOTE,
        "extra": {},
    }


def intent_body(client_ref="ref-1"):
    return {
        "client_ref": client_ref,
        "decision_plan_id": "plan-1",
        "decision_plan_digest": DIGEST_PLAN,
        "proposal": proposal_obj(),
        "created_ms": BASE_MS,
        "authority_id": "auth-1",
        "inputs_asof_ms": BASE_MS,
        "inputs_digest": DIGEST_INPUTS,
        "coverage_digest": DIGEST_COVERAGE,
        "quote_asof_ms": BASE_MS,
        "quote_digest": DIGEST_QUOTE,
        "evidence_asof_ms": BASE_MS,
        "evidence_digest": DIGEST_EVIDENCE,
        "risk_version": {
            "economic_seq": 0,
            "executor_token": None,
            "accounting_tokens": None,
        },
        "risk_state_digest": DIGEST_RISK,
    }


def order_event_body(client_ref="ref-1", status="open"):
    return {
        "client_ref": client_ref,
        "venue_ref": "v-1",
        "event": "ack",
        "status": status,
        "venue_ts_ms": BASE_MS + 10,
        "recv_at_ms": BASE_MS + 20,
        "reason": None,
    }


def arming_obj(authority_id="auth-1", release_hash=RELEASE_HASH):
    return {
        "authority_id": authority_id,
        "release_hash": release_hash,
        "rung": "live_limited",
        "maker": "principal-maker",
        "checker": "principal-checker",
        "armed_at_ms": BASE_MS,
        "armed_until_ms": BASE_MS + 3_600_000,
        "allowlist": ["AAPL"],
        "limits_overlay": {},
        "request_proof_digest": DIGEST_PLAN,
        "approval_proof_digest": DIGEST_EVIDENCE,
    }


def authority_issue_body(authority_id="auth-1"):
    return {
        "authority_id": authority_id,
        "event": "issue",
        "role": "ordinary",
        "request_id": "req-arm-1",
        "approval_id": "apr-1",
        "reason": None,
        "arming": arming_obj(authority_id=authority_id),
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def make_breaker(tmp_path, document=None, policy=None, executor=None, clock=None,
                 cancel_open=True):
    """A breaker over a real fold, a folding ledger and a fake policy."""
    obj = document if document is not None else example_document()
    if document is None:
        set_path(obj, ("execution", "on_halt", "cancel_open"), cancel_open)
        obj["series_id"] = SERIES_ID
    doc = ServeDocument.from_obj(obj)
    clock = clock if clock is not None else FakeClock()
    state = SeriesState(SERIES_ID)
    ledger = FoldingLedger(state, clock)
    policy = policy if policy is not None else RecordingPolicy()
    serve_root = ServeRoot(str(tmp_path / "serve"), SERIES_ID)
    breaker = Breaker(
        doc,
        serve_root,
        ledger=ledger,
        state=state,
        clock=clock,
        transition_policy=policy,
        executor=executor,
    )
    return breaker, ledger, state, clock, policy, serve_root


def open_a_working_order(ledger, client_ref="ref-1"):
    """Fold one acked order so `StateView.working` is not empty."""
    ledger.append({"kind": "intent", "id": f"intent-{client_ref}",
                   "body": intent_body(client_ref)})
    ledger.append({"kind": "order_event", "id": f"oe-{client_ref}",
                   "body": order_event_body(client_ref)})


def halt(breaker, reason="operator", **kw):
    kw.setdefault("control_request_id", HALT_REQUEST)
    kw.setdefault("principal_digest", PRINCIPAL_DIGEST)
    kw.setdefault("proof_digest", PROOF_DIGEST)
    return breaker.trip(reason, ACTOR, **kw)


def reduce_to_reducing(breaker, **kw):
    kw.setdefault("control_request_id", REDUCE_REQUEST)
    kw.setdefault("principal_digest", PRINCIPAL_DIGEST)
    kw.setdefault("proof_digest", PROOF_DIGEST)
    return breaker.reduce(ACTOR, **kw)


def flatten(breaker, **kw):
    kw.setdefault("control_request_id", FLATTEN_REQUEST)
    kw.setdefault("principal_digest", PRINCIPAL_DIGEST)
    kw.setdefault("proof_digest", PROOF_DIGEST)
    return breaker.flatten(ACTOR, **kw)


def resume(breaker, ledger, **kw):
    kw.setdefault("acknowledges_trip_id", ledger.last_trip()["id"])
    kw.setdefault("control_request_id", RESUME_REQUEST)
    kw.setdefault("principal_digest", PRINCIPAL_DIGEST)
    kw.setdefault("proof_digest", PROOF_DIGEST)
    return breaker.reset(ACTOR, **kw)


# ---------------------------------------------------------------------------
# `current` — the fold is the state (§5.8.1, §5.13 step 6)
# ---------------------------------------------------------------------------


def test_current_reads_the_breaker_off_the_view(tmp_path):
    breaker, _, state, _, _, _ = make_breaker(tmp_path)
    assert breaker.current(state.snapshot()) == "active"


def test_current_answers_with_a_breaker_states_member_after_each_transition(tmp_path):
    breaker, ledger, state, _, _, _ = make_breaker(tmp_path)
    reduce_to_reducing(breaker)
    assert breaker.current(state.snapshot()) == "reducing"
    halt(breaker)
    assert breaker.current(state.snapshot()) == "halted"
    assert breaker.current(state.snapshot()) in vocab.BREAKER_STATES


def test_current_never_reads_the_breakers_own_memory(tmp_path):
    # The fold is authoritative: a view folded elsewhere is what a leg
    # hands in at §5.13 step (6), and the breaker must answer from it.
    breaker, ledger, state, _, _, _ = make_breaker(tmp_path)
    stale = state.snapshot()
    halt(breaker)
    assert breaker.current(stale) == "active"
    assert breaker.current(state.snapshot()) == "halted"


# ---------------------------------------------------------------------------
# `trip` — the §6 record, the barrier, the request/proof ids
# ---------------------------------------------------------------------------


def test_a_trip_appends_one_trip_record_with_section_6s_body(tmp_path):
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path, cancel_open=False)
    seq = halt(breaker, reason="feed_dead")
    assert ledger.kinds() == ["trip"]
    env = ledger.last_trip()
    assert env["seq"] == seq
    assert set(env["body"]) == TRIP_BODY_KEYS
    assert env["body"]["from"] == "active"
    assert env["body"]["to"] == "halted"
    assert env["body"]["reason"] == "feed_dead"
    assert env["body"]["actor"] == ACTOR
    assert env["body"]["acknowledged_trip_id"] is None


def test_a_trip_persists_the_control_request_and_proof_ids_it_was_given(tmp_path):
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    body = ledger.last_trip()["body"]
    assert body["control_request_id"] == HALT_REQUEST
    assert body["principal_digest"] == PRINCIPAL_DIGEST
    assert body["proof_digest"] == PROOF_DIGEST


def test_an_automatic_trip_records_no_request_or_proof(tmp_path):
    # A guard halt or a dead feed has no operator behind it; the record
    # must say so rather than borrow the previous request's ids.
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path, cancel_open=False)
    breaker.trip("guard_halt", "guards.day_loss")
    body = ledger.last_trip()["body"]
    assert body["control_request_id"] is None
    assert body["principal_digest"] is None
    assert body["proof_digest"] is None
    assert body["actor"] == "guards.day_loss"


def test_a_trip_barriers_after_it_appends(tmp_path):
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    assert ("append", "trip") in ledger.calls
    assert ledger.calls.index(("append", "trip")) < ledger.calls.index(
        ("barrier", None)
    )


@pytest.mark.parametrize("reason", vocab.TRIP_REASONS)
def test_every_trip_reason_in_the_vocabulary_is_accepted(tmp_path, reason):
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path, cancel_open=False)
    breaker.trip(reason, ACTOR)
    assert ledger.last_trip()["body"]["reason"] == reason


@pytest.mark.parametrize("reason", ["", None, "boredom", "halt"])
def test_a_trip_refuses_a_reason_outside_the_vocabulary(tmp_path, reason):
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path, cancel_open=False)
    with pytest.raises(ProductionError):
        breaker.trip(reason, ACTOR)
    assert ledger.records == []


def test_a_trip_from_reducing_also_enters_halted(tmp_path):
    breaker, ledger, state, _, policy, _ = make_breaker(tmp_path, cancel_open=False)
    reduce_to_reducing(breaker)
    halt(breaker)
    assert breaker.current(state.snapshot()) == "halted"
    assert ledger.last_trip()["body"]["from"] == "reducing"


def test_a_trip_may_claim_the_halt_cause_for_the_sentinel_path(tmp_path):
    # `TRANSITION_CAUSES` carries both `trip` and `halt`; the sentinel
    # path is what fills the second, and nothing else would.
    breaker, _, _, _, policy, _ = make_breaker(tmp_path, cancel_open=False)
    breaker.trip("operator", ACTOR, cause="halt")
    assert policy.calls[-1][2] == "halt"


@pytest.mark.parametrize("cause", ["reduce", "resume", "flatten_request", "timer"])
def test_a_trip_refuses_a_cause_that_does_not_enter_halted(tmp_path, cause):
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path, cancel_open=False)
    with pytest.raises(ProductionError):
        breaker.trip("operator", ACTOR, cause=cause)
    assert ledger.records == []


def test_a_control_driven_trip_derives_a_record_id_from_its_request(tmp_path):
    """D15: a replayed command must dedupe, so its record id is stable."""
    first, ledger_a, _, _, _, _ = make_breaker(tmp_path / "a", cancel_open=False)
    second, ledger_b, _, _, _, _ = make_breaker(tmp_path / "b", cancel_open=False)
    halt(first)
    halt(second)
    assert ledger_a.last_trip()["id"] == ledger_b.last_trip()["id"]
    assert HALT_REQUEST in ledger_a.last_trip()["id"]


# ---------------------------------------------------------------------------
# The transition policy is the only door (D10)
# ---------------------------------------------------------------------------


def test_a_trip_asks_the_policy_for_the_current_state_and_the_trip_cause(tmp_path):
    breaker, _, _, _, policy, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    assert policy.calls == [("active", "halted", "trip", PROOF_DIGEST)]


def test_a_reduce_asks_the_policy_with_the_reduce_cause(tmp_path):
    breaker, _, _, _, policy, _ = make_breaker(tmp_path)
    reduce_to_reducing(breaker)
    assert policy.calls == [("active", "reducing", "reduce", PROOF_DIGEST)]


def test_a_reduce_from_halted_is_still_put_to_the_policy_and_not_assumed(tmp_path):
    """D12: only a verified flatten may enter `reducing` from `halted` —
    and the breaker learns that from the policy, never from a branch."""
    policy = RecordingPolicy()
    breaker, _, _, _, _, _ = make_breaker(tmp_path, policy=policy, cancel_open=False)
    halt(breaker)
    policy.calls.clear()
    reduce_to_reducing(breaker)
    assert policy.calls == [("halted", "reducing", "reduce", PROOF_DIGEST)]


def test_a_flatten_asks_the_policy_with_the_flatten_request_cause(tmp_path):
    breaker, _, _, _, policy, _ = make_breaker(tmp_path)
    flatten(breaker)
    assert policy.calls == [("active", "reducing", "flatten_request", PROOF_DIGEST)]


def test_a_reset_asks_the_policy_with_the_resume_cause(tmp_path):
    breaker, ledger, _, clock, policy, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    clock.advance(COOLING_OFF_MS)
    policy.calls.clear()
    resume(breaker, ledger)
    assert policy.calls == [("halted", "active", "resume", PROOF_DIGEST)]


@pytest.mark.parametrize("verb", ["trip", "reduce", "flatten"])
def test_a_vetoed_transition_appends_nothing_and_refuses(tmp_path, verb):
    policy = RecordingPolicy(allowed=False, reason="transition_not_permitted")
    breaker, ledger, state, _, _, _ = make_breaker(
        tmp_path, policy=policy, cancel_open=False
    )
    call = {"trip": halt, "reduce": reduce_to_reducing, "flatten": flatten}[verb]
    with pytest.raises(ProductionError) as excinfo:
        call(breaker)
    assert "transition_not_permitted" in str(excinfo.value)
    assert ledger.records == []
    assert state.snapshot().breaker == "active"


def test_a_vetoed_reset_leaves_the_series_halted(tmp_path):
    allow = RecordingPolicy()
    breaker, ledger, state, clock, _, _ = make_breaker(
        tmp_path, policy=allow, cancel_open=False
    )
    halt(breaker)
    clock.advance(COOLING_OFF_MS)
    allow.allowed = False
    allow.reason = "transition_not_permitted"
    with pytest.raises(ProductionError):
        resume(breaker, ledger)
    assert state.snapshot().breaker == "halted"
    assert len(ledger.trips()) == 1


# ---------------------------------------------------------------------------
# `reduce` and `flatten` — authenticated, and never a flatten in disguise
# ---------------------------------------------------------------------------


def test_reduce_moves_active_to_reducing_and_records_no_cancel(tmp_path):
    breaker, ledger, state, _, _, _ = make_breaker(tmp_path)
    reduce_to_reducing(breaker)
    assert state.snapshot().breaker == "reducing"
    body = ledger.last_trip()["body"]
    assert (body["from"], body["to"]) == ("active", "reducing")
    assert body["cancel_outcome"] == "none"


def test_flatten_moves_active_to_reducing(tmp_path):
    breaker, ledger, state, _, _, _ = make_breaker(tmp_path)
    flatten(breaker)
    assert state.snapshot().breaker == "reducing"
    assert ledger.last_trip()["body"]["to"] == "reducing"


def test_flatten_moves_halted_to_reducing(tmp_path):
    breaker, ledger, state, _, _, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    flatten(breaker)
    assert state.snapshot().breaker == "reducing"
    assert ledger.last_trip()["body"]["from"] == "halted"


@pytest.mark.parametrize("missing", ["control_request_id", "proof_digest"])
@pytest.mark.parametrize("verb", ["reduce", "flatten"])
def test_an_unauthenticated_reduce_or_flatten_refuses(tmp_path, verb, missing):
    """D12: `reduce --proof` and `flatten-request --proof` are
    authenticated acts; an unsigned one is not a transition at all."""
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path)
    call = {"reduce": reduce_to_reducing, "flatten": flatten}[verb]
    with pytest.raises(ProductionError):
        call(breaker, **{missing: None})
    assert ledger.records == []


# ---------------------------------------------------------------------------
# `reset` — cooling-off, the acknowledged trip, and the lost arm
# ---------------------------------------------------------------------------


def test_reset_returns_a_halted_series_to_active(tmp_path):
    breaker, ledger, state, clock, _, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    clock.advance(COOLING_OFF_MS)
    resume(breaker, ledger)
    assert state.snapshot().breaker == "active"
    body = ledger.last_trip()["body"]
    assert (body["from"], body["to"]) == ("halted", "active")


def test_reset_returns_a_reducing_series_to_active(tmp_path):
    breaker, ledger, state, clock, _, _ = make_breaker(tmp_path)
    reduce_to_reducing(breaker)
    clock.advance(COOLING_OFF_MS)
    resume(breaker, ledger)
    assert state.snapshot().breaker == "active"


def test_reset_records_the_trip_it_acknowledges(tmp_path):
    breaker, ledger, _, clock, _, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    acknowledged = ledger.last_trip()["id"]
    clock.advance(COOLING_OFF_MS)
    resume(breaker, ledger)
    assert ledger.last_trip()["body"]["acknowledged_trip_id"] == acknowledged


def test_reset_refuses_without_a_trip_id(tmp_path):
    breaker, ledger, state, clock, _, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    clock.advance(COOLING_OFF_MS)
    with pytest.raises(ProductionError):
        resume(breaker, ledger, acknowledges_trip_id=None)
    assert state.snapshot().breaker == "halted"


def test_reset_refuses_a_trip_id_that_is_not_the_latest_transition(tmp_path):
    """An operator must acknowledge the trip that is actually holding
    the series down, not an older one they happened to have open."""
    breaker, ledger, state, clock, _, _ = make_breaker(tmp_path, cancel_open=False)
    reduce_to_reducing(breaker)
    stale = ledger.last_trip()["id"]
    halt(breaker)
    clock.advance(COOLING_OFF_MS)
    with pytest.raises(ProductionError) as excinfo:
        resume(breaker, ledger, acknowledges_trip_id=stale)
    assert stale in str(excinfo.value)
    assert state.snapshot().breaker == "halted"


def test_reset_refuses_a_trip_id_that_is_not_in_the_ledger_at_all(tmp_path):
    breaker, ledger, state, clock, _, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    clock.advance(COOLING_OFF_MS)
    with pytest.raises(ProductionError):
        resume(breaker, ledger, acknowledges_trip_id="trip-nonsuch")
    assert state.snapshot().breaker == "halted"


def test_reset_refuses_before_cooling_off_has_elapsed(tmp_path):
    breaker, ledger, state, clock, _, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    clock.advance(COOLING_OFF_MS - 1)
    with pytest.raises(ProductionError) as excinfo:
        resume(breaker, ledger)
    assert "cooling_off_s" in str(excinfo.value)
    assert state.snapshot().breaker == "halted"
    assert len(ledger.trips()) == 1


def test_reset_is_permitted_exactly_at_the_cooling_off_boundary(tmp_path):
    breaker, ledger, state, clock, _, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    clock.advance(COOLING_OFF_MS)
    resume(breaker, ledger)
    assert state.snapshot().breaker == "active"


def test_cooling_off_is_measured_from_the_acknowledged_trips_recorded_at(tmp_path):
    # Not from process start, not from the reset request: the record's
    # own `recorded_at_ms` is the only instant that survives a restart.
    clock = FakeClock()
    breaker, ledger, state, _, _, _ = make_breaker(
        tmp_path, clock=clock, cancel_open=False
    )
    clock.advance(5 * 60_000)
    halt(breaker)
    tripped_at = ledger.last_trip()["recorded_at_ms"]
    clock.advance(COOLING_OFF_MS - 1)
    with pytest.raises(ProductionError):
        resume(breaker, ledger)
    clock.advance(1)
    assert clock.now_ms() - tripped_at == COOLING_OFF_MS
    resume(breaker, ledger)
    assert state.snapshot().breaker == "active"


def test_reset_reads_the_cooling_off_knob_from_the_document(tmp_path):
    obj = set_path(example_document(), ("lifecycle", "cooling_off_s"), 60)
    set_path(obj, ("execution", "on_halt", "cancel_open"), False)
    obj["series_id"] = SERIES_ID
    breaker, ledger, state, clock, _, _ = make_breaker(tmp_path, document=obj)
    halt(breaker)
    clock.advance(60_000)
    resume(breaker, ledger)
    assert state.snapshot().breaker == "active"


@pytest.mark.parametrize("missing", ["control_request_id", "proof_digest"])
def test_an_unauthenticated_reset_refuses(tmp_path, missing):
    breaker, ledger, state, clock, _, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    clock.advance(COOLING_OFF_MS)
    with pytest.raises(ProductionError):
        resume(breaker, ledger, **{missing: None})
    assert state.snapshot().breaker == "halted"


def test_a_series_that_resumes_is_active_but_unarmed(tmp_path):
    """D12: "resume returns `active` but unarmed, so a fresh
    maker-checker arm is required"."""
    breaker, ledger, state, clock, _, _ = make_breaker(tmp_path, cancel_open=False)
    ledger.append({"kind": "authority", "id": "auth-rec-1",
                   "body": authority_issue_body()})
    assert state.snapshot().arming is not None
    halt(breaker)
    assert state.snapshot().arming is None
    clock.advance(COOLING_OFF_MS)
    resume(breaker, ledger)
    view = state.snapshot()
    assert view.breaker == "active"
    assert view.arming is None


def test_leaving_active_for_reducing_also_revokes_the_ordinary_arm(tmp_path):
    breaker, ledger, state, _, _, _ = make_breaker(tmp_path)
    ledger.append({"kind": "authority", "id": "auth-rec-1",
                   "body": authority_issue_body()})
    reduce_to_reducing(breaker)
    assert state.snapshot().arming is None


# ---------------------------------------------------------------------------
# Halt cancel outcomes (D12) — `none | submitted | failed | partial | unknown`
# ---------------------------------------------------------------------------


def test_a_halt_with_nothing_working_records_none_and_calls_no_executor(tmp_path):
    executor = FakeExecutor(acks=())
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path, executor=executor)
    halt(breaker)
    assert ledger.last_trip()["body"]["cancel_outcome"] == "none"
    assert executor.cancel_all_calls == 0


def test_a_halt_records_none_when_the_document_disables_cancel_open(tmp_path):
    executor = FakeExecutor(acks=(ack("pending_cancel"),))
    breaker, ledger, _, _, _, _ = make_breaker(
        tmp_path, executor=executor, cancel_open=False
    )
    open_a_working_order(ledger)
    halt(breaker)
    assert ledger.last_trip()["body"]["cancel_outcome"] == "none"
    assert executor.cancel_all_calls == 0


def test_a_halt_cancels_working_orders_when_the_document_asks(tmp_path):
    executor = FakeExecutor(acks=(ack("pending_cancel"),))
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path, executor=executor)
    open_a_working_order(ledger)
    halt(breaker)
    assert executor.cancel_all_calls == 1
    assert ledger.last_trip()["body"]["cancel_outcome"] == "submitted"


def test_a_halt_records_failed_when_the_executor_raises(tmp_path):
    executor = FakeExecutor(raises=True)
    breaker, ledger, state, _, _, _ = make_breaker(tmp_path, executor=executor)
    open_a_working_order(ledger)
    halt(breaker)
    assert ledger.last_trip()["body"]["cancel_outcome"] == "failed"
    assert state.snapshot().breaker == "halted"


def test_a_halt_records_failed_when_every_cancel_was_refused(tmp_path):
    executor = FakeExecutor(acks=(ack("rejected"), ack("not_sent", "ref-2")))
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path, executor=executor)
    open_a_working_order(ledger)
    open_a_working_order(ledger, "ref-2")
    halt(breaker)
    assert ledger.last_trip()["body"]["cancel_outcome"] == "failed"


def test_a_halt_records_partial_when_some_cancels_were_refused(tmp_path):
    executor = FakeExecutor(acks=(ack("pending_cancel"), ack("rejected", "ref-2")))
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path, executor=executor)
    open_a_working_order(ledger)
    open_a_working_order(ledger, "ref-2")
    halt(breaker)
    assert ledger.last_trip()["body"]["cancel_outcome"] == "partial"


def test_a_halt_records_unknown_when_any_cancel_left_the_process_unresolved(tmp_path):
    """A venue that answered `unknown` collapses the whole outcome
    toward less certainty — the same rule §5.4 applies to statuses."""
    executor = FakeExecutor(acks=(ack("pending_cancel"), ack("unknown", "ref-2")))
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path, executor=executor)
    open_a_working_order(ledger)
    open_a_working_order(ledger, "ref-2")
    halt(breaker)
    assert ledger.last_trip()["body"]["cancel_outcome"] == "unknown"


def test_every_recorded_cancel_outcome_is_in_the_vocabulary(tmp_path):
    cases = ((), (ack("cancelled"),), (ack("rejected"),),
             (ack("cancelled"), ack("unknown", "ref-2")))
    for index, acks in enumerate(cases):
        executor = FakeExecutor(acks=acks)
        breaker, ledger, _, _, _, _ = make_breaker(tmp_path / f"case{index}",
                                                   executor=executor)
        open_a_working_order(ledger)
        halt(breaker)
        assert ledger.last_trip()["body"]["cancel_outcome"] in vocab.CANCEL_OUTCOMES


def test_a_failed_cancel_never_stops_the_halt_from_being_recorded(tmp_path):
    executor = FakeExecutor(raises=True)
    breaker, ledger, state, _, _, _ = make_breaker(tmp_path, executor=executor)
    open_a_working_order(ledger)
    halt(breaker)
    assert ledger.kinds()[-1] == "trip"
    assert state.snapshot().breaker == "halted"


# ---------------------------------------------------------------------------
# The `HALT` sentinel (D12, §5.8)
# ---------------------------------------------------------------------------


def test_the_sentinel_is_absent_until_it_is_created(tmp_path):
    breaker, _, _, _, _, serve_root = make_breaker(tmp_path)
    assert breaker.halt_sentinel_present() is False
    breaker.create_halt_sentinel()
    assert breaker.halt_sentinel_present() is True
    assert os.path.exists(serve_root.halt_sentinel)


def test_creating_a_sentinel_that_already_exists_never_fails_or_truncates(tmp_path):
    """A kill switch that raises because it is already on is a kill
    switch that can fail when it matters most."""
    breaker, _, _, _, _, serve_root = make_breaker(tmp_path)
    assert breaker.create_halt_sentinel() is True
    with open(serve_root.halt_sentinel, "w", encoding="utf-8") as handle:
        handle.write("halted by ops")
    assert breaker.create_halt_sentinel() is False
    with open(serve_root.halt_sentinel, encoding="utf-8") as handle:
        assert handle.read() == "halted by ops"


@pytest.mark.parametrize("purpose", ["resume", "flatten_request"])
def test_only_a_verified_resume_or_flatten_may_retire_the_sentinel(tmp_path, purpose):
    breaker, _, _, _, _, _ = make_breaker(tmp_path)
    breaker.create_halt_sentinel()
    assert breaker.retire_halt_sentinel(purpose) is True
    assert breaker.halt_sentinel_present() is False


@pytest.mark.parametrize("purpose", ["trip", "halt", "reduce", "adopt", "", None])
def test_retiring_the_sentinel_for_any_other_purpose_refuses(tmp_path, purpose):
    breaker, _, _, _, _, _ = make_breaker(tmp_path)
    breaker.create_halt_sentinel()
    with pytest.raises(ProductionError):
        breaker.retire_halt_sentinel(purpose)
    assert breaker.halt_sentinel_present() is True


def test_retiring_an_absent_sentinel_is_not_an_error(tmp_path):
    breaker, _, _, _, _, _ = make_breaker(tmp_path)
    assert breaker.retire_halt_sentinel("resume") is False


def test_a_reset_retires_the_sentinel_before_its_transition_barrier(tmp_path):
    """D12: a crash between retirement and the barrier leaves the ledger
    folded `halted`, which cannot enable action."""
    breaker, ledger, _, clock, _, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    breaker.create_halt_sentinel()
    seen = []
    original = ledger.append
    ledger.append = lambda record: (
        seen.append(breaker.halt_sentinel_present()) or original(record)
    )
    clock.advance(COOLING_OFF_MS)
    resume(breaker, ledger)
    assert seen == [False]
    assert breaker.halt_sentinel_present() is False


def test_a_flatten_retires_the_sentinel_before_its_transition_barrier(tmp_path):
    breaker, ledger, _, _, _, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    breaker.create_halt_sentinel()
    seen = []
    original = ledger.append
    ledger.append = lambda record: (
        seen.append(breaker.halt_sentinel_present()) or original(record)
    )
    flatten(breaker)
    assert seen == [False]


@pytest.mark.parametrize("verb", ["flatten", "reset"])
def test_a_vetoed_transition_never_retires_the_sentinel(tmp_path, verb):
    """The kill switch outlives a refused transition: retirement is part
    of the transition, not a step taken on the way to asking for one."""
    allow = RecordingPolicy()
    breaker, ledger, _, clock, _, _ = make_breaker(
        tmp_path, policy=allow, cancel_open=False
    )
    halt(breaker)
    breaker.create_halt_sentinel()
    clock.advance(COOLING_OFF_MS)
    allow.allowed = False
    allow.reason = "transition_not_permitted"
    call = {"flatten": flatten, "reset": lambda b: resume(b, ledger)}[verb]
    with pytest.raises(ProductionError):
        call(breaker)
    assert breaker.halt_sentinel_present() is True


def test_a_reset_refused_for_cooling_off_never_retires_the_sentinel(tmp_path):
    breaker, ledger, _, clock, _, _ = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    breaker.create_halt_sentinel()
    clock.advance(COOLING_OFF_MS - 1)
    with pytest.raises(ProductionError):
        resume(breaker, ledger)
    assert breaker.halt_sentinel_present() is True


@pytest.mark.parametrize("verb", ["trip", "reduce"])
def test_neither_a_trip_nor_a_reduce_retires_the_sentinel(tmp_path, verb):
    breaker, _, _, _, _, _ = make_breaker(tmp_path, cancel_open=False)
    breaker.create_halt_sentinel()
    {"trip": halt, "reduce": reduce_to_reducing}[verb](breaker)
    assert breaker.halt_sentinel_present() is True


# ---------------------------------------------------------------------------
# `breaker.json` — a head-bound cache of the fold, nothing more (D15)
# ---------------------------------------------------------------------------


def read_cache(serve_root):
    with open(serve_root.breaker_cache, encoding="utf-8") as handle:
        return json.load(handle)


def test_the_cache_path_is_the_serve_roots_breaker_json(tmp_path):
    breaker, _, _, _, _, serve_root = make_breaker(tmp_path)
    assert breaker.cache_path == serve_root.breaker_cache


def test_write_cache_records_the_state_and_the_head_it_projects(tmp_path):
    breaker, ledger, state, _, _, serve_root = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    breaker.write_cache(state.snapshot())
    cached = read_cache(serve_root)
    assert set(cached) == {"state", "head_seq", "head_hash"}
    assert cached["state"] == "halted"
    assert (cached["head_seq"], cached["head_hash"]) == state.head()


def test_a_cache_write_leaves_no_temp_file_behind(tmp_path):
    breaker, _, state, _, _, serve_root = make_breaker(tmp_path)
    breaker.write_cache(state.snapshot())
    names = os.listdir(os.path.dirname(serve_root.breaker_cache))
    assert "breaker.json" in names
    assert [n for n in names if n.startswith("breaker.json.")] == []


def test_a_cache_at_the_head_is_used_as_it_stands(tmp_path):
    breaker, ledger, state, _, _, serve_root = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    breaker.write_cache(state.snapshot())
    assert breaker.load_cache(ledger, state.snapshot()) == "halted"


def test_a_cache_behind_the_head_is_rebuilt_and_rewritten(tmp_path):
    breaker, ledger, state, _, _, serve_root = make_breaker(tmp_path, cancel_open=False)
    breaker.write_cache(state.snapshot())
    behind = read_cache(serve_root)
    halt(breaker)
    assert behind["state"] == "active"
    assert breaker.load_cache(ledger, state.snapshot()) == "halted"
    rebuilt = read_cache(serve_root)
    assert rebuilt["state"] == "halted"
    assert (rebuilt["head_seq"], rebuilt["head_hash"]) == state.head()


def test_an_absent_cache_is_rebuilt_from_the_fold(tmp_path):
    breaker, ledger, state, _, _, serve_root = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    assert not os.path.exists(serve_root.breaker_cache)
    assert breaker.load_cache(ledger, state.snapshot()) == "halted"
    assert read_cache(serve_root)["state"] == "halted"


def test_a_cache_ahead_of_the_ledger_refuses(tmp_path):
    breaker, ledger, state, _, _, serve_root = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    breaker.write_cache(state.snapshot())
    forged = read_cache(serve_root)
    forged["head_seq"] += 5
    with open(serve_root.breaker_cache, "w", encoding="utf-8") as handle:
        json.dump(forged, handle)
    with pytest.raises(ProductionError):
        breaker.load_cache(ledger, state.snapshot())


def test_a_cache_that_diverges_at_its_own_seq_refuses(tmp_path):
    breaker, ledger, state, _, _, serve_root = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    breaker.write_cache(state.snapshot())
    forged = read_cache(serve_root)
    forged["head_hash"] = "f" * 64
    with open(serve_root.breaker_cache, "w", encoding="utf-8") as handle:
        json.dump(forged, handle)
    with pytest.raises(ProductionError):
        breaker.load_cache(ledger, state.snapshot())


def test_a_current_cache_whose_state_disagrees_with_the_fold_refuses(tmp_path):
    """§5.6: the cache is *validated against that fold* before READY —
    a head-bound file that claims `active` at a halted head is the one
    thing a head check alone would wave through."""
    breaker, ledger, state, _, _, serve_root = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    breaker.write_cache(state.snapshot())
    forged = read_cache(serve_root)
    forged["state"] = "active"
    with open(serve_root.breaker_cache, "w", encoding="utf-8") as handle:
        json.dump(forged, handle)
    with pytest.raises(ProductionError):
        breaker.load_cache(ledger, state.snapshot())


@pytest.mark.parametrize("text", ["", "{", '{"state": "halted"}', "[]"])
def test_an_unreadable_cache_refuses_rather_than_defaulting_to_active(tmp_path, text):
    breaker, ledger, state, _, _, serve_root = make_breaker(tmp_path, cancel_open=False)
    halt(breaker)
    with open(serve_root.breaker_cache, "w", encoding="utf-8") as handle:
        handle.write(text)
    with pytest.raises(ProductionError):
        breaker.load_cache(ledger, state.snapshot())


def test_the_cache_never_carries_a_state_outside_the_vocabulary(tmp_path):
    breaker, ledger, state, _, _, serve_root = make_breaker(tmp_path, cancel_open=False)
    for call in (reduce_to_reducing, halt):
        call(breaker)
        breaker.write_cache(state.snapshot())
        assert read_cache(serve_root)["state"] in vocab.BREAKER_STATES
