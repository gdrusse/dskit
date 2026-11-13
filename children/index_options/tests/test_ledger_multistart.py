"""The multi-start Kelly local-search methods in the weekly study (ADR-0315): declaration, week, replay, hedge."""

import copy
import math

import pytest

from index_options import ledger_studies
from test_ledger_studies import (FORMULATIONS, LOCAL_FORMULATIONS, LOCAL_SEARCH, _KELLY_BASE, _PARITY, _REPLAY_BLOCK,
                                 _for_kelly, _overlapping_kelly_world, _program_world, _weekly_portfolio_fixture)

#: ADR-0315's declared search: ADR-0307's budgets (moves raised so a climb ends at its local optimum; restarts 0, the
#: starts replace them) and the multi-start block in place of the stage-one ``start``.
MULTISTART_SEARCH = {"moves": 1000, "adds_evaluated": 64, "swaps_evaluated": 64, "restarts": 0, "seed": 20261112,
                     "multistart": {"top_groups": 16, "starts": 128, "climbed": 8, "pairs_evaluated": 64,
                                    "evaluations": 1000000, "scales": [1., .5, .25, .125]}}


def _multistart(fraction):
    base = LOCAL_FORMULATIONS["kelly050_ls_lp"]
    return {**base, "kelly": {**base["kelly"], "fraction": fraction, "local_search": copy.deepcopy(MULTISTART_SEARCH)}}


MULTISTART_FORMULATIONS = {"kelly050_ls_multistart_lp": _multistart(.5), "kelly075_ls_multistart_lp": _multistart(.75)}
#: The replay-hedge pairing (ADR-0307's 2026-11-12 entry): the same method on a distinct id with the sealed replay hedge.
MULTISTART_HEDGED = {name.removesuffix("_lp")+"_replay_hedge_lp": {**method, "hedge": dict(_REPLAY_BLOCK)}
                     for name, method in MULTISTART_FORMULATIONS.items()}

#: ADR-0315 Amendment 1: the same search with feasible starts (repair, single and unit starts, exact charge
#: estimates) on NEW ids; the ids above are frozen and their declaration is untouched.
MULTISTART2_SEARCH = {**MULTISTART_SEARCH,
                      "multistart": {**MULTISTART_SEARCH["multistart"],
                                     "feasible_starts": {"singles": 32, "units": True, "repair_contracts": 64}}}


def _multistart2(fraction):
    base = _multistart(fraction)
    return {**base, "kelly": {**base["kelly"], "local_search": copy.deepcopy(MULTISTART2_SEARCH)}}


MULTISTART2_FORMULATIONS = {"kelly050_ls_multistart2_lp": _multistart2(.5),
                            "kelly075_ls_multistart2_lp": _multistart2(.75)}
MULTISTART2_HEDGED = {name.removesuffix("_lp")+"_replay_hedge_lp": {**method, "hedge": dict(_REPLAY_BLOCK)}
                      for name, method in MULTISTART2_FORMULATIONS.items()}


def _world(search=None):
    """The overlapping two-week kelly world under a multi-start method."""
    params, data, method = _overlapping_kelly_world()
    declared = {**MULTISTART_SEARCH, **(search or {})}
    method = {"id": "ms", **_KELLY_BASE, "kelly": {**method["kelly"], "fraction": .5, "local_search": declared}}
    params["methods"] = [method]
    return params, data, method


def _result(**kw):
    params, data, method = _world(**kw)
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
    return params, data, method, study, study._method(method, None)


def test_the_multistart_formulations_validate_and_differ_from_the_local_search_only_by_the_start():
    for name, method in MULTISTART_FORMULATIONS.items():
        params, _inputs = _weekly_portfolio_fixture()
        params["methods"] = [{"id": name, **method}]
        _for_kelly(params)
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
        local = LOCAL_FORMULATIONS["kelly050_ls_lp"]
        assert {k: v for k, v in method.items() if k != "kelly"} == {k: v for k, v in local.items() if k != "kelly"}
        assert ({k: v for k, v in method["kelly"].items() if k not in ("local_search", "fraction")}
                == {k: v for k, v in local["kelly"].items() if k not in ("local_search", "fraction")})
        assert set(method["kelly"]["local_search"]) - {"multistart"} == set(LOCAL_SEARCH) - {"start"}
    assert MULTISTART_FORMULATIONS["kelly075_ls_multistart_lp"]["kelly"]["fraction"] == .75


def test_the_hedged_multistart_formulations_validate():
    for name, method in MULTISTART_HEDGED.items():
        params, _inputs = _weekly_portfolio_fixture()
        params["methods"] = [{"id": name, **method}]
        _for_kelly(params)
        params.update(hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params, tail_end_before="2021-07-01")


@pytest.mark.parametrize("change", [
    {"restarts": 1}, {"start": LOCAL_SEARCH["start"]}, {"multistart": None},
    {"multistart": {**MULTISTART_SEARCH["multistart"], "starts": 1}},
    {"multistart": {**MULTISTART_SEARCH["multistart"], "scales": [2.]}},
    {"multistart": {k: v for k, v in MULTISTART_SEARCH["multistart"].items() if k != "climbed"}},
    {"moves": 0}, {"extra": 1}])
def test_a_malformed_multistart_declaration_is_refused(change):
    params, _data, _method = _world(change)
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_the_multistart_refuses_the_keys_that_steer_the_integer_program():
    params, _data, method = _world()
    method["kelly"]["max_groups"] = 10
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_a_multistart_week_solves_no_integer_program_and_records_its_starts(monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    def never(*args, **kwargs):
        pytest.fail("a multi-start week solves no stage-one program")
    monkeypatch.setattr(TransportPortfolioSolve, "run", never)
    _params, _data, _method, _study, result = _result()
    week = result["decisions"][0]
    assert week["status"] == "selected" and week["books"] and "stage1" not in week
    local = week["kelly"]["local"]
    assert local["start_kind"] == "multistart" and local["end_value"] >= local["multistart"]["cash_value"]
    assert set(local["multistart"]) >= {"seed", "starts_digest", "chosen_start", "climbs", "cash_floor"}
    assert local["multistart"]["seed"] == MULTISTART_SEARCH["seed"]
    assert week["selection"]["utility"]["exact"] == pytest.approx(local["end_value"], rel=1e-9, abs=1e-9)
    assert week["kelly"]["solve"] == {"strategy_outcome": "local_search", "bound_source": None}
    certificate, = week["certificates"]
    assert certificate["strategy_outcome"] == "local_search" and certificate["certified"] is False
    assert week["kelly"]["screen"]["rule"] == "lp"
    assert sum(b["contracts"] for b in week["books"]) == local["end_contracts"]


def test_a_multistart_week_is_deterministic_and_the_recorded_route_replays_it_exactly():
    params, data, method, _study, result = _result()
    again = ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None)
    assert again == result
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=result) == result
    for field, value in (("starts_digest", "0"*64), ("chosen_start", 10**6), ("seed", 1)):
        forged = copy.deepcopy(result)
        forged["decisions"][0]["kelly"]["local"]["multistart"][field] = value
        with pytest.raises(ValueError, match="seed, starts digest or chosen start"):
            ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=forged)
    changed = copy.deepcopy(result)
    week = changed["decisions"][0]
    week["selection"]["quantities"] = [[q+1 if q else 0 for q in row] for row in week["selection"]["quantities"]]
    with pytest.raises(ValueError, match="recorded multi-start selection"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=changed)
    forged = copy.deepcopy(result)
    forged["decisions"][0]["kelly"]["local"]["end_value"] += 1.
    with pytest.raises(ValueError, match="differs from authoritative replay"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=forged)


def test_the_derived_route_reruns_the_multistart_and_refuses_actions_it_does_not_reach():
    from dskit.pipeline.libs.pyomo import RecordedCheckRefusal
    from index_options.mio_worker import MIOMethodWorker

    params, data, method, study, result = _result()
    claim = MIOMethodWorker.actions(result, study.weeks)
    assert claim["decisions"] and all("stage1" not in d and len(d["certificates"]) == 1
                                      for d in claim["decisions"].values())
    replay = ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, derived=claim)
    assert [w.get("selection") for w in replay["decisions"]] == [w.get("selection") for w in result["decisions"]]
    forged = copy.deepcopy(claim)
    day = next(iter(forged["decisions"]))
    forged["decisions"][day]["actions"] = [[0]*len(row) for row in forged["decisions"][day]["actions"]]
    with pytest.raises(RecordedCheckRefusal, match="multi-start actions"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, derived=forged)
    extra = copy.deepcopy(claim)
    extra["decisions"][day]["stage1"] = {"actions": [], "certificates": []}
    with pytest.raises(ValueError, match="derived method or decision calendar differs"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, derived=extra)


@pytest.mark.parametrize("name", sorted(MULTISTART_HEDGED))
def test_the_hedged_multistart_arm_trades_the_same_books_and_replays_from_record_and_worker(monkeypatch, name):
    from datetime import date, timedelta

    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve
    from index_options.mio_worker import MIOMethodWorker

    params, inputs = _program_world()
    closes = inputs["X"]["bars"]
    day = date.fromisoformat(closes[0]["date"])
    earlier = []
    while len(earlier) < 62:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            earlier.append({**closes[0], "date": day.isoformat(), "close": 100 + len(earlier) % 3 * .1})
    closes[:0] = reversed(earlier)
    params["clocks"]["history_start"] = closes[0]["date"]
    params["clocks"]["sessions"] = [row["date"] for row in closes]
    params["decision_weeks"] = ["2019-01-02"]
    _for_kelly(params)
    inputs["X"]["chain"] = [row for row in inputs["X"]["chain"]
                            if (row["strike"] == 100 and row["type"] == "call") or row["date"] == "2019-02-01"]
    for row in inputs["X"]["chain"]:
        if row["date"] in ("2019-01-02", "2019-01-03"):
            row["bid"], row["ask"] = .15, .16
    arm = {"id": name, **MULTISTART_HEDGED[name]}
    baseline = {k: v for k, v in arm.items() if k != "hedge"}
    baseline["id"] = name.replace("_replay_hedge_lp", "_lp")
    monkeypatch.setattr(TransportPortfolioSolve, "run", lambda *a, **k: pytest.fail("no stage-one solve"))
    params["methods"] = [baseline]
    plain = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(baseline, None)
    params.update(methods=[arm], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    hedged = study._method(arm, None)
    assert plain["decisions"][0]["books"] == hedged["decisions"][0]["books"]
    assert hedged["decisions"][0]["kelly"]["local"]["start_kind"] == "multistart"
    trade, = hedged["trades"]
    assert trade["hedge"]["status"] == "ok" and trade["hedge"]["units"] != 0
    assert study._method(arm, None, recorded=hedged) == hedged
    claim = MIOMethodWorker.actions(hedged, study.weeks)
    assert "stage1" not in claim["decisions"][study.weeks[0]]
    replay = study._method(arm, None, derived=claim)
    assert replay["trades"] == hedged["trades"]
    assert study._method(arm, None, recorded=replay) == replay


# --- ADR-0315 Amendment 1: the feasible-starts methods on new ids ----------------------------------------------

def test_the_amended_formulations_validate_on_new_ids_and_the_registered_ones_are_unchanged():
    assert MULTISTART_SEARCH == {"moves": 1000, "adds_evaluated": 64, "swaps_evaluated": 64, "restarts": 0,
                                 "seed": 20261112,
                                 "multistart": {"top_groups": 16, "starts": 128, "climbed": 8, "pairs_evaluated": 64,
                                                "evaluations": 1000000, "scales": [1., .5, .25, .125]}}
    assert sorted(MULTISTART_FORMULATIONS) == ["kelly050_ls_multistart_lp", "kelly075_ls_multistart_lp"]
    assert not set(MULTISTART2_FORMULATIONS) & set(MULTISTART_FORMULATIONS)
    assert not set(MULTISTART2_HEDGED) & set(MULTISTART_HEDGED)
    for name, method in MULTISTART2_FORMULATIONS.items():
        old = MULTISTART_FORMULATIONS[name.replace("multistart2", "multistart")]
        search, was = method["kelly"]["local_search"], old["kelly"]["local_search"]
        assert {k: v for k, v in search["multistart"].items() if k != "feasible_starts"} == was["multistart"]
        assert search["multistart"]["feasible_starts"] == {"singles": 32, "units": True, "repair_contracts": 64}
        assert {**method, "kelly": {**method["kelly"], "local_search": was}} == old
        params, _inputs = _weekly_portfolio_fixture()
        params["methods"] = [{"id": name, **method}]
        _for_kelly(params)
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    for name, method in MULTISTART2_HEDGED.items():
        params, _inputs = _weekly_portfolio_fixture()
        params["methods"] = [{"id": name, **method}]
        _for_kelly(params)
        params.update(hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params, tail_end_before="2021-07-01")


@pytest.mark.parametrize("block", [None, {}, {"singles": 32, "units": True},
                                   {"singles": -1, "units": True, "repair_contracts": 64},
                                   {"singles": 32, "units": 1, "repair_contracts": 64},
                                   {"singles": 32, "units": True, "repair_contracts": 0}])
def test_a_malformed_feasible_starts_block_is_refused(block):
    params, _data, _method = _world({"multistart": {**MULTISTART_SEARCH["multistart"], "feasible_starts": block}})
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_an_amended_week_records_its_repairs_is_deterministic_and_replays_from_record_and_worker():
    from dskit.pipeline.libs.pyomo import RecordedCheckRefusal
    from index_options.mio_worker import MIOMethodWorker

    params, data, method, study, result = _result(search=MULTISTART2_SEARCH)
    week = result["decisions"][0]
    multi = week["kelly"]["local"]["multistart"]
    assert multi["declared"] == MULTISTART2_SEARCH["multistart"] and len(multi["start_stats"]) == multi["starts"]
    assert set(multi) >= {"starts_repaired", "starts_emptied", "starts_distinct", "start_kinds", "start_stats"}
    assert week["kelly"]["local"]["end_value"] >= multi["cash_value"] and "stage1" not in week
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None) == result
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=result) == result
    forged = copy.deepcopy(result)
    forged["decisions"][0]["kelly"]["local"]["multistart"]["start_stats"][0]["kept"] += 1
    with pytest.raises(ValueError, match="differs from authoritative replay"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=forged)
    claim = MIOMethodWorker.actions(result, study.weeks)
    replay = ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, derived=claim)
    assert [w.get("selection") for w in replay["decisions"]] == [w.get("selection") for w in result["decisions"]]
    day = next(iter(claim["decisions"]))
    claim["decisions"][day]["actions"] = [[0]*len(row) for row in claim["decisions"][day]["actions"]]
    if any(q for row in week["selection"]["quantities"] for q in row):
        with pytest.raises(RecordedCheckRefusal, match="multi-start actions"):
            ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, derived=claim)
    # The registered declaration on the same world is untouched by the amendment's fields.
    old = _result()[4]["decisions"][0]["kelly"]["local"]["multistart"]
    assert not set(old) & {"starts_repaired", "starts_emptied", "starts_distinct", "start_kinds", "start_stats"}


# --- ADR-0315 Amendment 2: settleable candidates only, on new ids ----------------------------------------------

def _multistart3(fraction):
    base = _multistart2(fraction)
    return {**base, "kelly": {**base["kelly"], "settleable_only": True}}


#: ADR-0315 Amendment 2: the Amendment 1 methods with ``kelly.settleable_only`` on NEW ids; the ids above are frozen.
MULTISTART3_FORMULATIONS = {"kelly050_ls_multistart3_lp": _multistart3(.5),
                            "kelly075_ls_multistart3_lp": _multistart3(.75)}
MULTISTART3_HEDGED = {name.removesuffix("_lp")+"_replay_hedge_lp": {**method, "hedge": dict(_REPLAY_BLOCK)}
                      for name, method in MULTISTART3_FORMULATIONS.items()}


def _settleable_world(settleable, basis=None):
    """The Amendment 1 world, optionally with ``settleable_only`` and a study whose basis series ``basis`` edits."""
    params, data, method = _world(MULTISTART2_SEARCH)
    if settleable:
        method["kelly"]["settleable_only"] = True
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
    if basis is not None:
        basis(study._factors)
    return params, data, method, study


def _changed(name, start="2019-01-15"):
    """A basis edit: ``name``'s factor halves from ``start`` on (after the first week's fill, before its expiry)."""
    def edit(factors):
        for day in factors[name]:
            if day >= start:
                factors[name][day] = .5
    return edit


def _dropped_at(name, day):
    def edit(factors):
        del factors[name][day]
    return edit


def test_the_settleable_formulations_validate_on_new_ids_and_differ_from_amendment_one_only_by_the_key():
    assert not set(MULTISTART3_FORMULATIONS) & (set(MULTISTART2_FORMULATIONS) | set(MULTISTART_FORMULATIONS))
    assert not set(MULTISTART3_HEDGED) & (set(MULTISTART2_HEDGED) | set(MULTISTART_HEDGED))
    for name, method in MULTISTART3_FORMULATIONS.items():
        old = MULTISTART2_FORMULATIONS[name.replace("multistart3", "multistart2")]
        assert "settleable_only" not in old["kelly"] and method["kelly"]["settleable_only"] is True
        assert {**method, "kelly": {k: v for k, v in method["kelly"].items() if k != "settleable_only"}} == old
        params, _inputs = _weekly_portfolio_fixture()
        params["methods"] = [{"id": name, **method}]
        _for_kelly(params)
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    for name, method in MULTISTART3_HEDGED.items():
        params, _inputs = _weekly_portfolio_fixture()
        params["methods"] = [{"id": name, **method}]
        _for_kelly(params)
        params.update(hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params, tail_end_before="2021-07-01")


@pytest.mark.parametrize("value", [False, 1, None, "true"])
def test_a_settleable_only_that_is_not_true_is_refused(value):
    params, _data, method = _world(MULTISTART2_SEARCH)
    method["kelly"]["settleable_only"] = value
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_without_the_key_a_book_whose_basis_changes_before_expiry_is_selected_and_cannot_settle():
    _params, _data, method, study = _settleable_world(False)
    name = study._method(method, None)["decisions"][0]["books"][0]["symbol"]
    _params, _data, method, study = _settleable_world(False, _changed(name))
    with pytest.raises(ValueError, match="unsettleable selected book"):
        study._method(method, None)


@pytest.mark.parametrize("edit", [_changed, lambda name: _changed(name, "2019-02-01"),
                                  lambda name: _dropped_at(name, "2019-02-01")],
                         ids=["change_before_expiry", "change_on_expiry", "no_factor_on_expiry"])
def test_with_the_key_an_unsettleable_group_is_refused_before_the_solve_and_never_selected(edit):
    _params, _data, method, study = _settleable_world(False)
    plain = study._method(method, None)
    name = plain["decisions"][0]["books"][0]["symbol"]
    params, data, method, study = _settleable_world(True, edit(name))
    result = study._method(method, None)
    first = result["decisions"][0]
    assert first["status"] == "selected" and first["books"], "the other names still trade"
    assert all(book["symbol"] != name for week in result["decisions"] for book in week.get("books", []))
    assert all(trade["symbol"] != name for trade in result["trades"])
    assert {"symbol": name, "expiry": "2019-02-01", "reason": "unsettleable_expiry"} in first["refusals"]
    assert first["candidate_books"] == plain["decisions"][0]["candidate_books"]-1
    assert name not in first["eligible_by_symbol"]
    assert all(name not in group for group in first["kelly"]["local"]["multistart"]["lp_groups"])
    # Deterministic, and the record replays on the same basis series.
    again = _settleable_world(True, edit(name))[3]
    assert again._method(method, None) == result
    assert _settleable_world(True, edit(name))[3]._method(method, None, recorded=result) == result


def test_with_the_key_and_nothing_unsettleable_the_week_is_amendment_ones():
    _params, _data, method, study = _settleable_world(False)
    plain = study._method(method, None)
    _params, _data, method, study = _settleable_world(True)
    result = study._method(method, None)
    assert result["method"]["kelly"]["settleable_only"] is True and "settleable_only" not in plain["method"]["kelly"]
    assert {k: v for k, v in result.items() if k != "method"} == {k: v for k, v in plain.items() if k != "method"}


def test_a_pinned_split_keeps_its_candidates_settleable():
    from types import SimpleNamespace
    from datetime import date

    _params, _data, _method, study = _settleable_world(True)
    name = study.params["symbols"][0]
    meta = {"symbol": name, "decision_date": "2019-01-02", "expiry": "2019-02-01", "factor": 1.}
    assert study._settleable(meta)
    _changed(name)(study._factors)
    assert not study._settleable(meta)
    study.adjustments = {name: SimpleNamespace(effective=date(2019, 1, 15), basis_before=1., basis_after=.5)}
    assert study._settleable(meta)
    assert not study._settleable({**meta, "expiry": "2031-01-01"}), "no bar on the expiry"


def test_a_calendar_day_without_a_bar_inside_the_window_is_unsettleable():
    _params, _data, _method, study = _settleable_world(True)
    name = study.params["symbols"][0]
    meta = {"symbol": name, "decision_date": "2019-01-02", "expiry": "2019-02-01", "factor": 1.}
    assert study._settleable(meta)
    day = next(d for d in study.calendar if "2019-01-02" < d < "2019-02-01")
    del study._bars[name][day]
    assert not study._settleable(meta), "the ledger pass indexes every calendar bar in the window"


# --- ADR-0316: the hedge inside the search (static and daily re-struck), on new ids --------------------------------
# Amendment 1 (owner rulings 2026-11-13): hedging is optional contract by contract and the search decides; the daily
# hedge is scored on seeded path scenarios; a re-struck reserve over the cap blocks new admissions.

from test_ledger_studies import FAST_LOCAL_FORMULATIONS, _PROGRAM_BLOCK  # noqa: E402

#: The two hedge kinds of ADR-0316, both ``program`` mode: the fixed entry hedge held to expiry, and the same entry
#: re-struck every session (the owner's defaults: ratio 1.0 when hedged, 252 re-hedge sessions a year).
STATIC_BLOCK = {**_PROGRAM_BLOCK, "iv_source": "vendor_else_mid"}
DYNAMIC_BLOCK = {**STATIC_BLOCK, "kind": "daily_delta_forward", "rehedge_sessions_per_year": 252, "rehedge_paths": 4,
                 "rehedge_seed": 20261113}
_HEDGED_BASES = {**MULTISTART3_FORMULATIONS, **FAST_LOCAL_FORMULATIONS,
                 "kelly050_ls_lp": LOCAL_FORMULATIONS["kelly050_ls_lp"],
                 "kelly075_ls_lp": {**LOCAL_FORMULATIONS["kelly050_ls_lp"],
                                    "kelly": {**LOCAL_FORMULATIONS["kelly050_ls_lp"]["kelly"], "fraction": .75}}}
#: New ids: each base method, identical apart from the block (``<base>_hedged_static_lp``, ``<base>_hedged_dynamic_lp``).
HEDGED_FORMULATIONS = {
    name.removesuffix("_lp")+f"_hedged_{kind}_lp": {**copy.deepcopy(method), "hedge": dict(block_)}
    for name, method in _HEDGED_BASES.items()
    for kind, block_ in (("static", STATIC_BLOCK), ("dynamic", DYNAMIC_BLOCK))}


def _hedged_world(cheap_call=True):
    """The program-hedge world with a kelly history; one decision week, every strike of the 2019-02-01 expiry.

    ``cheap_call`` prices the 100 call far under parity on the decision and fill sessions, which makes the hedged
    synthetic forward (sold 100 put, bought 100 call, sold stock) the best book; without it the chain is fair.
    """
    from datetime import date, timedelta

    params, inputs = _program_world()
    closes = inputs["X"]["bars"]
    day = date.fromisoformat(closes[0]["date"])
    earlier = []
    while len(earlier) < 62:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            earlier.append({**closes[0], "date": day.isoformat(), "close": 100 + len(earlier) % 3 * .1})
    closes[:0] = reversed(earlier)
    params["clocks"]["history_start"] = closes[0]["date"]
    params["clocks"]["sessions"] = [row["date"] for row in closes]
    params["decision_weeks"] = ["2019-01-02"]
    _for_kelly(params)
    for row in inputs["X"]["chain"]:
        if (cheap_call and row["date"] in ("2019-01-02", "2019-01-03") and row["strike"] == 100
                and row["type"] == "call"):
            row["bid"], row["ask"] = .15, .16
    return params, inputs


def _hedged_study(name, world, block=None):
    params, inputs = world
    arm = {"id": name, **copy.deepcopy(HEDGED_FORMULATIONS[name])}
    arm["hedge"].update(block or {})
    params.update(methods=[arm], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    return params, inputs, arm, ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)


def _hedged_run(name, monkeypatch=None, cheap_call=True, block=None):
    params, inputs, arm, study = _hedged_study(name, _hedged_world(cheap_call), block)
    seen = []
    if monkeypatch is not None:
        from dskit.pipeline.libs.pyomo import TransportActionScore
        real = TransportActionScore.__init__

        def spy(scorer, program, *a, **k):
            seen.append(program)
            real(scorer, program, *a, **k)
        monkeypatch.setattr(TransportActionScore, "__init__", spy)
    return params, inputs, arm, study, study._method(arm, None), seen


def _legs(book):
    return [(leg["right"], leg["strike"], leg["sign"], leg["quantity"], leg.get("hedged", False)) for leg in book["legs"]]


def _option_pnl(trade, level):
    return trade["credit"] + 100 * sum(
        leg["sign"] * leg["quantity"] * max((leg["strike"] - level) if leg["right"] == "put" else (level - leg["strike"]), 0.)
        for leg in trade["legs"])


def test_the_hedged_formulations_are_their_bases_plus_the_block_and_validate_on_new_ids():
    assert sorted(HEDGED_FORMULATIONS) == sorted(
        f"kelly{f}_ls{v}_hedged_{k}_lp" for f in ("050", "075") for v in ("", "_fast", "_multistart3")
        for k in ("static", "dynamic"))
    for name, method in HEDGED_FORMULATIONS.items():
        base = name.replace("_hedged_static_lp", "_lp").replace("_hedged_dynamic_lp", "_lp")
        assert {k: v for k, v in method.items() if k != "hedge"} == _HEDGED_BASES[base]
        assert method["hedge"]["mode"] == "program" and method["hedge"]["ratio"] == 1.0
        assert method["hedge"].get("rehedge_sessions_per_year", 252) == 252
        params, _ = _hedged_world()
        params.update(methods=[{"id": name, **method}], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    # a kelly method that is not a local search still refuses a program hedge
    params, _ = _hedged_world()
    kelly = {k: v for k, v in LOCAL_FORMULATIONS["kelly050_ls_lp"]["kelly"].items() if k != "local_search"}
    params.update(methods=[{"id": "plain_kelly_hedged", **LOCAL_FORMULATIONS["kelly050_ls_lp"], "kelly": kelly,
                            "hedge": dict(STATIC_BLOCK)}], hedge_parity=dict(_PARITY))
    with pytest.raises(ValueError, match="hedge_with_sizer"):
        ledger_studies.WeeklyOptionPortfolioStudy._hedge_configuration(params)


@pytest.mark.parametrize("name", ["kelly075_ls_hedged_static_lp", "kelly050_ls_fast_hedged_static_lp"])
def test_the_search_scores_the_hedged_program_and_the_chosen_book_fills_with_its_hedge(monkeypatch, name):
    from dskit.pipeline.option_structures import structure_max_loss

    params, inputs, arm, study, result, seen = _hedged_run(name, monkeypatch)
    week = result["decisions"][0]
    assert week["status"] == "selected" and not result["rejected"]
    program = next(p for p in seen if "utility" in p)            # the one-stage program the search scores
    group, = program["groups"]
    book, = week["books"]
    hedge = book["program_hedge"]
    # The scenario payoff carries the hedge: every joint term is the hedged loss row (-u_i (S - F_0) beside the option).
    groups, metadata, _ = study._prepare("2019-01-02")
    _, hmeta, _, _ = ledger_studies.ReplayHedge(study, arm["hedge"], _PARITY, optional=True).candidates(
        groups, metadata, 100)
    units, reference = hmeta[0]["program_hedge"]["units"], hmeta[0]["program_hedge"]["reference"]
    assert any(units) and reference == hedge["reference"]
    assert group["losses"][0] == study._loss_row(hmeta[0]["items"], group["grid"][0], 100, (units, reference))
    assert group["losses"][0] != study._loss_row(hmeta[0]["items"], group["grid"][0], 100)
    assert group["linear_rows"] == [{"coefficients": group["linear_rows"][0]["coefficients"], "upper": 0.}]
    scenario = next(s for s in program["utility"]["joint"] if s["id"] != "market:0")
    assert set(scenario["terms"]) == {group["id"]}
    # The search took the cheap synthetic forward HEDGED and sold further puts unhedged, in one book.
    assert {leg[4] for leg in _legs(book)} == {True, False}
    assert all(leg[:3] == ("put", leg[1], -1) for leg in _legs(book) if not leg[4])
    # The hedged reserve is the program's: the book's reserve is its hedged maximum loss, inside the shared cap.
    held = week["selection"]["quantities"][0]
    raw = sum(u * q for u, q in zip(units, held))
    assert hedge["units_raw"] == pytest.approx(raw, abs=1e-9)
    legs = [(leg["right"], leg["sign"] * leg["quantity"]) for leg in book["legs"]]
    loss = structure_max_loss(legs, [leg["strike"] for leg in book["legs"]], book["credit"] - hedge["cost_usd"], 100,
                              hedge=(hedge["units_raw"], reference))
    assert book["reserve"] >= loss - 1e-6 and book["reserve"] <= program["caps"][0]["upper"] + 1e-6
    assert week["selection"]["reserves"][0] == pytest.approx(book["reserve"], abs=1e-3)
    # The fill applies exactly the hedge the search chose: the HEDGED legs' decision units, truncated once, at the fill forward.
    trade, = result["trades"]
    assert trade["hedge"]["status"] == "ok" and trade["hedge"]["units"] == math.trunc(round(raw, 9)) != 0
    assert trade["hedge"]["raw"] == pytest.approx(raw, abs=1e-9) and "rehedges" not in trade["hedge"]
    only = sum(u * q for u, q, item in zip(units, held, hmeta[0]["items"]) if item.get("hedged"))
    assert only == raw, "an unhedged leg carries no units"
    level = next(row["close"] for row in inputs["X"]["bars"] if row["date"] == trade["close_date"])
    rates, u, f = trade["hedge"]["rates"], trade["hedge"]["units"], trade["hedge"]["reference"]
    costs = abs(u) * (2 * rates["hedge_fee_per_share"] + (f + level) * rates["hedge_half_spread_bps"] / 1e4)
    assert trade["pnl"] == pytest.approx(_option_pnl(trade, level) + u * (level - f) - costs)


def test_every_contract_is_offered_unhedged_and_as_a_hedged_twin_and_the_two_are_exclusive(monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportActionScore

    name = "kelly075_ls_hedged_static_lp"
    params, inputs, arm, study, result, seen = _hedged_run(name, monkeypatch)
    program = next(p for p in seen if "utility" in p)
    group, = program["groups"]
    (base,), (meta,), _ = study._prepare("2019-01-02")
    n = len(base["capacity"])
    hedge = ledger_studies.ReplayHedge(study, arm["hedge"], _PARITY, optional=True)
    (wide,), (hmeta,), refusals, _ = hedge.candidates([base], [meta], 100)
    twins = hmeta["hedge_twins"]
    assert twins == n and len(group["capacity"]) == 2 * n and not refusals   # every item of this chain can be hedged
    # the first n columns are the unhedged group, bit for bit; the twins repeat the contracts with ``hedged: True``
    assert group["credit"][:n] == base["credit"] and group["capacity"] == base["capacity"] * 2
    assert [row[:n] for row in group["losses"]] == base["losses"]
    assert [row[:n] for row in wide["reserve_rows"][:len(base["reserve_rows"])]] == base["reserve_rows"]
    assert group["linear_rows"][0]["coefficients"][:n] == base["linear_rows"][0]["coefficients"]
    assert hmeta["items"][:n] == meta["items"] and all("hedged" not in item for item in meta["items"])
    assert hmeta["items"][n:] == [{**item, "hedged": True} for item in meta["items"]]
    assert hmeta["program_hedge"]["units"][:n] == [0.0] * n and all(hmeta["program_hedge"]["units"][n:])
    assert hmeta["program_hedge"]["costs"][:n] == [0.0] * n and all(c > 0 for c in hmeta["program_hedge"]["costs"][n:])
    # a contract is held once: against its twin, and against the other side in every hedged / unhedged combination
    pairs = {tuple(sorted(pair)) for pair in group["exclusive"]}
    for i in range(n):
        assert (i, n + i) in pairs
    for a, b in base["exclusive"]:
        assert {(a, b), tuple(sorted((a, n + b))), tuple(sorted((n + a, b))), (n + a, n + b)} <= pairs
    assert len(pairs) == len(group["exclusive"]) == n + 4 * len(base["exclusive"])
    assert result["decisions"][0]["candidate_contracts"] == n // 2, "a twin is not a further candidate contract"
    # a naked short put is feasible UNHEDGED; its hedged twin alone is unbounded above (sold stock), so it is refused
    scorer = TransportActionScore(program, {"parity_tolerance": params["solver"]["parity_tolerance"]})
    put = next(i for i, item in enumerate(meta["items"]) if (item["right"], item["sign"], item["strike"]) == ("put", -1, 100.))
    one = lambda *held: [[1 if i in held else 0 for i in range(2 * n)]]            # noqa: E731
    naked = scorer.evaluate(one(put))
    assert naked["reserves"][0] == pytest.approx(100 * 100. - base["credit"][put])
    with pytest.raises(ValueError):
        scorer.evaluate(one(n + put))
    with pytest.raises(ValueError, match="exclusive"):
        scorer.evaluate(one(put, n + put))
    # an optional hedge keeps a group it cannot hedge (no decision forward) and an item it cannot price, unhedged
    monkeypatch.setattr(ledger_studies.ReplayHedge, "forward", lambda self, *a, **k: (None, "forward_unavailable"))
    kept, kmeta, refusals, counts = ledger_studies.ReplayHedge(study, arm["hedge"], _PARITY, optional=True).candidates(
        [base], [meta], 100)
    assert kept == [base] and kmeta == [meta] and not refusals and counts == {"hedge_forward_unavailable": 1}


@pytest.mark.parametrize("name", ["kelly075_ls_hedged_static_lp", "kelly075_ls_hedged_dynamic_lp"])
def test_the_search_trades_a_naked_short_put_unhedged_when_that_is_the_better_book(name):
    params, inputs, arm, study, result, _ = _hedged_run(name, cheap_call=False)      # a fair chain: no cheap synthetic
    book, = result["decisions"][0]["books"]
    assert _legs(book) == [("put", 100.0, -1, 5, False)], "sold puts, no leg hedged, nothing bought"
    trade, = result["trades"]
    assert trade["hedge"] == {"status": "unhedged_chosen", "units": 0} and not result["rejected"]
    assert trade["reserve"] == pytest.approx(5 * 100 * 100. - trade["credit"])     # the unhedged maximum loss
    level = next(row["close"] for row in inputs["X"]["bars"] if row["date"] == trade["close_date"])
    assert trade["pnl"] == pytest.approx(_option_pnl(trade, level)) and result["pnl"] == pytest.approx(trade["pnl"])
    assert result["hedge"]["counts"]["books_unhedged_chosen"] == 1
    assert not any(k.startswith("rehedge") and v for k, v in result["hedge"]["counts"].items())
    assert study._method(arm, None, recorded=result) == result


def test_the_search_drops_the_hedge_when_its_cost_takes_the_edge_and_keeps_it_when_it_pays():
    cheap = _hedged_run("kelly075_ls_hedged_static_lp")[4]
    dear = _hedged_run("kelly075_ls_hedged_static_lp",
                       block={"hedge_cost_source": "measured", "hedge_half_spread_bps": 400.0})[4]
    assert any(leg[4] for leg in _legs(cheap["decisions"][0]["books"][0])) and cheap["trades"][0]["hedge"]["units"]
    assert not any(leg[4] for leg in _legs(dear["decisions"][0]["books"][0]))
    assert dear["trades"][0]["hedge"]["status"] == "unhedged_chosen"


@pytest.mark.parametrize("name", sorted(HEDGED_FORMULATIONS))
def test_a_hedged_search_is_deterministic_and_replays_from_record_and_worker(name):
    from index_options.mio_worker import MIOMethodWorker

    _, _, arm, study, result, _ = _hedged_run(name)
    again = _hedged_run(name)[4]                                   # a stage-one certificate's seconds read the clock
    assert again["trades"] == result["trades"] and again["curve"] == result["curve"]
    assert [w.get("selection") for w in again["decisions"]] == [w.get("selection") for w in result["decisions"]]
    assert result["trades"] and result["trades"][0]["hedge"]["status"] in ("ok", "unhedged_chosen")
    assert study._method(arm, None, recorded=result) == result
    claim = MIOMethodWorker.actions(result, study.weeks)
    replay = study._method(arm, None, derived=claim)
    assert replay["trades"] == result["trades"] and replay["decisions"][0]["books"] == result["decisions"][0]["books"]
    assert study._method(arm, None, recorded=replay) == replay
    wrong = copy.deepcopy(claim)
    actions = wrong["decisions"][study.weeks[0]]["actions"]
    actions[0][next(i for i, q in enumerate(actions[0]) if q)] += 1
    with pytest.raises(Exception, match="differ"):
        study._method(arm, None, derived=wrong)


def test_the_dynamic_hedge_enters_as_the_static_one_and_charges_the_expected_rehedge_cost_in_the_program(monkeypatch):
    _, _, _, _, static, first = _hedged_run("kelly075_ls_hedged_static_lp", monkeypatch)
    _, _, _, _, dynamic, second = _hedged_run("kelly075_ls_hedged_dynamic_lp", monkeypatch)
    fixed, daily = first[0]["groups"][0], second[0]["groups"][0]
    n = len(fixed["credit"]) // 2
    assert all(d <= f for d, f in zip(daily["credit"], fixed["credit"])) and daily["credit"] != fixed["credit"]
    assert daily["credit"][:n] == fixed["credit"][:n], "an unhedged contract pays no re-hedge cost"
    # the same entry hedge: the loss rows (scenario payoffs) are the static ones; only the cost moved
    assert daily["losses"] == fixed["losses"] and daily["linear_rows"] == fixed["linear_rows"]
    a, b = static["trades"][0]["hedge"], dynamic["trades"][0]["hedge"]
    assert static["decisions"][0]["selection"]["quantities"] == dynamic["decisions"][0]["selection"]["quantities"]
    assert (a["units"], a["reference"]) == (b["units"], b["reference"]) and b["rehedges"] == []


def test_the_dynamic_scenarios_are_the_static_moves_split_into_seeded_paths_carrying_the_rehedge_excess(monkeypatch):
    name = "kelly075_ls_hedged_dynamic_lp"
    _, _, _, _, _, first = _hedged_run("kelly075_ls_hedged_static_lp", monkeypatch)
    params, inputs, arm, study, result, second = _hedged_run(name, monkeypatch)
    static = next(p for p in first if "utility" in p)["utility"]["joint"]
    program = next(p for p in second if "utility" in p)
    joint, gid = program["utility"]["joint"], program["groups"][0]["id"]
    week = result["decisions"][0]["kelly"]
    paths = arm["hedge"]["rehedge_paths"]
    cells = [(0., week["quiet"])] + [(m, week["weights"][str(m)]) for m in arm["kelly"]["moves"] if week["weights"][str(m)] > 0]
    assert len(cells) > 2 and len(static) == len(cells)
    assert week["rehedge_paths"] == {"paths": paths, "seed": arm["hedge"]["rehedge_seed"], "scenarios": len(joint)}
    assert [s["id"] for s in joint] == [f"market:{m}:path{k}" for m, _ in cells for k in range(paths)]
    assert [s["weight"] for s in joint] == [w / paths for _, w in cells for _ in range(paths)]
    assert sum(s["weight"] for s in joint) == pytest.approx(1.)
    # the hand calculation: every twin's term is its static term plus the path's re-hedge excess less the mean excess
    groups, metadata, _ = study._prepare("2019-01-02")
    hedge = ledger_studies.ReplayHedge(study, arm["hedge"], _PARITY, optional=True)
    _, (meta,), _, _ = hedge.candidates(groups, metadata, 100)
    ov, entries, n = hedge.overlay, meta["program_hedge"]["entries"], len(meta["items"]) // 2
    beta, sigma, steps = week["betas"]["X"], week["sigma_daily"], hedge.overlay.sessions(meta["dte"])
    assert steps == 21 and sigma > 0
    excess = {}
    for c, (move, _) in enumerate(cells):
        for k, bridge in enumerate(ov.bridges(steps, (c,))):
            levels = ov.path(meta["program_hedge"]["reference"], max(0., meta["spot"] * (1 + beta * move)),
                             beta * sigma, bridge)
            assert len(levels) == steps + 1
            excess[c, k] = [ov.path_excess(e, levels, meta["dte"]) for e in entries]
    mean = [sum(w / paths * excess[c, k][i] for c, (_, w) in enumerate(cells) for k in range(paths))
            for i in range(2 * n)]
    by_id = {s["id"]: s for s in joint}
    flat = {s["id"]: s["terms"].get(gid, [0.] * 2 * n) for s in static}
    seen_excess = set()
    for c, (move, _) in enumerate(cells):
        base = flat["market:0" if not c else f"market:{move}"]
        for k in range(paths):
            terms = by_id[f"market:{move}:path{k}"]["terms"][gid]
            assert terms[:n] == pytest.approx(base[:n], abs=1e-9), "an unhedged contract has no path term"
            assert all(x == 0.0 for x in excess[c, k][:n])
            assert terms[n:] == pytest.approx([base[i] + excess[c, k][i] - mean[i] for i in range(n, 2 * n)], abs=1e-7)
            seen_excess.add(round(excess[c, k][n], 6))
    assert len(seen_excess) == len(cells) * paths, "every path re-hedges differently"
    # the paths add dispersion, never edge: each contract's weighted mean term is zero, as the static scenarios' is
    for i in range(2 * n):
        assert sum(s["weight"] * s["terms"][gid][i] for s in joint) == pytest.approx(0., abs=1e-7)
    # a hedge that never moves (the synthetic forward: put and call twins of one strike) has no excess beyond float order
    put = next(i for i in range(n, 2 * n) if (meta["items"][i]["right"], meta["items"][i]["sign"], meta["items"][i]["strike"]) == ("put", -1, 100.))
    call = next(i for i in range(n, 2 * n) if (meta["items"][i]["right"], meta["items"][i]["sign"], meta["items"][i]["strike"]) == ("call", 1, 100.))
    assert max(abs(row[put]) for row in excess.values()) > 100.
    # ... beyond its trade costs, which the program charges per item (gross): the two twins' re-hedge P&L cancels
    assert all(-10. < row[put] + row[call] < 0. for row in excess.values())
    free = type(ov)({**arm["hedge"], "hedge_cost_source": "measured", "hedge_fee_per_share": 0., "hedge_half_spread_bps": 0.})
    levels = ov.path(meta["program_hedge"]["reference"], 90., beta * sigma, ov.bridges(steps, (1,))[0])
    assert free.path_excess(entries[put], levels, meta["dte"]) == pytest.approx(
        -free.path_excess(entries[call], levels, meta["dte"]), abs=1e-7)
    assert abs(free.path_excess(entries[put], levels, meta["dte"])) > 10.
    # determinism: the same program from a second study
    assert _hedged_run(name, monkeypatch)[5][-1]["utility"]["joint"] == joint


def test_the_path_scenarios_can_change_the_book_the_dynamic_search_chooses():
    def world():
        params, inputs = _hedged_world(cheap_call=False)
        for row in inputs["X"]["chain"]:
            if row["date"] in ("2019-01-02", "2019-01-03") and row["strike"] == 110 and row["type"] == "call":
                row["bid"], row["ask"] = .15, .16
        return params, inputs

    books = {}
    for kind in ("static", "dynamic"):
        _, _, arm, study = _hedged_study(f"kelly075_ls_hedged_{kind}_lp", world())
        result = study._method(arm, None)
        books[kind] = _legs(result["decisions"][0]["books"][0])
        assert ("call", 110.0, 1, 6, True) in books[kind], "a hedged long call: a hedge with gamma"
        assert study._method(arm, None, recorded=result) == result
    assert books["static"] != books["dynamic"]


def test_the_dynamic_ledger_rehedges_every_session_from_the_days_delta_and_settles_every_trade():
    from dskit.pipeline.hedge_overlay import forward_leg_rehedged_value

    name = "kelly075_ls_multistart3_hedged_dynamic_lp"
    params, inputs, arm, study, result, _ = _hedged_run(name, cheap_call=False)
    trade, = result["trades"]
    record = trade["hedge"]
    steps = record["rehedges"]
    assert {leg[4] for leg in _legs(trade)} == {True, False}, "a book of hedged and unhedged legs"
    assert len(steps) > 5 and all(trade["fill_date"] < s["date"] < trade["expiry"] for s in steps)
    assert [s["date"] for s in steps] == sorted(s["date"] for s in steps)
    held = record["units"]
    hedge = ledger_studies.ReplayHedge(study, arm["hedge"], _PARITY, optional=True)
    legs = [leg for leg in trade["legs"] if leg["quantity"]]
    hedged = [leg for leg in legs if leg.get("hedged")]
    rates = record["rates"]
    for step in steps:
        # each step is the overlay's units for the HEDGED legs on THAT session's chain and mark forward
        entries = hedge._entries("X", trade["expiry"], trade["factor"], step["date"], step["forward"], hedged)
        target = hedge.overlay.book_units(entries, [leg["quantity"] for leg in hedged])
        assert target["status"] == "ok" and step["units"] == target["units"] == math.trunc(target["raw"])
        whole = hedge.overlay.book_units(hedge._book_entries("X", trade["expiry"], trade["factor"], step["date"],
                                                             step["forward"], legs), [leg["quantity"] for leg in legs])
        assert whole["units"] == step["units"]
        assert step["trade"] == step["units"] - held != 0
        assert step["cost"] == pytest.approx(abs(step["trade"]) * (
            rates["hedge_fee_per_share"] + step["forward"] * rates["hedge_half_spread_bps"] / 1e4))
        assert step["reserve"] > 0                                 # the exact reserve after each re-strike is recorded
        held = step["units"]
    assert steps[-1]["reserve"] == trade["reserve"] and record["fill_reserve"] > 0
    level = next(row["close"] for row in inputs["X"]["bars"] if row["date"] == trade["close_date"])
    assert trade["pnl"] == pytest.approx(_option_pnl(trade, level) + forward_leg_rehedged_value(record, level))
    assert trade["pnl"] >= -trade["reserve"]                       # the re-struck reserve still bounds the settlement
    counts = result["hedge"]["counts"]
    assert counts["rehedges"] == len(steps) and counts["rehedge_shares"] == sum(abs(s["trade"]) for s in steps)
    assert "rehedge_reserve_over" not in counts
    assert result["pnl"] == pytest.approx(trade["pnl"])            # the shared ledger reconciled the re-hedged trade
    assert all(row["equity"] > 0 for row in result["curve"])
    assert study._method(arm, None, recorded=result) == result


def test_a_static_hedged_book_whose_hedge_cannot_attach_does_not_fill():
    params, inputs = _hedged_world()
    for row in inputs["X"]["chain"]:
        if row["date"] == "2019-01-03":
            row["iv"] = 0.3
    name = "kelly075_ls_hedged_static_lp"
    arm = {"id": name, **copy.deepcopy(HEDGED_FORMULATIONS[name])}
    params.update(methods=[arm], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    real = ledger_studies.ReplayHedge._decision_book
    ledger_studies.ReplayHedge._decision_book = lambda self, book, legs: (None, "leg_no_iv")
    try:
        result = study._method(arm, None)
    finally:
        ledger_studies.ReplayHedge._decision_book = real
    assert not result["trades"] and result["rejected"][0]["reason"] == "hedge_unattached:leg_no_iv"


# --- ADR-0316 Amendment 1, ruling 2: a re-struck reserve over the cap blocks new admissions, never closes a book -----

def _forced_restrike(monkeypatch, start, shares):
    """From session ``start`` on, every re-strike targets ``shares`` more than the day's delta asks for.

    The synthetic forward the search holds has a constant delta, so nothing would re-strike it; the forced target runs
    the real :meth:`ReplayHedge.rehedge` (the trade, its cost, the exact re-derived reserve) on an under-hedged book.
    """
    from dskit.pipeline.hedge_overlay import HedgeOverlay

    state = {"day": None}
    rehedge, book_units = ledger_studies.ReplayHedge.rehedge, HedgeOverlay.book_units

    def spy(self, books, day, charge=None):
        state["day"] = day
        try:
            return rehedge(self, books, day, charge)
        finally:
            state["day"] = None

    def forced(self, entries, quantities):
        out = book_units(self, entries, quantities)
        if state["day"] is not None and state["day"] >= start and out["status"] == "ok":
            out = {**out, "units": out["units"] + shares}
        return out
    monkeypatch.setattr(ledger_studies.ReplayHedge, "rehedge", spy)
    monkeypatch.setattr(HedgeOverlay, "book_units", forced)


def _two_week_world(deposit=None):
    """The hedged world deciding both weeks; ``deposit`` funds a second expiry's chain so week two has a book to fill."""
    from datetime import date

    from dskit.pipeline.option_pricing import black76

    params, inputs = _hedged_world()
    del params["decision_weeks"]
    if deposit is None:
        return params, inputs
    params["clocks"]["entry_end_before"] = "2019-01-09"
    params["deposit"], params["deposit_every_days"] = deposit, 5
    spots, expiry = {row["date"]: row["close"] for row in inputs["X"]["bars"]}, "2019-02-08"
    row = {"symbol": "X", "expiration": expiry, "bid_size": 10, "ask_size": 10, "basis_factor": 1., "iv": 0.3}
    inputs["X"]["chain"].append({**row, "date": expiry, "type": "put", "strike_raw": 90., "strike": 90., "bid": .05,
                                 "ask": .06, "underlying_price": spots[expiry]})
    for day in sorted(spots):
        if not "2019-01-07" <= day < expiry:
            continue
        years = (date.fromisoformat(expiry) - date.fromisoformat(day)).days / 365
        for right in ("call", "put"):
            for strike in (95., 100., 105., 110., 115.):
                mid = black76(right, spots[day] * 1.001, strike, 0.3, years)
                inputs["X"]["chain"].append({**row, "date": day, "type": right, "strike_raw": strike, "strike": strike,
                                             "bid": mid * .99, "ask": mid * 1.01, "underlying_price": spots[day]})
    for context in inputs["X"]["contexts"]:
        if context["quote_date"] == "2019-01-07":
            context["spot"] = spots["2019-01-07"]
    return params, inputs


def test_a_restruck_reserve_over_the_cap_blocks_the_next_week_is_counted_and_never_closes_the_book(monkeypatch):
    name = "kelly075_ls_hedged_dynamic_lp"
    _, _, arm, study = _hedged_study(name, _two_week_world())
    calm = study._method(arm, None)
    assert [w["status"] for w in calm["decisions"]] == ["selected", "no_entry_fill_session"]
    assert "rehedge_reserve_over" not in calm["hedge"]["counts"] and calm["trades"][0]["hedge"]["rehedges"] == []
    _forced_restrike(monkeypatch, "2019-01-04", 100)
    _, inputs, arm, study = _hedged_study(name, _two_week_world())
    result = study._method(arm, None)
    first, second = result["decisions"]
    trade, = result["trades"]
    step, = trade["hedge"]["rehedges"]
    # the EXACT reserve after the re-strike: an under-hedged synthetic is long 100 shares, which can go to zero
    assert step["date"] == "2019-01-04" and step["trade"] == 100
    growth = step["reserve"] - trade["hedge"]["fill_reserve"]
    assert growth == pytest.approx(100 * step["forward"], rel=.02) and trade["reserve"] == step["reserve"]
    assert step["reserve"] > second["funding"], "the open reserve is over the funding the cap allows"
    # the week admits nothing, says why, and the block is counted
    assert second["status"] == "rehedge_reserve_over" and "books" not in second
    assert second["rehedge_reserve_over"] == {
        "date": "2019-01-07", "open_reserve": pytest.approx(step["reserve"]), "reserve_growth": pytest.approx(growth),
        "shortfall": pytest.approx(step["reserve"] - second["funding"]), "blocked": []}
    assert second["open_reserve"] == pytest.approx(step["reserve"])
    assert result["hedge"]["counts"]["rehedge_reserve_over"] == 1
    # never force-closed: the book is held to its expiry at the re-struck units and settles inside its reserve
    assert trade["close_date"] == trade["expiry"] and trade["hedge"]["units"] == -1000 and step["units"] == -900
    assert trade["pnl"] >= -trade["reserve"] and not result["rejected"]
    assert study._method(arm, None, recorded=result) == result


def test_a_reserve_that_grows_between_the_decision_and_the_fill_blocks_the_fill(monkeypatch):
    name = "kelly075_ls_hedged_dynamic_lp"
    _, _, arm, study = _hedged_study(name, _two_week_world(30000.))
    calm = study._method(arm, None)
    assert [w["status"] for w in calm["decisions"]] == ["selected", "selected"] and len(calm["trades"]) == 2
    # week two's own choice: a naked short put, unhedged, beside week one's hedged synthetic
    assert _legs(calm["decisions"][1]["books"][0]) == [("put", 100.0, -1, 3, False)]
    assert [t["hedge"]["status"] for t in calm["trades"]] == ["ok", "unhedged_chosen"]
    _forced_restrike(monkeypatch, "2019-01-08", 400)                # the fill session of week two
    _, _, arm, study = _hedged_study(name, _two_week_world(30000.))
    result = study._method(arm, None)
    first, second = result["decisions"]
    assert second["status"] == "selected" and second["books"] == calm["decisions"][1]["books"]
    trade, = result["trades"]
    blocked, = result["rejected"]
    assert blocked["id"] == second["books"][0]["id"] and blocked["reason"] == "rehedge_reserve_over"
    assert blocked["status"] == "unfilled"
    over = second["rehedge_reserve_over"]
    assert over["date"] == "2019-01-08" and over["blocked"] == [blocked["id"]]
    assert over["open_reserve"] == pytest.approx(trade["hedge"]["rehedges"][0]["reserve"]) and over["open_reserve"] > 90000.
    assert over["shortfall"] == pytest.approx(over["open_reserve"] - 90000.)
    assert result["hedge"]["counts"]["rehedge_reserve_over"] == 1
    assert trade["close_date"] == trade["expiry"] and trade["pnl"] >= -trade["reserve"]
    assert study._method(arm, None, recorded=result) == result


def test_reserve_over_needs_a_breached_cap_and_restruck_growth_and_only_a_rebalancing_hedge():
    params, inputs, arm, study = _hedged_study("kelly075_ls_hedged_dynamic_lp", _hedged_world())
    daily = ledger_studies.ReplayHedge(study, arm["hedge"], _PARITY, optional=True)
    grown = {"reserve": 700., "hedge": {"fill_reserve": 500., "rehedges": [{"reserve": 400.}, {"reserve": 700.}]}}
    plain = {"reserve": 300., "hedge": {"status": "unhedged_chosen", "units": 0}}
    breach, room = [{"members": ["X"], "upper": -50.}], [{"members": ["X"], "upper": 50.}]
    assert daily.reserve_over([grown, plain], breach) == {"open_reserve": 1000., "reserve_growth": 200., "shortfall": 50.}
    assert daily.reserve_over([grown, plain], room) is None, "inside the cap nothing is blocked"
    shrunk = {"reserve": 450., "hedge": {"fill_reserve": 500., "rehedges": [{"reserve": 450.}]}}
    assert daily.reserve_over([shrunk, plain], breach) is None, "a breach the re-strikes did not grow is the cap's own"
    assert daily.counts["rehedge_reserve_over"] == 1
    fixed = ledger_studies.ReplayHedge(study, HEDGED_FORMULATIONS["kelly075_ls_hedged_static_lp"]["hedge"], _PARITY)
    assert fixed.reserve_over([grown], breach) is None and "rehedge_reserve_over" not in fixed.counts


# --- ADR-0316 Amendment 2: the hedged ids price their legs on the quote layout the real runs read --------------------

def _real_layout(world, layout):
    """``world`` with its chain rows in a real run's layout: ``index`` = the MIO packet's quote index (no vol column at
    all), ``archive`` = the onboarded Theta archive (the vendor vol under ``implied_volatility``, no ``iv`` key)."""
    params, inputs = world
    for row in inputs["X"]["chain"]:
        iv = row.pop("iv")
        if layout == "archive":
            row["implied_volatility"] = iv
    return params, inputs


def test_the_twelve_ids_take_the_vendor_iv_else_the_mid_implied_vol():
    assert {m["hedge"]["iv_source"] for m in HEDGED_FORMULATIONS.values()} == {"vendor_else_mid"}
    assert {k: v for k, v in STATIC_BLOCK.items() if k != "iv_source"} == {
        k: v for k, v in _PROGRAM_BLOCK.items() if k != "iv_source"}


@pytest.mark.parametrize("layout", ["index", "archive"])
def test_on_the_real_layout_a_vendor_block_hedges_nothing_and_the_guard_counts_and_logs_the_week(layout, caplog):
    """The defect of the first real hedged run: every leg ``leg_no_iv``, every twin missing, the unhedged base traded."""
    import logging

    world = _real_layout(_hedged_world(), layout)
    params, inputs, arm, study = _hedged_study("kelly075_ls_hedged_static_lp", world, {"iv_source": "vendor"})
    with caplog.at_level(logging.WARNING, logger="index_options.ledger_studies"):
        result = study._method(arm, None)
    week, counts = result["decisions"][0], result["hedge"]["counts"]
    assert counts["leg_no_iv"] == counts["leg_no_iv_excluded"] > 0 and "leg_iv_mid" not in counts
    assert counts["weeks_all_legs_no_iv"] == week["hedge"]["excluded"]["weeks_all_legs_no_iv"] == 1
    assert not any(leg[4] for book in week["books"] for leg in _legs(book))
    assert any("HEDGE GUARD weeks_all_legs_no_iv" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("layout", ["index", "archive"])
@pytest.mark.parametrize("name", ["kelly075_ls_hedged_static_lp", "kelly075_ls_hedged_dynamic_lp"])
def test_on_the_real_layout_the_hedged_ids_price_from_the_mid_and_choose_hedged_twins(layout, name):
    params, inputs, arm, study = _hedged_study(name, _real_layout(_hedged_world(), layout))
    result = study._method(arm, None)
    week, counts = result["decisions"][0], result["hedge"]["counts"]
    # Only the two sides of the 100 call, quoted under its intrinsic value (no Black-76 vol fits), lack an iv.
    assert counts["leg_no_iv"] == week["hedge"]["excluded"]["leg_no_iv_excluded"] == 2
    assert counts["leg_iv_mid"] > 0 and "weeks_all_legs_no_iv" not in counts
    hedged = [leg for book in week["books"] for leg in _legs(book) if leg[4]]
    assert hedged and all(trade["hedge"]["units"] != 0 for trade in result["trades"])
    again = _hedged_study(name, _real_layout(_hedged_world(), layout))[3]._method(arm, None)  # certificates read the clock
    assert (again["trades"], again["curve"], again["hedge"]) == (result["trades"], result["curve"], result["hedge"])
    assert [w.get("selection") for w in again["decisions"]] == [w.get("selection") for w in result["decisions"]]
    assert study._method(arm, None, recorded=result) == result, "the recorded route replays it exactly"


@pytest.mark.parametrize("layout", ["fixture", "index", "archive"])
def test_the_program_hedge_prices_each_leg_as_the_replay_hedge_does(layout):
    """Replay parity: a hedged id's candidate entries equal the replay hedge's (ADR-0312 A1 S3) for the same legs: the
    vendor iv where the quote carries one, else the mid-implied vol the real replay runs used (``iv_source: "mid"``)."""
    world = _hedged_world() if layout == "fixture" else _real_layout(_hedged_world(), layout)
    _, _, _, study = _hedged_study("kelly075_ls_hedged_static_lp", world)
    program = ledger_studies.ReplayHedge(study, STATIC_BLOCK, _PARITY, optional=True)
    source = "vendor" if layout == "fixture" else "mid"
    replay = ledger_studies.ReplayHedge(study, {**_REPLAY_BLOCK, "iv_source": source}, _PARITY)
    day, expiry = "2019-01-02", "2019-02-01"
    legs = [{"right": r["right"], "strike": r["strike"], "sign": sign}
            for r in program._chain("X", day, expiry) for sign in (1, -1)]
    forward, _ = program.forward("X", day, expiry, 1.)
    mine = program._entries("X", expiry, 1., day, forward, legs)
    theirs = replay._entries("X", expiry, 1., day, forward, legs)
    assert [(e["status"], e["delta_f_sh"], e["units"]) for e in mine] == [
        (e["status"], e["delta_f_sh"], e["units"]) for e in theirs]
    assert sum(e["status"] == "ok" for e in mine) > len(mine) // 2
