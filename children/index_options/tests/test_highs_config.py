"""ADR-0304: the declared HiGHS block of a kelly method and the offline configuration sweep (synthetic fixtures only)."""

import copy
import csv
import json
import math
from types import SimpleNamespace

import pytest

from index_options import highs_config, ledger_studies
from index_options.highs_config import (COLUMNS, DEFAULT_ID, HIGHS_EXCLUDED, HIGHS_OPTIONS, HighsConfigSweep,
                                        candidate_id, candidates_from_grid, check_candidates, freeze, highs_problems,
                                        load_candidates)
from test_ledger_studies import (CFG_FORMULATIONS, KELLY_HIGHS_FROZEN, TOP_FORMULATIONS, _equal_but_last_bits,
                                 _for_kelly, _kelly_fixture, _weekly_portfolio_fixture)

Study = ledger_studies.WeeklyOptionPortfolioStudy
BLOCK = {"random_seed": 7, "mip_heuristic_effort": .3, "presolve": "on", "mip_detect_symmetry": False,
         "simplex_strategy": 4}
#: HiGHS 1.15.1's own values of the options a defaults block names (the probe in the first test checks them).
DEFAULTS = {"random_seed": 0, "mip_heuristic_effort": .05, "presolve": "choose", "mip_detect_symmetry": True}


def _highs():
    highspy = pytest.importorskip("highspy")
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    # HiGHS's task scheduler is process-wide and sized by the FIRST solve's threads: a default (auto) solve here
    # would make every later threads-1 solve of the session refuse, so this helper pins one thread as the documents do.
    h.setOptionValue("threads", 1)
    return highspy, h


def test_every_whitelisted_option_exists_in_the_installed_highs_with_the_declared_domain():
    highspy, h = _highs()
    ok = highspy.HighsStatus.kOk
    for name, (kind, domain) in HIGHS_OPTIONS.items():
        status, default = h.getOptionValue(name)
        assert status == ok, f"{name} is not a HiGHS option"
        if kind in ("int", "double"):
            low, high = domain
            assert low <= default <= high, (name, default)
            for edge in (low, high):
                assert h.setOptionValue(name, float(edge) if kind == "double" else edge) == ok, (name, edge)
            step = 1 if kind == "int" else .5
            assert h.setOptionValue(name, (low-step) if kind == "int" else float(low-step)) != ok, (name, "below")
            if high < 2147483647:
                assert h.setOptionValue(name, (high+step) if kind == "int" else float(high+step)) != ok, (name, "above")
        elif kind == "choice":
            assert default in domain, (name, default)
            for choice in domain:
                assert h.setOptionValue(name, choice) == ok, (name, choice)
        else:
            assert type(default) is bool, name
        assert not highs_problems({"random_seed": 0, name: default}), name
    assert set(DEFAULTS) <= set(HIGHS_OPTIONS)
    for name, value in DEFAULTS.items():
        assert h.getOptionValue(name)[1] == value or highspy.Highs().getOptionValue(name)[1] == value
    assert not set(HIGHS_OPTIONS) & set(HIGHS_EXCLUDED)


@pytest.mark.parametrize("choice", HIGHS_OPTIONS["solver"][1])
def test_no_allowed_solver_choice_drops_integrality(choice):
    """Older HiGHS documented that solver simplex/ipm IGNORES a MIP's integrality: that would change the feasible set.

    max x + y with 2x + 2y <= 3 over integers is 1 (the relaxation's is 1.5); the installed HiGHS must keep it at 1
    under every allowed ``solver`` value, or the option leaves the whitelist.
    """
    import numpy as np

    highspy, h = _highs()
    h.setOptionValue("solver", choice)
    h.addVars(2, np.array([0., 0.]), np.array([5., 5.]))
    h.changeColsCost(2, np.array([0, 1]), np.array([-1., -1.]))
    h.addRow(-highspy.kHighsInf, 3., 2, np.array([0, 1]), np.array([2., 2.]))
    h.changeColsIntegrality(2, np.array([0, 1]), np.array([highspy.HighsVarType.kInteger]*2))
    h.run()
    assert h.getModelStatus() == highspy.HighsModelStatus.kOptimal
    assert h.getInfo().objective_function_value == pytest.approx(-1.)
    assert all(float(v).is_integer() for v in h.getSolution().col_value)


@pytest.mark.parametrize("block", [BLOCK, {"random_seed": 0}, {"random_seed": 1, "solver": "ipm"},
                                   {"random_seed": 0, "mip_heuristic_effort": 1}, KELLY_HIGHS_FROZEN,
                                   {"random_seed": 3, "mip_pscost_minreliable": 0, "mip_lp_age_limit": 32767,
                                    "mip_pool_soft_limit": 1, "mip_allow_restart": False,
                                    "mip_heuristic_run_rins": False}])
def test_a_valid_block_is_accepted(block):
    assert highs_problems(block) == []


@pytest.mark.parametrize("block,match", [
    (None, "nonempty dict"), ({}, "nonempty dict"), ([], "nonempty dict"),
    ({"mip_heuristic_effort": .1}, "random_seed"),
    ({"random_seed": 0, "mip_rel_gap": .01}, "gap"),
    ({"random_seed": 0, "mip_abs_gap": 1.}, "gap"),
    ({"random_seed": 0, "mip_feasibility_tolerance": 1e-9}, "feasibility"),
    ({"random_seed": 0, "primal_feasibility_tolerance": 1e-9}, "feasibility"),
    ({"random_seed": 0, "dual_feasibility_tolerance": 1e-9}, "feasibility"),
    ({"random_seed": 0, "time_limit": 100.}, "time_limit"),
    ({"random_seed": 0, "threads": 1}, "thread"),
    ({"random_seed": 0, "parallel": "off"}, "thread"),
    ({"random_seed": 0, "mip_min_cliquetable_entries_for_parallelism": 10}, "thread"),
    ({"random_seed": 0, "mip_max_nodes": 10}, "node limit"),
    ({"random_seed": 0, "run_crossover": "off"}, "crossover"),
    ({"random_seed": 0, "mip_heuristik_effort": .1}, "not a configurable"),
    ({"random_seed": 0, "objective_bound": 1.}, "gap"),
    ({"random_seed": -1}, "random_seed"), ({"random_seed": 1.}, "random_seed"), ({"random_seed": True}, "random_seed"),
    ({"random_seed": 0, "mip_heuristic_effort": 1.5}, "mip_heuristic_effort"),
    ({"random_seed": 0, "mip_heuristic_effort": float("nan")}, "mip_heuristic_effort"),
    ({"random_seed": 0, "mip_heuristic_effort": True}, "mip_heuristic_effort"),
    ({"random_seed": 0, "mip_detect_symmetry": 1}, "mip_detect_symmetry"),
    ({"random_seed": 0, "presolve": "bogus"}, "presolve"),
    ({"random_seed": 0, "simplex_strategy": 2}, "simplex_strategy"),
    ({"random_seed": 0, "simplex_strategy": 1.}, "simplex_strategy"),
    ({"random_seed": 0, "solver": "pdlp"}, "solver"),
    ({"random_seed": 0, "mip_pool_soft_limit": 0}, "mip_pool_soft_limit"),
])
def test_a_bad_block_is_refused_by_name(block, match):
    problems = highs_problems(block)
    assert problems and match in "; ".join(problems)
    params, _ = _kelly_fixture(highs=block)
    with pytest.raises(ValueError, match="kelly.highs|invalid fixed method"):
        Study.validate_configuration(params)


def test_the_document_must_pin_one_thread_and_may_not_also_set_a_block_option():
    params, _ = _kelly_fixture(highs=BLOCK)
    Study.validate_configuration(params)
    for change, match in ((lambda o: o.pop("threads"), "threads 1"), (lambda o: o.update(threads=0), "threads 1"),
                          (lambda o: o.update(threads=True), "threads 1"),
                          (lambda o: o.update(presolve="off"), "both declare")):
        bad = copy.deepcopy(params)
        change(bad["solver"]["solver_options"])
        with pytest.raises(ValueError, match=match):
            Study.validate_configuration(bad)


def test_the_cfg_formulation_is_the_uncert_formulation_plus_a_placeholder_block():
    for name, method in CFG_FORMULATIONS.items():
        params, _ = _weekly_portfolio_fixture()
        params["methods"] = [{"id": name, **method}]
        _for_kelly(params)
        Study.validate_configuration(params)
    cfg, uncert = CFG_FORMULATIONS["kelly025_top100_cfg_lp"], TOP_FORMULATIONS["kelly025_top100_uncert_lp"]
    assert {k: v for k, v in cfg.items() if k != "kelly"} == {k: v for k, v in uncert.items() if k != "kelly"}
    assert {k: v for k, v in cfg["kelly"].items() if k != "highs"} == uncert["kelly"]
    assert cfg["kelly"]["highs"] == KELLY_HIGHS_FROZEN == {"random_seed": 0}, "a placeholder until the train sweep"


def test_without_a_block_the_solver_params_are_the_documents_own():
    params, _ = _kelly_fixture()
    kelly = params["methods"][0]["kelly"]
    assert Study._kelly_solver_params(params["solver"], kelly) is params["solver"]
    assert Study._kelly_solver_params(params["solver"], None) is params["solver"]
    capped = Study._kelly_solver_params(params["solver"], {**kelly, "max_groups": 3})
    assert capped == {**params["solver"], "group_screen_keep": 3}
    merged = Study._kelly_solver_params(params["solver"], {**kelly, "highs": {"random_seed": 1, "mip_heuristic_effort": 1}})
    assert merged["solver_options"] == {**params["solver"]["solver_options"], "random_seed": 1, "mip_heuristic_effort": 1.}
    assert type(merged["solver_options"]["mip_heuristic_effort"]) is float
    assert params["solver"]["solver_options"].get("random_seed") is None, "the document's block is not mutated"


def _spy_solves(monkeypatch):
    """Record, after every HiGHS solve of every week, the declared options as the highspy object holds them."""
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    seen = []
    resolve = TransportPortfolioSolve._resolve_solver

    def spied(self):
        solver = resolve(self)
        solve = solver.solve

        def counted(*args, **kwargs):
            out = solve(*args, **kwargs)
            highs = highs_config._highs_object(solver)
            seen.append({name: highs_config._option_value(highs, name) for name in BLOCK})
            return out
        solver.solve = counted
        return solver
    monkeypatch.setattr(TransportPortfolioSolve, "_resolve_solver", spied)
    return seen


def test_a_declared_block_reaches_every_highs_solve_and_is_recorded(monkeypatch):
    seen = _spy_solves(monkeypatch)
    params, inputs = _kelly_fixture(highs=BLOCK)
    result = Study(params, inputs)._method(params["methods"][0], None)
    solved = [d for d in result["decisions"] if "selection" in d]
    assert solved and len(seen) >= 2*len(solved), "the relaxation and the support search at least, every week"
    expected = {**BLOCK, "mip_heuristic_effort": .3}
    assert all(reading == expected for reading in seen), seen[:2]
    assert all(d["kelly"]["highs"] == BLOCK for d in result["decisions"] if "kelly" in d)
    seen.clear()
    params, inputs = _kelly_fixture()
    plain = Study(params, inputs)._method(params["methods"][0], None)
    assert seen and all(r["mip_heuristic_effort"] == .05 and r["presolve"] == "choose" for r in seen), "HiGHS defaults"
    assert all("highs" not in d.get("kelly", {}) for d in plain["decisions"]), "no field without the block"


def test_a_block_of_highs_defaults_changes_nothing_but_the_recorded_field():
    params, inputs = _kelly_fixture()
    plain = Study(params, inputs)._method(params["methods"][0], None)
    params, inputs = _kelly_fixture(highs=DEFAULTS)
    declared = Study(params, inputs)._method(params["methods"][0], None)
    assert declared["method"]["kelly"] == {**plain["method"]["kelly"], "highs": DEFAULTS}
    for a, b in zip(plain["decisions"], declared["decisions"]):
        if "kelly" in b:
            b = {**b, "kelly": {k: v for k, v in b["kelly"].items() if k != "highs"}}
        assert _equal_but_last_bits({**a, "certificates": None}, {**b, "certificates": None})
    assert [d.get("status") for d in plain["decisions"]] == [d.get("status") for d in declared["decisions"]]


def test_a_declared_block_replays_through_the_recorded_and_derived_routes():
    from index_options.mio_worker import MIOMethodWorker

    params, inputs = _kelly_fixture(highs=BLOCK)
    method = params["methods"][0]
    solved = Study(params, inputs)._method(method, None)
    assert any(d.get("status") == "selected" for d in solved["decisions"])
    assert Study(params, inputs)._method(method, None, recorded=solved) == solved
    actions = MIOMethodWorker.actions(solved, Study(params, inputs).weeks)
    assert _equal_but_last_bits(Study(params, inputs)._method(method, None, derived=actions), solved)
    tampered = copy.deepcopy(solved)
    week = next(d for d in tampered["decisions"] if "selection" in d)
    week["kelly"]["highs"] = {**BLOCK, "random_seed": 8}
    with pytest.raises(ValueError, match="HiGHS configuration differs"):
        Study(params, inputs)._method(method, None, recorded=tampered)
    other = copy.deepcopy(method)
    other["kelly"]["highs"] = {**BLOCK, "random_seed": 8}
    with pytest.raises(ValueError, match="recorded method"):
        Study(params, inputs)._method(other, None, recorded=solved)


def test_grid_candidates_are_ordered_deterministic_and_sampled_by_a_declared_seed():
    grid = {"mip_heuristic_effort": [.05, .3], "presolve": ["choose", "off"], "mip_detect_symmetry": [True, False]}
    full = candidates_from_grid(grid, {"random_seed": 0})
    assert full[0] == {"id": DEFAULT_ID, "highs": None} and len(full) == 9
    assert [c["highs"] for c in full[1:3]] == [
        {"random_seed": 0, "mip_detect_symmetry": True, "mip_heuristic_effort": .05, "presolve": "choose"},
        {"random_seed": 0, "mip_detect_symmetry": True, "mip_heuristic_effort": .05, "presolve": "off"}]
    assert full == candidates_from_grid(dict(reversed(list(grid.items()))), {"random_seed": 0}), "option order is sorted"
    assert all(c["id"] == candidate_id(c["highs"]) for c in full)
    sampled = candidates_from_grid(grid, {"random_seed": 0}, sample=3, seed=11)
    assert sampled == candidates_from_grid(grid, {"random_seed": 0}, sample=3, seed=11) and len(sampled) == 4
    assert [c["id"] for c in sampled[1:]] == [c["id"] for c in full[1:] if c in sampled[1:]], "grid order kept"
    assert load_candidates({"grid": grid, "base": {"random_seed": 0}, "sample": 3, "seed": 11}) == sampled
    for bad in ({"grid": {"presolve": []}}, {"grid": {"presolve": ["on", "on"]}, "base": {"random_seed": 0}},
                {"grid": {"presolve": ["on"]}}, {"grid": {"threads": [1]}, "base": {"random_seed": 0}},
                {"grid": grid, "base": {"random_seed": 0}, "sample": 3}, {"grids": grid}):
        with pytest.raises(ValueError):
            load_candidates(bad)
    with pytest.raises(ValueError, match="twice"):
        load_candidates({"candidates": [BLOCK, dict(BLOCK)]})
    with pytest.raises(ValueError, match="default"):
        check_candidates([{"id": candidate_id(BLOCK), "highs": BLOCK}])
    with pytest.raises(ValueError, match="digest"):
        check_candidates([{"id": DEFAULT_ID, "highs": None}, {"id": "mine", "highs": BLOCK}])


def _sweep(tmp_path, name, **kwargs):
    params, inputs = _kelly_fixture()
    study = Study(params, inputs)
    candidates = load_candidates({"candidates": [BLOCK]})
    weeks = kwargs.pop("weeks", None) or list(study.weeks)
    return HighsConfigSweep(study, "kelly", candidates, weeks, tmp_path/name, **kwargs), study


def _performance(rows):
    return [{k: v for k, v in r.items() if k not in ("seconds", "solve_seconds")} for r in rows]


def test_the_sweep_writes_one_ordered_row_per_week_and_candidate_with_solver_fields_only(tmp_path, monkeypatch):
    sweep, study = _sweep(tmp_path, "a")
    table = sweep.run()
    rows = table["rows"]
    assert [(r["week"], r["candidate"]) for r in rows] == [(w, c) for w in study.weeks
                                                           for c in (DEFAULT_ID, candidate_id(BLOCK))]
    assert all(set(r) == set(COLUMNS) for r in rows)
    measured = [r for r in rows if r["outcome"] != "no_program"]
    assert measured and all(r["solves"] >= 2 and r["error"] is None and r["seconds"] >= 0 for r in measured)
    assert all(r["options_verified"] is True for r in measured if r["candidate"] != DEFAULT_ID)
    assert all(r["options_verified"] is None for r in rows if r["candidate"] == DEFAULT_ID)
    assert {r["outcome"] for r in measured} <= {"certified", "relaxation_certified", "declined", "relaxation_failed"}
    for w in {r["week"] for r in measured}:
        pair = [r for r in measured if r["week"] == w]
        assert len(pair) == 2 and math.isclose(pair[0]["objective"], pair[1]["objective"], rel_tol=.1, abs_tol=1.)
    with open(tmp_path/"a"/highs_config.TABLE_CSV, newline="") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames) == COLUMNS and len(list(reader)) == len(rows)
    on_disk = json.loads((tmp_path/"a"/highs_config.TABLE_JSON).read_text())
    assert on_disk["rows"] == json.loads(json.dumps(rows)) and on_disk["method"] == "kelly"
    banned = {"pnl", "equity", "fills", "books", "trades", "realized", "settlement"}
    assert not banned & set(COLUMNS) and not banned & set(table)
    again, _ = _sweep(tmp_path, "b")
    assert _performance(again.run()["rows"]) == _performance(rows), "a second run measures the same table"
    calls = []
    monkeypatch.setattr(HighsConfigSweep, "measure", lambda self, *a: calls.append(a) or pytest.fail("re-measured"))
    resumed, _ = _sweep(tmp_path, "a")
    assert resumed.run()["rows"] == rows and not calls, "a finished journal resumes without a solve"


def test_the_sweep_resumes_a_cut_journal_and_refuses_another_sweeps(tmp_path):
    sweep, study = _sweep(tmp_path, "a")
    full = sweep.run()["rows"]
    journal = tmp_path/"a"/highs_config.JOURNAL_FILE
    lines = journal.read_text().splitlines()
    journal.write_text("\n".join(lines[:2])+"\n"+lines[2][:10]+"\n")
    resumed, _ = _sweep(tmp_path, "a")
    assert _performance(resumed.run()["rows"]) == _performance(full)
    assert len(journal.read_text().splitlines()) == len(lines)
    other, _ = _sweep(tmp_path, "a", weeks=[study.weeks[0]])
    with pytest.raises(ValueError, match="another sweep"):
        other.run()


def test_the_derived_path_measures_the_same_programs_without_a_path_solve(tmp_path):
    from index_options.mio_worker import MIOMethodWorker

    params, inputs = _kelly_fixture()
    solved = Study(params, inputs)._method(params["methods"][0], None)
    actions = MIOMethodWorker.actions(solved, Study(params, inputs).weeks)
    local, _ = _sweep(tmp_path, "solve")
    derived, _ = _sweep(tmp_path, "derived", derived={"format": "x", "actions": actions})
    a, b = local.run(), derived.run()
    assert (a["path"], b["path"]) == ("solve", "derived")
    assert _performance(a["rows"]) == _performance(b["rows"])


def test_the_sweep_refuses_holdout_weeks_non_kelly_methods_and_foreign_weeks(tmp_path):
    kelly = {"time_limit": 1, "gap": {"absolute": 1., "relative": .1}}
    stub = SimpleNamespace(params={"methods": [{"id": "k", "kelly": kelly}, {"id": "n"}],
                                   "solver": {"solver_options": {"threads": 1}}},
                           weeks=["2021-06-28", "2021-07-06"])
    cands = load_candidates({"candidates": [BLOCK]})
    with pytest.raises(ValueError, match="holdout"):
        HighsConfigSweep(stub, "k", cands, ["2021-07-06"], tmp_path)
    with pytest.raises(ValueError, match="kelly method"):
        HighsConfigSweep(stub, "n", cands, ["2021-06-28"], tmp_path)
    with pytest.raises(ValueError, match="decision weeks"):
        HighsConfigSweep(stub, "k", cands, ["2021-06-21"], tmp_path)
    stub.params["solver"]["solver_options"]["presolve"] = "on"
    with pytest.raises(ValueError, match="both declare"):
        HighsConfigSweep(stub, "k", cands, ["2021-06-28"], tmp_path)
    stub.params["solver"]["solver_options"] = {}
    with pytest.raises(ValueError, match="threads 1"):
        HighsConfigSweep(stub, "k", [{"id": DEFAULT_ID, "highs": None}], ["2021-06-28"], tmp_path)


def _table(rows, candidates, quarter_weeks):
    return {"format": highs_config.SWEEP_FORMAT, "method": "m", "candidates": candidates, "time_limit": 100,
            "gap": {"absolute": 1., "relative": .1},
            "rows": [{"week": w, "quarter": highs_config.quarter_of(w), "candidate": c, "certified": cert,
                      "seconds": s, "outcome": "certified" if cert else "declined"}
                     for (w, c), (cert, s) in rows.items() if w in quarter_weeks]}


def test_the_freeze_rule_needs_a_margin_and_no_fewer_certified_weeks_and_reads_only_train_weeks():
    fast, slow = {"random_seed": 1}, {"random_seed": 2}
    cands = load_candidates({"candidates": [fast, slow]})
    f, s = candidate_id(fast), candidate_id(slow)
    weeks = ["2019-10-07", "2020-01-06"]
    base = {(weeks[0], DEFAULT_ID): (True, 50.), (weeks[1], DEFAULT_ID): (False, 100.),
            (weeks[0], f): (True, 20.), (weeks[1], f): (False, 100.),
            (weeks[0], s): (True, 49.), (weeks[1], s): (False, 100.)}
    out = freeze([_table(base, cands, weeks[:1]), _table(base, cands, weeks[1:])])
    assert out["chosen"] == f and out["highs"] == fast
    assert out["scores"][DEFAULT_ID]["mean_log_time"] == pytest.approx((math.log(50)+math.log(100))/2)
    within = {**base, (weeks[0], f): (True, 48.)}
    assert freeze([_table(within, cands, weeks)])["chosen"] == DEFAULT_ID, "inside the margin the default stands"
    fewer = {**base, (weeks[1], DEFAULT_ID): (True, 90.)}
    assert freeze([_table(fewer, cands, weeks)])["chosen"] == DEFAULT_ID, "fewer certified weeks never wins"
    tie = {**base, (weeks[0], s): (True, 20.)}
    assert freeze([_table(tie, cands, weeks)])["chosen"] == f, "a tie goes to the earlier candidate"
    with pytest.raises(ValueError, match="train quarters"):
        freeze([_table({**base, ("2021-01-04", DEFAULT_ID): (True, 1.)}, cands, weeks+["2021-01-04"])])
    with pytest.raises(ValueError, match="one row per train week"):
        freeze([_table({k: v for k, v in base.items() if k != (weeks[1], s)}, cands, weeks)])
    with pytest.raises(ValueError, match="one sweep"):
        freeze([_table(base, cands, weeks), {**_table(base, cands, weeks), "time_limit": 50}])


def test_the_cli_freezes_tables_and_sweeps_a_loaded_study(tmp_path, monkeypatch):
    from index_options.mio_worker import MIOMethodWorker

    sweep, study = _sweep(tmp_path, "direct")
    direct = sweep.run()
    (tmp_path/"cands.json").write_text(json.dumps({"candidates": [BLOCK]}))
    (tmp_path/"weeks.json").write_text(json.dumps(list(study.weeks)))
    params, inputs = _kelly_fixture()
    monkeypatch.setattr(MIOMethodWorker, "load", lambda self: {"weekly": Study(params, inputs)})
    code = highs_config.main(["sweep", "--bundle", str(tmp_path/"bundle"), "--method", "kelly", "--candidates",
                              str(tmp_path/"cands.json"), "--weeks", str(tmp_path/"weeks.json"), "--work-dir",
                              str(tmp_path/"work"), "--out", str(tmp_path/"cli")])
    assert code == 0
    table = json.loads((tmp_path/"cli"/highs_config.TABLE_JSON).read_text())
    assert _performance(table["rows"]) == _performance(json.loads(json.dumps(direct["rows"])))
    week = next(r["week"] for r in table["rows"] if r["outcome"] != "no_program")
    rows = [{**r, "week": "2019-10-07", "quarter": "2019Q4"} for r in table["rows"] if r["week"] == week]
    (tmp_path/"train.json").write_text(json.dumps({**table, "rows": rows}))
    assert highs_config.main(["freeze", str(tmp_path/"train.json"), "--out", str(tmp_path/"frozen.json")]) == 0
    frozen = json.loads((tmp_path/"frozen.json").read_text())
    assert frozen["chosen"] in (DEFAULT_ID, candidate_id(BLOCK)) and frozen["quarters"] == ["2019Q4", "2020Q1"]
