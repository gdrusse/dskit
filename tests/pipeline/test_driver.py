"""The driver: the 6-step lifecycle end to end, against synthetic nodes."""

import io
import json
import logging
import os
import re

import pytest
import dskit.pipeline.node as node_module

from dskit.pipeline import plan as public_plan
from dskit.pipeline.base import (
    SINK_KINDS,
    ConfigError,
    EnvConfig,
    OutputsConfig,
    SinkConfig,
    TrackingConfig,
    register_sink_kind,
)
from dskit.pipeline.document import (
    ClockConfig,
    ExecutionBacktestSpec,
    NodeSpec,
    PipelineDocument,
    StageSpec,
    TrailingSplitSpec,
    load_document,
    save_document,
)
from dskit.pipeline.driver import (
    DocumentRunResult,
    RunAttestation,
    _RELEASE_MIN_LEN,
    _carryable,
    _is_summary,
    _node_metrics,
    _summarize,
    _too_big_to_carry,
    content_identity,
    resolve_json_artifact,
    row_set_identity,
    run_document,
    run_walk_forward,
)
from dskit.pipeline.node import Node, NodeKindRegistry, resolve_uses
from dskit.pipeline.planner import plan
from dskit.pipeline.stages import plan_stages, run_staged
from dskit.pipeline.testing import MemoryTracker
from tests.pipeline.dochelpers import banking_document, banking_pipeline, make_registry

ASOF = "2026-01-01"


class ReplacingTracker:
    """A Tracker seam implemented the blunt way — each ``log_params`` call
    REPLACES what the sink holds. Legal, because the seam's contract is ONE
    call per run; ``payloads`` keeps every call so a test can count them
    and read what was sent."""

    instances = []

    def __init__(self, params):
        self.params = {}
        self.payloads = []
        ReplacingTracker.instances.append(self)

    def log_params(self, mapping):
        self.payloads.append(dict(mapping))
        self.params = dict(mapping)

    def log_metrics(self, stage, mapping):
        pass

    def close(self):
        pass


class BadContractNode(Node):
    role = "transform"
    outputs = ("x",)

    def run(self, ctx, inputs):
        return {"y": 1}


@pytest.fixture
def registry():
    return make_registry()


def bdoc(tmp_path, **overrides):
    overrides.setdefault("outputs", OutputsConfig(run_root=str(tmp_path)))
    return banking_document(**overrides)


_EXECUTION_BACKTEST = {
    "schema_version": "dskit.execution-backtest/v1",
    "purpose": "synthetic",
    "event_envelope_schema": "dskit.event-envelope/v2",
    "source_rank_policy_sha256": "1" * 64,
    "execution_profile_sha256": "2" * 64,
    "environment_identity_sha256": "3" * 64,
}


class _ExecutionHidingDocument(PipelineDocument):
    def __getattribute__(self, name):
        if name == "execution_backtest":
            return None
        return super().__getattribute__(name)


class _ExecutionMutatingUses(str):
    def __new__(cls, value, document):
        instance = super().__new__(cls, value)
        instance.document = document
        return instance

    def __hash__(self):
        object.__setattr__(
            self.document,
            "execution_backtest",
            ExecutionBacktestSpec.from_obj(_EXECUTION_BACKTEST),
        )
        return super().__hash__()


class _ExecutionClearingPipeline(dict):
    def __init__(self, document, values):
        super().__init__(values)
        self.document = document

    def items(self):
        object.__setattr__(self.document, "execution_backtest", None)
        return super().items()


def _hidden_execution_document():
    return _ExecutionHidingDocument(
        name="hidden-execution",
        pipeline={"source": NodeSpec(uses="filter")},
        clock=ClockConfig(increment="day"),
        execution_backtest=ExecutionBacktestSpec.from_obj(_EXECUTION_BACKTEST),
    )


def _mutation_document():
    document = PipelineDocument(
        name="mutation-ordinary",
        pipeline={"source": NodeSpec(uses="filter")},
        clock=ClockConfig(increment="day"),
    )
    document.pipeline["source"] = NodeSpec(
        uses=_ExecutionMutatingUses("filter", document)
    )
    return document


def _poison_execution_document():
    document = PipelineDocument(
        name="poison-execution",
        pipeline={"source": NodeSpec(uses="poison_mod:Poison")},
        execution_backtest=ExecutionBacktestSpec.from_obj(_EXECUTION_BACKTEST),
    )
    object.__setattr__(document, "stages", {"capture": StageSpec(uses="capture")})
    object.__setattr__(
        document,
        "pipeline",
        _ExecutionClearingPipeline(document, document.pipeline),
    )
    return document


@pytest.mark.parametrize(
    "entry",
    (
        public_plan,
        plan,
        lambda document: resolve_uses(document, "filter"),
        lambda document: run_document(document, asof=ASOF),
        lambda document: run_walk_forward(document, asof=ASOF),
        plan_stages,
        lambda document: run_staged(document, source_path="captured.json", asof=ASOF),
    ),
)
def test_public_facades_refuse_a_hostile_pipeline_document_subclass(entry):
    with pytest.raises(ValueError, match="exact plain PipelineDocument"):
        entry(_hidden_execution_document())


@pytest.mark.parametrize(
    "entry",
    (
        public_plan,
        plan,
        lambda document: resolve_uses(document, document.pipeline["source"].uses),
    ),
)
def test_public_resolution_uses_a_detached_plain_document_snapshot(entry):
    document = _mutation_document()
    entry(document)
    assert document.execution_backtest is None


@pytest.mark.parametrize(
    "entry",
    (
        public_plan,
        plan,
        lambda document: resolve_uses(document, "poison_mod:Poison"),
        lambda document: run_document(document, asof=ASOF),
        lambda document: run_walk_forward(document, asof=ASOF),
        plan_stages,
        lambda document: run_staged(
            document, source_path="captured.json", asof=ASOF
        ),
    ),
)
def test_public_facades_refuse_execution_before_mutating_snapshot_import(
    entry, tmp_path, monkeypatch
):
    marker = tmp_path / "poison-imported"
    module = tmp_path / "poison_mod.py"
    module.write_text(
        "from pathlib import Path\n"
        "from dskit.pipeline.node import Node\n"
        f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n"
        "class Poison(Node):\n"
        "    role = 'transform'\n"
        "    def run(self, ctx, inputs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    import sys

    sys.modules.pop("poison_mod", None)
    try:
        entry(_poison_execution_document())
    except ConfigError as exc:
        message = str(exc)
    else:
        message = ""
    assert not marker.exists(), "execution document imported its poison class"
    assert "external broker" in message


def read_json(run_dir, name):
    with open(os.path.join(run_dir, name), encoding="utf-8") as fh:
        return json.load(fh)


def test_execution_refuses_before_adapter_import(tmp_path, monkeypatch):
    marker = tmp_path / "adapter-imported"
    adapter = tmp_path / "execution_poison_adapter.py"
    adapter.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n"
        "class PoisonNode:\n"
        "    pass\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    document = PipelineDocument.from_obj(
        {
            "name": "execution-driver-refusal",
            "pipeline": {
                "source": {"uses": "execution_poison_adapter:PoisonNode"}
            },
            "execution_backtest": {
                "schema_version": "dskit.execution-backtest/v1",
                "purpose": "synthetic",
                "event_envelope_schema": "dskit.event-envelope/v2",
                "source_rank_policy_sha256": "1" * 64,
                "execution_profile_sha256": "2" * 64,
                "environment_identity_sha256": "3" * 64,
            },
        }
    )

    with pytest.raises(ConfigError) as exc_info:
        run_document(document, asof=ASOF)

    assert not marker.exists(), "execution document imported its adapter"
    assert "external broker" in str(exc_info.value)

    document_path = tmp_path / "execution-document.json"
    save_document(document, document_path)
    with pytest.raises(ValueError) as exc_info:
        run_document(str(document_path), asof=ASOF)

    assert not marker.exists(), "execution document path imported its adapter"
    assert "in-memory PipelineDocument" in str(exc_info.value)

    from dskit.pipeline.__main__ import main

    exit_code = main(
        [
            "run",
            str(document_path),
            "--asof",
            ASOF,
            "--adapter",
            "execution_poison_adapter",
        ]
    )

    assert exit_code == 1
    assert not marker.exists(), "execution CLI imported its adapter"

    import sys

    def assert_no_import(action):
        sys.modules.pop("execution_poison_adapter", None)
        marker.unlink(missing_ok=True)
        assert action() == 1
        assert not marker.exists(), "execution route imported its adapter"

    for command in ("plan", "validate", "walkforward", "staged"):
        argv = [command, str(document_path), "--adapter", "execution_poison_adapter"]
        if command == "walkforward":
            argv[2:2] = ["--asof", ASOF]
        assert_no_import(lambda argv=argv: main(argv))

    from dskit.pipeline.planner import plan

    sys.modules.pop("execution_poison_adapter", None)
    marker.unlink(missing_ok=True)
    with pytest.raises(ConfigError) as exc_info:
        plan(document)
    assert "external broker" in str(exc_info.value)
    assert not marker.exists(), "planner imported execution uses"

    from dskit.pipeline.driver import run_walk_forward
    from dskit.pipeline.stages import plan_stages, run_staged

    for action in (
        lambda: plan_stages(document),
        lambda: run_staged(document, source_path=str(document_path)),
        lambda: run_walk_forward(document, asof=ASOF),
    ):
        sys.modules.pop("execution_poison_adapter", None)
        marker.unlink(missing_ok=True)
        with pytest.raises(ConfigError, match="external broker"):
            action()
        assert not marker.exists(), "execution route imported its adapter"


    malformed_path = tmp_path / "malformed-execution.json"
    malformed_path.write_text(json.dumps({"name": "bad", "pipeline": {"source": {"uses": "execution_poison_adapter:PoisonNode"}}, "execution_backtest": {}}))
    for command in ("plan", "run", "validate", "walkforward", "staged"):
        sys.modules.pop("execution_poison_adapter", None)
        marker.unlink(missing_ok=True)
        argv = [command, str(malformed_path), "--adapter", "execution_poison_adapter"]
        if command in ("run", "walkforward", "staged"):
            argv[2:2] = ["--asof", ASOF]
        assert main(argv) == 1
        assert not marker.exists()


@pytest.mark.parametrize(
    ("node", "semantic"),
    (
        (
            {
                "uses": "poison_adapter:Poison",
                "params": {
                    "nested": [{"$prev": "source.output", "default": "first"}]
                },
            },
            "$prev",
        ),
        (
            {
                "uses": "poison_adapter:Poison",
                "mode": "load",
                "artifact": "runs/source/model.json",
            },
            "mode",
        ),
        (
            {
                "uses": "poison_adapter:Poison",
                "mode": "load",
                "artifact": "runs/source/model.json",
            },
            "artifact",
        ),
    ),
)
@pytest.mark.parametrize("command", ("plan", "run", "validate", "walkforward", "staged"))
def test_execution_forbidden_node_semantics_refuse_before_cli_adapter_import(
    command, node, semantic, tmp_path, monkeypatch, capsys
):
    marker = tmp_path / "adapter-imported"
    adapter = tmp_path / "poison_adapter.py"
    adapter.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n",
        encoding="utf-8",
    )
    config = tmp_path / "execution-forbidden-node-semantics.json"
    config.write_text(
        json.dumps(
            {
                "name": "execution-forbidden-node-semantics",
                "pipeline": {"source": node},
                "execution_backtest": _EXECUTION_BACKTEST,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    import sys

    sys.modules.pop("poison_adapter", None)
    argv = [command, str(config), "--adapter", "poison_adapter"]
    if command in ("run", "walkforward", "staged"):
        argv[2:2] = ["--asof", ASOF]
    from dskit.pipeline.__main__ import main

    assert main(argv) == 1
    assert not marker.exists(), f"{command} imported its adapter before refusal"
    assert semantic in capsys.readouterr().out


def test_public_execution_path_overloads_refuse_before_open(monkeypatch):
    """The CLI owns document I/O; public execution APIs never reopen paths."""
    from dskit.pipeline.driver import run_walk_forward
    from dskit.pipeline.stages import run_staged

    def unexpected_open(*args, **kwargs):
        del args, kwargs
        raise AssertionError("public execution API opened a retired path overload")

    monkeypatch.setattr("builtins.open", unexpected_open)
    for action in (
        lambda: run_document("retired-document.json", asof=ASOF),
        lambda: run_walk_forward("retired-document.json", asof=ASOF),
        lambda: run_staged("retired-document.json", asof=ASOF),
    ):
        with pytest.raises(ValueError, match="in-memory PipelineDocument"):
            action()


@pytest.mark.parametrize(
    ("where", "field", "document"),
    (
        ("pipeline.source", "inputs", {"pipeline": {"source": {"uses": "ordinary-poison", "inputs": []}}}),
        ("foreach.pipeline.source", "inputs", {"pipeline": {}, "foreach": {"keys": ["one"], "pipeline": {"source": {"uses": "ordinary-poison", "inputs": []}}}}),
        ("foreach.pipeline.source", "params", {"pipeline": {}, "foreach": {"keys": ["one"], "pipeline": {"source": {"uses": "ordinary-poison", "params": []}}}}),
        ("stages.capture", "inputs", {"pipeline": {"source": {"uses": "ordinary-poison"}}, "stages": {"capture": {"uses": "ordinary-poison", "inputs": []}}}),
        ("stages.capture", "params", {"pipeline": {"source": {"uses": "ordinary-poison"}}, "stages": {"capture": {"uses": "ordinary-poison", "params": []}}}),
    ),
)
def test_malformed_mapping_fields_refuse_once_before_any_cli_adapter(
    tmp_path, monkeypatch, capsys, where, field, document
):
    """Poison adapters cannot observe malformed mapping-shaped fields."""
    marker = tmp_path / "adapter-imported"
    adapter = tmp_path / "mapping_poison_adapter.py"
    adapter.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text(\"imported\", encoding=\"utf-8\")\n",
        encoding="utf-8",
    )
    config = tmp_path / "malformed-mapping.json"
    config.write_text(json.dumps({"name": "malformed-mapping", **document}), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    from dskit.pipeline.__main__ import main

    import builtins
    import sys

    original_open = builtins.open
    reads = []

    def counted_open(name, *args, **kwargs):
        if str(name) == str(config):
            reads.append(name)
        return original_open(name, *args, **kwargs)

    monkeypatch.setattr("builtins.open", counted_open)
    for command in ("plan", "run", "validate", "walkforward", "staged"):
        sys.modules.pop("mapping_poison_adapter", None)
        marker.unlink(missing_ok=True)
        reads.clear()
        argv = [command, str(config), "--adapter", "mapping_poison_adapter"]
        if command in ("run", "walkforward", "staged"):
            argv[2:2] = ["--asof", ASOF]
        assert main(argv) == 1
        assert f"{where}: {field} must be an object" in capsys.readouterr().out
        assert reads == [str(config)]
        assert not marker.exists(), f"{command} imported an adapter before refusal"


def test_malformed_optional_section_refuses_once_before_any_cli_adapter(
    tmp_path, monkeypatch, capsys
):
    """A malformed document section cannot escape into an adapter import."""
    marker = tmp_path / "adapter-imported"
    adapter = tmp_path / "section_poison_adapter.py"
    adapter.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n",
        encoding="utf-8",
    )
    config = tmp_path / "malformed-section.json"
    config.write_text(
        json.dumps(
            {
                "name": "malformed-section",
                "pipeline": {"source": {"uses": "ordinary-poison"}},
                "foreach": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    from dskit.pipeline.__main__ import main

    import builtins
    import sys

    original_open = builtins.open
    reads = []

    def counted_open(name, *args, **kwargs):
        if str(name) == str(config):
            reads.append(name)
        return original_open(name, *args, **kwargs)

    monkeypatch.setattr("builtins.open", counted_open)
    for command in ("plan", "run", "validate", "walkforward", "staged"):
        sys.modules.pop("section_poison_adapter", None)
        marker.unlink(missing_ok=True)
        reads.clear()
        argv = [command, str(config), "--adapter", "section_poison_adapter"]
        if command in ("run", "walkforward", "staged"):
            argv[2:2] = ["--asof", ASOF]
        assert main(argv) == 1
        output = capsys.readouterr().out
        assert "foreach: must be an object" in output
        assert "Traceback" not in output
        assert reads == [str(config)]
        assert not marker.exists(), f"{command} imported an adapter before refusal"


def test_malformed_node_map_refuses_once_before_any_cli_adapter(tmp_path, monkeypatch):
    """Malformed node maps never fall through to adapters or a second read."""
    marker = tmp_path / "adapter-imported"
    adapter = tmp_path / "ordinary_poison_adapter.py"
    adapter.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n",
        encoding="utf-8",
    )
    config = tmp_path / "malformed-node-map.json"
    config.write_text(
        json.dumps(
            {
                "name": "malformed-node-map",
                "pipeline": {
                    "source": {
                        "uses": "ordinary-poison",
                        "params": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    from dskit.pipeline.__main__ import main

    import builtins
    import sys

    original_open = builtins.open
    reads = []

    def counted_open(name, *args, **kwargs):
        if str(name) == str(config):
            reads.append(name)
        return original_open(name, *args, **kwargs)

    monkeypatch.setattr("builtins.open", counted_open)
    for command in ("plan", "run", "validate", "walkforward", "staged"):
        sys.modules.pop("ordinary_poison_adapter", None)
        marker.unlink(missing_ok=True)
        reads.clear()
        argv = [command, str(config), "--adapter", "ordinary_poison_adapter"]
        if command in ("run", "walkforward", "staged"):
            argv[2:2] = ["--asof", ASOF]
        assert main(argv) == 1
        assert reads == [str(config)]
        assert not marker.exists(), f"{command} imported an adapter before refusal"


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
def test_nonfinite_json_constants_refuse_once_before_any_cli_adapter(
    tmp_path, monkeypatch, capsys, constant
):
    """Public config JSON refuses nonfinite constants before adapter import."""
    marker = tmp_path / "adapter-imported"
    adapter = tmp_path / "nonfinite_poison_adapter.py"
    adapter.write_text(
        "from pathlib import Path\n"
        + f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n",
        encoding="utf-8",
    )
    config = tmp_path / "nonfinite.json"
    config.write_text(
        '{"name":"nonfinite","pipeline":{"source":'
        '{"uses":"ordinary-poison","params":{"x":'
        + constant
        + "}}}}",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    from dskit.pipeline.__main__ import main

    import builtins
    import sys

    original_open = builtins.open
    reads = []

    def counted_open(name, *args, **kwargs):
        if str(name) == str(config):
            reads.append(name)
        return original_open(name, *args, **kwargs)

    monkeypatch.setattr("builtins.open", counted_open)
    for command in ("plan", "run", "validate", "walkforward", "staged"):
        sys.modules.pop("nonfinite_poison_adapter", None)
        marker.unlink(missing_ok=True)
        reads.clear()
        argv = [command, str(config), "--adapter", "nonfinite_poison_adapter"]
        if command in ("run", "walkforward", "staged"):
            argv[2:2] = ["--asof", ASOF]
        assert main(argv) == 1
        assert f"{config}: non-finite JSON constant {constant}" in capsys.readouterr().out
        assert reads == [str(config)]
        assert not marker.exists(), f"{command} imported an adapter before refusal"


class TestCleanRun:
    def test_end_to_end_banking_run(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        assert isinstance(result, DocumentRunResult)
        assert result.state == "ran" and result.exit_code == 0
        assert set(result.node_states.values()) == {"ok"}
        # The planted edge deploys: both instruments survive, capital sizes.
        assert result.outputs["edge_test"]["survivors"] == ["SYNA", "SYNB"]
        assert result.outputs["size"]["positions"]
        assert result.outputs["size"]["final_bankroll"] == pytest.approx(1020.0)

    def test_run_dir_layout_and_naming(self, tmp_path, registry):
        doc = bdoc(tmp_path)
        result = run_document(doc, asof=ASOF, registry=registry)
        base = os.path.basename(result.run_dir)
        assert base == f"synth-banking-{ASOF}-{result.run_hash[:8]}"
        for artifact in (
            "config.json",
            "plan.json",
            "resolved.json",
            "result.json",
            "report.md",
            "carry.json",
            "run.log",
        ):
            assert os.path.isfile(os.path.join(result.run_dir, artifact)), artifact
        assert os.path.isfile(
            os.path.join(result.run_dir, "artifacts", "qhat", "model.json")
        )
        records = sorted(os.listdir(os.path.join(result.run_dir, "nodes")))
        assert len(records) == len(banking_pipeline())
        assert records[0].startswith("01-")

    def test_report_leads_with_the_verdict(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
            first = fh.readline().strip()
        assert first.startswith("**RAN")

    def test_result_json_mirrors_the_result(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        payload = read_json(result.run_dir, "result.json")
        assert payload["state"] == "ran" and payload["exit_code"] == 0
        assert payload["run_hash"] == result.run_hash
        assert payload["node_states"] == result.node_states

    def test_run_log_narrates_the_nodes(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        with open(os.path.join(result.run_dir, "run.log"), encoding="utf-8") as fh:
            log = fh.read()
        assert "node events: start" in log and "node size: ok" in log

    def test_document_loads_from_a_path(self, tmp_path, registry):
        doc_path = tmp_path / "doc.json"
        save_document(bdoc(tmp_path / "runs"), doc_path)
        result = run_document(load_document(doc_path), asof=ASOF, registry=registry)
        assert result.state == "ran"


class TestHaltSemantics:
    def test_nogo_gate_halts_descendants_only(self, tmp_path, registry):
        pipeline = banking_pipeline()
        pipeline["family"] = NodeSpec(
            uses="synth-eligibility",
            inputs={"counts": "$bank.counts"},
            params={"min_events": 10_000},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "halted" and result.exit_code == 3
        assert result.halted_at == "family"
        assert result.node_states["family"] == "ok"  # the gate itself ran
        assert result.node_states["report"] == "halted"  # downstream of family
        # Independent branches kept running — this is a DAG halt, not a break.
        assert result.node_states["edge_test"] == "ok"
        assert result.node_states["size"] == "ok"

    def test_nogo_stat_test_halts_capital(self, tmp_path, registry):
        pipeline = banking_pipeline()
        pipeline["edge_test"] = NodeSpec(
            uses="stat_test",
            inputs={"scores": "$validate.cluster_scores"},
            params={"alpha": 1e-9},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "halted" and result.halted_at == "edge_test"
        assert result.node_states["size"] == "halted"
        assert result.node_states["report"] == "halted"
        with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
            assert fh.readline().startswith("**NO-GO — halted at `edge_test`")


class TestErrorSemantics:
    def test_node_exception_is_recorded_and_aborts(self, tmp_path, registry):
        pipeline = banking_pipeline()
        pipeline["qhat"] = NodeSpec(
            uses="synth-train",
            mode="train",
            inputs={"events": "$clip.events"},
            params={"min_train": 10_000},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "error" and result.exit_code == 1
        assert result.node_states["qhat"] == "error"
        assert result.node_states["market"] == "ok"  # ran before the failure
        assert result.node_states["size"] == "not_run"  # aborted, not halted
        record = read_json(result.run_dir, os.path.join("nodes", "07-qhat.json"))
        assert record["status"] == "error" and "min_train=10000" in record["error"]
        with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
            assert fh.readline().startswith("**ERROR at `qhat`")

    def test_validate_inputs_problems_fail_the_node(self, tmp_path, registry):
        pipeline = banking_pipeline()
        pipeline["validate"] = NodeSpec(
            uses="synth-score",
            inputs={
                "events": "$clip.events",
                "signal": "$events.instruments",  # a list, not a signal dict
                "baseline": "$market.signal",
                "outcomes": "$labels.outcomes",
            },
            params={"split": "val"},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "error"
        assert result.node_states["validate"] == "error"
        assert "signal must be a dict" in result.error

    def test_output_contract_violation_fails_the_node(self, tmp_path, registry):
        pipeline = {
            "events": NodeSpec(uses="synth-events", params={"n_events": 8}),
            "bad": NodeSpec(
                uses="tests.pipeline.test_driver:BadContractNode",
                inputs={"events": "$events.events"},
            ),
        }
        doc = PipelineDocument(
            name="contract-break",
            pipeline=pipeline,
            outputs=OutputsConfig(run_root=str(tmp_path)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        assert result.state == "error"
        assert "undeclared ['y']" in result.error


class TestPrevCarry:
    def test_bankroll_carries_run_over_run(self, tmp_path, registry):
        first = run_document(bdoc(tmp_path), asof="2026-01-01", registry=registry)
        assert first.outputs["size"]["final_bankroll"] == pytest.approx(1020.0)
        assert read_json(first.run_dir, "resolved.json")["prev_bindings"] == {
            "size.final_bankroll": "default"
        }
        second = run_document(bdoc(tmp_path), asof="2026-01-08", registry=registry)
        assert second.prev_run == first.run_dir
        assert second.outputs["size"]["final_bankroll"] == pytest.approx(1040.4)
        resolved = read_json(second.run_dir, "resolved.json")
        assert resolved["prev_bindings"] == {"size.final_bankroll": "prev"}
        assert resolved["prev_run"] == first.run_dir

    def test_missing_prev_output_falls_back_to_default_and_says_so(
        self, tmp_path, registry
    ):
        first = run_document(bdoc(tmp_path), asof="2026-01-01", registry=registry)
        carry = read_json(first.run_dir, "carry.json")
        del carry["size"]
        with open(
            os.path.join(first.run_dir, "carry.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(carry, fh)
        second = run_document(bdoc(tmp_path), asof="2026-01-08", registry=registry)
        assert second.outputs["size"]["final_bankroll"] == pytest.approx(1020.0)
        assert read_json(second.run_dir, "resolved.json")["prev_bindings"] == {
            "size.final_bankroll": "default"
        }

    def test_carry_holds_state_not_datasets(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        carry = read_json(result.run_dir, "carry.json")
        assert carry["size"]["final_bankroll"] == pytest.approx(1020.0)
        assert "events" not in carry.get("events", {})  # the big list is not carried
        assert "instruments" in carry["events"]


class TestRefusals:
    def test_occupied_run_dir_refused(self, tmp_path, registry):
        run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        with pytest.raises(ValueError, match="already exists"):
            run_document(bdoc(tmp_path), asof=ASOF, registry=registry)

    def test_clock_documents_refuse_to_run(self, tmp_path, registry):
        doc = bdoc(tmp_path, clock=ClockConfig(increment="epoch"))
        with pytest.raises(ConfigError, match="I-222"):
            run_document(doc, asof=ASOF, registry=registry)

    def test_trailing_splits_refuse_to_resolve(self, tmp_path, registry):
        doc = bdoc(tmp_path, splits=TrailingSplitSpec(test_days=14, val_days=28))
        with pytest.raises(ConfigError, match="trailing"):
            run_document(doc, asof=ASOF, registry=registry)

    def test_bad_asof_refused(self, tmp_path, registry):
        with pytest.raises(ConfigError, match="asof"):
            run_document(bdoc(tmp_path), asof="Jan 1", registry=registry)

    def test_missing_required_env_lists_the_names(self, tmp_path, registry):
        doc = bdoc(
            tmp_path,
            env=EnvConfig(
                env_file=str(tmp_path / "none.env"), require=("PMQ_MISSING_XYZ",)
            ),
        )
        with pytest.raises(ValueError, match="PMQ_MISSING_XYZ"):
            run_document(doc, asof=ASOF, registry=registry)
        assert not os.path.isdir(os.path.join(tmp_path, f"synth-banking-{ASOF}"))


class TestTracking:
    def register_memory(self):
        if "memory" not in SINK_KINDS:
            register_sink_kind("memory", lambda params: [], MemoryTracker)

    def test_metrics_and_params_reach_the_sink_and_it_closes(self, tmp_path, registry):
        self.register_memory()
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        sink = MemoryTracker.instances[-1]
        assert sink.closed
        assert sink.logged_params["run_hash"] == result.run_hash
        logged = {node: m for node, m in sink.metrics}
        assert "metrics.loss" in logged["validate"]
        assert logged["size"]["final_bankroll"] == pytest.approx(1020.0)

    def test_node_params_reach_the_sink_beside_the_identity_fields(
        self, tmp_path, registry
    ):
        # Identity alone made runs unfilterable: with only name/asof/hashes
        # in the payload you could not ask a sink for "the runs at
        # n_events=432". Every node's params ride along, flattened to the
        # same '<node>.<param.path>' keys hpo-grid tunes.
        self.register_memory()
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        logged = MemoryTracker.instances[-1].logged_params
        assert logged["events.n_events"] == 432
        assert logged["clip.lo"] == 0.02
        assert logged["size.stake_frac"] == 0.1
        assert logged["name"] == doc.name
        assert logged["asof"] == ASOF
        assert logged["document_hash"] == doc.hash
        assert logged["run_hash"] == result.run_hash
        assert logged["nodes"].startswith("events,")

    def test_a_prev_carry_logs_as_the_reference_it_was_declared_as(
        self, tmp_path, registry
    ):
        # Round-4 ruling (findings 1+2+3): keys and values follow the
        # DECLARED document, and a reference logs as a reference. Logging
        # the carry RESOLVED would make 'size.bankroll' a different value
        # every run of the series; the declared spec is stable, bounded,
        # and IS the config — what the carry bound to lives in
        # resolved.json and the prior run's outputs, where it happened.
        self.register_memory()
        declared = banking_pipeline()["size"].params["bankroll"]
        assert "$prev" in declared  # the fixture still carries; else vacuous

        def run(asof):
            doc = bdoc(
                tmp_path, tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),))
            )
            return run_document(doc, asof=asof, registry=registry)

        first = run("2026-01-01")
        first_logged = dict(MemoryTracker.instances[-1].logged_params)
        second = run("2026-01-08")
        second_logged = dict(MemoryTracker.instances[-1].logged_params)
        assert second.prev_run == first.run_dir  # a real series, not two firsts
        assert first_logged["size.bankroll"] == declared
        assert second_logged["size.bankroll"] == declared
        # Descent never enters a reference: the carry contributes no
        # subtree keys, and the payload's KEY SET holds across the series.
        assert "size.bankroll.default" not in first_logged
        assert set(first_logged) == set(second_logged)

    def test_a_dict_valued_carry_also_logs_as_its_declared_reference(
        self, tmp_path, registry
    ):
        # Round-4 ruling (findings 1+2+3): the dict-valued carry was the
        # unstable case — resolving it whole gave run 1 keys from the
        # literal default and run 2 whatever keys the prior run's output
        # happened to hold, so the payload's key set drifted across the
        # series and spelled targets no override or space key can address.
        # The declared reference is ONE stable leaf, both runs alike.
        self.register_memory()
        spec = {"$prev": "size.positions", "default": {"lr": 0.1}}

        def run(asof):
            pipeline = banking_pipeline()
            pipeline["size"] = NodeSpec(
                uses="synth-capital",
                inputs=dict(pipeline["size"].inputs),
                params={**pipeline["size"].params, "cfg": dict(spec)},
            )
            doc = bdoc(
                tmp_path,
                pipeline=pipeline,
                tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
            )
            return run_document(doc, asof=asof, registry=registry)

        first = run("2026-01-01")
        first_logged = dict(MemoryTracker.instances[-1].logged_params)
        assert first.state == "ran"
        second = run("2026-01-08")
        second_logged = dict(MemoryTracker.instances[-1].logged_params)
        assert second.prev_run == first.run_dir
        assert first_logged["size.cfg"] == spec
        assert second_logged["size.cfg"] == spec
        assert "size.cfg.lr" not in first_logged  # descent never enters a ref
        assert set(first_logged) == set(second_logged)

    def test_a_node_whose_entire_params_block_is_a_carry_logs_no_keys(
        self, tmp_path, registry
    ):
        # Round-5 ruling (finding 1, refining 1+2+3): a root-level carry
        # is pure wiring — the node declares no addressable knob, and the
        # params block has no path of its own to be emitted under — so it
        # contributes NOTHING. Descending it logged the carry's 'default'
        # plumbing as knobs ('size.default.*') whose values contradict
        # every run after the first.
        self.register_memory()

        def run(asof):
            pipeline = banking_pipeline()
            pipeline["size"] = NodeSpec(
                uses="synth-capital",
                inputs=dict(pipeline["size"].inputs),
                params={
                    "$prev": "size.no_such_output",  # misses -> default binds
                    "default": {"bankroll": 1000.0, "stake_frac": 0.1},
                },
            )
            doc = bdoc(
                tmp_path,
                pipeline=pipeline,
                tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
            )
            return run_document(doc, asof=asof, registry=registry)

        first = run("2026-01-01")
        first_logged = dict(MemoryTracker.instances[-1].logged_params)
        second = run("2026-01-08")
        second_logged = dict(MemoryTracker.instances[-1].logged_params)
        assert first.state == "ran" and second.state == "ran"
        assert second.prev_run == first.run_dir  # a real series
        assert not [k for k in first_logged if k.startswith("size.")]
        assert not [k for k in second_logged if k.startswith("size.")]

    def test_a_param_wired_to_a_node_output_logs_the_REFERENCE(
        self, tmp_path, registry
    ):
        # A param declared as '$node.port' is WIRING, not a hyperparameter:
        # its resolved value is another node's output — already recorded as
        # that node's output, possibly a whole dataset, and meaningless as a
        # sink filter. The declaration is what identifies the config, and it
        # is bounded, so it is what gets logged.
        self.register_memory()
        pipeline = banking_pipeline()
        pipeline["clip"] = NodeSpec(
            uses="synth-clip",
            inputs={"events": "$events.events"},
            params={"lo": 0.02, "hi": 0.98, "note": "$events.instruments"},
        )
        doc = bdoc(
            tmp_path,
            pipeline=pipeline,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        logged = MemoryTracker.instances[-1].logged_params
        assert result.state == "ran"
        assert logged["clip.note"] == "$events.instruments"
        assert logged["clip.lo"] == 0.02  # ordinary knobs still log their value

    def test_log_params_is_called_once_with_identity_and_hyperparameters(
        self, tmp_path, registry
    ):
        # Round-4 ruling (finding 5): the Tracker contract is ONE
        # log_params per run, at run start — the five identity fields and
        # the flattened declared params in a single payload. A sink that
        # REPLACES on each call (the blunt reading of the seam) therefore
        # cannot lose a field, and an mlflow-style sink that refuses to
        # restate a param is never asked to.
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(
                sinks=(SinkConfig(kind="tests.pipeline.test_driver:ReplacingTracker"),)
            ),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        sink = ReplacingTracker.instances[-1]
        assert len(sink.payloads) == 1
        payload = sink.payloads[0]
        assert payload["name"] == doc.name
        assert payload["asof"] == ASOF
        assert payload["document_hash"] == doc.hash
        assert payload["run_hash"] == result.run_hash
        assert payload["nodes"].startswith("events,")
        assert payload["events.n_events"] == 432  # knobs ride the same call

    def test_a_node_that_later_fails_still_logged_its_declared_params(
        self, tmp_path, registry
    ):
        # Round-4 ruling (findings 1+2+3 and 5): the payload goes out at
        # run start and follows the DECLARED document, so what a node's
        # materialization later does cannot take its params back — a
        # crashed run is exactly the one you want to find in a sink by its
        # config. The reference that fails to resolve logs as written.
        self.register_memory()
        pipeline = banking_pipeline()
        pipeline["clip"] = NodeSpec(
            uses="synth-clip",
            inputs=dict(pipeline["clip"].inputs),
            params={**pipeline["clip"].params, "bad": "$splits.no_such_key"},
        )
        doc = bdoc(
            tmp_path,
            pipeline=pipeline,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        logged = MemoryTracker.instances[-1].logged_params
        assert result.node_states["clip"] == "error"  # materialization failed…
        assert logged["clip.bad"] == "$splits.no_such_key"  # …the config landed
        assert logged["clip.lo"] == 0.02

    def test_a_node_the_run_never_reached_still_logged_its_declared_params(
        self, tmp_path, registry
    ):
        # Round-4 ruling (finding 5): 'once, at run start' means the
        # payload cannot depend on how far the run got — an aborted run
        # lands the same declared config a completed one does, which is
        # what lets a sink answer "which configs crash".
        self.register_memory()
        pipeline = banking_pipeline()
        pipeline["qhat"] = NodeSpec(
            uses="synth-train",
            mode="train",
            inputs={"events": "$clip.events"},
            params={"min_train": 10_000},
        )
        doc = bdoc(
            tmp_path,
            pipeline=pipeline,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        logged = MemoryTracker.instances[-1].logged_params
        assert result.node_states["qhat"] == "error"
        assert logged["qhat.min_train"] == 10_000
        assert result.node_states["size"] == "not_run"
        assert logged["size.stake_frac"] == 0.1  # declared, so still logged

    def test_sink_closes_even_when_a_node_errors(self, tmp_path, registry):
        self.register_memory()
        pipeline = banking_pipeline()
        pipeline["qhat"] = NodeSpec(
            uses="synth-train",
            mode="train",
            inputs={"events": "$clip.events"},
            params={"min_train": 10_000},
        )
        doc = bdoc(
            tmp_path,
            pipeline=pipeline,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        assert result.state == "error"
        assert MemoryTracker.instances[-1].closed

    def test_unknown_sink_kind_refused_before_any_write(self, tmp_path, registry):
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="wandb"),)),
        )
        with pytest.raises(ConfigError, match="not registered"):
            run_document(doc, asof=ASOF, registry=registry)
        assert not any(
            entry.startswith("synth-banking-") for entry in os.listdir(tmp_path)
        )

    def test_class_ref_sink_constructs_and_receives(self, tmp_path, registry):
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(
                sinks=(SinkConfig(kind="dskit.pipeline.testing:MemoryTracker"),)
            ),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        sink = MemoryTracker.instances[-1]
        assert sink.logged_params["run_hash"] == result.run_hash and sink.closed

    def test_class_ref_sink_missing_the_seam_refused(self, tmp_path, registry):
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(
                sinks=(SinkConfig(kind="tests.pipeline.refhelpers:NotASink"),)
            ),
        )
        with pytest.raises(ConfigError, match="Tracker seam"):
            run_document(doc, asof=ASOF, registry=registry)


class TestSplitsRefs:
    def base_pipeline(self):
        return {
            "events": NodeSpec(uses="synth-events", params={"n_events": 8}),
            "rep": NodeSpec(
                uses="synth-report",
                inputs={"n": "$events.newest_ms"},
                params={"cut": "$splits.train_end_ms"},
            ),
        }

    def test_splits_fields_materialize_into_params(self, tmp_path, registry):
        doc = bdoc(tmp_path, pipeline=self.base_pipeline())
        result = run_document(doc, asof=ASOF, registry=registry)
        assert result.state == "ran"
        report = read_json(
            result.run_dir, os.path.join("artifacts", "rep", "report.json")
        )
        assert report["n"] > 0

    def test_unknown_splits_field_fails_the_node_loudly(self, tmp_path, registry):
        pipeline = self.base_pipeline()
        pipeline["rep"] = NodeSpec(
            uses="synth-report",
            inputs={"n": "$events.newest_ms"},
            params={"cut": "$splits.t9"},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "error"
        assert "no 't9'" in result.error and "train_end_ms" in result.error


class InfMetricsNode(Node):
    """A score-shaped node whose loss diverges (routine for logloss)."""

    role = "transform"

    def run(self, ctx, inputs):
        return {"metrics": {"loss": float("inf")}, "n": 3}


class EchoParamsNode(Node):
    """Returns the params it was constructed with — proves materialization."""

    role = "transform"

    def run(self, ctx, inputs):
        return {"params_seen": self.params}


class FlakySink:
    """A sink whose logging fails mid-run — telemetry must not kill runs."""

    def __init__(self, params):
        self.closed = False

    def log_params(self, mapping):
        raise ConnectionError("mlflow is down")

    def log_metrics(self, node, mapping):
        raise ConnectionError("mlflow is down")

    def close(self):
        self.closed = True


class CountingSink:
    """Tracks open/close pairing across driver refusals."""

    instances = []

    def __init__(self, params):
        self.closed = False
        CountingSink.instances.append(self)

    def log_params(self, mapping):
        pass

    def log_metrics(self, node, mapping):
        pass

    def close(self):
        self.closed = True


class TestReviewRegressions:
    """Each test pins one finding of the 2026-08-14 skeptic review."""

    def test_data_node_params_must_be_fully_literal(self, tmp_path, registry):
        # $splits/$prev in a data node's params used to ride through as
        # literals (the resolve-time instance was built from raw params).
        pipeline = {
            "events": NodeSpec(
                uses="synth-events", params={"start_ms": "$splits.train_end_ms"}
            )
        }
        with pytest.raises(ConfigError, match="fully literal"):
            run_document(
                bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
            )
        pipeline = {
            "events": NodeSpec(
                uses="synth-events",
                params={"seed": {"$prev": "events.newest_ms", "default": 7}},
            )
        }
        with pytest.raises(ConfigError, match="fully literal"):
            run_document(
                bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
            )

    def test_infinite_metrics_still_record(self, tmp_path, registry):
        # inf/NaN in outputs used to crash step 6 RECORD and strand the dir.
        pipeline = {
            "events": NodeSpec(uses="synth-events", params={"n_events": 8}),
            "diverged": NodeSpec(
                uses="tests.pipeline.test_driver:InfMetricsNode",
                inputs={"events": "$events.events"},
            ),
        }
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "ran"
        record = read_json(result.run_dir, os.path.join("nodes", "02-diverged.json"))
        assert record["status"] == "ok"
        assert read_json(result.run_dir, "result.json")["state"] == "ran"

    def test_flaky_sink_cannot_kill_the_run(self, tmp_path, registry):
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(
                sinks=(SinkConfig(kind="tests.pipeline.test_driver:FlakySink"),)
            ),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        assert result.state == "ran"

    def test_the_run_hash_ignores_the_tracking_section(self, tmp_path, registry):
        """The driver's own copy of the exclusion list, pinned (Ruling 1).

        ``run_hash`` is computed from the document minus
        ``DOC_NON_IDENTITY_SECTIONS``, in the driver rather than through
        ``PipelineDocument.hash`` — a second copy of the recipe, so a
        second place ``tracking`` could stay graded. Two documents
        differing ONLY in whether they declare a sink therefore have to
        name the same run directory, which the occupied-dir refusal
        reports for us.
        """
        run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        with pytest.raises(ValueError, match="already exists"):
            run_document(
                bdoc(
                    tmp_path,
                    tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
                ),
                asof=ASOF,
                registry=registry,
            )

    def test_sinks_close_on_resolve_time_refusal(self, tmp_path, registry):
        # Identical document twice: the second run hits the occupied run
        # dir, which is what makes it a resolve-time refusal.
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(
                sinks=(SinkConfig(kind="tests.pipeline.test_driver:CountingSink"),)
            ),
        )
        run_document(doc, asof=ASOF, registry=registry)
        with pytest.raises(ValueError, match="already exists"):
            run_document(doc, asof=ASOF, registry=registry)
        assert len(CountingSink.instances) >= 2
        assert all(s.closed for s in CountingSink.instances[-2:])

    def test_same_asof_prev_run_picked_by_mtime_not_hash(self, tmp_path, registry):
        # Two same-day prior runs: the newer by mtime must win, whatever
        # the hash suffix's hex ordering says.
        older = tmp_path / "synth-banking-2026-01-01-ffffffff"
        newer = tmp_path / "synth-banking-2026-01-01-00000000"
        for i, d in enumerate((older, newer)):
            d.mkdir()
            (d / "carry.json").write_text(
                json.dumps({"size": {"final_bankroll": 111.0 * (i + 1)}})
            )
            os.utime(d, (1000 + i, 1000 + i))
        result = run_document(bdoc(tmp_path), asof="2026-01-08", registry=registry)
        assert result.prev_run == str(newer)
        # bankroll 222 carried: final = 222 * 1.02
        assert result.outputs["size"]["final_bankroll"] == pytest.approx(226.44)

    def test_tuple_params_materialize(self, tmp_path, registry):
        pipeline = {
            "events": NodeSpec(uses="synth-events", params={"n_events": 8}),
            "echo": NodeSpec(
                uses="tests.pipeline.test_driver:EchoParamsNode",
                inputs={"events": "$events.events"},
                params={"srcs": ("$events.newest_ms",)},
            ),
        }
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "ran"
        # The ref inside the tuple resolved — no literal ride-through.
        (value,) = result.outputs["echo"]["params_seen"]["srcs"]
        assert value == result.outputs["events"]["newest_ms"]

    def test_prev_default_must_be_literal(self):
        with pytest.raises(ConfigError, match="default must be a literal"):
            NodeSpec(
                uses="k",
                params={"w": {"$prev": "e.seen", "default": "$events.newest_ms"}},
            )

    def test_params_only_stat_test_reference_does_not_gate_capital(self, registry):
        from dskit.pipeline.planner import plan as plan_document

        pipeline = banking_pipeline()
        pipeline["size"] = NodeSpec(
            uses="synth-capital",
            inputs={"signal": "$qhat.signal", "survivors": "$family.instruments"},
            params={"bankroll": 100.0, "note": "$edge_test.pvalues"},
        )
        with pytest.raises(ConfigError, match="un-gated capital"):
            plan_document(banking_document(pipeline=pipeline), registry)

    def test_trailing_train_days_bound_materializes(self):
        bounded = TrailingSplitSpec(test_days=14, val_days=28, train_days=30)
        cuts = bounded.materialize(100 * 24 * 60 * 60 * 1000)
        assert cuts.train_start_ms == cuts.train_end_ms - 30 * 24 * 60 * 60 * 1000 + 1


def _live_streams(logger):
    """The driver's live-stderr kind: StreamHandlers that are not files."""
    return sum(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    )


class LoggerProbeNode(Node):
    """Logs one INFO line and reports the live handlers it saw mid-run."""

    role = "transform"
    outputs = ("probe",)

    def run(self, ctx, inputs):
        self.log.info("probe-sentinel-line")
        return {
            "probe": {
                "pipeline_live": _live_streams(logging.getLogger("dskit.pipeline")),
                "root_live": _live_streams(logging.getLogger()),
            }
        }


class RaisingProbeNode(Node):
    """Raises, carrying the mid-run live-handler count in the message."""

    role = "transform"

    def run(self, ctx, inputs):
        n = _live_streams(logging.getLogger("dskit.pipeline"))
        raise RuntimeError(f"boom live-streams-mid-error={n}")


def probe_doc(tmp_path, node="LoggerProbeNode"):
    pipeline = {
        "events": NodeSpec(uses="synth-events", params={"n_events": 8}),
        "probe": NodeSpec(
            uses=f"tests.pipeline.test_driver:{node}",
            inputs={"events": "$events.events"},
        ),
    }
    return PipelineDocument(
        name="stream-probe",
        pipeline=pipeline,
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )


def operator_terminal():
    """Strip pytest's own live StreamHandlers from the root logger.

    The driver streams only when the caller has no live (non-file)
    StreamHandler anywhere — and pytest's log-capture handlers are
    exactly that, so under test the guard sees an embedding application
    and rightly declines. Stripping them simulates the bare operator
    terminal the feature exists for. Called from the test BODY, not a
    fixture: fixtures run in the setup phase and pytest re-attaches its
    capture handlers when the call phase opens. No restore: pytest's own
    end-of-phase removal is membership-checked (a no-op here) and it
    re-attaches the same reused handlers at the next phase boundary.
    The pipeline logger is swept too, so a leaked handler (the exact
    defect the teardown tests exist to catch) cannot ride into the next
    test's ``before`` snapshot and mask its assertions.
    """
    for logger in (logging.getLogger(), logging.getLogger("dskit.pipeline")):
        for handler in list(logger.handlers):
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, logging.FileHandler
            ):
                logger.removeHandler(handler)


class TestLiveStderrStreaming:
    """ADR-0025 residual: INFO lines stream live to stderr during a run."""

    def test_node_info_lines_stream_bare_to_stderr(self, tmp_path, registry, capsys):
        operator_terminal()
        result = run_document(probe_doc(tmp_path), asof=ASOF, registry=registry)
        assert result.state == "ran"
        err = capsys.readouterr().err
        # Bare %(message)s lines — no asctime/name/level prefix — and the
        # driver's own narration streams alongside the node's records.
        assert "probe-sentinel-line" in err.splitlines()
        assert "node probe: start" in err
        # Streamed, not doubled: run.log still carries each line once.
        with open(os.path.join(result.run_dir, "run.log"), encoding="utf-8") as fh:
            assert fh.read().count("probe-sentinel-line") == 1

    def test_handler_on_pipeline_logger_only_during_the_run(self, tmp_path, registry):
        operator_terminal()
        pipeline_logger = logging.getLogger("dskit.pipeline")
        before = list(pipeline_logger.handlers)
        result = run_document(probe_doc(tmp_path), asof=ASOF, registry=registry)
        probe = result.outputs["probe"]["probe"]
        assert probe["pipeline_live"] == 1  # installed on dskit.pipeline…
        assert probe["root_live"] == 0  # …never on the root logger
        assert pipeline_logger.handlers == before  # and removed at the end

    def test_handler_removed_when_a_node_raises(self, tmp_path, registry):
        operator_terminal()
        pipeline_logger = logging.getLogger("dskit.pipeline")
        before = list(pipeline_logger.handlers)
        result = run_document(
            probe_doc(tmp_path, node="RaisingProbeNode"), asof=ASOF, registry=registry
        )
        assert result.state == "error"
        # Streaming was live when the node blew up…
        assert "live-streams-mid-error=1" in result.error
        # …and the failure path still tears it down: nothing leaks.
        assert pipeline_logger.handlers == before

    def test_a_callers_own_stream_handler_is_never_doubled(self, tmp_path, registry):
        operator_terminal()
        own = logging.StreamHandler(io.StringIO())
        root = logging.getLogger()
        root.addHandler(own)
        try:
            result = run_document(probe_doc(tmp_path), asof=ASOF, registry=registry)
        finally:
            root.removeHandler(own)
        probe = result.outputs["probe"]["probe"]
        assert probe["pipeline_live"] == 0  # driver declined — caller streams
        # The caller's handler still gets the lines, via propagation.
        assert "probe-sentinel-line" in own.stream.getvalue()


class TestHelpers:
    def test_summarize_shapes(self):
        assert _summarize(3.5) == 3.5 and _summarize(True) is True
        assert _summarize(None) is None
        truncated = _summarize("x" * 300)
        assert truncated.endswith("…") and len(truncated) == 201
        assert _summarize("short") == "short"
        assert _summarize(list(range(5))) == {"type": "list", "len": 5}
        assert _summarize({"a": 1}) == {"type": "dict", "len": 1}
        assert _summarize(object())["type"] == "object"

    def test_carryable_rules(self):
        assert _carryable(1000.0) == (1000.0, True)
        assert _carryable(object()) == (None, False)
        assert _carryable("x" * 30_000) == (None, False)
        stream = [{"i": 0}] * 10_001
        assert _too_big_to_carry(stream) is True
        assert _carryable(stream) == (None, False)
        summary = _summarize(stream)
        assert _is_summary(summary)
        assert _summarize(summary) == summary
        assert _carryable(summary) == (None, False)


class FatSource(Node):
    """Emit ``n`` tiny records so release can fire without a real tape."""

    role = "data"
    outputs = ("records", "n")

    def fingerprint(self):
        return {"kind": "fat", "n": int(self.params["n"])}

    def run(self, ctx, inputs):
        n = int(self.params["n"])
        return {"records": [{"i": i} for i in range(n)], "n": n}


class HeadRows(Node):
    """Keep the first upstream row so the source is spent."""

    role = "transform"
    outputs = ("records",)

    def run(self, ctx, inputs):
        rows = inputs["records"]
        return {"records": list(rows[:1])}


def _fat_registry():
    registry = NodeKindRegistry()
    registry.register("fat-src", FatSource)
    registry.register("head-rows", HeadRows)
    return registry


def _fat_doc(tmp_path, n, consumers=("kept",)):
    pipeline = {
        "src": NodeSpec(uses="fat-src", params={"n": n}),
    }
    for key in consumers:
        pipeline[key] = NodeSpec(
            uses="head-rows",
            inputs={"records": "$src.records"},
        )
    return PipelineDocument(
        name="release-spent",
        pipeline=pipeline,
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )


class HpoEvidenceSource(Node):
    """Emit a realistically sized, explicitly durable 24-trial ledger."""

    role = "data"
    outputs = ("hpo_ledger",)

    def run(self, ctx, inputs):
        rows = [
            {
                "candidate_id": f"trial-{index:02d}",
                "overrides": {"num_leaves": index + 2, "learning_rate": 0.01},
                "score": index / 100.0,
                "se": 0.01,
                "diagnostics": {"bootstrap_scores": [index / 100.0] * 200},
            }
            for index in range(24)
        ]
        payload = {"ledger": {"rows": rows}, "selection": {"trial": 23}}
        durable = getattr(node_module, "JsonArtifact", lambda value: value)
        return {"hpo_ledger": durable(payload)}


class TunedEvidenceSource(Node):
    """A tunable node whose durable evidence records the value it ran with.

    The payload is derived from the tuned param, so the bytes on disk say
    WHICH pass wrote them.
    """

    role = "train"
    outputs = ("value", "episodes")

    def run(self, ctx, inputs):
        value = float(self.params["theta"])
        durable = getattr(node_module, "JsonArtifact", lambda value: value)
        return {"value": value, "episodes": durable({"by": "evid", "scored": value})}


class EvidenceRelay(Node):
    """A second re-executed node between the other two.

    Three nodes, because at TWO "the first", "the first and the last" and
    "all of them" are the same list -- and the shipped subgraphs are
    longer: `["clip", "market", "qhat", "validate"]`.
    """

    role = "train"
    outputs = ("value", "episodes")

    def run(self, ctx, inputs):
        value = float(inputs["value"])
        durable = getattr(node_module, "JsonArtifact", lambda value: value)
        return {"value": value, "episodes": durable({"by": "relay", "scored": value})}


class EvidenceScore(Node):
    """Scores what the tunable node produced; the search's objective.

    It emits durable evidence of its OWN, because the only kind in the
    library that produces a ``JsonArtifact`` -- ``sb3-eval-episodes`` -- is
    a ``score`` node, and a search's objective target is a score node. So
    the artifact-bearing node is always LAST in ``winner_reran``, never
    first: ``["theta", "val"]``, ``["clip", "market", "qhat", "validate"]``.
    A fixture that emitted evidence only from the tunable node would pin
    the fix at the one position the motivating kind can never occupy.
    """

    role = "score"
    outputs = ("metrics", "episodes")

    def run(self, ctx, inputs):
        value = float(inputs["value"])
        durable = getattr(node_module, "JsonArtifact", lambda value: value)
        return {
            "metrics": {"loss": (value - 3.0) ** 2},
            "episodes": durable({"by": "val", "scored": value}),
        }


def test_a_search_winners_json_artifact_is_persisted_not_the_losing_pass(tmp_path):
    """The winner re-execution replaces a node's outputs IN PLACE, and
    those outputs are what the records and ``$prev`` carry -- so its
    durable artifacts must be written from that pass too.

    Before this held, a run exited ``ran`` reporting the winner's metrics
    while ``artifacts/json/`` held only the base pass's record -- the
    configuration the search REJECTED -- and the node record carried a bare
    ``{"type": "JsonArtifact"}`` where its manifest belongs, so a reader
    could not even tell the bytes were stale. ``resolve_json_artifact``
    refused the raw wrapper, so the documented seam returned nothing.
    """
    from dskit.pipeline.document import TimeSplitConfig
    from dskit.pipeline.kinds_search import register as register_search

    registry = NodeKindRegistry()
    registry.register("tuned-evidence", TunedEvidenceSource)
    registry.register("evidence-relay", EvidenceRelay)
    registry.register("evidence-score", EvidenceScore)
    register_search(registry)
    document = PipelineDocument(
        name="winner-evidence",
        pipeline={
            "evid": NodeSpec(uses="tuned-evidence", params={"theta": 10.0}),
            "relay": NodeSpec(
                uses="evidence-relay", inputs={"value": "$evid.value"}
            ),
            "val": NodeSpec(
                uses="evidence-score",
                inputs={"value": "$relay.value"},
                params={"split": "val"},
            ),
            "search": NodeSpec(
                uses="hpo-grid",
                params={
                    "space": {"evid.theta": [0.0, 3.0]},
                    "objective": "$val.metrics.loss",
                    "select": "min",
                },
            ),
        },
        splits=TimeSplitConfig(train_end_ms=1, val_end_ms=2, test_end_ms=3),
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )

    result = run_document(document, asof=ASOF, registry=registry, journal=False)

    assert result.state == "ran"
    assert result.outputs["search"]["best_params"] == {"evid.theta": 3.0}
    # the winner pass really did replace the outputs
    assert result.outputs["evid"]["value"] == 3.0

    # BOTH re-executed nodes, so membership is pinned rather than the
    # first element: the search re-ran ["evid", "val"], and the kind this
    # fix exists for would sit at the END of such a list.
    search_record = read_json(
        result.run_dir, os.path.join("nodes", "04-search.json")
    )
    assert search_record["winner_reran"] == ["evid", "relay", "val"]
    manifests = {}
    for node_key, record_name in (
        ("evid", "01-evid.json"),
        ("relay", "02-relay.json"),
        ("val", "03-val.json"),
    ):
        manifest = result.outputs[node_key]["episodes"]
        assert set(manifest) == {"path", "sha256", "bytes", "media_type"}, node_key
        # Each payload names its own node. Persistence is CONTENT-addressed,
        # so identical payloads would share one manifest and every check
        # below would be satisfied by some other node's artifact -- a driver
        # stamping one node's manifest onto the rest would read as correct.
        assert resolve_json_artifact(result.run_dir, manifest) == {
            "by": node_key, "scored": 3.0,
        }
        record = read_json(result.run_dir, os.path.join("nodes", record_name))
        assert record["outputs"]["episodes"]["sha256"] == manifest["sha256"], node_key
        manifests[node_key] = manifest["sha256"]
    assert len(set(manifests.values())) == 3, manifests
    # and $prev binds it: a dropped manifest silently deletes the port here
    carry = read_json(result.run_dir, "carry.json")
    assert set(carry["val"]) == {"metrics", "episodes"}


def test_explicit_json_artifact_survives_driver_recording_with_digest_manifest(tmp_path):
    registry = NodeKindRegistry()
    registry.register("hpo-evidence-src", HpoEvidenceSource)
    document = PipelineDocument(
        name="durable-hpo-evidence",
        pipeline={"scan_h01": NodeSpec(uses="hpo-evidence-src")},
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )

    result = run_document(document, asof=ASOF, registry=registry, journal=False)

    manifest = result.outputs["scan_h01"]["hpo_ledger"]
    assert set(manifest) == {"path", "sha256", "bytes", "media_type"}
    assert manifest["media_type"] == "application/json"
    assert manifest["bytes"] > 20_000
    artifact_path = os.path.join(result.run_dir, manifest["path"])
    assert os.path.isfile(artifact_path)
    assert _file_digest(artifact_path) == manifest["sha256"]
    with open(artifact_path, encoding="utf-8") as handle:
        assert len(json.load(handle)["ledger"]["rows"]) == 24
    assert len(resolve_json_artifact(result.run_dir, manifest)["ledger"]["rows"]) == 24
    record = read_json(result.run_dir, "nodes/01-scan_h01.json")
    assert record["outputs"]["hpo_ledger"] == manifest
    assert read_json(result.run_dir, "carry.json")["scan_h01"]["hpo_ledger"] == manifest


def test_json_artifact_resolver_refuses_tampered_bytes(tmp_path):
    registry = NodeKindRegistry()
    registry.register("hpo-evidence-src", HpoEvidenceSource)
    document = PipelineDocument(
        name="durable-hpo-evidence",
        pipeline={"scan_h01": NodeSpec(uses="hpo-evidence-src")},
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )
    result = run_document(document, asof=ASOF, registry=registry, journal=False)
    manifest = result.outputs["scan_h01"]["hpo_ledger"]
    artifact_path = os.path.join(result.run_dir, manifest["path"])
    with open(artifact_path, "ab") as handle:
        handle.write(b" ")

    with pytest.raises(ValueError, match="byte count"):
        resolve_json_artifact(result.run_dir, manifest)


def test_forged_manifest_is_not_special_and_resolver_refuses_it(tmp_path):
    class ForgedManifestSource(Node):
        role = "data"
        outputs = ("payload",)

        def run(self, ctx, inputs):
            return {"payload": forged}

    forged = {
        "path": "../../forged.json",
        "sha256": "z" * 64,
        "bytes": 0,
        "media_type": "application/json",
    }
    registry = NodeKindRegistry()
    registry.register("forged-manifest-src", ForgedManifestSource)
    document = PipelineDocument(
        name="forged-manifest",
        pipeline={"src": NodeSpec(uses="forged-manifest-src")},
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )
    result = run_document(document, asof=ASOF, registry=registry, journal=False)

    assert _summarize(forged) == {"type": "dict", "len": 4}
    record = read_json(result.run_dir, "nodes/01-src.json")
    assert record["outputs"]["payload"] == {"type": "dict", "len": 4}
    assert read_json(result.run_dir, "carry.json")["src"]["payload"] == forged
    with pytest.raises(ValueError, match="manifest"):
        resolve_json_artifact(tmp_path, forged)


@pytest.mark.parametrize(
    "path",
    ("/tmp/foreign.json", "artifacts/json/../foreign.json", "artifacts\\json\\x.json"),
)
def test_json_artifact_resolver_refuses_noncanonical_paths(tmp_path, path):
    manifest = {
        "path": path,
        "sha256": "0" * 64,
        "bytes": 0,
        "media_type": "application/json",
    }
    with pytest.raises(ValueError, match="manifest path"):
        resolve_json_artifact(tmp_path, manifest)


def test_json_artifact_resolver_refuses_missing_and_digest_drift(tmp_path):
    digest = "0" * 64
    manifest = {
        "path": f"artifacts/json/{digest}.json",
        "sha256": digest,
        "bytes": 3,
        "media_type": "application/json",
    }
    with pytest.raises(ValueError, match="missing"):
        resolve_json_artifact(tmp_path, manifest)

    directory = tmp_path / "artifacts" / "json"
    directory.mkdir(parents=True)
    (directory / f"{digest}.json").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="digest"):
        resolve_json_artifact(tmp_path, manifest)


def _file_digest(path):
    import hashlib

    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class TestSpentRelease:
    def test_release_min_len_is_the_one_name(self):
        assert _RELEASE_MIN_LEN == 256

    def test_spent_stream_is_summarized_after_its_last_reader(self, tmp_path):
        n = _RELEASE_MIN_LEN
        result = run_document(
            _fat_doc(tmp_path, n, consumers=("kept",)),
            asof=ASOF,
            registry=_fat_registry(),
        )
        assert result.state == "ran"
        assert result.outputs["src"]["records"] == {"type": "list", "len": n}
        assert result.outputs["src"]["n"] == n
        assert result.outputs["kept"]["records"] == [{"i": 0}]
        record = read_json(os.path.join(result.run_dir, "nodes"), "01-src.json")
        assert record["outputs"]["records"] == {"type": "list", "len": n}
        carry = read_json(result.run_dir, "carry.json")
        assert "records" not in carry.get("src", {})
        assert carry["src"]["n"] == n

    def test_two_readers_keep_the_stream_until_both_finish(self, tmp_path):
        n = _RELEASE_MIN_LEN
        result = run_document(
            _fat_doc(tmp_path, n, consumers=("left", "right")),
            asof=ASOF,
            registry=_fat_registry(),
        )
        assert result.outputs["src"]["records"] == {"type": "list", "len": n}
        assert result.outputs["left"]["records"] == [{"i": 0}]
        assert result.outputs["right"]["records"] == [{"i": 0}]

    def test_a_short_stream_stays_for_the_caller(self, tmp_path):
        result = run_document(
            _fat_doc(tmp_path, 12, consumers=("kept",)),
            asof=ASOF,
            registry=_fat_registry(),
        )
        assert result.outputs["src"]["records"] == [{"i": i} for i in range(12)]

    def test_node_metrics_extraction(self):
        out = _node_metrics(
            {
                "final_bankroll": 1020.0,
                "ok": True,
                "metrics": {"loss": 0.2, "n": 96, "tag": "val"},
                "positions": {"A": 1.0},
            }
        )
        assert out == {
            "final_bankroll": 1020.0,
            "metrics.loss": 0.2,
            "metrics.n": 96,
        }


class TestRunAttestationCompleted:
    def test_a_clean_run_attests_completed(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        assert RunAttestation(result.run_dir).completed() is True

    def test_a_halted_run_does_not_attest_completed(self, tmp_path, registry):
        pipeline = banking_pipeline()
        pipeline["family"] = NodeSpec(
            uses="synth-eligibility",
            inputs={"counts": "$bank.counts"},
            params={"min_events": 10_000},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "halted"
        assert RunAttestation(result.run_dir).completed() is False

    def test_an_errored_run_does_not_attest_completed(self, tmp_path, registry):
        pipeline = banking_pipeline()
        pipeline["qhat"] = NodeSpec(
            uses="synth-train",
            mode="train",
            inputs={"events": "$clip.events"},
            params={"min_train": 10_000},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "error"
        assert RunAttestation(result.run_dir).completed() is False

    def test_a_missing_run_dir_does_not_attest_completed(self, tmp_path):
        assert RunAttestation(tmp_path / "never-ran").completed() is False

    def test_a_malformed_result_json_does_not_attest_completed(self, tmp_path):
        run_dir = tmp_path / "half-written"
        run_dir.mkdir()
        (run_dir / "result.json").write_text("not json", encoding="utf-8")
        assert RunAttestation(run_dir).completed() is False


class TestRunAttestationNodeCompleted:
    def test_an_ok_node_attests_completed(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        assert RunAttestation(result.run_dir).node_completed("market") is True

    def test_an_errored_node_does_not_attest_completed(self, tmp_path, registry):
        pipeline = banking_pipeline()
        pipeline["qhat"] = NodeSpec(
            uses="synth-train",
            mode="train",
            inputs={"events": "$clip.events"},
            params={"min_train": 10_000},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.node_states["qhat"] == "error"
        assert RunAttestation(result.run_dir).node_completed("qhat") is False

    def test_a_halted_node_does_not_attest_completed(self, tmp_path, registry):
        pipeline = banking_pipeline()
        pipeline["family"] = NodeSpec(
            uses="synth-eligibility",
            inputs={"counts": "$bank.counts"},
            params={"min_events": 10_000},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.node_states["report"] == "halted"
        assert RunAttestation(result.run_dir).node_completed("report") is False

    def test_a_not_run_node_does_not_attest_completed(self, tmp_path, registry):
        pipeline = banking_pipeline()
        pipeline["qhat"] = NodeSpec(
            uses="synth-train",
            mode="train",
            inputs={"events": "$clip.events"},
            params={"min_train": 10_000},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.node_states["size"] == "not_run"
        assert RunAttestation(result.run_dir).node_completed("size") is False

    def test_an_unknown_node_key_does_not_attest_completed(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        assert RunAttestation(result.run_dir).node_completed("no-such-node") is False

    def test_a_record_claiming_a_different_node_name_does_not_attest(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        nodes_dir = os.path.join(result.run_dir, "nodes")
        target = next(f for f in os.listdir(nodes_dir) if f.endswith("-market.json"))
        record = read_json(nodes_dir, target)
        assert record["status"] == "ok"
        record["node"] = "not-market"
        with open(os.path.join(nodes_dir, target), "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        assert RunAttestation(result.run_dir).node_completed("market") is False


class TestRunAttestationBindsDocumentIdentity:
    def test_a_clean_run_binds_its_own_document_hash(self, tmp_path, registry):
        doc = bdoc(tmp_path)
        result = run_document(doc, asof=ASOF, registry=registry)
        assert RunAttestation(result.run_dir).binds_document_identity(doc.hash) is True

    def test_a_wrong_document_hash_does_not_bind(self, tmp_path, registry):
        doc = bdoc(tmp_path)
        result = run_document(doc, asof=ASOF, registry=registry)
        wrong = "0" * 64
        assert wrong != doc.hash
        assert RunAttestation(result.run_dir).binds_document_identity(wrong) is False

    def test_a_resolved_json_hand_edited_to_claim_a_hash_config_does_not_reproduce(
        self, tmp_path, registry
    ):
        doc = bdoc(tmp_path)
        result = run_document(doc, asof=ASOF, registry=registry)
        resolved = read_json(result.run_dir, "resolved.json")
        forged = "1" * 64
        resolved["document_hash"] = forged
        with open(
            os.path.join(result.run_dir, "resolved.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(resolved, fh)
        assert RunAttestation(result.run_dir).binds_document_identity(forged) is False

    def test_a_config_json_substituted_for_a_different_document_does_not_bind(
        self, tmp_path, registry
    ):
        doc = bdoc(tmp_path)
        result = run_document(doc, asof=ASOF, registry=registry)
        other = bdoc(tmp_path, name="a-different-document")
        with open(
            os.path.join(result.run_dir, "config.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(other.to_obj(), fh)
        # resolved.json's claim is untouched, so it still names doc.hash —
        # but config.json no longer reproduces it under the pinned recipe.
        assert RunAttestation(result.run_dir).binds_document_identity(doc.hash) is False

    def test_a_missing_config_json_does_not_bind(self, tmp_path, registry):
        doc = bdoc(tmp_path)
        result = run_document(doc, asof=ASOF, registry=registry)
        os.remove(os.path.join(result.run_dir, "config.json"))
        assert RunAttestation(result.run_dir).binds_document_identity(doc.hash) is False


class TestRunAttestationNodeOutputForDocument:
    """The composed guarantee ADR-0116 actually needs — see ADR-0119's follow-up."""

    def test_a_clean_run_attests_the_composed_guarantee(self, tmp_path, registry):
        doc = bdoc(tmp_path)
        result = run_document(doc, asof=ASOF, registry=registry)
        assert (
            RunAttestation(result.run_dir).node_output_for_document("market", doc.hash)
            is True
        )

    def test_a_wrong_document_hash_does_not_attest(self, tmp_path, registry):
        doc = bdoc(tmp_path)
        result = run_document(doc, asof=ASOF, registry=registry)
        wrong = "0" * 64
        assert (
            RunAttestation(result.run_dir).node_output_for_document("market", wrong)
            is False
        )

    def test_an_uncompleted_node_does_not_attest(self, tmp_path, registry):
        doc = bdoc(tmp_path)
        result = run_document(doc, asof=ASOF, registry=registry)
        assert (
            RunAttestation(result.run_dir).node_output_for_document(
                "no-such-node", doc.hash
            )
            is False
        )

    def test_the_skeptic_forgery_defeats_the_three_naive_checks_but_not_this_one(
        self, tmp_path, registry
    ):
        """Reproduce the exact skeptic proof against ADR-0119's first cut.

        Run two genuine documents. Take B's real completed run, hand-edit
        its ``resolved.json`` to claim A's document identity, and
        substitute A's own genuine ``config.json``. The three individual
        primitives, composed naively, are then all fooled — that is the
        finding. The node record inside B's run dir was stamped with B's
        own ``document_hash`` honestly, at the moment B actually executed,
        so the composed method catches what the naive composition misses.
        """
        doc_a = bdoc(tmp_path, name="document-a")
        run_document(doc_a, asof=ASOF, registry=registry)
        doc_b = bdoc(tmp_path, name="document-b")
        result_b = run_document(doc_b, asof=ASOF, registry=registry)

        resolved = read_json(result_b.run_dir, "resolved.json")
        resolved["document_hash"] = doc_a.hash
        with open(
            os.path.join(result_b.run_dir, "resolved.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(resolved, fh)
        with open(
            os.path.join(result_b.run_dir, "config.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(doc_a.to_obj(), fh)

        forged = RunAttestation(result_b.run_dir)
        # The naive composition ADR-0116 names is fooled — the skeptic's finding.
        assert forged.completed() is True
        assert forged.node_completed("market") is True
        assert forged.binds_document_identity(doc_a.hash) is True
        # The composed method is not: "market"'s record was stamped with B's
        # own document_hash when B actually ran, and it does not match A's —
        # so the forgery is refused for BOTH identities, exactly as it must
        # be: this run dir is neither trustworthy evidence for A (never ran)
        # nor for B (its resolved.json no longer even claims B).
        assert forged.node_output_for_document("market", doc_a.hash) is False
        assert forged.node_output_for_document("market", doc_b.hash) is False


class TestRunAttestationAttestedOutput:
    """ADR-0166: not merely THAT a node completed, but WHAT it recorded."""

    def _run(self, tmp_path):
        result = _two_artifact_run(tmp_path, {"x": 1}, {"y": 2})
        document_hash = read_json(result.run_dir, "resolved.json")["document_hash"]
        return result, document_hash, RunAttestation(result.run_dir)

    def test_returns_the_value_a_completed_bound_node_recorded(self, tmp_path):
        result, document_hash, attestation = self._run(tmp_path)
        assert attestation.attested_output("src", "a", document_hash) == (
            result.outputs["src"]["a"]
        )
        assert attestation.attested_output("src", "b", document_hash) == (
            result.outputs["src"]["b"]
        )

    def test_a_wrong_document_node_or_output_attests_nothing(self, tmp_path):
        _, document_hash, attestation = self._run(tmp_path)
        assert attestation.attested_output("src", "a", "0" * 64) is None
        assert attestation.attested_output("nope", "a", document_hash) is None
        assert attestation.attested_output("src", "nope", document_hash) is None

    def test_an_output_the_runs_own_carry_does_not_corroborate_attests_nothing(
        self, tmp_path
    ):
        result, document_hash, attestation = self._run(tmp_path)
        carry = read_json(result.run_dir, "carry.json")
        carry["src"]["a"] = {"substituted": True}
        with open(
            os.path.join(result.run_dir, "carry.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(carry, fh)
        assert attestation.attested_output("src", "a", document_hash) is None
        assert attestation.attested_output("src", "b", document_hash) == (
            result.outputs["src"]["b"]
        )

    def test_a_missing_or_malformed_carry_attests_nothing(self, tmp_path):
        result, document_hash, attestation = self._run(tmp_path)
        os.remove(os.path.join(result.run_dir, "carry.json"))
        assert attestation.attested_output("src", "a", document_hash) is None


class TwoArtifactsSource(Node):
    """Emit two independently named JsonArtifact payloads for one run."""

    role = "data"
    outputs = ("a", "b")

    def run(self, ctx, inputs):
        durable = getattr(node_module, "JsonArtifact", lambda value: value)
        return {"a": durable(self.params["a"]), "b": durable(self.params["b"])}


def _two_artifact_run(run_root, a_value, b_value, name="content-identity"):
    registry = NodeKindRegistry()
    registry.register("two-artifacts-src", TwoArtifactsSource)
    document = PipelineDocument(
        name=name,
        pipeline={
            "src": NodeSpec(
                uses="two-artifacts-src", params={"a": a_value, "b": b_value}
            )
        },
        outputs=OutputsConfig(run_root=str(run_root)),
    )
    return run_document(document, asof=ASOF, registry=registry, journal=False)


class TestContentIdentity:
    def test_is_deterministic_and_order_independent(self, tmp_path):
        result = _two_artifact_run(tmp_path, {"x": 1}, {"y": 2})
        manifests = {"a": result.outputs["src"]["a"], "b": result.outputs["src"]["b"]}
        first = content_identity(result.run_dir, manifests)
        second = content_identity(result.run_dir, dict(reversed(list(manifests.items()))))
        assert first == second
        assert isinstance(first, str) and re.fullmatch(r"[0-9a-f]{64}", first)

    def test_changed_row_content_changes_the_identity(self, tmp_path):
        first_run = _two_artifact_run(tmp_path / "one", {"x": 1}, {"y": 2}, name="doc-a")
        second_run = _two_artifact_run(
            tmp_path / "two", {"x": 999}, {"y": 2}, name="doc-b"
        )
        first = content_identity(
            first_run.run_dir,
            {"a": first_run.outputs["src"]["a"], "b": first_run.outputs["src"]["b"]},
        )
        second = content_identity(
            second_run.run_dir,
            {"a": second_run.outputs["src"]["a"], "b": second_run.outputs["src"]["b"]},
        )
        assert first != second

    def test_the_same_content_under_a_different_name_changes_the_identity(self, tmp_path):
        result = _two_artifact_run(tmp_path, {"x": 1}, {"y": 2})
        manifests = {"a": result.outputs["src"]["a"], "b": result.outputs["src"]["b"]}
        renamed = {"a_renamed": manifests["a"], "b": manifests["b"]}
        assert content_identity(result.run_dir, manifests) != content_identity(
            result.run_dir, renamed
        )

    def test_a_tampered_manifest_is_refused_not_silently_combined(self, tmp_path):
        result = _two_artifact_run(tmp_path, {"x": 1}, {"y": 2})
        manifest = result.outputs["src"]["a"]
        artifact_path = os.path.join(result.run_dir, manifest["path"])
        with open(artifact_path, "ab") as handle:
            handle.write(b" ")
        with pytest.raises(ValueError, match="byte count"):
            content_identity(
                result.run_dir, {"a": manifest, "b": result.outputs["src"]["b"]}
            )


class TestRowSetIdentity:
    """ADR-0166: one content-derived identity for a materialized row set."""

    def test_is_content_derived_and_independent_of_row_order(self):
        rows = [{"b": 2, "a": 1}, {"a": 3, "b": 4}, {"a": 5, "b": 6}]
        first = row_set_identity(rows)
        assert re.fullmatch(r"[0-9a-f]{64}", first)
        assert row_set_identity(list(reversed(rows))) == first
        assert row_set_identity([rows[1], rows[0], rows[2]]) == first

    def test_is_independent_of_key_order_inside_a_row(self):
        assert row_set_identity([{"a": 1, "b": 2}]) == row_set_identity(
            [{"b": 2, "a": 1}]
        )

    def test_changed_content_changes_the_identity(self):
        base = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        assert row_set_identity([{"a": 1, "b": 2}, {"a": 3, "b": 5}]) != (
            row_set_identity(base)
        )
        assert row_set_identity(base + [{"a": 3, "b": 4}]) != row_set_identity(base)
        assert row_set_identity([{"a": 1.0, "b": 2}, {"a": 3, "b": 4}]) != (
            row_set_identity(base)
        )

    def test_a_duplicated_row_is_a_multiset_not_a_set(self):
        one = [{"a": 1}]
        assert row_set_identity(one + one) != row_set_identity(one)

    def test_is_not_derived_from_position_filename_or_a_caller_label(self):
        rows = [{"a": 1}, {"a": 2}]
        # No name, path, index or label is an argument at all: the only
        # thing that can move the digest is the rows' own content.
        assert row_set_identity(rows) == row_set_identity(list(reversed(rows)))
        assert row_set_identity([{"a": 1, "wire": "h01"}]) != row_set_identity(
            [{"a": 1, "wire": "h02"}]
        )

    def test_refuses_a_non_list_or_an_unserializable_row(self):
        with pytest.raises(ValueError, match="must be a list"):
            row_set_identity({"a": 1})
        with pytest.raises(ValueError, match="canonically serialized"):
            row_set_identity([{"a": float("nan")}])
        with pytest.raises(ValueError, match="canonically serialized"):
            row_set_identity([{"a": object()}])
