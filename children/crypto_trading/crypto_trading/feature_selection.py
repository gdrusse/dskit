"""Family and scale ablation selection for the minute predictor: which groups of features earn their place.

The question (plan 2026-10-09-data-windows.md, "Feature families and selection"): for one MODEL CLASS, does
dropping a feature family, or one scale of the multi-scale columns, hurt the forecast beyond fold noise?
Each class (the sequence models, the tabular models) is run once per arm of ``configs/features-arms.json`` on
the same W1 walk-forward folds, rows, seeds and loss; ``dskit.pipeline walkforward`` writes one fold-score
summary per arm. This module reads those summaries and decides, by an automated rule, which groups to keep.

- **The rule.** For a dropped group ``g`` let ``d_k = s_k(minus g) - s_k(all)`` over folds ``k`` (the fold's
  validation score, lower is better, so a positive ``d`` means dropping ``g`` hurts). The noise measure is the
  paired standard error of ``d`` from :func:`dskit.pipeline.stats.across_fold_t`. Keep ``g`` iff
  ``mean(d) > 0`` and ``mean(d) >= z * SE``: the one-standard-error rule, liberal toward keeping, because a
  false drop costs signal and a false keep costs one column. No spread in ``d`` is the same rule with
  ``SE = 0``. Fewer than ``min_folds`` folds refuse. There is no multiplicity correction.
- **Two axes, never stacked.** The family arms drop one family; the scale arms drop one scale, which is every
  column that carries that many minutes: the ``scales`` grid's ``ret``/``trend``/``vol``/``xasset`` columns AND
  the EWMA half-life, range window and taker window of the same minutes. Both are compared with the SAME ``all`` run.
- **Per class.** Each class gets its own kept set and file; a group kept by exactly one class is listed with
  both (mean, SE) pairs and is never reconciled.
- **The check.** An optional ``selected`` arm (the kept families and scales, run on the same folds) must not be
  worse than ``all`` by more than ``z`` standard errors, or the class falls back to ``all`` and is flagged. A class
  with no kept family (or scale) keeps the whole axis, flagged: an empty feature set is not a model.

The group-to-column grammar is the feature node's own: a column is ``<family>__<name>``, and a scale-bearing
name is one of ``r<n>``, ``ma_<n>``, ``rms_<n>``, ``hl_<n>``, ``jump_<n>``, ``pair_chg_<n>`` (the ``scales``
grid) or ``ewma_<n>``, ``hi_<n>``, ``lo_<n>``, ``taker_<n>`` (a window knob, scale-bearing when ``n`` is a
minute of the grid). The column list is PROBED from :class:`~crypto_trading.minute_features.MinuteFeatures` on
deterministic synthetic bars, never restated here, and a grid column whose scale is outside the arms' grid
refuses. A scale arm sets ``scales`` and the three window knobs, and ``taker_check_windows`` (the full taker
list) so the node's row set is the same in every arm; :meth:`FeatureGroups.arm_params` is the one place that
derives them, and a test pins the shipped manifests to it.

Interim home: ``KeepRule`` and ``FoldScores`` are generic and graduate to dskit under ADR-0261 (PROPOSED, not
yet approved); this module keeps them, unchanged, until that ADR is approved and built.

Import cost: stdlib + dskit; numpy only inside the feature node's ``run``.
"""

import glob
import hashlib
import json
import math
import os
import re
import string
from dataclasses import dataclass
from functools import lru_cache

from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok
from dskit.pipeline.runs import WALKFORWARD_FILE
from dskit.pipeline.stats import across_fold_t

from .minute_features import BAR_MS, FAMILIES, MinuteFeatures

__all__ = [
    "ALL_ARM", "AblationSelect", "DEFAULT_MIN_FOLDS", "DEFAULT_Z", "DISAGREEMENTS_FILE", "FeatureGroups",
    "FoldScores", "KeepRule", "SELECTED_ARM", "SELECTED_FILE",
]

#: The one-standard-error rule: keep a group when dropping it hurts by at least this many SEs.
DEFAULT_Z = 1.0
#: Fewer folds than this give no usable standard error (the owner-approved floor).
DEFAULT_MIN_FOLDS = 8
#: The reference arm every dropped group is compared with.
ALL_ARM = "all"
#: The optional arm that runs the kept groups; it is read, never required.
SELECTED_ARM = "selected"
#: The file one class's selection is written to, in the node's artifact directory.
SELECTED_FILE = "selected-features-{name}.json"
#: The groups the classes disagree on, in the same directory.
DISAGREEMENTS_FILE = "feature-selection-disagreements.json"
#: The driver names a summary directory ``<document>-walkforward-<asof>-<hash>``.
SUMMARY_INFIX = "-walkforward-"
#: The walk-forward summary's direction: a lower score is better, so a positive difference is a loss.
LOWER_IS_BETTER = "min"
_RAN = "ran"
_SEP = "__"
#: The families whose columns can carry a scale, and the name shapes that do (the scale is the trailing integer).
_SCALE_FAMILIES = ("ret", "trend", "vol", "xasset", "range", "flow")
#: Names the ``scales`` knob produces: always scale-bearing.
_GRID_NAME = re.compile(r"^(?:r|ma_|rms_|hl_|jump_|pair_chg_)(\d+)$")
#: Names a window knob produces: scale-bearing only when the window is a minute of the grid.
_WINDOW_NAME = re.compile(r"^(?:ewma_|hi_|lo_|taker_)(\d+)$")
#: The node knobs whose windows are minutes of the scale grid, and the extra knob that keeps the rows when one goes.
_WINDOW_KNOBS = ("ewma_half_lives", "range_windows", "taker_windows")
_CHECK_KNOB = "taker_check_windows"
_CLASS_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_FORBIDDEN_FEATURE_KNOBS = ("families", "scales", _CHECK_KNOB)
#: The probe's synthetic bars: a Monday midnight UTC start, so calendar families see ordinary weekdays.
_PROBE_START_MS = 1_767_571_200_000
_PROBE_EXTRA_BARS = 5
_KIND_FAMILY, _KIND_SCALE = "family", "scale"


def _sha256(path):
    """Return the hex sha256 of a file's bytes."""
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@dataclass(frozen=True)
class FoldScores:
    """One arm's per-fold validation scores, as ``dskit.pipeline walkforward`` summarised them.

    Parameters
    ----------
    cutoffs : tuple of str
        The fold cutoffs (``YYYY-MM-DD``), in fold order.
    scores : tuple of float
        The fold scores, one per cutoff; lower is better.
    objective : str
        The ``$node.path`` objective each fold reported.
    select : str
        The summary's direction (``"min"``).
    path : str
        The ``walkforward.json`` that was read.
    sha256 : str
        That file's hash, recorded so a selection names what it read.

    Examples
    --------
    Three folds of an arm that was read from a summary::

        scores = FoldScores.read("pipeline_runs/select-sequence/runs/minus_flow")
        # -> FoldScores(cutoffs=("2024-01-01", ...), scores=(0.91, ...), objective="$score.metrics.weighted_crps", ...)
    """

    cutoffs: tuple
    scores: tuple
    objective: str
    select: str
    path: str
    sha256: str

    @classmethod
    def read(cls, directory):
        """Read the one walk-forward summary under a run root.

        Parameters
        ----------
        directory : str
            An arm's run root (the walk-forward document's ``outputs.run_root``).

        Returns
        -------
        FoldScores
            The summary's fold scores.

        Raises
        ------
        ValueError
            No summary or more than one, a summary that did not run to the end, a direction other than
            ``min``, a fold that did not run, or a score that is not a finite number.
        """
        found = sorted(glob.glob(os.path.join(directory, f"*{SUMMARY_INFIX}*", WALKFORWARD_FILE)))
        if not found:
            raise ValueError(f"no walk-forward summary under {directory!r} (expected *{SUMMARY_INFIX}*/{WALKFORWARD_FILE})")
        if len(found) != 1:
            raise ValueError(f"exactly one walk-forward summary is expected under {directory!r}, found {len(found)}: "
                             f"{[os.path.basename(os.path.dirname(p)) for p in found]}")
        with open(found[0], encoding="utf-8") as handle:
            summary = json.load(handle)
        return cls._from_summary(summary, found[0])

    @classmethod
    def _from_summary(cls, summary, path):
        """Check a parsed summary and build its scores."""
        if summary.get("state") != _RAN:
            raise ValueError(f"{path}: the walk-forward state is {summary.get('state')!r}, not {_RAN!r}")
        if summary.get("select") != LOWER_IS_BETTER:
            raise ValueError(f"{path}: select is {summary.get('select')!r}; the ablation reads a loss (select {LOWER_IS_BETTER!r})")
        folds = summary.get("folds")
        if not isinstance(folds, list) or not folds:
            raise ValueError(f"{path}: the summary holds no folds")
        for index, fold in enumerate(folds):
            if fold.get("state") != _RAN:
                raise ValueError(f"{path}: fold {fold.get('cutoff')!r} (index {index}) is {fold.get('state')!r}, not {_RAN!r}")
            if not number_ok(fold.get("score")):
                raise ValueError(f"{path}: fold {fold.get('cutoff')!r} has no finite score, got {fold.get('score')!r}")
        return cls(cutoffs=tuple(f["cutoff"] for f in folds), scores=tuple(float(f["score"]) for f in folds),
                   objective=summary.get("objective"), select=summary["select"], path=path, sha256=_sha256(path))

    def same_folds(self, other):
        """Refuse unless ``other`` was scored on the same folds with the same objective and direction.

        Parameters
        ----------
        other : FoldScores
            Another arm's scores.

        Raises
        ------
        ValueError
            The objective, the direction or the cutoffs differ.
        """
        if self.objective != other.objective:
            raise ValueError(f"the objective differs: {self.objective!r} vs {other.objective!r}")
        if self.select != other.select:
            raise ValueError(f"the select direction differs: {self.select!r} vs {other.select!r}")
        if self.cutoffs != other.cutoffs:
            raise ValueError(f"the fold cutoffs differ: {list(self.cutoffs)} vs {list(other.cutoffs)}")


class KeepRule:
    """Keep a group iff dropping it hurts by at least ``z`` paired standard errors.

    Parameters
    ----------
    z : float, default DEFAULT_Z
        Standard errors the mean difference must reach (``>= 0``; ``0`` keeps any positive mean).
    min_folds : int, default DEFAULT_MIN_FOLDS
        The fewest folds the rule will judge (``>= 2``).

    Examples
    --------
    Judge a dropped group against the reference arm on the same folds::

        rule = KeepRule(z=1.0, min_folds=8)
        verdict = rule.judge(all_scores, minus_flow_scores)
        # -> {"keep": True, "mean": 0.012, "se": 0.003, "t": 4.0, "p_value": 0.001, "n": 10}
    """

    def __init__(self, z=DEFAULT_Z, min_folds=DEFAULT_MIN_FOLDS):
        problems = self.problems(z, min_folds)
        if problems:
            raise ValueError("; ".join(problems))
        self.z, self.min_folds = float(z), int(min_folds)

    @staticmethod
    def problems(z, min_folds):
        """Problems with the two knobs.

        Parameters
        ----------
        z : object
            The declared ``z``.
        min_folds : object
            The declared ``min_folds``.

        Returns
        -------
        list of str
            Empty when both are usable.
        """
        problems = []
        if isinstance(z, bool) or not number_ok(z) or z < 0:
            problems.append(f"z must be a finite number >= 0, got {z!r}")
        check_int_param(problems, "min_folds", min_folds, ge=2)
        return problems

    def _difference(self, reference, arm):
        """Return the stats of ``arm - reference`` per fold, after the same-folds and floor checks."""
        reference.same_folds(arm)
        if len(reference.scores) < self.min_folds:
            raise ValueError(f"the rule needs at least {self.min_folds} folds, got {len(reference.scores)}")
        return across_fold_t([a - r for a, r in zip(arm.scores, reference.scores)])

    def judge(self, reference, arm):
        """Decide whether the dropped group behind ``arm`` is kept.

        Parameters
        ----------
        reference : FoldScores
            The ``all`` arm.
        arm : FoldScores
            The arm that drops one group, on the same folds.

        Returns
        -------
        dict
            ``keep`` plus ``mean``, ``se``, ``t`` (``None`` without spread), ``p_value`` and ``n`` of the
            per-fold difference.

        Raises
        ------
        ValueError
            Different folds, objective or direction, or fewer folds than ``min_folds``.
        """
        stats = self._difference(reference, arm)
        keep = stats["mean"] > 0 and stats["mean"] >= self.z * stats["se"]
        return {"keep": keep, **self._reported(stats)}

    def worse(self, reference, candidate):
        """Say whether ``candidate`` is worse than ``reference`` by more than ``z`` standard errors.

        Parameters
        ----------
        reference : FoldScores
            The ``all`` arm.
        candidate : FoldScores
            The arm under test (the selected groups), on the same folds.

        Returns
        -------
        dict
            ``worse`` plus the difference's ``mean``, ``se``, ``t``, ``p_value`` and ``n``.

        Raises
        ------
        ValueError
            As :meth:`judge`.
        """
        stats = self._difference(reference, candidate)
        return {"worse": stats["mean"] > self.z * stats["se"], **self._reported(stats)}

    @staticmethod
    def _reported(stats):
        """Return the fields of ``across_fold_t`` a selection file records."""
        return {key: stats[key] for key in ("mean", "se", "t", "p_value", "n")}


@lru_cache(maxsize=16)
def _probe_columns(resolved_json):
    """Return the feature columns the node emits for these resolved params, from deterministic synthetic bars."""
    resolved = json.loads(resolved_json)
    bars = MinuteFeatures.need_bars(resolved) + resolved["max_horizon"] + _PROBE_EXTRA_BARS
    klines = [row for a, asset in enumerate(resolved["assets"]) for row in _probe_bars(asset, a, bars)]
    out = MinuteFeatures("probe", resolved).run(None, {"klines": klines})
    if not out["records"]:
        raise ValueError(f"the column probe produced no row ({bars} bars per asset): {out['provenance']['assets']}")
    return tuple(c for c in out["records"][0] if c.partition(_SEP)[0] in FAMILIES and _SEP in c)


def _probe_bars(asset, offset, count):
    """Return ``count`` deterministic one-minute kline rows for the column probe."""
    rows, price = [], 100.0
    for i in range(count):
        opened, price = price, price * math.exp(0.002 * math.sin(0.37 * i + offset) + 0.0005 * math.cos(1.3 * i))
        volume = 5.0 + (i + offset) % 7
        rows.append({
            "asset": asset, "open_time_ms": _PROBE_START_MS + i * BAR_MS, "open": opened,
            "high": max(opened, price) * 1.0005, "low": min(opened, price) * 0.9995, "close": price,
            "volume": volume, "close_time_ms": _PROBE_START_MS + (i + 1) * BAR_MS - 1,
            "trades": 50 + (i + offset) % 11, "taker_buy_volume": volume * 0.5})
    return rows


class FeatureGroups:
    """The families, the scales and the columns they own, with the arm that drops each group.

    Build one with :meth:`from_probe`.

    Parameters
    ----------
    families : tuple of str
        The families of the ``all`` arm, in its order.
    scales : tuple of int
        The scale grid, ascending.
    columns : tuple of str
        Every feature column, in the node's order.
    dropped : dict
        Arm name -> ``("family", name)`` or ``("scale", n)``.
    windows : dict
        The full window lists of the ``all`` arm, by node knob (``ewma_half_lives``, ``range_windows``,
        ``taker_windows``).

    Examples
    --------
    The groups of an arms document, with the columns probed from the feature node::

        groups = FeatureGroups.from_probe(arms_document, {"assets": ["BTC", "ETH"]})
        groups.columns_of_scale(60)   # -> ("ret__r60", "trend__ma_60", "vol__rms_60", "vol__ewma_60", ...)
        groups.dropped_by("minus_scale_60")   # -> ("scale", 60)
        groups.arm_params("minus_scale_60")["taker_windows"]   # -> [15]
    """

    def __init__(self, families, scales, columns, dropped, windows):
        self.families, self.scales, self.columns = tuple(families), tuple(scales), tuple(columns)
        self._dropped = dict(dropped)
        self._windows = {knob: tuple(windows[knob]) for knob in _WINDOW_KNOBS}
        self._check_columns()

    def _check_columns(self):
        """Refuse a scale-bearing column whose scale the grid lacks, and a grid scale no column carries."""
        seen = {self.scale_of(c) for c in self.columns} - {None}
        if seen != set(self.scales):
            raise ValueError(f"the columns carry scales {sorted(seen)}, the arms' grid is {list(self.scales)}")

    @classmethod
    def from_probe(cls, arms, feature_params):
        """Build the groups from an arms document and the feature node's columns.

        Parameters
        ----------
        arms : dict
            ``configs/features-arms.json``: ``arms`` (kept families, with ``all``) and ``scale_arms``
            (kept scales).
        feature_params : dict
            The :class:`MinuteFeatures` params other than ``families`` and ``scales`` (``assets`` at least).

        Returns
        -------
        FeatureGroups
            The families, scales, probed columns and per-arm dropped group.

        Raises
        ------
        ValueError
            ``families`` or ``scales`` in the params, no ``all`` arm, an arm that does not drop exactly one
            group, or columns whose scales are not the grid.
        """
        for knob in _FORBIDDEN_FEATURE_KNOBS:
            if knob in feature_params:
                raise ValueError(f"minute_features must not set {knob}: the arms supply it")
        if ALL_ARM not in arms.get("arms", {}):
            raise ValueError(f"the arms document has no {ALL_ARM!r} arm")
        families = list(arms["arms"][ALL_ARM])
        scales = sorted({s for kept in arms["scale_arms"].values() for s in kept})
        dropped = {}
        for arm, kept in arms["arms"].items():
            if arm != ALL_ARM:
                dropped[arm] = (_KIND_FAMILY, cls._one_dropped(arm, families, kept))
        for arm, kept in arms["scale_arms"].items():
            dropped[arm] = (_KIND_SCALE, cls._one_dropped(arm, scales, kept))
        resolved = MinuteFeatures.resolve({**feature_params, "families": families, "scales": scales})
        problems = MinuteFeatures.validate_params(resolved)
        if problems:
            raise ValueError(f"minute_features: {problems}")
        windows = {knob: resolved[knob] for knob in _WINDOW_KNOBS}
        groups = cls(families, scales, _probe_columns(json.dumps(resolved, sort_keys=True)), dropped, windows)
        groups._check_arms(feature_params)
        return groups

    def _check_arms(self, feature_params):
        """Refuse an arm whose parameters the feature node would not accept, naming the arm."""
        for arm in (ALL_ARM, *self.ablation_arms()):
            problems = MinuteFeatures.validate_params({**feature_params, **self.arm_params(arm)})
            if problems:
                raise ValueError(f"arm {arm!r}: minute_features: {problems}")

    @staticmethod
    def _one_dropped(arm, universe, kept):
        """Return the single member of ``universe`` that ``kept`` leaves out, or refuse naming the arm."""
        left = [g for g in universe if g not in kept]
        extra = [g for g in kept if g not in universe]
        if len(left) != 1 or extra:
            raise ValueError(f"arm {arm!r} must drop exactly one group of {list(universe)}, "
                             f"drops {left} and adds {extra}")
        return left[0]

    def scale_of(self, column):
        """Return the scale a column carries, or None.

        Parameters
        ----------
        column : str
            A ``<family>__<name>`` column.

        Returns
        -------
        int or None
            The minutes of a column named ``r<n>``, ``ma_<n>``, ``rms_<n>``, ``hl_<n>``, ``jump_<n>`` or
            ``pair_chg_<n>``, or of a window column (``ewma_<n>``, ``hi_<n>``, ``lo_<n>``, ``taker_<n>``)
            whose ``n`` is a minute of the grid; None for every other column.
        """
        family, _, name = column.partition(_SEP)
        if family not in _SCALE_FAMILIES:
            return None
        grid, window = _GRID_NAME.match(name), _WINDOW_NAME.match(name)
        if grid:
            return int(grid.group(1))
        return int(window.group(1)) if window and int(window.group(1)) in self.scales else None

    def family_of(self, column):
        """Return the family of a column (its prefix)."""
        return column.partition(_SEP)[0]

    def columns_of_family(self, family):
        """Return the columns a family owns, in the node's order."""
        return tuple(c for c in self.columns if self.family_of(c) == family)

    def columns_of_scale(self, scale):
        """Return the columns that carry ``scale``, in the node's order."""
        return tuple(c for c in self.columns if self.scale_of(c) == scale)

    def select_columns(self, families, scales):
        """Return the columns of the kept families whose scale (if any) is kept.

        Parameters
        ----------
        families : iterable of str
            The kept families.
        scales : iterable of int
            The kept scales.

        Returns
        -------
        tuple of str
            The kept columns, in the node's order.
        """
        families, scales = set(families), set(scales)
        return tuple(c for c in self.columns
                     if self.family_of(c) in families and self.scale_of(c) in (scales | {None}))

    def params_for(self, families, scales):
        """Return the feature node's scale-and-window params for kept families and scales.

        Parameters
        ----------
        families : iterable of str
            The kept families.
        scales : iterable of int
            The kept scales.

        Returns
        -------
        dict
            ``families``, ``scales``, the three window knobs with the windows of a dropped scale removed (a
            window that is not a minute of the grid stays), and ``taker_check_windows`` (every taker window).
        """
        families, scales = list(families), list(scales)
        params = {"families": families, "scales": scales}
        for knob, full in self._windows.items():
            params[knob] = [w for w in full if w not in self.scales or w in scales]
        params[_CHECK_KNOB] = list(self._windows["taker_windows"])
        return params

    def arm_params(self, arm):
        """Return the feature node's params for one arm (``all`` included).

        Parameters
        ----------
        arm : str
            ``all`` or an arm of the arms document.

        Returns
        -------
        dict
            As :meth:`params_for`, for the families and scales the arm keeps.

        Raises
        ------
        ValueError
            An arm the arms document does not define.
        """
        families, scales = list(self.families), list(self.scales)
        if arm != ALL_ARM:
            if arm not in self._dropped:
                raise ValueError(f"unknown arm {arm!r}: the arms document defines {[ALL_ARM, *self._dropped]}")
            kind, name = self._dropped[arm]
            families = [f for f in families if not (kind == _KIND_FAMILY and f == name)]
            scales = [s for s in scales if not (kind == _KIND_SCALE and s == name)]
        return self.params_for(families, scales)

    def dropped_by(self, arm):
        """Return the group an arm drops: ``("family", name)`` or ``("scale", n)``."""
        return self._dropped[arm]

    def ablation_arms(self):
        """Return the arms that each drop one group (every arm but ``all``), in the arms document's order."""
        return tuple(self._dropped)


class _ClassScores:
    """One class's fold scores per arm, read off its run-root pattern (``{arm}`` filled per arm)."""

    def __init__(self, name, runs, arm_names):
        self.name = name
        self.arms = {arm: self._read(runs, arm) for arm in arm_names}
        self.selected = self._read_optional(runs, SELECTED_ARM)

    def _read(self, runs, arm):
        """Read one arm, naming class and arm in any refusal."""
        try:
            return FoldScores.read(runs.format(arm=arm))
        except ValueError as error:
            raise ValueError(f"class {self.name!r}, arm {arm!r}: {error}") from error

    def _read_optional(self, runs, arm):
        """Read the selected arm when its directory exists, else None."""
        return self._read(runs, arm) if os.path.isdir(runs.format(arm=arm)) else None

    @property
    def reference(self):
        """The ``all`` arm's scores."""
        return self.arms[ALL_ARM]


class _ClassSelection:
    """One class's verdicts, kept groups, check and file record."""

    def __init__(self, scores, groups, rule, arms_hash):
        self.scores, self.groups, self.rule, self.arms_hash = scores, groups, rule, arms_hash
        self.verdicts = {}
        for arm in groups.ablation_arms():
            kind, name = groups.dropped_by(arm)
            self.verdicts[f"{kind}:{name}"] = {"arm": arm, "kind": kind, "name": name,
                                               **rule.judge(scores.reference, scores.arms[arm])}

    def kept(self, kind, universe):
        """Return the members of ``universe`` the rule keeps on one axis."""
        return [g for g in universe if self.verdicts[f"{kind}:{g}"]["keep"]]

    def record(self):
        """Build the class's selection record, applying the empty-axis rule and the selected-arm check."""
        groups, flags = self.groups, []
        families = self.kept(_KIND_FAMILY, groups.families)
        scales = self.kept(_KIND_SCALE, groups.scales)
        if not families:
            families, flags = list(groups.families), flags + ["no_family_kept"]
        if not scales:
            scales, flags = list(groups.scales), flags + ["no_scale_kept"]
        check, before = self._check(), None
        if check["status"] == "fell_back":
            before = {"families": families, "scales": scales}
            families, scales = list(groups.families), list(groups.scales)
            flags.append("selected_arm_worse_than_all")
        record = {
            "class": self.scores.name,
            "rule": {"z": self.rule.z, "min_folds": self.rule.min_folds, "reference_arm": ALL_ARM,
                     "objective": self.scores.reference.objective},
            "folds": {"n": len(self.scores.reference.cutoffs), "cutoffs": list(self.scores.reference.cutoffs)},
            "families": {"kept": families, "dropped": [f for f in groups.families if f not in families]},
            "scales": {"kept": scales, "dropped": [s for s in groups.scales if s not in scales]},
            "columns": list(groups.select_columns(families, scales)),
            "selected_arm": groups.params_for(families, scales),
            "check": check, "fell_back_to_all": before is not None, "flags": flags,
            "evidence": self.verdicts, "sources": self._sources(),
        }
        if before is not None:
            record["selection_before_fallback"] = before
        return record

    def _check(self):
        """Compare the selected arm with ``all``: pending without one, else passed or fell_back."""
        if self.scores.selected is None:
            return {"status": "pending", "arm": SELECTED_ARM}
        result = self.rule.worse(self.scores.reference, self.scores.selected)
        status = "fell_back" if result.pop("worse") else "passed"
        return {"status": status, "arm": SELECTED_ARM, **result}

    def _sources(self):
        """Return every file the selection read, by arm, with its hash."""
        sources = {arm: {"path": s.path, "sha256": s.sha256} for arm, s in self.scores.arms.items()}
        if self.scores.selected is not None:
            sources[SELECTED_ARM] = {"path": self.scores.selected.path, "sha256": self.scores.selected.sha256}
        sources["arms_file"] = {"sha256": self.arms_hash}
        return sources


class AblationSelect(Node):
    """Read each class's per-arm walk-forward summaries and write its kept feature groups (role ``report``).

    No input ports. Outputs: ``selected`` (class name -> the record written to
    ``selected-features-<class>.json``) and ``disagreements`` (groups kept by some classes and dropped by
    others, with each class's mean difference and standard error, written to
    ``feature-selection-disagreements.json``). Both files go to this node's artifact directory in the run.

    Parameters
    ----------
    params : dict
        ``arms_file`` (str, path to ``features-arms.json``), ``classes`` (object: class name -> ``{"runs":
        pattern}``, the arm's run root with ``{arm}`` where the arm's name goes; ``notes`` allowed),
        ``minute_features`` (the :class:`MinuteFeatures` params other than ``families``, ``scales`` and
        ``taker_check_windows``, which the arms supply; ``assets`` required), ``z`` (float >= 0, default :data:`DEFAULT_Z`), ``min_folds`` (int >= 2, default
        :data:`DEFAULT_MIN_FOLDS`).

    Examples
    --------
    Select for two classes from their arm summaries::

        node = AblationSelect("select", {
            "arms_file": "configs/features-arms.json",
            "classes": {"sequence": {"runs": "pipeline_runs/select-sequence/{arm}/runs/{arm}"},
                        "tabular": {"runs": "pipeline_runs/select-tabular/{arm}/runs/{arm}"}},
            "minute_features": {"assets": ["BTC", "ETH"]}})
        out = node.run(ctx, {})
        # -> out["selected"]["sequence"]["families"]["kept"], out["disagreements"]
    """

    role = "report"
    outputs = ("selected", "disagreements")
    DEFAULTS = {"z": DEFAULT_Z, "min_folds": DEFAULT_MIN_FOLDS}
    _PARAMS = ("arms_file", "classes", "minute_features") + tuple(DEFAULTS)
    _CLASS_KEYS = ("runs", "notes")

    @classmethod
    def validate_params(cls, params):
        """Default-deny check of the knobs.

        Parameters
        ----------
        params : dict
            The declared params.

        Returns
        -------
        list of str
            Problems; empty when none.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        full = {**cls.DEFAULTS, **params}
        if not isinstance(full.get("arms_file"), str) or not full["arms_file"]:
            problems.append(f"arms_file must be a non-empty path, got {full.get('arms_file')!r}")
        problems += cls._classes_problems(full.get("classes"))
        problems += cls._features_problems(full.get("minute_features"))
        problems += KeepRule.problems(full["z"], full["min_folds"])
        return problems

    @classmethod
    def _classes_problems(cls, classes):
        """Problems with ``classes``: names that can name a file, each with a non-empty ``runs``."""
        if not isinstance(classes, dict) or not classes:
            return [f"classes must be a non-empty object of class name -> {{runs}}, got {classes!r}"]
        problems = []
        for name, spec in classes.items():
            if not isinstance(name, str) or not _CLASS_NAME.match(name):
                problems.append(f"class name must match {_CLASS_NAME.pattern}, got {name!r}")
            if not isinstance(spec, dict) or set(spec) - set(cls._CLASS_KEYS):
                problems.append(f"class {name!r} must be an object with runs (and optional notes), got {spec!r}")
            elif not cls._pattern_ok(spec.get("runs")):
                problems.append(f"class {name!r}: runs must be a path pattern holding {{arm}} and no other field, "
                                f"got {spec.get('runs')!r}")
        return problems

    @staticmethod
    def _pattern_ok(runs):
        """Say whether ``runs`` is a string whose only format field is ``{arm}``, present at least once."""
        if not isinstance(runs, str):
            return False
        try:
            fields = [f for _, f, _, _ in string.Formatter().parse(runs) if f is not None]
        except ValueError:
            return False
        return bool(fields) and set(fields) == {"arm"}

    @staticmethod
    def _features_problems(features):
        """Problems with ``minute_features``: an object the node accepts once an arm supplies families and scales."""
        if not isinstance(features, dict):
            return [f"minute_features must be an object with at least assets, got {features!r}"]
        problems = [f"minute_features must not set {k}: the arms supply it" for k in _FORBIDDEN_FEATURE_KNOBS if k in features]
        if not problems:
            problems += MinuteFeatures.validate_params({**features, "families": [FAMILIES[0]]})
        return problems

    def run(self, ctx, inputs):
        """Read the summaries, apply the rule per class, write the files.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; its run directory receives the artifacts.
        inputs : dict
            Unused.

        Returns
        -------
        dict
            ``selected`` and ``disagreements`` as above.

        Raises
        ------
        ValueError
            A missing or unfinished arm summary, classes scored on different folds or objectives, an arm that
            does not drop exactly one group, or fewer folds than ``min_folds``.
        """
        full = {**self.DEFAULTS, **self.params}
        arms_path = full["arms_file"]
        with open(arms_path, encoding="utf-8") as handle:
            arms = json.load(handle)
        groups = FeatureGroups.from_probe(arms, full["minute_features"])
        arm_names = [ALL_ARM, *groups.ablation_arms()]
        scores = [_ClassScores(name, spec["runs"], arm_names) for name, spec in full["classes"].items()]
        for other in scores[1:]:
            scores[0].reference.same_folds(other.reference)
        rule = KeepRule(full["z"], full["min_folds"])
        selections = [_ClassSelection(s, groups, rule, _sha256(arms_path)) for s in scores]
        selected = {sel.scores.name: sel.record() for sel in selections}
        disagreements = self._disagreements(selections)
        for name, record in selected.items():
            self.write_artifact(ctx, SELECTED_FILE.format(name=name), record)
        self.write_artifact(ctx, DISAGREEMENTS_FILE, disagreements)
        return {"selected": selected, "disagreements": disagreements}

    @staticmethod
    def _disagreements(selections):
        """List the groups the classes' rule verdicts split on, with every class's (mean, SE)."""
        rows = []
        for label, first in selections[0].verdicts.items():
            kept = {sel.scores.name: sel.verdicts[label]["keep"] for sel in selections}
            if len(set(kept.values())) > 1:
                rows.append({"group": label, "kind": first["kind"], "name": first["name"], "kept": kept,
                             "by_class": {sel.scores.name: {k: sel.verdicts[label][k] for k in ("arm", "mean", "se", "t", "n")}
                                          for sel in selections}})
        return rows
