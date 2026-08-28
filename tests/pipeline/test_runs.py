"""Cross-run comparison: reading `pipeline_runs/` back and tabulating it.

The runs the reader is tested against are produced BY the driver in-test —
a reader pinned to a hand-written fixture would keep passing after the
writer moved.
"""

import json
import os
import pathlib
import re

import pytest

from dskit.pipeline import driver as driver_mod
from dskit.pipeline import runs as runs_mod
from dskit.pipeline.base import OutputsConfig
from dskit.pipeline.document import NodeSpec
from dskit.pipeline.driver import run_document
from dskit.pipeline.markdown import MISSING, pipe_table, render_cell
from dskit.pipeline.node import Node
from dskit.pipeline.runs import (
    CARRY_FILE,
    CONFIG_FILE,
    DEFAULT_RUN_ROOT,
    NODES_DIR,
    RESULT_FILE,
    RunProblem,
    RunSummary,
    format_runs,
    node_metrics,
    param_at,
    resolve_run_root,
    scan_runs,
)
from dskit.pipeline.__main__ import main
from tests.pipeline.dochelpers import banking_document, make_registry

FIRST = "2026-01-01"
SECOND = "2026-02-01"
REPO = pathlib.Path(__file__).parents[2]


class DivergedScalarNode(Node):
    """A node whose TOP-LEVEL score diverges (routine for a logloss).

    ``inf`` is not JSON, so the record keeps only the text ``"inf"`` and
    ``carry.json`` keeps nothing — the reader must say so rather than
    render the same blank a node that measured nothing renders.
    """

    role = "transform"

    def run(self, ctx, inputs):
        """Return a diverged top-level loss beside a real count."""
        return {"loss": float("inf"), "n": 3}


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

    def test_a_null_result_json_is_skipped_with_a_reason(self, tmp_path):
        """`null` is the one JSON payload that parses and is not a dict;
        it must not slip out as a RunProblem with a BLANK reason — the
        verb prints the reason verbatim after a colon."""
        root = str(tmp_path / "pipeline_runs")
        os.makedirs(os.path.join(root, "nulldir"))
        path = os.path.join(root, "nulldir", RESULT_FILE)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("null")
        _runs, problems = scan_runs(root)
        (problem,) = problems
        assert problem.reason, "a skip with no reason is a silent skip"
        assert RESULT_FILE in problem.reason
        assert "not an object" in problem.reason

    def test_default_run_root_agrees_with_the_driver(self, tmp_path, monkeypatch):
        """The reader's default root and the writer's are the same place."""
        monkeypatch.chdir(tmp_path)
        result = run_document(
            banking_document(), asof=FIRST, registry=make_registry()
        )
        assert result.run_dir.startswith(os.path.abspath(DEFAULT_RUN_ROOT))
        runs, _ = scan_runs(DEFAULT_RUN_ROOT)
        assert [r.run_dir for r in runs] == [result.run_dir]

    def test_only_one_module_names_the_default_run_root(self):
        """Every writer of a run root resolves it through ONE name — a
        second copy is how the walk-forward default drifts unnoticed."""
        hits = sorted(
            path.name
            for path in (REPO / "dskit").rglob("*.py")
            if '"./pipeline_runs"' in path.read_text(encoding="utf-8")
        )
        assert hits == ["runs.py"]

    def test_resolve_run_root_is_what_the_declaration_means(self, tmp_path):
        assert resolve_run_root("") == os.path.abspath(DEFAULT_RUN_ROOT)
        assert resolve_run_root(None) == os.path.abspath(DEFAULT_RUN_ROOT)
        assert resolve_run_root(str(tmp_path)) == str(tmp_path)


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
    """One rule, one name: the driver's write-side extraction and the
    reader's are the SAME function object, not two copies held together
    by a case list that can omit the knob someone adds next."""

    CASES = (
        {"score": 0.5, "flag": True, "name": "x", "rows": [1, 2]},
        {"metrics": {"loss": 0.25, "n": 12, "label": "val", "ok": False}},
        {"metrics": "not a dict", "n": 3},
        {},
    )

    def test_the_driver_uses_this_very_function(self):
        assert driver_mod._node_metrics is node_metrics

    def test_the_rule_itself(self):
        assert node_metrics(self.CASES[0]) == {"score": 0.5}
        assert node_metrics(self.CASES[1]) == {"metrics.loss": 0.25, "metrics.n": 12}
        assert node_metrics(self.CASES[2]) == {"n": 3}
        assert node_metrics(self.CASES[3]) == {}


class TestSummarizedMetrics:
    """A `metrics` dict the writer could not carry survives in the record
    only as ``{"type": "dict", "len": n}``. That marker is a note about a
    measurement, not a measurement."""

    @pytest.fixture
    def diverged_root(self, tmp_path):
        """A run whose one scored node reports a non-finite loss — which
        `driver._carryable` refuses, so carry.json cannot restore it."""
        root = str(tmp_path / "pipeline_runs")
        pipeline = {
            "events": NodeSpec(uses="synth-events", params={"n_events": 8}),
            "diverged": NodeSpec(
                uses="tests.pipeline.test_driver:InfMetricsNode",
                inputs={"events": "$events.events"},
            ),
        }
        run_document(
            banking_document(
                pipeline=pipeline, outputs=OutputsConfig(run_root=root)
            ),
            asof=FIRST,
            registry=make_registry(),
        )
        return root

    def test_the_marker_is_never_mined_for_a_number(self, diverged_root):
        (run,), problems = scan_runs(diverged_root)
        assert problems == ()
        assert "diverged.metrics.len" not in run.metrics
        assert run.metrics["diverged.n"] == 3  # real metrics still tabulate

    def test_the_unavailable_metrics_are_said_out_loud(self, diverged_root):
        (run,), _ = scan_runs(diverged_root)
        assert any("diverged" in note and "metrics" in note for note in run.notes)

    def test_the_verb_prints_the_note(self, diverged_root, capsys):
        """The note exists to be READ: a mechanism the only user-facing
        surface never prints is the silent truncation it was written to
        prevent."""
        assert main(["runs", "--root", diverged_root]) == 0
        out = capsys.readouterr().out
        assert "metrics.len" not in out
        _table, marker, printed = out.partition("notes")
        assert marker, f"the verb printed no notes section:\n{out}"
        assert "diverged" in printed and "metrics" in printed

    def test_a_real_metrics_dict_is_not_mistaken_for_the_marker(self):
        assert node_metrics({"metrics": {"type": 2, "len": 3}}) == {
            "metrics.type": 2,
            "metrics.len": 3,
        }


class TestLostMeasurements:
    """Everything the records hold but the reader cannot turn into a
    number is NAMED. A blank cell that means "diverged", one that means
    "the record was truncated" and one that means "never measured" are
    three different facts, and only the last is routine."""

    @pytest.fixture
    def diverged_scalar_root(self, tmp_path):
        """A run whose scored node diverges in a TOP-LEVEL output."""
        root = str(tmp_path / "pipeline_runs")
        pipeline = {
            "events": NodeSpec(uses="synth-events", params={"n_events": 8}),
            "diverged": NodeSpec(
                uses="tests.pipeline.test_runs:DivergedScalarNode",
                inputs={"events": "$events.events"},
            ),
        }
        run_document(
            banking_document(
                pipeline=pipeline, outputs=OutputsConfig(run_root=root)
            ),
            asof=FIRST,
            registry=make_registry(),
        )
        return root

    def test_a_diverged_top_level_number_is_said_out_loud(
        self, diverged_scalar_root
    ):
        (run,), problems = scan_runs(diverged_scalar_root)
        assert problems == ()
        assert "diverged.loss" not in run.metrics  # "inf" is text, not a number
        assert run.metrics["diverged.n"] == 3
        assert any("diverged.loss" in note and "inf" in note for note in run.notes)

    def test_the_verb_prints_the_diverged_scalar_note(
        self, diverged_scalar_root, capsys
    ):
        assert main(["runs", "--root", diverged_scalar_root]) == 0
        _table, marker, printed = capsys.readouterr().out.partition("notes")
        assert marker
        assert "diverged.loss" in printed

    def test_an_unreadable_node_record_is_named_and_recovered(self, two_runs):
        """A truncated record must not take the node's carried numbers
        with it, and must not pass for a node that measured nothing."""
        root, results = two_runs
        record = os.path.join(results[1].run_dir, NODES_DIR, "10-size.json")
        assert os.path.isfile(record)
        with open(record, "w", encoding="utf-8") as fh:
            fh.write("{trunc")
        newest = scan_runs(root)[0][0]
        assert newest.run_dir == results[1].run_dir
        assert newest.metrics["size.final_bankroll"] == pytest.approx(1040.4)
        assert any("10-size.json" in note for note in newest.notes)

    def test_a_missing_nodes_dir_is_named(self, two_runs):
        root, results = two_runs
        victim = results[1].run_dir
        for entry in os.listdir(os.path.join(victim, NODES_DIR)):
            os.remove(os.path.join(victim, NODES_DIR, entry))
        os.rmdir(os.path.join(victim, NODES_DIR))
        newest = scan_runs(root)[0][0]
        assert any(NODES_DIR in note for note in newest.notes)

    def test_an_unreadable_config_is_named_not_read_as_an_absent_knob(
        self, two_runs
    ):
        """`param_at` reports None for a knob a document never declared —
        a legitimate answer. An unreadable config.json is not that."""
        root, results = two_runs
        with open(
            os.path.join(results[1].run_dir, CONFIG_FILE), "w", encoding="utf-8"
        ) as fh:
            fh.write("{trunc")
        newest = scan_runs(root)[0][0]
        assert any(CONFIG_FILE in note for note in newest.notes)

    def test_an_unreadable_carry_is_named(self, two_runs):
        root, results = two_runs
        with open(
            os.path.join(results[1].run_dir, CARRY_FILE), "w", encoding="utf-8"
        ) as fh:
            fh.write("{trunc")
        newest = scan_runs(root)[0][0]
        assert any(CARRY_FILE in note for note in newest.notes)


class TestRunDirLayout:
    """The reader's names for the run-dir layout and the writer's are one
    agreement. Rename `nodes/` writer-side with nothing pinning it and
    every run tabulates blank, with no problem and no note."""

    def test_the_writer_writes_the_names_the_reader_reads(self, two_runs):
        _root, results = two_runs
        run_dir = results[0].run_dir
        for name in (RESULT_FILE, CONFIG_FILE, CARRY_FILE):
            assert os.path.isfile(os.path.join(run_dir, name)), name
        assert os.path.isdir(os.path.join(run_dir, NODES_DIR))

    def test_the_pipeline_writers_do_not_restate_the_layout(self):
        """driver.py and resolve.py write THROUGH the reader's names."""
        for module in ("driver.py", "resolve.py"):
            source = (REPO / "dskit/pipeline" / module).read_text(encoding="utf-8")
            for name in (RESULT_FILE, CONFIG_FILE, CARRY_FILE):
                assert f'"{name}"' not in source, f"{module} restates {name}"

    def test_the_other_run_dir_reader_reads_the_same_names(self):
        """dskit/assets/ingest.py reads run dirs too and cannot import
        the pipeline package (the tiers are independent), so the
        agreement is pinned instead: move a name and this goes red."""
        source = (REPO / "dskit/assets/ingest.py").read_text(encoding="utf-8")
        assert f'"{RESULT_FILE}"' in source
        assert f'"{NODES_DIR}"' in source

    def test_the_other_run_dir_reader_requires_the_same_keys(self):
        """Both readers require the same `result.json` key set, or the
        two disagree about what counts as a run — assets would ingest a
        directory the `runs` table lists as skipped, or vice versa. The
        agreement is a value in two places, so it is pinned: change
        either side's set and this goes red."""
        source = (REPO / "dskit/assets/ingest.py").read_text(encoding="utf-8")
        match = re.search(r"for key in \(([^)]*)\)", source)
        assert match, "ingest.py no longer states its required result.json keys"
        theirs = set(re.findall(r'"(\w+)"', match.group(1)))
        assert theirs == set(runs_mod._REQUIRED)

    def test_a_blank_or_null_identity_field_is_not_a_run(self, two_runs):
        """Key PRESENCE is not enough: an empty run_hash renders the
        MISSING dash in an identity cell, and a null document_hash
        renders the literal ``None`` — a fabricated 8-char "hash prefix".
        The other reader (`ingest._check_str`) requires non-empty
        strings, and so does this one."""
        root, results = two_runs
        path = os.path.join(results[0].run_dir, RESULT_FILE)
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        payload["run_hash"] = ""
        payload["document_hash"] = None
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        runs, problems = scan_runs(root)
        assert len(runs) == 1
        (problem,) = problems
        assert "run_hash" in problem.reason
        assert "document_hash" in problem.reason

    def test_a_result_without_a_document_hash_is_not_a_run(self, two_runs):
        """A blank identity cell would read as a rendering failure; the
        other run-dir reader requires the field, and so does this one."""
        root, results = two_runs
        path = os.path.join(results[0].run_dir, RESULT_FILE)
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        del payload["document_hash"]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        runs, problems = scan_runs(root)
        assert len(runs) == 1
        assert "document_hash" in problems[0].reason


class TestSharedRenderer:
    """Every markdown table this package emits is built by ONE renderer."""

    def test_only_one_module_builds_a_markdown_table(self):
        """The pattern is the SEPARATOR ITSELF, not a quoted copy of it:
        a hand-built `|---|---|---|` is exactly what must be caught."""
        hits = sorted(
            path.name
            for path in (REPO / "dskit").rglob("*.py")
            if "---|" in path.read_text(encoding="utf-8")
        )
        assert hits == ["markdown.py"]

    def test_the_run_report_node_table_goes_through_the_renderer(self):
        """A hand-built table restates the renderer's decisions and
        drifts: `str(0.000123456789)` where render_cell gives 6 s.f., and
        no escaping where the renderer escapes."""
        lines = driver_mod._node_table(
            ("a|b", "plain"),
            lambda key: "transform",
            {"a|b": "ok", "plain": "not_run"},
            {"a|b": 0.000123456789},
        )
        assert lines[0] == "| node | role | status | seconds |"
        assert r"a\|b" in lines[2]
        assert "0.000123457" in lines[2]
        assert lines[3].endswith(f"| {MISSING} |")  # never ran, never timed

    def test_the_walk_forward_table_goes_through_the_renderer(self):
        lines = driver_mod._fold_table(
            [
                {
                    "cutoff": "2026-01-01",
                    "state": "ran|x",
                    "score": 0.000123456789,
                    "run_dir": "/r/demo-2026-01-01-0badc0de",
                },
                {
                    "cutoff": "2026-02-01",
                    "state": "error",
                    "score": None,
                    "run_dir": "",
                },
            ]
        )
        assert lines[0] == "| fold cutoff | state | score | run |"
        assert r"ran\|x" in lines[2]
        assert "0.000123457" in lines[2]
        assert "`demo-2026-01-01-0badc0de`" in lines[2]
        assert lines[3].count(MISSING) == 2  # no score, no run dir

    def test_the_empty_string_is_not_a_blank_cell(self):
        """markdown.py's own rule: a blank cell is indistinguishable from
        a rendering bug, so nothing may render as one."""
        assert render_cell("") == MISSING

    def test_a_boolean_reads_the_same_everywhere(self):
        run = RunSummary(
            run_dir="/x/demo-2026-01-01-0badc0de",
            name="demo",
            asof=FIRST,
            state="ran",
            run_hash="0" * 64,
            document_hash="1" * 64,
            config={"strict": True},
        )
        assert render_cell(True) == "yes"
        assert "| yes |" in format_runs([run], params=("strict",))

    def test_a_pipe_in_a_value_cannot_shift_the_columns(self):
        run = RunSummary(
            run_dir="/x/demo-2026-01-01-0badc0de",
            name="demo",
            asof=FIRST,
            state="ran",
            run_hash="0" * 64,
            document_hash="1" * 64,
            metrics={"scorer.score": 1},
            config={"tag": "a|b"},
        )
        header, _sep, row = format_runs([run], params=("tag",)).splitlines()
        delimiters = r"(?<!\\)\|"  # the escaped pipe is content, not a column
        assert len(re.findall(delimiters, row)) == len(re.findall(delimiters, header))
        assert r"a\|b" in row

    def test_a_row_of_the_wrong_width_is_refused(self):
        with pytest.raises(ValueError, match="2 column"):
            pipe_table(("a", "b"), [[1, 2, 3]])

    def test_a_newline_in_a_value_cannot_break_the_row(self):
        """A `|` opens a phantom COLUMN; a newline opens a phantom ROW —
        and the width check counts cells before the join, so it cannot
        catch it. A two-line `notes` in a config is ordinary; it must
        render as one line a reader can follow."""
        for text in ("a\nb", "a\r\nb", "a\rb"):
            cell = render_cell(text)
            assert "\n" not in cell and "\r" not in cell
            assert "a" in cell and "b" in cell  # both halves survive
        assert "\n" not in pipe_table(("x", "y"), [["a\nb", 1]])[2]
        run = RunSummary(
            run_dir="/x/demo-2026-01-01-0badc0de",
            name="demo",
            asof=FIRST,
            state="ran",
            run_hash="0" * 64,
            document_hash="1" * 64,
            config={"notes": "line1\nline2"},
        )
        lines = format_runs([run], params=("notes",)).splitlines()
        assert len(lines) == 3  # header, separator, ONE row


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

    def test_a_non_positive_limit_is_refused_not_ignored(self, two_runs, capsys):
        """`--limit 0` asked for nothing; printing everything is the
        opposite answer, and a negative slices from the wrong end."""
        root, _ = two_runs
        for bad in ("0", "-5"):
            with pytest.raises(SystemExit):
                main(["runs", "--root", root, "--limit", bad])
            assert "at least 1" in capsys.readouterr().err

    def test_a_metric_no_scanned_run_reported_is_refused(self, two_runs, capsys):
        """A typo'd --metric would render a confident column of dashes
        that reads as "these runs never measured it" — the same
        default-deny as --limit: a typo is an error, not a silent
        default."""
        root, _ = two_runs
        assert main(["runs", "--root", root, "--metric", "size.final_bankrol"]) == 1
        out = capsys.readouterr().out
        assert "size.final_bankrol" in out  # the refusal names the key
        assert MISSING not in out  # and no table of dashes is printed

    def test_a_metric_only_an_unshown_run_reported_still_renders(
        self, tmp_path, capsys
    ):
        """The check is against every SCANNED run, not the --limit'd
        view: a metric only an older run measured is a real key, and a
        column of dashes over the shown runs is then a true statement."""
        root = str(tmp_path / "pipeline_runs")
        registry = make_registry()
        run_document(
            banking_document(outputs=OutputsConfig(run_root=root)),
            asof=FIRST,
            registry=registry,
        )
        pipeline = {
            "events": NodeSpec(uses="synth-events", params={"n_events": 8}),
            "diverged": NodeSpec(
                uses="tests.pipeline.test_runs:DivergedScalarNode",
                inputs={"events": "$events.events"},
            ),
        }
        run_document(
            banking_document(
                pipeline=pipeline, outputs=OutputsConfig(run_root=root)
            ),
            asof=SECOND,
            registry=registry,
        )
        argv = ["runs", "--root", root, "--limit", "1"]
        assert main([*argv, "--metric", "size.final_bankroll"]) == 0
        out = capsys.readouterr().out
        assert "size.final_bankroll" in out
        table = out.partition("\n\n")[0]  # the notes block also names runs
        assert table.count("synth-banking") == 1  # only the newest is shown
        assert FIRST not in table

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
        # markdown.py is a new module too — a tree that omits it sends the
        # next agent to write a fourth table renderer.
        assert "markdown.py" in readme and "markdown.py" in claude
        assert "dskit.pipeline runs" in readme
        what_ships = readme.split("## What ships")[1].split("\n## ")[0]
        assert "runs" in what_ships

    def test_the_repo_wide_command_list_carries_the_verb(self):
        """CLAUDE.md's Commands block is the canonical CLI inventory an
        agent reads first; a verb absent from it does not exist to them."""
        root_claude = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
        commands = root_claude.split("## Commands")[1].split("\n## ")[0]
        assert "dskit.pipeline runs" in commands


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
