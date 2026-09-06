"""`control.py` — the durable command spool and the sole-writer processor (§5.8, D15).

A control CLI runs in a different process from the serve loop, and the two
share nothing but a filesystem. §5.8 resolves that with a spool: the CLI writes
one fsynced `commands/inbox/<request_id>.json` and returns; the process holding
`serve.lock` — the *sole ledger writer* — consumes it, appends its records,
barriers, and only then moves the file to `commands/applied` or
`commands/rejected`. Everything asserted here follows from that ordering.

Five properties carry the weight:

* **Retry vs repeat.** The caller's UUID `request_id` is reused *only* for a
  retry, so the same id with the same payload digest is idempotent (the queued
  path comes back, nothing is written twice) while the same id with a
  *different* payload refuses. A legitimate repeated command uses a new id.
  Without both halves a crashed CLI could either duplicate a money-moving
  command or be unable to retry one.
* **Record before receipt (D13).** The `control_request`, the handler's
  records and the `command_result` are appended and barriered *before* the file
  is moved. A crash anywhere leaves either an inbox file with no receipt (the
  gap §8 says `verify` reports) or a receipt whose ledger records exist — never
  a receipt for records that were never written.
* **The receipt is written before the inbox file is unlinked**, so a crash in
  between leaves both; `pending()` therefore skips any request that already has
  a terminal receipt. Losing a queued command is the failure this avoids.
* **`CommandProcessor` owns no verb logic.** It dispatches to handlers injected
  by `compose.py`; the absence of a handler IS the refusal, which is how the
  synchronous CLI path (no running loop) refuses `execute-flatten` without the
  processor knowing what a flatten is. An AST scan pins that no purpose literal
  is compared anywhere in the module.
* **No secret reaches the ledger.** The proof bytes travel in the inbox file so
  the writer can re-verify them; they never appear in an appended record.

The HALT sentinel is deliberately *not* the inbox's business: `halt` creates it
before queueing its audit command, and that ordering is `__main__`'s. What is
pinned here is the seam — queueing neither creates nor requires it, and the
spool keeps working while it exists.

Nothing here reads a wall clock: the clock is injected and every instant is an
int computed in this file.
"""

import ast
import base64
import inspect
import json
import os
import pathlib
import uuid

import pytest

from dskit.production import control as control_module
from dskit.production import vocab
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.control import CommandProcessor, ControlInbox
from dskit.production.ledger import ServeRoot
from dskit.production.state import SeriesState

# ---------------------------------------------------------------------------
# Fixed material — restated here, never read back from the subject
# ---------------------------------------------------------------------------

SERIES_ID = "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1"
RELEASE_HASH = "b" * 64
GENESIS_HASH = "0" * 64
PROCESS_ID = "proc-1"
BASE_MS = 1_767_268_800_000

#: The ten authenticated purposes §5.6 and §5.11.2 close — restated, not
#: imported, so this file can disagree with `vocab` and say so.
AUTHENTICATED_PURPOSES = (
    "arm_request",
    "arm_approval",
    "reduce",
    "flatten_request",
    "flatten_approval",
    "execute_flatten",
    "resume",
    "adopt",
    # §5.11.2: a page suppressed by an unauthenticated caller is an outage
    # with no evidence, so both alert verbs carry a proof.
    "ack",
    "silence",
)

#: The §7 mutating verbs that carry no maker-checker proof and so are not
#: `APPROVAL_PURPOSES` members, yet still queue through the same spool. Four
#: in phase 1, plus §5.13.2's `outcomes`, which records observed facts
#: rather than an operator's claim and so carries no proof either.
UNAUTHENTICATED_PURPOSES = ("halt", "disarm", "reconcile", "ready", "outcomes")

#: What `ControlInbox.queue` accepts, and nothing else.
CONTROL_PURPOSES = AUTHENTICATED_PURPOSES + UNAUTHENTICATED_PURPOSES

#: §5.8: `execute-flatten` requires an active ready loop, so a synchronous
#: (lock-taking, loop-less) CLI is never given a handler for it.
EXECUTING_PURPOSES = ("execute_flatten",)

#: The six keys a caller supplies; `queued_at_ms` is the inbox's to stamp.
CALLER_COMMAND_KEYS = (
    "request_id",
    "purpose",
    "payload",
    "payload_digest",
    "release_hash",
    "proof",
)
QUEUED_COMMAND_KEYS = CALLER_COMMAND_KEYS + ("queued_at_ms",)

#: §6's `control_request` body: the request, what it asks, what it is bound to,
#: and the derived digests. `principal_digest` is null on receipt — verifying a
#: principal is the handler's work, and the record is appended before dispatch.
CONTROL_REQUEST_BODY_KEYS = {
    "request_id",
    "purpose",
    "payload",
    "release_hash",
    "principal_digest",
    "proof_digest",
    "expires_ms",
}

#: §6's `command_result` body.
COMMAND_RESULT_BODY_KEYS = {"request_id", "status", "emitted_record_ids", "reason"}

#: What a caller hands the ledger; the other nine are assigned (R1).
CALLER_LEDGER_KEYS = ("kind", "id", "body")

PROOF = b"\x00signed-by-the-maker\xff"
OTHER_PROOF = b"\x00signed-by-the-checker\xff"


def request_id(n=1):
    """A stable UUID per index, so ordering assertions are readable."""
    return str(uuid.UUID(int=n))


# ---------------------------------------------------------------------------
# Fakes — the collaborators the inbox and the processor are handed
# ---------------------------------------------------------------------------


class FakeClock:
    """The one `Clock` method the spool needs; no wall time."""

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
    """The `Ledger` surface the processor uses, folding into a real `SeriesState`.

    `append` assigns the nine §6 envelope fields and calls `SeriesState.apply`
    — what the real ledger does — so assertions are about the fold rather than
    about a stub. `calls` records the order of `append` and `barrier` so
    "barriers before the file moves" is checkable from the outside.
    """

    def __init__(self, state, clock, barrier_raises=False):
        self.state = state
        self.clock = clock
        self.barrier_raises = barrier_raises
        self.records = []
        self.calls = []
        self.seq = 0
        self.head_hash = GENESIS_HASH

    def append(self, record):
        assert isinstance(record, dict), record
        assert set(record) == set(CALLER_LEDGER_KEYS), sorted(record)
        assert isinstance(record["body"], dict), record["body"]
        self.seq += 1
        prev = self.head_hash
        digest = canonical_hash(record)
        envelope = {
            **record,
            "body": json.loads(json.dumps(record["body"])),
            "payload_digest": digest,
            "seq": self.seq,
            "series_id": SERIES_ID,
            "process_id": PROCESS_ID,
            "release_hash": RELEASE_HASH,
            "recorded_at_ms": self.clock.now_ms(),
            "schema_version": 1,
            "prev_hash": prev,
            "hash": canonical_hash([prev, digest]),
        }
        self.head_hash = envelope["hash"]
        self.records.append(envelope)
        self.calls.append(("append", record["kind"], record["id"]))
        if self.state is not None:
            self.state.apply(envelope)
        return envelope["seq"]

    def append_many(self, records):
        return tuple(self.append(record) for record in records)

    def barrier(self):
        self.calls.append(("barrier", None, None))
        if self.barrier_raises:
            raise OSError("the platter is gone")

    def head(self):
        return (self.seq, self.head_hash)

    def kinds(self):
        return [envelope["kind"] for envelope in self.records]

    def ids(self):
        return [envelope["id"] for envelope in self.records]

    def bodies(self, kind):
        return [e["body"] for e in self.records if e["kind"] == kind]

    def one(self, kind):
        bodies = self.bodies(kind)
        assert len(bodies) == 1, bodies
        return bodies[0]


class RecordingHandler:
    """A verb handler: records `(command, view)` and answers what it was told."""

    def __init__(self, records=(), status="applied", reason="", raises=None):
        self.records = tuple(records)
        self.status = status
        self.reason = reason
        self.raises = raises
        self.calls = []

    def __call__(self, command, view):
        self.calls.append((command, view))
        if self.raises is not None:
            raise self.raises
        return (self.records, self.status, self.reason)


def handler_record(record_id="recon:recon-1"):
    """One record a handler asks the processor to append on its behalf.

    A `recon` row: §6's simplest body, so these tests are about the processor
    and never about whether a verb's own record folds.
    """
    return {
        "kind": "recon",
        "id": record_id,
        "body": {
            "scope": {"venue": "paper", "account": "strategy-a"},
            "ours_digest": "1" * 64,
            "theirs_digest": "1" * 64,
            "breaks": [],
            "status": "clean",
            "action": "none",
        },
    }


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def payload(**over):
    """A canonical command payload — whatever the verb needs, JSON-shaped."""
    values = {"until_ms": BASE_MS + 3_600_000, "allow": ["INS1"]}
    values.update(over)
    return values


def command(**over):
    """The six caller keys of one control command."""
    body = over.pop("payload", None)
    body = payload() if body is None else body
    values = {
        "request_id": request_id(1),
        "purpose": "arm_request",
        "payload": body,
        "payload_digest": canonical_hash(body),
        "release_hash": RELEASE_HASH,
        "proof": PROOF,
    }
    values.update(over)
    return values


def serve_root(tmp_path, name="serve"):
    return ServeRoot(str(tmp_path / name), SERIES_ID)


def make_inbox(tmp_path, clock=None, name="serve"):
    """A `ControlInbox` over a fresh serve root, with an injected clock."""
    return ControlInbox(serve_root(tmp_path, name), clock or FakeClock())


def inbox_file(inbox, rid):
    return pathlib.Path(inbox.serve_root.commands_inbox) / f"{rid}.json"


def read_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def listing(directory):
    return sorted(os.listdir(directory))


def make_processor(tmp_path, handlers=None, clock=None, barrier_raises=False):
    """An inbox, its fold, its ledger and a processor over all three."""
    clock = clock or FakeClock()
    inbox = make_inbox(tmp_path, clock)
    state = SeriesState(SERIES_ID)
    ledger = FoldingLedger(state, clock, barrier_raises=barrier_raises)
    processor = CommandProcessor(inbox, ledger, state, dict(handlers or {}), clock)
    return processor, inbox, ledger, state, clock


def refusal(exc):
    """Every problem string of a raised `ProductionError`, joined."""
    return "; ".join(exc.value.problems)


# ===========================================================================
# ControlInbox — construction and the public surface
# ===========================================================================


def test_the_inbox_takes_a_serve_root_and_a_clock_positionally(tmp_path):
    serve = serve_root(tmp_path)
    inbox = ControlInbox(serve, FakeClock())
    assert inbox.serve_root is serve


def test_the_inboxs_public_surface_is_exactly_the_five_spool_verbs():
    """A sixth public name would be a second way to move a command; the spool
    has one queue verb, one read verb, two terminal verbs and one receipt
    reader, and nothing that could append to a ledger."""
    public = sorted(
        name
        for name in vars(ControlInbox)
        if not name.startswith("_") and name != "serve_root"
    )
    assert public == ["mark_applied", "mark_rejected", "pending", "queue", "receipt"]


def test_the_control_purpose_set_has_exactly_one_home():
    """A closed set in two modules is a closed set that can disagree
    (CLAUDE.md). `CONTROL_PURPOSES` belongs in `vocab.py` beside
    `APPROVAL_PURPOSES`; wherever it lands, it lands once."""
    homes = [
        name
        for name, module in (("vocab", vocab), ("control", control_module))
        if hasattr(module, "CONTROL_PURPOSES")
    ]
    assert len(homes) == 1, f"CONTROL_PURPOSES defined in {homes} — pick one home"
    defined = getattr(vocab if homes == ["vocab"] else control_module, "CONTROL_PURPOSES")
    assert tuple(defined) == CONTROL_PURPOSES


def test_every_authenticated_purpose_of_the_control_set_is_an_approval_purpose():
    """The ten purposes a verifier may be asked about are a subset of what
    the spool carries; the five unauthenticated verbs are the difference."""
    assert set(AUTHENTICATED_PURPOSES) == set(vocab.APPROVAL_PURPOSES)
    assert not set(UNAUTHENTICATED_PURPOSES) & set(vocab.APPROVAL_PURPOSES)


def test_the_executing_purposes_are_named_and_are_control_purposes():
    assert tuple(control_module.EXECUTING_PURPOSES) == EXECUTING_PURPOSES
    assert set(EXECUTING_PURPOSES) <= set(CONTROL_PURPOSES)


# ===========================================================================
# queue — the durable write
# ===========================================================================


def test_queue_returns_the_inbox_path_named_by_the_request_id(tmp_path):
    inbox = make_inbox(tmp_path)
    path = inbox.queue(command())
    assert pathlib.Path(path) == inbox_file(inbox, request_id(1))
    assert os.path.exists(path)
    assert listing(inbox.serve_root.commands_inbox) == [f"{request_id(1)}.json"]


def test_the_queued_file_carries_the_seven_command_keys(tmp_path):
    inbox = make_inbox(tmp_path)
    stored = read_json(inbox.queue(command()))
    assert set(stored) == set(QUEUED_COMMAND_KEYS)


def test_the_queued_file_holds_the_proof_base64_encoded(tmp_path):
    """The proof is bytes in memory and JSON has no bytes; the writer must be
    able to re-verify the exact bytes the maker signed."""
    inbox = make_inbox(tmp_path)
    stored = read_json(inbox.queue(command()))
    assert stored["proof"] == base64.b64encode(PROOF).decode("ascii")
    assert base64.b64decode(stored["proof"]) == PROOF


def test_pending_hands_the_proof_back_as_the_bytes_that_were_queued(tmp_path):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    (queued,) = inbox.pending()
    assert queued["proof"] == PROOF
    assert isinstance(queued["proof"], bytes)


def test_the_inbox_stamps_queued_at_from_its_clock(tmp_path):
    clock = FakeClock(BASE_MS)
    inbox = make_inbox(tmp_path, clock)
    inbox.queue(command())
    clock.advance(60_000)
    (queued,) = inbox.pending()
    assert queued["queued_at_ms"] == BASE_MS


def test_a_caller_supplied_queued_at_refuses(tmp_path):
    """`queued_at_ms` is assigned, like the ledger's nine envelope fields: a
    caller that could backdate a command could reorder the spool."""
    inbox = make_inbox(tmp_path)
    with pytest.raises(ProductionError) as exc:
        inbox.queue({**command(), "queued_at_ms": BASE_MS - 1})
    assert "queued_at_ms" in refusal(exc)


def test_an_unknown_command_key_refuses(tmp_path):
    inbox = make_inbox(tmp_path)
    with pytest.raises(ProductionError) as exc:
        inbox.queue({**command(), "actor": "operator"})
    assert "actor" in refusal(exc)


@pytest.mark.parametrize("missing", CALLER_COMMAND_KEYS)
def test_every_command_key_is_required(tmp_path, missing):
    inbox = make_inbox(tmp_path)
    incomplete = {k: v for k, v in command().items() if k != missing}
    with pytest.raises(ProductionError) as exc:
        inbox.queue(incomplete)
    assert missing in refusal(exc)


def test_queue_accumulates_every_problem_into_one_raise(tmp_path):
    inbox = make_inbox(tmp_path)
    with pytest.raises(ProductionError) as exc:
        inbox.queue({"request_id": "not-a-uuid", "purpose": "nope", "extra": 1})
    assert len(exc.value.problems) >= 3


def test_queue_refuses_a_command_that_is_not_a_dict(tmp_path):
    inbox = make_inbox(tmp_path)
    with pytest.raises(ProductionError):
        inbox.queue([("purpose", "halt")])


@pytest.mark.parametrize("purpose", CONTROL_PURPOSES)
def test_every_control_purpose_is_accepted(tmp_path, purpose):
    inbox = make_inbox(tmp_path)
    path = inbox.queue(command(purpose=purpose))
    assert read_json(path)["purpose"] == purpose


@pytest.mark.parametrize("purpose", ["", "submit", "arm", "ARM_REQUEST", "flatten", None])
def test_a_purpose_outside_the_closed_set_refuses(tmp_path, purpose):
    inbox = make_inbox(tmp_path)
    with pytest.raises(ProductionError) as exc:
        inbox.queue(command(purpose=purpose))
    assert "purpose" in refusal(exc)


@pytest.mark.parametrize("rid", ["", "req-1", "018f0f4e", 7, None])
def test_a_request_id_that_is_not_a_uuid_refuses(tmp_path, rid):
    """§5.8 fixes the id as a caller UUID; a free-form id would let two callers
    collide and make the retry rule meaningless."""
    inbox = make_inbox(tmp_path)
    with pytest.raises(ProductionError) as exc:
        inbox.queue(command(request_id=rid))
    assert "request_id" in refusal(exc)


def test_a_payload_digest_that_does_not_match_the_payload_refuses(tmp_path):
    """The digest is what "the same payload" means; if it can disagree with the
    payload, a repeat carrying a stale digest replays as a retry."""
    inbox = make_inbox(tmp_path)
    with pytest.raises(ProductionError) as exc:
        inbox.queue(command(payload_digest="c" * 64))
    assert "payload_digest" in refusal(exc)


def test_the_payload_digest_is_the_canonical_hash_of_the_payload(tmp_path):
    inbox = make_inbox(tmp_path)
    body = payload(until_ms=BASE_MS + 1)
    stored = read_json(inbox.queue(command(payload=body, payload_digest=canonical_hash(body))))
    assert stored["payload_digest"] == canonical_hash(body)


@pytest.mark.parametrize("release", ["", "b" * 63, "B" * 64, 64, None])
def test_a_release_hash_that_is_not_a_64_hex_digest_refuses(tmp_path, release):
    inbox = make_inbox(tmp_path)
    with pytest.raises(ProductionError) as exc:
        inbox.queue(command(release_hash=release))
    assert "release_hash" in refusal(exc)


def test_a_proof_that_is_not_bytes_refuses(tmp_path):
    inbox = make_inbox(tmp_path)
    with pytest.raises(ProductionError) as exc:
        inbox.queue(command(proof="signed"))
    assert "proof" in refusal(exc)


@pytest.mark.parametrize("purpose", UNAUTHENTICATED_PURPOSES)
def test_an_unauthenticated_verb_may_queue_an_empty_proof(tmp_path, purpose):
    inbox = make_inbox(tmp_path)
    path = inbox.queue(command(purpose=purpose, proof=b""))
    assert read_json(path)["proof"] == ""


def test_a_payload_that_is_not_json_shaped_refuses(tmp_path):
    inbox = make_inbox(tmp_path)
    with pytest.raises(ProductionError):
        inbox.queue(command(payload={"when": object()}, payload_digest="d" * 64))


def test_queue_fsyncs_the_inbox_directory(tmp_path, monkeypatch):
    """"Success once durably queued" is the CLI's whole promise: the file and
    its directory entry must both survive power loss."""
    inbox = make_inbox(tmp_path)
    seen = []
    monkeypatch.setattr(control_module, "fsync_dir", lambda d: seen.append(d))
    inbox.queue(command())
    assert inbox.serve_root.commands_inbox in seen


# ===========================================================================
# queue — retry versus repeat (the §8 line)
# ===========================================================================


def test_the_same_id_and_digest_returns_the_queued_path_and_writes_nothing_new(tmp_path):
    """A CLI that crashed after writing and retries must not be told it failed,
    and must not write a second command."""
    inbox = make_inbox(tmp_path)
    first = inbox.queue(command())
    before = pathlib.Path(first).read_bytes()
    second = inbox.queue(command())
    assert second == first
    assert pathlib.Path(first).read_bytes() == before
    assert listing(inbox.serve_root.commands_inbox) == [f"{request_id(1)}.json"]


def test_a_retry_does_not_restamp_the_queue_instant(tmp_path):
    clock = FakeClock(BASE_MS)
    inbox = make_inbox(tmp_path, clock)
    inbox.queue(command())
    clock.advance(5_000)
    inbox.queue(command())
    (queued,) = inbox.pending()
    assert queued["queued_at_ms"] == BASE_MS


def test_the_same_id_with_a_different_payload_refuses(tmp_path):
    """A legitimate repeated command uses a NEW id; reusing one with different
    content is how a replayed money-moving command would slip through."""
    inbox = make_inbox(tmp_path)
    original = inbox.queue(command())
    before = pathlib.Path(original).read_bytes()
    other = payload(until_ms=BASE_MS + 7_200_000)
    with pytest.raises(ProductionError) as exc:
        inbox.queue(command(payload=other, payload_digest=canonical_hash(other)))
    assert request_id(1) in refusal(exc)
    assert pathlib.Path(original).read_bytes() == before


def test_the_same_id_with_a_different_purpose_refuses(tmp_path):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    with pytest.raises(ProductionError):
        inbox.queue(command(purpose="halt"))


def test_the_same_id_with_a_different_proof_refuses(tmp_path):
    """The proof is part of what was queued; swapping it under a queued id is a
    different command, not a retry."""
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    with pytest.raises(ProductionError):
        inbox.queue(command(proof=OTHER_PROOF))


def test_a_repeat_under_a_new_id_queues_a_second_command(tmp_path):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    inbox.queue(command(request_id=request_id(2)))
    assert len(inbox.pending()) == 2


# ===========================================================================
# pending — what the loop consumes, in what order
# ===========================================================================


def test_pending_is_empty_on_a_fresh_serve_root(tmp_path):
    assert make_inbox(tmp_path).pending() == ()


def test_pending_returns_the_queued_commands_in_queue_order(tmp_path):
    clock = FakeClock(BASE_MS)
    inbox = make_inbox(tmp_path, clock)
    inbox.queue(command(request_id=request_id(3)))
    clock.advance(1_000)
    inbox.queue(command(request_id=request_id(1)))
    clock.advance(1_000)
    inbox.queue(command(request_id=request_id(2)))
    assert [c["request_id"] for c in inbox.pending()] == [
        request_id(3),
        request_id(1),
        request_id(2),
    ]


def test_pending_breaks_a_tie_on_the_request_id(tmp_path):
    """Two commands queued in the same millisecond must still have one order,
    or two processes replaying the same spool disagree about what ran first."""
    inbox = make_inbox(tmp_path, FakeClock(BASE_MS))
    for n in (3, 1, 2):
        inbox.queue(command(request_id=request_id(n)))
    assert [c["request_id"] for c in inbox.pending()] == [
        request_id(1),
        request_id(2),
        request_id(3),
    ]


def test_pending_carries_the_seven_keys_of_each_queued_command(tmp_path):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    (queued,) = inbox.pending()
    assert set(queued) == set(QUEUED_COMMAND_KEYS)
    assert queued["payload"] == payload()
    assert queued["payload_digest"] == canonical_hash(payload())


def test_a_second_inbox_over_the_same_root_sees_the_queued_command(tmp_path):
    """The spool is the filesystem, not process memory: the CLI writes it and a
    different process reads it."""
    writer = make_inbox(tmp_path)
    writer.queue(command())
    reader = ControlInbox(serve_root(tmp_path), FakeClock())
    assert [c["request_id"] for c in reader.pending()] == [request_id(1)]


def test_a_malformed_inbox_file_refuses_and_names_it(tmp_path):
    """A file the loop cannot parse is not silently skipped: a dropped control
    command is indistinguishable from one that was never sent."""
    inbox = make_inbox(tmp_path)
    path = inbox_file(inbox, request_id(1))
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProductionError) as exc:
        inbox.pending()
    assert request_id(1) in refusal(exc)


def test_an_inbox_file_whose_id_disagrees_with_its_name_refuses(tmp_path):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    path = inbox_file(inbox, request_id(1))
    stored = read_json(path)
    stored["request_id"] = request_id(2)
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ProductionError):
        inbox.pending()


def test_an_inbox_file_whose_digest_disagrees_with_its_payload_refuses(tmp_path):
    inbox = make_inbox(tmp_path)
    path = inbox_file(inbox, request_id(1))
    inbox.queue(command())
    stored = read_json(path)
    stored["payload"] = {"until_ms": 1}
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ProductionError):
        inbox.pending()


# ===========================================================================
# mark_applied / mark_rejected — the atomic move and its receipt
# ===========================================================================


def receipt_body(status="applied", reason="", ids=("recon:recon-1",)):
    return {"status": status, "reason": reason, "emitted_record_ids": list(ids)}


def test_mark_applied_moves_the_file_to_the_applied_queue(tmp_path):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    inbox.mark_applied(request_id(1), receipt_body())
    serve = inbox.serve_root
    assert listing(serve.commands_inbox) == []
    assert listing(serve.commands_applied) == [f"{request_id(1)}.json"]
    assert listing(serve.commands_rejected) == []


def test_mark_rejected_moves_the_file_to_the_rejected_queue(tmp_path):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    inbox.mark_rejected(request_id(1), receipt_body(status="rejected", reason="no handler"))
    serve = inbox.serve_root
    assert listing(serve.commands_inbox) == []
    assert listing(serve.commands_rejected) == [f"{request_id(1)}.json"]
    assert listing(serve.commands_applied) == []


def test_the_receipt_reports_the_status_and_the_reason(tmp_path):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    inbox.mark_rejected(request_id(1), receipt_body(status="rejected", reason="unarmed"))
    got = inbox.receipt(request_id(1))
    assert got["status"] == "rejected"
    assert got["status"] in vocab.COMMAND_STATUSES
    assert got["reason"] == "unarmed"


def test_the_terminal_file_still_carries_the_command_that_was_applied(tmp_path):
    """`verify` compares a receipt against the ledger; a receipt that dropped
    the payload digest could not say WHICH command it is the receipt for."""
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    inbox.mark_applied(request_id(1), receipt_body())
    stored = read_json(pathlib.Path(inbox.serve_root.commands_applied) / f"{request_id(1)}.json")
    assert set(QUEUED_COMMAND_KEYS) <= set(stored)
    assert stored["payload_digest"] == canonical_hash(payload())


def test_receipt_is_none_while_a_command_is_still_pending(tmp_path):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    assert inbox.receipt(request_id(1)) is None


def test_receipt_is_none_for_a_request_that_was_never_queued(tmp_path):
    assert make_inbox(tmp_path).receipt(request_id(9)) is None


def test_marking_a_request_that_was_never_queued_refuses(tmp_path):
    inbox = make_inbox(tmp_path)
    with pytest.raises(ProductionError) as exc:
        inbox.mark_applied(request_id(9), receipt_body())
    assert request_id(9) in refusal(exc)


def test_marking_a_terminal_request_again_refuses(tmp_path):
    """A terminal receipt is terminal; a second one would let a replayed
    command re-emerge with a different answer."""
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    inbox.mark_applied(request_id(1), receipt_body())
    with pytest.raises(ProductionError):
        inbox.mark_rejected(request_id(1), receipt_body(status="rejected"))


def test_mark_applied_refuses_a_receipt_that_does_not_say_applied(tmp_path):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    with pytest.raises(ProductionError) as exc:
        inbox.mark_applied(request_id(1), receipt_body(status="rejected"))
    assert "status" in refusal(exc)


def test_mark_rejected_refuses_a_receipt_that_does_not_say_rejected(tmp_path):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    with pytest.raises(ProductionError):
        inbox.mark_rejected(request_id(1), receipt_body(status="applied"))


@pytest.mark.parametrize("status", ["", "queued", "ok", None])
def test_a_receipt_status_outside_the_closed_set_refuses(tmp_path, status):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    with pytest.raises(ProductionError):
        inbox.mark_applied(request_id(1), receipt_body(status=status))


def test_a_terminal_request_is_no_longer_pending(tmp_path):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    inbox.mark_applied(request_id(1), receipt_body())
    assert inbox.pending() == ()


def test_a_request_with_a_receipt_is_not_pending_even_if_its_inbox_file_survived(tmp_path):
    """The receipt is written BEFORE the inbox file is unlinked, so a crash in
    between leaves both. Re-consuming that command would apply it twice."""
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    inbox.mark_applied(request_id(1), receipt_body())
    inbox_file(inbox, request_id(1)).write_text(
        json.dumps(read_json(pathlib.Path(inbox.serve_root.commands_applied)
                             / f"{request_id(1)}.json")),
        encoding="utf-8",
    )
    assert inbox.pending() == ()
    assert inbox.receipt(request_id(1))["status"] == "applied"


def test_a_request_in_both_terminal_queues_refuses(tmp_path):
    """Two contradictory terminal receipts are not a state the spool may
    silently resolve by preferring one directory."""
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    inbox.mark_applied(request_id(1), receipt_body())
    forged = read_json(pathlib.Path(inbox.serve_root.commands_applied) / f"{request_id(1)}.json")
    (pathlib.Path(inbox.serve_root.commands_rejected) / f"{request_id(1)}.json").write_text(
        json.dumps({**forged, "status": "rejected"}), encoding="utf-8"
    )
    with pytest.raises(ProductionError):
        inbox.receipt(request_id(1))


def test_requeueing_a_terminal_request_with_the_same_digest_is_idempotent(tmp_path):
    """A CLI that crashed before reading its answer retries; the command was
    already applied, so the retry must not queue it a second time."""
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    inbox.mark_applied(request_id(1), receipt_body())
    path = inbox.queue(command())
    assert pathlib.Path(path).parent == pathlib.Path(inbox.serve_root.commands_applied)
    assert listing(inbox.serve_root.commands_inbox) == []


def test_requeueing_a_terminal_request_with_a_different_payload_refuses(tmp_path):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    inbox.mark_applied(request_id(1), receipt_body())
    other = payload(until_ms=BASE_MS + 99)
    with pytest.raises(ProductionError):
        inbox.queue(command(payload=other, payload_digest=canonical_hash(other)))


def test_the_terminal_move_fsyncs_its_directory(tmp_path, monkeypatch):
    inbox = make_inbox(tmp_path)
    inbox.queue(command())
    seen = []
    monkeypatch.setattr(control_module, "fsync_dir", lambda d: seen.append(d))
    inbox.mark_applied(request_id(1), receipt_body())
    assert inbox.serve_root.commands_applied in seen


# ===========================================================================
# HALT is not the spool's business (§5.8)
# ===========================================================================


def test_queueing_a_halt_command_does_not_create_the_sentinel(tmp_path):
    """`halt` creates `HALT` before queueing its audit command — that ordering
    is the CLI's. The spool must not be the thing that stops the loop, or
    stopping would depend on inbox health."""
    inbox = make_inbox(tmp_path)
    inbox.queue(command(purpose="halt", proof=b""))
    assert not os.path.exists(inbox.serve_root.halt_sentinel)


def test_the_spool_keeps_working_while_the_halt_sentinel_exists(tmp_path):
    """A halted series must still accept `resume`, `disarm` and `reconcile`."""
    inbox = make_inbox(tmp_path)
    pathlib.Path(inbox.serve_root.halt_sentinel).write_text("halted", encoding="utf-8")
    inbox.queue(command(purpose="resume"))
    assert [c["purpose"] for c in inbox.pending()] == ["resume"]


# ===========================================================================
# The module's mechanics — one owner for the durable-write helpers
# ===========================================================================


MODULE_PATH = pathlib.Path(control_module.__file__)
MODULE_TREE = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))


def test_control_imports_the_durability_helpers_it_needs_and_defines_none(tmp_path):
    """`fsync_dir` has one owner in `dskit.onboarding.base` (CLAUDE.md: a
    function is never repeated across modules)."""
    imported = {
        alias.name
        for node in ast.walk(MODULE_TREE)
        if isinstance(node, ast.ImportFrom) and node.module == "dskit.onboarding.base"
        for alias in node.names
    }
    assert "fsync_dir" in imported
    defined = {n.name for n in ast.walk(MODULE_TREE) if isinstance(n, ast.FunctionDef)}
    assert not [name for name in defined if "fsync" in name]


def test_the_terminal_move_is_an_atomic_rename_not_a_copy():
    """A copy-then-delete can lose the command; `os.replace` cannot."""
    calls = [
        node.func.attr
        for node in ast.walk(MODULE_TREE)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert "replace" in calls
    assert not {"copy", "copy2", "copyfile", "move"} & set(calls)


def test_the_inbox_file_is_created_exclusively():
    """Exclusive create is what makes "the same id twice" observable at all."""
    assert "O_EXCL" in MODULE_PATH.read_text(encoding="utf-8")


def test_no_purpose_literal_is_compared_anywhere_in_the_module():
    """`CommandProcessor` owns no verb logic (§8): a purpose reaches a handler
    or it does not, and an `if purpose ==` chain is the second decision table."""
    offenders = []
    for node in ast.walk(MODULE_TREE):
        if not isinstance(node, ast.Compare):
            continue
        for operand in [node.left, *node.comparators]:
            if isinstance(operand, ast.Constant) and operand.value in CONTROL_PURPOSES:
                offenders.append(f"line {node.lineno}: {operand.value}")
    assert offenders == []


# ===========================================================================
# CommandProcessor — dispatch, order and the records it writes
# ===========================================================================


def test_the_processor_takes_inbox_ledger_state_handlers_and_clock_positionally(tmp_path):
    processor, inbox, ledger, state, clock = make_processor(tmp_path)
    assert isinstance(processor, CommandProcessor)


def test_the_processors_public_surface_is_exactly_process_pending():
    public = sorted(name for name in vars(CommandProcessor) if not name.startswith("_"))
    assert public == ["process_pending"]


def test_process_pending_on_an_empty_inbox_writes_nothing(tmp_path):
    processor, inbox, ledger, state, _ = make_processor(tmp_path)
    assert processor.process_pending(state.snapshot()) == ()
    assert ledger.calls == []


def test_an_applied_command_writes_request_then_handler_records_then_result(tmp_path):
    """D13: the record order IS the audit trail. The request is on the chain
    before the verb runs, and the result closes it."""
    handler = RecordingHandler(records=(handler_record(),))
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {"arm_request": handler})
    inbox.queue(command())
    processor.process_pending(state.snapshot())
    assert ledger.kinds() == ["control_request", "recon", "command_result"]


def test_the_record_ids_are_kind_qualified_and_unique_in_the_series(tmp_path):
    """R9: a record `id` is unique across the series, so producers qualify it
    with its kind — the request and its result share a request id, not an id."""
    handler = RecordingHandler(records=(handler_record(),))
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {"arm_request": handler})
    inbox.queue(command())
    processor.process_pending(state.snapshot())
    ids = ledger.ids()
    assert ids[0] == f"control_request:{request_id(1)}"
    assert ids[-1] == f"command_result:{request_id(1)}"
    assert len(set(ids)) == len(ids)


def test_the_control_request_body_carries_section_sixs_fields(tmp_path):
    handler = RecordingHandler()
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {"arm_request": handler})
    inbox.queue(command())
    processor.process_pending(state.snapshot())
    body = ledger.one("control_request")
    assert set(body) == CONTROL_REQUEST_BODY_KEYS
    assert body["request_id"] == request_id(1)
    assert body["purpose"] == "arm_request"
    assert body["payload"] == payload()
    assert body["release_hash"] == RELEASE_HASH


def test_the_control_request_records_no_principal_before_the_handler_verified_one(tmp_path):
    """The record is appended on receipt, before dispatch; a principal digest
    there would be a claim nobody checked."""
    processor, inbox, ledger, state, _ = make_processor(
        tmp_path, {"arm_request": RecordingHandler()}
    )
    inbox.queue(command())
    processor.process_pending(state.snapshot())
    body = ledger.one("control_request")
    assert body["principal_digest"] is None
    assert isinstance(body["proof_digest"], str) and len(body["proof_digest"]) == 64


def test_the_proof_bytes_never_reach_a_ledger_record(tmp_path):
    """redact.py's rule: no secret ever reaches a ledger record. The proof
    travels in the inbox file so the writer can verify it, and stops there."""
    processor, inbox, ledger, state, _ = make_processor(
        tmp_path, {"arm_request": RecordingHandler()}
    )
    inbox.queue(command())
    processor.process_pending(state.snapshot())
    written = json.dumps(ledger.records)
    assert base64.b64encode(PROOF).decode("ascii") not in written
    assert PROOF.decode("latin-1") not in written


def test_the_command_result_body_names_the_records_the_command_emitted(tmp_path):
    handler = RecordingHandler(records=(handler_record("recon:recon-1"),
                                        handler_record("recon:recon-2")))
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {"arm_request": handler})
    inbox.queue(command())
    processor.process_pending(state.snapshot())
    body = ledger.one("command_result")
    assert set(body) == COMMAND_RESULT_BODY_KEYS
    assert body["request_id"] == request_id(1)
    assert body["status"] == "applied"
    assert list(body["emitted_record_ids"]) == ["recon:recon-1", "recon:recon-2"]


def test_the_emitted_ids_exclude_the_receipt_records_themselves(tmp_path):
    processor, inbox, ledger, state, _ = make_processor(
        tmp_path, {"arm_request": RecordingHandler()}
    )
    inbox.queue(command())
    processor.process_pending(state.snapshot())
    assert ledger.one("command_result")["emitted_record_ids"] == []


def test_process_pending_returns_the_command_result_bodies_in_order(tmp_path):
    handler = RecordingHandler()
    processor, inbox, ledger, state, clock = make_processor(tmp_path, {"halt": handler})
    for n in (1, 2):
        inbox.queue(command(request_id=request_id(n), purpose="halt", proof=b""))
        clock.advance(1_000)
    results = processor.process_pending(state.snapshot())
    assert [r["request_id"] for r in results] == [request_id(1), request_id(2)]
    assert [r["status"] for r in results] == ["applied", "applied"]


def test_the_handler_receives_the_queued_command_and_the_view_it_was_given(tmp_path):
    handler = RecordingHandler()
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {"arm_request": handler})
    inbox.queue(command())
    view = state.snapshot()
    processor.process_pending(view)
    (got_command, got_view), = handler.calls
    assert got_view is view
    assert got_command["request_id"] == request_id(1)
    assert got_command["proof"] == PROOF
    assert set(got_command) == set(QUEUED_COMMAND_KEYS)


def test_every_purpose_is_dispatched_by_the_same_path(tmp_path):
    """No verb is special to the processor: the handler map is the whole table."""
    handlers = {purpose: RecordingHandler() for purpose in CONTROL_PURPOSES}
    processor, inbox, ledger, state, clock = make_processor(tmp_path, handlers)
    for n, purpose in enumerate(CONTROL_PURPOSES, start=1):
        inbox.queue(command(request_id=request_id(n), purpose=purpose))
        clock.advance(1_000)
    results = processor.process_pending(state.snapshot())
    assert [r["status"] for r in results] == ["applied"] * len(CONTROL_PURPOSES)
    assert ledger.kinds().count("control_request") == len(CONTROL_PURPOSES)


def test_a_purpose_with_no_handler_is_rejected_and_names_itself(tmp_path):
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {})
    inbox.queue(command(purpose="reduce"))
    (result,) = processor.process_pending(state.snapshot())
    assert result["status"] == "rejected"
    assert "reduce" in result["reason"]
    assert ledger.kinds() == ["control_request", "command_result"]
    assert listing(inbox.serve_root.commands_rejected) == [f"{request_id(1)}.json"]


def test_execute_flatten_is_rejected_by_a_processor_without_a_loop(tmp_path):
    """§5.8: `execute-flatten` requires an active ready loop. The synchronous
    CLI path injects no handler for it, so the absence IS the refusal — the
    processor never learns what a flatten is."""
    handlers = {p: RecordingHandler() for p in CONTROL_PURPOSES if p not in EXECUTING_PURPOSES}
    processor, inbox, ledger, state, _ = make_processor(tmp_path, handlers)
    inbox.queue(command(purpose="execute_flatten"))
    (result,) = processor.process_pending(state.snapshot())
    assert result["status"] == "rejected"
    assert "execute_flatten" in result["reason"]


def test_a_handler_that_refuses_rejects_the_command_and_appends_nothing_of_its_own(tmp_path):
    """A refused verb is a rejected command, not a crashed loop."""
    handler = RecordingHandler(raises=ProductionError(["proof does not verify"]))
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {"reduce": handler})
    inbox.queue(command(purpose="reduce"))
    (result,) = processor.process_pending(state.snapshot())
    assert result["status"] == "rejected"
    assert "proof does not verify" in result["reason"]
    assert ledger.kinds() == ["control_request", "command_result"]


def test_a_handler_that_rejects_still_leaves_a_full_audit_trail(tmp_path):
    handler = RecordingHandler(status="rejected", reason="expired")
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {"resume": handler})
    inbox.queue(command(purpose="resume"))
    processor.process_pending(state.snapshot())
    assert ledger.kinds() == ["control_request", "command_result"]
    assert ledger.one("command_result")["reason"] == "expired"
    assert inbox.receipt(request_id(1))["status"] == "rejected"


def test_a_handler_answering_outside_the_status_vocabulary_refuses(tmp_path):
    """A handler is in-process code; a status the vocabulary does not close is
    a contract violation, not a rejected command."""
    handler = RecordingHandler(status="queued")
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {"arm_request": handler})
    inbox.queue(command())
    with pytest.raises(ProductionError):
        processor.process_pending(state.snapshot())


def test_a_handler_answering_a_malformed_record_refuses(tmp_path):
    handler = RecordingHandler(records=({"kind": "recon"},))
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {"arm_request": handler})
    inbox.queue(command())
    with pytest.raises((ProductionError, AssertionError)):
        processor.process_pending(state.snapshot())


# ===========================================================================
# CommandProcessor — record before receipt, and the evidence `verify` needs
# ===========================================================================


def test_the_barrier_crosses_before_the_file_is_moved(tmp_path):
    """D13. A receipt written first would claim a command was applied whose
    records a power loss then swallowed."""
    handler = RecordingHandler(records=(handler_record(),))
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {"arm_request": handler})
    inbox.queue(command())
    seen = {}
    original = inbox.mark_applied

    def watched(rid, receipt):
        seen["calls"] = list(ledger.calls)
        return original(rid, receipt)

    inbox.mark_applied = watched
    processor.process_pending(state.snapshot())
    assert seen["calls"][-1][0] == "barrier"
    assert [call[0] for call in seen["calls"]].count("append") == 3


def test_a_failed_barrier_leaves_the_command_pending_with_no_receipt(tmp_path):
    """The gap §8 says `verify` reports: an inbox file with no receipt. The
    opposite — a receipt with no records — must be impossible."""
    handler = RecordingHandler(records=(handler_record(),))
    processor, inbox, ledger, state, _ = make_processor(
        tmp_path, {"arm_request": handler}, barrier_raises=True
    )
    inbox.queue(command())
    with pytest.raises(OSError):
        processor.process_pending(state.snapshot())
    assert inbox.receipt(request_id(1)) is None
    assert [c["request_id"] for c in inbox.pending()] == [request_id(1)]


def test_every_applied_receipt_names_records_the_ledger_actually_holds(tmp_path):
    handler = RecordingHandler(records=(handler_record(),))
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {"arm_request": handler})
    inbox.queue(command())
    processor.process_pending(state.snapshot())
    receipt = inbox.receipt(request_id(1))
    assert receipt["status"] == "applied"
    assert set(receipt["emitted_record_ids"]) <= set(ledger.ids())


def test_a_second_pass_over_a_consumed_spool_writes_nothing(tmp_path):
    """Replay after a crash is idempotent: a command with a terminal receipt is
    not pending, so re-entering the loop cannot apply it twice."""
    handler = RecordingHandler(records=(handler_record(),))
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {"arm_request": handler})
    inbox.queue(command())
    processor.process_pending(state.snapshot())
    before = list(ledger.calls)
    assert processor.process_pending(state.snapshot()) == ()
    assert ledger.calls == before


# ===========================================================================
# The two halves of pending-control state (§5.13.1)
# ===========================================================================


def test_a_queued_command_is_in_the_spool_before_the_fold_knows_it(tmp_path):
    """`ControlInbox` is the writer and `SeriesState` folds the
    `control_request` when it is recorded — which is why an `Authority` holds
    both: a queued-but-unfolded command cannot be missed."""
    processor, inbox, ledger, state, _ = make_processor(
        tmp_path, {"arm_request": RecordingHandler()}
    )
    inbox.queue(command())
    assert dict(state.snapshot().pending_control) == {}
    assert len(inbox.pending()) == 1


def test_processing_folds_the_request_and_the_result_clears_it(tmp_path):
    processor, inbox, ledger, state, _ = make_processor(
        tmp_path, {"arm_request": RecordingHandler()}
    )
    inbox.queue(command())
    processor.process_pending(state.snapshot())
    assert dict(state.snapshot().pending_control) == {}
    assert inbox.pending() == ()


def test_a_flatten_cycle_is_applied_when_it_is_queued_not_when_it_completes(tmp_path):
    """§5.8: `execute-flatten` moves to `applied` when its cycle is QUEUED —
    otherwise the pending-control gate would block the cycle's own first leg.
    So by the time `process_pending` returns, nothing is pending anywhere."""
    handler = RecordingHandler(records=(handler_record("recon:flatten-1"),))
    processor, inbox, ledger, state, _ = make_processor(
        tmp_path, {"execute_flatten": handler}
    )
    inbox.queue(command(purpose="execute_flatten"))
    (result,) = processor.process_pending(state.snapshot())
    assert result["status"] == "applied"
    assert dict(state.snapshot().pending_control) == {}
    assert inbox.pending() == ()
    assert inbox.receipt(request_id(1))["status"] == "applied"


# ===========================================================================
# The processor is the sole ledger writer (§5.8, D15)
# ===========================================================================


def test_the_inbox_holds_no_ledger(tmp_path):
    """The spool cannot append even by accident: it was never given a ledger."""
    inbox = make_inbox(tmp_path)
    assert not [name for name in vars(inbox) if "ledger" in name.lower()]
    parameters = list(inspect.signature(ControlInbox.__init__).parameters)
    assert parameters == ["self", "serve_root", "clock"]


def test_the_handler_never_appends_its_own_records(tmp_path):
    """A handler returns records; the process holding the lock writes them.
    Two writers is the thing D15 forbids."""
    handler = RecordingHandler(records=(handler_record(),))
    processor, inbox, ledger, state, _ = make_processor(tmp_path, {"arm_request": handler})
    inbox.queue(command())
    processor.process_pending(state.snapshot())
    appended = [call for call in ledger.calls if call[0] == "append"]
    assert len(appended) == 3
    assert appended[1][2] == "recon:recon-1"
