"""Focused adapter/capacity regression tests for ADR-0238."""
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("torch")
pytest.importorskip("sklearn")

from dskit.pipeline.libs.predictive_cdf import TorchCDF


def model(backend="hf", **extra):
    pytest.importorskip("transformers" if backend == "hf" else "neuralforecast")
    adapters = {
        "hf": {"class": "dskit.pipeline.libs.transformers:HFSequenceEncoder",
               "params": {"model": "PatchTST", "config": {
                   "d_model": 4, "num_hidden_layers": 1, "num_attention_heads": 1,
                   "ffn_dim": 8, "patch_length": 4, "patch_stride": 3,
                   "norm_type": "layernorm", "scaling": None}}},
        "nf": {"class": "dskit.pipeline.libs.neuralforecast:NFSequenceEncoder",
               "params": {"model": "VanillaTransformer", "output_size": 8,
                          "settings": {"hidden_size": 4, "n_head": 1,
                          "conv_hidden_size": 8, "encoder_layers": 1,
                          "decoder_layers": 1, "dropout": 0.1}}},
    }
    return TorchCDF(encoder={"kind": "external",
        "sequence_indices": [[i] for i in range(22)], "context_indices": [22, 23],
        "adapter": adapters[backend]},
        losses=[{"kind": "crps", "weight": 1.0}, {"kind": "tail_crps", "weight": 1.0}],
        hidden=[2], epochs=2, batch_size=8, seeds=[11], device="cpu", **extra)


@pytest.mark.parametrize("backend", ["hf", "nf"])
def test_external_sequence_encoder_preserves_rows_and_gradients(backend):
    import torch
    torch.set_num_threads(1)
    m = model(backend)
    module = m._build_module(24).eval()
    x = torch.randn(3, 24, requires_grad=True)
    y = module(x)
    assert y.shape == (3, 9)
    torch.testing.assert_close(y, torch.cat([module(x[i:i+1]) for i in range(3)]),
                               atol=1e-5, rtol=1e-5)
    y.square().sum().backward()
    assert torch.isfinite(x.grad).all()
    assert x.grad[:, :4].abs().sum() > 0  # Older patches cannot be silently dropped.
    assert x.grad[:, 22:].abs().sum() > 0

def test_capacity_refuses_before_optimizer_and_constant_mask_is_training_only(tmp_path):
    from dskit.pipeline.libs.predictive_cdf import MixtureMLPCDF
    rng = np.random.default_rng(4)
    x = rng.normal(size=(24, 3))
    x[:, 2] = 7
    y = rng.normal(size=24)
    m = MixtureMLPCDF(hidden=[2], epochs=2, seeds=[11], device="cpu",
                      max_parameters=1, mask_constant_features=True, training_telemetry=True)
    with pytest.raises(ValueError, match="parameter budget"):
        m.fit(x, y, x[:4], y[:4])
    m = MixtureMLPCDF(hidden=[2], epochs=2, seeds=[11], device="cpu",
                      max_parameters=100, mask_constant_features=True, training_telemetry=True)
    m.fit(x, y, x[:4], y[:4])
    moved = x[:4].copy()
    moved[:, 2] = 100
    np.testing.assert_array_equal(m.curve(x[:4]).cdf([0.]), m.curve(moved).cdf([0.]))
    assert m.training_diagnostics[0]["optimizer_steps"] == 2
    assert m.training_diagnostics[0]["parameter_delta_l2"] > 0
    assert np.isfinite(m.training_diagnostics[0]["epoch_zero_monitor_loss"])
    m.save_checkpoint(tmp_path / "model")
    restored = MixtureMLPCDF.load_checkpoint(tmp_path / "model")
    np.testing.assert_array_equal(m.curve(moved).cdf([0.]), restored.curve(moved).cdf([0.]))


def test_atomic_fit_store_refuses_changed_identity_and_corruption(tmp_path):
    from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, IntegrityError
    store = AtomicFitStore(tmp_path / "fit", {"config": "a"})
    store.publish({"x.json": b'{"v": 1}'})
    assert store.verify()["identity"] == store.identity
    with pytest.raises(IntegrityError):
        AtomicFitStore(tmp_path / "fit", {"config": "b"}).verify()
    (tmp_path / "fit" / "x.json").write_text("changed")
    with pytest.raises(IntegrityError):
        store.verify()


def test_admission_and_forecast_pairing():
    import pandas as pd
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment, IntegrityError
    frame = pd.DataFrame({"day": ["2020-01-01", "2020-01-02", "2020-02-01"],
                          "end": ["2020-01-31", "2020-02-01", "2020-03-01"]})
    assert CDFExperiment.disjoint_count(frame, "day", "end") == 2
    CDFExperiment.check_pairing(frame, frame.copy(), ["day"])
    with pytest.raises(IntegrityError):
        CDFExperiment.check_pairing(frame, frame.iloc[:2], ["day"])


@pytest.mark.parametrize("backend", ["hf", "nf"])
def test_external_fit_checkpoint_roundtrip(tmp_path, backend):
    from dskit.pipeline.libs.predictive_cdf import TorchCDF
    m = model(backend, training_telemetry=True, mask_constant_features=True)
    rng = np.random.default_rng(7)
    x = rng.normal(size=(16, 24))
    y = rng.normal(size=16)
    def context(n, prefix):
        return [{"identity": [prefix+str(i)], "thresholds": [-2.5,-.5,.5,2.5],
                 "weights": [.25]*4, "intervals": [[-2.5,-.5],[.5,2.5]]}
                for i in range(n)]
    m.fit_decision_context(context(16,"f"),context(4,"c"))
    m.fit(x,y,x[:4],y[:4])
    m.save_checkpoint(tmp_path / "fit")
    restored = TorchCDF.load_checkpoint(tmp_path / "fit")
    np.testing.assert_allclose(m.curve(x[:4]).cdf([-.5,.5]), restored.curve(x[:4]).cdf([-.5,.5]),
                               atol=1e-7,rtol=1e-6)


@pytest.fixture
def experiment_config(tmp_path):
    import json
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    pytest.importorskip("transformers")
    from pathlib import Path
    from dskit.pipeline.libs.cdf_experiment import AtomicFitStore
    cfg = json.loads(Path("children/index_options/configs/advanced-cdf-zoo.json").read_text())
    cfg["source_hashes"] = {}
    cfg["dependencies"] = {}
    cfg["tickers"] = ["AAA", "BBB"]
    cfg["arms"] = {"base": cfg["arms"]["base"]}
    cfg["candidates"] = cfg["candidates"][:1]
    cfg["candidates"][0]["adapters"]["pooled"] = cfg["candidates"][0]["adapters"]["unpooled"][-1:]
    cfg["training"].update(epochs=2,patience=None)
    cfg["capacity"]["pooled"].update(device="cpu",per_row=1000,head_per_row=1000)
    cfg["arms"]["base"].update(min_fit=2,min_cal=1)
    cfg["date_upper_exclusive"] = cfg["end_upper_exclusive"] = "2002-01-01"
    folds = tmp_path/"folds.jsonl"
    folds.write_text(json.dumps({"fold":1,"role":"scored","train_start":"2001-01-01",
        "train_end":"2001-01-25","val_start":"2001-02-01","val_end":"2001-02-03"})+"\n")
    cfg["study"]["fold_table"].update(path=str(folds),sha256=AtomicFitStore.file_hash(folds),
        holdout_start="2002-01-01",cal_n=5)
    cfg["output"] = str(tmp_path/"out")
    cfg["panel"] = str(tmp_path/"panel.parquet")
    rng = np.random.default_rng(100)
    rows = []
    for ticker in cfg["tickers"]:
        for date in list(pd.date_range("2001-01-01",periods=25))+list(pd.date_range("2001-02-01",periods=3)):
            row = {name:float(rng.normal()) for name in cfg["arms"]["base"]["features"]}
            row.update(symbol=ticker,quote_date=str(date.date()),expiry=str((date+pd.Timedelta(days=1)).date()),
                       settlement_date=str((date+pd.Timedelta(days=1)).date()),actual_calendar_dte=1.,
                       reference_scale=.03,terminal_return=float(rng.normal()*.03))
            rows.append(row)
    pd.DataFrame(rows).to_parquet(cfg["panel"])
    cfg["panel_sha256"] = AtomicFitStore.file_hash(cfg["panel"])
    return cfg


def test_pooled_crash_after_first_shard_resumes_checkpoint_without_refit(experiment_config, monkeypatch):
    from pathlib import Path
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment, AtomicFitStore
    exp = CDFExperiment(experiment_config)
    original = AtomicFitStore.publish
    def crash(self, files):
        result = original(self,files)
        if self.path.name == "AAA" and self.path.parent.name == "scores":
            raise KeyboardInterrupt("simulated process loss after one ticker")
        return result
    monkeypatch.setattr(AtomicFitStore,"publish",crash)
    with pytest.raises(KeyboardInterrupt):
        exp.run("pooled")
    monkeypatch.setattr(AtomicFitStore,"publish",original)
    def no_refit(*args,**kwargs): raise AssertionError("completed fit was rerun")
    monkeypatch.setattr(TorchCDF,"fit",no_refit)
    exp.run("pooled")
    root = Path(experiment_config["output"])/"pooled/base/PatchTST/1/pooled"
    assert (root/"completed/complete.json").exists()
    assert (root/"scores/BBB/complete.json").exists()
    exp.run("pooled")  # A fully complete retry validates and retains both shards.


def test_holdout_predicate_refused_before_panel_read(experiment_config):
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment, IntegrityError
    experiment_config["date_upper_exclusive"] = "2003-01-01"
    with pytest.raises(IntegrityError,match="holdout"):
        CDFExperiment(experiment_config)


def _interrupt_after_one_score(config, monkeypatch):
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment, AtomicFitStore
    from pathlib import Path
    exp = CDFExperiment(config)
    original = AtomicFitStore.publish
    def crash(self, files):
        result = original(self, files)
        if self.path.name == "AAA" and self.path.parent.name == "scores":
            raise KeyboardInterrupt("interrupted after publishing first shard")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(AtomicFitStore, "publish", crash)
        with pytest.raises(KeyboardInterrupt):
            exp.run("pooled")
    return exp, Path(config["output"])/"pooled/base/PatchTST/1/pooled"


@pytest.mark.parametrize("manifest", ["fit/complete.json", "scores/AAA/complete.json"])
@pytest.mark.parametrize("payload", ["{", "[]", '{"identity":"x"}'])
def test_partial_resume_corrupt_manifest_halts_not_skips(experiment_config, monkeypatch, manifest, payload):
    from dskit.pipeline.libs.cdf_experiment import IntegrityError
    exp, root = _interrupt_after_one_score(experiment_config, monkeypatch)
    (root/manifest).write_text(payload)
    with pytest.raises(IntegrityError):
        exp.run("pooled")
    assert not (root/"skipped").exists()


def test_orphan_pooled_score_refuses_before_refit(experiment_config, monkeypatch):
    import shutil
    from dskit.pipeline.libs.cdf_experiment import IntegrityError
    exp, root = _interrupt_after_one_score(experiment_config, monkeypatch)
    shutil.rmtree(root/"fit")
    called = []
    def no_refit(*args, **kwargs):
        called.append(True)
        raise AssertionError("refit attempted")
    monkeypatch.setattr(TorchCDF, "fit", no_refit)
    with pytest.raises(IntegrityError):
        exp.run("pooled")
    assert called == []
    assert not (root/"skipped").exists()


def test_skip_marker_does_not_hide_corrupt_published_fit(experiment_config, monkeypatch):
    from pathlib import Path
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment, AtomicFitStore, IntegrityError
    exp = CDFExperiment(experiment_config)
    original = AtomicFitStore.publish
    def fail_after_score(self, files):
        result = original(self, files)
        if self.path.name == "AAA" and self.path.parent.name == "scores":
            raise ValueError("model-specific prediction failure")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(AtomicFitStore,"publish",fail_after_score)
        exp.run("pooled")
    root = Path(experiment_config["output"])/"pooled/base/PatchTST/1/pooled"
    assert (root/"skipped").exists()
    (root/"fit/model/weights.pt").write_bytes(b"corruption")
    with pytest.raises(IntegrityError):
        exp.run("pooled")


def test_conflicting_terminal_states_refuse(experiment_config, monkeypatch):
    from dskit.pipeline.libs.cdf_experiment import IntegrityError
    exp, root = _interrupt_after_one_score(experiment_config, monkeypatch)
    (root/"skipped").mkdir()
    (root/"completed").mkdir()
    with pytest.raises(IntegrityError, match="conflicting"):
        exp.run("pooled")

@pytest.mark.parametrize("control", [{"max_parameters":1}, {"mask_constant_features":True},
                                     {"training_telemetry":True}])
def test_set_encoder_refuses_unsupported_mixture_controls(control):
    from dskit.pipeline.libs.predictive_cdf import SetMixtureCDF
    with pytest.raises(ValueError, match="unsupported"):
        SetMixtureCDF(nodes=1,node_features=1,tensor_indices=[0],mask_indices=[1],
                      context_indices=[2],hidden=[2],device="cpu",**control)


def test_set_parameter_count_refuses_unrelated_mlp_count():
    from dskit.pipeline.libs.predictive_cdf import SetMixtureCDF
    m = SetMixtureCDF(nodes=1,node_features=1,tensor_indices=[0],mask_indices=[1],
                      context_indices=[2],hidden=[2],device="cpu")
    with pytest.raises(ValueError, match="unsupported"):
        m.parameter_count(3)


@pytest.mark.parametrize("name, settings", [
    ("StudentMixtureMLPCDF", {"degrees":5}),
    ("DecisionWeightedMixtureMLPCDF", {"decision_weight":2}),
    ("SetMixtureCDF", {"nodes":1,"node_features":1,"tensor_indices":[0],
                       "mask_indices":[1],"context_indices":[2]})])
def test_unsupported_checkpoints_refuse_before_io(tmp_path, name, settings):
    from dskit.pipeline.libs import predictive_cdf
    cls = getattr(predictive_cdf,name)
    m = cls(device="cpu",**settings)
    with pytest.raises(ValueError, match="unsupported"):
        m.save_checkpoint(tmp_path/"save")
    with pytest.raises(ValueError, match="unsupported"):
        cls.load_checkpoint(tmp_path/"absent")
    assert list(tmp_path.iterdir()) == []


def test_unfitted_checkpoint_and_nonsequence_validation_refuse(tmp_path):
    from dskit.pipeline.libs.predictive_cdf import MixtureMLPCDF
    with pytest.raises(ValueError, match="fitted"):
        MixtureMLPCDF(device="cpu").save_checkpoint(tmp_path/"new")
    assert list(tmp_path.iterdir()) == []
    m = TorchCDF(encoder={"kind":"mlp"},losses=[{"kind":"crps","weight":1.},
                   {"kind":"tail_crps","weight":1.}],device="cpu")
    with pytest.raises(ValueError,match="sequence"):
        m.validate_encoder(3)


@pytest.mark.parametrize("band_index", [0, 20, -1])
@pytest.mark.parametrize("column,value", [("expiry", None), ("expiry", ""), ("quote_date", None), ("settlement_date", None),
    ("actual_calendar_dte", float("nan")), ("terminal_return", float("nan"))])
def test_bad_panel_contract_halts_all_bands(experiment_config, band_index, column, value):
    import pandas as pd
    from pathlib import Path
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment, AtomicFitStore, IntegrityError
    frame = pd.read_parquet(experiment_config["panel"])
    frame.loc[frame.index[band_index], column] = value
    frame.to_parquet(experiment_config["panel"])
    experiment_config["panel_sha256"] = AtomicFitStore.file_hash(experiment_config["panel"])
    with pytest.raises(IntegrityError):
        CDFExperiment(experiment_config).run("pooled")
    assert not list(Path(experiment_config["output"]).rglob("reason.json"))


@pytest.mark.parametrize("value", [None, "", "  "])
def test_pairing_rejects_shared_missing_identity(value):
    import pandas as pd
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment, IntegrityError
    frame = pd.DataFrame({"ticker": ["A"], "expiry": [value]})
    with pytest.raises(IntegrityError):
        CDFExperiment.check_pairing(frame, frame.copy(), ["ticker", "expiry"])


def test_resume_rejects_hash_valid_missing_forecast_identity(experiment_config, monkeypatch):
    import json
    import pandas as pd
    from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, IntegrityError
    exp, root = _interrupt_after_one_score(experiment_config, monkeypatch)
    path = root/"scores/AAA"
    scores = pd.read_parquet(path/"scores.parquet")
    scores.loc[0, "expiry"] = None
    scores.to_parquet(path/"scores.parquet", index=False)
    manifest = json.loads((path/"complete.json").read_text())
    manifest["files"]["scores.parquet"] = AtomicFitStore.file_hash(path/"scores.parquet")
    (path/"complete.json").write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError):
        exp.run("pooled")
    assert not (root/"skipped").exists()


def test_pairing_missing_schema_is_integrity_error():
    import pandas as pd
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment, IntegrityError
    with pytest.raises(IntegrityError):
        CDFExperiment.check_pairing(pd.DataFrame({"a":[1]}), pd.DataFrame({"a":[1]}), ["b"])


def test_missing_predicate_statistics_refuses_without_row_read(experiment_config, monkeypatch):
    import pandas as pd
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment, AtomicFitStore, IntegrityError
    frame = pd.read_parquet(experiment_config["panel"])
    frame.to_parquet(experiment_config["panel"], write_statistics=False)
    experiment_config["panel_sha256"] = AtomicFitStore.file_hash(experiment_config["panel"])
    def no_read(*args, **kwargs):
        raise AssertionError("rows read before metadata gate")
    monkeypatch.setattr(pd, "read_parquet", no_read)
    with pytest.raises(IntegrityError, match="temporal predicate"):
        CDFExperiment(experiment_config).load_panel()


@pytest.mark.parametrize("kind", ["declared_other_code", "loaded_other_code", "missing_anchor"])
def test_source_pins_bind_executing_tree_before_panel_access(experiment_config, tmp_path, monkeypatch, kind):
    import sys
    from pathlib import Path
    from dskit.pipeline.libs import cdf_experiment as runner
    from dskit.pipeline.libs import predictive_cdf as owner
    declared = tmp_path / "declared"
    runner_copy = declared / "dskit/pipeline/libs/cdf_experiment.py"
    owner_copy = declared / "dskit/pipeline/libs/predictive_cdf.py"
    runner_copy.parent.mkdir(parents=True)
    runner_copy.write_bytes(Path(runner.__file__).read_bytes())
    owner_copy.write_bytes(Path(owner.__file__).read_bytes())
    if kind == "declared_other_code":
        owner_copy.write_text("# An intact older checkout, not the executing implementation.\n")
    experiment_config["source_hashes"] = {
        str(p): runner.AtomicFitStore.file_hash(p) for p in [runner_copy, owner_copy]
    }
    if kind == "loaded_other_code":
        loaded = tmp_path / "third/dskit/pipeline/libs/predictive_cdf.py"
        loaded.parent.mkdir(parents=True)
        loaded.write_text("# A different imported checkout.\n")
        monkeypatch.setattr(sys.modules[owner.__name__], "__file__", str(loaded))
    if kind == "missing_anchor":
        del experiment_config["source_hashes"][str(runner_copy)]
    with pytest.raises(runner.IntegrityError, match="executing source|runner source"):
        runner.CDFExperiment(experiment_config)
    assert not Path(experiment_config["output"]).exists()


def test_identical_relocated_source_pins_are_compatible(experiment_config, tmp_path):
    from pathlib import Path
    from dskit.pipeline.libs import cdf_experiment as runner
    from dskit.pipeline.libs import predictive_cdf as owner
    declared = tmp_path / "old_checkout"
    paths = []
    from dskit.pipeline.libs import parquet as reader
    for module in [runner, owner, reader]:
        path = declared / Path(*module.__name__.split(".")).with_suffix(".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Path(module.__file__).read_bytes())
        paths.append(path)
    experiment_config["source_hashes"] = {
        str(p): runner.AtomicFitStore.file_hash(p) for p in paths
    }
    runner.CDFExperiment(experiment_config)

@pytest.mark.parametrize("kind", ["duplicate_anchor", "outside_root", "missing_file", "lazy_owner"])
def test_source_pin_resolution_refuses_ambiguous_or_unavailable_owners(tmp_path, monkeypatch, kind):
    from types import SimpleNamespace
    from dskit.pipeline.libs import cdf_experiment as runner
    from pathlib import Path
    root = Path(runner.__file__).resolve().parents[3]
    anchor = tmp_path / "declared/dskit/pipeline/libs/cdf_experiment.py"
    anchor.parent.mkdir(parents=True)
    anchor.write_bytes(Path(runner.__file__).read_bytes())
    pins = {str(anchor): runner.AtomicFitStore.file_hash(anchor)}
    if kind == "duplicate_anchor":
        other = tmp_path / "second/dskit/pipeline/libs/cdf_experiment.py"
        pins[str(other)] = pins[str(anchor)]
    elif kind == "outside_root":
        other = tmp_path / "outside.py"
        other.write_text("# outside declared project\n")
        pins[str(other)] = runner.AtomicFitStore.file_hash(other)
    elif kind == "missing_file":
        pins[str(anchor.parent / "missing.py")] = "0" * 64
    else:
        import sys
        name = "dskit.pipeline.libs.torch_ts"
        other = anchor.parent / "torch_ts.py"
        other.write_bytes((root / "dskit/pipeline/libs/torch_ts.py").read_bytes())
        pins[str(other)] = runner.AtomicFitStore.file_hash(other)
        different = tmp_path / "different.py"
        different.write_text("# another import owner\n")
        monkeypatch.delitem(sys.modules, name, raising=False)
        find_spec = runner.importlib.util.find_spec
        monkeypatch.setattr(runner.importlib.util, "find_spec",
            lambda requested: SimpleNamespace(origin=str(different)) if requested == name else find_spec(requested))
    with pytest.raises(runner.IntegrityError, match="runner source|executing source"):
        runner.CDFExperiment._verify_source_pins(pins)

def test_source_pins_check_loaded_package_initializer(tmp_path, monkeypatch):
    import dskit
    from pathlib import Path
    from dskit.pipeline.libs import cdf_experiment as runner
    anchor = Path(runner.__file__)
    initializer = Path(dskit.__file__)
    pins = {str(p): runner.AtomicFitStore.file_hash(p) for p in (anchor, initializer)}
    other = tmp_path / "__init__.py"
    other.write_text("# different loaded package\n")
    monkeypatch.setattr(dskit, "__file__", str(other))
    with pytest.raises(runner.IntegrityError, match="executing source import owner"):
        runner.CDFExperiment._verify_source_pins(pins)


def test_source_pins_require_bounded_reader_owner():
    from pathlib import Path
    from dskit.pipeline.libs import cdf_experiment as runner
    pins = {str(Path(runner.__file__)): runner.AtomicFitStore.file_hash(runner.__file__)}
    with pytest.raises(runner.IntegrityError, match="bounded reader"):
        runner.CDFExperiment._verify_source_pins(pins)


def test_experiment_bounded_read_preserves_only_mature_rows(experiment_config, monkeypatch):
    import pandas as pd
    import pyarrow.dataset as ds
    from dskit.pipeline.libs import cdf_experiment as runner
    frame = pd.read_parquet(experiment_config["panel"])
    original_count = len(frame)
    excluded = frame.iloc[:2].copy()
    excluded.iloc[0, excluded.columns.get_loc("quote_date")] = "2002-01-01"
    excluded.iloc[1, excluded.columns.get_loc("settlement_date")] = "2002-01-01"
    pd.concat([frame, excluded]).to_parquet(experiment_config["panel"])
    experiment_config["panel_sha256"] = runner.AtomicFitStore.file_hash(experiment_config["panel"])
    assert len(runner.CDFExperiment(experiment_config).load_panel()) == original_count


def test_bounded_reader_source_drift_invalidates_experiment_pin(experiment_config, tmp_path):
    from pathlib import Path
    from dskit.pipeline.libs import cdf_experiment as runner, parquet as reader
    root = tmp_path / "declared"
    pins = {}
    for module in (runner, reader):
        target = root / Path(*module.__name__.split(".")).with_suffix(".py")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(module.__file__).read_bytes())
        if module is reader:
            target.write_bytes(target.read_bytes() + b"\n# other reader\n")
        pins[str(target)] = runner.AtomicFitStore.file_hash(target)
    experiment_config["source_hashes"] = pins
    with pytest.raises(runner.IntegrityError, match="executing source"):
        runner.CDFExperiment(experiment_config)
