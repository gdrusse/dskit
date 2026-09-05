"""`state.py` — one fold, one owner (§5.8.1).

Everything derived from the ledger comes out of `SeriesState.apply`:
positions, working orders, pending refs, balances, decision history, the
breaker, the arming, the readiness GO, guard holds, the reduction
projection and the pending-control set. `StateView` is the frozen
projection of that fold and of nothing else; `Recovery` replays it from
the last `snapshot` record and closes what a crash left open. Two AST
tests pin the "nothing else folds the ledger" half.

Record shape: the §6 envelope NESTS its body. A caller appends exactly
`{"kind", "id", "body"}` and the ledger assigns the rest, so a body may
carry its own `kind`, `release_hash` or `series_id` — a recovering
process's envelope `release_hash` legitimately differs from a
`tick_start` body's. The fold reads body fields off `record["body"]` and
`kind`/`seq`/`hash`/`release_hash` off the envelope. Bodies stay
self-describing all the same: `role` (authority), `state_kind`
(guard_state), `outcome_kind` (outcome), `flow_kind` (cash_flow) and
`verified_payload_digest` (control_approval).
"""

import ast
import copy
import dataclasses
import hashlib
import inspect
import pathlib
import types
from decimal import Decimal

import pytest

from dskit.production import state as state_module
from dskit.production import vocab
from dskit.production.base import ProductionError, canonical_bytes, canonical_hash
from dskit.production.records import Fill
from dskit.production.state import (
    DEFAULT_MAX_HISTORY,
    PositionBook,
    Recovery,
    SeriesState,
    StateView,
    TickState,
)

# ---------------------------------------------------------------------------
# Fixed material — every value here is a constant so a hand-computed
# expectation below can be read against it.
# ---------------------------------------------------------------------------

SERIES_ID = "3f0a1c62-0000-4000-8000-000000000001"
OTHER_SERIES_ID = "3f0a1c62-0000-4000-8000-0000000000ff"
RELEASE_HASH = "b" * 64
GENESIS_HASH = "0" * 64
BASE_MS = 1_767_268_800_000

DIGEST_INPUTS = "1" * 64
DIGEST_COVERAGE = "2" * 64
DIGEST_QUOTE = "3" * 64
DIGEST_EVIDENCE = "4" * 64
DIGEST_RISK = "5" * 64
DIGEST_PLAN = "6" * 64
DIGEST_READY = "7" * 64
RIGHT_A = "a1" * 32
RIGHT_B = "a2" * 32
DERIVED_REF = "derived-ref-1"

# What the ledger assigns; a caller supplies only `kind`, `id` and `body`.
ASSIGNED = (
    "payload_digest",
    "seq",
    "series_id",
    "process_id",
    "release_hash",
    "recorded_at_ms",
    "schema_version",
    "prev_hash",
    "hash",
)
CALLER_KEYS = ("kind", "id", "body")
ENVELOPE_KEYS = frozenset(ASSIGNED) | frozenset(CALLER_KEYS)

# The 14 `StateView` members, in §5.8.1's order.
VIEW_MEMBERS = (
    "positions",
    "working",
    "pending",
    "balances",
    "decision_history",
    "breaker",
    "arming",
    "readiness",
    "guard_holds",
    "reduction",
    "pending_control",
    "risk_version",
    "head_seq",
    "head_hash",
)

# D14: `economic_seq` advances on economic events ONLY. A `cash_flow` is
# a balance update and does advance it; an `outcome` (a label or mark) and
# an `adoption` (a receipt for one) do not.
ECONOMIC_KINDS = ("order_event", "fill", "cash_flow")


# ---------------------------------------------------------------------------
# Envelope helper — dense seq, chained hash, flat body (§6)
# ---------------------------------------------------------------------------


class Chain:
    """Hands out §6 envelopes with dense `seq` and a chained `hash`."""

    def __init__(self, series_id=SERIES_ID, seq=0, head_hash=GENESIS_HASH):
        self.series_id = series_id
        self.seq = seq
        self.head_hash = head_hash

    def env(self, kind, body=None, rid=None, **overrides):
        self.seq += 1
        prev = self.head_hash
        caller = {"kind": kind, "id": rid or f"{kind}-{self.seq}",
                  "body": dict(body or {})}
        digest = canonical_hash(caller)
        env = {
            **caller,
            "payload_digest": digest,
            "seq": self.seq,
            "series_id": self.series_id,
            "process_id": "proc-1",
            "release_hash": RELEASE_HASH,
            "recorded_at_ms": BASE_MS + self.seq,
            "schema_version": 1,
            "prev_hash": prev,
            "hash": hashlib.sha256((prev + digest).encode()).hexdigest(),
        }
        assert set(env) == ENVELOPE_KEYS, sorted(set(env) ^ ENVELOPE_KEYS)
        env.update(overrides)
        self.head_hash = env["hash"]
        return env


def new_state():
    """A fresh fold and the chain that feeds it."""
    return SeriesState(SERIES_ID), Chain()


def fold(st, chain, kind, body=None, **overrides):
    """Apply one hand-built record; return the envelope that was folded."""
    env = chain.env(kind, body, **overrides)
    st.apply(env)
    return env


def position_of(positions, instrument):
    """The single position for `instrument` (fails loudly if absent)."""
    hits = [p for p in positions if p.instrument == instrument]
    assert len(hits) == 1, f"expected one {instrument} position, got {hits!r}"
    return hits[0]


# ---------------------------------------------------------------------------
# §6 bodies — every field the table names, minus the ledger-assigned ones
# ---------------------------------------------------------------------------


def proposal_obj(pid="cand-1", instrument="AAA", side="buy", qty="10"):
    return {
        "id": pid,
        "instrument": instrument,
        "side": side,
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


def intent_body(client_ref="ref-1", qty="10", instrument="AAA", side="buy"):
    # The canonical records.Intent minus `release_hash`, which the ledger
    # assigns (reported: §5.4's Intent HAS that field).
    return {
        "client_ref": client_ref,
        "decision_plan_id": "plan-1",
        "decision_plan_digest": DIGEST_PLAN,
        "proposal": proposal_obj(instrument=instrument, side=side, qty=qty),
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


def order_event_body(client_ref="ref-1", event="ack", status="open", venue_ref="v-1"):
    return {
        "client_ref": client_ref,
        "venue_ref": venue_ref,
        "event": event,
        "status": status,
        "venue_ts_ms": BASE_MS + 10,
        "recv_at_ms": BASE_MS + 20,
        "reason": None,
    }


def fill_body(
    fill_id="f-1",
    client_ref="ref-1",
    instrument="AAA",
    side="buy",
    qty="10",
    price="100",
    status="final",
):
    return {
        "fill_id": fill_id,
        "venue_ref": "v-1",
        "client_ref": client_ref,
        "instrument": instrument,
        "side": side,
        "qty": qty,
        "price": price,
        "fee": "0",
        "fee_currency": "USD",
        "liquidity": "taker",
        "status": status,
        "ts_ms": BASE_MS + 30,
        "native": None,
    }


def cash_flow_body(currency="USD", amount="250", external=True, flow_kind="deposit"):
    return {
        "effective_at_ms": BASE_MS - 86_400_000,
        "known_at_ms": BASE_MS,
        "supersedes": None,
        "currency": currency,
        "amount": amount,
        "flow_kind": flow_kind,
        "external": external,
        "source": "venue",
        "evidence": {"break_id": "brk-1", "delta": amount},
    }


ARMED_UNTIL_MS = BASE_MS + 3_600_000
RIGHTS_EXPIRE_MS = BASE_MS + 300_000


def arming_obj(authority_id="auth-1"):
    """`ArmingState.to_obj()` as the `authority` issue body embeds it."""
    return {
        "authority_id": authority_id,
        "release_hash": RELEASE_HASH,
        "rung": "live_limited",
        "maker": "principal-maker",
        "checker": "principal-checker",
        "armed_at_ms": BASE_MS,
        "armed_until_ms": ARMED_UNTIL_MS,
        "allowlist": ["AAA"],
        "limits_overlay": {},
        "request_proof_digest": DIGEST_PLAN,
        "approval_proof_digest": DIGEST_EVIDENCE,
    }


def reduction_authorization_obj(authority_id="auth-2", rights=()):
    """`ReductionAuthorization.to_obj()` as a reduction issue embeds it."""
    return {
        "authority_id": authority_id,
        "release_hash": RELEASE_HASH,
        "request_id": "req-flatten-1",
        "reduction_intent_digests": list(rights),
        "expires_ms": RIGHTS_EXPIRE_MS,
    }


def authority_body(event="issue", role="ordinary", authority_id="auth-1", rights=()):
    body = {
        "authority_id": authority_id,
        "event": event,
        "role": role,
        "request_id": "req-1",
        "approval_id": "apr-1",
        "reason": None,
    }
    if event == "issue" and role == "ordinary":
        body["arming"] = arming_obj(authority_id=authority_id)
    if event == "issue" and role == "reduction":
        body["authorization"] = reduction_authorization_obj(
            authority_id=authority_id, rights=rights)
    return body


def authority_use_body(digest=RIGHT_A, authority_id="auth-2", client_ref="ref-9"):
    return {
        "authority_id": authority_id,
        "reduction_intent_digest": digest,
        "client_ref": client_ref,
        "reserved_at_ms": BASE_MS + 40,
    }


def guard_state_body(
    guard="day_loss",
    scope_key="AAA",
    state_kind="hold",
    held_until_ms=BASE_MS + 600_000,
    resume_at_ms=None,
):
    return {
        "guard": guard,
        "scope_key": scope_key,
        "state_kind": state_kind,
        "reason": "loss bound breached",
        "held_until_ms": held_until_ms,
        "resume_at_ms": resume_at_ms,
        "finding": {"guard": guard, "verdict": state_kind},
    }


def readiness_body(verdict="go", valid_until_ms=BASE_MS + 900_000):
    return {
        "verdict": verdict,
        "items": [
            {
                "item": "release_verified",
                "required": True,
                "evidence": "release.json",
                "waiver": None,
                "passed": True,
            },
            {
                "item": "reconciled",
                "required": True,
                "evidence": "recon-1",
                "waiver": None,
                "passed": True,
            },
        ],
        "readiness_digest": DIGEST_READY,
        "evaluated_at_ms": BASE_MS,
        "valid_until_ms": valid_until_ms,
    }


def trip_body(from_state="active", to_state="reducing"):
    return {
        "from": from_state,
        "to": to_state,
        "reason": "operator",
        "actor": "operator",
        "control_request_id": "req-2",
        "principal_digest": DIGEST_RISK,
        "proof_digest": DIGEST_PLAN,
        "acknowledged_trip_id": None,
    }


def decision_body(tick_id="T1", legs=("leg-1", "leg-2")):
    return {
        "tick_id": tick_id,
        "decision_plan_ids": ["plan-1"],
        "decision_plan_digests": [DIGEST_PLAN],
        "legs": [
            {
                "leg_id": leg_id,
                "instrument": "AAA",
                "prediction": "0.01",
                "confidence": "0.5",
                "baseline": "0.0",
                "expected_value": "5",
                "reference_price": "100",
                "proposal": proposal_obj(pid=f"cand-{index}"),
                "findings": [],
                "final": "buy",
                "client_ref": f"ref-{index}",
            }
            for index, leg_id in enumerate(legs, start=1)
        ],
    }


def tick_body(tick_id="T1", status="decided"):
    return {
        "tick_id": tick_id,
        "tick_at": BASE_MS,
        "data_asof_ms": BASE_MS - 1_000,
        "observed_at_ms": BASE_MS + 5,
        "status": status,
        "feed": {
            "status": "ok",
            "acq_id": "acq-1",
            "records_added": 3,
            "source_config_hash": DIGEST_INPUTS,
            "required_keys_digest": DIGEST_COVERAGE,
            "watermarks_by_key": {"AAA": BASE_MS - 1_000},
            "coverage_digest": DIGEST_COVERAGE,
        },
        "inputs_digest": DIGEST_INPUTS,
        "nav": "1000",
        "calendar": "open",
        "overrun_absorbed": [],
        "latency_ms": {},
        "leg_latency_ms": {},
        "health": "ready",
        "breaker": "active",
        "rung": "live_limited",
        "refusal_reason": None,
        "error": None,
    }


def decision_plan_body(plan_id="plan-1", result="submit"):
    return {
        "plan_id": plan_id,
        "inputs_asof_ms": BASE_MS,
        "inputs_digest": DIGEST_INPUTS,
        "coverage_digest": DIGEST_COVERAGE,
        "quote_asof_ms": BASE_MS,
        "quote_digest": DIGEST_QUOTE,
        "evidence_asof_ms": BASE_MS,
        "evidence_digest": DIGEST_EVIDENCE,
        "provenance_digests": {"head": DIGEST_PLAN},
        "original": proposal_obj(),
        "final": proposal_obj(),
        "findings": [],
        "gate_results": [],
        "scope_verdict": {"allowed": True, "scope_key": "AAA", "reason": None},
        "risk_effect": "increase",
        "risk_version": {
            "economic_seq": 0,
            "executor_token": None,
            "accounting_tokens": None,
        },
        "risk_state_digest": DIGEST_RISK,
        "result": result,
    }


def full_sequence():
    """Every §6 record kind, in an order the fold accepts.

    The two kinds whose economic status the plan leaves open sit last, so
    the `economic_seq` walk can stop at the first of them.
    """
    return [
        ("process", {"event": "start", "doc_hash": DIGEST_PLAN,
                     "serving_hash": DIGEST_INPUTS, "run_hash": DIGEST_QUOTE,
                     "artifact_digests": {}, "source_config_hash": DIGEST_INPUTS,
                     "runtime_fingerprint": DIGEST_RISK, "rung": "live_limited",
                     "executor_kind": "paper", "code_version": "0.0.1"}),
        ("readiness", readiness_body()),
        ("control_request", {"request_id": "req-1", "purpose": "arm_request",
                             "payload": {"rung": "live_limited"},
                             "principal_digest": DIGEST_RISK,
                             "proof_digest": DIGEST_PLAN,
                             "expires_ms": BASE_MS + 600_000}),
        ("control_approval", {"request_id": "req-1", "purpose": "arm_approval",
                              "checker_principal_digest": DIGEST_EVIDENCE,
                              "checker_proof_digest": DIGEST_QUOTE,
                              "verified_payload_digest": DIGEST_PLAN}),
        ("authority", authority_body()),
        ("command_result", {"request_id": "req-1", "status": "applied",
                            "record_ids": ["authority-5"], "reason": None}),
        ("tick_start", {"tick_id": "T1", "tick_at_ms": BASE_MS}),
        ("decision_plan", decision_plan_body()),
        ("intent", intent_body()),
        ("authorization", {"authority_use_id": None,
                           "permit": {"client_ref": "ref-1",
                                      "authority_id": "auth-1",
                                      "intent_digest": DIGEST_PLAN,
                                      "valid_until_ms": BASE_MS + 5_000}}),
        ("order_event", order_event_body()),
        ("fill", fill_body()),
        ("guard_state", guard_state_body()),
        ("monitor", {"monitor": "drift", "slice": "all", "window": "1d",
                     "statistic": "0.02", "threshold": "0.10",
                     "status": "ok", "provisional": False}),
        ("alert", {"fingerprint": "fp-1", "severity": "warning",
                   "status": "firing", "summary": "drift", "source": "monitor",
                   "tick_id": "T1", "at_ms": BASE_MS, "labels": {},
                   "sinks": [{"sink": "memory", "delivered": True}]}),
        ("health", {"from": "starting", "to": "ready", "cause": "probes",
                    "probe_evidence": {}}),
        ("recon", {"scope": "orders", "ours_digest": DIGEST_INPUTS,
                   "theirs_digest": DIGEST_INPUTS, "breaks": [],
                   "status": "clean", "action": "none"}),
        ("cash_flow", cash_flow_body()),
        ("decision", decision_body()),
        ("tick", tick_body()),
        ("trip", trip_body()),
        ("cancel_outcome", {"trip_id": "trip-1", "outcome": "none", "acks": []}),
        ("authority", authority_body(role="reduction", authority_id="auth-2",
                                     rights=(RIGHT_A, RIGHT_B))),
        ("authority_use", authority_use_body()),
        ("snapshot", None),
        ("outcome", {"leg_id": "leg-1", "outcome_kind": "settled",
                     "effective_at_ms": BASE_MS, "known_at_ms": BASE_MS + 60,
                     "value": "1", "weight": "1", "terminal": True,
                     "supersedes": None, "source": "venue"}),
        ("adoption", {"control_request_id": "req-3",
                      "principal_digest": DIGEST_RISK,
                      "proof_digest": DIGEST_PLAN, "break_ids": ["brk-1"],
                      "delta_digest": DIGEST_EVIDENCE,
                      "before_recon_id": "recon-17",
                      "after_recon_id": "recon-18"}),
    ]


def fold_sequence(st, chain, pairs):
    """Fold `pairs`, filling the snapshot body from the state itself."""
    applied = []
    for kind, body in pairs:
        if kind == "snapshot":
            payload = st.to_snapshot_obj()
            body = {"at_seq": st.head()[0], "state": payload,
                    "state_digest": canonical_hash(payload)}
        applied.append(fold(st, chain, kind, body))
    return applied


# ---------------------------------------------------------------------------
# Recording fakes for `Recovery(ledger, state, id_source, executor)`
# ---------------------------------------------------------------------------


class FakeClock:
    """The two `Clock` methods anything here needs; no wall time."""

    def __init__(self, ms=BASE_MS):
        self._ms = int(ms)

    def now_ms(self):
        return self._ms

    def monotonic(self):
        return self._ms / 1000.0


class FakeLedger:
    """The `Ledger` surface `Recovery` uses, list-backed.

    `append` folds into the attached state, which is what §5.8.1 requires
    of the real one ("`Ledger.append` calls it"), so recovery's own
    appends land in the fold without recovery applying them twice.
    """

    def __init__(self, series_id=SERIES_ID):
        self.chain = Chain(series_id=series_id)
        self.records = []
        self.state = None
        self.barrier_calls = 0

    def append(self, record):
        # A caller supplies exactly kind/id/body; the ledger assigns the rest.
        assert isinstance(record, dict), record
        assert set(record) == set(CALLER_KEYS), sorted(record)
        assert isinstance(record["body"], dict), record["body"]
        env = self.chain.env(record["kind"], record["body"], rid=record["id"])
        self.records.append(env)
        if self.state is not None:
            self.state.apply(env)
        return env["seq"]

    def append_many(self, records):
        return [self.append(record) for record in records]

    def scan(self, kind=None, since_seq=0):
        return tuple(
            record
            for record in self.records
            if record["seq"] > since_seq and (kind is None or record["kind"] == kind)
        )

    def head(self):
        return (self.chain.seq, self.chain.head_hash)

    def latest_snapshot(self):
        snaps = [r for r in self.records if r["kind"] == "snapshot"]
        return snaps[-1] if snaps else None

    def barrier(self):
        self.barrier_calls += 1

    def snapshot(self, payload):
        at_seq = self.head()[0]
        return self.append(
            {
                "kind": "snapshot",
                "id": f"snapshot-{at_seq}",
                "body": {
                    "at_seq": at_seq,
                    "state": payload,
                    "state_digest": canonical_hash(payload),
                },
            }
        )

    def kinds(self, since=0):
        return [r["kind"] for r in self.records[since:]]


class FakeOrderState:
    """An `OrderState`-shaped answer from `executor.order(ref)`."""

    def __init__(self, client_ref, venue_ref, status, qty="10", filled_qty="0"):
        self.client_ref = client_ref
        self.venue_ref = venue_ref
        self.status = status
        self.instrument = "AAA"
        self.side = "buy"
        self.qty = Decimal(qty)
        self.filled_qty = Decimal(filled_qty)
        self.remaining_qty = self.qty - self.filled_qty
        self.limit = Decimal("100")
        self.tif = "gtc"
        self.avg_price = None
        self.fee = Decimal("0")
        self.reason = None
        self.ts_ms = BASE_MS + 50
        self.created_ms = BASE_MS
        self.updated_ms = BASE_MS + 50
        self.native = None

    def to_obj(self):
        return {
            "client_ref": self.client_ref,
            "venue_ref": self.venue_ref,
            "status": self.status,
            "instrument": self.instrument,
            "side": self.side,
            "qty": str(self.qty),
            "filled_qty": str(self.filled_qty),
            "remaining_qty": str(self.remaining_qty),
            "ts_ms": self.ts_ms,
        }


class FakeExecutor:
    """Query-only executor: `order(ref)` answers or raises; `submit` fails."""

    def __init__(self, answers=None, raises=()):
        self.answers = dict(answers or {})
        self.raises = set(raises)
        self.queried = []
        self.submitted = []

    def order(self, ref):
        self.queried.append(ref)
        if ref in self.raises:
            raise RuntimeError("venue unreachable")
        return self.answers[ref]

    def submit(self, *args, **kwargs):
        self.submitted.append((args, kwargs))
        raise AssertionError("recovery must never submit")

    def cancel(self, ref):
        raise AssertionError("recovery must never cancel")


class FakeIdSource:
    """Deterministic ids; `client_ref` is signature-agnostic on purpose.

    §5.13 writes `IdSource.client_ref(...)` with its arguments elided, so
    this records the call and answers the same ref whatever it is handed.
    """

    def __init__(self):
        self.calls = []

    def next_tick_id(self, *args, **kwargs):
        self.calls.append(("next_tick_id", args, kwargs))
        return "T-recovered"

    def leg_id(self, *args, **kwargs):
        self.calls.append(("leg_id", args, kwargs))
        return "leg-recovered"

    def plan_id(self, *args, **kwargs):
        self.calls.append(("plan_id", args, kwargs))
        return "plan-recovered"

    def client_ref(self, *args, **kwargs):
        self.calls.append(("client_ref", args, kwargs))
        return DERIVED_REF


def crashed_series():
    """A ledger a process died mid-tick against.

    Seq 1-4 are a complete tick T0; seq 5 snapshots at_seq 4; seq 6-11 are
    T1's opening records: a `tick_start` with no terminal `tick`, a
    submitted plan with no intent, a refused plan (nothing to recover), an
    intent with no `order_event`, and an intent that was acknowledged.
    """
    led = FakeLedger()
    producer = SeriesState(SERIES_ID)
    led.state = producer
    led.append(
        {
            "kind": "process",
            "id": "p-1",
            "body": {
                "event": "start",
                "series_id": SERIES_ID,
                "release_hash": RELEASE_HASH,
                "doc_hash": DIGEST_PLAN,
                "serving_hash": DIGEST_INPUTS,
                "run_hash": DIGEST_QUOTE,
                "artifact_digests": {},
                "source_config_hash": DIGEST_INPUTS,
                "runtime_fingerprint": DIGEST_RISK,
                "rung": "live_limited",
                "executor_kind": "paper",
                "code_version": "0.0.1",
            },
        }
    )
    led.append({"kind": "tick_start", "id": "ts-0",
                "body": {"tick_id": "T0", "tick_at_ms": BASE_MS,
                         "release_hash": RELEASE_HASH}})
    led.append({"kind": "tick", "id": "tk-0", "body": tick_body(tick_id="T0")})
    led.append({"kind": "decision", "id": "dc-0",
                "body": decision_body(tick_id="T0", legs=("leg-0",))})
    led.snapshot(producer.to_snapshot_obj())
    led.append({"kind": "tick_start", "id": "ts-1",
                "body": {"tick_id": "T1", "tick_at_ms": BASE_MS + 60_000,
                         "release_hash": RELEASE_HASH}})
    led.append({"kind": "decision_plan", "id": "dp-1",
                "body": decision_plan_body(plan_id="plan-1", result="submit")})
    led.append({"kind": "decision_plan", "id": "dp-2",
                "body": decision_plan_body(plan_id="plan-2", result="not_sent")})
    led.append({"kind": "intent", "id": "in-2",
                "body": intent_body(client_ref="ref-2")})
    led.append({"kind": "intent", "id": "in-3",
                "body": intent_body(client_ref="ref-3")})
    led.append({"kind": "order_event", "id": "oe-3",
                "body": order_event_body(client_ref="ref-3")})
    led.state = None
    return led


def recovered_once():
    """Run recovery over `crashed_series()`; return every collaborator."""
    led = crashed_series()
    seeded = len(led.records)
    st = SeriesState(SERIES_ID)
    led.state = st
    ids = FakeIdSource()
    executor = FakeExecutor(
        answers={
            "ref-2": FakeOrderState(client_ref="ref-2", venue_ref="v-2",
                                    status="open"),
        },
        raises=(DERIVED_REF,),
    )
    report = Recovery(led, st, ids, executor).run(FakeClock())
    return led, st, ids, executor, report, seeded


# ---------------------------------------------------------------------------
# StateView — the frozen projection, and only of the fold (§5.8.1)
# ---------------------------------------------------------------------------


def test_state_view_declares_exactly_the_fourteen_members_in_order():
    assert dataclasses.is_dataclass(StateView)
    assert tuple(f.name for f in dataclasses.fields(StateView)) == VIEW_MEMBERS


def test_state_view_is_frozen_and_offers_no_setter():
    st, chain = new_state()
    view = st.snapshot()
    assert StateView.__dataclass_params__.frozen is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.breaker = "halted"
    assert [name for name in dir(view) if name.startswith("set_")] == []


def test_state_view_members_are_immutable_containers():
    st, chain = new_state()
    fold(st, chain, "intent", intent_body())
    fold(st, chain, "order_event", order_event_body())
    fold(st, chain, "cash_flow", cash_flow_body())
    fold(st, chain, "guard_state", guard_state_body())
    fold(st, chain, "control_request",
         {"request_id": "req-1", "purpose": "arm_request", "payload": {},
          "principal_digest": DIGEST_RISK, "proof_digest": DIGEST_PLAN,
          "expires_ms": BASE_MS + 1_000})
    view = st.snapshot()
    for name in ("positions", "pending", "decision_history"):
        assert isinstance(getattr(view, name), tuple), name
    for name in ("working", "balances", "guard_holds", "pending_control"):
        assert isinstance(getattr(view, name), types.MappingProxyType), name
        with pytest.raises(TypeError):
            getattr(view, name)["forged"] = "x"


def test_a_taken_view_never_moves_under_a_later_fold():
    st, chain = new_state()
    before = st.snapshot()
    fold(st, chain, "fill", fill_body())
    assert before.positions == ()
    assert before.head_seq == 0
    assert st.snapshot().head_seq == 1


def test_a_fresh_fold_starts_at_the_genesis_head_with_an_empty_view():
    st, _ = new_state()
    assert st.head() == (0, GENESIS_HASH)
    view = st.snapshot()
    assert (view.positions, view.working, view.pending) == ((), {}, ())
    assert (view.balances, view.decision_history) == ({}, ())
    assert view.breaker == "active"
    assert view.arming is None and view.readiness is None
    assert view.reduction is None
    assert (view.guard_holds, view.pending_control) == ({}, {})
    assert view.risk_version.economic_seq == 0
    assert view.risk_version.executor_token is None
    assert view.risk_version.accounting_tokens is None
    assert (view.head_seq, view.head_hash) == (0, GENESIS_HASH)


def test_tick_state_carries_five_members_and_no_rung():
    names = tuple(f.name for f in dataclasses.fields(TickState))
    assert names == ("view", "account", "feed_status", "feed_ages", "calendar")
    assert TickState.__dataclass_params__.frozen is True
    assert "rung" not in names
    assert not hasattr(TickState, "rung")


# ---------------------------------------------------------------------------
# apply() — the envelope discipline (§5.8, §6)
# ---------------------------------------------------------------------------


def test_apply_refuses_an_unknown_kind():
    st, chain = new_state()
    with pytest.raises(ProductionError):
        st.apply(chain.env("not_a_record_kind"))
    assert st.head() == (0, GENESIS_HASH)


def test_apply_refuses_a_sequence_gap():
    st, chain = new_state()
    fold(st, chain, "tick_start", {"tick_id": "T1", "tick_at_ms": BASE_MS})
    ahead = chain.env("tick_start", {"tick_id": "T2", "tick_at_ms": BASE_MS})
    ahead["seq"] = 4
    with pytest.raises(ProductionError):
        st.apply(ahead)
    assert st.head()[0] == 1


def test_apply_refuses_a_repeated_sequence():
    st, chain = new_state()
    env = fold(st, chain, "tick_start", {"tick_id": "T1", "tick_at_ms": BASE_MS})
    with pytest.raises(ProductionError):
        st.apply(dict(env))
    assert st.head()[0] == 1


def test_apply_refuses_a_record_from_another_series():
    st, _ = new_state()
    foreign = Chain(series_id=OTHER_SERIES_ID)
    with pytest.raises(ProductionError):
        st.apply(foreign.env("tick_start", {"tick_id": "T1", "tick_at_ms": BASE_MS}))
    assert st.head() == (0, GENESIS_HASH)


def test_apply_refuses_a_record_that_is_not_a_mapping():
    st, _ = new_state()
    with pytest.raises(ProductionError):
        st.apply(["kind", "tick_start"])


def test_apply_tolerates_an_unknown_body_field():
    # §5.8: "Readers tolerate unknown fields and upcast schema_version."
    st, chain = new_state()
    fold(st, chain, "tick_start",
         {"tick_id": "T1", "tick_at_ms": BASE_MS, "future_field": {"v": 2}})
    assert st.open_ticks()[0].tick_id == "T1"


def test_head_reports_the_last_folded_seq_and_hash():
    st, chain = new_state()
    first = fold(st, chain, "tick_start", {"tick_id": "T1", "tick_at_ms": BASE_MS})
    assert st.head() == (1, first["hash"])
    second = fold(st, chain, "tick", tick_body(tick_id="T1"))
    assert st.head() == (2, second["hash"])
    assert st.snapshot().head_hash == second["hash"]


def test_every_record_kind_in_the_vocabulary_folds():
    pairs = full_sequence()
    assert {kind for kind, _ in pairs} == set(vocab.RECORD_KINDS)
    st, chain = new_state()
    fold_sequence(st, chain, pairs)
    assert st.head()[0] == len(pairs)


# ---------------------------------------------------------------------------
# The fold — orders, fills, money (§5.8.1, D14)
# ---------------------------------------------------------------------------


def test_an_intent_is_pending_not_working_and_not_economic():
    # D14: "an intent becomes economic when it is acknowledged, not when
    # it is recorded" — step (7)'s exact-version recheck depends on it.
    st, chain = new_state()
    fold(st, chain, "intent", intent_body(client_ref="ref-1"))
    view = st.snapshot()
    assert view.pending == ("ref-1",)
    assert dict(view.working) == {}
    assert view.risk_version.economic_seq == 0


def test_an_ack_moves_the_ref_from_pending_into_working_and_is_economic():
    st, chain = new_state()
    fold(st, chain, "intent", intent_body(client_ref="ref-1", qty="10"))
    fold(st, chain, "order_event",
         order_event_body(client_ref="ref-1", event="ack", status="open"))
    view = st.snapshot()
    assert view.pending == ()
    order = view.working["ref-1"]
    assert order.qty == Decimal("10")
    assert order.filled_qty == Decimal("0")
    assert order.remaining_qty == Decimal("10")
    assert order.instrument == "AAA"
    assert order.venue_ref == "v-1"
    assert view.risk_version.economic_seq == 1


@pytest.mark.parametrize(
    "event,status",
    [
        ("cancel", "cancelled"),
        ("expire", "expired"),
        ("reject", "rejected"),
        ("not_sent", "not_sent"),
    ],
)
def test_a_terminal_order_event_leaves_working(event, status):
    assert status in vocab.TERMINAL_STATUSES
    st, chain = new_state()
    fold(st, chain, "intent", intent_body(client_ref="ref-1"))
    fold(st, chain, "order_event",
         order_event_body(client_ref="ref-1", event="ack", status="open"))
    assert "ref-1" in st.snapshot().working
    fold(st, chain, "order_event",
         order_event_body(client_ref="ref-1", event=event, status=status))
    view = st.snapshot()
    assert dict(view.working) == {}
    assert view.pending == ()


def test_an_order_event_for_an_unknown_ref_is_tolerated():
    # Recovery appends one for a plan whose intent was never written, so
    # the fold cannot refuse a ref it has no intent for.
    st, chain = new_state()
    fold(st, chain, "order_event",
         order_event_body(client_ref="never-seen", event="unknown",
                          status="unknown"))
    assert dict(st.snapshot().working) == {}


def test_a_fill_moves_the_position_book_and_the_working_order():
    st, chain = new_state()
    fold(st, chain, "intent", intent_body(client_ref="ref-1", qty="10"))
    fold(st, chain, "order_event",
         order_event_body(client_ref="ref-1", event="ack", status="open"))
    fold(st, chain, "fill",
         fill_body(fill_id="f-1", client_ref="ref-1", qty="4", price="101"))
    view = st.snapshot()
    held = position_of(view.positions, "AAA")
    assert held.qty == Decimal("4")
    assert held.avg_cost == Decimal("101")
    assert held.source == "derived"
    order = view.working["ref-1"]
    assert order.filled_qty == Decimal("4")
    assert order.remaining_qty == Decimal("6")
    assert order.filled_qty + order.remaining_qty == order.qty
    assert view.risk_version.economic_seq == 2


def test_a_reversed_fill_is_undone_exactly_once():
    st, chain = new_state()
    fold(st, chain, "intent", intent_body(client_ref="ref-1", qty="20"))
    fold(st, chain, "order_event",
         order_event_body(client_ref="ref-1", event="ack", status="open"))
    fold(st, chain, "fill",
         fill_body(fill_id="f-1", client_ref="ref-1", qty="10", price="100"))
    fold(st, chain, "fill",
         fill_body(fill_id="f-2", client_ref="ref-1", qty="10", price="120"))
    both = position_of(st.snapshot().positions, "AAA")
    assert (both.qty, both.avg_cost) == (Decimal("20"), Decimal("110"))
    fold(st, chain, "fill",
         fill_body(fill_id="f-2", client_ref="ref-1", qty="10", price="120",
                   status="reversed"))
    undone = position_of(st.snapshot().positions, "AAA")
    assert undone.qty == Decimal("10")
    assert undone.avg_cost == Decimal("100")
    assert st.snapshot().risk_version.economic_seq == 4
    with pytest.raises(ProductionError):
        st.apply(chain.env("fill",
                           fill_body(fill_id="f-2", client_ref="ref-1",
                                     qty="10", price="120", status="reversed")))
    still = position_of(st.snapshot().positions, "AAA")
    assert (still.qty, still.avg_cost) == (Decimal("10"), Decimal("100"))


def test_a_cash_flow_adjusts_the_balance_and_is_economic():
    st, chain = new_state()
    fold(st, chain, "cash_flow", cash_flow_body(amount="250"))
    fold(st, chain, "cash_flow",
         cash_flow_body(amount="-100", external=True, flow_kind="withdrawal"))
    fold(st, chain, "cash_flow", cash_flow_body(currency="EUR", amount="40"))
    view = st.snapshot()
    assert view.balances["USD"] == Decimal("150")
    assert view.balances["EUR"] == Decimal("40")
    assert view.risk_version.economic_seq == 3
    assert view.decision_history == ()
    assert view.positions == ()


def test_only_economic_events_advance_the_economic_seq():
    # D14: fills, order events and cash flows move it; an `intent`, an
    # `authority_use`, an `outcome` and an `adoption` deliberately do not.
    st, chain = new_state()
    pairs = full_sequence()
    expected = 0
    walked = 0
    for kind, body in pairs:
        if kind == "snapshot":
            payload = st.to_snapshot_obj()
            body = {"at_seq": st.head()[0], "state": payload,
                    "state_digest": canonical_hash(payload)}
        fold(st, chain, kind, body)
        if kind in ECONOMIC_KINDS:
            expected += 1
        walked += 1
        assert st.snapshot().risk_version.economic_seq == expected, kind
    assert walked == len(pairs)
    assert expected == 3


# ---------------------------------------------------------------------------
# The fold — breaker, arming, reduction rights (D10, D11, D12)
# ---------------------------------------------------------------------------


def test_a_trip_folds_the_breaker_state():
    st, chain = new_state()
    assert st.snapshot().breaker == "active"
    fold(st, chain, "trip", trip_body("active", "reducing"))
    assert st.snapshot().breaker == "reducing"
    fold(st, chain, "trip", trip_body("reducing", "halted"))
    assert st.snapshot().breaker == "halted"
    assert st.snapshot().breaker in vocab.BREAKER_STATES


def test_last_trip_tracks_the_latest_trip_and_is_none_before_any():
    """§5.8.1: the breaker's reset reads the trip it acknowledges — and
    the instant cooling-off runs from — off the fold, never off the ledger."""
    st, chain = new_state()
    assert st.last_trip() is None
    first = fold(st, chain, "trip", trip_body("active", "reducing"))
    latest = st.last_trip()
    assert isinstance(latest, types.MappingProxyType)
    assert set(latest) == {"id", "seq", "recorded_at_ms", "from", "to", "reason",
                           "acknowledged_trip_id"}
    assert (latest["id"], latest["seq"], latest["recorded_at_ms"]) == (
        first["id"], first["seq"], first["recorded_at_ms"])
    assert (latest["from"], latest["to"]) == ("active", "reducing")
    second = fold(st, chain, "trip", trip_body("reducing", "halted"))
    assert st.last_trip()["id"] == second["id"]
    assert st.last_trip()["to"] == "halted"
    restored = SeriesState(SERIES_ID)
    restored.restore(snapshot_env(st, chain))
    assert restored.last_trip() == st.last_trip()


def test_an_ordinary_authority_issue_folds_into_the_arming():
    st, chain = new_state()
    fold(st, chain, "authority", authority_body(event="issue", role="ordinary"))
    arming = st.snapshot().arming
    assert arming.authority_id == "auth-1"
    assert arming.release_hash == RELEASE_HASH
    assert arming.rung == "live_limited"
    assert arming.maker == "principal-maker"
    assert arming.checker == "principal-checker"
    assert arming.armed_at_ms == BASE_MS
    assert arming.armed_until_ms == ARMED_UNTIL_MS
    assert tuple(arming.allowlist) == ("AAA",)
    assert arming.limits_overlay == {}
    assert arming.request_proof_digest == DIGEST_PLAN
    assert arming.approval_proof_digest == DIGEST_EVIDENCE


@pytest.mark.parametrize("event", ["disarm", "revoke", "expire"])
def test_disarm_revoke_and_expire_clear_the_arming(event):
    assert vocab.AUTHORITY_EVENTS == ("issue", "disarm", "revoke", "expire")
    st, chain = new_state()
    fold(st, chain, "authority", authority_body(event="issue"))
    assert st.snapshot().arming is not None
    fold(st, chain, "authority", authority_body(event=event))
    assert st.snapshot().arming is None


def test_leaving_active_revokes_the_ordinary_arm():
    # D10/D12: "Any transition away from `active` revokes ordinary arming."
    st, chain = new_state()
    fold(st, chain, "authority", authority_body(event="issue"))
    assert st.snapshot().arming is not None
    fold(st, chain, "trip", trip_body("active", "reducing"))
    view = st.snapshot()
    assert view.breaker == "reducing"
    assert view.arming is None


def test_a_reduction_authority_folds_into_the_reduction_projection():
    st, chain = new_state()
    fold(st, chain, "authority",
         authority_body(role="reduction", authority_id="auth-2",
                        rights=(RIGHT_A, RIGHT_B)))
    reduction = st.snapshot().reduction
    assert reduction.authority_id == "auth-2"
    assert tuple(reduction.rights) == (RIGHT_A, RIGHT_B)
    assert tuple(reduction.reserved) == ()
    assert reduction.expires_ms == RIGHTS_EXPIRE_MS
    assert st.snapshot().arming is None


def test_an_authority_use_reserves_one_right_and_is_not_economic():
    # D14: "an `authority_use` is a rights reservation, not an economic
    # one, and must not advance it".
    st, chain = new_state()
    fold(st, chain, "authority",
         authority_body(role="reduction", authority_id="auth-2",
                        rights=(RIGHT_A, RIGHT_B)))
    before = st.snapshot().risk_version.economic_seq
    fold(st, chain, "authority_use", authority_use_body(digest=RIGHT_A))
    view = st.snapshot()
    assert tuple(view.reduction.reserved) == (RIGHT_A,)
    assert tuple(view.reduction.rights) == (RIGHT_A, RIGHT_B)
    assert view.risk_version.economic_seq == before


def test_a_second_use_of_one_reduction_right_refuses():
    # D12: "each digest is single-use"; the reservation "is never erased
    # or reused".
    st, chain = new_state()
    fold(st, chain, "authority",
         authority_body(role="reduction", authority_id="auth-2",
                        rights=(RIGHT_A, RIGHT_B)))
    fold(st, chain, "authority_use", authority_use_body(digest=RIGHT_A))
    with pytest.raises(ProductionError):
        st.apply(chain.env("authority_use",
                           authority_use_body(digest=RIGHT_A,
                                              client_ref="ref-dup")))
    assert tuple(st.snapshot().reduction.reserved) == (RIGHT_A,)


def test_an_authority_use_of_an_ungranted_digest_refuses():
    st, chain = new_state()
    fold(st, chain, "authority",
         authority_body(role="reduction", authority_id="auth-2",
                        rights=(RIGHT_A,)))
    with pytest.raises(ProductionError):
        st.apply(chain.env("authority_use", authority_use_body(digest=RIGHT_B)))
    assert tuple(st.snapshot().reduction.reserved) == ()


def test_revoking_a_reduction_authority_clears_the_projection():
    st, chain = new_state()
    fold(st, chain, "authority",
         authority_body(role="reduction", authority_id="auth-2",
                        rights=(RIGHT_A, RIGHT_B)))
    fold(st, chain, "authority",
         authority_body(event="revoke", role="reduction",
                        authority_id="auth-2", rights=(RIGHT_A, RIGHT_B)))
    assert st.snapshot().reduction is None


# ---------------------------------------------------------------------------
# The fold — readiness, guard holds, control, decisions, open ticks
# ---------------------------------------------------------------------------


def test_a_readiness_record_folds_into_the_view():
    st, chain = new_state()
    fold(st, chain, "readiness", readiness_body(verdict="go"))
    ready = st.snapshot().readiness
    assert ready.verdict == "go"
    assert ready.verdict in vocab.READINESS_VERDICTS
    assert ready.readiness_digest == DIGEST_READY
    assert ready.evaluated_at_ms == BASE_MS
    assert ready.valid_until_ms == BASE_MS + 900_000
    assert len(ready.items) == 2
    fold(st, chain, "readiness", readiness_body(verdict="no_go"))
    assert st.snapshot().readiness.verdict == "no_go"


def test_guard_holds_and_pauses_survive_in_the_fold():
    # §5.5: a pause held only in a strategy object is the restart amnesia
    # the fold exists to prevent.
    st, chain = new_state()
    fold(st, chain, "guard_state", guard_state_body())
    fold(st, chain, "guard_state",
         guard_state_body(guard="strategy", scope_key="*", state_kind="pause",
                          held_until_ms=None, resume_at_ms=BASE_MS + 300_000))
    holds = st.snapshot().guard_holds
    assert sorted(holds) == [("day_loss", "AAA"), ("strategy", "*")]
    held = holds[("day_loss", "AAA")]
    assert held["state_kind"] == "hold"
    assert held["held_until_ms"] == BASE_MS + 600_000
    assert held["resume_at_ms"] is None
    paused = holds[("strategy", "*")]
    assert paused["state_kind"] == "pause"
    assert paused["resume_at_ms"] == BASE_MS + 300_000
    assert isinstance(held, types.MappingProxyType)


def test_a_control_request_is_pending_until_its_command_result():
    st, chain = new_state()
    fold(st, chain, "control_request",
         {"request_id": "req-1", "purpose": "arm_request", "payload": {},
          "principal_digest": DIGEST_RISK, "proof_digest": DIGEST_PLAN,
          "expires_ms": BASE_MS + 600_000})
    fold(st, chain, "control_request",
         {"request_id": "req-2", "purpose": "reduce", "payload": {},
          "principal_digest": DIGEST_RISK, "proof_digest": DIGEST_PLAN,
          "expires_ms": BASE_MS + 600_000})
    pending = st.snapshot().pending_control
    assert sorted(pending) == ["req-1", "req-2"]
    assert pending["req-1"] == "arm_request"
    fold(st, chain, "command_result",
         {"request_id": "req-1", "status": "applied", "record_ids": [],
          "reason": None})
    assert sorted(st.snapshot().pending_control) == ["req-2"]


def test_a_decision_appends_its_legs_to_a_bounded_history():
    st = SeriesState(SERIES_ID, max_history=2)
    chain = Chain()
    fold(st, chain, "decision", decision_body(tick_id="T1",
                                              legs=("leg-1", "leg-2")))
    history = st.snapshot().decision_history
    assert [entry["leg_id"] for entry in history] == ["leg-1", "leg-2"]
    assert [entry["tick_id"] for entry in history] == ["T1", "T1"]
    assert history[0]["client_ref"] == "ref-1"
    assert isinstance(history[0], types.MappingProxyType)
    fold(st, chain, "decision", decision_body(tick_id="T2",
                                              legs=("leg-3", "leg-4")))
    newest = st.snapshot().decision_history
    assert [entry["leg_id"] for entry in newest] == ["leg-3", "leg-4"]
    assert [entry["tick_id"] for entry in newest] == ["T2", "T2"]


def test_the_history_bound_has_one_named_default():
    assert isinstance(DEFAULT_MAX_HISTORY, int)
    assert DEFAULT_MAX_HISTORY >= 1
    params = inspect.signature(SeriesState.__init__).parameters
    assert "max_history" in params
    assert params["max_history"].default in (None, DEFAULT_MAX_HISTORY)
    with pytest.raises(ProductionError):
        SeriesState(SERIES_ID, max_history=0)


def test_open_ticks_track_a_tick_start_until_its_terminal_tick():
    st, chain = new_state()
    fold(st, chain, "tick_start", {"tick_id": "T1", "tick_at_ms": BASE_MS})
    fold(st, chain, "tick_start", {"tick_id": "T2", "tick_at_ms": BASE_MS + 60_000})
    assert isinstance(st.open_ticks(), tuple)
    assert [t.tick_id for t in st.open_ticks()] == ["T1", "T2"]
    first = st.open_ticks()[0]
    assert first.tick_at_ms == BASE_MS
    assert first.release_hash == RELEASE_HASH
    fold(st, chain, "tick", tick_body(tick_id="T1"))
    assert [t.tick_id for t in st.open_ticks()] == ["T2"]
    assert not hasattr(st.snapshot(), "open_ticks")


def test_a_snapshot_record_only_moves_the_head():
    st, chain = new_state()
    fold(st, chain, "intent", intent_body(client_ref="ref-1"))
    fold(st, chain, "cash_flow", cash_flow_body())
    before = st.snapshot()
    payload = st.to_snapshot_obj()
    env = fold(st, chain, "snapshot",
               {"at_seq": st.head()[0], "state": payload,
                "state_digest": canonical_hash(payload)})
    after = st.snapshot()
    for name in VIEW_MEMBERS[:-2]:
        assert getattr(after, name) == getattr(before, name), name
    assert after.head_seq == before.head_seq + 1
    assert after.head_hash == env["hash"]


# ---------------------------------------------------------------------------
# PositionBook — our side of the two-sided comparison (§5.7, §5.8.1)
# ---------------------------------------------------------------------------


def book_with(*bodies):
    """A `PositionBook` with each fill body applied in order."""
    book = PositionBook()
    for body in bodies:
        book.apply(Fill.from_obj(body))
    return book


def test_cost_basis_averages_on_an_increase_and_holds_on_a_reduction():
    book = book_with(
        fill_body(fill_id="f-1", qty="10", price="100"),
        fill_body(fill_id="f-2", qty="10", price="120"),
    )
    assert book.net_qty("AAA") == Decimal("20")
    # (10 * 100 + 10 * 120) / 20
    assert position_of(book.positions(), "AAA").avg_cost == Decimal("110")
    book.apply(Fill.from_obj(
        fill_body(fill_id="f-3", side="sell", qty="5", price="130")))
    assert book.net_qty("AAA") == Decimal("15")
    assert position_of(book.positions(), "AAA").avg_cost == Decimal("110")


def test_a_sell_through_flat_flips_the_sign_and_rebases_the_cost():
    book = book_with(
        fill_body(fill_id="f-1", qty="10", price="100"),
        fill_body(fill_id="f-2", side="sell", qty="25", price="90"),
    )
    assert book.net_qty("AAA") == Decimal("-15")
    assert position_of(book.positions(), "AAA").avg_cost == Decimal("90")


def test_reverse_restores_the_state_the_fill_changed():
    book = book_with(
        fill_body(fill_id="f-1", qty="10", price="100"),
        fill_body(fill_id="f-2", qty="10", price="120"),
    )
    book.reverse("f-2")
    held = position_of(book.positions(), "AAA")
    assert (held.qty, held.avg_cost) == (Decimal("10"), Decimal("100"))
    book.reverse("f-1")
    assert book.net_qty("AAA") == Decimal("0")
    assert book.positions() == ()


def test_reverse_recomputes_the_position_from_the_remaining_log():
    # Not "undo the last fill": reversing the FIRST leaves exactly what
    # applying the second alone would have built.
    book = book_with(
        fill_body(fill_id="f-1", qty="10", price="100"),
        fill_body(fill_id="f-2", qty="10", price="120"),
    )
    book.reverse("f-1")
    held = position_of(book.positions(), "AAA")
    assert (held.qty, held.avg_cost) == (Decimal("10"), Decimal("120"))
    alone = book_with(fill_body(fill_id="f-2", qty="10", price="120"))
    assert book.positions() == alone.positions()


def test_reverse_refuses_a_fill_realised_by_going_flat():
    # The log runs from the last flat: once the position closes, those
    # fills are realised and there is nothing left to recompute from.
    book = book_with(
        fill_body(fill_id="f-1", qty="10", price="100"),
        fill_body(fill_id="f-2", side="sell", qty="10", price="130"),
    )
    assert book.positions() == ()
    for fill_id in ("f-1", "f-2"):
        with pytest.raises(ProductionError):
            book.reverse(fill_id)
    assert book.net_qty("AAA") == Decimal("0")


def test_reverse_refuses_an_unknown_fill():
    book = book_with(fill_body(fill_id="f-1", qty="10", price="100"))
    with pytest.raises(ProductionError):
        book.reverse("never-applied")
    assert book.net_qty("AAA") == Decimal("10")


def test_reverse_refuses_a_second_reversal_of_one_fill():
    book = book_with(
        fill_body(fill_id="f-1", qty="10", price="100"),
        fill_body(fill_id="f-2", qty="10", price="120"),
    )
    book.reverse("f-2")
    with pytest.raises(ProductionError):
        book.reverse("f-2")
    assert book.net_qty("AAA") == Decimal("10")


def test_positions_are_sorted_derived_and_unknown_instruments_are_flat():
    book = book_with(
        fill_body(fill_id="f-1", instrument="BBB", qty="3", price="10"),
        fill_body(fill_id="f-2", instrument="AAA", qty="2", price="20"),
    )
    assert [p.instrument for p in book.positions()] == ["AAA", "BBB"]
    assert {p.source for p in book.positions()} == {"derived"}
    assert "derived" in vocab.POSITION_SOURCES
    assert book.net_qty("ZZZ") == Decimal("0")
    assert isinstance(book.net_qty("ZZZ"), Decimal)


# ---------------------------------------------------------------------------
# Snapshot and restore (§6 `snapshot`, §5.8)
# ---------------------------------------------------------------------------


def snapshot_env(st, chain):
    """The `snapshot` envelope the ledger would write for `st`."""
    payload = st.to_snapshot_obj()
    return chain.env(
        "snapshot",
        {"at_seq": st.head()[0], "state": payload,
         "state_digest": canonical_hash(payload)},
    )


def test_the_snapshot_payload_carries_every_member_plus_monitor_state():
    st, chain = new_state()
    fold_sequence(st, chain, full_sequence())
    payload = st.to_snapshot_obj()
    required = set(VIEW_MEMBERS) | {"monitor_state"}
    assert not required - set(payload)
    # It is what a ledger canonicalises, so it must be JSON-ready.
    canonical_bytes(payload)
    assert isinstance(st.monitor_state(), types.MappingProxyType)
    assert st.monitor_state()


def test_restore_rebuilds_the_fold_and_drops_live_session_tokens():
    st, chain = new_state()
    fold(st, chain, "intent", intent_body(client_ref="ref-1", qty="10"))
    fold(st, chain, "order_event", order_event_body(client_ref="ref-1"))
    fold(st, chain, "fill", fill_body(fill_id="f-1", qty="4", price="101"))
    fold(st, chain, "cash_flow", cash_flow_body())
    fold(st, chain, "guard_state", guard_state_body())
    fold(st, chain, "trip", trip_body("active", "reducing"))
    env = snapshot_env(st, chain)

    loaded = copy.deepcopy(env)
    tokens = loaded["body"]["state"]["risk_version"]
    tokens["executor_token"] = "live-session-token"
    tokens["accounting_tokens"] = {"paper": 7}
    restored = SeriesState(SERIES_ID)
    restored.restore(loaded)

    view = restored.snapshot()
    assert view.risk_version.executor_token is None
    assert view.risk_version.accounting_tokens is None
    assert view.risk_version.economic_seq == 3
    assert restored.head() == (env["body"]["at_seq"],
                               env["body"]["state"]["head_hash"])
    assert view == st.snapshot()


def test_restore_refuses_a_record_that_is_not_a_snapshot():
    chain = Chain()
    with pytest.raises(ProductionError):
        SeriesState(SERIES_ID).restore(
            chain.env("tick_start", {"tick_id": "T1", "tick_at_ms": BASE_MS}))


def test_restore_refuses_a_payload_missing_a_member():
    st, chain = new_state()
    fold(st, chain, "cash_flow", cash_flow_body())
    broken = copy.deepcopy(snapshot_env(st, chain))
    broken["body"]["state"].pop("guard_holds")
    with pytest.raises(ProductionError):
        SeriesState(SERIES_ID).restore(broken)


def test_restore_refuses_an_at_seq_that_disagrees_with_the_head_it_carries():
    st, chain = new_state()
    fold(st, chain, "cash_flow", cash_flow_body())
    broken = copy.deepcopy(snapshot_env(st, chain))
    broken["body"]["at_seq"] = broken["body"]["at_seq"] + 1
    with pytest.raises(ProductionError):
        SeriesState(SERIES_ID).restore(broken)


def test_restore_refuses_a_fold_that_has_already_started():
    st, chain = new_state()
    fold(st, chain, "cash_flow", cash_flow_body())
    env = snapshot_env(st, chain)
    other, other_chain = new_state()
    fold(other, other_chain, "cash_flow", cash_flow_body())
    with pytest.raises(ProductionError):
        other.restore(env)


def test_a_reversal_after_a_restart_recomputes_from_the_restored_log():
    st, chain = new_state()
    fold(st, chain, "intent", intent_body(client_ref="ref-1", qty="20"))
    fold(st, chain, "order_event", order_event_body(client_ref="ref-1"))
    fold(st, chain, "fill", fill_body(fill_id="f-1", qty="10", price="100"))
    fold(st, chain, "fill", fill_body(fill_id="f-2", qty="10", price="120"))
    env = snapshot_env(st, chain)

    restored = SeriesState(SERIES_ID)
    restored.restore(env)
    restored.apply(env)
    fold(restored, chain, "fill",
         fill_body(fill_id="f-1", qty="10", price="100", status="reversed"))

    held = position_of(restored.snapshot().positions, "AAA")
    alone = book_with(fill_body(fill_id="f-2", qty="10", price="120"))
    assert (held.qty, held.avg_cost) == (Decimal("10"), Decimal("120"))
    assert (held,) == alone.positions()


def test_recovery_from_the_last_snapshot_reproduces_the_same_view():
    first, chain = new_state()
    for kind, body in (
        ("tick_start", {"tick_id": "T1", "tick_at_ms": BASE_MS}),
        ("readiness", readiness_body()),
        ("authority", authority_body()),
        ("intent", intent_body(client_ref="ref-1", qty="10")),
        ("order_event", order_event_body(client_ref="ref-1")),
        ("fill", fill_body(fill_id="f-1", qty="4", price="101")),
        ("cash_flow", cash_flow_body()),
        ("guard_state", guard_state_body()),
        ("decision", decision_body()),
    ):
        fold(first, chain, kind, body)
    env = snapshot_env(first, chain)
    first.apply(env)
    later = [
        chain.env("fill", fill_body(fill_id="f-2", qty="6", price="99")),
        chain.env("trip", trip_body("active", "reducing")),
        chain.env("monitor", {"monitor": "drift", "slice": "all",
                              "window": "1d", "statistic": "0.03",
                              "threshold": "0.10", "status": "ok",
                              "provisional": False}),
        chain.env("tick", tick_body(tick_id="T1")),
    ]
    for record in later:
        first.apply(record)

    second = SeriesState(SERIES_ID)
    second.restore(env)
    assert second.head() == (env["body"]["at_seq"],
                             env["body"]["state"]["head_hash"])
    second.apply(env)
    assert second.head() == (env["seq"], env["hash"])
    for record in later:
        second.apply(record)

    assert second.snapshot() == first.snapshot()
    assert second.monitor_state() == first.monitor_state()
    assert second.to_snapshot_obj() == first.to_snapshot_obj()


# ---------------------------------------------------------------------------
# Recovery (§5.8.1, §5.13, D13)
# ---------------------------------------------------------------------------


def test_recovery_replays_only_what_follows_the_last_snapshot():
    led, st, ids, executor, report, seeded = recovered_once()
    assert seeded == 11
    assert report.replayed == 7
    # T0's decision is older than the snapshot: it is in the view only
    # because the snapshot payload was restored first.
    assert [entry["tick_id"] for entry in st.snapshot().decision_history] == ["T0"]


def test_recovery_closes_an_open_tick_with_a_failed_terminal_pair():
    led, st, ids, executor, report, seeded = recovered_once()
    appended = led.records[seeded:]
    assert appended[0]["kind"] == "tick"
    assert appended[0]["body"]["tick_id"] == "T1"
    assert appended[0]["body"]["status"] == "failed"
    assert appended[1]["kind"] == "decision"
    assert appended[1]["body"]["tick_id"] == "T1"
    assert appended[1]["body"]["legs"] == []
    assert tuple(report.closed_ticks) == ("T1",)
    assert st.open_ticks() == ()


def test_recovery_queries_every_unmatched_ref_and_never_submits():
    led, st, ids, executor, report, seeded = recovered_once()
    # ref-3 was acknowledged and plan-2 terminalised as not_sent: neither
    # is ambiguous, so neither is queried.
    assert set(executor.queried) == {DERIVED_REF, "ref-2"}
    assert len(executor.queried) == 2
    assert set(report.queried_refs) == {DERIVED_REF, "ref-2"}
    assert executor.submitted == []
    assert any(call[0] == "client_ref" for call in ids.calls)


def test_recovery_records_the_venue_answer_as_a_status_event():
    led, st, ids, executor, report, seeded = recovered_once()
    (answered,) = [r for r in led.records[seeded:]
                   if r["kind"] == "order_event"
                   and r["body"]["client_ref"] == "ref-2"]
    assert answered["body"]["event"] == "status"
    assert answered["body"]["status"] == "open"
    assert answered["body"]["venue_ref"] == "v-2"
    view = st.snapshot()
    assert sorted(view.working) == ["ref-2", "ref-3"]
    assert view.pending == ()


def test_recovery_records_unknown_when_the_executor_raises():
    led, st, ids, executor, report, seeded = recovered_once()
    (ambiguous,) = [r for r in led.records[seeded:]
                    if r["kind"] == "order_event"
                    and r["body"]["client_ref"] == DERIVED_REF]
    assert ambiguous["body"]["event"] == "unknown"
    assert ambiguous["body"]["status"] == "unknown"
    assert "unknown" in vocab.ORDER_EVENTS
    assert "unknown" in vocab.STATUSES


def test_recovery_ends_with_a_recovered_process_record():
    led, st, ids, executor, report, seeded = recovered_once()
    assert led.kinds(seeded) == [
        "tick", "decision", "order_event", "order_event", "process",
    ]
    last = led.records[-1]
    assert last["body"]["event"] == "recovered"
    assert "recovered" in vocab.PROCESS_EVENTS
    assert led.barrier_calls >= 1


def test_recovering_twice_appends_no_second_recovery():
    led, st, ids, executor, report, seeded = recovered_once()
    after_first = len(led.records)
    assert after_first == 16
    second = SeriesState(SERIES_ID)
    led.state = second
    again = Recovery(led, second, FakeIdSource(), executor).run(FakeClock())
    assert again.replayed == 12
    assert tuple(again.closed_ticks) == ()
    assert tuple(again.queried_refs) == ()
    assert len(executor.queried) == 2
    assert [k for k in led.kinds(after_first) if k != "process"] == []
    assert sorted(second.snapshot().working) == ["ref-2", "ref-3"]


def test_the_recovery_report_is_a_frozen_value():
    led, st, ids, executor, report, seeded = recovered_once()
    assert dataclasses.is_dataclass(report)
    assert type(report).__dataclass_params__.frozen is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.replayed = 99


# ---------------------------------------------------------------------------
# Nothing else folds the ledger (§5.8.1, AST)
# ---------------------------------------------------------------------------

PACKAGE_DIR = pathlib.Path(state_module.__file__).parent

# state.py folds; these read history for their own report or verb.
SCAN_READERS = frozenset({
    "state.py", "ledger.py", "reconcile.py", "__main__.py",
    "outcomes.py", "report.py",
})
FOLD_OWNERS = frozenset({"state.py", "ledger.py"})
FOLDED_ATTRS = frozenset({
    "positions", "working", "breaker", "arming", "readiness",
    "guard_holds", "reduction", "pending_control",
})


def module_paths():
    """Every module in the package as it stands."""
    return sorted(PACKAGE_DIR.rglob("*.py"))


def scan_calls(tree):
    """Line numbers of every `<something>.scan(...)` call."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "scan"
    ]


def self_assignments(tree, names):
    """`(attr, line)` for every `self.<attr> = ...` naming one of `names`."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        else:
            continue
        flat = []
        for target in targets:
            if isinstance(target, (ast.Tuple, ast.List)):
                flat.extend(target.elts)
            else:
                flat.append(target)
        for target in flat:
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr in names):
                found.append((target.attr, target.lineno))
    return found


def test_the_ast_scanners_catch_what_they_forbid():
    source = (
        "class Blotter:\n"
        "    def rebuild(self, ledger):\n"
        "        for record in ledger.scan(kind='fill'):\n"
        "            self.positions = record\n"
        "            self.working, self.arming = {}, None\n"
    )
    tree = ast.parse(source)
    assert scan_calls(tree) == [3]
    assert self_assignments(tree, FOLDED_ATTRS) == [
        ("positions", 4), ("working", 5), ("arming", 5),
    ]
    assert self_assignments(ast.parse("self.other = 1\n"), FOLDED_ATTRS) == []


def test_only_the_fold_and_its_named_readers_scan_the_ledger():
    paths = module_paths()
    assert paths, PACKAGE_DIR
    offenders = []
    for path in paths:
        if path.name in SCAN_READERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders.extend(f"{path.name}:{line}" for line in scan_calls(tree))
    assert offenders == []


def test_no_other_module_owns_the_folded_attributes():
    offenders = []
    for path in module_paths():
        if path.name in FOLD_OWNERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders.extend(
            f"{path.name}:{line}:{attr}"
            for attr, line in self_assignments(tree, FOLDED_ATTRS)
        )
    assert offenders == []
