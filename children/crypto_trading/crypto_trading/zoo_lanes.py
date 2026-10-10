"""The predictor zoo's lane generator: one workflow-batch manifest, one lane per zoo cell.

The zoo is a grid (plan 2026-10-09-data-windows.md): model x loss x regime, plus two baselines. Each lane is one
``dskit.pipeline walkforward`` of a dskit panel trainer (``PanelTrain``) or baseline (``EmpiricalBaseline`` /
``EwmaStudentBaseline``) and ``PanelScore`` over the minute panel, on the W1 folds. This module only WRITES the
manifest: every value comes from the recipe (``configs/zoo-lanes-recipe.json``) or from the selection manifest it
names as ``base`` (panel, window, folds, training, grid, zoo), so a number has one home and a test pins the
manifest to a fresh generation. Nothing here trains, scores or imports torch.

A lane's whole ``fit`` and ``score`` params are written into ``args.cell.<lane>`` (generated, auditable); the one
template (``zoo-template.json``) only places them. :meth:`ZooLanes.w2_manifest` turns chosen lanes into the
single-read W2 manifest by swapping the walk-forward bounds for W2's fixed ones.

Per-class features: ``recipe.features.classes.<class>.from`` names a feature-selection output (a directory holding
``selected-features-<class>.json``, or that file) and a model of that class (``recipe.models.<m>.class``) then reads
the selected columns; a class with no source keeps ``features.context``. A named source that is missing or empty
refuses. The same module is the CLI of the zoo's later steps (``pick``, ``hpo-manifest``, ``hpo-pick``,
``frozen-manifest``), each a thin dispatch to :mod:`crypto_trading.zoo_pick` / :mod:`crypto_trading.zoo_hpo`.

Import cost: stdlib + the selection module (stdlib + dskit core).
"""

import argparse
import copy
import hashlib
import importlib
import json
import os
import sys
from datetime import date, timedelta

from .feature_selection import SELECTED_FILE

__all__ = ["ClassFeatures", "FoldSchedule", "POOLED", "UNPOOLED", "ZooLanes", "main"]

_REF = "@"
_NOTES = "notes"
POOLED, UNPOOLED = "pooled", "unpooled"
_SPLITS = {"train_end_ms": "$splits.train_end_ms", "val_start_ms": "$splits.val_start_ms",
           "val_end_ms": "$splits.val_end_ms"}
_INHERITED = ("panel", "window", "folds", "training", "grid", "zoo")
_TRAIN_USES = "dskit.pipeline.libs.panel_torch:PanelTrain"
_SCORE_USES = "dskit.pipeline.libs.panel_score:PanelScore"
_CELL_KEYS = ("uses", "fit", "score")
_UNFILLED_LANE = "finalist"
_UNFILLED_CELL = "<frozen finalist: a lane's cell from workflow-zoo-w1.json, regenerated with W2's bounds by zoo_lanes>"
_BASELINE_PACK = "dskit.pipeline.libs.panel_score"


class FoldSchedule:
    """An expanding walk-forward schedule: ``count`` cutoffs ``step_days`` apart from ``first``.

    Parameters
    ----------
    folds : dict
        ``first`` (``YYYY-MM-DD``), ``step_days``, ``count``, ``val_days``, ``embargo_days`` (the manifest's ``folds``).

    Examples
    --------
    The folds after a 4-fold warmup::

        schedule = FoldSchedule({"first": "2024-01-01", "step_days": 60, "count": 14, "val_days": 60, "embargo_days": 1})
        schedule.window(4, 10).cutoffs()[0]
        # -> "2024-08-28"
    """

    KEYS = ("first", "step_days", "count", "val_days", "embargo_days")

    def __init__(self, folds):
        self.folds = {k: folds[k] for k in self.KEYS}

    def cutoffs(self):
        """Return the cutoffs (``YYYY-MM-DD``), in order."""
        first, step = date.fromisoformat(self.folds["first"]), self.folds["step_days"]
        return [(first + timedelta(days=i * step)).isoformat() for i in range(self.folds["count"])]

    def window(self, skip, count):
        """Return the schedule of ``count`` folds starting ``skip`` folds in (same step, val and embargo)."""
        if skip < 0 or count < 1 or skip + count > self.folds["count"]:
            raise ValueError(f"folds {skip}+{count} do not fit a schedule of {self.folds['count']}")
        first = date.fromisoformat(self.folds["first"]) + timedelta(days=skip * self.folds["step_days"])
        return FoldSchedule({**self.folds, "first": first.isoformat(), "count": count})

    def val_end(self):
        """Return the exclusive end date of the last validation window."""
        return date.fromisoformat(self.cutoffs()[-1]) + timedelta(days=self.folds["val_days"])

    def record(self, notes):
        """Return the schedule as a manifest ``folds`` block with ``notes``."""
        return {"notes": notes, **self.folds}


class ClassFeatures:
    """The feature context each model class trains on: a selection file's columns, or the recipe default.

    Parameters
    ----------
    features : dict
        ``recipe["features"]``: ``context`` (the default ColumnSelector) and ``classes`` (class name to
        ``{"from": <directory or file or null>}``; ``notes`` is skipped).
    overrides : dict, optional
        Class name to a ``from`` replacing the recipe's (the command line's ``--features-from``).

    Raises
    ------
    ValueError
        A ``from`` naming a class the recipe does not declare, a missing file, or a file that is not JSON, is
        for another class, or holds no columns.

    Examples
    --------
    Read the sequence class from a selection run's artifacts::

        sources = ClassFeatures(recipe["features"], {"sequence": "pipeline_runs/select-features/artifacts/select"})
        sources.context("sequence")
        # -> {"names": ["ret__r5", "vol__rms_60", ...]}
    """

    def __init__(self, features, overrides=None):
        self.default = features["context"]
        self.sources = {k: v for k, v in features["classes"].items() if k != _NOTES}
        unknown = sorted(set(overrides or {}) - set(self.sources))
        if unknown:
            raise ValueError(f"features-from names {unknown}, but the recipe's classes are {sorted(self.sources)}")
        self.paths = {name: (overrides or {}).get(name, spec.get("from")) for name, spec in self.sources.items()}
        self.resolved = {name: self._resolve(name, path) for name, path in self.paths.items()}

    @staticmethod
    def _locate(name, path):
        """Return the selection file of class ``name`` under ``path`` (a directory or the file itself)."""
        return os.path.join(path, SELECTED_FILE.format(name=name)) if os.path.isdir(path) else path

    def _resolve(self, name, path):
        """Return ``{"from", "context", "sha256"}`` of one class; the default context when ``path`` is null."""
        if path is None:
            return {"from": None, "context": copy.deepcopy(self.default)}
        found = self._locate(name, path)
        if not os.path.isfile(found):
            raise ValueError(f"features.classes.{name}.from: no selection file at {found!r}")
        with open(found, "rb") as handle:
            raw = handle.read()
        try:
            record = json.loads(raw)
        except ValueError as err:
            raise ValueError(f"{found}: not JSON ({err})") from err
        if not isinstance(record, dict) or record.get("class") != name:
            raise ValueError(f"{found}: the selection is for class "
                             f"{record.get('class') if isinstance(record, dict) else record!r}, not {name!r}")
        columns = record.get("columns")
        if not (isinstance(columns, list) and columns and all(isinstance(c, str) and c for c in columns)
                and len(set(columns)) == len(columns)):
            raise ValueError(f"{found}: `columns` must be a non-empty list of distinct column names, got {columns!r}")
        return {"from": found, "context": {"names": list(columns)}, "sha256": hashlib.sha256(raw).hexdigest()}

    def context(self, name):
        """Return a copy of the ColumnSelector class ``name`` trains on (``KeyError`` for an undeclared class)."""
        return copy.deepcopy(self.resolved[name]["context"])

    def record(self, features):
        """Return ``features`` with each class's resolved ``from``, ``context`` and ``sha256`` filled in."""
        out = copy.deepcopy(features)
        for name, resolved in self.resolved.items():
            out["classes"][name].update(copy.deepcopy(resolved))
        return out


class ZooLanes:
    """Build the zoo manifest from a recipe and the selection manifest it inherits from.

    Parameters
    ----------
    recipe : dict
        ``configs/zoo-lanes-recipe.json``: ``base`` (the selection manifest's file name), ``models``, ``losses``,
        ``regimes``, ``baselines``, ``features``, ``work_dir``, ``adapter``, ``template``.
    base : dict
        The selection manifest named by ``recipe["base"]``; its ``args`` supply panel, window, folds, training,
        grid and zoo.
    features_from : dict, optional
        Class name to a feature-selection directory or file, replacing the recipe's ``from`` for that class.

    Examples
    --------
    Generate the manifest of a recipe::

        lanes = ZooLanes(recipe, base_manifest)
        manifest = lanes.manifest()
        # -> manifest["args"]["arms"][:2] == ["patchtst-pinball-pooled", "patchtst-pinball-btc"]
    """

    def __init__(self, recipe, base, features_from=None):
        self.recipe, self.base = recipe, base["args"]
        self.window = self.base["window"]
        self.zoo, self.grid, self.training = self.base["zoo"], self.base["grid"], self.base["training"]
        self.classes = ClassFeatures(recipe["features"], features_from)

    def _ref(self, value):
        """Resolve ``"@a.b"`` against the base args; any other value is returned as given."""
        if not (isinstance(value, str) and value.startswith(_REF)):
            return copy.deepcopy(value)
        node = self.base
        for part in value[len(_REF):].split("."):
            node = node[part]
        return copy.deepcopy(node)

    def _bounds(self, bounds=None):
        """Return the five band cuts: ``$splits`` references unless ``bounds`` fixes the three instants."""
        return {**(bounds or _SPLITS), "cal_days": self.zoo["cal_days"], "min_run": self.zoo["min_run"]}

    def _panel(self):
        """Return the panel spec the dskit nodes take: the four store keys of ``args.panel``."""
        return {k: self.base["panel"][k] for k in ("root", "source", "stream", "relpath")}

    def _regimes(self):
        """Yield ``(suffix, group codes, asset indicator)``: pooled, then one lane per asset."""
        groups = self.window["groups"]
        yield "pooled", list(groups), True
        for name, code in zip(self.window["assets"], groups):
            yield name.lower(), [code], False

    def scopes(self):
        """Return ``{scope: [lane suffixes]}``: ``pooled`` is one lane, ``unpooled`` one lane per asset.

        Returns
        -------
        dict
            The two scopes in grid order, each a list of the suffixes its lanes end in.
        """
        out = {}
        for suffix, _, indicator in self._regimes():
            out.setdefault(POOLED if indicator else UNPOOLED, []).append(suffix)
        return out

    def cell_names(self):
        """Return ``(model cells, baseline cells)``: ``<model>-<loss>`` names, then the baselines' names."""
        return [f"{m}-{loss}" for m in self.recipe["models"] for loss in self.recipe["losses"]], list(self.recipe["baselines"])

    def _head(self, loss):
        """Return the horizon head of one loss variant, its wings and levels read from the grid."""
        spec = self.recipe["losses"][loss]
        common = self.grid["common"]
        if spec["head"] == "quantile":
            return {"kind": "quantile", "quantiles": list(common["levels"]), "bounds": [common["z_min"], common["z_max"]]}
        terms = self._ref(spec["terms"])
        head = {"kind": "mixture", "components": self.training["components"], "family": self.training["family"],
                "losses": terms, "min_scale": self.training["min_scale"]}
        if "compile" in self.training:
            head["compile"] = self.training["compile"]
        if any(t["kind"] == "tail_crps" for t in terms):
            head["tail_intervals"] = copy.deepcopy(common["tail_intervals"])
        return head

    def _train(self, model, loss, groups, indicator, bounds=None):
        """Return the ``PanelTrain`` params of one cell."""
        spec, t = self.recipe["models"][model], self.training
        params = {"panel": self._panel(), "assets": groups, "asset_indicator": indicator, "bounds": self._bounds(bounds),
                  "context": self.classes.context(spec["class"]), "trunk": copy.deepcopy(self.zoo["trunk"]),
                  "head": self._head(loss), "missing": t["missing"], "device": t["device"], "epochs": t["epochs"],
                  "patience": t["patience"], "row_stride": t["row_stride"], "monitor": t["monitor"], "lr": t["lr"], "optimizer": t["optimizer"],
                  "optimizer_params": {"weight_decay": t["weight_decay"]},
                  "loader": {"batch_size": t["batch_size"], "seed": t["seed"]}}
        if spec.get("encoder"):
            params["sequence"] = {"columns": copy.deepcopy(self.zoo["sequence_columns"]), "window": self.zoo["window"],
                                  "stride": self.zoo["stride"]}
            params["encoder"] = self._ref(spec["encoder"])
        return params

    def _baseline(self, name, groups, bounds=None):
        """Return the params of one baseline node (they pool the ``groups`` they are given)."""
        params = {"panel": self._panel(), "assets": groups, "bounds": self._bounds(bounds),
                  "grid": copy.deepcopy(self.grid["common"])}
        params.update(copy.deepcopy(self.recipe["baselines"][name]["params"]))
        return params

    def _score(self, groups, bounds=None):
        """Return the ``PanelScore`` params of one cell."""
        return {"panel": self._panel(), "bounds": self._bounds(bounds), "assets": groups,
                "grid": copy.deepcopy(self.grid["common"]), "tail_weight": self.grid["tail_weight"], "split": "val"}

    def cells(self, bounds=None):
        """Return ``{lane: {"uses", "fit", "score"}}`` for every trainable cell and baseline.

        Parameters
        ----------
        bounds : dict, optional
            ``train_end_ms`` / ``val_start_ms`` / ``val_end_ms`` instants replacing the ``$splits`` references
            (the W2 read).

        Returns
        -------
        dict
            Lane name to its node kind and full ``fit`` / ``score`` params, trainable cells first.
        """
        cells = {}
        for suffix, groups, indicator in self._regimes():
            for model in self.recipe["models"]:
                for loss in self.recipe["losses"]:
                    cells[f"{model}-{loss}-{suffix}"] = {
                        "uses": _TRAIN_USES, "fit": self._train(model, loss, groups, indicator, bounds),
                        "score": self._score(groups, bounds)}
        for suffix, groups, _ in self._regimes():
            for name, spec in self.recipe["baselines"].items():
                cells[f"{name}-{suffix}"] = {"uses": f"{_BASELINE_PACK}:{spec['class']}",
                                             "fit": self._baseline(name, groups, bounds), "score": self._score(groups, bounds)}
        return cells

    def manifest(self, cells=None, work_dir=None, folds=None, template=None):
        """Return the W1 walk-forward manifest, one lane per cell.

        Parameters
        ----------
        cells : dict, optional
            ``{lane: {"uses", "fit", "score"}}`` replacing :meth:`cells` (the HPO steps' own lanes).
        work_dir : str, optional
            Replaces ``recipe["work_dir"]``.
        folds : dict, optional
            Replaces the inherited ``folds`` (same keys).
        template : str, optional
            Replaces ``recipe["template"]`` (a path the manifest's directory resolves).

        Returns
        -------
        dict
            A workflow-batch manifest (ADR-0227): ``args`` (inherited panel, window, folds, training, grid, zoo,
            plus ``arms`` and ``cell``), ``lanes``, ``layout``, ``registry`` and ``steps``.
        """
        cells = self.cells() if cells is None else cells
        args = {"notes": self.recipe["args_notes"], "work_dir": work_dir or self.recipe["work_dir"], "adapter": self.recipe["adapter"],
                **{k: copy.deepcopy(self.base[k]) for k in _INHERITED},
                "features": self.classes.record(self.recipe["features"]), "arms": list(cells),
                "cell": {"notes": "Generated by crypto_trading.zoo_lanes from zoo-lanes-recipe.json: each lane's full fit and score params. Edit the recipe, never this block.", **cells}}
        if folds is not None:
            args["folds"] = copy.deepcopy(folds)
        return {"notes": self.recipe["notes"], "args": args,
                "lanes": {"notes": "One chain per cell: `$lane` binds the cell name and `cell` (keyed by lane) is narrowed to the lane's own entry.", "key": "arms", "keyed": ["cell"]},
                "layout": {"fit": {"dir": "{W}/fit", "runs": "{W}/runs/{A}"}},
                "registry": {"fit": {"notes": "One walk-forward per cell; its summary lands under the layout's `runs` directory.",
                                     "template": template or self.recipe["template"], "extends": None,
                                     "command": "{python} -m dskit.pipeline walkforward {config} --asof {in.asof} --adapter {in.adapter}"}},
                "steps": {"fit": {"notes": "The as-of date is W1's end, so run names say what the data stops at.", "registry": "fit",
                                  "in": {"A": "$lane", "W": "$args.work_dir", "asof": "$args.window.end", "adapter": "$args.adapter",
                                         "cell": "$args.cell.$lane", "folds": "$args.folds"}, "out": ["runs"]}}}

    def w2_manifest(self, finalists, w2, template):
        """Return the single-read W2 manifest for frozen finalists.

        Parameters
        ----------
        finalists : list of str
            Lane names of the W1 cells chosen to be read once (unknown names raise ``KeyError``); empty gives one
            lane of unfilled markers, the skeleton.
        w2 : dict
            ``train_end_ms``, ``val_start_ms``, ``val_end_ms`` (the fixed instants) and ``notes``.
        template : str
            The W2 template file name.

        Returns
        -------
        dict
            A workflow manifest whose cells carry the W2 bounds in place of ``$splits``.
        """
        cells = self.cells({k: w2[k] for k in _SPLITS})
        chosen = {lane: cells[lane] for lane in finalists} or {_UNFILLED_LANE: dict.fromkeys(_CELL_KEYS, _UNFILLED_CELL)}
        manifest = self.manifest()
        manifest["args"].pop("folds")
        manifest["args"]["w2"] = copy.deepcopy(w2)
        manifest["args"]["arms"] = list(chosen)
        manifest["args"]["cell"] = {"notes": "Frozen finalists' params with W2's fixed bounds.", **chosen}
        manifest["args"]["work_dir"] = self.recipe["w2"]["work_dir"]
        manifest["registry"]["fit"]["template"] = template
        manifest["registry"]["fit"]["command"] = "{python} -m dskit.pipeline run {config} --asof {in.asof} --adapter {in.adapter}"
        manifest["steps"]["fit"]["in"].pop("folds")
        manifest["steps"]["fit"]["in"]["asof"] = "$args.w2.asof"
        return manifest


#: The zoo's later steps: subcommand to the ``module:function`` that runs it (each takes the remaining argv).
COMMANDS = {"pick": "crypto_trading.zoo_pick:main", "hpo-manifest": "crypto_trading.zoo_hpo:hpo_manifest_main",
            "hpo-pick": "crypto_trading.zoo_hpo:hpo_pick_main", "frozen-manifest": "crypto_trading.zoo_hpo:frozen_manifest_main",
            "hpo-evaluate": "crypto_trading.zoo_hpo:evaluate_main", "production-manifest": "crypto_trading.zoo_production:manifest_main",
            "production-index": "crypto_trading.zoo_production:index_main"}


def _features_from(pairs):
    """Turn ``["sequence=DIR", ...]`` into ``{"sequence": "DIR"}``."""
    out = {}
    for pair in pairs or ():
        name, sep, path = pair.partition("=")
        if not (name and sep and path):
            raise SystemExit(f"--features-from takes CLASS=PATH, got {pair!r}")
        out[name] = path
    return out


def main(argv=None):
    """Write the W1 manifest (and optionally the W2 skeleton) from a recipe, or run a later step.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments; ``recipe`` is the recipe file, ``--out`` the W1 manifest, ``--w2-out`` the W2 one,
        ``--features-from CLASS=PATH`` (repeatable) a class's feature-selection output. A first argument in
        :data:`COMMANDS` runs that step instead; ``--hpo-out`` / ``--production-out`` write the two pipelines.

    Returns
    -------
    int
        0 on success; a later step returns its own exit code.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in COMMANDS:
        module, _, name = COMMANDS[argv[0]].partition(":")
        return getattr(importlib.import_module(module), name)(argv[1:])
    parser = argparse.ArgumentParser(description="Generate the zoo's workflow-batch manifest from its recipe.",
                                     epilog=f"Later steps: {', '.join(COMMANDS)} (each with --help).")
    parser.add_argument("recipe")
    parser.add_argument("--out", required=True)
    parser.add_argument("--w2-out")
    parser.add_argument("--hpo-out", help="the HPO pipeline (one workflow manifest)")
    parser.add_argument("--production-out", help="the production pipeline (one workflow manifest)")
    parser.add_argument("--features-from", action="append", metavar="CLASS=PATH",
                        help="a class's feature-selection directory or selected-features-<class>.json")
    args = parser.parse_args(argv)
    here = os.path.dirname(os.path.abspath(args.recipe))
    with open(args.recipe, encoding="utf-8") as handle:
        recipe = json.load(handle)
    with open(os.path.join(here, recipe["base"]), encoding="utf-8") as handle:
        lanes = ZooLanes(recipe, json.load(handle), _features_from(args.features_from))
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(lanes.manifest(), indent=2) + "\n")
    if args.w2_out:
        with open(args.w2_out, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(lanes.w2_manifest(recipe["w2"]["finalists"], recipe["w2"]["bounds"], recipe["w2"]["template"]),
                                    indent=2) + "\n")
    for flag, pipeline in ((args.hpo_out, "HpoPipeline"), (args.production_out, "ProductionPipeline")):
        if flag:
            module = importlib.import_module("crypto_trading.zoo_pipelines")
            with open(flag, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(getattr(module, pipeline)(recipe).manifest(), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
