"""The run-identified table writer: one file, hence one published stream, per run."""

import json
import os

import pytest
from dskit.pipeline.kinds_table import RecordsWrite

from crypto_trading.run_write import RunStampedWrite


class Ctx:
    def __init__(self, run_dir, asof="2026-10-06"):
        self.run_dir = str(run_dir)
        self.asof = asof


def node(tmp_path, **over):
    params = {"path": str(tmp_path / "table-{run}.jsonl"), "source": "test rows", **over}
    return RunStampedWrite("write", params)


def test_the_path_names_the_run_and_every_row_says_which_run_it_came_from(tmp_path):
    run_dir = tmp_path / "runs" / "crypto-features-15m-2026-10-06-535ceca6"
    rows = [{"ticker": "A", "decision_ms": 1}, {"ticker": "B", "decision_ms": 2}]
    out = node(tmp_path).run(Ctx(run_dir), {"records": rows})
    expected = tmp_path / "table-crypto-features-15m-2026-10-06-535ceca6.jsonl"
    assert out["path"] == str(expected) and out["provenance"]["path"] == str(expected)
    written = [json.loads(line) for line in expected.read_text(encoding="utf-8").splitlines()]
    assert written == [{**r, "run_id": "crypto-features-15m-2026-10-06-535ceca6"} for r in rows]
    assert rows == [{"ticker": "A", "decision_ms": 1}, {"ticker": "B", "decision_ms": 2}], "inputs are not mutated"


def test_two_runs_write_two_files(tmp_path):
    for suffix in ("aaaaaaaa", "bbbbbbbb"):
        node(tmp_path).run(Ctx(tmp_path / f"runs/doc-2026-10-06-{suffix}"), {"records": [{"x": 1}]})
    assert sorted(os.listdir(tmp_path)) == ["table-doc-2026-10-06-aaaaaaaa.jsonl", "table-doc-2026-10-06-bbbbbbbb.jsonl"]


def test_it_is_a_records_write_so_its_guarantees_are_kept(tmp_path):
    assert issubclass(RunStampedWrite, RecordsWrite)
    run = Ctx(tmp_path / "runs/doc-2026-10-06-aaaaaaaa")
    node(tmp_path).run(run, {"records": [{"x": 1}]})
    with pytest.raises(FileExistsError):
        node(tmp_path).run(run, {"records": [{"x": 1}]})  # overwrite is not declared
    node(tmp_path, overwrite=True).run(run, {"records": [{"x": 1}]})
    with pytest.raises(ValueError, match="NaN|finite|JSON"):
        node(tmp_path, overwrite=True).run(run, {"records": [{"x": float("nan")}]})


def test_a_path_without_the_run_placeholder_is_refused_because_it_would_shadow_the_last_table(tmp_path):
    with pytest.raises(Exception, match=r"\{run\}"):
        RunStampedWrite("write", {"path": str(tmp_path / "table.jsonl"), "source": "x"})
    with pytest.raises(Exception, match=r"\{run\}"):
        RunStampedWrite("write", {"path": str(tmp_path / "{run}-{run}.jsonl"), "source": "x"})


def test_a_run_name_that_cannot_be_a_stream_name_is_refused_by_name(tmp_path):
    with pytest.raises(ValueError, match="Doc-2026"):
        node(tmp_path).run(Ctx(tmp_path / "runs/Doc-2026-10-06-aaaaaaaa"), {"records": [{"x": 1}]})
    with pytest.raises(ValueError, match="run frame"):
        node(tmp_path).run(None, {"records": [{"x": 1}]})


def test_default_deny_is_inherited(tmp_path):
    with pytest.raises(Exception, match="surprise"):
        node(tmp_path, surprise=1)
