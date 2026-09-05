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


def test_bounded_walk_runs_folds_concurrently_and_keeps_them_in_order(
    tmp_path, monkeypatch
):
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


def test_bounded_walk_divides_the_memory_envelope_between_concurrent_folds(
    tmp_path, monkeypatch
):
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
    assert set(limits) == {modelability._MEMORY_LIMIT // 4}
    limits.clear()
    modelability._run_bounded_walk(ctx, document, "tag")
    assert set(limits) == {modelability._MEMORY_LIMIT}
