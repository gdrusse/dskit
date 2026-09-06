"""The time-series architecture zoo (ADR-0041).

``torch.py`` is pinned by content hash: this file is the CATALOG, and the
identity pin below refuses an accidental edit of the engine pack. ADR-0045
(batched eval) intentionally moved the hash; recompute on a deliberate
engine-pack change. Every net is built INSIDE ``build_module`` — the
purity gate scans class bodies too.
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.libs.torch import TorchPredict, TorchTrain
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.node import Node, NodeContext
from dskit.pipeline.planner import plan

torch = pytest.importorskip("torch")

from dskit.pipeline.libs.torch_ts import (  # noqa: E402
    ARCHS,
    CategoricalEmbeddingMLPRegressor,
    CategoricalRecurrentFusionRegressor,
    CategoricalTemporalFusionRegressor,
    NODE_KINDS,
    TimeSeriesPredict,
    TimeSeriesTrain,
    register,
    register_arch,
)

TORCH_PY = (
    pathlib.Path(__file__).parents[2] / "dskit" / "pipeline" / "libs" / "torch.py"
)
#: Content pin of ``libs/torch.py``. Recompute on a deliberate engine-pack
#: change (ADR-0045 moved it for batched eval; ADR-0091 added the audited
#: serving-load declaration); accidental edits fail here.
TORCH_PY_SHA256 = (
    "3cdfc7a089f1a3009a0dca15a791aecc3bc7eb50484456ad85f20ea45757e556"
)

SHIPPED = (
    "dlinear", "nlinear", "mlp", "lstm", "gru",
    "lstm_attn", "gru_attn", "tcn", "cnn1d", "patchtst", "transformer", "tft",
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
    for missing in ("arch", "head", "seq_len", "channels"):
        params = {k: v for k, v in TRAIN_PARAMS.items() if k != missing}
        assert any(missing in p for p in TimeSeriesTrain.validate_params(params)), missing
        nulled = {**TRAIN_PARAMS, missing: None}
        assert any(missing in p for p in TimeSeriesTrain.validate_params(nulled)), missing
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


def test_arch_names_use_underscores_and_the_shipped_set():
    assert set(ARCHS) >= set(SHIPPED)
    assert all("_" not in name or name.replace("_", "").isalpha() for name in SHIPPED)
    assert "-" not in "".join(SHIPPED)


def test_patience_requires_monitor():
    problems = TimeSeriesTrain.validate_params({**TRAIN_PARAMS, "patience": 2})
    assert any("patience requires monitor" in p for p in problems)
    assert TimeSeriesTrain.validate_params(
        {**TRAIN_PARAMS, "patience": 2, "monitor": "val_loss"}
    ) == []


def test_an_unknown_arch_or_head_is_refused_naming_the_vocabulary():
    assert any("lstm" in p for p in TimeSeriesTrain.validate_params(
        {**TRAIN_PARAMS, "arch": "nbeats"}
    ))
    assert any("regression" in p for p in TimeSeriesTrain.validate_params(
        {**TRAIN_PARAMS, "head": "multiclass"}
    ))


def test_an_unhashable_arch_or_head_returns_a_problem():
    """JSON can put a mapping where a name belongs; validate must not explode."""
    for knob, junk in (("arch", {}), ("head", []), ("order", {})):
        problems = TimeSeriesTrain.validate_params({**TRAIN_PARAMS, knob: junk})
        assert problems and all(isinstance(p, str) for p in problems), knob
        assert any(knob in p for p in problems), (knob, problems)


def test_every_shipped_arch_constructs_and_maps_B_seq_ch_to_B_1():
    batch = torch.zeros(3, SEQ * CH)
    for name in SHIPPED:
        node = TimeSeriesTrain("m", {**TRAIN_PARAMS, "arch": name})
        module = node.build_module(node.params)
        out = module(batch)
        assert tuple(out.shape) == (3, 1), name


def test_n_ahead_maps_to_B_H():
    batch = torch.zeros(3, SEQ * CH)
    params = {**TRAIN_PARAMS, "arch": "lstm", "n_ahead": 4}
    module = TimeSeriesTrain("m", params).build_module(params)
    assert tuple(module(batch).shape) == (3, 4)


def test_transformer_ships_as_a_one_layer_encoder():
    assert "transformer" in ARCHS
    assert TimeSeriesTrain.validate_params(
        {**TRAIN_PARAMS, "arch": "transformer"}
    ) == []


def test_tcn_is_dilated_so_the_oldest_lag_can_fire():
    """ADR-0041 ships dilated causal conv, not a 3-tap last-step filter."""
    torch.manual_seed(0)
    module = TimeSeriesTrain("m", {**TRAIN_PARAMS, "arch": "tcn"}).build_module(
        {**TRAIN_PARAMS, "arch": "tcn"}
    )
    base = torch.zeros(1, SEQ * CH)
    shifted = base.clone()
    shifted[0, SEQ - 1] = 1.0  # oldest lag under recent_first
    assert not torch.equal(module(base), module(shifted))


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


# ---------------------------------------------------------------------------
# Conformance (pipeline CLAUDE.md — probes are not optional)
# ---------------------------------------------------------------------------

PROBE_ROW = ts_rows()[0]


def _inverted_rows():
    rows = ts_rows()
    for row in rows:
        row["y"] = 1.0 - row["y"]
    return rows


def probes(tmp_path):
    """Fixture fit on ``ts_rows``; probe inputs invert ``y`` so a silent
    refit cannot impersonate a restore.
    """
    fixture = TimeSeriesTrain("fixture", dict(TRAIN_PARAMS)).run(
        NodeContext(
            name="fixture", asof="2026-01-01",
            run_dir=str(tmp_path / "fixture-run"),
        ),
        {"rows": ts_rows()},
    )
    artifact = fixture["artifact_path"]
    expected = fixture["signal"].predict(PROBE_ROW)

    def restored(out):
        signal = out["signal"]
        prediction = signal.predict(PROBE_ROW)
        return (
            bool(getattr(signal, "loaded", False))
            and signal.artifact_path == artifact
            and prediction is not None
            and abs(prediction - expected) < 1e-9
        )

    return {
        "torch-ts-train": NodeProbe(
            params=dict(TRAIN_PARAMS),
            required=("arch", "head", "seq_len", "channels", "features"),
            inputs={"rows": _inverted_rows()},
            stream_ports=("rows",),
            runnable=True,
            load_artifact=artifact,
            verify_loaded=lambda out: (
                restored(out) and out.get("artifact_path") == artifact
            ),
        ),
        "torch-ts-predict": NodeProbe(
            params={},
            inputs={"artifact_path": artifact},
            stream_ports=(),
            runnable=True,
            load_artifact=artifact,
            verify_loaded=restored,
        ),
    }


TestTorchTsConformance = conformance_suite(
    registry=NODE_KINDS,
    module="dskit.pipeline.libs.torch_ts",
    probes=probes,
    expected_roles={
        "torch-ts-train": "train",
        "torch-ts-predict": "signal",
    },
    name="TestTorchTsConformance",
)


def test_zoo_estimator_splits_the_row_by_name(tmp_path):
    """The lag columns are the time axis; everything else is context."""
    pytest.importorskip("torch")
    import numpy as np

    from dskit.pipeline.libs.torch_ts import ZooEstimator

    model = ZooEstimator(arch="gru", epochs=1, batch_size=64, seed=0)
    names = ["ret_lag_0", "vol", "ret_lag_1", "ret_lag_2", "tod"]
    sequence, static = model._split(len(names), names)
    assert [names[i] for i in sequence] == ["ret_lag_0", "ret_lag_1", "ret_lag_2"]
    assert [names[i] for i in static] == ["vol", "tod"]
    # No names: the whole row is one channel, honestly declared.
    assert model._split(5, None) == ([0, 1, 2, 3, 4], [])
    with pytest.raises(ValueError, match="no column starts with"):
        model._split(2, ["vol", "tod"])
    with pytest.raises(ValueError, match="not contiguous"):
        model._split(2, ["ret_lag_0", "ret_lag_2"])
    del np, tmp_path


def test_zoo_estimator_learns_a_path_signal_and_refuses_bad_knobs():
    pytest.importorskip("torch")
    import numpy as np

    from dskit.pipeline.libs.torch_ts import ZooEstimator

    rng = np.random.default_rng(0)
    n, lags = 2000, 6
    seq = rng.normal(0.0, 1.0, (n, lags))
    static = rng.normal(0.0, 1.0, (n, 1))
    y = 0.8 * seq[:, 0] - 0.5 * seq[:, 2] + 0.3 * static[:, 0]
    x = np.column_stack([seq, static])
    names = [f"ret_lag_{i}" for i in range(lags)] + ["vol"]
    model = ZooEstimator(
        arch="gru", epochs=25, lr=3e-3, batch_size=256, seed=0,
    ).fit(x[:1500], y[:1500], feature_names=names)
    hat = model.predict(x[1500:])
    assert hat.shape == (500,)
    assert np.corrcoef(hat, y[1500:])[0, 1] > 0.5, (
        "a GRU that cannot find a linear function of the path it was given "
        "is not wired to the path"
    )
    with pytest.raises(ValueError, match="arch must be one of"):
        ZooEstimator(arch="not-an-arch")
    with pytest.raises(ValueError, match="unknown knob"):
        ZooEstimator(arch="gru", nonsense=1)
    with pytest.raises(RuntimeError, match="predict before fit"):
        ZooEstimator(arch="gru").predict(x[:1])


def test_zoo_estimator_averages_a_declared_seed_set():
    """ADR-0072: ``seeds`` fits one member each and averages them.

    The default stays a single fit, and that fit must be bit-identical
    to member ``seed`` of an averaged run — otherwise the knob would
    silently move every number recorded before it existed.
    """
    pytest.importorskip("torch")
    import numpy as np

    from dskit.pipeline.libs.torch_ts import ZooEstimator

    rng = np.random.default_rng(3)
    x = rng.normal(0.0, 1.0, (200, 5))
    y = rng.normal(0.0, 1.0, 200)
    names = [f"ret_lag_{i}" for i in range(4)] + ["vol"]
    knobs = dict(arch="gru", epochs=2, batch_size=64, device="cpu")

    single = ZooEstimator(seed=0, **knobs).fit(x, y, feature_names=names)
    averaged = ZooEstimator(seeds=[0, 1, 2], **knobs).fit(
        x, y, feature_names=names,
    )
    assert len(single._modules) == 1 and len(averaged._modules) == 3
    hat_single = single.predict(x)
    hat_avg = averaged.predict(x)
    assert hat_single.shape == hat_avg.shape == (200,)
    assert np.isfinite(hat_avg).all()

    # Member 0 of the averaged fit IS the old single-seed fit.
    seq, static = averaged._parts(np.asarray(x, dtype=np.float64))
    member0 = averaged._predict_one(
        averaged._modules[0], seq, static, averaged._resolved_device(),
    )
    assert np.allclose(hat_single, member0)
    # And the average is the mean of its members, not member 0 again.
    members = [
        averaged._predict_one(
            module, seq, static, averaged._resolved_device(),
        )
        for module in averaged._modules
    ]
    assert np.allclose(hat_avg, np.mean(members, axis=0))
    assert not np.allclose(hat_avg, member0)
    # A seed set narrows the spread it averages over.
    spread = np.std(members, axis=0).mean()
    assert spread > 0.0


def test_zoo_estimator_refuses_a_seed_set_that_is_not_whole_numbers():
    """An empty or mistyped ``seeds`` is a typo, not an ensemble."""
    pytest.importorskip("torch")

    from dskit.pipeline.libs.torch_ts import ZooEstimator

    with pytest.raises(ValueError, match="non-empty list of whole"):
        ZooEstimator(arch="gru", seeds=[])
    with pytest.raises(ValueError, match="non-empty list of whole"):
        ZooEstimator(arch="gru", seeds=5)
    with pytest.raises(ValueError, match="whole numbers"):
        ZooEstimator(arch="gru", seeds=[0, "1"])
    with pytest.raises(ValueError, match="whole numbers"):
        ZooEstimator(arch="gru", seeds=[0, 1.5])
    with pytest.raises(ValueError, match="whole numbers"):
        ZooEstimator(arch="gru", seeds=[True, 1])


def test_zoo_estimator_serves_every_named_arch():
    """Each arch the owner asked for fits and predicts through the façade."""
    pytest.importorskip("torch")
    import numpy as np

    from dskit.pipeline.libs.torch_ts import ZooEstimator

    rng = np.random.default_rng(1)
    x = rng.normal(0.0, 1.0, (300, 5))
    y = rng.normal(0.0, 1.0, 300)
    names = [f"ret_lag_{i}" for i in range(4)] + ["vol"]
    for arch in ("gru", "lstm", "tft"):
        hat = ZooEstimator(
            arch=arch, epochs=1, batch_size=128, seed=0,
        ).fit(x, y, feature_names=names).predict(x)
        assert hat.shape == (300,) and np.isfinite(hat).all(), arch


def test_categorical_embedding_mlp_fits_one_declared_category_column():
    import numpy as np

    x = np.asarray(
        [
            [0.0, 10.0, 0.0],
            [1.0, 9.0, 0.0],
            [0.0, 8.0, 1.0],
            [1.0, 7.0, 1.0],
        ]
    )
    model = CategoricalEmbeddingMLPRegressor(
        hidden_size=4,
        hidden_depth=3,
        embedding_dim=2,
        epochs=1,
        batch_size=2,
        dropout=0.0,
        seed=0,
        device="cpu",
    )
    model.fit(
        x,
        np.asarray([0.0, 1.0, 1.0, 2.0]),
        categorical_feature=[2],
        feature_names=["x", "z", "symbol_code"],
    )
    prediction = model.predict(x)
    assert prediction.shape == (4,)
    assert np.all(np.isfinite(prediction))
    linear = [
        layer
        for layer in model._module.net
        if layer.__class__.__name__ == "Linear"
    ]
    assert len(linear) == 4
    with pytest.raises(ValueError, match="unseen category"):
        model.predict(np.asarray([[0.0, 1.0, 2.0]]))


def test_categorical_embedding_mlp_does_not_promote_pooled_input_to_float64():
    import numpy as np

    class Float32Only:
        def __init__(self, values):
            self.values = np.asarray(values, dtype=np.float32)

        def __array__(self, dtype=None, copy=None):
            if dtype is not None and np.dtype(dtype) == np.dtype(np.float64):
                raise AssertionError("pooled feature matrix was promoted to float64")
            return np.array(self.values, dtype=dtype, copy=copy)

    x = Float32Only([[0.0, 1.0, 0.0], [1.0, 0.0, 1.0]])
    model = CategoricalEmbeddingMLPRegressor(
        hidden_size=2,
        embedding_dim=2,
        epochs=1,
        batch_size=2,
        seed=0,
        device="cpu",
    )
    model.fit(
        x,
        np.asarray([0.0, 1.0], dtype=np.float32),
        categorical_feature=[2],
    )
    assert model._center.dtype == np.float32
    assert model._scale.dtype == np.float32


def test_categorical_embedding_mlp_chunked_standardizer_matches_numpy():
    import numpy as np

    values = np.random.default_rng(4).normal(size=(19, 5)).astype(np.float32)
    center, scale = CategoricalEmbeddingMLPRegressor._standardizer(
        values, block_rows=3
    )
    np.testing.assert_allclose(center, values.mean(axis=0), rtol=1e-6)
    np.testing.assert_allclose(scale, values.std(axis=0), rtol=1e-6)


@pytest.mark.parametrize("arch", ["lstm", "gru"])
def test_categorical_recurrent_fusion_keeps_static_features_outside_time(arch):
    import numpy as np

    names = [
        f"ohlcv_t{step:03d}_{field}"
        for step in range(3)
        for field in ("open", "high", "low", "close", "volume")
    ] + ["tod_sin", "ref_ret_SPY", "symbol_code"]
    rows = []
    for index in range(8):
        path = []
        for step in range(3):
            price = 100.0 + index + step
            path.extend([price, price + 1.0, price - 1.0, price + 0.5, 10 + step])
        rows.append(path + [index / 8.0, -index / 100.0, index % 2])
    x = np.asarray(rows, dtype=np.float32)
    model = CategoricalRecurrentFusionRegressor(
        arch=arch,
        context_length=2,
        hidden_size=4,
        num_layers=1,
        static_projection_dim=3,
        embedding_dim=2,
        epochs=1,
        batch_size=4,
        dropout=0.0,
        device="cpu",
    ).fit(
        x,
        np.linspace(-0.1, 0.1, len(x)),
        categorical_feature=[len(names) - 1],
        feature_names=names,
    )
    prediction = model.predict(x)
    assert prediction.shape == (len(x),)
    assert np.isfinite(prediction).all()
    assert model._module.recurrent.input_size == 5
    assert model._module.static[0].in_features == 2
    assert model._module.head.in_features == 4 + 3 + 2


@pytest.mark.parametrize("arch", ["tcn", "transformer"])
def test_categorical_temporal_fusion_keeps_static_features_outside_time(arch):
    import numpy as np

    names = [
        f"ohlcv_t{step:03d}_{field}"
        for step in range(4)
        for field in ("open", "high", "low", "close", "volume")
    ] + ["tod_sin", "ref_ret_SPY", "symbol_code"]
    rows = []
    for index in range(8):
        path = []
        for step in range(4):
            price = 100.0 + index + step
            path.extend([price, price + 1.0, price - 1.0, price + 0.5, 10 + step])
        rows.append(path + [index / 8.0, -index / 100.0, index % 2])
    x = np.asarray(rows, dtype=np.float32)
    model = CategoricalTemporalFusionRegressor(
        arch=arch,
        context_length=3,
        hidden_size=4,
        num_layers=1,
        static_projection_dim=3,
        embedding_dim=2,
        epochs=1,
        batch_size=4,
        dropout=0.0,
        nhead=2,
        device="cpu",
    ).fit(
        x,
        np.linspace(-0.1, 0.1, len(x)),
        categorical_feature=[len(names) - 1],
        feature_names=names,
    )
    prediction = model.predict(x)
    assert prediction.shape == (len(x),)
    assert np.isfinite(prediction).all()
    assert model._module.static[0].in_features == 2
    assert model._module.head.in_features == 4 + 3 + 2
