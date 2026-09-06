"""The venue/account ownership domain: leases, fencing tokens, renewal (plan §5.7.2).

A serve process may act only while it holds the lease on
``document.coordination.scope`` — the canonical ``ExecutionScope{venue,
account}``, never a release id, so an old and a new release contend for
the SAME domain instead of both believing they own it. :class:`Lease` is
the seam: four abstract hooks (``acquire``, ``renew``, ``current``,
``release``) a child implements over its coordination service, and one
concrete rule, :meth:`Lease.permit_current`, the local half of fencing —
a held permit is current only if it IS what ``current(scope)`` answers,
token included. :class:`LeasePermit` carries the scope, the holder, a
monotonic ``fencing_token`` and an expiry; the gateway rejects a stale
token, which is only meaningful because a stale one is recognisable.

Core :class:`ProcessLease` is in-process only and declares
``LIVE_CAPABLE = False``; a live plan resolves a child lease class
through :data:`LEASE_KINDS`. :func:`scope_equal` is the one owner of
§5.7.2's exact-equality rule among the actual, document, release, lease
and ``ActPermit`` scopes, so no caller respells ``a == b == c`` — and a
missing scope refuses rather than comparing false.

:class:`LeaseRenewer` is the synchronous driver of the supervised
renewal cadence: ``tick(now_ms)`` renews when ``every_ms`` has elapsed
since the last success and, per §5.7.2, a missed deadline
(``every_ms + renew_timeout_ms``, inclusive) or a refused renewal
invalidates the local permit at once without waiting for nominal expiry
— after which there is simply no permit to fence a submit with, while
query, reconcile and cancel go on. The rung is never read here.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from dskit.pipeline.node import check_int_param
from dskit.production.base import ProductionError, Registry, _check_str, reject_unknown_params
from dskit.production.records import ExecutionScope
from dskit.production.redact import get_logger, redact

__all__ = [
    "LEASE_KINDS",
    "Lease",
    "LeasePermit",
    "LeaseRenewer",
    "ProcessLease",
    "scope_equal",
]

_LOG = get_logger("coordination")
_NOTES = ("notes",)
#: The first fencing token a lease ever issues; every acquire and renew raises it.
_FIRST_TOKEN = 1


def _check_scope(problems, name, value):
    """Append a problem unless ``value`` is the canonical ``ExecutionScope``."""
    if not isinstance(value, ExecutionScope):
        problems.append(f"{name} must be an ExecutionScope, got {value!r}")


@dataclass(frozen=True)
class LeasePermit:
    """What holding the lease looks like: scope, holder, fence and expiry (§5.7.2).

    Parameters
    ----------
    scope : ExecutionScope
        The ownership domain — never a string.
    holder : str
        Who holds it (a release/process identity), non-empty.
    fencing_token : int
        Monotonic, ``>= 1``; the gateway rejects a stale one.
    expires_ms : int
        Epoch ms; the permit is dead AT and after this instant.

    Examples
    --------
    ::

        permit = LeasePermit(
            scope=ExecutionScope(venue="paper", account="strategy-a"),
            holder="release-aaaa/process-1", fencing_token=1,
            expires_ms=1_767_268_830_000,
        )
    """

    scope: ExecutionScope
    holder: str
    fencing_token: int
    expires_ms: int

    def __post_init__(self):
        """Refuse any member no gateway could act on — every problem at once."""
        problems = []
        _check_scope(problems, "LeasePermit.scope", self.scope)
        _check_str(problems, "LeasePermit.holder", self.holder)
        if isinstance(self.fencing_token, bool) or not isinstance(self.fencing_token, int):
            problems.append(f"LeasePermit.fencing_token must be an int, got {self.fencing_token!r}")
        elif self.fencing_token < _FIRST_TOKEN:
            problems.append(f"LeasePermit.fencing_token must be >= {_FIRST_TOKEN}, got {self.fencing_token}")
        if isinstance(self.expires_ms, bool) or not isinstance(self.expires_ms, int):
            problems.append(f"LeasePermit.expires_ms must be an epoch-ms int, got {self.expires_ms!r}")
        if problems:
            raise ProductionError(problems)


class Lease(ABC):
    """The coordination seam (§5.7.2): who owns an execution scope right now.

    A child implements the four hooks over its service (a database row, a
    coordination store); ``permit_current`` is concrete so the fencing
    rule has one owner. A base that defaulted ``LIVE_CAPABLE`` to True
    would make every fake lease live, so it is False until a child says
    otherwise.

    Examples
    --------
    A lease over a shared dict, complete enough to construct::

        class DictLease(Lease):
            def __init__(self, table, clock):
                self.table, self.clock, self.token = table, clock, 0

            def acquire(self, scope, holder, ttl_ms):
                self.token += 1
                permit = LeasePermit(scope, holder, self.token, self.clock.now_ms() + ttl_ms)
                self.table[scope] = permit
                return permit

            def renew(self, permit):
                return self.acquire(permit.scope, permit.holder, 30_000)

            def current(self, scope):
                return self.table.get(scope)

            def release(self, permit):
                self.table.pop(permit.scope, None)

        lease = DictLease({}, clock)
        lease.permit_current(lease.acquire(scope, "release-a/process-1", 30_000))  # True
    """

    #: Only a child that says so may back a live document (§5.7.2).
    LIVE_CAPABLE = False

    @abstractmethod
    def acquire(self, scope, holder, ttl_ms):
        """Take ``scope`` for ``holder`` for ``ttl_ms``.

        Parameters
        ----------
        scope : ExecutionScope
        holder : str
        ttl_ms : int

        Returns
        -------
        LeasePermit

        Raises
        ------
        ProductionError
            When another holder's unexpired permit stands.
        """

    @abstractmethod
    def renew(self, permit):
        """Extend a current permit, raising its fencing token.

        Parameters
        ----------
        permit : LeasePermit
            Must be what ``current(permit.scope)`` answers.

        Returns
        -------
        LeasePermit

        Raises
        ------
        ProductionError
            When ``permit`` is not current.
        """

    @abstractmethod
    def current(self, scope):
        """Return the unexpired permit on ``scope``, or None.

        Parameters
        ----------
        scope : ExecutionScope

        Returns
        -------
        LeasePermit or None
        """

    @abstractmethod
    def release(self, permit):
        """Free ``scope`` — only for the permit that holds it.

        Parameters
        ----------
        permit : LeasePermit

        Raises
        ------
        ProductionError
            When ``permit`` is not current.
        """

    def permit_current(self, permit):
        """Say whether ``permit`` is exactly what the lease holds now — the local fencing check.

        Parameters
        ----------
        permit : LeasePermit

        Returns
        -------
        bool
            False for anything that is not a ``LeasePermit``, an expired or
            released permit, a superseded token, or a permit this lease
            never issued.
        """
        if not isinstance(permit, LeasePermit):
            return False
        return self.current(permit.scope) == permit


class ProcessLease(Lease):
    """The in-process lease — valid for ``shadow``/``paper`` only (§5.7.2).

    One table of scope -> permit and one monotonic token counter shared by
    every scope, so no two permits this lease ever issued carry the same
    fence. Expiry is judged against the injected clock, inclusive at
    ``expires_ms``; an expired grip lapses and the next holder may take
    the scope.

    Parameters
    ----------
    params : dict or None
        Must be empty (``notes`` aside) — default-deny over ``_PARAMS``.
    clock : Clock
        ``now_ms()`` stamps expiries and judges them.

    Examples
    --------
    ::

        lease = ProcessLease({}, clock=clock)
        scope = ExecutionScope(venue="paper", account="strategy-a")
        permit = lease.acquire(scope, "release-a/process-1", 30_000)
        permit.fencing_token  # 1
        lease.renew(permit).fencing_token  # 2
    """

    LIVE_CAPABLE = False
    _PARAMS = ()

    def __init__(self, params=None, *, clock):
        params = dict(params or {})
        problems = self.validate_params(params)
        if problems:
            raise ProductionError(problems)
        self._clock = clock
        self._held = {}
        self._ttl = {}
        self._token = _FIRST_TOKEN - 1

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when acceptable.

        Parameters
        ----------
        params : dict

        Returns
        -------
        list of str
            Default-deny over ``_PARAMS`` and ``notes``.
        """
        problems = []
        reject_unknown_params(problems, params, tuple(cls._PARAMS) + _NOTES)
        return problems

    def acquire(self, scope, holder, ttl_ms):
        """Take ``scope`` unless an unexpired permit — anyone's — stands on it.

        Parameters
        ----------
        scope : ExecutionScope
        holder : str
        ttl_ms : int
            ``>= 1``; remembered for renewals.

        Returns
        -------
        LeasePermit

        Raises
        ------
        ProductionError
            On a malformed argument, or while another permit holds the
            scope — the refusal names its holder.
        """
        problems = []
        _check_scope(problems, "scope", scope)
        _check_str(problems, "holder", holder)
        check_int_param(problems, "ttl_ms", ttl_ms, ge=1)
        if problems:
            raise ProductionError(problems)
        held = self.current(scope)
        if held is not None:
            raise ProductionError(
                [
                    f"scope {scope.to_obj()} is held by {held.holder!r} "
                    f"(token {held.fencing_token}) until {held.expires_ms}"
                ]
            )
        return self._issue(scope, holder, ttl_ms)

    def renew(self, permit):
        """Reissue a current permit with the next token and a fresh ttl from now.

        Parameters
        ----------
        permit : LeasePermit

        Returns
        -------
        LeasePermit

        Raises
        ------
        ProductionError
            When ``permit`` is not exactly the current permit on its scope.
        """
        self._require_current(permit, "renew")
        return self._issue(permit.scope, permit.holder, self._ttl[permit.scope])

    def current(self, scope):
        """Return the unexpired permit on ``scope``, or None.

        Parameters
        ----------
        scope : ExecutionScope

        Returns
        -------
        LeasePermit or None
            None once ``expires_ms`` has been reached.

        Raises
        ------
        ProductionError
            If ``scope`` is not an ``ExecutionScope``.
        """
        problems = []
        _check_scope(problems, "scope", scope)
        if problems:
            raise ProductionError(problems)
        permit = self._held.get(scope)
        if permit is None or permit.expires_ms <= self._clock.now_ms():
            return None
        return permit

    def release(self, permit):
        """Free the scope ``permit`` holds.

        Parameters
        ----------
        permit : LeasePermit

        Raises
        ------
        ProductionError
            When ``permit`` is not exactly the current permit on its scope.
        """
        self._require_current(permit, "release")
        del self._held[permit.scope]
        del self._ttl[permit.scope]

    def _issue(self, scope, holder, ttl_ms):
        """Mint the next token for ``scope`` and remember its ttl."""
        self._token += 1
        permit = LeasePermit(
            scope=scope, holder=holder, fencing_token=self._token,
            expires_ms=self._clock.now_ms() + ttl_ms,
        )
        self._held[scope] = permit
        self._ttl[scope] = ttl_ms
        return permit

    def _require_current(self, permit, verb):
        """Refuse ``verb`` for anything but the exact current permit."""
        if not isinstance(permit, LeasePermit):
            raise ProductionError([f"cannot {verb}: {permit!r} is not a LeasePermit"])
        if not self.permit_current(permit):
            raise ProductionError(
                [
                    f"cannot {verb}: permit (holder {permit.holder!r}, token "
                    f"{permit.fencing_token}) is not the current lease on its scope"
                ]
            )


def scope_equal(*scopes):
    """Say whether every scope given is the same ``ExecutionScope`` (§5.7.2).

    The one owner of the exact-equality rule startup, each tick and the
    final verifier apply among the actual, document, release, lease and
    ``ActPermit`` scopes.

    Parameters
    ----------
    *scopes : ExecutionScope
        At least two.

    Returns
    -------
    bool

    Raises
    ------
    ProductionError
        With fewer than two scopes (comparing one with itself proves
        nothing), or when any is missing or not an ``ExecutionScope`` —
        a ``None`` means a scope was never obtained, which must refuse
        rather than compare false.
    """
    problems = []
    if len(scopes) < 2:
        problems.append(f"scope_equal compares at least two scopes, got {len(scopes)}")
    for index, scope in enumerate(scopes):
        _check_scope(problems, f"scope[{index}]", scope)
    if problems:
        raise ProductionError(problems)
    first = scopes[0]
    return all(scope == first for scope in scopes[1:])


class LeaseRenewer:
    """The renewal cadence, driven synchronously by a supervised worker (§5.7.2).

    ``tick(now_ms)`` renews once ``every_ms`` has elapsed since the last
    successful renewal. A missed deadline — more than ``every_ms +
    timeout_ms`` elapsed — or a refused renewal invalidates the local
    permit immediately, without raising: losing the lease disables submit
    and leaves query, reconcile and cancel alone. Once invalidated the
    renewer never touches the lease again.

    Parameters
    ----------
    lease : Lease
    permit : LeasePermit
        The permit acquired before reconciliation.
    clock : Clock
        Supplies the instant construction counts from.
    every_ms : int
        ``document.coordination.renew_every_ms``, ``>= 1``.
    timeout_ms : int
        ``document.coordination.renew_timeout_ms``, ``>= 1``.

    Attributes
    ----------
    permit : LeasePermit or None
        The current local permit; None once invalidated.
    invalidated : bool

    Examples
    --------
    ::

        renewer = LeaseRenewer(lease, permit, clock=clock, every_ms=10_000, timeout_ms=2_000)
        renewer.tick(clock.now_ms() + 10_000).fencing_token  # 2
        renewer.invalidated  # False
    """

    def __init__(self, lease, permit, *, clock, every_ms, timeout_ms):
        problems = []
        if not isinstance(lease, Lease):
            problems.append(f"lease must be a Lease, got {lease!r}")
        if not isinstance(permit, LeasePermit):
            problems.append(f"permit must be a LeasePermit, got {permit!r}")
        check_int_param(problems, "every_ms", every_ms, ge=1)
        check_int_param(problems, "timeout_ms", timeout_ms, ge=1)
        if problems:
            raise ProductionError(problems)
        self._lease = lease
        self._permit = permit
        self._every_ms = every_ms
        self._deadline_ms = every_ms + timeout_ms
        self._last_ok_ms = clock.now_ms()
        self._invalidated = False

    @property
    def permit(self):
        """The current local permit, or None once invalidated."""
        return self._permit

    @property
    def invalidated(self):
        """Whether the local permit has been invalidated."""
        return self._invalidated

    def tick(self, now_ms):
        """Renew if due; invalidate on a missed deadline or a refusal.

        Parameters
        ----------
        now_ms : int
            The worker's instant.

        Returns
        -------
        LeasePermit or None
            The permit a submit would fence with — renewed when the
            cadence was due — or None once invalidated.

        Raises
        ------
        ProductionError
            If ``now_ms`` is not an int.
        """
        if isinstance(now_ms, bool) or not isinstance(now_ms, int):
            raise ProductionError([f"now_ms must be an epoch-ms int, got {now_ms!r}"])
        if self._invalidated:
            return None
        elapsed = now_ms - self._last_ok_ms
        if elapsed > self._deadline_ms:
            return self._invalidate(f"missed the renewal deadline: {elapsed} ms since the last success")
        if elapsed < self._every_ms:
            return self._permit
        try:
            renewed = self._lease.renew(self._permit)
        except Exception as exc:  # a lost lease disables submit; it never kills the loop
            return self._invalidate(f"renewal refused: {exc}")
        if not isinstance(renewed, LeasePermit):
            return self._invalidate(f"lease.renew answered {renewed!r}, not a LeasePermit")
        self._permit = renewed
        self._last_ok_ms = now_ms
        return renewed

    def _invalidate(self, why):
        """Drop the local permit for good and say why, redacted."""
        _LOG.warning("lease permit invalidated: %s", redact(why))
        self._invalidated = True
        self._permit = None
        return None


# ---------------------------------------------------------------------------
# The registry (§4.3) — import is registration
# ---------------------------------------------------------------------------

LEASE_KINDS = Registry("lease", Lease)
LEASE_KINDS.register("process", ProcessLease)
