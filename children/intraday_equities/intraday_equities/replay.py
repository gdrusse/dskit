"""Developmental equity replay policies over existing production seams (ADR-0120).

Gate 5a already proved ``ServeLoop`` + ``ReplayFeed`` + ``ReplayClock`` +
``PaperExecutor`` drive deterministic historical ticks; no generic hook
is missing and this module does not subclass ``ServeLoop``. Equity policy
(bar choice, next-bar-open quotes, ``(symbol, lead)`` identity, Schwab
costs) is injected into ``compose.bundles_for``: a ``BarTape`` is the
D20 tape (clock + feed), ``ReleaseIdSource`` allocates because this is
not a recorded series, ``_TapeCadence`` ticks at bar instants, this
object is the decider, and ``_PaperVenue`` records fills from compose's
``PaperExecutor``. Overlap/expiry is ADR-0120 **accepted**.

Every fill-model value is a field of ``configs/fill-policy.json``. This
file has no default for those knobs — a missing or unknown name refuses.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from collections import Counter, defaultdict
from dataclasses import replace
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dskit.onboarding import OnboardingRoot
from dskit.pipeline.base import config_hash, import_ref
from dskit.pipeline.node import (
    ConfigError,
    Node,
    ServingContract,
    check_int_param,
    register_node_kind,
    reject_unknown_params,
)
from dskit.pipeline.records import number_ok
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.bundles import Data, Invocation, ReplayTape
from dskit.production.cadence import Cadence
from dskit.production.cashflows import (
    RecurringCashFlowSchedule,
    ReplaceCashFlow,
    SkipCashFlow,
    WithdrawalCashFlow,
)
from dskit.production.compose import ReplayCashFlowComposer, bundles_for, guard_chain
from dskit.production.document import ServeDocument
from dskit.production.executor import PaperExecutor
from dskit.production.health import InstanceLock
from dskit.production.ids import ReleaseIdSource
from dskit.production.ledger import ServeRoot
from dskit.production.loop import ServeLoop
from dskit.production.reconcile import LedgerHistory
from dskit.production.records import (
    EntryBatch,
    ExecutionScope,
    FeedResult,
    InputWatermark,
    Proposal,
    Quote,
)
from dskit.production.release import (
    ReleaseManifest,
    RuntimeFingerprint,
    artifact_digest,
    runtime_capture_memo,
)

from .nodes_capital import SchwabCostModel

__all__ = [
    "BarTape",
    "CashFlowPolicy",
    "DevelopmentReplay",
    "FillPolicy",
    "HorizonBook",
    "ReplayAdapter",
    "ScheduledCashFlowPolicy",
]


def _child_root():
    """Return this package's child root from the file location."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _HashView:
    """Hand a mapping to ``config_hash``, which requires ``to_obj``."""

    def __init__(self, obj):
        self._obj = obj

    def to_obj(self):
        return self._obj


class FillPolicy:
    """Validated fill-model bundle. Values come from the document, never defaults.

    Parameters
    ----------
    params : dict
        Every name in :attr:`_PARAMS` is required. Closed vocabularies
        are the ADR-0120 accepted/ruled members; any other member
        refuses. ``notes`` is allowed.

    Examples
    --------
    Load the shipped developmental bundle::

        policy = FillPolicy.from_path(
            os.path.join(_child_root(), "configs", "fill-policy.json")
        )
        policy.fill_bar_offset  # 1 — from the JSON, not a Python default
    """

    _PARAMS = (
        "fill_bar_offset",
        "decision_price_field",
        "fill_price_field",
        "order_type",
        "partial_fills",
        "rejections",
        "halt_field",
        "halt_handling",
        "halted_true",
        "forced_exit_at",
        "forced_exit_horizon_basis",
        "forced_exit_price_field",
        "spread_bps",
        "taf_per_share",
        "sec31_bps",
        "min_price",
        "cost_model",
        "mark_source",
        "same_lead_overlap",
        "different_lead_overlap",
        "same_tick_order",
        "horizon_field",
        "symbol_field",
        "qty_field",
        "side_field",
        "paper_fill_rule",
        "paper_fees",
        "p_fill_on_touch",
        "submit_latency_ms",
        "cancel_latency_ms",
        "seed",
        "fill_suffix_bars",
        "fill_suffix_weekdays",
    )
    _VOCAB = {
        "order_type": ("market",),
        "rejections": ("none",),
        "halt_handling": ("skip", "queue"),
        "forced_exit_at": ("horizon_expiry",),
        "forced_exit_horizon_basis": ("fill",),
        "mark_source": ("fill_bar_open",),
        "same_lead_overlap": ("refuse", "override"),
        "different_lead_overlap": ("concurrent",),
        "same_tick_order": ("exits_then_entries",),
        "paper_fill_rule": ("touch",),
        "paper_fees": ("none",),
        "cost_model": ("intraday_equities.nodes_capital:SchwabCostModel",),
    }

    def __init__(self, params):
        problems = self.validate_params(params)
        if problems:
            raise ConfigError(problems)
        self._params = dict(params)
        for name in self._PARAMS:
            setattr(self, name, params[name])
        cost_cls = import_ref(params["cost_model"])
        self.costs = cost_cls({name: params[name] for name in SchwabCostModel._PARAMS})

    @classmethod
    def from_path(cls, path):
        """Load and validate one JSON fill-policy document."""
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + ("notes",))
        for name in cls._PARAMS:
            if name not in params:
                problems.append(f"{name} is required")
        if problems:
            return problems
        check_int_param(problems, "fill_bar_offset", params["fill_bar_offset"], ge=1)
        check_int_param(problems, "submit_latency_ms", params["submit_latency_ms"], ge=0)
        check_int_param(problems, "cancel_latency_ms", params["cancel_latency_ms"], ge=0)
        check_int_param(problems, "seed", params["seed"], ge=0)
        check_int_param(problems, "fill_suffix_bars", params["fill_suffix_bars"], ge=1)
        check_int_param(problems, "fill_suffix_weekdays", params["fill_suffix_weekdays"], ge=1)
        if params["partial_fills"] is not False:
            problems.append(
                f"partial_fills must be false for this fill model, got {params['partial_fills']!r}"
            )
        if params["halted_true"] is not True:
            problems.append(
                f"halted_true must be true (JSON true) for this fill model, got "
                f"{params['halted_true']!r}"
            )
        if not number_ok(params["p_fill_on_touch"]) or params["p_fill_on_touch"] != 1.0:
            problems.append(
                f"p_fill_on_touch must be 1.0 for this fill model, got "
                f"{params['p_fill_on_touch']!r}"
            )
        for name, allowed in cls._VOCAB.items():
            if params[name] not in allowed:
                problems.append(
                    f"{name} must be one of {list(allowed)}, got {params[name]!r}"
                )
        for name in ("decision_price_field", "fill_price_field", "forced_exit_price_field",
                     "halt_field", "horizon_field", "symbol_field", "qty_field", "side_field"):
            if not isinstance(params[name], str) or not params[name]:
                problems.append(f"{name} must be a non-empty string, got {params[name]!r}")
        problems.extend(SchwabCostModel.validate_params({
            name: params[name] for name in SchwabCostModel._PARAMS
        }))
        return problems

    def to_obj(self):
        """Return the document mapping this policy was built from."""
        return dict(self._params)

    def digest(self):
        """Identity hash of the fill-policy document (notes stripped)."""
        return config_hash(_HashView(self.to_obj()), exclude=())

    def paper_params(self):
        """``PaperExecutor`` knobs copied from this document — no literals."""
        return {
            "fill_rule": self.paper_fill_rule,
            "fees": {"kind": self.paper_fees},
            "partial_fills": self.partial_fills,
            "p_fill_on_touch": self.p_fill_on_touch,
            "latency_ms": {
                "submit": self.submit_latency_ms,
                "cancel": self.cancel_latency_ms,
            },
            "seed": self.seed,
        }


def _decimal_param(value, name):
    """Read a positive ``Decimal`` from a JSON string; refuse anything else."""
    if not isinstance(value, str):
        raise ConfigError([f"{name} must be a decimal string, got {value!r}"])
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ConfigError([f"{name} must be a decimal string, got {value!r}"]) from None
    if not amount.is_finite() or amount <= 0:
        raise ConfigError([f"{name} must be a positive finite amount, got {value!r}"])
    return amount


class CashFlowPolicy:
    """Validated replay cash-flow bundle: initial capital plus a daily contribution (ADR-0176).

    Values come from the document, never defaults, mirroring
    :class:`FillPolicy`. There is no standalone one-time-deposit primitive
    in ``dskit.production.cashflows`` (only ``WithdrawalCashFlow``, always
    sign-flipped negative), so :meth:`composer_for` folds the one-time
    initial capital into the schedule's first daily occurrence via the
    existing ``ReplaceCashFlow`` override rather than adding a new override
    type to that safety-critical module.

    Parameters
    ----------
    params : dict
        Every name in :attr:`_PARAMS` is required. ``notes`` is allowed.

    Examples
    --------
    Load the shipped P19 backtest bundle::

        policy = CashFlowPolicy.from_path(
            os.path.join(_child_root(), "configs", "cash-flow-policy.json")
        )
        policy.currency  # 'USD' — from the JSON, not a Python default
    """

    _PARAMS = (
        "currency",
        "daily_contribution_amount",
        "initial_capital_amount",
        "timezone",
    )
    #: Whether every ticked trading date must carry a booked flow.
    FUNDS_EVERY_TRADING_DAY = True

    def __init__(self, params):
        problems = self.validate_params(params)
        if problems:
            raise ConfigError(problems)
        self._params = dict(params)
        self.currency = params["currency"]
        self.daily_contribution_amount = _decimal_param(
            params["daily_contribution_amount"], "daily_contribution_amount"
        )
        self.initial_capital_amount = _decimal_param(
            params["initial_capital_amount"], "initial_capital_amount"
        )
        self.timezone = ZoneInfo(params["timezone"])

    @classmethod
    def from_path(cls, path):
        """Load and validate one JSON cash-flow-policy document.

        The document's optional ``kind`` names the policy class
        (:data:`_CASH_FLOW_POLICY_KINDS`); a document without one is this
        daily policy, so the shipped ADR-0176 file keeps its identity.

        Parameters
        ----------
        path : str
            The JSON document.

        Returns
        -------
        CashFlowPolicy
            The class the document's ``kind`` names, validated.

        Raises
        ------
        ConfigError
            The ``kind`` is unknown or the document is malformed.
        """
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
        kind = obj.get("kind") if isinstance(obj, dict) else None
        policy_cls = _CASH_FLOW_POLICY_KINDS.get(kind)
        if policy_cls is None:
            raise ConfigError([
                f"cash-flow policy kind {kind!r} is unknown; known: "
                f"{sorted(str(name) for name in _CASH_FLOW_POLICY_KINDS)}"
            ])
        return policy_cls(obj)

    def segment(self, carried, calendar_dates):
        """Return the policy one release segment of a longer run books under.

        Parameters
        ----------
        carried : Decimal or None
            The previous segment's closing cash, or ``None`` for the first
            segment (which books the initial capital itself).
        calendar_dates : frozenset of datetime.date
            Every trading date of the WHOLE run. The daily policy needs
            none of them: each segment re-anchors at its own first bar.

        Returns
        -------
        CashFlowPolicy
            ``self`` for the first segment; otherwise a policy whose
            initial capital is ``carried`` (ADR-0184).
        """
        del calendar_dates
        if carried is None:
            return self
        return CashFlowPolicy({**self.to_obj(), "initial_capital_amount": str(carried)})

    def describe_flow(self, body, first):
        """Name the rule behind one booked cash-flow record (ADR-0183 item 13).

        Parameters
        ----------
        body : dict
            The booked record's body.
        first : bool
            Whether it is the first record this replay booked, which
            :meth:`composer_for` replaced with initial capital plus that
            day's contribution.

        Returns
        -------
        tuple of str
            ``(rule, detail)``.
        """
        if first:
            return "initial_capital", (
                f"initial_capital_amount {self.initial_capital_amount} + "
                f"daily_contribution_amount {self.daily_contribution_amount} "
                f"{body['currency']}"
            )
        return "daily_contribution", (
            f"daily_contribution_amount {self.daily_contribution_amount} "
            f"{body['currency']}"
        )

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + ("notes",))
        for name in cls._PARAMS:
            if name not in params:
                problems.append(f"{name} is required")
        if problems:
            return problems
        if not isinstance(params["currency"], str) or not params["currency"]:
            problems.append(f"currency must be a non-empty string, got {params['currency']!r}")
        for name in ("daily_contribution_amount", "initial_capital_amount"):
            try:
                _decimal_param(params[name], name)
            except ConfigError as exc:
                problems.extend(exc.errors)
        if not isinstance(params["timezone"], str) or not params["timezone"]:
            problems.append(f"timezone must be a non-empty string, got {params['timezone']!r}")
        else:
            try:
                ZoneInfo(params["timezone"])
            except (ZoneInfoNotFoundError, ValueError):
                problems.append(f"timezone is not a known zoneinfo key: {params['timezone']!r}")
        return problems

    def to_obj(self):
        """Return the document mapping this policy was built from."""
        return dict(self._params)

    def digest(self):
        """Identity hash of the cash-flow-policy document (notes stripped)."""
        return config_hash(_HashView(self.to_obj()), exclude=())

    def composer_for(self, series_id, start_ms, trading_dates):
        """Build one ``ReplayCashFlowComposer`` anchored to a replay's own tape start.

        Parameters
        ----------
        series_id : str
            The replay's own fresh identity (``EquityReplay`` mints one
            ``uuid.uuid4()`` per run); used verbatim as the schedule's
            ``schedule_id``, so two replay runs can never collide.
        start_ms : int
            ``tape.start_ms()`` — epoch milliseconds of the tape's first
            instant.
        trading_dates : frozenset of datetime.date
            Local dates (in ``self.timezone``) on which the tape has at
            least one bar (ADR-0178). Every other date from the anchor's
            date through ``max(trading_dates)`` gets a ``SkipCashFlow``, so
            a non-trading day is never funded.

        Returns
        -------
        tuple
            ``(composer, funding_instants_ms)``. ``composer`` is the
            ``ReplayCashFlowComposer``, anchored so the schedule's first
            occurrence is ``initial_capital_amount +
            daily_contribution_amount`` and every later funded occurrence is
            ``daily_contribution_amount``. ``funding_instants_ms`` is a tuple
            of int epoch milliseconds, ascending, one per trading date: the
            instant each funded occurrence falls due.

        Raises
        ------
        ConfigError
            ``start_ms`` is ``0`` — the documented empty-tape signal
            (:class:`_CapturedEnvelopeReplayTape`/``BarTape``'s shared
            ``start_ms()`` fallback, ADR-0175): a cash-flow schedule has
            nothing to fund over zero bars, so this refuses before
            constructing anything rather than silently anchoring
            real-dollar flows at the 1970 epoch.
        ConfigError
            The derived anchor is a nonexistent or ambiguous local instant
            in ``self.timezone`` (a DST gap or fold) — re-raised from
            ``RecurringCashFlowSchedule``'s own construction-time
            ``_valid_local`` check, naming this policy and the offending
            instant rather than surfacing a bare, unattributed
            ``ValueError``.
        """
        if start_ms == 0:
            raise ConfigError([
                "cash-flow policy: the replay tape is empty (start_ms == 0); "
                "there is nothing to fund"
            ])
        anchor = datetime.fromtimestamp(
            start_ms / 1000, tz=timezone.utc
        ).astimezone(self.timezone)
        first_amount = self.initial_capital_amount + self.daily_contribution_amount
        overrides = [ReplaceCashFlow("initial-capital-seed", anchor, first_amount)]
        funding_instants_ms = []
        day = anchor.date()
        last = max(trading_dates)
        while day <= last:
            occurrence_at = anchor.replace(year=day.year, month=day.month, day=day.day)
            if day in trading_dates:
                funding_instants_ms.append(int(occurrence_at.timestamp() * 1000))
            else:
                overrides.append(
                    SkipCashFlow(f"non-trading-day-{day.isoformat()}", occurrence_at)
                )
            day += timedelta(days=1)
        try:
            schedule = RecurringCashFlowSchedule(
                schedule_id=series_id,
                anchor=anchor,
                interval_days=1,
                currency=self.currency,
                amount=self.daily_contribution_amount,
                timezone=self.timezone,
                overrides=tuple(overrides),
            )
        except ValueError as exc:
            raise ConfigError([
                f"cash-flow policy: could not anchor the schedule at "
                f"{anchor.isoformat()} ({self.timezone.key}): {exc}"
            ]) from exc
        return ReplayCashFlowComposer(schedule), tuple(funding_instants_ms)



_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
#: The only holiday rule ruled so far (ADR-0185 ruling 3).
_HOLIDAY_RULES = ("next_trading_day",)
#: Dated override kinds: the first three edit one SCHEDULED date, the last
#: two add a flow on any date.
_SCHEDULED_OVERRIDES = ("skip", "move", "replace")
#: The rule each booked amount reports (``describe_flow``).
_RULE_NAMES = {
    "scheduled": "scheduled_contribution",
    "move": "moved_contribution",
    "replace": "replaced_contribution",
}
_OVERRIDE_FIELDS = {
    "skip": ("kind", "date"),
    "move": ("kind", "date", "to"),
    "replace": ("kind", "date", "amount"),
    "one_off": ("kind", "date", "amount"),
    "withdrawal": ("kind", "date", "amount"),
}


def _iso_date_ok(value):
    """Whether ``value`` is a ``YYYY-MM-DD`` string."""
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return len(value) == 10


class ScheduledCashFlowPolicy(CashFlowPolicy):
    """Initial capital plus a recurring weekday contribution with dated overrides (ADR-0185).

    ``kind: "scheduled"``. The first contribution falls on the first
    ``first_weekday`` on or after the run's first trading date, then every
    ``interval_days``, at ``local_time`` in ``timezone``. A contribution
    date without a session rolls to the next trading date
    (``holiday_rule: "next_trading_day"``). The initial capital is booked
    at ``local_time`` on the first trading date. A funding instant is
    booked before a decision at that same instant (``read_entry`` funds
    first). External flows are ledger ``cash_flow`` records and never
    trading P&L.

    The phase is GLOBAL: :meth:`segment` binds the whole run's trading
    calendar, so a release boundary never re-anchors the alternation and a
    holiday roll that crosses a boundary is booked once, in the segment
    holding its target date.

    It rides the same core primitives as the daily policy: one daily
    ``RecurringCashFlowSchedule`` at ``local_time`` whose non-funded dates
    are skipped, whose funded dates are the schedule amount or a
    ``ReplaceCashFlow``, and whose withdrawals are ``WithdrawalCashFlow``.

    Parameters
    ----------
    params : dict
        Every name in :attr:`_PARAMS` is required; ``overrides`` (a list of
        ``{kind, date, ...}`` rows: ``skip``/``move`` (``to``)/``replace``
        (``amount``) edit a scheduled date, ``one_off``/``withdrawal``
        (``amount``) add a flow) and ``notes`` are optional.

    Examples
    --------
    Load the ADR-0185 policy and build one replay's composer::

        policy = CashFlowPolicy.from_path("configs/cash-flow-policy-biweekly.json")
        composer, instants = policy.composer_for("series", start_ms, trading_dates)
        policy.contribution_amount
        # -> Decimal('500')
    """

    _PARAMS = (
        "kind",
        "currency",
        "initial_capital_amount",
        "contribution_amount",
        "interval_days",
        "first_weekday",
        "local_time",
        "timezone",
        "holiday_rule",
    )
    _OPTIONAL_PARAMS = ("overrides",)
    FUNDS_EVERY_TRADING_DAY = False

    def __init__(self, params):
        problems = self.validate_params(params)
        if problems:
            raise ConfigError(problems)
        self._params = dict(params)
        self.currency = params["currency"]
        self.initial_capital_amount = _decimal_param(
            params["initial_capital_amount"], "initial_capital_amount"
        )
        self.contribution_amount = _decimal_param(
            params["contribution_amount"], "contribution_amount"
        )
        self.interval_days = params["interval_days"]
        self.weekday = _WEEKDAYS.index(params["first_weekday"])
        hour, minute = (int(part) for part in params["local_time"].split(":"))
        self.local_hour, self.local_minute = hour, minute
        self.timezone = ZoneInfo(params["timezone"])
        self.overrides = tuple(dict(row) for row in params["overrides"]) if "overrides" in params else ()
        self._carried = None
        self._calendar = None
        self._rules = {}

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + cls._OPTIONAL_PARAMS + ("notes",))
        for name in cls._PARAMS:
            if name not in params:
                problems.append(f"{name} is required")
        if problems:
            return problems
        if params["kind"] != "scheduled":
            problems.append(f"kind must be 'scheduled', got {params['kind']!r}")
        if not isinstance(params["currency"], str) or not params["currency"]:
            problems.append(f"currency must be a non-empty string, got {params['currency']!r}")
        for name in ("contribution_amount", "initial_capital_amount"):
            try:
                _decimal_param(params[name], name)
            except ConfigError as exc:
                problems.extend(exc.errors)
        interval = params["interval_days"]
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
            problems.append(f"interval_days must be a positive int, got {interval!r}")
        if params["first_weekday"] not in _WEEKDAYS:
            problems.append(f"first_weekday must be one of {list(_WEEKDAYS)}, got {params['first_weekday']!r}")
        problems.extend(cls._local_time_problems(params["local_time"]))
        if params["holiday_rule"] not in _HOLIDAY_RULES:
            problems.append(f"holiday_rule must be one of {list(_HOLIDAY_RULES)}, got {params['holiday_rule']!r}")
        if not isinstance(params["timezone"], str) or not params["timezone"]:
            problems.append(f"timezone must be a non-empty string, got {params['timezone']!r}")
        else:
            try:
                ZoneInfo(params["timezone"])
            except (ZoneInfoNotFoundError, ValueError):
                problems.append(f"timezone is not a known zoneinfo key: {params['timezone']!r}")
        problems.extend(cls._override_problems(params["overrides"] if "overrides" in params else []))
        return problems

    @staticmethod
    def _local_time_problems(value):
        """Problems with an ``HH:MM`` wall time."""
        parts = value.split(":") if isinstance(value, str) else ()
        if (
            len(parts) != 2 or not all(len(part) == 2 and part.isdigit() for part in parts)
            or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59)
        ):
            return [f"local_time must be HH:MM, got {value!r}"]
        return []

    @staticmethod
    def _override_problems(overrides):
        """Problems with the dated override rows."""
        if not isinstance(overrides, list):
            return [f"overrides must be a list, got {overrides!r}"]
        problems = []
        seen = set()
        for index, row in enumerate(overrides):
            kind = row.get("kind") if isinstance(row, dict) else None
            fields = _OVERRIDE_FIELDS.get(kind) if isinstance(kind, str) else None
            if fields is None:
                problems.append(f"overrides[{index}] kind must be one of {sorted(_OVERRIDE_FIELDS)}")
                continue
            if set(row) != set(fields):
                problems.append(f"overrides[{index}] must have exactly {list(fields)}")
                continue
            for name in ("date", "to"):
                if name in row and not _iso_date_ok(row[name]):
                    problems.append(f"overrides[{index}].{name} must be YYYY-MM-DD, got {row[name]!r}")
            if "amount" in row:
                try:
                    _decimal_param(row["amount"], f"overrides[{index}].amount")
                except ConfigError as exc:
                    problems.extend(exc.errors)
            if row["kind"] in _SCHEDULED_OVERRIDES:
                if row["date"] in seen:
                    problems.append(f"overrides[{index}] edits {row['date']} twice")
                seen.add(row["date"])
        return problems

    def segment(self, carried, calendar_dates):
        """Bind the whole run's calendar (the global phase) and this segment's carry.

        Parameters
        ----------
        carried : Decimal or None
            The previous segment's closing cash; ``None`` books the initial
            capital instead.
        calendar_dates : frozenset of datetime.date
            Every trading date of the whole run.

        Returns
        -------
        ScheduledCashFlowPolicy
            A copy with the same document identity.
        """
        bound = ScheduledCashFlowPolicy(self._params)
        bound._carried = carried
        bound._calendar = frozenset(calendar_dates)
        return bound

    def funding_plan(self, trading_dates):
        """Every date's deposit and withdrawal over ``trading_dates``, with its rules.

        Parameters
        ----------
        trading_dates : frozenset of datetime.date
            The dates this replay holds bars on.

        Returns
        -------
        tuple of dict
            Ascending ``{date, deposit, withdrawal, rules}`` rows (Decimals;
            ``rules`` a tuple of rule names), only for dates in
            ``trading_dates`` that book something.

        Raises
        ------
        ConfigError
            A ``skip``/``move``/``replace`` override names a date that is
            not a scheduled contribution date.
        """
        calendar = sorted(self._calendar if self._calendar is not None else trading_dates)
        first = calendar[0]
        deposits, withdrawals, rules = defaultdict(Decimal), defaultdict(Decimal), defaultdict(list)
        edits = {row["date"]: row for row in self.overrides if row["kind"] in _SCHEDULED_OVERRIDES}
        scheduled = self._scheduled_dates(first, calendar[-1])
        stray = sorted(set(edits) - {day.isoformat() for day in scheduled})
        if stray:
            raise ConfigError([f"cash-flow overrides name dates that are not scheduled contributions: {stray}"])
        for day in scheduled:
            edit = edits.get(day.isoformat(), {})
            if edit.get("kind") == "skip":
                continue
            target = date.fromisoformat(edit["to"]) if edit.get("kind") == "move" else day
            amount = Decimal(edit["amount"]) if edit.get("kind") == "replace" else self.contribution_amount
            rule = edit.get("kind") or "scheduled"
            self._book(deposits, rules, calendar, target, amount, rule, strict=rule == "move")
        for row in self.overrides:
            if row["kind"] == "one_off":
                self._book(deposits, rules, calendar, date.fromisoformat(row["date"]), Decimal(row["amount"]), "one_off")
            elif row["kind"] == "withdrawal":
                self._book(withdrawals, rules, calendar, date.fromisoformat(row["date"]), Decimal(row["amount"]), "withdrawal")
        if self._carried is None:
            deposits[first] += self.initial_capital_amount
            rules[first].insert(0, "initial_capital")
        else:
            opening = min(trading_dates)
            deposits[opening] += Decimal(self._carried)
            rules[opening].insert(0, "carried_cash")
        return tuple(
            {"date": day, "deposit": deposits.get(day, Decimal("0")),
             "withdrawal": withdrawals.get(day, Decimal("0")), "rules": tuple(rules[day])}
            for day in sorted(set(deposits) | set(withdrawals))
            if day in trading_dates and (deposits.get(day) or withdrawals.get(day))
        )

    def _scheduled_dates(self, first, last):
        """Return the contribution dates from the first ``first_weekday`` on or after ``first`` through ``last``."""
        day = first + timedelta(days=(self.weekday - first.weekday()) % 7)
        dates = []
        while day <= last:
            dates.append(day)
            day += timedelta(days=self.interval_days)
        return dates

    @staticmethod
    def _book(ledger, rules, calendar, target, amount, rule, strict=True):
        """Add ``amount`` on ``target`` rolled to the next trading date.

        Only a SCHEDULED contribution whose roll runs past the run's last
        trading date is dropped (the run ended); a dated override that
        cannot land inside the run refuses, so it never applies zero times.
        """
        index = bisect_left(calendar, target)
        inside = calendar[0] <= target and index < len(calendar)
        if not inside:
            if strict and rule != "scheduled":
                raise ConfigError([
                    f"cash-flow override {rule} on {target.isoformat()} falls outside the run "
                    f"({calendar[0].isoformat()}..{calendar[-1].isoformat()})"
                ])
            return
        booked = calendar[index]
        ledger[booked] += amount
        name = _RULE_NAMES.get(rule, rule)
        if booked != target:
            name = f"{name}(rolled from {target.isoformat()})"
        rules[booked].append(name)

    def _instant(self, day):
        """``local_time`` on ``day`` in the policy timezone."""
        return datetime(day.year, day.month, day.day, self.local_hour, self.local_minute, tzinfo=self.timezone)

    def composer_for(self, series_id, start_ms, trading_dates):
        """Build one replay's composer over its own trading dates (see the class docstring).

        Parameters
        ----------
        series_id : str
            The replay's own fresh identity, the schedule id.
        start_ms : int
            ``tape.start_ms()``; the replay's funding window opens there.
        trading_dates : frozenset of datetime.date
            Local dates with at least one bar.

        Returns
        -------
        tuple
            ``(composer, funding_instants_ms)``, one instant per booking date.
            A first instant before ``start_ms`` (a tape opening after
            ``local_time``) is booked before the first decision:
            ``EquityReplay`` opens its window at the earlier of the two.

        Raises
        ------
        ConfigError
            An empty tape or an unschedulable local instant.
        """
        if start_ms == 0:
            raise ConfigError(["cash-flow policy: the replay tape is empty (start_ms == 0); there is nothing to fund"])
        plan = self.funding_plan(trading_dates)
        instants = tuple(int(self._instant(row["date"]).timestamp() * 1000) for row in plan)
        self._rules = {row["date"]: row["rules"] for row in plan}
        deposits = {row["date"]: row["deposit"] for row in plan}
        anchor = self._instant(min(trading_dates))
        overrides = []
        day = anchor.date()
        while day <= max(trading_dates):
            amount = deposits.get(day, Decimal("0"))
            at = self._instant(day)
            if amount <= 0:
                overrides.append(SkipCashFlow(f"unfunded-{day.isoformat()}", at))
            elif amount != self.contribution_amount:
                overrides.append(ReplaceCashFlow(f"amount-{day.isoformat()}", at, amount))
            day += timedelta(days=1)
        overrides.extend(
            WithdrawalCashFlow(f"withdrawal-{row['date'].isoformat()}", self._instant(row["date"]), row["withdrawal"])
            for row in plan if row["withdrawal"]
        )
        try:
            schedule = RecurringCashFlowSchedule(
                schedule_id=series_id, anchor=anchor, interval_days=1, currency=self.currency,
                amount=self.contribution_amount, timezone=self.timezone, overrides=tuple(overrides),
            )
        except ValueError as exc:
            raise ConfigError([f"cash-flow policy: could not build the schedule at {anchor.isoformat()}: {exc}"]) from exc
        return ReplayCashFlowComposer(schedule), instants

    def describe_flow(self, body, first):
        """``(rule, detail)`` from the funding plan's rules for the booked date."""
        del first
        day = datetime.fromtimestamp(body["effective_at_ms"] / 1000, tz=timezone.utc).astimezone(self.timezone).date()
        if body["flow_kind"] == "withdrawal":
            return "withdrawal", f"withdrawal {body['amount']} {body['currency']}"
        names = [name for name in self._rules.get(day, ()) if name != "withdrawal"]
        return "+".join(name.split("(")[0] for name in names), f"{' + '.join(names)} {body['currency']}"


#: ``kind`` -> cash-flow policy class; a document without a ``kind`` is the daily policy.
_CASH_FLOW_POLICY_KINDS = {None: CashFlowPolicy, "scheduled": ScheduledCashFlowPolicy}


class HorizonBook:
    """Open lots keyed by ``(symbol, lead)`` under the proposed overlap rule.

    Parameters
    ----------
    policy : FillPolicy
        Closed overlap and expiry vocabularies.

    Examples
    --------
    Open one h2 lot and read its expiry index::

        book = HorizonBook(FillPolicy.from_path(
            os.path.join(_child_root(), "configs", "fill-policy.json")
        ))
        book.open_lot("AAA", 2, qty=10, side="buy", fill_index=1)
        book.expiry_index("AAA", 2)  # 3  — fill index + lead
    """

    def __init__(self, policy):
        self._policy = policy
        self._lots = {}

    def is_open(self, symbol, lead):
        """Return whether ``(symbol, lead)`` already holds a lot."""
        return (symbol, lead) in self._lots

    def open_lot(self, symbol, lead, qty, side, fill_index):
        """Record a new lot. Return False when the same-lead rule refuses."""
        key = (symbol, lead)
        overlap = self._policy.same_lead_overlap
        if key in self._lots:
            if overlap == "refuse":
                return False
            if overlap == "override":
                self.close_lot(symbol, lead)
            else:
                raise ConfigError([f"same_lead_overlap {overlap!r} has no dispatch"])
        if self._policy.forced_exit_horizon_basis == "fill":
            expiry_index = fill_index + lead
        else:
            raise ConfigError([
                f"forced_exit_horizon_basis {self._policy.forced_exit_horizon_basis!r} "
                "has no expiry rule"
            ])
        self._lots[key] = {
            "qty": qty,
            "side": side,
            "fill_index": fill_index,
            "expiry_index": expiry_index,
        }
        return True

    def expiry_index(self, symbol, lead):
        """Bar index of the forced exit for an open lot."""
        return self._lots[(symbol, lead)]["expiry_index"]

    def close_lot(self, symbol, lead):
        """Remove an open lot and return it."""
        return self._lots.pop((symbol, lead))

    def expiring(self, symbol, index):
        """Lots on ``symbol`` whose expiry bar is due (``expiry_index <= index``)."""
        return [
            (lead, lot)
            for (sym, lead), lot in tuple(self._lots.items())
            if sym == symbol and lot["expiry_index"] <= index
        ]

    def unclosed(self):
        """Open lots still on the book, as ``((symbol, lead), lot)`` pairs."""
        return tuple(self._lots.items())


class BarTape(ReplayTape):
    """Historical bars as D20 tape data: feed results, not a scheduler."""

    def __init__(self, bars, source_config_hash):
        counts = Counter(int(bar["asof_ms"]) for bar in bars)
        self._times = tuple(sorted(counts))
        self._source = source_config_hash
        self._results = tuple(
            FeedResult(
                status="live",
                acq_id=f"bar-{ts}",
                records_added=counts[ts],
                source_config_hash=source_config_hash,
                at_ms=ts,
            )
            for ts in self._times
        )

    def start_ms(self):
        """Return the first bar instant, or 0 when the tape is empty."""
        return self._times[0] if self._times else 0

    def feed_results(self):
        """Return one live pull per unique bar timestamp."""
        return self._results

    def id_allocations(self):
        """Return no recorded ids — ``ReleaseIdSource`` allocates live."""
        return ()


class _TapeCadence(Cadence):
    """Tick exactly at the tape's timestamps."""

    def __init__(self, times):
        super().__init__({})
        self._times = tuple(times)

    def next_tick(self, after_ms, calendar):
        """Return the next tape instant strictly after ``after_ms``."""
        for ts in self._times:
            if ts > after_ms:
                return ts
        return None


class _TapeEntry(Node):
    """Source root so ``Decider.prepare`` can classify the developmental entry.

    Compose requires a serving run document with one ``entry_read``. The
    tick never executes this node: ``EquityReplay`` overlays
    ``Data.decider`` after ``bundles_for`` returns.

    Parameters
    ----------
    params : dict
        ``since_ms`` (int or null), ``root`` / ``source`` / ``stream``
        (non-empty str) — the contract ``OnboardingRoot`` opens.

    Examples
    --------
    Construct the entry compose will classify::

        node = _TapeEntry("bars", {
            "since_ms": None, "root": "/tmp/ob", "source": "replay", "stream": "bars",
        })
        node.role
        # -> 'data'
    """

    role = "data"
    outputs = ("records",)
    _PARAMS = ("since_ms", "root", "source", "stream")

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify as the tick's one mutable read."""
        return "entry_read"

    @classmethod
    def serving_contract(cls, params, verified_run_evidence):
        """Bind the empty onboarding root compose opens, then overlays away."""
        return ServingContract(
            source_binding={
                "kind": "onboarding-stream",
                "root": params["root"],
                "source": params["source"],
                "stream": params["stream"],
            },
            entity_key_fields=("symbol",),
            event_time_field="asof_ms",
            digest_recipe={"kind": "stream-digest"},
        )

    @classmethod
    def validate_params(cls, params):
        """Require the contract fields; ``since_ms`` may be JSON null."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + ("notes",))
        for name in ("root", "source", "stream"):
            if not isinstance(params.get(name), str) or not params.get(name):
                problems.append(f"{name} must be a non-empty string")
        if params.get("since_ms") is not None:
            check_int_param(problems, "since_ms", params.get("since_ms"), ge=0)
        return problems

    def run(self, ctx, inputs):
        """Emit no rows — the overlay decider never asks this node to run."""
        return {"records": []}


class _TapeHead(Node):
    """Pure head so the served graph has a descendant of the entry.

    Parameters
    ----------
    params : dict
        None. ``notes`` is allowed.

    Examples
    --------
    Wire the entry through::

        node = _TapeHead("select", {})
        node.run(None, {"records": [{"symbol": "AAA"}]})
        # -> {'records': [{'symbol': 'AAA'}]}
    """

    role = "transform"
    outputs = ("records",)
    _PARAMS = ()

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify as a pure input reader."""
        return "pure"

    @classmethod
    def validate_params(cls, params):
        """Refuse unknown knobs."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + ("notes",))
        return problems

    def run(self, ctx, inputs):
        """Pass records through."""
        return {"records": list(inputs.get("records") or [])}


class _PaperVenue:
    """PaperExecutor that records equity fill rows after LegPipeline submits."""

    def __init__(self, inner, owner):
        self._inner = inner
        self._owner = owner

    def submit(self, intent, permit, state):
        meta = self._owner._pending_by_id.get(intent.proposal.id)
        if meta is not None:
            px = Decimal(str(meta["price"]))
            self._inner.on_quote(Quote(
                instrument=meta["symbol"], bid=px, ask=px, mid=px, asof_ms=meta["asof_ms"],
            ))
        ack = self._inner.submit(intent, permit, state)
        self._owner._on_ack(intent, ack)
        return ack

    def __getattr__(self, name):
        return getattr(self._inner, name)


class EquityReplay:
    """Drive compose.bundles_for + ServeLoop around the equity book.

    Parameters
    ----------
    policy : FillPolicy
        The fill-model bundle.
    cash_flow_policy : CashFlowPolicy, optional
        Funding (ADR-0176); ``None`` binds no composer.
    decider : object, optional
        A per-tick strategy (ADR-0184 S6) with ``decide(asof_ms,
        portfolio) -> list of decision rows``. After each tick's exits and
        entries, it is called with :meth:`_portfolio`'s live account state
        and its rows are queued exactly like upfront ``decisions`` (same
        refusals, next-bar fill). ``None`` (the default) leaves the
        upfront-decisions path unchanged.
    guards : dict, optional
        The serve document's ``guards`` map (ADR-0183 phase 2), judged by
        the production guard chain on every leg. ``None`` (the default)
        declares ``{}``, so every existing serve identity holds. A breach
        that refuses a leg fails the run loudly: the fill model forbids
        rejections, so the fill is "queued but never submitted".
    ledger_dir : str, optional
        Where the replay's ledger ROOT (``placement.ledger_root``: one
        ``<series_id>/`` holding ``series.json`` and ``ledger/``) is copied
        after the ledger closes and before the scratch dir is removed, so
        ``ServeRoot(ledger_dir, series_id)`` reads it back. The copy is
        made before any end-of-run refusal is raised, so a failed run
        (a guard breach) still leaves its evidence. An existing non-empty
        directory refuses before the replay runs; ``None`` (the default)
        keeps nothing.

    Attributes
    ----------
    findings : list of dict
        One row per decided leg that carries at least one guard finding
        (so empty without guards), joined to the fill it produced by
        proposal id: ``symbol``, ``lead``, ``kind``, ``asof_ms`` (the
        fill's), ``tick_id``, ``leg_id``, ``verdict`` (the leg's composite)
        and ``findings`` (the stored ``Finding.to_obj()`` dicts). A leg that
        never filled carries ``None`` for the four fill keys.
    series_id : str or None
        The last run's serve series id.
    ledger_head : tuple or None
        The last run's ``Ledger.head()``, ``(seq, hash)``.

    Examples
    --------
    Replay one decision under an observational size limit::

        replay = EquityReplay(policy, guards={"size": {"uses": "limit", "params": {
            "measure": "quantity", "bound": {"max": "1000000"}, "on_breach": "refuse",
        }}})
        replay.run(bars, decisions)
        replay.findings[0]["verdict"]  # 'allow'
    """

    def __init__(
        self, policy, cash_flow_policy=None, decider=None, guards=None, ledger_dir=None,
    ):
        self._policy = policy
        self._cash_flow_policy = cash_flow_policy
        self._decider = decider
        self._guards = guards
        self._ledger_dir = ledger_dir
        self.findings = []
        self.series_id = None
        self.ledger_head = None
        self._filled_by_id = {}
        self.proposer = self
        self.serving_hash = policy.digest()
        self.fills = []
        self.skipped = []
        self.refused = []
        self.cash_flows = []
        self.cash = []
        self._by_symbol = {}
        self._index_of = {}
        self._pending = {}
        self._book = HorizonBook(policy)
        self._venue = None
        self._source = policy.digest()
        self._asof = 0
        self._last_bar = {}
        self._queued = []
        self._pending_by_id = {}
        self._fault = None
        self._cash_flow_ledger = None
        self._cash_flow_composer = None
        self._cash_flow_window_ms = None
        self._cash_flow_funding_instants = ()
        self._cash_flow_funding_index = 0
        self._cash_balance = Decimal("0")
        #: Every cash-flow body this replay booked to its ledger, in order,
        #: with its exact decimal ``amount`` (``cash_flows`` holds the rows).
        self.booked_cash_flows = []

    @property
    def cash_balance(self):
        """The running cash balance (``Decimal``): funding plus every fill's cash."""
        return self._cash_balance

    def run(self, bars, decisions=()):
        """Drive ServeLoop over ``bars`` and return fills/skips/refusals."""
        policy = self._policy
        by_symbol = defaultdict(list)
        halt_field = policy.halt_field
        for bar in bars:
            row = dict(bar)
            row["asof_ms"] = int(row["asof_ms"])
            if halt_field in row:
                row[halt_field] = _halt_flag(row[halt_field], halt_field, row["asof_ms"])
            for field in (
                policy.fill_price_field,
                policy.forced_exit_price_field,
                policy.decision_price_field,
            ):
                if field in row:
                    _refuse_non_finite_price(row[field], field, row["asof_ms"])
            by_symbol[row[policy.symbol_field]].append(row)
        for seq in by_symbol.values():
            seq.sort(key=lambda row: row["asof_ms"])
            times = [row["asof_ms"] for row in seq]
            if len(set(times)) != len(times):
                symbol = seq[0][policy.symbol_field]
                raise ConfigError([
                    f"duplicate asof_ms on {symbol!r}: each bar on a name must have a unique timestamp"
                ])
        self._by_symbol = dict(by_symbol)
        self._index_of = {
            symbol: {bar["asof_ms"]: i for i, bar in enumerate(seq)}
            for symbol, seq in self._by_symbol.items()
        }
        self._pending = {symbol: defaultdict(list) for symbol in self._by_symbol}
        self._last_bar = {symbol: seq[0]["asof_ms"] for symbol, seq in self._by_symbol.items()}
        for decision in decisions:
            self._enqueue_decision(decision)
        if not self._by_symbol:
            return self._result()
        # One process mints this release and re-verifies it every tick: the
        # inventory is re-read only when it moved (ADR-0184 S7(d)).
        with runtime_capture_memo():
            self._run_loop(bars)
        last = {symbol: seq[-1]["asof_ms"] for symbol, seq in self._by_symbol.items()}
        for (sym, lead), lot in self._book.unclosed():
            self.refused.append({
                "symbol": sym, "asof_ms": last.get(sym), "lead": lead, "qty": lot["qty"],
                "reason": "expiry_past_tape",
            })
            self._book.close_lot(sym, lead)
        return self._result()

    def _enqueue_decision(self, decision):
        """Queue one decision for its next-bar fill, or record why it is refused."""
        policy = self._policy
        symbol = decision[policy.symbol_field]
        lead = decision[policy.horizon_field]
        if symbol not in self._by_symbol:
            self.refused.append({
                "symbol": symbol, "asof_ms": decision["asof_ms"], "lead": lead,
                "reason": "unknown_symbol", "decision_ms": decision["asof_ms"],
            })
            return
        if isinstance(lead, bool) or not isinstance(lead, int) or lead < 1:
            self.refused.append({
                "symbol": symbol, "asof_ms": decision["asof_ms"], "lead": lead,
                "reason": "lead", "decision_ms": decision["asof_ms"],
            })
            return
        decision_index = self._index_of[symbol].get(int(decision["asof_ms"]))
        seq = self._by_symbol[symbol]
        if decision_index is None:
            self.refused.append({
                "symbol": symbol, "asof_ms": decision["asof_ms"], "lead": lead,
                "reason": "unknown_decision_bar", "decision_ms": decision["asof_ms"],
            })
            return
        if policy.decision_price_field not in seq[decision_index]:
            self.refused.append({
                "symbol": symbol, "asof_ms": decision["asof_ms"], "lead": lead,
                "reason": "decision_price_field", "decision_ms": decision["asof_ms"],
            })
            return
        fill_index = decision_index + policy.fill_bar_offset
        if fill_index >= len(seq):
            self.refused.append({
                "symbol": symbol, "asof_ms": decision["asof_ms"], "lead": lead,
                "reason": "fill_bar_past_tape", "decision_ms": decision["asof_ms"],
            })
            return
        self._pending[symbol][fill_index].append(decision)

    def _portfolio(self, asof):
        """Live account state at tick ``asof`` for a per-tick decider (ADR-0184 S6).

        ``cash`` = ``buying_power`` = the running cash balance after this
        tick's funding, exits and entries; ``positions`` holds the signed
        shares of lots that are still open at each name's fill bar (a lot
        expiring at or before it is exited before that bar's entries);
        ``mark_prices`` are each name's latest decision-price close; and
        ``gross_limit`` is NAV (cash plus every open lot at its mark).
        """
        policy = self._policy
        marks = {}
        for symbol, seq in self._by_symbol.items():
            bar = seq[self._index_of[symbol][self._last_bar[symbol]]]
            price = bar.get(policy.decision_price_field)
            if number_ok(price) and not isinstance(price, bool) and price > 0:
                marks[symbol] = float(price)
        cash = float(self._cash_balance)
        nav = cash
        positions = {}
        for (symbol, _lead), lot in self._book.unclosed():
            signed = lot["qty"] if lot["side"] == "buy" else -lot["qty"]
            nav += signed * marks.get(symbol, 0.0)
            index = self._index_of[symbol].get(asof)
            if index is not None and lot["expiry_index"] <= index + policy.fill_bar_offset:
                continue
            positions[symbol] = positions.get(symbol, 0) + signed
        return {
            "asof_ms": asof,
            "cash": cash,
            "buying_power": cash,
            "positions": positions,
            "mark_prices": marks,
            "gross_limit": nav,
        }

    def _result(self):
        self.fills.sort(key=lambda row: (
            row["asof_ms"], row["symbol"], row["lead"], 0 if row["kind"] == "exit" else 1,
        ))
        return {
            "fills": self.fills,
            "skipped": self.skipped,
            "refused": self.refused,
            "cash_flows": self.cash_flows,
            "cash": self.cash,
        }

    def _run_loop(self, bars):
        policy = self._policy
        digest = policy.digest()
        tape = BarTape(bars, self._source)
        times = tape._times
        symbols = sorted(self._by_symbol)
        self._refuse_occupied_ledger_dir()
        work = tempfile.mkdtemp(prefix="gate5-replay-")
        series_id = str(uuid.uuid4())
        self.series_id = series_id
        try:
            run_dir, artifact, ob_root = _write_serving_run(work, policy)
            document = ServeDocument.from_obj(
                _serve_document(run_dir, series_id, symbols, times, guards=self._guards)
            )
            release = _release_for(
                run_dir, artifact, symbols, digest, self._source,
                document, tape.start_ms(), ob_root,
            )
            serve = ServeRoot(os.path.join(work, "serve"), series_id)
            lock = InstanceLock(serve.lock_path)
            lock.acquire()
            invocation = Invocation(
                armed=False, env_release_hash=None, once=False,
                max_ticks=max(len(times), 1),
            )
            cash_flow_composer = None
            if self._cash_flow_policy is not None:
                trading_dates = frozenset(
                    datetime.fromtimestamp(t / 1000, tz=timezone.utc)
                    .astimezone(self._cash_flow_policy.timezone)
                    .date()
                    for t in times
                )
                cash_flow_composer, cash_flow_funding_instants = (
                    self._cash_flow_policy.composer_for(
                        series_id, tape.start_ms(), trading_dates
                    )
                )
            try:
                schedule, data, decision, safety, execution, recording, observability = bundles_for(
                    document,
                    release,
                    None,
                    serve_root=serve,
                    secrets={},
                    invocation=invocation,
                    process_id="replay-1",
                    lock=lock,
                    journal_hook=_journal_noop,
                    tape=tape,
                    cash_flow_composer=cash_flow_composer,
                )
            except ProductionError as exc:
                raise ConfigError(list(exc.problems)) from exc
            if cash_flow_composer is not None:
                self._cash_flow_ledger = recording.ledger
                self._cash_flow_composer = cash_flow_composer
                # A scheduled policy's first instant can precede a tape that
                # opens late; the window opens at whichever comes first, so
                # that deposit is booked before the first decision. The daily
                # policy's first instant IS the tape start (a no-op there).
                self._cash_flow_window_ms = min(
                    [tape.start_ms(), *cash_flow_funding_instants[:1]]
                )
                self._cash_flow_funding_instants = cash_flow_funding_instants
                self._cash_flow_funding_index = 0
            else:
                self._cash_flow_ledger = None
                self._cash_flow_composer = None
                self._cash_flow_window_ms = None
                self._cash_flow_funding_instants = ()
                self._cash_flow_funding_index = 0
            self._venue = _PaperVenue(
                PaperExecutor(
                    policy.paper_params(),
                    clock=schedule.clock,
                    scope=document.coordination.scope,
                ),
                self,
            )
            schedule = replace(schedule, cadence=_TapeCadence(times))
            data = Data(feed=data.feed, decider=self)
            execution = replace(execution, executor=self._venue)
            recording = replace(
                recording, id_source=ReleaseIdSource(release.release_hash),
            )
            loop = ServeLoop(
                document, release, schedule, data, decision, safety, execution,
                recording, observability, lock=lock, process_id="replay-1",
            )
            code = loop.run()
            try:
                self._flush_cash_flows()
            except (KeyError, TypeError, ValueError, ArithmeticError, ProductionError) as exc:
                raise ConfigError([str(exc)]) from exc
            failed = []
            for envelope in recording.ledger.scan(kind="tick"):
                body = envelope.get("body") or {}
                if body.get("status") == "failed" or body.get("status") == "refused":
                    err = body.get("error") or {}
                    cls = err.get("class") or body.get("status") or "tick"
                    text = err.get("text") or body.get("refusal_reason") or ""
                    failed.append(f"{cls}: {text}")
            leftover = list(self._queued) + list(self._pending_by_id)
            legs = LedgerHistory(recording.ledger).leg_findings(0)
            self.ledger_head = recording.ledger.head()
            recording.ledger.close()
            lock.release()
            self.findings = self._findings_rows(legs)
            self._keep_ledger(os.path.join(work, "serve"))
            if self._fault is not None:
                raise self._fault
            if failed:
                raise ConfigError([
                    f"ServeLoop tick status failed: {failed[0]}"
                ])
            if leftover:
                raise ConfigError([
                    f"{len(leftover)} fill(s) queued but never submitted"
                ])
            if code != 0:
                raise ConfigError([
                    f"ServeLoop exited {code} state={loop.state!r}"
                ])
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _refuse_occupied_ledger_dir(self):
        """Refuse a ``ledger_dir`` that exists and is not an empty directory."""
        target = self._ledger_dir
        if target is None or not os.path.lexists(target):
            return
        if not os.path.isdir(target) or os.listdir(target):
            raise ConfigError([
                f"ledger_dir {target!r} already exists and is not empty; "
                "a kept ledger is never merged into or overwritten"
            ])

    def _keep_ledger(self, ledger_root):
        """Copy the closed ledger root to ``ledger_dir`` when one was asked for."""
        if self._ledger_dir is None:
            return
        self._refuse_occupied_ledger_dir()
        shutil.copytree(ledger_root, self._ledger_dir, dirs_exist_ok=True)

    def _findings_rows(self, legs):
        """Join each judged leg to the fill it produced, by proposal id."""
        rows = []
        for leg in legs:
            if not leg["findings"]:
                continue
            fill = self._filled_by_id.get(leg["proposal_id"], {})
            rows.append({
                "symbol": fill.get("symbol"),
                "lead": fill.get("lead"),
                "kind": fill.get("kind"),
                "asof_ms": fill.get("asof_ms"),
                "tick_id": leg["tick_id"],
                "leg_id": leg["leg_id"],
                "verdict": leg["verdict"],
                "findings": leg["findings"],
            })
        return rows

    def _submit_due_cash_flows(self, tick_at_ms):
        """Fund the window once this tick reaches the next funding instant (ADR-0176/0178)."""
        if self._cash_flow_composer is None:
            return
        instants = self._cash_flow_funding_instants
        index = self._cash_flow_funding_index
        if index >= len(instants) or tick_at_ms < instants[index]:
            return
        self._advance_cash_flow_window(tick_at_ms + 1)

    def _flush_cash_flows(self):
        """Fund every funding instant no tick reached, once the tape ends (ADR-0178 point 1.6)."""
        if self._cash_flow_composer is None or not self._cash_flow_funding_instants:
            return
        self._advance_cash_flow_window(self._cash_flow_funding_instants[-1] + 1)

    def _advance_cash_flow_window(self, window_end_ms):
        """Append records due in ``[_cash_flow_window_ms, window_end_ms)``; advance the index."""
        if window_end_ms <= self._cash_flow_window_ms:
            return
        start = datetime.fromtimestamp(self._cash_flow_window_ms / 1000, tz=timezone.utc)
        end_exclusive = datetime.fromtimestamp(window_end_ms / 1000, tz=timezone.utc)
        due = self._cash_flow_composer.due(start, end_exclusive)
        if due:
            self._cash_flow_ledger.append_many(due)
            for record in due:
                self._cash_balance += Decimal(record["body"]["amount"])
                self.cash_flows.append(self._cash_flow_row(record["body"]))
                self.booked_cash_flows.append(dict(record["body"]))
                self._snapshot_cash(record["body"]["effective_at_ms"])
        instants = self._cash_flow_funding_instants
        index = self._cash_flow_funding_index
        while index < len(instants) and instants[index] < window_end_ms:
            index += 1
        self._cash_flow_funding_index = index
        self._cash_flow_window_ms = window_end_ms

    def _cash_flow_row(self, body):
        """One submitted cash-flow record as an output row (ADR-0183 item 13).

        The policy names the rule (:meth:`CashFlowPolicy.describe_flow`):
        the replay only knows whether this is the first record it booked.
        """
        rule, detail = self._cash_flow_policy.describe_flow(body, not self.cash_flows)
        return {
            "asof_ms": body["effective_at_ms"],
            "amount": float(Decimal(body["amount"])),
            "currency": body["currency"],
            "rule": rule,
            "detail": f"{body['flow_kind']}: {detail}",
        }

    def _snapshot_cash(self, asof_ms):
        """Record the running balance at ``asof_ms``; one row per instant (last wins).

        Only a funded replay has a balance worth reporting — without a
        cash-flow policy the balance starts at zero and goes negative.
        """
        if self._cash_flow_composer is None:
            return
        row = {"asof_ms": asof_ms, "cash": float(self._cash_balance)}
        if self.cash and self.cash[-1]["asof_ms"] == asof_ms:
            self.cash[-1] = row
        else:
            self.cash.append(row)

    def read_entry(self, tick_at_ms):
        """Freeze every name's bar at this tick as the entry batch."""
        asof = int(tick_at_ms)
        self._submit_due_cash_flows(asof)
        records = []
        watermarks = {}
        for symbol, seq in self._by_symbol.items():
            idx = self._index_of[symbol].get(asof)
            if idx is not None:
                self._last_bar[symbol] = asof
                records.append(seq[idx])
            mark_at = self._last_bar[symbol]
            watermarks[symbol] = InputWatermark(
                key=symbol, latest_asof_ms=mark_at, source_digest=canonical_hash(symbol),
            )
        self._asof = asof
        keys = sorted(watermarks)
        digest = self._policy.digest()
        return EntryBatch(
            outputs={"records": records},
            watermarks_by_key=watermarks,
            required_keys_digest=canonical_hash(keys),
            coverage_digest=digest,
            data_asof_ms=min(w.latest_asof_ms for w in watermarks.values()),
            inputs_digest=digest,
            source_config_hash=self._source,
        )

    def evaluate(self, batch):
        """Queue equity exits/entries at this tape instant for LegPipeline."""
        try:
            asof = self._asof
            policy = self._policy
            if policy.forced_exit_at != "horizon_expiry":
                raise ConfigError([f"forced_exit_at {policy.forced_exit_at!r} has no dispatch"])
            if policy.mark_source != "fill_bar_open":
                raise ConfigError([f"mark_source {policy.mark_source!r} has no dispatch"])
            if policy.different_lead_overlap != "concurrent":
                raise ConfigError([
                    f"different_lead_overlap {policy.different_lead_overlap!r} has no dispatch"
                ])
            live = []
            for symbol, seq in self._by_symbol.items():
                idx = self._index_of[symbol].get(asof)
                if idx is not None and self._apply_exits(symbol, seq[idx], idx):
                    live.append((symbol, seq[idx], idx))
            for item in live:
                self._apply_entries(*item)
            if self._decider is not None:
                for decision in self._decider.decide(asof, self._portfolio(asof)):
                    self._enqueue_decision(decision)
            return {"records": list(batch.outputs["records"])}, self._policy.digest()
        except ConfigError as exc:
            if self._fault is None:
                self._fault = exc
            raise
        except (KeyError, TypeError, ValueError, ArithmeticError, ProductionError) as exc:
            wrapped = ConfigError([str(exc)])
            if self._fault is None:
                self._fault = wrapped
            raise wrapped from exc

    def candidates(self, head_outputs):
        """No extra candidates — equity size is already on the queued proposals."""
        return ()

    def quotes(self, head_outputs):
        """Return next-bar-open quotes so the tick's QuoteSet is non-empty."""
        try:
            policy = self._policy
            asof = self._asof
            found = []
            for symbol, seq in self._by_symbol.items():
                idx = self._index_of[symbol].get(asof)
                if idx is None:
                    continue
                bar = seq[idx]
                if self._halted(bar):
                    continue
                if policy.fill_price_field not in bar:
                    raise ConfigError([
                        f"bar missing {policy.fill_price_field!r} at asof_ms={asof!r}"
                    ])
                price = Decimal(str(bar[policy.fill_price_field]))
                quote = Quote(instrument=symbol, bid=price, ask=price, mid=price, asof_ms=asof)
                self._venue.on_quote(quote)
                found.append(quote)
            return found
        except ConfigError as exc:
            if self._fault is None:
                self._fault = exc
            raise
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            wrapped = ConfigError([str(exc)])
            if self._fault is None:
                self._fault = wrapped
            raise wrapped from exc

    def proposals(self, head_outputs, candidates, account, provenance):
        """Return this tick's queued equity orders for LegPipeline."""
        found = list(self.quotes(head_outputs))
        quote_digest = canonical_hash([quote.to_obj() for quote in found])
        digest = self._policy.digest()
        queued, self._queued = self._queued, []
        out = []
        for meta in queued:
            out.append(self._proposal_for(meta, digest, quote_digest))
        return out

    def _apply_exits(self, symbol, bar, index):
        """Apply halt skip/queue or forced exits at one fill bar; True when live.

        Every symbol's exits run before any symbol's entries on the same
        tick (ADR-0184 S1), so a same-bar sale funds a same-bar buy.
        """
        policy = self._policy
        halted = self._halted(bar)
        pending = self._pending[symbol]
        if halted:
            for lead, _lot in self._book.expiring(symbol, index):
                self.skipped.append({
                    "symbol": symbol, "asof_ms": bar["asof_ms"], "lead": lead, "reason": "halted",
                })
            incoming = list(pending.pop(index, ()))
            if policy.halt_handling == "skip":
                for decision in incoming:
                    self.skipped.append({
                        "symbol": symbol, "asof_ms": bar["asof_ms"], "reason": "halted",
                        "decision_ms": decision["asof_ms"],
                    })
            elif policy.halt_handling == "queue":
                nxt = index + 1
                if nxt >= len(self._by_symbol[symbol]):
                    for decision in incoming:
                        self.refused.append({
                            "symbol": symbol,
                            "asof_ms": bar["asof_ms"],
                            "lead": decision[self._policy.horizon_field],
                            "reason": "fill_bar_past_tape",
                            "decision_ms": decision["asof_ms"],
                        })
                else:
                    pending[nxt].extend(incoming)
            else:
                raise ConfigError([f"halt_handling {policy.halt_handling!r} has no dispatch"])
            return False
        if policy.same_tick_order != "exits_then_entries":
            raise ConfigError([f"same_tick_order {policy.same_tick_order!r} has no dispatch"])
        self._process_exits(symbol, bar, index)
        return True

    def _apply_entries(self, symbol, bar, index):
        """Open this live fill bar's pending entries, after every symbol's exits."""
        self._process_entries(symbol, bar, index, self._pending[symbol].pop(index, ()))

    def _halted(self, bar):
        """Return whether ``bar`` is halted, accepting JSON and numpy bools."""
        field = self._policy.halt_field
        if field not in bar:
            raise ConfigError([f"bar missing halt field {field!r} at asof_ms={bar.get('asof_ms')!r}"])
        flag = _halt_flag(bar[field], field, bar.get("asof_ms"))
        return flag == self._policy.halted_true

    def _process_exits(self, symbol, bar, index):
        """Force-exit lots whose expiry bar is this fill bar."""
        policy = self._policy
        price = bar[policy.forced_exit_price_field]
        for lead, lot in self._book.expiring(symbol, index):
            side = "sell" if lot["side"] == "buy" else "buy"
            fee = (
                policy.costs.sell_per_share(price) if side == "sell" else policy.costs.buy_per_share(price)
            ) * lot["qty"]
            self._queue_fill(
                "exit", symbol, lead, side, lot["qty"], price, bar["asof_ms"], fee, index,
                reason=policy.forced_exit_at,
            )
            self._book.close_lot(symbol, lead)

    def _process_entries(self, symbol, bar, index, incoming):
        """Open new lots after exits on this fill bar."""
        policy = self._policy
        price = bar[policy.fill_price_field]
        for decision in incoming:
            lead = decision[policy.horizon_field]
            qty = decision[policy.qty_field]
            side = decision[policy.side_field]
            if isinstance(qty, bool) or isinstance(qty, str) or not number_ok(qty) or qty <= 0:
                raise ConfigError([
                    f"qty must be a positive number, got {qty!r} at asof_ms={bar['asof_ms']!r}"
                ])
            if policy.fill_price_field not in bar:
                raise ConfigError([
                    f"bar missing {policy.fill_price_field!r} at asof_ms={bar['asof_ms']!r}"
                ])
            if self._book.is_open(symbol, lead) and policy.same_lead_overlap == "override":
                lot = self._book.close_lot(symbol, lead)
                exit_side = "sell" if lot["side"] == "buy" else "buy"
                exit_px = bar[policy.forced_exit_price_field]
                exit_fee = (
                    policy.costs.sell_per_share(exit_px) if exit_side == "sell"
                    else policy.costs.buy_per_share(exit_px)
                ) * lot["qty"]
                self._queue_fill(
                    "exit", symbol, lead, exit_side, lot["qty"], exit_px, bar["asof_ms"],
                    exit_fee, index, reason="same_lead_override",
                    decision_ms=decision["asof_ms"],
                )
            if policy.costs.below_floor(price):
                self.refused.append({
                    "symbol": symbol, "asof_ms": bar["asof_ms"], "lead": lead, "reason": "min_price",
                    "decision_ms": decision["asof_ms"],
                })
                continue
            fee = (
                policy.costs.buy_per_share(price) if side == "buy" else policy.costs.sell_per_share(price)
            ) * qty
            if self._cash_flow_composer is not None and side == "buy":
                cost = Decimal(str(price)) * Decimal(str(qty)) + Decimal(str(fee))
                if cost > self._cash_balance:
                    self.refused.append({
                        "symbol": symbol, "asof_ms": bar["asof_ms"], "lead": lead,
                        "reason": "insufficient_cash", "decision_ms": decision["asof_ms"],
                    })
                    continue
            if not self._book.open_lot(symbol, lead, qty, side, index):
                self.refused.append({
                    "symbol": symbol, "asof_ms": bar["asof_ms"], "lead": lead, "reason": "same_lead_open",
                    "decision_ms": decision["asof_ms"],
                })
                continue
            self._queue_fill(
                "entry", symbol, lead, side, qty, price, bar["asof_ms"], fee, index,
                decision_ms=decision["asof_ms"],
            )

    def _queue_fill(
        self, kind, symbol, lead, side, qty, price, asof_ms, fee, index,
        reason=None, decision_ms=None,
    ):
        """Remember one fill so ``proposals`` can hand it to LegPipeline.

        ``decision_ms`` is the decision bar an entry (or the override exit
        it forces) answers; ``reason`` is why an exit happened — the
        policy's ``forced_exit_at`` name or ``same_lead_override``. Both
        ride onto the output fill row so a report can join a fill back to
        the decision that caused it (ADR-0183).
        """
        prefix = "0" if kind == "exit" else "1"
        client_ref = f"{prefix}-{kind}-{symbol}-{lead}-{index}"
        meta = {
            "id": client_ref,
            "kind": kind,
            "symbol": symbol,
            "lead": lead,
            "side": side,
            "qty": qty,
            "price": price,
            "asof_ms": asof_ms,
            "fee": fee,
            "reason": reason,
            "decision_ms": decision_ms,
        }
        self._pending_by_id[client_ref] = meta
        self._queued.append(meta)
        cash_delta = Decimal(str(price)) * Decimal(str(qty))
        fee_d = Decimal(str(fee))
        if side == "buy":
            self._cash_balance -= cash_delta + fee_d
        else:
            self._cash_balance += cash_delta - fee_d
        self._snapshot_cash(asof_ms)

    def _proposal_for(self, meta, digest, quote_digest):
        """Build the Proposal LegPipeline submits for one queued fill."""
        policy = self._policy
        px = Decimal(str(meta["price"]))
        qty_d = Decimal(str(meta["qty"]))
        return Proposal(
            id=meta["id"],
            instrument=meta["symbol"],
            side=meta["side"],
            qty=qty_d,
            notional=None,
            limit=None,
            tif="ioc",
            expires_ms=meta["asof_ms"] + 86_400_000,
            reference_price=px,
            exposure=qty_d * px,
            direction="long" if meta["side"] == "buy" else "short",
            confidence=0.0,
            prediction=0.0,
            baseline=0.0,
            expected_value=0.0,
            inputs_asof_ms=meta["asof_ms"],
            inputs_digest=digest,
            coverage_digest=digest,
            quote_asof_ms=meta["asof_ms"],
            quote_digest=quote_digest,
            extra={"order_type": policy.order_type, "kind": meta["kind"], "lead": meta["lead"]},
        )

    def _on_ack(self, intent, ack):
        """Record one filled equity row after LegPipeline reaches the venue."""
        try:
            meta = self._pending_by_id.pop(intent.proposal.id, None)
            if meta is None:
                return
            if ack.status != "filled":
                raise ConfigError([
                    f"replay fill model forbids rejections, got status={ack.status!r} "
                    f"reason={ack.reason!r} for {intent.proposal.id}"
                ])
            row = _fill_row(
                meta["kind"], meta["symbol"], meta["lead"], meta["side"],
                meta["qty"], meta["price"], meta["asof_ms"], meta["fee"], ack,
            )
            for key in ("reason", "decision_ms"):
                if meta[key] is not None:
                    row[key] = meta[key]
            self.fills.append(row)
            self._filled_by_id[intent.proposal.id] = {
                key: row[key] for key in ("kind", "symbol", "lead", "asof_ms")
            }
        except ConfigError as exc:
            if self._fault is None:
                self._fault = exc
            raise


def _refuse_non_finite_price(value, field, asof_ms):
    """Refuse a present non-finite price; None/'' stay for halt-skip quotes."""
    if value is None or value == "":
        return
    if isinstance(value, Decimal):
        ok = value.is_finite()
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        ok = number_ok(value)
    else:
        return
    if ok:
        return
    raise ConfigError([
        f"{field} must be a finite number, got {value!r} at asof_ms={asof_ms!r}"
    ])


def _halt_flag(value, field, asof_ms):
    """Return a Python bool from a JSON or numpy bool; otherwise refuse."""
    if isinstance(value, bool):
        return value
    try:
        import numpy as np
        if isinstance(value, np.generic) and getattr(value.dtype, "kind", "") == "b":
            return bool(value)
    except ImportError:
        pass
    raise ConfigError([
        f"halt field {field!r} must be a JSON bool, got {value!r} at asof_ms={asof_ms!r}"
    ])


def _fill_row(kind, symbol, lead, side, qty, price, asof_ms, fee, ack):
    """One output fill; price/qty from the adapter, fee from SchwabCostModel."""
    return {
        "kind": kind,
        "symbol": symbol,
        "lead": lead,
        "side": side,
        "qty": qty,
        "price": float(ack.avg_price) if ack.avg_price is not None else float(price),
        "asof_ms": asof_ms,
        "fee": float(fee),
    }


def _journal_noop(**kwargs):
    """D22 hook that does not write a child journal row."""
    return None


def _write_serving_run(work, policy):
    """Write the run dir compose.Decider.prepare loads, plus an empty onboarding root."""
    run_dir = os.path.join(work, "run")
    os.makedirs(run_dir, exist_ok=True)
    ob = OnboardingRoot.create(os.path.join(work, "ob"))
    artifact = os.path.join(run_dir, "policy")
    with open(artifact, "w", encoding="utf-8") as fh:
        json.dump(policy.to_obj(), fh)
    config = {
        "name": "intraday-equities-development-replay-serving",
        "pipeline": {
            "bars": {
                "uses": "intraday_equities.replay:_TapeEntry",
                "params": {
                    "since_ms": None,
                    "root": ob.root,
                    "source": "replay",
                    "stream": "bars",
                },
            },
            "select": {
                "uses": "intraday_equities.replay:_TapeHead",
                "inputs": {"records": "$bars.records"},
            },
        },
    }
    with open(os.path.join(run_dir, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    return run_dir, artifact, ob.root


def _serve_document(run_dir, series_id, symbols, times, guards=None):
    """Shadow ServeDocument compose.bundles_for accepts; cadence is overlaid to the tape.

    ``guards`` is the document's guard map; ``None`` declares ``{}`` so every
    serve identity minted before guards existed is unchanged.
    """
    universe = list(symbols) or ["AAA"]
    span = (int(times[-1]) - int(times[0]) + 1) if times else 1
    horizon_ms = max(span * 2, 1)
    renew_every_ms = 10_000
    renew_timeout_ms = 2_000
    ttl_ms = max(horizon_ms, 2 * (renew_every_ms + renew_timeout_ms) + 1)
    return {
        "name": "intraday-equities-development-replay-serve",
        "series_id": series_id,
        "rung": "shadow",
        "serving": {
            "run_dir": run_dir,
            "adapter": "intraday_equities",
            "entry": {"node": "bars", "param": "since_ms", "window_ms": 14400000},
            "heads": ["select"],
            "required_universe": universe,
            "proposer": {
                "uses": "intent-rows",
                "params": {
                    "output": "records",
                    "fields": {"instrument": "symbol", "side": "side", "qty": "qty"},
                    "ttl_ms": 86400000,
                    "default_tif": "ioc",
                },
            },
            "max_artifact_age": "P100000D",
        },
        "feed": {"uses": "replay"},
        "schedule": {
            "clock": {"uses": "replay"},
            "calendar": {"uses": "always-open"},
            "cadence": {"uses": "fixed-interval", "params": {"period_ms": 1000}},
            "dead_after_ms": horizon_ms,
            "max_staleness_ms": horizon_ms,
            "max_quote_age_ms": horizon_ms,
        },
        "guards": {} if guards is None else guards,
        "execution": {"uses": "shadow", "submit_timeout_ms": 5000},
        "accounting": {"uses": "paper", "max_valuation_age_ms": horizon_ms},
        "arming": {"max_duration_s": 14400, "approval": {"uses": "deny-all"}},
        "coordination": {
            "scope": {"venue": "paper", "account": "replay"},
            "lease": {"uses": "process"},
            "ttl_ms": ttl_ms,
            "renew_every_ms": renew_every_ms,
            "renew_timeout_ms": renew_timeout_ms,
        },
        "reconcile": {
            "on_start": False,
            "every_s": 86400,
            "on_mismatch": "halt",
            "lookback_ms": 86400000,
        },
        "monitors": {},
        "health": {
            "failure_threshold": 3,
            "success_threshold": 1,
            "timeout_s": 1.0,
            "probes": {"disk": {"uses": "ledger-writable"}},
        },
        "durability": {"fsync": "none"},
        "resilience": {
            "retry": {
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
            },
            "breaker": {"min_calls": 5, "failure_rate": 0.5, "open_s": 30},
            "limiter": {
                "submit": {"rate_per_s": 5, "burst": 5, "max_in_flight": 1},
                "cancel": {"rate_per_s": 10, "burst": 10, "reserved": True},
            },
            "transport": {"uses": "urllib"},
        },
        "lifecycle": {"cooling_off_s": 900, "shutdown_grace_s": 30},
        "readiness": {
            "checklist": "configs/readiness.json",
            "waivers": [],
            "valid_for_s": 86400,
        },
        "alerting": {
            "sinks": {"ops": {"uses": "memory"}},
            "routes": [{"severity": "critical", "sinks": ["ops"]}],
        },
        "placement": {"ledger_root": "./serve"},
    }


#: A syntactically valid series id for validating a guard map; never served.
_VALIDATION_SERIES_ID = "00000000-0000-4000-8000-000000000000"


def _guard_problems(guards):
    """Judge a guard map by the serve-document grammar and the guard classes themselves."""
    try:
        guard_chain(ServeDocument.from_obj(
            _serve_document("guard-validation", _VALIDATION_SERIES_ID, (), (), guards=guards)
        ))
    except ProductionError as exc:
        return list(exc.problems)
    return []


def _release_for(run_dir, artifact, symbols, digest, source_hash, document, created_ms, ob_root):
    """Build a ReleaseManifest whose feed_spec matches ``_TapeEntry``'s contract."""
    hex64 = digest
    keys = list(symbols) or ["AAA"]
    return ReleaseManifest(
        series_id=document.series_id,
        doc_hash=document.doc_hash,
        run_hash=hex64,
        serving_hash=hex64,
        artifacts={"policy": {"digest": artifact_digest(artifact), "timestamp_ms": created_ms}},
        classes={"replay": {"ref": "intraday_equities.replay:FillPolicy", "code_digest": hex64}},
        adapter={"name": "intraday_equities", "digest": hex64},
        feed_spec={
            "source_binding": {
                "kind": "onboarding-stream",
                "root": ob_root,
                "source": "replay",
                "stream": "bars",
            },
            "entity_key_fields": ["symbol"],
            "event_time_field": "asof_ms",
            "digest_recipe": {"kind": "stream-digest"},
            "required_keys": keys,
            "required_keys_digest": canonical_hash(keys),
            "source_config_hash": source_hash,
            "source_config_version": "1",
        },
        source_config={"hash": source_hash, "version": "1"},
        execution_scope=ExecutionScope(venue="paper", account="replay"),
        approval_fingerprint=hex64,
        lease_fingerprint=hex64,
        checklist_digest=hex64,
        runtime_fingerprint=RuntimeFingerprint.capture(),
        created_ms=created_ms if created_ms > 0 else 1,
    )

class ReplayAdapter:
    """Drive ServeLoop with next-bar-open quotes and the overlap book.

    Parameters
    ----------
    policy : FillPolicy
        The fill-model bundle.
    cash_flow_policy : CashFlowPolicy, optional
        The initial-capital/daily-contribution bundle (ADR-0176). ``None``
        (the default) means no cash-flow composer is bound, matching this
        class's behavior before ADR-0176 exactly.
    guards, ledger_dir : optional
        Passed to :class:`EquityReplay` unchanged (ADR-0183 phase 2);
        ``None`` keeps the guard map ``{}`` and keeps no ledger.

    Examples
    --------
    One h1 buy filled at the next bar's open::

        policy = FillPolicy.from_path(
            os.path.join(_child_root(), "configs", "fill-policy.json")
        )
        adapter = ReplayAdapter(policy)
        out = adapter.replay(
            [{"symbol": "AAA", "asof_ms": 1000, "open": 10.0, "close": 10.5, "halted": False},
             {"symbol": "AAA", "asof_ms": 2000, "open": 11.0, "close": 11.5, "halted": False},
             {"symbol": "AAA", "asof_ms": 3000, "open": 12.0, "close": 12.5, "halted": False}],
            [{"symbol": "AAA", "asof_ms": 1000, "lead": 1, "qty": 10, "side": "buy"}],
        )
        out["fills"][0]["price"]  # 11.0
    """

    def __init__(self, policy, cash_flow_policy=None, guards=None, ledger_dir=None):
        self._policy = policy
        self._cash_flow_policy = cash_flow_policy
        self._guards = guards
        self._ledger_dir = ledger_dir

    def replay(self, bars, decisions):
        """Replay ``decisions`` over ``bars`` through :class:`ServeLoop`.

        Parameters
        ----------
        bars : list of dict
            Per-symbol bars carrying the policy's price and halt fields.
        decisions : list of dict
            Decision-bar rows carrying symbol, lead, side, qty, asof_ms.

        Returns
        -------
        dict
            ``fills``, ``skipped``, ``refused``, ``cash_flows`` (one row per
            cash-flow record the policy submitted: ``asof_ms``, ``amount``,
            ``currency``, ``rule``, ``detail``) and ``cash`` (the running
            balance after each change, ``{asof_ms, cash}``; both empty
            without a cash-flow policy), ``findings`` (the
            :attr:`EquityReplay.findings` rows; empty without guards) and
            ``ledger`` (``None`` unless ``ledger_dir`` was given; then
            ``root``, ``series_id``, ``seq`` and ``hash`` — the kept
            ledger's root and the head it closed at).
        """
        replay = EquityReplay(
            self._policy, self._cash_flow_policy,
            guards=self._guards, ledger_dir=self._ledger_dir,
        )
        out = replay.run(bars, decisions)
        out["findings"] = replay.findings
        out["ledger"] = None
        if self._ledger_dir is not None:
            seq, head = replay.ledger_head
            out["ledger"] = {
                "root": self._ledger_dir, "series_id": replay.series_id,
                "seq": seq, "hash": head,
            }
        return out

class DevelopmentReplay(Node):
    """Pipeline doorway for developmental replay: ineligible, evidence-bounded.

    Parameters
    ----------
    params : dict
        ``deployment_eligible`` (must be JSON false), ``evidence_end``
        (``YYYY-MM-DD``, last included UTC date), ``fill_policy`` (path
        relative to the child root), ``fill_policy_sha256`` (must match
        the loaded document), ``caps`` (must be ``development-only``).
        Optional, paired (ADR-0179): ``cash_flow_policy`` (path relative to
        the child root) and ``cash_flow_policy_sha256`` (must match the
        loaded :class:`CashFlowPolicy` digest) — both present or both
        absent. Absent means no cash-flow composer, exactly as before.
        Optional (ADR-0183 phase 2), each absent from the identity when
        omitted: ``keep_ledger`` (bool, default false; true copies the
        replay's ledger root to ``<artifact_dir>/ledger``) and ``guards``
        (the serve document's guard map, judged by the production document
        grammar and ``compose.guard_chain``; default ``{}``).

    Outputs: ``fills``, ``skipped``, ``refused``, ``cash_flows``, ``cash``,
    ``findings`` (one row per judged leg; empty without guards) and
    ``ledger`` (``None`` unless ``keep_ledger``; then ``root``,
    ``series_id``, ``seq``, ``hash``). A guard breach fails the run.

    Examples
    --------
    Construct the shipped developmental document's node::

        node = DevelopmentReplay("replay", {
            "deployment_eligible": False,
            "evidence_end": "2025-10-16",
            "fill_policy": "configs/fill-policy.json",
            "fill_policy_sha256": FillPolicy.from_path(
                os.path.join(_child_root(), "configs", "fill-policy.json")
            ).digest(),
            "caps": "development-only",
        })
        node.params["deployment_eligible"]  # False
    """

    role = "transform"
    outputs = ("fills", "skipped", "refused", "cash_flows", "cash", "findings", "ledger")
    _PARAMS = (
        "deployment_eligible",
        "evidence_end",
        "fill_policy",
        "fill_policy_sha256",
        "caps",
    )
    _OPTIONAL_PARAMS = ("cash_flow_policy", "cash_flow_policy_sha256", "keep_ledger", "guards")

    def __init__(self, key, params=None, **kwargs):
        super().__init__(key, params, **kwargs)
        self._policy = FillPolicy.from_path(self._resolved_fill_policy_path(self.params))
        if "cash_flow_policy" in self.params:
            self._cash_flow_policy = CashFlowPolicy.from_path(
                self._resolved_cash_flow_policy_path(self.params)
            )
        else:
            self._cash_flow_policy = None

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the node as a pure input/param reader."""
        return "pure"

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(
            problems, params, cls._PARAMS + cls._OPTIONAL_PARAMS + ("notes",)
        )
        for name in cls._PARAMS:
            if name not in params:
                problems.append(f"{name} is required")
        if problems:
            return problems
        if params["deployment_eligible"] is not False:
            problems.append(
                "deployment_eligible must be false — developmental replay is not "
                f"deployment evidence, got {params['deployment_eligible']!r}"
            )
        if params["caps"] != "development-only":
            problems.append(
                "caps must be 'development-only' until confirmed caps exist, got "
                f"{params['caps']!r}"
            )
        if not isinstance(params["evidence_end"], str):
            problems.append(f"evidence_end must be YYYY-MM-DD, got {params['evidence_end']!r}")
        else:
            try:
                datetime.strptime(params["evidence_end"], "%Y-%m-%d")
            except ValueError:
                problems.append(f"evidence_end must be YYYY-MM-DD, got {params['evidence_end']!r}")
        if not isinstance(params["fill_policy"], str) or not params["fill_policy"]:
            problems.append("fill_policy must be a non-empty path")
        else:
            path = cls._resolved_fill_policy_path(params)
            if not os.path.isfile(path):
                problems.append(f"fill_policy path does not exist: {path}")
            elif not isinstance(params["fill_policy_sha256"], str):
                problems.append("fill_policy_sha256 must be the fill-policy digest")
            else:
                loaded = FillPolicy.from_path(path)
                if loaded.digest() != params["fill_policy_sha256"]:
                    problems.append(
                        "fill_policy_sha256 does not match the loaded fill-policy digest"
                    )
        has_cash_flow_policy = "cash_flow_policy" in params
        if has_cash_flow_policy != ("cash_flow_policy_sha256" in params):
            problems.append(
                "cash_flow_policy and cash_flow_policy_sha256 must both be present "
                "or both be absent"
            )
        elif has_cash_flow_policy:
            if not isinstance(params["cash_flow_policy"], str) or not params["cash_flow_policy"]:
                problems.append("cash_flow_policy must be a non-empty path")
            else:
                path = cls._resolved_cash_flow_policy_path(params)
                if not os.path.isfile(path):
                    problems.append(f"cash_flow_policy path does not exist: {path}")
                elif not isinstance(params["cash_flow_policy_sha256"], str):
                    problems.append("cash_flow_policy_sha256 must be the cash-flow-policy digest")
                else:
                    loaded = CashFlowPolicy.from_path(path)
                    if loaded.digest() != params["cash_flow_policy_sha256"]:
                        problems.append(
                            "cash_flow_policy_sha256 does not match the loaded "
                            "cash-flow-policy digest"
                        )
        if "keep_ledger" in params and not isinstance(params["keep_ledger"], bool):
            problems.append(f"keep_ledger must be a bool, got {params['keep_ledger']!r}")
        if "guards" in params:
            problems.extend(_guard_problems(params["guards"]))
        return problems

    @classmethod
    def _resolved_fill_policy_path(cls, params):
        """Join a relative fill-policy path onto the child root."""
        path = params["fill_policy"]
        if os.path.isabs(path):
            return path
        return os.path.join(_child_root(), path)

    @classmethod
    def _resolved_cash_flow_policy_path(cls, params):
        """Join a relative cash-flow-policy path onto the child root."""
        path = params["cash_flow_policy"]
        if os.path.isabs(path):
            return path
        return os.path.join(_child_root(), path)

    def _exclusive_end_ms(self):
        """First UTC instant after ``evidence_end`` (epoch ms)."""
        return self._utc_day_end_ms(1)

    def _fill_weekdays_from(self, asof_ms):
        """Count UTC weekdays from the day after evidence_end through the bar's date."""
        evidence = datetime.strptime(self.params["evidence_end"], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        bar = datetime.fromtimestamp(int(asof_ms) / 1000, tz=timezone.utc)
        bar_day = datetime(bar.year, bar.month, bar.day, tzinfo=timezone.utc)
        if bar_day <= evidence:
            return 0
        days = 0
        cursor = evidence + timedelta(days=1)
        while cursor <= bar_day:
            if cursor.weekday() < 5:
                days += 1
            cursor += timedelta(days=1)
        return days

    def _utc_day_end_ms(self, extra_days):
        """Midnight UTC ``extra_days`` after ``evidence_end``, as epoch ms."""
        day = datetime.strptime(self.params["evidence_end"], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        return int((day + timedelta(days=extra_days)).timestamp() * 1000)

    def validate_inputs(self, inputs):
        """Require ``bars`` and ``decisions`` lists."""
        problems = []
        for port in ("bars", "decisions"):
            if port not in inputs:
                problems.append(f"{port} is required")
            elif not isinstance(inputs[port], (list, tuple)):
                problems.append(f"{port} must be a list, got {type(inputs[port]).__name__}")
        return problems

    def run(self, ctx, inputs):
        """Refuse out-of-window decisions/bars, then replay through :class:`ReplayAdapter`."""
        self._refuse_out_of_window(inputs["bars"], inputs["decisions"])
        ledger_dir = None
        if self.params.get("keep_ledger"):
            ledger_dir = os.path.join(self.artifact_dir(ctx), "ledger")
        return ReplayAdapter(
            self._policy, self._cash_flow_policy,
            guards=self.params.get("guards"), ledger_dir=ledger_dir,
        ).replay(list(inputs["bars"]), list(inputs["decisions"]))

    def _refuse_out_of_window(self, bars, decisions):
        """Refuse decisions at/after ``evidence_end`` and bars past the fill suffix.

        Shared by this node and ``DevelopmentSimulation`` (ADR-0184 S7), so
        the evidence window is enforced by one gate.

        Parameters
        ----------
        bars : iterable of dict
            Rows carrying ``asof_ms`` and ``symbol``.
        decisions : iterable of dict
            Rows carrying ``asof_ms``.

        Raises
        ------
        ConfigError
            A decision at or after the day after ``evidence_end``, a weekend
            fill-only bar, or fill-only bars beyond ``fill_suffix_weekdays``
            / ``fill_suffix_bars``.
        """
        exclusive = self._exclusive_end_ms()
        suffix_bars = int(self._policy.fill_suffix_bars)
        suffix_weekdays = int(self._policy.fill_suffix_weekdays)
        late_decisions = [
            row for row in decisions
            if int(row["asof_ms"]) >= exclusive
        ]
        if late_decisions:
            raise ConfigError([
                f"evidence_end {self.params['evidence_end']!r} excludes "
                f"{len(late_decisions)} decision(s) at or after {exclusive}"
            ])
        per_symbol = {}
        for bar in bars:
            asof_ms = int(bar["asof_ms"])
            if asof_ms < exclusive:
                continue
            when = datetime.fromtimestamp(asof_ms / 1000, tz=timezone.utc)
            if when.weekday() >= 5:
                raise ConfigError([
                    f"evidence_end {self.params['evidence_end']!r} excludes "
                    f"weekend fill-only bar(s) at {asof_ms}"
                ])
            if self._fill_weekdays_from(asof_ms) > suffix_weekdays:
                raise ConfigError([
                    f"evidence_end {self.params['evidence_end']!r} excludes "
                    f"bar(s) beyond fill_suffix_weekdays={suffix_weekdays}"
                ])
            symbol = str(bar["symbol"])
            per_symbol[symbol] = per_symbol.get(symbol, 0) + 1
            if per_symbol[symbol] > suffix_bars:
                raise ConfigError([
                    f"evidence_end {self.params['evidence_end']!r} excludes "
                    f"bar(s) beyond fill_suffix_bars={suffix_bars}"
                ])


NODE_KINDS = {
    "intraday_equities-development-replay": DevelopmentReplay,
}

for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)
