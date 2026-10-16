"""Recurring cash-flow schedule contracts start from deterministic replay values."""

from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import datetime, timedelta, timezone as utc_timezone, tzinfo
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from dskit.production import cashflows
from dskit.production.cashflows import (
    CashFlowOverride,
    CorrectCashFlow,
    DueCashFlow,
    MoveCashFlow,
    RecurringCashFlowSchedule,
    ReplaceCashFlow,
    SkipCashFlow,
    WithdrawalCashFlow,
)


def test_a_schedule_materializes_its_placeholder_anchor_once():
    """A schedule emits its explicitly supplied first replay declaration."""
    timezone = ZoneInfo("America/New_York")
    anchor = datetime(2026, 1, 6, 13, 17, tzinfo=timezone)
    schedule = RecurringCashFlowSchedule(
        "test-schedule",
        anchor,
        13,
        "XYZ",
        Decimal("37"),
        timezone,
    )

    flows = schedule.materialize(
        anchor,
        anchor + timedelta(days=1),
    )

    assert len(flows) == 1
    assert flows[0].effective_at == anchor
    assert flows[0].currency == "XYZ"
    assert flows[0].amount == Decimal("37")
    assert flows[0].flow_kind == "deposit"


def test_a_skip_override_suppresses_its_base_occurrence():
    """A dated skip removes only the matching placeholder recurrence."""
    timezone = ZoneInfo("America/New_York")
    anchor = datetime(2026, 1, 6, 13, 17, tzinfo=timezone)
    schedule = RecurringCashFlowSchedule(
        "test-schedule",
        anchor,
        13,
        "XYZ",
        Decimal("37"),
        timezone,
        overrides=(SkipCashFlow("skip-anchor", anchor),),
    )

    flows = schedule.materialize(anchor, anchor + timedelta(days=14))

    assert [flow.effective_at for flow in flows] == [anchor + timedelta(days=13)]




def test_move_and_replace_transform_only_the_named_occurrences():
    """Occurrence overrides preserve recurrence identity while changing due values."""
    timezone = ZoneInfo("America/New_York")
    anchor = datetime(2026, 1, 6, 13, 17, tzinfo=timezone)
    second = datetime(2026, 1, 19, 13, 17, tzinfo=timezone)
    moved = datetime(2026, 1, 20, 16, 3, tzinfo=timezone)
    schedule = RecurringCashFlowSchedule(
        "test-schedule", anchor, 13, "XYZ", Decimal("37"), timezone,
        overrides=(
            ReplaceCashFlow("replace-anchor", anchor, Decimal("53")),
            MoveCashFlow("move-second", second, moved),
        ),
    )

    original = RecurringCashFlowSchedule(
        "test-schedule", anchor, 13, "XYZ", Decimal("37"), timezone
    ).materialize(anchor, moved + timedelta(days=1))
    flows = schedule.materialize(anchor, moved + timedelta(days=1))

    assert [(flow.effective_at, flow.amount) for flow in flows] == [
        (anchor, Decimal("53")),
        (moved, Decimal("37")),
    ]
    assert [flow.flow_id for flow in flows] == [flow.flow_id for flow in original]


def test_withdrawal_and_correction_are_standalone_signed_flows():
    """Standalone overrides materialize without pretending to be recurrences."""
    timezone = ZoneInfo("America/New_York")
    anchor = datetime(2026, 1, 6, 13, 17, tzinfo=timezone)
    prior = RecurringCashFlowSchedule(
        "test-schedule", anchor, 13, "XYZ", Decimal("37"), timezone
    ).materialize(anchor, anchor + timedelta(days=1))[0]
    withdrawal_at = datetime(2026, 1, 7, 12, 0, tzinfo=timezone)
    correction_at = datetime(2026, 1, 8, 12, 0, tzinfo=timezone)
    schedule = RecurringCashFlowSchedule(
        "test-schedule", anchor, 13, "XYZ", Decimal("37"), timezone,
        overrides=(
            WithdrawalCashFlow("one-withdrawal", withdrawal_at, Decimal("11")),
            CorrectCashFlow("correct-anchor", correction_at, prior.flow_id, Decimal("41")),
        ),
    )

    flows = schedule.materialize(anchor, correction_at + timedelta(seconds=1))

    assert [(flow.effective_at, flow.amount, flow.flow_kind) for flow in flows] == [
        (anchor, Decimal("37"), "deposit"),
        (withdrawal_at, Decimal("-11"), "withdrawal"),
        (correction_at, Decimal("41"), "adjustment"),
    ]
    assert flows[-1].supersedes == prior.flow_id


def test_recurrence_keeps_local_wall_time_across_est_and_edt():
    """Local-calendar steps keep wall time across an offset transition."""
    timezone = ZoneInfo("America/New_York")
    anchor = datetime(2026, 2, 27, 13, 17, tzinfo=timezone)
    schedule = RecurringCashFlowSchedule(
        "test-schedule", anchor, 13, "XYZ", Decimal("37"), timezone
    )

    flows = schedule.materialize(anchor, datetime(2026, 3, 28, tzinfo=timezone))

    assert [flow.effective_at.hour for flow in flows] == [13, 13, 13]
    assert [flow.effective_at.utcoffset() for flow in flows] == [
        timedelta(hours=-5), timedelta(hours=-4), timedelta(hours=-4),
    ]
    assert flows[1].effective_at.astimezone(utc_timezone.utc).hour == 17


def test_window_is_half_open_after_normalizing_boundaries_to_utc():
    """The start is included and the end is excluded independent of boundary zone."""
    timezone = ZoneInfo("America/New_York")
    anchor = datetime(2026, 3, 13, 13, 17, tzinfo=timezone)
    second = datetime(2026, 3, 26, 13, 17, tzinfo=timezone)
    schedule = RecurringCashFlowSchedule(
        "test-schedule", anchor, 13, "XYZ", Decimal("37"), timezone
    )

    flows = schedule.materialize(
        anchor.astimezone(utc_timezone.utc), second.astimezone(utc_timezone.utc)
    )

    assert [flow.effective_at for flow in flows] == [anchor]


def test_restarting_a_window_reuses_ids_and_orders_equal_instants_by_id():
    """Repeated materialization is idempotent and flow-to-flow ordering is stable."""
    timezone = ZoneInfo("America/New_York")
    anchor = datetime(2026, 1, 6, 13, 17, tzinfo=timezone)
    schedule = RecurringCashFlowSchedule(
        "test-schedule", anchor, 13, "XYZ", Decimal("37"), timezone,
        overrides=(
            WithdrawalCashFlow("z-withdrawal", anchor, Decimal("10")),
            WithdrawalCashFlow("a-withdrawal", anchor, Decimal("25")),
        ),
    )

    first = schedule.materialize(anchor, anchor + timedelta(seconds=1))
    restarted = schedule.materialize(anchor, anchor + timedelta(seconds=1))

    assert restarted == first
    assert len({flow.flow_id for flow in first}) == len(first)
    assert [flow.flow_id for flow in first] == sorted(flow.flow_id for flow in first)


@pytest.mark.parametrize(
    "anchor",
    [
        datetime(2026, 3, 8, 2, 30, tzinfo=ZoneInfo("America/New_York")),
        datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York")),
    ],
)
def test_nonexistent_or_ambiguous_local_anchor_refuses(anchor):
    """A named zone gap or fold is never silently guessed."""
    with pytest.raises(ValueError, match="ambiguous|nonexistent"):
        RecurringCashFlowSchedule(
            "test-schedule", anchor, 13, "XYZ", Decimal("37"),
            ZoneInfo("America/New_York"),
        )


def test_schedule_and_override_values_are_frozen():
    """A caller cannot mutate an identity-bearing schedule after materialization."""
    timezone = ZoneInfo("America/New_York")
    anchor = datetime(2026, 1, 6, 13, 17, tzinfo=timezone)
    schedule = RecurringCashFlowSchedule(
        "test-schedule", anchor, 13, "XYZ", Decimal("37"), timezone
    )
    override = SkipCashFlow("skip-anchor", anchor)

    with pytest.raises(FrozenInstanceError):
        schedule.amount = Decimal("999")
    with pytest.raises(FrozenInstanceError):
        override.override_id = "changed"



def test_two_occurrence_overrides_for_one_date_refuse():
    """One recurrence cannot depend on override tuple order."""
    timezone = ZoneInfo("America/New_York")
    anchor = datetime(2026, 1, 6, 13, 17, tzinfo=timezone)

    with pytest.raises(ValueError, match="one override|same occurrence"):
        RecurringCashFlowSchedule(
            "test-schedule", anchor, 13, "XYZ", Decimal("37"), timezone,
            overrides=(
                MoveCashFlow("move", anchor, anchor + timedelta(hours=1)),
                ReplaceCashFlow("replace", anchor, Decimal("59")),
            ),
        )



def test_placeholder_initial_cash_replaces_only_the_first_occurrence():
    """The initial balance is one flow; later recurrences keep the base amount."""
    timezone = ZoneInfo("America/New_York")
    anchor = datetime(2026, 1, 6, 13, 17, tzinfo=timezone)
    schedule = RecurringCashFlowSchedule(
        "test-schedule", anchor, 13, "XYZ", Decimal("37"), timezone,
        overrides=(
            ReplaceCashFlow("placeholder-initial", anchor, Decimal("91")),
        ),
    )

    flows = schedule.materialize(anchor, anchor + timedelta(days=14))

    assert [flow.amount for flow in flows] == [Decimal("91"), Decimal("37")]
    assert len({flow.flow_id for flow in flows}) == 2


def test_a_correction_for_an_unbooked_schedule_flow_refuses_early():
    """A schedule rejects arbitrary supersedes ids before replay reaches the fold."""
    timezone = ZoneInfo("America/New_York")
    anchor = datetime(2026, 1, 6, 13, 17, tzinfo=timezone)

    with pytest.raises(ValueError, match="earlier flow"):
        RecurringCashFlowSchedule(
            "test-schedule", anchor, 13, "XYZ", Decimal("37"), timezone,
            overrides=(
                CorrectCashFlow(
                    "correction", anchor + timedelta(days=1), "arbitrary", Decimal("41")
                ),
            ),
        )


def test_a_correction_cannot_predate_its_schedule_target():
    """A correction must follow the schedule flow it supersedes."""
    timezone = ZoneInfo("UTC")
    anchor = datetime(2031, 4, 9, 13, 17, tzinfo=timezone)
    prior = RecurringCashFlowSchedule(
        "test-schedule", anchor, 13, "XYZ", Decimal("37"), timezone
    ).materialize(anchor, anchor + timedelta(days=1))[0]

    with pytest.raises(ValueError, match="earlier flow"):
        RecurringCashFlowSchedule(
            "test-schedule", anchor, 13, "XYZ", Decimal("37"), timezone,
            overrides=(
                CorrectCashFlow(
                    "backdated", anchor - timedelta(days=1), prior.flow_id, Decimal("41")
                ),
            ),
        )


def test_an_unapproved_override_cannot_mint_standalone_replay_cash():
    """A user-defined override cannot inject an arbitrary declaration."""
    timezone = ZoneInfo("UTC")
    anchor = datetime(2031, 4, 9, 13, 17, tzinfo=timezone)

    class ForgedOverride(CashFlowOverride):
        override_id = "forged"

        def apply(self, occurrence):
            return occurrence

        def _standalone(self, schedule_id, currency):
            return (
                DueCashFlow(
                    "forged",
                    anchor,
                    "XYZ",
                    Decimal("9999999"),
                    "deposit",
                ),
            )

    with pytest.raises(ValueError, match="approved concrete CashFlowOverride"):
        RecurringCashFlowSchedule(
            "test-schedule",
            anchor,
            13,
            "XYZ",
            Decimal("37"),
            timezone,
            overrides=(ForgedOverride(),),
        )


def test_a_later_gap_occurrence_refuses_before_an_unconvertible_move_destination():
    """The DST-gap refusal wins over a move whose destination cannot reach UTC."""
    timezone = ZoneInfo("America/New_York")
    first = datetime(2026, 3, 5, 2, 30, tzinfo=timezone)
    year_one = datetime(1, 1, 1, tzinfo=utc_timezone(timedelta(hours=5)))
    schedule = RecurringCashFlowSchedule(
        "test-schedule", first, 1, "XYZ", Decimal("37"), timezone,
        overrides=(MoveCashFlow("move", first, year_one),),
    )
    with pytest.raises(ValueError, match="nonexistent in America/New_York"):
        schedule.materialize(
            datetime(2026, 3, 5, tzinfo=utc_timezone.utc),
            datetime(2026, 3, 20, tzinfo=utc_timezone.utc),
        )


# ---------------------------------------------------------------------------
# Pins for the target-indexed walk: same flows, same hooks, any window
# ---------------------------------------------------------------------------

_ZONE = ZoneInfo("UTC")
_ANCHOR = datetime(2026, 1, 1, 9, tzinfo=_ZONE)
_hooks = [0]


def _hook():
    _hooks[0] += 1


class _Str(str):
    def __len__(self):
        _hook()
        return str.__len__(self)


class _Amount(Decimal):
    def is_finite(self):
        _hook()
        return Decimal.is_finite(self)


class _Zone(ZoneInfo):
    def utcoffset(self, when):
        _hook()
        return ZoneInfo.utcoffset(self, when)

    def fromutc(self, when):
        _hook()
        return ZoneInfo.fromutc(self, when)


class _Instant(datetime):
    def __eq__(self, other):
        _hook()
        return datetime.__eq__(self, other)

    __hash__ = datetime.__hash__


class _Offset(tzinfo):
    def utcoffset(self, when):
        _hook()
        return timedelta(0)

    def dst(self, when):
        return timedelta(0)

    def tzname(self, when):
        return "C"


class _ComparedZoneType(type):
    def __eq__(cls, other):
        _hook()
        return type.__eq__(cls, other)

    __hash__ = type.__hash__


class _Fixed(tzinfo, metaclass=_ComparedZoneType):
    def utcoffset(self, when):
        return timedelta(0)

    def dst(self, when):
        return timedelta(0)

    def tzname(self, when):
        return "F"


def _counted(name):
    compare = getattr(datetime, name)

    def hooked(self, other):
        _hook()
        return compare(self, other)
    return hooked


class _Bound(datetime):
    __lt__, __le__, __ge__, __gt__ = (_counted(name) for name in ("__lt__", "__le__", "__ge__", "__gt__"))


def day(n, hours=0):
    return _ANCHOR + timedelta(days=n, hours=hours)


def a_schedule(*overrides, currency="XYZ", amount=Decimal("37"), zone=_ZONE):
    return RecurringCashFlowSchedule(
        "test-schedule", _ANCHOR.replace(tzinfo=zone), 1, currency, amount, zone, overrides=overrides,
    )


def _calls(schedule, start, end):
    _hooks[0] = 0
    schedule.materialize(start, end)
    return _hooks[0]


def test_an_override_target_outside_the_window_still_reaches_it_and_nothing_else_does():
    """A window result equals the wide result filtered to the window, at every edge."""
    schedule = a_schedule(
        MoveCashFlow("into", day(1), day(4, 1)),        # target before start, lands inside
        MoveCashFlow("out", day(3), day(8)),            # target inside, lands after end
        SkipCashFlow("skip", day(5)),
        ReplaceCashFlow("late", day(7), Decimal("5")),  # target after end
        MoveCashFlow("edge", day(9), day(2)),           # lands exactly on the start
    )
    wide = schedule.materialize(day(0), day(15))
    assert [flow.effective_at for flow in wide] == [
        day(0), day(2), day(2), day(4), day(4, 1), day(6), day(7), day(8), day(8),
        day(10), day(11), day(12), day(13), day(14),
    ]
    for start, end in ((day(2), day(6)), (day(2), day(2, 1)), (day(4, 1), day(4, 2)),
                       (day(6), day(9)), (day(5), day(5, 1)), (day(2, 1), day(8))):
        assert schedule.materialize(start, end) == tuple(
            flow for flow in wide if start <= flow.effective_at < end
        )


@pytest.mark.parametrize("first, expected", [(True, ["5", "5"]), (False, ["5", "37"])])
def test_a_move_reaches_a_later_override_on_its_new_instant_but_not_an_earlier_one(first, expected):
    """Overrides apply in tuple order: a later one sees the moved flow, an earlier one does not."""
    move, fix = MoveCashFlow("move", day(2), day(4)), ReplaceCashFlow("fix", day(4), Decimal("5"))
    schedule = a_schedule(*((move, fix) if first else (fix, move)))
    assert sorted(flow.amount for flow in schedule.materialize(day(4), day(4, 1))) == [
        Decimal(amount) for amount in expected
    ]


_LATER = tuple(ReplaceCashFlow(f"later-{n}", day(n), Decimal("5")) for n in (*range(1, 10), *range(12, 20)))
#: variant -> (schedule keywords, extra overrides, the fewest hook calls the walk makes)
_HOOKED = {
    "currency": ({"currency": _Str("XYZ")}, (), 20),
    "amount": ({"amount": _Amount("37")}, (), 20),
    "zone": ({"zone": _Zone("UTC")}, (), None),
    "instant": ({}, (SkipCashFlow("hooked", _Instant.fromtimestamp(day(10).timestamp(), _ZONE)),), 20),
    "target": ({}, (SkipCashFlow("hooked", day(10).replace(tzinfo=_Offset())),), 20),
    "target zone": ({}, (SkipCashFlow("hooked", day(10).replace(tzinfo=_Zone("UTC"))),), 20),
    "move target": ({}, (MoveCashFlow("hooked", day(10).replace(tzinfo=_Offset()), day(11)),), 20),
    # each of the 16 later overrides re-reads the moved flow's instant
    "destination": ({}, (MoveCashFlow("hooked", day(10), day(11).replace(tzinfo=_Offset())), *_LATER), 16),
}


@pytest.mark.parametrize("variant", list(_HOOKED))
def test_a_value_that_can_run_a_hook_pays_the_hook_for_every_walked_occurrence(variant):
    """A hook-bearing value is called as often as the plain walk calls it, whatever the window."""
    kwargs, extra, floor = _HOOKED[variant]
    schedule = a_schedule(*extra, ReplaceCashFlow("last", day(20), Decimal("5")), **kwargs)
    if floor is None:  # a zone is called while locating each occurrence too: building must add more
        _hooks[0] = 0
        for index in range(21):
            schedule._occurrence(index).astimezone(utc_timezone.utc)
        floor = 2 * _hooks[0]
    late, early = _calls(schedule, day(19), day(19, 1)), _calls(schedule, day(5), day(5, 1))
    assert late == early and late >= floor


def test_a_zone_whose_type_can_run_a_hook_is_never_compared():
    """Telling a plain zone from a hook-bearing one runs no metaclass ``__eq__``."""
    schedule = a_schedule(SkipCashFlow("k", day(2).replace(tzinfo=_Fixed())))
    assert _calls(schedule, day(0), day(5)) == 0


@pytest.mark.parametrize("end_hooked, late, early", [(False, 21, 21), (True, 25, 38)])
def test_a_window_bound_that_can_run_a_hook_is_compared_as_often_as_the_plain_walk_does(
        end_hooked, late, early):
    """The comparisons a hook-bearing bound sees are the full walk's, not the skipping walk's."""
    schedule = a_schedule(SkipCashFlow("k", day(10)), ReplaceCashFlow("last", day(20), Decimal("5")))

    def window(start, end):
        bound = (end if end_hooked else start).timestamp()
        hooked = _Bound.fromtimestamp(bound, utc_timezone.utc)
        return (start, hooked) if end_hooked else (hooked, end)

    assert _calls(schedule, *window(day(19), day(19, 1))) == late
    assert _calls(schedule, *window(day(5), day(5, 1))) == early


def test_an_approved_override_type_the_target_index_was_not_taught_still_reaches_every_flow(
        monkeypatch):
    """The index trusts the types it was taught, never the approved list.

    Approving a sixth type fails the first assertion on purpose: decide whether the
    index can skip its ``apply`` off target before teaching it the type.
    """
    five = {SkipCashFlow, MoveCashFlow, ReplaceCashFlow, WithdrawalCashFlow, CorrectCashFlow}
    assert set(cashflows._APPROVED_OVERRIDE_TYPES) == five
    # the index's own list, absent before the index existed, names the same five
    assert set(getattr(cashflows, "_INDEXED_OVERRIDE_TYPES", five)) == five
    due = a_schedule().materialize(day(0), day(1))[0]
    for override in (WithdrawalCashFlow("w", day(3), Decimal("2")),
                     CorrectCashFlow("c", day(3), due.flow_id, Decimal("2"))):
        assert override._target() is None and override.apply(due) is due

    @dataclass(frozen=True)
    class Bump(CashFlowOverride):
        override_id: str

        def apply(self, occurrence):
            return replace(occurrence, amount=occurrence.amount + 1)

    monkeypatch.setattr(
        cashflows, "_APPROVED_OVERRIDE_TYPES", (*cashflows._APPROVED_OVERRIDE_TYPES, Bump)
    )
    schedule = a_schedule(Bump("bump"), SkipCashFlow("skip", day(2)))
    assert [(flow.effective_at, flow.amount) for flow in schedule.materialize(day(1), day(5))] == [
        (day(1), Decimal("38")), (day(3), Decimal("38")), (day(4), Decimal("38")),
    ]


def test_a_schedule_subclass_keeps_every_private_step_of_the_walk():
    """A subclass hooking a private step sees it for every walked occurrence, as before."""
    class Counted(RecurringCashFlowSchedule):
        def _check_emitted(self, flow):
            _hook()
            return super()._check_emitted(flow)

    class Own(RecurringCashFlowSchedule):
        def _recurrences(self, end_utc):
            _hook()
            return ()

    class Watched(RecurringCashFlowSchedule):
        @property
        def overrides(self):
            _hook()
            return vars(self)["_watched"]

        @overrides.setter
        def overrides(self, value):
            vars(self)["_watched"] = value

    def build(cls):
        return cls(
            "test-schedule", _ANCHOR, 1, "XYZ", Decimal("37"), _ZONE,
            overrides=(SkipCashFlow("k", day(10)), ReplaceCashFlow("last", day(20), Decimal("5"))),
        )

    counted = build(Counted)
    assert _calls(counted, day(19), day(19, 1)) == _calls(counted, day(5), day(5, 1)) == 20
    watched = build(Watched)  # once for the targets, once per walked occurrence, once standalone
    assert _calls(watched, day(19), day(19, 1)) == _calls(watched, day(5), day(5, 1)) == 21 + 2
    own = build(Own)
    _hooks[0] = 0
    assert own.materialize(day(0), day(5)) == () and _hooks[0] == 1


_NY, _LONDON = ZoneInfo("America/New_York"), ZoneInfo("Europe/London")
#: 01:30 UTC on 25 Oct, spelled as London's ambiguous wall time; the same instant as _ny_day(24).
_FOLD = datetime(2026, 10, 25, 1, 30, tzinfo=_LONDON, fold=1)
_FOLD_WINDOW = (datetime(2026, 10, 19, tzinfo=utc_timezone.utc), datetime(2026, 10, 27, tzinfo=utc_timezone.utc))


def _ny_day(n):
    return datetime(2026, 10, n, 21, 30, tzinfo=_NY)


def _utc_days(*overrides, window=_FOLD_WINDOW):
    schedule = RecurringCashFlowSchedule("s", _ny_day(1), 1, "XYZ", Decimal("37"), _NY, overrides)
    return [flow.effective_at.astimezone(utc_timezone.utc).day for flow in schedule.materialize(*window)]


def test_a_moved_flow_on_an_ambiguous_wall_time_is_still_skipped_by_a_later_override_on_its_instant():
    """The walk compares instants; the index must look the moved flow up by instant too."""
    moved = _utc_days(MoveCashFlow("m", _ny_day(20), _FOLD), SkipCashFlow("k", _ny_day(24)))
    assert moved == [19, 20, 22, 23, 24, 26]


def test_an_override_whose_target_is_spelled_as_an_ambiguous_wall_time_still_applies():
    """The index keys a target by its UTC instant, not by how the override spelled it."""
    assert _utc_days(SkipCashFlow("k", _FOLD)) == [19, 20, 21, 22, 23, 24, 26]


def test_a_window_start_whose_astimezone_answers_a_hook_zone_is_walked_not_skipped():
    """A start the gate cannot call plain keeps the full walk's zone calls (25), not the skipping walk's."""
    class Counting(tzinfo):
        def utcoffset(self, when):
            _hook()
            return timedelta(0)

        def dst(self, when):
            return timedelta(0)

        def tzname(self, when):
            return "C"

    class Start(datetime):
        def astimezone(self, tz=None):
            plain = datetime(self.year, self.month, self.day, self.hour, self.minute, tzinfo=self.tzinfo)
            moment = datetime.astimezone(plain, tz)
            return datetime(moment.year, moment.month, moment.day, moment.hour, moment.minute,
                            tzinfo=Counting())

    _hooks[0] = 0
    _utc_days(SkipCashFlow("k", _ny_day(22)),
              window=(Start(2026, 10, 19, tzinfo=utc_timezone.utc), _FOLD_WINDOW[1]))
    assert _hooks[0] == 25


def test_an_override_type_that_only_claims_approval_is_walked_on_every_occurrence():
    """The index knows its own types by identity, never by what a type's ``__eq__`` claims."""
    class Claims(type(SkipCashFlow)):
        def __eq__(cls, other):
            return other is SkipCashFlow or type.__eq__(cls, other)

        __hash__ = type.__hash__

    class Lookalike(SkipCashFlow, metaclass=Claims):
        def apply(self, occurrence):
            _hook()
            return super().apply(occurrence)

    assert _calls(a_schedule(Lookalike("k", day(3))), day(0), day(5)) == 5


def test_a_zone_that_can_run_a_hook_is_called_by_every_override_the_walk_passes():
    """Each override the walk passes converts the occurrence through a hook-bearing zone."""
    schedule = a_schedule(*(SkipCashFlow(f"k{n}", day(n)) for n in (2, 4, 6)), zone=_Zone("UTC"))
    assert _calls(schedule, day(0), day(8)) == 241  # the tuple walk's count on origin/main


def test_a_chain_of_moves_skips_an_override_earlier_in_the_tuple_than_the_last_move():
    """Overrides apply in tuple order once: the replace sits before the second move, so it misses the flow."""
    schedule = a_schedule(
        MoveCashFlow("first", day(10), day(7)),
        ReplaceCashFlow("fix", day(5), Decimal("5")),
        MoveCashFlow("second", day(7), day(5)),
    )
    flows = schedule.materialize(day(5), day(5, 1))
    assert sorted(flow.amount for flow in flows) == [Decimal("5"), Decimal("37"), Decimal("37")]


def test_a_move_to_an_instant_utc_cannot_reach_refuses_as_the_plain_walk_does():
    """The destination's conversion error surfaces; the move is never dropped silently."""
    year_one = datetime(1, 1, 1, tzinfo=utc_timezone(timedelta(hours=5)))
    schedule = a_schedule(MoveCashFlow("lost", day(2), year_one))
    with pytest.raises(OverflowError):
        schedule.materialize(day(0), day(6))
