"""The sklearn library pack: unit + real-library integration + conformance.

This module IMPORTS without scikit-learn installed — every test that
needs the real library (and the conformance probe factory) calls
``pytest.importorskip("sklearn")`` at its top, so the unit half of the
file keeps running on a dependency-less machine, exactly like the pack
itself keeps planning there.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
from types import SimpleNamespace

import numpy as np
import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.driver import run_document
from dskit.pipeline.fitted import (
    SIDECAR_NAME,
    ApplyTransform,
    FeatureSelector,
    FittedTransform,
    TransformCarrier,
)
from dskit.pipeline.libs.sklearn import (
    NODE_KINDS,
    SEGMENT_SCHEMA,
    ColumnSubsetEstimator,
    _SEGMENT_PATHS,
    SklearnFit,
    SklearnPredict,
    SklearnSegment,
    SklearnSelect,
    SklearnSignal,
    register,
)
from dskit.pipeline.node import NodeContext, NodeKindRegistry, class_ref
from dskit.pipeline.planner import plan
from dskit.pipeline.split_policy import SPLIT_NAMES

ASOF = "2026-01-01"

RIDGE = "sklearn.linear_model.Ridge"

#: The canonical fit params every round-trip test shares — Ridge accepts
#: random_state, so the seed threading is exercised on the happy path.
FIT_PARAMS = {
    "estimator": RIDGE,
    "estimator_params": {"alpha": 1e-6},
    "features": ["x"],
    "label": "y",
    "seed": 7,
}

#: A probe row far from the inverted relationship's predictions.
SAMPLE = {"x": 0.9}

EXAMPLE = (
    pathlib.Path(__file__).parents[2] / "examples" / "pipeline" / "sklearn-fit.json"
)


def rows_linear(slope=1.0, intercept=0.0, n=9):
    """Deterministic in-memory rows: ``y = intercept + slope * x``."""
    return [{"x": 0.1 * i, "y": intercept + slope * 0.1 * i} for i in range(n)]


def rows_inverted(n=9):
    """The opposite relationship (``y = 1 - x``) — a fresh fit on these
    is distinguishable from a restore of the ``y = x`` artifact."""
    return rows_linear(slope=-1.0, intercept=1.0, n=n)


def _ctx(tmp_path, name="run"):
    return NodeContext(name="t", asof=ASOF, run_dir=str(tmp_path / name))


def _fit(tmp_path, *, params=None, rows=None, run_name="fitrun"):
    pytest.importorskip("sklearn")
    node = SklearnFit("fit", dict(params or FIT_PARAMS))
    rows = rows_linear() if rows is None else rows
    return node.run(_ctx(tmp_path, run_name), {"rows": rows})


# ---------------------------------------------------------------------------
# Registration (no sklearn needed)
# ---------------------------------------------------------------------------


def test_node_kinds_table_and_roles():
    table = dict(NODE_KINDS)
    assert table == {
        "sklearn-fit": SklearnFit,
        "sklearn-predict": SklearnPredict,
        "sklearn-select": SklearnSelect,
        "sklearn-segment": SklearnSegment,
    }
    assert SklearnFit.role == "train"
    assert SklearnFit.outputs == ("signal", "artifact_path", "metrics")
    assert SklearnPredict.role == "signal"
    assert SklearnPredict.outputs == ("signal",)


def test_register_is_explicit_and_idempotent():
    registry = NodeKindRegistry()
    register(registry)
    register(registry)  # second call skips, never raises or shadows
    for kind, _cls in NODE_KINDS:
        assert kind in registry
    cls, owned = registry.get("sklearn-fit")
    assert cls is SklearnFit and owned is False


# ---------------------------------------------------------------------------
# validate_params (no sklearn needed — plan-time must not touch the library)
# ---------------------------------------------------------------------------


def test_fit_params_canonical_set_validates_clean():
    assert SklearnFit.validate_params(dict(FIT_PARAMS)) == []


def test_fit_params_core_knobs_are_required():
    problems = SklearnFit.validate_params({})
    text = " ".join(problems)
    for knob in ("estimator", "features", "label"):
        assert knob in text, problems


def test_fit_params_unknown_keys_refused_by_name():
    problems = SklearnFit.validate_params({**FIT_PARAMS, "warm_start": True})
    assert any("warm_start" in p for p in problems)


@pytest.mark.parametrize(
    "knob,value,needle",
    [
        ("estimator", "Ridge", "dotted import path"),
        ("estimator", "sklearn..Ridge", "dotted import path"),
        ("estimator", "1bad.Thing", "dotted import path"),
        # The COLON form. Both the pack docstring and the model-sweep
        # cookbook tell readers a colon is refused; TODO.md's own
        # model-selection items spell it the other way
        # ("lightgbm:LGBMRegressor"). Two claims, opposite signs — so the
        # engine's answer is pinned here rather than left to prose.
        ("estimator", "lightgbm:LGBMRegressor", "dotted import path"),
        ("estimator", "sklearn.ensemble:RandomForestRegressor", "dotted import path"),
        ("estimator", "", "dotted import path"),
        ("estimator", 5, "dotted import path"),
        ("features", [], "non-empty list"),
        ("features", "x", "non-empty list"),
        ("features", {"x": 1}, "non-empty list"),
        ("features", ["x", ""], "non-empty strings"),
        ("features", ["x", "x"], "distinct"),
        ("label", "", "label"),
        ("label", 0, "label"),
        ("seed", True, "seed"),
        ("seed", -1, "seed"),
        ("seed", 2**32, "seed"),
        ("seed", "7", "seed"),
        ("seed", None, "seed"),
        ("estimator_params", [1], "estimator_params"),
        ("estimator_params", "alpha=1", "estimator_params"),
        ("estimator_params", {1: 2}, "estimator_params"),
        ("predict_method", "proba", "predict_method"),
        ("predict_method", [], "predict_method"),
    ],
)
def test_fit_params_junk_refused_by_name(knob, value, needle):
    problems = SklearnFit.validate_params({**FIT_PARAMS, knob: value})
    assert any(needle in p for p in problems), (knob, value, problems)


def test_fit_params_seed_and_random_state_cannot_both_be_set():
    params = {**FIT_PARAMS, "estimator_params": {"random_state": 3}}
    problems = SklearnFit.validate_params(params)
    assert any("one source of truth" in p for p in problems)


def test_predict_params_artifact_required_and_shaped():
    assert SklearnPredict.validate_params({"artifact": "runs/model.joblib"}) == []
    assert any("artifact" in p for p in SklearnPredict.validate_params({}))
    for junk in ("", 5, None, ["a"]):
        problems = SklearnPredict.validate_params({"artifact": junk})
        assert any("artifact" in p for p in problems), junk
    problems = SklearnPredict.validate_params({"artifact": "x", "mode": "load"})
    assert any("mode" in p for p in problems)


def test_validators_are_total_on_junk():
    """A quick local sweep; the conformance fuzz below hammers deeper."""
    junk = ["1,000", float("nan"), 1e308, True, None, [], {}, {"nested": 1}, ""]
    for cls, base in ((SklearnFit, FIT_PARAMS), (SklearnPredict, {"artifact": "a/b"})):
        for knob in (*base, "unknown_junk_knob"):
            for value in junk:
                problems = cls.validate_params({**base, knob: value})
                assert isinstance(problems, list)
                assert all(isinstance(p, str) for p in problems)


# ---------------------------------------------------------------------------
# validate_inputs (no sklearn needed)
# ---------------------------------------------------------------------------


def test_fit_inputs_refuse_a_one_shot_iterable_without_consuming_it():
    node = SklearnFit("fit", dict(FIT_PARAMS))
    seen = []

    def one_shot():
        for row in rows_linear():
            seen.append(row)
            yield row

    problems = node.validate_inputs({"rows": one_shot()})
    assert problems and "one-shot" in problems[0]
    assert seen == []  # refused BY NAME, not by walking it


def test_fit_inputs_accept_a_list_and_allow_absent_rows_under_load():
    node = SklearnFit("fit", dict(FIT_PARAMS))
    assert node.validate_inputs({"rows": rows_linear()}) == []
    assert node.validate_inputs({}) != []  # train mode: rows are required
    loader = SklearnFit("fit", dict(FIT_PARAMS), mode="load", artifact="a/model.joblib")
    assert loader.validate_inputs({}) == []  # a load never reads them


# ---------------------------------------------------------------------------
# The signal seam (stub estimators — no sklearn needed)
# ---------------------------------------------------------------------------


class _StubPoint:
    """Records the vector it was asked about; answers a constant."""

    def __init__(self, answer=0.5):
        self.answer = answer
        self.asked = []

    def predict(self, matrix):
        self.asked.append(matrix)
        return [self.answer]


class _StubProba:
    def __init__(self, row):
        self.row = row

    def predict_proba(self, matrix):
        return [self.row]


def test_signal_predict_reads_dicts_and_attribute_objects():
    stub = _StubPoint(0.25)
    signal = SklearnSignal(stub, ["x"], "predict", "a/model.joblib", loaded=False)
    assert signal.predict({"x": 0.5}) == 0.25

    class Row:
        x = 0.7

    assert signal.predict(Row()) == 0.25
    assert stub.asked == [[[0.5]], [[0.7]]]


def test_signal_predict_mapping_key_beats_a_dict_method_name():
    """A feature literally named ``items`` must read the VALUE, never the
    bound ``dict.items`` method."""
    stub = _StubPoint()
    signal = SklearnSignal(stub, ["items"], "predict", "p", loaded=False)
    assert signal.predict({"items": 0.3}) == 0.5
    assert stub.asked == [[[0.3]]]


def test_signal_predict_declines_on_missing_none_and_non_finite():
    signal = SklearnSignal(_StubPoint(), ["x"], "predict", "p", loaded=False)
    assert signal.predict({}) is None
    assert signal.predict({"x": None}) is None
    assert signal.predict({"x": float("nan")}) is None
    assert signal.predict({"x": float("inf")}) is None


def test_signal_predict_declines_an_attribute_object_missing_the_feature():
    signal = SklearnSignal(_StubPoint(), ["x"], "predict", "p", loaded=False)

    class Row:
        z = 0.5  # no ``x`` anywhere

    assert signal.predict(Row()) is None


def test_signal_predict_raises_on_a_non_numeric_field():
    signal = SklearnSignal(_StubPoint(), ["x"], "predict", "p", loaded=False)
    with pytest.raises(ValueError, match="not a number"):
        signal.predict({"x": "0.5"})


def test_signal_predict_proba_is_binary_only():
    ok = SklearnSignal(_StubProba([0.4, 0.6]), ["x"], "predict_proba", "p", loaded=True)
    assert ok.predict({"x": 1.0}) == pytest.approx(0.6)
    bad = SklearnSignal(
        _StubProba([0.2, 0.3, 0.5]), ["x"], "predict_proba", "p", loaded=True
    )
    with pytest.raises(ValueError, match="binary-only"):
        bad.predict({"x": 1.0})


# ---------------------------------------------------------------------------
# Real-sklearn integration: fit, persist, restore
# ---------------------------------------------------------------------------


def test_fit_params_accept_a_list_of_label_keys():
    params = {**FIT_PARAMS, "label": ["y", "z"]}
    assert SklearnFit.validate_params(params) == []


def test_list_label_wraps_multioutput_and_predicts_a_vector(tmp_path):
    pytest.importorskip("sklearn")
    rows = [{"x": float(i), "y1": 2.0 * i, "y2": -1.0 * i} for i in range(12)]
    params = {
        "estimator": RIDGE,
        "estimator_params": {"alpha": 1e-6},
        "features": ["x"],
        "label": ["y1", "y2"],
        "seed": 7,
    }
    out = SklearnFit("fit", params).run(_ctx(tmp_path, "mo"), {"rows": rows})
    pred = out["signal"].predict({"x": 3.0})
    assert pred == pytest.approx([6.0, -3.0], abs=1e-2)


def test_fit_learns_and_persists_with_provenance(tmp_path):
    out = _fit(tmp_path)
    assert set(out) == set(SklearnFit.outputs)
    assert out["signal"].predict({"x": 0.5}) == pytest.approx(0.5, abs=1e-3)
    assert out["signal"].loaded is False
    assert out["metrics"] == {"loaded": 0.0, "n_features": 1.0, "n_rows": 9.0}

    artifact = out["artifact_path"]
    assert os.path.isfile(artifact) and artifact.endswith("model.joblib")
    with open(artifact + ".json", encoding="utf-8") as fh:
        sidecar = json.load(fh)
    assert sidecar["format"] == "sklearn-joblib-v1"
    assert sidecar["estimator"] == RIDGE
    assert sidecar["features"] == ["x"] and sidecar["label"] == "y"
    assert sidecar["seed"] == 7 and sidecar["n_rows"] == 9
    # RE-PINNED (S2-A): the digest covers model bytes + the sidecar's own
    # schema fields, not the bytes alone. Hand-verified — _combined_digest
    # recomputes it here from the DOCUMENTED material (module docstring),
    # independently of the pack's own helper.
    assert sidecar["sha256"] == _combined_digest(artifact, sidecar)


def test_load_restores_the_pinned_fit_and_never_refits(tmp_path):
    fitted = _fit(tmp_path)
    pinned = fitted["artifact_path"]
    expected = fitted["signal"].predict(SAMPLE)

    loader = SklearnFit("fit", dict(FIT_PARAMS), mode="load", artifact=pinned)
    # Wire DIFFERENT rows in: a silent refit would learn y = 1 - x and
    # predict ~0.1 at x=0.9; the restore must keep predicting ~0.9.
    out = loader.run(_ctx(tmp_path, "loadrun"), {"rows": rows_inverted()})
    assert out["metrics"]["loaded"] == 1.0
    assert out["metrics"]["n_rows"] == 9.0
    assert out["artifact_path"] == pinned
    assert out["signal"].loaded is True
    assert out["signal"].predict(SAMPLE) == pytest.approx(expected, abs=1e-9)

    fresh = _fit(tmp_path, rows=rows_inverted(), run_name="freshrun")
    assert fresh["metrics"]["loaded"] == 0.0
    assert abs(fresh["signal"].predict(SAMPLE) - expected) > 0.5  # distinguishable


@pytest.mark.parametrize(
    "mutation",
    [
        {"estimator": "sklearn.linear_model.Lasso"},
        {"estimator_params": {"alpha": 9.0}},
        {"features": ["x", "y"], "label": "x"},
        {"label": "x", "features": ["y"]},
        {"seed": 8},
        {"predict_method": "predict_proba"},
    ],
)
def test_load_refuses_an_artifact_that_contradicts_the_params(tmp_path, mutation):
    pinned = _fit(tmp_path)["artifact_path"]
    params = {**FIT_PARAMS, **mutation}
    loader = SklearnFit("fit", params, mode="load", artifact=pinned)
    with pytest.raises(ValueError, match="does not match this node's params"):
        loader.run(_ctx(tmp_path, "loadrun"), {"rows": rows_linear()})


def test_load_refuses_an_empty_artifact_pin_by_name(tmp_path):
    pytest.importorskip("sklearn")
    loader = SklearnFit("fit", dict(FIT_PARAMS), mode="load", artifact="")
    with pytest.raises(ValueError, match="pinned artifact path"):
        loader.run(_ctx(tmp_path, "loadrun"), {})


def test_load_reports_a_library_version_drift_but_still_restores(tmp_path, caplog):
    fitted = _fit(tmp_path)
    pinned = fitted["artifact_path"]
    sidecar_path = pinned + ".json"
    with open(sidecar_path, encoding="utf-8") as fh:
        sidecar = json.load(fh)
    sidecar["library_version"] = "0.0.1"  # provenance, never identity
    with open(sidecar_path, "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh)
    loader = SklearnFit("fit", dict(FIT_PARAMS), mode="load", artifact=pinned)
    with caplog.at_level("INFO", logger="dskit.pipeline.fit"):
        out = loader.run(_ctx(tmp_path, "loadrun"), {})
    assert out["metrics"]["loaded"] == 1.0
    assert any("library version" in r.getMessage() for r in caplog.records)


def test_load_refuses_missing_artifact_and_missing_sidecar(tmp_path):
    loader = SklearnFit(
        "fit", dict(FIT_PARAMS), mode="load", artifact=str(tmp_path / "no.joblib")
    )
    with pytest.raises(ValueError, match="does not exist"):
        loader.run(_ctx(tmp_path, "l1"), {})

    pinned = _fit(tmp_path)["artifact_path"]
    os.remove(pinned + ".json")
    loader = SklearnFit("fit", dict(FIT_PARAMS), mode="load", artifact=pinned)
    with pytest.raises(ValueError, match="sidecar.*missing"):
        loader.run(_ctx(tmp_path, "l2"), {})


@pytest.mark.parametrize(
    "rewrite,needle",
    [
        (lambda s: "{not json", "not readable JSON"),
        (lambda s: json.dumps({**json.loads(s), "format": "v0"}), "format"),
        (
            lambda s: json.dumps(
                {k: v for k, v in json.loads(s).items() if k != "sha256"}
            ),
            "missing",
        ),
        (
            lambda s: json.dumps({**json.loads(s), "features": []}),
            "malformed",
        ),
        (
            lambda s: json.dumps({**json.loads(s), "n_rows": "many"}),
            "malformed",
        ),
        # The estimator path is IMPORTED on the load path now (the
        # isinstance check needs the class), so junk there must refuse by
        # name rather than crash inside the importer.
        (
            lambda s: json.dumps({**json.loads(s), "estimator": 5}),
            "malformed",
        ),
        (
            lambda s: json.dumps({**json.loads(s), "estimator": "Ridge"}),
            "malformed",
        ),
    ],
)
def test_load_refuses_a_broken_sidecar(tmp_path, rewrite, needle):
    pinned = _fit(tmp_path)["artifact_path"]
    sidecar_path = pinned + ".json"
    with open(sidecar_path, encoding="utf-8") as fh:
        text = fh.read()
    with open(sidecar_path, "w", encoding="utf-8") as fh:
        fh.write(rewrite(text))
    loader = SklearnFit("fit", dict(FIT_PARAMS), mode="load", artifact=pinned)
    with pytest.raises(ValueError, match=needle):
        loader.run(_ctx(tmp_path, "loadrun"), {})


def test_load_refuses_a_model_file_that_changed_since_it_was_written(tmp_path):
    pinned = _fit(tmp_path)["artifact_path"]
    with open(pinned, "ab") as fh:
        fh.write(b"tampered")
    loader = SklearnFit("fit", dict(FIT_PARAMS), mode="load", artifact=pinned)
    with pytest.raises(ValueError, match="content hash"):
        loader.run(_ctx(tmp_path, "loadrun"), {})


def test_load_refuses_bytes_joblib_cannot_restore(tmp_path):
    pinned = _fit(tmp_path)["artifact_path"]
    garbage = b"not a joblib payload"
    with open(pinned, "wb") as fh:
        fh.write(garbage)
    sidecar_path = pinned + ".json"
    with open(sidecar_path, encoding="utf-8") as fh:
        sidecar = json.load(fh)
    # RE-PINNED (S2-A): re-record the COMBINED digest, so the load gets past
    # the hash check and the joblib failure is what refuses. Hand-verified —
    # _combined_digest derives it from the documented material.
    sidecar["sha256"] = _combined_digest(pinned, sidecar)
    with open(sidecar_path, "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh)
    loader = SklearnFit("fit", dict(FIT_PARAMS), mode="load", artifact=pinned)
    with pytest.raises(ValueError, match="failed to load"):
        loader.run(_ctx(tmp_path, "loadrun"), {})


# ---------------------------------------------------------------------------
# S2-A: the sidecar is hash material — tampering it refuses like tampering
# the model file, and even a re-hashed sidecar cannot relabel the class
# ---------------------------------------------------------------------------


def _combined_digest(artifact, sidecar):
    """The DOCUMENTED hash material, recomputed independently of the pack:
    sha256 over the model bytes, a NUL separator, and the canonical JSON
    (sorted keys, compact separators) of every sidecar field except
    ``sha256`` (the digest cannot cover itself) and ``library_version``
    (provenance, never identity)."""
    import hashlib

    material = {
        k: v for k, v in sidecar.items() if k not in ("sha256", "library_version")
    }
    digest = hashlib.sha256()
    digest.update(pathlib.Path(artifact).read_bytes())
    digest.update(b"\0")
    digest.update(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return digest.hexdigest()


def _rewrite_sidecar(artifact, mutate):
    sidecar_path = artifact + ".json"
    with open(sidecar_path, encoding="utf-8") as fh:
        sidecar = json.load(fh)
    mutate(sidecar)
    with open(sidecar_path, "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh)
    return sidecar


LASSO_PARAMS = {
    **FIT_PARAMS,
    "estimator": "sklearn.linear_model.Lasso",
    "estimator_params": {"alpha": 0.5},
}


def _lasso_disguised_as_ridge(tmp_path):
    """A REAL Lasso artifact whose sidecar is rewritten to claim the
    canonical Ridge identity — the confirmed S2-A exploit shape."""
    pinned = _fit(tmp_path, params=LASSO_PARAMS)["artifact_path"]

    def mutate(sidecar):
        sidecar["estimator"] = FIT_PARAMS["estimator"]
        sidecar["estimator_params"] = FIT_PARAMS["estimator_params"]

    return pinned, _rewrite_sidecar(pinned, mutate)


def test_a_foreign_lasso_artifact_cannot_pass_verification_as_a_ridge(tmp_path):
    """S2-A exploit 1: pre-fix the hash covered only the model bytes, so a
    Lasso artifact under a Ridge-claiming sidecar loaded clean. The
    sidecar is hash material now — the edit refuses at load by name."""
    pinned, _ = _lasso_disguised_as_ridge(tmp_path)
    loader = SklearnFit("fit", dict(FIT_PARAMS), mode="load", artifact=pinned)
    with pytest.raises(ValueError, match="content hash"):
        loader.run(_ctx(tmp_path, "loadrun"), {"rows": rows_linear()})


def test_a_rehashed_sidecar_still_cannot_relabel_the_estimator_class(tmp_path):
    """S2-A belt-and-braces: an adversary who RECOMPUTES the digest after
    rewriting the identity still cannot pass a Lasso off as a Ridge — the
    restored object is isinstance-checked against the sidecar's estimator
    class and refused by name."""
    pinned, sidecar = _lasso_disguised_as_ridge(tmp_path)
    sidecar["sha256"] = _combined_digest(pinned, sidecar)
    with open(pinned + ".json", "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh)
    loader = SklearnFit("fit", dict(FIT_PARAMS), mode="load", artifact=pinned)
    with pytest.raises(ValueError, match="sidecar declares"):
        loader.run(_ctx(tmp_path, "loadrun"), {"rows": rows_linear()})


def test_a_reordered_sidecar_feature_list_refuses_instead_of_transposing(tmp_path):
    """S2-A exploit 2: swapping the sidecar's feature order silently
    transposed SklearnPredict's vectors pre-fix (the sidecar IS the
    feature order that node serves). A reorder is a sidecar tamper; it
    must refuse on the content hash."""
    params = {
        "estimator": RIDGE,
        "estimator_params": {"alpha": 1e-6},
        "features": ["a", "b"],
        "label": "y",
    }
    rows = [
        {
            "a": 0.1 * i,
            "b": ((i * 3) % 7) / 7.0,
            "y": 0.1 * i + 10.0 * (((i * 3) % 7) / 7.0),
        }
        for i in range(9)
    ]
    pinned = _fit(tmp_path, params=params, rows=rows)["artifact_path"]
    _rewrite_sidecar(pinned, lambda s: s.update(features=["b", "a"]))
    node = SklearnPredict("serve", {"artifact": pinned})
    with pytest.raises(ValueError, match="content hash"):
        node.run(_ctx(tmp_path, "serverun"), {})


@pytest.mark.parametrize(
    "edit",
    [
        {"estimator_params": {"alpha": 9.0}},
        {"label": "z"},
        {"predict_method": "predict_proba"},
        {"seed": 8},
        {"n_rows": 1},
    ],
)
def test_every_identity_sidecar_field_is_hash_material(tmp_path, edit):
    """Every schema-bearing sidecar field is folded into the digest — an
    edit to ANY of them refuses at load, even on SklearnPredict, which
    declares no identity params of its own to cross-check against."""
    pinned = _fit(tmp_path)["artifact_path"]
    _rewrite_sidecar(pinned, lambda s: s.update(edit))
    node = SklearnPredict("serve", {"artifact": pinned})
    with pytest.raises(ValueError, match="content hash"):
        node.run(_ctx(tmp_path, "serverun"), {})


# ---------------------------------------------------------------------------
# Seed threading, import refusals, constructor typos, bad rows
# ---------------------------------------------------------------------------


def test_seed_threads_into_random_state_where_accepted(tmp_path):
    params = {
        "estimator": "sklearn.linear_model.LogisticRegression",
        "estimator_params": {"C": 1.0, "max_iter": 200},
        "features": ["x"],
        "label": "y",
        "seed": 7,
        "predict_method": "predict_proba",
    }
    rows = [{"x": 0.1 * i, "y": int(i >= 5)} for i in range(10)]
    out = _fit(tmp_path, params=params, rows=rows)
    assert out["signal"].estimator.get_params()["random_state"] == 7
    belief = out["signal"].predict({"x": 0.9})
    assert 0.5 < belief < 1.0  # a genuine probability, positive side


def test_seed_is_refused_where_the_estimator_never_reads_it(tmp_path):
    params = {
        "estimator": "sklearn.linear_model.LinearRegression",
        "features": ["x"],
        "label": "y",
        "seed": 7,
    }
    with pytest.raises(ValueError, match="accepts no random_state"):
        _fit(tmp_path, params=params)


@pytest.mark.parametrize(
    "estimator,needle",
    [
        ("no_such_library_zzz.Model", "cannot import"),
        ("sklearn.linear_model.NoSuchEstimator", "no attribute"),
        ("json.dumps", "no fit method"),
    ],
)
def test_unimportable_or_unfit_estimators_refused_by_name(tmp_path, estimator, needle):
    pytest.importorskip("sklearn")
    params = {"estimator": estimator, "features": ["x"], "label": "y"}
    with pytest.raises(ValueError, match=needle):
        _fit(tmp_path, params=params)


def test_a_typoed_nested_estimator_param_is_refused_at_fit(tmp_path):
    params = {**FIT_PARAMS, "estimator_params": {"alphaa": 1.0}}
    with pytest.raises(ValueError, match="estimator_params"):
        _fit(tmp_path, params=params)


@pytest.mark.parametrize(
    "rows,needle",
    [
        ([], "zero rows"),
        ([{"x": 0.1, "y": 1.0}, {"x": 0.2}], "carries no 'y'"),
        ([{"x": 0.1, "y": None}], "carries no 'y'"),
        ([{"x": "0.1", "y": 1.0}], "finite number"),
        ([{"x": float("nan"), "y": 1.0}], "finite number"),
    ],
)
def test_untrainable_rows_are_refused_by_row_and_key(tmp_path, rows, needle):
    with pytest.raises(ValueError, match=needle):
        _fit(tmp_path, rows=rows)


def test_multiclass_predict_proba_refuses_rather_than_guessing(tmp_path):
    params = {
        "estimator": "sklearn.linear_model.LogisticRegression",
        "estimator_params": {"max_iter": 200},
        "features": ["x"],
        "label": "y",
        "predict_method": "predict_proba",
    }
    rows = [{"x": 0.1 * i, "y": i % 3} for i in range(12)]
    out = _fit(tmp_path, params=params, rows=rows)
    with pytest.raises(ValueError, match="binary-only"):
        out["signal"].predict({"x": 0.4})


# ---------------------------------------------------------------------------
# SklearnPredict — inference from the pin
# ---------------------------------------------------------------------------


def test_predict_node_serves_the_pinned_model(tmp_path):
    fitted = _fit(tmp_path)
    pinned = fitted["artifact_path"]
    node = SklearnPredict("serve", {"artifact": pinned})
    out = node.run(_ctx(tmp_path, "serverun"), {})
    assert set(out) == {"signal"}
    assert out["signal"].loaded is True and out["signal"].artifact_path == pinned
    assert out["signal"].predict(SAMPLE) == pytest.approx(
        fitted["signal"].predict(SAMPLE), abs=1e-9
    )


def test_predict_node_accepts_a_matching_mode_load_pin(tmp_path):
    pinned = _fit(tmp_path)["artifact_path"]
    node = SklearnPredict("serve", {"artifact": pinned}, mode="load", artifact=pinned)
    out = node.run(_ctx(tmp_path, "serverun"), {})
    assert out["signal"].loaded is True


def test_predict_node_refuses_mode_train_by_name(tmp_path):
    node = SklearnPredict("serve", {"artifact": "a/model.joblib"}, mode="train")
    with pytest.raises(ValueError, match="mode='train'"):
        node.run(_ctx(tmp_path, "serverun"), {})


def test_predict_node_refuses_a_contradictory_node_level_artifact(tmp_path):
    pinned = _fit(tmp_path)["artifact_path"]
    node = SklearnPredict(
        "serve",
        {"artifact": pinned},
        mode="load",
        artifact=str(tmp_path / "other.joblib"),
    )
    with pytest.raises(ValueError, match="one source of truth"):
        node.run(_ctx(tmp_path, "serverun"), {})


def test_predict_node_refuses_an_empty_node_level_pin(tmp_path):
    """ADR-0038's declared delta: an EMPTY node-level pin refuses instead
    of quietly falling through to params.artifact — torch's and sb3's
    stricter rule, kept as the single one. Document-unreachable (the
    document already refuses mode='load' without an artifact), so it
    takes direct construction to reach."""
    node = SklearnPredict("serve", {"artifact": "a/model.joblib"}, mode="load")
    with pytest.raises(ValueError, match="empty artifact reference"):
        node.run(_ctx(tmp_path, "serverun"), {})


def test_a_node_level_artifact_without_mode_load_is_not_a_pin(tmp_path):
    """ADR-0038's IFF rule, and the consequence it carries: a node-level
    ``artifact`` exists ONLY under ``mode='load'``, so without the mode
    there is nothing to contradict and ``params.artifact`` is served.

    The document cannot produce this state — it refuses ``artifact``
    without ``mode='load'`` (``document.py``: "'artifact' without mode
    'load' has no meaning") — so it takes direct construction, and this
    pins that the base treats the stray field as ABSENT rather than as a
    silent second pin."""
    pinned = _fit(tmp_path)["artifact_path"]
    node = SklearnPredict(
        "serve", {"artifact": pinned}, artifact=str(tmp_path / "other.joblib")
    )
    assert node.node_level_pin() is None
    out = node.run(_ctx(tmp_path, "serverun"), {})
    assert out["signal"].artifact_path == pinned


def test_predict_node_refuses_a_missing_artifact_by_name(tmp_path):
    node = SklearnPredict("serve", {"artifact": str(tmp_path / "gone.joblib")})
    with pytest.raises(ValueError, match="does not exist"):
        node.run(_ctx(tmp_path, "serverun"), {})


# ---------------------------------------------------------------------------
# The selector doorway (ADR-0042): sklearn selectors BY IMPORT PATH
# ---------------------------------------------------------------------------

DAY = 24 * 60 * 60 * 1000

#: The three candidate columns the selection tests choose among, in the
#: order every document below declares them.
CANDIDATES = ["strong", "other", "flat"]

VARIANCE = "sklearn.feature_selection.VarianceThreshold"
KBEST = "sklearn.feature_selection.SelectKBest"
RFE = "sklearn.feature_selection.RFE"
F_REGRESSION = "sklearn.feature_selection.f_regression"
MUTUAL_INFO = "sklearn.feature_selection.mutual_info_regression"

SELECT_PARAMS = {
    "fit_split": "train",
    "features": list(CANDIDATES),
    "selector": VARIANCE,
    "selector_params": {"threshold": 0.0},
}


def rows_selectable(n=12, *, day=1, flat=1.0):
    """Rows carrying columns of four different worths.

    ``strong`` IS the label; ``echo`` tracks it with a wobble (weaker,
    but real); ``other`` is a deterministic pseudo-random column with no
    relation to it; ``flat`` is constant. Every selector below therefore
    has one obvious answer and no tie to break — and ``echo``, which no
    document here declares as a candidate, doubles as the non-candidate
    column the projection must leave alone.
    """
    return [
        {
            "asof_ms": day * DAY + i,
            "contract": f"C-{day}-{i}",
            "strong": float(i),
            "echo": float(i) + (i % 3),
            "other": float((i * 13) % 7),
            "flat": flat if flat is not None else float(i),
            "y": float(i),
        }
        for i in range(n)
    ]


#: The val rows differ in ONE way that matters: ``flat`` varies there. A
#: fit that saw them would keep a column the train split says is constant.
SELECT_TRAIN_ROWS = rows_selectable(day=1)
SELECT_VAL_ROWS = rows_selectable(day=15, flat=None)

SELECT_SPLITS = {"train_end_ms": 10 * DAY, "val_end_ms": 20 * DAY,
                 "test_end_ms": 30 * DAY}


def _split_ctx(tmp_path, name="selectrun"):
    from dskit.pipeline.base import TimeSplitConfig

    splits = TimeSplitConfig(**SELECT_SPLITS)
    return NodeContext(
        name="t",
        asof=ASOF,
        run_dir=str(tmp_path / name),
        splits=splits,
        splits_info=splits.to_obj(),
    )


def _select(tmp_path, params=None, *, rows=None, name="selectrun"):
    pytest.importorskip("sklearn")
    node = SklearnSelect("select", {**SELECT_PARAMS, **(params or {})})
    rows = SELECT_TRAIN_ROWS + SELECT_VAL_ROWS if rows is None else rows
    return node, node.run(_split_ctx(tmp_path, name), {"rows": rows})


def test_the_selector_is_a_member_of_the_fitted_family():
    assert SklearnSelect.role == "fitted_transform"
    assert SklearnSelect.outputs == ("transform", "rows", "metrics", "features")
    assert issubclass(SklearnSelect, FeatureSelector)
    assert SklearnSelect.surviving_features is not FeatureSelector.surviving_features


def test_select_params_canonical_set_validates_clean():
    assert SklearnSelect.validate_params(dict(SELECT_PARAMS)) == []


def test_select_params_the_selector_path_is_required_and_shape_checked():
    assert any(
        "selector" in p
        for p in SklearnSelect.validate_params(
            {"fit_split": "train", "features": CANDIDATES}
        )
    )
    for bad in ("", "NoDots", "sklearn.feature_selection:SelectKBest", 7):
        problems = SklearnSelect.validate_params({**SELECT_PARAMS, "selector": bad})
        assert any("selector" in p for p in problems), bad


def test_select_params_unknown_keys_refused_by_name():
    problems = SklearnSelect.validate_params({**SELECT_PARAMS, "k": 3})
    assert any("'k'" in p or "k" in p for p in problems)


def test_select_params_refuse_a_second_spelling_of_the_declared_knobs():
    """``selector_params`` may not carry what the node already owns.

    Two spellings of the inner estimator (or of the score function) would
    disagree, and a search space addressing the node's own knob would
    silently tune the loser — the ``optimizer_params``/``lr`` rule, one
    pack over.
    """
    for shadowed in ("estimator", "score_func"):
        problems = SklearnSelect.validate_params(
            {**SELECT_PARAMS, "selector_params": {shadowed: "x.Y"}}
        )
        assert any(shadowed in p for p in problems), shadowed


def test_select_params_estimator_params_without_estimator_is_refused():
    """Those kwargs construct the inner estimator; nothing else reads them."""
    problems = SklearnSelect.validate_params(
        {**SELECT_PARAMS, "estimator_params": {"alpha": 1.0}}
    )
    assert any("estimator_params" in p and "estimator" in p for p in problems)


def test_select_params_the_optional_paths_are_shape_checked():
    for knob, example in (("estimator", RIDGE), ("score_func", F_REGRESSION)):
        assert SklearnSelect.validate_params({**SELECT_PARAMS, knob: example}) == []
        problems = SklearnSelect.validate_params({**SELECT_PARAMS, knob: "NoDots"})
        assert any(knob in p for p in problems), knob


def test_an_unsupervised_selector_needs_no_label(tmp_path):
    """VarianceThreshold drops the constant column and keeps the rest."""
    _node, out = _select(tmp_path)
    assert out["features"] == ["strong", "other"]
    assert out["metrics"] == {
        "n_rows": len(SELECT_TRAIN_ROWS) + len(SELECT_VAL_ROWS),
        "n_fit_rows": len(SELECT_TRAIN_ROWS),
        "n_candidates": 3,
        "n_selected": 2,
    }


def test_the_fit_sees_the_declared_split_only(tmp_path):
    """THE leak, in the pack: ``flat`` is constant on the train rows and
    VARIES on the val rows, so a matrix built from the whole stream keeps
    a column the train split says carries nothing."""
    _node, out = _select(tmp_path)
    assert "flat" not in out["features"]
    assert all("flat" not in row for row in out["rows"])


def test_a_supervised_selector_reads_the_declared_label(tmp_path):
    _node, out = _select(
        tmp_path,
        {
            "features": ["strong", "other"],
            "selector": KBEST,
            "selector_params": {"k": 1},
            "score_func": F_REGRESSION,
            "label": "y",
        },
    )
    assert out["features"] == ["strong"]


def test_a_score_func_may_be_named_by_import_path(tmp_path):
    """Mutual information is a CALLABLE knob, so it is a path like any
    other — the constant column scores zero and loses to the two columns
    that carry the label's information."""
    _node, out = _select(
        tmp_path,
        {
            "features": ["strong", "echo", "flat"],
            "selector": KBEST,
            "selector_params": {"k": 2},
            "score_func": MUTUAL_INFO,
            "label": "y",
        },
    )
    assert out["features"] == ["strong", "echo"]


def test_a_wrapper_selector_takes_its_inner_estimator_by_import_path(tmp_path):
    """RFE's ``estimator`` cannot be spelled inside a JSON kwargs block,
    so it is the pack's OWN doorway knob — one grammar for "name me a
    model", reused."""
    _node, out = _select(
        tmp_path,
        {
            "features": ["strong", "other"],
            "selector": RFE,
            "selector_params": {"n_features_to_select": 1},
            "estimator": RIDGE,
            "estimator_params": {"alpha": 1e-6},
            "label": "y",
        },
    )
    assert out["features"] == ["strong"]


def test_a_supervised_selector_with_no_label_refuses_by_name(tmp_path):
    pytest.importorskip("sklearn")
    node = SklearnSelect(
        "select",
        {**SELECT_PARAMS, "selector": KBEST, "selector_params": {"k": 1}},
    )
    with pytest.raises(ValueError, match="label"):
        node.run(_split_ctx(tmp_path), {"rows": SELECT_TRAIN_ROWS})


def test_a_class_that_is_not_a_selector_refuses_by_name(tmp_path):
    """A selector is a transformer with ``get_support`` — an estimator
    without one has no notion of which columns survived."""
    pytest.importorskip("sklearn")
    node = SklearnSelect("select", {**SELECT_PARAMS, "selector": RIDGE,
                                    "selector_params": {}, "label": "y"})
    with pytest.raises(ValueError, match="get_support"):
        node.run(_split_ctx(tmp_path), {"rows": SELECT_TRAIN_ROWS})


class ShortMask:
    """A "selector" whose support mask does not cover the candidates.

    The doorway takes any class that can report its support, which means
    the pack cannot assume the mask's LENGTH either: a selector fitted on
    a matrix it reshaped, or one whose mask counts something other than
    input columns, would otherwise zip silently against the candidate list
    and drop the tail — a projection nobody asked for, reported as a
    selection. There is no such class in sklearn today, which is exactly
    why the check needs a fixture to be pinnable at all.
    """

    def fit(self, matrix, targets=None):
        """Fit nothing; the mask is canned."""
        return self

    def get_support(self):
        """One bool for three candidates — deliberately short."""
        return [True]


def test_a_support_mask_that_does_not_cover_the_candidates_refuses(tmp_path):
    node = SklearnSelect(
        "select",
        {**SELECT_PARAMS,
         "selector": f"{ShortMask.__module__}.{ShortMask.__name__}",
         "selector_params": {}},
    )
    with pytest.raises(ValueError, match="one bool per candidate"):
        node.run(_split_ctx(tmp_path), {"rows": SELECT_TRAIN_ROWS})


def test_a_row_missing_a_candidate_is_refused_by_name(tmp_path):
    pytest.importorskip("sklearn")
    node = SklearnSelect("select", dict(SELECT_PARAMS))
    rows = [dict(row) for row in SELECT_TRAIN_ROWS]
    del rows[3]["other"]
    with pytest.raises(ValueError, match="carries no 'other'"):
        node.run(_split_ctx(tmp_path), {"rows": rows})


def test_the_selected_columns_round_trip_through_the_artifact(tmp_path):
    """Serving reads the columns training chose, from the sidecar."""
    node, out = _select(tmp_path)
    sidecar = os.path.join(node.artifact_dir(_split_ctx(tmp_path)), SIDECAR_NAME)
    served = SklearnSelect(
        "select", {"features": list(CANDIDATES), "selector": VARIANCE,
                   "selector_params": {"threshold": 0.0}},
        mode="load", artifact=sidecar,
    )
    restored = served.run(_ctx(tmp_path, "servingrun"), {"rows": SELECT_VAL_ROWS})

    assert restored["features"] == out["features"]
    assert restored["metrics"]["n_fit_rows"] == 0


#: The whole composition ADR-0042 asks for, as a document: a selector
#: chooses among two candidates, and the model BELOW reads the surviving
#: list out of the selector's own output instead of restating a list
#: nobody can know before the fit. ``noise`` is constant by construction,
#: so the variance threshold must drop it and the model must never see it.
SELECT_FLOW = {
    "name": "select-then-fit",
    "pipeline": {
        "dataset": {
            "uses": "dskit.pipeline.synthetic_nodes:SynthEvents",
            "params": {"n_events": 104, "n_instruments": 2, "seed": 4},
        },
        "labels": {
            "uses": "dskit.pipeline.synthetic_nodes:SynthLabels",
            "inputs": {"events": "$dataset.events"},
        },
        "noisy": {
            "uses": "derive",
            "inputs": {"records": "$dataset.events"},
            "params": {"field": "noise", "cases": [{"when": [], "value": 0.0}]},
        },
        "select": {
            "uses": "dskit.pipeline.libs.sklearn:SklearnSelect",
            "inputs": {"rows": "$noisy.records"},
            "params": {
                "fit_split": "train",
                "features": ["mid", "noise"],
                "selector": VARIANCE,
                "selector_params": {"threshold": 0.0},
            },
        },
        "train_rows": {
            "uses": "filter",
            "inputs": {"records": "$select.rows"},
            "params": {
                "where": [
                    {"field": "asof_ms", "op": "<=", "value": "$splits.train_end_ms"}
                ]
            },
        },
        "model": {
            "uses": "dskit.pipeline.libs.sklearn:SklearnFit",
            "inputs": {"rows": "$train_rows.records"},
            "params": {
                "estimator": RIDGE,
                "features": "$select.features",
                "label": "settled_yes",
            },
        },
        "validate": {
            "uses": "validate",
            "inputs": {
                "records": "$dataset.events",
                "signal": "$model.signal",
                "outcomes": "$labels.outcomes",
            },
            "params": {"split": "val", "metric": "squared_error", "min_events": 5},
        },
        "sweep": {
            "uses": "hpo-grid",
            "params": {
                "space": {
                    "model.estimator": [
                        "sklearn.linear_model.LinearRegression",
                        RIDGE,
                    ]
                },
                "objective": "$validate.metrics.loss",
                "select": "min",
            },
        },
    },
    "splits": {
        "kind": "time",
        "train_end_ms": 92620800000,
        "val_end_ms": 93916800000,
        "test_end_ms": 95299200000,
    },
}


def test_the_model_below_reads_the_surviving_list_end_to_end(tmp_path):
    """ADR-0042 owner flows 1 and 3, run: select upstream, sweep models.

    Both flows are the SAME document — a selector above the model with a
    space over ``model.estimator`` — which is the design target: they are
    document edits over one node, not three code paths. What distinguishes
    them is only intent (flow 3 fixes the selector's method deliberately;
    flow 1 treats the feature set as given).

    ``$select.features`` is the wire that makes either usable: a document
    cannot state which columns survive — that is the fit's answer — so the
    model reads the selector's ``features`` output as its own knob.
    """
    pytest.importorskip("sklearn")
    obj = json.loads(json.dumps(SELECT_FLOW))
    obj["outputs"] = {"run_root": str(tmp_path)}
    result = run_document(PipelineDocument.from_obj(obj), asof=ASOF)

    assert result.state == "ran" and result.exit_code == 0
    assert result.outputs["select"]["features"] == ["mid"]
    assert all("noise" not in row for row in result.outputs["select"]["rows"])
    # What the winner actually consumed, read off its own sidecar.
    with open(result.outputs["model"]["artifact_path"] + ".json") as fh:
        assert json.load(fh)["features"] == ["mid"]
    assert result.outputs["validate"]["metrics"]["n"] >= 5

    sweep = result.outputs["sweep"]
    assert [t["overrides"]["model.estimator"] for t in sweep["trials"]] == [
        "sklearn.linear_model.LinearRegression",
        RIDGE,
    ]
    assert sweep["best_score"] == min(t["score"] for t in sweep["trials"])
    assert sweep["best_params"]["model.estimator"] in (
        "sklearn.linear_model.LinearRegression",
        RIDGE,
    )


def test_the_flow_refuses_when_the_model_restates_the_candidates(tmp_path):
    """The other half of the same claim: a model that declares the
    CANDIDATE list gets a fit refusal naming the dropped column, so the
    ``$select.features`` wire is load-bearing rather than stylistic."""
    pytest.importorskip("sklearn")
    obj = json.loads(json.dumps(SELECT_FLOW))
    obj["pipeline"]["model"]["params"]["features"] = ["mid", "noise"]
    obj["outputs"] = {"run_root": str(tmp_path)}
    result = run_document(PipelineDocument.from_obj(obj), asof=ASOF)

    assert result.state == "error" and result.exit_code != 0
    assert "carries no 'noise'" in (result.error or "")


SELECTION_DEMO = os.path.join(
    os.path.dirname(__file__), "..", "..", "examples", "pipeline",
    "selection-demo.json",
)


class TestSelectionDemo:
    """``examples/pipeline/selection-demo.json`` — D1, the cookbook.

    The idiom is ``TestExampleDocument`` in ``test_optuna.py``: load,
    hash, plan through the real planner, run through the real driver.
    Two extra pins the card asked for: the winner consumed the selected
    columns, and it beat the loser on the declared metric.
    """

    def test_loads_and_hashes_stably(self):
        doc = load_document(SELECTION_DEMO)
        assert doc.name == "selection-demo"
        assert load_document(SELECTION_DEMO).hash == doc.hash

    def test_plans_via_the_real_planner(self):
        the_plan = plan(load_document(SELECTION_DEMO))
        assert the_plan.role_of("select") == "fitted_transform"
        assert the_plan.role_of("sweep") == "search"
        assert ("sweep", "report") in the_plan.edges

    def test_runs_end_to_end_and_the_winner_beat_the_loser(
        self, tmp_path, monkeypatch
    ):
        pytest.importorskip("sklearn")
        monkeypatch.chdir(tmp_path)
        result = run_document(load_document(SELECTION_DEMO), asof=ASOF)
        assert result.state == "ran" and result.exit_code == 0

        selected = result.outputs["select"]["features"]
        assert selected == ["mid"]
        with open(result.outputs["model"]["artifact_path"] + ".json") as fh:
            assert json.load(fh)["features"] == selected

        sweep = result.outputs["sweep"]
        scores = {t["overrides"]["model.estimator"]: t["score"]
                  for t in sweep["trials"]}
        winner = sweep["best_params"]["model.estimator"]
        loser = next(name for name in scores if name != winner)
        assert scores[winner] < scores[loser]
        assert result.outputs["validate"]["metrics"]["loss"] == pytest.approx(
            sweep["best_score"]
        )

    def test_flow_2_the_same_graph_with_a_selector_key_also_plans_and_runs(
        self, tmp_path, monkeypatch
    ):
        """ADR-0044: a space over BOTH keys is owner flow 2."""
        pytest.importorskip("sklearn")
        obj = json.loads(pathlib.Path(SELECTION_DEMO).read_text())
        obj["pipeline"]["select"]["params"]["features"] = [
            "mid", "noise", "asof_ms",
        ]
        obj["pipeline"]["sweep"]["params"]["space"][
            "select.selector_params.threshold"
        ] = [0.0, 1.0]
        obj["outputs"] = {"run_root": str(tmp_path)}
        doc = PipelineDocument.from_obj(obj)
        plan(doc)
        monkeypatch.chdir(tmp_path)
        result = run_document(doc, asof=ASOF)
        assert result.state == "ran", (result.state, result.error)
        trials = result.outputs["sweep"]["trials"]
        assert len(trials) == 4
        thresholds = {
            t["overrides"]["select.selector_params.threshold"] for t in trials
        }
        assert thresholds == {0.0, 1.0}
        kept = {
            0.0: ["mid", "asof_ms"],
            1.0: ["asof_ms"],
        }
        winner_t = result.outputs["sweep"]["best_params"][
            "select.selector_params.threshold"
        ]
        assert result.outputs["select"]["features"] == kept[winner_t]
        # The 4-trial winner is one threshold. Pin BOTH so a no-op
        # override cannot hide behind whichever value the grid lists first.
        for threshold, expect in kept.items():
            one = json.loads(pathlib.Path(SELECTION_DEMO).read_text())
            one["pipeline"]["select"]["params"]["features"] = [
                "mid", "noise", "asof_ms",
            ]
            one["pipeline"]["sweep"]["params"]["space"][
                "select.selector_params.threshold"
            ] = [threshold]
            one["outputs"] = {"run_root": str(tmp_path / f"t{threshold}")}
            pinned = run_document(
                PipelineDocument.from_obj(one), asof=ASOF,
            )
            assert pinned.state == "ran", (threshold, pinned.error)
            assert pinned.outputs["select"]["features"] == expect


# ---------------------------------------------------------------------------
# SklearnSegment (ADR-0160) — the validation surface and the row rule
# ---------------------------------------------------------------------------

#: Removes a key from :func:`_segment_params` rather than setting it, so a
#: "declared but wrong" case and an "absent" case are both expressible.
_DROP = object()

#: The canonical segment params every case below varies one knob of.
SEGMENT_PARAMS = {
    "fit_split": "train",
    "features": ["f0", "f1"],
    "algorithm": "kmeans",
    "algorithm_params": {"n_clusters": 2, "n_init": 1},
    "seed": 17,
}

#: One projectable row, carrying both declared features and neither
#: reserved output key.
SEGMENT_ROW = {"f0": 0.0, "f1": 1.0}


def _segment_params(**overrides):
    """The canonical params with ``overrides``; a ``_DROP`` value removes its key."""
    merged = {**SEGMENT_PARAMS, **overrides}
    return {k: v for k, v in merged.items() if v is not _DROP}


class ReachTheValidators(SklearnSegment):
    """A concrete stand-in whose only purpose is to be constructible.

    ``SklearnSegment`` is abstract until its own ``fit`` and
    ``apply_state`` exist, and an abstract class cannot be instantiated at
    all — so the INHERITED validators, which are instance methods, would
    be unreachable from a test. Both hooks raise: nothing in this section
    may execute, and a test that accidentally did would say so rather than
    pass quietly.
    """

    def fit(self, rows, params):
        raise AssertionError("the validation slice never fits")

    def apply_state(self, state, rows, params):
        raise AssertionError("the validation slice never projects")


def _segment(key="regime", **overrides):
    return ReachTheValidators(key, _segment_params(**overrides))


def test_the_segment_is_a_member_of_the_fitted_family():
    assert SklearnSegment.role == "fitted_transform"
    assert SklearnSegment.outputs == (
        "transform", "rows", "metrics", "segment_model_id",
    )
    assert issubclass(SklearnSegment, FittedTransform)
    assert SklearnSegment._PARAMS == FittedTransform._PARAMS + (
        "algorithm", "algorithm_params", "features", "seed",
    )


def test_the_train_split_this_node_narrows_to_is_a_real_split_name():
    """The literal is named once in the pack; this pins it to the vocabulary."""
    from dskit.pipeline.libs.sklearn import _TRAIN_SPLIT

    assert _TRAIN_SPLIT in SPLIT_NAMES


def test_segment_params_canonical_set_validates_clean():
    assert SklearnSegment.validate_params(_segment_params()) == []


def test_segment_refuses_an_unknown_param():
    problems = SklearnSegment.validate_params(_segment_params(n_clusters=3))
    assert any("n_clusters" in p for p in problems), problems


@pytest.mark.parametrize("algorithm", ["kmeans", "minibatch_kmeans", "birch"])
def test_segment_accepts_every_member_of_the_closed_catalog(algorithm):
    assert SklearnSegment.validate_params(_segment_params(
        algorithm=algorithm, algorithm_params={"n_clusters": 2}, seed=_DROP,
    )) == []


@pytest.mark.parametrize(
    "algorithm",
    [_DROP, None, "KMeans", "dbscan", "sklearn.cluster.KMeans", 3],
)
def test_segment_refuses_anything_outside_the_closed_catalog(algorithm):
    """An arbitrary class path is deliberately excluded — this node extracts
    centers, which only the three catalog members are known to expose."""
    problems = SklearnSegment.validate_params(
        _segment_params(algorithm=algorithm, seed=_DROP)
    )
    assert any("algorithm" in p for p in problems), problems


@pytest.mark.parametrize(
    "features",
    [_DROP, None, [], ["f0", "f0"], ["f0", 3], ["f0", ""], "f0", ("f0", "f1")],
)
def test_segment_features_must_be_a_distinct_non_empty_key_list(features):
    problems = SklearnSegment.validate_params(_segment_params(features=features))
    assert any("features" in p for p in problems), problems


@pytest.mark.parametrize(
    "algorithm_params", [None, [], "n_clusters=2", {3: 1}, {"": 1}]
)
def test_segment_algorithm_params_must_be_a_non_empty_string_keyed_dict(
    algorithm_params,
):
    problems = SklearnSegment.validate_params(
        _segment_params(algorithm_params=algorithm_params)
    )
    assert any("algorithm_params" in p for p in problems), problems


# -- the ONE source of randomness, per algorithm (four atomic cases) ---------


@pytest.mark.parametrize("algorithm", ["kmeans", "minibatch_kmeans"])
@pytest.mark.parametrize("seed", [_DROP, 17], ids=["no-seed", "with-seed"])
def test_a_seeded_algorithm_refuses_algorithm_params_random_state(algorithm, seed):
    """Refused with OR without a ``seed``: one knob, one spelling either way."""
    problems = SklearnSegment.validate_params(_segment_params(
        algorithm=algorithm,
        algorithm_params={"n_clusters": 2, "random_state": 3},
        seed=seed,
    ))
    assert any("random_state" in p for p in problems), problems


def test_birch_refuses_a_seed_with_no_random_state_in_sight():
    """Birch consumes no random state; a recorded seed would be false provenance."""
    problems = SklearnSegment.validate_params(_segment_params(
        algorithm="birch", algorithm_params={"n_clusters": 2}, seed=17,
    ))
    assert any("seed" in p and "birch" in p for p in problems), problems


def test_birch_refuses_algorithm_params_random_state_with_no_seed_in_sight():
    problems = SklearnSegment.validate_params(_segment_params(
        algorithm="birch", algorithm_params={"random_state": 3}, seed=_DROP,
    ))
    assert any("random_state" in p for p in problems), problems


@pytest.mark.parametrize("algorithm", ["kmeans", "minibatch_kmeans"])
def test_a_seeded_algorithm_accepts_a_seed_when_no_random_state_is_declared(
    algorithm,
):
    assert SklearnSegment.validate_params(_segment_params(
        algorithm=algorithm, algorithm_params={"n_clusters": 2}, seed=17,
    )) == []


@pytest.mark.parametrize("seed", [True, 1.5, -1, 2 ** 32, "7", None])
def test_segment_seed_must_be_an_exact_int_inside_the_32_bit_range(seed):
    problems = SklearnSegment.validate_params(_segment_params(seed=seed))
    assert any("seed" in p for p in problems), problems


# -- the two literal-"train" gates (§3.1) -----------------------------------


@pytest.mark.parametrize("split", ["val", "cal", "test"])
def test_a_non_train_fit_split_still_passes_the_mode_blind_param_gate(split):
    """``validate_params`` is the family's own classmethod — mode-blind, so
    it cannot narrow ``fit_split`` and this slice does not make it try."""
    assert SklearnSegment.validate_params(_segment_params(fit_split=split)) == []


@pytest.mark.parametrize("split", ["val", "cal", "test"])
def test_the_train_gate_refuses_the_fit_split_the_param_gate_allowed(split):
    problems = _segment(fit_split=split).validate_train_inputs(
        {"rows": [dict(SEGMENT_ROW)]}
    )
    assert any("fit_split" in p and "train" in p for p in problems), problems


def test_the_train_gate_accepts_the_train_split():
    assert _segment().validate_train_inputs({"rows": [dict(SEGMENT_ROW)]}) == []


@pytest.mark.parametrize("knob", ["seed", "algorithm_params"])
def test_load_mode_refuses_the_fitting_knobs_that_describe_no_restored_state(knob):
    """``seed`` and ``algorithm_params`` describe FITTING, not the extracted
    predictor — a load that accepted them would imply it could recreate a
    native model from them."""
    node = ReachTheValidators(
        "regime",
        _segment_params(**{
            "seed": _DROP, "algorithm_params": _DROP, knob: SEGMENT_PARAMS[knob],
        }),
        mode="load",
        artifact="runs/x/fitted.json",
    )
    problems = node.validate_load_inputs({"rows": [dict(SEGMENT_ROW)]})
    assert any(knob in p for p in problems), problems


def test_load_mode_accepts_a_document_carrying_only_what_describes_the_state():
    node = ReachTheValidators(
        "regime",
        _segment_params(seed=_DROP, algorithm_params=_DROP),
        mode="load",
        artifact="runs/x/fitted.json",
    )
    assert node.validate_load_inputs({"rows": [dict(SEGMENT_ROW)]}) == []


# -- row_problems: one owner, two doorways, six atomic refusals -------------

#: ``(id, rows, fragment)`` — each row breaks exactly ONE rule, because a
#: compound fixture proves at most one of its branches.
_UNPROJECTABLE_ROWS = [
    ("non-mapping-row", [SimpleNamespace(f0=0.0, f1=1.0)], "mapping"),
    # The fragments DISTINGUISH the three feature branches. Asserting a bare
    # "'f1'" for all three would pass an implementation that collapsed
    # "absent" into "not a number" — the branches would be indistinguishable
    # from the outside, which is exactly what an atomic fixture is for.
    ("absent-feature", [{"f0": 0.0}], "carries no 'f1'"),
    ("non-finite-feature", [{"f0": 0.0, "f1": float("nan")}], "'f1' is nan"),
    ("bool-feature", [{"f0": 0.0, "f1": True}], "'f1' is True"),
    ("segment-already-present", [{**SEGMENT_ROW, "segment": 0}], "'segment'"),
    (
        "segment_model_id-already-present",
        [{**SEGMENT_ROW, "segment_model_id": "deadbeef"}],
        "'segment_model_id'",
    ),
]
_UNPROJECTABLE_IDS = [case[0] for case in _UNPROJECTABLE_ROWS]


@pytest.mark.parametrize(
    "_id,rows,fragment", _UNPROJECTABLE_ROWS, ids=_UNPROJECTABLE_IDS
)
def test_the_fitted_doorway_refuses_each_unprojectable_row(_id, rows, fragment):
    problems = _segment().validate_common_inputs({"rows": rows})
    assert any(fragment in p for p in problems), problems


@pytest.mark.parametrize(
    "_id,rows,fragment", _UNPROJECTABLE_ROWS, ids=_UNPROJECTABLE_IDS
)
def test_the_carrier_doorway_refuses_each_unprojectable_row(_id, rows, fragment):
    """The SECOND stream reaches the same rule through ``ApplyTransform`` —
    otherwise the sibling half of the family projects unvalidated rows."""
    carrier = TransformCarrier(_segment(), {"schema": SEGMENT_SCHEMA})
    problems = ApplyTransform("apply", {}).validate_inputs(
        {"transform": carrier, "rows": rows}
    )
    assert any(fragment in p for p in problems), problems


def test_both_doorways_accept_a_projectable_stream():
    rows = [dict(SEGMENT_ROW), {"f0": 1.0, "f1": 0.0, "kept": "untouched"}]
    node = _segment()
    assert node.validate_common_inputs({"rows": rows}) == []
    carrier = TransformCarrier(node, {"schema": SEGMENT_SCHEMA})
    assert ApplyTransform("apply", {}).validate_inputs(
        {"transform": carrier, "rows": rows}
    ) == []


def test_the_fitted_doorway_still_refuses_a_stream_that_is_not_a_list():
    problems = _segment().validate_common_inputs({"rows": iter([SEGMENT_ROW])})
    assert any("rows must be a list" in p for p in problems), problems


# ---------------------------------------------------------------------------
# SklearnSegment — the extracted state: fit, its schema, and the sidecar
# ---------------------------------------------------------------------------


class ApplyWouldRaise(SklearnSegment):
    """A stand-in for the state slice, with the REAL ``fit`` inherited.

    Deliberately NOT :class:`ReachTheValidators`: that one overrides
    ``fit`` as a raiser, so calling ``fit`` on it would dispatch to the
    raiser and never reach ``SklearnSegment.fit`` — the method this
    section exists to exercise. Only ``apply_state`` is supplied here,
    and it raises: nothing in this section may project.
    """

    def apply_state(self, state, rows, params):
        raise AssertionError("the state slice never projects")


#: Two well-separated clouds in the declared feature order.
SEGMENT_FIT_ROWS = [
    {"f0": 0.0, "f1": 0.0},
    {"f0": 0.1, "f1": 0.1},
    {"f0": 9.0, "f1": 9.0},
    {"f0": 9.1, "f1": 9.1},
]


def _state_node(key="regime", **overrides):
    return ApplyWouldRaise(key, _segment_params(**overrides))


def _canonical(state):
    return json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _stub_cluster(monkeypatch, algorithm, **attributes):
    """Patch a catalog member's sklearn class with a stub whose FITTED
    attributes are exactly ``attributes`` — the only way to reach ``fit``'s
    own degenerate-output refusals, which a healthy estimator never trips.

    A name left out of ``attributes`` is simply never set, which is how the
    missing-fitted-attribute cases are built.
    """
    import importlib

    from dskit.pipeline.libs.sklearn import _SEGMENT_PATHS

    module_name, _, class_name = _SEGMENT_PATHS[algorithm].rpartition(".")
    module = importlib.import_module(module_name)

    class Stub:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def fit(self, matrix):
            for name, value in attributes.items():
                setattr(self, name, value)
            return self

    monkeypatch.setattr(module, class_name, Stub)
    return Stub


def test_the_state_slice_stand_in_really_inherits_the_class_under_test():
    """Guards the checkpoint's own defect: a stand-in that overrode ``fit``
    would exercise the override, never ``SklearnSegment.fit``."""
    assert ApplyWouldRaise.fit is SklearnSegment.fit
    assert ReachTheValidators.fit is not SklearnSegment.fit


@pytest.mark.parametrize("algorithm", ["kmeans", "minibatch_kmeans", "birch"])
def test_fit_returns_exactly_the_declared_json_state(algorithm):
    pytest.importorskip("sklearn")
    node = _state_node(algorithm=algorithm, algorithm_params={"n_clusters": 2},
                       seed=17 if algorithm != "birch" else _DROP)
    state = node.fit(SEGMENT_FIT_ROWS, node.params)

    assert set(state) == {
        "schema", "algorithm", "features", "centers", "center_labels",
    }
    # The LITERAL, not the constant: comparing the state against
    # SEGMENT_SCHEMA moves both sides together, so a silently renamed
    # schema would still read as agreeing with itself — and the tag is
    # what a stored state is dispatched on years from now.
    assert state["schema"] == "dskit.sklearn-segment/v1"
    assert SEGMENT_SCHEMA == "dskit.sklearn-segment/v1"
    assert state["algorithm"] == algorithm
    assert state["features"] == ["f0", "f1"]
    assert state["centers"] and all(
        len(center) == 2 and all(isinstance(v, float) for v in center)
        for center in state["centers"]
    )
    assert len(state["center_labels"]) == len(state["centers"])
    assert all(
        isinstance(label, int) and not isinstance(label, bool) and label >= 0
        for label in state["center_labels"]
    )
    # JSON-able with no NaN, because the base persists it verbatim.
    assert json.loads(_canonical(state)) == state


@pytest.mark.parametrize("algorithm", ["kmeans", "minibatch_kmeans"])
def test_the_kmeans_family_labels_centers_positionally(algorithm):
    pytest.importorskip("sklearn")
    node = _state_node(algorithm=algorithm, algorithm_params={"n_clusters": 2})
    state = node.fit(SEGMENT_FIT_ROWS, node.params)
    assert state["center_labels"] == list(range(len(state["centers"])))


def test_birch_carries_its_own_subcluster_labels_which_may_repeat():
    """Birch's sub-centers map MANY-to-one onto global labels, so the label
    list is read from the estimator, never derived from a position.

    ``n_segments`` is asserted HERE and not on a KMeans state, because
    KMeans is the case where the two candidate answers COINCIDE: its
    labels are ``range(len(centers))``, so "distinct labels" and "number
    of centers" are the same number and a metric computed either way
    reads correctly. Only a many-to-one state tells them apart.
    """
    pytest.importorskip("sklearn")
    node = _state_node(algorithm="birch", seed=_DROP,
                       algorithm_params={"n_clusters": 2, "threshold": 0.05})
    state = node.fit(SEGMENT_FIT_ROWS, node.params)

    distinct = len(set(state["center_labels"]))
    assert len(state["centers"]) > distinct, (
        "this fixture exists to make the two answers differ; a Birch that "
        "stopped merging sub-centers would make it prove nothing"
    )
    assert node.state_metrics(state) == {"n_segments": distinct}


def test_the_model_id_is_the_canonical_digest_of_the_state_itself():
    pytest.importorskip("sklearn")
    node = _state_node()
    state = node.fit(SEGMENT_FIT_ROWS, node.params)
    expected = hashlib.sha256(_canonical(state).encode("utf-8")).hexdigest()
    assert node.segment_model_id(state) == expected
    assert re.fullmatch(r"[0-9a-f]{64}", node.segment_model_id(state))


def test_the_model_id_moves_when_any_part_of_the_state_moves():
    node = _state_node()
    base = {
        "schema": SEGMENT_SCHEMA,
        "algorithm": "kmeans",
        "features": ["f0", "f1"],
        "centers": [[0.0, 0.0], [9.0, 9.0]],
        "center_labels": [0, 1],
    }
    moved = {**base, "centers": [[0.0, 0.0], [9.0, 9.5]]}
    assert node.segment_model_id(base) != node.segment_model_id(moved)


@pytest.mark.parametrize("split", ["val", "cal", "test", None])
def test_fit_repeats_the_train_gate_for_a_caller_that_skipped_the_first(split):
    """The SECOND gate (§3.1). ``validate_train_inputs`` refuses first, but a
    caller reaching ``fit`` directly never passed through it."""
    node = _state_node()
    params = {**node.params, "fit_split": split}
    with pytest.raises(ValueError, match="fit_split"):
        node.fit(SEGMENT_FIT_ROWS, params)


@pytest.mark.parametrize("algorithm", ["kmeans", "minibatch_kmeans", "birch"])
def test_a_constructor_refusal_names_the_node_key_and_the_algorithm(
    monkeypatch, algorithm
):
    import importlib

    from dskit.pipeline.libs.sklearn import _SEGMENT_PATHS

    module_name, _, class_name = _SEGMENT_PATHS[algorithm].rpartition(".")

    class Rejects:
        def __init__(self, **kwargs):
            raise TypeError("unexpected keyword argument 'nope'")

    monkeypatch.setattr(
        importlib.import_module(module_name), class_name, Rejects
    )
    node = _state_node(algorithm=algorithm, algorithm_params={"nope": 1},
                       seed=_DROP)
    with pytest.raises(ValueError, match=rf"regime:.*{algorithm}"):
        node.fit(SEGMENT_FIT_ROWS, node.params)


@pytest.mark.parametrize("algorithm", ["kmeans", "birch"])
def test_a_fit_refusal_from_the_library_names_the_node_key_and_algorithm(
    monkeypatch, algorithm
):
    """An unwrapped sklearn ``fit`` error names neither the node nor the
    algorithm — the one refusal in this class that would identify nothing."""
    import importlib

    from dskit.pipeline.libs.sklearn import _SEGMENT_PATHS

    module_name, _, class_name = _SEGMENT_PATHS[algorithm].rpartition(".")

    class Explodes:
        def __init__(self, **kwargs):
            pass

        def fit(self, matrix):
            raise ValueError("n_samples=4 should be >= n_clusters=99")

    monkeypatch.setattr(
        importlib.import_module(module_name), class_name, Explodes
    )
    node = _state_node(algorithm=algorithm, algorithm_params={},
                       seed=_DROP if algorithm == "birch" else 17)
    with pytest.raises(ValueError, match=rf"regime:.*{algorithm}"):
        node.fit(SEGMENT_FIT_ROWS, node.params)


# -- degenerate fitted output: one atomic fixture per branch ----------------

_KM = "cluster_centers_"
_BC, _BL = "subcluster_centers_", "subcluster_labels_"

#: ``(id, algorithm, fitted attributes, refusal fragment)``. Each stub
#: breaks exactly ONE invariant: an ``or``-combined implementation could
#: half-satisfy any of these and still pass a compound fixture.
_DEGENERATE_FITS = [
    ("empty-center-set", "kmeans", {_KM: []}, "no centers"),
    ("wrong-center-width", "kmeans", {_KM: [[0.0]]}, "width"),
    ("non-finite-center", "kmeans", {_KM: [[0.0, float("inf")]]}, "finite"),
    ("centers-attribute-missing", "kmeans", {}, _KM),
    ("labels-attribute-missing", "birch", {_BC: [[0.0, 1.0]]}, _BL),
    ("non-int-label", "birch", {_BC: [[0.0, 1.0]], _BL: [0.5]}, "center_labels"),
    ("bool-label", "birch", {_BC: [[0.0, 1.0]], _BL: [True]}, "center_labels"),
    ("negative-label", "birch", {_BC: [[0.0, 1.0]], _BL: [-1]}, "center_labels"),
    (
        "label-count-mismatch",
        "birch",
        {_BC: [[0.0, 1.0], [1.0, 0.0]], _BL: [0]},
        "center_labels",
    ),
]
_DEGENERATE_IDS = [case[0] for case in _DEGENERATE_FITS]


@pytest.mark.parametrize(
    "_id,algorithm,attributes,fragment", _DEGENERATE_FITS, ids=_DEGENERATE_IDS
)
def test_a_degenerate_fitted_estimator_refuses_before_state_is_written(
    monkeypatch, _id, algorithm, attributes, fragment
):
    _stub_cluster(monkeypatch, algorithm, **attributes)
    node = _state_node(algorithm=algorithm, algorithm_params={},
                       seed=_DROP if algorithm == "birch" else 17)
    with pytest.raises(ValueError, match=re.escape(fragment)):
        node.fit(SEGMENT_FIT_ROWS, node.params)


def test_a_healthy_stub_proves_the_degenerate_fixtures_isolate_one_branch(
    monkeypatch,
):
    """The control: the same stub shape, with nothing broken, fits clean —
    so every refusal above is its own broken invariant, not the stub."""
    _stub_cluster(monkeypatch, "kmeans", **{_KM: [[0.0, 0.0], [9.0, 9.0]]})
    node = _state_node(algorithm="kmeans", algorithm_params={})
    state = node.fit(SEGMENT_FIT_ROWS, node.params)
    assert state["centers"] == [[0.0, 0.0], [9.0, 9.0]]
    assert state["center_labels"] == [0, 1]


# -- the sidecar: what a RESTORE is held to --------------------------------


def _sidecar_ctx(tmp_path, name="segmentrun"):
    return NodeContext(name="t", asof=ASOF, run_dir=str(tmp_path / name))


def _persist(ctx, node, state, *, fit_split="train", node_class=None):
    """Write the sidecar exactly as ``run_train`` does, and answer its path."""
    return node.write_artifact(ctx, SIDECAR_NAME, {
        "node_class": node_class or class_ref(type(node)),
        "fit_split": fit_split,
        "n_fit_rows": len(SEGMENT_FIT_ROWS),
        "state": state,
    })


def _fitted_state(node=None):
    pytest.importorskip("sklearn")
    node = node or _state_node()
    return node.fit(SEGMENT_FIT_ROWS, node.params)


def test_a_state_written_and_read_back_survives_the_round_trip(tmp_path):
    ctx = _sidecar_ctx(tmp_path)
    node = _state_node()
    state = _fitted_state(node)
    payload = node._sidecar(ctx, _persist(ctx, node, state))
    assert payload["state"] == state


def test_the_segment_writes_no_native_model_beside_its_json_state(tmp_path):
    """The whole reason the catalog is closed: a segmentation persists
    centers and labels, so a serving run restores JSON and no pickle."""
    ctx = _sidecar_ctx(tmp_path)
    node = _state_node()
    path = _persist(ctx, node, _fitted_state(node))
    assert os.listdir(os.path.dirname(path)) == [SIDECAR_NAME]


@pytest.mark.parametrize("split", ["val", "cal", "test", None])
def test_a_restored_artifact_fitted_off_train_refuses_even_when_undeclared(
    tmp_path, split
):
    """ADR-0040 lets a load omit ``fit_split``, so the base's own
    restate-never-misdescribe check sees nothing to compare. The new
    ``sidecar_problems`` hook is the only place this is caught."""
    ctx = _sidecar_ctx(tmp_path)
    fitting = _state_node()
    state = _fitted_state(fitting)
    path = _persist(ctx, fitting, state, fit_split=split)

    loading = ApplyWouldRaise(
        "regime",
        _segment_params(fit_split=_DROP, seed=_DROP, algorithm_params=_DROP),
        mode="load",
        artifact=path,
    )
    with pytest.raises(ValueError, match="fit_split"):
        loading._sidecar(ctx, path)


def test_a_restored_artifact_fitted_on_train_is_accepted_with_no_declaration(
    tmp_path
):
    ctx = _sidecar_ctx(tmp_path)
    fitting = _state_node()
    state = _fitted_state(fitting)
    path = _persist(ctx, fitting, state)

    loading = ApplyWouldRaise(
        "regime",
        _segment_params(fit_split=_DROP, seed=_DROP, algorithm_params=_DROP),
        mode="load",
        artifact=path,
    )
    assert loading._sidecar(ctx, path)["state"] == state


#: ``(id, state mutation, refusal fragment)`` — every way a stored state can
#: contradict its own schema, one fixture each.
_CORRUPT_STATES = [
    ("wrong-schema-tag", {"schema": "dskit.sklearn-segment/v2"}, "schema"),
    ("missing-key", {"centers": None}, "centers"),
    ("extra-key", {"inertia": 1.0}, "inertia"),
    ("off-catalog-algorithm", {"algorithm": "dbscan"}, "algorithm"),
    ("empty-center-set", {"centers": []}, "centers"),
    ("ragged-centers", {"centers": [[0.0, 0.0], [1.0]]}, "centers"),
    ("non-numeric-center", {"centers": [[0.0, "x"]]}, "centers"),
    ("label-count-mismatch", {"center_labels": [0]}, "center_labels"),
    ("negative-label", {"center_labels": [-1, 0]}, "center_labels"),
    ("bool-label", {"center_labels": [True, False]}, "center_labels"),
    ("features-not-a-key-list", {"features": ["f0", ""]}, "features"),
]
_CORRUPT_IDS = [case[0] for case in _CORRUPT_STATES]

_SOUND_STATE = {
    "schema": SEGMENT_SCHEMA,
    "algorithm": "kmeans",
    "features": ["f0", "f1"],
    "centers": [[0.0, 0.0], [9.0, 9.0]],
    "center_labels": [0, 1],
}


def test_the_sound_state_fixture_really_is_sound():
    """The control for the corruption table below."""
    assert _state_node().state_problems(dict(_SOUND_STATE)) == []


@pytest.mark.parametrize(
    "_id,mutation,fragment", _CORRUPT_STATES, ids=_CORRUPT_IDS
)
def test_a_corrupt_stored_state_is_refused_by_name(_id, mutation, fragment):
    state = {**_SOUND_STATE, **mutation}
    state = {k: v for k, v in state.items() if v is not None}
    problems = _state_node().state_problems(state)
    assert any(fragment in p for p in problems), problems


@pytest.mark.parametrize(
    "knob,value",
    [("algorithm", "birch"), ("features", ["f1", "f0"]), ("features", ["f0"])],
)
def test_a_document_that_misdescribes_the_restored_state_refuses(knob, value):
    """A document may restate what a state is, never misdescribe it — the
    same rule ``node_class`` and ``fit_split`` already carry."""
    node = _state_node(**{knob: value, "seed": _DROP, "algorithm_params": {}})
    problems = node.state_problems(dict(_SOUND_STATE))
    assert any(knob in p for p in problems), problems


def test_a_corrupt_state_reaches_the_refusal_through_the_sidecar_too(tmp_path):
    """``state_problems`` is asked by ``_sidecar``, not only by tests."""
    ctx = _sidecar_ctx(tmp_path)
    node = _state_node()
    path = _persist(ctx, node, {**_SOUND_STATE, "center_labels": [0]})
    with pytest.raises(ValueError, match="center_labels"):
        node._sidecar(ctx, path)


# ---------------------------------------------------------------------------
# SklearnSegment — assignment: the concrete class, and what it emits
# ---------------------------------------------------------------------------

#: Two tight clouds with an exact midpoint at (4.5, 4.5), so a tie is
#: constructible rather than hoped for.
SEGMENT_STREAM = [
    {"f0": 0.0, "f1": 0.0, "keep": "a"},
    {"f0": 0.2, "f1": 0.2, "keep": "b"},
    {"f0": 9.0, "f1": 9.0, "keep": "c"},
    {"f0": 8.8, "f1": 8.8, "keep": "d"},
]


def _segment_node(key="regime", **overrides):
    return SklearnSegment(key, _segment_params(**overrides))


def _fitted_segment(tmp_path, *, name="segmentfit", **overrides):
    """A real fit through ``run``: its node, ctx, outputs and sidecar path."""
    pytest.importorskip("sklearn")
    ctx = _split_ctx(tmp_path, name)
    node = _segment_node(**overrides)
    out = node.run(ctx, {"rows": SEGMENT_TRAIN_ROWS + SEGMENT_VAL_ROWS})
    return node, ctx, out, os.path.join(node.artifact_dir(ctx), SIDECAR_NAME)


def _clouds(day, *, offset=0.0):
    """Four rows on one split day: two near the origin, two near (9, 9)."""
    return [
        {"asof_ms": day * DAY + i, "contract": f"C-{day}-{i}",
         "f0": base + offset, "f1": base + offset, "keep": f"{day}-{i}"}
        for i, base in enumerate((0.0, 0.2, 9.0, 8.8))
    ]


SEGMENT_TRAIN_ROWS = _clouds(1)
SEGMENT_VAL_ROWS = _clouds(15)


def test_the_segment_is_concrete_only_now_that_it_can_project():
    """Both hooks are its own, so it no longer needs a stand-in."""
    assert SklearnSegment.fit is not FittedTransform.fit
    assert SklearnSegment.apply_state is not FittedTransform.apply_state
    assert _segment_node().params["algorithm"] == "kmeans"


@pytest.mark.parametrize("algorithm", ["kmeans", "minibatch_kmeans", "birch"])
def test_assignment_agrees_with_the_library_it_extracted_from(algorithm):
    """The whole bet of storing centers instead of a pickled model: the
    nearest-center rule must answer what the estimator's own ``predict``
    answers, for every catalog member."""
    sklearn_cluster = pytest.importorskip("sklearn.cluster")

    node = _segment_node(algorithm=algorithm, algorithm_params={"n_clusters": 2},
                         seed=_DROP if algorithm == "birch" else 17)
    state = node.fit(SEGMENT_STREAM, node.params)

    kwargs = {"n_clusters": 2}
    if algorithm != "birch":
        kwargs["random_state"] = 17
    reference = getattr(
        sklearn_cluster, _SEGMENT_PATHS[algorithm].rpartition(".")[2]
    )(**kwargs).fit([[row["f0"], row["f1"]] for row in SEGMENT_STREAM])

    matrix = [[row["f0"], row["f1"]] for row in SEGMENT_STREAM]
    assert [row["segment"] for row in node.apply_state(state, SEGMENT_STREAM,
                                                       node.params)] == [
        int(label) for label in reference.predict(matrix)
    ]


def test_an_exact_tie_goes_to_the_lowest_center_index():
    """Ties are decided by INDEX, not by whichever center was compared
    first — a rule that varied with dict or array order would assign the
    same row differently on a different machine."""
    node = _segment_node()
    state = {
        "schema": SEGMENT_SCHEMA,
        "algorithm": "kmeans",
        "features": ["f0", "f1"],
        "centers": [[0.0, 0.0], [10.0, 10.0]],
        "center_labels": [7, 3],
    }
    midpoint = [{"f0": 5.0, "f1": 5.0}]
    assert node.apply_state(state, midpoint, node.params)[0]["segment"] == 7


def test_a_projected_row_keeps_every_field_and_gains_exactly_two():
    node = _segment_node()
    state = {
        "schema": SEGMENT_SCHEMA,
        "algorithm": "kmeans",
        "features": ["f0", "f1"],
        "centers": [[0.0, 0.0], [9.0, 9.0]],
        "center_labels": [0, 1],
    }
    out = node.apply_state(state, SEGMENT_STREAM, node.params)

    assert len(out) == len(SEGMENT_STREAM)
    for before, after in zip(SEGMENT_STREAM, out):
        assert set(after) == set(before) | {"segment", "segment_model_id"}
        assert all(after[k] == v for k, v in before.items())
        assert after["segment_model_id"] == node.segment_model_id(state)
    assert [row["segment"] for row in out] == [0, 0, 1, 1]
    # The input rows are never mutated — a new row is emitted per row in.
    assert all("segment" not in row for row in SEGMENT_STREAM)


def test_the_run_emits_every_row_and_the_model_id_as_a_port(tmp_path):
    node, _ctx, out, _sidecar = _fitted_segment(tmp_path)
    state = out["transform"].state

    assert len(out["rows"]) == len(SEGMENT_TRAIN_ROWS) + len(SEGMENT_VAL_ROWS)
    assert out["segment_model_id"] == node.segment_model_id(state)
    assert all(
        row["segment_model_id"] == out["segment_model_id"] for row in out["rows"]
    )
    assert out["metrics"] == {
        "n_rows": len(SEGMENT_TRAIN_ROWS) + len(SEGMENT_VAL_ROWS),
        "n_fit_rows": len(SEGMENT_TRAIN_ROWS),
        "n_segments": len(set(state["center_labels"])),
    }
    assert all(isinstance(v, (int, float)) for v in out["metrics"].values())


def test_the_fit_saw_the_train_split_alone(tmp_path):
    """The family's leakage rule, read off the count rather than assumed."""
    _node, _ctx, out, _sidecar = _fitted_segment(tmp_path)
    assert out["metrics"]["n_fit_rows"] == len(SEGMENT_TRAIN_ROWS)


def test_a_load_restores_the_identical_state_and_never_refits(tmp_path):
    node, ctx, trained, sidecar = _fitted_segment(tmp_path)
    served = SklearnSegment(
        "regime",
        _segment_params(fit_split=_DROP, seed=_DROP, algorithm_params=_DROP),
        mode="load",
        artifact=sidecar,
    )
    bare = NodeContext(name="t", asof=ASOF, run_dir=ctx.run_dir)
    out = served.run(bare, {"rows": SEGMENT_VAL_ROWS})

    assert out["transform"].state == trained["transform"].state
    assert out["segment_model_id"] == trained["segment_model_id"]
    assert out["metrics"]["n_fit_rows"] == 0
    assert [row["segment"] for row in out["rows"]] == [
        row["segment"] for row in trained["rows"][len(SEGMENT_TRAIN_ROWS):]
    ]


def test_the_purity_screen_passes_because_assignment_reads_one_row(tmp_path):
    """``apply_state`` is row-wise by construction; the base's screen is
    what proves it, and it runs on every fit above."""
    _node, _ctx, out, _sidecar = _fitted_segment(tmp_path)
    assert out["rows"]


# -- the seed is real, not decorative --------------------------------------

#: Rows placed so more than one locally-optimal partition is reachable
#: from different random starts — ``center_labels`` alone is positional by
#: definition and proves nothing, so the fixture must make ``centers``
#: themselves move with the initialization.
SEED_ROWS = [
    {"f0": x, "f1": y}
    for x, y in (
        (0.0, 0.0), (0.4, 0.1), (0.1, 0.5), (1.0, 1.1), (1.2, 0.9),
        (2.0, 2.2), (2.3, 1.9), (2.1, 2.5), (3.0, 3.1), (3.4, 2.8),
        (4.0, 4.2), (4.3, 3.9), (5.0, 5.1), (5.2, 4.8), (6.0, 6.2),
        (6.3, 5.9), (7.0, 7.1), (7.2, 6.8), (8.0, 8.2), (8.3, 7.9),
    )
]

#: Per algorithm, the two seeds verified (in RED) to reach DIFFERENT local
#: optima on ``SEED_ROWS``, and the cluster count that makes them do it. A
#: fixture sized for one member can converge regardless of seed for the
#: other, so neither is assumed.
_SEED_CASES = {
    "kmeans": ({"n_clusters": 5, "n_init": 1}, 0, 3),
    "minibatch_kmeans": ({"n_clusters": 5, "n_init": 1, "batch_size": 4}, 0, 3),
}


@pytest.mark.parametrize("algorithm", sorted(_SEED_CASES))
def test_the_same_seed_reproduces_the_same_centers(algorithm):
    pytest.importorskip("sklearn")
    kwargs, seed, _other = _SEED_CASES[algorithm]
    node = _segment_node(algorithm=algorithm, algorithm_params=kwargs, seed=seed)
    first = node.fit(SEED_ROWS, node.params)
    second = node.fit(SEED_ROWS, node.params)

    assert first["centers"] == second["centers"]
    assert node.segment_model_id(first) == node.segment_model_id(second)


@pytest.mark.parametrize("algorithm", sorted(_SEED_CASES))
def test_a_different_seed_reaches_different_centers(algorithm):
    """The half that proves the knob is THREADED rather than dropped:
    same-seed reproducibility alone cannot tell a real seed from a fit
    that is deterministic whatever the seed says."""
    pytest.importorskip("sklearn")
    kwargs, seed, other = _SEED_CASES[algorithm]
    one = _segment_node(algorithm=algorithm, algorithm_params=kwargs, seed=seed)
    two = _segment_node(algorithm=algorithm, algorithm_params=kwargs, seed=other)

    assert one.fit(SEED_ROWS, one.params)["centers"] != two.fit(
        SEED_ROWS, two.params
    )["centers"]


# -- registration, now that the class is concrete --------------------------


def test_the_segment_is_exported_and_registered():
    import dskit.pipeline.libs.sklearn as pack

    assert "SklearnSegment" in pack.__all__
    assert "SEGMENT_SCHEMA" in pack.__all__
    registry = NodeKindRegistry()
    register(registry)
    register(registry)  # idempotent: a present name is skipped, never shadowed
    cls, owned = registry.get("sklearn-segment")
    assert cls is SklearnSegment and owned is False


# -- review corrections: the shared kwargs rule, and the bounded row rule ---


def test_the_shared_kwargs_rule_still_accepts_what_a_written_bundle_carries():
    """``_kwargs_problems`` is also what ``load_bundle`` grades a written
    manifest by, and ``head_params`` is HASH MATERIAL — so narrowing it
    would strand bundles that already exist, with no repair path: editing
    the manifest to drop the key moves the content hash. The empty-key
    rule belongs to the one node that wants it, not to the shared rule.
    """
    from dskit.pipeline.libs.sklearn import _kwargs_problems

    assert _kwargs_problems("estimator_params", {"": 1}) == []
    assert _kwargs_problems("selector_params", {"": 1}) == []
    assert SklearnFit.validate_params({
        "estimator": RIDGE, "features": ["x"], "label": "y",
        "estimator_params": {"": 1},
    }) == []
    assert _kwargs_problems("estimator_params", {3: 1})  # non-string still refused


def test_the_segment_still_refuses_an_empty_algorithm_params_key():
    problems = SklearnSegment.validate_params(
        _segment_params(algorithm_params={"": 1})
    )
    assert any("algorithm_params" in p and "empty key" in p for p in problems)


def test_the_row_rule_names_the_first_offender_not_every_row():
    """One message per broken RULE, not per row. A stream of identically
    broken rows would otherwise answer one string per row per feature:
    unreadable at a hundred rows, and the validator itself becoming the
    memory event at a million. ``Standardize.row_problems`` names the
    first offender for the same reason."""
    problems = _segment().validate_common_inputs(
        {"rows": [{"f0": 0.0} for _ in range(5_000)]}
    )
    assert len(problems) == 1
    assert "rows[0]" in problems[0] and "'f1'" in problems[0]


def test_every_distinct_broken_rule_is_still_reported_once():
    """Bounding must not collapse DIFFERENT rules into one message."""
    rows = [
        {"f0": 0.0},                               # f1 absent
        {"f0": 0.0, "f1": True},                   # f1 not a number
        {**SEGMENT_ROW, "segment": 0},             # one reserved key
        {**SEGMENT_ROW, "segment_model_id": "x"},  # the other
        "not a mapping at all",
    ]
    assert len(_segment().validate_common_inputs({"rows": rows})) == 5


def test_fit_refuses_the_very_rows_its_own_row_rule_refuses():
    """The pack's shared matrix reader counts a bool as 0/1; this class
    does not. Without the repeated rule, a caller reaching ``fit()``
    directly learned a persistable state from bools that ``apply_state``
    then refused to assign — the state and the projection disagreeing
    about the same rows."""
    pytest.importorskip("sklearn")
    node = _state_node(algorithm_params={"n_clusters": 2})
    rows = [{"f0": True, "f1": False}, {"f0": False, "f1": True}]
    with pytest.raises(ValueError, match="not a finite real number"):
        node.fit(rows, node.params)


def test_an_omitted_seed_fits_exactly_as_an_explicit_seed_of_zero_does():
    """The default's VALUE, not merely that it HAS one.

    Two halves, and the first alone is not enough. Asserting that two
    omitting fits agree with each other is true of ANY fixed default, so
    it catches a dropped default (``random_state=None``) but not a MOVED
    one — and ``seed`` is not a config-identity input for an omitting
    document, so moving it computes a different segmentation and a
    different ``segment_model_id`` under the SAME config hash and the
    same run directory, orphaning every artifact keyed to the old one.

    The literal ``0`` is deliberate: this test IS the pin between the
    constant, the class docstring and the plan's §3.1, and comparing
    against ``DEFAULT_SEGMENT_SEED`` would move both sides together. The
    comparison against seed 1 is what keeps the first assertion from
    being vacuous — it proves the knob is threaded at all.
    """
    pytest.importorskip("sklearn")
    kwargs = {"n_clusters": 5, "n_init": 1}
    omitted = _state_node(seed=_DROP, algorithm_params=kwargs)
    zero = _state_node(seed=0, algorithm_params=kwargs)
    one = _state_node(seed=1, algorithm_params=kwargs)

    state = omitted.fit(SEED_ROWS, omitted.params)
    assert state["centers"] == omitted.fit(SEED_ROWS, omitted.params)["centers"]
    assert state["centers"] == zero.fit(SEED_ROWS, zero.params)["centers"]
    assert state["centers"] != one.fit(SEED_ROWS, one.params)["centers"]
    assert omitted.segment_model_id(state) == zero.segment_model_id(
        zero.fit(SEED_ROWS, zero.params)
    )


# ---------------------------------------------------------------------------
# The conformance hookup (docs/24 §10 step 8)
# ---------------------------------------------------------------------------

EXPECTED_ROLES = {
    "sklearn-fit": "train",
    "sklearn-predict": "signal",
    "sklearn-select": "fitted_transform",
    "sklearn-segment": "fitted_transform",
}


def _selected(tmp_path):
    """A REAL fitted selection: its ctx, its carrier and its sidecar path.

    The family's load check needs an artifact that exists — a state
    restored from nothing proves nothing — so the factory fits once, on
    the split whose rows say the constant column carries nothing.
    """
    ctx = _split_ctx(tmp_path, "selectfixture")
    node = SklearnSelect("fixture_select", dict(SELECT_PARAMS))
    out = node.run(ctx, {"rows": SELECT_TRAIN_ROWS + SELECT_VAL_ROWS})
    return ctx, out["transform"], os.path.join(node.artifact_dir(ctx), SIDECAR_NAME)


def _segmented(tmp_path):
    """A REAL fitted segmentation: its ctx, sidecar, model id and the
    projection it gives a DISTINCT probe stream.

    The probe's rows are not the fixture's rows, and its expected output
    is what the FIXTURE's centers make of them — so a node that quietly
    refitted on the probe stream (two clouds at different places) could
    not answer the same labels or the same model id.
    """
    ctx = _split_ctx(tmp_path, "segmentfixture")
    node = SklearnSegment("fixture_segment", dict(SEGMENT_PARAMS))
    out = node.run(ctx, {"rows": SEGMENT_TRAIN_ROWS + SEGMENT_VAL_ROWS})
    state = out["transform"].state
    stream = _clouds(15, offset=0.05)
    return (
        ctx,
        os.path.join(node.artifact_dir(ctx), SIDECAR_NAME),
        out["segment_model_id"],
        stream,
        node.apply_state(state, stream, node.params),
    )


def probes(tmp_path):
    """One populated probe per kind. The fixture artifact is a REAL fit
    (``y = x``); the probes' wired rows carry the OPPOSITE relationship
    (``y = 1 - x``), so ``verify_loaded`` discriminates a restore from a
    silent refit on the prediction itself, not just on paperwork."""
    pytest.importorskip("sklearn")
    select_ctx, carrier, select_sidecar = _selected(tmp_path)
    (
        segment_ctx,
        segment_sidecar,
        segment_model_id,
        segment_stream,
        segment_expected,
    ) = _segmented(tmp_path)
    fitted = SklearnFit("fixture_fit", dict(FIT_PARAMS)).run(
        _ctx(tmp_path, "fixture"), {"rows": rows_linear()}
    )
    pinned = fitted["artifact_path"]
    expected = fitted["signal"].predict(SAMPLE)

    def restored(signal):
        return (
            signal is not None
            and getattr(signal, "loaded", False) is True
            and getattr(signal, "artifact_path", None) == pinned
            and abs(signal.predict(SAMPLE) - expected) < 1e-9
        )

    def verify_fit_loaded(out):
        return (
            out.get("artifact_path") == pinned
            and out.get("metrics", {}).get("loaded") == 1.0
            and restored(out.get("signal"))
        )

    return {
        "sklearn-fit": NodeProbe(
            params=dict(FIT_PARAMS),
            required=("estimator", "features", "label"),
            inputs={"rows": rows_inverted()},
            stream_ports=("rows",),
            runnable=True,
            load_artifact=pinned,
            verify_loaded=verify_fit_loaded,
        ),
        "sklearn-predict": NodeProbe(
            params={"artifact": pinned},
            required=("artifact",),
            inputs={},
            stream_ports=(),
            runnable=True,
            load_artifact=pinned,
            verify_loaded=lambda out: restored(out.get("signal")),
        ),
        # ``fit_split`` is NOT listed as required: the planner refuses it
        # (it can see the splits section), validate_params cannot.
        "sklearn-select": NodeProbe(
            params=dict(SELECT_PARAMS),
            required=("features", "selector"),
            inputs={"rows": SELECT_TRAIN_ROWS + SELECT_VAL_ROWS},
            stream_ports=("rows",),
            runnable=True,
            ctx=select_ctx,
            load_artifact=select_sidecar,
            verify_loaded=lambda out: (
                out["metrics"]["n_fit_rows"] == 0
                and out["transform"].state == carrier.state
            ),
        ),
        # ``fit_split`` is NOT required here either, for the same reason:
        # only the planner can see whether the document carves the split.
        "sklearn-segment": NodeProbe(
            params=dict(SEGMENT_PARAMS),
            required=("algorithm", "features"),
            inputs={"rows": SEGMENT_TRAIN_ROWS + SEGMENT_VAL_ROWS},
            stream_ports=("rows",),
            runnable=True,
            ctx=segment_ctx,
            load_artifact=segment_sidecar,
            verify_loaded=lambda out: (
                out["metrics"]["n_fit_rows"] == 0
                and out["segment_model_id"] == segment_model_id
                and out["transform"].apply(segment_stream) == segment_expected
            ),
        ),
    }


TestSklearnConformance = conformance_suite(
    registry=NODE_KINDS,
    module="dskit.pipeline.libs.sklearn",
    probes=probes,
    expected_roles=EXPECTED_ROLES,
    name="TestSklearnConformance",
)



# -- the document doorway: plan-time rules and a real run ------------------

#: The synthetic data kind stamps its events around day 1000, so the cuts
#: are placed where its rows actually are rather than where a hand-written
#: fixture would like them to be.
SEGMENT_DOC_SPLITS = {
    "train_end_ms": 1005 * DAY,
    "val_end_ms": 1020 * DAY,
    "test_end_ms": 2000 * DAY,
}


def _segment_document(tmp_path, **params):
    from dskit.pipeline.base import OutputsConfig, TimeSplitConfig
    from dskit.pipeline.document import NodeSpec

    declared = {
        "fit_split": "train",
        "features": ["mid", "p_true"],
        "algorithm": "kmeans",
        "algorithm_params": {"n_clusters": 2, "n_init": 1},
        "seed": 17,
        **params,
    }
    return PipelineDocument(
        name="segment-doc",
        splits=TimeSplitConfig(**SEGMENT_DOC_SPLITS),
        pipeline={
            "rows": NodeSpec(
                uses="dskit.pipeline.synthetic_nodes:SynthEvents",
                params={"n_events": 24, "n_instruments": 2, "seed": 3},
            ),
            "regime": NodeSpec(
                uses="sklearn-segment",
                inputs={"rows": "$rows.events"},
                params={k: v for k, v in declared.items() if v is not _DROP},
            ),
        },
        outputs=OutputsConfig(run_root=str(tmp_path / "runs")),
    )


def test_a_segment_document_plans_and_runs_end_to_end(tmp_path):
    """The doorway a user actually reaches: a JSON document, the registry,
    the planner and the driver — not a directly constructed node."""
    pytest.importorskip("sklearn")
    register()
    document = _segment_document(tmp_path)
    assert plan(document).role_of("regime") == "fitted_transform"

    result = run_document(document, asof=ASOF)
    assert result.state == "ran", result.error
    out = result.outputs["regime"]

    assert out["metrics"]["n_rows"] == 48
    assert 0 < out["metrics"]["n_fit_rows"] < 48, "the fit saw one split only"
    assert out["metrics"]["n_segments"] == 2
    assert len({row["segment_model_id"] for row in out["rows"]}) == 1
    assert out["segment_model_id"] == out["rows"][0]["segment_model_id"]
    assert all("segment" in row for row in out["rows"])


def test_a_segment_document_that_fits_off_train_refuses_at_the_run(tmp_path):
    """The leakage gate, reached the way a document reaches it."""
    pytest.importorskip("sklearn")
    register()
    document = _segment_document(tmp_path, fit_split="val")
    result = run_document(document, asof=ASOF)
    assert result.state != "ran"
    assert "fit_split" in str(result.error)

# ---------------------------------------------------------------------------
# The example document — loads, hashes, plans, runs
# ---------------------------------------------------------------------------


def test_example_document_loads_hashes_and_plans():
    doc = PipelineDocument.from_obj(json.loads(EXAMPLE.read_text()))
    assert len(doc.hash) == 64  # identity computes over the real grammar
    planned = plan(doc)  # default registry: the toolkit's own kinds
    assert planned.order.index("qhat") < planned.order.index("validate")
    assert planned.role_of("qhat") == "train"
    assert planned.role_of("validate") == "score"
    assert ("qhat", "validate") in planned.edges


def test_example_document_runs_end_to_end(tmp_path):
    pytest.importorskip("sklearn")
    obj = json.loads(EXAMPLE.read_text())
    obj["outputs"] = {"run_root": str(tmp_path)}  # hash-excluded override
    result = run_document(PipelineDocument.from_obj(obj), asof=ASOF)
    assert result.state == "ran" and result.exit_code == 0
    qhat = result.outputs["qhat"]
    assert qhat["metrics"]["loaded"] == 0.0
    assert os.path.isfile(qhat["artifact_path"])
    assert os.path.isfile(qhat["artifact_path"] + ".json")
    scored = result.outputs["validate"]["metrics"]
    assert scored["n"] >= 5
    assert 0.0 <= scored["loss"] <= 1.0  # brier over real predict_proba beliefs


# ---------------------------------------------------------------------------
# The model-sweep cookbook — the doorway IS the model registry
# ---------------------------------------------------------------------------

SWEEP_EXAMPLE = (
    pathlib.Path(__file__).parents[2] / "examples" / "pipeline" / "model-sweep.json"
)

#: The candidate list, restated INDEPENDENTLY of the document and the
#: docstring table both tests below check — an expectation sourced from
#: its own subject would assert nothing (CLAUDE.md, the deliberate
#: restatement exception).
SWEPT_ESTIMATORS = (
    "sklearn.linear_model.LinearRegression",
    "sklearn.linear_model.Ridge",
    "sklearn.ensemble.RandomForestRegressor",
    "sklearn.ensemble.GradientBoostingRegressor",
    "sklearn.svm.SVR",
    "sklearn.neighbors.KNeighborsRegressor",
)


def sweep_space():
    """The shipped example's ``sweep.space["model.estimator"]`` list."""
    obj = json.loads(SWEEP_EXAMPLE.read_text())
    return obj["pipeline"]["sweep"]["params"]["space"]["model.estimator"]


def docs_table_estimators():
    """Every ``estimator`` cell of the pack docstring's cookbook table, in
    document order — the table is the docs half of the pin below."""
    import dskit.pipeline.libs.sklearn as pack

    out = []
    for line in pack.__doc__.splitlines():
        stripped = line.strip()
        if not stripped.startswith("| ``"):
            continue
        out.append(stripped.split("|")[1].strip().strip("`"))
    return out


def test_model_sweep_example_loads_hashes_and_plans():
    doc = PipelineDocument.from_obj(json.loads(SWEEP_EXAMPLE.read_text()))
    assert len(doc.hash) == 64
    planned = plan(doc)  # default registry: hpo-grid and validate are owned
    assert planned.role_of("sweep") == "search"
    assert planned.role_of("model") == "train"
    assert planned.order.index("model") < planned.order.index("sweep")
    assert ("sweep", "report") in planned.edges


def test_model_sweep_space_is_the_documented_candidate_list():
    # The list lives in TWO places by necessity — the runnable document
    # and the pack's docs table — so the agreement is pinned, not hoped.
    assert tuple(sweep_space()) == SWEPT_ESTIMATORS
    table = docs_table_estimators()
    assert tuple(e for e in table if e.startswith("sklearn.")) == SWEPT_ESTIMATORS


def readme_sklearn_bullet():
    """The pipeline README's sklearn entry — the third prose copy of the
    cookbook's names (extra, estimator class), folded into the pins below
    so it cannot drift alone."""
    text = (
        pathlib.Path(__file__).parents[2] / "dskit" / "pipeline" / "README.md"
    ).read_text()
    start = text.index("**sklearn**")
    return text[start : text.index("**torch**", start)]


def test_model_sweep_ships_without_the_lightgbm_extra():
    # The table documents "lightgbm.LGBMRegressor" as a candidate; the
    # SHIPPED space must stay sklearn-only, or the example stops running
    # for anyone who installed dskit[sklearn] and nothing else.
    assert "lightgbm.LGBMRegressor" in docs_table_estimators()
    assert all(e.startswith("sklearn.") for e in sweep_space())
    # The README restates the non-sklearn class path; every such path it
    # names must be a row of the docs table, so renaming the row without
    # the README (or vice versa) fails here rather than shipping a
    # reader-facing name the doorway no longer documents.
    readme_classes = set(
        re.findall(r"\b([a-z]\w*(?:\.\w+)*\.[A-Z]\w+)\b", readme_sklearn_bullet())
    )
    assert "lightgbm.LGBMRegressor" in readme_classes
    assert readme_classes <= set(docs_table_estimators()), sorted(readme_classes)


def run_sweep_example(run_root, obj=None):
    """Run the shipped cookbook (or a mutated copy of it) and return the
    driver result. ``run_root`` is hash-excluded, so overriding it leaves
    the document's identity untouched."""
    obj = json.loads(SWEEP_EXAMPLE.read_text()) if obj is None else obj
    obj["outputs"] = {"run_root": str(run_root)}
    return run_document(PipelineDocument.from_obj(obj), asof=ASOF)


def test_model_sweep_example_runs_end_to_end_and_picks_a_winner(tmp_path):
    pytest.importorskip("sklearn")
    result = run_sweep_example(tmp_path)
    assert result.state == "ran" and result.exit_code == 0
    sweep = result.outputs["sweep"]
    # Exhaustive: one trial per candidate, each actually fitted.
    tried = [t["overrides"]["model.estimator"] for t in sweep["trials"]]
    assert tuple(tried) == SWEPT_ESTIMATORS
    assert sweep["best_score"] == min(t["score"] for t in sweep["trials"])
    # The sweep SELECTS, it does not merely run — and on an HONEST
    # train-only fit the plain linear baseline wins this synthetic market
    # (its mid is a shrunken affine function of the truth, so there is no
    # interaction for a tree to find and the forest trails at 0.22-0.26).
    # Every rival here is deterministic; the winner's margin over the
    # runner-up (kNN, 0.176) sits far outside the unseeded forest's
    # spread.
    assert sweep["best_params"] == {
        "model.estimator": "sklearn.linear_model.LinearRegression"
    }
    assert sweep["best_score"] < 0.17


def test_model_sweep_survives_the_dataset_swap_its_notes_invite(tmp_path):
    # The dataset note promises "Swap this node for your own data source
    # and the sweep below is unchanged" — so the metric may not be a
    # landmine under nearby data. All six candidates predict through
    # UNBOUNDED `predict`, and on this exact nearby dataset (measured)
    # SVR extrapolates a belief of ~1.025: `brier` guards a [0, 1] belief
    # contract and would kill the whole sweep mid-run on it. The document
    # therefore scores with `squared_error` — the documented EXCEPTION to
    # tier-1's venue rule (dskit/pipeline/metrics.py, ADR-0025: binary
    # venues score logloss/brier), which its validate note must cite and
    # defer to rather than restate as a rule of its own
    # (examples/pipeline/sklearn-fit.json keeps the venue rule as
    # written: bounded predict_proba beliefs, brier).
    pytest.importorskip("sklearn")
    obj = json.loads(SWEEP_EXAMPLE.read_text())
    obj["pipeline"]["dataset"]["params"].update({"seed": 22, "n_instruments": 1})
    result = run_sweep_example(tmp_path, obj)
    assert result.state == "ran" and result.exit_code == 0
    assert result.outputs["sweep"]["best_params"]["model.estimator"] in SWEPT_ESTIMATORS
    shipped = json.loads(SWEEP_EXAMPLE.read_text())
    assert shipped["pipeline"]["validate"]["params"]["metric"] == "squared_error"
    assert "brier" in shipped["pipeline"]["validate"]["notes"]
    assert "dskit/pipeline/metrics.py" in shipped["pipeline"]["validate"]["notes"]
    # The fact those notes cite, pinned rather than quoted: flip ONLY the
    # metric to brier over the same swapped data and the run dies on
    # brier's own domain guard — the out-of-[0, 1] belief refused by name.
    flipped = json.loads(json.dumps(obj))
    flipped["pipeline"]["validate"]["params"]["metric"] = "brier"
    result = run_sweep_example(tmp_path / "brier", flipped)
    assert result.state == "error" and result.exit_code != 0
    assert "brier: q must lie in [0, 1]" in result.error


def test_dataset_swap_recipe_says_the_split_cuts_move_with_the_data(tmp_path):
    # The other half of the swap invitation. The three `splits` cuts are
    # ABSOLUTE epoch-ms instants placed inside SynthEvents' default span
    # (start day 1000, i.e. 1972-73); a real data source starts elsewhere,
    # and cuts the data never straddles leave `train_rows` empty — the
    # first fit then dies on zero rows (measured below). So the notes must
    # state the coupling (an invitation whose one required edit is
    # unstated is an escape hatch nobody built), and the stated recipe —
    # move the three cuts with the data — must actually run.
    pytest.importorskip("sklearn")
    obj = json.loads(SWEEP_EXAMPLE.read_text())
    day_ms = 86_400_000
    new_start = 1_700_000_000_000  # any modern real-world stream

    stale = json.loads(json.dumps(obj))
    stale["pipeline"]["dataset"]["params"]["start_ms"] = new_start
    result = run_sweep_example(tmp_path / "stale", stale)
    assert result.state == "error" and result.exit_code != 0
    assert "cannot fit on zero rows" in result.error

    moved = json.loads(json.dumps(obj))
    moved["pipeline"]["dataset"]["params"]["start_ms"] = new_start
    delta = new_start - 1_000 * day_ms  # shipped cuts sit in the default span
    for cut in ("train_end_ms", "val_end_ms", "test_end_ms"):
        moved["splits"][cut] = obj["splits"][cut] + delta
    result = run_sweep_example(tmp_path / "moved", moved)
    assert result.state == "ran" and result.exit_code == 0
    assert result.outputs["sweep"]["best_params"]["model.estimator"] in SWEPT_ESTIMATORS

    # The notes must carry the recipe: the dataset note points at the
    # cuts, and the splits note names the failure a stale cut earns.
    assert "`splits` cuts" in obj["pipeline"]["dataset"]["notes"]
    assert "cannot fit on zero rows" in obj["splits"]["notes"]


def test_train_rows_usability_gate_is_absent_on_purpose_and_says_so(tmp_path):
    # These events carry no `usable` field (pinned below), so the filter's
    # usability gate has nothing to test: `require_usable: false` would be
    # an inert restatement of the default, and flipping it on engages the
    # sparse semantics (no field = cannot claim usability = dropped) —
    # every record dropped, run dead at the first fit (measured below).
    # The shipped document therefore does NOT declare the knob, and the
    # node's note must explain the absence, because this cookbook explains
    # every knob it does declare.
    pytest.importorskip("sklearn")
    obj = json.loads(SWEEP_EXAMPLE.read_text())
    train_rows = obj["pipeline"]["train_rows"]
    assert "require_usable" not in train_rows["params"]
    assert "require_usable" in train_rows["notes"]
    assert "cannot fit on zero rows" in train_rows["notes"]

    result = run_sweep_example(tmp_path / "shipped", json.loads(json.dumps(obj)))
    record = result.outputs["dataset"]["events"][0]
    fields = vars(record) if hasattr(record, "__dict__") else record
    assert "usable" not in fields  # the gate would test a field that isn't there

    flipped = json.loads(json.dumps(obj))
    flipped["pipeline"]["train_rows"]["params"]["require_usable"] = True
    result = run_sweep_example(tmp_path / "flipped", flipped)
    assert result.state == "error" and result.exit_code != 0
    assert "cannot fit on zero rows" in result.error


#: n -> English word/ordinal, restated independently of the prose the
#: counts test checks (an expectation sourced from its subject would
#: assert nothing). A count drifting off this range fails loudly.
_COUNT_WORDS = {5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}
_ORDINAL_WORDS = {6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth"}


def test_restated_counts_agree_with_the_document_everywhere():
    # The cookbook's pitch is "adding a candidate is one more string in
    # the list" — so every prose copy of the candidate COUNT (and of the
    # dataset arithmetic) is pinned to the document values it restates.
    # Without this, a seventh sklearn row updates the space, the table and
    # SWEPT_ESTIMATORS (all pinned) while "the six"/"6 candidates" stay
    # silently wrong with the suite green.
    obj = json.loads(SWEEP_EXAMPLE.read_text())
    n = len(sweep_space())
    word, next_ordinal = _COUNT_WORDS[n], _ORDINAL_WORDS[n + 1]
    flat = " ".join(pack_docstring().split())  # the docstring hard-wraps
    assert f"sweeps the {word} ``sklearn.`` rows" in flat
    assert f"the {next_ordinal} needs an extra" in flat
    assert f"{n} candidates, {n} trials" in obj["pipeline"]["sweep"]["notes"]
    assert f"last of the {word}" in obj["pipeline"]["train_rows"]["notes"]
    params = obj["pipeline"]["dataset"]["params"]
    events = params["n_events"] * params["n_instruments"]
    assert (
        f"{params['n_events']} days x {params['n_instruments']} instruments "
        f"= {events} events" in obj["pipeline"]["dataset"]["notes"]
    )


def test_model_sweep_fits_on_the_train_split_only(tmp_path):
    # THE leakage pin. A "compare many models" cookbook that fits on the
    # rows it selects on crowns whichever candidate memorises hardest —
    # verified: wired to the full stream the forest "won" at ~0.03 while
    # scoring 0.22-0.26 honestly. The `train_rows` filter upstream of `model`
    # is what makes the comparison mean anything, so the row COUNTS are
    # pinned: rewiring `model` back to `$dataset.events` moves n_rows to
    # the full population and fails here.
    pytest.importorskip("sklearn")
    obj = json.loads(SWEEP_EXAMPLE.read_text())
    doc = PipelineDocument.from_obj(obj)
    result = run_sweep_example(tmp_path, obj)
    assert result.state == "ran"
    events = result.outputs["dataset"]["events"]
    by_split = {"train": 0, "val": 0}
    for event in events:
        name = doc.splits.split_of(
            SimpleNamespace(asof_ms=event["asof_ms"], cluster=event["cluster"])
        )
        if name in by_split:
            by_split[name] += 1
    assert by_split["train"] and by_split["val"]
    assert by_split["train"] < len(events)  # a real cut, not the whole stream
    assert result.outputs["model"]["metrics"]["n_rows"] == float(by_split["train"])
    assert result.outputs["validate"]["metrics"]["n"] == by_split["val"]


def test_model_sweep_winner_pass_is_what_downstream_consumed(tmp_path):
    # `report` reads $validate.metrics.loss, and the document's notes say
    # that is the WINNING model's loss — true only because the driver
    # re-executes the dirty subgraph with the winning overrides. The
    # sidecar records which estimator the surviving pass fitted, so it
    # distinguishes the winner pass from the base pass; `loaded`/file
    # existence do not (both hold for every train-mode pass).
    pytest.importorskip("sklearn")
    result = run_sweep_example(tmp_path)
    artifact = result.outputs["model"]["artifact_path"]
    assert os.path.isfile(artifact)
    sidecar = json.loads(pathlib.Path(artifact + ".json").read_text())
    winner = result.outputs["sweep"]["best_params"]["model.estimator"]
    base = json.loads(SWEEP_EXAMPLE.read_text())["pipeline"]["model"]["params"]
    assert winner != base["estimator"]  # else this pins nothing
    assert sidecar["estimator"] == winner
    assert result.outputs["validate"]["metrics"]["loss"] == pytest.approx(
        result.outputs["sweep"]["best_score"]
    )


def test_model_sweep_optuna_swap_note_is_a_working_recipe():
    # "Never document an escape hatch you did not build." The sweep note
    # offers OptunaSearch over the same list; OptunaSearch's knobs are
    # NOT hpo-grid's, so the note must state every edit. This pins the
    # stated recipe against the engine, and the bare `uses` swap against
    # the refusal it actually earns.
    from dskit.pipeline.libs.optuna import OptunaSearch

    sweep = json.loads(SWEEP_EXAMPLE.read_text())["pipeline"]["sweep"]
    bare = OptunaSearch.validate_params(sweep["params"])
    assert bare, "a bare `uses` swap must not be presented as sufficient"
    swapped = {k: v for k, v in sweep["params"].items() if k != "select"}
    swapped.update({"direction": "minimize", "n_trials": 6, "seed": 0})
    assert OptunaSearch.validate_params(swapped) == []
    # The note must NAME every knob the working recipe needed.
    for knob in ("select", "direction", "n_trials", "seed"):
        assert knob in sweep["notes"], knob


def test_model_sweep_single_model_tuning_note_is_a_working_recipe():
    # The `model` note's alternate recipe: pin the estimator and search
    # its own knobs. Overrides may only address EXISTING params, so the
    # recipe needs `estimator_params` declared in the node's params
    # block — an edit the note must state, because the same note removes
    # `estimator_params` from the shipped document.
    obj = json.loads(SWEEP_EXAMPLE.read_text())
    model = obj["pipeline"]["model"]
    model["params"]["estimator"] = RIDGE
    obj["pipeline"]["sweep"]["params"]["space"] = {
        "model.estimator_params.alpha": [0.1, 1.0, 10.0]
    }
    with pytest.raises(ConfigError, match="declares no param 'estimator_params'"):
        plan(PipelineDocument.from_obj(obj))
    model["params"]["estimator_params"] = {"alpha": 1.0}
    plan(PipelineDocument.from_obj(obj))  # the stated recipe, whole
    assert "add `estimator_params`" in model["notes"]


def test_lightgbm_resolves_through_the_doorway_with_no_wrapper(tmp_path):
    # The TODO's claim, RUN: LightGBM's sklearn-compatible API needs the
    # extra and nothing else — no pack, no wrapper class, no new kind.
    pytest.importorskip("lightgbm")
    params = {
        "estimator": "lightgbm.LGBMRegressor",
        "estimator_params": {"n_estimators": 5, "min_child_samples": 1, "verbose": -1},
        "features": ["x"],
        "label": "y",
        "seed": 3,
    }
    assert SklearnFit.validate_params(params) == []
    node = SklearnFit("lgbm", params)
    ctx = NodeContext(name="t", asof=ASOF, run_dir=str(tmp_path))
    out = node.run(ctx, {"rows": rows_linear(n=40)})
    signal = out["signal"]
    assert isinstance(signal, SklearnSignal) and signal.loaded is False
    assert isinstance(signal.predict({"x": 0.5}), float)
    sidecar = json.loads(pathlib.Path(out["artifact_path"] + ".json").read_text())
    assert sidecar["estimator"] == "lightgbm.LGBMRegressor"
    # The doorway is the point: this pack exports two nodes and one
    # signal, never a per-model class.
    import dskit.pipeline.libs.sklearn as pack

    assert not [n for n in pack.__all__ if "LGBM" in n or "Regressor" in n]


def test_lightgbm_extra_is_declared_and_covered_by_all():
    # The requirement strings necessarily appear twice — the extra and
    # the `all` bundle the libs suite installs from — so the agreement is
    # pinned. An `all` that missed one would leave CI green while the
    # cookbook's documented candidate is unimportable. The extra is
    # SELF-SUFFICIENT, on the repo's transformers precedent
    # (`transformers = ["transformers>=4.30", "torch>=2.0"]` carries the
    # runtime its pack needs): the LGBM path runs THROUGH SklearnFit,
    # whose run() imports joblib and whose LGBMRegressor is the sklearn
    # estimator API — so the documented `pip install 'dskit[lightgbm]'`
    # must pull all three, or the sweep note's own command installs a
    # candidate that cannot fit.
    import tomllib

    root = pathlib.Path(__file__).parents[2]
    with open(root / "pyproject.toml", "rb") as fh:
        extras = tomllib.load(fh)["project"]["optional-dependencies"]
    assert extras["lightgbm"] == [
        "lightgbm>=4.0",
        "scikit-learn>=1.3",
        "joblib>=1.3",
    ]
    assert set(extras["lightgbm"]) <= set(extras["all"])
    # And it is an EXTRA, never a pack: no lightgbm module under libs/.
    assert not list((root / "dskit" / "pipeline" / "libs").glob("*lightgbm*"))


# ---------------------------------------------------------------------------
# The cookbook's remaining prose claims, pinned against the engine
# ---------------------------------------------------------------------------


def pack_docstring():
    """The sklearn pack's module docstring — the tier-2 cookbook text."""
    import dskit.pipeline.libs.sklearn as pack

    return pack.__doc__


def test_colon_spelling_is_refused_at_plan_only_in_the_base_params(tmp_path):
    # Two DIFFERENT answers from the engine, so the prose may not give one.
    # A colon in the node's own params block is a plan-time shape problem;
    # a colon inside a search SPACE is not shape-checked at plan (the
    # planner never constructs trial params), so it plans clean, hashes,
    # and kills the run mid-sweep — after earlier candidates already fitted.
    pytest.importorskip("sklearn")
    obj = json.loads(SWEEP_EXAMPLE.read_text())
    base = json.loads(json.dumps(obj))
    base["pipeline"]["model"]["params"]["estimator"] = "lightgbm:LGBMRegressor"
    with pytest.raises(ConfigError, match="dotted import path"):
        plan(PipelineDocument.from_obj(base))

    spaced = json.loads(json.dumps(obj))
    spaced["pipeline"]["sweep"]["params"]["space"]["model.estimator"] = [
        RIDGE,
        "lightgbm:LGBMRegressor",
    ]
    doc = PipelineDocument.from_obj(spaced)
    plan(doc)  # NOT refused — the claim the note used to make
    assert len(doc.hash) == 64
    spaced["outputs"] = {"run_root": str(tmp_path)}
    result = run_document(PipelineDocument.from_obj(spaced), asof=ASOF)
    assert result.state == "error" and result.exit_code != 0
    assert result.outputs.get("sweep") is None

    # The note must describe THAT, not a plan-time refusal it never gets.
    note = obj["pipeline"]["sweep"]["notes"]
    assert "mid-run" in note
    assert "refused at plan" not in note


def test_unseeded_forest_moves_between_runs_of_one_identity(tmp_path):
    # The reproducibility caveat, pinned. One params block serves six
    # candidates, so it may carry no `seed` (three of them take no
    # random_state) — which leaves the forest unseeded and its recorded
    # trial score different on every run of the SAME identity hash. The
    # selection is stable regardless; the notes must say both.
    pytest.importorskip("sklearn")
    forest = "sklearn.ensemble.RandomForestRegressor"
    boosted = "sklearn.ensemble.GradientBoostingRegressor"
    first = run_sweep_example(tmp_path / "a")
    second = run_sweep_example(tmp_path / "b")

    def score(result, estimator):
        return next(
            trial["score"]
            for trial in result.outputs["sweep"]["trials"]
            if trial["overrides"]["model.estimator"] == estimator
        )

    assert score(first, forest) != score(second, forest)
    # ...while every deterministic rival, and the WINNER, hold still.
    assert score(first, boosted) == score(second, boosted)
    assert first.outputs["sweep"]["best_params"] == second.outputs["sweep"]["best_params"]
    assert "same identity" in json.loads(SWEEP_EXAMPLE.read_text())["notes"]


#: The unseeded forest's honest-score envelope actually MEASURED — 65
#: e2e runs, min 0.219 / max 0.257 (a later 60-run remeasure landed
#: inside it at 0.2224-0.2564). Restated independently of the prose band
#: the docstring quotes, so the containment pin below can fail (an
#: expectation sourced from its subject would assert nothing).
_FOREST_HONEST_ENVELOPE = (0.219, 0.257)

#: The prose quotes the band to TWO DECIMALS, so half that last digit is
#: the only slack any band check gets — the old ±0.02 slack is exactly
#: what let the prose understate the band's top edge.
_HALF_QUOTED_DIGIT = 0.005


def quoted_forest_figures():
    """The honest band and leaky point the cookbook quotes about the
    unseeded forest — parsed OUT OF the pack docstring, with the two
    document copies required to quote the same figures verbatim (the
    agreement half of the pin: three restatements, one source of truth,
    per CLAUDE.md's duplication rule). Returns ``(lo, hi, leaky)``."""
    doc = pack_docstring()
    band = re.search(r"(\d\.\d+)-(\d\.\d+) honest", doc)
    approx = re.search(r"~(\d\.\d+) leaky", doc)
    assert band and approx, "the docstring must quote both measured figures"
    obj = json.loads(SWEEP_EXAMPLE.read_text())
    for text in (obj["notes"], obj["pipeline"]["train_rows"]["notes"]):
        assert f"{band.group(1)}-{band.group(2)}" in text
    assert f"~{approx.group(1)}" in obj["pipeline"]["train_rows"]["notes"]
    return float(band.group(1)), float(band.group(2)), float(approx.group(1))


def test_documented_forest_band_contains_the_measured_envelope():
    # The deterministic half of the honest-band pin: the prose calls its
    # "0.22-0.26 honest" figure a measured envelope, so the quote must
    # CONTAIN the envelope actually recorded. Narrowing the prose back to
    # the old understated "0.22-0.25" fails HERE with no rerun lottery —
    # a single e2e run lands inside a narrowed band most of the time
    # (the top edge bit at ~2.5% per run) and would let it ride.
    lo, hi, _ = quoted_forest_figures()
    assert lo - _HALF_QUOTED_DIGIT <= _FOREST_HONEST_ENVELOPE[0]
    assert _FOREST_HONEST_ENVELOPE[1] <= hi + _HALF_QUOTED_DIGIT


def test_cookbook_figures_are_the_ones_the_engine_produces(tmp_path):
    # Every measured number the prose quotes, pinned to a real run — the
    # notes and the pack docstring both quote them, and the fields they
    # derive from (dataset params, split boundaries) are exactly what the
    # cookbook invites a reader to change. The forest's figures are
    # parsed from the prose rather than restated here, so a drifted copy
    # fails. The honest figure is the measured ENVELOPE itself
    # (_FOREST_HONEST_ENVELOPE, quoted to two decimals), so this run
    # must land INSIDE the quoted band with only the half-digit quantum
    # for slack. The leaky point keeps its "~" tolerance (envelope
    # 0.027-0.044 over 25 leaky runs).
    pytest.importorskip("sklearn")
    forest = "sklearn.ensemble.RandomForestRegressor"
    obj = json.loads(SWEEP_EXAMPLE.read_text())
    doc = PipelineDocument.from_obj(obj)
    honest = run_sweep_example(tmp_path / "honest", json.loads(json.dumps(obj)))

    counts = {"train": 0, "val": 0, "test": 0}
    for event in honest.outputs["dataset"]["events"]:
        name = doc.splits.split_of(
            SimpleNamespace(asof_ms=event["asof_ms"], cluster=event["cluster"])
        )
        if name in counts:
            counts[name] += 1
    assert counts == {"train": 146, "val": 30, "test": 32}
    assert "146 train rows, 30 val, 32 test" in obj["splits"]["notes"]

    scores = {
        trial["overrides"]["model.estimator"]: trial["score"]
        for trial in honest.outputs["sweep"]["trials"]
    }
    lo, hi, leaky_point = quoted_forest_figures()
    # HONEST: the forest lands inside the quoted band — no slack beyond
    # the two-decimal quote's own half digit (a measured 0.219 sits
    # under the quoted 0.22) — far behind the deterministic winner (min
    # observed gap 0.058, outside any spread).
    assert lo - _HALF_QUOTED_DIGIT <= scores[forest] <= hi + _HALF_QUOTED_DIGIT
    assert scores[forest] > honest.outputs["sweep"]["best_score"]

    leaky = json.loads(json.dumps(obj))
    leaky["pipeline"]["model"]["inputs"]["rows"] = "$dataset.events"
    leaked = run_sweep_example(tmp_path / "leaky", leaky)
    leaky_scores = {
        trial["overrides"]["model.estimator"]: trial["score"]
        for trial in leaked.outputs["sweep"]["trials"]
    }
    # LEAKY: the same forest "wins", near the quoted ~0.03 (its closest
    # rival, the boosted trees, memorises to a deterministic 0.065).
    assert min(leaky_scores, key=leaky_scores.get) == forest
    assert leaky_point - 0.025 <= leaky_scores[forest] <= leaky_point + 0.025


def test_docstring_names_no_extra_that_pyproject_does_not_declare():
    # "Never document an escape hatch you did not build." The cookbook
    # table's non-sklearn row leans on an EXTRA; every extra the pack's
    # prose names must exist, or `pip install 'dskit[...]'` errors out.
    # The README's sklearn bullet is the third copy of that name, so it
    # is scanned too (single backticks there, double in the docstring).
    import tomllib

    root = pathlib.Path(__file__).parents[2]
    with open(root / "pyproject.toml", "rb") as fh:
        declared = set(tomllib.load(fh)["project"]["optional-dependencies"])
    prose = (
        pack_docstring()
        + json.loads(SWEEP_EXAMPLE.read_text())["pipeline"]["sweep"]["notes"]
        + readme_sklearn_bullet()
    )
    named = set(re.findall(r"dskit\[([a-z0-9_-]+)\]", prose))
    named |= set(re.findall(r"``([a-z0-9_-]+)`` extra", prose))
    named |= set(re.findall(r"(?<!`)`([a-z0-9_-]+)` extra", prose))
    assert "lightgbm" in named, "the README bullet must still name the extra"
    assert named <= declared, sorted(named - declared)


def test_docstring_classifier_line_carries_the_binary_only_caveat():
    # The module docstring offers classifier counterparts with
    # predict_method="predict_proba"; the seam refuses >2 classes at
    # PREDICT time (pinned in
    # test_multiclass_predict_proba_refuses_rather_than_guessing), which
    # is long after the document planned and fitted. Unqualified, that
    # sentence sends a reader into a mid-run refusal.
    block = next(
        para
        for para in pack_docstring().split("\n\n")
        if "predict_proba" in para and "LogisticRegression" in para
    )
    assert "binary" in block


# ---------------------------------------------------------------------------
# ColumnSubsetEstimator (ADR-0108): a feature mask is a MODEL knob
# ---------------------------------------------------------------------------

#: A four-column design matrix naming a real child shape: two stale lags,
#: one real feature, and a trailing native categorical (``symbol_code``'s
#: own position in the pooled scan's design matrix — ADR-0108's own
#: worked example).
MASK_COLUMNS = ["ret_lag_0", "ret_lag_1", "vol_5m", "symbol_code"]


class _RecordingLstsq:
    """Least-squares stub with a LightGBM-shaped ``fit`` signature.

    Requires no external library — numpy alone — so the mask/remap logic
    below is proven correct even where sklearn and lightgbm are both
    absent; it RECORDS the ``feature_names``/``categorical_feature`` it
    was given, which is what the index-remap tests read back.
    """

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.seen_feature_names = None
        self.seen_categorical_feature = None

    def fit(self, x, y, feature_names=None, categorical_feature=None):
        """Fit by least squares; record the two forwarded kwargs."""
        self.seen_feature_names = (
            None if feature_names is None else list(feature_names)
        )
        self.seen_categorical_feature = (
            None if categorical_feature is None else list(categorical_feature)
        )
        design = np.column_stack([np.ones(x.shape[0]), x])
        self.coef_, *_ = np.linalg.lstsq(design, y, rcond=None)
        return self

    def predict(self, x):
        """Predict from the fitted least-squares coefficients."""
        design = np.column_stack([np.ones(x.shape[0]), x])
        return design @ self.coef_


class _NoKwargsStub:
    """``fit``/``predict`` with NEITHER optional kwarg — proves the
    wrapper inspects the signature rather than assuming both exist."""

    def fit(self, x, y):
        """Fit a trivial all-ones coefficient vector."""
        self.n_features_seen = int(x.shape[1])
        self.coef_ = np.ones(x.shape[1])
        return self

    def predict(self, x):
        """Predict via the fixed coefficient vector."""
        return x @ self.coef_


def _lstsq_path():
    return f"{_RecordingLstsq.__module__}.{_RecordingLstsq.__name__}"


def _no_kwargs_path():
    return f"{_NoKwargsStub.__module__}.{_NoKwargsStub.__name__}"


def mask_rows(n=40, seed=0):
    """Deterministic rows over :data:`MASK_COLUMNS`.

    ``y`` depends only on ``vol_5m`` and ``symbol_code`` — the two
    columns every mask below keeps — so a correct projection loses no
    signal and an incorrect one (wrong columns, wrong order) would.
    """
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, 4)).astype(np.float64)
    x[:, 3] = (x[:, 3] > 0.0).astype(np.float64)  # symbol_code: 0/1
    y = 2.0 * x[:, 2] + 1.5 * x[:, 3] + 0.01 * rng.normal(size=n)
    return x, y


def test_column_subset_drop_and_keep_agree_on_the_same_surviving_columns():
    """Complementary ``drop``/``keep`` masks must choose the identical
    subset and therefore predict identically."""
    x, y = mask_rows()
    dropped = ColumnSubsetEstimator(_lstsq_path(), drop=["ret_lag_0", "ret_lag_1"])
    dropped.fit(x, y, feature_names=MASK_COLUMNS)
    kept = ColumnSubsetEstimator(_lstsq_path(), keep=["vol_5m", "symbol_code"])
    kept.fit(x, y, feature_names=MASK_COLUMNS)

    assert dropped._indices == kept._indices == [2, 3]
    assert np.allclose(dropped.predict(x), kept.predict(x))


def test_column_subset_survivor_order_is_the_declared_candidate_order():
    """``keep`` names its columns out of order; the survivors must keep
    their ORIGINAL position in ``feature_names``, never the keep list's
    order — a mask reorders nothing, it only removes."""
    model = ColumnSubsetEstimator(_lstsq_path(), keep=["symbol_code", "vol_5m"])
    x, y = mask_rows()
    model.fit(x, y, feature_names=MASK_COLUMNS)
    assert [MASK_COLUMNS[i] for i in model._indices] == ["vol_5m", "symbol_code"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"drop": ["vol_5m"], "keep": ["vol_5m"]},  # both
        {},  # neither
    ],
)
def test_column_subset_refuses_both_or_neither_of_drop_and_keep(kwargs):
    x, y = mask_rows()
    model = ColumnSubsetEstimator(_lstsq_path(), **kwargs)
    with pytest.raises(ValueError, match="exactly one of 'drop' or 'keep'"):
        model.fit(x, y, feature_names=MASK_COLUMNS)


def test_column_subset_refuses_a_drop_name_absent_from_feature_names():
    x, y = mask_rows()
    model = ColumnSubsetEstimator(_lstsq_path(), drop=["not_a_real_column"])
    with pytest.raises(ValueError, match=re.escape("not_a_real_column")):
        model.fit(x, y, feature_names=MASK_COLUMNS)


def test_column_subset_refuses_a_keep_name_absent_from_feature_names():
    x, y = mask_rows()
    model = ColumnSubsetEstimator(_lstsq_path(), keep=["not_a_real_column"])
    with pytest.raises(ValueError, match=re.escape("not_a_real_column")):
        model.fit(x, y, feature_names=MASK_COLUMNS)


@pytest.mark.parametrize("knob", ["drop", "keep"])
def test_column_subset_refuses_a_duplicate_name_in_drop_or_keep(knob):
    """A repeated column name used to be silently absorbed by a bare
    ``set()``. ``_feature_list_problems`` already refuses duplicates for
    ``features`` elsewhere in this file — reused here rather than a
    second copy of the same rule (CLAUDE.md's duplication doctrine)."""
    x, y = mask_rows()
    model = ColumnSubsetEstimator(
        _lstsq_path(), **{knob: ["vol_5m", "vol_5m", "symbol_code"]}
    )
    with pytest.raises(ValueError, match="distinct"):
        model.fit(x, y, feature_names=MASK_COLUMNS)


def test_column_subset_refuses_a_mask_that_leaves_zero_surviving_columns():
    x, y = mask_rows()
    model = ColumnSubsetEstimator(_lstsq_path(), drop=list(MASK_COLUMNS))
    with pytest.raises(ValueError, match="zero surviving columns"):
        model.fit(x, y, feature_names=MASK_COLUMNS)


def test_column_subset_refuses_a_declared_mask_with_no_feature_names():
    x, y = mask_rows()
    model = ColumnSubsetEstimator(_lstsq_path(), drop=["vol_5m"])
    with pytest.raises(ValueError, match="feature_names=None"):
        model.fit(x, y, feature_names=None)


def test_column_subset_categorical_feature_lands_on_its_new_index():
    """``symbol_code`` sits at index 3; dropping the two lag columns
    shifts every survivor down by two, so the wrapper must declare it
    categorical at its NEW index (1), not its original one (3) — the
    ADR's central correctness requirement."""
    x, y = mask_rows()
    model = ColumnSubsetEstimator(_lstsq_path(), drop=["ret_lag_0", "ret_lag_1"])
    model.fit(x, y, feature_names=MASK_COLUMNS, categorical_feature=[3])

    assert model._indices == [2, 3]
    assert model._model.seen_categorical_feature == [1]
    assert model._model.seen_feature_names == ["vol_5m", "symbol_code"]


def test_column_subset_a_categorical_index_the_mask_removed_is_simply_dropped():
    """A categorical index the mask itself removes names no surviving
    column — nothing is left to declare categorical, so it is dropped
    from the forwarded list rather than mis-forwarded at some index."""
    x, y = mask_rows()
    model = ColumnSubsetEstimator(_lstsq_path(), keep=["vol_5m"])
    model.fit(x, y, feature_names=MASK_COLUMNS, categorical_feature=[3])
    assert model._model.seen_categorical_feature == []


def test_column_subset_never_forwards_a_kwarg_the_inner_fit_does_not_accept():
    """Inspected, never assumed: an estimator whose ``fit`` takes neither
    optional kwarg must still fit cleanly — nothing is forced on it."""
    x, y = mask_rows()
    model = ColumnSubsetEstimator(
        _no_kwargs_path(), drop=["ret_lag_0", "ret_lag_1"]
    )
    model.fit(x, y, feature_names=MASK_COLUMNS, categorical_feature=[3])
    assert model._model.n_features_seen == 2


def test_column_subset_predict_projects_with_the_fitted_indices():
    """Mutating a column the mask DROPPED must not move the prediction —
    ``predict`` reads the fitted subset alone, never the full row."""
    x, y = mask_rows()
    model = ColumnSubsetEstimator(_lstsq_path(), keep=["vol_5m", "symbol_code"])
    model.fit(x, y, feature_names=MASK_COLUMNS)
    mutated = x.copy()
    mutated[:, 0] = 999.0
    mutated[:, 1] = -999.0
    assert np.allclose(model.predict(x), model.predict(mutated))


def test_column_subset_masked_fit_matches_an_equivalent_hand_sliced_fit():
    """The real proof the projection is correct: fitting through the
    mask must be numerically IDENTICAL to hand-slicing the same two
    columns and fitting the SAME estimator directly on them, including
    the categorical index each spells in its own coordinates."""
    x, y = mask_rows()
    masked = ColumnSubsetEstimator(_lstsq_path(), drop=["ret_lag_0", "ret_lag_1"])
    masked.fit(x, y, feature_names=MASK_COLUMNS, categorical_feature=[3])

    hand = _RecordingLstsq()
    hand.fit(
        x[:, [2, 3]], y,
        feature_names=["vol_5m", "symbol_code"], categorical_feature=[1],
    )

    probe, _ = mask_rows(n=7, seed=99)
    assert np.allclose(masked.predict(probe), hand.predict(probe[:, [2, 3]]))


def test_column_subset_wraps_a_real_sklearn_estimator(tmp_path):
    """Integration half, gated: a REAL sklearn regressor behind the same
    dotted-path doorway :class:`SklearnFit` uses, masked and compared
    against hand-slicing it directly."""
    pytest.importorskip("sklearn")
    from sklearn.linear_model import Ridge

    x, y = mask_rows()
    masked = ColumnSubsetEstimator(
        "sklearn.linear_model.Ridge", drop=["ret_lag_0", "ret_lag_1"], alpha=1e-6,
    )
    masked.fit(x, y, feature_names=MASK_COLUMNS)
    hand = Ridge(alpha=1e-6).fit(x[:, [2, 3]], y)
    assert np.allclose(masked.predict(x), hand.predict(x[:, [2, 3]]))


def test_column_subset_categorical_feature_reaches_real_lightgbm():
    """Integration half, gated: real LightGBM's ``fit`` spells the
    categorical kwarg ``categorical_feature`` (matching this wrapper) but
    the surviving-names kwarg ``feature_name`` SINGULAR (NOT
    ``feature_names`` — LightGBM takes no ``**kwargs`` either, so the
    plural is silently never forwarded), so the mask must still fit,
    predict, AND actually name its columns to the booster.

    A shape check alone cannot prove that: a forwarding bug that
    silently drops the names still fits and predicts fine (LightGBM
    falls back to generic ``Column_0``/``Column_1`` names), so the real
    proof reads the surviving names back out of the fitted booster
    itself, exactly as the library records them.
    """
    pytest.importorskip("lightgbm")
    x, y = mask_rows(n=200)
    masked = ColumnSubsetEstimator(
        "lightgbm.LGBMRegressor",
        drop=["ret_lag_0", "ret_lag_1"],
        n_estimators=5,
        verbosity=-1,
    )
    masked.fit(x, y, feature_names=MASK_COLUMNS, categorical_feature=[3])
    prediction = masked.predict(x)
    assert prediction.shape == (x.shape[0],)
    # THE proof (ADR-0108/fix 2): the booster's own record of what it was
    # told, not merely that fit/predict ran — ``feature_name`` singular is
    # the spelling real LightGBM accepts, and a booster that never got it
    # falls back to ``Column_0``/``Column_1`` while still fitting cleanly.
    feature_infos = masked._model.booster_.dump_model()["feature_infos"]
    assert set(feature_infos) == {"vol_5m", "symbol_code"}
    assert not any(name.startswith("Column_") for name in feature_infos)


# ---------------------------------------------------------------------------
# ColumnSubsetEstimator: routed import/construction refusals (fix 3) and the
# predict-time shape guard (fix 5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "estimator,needle",
    [
        ("no_such_library_zzz.Model", "cannot import"),
        ("sklearn.linear_model.NoSuchEstimator", "no attribute"),
    ],
)
def test_column_subset_refuses_an_unimportable_estimator_by_name(estimator, needle):
    """Fix 3: ``fit`` now routes through this file's OWN
    ``_import_object``/``_import_estimator`` — the same helpers
    ``SklearnFit.run_train`` uses — instead of a raw ``importlib``/
    ``getattr`` pair, so a bad module or a bad attribute refuses as a
    clean, by-name ``ValueError`` (naming the offending path) instead of
    a raw ``ModuleNotFoundError``/``AttributeError`` escaping unwrapped."""
    pytest.importorskip("sklearn")
    x, y = mask_rows()
    model = ColumnSubsetEstimator(estimator, drop=["ret_lag_0"])
    with pytest.raises(ValueError, match=needle) as excinfo:
        model.fit(x, y, feature_names=MASK_COLUMNS)
    assert estimator in str(excinfo.value)


def test_column_subset_refuses_a_typoed_estimator_param_by_name():
    """Fix 3: ``fit`` constructs the inner estimator through
    ``_construct`` (the same helper ``SklearnFit``/``SklearnSelect``
    use), so a typo'd constructor kwarg refuses as a clean ``ValueError``
    naming BOTH the estimator path and the offending block, instead of a
    raw ``TypeError`` escaping unwrapped."""
    pytest.importorskip("sklearn")
    x, y = mask_rows()
    model = ColumnSubsetEstimator(
        "sklearn.linear_model.Ridge", drop=["ret_lag_0"], alphaa=1.0,
    )
    with pytest.raises(ValueError, match="estimator_params") as excinfo:
        model.fit(x, y, feature_names=MASK_COLUMNS)
    assert "sklearn.linear_model.Ridge" in str(excinfo.value)


def test_column_subset_predict_refuses_a_matrix_with_a_different_column_count():
    """Fix 5: ``predict`` stores the column count ``fit`` saw and refuses
    a mismatch, naming both numbers, rather than silently answering on a
    reshaped matrix. Width alone cannot catch every corruption — a
    same-count matrix with its columns shuffled passes this check, which
    is why the docstring says so rather than promising more."""
    x, y = mask_rows()
    model = ColumnSubsetEstimator(_lstsq_path(), keep=["vol_5m", "symbol_code"])
    model.fit(x, y, feature_names=MASK_COLUMNS)
    narrower = x[:, :-1]  # one column short of the 4 `fit` saw
    with pytest.raises(ValueError, match=re.escape("has 3 column(s), fit saw 4")):
        model.predict(narrower)


# ---------------------------------------------------------------------------
# The multi-head bundle artifact (ADR-0114 Phase 2)
# ---------------------------------------------------------------------------

BUNDLE_HEADS = ("h01", "h02")
BUNDLE_FEATURE_ORDER = ["x0", "x1"]
BUNDLE_PREDICT_FIXTURE = [[0.5, 0.5], [1.5, 1.0], [-0.5, 2.0]]


def _bundle_heads():
    """Two tiny, distinguishable Ridge fits — real sklearn, synthetic data."""
    pytest.importorskip("sklearn")
    from sklearn.linear_model import Ridge

    x = np.array([[0.0, 1.0], [1.0, 0.0], [2.0, 2.0], [3.0, 1.0]])
    y1 = np.array([1.0, 2.0, 3.0, 4.0])
    y2 = np.array([2.0, 1.0, 5.0, 3.0])
    return {
        "h01": Ridge(alpha=1e-6).fit(x, y1),
        "h02": Ridge(alpha=1e-6).fit(x, y2),
    }


def _bundle_kwargs(estimators, *, heads=BUNDLE_HEADS):
    return dict(
        heads=heads,
        estimators=estimators,
        head_params={
            name: {"estimator": RIDGE, "estimator_params": {"alpha": 1e-6}}
            for name in heads
        },
        feature_order=list(BUNDLE_FEATURE_ORDER),
        surviving_features={name: list(BUNDLE_FEATURE_ORDER) for name in heads},
        categorical_encoding={},
        training_identities={
            name: {"cut_ms": 123, "seed": 0, "n_rows": 4} for name in heads
        },
        predict_fixture=[list(row) for row in BUNDLE_PREDICT_FIXTURE],
    )


def _bundle_digest(joblib_path, manifest):
    """The DOCUMENTED bundle hash material, recomputed independently of
    the pack (mirrors ``_combined_digest`` above, generalized S2-A): sha256
    over the joblib bytes, a NUL separator, and the canonical JSON of every
    manifest field except ``sha256`` and ``library_versions``."""
    import hashlib as _hashlib

    material = {
        k: v for k, v in manifest.items()
        if k not in ("sha256", "library_versions")
    }
    digest = _hashlib.sha256()
    digest.update(pathlib.Path(joblib_path).read_bytes())
    digest.update(b"\0")
    digest.update(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return digest.hexdigest()


def _rewrite_manifest(path, edit):
    manifest_path = path + ".json"
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest.update(edit)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    return manifest


def test_bundle_write_and_load_round_trips_with_one_joblib_and_one_manifest(tmp_path):
    from dskit.pipeline.libs.sklearn import EstimatorBundle, load_bundle, write_bundle

    estimators = _bundle_heads()
    path = str(tmp_path / "bundle.joblib")
    manifest = write_bundle(path, **_bundle_kwargs(estimators))

    assert os.path.isfile(path)
    assert os.path.isfile(path + ".json")
    assert manifest["format"] == "sklearn-bundle-joblib-v1"
    assert manifest["heads"] == list(BUNDLE_HEADS)
    assert manifest["sha256"] == _bundle_digest(path, manifest)

    bundle = load_bundle(path)
    assert isinstance(bundle, EstimatorBundle)
    assert set(bundle.estimators) == set(BUNDLE_HEADS)
    assert bundle.manifest["sha256"] == manifest["sha256"]
    fixture = np.array(BUNDLE_PREDICT_FIXTURE, dtype=float)
    for name in BUNDLE_HEADS:
        np.testing.assert_allclose(
            bundle.predict(name, fixture), estimators[name].predict(fixture)
        )


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda k, e: {n: v for n, v in e.items() if n != "h02"}, "missing"),
        (lambda k, e: {**e, "h03": e["h01"]}, "undeclared"),
    ],
)
def test_bundle_write_refuses_when_estimators_mismatch_declared_heads(
    tmp_path, mutate, needle
):
    from dskit.pipeline.libs.sklearn import write_bundle

    estimators = _bundle_heads()
    kwargs = _bundle_kwargs(estimators)
    kwargs["estimators"] = mutate(kwargs["heads"], estimators)
    with pytest.raises(ValueError, match=needle):
        write_bundle(str(tmp_path / "bundle.joblib"), **kwargs)
    assert list(tmp_path.iterdir()) == []


def test_bundle_write_refuses_duplicate_declared_head_names(tmp_path):
    from dskit.pipeline.libs.sklearn import write_bundle

    estimators = _bundle_heads()
    kwargs = _bundle_kwargs(estimators)
    kwargs["heads"] = ("h01", "h01")
    with pytest.raises(ValueError, match="distinct"):
        write_bundle(str(tmp_path / "bundle.joblib"), **kwargs)
    assert list(tmp_path.iterdir()) == []


def test_bundle_write_refuses_a_head_name_that_could_escape_the_directory(tmp_path):
    from dskit.pipeline.libs.sklearn import write_bundle

    estimators = _bundle_heads()
    kwargs = _bundle_kwargs(estimators)
    escaped = "../../escape"
    kwargs["heads"] = ("h01", escaped)
    kwargs["estimators"] = {"h01": estimators["h01"], escaped: estimators["h02"]}
    kwargs["head_params"][escaped] = kwargs["head_params"].pop("h02")
    kwargs["surviving_features"][escaped] = kwargs["surviving_features"].pop("h02")
    kwargs["training_identities"][escaped] = kwargs["training_identities"].pop("h02")
    with pytest.raises(ValueError, match="letters, digits"):
        write_bundle(str(tmp_path / "bundle.joblib"), **kwargs)
    assert list(tmp_path.iterdir()) == []


def test_bundle_write_refuses_to_clobber_without_overwrite(tmp_path):
    from dskit.pipeline.libs.sklearn import write_bundle

    path = str(tmp_path / "bundle.joblib")
    write_bundle(path, **_bundle_kwargs(_bundle_heads()))
    with pytest.raises(ValueError, match="overwrite"):
        write_bundle(path, **_bundle_kwargs(_bundle_heads()))
    # overwrite=True replaces it deliberately.
    replaced = write_bundle(
        path, overwrite=True, **_bundle_kwargs(_bundle_heads())
    )
    assert replaced["sha256"]


def test_bundle_write_interrupted_mid_write_leaves_no_partial_bundle(
    tmp_path, monkeypatch
):
    from dskit.pipeline.libs.sklearn import write_bundle

    path = str(tmp_path / "bundle.joblib")
    calls = {"n": 0}
    real_replace = os.replace

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:  # let the joblib file land; fail the manifest
            raise OSError("disk full (simulated)")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    with pytest.raises(OSError, match="disk full"):
        write_bundle(path, **_bundle_kwargs(_bundle_heads()))

    assert os.path.isfile(path)  # the joblib landed, atomically
    assert not os.path.isfile(path + ".json")  # the manifest never did
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp-" in p.name]
    assert leftovers == []

    monkeypatch.setattr(os, "replace", real_replace)
    manifest = write_bundle(
        path, overwrite=True, **_bundle_kwargs(_bundle_heads())
    )
    assert os.path.isfile(path + ".json")
    assert manifest["sha256"]


def test_bundle_load_refuses_missing_bundle_and_missing_manifest(tmp_path):
    from dskit.pipeline.libs.sklearn import load_bundle, write_bundle

    with pytest.raises(ValueError, match="does not exist"):
        load_bundle(str(tmp_path / "nope.joblib"))

    path = str(tmp_path / "bundle.joblib")
    write_bundle(path, **_bundle_kwargs(_bundle_heads()))
    os.remove(path + ".json")
    with pytest.raises(ValueError, match="manifest .* is missing"):
        load_bundle(path)


def test_bundle_load_refuses_a_foreign_class_relabelled_as_declared(tmp_path):
    from sklearn.linear_model import Lasso

    from dskit.pipeline.libs.sklearn import load_bundle, write_bundle

    estimators = _bundle_heads()
    path = str(tmp_path / "bundle.joblib")
    write_bundle(path, **_bundle_kwargs(estimators))

    # Relabel h01's payload as a foreign class, then RECOMPUTE the digest
    # over the new bytes — an adversary who controls both files still
    # cannot pass a Lasso off as the manifest's declared Ridge.
    import joblib

    tampered = dict(joblib.load(path))
    tampered["h01"] = Lasso(alpha=1e-6).fit([[0.0, 1.0], [1.0, 2.0]], [1.0, 2.0])
    joblib.dump(tampered, path)
    with open(path + ".json", encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest["sha256"] = _bundle_digest(path, manifest)
    with open(path + ".json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)

    with pytest.raises(ValueError, match="relabelled"):
        load_bundle(path)


@pytest.mark.parametrize(
    "edit",
    [
        {"heads": ["h02", "h01"]},
        {"feature_order": ["x1", "x0"]},
        {"predict_checksum": "0" * 64},
        {"categorical_encoding": {"x0": {"kind": "native"}}},
        {
            "training_identities": {
                "h01": {"cut_ms": 999, "seed": 0, "n_rows": 4},
                "h02": {"cut_ms": 123, "seed": 0, "n_rows": 4},
            }
        },
        {
            "surviving_features": {
                "h01": ["x0"],
                "h02": list(BUNDLE_FEATURE_ORDER),
            }
        },
        {
            "head_params": {
                "h01": {"estimator": RIDGE, "estimator_params": {"alpha": 9.0}},
                "h02": {"estimator": RIDGE, "estimator_params": {"alpha": 1e-6}},
            }
        },
    ],
)
def test_every_bundle_manifest_field_is_hash_material(tmp_path, edit):
    """The digest covers ordered head names, per-head parameters, feature
    order, category map, cuts, and the checksum — not the model bytes
    alone. An edit to ANY of these refuses at load (ADR-0114 Phase 2)."""
    from dskit.pipeline.libs.sklearn import load_bundle, write_bundle

    path = str(tmp_path / "bundle.joblib")
    write_bundle(path, **_bundle_kwargs(_bundle_heads()))
    _rewrite_manifest(path, edit)
    with pytest.raises(ValueError, match="content hash"):
        load_bundle(path)


def test_bundle_predict_checksum_is_independently_reproducible(tmp_path):
    from dskit.pipeline.libs.sklearn import write_bundle

    estimators = _bundle_heads()
    manifest = write_bundle(
        str(tmp_path / "bundle.joblib"), **_bundle_kwargs(estimators)
    )
    fixture = np.array(BUNDLE_PREDICT_FIXTURE, dtype=float)
    predictions = {
        name: [float(v) for v in np.ravel(estimators[name].predict(fixture))]
        for name in BUNDLE_HEADS
    }
    independent = hashlib.sha256(
        json.dumps(predictions, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert manifest["predict_checksum"] == independent


def test_bundle_load_replay_catches_an_in_memory_head_swap(tmp_path, monkeypatch):
    """A head swap that happens AFTER the on-disk digest already verified
    (a hostile or corrupted deserialize, not a file edit) is still caught:
    the loader replays the stored fixture through the RESTORED objects and
    refuses when the replayed beliefs disagree with the manifest's pinned
    checksum, independent of the sha256 check above."""
    import joblib

    from dskit.pipeline.libs.sklearn import load_bundle, write_bundle

    estimators = _bundle_heads()
    path = str(tmp_path / "bundle.joblib")
    write_bundle(path, **_bundle_kwargs(estimators))

    real_load = joblib.load

    def swapped_load(target, *a, **kw):
        payload = real_load(target, *a, **kw)
        if target == path:
            payload = {"h01": payload["h02"], "h02": payload["h01"]}
        return payload

    monkeypatch.setattr(joblib, "load", swapped_load)
    with pytest.raises(ValueError, match="predict checksum"):
        load_bundle(path)


def test_bundle_load_refuses_a_bundle_that_declares_the_wrong_format(tmp_path):
    from dskit.pipeline.libs.sklearn import load_bundle, write_bundle

    path = str(tmp_path / "bundle.joblib")
    write_bundle(path, **_bundle_kwargs(_bundle_heads()))
    _rewrite_manifest(path, {"format": "sklearn-joblib-v1"})
    with pytest.raises(ValueError, match="format"):
        load_bundle(path)
