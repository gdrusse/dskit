"""`readiness.py` — the release-bound GO the action matrix reads (§5.13, D24).

Readiness is not a health check and not a guard. It is a durable,
expiring, release-bound statement that a named checklist was satisfied,
recorded as a §6 `readiness` record and folded into
`StateView.readiness`; `ActPermit` binds its digest and expiry, and the
action matrix refuses a live submit without it. Four rulings shape these
tests:

* **D24 — the checklist's CONTENTS are pinned, not its path.** `plan`
  canonicalises the file named by `document.readiness.checklist` into
  `release.checklist_digest`, and `ready` refuses a checklist whose
  digest differs. Without that, `doc_hash` covers the path and not the
  contents, and a GO could be re-earned against a quietly shortened
  checklist under a fixed release and a live arm.
* **§5.13 — some items are unwaivable.** Release/runtime verification,
  executor conformance, authenticated execution-scope equality, a clean
  startup reconciliation, a fenced lease and the required safety controls
  are the foundations the rest of the ladder stands on; a waiver on one
  refuses rather than passing.
* **§5.13 — the digest recipe is exact.** `readiness_digest =
  canonical_hash(release_hash, items)` with `items` sorted by `item` and
  each contributing exactly `(item, required, evidence, waiver, passed)`
  in that order — the same "exactly those fields in that order" standard
  `requirement_digest` follows, because a permit binds this value.
* **§5.13 — a GO expires.** `valid_until_ms = evaluated_at_ms +
  document.readiness.valid_for_s * 1000`, and `current` reads
  `StateView.readiness` — never by folding the ledger again — so the loop
  asks the same object every other freshness check asks.

The NO-GO exit code (5) belongs to `__main__`; here a NO-GO is a VERDICT,
recorded like any other, because "the checklist is not yet satisfied" is
a result and not an error.

Nothing reads wall time: the clock is a `TestClock`, the checklist is a
temp file, and the ledger is a fake that chains and folds into a real
`SeriesState` so `current` is answered by the fold.
"""

import dataclasses
import hashlib
import json

import pytest

from dskit.production import readiness as readiness_module
from dskit.production import vocab
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.clock import TestClock
from dskit.production.document import ServeDocument
from dskit.production.readiness import Readiness, ReadinessResult, readiness_digest
from dskit.production.state import ReadinessProjection, SeriesState
from tests.production.test_document import minimal_document, set_path

# ---------------------------------------------------------------------------
# Fixed material
# ---------------------------------------------------------------------------

SERIES_ID = "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1"
RELEASE_HASH = "b" * 64
OTHER_RELEASE_HASH = "c" * 64
GENESIS_HASH = "0" * 64
PROCESS_ID = "proc-1"
BASE_MS = 1_767_268_800_000

#: `document.readiness.valid_for_s` in §4.1's illustration — one day.
VALID_FOR_S = 86_400
VALID_FOR_MS = VALID_FOR_S * 1000

#: What a caller hands the ledger; the other nine are assigned (§6, R1).
CALLER_KEYS = ("kind", "id", "body")

#: The five fields §5.13 says each item contributes to the digest, in
#: exactly that order — and the five `ReadinessProjection` carries.
ITEM_FIELDS = ("item", "required", "evidence", "waiver", "passed")

#: The four keys the checklist FILE declares per item (`passed` is the
#: evaluation's answer, never an input).
CHECKLIST_FIELDS = ("item", "required", "evidence", "waiver")

#: §6's `readiness` body: the result plus the release it is bound to.
READINESS_BODY_KEYS = {
    "release_hash",
    "verdict",
    "items",
    "readiness_digest",
    "evaluated_at_ms",
    "valid_until_ms",
}

#: The five members §5.13 gives `ReadinessResult`, in declared order.
RESULT_FIELDS = ("verdict", "items", "readiness_digest", "evaluated_at_ms", "valid_until_ms")

#: An item beyond the foundations, so "a waivable item" has an example.
EXTRA_ITEM = "runbook_signed"


# ---------------------------------------------------------------------------
# Local fakes
# ---------------------------------------------------------------------------


class FoldingLedger:
    """The `Ledger` surface `record` uses, folding into a real state.

    `calls` records `append`/`barrier`/`scan` in order, so "the evaluation
    crosses a barrier" and "`current` never folds the ledger again" are
    both checkable from the outside.
    """

    def __init__(self, state, clock, release_hash=RELEASE_HASH):
        self.state = state
        self.clock = clock
        self.release_hash = release_hash
        self.records = []
        self.calls = []
        self.seq = 0
        self.head_hash = GENESIS_HASH

    def append(self, record):
        assert isinstance(record, dict), record
        assert set(record) == set(CALLER_KEYS), sorted(record)
        assert record["kind"] in vocab.RECORD_KINDS, record["kind"]
        self.seq += 1
        prev = self.head_hash
        digest = canonical_hash(record)
        env = {
            **record,
            "body": dict(record["body"]),
            "payload_digest": digest,
            "seq": self.seq,
            "series_id": SERIES_ID,
            "process_id": PROCESS_ID,
            "release_hash": self.release_hash,
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
        return tuple(self.append(record) for record in records)

    def barrier(self):
        self.calls.append(("barrier", None))

    def scan(self, kind=None, since_seq=0):
        self.calls.append(("scan", kind))
        return tuple(
            env
            for env in self.records
            if env["seq"] > since_seq and (kind is None or env["kind"] == kind)
        )

    def head(self):
        return (self.seq, self.head_hash)

    def of_kind(self, kind):
        return [env for env in self.records if env["kind"] == kind]


class FakeRelease:
    """The two release facts readiness binds: its hash and its checklist."""

    def __init__(self, checklist_digest, release_hash=RELEASE_HASH):
        self.release_hash = release_hash
        self.checklist_digest = checklist_digest


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def foundation_items(**evidence):
    """Every unwaivable foundation item, each evidenced by default."""
    return [
        {
            "item": name,
            "required": True,
            "evidence": evidence.get(name, f"{name}.json"),
            "waiver": None,
        }
        for name in readiness_module.UNWAIVABLE_ITEMS
    ]


def checklist(extra=(), **evidence):
    """The foundation checklist plus any extra items a test declares."""
    return foundation_items(**evidence) + [dict(item) for item in extra]


def waivable_item(item=EXTRA_ITEM, required=True, evidence=None, waiver=None):
    """One item beyond the foundations — the only kind a waiver may carry."""
    return {"item": item, "required": required, "evidence": evidence, "waiver": waiver}


def write_checklist(tmp_path, items, name="readiness.json"):
    """Write the checklist file and return its path."""
    path = tmp_path / name
    path.write_text(json.dumps(items), encoding="utf-8")
    return str(path)


def a_document(waivers=(), valid_for_s=VALID_FOR_S, checklist_path="configs/readiness.json"):
    """A shadow document whose `readiness` block is §4.1's illustration."""
    obj = minimal_document(series_id=SERIES_ID)
    set_path(obj, ["readiness", "checklist"], checklist_path)
    set_path(obj, ["readiness", "waivers"], list(waivers))
    set_path(obj, ["readiness", "valid_for_s"], valid_for_s)
    return ServeDocument.from_obj(obj)


def make_readiness(tmp_path, items=None, *, document=None, digest=None, clock=None,
                   release_hash=RELEASE_HASH, state=None):
    """A `Readiness` over a written checklist, a folding ledger and a real state."""
    items = checklist() if items is None else items
    path = write_checklist(tmp_path, items)
    clock = clock or TestClock(start_ms=BASE_MS)
    state = state or SeriesState(SERIES_ID)
    ledger = FoldingLedger(state, clock, release_hash=release_hash)
    release = FakeRelease(
        canonical_hash(items) if digest is None else digest, release_hash=release_hash
    )
    ready = Readiness(
        document or a_document(checklist_path=path),
        release,
        ledger=ledger,
        state=state,
        clock=clock,
        checklist_path=path,
    )
    return ready, ledger, state, clock, release


def expected_digest(release_hash, items):
    """§5.13's recipe, restated here and never imported from the subject."""
    ordered = sorted(items, key=lambda item: item["item"])
    return canonical_hash(
        (
            release_hash,
            tuple(tuple(item[field] for field in ITEM_FIELDS) for item in ordered),
        )
    )


# ---------------------------------------------------------------------------
# The module surface
# ---------------------------------------------------------------------------


def test_the_module_exports_what_section_8_places_here():
    for name in ("Readiness", "ReadinessResult", "readiness_digest"):
        assert name in readiness_module.__all__, f"{name} must be part of the public surface"


def test_the_unwaivable_items_are_the_vocabularys_and_not_a_second_copy():
    """The VOCAB GAP the phase-1 suite reported is closed: §8 places every
    closed set in `vocab.py`, and a set that lived in the module reading it
    is exactly the second home this package refuses. `readiness.py` imports
    it and exports nothing of its own."""
    assert readiness_module.UNWAIVABLE_ITEMS is vocab.UNWAIVABLE_ITEMS
    assert "UNWAIVABLE_ITEMS" not in readiness_module.__all__


def test_all_leaks_no_private_name_and_names_nothing_missing():
    assert readiness_module.__all__, "readiness.py must declare __all__"
    assert not [n for n in readiness_module.__all__ if n.startswith("_")]
    missing = [n for n in readiness_module.__all__ if not hasattr(readiness_module, n)]
    assert not missing, f"__all__ names nothing: {missing}"


# ---------------------------------------------------------------------------
# The unwaivable foundations (§5.13)
# ---------------------------------------------------------------------------


def test_the_unwaivable_items_are_a_closed_tuple_of_distinct_names():
    """One owner, in `vocab.py` where §8 places every closed set."""
    items = readiness_module.UNWAIVABLE_ITEMS
    assert isinstance(items, tuple) and items
    assert len(set(items)) == len(items)
    assert all(isinstance(name, str) and name for name in items)


def test_the_unwaivable_items_are_the_six_foundations_section_5_13_names():
    """Release/runtime verification, executor conformance, authenticated
    execution-scope equality, clean startup reconciliation, fenced lease
    capability, required safety controls — six, and no fewer."""
    assert len(readiness_module.UNWAIVABLE_ITEMS) == 6


# ---------------------------------------------------------------------------
# The digest recipe (§5.13, what `ActPermit` binds)
# ---------------------------------------------------------------------------


def digest_items(**overrides):
    """Two evaluated items, in the shape the digest walks."""
    items = [
        {"item": "b_item", "required": True, "evidence": "b.json",
         "waiver": None, "passed": True},
        {"item": "a_item", "required": False, "evidence": None,
         "waiver": "waived by ops", "passed": True},
    ]
    for item in items:
        item.update(overrides.get(item["item"], {}))
    return items


def test_the_digest_is_the_release_hash_over_the_items_sorted_by_item():
    items = digest_items()
    assert readiness_digest(RELEASE_HASH, items) == expected_digest(RELEASE_HASH, items)


def test_the_digest_ignores_the_order_the_items_arrived_in():
    items = digest_items()
    assert readiness_digest(RELEASE_HASH, items) == readiness_digest(
        RELEASE_HASH, list(reversed(items))
    )


def test_the_digest_binds_the_release():
    """A GO earned under one release must not read as current under another."""
    items = digest_items()
    assert readiness_digest(RELEASE_HASH, items) != readiness_digest(
        OTHER_RELEASE_HASH, items
    )


@pytest.mark.parametrize("field", ITEM_FIELDS)
def test_every_one_of_the_five_contributions_moves_the_digest(field):
    """"Each contributes exactly (item, required, evidence, waiver,
    passed)" — a field the digest ignored would be a field a permit does
    not actually bind."""
    changed = {
        "item": "z_item",
        "required": False,
        "evidence": "other.json",
        "waiver": "quietly waived",
        "passed": False,
    }[field]
    moved = digest_items(b_item={field: changed})
    assert readiness_digest(RELEASE_HASH, moved) != readiness_digest(
        RELEASE_HASH, digest_items()
    )


def test_the_digest_reads_nothing_but_those_five_fields():
    items = digest_items()
    noisy = [dict(item, notes="why we ticked it", evaluated_by="ops") for item in items]
    assert readiness_digest(RELEASE_HASH, noisy) == readiness_digest(RELEASE_HASH, items)


def test_the_digest_refuses_an_item_missing_one_of_the_five():
    incomplete = [{"item": "a", "required": True, "evidence": "a.json", "waiver": None}]
    with pytest.raises(ProductionError):
        readiness_digest(RELEASE_HASH, incomplete)


# ---------------------------------------------------------------------------
# `ReadinessResult` — the value the record, the fold and the permit share
# ---------------------------------------------------------------------------


def test_the_result_is_a_frozen_value_with_section_5_13s_five_members(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    result = ready.evaluate(BASE_MS)
    assert dataclasses.is_dataclass(result)
    assert type(result).__dataclass_params__.frozen is True
    assert tuple(f.name for f in dataclasses.fields(ReadinessResult)) == RESULT_FIELDS
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.verdict = "no_go"


def test_the_result_serializes_to_the_five_members_and_round_trips(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    result = ready.evaluate(BASE_MS)
    obj = result.to_obj()
    assert set(obj) == set(RESULT_FIELDS)
    assert ReadinessResult.from_obj(obj) == result


def test_every_evaluated_item_carries_exactly_the_five_fields(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    result = ready.evaluate(BASE_MS)
    assert all(set(item) == set(ITEM_FIELDS) for item in result.items)


def test_the_evaluated_items_come_back_sorted_by_item(tmp_path):
    extra = [waivable_item("zzz_last", evidence="z.json"),
             waivable_item("aaa_first", evidence="a.json")]
    ready, ledger, state, clock, release = make_readiness(tmp_path, checklist(extra=extra))
    names = [item["item"] for item in ready.evaluate(BASE_MS).items]
    assert names == sorted(names)


def test_the_results_digest_is_the_recipe_over_its_own_items(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    result = ready.evaluate(BASE_MS)
    assert result.readiness_digest == expected_digest(RELEASE_HASH, result.items)


# ---------------------------------------------------------------------------
# `evaluate` — checklist to GO / NO-GO
# ---------------------------------------------------------------------------


def test_a_fully_evidenced_checklist_evaluates_to_go(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    result = ready.evaluate(BASE_MS)
    assert result.verdict == "go"
    assert all(item["passed"] for item in result.items)


def test_the_verdict_is_always_a_readiness_verdicts_member(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    assert ready.evaluate(BASE_MS).verdict in vocab.READINESS_VERDICTS


def test_a_required_item_without_evidence_is_a_no_go(tmp_path):
    """A NO-GO is a RESULT, not an exception: nothing is wrong, the
    checklist is simply not yet satisfied (§5.13)."""
    items = checklist(**{readiness_module.UNWAIVABLE_ITEMS[0]: None})
    ready, ledger, state, clock, release = make_readiness(tmp_path, items)
    result = ready.evaluate(BASE_MS)
    assert result.verdict == "no_go"
    failed = [item for item in result.items if not item["passed"]]
    assert [item["item"] for item in failed] == [readiness_module.UNWAIVABLE_ITEMS[0]]


@pytest.mark.parametrize("evidence", [None, "", 0, False, []])
def test_only_truthy_evidence_passes_an_item(tmp_path, evidence):
    items = checklist(extra=[waivable_item(evidence=evidence)])
    ready, ledger, state, clock, release = make_readiness(tmp_path, items)
    result = ready.evaluate(BASE_MS)
    assert result.verdict == "no_go"


def test_an_unrequired_item_without_evidence_does_not_stop_a_go(tmp_path):
    items = checklist(extra=[waivable_item(required=False, evidence=None)])
    ready, ledger, state, clock, release = make_readiness(tmp_path, items)
    result = ready.evaluate(BASE_MS)
    assert result.verdict == "go"
    assert [item["passed"] for item in result.items if item["item"] == EXTRA_ITEM] == [False]


def test_a_waiver_in_the_checklist_passes_a_waivable_item(tmp_path):
    items = checklist(extra=[waivable_item(evidence=None, waiver="deferred to 2026-Q2")])
    ready, ledger, state, clock, release = make_readiness(tmp_path, items)
    result = ready.evaluate(BASE_MS)
    assert result.verdict == "go"
    waived = [item for item in result.items if item["item"] == EXTRA_ITEM][0]
    assert waived["passed"] is True and waived["waiver"] == "deferred to 2026-Q2"


def test_a_waiver_in_the_document_passes_a_waivable_item(tmp_path):
    """READING: `document.readiness.waivers` is a list of item NAMES; the
    recorded `waiver` says which of the two sources waived it, so the
    digest — and therefore the permit — records WHY it passed."""
    items = checklist(extra=[waivable_item(evidence=None)])
    path = write_checklist(tmp_path, items)
    document = a_document(waivers=[EXTRA_ITEM], checklist_path=path)
    ready, ledger, state, clock, release = make_readiness(
        tmp_path, items, document=document
    )
    result = ready.evaluate(BASE_MS)
    assert result.verdict == "go"
    waived = [item for item in result.items if item["item"] == EXTRA_ITEM][0]
    assert waived["waiver"] == readiness_module.DOCUMENT_WAIVER


def test_a_document_waiver_naming_no_checklist_item_refuses(tmp_path):
    """A waiver for nothing is a typo that would silently waive nothing."""
    items = checklist()
    path = write_checklist(tmp_path, items)
    document = a_document(waivers=["no_such_item"], checklist_path=path)
    ready, ledger, state, clock, release = make_readiness(
        tmp_path, items, document=document
    )
    with pytest.raises(ProductionError):
        ready.evaluate(BASE_MS)


# ---------------------------------------------------------------------------
# Unwaivable foundations refuse a waiver (§5.13)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", readiness_module.UNWAIVABLE_ITEMS)
def test_a_checklist_waiver_on_a_foundation_item_refuses(tmp_path, name):
    items = [
        dict(item, waiver="signed off verbally", evidence=None)
        if item["item"] == name else item
        for item in checklist()
    ]
    ready, ledger, state, clock, release = make_readiness(tmp_path, items)
    with pytest.raises(ProductionError) as exc:
        ready.evaluate(BASE_MS)
    assert name in str(exc.value)


@pytest.mark.parametrize("name", readiness_module.UNWAIVABLE_ITEMS)
def test_a_document_waiver_on_a_foundation_item_refuses(tmp_path, name):
    items = checklist()
    path = write_checklist(tmp_path, items)
    document = a_document(waivers=[name], checklist_path=path)
    ready, ledger, state, clock, release = make_readiness(
        tmp_path, items, document=document
    )
    with pytest.raises(ProductionError):
        ready.evaluate(BASE_MS)


@pytest.mark.parametrize("name", readiness_module.UNWAIVABLE_ITEMS)
def test_a_checklist_that_omits_a_foundation_item_refuses(tmp_path, name):
    """The same hole D24 closes: a shortened checklist must not earn a GO."""
    items = [item for item in checklist() if item["item"] != name]
    ready, ledger, state, clock, release = make_readiness(tmp_path, items)
    with pytest.raises(ProductionError):
        ready.evaluate(BASE_MS)


def test_a_foundation_item_declared_optional_refuses(tmp_path):
    """`required: false` on a foundation is a waiver spelled differently."""
    name = readiness_module.UNWAIVABLE_ITEMS[0]
    items = [
        dict(item, required=False) if item["item"] == name else item
        for item in checklist()
    ]
    ready, ledger, state, clock, release = make_readiness(tmp_path, items)
    with pytest.raises(ProductionError):
        ready.evaluate(BASE_MS)


# ---------------------------------------------------------------------------
# Release binding (D24)
# ---------------------------------------------------------------------------


def test_a_checklist_whose_digest_is_not_the_releases_refuses(tmp_path):
    """D24: without this, `doc_hash` covers the PATH and not the contents."""
    ready, ledger, state, clock, release = make_readiness(tmp_path, digest="f" * 64)
    with pytest.raises(ProductionError) as exc:
        ready.evaluate(BASE_MS)
    assert "checklist" in str(exc.value).lower()


def test_a_checklist_edited_after_the_release_was_cut_refuses(tmp_path):
    items = checklist(extra=[waivable_item(evidence="runbook.md")])
    path = write_checklist(tmp_path, items)
    document = a_document(checklist_path=path)
    ready, ledger, state, clock, release = make_readiness(
        tmp_path, items, document=document
    )
    assert ready.evaluate(BASE_MS).verdict == "go"
    write_checklist(tmp_path, items[:-1])
    with pytest.raises(ProductionError):
        ready.evaluate(BASE_MS)


def test_a_missing_checklist_file_refuses(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    (tmp_path / "readiness.json").unlink()
    with pytest.raises(ProductionError):
        ready.evaluate(BASE_MS)


@pytest.mark.parametrize(
    "bad_item",
    [
        {"item": EXTRA_ITEM, "required": True, "evidence": "a.json"},
        {"item": EXTRA_ITEM, "required": True, "evidence": "a.json",
         "waiver": None, "owner": "ops"},
        {"item": EXTRA_ITEM, "required": "yes", "evidence": "a.json", "waiver": None},
        {"required": True, "evidence": "a.json", "waiver": None},
        EXTRA_ITEM,
    ],
)
def test_a_malformed_checklist_item_refuses(tmp_path, bad_item):
    """Default-deny over the four declared keys: a missing one, an unknown
    one, a `required` that is not a boolean and a nameless item each
    refuse — on a checklist that is otherwise complete, so the refusal is
    the malformation and not some other hole."""
    items = checklist() + [bad_item]
    ready, ledger, state, clock, release = make_readiness(tmp_path, items)
    with pytest.raises(ProductionError):
        ready.evaluate(BASE_MS)


@pytest.mark.parametrize("bad", [{"items": []}, "a checklist", 3, None])
def test_a_checklist_that_is_not_a_list_of_items_refuses(tmp_path, bad):
    ready, ledger, state, clock, release = make_readiness(
        tmp_path, bad, digest=canonical_hash(bad)
    )
    with pytest.raises(ProductionError):
        ready.evaluate(BASE_MS)


def test_a_checklist_naming_the_same_item_twice_refuses(tmp_path):
    items = checklist(extra=[waivable_item(evidence="a.json"),
                             waivable_item(evidence="b.json")])
    ready, ledger, state, clock, release = make_readiness(tmp_path, items)
    with pytest.raises(ProductionError):
        ready.evaluate(BASE_MS)


# ---------------------------------------------------------------------------
# Expiry (§5.13, what `ActPermit.readiness_until_ms` binds)
# ---------------------------------------------------------------------------


def test_the_evaluation_is_stamped_at_the_instant_it_was_asked_for(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    result = ready.evaluate(BASE_MS + 1234)
    assert result.evaluated_at_ms == BASE_MS + 1234


def test_the_go_expires_after_the_documents_declared_window(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    result = ready.evaluate(BASE_MS)
    assert result.valid_until_ms == BASE_MS + VALID_FOR_MS


def test_the_expiry_window_is_the_documents_and_not_a_code_default(tmp_path):
    items = checklist()
    path = write_checklist(tmp_path, items)
    document = a_document(valid_for_s=3600, checklist_path=path)
    ready, ledger, state, clock, release = make_readiness(
        tmp_path, items, document=document
    )
    assert ready.evaluate(BASE_MS).valid_until_ms == BASE_MS + 3_600_000


# ---------------------------------------------------------------------------
# `record` — the durable, barriered GO (§6)
# ---------------------------------------------------------------------------


def test_recording_appends_one_readiness_record_and_barriers_it(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    seq = ready.record(ready.evaluate(BASE_MS))
    assert [kind for name, kind in ledger.calls if name == "append"] == ["readiness"]
    assert ledger.calls[-1] == ("barrier", None), "the GO is durable before it is read"
    assert seq == ledger.of_kind("readiness")[0]["seq"]


def test_the_readiness_body_is_the_result_plus_the_release_it_binds(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    result = ready.evaluate(BASE_MS)
    ready.record(result)
    body = ledger.of_kind("readiness")[0]["body"]
    assert set(body) == READINESS_BODY_KEYS
    assert body["release_hash"] == RELEASE_HASH
    assert {k: body[k] for k in RESULT_FIELDS} == result.to_obj()


def test_the_readiness_record_id_is_kind_qualified(tmp_path):
    """R9: record ids are kind-qualified and unique across the series."""
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    ready.record(ready.evaluate(BASE_MS))
    ready.record(ready.evaluate(BASE_MS + 60_000))
    ids = [env["id"] for env in ledger.of_kind("readiness")]
    assert all(i.startswith("readiness:") for i in ids), ids
    assert len(set(ids)) == 2


def test_a_no_go_is_recorded_just_as_a_go_is(tmp_path):
    """"`ready` is the only verb that writes it" (§6) — and a NO-GO is a
    durable fact an operator must be able to read back."""
    items = checklist(**{readiness_module.UNWAIVABLE_ITEMS[0]: None})
    ready, ledger, state, clock, release = make_readiness(tmp_path, items)
    result = ready.evaluate(BASE_MS)
    ready.record(result)
    assert ledger.of_kind("readiness")[0]["body"]["verdict"] == "no_go"


def test_the_recorded_evaluation_folds_into_the_state_view(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    result = ready.evaluate(BASE_MS)
    ready.record(result)
    projection = state.snapshot().readiness
    assert isinstance(projection, ReadinessProjection)
    assert projection.readiness_digest == result.readiness_digest
    assert projection.valid_until_ms == result.valid_until_ms


def test_the_result_round_trips_through_the_folds_projection(tmp_path):
    """§5.8.1's `ReadinessProjection` carries exactly these five fields, so
    `current` can rebuild the result the permit binds."""
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    result = ready.evaluate(BASE_MS)
    ready.record(result)
    projection = state.snapshot().readiness
    assert ReadinessResult.from_obj(projection.to_obj()) == result


# ---------------------------------------------------------------------------
# `current` — read the fold, never the ledger (§5.13)
# ---------------------------------------------------------------------------


def test_current_is_none_before_anything_was_recorded(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    assert ready.current(state.snapshot(), BASE_MS) is None


def test_current_returns_the_recorded_evaluation_while_it_is_unexpired(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    result = ready.evaluate(BASE_MS)
    ready.record(result)
    assert ready.current(state.snapshot(), BASE_MS + VALID_FOR_MS - 1) == result


def test_current_is_none_at_the_instant_the_go_expires(tmp_path):
    """Expiry is INCLUSIVE, as `Arming`'s is: at the deadline it is gone."""
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    ready.record(ready.evaluate(BASE_MS))
    assert ready.current(state.snapshot(), BASE_MS + VALID_FOR_MS) is None


def test_current_is_none_after_the_go_expires(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    ready.record(ready.evaluate(BASE_MS))
    assert ready.current(state.snapshot(), BASE_MS + VALID_FOR_MS + 1) is None


def test_current_returns_a_recorded_no_go_rather_than_hiding_it(tmp_path):
    """The verdict is the caller's business; `current` answers freshness."""
    items = checklist(**{readiness_module.UNWAIVABLE_ITEMS[0]: None})
    ready, ledger, state, clock, release = make_readiness(tmp_path, items)
    ready.record(ready.evaluate(BASE_MS))
    assert ready.current(state.snapshot(), BASE_MS).verdict == "no_go"


def test_current_refuses_a_go_earned_under_another_release(tmp_path):
    """D24: "every live rung requires a current GO record bound to the
    EXACT release". The projection drops `release_hash`, so the digest —
    which is computed over it — is what proves the binding."""
    items = checklist()
    ready, ledger, state, clock, release = make_readiness(tmp_path, items)
    ready.record(ready.evaluate(BASE_MS))
    other, _, _, _, _ = make_readiness(
        tmp_path, items, release_hash=OTHER_RELEASE_HASH, state=SeriesState(SERIES_ID)
    )
    assert other.current(state.snapshot(), BASE_MS) is None


def test_current_never_folds_the_ledger_again(tmp_path):
    """§5.13: read from `StateView.readiness`, never by folding again."""
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    ready.record(ready.evaluate(BASE_MS))
    view = state.snapshot()
    ledger.calls.clear()
    ready.current(view, BASE_MS)
    assert ledger.calls == []


# ---------------------------------------------------------------------------
# The axis the action matrix reads (§5.14, §5.16)
# ---------------------------------------------------------------------------


def test_a_current_go_answers_go_on_the_policy_axis(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    ready.record(ready.evaluate(BASE_MS))
    assert ready.verdict_for(state.snapshot(), BASE_MS) == "go"


@pytest.mark.parametrize("at_ms", [BASE_MS + VALID_FOR_MS, BASE_MS + VALID_FOR_MS + 1])
def test_an_expired_go_answers_no_go_on_the_policy_axis(tmp_path, at_ms):
    """READING: "expired => no_go" has ONE owner. `test_policy.py` pins
    that a live submit needs `readiness == "go"`; a leg restating the
    expiry rule would be the second copy that diverges (CLAUDE.md)."""
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    ready.record(ready.evaluate(BASE_MS))
    assert ready.verdict_for(state.snapshot(), at_ms) == "no_go"


def test_a_series_that_never_evaluated_answers_no_go(tmp_path):
    """Fail closed: nothing recorded is not the same as nothing wrong."""
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    assert ready.verdict_for(state.snapshot(), BASE_MS) == "no_go"


def test_the_policy_axis_is_always_a_readiness_verdicts_member(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    assert ready.verdict_for(state.snapshot(), BASE_MS) in vocab.READINESS_VERDICTS


def test_a_recorded_no_go_answers_no_go(tmp_path):
    items = checklist(**{readiness_module.UNWAIVABLE_ITEMS[0]: None})
    ready, ledger, state, clock, release = make_readiness(tmp_path, items)
    ready.record(ready.evaluate(BASE_MS))
    assert ready.verdict_for(state.snapshot(), BASE_MS) == "no_go"


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_readiness_takes_its_collaborators_by_keyword(tmp_path):
    """§5.16's spelling rule: values positional, collaborators named."""
    path = write_checklist(tmp_path, checklist())
    with pytest.raises(TypeError):
        Readiness(a_document(checklist_path=path), FakeRelease("a" * 64), None, None, None)


def test_constructing_a_readiness_writes_nothing(tmp_path):
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    assert ledger.records == [] and ledger.calls == []


def test_evaluating_writes_nothing_until_record_is_called(tmp_path):
    """`evaluate` answers; `record` is what makes the answer durable."""
    ready, ledger, state, clock, release = make_readiness(tmp_path)
    ready.evaluate(BASE_MS)
    assert ledger.records == []


# ---------------------------------------------------------------------------
# §5.13.4 — outcome evidence: the third kind, what the SERIES can prove
# ---------------------------------------------------------------------------
#
# Phase 1 knows two kinds of evidence: an operator-supplied assertion (any
# truthy value passes) and the unwaivable foundation checks the code
# performs itself. Phase 2 adds evidence the series PROVES from its own
# recorded outcomes, through one hook — `Readiness.evidence_for(name, view,
# at_ms) -> (bool, detail)` — resolved against a module-level TABLE, so a
# new evidence name is a table entry and a test line and never a branch.
#
# The three names are ordinary WAIVABLE items: a shadow series has no
# outcomes and a new release legitimately starts with none. That sentence is
# only true if an EMPTY series FAILS them — otherwise no waiver would ever
# be needed — so "nothing to score" is a NO-GO here, unlike §5.10.1's
# monitor, whose vacuous window is `insufficient` rather than `ok`.

DAY_MS = 86_400_000
HOUR_MS = 3_600_000

#: §4.1's phase-2 readiness knobs, as a document that cites all three
#: evidence names must declare them.
OUTCOME_WINDOW = "P7D"
OUTCOME_WINDOW_MS = 7 * DAY_MS
MIN_COVERAGE = 0.8
MAX_OUTCOME_AGE = "PT6H"
MAX_OUTCOME_AGE_MS = 6 * HOUR_MS
CALIBRATION_MONITOR = "calib"


def evidence_document(tmp_path, items, *, window=OUTCOME_WINDOW, coverage=MIN_COVERAGE,
                      max_age=MAX_OUTCOME_AGE, monitor=CALIBRATION_MONITOR, waivers=()):
    """A document whose `readiness` block declares §5.13.4's knobs, and the checklist path."""
    path = write_checklist(tmp_path, items)
    obj = minimal_document(series_id=SERIES_ID)
    set_path(obj, ["readiness", "checklist"], path)
    set_path(obj, ["readiness", "waivers"], list(waivers))
    set_path(obj, ["readiness", "valid_for_s"], VALID_FOR_S)
    for key, value in (
        ("outcome_window", window),
        ("min_outcome_coverage", coverage),
        ("max_outcome_age", max_age),
        ("calibration_monitor", monitor),
    ):
        if value is not None:
            set_path(obj, ["readiness", key], value)
    if monitor is not None:
        set_path(obj, ["monitors", monitor], {"uses": "calibration", "params": {}})
    return ServeDocument.from_obj(obj), path


def evidence_item(name, item=None, required=True, waiver=None):
    """One checklist item whose `evidence` is a series evidence NAME."""
    return {"item": item or f"{name}_item", "required": required,
            "evidence": name, "waiver": waiver}


def make_evidence_readiness(tmp_path, name="outcome_coverage", **knobs):
    """A `Readiness` over a checklist that cites one evidence name."""
    items = checklist(extra=[evidence_item(name)])
    document, path = evidence_document(tmp_path, items, **knobs)
    clock = TestClock(start_ms=BASE_MS)
    state = SeriesState(SERIES_ID)
    ledger = FoldingLedger(state, clock)
    ready = Readiness(
        document, FakeRelease(canonical_hash(items)),
        ledger=ledger, state=state, clock=clock, checklist_path=path,
    )
    return ready, ledger, state, clock


def decided_leg(ledger, leg_id="leg-1", tick_id="T1", at_ms=BASE_MS):
    """Append the `decision` and the `tick` that give one leg its instant (§5.13.2)."""
    ledger.append({"kind": "decision", "id": f"decision:{tick_id}", "body": {
        "tick_id": tick_id,
        "decision_plan_ids": [f"plan-{leg_id}"],
        "decision_plan_digests": ["d" * 64],
        "legs": [{
            "leg_id": leg_id, "instrument": "AAA", "prediction": 0.6,
            "confidence": 0.5, "baseline": 0.5, "expected_value": 1.0,
            "reference_price": "100", "proposal": {"qty": "10"},
            "findings": [], "final": "buy", "client_ref": f"ref-{leg_id}",
        }],
    }})
    ledger.append({"kind": "tick", "id": f"tick:{tick_id}", "body": {
        "tick_id": tick_id, "tick_at": at_ms, "data_asof_ms": at_ms - 1_000,
        "observed_at_ms": at_ms, "status": "decided", "feed": {},
        "inputs_digest": "e" * 64, "nav": "1000", "calendar": "open",
        "overrun_absorbed": [], "latency_ms": {}, "leg_latency_ms": {},
        "health": "ready", "breaker": "active", "rung": "paper",
        "refusal_reason": None, "error": None,
    }})


def outcome_for(ledger, leg_id="leg-1", *, known_at_ms=BASE_MS, effective_at_ms=None,
                terminal=True, outcome_kind="settled", supersedes=None, record_id=None):
    """Append one §6 `outcome` record; returns its id, so a correction can name it."""
    record_id = record_id or f"outcome:{leg_id}:{known_at_ms}"
    ledger.append({"kind": "outcome", "id": record_id, "body": {
        "leg_id": leg_id, "outcome_kind": outcome_kind,
        "effective_at_ms": known_at_ms if effective_at_ms is None else effective_at_ms,
        "known_at_ms": known_at_ms, "value": "1", "weight": "10",
        "terminal": terminal, "supersedes": supersedes, "source": "settlement",
    }})
    return record_id


def monitor_verdict(ledger, monitor=CALIBRATION_MONITOR, slice_="all", status="ok",
                    provisional=False):
    """Append one §6 `monitor` record, which is what the fold keeps per monitor and slice."""
    ledger.append({"kind": "monitor", "id": f"monitor:{monitor}:{slice_}:{status}", "body": {
        "monitor": monitor, "slice": slice_, "window": "count:50",
        "statistic": "0.02", "threshold": "0.10", "status": status,
        "provisional": provisional,
    }})


class AlwaysTrue(readiness_module.Evidence):
    """A fake evidence name, to prove the TABLE resolves and not a branch."""

    def __init__(self):
        self.calls = []

    def prove(self, readiness, view, at_ms):
        self.calls.append((readiness, view, at_ms))
        return True, "the fake rule answered"


# -- the table and the hook -------------------------------------------------


def test_the_module_exports_the_evidence_seam_and_its_table():
    for name in ("Evidence", "EVIDENCE_RULES", "OutcomeCoverage", "OutcomeFreshness",
                 "CalibrationCurrent"):
        assert name in readiness_module.__all__, f"{name} must be part of the public surface"


def test_the_evidence_table_is_keyed_by_the_vocabularys_names_exactly():
    """§5.13.4: the names are a closed set, and the table is what resolves
    them — a name in one and not the other is a name nothing can prove."""
    assert set(readiness_module.EVIDENCE_RULES) == set(vocab.READINESS_EVIDENCE)


def test_every_evidence_rule_is_an_evidence():
    for name, rule in readiness_module.EVIDENCE_RULES.items():
        assert isinstance(rule, readiness_module.Evidence), name


def test_the_evidence_seam_is_abstract_and_refuses_to_be_built():
    """CLAUDE.md: a hook that only raised `NotImplementedError` would let an
    incomplete rule construct and fail at the first `ready`."""
    assert readiness_module.Evidence.__abstractmethods__
    with pytest.raises(TypeError, match="abstract"):
        readiness_module.Evidence()


def test_none_of_the_evidence_names_is_an_unwaivable_item():
    """§5.13.4: "these are ordinary WAIVABLE items ... so they do not join
    `UNWAIVABLE_ITEMS`, which stays the six foundation items"."""
    assert not set(vocab.READINESS_EVIDENCE) & set(vocab.UNWAIVABLE_ITEMS)
    assert len(vocab.UNWAIVABLE_ITEMS) == 6


def test_a_new_evidence_name_is_a_table_entry_and_not_a_branch(tmp_path, monkeypatch):
    """The whole point of the table: a name added to it resolves, with no
    edit anywhere else. A dispatch written as `if name ==` could not."""
    fake = AlwaysTrue()
    monkeypatch.setitem(readiness_module.EVIDENCE_RULES, "fake_evidence", fake)
    items = checklist(extra=[evidence_item("fake_evidence")])
    document, path = evidence_document(tmp_path, items)
    state = SeriesState(SERIES_ID)
    clock = TestClock(start_ms=BASE_MS)
    ready = Readiness(document, FakeRelease(canonical_hash(items)),
                      ledger=FoldingLedger(state, clock), state=state, clock=clock,
                      checklist_path=path)
    result = ready.evaluate(BASE_MS)
    assert result.verdict == "go"
    assert len(fake.calls) == 1, "the table's rule is what answered the item"


def test_an_evidence_rule_is_handed_the_fold_and_the_evaluation_instant(tmp_path, monkeypatch):
    """The hook's signature is `(name, view, at_ms)`: the fold's frozen view
    and the instant the evaluation was asked for, never a wall clock."""
    fake = AlwaysTrue()
    monkeypatch.setitem(readiness_module.EVIDENCE_RULES, "fake_evidence", fake)
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="fake_evidence")
    ready.evaluate(BASE_MS + 77)
    (_ready, view, at_ms), = fake.calls
    assert at_ms == BASE_MS + 77
    assert view.head_seq == state.snapshot().head_seq


def test_evidence_for_answers_a_pair_of_verdict_and_detail(tmp_path, monkeypatch):
    monkeypatch.setitem(readiness_module.EVIDENCE_RULES, "fake_evidence", AlwaysTrue())
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="fake_evidence")
    proven, detail = ready.evidence_for("fake_evidence", state.snapshot(), BASE_MS)
    assert proven is True
    assert isinstance(detail, str) and detail


def test_evidence_for_refuses_a_name_the_table_does_not_hold(tmp_path):
    ready, ledger, state, clock = make_evidence_readiness(tmp_path)
    with pytest.raises(ProductionError):
        ready.evidence_for("outcome_covrage", state.snapshot(), BASE_MS)


def test_an_ordinary_operator_assertion_still_passes_by_truthiness(tmp_path):
    """Phase 1's kind of evidence is untouched: only a name the TABLE holds
    is proven by the series."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path)
    items = checklist(extra=[waivable_item(evidence="signed-off in ops/2026-01-06.md")])
    document, path = evidence_document(tmp_path, items)
    state = SeriesState(SERIES_ID)
    ready = Readiness(document, FakeRelease(canonical_hash(items)),
                      ledger=FoldingLedger(state, clock), state=state, clock=clock,
                      checklist_path=path)
    assert ready.evaluate(BASE_MS).verdict == "go"


# -- outcome_coverage -------------------------------------------------------


def test_coverage_passes_when_enough_decided_legs_carry_a_terminal_outcome(tmp_path):
    ready, ledger, state, clock = make_evidence_readiness(tmp_path)
    for index in range(5):
        decided_leg(ledger, leg_id=f"leg-{index}", tick_id=f"T{index}", at_ms=BASE_MS - DAY_MS)
    for index in range(4):
        outcome_for(ledger, leg_id=f"leg-{index}", known_at_ms=BASE_MS - HOUR_MS)
    proven, detail = ready.evidence_for("outcome_coverage", state.snapshot(), BASE_MS)
    assert proven is True, detail
    assert ready.evaluate(BASE_MS).verdict == "go"


def test_coverage_fails_below_the_declared_minimum(tmp_path):
    """A series whose labels never arrive cannot be shown to be working, and
    a GO earned without that is the checklist agreeing with itself."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path)
    for index in range(5):
        decided_leg(ledger, leg_id=f"leg-{index}", tick_id=f"T{index}", at_ms=BASE_MS - DAY_MS)
    for index in range(3):
        outcome_for(ledger, leg_id=f"leg-{index}", known_at_ms=BASE_MS - HOUR_MS)
    proven, detail = ready.evidence_for("outcome_coverage", state.snapshot(), BASE_MS)
    assert proven is False
    assert "0.6" in detail, detail
    assert ready.evaluate(BASE_MS).verdict == "no_go"


def test_the_minimum_is_the_documents_and_not_a_code_default(tmp_path):
    """§4.1: "Code holds no threshold"."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, coverage=0.5)
    for index in range(4):
        decided_leg(ledger, leg_id=f"leg-{index}", tick_id=f"T{index}", at_ms=BASE_MS - DAY_MS)
    for index in range(2):
        outcome_for(ledger, leg_id=f"leg-{index}", known_at_ms=BASE_MS - HOUR_MS)
    assert ready.evidence_for("outcome_coverage", state.snapshot(), BASE_MS)[0] is True


def test_coverage_counts_only_the_legs_decided_inside_the_window(tmp_path):
    """`document.readiness.outcome_window` bounds the denominator: an
    unscored leg from last month cannot condemn a series for ever."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path)
    decided_leg(ledger, leg_id="old", tick_id="T0", at_ms=BASE_MS - OUTCOME_WINDOW_MS - 1)
    decided_leg(ledger, leg_id="new", tick_id="T1", at_ms=BASE_MS - HOUR_MS)
    outcome_for(ledger, leg_id="new", known_at_ms=BASE_MS - 60_000)
    assert ready.evidence_for("outcome_coverage", state.snapshot(), BASE_MS)[0] is True


def test_a_non_terminal_outcome_is_not_coverage(tmp_path):
    """A `marked` outcome is a mark, not a resolution — §5.13.2 gives
    `terminal` its own field for exactly this question."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path)
    decided_leg(ledger, leg_id="leg-1", tick_id="T1", at_ms=BASE_MS - HOUR_MS)
    outcome_for(ledger, leg_id="leg-1", outcome_kind="marked", terminal=False,
                known_at_ms=BASE_MS - 60_000)
    proven, detail = ready.evidence_for("outcome_coverage", state.snapshot(), BASE_MS)
    assert proven is False, detail


def test_coverage_reads_the_head_of_the_supersede_chain(tmp_path):
    """D21: what stands is the head. A `marked` outcome corrected into a
    terminal `settled` one IS coverage, and the superseded record is not a
    second leg."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path)
    decided_leg(ledger, leg_id="leg-1", tick_id="T1", at_ms=BASE_MS - HOUR_MS)
    first = outcome_for(ledger, leg_id="leg-1", outcome_kind="marked", terminal=False,
                        known_at_ms=BASE_MS - 120_000)
    outcome_for(ledger, leg_id="leg-1", outcome_kind="corrected", terminal=True,
                known_at_ms=BASE_MS - 60_000, supersedes=first)
    assert ready.evidence_for("outcome_coverage", state.snapshot(), BASE_MS)[0] is True


def test_coverage_answers_at_the_cut_and_not_at_head(tmp_path):
    """D21's vintage rule: an outcome learned AFTER the evaluation instant
    cannot have proven a GO taken before it."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path)
    decided_leg(ledger, leg_id="leg-1", tick_id="T1", at_ms=BASE_MS - HOUR_MS)
    outcome_for(ledger, leg_id="leg-1", known_at_ms=BASE_MS + 60_000)
    assert ready.evidence_for("outcome_coverage", state.snapshot(), BASE_MS)[0] is False
    assert ready.evidence_for("outcome_coverage", state.snapshot(), BASE_MS + 60_000)[0] is True


def test_a_window_with_no_decided_leg_proves_nothing(tmp_path):
    """READING: an empty window FAILS. §5.13.4 says a shadow series and a
    new release are why these items are waivable — a sentence that is only
    true if the vacuous case is a NO-GO that a waiver has to clear."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path)
    proven, detail = ready.evidence_for("outcome_coverage", state.snapshot(), BASE_MS)
    assert proven is False
    assert "no decided leg" in detail, detail


def test_a_waiver_clears_the_evidence_item_a_new_release_cannot_prove(tmp_path):
    """The waivable half: the three names are NOT foundations, so a shadow
    series waives them in the document and still earns its GO."""
    items = checklist(extra=[evidence_item("outcome_coverage", item="scored")])
    document, path = evidence_document(tmp_path, items, waivers=["scored"])
    state = SeriesState(SERIES_ID)
    clock = TestClock(start_ms=BASE_MS)
    ready = Readiness(document, FakeRelease(canonical_hash(items)),
                      ledger=FoldingLedger(state, clock), state=state, clock=clock,
                      checklist_path=path)
    result = ready.evaluate(BASE_MS)
    assert result.verdict == "go"
    waived = [item for item in result.items if item["item"] == "scored"][0]
    assert waived["waiver"] == readiness_module.DOCUMENT_WAIVER


# -- outcome_freshness ------------------------------------------------------


def test_freshness_passes_while_the_newest_label_is_inside_the_maximum(tmp_path):
    """The bound is INCLUSIVE, as every `guards.Bound` maximum is: an age
    bound that failed at its own bound would be the one maximum in the
    package meaning something else."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="outcome_freshness")
    decided_leg(ledger, leg_id="leg-1", tick_id="T1", at_ms=BASE_MS - DAY_MS)
    outcome_for(ledger, leg_id="leg-1", known_at_ms=BASE_MS - MAX_OUTCOME_AGE_MS)
    assert ready.evidence_for("outcome_freshness", state.snapshot(), BASE_MS)[0] is True
    assert ready.evaluate(BASE_MS).verdict == "go"


def test_freshness_fails_once_the_newest_label_is_older_than_the_maximum(tmp_path):
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="outcome_freshness")
    decided_leg(ledger, leg_id="leg-1", tick_id="T1", at_ms=BASE_MS - DAY_MS)
    outcome_for(ledger, leg_id="leg-1", known_at_ms=BASE_MS - MAX_OUTCOME_AGE_MS - 1)
    proven, detail = ready.evidence_for("outcome_freshness", state.snapshot(), BASE_MS)
    assert proven is False, detail
    assert ready.evaluate(BASE_MS).verdict == "no_go"


def test_a_stopped_label_feed_still_passes_coverage_and_fails_freshness(tmp_path):
    """§5.13.4's whole reason for two names: "a long window keeps its
    average up for a while after the arrivals stop". Coverage cannot see
    that; freshness is what does."""
    items = checklist(extra=[evidence_item("outcome_coverage"),
                             evidence_item("outcome_freshness")])
    document, path = evidence_document(tmp_path, items)
    state = SeriesState(SERIES_ID)
    clock = TestClock(start_ms=BASE_MS)
    ledger = FoldingLedger(state, clock)
    ready = Readiness(document, FakeRelease(canonical_hash(items)),
                      ledger=ledger, state=state, clock=clock, checklist_path=path)
    # Every leg of the window was scored — but the last label landed two days
    # ago and nothing has arrived since.
    for index in range(5):
        decided_leg(ledger, leg_id=f"leg-{index}", tick_id=f"T{index}",
                    at_ms=BASE_MS - 3 * DAY_MS)
        outcome_for(ledger, leg_id=f"leg-{index}", known_at_ms=BASE_MS - 2 * DAY_MS)
    view = state.snapshot()
    assert ready.evidence_for("outcome_coverage", view, BASE_MS)[0] is True
    assert ready.evidence_for("outcome_freshness", view, BASE_MS)[0] is False
    assert ready.evaluate(BASE_MS).verdict == "no_go"


def test_a_superseded_arrival_still_counts_as_the_feed_being_alive(tmp_path):
    """Freshness deliberately does NOT go through the standing heads:
    it measures whether the FEED is alive, not what stands, and a
    superseded arrival was still an arrival."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="outcome_freshness")
    decided_leg(ledger, leg_id="leg-1", tick_id="T1", at_ms=BASE_MS - DAY_MS)
    first = outcome_for(ledger, leg_id="leg-1", known_at_ms=BASE_MS - 120_000)
    outcome_for(ledger, leg_id="leg-1", outcome_kind="corrected",
                known_at_ms=BASE_MS - 60_000, supersedes=first)
    proven, detail = ready.evidence_for("outcome_freshness", state.snapshot(), BASE_MS)
    assert proven is True, detail
    assert "60000 ms" in detail


def test_freshness_reads_known_at_ms_and_never_the_effective_instant(tmp_path):
    """READING: a LATE label is precisely one whose `effective_at_ms` is old
    and whose `known_at_ms` is now. Bounding the scan on the effective
    instant would drop the freshest arrival there is."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="outcome_freshness")
    decided_leg(ledger, leg_id="leg-1", tick_id="T1", at_ms=BASE_MS - 30 * DAY_MS)
    outcome_for(ledger, leg_id="leg-1", effective_at_ms=BASE_MS - 30 * DAY_MS,
                known_at_ms=BASE_MS - 60_000)
    assert ready.evidence_for("outcome_freshness", state.snapshot(), BASE_MS)[0] is True


def test_a_series_with_no_outcome_at_all_proves_no_freshness(tmp_path):
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="outcome_freshness")
    proven, detail = ready.evidence_for("outcome_freshness", state.snapshot(), BASE_MS)
    assert proven is False
    assert "no outcome" in detail, detail


def test_freshness_ignores_an_arrival_after_the_cut(tmp_path):
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="outcome_freshness")
    decided_leg(ledger, leg_id="leg-1", tick_id="T1", at_ms=BASE_MS - DAY_MS)
    outcome_for(ledger, leg_id="leg-1", known_at_ms=BASE_MS + 1)
    assert ready.evidence_for("outcome_freshness", state.snapshot(), BASE_MS)[0] is False


def test_fresh_labels_on_too_few_legs_fail_coverage_and_pass_freshness(tmp_path):
    """The other half of the pair: freshness alone cannot see a series that
    scores one leg in ten, however promptly it scores it."""
    items = checklist(extra=[evidence_item("outcome_coverage"),
                             evidence_item("outcome_freshness")])
    document, path = evidence_document(tmp_path, items)
    state = SeriesState(SERIES_ID)
    clock = TestClock(start_ms=BASE_MS)
    ledger = FoldingLedger(state, clock)
    ready = Readiness(document, FakeRelease(canonical_hash(items)),
                      ledger=ledger, state=state, clock=clock, checklist_path=path)
    for index in range(10):
        decided_leg(ledger, leg_id=f"leg-{index}", tick_id=f"T{index}", at_ms=BASE_MS - DAY_MS)
    outcome_for(ledger, leg_id="leg-0", known_at_ms=BASE_MS - 60_000)
    view = state.snapshot()
    assert ready.evidence_for("outcome_freshness", view, BASE_MS)[0] is True
    assert ready.evidence_for("outcome_coverage", view, BASE_MS)[0] is False


# -- calibration_current ----------------------------------------------------


def test_calibration_passes_on_the_monitors_latest_ok_verdict(tmp_path):
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="calibration_current")
    monitor_verdict(ledger, status="ok")
    assert ready.evidence_for("calibration_current", state.snapshot(), BASE_MS)[0] is True
    assert ready.evaluate(BASE_MS).verdict == "go"


def test_calibration_fails_on_an_alarm(tmp_path):
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="calibration_current")
    monitor_verdict(ledger, status="alarm")
    proven, detail = ready.evidence_for("calibration_current", state.snapshot(), BASE_MS)
    assert proven is False, detail
    assert ready.evaluate(BASE_MS).verdict == "no_go"


def test_a_provisional_verdict_is_not_evidence(tmp_path):
    """§5.13.4, the point of the section: §5.10.1 makes an outcome monitor
    say out loud that its labels are still arriving, and treating "not yet
    known" as "fine" is the failure this hook exists to prevent. The status
    here is `ok` — only `provisional` separates it from the passing case."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="calibration_current")
    monitor_verdict(ledger, status="ok", provisional=True)
    proven, detail = ready.evidence_for("calibration_current", state.snapshot(), BASE_MS)
    assert proven is False, "a provisional verdict is explicitly NOT evidence"
    assert "provisional" in detail, detail
    assert ready.evaluate(BASE_MS).verdict == "no_go"


def test_the_latest_verdict_is_what_counts_not_the_best_one(tmp_path):
    """The fold keeps the LATEST per monitor and slice; a series that
    alarmed after passing has not proven anything by having passed."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="calibration_current")
    monitor_verdict(ledger, status="ok")
    monitor_verdict(ledger, status="alarm")
    assert ready.evidence_for("calibration_current", state.snapshot(), BASE_MS)[0] is False


def test_a_warn_verdict_is_still_evidence(tmp_path):
    """§5.13.4 refuses `alarm` and `provisional` and nothing else: a warn
    band is a threshold being approached, not a monitor that cannot answer."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="calibration_current")
    monitor_verdict(ledger, status="warn")
    assert ready.evidence_for("calibration_current", state.snapshot(), BASE_MS)[0] is True


def test_an_insufficient_verdict_is_not_evidence(tmp_path):
    """READING: `insufficient` is the monitor saying it has too few
    observations to answer, which is the same "not yet known" the
    provisional rule refuses. A series with three labelled legs must not
    earn a calibration GO because its monitor could not speak."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="calibration_current")
    monitor_verdict(ledger, status="insufficient")
    proven, detail = ready.evidence_for("calibration_current", state.snapshot(), BASE_MS)
    assert proven is False, detail


def test_a_monitor_that_never_recorded_a_verdict_proves_nothing(tmp_path):
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="calibration_current")
    proven, detail = ready.evidence_for("calibration_current", state.snapshot(), BASE_MS)
    assert proven is False
    assert CALIBRATION_MONITOR in detail, detail


def test_every_slice_of_the_monitor_must_be_evidence(tmp_path):
    """READING: the fold keys verdicts by `(monitor, slice)`. A monitor
    sliced per instrument would otherwise pass on its best slice while one
    instrument alarms."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="calibration_current")
    monitor_verdict(ledger, slice_="AAA", status="ok")
    monitor_verdict(ledger, slice_="BBB", status="alarm")
    assert ready.evidence_for("calibration_current", state.snapshot(), BASE_MS)[0] is False


def test_another_monitors_alarm_is_not_this_evidence(tmp_path):
    ready, ledger, state, clock = make_evidence_readiness(tmp_path, name="calibration_current")
    monitor_verdict(ledger, status="ok")
    monitor_verdict(ledger, monitor="pred_shift", status="alarm")
    assert ready.evidence_for("calibration_current", state.snapshot(), BASE_MS)[0] is True


# -- the knobs the names need (§4.1) ----------------------------------------


@pytest.mark.parametrize(
    "name,missing",
    [
        ("outcome_coverage", "outcome_window"),
        ("outcome_coverage", "min_outcome_coverage"),
        ("outcome_freshness", "max_outcome_age"),
        ("calibration_current", "calibration_monitor"),
    ],
)
def test_an_evidence_name_whose_knob_the_document_lacks_refuses(tmp_path, name, missing):
    """A missing threshold is a MISCONFIGURATION, not a failed proof: it
    refuses by name rather than being recorded as a NO-GO nobody can fix,
    and §4.1 rules that the code holds no threshold to fall back on."""
    knobs = {"outcome_window": "window", "min_outcome_coverage": "coverage",
             "max_outcome_age": "max_age", "calibration_monitor": "monitor"}
    ready, ledger, state, clock = make_evidence_readiness(
        tmp_path, name=name, **{knobs[missing]: None}
    )
    with pytest.raises(ProductionError) as exc:
        ready.evaluate(BASE_MS)
    assert missing in str(exc.value)


def test_the_knobs_are_only_needed_when_an_item_cites_the_name(tmp_path):
    """A phase-1 document that names no series evidence keeps working with
    no `readiness` knobs at all — the section adds no required key."""
    items = checklist()
    path = write_checklist(tmp_path, items)
    document = a_document(checklist_path=path)
    state = SeriesState(SERIES_ID)
    clock = TestClock(start_ms=BASE_MS)
    ready = Readiness(document, FakeRelease(canonical_hash(items)),
                      ledger=FoldingLedger(state, clock), state=state, clock=clock,
                      checklist_path=path)
    assert ready.evaluate(BASE_MS).verdict == "go"


# -- what the record binds --------------------------------------------------


def test_the_recorded_item_keeps_the_evidence_name_and_the_proven_verdict(tmp_path):
    """The five digest fields are unchanged: `evidence` stays the NAME the
    operator declared, and `passed` carries what the series proved."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path)
    decided_leg(ledger, leg_id="leg-1", tick_id="T1", at_ms=BASE_MS - HOUR_MS)
    result = ready.evaluate(BASE_MS)
    item = [row for row in result.items if row["item"] == "outcome_coverage_item"][0]
    assert set(item) == set(ITEM_FIELDS)
    assert item["evidence"] == "outcome_coverage"
    assert item["passed"] is False


def test_the_digest_moves_when_the_series_stops_proving_the_same_checklist(tmp_path):
    """What `ActPermit` binds is the EVALUATION, so a checklist that stopped
    being proven cannot re-use a permit earned when it was."""
    ready, ledger, state, clock = make_evidence_readiness(tmp_path)
    decided_leg(ledger, leg_id="leg-1", tick_id="T1", at_ms=BASE_MS - HOUR_MS)
    unproven = ready.evaluate(BASE_MS)
    outcome_for(ledger, leg_id="leg-1", known_at_ms=BASE_MS - 60_000)
    proven = ready.evaluate(BASE_MS)
    assert unproven.verdict == "no_go" and proven.verdict == "go"
    assert unproven.readiness_digest != proven.readiness_digest
