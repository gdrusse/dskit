"""The book against hand-computed P&L: fees, cash flows, partial closes, shorts.

Then the fold's contract: it records the account after every folded event,
so whatever can watch it (a subclass, a hooked value, a custom sequence, a
half-built book read back from a traceback) sees exactly that, however the
book shortcuts plain same-instant events; and LocalTime.day is strftime's
text.
"""

import sys
from datetime import datetime, timedelta, timezone, tzinfo
from fractions import Fraction
from unittest.mock import ANY
from zoneinfo import ZoneInfo

import pytest

from dskit.evaluation.book import EvaluationBook
from dskit.evaluation.events import Event, EventLog, LocalTime, Mark
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


# ---------------------------------------------------------------------------
# The fold records the account after every folded event: whatever can watch
# it fold (a subclass, a hooked value, a custom sequence, a half-built book
# read back from a traceback) sees it do exactly that.
# ---------------------------------------------------------------------------


def _same_instant():
    b = Builder()
    b.add("run_start", 0, run_id="r", tz="UTC")
    b.add("cashflow", DAY1, amount=1000)
    b.add("mark", DAY1, "AAA", price=10.0)
    b.add("fill", DAY1, "AAA", fill_id="f1", side="buy", qty=1, price=10.0)
    b.add("mark", DAY1, "AAA", price=11.0)
    b.add("mark", DAY1, "BBB", price=5.0)
    b.add("mark", DAY1 + MIN, "AAA", price=12.0)
    return b.events


def _book_in(frame):
    """The EvaluationBook being built in ``frame`` or a caller of it, or None."""
    while frame is not None and not isinstance(frame.f_locals.get("self"), EvaluationBook):
        frame = frame.f_back
    return None if frame is None else frame.f_locals["self"]


def _half_built(exc):
    """The book a fold raised in, read back through the traceback."""
    tb = exc.__traceback__
    while not isinstance(tb.tb_frame.f_locals.get("self"), EvaluationBook):
        tb = tb.tb_next
    return tb.tb_frame.f_locals["self"]


def test_a_same_instant_fill_and_marks_land_one_point_per_instant():
    class Subclass(EvaluationBook):
        pass

    log = EventLog(_same_instant())
    points = EvaluationBook(log).points
    assert [(p.ts_ms, p.equity, p.cash) for p in points] == [(DAY1, 1001, 990),
                                                               (DAY1 + MIN, 1002, 990)]
    assert Subclass(log).points == points


def test_a_subclass_sees_one_record_per_folded_event():
    calls = []

    class Counting(EvaluationBook):
        def _record(self, ts_ms):
            calls.append(ts_ms)
            super()._record(ts_ms)

    Counting(EventLog(_same_instant()))
    assert calls == [DAY1] * 5 + [DAY1 + MIN]


def test_a_hooked_fill_id_sees_every_earlier_point():
    seen = []

    class Spy(str):
        def __hash__(self):
            book = _book_in(sys._getframe(1))
            if book is not None:
                seen.append(len(book.points))
            return str.__hash__(self)

    events = _same_instant()
    events[3]["fill_id"] = Spy("f1")
    EvaluationBook(EventLog(events))
    assert seen == [1]


@pytest.mark.parametrize("custom", ["sequence", "log"])
def test_a_custom_event_sequence_sees_the_book_after_every_event(custom):
    seen = []

    def peek(events):
        for event in events:
            seen.append(len(sys._getframe(1).f_locals["self"].points))
            yield event

    class PeekingList(list):
        def __iter__(self):
            return peek(list.__iter__(self))

    class PeekingLog(EventLog):
        def __iter__(self):
            return peek(super().__iter__())

    if custom == "log":
        log = PeekingLog(_same_instant())
    else:
        log = EventLog(_same_instant())
        log.links  # built from the plain list first
        log._events = PeekingList(log._events)
    EvaluationBook(log)
    assert seen == [0, 0, 1, 1, 1, 1, 1]


def test_a_mark_that_lost_its_price_fails_with_every_earlier_point_recorded():
    log = EventLog(_same_instant())
    del log.events[4]._body["price"]
    with pytest.raises(TypeError) as caught:
        EvaluationBook(log)
    assert [(p.ts_ms, p.cash) for p in _half_built(caught.value).points] == [(DAY1, 990)]


def test_an_overflowing_fill_meets_the_book_with_every_earlier_point_recorded():
    b = Builder()
    b.add("run_start", 0, run_id="r", tz="UTC")
    b.add("cashflow", DAY1, amount=1e6)
    b.add("fill", DAY1, "AAA", fill_id="f1", side="buy", qty=1e300, price=1.0)
    b.add("mark", DAY1, "BBB", price=5.0)
    b.add("fill", DAY1, "AAA", fill_id="f2", side="sell", qty=1e300, price=1e300)
    with pytest.raises(OverflowError) as caught:
        EvaluationBook(EventLog(b.events))
    assert [(p.ts_ms, p.cash) for p in _half_built(caught.value).points] == [
        (DAY1, 10**6 - 10**300)]


def test_day_is_strftime_for_a_short_year_and_for_a_custom_zone():
    class Counting(tzinfo):
        calls = 0

        def utcoffset(self, dt):
            return timedelta(hours=1)

        def dst(self, dt):
            Counting.calls += 1
            return timedelta(0)

    local = LocalTime("UTC")
    year_999 = int(datetime(999, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
    assert local.day(year_999) == local.at(year_999).strftime("%Y-%m-%d")
    local.zone = Counting()
    text, calls = local.day(DAY1), Counting.calls
    expected = local.at(DAY1).strftime("%Y-%m-%d")
    assert (text, calls) == (expected, Counting.calls - calls)


def test_day_keeps_strftime_for_a_datetime_or_zone_subclass():
    class Stamped(datetime):
        def strftime(self, fmt):
            return "stamped " + fmt

    class Local(LocalTime):
        def at(self, ms):
            return Stamped.fromtimestamp(ms / 1000.0, timezone.utc)

    assert Local("UTC").day(DAY1) == "stamped %Y-%m-%d"
    calls = []

    class Zone(ZoneInfo):
        def dst(self, dt):
            calls.append(dt)
            return super().dst(dt)

    local = LocalTime("UTC")
    local.zone = Zone("America/New_York")
    text, during = local.day(DAY1), len(calls)
    expected = local.at(DAY1).strftime("%Y-%m-%d")
    assert (text, during) == (expected, len(calls) - during)


def test_the_fold_reads_the_fields_the_plain_check_declares(monkeypatch, scenario):
    read, get = set(), Event.get

    def spy(self, name, default=None):
        if self.kind in ("fill", "mark", "cashflow"):
            read.add((self.kind, name))
        return get(self, name, default)

    log = EventLog(scenario)
    monkeypatch.setattr(Event, "get", spy)
    EvaluationBook(log)
    assert read == {("fill", "qty"), ("fill", "price"), ("fill", "fee"), ("fill", "side"),
                    ("fill", "fill_id"), ("fill", "order_id"), ("mark", "price"),
                    ("cashflow", "amount")}
    declared = getattr(EvaluationBook, "_READS", None)  # the plain-event check, where present
    if declared is not None:
        assert {(kind, name) for kind, (numbers, ids) in declared.items()
                for name in (*numbers, *ids)} == read


# Whatever an event carries, a fold shows hooks and errors exactly what the
# event-by-event fold of a subclass shows them.

_SAW = []


def _saw(what):
    """Note what a hook saw of the book being built: its point count and last point."""
    book = _book_in(sys._getframe(2))
    points = None if book is None else vars(book).get("_points")
    _SAW.append((what, None if points is None else (len(points), points[-1:])))


class _Loud(str):
    def __hash__(self):
        _saw("hash")
        return str.__hash__(self)

    def __eq__(self, other):
        _saw("eq")
        return str.__eq__(self, other)


class _Raising(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        raise LookupError(f"compared with {other!r}")


class _LoudNe(int):
    def __ne__(self, other):
        _saw("ne")
        return int.__ne__(self, other)


class _LoudFloat(float):
    def __repr__(self):
        _saw("repr")
        return float.__repr__(self)


class _LoudInt(int):
    @property
    def numerator(self):
        _saw("numerator")
        return int(self)


class _LoudDict(dict):
    def __iter__(self):
        _saw("iter")
        return dict.__iter__(self)

    def __contains__(self, key):
        _saw("contains")
        return dict.__contains__(self, key)

    def get(self, key, default=None):
        _saw("get")
        return dict.get(self, key, default)


class _QuietDict(dict):
    pass


class _LoudMark(Mark):
    def get(self, name, default=None):
        _saw("get")
        return Mark.get(self, name, default)


class _Eager(EvaluationBook):
    """A subclass, so always the event-by-event fold."""


def _rekey(mapping, name, key):
    """A copy of ``mapping`` with ``key`` in place of ``name``."""
    return {key if k == name else k: v for k, v in mapping.items()}


def _hostile_kind_in_a_dict_subclass(event):
    event.__dict__ = _QuietDict({**vars(event), "kind": _Loud("mark")})


def _shadow_get(event):
    event.get = lambda name, default=None: (_saw("get"), Mark.get(event, name, default))[1]


#: Each changes the log below, mostly the BBB mark [4] or the cash flow [5]
#: that arrive while the instant DAY1 is still open.
_WATCHED = {
    "mark subclass": lambda log: log._events.__setitem__(4, _LoudMark(log._events[4].to_obj())),
    "nan price": lambda log: log._events[4]._body.__setitem__("price", float("nan")),
    "infinite price": lambda log: log._events[4]._body.__setitem__("price", float("inf")),
    "int-subclass price": lambda log: log._events[4]._body.__setitem__("price", _LoudInt(5)),
    "absent amount": lambda log: log._events[5]._body.pop("amount"),
    "float-subclass amount": lambda log: log._events[5]._body.__setitem__("amount",
                                                                          _LoudFloat(50.0)),
    "kind shadow": lambda log: setattr(log._events[4], "kind", _Loud("mark")),
    "get shadow": lambda log: _shadow_get(log._events[4]),
    "str-subclass instrument": lambda log: setattr(log._events[4], "instrument", _Loud("BBB")),
    "no instrument": lambda log: delattr(log._events[4], "instrument"),
    "str-subclass body key": lambda log: setattr(log._events[4], "_body", _rekey(
        log._events[4]._body, "price", _Loud("price"))),
    "str-subclass event key": lambda log: setattr(log._events[4], "__dict__", _rekey(
        vars(log._events[4]), "instrument", _Raising("instrument"))),
    "str-subclass kind key": lambda log: setattr(log._events[4], "__dict__", {
        **vars(log._events[4]), _Raising("kind"): "mark"}),
    "dict-subclass body": lambda log: setattr(log._events[4], "_body",
                                              _LoudDict(log._events[4]._body)),
    "dict-subclass event dict": lambda log: setattr(log._events[4], "__dict__",
                                                    _LoudDict(vars(log._events[4]))),
    "dict-subclass log dict": lambda log: setattr(log, "__dict__", _LoudDict(vars(log))),
    "dict-subclass event dict with a hostile kind": lambda log:
        _hostile_kind_in_a_dict_subclass(log._events[4]),
    "instant moved back": lambda log: setattr(log._events[4], "ts_ms", DAY1 - 1),
    "int-subclass instant": lambda log: setattr(log._events[4], "ts_ms", _LoudNe(DAY1)),
}


def _fold(change, book_class):
    """What folding the changed log shows: points, or error and half-built points; and hooks."""
    b = Builder()
    b.add("run_start", 0, run_id="r", tz="UTC")
    b.add("cashflow", DAY1, amount=1000)
    b.add("fill", DAY1, "AAA", fill_id="f1", side="buy", qty=1, price=10.0)
    b.add("mark", DAY1, "AAA", price=11.0)
    b.add("mark", DAY1, "BBB", price=5.0)
    b.add("cashflow", DAY1, amount=50)
    b.add("mark", DAY1 + MIN, "AAA", price=12.0)
    log = EventLog(b.events)
    change(log)
    _SAW.clear()
    try:
        outcome = book_class(log).points
    except Exception as exc:
        outcome = (type(exc), str(exc), vars(_half_built(exc)).get("_points"))
    return outcome, list(_SAW)


@pytest.mark.parametrize("change", _WATCHED.values(), ids=_WATCHED)
def test_a_watched_event_at_an_open_instant_sees_the_event_by_event_fold(change):
    assert _fold(change, EvaluationBook) == _fold(change, _Eager)


def test_a_mark_that_lost_its_price_in_a_log_of_ints_fails_as_event_by_event():
    def fold(book_class):
        b = Builder()
        b.add("run_start", 0, run_id="r", tz="UTC")
        b.add("cashflow", DAY1, amount=1000)
        b.add("mark", DAY1, "AAA", price=10)
        b.add("mark", DAY1, "BBB", price=5)
        log = EventLog(b.events)
        del log._events[3]._body["price"]
        with pytest.raises(TypeError) as caught:
            book_class(log)
        return str(caught.value), vars(_half_built(caught.value)).get("_points")

    assert fold(EvaluationBook) == fold(_Eager)


def test_a_fill_whose_links_move_its_instant_folds_event_by_event():
    fill = []

    class Moving(str):
        def __hash__(self):
            if fill:
                fill.pop().ts_ms = ANY  # public, from inside the fold
            return str.__hash__(self)

    log = EventLog()
    log.emit("run_start", 0, 0, run_id="r", tz="UTC")
    log.emit("cashflow", 0, 0, amount=1000, rule="initial")
    log.emit("decision", 1, 1, decision_id="d1", action="enter", reason="r")
    log.emit("order", 1, 1, "AAA", order_id="o1", decision_id=Moving("d1"), side="buy", qty=1)
    moved = log.emit("fill", 2, 2, "AAA", fill_id="f1", order_id="o1", side="buy", qty=1,
                     price=10)
    log.emit("mark", 3, 3, "AAA", price=11)
    log.links
    fill.append(moved)
    assert [p.ts_ms for p in EvaluationBook(log).points] == [3]


def test_a_plain_log_records_each_instant_once_and_lands_the_eager_points(monkeypatch):
    log = EventLog(_same_instant())
    eager, calls, record = _Eager(log).points, [], EvaluationBook._record

    def counting(self, ts_ms):
        calls.append(ts_ms)
        record(self, ts_ms)

    monkeypatch.setattr(EvaluationBook, "_record", counting)
    assert EvaluationBook(log).points == eager
    if hasattr(EvaluationBook, "_READS"):  # the same-instant shortcut, where present
        assert calls == [DAY1, DAY1, DAY1 + MIN]
