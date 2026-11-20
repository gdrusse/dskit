"""P2 (improvement #4): each open book may be closed at the current bid/ask instead of held to expiry.

The two-week local-search world of ``test_ledger_studies`` with one addition: the week-two forecasts also cover the
expiry week one's book is still open in (2019-02-01), so the held book has a forecast to be valued under.
"""
import copy
import math

import pytest

from index_options import ledger_studies
from index_options.close_candidates import CloseCandidates
from test_ledger_studies import SHOCK10, _local_world

STUDY = ledger_studies.WeeklyOptionPortfolioStudy
OPEN_ID = "2019-01-02:C0N0:2019-02-01"
SPEC = {"enabled": True, "price": "bid_ask", "min_dte": 5}
LOOSE = {"start": {"rho": 0., "shock": {**SHOCK10, "loss_fraction": 1.}}}


def _world(shift=0., crash=False, spec=SPEC):
    """The week-two forecast for the held expiry is the normal shifted down by ``shift`` z; ``crash`` drops C0N0's
    bars from 2019-01-09 (after the close fill, before expiry): the held short put then loses."""
    params, data, method = _local_world(search=LOOSE)
    for pair in params["selected_pairs"]:
        if pair["symbol"] == "C0N0":
            pair["dte_min"] = 24
    one = data["C0N0"]
    forecast = copy.deepcopy(one["forecasts"][0])
    forecast["quote_date"] = "2019-01-07"
    z = [-3+.5*i for i in range(13)]
    for i, v in enumerate(z):
        forecast["method_cdf_"+str(i)] = .5*(1+math.erf((v+shift)/2**.5))
    context = copy.deepcopy(one["contexts"][0])
    context["quote_date"] = "2019-01-07"
    one["forecasts"].append(forecast)
    one["contexts"].append(context)
    if crash:
        for row in one["bars"]:
            if row["date"] >= "2019-01-09":
                row["close"] = 88.
    if spec is not None:
        method["kelly"] = {**method["kelly"], "close_candidates": copy.deepcopy(spec)}
    return params, data, method


def _run(**kw):
    params, data, method = _world(**kw)
    return params, data, method, STUDY(params, data)._method(method, None)


def _book(result, key=OPEN_ID):
    return next(t for t in result["trades"] if t["id"] == key)


# --- the declaration -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"enabled": 1, "price": "bid_ask", "min_dte": 5}, {"enabled": True, "price": "mid", "min_dte": 5},
    {"enabled": True, "price": "bid_ask", "min_dte": 0}, {"enabled": True, "price": "bid_ask"},
    {"enabled": True, "price": "bid_ask", "min_dte": 5, "extra": 1}, None])
def test_a_malformed_declaration_is_refused(bad):
    params, _data, method = _world(spec=None)
    method["kelly"]["close_candidates"] = bad
    with pytest.raises(ValueError, match="invalid fixed method"):
        STUDY.validate_configuration(params)


def test_a_wellformed_declaration_validates_and_disabled_is_absent():
    params, _data, _method = _world()
    STUDY.validate_configuration(params)
    assert CloseCandidates.spec({"close_candidates": {**SPEC, "enabled": False}}) is None
    assert CloseCandidates.spec({}) is None


def test_the_hedge_and_the_other_incompatible_blocks_are_refused_by_name():
    params, _data, method = _world()
    for key in ("hedge", "calibration", "exposure_limits", "sizer"):
        params["methods"] = [{**method, key: {}}]
        with pytest.raises(ValueError, match=f"close_candidates_with_{key}"):
            STUDY.validate_configuration(params)
    for key in ("accuracy", "robust_scale"):
        params["methods"] = [{**method, "kelly": {**method["kelly"], key: {}}}]
        with pytest.raises(ValueError, match="close_candidates_with_edge_weights"):
            STUDY.validate_configuration(params)
    from index_options.ledger_studies import EdgeWeights
    assert set(CloseCandidates.EDGE) == set(EdgeWeights.KEYS), "every edge key is refused beside the closes"
    multi = {**method["kelly"], "local_search": {"multistart": {}, "moves": 1}}
    params["methods"] = [{**method, "kelly": multi}]
    with pytest.raises(ValueError, match="needs_stage_one_local_search"):
        STUDY.validate_configuration(params)


# --- key absent: unchanged -------------------------------------------------------------------------------------

def test_a_disabled_declaration_changes_no_record():
    _p, _d, _m, plain = _run(spec=None)
    _p, _d, _m, off = _run(spec={**SPEC, "enabled": False})
    for key in ("trades", "pnl", "fees", "curve", "final_equity"):
        assert off[key] == plain[key], key
    for first, second in zip(off["decisions"], plain["decisions"]):
        for key in ("status", "selection", "books", "kelly"):   # (certificate seconds are wall clock)
            assert first.get(key) == second.get(key), key
    assert CloseCandidates.KEY not in off and "closes" not in str(plain["decisions"][1]["kelly"].keys())


# --- closing beats holding -------------------------------------------------------------------------------------

def test_a_book_whose_forecast_turned_is_closed_and_the_close_beats_holding():
    _p, _d, _m, held = _run(shift=2.5, crash=True, spec=None)
    _p, _d, _m, closed = _run(shift=2.5, crash=True)
    week = closed["decisions"][1]["kelly"][CloseCandidates.KEY]
    assert week["status"] == "candidates" and [c["id"] for c in week["candidates"]] == [OPEN_ID]
    assert week["candidates"][0]["close_minus_hold"] > 0, "the close beats the forecast's hold value"
    assert week["closing"] == [OPEN_ID] and [f["id"] for f in week["filled"]] == [OPEN_ID] and week["unfilled"] == []
    early = _book(closed)
    assert early["closed_early"] and early["close_date"] == "2019-01-08" and early["close_decision_date"] == "2019-01-07"
    assert "closed_early" not in _book(held) and _book(held)["close_date"] == "2019-02-01"
    assert early["pnl"] > _book(held)["pnl"], "the realized close beats holding through the crash"
    assert closed[CloseCandidates.KEY]["ids"] == [OPEN_ID] and closed[CloseCandidates.KEY]["closed"] == 1
    assert closed["decisions"][1]["kelly"]["local"]["holds_closed"] == 1


def test_a_neutral_forecast_holds_to_expiry():
    _p, _d, _m, result = _run(shift=0.)
    week = result["decisions"][1]["kelly"][CloseCandidates.KEY]
    assert week["candidates"] and week["closing"] == [] and "closed_early" not in _book(result)


def test_below_min_dte_a_book_is_not_a_candidate():
    _p, _d, _m, result = _run(shift=2.5, spec={**SPEC, "min_dte": 40})
    week = result["decisions"][1]["kelly"][CloseCandidates.KEY]
    assert week["candidates"] == [] and week["skipped"] == [{"id": OPEN_ID, "reason": "below_min_dte"}]
    assert "closed_early" not in _book(result)


# --- cash and fees ---------------------------------------------------------------------------------------------

def test_the_close_releases_cash_and_charges_real_quotes_and_fees():
    params, data, _m, closed = _run(shift=2.5, crash=True)
    early = _book(closed)
    rows = {(r["date"], r["strike"]): r for r in data["C0N0"]["chain"]
            if r["expiration"] == "2019-02-01" and r["date"] == "2019-01-08"}
    fee = params["bridge"]["solver"]["fee_per_contract_usd"]
    mult = params["bridge"]["solver"]["multiplier"]
    long_bid, short_ask = rows["2019-01-08", 90.]["bid"], rows["2019-01-08", 100.]["ask"]
    proceeds = long_bid*mult-short_ask*mult-2*fee
    assert early["close_proceeds"] == pytest.approx(proceeds)
    assert early["close_fees"] == pytest.approx(2*fee)
    assert early["pnl"] == pytest.approx(early["credit"]+proceeds)
    assert early["close_fill_prices"] == [long_bid, short_ask]
    plain_fees = sum(t["fees"] for t in closed["trades"])
    assert closed["fees"] == pytest.approx(plain_fees+2*fee), "closing fees join the total"
    assert closed["pnl"] == pytest.approx(sum(t["pnl"] for t in closed["trades"]))
    assert closed["final_equity"] == pytest.approx(closed["contributions"]+closed["pnl"])
    ledger = closed["ledger_summary"]
    assert (ledger.get("early_closed") or ledger)["closed"] == {"exit": 1}, "the ledger replayed the exit"


def test_account_close_early_moves_cash_and_realized_by_the_closing_trade():
    from dskit.pipeline.libs.option_ledger import FundedOptionAccount

    account = FundedOptionAccount(10000., 0., 14, "2019-01-02")
    book = {"id": "b", "symbol": "X", "expiry": "2019-02-01", "reserve": 500., "credit": 100.}
    assert account.admit("2019-01-02", [book], [{"members": ["X"], "upper": 1000.}])
    assert account.cash == 10100.
    account.advance("2019-01-08", {})
    account.close_early("2019-01-08", "b", 40., close_fees=1.3)
    assert account.cash == pytest.approx(10040.) and account.realized == 40. and not account.open
    assert account.closed[0]["closed_early"] and account.funding == 10040.
    with pytest.raises(ValueError):
        account.close_early("2019-01-08", "b", 1.)
    account.admit("2019-01-08", [{**book, "id": "c"}], [{"members": ["X"], "upper": 1000.}])
    with pytest.raises(ValueError, match="reserve"):
        account.close_early("2019-01-08", "c", -600.)
    with pytest.raises(ValueError):
        account.close_early("2019-02-01", "c", 0.)


# --- no look-ahead ---------------------------------------------------------------------------------------------

def test_the_decision_never_reads_the_fill_session_or_later_quotes():
    _p, data, method = _world(shift=2.5, crash=True)
    params = _p
    base = STUDY(params, data)._method(method, None)
    altered = copy.deepcopy(data)
    for row in altered["C0N0"]["chain"]:
        if row["expiration"] == "2019-02-01" and row["date"] >= "2019-01-08":
            row["bid"], row["ask"] = row["bid"]*.95, row["ask"]*1.1
    other = STUDY(params, altered)._method(method, None)
    a, b = base["decisions"][1], other["decisions"][1]
    assert a["kelly"][CloseCandidates.KEY]["candidates"] == b["kelly"][CloseCandidates.KEY]["candidates"]
    assert a["kelly"][CloseCandidates.KEY]["closing"] == b["kelly"][CloseCandidates.KEY]["closing"]
    assert a["selection"] == b["selection"]
    assert _book(base)["close_proceeds"] != _book(other)["close_proceeds"], "the fill DOES price the fill session"
    later = copy.deepcopy(data)
    for row in later["C0N0"]["chain"]:
        if row["expiration"] == "2019-02-01" and row["date"] > "2019-01-08":
            row["bid"], row["ask"] = row["bid"]*.9, row["ask"]*1.2
    assert _book(STUDY(params, later)._method(method, None))["close_proceeds"] == _book(base)["close_proceeds"]
    same_day = copy.deepcopy(data)
    for row in same_day["C0N0"]["chain"]:
        if row["expiration"] == "2019-02-01" and row["date"] == "2019-01-07" and row["strike"] == 100.:
            row["ask"] += 1.
    moved = STUDY(params, same_day)._method(method, None)
    assert (moved["decisions"][1]["kelly"][CloseCandidates.KEY]["candidates"][0]["close_proceeds"]
            < base["decisions"][1]["kelly"][CloseCandidates.KEY]["candidates"][0]["close_proceeds"]), \
        "the decision values the close at the decision date's own ask"


def test_a_close_that_cannot_fill_leaves_the_book_held():
    params, data, method = _world(shift=2.5, crash=True)
    for row in data["C0N0"]["chain"]:
        if row["expiration"] == "2019-02-01" and row["date"] == "2019-01-08" and row["strike"] == 100.:
            row["ask_size"] = 0
    result = STUDY(params, data)._method(method, None)
    week = result["decisions"][1]["kelly"][CloseCandidates.KEY]
    assert week["closing"] == [OPEN_ID] and week["filled"] == []
    assert week["unfilled"] == [{"id": OPEN_ID, "reason": "missing_close_quote"}]
    assert "closed_early" not in _book(result) and result[CloseCandidates.KEY]["closed"] == 0


def test_the_recorded_route_replays_a_closing_run_exactly():
    params, data, method, result = _run(shift=2.5, crash=True)
    assert STUDY(params, data)._method(method, None, recorded=result) == result


def test_a_close_that_would_breach_the_books_reserve_is_refused_not_fatal():
    params, data, method = _world(shift=2.5, crash=True)
    study = STUDY(params, data)
    closer = CloseCandidates(study, SPEC)
    result = study._method(method, None)
    book = {**_book(result), "reserve": 0.}
    fields, reason = closer.fill(book, "2019-01-08")
    assert fields is None and reason == "close_exceeds_reserve"
    book["reserve"] = 1e6
    assert closer.fill(book, "2019-01-08")[0]["pnl"] == pytest.approx(_book(result)["pnl"])
