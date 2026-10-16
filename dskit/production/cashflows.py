"""Materialize declared replay cash flows without treating them as settlement."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone as utc_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from dskit.production.base import canonical_hash, pin_members
from dskit.production.vocab import CASH_FLOW_KINDS

__all__ = [
    "CashFlowOverride", "CorrectCashFlow", "DEFAULT_OVERRIDES", "DueCashFlow",
    "MoveCashFlow", "RecurringCashFlowSchedule", "ReplaceCashFlow", "SkipCashFlow",
    "WithdrawalCashFlow",
]

DEFAULT_OVERRIDES = ()
_DEPOSIT, _WITHDRAWAL, _ADJUSTMENT = pin_members(
    "cashflows.py's emitted kinds", ("deposit", "withdrawal", "adjustment"),
    CASH_FLOW_KINDS, exact=True,
)
_BASE_ID_TAG = "recurring-cash-flow-v1"
_OVERRIDE_ID_TAG = "cash-flow-override-v1"


def _aware(value, name):
    """Require one timezone-aware datetime."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")


def _utc(value, name):
    """Normalize one aware datetime to UTC."""
    _aware(value, name)
    return value.astimezone(utc_timezone.utc)


def _plain_instant(value):
    """Whether ``value`` is a plain ``datetime`` whose zone answers without a hook."""
    if type(value) is not datetime:
        return False
    zone = type(value.tzinfo)
    return zone is ZoneInfo or zone is utc_timezone


def _amount(value, name, *, positive=False):
    """Return one exact finite amount, optionally requiring positivity."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{name} must be a positive finite Decimal")
    return value


def _identity(value, name):
    """Require one non-empty caller-owned identity."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty str")


def _valid_local(value, zone, name):
    """Refuse a named zone's ambiguous fold or nonexistent gap."""
    _aware(value, name)
    local = value.astimezone(zone)
    naive = local.replace(tzinfo=None)
    candidates = []
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        returned = candidate.astimezone(utc_timezone.utc).astimezone(zone)
        if returned.replace(tzinfo=None) == naive and returned.fold == fold:
            candidates.append(candidate)
    if not candidates:
        raise ValueError(f"{name} is nonexistent in {zone.key}")
    if len({candidate.utcoffset() for candidate in candidates}) > 1:
        raise ValueError(f"{name} is ambiguous in {zone.key}")
    if _utc(local, name) != _utc(candidates[0], name):
        raise ValueError(f"{name} does not identify its normalized local instant")


def _base_id(schedule_id, occurrence):
    """Return one stable base-occurrence identity."""
    return canonical_hash(
        [_BASE_ID_TAG, schedule_id, occurrence.astimezone(utc_timezone.utc).isoformat()]
    )


def _override_id(schedule_id, override_id, effective_at):
    """Return one stable standalone-override identity."""
    return canonical_hash([
        _OVERRIDE_ID_TAG, schedule_id, override_id,
        effective_at.astimezone(utc_timezone.utc).isoformat(),
    ])


@dataclass(frozen=True)
class DueCashFlow:
    """One immutable cash movement declaration, never a settlement.

    Examples
    --------
    ::

        flow = DueCashFlow("one", datetime(2026, 1, 2, tzinfo=utc_timezone.utc),
                           "USD", Decimal("5"), "deposit")
        flow.amount
        # -> Decimal('5')
    """

    flow_id: str
    effective_at: datetime
    currency: str
    amount: Decimal
    flow_kind: str
    supersedes: str | None = None

    def __post_init__(self):
        """Validate one immutable due declaration."""
        _identity(self.flow_id, "flow_id")
        _aware(self.effective_at, "effective_at")
        _identity(self.currency, "currency")
        _amount(self.amount, "amount")
        if self.flow_kind not in CASH_FLOW_KINDS:
            raise ValueError(f"flow_kind must be one of {list(CASH_FLOW_KINDS)}")
        if self.supersedes is not None:
            _identity(self.supersedes, "supersedes")
        if self.flow_kind == _DEPOSIT and self.amount <= 0:
            raise ValueError("a deposit amount must be positive")
        if self.flow_kind == _WITHDRAWAL and self.amount >= 0:
            raise ValueError("a withdrawal amount must be negative")
        if (self.flow_kind == _ADJUSTMENT) != (self.supersedes is not None):
            raise ValueError("exactly an adjustment must name supersedes")


class CashFlowOverride(ABC):
    """A polymorphic dated change to recurring or standalone cash.

    Examples
    --------
    ::

        class Keep(CashFlowOverride):
            def apply(self, occurrence):
                return occurrence

        Keep()
    """

    @abstractmethod
    def apply(self, occurrence):
        """Return this override's transformation of one base occurrence."""

    def _standalone(self, schedule_id, currency):
        """Return standalone flows supplied by this override; normally none."""
        return ()

    def _validate(self, schedule):
        """Validate schedule-relative facts unavailable to the value alone."""
        return None

    def _target(self):
        """Return the base occurrence this override targets, or None."""
        return None

    def _landing(self):
        """Return the instant a flow this override targets holds after ``apply``."""
        return self._target()


@dataclass(frozen=True)
class SkipCashFlow(CashFlowOverride):
    """Suppress one base recurrence occurrence.

    Parameters
    ----------
    override_id : str
        Stable identity of the exception.
    occurrence_at : datetime
        Aware base occurrence to suppress.

    Examples
    --------
    ::

        SkipCashFlow("skip", datetime(2026, 1, 2, tzinfo=utc_timezone.utc))
    """

    override_id: str
    occurrence_at: datetime

    def __post_init__(self):
        """Validate this immutable dated override."""
        _identity(self.override_id, "override_id")
        _aware(self.occurrence_at, "occurrence_at")

    def apply(self, occurrence):
        """Suppress the occurrence when its instant matches."""
        if _utc(occurrence.effective_at, "occurrence") == _utc(self.occurrence_at, "target"):
            return None
        return occurrence

    def _target(self):
        return self.occurrence_at


@dataclass(frozen=True)
class MoveCashFlow(CashFlowOverride):
    """Move one recurrence while preserving its base identity.

    Parameters
    ----------
    override_id : str
        Stable identity of the exception.
    occurrence_at, effective_at : datetime
        Aware original and replacement instants.

    Examples
    --------
    ::

        MoveCashFlow("move", original, replacement)
    """

    override_id: str
    occurrence_at: datetime
    effective_at: datetime

    def __post_init__(self):
        """Validate this immutable dated override."""
        _identity(self.override_id, "override_id")
        _aware(self.occurrence_at, "occurrence_at")
        _aware(self.effective_at, "effective_at")

    def apply(self, occurrence):
        """Transform the occurrence only when its instant matches."""
        if _utc(occurrence.effective_at, "occurrence") != _utc(self.occurrence_at, "target"):
            return occurrence
        return replace(occurrence, effective_at=self.effective_at)

    def _target(self):
        return self.occurrence_at

    def _landing(self):
        return self.effective_at


@dataclass(frozen=True)
class ReplaceCashFlow(CashFlowOverride):
    """Replace one recurrence amount while preserving its identity.

    Parameters
    ----------
    override_id : str
        Stable identity of the exception.
    occurrence_at : datetime
        Aware base occurrence to replace.
    amount : Decimal
        Positive replacement amount.

    Examples
    --------
    ::

        ReplaceCashFlow("replace", occurrence, Decimal("725"))
    """

    override_id: str
    occurrence_at: datetime
    amount: Decimal

    def __post_init__(self):
        """Validate this immutable dated override."""
        _identity(self.override_id, "override_id")
        _aware(self.occurrence_at, "occurrence_at")
        _amount(self.amount, "amount", positive=True)

    def apply(self, occurrence):
        """Transform the occurrence only when its instant matches."""
        if _utc(occurrence.effective_at, "occurrence") != _utc(self.occurrence_at, "target"):
            return occurrence
        return replace(occurrence, amount=self.amount)

    def _target(self):
        return self.occurrence_at


@dataclass(frozen=True)
class WithdrawalCashFlow(CashFlowOverride):
    """Add one standalone withdrawal; the declared amount is positive.

    Parameters
    ----------
    override_id : str
        Stable identity of the exception.
    effective_at : datetime
        Aware withdrawal instant.
    amount : Decimal
        Positive magnitude; materialization signs it negative.

    Examples
    --------
    ::

        WithdrawalCashFlow("withdraw", instant, Decimal("25"))
    """

    override_id: str
    effective_at: datetime
    amount: Decimal

    def __post_init__(self):
        """Validate this immutable dated override."""
        _identity(self.override_id, "override_id")
        _aware(self.effective_at, "effective_at")
        _amount(self.amount, "amount", positive=True)

    def apply(self, occurrence):
        """Leave a recurring occurrence unchanged."""
        return occurrence

    def _standalone(self, schedule_id, currency):
        return (DueCashFlow(
            _override_id(schedule_id, self.override_id, self.effective_at),
            self.effective_at, currency, -self.amount, _WITHDRAWAL,
        ),)


@dataclass(frozen=True)
class CorrectCashFlow(CashFlowOverride):
    """Add one adjustment replacing a prior flow value.

    Parameters
    ----------
    override_id : str
        Stable identity of the exception.
    effective_at : datetime
        Aware correction instant.
    supersedes_flow_id : str
        Prior schedule flow identity.
    amount : Decimal
        Exact corrected value.

    Examples
    --------
    ::

        CorrectCashFlow("correct", instant, "prior", Decimal("450"))
    """

    override_id: str
    effective_at: datetime
    supersedes_flow_id: str
    amount: Decimal

    def __post_init__(self):
        """Validate this immutable dated override."""
        _identity(self.override_id, "override_id")
        _aware(self.effective_at, "effective_at")
        _identity(self.supersedes_flow_id, "supersedes_flow_id")
        _amount(self.amount, "amount")

    def apply(self, occurrence):
        """Leave a recurring occurrence unchanged."""
        return occurrence

    def _standalone(self, schedule_id, currency):
        return (DueCashFlow(
            _override_id(schedule_id, self.override_id, self.effective_at),
            self.effective_at, currency, self.amount, _ADJUSTMENT,
            self.supersedes_flow_id,
        ),)

    def _validate(self, schedule):
        """Refuse a correction whose target cannot have been booked earlier."""
        if self.supersedes_flow_id not in schedule._flow_ids_before(self.effective_at):
            raise ValueError(
                f"correction {self.override_id!r} must supersede an earlier flow"
            )


_APPROVED_OVERRIDE_TYPES = (
    SkipCashFlow,
    MoveCashFlow,
    ReplaceCashFlow,
    WithdrawalCashFlow,
    CorrectCashFlow,
)
#: The types the target index understands: off its target each one's
#: ``apply`` returns the flow unchanged, and only a move changes its instant.
#: Any other type, approved or not, sends a schedule down the tuple walk.
_INDEXED_OVERRIDE_TYPES = (
    SkipCashFlow,
    MoveCashFlow,
    ReplaceCashFlow,
    WithdrawalCashFlow,
    CorrectCashFlow,
)


@dataclass(frozen=True)
class RecurringCashFlowSchedule:
    """An anchored local-calendar recurrence for replay declarations.

    Parameters
    ----------
    schedule_id : str
        Stable identity used to derive flow ids.
    anchor : datetime
        First occurrence in ``timezone``; placeholders are permitted.
    interval_days : int
        Positive local-calendar interval.
    currency : str
        Currency of every emitted flow.
    amount : Decimal
        Positive recurring amount.
    timezone : ZoneInfo
        Named recurrence zone.
    overrides : tuple
        Immutable dated override values.

    Examples
    --------
    ::

        zone = ZoneInfo("UTC")
        schedule = RecurringCashFlowSchedule(
            "example", datetime(2031, 4, 9, 13, 17, tzinfo=zone), 13,
            "XYZ", Decimal("37"), zone,
        )
        len(schedule.materialize(schedule.anchor, schedule.anchor + timedelta(days=1)))
        # -> 1
    """

    schedule_id: str
    anchor: datetime
    interval_days: int
    currency: str
    amount: Decimal
    timezone: ZoneInfo
    overrides: tuple = DEFAULT_OVERRIDES

    def __post_init__(self):
        """Validate the schedule and every override identity."""
        _identity(self.schedule_id, "schedule_id")
        if not isinstance(self.timezone, ZoneInfo):
            raise ValueError("timezone must be a zoneinfo.ZoneInfo")
        _aware(self.anchor, "anchor")
        if not isinstance(self.anchor.tzinfo, ZoneInfo) or self.anchor.tzinfo.key != self.timezone.key:
            raise ValueError("anchor must use timezone")
        _valid_local(self.anchor, self.timezone, "anchor")
        if (isinstance(self.interval_days, bool) or not isinstance(self.interval_days, int)
                or self.interval_days < 1):
            raise ValueError("interval_days must be a positive int")
        _identity(self.currency, "currency")
        _amount(self.amount, "amount", positive=True)
        if not isinstance(self.overrides, tuple):
            raise ValueError("overrides must be a tuple of approved concrete CashFlowOverride values")
        object.__setattr__(self, "overrides", tuple(self.overrides))
        if not all(
            type(override) in _APPROVED_OVERRIDE_TYPES
            for override in self.overrides
        ):
            raise ValueError(
                "overrides must be a tuple of approved concrete CashFlowOverride values"
            )
        identities = [override.override_id for override in self.overrides]
        if len(identities) != len(set(identities)):
            raise ValueError("override_id values must be unique within a schedule")
        targets = [
            _utc(target, "override occurrence_at")
            for target in (override._target() for override in self.overrides)
            if target is not None
        ]
        if len(targets) != len(set(targets)):
            raise ValueError("only one override may target the same occurrence")
        for override in self.overrides:
            target = override._target()
            if target is not None and not self._is_occurrence(target):
                raise ValueError(f"override {override.override_id!r} targets no occurrence")
            override._validate(self)
        self._standalone_flows()

    def materialize(self, start, end_exclusive):
        """Return declarations in the normalized half-open instant window."""
        start_utc = _utc(start, "start")
        end_utc = _utc(end_exclusive, "end_exclusive")
        if start_utc >= end_utc:
            raise ValueError("start must precede end_exclusive")
        flows = list(self._recurrences(end_utc))
        flows.extend(self._standalone_flows())
        selected = [flow for flow in flows
                    if start_utc <= _utc(flow.effective_at, "effective_at") < end_utc]
        selected.sort(key=lambda flow: (_utc(flow.effective_at, "effective_at"), flow.flow_id))
        identities = [flow.flow_id for flow in selected]
        if len(identities) != len(set(identities)):
            raise ValueError("materialization produced duplicate flow_id values")
        return tuple(selected)

    def _recurrences(self, end_utc):
        targets = [_utc(target, "override occurrence_at")
                   for target in (override._target() for override in self.overrides)
                   if target is not None]
        last_target = max(targets, default=None)
        # The override index serves the exact class only: a subclass keeps
        # the tuple walk, private steps and all.
        positions = (self._target_positions() if type(self) is RecurringCashFlowSchedule
                     else None)
        flows = []
        index = 0
        while True:
            occurrence = self._occurrence(index)
            occurrence_utc = _utc(occurrence, "occurrence")
            if occurrence_utc >= end_utc and (last_target is None or occurrence_utc > last_target):
                break
            due = DueCashFlow(
                _base_id(self.schedule_id, occurrence), occurrence,
                self.currency, self.amount, _DEPOSIT,
            )
            if positions is None:
                for override in self.overrides:
                    if due is None:
                        break
                    due = override.apply(due)
            elif positions:
                due = self._apply_targeted(due, positions)
            if due is not None:
                flows.append(self._check_emitted(due))
            index += 1
        return tuple(flows)

    def _target_positions(self):
        """Map each targeted override's UTC instant to its tuple position, or None to walk."""
        # The index may replace the walk only where the walk calls no hook and
        # refuses nothing sooner: index-aware types, plain instants and zone
        # (every apply it passes by compares only those), unique targets, and
        # move destinations that reach UTC (the index converts one right
        # after the move; the walk may never). It trusts the shipped methods
        # of those exact types and this module's helpers: a class-level patch
        # of either, like an object.__setattr__ on a frozen value, is unguarded.
        if type(self.timezone) is not ZoneInfo:
            return None
        positions, targeted = {}, 0
        for position, override in enumerate(self.overrides):
            if not any(type(override) is known for known in _INDEXED_OVERRIDE_TYPES):
                return None
            target = override._target()
            if target is None:
                continue
            moved = override._landing()
            if not (_plain_instant(target) and _plain_instant(moved)):
                return None
            try:
                _utc(moved, "occurrence")
            except (OverflowError, ValueError):
                return None
            positions[_utc(target, "override occurrence_at")] = position
            targeted += 1
        return positions if len(positions) == targeted else None

    def _apply_targeted(self, due, positions):
        """Apply, in tuple order, only the overrides whose target ``due`` reaches."""
        # Equal to walking the whole tuple: an override whose target is not
        # due's instant returns due unchanged, and a move can only reach the
        # one LATER override targeting its new instant.
        position = -1
        while due is not None:
            found = positions.get(_utc(due.effective_at, "occurrence"))
            if found is None or found <= position:
                break
            position = found
            due = self.overrides[found].apply(due)
        return due

    def _standalone_flows(self):
        """Return freshly validated, concrete standalone declarations."""
        flows = []
        for override in self.overrides:
            flows.extend(
                self._check_emitted(flow)
                for flow in override._standalone(self.schedule_id, self.currency)
            )
        return tuple(flows)

    def _occurrence(self, index):
        day = self.anchor.date() + timedelta(days=index * self.interval_days)
        occurrence = datetime(
            day.year, day.month, day.day, self.anchor.hour, self.anchor.minute,
            self.anchor.second, self.anchor.microsecond, tzinfo=self.timezone,
        )
        _valid_local(occurrence, self.timezone, "occurrence")
        return occurrence

    def _is_occurrence(self, value):
        local = value.astimezone(self.timezone)
        days = (local.date() - self.anchor.date()).days
        if days < 0 or days % self.interval_days:
            return False
        return _utc(local, "override occurrence_at") == _utc(
            self._occurrence(days // self.interval_days), "occurrence"
        )

    def _flow_ids_before(self, instant):
        """Return ids this schedule can emit strictly before ``instant``."""
        end_utc = _utc(instant, "correction effective_at")
        flows = list(self._recurrences(end_utc))
        flows.extend(self._standalone_flows())
        return frozenset(
            flow.flow_id
            for flow in flows
            if _utc(flow.effective_at, "effective_at") < end_utc
        )

    def _check_emitted(self, flow):
        """Validate and seal one declaration emitted by an override."""
        if type(flow) is not DueCashFlow:
            raise ValueError(f"override emitted {flow!r}, not a concrete DueCashFlow")
        flow = DueCashFlow(
            flow.flow_id,
            flow.effective_at,
            flow.currency,
            flow.amount,
            flow.flow_kind,
            flow.supersedes,
        )
        if isinstance(flow.effective_at.tzinfo, ZoneInfo):
            _valid_local(flow.effective_at, flow.effective_at.tzinfo, "effective_at")
        return flow
