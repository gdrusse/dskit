"""The arrival seam against the fold: an order in transport, and a restart (ADR-0161).

`test_executor.py` proves what `ArrivalPaperExecutor` answers. This module
proves the other half of the audit's fourth clause — "restart mid-flight" —
which is not an executor behaviour at all: it is whether the machinery the
fold already ships can carry an order that is neither sent nor settled.

It can, and none of it is new. `SeriesState` records an `intent` as a
`pending` order; `StateView.pending` is "client refs whose intent has no
`order_event` yet"; `Recovery.run` resolves each of them through
`executor.order(ref)` and never submits or cancels; `Reconciler._orders`
resolves the same refs the same way. What was missing was a venue that could
ANSWER: a synchronous one has no state between send and fill, so every
recovering process had to record `unknown`.

The collaborators come from the suites that own them — `test_state.py` for
the ledger and recovery fakes, `test_reconcile.py` for the reconciler, and
`test_executor.py` for the venue builders — rather than being restated here,
because a second copy of a fake drifts from the one it was copied from.
"""

import dataclasses
from decimal import Decimal

import pytest

from dskit.production.executor import ArrivalPaperExecutor, PaperExecutor
from dskit.production.state import Recovery, SeriesState
from dskit.production.vocab import TERMINAL_STATUSES
from dskit.production.clock import TestClock
from tests.production.test_breaker import (
    halt,
    make_breaker,
    order_event_body as breaker_order_event_body,
    outcome_of,
)
from tests.production.test_breaker import intent_body as breaker_intent_body
from tests.production.test_executor import (
    ARRIVAL_MS,
    ARRIVAL_ASK,
    BOOK_AT_ARRIVAL,
    BOOK_AT_DECISION,
    INSTRUMENT,
    NOW_MS,
    TRANSPORT_PARAMS,
    book_at,
    order_at,
    simulated,
    tick_state,
)
from tests.production.test_reconcile import (
    SCOPE,
    fold,
    make_reconciler,
    seed_pending,
)
from tests.production.test_reconcile import intent_body as reconcile_intent_body
from tests.production.test_reconcile import order_event_body as reconcile_order_event_body
from tests.production.test_state import (
    FakeClock,
    FakeIdSource,
    FakeLedger,
    SERIES_ID,
)
from tests.production.test_state import intent_body as state_intent_body

REF = "ref-1"


class CountingVenue(ArrivalPaperExecutor):
    """The arrival venue, counting the two verbs recovery must never use."""

    def __init__(self, params=None, *, clock, scope=None):
        super().__init__(params, clock=clock, scope=scope)
        self.sends = 0
        self.cancels = 0

    def submit(self, intent, permit, state):
        self.sends += 1
        return super().submit(intent, permit, state)

    def cancel(self, ref):
        self.cancels += 1
        return super().cancel(ref)


def crashed_after_send():
    """A fold that recorded the intent and a venue holding the order in transport.

    Returns
    -------
    tuple
        ``(ledger, state, venue, clock)`` — the process died between
        `submit` returning `pending` and the acknowledgement being folded,
        which is the window `StateView.pending` names.
    """
    ledger = FakeLedger(series_id=SERIES_ID)
    state = SeriesState(SERIES_ID)
    ledger.state = state
    ledger.append(
        {
            "kind": "intent",
            "id": f"in-{REF}",
            "body": state_intent_body(client_ref=REF, instrument=INSTRUMENT),
        }
    )
    clock = TestClock(start_ms=NOW_MS)
    venue = CountingVenue(TRANSPORT_PARAMS, clock=clock)
    venue.on_quote(BOOK_AT_DECISION)
    venue.submit(order_at(REF), simulated(REF), tick_state())
    return ledger, state, venue, clock


def recovered():
    """Run `Recovery` over `crashed_after_send()` and return every collaborator."""
    ledger, state, venue, clock = crashed_after_send()
    report = Recovery(ledger, state, FakeIdSource(), venue).run(FakeClock(NOW_MS + 1))
    return ledger, state, venue, clock, report


def order_events(ledger):
    """The `order_event` bodies the ledger holds, in chain order."""
    return [record["body"] for record in ledger.records if record["kind"] == "order_event"]


# ---------------------------------------------------------------------------
# The window the fold already names
# ---------------------------------------------------------------------------


def test_an_order_in_transport_sits_in_the_folds_own_pending_slot():
    """§5.8.1's `pending` is exactly this window, and it existed before this
    seam did — what is new is that the venue can be asked about it."""
    _ledger, state, venue, _clock = crashed_after_send()
    assert state.snapshot().pending == (REF,)
    assert venue.order(REF).status == "pending"


def test_recovery_resolves_the_in_flight_ref_by_asking_and_never_by_resending():
    """"An unknown outcome is resolved by querying, never by resending."
    `Recovery` calls one verb; a venue answering `pending` is what makes that
    rule usable for an order still in transport."""
    ledger, _state, venue, _clock, report = recovered()
    assert report.queried_refs == (REF,)
    assert [body["status"] for body in order_events(ledger)] == ["pending"]
    assert (venue.sends, venue.cancels) == (1, 0)
    assert venue.fills(0) == ((), None)


def test_the_recovered_order_is_working_rather_than_terminalised():
    """`pending` is deliberately not in `TERMINAL_STATUSES`, so the recovered
    fold carries the order forward instead of closing an order that is still
    on its way to the venue."""
    _ledger, state, _venue, _clock, _report = recovered()
    view = state.snapshot()
    assert view.pending == ()
    assert view.working[REF].status == "pending"
    assert view.working[REF].status not in TERMINAL_STATUSES


def test_the_order_still_lands_after_the_restart_queried_it():
    """Querying an order must not consume it: the arrival still happens, on
    the book of its own instant, and the fill is the one the audit's sequence
    asks for."""
    _ledger, _state, venue, clock, _report = recovered()
    clock.advance(ARRIVAL_MS)
    venue.on_quote(BOOK_AT_ARRIVAL)
    landed = venue.order(REF)
    assert landed is not None
    assert (landed.status, landed.avg_price) == ("filled", ARRIVAL_ASK)
    assert (venue.sends, venue.cancels) == (1, 0)


def test_a_query_after_a_long_outage_never_fabricates_a_fill():
    """The restart case as it actually happens: downtime longer than the
    order's latency, and no quote since the crash. A read that landed
    transport would price the order against the last PRE-CRASH book and
    `Recovery` would append that fill to the chain as fact, though no quote
    ever said the market was still there. `pending` is the only honest
    answer, and it is the one the fold already knows how to carry."""
    ledger, state, venue, clock = crashed_after_send()
    clock.advance(10 * ARRIVAL_MS)
    Recovery(ledger, state, FakeIdSource(), venue).run(FakeClock(NOW_MS + 10 * ARRIVAL_MS))
    assert [body["status"] for body in order_events(ledger)] == ["pending"]
    assert venue.fills(0) == ((), None)
    assert (venue.sends, venue.cancels) == (1, 0)


@pytest.mark.parametrize("downtime", (0, ARRIVAL_MS - 1, ARRIVAL_MS, 10 * ARRIVAL_MS))
def test_two_restarts_of_different_duration_record_the_same_ledger(downtime):
    """The determinism the replay-parity claim rests on: how long the process
    was down is not part of the tape, so it must not be part of what the
    chain says happened."""
    def restart(after_ms):
        ledger, state, venue, clock = crashed_after_send()
        clock.advance(after_ms)
        Recovery(ledger, state, FakeIdSource(), venue).run(FakeClock(NOW_MS + after_ms))
        return [
            {key: value for key, value in body.items() if key != "recv_at_ms"}
            for body in order_events(ledger)
        ]

    assert restart(downtime) == restart(0)


# ---------------------------------------------------------------------------
# A halt reaches an order in transport, and says so truthfully
# ---------------------------------------------------------------------------


def halting_series(tmp_path):
    """A breaker over one resting order and one still in transport."""
    clock = TestClock(start_ms=NOW_MS)
    venue = ArrivalPaperExecutor({"latency_ms": {"submit": 100, "cancel": 5}}, clock=clock)
    venue.on_quote(BOOK_AT_DECISION)
    venue.submit(order_at("resting", limit="99.00"), simulated("resting"), tick_state())
    clock.advance(100)
    venue.on_quote(dataclasses.replace(BOOK_AT_DECISION, asof_ms=NOW_MS + 100))
    venue.submit(order_at("transport", limit="99.50"), simulated("transport"),
                 tick_state())
    breaker, ledger, state, _clock, _policy, _root = make_breaker(tmp_path, executor=venue)
    for ref, status in (("resting", "open"), ("transport", "pending")):
        ledger.append({"kind": "intent", "id": f"intent-{ref}", "body": breaker_intent_body(ref)})
        ledger.append({"kind": "order_event", "id": f"oe-{ref}",
                       "body": breaker_order_event_body(ref, status)})
    return venue, clock, breaker, ledger, state


def test_a_halt_that_reports_submitted_leaves_nothing_able_to_fill(tmp_path):
    """The whole path, not the primitive: halt -> `cancel_working` ->
    `executor.cancel_all` -> `cancel_outcome`. `submitted` is the fully
    successful signal, so an order that survives a `submitted` halt and goes
    on to fill is the kill switch reporting all-clear over a live position."""
    venue, clock, breaker, ledger, state = halting_series(tmp_path)
    assert set(state.snapshot().working) == {"resting", "transport"}
    halt(breaker)
    assert outcome_of(ledger) == "submitted"
    clock.advance(200)
    venue.on_quote(book_at(NOW_MS + 300, "98.50"))
    assert venue.fills(0) == ((), None)
    assert venue.order("transport").status == "cancelled"
    assert venue.order("resting").status == "cancelled"


def test_a_venue_that_cannot_say_leaves_the_outcome_unknown():
    """The answer before this seam, kept as the contrast: a venue with no
    state between send and fill has nothing to return for a ref in transport,
    and `Recovery` correctly records the ambiguity rather than a guess."""
    ledger = FakeLedger(series_id=SERIES_ID)
    state = SeriesState(SERIES_ID)
    ledger.state = state
    ledger.append(
        {
            "kind": "intent",
            "id": f"in-{REF}",
            "body": state_intent_body(client_ref=REF, instrument=INSTRUMENT),
        }
    )
    venue = PaperExecutor(TRANSPORT_PARAMS, clock=TestClock(start_ms=NOW_MS))
    Recovery(ledger, state, FakeIdSource(), venue).run(FakeClock(NOW_MS + 1))
    assert [body["status"] for body in order_events(ledger)] == ["unknown"]


# ---------------------------------------------------------------------------
# Reconciliation sees no break where there is only transport
# ---------------------------------------------------------------------------


def test_a_pending_ref_still_in_transport_is_resolved_rather_than_broken():
    """§5.9 resolves every pending ref through `executor.order(ref)` and
    places the answer on BOTH sides, so an order the venue has not yet
    received must not read as a divergence between the fold and the venue."""
    reconciler, ledger, state, clock = make_reconciler()
    seed_pending(ledger, REF)
    venue = ArrivalPaperExecutor(TRANSPORT_PARAMS, clock=clock, scope=SCOPE)
    venue.submit(order_at(REF), simulated(REF), tick_state())
    report = reconciler.run(state.snapshot(), venue, SCOPE)
    assert report.breaks == ()


def test_an_acked_order_still_in_transport_is_not_missing_at_the_venue():
    """The SECOND member of the halt sweep's defect family, found by auditing
    every generic consumer of `open_orders()`. Once the `pending` ack folds
    as an `order_event` the ref leaves `StateView.pending` and enters
    `working`, and `Reconciler._orders` then compares `working` against
    `executor.open_orders()`. A venue that kept transport off that answer
    reported `missing_at_venue` — an economic break whose configured
    `on_mismatch` may halt the series — over an order nothing is wrong with.
    Answering the base contract is what fixes both consumers at once."""
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "intent", reconcile_intent_body(REF, instrument=INSTRUMENT, limit=None))
    fold(ledger, "order_event", reconcile_order_event_body(REF, "pending"))
    venue = ArrivalPaperExecutor(TRANSPORT_PARAMS, clock=clock, scope=SCOPE)
    venue.submit(order_at(REF), simulated(REF), tick_state())
    view = state.snapshot()
    assert (set(view.working), view.pending) == ({REF}, ())
    assert reconciler.run(view, venue, SCOPE).breaks == ()


# ---------------------------------------------------------------------------
# The whole acceptance sequence, end to end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("restart_first", (False, True))
def test_the_audits_acceptance_sequence_runs_whole(restart_first):
    """The page's illustrative sequence in one script, with and without a
    restart in the middle of it — "repeat with delayed/out-of-order messages
    and restart mid-flight". Passing it establishes ORDERING, not agreement
    with any venue's fills."""
    ledger, state, venue, clock = crashed_after_send()
    if restart_first:
        Recovery(ledger, state, FakeIdSource(), venue).run(FakeClock(NOW_MS + 1))
    limit = order_at("limit-ref", limit="100.02")
    venue.submit(limit, simulated("limit-ref"), tick_state())

    clock.advance(ARRIVAL_MS)
    venue.on_quote(BOOK_AT_ARRIVAL)
    venue.on_quote(BOOK_AT_DECISION)                      # a delayed duplicate
    assert dict(venue.stream_gaps()) == {INSTRUMENT: 1}
    assert venue.order(REF).avg_price == ARRIVAL_ASK      # the market buy
    assert venue.order("limit-ref").filled_qty == Decimal("0")

    venue.cancel("limit-ref")
    assert venue.order("limit-ref").status == "pending_cancel"
    clock.advance(1)
    venue.on_quote(
        BOOK_AT_ARRIVAL.__class__(
            instrument=INSTRUMENT, bid=Decimal("99.90"), ask=Decimal("100.00"),
            mid=Decimal("99.95"), asof_ms=NOW_MS + ARRIVAL_MS + 1,
        )
    )
    assert venue.order("limit-ref").status == "filled"
    assert (venue.sends, venue.cancels) == (2, 1)
