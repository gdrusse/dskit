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
import re

import pytest

from dskit.pipeline.base import abstract_class_problem, import_library_class
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


class StrictKnobAdapter(DoubleAdapter):
    """Reads its knob EAGERLY, so a mis-typed one is a ``TypeError`` raised
    by the adapter's own constructor — the case the ``adapter_params``
    refusal was written for, and the one it must keep."""

    _PARAMS = ("n_groups",)

    def __init__(self, params=None):
        super().__init__(params)
        self.n_groups = int(self.params["n_groups"])


def ref_to(cls):
    return f"{cls.__module__}:{cls.__qualname__}"


#: The declared refs the plan-time tests name. The module ref only needs to
#: be a shape-valid class path at plan; the adapter ref must import (the
#: node delegates to the adapter's own validator when it can).
MODULE_REF = "torch.nn.Linear"
ADAPTER_REF = ref_to(DoubleAdapter)


class IncompleteAdapter(TorchAdapter):
    """Prepares rows and stops — the half-written adapter the ABC exists to
    catch. Declaring it must stay legal; CONSTRUCTING it must not."""

    def prepare(self, rows, params, *, where):
        return TorchBatches(0, None)


class IncompleteKnobAdapter(IncompleteAdapter):
    """Half-written AND knobbed. Its class-side contract (``_PARAMS``,
    ``requires_features``, ``validate_params``) must keep serving plan time
    even though the class can never construct — abstractness is a defect of
    CONSTRUCTION, not of resolution."""

    requires_features = False
    _PARAMS = ("n_groups",)


#: The hooks an adapter genuinely cannot inherit: rows -> batches, the batch
#: at an index, the objective, and one record -> one belief. Pinned as a set
#: because ADDING one silently breaks every out-of-repo adapter at
#: construction, and DROPPING one puts the raise back at call time — the
#: failure this ABC replaced.
ABSTRACT_HOOKS = {"prepare", "select", "loss", "predict"}


def test_the_adapter_seam_declares_exactly_its_four_abstract_hooks():
    assert set(TorchAdapter.__abstractmethods__) == ABSTRACT_HOOKS


def test_the_seams_docstring_enumerates_exactly_the_abstract_hooks():
    """The required set is stated twice — once by the decorators, once by
    the class docstring an adapter author reads BEFORE writing a line. The
    docstring is the copy that cannot fail loudly, so it is pinned here: a
    hook made abstract without being enumerated (or enumerated as required
    while it keeps a working default) sends the author to write the wrong
    four and meet a construction refusal naming a hook nobody told them
    about.
    """
    enumerated = re.findall(r"^\s*\d+\. ``(\w+)``", TorchAdapter.__doc__, re.M)
    assert len(enumerated) == len(ABSTRACT_HOOKS)  # numbered once each
    assert set(enumerated) == ABSTRACT_HOOKS


def test_an_incomplete_adapter_refuses_at_construction():
    """Abstract means abstract: the missing hooks are named by TypeError at
    construction, not by NotImplementedError deep in a training loop."""
    with pytest.raises(TypeError) as caught:
        IncompleteAdapter({})
    message = str(caught.value)
    assert "IncompleteAdapter" in message
    assert all(hook in message for hook in ABSTRACT_HOOKS - {"prepare"})


def test_the_optional_hooks_stay_optional():
    """Only the four are abstract — an adapter that accepts the defaults for
    ``module_params``/``beliefs``/``to_device``/``fitted``/the state pair
    constructs, exactly as ``DoubleAdapter`` and ``RowVectorAdapter`` do."""
    for cls in (RowVectorAdapter, DoubleAdapter):
        assert not getattr(cls, "__abstractmethods__", frozenset())
        assert isinstance(cls({}), TorchAdapter)


def test_a_complete_declared_adapter_resolves():
    """The structural half of the declared path: a complete adapter passes
    the import-path grammar's ``requires`` check, as it always did."""
    assert import_library_class(ref_to(DoubleAdapter), "a", requires=("prepare",))


def test_an_incomplete_adapter_still_RESOLVES_structurally():
    """The TODO's claim, kept true on purpose: adapters named by import
    path are checked STRUCTURALLY at resolution and are unaffected by the
    ABC. A half-written adapter, having written ``prepare`` first, passes
    ``requires`` — because ``import_library_class``'s ``ValueError``
    already means "the library may rightly be missing HERE" and plan-time
    callers swallow it, an abstractness refusal must not ride that
    channel. Construction is where the ABC bites.
    """
    cls = import_library_class(ref_to(IncompleteAdapter), "a", requires=("prepare",))
    assert cls is IncompleteAdapter


def test_a_declared_incomplete_adapter_is_refused_by_its_missing_hooks():
    """The declared path end to end, and the half the ABC changed.

    ``build_adapter`` resolves then CONSTRUCTS, and asks core's
    ``abstract_class_problem`` in between — so the ABC's raw ``TypeError``
    never reaches the ``adapter_params`` branch below. The refusal must
    name the hooks that were never implemented, not ``adapter_params``,
    which here is empty and entirely correct and would send the author to
    inspect JSON knobs instead of writing the three missing methods.

    Pinned by EQUALITY, in the same words as the plan-side test above:
    the two doorways say one sentence between them, and the whole point
    of ``validate_params`` reporting at plan what ``build_adapter`` would
    raise at run is that the two cannot drift. Substring checks would let
    the run copy say ``"torch adapters"`` — or name a different subject
    entirely — while still passing. The literal is restated here on
    purpose: a test that sourced its expected wording from the module it
    validates would assert nothing.
    """
    node = DeclaredTrain("k", {**FLAT_PARAMS, "module": MODULE_REF})
    ref = ref_to(IncompleteAdapter)
    with pytest.raises(ValueError) as caught:
        node.build_adapter({"adapter": ref, "adapter_params": {}})
    assert str(caught.value) == abstract_class_problem(
        IncompleteAdapter, "torch adapter", repr(ref)
    )


def test_a_mis_typed_adapter_knob_still_names_adapter_params():
    """The other side of the same discrimination: a COMPLETE adapter whose
    own constructor rejects a knob still gets the ``adapter_params``
    diagnosis, which the abstract-hook guard must not have swallowed."""
    node = DeclaredTrain("k", {**FLAT_PARAMS, "module": MODULE_REF})
    with pytest.raises(ValueError) as caught:
        node.build_adapter(
            {
                "adapter": ref_to(StrictKnobAdapter),
                "adapter_params": {"n_groups": ["not", "an", "int"]},
            }
        )
    message = str(caught.value)
    assert ref_to(StrictKnobAdapter) in message
    assert "adapter_params" in message


def test_plan_still_validates_an_incomplete_adapters_knobs():
    """Abstractness must not be smuggled into the "cannot be imported HERE"
    channel. ``_resolve_adapter`` swallows ``ValueError`` because a plan
    machine may rightly lack the library — but an incomplete adapter
    IMPORTS fine, and its ``validate_params`` (a classmethod) works fine,
    so a mis-typed knob must still be refused at plan exactly as it is for
    a complete adapter. Validation approving a knob the run never uses is
    the drift shape this repo's standards name."""
    problems = DeclaredTrain.validate_params(
        {
            **FLAT_PARAMS,
            "module": MODULE_REF,
            "adapter": ref_to(IncompleteKnobAdapter),
            "adapter_params": {"nn_groups": 3},
        }
    )
    assert any("adapter_params" in p and "nn_groups" in p for p in problems), problems


def test_plan_refuses_an_adapter_that_can_never_construct():
    """Plan-time truth, the other half: an adapter that IMPORTED fine but
    can never be constructed is a plan-time fact, and ``validate_params``
    must report it — in exactly the sentence ``build_adapter`` would raise
    at run, so ``python -m dskit.pipeline validate`` never pronounces
    sound a document whose trainer is guaranteed to refuse. This is a
    problems LIST, not ``import_library_class``'s swallowed ``ValueError``
    channel: a machine genuinely missing the library still gets ``None``
    from ``_resolve_adapter`` and no false refusal."""
    problems = DeclaredTrain.validate_params(
        {
            **FLAT_PARAMS,
            "module": MODULE_REF,
            "adapter": ref_to(IncompleteAdapter),
            "adapter_params": {},
        }
    )
    expected = abstract_class_problem(
        IncompleteAdapter, "torch adapter", repr(ref_to(IncompleteAdapter))
    )
    assert expected in problems, problems


def test_plan_still_reads_an_incomplete_adapters_requires_features():
    """Same channel, other consumer: ``requires_features = False`` on an
    incomplete adapter still lifts the ``features`` demand at plan."""
    params = {k: v for k, v in FLAT_PARAMS.items() if k != "features"}
    problems = DeclaredTrain.validate_params(
        {**params, "module": MODULE_REF, "adapter": ref_to(IncompleteKnobAdapter)}
    )
    assert not any("features is required" in p for p in problems), problems


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


#: Every call the declared-loss sentinel saw, so a test can prove the
#: SELECTED objective is what the training step actually applied — not
#: merely what validation accepted.
LOSS_CALLS = []


def recording_huber(prediction, target):
    """A Huber objective that records every call — the sentinel a document
    can NAME, so 'the selection reached the training step' is an assertion
    about the loop, not about the params dict."""
    LOSS_CALLS.append(tuple(prediction.reshape(-1).shape))
    return torch.nn.functional.smooth_l1_loss(prediction, target)


def test_the_default_objective_is_still_mse():
    """The default is the SAME callable the adapter hardcoded — resolved
    through the knob's own doorway, so there is one loss path, not two."""
    assert RowVectorAdapter(FLAT_PARAMS).build_loss() is torch.nn.functional.mse_loss


def test_a_declared_loss_resolves_to_the_named_callable():
    adapter = RowVectorAdapter(
        {**FLAT_PARAMS, "loss": "torch.nn.functional:smooth_l1_loss"}
    )
    assert adapter.build_loss() is torch.nn.functional.smooth_l1_loss


@pytest.mark.parametrize(
    "params,needle",
    [
        ({"loss": 3}, "loss must be a class path"),
        ({"loss": "mse_loss"}, "must name a class as module.ClassName"),
        ({"loss": ""}, "loss must be a class path"),
    ],
)
def test_loss_knobs_are_refused_by_name_at_plan(params, needle):
    problems = LinearRegressor.validate_params({**FLAT_PARAMS, **params})
    assert any(needle in p for p in problems), problems


def test_a_declared_loss_reaches_the_training_step(tmp_path):
    """The knob is not paperwork: the named callable is what every batch's
    backward pass was taken on."""
    LOSS_CALLS.clear()
    DeclaredTrain(
        "huber",
        {**DECLARED_FLAT, "loss": f"{__name__}:recording_huber"},
        mode="train",
    ).run(ctx(tmp_path), {"rows": flat_rows()})
    assert LOSS_CALLS, "the declared loss was never called by the fit"


def test_a_declared_loss_actually_changes_the_fit(tmp_path):
    """Huber is the point of the knob on fat-tailed targets — it must train
    a DIFFERENT model than MSE, or the selection changed nothing."""
    mse = DeclaredTrain("a", DECLARED_FLAT, mode="train").run(
        ctx(tmp_path, "a"), {"rows": flat_rows()}
    )
    huber = DeclaredTrain(
        "b",
        {**DECLARED_FLAT, "loss": "torch.nn.functional:smooth_l1_loss"},
        mode="train",
    ).run(ctx(tmp_path, "b"), {"rows": flat_rows()})
    a = torch.load(mse["artifact_path"], map_location="cpu", weights_only=True)
    b = torch.load(huber["artifact_path"], map_location="cpu", weights_only=True)
    assert not all(torch.equal(a[k], b[k]) for k in a)


def test_a_loss_that_cannot_be_imported_is_refused_at_run(tmp_path):
    node = DeclaredTrain(
        "k", {**DECLARED_FLAT, "loss": "torch.nn.functional:no_such_loss"}, mode="train"
    )
    with pytest.raises(ValueError, match="no_such_loss"):
        node.run(ctx(tmp_path), {"rows": flat_rows()})


#: Rows the DoubleAdapter family reads — one value ``v`` and a label.
def double_rows(n=16):
    return [{"v": i / 10.0, "y": float(i % 2)} for i in range(n)]


#: The knob is only real for an adapter that APPLIES it, and the declared
#: family is where a foreign adapter arrives. ``DoubleAdapter`` hardcodes
#: its objective (it does not set ``applies_loss``); this one goes through
#: the doorway, which is the whole difference.
class ObjectiveAdapter(DoubleAdapter):
    """A declared adapter that applies the node's declared objective."""

    applies_loss = True

    def loss(self, module, batch):
        x, y = batch
        return self.build_loss()(module(x).reshape(-1), y)


def declared_loss_params(adapter, **extra):
    return {
        "module": "torch.nn.Linear",
        "adapter": ref_to(adapter),
        "epochs": 2,
        "loader": {"batch_size": 4, "seed": 3},
        **extra,
    }


def test_an_adapter_that_ignores_the_objective_refuses_the_knob_at_plan():
    """The knob is accepted by the NODE but applied by the ADAPTER, so an
    adapter that hardcodes its objective must refuse ``loss`` by name — a
    silently-ignored objective trains a different model than the document
    declares, which is the exact defect the knob exists to prevent."""
    problems = DeclaredTrain.validate_params(
        declared_loss_params(DoubleAdapter, loss="torch.nn.functional:smooth_l1_loss")
    )
    assert any("applies_loss" in p and "DoubleAdapter" in p for p in problems), problems


def test_an_adapter_that_ignores_the_objective_refuses_the_knob_at_run():
    """The SAME sentence at the fit's own adapter doorway, for the machine
    whose plan could not import the adapter and so could not tell — a fit
    never proceeds under an objective nobody applies. Pinned by equality
    against the plan-side wording, as the abstract-hook refusal already
    is: two doorways, one sentence, and no room to drift."""
    declared = declared_loss_params(
        DoubleAdapter, loss="torch.nn.functional:smooth_l1_loss"
    )
    node = DeclaredTrain("k", declared_loss_params(ObjectiveAdapter), mode="train")
    with pytest.raises(ValueError) as caught:
        node._adapter_for_fit(declared)
    assert str(caught.value) == "\n".join(
        p for p in DeclaredTrain.validate_params(declared) if "applies_loss" in p
    )


def test_an_adapter_that_applies_the_objective_gets_the_declared_one(tmp_path):
    """And the other side of that discrimination: an adapter that answers
    through ``build_loss`` trains on the callable the document named."""
    LOSS_CALLS.clear()
    params = declared_loss_params(ObjectiveAdapter, loss=f"{__name__}:recording_huber")
    assert DeclaredTrain.validate_params(params) == []
    DeclaredTrain("k", params, mode="train").run(
        ctx(tmp_path), {"rows": double_rows()}
    )
    assert LOSS_CALLS, "the declared loss was never called by the fit"


def test_an_adapter_declares_whether_it_applies_the_objective():
    """Default-deny: the ABC promises nothing, the shipped row-vector
    adapter promises it, so the flag is a declaration and never a guess."""
    assert TorchAdapter.applies_loss is False
    assert RowVectorAdapter.applies_loss is True


def test_a_module_loss_class_is_instantiated_like_an_optimizer(tmp_path):
    """``torch.nn:HuberLoss`` is the spelling a torch user reaches for, and
    the grammar says "name me a CLASS" — so a resolved class is CONSTRUCTED
    here exactly as ``optimizer`` is, never called with the batch as its
    constructor kwargs."""
    fn = RowVectorAdapter({**FLAT_PARAMS, "loss": "torch.nn:HuberLoss"}).build_loss()
    assert isinstance(fn, torch.nn.HuberLoss)
    value = fn(torch.tensor([1.0, 2.0]), torch.tensor([1.0, 3.0]))
    assert value.ndim == 0
    out = DeclaredTrain(
        "k", {**DECLARED_FLAT, "loss": "torch.nn:HuberLoss"}, mode="train"
    ).run(ctx(tmp_path), {"rows": flat_rows()})
    assert out["metrics"]["final_loss"] >= 0.0


class NeedsAnArgumentLoss:
    """A loss class whose constructor takes a required argument — no import
    path can supply it, so the doorway must say so BY NAME."""

    def __init__(self, weight):
        self.weight = weight

    def __call__(self, prediction, target):
        return prediction.sum() * self.weight


def test_a_loss_class_that_cannot_be_constructed_is_refused_by_name():
    adapter = RowVectorAdapter({**FLAT_PARAMS, "loss": f"{__name__}:NeedsAnArgumentLoss"})
    with pytest.raises(ValueError, match="NeedsAnArgumentLoss"):
        adapter.build_loss()


class NotCallableWhenBuilt:
    """A CLASS whose instances are not callable. ``getattr(cls, "__call__")``
    finds ``type.__call__`` and is always callable, so only the CONSTRUCTED
    object can answer whether this is a loss."""


def test_a_loss_whose_instances_are_not_callable_is_refused_by_name():
    """The doorway promises a callable; a class that constructs into a
    non-callable must be refused HERE, by name, not as a ``TypeError``
    inside the batch loop."""
    adapter = RowVectorAdapter(
        {**FLAT_PARAMS, "loss": f"{__name__}:NotCallableWhenBuilt"}
    )
    with pytest.raises(ValueError, match="NotCallableWhenBuilt"):
        adapter.build_loss()


# ---------------------------------------------------------------------------
# The promise belongs to the ``loss()`` IMPLEMENTATION, not to the class —
# so every documented way to write one's own objective (an adapter
# subclass, a family that swaps the adapter, a family that answers loss()
# itself) is held to the same declaration.
# ---------------------------------------------------------------------------


class HardcodedRowAdapter(RowVectorAdapter):
    """A row-vector adapter that computes its OWN objective. It inherits an
    ``applies_loss = True`` it never earned, so the promise must be reset
    for any subclass overriding ``loss`` without repeating it."""

    def loss(self, module, batch):
        features, label = batch
        return torch.nn.functional.mse_loss(module(features).reshape(-1), label)


class ReclaimedRowAdapter(RowVectorAdapter):
    """The other side: overrides ``loss`` AND repeats the declaration, so
    the knob stays real."""

    applies_loss = True

    def loss(self, module, batch):
        features, label = batch
        return self.build_loss()(module(features).reshape(-1), label)


class SwappedAdapterFamily(LinearRegressor):
    """A family that swaps the adapter through ``build_adapter`` — the
    pack's documented extension hook, and the tier-3 child pattern."""

    def build_adapter(self, params):
        return HardcodedRowAdapter(params)


class HardcodedLossFamily(LinearRegressor):
    """A family that answers ``loss()`` itself rather than delegating to
    the adapter — what ``TorchTrain.loss``'s docstring invites."""

    def loss(self, module, batch):
        features, label = batch
        return torch.nn.functional.mse_loss(module(features).reshape(-1), label)


def test_an_adapter_subclass_overriding_loss_loses_the_inherited_promise():
    """``applies_loss`` describes a ``loss()`` implementation, so it cannot
    be inherited past one that was replaced."""
    assert HardcodedRowAdapter.applies_loss is False
    assert ReclaimedRowAdapter.applies_loss is True
    # A subclass that did NOT touch loss() keeps the promise it inherited.
    assert WidthAdapter.applies_loss is True


def test_an_adapter_subclass_that_hardcodes_its_objective_refuses_the_knob():
    problems = DeclaredTrain.validate_params(
        declared_loss_params(
            HardcodedRowAdapter,
            loss="torch.nn.functional:smooth_l1_loss",
            features=["x1", "x2"],
            label="y",
        )
    )
    assert any(
        "applies_loss" in p and "HardcodedRowAdapter" in p for p in problems
    ), problems


def test_the_plan_asks_the_adapter_the_fit_will_actually_build():
    """One statement of the adapter identity: what ``validate_params``
    interrogates about the objective is the class ``build_adapter``
    constructs, for the default family and the declared one alike."""
    node = LinearRegressor("k", FLAT_PARAMS, mode="train")
    assert type(node.build_adapter(FLAT_PARAMS)) is node._loss_adapter(FLAT_PARAMS)
    declared = declared_loss_params(ObjectiveAdapter)
    node = DeclaredTrain("k", declared, mode="train")
    assert type(node.build_adapter(declared)) is node._loss_adapter(declared)


def test_a_family_that_swaps_the_adapter_cannot_ignore_the_objective(tmp_path):
    """The refusal lives where EVERY family's adapter is built, so
    overriding ``build_adapter`` cannot walk past it: the fit refuses
    rather than training under an objective the document did not get."""
    LOSS_CALLS.clear()
    node = SwappedAdapterFamily(
        "k", {**FLAT_PARAMS, "loss": f"{__name__}:recording_huber"}, mode="train"
    )
    with pytest.raises(ValueError, match="applies_loss"):
        node.run(ctx(tmp_path), {"rows": flat_rows()})
    assert LOSS_CALLS == []


def test_a_family_that_hardcodes_its_objective_refuses_the_knob(tmp_path):
    """A node that answers ``loss()`` itself has made no promise about the
    knob either — refused at plan, and again at the fit."""
    params = {**FLAT_PARAMS, "loss": f"{__name__}:recording_huber"}
    problems = HardcodedLossFamily.validate_params(params)
    assert any(
        "applies_loss" in p and "HardcodedLossFamily" in p for p in problems
    ), problems
    LOSS_CALLS.clear()
    with pytest.raises(ValueError, match="applies_loss"):
        HardcodedLossFamily("k", params, mode="train").run(
            ctx(tmp_path), {"rows": flat_rows()}
        )
    assert LOSS_CALLS == []


class BufferedLoss(torch.nn.Module):
    """A loss with REGISTERED state. Every shipped ``nn`` loss constructs
    with zero buffers, so only a stateful one can prove the constructed
    objective is moved where the module and the batches already go."""

    def __init__(self):
        super().__init__()
        self.register_buffer("w", torch.ones(1))

    def forward(self, prediction, target):
        return ((prediction - target) ** 2 * self.w).mean()


def test_a_stateful_loss_class_is_moved_to_the_declared_device():
    """``device`` moves the module and every batch; a constructed loss
    module's own state must ride along, or a stateful loss dies mid-fit
    with a raw cross-device error — the obscure failure ``build_loss``
    exists to prevent."""
    adapter = RowVectorAdapter(
        {**FLAT_PARAMS, "device": "meta", "loss": ref_to(BufferedLoss)}
    )
    assert adapter.build_loss().w.device.type == "meta"


def test_a_functional_loss_ignores_the_device_knob():
    """A functional objective carries no state, so ``device`` must leave it
    the very same object — the default path stays byte-identical."""
    adapter = RowVectorAdapter({**FLAT_PARAMS, "device": "meta"})
    assert adapter.build_loss() is torch.nn.functional.mse_loss


class HardcodedLossMixin:
    """A co-base supplying ``loss()`` — "one mixin for the pair is the
    shape" is the pack's own documented pattern, so the promise reset must
    key on the RESOLVED implementation, not on the class's own body."""

    def loss(self, module, batch):
        features, label = batch
        return torch.nn.functional.mse_loss(module(features).reshape(-1), label)


class MixinLossFamily(HardcodedLossMixin, LinearRegressor):
    """A family whose ``loss()`` arrives from a mixin, promising nothing."""


class MixinLossAdapter(HardcodedLossMixin, RowVectorAdapter):
    """Same replacement, adapter side."""


class DoorwayLossMixin:
    """A mixin ``loss()`` that DOES go through the doorway, so a class may
    honestly re-declare the promise beside it."""

    def loss(self, module, batch):
        features, label = batch
        return self.build_loss()(module(features).reshape(-1), label)


class ReclaimedMixinAdapter(DoorwayLossMixin, RowVectorAdapter):
    """Declares ``applies_loss`` in its own body FOR the mixin's ``loss``."""

    applies_loss = True


def test_a_mixin_supplied_loss_loses_the_inherited_promise():
    """``applies_loss`` follows the implementation that will actually run:
    a ``loss()`` resolved from a co-base earned nothing, while a fresh
    declaration binds to whatever implementation is visible beside it."""
    assert MixinLossFamily.applies_loss is False
    assert MixinLossAdapter.applies_loss is False
    assert ReclaimedMixinAdapter.applies_loss is True


def test_a_family_with_a_mixin_loss_refuses_the_knob(tmp_path):
    """End-to-end for the mixin hole: refused at plan and at the fit, and
    the declared sentinel is never silently dropped."""
    params = {**FLAT_PARAMS, "loss": f"{__name__}:recording_huber"}
    problems = MixinLossFamily.validate_params(params)
    assert any(
        "applies_loss" in p and "MixinLossFamily" in p for p in problems
    ), problems
    LOSS_CALLS.clear()
    with pytest.raises(ValueError, match="applies_loss"):
        MixinLossFamily("k", params, mode="train").run(
            ctx(tmp_path), {"rows": flat_rows()}
        )
    assert LOSS_CALLS == []


def test_a_family_swapping_build_adapter_alone_is_unknown_at_plan():
    """Plan must never name an adapter the fit does not build: a family
    overriding ``build_adapter`` without ``_loss_adapter`` beside it
    answers ``None`` at plan — cannot-tell, settled by the fit's own
    doorway — rather than restating an identity the override changed."""
    assert SwappedAdapterFamily._loss_adapter(FLAT_PARAMS) is None
    # And the pack's own pairs still answer: they define the two together.
    assert LinearRegressor._loss_adapter(FLAT_PARAMS) is RowVectorAdapter


class LyingPlanFamily(LinearRegressor):
    """Overrides the pair TOGETHER but in disagreement — the residual
    duplication the run-side pin exists to catch loudly."""

    def build_adapter(self, params):
        return HardcodedRowAdapter(params)

    @classmethod
    def _loss_adapter(cls, params):
        return RowVectorAdapter


def test_plan_and_run_adapter_identity_is_pinned_at_the_fit():
    """When plan NAMES a class, the fit must build exactly that class —
    the runtime pin on the one duplication ``_ADAPTER`` cannot collapse."""
    node = LyingPlanFamily("k", FLAT_PARAMS, mode="train")
    with pytest.raises(ValueError, match="drifted") as caught:
        node._adapter_for_fit(FLAT_PARAMS)
    message = str(caught.value)
    assert "RowVectorAdapter" in message and "HardcodedRowAdapter" in message


def test_import_library_class_requires_the_named_methods():
    """``requires`` refuses a resolvable path whose class lacks the method
    BY NAME — the plan-time honesty the declared grammar rests on."""
    cls = import_library_class("torch.nn.Linear", "torch module", requires=("forward",))
    assert issubclass(cls, torch.nn.Module)
    with pytest.raises(ValueError, match="no forward"):
        import_library_class(
            "collections.OrderedDict", "torch module", requires=("forward",)
        )
