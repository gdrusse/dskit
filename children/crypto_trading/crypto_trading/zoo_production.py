"""The production pipeline: the picked model with frozen hyperparameters, retrained on a cadence and scored forward.

Takes the zoo's pick (``zoo-pick.json``) and the HPO's freeze (``hpo-frozen.json``) and runs the model as production
would: block 1 is the initial training on the whole 1-minute history up to the first forward block (everything before
``forward_start`` minus the embargo), then the model is refit every ``retrain_days`` on an expanding window with the
SAME frozen hyperparameters (no search), and each forward block is scored out of sample with the standard metrics next
to both baselines of the scope. A block is one lane (a one-fold ``walkforward``, so it uses exactly the zoo's split,
embargo, fit and score machinery); lanes are independent, so they run in parallel.

Window rule. ``forward_start`` and ``evaluate_through`` (exclusive) default to W2's start and end, from the recipe's
``production`` section, pinned by a test to ``w2.bounds``. W4 and W5 are reserved (owner-only decision): an
``evaluate_through`` past W2's end is refused unless the recipe's ``allow_past_w2`` is true, and that flag opens W4
only: an ``evaluate_through`` past W5's start (the held-out cut, read from the one file that owns it, named by
``production.w5``) is refused ALWAYS. A ``forward_start`` before W2's start is refused always (those days are W1's
selection folds and the embargo).

The panel. The frozen model was fitted on the zoo's panel, which stops at W2's end; a window past it (W4) needs a panel
that reaches it. ``production-manifest --panel-source/--panel-relpath`` repoint every lane (the model, both baselines and
every score) at such a build, and nothing else: the second declared override beside ``row_stride`` (``PRODUCTION_OVERRIDES``),
so every other param must still hash to the freeze, and the realized panel of each retrain must equal the declared one.

Models are saved at every retrain by the existing seam, not a new one: ``PanelTrain`` already writes ``model.pt`` (the
weights), ``model.json`` (module parameters, window/stride/assets/columns, the scaler and band hash in
``adapter_state``, a content hash) and ``training_curve.json`` under the fold run's ``artifacts/<fit node>``; a
baseline writes its fitted-state file there. :class:`ModelIndex` copies each block's artifact directory to a stable path
``<out>/models/<lane>/block-<k>-<cutoff>/`` (the run directory's name carries a hash) and lists them in ``models.json``
(block, cutoff, train end, hyperparameter hash, path, sha256). ``PanelTrain(mode="load", artifact=<path>/model.pt)``
restores any of them.

Import cost: stdlib + dskit core (no torch).
"""

import argparse
import copy
import hashlib
import os
import shutil
from datetime import date, datetime, timedelta, timezone

from .zoo_pick import (
    ZooResults, batch_root, check_frozen, file_sha256, hparams_sha256, load_frozen, load_json, load_recipe, resolve_recipe, template_path,
    write_json,
)

__all__ = ["ForwardWindow", "ModelIndex", "PRODUCTION_OVERRIDES", "ProductionPlan", "index_main", "manifest_main"]

_MS = 1000
#: The one fit param the production pipeline overrides (its value is the recipe's ``production.row_stride``).
_STRIDE = "row_stride"
#: The panel keys the production pipeline may repoint (an extended build of the same panel, registered as its own source).
_PANEL_KEYS = ("source", "relpath")
#: What the production pipeline overrides on the frozen model, as :func:`~crypto_trading.zoo_pick.hparams_sha256` exclusions:
#: every other fit param must hash to the freeze.
PRODUCTION_OVERRIDES = (_STRIDE, *(f"panel.{key}" for key in _PANEL_KEYS))
_NOTES = "notes"
_TRUE = ("true", "1", "yes")
_FALSE = ("false", "0", "no")


def _date_of(ms):
    """Return the UTC date of an epoch-millisecond instant."""
    return datetime.fromtimestamp(ms / _MS, tz=timezone.utc).date()


def w5_start_ms(production, recipe_dir):
    """Return W5's start instant: the held-out cut, read from the file and path ``production.w5`` names.

    Parameters
    ----------
    production : dict
        ``recipe["production"]``; ``w5`` is ``{"file", "path"}`` (a file beside the recipe, a dotted path in it).
    recipe_dir : str
        The recipe's directory.

    Returns
    -------
    int
        The cut, epoch milliseconds.

    Raises
    ------
    ValueError
        The path is not in the file or does not hold an integer.
    """
    spec = production["w5"]
    node = load_json(os.path.join(recipe_dir, spec["file"]))
    for part in spec["path"].split("."):
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"production.w5: {spec['path']!r} is not in {spec['file']}")
        node = node[part]
    if isinstance(node, bool) or not isinstance(node, int):
        raise ValueError(f"production.w5: {spec['path']!r} in {spec['file']} is not an integer instant, got {node!r}")
    return node


def _flag(text):
    """Parse a ``true``/``false`` command-line value."""
    if text.lower() in _TRUE:
        return True
    if text.lower() in _FALSE:
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {text!r}")


class ForwardWindow:
    """The forward period and its retrain blocks, refused when it strays past W2 without the recipe flag.

    Parameters
    ----------
    production : dict
        ``recipe["production"]``: ``forward_start``, ``evaluate_through``, ``retrain_days``, ``allow_past_w2``.
    w2 : dict
        ``recipe["w2"]["bounds"]``: the W2 instants that bound the default window.
    embargo_days : int
        The zoo's embargo (``folds.embargo_days``).
    w5 : int
        W5's start instant (:func:`w5_start_ms`): no ``evaluate_through`` may pass it, whatever ``allow_past_w2`` says.
    overrides : dict, optional
        Replacements for any ``production`` key (the command line).

    Raises
    ------
    ValueError
        ``evaluate_through`` past W5's start; past W2's end without ``allow_past_w2``; ``forward_start`` before W2's
        start; an empty window; a ``retrain_days`` that is not a positive integer.

    Examples
    --------
    Blocks of the default window::

        window = ForwardWindow(recipe["production"], recipe["w2"]["bounds"], 1, w5_start_ms(recipe["production"], configs_dir))
        [b["first"] for b in window.blocks()]
        # -> ["2026-05-02", "2026-07-01"]
    """

    def __init__(self, production, w2, embargo_days, w5, overrides=None):
        cfg = {**{k: v for k, v in production.items() if k != _NOTES}, **{k: v for k, v in (overrides or {}).items() if v is not None}}
        self.start = date.fromisoformat(cfg["forward_start"])
        self.through = date.fromisoformat(cfg["evaluate_through"])
        self.retrain_days, self.allow = cfg["retrain_days"], cfg["allow_past_w2"]
        self.embargo_days = embargo_days
        w2_start, w2_end, w5_start = _date_of(w2["val_start_ms"]), _date_of(w2["val_end_ms"] + 1), _date_of(w5)
        if isinstance(self.retrain_days, bool) or not isinstance(self.retrain_days, int) or self.retrain_days < 1:
            raise ValueError(f"retrain_days must be an integer >= 1, got {self.retrain_days!r}")
        if not isinstance(self.allow, bool):
            raise ValueError(f"allow_past_w2 must be true or false, got {self.allow!r}")
        if self.start < w2_start:
            raise ValueError(f"forward_start {self.start} is before W2's start {w2_start}: those days are W1's folds and the embargo")
        if self.through > w5_start:
            raise ValueError(f"evaluate_through {self.through} is past W5's start {w5_start}: the holdout is read once, by the owner, "
                             "and no setting opens it")
        if self.through > w2_end and not self.allow:
            raise ValueError(f"evaluate_through {self.through} is past W2's end {w2_end}: W4 and W5 are reserved (owner-only "
                             "decision); allow_past_w2 opens W4 only")
        if self.through <= self.start:
            raise ValueError(f"evaluate_through {self.through} must be after forward_start {self.start}")

    def blocks(self):
        """Return the retrain blocks, in order.

        Returns
        -------
        list of dict
            ``k`` (from 1), ``name`` (``b01``...), ``first`` (the cutoff, the block's first scored day), ``val_days``
            (clipped so the last block ends at ``evaluate_through``), ``val_end`` (exclusive), ``train_end``
            (exclusive: the cutoff less the embargo) and ``embargo_days``.
        """
        out, first, k = [], self.start, 1
        while first < self.through:
            days = min(self.retrain_days, (self.through - first).days)
            out.append({"k": k, "name": f"b{k:02d}", "first": first.isoformat(), "val_days": days,
                        "val_end": (first + timedelta(days=days)).isoformat(),
                        "train_end": (first - timedelta(days=self.embargo_days)).isoformat(), "embargo_days": self.embargo_days})
            first, k = first + timedelta(days=self.retrain_days), k + 1
        return out


class ProductionPlan:
    """The production lanes: the frozen model's lanes and the scope's baselines, one set per block.

    Parameters
    ----------
    recipe : dict
        The recipe (``production`` section).
    lanes : ZooLanes
        The zoo generator.
    pick : dict
        ``zoo-pick.json`` (its ``winner``).
    frozen : dict
        ``hpo-frozen.json``.
    window : ForwardWindow
        The forward period.

    Raises
    ------
    ValueError
        A pick with no winner; a freeze for another cell or another recipe than the pick's.

    Examples
    --------
    The production manifest of a pick and a freeze::

        plan = ProductionPlan(recipe, lanes, pick, frozen, window)
        plan.manifest("../production-template.json")["args"]["arms"][0]
        # -> "b01-gru-crps-pooled"
    """

    def __init__(self, recipe, lanes, pick, frozen, window):
        if not pick.get("winner"):
            raise ValueError("the zoo pick has no winner: there is nothing to run forward")
        if frozen["pick_winner"] != {k: pick["winner"][k] for k in frozen["pick_winner"]}:
            raise ValueError(f"hpo-frozen.json is for {frozen['pick_winner']['cell']}, the pick is {pick['winner']['cell']}")
        if frozen["recipe"] != pick["recipe"]:
            raise ValueError("hpo-frozen.json and zoo-pick.json were written from different recipes")
        check_frozen(frozen)
        self.recipe, self.lanes, self.pick, self.frozen, self.window = recipe, lanes, pick, frozen, window
        self.production = recipe["production"]
        self.stride = self.production[_STRIDE]
        if isinstance(self.stride, bool) or not isinstance(self.stride, int) or self.stride < 1:
            raise ValueError(f"production.{_STRIDE} must be an integer >= 1, got {self.stride!r}")
        self.panel = {key: lanes.base["panel"][key] for key in _PANEL_KEYS}
        self.model_lanes = list(pick["winner"]["lanes"])
        self.scope = pick["winner"]["scope"]

    def zoo_lanes(self):
        """Return ``(model lanes, baseline lanes)`` as zoo lane names: the frozen cell's, and the scope's baselines."""
        suffixes = self.lanes.scopes()[self.scope]
        _, baselines = self.lanes.cell_names()
        return self.model_lanes, [f"{b}-{s}" for b in baselines for s in suffixes]

    def cells(self):
        """Return ``{zoo lane: cell}``: the frozen model's params (per lane) and the baselines as the zoo ran them."""
        zoo = self.lanes.cells()
        model, baselines = self.zoo_lanes()
        cells = {lane: zoo[lane] for lane in baselines}
        for lane in model:
            fit = copy.deepcopy(self.frozen["params"][lane])
            fit[_STRIDE] = self.stride
            fit["panel"] = {**fit["panel"], **self.panel}
            if hparams_sha256(fit, PRODUCTION_OVERRIDES) != hparams_sha256(self.frozen["params"][lane], PRODUCTION_OVERRIDES):
                raise ValueError(f"{lane}: the production fit differs from the frozen one beyond {PRODUCTION_OVERRIDES}")
            cells[lane] = {**zoo[lane], "fit": fit}
        return cells

    def manifest(self, template):
        """Return the workflow-batch manifest: one lane per block and zoo lane, each a one-fold walk-forward.

        Parameters
        ----------
        template : str
            The production template's path as the manifest's directory resolves it.
        """
        cells, blocks = self.cells(), self.window.blocks()
        lanes = {f"{b['name']}-{lane}": (b, cell) for b in blocks for lane, cell in cells.items()}
        manifest = self.lanes.manifest({name: cell for name, (_, cell) in lanes.items()}, self.production["work_dir"], None, template)
        notes = self.production["manifest_notes"]
        manifest["notes"], manifest["args"]["notes"] = notes["manifest"], notes["args"]
        manifest["args"].pop("folds")
        manifest["steps"]["fit"]["in"].pop("folds")
        manifest["steps"]["fit"]["in"]["block"] = "$args.block.$lane"
        manifest["lanes"]["keyed"] = ["cell", "block"]
        manifest["args"]["block"] = {"notes": "Each lane's retrain block: the cutoff its one-fold walk-forward starts at and how many days it scores.",
                                     **{name: {k: b[k] for k in ("k", "first", "val_days", "embargo_days")} for name, (b, _) in lanes.items()}}
        manifest["args"]["production"] = {
            "pick": self.pick["winner"]["cell"], "model_lanes": self.model_lanes, "blocks": blocks,
            "forward_start": self.window.start.isoformat(), "evaluate_through": self.window.through.isoformat(),
            "retrain_days": self.window.retrain_days, "allow_past_w2": self.window.allow,
            _STRIDE: self.stride, "panel": dict(self.panel),
            "hparams_sha256": {lane: hparams_sha256(self.frozen["params"][lane], PRODUCTION_OVERRIDES) for lane in self.model_lanes},
            "frozen_winner": self.frozen["winner"]}
        return manifest


class ModelIndex:
    """Persist each retrain's model under a stable path and index them; score the forward blocks.

    Parameters
    ----------
    recipe : dict
        The recipe (``production.files``, ``lane_runs``, ``pick.metrics``).
    template : dict
        The production template (objective, score and fit node keys).
    manifest : dict
        The production lanes manifest (``args.production``, ``args.block``, ``args.cell``).
    root : str
        The workflow-batch root the lanes ran under.
    frozen : dict
        ``hpo-frozen.json``: every model lane's realized hyperparameters must hash to its ``hparams_sha256``.

    Examples
    --------
    Persist and index a finished batch::

        index = ModelIndex(recipe, template, manifest, "pipeline_runs/zoo-production/train/lanes", frozen)
        entries = index.persist("pipeline_runs/zoo-production")
    """

    def __init__(self, recipe, template, manifest, root, frozen):
        check_frozen(frozen)
        self.recipe, self.manifest, self.root, self.frozen = recipe, manifest, root, frozen
        self.files = recipe["production"]["files"]
        self.info = manifest["args"]["production"]
        self.stride = recipe["production"][_STRIDE]
        if self.info.get(_STRIDE) != self.stride:
            raise ValueError(f"the manifest was built with {_STRIDE} {self.info.get(_STRIDE)!r}, the recipe declares {self.stride!r}")
        self.panel = self.info.get("panel")
        self.blocks = {b["name"]: b for b in self.info["blocks"]}
        self.results = {name: ZooResults(root, recipe, template, f"{name}-") for name in self.blocks}
        self.fit_node = self.results[next(iter(self.blocks))].spec.model_node

    def _lanes(self):
        """Yield ``(block, zoo lane, cell)`` for every lane of the batch, blocks in order."""
        cells = self.manifest["args"]["cell"]
        for name, block in self.blocks.items():
            for key in self.manifest["args"]["arms"]:
                if key.startswith(f"{name}-"):
                    yield block, key[len(name) + 1:], cells[key]

    def persist(self, out_dir):
        """Copy every lane's artifact directory to its stable path and write the index.

        Parameters
        ----------
        out_dir : str
            The production work directory; ``models/`` and ``models.json`` go under it.

        Returns
        -------
        list of dict
            The index entries (also written to ``models.json``).

        Raises
        ------
        ValueError
            A lane that is not one fold at its block's cutoff; a run with no artifact, or a model lane without
            ``model.pt`` and ``model.json``; a run whose realized config (its ``config.json``) hashes differently from
            the manifest's cell or, for a model lane, from the manifest's recorded hash and ``hpo-frozen.json``'s;
            a previous block directory that cannot be cleared. A block directory already present is replaced.
        """
        entries = []
        for block, lane, cell in self._lanes():
            run = self._lane(block, lane).run_dirs[0]
            source = os.path.join(run, "artifacts", self.fit_node)
            self._check_saved(run, source, lane)
            relative = os.path.join(self.files["models_dir"], lane, f"block-{block['k']}-{block['first']}")
            target = os.path.join(out_dir, relative)
            self._clear(target)
            os.makedirs(target)
            digests = {}
            for name in sorted(os.listdir(source)):
                shutil.copy2(os.path.join(source, name), os.path.join(target, name))
                digests[name] = file_sha256(os.path.join(target, name))
            entries.append(self._entry(block, lane, cell, run, relative, digests, target))
        write_json(os.path.join(out_dir, self.files["index"]), {
            "pick": self.info["pick"], "frozen_winner": self.info["frozen_winner"], "blocks": self.info["blocks"],
            "models": entries})
        return entries

    @staticmethod
    def _clear(target):
        """Remove a previous block directory, refusing (not ignoring) a failure."""
        try:
            shutil.rmtree(target)
        except FileNotFoundError:
            pass
        except OSError as err:
            raise ValueError(f"{target}: cannot clear the previous block directory ({err})") from err

    def _lane(self, block, lane):
        """Return the block's :class:`LaneFolds`, refusing anything but one fold at the block's cutoff."""
        found = self.results[block["name"]].lane(lane)
        if list(found.cutoffs) != [block["first"]]:
            raise ValueError(f"{found.lane}: a production lane is one fold at the block's cutoff {block['first']}, "
                             f"got {list(found.cutoffs)}")
        return found

    def _check_saved(self, run, source, lane):
        """Refuse a retrain that left no artifact; a model lane must hold its weights and its sidecar."""
        present = set(os.listdir(source)) if os.path.isdir(source) else set()
        needed = {self.files["weights"], self.files["sidecar"]} if lane in self.info["model_lanes"] else set()
        if not present or needed - present:
            raise ValueError(f"{run}: artifacts/{self.fit_node} lacks {sorted(needed - present) or 'any file'}: the retrain saved no model")

    def _realized(self, run):
        """Return the fit params the run actually executed (its ``config.json``), not the manifest's copy."""
        return load_json(os.path.join(run, self.files["config"]))["pipeline"][self.fit_node]["params"]

    def _entry(self, block, lane, cell, run, relative, digests, target):
        """Build one index entry; the run's REALIZED config must match the manifest cell and the freeze, bar the declared stride."""
        realized = self._realized(run)
        if hparams_sha256(realized) != hparams_sha256(cell["fit"]):
            raise ValueError(f"{lane} block {block['k']}: the run's realized config differs from the manifest's cell")
        model = lane in self.info["model_lanes"]
        if model and realized.get(_STRIDE) != self.stride:
            raise ValueError(f"{lane} block {block['k']}: realized {_STRIDE} {realized.get(_STRIDE)!r} is not the declared "
                             f"production.{_STRIDE} {self.stride!r}")
        if model and self.panel is not None and {k: realized["panel"].get(k) for k in _PANEL_KEYS} != self.panel:
            raise ValueError(f"{lane} block {block['k']}: the retrain read panel {realized['panel']!r}, not the declared {self.panel!r}")
        hashed = hparams_sha256(realized, PRODUCTION_OVERRIDES if model else ())
        wanted = self.info["hparams_sha256"].get(lane)
        if wanted is not None and hashed != wanted:
            raise ValueError(f"{lane} block {block['k']}: the run's hyperparameters differ from the frozen ones")
        if model and hashed != hparams_sha256(self.frozen["params"][lane], PRODUCTION_OVERRIDES):
            raise ValueError(f"{lane} block {block['k']}: the run's hyperparameters differ from hpo-frozen.json's")
        sidecar = os.path.join(target, self.files["sidecar"])
        state = load_json(sidecar)["state_hash"] if os.path.isfile(sidecar) else None
        listing = "".join(f"{name}:{digest}\n" for name, digest in digests.items())
        return {"lane": lane, "block": block["k"], "cutoff": block["first"], "train_end": block["train_end"],
                "hparams_sha256": hashed, _STRIDE: realized.get(_STRIDE), "panel": {k: realized["panel"].get(k) for k in _PANEL_KEYS}, "path": relative, "files": digests, "state_hash": state,
                "sha256": hashlib.sha256(listing.encode("utf-8")).hexdigest()}

    def report(self):
        """Return the forward-period record: each block's model against its baselines, and the whole period.

        Returns
        -------
        dict
            ``blocks`` (per block: each cell's objective, and the model's paired difference to the best baseline) and
            ``overall`` (each cell's row-weighted objective over all blocks, and whether the model beats both baselines).
        """
        model_lanes = self.info["model_lanes"]
        suffixes = [lane.rsplit("-", 1)[1] for lane in model_lanes]
        model_name = model_lanes[0].rsplit("-", 1)[0]
        baselines = sorted({lane.rsplit("-", 1)[0] for _, lane, _ in self._lanes()} - {model_name})
        per_block, totals = [], {}
        for block, lane, _ in self._lanes():
            self._lane(block, lane)
        for name, block in self.blocks.items():
            res = self.results[name]
            cells = {model_name: res.cell(model_name, suffixes)}
            cells.update({b: res.cell(b, suffixes) for b in baselines})
            best = min((c for n, c in cells.items() if n != model_name), key=lambda c: c.mean)
            gap = [a - b for a, b in zip(cells[model_name].scores, best.scores)]
            per_block.append({"block": block["k"], "cutoff": block["first"], "val_days": block["val_days"],
                              "rows": cells[model_name].rows[0], "scores": {n: c.mean for n, c in cells.items()},
                              "metrics": {n: {m: c.metric(m)[0] for m in res.metric_names} for n, c in cells.items()},
                              "best_baseline": best.name, "model_minus_best_baseline": gap[0]})
            for n, c in cells.items():
                total = totals.setdefault(n, [0.0, 0])
                total[0] += c.mean * c.rows[0]
                total[1] += c.rows[0]
        overall = {n: s / r for n, (s, r) in totals.items()}
        return {"objective": self.results[next(iter(self.blocks))].spec.objective, "model": model_name, "blocks": per_block,
                "overall": overall, "rows": next(iter(totals.values()))[1],
                "model_beats_baselines": all(overall[model_name] < overall[b] for b in baselines)}

    def markdown(self, report):
        """Return the short ``production-report.md``."""
        model = report["model"]
        lines = [f"# Production forward run ({report['objective']}, lower is better)", "",
                 f"Model {model}: overall {report['overall'][model]:.5f}; beats both baselines: "
                 f"{'yes' if report['model_beats_baselines'] else 'NO'}.", "",
                 "| block | cutoff | days | " + " | ".join(report["overall"]) + " |",
                 "|---|---|---|" + "---|" * len(report["overall"])]
        for b in report["blocks"]:
            lines.append(f"| {b['block']} | {b['cutoff']} | {b['val_days']} | " + " | ".join(f"{b['scores'][n]:.5f}" for n in report["overall"]) + " |")
        lines.append("| all | | | " + " | ".join(f"{report['overall'][n]:.5f}" for n in report["overall"]) + " |")
        return "\n".join(lines) + "\n"


def manifest_main(argv=None):
    """Write the production lanes manifest (``production-manifest``).

    Parameters
    ----------
    argv : list of str, optional
        ``--pick``, ``--frozen``, ``--out``, optional ``--recipe``, ``--forward-start``, ``--evaluate-through``,
        ``--retrain-days`` and ``--allow-past-w2 true|false`` (each replacing the recipe's ``production`` value), and
        ``--panel-source`` with ``--panel-relpath`` (both or neither; empty means the base manifest's panel).

    Returns
    -------
    int
        0.
    """
    parser = argparse.ArgumentParser(prog="zoo_lanes production-manifest", description="Production lanes: frozen model + baselines, one lane per retrain block.")
    parser.add_argument("--pick", required=True)
    parser.add_argument("--frozen", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--recipe")
    parser.add_argument("--forward-start")
    parser.add_argument("--evaluate-through")
    parser.add_argument("--retrain-days", type=int)
    parser.add_argument("--allow-past-w2", type=_flag)
    parser.add_argument("--panel-source")
    parser.add_argument("--panel-relpath")
    args = parser.parse_args(argv)
    pick, frozen = load_json(args.pick), load_frozen(args.frozen, args.pick)
    recipe, lanes, _, here = _recipe_for(pick, frozen, args.recipe)
    if args.panel_source or args.panel_relpath:
        lanes.use_panel(args.panel_source, args.panel_relpath)
    window = ForwardWindow(recipe["production"], recipe["w2"]["bounds"], lanes.base["folds"]["embargo_days"],
                           w5_start_ms(recipe["production"], here),
                           {"forward_start": args.forward_start, "evaluate_through": args.evaluate_through,
                            "retrain_days": args.retrain_days, "allow_past_w2": args.allow_past_w2})
    template = template_path(here, recipe["production"]["template"], args.out)
    write_json(args.out, ProductionPlan(recipe, lanes, pick, frozen, window).manifest(template))
    return 0


def _recipe_for(pick, frozen, override):
    """Load the recipe both records were written from, refusing a changed recipe."""
    path = resolve_recipe(pick, override)
    if frozen["recipe"] != pick["recipe"]:
        raise ValueError("hpo-frozen.json and zoo-pick.json were written from different recipes")
    return load_recipe(path)


def index_main(argv=None):
    """Persist every retrain's model, index them and write the forward report (``production-index``).

    Parameters
    ----------
    argv : list of str, optional
        ``--lanes`` (the production manifest), ``--out-dir``, one of ``--root`` / ``--batch``, ``--pick``, ``--frozen``,
        optional ``--recipe``.

    Returns
    -------
    int
        0.
    """
    parser = argparse.ArgumentParser(prog="zoo_lanes production-index", description="Save every retrain's model under a stable path; index and report.")
    parser.add_argument("--lanes", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--pick", required=True)
    parser.add_argument("--frozen", required=True, help="hpo-frozen.json: the realized hyperparameters are checked against it")
    parser.add_argument("--root")
    parser.add_argument("--batch")
    parser.add_argument("--recipe")
    args = parser.parse_args(argv)
    pick = load_json(args.pick)
    recipe, lanes, _, here = load_recipe(resolve_recipe(pick, args.recipe))
    template = load_json(os.path.join(here, recipe["production"]["template"]))
    manifest = load_json(args.lanes)
    info = manifest["args"]["production"]
    ForwardWindow(recipe["production"], recipe["w2"]["bounds"], lanes.base["folds"]["embargo_days"], w5_start_ms(recipe["production"], here),
                  {k: info[k] for k in ("forward_start", "evaluate_through", "retrain_days", "allow_past_w2")})
    index = ModelIndex(recipe, template, manifest, batch_root(args.root, args.batch), load_frozen(args.frozen, args.pick))
    entries = index.persist(args.out_dir)
    report = index.report()
    files = recipe["production"]["files"]
    write_json(os.path.join(args.out_dir, files["report"]), report)
    with open(os.path.join(args.out_dir, files["report_md"]), "w", encoding="utf-8") as handle:
        handle.write(index.markdown(report))
    print(f"production-index: {len(entries)} models saved under {args.out_dir}; model {report['model']} overall "
          f"{report['overall'][report['model']]:.6g}", flush=True)
    return 0
