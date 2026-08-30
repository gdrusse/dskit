"""The sklearn library pack: unit + real-library integration + conformance.

This module IMPORTS without scikit-learn installed — every test that
needs the real library (and the conformance probe factory) calls
``pytest.importorskip("sklearn")`` at its top, so the unit half of the
file keeps running on a dependency-less machine, exactly like the pack
itself keeps planning there.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from types import SimpleNamespace

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.driver import run_document
from dskit.pipeline.fitted import SIDECAR_NAME, FeatureSelector
from dskit.pipeline.libs.sklearn import (
    NODE_KINDS,
    SklearnFit,
    SklearnPredict,
    SklearnSelect,
    SklearnSignal,
    register,
)
from dskit.pipeline.node import NodeContext, NodeKindRegistry
from dskit.pipeline.planner import plan

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
# The conformance hookup (docs/24 §10 step 8)
# ---------------------------------------------------------------------------

EXPECTED_ROLES = {
    "sklearn-fit": "train",
    "sklearn-predict": "signal",
    "sklearn-select": "fitted_transform",
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


def probes(tmp_path):
    """One populated probe per kind. The fixture artifact is a REAL fit
    (``y = x``); the probes' wired rows carry the OPPOSITE relationship
    (``y = 1 - x``), so ``verify_loaded`` discriminates a restore from a
    silent refit on the prediction itself, not just on paperwork."""
    pytest.importorskip("sklearn")
    select_ctx, carrier, select_sidecar = _selected(tmp_path)
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
    }


TestSklearnConformance = conformance_suite(
    registry=NODE_KINDS,
    module="dskit.pipeline.libs.sklearn",
    probes=probes,
    expected_roles=EXPECTED_ROLES,
    name="TestSklearnConformance",
)


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
