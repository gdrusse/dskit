"""The scikit-learn library pack — the generic estimator doorway (D-146 tier 2).

Two Nodes and one signal object, docs/25 §2 row 2:

* :class:`SklearnFit` (role ``train``) — imports ANY estimator class by
  dotted path (``"sklearn.linear_model.Ridge"``), fits it on feature
  rows, persists the fitted model via joblib next to a provenance
  sidecar, and hands it downstream as a :class:`SklearnSignal`.
  ``mode="load"`` REALLY loads (docs/25 §2): sklearn persists, unlike
  the causal-beta family, so refusing would be wrong here — the pinned
  artifact is restored (hash-verified, identity-matched against this
  node's params) and NEVER refitted.
* :class:`SklearnPredict` (role ``signal``) — inference only, from a
  pinned ``params.artifact``. No mode gymnastics: it always loads, and a
  missing or unprovenanced artifact is refused by name.

This is a DOORWAY, not a model registry: the estimator is named by the
document, constructed from the document's own ``estimator_params``, and
nothing here hard-codes a family. A project's own problem-specific model
stacks are not forked by this pack — a document that wants one of those
wraps it in an adapter Node; this pack serves the plain "fit X, predict
with X" shape any project has.

The artifact format (``sklearn-joblib-v1``)::

    <dir>/model.joblib        # joblib.dump of the fitted estimator
    <dir>/model.joblib.json   # the sidecar: estimator path, constructor
                              # params, features, label, seed,
                              # predict_method, sha256 (see below),
                              # n_rows, library_version

**What ``sha256`` covers (S2-A):** the model bytes AND the sidecar's own
schema-bearing fields — sha256 over the ``.joblib`` bytes, a NUL byte,
then the canonical JSON (sorted keys, compact separators) of every
sidecar field except ``sha256`` itself (it cannot cover its own value)
and ``library_version`` (provenance, never identity — a version drift
logs and still restores). The sidecar IS schema: it supplies the feature
ORDER and the predict method :class:`SklearnPredict` serves with, so a
digest over the model file alone left the two exploits this fold closes
— a foreign estimator relabelled to pass as the declared one, and a
reordered feature list that silently transposed every prediction. Any
sidecar edit now fails the hash exactly like a model-file edit, refused
by name. Older ``sklearn-joblib-v1`` sidecars written under the
bytes-only digest no longer verify: refit to re-pin them.

The sidecar is what makes ``mode="load"`` honest: the loader refuses a
missing file, a missing sidecar, a content-hash mismatch, a restored
object that is not an instance of the estimator class the sidecar
declares, and any identity field that contradicts the loading node's
params — so a document can never claim it restored a model it did not.
Note that a joblib artifact is CODE (pickle): load only artifacts you
wrote.

Two knob caveats, stated up front:

* ``estimator_params`` is passed verbatim to the constructor. A typo'd
  KEY inside it ([[I-227]] territory — nested knobs are opaque to the
  plan-time validator) is caught by the constructor itself at fit time,
  refused with the estimator's own message; it cannot be caught at plan
  without importing the library.
* ``seed`` is honored only where the estimator accepts ``random_state``;
  a seed the estimator would never read is refused at run, by name — a
  recorded knob nothing consumed is a config lie.

Rows are the plain feature-row shape (list of dicts, or objects with
attributes): every declared ``features`` key and the ``label`` key must
be a finite number on every row — a row that cannot be trained on is
refused by name, never silently dropped. Any split/causality cut is the
DOCUMENT's job (an upstream ``filter`` node someone can read); this pack
fits exactly what was wired in.

**The estimator cookbook.** Because ``estimator`` is a declared param on
a ``train``-role node, a search space over ``model.estimator`` IS a model
sweep — ``examples/pipeline/model-sweep.json`` sweeps the six
``sklearn.`` rows below (the seventh needs an extra, so the shipped
example leaves it out) and picks a winner on the val split, fitting on
train rows only through a ``filter`` node the document declares. There
is deliberately no registry of per-model classes: each would re-do what
the doorway already does, and be one more place to drift. Spell the
estimator as a DOTTED import path (``module.ClassName``), never a colon
— and note WHERE the two spellings part company: a colon in a node's own
``estimator`` param is a plan-time shape problem, while a colon inside a
search SPACE is not (the planner never builds trial params), so that
document plans, hashes, and dies mid-run on the offending trial. Both
answers are pinned in ``tests/pipeline_libs/test_sklearn.py``.

| estimator | family | reach for it when |
|---|---|---|
| ``sklearn.linear_model.LinearRegression`` | linear | you want the honest baseline every other row must beat |
| ``sklearn.linear_model.Ridge`` | linear, penalized | features are collinear or wide relative to the row count |
| ``sklearn.ensemble.RandomForestRegressor`` | bagged trees | interactions and non-linearity, with little tuning |
| ``sklearn.ensemble.GradientBoostingRegressor`` | boosted trees | the same, traded for accuracy over fit time |
| ``sklearn.svm.SVR`` | kernel | few rows, smooth structure, features already scaled |
| ``sklearn.neighbors.KNeighborsRegressor`` | instance-based | local structure with no global form to assume |
| ``lightgbm.LGBMRegressor`` | boosted trees | many rows or many features — needs the ``lightgbm`` extra, no pack |

The ``lightgbm`` row is the only non-sklearn extra declared today; it is
the shape any other sklearn-compatible library WOULD take (declare the
extra in ``pyproject.toml``, then name the class — no pack, no wrapper),
which is how xgboost and catboost would enter. Do not reach for an extra
that is not in ``pyproject.toml``: the prose here is pinned against the
declared list. Classifier counterparts (``LogisticRegression``,
``RandomForestClassifier``, …) swap in the same way with
``predict_method="predict_proba"`` — but that seam is binary-only (it
serves ``P(classes_[1])``), and a third class is refused at PREDICT
time, long after the document planned and fitted.

One params block serves every candidate in a sweep, so it may carry only
knobs they ALL accept: ``estimator_params`` and ``seed`` are omitted from
the example on purpose (``LinearRegression``/``SVR``/``KNeighbors`` take
no ``random_state``, and this pack refuses a seed the estimator would
never read).

A sweep is only as honest as its cut. Because this pack fits exactly what
was wired in, a document that wires the FULL stream into the candidates
and scores them on its own val rows selects in-sample, and the ranking it
reports is a memorisation ranking — on the cookbook's synthetic market
that inverts the result outright (measured and pinned: the forest "wins"
at ~0.03 leaky and trails at 0.22-0.26 honest — the envelope measured
over 65 runs, quoted to two decimals, not a point, because that
candidate is unseeded, and usually last: the odd run edges it past the
boosted trees — while the plain linear baseline it beat becomes the
winner). Put the train cut upstream, in a node.

The metric rule is tier-1's, not this pack's: binary venues score
``logloss``/``brier``, mark-to-market venues the unbounded pair
(``dskit/pipeline/metrics.py``, ADR-0025). The cookbook is the
DOCUMENTED EXCEPTION to that rule, and here is its mechanism: raw
regressor ``predict`` beliefs are unclipped, and ``brier``'s domain
guard refuses any belief outside [0, 1] — on nearby data SVR emits
~1.025 (measured), so one such trial kills the whole sweep mid-run.
Until a calibration/clipping step exists between fit and validate, an
uncalibrated-regressor sweep on a binary venue scores ``squared_error``;
a ``predict_proba`` document keeps the venue rule as written, as
``examples/pipeline/sklearn-fit.json`` does with ``brier``.

**Feature selection comes through the same doorway** (``sklearn-select``,
ADR-0042). ``selector`` names the class — ``VarianceThreshold``,
``SelectKBest``, ``SelectPercentile``, ``RFE``, ``SelectFromModel``, or
one this file has never heard of — and the only requirement is that it
can say which columns survived, which in sklearn is ``get_support``. So
there are no per-selector wrapper classes here either, for the same
reason there are no per-model ones.

Two of sklearn's selector arguments cannot be spelled inside a JSON
kwargs block, because one is an estimator OBJECT and the other a
FUNCTION. Each therefore gets its OWN dotted-path knob on the node:
``estimator``/``estimator_params`` for the wrapper selectors (``RFE``,
``SelectFromModel``) and ``score_func`` for the univariate ones
(``mutual_info_regression``, ``f_regression``). A ``selector_params``
entry restating either is refused by name — two spellings of one
constructor argument would disagree, and a search space addressing the
node's knob would tune the loser. Supervision is DECLARED: ``label``
names the target column, and a selector that needs one when the document
declared none is refused with the library's own words quoted, because
sklearn's ``fit`` signature cannot be asked (``SelectKBest.fit`` spells
``y=None`` exactly as ``VarianceThreshold.fit`` does).

The cut is fitted on the declared split and nothing else — the family's
rule, inherited, not restated — and the surviving list is an artifact, so
serving projects the identical columns. The model BELOW reads it as
``"features": "$select.features"``: which columns survive is the fit's
answer, so no document can state it in advance.

Import cost: stdlib + ``dskit.pipeline`` only. sklearn and joblib are
imported inside the run path exclusively (``tests/pipeline/test_purity.py``
enforces it) so documents plan on machines without them.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from collections.abc import Mapping

from dskit.pipeline.fitted import FeatureSelector
from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    TrainableNode,
    reject_unknown_params,
)

__all__ = [
    "NODE_KINDS",
    "SklearnFit",
    "SklearnPredict",
    "SklearnSelect",
    "SklearnSignal",
    "register",
]

#: The on-disk artifact format this pack writes and the only one it loads.
_ARTIFACT_FORMAT = "sklearn-joblib-v1"

#: How a :class:`SklearnSignal` asks the estimator for a belief. Tuple,
#: not set: membership by equality never raises on unhashable junk.
_PREDICT_METHODS = ("predict", "predict_proba")

#: Sidecar keys the loader cannot proceed without.
_SIDECAR_REQUIRED = (
    "estimator",
    "features",
    "label",
    "n_rows",
    "predict_method",
    "sha256",
)

#: What a selector class must be able to do: fit, then say which columns
#: survived. sklearn spells the second one ``get_support``.
_SELECTOR_METHODS = ("fit", "get_support")

#: The example a missing/malformed ``selector`` path is refused against.
_SELECTOR_EXAMPLE = "sklearn.feature_selection.SelectKBest"

#: The selector constructor arguments that CANNOT be spelled in a JSON
#: kwargs block — one is an estimator OBJECT, the other a FUNCTION — so
#: each gets its own dotted-path knob on the node, mapped here to the
#: example its refusal quotes. Being the node's own knobs is what makes
#: them addressable by a search space; being listed HERE is what makes
#: ``selector_params`` refuse a second spelling of either.
_SELECTOR_PATH_KNOBS = {
    "estimator": "sklearn.linear_model.Ridge",
    "score_func": "sklearn.feature_selection.f_regression",
}

#: numpy's seed range — refusing outside it at PLAN beats a RandomState
#: ValueError after the feature matrix is already built.
_SEED_MAX = 2**32

#: Sidecar fields the content hash does NOT cover: ``sha256`` is where the
#: hash is recorded (covering it would chase its own tail), and
#: ``library_version`` is provenance the loader logs but never enforces.
#: EVERYTHING else — estimator, estimator_params, features, label,
#: predict_method, seed, n_rows, format — is hash material (S2-A).
_UNHASHED_SIDECAR_FIELDS = ("sha256", "library_version")


# ---------------------------------------------------------------------------
# Validation helpers (total: return problems, never raise)
# ---------------------------------------------------------------------------


#: Default-deny on this class's own knobs. One definition, in ``node.py``
#: beside the ``validate_params`` protocol it serves.
_reject_unknown = reject_unknown_params


def _import_path_problems(name, value, *, example):
    """Problems with a dotted import-path STRING — shape only, checked at
    plan; whether it imports is the run's question (the library may not
    exist on the planning machine, by design)."""
    if not isinstance(value, str) or not value:
        return [
            f"{name} must be a dotted import path string like {example!r}, got {value!r}"
        ]
    parts = value.split(".")
    if len(parts) < 2 or not all(p.isidentifier() for p in parts):
        return [
            f"{name} must be a dotted import path (module.ClassName) like "
            f"{example!r}, got {value!r}"
        ]
    return []


def _feature_list_problems(name, value):
    """Problems with a feature-key list — non-empty, distinct, non-empty
    strings. Duplicates would silently double a column's weight."""
    if not isinstance(value, list) or not value:
        return [f"{name} must be a non-empty list of row keys, got {value!r}"]
    problems, seen, dupes = [], set(), set()
    for item in value:
        if not isinstance(item, str) or not item:
            problems.append(f"{name} entries must be non-empty strings, got {item!r}")
            continue
        if item in seen:
            dupes.add(item)
        seen.add(item)
    if dupes:
        problems.append(
            f"{name} repeats {sorted(dupes)} — feature keys must be distinct"
        )
    return problems


def _seed_problems(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return [
            f"seed must be an int (omit the key for an unseeded fit), got {value!r}"
        ]
    if not 0 <= value < _SEED_MAX:
        return [f"seed must lie in [0, 2**32), got {value!r}"]
    return []


def _kwargs_problems(name, value):
    """Shape only: a dict with string keys. What is INSIDE is the
    constructor's contract — a typo'd nested key ([[I-227]]) surfaces as
    the constructor's own refusal at fit time, wrapped by name.

    One rule for every kwargs block this pack forwards to a library
    constructor (``estimator_params``, ``selector_params``): the shape
    question is identical, and a second copy would be the place the two
    drifted.
    """
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        return [
            f"{name} must be a dict of constructor kwargs with "
            f"string keys, got {value!r}"
        ]
    return []


def _predict_method_problems(value):
    if not isinstance(value, str) or value not in _PREDICT_METHODS:
        return [
            f"predict_method must be one of {list(_PREDICT_METHODS)}, got {value!r}"
        ]
    return []


# ---------------------------------------------------------------------------
# Row access + the feature matrix
# ---------------------------------------------------------------------------


def _row_value(row, name):
    """``(present, value)`` for one field — mappings by KEY, everything
    else by attribute. Mapping-first is load-bearing: a dict row with a
    feature named ``"items"`` must yield the VALUE, never the bound
    ``dict.items`` method an attr-first lookup would find."""
    if isinstance(row, Mapping):
        if name in row:
            return True, row[name]
        return False, None
    if hasattr(row, name):
        return True, getattr(row, name)
    return False, None


def _finite_number(value):
    """``float(value)`` when value is a finite real number (bools count,
    as 0/1); ``None`` otherwise. Strings are NOT coerced — ``"0.5"`` in a
    numeric column is corruption to report, not data to launder."""
    if not isinstance(value, (bool, int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _row_vector(row, index, columns, where):
    """One row's finite numbers for ``columns``, or a refusal naming the
    row and the key. Silently dropping a row would make ``n_rows`` a lie
    about what the library saw."""
    vector = []
    for name in columns:
        present, value = _row_value(row, name)
        if not present or value is None:
            raise ValueError(
                f"{where}: row {index} carries no {name!r} — every row must "
                "carry every column the fit reads (cut or repair the stream "
                "upstream; a silently dropped row would misreport the fit)"
            )
        number = _finite_number(value)
        if number is None:
            raise ValueError(
                f"{where}: row {index} field {name!r} is {value!r}, not a "
                "finite number — corrupt input, refused by name"
            )
        vector.append(number)
    return vector


def _refuse_zero_rows(rows, where):
    """Refuse an empty fit stream by name."""
    if not rows:
        raise ValueError(
            f"{where}: cannot fit on zero rows — wire a non-empty rows input"
        )


def _fit_matrix(rows, features, label, where):
    """``(X, y)`` from the wired rows — every feature and the label, on
    every row, or a refusal naming the row and the key."""
    _refuse_zero_rows(rows, where)
    matrix, targets = [], []
    for i, row in enumerate(rows):
        vector = _row_vector(row, i, (*features, label), where)
        matrix.append(vector[:-1])
        targets.append(vector[-1])
    return matrix, targets


def _design_matrix(rows, features, where):
    """``X`` alone, by the same row rule — what an UNSUPERVISED fit reads
    (a variance threshold has no target to be given)."""
    _refuse_zero_rows(rows, where)
    return [_row_vector(row, i, features, where) for i, row in enumerate(rows)]


# ---------------------------------------------------------------------------
# The run-path imports (heavy, function-local by doctrine)
# ---------------------------------------------------------------------------


def _import_object(path, where, subject="estimator"):
    """The object behind a dotted path — or a refusal naming the path.

    Import errors here are the honest 'library not installed / path typo'
    answer, delivered at execute where the library is due. ``subject``
    names what the document was pointing at, because a pack that resolves
    THREE kinds of path (an estimator class, a selector class, a scoring
    FUNCTION) must say which one it could not find.
    """
    import importlib

    module_name, _, attr_name = path.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(
            f"{where}: cannot import {subject} {path!r} ({exc}) — is the "
            "library installed on this machine, and the path spelled as "
            "module.ClassName?"
        ) from exc
    obj = getattr(module, attr_name, None)
    if obj is None:
        raise ValueError(
            f"{where}: module {module_name!r} has no attribute {attr_name!r} — "
            f"{subject} {path!r} does not exist"
        )
    return obj


def _import_estimator(path, where):
    """The estimator CLASS behind a dotted path — or a refusal naming the
    path. An estimator is a thing that fits."""
    est_cls = _import_object(path, where)
    if not callable(getattr(est_cls, "fit", None)):
        raise ValueError(f"{where}: {path!r} has no fit method — not an estimator")
    return est_cls


def _import_selector(path, where):
    """The selector CLASS behind a dotted path — or a refusal naming the
    path and the method it lacks.

    A selector is an estimator that also REPORTS which columns survived,
    which in sklearn is spelled ``get_support``. Every selector in the
    library has it (``VarianceThreshold``, ``SelectKBest``, ``RFE``,
    ``SelectFromModel``, …), so requiring it is how this doorway stays a
    doorway instead of a registry of the classes someone remembered.
    """
    cls_ = _import_object(path, where, subject="selector")
    for method in _SELECTOR_METHODS:
        if not callable(getattr(cls_, method, None)):
            raise ValueError(
                f"{where}: {path!r} has no {method} method — a feature "
                f"selector must fit and then report which columns survived "
                f"({'/'.join(_SELECTOR_METHODS)}); {path!r} is not one"
            )
    return cls_


def _import_callable(path, where, subject):
    """The FUNCTION behind a dotted path — or a refusal naming it. A
    scoring function is passed to the selector, never instantiated."""
    obj = _import_object(path, where, subject=subject)
    if not callable(obj):
        raise ValueError(
            f"{where}: {subject} {path!r} is not callable — it must be a "
            "function the selector can score columns with"
        )
    return obj


def _construct(cls_, kwargs, path, where, kwargs_name):
    """Instantiate ``cls_`` with ``kwargs`` — or refuse naming the path
    and the block the kwargs came from."""
    try:
        return cls_(**kwargs)
    except TypeError as exc:
        raise ValueError(
            f"{where}: {path} rejected {kwargs_name} ({exc}) — a typo'd "
            "nested knob is caught here, by the constructor, not at plan "
            "(I-227)"
        ) from exc


def _content_hash(path, sidecar):
    """The artifact's identity: model bytes + the sidecar's schema (S2-A).

    sha256 over the ``.joblib`` bytes, a NUL separator, then the canonical
    JSON of every sidecar field outside
    :data:`_UNHASHED_SIDECAR_FIELDS`. Folding the sidecar in is what makes
    a relabelled estimator or a reordered feature list fail verification
    instead of loading clean — the sidecar IS the schema this pack serves
    with, so it must be as tamper-evident as the model file.
    """
    material = {k: v for k, v in sidecar.items() if k not in _UNHASHED_SIDECAR_FIELDS}
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    digest.update(b"\0")
    digest.update(
        json.dumps(
            material, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    )
    return digest.hexdigest()


def _load_artifact(artifact, where):
    """``(estimator, sidecar)`` from a pinned artifact — or a refusal
    naming exactly what is wrong. Every refusal names the artifact (and
    the load), so a document can never half-load in silence."""
    if not isinstance(artifact, str) or not artifact:
        raise ValueError(
            f"{where}: loading requires a pinned artifact path, got {artifact!r}"
        )
    if not os.path.isfile(artifact):
        raise ValueError(
            f"{where}: artifact {artifact!r} does not exist — nothing to load"
        )
    sidecar_path = artifact + ".json"
    if not os.path.isfile(sidecar_path):
        raise ValueError(
            f"{where}: artifact sidecar {sidecar_path!r} is missing — refusing "
            "to load a model whose provenance cannot be verified"
        )
    try:
        with open(sidecar_path, encoding="utf-8") as fh:
            sidecar = json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"{where}: artifact sidecar {sidecar_path!r} is not readable JSON "
            f"({exc}) — refusing to load"
        ) from exc
    if not isinstance(sidecar, dict) or sidecar.get("format") != _ARTIFACT_FORMAT:
        raise ValueError(
            f"{where}: artifact {artifact!r} sidecar declares format "
            f"{sidecar.get('format') if isinstance(sidecar, dict) else sidecar!r}, "
            f"this pack loads {_ARTIFACT_FORMAT!r} only"
        )
    missing = [k for k in _SIDECAR_REQUIRED if k not in sidecar]
    if missing:
        raise ValueError(
            f"{where}: artifact {artifact!r} sidecar is missing {missing} — "
            "refusing to load an under-described model"
        )
    problems = _feature_list_problems("features", sidecar["features"])
    problems += _predict_method_problems(sidecar["predict_method"])
    # Shape-checked BEFORE it is imported below: the estimator path is now
    # read on the load path too, and junk there must refuse by name rather
    # than crash inside the importer.
    problems += _import_path_problems(
        "estimator", sidecar["estimator"], example="sklearn.linear_model.Ridge"
    )
    n_rows = sidecar["n_rows"]
    if isinstance(n_rows, bool) or not isinstance(n_rows, int) or n_rows < 0:
        problems.append(f"n_rows must be a non-negative int, got {n_rows!r}")
    if problems:
        raise ValueError(
            f"{where}: artifact {artifact!r} sidecar is malformed: "
            + "; ".join(problems)
        )
    actual = _content_hash(artifact, sidecar)
    if actual != sidecar["sha256"]:
        raise ValueError(
            f"{where}: artifact {artifact!r} content hash {actual[:16]}… does "
            f"not match its sidecar ({str(sidecar['sha256'])[:16]}…) — the "
            "model file or its sidecar changed since it was written (the "
            "hash covers both); refusing to load"
        )
    declared = _import_estimator(sidecar["estimator"], where)
    import joblib

    try:
        estimator = joblib.load(artifact)
    except Exception as exc:  # noqa: BLE001 - any unpickling failure is the answer
        raise ValueError(
            f"{where}: artifact {artifact!r} failed to load ({exc}) — the "
            "file is not a joblib model this environment can restore"
        ) from exc
    if not isinstance(estimator, declared):
        raise ValueError(
            f"{where}: artifact {artifact!r} restored a "
            f"{type(estimator).__name__}, but its sidecar declares "
            f"{sidecar['estimator']!r} — refusing a relabelled model (the "
            "estimator class is identity, not a label)"
        )
    return estimator, sidecar


def _identity_mismatches(sidecar, params):
    """Fields where the pinned artifact contradicts the loading node's
    params — a load that ran anyway would be a document lying about which
    model it used."""
    declared = {
        "estimator": params.get("estimator"),
        "estimator_params": params.get("estimator_params") or {},
        "features": list(params.get("features") or []),
        "label": params.get("label"),
        "predict_method": params.get("predict_method", "predict"),
        "seed": params.get("seed"),
    }
    out = []
    for name, want in declared.items():
        got = sidecar.get(name)
        if got != want:
            out.append(f"{name}: artifact carries {got!r}, params declare {want!r}")
    return out


# ---------------------------------------------------------------------------
# The signal seam
# ---------------------------------------------------------------------------


class SklearnSignal:
    """One fitted estimator behind the toolkit's ``predict(record)`` seam.

    Adapts a record (dict or attribute object) to the estimator's feature
    vector using the fit-time ``features`` order. ``predict`` DECLINES
    (returns ``None`` — "no coverage", which the owned ``validate`` kind
    skips) when any feature is absent, ``None``, or non-finite: a missing
    input is never turned into a fabricated belief. A feature that is
    present but not a number RAISES — that is corruption, not coverage.

    ``predict_method="predict_proba"`` returns the probability of the
    POSITIVE class (``classes_[1]``) and is binary-only; anything else
    refuses by name rather than guessing which of N columns is a belief.

    Provenance rides on the object: ``artifact_path`` is the model file
    this signal came from, and ``loaded`` says whether it was RESTORED
    from a pinned artifact (True) or fitted fresh this run (False) — the
    fields a conformance ``verify_loaded`` can interrogate.
    """

    __slots__ = ("artifact_path", "estimator", "features", "loaded", "predict_method")

    def __init__(self, estimator, features, predict_method, artifact_path, *, loaded):
        self.estimator = estimator
        self.features = tuple(features)
        self.predict_method = predict_method
        self.artifact_path = artifact_path
        self.loaded = bool(loaded)

    def predict(self, record):
        """The estimator's belief for one record, or ``None`` for no
        coverage."""
        vector = []
        for name in self.features:
            present, value = _row_value(record, name)
            if not present or value is None:
                return None  # no coverage — never a fabricated belief
            number = _finite_number(value)
            if number is None:
                if isinstance(value, (bool, int, float)):
                    return None  # recorded non-finite: a miss, not coverage
                raise ValueError(
                    f"record field {name!r} is {value!r}, not a number — "
                    "corrupt input, not missing coverage"
                )
            vector.append(number)
        if self.predict_method == "predict_proba":
            row = list(self.estimator.predict_proba([vector])[0])
            if len(row) != 2:
                raise ValueError(
                    f"predict_proba returned {len(row)} classes — the "
                    "probability seam is binary-only (the belief is "
                    "P(classes_[1])); use predict_method='predict' for "
                    "multi-class or regression estimators"
                )
            return float(row[1])
        return float(self.estimator.predict([vector])[0])


# ---------------------------------------------------------------------------
# The nodes
# ---------------------------------------------------------------------------


def _rows_problems(rows):
    """The wired ``rows`` port must be a materialized list — one message,
    written once, for every mode that reads it."""
    if isinstance(rows, list):
        return []
    return [
        "rows must be a list of feature rows (a one-shot iterable "
        f"would be consumed by validation), got {rows!r}"
    ]


class SklearnFit(TrainableNode):
    """Fit any sklearn-style estimator on feature rows (role ``train``).

    ``mode`` is honored for real, both ways. ``"train"`` (or unset) fits
    fresh on the wired ``rows`` and persists the fitted model —
    ``model.joblib`` plus its provenance sidecar — under this node's
    artifact dir. ``"load"`` restores the pinned ``artifact`` — sidecar
    present, content hash intact, identity fields matching this node's
    params — and NEVER refits; the wired rows are ignored (and may be
    omitted entirely). The restored signal carries ``loaded=True`` /
    ``artifact_path=<the pin>`` and ``metrics.loaded == 1.0``, so a run
    that claims it loaded can be checked.

    Outputs: ``signal`` (a :class:`SklearnSignal`), ``artifact_path``
    (the model file this run fitted or restored), ``metrics``.
    """

    role = "train"
    outputs = ("signal", "artifact_path", "metrics")

    _PARAMS = (
        "estimator",
        "estimator_params",
        "features",
        "label",
        "predict_method",
        "seed",
    )

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        if "estimator" not in params:
            problems.append(
                "estimator is required — the dotted import path of the "
                "estimator class, e.g. 'sklearn.linear_model.Ridge'"
            )
        else:
            problems += _import_path_problems(
                "estimator", params["estimator"], example="sklearn.linear_model.Ridge"
            )
        if "features" not in params:
            problems.append("features is required — the row keys the estimator reads")
        else:
            problems += _feature_list_problems("features", params["features"])
        label = params.get("label")
        if "label" not in params:
            problems.append("label is required — the row key holding the target")
        elif not isinstance(label, str) or not label:
            problems.append(f"label must be a non-empty row key, got {label!r}")
        estimator_params = params.get("estimator_params", {})
        problems += _kwargs_problems("estimator_params", estimator_params)
        if "seed" in params:
            problems += _seed_problems(params["seed"])
            if (
                isinstance(estimator_params, dict)
                and "random_state" in estimator_params
            ):
                problems.append(
                    "seed and estimator_params.random_state are both set — one "
                    "source of truth; drop one"
                )
        if "predict_method" in params:
            problems += _predict_method_problems(params["predict_method"])
        return problems

    def validate_train_inputs(self, inputs):
        return _rows_problems(inputs.get("rows"))

    def validate_load_inputs(self, inputs):
        rows = inputs.get("rows")
        if rows is None:
            return []  # a load never reads them; a document may omit the wire
        return _rows_problems(rows)

    # -- mode="load" ---------------------------------------------------------

    def run_load(self, ctx, inputs):
        estimator, sidecar = _load_artifact(self.artifact, self.key)
        mismatches = _identity_mismatches(sidecar, self.params)
        if mismatches:
            raise ValueError(
                f"{self.key}: mode='load' artifact {self.artifact!r} does not "
                "match this node's params — refusing to load a different "
                "model than the document declares: " + "; ".join(mismatches)
            )
        recorded = sidecar.get("library_version")
        current = self._library_version(self.params["estimator"])
        if recorded is not None and current is not None and recorded != current:
            self.log.info(
                "loaded artifact was written under library version %s, this "
                "environment runs %s — joblib restored it, but retrain to be sure",
                recorded,
                current,
            )
        signal = SklearnSignal(
            estimator,
            sidecar["features"],
            sidecar["predict_method"],
            self.artifact,
            loaded=True,
        )
        self.log.info(
            "restored %s from %s (fitted on %d row(s); never refit)",
            sidecar["estimator"],
            self.artifact,
            sidecar["n_rows"],
        )
        return {
            "signal": signal,
            "artifact_path": self.artifact,
            "metrics": {
                "loaded": 1.0,
                "n_features": float(len(sidecar["features"])),
                "n_rows": float(sidecar["n_rows"]),
            },
        }

    # -- mode="train" (or unset) ----------------------------------------------

    def run_train(self, ctx, inputs):
        params = self.params
        path = params["estimator"]
        features = list(params["features"])
        label = params["label"]
        predict_method = params.get("predict_method", "predict")
        est_cls = _import_estimator(path, self.key)
        kwargs = dict(params.get("estimator_params") or {})
        estimator = _construct(est_cls, kwargs, path, self.key, "estimator_params")
        self._apply_seed(estimator, path)
        matrix, targets = _fit_matrix(inputs["rows"], features, label, self.key)
        estimator.fit(matrix, targets)

        import joblib

        model_path = os.path.join(self.artifact_dir(ctx), "model.joblib")
        joblib.dump(estimator, model_path)
        sidecar = {
            "format": _ARTIFACT_FORMAT,
            "estimator": path,
            "estimator_params": kwargs,
            "features": features,
            "label": label,
            "predict_method": predict_method,
            "seed": params.get("seed"),
            "n_rows": len(matrix),
            "library_version": self._library_version(path),
        }
        # Hashed LAST, over the material above: the digest covers the model
        # bytes and every schema-bearing sidecar field (S2-A).
        sidecar["sha256"] = _content_hash(model_path, sidecar)
        self.write_artifact(ctx, "model.joblib.json", sidecar)
        self.log.info(
            "fitted %s on %d row(s) x %d feature(s) -> %s",
            path,
            len(matrix),
            len(features),
            model_path,
        )
        signal = SklearnSignal(
            estimator, features, predict_method, model_path, loaded=False
        )
        return {
            "signal": signal,
            "artifact_path": model_path,
            "metrics": {
                "loaded": 0.0,
                "n_features": float(len(features)),
                "n_rows": float(len(matrix)),
            },
        }

    def _apply_seed(self, estimator, path):
        """Thread ``seed`` into ``random_state`` — or refuse by name when
        the estimator would never read it (a knob nothing consumes is a
        config lie)."""
        seed = self.params.get("seed")
        if seed is None:
            return
        get_params = getattr(estimator, "get_params", None)
        if callable(get_params) and "random_state" in get_params():
            estimator.set_params(random_state=seed)
            return
        raise ValueError(
            f"{self.key}: seed={seed} but {path} accepts no random_state — "
            "drop the seed, or choose an estimator that takes one (a seed "
            "the estimator never reads is a config lie)"
        )

    @staticmethod
    def _library_version(path):
        """The estimator's top-level library version, when knowable —
        provenance for the sidecar, never identity."""
        module = sys.modules.get(path.split(".", 1)[0])
        version = getattr(module, "__version__", None)
        return version if isinstance(version, str) else None


class SklearnPredict(TrainableNode):
    """Inference-only: the signal behind a pinned artifact (role ``signal``).

    Always loads — the same verified path :class:`SklearnFit` uses under
    ``mode="load"`` — from ``params.artifact``; the sidecar supplies the
    feature order and predict method, so nothing about the fit is
    restated (or restatable, wrongly) in the document. ``default_mode``
    is ``"load"``, so an unset ``mode`` loads; ``mode="train"`` is refused
    by name (this node never fits — that is :class:`SklearnFit`'s job),
    and a node-level ``artifact`` that contradicts ``params.artifact`` is
    refused rather than silently picking one.
    """

    role = "signal"
    outputs = ("signal",)
    default_mode = "load"

    _PARAMS = ("artifact",)

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        artifact = params.get("artifact")
        if "artifact" not in params:
            problems.append(
                "artifact is required — the pinned model file this node serves"
            )
        elif not isinstance(artifact, str) or not artifact:
            problems.append(
                f"artifact must be a non-empty path string, got {artifact!r}"
            )
        return problems

    def run_train(self, ctx, inputs):
        raise ValueError(
            f"{self.key}: mode='train' — this node never fits; it always "
            "loads its pinned params.artifact (train with sklearn-fit)"
        )

    def run_load(self, ctx, inputs):
        pinned = self.pinned_artifact(
            self.params.get("artifact"),
            missing=(
                "no artifact reference — this node always loads; set "
                "params.artifact (mode='load' may restate it)"
            ),
        )
        estimator, sidecar = _load_artifact(pinned, self.key)
        self.log.info(
            "serving %s from %s (fitted on %d row(s))",
            sidecar["estimator"],
            pinned,
            sidecar["n_rows"],
        )
        return {
            "signal": SklearnSignal(
                estimator,
                sidecar["features"],
                sidecar["predict_method"],
                pinned,
                loaded=True,
            )
        }


class SklearnSelect(FeatureSelector):
    """Choose which candidate columns survive, with any sklearn selector.

    A member of the fitted-transform family (ADR-0040) through
    :class:`~dskit.pipeline.fitted.FeatureSelector` (ADR-0042): the base
    owns the whole envelope — fitting on the DECLARED split and nothing
    else, canonical ordering, the persisted column list, the load-mode
    restore that never refits — and this class supplies the one hook,
    :meth:`surviving_features`.

    The selector arrives BY IMPORT PATH, the same doorway
    :class:`SklearnFit` opens for estimators, so the pack ships no
    per-selector wrapper class and a selector this file has never heard
    of works the day the library ships it. What sklearn cannot express in
    a JSON kwargs block gets its own path knob: ``estimator`` for the
    wrapper selectors that take an inner model (``RFE``,
    ``SelectFromModel``) and ``score_func`` for the univariate ones that
    take a scoring function (``SelectKBest``, ``SelectPercentile``).
    Everything else is the selector's own constructor kwargs, forwarded
    verbatim and validated by the constructor.

    Supervision is declared, not guessed: ``label`` names the target
    column when the selector needs one, and is absent when it does not
    (a variance threshold reads no target). A supervised selector with no
    label refuses by name rather than raising sklearn's positional-arg
    ``TypeError``.

    Parameters
    ----------
    params : dict
        ``selector`` (dotted import path, required), ``selector_params``
        (dict of its constructor kwargs, default ``{}``), ``estimator`` /
        ``estimator_params`` (the inner model for a wrapper selector,
        optional), ``score_func`` (dotted path to a scoring function,
        optional), ``label`` (target column, optional), plus
        :class:`~dskit.pipeline.fitted.FeatureSelector`'s ``features``
        (the candidates, required) and the family's ``fit_split`` /
        ``order_field`` / ``purity_check``.

    Examples
    --------
    Keep the two candidates mutual information likes best, learned from
    the train split alone::

        node = SklearnSelect("select", {
            "fit_split": "train",
            "features": ["ret_lag_0", "ret_lag_1", "spread"],
            "selector": "sklearn.feature_selection.SelectKBest",
            "selector_params": {"k": 2},
            "score_func": "sklearn.feature_selection.mutual_info_regression",
            "label": "y",
        })
        out = node.run(ctx, {"rows": rows})
        # -> out["features"] == ["ret_lag_0", "spread"]
    """

    _PARAMS = FeatureSelector._PARAMS + (
        "estimator",
        "estimator_params",
        "label",
        "score_func",
        "selector",
        "selector_params",
    )

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            The family's problems, plus one per broken knob of this
            pack's own: a missing or malformed ``selector`` /
            ``estimator`` / ``score_func`` path, a kwargs block that is
            not a string-keyed dict, a non-string ``label``, and a
            ``selector_params`` entry restating a knob this node owns.
        """
        problems = super().validate_params(params)
        if "selector" not in params:
            problems.append(
                "selector is required — the dotted import path of the "
                f"selector class, e.g. {_SELECTOR_EXAMPLE!r}"
            )
        else:
            problems += _import_path_problems(
                "selector", params["selector"], example=_SELECTOR_EXAMPLE
            )
        problems += cls._path_knob_problems(params)
        problems += _kwargs_problems("estimator_params",
                                     params.get("estimator_params", {}))
        label = params.get("label")
        if label is not None and (not isinstance(label, str) or not label):
            problems.append(
                f"label must be a non-empty row key naming the target the "
                f"selector supervises on (omit it for an unsupervised "
                f"selector), got {label!r}"
            )
        return problems

    @classmethod
    def _path_knob_problems(cls, params):
        """The optional path knobs, and the second-spelling rule."""
        problems = []
        for knob, example in _SELECTOR_PATH_KNOBS.items():
            if knob in params:
                problems += _import_path_problems(knob, params[knob], example=example)
        kwargs = params.get("selector_params", {})
        problems += _kwargs_problems("selector_params", kwargs)
        if not isinstance(kwargs, dict):
            return problems
        for knob in _SELECTOR_PATH_KNOBS:
            if knob in kwargs:
                problems.append(
                    f"selector_params carries {knob!r}, which this node owns "
                    f"as its own knob — declare it as params.{knob} (a dotted "
                    "import path). Two spellings of one constructor argument "
                    "would disagree, and a search space addressing the node's "
                    "knob would tune the loser"
                )
        return problems

    # -- the ONE hook (ADR-0042) --------------------------------------------

    def surviving_features(self, rows, params):
        """Fit the declared selector on ``rows`` and report the survivors.

        Parameters
        ----------
        rows : list
            The rows of the declared ``fit_split``, and nothing else —
            the base cut them, which is the whole leakage guarantee. The
            matrix is built HERE, from these rows, so there is no wider
            stream for the library to see.
        params : dict
            ``self.params``, passed through by the base.

        Returns
        -------
        list of str
            The candidates whose ``get_support`` mask is true.

        Raises
        ------
        ValueError
            When the selector path does not import, names a class that
            cannot report its support, rejects its kwargs, or needs a
            target the document declared no ``label`` for.
        """
        candidates = list(self.features())
        selector = self._build_selector()
        matrix = _design_matrix(rows, candidates, self.key)
        self._fit_selector(selector, matrix, self._targets(rows))
        support = list(selector.get_support())
        if len(support) != len(candidates):
            raise ValueError(
                f"{self.key}: {params['selector']} reported a support mask of "
                f"{len(support)} column(s) for {len(candidates)} candidate(s) "
                "— the mask must name one bool per candidate"
            )
        return [name for name, keep in zip(candidates, support) if keep]

    def _build_selector(self):
        """The constructed selector, with its path knobs resolved."""
        params = self.params
        path = params["selector"]
        cls_ = _import_selector(path, self.key)
        kwargs = dict(params.get("selector_params") or {})
        if "estimator" in params:
            inner = params["estimator"]
            kwargs["estimator"] = _construct(
                _import_estimator(inner, self.key),
                dict(params.get("estimator_params") or {}),
                inner,
                self.key,
                "estimator_params",
            )
        if "score_func" in params:
            kwargs["score_func"] = _import_callable(
                params["score_func"], self.key, "score_func"
            )
        return _construct(cls_, kwargs, path, self.key, "selector_params")

    def _targets(self, rows):
        """The target column for a supervised selector, or ``None``."""
        label = self.params.get("label")
        if label is None:
            return None
        return [_row_vector(row, i, (label,), self.key)[0]
                for i, row in enumerate(rows)]

    def _fit_selector(self, selector, matrix, targets):
        """Fit it — supervised exactly when a label was declared.

        The unsupervised call is wrapped because sklearn's own refusal
        does not say what to DO about it, and cannot be anticipated: a
        supervised selector is not identifiable from its ``fit``
        signature (``SelectKBest.fit`` spells ``y=None``, exactly like
        ``VarianceThreshold.fit``), and which class needs a target is a
        library tag this pack will not mirror. So the library's own words
        are quoted verbatim and the fix — declare ``label`` — is named
        beside them.
        """
        if targets is not None:
            selector.fit(matrix, targets)
            return
        try:
            selector.fit(matrix)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{self.key}: {self.params['selector']} refused a fit with no "
                f"target ({exc}) — if this selector supervises on one, declare "
                "the 'label' knob naming the row key that holds it"
            ) from exc


# ---------------------------------------------------------------------------
# Registration (explicit — importing this pack registers nothing)
# ---------------------------------------------------------------------------

#: Kind name -> class, the pack's registration table. Import-path
#: references (``"dskit.pipeline.libs.sklearn:SklearnFit"``) work with
#: no registration at all.
NODE_KINDS = (
    ("sklearn-fit", SklearnFit),
    ("sklearn-predict", SklearnPredict),
    ("sklearn-select", SklearnSelect),
)


def register(registry=None) -> None:
    """Claim the ``sklearn-*`` kind names in ``registry`` (default
    :data:`~dskit.pipeline.node.DEFAULT_NODE_KINDS`). Idempotent: a
    name already present is SKIPPED, never shadowed. Called explicitly by
    users — never at import time (the libs ``__init__`` doctrine)."""
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls)
