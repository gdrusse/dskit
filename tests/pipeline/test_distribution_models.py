"""EmpiricalLocationScale: the first sample-set model rung (ADR-0168)."""

import math
import os
import random

import pytest

from dskit.pipeline.base import ConfigError, TimeSplitConfig
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.distribution_models import (
    REFERENCE_SCALE_FIELD,
    EmpiricalLocationScale,
)
from dskit.pipeline.distribution_scores import SampleDistribution
from dskit.pipeline.fitted import SIDECAR_NAME
from dskit.pipeline.node import NodeContext

DAY = 24 * 60 * 60 * 1000
PARAMS = {"fit_split": "train", "label": "y", "scale_field": "vol", "n_samples": 50}


def _rows():
    rng = random.Random(9)
    out = []
    for i in range(80):
        vol = 0.01 * (1 + (i % 4))
        out.append({"contract": f"C{i}", "asof_ms": (1 if i < 40 else 15) * DAY + i,
                    "vol": vol, "y": vol * rng.gauss(0, 1) + (0.0 if i < 40 else 5.0)})
    out.append({"contract": "late", "asof_ms": 15 * DAY + 99, "vol": 0.02, "y": None})
    out.append({"contract": "novol", "asof_ms": 15 * DAY + 98, "vol": 0.0, "y": 0.1})
    return out


@pytest.fixture
def ctx(tmp_path):
    splits = TimeSplitConfig(train_end_ms=10 * DAY, val_end_ms=20 * DAY, test_end_ms=30 * DAY)
    return NodeContext(name="m", asof="2026-01-01", run_dir=str(tmp_path),
                       splits=splits, splits_info=splits.to_obj())


def test_fits_on_train_only_and_emits_standardized_forecasts(ctx):
    out = EmpiricalLocationScale("model", dict(PARAMS)).run(ctx, {"rows": _rows()})
    rows = out["rows"]
    assert len(rows) == 82
    shape = rows[0]["samples"]
    assert len(shape) == 50 and shape == sorted(shape)
    # val rows carry a +5 shift the train split never saw: the shape must not
    assert max(shape) < 5
    assert rows[10]["outcome"] == pytest.approx(rows[10]["y"] / rows[10]["vol"])
    assert rows[10][REFERENCE_SCALE_FIELD] == rows[10]["vol"]
    late, novol = rows[-2], rows[-1]
    assert late["outcome"] is None and late["samples"] is not None
    assert novol["samples"] is None and novol[REFERENCE_SCALE_FIELD] is None


def test_scale_multiplier_rescales_outcomes_not_the_standardized_shape(ctx, tmp_path):
    base = EmpiricalLocationScale("a", dict(PARAMS)).run(ctx, {"rows": _rows()})["rows"]
    wide = EmpiricalLocationScale("b", dict(PARAMS, scale_multiplier=2.0)).run(
        ctx, {"rows": _rows()})["rows"]
    assert wide[5]["outcome"] == pytest.approx(base[5]["outcome"] / 2)
    assert wide[5]["samples"] == pytest.approx([q / 2 for q in base[5]["samples"]])


def test_relative_scale_hook_stretches_the_shape(ctx):
    class Doubled(EmpiricalLocationScale):
        def relative_scale(self, row, state):
            return 2.0

    base = EmpiricalLocationScale("a", dict(PARAMS)).run(ctx, {"rows": _rows()})["rows"]
    doubled = Doubled("b", dict(PARAMS)).run(ctx, {"rows": _rows()})["rows"]
    assert doubled[3]["samples"] == pytest.approx([2 * q for q in base[3]["samples"]])


def test_shape_is_exactly_the_fit_splits_midpoint_quantiles(ctx):
    rows = EmpiricalLocationScale("m", dict(PARAMS, n_samples=8)).run(
        ctx, {"rows": _rows()})["rows"]
    z = SampleDistribution([r["y"] / r["vol"] for r in _rows()[:40]])
    assert rows[0]["samples"] == [z.quantile((k + 0.5) / 8) for k in range(8)]


def test_load_mode_restores_the_same_forecasts(ctx, tmp_path):
    out = EmpiricalLocationScale("model", dict(PARAMS)).run(ctx, {"rows": _rows()})
    artifacts = tmp_path / "artifacts" / "model"
    assert any(artifacts.iterdir())
    loaded = EmpiricalLocationScale("model", dict(PARAMS), mode="load",
                                    artifact=str(artifacts)).run(ctx, {"rows": _rows()})
    assert loaded["rows"] == out["rows"]


@pytest.mark.parametrize("changed", [
    {"scale_multiplier": 10.0}, {"n_samples": 7}, {"label": "vol"}, {"scale_field": "y"},
])
def test_load_refuses_a_document_that_misdescribes_the_state(ctx, tmp_path, changed):
    EmpiricalLocationScale("model", dict(PARAMS)).run(ctx, {"rows": _rows()})
    node = EmpiricalLocationScale("model", dict(PARAMS, **changed), mode="load",
                                  artifact=str(tmp_path / "artifacts" / "model"))
    with pytest.raises(ValueError, match="contradicts"):
        node.run(ctx, {"rows": _rows()})


def test_too_few_usable_fit_rows_refuses(ctx):
    rows = [dict(r, y=None) for r in _rows()]
    with pytest.raises(ValueError, match="need >= 2"):
        EmpiricalLocationScale("m", dict(PARAMS)).run(ctx, {"rows": rows})


@pytest.mark.parametrize("bad", [
    {"label": None}, {"scale_field": ""}, {"scale_multiplier": 0},
    {"scale_multiplier": True}, {"n_samples": 1}, {"typo": 1}, {"fit_split": "nope"},
])
def test_invalid_params_refuse(bad):
    params = {k: v for k, v in dict(PARAMS, **bad).items() if v is not None}
    with pytest.raises(ConfigError):
        EmpiricalLocationScale("m", params)


def _conformance_probes(tmp_path):
    splits = TimeSplitConfig(train_end_ms=10 * DAY, val_end_ms=20 * DAY, test_end_ms=30 * DAY)
    fit_ctx = NodeContext(name="c", asof="2026-01-01", run_dir=str(tmp_path / "fit"),
                          splits=splits, splits_info=splits.to_obj())
    node = EmpiricalLocationScale("model", dict(PARAMS))
    carrier = node.run(fit_ctx, {"rows": _rows()})["transform"]
    return {"distribution-empirical": NodeProbe(
        params=dict(PARAMS), required=("label", "scale_field"),
        inputs={"rows": _rows()}, stream_ports=("rows",), runnable=True, ctx=fit_ctx,
        load_artifact=os.path.join(node.artifact_dir(fit_ctx), SIDECAR_NAME),
        verify_loaded=lambda out: (out["metrics"]["n_fit_rows"] == 0
                                   and out["transform"].state == carrier.state),
    )}


TestEmpiricalLocationScaleConformance = conformance_suite(
    registry=(("distribution-empirical", EmpiricalLocationScale),),
    module="dskit.pipeline.distribution_models",
    probes=_conformance_probes,
    expected_roles={"distribution-empirical": "fitted_transform"},
    name="TestEmpiricalLocationScaleConformance",
)


# -- ADR-0169: fitted-scale rungs -------------------------------------------

from dskit.pipeline.distribution_models import (  # noqa: E402
    LinearScaleLocationScale,
    ScaleModelLocationScale,
)

HAR = {"fit_split": "train", "label": "y", "scale_field": "vol", "n_samples": 50,
       "scale_target": "fwd", "scale_features": ["vol", "vol5"]}


def _scale_rows(n=200, seed=3):
    """fwd = exp(0.1) * vol^0.6 * vol5^0.3 exactly; y = fwd * N(0, 1)."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        vol, vol5 = rng.uniform(0.005, 0.03), rng.uniform(0.005, 0.03)
        fwd = math.exp(0.1) * vol ** 0.6 * vol5 ** 0.3
        out.append({"contract": f"C{i}", "asof_ms": (1 if i < n // 2 else 15) * DAY + i,
                    "vol": vol, "vol5": vol5, "fwd": fwd, "y": fwd * rng.gauss(0, 1)})
    return out


def test_linear_rung_recovers_log_har_coefficients(ctx):
    out = LinearScaleLocationScale("har", dict(HAR)).run(ctx, {"rows": _scale_rows()})
    assert out["transform"].state["model"]["coef"] == pytest.approx([0.1, 0.6, 0.3], abs=1e-9)


def test_shape_is_standardized_by_the_prediction_not_the_reference(ctx):
    rows = _scale_rows()
    out = LinearScaleLocationScale("har", dict(HAR)).run(ctx, {"rows": rows})
    z = SampleDistribution([r["y"] / r["fwd"] for r in rows[:100]])
    assert out["transform"].state["shape"] == pytest.approx(
        [z.quantile((k + 0.5) / 50) for k in range(50)])
    row = out["rows"][150]
    stretch = rows[150]["fwd"] / rows[150]["vol"]
    assert row["samples"] == pytest.approx([stretch * q for q in out["transform"].state["shape"]])


def test_a_row_without_usable_features_gets_no_forecast(ctx):
    rows = _scale_rows()
    rows[150]["vol5"] = 0.0
    out = LinearScaleLocationScale("har", dict(HAR)).run(ctx, {"rows": rows})
    assert out["rows"][150]["samples"] is None


def test_ridge_shrinks_slopes_and_is_described(ctx, tmp_path):
    plain = LinearScaleLocationScale("a", dict(HAR)).run(ctx, {"rows": _scale_rows()})
    ridge = LinearScaleLocationScale("b", dict(HAR, ridge_alpha=50.0)).run(
        ctx, {"rows": _scale_rows()})
    assert sum(abs(b) for b in ridge["transform"].state["model"]["coef"][1:]) < \
        sum(abs(b) for b in plain["transform"].state["model"]["coef"][1:])
    node = LinearScaleLocationScale("a", dict(HAR, scale_features=["vol"]), mode="load",
                                    artifact=str(tmp_path / "artifacts" / "a"))
    with pytest.raises(ValueError, match="contradicts"):
        node.run(ctx, {"rows": _scale_rows()})


def test_collinear_features_refuse(ctx):
    rows = [dict(r, vol5=r["vol"]) for r in _scale_rows()]
    with pytest.raises(ValueError, match="collinear"):
        LinearScaleLocationScale("har", dict(HAR)).run(ctx, {"rows": rows})


@pytest.mark.parametrize("bad", [
    {"scale_target": ""}, {"scale_features": []}, {"scale_features": ["a", "a"]},
    {"ridge_alpha": -1}, {"scale_target": None}, {"scale_features": None},
])
def test_scale_rung_invalid_params_refuse(bad):
    params = {k: v for k, v in dict(HAR, **bad).items() if v is not None}
    with pytest.raises(ConfigError):
        LinearScaleLocationScale("m", params)


def test_scale_model_base_is_abstract():
    with pytest.raises(TypeError):
        ScaleModelLocationScale("m", dict(HAR))


def _linear_probes(tmp_path):
    splits = TimeSplitConfig(train_end_ms=10 * DAY, val_end_ms=20 * DAY, test_end_ms=30 * DAY)
    fit_ctx = NodeContext(name="c", asof="2026-01-01", run_dir=str(tmp_path / "fit"),
                          splits=splits, splits_info=splits.to_obj())
    node = LinearScaleLocationScale("har", dict(HAR))
    carrier = node.run(fit_ctx, {"rows": _scale_rows()})["transform"]
    return {"distribution-linear-scale": NodeProbe(
        params=dict(HAR), required=("label", "scale_field", "scale_target", "scale_features"),
        inputs={"rows": _scale_rows()}, stream_ports=("rows",), runnable=True, ctx=fit_ctx,
        load_artifact=os.path.join(node.artifact_dir(fit_ctx), SIDECAR_NAME),
        verify_loaded=lambda out: (out["metrics"]["n_fit_rows"] == 0
                                   and out["transform"].state == carrier.state),
    )}


TestLinearScaleConformance = conformance_suite(
    registry=(("distribution-linear-scale", LinearScaleLocationScale),),
    module="dskit.pipeline.distribution_models",
    probes=_linear_probes,
    expected_roles={"distribution-linear-scale": "fitted_transform"},
    name="TestLinearScaleConformance",
)
