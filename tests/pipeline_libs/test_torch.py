"""The tier-2 torch pack: unit, artifact, determinism and conformance tests.

Everything runs on tiny in-memory rows and tiny epochs (CPU, sub-second
per fit); no real data file is read. The conformance suite at the bottom
is the bar (pipeline/CLAUDE.md step 8): populated trainable probes with a
REAL fixture artifact for ``load_artifact`` and a ``verify_loaded`` that
rejects a fresh fit by provenance.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
from types import SimpleNamespace

import pytest

from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.driver import run_document
from dskit.pipeline.libs.torch import (
    ARTIFACT_FORMAT,
    NODE_KINDS,
    DeclaredPredict,
    DeclaredTrain,
    LinearPredictor,
    LinearRegressor,
    TorchPredict,
    TorchTrain,
    register,
)
from dskit.pipeline.node import NodeContext, NodeKindRegistry, node_class_errors
from dskit.pipeline.planner import plan

torch = pytest.importorskip("torch")

FEATURES = ["x1", "x2"]

#: A valid param set — the same one the conformance probes use.
PARAMS = {
    "features": FEATURES,
    "label": "y",
    "epochs": 2,
    "lr": 0.1,
    "loader": {"batch_size": 8, "shuffle": True, "seed": 11},
}

EXAMPLE = (
    pathlib.Path(__file__).parents[2] / "examples" / "pipeline" / "torch-train.json"
)


def make_rows(n=24):
    """Deterministic linear-ish rows — pure arithmetic, no RNG state."""
    rows = []
    for i in range(n):
        x1 = (i % 7) / 7.0
        x2 = ((i * 5) % 11) / 11.0
        rows.append({"x1": x1, "x2": x2, "y": 0.3 * x1 - 0.2 * x2 + 0.1})
    return rows


def make_inverted_rows(n=24):
    """The OPPOSITE relationship (``y -> 1 - y``): a silent refit on these
    is numerically far from any restore of the ``make_rows()`` fit."""
    return [{**row, "y": 1.0 - row["y"]} for row in make_rows(n)]


#: The row the conformance probes interrogate — the pinned fit answers
#: ~-0.01 here, a refit on the inverted rows ~+0.59 (gap ~0.6).
PROBE_ROW = {"x1": 0.9, "x2": 0.1}


def ctx(tmp_path, sub="run"):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / sub))


def train(tmp_path, *, sub="run", params=None, rows=None, cls=LinearRegressor):
    node = cls("qhat", dict(params if params is not None else PARAMS))
    rows = make_rows() if rows is None else rows
    return node.run(ctx(tmp_path, sub), {"rows": rows})


def sidecar_path(artifact_path):
    return os.path.splitext(artifact_path)[0] + ".json"


def _combined_digest(artifact_path, data):
    """The DOCUMENTED state_hash material, recomputed independently of the
    pack: sha256 over the state-file bytes, a NUL separator, and the
    canonical JSON (sorted keys, compact separators) of every other
    sidecar field."""
    import hashlib

    material = {k: v for k, v in data.items() if k != "state_hash"}
    digest = hashlib.sha256()
    digest.update(pathlib.Path(artifact_path).read_bytes())
    digest.update(b"\0")
    digest.update(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return digest.hexdigest()


def edit_sidecar(artifact_path, rehash=True, **changes):
    """Rewrite sidecar fields. ``rehash=True`` (default) re-records the
    combined digest so the deeper, post-hash defenses stay reachable —
    simulating an adversary who knows the scheme; ``rehash=False`` is the
    plain tamper the hash itself must catch (S2-A)."""
    path = pathlib.Path(sidecar_path(artifact_path))
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    if rehash:
        data["state_hash"] = _combined_digest(artifact_path, data)
    path.write_text(json.dumps(data), encoding="utf-8")


def state_of(artifact_path):
    return torch.load(artifact_path, map_location="cpu", weights_only=True)


def states_equal(a, b):
    return set(a) == set(b) and all(torch.equal(a[k], b[k]) for k in a)


# -- custom families (also prove third-party subclassing works) --------------


class _FixedWidth:
    """A build hook that IGNORES the features param (fixed 2-wide input)."""

    def build_module(self, params):
        import torch as _torch

        return _torch.nn.Linear(2, 1)


class FixedWidthTrain(_FixedWidth, TorchTrain):
    """Trainer of the fixed-width family."""


class FixedWidthPredict(_FixedWidth, TorchPredict):
    """Its inference twin — shares the mixin's build_module."""


class L1Regressor(LinearRegressor):
    """The loss hook exercised: same module, L1 objective."""

    def loss(self, module, batch):
        import torch as _torch

        x, y = batch
        return _torch.nn.functional.l1_loss(module(x).reshape(-1), y)


# -- params: default-deny, loader block, value checks -------------------------


@pytest.mark.parametrize("cls", [LinearRegressor, LinearPredictor])
def test_unknown_top_level_knob_is_refused_by_name(cls):
    base = dict(PARAMS) if cls is LinearRegressor else {}
    problems = cls.validate_params({**base, "warm_start": True})
    assert any("warm_start" in p for p in problems)


@pytest.mark.parametrize(
    "key", ["num_workers", "pin_memory", "drop_last", "batch_sizes"]
)
def test_loader_block_default_denies_inside_by_name(key):
    # The I-227 territory: a typo (or an unsupported docs/24 knob) INSIDE
    # the nested block must be refused by name, not silently ignored.
    params = {**PARAMS, "loader": {**PARAMS["loader"], key: 1}}
    problems = LinearRegressor.validate_params(params)
    assert any(key in p and "loader" in p for p in problems)


@pytest.mark.parametrize("loader", ["big", 3, [8], None])
def test_loader_must_be_a_dict(loader):
    problems = LinearRegressor.validate_params({**PARAMS, "loader": loader})
    assert any("loader" in p for p in problems)


@pytest.mark.parametrize(
    ("block", "needle"),
    [
        ({"batch_size": 0}, "batch_size"),
        ({"batch_size": True}, "batch_size"),
        ({"shuffle": "yes"}, "shuffle"),
        ({"seed": -1}, "seed"),
        ({"seed": True}, "seed"),
    ],
)
def test_loader_value_checks(block, needle):
    problems = LinearRegressor.validate_params({**PARAMS, "loader": block})
    assert any(needle in p for p in problems)


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"epochs": 0}, "epochs"),
        ({"epochs": True}, "epochs"),
        ({"lr": 0}, "lr"),
        ({"lr": "fast"}, "lr"),
        ({"lr": float("nan")}, "lr"),
        ({"features": []}, "features"),
        ({"features": [1]}, "features"),
        ({"features": "mid"}, "features"),
        ({"label": ""}, "label"),
        ({"label": 7}, "label"),
    ],
)
def test_train_value_checks(override, needle):
    problems = LinearRegressor.validate_params({**PARAMS, **override})
    assert any(needle in p for p in problems)


def test_features_are_required_for_train_but_not_predict():
    dropped = {k: v for k, v in PARAMS.items() if k != "features"}
    assert any("features" in p for p in LinearRegressor.validate_params(dropped))
    assert LinearPredictor.validate_params({}) == []


@pytest.mark.parametrize("artifact", ["", 5, True])
def test_predict_artifact_param_checks(artifact):
    problems = LinearPredictor.validate_params({"artifact": artifact})
    assert any("artifact" in p for p in problems)


def test_the_reference_params_validate_clean():
    assert LinearRegressor.validate_params(dict(PARAMS)) == []
    assert (
        LinearPredictor.validate_params(
            {"artifact": "model.pt", "features": FEATURES, "label": "y"}
        )
        == []
    )


# -- inputs: one-shot refusal, load mode --------------------------------------


def test_train_refuses_a_one_shot_rows_iterable_without_consuming_it():
    node = LinearRegressor("qhat", dict(PARAMS))
    seen = []

    def one_shot():
        for row in make_rows():
            seen.append(row)
            yield row

    problems = node.validate_inputs({"rows": one_shot()})
    assert problems and any("rows" in p and "LIST" in p for p in problems)
    assert seen == []  # refused BY NAME, not by walking it


def test_train_refuses_missing_rows_but_not_under_load():
    node = LinearRegressor("qhat", dict(PARAMS))
    assert node.validate_inputs({}) != []
    loaded = LinearRegressor("qhat", dict(PARAMS), mode="load", artifact="x.pt")
    assert loaded.validate_inputs({}) == []


def test_predict_input_wire_checks():
    node = LinearPredictor("sig", {})
    assert node.validate_inputs({"artifact_path": 5}) != []
    assert node.validate_inputs({}) == []


def test_a_non_list_val_rows_wire_is_refused_by_name():
    # Salvaged (2026-08-25 merge) from the superseded parallel suite: the
    # ported loop has the same wire rule and no test pinned it.
    node = LinearRegressor("qhat", dict(PARAMS))
    problems = node.validate_inputs({"rows": [], "val_rows": iter([])})
    assert any("val_rows must be a LIST" in p for p in problems)
    assert node.validate_inputs({"rows": [], "val_rows": []}) == []


# -- training: contract, artifact, signal -------------------------------------


def test_train_returns_the_contract_and_saves_a_verifiable_artifact(tmp_path):
    out = train(tmp_path)
    assert set(out) == {"signal", "artifact_path", "metrics"}
    artifact = out["artifact_path"]
    assert os.path.isfile(artifact)
    sidecar = json.loads(
        pathlib.Path(sidecar_path(artifact)).read_text(encoding="utf-8")
    )
    assert sidecar["format"] == ARTIFACT_FORMAT
    assert sidecar["seed"] == 11
    assert sidecar["module_class"].endswith(":LinearRegressor")
    assert sidecar["params"]["features"] == FEATURES
    # RE-PINNED (S2-A): state_hash covers the state bytes AND the sidecar,
    # not the bytes alone. Hand-verified — _combined_digest recomputes it
    # from the DOCUMENTED material (module docstring), independently of the
    # pack's own helper.
    assert sidecar["state_hash"] == _combined_digest(artifact, sidecar)
    assert out["metrics"]["n_rows"] == 24
    assert out["metrics"]["n_skipped"] == 0
    assert out["metrics"]["seed"] == 11
    assert math.isfinite(out["metrics"]["final_loss"])


def test_signal_predicts_on_dicts_and_records_and_declines_gaps(tmp_path):
    out = train(tmp_path)
    signal = out["signal"]
    assert signal.loaded is False
    assert signal.artifact_path == out["artifact_path"]
    p = signal.predict({"x1": 0.5, "x2": 0.5})
    assert p is not None and math.isfinite(p)
    assert signal.predict(SimpleNamespace(x1=0.5, x2=0.5)) == pytest.approx(p)
    assert signal.predict({"x1": 0.5}) is None  # missing feature
    assert signal.predict({"x1": 0.5, "x2": float("nan")}) is None
    assert signal.predict({"x1": 0.5, "x2": "high"}) is None
    assert signal.predict({"x1": 0.5, "x2": True}) is not None  # bool = 0/1


def test_fit_reads_a_dict_feature_literally_named_items(tmp_path):
    """S2-B: attr-first lookup resolved a dict feature named ``items`` to
    the bound ``dict.items`` method, so every row was skipped and the fit
    failed loud ('no usable rows'). Mapping keys win over attribute names
    on mapping rows — the value trains."""
    rows = [{"items": r["x1"], "x2": r["x2"], "y": r["y"]} for r in make_rows()]
    params = {**PARAMS, "features": ["items", "x2"]}
    out = train(tmp_path, params=params, rows=rows)
    assert out["metrics"]["n_rows"] == 24
    assert out["metrics"]["n_skipped"] == 0
    p = out["signal"].predict({"items": 0.4, "x2": 0.6})
    assert p is not None and math.isfinite(p)


def test_signal_reads_a_mapping_key_that_shadows_a_dict_method(tmp_path):
    """S2-B, the silent half: on predict the method-shadowed keys read as
    None — no coverage where the record plainly carries values. The VALUE
    must be read, and a genuinely absent key must still decline."""
    from dskit.pipeline.libs.torch import TorchSignal

    module = train(tmp_path)["signal"].module
    signal = TorchSignal(module, ["items", "keys"], "art.pt", loaded=False)
    p = signal.predict({"items": 0.4, "keys": 0.6})
    assert p is not None and math.isfinite(p)
    assert signal.predict({"items": 0.4}) is None  # absent key still declines


def test_rows_with_gaps_are_skipped_and_counted_never_fabricated(tmp_path):
    rows = make_rows() + [
        {"x1": 0.1, "y": 0.2},  # missing x2
        {"x1": 0.1, "x2": float("inf"), "y": 0.2},  # non-finite
        {"x1": 0.1, "x2": 0.2, "y": None},  # no label
    ]
    out = train(tmp_path, rows=rows)
    assert out["metrics"]["n_rows"] == 24
    assert out["metrics"]["n_skipped"] == 3


def test_all_rows_unusable_is_a_loud_failure(tmp_path):
    with pytest.raises(ValueError, match="no usable rows"):
        train(tmp_path, rows=[{"x1": 0.1}, {"wrong": 1}])


def test_bool_labels_train_as_zero_one(tmp_path):
    rows = [{"x1": r["x1"], "x2": r["x2"], "y": r["y"] > 0.1} for r in make_rows()]
    out = train(tmp_path, rows=rows)
    assert out["metrics"]["n_rows"] == 24


def test_shuffle_off_still_trains(tmp_path):
    params = {**PARAMS, "loader": {"batch_size": 8, "shuffle": False, "seed": 11}}
    out = train(tmp_path, params=params)
    assert math.isfinite(out["metrics"]["final_loss"])


def test_custom_loss_hook_changes_the_fit(tmp_path):
    mse = train(tmp_path, sub="mse")
    l1 = train(tmp_path, sub="l1", cls=L1Regressor)
    assert not states_equal(
        state_of(mse["artifact_path"]), state_of(l1["artifact_path"])
    )


# -- determinism ---------------------------------------------------------------


def test_same_seed_produces_identical_state_dicts(tmp_path):
    first = train(tmp_path, sub="a")
    second = train(tmp_path, sub="b")
    assert states_equal(
        state_of(first["artifact_path"]), state_of(second["artifact_path"])
    )
    assert first["metrics"]["final_loss"] == second["metrics"]["final_loss"]


def test_different_seeds_differ(tmp_path):
    first = train(tmp_path, sub="a")
    params = {**PARAMS, "loader": {**PARAMS["loader"], "seed": 12}}
    second = train(tmp_path, sub="b", params=params)
    assert not states_equal(
        state_of(first["artifact_path"]), state_of(second["artifact_path"])
    )


# -- mode="load" on the trainer: really loads, refuses by name ----------------


def test_load_mode_really_restores_and_never_refits(tmp_path):
    trained = train(tmp_path)
    artifact = trained["artifact_path"]
    node = LinearRegressor("qhat", dict(PARAMS), mode="load", artifact=artifact)
    # No rows at all: a refit would crash, a restore does not need them.
    out = node.run(ctx(tmp_path, "load-run"), {})
    assert set(out) == {"signal", "artifact_path", "metrics"}
    assert out["artifact_path"] == artifact
    assert out["signal"].loaded is True
    assert out["metrics"] == {"loaded": 1, "seed": 11}
    row = {"x1": 0.3, "x2": 0.7}
    assert out["signal"].predict(row) == pytest.approx(trained["signal"].predict(row))


def refuses(node, tmp_path, match, inputs=None):
    with pytest.raises(ValueError, match=match):
        node.run(ctx(tmp_path, "refusal-run"), inputs or {})


def loader_node(artifact, params=None):
    return LinearRegressor(
        "qhat",
        dict(params if params is not None else PARAMS),
        mode="load",
        artifact=artifact,
    )


def test_load_refuses_an_empty_or_missing_artifact(tmp_path):
    refuses(loader_node(""), tmp_path, "artifact")
    refuses(loader_node(str(tmp_path / "nope.pt")), tmp_path, "does not exist")


def test_load_refuses_a_missing_sidecar(tmp_path):
    artifact = train(tmp_path)["artifact_path"]
    os.remove(sidecar_path(artifact))
    refuses(loader_node(artifact), tmp_path, "sidecar")


def test_load_refuses_a_corrupt_state_file_by_content_hash(tmp_path):
    artifact = train(tmp_path)["artifact_path"]
    with open(artifact, "ab") as fh:
        fh.write(b"tampered")
    refuses(loader_node(artifact), tmp_path, "hash mismatch")


def test_a_reordered_sidecar_feature_order_refuses_instead_of_transposing(tmp_path):
    """S2-A (torch): the predict doorway serves the SIDECAR's feature
    order when the node declares none — pre-fix a reordered sidecar
    silently transposed every vector. The sidecar is hash material now,
    so the unrehashed edit refuses at load like a state-file edit."""
    artifact = train(tmp_path)["artifact_path"]
    edit_sidecar(artifact, rehash=False, params={**PARAMS, "features": ["x2", "x1"]})
    node = LinearPredictor("sig", {})
    with pytest.raises(ValueError, match="hash mismatch"):
        node.run(ctx(tmp_path, "sig-run"), {"artifact_path": artifact})


@pytest.mark.parametrize(
    "changes", [{"seed": 999}, {"params": {**PARAMS, "epochs": 99}}]
)
def test_sidecar_identity_edits_break_the_recorded_hash(tmp_path, changes):
    """S2-A (torch): seed and params are the identity the restore REPORTS
    (metrics.seed, the rebuilt module) — an unrehashed edit to either
    refuses on the recorded hash instead of riding through."""
    artifact = train(tmp_path)["artifact_path"]
    edit_sidecar(artifact, rehash=False, **changes)
    refuses(loader_node(artifact), tmp_path, "hash mismatch")


def test_load_refuses_unparseable_or_non_object_sidecars(tmp_path):
    artifact = train(tmp_path, sub="a")["artifact_path"]
    pathlib.Path(sidecar_path(artifact)).write_text("{not json", encoding="utf-8")
    refuses(loader_node(artifact), tmp_path, "not JSON")
    other = train(tmp_path, sub="b")["artifact_path"]
    pathlib.Path(sidecar_path(other)).write_text("[1, 2]", encoding="utf-8")
    refuses(loader_node(other), tmp_path, "JSON object")


def test_load_refuses_missing_sidecar_keys_and_alien_formats(tmp_path):
    artifact = train(tmp_path, sub="a")["artifact_path"]
    data = json.loads(pathlib.Path(sidecar_path(artifact)).read_text(encoding="utf-8"))
    del data["seed"]
    pathlib.Path(sidecar_path(artifact)).write_text(json.dumps(data), encoding="utf-8")
    refuses(loader_node(artifact), tmp_path, "lacks key")
    other = train(tmp_path, sub="b")["artifact_path"]
    edit_sidecar(other, format="somebody-elses-v9")
    refuses(loader_node(other), tmp_path, "format")


def test_load_refuses_unresolvable_or_malformed_class_refs(tmp_path):
    artifact = train(tmp_path, sub="a")["artifact_path"]
    edit_sidecar(artifact, module_class="not a ref")
    refuses(loader_node(artifact), tmp_path, "importable class")
    other = train(tmp_path, sub="b")["artifact_path"]
    edit_sidecar(other, module_class="no.such.module:Nope")
    refuses(loader_node(other), tmp_path, "unresolvable")


def test_load_refuses_a_different_module_family_by_name(tmp_path):
    artifact = train(tmp_path)["artifact_path"]
    edit_sidecar(
        artifact, module_class="tests.pipeline_libs.test_torch:FixedWidthTrain"
    )
    refuses(loader_node(artifact), tmp_path, "build_module is not")


def test_load_refuses_a_shape_param_mismatch_by_name(tmp_path):
    artifact = train(tmp_path)["artifact_path"]
    params = {**PARAMS, "features": ["x1"]}
    refuses(loader_node(artifact, params=params), tmp_path, "mismatch on 'features'")


def test_load_refuses_non_dict_sidecar_params(tmp_path):
    artifact = train(tmp_path)["artifact_path"]
    edit_sidecar(artifact, params="nope")
    refuses(loader_node(artifact), tmp_path, "params are not a dict")


def test_load_refuses_a_state_that_does_not_fit_the_module(tmp_path):
    artifact = train(tmp_path)["artifact_path"]
    # Sidecar and node agree on a 1-wide module; the saved state is 2-wide.
    edit_sidecar(artifact, params={"features": ["x1"], "label": "y"})
    params = {**PARAMS, "features": ["x1"]}
    refuses(loader_node(artifact, params=params), tmp_path, "does not restore")


# -- the predict doorway -------------------------------------------------------


def test_predict_loads_via_the_artifact_path_wire(tmp_path):
    trained = train(tmp_path)
    node = LinearPredictor("sig", {})
    out = node.run(
        ctx(tmp_path, "sig-run"), {"artifact_path": trained["artifact_path"]}
    )
    assert set(out) == {"signal"}
    assert out["signal"].loaded is True
    assert out["signal"].artifact_path == trained["artifact_path"]
    row = {"x1": 0.3, "x2": 0.7}
    assert out["signal"].predict(row) == pytest.approx(trained["signal"].predict(row))


def test_predict_loads_via_params_and_via_mode_load(tmp_path):
    artifact = train(tmp_path)["artifact_path"]
    by_params = LinearPredictor("sig", {"artifact": artifact})
    assert by_params.run(ctx(tmp_path, "p1"), {})["signal"].loaded is True
    pinned = LinearPredictor("sig", {}, mode="load", artifact=artifact)
    assert pinned.run(ctx(tmp_path, "p2"), {})["signal"].loaded is True


def test_predict_cross_checks_declared_features_against_the_sidecar(tmp_path):
    artifact = train(tmp_path)["artifact_path"]
    good = LinearPredictor("sig", {"artifact": artifact, "features": FEATURES})
    assert good.run(ctx(tmp_path, "ok"), {})["signal"].features == tuple(FEATURES)
    bad = LinearPredictor("sig", {"artifact": artifact, "features": ["x1"]})
    refuses(bad, tmp_path, "mismatch on 'features'")


def test_predict_refuses_mode_train_and_missing_references(tmp_path):
    node = LinearPredictor("sig", {}, mode="train")
    with pytest.raises(NotImplementedError, match="inference-only"):
        node.run(ctx(tmp_path, "t"), {})
    refuses(LinearPredictor("sig", {}), tmp_path, "no artifact reference")
    empty = LinearPredictor("sig", {}, mode="load", artifact="")
    refuses(empty, tmp_path, "empty artifact reference")


def test_predict_refuses_a_sidecar_with_no_features_for_the_signal(tmp_path):
    trained = train(tmp_path, cls=FixedWidthTrain)
    artifact = trained["artifact_path"]
    edit_sidecar(artifact, params={"label": "y"})  # features gone; module builds
    node = FixedWidthPredict("sig", {"artifact": artifact})
    refuses(node, tmp_path, "records no features")


def test_predict_refuses_an_artifact_from_another_family(tmp_path):
    artifact = train(tmp_path, cls=FixedWidthTrain)["artifact_path"]
    node = LinearPredictor("sig", {"artifact": artifact})
    refuses(node, tmp_path, "build_module is not")


# -- registration and the abstract bases ---------------------------------------


def test_register_is_explicit_and_idempotent():
    registry = NodeKindRegistry()
    register(registry)
    register(registry)  # idempotent: present names are skipped, never shadowed
    assert registry.kinds() == (
        "torch-linear-predict",
        "torch-linear-train",
        "torch-predict",
        "torch-train",
    )
    assert registry.get("torch-linear-train") == (LinearRegressor, False)
    assert registry.get("torch-linear-predict") == (LinearPredictor, False)
    assert registry.get("torch-train") == (DeclaredTrain, False)
    assert registry.get("torch-predict") == (DeclaredPredict, False)


def test_abstract_bases_stay_out_of_the_kind_table():
    assert dict(NODE_KINDS) == {
        "torch-train": DeclaredTrain,
        "torch-predict": DeclaredPredict,
        "torch-linear-train": LinearRegressor,
        "torch-linear-predict": LinearPredictor,
    }
    for base in (TorchTrain, TorchPredict):
        problems = node_class_errors(base, "torch pack")
        assert any("abstract" in p for p in problems)


# -- the DECLARED family: an architecture named by the document -----------------

#: The declared counterpart of PARAMS — same fit, but the architecture is
#: config, not a Python subclass.
DECLARED_PARAMS = {
    **PARAMS,
    "module": "torch.nn.Linear",
    "module_params": {"in_features": len(FEATURES), "out_features": 1},
}


def test_declared_module_fits_the_architecture_the_params_name(tmp_path):
    out = train(tmp_path, params=DECLARED_PARAMS, cls=DeclaredTrain)
    prediction = out["signal"].predict(PROBE_ROW)
    assert prediction is not None and math.isfinite(prediction)


def test_declared_module_accepts_both_path_spellings(tmp_path):
    colon = train(
        tmp_path,
        sub="colon",
        params={**DECLARED_PARAMS, "module": "torch.nn:Linear"},
        cls=DeclaredTrain,
    )
    dotted = train(
        tmp_path, sub="dotted", params=DECLARED_PARAMS, cls=DeclaredTrain
    )
    assert colon["signal"].predict(PROBE_ROW) == pytest.approx(
        dotted["signal"].predict(PROBE_ROW)
    )


def test_a_missing_module_is_refused_at_plan():
    problems = DeclaredTrain.validate_params(dict(PARAMS))
    assert any("module is required" in p for p in problems)


def test_a_malformed_module_path_is_refused_at_plan():
    problems = DeclaredTrain.validate_params(
        {**DECLARED_PARAMS, "module": "notapath"}
    )
    assert any("module must name a class" in p for p in problems)


def test_a_non_dict_module_params_is_refused_at_plan():
    problems = DeclaredTrain.validate_params(
        {**DECLARED_PARAMS, "module_params": [1, 2]}
    )
    assert any("module_params must be a dict" in p for p in problems)


def test_non_string_module_params_keys_are_refused_at_plan():
    # Salvaged (2026-08-25 merge) from the superseded parallel suite: a
    # dict with a non-string key is not constructor kwargs either.
    problems = DeclaredTrain.validate_params(
        {**DECLARED_PARAMS, "module_params": {1: 2}}
    )
    assert any("module_params must be a dict" in p for p in problems)


def test_a_bad_constructor_knob_is_refused_by_the_constructor(tmp_path):
    with pytest.raises(ValueError, match="rejected module_params"):
        train(
            tmp_path,
            params={**DECLARED_PARAMS, "module_params": {"nope": 1}},
            cls=DeclaredTrain,
        )


def test_a_different_declared_architecture_cannot_restore_the_artifact(tmp_path):
    """The class-identity check cannot separate two DECLARED families (they
    share one build_module), so ``module``/``module_params`` are the values
    the sidecar cross-check refuses on."""
    fitted = train(tmp_path, params=DECLARED_PARAMS, cls=DeclaredTrain)
    other = DeclaredTrain(
        "qhat",
        {**DECLARED_PARAMS, "module_params": {"in_features": 2, "out_features": 2}},
        mode="load",
        artifact=fitted["artifact_path"],
    )
    with pytest.raises(ValueError, match="sidecar mismatch on 'module_params'"):
        other.run(ctx(tmp_path, sub="reload"), {})


# -- the shipped example: loads, hashes, plans, runs ----------------------------

EXPECTED_ORDER = ("market", "qhat", "predict")


def test_example_loads_and_hashes_stably():
    doc = load_document(str(EXAMPLE))
    assert doc.name == "torch-linear-demo"
    assert tuple(doc.pipeline) == EXPECTED_ORDER
    assert doc.hash == load_document(str(EXAMPLE)).hash


def test_example_plans_so_every_reference_is_real():
    resolved = plan(load_document(str(EXAMPLE)))
    assert tuple(resolved.order) == EXPECTED_ORDER
    assert resolved.role_of("market") == "data"
    assert resolved.role_of("qhat") == "train"
    assert resolved.role_of("predict") == "signal"


def test_example_runs_end_to_end_and_the_predictor_restores_the_fit(tmp_path):
    obj = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    obj["outputs"]["run_root"] = str(tmp_path / "runs")
    result = run_document(PipelineDocument.from_obj(obj), asof="2026-01-01")
    assert result.error == ""
    assert result.state == "ran"
    assert result.exit_code == 0
    assert set(result.node_states) == set(EXPECTED_ORDER)
    assert set(result.node_states.values()) == {"ok"}
    signal = result.outputs["predict"]["signal"]
    assert signal.loaded is True
    assert signal.artifact_path == result.outputs["qhat"]["artifact_path"]
    row = result.outputs["market"]["events"][0]
    p = signal.predict(row)
    assert p is not None and math.isfinite(p)
    trained = result.outputs["qhat"]["signal"]
    assert p == pytest.approx(trained.predict(row))


DECLARED_EXAMPLE = (
    pathlib.Path(__file__).parents[2] / "examples" / "pipeline" / "torch-declared.json"
)


def test_declared_example_loads_hashes_and_runs(tmp_path):
    # Salvaged (2026-08-25 merge) from the superseded parallel suite and
    # adapted to the ported grammar: the shipped DECLARED example must stay
    # loadable, hash-stable, and runnable end-to-end.
    doc = load_document(str(DECLARED_EXAMPLE))
    assert doc.name == "torch-declared-demo"
    assert doc.hash == load_document(str(DECLARED_EXAMPLE)).hash
    obj = json.loads(DECLARED_EXAMPLE.read_text(encoding="utf-8"))
    obj["outputs"]["run_root"] = str(tmp_path / "runs")
    result = run_document(PipelineDocument.from_obj(obj), asof="2026-01-01")
    assert result.error == ""
    assert result.state == "ran"
    signal = result.outputs["predict"]["signal"]
    assert signal.loaded is True
    row = result.outputs["market"]["events"][0]
    assert signal.predict(row) == pytest.approx(
        result.outputs["qhat"]["signal"].predict(row)
    )


# -- the conformance suite (pipeline/CLAUDE.md step 8) ---------------------------

EXPECTED_ROLES = {
    "torch-train": "train",
    "torch-predict": "signal",
    "torch-linear-train": "train",
    "torch-linear-predict": "signal",
}


def _fixture_artifact(tmp_path):
    """A REAL trained fit under tmp_path — what mode='load' must restore
    (the whole output, so the probes can pin its PREDICTION, not just its
    paperwork)."""
    node = LinearRegressor("fixture_train", dict(PARAMS))
    return node.run(
        NodeContext(
            name="fixture", asof="2026-01-01", run_dir=str(tmp_path / "fixture-run")
        ),
        {"rows": make_rows()},
    )


def test_probe_rows_make_a_silent_refit_numerically_distinguishable(tmp_path):
    """Pins the S2-C bar behind the probes: ``verify_loaded`` accepts only
    a prediction within 1e-9 of the pinned fit, and a refit on the probes'
    inverted rows lands ~0.6 away at PROBE_ROW — the NUMBER, not the
    paperwork, is what discriminates a restore from a silent refit."""
    pinned = train(tmp_path, sub="pin")
    refit = train(tmp_path, sub="refit", rows=make_inverted_rows())
    gap = abs(pinned["signal"].predict(PROBE_ROW) - refit["signal"].predict(PROBE_ROW))
    assert gap > 0.05


def probes(tmp_path):
    """The fixture artifact is a REAL fit on ``make_rows()``; the probes'
    wired rows carry the OPPOSITE relationship (``y -> 1 - y``), so
    ``verify_loaded`` discriminates a restore from a silent refit on the
    PREDICTION itself (S2-C) — provenance alone was paperwork a refit
    could counterfeit by echoing the pinned path and flag."""
    fixture = _fixture_artifact(tmp_path)
    artifact = fixture["artifact_path"]
    expected = fixture["signal"].predict(PROBE_ROW)

    # The DECLARED family needs its own fixture: its sidecar records
    # module/module_params, which the linear fixture's does not carry, and a
    # load cross-checks them.
    declared_fixture = DeclaredTrain("fixture_declared", dict(DECLARED_PARAMS)).run(
        NodeContext(
            name="fixture",
            asof="2026-01-01",
            run_dir=str(tmp_path / "fixture-declared"),
        ),
        {"rows": make_rows()},
    )
    declared_artifact = declared_fixture["artifact_path"]
    declared_expected = declared_fixture["signal"].predict(PROBE_ROW)

    def restored(out):
        signal = out["signal"]
        prediction = signal.predict(PROBE_ROW)
        return (
            bool(getattr(signal, "loaded", False))
            and signal.artifact_path == artifact
            and prediction is not None
            and abs(prediction - expected) < 1e-9
        )

    def declared_restored(out):
        signal = out["signal"]
        prediction = signal.predict(PROBE_ROW)
        return (
            bool(getattr(signal, "loaded", False))
            and signal.artifact_path == declared_artifact
            and prediction is not None
            and abs(prediction - declared_expected) < 1e-9
        )

    return {
        "torch-train": NodeProbe(
            params=dict(DECLARED_PARAMS),
            required=("features", "module"),
            inputs={"rows": make_inverted_rows()},
            stream_ports=("rows",),
            runnable=True,
            load_artifact=declared_artifact,
            verify_loaded=lambda out: (
                declared_restored(out)
                and out["artifact_path"] == declared_artifact
            ),
        ),
        "torch-predict": NodeProbe(
            # Inference knobs only — the predict node refuses epochs/lr/loader
            # by name, and it must: they are history, not shape.
            params={
                "features": FEATURES,
                "label": "y",
                "module": DECLARED_PARAMS["module"],
                "module_params": dict(DECLARED_PARAMS["module_params"]),
            },
            inputs={"artifact_path": declared_artifact},
            stream_ports=(),
            runnable=True,
            load_artifact=declared_artifact,
            verify_loaded=declared_restored,
        ),
        "torch-linear-train": NodeProbe(
            params=dict(PARAMS),
            required=("features",),
            inputs={"rows": make_inverted_rows()},
            stream_ports=("rows",),
            runnable=True,
            load_artifact=artifact,
            # The NUMBER discriminates (a refit on the inverted rows lands
            # ~0.6 away); the artifact_path check is provenance on top.
            verify_loaded=lambda out: (
                restored(out) and out["artifact_path"] == artifact
            ),
        ),
        "torch-linear-predict": NodeProbe(
            params={},
            inputs={"artifact_path": artifact},
            stream_ports=(),
            runnable=True,
            load_artifact=artifact,
            verify_loaded=restored,
        ),
    }


def test_the_probes_reject_a_counterfeit_that_echoes_the_pinned_provenance(tmp_path):
    """S2-C: the provenance-only ``verify_loaded`` blessed ANY output that
    echoed the pinned path and the loaded flag — a silently refit module
    included. Reading the NUMBER rejects the counterfeit."""
    probe = probes(tmp_path)["torch-linear-train"]
    artifact = probe.load_artifact
    counterfeit = train(tmp_path, sub="counterfeit", rows=make_inverted_rows())[
        "signal"
    ]
    counterfeit.artifact_path = artifact  # echoes the pin...
    counterfeit.loaded = True  # ...and the flag; the weights are a refit
    assert not probe.verify_loaded(
        {"signal": counterfeit, "artifact_path": artifact, "metrics": {"loaded": 1}}
    )


TestTorchConformance = conformance_suite(
    registry=NODE_KINDS,
    module="dskit.pipeline.libs.torch",
    probes=probes,
    expected_roles=EXPECTED_ROLES,
    name="TestTorchConformance",
)
