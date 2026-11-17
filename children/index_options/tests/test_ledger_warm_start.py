"""``kelly.local_search.start.warm``: the opt-in MIP start of stage one from the previous decision week's book."""

import pytest

from index_options import ledger_studies
from test_ledger_studies import LOCAL_SEARCH, SHOCK10, _local_world

#: A nominal start (no radius, a wide-open shock gate) so stage one trades and there is a book to carry forward.
LOOSE = {"rho": 0., "shock": {**SHOCK10, "loss_fraction": 1.}}


def _result(warm=None, search=None):
    start = dict(LOOSE) if warm is None else {**LOOSE, "warm": warm}
    params, data, method = _local_world(search={"start": start, **(search or {})})
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
    return params, data, method, study._method(method, None)


def _decision(week):
    return {k: week.get(k) for k in ("status", "selection", "books")}


@pytest.mark.parametrize("warm", ["none", "previous_week", "previous_climb"])
def test_the_warm_key_is_declared(warm):
    params, _data, _method = _local_world(search={"start": {**LOOSE, "warm": warm}})
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


@pytest.mark.parametrize("bad", ["previous", True, 1, None, "NONE"])
def test_a_malformed_warm_key_is_refused(bad):
    params, _data, _method = _local_world(search={"start": {**LOOSE, "warm": bad}})
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_a_start_block_with_any_other_extra_key_is_still_refused():
    params, _data, _method = _local_world(search={"start": {**LOOSE, "colour": "red"}})
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_the_key_absent_or_none_leaves_the_weeks_and_their_records_untouched(monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    offered = []
    run = TransportPortfolioSolve.run

    def spy(self, ctx, inputs):
        offered.append(self.warm_quantities)
        return run(self, ctx, inputs)
    monkeypatch.setattr(TransportPortfolioSolve, "run", spy)
    _p, _d, _m, base = _result()
    _p, _d, _m, none = _result("none")
    assert offered and all(q is None for q in offered)
    for left, right in zip(base["decisions"], none["decisions"]):
        assert _decision(left) == _decision(right) and left.get("kelly") == right.get("kelly")
        assert "stage1_warm" not in left.get("kelly", {}).get("local", {})


@pytest.mark.parametrize("mode", ["previous_week", "previous_climb"])
def test_a_warm_week_offers_the_previous_book_by_contract_and_records_it(monkeypatch, mode):
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    offered = []
    run = TransportPortfolioSolve.run

    def spy(self, ctx, inputs):
        offered.append(self.warm_quantities)
        out = run(self, ctx, inputs)
        offered[-1] = (self.warm_quantities, self.last_start)
        return out
    monkeypatch.setattr(TransportPortfolioSolve, "run", spy)
    _p, _d, _m, cold = _result()
    cold_offered = list(offered)
    offered.clear()
    _p, _d, _m, warm = _result(mode)
    weeks = [w for w in warm["decisions"] if "kelly" in w and "local" in w["kelly"]]
    records = [w["kelly"]["local"]["stage1_warm"] for w in weeks]
    assert records and all(r["mode"] == mode for r in records)
    assert records[0] == {"mode": mode, "used": False, "reason": "no_previous_book", "objective": None,
                          "scored": None, "scale": None}
    for record in records:
        assert set(record) == {"mode", "used", "reason", "objective", "scored", "scale"}
        if record["used"]:
            assert record["objective"] is not None and record["scale"] in (1., .5, .25, .125)
    assert len(offered) == len(weeks)


def _same_leg_every_week(monkeypatch):
    """The fixture's weeks share no expiry; let a leg carry over by right, strike and side so week two is offered a book."""
    monkeypatch.setattr(ledger_studies.WeeklyOptionPortfolioStudy, "_contract_key",
                        staticmethod(lambda meta, item: (meta["symbol"], item["right"], item["strike"], item["sign"])))


@pytest.mark.parametrize("mode", ["previous_week", "previous_climb"])
def test_a_carried_book_reaches_stage_one_as_a_start_and_the_trade_is_the_climbs(monkeypatch, mode):
    _p, _d, _m, cold = _result()
    _same_leg_every_week(monkeypatch)
    params, data, method, warm = _result(mode)
    second = warm["decisions"][1]["kelly"]["local"]["stage1_warm"]
    assert second["mode"] == mode and second["reason"] != "no_previous_book"
    assert "used" in second and "seconds" not in second
    if second["used"]:
        assert second["objective"] == pytest.approx(second["scored"], abs=1e-3) and second["scale"] in (1., .5, .25, .125)
    else:
        assert second["reason"] in ("infeasible", "completion_infeasible", "all_zero")
    # Whatever stage one returns is only the climb's start; the first week (no start) is the cold week exactly.
    assert _decision(warm["decisions"][0]) == _decision(cold["decisions"][0])
    again = ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=warm)
    assert again == warm


def test_a_recorded_replay_of_a_warm_run_reproduces_it_exactly():
    params, data, method, result = _result("previous_week")
    again = ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=result)
    assert again == result


def test_the_contract_book_maps_by_contract_and_a_vanished_contract_gets_zero():
    meta_a = [{"id": "d1:S:e1", "symbol": "S", "expiry": "e1",
               "items": [{"right": "put", "strike": 100., "sign": 1}, {"right": "put", "strike": 90., "sign": -1},
                         {"right": "call", "strike": 110., "sign": -1}]}]
    groups_a = [{"id": "d1:S:e1", "capacity": [5, 5, 5]}]
    book = ledger_studies.WeeklyOptionPortfolioStudy._contract_book(groups_a, meta_a, [[2, 0, 3]])
    assert book == {("S", "e1", "put", 100., 1, False): 2,
                    ("S", "e1", "call", 110., -1, False): 3}
    study = object.__new__(ledger_studies.WeeklyOptionPortfolioStudy)
    study._warm_books = {"m": book}
    # next week: same expiry with the legs reordered and one gone, plus a new expiry
    meta_b = [{"id": "d2:S:e1", "symbol": "S", "expiry": "e1",
               "items": [{"right": "call", "strike": 110., "sign": -1}, {"right": "put", "strike": 100., "sign": 1}]},
              {"id": "d2:S:e2", "symbol": "S", "expiry": "e2",
               "items": [{"right": "put", "strike": 100., "sign": 1}]}]
    groups_b = [{"id": "d2:S:e1", "capacity": [5, 5]}, {"id": "d2:S:e2", "capacity": [5]}]
    assert study._warm_start_quantities("m", groups_b, meta_b) == [[3, 2], [0]]
    assert study._warm_start_quantities("other", groups_b, meta_b) is None
    study._warm_books = {"m": {("S", "gone", "put", 1., 1, False): 4}}
    assert study._warm_start_quantities("m", groups_b, meta_b) is None     # nothing carries over: no start offered


def test_a_hedged_twin_is_a_different_contract_from_its_plain_leg():
    key = ledger_studies.WeeklyOptionPortfolioStudy._contract_key
    meta = {"symbol": "S", "expiry": "e"}
    plain = {"right": "put", "strike": 100., "sign": 1}
    assert key(meta, plain) != key(meta, {**plain, "hedged": True})
    assert key(meta, plain) == key(meta, {**plain, "hedged": False})


def test_a_second_run_on_one_study_starts_cold(monkeypatch):
    _same_leg_every_week(monkeypatch)
    params, data, method = _local_world(search={"start": {**LOOSE, "warm": "previous_week"}})
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
    first = study._method(method, None)
    second = study._method(method, None)
    assert first["decisions"][0]["kelly"]["local"]["stage1_warm"]["reason"] == "no_previous_book"
    assert second["decisions"][0]["kelly"]["local"]["stage1_warm"]["reason"] == "no_previous_book"
    assert [w.get("kelly", {}).get("local", {}).get("stage1_warm") for w in first["decisions"]] ==         [w.get("kelly", {}).get("local", {}).get("stage1_warm") for w in second["decisions"]]
