"""MarketRecord envelope shape rules + the accounting split arithmetic."""

import pytest

from dskit.pipeline import (
    BinaryAccounting,
    MarketRecord,
    MarkToMarketAccounting,
    PositionOutcome,
    settle_position,
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
