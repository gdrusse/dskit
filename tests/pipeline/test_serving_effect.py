"""ADR-0091: the closed serving-effect vocabulary on the Node ABC.

Four things are pinned here, all of them fail-closed by design:

* the vocabulary itself (``SERVING_EFFECTS``) and the base defaults —
  an unannotated class is ``forbidden`` and offers no serving contract;
* ``TrainableNode``'s one widening: ``release_read`` for a
  manifest-pinned LOAD, and nothing else;
* the phase-1 audit — one line per registered kind and per synthetic
  class, so a NEW kind must be classified deliberately rather than
  inheriting a blanket answer;
* that ``serving_effect`` is PURE (it decides before anything is
  constructed, so it may not touch the filesystem or a socket) and that
  the audited ``release_read`` classes reach their artifact only through
  the base's read services.
"""

import ast
import builtins
import dataclasses
import inspect
import json
import os
import socket
import textwrap

import subprocess
import sys
import pytest

import dskit.pipeline  # noqa: F401 — importing REGISTERS the toolkit kinds
from dskit.pipeline import fitted, synthetic_nodes
from dskit.pipeline import node as node_module
from dskit.pipeline.libs.observations import ObservationRows
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node, NodeContext, TrainableNode

ASOF = "2026-01-01"

#: The evidence a manifest-pinned load presents (SEAM-DESIGN §1).
LOAD_EVIDENCE = {"mode": "load", "artifact_pinned": True}

#: Representative params for the classes whose knobs are required.
OBS_PARAMS = {
    "root": "./ob",
    "source": "alpaca",
    "stream": "bars",
    "key_fields": ["symbol", "ts"],
    "ts_field": "ts",
}
FITTED_PARAMS = {"fit_split": "train", "features": ["ret_lag_0"]}


# ---------------------------------------------------------------------------
# The phase-1 audit table (SEAM-DESIGN §5) — kind name -> the effect the
# class answers with NO run evidence, and the effect it answers under a
# manifest-pinned load.
# ---------------------------------------------------------------------------

KIND_EFFECTS = {
    "apply-transform": ("pure", "pure"),
    "banking-report": ("forbidden", "forbidden"),
    "concat": ("pure", "pure"),
    "derive": ("pure", "pure"),
    "eligibility": ("forbidden", "forbidden"),
    "event-bank": ("forbidden", "forbidden"),
    "event-grid": ("pure", "pure"),
    "filter": ("pure", "pure"),
    "groupby": ("pure", "pure"),
    "hpo-grid": ("forbidden", "forbidden"),
    "join": ("pure", "pure"),
    "records-write": ("forbidden", "forbidden"),
    "run-report": ("forbidden", "forbidden"),
    "standardize": ("forbidden", "release_read"),
    "stat_test": ("forbidden", "forbidden"),
    "table-file": ("forbidden", "forbidden"),
    "table-write": ("forbidden", "forbidden"),
    "top-trials": ("forbidden", "forbidden"),
    "validate": ("forbidden", "forbidden"),
}

#: The audited classes that no toolkit registry claims a name for:
#: ``observations`` and ``FeatureSelector`` are wired by import path, and
#: the synthetic set registers only into private/demo registries.
CLASS_EFFECTS = {
    ObservationRows: ("entry_read", "entry_read"),
    fitted.FeatureSelector: ("forbidden", "release_read"),
    synthetic_nodes.SynthBank: ("pure", "pure"),
    synthetic_nodes.SynthCapital: ("forbidden", "forbidden"),
    synthetic_nodes.SynthClip: ("pure", "pure"),
    synthetic_nodes.SynthEligibility: ("pure", "pure"),
    synthetic_nodes.SynthEvents: ("forbidden", "forbidden"),
    synthetic_nodes.SynthLabels: ("forbidden", "forbidden"),
    synthetic_nodes.SynthMarketSignal: ("pure", "pure"),
    synthetic_nodes.SynthReport: ("forbidden", "forbidden"),
    synthetic_nodes.SynthScore: ("forbidden", "forbidden"),
    synthetic_nodes.SynthSearch: ("forbidden", "forbidden"),
    synthetic_nodes.SynthStatTest: ("forbidden", "forbidden"),
    synthetic_nodes.SynthTrain: ("forbidden", "release_read"),
}

#: The classes SEAM-DESIGN §5 audits as ``release_read``: their artifact
#: reads go through the base's services, never a bare ``open``.
RELEASE_READ_CLASSES = (
    fitted.Standardize,
    fitted.FeatureSelector,
    synthetic_nodes.SynthTrain,
)

#: Names that would mean a class reached the filesystem or the network on
#: the load path rather than through ``Node.read_artifact`` /
#: ``read_artifact_text``. The base's WRITE services count: a served tick
#: hands a ``release_read`` node a reader that can only ANSWER, and a load
#: hook that persisted anything would be reaching past it — the reader
#: cannot be given a write verb to intercept, so the write must not be
#: there at all.
IO_NAMES = (
    "open(", "os.", "io.", "socket", "pathlib",
    "write_artifact", "artifact_dir",
)


def audited_classes():
    """Every class in the audit, kind entries resolved to their classes."""
    out = dict(CLASS_EFFECTS)
    for name in KIND_EFFECTS:
        cls, _owned = DEFAULT_NODE_KINDS.get(name)
        out[cls] = KIND_EFFECTS[name]
    return out


def params_for(cls):
    """A representative params dict for one audited class."""
    if cls is ObservationRows:
        return dict(OBS_PARAMS)
    if issubclass(cls, fitted.FittedTransform):
        return dict(FITTED_PARAMS)
    return {}


def boom(*args, **kwargs):
    """Stand-in for any I/O primitive: being called at all is the defect."""
    raise AssertionError(f"I/O attempted: {args!r} {kwargs!r}")


class FakeReader:
    """A ``ReleaseReader`` stand-in: manifest-named text, nothing else."""

    def __init__(self, values):
        self.values = dict(values)
        self.asked = []

    def get(self, name):
        self.asked.append(name)
        return self.values[name]

    def names(self):
        return tuple(sorted(self.values))


class ArtifactProbe(Node):
    """Reads one artifact through the base's read services."""

    role = "transform"
    outputs = ("value",)

    def run(self, ctx, inputs):
        return {"value": self.read_artifact(ctx, "model.json")}


class LoadableTrainable(TrainableNode):
    """A concrete trainable, so the classmethod can be asked of a real class."""

    role = "train"
    outputs = ("value",)

    def run_train(self, ctx, inputs):
        return {"value": 1}

    def run_load(self, ctx, inputs):
        return {"value": self.read_artifact(ctx, "model.json")}


# ---------------------------------------------------------------------------
# The vocabulary and the base defaults
# ---------------------------------------------------------------------------


class TestVocabulary:
    def test_serving_effects_is_the_closed_four_member_tuple(self):
        assert node_module.SERVING_EFFECTS == (
            "pure",
            "entry_read",
            "release_read",
            "forbidden",
        )

    def test_the_vocabulary_and_the_contract_are_exported(self):
        assert "SERVING_EFFECTS" in node_module.__all__
        assert "ServingContract" in node_module.__all__

    def test_the_base_effect_is_forbidden_and_is_a_classmethod(self):
        assert Node.serving_effect({}, {}) == "forbidden"
        assert Node.serving_effect({"anything": 1}, LOAD_EVIDENCE) == "forbidden"
        assert isinstance(inspect.getattr_static(Node, "serving_effect"), classmethod)

    def test_the_base_offers_no_serving_contract(self):
        assert Node.serving_contract({}, {}) is None
        assert Node.serving_contract({"anything": 1}, LOAD_EVIDENCE) is None
        assert isinstance(inspect.getattr_static(Node, "serving_contract"), classmethod)


# ---------------------------------------------------------------------------
# NodeContext.release_reader — additive, and additive at the END
# ---------------------------------------------------------------------------


class TestReleaseReaderField:
    def test_release_reader_is_the_last_field_so_no_positional_caller_moves(self):
        names = [f.name for f in dataclasses.fields(NodeContext)]
        assert names == [
            "name",
            "asof",
            "run_dir",
            "splits",
            "splits_info",
            "secrets",
            "tracker",
            "prev",
            "rerun",
            "fold_index",
            "release_reader",
        ]

    def test_the_drivers_own_construction_still_works_and_defaults_to_none(
        self, tmp_path
    ):
        # The exact keyword shape driver._node_context uses.
        ctx = NodeContext(
            name="doc",
            asof=ASOF,
            run_dir=str(tmp_path),
            splits=None,
            splits_info={},
            secrets=None,
            tracker=None,
            prev={},
            fold_index=None,
        )
        assert ctx.release_reader is None

    def test_the_ten_positional_arguments_still_construct(self, tmp_path):
        ctx = NodeContext("doc", ASOF, str(tmp_path), None, {}, None, None, {}, None, 0)
        assert ctx.release_reader is None
        assert ctx.fold_index == 0

    def test_replace_carries_a_reader_without_touching_the_original(self, tmp_path):
        ctx = NodeContext(name="doc", asof=ASOF, run_dir=str(tmp_path))
        reader = FakeReader({})
        with_reader = dataclasses.replace(ctx, release_reader=reader)
        assert with_reader.release_reader is reader
        assert ctx.release_reader is None


# ---------------------------------------------------------------------------
# TrainableNode — release_read for a manifest-pinned load, nothing else
# ---------------------------------------------------------------------------


class TestTrainableEffect:
    @pytest.mark.parametrize("cls", [TrainableNode, LoadableTrainable])
    def test_a_manifest_pinned_load_is_release_read(self, cls):
        assert cls.serving_effect({}, {"mode": "load", "artifact_pinned": True}) == (
            "release_read"
        )

    @pytest.mark.parametrize(
        "evidence",
        [
            {},
            {"mode": "train"},
            {"mode": "train", "artifact_pinned": True},
            {"mode": "load"},
            {"mode": "load", "artifact_pinned": False},
            {"mode": "load", "artifact_pinned": None},
            {"mode": "load", "artifact_pinned": "yes"},
            {"mode": "load", "artifact_pinned": 1},
            {"artifact_pinned": True},
        ],
    )
    def test_everything_short_of_a_pinned_load_is_forbidden(self, evidence):
        assert LoadableTrainable.serving_effect({}, evidence) == "forbidden"

    def test_a_trainable_still_offers_no_serving_contract(self):
        assert LoadableTrainable.serving_contract({}, LOAD_EVIDENCE) is None


# ---------------------------------------------------------------------------
# The phase-1 registry-enumeration audit
# ---------------------------------------------------------------------------


class TestAudit:
    def test_every_registered_kind_is_classified_deliberately(self):
        # A fresh interpreter: another test module's adapter (synth_adapter)
        # registers extra kinds into the shared registry when it is imported
        # in the same process, and those are the ADAPTER's to classify.
        code = (
            "import json, dskit.pipeline\n"
            "from dskit.pipeline.node import DEFAULT_NODE_KINDS\n"
            "print(json.dumps(sorted(DEFAULT_NODE_KINDS.kinds())))"
        )
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        assert set(json.loads(done.stdout)) == set(KIND_EFFECTS)

    def test_every_synthetic_node_class_is_classified_deliberately(self):
        declared = {
            getattr(synthetic_nodes, name)
            for name in synthetic_nodes.__all__
            if isinstance(getattr(synthetic_nodes, name), type)
            and issubclass(getattr(synthetic_nodes, name), Node)
        }
        assert declared
        assert sorted(c.__qualname__ for c in declared - set(CLASS_EFFECTS)) == []

    @pytest.mark.parametrize("name", sorted(KIND_EFFECTS))
    def test_each_registered_kinds_effect(self, name):
        cls, _owned = DEFAULT_NODE_KINDS.get(name)
        bare, pinned = KIND_EFFECTS[name]
        params = params_for(cls)
        assert cls.serving_effect(params, {}) == bare
        assert cls.serving_effect(params, LOAD_EVIDENCE) == pinned

    @pytest.mark.parametrize(
        "cls", sorted(CLASS_EFFECTS, key=lambda c: c.__qualname__)
    )
    def test_each_unregistered_audited_classs_effect(self, cls):
        bare, pinned = CLASS_EFFECTS[cls]
        params = params_for(cls)
        assert cls.serving_effect(params, {}) == bare
        assert cls.serving_effect(params, LOAD_EVIDENCE) == pinned

    def test_only_the_entry_class_offers_a_serving_contract(self):
        offenders = []
        for cls in audited_classes():
            if cls is ObservationRows:
                continue
            if cls.serving_contract(params_for(cls), LOAD_EVIDENCE) is not None:
                offenders.append(cls.__qualname__)
        assert offenders == []

    def test_every_audited_answer_is_a_member_of_the_vocabulary(self):
        effects = node_module.SERVING_EFFECTS
        offenders = []
        for cls in audited_classes():
            for evidence in ({}, LOAD_EVIDENCE):
                answer = cls.serving_effect(params_for(cls), evidence)
                if answer not in effects:
                    offenders.append((cls.__qualname__, answer))
        assert offenders == []

    def test_serving_effect_performs_no_io_for_any_audited_class(self, monkeypatch):
        monkeypatch.setattr(builtins, "open", boom)
        monkeypatch.setattr(os, "listdir", boom)
        monkeypatch.setattr(os, "scandir", boom)
        monkeypatch.setattr(os, "stat", boom)
        monkeypatch.setattr(socket, "socket", boom)
        failures = []
        for cls in audited_classes():
            for evidence in ({}, LOAD_EVIDENCE):
                try:
                    cls.serving_effect(params_for(cls), evidence)
                except Exception as exc:  # reported, never swallowed
                    failures.append(f"{cls.__qualname__}: {exc!r}")
        monkeypatch.undo()
        assert failures == []

    def test_serving_contract_performs_no_io_for_any_audited_class(self, monkeypatch):
        monkeypatch.setattr(builtins, "open", boom)
        monkeypatch.setattr(os, "listdir", boom)
        monkeypatch.setattr(os, "scandir", boom)
        monkeypatch.setattr(os, "stat", boom)
        monkeypatch.setattr(socket, "socket", boom)
        failures = []
        for cls in audited_classes():
            try:
                cls.serving_contract(params_for(cls), LOAD_EVIDENCE)
            except Exception as exc:  # reported, never swallowed
                failures.append(f"{cls.__qualname__}: {exc!r}")
        monkeypatch.undo()
        assert failures == []


# ---------------------------------------------------------------------------
# The audited release_read classes reach an artifact only through the base
# ---------------------------------------------------------------------------


def self_attrs(source):
    """The ``self.<name>`` attributes named anywhere in ``source``."""
    return {
        n.attr
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
    }


def owner_of(cls, name):
    """The class in ``cls``'s MRO that DEFINES ``name``, or ``None``."""
    for base in cls.__mro__:
        if name in vars(base):
            return base
    return None


def load_sources(cls):
    """``run_load`` plus the class's OWN helpers it calls, as source text.

    ``Node``'s own artifact services are excluded on purpose: they ARE
    the sanctioned doorway, and reading through them is what the audit
    asks for.
    """
    sources = {"run_load": textwrap.dedent(inspect.getsource(cls.run_load))}
    for name in sorted(self_attrs(sources["run_load"])):
        owner = owner_of(cls, name)
        if owner is None or owner is Node:
            continue
        member = vars(owner)[name]
        if not inspect.isfunction(member):
            continue
        sources[name] = textwrap.dedent(inspect.getsource(member))
    return sources


class TestNoDirectIoOnTheLoadPath:
    @pytest.mark.parametrize(
        "cls", RELEASE_READ_CLASSES, ids=lambda c: c.__qualname__
    )
    def test_run_load_and_its_own_helpers_name_no_io_primitive(self, cls):
        offenders = []
        for where, source in load_sources(cls).items():
            offenders.extend(
                f"{cls.__qualname__}.{where}: {name}"
                for name in IO_NAMES
                if name in source
            )
        assert offenders == []


# ---------------------------------------------------------------------------
# The read side of the artifact services
# ---------------------------------------------------------------------------


class TestReadArtifact:
    def test_a_release_reader_answers_and_no_file_is_opened(
        self, tmp_path, monkeypatch
    ):
        reader = FakeReader({"model.json": '{"learn": 0.8}'})
        ctx = NodeContext(
            name="t", asof=ASOF, run_dir=str(tmp_path), release_reader=reader
        )
        node = ArtifactProbe("probe", {})
        monkeypatch.setattr(builtins, "open", boom)
        text = node.read_artifact_text(ctx, "model.json")
        parsed = node.read_artifact(ctx, "model.json")
        monkeypatch.undo()
        assert text == '{"learn": 0.8}'
        assert parsed == {"learn": 0.8}
        assert reader.asked == ["model.json", "model.json"]

    def test_a_directory_reference_joins_the_filename(self, tmp_path):
        art = tmp_path / "artifacts" / "qhat"
        art.mkdir(parents=True)
        (art / "model.json").write_text('{"learn": 0.8}', encoding="utf-8")
        ctx = NodeContext(name="t", asof=ASOF, run_dir=str(tmp_path))
        node = ArtifactProbe("probe", {}, artifact=str(art))
        assert node.read_artifact_text(ctx, "model.json") == '{"learn": 0.8}'
        assert node.read_artifact(ctx, "model.json") == {"learn": 0.8}

    def test_a_file_reference_is_read_whatever_the_filename_says(self, tmp_path):
        path = tmp_path / "pinned.json"
        path.write_text(json.dumps({"learn": 0.5}), encoding="utf-8")
        ctx = NodeContext(name="t", asof=ASOF, run_dir=str(tmp_path))
        node = ArtifactProbe("probe", {}, artifact=str(path))
        assert node.read_artifact(ctx, "ignored.json") == {"learn": 0.5}

    def test_an_explicit_ref_wins_over_the_nodes_own_artifact(self, tmp_path):
        pinned = tmp_path / "pinned"
        pinned.mkdir()
        (pinned / "model.json").write_text('{"learn": 0.1}', encoding="utf-8")
        other = tmp_path / "other"
        other.mkdir()
        (other / "model.json").write_text('{"learn": 0.9}', encoding="utf-8")
        ctx = NodeContext(name="t", asof=ASOF, run_dir=str(tmp_path))
        node = ArtifactProbe("probe", {}, artifact=str(pinned))
        assert node.read_artifact(ctx, "model.json", ref=str(other)) == {"learn": 0.9}

    def test_nothing_naming_an_artifact_refuses_by_name(self, tmp_path):
        # No reader and no pin: the doorway says so rather than opening
        # whatever ``""`` resolves to and reporting the OS's error.
        ctx = NodeContext(name="t", asof=ASOF, run_dir=str(tmp_path))
        node = ArtifactProbe("probe", {})
        with pytest.raises(ValueError) as exc:
            node.read_artifact_text(ctx, "model.json")
        assert "probe" in str(exc.value)
        assert "model.json" in str(exc.value)
        with pytest.raises(ValueError):
            node.read_artifact(ctx, "model.json")

    def test_the_reader_wins_over_a_pinned_artifact_on_disk(
        self, tmp_path, monkeypatch
    ):
        pinned = tmp_path / "pinned"
        pinned.mkdir()
        (pinned / "model.json").write_text('{"learn": 0.1}', encoding="utf-8")
        reader = FakeReader({"model.json": '{"learn": 0.8}'})
        ctx = NodeContext(
            name="t", asof=ASOF, run_dir=str(tmp_path), release_reader=reader
        )
        node = ArtifactProbe("probe", {}, artifact=str(pinned))
        monkeypatch.setattr(builtins, "open", boom)
        parsed = node.read_artifact(ctx, "model.json")
        monkeypatch.undo()
        assert parsed == {"learn": 0.8}
