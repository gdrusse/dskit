"""The venue seam: read, query and cancel for everyone; ``submit`` for the few (§5.7, D14).

The hierarchy is the §5.15 Liskov split, and the split exists for one
reason: a subclass may never strengthen a precondition. ``Executor`` is the
read/query/cancel surface — ``capabilities``, ``check``, ``execution_scope``,
``order``, ``open_orders``, ``fills``, ``balances``, the concrete
``positions``/``settlements``/``events``/``venue_time_ms``/``cancel_all`` —
and is always constructible and never armed, which is what lets recovery and
a halt's cancel survive every failure the submit path can have.
``SubmittingExecutor`` adds the one verb that moves money,
``submit(intent, permit, state)``, with ``permit`` required of every
subclass and typed against the ``Permit`` base. The base contract is
TOTAL: ``submit`` returns an ``Ack`` describing what happened, including a
refusal — a non-permit is ``Ack(not_sent, reason="permit_type")``, never a
raise — so a caller holding an ``Executor`` can always recover and cancel
without knowing the rung.

Core ships three simulated venues and one abstract wrapper. ``ShadowExecutor``
decides and declines: ``submit`` answers ``not_sent``/``shadow`` and nothing
here can reach a socket. ``PaperExecutor`` is a deterministic order book fed
``on_quote``: its fill, resting, size-cap and time-in-force rules are strategy
objects keyed by the ``vocab`` members a document spells, fees are
``FEE_KINDS`` strategies, every instant comes from the injected clock plus
the declared latency, and the only randomness is ``random.Random(seed)`` —
so a replay reproduces every fill. ``RecordedExecutor`` replays a tape of
``(method, args, answer)`` triples and invents nothing. ``LiveExecutor``
holds the act gate: it refuses a non-``ActPermit`` BY TYPE and delegates the
indivisible verify/call sequence to ``SubmissionVerifier.verify_and_call``
with ``_submit_native`` as the callback — abstract, so only a child's venue
subclass is constructible and core can never send an order.

``executor_conformance_suite`` is the closed battery of §5.7, built the way
``dskit.pipeline.conformance.conformance_suite`` is: point it at a class and
get a pytest class back, so a child's venue is proven by the same suite that
proves ``PaperExecutor``.
"""

import dataclasses
import random
from abc import ABC, abstractmethod
from decimal import Decimal, InvalidOperation
from types import MappingProxyType

from dskit.pipeline.node import check_int_param
from dskit.production.base import ProductionError, Registry, pin_members, reject_unknown_params
from dskit.production.clock import Clock
from dskit.production.records import (
    Ack,
    ActPermit,
    ExecutionScope,
    Fill,
    Intent,
    OrderState,
    Permit,
    Quote,
    SimulatedPermit,
)
from dskit.production.redact import get_logger
from dskit.production.vocab import (
    DEDUPE_MODES,
    FEE_KIND_NAMES,
    FENCING_MODES,
    FILL_RULES,
    FILL_STATUSES,
    LIQUIDITY,
    OPERATIONS,
    POSITION_MODELS,
    POSITION_SOURCES,
    RESTING_RULES,
    SIDES,
    SIZE_CAPS,
    STATUSES,
    TERMINAL_STATUSES,
    TIFS,
)

__all__ = [
    "BpsFee",
    "Capabilities",
    "DEFAULT_FEE_KIND",
    "DEFAULT_FILL_RULE",
    "DEFAULT_LATENCY_MS",
    "DEFAULT_PARTIAL_FILLS",
    "DEFAULT_P_FILL_ON_TOUCH",
    "DEFAULT_QUEUE_FRAC",
    "DEFAULT_RESTING_RULE",
    "DEFAULT_SEED",
    "DEFAULT_SIZE_CAP",
    "EXECUTOR_KINDS",
    "Executor",
    "FEE_KINDS",
    "Fee",
    "LIVE_FENCING",
    "LiveExecutor",
    "MakerTakerBpsFee",
    "NoFee",
    "PaperExecutor",
    "PerUnitFee",
    "PxqRateFee",
    "RecordedExecutor",
    "SIMULATED_SCOPE",
    "SIMULATED_UNITS",
    "ShadowExecutor",
    "SubmittingExecutor",
    "empty_ack",
    "executor_conformance_suite",
]

_LOG = get_logger("executor")
_NOTES = ("notes",)
_ZERO = Decimal(0)
_ONE = Decimal(1)
#: Basis points per unit of price.
_BPS = Decimal(10_000)

# ---------------------------------------------------------------------------
# Named defaults — one name each, read by ``validate_params`` and the run alike
# ---------------------------------------------------------------------------

DEFAULT_FILL_RULE = pin_members("executor.py's DEFAULT_FILL_RULE", ("touch",), FILL_RULES)[0]
DEFAULT_RESTING_RULE = pin_members(
    "executor.py's DEFAULT_RESTING_RULE", ("touch",), RESTING_RULES
)[0]
DEFAULT_SIZE_CAP = pin_members("executor.py's DEFAULT_SIZE_CAP", ("none",), SIZE_CAPS)[0]
DEFAULT_FEE_KIND = pin_members("executor.py's DEFAULT_FEE_KIND", ("none",), FEE_KIND_NAMES)[0]
DEFAULT_P_FILL_ON_TOUCH = 1.0
DEFAULT_QUEUE_FRAC = 0.0
DEFAULT_SEED = 0
DEFAULT_PARTIAL_FILLS = True
DEFAULT_LATENCY_MS = 0

#: The fence every live executor must declare (§5.7.2).
LIVE_FENCING = pin_members("executor.py's LIVE_FENCING", ("submit_token",), FENCING_MODES)[0]

#: What a simulated venue answers for ``execution_scope()`` when ``compose``
#: has not handed it the document's ``coordination.scope``: no venue at all.
SIMULATED_SCOPE = ExecutionScope(venue="simulated", account="simulated")

#: The units a simulated venue trades in — abstract units priced in the
#: quote's own currency, since a paper book has no opinion of its own.
SIMULATED_UNITS = MappingProxyType({"qty": "unit", "price": "quote", "cash": "quote"})

#: The three unit names §5.7's ``units`` block declares (a vocab.py candidate).
_UNIT_NAMES = ("qty", "price", "cash")

#: The two verbs a paper venue models latency for.
_LATENCY_VERBS = pin_members("executor.py's latency verbs", ("submit", "cancel"), OPERATIONS)

#: The order statuses this module spells for itself, pinned to ``STATUSES``.
_OPEN, _PARTIAL, _FILLED, _CANCELLED, _EXPIRED, _REJECTED, _NOT_SENT, _UNKNOWN = pin_members(
    "executor.py's statuses",
    ("open", "partial", "filled", "cancelled", "expired", "rejected", "not_sent", "unknown"),
    STATUSES,
)
_FINAL, _REVERSED = pin_members("executor.py's fill statuses", ("final", "reversed"), FILL_STATUSES)
_MAKER, _TAKER = pin_members("executor.py's liquidity flags", ("maker", "taker"), LIQUIDITY)
_DERIVED, _VENUE = pin_members(
    "executor.py's position sources", ("derived", "venue"), POSITION_SOURCES
)

# ---------------------------------------------------------------------------
# Small shared rules
# ---------------------------------------------------------------------------


def _member(problems, name, value, closed):
    """Append a problem unless ``value`` is a member of ``closed``."""
    if value not in closed:
        problems.append(f"{name} must be one of {list(closed)}, got {value!r}")


def _exact(problems, name, value):
    """Return ``value`` as a finite non-negative Decimal, or append why it is not one."""
    if isinstance(value, bool) or isinstance(value, float):
        problems.append(f"{name} must be spelled exactly (an int, a string or a Decimal), got {value!r}")
        return None
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        problems.append(f"{name} must be a number, got {value!r}")
        return None
    if isinstance(value, (int, str, Decimal)) and amount.is_finite() and amount >= _ZERO:
        return amount
    problems.append(f"{name} must be a finite number >= 0, got {value!r}")
    return None


def _ratio(problems, name, value, *, top_inclusive):
    """Return ``value`` as a float in ``[0, 1]`` (or ``[0, 1)``), or append why it is not."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.append(f"{name} must be a number, got {value!r}")
        return None
    top_ok = value <= 1.0 if top_inclusive else value < 1.0
    if not (value >= 0.0 and top_ok):
        bound = "[0, 1]" if top_inclusive else "[0, 1)"
        problems.append(f"{name} must be in {bound}, got {value!r}")
        return None
    return float(value)


def _block(problems, name, value):
    """Return ``value`` as a dict when it is a mapping block, else append a problem."""
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        problems.append(f"{name} must be a block (a mapping with string keys), got {value!r}")
        return None
    return dict(value)


def _intent_of(intent):
    """Return ``intent``, refusing anything that is not the canonical ``Intent`` (§5.4)."""
    if not isinstance(intent, Intent):
        raise ProductionError([f"submit takes an Intent, got {intent!r}"])
    return intent


def empty_ack(client_ref, ts_ms, status, reason):
    """Return an ``Ack`` that carries no venue reference and no fill.

    The one recipe for every answer that did not trade: a ``not_sent``
    refusal, a venue's ``rejected``, or an ``unknown`` after a raise.

    Parameters
    ----------
    client_ref : str
        The intent's reference the fold matches the answer by.
    ts_ms : int
        The injected clock's reading.
    status : str
        A ``STATUSES`` member.
    reason : str
        Why — a verifier reason, a policy rule name or a venue's word.

    Returns
    -------
    Ack
        ``venue_ref`` None, ``filled_qty`` 0, ``avg_price`` None, ``fee`` 0,
        ``native`` None.
    """
    return Ack(
        client_ref=client_ref,
        venue_ref=None,
        status=status,
        ts_ms=ts_ms,
        filled_qty=_ZERO,
        avg_price=None,
        fee=_ZERO,
        reason=reason,
        native=None,
    )


def _ack_of(order):
    """Return the ``Ack`` view of an ``OrderState``."""
    return Ack(**{f.name: getattr(order, f.name) for f in dataclasses.fields(Ack)})


# ---------------------------------------------------------------------------
# Capabilities — read before any I/O (§5.7)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Capabilities:
    """What a venue can do, declared once and read before any I/O (§5.7).

    Every member gates a safety behaviour — whether positions may be
    compared, whether a re-used reference replays, whether the lease fence
    rides on submits — so each closed member is validated against its
    ``vocab`` set and the value is frozen: a capability block that could be
    widened at run time is not a capability.

    Parameters
    ----------
    tifs : tuple of str
        The ``TIFS`` members the venue accepts.
    market_orders : bool
    notional : bool
        Whether orders may be sized by notional rather than quantity.
    positions : str
        One of ``POSITION_SOURCES`` — ``venue`` when the venue reports
        positions, ``derived`` when only the fold knows them.
    settlements : bool
    stream : bool
    dedupe : str
        One of ``DEDUPE_MODES``: what a re-used ``client_ref`` does.
    units : mapping
        ``{"qty", "price", "cash"}`` to non-empty unit names.
    position_model : str
        One of ``POSITION_MODELS``.
    fencing : str
        One of ``FENCING_MODES``; a live executor must declare
        ``submit_token``.

    Examples
    --------
    ::

        caps = Capabilities(
            tifs=("ioc", "gtc"), market_orders=True, notional=False, positions="derived",
            settlements=False, stream=False, dedupe="replays",
            units={"qty": "share", "price": "USD", "cash": "USD"},
            position_model="netting", fencing="none",
        )
        caps.units["cash"]  # 'USD'
    """

    tifs: tuple
    market_orders: bool
    notional: bool
    positions: str
    settlements: bool
    stream: bool
    dedupe: str
    units: MappingProxyType
    position_model: str
    fencing: str

    def __post_init__(self):
        """Validate every closed member and freeze the two containers."""
        problems = []
        if isinstance(self.tifs, str) or not isinstance(self.tifs, (list, tuple)):
            problems.append(f"Capabilities.tifs must be a sequence, got {self.tifs!r}")
        else:
            for tif in self.tifs:
                _member(problems, "Capabilities.tifs entry", tif, TIFS)
        for name in ("market_orders", "notional", "settlements", "stream"):
            if not isinstance(getattr(self, name), bool):
                problems.append(f"Capabilities.{name} must be a bool, got {getattr(self, name)!r}")
        _member(problems, "Capabilities.positions", self.positions, POSITION_SOURCES)
        _member(problems, "Capabilities.dedupe", self.dedupe, DEDUPE_MODES)
        _member(problems, "Capabilities.position_model", self.position_model, POSITION_MODELS)
        _member(problems, "Capabilities.fencing", self.fencing, FENCING_MODES)
        raw = dict(self.units) if isinstance(self.units, MappingProxyType) else self.units
        units = _block(problems, "Capabilities.units", raw)
        if units is not None:
            if set(units) != set(_UNIT_NAMES):
                problems.append(f"Capabilities.units must declare exactly {list(_UNIT_NAMES)}, got {sorted(units)}")
            for name, value in units.items():
                if not isinstance(value, str) or not value:
                    problems.append(f"Capabilities.units[{name!r}] must be a non-empty string, got {value!r}")
        if problems:
            raise ProductionError(problems)
        object.__setattr__(self, "tifs", tuple(self.tifs))
        object.__setattr__(self, "units", MappingProxyType(dict(units)))


# ---------------------------------------------------------------------------
# Fees — one strategy per kind, registry-resolved (§4.3, §5.7)
# ---------------------------------------------------------------------------

#: Which knob each liquidity flag pays; ``unknown`` pays the larger of the two.
_LIQUIDITY_KNOBS = pin_members(
    "executor.py's liquidity knobs", {"maker": "maker_bps", "taker": "taker_bps"}, LIQUIDITY
)


class Fee(ABC):
    """One fee strategy: ``charge(qty, price, liquidity) -> Decimal`` (§5.7).

    Constructed as ``cls(params)`` from a document's ``fees`` block minus
    its ``kind``; every knob the kind names is required and spelled
    exactly (an int, a decimal string or a ``Decimal`` — never a float,
    because a fee is money). ``charge`` refuses a liquidity flag outside
    ``LIQUIDITY`` and hands the exact amounts to the kind's closed form.

    Parameters
    ----------
    params : dict, optional
        The kind's knobs; ``notes`` is allowed beside them.

    Raises
    ------
    ProductionError
        On an unknown key, a missing knob, or a knob that is not an exact
        non-negative number.

    Examples
    --------
    A flat fee per unit traded::

        class Flat(Fee):
            _PARAMS = ("per_unit",)

            def _charge(self, qty, price, liquidity):
                return qty * self.knob("per_unit")

        Flat({"per_unit": "0.01"}).charge(Decimal("10"), Decimal("0.42"), "taker")
        # -> Decimal('0.10')
    """

    #: The knobs the kind requires; ``notes`` is always allowed beside them.
    _PARAMS = ()

    def __init__(self, params=None):
        params = dict(params or {})
        problems = self.validate_params(params)
        if problems:
            raise ProductionError(problems)
        self._knobs = MappingProxyType({name: _decimal(params[name]) for name in self._PARAMS})

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when it is acceptable.

        Parameters
        ----------
        params : dict
            The fee block as written, minus its ``kind``.

        Returns
        -------
        list of str
        """
        problems = []
        reject_unknown_params(problems, params, tuple(cls._PARAMS) + _NOTES)
        for name in cls._PARAMS:
            if name not in params:
                problems.append(f"fee knob {name!r} is required")
            else:
                _exact(problems, name, params[name])
        return problems

    def knob(self, name):
        """Return one declared knob as an exact ``Decimal``.

        Parameters
        ----------
        name : str
            A member of the kind's ``_PARAMS``.

        Returns
        -------
        Decimal
        """
        return self._knobs[name]

    def charge(self, qty, price, liquidity):
        """Return the fee for one fill.

        Parameters
        ----------
        qty : Decimal
        price : Decimal
        liquidity : str
            A ``LIQUIDITY`` member; ``unknown`` is charged conservatively.

        Returns
        -------
        Decimal

        Raises
        ------
        ProductionError
            On a liquidity flag outside the vocabulary, or a quantity or
            price that is not an exact number.
        """
        problems = []
        _member(problems, "liquidity", liquidity, LIQUIDITY)
        amounts = (_exact(problems, "qty", qty), _exact(problems, "price", price))
        if problems:
            raise ProductionError(problems)
        return self._charge(amounts[0], amounts[1], liquidity)

    @abstractmethod
    def _charge(self, qty, price, liquidity):
        """Return the kind's closed form over exact amounts and a checked flag."""


class NoFee(Fee):
    """``kind: none`` — nothing is charged.

    Examples
    --------
    ::

        NoFee({}).charge(Decimal("10"), Decimal("0.42"), "taker")  # Decimal('0')
    """

    def _charge(self, qty, price, liquidity):
        return _ZERO


class PerUnitFee(Fee):
    """``kind: per_unit`` — a flat amount per unit of quantity.

    Examples
    --------
    ::

        PerUnitFee({"per_unit": "0.01"}).charge(Decimal("10"), Decimal("0.42"), "taker")
        # -> Decimal('0.10')
    """

    _PARAMS = ("per_unit",)

    def _charge(self, qty, price, liquidity):
        return qty * self.knob("per_unit")


class BpsFee(Fee):
    """``kind: bps`` — basis points of the notional, whatever the liquidity.

    Examples
    --------
    ::

        BpsFee({"bps": 5}).charge(Decimal("10"), Decimal("0.42"), "taker")  # Decimal('0.0021')
    """

    _PARAMS = ("bps",)

    def _charge(self, qty, price, liquidity):
        return qty * price * self.knob("bps") / _BPS


class MakerTakerBpsFee(Fee):
    """``kind: maker_taker_bps`` — one rate per side of the book.

    A fill whose liquidity is ``unknown`` pays the larger rate: a model
    that cannot tell which side it was must not assume the cheaper one.

    Examples
    --------
    ::

        fee = MakerTakerBpsFee({"maker_bps": 1, "taker_bps": 5})
        fee.charge(Decimal("10"), Decimal("0.42"), "maker")  # Decimal('0.00042')
        fee.charge(Decimal("10"), Decimal("0.42"), "unknown")  # Decimal('0.0021')
    """

    _PARAMS = tuple(_LIQUIDITY_KNOBS.values())

    def _charge(self, qty, price, liquidity):
        rates = {flag: self.knob(name) for flag, name in _LIQUIDITY_KNOBS.items()}
        return qty * price * rates.get(liquidity, max(rates.values())) / _BPS


class PxqRateFee(Fee):
    """``kind: pxq_rate`` — a plain rate on price times quantity.

    Examples
    --------
    ::

        PxqRateFee({"rate": "0.001"}).charge(Decimal("10"), Decimal("0.42"), "taker")
        # -> Decimal('0.0042')
    """

    _PARAMS = ("rate",)

    def _charge(self, qty, price, liquidity):
        return qty * price * self.knob("rate")


FEE_KINDS = Registry("fee", Fee)
FEE_KINDS.register("none", NoFee)
FEE_KINDS.register("per_unit", PerUnitFee)
FEE_KINDS.register("bps", BpsFee)
FEE_KINDS.register("maker_taker_bps", MakerTakerBpsFee)
FEE_KINDS.register("pxq_rate", PxqRateFee)
pin_members("executor.py's FEE_KINDS", FEE_KINDS.kinds(), FEE_KIND_NAMES, exact=True)


# ---------------------------------------------------------------------------
# The seam — read, query, cancel (§5.7, §5.15)
# ---------------------------------------------------------------------------


class Executor(ABC):
    """Read, query and cancel — always constructible, never armed (§5.7, D14).

    Constructed as ``cls(params, clock=clock)`` from the document's
    ``execution`` site, default-deny over the subclass's ``_PARAMS`` plus
    ``notes``. The eight hooks a venue must answer for itself are abstract,
    so an incomplete subclass refuses to construct; the five verbs core can
    answer once for everyone are concrete, and a ``positions: venue`` child
    overrides ``positions()`` without inheriting derivation code — fill
    derivation belongs to ``SeriesState``'s ``PositionBook``, not here.

    Parameters
    ----------
    params : dict, optional
        The ``{uses, params}`` site's ``params``; ``None`` means ``{}``.
    clock : Clock
        Every instant an executor stamps comes from here.

    Raises
    ------
    ProductionError
        On a key outside ``_PARAMS`` and ``notes``, or a missing clock.

    Examples
    --------
    The smallest venue: read-only, nothing working::

        class Empty(Executor):
            def capabilities(self):
                return Capabilities(
                    tifs=("ioc",), market_orders=True, notional=False, positions="derived",
                    settlements=False, stream=False, dedupe="none",
                    units={"qty": "unit", "price": "quote", "cash": "quote"},
                    position_model="netting", fencing="none",
                )

            def check(self, config):
                return ()

            def execution_scope(self):
                return ExecutionScope(venue="paper", account="strategy-a")

            def cancel(self, ref):
                return empty_ack(ref, self._clock.now_ms(), "not_sent", "nothing working")

            def order(self, ref):
                return None

            def open_orders(self):
                return ()

            def fills(self, since_ms, cursor=None):
                return ((), None)

            def balances(self):
                return ()

        venue = Empty({}, clock=TestClock())
        venue.cancel_all()  # ()
    """

    #: The knobs the subclass accepts; ``notes`` is always allowed beside them.
    _PARAMS = ()
    #: The knobs, among ``_PARAMS``, whose value names an environment variable.
    _SECRETS = ()

    def __init__(self, params=None, *, clock):
        params = dict(params or {})
        problems = self.validate_params(params)
        if not isinstance(clock, Clock):
            problems.append(f"an executor needs an injected Clock, got {clock!r}")
        if problems:
            raise ProductionError(problems)
        self._params = MappingProxyType(params)
        self._clock = clock

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when it is acceptable.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending key.
        """
        problems = []
        reject_unknown_params(problems, params, tuple(cls._PARAMS) + _NOTES)
        return problems

    @classmethod
    def spec(cls):
        """Return the knob inventory: ``{"params": ..., "secrets": ...}`` (§5.7).

        Returns
        -------
        dict
            ``params`` — the default-deny knob names; ``secrets`` — those
            among them whose value names an environment variable, so a
            secret is never a literal inside a graded document.

        Raises
        ------
        ProductionError
            If a secret knob is not one of the class's params.
        """
        stray = sorted(set(cls._SECRETS) - set(cls._PARAMS))
        if stray:
            raise ProductionError([f"{cls.__name__}: secret knob(s) {stray} are not params"])
        return {"params": tuple(cls._PARAMS), "secrets": tuple(cls._SECRETS)}

    @abstractmethod
    def capabilities(self):
        """Return the venue's :class:`Capabilities`."""

    @abstractmethod
    def check(self, config):
        """Return a tuple of problems with the document's ``execution`` block; empty when it can serve it."""

    @abstractmethod
    def execution_scope(self):
        """Return the authenticated ``ExecutionScope`` this executor acts in."""

    @abstractmethod
    def cancel(self, ref):
        """Cancel one order by ``client_ref`` and return an ``Ack``."""

    @abstractmethod
    def order(self, ref):
        """Return the ``OrderState`` for ``client_ref``, or None when the venue has none."""

    @abstractmethod
    def open_orders(self):
        """Return a tuple of the non-terminal ``OrderState``s this executor owns."""

    @abstractmethod
    def fills(self, since_ms, cursor=None):
        """Return ``(page, next_cursor)`` of ``Fill``s at or after ``since_ms``; None ends paging."""

    @abstractmethod
    def balances(self):
        """Return a tuple of ``Balance``s as the venue reports them."""

    def positions(self):
        """Return the venue's own positions — nothing unless it reports them.

        Returns
        -------
        tuple of Position
            Empty here: only a ``capabilities().positions == "venue"``
            child overrides this; ours are derived by the fold.
        """
        return ()

    def settlements(self, since_ms):
        """Return the settlements at or after ``since_ms``.

        Parameters
        ----------
        since_ms : int

        Returns
        -------
        tuple of Settlement
            Empty in phase 1.
        """
        return ()

    def events(self):
        """Return the venue's event stream — none in phase 1.

        Returns
        -------
        tuple
        """
        return ()

    def venue_time_ms(self):
        """Return the venue's clock as epoch ms, or None when it exposes none.

        Returns
        -------
        int or None
        """
        return None

    def cancel_all(self):
        """Cancel every working order this executor owns.

        Returns
        -------
        tuple of Ack
            One per open order, in ``open_orders`` order — only refs this
            executor owns, never another process's.
        """
        return tuple(self.cancel(order.client_ref) for order in self.open_orders())


class SubmittingExecutor(Executor):
    """An ``Executor`` that can also send: adds ``submit`` and nothing else (§5.15).

    ``submit`` is typed against the ``Permit`` base, so every subclass
    accepts the base contract; only ``LiveExecutor`` narrows, by refusing a
    non-``ActPermit`` with a value rather than a raise. ``permit`` is
    REQUIRED of every subclass — the split exists so no subclass demands
    more than the shared signature promises.

    Examples
    --------
    ::

        class Decline(ShadowExecutor):
            pass

        Decline({}, clock=TestClock()).submit(intent, permit, state).status  # 'not_sent'
    """

    @abstractmethod
    def submit(self, intent, permit, state):
        """Send ``intent`` under ``permit`` given the leg's ``TickState``; always return an ``Ack``."""


# ---------------------------------------------------------------------------
# ShadowExecutor — decides and declines (§5.7)
# ---------------------------------------------------------------------------


class ShadowExecutor(SubmittingExecutor):
    """The shadow rung's venue: records nothing, sends nothing, reaches no socket.

    ``submit`` answers ``Ack(not_sent, reason="shadow")`` for any permit,
    so a shadow run reads in the ledger as a run that decided and declined
    rather than one that failed. Every read answers empty.

    Parameters
    ----------
    params : dict, optional
        No knobs; ``notes`` is allowed.
    clock : Clock
    scope : ExecutionScope, optional
        The ownership domain this simulated venue answers for
        ``execution_scope()`` — ``compose`` passes the document's
        ``coordination.scope``, since a simulated venue has none of its own.
        Default :data:`SIMULATED_SCOPE`.

    Examples
    --------
    ::

        venue = ShadowExecutor({}, clock=TestClock(start_ms=0))
        venue.submit(intent, SimulatedPermit(...), state).reason  # 'shadow'
        venue.open_orders()  # ()
    """

    def __init__(self, params=None, *, clock, scope=None):
        super().__init__(params, clock=clock)
        self._scope = SIMULATED_SCOPE if scope is None else scope

    def capabilities(self):
        """Return the shadow venue's capabilities: every TIF, nothing fenced.

        Returns
        -------
        Capabilities
        """
        return Capabilities(
            tifs=TIFS,
            market_orders=True,
            notional=False,
            positions=_DERIVED,
            settlements=False,
            stream=False,
            dedupe="none",
            units=SIMULATED_UNITS,
            position_model="netting",
            fencing="none",
        )

    def check(self, config):
        """Return no problems: a shadow venue can serve any document.

        Parameters
        ----------
        config : mapping
            The document's ``execution`` block.

        Returns
        -------
        tuple
        """
        return ()

    def execution_scope(self):
        """Return the injected (or simulated) scope.

        Returns
        -------
        ExecutionScope
        """
        return self._scope

    def cancel(self, ref):
        """Answer ``not_sent``: nothing was ever sent, so nothing can be cancelled.

        Parameters
        ----------
        ref : str

        Returns
        -------
        Ack
        """
        return empty_ack(ref, self._clock.now_ms(), _NOT_SENT, "shadow")

    def order(self, ref):
        """Return None: the shadow venue holds no order.

        Parameters
        ----------
        ref : str

        Returns
        -------
        None
        """
        return None

    def open_orders(self):
        """Return no working orders.

        Returns
        -------
        tuple
        """
        return ()

    def fills(self, since_ms, cursor=None):
        """Return an empty page and no cursor.

        Parameters
        ----------
        since_ms : int
        cursor : object, optional

        Returns
        -------
        tuple
            ``((), None)``.
        """
        return ((), None)

    def balances(self):
        """Return no balances.

        Returns
        -------
        tuple
        """
        return ()

    def submit(self, intent, permit, state):
        """Decline: answer ``not_sent``/``shadow`` without touching anything.

        Parameters
        ----------
        intent : Intent
        permit : Permit
            Any permit; anything that is not one is ``permit_type``.
        state : TickState
            Ignored.

        Returns
        -------
        Ack

        Raises
        ------
        ProductionError
            If ``intent`` is not an ``Intent``.
        """
        ref = _intent_of(intent).client_ref
        if not isinstance(permit, Permit):
            raise ProductionError(["permit_type"])
        return empty_ack(ref, self._clock.now_ms(), _NOT_SENT, "shadow")


# ---------------------------------------------------------------------------
# PaperExecutor — the strategies its knobs select (§5.7)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Side:
    """One side of the book: its sign and the far touch it takes."""

    sign: Decimal
    far: str

    def far_of(self, quote):
        """Return the quote's far touch for this side (ask for a buy, bid for a sell)."""
        return getattr(quote, self.far)


_SIDES = pin_members(
    "executor.py's sides",
    {"buy": _Side(_ONE, "ask"), "sell": _Side(-_ONE, "bid")},
    SIDES,
)


@dataclasses.dataclass(frozen=True)
class _Reach:
    """Whether a limit has reached a reference price: at it, or strictly through it."""

    through: bool

    def reached(self, distance):
        """Say whether a signed ``limit - reference`` distance counts as reached."""
        return distance > _ZERO if self.through else distance >= _ZERO


def _far(side, quote):
    """Return the far touch as the reference price."""
    return side.far_of(quote)


def _mid(side, quote):
    """Return the mid as the reference price."""
    return quote.mid


@dataclasses.dataclass(frozen=True)
class _FillRule:
    """Where a marketable order prices, and how far its limit must reach to be one."""

    reference: object
    reach: _Reach


_FILL_RULES = pin_members(
    "executor.py's fill rules",
    {
        "touch": _FillRule(_far, _Reach(through=False)),
        "cross": _FillRule(_far, _Reach(through=True)),
        "mid": _FillRule(_mid, _Reach(through=False)),
    },
    FILL_RULES,
    exact=True,
)

_RESTING_RULES = pin_members(
    "executor.py's resting rules",
    {"touch": _Reach(through=False), "through": _Reach(through=True)},
    RESTING_RULES,
    exact=True,
)


class _SizeCap(ABC):
    """How much of an order one quote can absorb; built from the ``size_cap`` block minus ``kind``."""

    _PARAMS = ()

    def __init__(self, block):
        problems = []
        self.validate(problems, block)
        if problems:
            raise ProductionError(problems)
        self._block = dict(block)

    @classmethod
    def validate(cls, problems, block):
        """Append every problem with ``block`` for this cap kind."""
        reject_unknown_params(problems, block, tuple(cls._PARAMS) + _NOTES)
        for name in cls._PARAMS:
            if name not in block:
                problems.append(f"size_cap knob {name!r} is required")

    @abstractmethod
    def cap(self, qty):
        """Return how much of ``qty`` may fill now."""


class _NoCap(_SizeCap):
    """``none`` — the whole order."""

    def cap(self, qty):
        return qty


class _QuoteSizeCap(_SizeCap):
    """``quote_size`` — at most the declared size, since a ``Quote`` carries none."""

    _PARAMS = ("quote_size",)

    @classmethod
    def validate(cls, problems, block):
        super().validate(problems, block)
        if "quote_size" in block:
            size = _exact(problems, "size_cap.quote_size", block["quote_size"])
            if size is not None and size <= _ZERO:
                problems.append(f"size_cap.quote_size must be > 0, got {size}")

    def cap(self, qty):
        return min(qty, _decimal(self._block["quote_size"]))


class _FracCap(_SizeCap):
    """``frac`` — a fraction of the order."""

    _PARAMS = ("frac",)

    @classmethod
    def validate(cls, problems, block):
        super().validate(problems, block)
        if "frac" in block:
            frac = _ratio(problems, "size_cap.frac", block["frac"], top_inclusive=True)
            if frac is not None and frac <= 0.0:
                problems.append(f"size_cap.frac must be > 0, got {frac}")

    def cap(self, qty):
        return qty * Decimal(str(self._block["frac"]))


_SIZE_CAPS = pin_members(
    "executor.py's size caps",
    {"none": _NoCap, "quote_size": _QuoteSizeCap, "frac": _FracCap},
    SIZE_CAPS,
    exact=True,
)


def _no_deadline(expires_ms, session_end_ms):
    """Return no deadline: the order rests until filled or cancelled."""
    return None


def _at_expiry(expires_ms, session_end_ms):
    """Return the proposal's own expiry."""
    return expires_ms


def _at_session_end(expires_ms, session_end_ms):
    """Return the declared session end."""
    return session_end_ms


@dataclasses.dataclass(frozen=True)
class _Tif:
    """How a time-in-force behaves on the paper book."""

    rests: bool
    all_or_nothing: bool
    deadline: object
    needs_session_end: bool

    def available(self, session_end_ms):
        """Say whether the venue can honour this TIF with what the document declared."""
        return not self.needs_session_end or session_end_ms is not None


_TIFS = pin_members(
    "executor.py's time-in-force rules",
    {
        "ioc": _Tif(rests=False, all_or_nothing=False, deadline=_no_deadline, needs_session_end=False),
        "fok": _Tif(rests=False, all_or_nothing=True, deadline=_no_deadline, needs_session_end=False),
        "gtc": _Tif(rests=True, all_or_nothing=False, deadline=_no_deadline, needs_session_end=False),
        "gtd": _Tif(rests=True, all_or_nothing=False, deadline=_at_expiry, needs_session_end=False),
        "day": _Tif(rests=True, all_or_nothing=False, deadline=_at_session_end, needs_session_end=True),
    },
    TIFS,
    exact=True,
)


@dataclasses.dataclass(frozen=True)
class _Working:
    """One booked order and the expiry its proposal carried."""

    order: OrderState
    expires_ms: int


def _validate_fees(problems, block):
    """Append every problem with a ``fees`` block: its kind, then the kind's own knobs."""
    fees = _block(problems, "fees", block)
    if fees is None:
        return
    chosen = fees.pop("kind", None)
    if chosen not in FEE_KIND_NAMES:
        _member(problems, "fees.kind", chosen, FEE_KIND_NAMES)
        return
    problems.extend(FEE_KINDS.resolve(chosen).validate_params(fees))


def _validate_size_cap(problems, block):
    """Append every problem with a ``size_cap`` block: its kind, then the kind's own knobs."""
    cap = _block(problems, "size_cap", block)
    if cap is None:
        return
    chosen = cap.pop("kind", None)
    if chosen not in SIZE_CAPS:
        _member(problems, "size_cap.kind", chosen, SIZE_CAPS)
        return
    _SIZE_CAPS[chosen].validate(problems, cap)


def _validate_slippage(problems, block):
    """Append every problem with a ``slippage`` block: ``bps``, ``ticks`` and the ``tick`` size."""
    slip = _block(problems, "slippage", block)
    if slip is None:
        return
    reject_unknown_params(problems, slip, ("bps", "ticks", "tick") + _NOTES)
    for name in ("bps", "ticks", "tick"):
        if name in slip:
            _exact(problems, f"slippage.{name}", slip[name])
    if "ticks" in slip and "tick" not in slip:
        problems.append("slippage.ticks needs slippage.tick — a count of ticks without the size of one is not a price")


def _validate_latency(problems, block):
    """Append every problem with a ``latency_ms`` block: non-negative ints per verb."""
    latency = _block(problems, "latency_ms", block)
    if latency is None:
        return
    reject_unknown_params(problems, latency, _LATENCY_VERBS + _NOTES)
    for verb in _LATENCY_VERBS:
        if verb in latency:
            check_int_param(problems, f"latency_ms.{verb}", latency[verb], ge=0)


def _validate_int(problems, name, value):
    """Append a problem unless ``value`` is a plain int (a bool is not one)."""
    if isinstance(value, bool) or not isinstance(value, int):
        problems.append(f"{name} must be an int, got {value!r}")


def _decimal(value):
    """Return an already-validated exact amount as a ``Decimal``."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


class PaperExecutor(SubmittingExecutor):
    """A deterministic simulated venue, fed ``on_quote`` by the loop (§5.7).

    Every knob is a graded ``execution.params`` field and every rule it
    selects is a strategy object: ``fill_rule`` (``touch`` pays the far
    touch, ``cross`` needs the limit through it, ``mid`` fills at the mid),
    ``resting_rule`` (``touch``: the market coming TO a resting price is
    enough; ``through``: it must trade past it), ``size_cap`` (``none``,
    ``quote_size {quote_size}``, ``frac {frac}``), ``fees {kind, ...}``
    through ``FEE_KINDS``, ``slippage {bps, ticks, tick}`` moved adversely,
    ``latency_ms {submit, cancel}`` added to the injected clock, the
    time-in-force table (``ioc``/``fok`` never rest, ``gtd`` expires at the
    proposal's expiry, ``day`` at ``session_end_ms`` and is refused as a
    capability without it), ``p_fill_on_touch`` (each quote offers a
    resting order one draw), ``queue_frac`` (the share of the touched size
    ahead of us in the queue) and ``partial_fills``. Deterministic under
    ``seed``; no wall clock, no network. A re-used ``client_ref`` replays
    the original ``Ack`` (``dedupe: replays``).

    Parameters
    ----------
    params : dict, optional
        The eleven knobs above; ``notes`` is allowed.
    clock : Clock
    scope : ExecutionScope, optional
        As for :class:`ShadowExecutor`.

    Examples
    --------
    A buy at the ask, 5 bps taker fee::

        venue = PaperExecutor({"fill_rule": "touch", "fees": {"kind": "bps", "bps": 5}},
                              clock=TestClock(start_ms=0))
        venue.on_quote(Quote(instrument="INS1", bid=Decimal("0.40"), ask=Decimal("0.42"),
                             mid=Decimal("0.41"), asof_ms=0))
        ack = venue.submit(intent, SimulatedPermit(...), state)
        (ack.status, ack.avg_price, ack.fee)  # ('filled', Decimal('0.42'), Decimal('0.0021'))
    """

    _PARAMS = (
        "fees",
        "fill_rule",
        "latency_ms",
        "p_fill_on_touch",
        "partial_fills",
        "queue_frac",
        "resting_rule",
        "seed",
        "session_end_ms",
        "size_cap",
        "slippage",
    )

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params`` — one refusal per knob.

        Parameters
        ----------
        params : dict

        Returns
        -------
        list of str
        """
        problems = super().validate_params(params)
        if "fill_rule" in params:
            _member(problems, "fill_rule", params["fill_rule"], FILL_RULES)
        if "resting_rule" in params:
            _member(problems, "resting_rule", params["resting_rule"], RESTING_RULES)
        if "size_cap" in params:
            _validate_size_cap(problems, params["size_cap"])
        if "fees" in params:
            _validate_fees(problems, params["fees"])
        if "slippage" in params:
            _validate_slippage(problems, params["slippage"])
        if "latency_ms" in params:
            _validate_latency(problems, params["latency_ms"])
        if "p_fill_on_touch" in params:
            _ratio(problems, "p_fill_on_touch", params["p_fill_on_touch"], top_inclusive=True)
        if "queue_frac" in params:
            _ratio(problems, "queue_frac", params["queue_frac"], top_inclusive=False)
        if "seed" in params:
            _validate_int(problems, "seed", params["seed"])
        if "partial_fills" in params and not isinstance(params["partial_fills"], bool):
            problems.append(f"partial_fills must be a bool, got {params['partial_fills']!r}")
        if "session_end_ms" in params:
            _validate_int(problems, "session_end_ms", params["session_end_ms"])
        return problems

    def __init__(self, params=None, *, clock, scope=None):
        super().__init__(params, clock=clock)
        knobs = self._params
        self._scope = SIMULATED_SCOPE if scope is None else scope
        self._fill_rule = _FILL_RULES[knobs.get("fill_rule", DEFAULT_FILL_RULE)]
        self._resting_rule = _RESTING_RULES[knobs.get("resting_rule", DEFAULT_RESTING_RULE)]
        self._p_touch = float(knobs.get("p_fill_on_touch", DEFAULT_P_FILL_ON_TOUCH))
        self._queue_frac = Decimal(str(knobs.get("queue_frac", DEFAULT_QUEUE_FRAC)))
        self._partial = knobs.get("partial_fills", DEFAULT_PARTIAL_FILLS)
        self._session_end = knobs.get("session_end_ms")
        cap = dict(knobs.get("size_cap", {"kind": DEFAULT_SIZE_CAP}))
        self._size_cap = _SIZE_CAPS[cap.pop("kind")](cap)
        fees = dict(knobs.get("fees", {"kind": DEFAULT_FEE_KIND}))
        self._fee = FEE_KINDS.resolve(fees.pop("kind"))(fees)
        slippage = dict(knobs.get("slippage", {}))
        self._slip_bps = _decimal(slippage.get("bps", 0))
        self._slip_ticks = _decimal(slippage.get("ticks", 0)) * _decimal(slippage.get("tick", 0))
        latency = dict(knobs.get("latency_ms", {}))
        self._latency = {verb: latency.get(verb, DEFAULT_LATENCY_MS) for verb in _LATENCY_VERBS}
        self._rng = random.Random(knobs.get("seed", DEFAULT_SEED))
        self._quotes = {}
        self._book = {}
        self._acks = {}
        self._fills = []
        self._venue_refs = 0

    # -- the market -----------------------------------------------------

    def on_quote(self, quote):
        """Take one quote: update the market, then expire and fill what it reaches.

        Parameters
        ----------
        quote : Quote

        Raises
        ------
        ProductionError
            If ``quote`` is not a ``Quote``.
        """
        if not isinstance(quote, Quote):
            raise ProductionError([f"on_quote takes a Quote, got {quote!r}"])
        self._quotes[quote.instrument] = quote
        self._march(self._clock.now_ms())

    def capabilities(self):
        """Return the paper venue's capabilities; ``day`` only with a session end.

        Returns
        -------
        Capabilities
        """
        return Capabilities(
            tifs=tuple(name for name, tif in _TIFS.items() if tif.available(self._session_end)),
            market_orders=True,
            notional=False,
            positions=_DERIVED,
            settlements=False,
            stream=False,
            dedupe="replays",
            units=SIMULATED_UNITS,
            position_model="netting",
            fencing="none",
        )

    def check(self, config):
        """Return the problems with the ``execution`` block's params.

        Parameters
        ----------
        config : mapping
            The document's ``execution`` block; its ``params`` are judged.

        Returns
        -------
        tuple of str
        """
        problems = []
        block = _block(problems, "execution", config)
        if block is not None:
            params = _block(problems, "execution.params", block.get("params", {}))
            if params is not None:
                problems.extend(self.validate_params(params))
        return tuple(problems)

    def execution_scope(self):
        """Return the injected (or simulated) scope.

        Returns
        -------
        ExecutionScope
        """
        return self._scope

    def venue_time_ms(self):
        """Return the injected clock's reading: a paper venue's clock IS the loop's.

        Returns
        -------
        int
        """
        return self._clock.now_ms()

    # -- submit -----------------------------------------------------------

    def submit(self, intent, permit, state):
        """Price and book one order against the current market.

        Parameters
        ----------
        intent : Intent
        permit : Permit
            Any permit; anything that is not one is ``permit_type``.
        state : TickState
            Ignored.

        Returns
        -------
        Ack
            Stamped ``now + latency_ms.submit``; a re-used ``client_ref``
            returns the original answer.

        Raises
        ------
        ProductionError
            If ``intent`` is not an ``Intent``.
        """
        ref = _intent_of(intent).client_ref
        now = self._clock.now_ms() + self._latency["submit"]
        if not isinstance(permit, Permit):
            return empty_ack(ref, now, _NOT_SENT, "permit_type")
        if ref in self._acks:
            return self._acks[ref]
        self._acks[ref] = self._take(intent, now)
        return self._acks[ref]

    def _take(self, intent, now):
        """Gate, price and book one new order; return its ``Ack``."""
        ref, proposal = intent.client_ref, intent.proposal
        if proposal.tif not in self.capabilities().tifs:
            return empty_ack(ref, now, _REJECTED, "unsupported_tif")
        side = _SIDES.get(proposal.side)
        if side is None or proposal.qty is None:
            return empty_ack(ref, now, _REJECTED, "no_order")
        quote = self._quotes.get(proposal.instrument)
        if quote is None:
            return empty_ack(ref, now, _REJECTED, "no_quote")
        tif = _TIFS[proposal.tif]
        filled, price = self._marketable(side, proposal, tif, quote)
        self._venue_refs += 1
        venue_ref = f"v-{self._venue_refs}"
        fee = _ZERO
        if filled > _ZERO:
            fee = self._book_fill(ref, venue_ref, proposal.instrument, proposal.side, filled, price, _TAKER, now)
        order = OrderState(
            client_ref=ref,
            venue_ref=venue_ref,
            status=self._status(tif, filled, proposal.qty),
            ts_ms=now,
            filled_qty=filled,
            avg_price=price,
            fee=fee,
            reason="",
            native={},
            instrument=proposal.instrument,
            side=proposal.side,
            qty=proposal.qty,
            remaining_qty=proposal.qty - filled,
            limit=proposal.limit,
            tif=proposal.tif,
            created_ms=now,
            updated_ms=now,
        )
        self._book[ref] = _Working(order, proposal.expires_ms)
        return _ack_of(order)

    def _marketable(self, side, proposal, tif, quote):
        """Return ``(filled, price)`` for a new order against ``quote`` — ``(0, None)`` if it does not trade now."""
        reference = self._fill_rule.reference(side, quote)
        if proposal.limit is not None and not self._fill_rule.reach.reached(
            side.sign * (proposal.limit - reference)
        ):
            return _ZERO, None
        available = self._size_cap.cap(proposal.qty)
        if available <= _ZERO or (available < proposal.qty and (tif.all_or_nothing or not self._partial)):
            return _ZERO, None
        return available, self._slipped(side, reference)

    def _slipped(self, side, reference):
        """Return ``reference`` moved adversely by the declared slippage."""
        return reference * (_ONE + side.sign * self._slip_bps / _BPS) + side.sign * self._slip_ticks

    @staticmethod
    def _status(tif, filled, qty):
        """Return the order status a fill of ``filled`` out of ``qty`` leaves under ``tif``."""
        if filled == qty:
            return _FILLED
        if not tif.rests:
            return _CANCELLED
        return _PARTIAL if filled > _ZERO else _OPEN

    def _book_fill(self, ref, venue_ref, instrument, side_name, qty, price, liquidity, now):
        """Record one fill and return its fee."""
        fee = self._fee.charge(qty, price, liquidity)
        self._fills.append(
            Fill(
                fill_id=f"f-{len(self._fills) + 1}",
                venue_ref=venue_ref,
                client_ref=ref,
                instrument=instrument,
                side=side_name,
                qty=qty,
                price=price,
                fee=fee,
                fee_currency=SIMULATED_UNITS["cash"],
                liquidity=liquidity,
                status=_FINAL,
                ts_ms=now,
                native={},
            )
        )
        return fee

    # -- resting orders ---------------------------------------------------

    def _march(self, now):
        """Expire every working order past its deadline and offer the reached ones a fill."""
        for ref, working in list(self._book.items()):
            order = working.order
            if order.status in TERMINAL_STATUSES:
                continue
            deadline = _TIFS[order.tif].deadline(working.expires_ms, self._session_end)
            if deadline is not None and now >= deadline:
                self._rebook(ref, status=_EXPIRED, updated_ms=now)
                continue
            quote = self._quotes.get(order.instrument)
            if quote is None or not self._reached(order, quote):
                continue
            if not self._rng.random() < self._p_touch:
                continue
            available = self._size_cap.cap(order.remaining_qty) * (_ONE - self._queue_frac)
            if available <= _ZERO or (available < order.remaining_qty and not self._partial):
                continue
            self._fill_working(ref, order, available, quote, now)

    def _reached(self, order, quote):
        """Say whether the market has come to a resting order's price."""
        if order.limit is None:
            return True
        side = _SIDES[order.side]
        return self._resting_rule.reached(side.sign * (order.limit - side.far_of(quote)))

    def _fill_working(self, ref, order, qty, quote, now):
        """Fill ``qty`` of a resting order as the passive side, at its own limit."""
        passive = order.limit is not None
        price = order.limit if passive else _SIDES[order.side].far_of(quote)
        fee = self._book_fill(
            ref, order.venue_ref, order.instrument, order.side, qty, price, _MAKER if passive else _TAKER, now
        )
        filled = order.filled_qty + qty
        paid = (_ZERO if order.avg_price is None else order.avg_price * order.filled_qty) + price * qty
        self._rebook(
            ref,
            status=_FILLED if filled == order.qty else _PARTIAL,
            filled_qty=filled,
            remaining_qty=order.qty - filled,
            avg_price=paid / filled,
            fee=order.fee + fee,
            updated_ms=now,
        )

    def _rebook(self, ref, **changes):
        """Replace one booked order's fields."""
        working = self._book[ref]
        self._book[ref] = dataclasses.replace(working, order=dataclasses.replace(working.order, **changes))

    # -- read and cancel --------------------------------------------------

    def cancel(self, ref):
        """Cancel one order; a terminal order absorbs the cancel and answers its state.

        Parameters
        ----------
        ref : str

        Returns
        -------
        Ack
            Stamped ``now + latency_ms.cancel``; ``rejected``/``unknown_ref``
            for a reference this venue never booked.
        """
        now = self._clock.now_ms() + self._latency["cancel"]
        working = self._book.get(ref)
        if working is None:
            return empty_ack(ref, now, _REJECTED, "unknown_ref")
        if working.order.status not in TERMINAL_STATUSES:
            self._rebook(ref, status=_CANCELLED, updated_ms=now)
        return dataclasses.replace(_ack_of(self._book[ref].order), ts_ms=now)

    def order(self, ref):
        """Return the booked order for ``ref``, or None.

        Parameters
        ----------
        ref : str

        Returns
        -------
        OrderState or None
        """
        working = self._book.get(ref)
        return None if working is None else working.order

    def open_orders(self):
        """Return every non-terminal booked order, oldest first.

        Returns
        -------
        tuple of OrderState
        """
        return tuple(w.order for w in self._book.values() if w.order.status not in TERMINAL_STATUSES)

    def fills(self, since_ms, cursor=None):
        """Return every fill at or after ``since_ms`` in one page.

        Parameters
        ----------
        since_ms : int
        cursor : object, optional
            Ignored: the page is exhaustive, so the next cursor is None.

        Returns
        -------
        tuple
            ``(fills, None)``.
        """
        return (tuple(fill for fill in self._fills if fill.ts_ms >= since_ms), None)

    def balances(self):
        """Return no balances: a paper venue keeps no cash of its own — the fold does.

        Returns
        -------
        tuple
        """
        return ()


# ---------------------------------------------------------------------------
# RecordedExecutor — replay parity (D20)
# ---------------------------------------------------------------------------

#: The verbs a recording answers; ``events`` stays the base's (no stream in
#: phase 1) and the two classmethods are class facts, not session answers.
_REPLAYED = pin_members(
    "executor.py's replayed verbs",
    (
        "balances",
        "cancel",
        "cancel_all",
        "capabilities",
        "check",
        "execution_scope",
        "fills",
        "open_orders",
        "order",
        "positions",
        "settlements",
        "submit",
        "venue_time_ms",
    ),
    tuple(name for name in dir(SubmittingExecutor) if not name.startswith("_")),
)


def _checked_tape(tape):
    """Return ``tape`` as ``(method, args, answer)`` triples, refusing a broken recording."""
    if isinstance(tape, (str, bytes)) or not isinstance(tape, (list, tuple)):
        raise ProductionError(
            [f"a tape is a sequence of (method, args, answer) triples, got {type(tape).__name__}"]
        )
    problems, entries = [], []
    for position, entry in enumerate(tape):
        where = f"tape[{position}]"
        if isinstance(entry, (str, bytes)) or not isinstance(entry, (list, tuple)) or len(entry) != 3:
            problems.append(f"{where}: expected a (method, args, answer) triple, got {entry!r}")
            continue
        method, args, answer = entry
        if method not in _REPLAYED:
            problems.append(f"{where}: {method!r} is not one of {list(_REPLAYED)}")
            continue
        if isinstance(args, (str, bytes)) or not isinstance(args, (list, tuple)):
            problems.append(f"{where}: args must be a sequence, got {args!r}")
            continue
        entries.append((method, tuple(args), answer))
    if problems:
        raise ProductionError(problems)
    return tuple(entries)


class RecordedExecutor(SubmittingExecutor):
    """The replay venue: answers what a recorded session answered, and nothing else (D20).

    Each verb looks up ``(method, args)`` on the tape and returns the
    recorded answer; a submit is keyed by its ``client_ref`` (derived from
    release, tick and leg, so the same replayed leg asks the same
    question). Reads are facts of the session and may be asked more than
    once; a call the recording never made is a divergence and refuses.

    Parameters
    ----------
    params : dict, optional
        No knobs; ``notes`` is allowed.
    clock : Clock
    tape : sequence of (str, sequence, object)
        ``(method, args, answer)`` triples; ``method`` is one of the
        replayed verbs and ``args`` compares positionally.

    Raises
    ------
    ProductionError
        At construction, every malformed entry at once.

    Examples
    --------
    ::

        venue = RecordedExecutor({}, clock=TestClock(), tape=(
            ("execution_scope", (), ExecutionScope(venue="paper", account="strategy-a")),
            ("submit", ("ref-1",), ack),
        ))
        venue.submit(intent, SimulatedPermit(...), state) is ack  # True
        venue.order("ref-1")
        # -> ProductionError: replay asked order('ref-1',) but the recording never did
    """

    def __init__(self, params=None, *, clock, tape):
        super().__init__(params, clock=clock)
        self._tape = _checked_tape(tape)

    def _replay(self, method, args):
        """Return the recorded answer for ``(method, args)``, refusing an unrecorded call."""
        args = tuple(args)
        for recorded_method, recorded_args, answer in self._tape:
            if (recorded_method, recorded_args) == (method, args):
                return answer
        raise ProductionError([f"replay asked {method}{args!r} but the recording never did"])

    def capabilities(self):
        """Return the recorded capabilities.

        Returns
        -------
        Capabilities
        """
        return self._replay("capabilities", ())

    def check(self, config):
        """Return the recorded problems for ``config``.

        Parameters
        ----------
        config : mapping

        Returns
        -------
        tuple of str
        """
        return self._replay("check", (config,))

    def execution_scope(self):
        """Return the recorded scope.

        Returns
        -------
        ExecutionScope
        """
        return self._replay("execution_scope", ())

    def cancel(self, ref):
        """Return the recorded cancel answer for ``ref``.

        Parameters
        ----------
        ref : str

        Returns
        -------
        Ack
        """
        return self._replay("cancel", (ref,))

    def order(self, ref):
        """Return the recorded order state for ``ref``.

        Parameters
        ----------
        ref : str

        Returns
        -------
        OrderState or None
        """
        return self._replay("order", (ref,))

    def open_orders(self):
        """Return the recorded working orders.

        Returns
        -------
        tuple of OrderState
        """
        return self._replay("open_orders", ())

    def fills(self, since_ms, cursor=None):
        """Return the recorded page for ``(since_ms, cursor)``.

        Parameters
        ----------
        since_ms : int
        cursor : object, optional

        Returns
        -------
        tuple
            ``(page, next_cursor)``.
        """
        return self._replay("fills", (since_ms, cursor))

    def balances(self):
        """Return the recorded balances.

        Returns
        -------
        tuple of Balance
        """
        return self._replay("balances", ())

    def positions(self):
        """Return the recorded positions.

        Returns
        -------
        tuple of Position
        """
        return self._replay("positions", ())

    def settlements(self, since_ms):
        """Return the recorded settlements for ``since_ms``.

        Parameters
        ----------
        since_ms : int

        Returns
        -------
        tuple of Settlement
        """
        return self._replay("settlements", (since_ms,))

    def venue_time_ms(self):
        """Return the recorded venue time.

        Returns
        -------
        int or None
        """
        return self._replay("venue_time_ms", ())

    def cancel_all(self):
        """Return the recorded cancel-all answer.

        Returns
        -------
        tuple of Ack
        """
        return self._replay("cancel_all", ())

    def submit(self, intent, permit, state):
        """Return the recorded ``Ack`` for this intent's ``client_ref``.

        Parameters
        ----------
        intent : Intent
        permit : Permit
            Any permit; anything that is not one is ``permit_type``.
        state : TickState
            Ignored.

        Returns
        -------
        Ack

        Raises
        ------
        ProductionError
            If ``intent`` is not an ``Intent``, or the recording never
            submitted this reference.
        """
        ref = _intent_of(intent).client_ref
        if not isinstance(permit, Permit):
            return empty_ack(ref, self._clock.now_ms(), _NOT_SENT, "permit_type")
        return self._replay("submit", (ref,))


# ---------------------------------------------------------------------------
# LiveExecutor — the abstract wrapper core ships (§5.7, D14)
# ---------------------------------------------------------------------------


class LiveExecutor(SubmittingExecutor):
    """The act gate's holder: refuses by type, delegates the verify/call to the gate.

    Construction always succeeds for read/query/cancel — a child's venue
    subclass answers those itself — but refuses a lease that is not
    ``LIVE_CAPABLE`` and a ``capabilities().fencing`` other than
    ``submit_token`` (§5.7.2), because without both a fenced-out process's
    orders would still reach the venue. ``submit`` refuses any permit that
    is not an ``ActPermit`` BY TYPE, returning ``Ack(not_sent,
    reason="permit_type")``, and otherwise hands ``(intent, permit, state,
    self._submit_native)`` to ``SubmissionVerifier.verify_and_call`` so the
    checks and the send cannot be separated by a caller-visible gap. A gate
    that raises is answered ``unknown`` — the request may already have left
    — except a ``ProductionError``, which is a wiring defect and propagates.

    Parameters
    ----------
    params : dict, optional
        The child's knobs, default-deny over its ``_PARAMS``.
    clock : Clock
    verifier : SubmissionVerifier
        The final gate; ``compose`` hands the same object to ``Safety``.
    lease : Lease
        A child lease with ``LIVE_CAPABLE = True``.

    Raises
    ------
    ProductionError
        On a lease that is not live-capable or a fence other than
        ``submit_token``.

    Examples
    --------
    A child's venue is the only constructible kind::

        class Venue(LiveExecutor):
            _PARAMS = ("api_key_env",)
            _SECRETS = ("api_key_env",)

            def _submit_native(self, intent, permit, timeout_ms):
                return self.gateway.send(intent, permit, timeout_ms)

            ...  # the eight read/query/cancel hooks

        venue = Venue({"api_key_env": "VENUE_KEY"}, clock=clock, verifier=verifier, lease=lease)
        venue.submit(intent, SimulatedPermit(...), state).reason  # 'permit_type'
    """

    def __init__(self, params=None, *, clock, verifier, lease):
        super().__init__(params, clock=clock)
        problems = []
        if not getattr(lease, "LIVE_CAPABLE", False):
            problems.append("a live executor needs a lease whose class declares LIVE_CAPABLE = True (§5.7.2)")
        fencing = self.capabilities().fencing
        if fencing != LIVE_FENCING:
            problems.append(f"a live executor must declare fencing {LIVE_FENCING!r}, got {fencing!r} (§5.7.2)")
        if problems:
            raise ProductionError(problems)
        self._verifier = verifier
        self._lease = lease

    @abstractmethod
    def _submit_native(self, intent, permit, timeout_ms):
        """Send one order through the child gateway within ``timeout_ms``; return its ``Ack``."""

    def submit(self, intent, permit, state):
        """Refuse a non-``ActPermit`` by type, else run the gate's verify-and-call.

        Parameters
        ----------
        intent : Intent
        permit : Permit
            Only an ``ActPermit`` proceeds; anything else is ``permit_type``.
        state : TickState
            The leg's step-(2) state, handed to the gate unchanged.

        Returns
        -------
        Ack
            The gate's answer verbatim; ``unknown`` if the gate raised.

        Raises
        ------
        ProductionError
            If ``intent`` is not an ``Intent``, or the gate reports a
            wiring defect.
        """
        ref = _intent_of(intent).client_ref
        if not isinstance(permit, ActPermit):
            return empty_ack(ref, self._clock.now_ms(), _NOT_SENT, "permit_type")
        try:
            return self._verifier.verify_and_call(intent, permit, state, self._submit_native)
        except ProductionError:
            raise
        except Exception as exc:
            _LOG.error("the act gate raised for %s: %r", ref, exc)
            return empty_ack(ref, self._clock.now_ms(), _UNKNOWN, type(exc).__name__)

    def reset_after_reconcile(self):
        """Re-enable sends after reconciliation resolved the ambiguous reference.

        Returns
        -------
        None
            Delegated to the gate, which is the disable's one owner.
        """
        self._verifier.reset_after_reconcile()


EXECUTOR_KINDS = Registry("executor", Executor)
EXECUTOR_KINDS.register("paper", PaperExecutor)
EXECUTOR_KINDS.register("recorded", RecordedExecutor)
EXECUTOR_KINDS.register("shadow", ShadowExecutor)


# ---------------------------------------------------------------------------
# The conformance battery (§5.7)
# ---------------------------------------------------------------------------

#: A bound on paging, so a venue whose cursor never ends cannot hang the suite.
_MAX_FILL_PAGES = 10_000


def _moved(digest):
    """Return ``digest`` with its first character changed."""
    return ("1" if digest[:1] == "0" else "0") + digest[1:]


def _boom(*args, **kwargs):
    """Stand in for a door that must stay shut: being called is the defect."""
    raise AssertionError(f"forbidden call: {args!r} {kwargs!r}")


def executor_conformance_suite(
    cls, params, quotes, *, build=None, orders=None, state=None, name="TestExecutorConformance"
):
    """Build the pytest class that holds one executor to §5.7's closed battery.

    Parameters
    ----------
    cls : type
        The ``SubmittingExecutor`` subclass under test.
    params : dict
        Its ``execution.params`` block.
    quotes : sequence of Quote
        The market a paper-like venue is fed through ``on_quote`` before
        every check; the injected clock starts at the first quote's
        ``asof_ms``.
    build : callable, optional
        ``build(clock) -> executor`` for a class whose constructor needs
        more than ``(params, clock=)`` — a recorded tape, a live venue's
        verifier and lease. Default ``cls(params, clock=clock)``.
    orders : sequence of (Intent, Permit), optional
        The submissions the battery makes; a live venue's pairs carry
        ``ActPermit``s consistent with its gate.
    state : TickState, optional
        What ``submit`` receives as its third argument; a live venue's gate
        needs the leg's state, simulated venues ignore it.
    name : str
        The class name, so a report says which venue failed.

    Returns
    -------
    type
        A pytest test class with the nineteen battery checks.
    """
    import pytest

    from dskit.production.clock import TestClock
    from dskit.production.state import PositionBook

    orders = tuple(orders or ())
    quotes = tuple(quotes)
    start_ms = quotes[0].asof_ms if quotes else 0
    live = issubclass(cls, LiveExecutor)

    def make():
        """Build one venue over a fresh clock and feed it the market."""
        clock = TestClock(start_ms=start_ms)
        venue = build(clock) if build is not None else cls(dict(params), clock=clock)
        feed(venue)
        return venue, clock

    def feed(venue):
        """Hand every quote to a venue that takes them."""
        if hasattr(venue, "on_quote"):
            for quote in quotes:
                venue.on_quote(quote)

    def submit_all(venue):
        """Submit every order and return the acks."""
        return [venue.submit(intent, permit, state) for intent, permit in orders]

    def all_fills(venue):
        """Page fills to exhaustion."""
        seen, cursor = [], None
        for _page in range(_MAX_FILL_PAGES):
            page, cursor = venue.fills(0, cursor)
            seen.extend(page)
            if cursor is None:
                return tuple(seen)
        raise AssertionError(f"fills paged {_MAX_FILL_PAGES} times without exhausting the cursor")

    def filled_qty(venue, ref, ack):
        """Return the venue's filled quantity for ``ref``, or the ack's when it holds no order."""
        order = venue.order(ref)
        return ack.filled_qty if order is None else order.filled_qty

    def record_sends(venue):
        """Replace a live venue's native send with a recorder; return the record."""
        sent = []
        if live:

            def native(intent, permit, timeout_ms):
                sent.append((intent, permit, timeout_ms))
                return empty_ack(intent.client_ref, timeout_ms, _OPEN, "")

            venue._submit_native = native
        return sent

    def require_orders():
        """Skip a check that needs submissions when the caller gave none."""
        if not orders:
            pytest.skip("no orders given")

    def require_live(why):
        """Skip a check only a live venue can answer."""
        if not live:
            pytest.skip(why)

    class Suite:
        """§5.7's closed conformance battery, bound to one executor class."""

        def test_default_deny_spec(self):
            """Refuse an unknown knob; the spec and the default-deny list are one fact."""
            assert cls.validate_params({"conformance_unknown_knob": 1})
            spec = cls.spec()
            assert set(spec["params"]) == set(cls._PARAMS)
            assert set(spec["secrets"]) <= set(spec["params"])

        def test_check_performs_no_submit(self):
            """Keep ``check`` a read: no order, no fill and no send comes of it."""
            venue, _clock = make()
            sent = record_sends(venue)
            before = (venue.open_orders(), all_fills(venue))
            assert isinstance(venue.check({"params": dict(params)}), tuple)
            assert (venue.open_orders(), all_fills(venue)) == before
            assert sent == []

        def test_client_ref_is_echoed(self):
            """Answer every submit under the intent's own ``client_ref``."""
            venue, _clock = make()
            for ack, (intent, _permit) in zip(submit_all(venue), orders):
                assert ack.client_ref == intent.client_ref

        def test_the_same_client_ref_twice_is_idempotent_or_rejected(self):
            """Give a re-used reference the same venue_ref or ``rejected``/``duplicate_ref``."""
            venue, _clock = make()
            first, second = submit_all(venue), submit_all(venue)
            for a, b in zip(first, second):
                assert b.venue_ref == a.venue_ref or (b.status, b.reason) == (_REJECTED, "duplicate_ref")

        def test_terminal_states_absorb(self):
            """Keep a terminal order terminal: a second cancel changes nothing."""
            venue, _clock = make()
            submit_all(venue)
            for intent, _permit in orders:
                first, second = venue.cancel(intent.client_ref), venue.cancel(intent.client_ref)
                assert first.status in STATUSES and second.status in STATUSES
                if first.status in TERMINAL_STATUSES:
                    assert second.status == first.status
                    later = venue.order(intent.client_ref)
                    assert later is None or later.status in TERMINAL_STATUSES

        def test_filled_qty_is_monotone_except_when_reversed(self):
            """Never lower a reported ``filled_qty`` unless a fill was reversed."""
            venue, _clock = make()
            acks = submit_all(venue)
            before = {i.client_ref: filled_qty(venue, i.client_ref, a) for (i, _p), a in zip(orders, acks)}
            feed(venue)
            reversed_refs = {fill.client_ref for fill in all_fills(venue) if fill.status == _REVERSED}
            for (intent, _permit), ack in zip(orders, acks):
                ref = intent.client_ref
                if ref not in reversed_refs:
                    assert filled_qty(venue, ref, ack) >= before[ref], ref

        def test_filled_plus_remaining_equals_qty(self):
            """Report every order with ``filled_qty + remaining_qty == qty``."""
            venue, _clock = make()
            submit_all(venue)
            for intent, _permit in orders:
                order = venue.order(intent.client_ref)
                if order is not None:
                    assert isinstance(order, OrderState)
                    assert order.filled_qty + order.remaining_qty == order.qty

        def test_capability_gating_precedes_any_io(self):
            """Refuse an undeclared TIF locally: nothing reaches the book or the venue."""
            venue, _clock = make()
            missing = [tif for tif in TIFS if tif not in venue.capabilities().tifs]
            if not missing:
                pytest.skip("every TIF is supported; nothing to gate")
            require_orders()
            intent, permit = orders[0]
            before = (venue.open_orders(), all_fills(venue))
            sent = record_sends(venue)
            gated = dataclasses.replace(intent, proposal=dataclasses.replace(intent.proposal, tif=missing[0]))
            ack = venue.submit(gated, permit, state)
            assert ack.status in TERMINAL_STATUSES
            assert (venue.open_orders(), all_fills(venue)) == before
            assert sent == []

        def test_no_duplicate_fill_id(self):
            """Give every fill its own ``fill_id`` across every page."""
            venue, _clock = make()
            submit_all(venue)
            fills = all_fills(venue)
            assert all(isinstance(fill, Fill) for fill in fills)
            ids = [fill.fill_id for fill in fills]
            assert len(ids) == len(set(ids))

        def test_units_are_declared(self):
            """Declare non-empty ``qty``, ``price`` and ``cash`` units."""
            venue, _clock = make()
            units = venue.capabilities().units
            assert set(units) == set(_UNIT_NAMES)
            assert all(isinstance(value, str) and value for value in units.values())

        def test_derived_and_venue_positions_agree(self):
            """Report positions only when the venue owns them, and then agree with the fold."""
            venue, _clock = make()
            submit_all(venue)
            if venue.capabilities().positions != _VENUE:
                assert venue.positions() == ()
                return
            book = PositionBook()
            for fill in all_fills(venue):
                book.apply(fill)
            assert {p.instrument: p.qty for p in venue.positions()} == {
                p.instrument: p.qty for p in book.positions()
            }

        def test_unarmed_or_raw_authority_is_refused(self):
            """Refuse a raw authority — and, live, a simulated permit — as ``permit_type``."""
            venue, _clock = make()
            require_orders()
            intent, permit = orders[0]
            raw = venue.submit(intent, "raw-ordinary-authority", state)
            assert (raw.status, raw.reason) == (_NOT_SENT, "permit_type")
            if live:
                sent = record_sends(venue)
                simulated = SimulatedPermit(
                    plan_id=permit.plan_id,
                    decision_plan_digest=permit.decision_plan_digest,
                    client_ref=permit.client_ref,
                    valid_until_ms=permit.valid_until_ms,
                )
                ack = venue.submit(intent, simulated, state)
                assert (ack.status, ack.reason) == (_NOT_SENT, "permit_type")
                assert sent == []

        def test_no_initiated_replace_api(self):
            """Offer no replace verb: a replace is a cancel and a submit that skips the barrier."""
            assert not [n for n in dir(cls) if not n.startswith("_") and "replace" in n]

        def test_shadow_touches_no_socket(self):
            """Open no socket on any verb — a simulated venue cannot reach a venue."""
            if live:
                pytest.skip("a live executor is expected to reach its venue")
            import socket as socket_module

            saved = (socket_module.socket, socket_module.create_connection)
            socket_module.socket = socket_module.create_connection = _boom
            try:
                venue, _clock = make()
                submit_all(venue)
                venue.capabilities()
                venue.execution_scope()
                venue.check({"params": dict(params)})
                venue.open_orders()
                all_fills(venue)
                venue.balances()
                venue.positions()
                venue.settlements(0)
                venue.venue_time_ms()
                venue.cancel_all()
            finally:
                socket_module.socket, socket_module.create_connection = saved

        def test_paper_is_deterministic_under_seed(self):
            """Answer identically from two instances under the same seed."""
            if "seed" not in cls._PARAMS:
                pytest.skip("this executor declares no seed")
            first, second = make()[0], make()[0]
            assert [a.to_obj() for a in submit_all(first)] == [a.to_obj() for a in submit_all(second)]
            feed(first)
            feed(second)
            assert [f.to_obj() for f in all_fills(first)] == [f.to_obj() for f in all_fills(second)]
            for intent, _permit in orders:
                one, two = first.order(intent.client_ref), second.order(intent.client_ref)
                assert (one is None and two is None) or one.to_obj() == two.to_obj()

        def test_stale_bindings_refuse(self):
            """Refuse, without sending, a permit whose bound member moved."""
            require_live("bindings are the gate's; a simulated venue holds none")
            require_orders()
            intent, permit = orders[0]
            moved = (
                ("inputs_digest", _moved(permit.inputs_digest)),
                ("coverage_digest", _moved(permit.coverage_digest)),
                ("quote_digest", _moved(permit.quote_digest)),
                ("evidence_digest", _moved(permit.evidence_digest)),
                ("risk_state_digest", _moved(permit.risk_state_digest)),
                ("intent_digest", _moved(permit.intent_digest)),
                ("readiness_digest", _moved(permit.readiness_digest)),
                ("release_hash", _moved(permit.release_hash)),
                ("fencing_token", permit.fencing_token + 1),
                (
                    "risk_version",
                    dataclasses.replace(
                        permit.risk_version, economic_seq=permit.risk_version.economic_seq + 1
                    ),
                ),
            )
            for field, value in moved:
                venue, _clock = make()
                sent = record_sends(venue)
                ack = venue.submit(intent, dataclasses.replace(permit, **{field: value}), state)
                assert ack.status == _NOT_SENT, field
                assert sent == [], field

        def test_timeout_cannot_exceed_the_permit_and_disables_later_sends(self):
            """Bound the native deadline by the permit and stop sending after a hang."""
            require_live("only a live executor has a deadline to honour")
            require_orders()
            venue, clock = make()
            intent, permit = orders[0]
            seen = []

            def hung(intent_, permit_, timeout_ms):
                seen.append(timeout_ms)
                raise TimeoutError(f"no answer within {timeout_ms} ms")

            venue._submit_native = hung
            first = venue.submit(intent, permit, state)
            assert first.status == _UNKNOWN
            assert len(seen) == 1 and 0 < seen[0] <= permit.valid_until_ms - clock.now_ms()
            second = venue.submit(intent, permit, state)
            assert (second.status, second.reason) == (_NOT_SENT, "disabled")
            assert len(seen) == 1

        def test_two_instances_prove_a_stale_fencing_token_cannot_act(self):
            """Keep a fenced-out instance's order inside the process."""
            venue, _clock = make()
            if venue.capabilities().fencing != LIVE_FENCING:
                pytest.skip("this venue declares no fence")
            require_orders()
            intent, permit = orders[0]
            holder, fenced_out = venue, make()[0]
            holder.submit(intent, permit, state)
            sent = record_sends(fenced_out)
            stale = fenced_out.submit(
                intent, dataclasses.replace(permit, fencing_token=permit.fencing_token - 1), state
            )
            assert stale.status in (_NOT_SENT, _REJECTED)
            assert sent == []

        def test_submit_never_raises_for_a_permission_fact(self):
            """Answer a missing permit with an ``Ack``, never an exception."""
            venue, _clock = make()
            require_orders()
            intent, _permit = orders[0]
            assert isinstance(venue.submit(intent, None, state), Ack)

    Suite.__name__ = Suite.__qualname__ = name
    return Suite
