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

from test_ledger_studies import FAST_LOCAL_FORMULATIONS, _PROGRAM_BLOCK  # noqa: E402

#: The two hedge kinds of ADR-0316, both ``program`` mode: the fixed entry hedge held to expiry, and the same entry
#: re-struck every session.
STATIC_BLOCK = dict(_PROGRAM_BLOCK)
DYNAMIC_BLOCK = {**_PROGRAM_BLOCK, "kind": "daily_delta_forward", "rehedge_sessions_per_year": 252}
_HEDGED_BASES = {**MULTISTART3_FORMULATIONS, **FAST_LOCAL_FORMULATIONS,
                 "kelly050_ls_lp": LOCAL_FORMULATIONS["kelly050_ls_lp"],
                 "kelly075_ls_lp": {**LOCAL_FORMULATIONS["kelly050_ls_lp"],
                                    "kelly": {**LOCAL_FORMULATIONS["kelly050_ls_lp"]["kelly"], "fraction": .75}}}
#: New ids: each base method, identical apart from the block (``<base>_hedged_static_lp``, ``<base>_hedged_dynamic_lp``).
HEDGED_FORMULATIONS = {
    name.removesuffix("_lp")+f"_hedged_{kind}_lp": {**copy.deepcopy(method), "hedge": dict(block_)}
    for name, method in _HEDGED_BASES.items()
    for kind, block_ in (("static", STATIC_BLOCK), ("dynamic", DYNAMIC_BLOCK))}


def _hedged_world():
    """The program-hedge world with a kelly history; one decision week, every strike of the 2019-02-01 expiry."""
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
        if row["date"] in ("2019-01-02", "2019-01-03") and row["strike"] == 100 and row["type"] == "call":
            row["bid"], row["ask"] = .15, .16
    return params, inputs


def _hedged_run(name, monkeypatch=None):
    params, inputs = _hedged_world()
    arm = {"id": name, **copy.deepcopy(HEDGED_FORMULATIONS[name])}
    params.update(methods=[arm], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    seen = []
    if monkeypatch is not None:
        from dskit.pipeline.libs.pyomo import TransportActionScore
        real = TransportActionScore.__init__

        def spy(scorer, program, *a, **k):
            seen.append(program)
            real(scorer, program, *a, **k)
        monkeypatch.setattr(TransportActionScore, "__init__", spy)
    return params, inputs, arm, study, study._method(arm, None), seen


def test_the_hedged_formulations_are_their_bases_plus_the_block_and_validate_on_new_ids():
    assert sorted(HEDGED_FORMULATIONS) == sorted(
        f"kelly{f}_ls{v}_hedged_{k}_lp" for f in ("050", "075") for v in ("", "_fast", "_multistart3")
        for k in ("static", "dynamic"))
    for name, method in HEDGED_FORMULATIONS.items():
        base = name.replace("_hedged_static_lp", "_lp").replace("_hedged_dynamic_lp", "_lp")
        assert {k: v for k, v in method.items() if k != "hedge"} == _HEDGED_BASES[base]
        assert method["hedge"]["mode"] == "program"
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


@pytest.mark.parametrize("name", ["kelly075_ls_multistart3_hedged_static_lp", "kelly050_ls_fast_hedged_static_lp"])
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
    _, hmeta, _, _ = ledger_studies.ReplayHedge(study, arm["hedge"], _PARITY).candidates(groups, metadata, 100)
    units, reference = hmeta[0]["program_hedge"]["units"], hmeta[0]["program_hedge"]["reference"]
    assert any(units) and reference == hedge["reference"]
    assert group["losses"][0] == study._loss_row(hmeta[0]["items"], group["grid"][0], 100, (units, reference))
    assert group["losses"][0] != study._loss_row(hmeta[0]["items"], group["grid"][0], 100)
    assert group["linear_rows"] == [{"coefficients": group["linear_rows"][0]["coefficients"], "upper": 0.}]
    scenario = next(s for s in program["utility"]["joint"] if s["id"] != "market:0")
    assert set(scenario["terms"]) == {group["id"]}
    # The hedged reserve is the program's: the book's reserve is its hedged maximum loss, inside the shared cap.
    held = week["selection"]["quantities"][0]
    raw = sum(u * q for u, q in zip(units, held))
    assert hedge["units_raw"] == pytest.approx(raw, abs=1e-9)
    legs = [(leg["right"], leg["sign"] * leg["quantity"]) for leg in book["legs"]]
    loss = structure_max_loss(legs, [leg["strike"] for leg in book["legs"]], book["credit"] - hedge["cost_usd"], 100,
                              hedge=(hedge["units_raw"], reference))
    assert book["reserve"] >= loss - 1e-6 and book["reserve"] <= program["caps"][0]["upper"] + 1e-6
    assert week["selection"]["reserves"][0] == pytest.approx(book["reserve"], abs=1e-3)
    # The fill applies exactly the hedge the search chose: the decision units, truncated once, struck at the fill forward.
    trade, = result["trades"]
    assert trade["hedge"]["status"] == "ok" and trade["hedge"]["units"] == math.trunc(raw)
    assert trade["hedge"]["raw"] == pytest.approx(raw, abs=1e-9) and "rehedges" not in trade["hedge"]
    level = next(row["close"] for row in inputs["X"]["bars"] if row["date"] == trade["close_date"])
    rates, u, f = trade["hedge"]["rates"], trade["hedge"]["units"], trade["hedge"]["reference"]
    costs = abs(u) * (2 * rates["hedge_fee_per_share"] + (f + level) * rates["hedge_half_spread_bps"] / 1e4)
    options = trade["credit"] + 100 * sum(
        leg["sign"] * leg["quantity"] * max((leg["strike"] - level) if leg["right"] == "put" else (level - leg["strike"]), 0.)
        for leg in trade["legs"])
    assert trade["pnl"] == pytest.approx(options + u * (level - f) - costs)


def test_the_hedged_search_differs_from_the_replay_pairing_which_scores_the_unhedged_program(monkeypatch):
    _, _, _, _, hedged, seen = _hedged_run("kelly075_ls_multistart3_hedged_static_lp", monkeypatch)
    params, inputs = _hedged_world()
    base = {"id": "kelly075_ls_multistart3_lp", **copy.deepcopy(MULTISTART3_FORMULATIONS["kelly075_ls_multistart3_lp"])}
    params["methods"] = [base]
    plain = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(base, None)
    assert seen[-1]["groups"][0]["losses"] != seen[0]["groups"][0]["losses"]
    assert plain["decisions"][0]["selection"]["quantities"] != hedged["decisions"][0]["selection"]["quantities"]


@pytest.mark.parametrize("name", sorted(HEDGED_FORMULATIONS))
def test_a_hedged_search_is_deterministic_and_replays_from_record_and_worker(name):
    from index_options.mio_worker import MIOMethodWorker

    _, _, arm, study, result, _ = _hedged_run(name)
    again = _hedged_run(name)[4]                                   # a stage-one certificate's seconds read the clock
    assert again["trades"] == result["trades"] and again["curve"] == result["curve"]
    assert [w.get("selection") for w in again["decisions"]] == [w.get("selection") for w in result["decisions"]]
    assert result["trades"] and result["trades"][0]["hedge"]["status"] == "ok"
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
    _, _, _, _, static, first = _hedged_run("kelly075_ls_multistart3_hedged_static_lp", monkeypatch)
    _, _, _, _, dynamic, second = _hedged_run("kelly075_ls_multistart3_hedged_dynamic_lp", monkeypatch)
    fixed, daily = first[0]["groups"][0], second[0]["groups"][0]
    assert all(d <= f for d, f in zip(daily["credit"], fixed["credit"])) and daily["credit"] != fixed["credit"]
    # the same entry hedge: the loss rows (scenario payoffs) are the static ones; only the cost moved
    assert daily["losses"] == fixed["losses"] and daily["linear_rows"] == fixed["linear_rows"]
    a, b = static["trades"][0]["hedge"], dynamic["trades"][0]["hedge"]
    if static["decisions"][0]["selection"]["quantities"] == dynamic["decisions"][0]["selection"]["quantities"]:
        assert (a["units"], a["reference"]) == (b["units"], b["reference"])


def test_the_dynamic_ledger_rehedges_every_session_from_the_days_delta_and_settles_every_trade():
    from dskit.pipeline.hedge_overlay import forward_leg_rehedged_value

    params, inputs, arm, study, result, _ = _hedged_run("kelly075_ls_multistart3_hedged_dynamic_lp")
    trade, = result["trades"]
    record = trade["hedge"]
    steps = record["rehedges"]
    assert steps and all(trade["fill_date"] < s["date"] < trade["expiry"] for s in steps)
    assert [s["date"] for s in steps] == sorted(s["date"] for s in steps)
    held = record["units"]
    hedge = ledger_studies.ReplayHedge(study, arm["hedge"], _PARITY)
    legs = [leg for leg in trade["legs"] if leg["quantity"]]
    rates = record["rates"]
    for step in steps:
        # each step is the overlay's units on THAT session's chain and mark forward, traded from the units held
        entries = hedge._entries("X", trade["expiry"], trade["factor"], step["date"], step["forward"], legs)
        target = hedge.overlay.book_units(entries, [leg["quantity"] for leg in legs])
        assert target["status"] == "ok" and step["units"] == target["units"] == math.trunc(target["raw"])
        assert step["trade"] == step["units"] - held != 0
        assert step["cost"] == pytest.approx(abs(step["trade"]) * (
            rates["hedge_fee_per_share"] + step["forward"] * rates["hedge_half_spread_bps"] / 1e4))
        held = step["units"]
    level = next(row["close"] for row in inputs["X"]["bars"] if row["date"] == trade["close_date"])
    options = trade["credit"] + 100 * sum(
        leg["sign"] * leg["quantity"] * max((leg["strike"] - level) if leg["right"] == "put" else (level - leg["strike"]), 0.)
        for leg in trade["legs"])
    assert trade["pnl"] == pytest.approx(options + forward_leg_rehedged_value(record, level))
    assert trade["pnl"] >= -trade["reserve"]                       # the re-struck reserve still bounds the settlement
    counts = result["hedge"]["counts"]
    assert counts["rehedges"] == len(steps) and counts["rehedge_shares"] == sum(abs(s["trade"]) for s in steps)
    assert result["pnl"] == pytest.approx(trade["pnl"])            # the shared ledger reconciled the re-hedged trade
    # every daily mark carries the re-hedges to date: the last open mark plus the last session's move is the settlement
    assert all(row["equity"] > 0 for row in result["curve"])


def test_a_static_hedged_book_whose_hedge_cannot_attach_does_not_fill():
    params, inputs = _hedged_world()
    for row in inputs["X"]["chain"]:
        if row["date"] == "2019-01-03":
            row["iv"] = 0.3
    name = "kelly075_ls_multistart3_hedged_static_lp"
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
