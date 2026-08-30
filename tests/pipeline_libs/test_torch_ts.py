"""The time-series architecture zoo (ADR-0041).

``torch.py`` stays byte-identical: this file is the CATALOG, and the
identity pin below is how a later edit of the engine pack is refused.
Every net is built INSIDE ``build_module`` — the purity gate scans class
bodies too.
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.libs.torch import TorchPredict, TorchTrain
from dskit.pipeline.node import Node
from dskit.pipeline.planner import plan

torch = pytest.importorskip("torch")

from dskit.pipeline.libs.torch_ts import (  # noqa: E402
    ARCHS,
    NODE_KINDS,
    TimeSeriesPredict,
    TimeSeriesTrain,
    register,
    register_arch,
)

TORCH_PY = (
    pathlib.Path(__file__).parents[2] / "dskit" / "pipeline" / "libs" / "torch.py"
)
#: C6 drained this file. C5 must not touch it (ADR-0041).
TORCH_PY_SHA256 = (
    "0c908fa8549db2fcd83e2c23f0ae660e9f05a3ed487262b6b64c02ff540addc8"
)

SHIPPED = (
    "dlinear", "nlinear", "mlp", "lstm", "gru",
    "lstm_attn", "gru_attn", "tcn", "cnn1d", "patchtst",
)

SEQ, CH = 4, 1
FEATURES = [f"lag_{i}" for i in range(SEQ * CH)]
TRAIN_PARAMS = {
    "arch": "dlinear",
    "head": "regression",
    "seq_len": SEQ,
    "channels": CH,
    "features": FEATURES,
    "label": "y",
    "epochs": 2,
    "lr": 0.05,
    "loader": {"batch_size": 8, "shuffle": False, "seed": 3},
}


def ts_rows(n=16):
    rows = []
    for i in range(n):
        row = {name: ((i + k) % 7) / 7.0 for k, name in enumerate(FEATURES)}
        row["y"] = 0.2 * row["lag_0"] - 0.1 * row["lag_3"]
        rows.append(row)
    return rows


def test_torch_py_is_byte_identical_to_the_c6_drain():
    digest = hashlib.sha256(TORCH_PY.read_bytes()).hexdigest()
    assert digest == TORCH_PY_SHA256


def test_the_pair_shares_one_build_module_and_sits_on_the_torch_bases():
    assert issubclass(TimeSeriesTrain, TorchTrain)
    assert issubclass(TimeSeriesPredict, TorchPredict)
    assert TimeSeriesTrain.build_module is TimeSeriesPredict.build_module
    assert dict(NODE_KINDS) == {
        "torch-ts-train": TimeSeriesTrain,
        "torch-ts-predict": TimeSeriesPredict,
    }


def test_arch_head_seq_len_are_required_on_the_trainer_only():
    for missing in ("arch", "head", "seq_len"):
        params = {k: v for k, v in TRAIN_PARAMS.items() if k != missing}
        assert any(missing in p for p in TimeSeriesTrain.validate_params(params)), missing
    # Predictor may omit them — the module comes from the sidecar.
    assert TimeSeriesPredict.validate_params({"artifact": "x.pt"}) == []


def test_len_features_must_equal_seq_len_times_channels():
    params = {**TRAIN_PARAMS, "features": FEATURES[:-1]}
    problems = TimeSeriesTrain.validate_params(params)
    assert any("seq_len" in p and "channels" in p for p in problems)


def test_a_none_seq_len_returns_a_problem_and_does_not_explode():
    params = {**TRAIN_PARAMS, "seq_len": None}
    problems = TimeSeriesTrain.validate_params(params)
    assert problems and all(isinstance(p, str) for p in problems)


def test_arch_names_use_underscores_and_the_ten_ship():
    assert set(ARCHS) >= set(SHIPPED)
    assert all("_" not in name or name.replace("_", "").isalpha() for name in SHIPPED)
    assert "-" not in "".join(SHIPPED)


def test_an_unknown_arch_or_head_is_refused_naming_the_vocabulary():
    assert any("lstm" in p for p in TimeSeriesTrain.validate_params(
        {**TRAIN_PARAMS, "arch": "nbeats"}
    ))
    assert any("regression" in p for p in TimeSeriesTrain.validate_params(
        {**TRAIN_PARAMS, "head": "multiclass"}
    ))


def test_every_shipped_arch_constructs_and_maps_B_seq_ch_to_B_1():
    batch = torch.zeros(3, SEQ * CH)
    for name in SHIPPED:
        node = TimeSeriesTrain("m", {**TRAIN_PARAMS, "arch": name})
        module = node.build_module(node.params)
        out = module(batch)
        assert tuple(out.shape) == (3, 1), name


def test_register_arch_requires_problems_and_defaults():
    with pytest.raises(TypeError):
        register_arch("toy", lambda p, s, c: None)


class _TsMarket(Node):
    """Toy lag-rows + outcomes so an ``arch`` sweep can actually run."""

    role = "data"
    outputs = ("records", "outcomes")
    _PARAMS = ()

    def run(self, ctx, inputs):
        rows, outcomes = [], {}
        for i, row in enumerate(ts_rows(24)):
            cid = f"c{i}"
            rows.append({
                **row,
                "asof_ms": 86_400_000 * (i + 1),
                "contract": cid,
                "cluster": 1,
                "instrument": "X",
            })
            outcomes[cid] = True
        return {"records": rows, "outcomes": outcomes}


def test_a_typo_inside_arch_params_is_refused_at_plan():
    """Default-deny inside the block (I-227) — a mistyped width is not 32."""
    problems = TimeSeriesTrain.validate_params({
        **TRAIN_PARAMS, "arch": "lstm",
        "arch_params": {"lstm": {"hidde_size": 64}},
    })
    assert any("hidde_size" in p and "unknown" in p for p in problems)
    problems = TimeSeriesTrain.validate_params({
        **TRAIN_PARAMS, "arch": "lstm",
        "arch_params": {"ltsm": {"hidden_size": 64}},
    })
    assert any("ltsm" in p and "registered" in p for p in problems)


def test_builders_read_defaults_from_the_registry_only():
    """Rebind ``defaults`` and both plan-check and build follow (one name)."""
    entry = ARCHS["lstm"]
    original = entry["defaults"]
    entry["defaults"] = {**original, "hidden_size": 7}
    try:
        problems = TimeSeriesTrain.validate_params({
            **TRAIN_PARAMS, "arch": "lstm",
        })
        assert problems == []
        module = TimeSeriesTrain("m", {**TRAIN_PARAMS, "arch": "lstm"}).build_module(
            {**TRAIN_PARAMS, "arch": "lstm"}
        )
        assert module.inner.rnn.hidden_size == 7
    finally:
        entry["defaults"] = original


def test_a_space_over_arch_plans_and_picks_a_winner(tmp_path):
    """ADR-0041 / C5: ``arch`` is a swept param and the sweep RUNS."""
    from dskit.pipeline.node import NodeContext

    node = TimeSeriesTrain("model", dict(TRAIN_PARAMS))
    out = node.run(
        NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / "one")),
        {"rows": ts_rows()},
    )
    assert "signal" in out and out["metrics"]["epochs"] == 2

    doc = PipelineDocument.from_obj({
        "name": "ts-arch-sweep",
        "pipeline": {
            "data": {
                "uses": "tests.pipeline_libs.test_torch_ts:_TsMarket",
            },
            "model": {
                "uses": "dskit.pipeline.libs.torch_ts:TimeSeriesTrain",
                "inputs": {"rows": "$data.records"},
                "params": dict(TRAIN_PARAMS),
            },
            "validate": {
                "uses": "validate",
                "inputs": {
                    "records": "$data.records",
                    "signal": "$model.signal",
                    "outcomes": "$data.outcomes",
                },
                "params": {"split": "val", "metric": "squared_error",
                           "min_events": 1},
            },
            "sweep": {
                "uses": "hpo-grid",
                "params": {
                    "space": {"model.arch": ["dlinear", "mlp"]},
                    "objective": "$validate.metrics.loss",
                    "select": "min",
                },
            },
        },
        "splits": {
            "kind": "time",
            "train_end_ms": 86_400_000 * 16,
            "val_end_ms": 86_400_000 * 24,
            "test_end_ms": 86_400_000 * 30,
        },
        "outputs": {"run_root": str(tmp_path / "runs")},
    })
    the_plan = plan(doc)
    assert the_plan.role_of("model") == "train"
    assert the_plan.role_of("sweep") == "search"
    result = run_document(doc, asof="2026-01-01")
    assert result.state == "ran", (result.state, result.error)
    sweep = result.outputs["sweep"]
    assert len(sweep["trials"]) == 2
    assert sweep["best_params"]["model.arch"] in ("dlinear", "mlp")


def test_register_is_explicit():
    from dskit.pipeline.node import NodeKindRegistry
    registry = NodeKindRegistry()
    register(registry)
    assert "torch-ts-train" in registry
    register(registry)  # idempotent
