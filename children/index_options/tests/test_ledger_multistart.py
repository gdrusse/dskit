"""The multi-start Kelly local-search methods in the weekly study (ADR-0315): declaration, week, replay, hedge."""

import copy

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
