"""Opt-in ``kelly.local_search.start.strategy`` ``relax_fix`` (relax-and-fix for the stage-one dollar program)."""
import copy

import pytest

from index_options import ledger_studies
from test_ledger_studies import LOCAL_SEARCH, _local_result, _local_world

SEARCH = {"iterations": 3, "neighbourhood": 2, "node_limit": 100, "seed": 11}
RELAX = {"start": {**LOCAL_SEARCH["start"], "strategy": "relax_fix"}}
RELAX_SEARCHED = {"start": {**LOCAL_SEARCH["start"], "strategy": "relax_fix", "search": SEARCH}}
RECORD_KEYS = {"strategy", "outcome", "actions", "support_actions", "lp_bound", "restricted_objective",
               "restricted_termination", "final_objective", "lp_seconds", "restricted_seconds", "search_seconds",
               "seconds", "search"}


def _valid(change):
    params, _data, _method = _local_world(change)
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_the_opt_in_declarations_validate():
    _valid({"start": {**LOCAL_SEARCH["start"], "strategy": "milp"}})
    _valid(RELAX)
    _valid(RELAX_SEARCHED)


@pytest.mark.parametrize("start", [
    {"strategy": "direct"}, {"strategy": None}, {"strategy": 1}, {"strategy": "milp", "search": SEARCH},
    {"strategy": "relax_fix", "search": None}, {"strategy": "relax_fix", "search": {**SEARCH, "iterations": 0}},
    {"strategy": "relax_fix", "search": {"iterations": 3}}, {"search": SEARCH}, {"strategy": "relax_fix", "x": 1}])
def test_a_malformed_start_strategy_is_refused(start):
    params, _data, _method = _local_world({"start": {**LOCAL_SEARCH["start"], **start}})
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_relax_fix_needs_a_solver_block_that_returns_timeouts():
    params, _data, _method = _local_world(RELAX)
    params["solver"] = {k: v for k, v in params["solver"].items() if k != "timeout_policy"}
    with pytest.raises(ValueError):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_an_absent_key_and_an_explicit_milp_are_the_same_week_and_write_no_field():
    _p, _d, _m, _s, plain = _local_result()
    _p, _d, _m, _s, milp = _local_result(search={"start": {**LOCAL_SEARCH["start"], "strategy": "milp"}})
    for old, new in zip(plain["decisions"], milp["decisions"]):
        assert old.get("selection") == new.get("selection") and old.get("books") == new.get("books")
        assert old.get("kelly", {}).get("local") == new.get("kelly", {}).get("local")
        assert "stage1_solve" not in new.get("kelly", {}).get("local", {})


@pytest.mark.parametrize("search", [RELAX, RELAX_SEARCHED])
def test_relax_fix_stage_one_is_feasible_recorded_and_replays(search):
    params, data, method, study, result = _local_result(search=search)
    _p, _d, _m, _s, plain = _local_result()
    seen = 0
    for week, old in zip(result["decisions"], plain["decisions"]):
        local = week.get("kelly", {}).get("local")
        if local is None:
            continue
        seen += 1
        solve = local["stage1_solve"]
        assert set(solve) == RECORD_KEYS and solve["strategy"] == "relax_fix"
        assert solve["lp_bound"] is not None and solve["support_actions"] <= solve["actions"]
        assert solve["restricted_objective"] <= solve["lp_bound"]+1e-6
        assert solve["seconds"] >= 0 and (search is RELAX or solve["search"] is not None)
        assert local["end_value"] >= local["start_value"]
        assert week["stage1"]["selection"].get("candidate", week["stage1"]["selection"])["value"] <= solve["lp_bound"]+1e-6
        # The climb ends where the full MILP's climb ends on this small program (its own start may differ by its gap).
        assert local["end_value"] >= old["kelly"]["local"]["end_value"]-1e-6
    assert seen
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=result) == result
