"""Transformers library pack — the generic HF fine-tune/predict doorway.

Tier 2 (D-146, docs/25 §2 row 4): a generic wrapper any project can
subclass to fine-tune a Hugging Face ``PreTrainedModel`` and to run
inference from a pinned local checkpoint. It may NAME its library, but
imports it only inside the run path — this module imports cleanly, and
its documents plan, on a machine with neither ``transformers`` nor
``torch`` installed.

Scope: this pack wraps only the HF library itself — it is NOT a bespoke
attention-model family. A hand-rolled ``torch.nn.TransformerEncoder`` or a
project's own ladder-checkpoint ensemble (restoring its OWN checkpoints,
not HF ones) is a separate tier-3 concern this generic doorway neither
forks nor shadows; a ladder-transformer applying an attention model to a
ladder tensor is one such tier-3 node, built later and possibly on top of
this doorway, not prior art for it.

The artifact IS identity (docs/25 §2): a fit ``save_pretrained``'s the
checkpoint into the run's artifact dir and writes a SIDECAR
(``sidecar.json``) inside it — seed, params, model/node class paths,
config hash, and a CONTENT DIGEST. The digest is exposed in
``outputs.metrics`` so two runs restoring different checkpoints can never
read as the same experiment, and ``mode="load"`` re-verifies it before
trusting the directory.

**What ``content_digest`` covers (S2-A):** the checkpoint's file tree AND
the sidecar's own fields — sha256 over :func:`content_digest`'s tree
digest (names + bytes of every file except the sidecar), a NUL byte, then
the canonical JSON (sorted keys, compact separators) of every sidecar
field except ``content_digest`` itself. The sidecar is schema, not
decoration: it names the model class to restore and the ``features`` the
default ``encode`` reads, so a digest over the model FILES alone left
every one of those claims freely editable. Any sidecar edit now fails
verification exactly like a weight-file edit, refused by name.
Checkpoints written under the files-only digest no longer verify: refit
to re-pin them.

The subclass contract (the ``PyomoSolve`` pattern):

* :class:`TransformerFit` (role ``train``) — implement
  ``build_model(params) -> PreTrainedModel`` (from a CONFIG or a local
  directory; the base never touches the hub), optionally override
  ``encode(rows, params)``. The base owns seeding, the minimal fine-tune
  loop, the checkpoint + sidecar, and the ``mode="load"`` restore.
* :class:`TransformerPredict` (role ``signal``) — inference-only from a
  pinned local checkpoint; it ALWAYS loads and refuses by name when it
  cannot. Concrete as shipped (the sidecar records which model class to
  restore); override ``encode`` to match a custom fit subclass.
* :class:`TinyTransformerFit` — the CONCRETE reference subclass: a
  2-layer, single-head BERT-style regressor instantiated fresh from a
  config sized to the feature columns. Zero downloads, ever.

``TransformerFit`` is abstract (``build_model``) and therefore
unregistrable by construction — :data:`NODE_KINDS` carries only the
concrete pair, and :func:`register` is explicit and idempotent, never
run at import (see ``libs/__init__``).

Import cost: stdlib + ``dskit.pipeline`` only.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from abc import abstractmethod
from collections.abc import Mapping

from dskit.pipeline.base import (
    import_library_class,
    import_ref,
    library_path_problems,
)
from dskit.pipeline.kinds_stats import _check_int, _reject_unknown
from dskit.pipeline.node import DEFAULT_NODE_KINDS, TrainableNode
from dskit.pipeline.trainlog import (
    DEFAULT_MAX_LINES,
    TrainingCurve,
    probability_metrics,
)

__all__ = [
    "NODE_KINDS",
    "SIDECAR_NAME",
    "DeclaredTransformerFit",
    "TinyTransformerFit",
    "TransformerFit",
    "TransformerPredict",
    "TransformerSignal",
    "content_digest",
    "identity_digest",
    "register",
]

#: The sidecar file written INSIDE the checkpoint directory. Excluded from
#: the content digest, because it is where the digest is recorded.
SIDECAR_NAME = "sidecar.json"

#: The family tag a sidecar carries, so a predict node can refuse a
#: directory that merely happens to contain a ``sidecar.json``.
_FAMILY = "transformers"


# ---------------------------------------------------------------------------
# Identity helpers (stdlib only — safe at module level)
# ---------------------------------------------------------------------------


def content_digest(path, *, skip=(SIDECAR_NAME,)):
    """sha256 over every file under ``path`` (names + bytes), sorted.

    The checkpoint's FILE TREE: two directories digest equal iff their
    trees are byte-identical. ``skip`` names files excluded by basename —
    the sidecar records the digest, so hashing it whole would chase its
    own tail. This is half of identity; :func:`identity_digest` folds the
    sidecar's fields into the other half.
    """
    digest = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            if name in skip:
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, path).replace(os.sep, "/")
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            with open(full, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def identity_digest(path, sidecar):
    """The checkpoint's FULL identity: file tree + the sidecar's fields.

    sha256 over :func:`content_digest`'s tree digest, a NUL separator, and
    the canonical JSON (sorted keys, compact separators) of every sidecar
    field except ``content_digest`` itself. Folding the sidecar in is what
    makes a rewritten ``model_class``, ``node_class``, ``seed`` or feature
    schema fail verification (S2-A) — those claims steer what gets
    restored and how rows are encoded, so they are identity too.
    """
    material = {k: v for k, v in sidecar.items() if k != "content_digest"}
    digest = hashlib.sha256()
    digest.update(content_digest(path).encode("ascii"))
    digest.update(b"\0")
    digest.update(
        json.dumps(
            material, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    )
    return digest.hexdigest()


def _class_ref(cls):
    """The ``module:QualName`` reference the sidecar records."""
    return f"{cls.__module__}:{cls.__qualname__}"


def _field_or_none(row, name):
    """Attr-or-key access; genuinely absent reads as ``None`` (the caller
    decides whether absence means "no coverage" or a refusal)."""
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _numeric(value):
    """A finite real number — bools refused (``True`` is not a feature)."""
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _default_encode(rows, features):
    """Numeric feature columns -> HF model kwargs, or ``None``.

    Each row's ``features`` become one float vector, handed to the model
    as ``inputs_embeds`` with a length-1 sequence axis — the trivial
    regression/classification path that works for any BERT-style model
    whose ``hidden_size`` equals the feature count. Any missing or
    non-numeric feature value declines the WHOLE batch (``None``): a
    per-row prediction reads that as "no coverage", and a fit refuses it
    loudly rather than training on a silently imputed value.
    """
    if not isinstance(features, (list, tuple)) or not features:
        return None
    vectors = []
    for row in rows:
        vector = []
        for name in features:
            value = _field_or_none(row, name)
            if not _numeric(value):
                return None
            vector.append(float(value))
        vectors.append(vector)
    import torch

    x = torch.tensor(vectors, dtype=torch.float32)
    return {"inputs_embeds": x.unsqueeze(1)}


def _quiet_transformers():
    """Silence the library's shard progress bars — run-path cosmetics only
    (they write raw carriage returns to stderr, not to the node logger)."""
    from transformers.utils import logging as hf_logging

    hf_logging.disable_progress_bar()


def _read_sidecar(where, artifact):
    """The verified sidecar of a pinned checkpoint directory, or a refusal
    that NAMES mode/load/artifact (never an unrelated crash)."""
    if not isinstance(artifact, str) or not artifact:
        raise ValueError(
            f"{where}: mode='load' needs a pinned artifact — the local "
            "checkpoint directory a prior fit saved; got none"
        )
    sidecar_path = os.path.join(artifact, SIDECAR_NAME)
    if not os.path.isdir(artifact) or not os.path.isfile(sidecar_path):
        raise ValueError(
            f"{where}: artifact {artifact!r} is not a loadable checkpoint "
            f"directory (no {SIDECAR_NAME}) — pin the directory a "
            "TransformerFit run saved, and never a hub name: nothing here "
            "ever downloads"
        )
    try:
        with open(sidecar_path, encoding="utf-8") as fh:
            sidecar = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"{where}: artifact sidecar {sidecar_path!r} is unreadable: {exc}"
        ) from exc
    if not isinstance(sidecar, dict) or sidecar.get("family") != _FAMILY:
        raise ValueError(
            f"{where}: artifact {artifact!r} carries a sidecar of family "
            f"{sidecar.get('family') if isinstance(sidecar, dict) else sidecar!r}, "
            f"not {_FAMILY!r} — refusing to load a foreign checkpoint"
        )
    recomputed = identity_digest(artifact, sidecar)
    recorded = sidecar.get("content_digest")
    if recomputed != recorded:
        raise ValueError(
            f"{where}: artifact {artifact!r} content digest {recomputed} does "
            f"not match the sidecar's recorded {recorded} — the checkpoint's "
            "files or its sidecar changed since it was saved (the digest "
            "covers both); refusing to load an artifact that is not the one "
            "that was pinned"
        )
    return sidecar, recomputed


def _restore_model(where, artifact, sidecar):
    """``from_pretrained`` on the LOCAL directory — hub access forbidden."""
    _quiet_transformers()
    ref = sidecar.get("model_class", "")
    if not isinstance(ref, str) or not ref:
        raise ValueError(
            f"{where}: artifact {artifact!r} records model_class {ref!r}, which "
            "is not a 'module:ClassName' string — refusing to guess what to "
            "restore"
        )
    try:
        model_cls = import_ref(ref)
    except ValueError as exc:
        raise ValueError(
            f"{where}: artifact {artifact!r} records model_class {ref!r} "
            f"which cannot be imported: {exc}"
        ) from exc
    model = model_cls.from_pretrained(artifact, local_files_only=True)
    model.eval()
    return model


# ---------------------------------------------------------------------------
# The signal object
# ---------------------------------------------------------------------------


class TransformerSignal:
    """Per-record inference over a fitted/restored HF model.

    ``predict(row)`` returns a float, or ``None`` when the row cannot be
    encoded (missing/non-numeric feature) — "no coverage", which the
    toolkit's owned ``validate`` kind skips rather than scoring a
    fabricated belief.

    Provenance rides on the object so a ``verify_loaded`` probe can
    DISCRIMINATE a restore from a silent refit: ``artifact_path`` is the
    checkpoint directory this signal answers from, ``digest`` its content
    digest, and ``restored`` is True only when the model came back
    through ``from_pretrained`` on a pinned artifact.
    """

    __slots__ = ("_encoder", "_model", "artifact_path", "digest", "restored")

    def __init__(self, model, encoder, *, artifact_path, digest, restored):
        self._model = model
        self._encoder = encoder
        self.artifact_path = artifact_path
        self.digest = digest
        self.restored = bool(restored)

    def predict(self, row):
        """One row in, a float out — or ``None`` for no coverage."""
        encoded = self._encoder([row])
        if encoded is None:
            return None
        import torch

        with torch.no_grad():
            out = self._model(**encoded)
        return float(out.logits.reshape(-1)[0])


# ---------------------------------------------------------------------------
# TransformerFit — the trainable doorway
# ---------------------------------------------------------------------------


class TransformerFit(TrainableNode):
    """Fine-tune an HF model over feature rows; the checkpoint is the artifact.

    ABSTRACT — subclasses implement :meth:`build_model` (and this class
    therefore cannot be registered or instantiated; only concrete
    subclasses like :class:`TinyTransformerFit` can). The base owns
    everything else:

    * **Determinism**: ``torch.manual_seed(params.seed)`` before the model
      is built and trained; the seed is recorded in the sidecar. Same
      seed + same rows = byte-identical checkpoint = equal digest.
    * **The loop**: ``steps`` SGD steps at ``lr`` over the whole batch,
      CPU-friendly, ``model(**encoded, labels=labels).loss`` — any HF
      head that computes its own loss works unchanged.
    * **The artifact**: ``save_pretrained`` into
      ``artifacts/<key>/checkpoint`` + a sidecar (seed, params, model and
      node class paths, config hash, content digest). The digest is also
      exposed in ``outputs.metrics`` — the artifact IS identity.
    * **mode="load"**: REALLY restores via ``from_pretrained`` on the
      pinned LOCAL directory (``local_files_only=True``; the hub is never
      touched) after verifying the sidecar — and refuses BY NAME when the
      directory is missing, foreign, tampered with, or was fitted under
      different ``features``/``label`` knobs. It never refits.
    """

    role = "train"
    outputs = ("signal", "artifact_path", "metrics")

    #: The base's knobs. Subclasses with their own extend this tuple
    #: (``_PARAMS = TransformerFit._PARAMS + ("depth",)``) — the shared
    #: validator default-denies against the SUBCLASS's tuple.
    _PARAMS = (
        "features",
        "label",
        "log_every",
        "lr",
        "max_log_lines",
        "seed",
        "steps",
    )

    # -- the subclass contract ---------------------------------------------

    @abstractmethod
    def build_model(self, params):
        """A fresh ``transformers.PreTrainedModel`` to fine-tune.

        Build FROM A CONFIG (or a local ``save_pretrained`` directory) —
        never from a hub name: this pack must work with no network at
        all. Called after the seed is set, so weight init is
        deterministic.
        """
        raise NotImplementedError  # pragma: no cover - abstract

    def encode(self, rows, params):
        """``rows -> model kwargs`` (dict of tensors), or ``None``.

        The default reads ``params["features"]`` numeric columns into a
        float tensor handed as ``inputs_embeds`` (see
        :func:`_default_encode`). Override for real tokenization; return
        ``None`` to decline rows that cannot be encoded — a fit refuses
        that loudly, a signal reads it as no coverage.
        """
        return _default_encode(rows, params.get("features"))

    # -- validation ----------------------------------------------------------

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        features = params.get("features")
        if features is None:
            problems.append("features is required — the numeric columns encode() reads")
        elif not isinstance(features, list) or not features:
            problems.append(
                f"features must be a non-empty list of column names, got {features!r}"
            )
        else:
            if any(not isinstance(f, str) or not f for f in features):
                problems.append(
                    f"features entries must be non-empty strings, got {features!r}"
                )
            elif len(set(features)) != len(features):
                problems.append(f"features repeats a column: {features!r}")
        label = params.get("label", "label")
        if not isinstance(label, str) or not label:
            problems.append(f"label must be a non-empty column name, got {label!r}")
        seed = params.get("seed", 0)
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            problems.append(f"seed must be an int >= 0, got {seed!r}")
        steps = params.get("steps", 2)
        if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
            problems.append(f"steps must be an int >= 1, got {steps!r}")
        lr = params.get("lr", 1e-2)
        if (
            isinstance(lr, bool)
            or not isinstance(lr, (int, float))
            or not math.isfinite(lr)
            or lr <= 0
        ):
            problems.append(f"lr must be a finite number > 0, got {lr!r}")
        _check_int(problems, "log_every", params.get("log_every", 1), ge=1)
        _check_int(
            problems,
            "max_log_lines",
            params.get("max_log_lines", DEFAULT_MAX_LINES),
            ge=0,
        )
        return problems

    def validate_train_inputs(self, inputs):
        # A load consumes NEITHER port — the checkpoint is the input — so
        # the load hook stays the base's empty default rather than
        # demanding a rows wire it never reads.
        problems = []
        rows = inputs.get("rows")
        if not isinstance(rows, list):
            # A one-shot iterable would be consumed by any walk before
            # run() saw it — refuse the shape by name, walk nothing.
            problems.append(
                "rows must be a list of feature rows (a one-shot iterable "
                f"would be consumed by validation), got {rows!r}"
            )
        val_rows = inputs.get("val_rows")
        if val_rows is not None and not isinstance(val_rows, list):
            problems.append(
                "val_rows must be a list of feature rows (or be left "
                f"unwired), got {val_rows!r}"
            )
        return problems

    # -- the run --------------------------------------------------------------

    def run_train(self, ctx, inputs):
        import torch

        rows = inputs["rows"]
        if not rows:
            raise ValueError(f"{self.key}: rows is empty — nothing to fit")
        params = self.params
        label = params.get("label", "label")
        seed = params.get("seed", 0)
        steps = params.get("steps", 2)
        lr = params.get("lr", 1e-2)
        labels = self._labels_of(rows, label)
        torch.manual_seed(seed)
        model = self.build_model(params)
        if not hasattr(model, "save_pretrained") or not hasattr(
            type(model), "from_pretrained"
        ):
            raise ValueError(
                f"{self.key}: build_model returned {type(model).__name__}, "
                "which has no save_pretrained/from_pretrained — it must be a "
                "transformers.PreTrainedModel"
            )
        encoded = self.encode(rows, params)
        if encoded is None:
            raise ValueError(
                f"{self.key}: encode() declined the fit rows — every row must "
                f"carry numeric values for features {params.get('features')!r}"
            )
        # The optional validation port: a val-split row list the DOCUMENT
        # wires. Without it a fine-tune is a black box until it ends.
        val_rows = inputs.get("val_rows") or []
        val_encoded, val_labels, val_targets = None, None, []
        if val_rows:
            val_encoded = self.encode(val_rows, params)
            if val_encoded is None:
                raise ValueError(
                    f"{self.key}: encode() declined the val_rows — every row "
                    "must carry numeric values for features "
                    f"{params.get('features')!r}"
                )
            val_labels = self._labels_of(val_rows, label)
            val_targets = [float(v) for v in val_labels.reshape(-1).tolist()]

        optimizer = torch.optim.SGD(model.parameters(), lr=lr)
        final_loss = math.nan
        curve = TrainingCurve(
            self.key,
            self.log,
            total_epochs=steps,
            objective="val_loss" if val_rows else "train_loss",
            log_every=params.get("log_every", 1),
            max_lines=params.get("max_log_lines", DEFAULT_MAX_LINES),
        )
        for step in range(1, steps + 1):
            started = time.monotonic()
            model.train()
            optimizer.zero_grad()
            out = model(**encoded, labels=labels)
            out.loss.backward()
            optimizer.step()
            final_loss = float(out.loss.detach())

            val_loss, scored = None, {}
            if val_encoded is not None:
                model.eval()
                with torch.no_grad():
                    val_out = model(**val_encoded, labels=val_labels)
                    val_loss = float(val_out.loss.detach())
                    preds = val_out.logits.reshape(-1).tolist()
                scored = probability_metrics(preds, val_targets)
            curve.record(
                step,
                final_loss,
                val_loss=val_loss,
                metrics=scored,
                seconds=time.monotonic() - started,
            )
        curve.log_final()
        model.eval()
        # Durable, not merely streamed — the run report reads this file.
        self.write_artifact(ctx, "training_curve.json", curve.payload())

        _quiet_transformers()
        checkpoint = os.path.join(self.artifact_dir(ctx), "checkpoint")
        model.save_pretrained(checkpoint)
        config_text = json.dumps(model.config.to_dict(), sort_keys=True, default=str)
        sidecar = {
            "family": _FAMILY,
            "node_class": _class_ref(type(self)),
            "model_class": _class_ref(type(model)),
            "seed": seed,
            "params": self.params,
            "config_hash": hashlib.sha256(config_text.encode("utf-8")).hexdigest(),
        }
        # Digested LAST, over the material above: identity is the file tree
        # AND every schema-bearing sidecar field (S2-A).
        digest = identity_digest(checkpoint, sidecar)
        sidecar["content_digest"] = digest
        sidecar_text = json.dumps(sidecar, indent=2, sort_keys=True, allow_nan=False)
        with open(os.path.join(checkpoint, SIDECAR_NAME), "w", encoding="utf-8") as fh:
            fh.write(sidecar_text + "\n")
        self.log.info(
            "fitted %s on %d row(s), %d step(s); checkpoint digest %s",
            _class_ref(type(model)),
            len(rows),
            steps,
            digest[:12],
        )
        signal = TransformerSignal(
            model,
            lambda batch: self.encode(batch, self.params),
            artifact_path=checkpoint,
            digest=digest,
            restored=False,
        )
        metrics = {
            "n_rows": len(rows),
            "steps": steps,
            "final_loss": final_loss,
            "seed": seed,
            "restored": 0.0,
            "artifact_digest": digest,
        }
        if val_rows:
            metrics["n_val_rows"] = len(val_rows)
        metrics.update(curve.summary())
        return {
            "signal": signal,
            "artifact_path": checkpoint,
            "metrics": metrics,
        }

    def _labels_of(self, rows, label):
        """The float label tensor, or a refusal naming the column."""
        values = []
        for i, row in enumerate(rows):
            value = _field_or_none(row, label)
            if isinstance(value, bool):
                value = float(value)
            if not _numeric(value):
                raise ValueError(
                    f"{self.key}: label column {label!r} is missing or "
                    f"non-numeric on row {i}: {value!r}"
                )
            values.append(float(value))
        import torch

        return torch.tensor(values, dtype=torch.float32)

    def run_load(self, ctx, inputs):
        """The ``mode="load"`` path: verify, restore, NEVER refit."""
        where = f"{self.key} (mode='load')"
        sidecar, digest = _read_sidecar(where, self.artifact)
        recorded = sidecar.get("node_class")
        if recorded != _class_ref(type(self)):
            raise ValueError(
                f"{where}: artifact {self.artifact!r} was fitted by "
                f"{recorded!r}, not by this node class "
                f"{_class_ref(type(self))!r} — refusing a checkpoint whose "
                "encode/build contract may differ"
            )
        fitted = sidecar.get("params", {})
        for knob, default in (("features", None), ("label", "label")):
            ours = self.params.get(knob, default)
            theirs = fitted.get(knob, default)
            if ours != theirs:
                raise ValueError(
                    f"{where}: artifact {self.artifact!r} was fitted with "
                    f"{knob}={theirs!r} but this node declares {ours!r} — a "
                    "pinned checkpoint answers only under the schema it was "
                    "fitted with"
                )
        model = _restore_model(where, self.artifact, sidecar)
        self.log.info(
            "restored %s from %s (digest %s)",
            sidecar.get("model_class"),
            self.artifact,
            digest[:12],
        )
        signal = TransformerSignal(
            model,
            lambda batch: self.encode(batch, self.params),
            artifact_path=self.artifact,
            digest=digest,
            restored=True,
        )
        return {
            "signal": signal,
            "artifact_path": self.artifact,
            "metrics": {
                "seed": sidecar.get("seed"),
                "restored": 1.0,
                "artifact_digest": digest,
            },
        }


# ---------------------------------------------------------------------------
# TransformerPredict — inference-only from a pinned checkpoint
# ---------------------------------------------------------------------------


class TransformerPredict(TrainableNode):
    """A signal restored from a pinned local checkpoint. It ALWAYS loads.

    The pin arrives either as the node-level ``artifact`` (with
    ``mode="load"``) or as the ``artifact_dir`` param — both are
    hash-material, and giving both DIFFERENT values refuses. No pin, no
    signal: there is nothing this node could honestly answer from, so it
    refuses by name rather than inventing a model. ``default_mode`` is
    ``"load"``, so an unset ``mode`` loads; ``mode="train"`` refuses —
    training is :class:`TransformerFit`'s job.

    Concrete as shipped: the sidecar records the model class to restore
    and the ``features`` the checkpoint was fitted with, so the default
    :meth:`encode` needs no subclassing. A project that overrode
    ``TransformerFit.encode`` overrides it here to match.
    """

    role = "signal"
    outputs = ("signal", "metrics")
    default_mode = "load"

    _PARAMS = ("artifact_dir",)

    def encode(self, rows, params):
        """``rows -> model kwargs`` under the SIDECAR's params (the
        checkpoint's own schema), mirroring ``TransformerFit.encode``."""
        return _default_encode(rows, params.get("features"))

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        artifact_dir = params.get("artifact_dir", "")
        if not isinstance(artifact_dir, str):
            problems.append(
                f"artifact_dir must be a checkpoint directory path, got "
                f"{artifact_dir!r}"
            )
        return problems

    @property
    def _where(self):
        """This node's self-description, quoted into every refusal it
        raises in EITHER mode — one wording, one place."""
        return f"{self.key} (transformers predict)"

    def run_train(self, ctx, inputs):
        raise ValueError(
            f"{self._where}: inference-only — mode='train' "
            "has nothing to fit here; fine-tune with a TransformerFit "
            "subclass and pin its artifact"
        )

    def run_load(self, ctx, inputs):
        where = self._where
        pinned = self.pinned_artifact(
            self.params.get("artifact_dir"),
            missing=(
                "no artifact pinned — this node always loads; give "
                "mode='load' with an artifact, or the artifact_dir param"
            ),
        )
        sidecar, digest = _read_sidecar(where, pinned)
        model = _restore_model(where, pinned, sidecar)
        fitted_params = sidecar.get("params", {})
        self.log.info(
            "restored %s from %s (digest %s)",
            sidecar.get("model_class"),
            pinned,
            digest[:12],
        )
        signal = TransformerSignal(
            model,
            lambda batch: self.encode(batch, fitted_params),
            artifact_path=pinned,
            digest=digest,
            restored=True,
        )
        return {
            "signal": signal,
            "metrics": {"restored": 1.0, "artifact_digest": digest},
        }


# ---------------------------------------------------------------------------
# The concrete reference subclass
# ---------------------------------------------------------------------------


class TinyTransformerFit(TransformerFit):
    """The reference fit: a tiny BERT-style regressor, from config, fresh.

    2 layers, 1 attention head, ``hidden_size == len(features)`` (the
    default ``encode`` hands the feature vector in as ``inputs_embeds``),
    ``num_labels=1`` so the HF head computes its own MSE regression loss.
    Instantiated from a config — no name, no download, no cache: the
    whole point is proving the WRAPPER contract, not the model.
    """

    def build_model(self, params):
        from transformers import BertConfig, BertForSequenceClassification

        width = len(params["features"])
        config = BertConfig(
            vocab_size=8,
            hidden_size=width,
            num_hidden_layers=2,
            num_attention_heads=1,
            intermediate_size=2 * width,
            max_position_embeddings=4,
            num_labels=1,
        )
        return BertForSequenceClassification(config)


class DeclaredTransformerFit(TransformerFit):
    """Fine-tune ANY HF architecture the DOCUMENT names. Registered as
    ``transformers-fit``.

    :class:`TinyTransformerFit` hardcodes one BERT shape in Python;
    this builds whatever ``config_class`` / ``model_class`` name, with
    ``config_params`` as the config's kwargs — so a different
    architecture is a config edit, matching the ``estimator`` /
    ``module`` grammar the sklearn and torch packs use.

    **Still zero hub access, structurally.** The model is constructed
    from a CONFIG OBJECT (``model_class(config_class(**config_params))``),
    which is the one HF path that reads no cache and opens no socket;
    there is no ``from_pretrained``, and no place to put a hub name. The
    pack's no-network property is therefore preserved by construction
    rather than by convention.
    """

    _PARAMS = TransformerFit._PARAMS + (
        "config_class",
        "config_params",
        "model_class",
    )

    @classmethod
    def validate_params(cls, params):
        problems = list(super().validate_params(params))
        for name, example in (
            ("config_class", "transformers.BertConfig"),
            ("model_class", "transformers.BertForSequenceClassification"),
        ):
            if name not in params:
                problems.append(
                    f"{name} is required — the class path to build FROM CONFIG, "
                    f"e.g. {example!r} (never a hub name; this pack never "
                    "downloads)"
                )
            else:
                problems += library_path_problems(name, params[name], example=example)
        config_params = params.get("config_params", {})
        if not isinstance(config_params, dict) or any(
            not isinstance(k, str) for k in config_params
        ):
            problems.append(
                f"config_params must be a dict of config kwargs, got "
                f"{config_params!r}"
            )
        return problems

    def build_model(self, params):
        config_cls = import_library_class(params["config_class"], "transformer config")
        model_cls = import_library_class(params["model_class"], "transformer model")
        kwargs = dict(params.get("config_params") or {})
        try:
            config = config_cls(**kwargs)
        except TypeError as exc:
            raise ValueError(
                f"{params['config_class']} rejected config_params ({exc}) — a "
                "mis-typed config knob is caught here, by the config, not "
                "silently ignored"
            ) from exc
        return model_cls(config)


# ---------------------------------------------------------------------------
# Registration — explicit and idempotent, never at import
# ---------------------------------------------------------------------------

#: The pack's registrable kinds: CONCRETE classes only. ``TransformerFit``
#: is abstract and the registry refuses abstract classes by construction.
NODE_KINDS = (
    ("transformers-fit", DeclaredTransformerFit),
    ("transformers-tiny-fit", TinyTransformerFit),
    ("transformers-predict", TransformerPredict),
)


def register(registry=None) -> None:
    """Claim the ``transformers-*`` kind names in ``registry`` (default
    :data:`~dskit.pipeline.node.DEFAULT_NODE_KINDS`).

    Idempotent: a name already present is SKIPPED, never shadowed. Import
    paths (``"dskit.pipeline.libs.transformers:TinyTransformerFit"``)
    work with no registration at all — the shipped example uses exactly
    those.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls)
