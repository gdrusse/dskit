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
