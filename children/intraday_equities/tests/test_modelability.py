"""P10 modelability orchestration tests (ADR-0081, ADR-0093).

The child keeps only what is domain — which document, which cohort,
which tag — and hands the fold argv batch to dskit's
``BoundedFoldRunner`` with its documented knob and the frozen 17 GiB
cap. Pinned here: the batch the seam receives, the resume rule (a
journaled fold is never re-spawned; artifacts without journal evidence
refuse), the row reading through ``single_fold_row``, the journalled
width, and the preflight's one-child measurement through
``measure_one`` with no per-fold memory record left behind.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from dskit.pipeline.document import PipelineDocument, load_document

from intraday_equities import modelability

ADAPTER_ARGV = ["-m", "dskit.pipeline", "walkforward"]


def _document(run_root, count=3):
    return PipelineDocument.from_obj(
        {
            "name": "bounded-test",
            "pipeline": {
                "data": {
                    "uses": "dskit.pipeline.synthetic_nodes:SynthEvents",
                    "params": {"n_events": 2, "n_instruments": 1},
                }
            },
            "walkforward": {
                "objective": "$data.metrics.score",
                "select": "max",
                "first": "2025-01-03",
                "step_days": 7,
                "count": count,
                "val_days": 7,
                "embargo_days": 1,
                "train_days": 30,
            },
            "outputs": {"run_root": str(run_root)},
        }
    )


def test_single_fold_documents_preserve_pipeline_and_cover_cutoffs(tmp_path):
    document = _document(tmp_path)
    cutoffs = modelability._fold_cutoffs(document.walkforward)
    assert cutoffs == ["2025-01-03", "2025-01-10", "2025-01-17"]
    parts = [
        modelability._single_fold_document(document, cutoff, index)
        for index, cutoff in enumerate(cutoffs)
    ]
    assert [part.walkforward.count for part in parts] == [1, 1, 1]
    assert [part.walkforward.first for part in parts] == cutoffs
    assert all(part.pipeline == document.pipeline for part in parts)


def _finish(argv, cwd, journaled):
    """Leave the one-ran-row summary a real fold walk would, and journal it."""
    path, asof = argv[4], argv[6]
    document = load_document(path)
    summary = modelability._summary_dir(document, asof, cwd)
    os.makedirs(summary, exist_ok=True)
    cutoff = document.walkforward.first
    payload = {
        "state": "ran",
        "document_hash": document.hash,
        "folds": [
            {
                "cutoff": cutoff,
                "run_dir": os.path.join(summary, "run"),
                "state": "ran",
                "score": float(int(cutoff[-2:])),
            }
        ],
    }
    with open(os.path.join(summary, "walkforward.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    journaled.add(summary)
    return summary


class FakeRunner:
    """A seam double: records its construction and the batches it receives."""

    instances = []
    journaled = set()
    peak = 123_456_789
    failure = None

    def __init__(self, memory_limit_bytes, workers=None, env_var="DSKIT_FOLD_WORKERS"):
        self.memory_limit_bytes = memory_limit_bytes
        self.env_var = env_var
        self.workers = 7 if workers is None else workers
        self.batches = []
        self.measured = []
        type(self).instances.append(self)

    def run(self, commands, cwd=None, env=None):
        self.batches.append((commands, cwd, env))
        if type(self).failure is not None:
            raise type(self).failure
        for argv in commands:
            _finish(argv, cwd, type(self).journaled)
        return [subprocess.CompletedProcess(argv, 0, "", "") for argv in commands]

    def measure_one(self, argv, cwd=None, env=None):
        self.measured.append((argv, cwd, env))
        _finish(argv, cwd, type(self).journaled)
        return subprocess.CompletedProcess(argv, 0, "", ""), type(self).peak


def _harness(tmp_path, monkeypatch):
    journaled = set()
    FakeRunner.instances = []
    FakeRunner.journaled = journaled
    FakeRunner.peak = 123_456_789
    FakeRunner.failure = None
    monkeypatch.setattr(modelability, "_child_root", lambda _ctx: str(tmp_path))
    monkeypatch.setattr(
        modelability,
        "_journal_confirms_walk",
        lambda summary, *_args: summary in journaled,
    )
    monkeypatch.setattr(
        modelability,
        "append_action",
        lambda *_a, **kw: journaled.add(kw.get("outputs")),
    )
    monkeypatch.setattr(modelability, "BoundedFoldRunner", FakeRunner)
    ctx = SimpleNamespace(
        source_path=str(tmp_path / "configs" / "run.json"),
        run_dir=str(tmp_path / "stage"),
        asof="2026-02-28",
        document=SimpleNamespace(hash="f" * 64),
    )
    return ctx, journaled


def test_bounded_walk_hands_one_argv_per_fold_to_the_seam(tmp_path, monkeypatch):
    ctx, _journaled = _harness(tmp_path, monkeypatch)
    document = _document(tmp_path / "runs")
    summary = modelability._run_bounded_walk(ctx, document, "gate1-h01")
    (runner,) = FakeRunner.instances
    assert runner.memory_limit_bytes == modelability._MEMORY_LIMIT
    assert runner.env_var == "INTRADAY_EQUITIES_FOLD_WORKERS"
    ((commands, cwd, env),) = runner.batches
    assert cwd == str(tmp_path)
    assert env is None
    derived = tmp_path / "stage" / "derived"
    assert [argv[:4] for argv in commands] == [[sys.executable, *ADAPTER_ARGV]] * 3
    assert [argv[4] for argv in commands] == [
        str(derived / f"gate1-h01-part-{index:02d}.json") for index in range(3)
    ]
    assert [argv[5:] for argv in commands] == [
        ["--asof", "2026-02-28", "--adapter", "intraday_equities"]
    ] * 3
    payload = json.loads((Path(summary) / "walkforward.json").read_text())
    assert payload["state"] == "ran"
    assert [row["cutoff"] for row in payload["folds"]] == [
        "2025-01-03",
        "2025-01-10",
        "2025-01-17",
    ]
    assert payload["aggregate"]["n_folds"] == 3
    assert payload["aggregate"]["best_score"] == 17.0
    assert (Path(summary) / "report.md").is_file()


def test_bounded_walk_never_respawns_a_journaled_fold(tmp_path, monkeypatch):
    ctx, journaled = _harness(tmp_path, monkeypatch)
    document = _document(tmp_path / "runs")
    cutoffs = modelability._fold_cutoffs(document.walkforward)
    # Fold 1 already ran and was journaled by an earlier invocation.
    part = modelability._single_fold_document(document, cutoffs[1], 1)
    done = modelability._summary_dir(part, ctx.asof, str(tmp_path))
    argv = [sys.executable, *ADAPTER_ARGV, "", "--asof", ctx.asof]
    os.makedirs(tmp_path / "stage" / "derived", exist_ok=True)
    path = tmp_path / "stage" / "derived" / "pre.json"
    path.write_text(modelability._canonical(part))
    argv[4] = str(path)
    assert _finish(argv, str(tmp_path), journaled) == done
    modelability._run_bounded_walk(ctx, document, "tag")
    ((commands, _cwd, _env),) = FakeRunner.instances[0].batches
    assert [load_document(argv[4]).walkforward.first for argv in commands] == [
        cutoffs[0],
        cutoffs[2],
    ]


def test_bounded_walk_with_every_fold_journaled_never_calls_the_seam(
    tmp_path, monkeypatch
):
    ctx, journaled = _harness(tmp_path, monkeypatch)
    document = _document(tmp_path / "runs")
    first = modelability._run_bounded_walk(ctx, document, "tag")
    journaled.discard(first)
    import shutil

    shutil.rmtree(first)
    again = modelability._run_bounded_walk(ctx, document, "tag")
    assert again == first
    assert (Path(again) / "walkforward.json").is_file()
    assert [len(runner.batches) for runner in FakeRunner.instances] == [1, 0]


def test_bounded_walk_refuses_a_fold_with_artifacts_but_no_journal(
    tmp_path, monkeypatch
):
    ctx, _journaled = _harness(tmp_path, monkeypatch)
    document = _document(tmp_path / "runs")
    cutoffs = modelability._fold_cutoffs(document.walkforward)
    part = modelability._single_fold_document(document, cutoffs[0], 0)
    orphan = modelability._summary_dir(part, ctx.asof, str(tmp_path))
    os.makedirs(orphan)
    (Path(orphan) / "walkforward.json").write_text("{}")
    try:
        modelability._run_bounded_walk(ctx, document, "tag")
    except ValueError as error:
        assert "journal evidence" in str(error)
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("orphaned fold artifacts were accepted")
    assert all(not runner.batches for runner in FakeRunner.instances)


def test_bounded_walk_reads_each_row_through_single_fold_row(tmp_path, monkeypatch):
    ctx, _journaled = _harness(tmp_path, monkeypatch)
    document = _document(tmp_path / "runs")
    seen = []
    real = modelability.single_fold_row

    def spy(summary_dir, cutoff):
        seen.append(cutoff)
        return real(summary_dir, cutoff)

    monkeypatch.setattr(modelability, "single_fold_row", spy)
    modelability._run_bounded_walk(ctx, document, "tag")
    assert seen == modelability._fold_cutoffs(document.walkforward)


def test_bounded_walk_journals_the_width_the_seam_resolved_and_the_cap(
    tmp_path, monkeypatch
):
    ctx, journaled = _harness(tmp_path, monkeypatch)
    notes = []

    def journal(*_args, **kwargs):
        notes.append(kwargs.get("notes", ""))
        journaled.add(kwargs.get("outputs"))

    monkeypatch.setattr(modelability, "append_action", journal)
    modelability._run_bounded_walk(ctx, _document(tmp_path / "runs"), "tag")
    (note,) = notes
    assert "fold_workers=7" in note
    assert f"memory_limit_bytes={modelability._MEMORY_LIMIT}" in note
    assert "fold_processes=isolated" in note
    assert "state=ran folds=3" in note


def test_an_explicit_width_reaches_the_seam(tmp_path, monkeypatch):
    ctx, _journaled = _harness(tmp_path, monkeypatch)
    modelability._run_bounded_walk(ctx, _document(tmp_path / "runs"), "tag", workers=4)
    assert FakeRunner.instances[0].workers == 4


def test_a_finished_walk_is_reused_without_touching_the_seam(tmp_path, monkeypatch):
    ctx, journaled = _harness(tmp_path, monkeypatch)
    document = _document(tmp_path / "runs")
    summary = modelability._summary_dir(document, ctx.asof, str(tmp_path))
    os.makedirs(summary)
    (Path(summary) / "walkforward.json").write_text("{}")
    journaled.add(summary)
    assert modelability._run_bounded_walk(ctx, document, "tag") == summary
    assert all(not runner.batches for runner in FakeRunner.instances)


def test_a_failing_batch_surfaces_and_writes_no_summary(tmp_path, monkeypatch):
    ctx, _journaled = _harness(tmp_path, monkeypatch)
    FakeRunner.failure = RuntimeError("fold 0 exited 1; output tail:\nboom")
    document = _document(tmp_path / "runs")
    try:
        modelability._run_bounded_walk(ctx, document, "tag")
    except RuntimeError as error:
        assert "boom" in str(error)
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("the seam's failure did not surface")
    summary = modelability._summary_dir(document, ctx.asof, str(tmp_path))
    assert not os.path.exists(summary)


def test_the_child_knob_and_cap_are_one_value_each(monkeypatch):
    monkeypatch.delenv("INTRADAY_EQUITIES_FOLD_WORKERS", raising=False)
    assert modelability._WORKERS_ENV == "INTRADAY_EQUITIES_FOLD_WORKERS"
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    assert modelability._WORKERS_ENV in readme
    runner = modelability._runner()
    assert type(runner).__name__ == "BoundedFoldRunner"
    assert runner.memory_limit_bytes == modelability._MEMORY_LIMIT
    assert runner.env_var == modelability._WORKERS_ENV
    assert runner.workers == 1
    assert modelability._runner(workers=3).workers == 3


def _preflight(tmp_path, monkeypatch):
    ctx, journaled = _harness(tmp_path, monkeypatch)
    ctx.document = _document(tmp_path / "runs", count=1)
    monkeypatch.setattr(
        modelability,
        "_feature_cache_info",
        lambda _ctx: ("./cache", str(tmp_path / "cache"), "a" * 64),
    )
    monkeypatch.setattr(
        modelability,
        "_derived_document",
        lambda _ctx, name, horizon, **_kw: _document(tmp_path / "runs", count=1),
    )
    stage = modelability.MemoryPreflightStage(
        "memory", {"memory_limit_bytes": modelability._MEMORY_LIMIT}
    )
    return ctx, journaled, stage


def test_the_memory_preflight_measures_its_one_walk_through_the_seam(
    tmp_path, monkeypatch
):
    ctx, _journaled, stage = _preflight(tmp_path, monkeypatch)
    out = stage.run(ctx, {})
    (runner,) = FakeRunner.instances
    ((argv, cwd, env),) = runner.measured
    assert not runner.batches, "the preflight went through run, not measure_one"
    assert cwd == str(tmp_path) and env is None
    assert argv[:4] == [sys.executable, *ADAPTER_ARGV]
    assert out["peak_rss_bytes"] == 123_456_789
    assert out["limit_bytes"] == modelability._MEMORY_LIMIT
    assert out["passed"] is True
    assert out["summary_dir"] == modelability._summary_dir(
        load_document(argv[4]), ctx.asof, str(tmp_path)
    )
    stray = [
        path
        for path in tmp_path.rglob("memory-preflight.json")
        if path.parent.name != "derived"
    ]
    assert not stray, f"a per-fold memory record was persisted: {stray}"


def test_the_memory_preflight_refuses_a_peak_at_or_above_the_limit(
    tmp_path, monkeypatch
):
    ctx, _journaled, stage = _preflight(tmp_path, monkeypatch)
    FakeRunner.peak = modelability._MEMORY_LIMIT
    try:
        stage.run(ctx, {})
    except MemoryError as error:
        assert str(modelability._MEMORY_LIMIT) in str(error)
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("a peak at the limit passed")


def test_the_memory_preflight_refuses_to_reuse_a_finished_walk(tmp_path, monkeypatch):
    # A measurement needs a fresh spawn: a finished walk has no reading
    # to recover now that the per-fold record is gone (ADR-0093).
    ctx, journaled, stage = _preflight(tmp_path, monkeypatch)
    document = _document(tmp_path / "runs", count=1)
    summary = modelability._summary_dir(document, ctx.asof, str(tmp_path))
    os.makedirs(summary)
    (Path(summary) / "walkforward.json").write_text("{}")
    journaled.add(summary)
    try:
        stage.run(ctx, {})
    except ValueError as error:
        assert "fresh spawn" in str(error)
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("a finished walk was reused as a measurement")
    assert all(not runner.measured for runner in FakeRunner.instances)


def test_p10_config_freezes_the_exact_fit_and_new_source_universe():
    root = Path(__file__).parents[1]
    config = json.loads((root / "configs/run-p10-modelability.json").read_text())
    universe = json.loads((root / "configs/universe-p10.json").read_text())
    assets = modelability._ASSETS
    assert universe["symbols"] == assets
    assert "META" not in assets
    assert config["pipeline"]["scan"]["params"]["fit_symbols"] == assets
    assert config["pipeline"]["scan"]["params"]["label_residual_self"] == "raw"
    assert config["pipeline"]["features"]["params"]["dtype"] == "float32"
    assert config["pipeline"]["features"]["params"]["cache_dir"].endswith("-v5")
    assert config["pipeline"]["source_c"]["params"]["source"] == "alpaca-sip-split-c"
    assert assets[-9:] == [
        "UPRO",
        "BAC",
        "AMZN",
        "AVGO",
        "NFLX",
        "MSFT",
        "GOOGL",
        "SMH",
        "IWM",
    ]
    assert config["stages"]["gate1"]["params"]["assets"] == assets
    assert config["stages"]["gate2"]["params"]["assets"] == assets
    assert config["stages"]["gate3"]["params"]["assets"] == assets
