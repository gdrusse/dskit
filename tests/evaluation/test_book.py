"""The book against hand-computed P&L: fees, cash flows, partial closes, shorts."""

from fractions import Fraction

import pytest

from dskit.evaluation.book import EvaluationBook
from dskit.evaluation.events import EventLog
from dskit.pipeline.stats import max_drawdown
from tests.evaluation.conftest import DAY1, DAY2, MIN, Builder


@pytest.fixture
def book(scenario):
    return EvaluationBook(EventLog(scenario))


def test_round_trips_match_the_hand_values(book):
    trips = [(t.instrument, t.direction, t.qty, t.gross_pnl, t.fees, t.pnl)
             for t in book.round_trips]
    assert trips == [
        ("AAA", "long", 4.0, pytest.approx(3.8), pytest.approx(0.9), pytest.approx(2.9)),
        ("AAA", "long", 6.0, pytest.approx(11.1), pytest.approx(1.1), pytest.approx(10.0)),
        ("BBB", "short", 5.0, pytest.approx(4.5), pytest.approx(0.5), pytest.approx(4.0)),
    ]


def test_round_trips_carry_the_decisions_that_opened_and_closed_them(book):
    first = book.round_trips[0]
    assert (first.entry_decision_id, first.entry_reason) == ("d1", "edge_above_threshold")
    assert (first.exit_decision_id, first.exit_reason) == ("d2", "take_profit")
    assert first.holding_ms == MIN
    assert book.round_trips[1].holding_ms == 6 * MIN


def test_final_equity_cash_gross_and_fees_are_exact(book):
    last = book.points[-1]
    assert last.equity == Fraction("9516.9")
    assert last.cash == last.equity  # flat: cash is everything
    assert last.external == 9500
    assert last.trading_pnl == Fraction("16.9")
    assert last.gross_equity - last.external == Fraction("19.4")
    assert last.fees == Fraction("2.5")


def test_the_flat_book_agrees_with_windowbook_realised(book):
    assert book.open_lots() == []
    assert sum(Fraction(str(t.pnl)) for t in book.round_trips) == book.realised


def test_mid_trip_equity_marks_the_open_quantity(book):
    by_ts = {p.ts_ms: p for p in book.points}
    after_buy = by_ts[DAY1]  # deposit, marks and the buy share one instant
    assert after_buy.equity == Fraction("9999")  # 10 000 - fee 1, marked at the fill price
    assert after_buy.cash == Fraction("8998.5")
    assert after_buy.gross_notional == Fraction("1000.5")
    marked = by_ts[DAY1 + MIN]  # 6 left at avg 100.05 marked 101 after the partial close
    assert marked.unrealised == Fraction("5.7")
    assert marked.realised == Fraction("2.3")  # -1 entry fee + 3.8 - 0.5
    assert marked.equity == marked.cash + 6 * Fraction(101)
    assert marked.exposure == pytest.approx(606 / 10008)


def test_cash_flows_move_cash_and_equity_but_not_pnl(book):
    by_ts = {p.ts_ms: p for p in book.points}
    before, after = by_ts[DAY1 + MIN], by_ts[DAY1 + 5 * MIN]
    assert after.equity - before.equity == -500
    assert after.trading_pnl == before.trading_pnl


def test_short_positions_have_negative_net_notional(book):
    by_ts = {p.ts_ms: p for p in book.points}
    assert by_ts[DAY2].net_notional == -250
    assert by_ts[DAY2].gross_notional == 250


def test_twr_excludes_the_deposit_and_withdrawal(book):
    day1 = Fraction(10008, 10000) * Fraction("9512.9") / Fraction("9508")
    day2 = Fraction("9516.9") / Fraction("9512.9")
    assert book.time_weighted_return() == pytest.approx(float(day1 * day2 - 1))
    rets = [row.ret for row in book.days()]
    assert rets == [pytest.approx(float(day1 - 1)), pytest.approx(float(day2 - 1))]


def test_days_split_pnl_fees_and_flows_by_local_session(book):
    rows = [(r.day, r.pnl, r.gross_pnl, r.fees, r.flows, r.fills, r.trips) for r in book.days()]
    assert rows == [
        ("2026-09-01", pytest.approx(12.9), pytest.approx(14.9), 2.0, 9500.0, 3, 2),
        ("2026-09-02", pytest.approx(4.0), pytest.approx(4.5), 0.5, 0.0, 2, 1),
    ]


def test_drawdown_reuses_pipeline_stats_and_agrees_with_the_series(book):
    assert book.max_drawdown() == max_drawdown(book.pnl_increments())
    series = book.drawdowns()["series"]
    assert max(d for _, d in series) == pytest.approx(book.max_drawdown())
    assert book.max_drawdown() == pytest.approx(1.0)  # the entry fee from the zero baseline


def test_a_flip_through_zero_closes_then_opens():
    b = Builder()
    b.add("run_start", 0, run_id="r", tz="UTC")
    b.add("cashflow", 1, amount=1000)
    b.add("fill", 2, "A", fill_id="f1", side="buy", qty=2, price=10.0, fee=0.2)
    b.add("fill", 3, "A", fill_id="f2", side="sell", qty=5, price=12.0, fee=0.5)
    book = EvaluationBook(EventLog(b.events))
    (trip,) = book.round_trips
    assert trip.qty == 2.0 and trip.pnl == pytest.approx(2 * 2 - 0.2 - 0.2)
    (lot,) = book.open_lots()
    assert (lot["direction"], lot["qty"], lot["entry_fees"]) == ("short", 3.0,
                                                                pytest.approx(0.3))
    assert book.role_of_fill == {"f1": "entry", "f2": "exit"}


def test_twr_is_undefined_without_capital():
    b = Builder()
    b.add("run_start", 0, run_id="r", tz="UTC")
    b.add("mark", 1, "A", price=1.0)
    assert EvaluationBook(EventLog(b.events)).time_weighted_return() is None
