"""HPO on the warmup of W1, freeze, then tuned against untuned on the later W1 folds (plan 2026-10-09-data-windows.md).

Three steps, each a CLI of ``zoo_lanes`` and each a function of the recipe's ``hpo`` section and the zoo pick:

1. ``hpo-manifest``: one lane per candidate of the PICKED cell (an unpooled pick: both asset lanes), walk-forward over
   the first ``warmup_folds`` folds of the zoo's own schedule. The candidates are the ``default`` (the cell exactly as
   the zoo ran it) and the options child's predeclared Gaussian bundles, mapped onto the PanelTrain fit params by the
   recipe's ``hpo.map``; a bundle knob with no counterpart is listed in ``hpo.unmapped`` (and a knob in neither
   refuses), and a mapping restricted to a head kind is skipped, and recorded as skipped, on the other kind.
2. ``hpo-pick``: the lowest warmup mean objective wins (the ``default`` wins ties), and its FULL fit params are written
   to ``hpo-frozen.json`` with their hash.
3. ``frozen-manifest`` / ``hpo-evaluate``: the default cell and the frozen winner over the REMAINING folds (the same
   schedule shifted by ``warmup_folds``), so tuned and untuned meet out of sample on identical folds, and a paired
   difference over those folds. No search node anywhere; nothing here reads W2.

Import cost: stdlib + dskit core (no torch).
"""

import argparse
import copy
import os

from .zoo_lanes import FoldSchedule
from .zoo_pick import (
    ZooResults, batch_root, check_frozen, file_sha256, hparams_sha256, load_frozen, load_json, load_recipe, paired, resolve_recipe, template_path, write_json,
)

__all__ = ["Candidates", "HpoPlan", "evaluate_main", "frozen_manifest_main", "hpo_manifest_main", "hpo_pick_main"]

_NOTES = "notes"
_WINNER_KEYS = ("cell", "model", "loss", "scope", "lanes")


class Candidates:
    """The HPO candidates and how a bundle lands on a cell's fit params.

    Parameters
    ----------
    hpo : dict
        ``recipe["hpo"]``: ``default_candidate``, ``candidates``, ``map``, ``unmapped``.

    Raises
    ------
    ValueError
        A bundle param that is neither mapped nor listed as unmapped, a candidate named like the default, or a
        mapping with no ``to``.

    Examples
    --------
    Land bundle ``a`` on a cell's fit params::

        fit, applied, skipped = Candidates(recipe["hpo"]).apply("a", cell["fit"])
        # -> applied["trunk.hidden"] == [16]
    """

    def __init__(self, hpo):
        self.default = hpo["default_candidate"]
        self.bundles = hpo["candidates"]
        self.map = {k: v for k, v in hpo["map"].items() if k != _NOTES}
        self.unmapped = {k: v for k, v in hpo["unmapped"].items() if k != _NOTES}
        if self.default in self.bundles:
            raise ValueError(f"hpo.candidates has a bundle named {self.default!r}, the default candidate's name")
        for name, spec in self.map.items():
            if not isinstance(spec.get("to"), str):
                raise ValueError(f"hpo.map.{name} needs a `to` path")
        for name, bundle in self.bundles.items():
            loose = sorted(set(bundle["params"]) - set(self.map) - set(self.unmapped))
            if loose:
                raise ValueError(f"hpo.candidates.{name}: params {loose} are neither in hpo.map nor in hpo.unmapped")

    def names(self):
        """Return the candidate names: the default first, then the bundles in recipe order."""
        return [self.default, *self.bundles]

    def apply(self, name, fit):
        """Return ``(fit params, applied, skipped)`` of candidate ``name`` on a copy of ``fit``.

        Parameters
        ----------
        name : str
            A candidate name.
        fit : dict
            The cell's PanelTrain params (not modified).

        Returns
        -------
        tuple
            The new params; ``{path: value}`` actually set; ``{param: reason}`` of the bundle knobs not set
            (unmapped, or mapped to a head kind this cell does not have).

        Raises
        ------
        ValueError
            A mapped path whose parent does not exist in ``fit``.
        """
        out = copy.deepcopy(fit)
        if name == self.default:
            return out, {}, {}
        applied, skipped = {}, {}
        for param, value in self.bundles[name]["params"].items():
            if param in self.unmapped:
                skipped[param] = f"unmapped: {self.unmapped[param]}"
                continue
            spec = self.map[param]
            head = spec.get("head")
            if head and out["head"]["kind"] != head:
                skipped[param] = f"mapped to {spec['to']} for a {head} head; this cell's head is {out['head']['kind']}"
                continue
            self._set(out, spec["to"], copy.deepcopy(value))
            applied[spec["to"]] = value
        return out, applied, skipped

    @staticmethod
    def _set(fit, path, value):
        """Set the dotted ``path`` in ``fit``; its parents must exist."""
        *parents, leaf = path.split(".")
        node = fit
        for part in parents:
            if not isinstance(node.get(part), dict):
                raise ValueError(f"hpo.map target {path!r}: {part!r} is not a dict in the cell's fit params")
            node = node[part]
        node[leaf] = value


class HpoPlan:
    """The HPO of one picked cell: its lanes, candidate lanes and the two fold windows.

    Parameters
    ----------
    recipe : dict
        The recipe (``hpo`` section).
    lanes : ZooLanes
        The zoo generator the cells come from.
    pick : dict
        ``zoo-pick.json``; its ``winner`` is the cell tuned.

    Raises
    ------
    ValueError
        A pick with no winner, or a ``warmup_folds`` that leaves no later folds.

    Examples
    --------
    The search manifest of a pick::

        plan = HpoPlan(recipe, lanes, pick)
        manifest = plan.search_manifest("../zoo-template.json")
        # -> manifest["args"]["arms"][0] == "default-gru-crps-pooled"
    """

    def __init__(self, recipe, lanes, pick):
        if not pick.get("winner"):
            raise ValueError("the zoo pick has no winner: there is nothing to tune")
        self.recipe, self.lanes, self.pick, self.hpo = recipe, lanes, pick, recipe["hpo"]
        self.winner = pick["winner"]
        self.candidates = Candidates(self.hpo)
        self.schedule = FoldSchedule(lanes.base["folds"])
        self.warmup = self.hpo["warmup_folds"]
        self.search_schedule = self.schedule.window(0, self.warmup)
        self.evaluation_schedule = self.schedule.window(self.warmup, self.schedule.folds["count"] - self.warmup)
        self.cell_name = f"{self.winner['model']}-{self.winner['loss']}"
        self.suffixes = lanes.scopes()[self.winner["scope"]]
        self.lane_names = [f"{self.cell_name}-{s}" for s in self.suffixes]
        if self.lane_names != self.winner["lanes"]:
            raise ValueError(f"the pick's lanes {self.winner['lanes']} are not the recipe's {self.lane_names}: "
                             "the recipe changed since the pick")

    def lane(self, candidate, lane):
        """Return the name of ``candidate``'s copy of zoo lane ``lane``."""
        return f"{candidate}-{lane}"

    def cells(self, names, params=None):
        """Return ``({lane: cell}, {candidate: {"applied", "skipped"}})`` for candidates ``names``.

        Parameters
        ----------
        names : list of str
            Candidate names.
        params : dict, optional
            ``{candidate: {zoo lane: fit params}}`` replacing the bundle mapping (the frozen winner).
        """
        zoo = self.lanes.cells()
        cells, record = {}, {}
        for name in names:
            record[name] = {"applied": {}, "skipped": {}}
            for lane in self.lane_names:
                cell = copy.deepcopy(zoo[lane])
                if params and name in params:
                    cell["fit"] = copy.deepcopy(params[name][lane])
                else:
                    cell["fit"], applied, skipped = self.candidates.apply(name, cell["fit"])
                    record[name]["applied"].update(applied)
                    record[name]["skipped"].update(skipped)
                cells[self.lane(name, lane)] = cell
        return cells, record

    def _manifest(self, cells, schedule, work_dir, template, notes):
        """Return the zoo-shaped lane manifest of ``cells`` over ``schedule``."""
        manifest = self.lanes.manifest(cells, work_dir, schedule.record(notes["folds"]), template)
        manifest["notes"], manifest["args"]["notes"] = notes["manifest"], notes["args"]
        return manifest

    def search_manifest(self, template):
        """Return the workflow-batch manifest of the search: every candidate's lanes over the warmup folds.

        Parameters
        ----------
        template : str
            The zoo template's path as the manifest's directory resolves it.
        """
        cells, record = self.cells(self.candidates.names())
        manifest = self._manifest(cells, self.search_schedule, self.hpo["work_dir"], template, self.hpo["search_notes"])
        manifest["args"]["hpo"] = {"pick": self.winner["cell"], "warmup_folds": self.warmup, "candidates": record}
        return manifest

    def freeze(self, results):
        """Return the ``hpo-frozen.json`` record from the finished search.

        Parameters
        ----------
        results : ZooResults
            The search batch's results, one :class:`ZooResults` per candidate: a dict ``{candidate: ZooResults}``.

        Returns
        -------
        dict
            ``candidates`` (warmup mean, per-fold scores, applied and skipped knobs), ``winner`` (lowest mean, the
            default on a tie), ``params`` (its full fit params per zoo lane) and the default's, for the drift check.

        Raises
        ------
        ValueError
            A candidate scored on other folds than the warmup schedule.
        """
        cells, record = self.cells(self.candidates.names())
        scored, expected = {}, self.search_schedule.cutoffs()
        for name in self.candidates.names():
            cell = results[name].cell(self.cell_name, self.suffixes)
            if list(cell.cutoffs) != expected:
                raise ValueError(f"candidate {name}: folds {list(cell.cutoffs)} are not the warmup schedule {expected}")
            scored[name] = {"mean": cell.mean, "per_fold": list(cell.scores), "lanes": [self.lane(name, ln) for ln in self.lane_names],
                            **record[name]}
        order = {name: i for i, name in enumerate(self.candidates.names())}
        winner = min(scored, key=lambda n: (scored[n]["mean"], order[n]))
        params = {n: {lane: cells[self.lane(n, lane)]["fit"] for lane in self.lane_names} for n in (self.candidates.default, winner)}
        return {"pick": self.winner["cell"], "pick_winner": {k: self.winner[k] for k in _WINNER_KEYS},
                "warmup": {"cutoffs": expected, "objective": results[winner].spec.objective},
                "tie_rule": f"lowest warmup mean; {self.candidates.default!r} wins ties, then recipe order",
                "candidates": scored, "winner": winner, "params": params[winner], "default_params": params[self.candidates.default],
                "hparams_sha256": {lane: hparams_sha256(fit) for lane, fit in params[winner].items()}}

    def evaluation_lanes(self, frozen):
        """Return the candidate names the later folds run: the default, and the frozen winner unless it is the default."""
        default = self.candidates.default
        return [default] if frozen["winner"] == default else [default, self.hpo["frozen_label"]]

    def evaluation_manifest(self, frozen, template):
        """Return the workflow-batch manifest of the tuned-vs-default comparison over the remaining folds.

        Parameters
        ----------
        frozen : dict
            ``hpo-frozen.json``.
        template : str
            The zoo template's path as the manifest's directory resolves it.

        Raises
        ------
        ValueError
            The recipe's default cell is no longer the one the HPO ran against.
        """
        check_frozen(frozen)
        cells, _ = self.cells([self.candidates.default])
        for lane in self.lane_names:
            if cells[self.lane(self.candidates.default, lane)]["fit"] != frozen["default_params"][lane]:
                raise ValueError(f"lane {lane}: the recipe's default cell differs from the one the HPO ran against; "
                                 "rerun the search")
        names = self.evaluation_lanes(frozen)
        params = {self.hpo["frozen_label"]: frozen["params"]}
        cells, _ = self.cells(names, params)
        manifest = self._manifest(cells, self.evaluation_schedule, self.hpo["frozen_work_dir"], template, self.hpo["evaluate_notes"])
        manifest["args"]["hpo"] = {"pick": self.winner["cell"], "winner": frozen["winner"], "warmup_folds": self.warmup,
                                   "hparams_sha256": frozen["hparams_sha256"]}
        return manifest

    def evaluation(self, results, frozen):
        """Return the tuned-vs-default record over the remaining folds.

        Parameters
        ----------
        results : dict
            ``{candidate: ZooResults}`` of the evaluation batch.
        frozen : dict
            ``hpo-frozen.json``.
        """
        default = self.candidates.default
        expected = self.evaluation_schedule.cutoffs()
        cells = {n: results[n].cell(self.cell_name, self.suffixes) for n in self.evaluation_lanes(frozen)}
        for name, cell in cells.items():
            if list(cell.cutoffs) != expected:
                raise ValueError(f"{name}: folds {list(cell.cutoffs)} are not the evaluation schedule {expected}")
        names = results[default].metric_names
        record = {"pick": self.winner["cell"], "winner": frozen["winner"], "cutoffs": expected,
                  "candidates": {n: {"mean": c.mean, "per_fold": list(c.scores),
                                     "overall": {m: sum(c.metric(m)) / len(expected) for m in names}} for n, c in cells.items()}}
        if len(cells) == 2:
            tuned = cells[self.hpo["frozen_label"]]
            record["tuned_vs_default"] = paired(tuned, cells[default])
            record["tuned_better"] = record["tuned_vs_default"]["mean"] < 0
        else:
            record["tuned_vs_default"] = None
            record["note"] = "the search kept the default candidate; there is no tuned cell to compare"
        return record


def _candidate_results(root, recipe, template, names):
    """Return ``{candidate: ZooResults}`` reading lanes ``<candidate>-<lane>`` under ``root``."""
    return {n: ZooResults(root, recipe, template, f"{n}-") for n in names}


def hpo_manifest_main(argv=None):
    """Write the search manifest of a zoo pick (``hpo-manifest``).

    Parameters
    ----------
    argv : list of str, optional
        ``--pick zoo-pick.json``, ``--out MANIFEST``, optional ``--recipe``.

    Returns
    -------
    int
        0.
    """
    parser = argparse.ArgumentParser(prog="zoo_lanes hpo-manifest", description="Search manifest: one lane per HPO candidate of the picked cell.")
    parser.add_argument("--pick", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--recipe", help="default: the recipe the pick recorded (its bytes must be unchanged)")
    args = parser.parse_args(argv)
    pick = load_json(args.pick)
    recipe_path = resolve_recipe(pick, args.recipe)
    recipe, lanes, _, here = load_recipe(recipe_path)
    write_json(args.out, HpoPlan(recipe, lanes, pick).search_manifest(template_path(here, recipe["template"], args.out)))
    return 0


def hpo_pick_main(argv=None):
    """Read the finished search and write ``hpo-frozen.json`` (``hpo-pick``).

    Parameters
    ----------
    argv : list of str, optional
        ``--pick``, ``--out``, one of ``--root`` / ``--batch`` (the search batch's root, or its ``batch.json``,
        which must show every lane ok), optional ``--recipe``.

    Returns
    -------
    int
        0.
    """
    parser = argparse.ArgumentParser(prog="zoo_lanes hpo-pick", description="Freeze the best HPO candidate on the warmup folds.")
    parser.add_argument("--pick", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--root")
    parser.add_argument("--batch")
    parser.add_argument("--recipe")
    args = parser.parse_args(argv)
    pick = load_json(args.pick)
    recipe_path = resolve_recipe(pick, args.recipe)
    recipe, lanes, template, _ = load_recipe(recipe_path)
    plan = HpoPlan(recipe, lanes, pick)
    results = _candidate_results(batch_root(args.root, args.batch), recipe, template, plan.candidates.names())
    frozen = plan.freeze(results)
    sources = {}
    for name, res in results.items():
        res.check_rows()
        sources.update({f"{name}": res.sources()})
    write_json(args.out, {"recipe": pick["recipe"], "pick_file": {"path": os.path.abspath(args.pick), "sha256": file_sha256(args.pick)},
                           **frozen, "sources": sources})
    print(f"hpo-pick: {frozen['winner']} (warmup mean {frozen['candidates'][frozen['winner']]['mean']:.6g}); written to {args.out}", flush=True)
    return 0


def frozen_manifest_main(argv=None):
    """Write the tuned-vs-default manifest over the remaining folds (``frozen-manifest``).

    Parameters
    ----------
    argv : list of str, optional
        ``--frozen hpo-frozen.json``, ``--pick zoo-pick.json``, ``--out MANIFEST``, optional ``--recipe``.

    Returns
    -------
    int
        0.
    """
    parser = argparse.ArgumentParser(prog="zoo_lanes frozen-manifest", description="Default vs frozen winner over the folds after the warmup.")
    parser.add_argument("--frozen", required=True)
    parser.add_argument("--pick", required=True, help="the zoo-pick.json the freeze was made from (its sha256 is checked)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--recipe")
    args = parser.parse_args(argv)
    frozen = load_frozen(args.frozen, args.pick)
    recipe_path = resolve_recipe(frozen, args.recipe)
    recipe, lanes, _, here = load_recipe(recipe_path)
    write_json(args.out, HpoPlan(recipe, lanes, {"winner": frozen["pick_winner"]}).evaluation_manifest(frozen, template_path(here, recipe["template"], args.out)))
    return 0


def evaluate_main(argv=None):
    """Read the finished evaluation and write ``hpo-evaluation.json`` (``hpo-evaluate``).

    Parameters
    ----------
    argv : list of str, optional
        ``--frozen``, ``--pick``, ``--out``, one of ``--root`` / ``--batch``, optional ``--recipe``.

    Returns
    -------
    int
        0.
    """
    parser = argparse.ArgumentParser(prog="zoo_lanes hpo-evaluate", description="Tuned vs default on the folds after the warmup.")
    parser.add_argument("--frozen", required=True)
    parser.add_argument("--pick", required=True, help="the zoo-pick.json the freeze was made from (its sha256 is checked)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--root")
    parser.add_argument("--batch")
    parser.add_argument("--recipe")
    args = parser.parse_args(argv)
    frozen = load_frozen(args.frozen, args.pick)
    recipe_path = resolve_recipe(frozen, args.recipe)
    recipe, lanes, template, _ = load_recipe(recipe_path)
    plan = HpoPlan(recipe, lanes, {"winner": frozen["pick_winner"]})
    results = _candidate_results(batch_root(args.root, args.batch), recipe, template, plan.evaluation_lanes(frozen))
    record = plan.evaluation(results, frozen)
    for res in results.values():
        res.check_rows()
    write_json(args.out, {"frozen_file": {"path": os.path.abspath(args.frozen), "sha256": file_sha256(args.frozen)}, **record})
    gap = record["tuned_vs_default"]
    print("hpo-evaluate: " + (f"tuned - default = {gap['mean']:+.6g} (SE {gap['se']:.3g}) over {gap['n']} folds"
                              if gap else record["note"]) + f"; written to {args.out}", flush=True)
    return 0
