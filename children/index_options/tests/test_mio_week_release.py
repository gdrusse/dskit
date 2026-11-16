"""Owner 2026-11-16: releasing what a recorded decision week built changes no result, only memory.

``WeeklyOptionPortfolioStudy._release_week`` runs after each decision week is recorded: it forgets the latest
prepared week, the hedge's cached chains and each quote index's page cache, and returns the freed heap; the
end-of-run ledger forgets each hedged book's chains after reading them. Every one is a cache of a pure read, so a
run with the releases must equal a run without them, field for field.
"""

import copy
import sqlite3
from types import SimpleNamespace

import pytest

from index_options import ledger_studies
from index_options.ledger_studies import ParityForwards, WeeklyOptionPortfolioStudy, _SQLiteQuoteView
from index_options.mio_worker import MIOMethodWorker
from test_ledger_edge_weights import _arm, _digest, _stable, _study
from test_ledger_studies import _PARITY

#: The ADR-0318 accuracy method of the running grid (static hedge), the re-struck hedge (its marks re-read the
#: chains through ``ReplayHedge.path`` twice) and the multi-start search.
METHODS = ["kelly075_ls_fast_hedged_static_acc_lp", "kelly075_ls_fast_hedged_dynamic_lp",
           "kelly075_ls_multistart3_hedged_static_lp"]


def _run(name, release, monkeypatch):
    calls = []
    real = WeeklyOptionPortfolioStudy._release_week

    def spy(study, hedge=None):
        calls.append(hedge)
        if release:
            real(study, hedge)
    monkeypatch.setattr(WeeklyOptionPortfolioStudy, "_release_week", spy)
    if not release:
        monkeypatch.setattr(ParityForwards, "release", lambda self: None)
    arm = _arm(name)
    _params, _inputs, study = _study(arm)
    result = study._method(arm, None)
    monkeypatch.undo()
    return study, result, calls


@pytest.mark.parametrize("name", METHODS)
def test_releasing_each_recorded_week_changes_no_result(name, monkeypatch):
    _, kept, _ = _run(name, False, monkeypatch)
    study, released, calls = _run(name, True, monkeypatch)
    assert _digest(_stable(released)) == _digest(_stable(kept))
    assert len(calls) == len(study.weeks) and all(isinstance(h, ledger_studies.ReplayHedge) for h in calls)
    assert study._prepared_cache == {}


def test_a_released_chain_is_rebuilt_equal():
    arm = _arm(METHODS[0])
    _params, _inputs, study = _study(arm)
    day = study.weeks[0]
    meta = study._prepare(day)[1][0]
    forwards = ParityForwards(study, _PARITY)
    first = copy.deepcopy(forwards.chain(meta["symbol"], day, meta["expiry"]))
    forward = forwards.forward(meta["symbol"], day, meta["expiry"], meta["factor"])
    assert first and forwards._rows
    forwards.release()
    assert forwards._rows == {}
    assert forwards.chain(meta["symbol"], day, meta["expiry"]) == first
    assert forwards.forward(meta["symbol"], day, meta["expiry"], meta["factor"]) == forward


def test_the_week_release_drops_every_quote_index_page_cache_and_the_prepared_week(monkeypatch):
    arm = _arm(METHODS[0])
    _params, _inputs, study = _study(arm)
    study._prepare(study.weeks[0])
    executed = []

    class Connection:
        def execute(self, sql):
            executed.append(sql)
    views = []
    for symbol in study.params["symbols"]:
        view = object.__new__(_SQLiteQuoteView)
        view.index = SimpleNamespace(connection=Connection())
        views.append(view)
        study._quotes[symbol] = view
    returned = []
    monkeypatch.setattr(ledger_studies, "_return_freed_memory", lambda: returned.append(True))
    study._release_week(None)
    assert executed == ["PRAGMA shrink_memory"]*len(views) and returned == [True]
    assert study._prepared_cache == {}


def test_shrink_memory_runs_on_a_real_read_only_connection():
    view = object.__new__(_SQLiteQuoteView)
    view.index = SimpleNamespace(connection=sqlite3.connect(":memory:"))
    view.index.connection.execute("PRAGMA query_only=ON")
    view.release_memory()
    ledger_studies._return_freed_memory()


def test_the_worker_reads_its_memory():
    now, peak = MIOMethodWorker.memory_gib()
    assert peak is None or peak > 0
    assert now is None or now > 0
    if ledger_studies.sys.platform.startswith("linux"):
        assert now is not None and peak is not None and now <= peak + 1e-3
