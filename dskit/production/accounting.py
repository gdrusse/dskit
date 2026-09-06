"""The accounting seam: what the account IS, apart from what the executor can DO (§5.7.1, D14).

Accounting is the economic authority behind every guard, every
``DecisionPlan.risk_effect`` and every ``ActPermit``'s risk binding, and
D14 keeps it a separate seam from execution. :class:`Accounting` has three
abstract hooks — ``value`` (the marked portfolio value that becomes
``tick.nav``), ``classify`` (the proven risk effect of one proposal) and
``snapshot`` (the ``AccountState`` a permit binds) — and one concrete
discipline: ``source_tokens`` reports a live session's monotonic
executor/accounting tokens (the base reports none) and ``bind_tokens``
enforces, in the BASE, that a token never disappears, never goes
backwards, and is never reused while the account's economics changed.
A child supplies only the tokens; the rule has one owner.

Two things the module deliberately does NOT do. It never re-derives
positions, working orders or balances — §5.8.1 gives those one owner, the
fold, and a snapshot mirrors ``StateView``. And it never scans the ledger:
history reaches it through an injected ``history`` collaborator with
``fills(since_ms)`` / ``cash_flows(since_ms)`` / ``marks(since_ms)`` (the
§6 record bodies, ``cash_flow`` and ``outcome`` bodies carrying their
envelope ``id`` so ``supersedes`` can resolve), which ``reconcile.py``'s
``LedgerHistory`` supplies over the chain.

Ruling R8 shapes ``snapshot``: every requirement is RE-ANCHORED at the
snapshot's own ``at_ms`` — ``GuardChain.requirements`` builds the union at
the tick instant, a leg's refresh snapshots later, and a measure rebuilds
its digest from ``state.account.asof_ms`` — so evidence is keyed by the
re-anchored digest and ``AccountState.asof_ms == at_ms``. A calendar
requirement carries only its resolved bounds (§5.4), so the calendar kind
is recovered by asking which ``CALENDAR_WINDOWS`` member reproduces those
bounds at the window's start.

Core ships :class:`PaperAccounting` (deterministic, clockless in effect —
every instant is ``at_ms``) and :class:`RecordedAccounting` (D20 replay of
a tape; a diverging or exhausted call refuses). Live requires a child.
"""

import dataclasses
from abc import ABC, abstractmethod
from decimal import Decimal
from fractions import Fraction

from dskit.pipeline.node import check_int_param
from dskit.production.base import (
    ProductionError,
    Registry,
    _check_str,
    canonical_hash,
    reject_unknown_params,
)
from dskit.production.guards import (
    AGGREGATE_SCOPE_KEY,
    ConsecutiveLosses,
    Drawdown,
    ErrorVsRealised,
    Pnl,
    Window,
)
from dskit.production.records import (
    AccountState,
    Balance,
    EvidenceRequirement,
    Fill,
    MeasureEvidence,
    Proposal,
    RiskVersion,
)
from dskit.production.vocab import (
    CALENDAR_WINDOWS,
    FILL_STATUSES,
    OUTCOME_KINDS,
    RISK_EFFECTS,
    SIDES,
    WINDOW_KINDS,
)

__all__ = [
    "ACCOUNTING_KINDS",
    "Accounting",
    "PaperAccounting",
    "RecordedAccounting",
    "WindowBook",
    "decimal_of",
    "effective_bodies",
    "effective_fills",
]

_NOTES = ("notes",)
_ZERO = Decimal(0)

#: The three risk effects, pinned to the vocabulary at import.
_INCREASE, _NEUTRAL, _REDUCE = "increase", "neutral", "reduce"
if {_INCREASE, _NEUTRAL, _REDUCE} != set(RISK_EFFECTS):
    raise ProductionError(["accounting.py: the risk effects do not match vocab.RISK_EFFECTS"])

#: A side's sign on quantity — a table over ``SIDES``, never a side branch.
_SIGNS = {"buy": 1, "sell": -1, "none": 0}
if set(_SIGNS) != set(SIDES):
    raise ProductionError(["accounting.py: the side signs do not cover vocab.SIDES"])

#: Whether a fill of this status is applied; a ``reversed`` one undoes its id.
_APPLIES = {"pending": True, "final": True, "reversed": False}
if set(_APPLIES) != set(FILL_STATUSES):
    raise ProductionError(["accounting.py: the fill rules do not cover vocab.FILL_STATUSES"])

#: Whether an outcome of this kind carries a realised value a measure may read.
_REALISED = {"settled": True, "marked": True, "voided": False, "partial": True, "corrected": True}
if set(_REALISED) != set(OUTCOME_KINDS):
    raise ProductionError(["accounting.py: the outcome rules do not cover vocab.OUTCOME_KINDS"])

#: The three sources a snapshot folds and digests.
_FILLS, _CASH_FLOWS, _MARKS = "fills", "cash_flows", "marks"
#: The bitemporal keys every ``cash_flow`` and ``outcome`` body carries.
_ID, _EFFECTIVE, _KNOWN, _SUPERSEDES = "id", "effective_at_ms", "known_at_ms", "supersedes"


def decimal_of(exact):
    """Render an exact ``Fraction`` as a ``Decimal``, once, at the boundary.

    The fold works in ``Fraction`` so an averaged cost cannot drift; every
    number that LEAVES it — an evidence value, a §5.13.3 value point —
    crosses here, so the rounding happens in one place rather than once
    per caller.

    Parameters
    ----------
    exact : fractions.Fraction
        The folded value.

    Returns
    -------
    Decimal
        Under the ambient decimal context.
    """
    return Decimal(exact.numerator) / Decimal(exact.denominator)


def _check_instant(problems, name, value):
    """Append a problem unless ``value`` is an epoch-ms int (never a bool)."""
    if isinstance(value, bool) or not isinstance(value, int):
        problems.append(f"{name} must be an epoch-ms int, got {value!r}")


def _instant(value, name):
    """Return ``value`` if it is an epoch-ms int, else refuse."""
    problems = []
    _check_instant(problems, name, value)
    if problems:
        raise ProductionError(problems)
    return value


def _fill_of(reported):
    """Return a history fill as a ``Fill``: the record itself, or its ``to_obj`` body rebuilt.

    The one owner of that reading — ``reconcile.LedgerHistory.fills`` yields
    ``Fill`` records, a recorded tape or a test yields bodies; both fold alike.
    """
    return Fill.from_obj(reported) if isinstance(reported, dict) else reported


def _frozen(value):
    """Return ``value`` with every list or tuple, however nested, as a tuple."""
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    return value


def _answer_value(answer, where):
    """Refuse a recorded ``value`` answer that is not a Decimal or None."""
    if answer is not None and not isinstance(answer, Decimal):
        raise ProductionError([f"{where}: a recorded nav is a Decimal or None, got {answer!r}"])


def _answer_classify(answer, where):
    """Refuse a recorded ``classify`` answer outside ``RISK_EFFECTS``."""
    if answer not in RISK_EFFECTS:
        raise ProductionError([f"{where}: a recorded risk effect must be one of {list(RISK_EFFECTS)}"])


def _answer_snapshot(answer, where):
    """Refuse a recorded ``snapshot`` answer that is not an ``AccountState``."""
    if not isinstance(answer, AccountState):
        raise ProductionError([f"{where}: a recorded snapshot is an AccountState, got {answer!r}"])


class Accounting(ABC):
    """The seam: value, classify, snapshot — plus the base-owned token discipline (§5.7.1).

    Constructed as ``cls(params)`` from the document's ``accounting``
    site: default-deny over the subclass's ``_PARAMS`` plus ``notes``.
    The three hooks are abstract, so an incomplete child refuses to
    construct (§5.15). ``source_tokens`` is concrete and answers
    ``(None, None)`` — a simulated session has no session tokens; a live
    child overrides it, and :meth:`bind_tokens` enforces the monotonic
    discipline over whatever it answers.

    Parameters
    ----------
    params : dict, optional
        The ``{uses, params}`` site's ``params``; ``None`` means ``{}``.

    Raises
    ------
    ProductionError
        On a key outside ``_PARAMS`` and ``notes``.

    Examples
    --------
    The smallest complete strategy: a flat, unmarked account::

        class Flat(Accounting):
            def value(self, state_view, quotes, at_ms):
                return Decimal(0)

            def classify(self, proposal, state):
                return "increase"

            def snapshot(self, state_view, executor, quotes, at_ms, requirements, calendar):
                raise ProductionError(["Flat snapshots nothing"])

        flat = Flat({})
        flat.source_tokens(executor=None, at_ms=0)  # (None, None)
        flat.value(view, quotes, 0)  # Decimal('0')
    """

    #: The knobs a subclass accepts; ``notes`` is always allowed beside them.
    _PARAMS = ()

    def __init__(self, params=None):
        params = dict(params or {})
        problems = self.validate_params(params)
        if problems:
            raise ProductionError(problems)
        self._last_tokens = None

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
        reject_unknown_params(problems, params, cls._PARAMS + _NOTES)
        return problems

    @abstractmethod
    def value(self, state_view, quotes, at_ms):
        """Return the marked portfolio value that becomes ``tick.nav``.

        Parameters
        ----------
        state_view : StateView
            The fold at head — its balances and positions.
        quotes : QuoteSet
            This tick's quotes.
        at_ms : int
            The instant the marks are judged fresh against.

        Returns
        -------
        Decimal or None
            None when a required mark is missing or stale, or balances
            span currencies — recorded rather than guessed.
        """

    @abstractmethod
    def classify(self, proposal, state):
        """Return the proven risk effect of ``proposal`` against ``state.account``.

        Parameters
        ----------
        proposal : Proposal
        state : TickState

        Returns
        -------
        str
            Exactly one ``RISK_EFFECTS`` member.

        Raises
        ------
        ProductionError
            If the proposal has a side but no size (R10).
        """

    @abstractmethod
    def snapshot(self, state_view, executor, quotes, at_ms, requirements, calendar):
        """Return the ``AccountState`` a decision binds, as of ``at_ms``.

        Parameters
        ----------
        state_view : StateView
            The fold: positions, working orders and balances are its.
        executor : Executor
            The venue link a live child asks for its session tokens; a
            simulated strategy never touches it.
        quotes : QuoteSet
        at_ms : int
        requirements : tuple of EvidenceRequirement
            The deduplicated union ``GuardChain.requirements`` built.
        calendar : Calendar
            Resolves calendar windows when requirements are re-anchored.

        Returns
        -------
        AccountState
            ``asof_ms == at_ms``; one fresh ``MeasureEvidence`` per
            re-anchored requirement × scope key.

        Raises
        ------
        ProductionError
            An unsupported requirement, a stale or missing mark for a
            held instrument, or a token discipline breach.
        """

    def source_tokens(self, executor, at_ms):
        """Return the session's ``(executor_token, accounting_tokens)``.

        Parameters
        ----------
        executor : Executor
        at_ms : int

        Returns
        -------
        tuple
            ``(str or None, tuple of str or None)`` — the base answers
            ``(None, None)``: a simulated session has no tokens.
        """
        return (None, None)

    def bind_tokens(self, executor, at_ms, economic_seq, risk_digest):
        """Return the ``RiskVersion`` a snapshot binds, enforcing the token discipline.

        Absence, regression or reuse with changed economics refuses
        (§5.7.1): once a source has reported a token it must keep
        reporting one; a token compares monotonically (string order for
        the executor's, tuple order for accounting's); and the same pair
        may be reported twice only while ``risk_digest`` is unchanged.

        Parameters
        ----------
        executor : Executor
            Handed to :meth:`source_tokens`.
        at_ms : int
        economic_seq : int
            The fold's economic sequence.
        risk_digest : str
            ``AccountState.risk_digest()`` of the snapshot being bound.

        Returns
        -------
        RiskVersion

        Raises
        ------
        ProductionError
            On a malformed answer from ``source_tokens`` or a breach.
        """
        executor_token, accounting_tokens = self._checked_tokens(self.source_tokens(executor, at_ms))
        problems = []
        if self._last_tokens is not None:
            last_executor, last_accounting, last_digest = self._last_tokens
            self._check_monotone(problems, "executor_token", last_executor, executor_token)
            self._check_monotone(problems, "accounting_tokens", last_accounting, accounting_tokens)
            reported = executor_token is not None or accounting_tokens is not None
            if (
                reported
                and (executor_token, accounting_tokens) == (last_executor, last_accounting)
                and risk_digest != last_digest
            ):
                problems.append(
                    "source tokens were reused while the account's economics changed: a "
                    "version that did not move cannot vouch for an account that did"
                )
        if problems:
            raise ProductionError(problems)
        self._last_tokens = (executor_token, accounting_tokens, risk_digest)
        return RiskVersion(
            economic_seq=economic_seq,
            executor_token=executor_token,
            accounting_tokens=accounting_tokens,
        )

    @staticmethod
    def _checked_tokens(reported):
        """Return ``(executor_token, accounting_tokens)`` in canonical form, refusing a bad shape."""
        problems = []
        if isinstance(reported, (str, bytes)) or not isinstance(reported, (list, tuple)) or len(reported) != 2:
            raise ProductionError(
                [f"source_tokens must answer (executor_token, accounting_tokens), got {reported!r}"]
            )
        executor_token, accounting_tokens = reported
        if executor_token is not None:
            _check_str(problems, "executor_token", executor_token)
        if accounting_tokens is not None:
            if isinstance(accounting_tokens, (str, bytes)) or not isinstance(accounting_tokens, (list, tuple)):
                problems.append(f"accounting_tokens must be a sequence of str, got {accounting_tokens!r}")
            else:
                for position, token in enumerate(accounting_tokens):
                    _check_str(problems, f"accounting_tokens[{position}]", token)
                accounting_tokens = tuple(accounting_tokens)
        if problems:
            raise ProductionError(problems)
        return executor_token, accounting_tokens

    @staticmethod
    def _check_monotone(problems, name, last, current):
        """Append a problem when a once-reported token vanishes or goes backwards."""
        if last is None:
            return
        if current is None:
            problems.append(f"{name} disappeared: the source reported {last!r} and now reports none")
        elif current < last:
            problems.append(f"{name} went backwards: {current!r} after {last!r}")


# ---------------------------------------------------------------------------
# PaperAccounting — the deterministic fold (§5.7.1, §6, R8)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Folded:
    """What one snapshot folded: the effective, as-of records per source and their digests."""

    fills: tuple
    cash_flows: tuple
    marks: tuple
    digests: dict


def effective_fills(reported, at_ms):
    """Return the applied, un-reversed fills known by ``at_ms``.

    The ONE reading of "which fills count": a reversal undoes its
    ``fill_id`` however often it is repeated, and a fill stamped after the
    cut had not happened. §5.13.3's attribution and value curve ask the
    same question of the same history, so the rule lives here rather than
    once per caller.

    Parameters
    ----------
    reported : iterable
        ``Fill`` records or their §6 bodies, as ``LedgerHistory.fills``
        answers them.
    at_ms : int
        The cut; a fill with ``ts_ms > at_ms`` is dropped.

    Returns
    -------
    tuple of Fill
        In ``(ts_ms, fill_id)`` order.

    Raises
    ------
    ProductionError
        If an item is neither a ``Fill`` nor a fill body.
    """
    applied, reversed_ids = {}, set()
    for item in reported:
        fill = _fill_of(item)
        if not isinstance(fill, Fill):
            raise ProductionError([f"history.fills yielded {fill!r}, not a Fill or its body"])
        if fill.ts_ms > at_ms:
            continue
        if _APPLIES[fill.status]:
            applied[fill.fill_id] = fill
        else:
            reversed_ids.add(fill.fill_id)
    effective = [fill for fill_id, fill in applied.items() if fill_id not in reversed_ids]
    effective.sort(key=lambda fill: (fill.ts_ms, fill.fill_id))
    return tuple(effective)


def effective_bodies(reported, at_ms, what):
    """Return the bitemporal bodies known by ``at_ms``, minus those superseded.

    D21's reading of a ``cash_flow`` or an ``outcome`` chain: a record is
    counted when it was KNOWN by the cut, and a record another effective
    record supersedes is replaced rather than annotated — so a corrected
    adoption can never double-bank.

    Parameters
    ----------
    reported : iterable of dict
        §6 bodies carrying their envelope ``id``, as
        ``LedgerHistory.cash_flows`` and ``.marks`` answer them.
    at_ms : int
        The ``known_at_ms <= at_ms`` cut.
    what : str
        What to call the source in a refusal.

    Returns
    -------
    tuple of dict
        In ``(effective_at_ms, id)`` order.

    Raises
    ------
    ProductionError
        On a body that is not a mapping, or that is missing its id or
        either instant.
    """
    problems, known = [], []
    for position, body in enumerate(reported):
        where = f"{what}[{position}]"
        if not isinstance(body, dict):
            problems.append(f"{where} must be a record body dict, got {body!r}")
            continue
        _check_str(problems, f"{where}.{_ID}", body.get(_ID))
        _check_instant(problems, f"{where}.{_EFFECTIVE}", body.get(_EFFECTIVE))
        _check_instant(problems, f"{where}.{_KNOWN}", body.get(_KNOWN))
        if body.get(_SUPERSEDES) is not None:
            _check_str(problems, f"{where}.{_SUPERSEDES}", body.get(_SUPERSEDES))
        if not problems and body[_KNOWN] <= at_ms:
            known.append(dict(body))
    if problems:
        raise ProductionError(problems)
    superseded = {body[_SUPERSEDES] for body in known if body.get(_SUPERSEDES) is not None}
    effective = [body for body in known if body[_ID] not in superseded]
    effective.sort(key=lambda body: (body[_EFFECTIVE], body[_ID]))
    return tuple(effective)


class WindowBook:
    """The window's trades folded from a flat baseline (§5.7.1).

    Realised per CLOSING fill against an averaged cost, open quantity
    carried at that cost, and the cumulative path kept so a drawdown can
    be read off it. It is the one owner of "what did the trading come
    to": ``PaperAccounting``'s ``pnl``, ``drawdown`` and
    ``consecutive_losses`` measures fold through it, and §5.13.3's value
    curve reads the same fold rather than a second one — which is what
    makes the curve and the guard that halts on it agree by construction.

    Every number is a ``fractions.Fraction``, so an averaged cost is
    exact and a long window cannot drift.

    Examples
    --------
    A round trip that realised a loss::

        book = WindowBook()
        book.apply(bought_at_50)
        book.apply(sold_at_40)
        book.realised
        # -> Fraction(-1, 1)
    """

    def __init__(self):
        self._open = {}
        self._realised = Fraction(0)
        self._path = []
        self._closes = []

    def apply(self, fill):
        """Fold one fill: average the cost in on an add, realise against it on a close."""
        sign = _SIGNS[fill.side]
        if sign == 0:
            return
        qty, price, fee = Fraction(fill.qty), Fraction(fill.price), Fraction(fill.fee)
        delta = sign * qty
        held, cost = self._open.get(fill.instrument, (Fraction(0), Fraction(0)))
        if held == 0 or (held > 0) == (delta > 0):
            cost = (abs(held) * cost + qty * price) / (abs(held) + qty)
            realised = -fee
        else:
            closing = min(qty, abs(held))
            realised = closing * (price - cost) * (1 if held > 0 else -1) - fee
            after = held + delta
            if after == 0:
                cost = Fraction(0)
            elif (after > 0) != (held > 0):
                cost = price
            self._closes.append(realised)
        held += delta
        if held == 0:
            self._open.pop(fill.instrument, None)
        else:
            self._open[fill.instrument] = (held, cost)
        self._realised += realised
        self._path.append(self._realised)

    def unrealised(self, mark_of):
        """Return the open quantity against its mark: ``Σ qty × (mark − cost)``.

        An instrument ``mark_of`` answers ``None`` for contributes NOTHING,
        which is the conservative reading §5.13.3's value curve needs:
        unmarked open risk is not profit. The measures pass a ``mark_of``
        that refuses instead, because an evidence value computed over a
        position nobody could mark would be a number with a hole in it.

        Parameters
        ----------
        mark_of : callable
            ``mark_of(instrument)`` -> a number, or ``None`` for unmarked.

        Returns
        -------
        fractions.Fraction
            Exact.
        """
        total = Fraction(0)
        for instrument, (held, cost) in self._open.items():
            mark = mark_of(instrument)
            if mark is not None:
                total += held * (Fraction(mark) - cost)
        return total

    def pnl(self, mark_of):
        """Return realised plus the open quantity marked: ``cash + Σ qty × mark``."""
        return self._realised + self.unrealised(mark_of)

    def drawdown(self, mark_of):
        """Return the peak-to-trough decline of the cumulative path, baseline 0 included."""
        peak = worst = Fraction(0)
        for point in [Fraction(0), *self._path, self.pnl(mark_of)]:
            peak = max(peak, point)
            worst = max(worst, peak - point)
        return worst

    def consecutive_losses(self):
        """Return the length of the trailing run of closing fills that realised a loss."""
        run = 0
        for realised in reversed(self._closes):
            if realised >= 0:
                break
            run += 1
        return run

    @property
    def closes(self):
        """How many fills realised an outcome (closed quantity) in this window."""
        return len(self._closes)

    @property
    def realised(self):
        """The window's closed P&L, fees included.

        Returns
        -------
        fractions.Fraction
            Exact; ``pnl`` adds the marked open quantity to it.
        """
        return self._realised


class PaperAccounting(Accounting):
    """The deterministic core strategy: the fold's account, marked and evidenced (§5.7.1).

    ``value`` is cash plus every position marked at its quote's ``mid``;
    ``classify`` proves a reduction against positions AND working orders
    of the proposal's instrument (net signed exposure before and after:
    ``reduce`` iff its magnitude strictly falls without flipping sign,
    ``neutral`` for no size, else ``increase``); ``snapshot`` mirrors the
    fold's positions, working orders and balances and answers the four
    evidence measures from the injected history, as of ``at_ms``.

    **The fold.** Fills count when ``ts_ms <= at_ms``; a ``reversed``
    report undoes its ``fill_id`` however often it is repeated. Cash flows
    and outcomes count when ``known_at_ms <= at_ms`` (D21 bitemporality),
    and a record another effective record ``supersedes`` is replaced, never
    added to. Every window selects its samples by
    ``window_start_ms <= instant <= min(window_end_ms, at_ms)``, a
    ``count`` window keeping the last N; the baseline is the account as
    the window opened, so nothing before the window enters it.

    **The formulas** (a plan gap; these are the standard definitions):

    * ``pnl`` — the window's trades folded from a flat baseline: each fill
      that adds averages its price into the cost; each fill that closes
      realises ``closed_qty × (price − avg_cost) × sign(position)`` and
      every fill pays its fee; the quantity still open at the end is
      marked at its quote (``Σ qty × (mark − avg_cost)``). Identically
      ``trading cash + Σ open_qty × mark``: realised plus marked, and a
      cash flow never enters it (§6's partition).
    * ``drawdown`` — the largest peak-to-trough decline of the cumulative
      trading pnl path within the window, in fill order, starting at the
      baseline 0 and ending at the marked pnl; a non-negative magnitude.
    * ``consecutive_losses`` — the length of the trailing run of closing
      fills whose realised outcome (fee included) is negative; a fill that
      only adds to a position realises nothing and does not break the run.
    * ``error_vs_realised`` — the weight-averaged absolute error
      ``|prediction − value|`` over the window's terminal realised
      outcomes whose ``leg_id`` the fold's decision history knows; an
      outcome for a leg outside that history has no prediction and is
      skipped (it still moves the ``marks`` source digest).

    A position held unchanged across the whole window contributes no
    pnl: the strategy has current quotes but no price history, so the mark
    drift of a carried position is invisible to it — a child with a price
    series subclasses and overrides ``_pnl``. Marks enter through the
    window's trades, and a window whose open quantity has no fresh quote
    refuses rather than guessing.

    **Freshness.** ``value`` leaves ``nav`` None for a quote older than
    ``max_valuation_age_ms`` (inclusive: exactly that old still marks);
    ``snapshot`` REFUSES it for a held instrument, because a snapshot is
    what a permit binds.

    Parameters
    ----------
    params : dict, optional
        No knobs; ``notes`` is allowed. ``max_valuation_age_ms`` is a
        document sibling of ``uses``/``params`` (§4.1) and refuses here.
    clock : Clock
        Injected for the seam's uniform construction; every instant the
        strategy uses is the ``at_ms`` it was asked about, so a replay
        reproduces the snapshot.
    history : object
        ``fills(since_ms)`` (``Fill`` records or their ``to_obj`` dicts),
        ``cash_flows(since_ms)`` and ``marks(since_ms)`` (§6 bodies with
        their envelope ``id``). Asked once per snapshot, from the earliest
        window start.
    max_valuation_age_ms : int
        ``document.accounting.max_valuation_age_ms``; a positive int.

    Raises
    ------
    ProductionError
        On an unknown param or a valuation age that is not a positive int.

    Examples
    --------
    ::

        accounting = PaperAccounting({}, clock=clock, history=history, max_valuation_age_ms=60_000)
        accounting.value(view, quotes, at_ms)  # Decimal('1125') — 1000 cash + 10 marked at 12.50
        accounting.classify(sell_5, tick_state)  # 'reduce' against a long of 10
        account = accounting.snapshot(view, executor, quotes, at_ms, requirements, calendar)
        account.asof_ms == at_ms  # True
    """

    #: Measure kind -> the method that answers it, keyed by the guard
    #: classes' own ``kind`` so the two modules cannot spell a name apart.
    _EVIDENCE = {
        Pnl.kind: "_pnl",
        Drawdown.kind: "_drawdown",
        ConsecutiveLosses.kind: "_consecutive_losses",
        ErrorVsRealised.kind: "_error_vs_realised",
    }
    #: Window kind -> how a requirement is re-anchored at a later instant (R8).
    _REANCHOR = {
        "none": "_windows_plain",
        "duration": "_windows_plain",
        "count": "_windows_plain",
        "calendar": "_windows_calendar",
    }
    #: Window kind -> which of the in-range samples the window keeps.
    _SELECT = {
        "none": "_keep_all",
        "duration": "_keep_all",
        "count": "_keep_last",
        "calendar": "_keep_all",
    }

    def __init__(self, params=None, *, clock, history, max_valuation_age_ms):
        super().__init__(params)
        problems = []
        check_int_param(problems, "max_valuation_age_ms", max_valuation_age_ms, ge=1)
        if isinstance(max_valuation_age_ms, float):
            problems.append(f"max_valuation_age_ms must be an int, got {max_valuation_age_ms!r}")
        if problems:
            raise ProductionError(problems)
        self._clock = clock
        self._history = history
        self._max_age = max_valuation_age_ms

    # -- value ----------------------------------------------------------------------

    def value(self, state_view, quotes, at_ms):
        """Return cash plus every position marked at its fresh ``mid``, or None.

        Parameters
        ----------
        state_view : StateView
        quotes : QuoteSet
        at_ms : int

        Returns
        -------
        Decimal or None
            None when balances span currencies, or a held instrument has
            no quote or one older than ``max_valuation_age_ms``.
        """
        balances = state_view.balances
        if len(balances) > 1:
            return None
        marks = self._quotes_by_instrument(quotes)
        nav = next(iter(balances.values()), _ZERO)
        for position in state_view.positions:
            mid = self._fresh_mid(marks.get(position.instrument), at_ms)
            if mid is None:
                return None
            nav += position.qty * mid
        return nav

    # -- classify -------------------------------------------------------------------

    def classify(self, proposal, state):
        """Return the proven risk effect against ``state.account`` (D10, D12).

        Parameters
        ----------
        proposal : Proposal
        state : TickState

        Returns
        -------
        str
            ``neutral`` for no size; ``reduce`` iff the instrument's net
            signed exposure — positions plus working orders' remaining
            quantity — strictly falls in magnitude without flipping sign;
            else ``increase``.

        Raises
        ------
        ProductionError
            If ``proposal`` is not a ``Proposal`` or has a side but no
            ``qty`` (R10).
        """
        if not isinstance(proposal, Proposal):
            raise ProductionError([f"classify expects a Proposal, got {proposal!r}"])
        sign = _SIGNS[proposal.side]
        if sign == 0:
            return _NEUTRAL
        if proposal.qty is None:
            raise ProductionError(
                [f"proposal {proposal.id!r} has side {proposal.side!r} but declares no qty (R10)"]
            )
        delta = sign * proposal.qty
        if delta == 0:
            return _NEUTRAL
        account = state.account
        before = sum(
            (p.qty for p in account.positions if p.instrument == proposal.instrument), _ZERO
        ) + sum(
            (_SIGNS[o.side] * o.remaining_qty for o in account.working if o.instrument == proposal.instrument),
            _ZERO,
        )
        after = before + delta
        if abs(after) < abs(before) and (after == 0 or (after > 0) == (before > 0)):
            return _REDUCE
        return _INCREASE

    # -- snapshot -------------------------------------------------------------------

    def snapshot(self, state_view, executor, quotes, at_ms, requirements, calendar):
        """Return the account as of ``at_ms`` with one fresh evidence per re-anchored requirement.

        Parameters
        ----------
        state_view : StateView
        executor : Executor
            Never touched by the paper strategy beyond ``source_tokens``.
        quotes : QuoteSet
        at_ms : int
        requirements : tuple of EvidenceRequirement
        calendar : Calendar

        Returns
        -------
        AccountState

        Raises
        ------
        ProductionError
            A requirement that is not an ``EvidenceRequirement`` or names
            a measure this strategy cannot answer; a held instrument with
            no quote or one past ``max_valuation_age_ms``; a calendar
            requirement no calendar window reproduces; a window whose
            open quantity has no fresh mark; a token breach.
        """
        _instant(at_ms, "at_ms")
        problems = []
        checked = self._checked_requirements(problems, requirements)
        marks = self._quotes_by_instrument(quotes)
        self._check_marked(problems, state_view.positions, marks, at_ms)
        if problems:
            raise ProductionError(problems)
        anchored = self._reanchor(checked, at_ms, calendar)
        folded = self._fold(at_ms, anchored)
        evidence = {}
        for digest, requirement in anchored.items():
            evidence.setdefault(digest, {})[requirement.scope_key] = self._evidence(
                requirement, folded, state_view, marks, at_ms
            )
        economic_seq = state_view.risk_version.economic_seq
        account = AccountState(
            risk_version=RiskVersion(economic_seq=economic_seq, executor_token=None, accounting_tokens=None),
            asof_ms=at_ms,
            evidence_digest=canonical_hash(
                {d: {s: e.to_obj() for s, e in by_scope.items()} for d, by_scope in evidence.items()}
            ),
            balances=tuple(
                Balance(currency=currency, total=total, available=total, native=None)
                for currency, total in sorted(state_view.balances.items())
            ),
            positions=tuple(state_view.positions),
            working=tuple(state_view.working.values()),
            measure_evidence=evidence,
            source_digests=folded.digests,
        )
        version = self.bind_tokens(executor, at_ms, economic_seq, account.risk_digest())
        return dataclasses.replace(account, risk_version=version)

    # -- requirements: checking and re-anchoring (R8) ---------------------------------

    def _checked_requirements(self, problems, requirements):
        """Return the requirements this strategy can answer, appending why any cannot."""
        checked = []
        for position, requirement in enumerate(requirements):
            if not isinstance(requirement, EvidenceRequirement):
                problems.append(f"requirements[{position}] is not an EvidenceRequirement: {requirement!r}")
            elif requirement.measure not in self._EVIDENCE:
                problems.append(
                    f"requirements[{position}]: measure {requirement.measure!r} is not one "
                    f"{type(self).__name__} answers ({sorted(self._EVIDENCE)})"
                )
            else:
                checked.append(requirement)
        return checked

    def _reanchor(self, requirements, at_ms, calendar):
        """Return ``{digest: requirement}`` with every requirement re-anchored at ``at_ms``."""
        anchored = {}
        for requirement in requirements:
            for window in getattr(self, self._REANCHOR[requirement.window_kind])(requirement, calendar):
                start, end, baseline, arg = window.resolve(at_ms, calendar)
                fresh = EvidenceRequirement(
                    measure=requirement.measure,
                    window_kind=window.kind,
                    window_arg=arg,
                    scope_key=requirement.scope_key,
                    window_start_ms=start,
                    window_end_ms=end,
                    baseline_at_ms=baseline,
                    include_working=requirement.include_working,
                )
                anchored.setdefault(fresh.requirement_digest, fresh)
        return anchored

    def _windows_plain(self, requirement, calendar):
        """Re-anchor a none/duration/count requirement as the window it declares."""
        return (Window(requirement.window_kind, requirement.window_arg),)

    def _windows_calendar(self, requirement, calendar):
        """Re-anchor a calendar requirement as every calendar kind reproducing its bounds."""
        bounds = tuple(requirement.window_arg)
        windows = []
        for name in CALENDAR_WINDOWS:
            try:
                resolved = calendar.window(name, requirement.window_start_ms)
            except ProductionError:
                continue
            if tuple(resolved) == bounds:
                windows.append(Window("calendar", name))
        if not windows:
            raise ProductionError(
                [
                    f"cannot re-anchor {requirement.measure} over calendar window {bounds}: no "
                    f"calendar window kind reproduces those bounds at {requirement.window_start_ms}"
                ]
            )
        return tuple(windows)

    # -- the fold -------------------------------------------------------------------

    def _fold(self, at_ms, anchored):
        """Ask the history once, from the earliest window start, and keep what is effective as of ``at_ms``."""
        if not anchored:
            fills, cash_flows, marks = (), (), ()
        else:
            since_ms = min(requirement.window_start_ms for requirement in anchored.values())
            fills = effective_fills(self._history.fills(since_ms), at_ms)
            cash_flows = effective_bodies(self._history.cash_flows(since_ms), at_ms, _CASH_FLOWS)
            marks = effective_bodies(self._history.marks(since_ms), at_ms, _MARKS)
        return _Folded(
            fills=fills,
            cash_flows=cash_flows,
            marks=marks,
            digests={
                _FILLS: canonical_hash([fill.to_obj() for fill in fills]),
                _CASH_FLOWS: canonical_hash(list(cash_flows)),
                _MARKS: canonical_hash(list(marks)),
            },
        )

    # -- evidence -------------------------------------------------------------------

    def _evidence(self, requirement, folded, state_view, marks, at_ms):
        """Answer one re-anchored requirement with a fresh ``MeasureEvidence``."""
        value, sample_count, sources = getattr(self, self._EVIDENCE[requirement.measure])(
            requirement, folded, state_view, marks, at_ms
        )
        return MeasureEvidence(
            requirement_digest=requirement.requirement_digest,
            value=value,
            sample_count=sample_count,
            window_start_ms=requirement.window_start_ms,
            window_end_ms=requirement.window_end_ms,
            scope_key=requirement.scope_key,
            effective_at_ms=min(requirement.window_end_ms, at_ms),
            known_at_ms=at_ms,
            source_digests=sources,
        )

    def _pnl(self, requirement, folded, state_view, marks, at_ms):
        """Realised plus marked trading profit of the window's fills."""
        book, fills = self._book(requirement, folded, at_ms)
        value = decimal_of(book.pnl(self._marker(marks, at_ms)))
        return value, len(fills), {_FILLS: folded.digests[_FILLS]}

    def _drawdown(self, requirement, folded, state_view, marks, at_ms):
        """Peak-to-trough decline of the window's cumulative trading pnl."""
        book, fills = self._book(requirement, folded, at_ms)
        value = decimal_of(book.drawdown(self._marker(marks, at_ms)))
        return value, len(fills), {_FILLS: folded.digests[_FILLS]}

    def _consecutive_losses(self, requirement, folded, state_view, marks, at_ms):
        """Return the trailing run of losing closing fills in the window."""
        book, _fills = self._book(requirement, folded, at_ms)
        return Decimal(book.consecutive_losses()), book.closes, {_FILLS: folded.digests[_FILLS]}

    def _error_vs_realised(self, requirement, folded, state_view, marks, at_ms):
        """Weighted mean absolute prediction error over the window's realised outcomes."""
        legs = self._legs(state_view)
        samples = []
        for mark in folded.marks:
            leg = legs.get(mark.get("leg_id"))
            if leg is None:
                continue
            prediction, instrument = leg
            if not self._in_scope(requirement, instrument) or not self._in_window(
                requirement, mark[_EFFECTIVE], at_ms
            ):
                continue
            sample = self._realised_sample(mark, prediction)
            if sample is not None:
                samples.append(sample)
        selected = getattr(self, self._SELECT[requirement.window_kind])(samples, requirement)
        weight = sum((w for _error, w in selected), _ZERO)
        value = sum((error * w for error, w in selected), _ZERO) / weight if weight else _ZERO
        return value, len(selected), {_MARKS: folded.digests[_MARKS]}

    @staticmethod
    def _realised_sample(mark, prediction):
        """Return ``(|prediction − value|, weight)`` for a terminal realised outcome, else None."""
        problems = []
        outcome_kind = mark.get("outcome_kind")
        if outcome_kind not in _REALISED:
            problems.append(f"outcome {mark[_ID]!r}: outcome_kind {outcome_kind!r} is unknown")
        if not isinstance(mark.get("terminal"), bool):
            problems.append(f"outcome {mark[_ID]!r}: terminal must be a bool")
        try:
            value, weight = Decimal(str(mark.get("value"))), Decimal(str(mark.get("weight")))
        except ArithmeticError:
            problems.append(f"outcome {mark[_ID]!r}: value and weight must be decimal strings")
        if problems:
            raise ProductionError(problems)
        if not mark["terminal"] or not _REALISED[outcome_kind]:
            return None
        return abs(prediction - value), weight

    @staticmethod
    def _legs(state_view):
        """Return ``leg_id -> (prediction, instrument)`` from the fold's decision history."""
        legs = {}
        for entry in state_view.decision_history:
            leg_id, prediction, instrument = (
                entry.get("leg_id"),
                entry.get("prediction"),
                entry.get("instrument"),
            )
            if (
                isinstance(leg_id, str)
                and isinstance(prediction, (int, float))
                and not isinstance(prediction, bool)
                and isinstance(instrument, str)
            ):
                legs[leg_id] = (Decimal(str(prediction)), instrument)
        return legs

    def _book(self, requirement, folded, at_ms):
        """Fold the window's in-scope fills into a ``WindowBook``; return it with the fills."""
        fills = [
            fill
            for fill in folded.fills
            if self._in_scope(requirement, fill.instrument) and self._in_window(requirement, fill.ts_ms, at_ms)
        ]
        fills = getattr(self, self._SELECT[requirement.window_kind])(fills, requirement)
        book = WindowBook()
        for fill in fills:
            book.apply(fill)
        return book, fills

    @staticmethod
    def _in_scope(requirement, instrument):
        """Say whether ``instrument`` falls under the requirement's scope key."""
        return requirement.scope_key == AGGREGATE_SCOPE_KEY or instrument == requirement.scope_key

    @staticmethod
    def _in_window(requirement, instant, at_ms):
        """Say whether ``instant`` lies in ``[window_start_ms, min(window_end_ms, at_ms)]``."""
        return requirement.window_start_ms <= instant <= min(requirement.window_end_ms, at_ms)

    def _keep_all(self, entries, requirement):
        """Keep every in-range sample — a none, duration or calendar window."""
        return list(entries)

    def _keep_last(self, entries, requirement):
        """Keep the last ``window_arg`` in-range samples — a count window."""
        return list(entries)[-requirement.window_arg :]

    # -- marks ----------------------------------------------------------------------

    @staticmethod
    def _quotes_by_instrument(quotes):
        """Index a ``QuoteSet`` by instrument."""
        return {quote.instrument: quote for quote in quotes.quotes}

    def _fresh_mid(self, quote, at_ms):
        """Return the quote's ``mid`` when it is no older than ``max_valuation_age_ms``, else None."""
        if quote is None or at_ms - quote.asof_ms > self._max_age:
            return None
        return quote.mid

    def _check_marked(self, problems, positions, marks, at_ms):
        """Append a problem for every held instrument without a fresh quote."""
        for position in positions:
            quote = marks.get(position.instrument)
            if quote is None:
                problems.append(f"no quote marks held instrument {position.instrument!r}")
            elif self._fresh_mid(quote, at_ms) is None:
                problems.append(
                    f"quote for held instrument {position.instrument!r} is {at_ms - quote.asof_ms} ms "
                    f"old, past max_valuation_age_ms {self._max_age} — a stale mark cannot be bound"
                )

    def _marker(self, marks, at_ms):
        """Return ``instrument -> Fraction(mid)`` over fresh quotes, refusing an absent one."""

        def mark_of(instrument):
            mid = self._fresh_mid(marks.get(instrument), at_ms)
            if mid is None:
                raise ProductionError(
                    [f"no fresh quote marks {instrument!r}, whose quantity is open inside the window"]
                )
            return Fraction(mid)

        return mark_of


for _table in (PaperAccounting._REANCHOR, PaperAccounting._SELECT):
    if set(_table) != set(WINDOW_KINDS):
        raise ProductionError(["accounting.py: a window table does not cover vocab.WINDOW_KINDS"])


# ---------------------------------------------------------------------------
# RecordedAccounting — the replay strategy (D20)
# ---------------------------------------------------------------------------

#: The tape may record only the three hooks; ``source_tokens`` is never recorded.
_HOOKS = frozenset(Accounting.__abstractmethods__)
#: Hook -> the check its recorded answer must pass.
_ANSWERS = {"value": _answer_value, "classify": _answer_classify, "snapshot": _answer_snapshot}
if set(_ANSWERS) != _HOOKS:
    raise ProductionError(["accounting.py: the answer checks do not cover the Accounting hooks"])


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
        if not isinstance(method, str) or method not in _HOOKS:
            problems.append(f"{where}: {method!r} is not one of {sorted(_HOOKS)}")
            continue
        if isinstance(args, (str, bytes)) or not isinstance(args, (list, tuple)):
            problems.append(f"{where}: args must be a sequence, got {args!r}")
            continue
        try:
            _ANSWERS[method](answer, where)
        except ProductionError as exc:
            problems.extend(exc.problems)
            continue
        entries.append((method, _frozen(args), answer))
    if problems:
        raise ProductionError(problems)
    return tuple(entries)


class RecordedAccounting(Accounting):
    """The replay strategy: the tape's answers, in the recorded order (D20).

    Replay computes nothing and holds no history. Each hook consumes the
    next tape entry and must ask exactly what the recording asked — the
    same method with the same positional arguments (``value``:
    ``(at_ms, quote_digest)``; ``classify``: ``(proposal.id,)``;
    ``snapshot``: ``(at_ms, (requirement_digests,))``) — or the replay has
    diverged, and D20's parity claim is precisely that it did not. A call
    past the end of the tape refuses for the same reason.

    Parameters
    ----------
    params : dict, optional
        No knobs; ``notes`` is allowed.
    tape : sequence of (str, sequence, object)
        The ``(method, args, answer)`` triples a recorded run yields, in
        order; ``args`` compares positionally with lists read as tuples.

    Raises
    ------
    ProductionError
        At construction, if any entry is not a triple, names a method that
        is not one of the three hooks, carries non-sequence args, or
        records an answer of the wrong type.

    Examples
    --------
    A one-entry tape answers its nav, then refuses a second ask::

        replay = RecordedAccounting({}, tape=(("value", (at_ms, "3" * 64), Decimal("1125")),))
        replay.value(view, quotes, at_ms)  # Decimal('1125')
        replay.value(view, quotes, at_ms)
        # -> ProductionError: the tape is exhausted
    """

    def __init__(self, params=None, *, tape):
        super().__init__(params)
        self._tape = _checked_tape(tape)
        self._cursor = 0

    def _replay(self, method, args):
        """Return the next recorded answer, refusing any divergence from the tape."""
        position = self._cursor
        if position >= len(self._tape):
            raise ProductionError(
                [f"replay asked {method}{args!r} but the tape is exhausted after {position} entries"]
            )
        recorded_method, recorded_args, answer = self._tape[position]
        if (recorded_method, recorded_args) != (method, _frozen(args)):
            raise ProductionError(
                [
                    f"replay asked {method}{args!r} at entry {position} but the tape "
                    f"recorded {recorded_method}{recorded_args!r}"
                ]
            )
        self._cursor = position + 1
        return answer

    def value(self, state_view, quotes, at_ms):
        """Return the recorded nav for ``(at_ms, quotes.quote_digest)``.

        Parameters
        ----------
        state_view : StateView
        quotes : QuoteSet
        at_ms : int

        Returns
        -------
        Decimal or None

        Raises
        ------
        ProductionError
            If the next tape entry is not this call, or the tape is exhausted.
        """
        return self._replay("value", (at_ms, quotes.quote_digest))

    def classify(self, proposal, state):
        """Return the recorded risk effect for ``(proposal.id,)``.

        Parameters
        ----------
        proposal : Proposal
        state : TickState

        Returns
        -------
        str

        Raises
        ------
        ProductionError
            If the next tape entry is not this call, or the tape is exhausted.
        """
        return self._replay("classify", (proposal.id,))

    def snapshot(self, state_view, executor, quotes, at_ms, requirements, calendar):
        """Return the recorded ``AccountState`` for ``(at_ms, requirement digests)``.

        Parameters
        ----------
        state_view : StateView
        executor : Executor
        quotes : QuoteSet
        at_ms : int
        requirements : tuple of EvidenceRequirement
        calendar : Calendar

        Returns
        -------
        AccountState

        Raises
        ------
        ProductionError
            If the next tape entry is not this call, or the tape is exhausted.
        """
        digests = tuple(requirement.requirement_digest for requirement in requirements)
        return self._replay("snapshot", (at_ms, digests))


ACCOUNTING_KINDS = Registry("accounting", Accounting)
ACCOUNTING_KINDS.register("paper", PaperAccounting)
ACCOUNTING_KINDS.register("recorded", RecordedAccounting)
