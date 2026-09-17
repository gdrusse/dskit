"""The scikit-learn library pack — the generic estimator doorway (D-146 tier 2).

Three Nodes and one signal object, docs/25 §2 row 2:

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
* :class:`SklearnSelect` (role ``fitted_transform``) — ADR-0042 doorway:
  a :class:`~dskit.pipeline.fitted.FeatureSelector` whose rule is a
  dotted selector path (and optional inner ``estimator``).

Plus one plain estimator wrapper, not a Node: :class:`ColumnSubsetEstimator`
(ADR-0108) fits any estimator — named the same dotted way — on a declared
``drop``/``keep`` subset of columns, so a feature MASK is an ordinary
``estimator_params`` knob beside a candidate's hyperparameters rather than a
second feature pipeline upstream. It forwards the surviving column names
(as ``feature_names`` or, failing that, LightGBM's own singular
``feature_name``) and ``categorical_feature`` to the inner estimator,
RE-INDEXED to the surviving columns, so a native categorical such as
``symbol_code`` stays declared correctly after masking shifts its
position. It needs a CALLER that forwards those two kwargs into its own
``fit`` — :class:`SklearnFit` does not, so this class reaches an
estimator only through a caller written to do so (``intraday_equities``'s
``NoInformationScan``, not this pack's own train node).

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
import re
import sys
from collections.abc import Mapping

from dskit.pipeline.fitted import FeatureSelector, FittedTransform
from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    TrainableNode,
    atomic_write,
    reject_unknown_params,
)
from dskit.pipeline.records import number_ok

__all__ = [
    "ColumnSubsetEstimator",
    "EstimatorBundle",
    "NODE_KINDS",
    "SklearnFit",
    "SklearnPredict",
    "SklearnSelect",
    "SklearnSignal",
    "load_bundle",
    "register",
    "write_bundle",
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
# Dimensionality reduction (ADR-0149) — the closed-catalog state constants
# ---------------------------------------------------------------------------

#: The tag a reduction state carries, and the only one a load accepts. The
#: ``_ARTIFACT_FORMAT`` precedent: an identity a reader can refuse by name
#: beats a shape it has to guess at.
REDUCTION_SCHEMA = "dskit.sklearn-reduction/v1"

#: The closed catalog :class:`SklearnReduction` fits. An arbitrary import
#: path is deliberately excluded: this node EXTRACTS ``components_`` (and,
#: for pca, ``mean_``) into JSON, and only these two expose that extraction
#: behind a projection that is a plain matrix multiply.
_REDUCTION_ALGORITHMS = ("pca", "svd")

#: Catalog member -> the dotted path it resolves through, the same
#: doorway :class:`SklearnFit` opens for an estimator.
_REDUCTION_PATHS = {
    "pca": "sklearn.decomposition.PCA",
    "svd": "sklearn.decomposition.TruncatedSVD",
}

#: The fitted attributes each member's extracted state is read from:
#: ``(components, mean)``. TruncatedSVD never centres, so its mean is
#: ``None`` and the state omits the key — the omission IS the record.
_REDUCTION_ATTRIBUTES = {
    "pca": ("components_", "mean_"),
    "svd": ("components_", None),
}

#: The seed a fit runs under when the document declares none. Named once:
#: a literal in both the validator and the fit path is how a run comes to
#: be seeded by a number nothing recorded.
DEFAULT_REDUCTION_SEED = 0

#: The prefix of the projected columns an output row gains. One name, so
#: the writer and the collision refusal agree on what the columns are
#: called; a knob to rename it is out of ADR-0149's scope.
_COMPONENT_PREFIX = "component"

#: The constructor knobs this node already owns, spelled a second way
#: inside ``algorithm_params``, mapped to the reason each is refused. Two
#: spellings of one argument would disagree, and a search space addressing
#: the node's knob would tune the loser — so ``n_components`` refuses a
#: second spelling of the top-level knob, and ``random_state``/``seed``
#: both refuse a second spelling of the node's ``seed`` (which threads as
#: ``random_state``). ``whiten`` is refused for a different reason: it
#: changes PCA's projection to need ``singular_values_`` in the state,
#: which this node does not store. The validator reads THIS table — one
#: owner, so a name added here is refused the same day, and a name dropped
#: here stops being refused.
_REDUCTION_SHADOWED_KNOBS = {
    "n_components": "declare params.n_components instead, the one knob this "
                    "node threads and records",
    "random_state": "declare params.seed instead, the one knob this node "
                    "threads as random_state",
    "seed": "declare params.seed instead, the one knob this node threads as "
            "random_state",
    "whiten": "whitened PCA projects with components_.T scaled by the "
              "singular values, which this node does not store — it computes "
              "a plain matrix multiply",
}


def _reduction_number(value):
    """A component/mean coordinate as a finite ``float``, or ``None``.

    The envelope's own number rule (:func:`~dskit.pipeline.records.number_ok`,
    which excludes ``bool``) plus the conversion the JSON state stores.
    ``numpy.float64`` passes because it IS a ``float`` subclass; a narrower
    scalar coming back would mean the estimator invented a precision this
    node never gave it, and refusing by name beats storing a value the JSON
    state cannot round-trip.
    """
    return float(value) if number_ok(value) else None


def _reduction_rows(value):
    """``value`` as a list of lists (numpy rows included), or ``None``.

    A string is iterable but is not a list of points, so both the outer
    and the inner shape must be a sequence rather than merely iterable.
    """
    if isinstance(value, (str, bytes, Mapping)):
        return None
    try:
        rows = list(value)
    except TypeError:
        return None
    out = []
    for row in rows:
        if isinstance(row, (str, bytes, Mapping)):
            return None
        try:
            out.append(list(row))
        except TypeError:
            return None
    return out


def _reduction_flat(value):
    """``value`` as a flat list of scalars (a numpy 1-D row), or ``None``."""
    if isinstance(value, (str, bytes, Mapping)):
        return None
    try:
        return list(value)
    except TypeError:
        return None


def _reduction_geometry_problems(components, mean, width, n_components, *, mean_expected):
    """Ways a ``(components, mean)`` pair is not a usable reduction.

    ONE rule asked TWICE: of what a fit just extracted from an estimator,
    and of what a load restored from JSON. A second copy is exactly where a
    state this version writes and the next refuses would come from.

    Parameters
    ----------
    components : list of list, or None
        The component points, already normalized by :func:`_reduction_rows`;
        ``None`` means the value was not a list of points at all.
    mean : object
        The centering vector RAW. Ignored when ``mean_expected`` is false.
    width : int
        The number of declared features every component and the mean span.
    n_components : int
        The declared projected width the components must equal.
    mean_expected : bool
        Whether a mean is required (``pca``) — when true, ``mean`` must
        flatten to a width-length list of finite numbers, and a ``None`` or
        scalar is refused rather than conflated with an absent one.

    Returns
    -------
    list of str
        One problem per broken invariant; empty when the pair is usable.
    """
    if components is None:
        return ["components is not a list of points"]
    problems = []
    if len(components) != n_components:
        problems.append(
            f"components has {len(components)} row(s), n_components is "
            f"{n_components} — sklearn clamps a too-large n_components to "
            "the sample count, and a silent clamp would emit fewer columns "
            "than the document declared"
        )
    for i, row in enumerate(components):
        if len(row) != width:
            problems.append(
                f"components[{i}] has {len(row)} value(s), not the {width} "
                "declared feature(s)"
            )
        for j, value in enumerate(row):
            if _reduction_number(value) is None:
                problems.append(
                    f"components[{i}][{j}] is {value!r}, not a finite number"
                )
    if mean_expected:
        mean_flat = _reduction_flat(mean)
        if mean_flat is None:
            problems.append(f"mean is {mean!r}, not a list of numbers")
        else:
            if len(mean_flat) != width:
                problems.append(
                    f"mean has {len(mean_flat)} value(s), not the {width} "
                    "declared feature(s)"
                )
            for j, value in enumerate(mean_flat):
                if _reduction_number(value) is None:
                    problems.append(
                        f"mean[{j}] is {value!r}, not a finite number"
                    )
    return problems

# ---------------------------------------------------------------------------
# The multi-head bundle artifact (ADR-0114 Phase 2) — one joblib file
# holding a NAMED MAPPING of fitted estimators, plus one JSON manifest.
# S2-A generalized: the digest covers the joblib bytes AND every
# schema-bearing manifest field, exactly as :func:`_content_hash` does for
# one estimator (see the module docstring's own description of that
# rule) — this section is the same doorway, widened from one estimator
# to a named ORDERED set of them, for the ten-head final-model release
# (`children/intraday_equities/intraday_equities/final_model.py`).
# ---------------------------------------------------------------------------

#: The on-disk multi-head bundle format this pack writes and the only one
#: it loads. Distinct from :data:`_ARTIFACT_FORMAT`: one estimator and a
#: named mapping of them share the joblib+sidecar SHAPE but not the
#: schema, so the two formats are never confused for each other.
_BUNDLE_FORMAT = "sklearn-bundle-joblib-v1"

#: A head name must be a plain identifier — letters, digits, ``_`` or
#: ``-``, 1..64 characters. Refused rather than merely discouraged: a
#: head name is the one caller-supplied string this doorway threads
#: through untouched, and while nothing here builds a filesystem path
#: FROM one today, refusing ``/``/``..`` components outright means no
#: future caller of ``write_bundle`` can be tricked into escaping the
#: bundle's own directory through one (ADR-0114 Phase 2's "path escape"
#: refusal).
_HEAD_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: Manifest keys the bundle loader cannot proceed without.
_BUNDLE_MANIFEST_REQUIRED = (
    "categorical_encoding",
    "feature_order",
    "format",
    "head_params",
    "heads",
    "predict_checksum",
    "predict_fixture",
    "surviving_features",
    "training_identities",
)

#: Manifest fields the content digest does NOT cover: ``sha256`` records
#: the digest itself (it cannot cover its own value), and
#: ``library_versions`` is provenance the loader never enforces — the
#: same S2-A exclusion :data:`_UNHASHED_SIDECAR_FIELDS` makes for the
#: single-estimator artifact's ``library_version``. EVERYTHING else —
#: ``heads``, ``head_params``, ``feature_order``, ``surviving_features``,
#: ``categorical_encoding``, ``training_identities``, ``predict_fixture``,
#: ``predict_checksum``, ``format`` — is hash material.
_UNHASHED_BUNDLE_FIELDS = ("sha256", "library_versions")


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


def _label_keys(label):
    """One label key or a tuple of keys (ADR-0049)."""
    if isinstance(label, str):
        return (label,)
    return tuple(label)


def _fit_matrix(rows, features, label, where):
    """``(X, y)`` from the wired rows — every feature and the label, on
    every row, or a refusal naming the row and the key."""
    _refuse_zero_rows(rows, where)
    keys = _label_keys(label)
    matrix, targets = [], []
    for i, row in enumerate(rows):
        vector = _row_vector(row, i, (*features, *keys), where)
        matrix.append(vector[:-len(keys)])
        chunk = vector[-len(keys):]
        targets.append(chunk if len(keys) > 1 else chunk[0])
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


def _library_version(path):
    """The top-level library version behind a dotted path, when knowable —
    provenance only, never identity. Shared by :class:`SklearnFit` (its
    single-estimator sidecar) and the bundle writer below (its per-library
    ``library_versions`` map) — one owner, never two copies of the same
    ``sys.modules`` lookup."""
    module = sys.modules.get(path.split(".", 1)[0])
    version = getattr(module, "__version__", None)
    return version if isinstance(version, str) else None


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
# The multi-head bundle artifact (ADR-0114 Phase 2)
# ---------------------------------------------------------------------------


def _exact_head_keys_problems(label, heads, mapping):
    """Problems where ``mapping``'s keys do not exactly equal ``heads``.

    One rule shared by ``estimators``, ``head_params``, ``surviving_features``
    and ``training_identities`` — each is a per-head mapping, and a caller
    who forgot a head, duplicated one under a second name, or carried an
    extra must be told BY NAME which mapping and which head, never merely
    "shapes differ"."""
    if not isinstance(mapping, Mapping):
        return [f"{label} must be a mapping keyed by every declared head name"]
    declared = list(heads)
    missing = [h for h in declared if h not in mapping]
    extra = sorted(k for k in mapping if k not in declared)
    problems = []
    if missing:
        problems.append(f"{label} is missing declared head(s) {missing!r}")
    if extra:
        problems.append(f"{label} carries undeclared head(s) {extra!r}")
    return problems


def _bundle_head_list_problems(heads):
    """Problems with the declared, ORDERED head-name list itself: a
    non-empty list of distinct, path-safe identifiers."""
    if not isinstance(heads, (list, tuple)) or not heads:
        return ["heads must be a non-empty list of head-name strings"]
    problems = []
    seen = set()
    for name in heads:
        if not isinstance(name, str) or not _HEAD_NAME_RE.match(name):
            problems.append(
                f"head name {name!r} must be a non-empty string of letters, "
                "digits, '_' or '-' (at most 64 chars) — no path separators "
                "or '.' components, so a head name can never be used to "
                "escape the bundle's own directory"
            )
            continue
        if name in seen:
            problems.append(f"heads repeats {name!r} — head names must be distinct")
        seen.add(name)
    return problems


def _bundle_head_params_problems(heads, head_params):
    """Problems with the per-head constructor-identity mapping: each head
    names its own ``estimator`` (dotted path — this is what a LOAD-time
    isinstance check verifies against, per head) and its own
    ``estimator_params`` kwargs — no more, no fewer."""
    problems = _exact_head_keys_problems("head_params", heads, head_params)
    if problems:
        return problems
    for name in heads:
        entry = head_params.get(name)
        if not isinstance(entry, Mapping) or set(entry) != {
            "estimator", "estimator_params",
        }:
            problems.append(
                f"head_params[{name!r}] must be exactly "
                "{'estimator': <dotted path>, 'estimator_params': <dict>}, "
                f"got {entry!r}"
            )
            continue
        problems += _import_path_problems(
            f"head_params[{name!r}].estimator", entry["estimator"],
            example="sklearn.linear_model.Ridge",
        )
        problems += _kwargs_problems(
            f"head_params[{name!r}].estimator_params", entry["estimator_params"]
        )
    return problems


def _bundle_surviving_features_problems(heads, feature_order, surviving_features):
    """Problems with the per-head surviving-column list: each must be a
    non-empty, distinct list of names that are actually IN
    ``feature_order`` — nothing here requires every head to agree (a
    generic caller's heads may mask differently), only that what survives
    was a real candidate."""
    problems = _exact_head_keys_problems(
        "surviving_features", heads, surviving_features
    )
    if problems:
        return problems
    order_set = set(feature_order) if isinstance(feature_order, list) else set()
    for name in heads:
        value = surviving_features.get(name)
        problems += _feature_list_problems(f"surviving_features[{name!r}]", value)
        if isinstance(value, list) and order_set:
            unknown = [f for f in value if f not in order_set]
            if unknown:
                problems.append(
                    f"surviving_features[{name!r}] names {unknown} not "
                    "present in feature_order"
                )
    return problems


def _bundle_categorical_encoding_problems(feature_order, categorical_encoding):
    """Problems with the category map: a JSON-safe mapping whose keys are
    all actual candidate columns."""
    if not isinstance(categorical_encoding, Mapping):
        return ["categorical_encoding must be a mapping of feature name -> encoding"]
    order_set = set(feature_order) if isinstance(feature_order, list) else set()
    problems = [
        f"categorical_encoding names {key!r}, not present in feature_order"
        for key in categorical_encoding
        if order_set and key not in order_set
    ]
    try:
        json.dumps(dict(categorical_encoding), sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        problems.append("categorical_encoding must be canonical-JSON-safe")
    return problems


def _bundle_training_identities_problems(heads, training_identities):
    """Problems with the per-head training-identity/cuts mapping: a
    JSON-safe dict per head — the caller's own cuts/seed/row-count
    vocabulary, opaque to this generic doorway."""
    problems = _exact_head_keys_problems(
        "training_identities", heads, training_identities
    )
    if problems:
        return problems
    for name in heads:
        value = training_identities.get(name)
        if not isinstance(value, Mapping):
            problems.append(f"training_identities[{name!r}] must be a mapping")
            continue
        try:
            json.dumps(dict(value), sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            problems.append(
                f"training_identities[{name!r}] must be canonical-JSON-safe"
            )
    return problems


def _bundle_predict_fixture_problems(feature_order, predict_fixture):
    """Problems with the deterministic prediction fixture: a non-empty
    list of rows, each carrying one finite number per declared feature —
    the same row shape :func:`_row_vector` already enforces for a fit
    stream, restated here because this fixture never passes through
    that function (it is replayed through already-fitted estimators)."""
    width = len(feature_order) if isinstance(feature_order, list) else None
    if not isinstance(predict_fixture, list) or not predict_fixture:
        return ["predict_fixture must be a non-empty list of rows"]
    problems = []
    for i, row in enumerate(predict_fixture):
        if not isinstance(row, (list, tuple)) or (
            width is not None and len(row) != width
        ):
            problems.append(
                f"predict_fixture row {i} must carry {width} value(s), one "
                "per feature_order column"
            )
            continue
        if any(_finite_number(value) is None for value in row):
            problems.append(
                f"predict_fixture row {i} carries a non-finite or "
                "non-numeric value"
            )
    return problems


def _bundle_content_hash(path, manifest):
    """The bundle's identity: joblib bytes + the manifest's schema (S2-A,
    generalized from :func:`_content_hash` to a named mapping of heads).
    sha256 over the ``.joblib`` bytes, a NUL separator, then the canonical
    JSON of every manifest field outside :data:`_UNHASHED_BUNDLE_FIELDS`."""
    material = {
        k: v for k, v in manifest.items() if k not in _UNHASHED_BUNDLE_FIELDS
    }
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


def _bundle_predictions(heads, estimators, predict_fixture):
    """Each head's prediction vector over ``predict_fixture``, in head
    order — the raw material :func:`_bundle_predict_checksum` hashes."""
    import numpy as np

    matrix = np.asarray(predict_fixture, dtype=float)
    predictions = {}
    for name in heads:
        raw = estimators[name].predict(matrix)
        predictions[name] = [float(v) for v in np.ravel(raw)]
    return predictions


def _bundle_predict_checksum(predictions):
    """sha256 of the canonical JSON of a ``{head: [prediction, ...]}`` map —
    the DETERMINISTIC prediction checksum the plan names: replaying the
    same fixture through the same (or a restored) mapping always yields
    the same digest, so a load can PROVE it reproduced write-time beliefs,
    not merely that the bytes on disk are unchanged."""
    canon = json.dumps(
        predictions, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _dump_bundle_joblib(path, mapping):
    """Write ``mapping`` to ``path`` as one joblib file, ATOMICALLY: dump
    to memory first, then land the bytes via :func:`atomic_write` — the
    fix for the gap the module docstring calls out (:class:`SklearnFit`'s
    own ``joblib.dump(estimator, model_path)`` writes the final path
    directly and is NOT atomic; this doorway does not repeat that)."""
    import io

    import joblib

    buffer = io.BytesIO()
    joblib.dump(mapping, buffer)
    atomic_write(path, buffer.getvalue())


def _refuse_bundle_clobber(path, overwrite):
    """Refuse an existing bundle file unless the caller explicitly means
    to replace it — the same clobber-refusal convention
    ``kinds_table.FileWrite`` uses for every document-declared writer,
    restated here for a caller-declared path (this doorway is not a
    ``Node``, so there is no document param to read it from)."""
    if overwrite:
        return
    existing = [p for p in (path, path + ".json") if os.path.isfile(p)]
    if existing:
        raise ValueError(
            f"write_bundle: refusing to overwrite existing {existing} — "
            "pass overwrite=True to replace a bundle deliberately"
        )


def write_bundle(
    path,
    heads,
    estimators,
    *,
    head_params,
    feature_order,
    surviving_features,
    categorical_encoding,
    training_identities,
    predict_fixture,
    overwrite=False,
):
    """Persist a named mapping of fitted sklearn-shaped estimators as one
    joblib file plus one JSON manifest, atomically (ADR-0114 Phase 2).

    Generalizes :class:`SklearnFit`'s single-estimator artifact
    (``sklearn-joblib-v1``) to an ORDERED, NAMED set of them sharing one
    file: the ten-lead final-model release is the motivating caller
    (``children/intraday_equities/intraday_equities/final_model.py``),
    but nothing here is lead- or LightGBM-specific — ``heads`` is any
    caller-declared, path-safe name list. The digest covers the joblib
    bytes AND every schema-bearing manifest field (S2-A, the same rule
    :func:`_content_hash` already enforces for one estimator), so a
    relabelled head, a reordered feature list, a rewritten cut, or a
    changed checksum all fail LOAD, never merely a changed model file.

    Parameters
    ----------
    path : str
        Destination for the joblib file; the manifest is written beside
        it at ``path + ".json"``. Both are written atomically
        (same-directory temp file, fsync, replace) — an interrupted
        write leaves the previous pair intact or neither file at all,
        never a half-written one.
    heads : list of str
        The bundle's ordered, distinct head names — plain identifiers
        (letters/digits/``_``/``-``, <= 64 chars) so a head name can
        never be used to escape ``path``'s directory.
    estimators : dict
        ``head name -> fitted estimator``, keyed by EXACTLY ``heads`` —
        a missing or extra key refuses by name.
    head_params : dict
        ``head name -> {"estimator": <dotted path>, "estimator_params":
        <dict>}`` — the per-head constructor identity a LOAD-time
        isinstance check verifies each restored object against.
    feature_order : list of str
        The full candidate feature column order every head was built
        against.
    surviving_features : dict
        ``head name -> list of str``, each a subset of ``feature_order``
        naming that head's surviving columns (heads may differ).
    categorical_encoding : dict
        Feature name -> JSON-safe encoding descriptor, for columns of
        ``feature_order`` carrying a native categorical encoding.
    training_identities : dict
        ``head name -> JSON-safe dict`` of that head's own training
        identity (cuts, seed, row count, ...) — opaque to this doorway.
    predict_fixture : list
        Rows (each ``len(feature_order)`` finite numbers) replayed
        through every head to produce the deterministic
        ``predict_checksum``, and replayed again at load to verify the
        restored heads reproduce it.
    overwrite : bool
        Replace an existing bundle at ``path``/``path + ".json"``
        deliberately. Default ``False`` — an existing pair refuses.

    Returns
    -------
    dict
        The written manifest (the same object :func:`load_bundle` reads
        back and verifies).

    Raises
    ------
    ValueError
        A malformed ``heads``/``estimators``/mapping argument, an
        existing bundle with ``overwrite`` false, or a head name that
        cannot be validated as directory-safe.
    OSError
        The atomic write itself fails (open, write, fsync, or replace) —
        propagated unchanged from :func:`dskit.pipeline.node.atomic_write`.

    Examples
    --------
    Persist two tiny fitted Ridge heads sharing one feature order::

        manifest = write_bundle(
            "bundle.joblib",
            ["h01", "h02"],
            {"h01": ridge_a, "h02": ridge_b},
            head_params={
                "h01": {"estimator": "sklearn.linear_model.Ridge",
                         "estimator_params": {"alpha": 1e-6}},
                "h02": {"estimator": "sklearn.linear_model.Ridge",
                         "estimator_params": {"alpha": 1e-6}},
            },
            feature_order=["x0", "x1"],
            surviving_features={"h01": ["x0", "x1"], "h02": ["x0", "x1"]},
            categorical_encoding={},
            training_identities={"h01": {"n_rows": 4}, "h02": {"n_rows": 4}},
            predict_fixture=[[0.5, 0.5]],
        )
        manifest["heads"]
        # -> ["h01", "h02"]
    """
    where = "write_bundle"
    problems = _bundle_head_list_problems(heads)
    problems += _exact_head_keys_problems("estimators", heads, estimators)
    problems += _bundle_head_params_problems(heads, head_params)
    problems += _feature_list_problems("feature_order", feature_order)
    problems += _bundle_surviving_features_problems(
        heads, feature_order, surviving_features
    )
    problems += _bundle_categorical_encoding_problems(
        feature_order, categorical_encoding
    )
    problems += _bundle_training_identities_problems(heads, training_identities)
    problems += _bundle_predict_fixture_problems(feature_order, predict_fixture)
    if problems:
        raise ValueError(f"{where}: " + "; ".join(problems))

    _refuse_bundle_clobber(path, overwrite)

    ordered_estimators = {name: estimators[name] for name in heads}
    predictions = _bundle_predictions(heads, ordered_estimators, predict_fixture)
    libraries = sorted({head_params[name]["estimator"].split(".", 1)[0] for name in heads})

    manifest = {
        "format": _BUNDLE_FORMAT,
        "heads": list(heads),
        "head_params": {
            name: {
                "estimator": head_params[name]["estimator"],
                "estimator_params": dict(head_params[name]["estimator_params"]),
            }
            for name in heads
        },
        "feature_order": list(feature_order),
        "surviving_features": {
            name: list(surviving_features[name]) for name in heads
        },
        "categorical_encoding": dict(categorical_encoding),
        "training_identities": {
            name: dict(training_identities[name]) for name in heads
        },
        "predict_fixture": [list(row) for row in predict_fixture],
        "predict_checksum": _bundle_predict_checksum(predictions),
        "library_versions": {lib: _library_version(lib) for lib in libraries},
    }

    # The joblib file is written FIRST (and atomically) so the digest
    # below can hash its final bytes; the manifest is written LAST,
    # atomically, so an interrupted run never leaves a manifest that
    # claims a joblib file that is not actually on disk.
    _dump_bundle_joblib(path, ordered_estimators)
    manifest["sha256"] = _bundle_content_hash(path, manifest)
    atomic_write(
        path + ".json",
        json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8"),
    )
    return manifest


def load_bundle(path):
    """Restore a bundle :func:`write_bundle` wrote, hash-verified and
    never refitted (ADR-0114 Phase 2).

    Refuses, by name: a missing bundle or manifest file; a manifest
    declaring the wrong ``format`` or missing a required field; a content
    hash that does not match (any schema-bearing manifest field OR the
    joblib bytes changed since write); a restored head whose class is not
    an instance of what its manifest declares (a relabelled model); and a
    predict-checksum replay that disagrees with the manifest's pinned
    value (the restored heads do not reproduce write-time beliefs, even
    when the on-disk bytes were untouched — e.g. a hostile or corrupted
    in-memory deserialize).

    Parameters
    ----------
    path : str
        The joblib file path written by :func:`write_bundle`; its
        manifest is read from ``path + ".json"``.

    Returns
    -------
    EstimatorBundle
        The restored, verified bundle.

    Raises
    ------
    ValueError
        Any of the refusals above.

    Examples
    --------
    Restore a bundle and predict with one head::

        bundle = load_bundle("bundle.joblib")
        bundle.predict("h01", x)
        # -> one prediction per row of x
    """
    where = "load_bundle"
    if not isinstance(path, str) or not path:
        raise ValueError(f"{where}: loading requires a pinned bundle path, got {path!r}")
    if not os.path.isfile(path):
        raise ValueError(f"{where}: bundle {path!r} does not exist — nothing to load")
    manifest_path = path + ".json"
    if not os.path.isfile(manifest_path):
        raise ValueError(
            f"{where}: bundle manifest {manifest_path!r} is missing — refusing "
            "to load a bundle whose provenance cannot be verified"
        )
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"{where}: bundle manifest {manifest_path!r} is not readable JSON "
            f"({exc}) — refusing to load"
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("format") != _BUNDLE_FORMAT:
        raise ValueError(
            f"{where}: bundle {path!r} manifest declares format "
            f"{manifest.get('format') if isinstance(manifest, dict) else manifest!r}, "
            f"this pack loads {_BUNDLE_FORMAT!r} only"
        )
    missing = [k for k in _BUNDLE_MANIFEST_REQUIRED if k not in manifest]
    if missing or "sha256" not in manifest:
        raise ValueError(
            f"{where}: bundle {path!r} manifest is missing "
            f"{missing + (['sha256'] if 'sha256' not in manifest else [])} — "
            "refusing to load an under-described bundle"
        )

    heads = manifest["heads"]
    problems = _bundle_head_list_problems(heads)
    problems += _feature_list_problems("feature_order", manifest.get("feature_order"))
    problems += _bundle_head_params_problems(heads, manifest.get("head_params", {}))
    if problems:
        raise ValueError(
            f"{where}: bundle {path!r} manifest is malformed: "
            + "; ".join(problems)
        )

    actual = _bundle_content_hash(path, manifest)
    if actual != manifest["sha256"]:
        raise ValueError(
            f"{where}: bundle {path!r} content hash {actual[:16]}… does not "
            f"match its manifest ({str(manifest['sha256'])[:16]}…) — the "
            "bundle file or its manifest changed since it was written (the "
            "hash covers both); refusing to load"
        )

    import joblib

    try:
        payload = joblib.load(path)
    except Exception as exc:  # noqa: BLE001 - any unpickling failure is the answer
        raise ValueError(
            f"{where}: bundle {path!r} failed to load ({exc}) — the file is "
            "not a joblib mapping this environment can restore"
        ) from exc
    if not isinstance(payload, Mapping) or set(payload) != set(heads):
        raise ValueError(
            f"{where}: bundle {path!r} restored a payload whose head set "
            f"does not match its manifest ({sorted(heads)!r}) — refusing a "
            "relabelled bundle"
        )

    estimators = {}
    for name in heads:
        declared_path = manifest["head_params"][name]["estimator"]
        declared_cls = _import_estimator(declared_path, where)
        restored = payload[name]
        if not isinstance(restored, declared_cls):
            raise ValueError(
                f"{where}: bundle {path!r} head {name!r} restored a "
                f"{type(restored).__name__}, but its manifest declares "
                f"{declared_path!r} — refusing a relabelled model (the "
                "estimator class is identity, not a label)"
            )
        estimators[name] = restored

    predictions = _bundle_predictions(heads, estimators, manifest["predict_fixture"])
    replay = _bundle_predict_checksum(predictions)
    if replay != manifest["predict_checksum"]:
        raise ValueError(
            f"{where}: bundle {path!r} predict checksum {replay[:16]}… does "
            "not match its manifest "
            f"({str(manifest['predict_checksum'])[:16]}…) — replaying the "
            "deterministic prediction fixture through the restored heads "
            "produced different beliefs; refusing to load"
        )

    return EstimatorBundle(estimators, manifest, path)


class EstimatorBundle:
    """A verified, named mapping of fitted sklearn-shaped estimators,
    backed by one joblib file plus one JSON manifest (ADR-0114 Phase 2).

    Returned only by :func:`load_bundle` (after every refusal above has
    already passed) or held in memory straight from :func:`write_bundle`'s
    own ``estimators``/``manifest`` pair — this class itself does no
    verification, it is a plain, thin carrier over the two.

    Parameters
    ----------
    estimators : dict
        ``head name -> fitted estimator``.
    manifest : dict
        The bundle's full written/verified manifest.
    path : str
        The joblib file path this bundle was written to or loaded from.

    Examples
    --------
    Restore a written bundle and predict with one head::

        bundle = load_bundle("bundle.joblib")
        bundle.predict("h01", x)
        # -> one prediction per row of x
    """

    __slots__ = ("estimators", "manifest", "path")

    def __init__(self, estimators, manifest, path):
        self.estimators = dict(estimators)
        self.manifest = manifest
        self.path = path

    def predict(self, head, matrix):
        """The named head's prediction for ``matrix``.

        Parameters
        ----------
        head : str
            One of this bundle's head names.
        matrix : array-like
            Rows x this bundle's full ``feature_order`` width.

        Returns
        -------
        array-like
            Whatever the named head's own ``predict`` returns.

        Raises
        ------
        ValueError
            ``head`` is not one of this bundle's heads.
        """
        if head not in self.estimators:
            raise ValueError(
                f"EstimatorBundle carries no head {head!r} — has "
                f"{sorted(self.estimators)!r}"
            )
        return self.estimators[head].predict(matrix)


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
        out = self.estimator.predict([vector])[0]
        if hasattr(out, "__len__") and not isinstance(out, (str, bytes)):
            return [float(item) for item in out]
        return float(out)


# ---------------------------------------------------------------------------
# The column-mask estimator wrapper (ADR-0108) — a plain fit/predict object,
# never a Node: it is named by the SAME dotted-path doorway as any other
# estimator and lives in a document's estimator_params, never on a node kind
# of its own.
# ---------------------------------------------------------------------------


def _accepts_kwarg(model, name):
    """Report whether ``model.fit`` accepts the keyword ``name`` (or **kwargs)."""
    import inspect

    try:
        parameters = inspect.signature(model.fit).parameters
    except (TypeError, ValueError):
        return False
    return name in parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )


#: The two spellings a wrapped estimator's ``fit`` might declare for the
#: surviving column-name list, tried in this order. Most sklearn-style
#: estimators that take one spell it plural (``feature_names``, matched
#: by this pack's own stub fixtures); LightGBM's own ``fit`` spells it
#: SINGULAR (``feature_name``) and declares no ``**kwargs``, so trying
#: only the plural leaves the one library this ADR targets never told
#: the surviving names at all.
_FEATURE_NAME_KWARGS = ("feature_names", "feature_name")


class ColumnSubsetEstimator:
    """Fit any estimator on a declared subset of columns (ADR-0108).

    A feature mask is a MODEL knob, not a second feature pipeline: this
    wraps any estimator named by DOTTED import path (``module.ClassName``,
    never the ``module:Class`` colon form a node's own ``estimator`` knob
    takes) and fits it on the columns that survive a declared ``drop`` or
    ``keep`` list of column names — exactly one of the two, never both,
    never neither. A ``drop``/``keep`` name absent from ``feature_names``,
    or a mask that would leave zero surviving columns, refuses BY NAME
    naming the offender, because a typo'd mask that quietly keeps (or
    drops) everything reports a difference that never happened.

    The surviving column names are forwarded to the wrapped estimator's
    ``fit`` under whichever of ``feature_names`` or ``feature_name`` its
    signature accepts (inspected, never assumed, tried in that order) —
    LightGBM's own ``fit`` spells it singular and takes no ``**kwargs``,
    so trying the plural alone would silently forward nothing to the one
    library this ADR is written for. ``categorical_feature`` is forwarded
    the same inspected way, subset and RE-INDEXED to the surviving
    columns, so a native categorical such as ``symbol_code`` stays
    declared categorical at its correct, shifted position instead of
    silently pointing at whatever column now sits where it used to.
    ``predict`` projects with the same column indices ``fit`` chose, and
    refuses a matrix whose column COUNT has changed since fit.

    This class expects its CALLER to forward ``feature_names`` (and,
    where one exists, ``categorical_feature``) into ``fit`` — it is a
    plain estimator, not a node, and arranges no forwarding of its own.
    This pack's own :class:`SklearnFit` does NOT do this: ``run_train``
    calls ``estimator.fit(matrix, targets)`` with neither kwarg, so
    naming this class as ``SklearnFit``'s ``estimator`` refuses
    immediately (the mask cannot be verified with no ``feature_names``).
    The path that DOES forward both — by the same signature inspection
    this class itself uses — is ``intraday_equities``'s
    ``NoInformationScan``/``_fit_estimator``
    (``children/intraday_equities/intraday_equities/nodes.py:3160-3193``),
    which is what P16
    (``children/intraday_equities/configs/run-p16-feature-mask-zoo.json``)
    actually runs through. Teaching ``SklearnFit`` to forward them too is
    a named follow-up, not done here.

    Parameters
    ----------
    estimator : str
        Dotted import path of the wrapped estimator class, e.g.
        ``"lightgbm.LGBMRegressor"``.
    drop : list of str, optional
        Column names to remove; every survivor keeps its ORIGINAL
        column order. Exactly one of ``drop``/``keep`` must be given.
    keep : list of str, optional
        Column names to keep; every other column is removed. Exactly
        one of ``drop``/``keep`` must be given.
    **estimator_params
        Forwarded verbatim to the wrapped estimator's constructor.

    Examples
    --------
    Drop two stale lag columns and keep a trailing native-categorical
    column declared at its ORIGINAL (pre-mask) index::

        model = ColumnSubsetEstimator(
            "lightgbm.LGBMRegressor",
            drop=["ret_lag_5", "ret_lag_6"],
            n_estimators=50,
        )
        model.fit(
            x, y,
            feature_names=["ret_lag_5", "ret_lag_6", "vol_5m", "symbol_code"],
            categorical_feature=[3],
        )
        model.predict(x)
        # -> one prediction per row, fit on ["vol_5m", "symbol_code"]
        # alone, with categorical_feature=[1] (symbol_code's NEW index)
        # reaching the wrapped LightGBM
    """

    def __init__(self, estimator, drop=None, keep=None, **estimator_params):
        self.estimator = estimator
        self.drop = drop
        self.keep = keep
        self.estimator_params = dict(estimator_params)
        self._indices = None
        self._model = None
        self._n_columns = None

    def fit(self, x, y, feature_names=None, categorical_feature=None):
        """Fit the wrapped estimator on the declared column subset.

        Parameters
        ----------
        x : numpy.ndarray
            Rows x full candidate columns, in ``feature_names`` order.
        y : numpy.ndarray
            Targets, one per row.
        feature_names : list of str, optional
            ``x``'s column names, in order. Required whenever ``drop``
            or ``keep`` is declared — the mask cannot be verified by
            name without them.
        categorical_feature : list of int, optional
            Indices into ``x`` (BEFORE masking) naming native
            categorical columns. Re-indexed to the surviving columns
            and forwarded to the wrapped estimator when its ``fit``
            accepts the keyword; an index the mask removed is simply
            not forwarded — there is no column left to declare.

        Returns
        -------
        ColumnSubsetEstimator
            ``self``, fitted.

        Raises
        ------
        ValueError
            Neither or both of ``drop``/``keep`` were declared; ``drop``
            or ``keep`` is not a non-empty list of distinct, non-empty
            column-name strings; a ``drop``/``keep`` name is absent from
            ``feature_names``; the mask leaves zero surviving columns; a
            mask is declared and ``feature_names`` is ``None``; or
            ``estimator`` cannot be imported, names no ``fit`` method, or
            rejects ``estimator_params``.
        """
        has_drop = self.drop is not None
        has_keep = self.keep is not None
        if has_drop == has_keep:
            raise ValueError(
                "ColumnSubsetEstimator requires exactly one of 'drop' or "
                f"'keep', got drop={self.drop!r} keep={self.keep!r}"
            )
        knob, raw_mask = ("drop", self.drop) if has_drop else ("keep", self.keep)
        # Reuse the ONE list-of-column-names rule this file already owns
        # (``features``' own validator) rather than writing a second one:
        # a duplicate name was silently deduped by a bare ``set()`` before,
        # which is exactly the drift CLAUDE.md calls a scheduled bug.
        problems = _feature_list_problems(knob, raw_mask)
        if problems:
            raise ValueError("; ".join(problems))
        mask_names = list(raw_mask)
        if feature_names is None:
            raise ValueError(
                f"ColumnSubsetEstimator.{knob}={mask_names} is declared but "
                "fit was called with feature_names=None — the mask cannot "
                "be verified by name without them"
            )
        names = list(feature_names)
        if len(names) != int(x.shape[1]):
            raise ValueError(
                f"feature_names has {len(names)} name(s) for a design "
                f"matrix of {int(x.shape[1])} column(s)"
            )
        unknown = [name for name in mask_names if name not in names]
        if unknown:
            raise ValueError(
                f"{knob} names {unknown} are not present in feature_names {names}"
            )
        if has_drop:
            drop_set = set(mask_names)
            indices = [i for i, name in enumerate(names) if name not in drop_set]
        else:
            keep_set = set(mask_names)
            indices = [i for i, name in enumerate(names) if name in keep_set]
        if not indices:
            raise ValueError(
                f"{knob}={mask_names} leaves zero surviving columns of {names}"
            )

        where = "ColumnSubsetEstimator"
        path_problems = _import_path_problems(
            "estimator", self.estimator, example="lightgbm.LGBMRegressor"
        )
        if path_problems:
            raise ValueError(f"{where}: {'; '.join(path_problems)}")
        est_cls = _import_estimator(self.estimator, where)
        inner = _construct(
            est_cls, self.estimator_params, self.estimator, where, "estimator_params"
        )

        fit_kwargs = {}
        for kwarg in _FEATURE_NAME_KWARGS:
            if _accepts_kwarg(inner, kwarg):
                fit_kwargs[kwarg] = [names[i] for i in indices]
                break
        if categorical_feature is not None and _accepts_kwarg(
            inner, "categorical_feature"
        ):
            new_index = {old: new for new, old in enumerate(indices)}
            fit_kwargs["categorical_feature"] = [
                new_index[old] for old in categorical_feature if old in new_index
            ]

        inner.fit(x[:, indices], y, **fit_kwargs)
        self._indices = indices
        self._model = inner
        self._n_columns = int(x.shape[1])
        return self

    def predict(self, x):
        """Predict with the fitted estimator, on the fitted column subset.

        Parameters
        ----------
        x : numpy.ndarray
            Rows x the SAME full candidate columns ``fit`` saw, in the
            same order.

        Returns
        -------
        numpy.ndarray
            One prediction per row.

        Raises
        ------
        RuntimeError
            Called before ``fit``.
        ValueError
            ``x`` does not carry the same number of columns ``fit`` saw.
            This checks column COUNT only: a matrix with the right width
            whose columns were reordered or renamed since ``fit`` is
            indistinguishable from a correct one by shape alone, and is
            NOT caught here — only a ``feature_names`` mismatch supplied
            at ``fit`` time can catch that kind of corruption.
        """
        if self._model is None:
            raise RuntimeError("ColumnSubsetEstimator is not fitted")
        got = int(x.shape[1])
        if got != self._n_columns:
            raise ValueError(
                f"ColumnSubsetEstimator.predict: x has {got} column(s), fit "
                f"saw {self._n_columns} — the design matrix's shape has "
                "changed since fit"
            )
        return self._model.predict(x[:, self._indices])


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
        elif isinstance(label, str):
            if not label:
                problems.append(f"label must be a non-empty row key, got {label!r}")
        elif (
            not isinstance(label, (list, tuple))
            or not label
            or any(not isinstance(key, str) or not key for key in label)
        ):
            problems.append(
                "label must be a non-empty row key or a non-empty list of "
                f"row keys, got {label!r}"
            )
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
        current = _library_version(self.params["estimator"])
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
        labels = _label_keys(label)
        if len(labels) > 1:
            from sklearn.multioutput import MultiOutputRegressor
            estimator = MultiOutputRegressor(estimator)
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
            "library_version": _library_version(path),
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

    #: ADR-0091 phase 2b: audited and licensed, and stated HERE rather
    #: than inherited — a subclass that ever overrode the load path would
    #: keep the base's licence silently. This class overrides only fit-path
    #: members, so its restore is ``FittedTransform.run_load`` ->
    #: ``_sidecar`` -> ``Node.read_artifact``: the fitted state is a JSON
    #: column list, never a pickled model, so no deserialiser takes a path
    #: and the library is named on the FIT path alone.
    serving_load_audited = True

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
        est_params = params.get("estimator_params", {})
        problems += _kwargs_problems("estimator_params", est_params)
        if est_params and "estimator" not in params:
            problems.append(
                "estimator_params is set but estimator is not — those "
                "kwargs construct the inner estimator and nothing would "
                "read them"
            )
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


class SklearnReduction(FittedTransform):
    """Project rows onto learned axes — PCA or TruncatedSVD (ADR-0149).

    A member of the fitted-transform family (ADR-0040): the base owns the
    whole envelope — fitting on the declared split and nothing else, the
    persisted JSON sidecar, the load-mode restore that never refits, and
    the row-independence screen — and this class supplies ``fit`` and
    ``apply_state``.

    The catalog is CLOSED (``pca``, ``svd``), unlike this pack's estimator
    and selector doorways, which take any import path. The reason is the
    state: this node persists EXTRACTED ``components_`` (and, for ``pca``,
    ``mean_``) as JSON rather than a pickled model, and only these two
    expose that extraction behind a projection that is a plain matrix
    multiply this module owns. A native model is never written, so a
    serving run restores a plain JSON object and no deserialiser takes a
    path.

    The projection is computed HERE from the stored arrays — for ``pca``,
    ``(X - mean_) @ components_.T``; for ``svd``, ``X @ components_.T`` —
    and must equal the fitted estimator's own ``transform`` on fixtures,
    proven per catalog member. The declared ``features`` columns are
    DROPPED from every output row and replaced by ``component_<i>`` for
    ``i`` in ``0..n_components-1``; every non-feature column rides along.
    ``reduction_model_id`` (the sha256 of the canonical state, recomputed
    never copied) labels every row and is also a port, so a downstream row
    can be traced to the exact projection that produced it. No
    variance/reconstruction score is reported: an internal quality number
    is one a search would rank reductions by, which is model selection this
    node deliberately supplies no objective for.

    Parameters
    ----------
    params : dict
        ``algorithm`` (required, one of ``"pca"``, ``"svd"``),
        ``features`` (required, the row keys the matrix reads, in column
        order), ``n_components`` (required, a non-bool int ``>= 1`` and
        ``<= len(features)`` — the projected width), ``algorithm_params``
        (dict of the class's constructor kwargs, default ``{}``; ``seed``
        is threaded as ``random_state`` for either member), plus the
        family's ``fit_split`` / ``order_field`` / ``purity_check``.

    Examples
    --------
    Project two features onto two principal axes::

        node = SklearnReduction("reduce", {
            "fit_split": "train",
            "features": ["feature_a", "feature_b"],
            "algorithm": "pca",
            "n_components": 2,
            "seed": 17,
        })
        out = node.run(ctx, {"rows": rows})
        # -> out["rows"][0]["component_0"] == ...
        # -> out["reduction_model_id"] == "9f86d0…"
    """

    outputs = FittedTransform.outputs + ("reduction_model_id",)

    _PARAMS = FittedTransform._PARAMS + (
        "algorithm",
        "algorithm_params",
        "features",
        "n_components",
        "seed",
    )

    # -- the knobs ---------------------------------------------------------

    def features(self):
        """The declared feature keys, in the order components store them (list)."""
        declared = self.params.get("features")
        return list(declared) if isinstance(declared, (list, tuple)) else []

    def algorithm(self):
        """The catalog member this node fits (str)."""
        return self.params.get("algorithm")

    def n_components(self):
        """The declared projected width, or ``None`` when undeclared (int)."""
        return self.params.get("n_components")

    def _component_names(self, width=None):
        """The projected column names for ``width`` (default the declared one).

        The width is a parameter, not read from ``self.params``: the fit
        path hands the width it is about to fit, so the collision rule and
        the matrix can never disagree about which names are produced.
        """
        if width is None:
            width = self.n_components()
        if isinstance(width, bool) or not isinstance(width, int) or width < 1:
            return []
        return [f"{_COMPONENT_PREFIX}_{i}" for i in range(width)]

    # -- validation --------------------------------------------------------

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
            class's own: a missing or off-catalog ``algorithm``, a
            ``features`` list that is not distinct and non-empty, a
            missing or malformed ``n_components``, a malformed
            ``algorithm_params`` block, a malformed ``seed``, and any
            second spelling of a knob this node already owns.
        """
        problems = super().validate_params(params)
        if "features" not in params:
            problems.append(
                "features is required — the row keys the matrix reads, in "
                "the order the components store them"
            )
        else:
            problems += _feature_list_problems("features", params["features"])
        problems += cls._algorithm_problems(params)
        return problems

    @classmethod
    def _algorithm_problems(cls, params):
        """The closed catalog, its kwargs block, and the shadowed knobs."""
        problems = []
        algorithm = params.get("algorithm")
        if algorithm not in _REDUCTION_ALGORITHMS:
            problems.append(
                f"algorithm is required and must be one of "
                f"{list(_REDUCTION_ALGORITHMS)} — this node extracts "
                "components into JSON, so the catalog is closed rather than "
                f"an arbitrary import path, got {algorithm!r}"
            )
        kwargs = params.get("algorithm_params", {})
        problems += _kwargs_problems("algorithm_params", kwargs)
        if isinstance(kwargs, dict) and any(
            isinstance(key, str) and not key for key in kwargs
        ):
            problems.append(
                "algorithm_params carries an empty key — no callable takes "
                "one as a keyword argument, so it is a plan-time shape error "
                "wearing a run-time costume"
            )
        problems += cls._n_components_problems(params)
        problems += cls._shadowed_knob_problems(kwargs)
        problems += cls._feature_component_overlap_problems(params)
        if "seed" in params:
            problems += _seed_problems(params["seed"])
        return problems

    @classmethod
    def _feature_component_overlap_problems(cls, params):
        """Refuse a declared feature that is also a produced column name.

        A projection DROPS the declared features and ADDS ``component_<i>``
        for ``i`` in ``0..n_components-1``; a feature that IS one of those
        names is both dropped and written, so no run can ever succeed and
        the row rule's remedy ("rename the field upstream") is the wrong
        one. All three facts — the features, the width, and the prefix —
        are known at plan time, so the refusal happens where the document
        is read, not where it executes.
        """
        features = params.get("features")
        width = params.get("n_components")
        if (
            not isinstance(features, (list, tuple))
            or not all(isinstance(name, str) for name in features)
            or isinstance(width, bool)
            or not isinstance(width, int)
            or width < 1
        ):
            return []
        produced = {f"{_COMPONENT_PREFIX}_{i}" for i in range(width)}
        overlap = sorted(set(features) & produced)
        if not overlap:
            return []
        return [
            f"features declares {overlap}, which this node writes as a "
            "projected column name — a projected column cannot also be a "
            "source feature; rename the feature upstream"
        ]

    @classmethod
    def _n_components_problems(cls, params):
        """``n_components`` is a non-bool int ``>= 1`` and ``<= len(features)``."""
        if "n_components" not in params:
            return [
                "n_components is required — the number of projected columns "
                "the output emits"
            ]
        value = params["n_components"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return [
                f"n_components must be a non-bool int >= 1, got {value!r}"
            ]
        features = params.get("features")
        if isinstance(features, (list, tuple)) and value > len(features):
            return [
                f"n_components={value} exceeds the {len(features)} declared "
                "feature(s) — a projection cannot emit more axes than its "
                "source columns"
            ]
        return []

    @classmethod
    def _shadowed_knob_problems(cls, kwargs):
        """Refuse a second spelling of a knob this node already owns.

        Reads :data:`_REDUCTION_SHADOWED_KNOBS` — the one owner of which
        constructor args are already claimed by the node's own knobs — so
        a name added there is refused the same day, and a name dropped
        there stops being refused. An inline second copy is exactly the
        drift the module docstring's duplication rule exists to prevent.
        """
        if not isinstance(kwargs, dict):
            return []
        return [
            f"algorithm_params.{name} is set — {reason}"
            for name, reason in _REDUCTION_SHADOWED_KNOBS.items()
            if name in kwargs
        ]

    def validate_load_inputs(self, inputs):
        """Problems a RESTORE reads, empty when none.

        Parameters
        ----------
        inputs : dict
            The materialized inputs; unused.

        Returns
        -------
        list of str
            One problem per FITTING knob the document declared.
            ``seed`` and ``algorithm_params`` describe how a state was
            learned, never what the extracted projection IS — a load that
            accepted them would imply it could rebuild the native model
            they configured, which this node never persisted.
        """
        return [
            f"{knob} describes FITTING, not the restored state — a loaded "
            "projection is components (and, for pca, a mean), and nothing "
            f"in it can be rebuilt from {knob}; drop the knob under "
            "mode='load'"
            for knob in ("algorithm_params", "seed")
            if knob in self.params
        ]

    def row_problems(self, rows):
        """Ways ``rows`` cannot be projected; empty when every one can.

        This class's per-row admission is bespoke — it inherits none — and
        it is the SOLE owner of the rule, reached from the fitting node's
        own stream, the second stream an
        :class:`~dskit.pipeline.fitted.ApplyTransform` carrier projects, and
        :meth:`fit` itself. Bounded by the RULES, not the stream: each
        distinct rule is reported once, naming the first row that broke it.

        Parameters
        ----------
        rows : list
            The stream, already known to be a list.

        Returns
        -------
        list of str
            One problem per BROKEN RULE: a row that is not a mapping, a
            declared feature that is absent, a declared feature that is
            not a finite real number (a ``bool`` is not one), and a
            projected column name the row already carries.
        """
        features = self.features()
        if _feature_list_problems("features", features):
            features = ()
        return self._unusable_rows(rows, features, self._component_names())

    def _unusable_rows(self, rows, features, component_names):
        """The row rule over whichever feature list is about to be read."""
        first = {}
        for index, row in enumerate(rows):
            for key, message in self._broken_rules(
                index, row, features, component_names
            ):
                first.setdefault(key, message)
        return list(first.values())

    def _broken_rules(self, index, row, features, component_names):
        """``(rule key, message)`` for every rule ONE row breaks."""
        if not isinstance(row, Mapping):
            yield ("not-a-mapping",), (
                f"rows[{index}] is a {type(row).__name__} — a reduction "
                "reads features by key and writes component columns of its "
                "own, so every row must be a mapping"
            )
            return
        for name in features:
            if name not in row:
                yield ("feature-absent", name), (
                    f"rows[{index}] carries no {name!r} — EVERY row is "
                    "projected, so every row must carry every declared "
                    "feature (cut or repair the stream upstream)"
                )
            elif not number_ok(row[name]):
                yield ("feature-not-a-number", name), (
                    f"rows[{index}] field {name!r} is {row[name]!r}, not a "
                    "finite real number — the projection is a matrix "
                    "multiply over the declared features, and a bool is not "
                    "a number here"
                )
        for name in component_names:
            if name in row:
                yield ("output-key-present", name), (
                    f"rows[{index}] already carries {name!r}, which this "
                    "node writes — a reduction never overwrites the "
                    "evidence it was handed; rename the field upstream"
                )

    # -- the fitted state --------------------------------------------------

    def fit(self, rows, params):
        """Learn the projection from the fit split's rows.

        Parameters
        ----------
        rows : list
            The rows of the declared ``fit_split``, and nothing else — the
            base cut them, which is the whole leakage guarantee.
        params : dict
            ``self.params``, passed through by the base.

        Returns
        -------
        dict
            ``schema``, ``algorithm``, ``features``, ``components``, and —
            for ``pca`` only — ``mean``. Exactly those, all JSON-safe. No
            native estimator is persisted.

        Raises
        ------
        ValueError
            When a row breaks this class's own :meth:`row_problems` rule
            (the same repetition a direct caller could otherwise go around),
            when the catalog member rejects ``algorithm_params`` or refuses
            the fit, or when the fitted estimator exposes no usable
            components — including a component count sklearn silently
            clamped below the declared ``n_components``. Nothing is written.
        """
        features = list(params["features"])
        unusable = self._unusable_rows(
            rows, features, self._component_names(params["n_components"])
        )
        if unusable:
            raise ValueError(
                f"{self.key}: {'; '.join(unusable)}. That is this class's "
                "own row rule, asked here for the same reason the fit is "
                "reached directly: a caller bypassing validate_inputs would "
                "otherwise learn a state from rows the projection then "
                "refuses — and the pack's shared matrix reader counts a bool "
                "as 0/1, which this class does not"
            )
        algorithm = params["algorithm"]
        matrix = _design_matrix(rows, features, self.key)
        estimator = self._fitted_estimator(algorithm, params, matrix)
        components, mean = self._extracted(
            estimator, algorithm, len(features), params["n_components"]
        )
        state = {
            "schema": REDUCTION_SCHEMA,
            "algorithm": algorithm,
            "features": features,
            "components": components,
        }
        if mean is not None:
            state["mean"] = mean
        return state

    def _fitted_estimator(self, algorithm, params, matrix):
        """The catalog member, constructed and fitted — refusals name both."""
        path = _REDUCTION_PATHS[algorithm]
        named = f"algorithm {algorithm!r} ({path})"
        kwargs = dict(params.get("algorithm_params") or {})
        shadowed = self._shadowed_knob_problems(kwargs)
        if shadowed:
            raise ValueError(
                f"{self.key}: {'; '.join(shadowed)} — the direct-caller "
                "re-check, like the row rule: a caller bypassing "
                "validate_params would otherwise fit a projection "
                "``apply_state`` then computes wrongly (whiten changes the "
                "transform beyond the stored components)"
            )
        kwargs["n_components"] = params["n_components"]
        kwargs["random_state"] = params.get("seed", DEFAULT_REDUCTION_SEED)
        estimator = _construct(
            _import_object(path, self.key, subject="reduction"),
            kwargs,
            named,
            self.key,
            "algorithm_params",
        )
        try:
            estimator.fit(matrix)
        except Exception as exc:  # noqa: BLE001 - a refusal must name the node
            raise ValueError(
                f"{self.key}: {named} refused to fit {len(matrix)} row(s) of "
                f"{len(matrix[0]) if matrix else 0} feature(s): {exc}"
            ) from exc
        return estimator

    def _extracted(self, estimator, algorithm, width, n_components):
        """The fitted estimator's components and mean, as JSON state."""
        components_attr, mean_attr = _REDUCTION_ATTRIBUTES[algorithm]
        components = _reduction_rows(
            self._fitted_attribute(estimator, components_attr, algorithm)
        )
        mean = None
        if mean_attr is not None:
            mean = self._fitted_attribute(estimator, mean_attr, algorithm)
        problems = _reduction_geometry_problems(
            components,
            mean,
            width,
            n_components,
            mean_expected=mean_attr is not None,
        )
        if problems:
            raise ValueError(
                f"{self.key}: {algorithm!r} fitted a reduction this node "
                f"cannot store — {'; '.join(problems)}. Nothing was written"
            )
        return (
            [[float(value) for value in row] for row in components],
            None if mean is None else [float(value) for value in _reduction_flat(mean)],
        )

    def _fitted_attribute(self, estimator, name, algorithm):
        """One fitted attribute, or a refusal naming the node and the member."""
        value = getattr(estimator, name, None)
        if value is None:
            raise ValueError(
                f"{self.key}: {algorithm!r} exposes no {name!r} after fit — "
                "this node stores EXTRACTED components (and, for pca, a mean) "
                "rather than a pickled model, so a member without them cannot "
                "be stored. Nothing was written"
            )
        return value

    # -- what a RESTORE is held to ----------------------------------------

    def state_problems(self, state):
        """Ways a restored state is broken or misdescribed; empty when neither.

        Parameters
        ----------
        state : dict
            The restored state, as :meth:`fit` returned it.

        Returns
        -------
        list of str
            The stored shape's own problems first — it is read verbatim, so
            it is checked completely — then one problem per knob this
            document declares that the state contradicts: ``algorithm``,
            ``features``, and the projected width ``n_components``.
        """
        problems = self._state_shape_problems(state)
        if problems:
            return problems
        if state["algorithm"] != self.algorithm():
            problems.append(
                f"the restored state was fitted with algorithm "
                f"{state['algorithm']!r}, this document declares "
                f"{self.algorithm()!r}"
            )
        if state["features"] != self.features():
            problems.append(
                f"the restored state reads features {state['features']}, "
                f"this document declares {self.features()} — the components "
                "are axes in the STATE's feature order, so a restated list "
                "in any other order would project the wrong columns"
            )
        if len(state["components"]) != self.n_components():
            problems.append(
                f"the restored state has {len(state['components'])} "
                f"component(s), this document declares n_components="
                f"{self.n_components()} — the output width is the state's "
                "answer, and a document may restate it, never misdescribe it"
            )
        return problems

    def _state_shape_problems(self, state):
        """The stored state, checked completely — a load reads it verbatim."""
        if not isinstance(state, dict):
            return ["the restored state is not a mapping"]
        algorithm = state.get("algorithm")
        expected = {"schema", "algorithm", "features", "components"}
        if algorithm == "pca":
            expected.add("mean")
        if set(state) != expected:
            return [
                f"the restored state carries keys {sorted(state)}, not "
                f"exactly {sorted(expected)}"
            ]
        problems = []
        if state["schema"] != REDUCTION_SCHEMA:
            problems.append(
                f"the restored state declares schema {state['schema']!r}, "
                f"not {REDUCTION_SCHEMA!r}"
            )
        if algorithm not in _REDUCTION_ALGORITHMS:
            problems.append(
                f"the restored state names algorithm {algorithm!r}, outside "
                f"this node's catalog {list(_REDUCTION_ALGORITHMS)}"
            )
        problems += _feature_list_problems("features", state["features"])
        if problems:
            return problems
        width = len(state["features"])
        components_rows = _reduction_rows(state["components"])
        n_components = len(state["components"]) if components_rows is not None else 0
        problems += _reduction_geometry_problems(
            components_rows,
            state.get("mean"),
            width,
            n_components,
            mean_expected=algorithm == "pca",
        )
        return problems


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
