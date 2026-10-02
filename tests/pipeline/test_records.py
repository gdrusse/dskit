"""MarketRecord envelope shape rules + the accounting split arithmetic."""

from datetime import date, datetime, timedelta

import pytest

from dskit.pipeline import (
    BinaryAccounting,
    MarketRecord,
    MarkToMarketAccounting,
    PositionOutcome,
    settle_position,
)
from dskit.pipeline import records
from dskit.pipeline.records import (
    CLUSTER_FIELD,
    CONTRACT_FIELD,
    WEEKDAY_TAGS,
    cluster_of,
    cluster_ok,
    weekday_flags,
)


def _rec(**overrides):
    base = dict(
        venue="markets",
        instrument="SER",
        contract="SER-EV1-C1",
        asof_ms=1_000,
        usable=True,
        reason="ok",
    )
    base.update(overrides)
    return MarketRecord(**base)


class TestMarketRecordShape:
    def test_minimal_and_full_construct(self):
        assert _rec().cluster == "SER-EV1-C1"  # no group -> contract clusters
        full = _rec(group="SER-EV1", bid=0.4, ask=0.45, mid=0.42, lead_frac=0.05)
        assert full.cluster == "SER-EV1"
        assert not full.crossed

    def test_crossed_is_a_state_not_an_error(self):
        assert _rec(bid=0.6, ask=0.5).crossed
        assert not _rec(bid=0.5).crossed  # one-sided book: not crossed

    def test_identity_fields_required(self):
        for field in ("venue", "instrument", "contract", "reason"):
            with pytest.raises(ValueError, match=field):
                _rec(**{field: ""})

    def test_asof_and_usable_shapes(self):
        with pytest.raises(ValueError, match="asof_ms"):
            _rec(asof_ms=1.5)
        with pytest.raises(ValueError, match="asof_ms"):
            _rec(asof_ms=-1)
        with pytest.raises(ValueError, match="asof_ms"):
            _rec(asof_ms=True)  # bool is not an instant
        with pytest.raises(ValueError, match="usable"):
            _rec(usable=1)

    def test_price_domains(self):
        with pytest.raises(ValueError, match="bid"):
            _rec(bid=0.0)
        with pytest.raises(ValueError, match="ask"):
            _rec(ask=float("inf"))
        with pytest.raises(ValueError, match="mid"):
            _rec(mid=True)
        assert _rec(mid=142.50).mid == 142.50  # equities: no (0,1) bound

    def test_lead_frac_domain(self):
        with pytest.raises(ValueError, match="lead_frac"):
            _rec(lead_frac=1.0)
        with pytest.raises(ValueError, match="lead_frac"):
            _rec(lead_frac="soon")
        assert _rec(lead_frac=0.05).lead_frac == 0.05

    def test_group_optional_but_non_empty(self):
        with pytest.raises(ValueError, match="group"):
            _rec(group="")

    def test_native_rides_verbatim(self):
        native = object()
        assert _rec(native=native).native is native


class TestTheClusterIdentityIsOneRule:
    """``cluster_of`` is the ONE place the group-or-contract fallback is
    written. Three modules used to spell it — the envelope's property,
    the numpy pack's carried fields and the fitted family's split frame
    — and the day one of them read a different field, the fit silently
    changed which rows it saw."""

    def test_the_envelopes_property_and_the_function_answer_alike(self):
        assert cluster_of(_rec()) == _rec().cluster == "SER-EV1-C1"
        grouped = _rec(group="SER-EV1")
        assert cluster_of(grouped) == grouped.cluster == "SER-EV1"

    def test_a_dict_row_is_read_by_the_fields_it_actually_carries(self):
        """The vocabulary the toolkit EMITS: ``ArrayFeatures`` carries
        the cluster under ``CLUSTER_FIELD`` and the id under
        ``CONTRACT_FIELD`` — there is no ``cluster`` key on a feature
        row, only the envelope's property of that name."""
        assert cluster_of({CLUSTER_FIELD: "EV-1", CONTRACT_FIELD: "C-1"}) == "EV-1"
        assert cluster_of({CONTRACT_FIELD: "C-1"}) == "C-1"
        assert cluster_of({"cluster": "EV-9", CLUSTER_FIELD: "EV-1"}) == "EV-9"

    @pytest.mark.parametrize("value", ["", None, 0, 7, True, ("a",)])
    def test_only_an_identity_THE_ENVELOPE_CAN_HOLD_counts(self, value):
        """``cluster_ok`` is the bar, not presence: an empty string
        hashes exactly like a missing one, so a caller testing
        ``is not None`` would bucket a whole stream together."""
        assert not cluster_ok(value)
        assert cluster_of({CLUSTER_FIELD: value, CONTRACT_FIELD: value}) is None

    def test_an_unusable_group_falls_back_the_way_the_envelope_would(self):
        assert cluster_of({CLUSTER_FIELD: 3, CONTRACT_FIELD: "C-1"}) == "C-1"


class TestAccountingSplit:
    def test_binary_maps_bool_to_dollar(self):
        acc = BinaryAccounting()
        assert acc.payout_per_unit(True) == 1.0
        assert acc.payout_per_unit(False) == 0.0

    def test_binary_refuses_marks(self):
        with pytest.raises(ValueError, match="mark-to-market venue"):
            BinaryAccounting().payout_per_unit(0.97)

    def test_mtm_maps_mark_to_itself(self):
        acc = MarkToMarketAccounting()
        assert acc.payout_per_unit(142.5) == 142.5
        assert acc.payout_per_unit(0) == 0.0  # bankruptcy is a real mark

    def test_mtm_refuses_bools_and_bad_marks(self):
        acc = MarkToMarketAccounting()
        with pytest.raises(ValueError, match="binary venue"):
            acc.payout_per_unit(True)  # bool is an int — must still refuse
        with pytest.raises(ValueError, match="finite"):
            acc.payout_per_unit(float("nan"))
        with pytest.raises(ValueError, match="finite"):
            acc.payout_per_unit(-1.0)


class TestSharedSettlementArithmetic:
    def test_binary_win_loss(self):
        win = settle_position("C", qty=10, cost=4.2, fee=0.07, payout_per_unit=1.0)
        assert win.proceeds == 10.0
        assert win.pnl == pytest.approx(10.0 - 4.2 - 0.07)
        loss = settle_position("C", qty=10, cost=4.2, fee=0.07, payout_per_unit=0.0)
        assert loss.pnl == pytest.approx(-4.27)

    def test_mtm_long_and_short(self):
        long = settle_position(
            "AAPL", qty=5, cost=700.0, fee=1.0, payout_per_unit=145.0
        )
        assert long.pnl == pytest.approx(5 * 145.0 - 700.0 - 1.0)
        short = settle_position(
            "AAPL", qty=-5, cost=-700.0, fee=1.0, payout_per_unit=145.0
        )
        assert short.pnl == pytest.approx(-5 * 145.0 + 700.0 - 1.0)

    def test_inconsistent_outcome_cannot_exist(self):
        with pytest.raises(ValueError, match="proceeds"):
            PositionOutcome(
                contract="C",
                qty=1,
                cost=0.5,
                fee=0.0,
                payout_per_unit=1.0,
                proceeds=2.0,
                pnl=1.5,
            )
        with pytest.raises(ValueError, match="pnl"):
            PositionOutcome(
                contract="C",
                qty=1,
                cost=0.5,
                fee=0.0,
                payout_per_unit=1.0,
                proceeds=1.0,
                pnl=0.75,
            )

    def test_edge_domains(self):
        with pytest.raises(ValueError, match="non-zero"):
            settle_position("C", qty=0, cost=0.0, fee=0.0, payout_per_unit=1.0)
        with pytest.raises(ValueError, match="fee"):
            settle_position("C", qty=1, cost=0.5, fee=-0.01, payout_per_unit=1.0)
        with pytest.raises(ValueError, match="payout_per_unit"):
            settle_position("C", qty=1, cost=0.5, fee=0.0, payout_per_unit=-1.0)
        with pytest.raises(ValueError, match="finite"):
            settle_position("C", qty=1, cost=float("nan"), fee=0.0, payout_per_unit=1.0)
        with pytest.raises(ValueError, match="number"):
            settle_position("C", qty=True, cost=0.5, fee=0.0, payout_per_unit=1.0)


MON_FRI = ("mon", "tue", "wed", "thu", "fri")


class TestWeekdayFlags:
    """The one owner of "which weekday column does this date light" (ADR-0214)."""

    def test_the_vocabulary_is_seven_tags_monday_first(self):
        assert WEEKDAY_TAGS == ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

    @pytest.mark.parametrize(
        "day, expected",
        [
            # Anchors are calendar facts, not asked of the rule under test:
            # 2026-01-05 was a Monday, 2000-01-01 a Saturday, 2024-02-29 a Thursday.
            (date(2026, 1, 5), (1, 0, 0, 0, 0, 0, 0)),
            (date(2026, 1, 9), (0, 0, 0, 0, 1, 0, 0)),
            (date(2000, 1, 1), (0, 0, 0, 0, 0, 1, 0)),
            (date(2026, 1, 11), (0, 0, 0, 0, 0, 0, 1)),
            (date(2024, 2, 29), (0, 0, 0, 1, 0, 0, 0)),
        ],
    )
    def test_each_day_lights_exactly_its_own_column(self, day, expected):
        assert weekday_flags(day, WEEKDAY_TAGS, ()) == expected

    def test_a_week_walks_the_one_hot_diagonal(self):
        monday = date(2026, 1, 5)
        rows = [weekday_flags(monday + timedelta(days=i), WEEKDAY_TAGS, ()) for i in range(7)]
        assert rows == [tuple(int(i == j) for j in range(7)) for i in range(7)]

    def test_the_result_is_a_tuple_of_plain_ints(self):
        flags = weekday_flags(date(2026, 1, 5), MON_FRI, ())
        assert isinstance(flags, tuple)
        assert all(type(flag) is int for flag in flags)

    def test_columns_follow_the_declared_order_not_the_calendar(self):
        friday = date(2026, 1, 9)
        assert weekday_flags(friday, ("fri", "mon"), ("tue", "wed", "thu", "sat", "sun")) == (1, 0)
        assert weekday_flags(friday, ("mon", "fri"), ("tue", "wed", "thu", "sat", "sun")) == (0, 1)

    @pytest.mark.parametrize("day", [date(2026, 1, 10), date(2026, 1, 11)])
    def test_a_baseline_weekday_is_all_zero(self, day):
        assert weekday_flags(day, MON_FRI, ("sat", "sun")) == (0, 0, 0, 0, 0)

    @pytest.mark.parametrize(
        "day, weekdays, baseline, named",
        [
            (date(2026, 1, 10), MON_FRI, (), "sat"),  # Saturday, no baseline
            (date(2026, 1, 10), MON_FRI, ("sun",), "sat"),  # Saturday, wrong baseline
            (date(2026, 1, 6), ("mon",), (), "tue"),  # a single column
            (date(2026, 1, 11), ("sat",), ("mon",), "sun"),
        ],
    )
    def test_a_weekday_in_neither_set_raises_naming_it(self, day, weekdays, baseline, named):
        with pytest.raises(ValueError, match=named):
            weekday_flags(day, weekdays, baseline)

    def test_a_datetime_is_refused_not_truncated(self):
        for moment in (datetime(2026, 1, 5), datetime(2026, 1, 5, 23, 59, 59)):
            with pytest.raises(TypeError, match="datetime"):
                weekday_flags(moment, MON_FRI, ())

    @pytest.mark.parametrize("value", ["2026-01-05", None, 20260105, 1.5, b"2026-01-05"])
    def test_anything_but_a_date_is_refused(self, value):
        with pytest.raises(TypeError, match="date"):
            weekday_flags(value, MON_FRI, ())

    @pytest.mark.parametrize(
        "weekdays, baseline", [(("mon", "funday"), ()), (("mon",), ("xyz",)), ("mon", ())]
    )
    def test_an_unknown_tag_is_refused_rather_than_read_as_all_zero(self, weekdays, baseline):
        with pytest.raises(ValueError, match="unknown"):
            weekday_flags(date(2026, 1, 5), weekdays, baseline)

    def test_the_public_surface_names_the_rule_and_leaks_nothing_private(self):
        assert {"WEEKDAY_TAGS", "weekday_flags"} <= set(records.__all__)
        assert [name for name in records.__all__ if name.startswith("_")] == []
