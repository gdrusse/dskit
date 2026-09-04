"""ADR-0081 staged planning and journal-authoritative resume tests."""

from __future__ import annotations

import json

import pytest

from dskit.journal import init_journal
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.stages import Stage, StageKindRegistry, run_staged


class CountingStage(Stage):
    outputs = ("value",)
    calls = 0

    def run(self, ctx, inputs):
        del ctx, inputs
        type(self).calls += 1
        return {"value": type(self).calls}


class DoublerStage(Stage):
    outputs = ("result",)

    def validate_inputs(self, inputs):
        return [] if set(inputs) == {"source"} else ["source is required"]

    def run(self, ctx, inputs):
        del ctx
        return {"result": 2 * inputs["source"]}


def _document(run_root):
    return {
        "name": "staged-test",
        "pipeline": {
            "data": {
                "uses": "dskit.pipeline.synthetic_nodes:SynthEvents",
                "params": {"n_events": 2, "n_instruments": 1},
            }
        },
        "outputs": {"run_root": str(run_root)},
        "stages": {
            "first": {"uses": "count"},
            "second": {
                "uses": "double",
                "inputs": {"source": "$first.value"},
            },
        },
    }


def _write_child(tmp_path):
    child = tmp_path / "child"
    configs = child / "configs"
    configs.mkdir(parents=True)
    (child / "pyproject.toml").write_text("[project]\nname='test'\n")
    init_journal(str(child))
    path = configs / "run.json"
    path.write_text(json.dumps(_document(child / "runs")))
    return child, path


def _registry():
    registry = StageKindRegistry()
    registry.register("count", CountingStage)
    registry.register("double", DoublerStage)
    return registry


def test_stage_document_round_trips_and_orders_dependencies(tmp_path):
    obj = _document(tmp_path / "runs")
    document = PipelineDocument.from_obj(obj)
    rebuilt = PipelineDocument.from_obj(document.to_obj())
    assert rebuilt.hash == document.hash
    assert rebuilt.stages["second"].refs() == (("first", ("value",)),)


def test_staged_run_resumes_without_reexecuting(tmp_path, monkeypatch):
    child, path = _write_child(tmp_path)
    monkeypatch.setenv("DSKIT_JOURNAL_TESTS", "1")
    monkeypatch.chdir(child)
    CountingStage.calls = 0
    first = run_staged(str(path), asof="2026-01-02", registry=_registry())
    second = run_staged(str(path), asof="2026-01-02", registry=_registry())
    assert first.state == second.state == "ran"
    assert first.outputs["second"]["result"] == 2
    assert second.outputs == first.outputs
    assert CountingStage.calls == 1


def test_orphaned_stage_artifact_is_refused(tmp_path, monkeypatch):
    child, path = _write_child(tmp_path)
    monkeypatch.setenv("DSKIT_JOURNAL_TESTS", "1")
    monkeypatch.chdir(child)
    document = PipelineDocument.from_obj(_document(child / "runs"))
    run_dir = child / "runs" / (f"staged-test-staged-2026-01-02-{document.hash[:8]}")
    stage_dir = run_dir / "stages"
    stage_dir.mkdir(parents=True)
    (stage_dir / "first.json").write_text("{}\n")
    with pytest.raises(ValueError, match="without a matching successful journal"):
        run_staged(str(path), asof="2026-01-02", registry=_registry())


def test_stage_plan_refuses_an_undeclared_output(tmp_path):
    obj = _document(tmp_path / "runs")
    obj["stages"]["second"]["inputs"]["source"] = "$first.missing"
    path = tmp_path / "run.json"
    path.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="undeclared output"):
        run_staged(str(path), asof="2026-01-02", registry=_registry())
