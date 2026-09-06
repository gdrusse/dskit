"""`ids.py` — deterministic identifiers allocated before a tick runs (§5.13, D20).

D20 rests on one property: every identifier a serve process writes is a
function of *semantic* inputs — the release hash, the tick instant, the
leg index, the attempt — and of nothing else.  Not the wall clock, not a
sequence counter, not the ledger's `seq`, not the order the process
happened to ask.  That is what lets `replay` re-derive the same ids from
the tape, and what lets crash recovery query a client ref it never
stored.

So the tests here fall into four groups:

1. **The seam** — `IdSource` is an ABC whose four allocation hooks are
   `@abstractmethod` (§5.15: an incomplete subclass must fail at
   construction, not at the first live tick), while
   `flatten_client_ref` is *concrete on the base*: D12's formula has one
   owner, so every subclass answers the same ref for the same reduction.
2. **Determinism** — same inputs, same id, across instances, across call
   orders, across processes.
3. **Separation** — the four derivations are tagged (`tick-v1`,
   `leg-v1`, `plan-v1`, `client-v1`), so the same numbers under two
   methods never produce one id, and two releases never share a tick id.
4. **Independence** — no wall clock and no counter, proved twice: an AST
   check that `ids.py` imports neither `time`/`random`/`uuid` nor
   `secrets`, and a behavioural check that every method still answers
   the same value with those clocks monkeypatched to explode.

The one recipe pinned literally is `flatten_client_ref`: D12 requires a
*recovering process* — a different process, possibly a different release
— to re-derive that client ref from the ledger alone, so the byte layout
is part of the contract rather than an implementation detail.  The other
four derivations are pinned by behaviour (determinism, separation,
shape), which leaves their layout free.
"""

import ast
import pathlib
import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

import dskit
from dskit.production import ids as ids_module
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.ids import IdSource, RecordedIdSource, ReleaseIdSource

# ---------------------------------------------------------------------------
# Fixed material — constants so a hand-read expectation stays readable.
# ---------------------------------------------------------------------------

RELEASE = "a" * 64
OTHER_RELEASE = "b" * 64
TICK_ID = "c" * 64
OTHER_TICK_ID = "d" * 64
REDUCTION_DIGEST = "e" * 64
OTHER_REDUCTION_DIGEST = "f" * 64
REQUEST_ID = "3f0a1c62-0000-4000-8000-000000000001"
OTHER_REQUEST_ID = "3f0a1c62-0000-4000-8000-0000000000ff"

#: 2026-01-01T12:00:00Z, and a second instant one minute later.
T0 = 1_767_268_800_000
T1 = T0 + 60_000

MODULE_PATH = pathlib.Path(dskit.__file__).parent / "production" / "ids.py"

#: Nothing in an id may come from these: a clock, a random source, a
#: process-unique token or a wall-clock date.
FORBIDDEN_IMPORTS = ("time", "random", "uuid", "secrets", "datetime", "os")

HEX64 = set("0123456789abcdef")


def is_id(value):
    """A derived id: a 64-character lowercase hex sha256 digest."""
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX64


def all_four(source, tick_at_ms=T0, tick_id=TICK_ID, index=0, attempt=0):
    """The four allocations a tick makes, in one tuple."""
    return (
        source.next_tick_id(tick_at_ms),
        source.leg_id(tick_id, index),
        source.plan_id(tick_id, index),
        source.client_ref(tick_id, index, attempt),
    )


class MinimalIdSource(IdSource):
    """The smallest complete subclass — proves what the base leaves abstract."""

    def next_tick_id(self, tick_at_ms):
        return f"tick-{tick_at_ms}"

    def leg_id(self, tick_id, index):
        return f"leg-{tick_id}-{index}"

    def plan_id(self, tick_id, leg_index):
        return f"plan-{tick_id}-{leg_index}"

    def client_ref(self, tick_id, leg_index, attempt):
        return f"ref-{tick_id}-{leg_index}-{attempt}"


# ---------------------------------------------------------------------------
# 1. The seam (§5.13, §5.15)
# ---------------------------------------------------------------------------


def test_the_public_surface_is_the_abc_and_its_two_core_sources():
    assert set(ids_module.__all__) == {
        "IdSource",
        "ReleaseIdSource",
        "RecordedIdSource",
    }


def test_idsource_cannot_be_instantiated():
    """§5.15: a seam ABC declares its hooks abstract, so the ABC itself
    and any incomplete subclass refuse at construction."""
    with pytest.raises(TypeError):
        IdSource()


def test_the_four_allocation_hooks_are_abstract():
    assert set(IdSource.__abstractmethods__) == {
        "next_tick_id",
        "leg_id",
        "plan_id",
        "client_ref",
    }


def test_an_incomplete_subclass_refuses_to_construct():
    class Missing(IdSource):
        def next_tick_id(self, tick_at_ms):
            return "t"

        def leg_id(self, tick_id, index):
            return "l"

        def plan_id(self, tick_id, leg_index):
            return "p"

    with pytest.raises(TypeError):
        Missing()


def test_flatten_client_ref_is_concrete_on_the_base():
    """D12's formula has ONE owner: a subclass supplies the four
    allocations and inherits the flatten ref."""
    assert "flatten_client_ref" not in IdSource.__abstractmethods__
    ref = MinimalIdSource().flatten_client_ref(RELEASE, REQUEST_ID, 0, REDUCTION_DIGEST)
    assert is_id(ref)


def test_the_shipped_sources_are_idsources():
    assert isinstance(ReleaseIdSource(RELEASE), IdSource)
    assert isinstance(RecordedIdSource(()), IdSource)


# ---------------------------------------------------------------------------
# 2. Determinism — no counter, no call order, no instance identity
# ---------------------------------------------------------------------------


def test_two_instances_of_the_same_release_answer_the_same_four_ids():
    assert all_four(ReleaseIdSource(RELEASE)) == all_four(ReleaseIdSource(RELEASE))


def test_calling_next_tick_id_twice_with_one_instant_repeats_the_id():
    """Ids are allocated BEFORE `tick_start` and never from a counter
    (D20), so asking twice for the same instant is not a second tick."""
    source = ReleaseIdSource(RELEASE)
    assert source.next_tick_id(T0) == source.next_tick_id(T0)


def test_no_id_depends_on_how_many_calls_came_before_it():
    first = all_four(ReleaseIdSource(RELEASE))

    busy = ReleaseIdSource(RELEASE)
    for offset in range(10):
        busy.next_tick_id(T0 + 1_000 + offset)
        busy.leg_id(OTHER_TICK_ID, offset)
    # ... and answers the same four, asked in the reverse order.
    reversed_calls = (
        busy.client_ref(TICK_ID, 0, 0),
        busy.plan_id(TICK_ID, 0),
        busy.leg_id(TICK_ID, 0),
        busy.next_tick_id(T0),
    )
    assert first == tuple(reversed(reversed_calls))


def test_every_derived_id_is_a_lowercase_hex_sha256():
    for value in all_four(ReleaseIdSource(RELEASE)):
        assert is_id(value), value


# ---------------------------------------------------------------------------
# 3. Separation — the tags, the release, the instant, the index, the attempt
# ---------------------------------------------------------------------------


def test_the_four_derivations_are_tagged_apart():
    """`leg-v1`, `plan-v1` and `client-v1` over the same numbers must not
    collide: an id that named two things would break the identity chain
    §5.14 records (`decision_plan → intent → client_ref`)."""
    source = ReleaseIdSource(RELEASE)
    ids = {
        source.next_tick_id(T0),
        source.leg_id(TICK_ID, 0),
        source.plan_id(TICK_ID, 0),
        source.client_ref(TICK_ID, 0, 0),
        source.flatten_client_ref(RELEASE, REQUEST_ID, 0, REDUCTION_DIGEST),
    }
    assert len(ids) == 5


def test_a_tick_id_is_bound_to_the_release():
    assert ReleaseIdSource(RELEASE).next_tick_id(T0) != ReleaseIdSource(
        OTHER_RELEASE
    ).next_tick_id(T0)


def test_a_tick_id_separates_two_instants():
    source = ReleaseIdSource(RELEASE)
    assert source.next_tick_id(T0) != source.next_tick_id(T1)


@given(a=st.integers(min_value=0, max_value=2**53), b=st.integers(min_value=0, max_value=2**53))
def test_two_instants_never_share_a_tick_id(a, b):
    source = ReleaseIdSource(RELEASE)
    assert (source.next_tick_id(a) == source.next_tick_id(b)) == (a == b)


def test_leg_and_plan_ids_separate_the_index():
    source = ReleaseIdSource(RELEASE)
    assert source.leg_id(TICK_ID, 0) != source.leg_id(TICK_ID, 1)
    assert source.plan_id(TICK_ID, 0) != source.plan_id(TICK_ID, 1)


def test_leg_and_plan_ids_separate_the_tick():
    source = ReleaseIdSource(RELEASE)
    assert source.leg_id(TICK_ID, 0) != source.leg_id(OTHER_TICK_ID, 0)
    assert source.plan_id(TICK_ID, 0) != source.plan_id(OTHER_TICK_ID, 0)


def test_a_client_ref_separates_the_attempt():
    """D20: client ids derive from release/tick/leg/attempt — a retry is
    a new attempt and therefore a new ref, never a blind resend of the
    same one."""
    source = ReleaseIdSource(RELEASE)
    assert source.client_ref(TICK_ID, 0, 0) != source.client_ref(TICK_ID, 0, 1)
    assert source.client_ref(TICK_ID, 0, 0) != source.client_ref(TICK_ID, 1, 0)


def test_a_client_ref_is_bound_to_the_release():
    assert ReleaseIdSource(RELEASE).client_ref(TICK_ID, 0, 0) != ReleaseIdSource(
        OTHER_RELEASE
    ).client_ref(TICK_ID, 0, 0)


# ---------------------------------------------------------------------------
# 4. Independence — no wall clock, no randomness, no sequence
# ---------------------------------------------------------------------------


def test_the_module_imports_no_clock_no_randomness_and_no_process_token():
    """The brief's rule — nothing calls `time.time()` outside `clock.py`
    — has teeth here: an id that read the clock would replay differently
    in a second process, which is exactly the divergence D20 forbids."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & set(FORBIDDEN_IMPORTS), sorted(imported)


def test_no_allocation_reads_the_wall_clock(monkeypatch):
    source = ReleaseIdSource(RELEASE)
    before = all_four(source) + (
        source.flatten_client_ref(RELEASE, REQUEST_ID, 0, REDUCTION_DIGEST),
    )

    def explode(*args, **kwargs):
        raise AssertionError("an id source may never read the wall clock")

    for name in ("time", "time_ns", "monotonic", "monotonic_ns"):
        monkeypatch.setattr(time, name, explode)

    after = all_four(source) + (
        source.flatten_client_ref(RELEASE, REQUEST_ID, 0, REDUCTION_DIGEST),
    )
    assert before == after


# ---------------------------------------------------------------------------
# The flatten client ref — D12's one formula, one owner
# ---------------------------------------------------------------------------


def test_the_flatten_client_ref_is_the_tagged_hash_of_its_five_terms():
    """D12 pins `H("flatten-v1", release_hash, reduction_request_id,
    zero_based_intent_index, reduction_intent_digest)`, and pins it
    because a *recovering process* must re-derive the ref from the
    ledger alone before it may query or resume the reserved intent.  The
    recipe is therefore part of the contract, hashed by the one
    `base.canonical_hash` idiom every other digest uses."""
    ref = ReleaseIdSource(RELEASE).flatten_client_ref(
        RELEASE, REQUEST_ID, 3, REDUCTION_DIGEST
    )
    assert ref == canonical_hash(
        ("flatten-v1", RELEASE, REQUEST_ID, 3, REDUCTION_DIGEST)
    )


def test_the_flatten_client_ref_separates_every_term():
    source = ReleaseIdSource(RELEASE)
    base = source.flatten_client_ref(RELEASE, REQUEST_ID, 0, REDUCTION_DIGEST)
    variants = {
        base,
        source.flatten_client_ref(OTHER_RELEASE, REQUEST_ID, 0, REDUCTION_DIGEST),
        source.flatten_client_ref(RELEASE, OTHER_REQUEST_ID, 0, REDUCTION_DIGEST),
        source.flatten_client_ref(RELEASE, REQUEST_ID, 1, REDUCTION_DIGEST),
        source.flatten_client_ref(RELEASE, REQUEST_ID, 0, OTHER_REDUCTION_DIGEST),
    }
    assert len(variants) == 5


def test_the_index_separates_two_byte_identical_proposals():
    """D12: two entries whose `(instrument, side, qty, limit)` match are
    refused at signing time, and the digest itself carries `index` — so
    the ref for index 0 and index 1 differ even where everything else is
    equal."""
    source = ReleaseIdSource(RELEASE)
    assert source.flatten_client_ref(
        RELEASE, REQUEST_ID, 0, REDUCTION_DIGEST
    ) != source.flatten_client_ref(RELEASE, REQUEST_ID, 1, REDUCTION_DIGEST)


def test_the_flatten_ref_is_independent_of_the_id_source_that_derives_it():
    """One owner: a live `ReleaseIdSource`, a replaying
    `RecordedIdSource` and a bare subclass answer the same ref, and the
    recorded one consumes no tape entry doing it."""
    recorded = RecordedIdSource(())
    expected = ReleaseIdSource(RELEASE).flatten_client_ref(
        RELEASE, REQUEST_ID, 2, REDUCTION_DIGEST
    )
    assert (
        recorded.flatten_client_ref(RELEASE, REQUEST_ID, 2, REDUCTION_DIGEST)
        == expected
    )
    assert (
        MinimalIdSource().flatten_client_ref(RELEASE, REQUEST_ID, 2, REDUCTION_DIGEST)
        == expected
    )


def test_the_flatten_ref_ignores_the_release_the_source_was_built_with():
    """The release in the formula is the *reduction plan's* release, an
    argument — not the one the source happens to hold — so a recovering
    process re-derives it from the ledger row."""
    assert ReleaseIdSource(RELEASE).flatten_client_ref(
        OTHER_RELEASE, REQUEST_ID, 0, REDUCTION_DIGEST
    ) == ReleaseIdSource(OTHER_RELEASE).flatten_client_ref(
        OTHER_RELEASE, REQUEST_ID, 0, REDUCTION_DIGEST
    )


# ---------------------------------------------------------------------------
# ReleaseIdSource construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("release_hash", [None, 64, b"a" * 64, ""])
def test_a_release_id_source_refuses_a_release_hash_it_cannot_bind(release_hash):
    with pytest.raises(ProductionError):
        ReleaseIdSource(release_hash)


@pytest.mark.parametrize("bad", [-1, True, 1.0, "0", None])
def test_an_index_that_is_not_a_position_refuses(bad):
    """Every index is a zero-based POSITION: a leg's, a plan's, an
    attempt's, a reduction intent's.  A negative or non-int one is not a
    position at all, and an id derived from it would be a real id for a
    leg that cannot exist."""
    source = ReleaseIdSource(RELEASE)
    for call in (
        lambda: source.leg_id(TICK_ID, bad),
        lambda: source.plan_id(TICK_ID, bad),
        lambda: source.client_ref(TICK_ID, bad, 0),
        lambda: source.client_ref(TICK_ID, 0, bad),
        lambda: source.next_tick_id(bad),
        lambda: source.flatten_client_ref(RELEASE, REQUEST_ID, bad, REDUCTION_DIGEST),
    ):
        with pytest.raises(ProductionError):
            call()


# ---------------------------------------------------------------------------
# RecordedIdSource — the tape (D20: replay allocates nothing new)
# ---------------------------------------------------------------------------


def tape_of(source, tick_at_ms=T0, tick_id=TICK_ID, index=0, attempt=0):
    """The `(method, args, id)` triples a recorded ledger yields."""
    return (
        ("next_tick_id", (tick_at_ms,), source.next_tick_id(tick_at_ms)),
        ("leg_id", (tick_id, index), source.leg_id(tick_id, index)),
        ("plan_id", (tick_id, index), source.plan_id(tick_id, index)),
        (
            "client_ref",
            (tick_id, index, attempt),
            source.client_ref(tick_id, index, attempt),
        ),
    )


def test_the_tape_replays_every_id_exactly():
    live = ReleaseIdSource(RELEASE)
    tape = tape_of(live)
    replayed = RecordedIdSource(tape)
    assert all_four(replayed) == all_four(live)


def test_a_tape_read_back_from_json_replays_the_same_ids():
    """A recorded ledger arrives as JSON, so the triples' args are lists
    rather than tuples; the tape compares positional arguments, not
    container types."""
    live = ReleaseIdSource(RELEASE)
    tape = [[method, list(args), value] for method, args, value in tape_of(live)]
    assert all_four(RecordedIdSource(tape)) == all_four(live)


def test_the_tape_refuses_a_call_it_did_not_record():
    tape = tape_of(ReleaseIdSource(RELEASE))
    with pytest.raises(ProductionError):
        RecordedIdSource(tape).next_tick_id(T1)


def test_the_tape_refuses_a_call_made_out_of_order():
    """The tape is a sequence, not a lookup: replay that asks for a leg
    id where the recording asked for a tick id has diverged, and D20's
    parity claim is exactly that it did not."""
    tape = tape_of(ReleaseIdSource(RELEASE))
    with pytest.raises(ProductionError):
        RecordedIdSource(tape).leg_id(TICK_ID, 0)


def test_the_tape_refuses_once_exhausted():
    source = RecordedIdSource(tape_of(ReleaseIdSource(RELEASE)))
    all_four(source)
    with pytest.raises(ProductionError):
        source.next_tick_id(T0)


def test_an_empty_tape_refuses_the_first_allocation():
    with pytest.raises(ProductionError):
        RecordedIdSource(()).next_tick_id(T0)


def test_a_refused_replay_names_the_disagreement():
    tape = tape_of(ReleaseIdSource(RELEASE))
    with pytest.raises(ProductionError) as excinfo:
        RecordedIdSource(tape).next_tick_id(T1)
    assert excinfo.value.problems
    assert "next_tick_id" in str(excinfo.value)


@pytest.mark.parametrize(
    "tape",
    [
        (("next_tick_id", (T0,)),),
        (("nope", (T0,), "x" * 64),),
        (("next_tick_id", T0, "x" * 64),),
        ("next_tick_id",),
    ],
)
def test_a_malformed_tape_refuses_at_construction(tape):
    """Default-deny: a tape that cannot be replayed is a broken
    recording, and it says so before the first tick rather than at the
    call that trips over it."""
    with pytest.raises(ProductionError):
        RecordedIdSource(tape)
