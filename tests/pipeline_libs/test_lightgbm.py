"""LightGBM volatility-scale rung (ADR-0169)."""

import os
import random

import pytest

pytest.importorskip("lightgbm")

from dskit.pipeline.base import ConfigError, TimeSplitConfig
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.fitted import SIDECAR_NAME
from dskit.pipeline.libs.lightgbm import (
    FORCED_LGBM_KEYS,
    NODE_KINDS,
    LightGBMScaleLocationScale,
)
from dskit.pipeline.node import NodeContext

DAY = 24 * 60 * 60 * 1000
PARAMS = {"fit_split": "train", "label": "y", "scale_field": "vol", "n_samples": 40,
          "scale_target": "fwd", "scale_features": ["vol", "vol5"],
          "lgbm_params": {"n_estimators": 60, "min_child_samples": 10}}


def _rows(n=300, seed=5):
    """A nonlinear scale: fwd jumps when vol5 is high."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        vol, vol5 = rng.uniform(0.005, 0.03), rng.uniform(0.005, 0.03)
        fwd = vol * (2.0 if vol5 > 0.02 else 1.0)
        out.append({"contract": f"C{i}", "asof_ms": (1 if i < n // 2 else 15) * DAY + i,
                    "vol": vol, "vol5": vol5, "fwd": fwd, "y": fwd * rng.gauss(0, 1)})
    return out


@pytest.fixture
def ctx(tmp_path):
    splits = TimeSplitConfig(train_end_ms=10 * DAY, val_end_ms=20 * DAY, test_end_ms=30 * DAY)
    return NodeContext(name="g", asof="2026-01-01", run_dir=str(tmp_path),
                       splits=splits, splits_info=splits.to_obj())


def test_booster_learns_the_nonlinear_scale(ctx):
    rows = _rows()
    out = LightGBMScaleLocationScale("gbm", dict(PARAMS)).run(ctx, {"rows": rows})
    node = LightGBMScaleLocationScale("gbm", dict(PARAMS))
    model = out["transform"].state["model"]
    errors = [abs(node.predicted_vol(r, model) / r["fwd"] - 1) for r in rows[150:]]
    assert sum(errors) / len(errors) < 0.15
    assert out["rows"][200]["samples"] is not None


def test_fit_is_deterministic_and_seed_is_described(ctx, tmp_path):
    a = LightGBMScaleLocationScale("a", dict(PARAMS)).run(ctx, {"rows": _rows()})
    b = LightGBMScaleLocationScale("b", dict(PARAMS)).run(ctx, {"rows": _rows()})
    assert a["transform"].state["model"] == b["transform"].state["model"]
    node = LightGBMScaleLocationScale("a", dict(PARAMS, seed=1), mode="load",
                                      artifact=str(tmp_path / "artifacts" / "a"))
    with pytest.raises(ValueError, match="contradicts"):
        node.run(ctx, {"rows": _rows()})


def test_load_restores_identical_forecasts(ctx, tmp_path):
    fitted = LightGBMScaleLocationScale("gbm", dict(PARAMS)).run(ctx, {"rows": _rows()})
    loaded = LightGBMScaleLocationScale("gbm", dict(PARAMS), mode="load",
                                        artifact=str(tmp_path / "artifacts" / "gbm")).run(
        ctx, {"rows": _rows()})
    assert loaded["rows"] == fitted["rows"]


@pytest.mark.parametrize("bad", [
    *({"lgbm_params": {k: 1}} for k in FORCED_LGBM_KEYS),
    {"lgbm_params": {"x": [1]}}, {"lgbm_params": []}, {"seed": -1}, {"typo": 1},
])
def test_invalid_params_refuse(bad):
    with pytest.raises(ConfigError):
        LightGBMScaleLocationScale("g", dict(PARAMS, **bad))


def test_pack_registers_nothing_and_names_lightgbm_only_in_methods():
    import dskit.pipeline.libs.lightgbm as pack

    assert NODE_KINDS == ()
    source = open(pack.__file__).read()
    assert "\nimport lightgbm" not in source and "\nfrom lightgbm" not in source


def _probes(tmp_path):
    splits = TimeSplitConfig(train_end_ms=10 * DAY, val_end_ms=20 * DAY, test_end_ms=30 * DAY)
    fit_ctx = NodeContext(name="c", asof="2026-01-01", run_dir=str(tmp_path / "fit"),
                          splits=splits, splits_info=splits.to_obj())
    node = LightGBMScaleLocationScale("gbm", dict(PARAMS))
    carrier = node.run(fit_ctx, {"rows": _rows()})["transform"]
    return {"lightgbm-scale": NodeProbe(
        params=dict(PARAMS), required=("label", "scale_field", "scale_target", "scale_features"),
        inputs={"rows": _rows()}, stream_ports=("rows",), runnable=True, ctx=fit_ctx,
        load_artifact=os.path.join(node.artifact_dir(fit_ctx), SIDECAR_NAME),
        verify_loaded=lambda out: (out["metrics"]["n_fit_rows"] == 0
                                   and out["transform"].state == carrier.state),
    )}


TestLightGBMScaleConformance = conformance_suite(
    registry=(("lightgbm-scale", LightGBMScaleLocationScale),),
    module="dskit.pipeline.libs.lightgbm",
    probes=_probes,
    expected_roles={"lightgbm-scale": "fitted_transform"},
    name="TestLightGBMScaleConformance",
)
