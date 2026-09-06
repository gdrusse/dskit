"""`coordination.py` — the venue/account ownership domain (§5.7.2).

A serve process may only act while it holds the lease on
`document.coordination.scope`, and that scope is the canonical
`ExecutionScope{venue, account}` — **not** a release id — so an old and a
new release contend for the same domain instead of both believing they
own it. §5.7.2 requires exact equality among the actual (authenticated),
document, release, lease and `ActPermit` scopes at startup, at every tick
and at the final gate; `scope_equal` is that rule's one owner, so no
caller re-spells `a == b == c`.

`fencing_token` is monotonic, and the gateway rejects a stale one — which
is only meaningful if a *stale* token is recognisable, so
`Lease.permit_current(permit)` compares a held permit against
`current(scope)` and is the local half of that check.

Renewal runs in a supervised worker; `LeaseRenewer` is the synchronous
driver of its cadence, so the worker thread is a thin wrapper and every
timing rule here is testable without a real clock. §5.7.2's rule that
"any missed renewal deadline invalidates the local permit without
waiting for nominal expiry" is what disables submit while leaving
query/reconcile/cancel alone: after a miss there is simply no permit to
fence a submit with.

Core `ProcessLease` is in-process only and declares `LIVE_CAPABLE =
False`; a live plan resolves a child lease class. The rung itself is
never read here — `compose.py` is the only module allowed to read it.
"""

import dataclasses
import inspect

import pytest

from dskit.production.base import ProductionError
from dskit.production.coordination import (
    LEASE_KINDS,
    Lease,
    LeasePermit,
    LeaseRenewer,
    ProcessLease,
    scope_equal,
)
from dskit.production.records import ExecutionScope

# ---------------------------------------------------------------------------
# Fixed material — §4.1's `coordination` block, typed out
# ---------------------------------------------------------------------------

BASE_MS = 1_767_268_800_000

SCOPE = ExecutionScope(venue="paper", account="strategy-a")
OTHER_ACCOUNT = ExecutionScope(venue="paper", account="strategy-b")
OTHER_VENUE = ExecutionScope(venue="live-venue", account="strategy-a")

TTL_MS = 30_000
EVERY_MS = 10_000
TIMEOUT_MS = 2_000

#: Two holders that differ only by release — §5.7.2's "old and new
#: releases contend for the same ownership domain".
HOLDER_A = "release-aaaa/process-1"
HOLDER_B = "release-bbbb/process-2"

#: The four abstract hooks §5.7.2 names.
LEASE_HOOKS = ("acquire", "renew", "current", "release")


class FakeClock:
    """The two `Clock` methods coordination needs; no wall time."""

    def __init__(self, ms=BASE_MS):
        self._ms = int(ms)

    def now_ms(self):
        return self._ms

    def monotonic(self):
        return self._ms / 1000.0

    def advance(self, ms):
        self._ms += int(ms)
        return self._ms


class CountingLease(Lease):
    """A `Lease` that delegates, counts renewals and can start refusing.

    Subclassing the seam here is deliberate: it proves the ABC is
    implementable by a child with exactly the four hooks §5.7.2 names.
    """

    LIVE_CAPABLE = False

    def __init__(self, inner):
        self.inner = inner
        self.renew_calls = 0
        self.refusing = False

    def acquire(self, scope, holder, ttl_ms):
        return self.inner.acquire(scope, holder, ttl_ms)

    def renew(self, permit):
        self.renew_calls += 1
        if self.refusing:
            raise ProductionError(["lease lost"])
        return self.inner.renew(permit)

    def current(self, scope):
        return self.inner.current(scope)

    def release(self, permit):
        return self.inner.release(permit)


def new_lease(clock=None):
    """A `ProcessLease` and the clock it reads."""
    clock = clock if clock is not None else FakeClock()
    return ProcessLease({}, clock=clock), clock


def foreign_permit(**overrides):
    """A permit this test built by hand — no lease ever issued it."""
    fields = {
        "scope": SCOPE,
        "holder": HOLDER_A,
        "fencing_token": 99,
        "expires_ms": BASE_MS + TTL_MS,
    }
    fields.update(overrides)
    return LeasePermit(**fields)


# ---------------------------------------------------------------------------
# The seam — `Lease(ABC)` and its four hooks
# ---------------------------------------------------------------------------


def test_lease_is_abstract_and_names_the_four_hooks_of_5_7_2():
    assert inspect.isabstract(Lease)
    assert set(Lease.__abstractmethods__) == set(LEASE_HOOKS)
    with pytest.raises(TypeError):
        Lease()


def test_a_lease_is_not_live_capable_unless_it_says_so():
    # Default-deny: a child lease must declare itself live-capable; a
    # base that defaulted to True would make every fake lease live.
    assert Lease.LIVE_CAPABLE is False
    assert ProcessLease.LIVE_CAPABLE is False


def test_process_lease_is_the_only_lease_core_registers():
    assert LEASE_KINDS.kinds() == ("process",)
    assert LEASE_KINDS.family == "lease"
    assert LEASE_KINDS.resolve("process") is ProcessLease


def test_process_lease_default_denies_an_unknown_param():
    with pytest.raises(ProductionError) as excinfo:
        ProcessLease({"ttl_ms": 5}, clock=FakeClock())
    assert "ttl_ms" in str(excinfo.value)


# ---------------------------------------------------------------------------
# `LeasePermit` — the value object the fence is carried in
# ---------------------------------------------------------------------------


def test_lease_permit_carries_the_four_members_of_5_7_2_in_order():
    assert tuple(f.name for f in dataclasses.fields(LeasePermit)) == (
        "scope",
        "holder",
        "fencing_token",
        "expires_ms",
    )
    assert LeasePermit.__dataclass_params__.frozen is True


def test_a_lease_permit_scope_is_the_canonical_execution_scope():
    # §5.7.2: "coordination.scope is the graded canonical ExecutionScope,
    # not a release id" — a permit scoped by a string cannot be compared
    # to the document, release or ActPermit scope at all.
    with pytest.raises(ProductionError):
        foreign_permit(scope="paper/strategy-a")


@pytest.mark.parametrize(
    "overrides",
    [
        {"holder": ""},
        {"holder": None},
        {"fencing_token": 0},
        {"fencing_token": -1},
        {"fencing_token": "1"},
        {"fencing_token": 1.0},
        {"expires_ms": "later"},
        {"expires_ms": float(BASE_MS)},
    ],
)
def test_a_lease_permit_refuses_a_member_no_gateway_could_act_on(overrides):
    with pytest.raises(ProductionError):
        foreign_permit(**overrides)


def test_a_lease_permit_reports_every_bad_member_in_one_raise():
    with pytest.raises(ProductionError) as excinfo:
        foreign_permit(holder="", fencing_token=0, expires_ms="later")
    assert len(excinfo.value.problems) == 3


# ---------------------------------------------------------------------------
# acquire / current / release
# ---------------------------------------------------------------------------


def test_acquire_binds_the_scope_the_holder_and_the_ttl():
    lease, clock = new_lease()
    permit = lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    assert permit.scope == SCOPE
    assert permit.holder == HOLDER_A
    assert permit.expires_ms == clock.now_ms() + TTL_MS
    assert lease.current(SCOPE) == permit


def test_the_first_fencing_token_is_one():
    lease, _ = new_lease()
    assert lease.acquire(SCOPE, HOLDER_A, TTL_MS).fencing_token == 1


def test_every_acquire_and_renew_raises_the_fencing_token():
    lease, _ = new_lease()
    first = lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    second = lease.renew(first)
    third = lease.renew(second)
    lease.release(third)
    fourth = lease.acquire(SCOPE, HOLDER_B, TTL_MS)
    tokens = [p.fencing_token for p in (first, second, third, fourth)]
    assert tokens == sorted(set(tokens))
    assert tokens[0] < tokens[-1]


def test_current_is_none_for_a_scope_nobody_holds():
    lease, _ = new_lease()
    assert lease.current(SCOPE) is None


def test_current_is_none_once_the_permit_has_expired():
    lease, clock = new_lease()
    lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    clock.advance(TTL_MS)
    assert lease.current(SCOPE) is None


def test_release_frees_the_scope():
    lease, _ = new_lease()
    permit = lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    lease.release(permit)
    assert lease.current(SCOPE) is None


def test_release_refuses_a_permit_this_lease_never_issued():
    lease, _ = new_lease()
    lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    with pytest.raises(ProductionError):
        lease.release(foreign_permit())
    assert lease.current(SCOPE).holder == HOLDER_A


# ---------------------------------------------------------------------------
# Contention — the scope is the ownership domain, not the release
# ---------------------------------------------------------------------------


def test_a_second_holder_cannot_take_a_scope_another_release_still_holds():
    """§5.7.2: old and new releases contend for the same domain."""
    lease, _ = new_lease()
    held = lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    with pytest.raises(ProductionError) as excinfo:
        lease.acquire(SCOPE, HOLDER_B, TTL_MS)
    assert HOLDER_A in str(excinfo.value)
    assert lease.current(SCOPE) == held


def test_the_same_holder_cannot_double_acquire_a_scope_it_already_holds():
    # A second acquire would mint a second token for one holder and make
    # "the current token" ambiguous for the gateway.
    lease, _ = new_lease()
    lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    with pytest.raises(ProductionError):
        lease.acquire(SCOPE, HOLDER_A, TTL_MS)


def test_a_different_scope_is_a_separate_ownership_domain():
    lease, _ = new_lease()
    first = lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    second = lease.acquire(OTHER_ACCOUNT, HOLDER_B, TTL_MS)
    assert lease.current(SCOPE) == first
    assert lease.current(OTHER_ACCOUNT) == second
    assert first.fencing_token != second.fencing_token


def test_an_expired_grip_lapses_and_the_next_release_may_take_the_scope():
    lease, clock = new_lease()
    lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    clock.advance(TTL_MS)
    taken = lease.acquire(SCOPE, HOLDER_B, TTL_MS)
    assert lease.current(SCOPE) == taken
    assert taken.holder == HOLDER_B


# ---------------------------------------------------------------------------
# renew
# ---------------------------------------------------------------------------


def test_renew_extends_the_expiry_by_the_ttl_from_now():
    lease, clock = new_lease()
    permit = lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    clock.advance(EVERY_MS)
    renewed = lease.renew(permit)
    assert renewed.expires_ms == clock.now_ms() + TTL_MS
    assert renewed.expires_ms > permit.expires_ms
    assert renewed.holder == HOLDER_A
    assert renewed.scope == SCOPE


def test_renew_refuses_a_permit_this_lease_never_issued():
    lease, _ = new_lease()
    lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    with pytest.raises(ProductionError):
        lease.renew(foreign_permit())


def test_renew_refuses_an_expired_permit():
    lease, clock = new_lease()
    permit = lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    clock.advance(TTL_MS)
    with pytest.raises(ProductionError):
        lease.renew(permit)


def test_renew_refuses_a_permit_superseded_by_another_holder():
    lease, clock = new_lease()
    stale = lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    clock.advance(TTL_MS)
    lease.acquire(SCOPE, HOLDER_B, TTL_MS)
    with pytest.raises(ProductionError):
        lease.renew(stale)


def test_renew_refuses_a_permit_whose_token_is_no_longer_current():
    lease, _ = new_lease()
    first = lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    lease.renew(first)
    with pytest.raises(ProductionError):
        lease.renew(first)


# ---------------------------------------------------------------------------
# `permit_current` — the local half of the fencing check
# ---------------------------------------------------------------------------


def test_permit_current_is_true_only_for_the_token_the_lease_holds_now():
    lease, _ = new_lease()
    first = lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    second = lease.renew(first)
    assert lease.permit_current(second) is True
    assert lease.permit_current(first) is False


def test_permit_current_is_false_once_the_permit_expired():
    lease, clock = new_lease()
    permit = lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    clock.advance(TTL_MS)
    assert lease.permit_current(permit) is False


def test_permit_current_is_false_for_a_scope_the_lease_released():
    lease, _ = new_lease()
    permit = lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    lease.release(permit)
    assert lease.permit_current(permit) is False


def test_permit_current_is_false_for_a_permit_from_another_lease():
    lease, _ = new_lease()
    lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    assert lease.permit_current(foreign_permit()) is False


def test_permit_current_is_concrete_on_the_seam_so_no_child_respells_it():
    assert "permit_current" not in Lease.__abstractmethods__
    assert ProcessLease.permit_current is Lease.permit_current


# ---------------------------------------------------------------------------
# `scope_equal` — the one owner of §5.7.2's exact-equality rule
# ---------------------------------------------------------------------------


def test_scope_equal_is_true_when_actual_document_release_lease_and_permit_agree():
    same = ExecutionScope(venue="paper", account="strategy-a")
    assert scope_equal(SCOPE, SCOPE, same, same, SCOPE) is True


@pytest.mark.parametrize("odd", [OTHER_ACCOUNT, OTHER_VENUE])
def test_scope_equal_is_false_when_any_one_scope_differs(odd):
    assert scope_equal(SCOPE, SCOPE, odd, SCOPE, SCOPE) is False
    assert scope_equal(odd, SCOPE, SCOPE, SCOPE, SCOPE) is False
    assert scope_equal(SCOPE, SCOPE, SCOPE, SCOPE, odd) is False


def test_scope_equal_refuses_a_missing_scope_rather_than_comparing_false():
    # A `None` lease scope means "we never obtained one"; answering
    # `False` would let a caller log a mismatch and move on.
    with pytest.raises(ProductionError):
        scope_equal(SCOPE, None, SCOPE)


def test_scope_equal_refuses_anything_that_is_not_an_execution_scope():
    with pytest.raises(ProductionError):
        scope_equal(SCOPE, {"venue": "paper", "account": "strategy-a"})


def test_scope_equal_refuses_fewer_than_two_scopes():
    # Comparing one scope with itself always passes and would silently
    # satisfy a caller that dropped four of the five terms.
    with pytest.raises(ProductionError):
        scope_equal(SCOPE)
    with pytest.raises(ProductionError):
        scope_equal()


# ---------------------------------------------------------------------------
# `LeaseRenewer` — the supervised cadence, driven synchronously
# ---------------------------------------------------------------------------


def new_renewer(clock=None, every_ms=EVERY_MS, timeout_ms=TIMEOUT_MS):
    """A renewer over a counting lease that already holds `SCOPE`."""
    clock = clock if clock is not None else FakeClock()
    lease = CountingLease(ProcessLease({}, clock=clock))
    permit = lease.acquire(SCOPE, HOLDER_A, TTL_MS)
    renewer = LeaseRenewer(
        lease, permit, clock=clock, every_ms=every_ms, timeout_ms=timeout_ms
    )
    return renewer, lease, clock


def test_a_renewer_starts_valid_and_holding_the_permit_it_was_given():
    renewer, lease, _ = new_renewer()
    assert renewer.invalidated is False
    assert renewer.permit == lease.current(SCOPE)


def test_a_renewer_does_not_renew_before_the_cadence_is_due():
    renewer, lease, clock = new_renewer()
    before = renewer.permit
    assert renewer.tick(clock.advance(EVERY_MS - 1)) == before
    assert lease.renew_calls == 0


def test_a_renewer_renews_when_the_cadence_is_due():
    renewer, lease, clock = new_renewer()
    before = renewer.permit
    renewed = renewer.tick(clock.advance(EVERY_MS))
    assert lease.renew_calls == 1
    assert renewed.fencing_token > before.fencing_token
    assert renewer.permit == renewed
    assert renewer.invalidated is False


def test_a_renewed_permit_is_the_one_a_submit_would_fence_with():
    renewer, lease, clock = new_renewer()
    renewed = renewer.tick(clock.advance(EVERY_MS))
    assert lease.permit_current(renewed) is True


def test_the_renewal_deadline_is_every_plus_timeout_and_is_inclusive():
    renewer, lease, clock = new_renewer()
    assert renewer.tick(clock.advance(EVERY_MS + TIMEOUT_MS)) is not None
    assert lease.renew_calls == 1
    assert renewer.invalidated is False


def test_a_missed_renewal_deadline_invalidates_the_local_permit():
    """§5.7.2: a missed deadline invalidates without waiting for expiry."""
    renewer, lease, clock = new_renewer()
    assert renewer.tick(clock.advance(EVERY_MS + TIMEOUT_MS + 1)) is None
    assert renewer.invalidated is True
    assert renewer.permit is None
    assert lease.renew_calls == 0


def test_an_invalidated_renewer_never_touches_the_lease_again():
    renewer, lease, clock = new_renewer()
    renewer.tick(clock.advance(EVERY_MS + TIMEOUT_MS + 1))
    assert renewer.tick(clock.advance(EVERY_MS)) is None
    assert lease.renew_calls == 0
    assert renewer.permit is None


def test_a_refused_renewal_invalidates_the_permit_without_raising():
    # Losing the lease must disable submit, not kill the loop: query,
    # reconcile and cancel stay available (§5.7.2).
    renewer, lease, clock = new_renewer()
    lease.refusing = True
    assert renewer.tick(clock.advance(EVERY_MS)) is None
    assert renewer.invalidated is True
    assert renewer.permit is None
    assert lease.renew_calls == 1


def test_the_deadline_is_measured_from_the_last_successful_renewal():
    renewer, lease, clock = new_renewer()
    renewer.tick(clock.advance(EVERY_MS))
    assert renewer.tick(clock.advance(EVERY_MS)) is not None
    assert lease.renew_calls == 2
    assert renewer.invalidated is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"every_ms": 0},
        {"every_ms": -1},
        {"timeout_ms": 0},
        {"timeout_ms": -1},
    ],
)
def test_a_renewer_refuses_a_cadence_it_could_never_meet(kwargs):
    with pytest.raises(ProductionError):
        new_renewer(**kwargs)


def test_a_renewer_refuses_a_permit_that_is_not_a_lease_permit():
    clock = FakeClock()
    lease = ProcessLease({}, clock=clock)
    with pytest.raises(ProductionError):
        LeaseRenewer(
            lease, None, clock=clock, every_ms=EVERY_MS, timeout_ms=TIMEOUT_MS
        )
