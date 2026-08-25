"""The dataset seam: TorchAdapter, RowVectorAdapter, and a declared adapter.

Two halves, matching the toolkit/adapter split. The TOOLKIT half proves the
seam is real and that its default changed nothing. The DECLARED half proves
a project's own model family goes through it — declared, not subclassed —
on synthetic rows only (no data file is read, nothing is written outside
tmp_path). The parent project's venue adapters exercise the same seam with
their own panels; here the fixtures are generic module-level adapters.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from dskit.pipeline.base import import_library_class
from dskit.pipeline.libs.torch import (
    DeclaredPredict,
    DeclaredTrain,
    LinearRegressor,
    RowVectorAdapter,
    TorchAdapter,
    TorchBatches,
)
from dskit.pipeline.node import NodeContext

torch = pytest.importorskip("torch")


def ctx(tmp_path, sub="run"):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / sub))


# ---------------------------------------------------------------------------
# TOOLKIT half — the seam, and its do-nothing default
# ---------------------------------------------------------------------------

FLAT_PARAMS = {
    "features": ["x1", "x2"],
    "label": "y",
    "epochs": 2,
    "lr": 0.1,
    "loader": {"batch_size": 8, "shuffle": True, "seed": 11},
}


def flat_rows(n=24):
    return [
        {"x1": (i % 7) / 7.0, "x2": ((i * 5) % 11) / 11.0, "y": float(i % 2)}
        for i in range(n)
    ]


class DoubleAdapter(TorchAdapter):
    """A whole different dataset, objective and prediction — declared, not
    subclassed into the node. Module-level so a document can NAME it."""

    requires_features = False

    def prepare(self, rows, params, *, where):
        xs = [[float(r["v"])] for r in rows]
        ys = [float(r["y"]) for r in rows]
        return TorchBatches(
            len(xs),
            (
                torch.tensor(xs, dtype=torch.float32),
                torch.tensor(ys, dtype=torch.float32),
            ),
        )

    def module_params(self, batches, params):
        return {"in_features": 1, "out_features": 1}

    def select(self, batches, index):
        x, y = batches.payload
        return (x, y) if index is None else (x[index], y[index])

    def loss(self, module, batch):
        x, y = batch
        return torch.nn.functional.mse_loss(module(x).reshape(-1), y)

    def beliefs(self, module, batch):
        x, y = batch
        return module(x).reshape(-1).tolist(), y.tolist()

    def predict(self, module, record):
        with torch.no_grad():
            return float(module(torch.tensor([[float(record["v"])]])).reshape(-1)[0])


class WidthAdapter(RowVectorAdapter):
    """Flat rows, but the WIDTH comes from the data, not the document."""

    def module_params(self, batches, params):
        return {"in_features": 2, "out_features": 1}


class KnobbedAdapter(DoubleAdapter):
    """An adapter with its OWN declarable knob — whose validator owns it."""

    _PARAMS = ("n_groups",)


def ref_to(cls):
    return f"{cls.__module__}:{cls.__qualname__}"


#: The declared refs the plan-time tests name. The module ref only needs to
#: be a shape-valid class path at plan; the adapter ref must import (the
#: node delegates to the adapter's own validator when it can).
MODULE_REF = "torch.nn.Linear"
ADAPTER_REF = ref_to(DoubleAdapter)


def test_the_default_adapter_is_the_row_vector_one():
    """No ``adapter`` declared = the flat behaviour the pack always had."""
    node = LinearRegressor("k", FLAT_PARAMS, mode="train")
    assert isinstance(node.build_adapter(node.params), RowVectorAdapter)
    declared = DeclaredTrain("k", {**FLAT_PARAMS, "module": "torch.nn.Linear"})
    assert isinstance(declared.build_adapter(declared.params), RowVectorAdapter)


def test_the_row_vector_batch_is_still_a_two_tuple():
    """The documented ``loss(self, module, batch)`` contract is unchanged —
    every existing override unpacks ``x, y = batch``."""
    adapter = RowVectorAdapter(FLAT_PARAMS)
    batches = adapter.prepare(flat_rows(8), FLAT_PARAMS, where="rows")
    x, y = adapter.select(batches, torch.arange(3))
    assert x.shape == (3, 2) and y.shape == (3,)
    whole_x, whole_y = adapter.select(batches, None)
    assert whole_x.shape == (8, 2) and whole_y.shape == (8,)


def test_unusable_rows_are_counted_not_fabricated():
    adapter = RowVectorAdapter(FLAT_PARAMS)
    rows = flat_rows(4) + [{"x1": 1.0, "y": 1.0}, {"x1": 1.0, "x2": None, "y": 0.0}]
    batches = adapter.prepare(rows, FLAT_PARAMS, where="rows")
    assert len(batches) == 4 and batches.n_skipped == 2


def test_features_stay_required_when_no_adapter_says_otherwise():
    problems = DeclaredTrain.validate_params({"module": "torch.nn.Linear"})
    assert any("features is required" in p for p in problems)


def test_an_adapter_that_needs_no_features_lifts_the_demand():
    problems = DeclaredTrain.validate_params(
        {"module": MODULE_REF, "adapter": ADAPTER_REF}
    )
    assert not any("features is required" in p for p in problems), problems


@pytest.mark.parametrize(
    "params,needle",
    [
        ({"adapter": 7}, "adapter must be a class path"),
        ({"adapter": "nodots"}, "adapter must name a class"),
        ({"adapter_params": ["nope"]}, "adapter_params must be a dict"),
        ({"adapter": ADAPTER_REF, "adapter_params": {"bogus": 1}}, "adapter_params."),
    ],
)
def test_adapter_knobs_are_refused_by_name_at_plan(params, needle):
    problems = DeclaredTrain.validate_params(
        {"module": MODULE_REF, "features": ["x1"], **params}
    )
    assert any(needle in p for p in problems), problems


def test_an_adapters_own_validator_owns_its_knobs():
    """``adapter_params`` are checked by the ADAPTER's validate_params, never
    restated on the node — a typo is refused at plan."""
    assert KnobbedAdapter.validate_params({"n_groups": 3}) == []
    assert KnobbedAdapter.validate_params({"n_groupz": 3})


def test_a_custom_adapter_drives_the_whole_loop(tmp_path):
    rows = [{"v": i / 10.0, "y": float(i % 2)} for i in range(16)]
    node = DeclaredTrain(
        "k",
        {
            "module": "torch.nn.Linear",
            "adapter": ref_to(DoubleAdapter),
            "epochs": 2,
            "loader": {"batch_size": 4, "seed": 3},
        },
        mode="train",
    )
    out = node.run(ctx(tmp_path), {"rows": rows})
    assert out["metrics"]["n_rows"] == 16
    # The module was built from the ADAPTER's data-implied kwargs alone.
    assert out["signal"].predict({"v": 0.5}) is not None


def test_data_implied_kwargs_are_recorded_and_survive_a_load(tmp_path):
    """A shape the DATA implied is in the sidecar, so a restore rebuilds the
    module that was trained — the document alone does not carry it."""
    shared = {
        "module": "torch.nn.Linear",
        "adapter": ref_to(WidthAdapter),
        "features": ["x1", "x2"],
        "label": "y",
    }
    out = DeclaredTrain(
        "k", {**shared, "epochs": 1, "loader": {"seed": 5}}, mode="train"
    ).run(ctx(tmp_path), {"rows": flat_rows()})
    sidecar = json.loads(
        pathlib.Path(out["artifact_path"]).with_suffix(".json").read_text()
    )
    assert sidecar["data_params"] == {"in_features": 2, "out_features": 1}

    restored = DeclaredPredict("p", shared, mode="load", artifact=out["artifact_path"])
    signal = restored.run(ctx(tmp_path, "p"), {})["signal"]
    assert signal.loaded and signal.predict({"x1": 0.2, "x2": 0.3}) is not None


def test_a_declared_module_param_beats_the_data_implied_one():
    """The precedence the family PROMISES ("a declared value always wins,
    and the data can never silently override the config") — pinned at the
    merge itself, where a swapped merge order survived every other test
    (review M7). The adapter implies out_features=1; the document declares
    3; the built module must carry 3, keeping the data-implied in_features
    it did not fight."""
    node = DeclaredTrain(
        "k",
        {
            "module": "torch.nn.Linear",
            "adapter": ref_to(WidthAdapter),
            "module_params": {"out_features": 3},
            "features": ["x1", "x2"],
            "label": "y",
            "epochs": 1,
        },
        mode="train",
    )
    node._data_params = {"in_features": 2, "out_features": 1}
    module = node.build_module(node.params)
    assert module.in_features == 2 and module.out_features == 3


def test_a_tampered_data_params_breaks_the_content_hash(tmp_path):
    shared = {
        "module": "torch.nn.Linear",
        "adapter": ref_to(WidthAdapter),
        "features": ["x1", "x2"],
        "label": "y",
    }
    out = DeclaredTrain("k", {**shared, "epochs": 1}, mode="train").run(
        ctx(tmp_path), {"rows": flat_rows()}
    )
    path = pathlib.Path(out["artifact_path"]).with_suffix(".json")
    sidecar = json.loads(path.read_text())
    sidecar["data_params"] = {"in_features": 99}
    path.write_text(json.dumps(sidecar))
    with pytest.raises(ValueError, match="content hash mismatch"):
        DeclaredPredict("p", shared, mode="load", artifact=out["artifact_path"]).run(
            ctx(tmp_path, "p"), {}
        )


# ---------------------------------------------------------------------------
# DECLARED half — an architecture named by the document, end to end
# ---------------------------------------------------------------------------

#: The declared counterpart of FLAT_PARAMS — same fit, but the architecture
#: is config, not a Python subclass.
DECLARED_FLAT = {
    "module": "torch.nn.Linear",
    "module_params": {"in_features": 2, "out_features": 1},
    **FLAT_PARAMS,
}


def test_the_declared_doorway_records_validation_telemetry(tmp_path):
    node = DeclaredTrain("qhat", DECLARED_FLAT, mode="train")
    out = node.run(
        ctx(tmp_path),
        {"rows": flat_rows(16), "val_rows": flat_rows(8)},
    )
    metrics = out["metrics"]
    assert metrics["n_rows"] == 16 and metrics["n_val_rows"] == 8
    # Per-epoch validation metrics come from trainlog, not a second helper.
    assert {"final_logloss", "final_brier", "final_ece"} <= set(metrics)
    curve = json.loads(
        (
            pathlib.Path(tmp_path)
            / "run"
            / "artifacts"
            / "qhat"
            / "training_curve.json"
        ).read_text()
    )
    assert curve["epochs_run"] == 2
    assert all("val_loss" in row and "logloss" in row for row in curve["epochs"])


def test_one_seed_gives_one_declared_fit(tmp_path):
    rows = flat_rows()
    first = DeclaredTrain("a", DECLARED_FLAT, mode="train").run(
        ctx(tmp_path, "a"), {"rows": rows}
    )
    second = DeclaredTrain("b", DECLARED_FLAT, mode="train").run(
        ctx(tmp_path, "b"), {"rows": rows}
    )
    a = torch.load(first["artifact_path"], map_location="cpu", weights_only=True)
    b = torch.load(second["artifact_path"], map_location="cpu", weights_only=True)
    assert a.keys() == b.keys()
    assert all(torch.equal(a[k], b[k]) for k in a)
    assert first["metrics"]["final_loss"] == second["metrics"]["final_loss"]


def test_a_different_seed_gives_a_different_declared_fit(tmp_path):
    rows = flat_rows()
    first = DeclaredTrain("a", DECLARED_FLAT, mode="train").run(
        ctx(tmp_path, "a"), {"rows": rows}
    )
    other = {**DECLARED_FLAT, "loader": {**DECLARED_FLAT["loader"], "seed": 99}}
    second = DeclaredTrain("b", other, mode="train").run(
        ctx(tmp_path, "b"), {"rows": rows}
    )
    a = torch.load(first["artifact_path"], map_location="cpu", weights_only=True)
    b = torch.load(second["artifact_path"], map_location="cpu", weights_only=True)
    assert not all(torch.equal(a[k], b[k]) for k in a)


def test_the_default_optimizer_is_still_sgd(tmp_path):
    node = LinearRegressor("k", FLAT_PARAMS, mode="train")
    import torch as t

    built = node.build_optimizer(t.nn.Linear(2, 1), FLAT_PARAMS)
    assert isinstance(built, t.optim.SGD)


def test_a_declared_optimizer_carries_its_own_knobs(tmp_path):
    """A family whose regularization IS weight decay trains to a different
    model under plain SGD, silently — so the optimizer is declarable."""
    import torch as t

    node = LinearRegressor("k", FLAT_PARAMS, mode="train")
    built = node.build_optimizer(
        t.nn.Linear(2, 1),
        {
            **FLAT_PARAMS,
            "optimizer": "torch.optim.AdamW",
            "optimizer_params": {"weight_decay": 0.05},
        },
    )
    assert isinstance(built, t.optim.AdamW)
    assert built.param_groups[0]["weight_decay"] == 0.05
    assert built.param_groups[0]["lr"] == FLAT_PARAMS["lr"]


@pytest.mark.parametrize(
    "params,needle",
    [
        ({"optimizer": 3}, "optimizer must be a class path"),
        ({"optimizer_params": []}, "optimizer_params must be a dict"),
        ({"optimizer_params": {"lr": 0.1}}, "must not carry 'lr'"),
    ],
)
def test_optimizer_knobs_are_refused_by_name_at_plan(params, needle):
    problems = LinearRegressor.validate_params({**FLAT_PARAMS, **params})
    assert any(needle in p for p in problems), problems


def test_a_declared_optimizer_actually_changes_the_fit(tmp_path):
    sgd = DeclaredTrain("a", DECLARED_FLAT, mode="train").run(
        ctx(tmp_path, "a"), {"rows": flat_rows()}
    )
    adamw = DeclaredTrain(
        "b",
        {
            **DECLARED_FLAT,
            "optimizer": "torch.optim.AdamW",
            "optimizer_params": {"weight_decay": 0.1},
        },
        mode="train",
    ).run(ctx(tmp_path, "b"), {"rows": flat_rows()})
    a = torch.load(sgd["artifact_path"], map_location="cpu", weights_only=True)
    b = torch.load(adamw["artifact_path"], map_location="cpu", weights_only=True)
    assert not all(torch.equal(a[k], b[k]) for k in a)


def test_import_library_class_requires_the_named_methods():
    """``requires`` refuses a resolvable path whose class lacks the method
    BY NAME — the plan-time honesty the declared grammar rests on."""
    cls = import_library_class("torch.nn.Linear", "torch module", requires=("forward",))
    assert issubclass(cls, torch.nn.Module)
    with pytest.raises(ValueError, match="no forward"):
        import_library_class(
            "collections.OrderedDict", "torch module", requires=("forward",)
        )
