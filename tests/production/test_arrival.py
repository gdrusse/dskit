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

from decimal import Decimal

import pytest

from dskit.production.executor import ArrivalPaperExecutor, PaperExecutor
from dskit.production.state import Recovery, SeriesState
from dskit.production.vocab import TERMINAL_STATUSES
from dskit.production.clock import TestClock
from tests.production.test_executor import (
    ARRIVAL_MS,
    ARRIVAL_ASK,
    BOOK_AT_ARRIVAL,
    BOOK_AT_DECISION,
    INSTRUMENT,
    NOW_MS,
    TRANSPORT_PARAMS,
    order_at,
    simulated,
    tick_state,
)
from tests.production.test_reconcile import (
    SCOPE,
    make_reconciler,
    seed_pending,
)
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
    assert (landed.status, landed.avg_price) == ("filled", ARRIVAL_ASK)
    assert (venue.sends, venue.cancels) == (1, 0)


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
