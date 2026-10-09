"""The panel zoo end to end: one registered panel, a 3-fold expanding walk-forward, four lanes.

The lanes are a mixture head (crps + tail_crps), a quantile head and the two baselines, each
scored by its own ``PanelScore`` on the SAME validation rows. Everything is synthetic and CPU:
two assets, 15 horizons, hourly bars, tiny nets, seconds. The panel is written by calling
``ArrayPanelWrite.run`` on a ``columns`` mapping (the real producer is a child feature node) and
registered through a real onboarding store, so the lanes read it the way a production document
does. Expected row sets are restated through ``PanelBands`` cut by hand, never through the nodes.
"""

import itertools
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("torch")

import dskit.onboarding.acquire as acquire_module  # noqa: E402
from dskit.onboarding import OnboardingRoot, run_acquisition  # noqa: E402
from dskit.pipeline.document import PipelineDocument  # noqa: E402
from dskit.pipeline.driver import run_walk_forward  # noqa: E402
from dskit.pipeline.libs.array_panel import DAY_MS, ArrayPanel, ArrayPanelWrite, PanelBands  # noqa: E402
from dskit.pipeline.node import NodeContext  # noqa: E402
from dskit.pipeline.runs import scan_runs  # noqa: E402

STEP = 3_600_000
HORIZONS = list(range(1, 16))
SPAN = max(HORIZONS) * STEP
GROUPS = (3, 5)
DAYS = 30
T0 = int(datetime(2023, 11, 15, tzinfo=timezone.utc).timestamp() * 1000)
VOL = "vol__ewma"
FOLDS = ["2023-12-05", "2023-12-08", "2023-12-11"]
VAL_DAYS = 3
WINDOW = 6
CAL_DAYS = 2
TAIL_WEIGHT = 0.5
TAIL = [[-12.0, -2.0], [2.0, 12.0]]
GRID = {"z_min": -12.0, "z_max": 12.0, "z_points": 241, "levels": [0.1, 0.5, 0.9],
        "tail_intervals": TAIL}
LIB = "dskit.pipeline.libs"
LANES = {"mix": f"{LIB}.panel_torch:PanelTrain", "qnt": f"{LIB}.panel_torch:PanelTrain",
         "emp": f"{LIB}.panel_score:EmpiricalBaseline", "stu": f"{LIB}.panel_score:EwmaStudentBaseline"}
MIXTURE = {"kind": "mixture", "components": 2, "family": {"kind": "student", "degrees": 5},
           "losses": [{"kind": "crps", "weight": 1.0}, {"kind": "tail_crps", "weight": TAIL_WEIGHT}],
           "tail_intervals": TAIL, "min_scale": 0.1}
QUANTILE = {"kind": "quantile", "quantiles": GRID["levels"], "bounds": [GRID["z_min"], GRID["z_max"]]}
NET = {"trunk": {"hidden": [8], "activation": "relu"}, "epochs": 3, "lr": 0.02,
       "optimizer": "torch.optim.Adam", "loader": {"batch_size": 128, "seed": 3},
       "context": {"prefixes": ["f"]},
       "sequence": {"columns": {"prefixes": ["seq__"]}, "window": WINDOW, "stride": 1}}


def make_columns(seed=0):
    """Two hourly groups; ``y_h`` is vol-standardised, so its spread grows like sqrt(h)."""
    rng = np.random.default_rng(seed)
    n = DAYS * 24
    time = np.concatenate([T0 + np.arange(n, dtype=np.int64) * STEP for _ in GROUPS])
    group = np.concatenate([np.full(n, g, dtype=np.int64) for g in GROUPS])
    total = len(time)
    cols = {"t": time, "g": group, "ref": np.ones(total), VOL: np.exp(rng.normal(0.0, 0.2, total)),
            "f0": rng.normal(size=total), "f1": rng.normal(size=total),
            "seq__ret": rng.normal(size=total), "seq__flow": rng.normal(size=total)}
    for h in HORIZONS:
        y = np.sqrt(h) * (0.4 * cols["seq__ret"] + rng.standard_t(6, total))
        for g in GROUPS:
            y[np.flatnonzero(group == g)[-h:]] = np.nan
        cols[f"y_{h}"] = y
    return cols


def ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path))


def build_store(base, columns):
    """Write ``columns`` as an array panel and register it with onboarding; returns its source spec."""
    ticks = itertools.count()
    start = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    patch = pytest.MonkeyPatch()
    patch.setattr(acquire_module, "utc_now",
                  lambda: (start + timedelta(seconds=next(ticks))).isoformat(timespec="seconds"))
    try:
        archive = base / "archive"
        archive.mkdir()
        ArrayPanelWrite("w", {"time": "t", "group": "g", "reference": "ref", "label_prefix": "y_",
                              "horizons": HORIZONS, "step_ms": STEP, "path": str(archive / "px")}
                        ).run(ctx(base), {"columns": columns})
        root = OnboardingRoot.create(str(base / "ob"))
        registry = root.registry()
        vid = registry.register("source_config", {
            "name": "blobs", "catalog_source": "x", "connector": "localblobs",
            "config": {"path": str(archive), "as_of": "2026-01-01T00:00:00+00:00"}}, origin="test")
        registry.transition(vid, "active", origin="test")
        run_acquisition(root, registry, "blobs", "files", "backfill")
        return {"root": root.root, "source": "blobs", "stream": "files", "relpath": "px"}
    finally:
        patch.undo()


def lane_nodes(store):
    """The four lanes and their score nodes as JSON-able node specs; cuts are ``$splits`` refs."""
    bounds = {"train_end_ms": "$splits.train_end_ms", "val_start_ms": "$splits.val_start_ms",
              "val_end_ms": "$splits.val_end_ms", "cal_days": CAL_DAYS, "min_run": WINDOW}
    common = {"panel": store, "bounds": bounds}
    params = {
        "mix": dict(common, assets=list(GROUPS), head=MIXTURE,
                    encoder={"arch": "gru", "width": 6, "arch_params": {"hidden_size": 6}}, **NET),
        "qnt": dict(common, assets=list(GROUPS), head=QUANTILE,
                    encoder={"arch": "tcn", "width": 6,
                 "arch_params": {"hidden_size": 6, "kernel_size": 3, "dilations": [1, 2]}}, **NET),
        "emp": dict(common, grid=GRID),
        "stu": dict(common, grid=GRID, vol_column=VOL, degrees=[3, 5, 8]),
    }
    nodes = {key: {"uses": uses, "params": params[key]} for key, uses in LANES.items()}
    for key in LANES:
        nodes[f"score_{key}"] = {
            "uses": f"{LIB}.panel_score:PanelScore", "inputs": {"model": f"${key}.model"},
            "params": dict(common, grid=GRID, tail_weight=TAIL_WEIGHT, split="val")}
    return nodes


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    return build_store(tmp_path_factory.mktemp("zoo"), make_columns())


@pytest.fixture(scope="module")
def result(store, tmp_path_factory):
    doc = PipelineDocument.from_obj({
        "name": "panelzoo", "pipeline": lane_nodes(store),
        "outputs": {"run_root": str(tmp_path_factory.mktemp("runs"))},
        "walkforward": {"objective": "$score_mix.metrics.weighted_crps", "val_days": VAL_DAYS,
                        "embargo_days": 1, "folds": FOLDS}})
    return run_walk_forward(doc, asof="2026-01-01")


def _artifact(run_dir, key, name):
    with open(os.path.join(run_dir, "artifacts", key, name), encoding="utf-8") as fh:
        return json.load(fh)


def test_every_lane_scores_every_fold_on_the_same_rows(store, result):
    assert result.state == "ran" and len(result.folds) == len(FOLDS)
    panel = ArrayPanel.from_source(store)
    for fold in result.folds:
        run_dir = fold["run_dir"]
        with open(os.path.join(run_dir, "resolved.json"), encoding="utf-8") as fh:
            cuts = json.load(fh)["splits"]
        bands = PanelBands(panel, list(GROUPS), cuts["train_end_ms"], cuts["val_start_ms"],
                           cuts["val_end_ms"], CAL_DAYS, WINDOW)
        scored = {key: _artifact(run_dir, f"score_{key}", "horizon_scores.json") for key in LANES}
        assert {art["rows_sha256"] for art in scored.values()} == {bands.rows_sha256()}
        for key, art in scored.items():
            crps = np.asarray(art["overall"]["crps"], dtype=float)
            assert crps.shape == (len(HORIZONS),) and np.isfinite(crps).all(), key
            assert art["horizons"] == HORIZONS and art["tail_weight"] == TAIL_WEIGHT
        assert scored["mix"]["cdf_basis"] == "parametric"
        assert scored["qnt"]["cdf_basis"] == "extrapolated"


def test_no_fit_label_reaches_the_calibration_or_validation_window(store, result):
    panel = ArrayPanel.from_source(store)
    n_fit = []
    for fold in result.folds:
        with open(os.path.join(fold["run_dir"], "resolved.json"), encoding="utf-8") as fh:
            cuts = json.load(fh)["splits"]
        bands = PanelBands(panel, list(GROUPS), cuts["train_end_ms"], cuts["val_start_ms"],
                           cuts["val_end_ms"], CAL_DAYS, WINDOW)
        assert (panel.time[bands.fit_idx] + SPAN < bands.cal_start_ms).all()
        assert (panel.time[bands.fit_idx] + SPAN < cuts["val_start_ms"]).all()
        assert (panel.time[bands.cal_idx] + SPAN < cuts["val_start_ms"]).all()
        assert cuts["val_start_ms"] - cuts["train_end_ms"] == DAY_MS + 1     # embargo_days: 1
        n_fit.append(len(bands.fit_idx))
    assert n_fit == sorted(set(n_fit))                                        # expanding: strictly more


def test_the_objective_is_finite_per_fold_and_aggregated(result):
    assert all(fold["score"] is not None and np.isfinite(fold["score"]) for fold in result.folds)
    assert result.aggregate["n_scored"] == len(FOLDS)


def test_a_trained_lane_leaves_a_loadable_artifact_and_the_baselines_a_state(result):
    for fold in result.folds:
        for key in ("mix", "qnt"):
            assert os.path.isfile(os.path.join(fold["run_dir"], "artifacts", key, "training_curve.json"))
        for key in ("emp", "stu"):
            assert _artifact(fold["run_dir"], key, "state.json")["rows_sha256"]


def test_runs_tabulates_every_lane_score_per_fold(result):
    runs, _ = scan_runs(os.path.dirname(result.folds[0]["run_dir"]))
    assert len(runs) == len(FOLDS)
    for run in runs:
        for key in LANES:
            assert np.isfinite(run.metrics[f"score_{key}.metrics.weighted_crps"])
