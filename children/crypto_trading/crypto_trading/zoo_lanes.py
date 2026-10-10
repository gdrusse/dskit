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

Import cost: stdlib only.
"""

import argparse
import copy
import json
import os

__all__ = ["ZooLanes", "main"]

_REF = "@"
_SPLITS = {"train_end_ms": "$splits.train_end_ms", "val_start_ms": "$splits.val_start_ms",
           "val_end_ms": "$splits.val_end_ms"}
_INHERITED = ("panel", "window", "folds", "training", "grid", "zoo")
_TRAIN_USES = "dskit.pipeline.libs.panel_torch:PanelTrain"
_SCORE_USES = "dskit.pipeline.libs.panel_score:PanelScore"
_CELL_KEYS = ("uses", "fit", "score")
_UNFILLED_LANE = "finalist"
_UNFILLED_CELL = "<frozen finalist: a lane's cell from workflow-zoo-w1.json, regenerated with W2's bounds by zoo_lanes>"
_BASELINE_PACK = "dskit.pipeline.libs.panel_score"


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

    Examples
    --------
    Generate the manifest of a recipe::

        lanes = ZooLanes(recipe, base_manifest)
        manifest = lanes.manifest()
        # -> manifest["args"]["arms"][:2] == ["patchtst-pinball-pooled", "patchtst-pinball-btc"]
    """

    def __init__(self, recipe, base):
        self.recipe, self.base = recipe, base["args"]
        self.window = self.base["window"]
        self.zoo, self.grid, self.training = self.base["zoo"], self.base["grid"], self.base["training"]

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

    def _head(self, loss):
        """Return the horizon head of one loss variant, its wings and levels read from the grid."""
        spec = self.recipe["losses"][loss]
        common = self.grid["common"]
        if spec["head"] == "quantile":
            return {"kind": "quantile", "quantiles": list(common["levels"]), "bounds": [common["z_min"], common["z_max"]]}
        terms = self._ref(spec["terms"])
        head = {"kind": "mixture", "components": self.training["components"], "family": self.training["family"],
                "losses": terms, "min_scale": self.training["min_scale"]}
        if any(t["kind"] == "tail_crps" for t in terms):
            head["tail_intervals"] = copy.deepcopy(common["tail_intervals"])
        return head

    def _train(self, model, loss, groups, indicator, bounds=None):
        """Return the ``PanelTrain`` params of one cell."""
        spec, t = self.recipe["models"][model], self.training
        params = {"panel": self._panel(), "assets": groups, "asset_indicator": indicator, "bounds": self._bounds(bounds),
                  "context": copy.deepcopy(self.recipe["features"]["context"]), "trunk": copy.deepcopy(self.zoo["trunk"]),
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

    def manifest(self):
        """Return the W1 walk-forward manifest, one lane per cell.

        Returns
        -------
        dict
            A workflow-batch manifest (ADR-0227): ``args`` (inherited panel, window, folds, training, grid, zoo,
            plus ``arms`` and ``cell``), ``lanes``, ``layout``, ``registry`` and ``steps``.
        """
        cells = self.cells()
        args = {"notes": self.recipe["args_notes"], "work_dir": self.recipe["work_dir"], "adapter": self.recipe["adapter"],
                **{k: copy.deepcopy(self.base[k]) for k in _INHERITED},
                "features": copy.deepcopy(self.recipe["features"]), "arms": list(cells),
                "cell": {"notes": "Generated by crypto_trading.zoo_lanes from zoo-lanes-recipe.json: each lane's full fit and score params. Edit the recipe, never this block.", **cells}}
        return {"notes": self.recipe["notes"], "args": args,
                "lanes": {"notes": "One chain per cell: `$lane` binds the cell name and `cell` (keyed by lane) is narrowed to the lane's own entry.", "key": "arms", "keyed": ["cell"]},
                "layout": {"fit": {"dir": "{W}/fit", "runs": "{W}/runs/{A}"}},
                "registry": {"fit": {"notes": "One walk-forward per cell; its summary lands under the layout's `runs` directory.",
                                     "template": self.recipe["template"], "extends": None,
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


def main(argv=None):
    """Write the W1 manifest (and optionally the W2 skeleton) from a recipe.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments; ``recipe`` is the recipe file, ``--out`` the W1 manifest, ``--w2-out`` the W2 one.

    Returns
    -------
    int
        0 on success.
    """
    parser = argparse.ArgumentParser(description="Generate the zoo's workflow-batch manifest from its recipe.")
    parser.add_argument("recipe")
    parser.add_argument("--out", required=True)
    parser.add_argument("--w2-out")
    args = parser.parse_args(argv)
    here = os.path.dirname(os.path.abspath(args.recipe))
    with open(args.recipe, encoding="utf-8") as handle:
        recipe = json.load(handle)
    with open(os.path.join(here, recipe["base"]), encoding="utf-8") as handle:
        lanes = ZooLanes(recipe, json.load(handle))
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(lanes.manifest(), indent=2) + "\n")
    if args.w2_out:
        with open(args.w2_out, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(lanes.w2_manifest(recipe["w2"]["finalists"], recipe["w2"]["bounds"], recipe["w2"]["template"]),
                                    indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
