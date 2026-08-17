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

import pytest

from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.libs.sklearn import (
    NODE_KINDS,
    SklearnFit,
    SklearnPredict,
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
    assert table == {"sklearn-fit": SklearnFit, "sklearn-predict": SklearnPredict}
    assert SklearnFit.role == "train"
    assert SklearnFit.outputs == ("signal", "artifact_path", "metrics")
    assert SklearnPredict.role == "signal"
    assert SklearnPredict.outputs == ("signal",)


def test_register_is_explicit_and_idempotent():
    registry = NodeKindRegistry()
    register(registry)
    register(registry)  # second call skips, never raises or shadows
    assert "sklearn-fit" in registry and "sklearn-predict" in registry
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


def test_predict_node_refuses_a_missing_artifact_by_name(tmp_path):
    node = SklearnPredict("serve", {"artifact": str(tmp_path / "gone.joblib")})
    with pytest.raises(ValueError, match="does not exist"):
        node.run(_ctx(tmp_path, "serverun"), {})


# ---------------------------------------------------------------------------
# The conformance hookup (docs/24 §10 step 8)
# ---------------------------------------------------------------------------

EXPECTED_ROLES = {"sklearn-fit": "train", "sklearn-predict": "signal"}


def probes(tmp_path):
    """One populated probe per kind. The fixture artifact is a REAL fit
    (``y = x``); the probes' wired rows carry the OPPOSITE relationship
    (``y = 1 - x``), so ``verify_loaded`` discriminates a restore from a
    silent refit on the prediction itself, not just on paperwork."""
    pytest.importorskip("sklearn")
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
