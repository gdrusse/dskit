"""Recurring cash-flow schedule contracts start from deterministic replay values."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone as utc_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from dskit.production.cashflows import (
    CorrectCashFlow,
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
