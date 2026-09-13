"""ADR-0081 staged planning and journal-authoritative resume tests."""

from __future__ import annotations

import json

import pytest

from dskit.journal import init_journal
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.stages import Stage, StageKindRegistry, is_sha256hex, run_staged


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
    document = PipelineDocument.from_obj(_document(child / "runs"))
    first = run_staged(document, str(path), asof="2026-01-02", registry=_registry())
    second = run_staged(document, str(path), asof="2026-01-02", registry=_registry())
    assert first.state == second.state == "ran"
    assert first.outputs["second"]["result"] == 2
    assert second.outputs == first.outputs
    assert CountingStage.calls == 1

def test_cli_staged_keeps_the_original_path(tmp_path, monkeypatch):
    child, path = _write_child(tmp_path)
    monkeypatch.setenv("DSKIT_JOURNAL_TESTS", "1")
    monkeypatch.chdir(child)
    payload = _document(child / "runs")
    payload["stages"]["first"]["uses"] = "tests.pipeline.test_stages:CountingStage"
    payload["stages"]["second"]["uses"] = "tests.pipeline.test_stages:DoublerStage"
    path.write_text(json.dumps(payload))
    from dskit.pipeline.__main__ import main

    assert main(["staged", str(path), "--asof", "2026-01-02"]) == 0


def test_cli_staged_captures_relative_source_before_adapter_changes_cwd(
    tmp_path, monkeypatch
):
    child_a, path = _write_child(tmp_path)
    child_b = tmp_path / "child-b"
    (child_b / "configs").mkdir(parents=True)
    (child_b / "pyproject.toml").write_text("[project]\nname='test-b'\n")
    init_journal(str(child_b))
    monkeypatch.setenv("DSKIT_JOURNAL_TESTS", "1")
    monkeypatch.chdir(child_a)
    payload = _document(child_a / "runs")
    payload["stages"]["first"]["uses"] = "tests.pipeline.test_stages:CountingStage"
    payload["stages"]["second"]["uses"] = "tests.pipeline.test_stages:DoublerStage"
    path.write_text(json.dumps(payload))
    adapter = tmp_path / "move_staged_cwd.py"
    adapter.write_text("import os\nos.chdir(" + repr(str(child_b)) + ")\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    from dskit.journal import load_root
    from dskit.journal.store import read_actions
    from dskit.pipeline.__main__ import main

    assert main(
        [
            "staged",
            "configs/run.json",
            "--asof",
            "2026-01-02",
            "--adapter",
            "move_staged_cwd",
        ]
    ) == 0
    assert [row.inputs for row in read_actions(load_root(str(child_a)))] == [
        str(path),
        str(path),
    ]
    assert read_actions(load_root(str(child_b))) == []


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
        run_staged(document, str(path), asof="2026-01-02", registry=_registry())


def test_stage_plan_refuses_an_undeclared_output(tmp_path):
    obj = _document(tmp_path / "runs")
    obj["stages"]["second"]["inputs"]["source"] = "$first.missing"
    path = tmp_path / "run.json"
    path.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="undeclared output"):
        run_staged(PipelineDocument.from_obj(obj), str(path), asof="2026-01-02", registry=_registry())


def test_sha256hex_requires_an_exact_full_string():
    assert is_sha256hex("a" * 64)
    assert not is_sha256hex("a" * 64 + "\n")
def test_cli_staged_does_not_reopen_after_adapter_import(tmp_path, monkeypatch):
    child, path = _write_child(tmp_path)
    monkeypatch.setenv("DSKIT_JOURNAL_TESTS", "1")
    monkeypatch.chdir(child)
    payload = _document(child / "runs")
    payload["stages"]["first"]["uses"] = "tests.pipeline.test_stages:CountingStage"
    payload["stages"]["second"]["uses"] = "tests.pipeline.test_stages:DoublerStage"
    path.write_text(json.dumps(payload))
    swapped = {
        "name": "swapped-execution",
        "pipeline": {},
        "execution_backtest": {
            "schema_version": "dskit.execution-backtest/v1",
            "purpose": "synthetic",
            "event_envelope_schema": "dskit.event-envelope/v2",
            "source_rank_policy_sha256": "1" * 64,
            "execution_profile_sha256": "2" * 64,
            "environment_identity_sha256": "3" * 64,
        },
    }
    adapter = tmp_path / "swap_staged_config.py"
    adapter.write_text(
        "from pathlib import Path" + chr(10)
        + f"Path({str(path)!r}).write_text({json.dumps(swapped)!r})" + chr(10)
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    from dskit.pipeline.__main__ import main

    assert main(["staged", str(path), "--asof", "2026-01-02", "--adapter", "swap_staged_config"]) == 0
def test_cli_validate_keeps_the_pre_adapter_document(tmp_path, monkeypatch, capsys):
    child, path = _write_child(tmp_path)
    monkeypatch.chdir(child)
    swapped = {
        "name": "swapped-execution",
        "pipeline": {"source": {"uses": "dskit.pipeline.synthetic_nodes:SynthEvents"}},
        "execution_backtest": {
            "schema_version": "dskit.execution-backtest/v1",
            "purpose": "synthetic",
            "event_envelope_schema": "dskit.event-envelope/v2",
            "source_rank_policy_sha256": "1" * 64,
            "execution_profile_sha256": "2" * 64,
            "environment_identity_sha256": "3" * 64,
        },
    }
    adapter = tmp_path / "swap_validate_config.py"
    adapter.write_text(
        "from pathlib import Path" + chr(10)
        + f"Path({str(path)!r}).write_text({json.dumps(swapped)!r})" + chr(10)
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    from dskit.pipeline.__main__ import main

    assert main(["validate", str(path), "--adapter", "swap_validate_config"]) == 0
    output = capsys.readouterr().out
    assert "name:  staged-test" in output
    assert "swapped-execution" not in output

def test_cli_staged_missing_path_returns_one(tmp_path, capsys):
    from dskit.pipeline.__main__ import main

    missing = tmp_path / "missing.json"
    assert main(["staged", str(missing), "--asof", "2026-01-02"]) == 1
    assert str(missing) in capsys.readouterr().out

def test_cli_validate_reads_a_node_map_once(tmp_path, monkeypatch):
    child, path = _write_child(tmp_path)
    monkeypatch.chdir(child)
    import builtins
    import dskit.pipeline.__main__ as pipeline_main

    reads = []
    original_open = builtins.open
    def counted_open(name, *args, **kwargs):
        if str(name) == str(path):
            reads.append(name)
        return original_open(name, *args, **kwargs)
    monkeypatch.setattr("builtins.open", counted_open)

    assert pipeline_main.main(["validate", str(path)]) == 0
    assert len(reads) == 1
