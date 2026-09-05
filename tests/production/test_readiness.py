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
    for name in ("Readiness", "ReadinessResult", "readiness_digest", "UNWAIVABLE_ITEMS"):
        assert name in readiness_module.__all__, f"{name} must be part of the public surface"


def test_all_leaks_no_private_name_and_names_nothing_missing():
    assert readiness_module.__all__, "readiness.py must declare __all__"
    assert not [n for n in readiness_module.__all__ if n.startswith("_")]
    missing = [n for n in readiness_module.__all__ if not hasattr(readiness_module, n)]
    assert not missing, f"__all__ names nothing: {missing}"


# ---------------------------------------------------------------------------
# The unwaivable foundations (§5.13)
# ---------------------------------------------------------------------------


def test_the_unwaivable_items_are_a_closed_tuple_of_distinct_names():
    """VOCAB GAP (reported): §8 lists every closed set as living in
    `vocab.py`, and this one is not among them; it is pinned here so the
    set at least has ONE owner until the plan moves it."""
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
