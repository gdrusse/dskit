"""The four resilience policies between the loop and its venue (plan §5.12, D19).

A venue can be slow (a socket that never answers), rude (a ``429`` asking
for a four-hour ``Retry-After``) or lying (a ``200`` whose bytes never came
back). Four small objects absorb that, and every one of them is pure stdlib
with an injected ``clock``, ``sleeper`` and ``rng``, so a test can drive
them to an exact number and a serve process never reaches ``time.sleep``
or ``random`` on its own:

* :class:`Retry` — ``decide(attempt, outcome, is_write)`` answers ``retry``,
  ``give_up`` or ``reconcile``; ``backoff_s`` / ``wait`` say how long. The
  one rule above the rest: an **ambiguous write never retries**. A submit
  that raised after the bytes left may already be a position, so the
  answer is ``reconcile`` at every attempt, under every ``retry_writes``
  mode, with any budget (D13, D19). A retry budget makes a storm cost more
  than it earns.
* :class:`CircuitBreaker` — one per scope (:class:`CircuitBreakers` hands
  them out), opening when a dependency fails at ``failure_rate`` over at
  least ``min_calls`` calls. A business rejection (``fatal``) is an answer,
  not a failure, and never opens it.
* :class:`RateLimiter` — a token bucket and a write bulkhead per lane. The
  ``cancel`` lane is reserved, so a halt can still cancel when submits are
  throttled to a standstill, and is still bounded by its own burst, so it
  cannot become the flood.
* :class:`Transport` — the socket boundary, ``send(...) -> (status,
  headers, body)``; :class:`UrllibTransport` is the core kind. A non-2xx
  comes back as a value for :class:`HttpClassifier` to name, never as an
  exception, and ``send`` refuses a call with no deadline.

Every wait is bounded by ONE ceiling: ``MAX_BACKOFF_S`` is imported from
``dskit.onboarding.connector`` and caps the exponential, the jitter, a
server-sent ``Retry-After`` and a limiter hold alike. Every knob is read
from the graded ``resilience`` section of the serve document (§4.1), whose
key sets ``document.py`` owns; the ``DEFAULT_*`` names below are the only
numbers this module holds, and validation and behaviour read the same
name. Dispatch over outcomes, circuit states and jitter modes goes through
tables, never a chain of ``if``.
"""

import hashlib
import hmac
import math
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime

from dskit.onboarding.connector import MAX_BACKOFF_S
from dskit.pipeline.node import check_int_param
from dskit.production.base import (
    ProductionError,
    Registry,
    _check_str,
    pin_members,
    reject_unknown_params,
)
from dskit.production.document import (
    BREAKER_KEYS,
    LIMITER_LANE_KEYS,
    RETRY_BUDGET_KEYS,
    RETRY_KEYS,
)
from dskit.production.redact import register_secret, resolve_secrets
from dskit.production.vocab import (
    JITTER_MODES,
    RETRY_AFTER_MODES,
    RETRY_WRITE_MODES,
    SIGNER_ALGORITHMS,
)

__all__ = [
    "DEFAULT_BASE_S",
    "DEFAULT_BUDGET_CAPACITY",
    "DEFAULT_CAP_S",
    "DEFAULT_CONNECT_S",
    "DEFAULT_FAILURE_RATE",
    "DEFAULT_JITTER",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_IN_FLIGHT",
    "DEFAULT_MAX_SKEW_MS",
    "DEFAULT_MIN_CALLS",
    "DEFAULT_OPEN_S",
    "DEFAULT_READ_S",
    "DEFAULT_REFUND",
    "DEFAULT_RESERVED",
    "DATE_HEADER_RESOLUTION_MS",
    "DEFAULT_RETRY_AFTER",
    "DEFAULT_RETRY_WRITES",
    "DEFAULT_SIGNER_ALGORITHM",
    "DEFAULT_SIGNER_PREFIX",
    "DEFAULT_THROTTLE_BASE_S",
    "DEFAULT_THROTTLE_COST",
    "DEFAULT_TRANSIENT_COST",
    "MAX_ATTEMPTS_BOUNDS",
    "MAX_BACKOFF_S",
    "SIGNER_KINDS",
    "TRANSPORT_KINDS",
    "CallOutcome",
    "CircuitBreaker",
    "CircuitBreakers",
    "Classifier",
    "HmacSigner",
    "HttpClassifier",
    "RateLimiter",
    "ResiliencePolicies",
    "Retry",
    "RetryBudget",
    "Signer",
    "Transport",
    "UrllibTransport",
    "resilience_from_document",
]

# --- Retry (§5.12): attempts, waits, modes, budget -------------------------
#: Attempts per call, inclusive bounds and the default.
MAX_ATTEMPTS_BOUNDS = (1, 10)
DEFAULT_MAX_ATTEMPTS = 3
#: First wait after a transient fault, in seconds; doubles per attempt.
DEFAULT_BASE_S = 0.05
#: First wait after a throttle (``429``): an order of magnitude longer.
DEFAULT_THROTTLE_BASE_S = 1.0
#: The document's cap on one wait; itself bounded by ``MAX_BACKOFF_S``.
DEFAULT_CAP_S = 20.0
DEFAULT_JITTER = "full"
DEFAULT_RETRY_AFTER = "honor"
DEFAULT_RETRY_WRITES = "idempotent_only"
#: The retry budget: a transient retry costs more than a throttled one,
#: every success refunds a little, and the balance never exceeds capacity.
DEFAULT_BUDGET_CAPACITY = 500
DEFAULT_TRANSIENT_COST = 14
DEFAULT_THROTTLE_COST = 5
DEFAULT_REFUND = 1

# --- CircuitBreaker (§5.12) -------------------------------------------------
#: Calls before the failure rate is judged at all; small, per D19.
DEFAULT_MIN_CALLS = 5
#: The failure fraction at or above which the circuit opens, in ``(0, 1]``.
DEFAULT_FAILURE_RATE = 0.5
#: Seconds an open circuit waits before admitting one probe.
DEFAULT_OPEN_S = 30

# --- RateLimiter (§5.12, D19) -----------------------------------------------
#: The write bulkhead: one call in flight per lane unless the lane says more.
DEFAULT_MAX_IN_FLIGHT = 1
#: A lane is not reserved unless the document says so (``cancel`` is, in §4.1).
DEFAULT_RESERVED = False

# --- Transport (§5.12) ------------------------------------------------------
#: Socket deadlines in seconds; urllib takes the wider of the two.
DEFAULT_CONNECT_S = 2.0
DEFAULT_READ_S = 5.0

# --- Signer (§5.12.1) -------------------------------------------------------
#: The hash a signer computes with when the document names none.
DEFAULT_SIGNER_ALGORITHM = pin_members(
    "resilience.py's DEFAULT_SIGNER_ALGORITHM", ("sha256",), SIGNER_ALGORITHMS
)[0]
#: The widest venue clock skew a signature may be stamped at, in ms. Five
#: seconds sits well inside the windows venues publish, so a refusal here
#: means the local clock has genuinely drifted rather than that the bound is
#: tight; a document that knows its venue's window may widen it.
DEFAULT_MAX_SKEW_MS = 5_000
#: The literal a venue requires before the payload: none, unless asked for.
DEFAULT_SIGNER_PREFIX = ""
#: How finely an HTTP ``Date`` header can place an instant. An HTTP-date is
#: a whole number of SECONDS (RFC 7231), so a skew measured from one is good
#: to a second and no better — which is a BOUND on what any ``max_skew_ms``
#: read against it can mean, not merely a caveat. ``Signer.TIME_RESOLUTION_MS``
#: is what a subclass with a finer source lowers.
DATE_HEADER_RESOLUTION_MS = 1_000

#: The two hashes, keyed by the vocabulary — a table, never an ``if``.
_ALGORITHMS = {"sha256": hashlib.sha256, "sha512": hashlib.sha512}
pin_members("resilience.py's signer algorithms", _ALGORITHMS, SIGNER_ALGORITHMS, exact=True)

#: What ``probe_request()`` must supply: ``Transport.send``'s arguments, so
#: the base can send it without knowing anything about the venue.
_PROBE_KEYS = ("method", "url", "headers", "body", "timeout")

_NOTES = ("notes",)
#: The two lanes §5.12 gives the limiter; ``document.py`` spells the same
#: pair inside its limiter grammar (a one-owner home is review debt).
_LANES = ("submit", "cancel")
#: The blocks of §4.1's ``resilience`` section, in the order they are built.
_SECTION_KEYS = ("retry", "breaker", "limiter", "transport")
_SITE_KEYS = ("uses", "params")
_TIMEOUT_KEYS = ("connect_s", "read_s")
_MS_PER_S = 1000
#: The one ceiling, as a limiter hold in milliseconds.
_MAX_HOLD_MS = int(MAX_BACKOFF_S * _MS_PER_S)


# ---------------------------------------------------------------------------
# Shared validation — small rules, each with one home in this module
# ---------------------------------------------------------------------------


def _is_number(value):
    """Return whether ``value`` is an int or a float and not a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_positive(problems, name, value, le=None):
    """Append a problem unless ``value`` is a finite number > 0 (and <= ``le`` if given)."""
    if not _is_number(value) or not math.isfinite(value) or value <= 0:
        problems.append(f"{name} must be a positive number, got {value!r}")
    elif le is not None and value > le:
        problems.append(f"{name} must be <= {le}, got {value!r}")


def _check_int_between(problems, name, value, bounds):
    """Append a problem unless ``value`` is an int inside the inclusive ``bounds``."""
    low, high = bounds
    before = len(problems)
    check_int_param(problems, name, value, ge=low)
    if len(problems) == before and value > high:
        problems.append(f"{name} must be an int <= {high}, got {value!r}")


def _check_choice(problems, name, value, choices):
    """Append a problem unless ``value`` is one of ``choices``."""
    if value not in choices:
        problems.append(f"{name} must be one of {list(choices)}, got {value!r}")


def _check_bool(problems, name, value):
    """Append a problem unless ``value`` is a bool."""
    if not isinstance(value, bool):
        problems.append(f"{name} must be true or false, got {value!r}")


def _check_deadlines(problems, knobs):
    """Append a problem for each of ``connect_s`` / ``read_s`` that is not positive."""
    for name in _TIMEOUT_KEYS:
        _check_positive(problems, name, knobs.get(name))


def _check_attempt(attempt):
    """Refuse an attempt number that is not a 1-based int."""
    problems = []
    check_int_param(problems, "attempt", attempt, ge=1)
    if problems:
        raise ProductionError(problems)


def _check_retry_after(retry_after):
    """Refuse a ``Retry-After`` that is not a finite number of seconds >= 0."""
    if not _is_number(retry_after) or not math.isfinite(retry_after) or retry_after < 0:
        raise ProductionError(
            [f"retry_after must be a finite number of seconds >= 0, got {retry_after!r}"]
        )


def _lookup(table, key, what):
    """Return ``table[key]``, refusing a key outside the table by name."""
    try:
        return table[key]
    except (KeyError, TypeError):
        raise ProductionError(
            [f"{what} must be one of {list(table)}, got {key!r}"]
        ) from None


class _Configured(ABC):
    """``cls(params)`` construction: default-deny over ``_PARAMS``, defaults, validate, configure."""

    _PARAMS = ()
    _DEFAULTS = {}

    def __init__(self, params=None):
        params = dict(params or {})
        problems = self.validate_params(params)
        if problems:
            raise ProductionError(problems)
        self._configure(self.effective(params))

    @classmethod
    def effective(cls, params):
        """Return ``params`` with every absent knob filled from the class defaults.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        dict
            A new dict: the class ``_DEFAULTS`` overlaid by ``params``, so
            validation and the run read the same value for every knob.
        """
        return {**cls._DEFAULTS, **params}

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when it is acceptable.

        The base refuses a non-dict and any key outside ``_PARAMS`` plus
        ``notes``; the subclass ``_check`` hook judges the effective knobs
        (defaults applied) and never raises.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending key.
        """
        if not isinstance(params, dict):
            return [f"params must be an object (dict), got {params!r}"]
        problems = []
        reject_unknown_params(problems, params, tuple(cls._PARAMS) + _NOTES)
        cls._check(problems, cls.effective(params))
        return problems

    @classmethod
    def _check(cls, problems, knobs):
        """Append problems with the effective knobs; the base has none to check."""

    def _configure(self, knobs):
        """Read the validated, defaulted knobs; the base has none to read."""


# ---------------------------------------------------------------------------
# Classifier — code first, status second
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CallOutcome:
    """What one transport call came to, as the classifier sees it.

    Parameters
    ----------
    status : int or None
        The HTTP status the venue answered with, or ``None`` if none came.
    exception : BaseException or None
        What the call raised, or ``None`` if it returned.
    request_sent : bool
        Whether the request bytes had left before the exception — the
        fact that separates a safe ``transient`` from an ``ambiguous``.

    Examples
    --------
    A ``200`` that came back whole, and a timeout after the bytes left::

        CallOutcome(200, None, True).status              # 200
        CallOutcome(exception=TimeoutError(), request_sent=True).request_sent  # True
    """

    status: int | None = None
    exception: BaseException | None = None
    request_sent: bool = False

    def __post_init__(self):
        """Refuse a shape the classifier could misread."""
        problems = []
        if self.status is not None and not (
            isinstance(self.status, int) and not isinstance(self.status, bool)
        ):
            problems.append(f"status must be an int or None, got {self.status!r}")
        if self.exception is not None and not isinstance(self.exception, BaseException):
            problems.append(f"exception must be an exception or None, got {self.exception!r}")
        _check_bool(problems, "request_sent", self.request_sent)
        if problems:
            raise ProductionError(problems)


class Classifier(ABC):
    """The seam that names a :class:`CallOutcome` (§5.12).

    A classifier is chosen by the transport's owner, not by a document
    ``uses``: §4.3 closes the registry list and no classifier family is in
    it. Subclass and implement :meth:`classify`.

    Examples
    --------
    A classifier for a venue that says everything in a JSON ``code``::

        class CodeClassifier(Classifier):
            def classify(self, outcome):
                return "ok" if outcome.status == 200 else "fatal"

        CodeClassifier().classify(CallOutcome(200, None, True))   # 'ok'
    """

    @abstractmethod
    def classify(self, outcome):
        """Return the ``vocab.RESILIENCE_OUTCOMES`` member ``outcome`` is.

        Parameters
        ----------
        outcome : CallOutcome
            The call as it ended.

        Returns
        -------
        str
            One of ``ok``, ``transient``, ``throttled``, ``fatal``, ``ambiguous``.
        """


#: Statuses with their own meaning, read before the hundreds class.
_EXACT_STATUS = {408: "transient", 429: "throttled"}
#: The hundreds class of a status -> outcome; anything else is ``fatal``.
_STATUS_CLASS = {2: "ok", 4: "fatal", 5: "transient"}
_OTHER_STATUS = "fatal"
#: What counts as a network fault before the request left: any OS-level
#: error, which is what a refused connection, a reset and a socket timeout
#: all are (``urllib.error.URLError`` included).
_NETWORK_FAULTS = (OSError,)


class HttpClassifier(Classifier):
    """The default HTTP classifier: the exception first, the status second.

    A raise after the request left is ``ambiguous`` whatever the status
    says — the bytes may have landed. A raise before it left is
    ``transient`` for a network fault and ``fatal`` for anything local (a
    body that would not build). Then ``408`` / ``429`` / ``5xx`` are
    retryable, ``2xx`` is ``ok``, and every other status — the other
    ``4xx``, an unfollowed redirect, an informational reply — is ``fatal``,
    because a second identical call would not fix it.

    Examples
    --------
    ::

        classify = HttpClassifier().classify
        classify(CallOutcome(status=429, request_sent=True))                  # 'throttled'
        classify(CallOutcome(exception=TimeoutError(), request_sent=True))    # 'ambiguous'
        classify(CallOutcome(exception=ConnectionError(), request_sent=False))  # 'transient'
    """

    def classify(self, outcome):
        """Return the outcome's class; an empty outcome refuses rather than guessing.

        Parameters
        ----------
        outcome : CallOutcome
            The call as it ended.

        Returns
        -------
        str
            A ``vocab.RESILIENCE_OUTCOMES`` member.

        Raises
        ------
        ProductionError
            If ``outcome`` is not a :class:`CallOutcome`, or carries neither
            a status nor an exception.
        """
        if not isinstance(outcome, CallOutcome):
            raise ProductionError([f"classify takes a CallOutcome, got {outcome!r}"])
        if outcome.exception is not None:
            return self._classify_exception(outcome)
        if outcome.status is None:
            raise ProductionError(
                ["an outcome with no status and no exception has nothing to classify"]
            )
        return _EXACT_STATUS.get(outcome.status) or _STATUS_CLASS.get(
            outcome.status // 100, _OTHER_STATUS
        )

    def _classify_exception(self, outcome):
        """Return the class of a call that raised: ambiguous once the bytes left."""
        if outcome.request_sent:
            return "ambiguous"
        return "transient" if isinstance(outcome.exception, _NETWORK_FAULTS) else "fatal"


# ---------------------------------------------------------------------------
# Retry — the budget, the decision, the wait
# ---------------------------------------------------------------------------


class RetryBudget(_Configured):
    """The retry budget: a storm of retries costs more than it earns (§5.12).

    A transient retry spends ``transient_cost``, a throttled one
    ``throttle_cost``; every success refunds ``refund``; the balance never
    exceeds ``capacity``. A retry the balance cannot pay for is refused and
    spends nothing.

    Parameters
    ----------
    params : dict, optional
        The ``retry.budget`` block: ``capacity`` (int >= 1), ``transient_cost``,
        ``throttle_cost`` and ``refund`` (ints >= 0), each defaulting to its
        ``DEFAULT_*`` name. ``None`` means ``{}``.

    Examples
    --------
    ::

        budget = RetryBudget({"capacity": 20, "transient_cost": 14})
        budget.spend(budget.transient_cost)   # True
        budget.balance                        # 6
        budget.spend(budget.transient_cost)   # False: 6 cannot pay 14
    """

    _PARAMS = RETRY_BUDGET_KEYS
    _DEFAULTS = {
        "capacity": DEFAULT_BUDGET_CAPACITY,
        "transient_cost": DEFAULT_TRANSIENT_COST,
        "throttle_cost": DEFAULT_THROTTLE_COST,
        "refund": DEFAULT_REFUND,
    }

    @classmethod
    def _check(cls, problems, knobs):
        """Append a problem for a capacity below one or a negative cost or refund."""
        check_int_param(problems, "capacity", knobs["capacity"], ge=1)
        for name in ("transient_cost", "throttle_cost", "refund"):
            check_int_param(problems, name, knobs[name], ge=0)

    def _configure(self, knobs):
        """Start full."""
        self._capacity = int(knobs["capacity"])
        self._transient_cost = int(knobs["transient_cost"])
        self._throttle_cost = int(knobs["throttle_cost"])
        self._refund = int(knobs["refund"])
        self._balance = self._capacity

    @property
    def capacity(self):
        """Return the ceiling the balance never exceeds."""
        return self._capacity

    @property
    def balance(self):
        """Return what is left to spend."""
        return self._balance

    @property
    def transient_cost(self):
        """Return the price of one transient (or ambiguous-read) retry."""
        return self._transient_cost

    @property
    def throttle_cost(self):
        """Return the price of one throttled retry."""
        return self._throttle_cost

    def spend(self, cost):
        """Pay ``cost`` if the balance covers it; otherwise pay nothing.

        Parameters
        ----------
        cost : int
            The price of the retry being considered.

        Returns
        -------
        bool
            ``True`` if it was paid, ``False`` if the balance could not.
        """
        if cost > self._balance:
            return False
        self._balance -= cost
        return True

    def refund(self):
        """Credit one success, never above capacity.

        Returns
        -------
        int
            The balance after the refund.
        """
        self._balance = min(self._capacity, self._balance + self._refund)
        return self._balance


def _full_jitter(rng, wait):
    """Return a uniform draw in ``[0, wait]``."""
    return rng.uniform(0, wait)


def _equal_jitter(rng, wait):
    """Return half the wait plus a uniform draw over the other half."""
    half = wait / 2
    return half + rng.uniform(0, half)


def _no_jitter(rng, wait):
    """Return the wait untouched; the rng is not consulted."""
    return wait


def _exponential(base, attempt, cap):
    """Return ``base`` doubled ``attempt - 1`` times, never above ``cap``."""
    wait = base
    for _ in range(attempt - 1):
        if wait >= cap:
            break
        wait *= 2
    return min(wait, cap)


#: ``jitter`` mode -> how a capped wait is randomised.
_JITTERS = {"full": _full_jitter, "equal": _equal_jitter, "none": _no_jitter}
#: ``retry_after`` mode -> whether a server-sent delay is obeyed.
_RETRY_AFTER_HONOURED = {"honor": True, "ignore": False}
#: ``retry_writes`` mode -> whether a transient/throttled WRITE may retry.
_WRITES_MAY_RETRY = {"never": False, "idempotent_only": True}
#: Outcome -> the knob its first wait starts from; ``ok`` needs no wait.
_BASE_KNOB = {
    "transient": "base_s",
    "throttled": "throttle_base_s",
    "fatal": "base_s",
    "ambiguous": "base_s",
}


class Retry(_Configured):
    """The retry policy: attempts, budget, backoff — and the ambiguous-write rule.

    ``decide`` is asked after every failed attempt and answers a
    ``vocab.RETRY_DECISIONS`` member; ``backoff_s`` computes the wait before
    the next attempt and ``wait`` sleeps it through the injected sleeper.
    An ambiguous WRITE answers ``reconcile`` always: the executor queries
    the venue, it never resends (D13). ``fatal`` never retries. A
    transient, throttled or ambiguous READ retries while attempts remain,
    the budget can pay, and — for a write — ``retry_writes`` allows it.

    Parameters
    ----------
    params : dict, optional
        The ``resilience.retry`` block (§4.1): ``max_attempts`` (int in
        ``MAX_ATTEMPTS_BOUNDS``), ``base_s``, ``throttle_base_s``, ``cap_s``
        (positive seconds; ``cap_s <= MAX_BACKOFF_S``), ``jitter``
        (``vocab.JITTER_MODES``), ``retry_after`` (``RETRY_AFTER_MODES``),
        ``retry_writes`` (``RETRY_WRITE_MODES``) and ``budget`` (see
        :class:`RetryBudget`); each defaults to its ``DEFAULT_*`` name.
    clock : Clock
        Injected for the seam's uniform contract (§5.12); the policy reads
        no time itself — the executor's deadline is the clock's consumer.
    sleeper : callable
        ``sleeper(seconds)``; what ``wait`` calls instead of ``time.sleep``.
    rng : object
        Provides ``uniform(a, b)``; a ``random.Random`` in production.

    Examples
    --------
    Three attempts with the jitter off, driven by test doubles::

        from dskit.production.clock import TestClock
        import random

        policy = Retry(
            {"max_attempts": 3, "jitter": "none"},
            clock=TestClock(), sleeper=lambda seconds: None, rng=random.Random(7),
        )
        policy.decide(1, "transient", False)   # 'retry'
        policy.decide(3, "transient", False)   # 'give_up'
        policy.decide(1, "ambiguous", True)    # 'reconcile'
        policy.backoff_s(2, "transient")       # 0.1
    """

    _PARAMS = RETRY_KEYS
    _DEFAULTS = {
        "max_attempts": DEFAULT_MAX_ATTEMPTS,
        "base_s": DEFAULT_BASE_S,
        "throttle_base_s": DEFAULT_THROTTLE_BASE_S,
        "cap_s": DEFAULT_CAP_S,
        "jitter": DEFAULT_JITTER,
        "retry_after": DEFAULT_RETRY_AFTER,
        "retry_writes": DEFAULT_RETRY_WRITES,
        "budget": {},
    }

    def __init__(self, params=None, *, clock, sleeper, rng):
        problems = []
        if not callable(sleeper):
            problems.append(f"sleeper must be callable as sleeper(seconds), got {sleeper!r}")
        if not callable(getattr(rng, "uniform", None)):
            problems.append(f"rng must provide uniform(a, b), got {rng!r}")
        if problems:
            raise ProductionError(problems)
        self._clock = clock
        self._sleeper = sleeper
        self._rng = rng
        super().__init__(params)

    @classmethod
    def _check(cls, problems, knobs):
        """Append every problem with the retry knobs, the budget's included."""
        _check_int_between(problems, "max_attempts", knobs["max_attempts"], MAX_ATTEMPTS_BOUNDS)
        _check_positive(problems, "base_s", knobs["base_s"])
        _check_positive(problems, "throttle_base_s", knobs["throttle_base_s"])
        _check_positive(problems, "cap_s", knobs["cap_s"], le=MAX_BACKOFF_S)
        _check_choice(problems, "jitter", knobs["jitter"], JITTER_MODES)
        _check_choice(problems, "retry_after", knobs["retry_after"], RETRY_AFTER_MODES)
        _check_choice(problems, "retry_writes", knobs["retry_writes"], RETRY_WRITE_MODES)
        budget = knobs["budget"]
        if not isinstance(budget, dict):
            problems.append(f"budget must be an object (dict), got {budget!r}")
        else:
            problems.extend(f"budget: {p}" for p in RetryBudget.validate_params(budget))

    def _configure(self, knobs):
        """Resolve every mode to its strategy once."""
        self._max_attempts = int(knobs["max_attempts"])
        self._cap_s = float(knobs["cap_s"])
        self._bases = {outcome: float(knobs[knob]) for outcome, knob in _BASE_KNOB.items()}
        self._jitter = _JITTERS[knobs["jitter"]]
        self._honours_retry_after = _RETRY_AFTER_HONOURED[knobs["retry_after"]]
        self._writes_may_retry = _WRITES_MAY_RETRY[knobs["retry_writes"]]
        self._budget = RetryBudget(knobs["budget"])

    @property
    def budget(self):
        """Return the :class:`RetryBudget` this policy spends from."""
        return self._budget

    def decide(self, attempt, outcome, is_write):
        """Return what to do after attempt number ``attempt`` ended in ``outcome``.

        Parameters
        ----------
        attempt : int
            The ONE-based number of the attempt that just failed, so
            ``max_attempts`` tries yield decisions ``1..max_attempts`` and
            the last of them is ``give_up``.
        outcome : str
            A ``vocab.RESILIENCE_OUTCOMES`` member other than ``ok``.
        is_write : bool
            Whether the call could have moved money or a position.

        Returns
        -------
        str
            ``retry``, ``give_up`` or ``reconcile`` (``vocab.RETRY_DECISIONS``);
            ``reconcile`` only ever for an ambiguous write.

        Raises
        ------
        ProductionError
            If ``attempt`` is below one, ``outcome`` is ``ok`` or unknown,
            or ``is_write`` is not a bool.
        """
        _check_attempt(attempt)
        if not isinstance(is_write, bool):
            raise ProductionError([f"is_write must be a bool, got {is_write!r}"])
        decider = _lookup(self._DECIDERS, outcome, "outcome")
        return decider(self, attempt, is_write)

    def _decide_fatal(self, attempt, is_write):
        """Never retry a fatal; nothing is spent."""
        return "give_up"

    def _decide_ambiguous(self, attempt, is_write):
        """Reconcile an ambiguous write; an ambiguous read may retry, nothing moved."""
        if is_write:
            return "reconcile"
        return self._decide_retryable(attempt, is_write, self._budget.transient_cost)

    def _decide_transient(self, attempt, is_write):
        """Retry a transient at the transient cost."""
        return self._decide_retryable(attempt, is_write, self._budget.transient_cost)

    def _decide_throttled(self, attempt, is_write):
        """Retry a throttle at the throttle cost."""
        return self._decide_retryable(attempt, is_write, self._budget.throttle_cost)

    def _decide_retryable(self, attempt, is_write, cost):
        """Retry while writes are allowed, attempts remain and the budget pays."""
        if is_write and not self._writes_may_retry:
            return "give_up"
        if attempt >= self._max_attempts:
            return "give_up"
        return "retry" if self._budget.spend(cost) else "give_up"

    #: Outcome -> decider; ``ok`` is absent because success needs no decision.
    _DECIDERS = {
        "transient": _decide_transient,
        "throttled": _decide_throttled,
        "fatal": _decide_fatal,
        "ambiguous": _decide_ambiguous,
    }

    def backoff_s(self, attempt, outcome, retry_after=None):
        """Return the seconds to wait before the attempt after ``attempt``.

        The exponential starts from ``base_s`` (``throttle_base_s`` for a
        throttle), doubles per attempt, is capped at ``cap_s`` BEFORE the
        jitter, then jittered per the mode. A honoured ``retry_after`` is
        obeyed as given — not jittered — and still capped at ``cap_s``,
        which is itself bounded by ``MAX_BACKOFF_S``: nothing here ever
        exceeds the one ceiling.

        Parameters
        ----------
        attempt : int
            The one-based number of the attempt that just failed.
        outcome : str
            A ``vocab.RESILIENCE_OUTCOMES`` member other than ``ok``.
        retry_after : float or None, optional
            A server-sent ``Retry-After`` in seconds, if any.

        Returns
        -------
        float
            Seconds, in ``[0, cap_s]``.

        Raises
        ------
        ProductionError
            If ``attempt`` is below one, ``outcome`` is ``ok`` or unknown,
            or ``retry_after`` is not a finite number >= 0.
        """
        _check_attempt(attempt)
        base = _lookup(self._bases, outcome, "outcome")
        if retry_after is not None:
            _check_retry_after(retry_after)
            if self._honours_retry_after:
                return min(float(retry_after), self._cap_s)
        return self._jitter(self._rng, _exponential(base, attempt, self._cap_s))

    def wait(self, attempt, outcome, retry_after=None):
        """Sleep the backoff through the injected sleeper and return it.

        Parameters
        ----------
        attempt : int
            As for :meth:`backoff_s`.
        outcome : str
            As for :meth:`backoff_s`.
        retry_after : float or None, optional
            As for :meth:`backoff_s`.

        Returns
        -------
        float
            The seconds the sleeper was asked for.

        Raises
        ------
        ProductionError
            As for :meth:`backoff_s`.
        """
        seconds = self.backoff_s(attempt, outcome, retry_after)
        self._sleeper(seconds)
        return seconds

    def refund(self):
        """Credit one success to the budget.

        Returns
        -------
        int
            The budget's balance after the refund.
        """
        return self._budget.refund()


# ---------------------------------------------------------------------------
# CircuitBreaker — a dependency that is failing stops being called
# ---------------------------------------------------------------------------

#: Outcome -> ``(calls, failures)`` it adds. A business rejection is an
#: answer, so ``fatal`` counts as neither (§5.12: not counted).
_TALLY = {
    "ok": (1, 0),
    "transient": (1, 1),
    "throttled": (1, 1),
    "ambiguous": (1, 1),
    "fatal": (0, 0),
}


class CircuitBreaker(_Configured):
    """One network scope's circuit (``vocab.CIRCUIT_STATES``), never the venue's.

    ``closed`` admits calls and tallies them; at ``min_calls`` or more with
    ``failures / calls >= failure_rate`` it opens. ``open`` refuses for
    ``open_s`` seconds, then admits exactly one probe (``half_open``): an
    answer closes it with clean counts, a fault reopens it for another
    ``open_s``. ``trip`` forces it open until ``reset``; ``observe_only``
    puts it in ``metrics_only``, where it tallies and never blocks, so an
    operator can watch what it would have done.

    Parameters
    ----------
    params : dict, optional
        The ``resilience.breaker`` block: ``min_calls`` (int >= 1),
        ``failure_rate`` (in ``(0, 1]``), ``open_s`` (positive seconds);
        each defaults to its ``DEFAULT_*`` name.
    clock : Clock
        Injected; ``now_ms()`` times the open window.

    Examples
    --------
    ::

        from dskit.production.clock import TestClock

        clock = TestClock()
        breaker = CircuitBreaker({"min_calls": 2, "open_s": 30}, clock=clock)
        breaker.record("transient")
        breaker.record("transient")
        breaker.state    # 'open'
        breaker.allow()  # False
        clock.advance(30_000)
        breaker.allow()  # True: the one probe
        breaker.record("ok")
        breaker.state    # 'closed'
    """

    _PARAMS = BREAKER_KEYS
    _DEFAULTS = {
        "min_calls": DEFAULT_MIN_CALLS,
        "failure_rate": DEFAULT_FAILURE_RATE,
        "open_s": DEFAULT_OPEN_S,
    }

    def __init__(self, params=None, *, clock):
        self._clock = clock
        super().__init__(params)

    @classmethod
    def _check(cls, problems, knobs):
        """Append a problem for a knob outside its bounds."""
        check_int_param(problems, "min_calls", knobs["min_calls"], ge=1)
        _check_positive(problems, "failure_rate", knobs["failure_rate"], le=1.0)
        _check_positive(problems, "open_s", knobs["open_s"])

    def _configure(self, knobs):
        """Start closed with clean counts."""
        self._min_calls = int(knobs["min_calls"])
        self._failure_rate = float(knobs["failure_rate"])
        self._open_ms = knobs["open_s"] * _MS_PER_S
        self._opened_at_ms = None
        self._close()

    @property
    def state(self):
        """Return the current ``vocab.CIRCUIT_STATES`` member."""
        return self._state

    @property
    def calls(self):
        """Return the counted calls since the circuit last closed or opened."""
        return self._calls

    @property
    def failures(self):
        """Return the counted failures since the circuit last closed or opened."""
        return self._failures

    def allow(self):
        """Say whether a call may go out now.

        Returns
        -------
        bool
            ``True`` in ``closed`` and ``metrics_only``; ``False`` in
            ``forced_open`` and ``half_open`` (the probe is out); in
            ``open``, ``True`` exactly once after ``open_s`` has elapsed.
        """
        return self._ALLOW[self._state](self)

    def record(self, outcome):
        """Tally a call's outcome and move the circuit if it warrants.

        Parameters
        ----------
        outcome : str
            A ``vocab.RESILIENCE_OUTCOMES`` member.

        Raises
        ------
        ProductionError
            If ``outcome`` is outside the closed vocabulary.
        """
        calls, failures = _lookup(_TALLY, outcome, "outcome")
        self._calls += calls
        self._failures += failures
        self._AFTER_RECORD[self._state](self, bool(failures))

    def trip(self):
        """Force the circuit open; only :meth:`reset` releases it."""
        self._state = "forced_open"

    def reset(self):
        """Return the circuit to ``closed`` with clean counts, from any state."""
        self._close()

    def observe_only(self):
        """Put the circuit in ``metrics_only``: tally everything, block nothing."""
        self._state = "metrics_only"

    def _close(self):
        """Enter ``closed`` with clean counts."""
        self._state = "closed"
        self._calls = 0
        self._failures = 0

    def _open(self):
        """Enter ``open`` now; the window and the counts start afresh."""
        self._state = "open"
        self._opened_at_ms = self._clock.now_ms()
        self._calls = 0
        self._failures = 0

    def _allow_always(self):
        """Admit the call."""
        return True

    def _allow_never(self):
        """Refuse the call."""
        return False

    def _allow_probe(self):
        """Admit one probe once ``open_s`` has elapsed, moving to ``half_open``."""
        if self._clock.now_ms() - self._opened_at_ms >= self._open_ms:
            self._state = "half_open"
            return True
        return False

    def _after_closed(self, faulted):
        """Open once enough calls have failed at the rate."""
        if self._calls >= self._min_calls and self._failures / self._calls >= self._failure_rate:
            self._open()

    def _after_half_open(self, faulted):
        """Judge the probe: a fault reopens, an answer closes."""
        if faulted:
            self._open()
        else:
            self._close()

    def _after_tally(self, faulted):
        """Keep the state; the counts were the point."""

    #: State -> what ``allow`` answers.
    _ALLOW = {
        "closed": _allow_always,
        "metrics_only": _allow_always,
        "forced_open": _allow_never,
        "half_open": _allow_never,
        "open": _allow_probe,
    }
    #: State -> what a recorded outcome does after the tally.
    _AFTER_RECORD = {
        "closed": _after_closed,
        "half_open": _after_half_open,
        "open": _after_tally,
        "forced_open": _after_tally,
        "metrics_only": _after_tally,
    }


class CircuitBreakers:
    """One :class:`CircuitBreaker` per scope, all on the document's knobs.

    A venue's order endpoint failing must not stop its cancel endpoint, so
    every scope — a lane, an endpoint, whatever the executor names — has
    its own circuit; the same scope always gets the same one.

    Parameters
    ----------
    params : dict, optional
        The ``resilience.breaker`` block, validated once here and handed to
        every circuit.
    clock : Clock
        Injected; shared by every circuit.

    Examples
    --------
    ::

        from dskit.production.clock import TestClock

        breakers = CircuitBreakers({"min_calls": 5}, clock=TestClock())
        breakers.for_scope("orders") is breakers.for_scope("orders")   # True
        breakers.for_scope("cancels").state                            # 'closed'
    """

    def __init__(self, params=None, *, clock):
        params = dict(params or {})
        problems = CircuitBreaker.validate_params(params)
        if problems:
            raise ProductionError(problems)
        self._params = params
        self._clock = clock
        self._scopes = {}

    def for_scope(self, scope):
        """Return the circuit for ``scope``, creating it on first sight.

        Parameters
        ----------
        scope : str
            A non-empty scope name.

        Returns
        -------
        CircuitBreaker
            The same object for the same scope, every time.

        Raises
        ------
        ProductionError
            If ``scope`` is not a non-empty string.
        """
        if not isinstance(scope, str) or not scope:
            raise ProductionError([f"a breaker scope is a non-empty string, got {scope!r}"])
        if scope not in self._scopes:
            self._scopes[scope] = CircuitBreaker(self._params, clock=self._clock)
        return self._scopes[scope]


# ---------------------------------------------------------------------------
# RateLimiter — two lanes, the cancel one reserved and still bounded
# ---------------------------------------------------------------------------


def _header(headers, wanted):
    """Return the value of header ``wanted`` (lower-case), whatever its case, or None."""
    items = getattr(headers, "items", None)
    if items is None:
        return None
    for name, value in items():
        if isinstance(name, str) and name.lower() == wanted:
            return value
    return None


def _delay_seconds(text):
    """Return ``text`` as a finite number of seconds >= 0, or None."""
    try:
        seconds = float(text)
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _http_date_ms(text):
    """Return an RFC 7231 HTTP-date as epoch milliseconds, or None if unparsable."""
    try:
        stamp = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * _MS_PER_S)


def _retry_after_ms(headers, now_ms):
    """Return the ``Retry-After`` delay in ms, or None when absent, malformed or past."""
    text = _header(headers, "retry-after")
    if text is None:
        return None
    seconds = _delay_seconds(text)
    if seconds is not None:
        return int(seconds * _MS_PER_S)
    instant_ms = _http_date_ms(text)
    if instant_ms is None or instant_ms <= now_ms:
        return None
    return instant_ms - now_ms


class _Lane(_Configured):
    """One lane: a token bucket (GCRA form, exact in ms), a bulkhead and a hold."""

    _PARAMS = LIMITER_LANE_KEYS
    _DEFAULTS = {"max_in_flight": DEFAULT_MAX_IN_FLIGHT, "reserved": DEFAULT_RESERVED}

    @classmethod
    def _check(cls, problems, knobs):
        """Append a problem for a missing rate or burst, or a knob outside its bounds."""
        _check_positive(problems, "rate_per_s", knobs.get("rate_per_s"))
        check_int_param(problems, "burst", knobs.get("burst"), ge=1)
        check_int_param(problems, "max_in_flight", knobs["max_in_flight"], ge=1)
        _check_bool(problems, "reserved", knobs["reserved"])

    def _configure(self, knobs):
        """Start with a full bucket, nothing in flight and no hold."""
        self._interval_ms = _MS_PER_S / knobs["rate_per_s"]
        self._tolerance_ms = (int(knobs["burst"]) - 1) * self._interval_ms
        self._max_in_flight = int(knobs["max_in_flight"])
        self._reserved = knobs["reserved"]
        self._tat_ms = 0
        self._in_flight = 0
        self._hold_until_ms = 0

    @property
    def reserved(self):
        """Say whether the lane is insulated from holds observed on other lanes."""
        return self._reserved

    @property
    def in_flight(self):
        """Return how many acquired calls have not been released."""
        return self._in_flight

    def acquire(self, now_ms):
        """Take a token and a bulkhead slot if the lane can give both now."""
        if now_ms < self._hold_until_ms or self._in_flight >= self._max_in_flight:
            return False
        arrival_ms = max(self._tat_ms, now_ms)
        if arrival_ms - now_ms > self._tolerance_ms:
            return False
        self._tat_ms = arrival_ms + self._interval_ms
        self._in_flight += 1
        return True

    def release(self):
        """Give back one bulkhead slot."""
        self._in_flight -= 1

    def next_allowed_ms(self, now_ms):
        """Return the first instant a token is certainly available and no hold stands."""
        earliest = max(now_ms, self._tat_ms - self._tolerance_ms, self._hold_until_ms)
        return int(math.ceil(earliest))

    def hold(self, until_ms):
        """Refuse every acquire before ``until_ms``; a longer hold wins."""
        self._hold_until_ms = max(self._hold_until_ms, until_ms)


class RateLimiter:
    """Token buckets and write bulkheads per lane, from ``resilience.limiter``.

    Each lane has its own bucket (``rate_per_s`` tokens a second, up to
    ``burst``), its own bulkhead (``max_in_flight`` acquired-and-unreleased
    calls, default one — D19's write bulkhead) and its own hold, set by a
    ``Retry-After`` the lane observed and capped at ``MAX_BACKOFF_S``. A
    hold observed on one lane also holds every lane that is NOT
    ``reserved`` — a venue throttle is account-wide — while a reserved
    lane is held only by what it saw itself: that is how the ``cancel``
    lane keeps its capacity when submits are exhausted, and its own burst
    is what keeps it bounded.

    Parameters
    ----------
    params : dict
        The ``resilience.limiter`` block: both lanes ``submit`` and
        ``cancel`` required, each ``{rate_per_s, burst, max_in_flight,
        reserved}`` (the last two defaulting to ``DEFAULT_MAX_IN_FLIGHT``
        and ``DEFAULT_RESERVED``).
    clock : Clock
        Injected; ``now_ms()`` is the bucket's and the hold's time.

    Examples
    --------
    ::

        from dskit.production.clock import TestClock

        limiter = RateLimiter(
            {"submit": {"rate_per_s": 5, "burst": 5},
             "cancel": {"rate_per_s": 10, "burst": 10, "reserved": True}},
            clock=TestClock(),
        )
        limiter.acquire("submit")    # True
        limiter.acquire("submit")    # False: one write in flight
        limiter.release("submit")
        limiter.observe("submit", {"Retry-After": "5"})
        limiter.acquire("submit")    # False: held for five seconds
        limiter.acquire("cancel")    # True: reserved, so not held
    """

    def __init__(self, params=None, *, clock):
        params = dict(params or {})
        problems = self.validate_params(params)
        if problems:
            raise ProductionError(problems)
        self._clock = clock
        self._lanes = {name: _Lane(params[name]) for name in _LANES}

    @classmethod
    def validate_params(cls, params):
        """Return every problem with a ``limiter`` block; empty when acceptable.

        Parameters
        ----------
        params : dict
            The block as written: one object per lane, both lanes present.

        Returns
        -------
        list of str
            Accumulated problems, each naming the lane and the knob.
        """
        if not isinstance(params, dict):
            return [f"limiter must be an object (dict), got {params!r}"]
        problems = []
        reject_unknown_params(problems, params, _LANES + _NOTES)
        for name in _LANES:
            if name not in params:
                problems.append(f"{name} lane is required")
            elif not isinstance(params[name], dict):
                problems.append(f"{name} must be an object (dict), got {params[name]!r}")
            else:
                problems.extend(f"{name}: {p}" for p in _Lane.validate_params(params[name]))
        return problems

    @property
    def lanes(self):
        """Return the lane names, in document order."""
        return tuple(self._lanes)

    def _lane(self, lane):
        """Return the lane object, refusing a lane the document never declared."""
        return _lookup(self._lanes, lane, "lane")

    def acquire(self, lane):
        """Take a token and a bulkhead slot on ``lane`` if both are free now.

        Parameters
        ----------
        lane : str
            ``submit`` or ``cancel``.

        Returns
        -------
        bool
            ``True`` if the call may go out (release it afterwards);
            ``False`` if the lane is held, its bulkhead is full or its
            bucket is empty.

        Raises
        ------
        ProductionError
            If ``lane`` was never declared.
        """
        return self._lane(lane).acquire(self._clock.now_ms())

    def release(self, lane):
        """Give back the bulkhead slot an acquire took.

        Parameters
        ----------
        lane : str
            The lane that was acquired.

        Raises
        ------
        ProductionError
            If ``lane`` was never declared, or nothing is in flight on it.
        """
        target = self._lane(lane)
        if target.in_flight == 0:
            raise ProductionError([f"{lane}: release without a matching acquire"])
        target.release()

    def in_flight(self, lane):
        """Return how many calls are acquired and not yet released on ``lane``.

        Parameters
        ----------
        lane : str
            The lane.

        Returns
        -------
        int
            The bulkhead's occupancy.

        Raises
        ------
        ProductionError
            If ``lane`` was never declared.
        """
        return self._lane(lane).in_flight

    def next_allowed_ms(self, lane):
        """Return the earliest instant an acquire on ``lane`` could succeed.

        Now while a token waits and no hold stands; otherwise the later of
        the next token's instant and the hold's end. The bulkhead has no
        schedule, so a full one is not reflected.

        Parameters
        ----------
        lane : str
            The lane.

        Returns
        -------
        int
            Epoch milliseconds, rounded up.

        Raises
        ------
        ProductionError
            If ``lane`` was never declared.
        """
        return self._lane(lane).next_allowed_ms(self._clock.now_ms())

    def reserved(self, lane):
        """Say whether ``lane`` ignores holds observed on the other lanes.

        Parameters
        ----------
        lane : str
            The lane.

        Returns
        -------
        bool
            The lane's ``reserved`` knob.

        Raises
        ------
        ProductionError
            If ``lane`` was never declared.
        """
        return self._lane(lane).reserved

    def observe(self, lane, headers):
        """Read a response's ``Retry-After`` and hold the lanes it applies to.

        Runs on the response path, so a header that is absent, malformed,
        negative or in the past is ignored, never raised on. The hold is
        capped at ``MAX_BACKOFF_S`` and lands on ``lane`` and on every
        other lane that is not ``reserved``.

        Parameters
        ----------
        lane : str
            The lane the response answered.
        headers : dict
            The response headers, any case.

        Raises
        ------
        ProductionError
            If ``lane`` was never declared — a programming error, not a
            venue's.
        """
        target = self._lane(lane)
        now_ms = self._clock.now_ms()
        delay_ms = _retry_after_ms(headers, now_ms)
        if delay_ms is None:
            return
        until_ms = now_ms + min(delay_ms, _MAX_HOLD_MS)
        target.hold(until_ms)
        for other in self._lanes.values():
            if other is not target and not other.reserved:
                other.hold(until_ms)


# ---------------------------------------------------------------------------
# Transport — the socket boundary
# ---------------------------------------------------------------------------


class Transport(_Configured):
    """The socket seam (§5.12): one ``send``, a value back, never a hidden retry.

    A transport does exactly one request and reports what came back. It
    raises only what the socket raised (a connection fault, a timeout) so
    the classifier can see it; a non-2xx status is a VALUE, because whether
    a ``429`` is ``throttled`` is the classifier's call, not the socket's.

    Parameters
    ----------
    params : dict, optional
        The ``transport.params`` block of the document; default-deny over
        the subclass's ``_PARAMS`` plus ``notes``.

    Examples
    --------
    A transport that answers from a recording, for a replay::

        class TapeTransport(Transport):
            def send(self, method, url, headers, body, timeout):
                return 200, {"Content-Type": "application/json"}, b"{}"

        TapeTransport({}).send("GET", "https://venue.example/x", {}, None,
                               {"connect_s": 2.0, "read_s": 5.0})[0]   # 200
    """

    @abstractmethod
    def send(self, method, url, headers, body, timeout):
        """Do one request and return ``(status, headers, body)``.

        Parameters
        ----------
        method : str
            The HTTP method.
        url : str
            The absolute URL.
        headers : dict
            Request headers.
        body : bytes or None
            The request body, ``None`` for a bodiless method.
        timeout : dict
            ``{connect_s, read_s}``, both positive seconds; ``None`` refused.

        Returns
        -------
        tuple
            ``(int status, dict headers, bytes body)``.

        Raises
        ------
        ProductionError
            If ``timeout`` is missing, partial or not positive.
        """


def _check_timeout(timeout):
    """Return ``(connect_s, read_s)`` from a timeout object, refusing anything else."""
    problems = []
    if not isinstance(timeout, dict):
        problems.append(
            f"timeout must be an object with {list(_TIMEOUT_KEYS)}, got {timeout!r}"
        )
    else:
        reject_unknown_params(problems, timeout, _TIMEOUT_KEYS)
        _check_deadlines(problems, timeout)
    if problems:
        raise ProductionError(problems)
    return timeout["connect_s"], timeout["read_s"]


class UrllibTransport(Transport):
    """The core transport, on ``urllib.request`` — one request, one socket deadline.

    urllib exposes a single socket timeout, so ``send`` passes the wider of
    ``connect_s`` and ``read_s``: the narrower value would truncate a read
    the document allowed. ``urlopen`` is called by attribute at send time,
    which is what lets a socket-free test of it exist.

    Parameters
    ----------
    params : dict, optional
        ``connect_s`` and ``read_s`` (positive seconds), the document's
        configured deadlines, exposed as :attr:`timeout` for the caller to
        pass to ``send``; each defaults to its ``DEFAULT_*`` name.

    Examples
    --------
    ::

        transport = UrllibTransport({"connect_s": 2.0, "read_s": 5.0})
        transport.timeout   # {'connect_s': 2.0, 'read_s': 5.0}
        status, headers, body = transport.send(
            "GET", "https://venue.example/orders", {"Accept": "application/json"},
            None, transport.timeout,
        )
    """

    _PARAMS = _TIMEOUT_KEYS
    _DEFAULTS = {"connect_s": DEFAULT_CONNECT_S, "read_s": DEFAULT_READ_S}

    @classmethod
    def _check(cls, problems, knobs):
        """Append a problem for a deadline that is not positive."""
        _check_deadlines(problems, knobs)

    def _configure(self, knobs):
        """Keep the configured deadlines."""
        self._timeout = {name: float(knobs[name]) for name in _TIMEOUT_KEYS}

    @property
    def timeout(self):
        """Return the configured ``{connect_s, read_s}``, ready to pass to ``send``."""
        return dict(self._timeout)

    def send(self, method, url, headers, body, timeout):
        """Do one request through ``urllib.request.urlopen``.

        Parameters
        ----------
        method : str
            The HTTP method.
        url : str
            The absolute URL.
        headers : dict
            Request headers.
        body : bytes or None
            The request body, ``None`` for a bodiless method.
        timeout : dict
            ``{connect_s, read_s}``, both positive seconds.

        Returns
        -------
        tuple
            ``(int status, dict headers, bytes body)``; a non-2xx status is
            returned, not raised.

        Raises
        ------
        ProductionError
            If ``timeout`` is missing, partial or not positive.
        OSError
            A connection fault or a socket timeout, as urllib raised it.
        """
        connect_s, read_s = _check_timeout(timeout)
        request = urllib.request.Request(
            url, data=body, headers=dict(headers or {}), method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=max(connect_s, read_s)) as response:
                return response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers.items()), error.read()


# ---------------------------------------------------------------------------
# Signer (§5.12.1) — a request signature bound to a bounded clock skew
# ---------------------------------------------------------------------------


def _canonical_request(prefix, method, url, at_ms, body):
    """Return the bytes a signature covers: prefix, request line, timestamp, body.

    One owner for the payload, so the object that signs and the test that
    checks a signature are not two spellings of the same recipe. Newline
    separated because a separator no field may contain is what stops
    ``POST /a`` + ``/b`` from signing the same bytes as ``POST /a/b``.

    Parameters
    ----------
    prefix : str
        The venue-required literal that leads the payload; ``""`` for none.
    method : str
        The HTTP method, upper-cased here so a caller's spelling cannot
        change the signature.
    url : str
        The absolute URL.
    at_ms : int
        The timestamp being stamped, in epoch milliseconds.
    body : bytes or None
        The request body; absent bodies sign as empty.

    Returns
    -------
    bytes
        The payload to authenticate.
    """
    return b"\n".join(
        [
            prefix.encode("utf-8"),
            method.upper().encode("utf-8"),
            url.encode("utf-8"),
            str(at_ms).encode("utf-8"),
            bytes(body or b""),
        ]
    )


def _check_body(problems, body):
    """Append a problem unless ``body`` is bytes-like or absent."""
    if body is not None and not isinstance(body, (bytes, bytearray)):
        problems.append(f"body must be bytes or None, got {type(body).__name__}")


class Signer(_Configured):
    """The venue-facing signature seam, and the skew window that bounds it (§5.12.1).

    It lives beside the retry policies rather than in ``arming.py`` because
    it authenticates the PROCESS to the venue, while ``ApprovalVerifier``
    authenticates a HUMAN to the process; the two share nothing but the
    word "sign", and merging them would put a venue API key on the same
    seam as an operator's proof.

    The estimate is kept here, not in the subclass: ``probe(transport)``
    asks the venue what time it thinks it is and records the difference,
    ``skew_ms()`` reports it, and :meth:`check_skew` is the refusal every
    ``sign`` owes its caller before it computes anything. Core holds no
    venue and therefore no time endpoint, so :meth:`probe_request` answers
    ``None`` here and a child supplies the one venue fact.

    Parameters
    ----------
    params : dict, optional
        ``max_skew_ms`` (int, default :data:`DEFAULT_MAX_SKEW_MS`, and never
        finer than :attr:`TIME_RESOLUTION_MS`), ``probe_every_ms`` (int >= 1,
        REQUIRED — a staleness rule with no period never fires), ``time_url``
        (str, optional): the venue's clock endpoint, and ``probe_timeout``
        (``{connect_s, read_s}``, default the transport's own deadlines):
        what bounds the one request that reads it. Plus whatever the subclass
        declares.
    clock : Clock, keyword-only
        Injected; ``now_ms()`` is what a server's answer is measured
        against and what makes an estimate old.

    Raises
    ------
    ProductionError
        On an unknown or malformed knob, or a clock with no ``now_ms()``.

    Examples
    --------
    A venue's signer is a subclass that supplies ``sign`` and the one thing
    core cannot know — where to ask the time::

    A venue is a DOCUMENT, not a subclass — the params below are what one
    writes, and nothing here is overridden::

        signer = HmacSigner({"key_env": "VENUE_KEY", "header": "X-Sig",
                             "timestamp_header": "X-Ts",
                             "time_url": "https://venue.example/time",
                             "probe_every_ms": 60000}, clock=clock)
        signer.probe(transport)   # 40 — the venue's clock is 40 ms ahead
        headers, body = signer.sign("POST", url, {}, b"{}", clock.now_ms())

    The hook is for the venues those knobs cannot describe — a probe that
    must be a POST, be signed, or carry a header::

        class MyVenueSigner(HmacSigner):
            def probe_request(self):
                return {"method": "POST", "url": "https://venue.example/time",
                        "headers": {"X-Api-Key": "..."}, "body": b"{}",
                        "timeout": {"connect_s": 1.0, "read_s": 1.0}}
    """

    #: How finely THIS signer's time source can place the venue's instant.
    #: The base reads an HTTP ``Date``, which resolves to a second; a venue
    #: that publishes milliseconds declares a smaller value and may then set
    #: a finer ``max_skew_ms``. It is a class declaration for the same reason
    #: ``Lease.LIVE_CAPABLE`` is: the fact belongs to the implementation, and
    #: the check must be able to read it without building one.
    TIME_RESOLUTION_MS = DATE_HEADER_RESOLUTION_MS

    _PARAMS = ("max_skew_ms", "probe_every_ms", "time_url", "probe_timeout")
    _DEFAULTS = {
        "max_skew_ms": DEFAULT_MAX_SKEW_MS,
        "time_url": None,
        "probe_timeout": {"connect_s": DEFAULT_CONNECT_S, "read_s": DEFAULT_READ_S},
    }

    def __init__(self, params=None, *, clock):
        if not callable(getattr(clock, "now_ms", None)):
            raise ProductionError([f"clock must provide now_ms(), got {clock!r}"])
        self._clock = clock
        self._skew_ms = 0
        self._probed_at_ms = None
        super().__init__(params)

    @classmethod
    def _check(cls, problems, knobs):
        """Bound the window, require the freshness period, and check the probe."""
        check_int_param(problems, "max_skew_ms", knobs.get("max_skew_ms"), ge=0)
        check_int_param(problems, "probe_every_ms", knobs.get("probe_every_ms"), ge=1)
        cls._check_reachable(problems, knobs.get("max_skew_ms"))
        if knobs.get("time_url") is not None:
            _check_str(problems, "time_url", knobs["time_url"])
        try:
            _check_timeout(knobs.get("probe_timeout"))
        except ProductionError as exc:
            problems.extend(f"probe_timeout: {problem}" for problem in exc.problems)

    @classmethod
    def _check_reachable(cls, problems, max_skew_ms):
        """Refuse a bound this signer's time source can never satisfy."""
        if not isinstance(max_skew_ms, int) or isinstance(max_skew_ms, bool):
            return
        if max_skew_ms < cls.TIME_RESOLUTION_MS:
            problems.append(
                f"max_skew_ms={max_skew_ms} is finer than {cls.__name__}'s time "
                f"source can resolve ({cls.TIME_RESOLUTION_MS} ms), so this signer "
                "could never produce a signature: refusing here, where the document "
                "is planned, rather than at the first submit after someone arms it. "
                "Widen the bound, or declare a finer TIME_RESOLUTION_MS on a "
                "subclass whose server_ms() reads a finer source"
            )

    def _configure(self, knobs):
        """Keep the two bounds and the probe the document configured."""
        self._max_skew_ms = int(knobs["max_skew_ms"])
        self._probe_every_ms = int(knobs["probe_every_ms"])
        self._time_url = knobs["time_url"]
        self._probe_timeout = dict(knobs["probe_timeout"])

    @property
    def max_skew_ms(self):
        """The widest skew this signer will stamp a request at, in ms."""
        return self._max_skew_ms

    @property
    def probe_every_ms(self):
        """How old the last successful probe may be before signing refuses, in ms."""
        return self._probe_every_ms

    def skew_ms(self):
        """Return the current estimate of the venue's clock minus ours.

        Returns
        -------
        int
            Milliseconds; positive when the venue is ahead, negative when
            it is behind, and ``0`` before any probe has succeeded — which
            :meth:`check_skew` refuses on separately, so the zero is never
            mistaken for agreement.
        """
        return self._skew_ms

    def probe_request(self):
        """Return the bounded request that asks the venue for its clock.

        The base builds a plain ``GET`` of the document's ``time_url`` under
        its ``probe_timeout``, because a venue's clock endpoint is a CONFIG
        value in exactly the way a connector's endpoint is — using this
        package against a new venue means writing a new document, not
        subclassing. The hook survives for the venues those two knobs cannot
        describe: a probe that must be signed, that is a ``POST``, or that
        needs a header. A signer given neither refuses in :meth:`probe`.

        Returns
        -------
        dict or None
            ``{method, url, headers, body, timeout}`` — exactly
            ``Transport.send``'s arguments — or ``None`` when the document
            named no ``time_url`` and nothing overrode this.
        """
        if self._time_url is None:
            return None
        return {
            "method": "GET",
            "url": self._time_url,
            "headers": {},
            "body": None,
            "timeout": dict(self._probe_timeout),
        }

    def server_ms(self, status, headers, body):
        """Read the venue's own instant out of a probe's answer.

        The default reads the HTTP ``Date`` header, which every compliant
        server sends and which needs no agreement about a body's shape. An
        HTTP-date is a whole number of SECONDS, so the estimate it yields is
        good to a second and no better — a venue that publishes milliseconds
        is worth an override, and ``max_skew_ms`` should be set knowing that
        a venue in perfect agreement can read as up to a second behind.

        Parameters
        ----------
        status : int
            The response status.
        headers : dict
            The response headers.
        body : bytes
            The response body.

        Returns
        -------
        int
            The venue's instant in epoch milliseconds.

        Raises
        ------
        ProductionError
            When the answer carries no readable server time — an estimate
            guessed from a silent response is worse than none.
        """
        stamp = _http_date_ms(_header(headers, "date"))
        if stamp is None:
            raise ProductionError(
                [
                    f"the time probe answered {status} with no readable Date header; "
                    "override server_ms(status, headers, body) for a venue that "
                    "publishes its clock elsewhere"
                ]
            )
        return stamp

    def probe(self, transport):
        """Ask the venue for its clock once and update the estimate.

        Parameters
        ----------
        transport : Transport
            The socket boundary; ``send`` is called exactly once.

        Returns
        -------
        int
            The new skew in milliseconds.

        Raises
        ------
        ProductionError
            When this signer declares no time endpoint, when the request
            it declares carries no deadline, or when the answer holds no
            server time. A failed probe leaves the previous estimate and
            its age untouched, so it ages out rather than being trusted.
        """
        request = self.probe_request()
        if request is None:
            raise ProductionError(
                [
                    f"{type(self).__name__} has no time endpoint: name the venue's "
                    "clock in params.time_url, or — for a venue whose probe must be "
                    "a POST, be signed, or carry a header — override probe_request()"
                ]
            )
        problems = []
        reject_unknown_params(problems, request, _PROBE_KEYS)
        for name in _PROBE_KEYS:
            if name not in request:
                problems.append(f"probe_request() must supply {name!r}")
        if problems:
            raise ProductionError(problems)
        _check_timeout(request["timeout"])
        status, headers, body = transport.send(**request)
        server = self.server_ms(status, headers, body)
        local = self._clock.now_ms()
        self._skew_ms = int(server) - int(local)
        self._probed_at_ms = int(local)
        return self._skew_ms

    def check_skew(self):
        """Refuse to sign at all when the estimate is stale or too wide.

        This is the safety point of the object. A signature stamped outside
        the venue's own window is rejected AFTER the request has been sent,
        which makes the submit ``unknown`` and forces a reconciliation;
        refusing to sign makes it ``not_sent``, which costs nothing.
        Sending a request that is known to be unacceptable is never the
        better branch, so both halves fail CLOSED: an estimate that was
        never taken is infinitely old, and the width is judged in BOTH
        directions, since a venue whose clock is behind ours rejects a
        signature exactly as one that is ahead does.

        Returns
        -------
        None
            Returns only when the estimate is fresh and inside the bound.

        Raises
        ------
        ProductionError
            Naming the age or the skew and the bound it broke. It quotes
            neither the key nor anything derived from it.
        """
        if self._probed_at_ms is None:
            raise ProductionError(
                [
                    "no clock probe has succeeded: refusing to sign rather than "
                    "stamp a request at a skew nobody has measured"
                ]
            )
        age = self._clock.now_ms() - self._probed_at_ms
        if age > self._probe_every_ms:
            raise ProductionError(
                [
                    f"the last clock probe is {age} ms old, past probe_every_ms="
                    f"{self._probe_every_ms}: refusing to sign on a stale estimate"
                ]
            )
        if abs(self._skew_ms) > self._max_skew_ms:
            raise ProductionError(
                [
                    f"venue clock skew {self._skew_ms} ms exceeds max_skew_ms="
                    f"{self._max_skew_ms}: refusing to sign, which is not_sent, "
                    "rather than sending a signature the venue will reject, which "
                    "is unknown"
                ]
            )

    @abstractmethod
    def sign(self, method, url, headers, body, at_ms):
        """Return a NEW header map and body carrying the signature.

        Parameters
        ----------
        method : str
            The HTTP method.
        url : str
            The absolute URL.
        headers : dict or None
            The caller's headers; never mutated.
        body : bytes or None
            The request body; never mutated.
        at_ms : int
            The instant to stamp, in epoch milliseconds.

        Returns
        -------
        tuple
            ``(dict headers, bytes body)`` — new objects, so a retry signs
            the original request rather than one already stamped.

        Raises
        ------
        ProductionError
            On a malformed argument, or when :meth:`check_skew` refuses.
        """


class HmacSigner(Signer):
    """The HMAC signature most venues ask for, over a bounded skew window.

    The document names the env var, never the value: the key resolves once
    through :func:`~dskit.production.redact.resolve_secrets` and is
    registered as a credential, so it can reach neither a log line nor a
    record. An unset or empty variable refuses HERE, at construction —
    discovering a missing credential at the first live submit is the
    failure that ordering exists to prevent.

    What the signature covers is :func:`_canonical_request`: the venue's
    prefix, the request line, the timestamp and the body. The DIGEST is
    what a ``reason`` may quote; the key never appears in a header, a
    message or a repr.

    A document is enough to make one work: ``time_url`` names the venue's
    clock and ``probe_timeout`` bounds the request that reads it, so using
    this against a new venue means writing a new config rather than
    subclassing. :meth:`~Signer.probe_request` remains for the venues those
    two knobs cannot describe.

    Parameters
    ----------
    params : dict
        ``key_env`` (str, required): the NAME of the environment variable
        holding the secret; ``header`` and ``timestamp_header`` (str,
        required): the header names the venue expects; ``algorithm`` (one
        of ``vocab.SIGNER_ALGORITHMS``, default
        :data:`DEFAULT_SIGNER_ALGORITHM`); ``prefix`` (str, default
        :data:`DEFAULT_SIGNER_PREFIX`): a venue-required literal before the
        payload; plus ``max_skew_ms``, ``probe_every_ms``, ``time_url`` and
        ``probe_timeout`` from :class:`Signer`.
    clock : Clock, keyword-only
        Injected, as for :class:`Signer`.

    Raises
    ------
    ProductionError
        On an unknown or malformed knob, or when ``key_env`` names a
        variable that is unset or empty.

    Examples
    --------
    Built the way ``compose`` builds it, from a document's site::

        signer = HmacSigner({"key_env": "VENUE_KEY", "header": "X-Signature",
                             "timestamp_header": "X-Timestamp",
                             "algorithm": "sha256", "probe_every_ms": 60000,
                             "max_skew_ms": 5000,
                             "time_url": "https://venue.example/time"},
                            clock=clock)
        signer.skew_ms()   # 0 — and signing refuses until a probe succeeds
    """

    _PARAMS = Signer._PARAMS + (
        "key_env",
        "header",
        "timestamp_header",
        "algorithm",
        "prefix",
    )
    _DEFAULTS = {
        **Signer._DEFAULTS,
        "algorithm": DEFAULT_SIGNER_ALGORITHM,
        "prefix": DEFAULT_SIGNER_PREFIX,
    }

    @classmethod
    def _check(cls, problems, knobs):
        """Require the three names the venue fixes, and a known algorithm."""
        super()._check(problems, knobs)
        for name in ("key_env", "header", "timestamp_header"):
            _check_str(problems, name, knobs.get(name))
        _check_str(problems, "prefix", knobs.get("prefix"), non_empty=False)
        _check_choice(problems, "algorithm", knobs.get("algorithm"), SIGNER_ALGORITHMS)

    def _configure(self, knobs):
        """Resolve the key once, register it as a credential, keep the rest."""
        super()._configure(knobs)
        self._header_name = knobs["header"]
        self._timestamp_header = knobs["timestamp_header"]
        self._algorithm = knobs["algorithm"]
        self._prefix = knobs["prefix"]
        self._key = self._resolve_key(knobs["key_env"])

    @staticmethod
    def _resolve_key(key_env):
        """Read the named variable, register the value as a credential, return its bytes."""
        secrets = resolve_secrets(None)
        value = secrets.get(key_env)
        if not value:
            raise ProductionError(
                [
                    f"key_env names {key_env!r}, which is unset or empty in this "
                    "environment — a signer with no key cannot sign, and finding "
                    "that out at the first live submit is the failure this refusal "
                    "exists to prevent"
                ]
            )
        register_secret(value)
        return value.encode("utf-8")

    @property
    def algorithm(self):
        """The hash the document named, a member of ``vocab.SIGNER_ALGORITHMS``."""
        return self._algorithm

    def sign(self, method, url, headers, body, at_ms):
        """Stamp and sign one request, refusing first on skew (see :meth:`Signer.sign`).

        Parameters
        ----------
        method, url, headers, body, at_ms
            As :meth:`Signer.sign` takes them.

        Returns
        -------
        tuple
            ``(dict headers, bytes body)`` — a new map carrying the
            signature and the timestamp, and the body as immutable bytes,
            so nothing the caller does afterwards changes what was signed.

        Raises
        ------
        ProductionError
            When the skew estimate is stale or too wide, or on a malformed
            argument. Neither message quotes the key.
        """
        self.check_skew()
        problems = []
        _check_str(problems, "method", method)
        _check_str(problems, "url", url)
        if headers is not None and not isinstance(headers, dict):
            problems.append(f"headers must be a dict or None, got {headers!r}")
        _check_body(problems, body)
        check_int_param(problems, "at_ms", at_ms, ge=0)
        if problems:
            raise ProductionError(problems)
        signed_body = bytes(body) if body is not None else None
        payload = _canonical_request(self._prefix, method, url, at_ms, signed_body)
        digest = hmac.new(self._key, payload, _ALGORITHMS[self._algorithm]).hexdigest()
        out = dict(headers or {})
        out[self._header_name] = digest
        out[self._timestamp_header] = str(at_ms)
        return out, signed_body


# ---------------------------------------------------------------------------
# ResiliencePolicies — the one value the Execution bundle carries (§5.16)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResiliencePolicies:
    """The §5.12 set as one value: what ``compose`` builds and ``Execution`` holds.

    Parameters
    ----------
    retry : Retry
        The retry policy.
    breaker : CircuitBreakers
        The per-scope circuits.
    limiter : RateLimiter
        The lanes.
    transport : Transport
        The socket boundary.

    Examples
    --------
    Built from a document's section by :func:`resilience_from_document`;
    by hand, for a test double::

        policies = ResiliencePolicies(retry, breakers, limiter, transport)
        policies.retry.decide(1, "transient", False)   # 'retry'
    """

    retry: Retry
    breaker: CircuitBreakers
    limiter: RateLimiter
    transport: Transport


def _transport_from_site(site):
    """Build the transport a ``{uses, params}`` site names, through the registry."""
    if not isinstance(site, dict):
        raise ProductionError([f"transport must be a {{uses, params}} object, got {site!r}"])
    problems = []
    reject_unknown_params(problems, site, _SITE_KEYS + _NOTES)
    uses = site.get("uses")
    if not isinstance(uses, str) or not uses:
        problems.append(f"uses must be a non-empty string, got {uses!r}")
    if problems:
        raise ProductionError(problems)
    return TRANSPORT_KINDS.resolve(uses)(site.get("params"))


def resilience_from_document(section, *, clock, sleeper, rng):
    """Build :class:`ResiliencePolicies` from a document's ``resilience`` section.

    Every block is built from its own knobs and nothing else; a problem in
    any block is accumulated and all are raised at once, prefixed by the
    block's path.

    Parameters
    ----------
    section : dict
        The ``resilience`` section as written (``document.to_obj()``'s
        copy): ``retry``, ``breaker``, ``limiter`` and ``transport`` all
        required, ``notes`` allowed, nothing else.
    clock : Clock
        Injected into every policy.
    sleeper : callable
        ``sleeper(seconds)``, injected into the retry policy.
    rng : object
        Provides ``uniform(a, b)``, injected into the retry policy.

    Returns
    -------
    ResiliencePolicies
        The four policies.

    Raises
    ------
    ProductionError
        Naming every missing or unknown block and every bad knob.
    """
    if not isinstance(section, dict):
        raise ProductionError([f"resilience must be an object (dict), got {section!r}"])
    problems = []
    reject_unknown_params(problems, section, _SECTION_KEYS + _NOTES)
    problems.extend(f"resilience.{key} is required" for key in _SECTION_KEYS if key not in section)
    if problems:
        raise ProductionError(problems)
    builders = {
        "retry": lambda block: Retry(block, clock=clock, sleeper=sleeper, rng=rng),
        "breaker": lambda block: CircuitBreakers(block, clock=clock),
        "limiter": lambda block: RateLimiter(block, clock=clock),
        "transport": _transport_from_site,
    }
    built = {}
    for key, build in builders.items():
        try:
            built[key] = build(section[key])
        except ProductionError as error:
            problems.extend(f"resilience.{key}: {problem}" for problem in error.problems)
    if problems:
        raise ProductionError(problems)
    return ResiliencePolicies(**built)


#: The transport family (§4.3): the core kind, or a ``pkg.module:Class``.
TRANSPORT_KINDS = Registry("transport", Transport)
TRANSPORT_KINDS.register("urllib", UrllibTransport)

#: The signer family (§5.12.1), behind ``document.execution.signer``. A
#: child's venue signer subclasses ``HmacSigner`` for the one fact core
#: cannot hold — where to ask the venue's clock — and is named by path.
SIGNER_KINDS = Registry("signer", Signer)
SIGNER_KINDS.register("hmac", HmacSigner)
