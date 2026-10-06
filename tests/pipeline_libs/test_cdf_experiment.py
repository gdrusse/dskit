"""Focused adapter/capacity regression tests for ADR-0238."""
import numpy as np
import pytest

from dskit.pipeline.libs.predictive_cdf import TorchCDF


def model(backend="hf", **extra):
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
    import torch
    from dskit.pipeline.libs.predictive_cdf import MixtureMLPCDF
    rng = np.random.default_rng(4)
    x = rng.normal(size=(24, 3)); x[:, 2] = 7
    y = rng.normal(size=24)
    m = MixtureMLPCDF(hidden=[2], epochs=2, seeds=[11], device="cpu",
                      max_parameters=1, mask_constant_features=True, training_telemetry=True)
    with pytest.raises(ValueError, match="parameter budget"):
        m.fit(x, y, x[:4], y[:4])
    m = MixtureMLPCDF(hidden=[2], epochs=2, seeds=[11], device="cpu",
                      max_parameters=100, mask_constant_features=True, training_telemetry=True)
    m.fit(x, y, x[:4], y[:4])
    moved = x[:4].copy(); moved[:, 2] = 100
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
    x = rng.normal(size=(16, 24)); y = rng.normal(size=16)
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
    import copy, json, pandas as pd
    from pathlib import Path
    from dskit.pipeline.libs.cdf_experiment import AtomicFitStore
    cfg = json.loads(Path("children/index_options/configs/advanced-cdf-zoo.json").read_text())
    cfg["source_hashes"] = {}; cfg["dependencies"] = {}
    cfg["tickers"] = ["AAA", "BBB"]; cfg["arms"] = {"base": cfg["arms"]["base"]}
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
