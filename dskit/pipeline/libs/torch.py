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

* **The dataset seam** — :class:`TorchAdapter` answers "how does a row
  become model input", and a document NAMES one (``adapter`` /
  ``adapter_params``, the same grammar as ``module``). The default is
  :class:`RowVectorAdapter`, bit-for-bit the flat feature-vector /
  MSE behaviour this pack always had, which is why the seam changed
  nothing for documents written before it. An adapter also supplies
  constructor kwargs the DATA implies (a vocab size, a sequence length)
  UNDER the document's own, so a declared value always wins. Without
  this, the architecture was declarable while the DATASET was not, and
  any model whose example is not a flat row — a sequence, a panel, a
  ladder — could not be named in a document at all.

Inputs are ROWS — a list of dicts (or attribute-bearing records), the
FitRows/ArrayFeatures shape; feature and label keys are named by params.
What an "example" IS, though, is the adapter's to decide. In-memory only:
nothing here reads a data file.

Packs never auto-register: :data:`NODE_KINDS` plus an explicit
:func:`register` call is the deliberate path (``libs/__init__``), and the
abstract bases stay OUT of the table (``node_class_errors`` refuses
abstract classes).

Import cost: stdlib + ``dskit.pipeline`` only — torch is imported
strictly inside run-path methods (``tests/pipeline/test_purity.py``
enforces this twice).
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping

from dskit.pipeline.base import (
    abstract_class_problem,
    import_library_class,
    import_ref,
    is_class_ref,
    library_path_problems,
)
from dskit.pipeline.kinds_stats import _check_int, _reject_unknown
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node
from dskit.pipeline.trainlog import (
    DEFAULT_MAX_LINES,
    TrainingCurve,
    probability_metrics,
)

__all__ = [
    "ARTIFACT_FORMAT",
    "LOADER_PARAMS",
    "DeclaredPredict",
    "DeclaredTrain",
    "LinearPredictor",
    "LinearRegressor",
    "NODE_KINDS",
    "RowVectorAdapter",
    "TorchAdapter",
    "TorchBatches",
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
#: The objective when a document names none — the same callable this pack
#: always applied, now reached through the ``loss`` knob's OWN doorway so
#: the default and a declared value resolve by ONE path, not two. Spelled
#: as an import path (never imported here: the core stays importable with
#: no torch installed).
DEFAULT_LOSS = "torch.nn.functional:mse_loss"
#: The objective's declared knob pair — named ONCE so the plan-time gate,
#: the ignored-objective check, and the fit's doorway all interrogate the
#: same two spellings, and a third spelling cannot appear in one of them
#: silently.
_LOSS_KNOBS = ("loss", "loss_params")
DEFAULT_EPOCHS = 5
DEFAULT_LR = 0.01
DEFAULT_LABEL = "label"

#: Base knobs that pin a trained module's SHAPE — a load whose sidecar
#: disagrees on one of these is refused by name. MODULE-level, not merely
#: a class attribute, for the reason ``LOADER_PARAMS`` is: ``_load_artifact``
#: reads them by LOOPING over the table, so none of these literals appears
#: in the class's own bytecode, and the conformance check that hunts orphan
#: knobs (``_reachable_knob_names``) deliberately refuses to let a class's
#: own ``_PARAMS``-style attribute vouch for its knobs — it looks for a
#: module attribute of the name the code NAMES, hence the leading
#: underscore matching ``self._SHAPE_PARAMS``. Declared here, ``label``
#: stops reading as an unread knob on the predict family (it was never
#: unread; only invisible).
_SHAPE_PARAMS = ("features", "label")

#: Keys every sidecar must carry — an artifact without them is refused.
_SIDECAR_KEYS = ("format", "module_class", "params", "seed", "state_hash")

#: The word every adapter doorway reports under — resolution
#: (``_resolve_adapter``), plan (``validate_params``) and run
#: (``build_adapter``) all name the same subject, so a refusal reads the
#: same whichever door it came from. Named ONCE because the plan sentence
#: and the run sentence are pinned EQUAL by
#: ``tests/pipeline_libs/test_torch_adapter.py``: four copies of a literal
#: is exactly the unpinned duplication where one changes silently.
_ADAPTER_SUBJECT = "torch adapter"

#: Same rule for the ``loss`` doorway: resolution (``build_loss``) and both
#: of ``_construct_loss``'s refusals report under one name, so the knob
#: never refuses under two different subjects — named ONCE for exactly the
#: reason ``_ADAPTER_SUBJECT`` is.
_LOSS_SUBJECT = "torch loss"


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


def _optimizer_problems(params):
    """Problems with the declared ``optimizer``/``optimizer_params``.

    ``lr`` is refused INSIDE the block on purpose: the node already owns
    ``lr``, two spellings would disagree, and a search space addressing
    ``<node>.lr`` would silently tune the loser.
    """
    problems = []
    path = params.get("optimizer")
    if path is not None:
        problems += library_path_problems("optimizer", path, example="torch.optim.AdamW")
    block = params.get("optimizer_params", {})
    if not isinstance(block, dict) or any(not isinstance(k, str) for k in block):
        problems.append(
            f"optimizer_params must be a dict of optimizer kwargs, got {block!r}"
        )
    elif "lr" in block:
        problems.append(
            "optimizer_params must not carry 'lr' — the node's own lr knob is "
            "the one place a learning rate is declared (two spellings would "
            "disagree, and a search over '<node>.lr' would tune the loser)"
        )
    return problems


def _loss_problems(params):
    """Problems with the declared ``loss``/``loss_params`` — shape only, at plan.

    Exactly the ``optimizer`` grammar (``library_path_problems`` for the
    path, a kwargs dict beside it), for exactly its reason: the objective
    is part of a model's DEFINITION — a fat-tailed target fitted under MSE
    trains to a different model than under Huber, silently and with no
    error — and whether the path IMPORTS is settled at execute, where the
    library is due. Unlike ``optimizer_params`` there is no reserved key
    inside the block: the node owns no knob a loss kwarg could shadow.
    """
    problems = []
    path = params.get("loss")
    if path is not None:
        problems += library_path_problems(
            "loss", path, example="torch.nn.functional:smooth_l1_loss"
        )
    block = params.get("loss_params", {})
    if not isinstance(block, dict) or any(not isinstance(k, str) for k in block):
        problems.append(f"loss_params must be a dict of loss kwargs, got {block!r}")
    return problems


def _loss_ignored_problem(subject, params):
    """Why a declared ``loss``/``loss_params`` would never be applied by
    ``subject``, or None.

    ONE sentence said wherever a ``loss()`` implementation is chosen —
    plan (``validate_params``) and the fit (``_adapter_for_fit``) — the
    way :func:`~dskit.pipeline.base.abstract_class_problem` is: the node
    ACCEPTS the knob, a ``loss()`` APPLIES it, and one that computes its
    own objective would silently train a different model than the document
    declares. ``subject`` is whoever answers ``loss()`` — the adapter, or
    the node itself when a family overrode ``loss`` — as a class (plan) or
    an instance (run); ``None`` means this machine cannot tell (the
    library may rightly be absent, and run settles it).
    """
    declared = [f"{k} {params[k]!r}" for k in _LOSS_KNOBS if params.get(k)]
    # ``getattr`` because the adapter doorway is STRUCTURAL (it requires
    # ``prepare``, not a base class): a class that never heard of the flag
    # has not promised anything, which is the deny side of default-deny.
    if not declared or subject is None or getattr(subject, "applies_loss", False):
        return None
    name = subject.__name__ if isinstance(subject, type) else type(subject).__name__
    return (
        f"{' and '.join(declared)} would be IGNORED: {name} computes its own "
        "objective (applies_loss is False) — have loss() apply the declared "
        "objective (self.build_loss()(...) in an adapter, super().loss(...) "
        "in a node) and set applies_loss = True, or drop the loss param"
    )


class _LossPromise:
    """Declares whether this class's ``loss()`` applies the node's ``loss``.

    Mixed into both sides of the objective — the adapter that implements
    ``loss()`` and the node that may override it — because the promise is
    a property of that IMPLEMENTATION, not of a class name: a subclass
    replacing ``loss()`` inherits an ``applies_loss`` it never earned, and
    the seam would then approve an objective nothing applies. So a class
    body that declares ``applies_loss`` BINDS the promise to the ``loss``
    implementation visible there, and any subclass whose RESOLVED ``loss``
    is a different function — its own body or a co-base mixin, "one mixin
    for the pair" being this pack's own documented shape — loses the
    promise unless it declares again. Default-deny applied to inheritance,
    keyed to what the MRO will actually run, never to ``cls.__dict__``.
    Overriding neither inherits a promise still kept by the inherited
    implementation.

    Attributes
    ----------
    applies_loss : bool
        Class-level, default ``False``. See :class:`TorchAdapter`.
    _loss_promised_for : callable or None
        Class-level. The ``loss`` implementation the standing promise was
        declared FOR — the witness the reset compares against.
    """

    applies_loss = False
    _loss_promised_for = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "applies_loss" in cls.__dict__:
            # A fresh declaration: bind it to the implementation this
            # class resolves NOW (``getattr`` — a promise with no loss()
            # anywhere binds to nothing and denies every real one).
            cls._loss_promised_for = (
                getattr(cls, "loss", None) if cls.applies_loss else None
            )
        elif cls.applies_loss and getattr(cls, "loss", None) is not (
            cls._loss_promised_for
        ):
            cls.applies_loss = False


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


def _on_device(adapter, batches, index, device):
    """One batch, on ``device`` — or exactly today's batch when ``device``
    is ``None``.

    A single funnel so the training loop never repeats the conditional, and
    so the ``device is None`` path is provably the untouched one: it returns
    ``adapter.select(...)`` and nothing else happens.
    """
    batch = adapter.select(batches, index)
    return batch if device is None else adapter.to_device(batch, device)


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


# ---------------------------------------------------------------------------
# The BATCH-ADAPTER seam — how rows become model input (docs/25 §2)
# ---------------------------------------------------------------------------


class TorchBatches:
    """One split, prepared: how many examples, how many were unusable, and
    whatever opaque payload the adapter needs to cut a batch out of it.

    A concrete container rather than a bare tuple because the trainer
    reports ``n_rows``/``n_skipped`` in ``metrics`` for EVERY adapter — a
    fit that silently dropped 90% of its rows must be visible in the run
    report whether the rows were feature vectors or event panels.
    """

    __slots__ = ("n", "n_skipped", "payload")

    def __init__(self, n, payload, *, n_skipped=0):
        self.n = int(n)
        self.payload = payload
        self.n_skipped = int(n_skipped)

    def __len__(self) -> int:
        return self.n


class TorchAdapter(_LossPromise, ABC):
    """HOW ROWS BECOME MODEL INPUT — the seam :class:`TorchTrain` was missing.

    Before this, the trainer hardcoded one dataset shape: every row is a
    flat vector of ``features``, the batch is ``(x[idx], y[idx])``, the
    objective is MSE on ``module(x)``, and a prediction is that same scalar.
    That is right for a regressor over a feature row and WRONG for every
    model whose example is not a row — a sequence model, a panel model, a
    graph model. Such a model could not be named in a document at all: the
    architecture was declarable (``module``) while the DATASET was not.

    An adapter owns the four things the base genuinely cannot know, and
    the class is ABSTRACT in exactly these four — an adapter missing one
    is refused at construction, never deep inside a training loop:

    1. ``prepare`` — ``prepare(rows, params, *, where)``: a list of rows
       in, a :class:`TorchBatches` out. This is where a row list becomes
       tensors, panels, or anything else; unusable rows are counted here
       (``n_skipped``) and never fabricated. ``where`` (str) names the
       port (``"rows"``/``"val_rows"``) so a refusal says which wire is
       wrong.
    2. ``select`` — ``select(batches, index)``: the examples at ``index``
       (a ``LongTensor`` of positions), or the WHOLE split when ``index``
       is ``None``. Only the adapter knows how to slice a batch whose
       shape it invented.
    3. ``loss`` — ``loss(module, batch)``: the objective, a scalar tensor
       that is backpropagated. WHICH objective can be declarable: the
       node's ``loss`` knob names a callable by import path and
       :meth:`build_loss` resolves it (MSE when undeclared). An adapter
       that answers through :meth:`build_loss` says so with
       ``applies_loss = True``; one that computes its own objective leaves
       the default ``False``, and the node then REFUSES a declared ``loss``
       by name rather than ignoring it.
    4. ``predict`` — ``predict(module, record)``: one record to one float
       belief (or ``None`` for no coverage), which is what
       :class:`TorchSignal` serves downstream.

    Everything else has a WORKING default and stays optional, so an
    adapter implements it only when its shape demands it:
    :meth:`module_params` (constructor kwargs the DATA implies — a vocab
    size, a token width — merged UNDER the document's own, so a declared
    value always wins and nothing the data says can silently override the
    config), :meth:`beliefs` (the calibrated probabilities + matching
    labels that :func:`~dskit.pipeline.trainlog.probability_metrics`
    scores per epoch; one metrics helper, every adapter),
    :meth:`to_device`, :meth:`fitted`, and the
    :meth:`save_state`/:meth:`load_state` pair.

    Adapters are DECLARED, not subclassed into the node: a document names
    one in ``adapter`` with ``adapter_params`` as its kwargs, exactly the
    ``module``/``module_params`` and ``estimator``/``estimator_params``
    grammar the pack already uses. Import torch INSIDE methods, never at
    module scope (``tests/pipeline/test_purity.py``).

    Parameters
    ----------
    params : dict or None
        The node's params with the document's ``adapter_params`` layered
        ON TOP, kept as ``self.params``; ``None`` means ``{}``. The node
        passes its WHOLE params dict so an adapter can read
        ``features``/``label`` without the document restating them.

    Attributes
    ----------
    requires_features : bool
        Class-level, default ``True``. Whether the node's ``features``
        knob is meaningful for this adapter. The default row-vector
        adapter needs it; an adapter that builds its examples some other
        way sets it ``False`` so the node stops demanding a feature list
        it would never read.
    applies_loss : bool
        Class-level, default ``False`` (:class:`_LossPromise`). Whether
        :meth:`loss` applies the node's ``loss`` knob through
        :meth:`build_loss`. Default-deny, and for the same reason
        ``features`` has its flag: a document that declares ``loss`` for an
        adapter which never made that promise is REFUSED by name — at plan
        and again at the fit — because an ignored objective trains a
        different model in silence. The promise describes THIS class's
        :meth:`loss`, so a subclass that overrides ``loss`` must repeat the
        declaration to keep it.
    _PARAMS : tuple of str
        Class-level, default ``()``. The adapter's OWN declarable knobs,
        which :meth:`validate_params` enforces default-deny at plan time
        — so a typo in ``adapter_params`` is refused by name.

    Examples
    --------
    A minimal COMPLETE adapter — the four hooks and nothing else — over
    rows carrying one value ``v`` and a label ``y``::

        import torch
        from dskit.pipeline.libs.torch import TorchAdapter, TorchBatches

        class ScalarAdapter(TorchAdapter):
            requires_features = False
            applies_loss = True

            def prepare(self, rows, params, *, where):
                xs = [[float(r["v"])] for r in rows]
                ys = [float(r["y"]) for r in rows]
                return TorchBatches(
                    len(xs),
                    (
                        torch.tensor(xs, dtype=torch.float32),
                        torch.tensor(ys, dtype=torch.float32),
                    ),
                )

            def select(self, batches, index):
                x, y = batches.payload
                return (x, y) if index is None else (x[index], y[index])

            def loss(self, module, batch):
                x, y = batch
                return self.build_loss()(module(x).reshape(-1), y)

            def predict(self, module, record):
                with torch.no_grad():
                    x = torch.tensor([[float(record["v"])]])
                    return float(module(x).reshape(-1)[0])

        adapter = ScalarAdapter({"label": "y"})

    Leave one of those four out and the class stays declarable but
    refuses to CONSTRUCT, naming the hooks that are missing.
    """

    #: Does the node's ``features`` list mean anything here?
    requires_features = True

    #: The adapter's OWN declarable knobs, for its ``validate_params``.
    _PARAMS: tuple = ()

    #: The memo slot :meth:`build_loss` fills on first resolve, declared at
    #: class level so there is something to test before that first call.
    _loss_fn = None

    def __init__(self, params=None):
        self.params = dict(params or {})

    @classmethod
    def validate_params(cls, params):
        """Problems with ``adapter_params``, checked at PLAN time by the
        class that owns those knobs — never restated on the node."""
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        return problems

    # -- the four hooks ----------------------------------------------------

    @abstractmethod
    def prepare(self, rows, params, *, where):
        """``rows`` -> :class:`TorchBatches`. ``where`` names the port
        (``"rows"``/``"val_rows"``) so a refusal says which wire is wrong."""
        raise NotImplementedError

    def module_params(self, batches, params):
        """Constructor kwargs the DATA implies; ``{}`` when none do."""
        return {}

    @abstractmethod
    def select(self, batches, index):
        """The batch at ``index`` (a ``LongTensor`` of example positions),
        or the WHOLE split when ``index`` is ``None``."""
        raise NotImplementedError

    @abstractmethod
    def loss(self, module, batch):
        """A scalar tensor to backpropagate."""
        raise NotImplementedError

    def build_loss(self, device=None):
        """The objective callable this adapter's :meth:`loss` applies.

        The ``loss`` knob's resolution doorway, and the reason the knob can
        exist at all without a registry: the document names an import path
        (``torch.nn.functional:smooth_l1_loss``) exactly as it names an
        ``optimizer``. Declarable because the objective is part of a model's
        DEFINITION: a fat-tailed return series fitted under MSE trains to a
        different model than under Huber, silently and with no error.

        Two things a path may name, and the difference is exactly the one
        :meth:`~TorchTrain.build_optimizer` already makes. A CLASS
        (``torch.nn:HuberLoss``) is a loss factory and is CONSTRUCTED here
        with ``loss_params`` as its constructor kwargs — exactly how
        ``optimizer_params`` reaches the optimizer, and the only reachable
        home for a knob like Huber's ``delta``, which on a return-scale
        target IS the tail cutoff. Anything else callable is used as it
        stands — a functional loss, a project's own objective — with a
        non-empty ``loss_params`` carried as call-time kwargs
        (:func:`functools.partial`); when the block is absent or empty the
        callable is returned as the very same object, so the default path
        stays byte-identical.

        Resolution is memoized on the instance, so the training loop pays
        the import lookup once per fit rather than once per batch.

        Parameters
        ----------
        device : str or None
            Where the fit runs — THREADED from the node's single ``device``
            read (:meth:`TorchTrain.run`), exactly as every batch's move
            is, and never re-read from this adapter's own params, where a
            same-named adapter knob could point somewhere the batches never
            go. ``None`` (the default, and every direct call outside a
            fit) moves nothing.

        Returns
        -------
        callable
            The declared objective — the resolved callable, or an instance
            of the resolved class — or the callable behind
            :data:`DEFAULT_LOSS` (``torch.nn.functional.mse_loss``) when the
            document named none, which is the same object this pack always
            applied. A constructed ``nn.Module`` objective is moved to
            ``device`` first, exactly as the fit moves the module and every
            batch — a stateful loss (registered buffers) must live where
            the tensors it meets do, or it dies mid-fit with a raw
            cross-device error. A functional objective is returned as the
            very same object, ``device`` or not.

        Raises
        ------
        ValueError
            When the declared path names no importable module or attribute
            (:func:`~dskit.pipeline.base.import_library_class`); when a
            resolved CLASS rejects ``loss_params`` as its constructor
            kwargs; or when what the path yields is not callable — each
            refused by name here rather than as an obscure error mid-fit.
        """
        if self._loss_fn is None:
            path = self.params.get("loss") or DEFAULT_LOSS
            resolved = import_library_class(path, _LOSS_SUBJECT)
            built = self._construct_loss(
                resolved, path, dict(self.params.get("loss_params") or {})
            )
            if device:
                import torch

                # A constructed loss MODULE carries state (buffers, e.g. a
                # class-weight vector) that must live where ``TorchTrain``
                # already moved the module and moves every batch. Only an
                # ``nn.Module`` promises ``.to``; a bare callable carries
                # no tensors for this machine to move.
                if isinstance(built, torch.nn.Module):
                    built = built.to(device)
            self._loss_fn = built
        return self._loss_fn

    @staticmethod
    def _construct_loss(resolved, path, kwargs):
        """The callable a resolved loss path yields, or a refusal naming it.

        A resolved CLASS is constructed with ``kwargs`` (the document's
        ``loss_params``, refused BY that class's own constructor when
        mis-typed — the ``optimizer_params`` diagnosis, mirrored); anything
        else is used as it stands, with a non-empty ``kwargs`` bound as
        call-time keywords. The RESULT is asked whether it is callable
        before any binding, because only the constructed object can answer:
        ``import_library_class(..., requires=)`` interrogates the class,
        and every class carries ``type.__call__``, so a class whose
        INSTANCES are not callable would sail past a structural check and
        die inside the batch loop.
        """
        built = resolved
        if isinstance(resolved, type):
            try:
                built = resolved(**kwargs)
            except TypeError as exc:
                raise ValueError(
                    f"{_LOSS_SUBJECT}: {path!r} rejected loss_params ({exc}) "
                    "— a loss class's constructor kwargs are declared there, "
                    "exactly as optimizer_params carries the optimizer's"
                ) from exc
        if not callable(built):
            raise ValueError(
                f"{_LOSS_SUBJECT}: {path!r} yields {built!r}, which is not "
                "callable — name a loss function, or a loss class whose "
                "instances are"
            )
        if not isinstance(resolved, type) and kwargs:
            built = functools.partial(built, **kwargs)
        return built

    def beliefs(self, module, batch):
        """``(preds, labels)`` in ``[0, 1]`` for the per-epoch probability
        metrics, or ``(None, None)`` when this objective has none."""
        return None, None

    def to_device(self, batch, device):
        """``batch`` moved onto ``device`` — the adapter's call, because the
        adapter is the only thing that knows the batch's SHAPE.

        The base declines (returns it unchanged), which is correct for an
        adapter whose batch holds no tensors and loud for one that does: a
        CPU batch meeting a module on ``cuda`` raises a device mismatch
        immediately rather than training something wrong. Every shipped
        adapter implements it for its own shape.
        """
        return batch

    def fitted(self, module, train_batches, val_batches):
        """Called ONCE when the fit closes, before the artifact is written.

        The hook an adapter needs when serving requires the trained model
        (materializing a lookup, calibrating a temperature). Default: do
        nothing — a row-vector model answers straight from ``module``.
        """
        return None

    # -- fitted state that is NOT in the state dict ------------------------

    def save_state(self, prefix):
        """Persist whatever :meth:`fitted` materialized, beside ``model.pt``.

        Returns a JSON-small manifest ``{name: {...}}`` recorded in the
        artifact sidecar — so whatever is written here is covered by the
        artifact's content hash, exactly like the state file. ``{}`` (the
        default) means this adapter carries no state beyond the weights.

        This exists because ``mode="load"`` consumes NO inputs: "the
        artifact IS the input". An adapter whose serving surface is built
        by :meth:`fitted` from the panels it was prepared with therefore
        has NOTHING to rebuild from at load time, and a restore that
        silently answers ``None`` for every lookup is a run that quietly
        did not use the model at all. ``prefix`` is the artifact path with
        its extension stripped, so a file lands next to ``model.pt`` and
        travels with it.
        """
        return {}

    def load_state(self, prefix, recorded):
        """Restore what :meth:`save_state` wrote, or RAISE.

        ``recorded`` is the sidecar's manifest for this adapter. An adapter
        that needs state and finds none must raise — the caller turns that
        into a refusal naming the artifact. Falling back to "serve nothing"
        is the failure this hook exists to make impossible.
        """
        return None

    @abstractmethod
    def predict(self, module, record):
        """One record -> one float, or ``None`` for no coverage."""
        raise NotImplementedError


class RowVectorAdapter(TorchAdapter):
    """The DEFAULT adapter: one flat feature vector per row, MSE objective.

    Bit-for-bit the behaviour :class:`TorchTrain` had before the seam
    existed — same ``_usable_rows`` skip-and-count, same
    ``torch.tensor(xs)`` matrix, same ``mse_loss(module(x).reshape(-1), y)``,
    same ``module(row)`` prediction. It is the default precisely so that
    adding the seam changed nothing for any document already in flight.

    The objective is the one thing a document may swap without leaving this
    shape: ``loss`` names a callable applied to
    ``(module(features).reshape(-1), label)``, and the default resolves to
    that same ``mse_loss``.
    """

    #: This adapter answers through ``build_loss``, so the node's knob is
    #: real here — the promise the node checks before accepting ``loss``.
    applies_loss = True

    def prepare(self, rows, params, *, where):
        import torch

        features = list(params["features"])
        label = params.get("label", DEFAULT_LABEL)
        xs, ys, skipped = _usable_rows(rows, features, label)
        payload = None
        if xs:
            payload = (
                torch.tensor(xs, dtype=torch.float32),
                torch.tensor(ys, dtype=torch.float32),
            )
        return TorchBatches(len(xs), payload, n_skipped=skipped)

    def select(self, batches, index):
        # A 2-tuple ``(features, label)`` — the batch shape the pack's
        # documented ``loss(self, module, batch)`` hook has always been
        # handed, kept verbatim so every existing override still unpacks.
        x, y = batches.payload
        return (x, y) if index is None else (x[index], y[index])

    def to_device(self, batch, device):
        """The ``(features, label)`` pair, moved. Only this class knows the
        batch is a 2-tuple, which is why the move lives here."""
        x, y = batch
        return x.to(device), y.to(device)

    def loss(self, module, batch):
        """The objective over one ``(features, label)`` batch.

        Parameters
        ----------
        module : torch.nn.Module
            The module being fitted; called on the batch's feature matrix.
        batch : tuple
            The ``(features, label)`` tensor pair :meth:`select` produced.

        Returns
        -------
        torch.Tensor
            A scalar — whatever callable the document's ``loss`` names
            applied to ``(module(features).reshape(-1), label)``, MSE when
            it names nothing (:meth:`build_loss`).

        Raises
        ------
        ValueError
            When the declared ``loss`` path cannot be resolved to a usable
            callable — :meth:`build_loss` refuses it by name on the first
            batch rather than dying mid-fit.
        """
        features, label = batch
        return self.build_loss()(module(features).reshape(-1), label)

    def beliefs(self, module, batch):
        features, label = batch
        return module(features).reshape(-1).tolist(), label.tolist()

    def predict(self, module, record):
        import torch

        names = self.params.get("features") or ()
        values = [_value(record, name) for name in names]
        if not values or any(v is None for v in values):
            return None
        with torch.no_grad():
            out = module(torch.tensor([values], dtype=torch.float32))
        return float(out.reshape(-1)[0])


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

    __slots__ = ("adapter", "artifact_path", "features", "loaded", "module")

    def __init__(self, module, features, artifact_path, *, loaded, adapter=None):
        self.module = module
        self.features = tuple(features)
        self.artifact_path = artifact_path
        self.loaded = bool(loaded)
        #: The :class:`TorchAdapter` that prepared this fit. It owns the
        #: record -> belief translation, so a panel model answers through
        #: the same ``predict`` a feature-row model does.
        self.adapter = adapter

    def predict(self, record):
        """One row in, a float out — or ``None`` when any feature is
        missing or non-finite (no coverage, never a made-up number)."""
        if self.adapter is not None:
            return self.adapter.predict(self.module, record)
        import torch

        values = [_value(record, name) for name in self.features]
        if any(v is None for v in values):
            return None
        with torch.no_grad():
            out = self.module(torch.tensor([values], dtype=torch.float32))
        return float(out.reshape(-1)[0])


def _adapter_unknown_at_plan(cls, params):
    """Plan cannot name this family's adapter — ``build_adapter`` was
    overridden without ``_loss_adapter`` beside it, so answering would
    restate an identity the override may have changed. ``None`` leaves the
    ``loss`` interrogation to the fit's own doorway (``_adapter_for_fit``),
    which asks the adapter actually built."""
    return None


class _TorchModel(_LossPromise, Node):
    """The grammar the train and predict doorways share: the
    ``build_module`` hook and the artifact save/load protocol.

    Abstract by construction (``build_module`` is the subclass's
    identity), so neither base can enter a registry —
    ``node_class_errors`` refuses abstract classes.

    The adapter identity is stated ONCE per family: ``build_adapter``
    constructs it at run and ``_loss_adapter`` names it at plan, both
    reading :attr:`_ADAPTER` here. A subclass whose resolved
    ``build_adapter`` sits SHALLOWER in the MRO than its resolved
    ``_loss_adapter`` has changed the run side alone, so
    ``__init_subclass__`` replaces its plan answer with cannot-tell
    (``None``) rather than let plan certify an adapter the fit does not
    build; overriding the pair together — one class or one mixin defining
    both — is trusted, and their residual agreement is pinned by a runtime
    refusal in :meth:`_adapter_for_fit`.
    """

    #: The adapter class this family fits with — stated ONCE, read by
    #: ``build_adapter`` at run and ``_loss_adapter`` at plan, so the two
    #: cannot drift into disagreeing about what a fit will use.
    _ADAPTER = RowVectorAdapter
    #: Role-specific knobs, set by :class:`TorchTrain`/:class:`TorchPredict`.
    _BASE_PARAMS = ()
    #: A concrete family's OWN model knobs — appended to the allowed list
    #: and to the shape cross-check at load.
    _EXTRA_PARAMS = ()
    #: Base knobs that pin the trained module's SHAPE; a load where these
    #: disagree with the sidecar is refused by name (training knobs like
    #: epochs/lr may lawfully differ — they are history, not shape). The
    #: values live in the module-level ``_SHAPE_PARAMS`` table.
    _SHAPE_PARAMS = _SHAPE_PARAMS
    #: The adapter THIS fit is using, set at the top of ``run``. Kept as a
    #: class-level ``None`` so a directly-called ``loss`` still works.
    _adapter = None
    #: Constructor kwargs the DATA implied for this fit (``module_params``
    #: on the adapter). Recorded in the sidecar and merged back at load —
    #: without that, a restore would rebuild the module from the document
    #: alone and silently get a different shape than was trained.
    _data_params: dict = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        mro = cls.__mro__
        built = next(i for i, base in enumerate(mro) if "build_adapter" in vars(base))
        planned = next(
            i for i, base in enumerate(mro) if "_loss_adapter" in vars(base)
        )
        if planned > built:
            cls._loss_adapter = classmethod(_adapter_unknown_at_plan)

    @classmethod
    def _allowed(cls):
        return tuple(cls._BASE_PARAMS) + tuple(cls._EXTRA_PARAMS)

    @abstractmethod
    def build_module(self, params):
        """Return the ``torch.nn.Module`` these params describe. The
        subclass hook — import torch INSIDE it, never at module top."""
        raise NotImplementedError

    def build_adapter(self, params):
        """The :class:`TorchAdapter` that turns rows into batches.

        Default: :attr:`_ADAPTER` (:class:`RowVectorAdapter`, the flat
        feature-vector shape this pack always had). A family fitting a
        different shape names it in :attr:`_ADAPTER`, so plan and run read
        ONE statement of that identity; a family that must CONSTRUCT its
        adapter differently overrides this and :meth:`_loss_adapter`
        together — overriding this alone demotes the family's plan answer
        to cannot-tell (see the class docstring), and a pair that
        disagrees is refused at the fit (:meth:`_adapter_for_fit`). The
        declared family, whose adapter is a document value rather than a
        class attribute, overrides the pair.

        Parameters
        ----------
        params : dict
            The node's params — handed to the adapter's constructor, which
            reads its own knobs (``features``/``label``/…) from them.

        Returns
        -------
        TorchAdapter
            A fresh :attr:`_ADAPTER` instance over ``params``.
        """
        return self._ADAPTER(params)

    def _adapter_for_fit(self, params):
        """The adapter this fit will use, held to the ``loss`` promise.

        The ONE doorway every family's adapter passes on its way into a
        training loop — :meth:`build_adapter` is the extension hook, so a
        refusal bolted onto one family's override is a refusal the next
        family walks past. Both implementations that could answer
        ``loss()`` are asked here: the node (a family may override
        :meth:`~TorchTrain.loss`) and the adapter it built.

        Parameters
        ----------
        params : dict
            The node's params, read for the declared ``loss`` path.

        Returns
        -------
        TorchAdapter
            Whatever :meth:`build_adapter` returned.

        Raises
        ------
        ValueError
            When the class :meth:`_loss_adapter` promised at plan is not
            the class this fit built — the runtime pin on the plan/run
            identity pair, so one side changing silently is a refusal, not
            a certificate for the wrong adapter. Or when the document
            declares a ``loss`` that neither this node nor its adapter
            applies (``applies_loss`` is ``False``), in the same sentence
            plan says — a fit never proceeds under an objective nothing
            will use.
        """
        adapter = self.build_adapter(params)
        promised = self._loss_adapter(params)
        if promised is not None and type(adapter) is not promised:
            raise ValueError(
                f"{type(self).__name__} plans with {promised.__name__} but the "
                f"fit built {type(adapter).__name__}: _loss_adapter and "
                "build_adapter have drifted — override them together, or state "
                "the identity once in _ADAPTER"
            )
        for subject in (self, adapter):
            problem = _loss_ignored_problem(subject, params)
            if problem:
                raise ValueError(problem)
        return adapter

    def build_optimizer(self, module, params):
        """The optimizer for this fit — ``torch.optim.SGD`` at ``lr`` unless
        the document names another in ``optimizer``/``optimizer_params``.

        Declarable because the optimizer is part of a model's DEFINITION,
        not a detail: a family whose regularization is carried by
        ``weight_decay`` (a bespoke family might shrink per-instrument
        terms toward a pooled head that way) trains to a different
        model under plain SGD, silently and with no error. ``lr`` stays the
        node's own knob so it means one thing across every optimizer, and a
        declared ``lr`` inside ``optimizer_params`` is refused rather than
        quietly shadowing it.
        """
        import torch

        path = params.get("optimizer")
        kwargs = dict(params.get("optimizer_params") or {})
        lr = float(params.get("lr", DEFAULT_LR))
        if not path:
            return torch.optim.SGD(module.parameters(), lr=lr, **kwargs)
        cls = import_library_class(path, "torch optimizer", requires=("step",))
        try:
            return cls(module.parameters(), lr=lr, **kwargs)
        except TypeError as exc:
            raise ValueError(
                f"{path} rejected optimizer_params ({exc}) — a mis-typed "
                "optimizer knob is caught here, by the optimizer, not silently"
            ) from exc

    @classmethod
    def _features_required(cls, params) -> bool:
        """Does ``features`` have to be declared? Only the adapter knows,
        and only the DECLARED family can have a non-default one."""
        return True

    @classmethod
    def _loss_adapter(cls, params):
        """The adapter class whose ``applies_loss`` plan interrogates —
        :attr:`_ADAPTER`, the same value :meth:`build_adapter` constructs,
        so the question asked at plan is about the class the fit builds.
        Valid only beside that ``build_adapter``: a subclass replacing the
        run side alone gets this answer swapped for cannot-tell
        (``__init_subclass__``), and :meth:`_adapter_for_fit` pins the
        pair's agreement at the fit."""
        return cls._ADAPTER

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

    @staticmethod
    def _state_prefix(state_path):
        """``model.pt`` -> ``model``: where an adapter's own state files go,
        so they travel with the artifact a document pins."""
        return os.path.splitext(state_path)[0]

    def _save_artifact(self, ctx, module, seed, adapter=None):
        """Write ``model.pt`` + the ``model.json`` sidecar; returns the
        state file's path (the artifact reference a document pins).

        An adapter carrying FITTED STATE beyond the weights (a serving
        lookup, a calibration) writes it here through
        :meth:`TorchAdapter.save_state`, and its manifest is recorded in the
        sidecar — which is what puts it under the same content hash as the
        state file. ``adapter_state`` is omitted entirely when the adapter
        has none, so an artifact from a stateless adapter is byte-identical
        to one written before this existed.
        """
        import torch

        state_path = os.path.join(self.artifact_dir(ctx), "model.pt")
        torch.save(module.state_dict(), state_path)
        adapter = adapter if adapter is not None else self._adapter
        adapter_state = (
            adapter.save_state(self._state_prefix(state_path))
            if adapter is not None
            else {}
        )
        sidecar = {
            "data_params": dict(self._data_params),
            "format": ARTIFACT_FORMAT,
            "module_class": self._class_ref(),
            "params": self.params,
            "seed": seed,
        }
        if adapter_state:
            sidecar["adapter_state"] = adapter_state
        # Hashed LAST, over the material above: the digest covers the state
        # bytes and every other sidecar field (S2-A).
        sidecar["state_hash"] = self._state_hash(state_path, sidecar)
        self.write_artifact(ctx, "model.json", sidecar)
        return state_path

    def _restore_adapter(self, sidecar, state_path):
        """The artifact's adapter, with its fitted state restored.

        The load-path counterpart of :meth:`_save_artifact`. An adapter that
        needs state and cannot find it RAISES here, and the refusal names
        the artifact — never a restored model that answers ``None`` for
        every lookup while the run exits 0.
        """
        adapter = self.build_adapter(sidecar["params"])
        try:
            adapter.load_state(
                self._state_prefix(state_path), sidecar.get("adapter_state") or {}
            )
        except Exception as exc:  # noqa: BLE001 - refusal must name the artifact
            self._refuse(
                f"{type(adapter).__name__} could not restore the fitted state "
                f"recorded beside {state_path!r}: {exc}"
            )
        return adapter

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
        # Restore the data-implied kwargs the fit used, so ``build_module``
        # rebuilds the SAME shape it trained — the document alone does not
        # carry them.
        data_params = sidecar.get("data_params") or {}
        if not isinstance(data_params, dict):
            self._refuse(f"artifact sidecar data_params are not a dict: {data_params!r}")
        self._data_params = data_params
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
    ``"label"``), ``epochs``, ``lr``, ``optimizer``/``optimizer_params``
    and ``loss``/``loss_params`` (an import path plus the objective's own
    kwargs — the objective is part of the model's definition, so Huber at
    a declared ``delta`` is a document edit, never a subclass; default
    :data:`DEFAULT_LOSS`, and an adapter that computes its own objective
    refuses both knobs rather than ignoring them, by name at plan),
    and the docs/24 §3 ``loader`` block
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

    **Training telemetry.** Every epoch records its mean train loss and —
    when the optional ``val_rows`` port is wired — the validation loss and
    the calibrated-probability metrics (``logloss``, ``brier``, ``ece``;
    :func:`dskit.pipeline.trainlog.probability_metrics`, binary labels
    only). Lines STREAM through ``self.log`` as each epoch closes, so a
    diverging fit is visible while it runs rather than after it; the
    complete per-epoch table lands in ``training_curve.json`` under the
    node's artifact dir, and the reduction (best epoch, final losses)
    joins ``metrics``. ``log_every``/``max_log_lines`` bound the stream —
    a 200-epoch fit costs ~20 lines, not 200 — while the ARTIFACT stays
    complete regardless.

    ``val_rows`` is a genuinely separate port, not a slice of ``rows``:
    the document wires it from a val-split filter, so this node cannot
    invent its own split and cannot see the test window at all.

    **Checkpoint selection (ADR-0035).** ``monitor`` names the recorded
    loss the fit is selected on (:attr:`_MONITORS` — each a loss, lower
    is better; a maximize-metric cannot be named). When declared, the
    curve tracks it, the best epoch's weights are snapshotted (detached,
    on CPU) and RESTORED after the loop — before ``final_loss`` and
    ``adapter.fitted``, so the persisted artifact, the serving state, and
    the final metrics all describe the selected weights — and ``metrics``
    stamps ``monitor``/``selected_epoch``/``monitor_value``. Undeclared,
    the final epoch's weights persist, bit-for-bit the old behavior. A
    val-derived monitor with no ``val_rows`` wired refuses before epoch
    1; a monitor that never records a finite value refuses after the
    loop.
    """

    role = "train"
    outputs = ("signal", "artifact_path", "metrics")

    #: This node's ``loss`` DELEGATES to the adapter, which is how the
    #: declared objective reaches a batch — so the node keeps the promise
    #: and the adapter's own flag decides. A family that overrides ``loss``
    #: to compute its own objective loses it again (:class:`_LossPromise`).
    applies_loss = True

    _BASE_PARAMS = (
        "device",
        "epochs",
        "features",
        "label",
        "loader",
        "log_every",
        "loss",
        "loss_params",
        "lr",
        "max_log_lines",
        "monitor",
        "optimizer",
        "optimizer_params",
    )

    #: The monitorable row keys — every one a LOSS the curve records.
    #: Class-level so a child pack widens it by declaration; validation is
    #: a pure string check, importable with no torch installed.
    _MONITORS = ("train_loss", "val_loss", "logloss", "brier", "ece")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._allowed())
        device = params.get("device")
        if device is not None and (not isinstance(device, str) or not device):
            problems.append(
                f"device must be null or a torch device string, got {device!r} "
                "(e.g. 'cuda', 'cuda:0', 'cpu'). Availability is NOT checked "
                "here — a plan machine may rightly have no GPU; the move at "
                "fit time is what refuses an absent device, loudly"
            )
        _check_int(problems, "epochs", params.get("epochs", DEFAULT_EPOCHS), ge=1)
        lr = params.get("lr", DEFAULT_LR)
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
        monitor = params.get("monitor")
        if monitor is not None and (
            not isinstance(monitor, str) or monitor not in cls._MONITORS
        ):
            problems.append(
                f"monitor must be one of {sorted(cls._MONITORS)} (each a "
                f"loss — lower is better), got {monitor!r}"
            )
        problems.extend(_loader_problems(params.get("loader", {})))
        problems.extend(_optimizer_problems(params))
        problems.extend(_loss_problems(params))
        # Both implementations that could answer ``loss()`` — this family
        # (it may have overridden ``loss``) and the adapter it will build.
        # Only worth interrogating when an objective knob is declared.
        if any(params.get(k) for k in _LOSS_KNOBS):
            for subject in (cls, cls._loss_adapter(params)):
                ignored = _loss_ignored_problem(subject, params)
                if ignored:
                    problems.append(ignored)
        problems.extend(
            _feature_problems(params, required=cls._features_required(params))
        )
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
                "val_rows must be a LIST of feature/label rows (or be left "
                f"unwired), got {type(val_rows).__name__}"
            )
        return problems

    def loss(self, module, batch):
        """The training objective for one batch — delegated to the adapter.

        The default chain: :class:`RowVectorAdapter` applies the declared
        ``loss`` (MSE when none is declared) over ``(features, label)``.
        Declare ``loss`` to swap the objective; override this only for one
        no import path can express (and then say ``applies_loss = False``
        beside it, so a document declaring ``loss`` is refused rather than
        ignored).

        Parameters
        ----------
        module : torch.nn.Module
            The module being fitted.
        batch : object
            Whatever the adapter's ``select`` produced — only the adapter
            knows its shape.

        Returns
        -------
        torch.Tensor
            A scalar tensor the loop can backpropagate.

        Raises
        ------
        ValueError
            On a direct call before ``run`` set the fit's adapter, this
            builds one through :meth:`~_TorchModel._adapter_for_fit`, which
            refuses a declared ``loss`` nothing applies and a plan/run
            adapter-identity drift; the adapter's own ``loss`` then refuses
            an unusable declared ``loss`` path by name (``build_loss``).
        """
        adapter = self._adapter or self._adapter_for_fit(self.params)
        return adapter.loss(module, batch)

    def run(self, ctx, inputs):
        if self.mode == "load":
            module, sidecar = self._load_artifact(self.artifact)
            features = self.params.get("features") or sidecar["params"].get(
                "features", ()
            )
            self.log.info("restored %s from %s", self._class_ref(), self.artifact)
            return {
                "signal": TorchSignal(
                    module,
                    features,
                    self.artifact,
                    loaded=True,
                    # The adapter's OWN fitted state comes back too. Without
                    # this a restored panel model answers None for every
                    # lookup and the run silently serves the market instead.
                    adapter=self._restore_adapter(sidecar, self.artifact),
                ),
                "artifact_path": self.artifact,
                "metrics": {"loaded": 1, "seed": sidecar["seed"]},
            }

        import torch

        loader = dict(LOADER_DEFAULTS)
        loader.update(self.params.get("loader", {}))
        seed, batch_size = loader["seed"], loader["batch_size"]
        epochs = self.params.get("epochs", DEFAULT_EPOCHS)
        features = list(self.params.get("features") or ())
        label = self.params.get("label", DEFAULT_LABEL)

        # THE DATASET SEAM. What an "example" is belongs to the adapter, so
        # the loop below is the same whether the batch is a feature matrix
        # or a ladder panel — and the default adapter makes it identical to
        # what this node did before the seam existed.
        adapter = self._adapter = self._adapter_for_fit(self.params)
        train_set = adapter.prepare(inputs["rows"], self.params, where="rows")
        if not len(train_set):
            raise ValueError(
                f"{self.key}: no usable rows — {adapter.__class__.__name__} "
                f"could build no example from features {features} + label "
                f"{label!r} ({train_set.n_skipped} row(s) seen)"
            )

        # The optional validation port — a val-split row list the DOCUMENT
        # wires. Absent is fine (the curve then tracks train loss); present
        # is what makes the fit's generalization visible per epoch.
        val_set, val_rows = None, inputs.get("val_rows")
        if val_rows:
            val_set = adapter.prepare(val_rows, self.params, where="val_rows")
            if not len(val_set):
                raise ValueError(
                    f"{self.key}: val_rows was wired but "
                    f"{adapter.__class__.__name__} could build no example from "
                    f"features {features} + label {label!r} "
                    f"({val_set.n_skipped} row(s) seen) — fix the wiring rather "
                    "than training blind"
                )

        # ADR-0035: every monitor except train_loss is validation
        # telemetry — its row key only exists when val_rows are wired. A
        # declared selection rule that silently degraded to train-loss
        # selection is exactly the trap the seam exists to close.
        monitor = self.params.get("monitor")
        if monitor and monitor != "train_loss" and val_set is None:
            raise ValueError(
                f"{self.key}: monitor {monitor!r} selects on validation "
                "telemetry but no val_rows are wired — wire val_rows or "
                "drop monitor"
            )

        torch.manual_seed(seed)  # pins the init: one seed, one state dict
        # Data-implied constructor kwargs go UNDER the document's own, so a
        # declared value always wins and the data can never silently
        # override the config.
        self._data_params = adapter.module_params(train_set, self.params)
        module = self.build_module(self.params)
        # WHERE the fit runs. Declared, never sniffed: the default is None,
        # which is CPU and bit-for-bit what this loop always did. A named
        # device moves the module here and every batch below through the
        # adapter, because only the adapter knows the batch's shape.
        device = self.params.get("device") or None
        if device:
            module.to(device)
            self.log.info("training on device %r", device)
        # The objective resolves HERE, against the fit's ONE device read —
        # the same read that just moved the module and moves every batch
        # below — never against the adapter's own params, where a
        # same-named adapter knob could point somewhere the batches never
        # go. Memoized: every later loss() call gets this object.
        adapter.build_loss(device)
        optimizer = self.build_optimizer(module, self.params)
        order_gen = torch.Generator().manual_seed(seed)  # pins the shuffle
        curve = TrainingCurve(
            self.key,
            self.log,
            total_epochs=epochs,
            objective=monitor or ("val_loss" if val_set else "train_loss"),
            log_every=self.params.get("log_every", 1),
            max_lines=self.params.get("max_log_lines", DEFAULT_MAX_LINES),
        )
        best_state = None  # the monitor's snapshot; None until a best epoch
        n = len(train_set)
        for epoch in range(1, epochs + 1):
            started = time.monotonic()
            module.train()
            if loader["shuffle"]:
                order = torch.randperm(n, generator=order_gen)
            else:
                order = torch.arange(n)
            batch_loss_sum, n_batches = 0.0, 0
            for start in range(0, n, batch_size):
                idx = order[start : start + batch_size]
                optimizer.zero_grad()
                batch_loss = self.loss(
                    module, _on_device(adapter, train_set, idx, device)
                )
                batch_loss.backward()
                optimizer.step()
                batch_loss_sum += float(batch_loss.detach())
                n_batches += 1
            train_loss = batch_loss_sum / max(n_batches, 1)

            val_loss, scored = None, {}
            if val_set is not None:
                module.eval()
                with torch.no_grad():
                    val_batch = _on_device(adapter, val_set, None, device)
                    val_loss = float(self.loss(module, val_batch))
                    preds, val_labels = adapter.beliefs(module, val_batch)
                if preds is not None:
                    scored = probability_metrics(preds, val_labels)
                if monitor and monitor not in ("train_loss", "val_loss") and monitor not in scored:
                    # A diverged epoch (non-finite predictions) drops the
                    # probability metrics entirely; the monitored key must
                    # still be PRESENT — as a None the curve records but
                    # never selects — or the strict curve would abort the
                    # whole fit instead of restoring the pre-divergence
                    # best (ADR-0035).
                    scored = dict(scored)
                    scored[monitor] = None
            row = curve.record(
                epoch,
                train_loss,
                val_loss=val_loss,
                metrics=scored,
                seconds=time.monotonic() - started,
            )
            if monitor and row["best"]:
                # Snapshot NOW: the state_dict's tensors alias the live
                # training weights, so the copy is mandatory, and to-CPU
                # means a cuda fit holds one extra host copy, never 2x
                # device memory. One snapshot lives at a time. Non-tensor
                # entries (a module's get_extra_state()) are deep-copied —
                # they have no .detach().
                best_state = {
                    k: (
                        v.detach().to("cpu", copy=True)
                        if hasattr(v, "detach")
                        else copy.deepcopy(v)
                    )
                    for k, v in module.state_dict().items()
                }
        curve.log_final()

        if monitor:
            if best_state is None:
                raise ValueError(
                    f"{self.key}: monitor {monitor!r} never recorded a "
                    "finite value — every epoch's tracked objective was "
                    "missing or non-finite; there is nothing to select"
                )
            # Restore BEFORE final_loss and adapter.fitted: the persisted
            # artifact, the serving state, and the final metrics must all
            # describe the SELECTED weights, not the last epoch's.
            module.load_state_dict(best_state)
            self.log.info(
                "%s: restored epoch %d weights (best %s %.6g)",
                self.key,
                curve.best_epoch,
                monitor,
                curve.best_value,
            )

        module.eval()
        with torch.no_grad():
            final_loss = float(
                self.loss(module, _on_device(adapter, train_set, None, device))
            )
        # Serving may need the FITTED model (a lookup to materialize, a
        # temperature to calibrate). Once, here, never per prediction.
        adapter.fitted(module, train_set, val_set)
        if device:
            # The ARTIFACT is device-independent: a fit on cuda must restore
            # on a CPU-only machine, and the served module answers single
            # records where a device transfer costs more than the forward.
            module.to("cpu")
        # The curve is DURABLE, not just streamed: the run report reads the
        # artifact, and a strided stream never loses an epoch here.
        self.write_artifact(ctx, "training_curve.json", curve.payload())
        artifact_path = self._save_artifact(ctx, module, seed, adapter=adapter)
        self.log.info(
            "trained %s on %d example(s) (%d skipped), %d epoch(s), seed %d -> %s",
            self._class_ref(),
            n,
            train_set.n_skipped,
            epochs,
            seed,
            artifact_path,
        )
        metrics = {
            "n_rows": n,
            "n_skipped": train_set.n_skipped,
            "epochs": epochs,
            "seed": seed,
            "final_loss": final_loss,
            "device": device or "cpu",
        }
        if val_set is not None:
            metrics["n_val_rows"] = len(val_set)
            metrics["n_val_skipped"] = val_set.n_skipped
        metrics.update(curve.summary())
        if monitor:
            # selected_epoch is THE contract key: which epoch's weights
            # were persisted. best_epoch (from the summary) agrees here by
            # construction, but a consumer must not have to know that.
            metrics["monitor"] = monitor
            metrics["selected_epoch"] = curve.best_epoch
            metrics["monitor_value"] = curve.best_value
        return {
            "signal": TorchSignal(
                module, features, artifact_path, loaded=False, adapter=adapter
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
        adapter = self._restore_adapter(sidecar, reference)
        features = self.params.get("features") or sidecar["params"].get("features")
        if not features and adapter.requires_features:
            self._refuse(
                "artifact sidecar records no features and this node declares "
                "none — the signal would have no row keys to read"
            )
        self.log.info("restored %s from %s", self._class_ref(), reference)
        return {
            "signal": TorchSignal(
                module, features or (), reference, loaded=True, adapter=adapter
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


# ---------------------------------------------------------------------------
# The DECLARED family — an architecture the DOCUMENT names
# ---------------------------------------------------------------------------


class _DeclaredModule:
    """``build_module`` from a class path in ``params["module"]``.

    The config-declared counterpart of :class:`_LinearModule`, and the
    reason a new architecture is a CONFIG edit rather than a new Python
    subclass. ``module`` names any ``nn.Module`` class
    (``"torch.nn.Linear"`` or ``"torch.nn:Linear"``) and ``module_params``
    is its constructor kwargs, exactly as ``estimator`` /
    ``estimator_params`` work in the sklearn pack — one grammar for "name
    me a model", across doorways.

    Shared by the train/predict pair as ONE mixin, which is what makes the
    sidecar's ``build_module`` identity check pass across the pair. Note
    what that check now means for this family: every declared
    architecture shares this one ``build_module``, so the class check
    alone can no longer tell a Linear artifact from an LSTM one. That is
    exactly why ``module``/``module_params`` are ``_EXTRA_PARAMS`` — the
    base cross-checks those against the sidecar at load
    (``_load_artifact``), so a mismatched architecture is still refused BY
    NAME, by value rather than by class.
    """

    def build_module(self, params):
        cls = import_library_class(
            params["module"], "torch module", requires=("forward",)
        )
        # Data-implied kwargs UNDER the document's own: this family is the
        # one that owns a ``module_params`` sub-dict, so it is the one that
        # knows where an adapter's shape kwargs belong. A declared value
        # always wins, and the data can never silently override the config.
        kwargs = {**self._data_params, **(params.get("module_params") or {})}
        try:
            return cls(**kwargs)
        except TypeError as exc:
            raise ValueError(
                f"{params['module']} rejected module_params ({exc}) — a "
                "mis-typed constructor knob is caught here, by the "
                "constructor, not silently"
            ) from exc


class _DeclaredParams:
    """The declared family's shared knobs and their plan-time checks."""

    _EXTRA_PARAMS = ("adapter", "adapter_params", "module", "module_params")

    @classmethod
    def _resolve_adapter(cls, params):
        """The declared :class:`TorchAdapter` CLASS, or ``None`` when none
        is declared or it cannot be imported HERE (a plan machine may
        rightly lack the library; execute settles it)."""
        path = params.get("adapter")
        if not path or library_path_problems("adapter", path, example="x.y:A"):
            return None
        try:
            return import_library_class(
                path, _ADAPTER_SUBJECT, requires=("prepare",)
            )
        except ValueError:
            return None

    @classmethod
    def _features_required(cls, params) -> bool:
        adapter = cls._resolve_adapter(params)
        # Unresolvable here = keep the base's demand. Being strict on the
        # plan machine is the safe direction: it can only ask for a knob
        # that a row-vector fit would genuinely need.
        return True if adapter is None else bool(adapter.requires_features)

    @classmethod
    def _loss_adapter(cls, params):
        """The DECLARED adapter, which is the one whose ``applies_loss``
        answers here — the same ``_resolve_adapter`` ``build_adapter``
        builds from. ``None`` when it cannot be imported on this machine,
        which leaves the refusal to the fit."""
        if not params.get("adapter"):
            return super()._loss_adapter(params)
        return cls._resolve_adapter(params)

    @classmethod
    def validate_params(cls, params):
        problems = list(super().validate_params(params))
        if "module" not in params:
            problems.append(
                "module is required — the class path of the nn.Module to "
                "build, e.g. 'torch.nn.Linear'"
            )
        else:
            problems += library_path_problems(
                "module", params["module"], example="torch.nn.Linear"
            )
        module_params = params.get("module_params", {})
        if not isinstance(module_params, dict) or any(
            not isinstance(k, str) for k in module_params
        ):
            problems.append(
                f"module_params must be a dict of constructor kwargs, got "
                f"{module_params!r}"
            )
        adapter_params = params.get("adapter_params", {})
        if not isinstance(adapter_params, dict) or any(
            not isinstance(k, str) for k in adapter_params
        ):
            problems.append(
                f"adapter_params must be a dict of the adapter's own knobs, got "
                f"{adapter_params!r}"
            )
        if "adapter" in params:
            problems += library_path_problems(
                "adapter",
                params["adapter"],
                example="dskit.pipeline.libs.torch:RowVectorAdapter",
            )
            # Delegate to the adapter's OWN validator when it is importable
            # here, so its knobs are checked at plan by the class that owns
            # them — never restated (or allowed to drift) on this node.
            adapter = cls._resolve_adapter(params)
            # An adapter that imported fine but can never construct is a
            # plan-time fact: say NOW what ``build_adapter`` would raise at
            # run, in the same sentence. This problems list swallows
            # nothing, so a machine genuinely missing the library
            # (``_resolve_adapter`` -> ``None``) still gets no false
            # refusal — the channel argument that keeps
            # ``import_library_class`` structural does not apply here.
            abstract = abstract_class_problem(
                adapter, _ADAPTER_SUBJECT, repr(params["adapter"])
            )
            if abstract:
                problems.append(abstract)
            validator = getattr(adapter, "validate_params", None)
            if validator is not None and isinstance(adapter_params, dict):
                problems += [f"adapter_params.{p}" for p in validator(adapter_params)]
        return problems

    def build_adapter(self, params):
        """The declared adapter, or the flat-vector default when none is named.

        The default is why the seam changed nothing for documents written
        before it existed. Two DIFFERENT failures are told apart here. An
        adapter that never implemented some of :class:`TorchAdapter`'s
        abstract hooks is refused BEFORE construction, naming those hooks
        (core's :func:`~dskit.pipeline.base.abstract_class_problem` —
        asked, never restated); the fix is code, and the document may be
        perfect. Only a complete class that still rejects its kwargs
        reaches the ``adapter_params`` diagnosis.

        Parameters
        ----------
        params : dict
            The node's params. ``adapter`` (str, optional) is the
            adapter's ``pkg.module:Class`` path — absent or empty means
            the default; ``adapter_params`` (dict, optional) are the
            adapter's OWN knobs, layered ON TOP of ``params`` so an
            adapter knob is never shadowed by a same-named node knob.

        Returns
        -------
        TorchAdapter
            The constructed adapter — a :class:`RowVectorAdapter` over
            ``params`` when no ``adapter`` is declared.

        Raises
        ------
        ValueError
            When the declared path does not resolve to a class carrying
            ``prepare``
            (:func:`~dskit.pipeline.base.import_library_class`); when the
            resolved class is still ABSTRACT, in a sentence naming every
            unimplemented hook; when a complete adapter's own constructor
            rejects its ``adapter_params``, naming that knob dict so the
            author reads JSON rather than code.

            A declared ``loss`` this adapter would never apply is refused
            too — not here, but at
            :meth:`~_TorchModel._adapter_for_fit`, the one doorway every
            family's adapter passes on its way into the loop.
        """
        path = params.get("adapter")
        if not path:
            return self._ADAPTER(params)
        cls = import_library_class(path, _ADAPTER_SUBJECT, requires=("prepare",))
        # Asked HERE and at plan (``validate_params``) — never at resolution:
        # plan-time callers rightly treat a resolution failure as "library
        # may be missing on this machine", and an abstractness refusal must
        # never hide in that channel.
        problem = abstract_class_problem(cls, _ADAPTER_SUBJECT, repr(path))
        if problem:
            raise ValueError(problem)
        kwargs = dict(params.get("adapter_params") or {})
        # The adapter sees the node's params (it needs ``features``/``label``)
        # with its OWN declared knobs layered ON TOP, so an adapter knob is
        # never shadowed by a same-named node knob.
        try:
            return cls({**params, **kwargs})
        except TypeError as exc:
            raise ValueError(
                f"{path} rejected adapter_params ({exc}) — a mis-typed adapter "
                "knob is caught here, by the adapter, not silently"
            ) from exc


class DeclaredTrain(_DeclaredParams, _DeclaredModule, TorchTrain):
    """Train ANY ``nn.Module`` the document names. Registered as
    ``torch-train``.

    The generic trainer the pack was missing: everything
    :class:`TorchTrain` owns (deterministic seeding, the batching loop,
    per-epoch :class:`~dskit.pipeline.trainlog.TrainingCurve` telemetry,
    the artifact + sidecar protocol) applied to an architecture chosen in
    config. ``torch-linear-train`` remains as the worked example of the
    subclassing route; this is the route that needs no Python at all.
    """


class DeclaredPredict(_DeclaredParams, _DeclaredModule, TorchPredict):
    """Inference for :class:`DeclaredTrain` artifacts. Registered as
    ``torch-predict``. The ``module``/``module_params`` it declares must
    match the sidecar's — the base refuses a mismatch by name."""


#: The pack's registerable kinds — CONCRETE classes only; the abstract
#: bases are subclass material, not kinds.
NODE_KINDS = (
    ("torch-train", DeclaredTrain),
    ("torch-predict", DeclaredPredict),
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
