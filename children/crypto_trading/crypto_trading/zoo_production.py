"""The production pipeline: the picked model with frozen hyperparameters, retrained on a cadence and scored forward.

Takes the zoo's pick (``zoo-pick.json``) and the HPO's freeze (``hpo-frozen.json``) and runs the model as production
would: block 1 is the initial training on the whole 1-minute history up to the first forward block (everything before
``forward_start`` minus the embargo), then the model is refit every ``retrain_days`` on an expanding window with the
SAME frozen hyperparameters (no search), and each forward block is scored out of sample with the standard metrics next
to both baselines of the scope. A block is one lane (a one-fold ``walkforward``, so it uses exactly the zoo's split,
embargo, fit and score machinery); lanes are independent, so they run in parallel.

Window rule. ``forward_start`` and ``evaluate_through`` (exclusive) default to W2's start and end, from the recipe's
``production`` section, pinned by a test to ``w2.bounds``. W4 and W5 are reserved (owner-only decision): an
``evaluate_through`` past W2's end is refused unless the recipe's ``allow_past_w2`` is true; a ``forward_start`` before
W2's start is refused always (those days are W1's selection folds and the embargo).

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
import json
import os
import shutil
from datetime import date, datetime, timedelta, timezone

from .zoo_pick import (
    ZooResults, batch_root, file_sha256, hparams_sha256, load_json, load_recipe, resolve_recipe, template_path, write_json,
)

__all__ = ["ForwardWindow", "ModelIndex", "ProductionPlan", "index_main", "manifest_main"]

_MS = 1000
_NOTES = "notes"
_TRUE = ("true", "1", "yes")
_FALSE = ("false", "0", "no")


def _date_of(ms):
    """Return the UTC date of an epoch-millisecond instant."""
    return datetime.fromtimestamp(ms / _MS, tz=timezone.utc).date()


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
    overrides : dict, optional
        Replacements for any ``production`` key (the command line).

    Raises
    ------
    ValueError
        ``evaluate_through`` past W2's end without ``allow_past_w2``; ``forward_start`` before W2's start; an empty
        window; a ``retrain_days`` that is not a positive integer.

    Examples
    --------
    Blocks of the default window::

        window = ForwardWindow(recipe["production"], recipe["w2"]["bounds"], 1)
        [b["first"] for b in window.blocks()]
        # -> ["2026-05-02", "2026-07-01"]
    """

    def __init__(self, production, w2, embargo_days, overrides=None):
        cfg = {**{k: v for k, v in production.items() if k != _NOTES}, **{k: v for k, v in (overrides or {}).items() if v is not None}}
        self.start = date.fromisoformat(cfg["forward_start"])
        self.through = date.fromisoformat(cfg["evaluate_through"])
        self.retrain_days, self.allow = cfg["retrain_days"], cfg["allow_past_w2"]
        self.embargo_days = embargo_days
        w2_start, w2_end = _date_of(w2["val_start_ms"]), _date_of(w2["val_end_ms"] + 1)
        if isinstance(self.retrain_days, bool) or not isinstance(self.retrain_days, int) or self.retrain_days < 1:
            raise ValueError(f"retrain_days must be an integer >= 1, got {self.retrain_days!r}")
        if not isinstance(self.allow, bool):
            raise ValueError(f"allow_past_w2 must be true or false, got {self.allow!r}")
        if self.start < w2_start:
            raise ValueError(f"forward_start {self.start} is before W2's start {w2_start}: those days are W1's folds and the embargo")
        if self.through > w2_end and not self.allow:
            raise ValueError(f"evaluate_through {self.through} is past W2's end {w2_end}: W4 and W5 are reserved (owner-only "
                             "decision); set allow_past_w2 to true in the recipe to override")
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
        self.recipe, self.lanes, self.pick, self.frozen, self.window = recipe, lanes, pick, frozen, window
        self.production = recipe["production"]
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
            cells[lane] = {**zoo[lane], "fit": copy.deepcopy(self.frozen["params"][lane])}
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
            "hparams_sha256": {lane: hparams_sha256(self.frozen["params"][lane]) for lane in self.model_lanes},
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

    Examples
    --------
    Persist and index a finished batch::

        index = ModelIndex(recipe, template, manifest, "pipeline_runs/zoo-production/train/lanes")
        entries = index.persist("pipeline_runs/zoo-production")
    """

    def __init__(self, recipe, template, manifest, root):
        self.recipe, self.manifest, self.root = recipe, manifest, root
        self.files = recipe["production"]["files"]
        self.info = manifest["args"]["production"]
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
            A lane whose run holds no artifact, or whose model lane's hyperparameter hash differs from the freeze.
        """
        entries = []
        for block, lane, cell in self._lanes():
            run = self.results[block["name"]].lane(lane).run_dirs[0]
            source = os.path.join(run, "artifacts", self.fit_node)
            if not os.path.isdir(source) or not os.listdir(source):
                raise ValueError(f"{run}: no artifact under artifacts/{self.fit_node}: the retrain saved no model")
            relative = os.path.join(self.files["models_dir"], lane, f"block-{block['k']}-{block['first']}")
            target = os.path.join(out_dir, relative)
            os.makedirs(target, exist_ok=True)
            digests = {}
            for name in sorted(os.listdir(source)):
                shutil.copy2(os.path.join(source, name), os.path.join(target, name))
                digests[name] = file_sha256(os.path.join(target, name))
            entries.append(self._entry(block, lane, cell, relative, digests, target))
        write_json(os.path.join(out_dir, self.files["index"]), {
            "pick": self.info["pick"], "frozen_winner": self.info["frozen_winner"], "blocks": self.info["blocks"],
            "models": entries})
        return entries

    def _entry(self, block, lane, cell, relative, digests, target):
        """Build one index entry; checks a model lane's hyperparameter hash against the freeze."""
        hashed = hparams_sha256(cell["fit"])
        wanted = self.info["hparams_sha256"].get(lane)
        if wanted is not None and hashed != wanted:
            raise ValueError(f"{lane} block {block['k']}: hyperparameters differ from the frozen ones")
        sidecar = os.path.join(target, self.files["sidecar"])
        state = load_json(sidecar)["state_hash"] if os.path.isfile(sidecar) else None
        listing = "".join(f"{name}:{digest}\n" for name, digest in digests.items())
        return {"lane": lane, "block": block["k"], "cutoff": block["first"], "train_end": block["train_end"],
                "hparams_sha256": hashed, "path": relative, "files": digests, "state_hash": state,
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
        ``--retrain-days`` and ``--allow-past-w2 true|false`` (each replacing the recipe's ``production`` value).

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
    args = parser.parse_args(argv)
    pick, frozen = load_json(args.pick), load_json(args.frozen)
    recipe, lanes, _, here = _recipe_for(pick, frozen, args.recipe)
    window = ForwardWindow(recipe["production"], recipe["w2"]["bounds"], lanes.base["folds"]["embargo_days"],
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
        ``--lanes`` (the production manifest), ``--out-dir``, one of ``--root`` / ``--batch``, ``--pick``,
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
    parser.add_argument("--root")
    parser.add_argument("--batch")
    parser.add_argument("--recipe")
    args = parser.parse_args(argv)
    pick = load_json(args.pick)
    recipe, _, _, here = load_recipe(resolve_recipe(pick, args.recipe))
    with open(os.path.join(here, recipe["production"]["template"]), encoding="utf-8") as handle:
        template = json.load(handle)
    index = ModelIndex(recipe, template, load_json(args.lanes), batch_root(args.root, args.batch))
    entries = index.persist(args.out_dir)
    report = index.report()
    files = recipe["production"]["files"]
    write_json(os.path.join(args.out_dir, files["report"]), report)
    with open(os.path.join(args.out_dir, files["report_md"]), "w", encoding="utf-8") as handle:
        handle.write(index.markdown(report))
    print(f"production-index: {len(entries)} models saved under {args.out_dir}; model {report['model']} overall "
          f"{report['overall'][report['model']]:.6g}", flush=True)
    return 0
