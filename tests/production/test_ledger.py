"""`ledger.py` — the append-only chain, its caches and its serve-root layout.

What this file pins, in the plan's own terms (§5.8, §5.8.1, §6, D13, D15):

* **`ServeRoot`** is the ONLY thing that knows the directory shape. It
  creates `<root>/<series_id>/` plus the `series.json` genesis on first
  use, refuses a series id that disagrees with an existing genesis, and
  hands out one accessor per line of the §5.8 tree.
* **The envelope** is exactly §6's eleven ledger fields merged with the
  caller's record. Ledger-assigned fields never enter `payload_digest`,
  and `hash = record_hash(prev_hash, envelope - hash)` with a genesis
  `prev_hash` of 64 zeros.
* **Idempotency** is by the caller's stable `id` plus that digest: the
  same payload returns the prior `seq` and writes nothing, a different
  payload refuses.
* **Durability** is graded (`every` / `batch:{n, ms}` / `none`) but
  `barrier()` always reaches the platter — D13's safety records cross it
  regardless of policy.
* **Damage is located, not merely detected.** `verify()` returns the
  first bad `seq` for an edit, a deletion, an insertion and a reorder.

  Two readings of "first bad seq" are possible; this suite pins the one
  that is uniform across all four mutations: **the `seq` the walk
  EXPECTED at the first position that fails.** For an edited line that is
  its own `seq`; for a deletion or a reorder it is the `seq` that should
  have been at that position. (Reported as a plan gap — §5.8 says only
  `first_bad_seq | None`.)
* **Money never touches float, ratios may.** The refusal is keyed on
  `vocab.MONEY_FIELDS`, walked recursively to any depth: a `float` under
  a money key refuses and the message names the key path, while a float
  under any other key (`confidence`, `prediction`, `expected_value`,
  `statistic`) is legal — §5.4/§5.5 are explicit that dimensionless
  ratios are floats, and §6's `decision` body carries them.

`clock.py` and `state.py` belong to other groups, so the collaborators
here are local fakes with the two methods the ledger actually calls:
``now_ms()`` and ``apply(record)``.
"""

import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from dskit.production import vocab
from dskit.production.base import ProductionError, canonical_hash, record_hash
from dskit.production.ledger import Checkpoint, JsonlLedger, Ledger, ServeRoot

SERIES = "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1"
PROCESS = "proc-a"
RELEASE = "a" * 64
GENESIS_PREV = "0" * 64

#: `Checkpoint.validate_against` answers with one of these; the names come
#: from the vocabulary, never from a literal spelled here.
CURRENT, STALE = vocab.CACHE_STATES

#: The nine fields §6 says the ledger assigns; `kind` and `id` are the
#: caller's, so the envelope is the caller's record plus these.
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


# ---------------------------------------------------------------------------
# Local fakes — clock.py and state.py are other groups' modules
# ---------------------------------------------------------------------------


class FakeClock:
    """The two `Clock` methods the ledger uses, settable from a test."""

    def __init__(self, ms=1_767_268_800_000):
        self._ms = int(ms)

    def now_ms(self):
        return self._ms

    def monotonic(self):
        return self._ms / 1000.0

    def advance(self, ms):
        self._ms += int(ms)
        return self._ms

    def set(self, ms):
        self._ms = int(ms)
        return self._ms


class RecordingState:
    """A `SeriesState` stand-in: records what `apply` was handed."""

    def __init__(self):
        self.applied = []

    def apply(self, record):
        self.applied.append(record)

    def snapshot(self):
        return {"folded": len(self.applied)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rec(kind="tick_start", rid="tick-1", **body):
    rec = {"kind": kind, "id": rid}
    rec.update(body)
    return rec


def _segment_paths(serve):
    names = sorted(n for n in os.listdir(serve.ledger_dir) if n.endswith(".jsonl"))
    return [os.path.join(serve.ledger_dir, n) for n in names]


def _raw_lines(serve):
    out = []
    for path in _segment_paths(serve):
        with open(path, encoding="utf-8") as fh:
            out.extend(ln for ln in fh.read().split("\n") if ln.strip())
    return out


def _envelopes(serve):
    return [json.loads(ln) for ln in _raw_lines(serve)]


def _rewrite(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


def _fsync_counts(monkeypatch):
    """Count fsyncs, split by whether the fd names a directory."""
    counts = {"file": 0, "dir": 0}
    real_fsync = os.fsync
    real_fdatasync = getattr(os, "fdatasync", None)

    def _classify(fd):
        try:
            return "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        except OSError:
            return "file"

    def fake_fsync(fd):
        counts[_classify(fd)] += 1
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fake_fsync)
    if real_fdatasync is not None:

        def fake_fdatasync(fd):
            counts[_classify(fd)] += 1
            return real_fdatasync(fd)

        monkeypatch.setattr(os, "fdatasync", fake_fdatasync)
    return counts


def _child(code):
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )


@pytest.fixture
def serve(tmp_path):
    return ServeRoot(str(tmp_path / "serve"), SERIES)


@pytest.fixture
def open_ledger():
    """Open ledgers and guarantee the writer lock is released at teardown."""
    opened = []

    def _open(serve_root, process_id=PROCESS, release_hash=RELEASE, clock=None, **kw):
        led = JsonlLedger(
            serve_root,
            process_id,
            release_hash,
            clock=clock if clock is not None else FakeClock(),
            **kw,
        )
        opened.append(led)
        return led

    yield _open
    for led in opened:
        try:
            led.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ServeRoot — the layout is owned in one place (§5.8)
# ---------------------------------------------------------------------------


def test_serve_root_creates_the_series_dir_and_a_genesis_series_json(tmp_path):
    root = str(tmp_path / "serve")
    sr = ServeRoot(root, SERIES)
    assert sr.series_path == os.path.join(root, SERIES)
    assert os.path.isdir(sr.series_path)
    with open(sr.genesis_path, encoding="utf-8") as fh:
        genesis = json.load(fh)
    assert set(genesis) == {"series_id", "created_ms", "schema_version"}
    assert genesis["series_id"] == SERIES
    assert isinstance(genesis["created_ms"], int) and genesis["created_ms"] > 0
    assert isinstance(genesis["schema_version"], int)
    assert genesis["schema_version"] >= 1


def test_serve_root_genesis_is_immutable_across_reopens(tmp_path):
    root = str(tmp_path / "serve")
    first = ServeRoot(root, SERIES)
    with open(first.genesis_path, encoding="utf-8") as fh:
        before = json.load(fh)
    second = ServeRoot(root, SERIES)
    with open(second.genesis_path, encoding="utf-8") as fh:
        after = json.load(fh)
    assert after == before


def test_serve_root_refuses_a_series_id_that_disagrees_with_the_genesis(tmp_path):
    root = str(tmp_path / "serve")
    sr = ServeRoot(root, SERIES)
    other = "018f0f4e-0000-0000-0000-000000000002"
    with open(sr.genesis_path, encoding="utf-8") as fh:
        genesis = json.load(fh)
    genesis["series_id"] = other
    with open(sr.genesis_path, "w", encoding="utf-8") as fh:
        json.dump(genesis, fh)
    with pytest.raises(ProductionError) as exc:
        ServeRoot(root, SERIES)
    assert SERIES in str(exc.value)
    assert other in str(exc.value)


@pytest.mark.parametrize("bad", ["", "   ", 5, None, ["x"]])
def test_serve_root_refuses_a_non_string_or_empty_series_id(tmp_path, bad):
    with pytest.raises(ProductionError):
        ServeRoot(str(tmp_path / "serve"), bad)


def test_serve_root_hands_out_every_path_in_the_layout_tree(serve):
    base = serve.series_path
    assert serve.genesis_path == os.path.join(base, "series.json")
    assert serve.arming_cache == os.path.join(base, "arming.json")
    assert serve.breaker_cache == os.path.join(base, "breaker.json")
    assert serve.checkpoint_cache == os.path.join(base, "checkpoint.json")
    assert serve.halt_sentinel == os.path.join(base, "HALT")
    assert serve.lock_path == os.path.join(base, "serve.lock")
    assert serve.commands_inbox == os.path.join(base, "commands", "inbox")
    assert serve.commands_applied == os.path.join(base, "commands", "applied")
    assert serve.commands_rejected == os.path.join(base, "commands", "rejected")
    assert serve.heartbeat_path == os.path.join(base, "heartbeat.json")
    assert serve.ledger_dir == os.path.join(base, "ledger")


def test_serve_root_release_and_process_paths_follow_the_tree(serve):
    rel = serve.release_dir(RELEASE)
    assert rel == os.path.join(serve.series_path, "releases", RELEASE)
    assert serve.process_base_dir(RELEASE, PROCESS) == os.path.join(
        rel, "process-" + PROCESS, "base"
    )


def test_serve_root_creates_the_directories_other_writers_need(serve):
    for path in (
        serve.ledger_dir,
        serve.commands_inbox,
        serve.commands_applied,
        serve.commands_rejected,
    ):
        assert os.path.isdir(path)


def test_serve_root_does_not_create_the_halt_sentinel(serve):
    assert not os.path.exists(serve.halt_sentinel)


def test_serve_root_exposes_the_series_id_it_validated(serve):
    assert serve.series_id == SERIES


# ---------------------------------------------------------------------------
# The Ledger seam
# ---------------------------------------------------------------------------


def test_ledger_abc_cannot_be_instantiated():
    with pytest.raises(TypeError):
        Ledger()


def test_ledger_declares_the_eight_seam_methods_abstract():
    assert {
        "append",
        "append_many",
        "barrier",
        "scan",
        "head",
        "verify",
        "snapshot",
        "latest_snapshot",
    } <= set(Ledger.__abstractmethods__)


def test_ledger_declares_close_so_the_writer_lock_can_be_released():
    assert callable(getattr(Ledger, "close"))


def test_jsonl_ledger_is_a_ledger(serve, open_ledger):
    led = open_ledger(serve)
    assert isinstance(led, Ledger)


# ---------------------------------------------------------------------------
# The chain (§6, D15)
# ---------------------------------------------------------------------------


def test_a_fresh_ledger_head_is_seq_zero_and_the_genesis_hash(serve, open_ledger):
    led = open_ledger(serve)
    assert led.head() == (0, GENESIS_PREV)


def test_a_fresh_ledger_verifies_and_scans_empty(serve, open_ledger):
    led = open_ledger(serve)
    assert led.verify() is None
    assert list(led.scan()) == []
    assert led.latest_snapshot() is None


def test_the_first_record_chains_from_a_prev_hash_of_sixty_four_zeros(
    serve, open_ledger
):
    led = open_ledger(serve)
    led.append(_rec())
    (env,) = _envelopes(serve)
    assert env["prev_hash"] == GENESIS_PREV
    assert len(env["hash"]) == 64


def test_append_assigns_dense_one_based_seqs(serve, open_ledger):
    led = open_ledger(serve)
    seqs = [led.append(_rec(rid=f"t-{i}")) for i in range(4)]
    assert seqs == [1, 2, 3, 4]
    assert [e["seq"] for e in _envelopes(serve)] == [1, 2, 3, 4]


def test_the_envelope_is_the_callers_record_plus_the_nine_assigned_fields(
    serve, open_ledger
):
    led = open_ledger(serve)
    led.append(_rec(kind="tick_start", rid="t-1", tick_at_ms=17, release_hash_note="x"))
    (env,) = _envelopes(serve)
    assert set(env) == {"kind", "id", "tick_at_ms", "release_hash_note", *ASSIGNED}
    assert env["kind"] == "tick_start"
    assert env["id"] == "t-1"
    assert env["tick_at_ms"] == 17


def test_the_assigned_fields_carry_the_series_process_release_and_clock(
    serve, open_ledger
):
    clock = FakeClock(1_700_000_000_000)
    led = open_ledger(serve, clock=clock)
    led.append(_rec(rid="t-1"))
    clock.advance(2_500)
    led.append(_rec(rid="t-2"))
    first, second = _envelopes(serve)
    for env in (first, second):
        assert env["series_id"] == SERIES
        assert env["process_id"] == PROCESS
        assert env["release_hash"] == RELEASE
        assert isinstance(env["schema_version"], int)
        assert env["schema_version"] >= 1
    assert first["recorded_at_ms"] == 1_700_000_000_000
    assert second["recorded_at_ms"] == 1_700_000_002_500
    assert first["schema_version"] == second["schema_version"]


def test_each_hash_is_record_hash_of_prev_hash_and_the_envelope_without_hash(
    serve, open_ledger
):
    led = open_ledger(serve)
    for i in range(3):
        led.append(_rec(rid=f"t-{i}"))
    prev = GENESIS_PREV
    for env in _envelopes(serve):
        assert env["prev_hash"] == prev
        body = {k: v for k, v in env.items() if k != "hash"}
        assert env["hash"] == record_hash(prev, body)
        prev = env["hash"]


def test_payload_digest_is_the_canonical_hash_of_the_callers_record(serve, open_ledger):
    led = open_ledger(serve)
    rec = _rec(kind="fill", rid="f-1", qty=3, price=Decimal("1.25"))
    led.append(dict(rec))
    (env,) = _envelopes(serve)
    assert env["payload_digest"] == canonical_hash(rec)


def test_payload_digest_excludes_every_ledger_assigned_field(tmp_path, open_ledger):
    """The same body under a different series/process/release/clock digests equal."""
    rec = _rec(kind="fill", rid="f-1", qty=3)
    root_a = ServeRoot(str(tmp_path / "a"), SERIES)
    open_ledger(root_a, clock=FakeClock(1_000)).append(dict(rec))
    root_b = ServeRoot(str(tmp_path / "b"), "018f0f4e-0000-0000-0000-0000000000bb")
    open_ledger(
        root_b,
        process_id="proc-b",
        release_hash="b" * 64,
        clock=FakeClock(9_999_999),
    ).append(dict(rec))
    (env_a,) = _envelopes(root_a)
    (env_b,) = _envelopes(root_b)
    assert env_a["payload_digest"] == env_b["payload_digest"]
    assert env_a["hash"] != env_b["hash"]


def test_append_writes_exactly_one_line_per_record(serve, open_ledger):
    led = open_ledger(serve)
    for i in range(5):
        led.append(_rec(rid=f"t-{i}"))
    assert len(_raw_lines(serve)) == 5
    for path in _segment_paths(serve):
        with open(path, "rb") as fh:
            assert fh.read().endswith(b"\n")


def test_append_many_returns_the_tuple_of_seqs_and_chains_them(serve, open_ledger):
    led = open_ledger(serve)
    seqs = led.append_many([_rec(rid=f"t-{i}") for i in range(3)])
    assert tuple(seqs) == (1, 2, 3)
    assert led.head()[0] == 3
    assert led.verify() is None


def test_scan_yields_every_envelope_in_seq_order(serve, open_ledger):
    led = open_ledger(serve)
    led.append(_rec(kind="tick_start", rid="t-1"))
    led.append(_rec(kind="fill", rid="f-1"))
    led.append(_rec(kind="tick_start", rid="t-2"))
    got = list(led.scan())
    assert [e["seq"] for e in got] == [1, 2, 3]
    assert [e["id"] for e in got] == ["t-1", "f-1", "t-2"]


def test_scan_filters_by_kind(serve, open_ledger):
    led = open_ledger(serve)
    led.append(_rec(kind="tick_start", rid="t-1"))
    led.append(_rec(kind="fill", rid="f-1"))
    led.append(_rec(kind="tick_start", rid="t-2"))
    assert [e["id"] for e in led.scan(kind="tick_start")] == ["t-1", "t-2"]
    assert [e["id"] for e in led.scan(kind="fill")] == ["f-1"]


def test_scan_since_seq_is_exclusive_so_a_snapshot_seq_replays_forward(
    serve, open_ledger
):
    led = open_ledger(serve)
    for i in range(1, 5):
        led.append(_rec(rid=f"t-{i}"))
    assert [e["seq"] for e in led.scan(since_seq=0)] == [1, 2, 3, 4]
    assert [e["seq"] for e in led.scan(since_seq=2)] == [3, 4]
    assert list(led.scan(since_seq=4)) == []


def test_head_after_reopen_is_the_last_lines_seq_and_hash(serve, open_ledger):
    led = open_ledger(serve)
    for i in range(3):
        led.append(_rec(rid=f"t-{i}"))
    led.close()
    last = _envelopes(serve)[-1]
    reopened = open_ledger(serve)
    assert reopened.head() == (last["seq"], last["hash"])


def test_a_reopened_ledger_continues_the_chain(serve, open_ledger):
    led = open_ledger(serve)
    led.append(_rec(rid="t-1"))
    led.close()
    reopened = open_ledger(serve)
    assert reopened.append(_rec(rid="t-2")) == 2
    envs = _envelopes(serve)
    assert envs[1]["prev_hash"] == envs[0]["hash"]
    assert reopened.verify() is None


# ---------------------------------------------------------------------------
# Idempotency by caller id + payload digest (D15)
# ---------------------------------------------------------------------------


def test_the_same_id_and_payload_returns_the_prior_seq_and_appends_nothing(
    serve, open_ledger
):
    led = open_ledger(serve)
    rec = _rec(kind="fill", rid="f-1", qty=3)
    first = led.append(dict(rec))
    again = led.append(dict(rec))
    assert again == first
    assert len(_raw_lines(serve)) == 1
    assert led.head()[0] == 1


def test_the_same_id_with_a_different_payload_refuses_naming_the_id(serve, open_ledger):
    led = open_ledger(serve)
    led.append(_rec(kind="fill", rid="f-1", qty=3))
    with pytest.raises(ProductionError) as exc:
        led.append(_rec(kind="fill", rid="f-1", qty=4))
    assert "f-1" in str(exc.value)
    assert len(_raw_lines(serve)) == 1


def test_idempotency_survives_a_reopen(serve, open_ledger):
    led = open_ledger(serve)
    rec = _rec(kind="fill", rid="f-1", qty=3)
    first = led.append(dict(rec))
    led.close()
    reopened = open_ledger(serve)
    assert reopened.append(dict(rec)) == first
    assert len(_raw_lines(serve)) == 1
    with pytest.raises(ProductionError):
        reopened.append(_rec(kind="fill", rid="f-1", qty=99))


def test_a_deduplicated_append_does_not_refold_the_state(serve, open_ledger):
    state = RecordingState()
    led = open_ledger(serve, state=state)
    rec = _rec(kind="fill", rid="f-1", qty=3)
    led.append(dict(rec))
    led.append(dict(rec))
    assert len(state.applied) == 1


def test_append_many_deduplicates_within_the_batch(serve, open_ledger):
    led = open_ledger(serve)
    rec = _rec(kind="fill", rid="f-1", qty=3)
    seqs = led.append_many([dict(rec), _rec(rid="t-2"), dict(rec)])
    assert tuple(seqs) == (1, 2, 1)
    assert len(_raw_lines(serve)) == 2


# ---------------------------------------------------------------------------
# Vocabulary and default-deny on the record itself
# ---------------------------------------------------------------------------


def test_every_kind_this_suite_appends_is_a_vocab_record_kind():
    for kind in ("process", "tick_start", "tick", "fill", "snapshot", "decision"):
        assert kind in vocab.RECORD_KINDS


def test_an_unknown_kind_refuses(serve, open_ledger):
    led = open_ledger(serve)
    with pytest.raises(ProductionError) as exc:
        led.append(_rec(kind="not_a_record_kind", rid="x-1"))
    assert "not_a_record_kind" in str(exc.value)
    assert _raw_lines(serve) == []


def test_a_missing_kind_refuses(serve, open_ledger):
    led = open_ledger(serve)
    with pytest.raises(ProductionError):
        led.append({"id": "x-1"})


def test_a_missing_id_refuses(serve, open_ledger):
    led = open_ledger(serve)
    with pytest.raises(ProductionError):
        led.append({"kind": "tick_start"})


@pytest.mark.parametrize("bad_id", ["", 7, None])
def test_a_non_string_or_empty_id_refuses(serve, open_ledger, bad_id):
    led = open_ledger(serve)
    with pytest.raises(ProductionError):
        led.append({"kind": "tick_start", "id": bad_id})


@pytest.mark.parametrize("field", ASSIGNED)
def test_a_record_body_may_not_carry_a_ledger_assigned_field(serve, open_ledger, field):
    led = open_ledger(serve)
    with pytest.raises(ProductionError) as exc:
        led.append(_rec(**{field: "forged"}))
    assert field in str(exc.value)
    assert _raw_lines(serve) == []


def test_a_non_dict_record_refuses(serve, open_ledger):
    led = open_ledger(serve)
    with pytest.raises(ProductionError):
        led.append(["kind", "tick_start"])


# ---------------------------------------------------------------------------
# Money never touches float; ratios may (§5.8, §8, vocab.MONEY_FIELDS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", vocab.MONEY_FIELDS)
def test_a_float_under_any_money_field_refuses(serve, open_ledger, field):
    led = open_ledger(serve)
    with pytest.raises(ProductionError) as exc:
        led.append(_rec(kind="fill", rid="f-1", **{field: 1.5}))
    assert field in str(exc.value)
    assert _raw_lines(serve) == []


@pytest.mark.parametrize(
    "body, path_parts",
    [
        ({"legs": [{"price": 1.5}]}, ("legs", "price")),
        ({"detail": {"charges": {"fee": 0.25}}}, ("detail", "charges", "fee")),
        ({"balances": [{"currency": "USD", "total": 1.0}]}, ("balances", "total")),
        ({"price": [1.0, 2]}, ("price",)),
    ],
)
def test_a_nested_money_float_refuses_and_the_message_names_the_path(
    serve, open_ledger, body, path_parts
):
    led = open_ledger(serve)
    with pytest.raises(ProductionError) as exc:
        led.append(_rec(kind="fill", rid="f-1", **body))
    for part in path_parts:
        assert part in str(exc.value)
    assert _raw_lines(serve) == []


def test_a_float_under_a_non_money_field_is_legal(serve, open_ledger):
    """§5.4/§5.5: dimensionless ratios are floats and §6's `decision` carries them."""
    led = open_ledger(serve)
    led.append(
        _rec(
            kind="decision",
            rid="d-1",
            confidence=0.42,
            prediction=-0.9,
            expected_value=0.0125,
            statistic=1.5,
            legs=[{"confidence": 0.5, "qty": 3}],
        )
    )
    (env,) = _envelopes(serve)
    assert env["confidence"] == 0.42
    assert env["prediction"] == -0.9
    assert env["legs"][0]["confidence"] == 0.5
    for name in ("confidence", "prediction", "expected_value", "statistic"):
        assert name not in vocab.MONEY_FIELDS


@pytest.mark.parametrize("value", [3, 0, -7, "1.50", None])
def test_a_money_field_that_is_not_a_float_is_accepted(serve, open_ledger, value):
    led = open_ledger(serve)
    assert led.append(_rec(kind="fill", rid="f-1", price=value)) == 1
    assert _envelopes(serve)[0]["price"] == value


def test_ints_bools_strings_and_nulls_are_accepted(serve, open_ledger):
    led = open_ledger(serve)
    led.append(
        _rec(kind="cash_flow", rid="c-1", amount=100, external=True, reason=None)
    )
    (env,) = _envelopes(serve)
    assert env["amount"] == 100
    assert env["external"] is True
    assert env["reason"] is None


def test_a_decimal_money_field_lands_in_the_file_as_a_string(serve, open_ledger):
    led = open_ledger(serve)
    led.append(_rec(kind="fill", rid="f-1", price=Decimal("1.50"), fee=Decimal("0.02")))
    (env,) = _envelopes(serve)
    assert env["price"] == "1.50"
    assert env["fee"] == "0.02"
    assert isinstance(env["price"], str)


# ---------------------------------------------------------------------------
# The state collaborator (§5.8.1 — the fold is never behind the chain)
# ---------------------------------------------------------------------------


def test_append_folds_the_full_envelope_into_the_attached_state(serve, open_ledger):
    state = RecordingState()
    led = open_ledger(serve, state=state)
    led.append(_rec(kind="tick_start", rid="t-1"))
    led.append(_rec(kind="fill", rid="f-1", qty=2))
    assert [r["id"] for r in state.applied] == ["t-1", "f-1"]
    assert [r["seq"] for r in state.applied] == [1, 2]
    assert state.applied[-1]["hash"] == led.head()[1]
    assert state.applied[-1]["prev_hash"] == state.applied[0]["hash"]


def test_append_many_folds_every_record_in_order(serve, open_ledger):
    state = RecordingState()
    led = open_ledger(serve, state=state)
    led.append_many([_rec(rid=f"t-{i}") for i in range(3)])
    assert [r["seq"] for r in state.applied] == [1, 2, 3]


def test_a_ledger_without_a_state_still_appends(serve, open_ledger):
    led = open_ledger(serve)
    assert led.append(_rec(rid="t-1")) == 1


def test_a_refused_record_is_never_folded(serve, open_ledger):
    state = RecordingState()
    led = open_ledger(serve, state=state)
    with pytest.raises(ProductionError):
        led.append(_rec(kind="not_a_record_kind", rid="x-1"))
    assert state.applied == []


# ---------------------------------------------------------------------------
# Torn tail (D15)
# ---------------------------------------------------------------------------


def test_a_torn_final_line_is_discarded_and_the_chain_continues(serve, open_ledger):
    led = open_ledger(serve)
    for i in range(1, 4):
        led.append(_rec(rid=f"t-{i}"))
    led.close()
    third = _envelopes(serve)[-1]
    path = _segment_paths(serve)[-1]
    with open(path, "ab") as fh:
        fh.write(b'{"kind":"tick_start","id":"torn","se')

    reopened = open_ledger(serve)
    assert reopened.head() == (3, third["hash"])
    assert reopened.append(_rec(rid="t-4")) == 4
    envs = _envelopes(serve)
    assert len(envs) == 4
    assert envs[3]["prev_hash"] == third["hash"]
    assert reopened.verify() is None


def test_the_torn_bytes_are_truncated_rather_than_kept(serve, open_ledger):
    led = open_ledger(serve)
    led.append(_rec(rid="t-1"))
    led.close()
    path = _segment_paths(serve)[-1]
    with open(path, "ab") as fh:
        fh.write(b'{"kind":"tick_st')
    reopened = open_ledger(serve)
    reopened.append(_rec(rid="t-2"))
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    assert "tick_st\n" not in raw
    for line in raw.split("\n"):
        if line.strip():
            json.loads(line)
    assert len(_raw_lines(serve)) == 2


def test_a_final_line_without_its_newline_is_a_torn_write(serve, open_ledger):
    led = open_ledger(serve)
    for i in range(1, 3):
        led.append(_rec(rid=f"t-{i}"))
    led.close()
    first = _envelopes(serve)[0]
    path = _segment_paths(serve)[-1]
    size = os.path.getsize(path)
    with open(path, "r+b") as fh:
        fh.truncate(size - 1)
    reopened = open_ledger(serve)
    assert reopened.head() == (1, first["hash"])
    assert reopened.append(_rec(rid="t-3")) == 2


def test_a_torn_tail_does_not_lose_the_idempotency_index(serve, open_ledger):
    led = open_ledger(serve)
    rec = _rec(kind="fill", rid="f-1", qty=3)
    led.append(dict(rec))
    led.close()
    with open(_segment_paths(serve)[-1], "ab") as fh:
        fh.write(b'{"kind":"fill","id":"f-1"')
    reopened = open_ledger(serve)
    assert reopened.append(dict(rec)) == 1
    with pytest.raises(ProductionError):
        reopened.append(_rec(kind="fill", rid="f-1", qty=4))


# ---------------------------------------------------------------------------
# Rotation and segment continuity (§4.1 placement.rotate)
# ---------------------------------------------------------------------------


def _assert_continuous(serve, led, expected_records):
    envs = _envelopes(serve)
    assert [e["seq"] for e in envs] == list(range(1, expected_records + 1))
    prev = GENESIS_PREV
    for env in envs:
        assert env["prev_hash"] == prev
        prev = env["hash"]
    assert led.head() == (envs[-1]["seq"], envs[-1]["hash"])
    assert led.verify() is None
    assert [e["seq"] for e in led.scan()] == list(range(1, expected_records + 1))


def test_segments_are_named_ledger_nnnn_jsonl(serve, open_ledger):
    led = open_ledger(serve, rotate={"by": "size", "max_bytes": 256})
    for i in range(1, 7):
        led.append(_rec(rid=f"t-{i}", filler="x" * 200))
    names = [os.path.basename(p) for p in _segment_paths(serve)]
    assert names[0] == "ledger.0001.jsonl"
    assert names == [f"ledger.{i:04d}.jsonl" for i in range(1, len(names) + 1)]


def test_rotate_by_size_rolls_and_keeps_the_chain_continuous(serve, open_ledger):
    led = open_ledger(serve, rotate={"by": "size", "max_bytes": 256})
    for i in range(1, 7):
        led.append(_rec(rid=f"t-{i}", filler="x" * 200))
    assert len(_segment_paths(serve)) >= 3
    _assert_continuous(serve, led, 6)


def test_the_first_record_of_a_new_segment_chains_from_the_prior_segment(
    serve, open_ledger
):
    led = open_ledger(serve, rotate={"by": "size", "max_bytes": 256})
    for i in range(1, 7):
        led.append(_rec(rid=f"t-{i}", filler="x" * 200))
    prev_tail = None
    for path in _segment_paths(serve):
        with open(path, encoding="utf-8") as fh:
            envs = [json.loads(ln) for ln in fh.read().split("\n") if ln.strip()]
        assert envs
        if prev_tail is not None:
            assert envs[0]["prev_hash"] == prev_tail["hash"]
            assert envs[0]["seq"] == prev_tail["seq"] + 1
        prev_tail = envs[-1]


def test_rotate_by_day_rolls_when_the_utc_day_changes(serve, open_ledger):
    base = int(
        datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    clock = FakeClock(base)
    led = open_ledger(serve, rotate={"by": "day"}, clock=clock)
    led.append(_rec(rid="t-1"))
    clock.advance(3_600_000)
    led.append(_rec(rid="t-2"))
    assert len(_segment_paths(serve)) == 1
    clock.advance(24 * 3_600_000)
    led.append(_rec(rid="t-3"))
    assert len(_segment_paths(serve)) == 2
    _assert_continuous(serve, led, 3)


def test_rotate_by_process_rolls_on_each_open(serve, open_ledger):
    led = open_ledger(serve, rotate={"by": "process"})
    led.append(_rec(rid="t-1"))
    led.append(_rec(rid="t-2"))
    assert len(_segment_paths(serve)) == 1
    led.close()
    reopened = open_ledger(serve, rotate={"by": "process"}, process_id="proc-b")
    reopened.append(_rec(rid="t-3"))
    assert len(_segment_paths(serve)) == 2
    _assert_continuous(serve, reopened, 3)


def test_the_default_rotation_does_not_roll_a_segment_on_reopen(serve, open_ledger):
    led = open_ledger(serve)
    led.append(_rec(rid="t-1"))
    led.close()
    reopened = open_ledger(serve)
    reopened.append(_rec(rid="t-2"))
    assert len(_segment_paths(serve)) == 1
    _assert_continuous(serve, reopened, 2)


def test_every_vocab_rotate_mode_is_accepted(tmp_path, open_ledger):
    for i, by in enumerate(vocab.ROTATE_BY):
        rotate = {"by": by}
        if by == "size":
            rotate["max_bytes"] = 4096
        sr = ServeRoot(str(tmp_path / f"r{i}"), SERIES)
        led = open_ledger(sr, rotate=rotate)
        assert led.append(_rec(rid="t-1")) == 1


def test_an_unknown_rotate_by_refuses(serve, open_ledger):
    with pytest.raises(ProductionError) as exc:
        open_ledger(serve, rotate={"by": "fortnight"})
    assert "fortnight" in str(exc.value)


def test_rotate_by_size_requires_max_bytes(serve, open_ledger):
    with pytest.raises(ProductionError):
        open_ledger(serve, rotate={"by": "size"})


def test_an_unknown_rotate_key_refuses(serve, open_ledger):
    with pytest.raises(ProductionError):
        open_ledger(serve, rotate={"by": "day", "every": 2})


# ---------------------------------------------------------------------------
# verify() locates damage (§8)
# ---------------------------------------------------------------------------


def _four_records(serve, open_ledger, **kw):
    led = open_ledger(serve, **kw)
    for i in range(1, 5):
        led.append(_rec(rid=f"t-{i}", note=f"n{i}"))
    led.close()
    return led


def test_verify_returns_none_on_an_intact_chain(serve, open_ledger):
    _four_records(serve, open_ledger)
    led = open_ledger(serve)
    assert led.verify() is None


def test_verify_locates_an_edited_line(serve, open_ledger):
    _four_records(serve, open_ledger)
    path = _segment_paths(serve)[0]
    lines = _raw_lines(serve)
    env = json.loads(lines[2])
    env["note"] = "tampered"
    lines[2] = json.dumps(env, sort_keys=True, separators=(",", ":"))
    _rewrite(path, lines)
    led = open_ledger(serve)
    assert led.verify() == 3


def test_verify_locates_a_deleted_line(serve, open_ledger):
    _four_records(serve, open_ledger)
    path = _segment_paths(serve)[0]
    lines = _raw_lines(serve)
    del lines[2]
    _rewrite(path, lines)
    led = open_ledger(serve)
    assert led.verify() == 3


def test_verify_locates_an_inserted_line(serve, open_ledger):
    _four_records(serve, open_ledger)
    path = _segment_paths(serve)[0]
    lines = _raw_lines(serve)
    lines.insert(2, lines[1])
    _rewrite(path, lines)
    led = open_ledger(serve)
    assert led.verify() == 3


def test_verify_locates_a_reordered_pair(serve, open_ledger):
    _four_records(serve, open_ledger)
    path = _segment_paths(serve)[0]
    lines = _raw_lines(serve)
    lines[1], lines[2] = lines[2], lines[1]
    _rewrite(path, lines)
    led = open_ledger(serve)
    assert led.verify() == 2


def test_verify_locates_a_forged_prev_hash(serve, open_ledger):
    _four_records(serve, open_ledger)
    path = _segment_paths(serve)[0]
    lines = _raw_lines(serve)
    env = json.loads(lines[1])
    env["prev_hash"] = "f" * 64
    lines[1] = json.dumps(env, sort_keys=True, separators=(",", ":"))
    _rewrite(path, lines)
    led = open_ledger(serve)
    assert led.verify() == 2


def test_verify_walks_every_segment(serve, open_ledger):
    led = open_ledger(serve, rotate={"by": "size", "max_bytes": 256})
    for i in range(1, 7):
        led.append(_rec(rid=f"t-{i}", filler="x" * 200))
    led.close()
    paths = _segment_paths(serve)
    assert len(paths) >= 3
    victim = paths[-1]
    with open(victim, encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().split("\n") if ln.strip()]
    env = json.loads(lines[0])
    bad_seq = env["seq"]
    env["filler"] = "tampered"
    lines[0] = json.dumps(env, sort_keys=True, separators=(",", ":"))
    _rewrite(victim, lines)
    reopened = open_ledger(serve)
    assert reopened.verify() == bad_seq


def test_verify_accepts_a_line_carrying_an_unknown_envelope_field(serve, open_ledger):
    """Readers tolerate unknown fields; the hash covered them (§5.8)."""
    led = open_ledger(serve)
    led.append(_rec(rid="t-1"))
    led.close()
    prior = _envelopes(serve)[-1]
    env = {
        "kind": "tick_start",
        "id": "hand-1",
        "payload_digest": canonical_hash({"kind": "tick_start", "id": "hand-1"}),
        "seq": prior["seq"] + 1,
        "series_id": SERIES,
        "process_id": PROCESS,
        "release_hash": RELEASE,
        "recorded_at_ms": prior["recorded_at_ms"] + 1,
        "schema_version": prior["schema_version"],
        "prev_hash": prior["hash"],
        "future_field": "written by a newer schema",
    }
    env["hash"] = record_hash(prior["hash"], env)
    with open(_segment_paths(serve)[-1], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(env, sort_keys=True, separators=(",", ":")) + "\n")

    reopened = open_ledger(serve)
    assert reopened.verify() is None
    assert reopened.head() == (env["seq"], env["hash"])
    scanned = list(reopened.scan())[-1]
    assert scanned["future_field"] == "written by a newer schema"
    assert reopened.append(_rec(rid="t-2")) == env["seq"] + 1
    assert _envelopes(serve)[-1]["prev_hash"] == env["hash"]


# ---------------------------------------------------------------------------
# Durability policy and the barrier (D13)
# ---------------------------------------------------------------------------


def test_fsync_every_syncs_the_file_on_every_append(serve, open_ledger, monkeypatch):
    led = open_ledger(serve, fsync="every")
    counts = _fsync_counts(monkeypatch)
    seen = 0
    for i in range(1, 6):
        led.append(_rec(rid=f"t-{i}"))
        assert counts["file"] > seen
        seen = counts["file"]
    assert counts["file"] >= 5


def test_fsync_batch_holds_until_n_records(serve, open_ledger, monkeypatch):
    led = open_ledger(serve, fsync={"batch": {"n": 3, "ms": 1_000_000}})
    counts = _fsync_counts(monkeypatch)
    led.append(_rec(rid="t-1"))
    led.append(_rec(rid="t-2"))
    assert counts["file"] == 0
    led.append(_rec(rid="t-3"))
    assert counts["file"] >= 1


def test_fsync_batch_flushes_when_ms_elapses(serve, open_ledger, monkeypatch):
    clock = FakeClock(1_000_000)
    led = open_ledger(serve, fsync={"batch": {"n": 1000, "ms": 200}}, clock=clock)
    counts = _fsync_counts(monkeypatch)
    led.append(_rec(rid="t-1"))
    assert counts["file"] == 0
    clock.advance(250)
    led.append(_rec(rid="t-2"))
    assert counts["file"] >= 1


def test_fsync_none_never_syncs_the_file_on_append(serve, open_ledger, monkeypatch):
    led = open_ledger(serve, fsync="none")
    counts = _fsync_counts(monkeypatch)
    for i in range(1, 6):
        led.append(_rec(rid=f"t-{i}"))
    assert counts["file"] == 0


def test_barrier_syncs_regardless_of_policy(serve, open_ledger, monkeypatch):
    for i, mode in enumerate(("none", {"batch": {"n": 1000, "ms": 1_000_000}})):
        led = open_ledger(serve, fsync=mode)
        counts = _fsync_counts(monkeypatch)
        led.append(_rec(rid=f"t-{i}"))
        assert counts["file"] == 0
        led.barrier()
        assert counts["file"] >= 1
        led.close()


def test_barrier_resets_the_batch_so_the_next_records_are_counted_afresh(
    serve, open_ledger, monkeypatch
):
    led = open_ledger(serve, fsync={"batch": {"n": 3, "ms": 1_000_000}})
    counts = _fsync_counts(monkeypatch)
    led.append(_rec(rid="t-1"))
    led.append(_rec(rid="t-2"))
    led.barrier()
    after_barrier = counts["file"]
    assert after_barrier >= 1
    led.append(_rec(rid="t-3"))
    led.append(_rec(rid="t-4"))
    assert counts["file"] == after_barrier


def test_a_segment_creation_reaches_the_directory(serve, open_ledger, monkeypatch):
    led = open_ledger(serve, fsync="none", rotate={"by": "size", "max_bytes": 256})
    counts = _fsync_counts(monkeypatch)
    for i in range(1, 5):
        led.append(_rec(rid=f"t-{i}", filler="x" * 200))
    led.barrier()
    assert len(_segment_paths(serve)) >= 2
    assert counts["dir"] >= 1


def test_every_vocab_fsync_mode_is_accepted(tmp_path, open_ledger):
    modes = {
        "every": "every",
        "none": "none",
        "batch": {"batch": {"n": 5, "ms": 100}},
    }
    assert set(vocab.FSYNC_MODES) == set(modes)
    for i, name in enumerate(sorted(modes)):
        sr = ServeRoot(str(tmp_path / f"f{i}"), SERIES)
        led = open_ledger(sr, fsync=modes[name])
        assert led.append(_rec(rid="t-1")) == 1


def test_an_unknown_fsync_mode_refuses(serve, open_ledger):
    with pytest.raises(ProductionError) as exc:
        open_ledger(serve, fsync="sometimes")
    assert "sometimes" in str(exc.value)


def test_a_batch_policy_missing_its_knobs_refuses(serve, open_ledger):
    with pytest.raises(ProductionError):
        open_ledger(serve, fsync={"batch": {"n": 5}})


def test_the_default_durability_is_the_safest_one(serve, open_ledger, monkeypatch):
    led = open_ledger(serve)
    counts = _fsync_counts(monkeypatch)
    led.append(_rec(rid="t-1"))
    assert counts["file"] >= 1


# ---------------------------------------------------------------------------
# Writer lock (§5.8 — the serving process is the sole ledger writer)
# ---------------------------------------------------------------------------


def test_the_writer_lock_is_serve_lock_in_the_series_root(serve, open_ledger):
    open_ledger(serve)
    assert os.path.exists(serve.lock_path)


def test_a_second_writer_on_the_same_series_refuses(serve, open_ledger):
    open_ledger(serve)
    with pytest.raises(ProductionError) as exc:
        open_ledger(serve, process_id="proc-b")
    assert "lock" in str(exc.value).lower()


def test_closing_releases_the_writer_lock(serve, open_ledger):
    first = open_ledger(serve)
    first.append(_rec(rid="t-1"))
    first.close()
    second = open_ledger(serve, process_id="proc-b")
    assert second.append(_rec(rid="t-2")) == 2


def test_a_second_writer_in_another_process_refuses(serve, open_ledger):
    open_ledger(serve)
    code = (
        "from dskit.production.ledger import JsonlLedger, ServeRoot\n"
        "from dskit.production.base import ProductionError\n"
        "import sys\n"
        "class C:\n"
        "    def now_ms(self):\n"
        "        return 1\n"
        "    def monotonic(self):\n"
        "        return 1.0\n"
        f"sr = ServeRoot({os.path.dirname(serve.series_path)!r}, {SERIES!r})\n"
        "try:\n"
        f"    JsonlLedger(sr, 'proc-child', {RELEASE!r}, clock=C())\n"
        "except ProductionError:\n"
        "    sys.exit(7)\n"
        "sys.exit(0)\n"
    )
    proc = _child(code)
    assert proc.returncode == 7, proc.stderr


# ---------------------------------------------------------------------------
# Snapshots (§6 `snapshot` record)
# ---------------------------------------------------------------------------


def test_snapshot_appends_a_snapshot_record_naming_the_head_it_projects(
    serve, open_ledger
):
    led = open_ledger(serve)
    led.append(_rec(rid="t-1"))
    led.append(_rec(rid="t-2"))
    view = {"positions": {"AAA": "3"}, "breaker": "active"}
    seq = led.snapshot(view)
    assert seq == 3
    env = _envelopes(serve)[-1]
    assert env["kind"] == "snapshot"
    assert env["seq"] == 3
    assert env["at_seq"] == 2
    assert env["state"] == view
    assert env["state_digest"] == canonical_hash(view)
    assert isinstance(env["id"], str) and env["id"]


def test_latest_snapshot_returns_the_last_one_after_reopen(serve, open_ledger):
    led = open_ledger(serve)
    led.append(_rec(rid="t-1"))
    led.snapshot({"n": 1})
    led.append(_rec(rid="t-2"))
    led.snapshot({"n": 2})
    led.close()
    reopened = open_ledger(serve)
    latest = reopened.latest_snapshot()
    assert latest["state"] == {"n": 2}
    assert latest["at_seq"] == 3
    assert reopened.verify() is None


def test_snapshot_every_n_records_appends_one_automatically(serve, open_ledger):
    state = RecordingState()
    led = open_ledger(serve, state=state, snapshot_every=3)
    for i in range(1, 4):
        led.append(_rec(rid=f"t-{i}"))
    kinds = [e["kind"] for e in _envelopes(serve)]
    assert kinds == ["tick_start", "tick_start", "tick_start", "snapshot"]
    assert _envelopes(serve)[-1]["at_seq"] == 3
    assert led.head()[0] == 4


def test_snapshot_every_requires_a_state_to_project(serve, open_ledger):
    with pytest.raises(ProductionError):
        open_ledger(serve, snapshot_every=3)


def test_the_snapshot_cadence_default_is_named_once_and_appends_nothing(
    serve, open_ledger
):
    """One named default (no document knob), and it is off — a stateless
    ledger must construct, which it could not if the default asked for a
    projection there is no `state` to supply."""
    from dskit.production.ledger import DEFAULT_SNAPSHOT_EVERY

    assert DEFAULT_SNAPSHOT_EVERY is None
    led = open_ledger(serve)
    for i in range(1, 6):
        led.append(_rec(rid=f"t-{i}"))
    assert [e["kind"] for e in _envelopes(serve)] == ["tick_start"] * 5
    assert led.latest_snapshot() is None


def test_a_snapshot_record_is_folded_like_any_other(serve, open_ledger):
    state = RecordingState()
    led = open_ledger(serve, state=state)
    led.append(_rec(rid="t-1"))
    led.snapshot({"n": 1})
    assert [r["kind"] for r in state.applied] == ["tick_start", "snapshot"]


# ---------------------------------------------------------------------------
# Checkpoint — the head-bound cache (§5.8, D15)
# ---------------------------------------------------------------------------


def _checkpoint(head_seq, head_hash, **kw):
    args = {
        "release_hash": RELEASE,
        "last_tick_at": 1_700_000_000_000,
        "last_completed_tick_at": 1_700_000_000_000,
        "pending": ["ref-1"],
        "positions_snapshot_at": 1_700_000_000_000,
        "schema_version": 1,
        "head_seq": head_seq,
        "head_hash": head_hash,
    }
    args.update(kw)
    return Checkpoint(**args)


def test_a_checkpoint_round_trips_through_write_and_load(tmp_path):
    path = str(tmp_path / "checkpoint.json")
    cp = _checkpoint(2, "b" * 64)
    cp.write(path)
    back = Checkpoint.load(path)
    assert back.release_hash == RELEASE
    assert back.head_seq == 2
    assert back.head_hash == "b" * 64
    assert back.last_tick_at == 1_700_000_000_000
    assert back.last_completed_tick_at == 1_700_000_000_000
    assert back.positions_snapshot_at == 1_700_000_000_000
    assert list(back.pending) == ["ref-1"]
    assert back.schema_version == 1


def test_a_checkpoint_write_leaves_no_temp_file_behind(tmp_path):
    path = str(tmp_path / "checkpoint.json")
    _checkpoint(1, "c" * 64).write(path)
    assert os.listdir(str(tmp_path)) == ["checkpoint.json"]


def test_a_checkpoint_write_replaces_the_previous_one_wholesale(tmp_path):
    path = str(tmp_path / "checkpoint.json")
    _checkpoint(1, "c" * 64).write(path)
    _checkpoint(2, "d" * 64).write(path)
    assert Checkpoint.load(path).head_seq == 2


def test_a_checkpoint_at_the_ledger_head_is_current(serve, open_ledger):
    led = open_ledger(serve)
    for i in range(1, 4):
        led.append(_rec(rid=f"t-{i}"))
    seq, head = led.head()
    assert _checkpoint(seq, head).validate_against(led) == CURRENT


def test_a_checkpoint_behind_the_ledger_head_is_stale(serve, open_ledger):
    led = open_ledger(serve)
    for i in range(1, 4):
        led.append(_rec(rid=f"t-{i}"))
    second = _envelopes(serve)[1]
    assert _checkpoint(second["seq"], second["hash"]).validate_against(led) == STALE


def test_a_genesis_checkpoint_against_a_written_ledger_is_stale(serve, open_ledger):
    led = open_ledger(serve)
    led.append(_rec(rid="t-1"))
    assert _checkpoint(0, GENESIS_PREV).validate_against(led) == STALE


def test_a_checkpoint_ahead_of_the_ledger_refuses(serve, open_ledger):
    led = open_ledger(serve)
    led.append(_rec(rid="t-1"))
    with pytest.raises(ProductionError):
        _checkpoint(9, "e" * 64).validate_against(led)


def test_a_checkpoint_that_diverges_at_its_own_seq_refuses(serve, open_ledger):
    led = open_ledger(serve)
    for i in range(1, 4):
        led.append(_rec(rid=f"t-{i}"))
    with pytest.raises(ProductionError):
        _checkpoint(2, "f" * 64).validate_against(led)


def test_a_genesis_checkpoint_with_a_wrong_hash_refuses(serve, open_ledger):
    led = open_ledger(serve)
    led.append(_rec(rid="t-1"))
    with pytest.raises(ProductionError):
        _checkpoint(0, "0" * 63 + "1").validate_against(led)


def test_a_crash_before_replace_leaves_the_previous_checkpoint_readable(tmp_path):
    path = str(tmp_path / "checkpoint.json")
    _checkpoint(1, "c" * 64).write(path)
    code = (
        "import os, pathlib\n"
        "from dskit.production.ledger import Checkpoint\n"
        "_die = lambda *a, **k: os._exit(9)\n"
        "os.replace = _die\n"
        "os.rename = _die\n"
        "pathlib.Path.replace = _die\n"
        "pathlib.Path.rename = _die\n"
        "cp = Checkpoint(\n"
        f"    release_hash={RELEASE!r}, last_tick_at=2, last_completed_tick_at=2,\n"
        "    pending=[], positions_snapshot_at=2, schema_version=1,\n"
        "    head_seq=2, head_hash='d' * 64)\n"
        f"cp.write({path!r})\n"
        "os._exit(0)\n"
    )
    proc = _child(code)
    assert proc.returncode == 9, proc.stderr
    survivor = Checkpoint.load(path)
    assert survivor.head_seq == 1
    assert survivor.head_hash == "c" * 64


# ---------------------------------------------------------------------------
# Crash mid-batch — the records already written are still there (D15)
# ---------------------------------------------------------------------------


def test_a_process_killed_mid_batch_leaves_a_verifiable_chain(serve, open_ledger):
    code = (
        "import os\n"
        "from dskit.production.ledger import JsonlLedger, ServeRoot\n"
        "class C:\n"
        "    def now_ms(self):\n"
        "        return 1700000000000\n"
        "    def monotonic(self):\n"
        "        return 1.0\n"
        f"sr = ServeRoot({os.path.dirname(serve.series_path)!r}, {SERIES!r})\n"
        f"led = JsonlLedger(sr, 'proc-child', {RELEASE!r}, clock=C(),\n"
        "                  fsync={'batch': {'n': 1000, 'ms': 1000000}})\n"
        "for i in range(3):\n"
        "    led.append({'kind': 'tick_start', 'id': 't-%d' % i})\n"
        "os._exit(9)\n"
    )
    proc = _child(code)
    assert proc.returncode == 9, proc.stderr
    led = open_ledger(serve)
    assert led.verify() is None
    assert led.head()[0] == 3
    assert [e["id"] for e in led.scan()] == ["t-0", "t-1", "t-2"]
    assert led.append(_rec(rid="t-3")) == 4


# ---------------------------------------------------------------------------
# The final head the journal row anchors (D15, D22)
# ---------------------------------------------------------------------------


def test_head_reflects_the_process_stop_record_the_journal_row_renders(
    serve, open_ledger
):
    led = open_ledger(serve)
    led.append(
        _rec(kind="process", rid="p-start", event="start", series_id_note=SERIES)
    )
    led.append(_rec(kind="tick_start", rid="t-1"))
    stop_seq = led.append(_rec(kind="process", rid="p-stop", event="stop", exit_code=0))
    led.barrier()
    stop = _envelopes(serve)[-1]
    assert stop["kind"] == "process"
    assert stop["event"] == "stop"
    assert stop["event"] in vocab.PROCESS_EVENTS
    assert led.head() == (stop_seq, stop["hash"])
    led.close()
    reopened = open_ledger(serve)
    assert reopened.head() == (stop_seq, stop["hash"])
    assert reopened.verify() is None
