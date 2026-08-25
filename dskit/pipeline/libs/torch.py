"""Torch library pack — the generic train/predict doorway (docs/25 §2,
ADR-0025, tier 2).

``TorchTrain`` (role ``train``) and ``TorchPredict`` (role ``signal``)
are SUBCLASS HOOKS: a project subclasses, implements
``build_module(self, params) -> torch.nn.Module`` (and optionally
``loss(self, module, batch)``; the default dispatches on the ``loss``
param — mse/mae/quantile), and references the subclass from a document.
Raw functions are never referenceable (D-145). Two concrete families
ship:

* ``LinearRegressor``/``LinearPredictor`` — the reference pair, one
  ``nn.Linear`` over ``params["features"]``.
* ``DeclaredTrain``/``DeclaredPredict`` (ADR-0025) — the DOCUMENT names
  the ``nn.Module`` by import path (``module: "pkg.module:Class"``,
  built as ``Class(**module_params)``); a model swap is a config edit,
  never a new subclass. The declared class is resolved at RUN (importing
  a project's model module at plan would drag torch into planning);
  its NAME's shape is checked at plan, the pyomo-pack precedent.

Scope, stated plainly: this pack is the generic tier-2 doorway ANY
project could use — it is NOT a bespoke sequence-model zoo. Domain
architectures stay tier-3 classes in the child, referenced through the
declared seam or subclassed onto these bases.

What the base owns, so no subclass re-invents it:

* **The training loop** — ``epochs``, ``lr``, ``optimizer``
  (``sgd``/``adam``/``adamw``), ``weight_decay``, ``grad_clip``, the
  ``loss`` param (``mse``/``mae``/``quantile`` + ``loss_tau``), and the
  docs/24 §3 ``loader`` block ``{"batch_size", "shuffle", "seed"}``.
  Every nested block is DEFAULT-DENY inside (the I-227 nested-knob
  territory): an unknown key is refused BY NAME at plan time.
  ``num_workers``/``pin_memory``/``drop_last`` stay deliberately
  unsupported — batching here is single-process and deterministic, and
  a worker pool would silently cost that.
* **Validation + early stopping** — an optional ``val_rows`` input is
  scored (the node's own ``loss``) once per epoch into the curve; the
  ``early_stopping`` block ``{"patience", "min_delta"}`` stops on a
  stalled val loss and RESTORES the best epoch's weights. Declaring
  ``early_stopping`` without wiring ``val_rows`` is refused by name.
* **The curve** — every epoch's ``train_loss`` (and ``val_loss`` when
  wired) is recorded into a :class:`~dskit.pipeline.trainlog.TrainingCurve`
  and written beside the model as ``trainlog.json`` (ADR-0025): which
  epoch won is run evidence, not a log line.
* **Windowed inputs** — the ``sequence`` block ``{"group_by",
  "order_by", "lookback"}`` turns the row stream into per-entity
  lookback windows: rows are grouped by ``group_by``, ordered by
  ``order_by``, and the module receives ``(batch, lookback,
  n_features)`` with the window-END row's label — the loader story the
  child gap reports flagged. Strided/multi-resolution sampling is a
  subclass concern. A sequence signal predicts from a WINDOW (a list of
  rows), never a single record — handing it one raises with the fix in
  the message rather than reading as silent no-coverage.
* **Determinism** — ``torch.manual_seed(loader.seed)`` before the module
  is built (weight init) and a dedicated ``torch.Generator`` for the
  in-split shuffle, so two trains with one seed on one device produce
  IDENTICAL state dicts; the seed is recorded in the artifact
  (docs/25 §2). ``device`` is ``"cpu"`` (default), ``"auto"``
  (cuda → mps → cpu), or an explicit torch device string; the trained
  module always returns to CPU before saving, so the artifact bytes
  never depend on where the fit ran. Cross-DEVICE bitwise identity is
  torch's to promise, not this pack's — the guarantee is per device.
* **The artifact** — ``model.pt`` (the ``state_dict``) plus a
  ``model.json`` sidecar (seed, params, module class import path,
  ``state_hash``) under the run's ``artifacts/<key>/``. ``mode="load"``
  REALLY restores the state dict — it refuses BY NAME on a missing or
  mismatched sidecar and never refits — and the sidecar's recorded class
  must build the SAME module as the invoking class (compared by
  ``build_module`` function identity, so a train/predict pair sharing
  one mixin matches). For the declared pair the identity check passes by
  construction and the ``module``/``module_params`` params cross-check
  (below) is what refuses a different declared model.
* **What ``state_hash`` covers (S2-A)** — the state-file bytes AND the
  sidecar itself: sha256 over ``model.pt``'s bytes, a NUL byte, then the
  canonical JSON (sorted keys, compact separators) of every sidecar
  field except ``state_hash`` (it cannot cover its own value). The
  sidecar is schema, not decoration — :class:`TorchPredict` serves the
  SIDECAR's ``features`` order when the node declares none, so a digest
  over the state file alone let a reordered feature list silently
  transpose every vector. Any sidecar edit now fails the hash exactly
  like a state-file edit.
* **Shape cross-checks** — ``features``/``label``/``sequence`` (and the
  declared pair's ``module``/``module_params``) pin the trained module's
  serving shape; a load where the node and sidecar disagree is refused
  by name. Training knobs (epochs, lr, optimizer, …) are history, not
  shape, and may lawfully differ.
* **The signal** — ``run`` returns a :class:`TorchSignal` exposing
  ``predict(row_or_record) -> float | None`` (``None`` = no coverage,
  the ``validate`` kind skips it; sequence signals take a window — a
  LIST of rows — instead) with provenance: ``artifact_path`` and a
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
from dskit.pipeline.trainlog import TrainingCurve

__all__ = [
    "ARTIFACT_FORMAT",
    "DeclaredPredict",
    "DeclaredTrain",
    "EARLY_STOPPING_PARAMS",
    "LOADER_PARAMS",
    "LinearPredictor",
    "LinearRegressor",
    "NODE_KINDS",
    "SEQUENCE_PARAMS",
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

#: The ``early_stopping`` block (ADR-0025) — default-deny inside, like
#: ``loader``. ``patience`` is required; ``min_delta`` defaults to 0.0.
EARLY_STOPPING_PARAMS = ("min_delta", "patience")

#: The ``sequence`` block (ADR-0025) — default-deny inside. All three
#: keys are required: a window with no grouping, no ordering, or no
#: length is not a window.
SEQUENCE_PARAMS = ("group_by", "lookback", "order_by")

#: Optimizers the loop can build — a closed vocabulary, refused by name
#: otherwise (a typo must not silently become SGD).
OPTIMIZERS = ("adam", "adamw", "sgd")

#: Built-in objectives the default ``loss`` hook dispatches on.
LOSSES = ("mae", "mse", "quantile")

LOADER_DEFAULTS = {"batch_size": 32, "shuffle": True, "seed": 0}
DEFAULT_EPOCHS = 5
DEFAULT_LR = 0.01
DEFAULT_LABEL = "label"
DEFAULT_OPTIMIZER = "sgd"
DEFAULT_LOSS = "mse"
DEFAULT_LOSS_TAU = 0.5

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


def _raw_value(record, name):
    """Key-or-attr lookup WITHOUT the numeric frame — what ``group_by``/
    ``order_by`` read (a ticker is a string, a date an ISO string).
    ``None``/missing means the row cannot join a window."""
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def _positive_number_problem(problems, name, value, *, allow_zero=False):
    """Append a problem unless ``value`` is a finite number (> 0, or >= 0
    with ``allow_zero``)."""
    floor = "0 or more" if allow_zero else "> 0"
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (value < 0 if allow_zero else value <= 0)
    ):
        problems.append(f"{name} must be a finite number {floor}, got {value!r}")


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


def _early_stopping_problems(block):
    """Problems with an ``early_stopping`` block — default-deny inside,
    the loader rule again."""
    if not isinstance(block, dict) or any(not isinstance(k, str) for k in block):
        return [
            f"early_stopping must be a dict with keys from "
            f"{sorted(EARLY_STOPPING_PARAMS)}, got {block!r}"
        ]
    problems = []
    unknown = sorted(set(block) - set(EARLY_STOPPING_PARAMS))
    if unknown:
        problems.append(
            f"early_stopping: unknown key(s) {unknown} — allowed: "
            f"{sorted(EARLY_STOPPING_PARAMS)} (default-deny inside the "
            "block, I-227)"
        )
    if "patience" not in block:
        problems.append(
            "early_stopping.patience is required — how many epochs a "
            "stalled val loss is tolerated before stopping"
        )
    else:
        _check_int(problems, "early_stopping.patience", block["patience"], ge=1)
    _positive_number_problem(
        problems,
        "early_stopping.min_delta",
        block.get("min_delta", 0.0),
        allow_zero=True,
    )
    return problems


def _sequence_problems(block):
    """Problems with a ``sequence`` block — default-deny inside; all
    three keys required."""
    if not isinstance(block, dict) or any(not isinstance(k, str) for k in block):
        return [
            f"sequence must be a dict with keys from {sorted(SEQUENCE_PARAMS)}, "
            f"got {block!r}"
        ]
    problems = []
    unknown = sorted(set(block) - set(SEQUENCE_PARAMS))
    if unknown:
        problems.append(
            f"sequence: unknown key(s) {unknown} — allowed: "
            f"{sorted(SEQUENCE_PARAMS)} (default-deny inside the block, "
            "I-227; strided/multi-resolution sampling is a subclass "
            "concern, not a knob)"
        )
    for name in ("group_by", "order_by"):
        value = block.get(name)
        if not isinstance(value, str) or not value:
            problems.append(
                f"sequence.{name} is required and must be a non-empty row-key "
                f"string, got {value!r}"
            )
    if "lookback" not in block:
        problems.append(
            "sequence.lookback is required — the window length in rows (>= 2; "
            "a 1-row window is just the flat row path)"
        )
    else:
        _check_int(problems, "sequence.lookback", block["lookback"], ge=2)
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


def _sorted_group(key, order_by, group):
    """One entity's ``(order_value, row)`` pairs sorted ascending, or a
    refusal naming the key — heterogeneous order values cannot be a
    timeline."""
    try:
        return sorted(group, key=lambda pair: pair[0])
    except TypeError as exc:
        raise ValueError(
            f"{key}: sequence.order_by values are not mutually orderable "
            f"within one group — {exc}"
        ) from exc


def _usable_windows(key, rows, features, label, sequence):
    """``(xs, ys, n_skipped)`` as LOOKBACK WINDOWS (ADR-0025).

    Rows are grouped by ``sequence.group_by`` and ordered by
    ``sequence.order_by`` (ascending); every run of ``lookback``
    CONSECUTIVE rows whose features are all finite — and whose LAST row
    carries a finite label — becomes one ``[lookback][n_features]``
    sample labeled by that last row. ``n_skipped`` counts rows that
    could not join any window (missing group/order value) PLUS candidate
    windows dropped for a gap — skipped and counted, never fabricated,
    the ``_usable_rows`` rule at window granularity.
    """
    group_by = sequence["group_by"]
    order_by = sequence["order_by"]
    lookback = sequence["lookback"]
    groups = {}
    skipped = 0
    for row in rows:
        group = _raw_value(row, group_by)
        order = _raw_value(row, order_by)
        if group is None or order is None:
            skipped += 1  # a row with no place on any timeline
            continue
        groups.setdefault(group, []).append((order, row))
    xs, ys = [], []
    for group_key in sorted(groups, key=repr):  # deterministic group order
        ordered = _sorted_group(key, order_by, groups[group_key])
        feats = [[_value(row, name) for name in features] for _, row in ordered]
        labels = [_value(row, label) for _, row in ordered]
        for end in range(lookback - 1, len(ordered)):
            window = feats[end - lookback + 1 : end + 1]
            target = labels[end]
            if target is None or any(v is None for vec in window for v in vec):
                skipped += 1
                continue
            xs.append(window)
            ys.append(target)
    return xs, ys, skipped


class TorchSignal:
    """What a torch node's ``signal`` output IS: predictions + provenance.

    Flat signals answer ``predict(row_or_record) -> float | None``
    (``None`` = no coverage — a missing/non-finite feature — never a
    fabricated number; the toolkit's ``validate`` kind skips ``None``).
    SEQUENCE signals (``sequence`` set — the trained window spec) answer
    ``predict(window)`` where the window is a LIST of at least
    ``lookback`` rows; the trailing ``lookback`` rows, sorted by the
    trained ``order_by`` key, feed the module. Handing a sequence signal
    a single record raises with the fix in the message — that is a
    wiring error, and reading it as "no coverage" would silently zero a
    validation.

    Provenance is load-bearing, not decoration: ``artifact_path`` names
    the state file this module came from (or was saved to) and ``loaded``
    says whether it was RESTORED rather than fitted — the pair a probe's
    ``verify_loaded`` checks, so a silent refit cannot impersonate a
    restore (F-220 #12).
    """

    __slots__ = ("artifact_path", "features", "loaded", "module", "sequence")

    def __init__(self, module, features, artifact_path, *, loaded, sequence=None):
        self.module = module
        self.features = tuple(features)
        self.artifact_path = artifact_path
        self.loaded = bool(loaded)
        self.sequence = dict(sequence) if sequence else None

    def _vector(self, record):
        values = [_value(record, name) for name in self.features]
        return None if any(v is None for v in values) else values

    def predict(self, record):
        """One row (or, for a sequence signal, one window of rows) in, a
        float out — or ``None`` when any feature is missing or non-finite
        (no coverage, never a made-up number)."""
        import torch

        if self.sequence is not None:
            if not isinstance(record, (list, tuple)):
                raise TypeError(
                    "this signal was trained on lookback windows "
                    f"(sequence={self.sequence!r}) — predict takes a LIST of "
                    f"at least {self.sequence['lookback']} rows, got "
                    f"{type(record).__name__}"
                )
            lookback = self.sequence["lookback"]
            order_by = self.sequence["order_by"]
            pairs = []
            for row in record:
                order = _raw_value(row, order_by)
                if order is None:
                    return None  # a row with no place on the timeline
                pairs.append((order, row))
            if len(pairs) < lookback:
                return None  # not enough history — no coverage
            try:
                pairs.sort(key=lambda pair: pair[0])
            except TypeError:
                return None  # unorderable window — no coverage
            window = []
            for _, row in pairs[-lookback:]:
                vec = self._vector(row)
                if vec is None:
                    return None
                window.append(vec)
            batch = torch.tensor([window], dtype=torch.float32)
        else:
            values = self._vector(record)
            if values is None:
                return None
            batch = torch.tensor([values], dtype=torch.float32)
        with torch.no_grad():
            out = self.module(batch)
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
    #: Base knobs that pin the trained module's SHAPE (its serving
    #: contract); a load where these disagree with the sidecar is refused
    #: by name (training knobs like epochs/lr/optimizer may lawfully
    #: differ — they are history, not shape). ``sequence`` is shape: a
    #: window-trained module cannot serve flat rows.
    _SHAPE_PARAMS = ("features", "label", "sequence")

    @classmethod
    def _allowed(cls):
        return tuple(cls._BASE_PARAMS) + tuple(cls._EXTRA_PARAMS)

    @abstractmethod
    def build_module(self, params):
        """Return the ``torch.nn.Module`` these params describe. The
        subclass hook — import torch INSIDE it, never at module top."""
        raise NotImplementedError

    @staticmethod
    def _resolve_device(device):
        """The torch device for ``device`` — ``"auto"`` walks
        cuda → mps → cpu; anything else is handed to torch verbatim and
        refused by torch's own error if unknown. Run-time only: whether
        an accelerator exists is unknowable at plan."""
        import torch

        if device != "auto":
            return torch.device(device)
        if torch.cuda.is_available():
            return torch.device("cuda")
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")

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
        # Disagreement-by-ABSENCE is a shape mismatch too (skeptic pass):
        # a node declaring `sequence` over a flat-trained artifact would
        # otherwise load "cleanly" and feed windows through a flat module —
        # `out.reshape(-1)[0]` then serves the OLDEST window row's
        # prediction, silently wrong on every call. (The reverse — a node
        # declaring nothing — lawfully serves the sidecar's own values;
        # defaulted knobs like `label` are absent from BOTH sides and never
        # trip this.)
        if self.params.get("sequence") is not None and "sequence" not in trained:
            self._refuse(
                "this node declares a sequence block but the artifact was "
                "trained on flat rows — a flat module cannot serve windows"
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
    ``build_module`` (plus optionally ``loss``; the default dispatches on
    the ``loss`` param: mse / mae / quantile).

    Knobs: ``features`` (required list of row keys), ``label`` (default
    ``"label"``), ``epochs``, ``lr``, ``optimizer`` (sgd/adam/adamw),
    ``weight_decay``, ``grad_clip``, ``device`` (cpu/auto/explicit),
    ``loss`` + ``loss_tau``, the docs/24 §3 ``loader`` block, the
    ``early_stopping`` block (needs ``val_rows``), and the ``sequence``
    block (per-entity lookback windows) — every block default-deny
    inside (I-227). Input port ``rows`` is a LIST of dict/record rows
    (``val_rows``, optional, likewise); rows or windows missing a finite
    value are skipped and counted, never fabricated.

    ``mode="train"`` (or omitted) fits fresh, deterministically:
    ``torch.manual_seed(loader.seed)`` pins the init and a dedicated
    generator pins the shuffle, so one seed = one state dict (per
    device). Every epoch's losses land in a ``TrainingCurve`` written as
    ``trainlog.json``; with ``early_stopping`` the best val epoch's
    weights are restored before saving. The fit is saved as ``model.pt``
    + ``model.json`` (seed, params, module class, content hash) and
    ``artifact_path`` leaves through the outputs so a later run can pin
    it. ``mode="load"`` RESTORES that artifact — refuse by name on a
    missing/mismatched sidecar, never refit.
    """

    role = "train"
    outputs = ("signal", "artifact_path", "metrics")

    _BASE_PARAMS = (
        "device",
        "early_stopping",
        "epochs",
        "features",
        "grad_clip",
        "label",
        "loader",
        "loss",
        "loss_tau",
        "lr",
        "optimizer",
        "sequence",
        "weight_decay",
    )

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._allowed())
        _check_int(problems, "epochs", params.get("epochs", DEFAULT_EPOCHS), ge=1)
        _positive_number_problem(problems, "lr", params.get("lr", DEFAULT_LR))
        optimizer = params.get("optimizer", DEFAULT_OPTIMIZER)
        if optimizer not in OPTIMIZERS:
            problems.append(
                f"optimizer must be one of {sorted(OPTIMIZERS)}, got {optimizer!r}"
            )
        _positive_number_problem(
            problems,
            "weight_decay",
            params.get("weight_decay", 0.0),
            allow_zero=True,
        )
        if "grad_clip" in params:
            _positive_number_problem(problems, "grad_clip", params["grad_clip"])
        device = params.get("device", "cpu")
        if not isinstance(device, str) or not device:
            problems.append(
                f"device must be a non-empty string ('cpu', 'auto', or a torch "
                f"device), got {device!r}"
            )
        loss = params.get("loss", DEFAULT_LOSS)
        if loss not in LOSSES:
            problems.append(f"loss must be one of {sorted(LOSSES)}, got {loss!r}")
        if "loss_tau" in params:
            tau = params["loss_tau"]
            if loss != "quantile":
                problems.append(
                    f"loss_tau is only meaningful with loss='quantile' "
                    f"(declared loss: {loss!r}) — remove it or declare the "
                    "quantile objective"
                )
            if (
                isinstance(tau, bool)
                or not isinstance(tau, (int, float))
                or not math.isfinite(tau)
                or not (0.0 < tau < 1.0)
            ):
                problems.append(
                    f"loss_tau must be a number strictly between 0 and 1, "
                    f"got {tau!r}"
                )
        problems.extend(_loader_problems(params.get("loader", {})))
        if "early_stopping" in params:
            problems.extend(_early_stopping_problems(params["early_stopping"]))
        if "sequence" in params:
            problems.extend(_sequence_problems(params["sequence"]))
        problems.extend(_feature_problems(params, required=True))
        return problems

    def validate_inputs(self, inputs):
        if self.mode == "load":
            return []  # nothing is consumed — the artifact IS the input
        problems = []
        rows = inputs.get("rows")
        if not isinstance(rows, list):
            problems.append(
                "rows must be a LIST of feature/label rows — a one-shot "
                f"iterable is refused by name, got {type(rows).__name__} "
                "(walking it here would hand run() an exhausted stream)"
            )
        val_rows = inputs.get("val_rows")
        if val_rows is not None and not isinstance(val_rows, list):
            problems.append(
                "val_rows must be a LIST of feature/label rows when wired, "
                f"got {type(val_rows).__name__} (the rows rule)"
            )
        if "early_stopping" in self.params and val_rows is None:
            problems.append(
                "early_stopping is declared but no val_rows input is wired — "
                "stopping on the TRAIN loss would reward memorization; wire "
                "val_rows or drop the block"
            )
        return problems

    def loss(self, module, batch):
        """The training objective for one ``(features, label)`` batch —
        dispatched on the ``loss`` param (mse / mae / quantile with
        ``loss_tau``). Override for other objectives; return a scalar
        tensor the loop can backpropagate."""
        import torch

        features, label = batch
        predicted = module(features).reshape(-1)
        kind = self.params.get("loss", DEFAULT_LOSS)
        if kind == "mae":
            return torch.nn.functional.l1_loss(predicted, label)
        if kind == "quantile":
            tau = float(self.params.get("loss_tau", DEFAULT_LOSS_TAU))
            diff = label - predicted
            return torch.maximum(tau * diff, (tau - 1.0) * diff).mean()
        return torch.nn.functional.mse_loss(predicted, label)

    def _build_optimizer(self, module):
        """The declared optimizer over the module's parameters."""
        import torch

        lr = float(self.params.get("lr", DEFAULT_LR))
        decay = float(self.params.get("weight_decay", 0.0))
        kind = self.params.get("optimizer", DEFAULT_OPTIMIZER)
        if kind == "adam":
            return torch.optim.Adam(module.parameters(), lr=lr, weight_decay=decay)
        if kind == "adamw":
            return torch.optim.AdamW(module.parameters(), lr=lr, weight_decay=decay)
        return torch.optim.SGD(module.parameters(), lr=lr, weight_decay=decay)

    def _prepared(self, key, rows, features, label):
        """``(xs, ys, skipped)`` — windows under a ``sequence`` block,
        flat rows otherwise."""
        sequence = self.params.get("sequence")
        if sequence:
            return _usable_windows(key, rows, features, label, sequence)
        return _usable_rows(rows, features, label)

    def run(self, ctx, inputs):
        if self.mode == "load":
            module, sidecar = self._load_artifact(self.artifact)
            features = self.params.get("features") or sidecar["params"].get(
                "features", ()
            )
            sequence = self.params.get("sequence") or sidecar["params"].get("sequence")
            self.log.info("restored %s from %s", self._class_ref(), self.artifact)
            return {
                "signal": TorchSignal(
                    module, features, self.artifact, loaded=True, sequence=sequence
                ),
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
        sequence = self.params.get("sequence")
        shape = "window" if sequence else "row"
        xs, ys, skipped = self._prepared(self.key, inputs["rows"], features, label)
        if not xs:
            raise ValueError(
                f"{self.key}: no usable rows — every {shape} lacked a finite "
                f"value for features {features} + label {label!r} "
                f"({skipped} {shape}(s) seen)"
            )
        val_rows = inputs.get("val_rows")
        have_val = val_rows is not None
        val_x = val_y = None
        val_skipped = 0
        if have_val:
            vxs, vys, val_skipped = self._prepared(self.key, val_rows, features, label)
            if not vxs:
                raise ValueError(
                    f"{self.key}: no usable val_rows — every {shape} lacked a "
                    f"finite value for features {features} + label {label!r} "
                    f"({val_skipped} {shape}(s) seen); an empty validation set "
                    "cannot steer early stopping"
                )

        device = self._resolve_device(self.params.get("device", "cpu"))
        torch.manual_seed(seed)  # pins the init: one seed, one state dict
        module = self.build_module(self.params).to(device)
        x = torch.tensor(xs, dtype=torch.float32, device=device)
        y = torch.tensor(ys, dtype=torch.float32, device=device)
        if have_val:
            val_x = torch.tensor(vxs, dtype=torch.float32, device=device)
            val_y = torch.tensor(vys, dtype=torch.float32, device=device)
        optimizer = self._build_optimizer(module)
        grad_clip = self.params.get("grad_clip")
        early = self.params.get("early_stopping")
        patience = early["patience"] if early else None
        min_delta = float(early.get("min_delta", 0.0)) if early else 0.0
        order_gen = torch.Generator().manual_seed(seed)  # pins the shuffle
        curve = TrainingCurve()
        best_val = None
        best_state = None
        best_epoch = None
        stalled = 0
        epochs_run = 0
        for epoch in range(epochs):
            module.train()
            if loader["shuffle"]:
                order = torch.randperm(len(xs), generator=order_gen)
            else:
                order = torch.arange(len(xs))
            for start in range(0, len(xs), batch_size):
                idx = order[start : start + batch_size]
                optimizer.zero_grad()
                self.loss(module, (x[idx], y[idx])).backward()
                if grad_clip is not None:
                    torch.nn.utils.clip_grad_norm_(
                        module.parameters(), float(grad_clip)
                    )
                optimizer.step()
            epochs_run = epoch + 1
            module.eval()
            with torch.no_grad():
                row = {"train_loss": float(self.loss(module, (x, y)))}
                if have_val:
                    row["val_loss"] = float(self.loss(module, (val_x, val_y)))
            curve.record(epoch, row)
            if have_val:
                val_loss = row["val_loss"]
                improved = best_val is None or val_loss < best_val - min_delta
                if improved:
                    best_val, best_epoch, stalled = val_loss, epoch, 0
                    if early:
                        # The clone is only ever RESTORED under early
                        # stopping — without it, cloning a large module
                        # every improving epoch is pure wasted memory.
                        best_state = {
                            k: v.detach().clone()
                            for k, v in module.state_dict().items()
                        }
                else:
                    stalled += 1
                if early and stalled >= patience:
                    self.log.info(
                        "early stopping at epoch %d (best val_loss %.6f at "
                        "epoch %d, patience %d)",
                        epoch,
                        best_val,
                        best_epoch,
                        patience,
                    )
                    break
        if early and best_state is not None:
            module.load_state_dict(best_state)  # the best epoch ships, not the last
        module.to("cpu")  # artifact bytes must not depend on the fit device
        module.eval()
        x_cpu, y_cpu = x.to("cpu"), y.to("cpu")
        with torch.no_grad():
            final_loss = float(self.loss(module, (x_cpu, y_cpu)))
        artifact_path = self._save_artifact(ctx, module, seed)
        self.write_artifact(ctx, "trainlog.json", curve.to_obj())
        self.log.info(
            "trained %s on %d %s(s) (%d skipped), %d/%d epoch(s), seed %d -> %s",
            self._class_ref(),
            len(xs),
            shape,
            skipped,
            epochs_run,
            epochs,
            seed,
            artifact_path,
        )
        metrics = {
            "n_rows": len(xs),
            "n_skipped": skipped,
            "epochs": epochs,
            "epochs_run": epochs_run,
            "seed": seed,
            "final_loss": final_loss,
        }
        if have_val:
            metrics["n_val_rows"] = len(vxs)
            metrics["n_val_skipped"] = val_skipped
            metrics["best_val_loss"] = best_val
            metrics["best_epoch"] = best_epoch
            metrics["stopped_early"] = int(bool(early and epochs_run < epochs))
        return {
            "signal": TorchSignal(
                module, features, artifact_path, loaded=False, sequence=sequence
            ),
            "artifact_path": artifact_path,
            "metrics": metrics,
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
    when its ``build_module`` is not this class's. ``features``/``label``/
    ``sequence`` may be declared to cross-check the sidecar; omitted, the
    sidecar's own trained values are used (a window-trained artifact
    serves a window-taking signal automatically).
    """

    role = "signal"
    outputs = ("signal",)

    _BASE_PARAMS = ("artifact", "features", "label", "sequence")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._allowed())
        artifact = params.get("artifact")
        if artifact is not None and (not isinstance(artifact, str) or not artifact):
            problems.append(
                f"artifact must be a non-empty string path, got {artifact!r}"
            )
        if "sequence" in params:
            problems.extend(_sequence_problems(params["sequence"]))
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
        sequence = self.params.get("sequence") or sidecar["params"].get("sequence")
        self.log.info("restored %s from %s", self._class_ref(), reference)
        return {
            "signal": TorchSignal(
                module, features, reference, loaded=True, sequence=sequence
            )
        }


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


class _DeclaredModule:
    """The ADR-0025 build hook: the DOCUMENT names the ``nn.Module``.

    ``module`` is a ``pkg.module:Class`` import path; ``module_params``
    (a JSON object) is splatted into its constructor verbatim — the
    pyomo-pack ``solver_options`` precedent: the class's own signature is
    the contract, and an unknown kwarg fails there with the class named.
    The ref's SHAPE is checked at plan; resolution waits for run, where
    importing a model module (and torch under it) is lawful. Both params
    are load-time cross-checked against the sidecar (``_EXTRA_PARAMS``),
    so a declared-model artifact can only restore under the model that
    trained it.
    """

    _EXTRA_PARAMS = ("module", "module_params")

    @classmethod
    def _declared_problems(cls, params, *, required):
        problems = []
        ref = params.get("module")
        if ref is None:
            if required:
                problems.append(
                    "module is required — the nn.Module's import path "
                    "('pkg.module:Class'); the document names the model "
                    "(ADR-0025)"
                )
        elif not is_class_ref(ref):
            problems.append(
                f"module must be a 'pkg.module:Class' import path, got {ref!r}"
            )
        module_params = params.get("module_params")
        if module_params is not None and (
            not isinstance(module_params, dict)
            or any(not isinstance(k, str) or not k for k in module_params)
        ):
            problems.append(
                "module_params must be a dict of constructor kwargs (string "
                f"keys), got {module_params!r}"
            )
        return problems

    def build_module(self, params):
        import torch

        ref = params["module"]
        cls = import_ref(ref)  # raises ValueError naming the ref
        if not (isinstance(cls, type) and issubclass(cls, torch.nn.Module)):
            raise ValueError(
                f"{self.key}: module {ref!r} is not a torch.nn.Module "
                "subclass — the declared seam builds modules, nothing else"
            )
        try:
            return cls(**params.get("module_params", {}))
        except TypeError as exc:
            raise ValueError(
                f"{self.key}: module {ref!r} rejected module_params "
                f"{params.get('module_params', {})!r}: {exc}"
            ) from exc


class DeclaredTrain(_DeclaredModule, TorchTrain):
    """The declared trainer (ADR-0025): ``module`` names the
    ``nn.Module``, the base owns the loop. Registered as ``torch-train``."""

    @classmethod
    def validate_params(cls, params):
        problems = super().validate_params(params)
        problems.extend(cls._declared_problems(params, required=True))
        return problems


class DeclaredPredict(_DeclaredModule, TorchPredict):
    """The declared inference node for :class:`DeclaredTrain` artifacts —
    ``module`` may be omitted (the sidecar's trained value serves) or
    declared to cross-check it. Registered as ``torch-predict``."""

    @classmethod
    def validate_params(cls, params):
        problems = super().validate_params(params)
        problems.extend(cls._declared_problems(params, required=False))
        return problems


#: The pack's registerable kinds — CONCRETE classes only; the abstract
#: bases are subclass material, not kinds.
NODE_KINDS = (
    ("torch-linear-train", LinearRegressor),
    ("torch-linear-predict", LinearPredictor),
    ("torch-train", DeclaredTrain),
    ("torch-predict", DeclaredPredict),
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
