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

    def _target(self):
        """Return the base occurrence this override targets, or None."""
        return None


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

        zone = ZoneInfo("America/New_York")
        schedule = RecurringCashFlowSchedule(
            "demo", datetime(2026, 1, 2, 9, 30, tzinfo=zone), 14,
            "USD", Decimal("500"), zone,
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
        if not isinstance(self.overrides, tuple) or not all(
            isinstance(override, CashFlowOverride) for override in self.overrides
        ):
            raise ValueError("overrides must be a tuple of CashFlowOverride values")
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
            for flow in override._standalone(self.schedule_id, self.currency):
                self._check_emitted(flow)

    def materialize(self, start, end_exclusive):
        """Return declarations in the normalized half-open instant window."""
        start_utc = _utc(start, "start")
        end_utc = _utc(end_exclusive, "end_exclusive")
        if start_utc >= end_utc:
            raise ValueError("start must precede end_exclusive")
        flows = list(self._recurrences(end_utc))
        for override in self.overrides:
            flows.extend(override._standalone(self.schedule_id, self.currency))
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
            for override in self.overrides:
                if due is None:
                    break
                due = override.apply(due)
            if due is not None:
                self._check_emitted(due)
                flows.append(due)
            index += 1
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

    def _check_emitted(self, flow):
        if not isinstance(flow, DueCashFlow):
            raise ValueError(f"override emitted {flow!r}, not a DueCashFlow")
        if isinstance(flow.effective_at.tzinfo, ZoneInfo):
            _valid_local(flow.effective_at, flow.effective_at.tzinfo, "effective_at")
