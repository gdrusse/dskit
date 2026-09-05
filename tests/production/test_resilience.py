"""`resilience.py` — the four policy objects that stand between the loop and a
venue that is slow, rude, or lying.

Three properties are worth more than the rest of this file put together, and
each has its own section below.

1. **An ambiguous WRITE never retries.** A submit that raised after the bytes
   left is the one outcome where a retry can double a position, so `decide`
   answers `reconcile` for it at every attempt, under every `retry_writes`
   mode, with a full budget and attempts to spare (D19, §5.12). The sweep at
   the bottom of the decide section is the assertion that matters: there is no
   corner of the knob space where an ambiguous write comes back `retry`.
2. **Every wait is bounded by ONE ceiling.** `MAX_BACKOFF_S` is imported from
   `dskit.onboarding.connector`, not restated — a second copy is the bug
   CLAUDE.md's "Duplication that diverges" is about — and it caps the
   exponential, the jitter, a server-sent `Retry-After` and a limiter hold
   alike. A hostile server asking for four hours gets sixty seconds.
3. **The cancel lane is reserved and still bounded.** `cancel_all` runs when
   things are already going wrong, so cancels must not queue behind exhausted
   submit capacity; they must also not become the flood. Both halves are
   asserted.

Nothing here touches the wall clock, the network or `random`: a `FakeClock` is
advanced by hand, a `FakeSleeper` records the seconds it was asked for instead
of sleeping, and a `FakeRng` returns a settable fraction of its interval, so a
jittered backoff is an exact number rather than a range. `urllib.request.urlopen`
is monkeypatched; the transport must therefore call it by attribute on
`urllib.request`, which is the only way a socket-free test of it can exist.

Every knob is set from a copy of §4.1's `resilience` block, so a test that
changes a value proves the value is READ rather than compiled in.
"""

import dataclasses
import io
import socket
import urllib.error
import urllib.request
from email.message import Message
from email.utils import formatdate

import pytest

from dskit.onboarding.connector import MAX_BACKOFF_S as ONBOARDING_MAX_BACKOFF_S
from dskit.production import resilience
from dskit.production.base import ProductionError
from dskit.production.resilience import (
    DEFAULT_BASE_S,
    DEFAULT_BUDGET_CAPACITY,
    DEFAULT_CAP_S,
    DEFAULT_CONNECT_S,
    DEFAULT_FAILURE_RATE,
    DEFAULT_JITTER,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_IN_FLIGHT,
    DEFAULT_MIN_CALLS,
    DEFAULT_OPEN_S,
    DEFAULT_READ_S,
    DEFAULT_REFUND,
    DEFAULT_RETRY_AFTER,
    DEFAULT_RETRY_WRITES,
    DEFAULT_THROTTLE_BASE_S,
    DEFAULT_THROTTLE_COST,
    DEFAULT_TRANSIENT_COST,
    MAX_ATTEMPTS_BOUNDS,
    MAX_BACKOFF_S,
    TRANSPORT_KINDS,
    CallOutcome,
    CircuitBreaker,
    CircuitBreakers,
    Classifier,
    HttpClassifier,
    RateLimiter,
    ResiliencePolicies,
    Retry,
    Transport,
    UrllibTransport,
    resilience_from_document,
)
from dskit.production.vocab import (
    CIRCUIT_STATES,
    JITTER_MODES,
    RESILIENCE_OUTCOMES,
    RETRY_AFTER_MODES,
    RETRY_DECISIONS,
    RETRY_WRITE_MODES,
)

#: A fixed instant, so nothing here depends on when the suite runs.
START_MS = 1_767_225_600_000  # 2026-01-01T00:00:00Z

#: The kinds `decide` is ever asked about — `ok` is not a decision.
RETRYABLE_KINDS = tuple(k for k in RESILIENCE_OUTCOMES if k != "ok")


# ---------------------------------------------------------------------------
# The injected collaborators — deterministic doubles, never real time
# ---------------------------------------------------------------------------


class FakeClock:
    """A hand-advanced `Clock`: `now_ms`, `monotonic`, `sleep_until`."""

    def __init__(self, start_ms=START_MS):
        self._ms = start_ms

    def now_ms(self):
        return self._ms

    def monotonic(self):
        return self._ms / 1000.0

    def sleep_until(self, epoch_ms, wake=None):
        self._ms = max(self._ms, int(epoch_ms))

    def advance(self, ms):
        self._ms += int(ms)
        return self._ms


class FakeSleeper:
    """A callable that records the seconds it was asked to sleep."""

    def __init__(self):
        self.slept = []

    def __call__(self, seconds):
        self.slept.append(seconds)


class FakeRng:
    """`uniform(a, b)` returns `a + fraction * (b - a)`; every call recorded."""

    def __init__(self, fraction=1.0):
        self.fraction = fraction
        self.calls = []

    def uniform(self, a, b):
        self.calls.append((a, b))
        return a + self.fraction * (b - a)

    def random(self):
        return self.fraction


# ---------------------------------------------------------------------------
# §4.1's `resilience` block, as the document spells it
# ---------------------------------------------------------------------------


def retry_params(budget=None, **over):
    params = {
        "max_attempts": 3,
        "base_s": 0.05,
        "throttle_base_s": 1.0,
        "cap_s": 20.0,
        "jitter": "full",
        "retry_after": "honor",
        "retry_writes": "idempotent_only",
        "budget": {
            "capacity": 500,
            "transient_cost": 14,
            "throttle_cost": 5,
            "refund": 1,
        },
    }
    params.update(over)
    if budget is not None:
        params["budget"] = {**params["budget"], **budget}
    return params


def breaker_params(**over):
    params = {"min_calls": 5, "failure_rate": 0.5, "open_s": 30}
    params.update(over)
    return params


def limiter_params(submit=None, cancel=None):
    params = {
        "submit": {"rate_per_s": 5, "burst": 5, "max_in_flight": 1},
        "cancel": {"rate_per_s": 10, "burst": 10, "reserved": True},
    }
    if submit is not None:
        params["submit"] = {**params["submit"], **submit}
    if cancel is not None:
        params["cancel"] = {**params["cancel"], **cancel}
    return params


def resilience_section(**over):
    section = {
        "retry": retry_params(),
        "breaker": breaker_params(),
        "limiter": limiter_params(),
        "transport": {"uses": "urllib", "params": {"connect_s": 2.0, "read_s": 5.0}},
    }
    section.update(over)
    return section


def make_retry(params=None, *, fraction=1.0, clock=None):
    clock = clock or FakeClock()
    sleeper = FakeSleeper()
    rng = FakeRng(fraction)
    policy = Retry(
        retry_params() if params is None else params,
        clock=clock,
        sleeper=sleeper,
        rng=rng,
    )
    return policy, clock, sleeper, rng


def refusal(callable_):
    """Run something expected to refuse; return the accumulated problems."""
    with pytest.raises(ProductionError) as excinfo:
        callable_()
    assert excinfo.value.problems, "ProductionError carries a LIST of problems"
    return "; ".join(excinfo.value.problems)


# ---------------------------------------------------------------------------
# The module's boundary
# ---------------------------------------------------------------------------


def test_module_exports_only_public_names_and_every_name_it_declares():
    assert resilience.__all__, "`__all__` IS the API contract (CLAUDE.md)"
    for name in resilience.__all__:
        assert not name.startswith("_"), f"{name} is private and must not export"
        assert hasattr(resilience, name), f"{name} is exported but not defined"


@pytest.mark.parametrize(
    "name",
    (
        "CallOutcome",
        "CircuitBreaker",
        "CircuitBreakers",
        "Classifier",
        "HttpClassifier",
        "MAX_BACKOFF_S",
        "RateLimiter",
        "ResiliencePolicies",
        "Retry",
        "TRANSPORT_KINDS",
        "Transport",
        "UrllibTransport",
        "resilience_from_document",
    ),
)
def test_every_name_the_seam_promises_is_exported(name):
    assert name in resilience.__all__


def test_max_backoff_is_the_onboarding_ceiling_itself_not_a_second_copy():
    """§5.12: `MAX_BACKOFF_S` is IMPORTED from onboarding — one ceiling for
    the whole toolkit. A restated 60.0 would drift the day onboarding moves."""
    assert MAX_BACKOFF_S is ONBOARDING_MAX_BACKOFF_S
    assert MAX_BACKOFF_S == 60.0


def test_the_classifier_family_has_no_registry():
    """§4.3 closes the registry list at twenty and no classifier family is in
    it; a classifier is chosen by the transport's owner, not by a `uses`."""
    assert not hasattr(resilience, "CLASSIFIER_KINDS")


# ---------------------------------------------------------------------------
# Classifier — code first, status second
# ---------------------------------------------------------------------------


def test_classify_is_abstract_so_an_incomplete_classifier_cannot_construct():
    assert "classify" in Classifier.__abstractmethods__
    with pytest.raises(TypeError):
        Classifier()


def test_the_http_classifier_is_a_classifier():
    assert issubclass(HttpClassifier, Classifier)


def test_call_outcome_is_a_frozen_value_with_the_three_fields_in_order():
    names = tuple(f.name for f in dataclasses.fields(CallOutcome))
    assert names == ("status", "exception", "request_sent")
    outcome = CallOutcome(200, None, True)
    assert outcome.status == 200 and outcome.request_sent is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.status = 500


def test_call_outcome_defaults_to_nothing_sent_and_nothing_returned():
    outcome = CallOutcome()
    assert outcome.status is None
    assert outcome.exception is None
    assert outcome.request_sent is False


@pytest.mark.parametrize("status", (200, 201, 202, 204))
def test_a_two_hundred_is_ok(status):
    assert HttpClassifier().classify(CallOutcome(status=status, request_sent=True)) == "ok"


def test_request_timeout_is_transient_and_rate_limited_is_throttled():
    classify = HttpClassifier().classify
    assert classify(CallOutcome(status=408, request_sent=True)) == "transient"
    assert classify(CallOutcome(status=429, request_sent=True)) == "throttled"


@pytest.mark.parametrize("status", (500, 502, 503, 504))
def test_a_server_fault_is_transient(status):
    assert HttpClassifier().classify(CallOutcome(status=status, request_sent=True)) == "transient"


@pytest.mark.parametrize("status", (400, 401, 403, 404, 409, 422))
def test_any_other_client_error_is_fatal(status):
    assert HttpClassifier().classify(CallOutcome(status=status, request_sent=True)) == "fatal"


@pytest.mark.parametrize("status", (100, 301, 302, 304))
def test_a_status_that_is_neither_success_nor_retryable_is_fatal(status):
    """An unfollowed redirect or an informational reply is a contract error
    here, not something a second identical call would fix."""
    assert HttpClassifier().classify(CallOutcome(status=status, request_sent=True)) == "fatal"


@pytest.mark.parametrize(
    "exception",
    (
        ConnectionError("refused"),
        ConnectionResetError("reset"),
        TimeoutError("timed out"),
        socket.timeout("timed out"),
        OSError("no route to host"),
    ),
)
def test_a_connection_fault_before_the_request_left_is_transient(exception):
    outcome = CallOutcome(exception=exception, request_sent=False)
    assert HttpClassifier().classify(outcome) == "transient"


@pytest.mark.parametrize(
    "exception",
    (ConnectionError("refused"), TimeoutError("timed out"), ValueError("bad body")),
)
def test_any_raise_after_the_request_left_is_ambiguous(exception):
    """The bytes may have landed; nothing local can tell. §5.12/D13."""
    outcome = CallOutcome(exception=exception, request_sent=True)
    assert HttpClassifier().classify(outcome) == "ambiguous"


def test_a_local_error_before_the_request_left_is_fatal_not_transient():
    """A `ValueError` building the body is not a network fault; retrying it
    just burns the budget."""
    outcome = CallOutcome(exception=ValueError("bad body"), request_sent=False)
    assert HttpClassifier().classify(outcome) == "fatal"


def test_the_code_is_read_before_the_status():
    """§5.12: 'code first, status second'. A 200 that raised on the way back
    is ambiguous, never ok."""
    classify = HttpClassifier().classify
    ambiguous = CallOutcome(status=200, exception=TimeoutError(), request_sent=True)
    assert classify(ambiguous) == "ambiguous"
    transient = CallOutcome(status=200, exception=ConnectionError(), request_sent=False)
    assert classify(transient) == "transient"


def test_an_empty_outcome_refuses_rather_than_guessing():
    with pytest.raises(ProductionError):
        HttpClassifier().classify(CallOutcome())


@pytest.mark.parametrize("status", (200, 408, 429, 500, 404, 302))
def test_every_classification_is_a_resilience_outcomes_member(status):
    assert HttpClassifier().classify(CallOutcome(status=status, request_sent=True)) in (
        RESILIENCE_OUTCOMES
    )


# ---------------------------------------------------------------------------
# Retry — validation and the named defaults
# ---------------------------------------------------------------------------


def test_retry_requires_its_three_injected_collaborators():
    """D19: pure stdlib objects with an injected clock, sleeper and rng —
    a `Retry` that could reach `time.sleep` itself is untestable."""
    with pytest.raises(TypeError):
        Retry(retry_params())


def test_retry_refuses_an_unknown_knob_by_name():
    text = refusal(lambda: make_retry({**retry_params(), "backoff": 2}))
    assert "backoff" in text


def test_retry_refuses_an_unknown_budget_knob_by_name():
    params = retry_params()
    params["budget"] = {**params["budget"], "ceiling": 9}
    assert "ceiling" in refusal(lambda: make_retry(params))


def test_retry_accumulates_every_problem_into_one_raise():
    params = retry_params(max_attempts=99, jitter="sparkly", retry_writes="always")
    with pytest.raises(ProductionError) as excinfo:
        make_retry(params)
    assert len(excinfo.value.problems) >= 3, excinfo.value.problems


@pytest.mark.parametrize("attempts", (0, 11, 3.5, "3"))
def test_max_attempts_outside_its_bounds_refuses(attempts):
    assert "max_attempts" in refusal(
        lambda: make_retry(retry_params(max_attempts=attempts))
    )


@pytest.mark.parametrize("attempts", (1, 3, 10))
def test_max_attempts_inside_its_bounds_is_accepted(attempts):
    make_retry(retry_params(max_attempts=attempts))


def test_the_attempt_bounds_are_one_named_pair():
    assert MAX_ATTEMPTS_BOUNDS == (1, 10)
    assert MAX_ATTEMPTS_BOUNDS[0] <= DEFAULT_MAX_ATTEMPTS <= MAX_ATTEMPTS_BOUNDS[1]


def test_a_cap_above_the_one_ceiling_refuses_at_validation():
    """§5.12: `cap_s` is 'bounded by the imported MAX_BACKOFF_S'. Bounded at
    validation, so a document cannot declare a wait it will never get."""
    assert "cap_s" in refusal(
        lambda: make_retry(retry_params(cap_s=MAX_BACKOFF_S + 0.1))
    )
    make_retry(retry_params(cap_s=MAX_BACKOFF_S))


@pytest.mark.parametrize("key", ("base_s", "throttle_base_s", "cap_s"))
@pytest.mark.parametrize("value", (0, -1.0))
def test_a_non_positive_wait_knob_refuses(key, value):
    assert key in refusal(lambda: make_retry(retry_params(**{key: value})))


@pytest.mark.parametrize(
    "key,bad",
    (("jitter", "sparkly"), ("retry_after", "maybe"), ("retry_writes", "always")),
)
def test_a_mode_outside_its_closed_vocabulary_refuses(key, bad):
    assert key in refusal(lambda: make_retry(retry_params(**{key: bad})))


@pytest.mark.parametrize("mode", JITTER_MODES)
def test_every_jitter_mode_in_the_vocabulary_is_accepted(mode):
    make_retry(retry_params(jitter=mode))


@pytest.mark.parametrize("mode", RETRY_AFTER_MODES)
def test_every_retry_after_mode_in_the_vocabulary_is_accepted(mode):
    make_retry(retry_params(retry_after=mode))


@pytest.mark.parametrize("mode", RETRY_WRITE_MODES)
def test_every_retry_write_mode_in_the_vocabulary_is_accepted(mode):
    make_retry(retry_params(retry_writes=mode))


@pytest.mark.parametrize(
    "budget", ({"capacity": 0}, {"transient_cost": -1}, {"refund": -1})
)
def test_a_budget_outside_its_bounds_refuses(budget):
    assert "budget" in refusal(lambda: make_retry(retry_params(budget=budget)))


def test_every_default_is_one_named_constant_holding_the_documented_value():
    """CLAUDE.md: 'a default belongs to ONE name'. §5.12 fixes the values."""
    assert DEFAULT_MAX_ATTEMPTS == 3
    assert DEFAULT_BASE_S == 0.05
    assert DEFAULT_THROTTLE_BASE_S == 1.0
    assert DEFAULT_CAP_S == 20.0
    assert DEFAULT_JITTER == "full"
    assert DEFAULT_RETRY_AFTER == "honor"
    assert DEFAULT_RETRY_WRITES == "idempotent_only"
    assert DEFAULT_BUDGET_CAPACITY == 500
    assert DEFAULT_TRANSIENT_COST == 14
    assert DEFAULT_THROTTLE_COST == 5
    assert DEFAULT_REFUND == 1


def test_an_empty_retry_params_runs_on_those_same_constants():
    """The run must read the SAME name `validate_params` approved, so this
    asserts behaviour, not the constant: three attempts, then give up."""
    policy, _clock, _sleeper, _rng = make_retry({})
    assert policy.decide(DEFAULT_MAX_ATTEMPTS - 1, "transient", False) == "retry"
    assert policy.decide(DEFAULT_MAX_ATTEMPTS, "transient", False) == "give_up"
    assert policy.budget.capacity == DEFAULT_BUDGET_CAPACITY


# ---------------------------------------------------------------------------
# Retry.decide — the ambiguous-write rule above all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("attempt", (1, 2, 3, 10))
@pytest.mark.parametrize("mode", RETRY_WRITE_MODES)
def test_an_ambiguous_write_never_retries_it_reconciles(attempt, mode):
    """D19, §5.12, D13. The one rule that can double a position if it slips."""
    policy, _c, _s, _r = make_retry(
        retry_params(max_attempts=10, retry_writes=mode, budget={"capacity": 500})
    )
    assert policy.decide(attempt, "ambiguous", True) == "reconcile"


def test_an_ambiguous_write_reconciles_even_with_a_spent_budget():
    policy, _c, _s, _r = make_retry(
        retry_params(budget={"capacity": 1, "transient_cost": 14})
    )
    assert policy.decide(1, "ambiguous", True) == "reconcile"


def test_an_ambiguous_read_may_retry_because_nothing_moved():
    policy, _c, _s, _r = make_retry(retry_params())
    assert policy.decide(1, "ambiguous", False) == "retry"
    assert policy.decide(3, "ambiguous", False) == "give_up"


@pytest.mark.parametrize("is_write", (True, False))
@pytest.mark.parametrize("attempt", (1, 2, 3))
def test_a_fatal_never_retries(is_write, attempt):
    policy, _c, _s, _r = make_retry(retry_params())
    assert policy.decide(attempt, "fatal", is_write) == "give_up"


def test_decide_refuses_an_ok_outcome_because_success_needs_no_decision():
    policy, _c, _s, _r = make_retry(retry_params())
    with pytest.raises(ProductionError):
        policy.decide(1, "ok", False)


def test_decide_refuses_an_outcome_outside_the_closed_vocabulary():
    policy, _c, _s, _r = make_retry(retry_params())
    with pytest.raises(ProductionError):
        policy.decide(1, "weird", False)


@pytest.mark.parametrize("attempt", (0, -1))
def test_decide_refuses_an_attempt_below_one(attempt):
    """`attempt` is the ONE-based number of the attempt that just failed, so
    `max_attempts` tries produce decisions 1..max_attempts."""
    policy, _c, _s, _r = make_retry(retry_params())
    with pytest.raises(ProductionError):
        policy.decide(attempt, "transient", False)


@pytest.mark.parametrize("kind", ("transient", "throttled"))
def test_a_retryable_read_retries_until_the_attempt_cap(kind):
    policy, _c, _s, _r = make_retry(retry_params(max_attempts=3))
    assert policy.decide(1, kind, False) == "retry"
    assert policy.decide(2, kind, False) == "retry"
    assert policy.decide(3, kind, False) == "give_up"


def test_one_attempt_means_no_retry_at_all():
    policy, _c, _s, _r = make_retry(retry_params(max_attempts=1))
    assert policy.decide(1, "transient", False) == "give_up"


@pytest.mark.parametrize("kind", ("transient", "throttled"))
def test_a_write_never_retries_under_retry_writes_never(kind):
    policy, _c, _s, _r = make_retry(retry_params(retry_writes="never"))
    assert policy.decide(1, kind, True) == "give_up"
    assert policy.decide(1, kind, False) == "retry"


@pytest.mark.parametrize("kind", ("transient", "throttled"))
def test_a_write_may_retry_under_idempotent_only(kind):
    policy, _c, _s, _r = make_retry(retry_params(retry_writes="idempotent_only"))
    assert policy.decide(1, kind, True) == "retry"


@pytest.mark.parametrize("kind", RETRYABLE_KINDS)
@pytest.mark.parametrize("is_write", (True, False))
@pytest.mark.parametrize("attempt", (1, 2, 3))
def test_every_decision_is_a_retry_decisions_member(kind, is_write, attempt):
    policy, _c, _s, _r = make_retry(retry_params())
    assert policy.decide(attempt, kind, is_write) in RETRY_DECISIONS


@pytest.mark.parametrize("kind", RETRYABLE_KINDS)
@pytest.mark.parametrize("mode", RETRY_WRITE_MODES)
@pytest.mark.parametrize("attempt", (1, 2))
def test_reconcile_is_reserved_for_the_ambiguous_write(kind, mode, attempt):
    """Nothing else in the space may answer `reconcile`, or the executor
    would query the venue over an outcome that never touched it."""
    policy, _c, _s, _r = make_retry(retry_params(max_attempts=10, retry_writes=mode))
    for is_write in (True, False):
        decision = policy.decide(attempt, kind, is_write)
        if decision == "reconcile":
            assert kind == "ambiguous" and is_write is True


# ---------------------------------------------------------------------------
# The retry budget — a retry storm costs more than it earns
# ---------------------------------------------------------------------------


def test_the_budget_starts_full_at_its_declared_capacity():
    policy, _c, _s, _r = make_retry(retry_params(budget={"capacity": 40}))
    assert policy.budget.capacity == 40
    assert policy.budget.balance == 40


def test_a_transient_retry_spends_the_transient_cost():
    policy, _c, _s, _r = make_retry(
        retry_params(max_attempts=10, budget={"capacity": 20, "transient_cost": 14})
    )
    assert policy.decide(1, "transient", False) == "retry"
    assert policy.budget.balance == 6


def test_a_throttled_retry_spends_the_throttle_cost():
    policy, _c, _s, _r = make_retry(
        retry_params(max_attempts=10, budget={"capacity": 20, "throttle_cost": 5})
    )
    assert policy.decide(1, "throttled", False) == "retry"
    assert policy.budget.balance == 15


def test_a_retry_the_budget_cannot_pay_becomes_give_up_with_attempts_to_spare():
    policy, _c, _s, _r = make_retry(
        retry_params(max_attempts=10, budget={"capacity": 20, "transient_cost": 14})
    )
    assert policy.decide(1, "transient", False) == "retry"
    assert policy.decide(2, "transient", False) == "give_up"
    assert policy.budget.balance == 6, "a refused retry spends nothing"


def test_a_refused_retry_and_a_fatal_spend_nothing():
    policy, _c, _s, _r = make_retry(retry_params(budget={"capacity": 20}))
    policy.decide(1, "fatal", False)
    policy.decide(1, "ambiguous", True)
    policy.decide(3, "transient", False)
    assert policy.budget.balance == 20


def test_every_success_refunds_and_the_balance_never_exceeds_capacity():
    policy, _c, _s, _r = make_retry(
        retry_params(max_attempts=10, budget={"capacity": 20, "transient_cost": 14, "refund": 1})
    )
    assert policy.decide(1, "transient", False) == "retry"
    assert policy.budget.balance == 6
    policy.refund()
    assert policy.budget.balance == 7
    for _ in range(100):
        policy.refund()
    assert policy.budget.balance == 20


def test_a_refunded_budget_can_pay_for_a_retry_again():
    policy, _c, _s, _r = make_retry(
        retry_params(max_attempts=10, budget={"capacity": 20, "transient_cost": 14})
    )
    assert policy.decide(1, "transient", False) == "retry"
    assert policy.decide(2, "transient", False) == "give_up"
    for _ in range(8):
        policy.refund()
    assert policy.decide(2, "transient", False) == "retry"


def test_an_ambiguous_read_retry_is_charged_the_transient_cost():
    policy, _c, _s, _r = make_retry(
        retry_params(max_attempts=10, budget={"capacity": 20, "transient_cost": 14})
    )
    assert policy.decide(1, "ambiguous", False) == "retry"
    assert policy.budget.balance == 6


# ---------------------------------------------------------------------------
# Retry.backoff_s — exponential, jittered, and capped by ONE ceiling
# ---------------------------------------------------------------------------


def test_backoff_doubles_from_base_s_with_jitter_off():
    policy, _c, _s, _r = make_retry(retry_params(jitter="none", base_s=0.05, cap_s=20.0))
    assert policy.backoff_s(1, "transient") == pytest.approx(0.05)
    assert policy.backoff_s(2, "transient") == pytest.approx(0.10)
    assert policy.backoff_s(3, "transient") == pytest.approx(0.20)


def test_a_throttled_wait_starts_from_the_throttle_base_not_the_base():
    """A 429 means slow down by an order of magnitude, not by 50 ms."""
    policy, _c, _s, _r = make_retry(
        retry_params(jitter="none", base_s=0.05, throttle_base_s=1.0)
    )
    assert policy.backoff_s(1, "throttled") == pytest.approx(1.0)
    assert policy.backoff_s(2, "throttled") == pytest.approx(2.0)
    assert policy.backoff_s(1, "transient") == pytest.approx(0.05)


def test_the_exponential_is_capped_at_cap_s_before_the_jitter():
    policy, _c, _s, _r = make_retry(
        retry_params(jitter="none", base_s=0.05, cap_s=0.5)
    )
    assert policy.backoff_s(10, "transient") == pytest.approx(0.5)


def test_full_jitter_draws_uniformly_between_zero_and_the_capped_wait():
    policy, _c, _s, rng = make_retry(
        retry_params(jitter="full", base_s=0.05, cap_s=20.0), fraction=1.0
    )
    assert policy.backoff_s(3, "transient") == pytest.approx(0.20)
    assert rng.calls == [(0, pytest.approx(0.20))]
    policy, _c, _s, rng = make_retry(
        retry_params(jitter="full", base_s=0.05, cap_s=20.0), fraction=0.0
    )
    assert policy.backoff_s(3, "transient") == pytest.approx(0.0)


def test_equal_jitter_keeps_half_the_wait_and_draws_the_other_half():
    policy, _c, _s, rng = make_retry(
        retry_params(jitter="equal", base_s=0.05, cap_s=20.0), fraction=0.0
    )
    assert policy.backoff_s(3, "transient") == pytest.approx(0.10)
    assert rng.calls == [(0, pytest.approx(0.10))]
    policy, _c, _s, _r = make_retry(
        retry_params(jitter="equal", base_s=0.05, cap_s=20.0), fraction=1.0
    )
    assert policy.backoff_s(3, "transient") == pytest.approx(0.20)


def test_no_jitter_asks_the_rng_for_nothing():
    policy, _c, _s, rng = make_retry(retry_params(jitter="none"))
    policy.backoff_s(2, "transient")
    assert rng.calls == []


def test_a_honoured_retry_after_is_taken_as_given_and_not_jittered():
    policy, _c, _s, rng = make_retry(
        retry_params(jitter="full", retry_after="honor"), fraction=1.0
    )
    assert policy.backoff_s(1, "throttled", retry_after=3.0) == pytest.approx(3.0)
    assert rng.calls == [], "a server instruction is obeyed, not randomised"


def test_a_honoured_retry_after_is_still_capped_by_cap_s():
    policy, _c, _s, _r = make_retry(retry_params(retry_after="honor", cap_s=20.0))
    assert policy.backoff_s(1, "throttled", retry_after=9999) == pytest.approx(20.0)


def test_a_honoured_retry_after_is_capped_by_the_one_ceiling_at_the_widest_cap():
    """`cap_s` may be raised to the ceiling itself; nothing may exceed it."""
    policy, _c, _s, _r = make_retry(
        retry_params(retry_after="honor", cap_s=MAX_BACKOFF_S)
    )
    assert policy.backoff_s(1, "throttled", retry_after=9999) == pytest.approx(
        MAX_BACKOFF_S
    )


def test_retry_after_ignore_falls_back_to_the_ordinary_backoff():
    policy, _c, _s, _r = make_retry(
        retry_params(retry_after="ignore", jitter="none", throttle_base_s=1.0)
    )
    assert policy.backoff_s(2, "throttled", retry_after=3.0) == pytest.approx(2.0)


@pytest.mark.parametrize("jitter", JITTER_MODES)
@pytest.mark.parametrize("kind", RETRYABLE_KINDS)
@pytest.mark.parametrize("attempt", (1, 5, 10))
def test_no_wait_anywhere_in_the_space_exceeds_the_ceiling(jitter, kind, attempt):
    policy, _c, _s, _r = make_retry(
        retry_params(jitter=jitter, cap_s=MAX_BACKOFF_S, throttle_base_s=30.0),
        fraction=1.0,
    )
    for retry_after in (None, 0.1, 9999):
        wait = policy.backoff_s(attempt, kind, retry_after=retry_after)
        assert 0.0 <= wait <= MAX_BACKOFF_S


def test_backoff_refuses_an_attempt_below_one_and_an_ok_outcome():
    policy, _c, _s, _r = make_retry(retry_params())
    with pytest.raises(ProductionError):
        policy.backoff_s(0, "transient")
    with pytest.raises(ProductionError):
        policy.backoff_s(1, "ok")


def test_wait_sleeps_through_the_injected_sleeper_for_exactly_the_backoff():
    policy, _clock, sleeper, _r = make_retry(retry_params(jitter="none", base_s=0.05))
    slept = policy.wait(2, "transient")
    assert slept == pytest.approx(0.10)
    assert sleeper.slept == [pytest.approx(0.10)]


def test_wait_honours_a_capped_retry_after_through_the_same_sleeper():
    policy, _clock, sleeper, _r = make_retry(
        retry_params(jitter="none", retry_after="honor", cap_s=20.0)
    )
    policy.wait(1, "throttled", retry_after=9999)
    assert sleeper.slept == [pytest.approx(20.0)]


# ---------------------------------------------------------------------------
# CircuitBreaker — a dependency that is failing stops being called
# ---------------------------------------------------------------------------


def make_breaker(params=None, clock=None):
    clock = clock or FakeClock()
    return CircuitBreaker(breaker_params() if params is None else params, clock=clock), clock


def test_breaker_defaults_are_named_constants_holding_the_documented_values():
    assert DEFAULT_MIN_CALLS == 5
    assert DEFAULT_FAILURE_RATE == 0.5
    assert DEFAULT_OPEN_S == 30


def test_breaker_refuses_an_unknown_knob_by_name():
    assert "half_open_calls" in refusal(
        lambda: make_breaker({**breaker_params(), "half_open_calls": 2})
    )


@pytest.mark.parametrize(
    "params",
    ({"min_calls": 0}, {"failure_rate": 0.0}, {"failure_rate": 1.5}, {"open_s": 0}),
)
def test_breaker_refuses_a_knob_outside_its_bounds(params):
    with pytest.raises(ProductionError):
        make_breaker(breaker_params(**params))


def test_a_fresh_breaker_is_closed_and_admits_calls():
    breaker, _clock = make_breaker()
    assert breaker.state == "closed"
    assert breaker.allow() is True


def test_every_state_the_breaker_reaches_is_a_circuit_states_member():
    breaker, clock = make_breaker(breaker_params(min_calls=5, open_s=30))
    seen = {breaker.state}
    for _ in range(5):
        breaker.record("transient")
    seen.add(breaker.state)
    clock.advance(30_000)
    breaker.allow()
    seen.add(breaker.state)
    breaker.record("ok")
    seen.add(breaker.state)
    breaker.trip()
    seen.add(breaker.state)
    breaker.reset()
    breaker.observe_only()
    seen.add(breaker.state)
    assert seen <= set(CIRCUIT_STATES)
    assert seen == {"closed", "open", "half_open", "forced_open", "metrics_only"}


def test_a_business_rejection_is_not_a_failure_and_never_opens_the_circuit():
    """§5.12: 'business rejections not counted'. A venue refusing ten orders
    for insufficient margin is healthy; opening on it would hide the real
    fault and stop cancels."""
    breaker, _clock = make_breaker()
    for _ in range(10):
        breaker.record("fatal")
    assert breaker.state == "closed"
    assert breaker.allow() is True


def test_the_circuit_stays_closed_below_min_calls_however_bad_the_rate():
    breaker, _clock = make_breaker(breaker_params(min_calls=5))
    for _ in range(4):
        breaker.record("transient")
    assert breaker.state == "closed"
    breaker.record("transient")
    assert breaker.state == "open"


def test_the_circuit_stays_closed_below_the_failure_rate():
    breaker, _clock = make_breaker(breaker_params(min_calls=5, failure_rate=0.5))
    for kind in ("transient", "transient", "ok", "ok", "ok"):
        breaker.record(kind)
    assert breaker.state == "closed"
    assert breaker.allow() is True


def test_the_circuit_opens_at_the_failure_rate_and_refuses_calls():
    breaker, _clock = make_breaker(breaker_params(min_calls=5, failure_rate=0.5))
    for kind in ("transient", "transient", "transient", "ok", "ok"):
        breaker.record(kind)
    assert breaker.state == "open"
    assert breaker.allow() is False


@pytest.mark.parametrize("kind", ("transient", "throttled", "ambiguous"))
def test_every_non_business_failure_counts_towards_opening(kind):
    breaker, _clock = make_breaker(breaker_params(min_calls=5))
    for _ in range(5):
        breaker.record(kind)
    assert breaker.state == "open"


def test_the_circuit_refuses_an_outcome_outside_the_closed_vocabulary():
    breaker, _clock = make_breaker()
    with pytest.raises(ProductionError):
        breaker.record("weird")


def test_an_open_circuit_admits_exactly_one_probe_after_open_s():
    breaker, clock = make_breaker(breaker_params(min_calls=5, open_s=30))
    for _ in range(5):
        breaker.record("transient")
    clock.advance(30_000 - 1)
    assert breaker.allow() is False
    assert breaker.state == "open"
    clock.advance(1)
    assert breaker.allow() is True
    assert breaker.state == "half_open"
    assert breaker.allow() is False, "one probe, not a stampede"


def test_a_successful_probe_closes_the_circuit_and_clears_the_counts():
    breaker, clock = make_breaker(breaker_params(min_calls=5, open_s=30))
    for _ in range(5):
        breaker.record("transient")
    clock.advance(30_000)
    breaker.allow()
    breaker.record("ok")
    assert breaker.state == "closed"
    assert breaker.allow() is True
    for _ in range(4):
        breaker.record("transient")
    assert breaker.state == "closed", "the old failures were cleared"


def test_a_failed_probe_reopens_the_circuit_for_another_open_s():
    breaker, clock = make_breaker(breaker_params(min_calls=5, open_s=30))
    for _ in range(5):
        breaker.record("transient")
    clock.advance(30_000)
    breaker.allow()
    breaker.record("transient")
    assert breaker.state == "open"
    assert breaker.allow() is False
    clock.advance(30_000 - 1)
    assert breaker.allow() is False
    clock.advance(1)
    assert breaker.allow() is True


def test_trip_forces_the_circuit_open_and_time_does_not_release_it():
    breaker, clock = make_breaker(breaker_params(open_s=30))
    breaker.trip()
    assert breaker.state == "forced_open"
    assert breaker.allow() is False
    clock.advance(30_000 * 10)
    assert breaker.state == "forced_open"
    assert breaker.allow() is False


def test_reset_returns_a_forced_open_circuit_to_closed_with_clean_counts():
    breaker, _clock = make_breaker(breaker_params(min_calls=5))
    for _ in range(5):
        breaker.record("transient")
    breaker.trip()
    breaker.reset()
    assert breaker.state == "closed"
    assert breaker.allow() is True
    for _ in range(4):
        breaker.record("transient")
    assert breaker.state == "closed"


def test_metrics_only_records_the_failures_and_never_blocks_a_call():
    """The state `metrics_only` exists so an operator can watch what a
    breaker WOULD have done before letting it act."""
    breaker, _clock = make_breaker(breaker_params(min_calls=5))
    breaker.observe_only()
    assert breaker.state == "metrics_only"
    for _ in range(20):
        breaker.record("transient")
    assert breaker.state == "metrics_only"
    assert breaker.allow() is True
    breaker.reset()
    assert breaker.state == "closed"


# ---------------------------------------------------------------------------
# CircuitBreakers — one per scope, never one for the venue
# ---------------------------------------------------------------------------


def test_a_scope_gets_its_own_breaker_and_the_same_scope_gets_the_same_one():
    breakers = CircuitBreakers(breaker_params(), clock=FakeClock())
    first = breakers.for_scope("venue-a")
    assert isinstance(first, CircuitBreaker)
    assert breakers.for_scope("venue-a") is first
    assert breakers.for_scope("venue-b") is not first


def test_one_scope_tripping_leaves_every_other_scope_calling():
    breakers = CircuitBreakers(breaker_params(min_calls=5), clock=FakeClock())
    hot = breakers.for_scope("venue-a")
    cold = breakers.for_scope("venue-b")
    for _ in range(5):
        hot.record("transient")
    assert hot.allow() is False
    assert cold.allow() is True
    assert cold.state == "closed"


def test_the_scoped_breakers_share_the_documents_knobs():
    breakers = CircuitBreakers(breaker_params(min_calls=2), clock=FakeClock())
    breaker = breakers.for_scope("venue-a")
    breaker.record("transient")
    breaker.record("transient")
    assert breaker.state == "open"


# ---------------------------------------------------------------------------
# RateLimiter — two lanes, the cancel one reserved and still bounded
# ---------------------------------------------------------------------------


def make_limiter(params=None, clock=None):
    clock = clock or FakeClock()
    return RateLimiter(limiter_params() if params is None else params, clock=clock), clock


def test_the_write_bulkhead_default_is_one_named_constant():
    """D19: 'token buckets per scope with a write bulkhead of one'."""
    assert DEFAULT_MAX_IN_FLIGHT == 1
    limiter, _clock = make_limiter(
        {"submit": {"rate_per_s": 5, "burst": 5}, "cancel": {"rate_per_s": 5, "burst": 5}}
    )
    assert limiter.acquire("submit") is True
    assert limiter.acquire("submit") is False
    assert limiter.in_flight("submit") == 1


def test_the_limiter_refuses_an_unknown_lane_knob_by_name():
    assert "concurrency" in refusal(
        lambda: make_limiter(limiter_params(submit={"concurrency": 4}))
    )


def test_the_limiter_refuses_an_unknown_lane_by_name():
    params = limiter_params()
    params["query"] = {"rate_per_s": 1, "burst": 1}
    assert "query" in refusal(lambda: make_limiter(params))


@pytest.mark.parametrize("lane", ("submit", "cancel"))
def test_a_missing_lane_refuses_because_both_lanes_must_exist(lane):
    params = limiter_params()
    del params[lane]
    assert lane in refusal(lambda: make_limiter(params))


@pytest.mark.parametrize(
    "bad", ({"rate_per_s": 0}, {"burst": 0}, {"max_in_flight": 0}, {"reserved": "yes"})
)
def test_a_lane_knob_outside_its_bounds_refuses(bad):
    with pytest.raises(ProductionError):
        make_limiter(limiter_params(submit=bad))


def test_acquire_refuses_a_lane_the_document_never_declared():
    limiter, _clock = make_limiter()
    with pytest.raises(ProductionError):
        limiter.acquire("query")


def test_the_bucket_hands_out_burst_tokens_and_then_refuses():
    limiter, _clock = make_limiter(
        limiter_params(submit={"rate_per_s": 1, "burst": 3, "max_in_flight": 5})
    )
    assert [limiter.acquire("submit") for _ in range(4)] == [True, True, True, False]


def test_the_bucket_refills_at_rate_per_s_and_not_before():
    limiter, clock = make_limiter(
        limiter_params(submit={"rate_per_s": 4, "burst": 2, "max_in_flight": 5})
    )
    limiter.acquire("submit")
    limiter.acquire("submit")
    assert limiter.acquire("submit") is False
    clock.advance(249)
    assert limiter.acquire("submit") is False
    clock.advance(1)
    assert limiter.acquire("submit") is True


def test_the_bucket_never_refills_above_its_burst():
    limiter, clock = make_limiter(
        limiter_params(submit={"rate_per_s": 4, "burst": 2, "max_in_flight": 5})
    )
    limiter.acquire("submit")
    limiter.acquire("submit")
    clock.advance(100_000)
    assert [limiter.acquire("submit") for _ in range(3)] == [True, True, False]


def test_next_allowed_is_now_while_a_token_is_waiting_and_moves_out_when_spent():
    limiter, clock = make_limiter(
        limiter_params(submit={"rate_per_s": 4, "burst": 1, "max_in_flight": 5})
    )
    assert limiter.next_allowed_ms("submit") == clock.now_ms()
    limiter.acquire("submit")
    assert limiter.next_allowed_ms("submit") == clock.now_ms() + 250


def test_in_flight_is_released_and_a_release_below_zero_refuses():
    limiter, _clock = make_limiter(
        limiter_params(submit={"rate_per_s": 10, "burst": 10, "max_in_flight": 1})
    )
    assert limiter.acquire("submit") is True
    assert limiter.in_flight("submit") == 1
    assert limiter.acquire("submit") is False, "tokens remain; the bulkhead is full"
    limiter.release("submit")
    assert limiter.in_flight("submit") == 0
    assert limiter.acquire("submit") is True
    limiter.release("submit")
    with pytest.raises(ProductionError):
        limiter.release("submit")


def test_the_cancel_lane_keeps_its_capacity_when_the_submit_lane_is_exhausted():
    """§5.12: 'cancel capacity is reserved and has priority'. `cancel_all`
    runs when submits have already been throttled to a standstill."""
    limiter, _clock = make_limiter(
        limiter_params(
            submit={"rate_per_s": 1, "burst": 2, "max_in_flight": 5},
            cancel={"rate_per_s": 1, "burst": 2, "max_in_flight": 5, "reserved": True},
        )
    )
    while limiter.acquire("submit"):
        pass
    assert limiter.acquire("submit") is False
    assert limiter.acquire("cancel") is True


def test_the_cancel_lane_is_reserved_but_still_bounded_by_its_own_burst():
    """The other half: a reserved lane that could not run dry would BE the
    flood §5.12 says exhaustion must avoid."""
    limiter, _clock = make_limiter(
        limiter_params(
            cancel={"rate_per_s": 1, "burst": 2, "max_in_flight": 5, "reserved": True}
        )
    )
    assert [limiter.acquire("cancel") for _ in range(3)] == [True, True, False]


def test_a_retry_after_header_holds_the_lane_for_the_seconds_it_names():
    limiter, clock = make_limiter(
        limiter_params(submit={"rate_per_s": 10, "burst": 10, "max_in_flight": 5})
    )
    limiter.observe("submit", {"Retry-After": "5"})
    assert limiter.next_allowed_ms("submit") == clock.now_ms() + 5_000
    assert limiter.acquire("submit") is False
    clock.advance(4_999)
    assert limiter.acquire("submit") is False
    clock.advance(1)
    assert limiter.acquire("submit") is True


def test_a_retry_after_hold_is_capped_by_the_one_ceiling():
    """A hostile or buggy server may ask for hours; a lane never grants more
    than `MAX_BACKOFF_S` (the same ceiling `Retry` obeys)."""
    limiter, clock = make_limiter()
    limiter.observe("cancel", {"Retry-After": "99999"})
    assert limiter.next_allowed_ms("cancel") == clock.now_ms() + int(
        MAX_BACKOFF_S * 1000
    )


def test_a_retry_after_http_date_is_read_as_an_instant():
    clock = FakeClock()
    limiter, _clock = make_limiter(clock=clock)
    when = formatdate((clock.now_ms() + 30_000) / 1000.0, usegmt=True)
    limiter.observe("submit", {"Retry-After": when})
    assert limiter.next_allowed_ms("submit") == clock.now_ms() + 30_000


def test_the_retry_after_header_is_read_whatever_its_case():
    limiter, clock = make_limiter()
    limiter.observe("submit", {"retry-after": "5"})
    assert limiter.next_allowed_ms("submit") == clock.now_ms() + 5_000


def test_headers_without_a_retry_after_leave_the_lane_alone():
    limiter, clock = make_limiter()
    limiter.observe("submit", {"Content-Type": "application/json"})
    assert limiter.next_allowed_ms("submit") == clock.now_ms()
    assert limiter.acquire("submit") is True


def test_an_unparsable_retry_after_is_ignored_rather_than_raised():
    """`observe` runs on the response path; a malformed header must never be
    the thing that kills a tick."""
    limiter, clock = make_limiter()
    limiter.observe("submit", {"Retry-After": "soon"})
    limiter.observe("submit", {"Retry-After": "-5"})
    assert limiter.next_allowed_ms("submit") == clock.now_ms()


def test_a_hold_on_one_lane_does_not_hold_the_other():
    limiter, _clock = make_limiter()
    limiter.observe("submit", {"Retry-After": "5"})
    assert limiter.acquire("submit") is False
    assert limiter.acquire("cancel") is True


def test_observe_refuses_a_lane_the_document_never_declared():
    limiter, _clock = make_limiter()
    with pytest.raises(ProductionError):
        limiter.observe("query", {"Retry-After": "5"})


# ---------------------------------------------------------------------------
# Transport — the socket boundary, exercised without a socket
# ---------------------------------------------------------------------------


class FakeResponse:
    """Shaped like `http.client.HTTPResponse`: status, headers, read()."""

    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self.code = status
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(status, headers=None, body=b""):
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError(
        "https://venue.example/orders", status, "nope", message, io.BytesIO(body)
    )


def capture_urlopen(monkeypatch, result):
    """Patch `urllib.request.urlopen`; record the call, return/raise `result`."""
    seen = {}

    def fake(request, *args, **kwargs):
        seen["request"] = request
        seen["args"] = args
        seen["kwargs"] = kwargs
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def effective_timeout(seen):
    """The socket deadline urlopen was given, keyword or positional."""
    if "timeout" in seen["kwargs"]:
        return seen["kwargs"]["timeout"]
    args = seen["args"]
    return args[1] if len(args) > 1 else None


TIMEOUT = {"connect_s": 2.0, "read_s": 5.0}


def test_send_is_abstract_so_an_incomplete_transport_cannot_construct():
    assert "send" in Transport.__abstractmethods__
    with pytest.raises(TypeError):
        Transport()


def test_the_urllib_transport_is_the_only_registered_kind():
    assert TRANSPORT_KINDS.kinds() == ("urllib",)
    assert TRANSPORT_KINDS.family == "transport"
    assert TRANSPORT_KINDS.resolve("urllib") is UrllibTransport
    assert issubclass(UrllibTransport, Transport)
    assert "requests" not in TRANSPORT_KINDS


def test_the_registry_resolves_a_transport_by_class_reference():
    assert (
        TRANSPORT_KINDS.resolve("dskit.production.resilience:UrllibTransport")
        is UrllibTransport
    )


def test_the_registry_refuses_an_unregistered_transport_name():
    with pytest.raises(ProductionError):
        TRANSPORT_KINDS.resolve("requests")


def test_the_transport_refuses_an_unknown_knob_by_name():
    assert "verify" in refusal(lambda: UrllibTransport({"verify": False}))


def test_the_transport_timeouts_are_named_constants():
    assert DEFAULT_CONNECT_S == 2.0
    assert DEFAULT_READ_S == 5.0
    UrllibTransport({})


@pytest.mark.parametrize(
    "timeout", (None, {}, {"connect_s": 2.0}, {"read_s": 5.0}, {"connect_s": 0, "read_s": 5.0}, 5.0)
)
def test_a_missing_or_unusable_timeout_refuses(timeout):
    """§5.12: '`None` timeout refused'. A socket with no deadline is how a
    tick becomes permanent."""
    with pytest.raises(ProductionError):
        UrllibTransport({}).send("GET", "https://venue.example/x", {}, None, timeout)


def test_send_returns_the_status_headers_and_body_of_a_success(monkeypatch):
    response = FakeResponse(200, {"Content-Type": "application/json"}, b'{"ok":1}')
    seen = capture_urlopen(monkeypatch, response)
    status, headers, body = UrllibTransport({}).send(
        "GET", "https://venue.example/orders", {"Accept": "application/json"}, None,
        TIMEOUT,
    )
    assert status == 200
    assert isinstance(headers, dict)
    assert headers["Content-Type"] == "application/json"
    assert body == b'{"ok":1}'
    assert seen["request"] is not None


def test_send_passes_the_widest_of_the_two_deadlines_to_urllib(monkeypatch):
    """urllib exposes ONE socket timeout, so the transport passes
    `max(connect_s, read_s)`: the narrower value would truncate a read the
    document allowed."""
    seen = capture_urlopen(monkeypatch, FakeResponse(200))
    UrllibTransport({}).send(
        "GET", "https://venue.example/x", {}, None, {"connect_s": 2.0, "read_s": 5.0}
    )
    assert effective_timeout(seen) == pytest.approx(5.0)
    seen = capture_urlopen(monkeypatch, FakeResponse(200))
    UrllibTransport({}).send(
        "GET", "https://venue.example/x", {}, None, {"connect_s": 9.0, "read_s": 5.0}
    )
    assert effective_timeout(seen) == pytest.approx(9.0)


@pytest.mark.parametrize("code", (429, 500, 404))
def test_a_non_success_status_comes_back_as_a_value_not_an_exception(monkeypatch, code):
    """The classifier decides what a 429 means; a transport that raised would
    make `throttled` indistinguishable from a connection fault."""
    capture_urlopen(monkeypatch, http_error(code, {"Retry-After": "5"}, b"slow down"))
    status, headers, body = UrllibTransport({}).send(
        "POST", "https://venue.example/orders", {}, b"{}", TIMEOUT
    )
    assert status == code
    assert headers["Retry-After"] == "5"
    assert body == b"slow down"


def test_a_connection_fault_propagates_so_the_classifier_can_see_it(monkeypatch):
    capture_urlopen(monkeypatch, urllib.error.URLError("refused"))
    with pytest.raises(urllib.error.URLError):
        UrllibTransport({}).send("GET", "https://venue.example/x", {}, None, TIMEOUT)


def test_a_socket_timeout_propagates(monkeypatch):
    capture_urlopen(monkeypatch, socket.timeout("timed out"))
    with pytest.raises(OSError):
        UrllibTransport({}).send("GET", "https://venue.example/x", {}, None, TIMEOUT)


def test_the_body_reaches_urllib_for_a_write(monkeypatch):
    seen = capture_urlopen(monkeypatch, FakeResponse(201))
    UrllibTransport({}).send(
        "POST", "https://venue.example/orders", {"X-Client": "dskit"}, b'{"qty":1}',
        TIMEOUT,
    )
    request = seen["request"]
    assert request.data == b'{"qty":1}'
    assert request.get_method() == "POST"


# ---------------------------------------------------------------------------
# ResiliencePolicies — the one value the Execution bundle carries
# ---------------------------------------------------------------------------


def build_policies(section=None):
    return resilience_from_document(
        resilience_section() if section is None else section,
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        rng=FakeRng(),
    )


def test_the_bundle_member_is_one_frozen_value_with_the_four_policies():
    """§5.16: `Execution` carries the §5.12 set 'held as one
    `ResiliencePolicies` value'."""
    policies = build_policies()
    assert isinstance(policies, ResiliencePolicies)
    assert tuple(f.name for f in dataclasses.fields(ResiliencePolicies)) == (
        "retry",
        "breaker",
        "limiter",
        "transport",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        policies.retry = None


def test_the_factory_builds_each_policy_from_its_own_block():
    policies = build_policies()
    assert isinstance(policies.retry, Retry)
    assert isinstance(policies.breaker, CircuitBreakers)
    assert isinstance(policies.limiter, RateLimiter)
    assert isinstance(policies.transport, UrllibTransport)


def test_the_factory_reads_the_documents_knobs_rather_than_the_constants():
    """Change one number in the section and the built policy behaves
    differently — the proof that nothing is compiled in."""
    tight = build_policies(
        resilience_section(retry=retry_params(max_attempts=1))
    )
    assert tight.retry.decide(1, "transient", False) == "give_up"
    loose = build_policies(
        resilience_section(retry=retry_params(max_attempts=5))
    )
    assert loose.retry.decide(3, "transient", False) == "retry"


def test_the_factory_resolves_the_transport_through_its_registry():
    section = resilience_section(
        transport={
            "uses": "dskit.production.resilience:UrllibTransport",
            "params": {"connect_s": 1.0, "read_s": 2.0},
        }
    )
    assert isinstance(build_policies(section).transport, UrllibTransport)


def test_the_factory_refuses_an_unknown_section_key_by_name():
    section = resilience_section()
    section["signer"] = {"uses": "hmac"}
    assert "signer" in refusal(lambda: build_policies(section))


def test_the_factory_refuses_an_unregistered_transport():
    section = resilience_section(transport={"uses": "requests", "params": {}})
    with pytest.raises(ProductionError):
        build_policies(section)


def test_the_factory_refuses_a_section_whose_knobs_are_wrong():
    section = resilience_section(retry=retry_params(cap_s=MAX_BACKOFF_S + 1))
    assert "cap_s" in refusal(lambda: build_policies(section))


# ---------------------------------------------------------------------------
# What `cancel_all` leans on (§5.12) — the executor owns the loop; these are
# the decisions it asks these objects for, pinned here.
# ---------------------------------------------------------------------------


def test_cancel_all_retries_a_throttled_cancel_within_its_attempts():
    """'transient/429 outcomes retry only within configured attempts'. A
    cancel is a write, so this depends on `retry_writes`."""
    policy, _c, _s, _r = make_retry(
        retry_params(max_attempts=3, retry_writes="idempotent_only")
    )
    assert policy.decide(1, "throttled", True) == "retry"
    assert policy.decide(2, "throttled", True) == "retry"
    assert policy.decide(3, "throttled", True) == "give_up"


def test_cancel_all_waits_the_capped_retry_after_a_429_asked_for():
    policy, _clock, sleeper, _r = make_retry(
        retry_params(jitter="none", retry_after="honor", cap_s=20.0)
    )
    policy.wait(1, "throttled", retry_after=600)
    assert sleeper.slept == [pytest.approx(20.0)]


def test_cancel_all_never_resends_after_an_ambiguous_cancel_it_reconciles():
    """'ambiguous outcomes query then reconcile' — a resent cancel is safe,
    but the plan's rule is one rule for every write, so it holds here too."""
    policy, _c, _s, _r = make_retry(retry_params(max_attempts=5))
    assert policy.decide(1, "ambiguous", True) == "reconcile"


def test_cancel_all_gives_up_at_the_cap_rather_than_flooding_the_venue():
    policy, _c, _s, _r = make_retry(retry_params(max_attempts=2))
    assert policy.decide(2, "transient", True) == "give_up"


def test_cancel_all_can_still_send_when_the_submit_lane_is_exhausted():
    limiter, _clock = make_limiter(
        limiter_params(
            submit={"rate_per_s": 1, "burst": 1, "max_in_flight": 1},
            cancel={"rate_per_s": 1, "burst": 3, "max_in_flight": 1, "reserved": True},
        )
    )
    assert limiter.acquire("submit") is True
    assert limiter.acquire("submit") is False
    sent = 0
    while limiter.acquire("cancel"):
        sent += 1
        limiter.release("cancel")
    assert sent == 3, "sequential cancels, bounded by the cancel burst"
