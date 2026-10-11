"""Read the zoo's walk-forward results and pick a model: ranking, eligibility against the baselines, metric tables.

The zoo (``zoo_lanes``) leaves one ``walkforward.json`` per lane. This module reads them and answers one question:
which MODEL cell is the best predictor, and does it beat both baselines of its scope? A *cell* is a model-loss pair
in a scope: ``pooled`` (one lane) or ``unpooled`` (its btc and eth lanes, combined fold by fold weighted by each
lane's validation row count). The primary key is the mean over the folds of the zoo template's walk-forward objective
(read from the template, never restated), lower is better; a cell is eligible only if its mean is below BOTH
baselines' means in the same scope. Everything not derived from the results (the metric names, the near-tie width,
the layout of a lane's run directory, the file names) is the recipe's ``pick`` / ``lane_runs`` section.

What the summary exposes: a fold's score, cutoff and run directory, but no row counts or metrics. Those are read from
the fold's own ``horizon_scores.json`` (``PanelScore``'s artifact), which holds the validation row count and the
per-horizon tables; a PIT statistic of an unpooled cell is the row-weighted mean of its two lanes, an approximation
(a KS statistic does not add), while CRPS-family scores and pinball are exact.

The reading classes (:class:`LaneFolds`, :class:`CellFolds`, :class:`ZooResults`) are shared with the HPO and
production steps. Import cost: stdlib + dskit core (no torch).
"""

import argparse
import hashlib
import json
import os
import statistics

from dskit.pipeline.libs.panel_score import SCORES_NAME
from dskit.pipeline.stats import across_fold_t

from .feature_selection import FoldScores
from .zoo_lanes import ZooLanes

__all__ = ["CellFolds", "EXIT_NO_WINNER", "LaneFolds", "ScoreSpec", "ZooPick", "ZooResults", "file_sha256", "hparams_sha256", "load_json", "load_recipe", "main",
           "paired", "resolve_recipe", "template_path", "write_json", "batch_root", "check_frozen", "load_frozen"]

#: A pick with no eligible cell is a result, not an error: the repo's "halted" exit code (a NO-GO gate).
EXIT_NO_WINNER = 3
_ARTIFACTS = "artifacts"
_OVERALL = "overall"
_ROWS = "n_rows"
BATCH_OK = "ok"
BATCH_FILE = "batch.json"
_PANEL_SCORE = "dskit.pipeline.libs.panel_score:PanelScore"


def file_sha256(path):
    """Return the hex sha256 of a file's bytes."""
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def load_json(path):
    """Read a JSON file.

    Parameters
    ----------
    path : str
        The file.

    Returns
    -------
    object
        The parsed document.
    """
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, record):
    """Write ``record`` as indented JSON (NaN refused), creating the directory.

    Parameters
    ----------
    path : str
        The file to write.
    record : object
        A JSON-able document.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record, indent=2, allow_nan=False) + "\n")


def resolve_recipe(record, override=None):
    """Return the recipe path a pick or freeze was written from, refusing a recipe whose bytes changed since.

    Parameters
    ----------
    record : dict
        A ``zoo-pick.json`` or ``hpo-frozen.json`` (its ``recipe`` is ``{"path", "sha256"}``).
    override : str, optional
        A recipe path replacing the recorded one (its bytes must still match).

    Returns
    -------
    str
        The recipe path.

    Raises
    ------
    ValueError
        The recipe's sha256 differs from the recorded one.
    """
    path = override or record["recipe"]["path"]
    if file_sha256(path) != record["recipe"]["sha256"]:
        raise ValueError(f"{path}: the recipe changed since the pick was written; rerun the pick")
    return path


def check_frozen(frozen):
    """Refuse an ``hpo-frozen.json`` whose parameters no longer hash to the ``hparams_sha256`` it recorded.

    Parameters
    ----------
    frozen : dict
        The freeze record (``params`` per lane, ``hparams_sha256`` per lane).

    Raises
    ------
    ValueError
        A lane with no recorded hash, a recorded hash with no params, or params edited after the freeze.
    """
    recorded, params = frozen.get("hparams_sha256"), frozen.get("params")
    if not isinstance(recorded, dict) or not isinstance(params, dict) or set(recorded) != set(params) or not params:
        raise ValueError("hpo-frozen.json: `params` and `hparams_sha256` must name the same non-empty set of lanes")
    for lane, fit in params.items():
        if hparams_sha256(fit) != recorded[lane]:
            raise ValueError(f"hpo-frozen.json: lane {lane} params no longer hash to the recorded hparams_sha256 (edited since the freeze?)")


def load_frozen(path, pick_path=None):
    """Load ``hpo-frozen.json``, verifying its parameter hashes and (given) the pick it was frozen from.

    Parameters
    ----------
    path : str
        The freeze file.
    pick_path : str, optional
        The ``zoo-pick.json`` in use; its bytes must hash to the ``pick_file.sha256`` the freeze recorded.

    Returns
    -------
    dict
        The freeze record.

    Raises
    ------
    ValueError
        As :func:`check_frozen`, or a pick other than the one the freeze was made from.
    """
    frozen = load_json(path)
    check_frozen(frozen)
    if pick_path is not None and file_sha256(pick_path) != frozen["pick_file"]["sha256"]:
        raise ValueError(f"{pick_path} is not the zoo pick {path} was frozen from (sha256 differs)")
    return frozen


def template_path(recipe_dir, template, out):
    """Return the path a manifest written to ``out`` uses to reach ``template`` (a file beside the recipe).

    Parameters
    ----------
    recipe_dir : str
        The recipe's directory.
    template : str
        The template's file name.
    out : str
        The manifest file being written.

    Returns
    -------
    str
        The template path relative to ``out``'s directory.
    """
    return os.path.relpath(os.path.join(recipe_dir, template), os.path.dirname(os.path.abspath(out)))


def batch_root(root, batch):
    """Return a workflow-batch root from ``--root`` or ``--batch``, after checking its ``batch.json`` shows every lane ok.

    Parameters
    ----------
    root : str or None
        The batch root; its ``batch.json`` is read.
    batch : str or None
        The root's ``batch.json``.

    Returns
    -------
    str
        The batch root.

    Raises
    ------
    ValueError
        A lane that is not ok, a ``batch.json`` with no lanes, or no ``batch.json`` under ``root``.
    SystemExit
        Neither or both of ``root`` and ``batch`` given.
    """
    if bool(root) == bool(batch):
        raise SystemExit("give exactly one of --root and --batch")
    path = batch or os.path.join(root, BATCH_FILE)
    if not os.path.isfile(path):
        raise ValueError(f"{path}: no batch.json: the batch did not run to the end")
    status = load_json(path)
    bad = {x.get("lane"): x.get("status") for x in status.get("lanes", []) if x.get("status") != BATCH_OK}
    if bad or not status.get("lanes"):
        raise ValueError(f"{path}: not every lane is ok: {bad or 'no lanes recorded'}")
    return root or os.path.dirname(os.path.abspath(batch))


def hparams_sha256(fit, exclude=()):
    """Return the hex sha256 of a cell's whole ``fit`` params minus ``bounds`` (architecture, features, optimizer, loss: everything but the split).

    Parameters
    ----------
    fit : dict
        A cell's ``fit`` params.
    exclude : tuple of str, default ()
        Further keys left out (the production pipeline's declared overrides, e.g. ``row_stride``).

    Returns
    -------
    str
        The hash of the canonical JSON; equal for a lane across folds and blocks exactly when no hyperparameter moved.
    """
    body = {k: v for k, v in fit.items() if k != "bounds" and k not in exclude}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def load_recipe(path):
    """Load a recipe and the files it names; returns ``(recipe, ZooLanes, template, recipe_dir)``.

    Parameters
    ----------
    path : str
        The recipe file.

    Returns
    -------
    tuple
        The recipe dict, a :class:`ZooLanes` on its base manifest, the zoo template dict and the recipe's directory.
    """
    here = os.path.dirname(os.path.abspath(path))
    with open(path, encoding="utf-8") as handle:
        recipe = json.load(handle)
    with open(os.path.join(here, recipe["base"]), encoding="utf-8") as handle:
        lanes = ZooLanes(recipe, json.load(handle))
    with open(os.path.join(here, recipe["template"]), encoding="utf-8") as handle:
        template = json.load(handle)
    return recipe, lanes, template, here


def paired(cell, reference):
    """Return the paired per-fold difference ``cell - reference`` (negative = the cell is better) with its statistics.

    Parameters
    ----------
    cell : CellFolds
        The cell under test.
    reference : CellFolds
        The cell it is compared with, on the same folds.

    Returns
    -------
    dict
        ``reference``, ``mean``, ``se``, ``t``, ``p_value``, ``n`` and the ``per_fold`` differences.
    """
    if cell.cutoffs != reference.cutoffs:
        raise ValueError(f"{cell.name} and {reference.name} were scored on different folds")
    diffs = [a - b for a, b in zip(cell.scores, reference.scores)]
    stats = across_fold_t(diffs)
    return {"reference": reference.name, **{k: stats[k] for k in ("mean", "se", "t", "p_value", "n")}, "per_fold": diffs}


class ScoreSpec:
    """What the zoo template says about scoring: the walk-forward objective and the score node's name.

    Parameters
    ----------
    template : dict
        ``zoo-template.json``; the attributes are ``objective``, ``node`` (the score node's key) and ``model_node``
        (the fit node the score reads its model from).

    Raises
    ------
    ValueError
        No walk-forward objective, or no (or more than one) ``PanelScore`` node.

    Examples
    --------
    Read the objective a lane's folds report::

        spec = ScoreSpec(template)
        spec.objective, spec.node
        # -> ("$score.metrics.weighted_crps", "score")
    """

    def __init__(self, template):
        self.objective = template.get("walkforward", {}).get("objective")
        if not isinstance(self.objective, str):
            raise ValueError("the template declares no walkforward.objective")
        nodes = [k for k, v in template.get("pipeline", {}).items() if v.get("uses") == _PANEL_SCORE]
        if len(nodes) != 1:
            raise ValueError(f"the template must hold exactly one {_PANEL_SCORE} node, found {nodes}")
        self.node = nodes[0]
        wired = template["pipeline"][self.node].get("inputs", {}).get("model")
        if not (isinstance(wired, str) and wired.startswith("$")):
            raise ValueError(f"the template's {self.node} node must take `model` from the fit node, got {wired!r}")
        self.model_node = wired[1:].split(".")[0]


class LaneFolds:
    """One lane's walk-forward: fold scores plus each fold's row count, rows hash and metric means.

    Parameters
    ----------
    lane : str
        The lane's name.
    folds : FoldScores
        The summary's fold scores.
    run_dirs : tuple of str
        Each fold's run directory.
    rows : tuple of int
        Each fold's validation row count.
    rows_sha256 : tuple of str
        Each fold's validation-rows hash.
    metrics : dict
        Metric name to its per-fold horizon means.

    Examples
    --------
    Read a lane's run root::

        lane = LaneFolds.read("gru-crps-btc", "root/gru-crps-btc/runs/gru-crps-btc", "score", ["crps"])
        lane.scores[:2]
        # -> (0.91, 0.88)
    """

    def __init__(self, lane, folds, run_dirs, rows, rows_sha256, metrics):
        self.lane, self.folds, self.run_dirs = lane, folds, run_dirs
        self.rows, self.rows_sha256, self.metrics = rows, rows_sha256, metrics

    @property
    def cutoffs(self):
        """The fold cutoffs, in order."""
        return self.folds.cutoffs

    @property
    def scores(self):
        """The fold objective scores, in order."""
        return self.folds.scores

    @classmethod
    def read(cls, lane, directory, score_node, metric_names):
        """Read a lane's summary and each fold's score tables.

        Parameters
        ----------
        lane : str
            The lane's name.
        directory : str
            The lane's run root (holds one ``*-walkforward-*`` directory).
        score_node : str
            The score node's key (the artifact directory under the fold run).
        metric_names : list of str
            The per-horizon tables to average, e.g. ``["crps", "pinball"]``.

        Returns
        -------
        LaneFolds
            The lane's folds.

        Raises
        ------
        ValueError
            No summary, an unfinished fold, or a fold whose score tables are missing or lack a metric.
        """
        folds = FoldScores.read(directory)
        with open(folds.path, encoding="utf-8") as handle:
            summary = json.load(handle)
        runs, rows, hashes, metrics = [], [], [], {name: [] for name in metric_names}
        for fold in summary["folds"]:
            run = cls._run_dir(fold["run_dir"], folds.path)
            table = cls._table(run, score_node, folds.path)
            overall = table[_OVERALL]
            runs.append(run)
            rows.append(int(overall[_ROWS]))
            hashes.append(table["rows_sha256"])
            for name in metric_names:
                if name not in overall:
                    raise ValueError(f"{run}: the score table has no {name!r} (has {sorted(overall)})")
                metrics[name].append(statistics.fmean(overall[name]))
        return cls(lane, folds, tuple(runs), tuple(rows), tuple(hashes), metrics)

    @staticmethod
    def _run_dir(recorded, summary_path):
        """Return the fold's run directory: as recorded, else the same-named one beside the summary."""
        if os.path.isdir(recorded):
            return recorded
        beside = os.path.join(os.path.dirname(summary_path), os.path.basename(recorded))
        if os.path.isdir(beside):
            return beside
        raise ValueError(f"{summary_path}: fold run directory {recorded!r} is gone")

    @staticmethod
    def _table(run, score_node, summary_path):
        """Load a fold's ``horizon_scores.json``."""
        path = os.path.join(run, _ARTIFACTS, score_node, SCORES_NAME)
        if not os.path.isfile(path):
            raise ValueError(f"{summary_path}: fold {run!r} has no {SCORES_NAME} under {_ARTIFACTS}/{score_node}")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)


class CellFolds:
    """A cell's per-fold scores: one lane, or several combined fold by fold weighted by validation rows.

    Parameters
    ----------
    name : str
        The cell's name.
    lanes : list of LaneFolds
        Its lanes (one for a pooled cell, the asset lanes for an unpooled one), on the same folds.

    Raises
    ------
    ValueError
        No lanes or lanes on different folds.

    Examples
    --------
    Combine a model's two asset lanes::

        cell = CellFolds("gru-crps-unpooled", [btc_lane, eth_lane])
        cell.scores[0]  # the fold-0 objective, row-weighted over both assets
    """

    def __init__(self, name, lanes):
        if not lanes:
            raise ValueError(f"{name}: a cell needs at least one lane")
        for other in lanes[1:]:
            lanes[0].folds.same_folds(other.folds)
        self.name, self.lanes = name, list(lanes)
        self.cutoffs = lanes[0].cutoffs
        self.rows = tuple(sum(lane.rows[i] for lane in lanes) for i in range(len(self.cutoffs)))

    def _weighted(self, values):
        """Row-weight one per-fold series per lane into one series."""
        return tuple(sum(v[i] * lane.rows[i] for v, lane in zip(values, self.lanes)) / self.rows[i]
                     for i in range(len(self.cutoffs)))

    @property
    def scores(self):
        """The cell's per-fold objective."""
        return self._weighted([lane.scores for lane in self.lanes])

    def metric(self, name):
        """Return the cell's per-fold value of one metric."""
        return self._weighted([lane.metrics[name] for lane in self.lanes])

    @property
    def mean(self):
        """The mean of the per-fold objective: the ranking key."""
        return statistics.fmean(self.scores)

    @property
    def lane_names(self):
        """The names of the cell's lanes."""
        return [lane.lane for lane in self.lanes]


class ZooResults:
    """Every lane of a batch root, read lazily and checked to agree on folds and rows.

    Parameters
    ----------
    root : str
        The workflow-batch root (``--root``).
    recipe : dict
        The recipe (``lane_runs``, ``pick.metrics``).
    template : dict
        The zoo template (objective and score node).
    lane_prefix : str, default ""
        Prepended to a lane's name when reading (an HPO candidate's lanes carry ``<candidate>-``).

    Examples
    --------
    Read the pooled cell of a model::

        results = ZooResults("pipeline_runs/zoo-w1", recipe, template)
        cell = results.cell("gru-crps", ["pooled"])
    """

    def __init__(self, root, recipe, template, lane_prefix=""):
        self.root, self.recipe, self.prefix = root, recipe, lane_prefix
        self.spec = ScoreSpec(template)
        self.metric_names = list(recipe["pick"]["metrics"])
        self._lanes = {}

    def lane_dir(self, lane):
        """Return the run root of ``lane`` (the recipe's ``lane_runs`` pattern)."""
        return self.recipe["lane_runs"].format(root=self.root, lane=self.prefix + lane)

    def lane(self, name):
        """Return the :class:`LaneFolds` of lane ``name``, reading it once; refuses a foreign objective."""
        if name not in self._lanes:
            found = LaneFolds.read(self.prefix + name, self.lane_dir(name), self.spec.node, self.metric_names)
            if found.folds.objective != self.spec.objective:
                raise ValueError(f"{found.folds.path}: objective is {found.folds.objective!r}, the template's is "
                                 f"{self.spec.objective!r}")
            self._lanes[name] = found
        return self._lanes[name]

    def cell(self, name, suffixes, label=None):
        """Return the :class:`CellFolds` over the lanes ``<name>-<suffix>``, labelled ``label`` (default ``name``)."""
        return CellFolds(label or name, [self.lane(f"{name}-{s}") for s in suffixes])

    def check_rows(self):
        """Refuse lanes of one suffix that were scored on different rows (same fold, different row hash or count)."""
        seen = {}
        for name, lane in self._lanes.items():
            suffix = name.rsplit("-", 1)[1]
            for index, (rows, digest) in enumerate(zip(lane.rows, lane.rows_sha256)):
                first = seen.setdefault((suffix, index), (rows, digest, name))
                if first[:2] != (rows, digest):
                    raise ValueError(f"fold {index} of {name} was scored on other rows than {first[2]} "
                                     f"({rows} vs {first[0]} rows, hash differs: {digest != first[1]})")

    def sources(self):
        """Return ``{lane: {"path", "sha256"}}`` of every summary read."""
        return {name: {"path": lane.folds.path, "sha256": lane.folds.sha256} for name, lane in self._lanes.items()}


class ZooPick:
    """Rank the zoo's model cells and decide whether any beats the baselines.

    Parameters
    ----------
    lanes : ZooLanes
        The generator of the zoo (its scopes and cell names).
    results : ZooResults
        The batch root's results.

    Examples
    --------
    Pick from a finished zoo batch::

        recipe, lanes, template, _ = load_recipe("configs/zoo-lanes-recipe.json")
        pick = ZooPick(lanes, ZooResults("pipeline_runs/zoo-w1", recipe, template))
        record = pick.result()
        # -> record["winner"]["cell"] == "gru-crps_tail-pooled"
    """

    def __init__(self, lanes, results):
        self.lanes, self.results = lanes, results
        self.pick = results.recipe["pick"]

    def _cells(self, names, scope):
        """Return ``[(name, CellFolds)]`` of ``names`` in ``scope``; a cell is labelled ``<name>-<scope>``."""
        return [(n, self.results.cell(n, self.lanes.scopes()[scope], f"{n}-{scope}")) for n in names]

    @staticmethod
    def _entry(cell, model, loss, scope, kind):
        """Return the identity and score fields of one cell for the record."""
        return {"cell": cell.name, "kind": kind, "model": model, "loss": loss, "scope": scope, "lanes": cell.lane_names,
                "mean": cell.mean, "per_fold": list(cell.scores)}

    def _scope_entries(self, scope):
        """Return ``(model entries, baseline entries, cells by label)`` of one scope."""
        models, baselines = self.lanes.cell_names()
        base = self._cells(baselines, scope)
        best = min((c for _, c in base), key=lambda c: c.mean)
        base_entries = [{**self._entry(c, n, None, scope, "baseline"),
                         "vs_best_baseline": None if c is best else paired(c, best)} for n, c in base]
        entries, cells = [], {c.name: c for _, c in base}
        for name, cell in self._cells(models, scope):
            model, loss = name.split("-", 1)
            beats = {c.name: cell.mean < c.mean for _, c in base}
            entries.append({**self._entry(cell, model, loss, scope, "model"), "beats": beats,
                            "eligible": all(beats.values()), "vs_best_baseline": paired(cell, best)})
            cells[cell.name] = cell
        return entries, base_entries, cells

    def result(self):
        """Read every lane and return the full pick record.

        Returns
        -------
        dict
            ``objective``, ``cutoffs``, ``ranking`` (every model cell, lowest mean first, with ``rank``,
            ``eligible``, ``beats`` and the paired difference to the scope's best baseline), ``baselines``,
            ``winner`` (the lowest-mean eligible cell, or ``None``), ``near_ties``, ``metrics`` and ``sources``.
        """
        ranking, baselines, cells = [], [], {}
        for scope in self.lanes.scopes():
            entries, base_entries, scope_cells = self._scope_entries(scope)
            ranking += entries
            baselines += base_entries
            cells.update(scope_cells)
        self.results.check_rows()
        ranking.sort(key=lambda e: (e["mean"], e["cell"]))
        for rank, entry in enumerate(ranking, 1):
            entry["rank"] = rank
        eligible = [e for e in ranking if e["eligible"]]
        winner = eligible[0] if eligible else None
        some = next(iter(cells.values()))
        record = {"objective": self.results.spec.objective, "cutoffs": list(some.cutoffs), "n_folds": len(some.cutoffs),
                  "ranking": ranking, "baselines": baselines, "winner": winner,
                  "near_ties": self._near_ties(winner, eligible, cells), "sources": self.results.sources()}
        record["metrics"] = self._metric_tables(winner, baselines, cells)
        return record

    def _near_ties(self, winner, eligible, cells):
        """List eligible cells within ``near_tie_z`` paired standard errors of the winner (the pick is unchanged)."""
        if winner is None:
            return []
        out = []
        for entry in eligible[1:]:
            gap = paired(cells[entry["cell"]], cells[winner["cell"]])
            if gap["mean"] <= self.pick["near_tie_z"] * gap["se"]:
                out.append({"cell": entry["cell"], "gap_mean": gap["mean"], "gap_se": gap["se"]})
        return out

    def _metric_tables(self, winner, baselines, cells):
        """Return the per-fold and overall metric tables of the winner and the baselines (all of them without a winner)."""
        wanted = ([winner["cell"]] if winner else []) + [b["cell"] for b in baselines]
        names = self.results.metric_names
        tables = {}
        for cell_name in wanted:
            cell = cells[cell_name]
            series = {m: cell.metric(m) for m in names}
            tables[cell_name] = {
                "per_fold": {cutoff: {m: series[m][i] for m in names} for i, cutoff in enumerate(cell.cutoffs)},
                "overall": {m: statistics.fmean(series[m]) for m in names}}
        return {"names": list(names), "fold_window": "keyed by the fold cutoff; each window starts there and lasts the zoo's val_days",
                "cells": tables}

    def markdown(self, record):
        """Return the short ``zoo-pick.md``: the top of the ranking, the baselines, the winner's metrics."""
        top = self.pick["top_rows"]
        lines = [f"# Zoo pick ({record['n_folds']} folds, {record['objective']}, lower is better)", ""]
        if record["winner"]:
            w = record["winner"]
            lines.append(f"Winner: **{w['cell']}** mean {w['mean']:.5f}; "
                         f"vs best baseline {w['vs_best_baseline']['mean']:+.5f} (SE {w['vs_best_baseline']['se']:.5f}).")
        else:
            lines.append("**No cell beats both baselines of its scope.** The chain stops here.")
        if record["near_ties"]:
            lines.append("Near-ties: " + ", ".join(t["cell"] for t in record["near_ties"]) + " (the lowest mean is still the pick).")
        lines += ["", "| rank | cell | mean | vs best baseline (SE) | eligible |", "|---|---|---|---|---|"]
        for e in record["ranking"][:top]:
            gap = e["vs_best_baseline"]
            lines.append(f"| {e['rank']} | {e['cell']} | {e['mean']:.5f} | {gap['mean']:+.5f} ({gap['se']:.5f}) | "
                         f"{'yes' if e['eligible'] else 'no'} |")
        lines += ["", "| baseline | mean |", "|---|---|"]
        lines += [f"| {b['cell']} | {b['mean']:.5f} |" for b in record["baselines"]]
        names = record["metrics"]["names"]
        lines += ["", "Overall metrics (mean over folds)", "", "| cell | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
        for cell, table in record["metrics"]["cells"].items():
            lines.append(f"| {cell} | " + " | ".join(f"{table['overall'][m]:.5f}" for m in names) + " |")
        return "\n".join(lines) + "\n"


def main(argv=None):
    """Write ``zoo-pick.json`` and ``zoo-pick.md`` from a finished zoo batch.

    Parameters
    ----------
    argv : list of str, optional
        ``--root`` (the zoo batch root), ``--recipe`` and ``--out`` (a directory).

    Returns
    -------
    int
        0 with a winner, :data:`EXIT_NO_WINNER` when no cell beats both baselines of its scope (the files are
        still written).
    """
    parser = argparse.ArgumentParser(prog="zoo_lanes pick", description="Rank the zoo's models and pick the best that beats the baselines.")
    parser.add_argument("--root", required=True, help="the zoo workflow-batch root")
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--out", required=True, help="directory for zoo-pick.json and zoo-pick.md")
    args = parser.parse_args(argv)
    recipe, lanes, template, _ = load_recipe(args.recipe)
    pick = ZooPick(lanes, ZooResults(args.root, recipe, template))
    record = pick.result()
    record = {"recipe": {"path": os.path.abspath(args.recipe), "sha256": file_sha256(args.recipe)}, "root": os.path.abspath(args.root),
              **record}
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, recipe["pick"]["out_json"]), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record, indent=2, allow_nan=False) + "\n")
    with open(os.path.join(args.out, recipe["pick"]["out_md"]), "w", encoding="utf-8") as handle:
        handle.write(pick.markdown(record))
    if record["winner"] is None:
        print("zoo pick: no model cell beats both baselines of its scope on the mean objective; "
              f"ranking written to {args.out}", flush=True)
        return EXIT_NO_WINNER
    print(f"zoo pick: {record['winner']['cell']} (mean {record['winner']['mean']:.6g}); written to {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
