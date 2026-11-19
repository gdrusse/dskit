"""Owner 2026-11-16, round 2: freeing a finished stage one before the climb changes no result, only memory.

``WeeklyOptionPortfolioStudy._local_search_week`` drops its stage-one solver and dollar program once stage one is
read and calls ``_release_stage_one`` (collect, then return freed heap) before the climb builds its scorer; the worker
puts arrow's buffers on the system allocator. A run with the release must equal a run without it, field for field,
and the stage-one model must be gone by the time the climb's scorer exists.
"""

import gc

import pytest

from dskit.pipeline.libs import pyomo
from index_options.ledger_studies import WeeklyOptionPortfolioStudy
from index_options.mio_worker import MIOMethodWorker
from test_ledger_edge_weights import _arm, _digest, _stable, _study

#: The ADR-0318 accuracy method of the running grid (static hedge) and the re-struck hedge.
METHODS = ["kelly075_ls_fast_hedged_static_acc_lp", "kelly075_ls_fast_hedged_dynamic_lp"]


def _models_alive():
    return sum(1 for o in gc.get_objects() if type(o).__name__ == "ConcreteModel")


def _run(name, release, monkeypatch):
    calls, alive = [], []
    real = WeeklyOptionPortfolioStudy._release_stage_one

    def spy():
        calls.append(True)
        if release:
            real()
    monkeypatch.setattr(WeeklyOptionPortfolioStudy, "_release_stage_one", staticmethod(spy))
    score = pyomo.TransportActionScore

    class Watched(score):
        def __init__(self, program, params):
            if calls and len(alive) < len(calls):   # the climb's scorer: the first one after this week's release
                alive.append(_models_alive())
            super().__init__(program, params)
    monkeypatch.setattr(pyomo, "TransportActionScore", Watched)
    arm = _arm(name)
    _params, _inputs, study = _study(arm)
    gc.disable()   # only the release may collect: the fixture is too small for the collector to run on its own
    try:
        result = study._method(arm, None)
    finally:
        gc.enable()
        monkeypatch.undo()
    return result, calls, alive


@pytest.mark.parametrize("name", METHODS)
def test_releasing_stage_one_before_the_climb_changes_no_result(name, monkeypatch):
    kept, kept_calls, _ = _run(name, False, monkeypatch)
    released, calls, alive = _run(name, True, monkeypatch)
    assert _digest(_stable(released)) == _digest(_stable(kept))
    assert calls and len(calls) == len(kept_calls)
    assert alive == [0]*len(calls), "the stage-one model is collected before the climb's scorer is built"


def _spy_release(monkeypatch):
    """Count the releases (the real one still runs): every check below reads stage one AFTER it is dropped."""
    calls = []
    real = WeeklyOptionPortfolioStudy._release_stage_one

    def spy():
        calls.append(True)
        real()
    monkeypatch.setattr(WeeklyOptionPortfolioStudy, "_release_stage_one", staticmethod(spy))
    return calls


@pytest.mark.parametrize("mode", ["previous_week", "previous_climb"])
def test_a_live_warm_week_reads_its_start_and_remembers_its_book_after_the_release(monkeypatch, mode):
    from test_ledger_studies import SHOCK10, _local_world

    # The fixture's weeks share no expiry: carry a leg by right, strike and side so week two is offered a book.
    monkeypatch.setattr(WeeklyOptionPortfolioStudy, "_contract_key",
                        staticmethod(lambda meta, item: (meta["symbol"], item["right"], item["strike"], item["sign"])))
    remembered = []
    remember = WeeklyOptionPortfolioStudy._warm_remember

    def spy(self, method_id, warm, groups, metadata, quantities):
        remembered.append(groups)
        return remember(self, method_id, warm, groups, metadata, quantities)
    monkeypatch.setattr(WeeklyOptionPortfolioStudy, "_warm_remember", spy)
    calls = _spy_release(monkeypatch)
    params, data, method = _local_world(search={"start": {"rho": 0., "shock": {**SHOCK10, "loss_fraction": 1.},
                                                          "warm": mode}})
    result = WeeklyOptionPortfolioStudy(params, data)._method(method, None)
    weeks = [w for w in result["decisions"] if "local" in w.get("kelly", {})]
    assert len(weeks) >= 2 and len(calls) == len(weeks) == len(remembered)
    records = [w["kelly"]["local"]["stage1_warm"] for w in weeks]
    assert all(r["mode"] == mode for r in records)
    assert records[0]["reason"] == "no_previous_book" and records[1]["reason"] != "no_previous_book"
    for groups in remembered:   # stage one's groups (its radius) or the climb's program groups: never the dropped None
        assert groups and (mode == "previous_climb" or all(g["radius"] == 0. for g in groups))
    remembered.clear()
    again = WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=result)
    assert again == result and remembered and all(remembered)


def test_a_live_relax_fix_week_records_its_dollar_solve_after_the_release(monkeypatch):
    from test_ledger_studies import _local_world
    from test_local_start_relax_fix import RECORD_KEYS, RELAX

    calls = _spy_release(monkeypatch)
    params, data, method = _local_world(RELAX)
    result = WeeklyOptionPortfolioStudy(params, data)._method(method, None)
    weeks = [w for w in result["decisions"] if "local" in w.get("kelly", {})]
    assert weeks and len(calls) == len(weeks)
    for week in weeks:
        solve = week["kelly"]["local"]["stage1_solve"]
        assert set(solve) == RECORD_KEYS and solve["strategy"] == "relax_fix"
    assert WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=result) == result


def test_the_worker_puts_arrow_on_the_system_allocator_unless_told_otherwise(monkeypatch):
    import pyarrow as pa

    previous = pa.default_memory_pool()
    try:
        monkeypatch.delenv("ARROW_DEFAULT_MEMORY_POOL", raising=False)
        assert MIOMethodWorker.use_system_arrow_pool() == "system"
        pa.set_memory_pool(previous)
        monkeypatch.setenv("ARROW_DEFAULT_MEMORY_POOL", "anything")
        assert MIOMethodWorker.use_system_arrow_pool() == previous.backend_name, "an explicit choice wins"
    finally:
        pa.set_memory_pool(previous)
