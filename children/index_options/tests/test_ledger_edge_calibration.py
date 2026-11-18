"""The opt-in ``kelly.calibration`` (shrink_by_regime): the regime shrink of the forecast edge, point in time."""

import copy
import math
from types import SimpleNamespace

import pytest

from index_options import ledger_studies
from index_options.ledger_studies import RegimeEdgeShrink
from test_ledger_edge_weights import BASE, ORIGIN_MAIN, _arm, _digest, _expected, _spy, _stable, _study
from test_ledger_multistart import _hedged_world
from test_ledger_studies import FORMULATIONS, _PARITY

CAL = {"method": "shrink_by_regime", "window_weeks": 8, "min_obs": 3,
       "regimes": {"variable": "basket_vol", "edges": [.01, .02]}}
COLD = {**CAL, "min_obs": 100000}


def _cal(kelly=None):
    return RegimeEdgeShrink({"calibration": {**CAL, **(kelly or {})}})


def _book(close, expected, pnl, regime=0, fill=None):
    book = {"close_date": close, "pnl": pnl, "calibration": {"expected": expected, "regime": regime, "week": "x"},
            "legs": [{"quantity": 4, "selected_quantity": 4}]}
    if fill is not None:
        book["legs"] = [{"quantity": int(4*fill), "selected_quantity": 4}]
    return book


# --- declaration -------------------------------------------------------------------------------------------------

def _validate(arm):
    params, _ = _hedged_world()
    params.update(methods=[arm], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_a_well_formed_block_validates_and_composes_with_the_other_knobs():
    _validate(_arm(BASE, {"calibration": CAL}))
    _validate(_arm(BASE, {"calibration": {**CAL, "regimes": {"variable": "basket_vol", "edges": []}}}))
    _validate(_arm(BASE, {"calibration": CAL, "dte_tilt": .5, "name_spread": 2.}))


@pytest.mark.parametrize("block", [
    {**CAL, "method": "other"}, {**CAL, "window_weeks": 0}, {**CAL, "window_weeks": 10**9}, {**CAL, "window_weeks": 1.5}, {**CAL, "min_obs": 1},
    {**CAL, "min_obs": True}, {**CAL, "extra": 1}, {k: v for k, v in CAL.items() if k != "min_obs"},
    {**CAL, "regimes": {"variable": "vix", "edges": []}}, {**CAL, "regimes": {"variable": "basket_vol"}},
    {**CAL, "regimes": {"variable": "basket_vol", "edges": [.02, .01]}},
    {**CAL, "regimes": {"variable": "basket_vol", "edges": [0.]}},
    {**CAL, "regimes": {"variable": "basket_vol", "edges": [float("nan")]}}, "shrink_by_regime"])
def test_a_malformed_block_refuses(block):
    with pytest.raises(ValueError):
        _validate(_arm(BASE, {"calibration": block}))


def test_the_block_refuses_beside_the_accuracy_weight_and_on_a_multistart_method():
    with pytest.raises(ValueError):
        _validate(_arm(BASE, {"calibration": CAL, "accuracy": {"shrink_rows": 60}}))
    with pytest.raises(ValueError):
        _validate(_arm("kelly075_ls_multistart3_hedged_static_lp", {"calibration": CAL}))


# --- key absent / cold: byte for byte ------------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["kelly075_ls_fast_hedged_static_lp:cheap=True"])
def test_a_cold_declared_block_scores_origin_mains_programs_byte_for_byte(key, monkeypatch):
    """No book has settled (min_obs unreachable): lambda is 1, no value_adjust is written, the programs are unchanged."""
    name, cheap = key.split(":cheap=")
    scored, _dollar = _spy(monkeypatch)
    arm = _arm(name, {"calibration": COLD})
    _params, _inputs, study = _study(arm, cheap == "True")
    result = study._method(arm, None)
    assert _digest(scored) == ORIGIN_MAIN[key]["programs"]
    assert all("value_adjust" not in g for p in scored for g in p["groups"])
    week = result["decisions"][0]["kelly"]
    assert week["calibration"]["lambda"] == 1. and week["calibration"]["cold"] and week["calibration"]["books"] == 0
    # the books carry the stamp later weeks read, and nothing else about them moved
    plain = _arm(name)
    _p, _i, base = _study(plain, cheap == "True")
    base_result = base._method(plain, None)
    for got, want in zip(result["decisions"], base_result["decisions"], strict=True):
        assert [{k: v for k, v in b.items() if k != "calibration"} for b in got.get("books", [])] == want.get("books", [])
        assert all(set(b["calibration"]) == {"expected", "regime", "week"} for b in got.get("books", []))


def test_the_undeclared_method_carries_no_calibration_field(monkeypatch):
    arm = _arm(BASE)
    _p, _i, study = _study(arm)
    result = study._method(arm, None)
    assert all("calibration" not in (d.get("kelly") or {}) for d in result["decisions"])
    assert all("calibration" not in b for d in result["decisions"] for b in d.get("books", []))


# --- the shrink math on synthetic history -----------------------------------------------------------------------------

def test_the_factor_is_the_least_squares_scale_of_realised_on_expected_through_the_origin():
    closed = [_book("2019-03-01", 100., 40.), _book("2019-03-04", 200., 90.), _book("2019-03-05", 50., -10.)]
    f = _cal().factors("2019-03-11", closed)[0]
    slope = (100*40+200*90+50*-10)/(100**2+200**2+50**2)
    assert f["n"] == 3 and not f["cold"]
    assert f["slope"] == pytest.approx(slope) and f["lambda"] == pytest.approx(round(slope, 9))
    resid = [y-f["lambda"]*e for e, y in ((100, 40), (200, 90), (50, -10))]
    assert f["nu"] == pytest.approx(math.sqrt(sum(r*r for r in resid)/3)/(350/3))


def test_the_factor_is_bounded_to_zero_and_one():
    over = [_book("2019-03-01", 10., 50.)]*3        # realised above expected: slope 5 -> 1
    under = [_book("2019-03-01", 10., -30.)]*3      # realised below zero: slope -3 -> 0
    assert _cal().factors("2019-03-11", over)[0]["lambda"] == 1.
    assert _cal().factors("2019-03-11", under)[0]["lambda"] == 0.


def test_fewer_than_min_obs_books_is_cold_and_cold_is_face_value_not_zero():
    f = _cal().factors("2019-03-11", [_book("2019-03-01", 10., -30.)]*2)[0]
    assert f["cold"] and f["lambda"] == 1. and f["n"] == 2
    # an empty window is cold too: a method shrunk to 0 re-opens once its evidence ages out
    old = [_book("2019-01-02", 10., -30.)]*5
    assert _cal().factors("2019-03-11", old) == {} and _cal().factors("2019-01-30", old)[0]["lambda"] == 0.


def test_regimes_are_bucketed_by_the_decision_weeks_volatility_and_shrunk_separately():
    cal = _cal()
    assert [cal.regime(v) for v in (.005, .01, .015, .02, .09)] == [0, 1, 1, 2, 2]
    assert RegimeEdgeShrink({"calibration": {**CAL, "regimes": {"variable": "basket_vol", "edges": []}}}).regime(.5) == 0
    closed = ([_book("2019-03-01", 10., 10., regime=0)]*3 + [_book("2019-03-01", 10., 0., regime=1)]*3
              + [_book("2019-03-01", 10., 5., regime=2)]*2)
    f = cal.factors("2019-03-11", closed)
    assert (f[0]["lambda"], f[1]["lambda"], f[2]["cold"], f[2]["lambda"]) == (1., 0., True, 1.)


def test_a_partial_fill_scales_the_expected_profit_by_the_filled_fraction():
    full = _cal().history("2019-03-11", [_book("2019-03-01", 80., 30.)])[0]
    half = _cal().history("2019-03-11", [_book("2019-03-01", 80., 30., fill=.5)])[0]
    assert full == [(80., 30.)] and half == [(40., 30.)]


# --- point in time: nothing at or after the day, nor outside the window, moves the result -------------------------------

def test_poisoned_rows_on_or_after_the_day_or_before_the_window_cannot_move_the_factors():
    good = [_book("2019-03-01", 100., 40.), _book("2019-03-04", 200., 90.), _book("2019-03-05", 50., -10.)]
    day = "2019-03-11"
    baseline = _cal().factors(day, good)
    poison = [_book("2019-03-11", 100., -1e6), _book("2019-03-12", 100., 1e6), _book("2030-01-01", 1., 1e9),
              _book("2019-01-02", 100., 1e6),                       # older than 8 weeks
              {"close_date": "2019-03-06", "pnl": 1e9}]             # no stamp: not a book this method decided
    assert _cal().factors(day, good+poison) == baseline
    assert _cal().factors(day, poison[:4]) == {}
    # and the week's record and value_adjust are identical with and without them
    group = {"id": "g", "credit": [10., 5.], "masses": [.5, .5], "losses": [[4., 1.], [2., 0.]]}
    meta = [{"id": "g"}]
    a = _cal().apply(day, [group], meta, good, .015)
    b = _cal().apply(day, [group], meta, good+poison, .015)
    assert a == b


def test_a_book_settling_on_the_day_is_not_read_but_the_next_day_reads_it():
    book = [_book("2019-03-11", 100., 40.)]*3
    assert _cal().factors("2019-03-11", book) == {}
    assert _cal().factors("2019-03-12", book)[0]["n"] == 3


# --- the shrink in the week ------------------------------------------------------------------------------------------

def test_apply_adds_lambda_minus_one_times_the_weighted_expected_profit_and_leaves_the_group_alone():
    group = {"id": "g", "credit": [10., 5.], "masses": [.5, .5], "losses": [[4., 1.], [2., 0.]]}
    meta = [{"id": "g"}]
    closed = [_book("2019-03-01", 100., 40.)]*3          # slope .4
    out, record, mult = _cal().apply("2019-03-11", [group], meta, closed, .005)
    e = _expected(group)
    assert out[0]["value_adjust"] == pytest.approx([(.4-1.)*x for x in e], abs=1e-12)
    assert {k: v for k, v in out[0].items() if k != "value_adjust"} == group
    assert mult == {"g": .4} and record["lambda"] == .4 and record["regime"] == 0 and not record["cold"]
    assert record["factors"]["0"]["n"] == 3 and record["books"] == 3
    # composed on an edge weight m: the final expected profit is m * lambda * e
    weighted = {**group, "value_adjust": [(.5-1.)*x for x in e]}
    out, _r, mult = _cal().apply("2019-03-11", [weighted], meta, closed, .005, {"g": .5})
    assert [x+a for x, a in zip(e, out[0]["value_adjust"])] == pytest.approx([.5*.4*x for x in e], abs=1e-12)
    assert mult == {"g": .2}
    # a regime with no history is cold: nothing is written
    out, record, mult = _cal().apply("2019-03-11", [group], meta, closed, .05)
    assert out == [group] and mult is None and record["cold"] and record["regime"] == 2


def test_the_shrunk_edge_is_the_value_adjust_of_both_programs_and_the_week_records_the_factors(monkeypatch):
    def factors(self, day, closed):
        return {0: {"n": 9, "slope": .4, "lambda": .4, "nu": .3, "cold": False},
                1: {"n": 5, "slope": .9, "lambda": .9, "nu": .1, "cold": False}}
    monkeypatch.setattr(RegimeEdgeShrink, "factors", factors)
    scored, dollar = _spy(monkeypatch)
    arm = _arm(BASE, {"calibration": {**CAL, "regimes": {"variable": "basket_vol", "edges": []}}})
    _p, _i, study = _study(arm)
    result = study._method(arm, None)
    base_arm = _arm(BASE)
    _p, _i, base_study = _study(base_arm)
    monkeypatch.undo()
    b_scored, b_dollar = _spy(monkeypatch)
    base_study._method(base_arm, None)
    program = next(p for p in reversed(scored) if "utility" in p)
    base_program = next(p for p in reversed(b_scored) if "utility" in p)
    assert dollar and b_dollar
    for shrunk, plain in ((program, base_program), (dollar[0], b_dollar[0])):
        for group, base_group in zip(shrunk["groups"], plain["groups"], strict=True):
            assert _digest({k: v for k, v in group.items() if k != "value_adjust"}) == _digest(base_group)
            assert list(group["value_adjust"]) == pytest.approx([-.6*e for e in _expected(base_group)], abs=1e-12)
    assert _digest(program["utility"]) == _digest(base_program["utility"])
    week = result["decisions"][0]
    rec = week["kelly"]["calibration"]
    assert rec["lambda"] == .4 and rec["regime"] == 0 and set(rec["factors"]) == {"0", "1"}
    assert rec["factors"]["1"]["lambda"] == .9 and rec["books"] == 14
    group_by_id = {g["id"]: g for g in program["groups"]}
    for book in week["books"]:
        assert book["edge_multiplier"] == .4 and book["expected_raw"] != book["expected"]
        assert book["id"] in group_by_id
        assert book["calibration"]["regime"] == 0 and book["calibration"]["week"] == "2019-01-02"


def test_the_stamp_is_the_raw_forecast_expected_profit_of_the_chosen_quantities():
    cal = _cal()
    group = {"id": "g", "credit": [10., 5.], "masses": [.5, .5], "losses": [[4., 1.], [2., 0.]]}
    books = [{"id": "g"}]
    cal.stamp(books, [group], [{"id": "g"}], [[3, 2]], 1, "2019-03-11")
    e = _expected(group)
    assert books[0]["calibration"] == {"expected": pytest.approx(3*e[0]+2*e[1]), "regime": 1, "week": "2019-03-11"}


def test_a_settled_book_carries_its_stamp_into_the_history_the_next_weeks_read():
    arm = _arm(BASE, {"calibration": {**CAL, "min_obs": 2, "regimes": {"variable": "basket_vol", "edges": []}}})
    _p, _i, study = _study(arm)
    result = study._method(arm, None)
    (trade,) = result["trades"]
    cal = RegimeEdgeShrink(arm["kelly"])
    assert trade["calibration"]["week"] == "2019-01-02" and trade["close_date"] == "2019-02-01"
    assert cal.history("2019-02-01", result["trades"]) == {}, "settling on the day: not yet read"
    assert cal.history("2019-02-04", result["trades"]) == {0: [(trade["calibration"]["expected"], trade["pnl"])]}
