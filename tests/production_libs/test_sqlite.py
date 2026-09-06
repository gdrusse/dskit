"""`libs/sqlite.py` — the second `Ledger`, and the reason the seam is an ABC (§5.8.2).

The pack exists so a serve series can keep its chain in a database instead
of a directory of JSONL segments, without one line downstream learning
which it got. What is asserted here is therefore mostly SAMENESS, plus the
three refusals that are the pack's own:

* **Conformance.** The same records folded through both ledgers produce the
  same seqs, the same envelopes, the same hashes, the same `verify()`
  answer and the same `scan` results. This is the strongest test in the
  file: an implementation that agreed with §5.8's prose but not with
  `JsonlLedger` would still break `compose.py`, which builds either from
  one site.
* **The pragmas are not configurable.** `journal_mode=WAL` and
  `synchronous=FULL` are read back off the file under every
  `durability.fsync` grade. `fsync` grades the BARRIER cadence — how often
  the writer commits — and never the pragma, because a chain whose
  durability can be lowered by a config key is not a chain.
* **Append-only is the STORE's promise, not the writer's.** Raw `UPDATE`,
  `DELETE` and a mid-chain `INSERT` are issued against the file through a
  connection this package never opened, and the store refuses all three.
  That is the property JSONL gets from `O_APPEND` and a sqlite file would
  otherwise lack, so it is asserted from OUTSIDE the class or it is not
  asserted at all.
* **`rotate` refuses.** A sqlite chain is one file and
  `document.placement.rotate` names a JSONL segmentation policy, so a
  document that selects `sqlite` while declaring one refuses at `plan`
  rather than silently ignoring a knob its author believed in.

The fakes, the JSONL file helpers and the smallest valid document have
owners elsewhere and are imported: `tests.production.test_ledger` owns the
clock and state stand-ins the ledger seam actually calls and the segment
rewriters, and `tests.production.test_document` owns the minimal shadow
document. A second copy of any of them is the bug.
"""

import inspect
import json
import sqlite3
import subprocess
import sys

import pytest

from dskit.production.base import GENESIS_HASH, ProductionError, record_hash
from dskit.production.document import ServeDocument
from dskit.production.ledger import (
    LEDGER_KINDS,
    JsonlLedger,
    Ledger,
    ServeRoot,
    ledger_class,
)
from dskit.production.libs.sqlite import SqliteLedger

from tests.production.test_document import minimal_document
from tests.production.test_ledger import (
    FakeClock,
    RecordingState,
    _raw_lines,
    _rec,
    _rewrite,
    _segment_paths,
)

SERIES = "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1"
PROCESS = "proc-a"
RELEASE = "a" * 64

#: The mixed batch the conformance run folds through both ledgers: several
#: §6 kinds, a body carrying its own `release_hash`, a money string, a
#: dimensionless float, a null, and a repeated id.
BATCH = (
    _rec(kind="process", rid="process:p-1", event="start", release_hash="b" * 64),
    _rec(kind="tick_start", rid="tick_start:t-1", tick_id="t-1", tick_at_ms=1_000),
    _rec(kind="tick_start", rid="tick_start:t-1", tick_id="t-1", tick_at_ms=1_000),
    _rec(kind="decision", rid="decision:t-1", confidence=0.25, legs=[]),
    _rec(kind="fill", rid="fill:f-1", price="101.25", qty="3"),
    _rec(kind="tick", rid="tick:t-1", tick_id="t-1", status="ok", nav=None),
    _rec(kind="order_event", rid="order_event:o-1", event="ack", reason=""),
)

#: The six distinct records `BATCH` holds — one id repeats, and a repeat is
#: not a record.
RECORDS = 6


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class Chain:
    """One open ledger with the serve root and fold that belong to it."""

    def __init__(self, ledger, serve, state):
        self.ledger = ledger
        self.serve = serve
        self.state = state


@pytest.fixture
def open_chain(tmp_path):
    """Open ledgers of either kind under their own serve roots; close them at teardown."""
    opened = []

    def _open(cls=SqliteLedger, name=None, serve=None, clock=None, **kw):
        state = kw.pop("state", RecordingState())
        serve = serve if serve is not None else ServeRoot(
            str(tmp_path / (name or cls.__name__.lower())), SERIES
        )
        led = cls(
            serve,
            PROCESS,
            RELEASE,
            clock=clock if clock is not None else FakeClock(),
            state=state,
            **kw,
        )
        opened.append(led)
        return Chain(led, serve, state)

    yield _open
    for led in opened:
        try:
            led.close()
        except Exception:
            pass


@pytest.fixture
def both(open_chain):
    """Open one chain of each kind over identical clocks; yield `(jsonl, sqlite)`."""

    def _open(**kw):
        return (
            open_chain(JsonlLedger, name="jsonl", **kw),
            open_chain(SqliteLedger, name="sqlite", **kw),
        )

    return _open


def sql(chain, statement, params=()):
    """Run one statement against the FILE, through a connection this package never opened."""
    connection = sqlite3.connect(chain.serve.database_path)
    try:
        cursor = connection.execute(statement, params)
        rows = cursor.fetchall()
        connection.commit()
        return rows
    finally:
        connection.close()


def rows(chain):
    """How many records the store holds — committed ones only."""
    return sql(chain, "SELECT count(*) FROM records")[0][0]


def reopen(chain, cls=SqliteLedger):
    """Close and reopen the same series, so a store is read from disk not from memory."""
    chain.ledger.close()
    return cls(chain.serve, PROCESS, RELEASE, clock=FakeClock())


# ---------------------------------------------------------------------------
# The registration §4.3 calls import
# ---------------------------------------------------------------------------


def test_the_pack_registers_sqlite_into_the_ledger_family():
    assert LEDGER_KINDS.resolve("sqlite") is SqliteLedger
    assert "sqlite" in LEDGER_KINDS


def test_the_pack_is_a_ledger_and_shares_the_jsonl_constructor(open_chain):
    """§5.8.2: "the SAME constructor as `JsonlLedger`, so `compose.py`
    builds either from one site and nothing downstream learns which it
    got." A differing signature would make the composition root branch."""
    assert issubclass(SqliteLedger, Ledger)
    assert inspect.signature(SqliteLedger.__init__) == inspect.signature(
        JsonlLedger.__init__
    )
    assert isinstance(open_chain().ledger, Ledger)


# ---------------------------------------------------------------------------
# Conformance — the two chains agree, record for record (§5.8.2)
# ---------------------------------------------------------------------------


def test_the_two_ledgers_assign_the_same_seqs_to_the_same_records(both):
    jsonl, sqlite_chain = both()
    assert jsonl.ledger.append_many(BATCH) == sqlite_chain.ledger.append_many(BATCH)


def test_the_two_ledgers_write_identical_envelopes(both):
    """The envelope is the contract, so it is compared whole rather than
    field by field: a differing `schema_version`, a dropped `body` member or
    a re-derived `payload_digest` would all change what a reader sees."""
    jsonl, sqlite_chain = both()
    jsonl.ledger.append_many(BATCH)
    sqlite_chain.ledger.append_many(BATCH)
    assert list(sqlite_chain.ledger.scan()) == list(jsonl.ledger.scan())
    assert len(list(sqlite_chain.ledger.scan())) == RECORDS


def test_the_two_chains_reach_the_same_head(both):
    jsonl, sqlite_chain = both()
    jsonl.ledger.append_many(BATCH)
    sqlite_chain.ledger.append_many(BATCH)
    assert sqlite_chain.ledger.head() == jsonl.ledger.head()
    assert sqlite_chain.ledger.head()[1] != GENESIS_HASH


def test_both_verify_an_intact_chain(both):
    jsonl, sqlite_chain = both()
    jsonl.ledger.append_many(BATCH)
    sqlite_chain.ledger.append_many(BATCH)
    assert (jsonl.ledger.verify(), sqlite_chain.ledger.verify()) == (None, None)


@pytest.mark.parametrize("kind", ["tick_start", "fill", "process", "monitor"])
def test_scan_filters_by_kind_identically(both, kind):
    jsonl, sqlite_chain = both()
    jsonl.ledger.append_many(BATCH)
    sqlite_chain.ledger.append_many(BATCH)
    assert list(sqlite_chain.ledger.scan(kind=kind)) == list(
        jsonl.ledger.scan(kind=kind)
    )


@pytest.mark.parametrize("since", [0, 1, 3, 6, 99])
def test_scan_since_seq_is_exclusive_in_both(both, since):
    jsonl, sqlite_chain = both()
    jsonl.ledger.append_many(BATCH)
    sqlite_chain.ledger.append_many(BATCH)
    ours = list(sqlite_chain.ledger.scan(since_seq=since))
    assert ours == list(jsonl.ledger.scan(since_seq=since))
    assert all(envelope["seq"] > since for envelope in ours)


def test_both_fold_the_same_envelopes_into_the_attached_state(both):
    """§5.8.1: the fold is never behind the chain, and it is fed the record
    as it was WRITTEN. Two stores that agreed on their files but not on
    what they handed `SeriesState.apply` would diverge invisibly."""
    jsonl, sqlite_chain = both()
    jsonl.ledger.append_many(BATCH)
    sqlite_chain.ledger.append_many(BATCH)
    assert sqlite_chain.state.applied == jsonl.state.applied
    assert len(sqlite_chain.state.applied) == RECORDS


def test_the_same_id_and_payload_returns_the_prior_seq_in_both(both):
    jsonl, sqlite_chain = both()
    first = _rec(kind="tick_start", rid="tick_start:t-9", tick_id="t-9")
    for chain in (jsonl, sqlite_chain):
        assert chain.ledger.append(first) == 1
        assert chain.ledger.append(dict(first)) == 1
        assert len(chain.state.applied) == 1
    assert sqlite_chain.ledger.head() == jsonl.ledger.head()


def test_the_same_id_with_a_different_payload_refuses_in_both(both):
    jsonl, sqlite_chain = both()
    for chain in (jsonl, sqlite_chain):
        chain.ledger.append(_rec(kind="tick_start", rid="tick_start:t-9", tick_id="9"))
        with pytest.raises(ProductionError) as exc:
            chain.ledger.append(
                _rec(kind="tick_start", rid="tick_start:t-9", tick_id="MOVED")
            )
        assert "tick_start:t-9" in str(exc.value)


def test_idempotency_and_the_head_survive_a_reopen(open_chain):
    chain = open_chain()
    chain.ledger.append_many(BATCH)
    seq, head = chain.ledger.head()
    again = reopen(chain)
    try:
        assert again.head() == (seq, head)
        assert again.append(BATCH[1]) == 2
        assert again.verify() is None
    finally:
        again.close()


def test_a_snapshot_round_trips_and_latest_snapshot_agrees_with_jsonl(both):
    jsonl, sqlite_chain = both()
    for chain in (jsonl, sqlite_chain):
        chain.ledger.append_many(BATCH)
        chain.ledger.snapshot({"positions": {}, "at": 7})
    assert sqlite_chain.ledger.latest_snapshot() == jsonl.ledger.latest_snapshot()
    assert sqlite_chain.ledger.latest_snapshot()["body"]["at_seq"] == RECORDS


def test_a_ledger_with_no_snapshot_answers_none(open_chain):
    assert open_chain().ledger.latest_snapshot() is None


def test_snapshot_every_n_records_appends_one_automatically(open_chain):
    chain = open_chain(snapshot_every=2)
    chain.ledger.append_many(BATCH[:2])
    assert [envelope["kind"] for envelope in chain.ledger.scan()][-1] == "snapshot"


def test_a_closed_ledger_refuses_a_write_but_still_reads(open_chain):
    chain = open_chain()
    chain.ledger.append_many(BATCH)
    seq, head = chain.ledger.head()
    chain.ledger.close()
    chain.ledger.close()
    with pytest.raises(ProductionError, match="closed"):
        chain.ledger.append(_rec(kind="tick_start", rid="tick_start:after"))
    assert chain.ledger.head() == (seq, head)
    assert list(chain.ledger.scan(kind="fill"))
    assert chain.ledger.verify() is None


def test_a_float_under_a_money_field_refuses_before_anything_is_written(open_chain):
    chain = open_chain()
    with pytest.raises(ProductionError, match="price"):
        chain.ledger.append(_rec(kind="fill", rid="fill:bad", price=101.25))
    assert chain.ledger.head() == (0, GENESIS_HASH)
    assert list(chain.ledger.scan()) == []


def test_a_second_writer_on_the_same_series_refuses(open_chain):
    """`serve.lock` is the series' writer lock whatever the store is."""
    chain = open_chain()
    with pytest.raises(ProductionError, match="serve.lock"):
        SqliteLedger(chain.serve, "proc-b", RELEASE, clock=FakeClock())


def test_the_hash_recipe_is_the_one_shared_recipe(open_chain):
    """Nothing here reads a digest back off the thing that produced it: the
    stored hash is recomputed from `record_hash` and the prior head."""
    chain = open_chain()
    chain.ledger.append_many(BATCH[:2])
    prev = GENESIS_HASH
    for envelope in chain.ledger.scan():
        assert envelope["prev_hash"] == prev
        assert envelope["hash"] == record_hash(
            prev, {k: v for k, v in envelope.items() if k != "hash"}
        )
        prev = envelope["hash"]


# ---------------------------------------------------------------------------
# The pragmas are pinned — no document key reaches them (§5.8.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fsync", ["every", "none", {"batch": {"n": 4, "ms": 250}}], ids=("every", "none", "batch")
)
def test_wal_and_synchronous_full_hold_under_every_fsync_grade(open_chain, fsync):
    """§5.8.2: "`journal_mode=WAL` and `synchronous=FULL` are PINNED and not
    configurable … a chain whose durability can be lowered by a config key
    is not a chain." The grade names how often the writer COMMITS."""
    chain = open_chain(name=f"grade-{len(str(fsync))}", fsync=fsync)
    chain.ledger.append(BATCH[1])
    chain.ledger.barrier()
    assert sql(chain, "PRAGMA journal_mode")[0][0] == "wal"
    assert sql(chain, "PRAGMA synchronous")[0][0] == 2


def test_no_constructor_argument_names_a_pragma():
    """The other half: a knob that does not exist cannot be turned. `fsync`
    is the only durability word in the signature, and it grades commits."""
    names = set(inspect.signature(SqliteLedger.__init__).parameters)
    assert {n for n in names if "sync" in n or "journal" in n or "pragma" in n} == {
        "fsync"
    }


def test_fsync_none_holds_the_write_uncommitted_until_the_barrier(open_chain):
    """`none` still means "commit lazily", never "lose the write": a reader
    outside the transaction sees nothing until `barrier()` commits."""
    chain = open_chain(fsync="none")
    chain.ledger.append_many(BATCH[:2])
    assert rows(chain) == 0
    chain.ledger.barrier()
    assert rows(chain) == 2


def test_fsync_every_commits_each_append(open_chain):
    chain = open_chain(fsync="every")
    chain.ledger.append(BATCH[1])
    assert rows(chain) == 1


def test_fsync_batch_commits_at_n_records(open_chain):
    chain = open_chain(fsync={"batch": {"n": 3, "ms": 10_000}})
    chain.ledger.append_many(BATCH[:2])
    assert rows(chain) == 0
    chain.ledger.append(BATCH[3])
    assert rows(chain) == 3


def test_close_commits_what_a_lazy_grade_was_still_holding(open_chain):
    chain = open_chain(fsync="none")
    chain.ledger.append_many(BATCH[:2])
    chain.ledger.close()
    assert rows(chain) == 2


CRASHING_WRITER = """
import os
from dskit.production.ledger import ServeRoot
from dskit.production.libs.sqlite import SqliteLedger


class Clock:
    def now_ms(self):
        return 1_767_268_800_000

    def monotonic(self):
        return 0.0


led = SqliteLedger(ServeRoot({root!r}, {series!r}), "proc-crash", "a" * 64,
                   clock=Clock(), fsync="none")
led.append({{"kind": "tick_start", "id": "tick_start:t-1", "body": {{}}}})
led.append({{"kind": "tick_start", "id": "tick_start:t-2", "body": {{}}}})
led.barrier()
led.append({{"kind": "tick_start", "id": "tick_start:t-3", "body": {{}}}})
os._exit(9)
"""


def test_a_write_a_crash_never_committed_is_simply_absent(open_chain, tmp_path):
    """The sqlite analogue of JSONL's torn tail, and it must be a real
    crash: a graceful `close()` commits what a lazy grade was holding, so
    only a process that dies can lose a record. A commit is atomic, so what
    is lost is a whole record rather than half a line, and the chain comes
    back shorter and intact — which is what `verify()` must say."""
    root = str(tmp_path / "crash")
    done = subprocess.run(
        [sys.executable, "-c", CRASHING_WRITER.format(root=root, series=SERIES)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 9, done.stderr
    led = SqliteLedger(ServeRoot(root, SERIES), PROCESS, RELEASE, clock=FakeClock())
    try:
        assert led.head()[0] == 2
        assert led.verify() is None
        assert led.append({"kind": "tick_start", "id": "tick_start:t-3", "body": {}}) == 3
    finally:
        led.close()


# ---------------------------------------------------------------------------
# Append-only is the store's promise, not the writer's (§5.8.2)
# ---------------------------------------------------------------------------


def test_the_store_refuses_a_raw_update(open_chain):
    """Issued against the FILE through a connection this package never
    opened: the guarantee is the store's, so proving it through the class
    would prove nothing."""
    chain = open_chain()
    chain.ledger.append_many(BATCH)
    chain.ledger.barrier()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        sql(chain, "UPDATE records SET kind = 'tick' WHERE seq = 1")
    assert chain.ledger.verify() is None


def test_the_store_refuses_a_raw_delete(open_chain):
    chain = open_chain()
    chain.ledger.append_many(BATCH)
    chain.ledger.barrier()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        sql(chain, "DELETE FROM records WHERE seq = 1")
    assert rows(chain) == RECORDS


def test_the_store_refuses_an_insert_into_the_middle_of_the_chain(open_chain):
    """The third trigger: append-only means inserts land at the tail."""
    chain = open_chain()
    chain.ledger.append_many(BATCH)
    chain.ledger.barrier()
    _seq, head = chain.ledger.head()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        sql(
            chain,
            "INSERT INTO records (seq, kind, id, envelope, hash, prev_hash) "
            "VALUES (?, 'tick', 'forged-middle', '{}', ?, ?)",
            (2, "c" * 64, head),
        )


def test_the_store_refuses_a_tail_insert_that_breaks_the_link(open_chain):
    """The same trigger's other half: an append whose `prev_hash` names
    something other than the head is not extending THIS chain."""
    chain = open_chain()
    chain.ledger.append_many(BATCH)
    chain.ledger.barrier()
    seq, _head = chain.ledger.head()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        sql(
            chain,
            "INSERT INTO records (seq, kind, id, envelope, hash, prev_hash) "
            "VALUES (?, 'tick', 'forged-tail', '{}', ?, ?)",
            (seq + 1, "c" * 64, "d" * 64),
        )


def test_the_store_refuses_a_duplicate_record_id(open_chain):
    """`id UNIQUE`: the in-memory idempotency index is a convenience, the
    column is the promise. §6 makes an id unique across the SERIES."""
    chain = open_chain()
    chain.ledger.append_many(BATCH)
    chain.ledger.barrier()
    seq, head = chain.ledger.head()
    with pytest.raises(sqlite3.IntegrityError):
        sql(
            chain,
            "INSERT INTO records (seq, kind, id, envelope, hash, prev_hash) "
            "VALUES (?, 'tick_start', 'tick_start:t-1', '{}', ?, ?)",
            (seq + 1, "c" * 64, head),
        )


def test_the_refusals_survive_a_reopen(open_chain):
    """The triggers live in the FILE, so a process that never created the
    schema still cannot rewrite it — which is the whole point of putting
    them there rather than in the writer."""
    chain = open_chain()
    chain.ledger.append_many(BATCH)
    chain.ledger.close()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        sql(chain, "DELETE FROM records")


# ---------------------------------------------------------------------------
# Damage is located at the same seq both ledgers would name (§5.8)
# ---------------------------------------------------------------------------


def damage(chain, seq, field, value):
    """Edit one stored envelope past the triggers, the way corruption would."""
    sql(chain, "DROP TRIGGER records_no_update")
    (text,) = sql(chain, "SELECT envelope FROM records WHERE seq = ?", (seq,))[0]
    envelope = json.loads(text)
    envelope[field] = value
    sql(
        chain,
        "UPDATE records SET envelope = ? WHERE seq = ?",
        (json.dumps(envelope), seq),
    )


@pytest.mark.parametrize("seq", [1, 3, 6])
def test_verify_locates_an_edited_record_at_its_own_seq(open_chain, seq):
    chain = open_chain()
    chain.ledger.append_many(BATCH)
    chain.ledger.barrier()
    damage(chain, seq, "id", "rewritten")
    assert rows(chain) == RECORDS
    assert chain.ledger.verify() == seq


def test_verify_locates_a_forged_hash(open_chain):
    chain = open_chain()
    chain.ledger.append_many(BATCH)
    chain.ledger.barrier()
    damage(chain, 4, "hash", "e" * 64)
    assert chain.ledger.verify() == 4


def test_verify_locates_a_record_whose_envelope_is_not_json(open_chain):
    chain = open_chain()
    chain.ledger.append_many(BATCH)
    chain.ledger.barrier()
    sql(chain, "DROP TRIGGER records_no_update")
    sql(chain, "UPDATE records SET envelope = 'not json' WHERE seq = 2")
    assert chain.ledger.verify() == 2


def test_a_deletion_is_located_at_the_hole_the_way_jsonl_locates_one(both):
    """§5.8: `first_bad_seq` is "the seq the walk EXPECTED at the first
    failing position", so a deletion is located at the hole rather than at
    the record after it — and both stores must answer the same number."""
    jsonl, sqlite_chain = both()
    jsonl.ledger.append_many(BATCH)
    sqlite_chain.ledger.append_many(BATCH)
    sqlite_chain.ledger.barrier()
    sql(sqlite_chain, "DROP TRIGGER records_no_delete")
    sql(sqlite_chain, "DELETE FROM records WHERE seq = 3")

    kept = [line for line in _raw_lines(jsonl.serve) if '"seq":3,' not in line]
    assert len(kept) == RECORDS - 1
    _rewrite(_segment_paths(jsonl.serve)[0], kept)

    assert sqlite_chain.ledger.verify() == jsonl.ledger.verify() == 3


# ---------------------------------------------------------------------------
# rotate refuses (§5.8.2)
# ---------------------------------------------------------------------------


def test_constructing_with_a_rotation_refuses(open_chain):
    with pytest.raises(ProductionError) as exc:
        open_chain(rotate={"by": "day"})
    assert "rotate" in str(exc.value)


def test_a_document_that_declares_no_rotation_opens(open_chain):
    assert open_chain(rotate=None).ledger.head() == (0, GENESIS_HASH)


def sqlite_document(**overrides):
    """The minimal shadow document with its chain kept in a database."""
    return minimal_document(
        durability={"fsync": "every", "ledger": {"uses": "sqlite"}}, **overrides
    )


def test_a_document_selecting_sqlite_resolves_to_the_pack():
    assert ledger_class(ServeDocument(sqlite_document())) is SqliteLedger


def test_a_document_selecting_sqlite_and_declaring_rotate_refuses():
    """§5.8.2: it "refuses at `plan` rather than silently ignoring a knob
    its author believed in"."""
    obj = sqlite_document(placement={"ledger_root": "./serve", "rotate": {"by": "day"}})
    with pytest.raises(ProductionError) as exc:
        ledger_class(ServeDocument(obj))
    assert "rotate" in str(exc.value)


def test_plan_refuses_that_document_before_it_reads_the_run(tmp_path, capsys):
    """The refusal has to reach the operator through the verb, not only
    through the class: `plan` is where a document becomes a release, and
    the run directory this document names does not even exist."""
    from dskit.production.__main__ import main

    obj = sqlite_document(
        placement={
            "ledger_root": str(tmp_path / "serve"),
            "rotate": {"by": "size", "max_bytes": 1024},
        }
    )
    path = tmp_path / "serve.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    assert main(["plan", str(path)], journal_hook=lambda **row: None) == 1
    assert "rotate" in capsys.readouterr().err


def test_a_jsonl_document_still_accepts_rotate():
    """The refusal belongs to the sqlite chain, not to the grammar: a
    document that names no ledger keeps `jsonl` and its segmentation."""
    obj = minimal_document(placement={"ledger_root": "./serve", "rotate": {"by": "day"}})
    assert ledger_class(ServeDocument(obj)) is JsonlLedger
