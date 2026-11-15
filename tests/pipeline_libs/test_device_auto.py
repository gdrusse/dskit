"""``device="auto"`` for the torch mixture estimators: resolution, strictness, identity, restore.

Availability is mocked, so every branch of the resolver runs on any machine; nothing
here needs CUDA or MPS hardware.
"""
import json

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")
pytest.importorskip("sklearn")

from dskit.pipeline.libs import predictive_cdf
from dskit.pipeline.libs.predictive_cdf import (
    AUTO_DEVICE, DEVICE_KINDS, MixtureMLPCDF, TorchCDF, resolve_device)


def machine(monkeypatch, cuda=False, mps=False):
    """Pretend this machine has (or lacks) CUDA and MPS."""
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda)
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: mps)


LOSSES = [{"kind": "crps", "weight": 1.}, {"kind": "tail_crps", "weight": 1.}]


def small(device, **extra):
    return TorchCDF(encoder={"kind": "mlp"}, losses=LOSSES, hidden=[3], epochs=2,
                    batch_size=8, seeds=[11], device=device, **extra)


def contexts(n, prefix):
    return [{"identity": [prefix+str(i)], "thresholds": [-2.5, -.5, .5, 2.5],
             "weights": [.25]*4, "intervals": [[-2.5, -.5], [.5, 2.5]]} for i in range(n)]


def fitted(device, **extra):
    rng = np.random.default_rng(3)
    x, y = rng.normal(size=(24, 3)), rng.normal(size=24)
    model = small(device, **extra)
    model.fit_decision_context(contexts(24, "f"), contexts(4, "c"))
    model.fit(x, y, x[:4], y[:4])
    return model, x


# ---- the resolver: all three branches, any machine ----

@pytest.mark.parametrize("cuda,mps,expected", [
    (True, True, "cuda"), (True, False, "cuda"),   # a GPU is always first
    (False, True, "mps"),                          # then MPS
    (False, False, "cpu")])                        # then the CPU
def test_auto_prefers_cuda_then_mps_then_cpu(monkeypatch, cuda, mps, expected):
    machine(monkeypatch, cuda, mps)
    assert resolve_device(AUTO_DEVICE) == expected
    assert small(AUTO_DEVICE).device == expected


def test_preference_order_is_the_declared_one():
    assert DEVICE_KINDS == ("cuda", "mps", "cpu")


@pytest.mark.parametrize("explicit", ["cpu", "cuda", "cuda:1", "mps"])
@pytest.mark.parametrize("cuda,mps", [(True, True), (False, False)])
def test_explicit_device_is_never_substituted(monkeypatch, explicit, cuda, mps):
    machine(monkeypatch, cuda, mps)
    assert resolve_device(explicit) == explicit


def test_auto_is_resolved_once_at_construction(monkeypatch):
    machine(monkeypatch, cuda=False, mps=False)
    model = small(AUTO_DEVICE)
    machine(monkeypatch, cuda=True, mps=True)  # the machine "changes" afterwards
    assert (model.device, model.device_requested) == ("cpu", AUTO_DEVICE)


def test_explicit_cuda_is_not_checked_at_construction_as_before(monkeypatch):
    machine(monkeypatch)
    assert small("cuda").device == "cuda"   # torch refuses at first use, exactly as it did


# ---- MPS strictness and honesty ----

def test_explicit_mps_refuses_when_unavailable(monkeypatch):
    machine(monkeypatch, mps=False)
    with pytest.raises(ValueError, match="MPS is not available"):
        small("mps")


def test_explicit_mps_constructs_when_available(monkeypatch):
    machine(monkeypatch, mps=True)
    assert small("mps").device == "mps"


@pytest.mark.parametrize("device", ["mps", AUTO_DEVICE])
def test_deterministic_is_refused_on_mps_never_pretended(monkeypatch, device):
    machine(monkeypatch, mps=True)
    with pytest.raises(ValueError, match="deterministic.*MPS"):
        small(device, deterministic=True)


def test_deterministic_auto_still_works_where_auto_resolves_to_cpu(monkeypatch):
    machine(monkeypatch)
    assert small(AUTO_DEVICE, deterministic=True).device == "cpu"


def test_mps_compute_dtype_is_float32_everywhere_else_float64(monkeypatch):
    machine(monkeypatch, mps=True)
    mps = small("mps")
    assert mps._wide_dtype() is torch.float32
    for device in ("cpu", "cuda"):
        assert small(device)._wide_dtype() is torch.float64


def test_mps_epoch_loss_sum_matches_the_float64_device_sum():
    steps = [(torch.tensor(v, dtype=torch.float32), n) for v, n in ((.31, 8), (1.7, 8), (.05, 3))]
    device, host = predictive_cdf._loss_sum("cpu"), predictive_cdf._loss_sum("mps")
    assert type(device) is predictive_cdf._DeviceLossSum
    assert type(host) is predictive_cdf._HostLossSum
    for loss, rows in steps:
        device.add(loss, rows)
        host.add(loss, rows)
    assert host.value() == device.value()
    assert predictive_cdf._loss_sum("mps").value() == 0.


# ---- identity and the receipt ----

def test_auto_fit_is_byte_identical_to_the_explicit_device_it_resolves_to(monkeypatch):
    machine(monkeypatch)
    auto, x = fitted(AUTO_DEVICE)
    explicit, _ = fitted("cpu")
    assert auto._equivalence_state() == explicit._equivalence_state()
    assert auto.losses == explicit.losses
    np.testing.assert_array_equal(auto.curve(x[:4]).cdf([-.5, .5]), explicit.curve(x[:4]).cdf([-.5, .5]))


def test_equivalence_settings_record_the_resolved_device_not_the_request(monkeypatch):
    machine(monkeypatch)
    assert small(AUTO_DEVICE)._equivalence_settings()["device"] == "cpu"
    assert "device_requested" not in small(AUTO_DEVICE)._equivalence_settings()


def test_auto_checkpoint_records_request_and_resolved_device(monkeypatch, tmp_path):
    machine(monkeypatch)
    model, _ = fitted(AUTO_DEVICE)
    model.save_checkpoint(tmp_path/"ck")
    state = json.loads((tmp_path/"ck/state.json").read_text())
    assert state["settings"]["device"] == "cpu" and state["device_requested"] == AUTO_DEVICE
    assert model._research_state()["device"] == {"requested": AUTO_DEVICE, "resolved": "cpu",
                                                  "fitted_on": "cpu"}


def test_explicit_checkpoint_and_research_state_keep_their_existing_shape(tmp_path):
    model, _ = fitted("cpu")
    model.save_checkpoint(tmp_path/"ck")
    state = json.loads((tmp_path/"ck/state.json").read_text())
    assert "device_requested" not in state and state["settings"]["device"] == "cpu"
    assert "device" not in model._research_state()


# ---- restoring on a different device ----

def rewrite_state(path, **changes):
    """Make a checkpoint look fitted elsewhere: only state.json changes, the weights do not."""
    state = json.loads((path/"state.json").read_text())
    for key, value in changes.items():
        if key == "device":
            state["settings"]["device"] = value
        else:
            state[key] = value
    (path/"state.json").write_text(json.dumps(state))


def test_auto_checkpoint_fitted_on_cuda_restores_on_a_cpu_machine(monkeypatch, tmp_path):
    machine(monkeypatch)
    model, x = fitted(AUTO_DEVICE)
    model.save_checkpoint(tmp_path/"ck")
    rewrite_state(tmp_path/"ck", device="cuda")   # "fitted on CUDA"; this machine has none
    restored = TorchCDF.load_checkpoint(tmp_path/"ck")
    assert (restored.device, restored.fit_device) == ("cpu", "cuda")
    np.testing.assert_array_equal(model.curve(x[:4]).cdf([-.5, .5]), restored.curve(x[:4]).cdf([-.5, .5]))


def test_explicit_checkpoint_stays_strict_and_an_override_is_the_only_way_off_its_device(
        monkeypatch, tmp_path):
    machine(monkeypatch)
    model, x = fitted("cpu")
    model.save_checkpoint(tmp_path/"ck")
    rewrite_state(tmp_path/"ck", device="cuda")
    with pytest.raises((RuntimeError, AssertionError, ValueError)):   # torch's own refusal, no fallback
        TorchCDF.load_checkpoint(tmp_path/"ck")
    restored = TorchCDF.load_checkpoint(tmp_path/"ck", device="cpu")
    assert (restored.device, restored.fit_device) == ("cpu", "cuda")
    np.testing.assert_array_equal(model.curve(x[:4]).cdf([0.]), restored.curve(x[:4]).cdf([0.]))


def test_restore_maps_weights_to_the_restore_device(monkeypatch, tmp_path):
    machine(monkeypatch)
    model, _ = fitted("cpu")
    model.save_checkpoint(tmp_path/"ck")
    seen = {}

    def spy(path, map_location=None, **kwargs):
        seen["map_location"] = map_location
        raise RuntimeError("stop after the weights are requested")

    monkeypatch.setattr(torch, "load", spy)
    machine(monkeypatch, mps=True)
    with pytest.raises(RuntimeError, match="stop after"):
        TorchCDF.load_checkpoint(tmp_path/"ck", device=AUTO_DEVICE)
    assert seen["map_location"] == "mps"
    machine(monkeypatch, cuda=True, mps=True)
    with pytest.raises(RuntimeError, match="stop after"):
        TorchCDF.load_checkpoint(tmp_path/"ck", device=AUTO_DEVICE)
    assert seen["map_location"] == "cuda"


def test_restore_still_checks_the_weights_hash(monkeypatch, tmp_path):
    machine(monkeypatch)
    model, _ = fitted(AUTO_DEVICE)
    model.save_checkpoint(tmp_path/"ck")
    (tmp_path/"ck/weights.pt").write_bytes((tmp_path/"ck/weights.pt").read_bytes()+b"x")
    with pytest.raises(ValueError, match="hash"):
        TorchCDF.load_checkpoint(tmp_path/"ck")


def test_the_plain_mixture_accepts_auto_too(monkeypatch):
    machine(monkeypatch)
    assert MixtureMLPCDF(device=AUTO_DEVICE).device == "cpu"


# ---- MPS has no log_ndtr: refused at construction, never a per-cell skip ----

DECISION_LOG = [{"kind": "decision_log", "weight": 1.}, {"kind": "crps", "weight": 1.}]


@pytest.mark.parametrize("device", ["mps", AUTO_DEVICE])
def test_gaussian_decision_log_is_refused_where_the_device_is_mps(monkeypatch, device):
    machine(monkeypatch, mps=True)
    with pytest.raises(ValueError, match="decision_log.*gaussian.*MPS.*log_ndtr"):
        TorchCDF(encoder={"kind": "mlp"}, losses=DECISION_LOG, hidden=[3], epochs=1, device=device)


def test_gaussian_decision_log_is_untouched_off_mps(monkeypatch):
    machine(monkeypatch)
    for device in ("cpu", "cuda", AUTO_DEVICE):
        TorchCDF(encoder={"kind": "mlp"}, losses=DECISION_LOG, hidden=[3], epochs=1, device=device)
    machine(monkeypatch, mps=True)   # an MPS machine whose auto still means CUDA, or a CPU request
    TorchCDF(encoder={"kind": "mlp"}, losses=DECISION_LOG, hidden=[3], epochs=1, device="cpu")
    machine(monkeypatch, cuda=True, mps=True)
    assert TorchCDF(encoder={"kind": "mlp"}, losses=DECISION_LOG, hidden=[3], epochs=1,
                    device=AUTO_DEVICE).device == "cuda"


def test_mps_refusal_is_a_family_hook_so_a_family_that_has_the_op_is_not_refused(monkeypatch):
    machine(monkeypatch, mps=True)
    monkeypatch.setattr(predictive_cdf._GaussianFamily, "log_cdf_runs_on_mps", True)
    TorchCDF(encoder={"kind": "mlp"}, losses=DECISION_LOG, hidden=[3], epochs=1, device="mps")


def test_a_checkpoint_restored_on_mps_meets_the_same_refusal(monkeypatch, tmp_path):
    machine(monkeypatch)
    model = TorchCDF(encoder={"kind": "mlp"}, losses=DECISION_LOG, hidden=[3], epochs=2, batch_size=8,
                     seeds=[11], device=AUTO_DEVICE)
    rng = np.random.default_rng(3)
    x, y = rng.normal(size=(24, 3)), rng.normal(size=24)
    model.fit_decision_context(contexts(24, "f"), contexts(4, "c"))
    model.fit(x, y, x[:4], y[:4])
    model.save_checkpoint(tmp_path/"ck")
    machine(monkeypatch, mps=True)
    with pytest.raises(ValueError, match="MPS.*log_ndtr"):
        TorchCDF.load_checkpoint(tmp_path/"ck")     # "auto" resolves to MPS here


# ---- the MPS-only numerics, asserted on any machine ----

class Spy:
    """A tensor stand-in recording which conversions ``_wide`` makes, in order."""

    def __init__(self):
        self.calls = []

    def cpu(self):
        self.calls.append("cpu")
        return self

    def double(self):
        self.calls.append("double")
        return self


def pretend_mps(monkeypatch, device="cpu"):
    """Make the code treat ``device`` as Metal, so the MPS branches run on this machine."""
    monkeypatch.setattr(predictive_cdf, "_is_mps", lambda d: d == device)


def test_wide_makes_the_float64_copy_on_the_host_only_for_mps(monkeypatch):
    model = small("cpu")
    spy = Spy()
    model._wide(spy)
    assert spy.calls == ["double"]
    pretend_mps(monkeypatch)
    spy = Spy()
    model._wide(spy)
    assert spy.calls == ["cpu", "double"]       # float64 never exists on the Metal device


def test_context_tensor_narrows_floats_to_float32_only_on_mps(monkeypatch):
    model = small("cpu")
    floats, ints = np.array([.5, 1.5]), np.array([1, 2])
    assert model._context_tensor(floats).dtype is torch.float64
    pretend_mps(monkeypatch)
    assert model._context_tensor(floats).dtype is torch.float32
    assert model._context_tensor(ints).dtype is torch.int64       # integers keep their dtype
    assert model._context_tensor(np.array([True, False])).dtype is torch.bool


def test_target_and_wide_dtype_follow_the_device(monkeypatch):
    model = small("cpu")
    y = np.array([.1, .2, .3])
    assert model._target_tensor(y).dtype is torch.float64
    pretend_mps(monkeypatch)
    assert model._wide_dtype() is torch.float32
    assert model._target_tensor(y).dtype is torch.float32


def test_context_tensors_reach_the_losses_narrowed_on_mps(monkeypatch):
    machine(monkeypatch)
    model = small("cpu")
    model.fit_decision_context(contexts(24, "f"), contexts(4, "c"))
    plain = model._context_tensors(model._fit_context, np.zeros((24, 3)), "fit", 24)
    assert {t.dtype for t in plain.values() if t.is_floating_point()} == {torch.float64}
    pretend_mps(monkeypatch)
    narrowed = model._context_tensors(model._fit_context, np.zeros((24, 3)), "fit", 24)
    assert {t.dtype for t in narrowed.values() if t.is_floating_point()} == {torch.float32}


def test_the_mps_branches_train_end_to_end(monkeypatch):
    """Everything MPS changes (float32 targets and contexts, host loss sums, host float64 delta) runs on CPU."""
    machine(monkeypatch, mps=True)
    pretend_mps(monkeypatch)
    rng = np.random.default_rng(3)
    x, y = rng.normal(size=(24, 3)), rng.normal(size=24)
    model = TorchCDF(encoder={"kind": "mlp"}, losses=LOSSES, hidden=[3], epochs=2, batch_size=8,
                     seeds=[11], device="cpu", training_telemetry=True)
    model.fit_decision_context(contexts(24, "f"), contexts(4, "c"))
    model.fit(x, y, x[:4], y[:4])
    assert model.training_diagnostics[0]["parameter_delta_l2"] > 0
    assert all(np.isfinite(v) for v in model.losses[0])
    assert np.isfinite(model.curve(x[:4]).cdf([0.])).all()


# ---- provenance survives a restore on another device and a re-save ----

def test_resaving_an_auto_checkpoint_restored_with_an_explicit_device_keeps_its_provenance(
        monkeypatch, tmp_path):
    machine(monkeypatch)
    model, x = fitted(AUTO_DEVICE)
    model.save_checkpoint(tmp_path/"ck")
    rewrite_state(tmp_path/"ck", device="cuda")          # fitted on CUDA, as far as the record goes
    restored = TorchCDF.load_checkpoint(tmp_path/"ck", device="cpu")
    restored.save_checkpoint(tmp_path/"again")
    state = json.loads((tmp_path/"again/state.json").read_text())
    assert state["device_requested"] == AUTO_DEVICE
    assert state["settings"]["device"] == "cuda"          # where the weights were fitted
    assert restored._research_state()["device"] == {"requested": AUTO_DEVICE, "resolved": "cpu",
                                                     "fitted_on": "cuda"}
    again = TorchCDF.load_checkpoint(tmp_path/"again")    # and the second generation restores as the first did
    assert (again.device, again.fit_device) == ("cpu", "cuda")
    np.testing.assert_array_equal(model.curve(x[:4]).cdf([0.]), again.curve(x[:4]).cdf([0.]))


def test_refitting_a_restored_model_drops_the_old_provenance(monkeypatch, tmp_path):
    machine(monkeypatch)
    model, x = fitted(AUTO_DEVICE)
    model.save_checkpoint(tmp_path/"ck")
    rewrite_state(tmp_path/"ck", device="cuda")
    restored = TorchCDF.load_checkpoint(tmp_path/"ck", device="cpu")
    restored.fit_decision_context(contexts(24, "f"), contexts(4, "c"))
    restored.fit(x, np.zeros(24)+.1, x[:4], np.zeros(4)+.1)
    assert restored.fit_device == "cpu"
    restored.save_checkpoint(tmp_path/"refit")
    state = json.loads((tmp_path/"refit/state.json").read_text())
    assert "device_requested" not in state and state["settings"]["device"] == "cpu"


def test_an_explicit_checkpoint_resaved_after_an_override_stays_explicit(monkeypatch, tmp_path):
    machine(monkeypatch)
    model, _ = fitted("cpu")
    model.save_checkpoint(tmp_path/"ck")
    TorchCDF.load_checkpoint(tmp_path/"ck", device="cpu").save_checkpoint(tmp_path/"again")
    first = json.loads((tmp_path/"ck/state.json").read_text())
    again = json.loads((tmp_path/"again/state.json").read_text())
    assert "device_requested" not in again
    assert {k: v for k, v in again.items() if k != "weights_sha256"} == {
        k: v for k, v in first.items() if k != "weights_sha256"}


def test_an_explicit_checkpoint_restored_with_auto_and_resaved_stays_explicit(monkeypatch, tmp_path):
    """Provenance comes from the saved state: an "auto" override on an explicit checkpoint is not an auto fit."""
    machine(monkeypatch)
    model, x = fitted("cpu")
    model.save_checkpoint(tmp_path/"ck")
    restored = TorchCDF.load_checkpoint(tmp_path/"ck", device=AUTO_DEVICE)
    assert restored.device_requested == AUTO_DEVICE        # what was asked of this restore
    assert restored._was_auto() is False                    # but not what the checkpoint was
    assert restored._equivalence_settings()["device"] == restored.device
    assert "device" not in restored._research_state()
    restored.save_checkpoint(tmp_path/"again")
    first = json.loads((tmp_path/"ck/state.json").read_text())
    again = json.loads((tmp_path/"again/state.json").read_text())
    assert "device_requested" not in again
    assert {k: v for k, v in again.items() if k != "weights_sha256"} == {
        k: v for k, v in first.items() if k != "weights_sha256"}
    machine(monkeypatch, cuda=True, mps=True)               # another machine: it must not re-resolve there
    third = TorchCDF.load_checkpoint(tmp_path/"again")
    assert (third.device, third._was_auto()) == ("cpu", False)
    machine(monkeypatch)
    np.testing.assert_array_equal(model.curve(x[:4]).cdf([0.]), third.curve(x[:4]).cdf([0.]))


def test_a_refit_after_an_auto_restore_of_an_explicit_checkpoint_is_an_auto_fit(monkeypatch, tmp_path):
    machine(monkeypatch)
    model, x = fitted("cpu")
    model.save_checkpoint(tmp_path/"ck")
    restored = TorchCDF.load_checkpoint(tmp_path/"ck", device=AUTO_DEVICE)
    restored.fit_decision_context(contexts(24, "f"), contexts(4, "c"))
    restored.fit(x, np.zeros(24)+.1, x[:4], np.zeros(4)+.1)
    assert restored._was_auto() is True                     # a fit that asked for "auto" is an auto fit


# ---- the equivalence digest ----

def test_auto_digest_names_the_device_the_weights_were_fitted_on_not_the_restore_device(monkeypatch):
    machine(monkeypatch)
    model = small(AUTO_DEVICE)
    model.fit_device = "cuda"                      # restored on this machine from a CUDA fit
    assert model.device == "cpu" and model._equivalence_settings()["device"] == "cuda"


def test_explicit_digest_keeps_the_restore_device_and_the_docstring_says_it_is_not_portable():
    model = small("cpu")
    model.fit_device = "cuda"                      # an explicit restore override
    assert model._equivalence_settings()["device"] == "cpu"
    doc = predictive_cdf.MixtureMLPCDF._equivalence_state.__doc__
    assert "cross-device" in doc and "not comparable" in doc


# ---- explicit-device documents keep every byte they had ----

def test_explicit_device_artifacts_have_exactly_the_keys_they_always_had(monkeypatch, tmp_path):
    machine(monkeypatch, cuda=True, mps=True)      # a machine where "auto" would have picked something else
    model, x = fitted("cpu")
    assert set(model._equivalence_settings()) == {
        "components", "hidden", "epochs", "batch_size", "seeds", "device", "learning_rate", "weight_decay",
        "min_scale", "activation", "dropout", "head_features", "deterministic", "family"} - {"family"}
    assert model._equivalence_settings()["device"] == "cpu"
    model.save_checkpoint(tmp_path/"ck")
    state = json.loads((tmp_path/"ck/state.json").read_text())
    assert set(state) == {"class", "settings", "scaler", "seen_heads", "losses", "monitor_losses",
                          "best_epochs", "diagnostics", "weights_sha256"}
    assert state["settings"]["device"] == "cpu"
    assert (model.device_requested, model.fit_device) == ("cpu", "cpu")
    assert set(model._research_state()) == {"encoder", "losses", "training_composite_by_seed",
                                            "epochs_run_by_seed", "stopped_by_patience",
                                            "peak_cuda_allocated_bytes"}
    restored = TorchCDF.load_checkpoint(tmp_path/"ck")
    assert restored.device == "cpu"
    np.testing.assert_array_equal(model.curve(x[:4]).cdf([0.]), restored.curve(x[:4]).cdf([0.]))


def test_explicit_cpu_and_auto_on_cpu_write_the_same_weights_bytes(monkeypatch, tmp_path):
    machine(monkeypatch)
    for name, device in (("explicit", "cpu"), ("auto", AUTO_DEVICE)):
        fitted(device)[0].save_checkpoint(tmp_path/name)
    assert (tmp_path/"explicit/weights.pt").read_bytes() == (tmp_path/"auto/weights.pt").read_bytes()
