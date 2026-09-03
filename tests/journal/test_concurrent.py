"""Eight processes append at once; every row survives with its own id.

The regression: ``append_action`` read the ledger, chose the next id,
then rewrote the whole CSV. Two agents overlapping in that window both
read N rows, both picked ``A00(N+1)``, and the second ``os.replace``
dropped the first agent's row. This test fails without the writer lock
(rows lost / ids duplicated) and passes with it.
"""

import multiprocessing
import os

import pytest

from dskit.journal.base import JournalError, locked
from dskit.journal.locate import init_journal
from dskit.journal.record import append_action
from dskit.journal.store import read_actions

WRITERS = 8
ROWS_EACH = 3


def _child(tmp_path):
    """A minimal initialized child tree."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "configs").mkdir()
    return init_journal(str(tmp_path))


def _writer(child_root, index, barrier):
    """Record ``ROWS_EACH`` rows, all writers starting together."""
    os.environ["DSKIT_JOURNAL_TESTS"] = "1"
    barrier.wait(timeout=30)
    for n in range(ROWS_EACH):
        append_action(
            "research",
            f"w{index}-r{n}",
            inputs=f"writer {index}",
            start=child_root,
        )


def test_concurrent_appends_lose_no_row(tmp_path):
    """Every row from every process lands, with a distinct id."""
    root = _child(tmp_path)
    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(WRITERS)
    procs = [
        ctx.Process(target=_writer, args=(root.child_root, i, barrier))
        for i in range(WRITERS)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=120)
    assert [p.exitcode for p in procs] == [0] * WRITERS

    rows = read_actions(root)
    steps = sorted(row.step for row in rows)
    expected = sorted(
        f"w{i}-r{n}" for i in range(WRITERS) for n in range(ROWS_EACH)
    )
    ids = [row.id for row in rows]
    assert steps == expected, "rows were lost by a concurrent rewrite"
    assert len(set(ids)) == len(ids), f"duplicate ids: {ids}"
    assert sorted(ids) == [f"A{n:04d}" for n in range(1, len(ids) + 1)]


def test_lock_is_reentrant_within_one_thread(tmp_path):
    """``append_action`` takes the lock, then the store takes it again."""
    root = _child(tmp_path)
    with locked(root.decisioning):
        with locked(root.decisioning):
            pass
        action = append_action(
            "research", "nested", start=root.child_root
        )
    assert action is not None
    assert [row.step for row in read_actions(root)] == ["nested"]


def test_a_held_lock_makes_a_second_writer_wait_then_refuse(tmp_path):
    """The wait is bounded: a stuck holder times out, it does not hang."""
    root = _child(tmp_path)
    ctx = multiprocessing.get_context("fork")
    ready = ctx.Event()
    release = ctx.Event()
    holder = ctx.Process(
        target=_hold, args=(root.decisioning, ready, release)
    )
    holder.start()
    try:
        assert ready.wait(timeout=30)
        with pytest.raises(JournalError) as excinfo:
            with locked(root.decisioning, timeout=0.3):
                pass
        assert "still held" in str(excinfo.value)
    finally:
        release.set()
        holder.join(timeout=30)


def _hold(directory, ready, release):
    """Hold the lock until told to let go."""
    with locked(directory):
        ready.set()
        release.wait(timeout=60)


@pytest.fixture(autouse=True)
def _record_under_pytest(monkeypatch):
    """These tests write into a throwaway child on purpose."""
    monkeypatch.setenv("DSKIT_JOURNAL_TESTS", "1")
