"""The universal execution file: run / plan / validate / nodemap."""

import json
import os

import pytest

from dskit.pipeline.__main__ import main
from dskit.pipeline.document import load_document

EXAMPLE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "examples",
    "pipeline",
    "nodemap-minimal.json",
)


def test_example_document_loads_and_hashes_stably():
    doc = load_document(EXAMPLE)
    assert doc.name == "nodemap-minimal"
    assert load_document(EXAMPLE).hash == doc.hash


def test_run_executes_the_example_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # outputs.run_root "" -> ./pipeline_runs
    code = main(["run", os.path.abspath(EXAMPLE), "--asof", "2026-01-01"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("**RAN")
    runs = os.listdir(tmp_path / "pipeline_runs")
    assert len(runs) == 1 and runs[0].startswith("nodemap-minimal-2026-01-01-")

    # The same run again: the occupied run dir refuses, exit 1.
    assert main(["run", os.path.abspath(EXAMPLE), "--asof", "2026-01-01"]) == 1
    assert "already exists" in capsys.readouterr().out


def test_plan_prints_the_resolved_dag(capsys):
    code = main(["plan", EXAMPLE])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["order"][0] == "dataset"
    assert payload["nodes"]["validate"]["role"] == "score"
    assert payload["document_hash"] == load_document(EXAMPLE).hash


def test_plan_names_every_problem(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "name": "bad",
                "pipeline": {
                    "a": {"uses": "no-such-kind"},
                    "b": {"uses": "no.such.module:Thing"},
                },
            }
        )
    )
    assert main(["plan", str(bad)]) == 1
    out = capsys.readouterr().out
    assert "pipeline.a" in out and "pipeline.b" in out


def test_validate_dispatches_on_document_shape(capsys):
    assert main(["validate", EXAMPLE]) == 0
    out = capsys.readouterr().out
    assert "nodes: 5" in out and "hash:" in out
    legacy = os.path.join(os.path.dirname(EXAMPLE), "synthetic.json")
    assert main(["validate", legacy]) == 0
    assert "venue:" in capsys.readouterr().out


def test_validate_still_fails_broken_files(tmp_path, capsys):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert main(["validate", str(broken)]) == 1


def test_run_refuses_clock_documents(tmp_path, capsys):
    doc = json.loads(open(EXAMPLE, encoding="utf-8").read())
    doc["clock"] = {"increment": "epoch"}
    clocked = tmp_path / "clocked.json"
    clocked.write_text(json.dumps(doc))
    assert main(["run", str(clocked), "--asof", "2026-01-01"]) == 1
    assert "I-222" in capsys.readouterr().out


def test_nodemap_demo_runs_the_banking_document(capsys):
    code = main(["nodemap"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("**RAN — all 11 node(s) completed**")
    assert "| edge_test | stat_test | ok |" in out


@pytest.mark.parametrize("missing", ["ghost.json"])
def test_run_and_plan_fail_cleanly_on_missing_files(missing, capsys):
    assert main(["run", missing]) == 1
    assert main(["plan", missing]) == 1
