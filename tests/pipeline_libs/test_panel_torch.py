"""panel_torch pack: heads, a panel-reading trainer, and a forecaster the common grid scores.

Everything is a few hundred synthetic hourly rows and tiny CPU nets: no real data, no network,
seconds-long. Expected values are restated in plain numpy / torch, independently of the pack.
"""

import json

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

from dskit.pipeline.base import ConfigError  # noqa: E402
from dskit.pipeline.libs import panel_torch as pt  # noqa: E402
from dskit.pipeline.libs.array_panel import (  # noqa: E402
    ArrayPanel,
    ArrayPanelWrite,
    DevicePanel,
    PanelBands,
    PanelBatch,
)
from dskit.pipeline.libs.horizon_cdf import (  # noqa: E402
    CommonGrid,
    HorizonForecaster,
    MixtureHorizons,
    QuantileHorizons,
)
from dskit.pipeline.libs.panel_torch import (  # noqa: E402
    HEADS,
    NODE_KINDS,
    HorizonHead,
    MixtureHead,
    PanelAdapter,
    PanelTrain,
    QuantileHead,
    TorchHorizonForecaster,
)
from dskit.pipeline.libs.predictive_cdf import MixtureLossSpec, mixture_parts  # noqa: E402
from dskit.pipeline.libs.torch_ts import quantile_pinball_loss  # noqa: E402
from dskit.pipeline.node import NodeContext  # noqa: E402

STEP = 3_600_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % STEP)
HORIZONS = [1, 3]
GROUP_SIZES = {10: 420, 20: 360}
GAP = {10: 200}                      # group 10 skips 4 bars before this row
WINDOW, STRIDE = 8, 2
MIN_RUN = (WINDOW - 1) * STRIDE + 1  # 15
BOUNDS = {"train_end_ms": T0 + 300 * STEP, "val_start_ms": T0 + 310 * STEP,
          "val_end_ms": T0 + 340 * STEP, "cal_days": 2, "min_run": MIN_RUN}
TAIL = [[-8.0, -2.0], [2.0, 8.0]]
MIXTURE = {"kind": "mixture", "components": 2, "family": {"kind": "gaussian"},
           "losses": [{"kind": "nll", "weight": 1.0}], "min_scale": 0.1}
MIXTURE_TAIL = {"kind": "mixture", "components": 2, "family": {"kind": "student", "degrees": 5},
                "losses": [{"kind": "crps", "weight": 1.0}, {"kind": "tail_crps", "weight": 0.5}],
                "tail_intervals": TAIL, "min_scale": 0.1}
QUANTILE = {"kind": "quantile", "quantiles": [0.1, 0.5, 0.9], "bounds": [-8.0, 8.0]}
GRID = {"z_min": -8.0, "z_max": 8.0, "levels": [0.1, 0.5, 0.9], "tail_intervals": TAIL}


def make_columns(seed=0, sizes=None, gap=None, perturb_after=None):
    """Two sorted groups; y_h is a noisy function of f0 and the channel ``seq__a``."""
    sizes = GROUP_SIZES if sizes is None else sizes
    gap = GAP if gap is None else gap
    rng = np.random.default_rng(seed)
    time, group = [], []
    for g, n in sizes.items():
        t = T0 + np.arange(n, dtype=np.int64) * STEP
        if g in gap:
            t[gap[g]:] += 4 * STEP
        time.append(t)
        group.append(np.full(n, g, dtype=np.int64))
    time, group = np.concatenate(time), np.concatenate(group)
    n = len(time)
    cols = {"t": time, "g": group, "ref": rng.uniform(0.5, 2.0, n)}
    feats = {f"f{k}": rng.normal(size=n) for k in range(3)}
    feats["seq__a"] = rng.normal(size=n)
    feats["seq__b"] = rng.normal(size=n)
    for h in HORIZONS:
        y = 0.7 * feats["seq__a"] + 0.4 * feats["f0"] + 0.3 * rng.normal(size=n)
        for g in sizes:
            idx = np.flatnonzero(group == g)
            y[idx[-h:]] = np.nan
        cols[f"y_{h}"] = y
    if perturb_after is not None:
        g, row = perturb_after
        later = (group == g) & (np.arange(n) > row)
        for name in ("f0", "f1", "f2", "seq__a", "seq__b"):
            feats[name] = np.where(later, feats[name] + 5.0, feats[name])
    cols.update(feats)
    return cols


def build_panel(tmp_path, name="panel", step=STEP, **kw):
    node = ArrayPanelWrite("w", {
        "time": "t", "group": "g", "reference": "ref", "label_prefix": "y_",
        "horizons": list(HORIZONS), "step_ms": step, "path": str(tmp_path / name)})
    node.run(NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path)),
             {"columns": make_columns(**kw)})
    return str(tmp_path / name)


def params(path, **over):
    base = {
        "panel": {"root": "r", "source": "s", "stream": "st", "relpath": path},
        "assets": [10, 20], "asset_indicator": True, "bounds": dict(BOUNDS),
        "sequence": {"columns": {"prefixes": ["seq__"]}, "window": WINDOW, "stride": STRIDE},
        "context": {"prefixes": ["f"]},
        "encoder": {"arch": "gru", "width": 6, "arch_params": {"hidden_size": 6}},
        "trunk": {"hidden": [8], "activation": "relu"},
        "head": MIXTURE, "epochs": 6, "lr": 0.02,
        "optimizer": "torch.optim.Adam", "loader": {"batch_size": 64, "seed": 3},
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def store_stand_in(monkeypatch):
    """Open ``spec["relpath"]`` directly: the store round trip is array_panel's own test."""
    seen = []

    def opener(cls, spec):
        seen.append(dict(spec))
        return cls.open(spec["relpath"])

    monkeypatch.setattr(ArrayPanel, "from_source", classmethod(opener))
    return seen


@pytest.fixture
def path(tmp_path):
    return build_panel(tmp_path)


def ctx(tmp_path, name="run"):
    return NodeContext(name=name, asof="2026-01-01", run_dir=str(tmp_path / name))


def fit(tmp_path, path, key="model", run="run", **over):
    node = PanelTrain(key, params(path, **over))
    return node, node.run(ctx(tmp_path, run), {})


def val_rows(path, **over):
    panel = ArrayPanel.open(path)
    bands = PanelBands(panel, **bands_args(over))
    return panel, bands


def bands_args(over=None):
    over = over or {}
    return {"assets": over.get("assets", [10, 20]), **{
        k: v for k, v in BOUNDS.items()}}


# --- registry, heads ------------------------------------------------------------------------


def test_no_registered_kinds():
    assert NODE_KINDS == ()


def test_heads_registry_and_abstract_base():
    assert set(HEADS) == {"mixture", "quantile"}
    assert HEADS["mixture"] is MixtureHead and HEADS["quantile"] is QuantileHead
    with pytest.raises(TypeError):
        HorizonHead(MIXTURE)

    class Incomplete(HorizonHead):
        kind = "incomplete"

    with pytest.raises(TypeError):
        Incomplete({})


@pytest.mark.parametrize("spec,fragment", [
    ({**MIXTURE, "typo": 1}, "typo"),
    ({**QUANTILE, "typo": 1}, "typo"),
    ({**QUANTILE, "losses": MIXTURE["losses"]}, "losses"),
    ({**MIXTURE, "quantiles": [0.1, 0.9]}, "quantiles"),
    ({"kind": "nope"}, "kind"),
    ("mixture", "kind"),
    ({**MIXTURE, "components": 0}, "components"),
    ({**MIXTURE, "components": True}, "components"),
    ({**MIXTURE, "min_scale": 0}, "min_scale"),
    ({**MIXTURE, "family": {"kind": "laplace"}}, "family"),
    ({**MIXTURE, "losses": [{"kind": "decision_brier", "weight": 1.0}]}, "losses"),
    ({**MIXTURE, "losses": [{"kind": "tail_crps", "weight": 1.0}]}, "tail_intervals"),
    ({**MIXTURE, "tail_intervals": TAIL}, "tail_intervals"),
    ({k: v for k, v in MIXTURE.items() if k != "losses"}, "losses"),
    ({**QUANTILE, "quantiles": [0.5]}, "quantiles"),
    ({**QUANTILE, "quantiles": [0.9, 0.1]}, "quantiles"),
    ({**QUANTILE, "quantiles": [0.0, 0.5]}, "quantiles"),
    ({**QUANTILE, "bounds": [3.0, 1.0]}, "bounds"),
    ({k: v for k, v in QUANTILE.items() if k != "bounds"}, "bounds"),
])
def test_bad_head_specs_are_refused_by_name(spec, fragment):
    problems = HorizonHead.check(spec)
    assert problems and any(fragment in p for p in problems), problems
    with pytest.raises(ValueError):
        HorizonHead.build(spec)


def test_a_mispaired_key_says_which_head_owns_it():
    assert any("mixture head" in p for p in HorizonHead.check({**QUANTILE, "losses": []}))
    assert any("quantile head" in p for p in HorizonHead.check({**MIXTURE, "quantiles": [0.1, 0.9]}))


def test_good_head_specs_have_no_problems():
    for spec in (MIXTURE, MIXTURE_TAIL, QUANTILE):
        assert HorizonHead.check(spec) == []


def test_mixture_head_loss_is_mixture_loss_spec_on_the_same_tensors():
    head = HorizonHead.build(MIXTURE_TAIL)
    assert head.width(3) == 3 * 2 * 3 and head.components == 2
    assert head.levels is None and head.training_tail == TAIL
    torch.manual_seed(0)
    raw, y = torch.randn(5, 3, 6), torch.randn(5, 3) * 2
    spec = MixtureLossSpec(MIXTURE_TAIL["losses"], MIXTURE_TAIL["family"], TAIL)
    logw, mu, sigma = mixture_parts(raw.reshape(15, 6), 0.1)
    expected = spec.loss(y.reshape(15), logw, mu, sigma)
    assert torch.allclose(head.loss(raw, y), expected)
    # float32 on the device, float64 as the reference: the same number to float32 precision
    assert torch.allclose(head.loss(raw, y), head.loss(raw.double(), y.double()).float(), rtol=1e-4)


def test_mixture_head_distribution_is_a_valid_mixture_per_row_and_horizon():
    head = HorizonHead.build(MIXTURE)
    dist = head.distribution(torch.randn(4, 3, 6))
    assert isinstance(dist, MixtureHorizons) and dist.shape == (4, 3)
    assert torch.allclose(dist.logw.exp().sum(-1), torch.ones(4, 3))
    assert bool((dist.sigma > 0.1).all())
    cdf = dist.cdf(torch.tensor([-50.0, 0.0, 50.0]))
    assert cdf.shape == (4, 3, 3) and bool((cdf >= 0).all() and (cdf <= 1).all())
    assert head.training_tail is None


def test_quantile_head_sorts_trains_on_pinball_and_emits_clipped_knots():
    head = HorizonHead.build(QUANTILE)
    assert head.width(3) == 9 and head.levels == (0.1, 0.5, 0.9) and head.training_tail is None
    torch.manual_seed(1)
    raw, y = torch.randn(6, 3, 3), torch.randn(6, 3)
    ordered = torch.sort(raw, dim=-1).values
    assert torch.allclose(head.loss(raw, y), quantile_pinball_loss(ordered, y, [0.1, 0.5, 0.9]))
    dist = head.distribution(raw)
    assert isinstance(dist, QuantileHorizons) and dist.bounds == (-8.0, 8.0)
    assert torch.allclose(dist.quantile([0.1, 0.5, 0.9]), ordered)
    assert bool((torch.diff(dist.quantile([0.1, 0.5, 0.9]), dim=-1) >= 0).all())


# --- the node's knobs -----------------------------------------------------------------------


def test_params_are_default_deny_and_required(path):
    with pytest.raises(ConfigError, match="typo"):
        PanelTrain("m", params(path, typo=1))
    for knob in ("panel", "assets", "bounds", "trunk", "head"):
        bad = params(path)
        del bad[knob]
        with pytest.raises(ConfigError, match=knob):
            PanelTrain("m", bad)
    for knob in ("features", "label"):
        with pytest.raises(ConfigError, match=knob):
            PanelTrain("m", params(path, **{knob: ["f0"] if knob == "features" else "y"}))


@pytest.mark.parametrize("over,fragment", [
    ({"assets": []}, "assets"),
    ({"assets": [10, 10]}, "assets"),
    ({"assets": ["a"]}, "assets"),
    ({"asset_indicator": "yes"}, "asset_indicator"),
    ({"panel": "x"}, "panel"),
    ({"bounds": {**BOUNDS, "typo": 1}}, "bounds"),
    ({"bounds": {k: v for k, v in BOUNDS.items() if k != "min_run"}}, "min_run"),
    ({"bounds": {**BOUNDS, "cal_days": 1.5}}, "cal_days"),
    ({"sequence": {"columns": {"prefixes": ["seq__"]}, "window": 0}}, "window"),
    ({"sequence": {"columns": {"prefixes": ["seq__"]}, "window": 8, "stride": 0}}, "stride"),
    ({"sequence": {"window": 8}}, "columns"),
    ({"sequence": {"columns": {"prefixes": ["seq__"]}, "window": 8, "typo": 1}}, "typo"),
    ({"sequence": {"columns": {"typo": ["x"]}, "window": 8}}, "typo"),
    ({"context": {"names": []}}, "context"),
    ({"encoder": None}, "encoder"),                         # sequence without an encoder
    ({"sequence": None, "context": {"prefixes": ["f"]}}, "encoder"),   # encoder without a sequence
    ({"encoder": {"arch": "nope", "width": 4}}, "arch"),
    ({"encoder": {"arch": "gru", "width": 0}}, "width"),
    ({"encoder": {"arch": "gru", "width": 4, "typo": 1}}, "typo"),
    ({"encoder": {"class": "x:Y", "params": {}, "arch": "gru"}}, "arch"),
    ({"trunk": {"hidden": [], "activation": "relu"}}, "hidden"),
    ({"trunk": {"hidden": [4], "activation": "gelu"}}, "activation"),
    ({"trunk": {"hidden": [4], "activation": "relu", "dropout": 1.0}}, "dropout"),
    ({"trunk": {"hidden": [4], "activation": "relu", "typo": 1}}, "typo"),
    ({"head": {"kind": "nope"}}, "kind"),
    ({"max_parameters": 0}, "max_parameters"),
    ({"missing": "drop"}, "missing"),
    ({"monitor": "logloss"}, "monitor"),
    ({"sequence": None, "encoder": None, "context": None}, "context"),
])
def test_bad_knobs_are_refused_at_plan(path, over, fragment):
    bad = {k: v for k, v in params(path, **over).items() if v is not None}
    problems = PanelTrain.validate_params(bad)
    assert problems and any(fragment in p for p in problems), problems


def test_a_declared_loss_is_refused_by_name_and_so_is_a_wired_rows_port(path):
    with pytest.raises(ConfigError, match="loss"):
        PanelTrain("m", params(path, loss="torch.nn.functional:mse_loss"))
    with pytest.raises(ConfigError, match="loss_params"):
        PanelTrain("m", params(path, loss_params={"reduction": "sum"}))
    node = PanelTrain("m", params(path))
    assert node.validate_inputs({}) == []
    for port in ("rows", "val_rows"):
        problems = node.validate_inputs({port: []})
        assert problems and any(port in p for p in problems)


def test_good_params_validate_for_every_encoder_form_and_head(path):
    assert PanelTrain.validate_params(params(path)) == []
    for head in (MIXTURE_TAIL, QUANTILE):
        assert PanelTrain.validate_params(params(path, head=head)) == []
    assert PanelTrain.validate_params(params(path, sequence=None, encoder=None)) == []
    cls = {"class": "dskit.pipeline.libs.torch_ts:ArchSequenceEncoder",
           "params": {"arch": "tcn", "width": 4, "arch_params": {"kernel_size": 3, "dilations": [1, 2, 4]}}}
    assert PanelTrain.validate_params(params(path, encoder=cls)) == []


def test_node_refs_in_bounds_pass_plan(path):
    over = {"bounds": {**BOUNDS, "train_end_ms": "$splits.train_end_ms", "min_run": "$cells.min_run"}}
    assert PanelTrain.validate_params(params(path, **over)) == []


# --- fitting --------------------------------------------------------------------------------


def test_fit_outputs_loss_falls_and_the_panel_is_opened_through_the_store(
        tmp_path, path, store_stand_in):
    node, out = fit(tmp_path, path)
    assert set(out) == {"signal", "artifact_path", "metrics", "model"}
    assert isinstance(out["model"], TorchHorizonForecaster)
    assert isinstance(out["model"], HorizonForecaster)
    assert store_stand_in[0] == params(path)["panel"]
    curve = json.load(open(tmp_path / "run" / "artifacts" / "model" / "training_curve.json"))
    losses = [row["train_loss"] for row in curve["epochs"]]
    assert losses[-1] < losses[0] - 0.05
    assert all("val_loss" in row for row in curve["epochs"])
    panel, bands = val_rows(path)
    assert out["metrics"]["n_rows"] == len(bands.fit_idx)
    assert out["metrics"]["n_val_rows"] == len(bands.cal_idx)
    assert out["signal"].predict({"f0": 1.0}) is None       # serving goes through the forecaster


def test_validation_rows_are_never_uploaded(tmp_path, path, monkeypatch):
    loads = []
    real = DevicePanel.load.__func__

    def spy(cls, panel, bands, features, channels, spans, *args, **kw):
        loads.append(list(spans))
        return real(cls, panel, bands, features, channels, spans, *args, **kw)

    monkeypatch.setattr(DevicePanel, "load", classmethod(spy))
    fit(tmp_path, path, epochs=1)
    panel, bands = val_rows(path)
    assert len(loads) == 1
    for lo, hi in loads[0]:
        assert not np.any((bands.val_idx >= lo) & (bands.val_idx < hi))


def test_scaler_is_fit_on_the_fit_band_only_and_saved_with_the_artifact(tmp_path, path):
    _, out = fit(tmp_path, path, epochs=1)
    panel, bands = val_rows(path)
    sidecar = json.load(open(out["artifact_path"].replace(".pt", ".json")))
    state = sidecar["adapter_state"]["panel"]
    names = state["scaler"]["columns"]
    assert sorted(names) == ["f0", "f1", "f2", "seq__a", "seq__b"]
    col = panel.columns.index("f0")
    fit_values = np.asarray(panel.features[bands.fit_idx, col], dtype=np.float64)
    assert state["scaler"]["mean"][names.index("f0")] == pytest.approx(fit_values.mean())
    assert state["scaler"]["std"][names.index("f0")] == pytest.approx(fit_values.std())
    assert state["rows_sha256"] == bands.rows_sha256()
    assert sidecar["data_params"] == {"context_width": 3 + 2, "window": WINDOW, "channels": 2,
                                      "horizons": 2}


def test_same_seed_gives_an_identical_state_dict_and_another_seed_does_not(tmp_path, path):
    _, a = fit(tmp_path, path, run="a")
    _, b = fit(tmp_path, path, run="b")
    _, c = fit(tmp_path, path, run="c", loader={"batch_size": 64, "seed": 4})
    sa, sb, sc = (torch.load(o["artifact_path"], weights_only=True) for o in (a, b, c))
    assert all(torch.equal(sa[k], sb[k]) for k in sa)
    assert any(not torch.equal(sa[k], sc[k]) for k in sa)


def test_early_stopping_restores_the_best_epoch(tmp_path, path):
    _, out = fit(tmp_path, path, epochs=40, lr=0.2, patience=2, monitor="val_loss")
    curve = json.load(open(tmp_path / "run" / "artifacts" / "model" / "training_curve.json"))
    rows = curve["epochs"]
    assert len(rows) < 40, "the run must stop early for this test to mean anything"
    best = min(rows, key=lambda r: r["val_loss"])
    assert out["metrics"]["selected_epoch"] == best["epoch"]
    assert out["metrics"]["monitor_value"] == pytest.approx(best["val_loss"])
    assert len(rows) - best["epoch"] == 2
    # the persisted weights ARE the best epoch's: a plain fit of that many epochs is identical
    _, plain = fit(tmp_path, path, run="plain", epochs=best["epoch"], lr=0.2)
    kept = torch.load(out["artifact_path"], weights_only=True)
    plain_state = torch.load(plain["artifact_path"], weights_only=True)
    assert all(torch.equal(kept[k], plain_state[k]) for k in kept)


def test_pooled_with_indicator_and_one_net_per_asset(tmp_path, path):
    _, pooled = fit(tmp_path, path, run="pooled")
    _, solo = fit(tmp_path, path, run="solo", assets=[10], asset_indicator=False)
    panel, both = val_rows(path)
    only = PanelBands(panel, **{**bands_args(), "assets": [10]})
    assert pooled["metrics"]["n_rows"] == len(both.fit_idx)
    assert solo["metrics"]["n_rows"] == len(only.fit_idx) < pooled["metrics"]["n_rows"]
    side = json.load(open(solo["artifact_path"].replace(".pt", ".json")))
    assert side["data_params"]["context_width"] == 3            # no indicator columns
    rows = only.val_idx[:6]
    assert solo["model"].forecast(panel, rows, 6).shape == (6, 2)
    with pytest.raises(ValueError, match="group"):
        solo["model"].forecast(panel, both.val_idx[-3:], 3)    # rows of the other asset


def test_the_indicator_appends_a_one_hot_asset_code_to_the_context(tmp_path, path):
    batch = PanelBatch(x=torch.ones(3, 3), seq=None, y=torch.zeros(3, 2), ref=torch.ones(3),
                       group=torch.tensor([10, 20, 10]))
    context, _ = PanelAdapter(params(path)).model_inputs(batch)
    assert context.tolist() == [[1, 1, 1, 1, 0], [1, 1, 1, 0, 1], [1, 1, 1, 1, 0]]
    plain, _ = PanelAdapter(params(path, asset_indicator=False)).model_inputs(batch)
    assert plain.shape == (3, 3)
    _, a = fit(tmp_path, path, run="a", epochs=1)
    _, b = fit(tmp_path, path, run="b", epochs=1, asset_indicator=False)
    widths = [json.load(open(o["artifact_path"].replace(".pt", ".json")))["data_params"]["context_width"]
              for o in (a, b)]
    assert widths == [5, 3]


@pytest.mark.parametrize("encoder", [
    {"arch": "gru", "width": 5, "arch_params": {"hidden_size": 4}},
    {"arch": "tcn", "width": 5, "arch_params": {"hidden_size": 4, "kernel_size": 3, "dilations": [1, 2, 4]}},
    {"class": "dskit.pipeline.libs.torch_ts:ArchSequenceEncoder",
     "params": {"arch": "lstm", "width": 5, "arch_params": {"hidden_size": 4}}},
])
def test_arch_encoders_train_and_forecast(tmp_path, path, encoder):
    _, out = fit(tmp_path, path, epochs=2, encoder=encoder)
    panel, bands = val_rows(path)
    dist = out["model"].forecast(panel, bands.val_idx[:8], 8)
    assert dist.shape == (8, 2)
    assert np.isfinite(dist.mu.numpy()).all()


def test_no_encoder_is_a_plain_mlp_over_the_context(tmp_path, path):
    _, out = fit(tmp_path, path, epochs=2, sequence=None, encoder=None)
    side = json.load(open(out["artifact_path"].replace(".pt", ".json")))
    assert side["data_params"]["channels"] == 0 and side["data_params"]["window"] == 1
    panel, bands = val_rows(path)
    assert out["model"].forecast(panel, bands.val_idx[:5], 5).shape == (5, 2)


def test_hf_patchtst_encoder_trains(tmp_path, path):
    pytest.importorskip("transformers")
    encoder = {"class": "dskit.pipeline.libs.transformers:HFSequenceEncoder",
               "params": {"model": "PatchTST", "config": {
                   "d_model": 4, "num_hidden_layers": 1, "num_attention_heads": 1,
                   "ffn_dim": 8, "patch_length": 4, "patch_stride": 3,
                   "norm_type": "layernorm", "scaling": None}}}
    _, out = fit(tmp_path, path, epochs=1, encoder=encoder)
    panel, bands = val_rows(path)
    assert out["model"].forecast(panel, bands.val_idx[:4], 4).shape == (4, 2)


def test_nf_vanilla_transformer_encoder_trains(tmp_path, tmp_path_factory):
    pytest.importorskip("neuralforecast")
    path = build_panel(tmp_path)
    encoder = {"class": "dskit.pipeline.libs.neuralforecast:NFSequenceEncoder",
               "params": {"model": "VanillaTransformer", "output_size": 8,
                          "settings": {"hidden_size": 4, "n_head": 1, "conv_hidden_size": 8,
                                       "encoder_layers": 1, "decoder_layers": 1, "dropout": 0.1}}}
    univariate = {"columns": {"names": ["seq__a"]}, "window": WINDOW, "stride": STRIDE}
    _, out = fit(tmp_path, path, epochs=1, encoder=encoder, sequence=univariate)
    assert out["metrics"]["n_rows"] > 0


# --- windows, stride, the device panel ------------------------------------------------------


@pytest.fixture
def long_path(tmp_path):
    return build_panel(tmp_path, name="long", sizes={10: 1500}, gap={})


@pytest.mark.parametrize("window,stride", [(60, 1), (240, 1), (240, 6)])
def test_long_windows_forward_on_a_batch_of_eight_and_never_materialise_columns(
        tmp_path, long_path, monkeypatch, window, stride):
    need = (window - 1) * stride + 1
    loaded, seq_shapes = [], []
    real = DevicePanel.load.__func__

    def spy(cls, *args, **kw):
        loaded.append(real(cls, *args, **kw))
        seq_shapes.append(tuple(loaded[-1].seq_data.shape))
        return loaded[-1]

    monkeypatch.setattr(DevicePanel, "load", classmethod(spy))
    bounds = {"train_end_ms": T0 + 1470 * STEP, "val_start_ms": T0 + 1480 * STEP,
              "val_end_ms": T0 + 1496 * STEP, "cal_days": 1, "min_run": 1435}
    node = PanelTrain("m", params(
        long_path, assets=[10], asset_indicator=False, bounds=bounds, epochs=1,
        sequence={"columns": {"prefixes": ["seq__"]}, "window": window, "stride": stride},
        encoder={"arch": "gru", "width": 4, "arch_params": {"hidden_size": 4}}))
    out = node.run(ctx(tmp_path), {})
    assert need <= 1435
    rows, channels = seq_shapes[0]
    assert channels == 2 and rows <= 1500                    # (rows, C): no (N, L, C) block
    panel = ArrayPanel.open(long_path)
    rows = PanelBands(panel, [10], **bounds).val_idx[:8]
    dist = out["model"].forecast(panel, rows, 8)
    assert dist.shape == (8, 2)
    assert loaded[-1].seq_data is None                       # released after the forecast


def test_a_window_longer_than_min_run_is_refused(tmp_path, path):
    node = PanelTrain("m", params(
        path, sequence={"columns": {"prefixes": ["seq__"]}, "window": 20, "stride": 2}))
    with pytest.raises(ValueError, match="min_run"):
        node.run(ctx(tmp_path), {})


# --- causality, gaps, release order ---------------------------------------------------------


def test_forecast_at_t_ignores_everything_after_t(tmp_path, path):
    cut = 330
    after = build_panel(tmp_path, name="after", perturb_after=(10, cut))
    _, out = fit(tmp_path, path, epochs=2)
    a, b = ArrayPanel.open(path), ArrayPanel.open(after)
    rows = np.arange(250, cut + 1)                             # group 10, up to and including t
    base = out["model"].forecast(a, rows, 64)
    moved = out["model"].forecast(b, rows, 64)
    assert torch.equal(base.mu, moved.mu) and torch.equal(base.sigma, moved.sigma)
    later = out["model"].forecast(b, np.array([cut + 5]), 1)
    assert not torch.equal(later.mu, out["model"].forecast(a, np.array([cut + 5]), 1).mu)


def test_forecast_after_a_gap_uses_only_the_same_run(tmp_path, path):
    before = build_panel(tmp_path, name="before", perturb_after=None)
    cols = make_columns()
    gap_row = GAP[10]
    for name in ("f0", "f1", "f2", "seq__a", "seq__b"):
        cols[name][:gap_row] += 9.0                            # everything before the gap moves
    node = ArrayPanelWrite("w", {
        "time": "t", "group": "g", "reference": "ref", "label_prefix": "y_",
        "horizons": list(HORIZONS), "step_ms": STEP, "path": str(tmp_path / "moved")})
    node.run(ctx(tmp_path, "w"), {"columns": cols})
    _, out = fit(tmp_path, path, epochs=1)
    a, b = ArrayPanel.open(before), ArrayPanel.open(str(tmp_path / "moved"))
    safe = np.arange(gap_row + MIN_RUN - 1, gap_row + MIN_RUN + 10)
    one = out["model"].forecast(a, safe, 16)
    two = out["model"].forecast(b, safe, 16)
    assert torch.equal(one.mu, two.mu)
    near = np.arange(gap_row, gap_row + 3)                      # the window would bridge the gap
    with pytest.raises(ValueError, match="gap"):
        out["model"].forecast(a, near, 3)


def test_the_training_panel_is_released_before_the_forecaster_loads_its_own(
        tmp_path, path, monkeypatch):
    events = []
    real_load, real_release = DevicePanel.load.__func__, DevicePanel.release

    def load(cls, *args, **kw):
        events.append("load")
        return real_load(cls, *args, **kw)

    def release(self):
        events.append("release")
        return real_release(self)

    monkeypatch.setattr(DevicePanel, "load", classmethod(load))
    monkeypatch.setattr(DevicePanel, "release", release)
    _, out = fit(tmp_path, path, epochs=1)
    assert events == ["load", "release"]
    panel, bands = val_rows(path)
    out["model"].forecast(panel, bands.val_idx[:4], 4)
    assert events == ["load", "release", "load", "release"]


def test_a_failed_fit_still_releases_the_training_panel(tmp_path, path, monkeypatch):
    events = []
    real_release = DevicePanel.release
    monkeypatch.setattr(DevicePanel, "release",
                        lambda self: (events.append("release"), real_release(self))[1])
    node = PanelTrain("m", params(path, max_parameters=1))
    with pytest.raises(ValueError, match="parameter budget exceeded"):
        node.run(ctx(tmp_path), {})
    assert events == ["release"]


def test_forecaster_refuses_a_panel_without_the_trained_columns(tmp_path, path):
    _, out = fit(tmp_path, path, epochs=1)
    cols = make_columns()
    del cols["f1"]
    ArrayPanelWrite("w", {
        "time": "t", "group": "g", "reference": "ref", "label_prefix": "y_",
        "horizons": list(HORIZONS), "step_ms": STEP, "path": str(tmp_path / "thin")}).run(
            ctx(tmp_path, "w"), {"columns": cols})
    with pytest.raises(ValueError, match="f1"):
        out["model"].forecast(ArrayPanel.open(str(tmp_path / "thin")), np.array([300]), 1)


# --- the artifact, the budget, the grid -----------------------------------------------------


def test_save_load_forecast_round_trip(tmp_path, path):
    _, trained = fit(tmp_path, path, epochs=2)
    loader = PanelTrain("again", params(path), mode="load", artifact=trained["artifact_path"])
    restored = loader.run(ctx(tmp_path, "load"), {})
    assert restored["metrics"]["loaded"] == 1 and restored["signal"].loaded
    panel, bands = val_rows(path)
    rows = bands.val_idx[:20]
    a, b = trained["model"].forecast(panel, rows, 20), restored["model"].forecast(panel, rows, 20)
    assert torch.equal(a.mu, b.mu) and torch.equal(a.sigma, b.sigma) and torch.equal(a.logw, b.logw)


def test_a_load_without_the_saved_panel_state_is_refused(tmp_path, path):
    _, trained = fit(tmp_path, path, epochs=1)
    sidecar_path = trained["artifact_path"].replace(".pt", ".json")
    sidecar = json.load(open(sidecar_path))
    del sidecar["adapter_state"]
    json.dump(sidecar, open(sidecar_path, "w"))
    loader = PanelTrain("again", params(path), mode="load", artifact=trained["artifact_path"])
    with pytest.raises(ValueError, match="cannot load artifact"):
        loader.run(ctx(tmp_path, "load"), {})


def test_parameter_count_agrees_with_the_trained_module_and_the_budget_refuses(tmp_path, path):
    count = PanelTrain.parameter_count(params(path))
    _, out = fit(tmp_path, path, epochs=1, max_parameters=count)
    state = torch.load(out["artifact_path"], weights_only=True)
    assert sum(t.numel() for t in state.values()) == count
    with pytest.raises(ValueError, match="parameter budget exceeded"):
        PanelTrain("m", params(path, max_parameters=count - 1)).run(ctx(tmp_path, "over"), {})
    bigger = PanelTrain.parameter_count(params(path, trunk={"hidden": [32, 32], "activation": "relu"}))
    assert bigger > count


def test_forecaster_is_scored_by_the_common_grid(tmp_path, path):
    panel, bands = val_rows(path)
    rows = bands.val_idx
    y = torch.tensor(np.asarray(panel.labels[rows]), dtype=torch.float32)
    grid = CommonGrid(GRID)
    for head in (MIXTURE_TAIL, QUANTILE):
        _, out = fit(tmp_path, path, run=head["kind"], epochs=2, head=head)
        model = out["model"]
        assert isinstance(model, HorizonForecaster) and model.components == head.get("components", 1)
        summary = grid.evaluate(model, panel, rows, y)
        assert summary["n_rows"] == len(rows)
        assert all(np.isfinite(summary[k]).all() for k in ("crps", "tail_crps", "pinball"))
        assert summary["cdf_basis"] == ("parametric" if head["kind"] == "mixture" else "extrapolated")


def test_the_grid_refuses_a_forecaster_trained_on_other_wings_or_bounds(tmp_path, path):
    panel, bands = val_rows(path)
    y = torch.tensor(np.asarray(panel.labels[bands.val_idx[:6]]), dtype=torch.float32)
    narrow = {**MIXTURE_TAIL, "tail_intervals": [[-6.0, -2.0], [2.0, 6.0]]}
    _, wings = fit(tmp_path, path, run="wings", epochs=1, head=narrow)
    with pytest.raises(ValueError, match="training_tail"):
        CommonGrid(GRID).evaluate(wings["model"], panel, bands.val_idx[:6], y)
    _, knots = fit(tmp_path, path, run="knots", epochs=1, head={**QUANTILE, "bounds": [-6.0, 6.0]})
    with pytest.raises(ValueError, match="bounds"):
        CommonGrid(GRID).evaluate(knots["model"], panel, bands.val_idx[:6], y)
    _, few = fit(tmp_path, path, run="few", epochs=1,
                 head={**QUANTILE, "quantiles": [0.2, 0.5, 0.8]})
    with pytest.raises(ValueError, match="levels"):
        CommonGrid(GRID).evaluate(few["model"], panel, bands.val_idx[:6], y)


def test_adapter_is_a_complete_torch_adapter_that_does_not_apply_a_loss():
    assert issubclass(PanelAdapter, __import__("dskit.pipeline.libs.torch", fromlist=["x"]).TorchAdapter)
    assert PanelAdapter.requires_features is False and PanelAdapter.applies_loss is False
    assert PanelTrain._ADAPTER is PanelAdapter
    assert pt.__all__ and all(not n.startswith("_") for n in pt.__all__)


# --- a whole document, and a device ---------------------------------------------------------


def test_a_document_runs_the_trainer_with_no_rows_and_split_refs_in_bounds(tmp_path, path):
    from dskit.pipeline.document import PipelineDocument
    from dskit.pipeline.driver import run_document
    from dskit.pipeline.planner import plan

    refs = {"train_end_ms": "$splits.train_end_ms", "val_start_ms": "$splits.val_start_ms",
            "val_end_ms": "$splits.val_end_ms", "cal_days": 2, "min_run": MIN_RUN}
    doc = PipelineDocument.from_obj({
        "name": "panel-torch-doc",
        "pipeline": {"model": {"uses": "dskit.pipeline.libs.panel_torch:PanelTrain",
                               "params": params(path, bounds=refs, epochs=2)}},
        "splits": {"kind": "time", "train_end_ms": BOUNDS["train_end_ms"],
                   "val_start_ms": BOUNDS["val_start_ms"], "val_end_ms": BOUNDS["val_end_ms"],
                   "test_end_ms": BOUNDS["val_end_ms"] + 1000},
        "outputs": {"run_root": str(tmp_path / "runs")},
    })
    assert plan(doc).role_of("model") == "train"
    result = run_document(doc, asof="2026-01-01")
    assert result.state == "ran", (result.state, result.error)
    assert isinstance(result.outputs["model"]["model"], TorchHorizonForecaster)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="no cuda device")
@pytest.mark.parametrize("head", [MIXTURE_TAIL, QUANTILE])
def test_a_cuda_fit_forecasts_on_the_device(tmp_path, path, head):
    _, out = fit(tmp_path, path, epochs=1, device="cuda", head=head)
    panel, bands = val_rows(path)
    dist = out["model"].forecast(panel, bands.val_idx[:6], 6)
    assert dist.device.type == "cuda" and dist.shape == (6, 2)


# --- row_stride: thin the FIT band by bar index -----------------------------------------------


def fit_index(path, **over):
    """The fit rows the adapter hands the trainer for these knobs, plus the adapter's other bands."""
    knobs = params(path, **over)
    adapter = PanelAdapter(knobs)
    try:
        fit_rows = adapter.prepare(None, knobs, where="fit").payload[1].numpy()
        cal_rows = adapter.prepare(None, knobs, where="cal").payload[1].numpy()
        scaler_mean = list(adapter.state.mean)
    finally:
        adapter.release()
    return fit_rows, cal_rows, scaler_mean


def hand_bars(path, rows):
    """Bar index of each row, restated from the construction in ``make_columns`` (not the node)."""
    panel = ArrayPanel.open(path)
    return np.asarray(panel.time)[rows] // STEP


def test_default_row_stride_reproduces_the_existing_fit_rows(path):
    _, bands = val_rows(path)
    omitted, _, _ = fit_index(path)
    explicit, _, _ = fit_index(path, row_stride=pt.DEFAULT_ROW_STRIDE, stride_offset=pt.DEFAULT_STRIDE_OFFSET)
    assert pt.DEFAULT_ROW_STRIDE == 1 and pt.DEFAULT_STRIDE_OFFSET == 0
    assert np.array_equal(omitted, bands.fit_idx)
    assert np.array_equal(explicit, bands.fit_idx)


@pytest.mark.parametrize("stride,offset", [(2, 0), (2, 1), (3, 0), (3, 2), (5, 4), (7, 3)])
def test_a_stride_keeps_exactly_the_rows_on_its_bar_phase(path, stride, offset):
    _, bands = val_rows(path)
    kept, _, _ = fit_index(path, row_stride=stride, stride_offset=offset)
    all_bars = hand_bars(path, bands.fit_idx)
    expected = bands.fit_idx[all_bars % stride == offset]
    assert len(expected) and len(expected) < len(bands.fit_idx)
    assert np.array_equal(kept, expected)
    assert np.all(hand_bars(path, kept) % stride == offset)


def test_a_stride_of_three_keeps_hand_listed_instants_of_the_first_group(path):
    kept, _, _ = fit_index(path, assets=[10], asset_indicator=False, row_stride=3, stride_offset=0)
    first_bar = T0 // STEP
    bars = hand_bars(path, kept)
    # group 10 fit rows start at min_run - 1 = 14 and run (gap included) to a purged tail; the first
    # kept bar is the first multiple of 3 at or after first_bar + 14
    start = first_bar + MIN_RUN - 1
    assert bars[0] == start + (-start) % 3
    assert set(np.diff(bars[bars < first_bar + 200])) == {3}


def test_the_phase_is_time_based_so_a_gap_does_not_shift_it_and_groups_agree(path):
    kept, _, _ = fit_index(path, row_stride=3, stride_offset=1)
    panel = ArrayPanel.open(path)
    group, bars = np.asarray(panel.group)[kept], hand_bars(path, kept)
    assert np.all(bars % 3 == 1)
    after_gap = bars[(group == 10) & (np.asarray(panel.time)[kept] >= T0 + 204 * STEP)]
    assert len(after_gap) and np.all(after_gap % 3 == 1)       # row-index phase would drift by 4 bars
    _, bands = val_rows(path)
    every = hand_bars(path, bands.fit_idx[np.asarray(panel.group)[bands.fit_idx] == 20])
    assert set(bars[group == 20]) == {b for b in every if b % 3 == 1}   # pooled groups keep the same instants


def test_calibration_and_validation_rows_and_the_scaler_do_not_depend_on_the_stride(tmp_path, path):
    _, bands = val_rows(path)
    _, cal_1, mean_1 = fit_index(path)
    for stride in (2, 5):
        _, cal_k, mean_k = fit_index(path, row_stride=stride, stride_offset=1)
        assert np.array_equal(cal_k, cal_1) and np.array_equal(cal_k, bands.cal_idx)
        assert mean_k == mean_1                                  # the scaler sees the whole fit band
    _, plain = fit(tmp_path, path, run="plain", epochs=1)
    _, thin = fit(tmp_path, path, run="thin", epochs=1, row_stride=4)
    assert thin["metrics"]["n_val_rows"] == plain["metrics"]["n_val_rows"] == len(bands.cal_idx)
    assert 0 < thin["metrics"]["n_rows"] < plain["metrics"]["n_rows"]
    _, bands_after = val_rows(path)
    assert bands_after.rows_sha256() == bands.rows_sha256()


def test_the_default_stride_fit_is_byte_identical_to_an_explicit_stride_of_one(tmp_path, path):
    _, plain = fit(tmp_path, path, run="plain", epochs=2)
    _, one = fit(tmp_path, path, run="one", epochs=2, row_stride=1, stride_offset=0)
    a = torch.load(plain["artifact_path"], weights_only=True)
    b = torch.load(one["artifact_path"], weights_only=True)
    assert all(torch.equal(a[k], b[k]) for k in a)
    assert plain["metrics"]["n_rows"] == one["metrics"]["n_rows"]


def test_a_thinned_row_still_gathers_its_full_window(path):
    knobs = params(path, row_stride=4)
    adapter = PanelAdapter(knobs)
    try:
        batches = adapter.prepare(None, knobs, where="fit")
        thin = adapter.select(batches, None)
        full_batches = PanelAdapter(params(path))
        try:
            full = full_batches.select(full_batches.prepare(None, params(path), where="fit"), None)
            keep = np.isin(full_batches.prepare(None, params(path), where="fit").payload[1].numpy(),
                           batches.payload[1].numpy())
            assert thin.seq.shape[1:] == full.seq.shape[1:]
            assert torch.equal(thin.seq, full.seq[torch.as_tensor(keep)])
        finally:
            full_batches.release()
    finally:
        adapter.release()


@pytest.mark.parametrize("over,fragment", [
    ({"row_stride": 0}, "row_stride"),
    ({"row_stride": -2}, "row_stride"),
    ({"row_stride": 1.5}, "row_stride"),
    ({"row_stride": True}, "row_stride"),
    ({"row_stride": "2"}, "row_stride"),
    ({"stride_offset": -1}, "stride_offset"),
    ({"stride_offset": 1}, "stride_offset"),                    # default stride 1: only offset 0
    ({"row_stride": 3, "stride_offset": 3}, "stride_offset"),
    ({"row_stride": 3, "stride_offset": 1.5}, "stride_offset"),
    ({"row_stride": 3, "stride_offset": False}, "stride_offset"),
])
def test_bad_row_stride_knobs_are_refused_at_plan(path, over, fragment):
    problems = PanelTrain.validate_params(params(path, **over))
    assert any(fragment in p for p in problems), problems


def test_good_row_stride_knobs_and_node_refs_pass_plan(path):
    assert PanelTrain.validate_params(params(path, row_stride=4, stride_offset=3)) == []
    assert PanelTrain.validate_params(params(path, row_stride="$s.stride", stride_offset="$s.off")) == []


def test_a_stride_that_leaves_no_fit_rows_is_refused_by_name(path):
    knobs = params(path, row_stride=10_000_000, stride_offset=1)
    adapter = PanelAdapter(knobs)
    try:
        with pytest.raises(ValueError, match="row_stride.*no fit rows"):
            adapter.prepare(None, knobs, where="fit")
    finally:
        adapter.release()
