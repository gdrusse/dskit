"""The series breaker: trips, cooling-off and the ``HALT`` kill switch (plan §5.6).

The breaker state is the ledger fold and nothing else. :class:`Breaker`
never keeps a state of its own: ``current(view)`` reads
``StateView.breaker``, every transition is a §6 ``trip`` record appended
and barriered through the injected ledger, and ``breaker.json`` is only
a head-bound cache of that projection — validated before ``READY``,
rebuilt when it is behind, refused when it is ahead of or off the chain
(D15). Four rulings shape the module:

* **D10 — the transition policy is the only door.** The breaker never
  decides whether a transition is legal; it asks the injected
  ``TransitionPolicy.permits(from_state, to_state, cause, proof)`` and a
  veto stops it dead with nothing appended. The to-state a cause reaches
  is the one table :data:`CAUSE_TARGETS`, so ``trip`` cannot be talked
  into ``reducing``.
* **D13 / ruling R6 — record before act, even for a cancel.** A halting
  ``trip`` is appended and barriered BEFORE any cancel I/O, so a process
  that dies inside ``cancel_all`` still folds ``halted``. The trip body
  therefore carries no outcome: what the best-effort cancel came to is a
  separate ``cancel_outcome`` record, appended and barriered after the
  attempt and bound to the trip it reports.
* **D12 — halt never flattens, and resume never re-arms.** A halt refuses
  submissions and best-effort cancels working orders when
  ``document.execution.on_halt.cancel_open`` says so, recording one of
  ``vocab.CANCEL_OUTCOMES``; the fold revokes the ordinary arm on every
  transition, so a resumed series is ``active`` and unarmed.
* **D12 — only a verified resume or flatten retires ``HALT``.** The
  sentinel is created with ``O_CREAT | O_EXCL`` plus a directory fsync,
  never truncated, and retired only for the two purposes in
  :data:`SENTINEL_RETIREMENT_PURPOSES` — before the transition barrier,
  so a crash in between leaves the ledger folded ``halted``.

``ledger.HeadBoundCache`` is the one owner of the cache discipline
``breaker.json`` and ``arming.json`` follow — it lives beside the chain
it projects rather than beside either of its two users — and the head
placement itself is ``ledger.validate_cache_head``, called through this
module's name so the ledger stays the single owner of the
at/behind/off-the-chain rule.

Nothing here reads wall time: the clock is injected and cooling-off is
measured against the acknowledged trip's ``recorded_at_ms``.
"""

import dataclasses
import os
from dataclasses import dataclass

from dskit.onboarding.base import fsync_dir
from dskit.production.base import (
    ProductionError,
    _check_str,
    check_credentials,
    pin_members,
)
from dskit.production.ledger import HeadBoundCache, validate_cache_head
from dskit.production.records import Ack
from dskit.production.redact import get_logger, redact
from dskit.production.vocab import (
    APPROVAL_PURPOSES,
    BREAKER_STATES,
    CANCEL_OUTCOMES,
    STATUSES,
    TRANSITION_CAUSES,
    TRIP_REASONS,
)

__all__ = [
    "Breaker",
    "CAUSE_TARGETS",
    "DEFAULT_CANCEL_OPEN",
    "SENTINEL_RETIREMENT_PURPOSES",
    "cancel_outcome",
]

_LOG = get_logger("breaker")

#: What a halt does about working orders when the document's optional
#: ``execution.on_halt`` block is absent: cancel them (D12's de-risking
#: direction). The one name ``validate`` and ``trip`` both read.
DEFAULT_CANCEL_OPEN = True

#: Where each ``vocab.TRANSITION_CAUSES`` member takes the breaker — the
#: to-state the policy is asked about. A cause is never a state a caller
#: chooses: ``trip`` and ``halt`` enter ``halted``, ``reduce`` and
#: ``flatten_request`` enter ``reducing``, ``resume`` returns to ``active``.
CAUSE_TARGETS = {
    "reduce": "reducing",
    "flatten_request": "reducing",
    "trip": "halted",
    "halt": "halted",
    "resume": "active",
}

#: The two verified purposes that may retire the ``HALT`` sentinel (D12).
SENTINEL_RETIREMENT_PURPOSES = ("resume", "flatten_request")

_ACTIVE, _REDUCING, _HALTED = "active", "reducing", "halted"
_TRIP, _CANCEL_OUTCOME = "trip", "cancel_outcome"
#: The ``reason`` a reduce, flatten or resume carries: an operator's act.
_OPERATOR = "operator"
#: Cancel acks that mean the venue refused the cancel outright, and the
#: one status that means nobody knows what happened.
_CANCEL_REFUSED = ("rejected", "not_sent")
_UNRESOLVED = "unknown"
_OUTCOME_NONE, _OUTCOME_SUBMITTED = "none", "submitted"
_OUTCOME_FAILED, _OUTCOME_PARTIAL, _OUTCOME_UNKNOWN = "failed", "partial", "unknown"


pin_members("breaker.py's CAUSE_TARGETS values", CAUSE_TARGETS.values(), BREAKER_STATES)
pin_members("breaker.py's CAUSE_TARGETS keys", CAUSE_TARGETS, TRANSITION_CAUSES, exact=True)
pin_members(
    "breaker.py's SENTINEL_RETIREMENT_PURPOSES", SENTINEL_RETIREMENT_PURPOSES, APPROVAL_PURPOSES
)
pin_members("breaker.py's breaker states", (_ACTIVE, _REDUCING, _HALTED), BREAKER_STATES)
pin_members("breaker.py's cancel statuses", _CANCEL_REFUSED + (_UNRESOLVED,), STATUSES)
pin_members(
    "breaker.py's cancel outcomes",
    (_OUTCOME_NONE, _OUTCOME_SUBMITTED, _OUTCOME_FAILED, _OUTCOME_PARTIAL, _OUTCOME_UNKNOWN),
    CANCEL_OUTCOMES,
)
pin_members("breaker.py's operator reason", (_OPERATOR,), TRIP_REASONS)
_HALTING_CAUSES = tuple(cause for cause, target in CAUSE_TARGETS.items() if target == _HALTED)


def _head_check(head_seq, head_hash, ledger):
    """Place a cached head in the chain through the ledger's one owner of that rule.

    Looked up by name at call time — never bound at import — so the
    owner stays ``ledger.validate_cache_head`` as this module sees it.
    """
    return validate_cache_head(head_seq, head_hash, ledger)


# ---------------------------------------------------------------------------
# The cancel outcome — one pure rule, one owner (D12, ruling R6)
# ---------------------------------------------------------------------------


def cancel_outcome(acks):
    """Classify what a best-effort cancel of working orders came to (D12).

    The rule collapses toward LESS certainty, as §5.4 does for statuses:
    one ``unknown`` ack makes the whole outcome ``unknown``; an attempt
    that answered nothing at all is ``unknown`` too. Otherwise every
    cancel refused (``rejected`` / ``not_sent``) is ``failed``, some
    refused is ``partial``, none refused is ``submitted``. ``none`` is
    never returned here — it is the outcome of a halt that did not try,
    which only the caller knows.

    Parameters
    ----------
    acks : iterable of Ack
        What ``executor.cancel_all()`` answered.

    Returns
    -------
    str
        A member of ``vocab.CANCEL_OUTCOMES`` other than ``none``.

    Raises
    ------
    ProductionError
        If any element is not an ``Ack``.
    """
    acks = tuple(acks)
    problems = [
        f"acks[{index}] is {ack!r}, not an Ack" for index, ack in enumerate(acks)
        if not isinstance(ack, Ack)
    ]
    if problems:
        raise ProductionError(problems)
    statuses = [ack.status for ack in acks]
    if not statuses or _UNRESOLVED in statuses:
        return _OUTCOME_UNKNOWN
    refused = sum(status in _CANCEL_REFUSED for status in statuses)
    if refused == len(statuses):
        return _OUTCOME_FAILED
    if refused:
        return _OUTCOME_PARTIAL
    return _OUTCOME_SUBMITTED


# ---------------------------------------------------------------------------
# The breaker
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Credentials:
    """Who asked for a transition and how they proved it — the three optional ``trip`` ids."""

    control_request_id: object
    principal_digest: object
    proof_digest: object

    def check(self, problems, required):
        """Append a problem per malformed id; per missing id too when ``required``."""
        check_credentials(problems, dataclasses.asdict(self), required=required)


class Breaker:
    """The series breaker over the ledger fold (§5.6, D10, D12, D13).

    Every verb asks the injected ``transition_policy`` first, appends and
    barriers its ``trip`` record second, and only then acts: a halt's
    best-effort cancel runs after the barrier and reports through a
    ``cancel_outcome`` record; a resume or flatten retires the ``HALT``
    sentinel between the policy's answer and the append.

    Parameters
    ----------
    document : ServeDocument
        Read for ``lifecycle.cooling_off_s`` and the optional
        ``execution.on_halt.cancel_open`` (default
        :data:`DEFAULT_CANCEL_OPEN`).
    serve_root : ServeRoot
        Supplies ``breaker_cache`` and ``halt_sentinel``.
    ledger : Ledger
        Where transitions are appended and barriered.
    state : SeriesState
        The fold; ``snapshot()`` is the current breaker state and
        ``last_trip()`` the trip a reset acknowledges (§5.8.1: nothing
        but the fold walks the ledger).
    clock : Clock
        ``now_ms()`` measures cooling-off against the trip's ``recorded_at_ms``.
    transition_policy : TransitionPolicy
        ``permits(from_state, to_state, cause, proof)`` — the only door (D10).
    executor : Executor or None
        ``cancel_all() -> tuple of Ack`` for a halt's best-effort cancel;
        None records ``failed`` when there was something to cancel.

    Examples
    --------
    Halt on a dead feed, then resume after cooling-off::

        breaker = Breaker(
            document, serve_root, ledger=ledger, state=state, clock=clock,
            transition_policy=TransitionPolicy(), executor=executor,
        )
        breaker.trip("feed_dead", "feed")
        breaker.current(state.snapshot())  # 'halted'
        breaker.reset(
            "operator", acknowledges_trip_id="trip:feed_dead:feed:1",
            control_request_id="req-1", principal_digest="7" * 64, proof_digest="8" * 64,
        )
        breaker.current(state.snapshot())  # 'active'
    """

    def __init__(
        self, document, serve_root, *, ledger, state, clock, transition_policy, executor=None
    ):
        self._serve_root = serve_root
        self._ledger = ledger
        self._state = state
        self._clock = clock
        self._policy = transition_policy
        self._executor = executor
        self._cooling_off_ms = int(document.lifecycle.cooling_off_s) * 1000
        on_halt = document.execution.on_halt
        self._cancel_open = DEFAULT_CANCEL_OPEN if on_halt is None else bool(on_halt.cancel_open)
        self._cache = HeadBoundCache(serve_root.breaker_cache, "state", _head_check)

    # -- the fold -----------------------------------------------------------

    def current(self, view):
        """Return the breaker state the fold holds.

        Parameters
        ----------
        view : StateView
            The fold, as the caller has it — never the breaker's own memory.

        Returns
        -------
        str
            A member of ``vocab.BREAKER_STATES``.
        """
        return view.breaker

    # -- transitions ------------------------------------------------------------

    def trip(
        self,
        reason,
        actor,
        control_request_id=None,
        principal_digest=None,
        proof_digest=None,
        cause="trip",
    ):
        """Enter ``halted``: record the trip, then best-effort cancel working orders.

        Parameters
        ----------
        reason : str
            A member of ``vocab.TRIP_REASONS``.
        actor : str
            Who tripped it (``"operator"``, ``"guards.day_loss"``, ``"feed"``).
        control_request_id, principal_digest, proof_digest : str or None
            The operator's request and proof; None for an automatic trip.
        cause : str
            ``"trip"``, or ``"halt"`` for the kill-switch path — a cause
            whose target is ``halted``.

        Returns
        -------
        int
            The ``seq`` of the appended ``trip`` record.

        Raises
        ------
        ProductionError
            On a reason or cause outside the vocabulary, a malformed id,
            or a policy veto — nothing is appended.
        """
        credentials = _Credentials(control_request_id, principal_digest, proof_digest)
        problems = []
        if reason not in TRIP_REASONS:
            problems.append(f"trip reason must be one of {list(TRIP_REASONS)}, got {reason!r}")
        _check_str(problems, "actor", actor)
        if CAUSE_TARGETS.get(cause) != _HALTED:
            problems.append(
                f"a trip's cause must enter {_HALTED!r} — one of {list(_HALTING_CAUSES)}, "
                f"got {cause!r}"
            )
        credentials.check(problems, required=False)
        if problems:
            raise ProductionError(problems)
        view = self._state.snapshot()
        seq, trip_id = self._transition(view, _HALTED, cause, reason, actor, credentials, None, None)
        self.cancel_working(view, trip_id)
        return seq

    def reduce(self, actor, control_request_id, principal_digest, proof_digest):
        """Enter ``reducing`` by a verified ``reduce`` — no submit authority is granted (D12).

        Parameters
        ----------
        actor : str
        control_request_id, principal_digest, proof_digest : str
            All three are required: an unsigned reduce is not a transition.

        Returns
        -------
        int
            The ``seq`` of the appended ``trip`` record.

        Raises
        ------
        ProductionError
            On a missing credential or a policy veto — nothing is appended.
        """
        credentials = self._authenticated(actor, control_request_id, principal_digest, proof_digest)
        view = self._state.snapshot()
        seq, _ = self._transition(view, _REDUCING, "reduce", _OPERATOR, actor, credentials, None, None)
        return seq

    def flatten(self, actor, control_request_id, principal_digest, proof_digest):
        """Enter ``reducing`` by a verified ``flatten_request``, retiring ``HALT`` first (D12).

        Parameters
        ----------
        actor : str
        control_request_id, principal_digest, proof_digest : str
            All three are required.

        Returns
        -------
        int
            The ``seq`` of the appended ``trip`` record.

        Raises
        ------
        ProductionError
            On a missing credential or a policy veto — nothing is appended
            and the sentinel stays.
        """
        credentials = self._authenticated(actor, control_request_id, principal_digest, proof_digest)
        view = self._state.snapshot()
        seq, _ = self._transition(
            view, _REDUCING, "flatten_request", _OPERATOR, actor, credentials, None,
            "flatten_request",
        )
        return seq

    def reset(self, actor, acknowledges_trip_id, control_request_id, principal_digest, proof_digest):
        """Return to ``active`` by a verified ``resume`` after cooling-off — unarmed (D12).

        Parameters
        ----------
        actor : str
        acknowledges_trip_id : str
            The id of the LATEST ``trip`` record — the one holding the
            series down; cooling-off is measured from its ``recorded_at_ms``.
        control_request_id, principal_digest, proof_digest : str
            All three are required.

        Returns
        -------
        int
            The ``seq`` of the appended ``trip`` record.

        Raises
        ------
        ProductionError
            Without a trip id, for a trip id that is not the latest
            transition, before ``document.lifecycle.cooling_off_s`` has
            elapsed, on a missing credential, or on a policy veto —
            nothing is appended and the sentinel stays.
        """
        credentials = self._authenticated(actor, control_request_id, principal_digest, proof_digest)
        latest = self._state.last_trip()
        problems = []
        if acknowledges_trip_id is None:
            problems.append("reset must acknowledge the trip holding the series down; none given")
        elif latest is None:
            problems.append(f"acknowledged trip {acknowledges_trip_id!r}: the ledger holds no trip")
        elif acknowledges_trip_id != latest["id"]:
            problems.append(
                f"acknowledged trip {acknowledges_trip_id!r} is not the latest transition "
                f"{latest['id']!r}"
            )
        else:
            elapsed = self._clock.now_ms() - latest["recorded_at_ms"]
            if elapsed < self._cooling_off_ms:
                problems.append(
                    f"cooling_off_s: {self._cooling_off_ms // 1000} s must elapse after trip "
                    f"{latest['id']!r}; {elapsed} ms have"
                )
        if problems:
            raise ProductionError(problems)
        view = self._state.snapshot()
        seq, _ = self._transition(
            view, _ACTIVE, "resume", _OPERATOR, actor, credentials, acknowledges_trip_id, "resume"
        )
        return seq

    def _authenticated(self, actor, control_request_id, principal_digest, proof_digest):
        """Return the credentials of an operator act, refusing a missing one."""
        credentials = _Credentials(control_request_id, principal_digest, proof_digest)
        problems = []
        _check_str(problems, "actor", actor)
        credentials.check(problems, required=True)
        if problems:
            raise ProductionError(problems)
        return credentials

    def _transition(self, view, to_state, cause, reason, actor, credentials, acknowledged, retire):
        """Ask the policy, retire the sentinel when told, then append and barrier the trip."""
        from_state = view.breaker
        decision = self._policy.permits(from_state, to_state, cause, credentials.proof_digest)
        if not decision.allowed:
            raise ProductionError(
                [f"breaker transition {from_state} -> {to_state} ({cause}) refused: {decision.reason}"]
            )
        if retire is not None:
            self.retire_halt_sentinel(retire)
        record_id = _trip_id(reason, actor, credentials.control_request_id, self._ledger.head()[0] + 1)
        body = {
            "from": from_state,
            "to": to_state,
            "reason": reason,
            "actor": redact(actor),
            "control_request_id": credentials.control_request_id,
            "principal_digest": credentials.principal_digest,
            "proof_digest": credentials.proof_digest,
            "acknowledged_trip_id": acknowledged,
        }
        seq = self._ledger.append({"kind": _TRIP, "id": record_id, "body": body})
        self._ledger.barrier()
        _LOG.info("breaker %s -> %s (%s) by %s", from_state, to_state, cause, redact(actor))
        return seq, record_id

    # -- the halt's best-effort cancel (D12, R6) ----------------------------------

    def cancel_working(self, view, trip_id):
        """Cancel working orders if asked, then record what it came to — after the halt's barrier.

        Public because §6 makes an unanswered halt a recovery duty: "A
        halting ``trip`` with no later ``cancel_outcome`` is what recovery
        looks for: it re-issues ``cancel_all`` query-first rather than
        assuming either answer" (ruling R6). ``executor.cancel_all``
        queries ``open_orders`` before it cancels anything, so a re-issue
        after a crash cancels what is still open rather than what the fold
        remembers.

        Parameters
        ----------
        view : StateView
            The fold as the caller has it; its ``working`` decides whether
            there is anything to sweep.
        trip_id : str
            The halting ``trip`` record this sweep answers; the
            ``cancel_outcome`` is appended under ``cancel_outcome:<trip_id>``,
            so a second call for one trip dedups instead of double-recording.

        Returns
        -------
        None
            The outcome is a record, not a return value.
        """
        acks = ()
        if not self._cancel_open or not view.working:
            outcome = _OUTCOME_NONE
        elif self._executor is None:
            outcome = _OUTCOME_FAILED
            _LOG.error("halt %s: %d working order(s) and no executor to cancel them", trip_id,
                       len(view.working))
        else:
            acks, outcome = self._cancel_all(trip_id)
        body = {"trip_id": trip_id, "outcome": outcome, "acks": [ack.to_obj() for ack in acks]}
        self._ledger.append({"kind": _CANCEL_OUTCOME, "id": f"{_CANCEL_OUTCOME}:{trip_id}", "body": body})
        self._ledger.barrier()

    def _cancel_all(self, trip_id):
        """Call the executor once; a raise is ``failed``, a malformed answer ``unknown``."""
        try:
            acks = tuple(self._executor.cancel_all())
        except Exception as exc:  # best effort: a failed cancel never stops the halt
            _LOG.error("halt %s: cancel_all raised %s", trip_id, redact(str(exc)))
            return (), _OUTCOME_FAILED
        if not all(isinstance(ack, Ack) for ack in acks):
            _LOG.error("halt %s: cancel_all answered with something that is not an Ack", trip_id)
            return (), _OUTCOME_UNKNOWN
        return acks, cancel_outcome(acks)

    # -- the HALT sentinel (D12, §5.8) --------------------------------------------

    def halt_sentinel_present(self):
        """Say whether the ``HALT`` kill switch is on.

        Returns
        -------
        bool
            True when ``serve_root.halt_sentinel`` exists.
        """
        return os.path.exists(self._serve_root.halt_sentinel)

    def create_halt_sentinel(self):
        """Turn the kill switch on: create ``HALT`` exclusively and fsync its directory.

        Never raises and never truncates: a kill switch that fails because
        it is already on is a kill switch that fails when it matters most.

        Returns
        -------
        bool
            True when this call created the file; False when it already
            existed or could not be created (logged).
        """
        path = self._serve_root.halt_sentinel
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        except OSError as exc:
            _LOG.error("cannot create HALT sentinel %s: %s", path, redact(str(exc)))
            return False
        os.close(fd)
        fsync_dir(os.path.dirname(path))
        return True

    def retire_halt_sentinel(self, purpose):
        """Turn the kill switch off — only for a verified resume or flatten request (D12).

        Parameters
        ----------
        purpose : str
            One of :data:`SENTINEL_RETIREMENT_PURPOSES`.

        Returns
        -------
        bool
            True when the file was removed; False when it was absent.

        Raises
        ------
        ProductionError
            For any other purpose — the sentinel stays.
        """
        if purpose not in SENTINEL_RETIREMENT_PURPOSES:
            raise ProductionError(
                [
                    f"HALT may be retired only for {list(SENTINEL_RETIREMENT_PURPOSES)}, "
                    f"not {purpose!r}"
                ]
            )
        path = self._serve_root.halt_sentinel
        try:
            os.remove(path)
        except FileNotFoundError:
            return False
        fsync_dir(os.path.dirname(path))
        return True

    # -- breaker.json (D15) ------------------------------------------------------------

    @property
    def cache_path(self):
        """``serve_root.breaker_cache`` — the head-bound cache file."""
        return self._cache.path

    def write_cache(self, view):
        """Cache the view's breaker state at its head.

        Parameters
        ----------
        view : StateView

        Returns
        -------
        None
        """
        self._cache.write(view.breaker, view)

    def load_cache(self, ledger, view):
        """Validate the cache against the fold and return the fold's state.

        Parameters
        ----------
        ledger : Ledger
        view : StateView

        Returns
        -------
        str
            ``view.breaker``; an absent or stale cache is rebuilt first.

        Raises
        ------
        ProductionError
            As :meth:`HeadBoundCache.load`.
        """
        return self._cache.load(ledger, view, view.breaker)


def _trip_id(reason, actor, control_request_id, next_seq):
    """Derive the kind-qualified record id (ruling R9): by request when there is one, else by head."""
    if control_request_id is not None:
        return f"{_TRIP}:{control_request_id}"
    return f"{_TRIP}:{reason}:{actor}:{next_seq}"
