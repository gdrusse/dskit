"""The transformers pack (docs/25 §2 row 4), held to the bar.

Everything runs from literals and ``tmp_path`` — no real data file, no
network, no hub: every model is instantiated fresh from a tiny config
(2 layers, 1 head, hidden = the feature count) and restored only from a
local ``save_pretrained`` directory.

The suite covers the pack's three claims:

* **The artifact IS identity** — same seed + same rows = byte-identical
  checkpoint = equal content digest; a different seed moves it; the
  digest is recorded in the sidecar and exposed in ``outputs.metrics``.
* **``mode="load"`` REALLY loads** — the restored signal answers exactly
  as the original, never refits, and refuses BY NAME when the pin is
  missing, foreign, tampered with, or schema-mismatched.
* **The wrapper contract** — the toolkit conformance suite runs over the
  pack's concrete pairs at the bottom of this file, with populated
  trainable probes and a ``verify_loaded`` that rejects a fresh fit.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil

import pytest

from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import load_document
from dskit.pipeline.libs.transformers import (
    NODE_KINDS,
    SIDECAR_NAME,
    DeclaredTransformerFit,
    PretrainedClassify,
    PretrainedEncode,
    PretrainedForecast,
    TinyTransformerFit,
    TransformerFit,
    TransformerPredict,
    content_digest,
    register,
)
from dskit.pipeline.node import NodeContext, NodeKindRegistry
from dskit.pipeline.planner import plan

pytest.importorskip("torch")
pytest.importorskip("transformers")

EXAMPLE = (
    pathlib.Path(__file__).parents[2]
    / "examples"
    / "pipeline"
    / "transformers-fit.json"
)

#: Deterministic literal rows — two numeric features and a float label.
ROWS = [
    {
        "x1": round(0.05 + 0.1 * i, 3),
        "x2": round(0.9 - 0.07 * i, 3),
        "y": 0.3 + 0.04 * i,
    }
    for i in range(8)
]

FIT_PARAMS = {
    "features": ["x1", "x2"],
    "label": "y",
    "seed": 3,
    "steps": 2,
    "lr": 0.05,
}

#: The OPPOSITE relationship (``y -> 1 - y``): a silent refit on these is
#: numerically far from any restore of the ROWS fit.
INVERTED_ROWS = [{**row, "y": 1.0 - row["y"]} for row in ROWS]


def _expected_digest(checkpoint):
    """The DOCUMENTED digest material, recomputed independently of the
    pack: sha256 over the checkpoint's tree digest, a NUL separator, and
    the canonical JSON (sorted keys, compact separators) of every sidecar
    field except ``content_digest`` itself."""
    import hashlib

    with open(os.path.join(checkpoint, SIDECAR_NAME), encoding="utf-8") as fh:
        sidecar = json.load(fh)
    material = {k: v for k, v in sidecar.items() if k != "content_digest"}
    digest = hashlib.sha256()
    digest.update(content_digest(checkpoint).encode("ascii"))
    digest.update(b"\0")
    digest.update(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return digest.hexdigest()


def _rehash(checkpoint):
    """Re-record the digest after a sidecar edit — the adversary who knows
    the scheme; what the digest CANNOT catch and the deeper checks must."""
    path = os.path.join(checkpoint, SIDECAR_NAME)
    with open(path, encoding="utf-8") as fh:
        sidecar = json.load(fh)
    sidecar["content_digest"] = _expected_digest(checkpoint)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh)


EXPECTED_ROLES = {
    "transformers-fit": "train",
    "transformers-tiny-fit": "train",
    "transformers-predict": "signal",
}

#: The DECLARED family's knobs: the same tiny BERT ``TinyTransformerFit``
#: hardcodes, but named by CONFIG instead of by a Python subclass.
DECLARED_FIT_PARAMS = {
    **FIT_PARAMS,
    "config_class": "transformers.BertConfig",
    "model_class": "transformers.BertForSequenceClassification",
    "config_params": {
        "vocab_size": 8,
        "hidden_size": 2,
        "num_hidden_layers": 2,
        "num_attention_heads": 1,
        "intermediate_size": 4,
        "max_position_embeddings": 4,
        "num_labels": 1,
    },
}


class OtherTinyFit(TinyTransformerFit):
    """A second concrete subclass — its checkpoints are NOT this one's."""


def _ctx(tmp_path, sub="run"):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / sub))


def _fit(tmp_path, sub="run", params=FIT_PARAMS, rows=ROWS, cls=TinyTransformerFit):
    return cls("fit", dict(params)).run(_ctx(tmp_path, sub), {"rows": list(rows)})


@pytest.fixture(scope="module")
def fitted(tmp_path_factory):
    """One fit, shared read-only — tamper tests copy it first."""
    tmp = tmp_path_factory.mktemp("transformers-fixture")
    return _fit(tmp)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_node_kinds_carries_every_concrete_kind(self):
        assert dict(NODE_KINDS) == {
            "transformers-fit": DeclaredTransformerFit,
            "transformers-tiny-fit": TinyTransformerFit,
            "transformers-predict": TransformerPredict,
            "transformers-encode": PretrainedEncode,
            "transformers-classify": PretrainedClassify,
            "transformers-forecast": PretrainedForecast,
        }

    def test_register_is_explicit_and_idempotent(self):
        registry = NodeKindRegistry()
        register(registry)
        register(registry)  # a second call skips, never shadows
        assert registry.kinds() == (
            "transformers-classify",
            "transformers-encode",
            "transformers-fit",
            "transformers-forecast",
            "transformers-predict",
            "transformers-tiny-fit",
        )

    def test_the_declared_family_validates_clean(self):
        # Pure param validation — no library import, so this runs even where
        # transformers is not installed.
        assert DeclaredTransformerFit.validate_params(dict(DECLARED_FIT_PARAMS)) == []

    def test_the_declared_family_requires_both_class_paths(self):
        params = {k: v for k, v in DECLARED_FIT_PARAMS.items() if k != "model_class"}
        problems = DeclaredTransformerFit.validate_params(params)
        assert any("model_class is required" in p for p in problems)

    def test_the_declared_family_refuses_a_malformed_class_path(self):
        problems = DeclaredTransformerFit.validate_params(
            {**DECLARED_FIT_PARAMS, "config_class": "nothubname"}
        )
        assert any("config_class must name a class" in p for p in problems)

    def test_the_declared_family_refuses_non_dict_config_params(self):
        problems = DeclaredTransformerFit.validate_params(
            {**DECLARED_FIT_PARAMS, "config_params": ["hidden_size", 2]}
        )
        assert any("config_params must be a dict" in p for p in problems)

    def test_the_abstract_base_cannot_be_registered(self):
        registry = NodeKindRegistry()
        with pytest.raises(ValueError, match="abstract"):
            registry.register("transformers-fit", TransformerFit)

    def test_the_abstract_base_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            TransformerFit("fit", dict(FIT_PARAMS))


# ---------------------------------------------------------------------------
# The fit: checkpoint, sidecar, identity
# ---------------------------------------------------------------------------


class TestFit:
    def test_outputs_match_the_declared_contract(self, fitted):
        assert sorted(fitted) == ["artifact_path", "metrics", "signal"]

    def test_the_checkpoint_carries_model_files_and_the_sidecar(self, fitted):
        names = sorted(os.listdir(fitted["artifact_path"]))
        assert SIDECAR_NAME in names
        assert "config.json" in names
        assert any(n.endswith(".safetensors") for n in names)

    def test_the_sidecar_records_the_full_identity(self, fitted):
        with open(
            os.path.join(fitted["artifact_path"], SIDECAR_NAME), encoding="utf-8"
        ) as fh:
            sidecar = json.load(fh)
        assert sidecar["family"] == "transformers"
        assert sidecar["seed"] == FIT_PARAMS["seed"]
        assert sidecar["params"] == FIT_PARAMS
        assert sidecar["node_class"].endswith(":TinyTransformerFit")
        assert sidecar["model_class"].endswith(":BertForSequenceClassification")
        assert len(sidecar["config_hash"]) == 64
        # RE-PINNED (S2-A): the digest folds the sidecar's fields in on top
        # of the file-tree digest. Hand-verified — _expected_digest derives
        # it from the DOCUMENTED material (module docstring), independently
        # of the pack's own identity_digest.
        assert sidecar["content_digest"] == _expected_digest(fitted["artifact_path"])
        # The file-tree digest is still the OTHER half of the material (a
        # weight-file edit refuses: test_a_tampered_checkpoint_refuses_on_digest).
        assert len(content_digest(fitted["artifact_path"])) == 64

    def test_metrics_expose_the_digest_and_the_loss(self, fitted):
        metrics = fitted["metrics"]
        assert metrics["artifact_digest"] == _expected_digest(fitted["artifact_path"])
        assert metrics["restored"] == 0.0
        assert metrics["n_rows"] == len(ROWS)
        assert metrics["final_loss"] == metrics["final_loss"]  # not NaN

    def test_same_seed_same_rows_is_the_same_checkpoint(self, fitted, tmp_path):
        again = _fit(tmp_path)
        assert (
            again["metrics"]["artifact_digest"] == fitted["metrics"]["artifact_digest"]
        )

    def test_a_different_seed_moves_the_digest(self, fitted, tmp_path):
        moved = _fit(tmp_path, params={**FIT_PARAMS, "seed": 4})
        assert (
            moved["metrics"]["artifact_digest"] != fitted["metrics"]["artifact_digest"]
        )

    def test_the_signal_predicts_a_float(self, fitted):
        value = fitted["signal"].predict(ROWS[0])
        assert isinstance(value, float)
        assert not fitted["signal"].restored

    def test_the_signal_declines_uncovered_rows(self, fitted):
        assert fitted["signal"].predict({"x1": 0.5}) is None  # x2 missing
        assert fitted["signal"].predict({"x1": 0.5, "x2": "wide"}) is None
        assert fitted["signal"].predict({"x1": 0.5, "x2": float("nan")}) is None

    def test_the_signal_serves_attribute_rows_too(self, fitted):
        # The owned `validate` kind hands predict() record OBJECTS as well
        # as dicts — attr-or-key access is one code path.
        class Row:
            x1, x2 = 0.15, 0.62

        assert fitted["signal"].predict(Row()) == pytest.approx(
            fitted["signal"].predict({"x1": 0.15, "x2": 0.62}), abs=1e-9
        )

    def test_a_single_feature_column_works(self, tmp_path):
        # The shipped example fits on one column — hidden_size 1 must build.
        params = {"features": ["x1"], "label": "y", "seed": 1, "steps": 1, "lr": 0.05}
        out = _fit(tmp_path, params=params)
        assert isinstance(out["signal"].predict(ROWS[0]), float)

    def test_empty_rows_refuse(self, tmp_path):
        with pytest.raises(ValueError, match="rows is empty"):
            _fit(tmp_path, rows=[])

    def test_a_missing_label_refuses_by_name(self, tmp_path):
        bad = [dict(row) for row in ROWS]
        del bad[3]["y"]
        with pytest.raises(ValueError, match="label column 'y'.*row 3"):
            _fit(tmp_path, rows=bad)

    def test_bool_labels_are_legal_binary_outcomes(self, tmp_path):
        rows = [{**row, "y": row["x1"] > 0.4} for row in ROWS]
        out = _fit(tmp_path, rows=rows)
        assert isinstance(out["signal"].predict(rows[0]), float)

    def test_undecodable_fit_rows_refuse_naming_features(self, tmp_path):
        bad = [dict(row) for row in ROWS]
        bad[2]["x2"] = None
        with pytest.raises(ValueError, match="encode\\(\\) declined.*features"):
            _fit(tmp_path, rows=bad)

    def test_a_build_model_that_returns_junk_is_refused(self, tmp_path):
        class BadModelFit(TransformerFit):
            def build_model(self, params):
                return object()

        with pytest.raises(ValueError, match="build_model.*PreTrainedModel"):
            _fit(tmp_path, cls=BadModelFit)


# ---------------------------------------------------------------------------
# mode="load" on the fit: restore or refuse, never refit
# ---------------------------------------------------------------------------


def _load_fit(
    tmp_path, artifact, *, cls=TinyTransformerFit, params=FIT_PARAMS, rows=ROWS
):
    node = cls("fit", dict(params), mode="load", artifact=artifact)
    return node.run(_ctx(tmp_path, "load"), {"rows": list(rows)})


def _tampered_copy(fitted, tmp_path, mutate):
    copy = str(tmp_path / "copy")
    shutil.copytree(fitted["artifact_path"], copy)
    mutate(copy)
    return copy


class TestFitLoad:
    def test_load_really_restores(self, fitted, tmp_path):
        out = _load_fit(tmp_path, fitted["artifact_path"])
        assert out["signal"].restored is True
        assert out["artifact_path"] == fitted["artifact_path"]
        assert out["metrics"]["restored"] == 1.0
        assert out["metrics"]["artifact_digest"] == fitted["metrics"]["artifact_digest"]
        for row in ROWS:
            assert out["signal"].predict(row) == pytest.approx(
                fitted["signal"].predict(row), abs=1e-9
            )

    def test_load_never_refits(self, fitted, tmp_path):
        # Different rows wired in; a refit would answer differently, a
        # restore answers from the pinned weights alone.
        other_rows = [{**row, "y": 1.0 - row["y"]} for row in ROWS]
        out = _load_fit(tmp_path, fitted["artifact_path"], rows=other_rows)
        assert out["signal"].predict(ROWS[0]) == pytest.approx(
            fitted["signal"].predict(ROWS[0]), abs=1e-9
        )
        assert "final_loss" not in out["metrics"]  # no training happened

    def test_load_stops_demanding_a_rows_wire_it_never_reads(self, tmp_path):
        """ADR-0038's declared delta: validate_inputs dispatches BY mode
        now, so a load — which consumes neither port — stops demanding the
        wire a fit needs."""
        loader = TinyTransformerFit(
            "fit", dict(FIT_PARAMS), mode="load", artifact="ck"
        )
        assert loader.validate_inputs({}) == []
        assert TinyTransformerFit("fit", dict(FIT_PARAMS)).validate_inputs({})

    def test_an_empty_artifact_refuses(self, tmp_path):
        with pytest.raises(ValueError, match="mode='load'.*artifact"):
            _load_fit(tmp_path, "")

    def test_a_missing_directory_refuses(self, tmp_path):
        with pytest.raises(ValueError, match="not a loadable checkpoint"):
            _load_fit(tmp_path, str(tmp_path / "nowhere"))

    def test_an_unreadable_sidecar_refuses(self, fitted, tmp_path):
        def corrupt(copy):
            with open(os.path.join(copy, SIDECAR_NAME), "w", encoding="utf-8") as fh:
                fh.write("not json")

        copy = _tampered_copy(fitted, tmp_path, corrupt)
        with pytest.raises(ValueError, match="sidecar.*unreadable"):
            _load_fit(tmp_path, copy)

    def test_a_foreign_family_refuses(self, fitted, tmp_path):
        def foreign(copy):
            with open(os.path.join(copy, SIDECAR_NAME), "w", encoding="utf-8") as fh:
                json.dump({"family": "pickle"}, fh)

        copy = _tampered_copy(fitted, tmp_path, foreign)
        with pytest.raises(ValueError, match="family.*refusing to load"):
            _load_fit(tmp_path, copy)

    def test_a_tampered_checkpoint_refuses_on_digest(self, fitted, tmp_path):
        def tamper(copy):
            with open(os.path.join(copy, "config.json"), "a", encoding="utf-8") as fh:
                fh.write(" ")

        copy = _tampered_copy(fitted, tmp_path, tamper)
        with pytest.raises(ValueError, match="digest.*does not match"):
            _load_fit(tmp_path, copy)

    def test_another_subclasses_checkpoint_refuses(self, fitted, tmp_path):
        with pytest.raises(ValueError, match="fitted by.*TinyTransformerFit"):
            _load_fit(tmp_path, fitted["artifact_path"], cls=OtherTinyFit)

    def test_a_features_mismatch_refuses(self, fitted, tmp_path):
        params = {**FIT_PARAMS, "features": ["x2", "x1"]}
        with pytest.raises(ValueError, match="features=.*schema"):
            _load_fit(tmp_path, fitted["artifact_path"], params=params)

    def test_a_label_mismatch_refuses(self, fitted, tmp_path):
        params = {**FIT_PARAMS, "label": "z"}
        with pytest.raises(ValueError, match="label=.*schema"):
            _load_fit(tmp_path, fitted["artifact_path"], params=params)


# ---------------------------------------------------------------------------
# TransformerPredict: always loads, refuses by name otherwise
# ---------------------------------------------------------------------------


class TestPredictNode:
    def test_loads_from_the_artifact_dir_param(self, fitted, tmp_path):
        node = TransformerPredict("sig", {"artifact_dir": fitted["artifact_path"]})
        out = node.run(_ctx(tmp_path), {})
        assert sorted(out) == ["metrics", "signal"]
        assert out["signal"].restored is True
        assert out["metrics"]["artifact_digest"] == fitted["metrics"]["artifact_digest"]
        assert out["signal"].predict(ROWS[0]) == pytest.approx(
            fitted["signal"].predict(ROWS[0]), abs=1e-9
        )

    def test_loads_from_the_node_level_artifact(self, fitted, tmp_path):
        node = TransformerPredict(
            "sig", {}, mode="load", artifact=fitted["artifact_path"]
        )
        out = node.run(_ctx(tmp_path), {})
        assert out["signal"].artifact_path == fitted["artifact_path"]

    def test_the_signal_reads_the_sidecars_feature_schema(self, fitted, tmp_path):
        # No features param anywhere on the predict node — the checkpoint
        # knows its own columns, and declines rows missing them.
        node = TransformerPredict("sig", {"artifact_dir": fitted["artifact_path"]})
        signal = node.run(_ctx(tmp_path), {})["signal"]
        assert isinstance(signal.predict(ROWS[0]), float)
        assert signal.predict({"x1": 0.2}) is None

    def test_two_disagreeing_pins_refuse(self, fitted, tmp_path):
        node = TransformerPredict(
            "sig",
            {"artifact_dir": str(tmp_path / "elsewhere")},
            mode="load",
            artifact=fitted["artifact_path"],
        )
        with pytest.raises(ValueError, match="disagree"):
            node.run(_ctx(tmp_path), {})

    def test_two_agreeing_pins_load(self, fitted, tmp_path):
        node = TransformerPredict(
            "sig",
            {"artifact_dir": fitted["artifact_path"]},
            mode="load",
            artifact=fitted["artifact_path"],
        )
        assert node.run(_ctx(tmp_path), {})["signal"].restored is True

    def test_no_pin_refuses_by_name(self, tmp_path):
        with pytest.raises(ValueError, match="no artifact pinned"):
            TransformerPredict("sig", {}).run(_ctx(tmp_path), {})

    def test_an_empty_node_level_pin_refuses_rather_than_falling_through(
        self, fitted, tmp_path
    ):
        """ADR-0038's declared delta: an empty node-level pin refuses
        instead of falling through to artifact_dir — torch's and sb3's
        stricter rule, kept as the single one. Document-unreachable."""
        node = TransformerPredict(
            "sig", {"artifact_dir": fitted["artifact_path"]}, mode="load"
        )
        with pytest.raises(ValueError, match="empty artifact reference"):
            node.run(_ctx(tmp_path), {})

    def test_a_node_level_artifact_without_mode_load_is_not_a_pin(
        self, fitted, tmp_path
    ):
        """ADR-0038's IFF rule, and the consequence it carries: a
        node-level ``artifact`` exists ONLY under ``mode='load'``, so
        without the mode this node has NO pin and refuses by name instead
        of loading the stray field.

        The document cannot produce this state — it refuses ``artifact``
        without ``mode='load'`` (``document.py``: "'artifact' without mode
        'load' has no meaning") — so it takes direct construction."""
        node = TransformerPredict("sig", {}, artifact=fitted["artifact_path"])
        assert node.node_level_pin() is None
        with pytest.raises(ValueError, match="no artifact pinned"):
            node.run(_ctx(tmp_path), {})

    def test_mode_train_refuses_by_name(self, tmp_path):
        with pytest.raises(ValueError, match="mode='train'"):
            TransformerPredict("sig", {}, mode="train").run(_ctx(tmp_path), {})

    def test_an_unimportable_model_class_refuses(self, fitted, tmp_path):
        # The sidecar is hash material now (S2-A), so this rewrite RE-RECORDS
        # the digest — an adversary who knows the scheme. The model_class
        # check behind the digest must still refuse, by name.
        def rewrite(copy):
            path = os.path.join(copy, SIDECAR_NAME)
            with open(path, encoding="utf-8") as fh:
                sidecar = json.load(fh)
            sidecar["model_class"] = "no.such.module:Nothing"
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(sidecar, fh)
            _rehash(copy)

        copy = _tampered_copy(fitted, tmp_path, rewrite)
        node = TransformerPredict("sig", {"artifact_dir": copy})
        with pytest.raises(ValueError, match="model_class.*cannot be imported"):
            node.run(_ctx(tmp_path), {})

    def test_a_non_string_model_class_refuses_instead_of_crashing(
        self, fitted, tmp_path
    ):
        # A rehashed sidecar is the only way to reach the restore path with
        # a junk model_class — it must refuse BY NAME, not AttributeError
        # its way out of the importer.
        def rewrite(copy):
            path = os.path.join(copy, SIDECAR_NAME)
            with open(path, encoding="utf-8") as fh:
                sidecar = json.load(fh)
            sidecar["model_class"] = 5
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(sidecar, fh)
            _rehash(copy)

        copy = _tampered_copy(fitted, tmp_path, rewrite)
        node = TransformerPredict("sig", {"artifact_dir": copy})
        with pytest.raises(ValueError, match="model_class 5.*not a 'module"):
            node.run(_ctx(tmp_path), {})

    def test_a_sidecar_without_recorded_features_yields_no_coverage(
        self, fitted, tmp_path
    ):
        # A custom fit subclass may legitimately encode without a features
        # knob, so the predict node still loads — but the DEFAULT encode has
        # no schema to read and declines every row rather than inventing one.
        def strip(copy):
            path = os.path.join(copy, SIDECAR_NAME)
            with open(path, encoding="utf-8") as fh:
                sidecar = json.load(fh)
            sidecar["params"] = {}
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(sidecar, fh)
            _rehash(copy)  # the sidecar is hash material (S2-A)

        copy = _tampered_copy(fitted, tmp_path, strip)
        signal = TransformerPredict("sig", {"artifact_dir": copy}).run(
            _ctx(tmp_path), {}
        )["signal"]
        assert signal.restored is True
        assert signal.predict(ROWS[0]) is None


# ---------------------------------------------------------------------------
# S2-A: the sidecar is hash material
# ---------------------------------------------------------------------------


class TestSidecarIsHashMaterial:
    """Pre-fix the content digest covered the checkpoint FILES only, so any
    sidecar edit — seed, feature schema, class claims — left it intact and
    rode through the predict doorway unchallenged."""

    @pytest.mark.parametrize(
        "edit",
        [
            {"seed": 999},
            {"params": {**FIT_PARAMS, "features": ["x2", "x1"]}},
            {"node_class": "tests.pipeline_libs.test_transformers:OtherTinyFit"},
        ],
    )
    def test_an_unrehashed_sidecar_edit_refuses_on_the_digest(
        self, fitted, tmp_path, edit
    ):
        def rewrite(copy):
            path = os.path.join(copy, SIDECAR_NAME)
            with open(path, encoding="utf-8") as fh:
                sidecar = json.load(fh)
            sidecar.update(edit)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(sidecar, fh)

        copy = _tampered_copy(fitted, tmp_path, rewrite)
        node = TransformerPredict("sig", {"artifact_dir": copy})
        with pytest.raises(ValueError, match="digest.*does not match"):
            node.run(_ctx(tmp_path), {})


# ---------------------------------------------------------------------------
# Validators (the conformance fuzz covers totality; these pin the messages)
# ---------------------------------------------------------------------------


class TestValidators:
    def test_fit_refuses_unknown_knobs_by_name(self):
        problems = TinyTransformerFit.validate_params(
            {**FIT_PARAMS, "warm_start": True}
        )
        assert any("warm_start" in p for p in problems)

    def test_fit_requires_features(self):
        problems = TinyTransformerFit.validate_params(
            {k: v for k, v in FIT_PARAMS.items() if k != "features"}
        )
        assert any("features is required" in p for p in problems)

    @pytest.mark.parametrize(
        "knob, value, needle",
        [
            ("features", [], "non-empty list"),
            ("features", "mid", "non-empty list"),
            ("features", [1], "non-empty strings"),
            ("features", ["a", "a"], "repeats"),
            ("label", "", "label"),
            ("label", 3, "label"),
            ("seed", -1, "seed"),
            ("seed", True, "seed"),
            ("steps", 0, "steps"),
            ("steps", "1,000", "steps"),
            ("lr", 0, "lr"),
            ("lr", float("nan"), "lr"),
            ("lr", True, "lr"),
        ],
    )
    def test_fit_refuses_junk_knobs(self, knob, value, needle):
        problems = TinyTransformerFit.validate_params({**FIT_PARAMS, knob: value})
        assert any(needle in p for p in problems), problems

    def test_fit_accepts_its_own_params(self):
        assert TinyTransformerFit.validate_params(dict(FIT_PARAMS)) == []

    def test_predict_refuses_unknown_knobs_and_junk_paths(self):
        assert any(
            "hub_name" in p
            for p in TransformerPredict.validate_params({"hub_name": "bert-base"})
        )
        assert any(
            "artifact_dir" in p
            for p in TransformerPredict.validate_params({"artifact_dir": 5})
        )
        assert TransformerPredict.validate_params({"artifact_dir": "ck"}) == []

    def test_fit_input_validation_refuses_one_shot_streams_unwalked(self):
        node = TinyTransformerFit("fit", dict(FIT_PARAMS))
        seen = []

        def one_shot():
            for row in ROWS:
                seen.append(row)
                yield row

        problems = node.validate_inputs({"rows": one_shot()})
        assert problems and "one-shot" in problems[0]
        assert seen == []  # refused by shape, consumed nothing
        assert node.validate_inputs({"rows": list(ROWS)}) == []


# ---------------------------------------------------------------------------
# The shipped example: loads, hashes stably, plans
# ---------------------------------------------------------------------------


class TestExampleDocument:
    def test_loads_and_hashes_stably(self):
        doc = load_document(str(EXAMPLE))
        assert doc.name == "transformers-fit"
        assert doc.hash == load_document(str(EXAMPLE)).hash

    def test_plans_so_every_reference_is_real(self):
        resolved = plan(load_document(str(EXAMPLE)))
        assert resolved.order == ("dataset", "qhat", "pinned")
        assert resolved.role_of("qhat") == "train"
        assert resolved.role_of("pinned") == "signal"

    def test_uses_this_packs_classes_by_import_path(self):
        doc = load_document(str(EXAMPLE))
        assert doc.pipeline["qhat"].uses.endswith(":TinyTransformerFit")
        assert doc.pipeline["pinned"].uses.endswith(":TransformerPredict")
        assert doc.pipeline["qhat"].mode == "train"
        assert doc.pipeline["pinned"].mode == "load"
        assert doc.pipeline["pinned"].artifact


# ---------------------------------------------------------------------------
# The conformance suite over the pack's concrete pairs
# ---------------------------------------------------------------------------


def test_probe_rows_make_a_silent_refit_numerically_distinguishable(tmp_path):
    """Pins the S2-C bar behind the probes: ``verify_loaded`` accepts only
    a prediction within 1e-9 of the pinned fit, and a refit on the probes'
    inverted rows lands ~0.02 away at ROWS[0] — the NUMBER, not the
    paperwork, is what discriminates a restore from a silent refit."""
    pinned = _fit(tmp_path, "pin")
    refit = _fit(tmp_path, "refit", rows=INVERTED_ROWS)
    gap = abs(pinned["signal"].predict(ROWS[0]) - refit["signal"].predict(ROWS[0]))
    assert gap > 1e-4


def probes(tmp_path):
    """Populated trainable probes. The fixture checkpoint is fitted fresh
    under ``tmp_path`` on ROWS; the probes' wired rows carry the OPPOSITE
    relationship (``y -> 1 - y``), so ``verify_loaded`` discriminates a
    restore from a silent refit on the PREDICTION itself (S2-C) — the
    provenance pair (``restored``/``artifact_path``) alone was paperwork a
    refit could counterfeit by echoing the pinned path and flag."""
    fixture = _fit(tmp_path, "fixture")
    checkpoint = fixture["artifact_path"]
    expected = fixture["signal"].predict(ROWS[0])

    def verify_loaded(out):
        signal = out["signal"]
        prediction = signal.predict(ROWS[0])
        return (
            bool(signal.restored)
            and signal.artifact_path == checkpoint
            and prediction is not None
            and abs(prediction - expected) < 1e-9
        )

    declared = _fit(
        tmp_path,
        "fixture-declared",
        params=DECLARED_FIT_PARAMS,
        cls=DeclaredTransformerFit,
    )
    declared_checkpoint = declared["artifact_path"]
    declared_expected = declared["signal"].predict(ROWS[0])

    def declared_verify_loaded(out):
        signal = out["signal"]
        prediction = signal.predict(ROWS[0])
        return (
            bool(signal.restored)
            and signal.artifact_path == declared_checkpoint
            and prediction is not None
            and abs(prediction - declared_expected) < 1e-9
        )

    return {
        "transformers-fit": NodeProbe(
            params=dict(DECLARED_FIT_PARAMS),
            required=("features", "config_class", "model_class"),
            inputs={"rows": [dict(row) for row in INVERTED_ROWS]},
            stream_ports=("rows",),
            runnable=True,
            load_artifact=declared_checkpoint,
            verify_loaded=declared_verify_loaded,
        ),
        "transformers-tiny-fit": NodeProbe(
            params=dict(FIT_PARAMS),
            required=("features",),
            inputs={"rows": [dict(row) for row in INVERTED_ROWS]},
            stream_ports=("rows",),
            runnable=True,
            load_artifact=checkpoint,
            verify_loaded=verify_loaded,
        ),
        "transformers-predict": NodeProbe(
            params={"artifact_dir": checkpoint},
            inputs={},
            stream_ports=(),
            runnable=True,
            load_artifact=checkpoint,
            verify_loaded=verify_loaded,
        ),
    }


def test_the_probes_reject_a_counterfeit_that_echoes_the_pinned_provenance(tmp_path):
    """S2-C: the provenance-only ``verify_loaded`` blessed ANY output that
    echoed the pinned checkpoint and the restored flag — a silently refit
    model included. Reading the NUMBER rejects the counterfeit."""
    probe = probes(tmp_path)["transformers-predict"]
    checkpoint = probe.load_artifact
    counterfeit = _fit(tmp_path, "counterfeit", rows=INVERTED_ROWS)["signal"]
    counterfeit.artifact_path = checkpoint  # echoes the pin...
    counterfeit.restored = True  # ...and the flag; the weights are a refit
    assert not probe.verify_loaded(
        {"signal": counterfeit, "metrics": {"restored": 1.0}}
    )


#: This module's bar covers the fit/predict family; the pretrained trio
#: (ADR-0083) runs its own suite in ``test_transformers_pretrained.py``
#: over an acquired snapshot.
FIT_PREDICT_KINDS = tuple(pair for pair in NODE_KINDS if pair[0] in EXPECTED_ROLES)


TestTransformersConformance = conformance_suite(
    registry=FIT_PREDICT_KINDS,
    module="dskit.pipeline.libs.transformers",
    probes=probes,
    expected_roles=EXPECTED_ROLES,
    name="TestTransformersConformance",
)
