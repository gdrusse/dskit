"""Torch library pack — the generic train/predict doorway (docs/25 §2, tier 2).

``TorchTrain`` (role ``train``) and ``TorchPredict`` (role ``signal``) are
SUBCLASS HOOKS: a project subclasses, implements
``build_module(self, params) -> torch.nn.Module`` (and optionally
``loss(self, module, batch) -> tensor``; the default is MSE over
``(features, label)``), and references the subclass from a document.
Raw functions are never referenceable (D-145); the concrete reference
family here is ``LinearRegressor``/``LinearPredictor`` — one
``nn.Linear`` over ``params["features"]``.

Scope, stated plainly: this pack is the generic tier-2 doorway ANY
project could use — it is NOT a bespoke ladder-transformer family and does
not replace a project's own sequence-model zoo. Those live outside the
pipeline; the ladder family enters the node map later as a TIER-3 ADAPTER
node, possibly built ON these bases. ``TorchMap``
(docs/25 §2, numpy row) lands with the numpy pack's ``ArrayMap`` base,
not here.

What the base owns, so no subclass re-invents it:

* **The training loop** — ``epochs``, ``lr``, and the docs/24 §3 ``loader``
  block ``{"batch_size", "shuffle", "seed"}``. The block is DEFAULT-DENY
  inside (the I-227 nested-knob territory): an unknown key in ``loader``
  is refused BY NAME at plan time. ``num_workers``/``pin_memory``/
  ``drop_last`` from the wider docs/24 convention are deliberately
  unsupported — batching here is single-process and deterministic, and a
  worker pool would silently cost that.
* **Determinism** — ``torch.manual_seed(loader.seed)`` before the module
  is built (weight init) and a dedicated ``torch.Generator`` for the
  in-split shuffle, so two trains with one seed produce IDENTICAL state
  dicts; the seed is recorded in the artifact (docs/25 §2, verbatim).
* **The artifact** — ``model.pt`` (the ``state_dict``) plus a ``model.json``
  sidecar (seed, params, module class import path, ``state_hash``) under
  the run's ``artifacts/<key>/``. ``mode="load"`` REALLY restores the state
  dict — it refuses BY NAME on a missing or mismatched sidecar and never
  refits — and the sidecar's recorded class must build the SAME module as
  the invoking class (compared by ``build_module`` function identity, so a
  train/predict pair sharing one mixin matches).
* **What ``state_hash`` covers (S2-A)** — the state-file bytes AND the
  sidecar itself: sha256 over ``model.pt``'s bytes, a NUL byte, then the
  canonical JSON (sorted keys, compact separators) of every sidecar field
  except ``state_hash`` (it cannot cover its own value). The sidecar is
  schema, not decoration — :class:`TorchPredict` serves the SIDECAR's
  ``features`` order when the node declares none, so a digest over the
  state file alone let a reordered feature list silently transpose every
  vector. Any sidecar edit now fails the hash exactly like a state-file
  edit. Sidecars written under the bytes-only digest no longer verify:
  retrain to re-pin them.
* **The signal** — ``run`` returns a :class:`TorchSignal` exposing
  ``predict(row_or_record) -> float | None`` (``None`` = no coverage, the
  ``validate`` kind skips it) with provenance: ``artifact_path`` and a
  ``loaded`` flag, which is what lets a probe's ``verify_loaded`` reject
  a fresh fit.

Inputs are ROWS — a list of dicts (or attribute-bearing records), the
FitRows/ArrayFeatures shape; feature and label keys are named by params.
In-memory only: nothing here reads a data file.

Packs never auto-register: :data:`NODE_KINDS` plus an explicit
:func:`register` call is the deliberate path (``libs/__init__``), and the
abstract bases stay OUT of the table (``node_class_errors`` refuses
abstract classes).

Import cost: stdlib + ``dskit.pipeline`` only — torch is imported
strictly inside run-path methods (``tests/pipeline/test_purity.py``
enforces this twice).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from abc import abstractmethod
from collections.abc import Mapping

from dskit.pipeline.base import import_ref, is_class_ref
from dskit.pipeline.kinds_stats import _check_int, _reject_unknown
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = [
    "ARTIFACT_FORMAT",
    "LOADER_PARAMS",
    "LinearPredictor",
    "LinearRegressor",
    "NODE_KINDS",
    "TorchPredict",
    "TorchSignal",
    "TorchTrain",
    "register",
]

#: The sidecar's format tag — a loader refuses any other by name.
ARTIFACT_FORMAT = "dskit-torch-v1"

#: The docs/24 §3 ``loader`` block, as this pack supports it. DEFAULT-DENY
#: inside the block (I-227): any other key — including the wider docs/24
#: convention's ``num_workers``/``pin_memory``/``drop_last`` — is refused
#: by name, because batching here is single-process and deterministic.
LOADER_PARAMS = ("batch_size", "shuffle", "seed")

LOADER_DEFAULTS = {"batch_size": 32, "shuffle": True, "seed": 0}
DEFAULT_EPOCHS = 5
DEFAULT_LR = 0.01
DEFAULT_LABEL = "label"

#: Keys every sidecar must carry — an artifact without them is refused.
_SIDECAR_KEYS = ("format", "module_class", "params", "seed", "state_hash")


def _value(record, name):
    """Key-or-attr numeric lookup on one row: a finite float, or ``None``
    (no coverage — never a fabricated number). Bools count as 0/1 so a
    ``settled_yes`` outcome can be a label directly.

    MAPPING-FIRST is load-bearing (S2-B): a dict row with a feature named
    ``items``/``keys``/``values`` must yield the VALUE, never the bound
    ``dict`` method an attr-first lookup finds — which read as "no
    coverage" on predict and killed every row on fit.
    """
    if isinstance(record, Mapping):
        value = record.get(name)
    else:
        value = getattr(record, name, None)
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _loader_problems(loader):
    """Problems with a ``loader`` block — default-deny INSIDE the block.

    Nested dict knobs are exactly where a typo hides from the top-level
    deny list (I-227), so this block's own keys are validated as strictly
    as the top level's: unknown keys are refused by name at PLAN.
    """
    if not isinstance(loader, dict) or any(not isinstance(k, str) for k in loader):
        return [
            f"loader must be a dict with keys from {sorted(LOADER_PARAMS)}, "
            f"got {loader!r}"
        ]
    problems = []
    unknown = sorted(set(loader) - set(LOADER_PARAMS))
    if unknown:
        problems.append(
            f"loader: unknown key(s) {unknown} — allowed: {sorted(LOADER_PARAMS)} "
            "(default-deny inside the block, I-227; num_workers/pin_memory/"
            "drop_last are deliberately unsupported — batching is "
            "single-process so the recorded seed fully determines the fit)"
        )
    _check_int(
        problems,
        "loader.batch_size",
        loader.get("batch_size", LOADER_DEFAULTS["batch_size"]),
        ge=1,
    )
    shuffle = loader.get("shuffle", LOADER_DEFAULTS["shuffle"])
    if not isinstance(shuffle, bool):
        problems.append(f"loader.shuffle must be a bool, got {shuffle!r}")
    _check_int(
        problems, "loader.seed", loader.get("seed", LOADER_DEFAULTS["seed"]), ge=0
    )
    return problems


def _feature_problems(params, *, required):
    """Problems with the ``features``/``label`` row-key params."""
    problems = []
    features = params.get("features")
    if features is None:
        if required:
            problems.append(
                "features is required — the list of row keys the module reads"
            )
    elif (
        not isinstance(features, list)
        or not features
        or any(not isinstance(f, str) or not f for f in features)
    ):
        problems.append(
            f"features must be a non-empty list of row-key strings, got {features!r}"
        )
    label = params.get("label")
    if label is not None and (not isinstance(label, str) or not label):
        problems.append(f"label must be a non-empty string row key, got {label!r}")
    return problems


def _usable_rows(rows, features, label):
    """``(xs, ys, n_skipped)`` from the row stream — a row missing any
    feature or the label (or carrying a non-finite value) is SKIPPED and
    counted, never fabricated into the fit."""
    xs, ys, skipped = [], [], 0
    for row in rows:
        values = [_value(row, name) for name in features]
        target = _value(row, label)
        if target is None or any(v is None for v in values):
            skipped += 1
            continue
        xs.append(values)
        ys.append(target)
    return xs, ys, skipped


class TorchSignal:
    """What a torch node's ``signal`` output IS: predictions + provenance.

    ``predict(row_or_record)`` answers a float, or ``None`` for no
    coverage (a missing/non-finite feature) — the toolkit's ``validate``
    kind skips ``None`` rather than scoring a fabricated belief.

    Provenance is load-bearing, not decoration: ``artifact_path`` names
    the state file this module came from (or was saved to) and ``loaded``
    says whether it was RESTORED rather than fitted — the pair a probe's
    ``verify_loaded`` checks, so a silent refit cannot impersonate a
    restore (F-220 #12).
    """

    __slots__ = ("artifact_path", "features", "loaded", "module")

    def __init__(self, module, features, artifact_path, *, loaded):
        self.module = module
        self.features = tuple(features)
        self.artifact_path = artifact_path
        self.loaded = bool(loaded)

    def predict(self, record):
        """One row in, a float out — or ``None`` when any feature is
        missing or non-finite (no coverage, never a made-up number)."""
        import torch

        values = [_value(record, name) for name in self.features]
        if any(v is None for v in values):
            return None
        with torch.no_grad():
            out = self.module(torch.tensor([values], dtype=torch.float32))
        return float(out.reshape(-1)[0])


class _TorchModel(Node):
    """The grammar the train and predict doorways share: the
    ``build_module`` hook and the artifact save/load protocol.

    Abstract by construction (``build_module`` is the subclass's
    identity), so neither base can enter a registry —
    ``node_class_errors`` refuses abstract classes.
    """

    #: Role-specific knobs, set by :class:`TorchTrain`/:class:`TorchPredict`.
    _BASE_PARAMS = ()
    #: A concrete family's OWN model knobs — appended to the allowed list
    #: and to the shape cross-check at load.
    _EXTRA_PARAMS = ()
    #: Base knobs that pin the trained module's SHAPE; a load where these
    #: disagree with the sidecar is refused by name (training knobs like
    #: epochs/lr may lawfully differ — they are history, not shape).
    _SHAPE_PARAMS = ("features", "label")

    @classmethod
    def _allowed(cls):
        return tuple(cls._BASE_PARAMS) + tuple(cls._EXTRA_PARAMS)

    @abstractmethod
    def build_module(self, params):
        """Return the ``torch.nn.Module`` these params describe. The
        subclass hook — import torch INSIDE it, never at module top."""
        raise NotImplementedError

    # -- the artifact protocol ---------------------------------------------

    @classmethod
    def _class_ref(cls):
        """This class's import path — what the sidecar records."""
        return f"{cls.__module__}:{cls.__qualname__}"

    @classmethod
    def _build_fn(cls):
        """The underlying ``build_module`` function — compared by IDENTITY
        at load, so a train/predict pair sharing one mixin matches and a
        different family is refused."""
        return getattr(cls.build_module, "__func__", cls.build_module)

    def _refuse(self, why):
        """Refuse a load BY NAME — every message names the artifact, so a
        refusal is never mistaken for an unrelated crash."""
        raise ValueError(
            f"{self.key}: cannot load artifact — {why}. mode='load' restores "
            "a pinned artifact exactly; it never refits."
        )

    @staticmethod
    def _state_hash(state_path, sidecar):
        """The artifact's identity: state bytes + the sidecar (S2-A).

        sha256 over ``model.pt``'s bytes, a NUL separator, then the
        canonical JSON of every sidecar field except ``state_hash``
        itself. The sidecar carries the feature schema a predict node
        serves with, so it must be as tamper-evident as the state file.
        """
        material = {k: v for k, v in sidecar.items() if k != "state_hash"}
        digest = hashlib.sha256()
        with open(state_path, "rb") as fh:
            digest.update(fh.read())
        digest.update(b"\0")
        digest.update(
            json.dumps(
                material, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        )
        return digest.hexdigest()

    def _save_artifact(self, ctx, module, seed):
        """Write ``model.pt`` + the ``model.json`` sidecar; returns the
        state file's path (the artifact reference a document pins)."""
        import torch

        state_path = os.path.join(self.artifact_dir(ctx), "model.pt")
        torch.save(module.state_dict(), state_path)
        sidecar = {
            "format": ARTIFACT_FORMAT,
            "module_class": self._class_ref(),
            "params": self.params,
            "seed": seed,
        }
        # Hashed LAST, over the material above: the digest covers the state
        # bytes and every other sidecar field (S2-A).
        sidecar["state_hash"] = self._state_hash(state_path, sidecar)
        self.write_artifact(ctx, "model.json", sidecar)
        return state_path

    def _load_artifact(self, state_path):
        """Restore ``(module, sidecar)`` from a pinned state file, or
        refuse by name. Never fits anything."""
        import torch

        if not isinstance(state_path, str) or not state_path:
            self._refuse(f"no usable artifact reference, got {state_path!r}")
        if not os.path.isfile(state_path):
            self._refuse(f"artifact state file {state_path!r} does not exist")
        sidecar_path = os.path.splitext(state_path)[0] + ".json"
        if not os.path.isfile(sidecar_path):
            self._refuse(
                f"artifact sidecar {sidecar_path!r} is missing — without it "
                "the seed, params and module class are unverifiable"
            )
        try:
            with open(sidecar_path, encoding="utf-8") as fh:
                sidecar = json.load(fh)
        except ValueError as exc:
            self._refuse(f"artifact sidecar {sidecar_path!r} is not JSON: {exc}")
        if not isinstance(sidecar, dict):
            self._refuse(f"artifact sidecar {sidecar_path!r} is not a JSON object")
        missing = [k for k in _SIDECAR_KEYS if k not in sidecar]
        if missing:
            self._refuse(f"artifact sidecar {sidecar_path!r} lacks key(s) {missing}")
        if sidecar["format"] != ARTIFACT_FORMAT:
            self._refuse(
                f"artifact format {sidecar['format']!r} is not {ARTIFACT_FORMAT!r}"
            )
        got = self._state_hash(state_path, sidecar)
        if got != sidecar["state_hash"]:
            self._refuse(
                f"artifact content hash mismatch for {state_path!r} — the state "
                "file or its sidecar is not the one that was written (the hash "
                f"covers both; sidecar {sidecar['state_hash']!r}, computed {got!r})"
            )
        ref = sidecar["module_class"]
        if not is_class_ref(ref):
            self._refuse(f"artifact sidecar names no importable class: {ref!r}")
        try:
            recorded = import_ref(ref)
        except ValueError as exc:
            self._refuse(f"artifact sidecar's module class is unresolvable: {exc}")
        recorded_build = getattr(recorded, "build_module", None)
        recorded_build = getattr(recorded_build, "__func__", recorded_build)
        if recorded_build is None or recorded_build is not self._build_fn():
            self._refuse(
                f"artifact was trained by {ref!r}, whose build_module is not "
                f"this class's ({self._class_ref()!r}) — a different module "
                "family cannot restore this state"
            )
        trained = sidecar["params"]
        if not isinstance(trained, dict):
            self._refuse(f"artifact sidecar params are not a dict: {trained!r}")
        for name in tuple(self._SHAPE_PARAMS) + tuple(self._EXTRA_PARAMS):
            if name in self.params and name in trained:
                if self.params[name] != trained[name]:
                    self._refuse(
                        f"artifact sidecar mismatch on {name!r}: trained with "
                        f"{trained[name]!r}, this node declares {self.params[name]!r}"
                    )
        try:
            module = self.build_module(trained)
            state = torch.load(state_path, map_location="cpu", weights_only=True)
            module.load_state_dict(state, strict=True)
        except Exception as exc:  # noqa: BLE001 - refusal must name the artifact
            self._refuse(
                f"artifact state at {state_path!r} does not restore into the "
                f"module {self._class_ref()!r} builds: {exc}"
            )
        module.eval()
        return module, sidecar


class TorchTrain(_TorchModel):
    """The generic torch trainer (role ``train``) — subclass and implement
    ``build_module`` (plus optionally ``loss``; default MSE).

    Knobs: ``features`` (required list of row keys), ``label`` (default
    ``"label"``), ``epochs``, ``lr``, and the docs/24 §3 ``loader`` block
    (``batch_size``/``shuffle``/``seed`` — default-deny inside, I-227).
    Input port ``rows`` is a LIST of dict/record rows; rows missing a
    finite feature or label are skipped and counted, never fabricated.

    ``mode="train"`` (or omitted) fits fresh, deterministically:
    ``torch.manual_seed(loader.seed)`` pins the init and a dedicated
    generator pins the shuffle, so one seed = one state dict. The fit is
    saved as ``model.pt`` + ``model.json`` (seed, params, module class,
    content hash) and ``artifact_path`` leaves through the outputs so a
    later run can pin it. ``mode="load"`` RESTORES that artifact — refuse
    by name on a missing/mismatched sidecar, never refit.
    """

    role = "train"
    outputs = ("signal", "artifact_path", "metrics")

    _BASE_PARAMS = ("epochs", "features", "label", "loader", "lr")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._allowed())
        _check_int(problems, "epochs", params.get("epochs", DEFAULT_EPOCHS), ge=1)
        lr = params.get("lr", DEFAULT_LR)
        if (
            isinstance(lr, bool)
            or not isinstance(lr, (int, float))
            or not math.isfinite(lr)
            or lr <= 0
        ):
            problems.append(f"lr must be a finite number > 0, got {lr!r}")
        problems.extend(_loader_problems(params.get("loader", {})))
        problems.extend(_feature_problems(params, required=True))
        return problems

    def validate_inputs(self, inputs):
        if self.mode == "load":
            return []  # nothing is consumed — the artifact IS the input
        rows = inputs.get("rows")
        if not isinstance(rows, list):
            return [
                "rows must be a LIST of feature/label rows — a one-shot "
                f"iterable is refused by name, got {type(rows).__name__} "
                "(walking it here would hand run() an exhausted stream)"
            ]
        return []

    def loss(self, module, batch):
        """The training objective for one ``(features, label)`` batch —
        default MSE. Override for other objectives; return a scalar
        tensor the loop can backpropagate."""
        import torch

        features, label = batch
        return torch.nn.functional.mse_loss(module(features).reshape(-1), label)

    def run(self, ctx, inputs):
        if self.mode == "load":
            module, sidecar = self._load_artifact(self.artifact)
            features = self.params.get("features") or sidecar["params"].get(
                "features", ()
            )
            self.log.info("restored %s from %s", self._class_ref(), self.artifact)
            return {
                "signal": TorchSignal(module, features, self.artifact, loaded=True),
                "artifact_path": self.artifact,
                "metrics": {"loaded": 1, "seed": sidecar["seed"]},
            }

        import torch

        loader = dict(LOADER_DEFAULTS)
        loader.update(self.params.get("loader", {}))
        seed, batch_size = loader["seed"], loader["batch_size"]
        epochs = self.params.get("epochs", DEFAULT_EPOCHS)
        features = list(self.params["features"])
        label = self.params.get("label", DEFAULT_LABEL)
        xs, ys, skipped = _usable_rows(inputs["rows"], features, label)
        if not xs:
            raise ValueError(
                f"{self.key}: no usable rows — every row lacked a finite value "
                f"for features {features} + label {label!r} "
                f"({skipped} row(s) seen)"
            )

        torch.manual_seed(seed)  # pins the init: one seed, one state dict
        module = self.build_module(self.params)
        x = torch.tensor(xs, dtype=torch.float32)
        y = torch.tensor(ys, dtype=torch.float32)
        optimizer = torch.optim.SGD(
            module.parameters(), lr=float(self.params.get("lr", DEFAULT_LR))
        )
        order_gen = torch.Generator().manual_seed(seed)  # pins the shuffle
        module.train()
        for _epoch in range(epochs):
            if loader["shuffle"]:
                order = torch.randperm(len(xs), generator=order_gen)
            else:
                order = torch.arange(len(xs))
            for start in range(0, len(xs), batch_size):
                idx = order[start : start + batch_size]
                optimizer.zero_grad()
                self.loss(module, (x[idx], y[idx])).backward()
                optimizer.step()
        module.eval()
        with torch.no_grad():
            final_loss = float(self.loss(module, (x, y)))
        artifact_path = self._save_artifact(ctx, module, seed)
        self.log.info(
            "trained %s on %d row(s) (%d skipped), %d epoch(s), seed %d -> %s",
            self._class_ref(),
            len(xs),
            skipped,
            epochs,
            seed,
            artifact_path,
        )
        return {
            "signal": TorchSignal(module, features, artifact_path, loaded=False),
            "artifact_path": artifact_path,
            "metrics": {
                "n_rows": len(xs),
                "n_skipped": skipped,
                "epochs": epochs,
                "seed": seed,
                "final_loss": final_loss,
            },
        }


class TorchPredict(_TorchModel):
    """Inference-only from a pinned artifact (role ``signal``) — it ALWAYS
    loads, it never fits.

    The artifact reference comes from (in order): the document's
    node-level ``mode="load"`` + ``artifact``; ``params["artifact"]``; or
    the ``artifact_path`` input port (wire ``"$train_key.artifact_path"``
    to run inference downstream of a trainer in the same document). No
    reference = refuse by name. ``mode="train"`` is refused: there is
    nothing here to fit — train with the :class:`TorchTrain` family.

    The subclass must share the trainer's ``build_module`` (one mixin for
    the pair is the shape — see ``LinearRegressor``/``LinearPredictor``):
    the sidecar's recorded class is resolved at load and refused by name
    when its ``build_module`` is not this class's. ``features``/``label``
    may be declared to cross-check the sidecar; omitted, the sidecar's
    own trained values are used.
    """

    role = "signal"
    outputs = ("signal",)

    _BASE_PARAMS = ("artifact", "features", "label")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._allowed())
        artifact = params.get("artifact")
        if artifact is not None and (not isinstance(artifact, str) or not artifact):
            problems.append(
                f"artifact must be a non-empty string path, got {artifact!r}"
            )
        problems.extend(_feature_problems(params, required=False))
        return problems

    def validate_inputs(self, inputs):
        path = inputs.get("artifact_path")
        if path is not None and (not isinstance(path, str) or not path):
            return [
                "artifact_path must be a non-empty string (wire it from a "
                f"train node's artifact_path output), got {path!r}"
            ]
        return []

    def run(self, ctx, inputs):
        if self.mode == "train":
            raise NotImplementedError(
                f"{self.key}: torch-predict is inference-only — mode='train' "
                "fits nothing here; train with the TorchTrain family and pin "
                "its artifact"
            )
        if self.mode == "load":
            reference = self.artifact
            if not reference:
                self._refuse("mode='load' was given an empty artifact reference")
        else:
            reference = self.params.get("artifact") or (inputs or {}).get(
                "artifact_path"
            )
            if not reference:
                self._refuse(
                    "no artifact reference — set mode='load' + artifact, "
                    "params['artifact'], or wire inputs['artifact_path'] from "
                    "a train node"
                )
        module, sidecar = self._load_artifact(reference)
        features = self.params.get("features") or sidecar["params"].get("features")
        if not features:
            self._refuse(
                "artifact sidecar records no features and this node declares "
                "none — the signal would have no row keys to read"
            )
        self.log.info("restored %s from %s", self._class_ref(), reference)
        return {"signal": TorchSignal(module, features, reference, loaded=True)}


class _LinearModule:
    """The reference family's shared build hook: one ``nn.Linear`` over
    ``params["features"]``. One mixin for the train/predict pair is the
    shape to copy — it is exactly what makes the sidecar's class-match
    check pass across the pair (same ``build_module`` function)."""

    def build_module(self, params):
        import torch

        return torch.nn.Linear(len(params["features"]), 1)


class LinearRegressor(_LinearModule, TorchTrain):
    """The concrete reference trainer: linear regression of ``label`` on
    ``features`` under the base's deterministic loop. Registered as
    ``torch-linear-train``."""


class LinearPredictor(_LinearModule, TorchPredict):
    """The concrete reference inference node for ``LinearRegressor``
    artifacts. Registered as ``torch-linear-predict``."""


#: The pack's registerable kinds — CONCRETE classes only; the abstract
#: bases are subclass material, not kinds.
NODE_KINDS = (
    ("torch-linear-train", LinearRegressor),
    ("torch-linear-predict", LinearPredictor),
)


def register(registry=None) -> None:
    """Claim the pack's kind names in ``registry`` (default
    :data:`~dskit.pipeline.node.DEFAULT_NODE_KINDS`).

    Explicit and idempotent: importing the pack never registers anything
    (``libs/__init__`` doctrine), a present name is skipped, never
    shadowed, and none of these is ``owned`` — ownership is the toolkit's
    doctrine marker, not a pack's to claim.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls)
