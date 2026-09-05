"""P10 modelability orchestration tests (ADR-0081)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from dskit.pipeline.document import PipelineDocument

from intraday_equities import modelability


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


def test_bounded_walk_runs_one_capped_process_per_fold(tmp_path, monkeypatch):
    document = _document(tmp_path / "runs")
    ctx = SimpleNamespace(
        source_path=str(tmp_path / "configs" / "run.json"),
        run_dir=str(tmp_path / "stage"),
        asof="2026-02-28",
    )
    calls = []

    def fake_run(_ctx, part, tag, memory_limit=None):
        calls.append((part.walkforward.first, tag, memory_limit))
        summary = tmp_path / f"part-{len(calls)}"
        summary.mkdir()
        payload = {
            "state": "ran",
            "folds": [
                {
                    "cutoff": part.walkforward.first,
                    "run_dir": str(summary / "run"),
                    "score": float(len(calls)),
                    "state": "ran",
                }
            ],
        }
        (summary / "walkforward.json").write_text(json.dumps(payload))
        return str(summary), 1

    monkeypatch.setattr(modelability, "_run_walk", fake_run)
    monkeypatch.setattr(modelability, "append_action", lambda *a, **k: None)
    monkeypatch.setattr(
        modelability,
        "_journal_confirms_walk",
        lambda summary, *_args: (
            (tmp_path / "runs") in __import__("pathlib").Path(summary).parents
        ),
    )
    summary = modelability._run_bounded_walk(ctx, document, "gate1-h01")
    assert [call[0] for call in calls] == modelability._fold_cutoffs(
        document.walkforward
    )
    assert all(call[2] == modelability._MEMORY_LIMIT for call in calls)
    payload = json.loads(
        (__import__("pathlib").Path(summary) / "walkforward.json").read_text()
    )
    assert payload["aggregate"]["n_folds"] == 3
    assert payload["aggregate"]["best_score"] == 3.0


def test_run_walk_recovers_a_journaled_persisted_memory_measurement(
    tmp_path, monkeypatch
):
    document = _document(tmp_path / "runs", count=1)
    ctx = SimpleNamespace(
        source_path=str(tmp_path / "child" / "configs" / "run.json"),
        run_dir=str(tmp_path / "stage"),
        asof="2026-02-28",
    )
    summary = tmp_path / "summary"
    summary.mkdir()
    peak = 123456
    (summary / "walkforward.json").write_text("{}\n")
    (summary / "memory-preflight.json").write_text(
        json.dumps(
            {
                "document_hash": document.hash,
                "memory_limit_bytes": modelability._MEMORY_LIMIT,
                "peak_rss_bytes": peak,
            }
        )
    )
    monkeypatch.setattr(modelability, "_summary_dir", lambda *args: str(summary))
    monkeypatch.setattr(modelability, "_journal_confirms_walk", lambda *args: True)
    reused, measured = modelability._run_walk(
        ctx,
        document,
        "memory-preflight",
        memory_limit=modelability._MEMORY_LIMIT,
    )
    assert reused == str(summary)
    assert measured == peak


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


def _walkforward_payload(path, cutoff):
    """Write the one-ran-row artifact a bounded fold is required to leave."""
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "walkforward.json"), "w", encoding="utf-8") as handle:
        json.dump({"state": "ran", "folds": [{"cutoff": cutoff, "score": 1.0}]}, handle)


def _bounded_harness(tmp_path, monkeypatch, cutoffs):
    """Stub every side effect of a bounded walk except the fold loop."""
    monkeypatch.setattr(modelability, "_child_root", lambda _ctx: str(tmp_path))
    monkeypatch.setattr(
        modelability, "_summary_dir", lambda *_a, **_k: str(tmp_path / "sum")
    )
    monkeypatch.setattr(modelability, "_fold_cutoffs", lambda _spec: cutoffs)
    monkeypatch.setattr(modelability, "_journal_confirms_walk", lambda *_a, **_k: True)
    monkeypatch.setattr(
        modelability, "_aggregate_folds", lambda folds, *_a: {"folds": folds}
    )
    monkeypatch.setattr(
        modelability, "_write_walkforward_summary", lambda *_a, **_k: None
    )
    monkeypatch.setattr(modelability, "append_action", lambda *_a, **_k: None)
    monkeypatch.setattr(
        modelability,
        "_single_fold_document",
        lambda _doc, cutoff, index: SimpleNamespace(cutoff=cutoff, index=index),
    )


def test_bounded_walk_runs_more_than_one_fold_process_at_a_time(tmp_path, monkeypatch):
    cutoffs = [f"2022-0{i}-01" for i in range(1, 5)]
    _bounded_harness(tmp_path, monkeypatch, cutoffs)
    live = []
    peak = []
    lock = threading.Lock()

    def fake_run_walk(_ctx, part, tag, *, memory_limit=None):
        with lock:
            live.append(1)
            peak.append(len(live))
        time.sleep(0.05)
        with lock:
            live.pop()
        out = str(tmp_path / f"part-{part.index}")
        _walkforward_payload(out, part.cutoff)
        return out, 1

    monkeypatch.setattr(modelability, "_run_walk", fake_run_walk)
    document = SimpleNamespace(
        name="doc",
        hash="a" * 64,
        walkforward=SimpleNamespace(select="max", weight_halflife_folds=0),
    )
    ctx = SimpleNamespace(asof="2026-02-28", source_path="cfg.json")
    modelability._run_bounded_walk(ctx, document, "tag", workers=4)
    assert max(peak) > 1, "folds still ran one at a time"


def test_bounded_walk_aggregates_folds_in_cutoff_order_under_concurrency(
    tmp_path, monkeypatch
):
    cutoffs = ["2022-01-01", "2022-02-01", "2022-03-01", "2022-04-01"]
    _bounded_harness(tmp_path, monkeypatch, cutoffs)
    seen = {}
    monkeypatch.setattr(
        modelability,
        "_aggregate_folds",
        lambda folds, *_a: seen.update(order=folds) or {},
    )

    def fake_run_walk(_ctx, part, tag, *, memory_limit=None):
        # Reverse the completion order: the last fold finishes first.
        time.sleep(0.02 * (len(cutoffs) - part.index))
        out = str(tmp_path / f"part-{part.index}")
        _walkforward_payload(out, part.cutoff)
        return out, 1

    monkeypatch.setattr(modelability, "_run_walk", fake_run_walk)
    document = SimpleNamespace(
        name="doc",
        hash="a" * 64,
        walkforward=SimpleNamespace(select="max", weight_halflife_folds=0),
    )
    ctx = SimpleNamespace(asof="2026-02-28", source_path="cfg.json")
    modelability._run_bounded_walk(ctx, document, "tag", workers=4)
    assert [row["cutoff"] for row in seen["order"]] == cutoffs


def test_the_memory_cap_is_applied_without_a_fork_time_python_hook(
    tmp_path, monkeypatch
):
    # preexec_fn bars posix_spawn, so the child is forked from a parent
    # that now has a fold pool running: CPython documents that as unsafe
    # and it can wedge before exec. The cap has to survive to exec
    # without Python running in between.
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(modelability.subprocess, "run", fake_run)
    monkeypatch.setattr(modelability, "_child_root", lambda _ctx: str(tmp_path))
    monkeypatch.setattr(
        modelability, "_summary_dir", lambda *_a, **_k: str(tmp_path / "fresh")
    )
    monkeypatch.setattr(modelability, "_save_derived", lambda *_a, **_k: None)
    monkeypatch.setattr(modelability, "_journal_confirms_walk", lambda *_a, **_k: True)
    document = SimpleNamespace(name="doc", hash="a" * 64)
    ctx = SimpleNamespace(asof="2026-02-28", run_dir=str(tmp_path / "r"))
    os.makedirs(tmp_path / "fresh", exist_ok=True)
    modelability._run_walk(ctx, document, "tag", memory_limit=8 * 1024**3)
    assert seen["kwargs"].get("preexec_fn") is None
    joined = " ".join(seen["command"])
    assert f"ulimit -v {8 * 1024**3 // 1024}" in joined
    assert " exec " in joined


def test_every_concurrent_fold_keeps_the_whole_address_space_cap(tmp_path, monkeypatch):
    # RLIMIT_AS is address space, not RSS, and the feature cache mmaps
    # all 25 symbols whatever the walk scores. Dividing the cap between
    # folds eats a fixed mapping floor that measured RSS never sees.
    cutoffs = ["2022-01-01", "2022-02-01"]
    _bounded_harness(tmp_path, monkeypatch, cutoffs)
    limits = []

    def fake_run_walk(_ctx, part, tag, *, memory_limit=None):
        limits.append(memory_limit)
        out = str(tmp_path / f"part-{part.index}")
        _walkforward_payload(out, part.cutoff)
        return out, 1

    monkeypatch.setattr(modelability, "_run_walk", fake_run_walk)
    document = SimpleNamespace(
        name="doc",
        hash="a" * 64,
        walkforward=SimpleNamespace(select="max", weight_halflife_folds=0),
    )
    ctx = SimpleNamespace(asof="2026-02-28", source_path="cfg.json")
    modelability._run_bounded_walk(ctx, document, "tag", workers=4)
    assert set(limits) == {modelability._MEMORY_LIMIT}


def test_a_cached_fold_only_resumes_under_the_limit_it_was_run_at(
    tmp_path, monkeypatch
):
    # The reason the address-space cap must not vary with worker width:
    # _run_walk refuses a finished fold whose persisted limit differs
    # from the one asked for, so a width-derived limit would invalidate
    # every completed fold of a gate whose whole point is resuming.
    summary = tmp_path / "sum"
    summary.mkdir()
    (summary / "walkforward.json").write_text("{}")
    with open(summary / "memory-preflight.json", "w", encoding="utf-8") as handle:
        json.dump(
            {
                "document_hash": "a" * 64,
                "memory_limit_bytes": modelability._MEMORY_LIMIT,
                "peak_rss_bytes": 10,
            },
            handle,
        )
    monkeypatch.setattr(modelability, "_child_root", lambda _ctx: str(tmp_path))
    monkeypatch.setattr(modelability, "_summary_dir", lambda *_a, **_k: str(summary))
    monkeypatch.setattr(modelability, "_save_derived", lambda *_a, **_k: None)
    monkeypatch.setattr(modelability, "_journal_confirms_walk", lambda *_a, **_k: True)
    document = SimpleNamespace(name="doc", hash="a" * 64)
    ctx = SimpleNamespace(asof="2026-02-28", run_dir=str(tmp_path / "r"))
    got, peak = modelability._run_walk(
        ctx, document, "tag", memory_limit=modelability._MEMORY_LIMIT
    )
    assert (got, peak) == (str(summary), 10)
    for divided in (modelability._MEMORY_LIMIT // 4, modelability._MEMORY_LIMIT // 2):
        try:
            modelability._run_walk(ctx, document, "tag", memory_limit=divided)
        except ValueError as error:
            assert "memory" in str(error)
        else:  # pragma: no cover - the assertion below reports it
            raise AssertionError("a differing limit silently resumed")


def test_a_failing_fold_cancels_the_ones_that_have_not_started(tmp_path, monkeypatch):
    cutoffs = [f"2022-{i:02d}-01" for i in range(1, 9)]
    _bounded_harness(tmp_path, monkeypatch, cutoffs)
    started = []

    def fake_run_walk(_ctx, part, tag, *, memory_limit=None):
        started.append(part.index)
        if part.index == 0:
            raise RuntimeError("fold 0 blew up")
        time.sleep(0.05)
        out = str(tmp_path / f"part-{part.index}")
        _walkforward_payload(out, part.cutoff)
        return out, 1

    monkeypatch.setattr(modelability, "_run_walk", fake_run_walk)
    document = SimpleNamespace(
        name="doc",
        hash="a" * 64,
        walkforward=SimpleNamespace(select="max", weight_halflife_folds=0),
    )
    ctx = SimpleNamespace(asof="2026-02-28", source_path="cfg.json")
    try:
        modelability._run_bounded_walk(ctx, document, "tag", workers=2)
    except RuntimeError as error:
        assert "fold 0" in str(error)
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("the failing fold did not surface")
    assert len(started) < len(cutoffs), "every fold ran after one had already failed"


def test_the_worker_width_defaults_to_the_machine_knob(tmp_path, monkeypatch):
    cutoffs = ["2022-01-01"]
    _bounded_harness(tmp_path, monkeypatch, cutoffs)
    monkeypatch.setenv(modelability._WORKERS_ENV, "5")
    seen = []

    def fake_run_walk(_ctx, part, tag, *, memory_limit=None):
        out = str(tmp_path / f"part-{part.index}")
        _walkforward_payload(out, part.cutoff)
        return out, 1

    monkeypatch.setattr(modelability, "_run_walk", fake_run_walk)
    monkeypatch.setattr(
        modelability,
        "append_action",
        lambda *_a, **kw: seen.append(kw.get("notes", "")),
    )
    document = SimpleNamespace(
        name="doc",
        hash="a" * 64,
        walkforward=SimpleNamespace(select="max", weight_halflife_folds=0),
    )
    ctx = SimpleNamespace(asof="2026-02-28", source_path="cfg.json")
    modelability._run_bounded_walk(ctx, document, "tag")
    assert "fold_workers=5" in seen[0]
