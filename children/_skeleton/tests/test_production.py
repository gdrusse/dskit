"""The child's serve document under the toolkit's own bar, and the proof
that every live-capable template stays fail-closed until it is written.

Two things are worth a test here, and they pull in opposite directions.
The first is that a child inherits a WORKING serving path: the sample
document parses, its identity hash is stable, and the nodes it names
answer the closed `serving_effect` API, so the fail-closed default cannot
silently refuse a child's own graph.

The second is that copying the skeleton must never be enough to move
money. `execution.py`, `accounting.py`, `approvals.py` and
`coordination.py` are templates whose outward-facing hooks refuse; a
child that forgets to implement one gets a loud failure, not a live
order. These tests fail the moment somebody makes a template
"convenient".
"""

import json
import os

import pytest

from dskit.production.document import ServeDocument

import yourproject.nodes as nodes
from yourproject.accounting import LiveBooks
from yourproject.approvals import SignedApprovals
from yourproject.coordination import FencedLease
from yourproject.execution import LiveVenue

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVE_DOC = os.path.join(CHILD_ROOT, "configs", "serve-sample.json")


def load_serve_document():
    """The sample serve document as the toolkit parses it."""
    with open(SERVE_DOC, encoding="utf-8") as handle:
        return ServeDocument.from_obj(json.load(handle))


# ---------------------------------------------------------------------------
# The document a child inherits
# ---------------------------------------------------------------------------


def test_the_sample_serve_document_validates():
    document = load_serve_document()
    assert document.rung == "shadow"
    assert document.serving.adapter == "yourproject"


def test_its_identity_is_stable_and_placement_is_not_part_of_it():
    first = load_serve_document()
    with open(SERVE_DOC, encoding="utf-8") as handle:
        moved = json.load(handle)
    moved["placement"]["ledger_root"] = "/somewhere/else"
    moved["notes"] = "documentation never changes what a process IS"
    assert ServeDocument.from_obj(moved).doc_hash == first.doc_hash


def test_the_head_and_entry_name_nodes_this_child_registers():
    document = load_serve_document()
    declared = set(nodes.NODE_KINDS)
    assert declared, "the child registers no kinds"
    # The entry and heads are node KEYS in the run document, so this pins the
    # weaker but real property: the child has kinds for them to resolve to.
    assert document.serving.entry.node
    assert tuple(document.serving.heads)


# ---------------------------------------------------------------------------
# The serving API the fail-closed default requires (ADR-0091)
# ---------------------------------------------------------------------------


def test_every_registered_kind_answers_the_serving_effect_api():
    for name, cls in nodes.NODE_KINDS.items():
        effect = cls.serving_effect({}, {})
        assert effect in ("pure", "entry_read", "release_read", "forbidden"), name


def test_the_source_declares_itself_the_entry_and_the_transform_pure():
    assert nodes.SampleRecords.serving_effect({}, {}) == "entry_read"
    assert nodes.EnrichRecords.serving_effect({}, {}) == "pure"


def test_the_entry_publishes_a_contract_with_no_universe_in_it():
    contract = nodes.SampleRecords.serving_contract({}, {})
    assert contract.entity_key_fields
    assert contract.event_time_field
    # The required key set is the serve document's and is pinned into the
    # release; a source cannot know it, and must not pretend to.
    assert not hasattr(contract, "universe")
    assert not hasattr(contract, "required_keys")


def test_the_serving_effect_answer_touches_no_io(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("serving_effect must be pure")

    monkeypatch.setattr("builtins.open", boom)
    for cls in nodes.NODE_KINDS.values():
        cls.serving_effect({}, {})


# ---------------------------------------------------------------------------
# Fail-closed: copying the skeleton cannot move money
# ---------------------------------------------------------------------------


def test_the_live_executor_refuses_to_send():
    venue = LiveVenue.__new__(LiveVenue)
    with pytest.raises(NotImplementedError):
        venue._submit_native(object(), object(), 1000)


def test_the_live_executor_declares_a_fencing_token():
    # A live executor whose gateway cannot reject a stale token would let two
    # processes both believe they own submit.
    caps = LiveVenue.capabilities(LiveVenue.__new__(LiveVenue))
    assert caps.fencing == "submit_token"


def test_check_reports_the_template_is_unimplemented():
    problems = LiveVenue.check(LiveVenue.__new__(LiveVenue), {})
    assert problems, "check() must refuse an unimplemented venue"


def test_live_accounting_refuses_every_hook():
    books = LiveBooks.__new__(LiveBooks)
    with pytest.raises(NotImplementedError):
        books.classify(object(), object())
    with pytest.raises(NotImplementedError):
        books.value(object(), object(), 0)
    with pytest.raises(NotImplementedError):
        books.snapshot(object(), object(), object(), 0, (), object())


def test_the_approval_verifier_refuses_every_proof():
    verifier = SignedApprovals.__new__(SignedApprovals)
    with pytest.raises(NotImplementedError):
        verifier.verify(b"canonical", b"proof", "arm_request")


def test_the_lease_is_live_capable_but_unimplemented():
    lease = FencedLease.__new__(FencedLease)
    assert FencedLease.LIVE_CAPABLE is True
    with pytest.raises(NotImplementedError):
        lease.acquire(object(), "holder", 30000)
