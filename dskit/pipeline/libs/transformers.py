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
concrete kinds, and :func:`register` is explicit and idempotent, never
run at import (see ``libs/__init__``).

**The pretrained doorway (ADR-0082 / ADR-0083).** Weights never arrive by
hub name — a name is not content-addressed, and a run whose weights can
change under an unchanged document hash is the defect the identity hash
exists to prevent. They are ACQUIRED: the ``huggingface`` connector in
``dskit.onboarding`` snapshots one repository at one commit, WORM, with a
Merkle manifest, and a document pins THAT snapshot by its manifest hash
(the ``root`` / ``snapshot`` / ``stream`` knobs of :data:`PIN_PARAMS`).
The read seam re-hashes the snapshot before a byte of it is trusted, and
every load is ``local_files_only=True`` — this pack still opens no socket.
Three kinds read the pin: :class:`PretrainedEncode` (``transformers-encode``,
role ``tensor``) turns a text field into pooled embedding columns beside
the record fields a document carries; :class:`PretrainedClassify`
(``transformers-classify``) turns it into one probability column per
``id2label`` entry — the sentiment-score case; :class:`PretrainedForecast`
(``transformers-forecast``, role ``signal``, always loads) restores a
zero-shot forecaster and answers ``predict(row)`` with the ``horizon``-th
step over the row's ordered ``features`` — the baseline a bespoke model
must beat. That last one makes a NARROW claim: its default hooks fit the
models whose forward takes ``past_values`` alone and returns
``prediction_outputs`` — PatchTST, PatchTSMixer — and one probe forward at
load refuses anything else BY NAME rather than scoring it wrong.
``build_model`` / ``build_tokenizer`` / ``vectors`` / ``column_names`` /
``context_length_of`` / ``forecast`` are the subclass seam: a Chronos,
TimesFM or Moirai wrapper supplies them, never a registry of per-model
classes. Nothing loads on trust: ``missing_keys`` is fatal (a randomly
initialized part is not the pinned model), weights come from safetensors
only, and a snapshot with no tokenizer artifacts refuses instead of
embedding every text as ``[UNK]``.

Import cost: stdlib + ``dskit.pipeline`` only.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import re
import time
from abc import abstractmethod
from collections.abc import Mapping

from dskit.pipeline.base import (
    import_library_class,
    import_ref,
    library_path_problems,
)
from dskit.pipeline.kinds_stats import _check_int, _reject_unknown
from dskit.pipeline.libs.numpy import narrow_params
from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    Node,
    TrainableNode,
    check_int_param,
    class_ref,
    reject_unknown_params,
)
from dskit.pipeline.records import number_ok
from dskit.pipeline.trainlog import (
    DEFAULT_MAX_LINES,
    TrainingCurve,
    probability_metrics,
)

__all__ = [
    "CLASSIFY_ACTIVATIONS",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CARRY_FIELDS",
    "DEFAULT_CLASSIFY_PREFIX",
    "DEFAULT_ENCODE_PREFIX",
    "DEFAULT_HORIZON",
    "DEFAULT_MAX_LENGTH",
    "DEFAULT_POOLING",
    "DEFAULT_REQUIRE_FIELDS",
    "DEFAULT_SNAPSHOT_STREAM",
    "NODE_KINDS",
    "PIN_PARAMS",
    "POOLERS",
    "POOLINGS",
    "SIDECAR_NAME",
    "TOKENIZER_FILES",
    "DeclaredTransformerFit",
    "ForecastSignal",
    "PretrainedClassify",
    "PretrainedEncode",
    "PretrainedForecast",
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
            "node_class": class_ref(type(self)),
            "model_class": class_ref(type(model)),
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
            class_ref(type(model)),
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
        if recorded != class_ref(type(self)):
            raise ValueError(
                f"{where}: artifact {self.artifact!r} was fitted by "
                f"{recorded!r}, not by this node class "
                f"{class_ref(type(self))!r} — refusing a checkpoint whose "
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
        """This node's self-description — the key PLUS the pack locator.

        Quoted into the refusals this PACK raises: ``mode='train'``
        below, and the sidecar/restore checks it is handed to. NOT into
        the artifact-PIN refusals (nothing pinned, an empty pin, a
        node-level pin contradicting ``artifact_dir``): ADR-0038 moved
        those to :meth:`~dskit.pipeline.node.Node.pinned_artifact`, a
        tier-1 service that can only prefix the bare ``self.key`` —
        stdlib-only core never calls a tier-2 wrapper, and the pack's
        share of that wording is the ``missing`` text alone. So this node
        names itself two ways by design: one wording per refusal SOURCE,
        not one per node.
        """
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
# The pretrained doorway (ADR-0082 / ADR-0083)
# ---------------------------------------------------------------------------

#: The pin every pretrained kind declares: the onboarding root the model was
#: acquired into, the snapshot's 64-hex manifest hash, and the FILE stream
#: the connector acquired it under.
PIN_PARAMS = ("root", "snapshot", "stream")

#: The ``huggingface`` connector's stream name; pinned equal to
#: ``dskit.onboarding.libs.huggingface.SNAPSHOT_STREAM`` by test, because
#: this pack may not import that module.
DEFAULT_SNAPSHOT_STREAM = "snapshot"

#: ONE name per default, read by ``validate_params`` and the accessors alike.
DEFAULT_POOLING = "mean"
DEFAULT_ENCODE_PREFIX = "emb_"
DEFAULT_CLASSIFY_PREFIX = "p_"
DEFAULT_MAX_LENGTH = 128
DEFAULT_BATCH_SIZE = 32
DEFAULT_HORIZON = 1
DEFAULT_CARRY_FIELDS = ()
DEFAULT_REQUIRE_FIELDS = ()

#: The filenames a tokenizer can be spelled as, as ``fnmatch`` patterns. A
#: snapshot carrying NONE of them is refused: ``AutoTokenizer`` answers a
#: specials-only vocabulary for a config+weights directory rather than
#: raising, and every text would then embed as ``[UNK]``.
TOKENIZER_FILES = (
    "tokenizer.json", "tokenizer_config.json", "vocab.*", "spiece.model",
    "merges.txt",
)

#: How many missing weight names a refusal spells out before summarizing.
_MAX_NAMED_KEYS = 8

# \Z, not $ — $ forgives a trailing newline (ADR-0020).
_SNAPSHOT_HASH = re.compile(r"^[0-9a-f]{64}\Z")
#: The stream is a directory segment on the onboarding side: the SAME rule as
#: ``dskit.onboarding.base._SEGMENT``, pinned equal by test (the pack may not
#: import that module).
_STREAM_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*\Z")


def _mean_pool(hidden, mask):
    """Average the hidden states over the unmasked tokens."""
    weights = mask.unsqueeze(-1).to(hidden.dtype)
    return (hidden * weights).sum(dim=1) / weights.sum(dim=1).clamp(min=1.0)


def _cls_pool(hidden, mask):
    """Take the first token's hidden state (BERT's ``[CLS]``)."""
    return hidden[:, 0, :]


def _max_pool(hidden, mask):
    """Take the per-dimension maximum over the unmasked tokens."""
    masked = hidden.masked_fill(mask.unsqueeze(-1) == 0, float("-inf"))
    return masked.max(dim=1).values


#: The pooling table — a knob value names a function, never an ``if`` chain.
POOLERS = {"mean": _mean_pool, "cls": _cls_pool, "max": _max_pool}

#: The closed ``pooling`` vocabulary, derived from the table.
POOLINGS = tuple(POOLERS)


def _softmax(logits):
    """One class wins: the logits of a single-label head as probabilities."""
    import torch

    return torch.softmax(logits, dim=-1)


def _sigmoid(logits):
    """Each class independently: the logits of a multi-label head as probabilities."""
    import torch

    return torch.sigmoid(logits)


#: The activation table — a classification head's ``problem_type`` names the
#: function that turns its logits into columns, never an ``if`` chain. A type
#: absent from the table (``regression``, or anything a later library adds)
#: refuses by name: reporting a regression output as a probability would be a
#: silent lie in every downstream column.
CLASSIFY_ACTIVATIONS = {
    None: _softmax,
    "single_label_classification": _softmax,
    "multi_label_classification": _sigmoid,
}


def _parameter_dtype(model):
    """The dtype the model's own parameters carry, or ``None`` when it has none."""
    for parameter in model.parameters():
        return parameter.dtype
    return None


def _tokenizer_file_problems(files_dir):
    """One problem when the snapshot names no tokenizer file at all, else none."""
    names = sorted(os.listdir(files_dir)) if os.path.isdir(files_dir) else []
    if any(fnmatch.fnmatch(name, pattern)
           for name in names for pattern in TOKENIZER_FILES):
        return []
    return [
        f"it carries none of {list(TOKENIZER_FILES)} — the acquired files are "
        f"{names}"
    ]


def _tokenizer_problems(tokenizer):
    """Problems with a loaded tokenizer: a specials-only vocabulary, no padding."""
    problems = []
    specials = getattr(tokenizer, "all_special_ids", None) or ()
    if len(tokenizer) <= len(specials):
        problems.append(
            f"the tokenizer knows {len(tokenizer)} token(s), all {len(specials)} "
            "of them special — the library synthesized a vocabulary the snapshot "
            "never carried, and every text would embed as [UNK]"
        )
    if getattr(tokenizer, "pad_token", None) is None:
        problems.append(
            "the tokenizer has no pad_token, so a batch cannot be padded — "
            "subclass build_tokenizer to set one"
        )
    return problems


def _pin_problems(params):
    """List problems with the snapshot pin knobs, empty when none."""
    problems = []
    root = params.get("root")
    if not isinstance(root, str) or not root:
        problems.append(
            "root is required — the onboarding root the snapshot was acquired "
            f"into, got {root!r}"
        )
    snapshot = params.get("snapshot")
    if not isinstance(snapshot, str) or not _SNAPSHOT_HASH.match(snapshot):
        problems.append(
            "snapshot is required — the 64-hex manifest hash of the acquired "
            f"model (never a hub name, never a path), got {snapshot!r}"
        )
    stream = params.get("stream", DEFAULT_SNAPSHOT_STREAM)
    if not isinstance(stream, str) or not _STREAM_NAME.match(stream):
        problems.append(
            "stream must be the connector's stream name (lowercase/digits/_/-), "
            f"got {stream!r}"
        )
    return problems


class _SnapshotPin:
    """The pin's vocabulary and its one-time resolution, shared by the pretrained kinds.

    A mixin, because the kinds that read a pinned snapshot sit on two
    different bases (:class:`~dskit.pipeline.node.Node` for the row
    makers, :class:`~dskit.pipeline.node.TrainableNode` for the signal)
    and the three accessors plus the memoized resolve must be ONE text.
    """

    #: The resolved FILE directories — per INSTANCE, keyed by the PIN that
    #: was resolved. One key, not one slot: a node may be asked for another
    #: snapshot (a restated node-level artifact), and a memo that ignored its
    #: argument would hand back the first snapshot's directory forever.
    _files_dirs = None

    def root(self):
        """Name the onboarding root the snapshot was acquired into (str)."""
        return self.params["root"]

    def snapshot(self):
        """Give the snapshot's manifest hash — the pin (str)."""
        return self.params["snapshot"]

    def stream(self):
        """Name the FILE stream the snapshot was acquired under (str)."""
        return self.params.get("stream", DEFAULT_SNAPSHOT_STREAM)

    def snapshot_dir(self, pin=None):
        """Answer the verified FILE directory behind the pin, once per instance.

        Parameters
        ----------
        pin : str or None
            The manifest hash to resolve; ``None`` means :meth:`snapshot`.

        Returns
        -------
        str
            The snapshot's ``payload/<stream>/`` directory, re-hashed clean.

        Raises
        ------
        ValueError
            Naming the node and every problem the read seam reported.
        """
        key = self.snapshot() if pin is None else pin
        if self._files_dirs is None:
            self._files_dirs = {}
        if key not in self._files_dirs:
            # Imported HERE: the read seam is the one sibling a pack may name,
            # and only at function depth (ADR-0077 / ADR-0083).
            from dskit.onboarding.observations import verified_payload_dir

            try:
                self._files_dirs[key] = verified_payload_dir(
                    self.root(), key, self.stream()
                )
            except Exception as exc:
                errors = getattr(exc, "errors", None)
                if errors is None:  # not the seam's typed refusal: a crash stays one
                    raise
                raise ValueError(
                    f"{self.key}: the pinned snapshot cannot be read — " + "; ".join(errors)
                ) from exc
        return self._files_dirs[key]

    def refuse_unreadable(self, what, exc):
        """Build the refusal a library ``OSError`` over the snapshot deserves.

        ONE text for every kind: a snapshot that does not carry what
        ``from_pretrained`` needs is a pin problem, and a bare ``OSError``
        naming a temporary directory tells nobody which node or which
        acquisition to look at.

        Parameters
        ----------
        what : str
            What was being loaded — it opens the message ("the tokenizer",
            ``"AutoModel"``).
        exc : OSError
            The library's own complaint, quoted verbatim.

        Returns
        -------
        ValueError
            The refusal to raise, naming the node key and the pin.
        """
        return ValueError(
            f"{self.key}: {what} cannot be loaded from the pinned snapshot "
            f"{self.snapshot()} — {exc}"
        )

    def load_pretrained(self, model_cls, files_dir, **kwargs):
        """Load ``model_cls`` from the verified snapshot, whole or not at all.

        ``output_loading_info=True`` is the point: ``from_pretrained``
        RANDOMLY INITIALIZES every weight the checkpoint does not carry and
        says so only in a log line, so an encoder snapshot loaded as a
        classifier answers confident probabilities from an untrained head.
        A non-empty ``missing_keys`` refuses by name. Unused weights
        (``unexpected_keys``) are lawful — a head this kind does not use is
        still the pinned model — and are logged, not refused.
        ``use_safetensors=True`` is the other half: a ``.bin`` checkpoint is
        a pickle, and a verified snapshot is still not a reason to run one.

        Parameters
        ----------
        model_cls : type
            The library class (or auto class) to restore.
        files_dir : str
            The snapshot's verified FILE payload directory.
        **kwargs
            Passed through to ``from_pretrained``.

        Returns
        -------
        transformers.PreTrainedModel
            The restored model, every weight of it from the snapshot.

        Raises
        ------
        ValueError
            When the snapshot carries no safetensors weights for the class
            (naming the node and the pin), or carries only some of them.
        """
        try:
            model, info = model_cls.from_pretrained(
                files_dir, local_files_only=True, use_safetensors=True,
                output_loading_info=True, **kwargs,
            )
        except OSError as exc:
            raise self.refuse_unreadable(model_cls.__name__, exc) from exc
        missing = sorted(info.get("missing_keys") or ())
        if missing:
            named = ", ".join(missing[:_MAX_NAMED_KEYS])
            if len(missing) > _MAX_NAMED_KEYS:
                named += f", and {len(missing) - _MAX_NAMED_KEYS} more"
            raise ValueError(
                f"{self.key}: the pinned snapshot {self.snapshot()} does not "
                f"carry {len(missing)} weight(s) {model_cls.__name__} needs "
                f"({named}) — a randomly initialized part is not the pinned "
                "model; acquire the checkpoint this head was trained into, or "
                "subclass build_model for the class the snapshot really holds"
            )
        unexpected = sorted(info.get("unexpected_keys") or ())
        self.log.info(
            "loaded %s from snapshot %s (%d weight(s) in the checkpoint unused "
            "by this class)", model_cls.__name__, self.snapshot()[:12],
            len(unexpected),
        )
        return model


def _unique_names(value):
    """Say whether ``value`` is a non-empty list of distinct non-empty strings."""
    return (
        isinstance(value, (list, tuple))
        and bool(value)
        and all(isinstance(v, str) and v for v in value)
        and len(set(value)) == len(value)
    )


class PretrainedEncode(_SnapshotPin, Node):
    """Text records → one feature row each, from a pinned pretrained encoder.

    Kind ``transformers-encode``, role ``tensor``, outputs ``rows`` and
    ``metrics`` — the :class:`~dskit.pipeline.libs.numpy.ArrayFeatures`
    shape, so the rows feed a fit or a feature selector unchanged. The
    model is the acquired snapshot the pin names (ADR-0083): located by
    manifest hash, re-verified, loaded with ``local_files_only=True`` and
    ``use_safetensors=True``, and refused when it carries no usable
    tokenizer or leaves a weight to random initialization. A record whose
    text field is missing or not a non-empty string, or which lacks one of
    ``require_fields``, yields no row and is counted in ``n_dropped``.

    Parameters
    ----------
    params : dict
        ``root`` (str, REQUIRED) — the onboarding root; ``snapshot`` (str,
        REQUIRED) — the snapshot's 64-hex manifest hash; ``stream`` (str,
        default ``"snapshot"``) — the FILE stream; ``text_field`` (str,
        REQUIRED) — the record field holding the text; ``carry_fields``
        (list of str, default ``[]``) — record fields copied onto every
        row; ``require_fields`` (list of str, default ``[]``) — record
        fields that must be present and non-null or the record yields no
        row; ``prefix`` (str, default ``"emb_"``) — the column prefix, one
        column per hidden dimension; ``pooling`` (``"mean"`` | ``"cls"`` |
        ``"max"``, default ``"mean"``); ``max_length`` (int >= 1, default
        128) — tokens kept per text, special tokens included: the library
        never truncates below them; ``batch_size`` (int >= 1, default 32).

    Examples
    --------
    Embed headlines, carrying the instant and the symbol onto each row::

        node = PretrainedEncode("emb", {
            "root": "./onboarding_root",
            "snapshot": "3f2a…64 hex…",
            "text_field": "headline",
            "carry_fields": ["asof_ms", "symbol"],
        })
        out = node.run(ctx, {"records": records})
        # -> {"rows": [{"asof_ms": ..., "symbol": ..., "emb_0": ..., ...}],
        #     "metrics": {"n_rows": ..., "n_records": ..., "n_dropped": ...,
        #                 "n_columns": ...}}
    """

    role = "tensor"
    outputs = ("rows", "metrics")

    #: The column prefix an unset ``prefix`` means for THIS class.
    default_prefix = DEFAULT_ENCODE_PREFIX

    _PARAMS = PIN_PARAMS + (
        "text_field",
        "carry_fields",
        "require_fields",
        "prefix",
        "pooling",
        "max_length",
        "batch_size",
    )

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown knob, missing required knob, or
            unusable value.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        problems.extend(_pin_problems(params))
        text_field = params.get("text_field")
        if not isinstance(text_field, str) or not text_field:
            problems.append(
                "text_field is required — the record field holding the text "
                f"to encode, got {text_field!r}"
            )
        for knob, default in (("carry_fields", DEFAULT_CARRY_FIELDS),
                              ("require_fields", DEFAULT_REQUIRE_FIELDS)):
            value = params.get(knob, default)
            if not isinstance(value, (list, tuple)) or any(
                not isinstance(f, str) or not f for f in value
            ):
                problems.append(f"{knob} must be a list of field names, got {value!r}")
            elif len(set(value)) != len(value):
                problems.append(f"{knob} repeats a field: {list(value)!r}")
        prefix = params.get("prefix", cls.default_prefix)
        if not isinstance(prefix, str):
            problems.append(f"prefix must be a string, got {prefix!r}")
        if "pooling" in cls._PARAMS:
            pooling = params.get("pooling", DEFAULT_POOLING)
            if pooling not in POOLINGS:
                problems.append(f"pooling must be one of {list(POOLINGS)}, got {pooling!r}")
        check_int_param(
            problems, "max_length", params.get("max_length", DEFAULT_MAX_LENGTH), ge=1
        )
        check_int_param(
            problems, "batch_size", params.get("batch_size", DEFAULT_BATCH_SIZE), ge=1
        )
        return problems

    def validate_inputs(self, inputs):
        """List problems with ``inputs``: ``records`` must be a list.

        Parameters
        ----------
        inputs : dict
            The materialized inputs.

        Returns
        -------
        list of str
            One problem when ``records`` is not a list (a one-shot iterable
            would be consumed by validation); empty otherwise.
        """
        records = inputs.get("records")
        if not isinstance(records, list):
            return [
                "records must be a list of records (a one-shot iterable would "
                f"be consumed by validation), got {records!r}"
            ]
        return []

    # -- the knob accessors -------------------------------------------------

    def text_field(self):
        """Name the record field holding the text (str)."""
        return self.params["text_field"]

    def carry_fields(self):
        """Name the record fields copied onto every row (tuple of str)."""
        return tuple(self.params.get("carry_fields", DEFAULT_CARRY_FIELDS))

    def require_fields(self):
        """Name the record fields a row cannot be made without (tuple of str)."""
        return tuple(self.params.get("require_fields", DEFAULT_REQUIRE_FIELDS))

    def prefix(self):
        """Give the column prefix (str)."""
        return self.params.get("prefix", type(self).default_prefix)

    def pooling(self):
        """Name the pooling — one of :data:`POOLINGS` (str)."""
        return self.params.get("pooling", DEFAULT_POOLING)

    def max_length(self):
        """Give the tokens kept per text, special tokens included (int).

        ``int``, not the declared value: ``check_int_param`` accepts an
        integral float by design (a metric wired into a knob is a float),
        and the library wants a genuine int.
        """
        return int(self.params.get("max_length", DEFAULT_MAX_LENGTH))

    def batch_size(self):
        """Give the texts encoded per forward pass (int, integral float coerced)."""
        return int(self.params.get("batch_size", DEFAULT_BATCH_SIZE))

    # -- the subclass seam -----------------------------------------------------

    def build_model(self, files_dir):
        """Load the model from the verified snapshot directory.

        Parameters
        ----------
        files_dir : str
            The snapshot's FILE payload directory.

        Returns
        -------
        transformers.PreTrainedModel
            The base encoder (``AutoModel``) — override for another head.
        """
        from transformers import AutoModel

        return self.load_pretrained(AutoModel, files_dir)

    def build_tokenizer(self, files_dir):
        """Load the tokenizer from the verified snapshot directory.

        Parameters
        ----------
        files_dir : str
            The snapshot's FILE payload directory.

        Returns
        -------
        transformers.PreTrainedTokenizerBase
            ``AutoTokenizer`` over the snapshot — override for a custom one.
        """
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(files_dir, local_files_only=True)

    def checked_tokenizer(self, files_dir):
        """Build the tokenizer through the hook and refuse one that cannot tokenize.

        ``AutoTokenizer.from_pretrained`` does not raise over a snapshot
        that carries no vocabulary: it SYNTHESIZES one holding nothing but
        special tokens, every text becomes ``[UNK]``, and the run produces
        finite, plausible, meaningless columns. Three questions catch that
        — does the snapshot name a tokenizer file at all, does the loaded
        tokenizer know a token that is not special, and can it pad a batch.

        Parameters
        ----------
        files_dir : str
            The snapshot's verified FILE payload directory.

        Returns
        -------
        transformers.PreTrainedTokenizerBase
            The tokenizer :meth:`build_tokenizer` answered.

        Raises
        ------
        ValueError
            Naming the node, the pin, and every problem found.
        """
        problems = _tokenizer_file_problems(files_dir)
        try:
            tokenizer = self.build_tokenizer(files_dir)
        except OSError as exc:
            raise self.refuse_unreadable("the tokenizer", exc) from exc
        problems.extend(_tokenizer_problems(tokenizer))
        if problems:
            raise ValueError(
                f"{self.key}: the pinned snapshot {self.snapshot()} carries no "
                "usable tokenizer — " + "; ".join(problems)
            )
        return tokenizer

    def vectors(self, outputs, encoded):
        """Reduce one batch's model outputs to one vector per text.

        Parameters
        ----------
        outputs : transformers.utils.ModelOutput
            The forward pass over ``encoded``.
        encoded : Mapping
            The tokenizer's batch (``attention_mask`` is read when present).

        Returns
        -------
        torch.Tensor
            Shape ``[batch, width]`` — the pooled last hidden state.
        """
        hidden = outputs.last_hidden_state
        mask = encoded.get("attention_mask")
        if mask is None:
            import torch

            mask = torch.ones(hidden.shape[:2], dtype=hidden.dtype)
        return POOLERS[self.pooling()](hidden, mask)

    def width_of(self, model):
        """Give the vector width when no record produced one (int)."""
        return int(getattr(model.config, "hidden_size", 0))

    def column_names(self, model, width):
        """Name the ``width`` feature columns, in vector order.

        Parameters
        ----------
        model : transformers.PreTrainedModel
            The loaded model, for a subclass that names columns from it.
        width : int
            The vector width.

        Returns
        -------
        list of str
            ``<prefix><i>`` for ``i`` in ``range(width)``.
        """
        return [f"{self.prefix()}{i}" for i in range(width)]

    # -- the run -------------------------------------------------------------------

    def run(self, ctx, inputs):
        """Encode every record carrying text into one feature row.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused — the model comes from the pin.
        inputs : dict
            ``records``, the stream to encode.

        Returns
        -------
        dict
            ``{"rows": [...], "metrics": {"n_rows", "n_records",
            "n_dropped", "n_columns"}}``.

        Raises
        ------
        ValueError
            When the pinned snapshot cannot be read, does not carry the
            whole model or a usable tokenizer, names no width for an empty
            record stream, or a feature column would take a carried
            field's name.
        """
        import torch

        records = inputs["records"]
        files_dir = self.snapshot_dir()
        _quiet_transformers()
        # The MODEL first: a snapshot that is nothing but a config fails
        # here, with the library's own message, before the tokenizer guard
        # reports the vocabulary such a snapshot also lacks.
        model = self.build_model(files_dir)
        model.eval()
        tokenizer = self.checked_tokenizer(files_dir)
        text_field = self.text_field()
        required = self.require_fields()
        texts, indices, untexted = [], [], 0
        for idx, record in enumerate(records):
            text = _field_or_none(record, text_field)
            if not isinstance(text, str) or not text:
                untexted += 1
                continue
            if any(_field_or_none(record, name) is None for name in required):
                continue
            texts.append(text)
            indices.append(idx)
        vectors = []
        step = self.batch_size()
        with torch.no_grad():
            for start in range(0, len(texts), step):
                encoded = tokenizer(
                    texts[start:start + step],
                    padding=True,
                    truncation=True,
                    max_length=self.max_length(),
                    return_tensors="pt",
                )
                # float32 before the rows are built: a bfloat16 model pools
                # in bfloat16, and a row of ~3-digit floats is not a feature.
                pooled = self.vectors(model(**encoded), encoded).to(torch.float32)
                vectors.extend(pooled.tolist())
        width = len(vectors[0]) if vectors else self.width_of(model)
        if not width:
            raise ValueError(
                f"{self.key}: no record produced a vector and the model's config "
                "names no hidden_size/num_labels — the row width is unknowable; "
                "override width_of for a family that declares its width elsewhere"
            )
        columns = self.column_names(model, width)
        if not _unique_names(columns) or len(columns) != width:
            raise ValueError(
                f"{self.key}: column_names answered {columns!r} for width {width} — "
                "one distinct non-empty name per dimension is the contract"
            )
        carry = self.carry_fields()
        clash = sorted(set(columns) & set(carry))
        if clash:
            raise ValueError(
                f"{self.key}: feature column(s) {clash} take a carried field's name "
                "— a column may not clobber the row's identity"
            )
        rows = []
        for idx, vector in zip(indices, vectors):
            row = {name: _field_or_none(records[idx], name) for name in carry}
            row.update(zip(columns, (float(v) for v in vector)))
            rows.append(row)
        metrics = {
            "n_rows": len(rows),
            "n_records": len(records),
            "n_dropped": len(records) - len(rows),
            "n_columns": len(columns),
        }
        self.log.info(
            "encoded %d row(s) x %d column(s) from %d record(s) "
            "(%d without a text string, %d missing a required field)",
            metrics["n_rows"], metrics["n_columns"], metrics["n_records"],
            untexted, metrics["n_dropped"] - untexted,
        )
        return {"rows": rows, "metrics": metrics}


class PretrainedClassify(PretrainedEncode):
    """Text records → one probability column per class, from a pinned classifier.

    Kind ``transformers-classify``; :class:`PretrainedEncode` with a
    sequence-classification head: the head's own ``problem_type`` chooses
    the activation through :data:`CLASSIFY_ACTIVATIONS` (softmax for a
    single-label head, sigmoid for a multi-label one), columns named
    ``<prefix><label>`` from the model's own ``id2label`` — the sentiment
    score a bar stream joins onto. ``pooling`` is narrowed away (a
    classification head pools for itself). A head with one label, or one
    whose ``problem_type`` is not in the table (``regression``), refuses by
    name at load: a column called a probability must be one.

    Parameters
    ----------
    params : dict
        :class:`PretrainedEncode`'s knobs minus ``pooling``; ``prefix``
        defaults to ``"p_"``.

    Examples
    --------
    Score headlines with a three-way sentiment model::

        node = PretrainedClassify("sentiment", {
            "root": "./onboarding_root",
            "snapshot": "9c1e…64 hex…",
            "text_field": "headline",
            "carry_fields": ["asof_ms", "symbol"],
        })
        out = node.run(ctx, {"records": records})
        # -> rows carry p_negative / p_neutral / p_positive beside the carried fields
    """

    default_prefix = DEFAULT_CLASSIFY_PREFIX

    _PARAMS = narrow_params(PretrainedEncode._PARAMS, "pooling")

    #: The activation the loaded head's ``problem_type`` chose — a strategy
    #: object, set by :meth:`build_model` before a batch is ever encoded, so
    #: an unreportable head refuses at load and not at the first row.
    _activation = None

    def build_model(self, files_dir):
        """Load the sequence-classification model from the snapshot.

        Parameters
        ----------
        files_dir : str
            The snapshot's FILE payload directory.

        Returns
        -------
        transformers.PreTrainedModel
            ``AutoModelForSequenceClassification`` over the snapshot.

        Raises
        ------
        ValueError
            When the head names fewer than two labels, or its
            ``problem_type`` has no activation in the table.
        """
        from transformers import AutoModelForSequenceClassification

        model = self.load_pretrained(AutoModelForSequenceClassification, files_dir)
        labels = getattr(model.config, "num_labels", None)
        if not isinstance(labels, int) or labels < 2:
            raise ValueError(
                f"{self.key}: the pinned snapshot {self.snapshot()} has "
                f"num_labels {labels!r} — a probability column needs at least "
                "two classes to be probable between; a one-output head is a "
                "regressor, not a classifier"
            )
        # Chosen here and not per batch: an empty record stream would
        # otherwise report columns from a head nothing can activate.
        self._activation = self.activation(model.config)
        return model

    def activation(self, config):
        """Name the activation the head's ``problem_type`` calls for.

        Parameters
        ----------
        config : transformers.PretrainedConfig
            The loaded head's config; ``problem_type`` is read.

        Returns
        -------
        callable
            The :data:`CLASSIFY_ACTIVATIONS` entry — ``logits`` in, a
            ``[batch, n_labels]`` probability tensor out.

        Raises
        ------
        ValueError
            When ``problem_type`` has no entry, naming the node and the type.
        """
        problem = getattr(config, "problem_type", None)
        try:
            return CLASSIFY_ACTIVATIONS[problem]
        except KeyError:
            raise ValueError(
                f"{self.key}: the pinned snapshot {self.snapshot()} declares "
                f"problem_type {problem!r}, which names no probability — this "
                f"kind reports {sorted(k for k in CLASSIFY_ACTIVATIONS if k)}; "
                "a regression head belongs behind a subclass supplying vectors"
            ) from None

    def vectors(self, outputs, encoded):
        """Turn one batch's logits into class probabilities.

        Parameters
        ----------
        outputs : transformers.utils.ModelOutput
            The forward pass; ``logits`` is read.
        encoded : Mapping
            The tokenizer's batch; unused — the head pools for itself.

        Returns
        -------
        torch.Tensor
            Shape ``[batch, n_labels]`` — softmax over the classes for a
            single-label head, sigmoid per class for a multi-label one.

        Raises
        ------
        ValueError
            When no activation was chosen, naming the node.
        """
        if self._activation is None:
            raise ValueError(
                f"{self.key}: no activation was chosen for this head — "
                "build_model picks one from the config's problem_type, so a "
                "subclass that overrides build_model must set _activation "
                "(or override vectors)"
            )
        return self._activation(outputs.logits)

    def width_of(self, model):
        """Give the class count when no record produced a vector (int)."""
        return int(getattr(model.config, "num_labels", 0))

    def column_names(self, model, width):
        """Name one column per class from the model's ``id2label``.

        Parameters
        ----------
        model : transformers.PreTrainedModel
            The loaded classifier.
        width : int
            The probability vector's width — must equal the label count.

        Returns
        -------
        list of str
            ``<prefix><label>`` in label-id order.

        Raises
        ------
        ValueError
            When ``id2label`` does not name exactly ``width`` string labels.
        """
        id2label = getattr(model.config, "id2label", None) or {}
        try:
            labels = [label for _, label in sorted(id2label.items(), key=lambda kv: int(kv[0]))]
        except (TypeError, ValueError):
            labels = []
        if len(labels) != width or not _unique_names(labels):
            raise ValueError(
                f"{self.key}: the model's id2label {id2label!r} does not name "
                f"{width} distinct class(es) — refusing to invent column names"
            )
        return [f"{self.prefix()}{label}" for label in labels]


class ForecastSignal:
    """Per-row zero-shot forecasts over a restored time-series model.

    ``predict(row)`` reads the ordered ``features`` (oldest first) as the
    context, asks the forecaster for its steps, and returns the
    ``horizon``-th as a float — or ``None`` when any feature is missing or
    not a finite number, which the owned ``validate`` kind reads as no
    coverage. ONE FORWARD PER ROW: there is no batching here, so a signal
    over a long row stream costs one model call per row — the price of a
    per-row seam every scoring kind can call. The context is built in the
    model's own parameter dtype (a bfloat16 checkpoint never sees a float32
    tensor). Provenance rides on the object as on
    :class:`TransformerSignal`: ``artifact_path`` is the verified snapshot
    directory, ``digest`` its manifest hash, ``restored`` always True (a
    zero-shot model is only ever restored).

    Parameters
    ----------
    model : object
        The restored forecaster, in eval mode.
    features : tuple of str
        The context columns, oldest first.
    horizon : int
        Which forecast step (1-based) ``predict`` answers.
    forecast : callable
        ``(model, context) -> torch.Tensor [batch, steps]`` — the node's
        :meth:`PretrainedForecast.forecast` hook.
    artifact_path : str
        The verified snapshot directory.
    digest : str
        The snapshot's manifest hash.

    Examples
    --------
    Restore through the node and ask for one row::

        out = PretrainedForecast("fc", params).run(ctx, {})
        out["signal"].predict({"x0": 0.1, "x1": -0.2, "x2": 0.05})  # 0.0123
    """

    __slots__ = (
        "_dtype", "_features", "_forecast", "_horizon", "_model", "artifact_path",
        "digest", "restored",
    )

    def __init__(self, model, features, horizon, forecast, *, artifact_path, digest):
        self._model = model
        self._features = tuple(features)
        self._horizon = horizon
        self._forecast = forecast
        self._dtype = _parameter_dtype(model)
        self.artifact_path = artifact_path
        self.digest = digest
        self.restored = True

    def predict(self, row):
        """Forecast one row's ``horizon``-th step, or decline it.

        Parameters
        ----------
        row : Mapping or object
            A row carrying the context features by key or attribute.

        Returns
        -------
        float or None
            The forecast at the horizon step; ``None`` when a feature is
            missing or not a finite number (no coverage).

        Raises
        ------
        ValueError
            When the model answers fewer steps than the horizon.
        """
        values = []
        for name in self._features:
            value = _field_or_none(row, name)
            if not number_ok(value):
                return None
            values.append(float(value))
        import torch

        dtype = torch.float32 if self._dtype is None else self._dtype
        context = torch.tensor(values, dtype=dtype).reshape(1, len(values), 1)
        with torch.no_grad():
            steps = self._forecast(self._model, context)
        flat = steps.reshape(steps.shape[0], -1)
        if flat.shape[1] < self._horizon:
            raise ValueError(
                f"the forecaster answered {flat.shape[1]} step(s), fewer than "
                f"horizon {self._horizon}"
            )
        return float(flat[0, self._horizon - 1])


class PretrainedForecast(_SnapshotPin, TrainableNode):
    """A zero-shot time-series forecaster restored from a pinned snapshot.

    Kind ``transformers-forecast``, role ``signal``; it ALWAYS loads
    (``default_mode = "load"``) — a zero-shot model has nothing to fit, so
    ``mode="train"`` refuses by name. The snapshot is pinned by manifest
    hash; a node-level ``artifact`` may RESTATE that hash under
    ``mode="load"`` and never replace it (a path is not a pin). The model
    class is the snapshot's own ``config.architectures[0]`` as
    ``transformers`` exports it, and it must be a ``PreTrainedModel``
    subclass.

    **What the default hooks fit.** :meth:`forecast` calls
    ``model(past_values=context).prediction_outputs[..., 0]`` — so the
    shipped kind covers exactly the models whose forward takes
    ``past_values`` ALONE and answers ``prediction_outputs``: PatchTST and
    PatchTSMixer, single-channel, point-forecast heads. Everything else —
    a forward wanting time features or an observed mask
    (``TimeSeriesTransformer``, Informer, Autoformer), a distribution head
    (``loss="nll"``), a multi-channel config, Chronos, TimesFM, Moirai — is
    a subclass supplying :meth:`build_model` and :meth:`forecast`, and
    refuses BY NAME at load rather than answering a wrong number: one zero
    context is probed through the hook before any row is scored.

    Parameters
    ----------
    params : dict
        ``root`` / ``snapshot`` / ``stream`` as :class:`PretrainedEncode`;
        ``features`` (list of str, REQUIRED) — the context columns, oldest
        first, as many as :meth:`context_length_of` reports; ``horizon``
        (int >= 1, default 1) — the forecast step ``predict`` answers, at
        most the model's ``prediction_length``.

    Examples
    --------
    Score a windowed row stream against the zero-shot baseline::

        node = PretrainedForecast("baseline", {
            "root": "./onboarding_root",
            "snapshot": "b7d0…64 hex…",
            "features": ["lag_8", "lag_7", "lag_6", "lag_5",
                         "lag_4", "lag_3", "lag_2", "lag_1"],
            "horizon": 1,
        })
        out = node.run(ctx, {})
        out["signal"].predict(row)   # a float, or None for no coverage
    """

    role = "signal"
    outputs = ("signal", "metrics")
    default_mode = "load"

    _PARAMS = PIN_PARAMS + ("features", "horizon")

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown knob, missing required knob, or
            unusable value.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        problems.extend(_pin_problems(params))
        features = params.get("features")
        if not isinstance(features, list) or not _unique_names(features):
            problems.append(
                "features is required — the ordered context columns, oldest "
                f"first, as a non-empty list of distinct names, got {features!r}"
            )
        check_int_param(problems, "horizon", params.get("horizon", DEFAULT_HORIZON), ge=1)
        return problems

    def features(self):
        """Name the context columns, oldest first (tuple of str)."""
        return tuple(self.params["features"])

    def horizon(self):
        """Give the forecast step ``predict`` answers (int, integral float coerced)."""
        return int(self.params.get("horizon", DEFAULT_HORIZON))

    # -- the subclass seam -----------------------------------------------------

    def build_model(self, files_dir):
        """Restore the forecaster the snapshot's own config names.

        Parameters
        ----------
        files_dir : str
            The snapshot's FILE payload directory.

        Returns
        -------
        transformers.PreTrainedModel
            ``config.architectures[0]`` resolved on ``transformers``, loaded
            with ``local_files_only=True``.

        Raises
        ------
        ValueError
            When the config names no architecture the library exports, or
            names one that is not a ``PreTrainedModel`` subclass — a config
            class, a factory function, anything the attribute happens to
            hit. ``getattr`` on a package answers a great many things that
            are not models.
        """
        import transformers
        from transformers import AutoConfig

        config = AutoConfig.from_pretrained(files_dir, local_files_only=True)
        architectures = getattr(config, "architectures", None) or []
        name = architectures[0] if architectures else None
        model_cls = getattr(transformers, name, None) if isinstance(name, str) else None
        if not (isinstance(model_cls, type)
                and issubclass(model_cls, transformers.PreTrainedModel)):
            raise ValueError(
                f"{self.key}: the snapshot's config names architectures "
                f"{architectures!r}, which is not a transformers model class — a "
                "model outside the library is a subclass supplying build_model"
            )
        return self.load_pretrained(model_cls, files_dir)

    def context_length_of(self, model):
        """Give the context the model reads, or ``None`` when it declares none.

        The hook the feature count is graded against: the library spells a
        context length ``config.context_length``, but a wrapped forecaster
        may keep it anywhere, and a family that declares none is checked by
        the load probe alone.

        Parameters
        ----------
        model : object
            The restored forecaster.

        Returns
        -------
        int or None
            The context length, or ``None`` when the config names none.
        """
        return getattr(getattr(model, "config", None), "context_length", None)

    def check_forecast(self, model, context_length):
        """Probe one forward through :meth:`forecast`; refuse a model it cannot drive.

        The default hook makes a narrow claim — ``past_values`` alone in,
        ``prediction_outputs`` out — and a model that does not honour it
        fails per ROW, deep inside a scoring loop, or worse answers a tensor
        of the wrong thing. One zero context settles it at load.

        Parameters
        ----------
        model : object
            The restored forecaster, in eval mode.
        context_length : int
            The context width to probe with.

        Returns
        -------
        None
            The probe is a gate, not a value.

        Raises
        ------
        ValueError
            Naming the node when the hook raises, answers something other
            than a rank-2 tensor, or answers fewer steps than ``horizon``.
        """
        import torch

        dtype = _parameter_dtype(model) or torch.float32
        horizon = self.horizon()
        try:
            with torch.no_grad():
                steps = self.forecast(model, torch.zeros(1, context_length, 1,
                                                         dtype=dtype))
        except Exception as exc:
            raise ValueError(self._misfit(f"it raised {type(exc).__name__}: {exc}")
                             ) from exc
        if not isinstance(steps, torch.Tensor):
            raise ValueError(self._misfit(
                f"it answered {type(steps).__name__}, not a tensor"))
        if steps.dim() != 2:
            raise ValueError(self._misfit(
                f"it answered a rank-{steps.dim()} tensor {tuple(steps.shape)}, "
                "not [batch, steps]"))
        if steps.shape[1] < horizon:
            raise ValueError(self._misfit(
                f"it answered {steps.shape[1]} step(s), fewer than horizon "
                f"{horizon}"))

    def _misfit(self, reason):
        """The one text every probe refusal carries."""
        return (
            f"{self.key}: the default forecast hook does not fit this model — "
            f"subclass build_model/forecast ({reason}); snapshot {self.snapshot()}"
        )

    def forecast(self, model, context):
        """Ask the model for its forecast steps over one context batch.

        Parameters
        ----------
        model : transformers.PreTrainedModel
            The restored forecaster.
        context : torch.Tensor
            Shape ``[batch, context_length, 1]``.

        Returns
        -------
        torch.Tensor
            Shape ``[batch, prediction_length]`` — channel 0 of the
            library's ``prediction_outputs``. Only a model whose forward
            takes ``past_values`` alone and answers that field fits;
            :meth:`check_forecast` proves it at load.
        """
        return model(past_values=context).prediction_outputs[..., 0]

    # -- the hooks ---------------------------------------------------------------

    def run_train(self, ctx, inputs):
        """Refuse: a zero-shot forecaster has nothing to fit."""
        raise ValueError(
            f"{self.key} (mode='train'): a zero-shot forecaster has nothing to "
            "fit — fine-tune with a TransformerFit subclass and pin ITS artifact"
        )

    def run_load(self, ctx, inputs):
        """Restore the pinned snapshot and hand out its signal.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused — the model comes from the pin.
        inputs : dict
            Empty: the snapshot is the input.

        Returns
        -------
        dict
            ``{"signal": ForecastSignal, "metrics": {"restored": 1.0,
            "snapshot_digest", "context_length", "horizon"}}``.

        Raises
        ------
        ValueError
            When the pin cannot be read, a node-level artifact contradicts
            it, the feature count differs from the model's context length,
            the model reads more than one channel, ``horizon`` exceeds its
            ``prediction_length``, or the default ``forecast`` hook cannot
            drive it.
        """
        pin = self.pinned_artifact(
            self.snapshot(),
            missing="snapshot is required — the acquired model's manifest hash",
        )
        files_dir = self.snapshot_dir(pin)
        _quiet_transformers()
        model = self.build_model(files_dir)
        model.eval()
        features = self.features()
        horizon = self.horizon()
        config = getattr(model, "config", None)
        context_length = self.context_length_of(model)
        if isinstance(context_length, int) and context_length != len(features):
            raise ValueError(
                f"{self.key}: the model's context_length is {context_length} but "
                f"features names {len(features)} column(s) — a zero-shot "
                "forecaster reads exactly its context"
            )
        channels = getattr(config, "num_input_channels", None)
        if isinstance(channels, int) and channels != 1:
            raise ValueError(
                f"{self.key}: the model reads num_input_channels {channels} but "
                "features names one series — a multivariate forecaster needs a "
                "subclass that builds its own context tensor"
            )
        prediction_length = getattr(config, "prediction_length", None)
        if isinstance(prediction_length, int) and horizon > prediction_length:
            raise ValueError(
                f"{self.key}: horizon {horizon} exceeds the model's "
                f"prediction_length {prediction_length}"
            )
        # Last, because the specific checks above give the better message:
        # one forward proves the hook fits before any row is scored.
        self.check_forecast(
            model,
            context_length if isinstance(context_length, int) else len(features),
        )
        self.log.info(
            "restored %s from snapshot %s (context %d, horizon %d)",
            class_ref(type(model)), pin[:12], len(features), horizon,
        )
        signal = ForecastSignal(
            model, features, horizon, self.forecast, artifact_path=files_dir, digest=pin
        )
        return {
            "signal": signal,
            "metrics": {
                "restored": 1.0,
                "snapshot_digest": pin,
                "context_length": len(features),
                "horizon": horizon,
            },
        }


# ---------------------------------------------------------------------------
# Registration — explicit and idempotent, never at import
# ---------------------------------------------------------------------------

#: The pack's registrable kinds: CONCRETE classes only. ``TransformerFit``
#: is abstract and the registry refuses abstract classes by construction;
#: the last three are the pretrained doorway (ADR-0083).
NODE_KINDS = (
    ("transformers-fit", DeclaredTransformerFit),
    ("transformers-tiny-fit", TinyTransformerFit),
    ("transformers-predict", TransformerPredict),
    ("transformers-encode", PretrainedEncode),
    ("transformers-classify", PretrainedClassify),
    ("transformers-forecast", PretrainedForecast),
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
