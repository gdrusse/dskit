"""Cross-run comparison: reading `pipeline_runs/` back and tabulating it.

The runs the reader is tested against are produced BY the driver in-test —
a reader pinned to a hand-written fixture would keep passing after the
writer moved.
"""

import json
import os
import pathlib

import pytest

from dskit.pipeline.base import OutputsConfig
from dskit.pipeline.driver import _node_metrics, run_document
from dskit.pipeline.runs import (
    DEFAULT_RUN_ROOT,
    RunProblem,
    RunSummary,
    format_runs,
    node_metrics,
    param_at,
    scan_runs,
)
from dskit.pipeline.__main__ import main
from tests.pipeline.dochelpers import banking_document, make_registry

FIRST = "2026-01-01"
SECOND = "2026-02-01"
REPO = pathlib.Path(__file__).parents[2]


@pytest.fixture
def two_runs(tmp_path):
    """Two real runs of one series, second newer, in a fresh run root."""
    root = str(tmp_path / "pipeline_runs")
    registry = make_registry()
    results = [
        run_document(
            banking_document(outputs=OutputsConfig(run_root=root)),
            asof=asof,
            registry=registry,
        )
        for asof in (FIRST, SECOND)
    ]
    return root, results


class TestScan:
    def test_reads_every_run_newest_first(self, two_runs):
        root, results = two_runs
        runs, problems = scan_runs(root)
        assert problems == ()
        assert [r.asof for r in runs] == [SECOND, FIRST]
        assert all(isinstance(r, RunSummary) for r in runs)
        assert {r.name for r in runs} == {"synth-banking"}
        assert [r.state for r in runs] == ["ran", "ran"]
        assert [r.run_hash for r in runs] == [results[1].run_hash, results[0].run_hash]
        assert [r.run_dir for r in runs] == [results[1].run_dir, results[0].run_dir]
        assert all(len(r.document_hash) == 64 for r in runs)

    def test_metrics_come_from_the_structured_records(self, two_runs):
        root, _ = two_runs
        newest = scan_runs(root)[0][0]
        # A top-level numeric output survives _summarize into nodes/NN-*.json;
        # a `metrics` DICT does not, and is recovered from carry.json.
        assert newest.metrics["size.final_bankroll"] == pytest.approx(1040.4)
        assert newest.metrics["validate.metrics.n"] > 0
        assert "validate.metrics.loss" in newest.metrics
        # Never prose: report.md is not consulted for any of it.
        assert "verdict" not in newest.metrics

    def test_config_is_read_for_params(self, two_runs):
        root, _ = two_runs
        newest = scan_runs(root)[0][0]
        assert param_at(newest.config, "name") == "synth-banking"
        assert param_at(newest.config, "pipeline.validate.params.split") == "val"
        assert param_at(newest.config, "pipeline.nope.params.x") is None

    def test_foreign_and_partial_dirs_are_reported_not_hidden(self, two_runs):
        root, _ = two_runs
        os.mkdir(os.path.join(root, "someone-elses-dir"))
        broken = os.path.join(root, "synth-banking-2026-03-01-deadbeef")
        os.mkdir(broken)
        with open(os.path.join(broken, "result.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with open(os.path.join(root, "stray.txt"), "w", encoding="utf-8") as fh:
            fh.write("hello")
        runs, problems = scan_runs(root)
        assert len(runs) == 2  # the good runs still tabulate
        assert all(isinstance(p, RunProblem) for p in problems)
        entries = {p.entry: p.reason for p in problems}
        assert set(entries) == {
            "someone-elses-dir",
            "synth-banking-2026-03-01-deadbeef",
            "stray.txt",
        }
        assert "result.json" in entries["someone-elses-dir"]
        assert "unreadable" in entries["synth-banking-2026-03-01-deadbeef"]
        assert "not a directory" in entries["stray.txt"]

    def test_a_missing_root_refuses(self, tmp_path):
        with pytest.raises(OSError, match="no run root"):
            scan_runs(str(tmp_path / "nope"))

    def test_default_run_root_agrees_with_the_driver(self, tmp_path, monkeypatch):
        """The reader's default root and the writer's are the same place."""
        monkeypatch.chdir(tmp_path)
        result = run_document(
            banking_document(), asof=FIRST, registry=make_registry()
        )
        assert result.run_dir.startswith(os.path.abspath(DEFAULT_RUN_ROOT))
        runs, _ = scan_runs(DEFAULT_RUN_ROOT)
        assert [r.run_dir for r in runs] == [result.run_dir]


class TestTable:
    def test_tabulates_the_declared_columns(self, two_runs):
        root, _ = two_runs
        runs, _ = scan_runs(root)
        text = format_runs(runs, metrics=("size.final_bankroll",))
        header, sep, *rows = text.strip().splitlines()
        assert header.startswith("| name | asof | state | run | doc |")
        assert "size.final_bankroll" in header
        assert "validate.metrics.loss" not in header  # selection is respected
        assert len(rows) == 2
        assert rows[0].split("|")[2].strip() == SECOND  # newest first

    def test_every_metric_is_a_column_when_none_are_selected(self, two_runs):
        root, _ = two_runs
        runs, _ = scan_runs(root)
        header = format_runs(runs).splitlines()[0]
        for key in ("size.final_bankroll", "validate.metrics.loss"):
            assert key in header

    def test_params_become_columns(self, two_runs):
        root, _ = two_runs
        runs, _ = scan_runs(root)
        text = format_runs(runs, params=("pipeline.validate.params.split",))
        assert "pipeline.validate.params.split" in text.splitlines()[0]
        assert "| val |" in text

    def test_no_runs_says_so(self):
        assert "no runs" in format_runs(())


class TestMetricRulePin:
    """`node_metrics` restates the driver's private extraction rule (the
    driver may not be edited to share it — it is a pre-standard module
    cleared in its own commit). This pin is what keeps the two honest."""

    CASES = (
        {"score": 0.5, "flag": True, "name": "x", "rows": [1, 2]},
        {"metrics": {"loss": 0.25, "n": 12, "label": "val", "ok": False}},
        {"metrics": "not a dict", "n": 3},
        {},
    )

    def test_agrees_with_the_driver(self):
        for case in self.CASES:
            assert node_metrics(case) == _node_metrics(case), case

    def test_the_rule_itself(self):
        assert node_metrics(self.CASES[0]) == {"score": 0.5}
        assert node_metrics(self.CASES[1]) == {"metrics.loss": 0.25, "metrics.n": 12}
        assert node_metrics(self.CASES[2]) == {"n": 3}
        assert node_metrics(self.CASES[3]) == {}


class TestVerb:
    def test_tabulates_two_runs(self, two_runs, capsys):
        root, results = two_runs
        assert main(["runs", "--root", root]) == 0
        out = capsys.readouterr().out
        assert "| name | asof | state |" in out
        assert out.count("synth-banking") == 2
        assert SECOND in out and FIRST in out
        assert results[1].run_hash[:8] in out

    def test_selection_flags(self, two_runs, capsys):
        root, _ = two_runs
        assert (
            main(
                [
                    "runs",
                    "--root",
                    root,
                    "--metric",
                    "size.final_bankroll",
                    "--param",
                    "pipeline.validate.params.split",
                ]
            )
            == 0
        )
        out = capsys.readouterr().out
        assert "size.final_bankroll" in out
        assert "validate.metrics.loss" not in out
        assert "pipeline.validate.params.split" in out

    def test_limit_never_truncates_silently(self, two_runs, capsys):
        root, _ = two_runs
        assert main(["runs", "--root", root, "--limit", "1"]) == 0
        out = capsys.readouterr().out
        assert out.count("synth-banking") == 1
        assert "1 older run" in out

    def test_skipped_entries_are_printed(self, two_runs, capsys):
        root, _ = two_runs
        os.mkdir(os.path.join(root, "someone-elses-dir"))
        assert main(["runs", "--root", root]) == 0
        out = capsys.readouterr().out
        assert "skipped" in out and "someone-elses-dir" in out

    def test_a_missing_root_is_an_error_exit(self, tmp_path, capsys):
        assert main(["runs", "--root", str(tmp_path / "nope")]) == 1
        assert "no run root" in capsys.readouterr().out

    def test_the_verb_is_in_the_help(self, capsys):
        with pytest.raises(SystemExit):
            main(["--help"])
        assert "runs" in capsys.readouterr().out


class TestDocsCurrency:
    def test_readme_and_claude_trees_carry_the_module_and_the_verb(self):
        readme = (REPO / "dskit/pipeline/README.md").read_text(encoding="utf-8")
        claude = (REPO / "dskit/pipeline/CLAUDE.md").read_text(encoding="utf-8")
        assert "runs.py" in readme and "runs.py" in claude
        assert "dskit.pipeline runs" in readme
        what_ships = readme.split("## What ships")[1].split("\n## ")[0]
        assert "runs" in what_ships


def test_the_reader_is_on_the_package_surface():
    import dskit.pipeline as pkg

    for name in ("RunProblem", "RunSummary", "format_runs", "scan_runs"):
        assert name in pkg.__all__ and hasattr(pkg, name)


def test_records_are_read_not_the_report(two_runs):
    """The scan must survive a run dir whose report.md is gone."""
    root, _ = two_runs
    for entry in os.listdir(root):
        os.remove(os.path.join(root, entry, "report.md"))
    runs, problems = scan_runs(root)
    assert problems == () and len(runs) == 2
    assert runs[0].metrics


def test_result_json_missing_keys_is_a_skip(two_runs):
    root, _ = two_runs
    victim = os.path.join(root, sorted(os.listdir(root))[0])
    with open(os.path.join(victim, "result.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": "x"}, fh)
    runs, problems = scan_runs(root)
    assert len(runs) == 1
    assert "missing" in problems[0].reason
