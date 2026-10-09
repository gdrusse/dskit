"""PanelScore and the two panel baselines: one scoring node, two fit-on-the-fit-band models.

Synthetic, CPU, seconds: two groups of hourly bars, a handful of days. Every expected number is
restated through an independent path (``CommonGrid.evaluate`` on rows cut by hand, ``numpy``
quantiles, ``StudentScaleFit`` fitted directly), never through the node under test.
"""

import itertools
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

import dskit.onboarding.acquire as acquire_module  # noqa: E402
from dskit.onboarding import OnboardingRoot, run_acquisition  # noqa: E402
from dskit.pipeline.base import ConfigError, OutputsConfig  # noqa: E402
from dskit.pipeline.document import NodeSpec, PipelineDocument, WalkForwardSpec  # noqa: E402
from dskit.pipeline.driver import run_walk_forward  # noqa: E402
from dskit.pipeline.libs import panel_score  # noqa: E402
from dskit.pipeline.libs.array_panel import DAY_MS, ArrayPanel, ArrayPanelWrite, PanelBands  # noqa: E402
from dskit.pipeline.libs.horizon_cdf import (  # noqa: E402
    CommonGrid,
    EmpiricalHorizonFit,
    HorizonForecaster,
    QuantileHorizons,
    StudentScaleFit,
)
from dskit.pipeline.libs.panel_score import (  # noqa: E402
    NODE_KINDS,
    EmpiricalBaseline,
    EmpiricalForecaster,
    EwmaStudentBaseline,
    PanelScore,
    StudentForecaster,
)
from dskit.pipeline.node import NodeContext  # noqa: E402

STEP = 3_600_000
HORIZONS = [1, 2, 3]
SPAN = max(HORIZONS) * STEP
GROUPS = (7, 9)
DAYS = 36
T0 = int(datetime(2023, 11, 15, tzinfo=timezone.utc).timestamp() * 1000)
VOL = "vol__ewma"
CUT = T0 + 24 * DAY_MS
BOUNDS = {"train_end_ms": CUT - DAY_MS - 1, "val_start_ms": CUT, "val_end_ms": CUT + 4 * DAY_MS - 1,
          "cal_days": 2, "min_run": 3}
GRID = {"z_min": -12.0, "z_max": 12.0, "z_points": 401, "levels": [0.1, 0.5, 0.9],
        "tail_intervals": [[-12.0, -2.0], [2.0, 12.0]]}
TAIL_WEIGHT = 0.5
EARLY_END_DAYS = 20


def make_columns(seed=0):
    rng = np.random.default_rng(seed)
    n = DAYS * 24
    time = np.concatenate([T0 + np.arange(n, dtype=np.int64) * STEP for _ in GROUPS])
    group = np.concatenate([np.full(n, g, dtype=np.int64) for g in GROUPS])
    total = len(time)
    vol = np.exp(rng.normal(0.0, 0.25, total))
    cols = {"t": time, "g": group, "ref": np.ones(total), VOL: vol, "f0": rng.normal(size=total)}
    for h in HORIZONS:
        y = vol * np.sqrt(h) * rng.standard_t(6, total)
        for g in GROUPS:
            y[np.flatnonzero(group == g)[-h:]] = np.nan
        cols[f"y_{h}"] = y
    return cols


def ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path))


def build_store(base, columns):
    """Register ``columns`` as one onboarding panel under ``base``; returns its source spec."""
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


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    """A real onboarding store holding one registered panel; yields its source spec."""
    return build_store(tmp_path_factory.mktemp("store"), make_columns())


@pytest.fixture(scope="module")
def short_store(tmp_path_factory):
    """A store whose second group stops before the validation window, so it has no validation rows."""
    columns = make_columns()
    keep = ~((columns["g"] == GROUPS[1]) & (columns["t"] >= T0 + EARLY_END_DAYS * DAY_MS))
    return build_store(tmp_path_factory.mktemp("short"), {k: v[keep] for k, v in columns.items()})


@pytest.fixture(scope="module")
def panel(store):
    return ArrayPanel.from_source(store)


def base_params(store, **over):
    params = {"panel": store, "bounds": dict(BOUNDS), "grid": dict(GRID)}
    params.update(over)
    return params


def score_params(store, **over):
    return base_params(store, **{"tail_weight": TAIL_WEIGHT, "split": "val", **over})


def train(node_cls, tmp_path, store, key="m", **over):
    node = node_cls(key, base_params(store, **over))
    return node, node.run(ctx(tmp_path), {})["model"]


STUDENT = {"vol_column": VOL, "degrees": [3, 5, 8]}


def val_labels(panel, rows):
    return torch.as_tensor(np.asarray(panel.labels[rows], dtype=np.float64))


def manual_bands(panel, **over):
    spec = dict(BOUNDS, **over)
    return PanelBands(panel, panel.groups, **spec)


# --- contract ---------------------------------------------------------------------------------


def test_no_registered_kinds_and_public_names_only():
    assert NODE_KINDS == ()
    assert set(panel_score.__all__) >= {"PanelScore", "EmpiricalBaseline", "EwmaStudentBaseline",
                                        "EmpiricalForecaster", "StudentForecaster", "NODE_KINDS"}
    assert not [n for n in panel_score.__all__ if n.startswith("_")]


def test_baseline_base_is_abstract():
    with pytest.raises(TypeError):
        panel_score._PanelBaseline("m", {})


def test_unknown_params_refused_by_name(store):
    for cls, extra in ((PanelScore, {"tail_weight": 1.0, "split": "val"}), (EmpiricalBaseline, {}),
                       (EwmaStudentBaseline, STUDENT)):
        with pytest.raises(ConfigError, match="typo_knob"):
            cls("m", base_params(store, typo_knob=1, **extra))


def test_bounds_keys_are_exact(store):
    short = {k: v for k, v in BOUNDS.items() if k != "min_run"}
    with pytest.raises(ConfigError, match="min_run"):
        PanelScore("s", score_params(store, bounds=short))
    with pytest.raises(ConfigError, match="extra_cut"):
        EmpiricalBaseline("m", base_params(store, bounds=dict(BOUNDS, extra_cut=1)))


def test_splits_references_are_accepted_in_bounds(store):
    refs = {k: f"$splits.{k}" for k in ("train_end_ms", "val_start_ms", "val_end_ms")}
    refs.update(cal_days=2, min_run=3)
    assert PanelScore.validate_params(score_params(store, bounds=refs)) == []
    assert EmpiricalBaseline.validate_params(base_params(store, bounds=refs)) == []


def test_score_requires_its_own_declarations(store):
    for knob in ("panel", "bounds", "grid", "tail_weight", "split"):
        params = score_params(store)
        del params[knob]
        with pytest.raises(ConfigError, match=knob):
            PanelScore("s", params)
    with pytest.raises(ConfigError, match="tail_weight"):
        PanelScore("s", score_params(store, tail_weight=-1.0))
    with pytest.raises(ConfigError, match="assets"):
        PanelScore("s", score_params(store, assets=[]))


@pytest.mark.parametrize("split", ["train", "cal", "test", "nope"])
def test_score_reads_the_validation_band_only(store, split):
    with pytest.raises(ConfigError, match="split"):
        PanelScore("s", score_params(store, split=split))


def test_student_baseline_requires_vol_column_and_degrees(store):
    for knob in ("vol_column", "degrees"):
        params = base_params(store, **STUDENT)
        del params[knob]
        with pytest.raises(ConfigError, match=knob):
            EwmaStudentBaseline("m", params)


def test_score_needs_a_forecaster_input(store, tmp_path):
    node = PanelScore("s", score_params(store))
    with pytest.raises(ValueError, match="model"):
        node.run(ctx(tmp_path), {})
    with pytest.raises(ValueError, match="model"):
        node.run(ctx(tmp_path), {"model": object()})


# --- the baselines fit on the fit band and match the owner's fits ------------------------------


def test_empirical_state_equals_numpy_quantiles_of_the_fit_rows(store, panel, tmp_path):
    node, model = train(EmpiricalBaseline, tmp_path, store)
    bands = manual_bands(panel)
    y = np.asarray(panel.labels[bands.fit_idx], dtype=np.float64)
    expected = np.quantile(y, GRID["levels"], axis=0).T            # (H, K)
    got = node.read_artifact(ctx(tmp_path), "state.json", ref=node.artifact_dir(ctx(tmp_path)))
    np.testing.assert_allclose(np.array(got["state"]["values"][0]), expected, rtol=0, atol=1e-12)
    assert isinstance(model, EmpiricalForecaster)
    assert got["rows_sha256"] == bands.rows_sha256()
    assert got["n_fit_rows"] == len(bands.fit_idx)
    direct = EmpiricalHorizonFit(GRID["levels"]).fit(val_labels(panel, bands.fit_idx))
    assert got["state"] == direct.state()


def test_empirical_fit_uses_fit_rows_only(store, panel, tmp_path):
    node, _ = train(EmpiricalBaseline, tmp_path, store)
    bands = manual_bands(panel)
    got = node.read_artifact(ctx(tmp_path), "state.json", ref=node.artifact_dir(ctx(tmp_path)))
    everything = np.asarray(panel.labels[:], dtype=np.float64)
    everything = everything[np.isfinite(everything).all(axis=1)]
    pooled_all = np.quantile(everything, GRID["levels"], axis=0).T
    assert not np.allclose(np.array(got["state"]["values"][0]), pooled_all)
    assert (panel.time[bands.fit_idx] + SPAN < bands.cal_start_ms).all()


def test_student_state_equals_the_owner_fit_on_the_same_rows(store, panel, tmp_path):
    node, model = train(EwmaStudentBaseline, tmp_path, store, **STUDENT)
    bands = manual_bands(panel)
    rows = bands.fit_idx
    vol = torch.as_tensor(np.asarray(panel.features[rows, panel.columns.index(VOL)], dtype=np.float64))
    direct = StudentScaleFit(degrees=STUDENT["degrees"]).fit(val_labels(panel, rows), vol)
    got = node.read_artifact(ctx(tmp_path), "state.json", ref=node.artifact_dir(ctx(tmp_path)))
    assert got["state"]["fit"] == direct.state()
    assert got["state"]["vol_column"] == VOL
    assert isinstance(model, StudentForecaster)
    assert model.components == 1 and model.levels is None


def test_student_refuses_an_unknown_vol_column(store, tmp_path):
    node = EwmaStudentBaseline("m", base_params(store, vol_column="nope", degrees=[3]))
    with pytest.raises(ValueError, match="nope"):
        node.run(ctx(tmp_path), {})


@pytest.mark.parametrize("cls,extra", [(EmpiricalBaseline, {}), (EwmaStudentBaseline, STUDENT)])
def test_baseline_fit_needs_panel_bounds_and_grid(store, tmp_path, cls, extra):
    for knob in ("panel", "bounds", "grid"):
        params = base_params(store, **extra)
        del params[knob]
        with pytest.raises(ValueError, match=knob):
            cls("m", params).run(ctx(tmp_path), {})


@pytest.mark.parametrize("cls,extra", [(EmpiricalBaseline, {}), (EwmaStudentBaseline, STUDENT)])
def test_baseline_save_then_load_forecasts_identically(store, panel, tmp_path, cls, extra):
    node, fitted = train(cls, tmp_path, store, **extra)
    art = node.artifact_dir(ctx(tmp_path))
    restored = cls("m", {"grid": dict(GRID), **extra}, mode="load", artifact=art).run(ctx(tmp_path), {})["model"]
    rows = manual_bands(panel).val_idx[:50]
    a, b = fitted.forecast(panel, rows, 50), restored.forecast(panel, rows, 50)
    levels = [0.05, 0.3, 0.5, 0.95]
    assert torch.equal(a.quantile(levels), b.quantile(levels))
    z = torch.linspace(-4, 4, 9, dtype=torch.float64)
    assert torch.equal(a.cdf(z), b.cdf(z))
    assert restored.training_bounds == fitted.training_bounds == {"min_run": 3, "cal_days": 2}


def test_load_refuses_a_state_from_another_baseline_and_a_changed_grid(store, tmp_path):
    node, _ = train(EmpiricalBaseline, tmp_path, store)
    art = node.artifact_dir(ctx(tmp_path))
    with pytest.raises(ValueError, match="EmpiricalBaseline"):
        EwmaStudentBaseline("m", {"grid": dict(GRID), **STUDENT}, mode="load", artifact=art).run(ctx(tmp_path), {})
    other = dict(GRID, levels=[0.2, 0.5, 0.8])
    with pytest.raises(ValueError, match="levels"):
        EmpiricalBaseline("m", {"grid": other}, mode="load", artifact=art).run(ctx(tmp_path), {})


def test_load_without_a_grid_refuses_by_name(store, tmp_path):
    node, _ = train(EmpiricalBaseline, tmp_path, store)
    with pytest.raises(ValueError, match="grid"):
        EmpiricalBaseline("m", {}, mode="load", artifact=node.artifact_dir(ctx(tmp_path))).run(ctx(tmp_path), {})


# --- PanelScore --------------------------------------------------------------------------------


def run_score(store, tmp_path, model, key="score", **over):
    node = PanelScore(key, score_params(store, **over))
    return node, node.run(ctx(tmp_path), {"model": model})["metrics"]


def test_metrics_equal_the_grid_evaluated_by_hand(store, panel, tmp_path):
    _, model = train(EwmaStudentBaseline, tmp_path, store, **STUDENT)
    _, metrics = run_score(store, tmp_path, model)
    bands = manual_bands(panel)
    direct = CommonGrid(GRID).evaluate(model, panel, bands.val_idx, val_labels(panel, bands.val_idx))
    for j, h in enumerate(HORIZONS):
        for name in ("crps", "tail_crps", "pinball", "pit_ks"):
            assert metrics[f"{name}_h{h}"] == pytest.approx(direct[name][j], rel=1e-12)
    for name in ("crps", "tail_crps", "pinball", "pit_ks"):
        assert metrics[name] == pytest.approx(sum(direct[name]) / len(HORIZONS), rel=1e-12)
    assert metrics["weighted_crps"] == pytest.approx(metrics["crps"] + TAIL_WEIGHT * metrics["tail_crps"])
    assert metrics["n_rows"] == len(bands.val_idx) == direct["n_rows"]
    assert metrics["rows_sha256"] == bands.rows_sha256()
    assert all(np.isfinite(v) for v in metrics.values() if not isinstance(v, str))


def test_two_different_forecasters_score_the_same_rows(store, tmp_path):
    _, empirical = train(EmpiricalBaseline, tmp_path, store, key="emp")
    _, student = train(EwmaStudentBaseline, tmp_path, store, key="stu", **STUDENT)
    _, a = run_score(store, tmp_path, empirical, key="s1")
    _, b = run_score(store, tmp_path, student, key="s2")
    assert a["rows_sha256"] == b["rows_sha256"] and a["n_rows"] == b["n_rows"]
    assert a["weighted_crps"] != b["weighted_crps"]


def test_assets_subset_equals_manual_subsetting(store, panel, tmp_path):
    _, model = train(EwmaStudentBaseline, tmp_path, store, **STUDENT)
    _, metrics = run_score(store, tmp_path, model, assets=[GROUPS[0]])
    bands = manual_bands(panel)
    rows = bands.val_idx[np.asarray(panel.group)[bands.val_idx] == GROUPS[0]]
    direct = CommonGrid(GRID).evaluate(model, panel, rows, val_labels(panel, rows))
    assert metrics["n_rows"] == len(rows) < len(bands.val_idx)
    for j, h in enumerate(HORIZONS):
        assert metrics[f"crps_h{h}"] == pytest.approx(direct["crps"][j], rel=1e-12)
    with pytest.raises(ValueError, match="assets"):
        run_score(store, tmp_path, model, assets=[12345])


def test_artifact_carries_the_full_tables(store, panel, tmp_path):
    _, model = train(EmpiricalBaseline, tmp_path, store)
    node, metrics = run_score(store, tmp_path, model)
    with open(os.path.join(node.artifact_dir(ctx(tmp_path)), "horizon_scores.json"), encoding="utf-8") as fh:
        art = json.load(fh)
    assert art["cdf_basis"] == "extrapolated"
    assert art["horizons"] == HORIZONS and art["levels"] == GRID["levels"]
    assert art["tail_weight"] == TAIL_WEIGHT and art["rows_sha256"] == metrics["rows_sha256"]
    assert sorted(art["per_asset"]) == sorted(str(g) for g in GROUPS)
    assert art["overall"]["n_rows"] == sum(a["n_rows"] for a in art["per_asset"].values())
    assert len(art["overall"]["histogram"]) == len(HORIZONS)
    by_level = np.array(art["pinball_by_level"]["overall"])
    assert by_level.shape == (len(HORIZONS), len(GRID["levels"]))
    np.testing.assert_allclose(by_level.mean(axis=1), art["overall"]["pinball"], rtol=1e-12)
    assert sorted(art["pinball_by_level"]["per_asset"]) == sorted(art["per_asset"])


def test_single_asset_scores_once_and_reports_it(store, tmp_path):
    _, model = train(EmpiricalBaseline, tmp_path, store)
    node, metrics = run_score(store, tmp_path, model, assets=[GROUPS[1]])
    with open(os.path.join(node.artifact_dir(ctx(tmp_path)), "horizon_scores.json"), encoding="utf-8") as fh:
        art = json.load(fh)
    assert list(art["per_asset"]) == [str(GROUPS[1])]
    assert art["per_asset"][str(GROUPS[1])] == art["overall"]


def test_a_group_without_validation_rows_is_reported_as_null_not_a_crash(short_store, tmp_path):
    _, model = train(EmpiricalBaseline, tmp_path, short_store)
    node, metrics = run_score(short_store, tmp_path, model)
    with open(os.path.join(node.artifact_dir(ctx(tmp_path)), "horizon_scores.json"), encoding="utf-8") as fh:
        art = json.load(fh)
    empty = str(GROUPS[1])
    assert art["per_asset"][empty] is None and art["pinball_by_level"]["per_asset"][empty] is None
    assert art["per_asset"][str(GROUPS[0])] == art["overall"] and metrics["n_rows"] > 0
    assert sorted(art["pinball_by_level"]["per_asset"]) == sorted(art["per_asset"])


class _Declared(HorizonForecaster):
    """Row-constant knots over the grid's levels, with whatever pins a test declares."""

    levels = tuple(GRID["levels"])

    def __init__(self, **attrs):
        for name, value in attrs.items():
            setattr(self, name, value)

    def forecast(self, panel, rows, chunk_rows):
        knots = torch.tensor([[[-1.3, 0.0, 1.3]] * len(HORIZONS)], dtype=torch.float64)
        return QuantileHorizons(self.levels, knots, (GRID["z_min"], GRID["z_max"]))


def test_declared_pins_that_agree_pass(store, tmp_path):
    model = _Declared(training_tail=GRID["tail_intervals"], training_tail_weight=TAIL_WEIGHT,
                      training_bounds={"min_run": 3, "cal_days": 2})
    _, metrics = run_score(store, tmp_path, model)
    assert np.isfinite(metrics["weighted_crps"])


@pytest.mark.parametrize("attrs,needle", [
    ({"training_tail": [[-12.0, -3.0], [3.0, 12.0]]}, "training_tail"),
    ({"levels": (0.25, 0.5, 0.75)}, "levels"),
    ({"training_tail_weight": 2.0}, "tail_weight"),
    ({"training_bounds": {"min_run": 4, "cal_days": 2}}, "min_run"),
    ({"training_bounds": {"min_run": 3, "cal_days": 5}}, "cal_days"),
])
def test_mismatched_pins_are_refused_by_name(store, tmp_path, attrs, needle):
    with pytest.raises(ValueError, match=needle):
        run_score(store, tmp_path, _Declared(**attrs))


def test_unpinned_forecaster_is_scored_without_the_optional_pins(store, tmp_path):
    _, metrics = run_score(store, tmp_path, _Declared())
    assert np.isfinite(metrics["crps"])


def test_node_bounds_that_leave_a_band_empty_refuse(store, tmp_path):
    _, model = train(EmpiricalBaseline, tmp_path, store)
    node = PanelScore("s", score_params(store, bounds=dict(BOUNDS, val_start_ms=T0 + 90 * DAY_MS,
                                                           val_end_ms=T0 + 91 * DAY_MS)))
    with pytest.raises(ValueError, match="empty"):
        node.run(ctx(tmp_path), {"model": model})


# --- the walk-forward lane: baselines alone, two folds, expanding -------------------------------


def _wf_document(tmp_path, store):
    refs = {"train_end_ms": "$splits.train_end_ms", "val_start_ms": "$splits.val_start_ms",
            "val_end_ms": "$splits.val_end_ms", "cal_days": 2, "min_run": 3}
    common = {"panel": store, "bounds": refs, "grid": dict(GRID)}
    lib = "dskit.pipeline.libs.panel_score"
    pipeline = {
        "emp": NodeSpec(uses=f"{lib}:EmpiricalBaseline", params=dict(common)),
        "stu": NodeSpec(uses=f"{lib}:EwmaStudentBaseline", params=dict(common, **STUDENT)),
        "score_emp": NodeSpec(uses=f"{lib}:PanelScore", inputs={"model": "$emp.model"},
                              params=dict(common, tail_weight=TAIL_WEIGHT, split="val")),
        "score_stu": NodeSpec(uses=f"{lib}:PanelScore", inputs={"model": "$stu.model"},
                              params=dict(common, tail_weight=TAIL_WEIGHT, split="val")),
    }
    wf = WalkForwardSpec(objective="$score_emp.metrics.weighted_crps", val_days=4, embargo_days=1,
                         folds=["2023-12-06", "2023-12-11"])
    return PipelineDocument(name="panelwf", pipeline=pipeline, outputs=OutputsConfig(run_root=str(tmp_path)),
                            walkforward=wf)


def _fold_files(run_dir, key, name):
    with open(os.path.join(run_dir, "artifacts", key, name), encoding="utf-8") as fh:
        return json.load(fh)


def test_two_fold_expanding_walkforward_runs_end_to_end(store, panel, tmp_path):
    result = run_walk_forward(_wf_document(tmp_path, store), asof="2026-01-01")
    assert result.state == "ran" and len(result.folds) == 2
    n_fit = []
    for fold in result.folds:
        run_dir = fold["run_dir"]
        with open(os.path.join(run_dir, "resolved.json"), encoding="utf-8") as fh:
            cuts = json.load(fh)["splits"]
        bands = manual_bands(panel, train_end_ms=cuts["train_end_ms"], val_start_ms=cuts["val_start_ms"],
                             val_end_ms=cuts["val_end_ms"])
        emp = _fold_files(run_dir, "emp", "state.json")
        stu = _fold_files(run_dir, "stu", "state.json")
        scored = _fold_files(run_dir, "score_emp", "horizon_scores.json")
        other = _fold_files(run_dir, "score_stu", "horizon_scores.json")
        assert emp["rows_sha256"] == stu["rows_sha256"] == scored["rows_sha256"] == other["rows_sha256"]
        assert emp["rows_sha256"] == bands.rows_sha256()
        assert (panel.time[bands.fit_idx] + SPAN < bands.cal_start_ms).all()
        assert (panel.time[bands.fit_idx] + SPAN < cuts["val_start_ms"]).all()
        assert (panel.time[bands.cal_idx] + SPAN < cuts["val_start_ms"]).all()
        assert fold["score"] is not None and np.isfinite(fold["score"])
        n_fit.append(emp["n_fit_rows"])
    assert n_fit[0] < n_fit[1]                                    # expanding: fold 2 fits on more
    assert result.aggregate["n_scored"] == 2
