"""Fake zoo batch trees for the pick, HPO and production tests: the files a walk-forward leaves, nothing trained.

A lane's run root holds ``<lane>-walkforward-<asof>-<hash>/walkforward.json`` and one run directory per fold with
``artifacts/<score node>/horizon_scores.json``; the writers below produce exactly the fields the readers use.
"""

import json
import os

ASOF = "2026-05-01"
HORIZONS = 3


def _dump(path, record):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle)


def write_lane(root, recipe, lane, cutoffs, scores, rows, objective, metrics=None, score_node="score", tail_weight=1.0, extra_files=None,
               fit=None, fit_node="fit"):
    """Write one lane's run root under ``root`` following the recipe's ``lane_runs`` pattern; returns the run root.

    ``scores`` and ``rows`` are per fold; ``metrics`` maps a metric name to its per-fold value (default: the score).
    ``extra_files`` maps a file name to its text, written under each fold's ``artifacts/fit``; ``fit`` is the realized
    fit params written to each fold's ``config.json`` (as the driver does).
    """
    run_root = recipe["lane_runs"].format(root=root, lane=lane)
    folds = []
    for i, cutoff in enumerate(cutoffs):
        run = os.path.join(run_root, f"{lane}-wf-{cutoff}-{i:04d}")
        overall = {"n_rows": rows[i]}
        for name in recipe["pick"]["metrics"]:
            value = (metrics or {}).get(name, [scores[i]] * len(cutoffs))[i]
            overall[name] = [value] * HORIZONS
        _dump(os.path.join(run, "artifacts", score_node, "horizon_scores.json"),
              {"overall": overall, "rows_sha256": f"rows-{lane.rsplit('-', 1)[1]}-{i}", "tail_weight": tail_weight})
        if fit is not None:
            _dump(os.path.join(run, "config.json"), {"pipeline": {fit_node: {"params": fit}}})
        for name, text in (extra_files or {}).items():
            os.makedirs(os.path.join(run, "artifacts", "fit"), exist_ok=True)
            with open(os.path.join(run, "artifacts", "fit", name), "w", encoding="utf-8") as handle:
                handle.write(text)
        folds.append({"cutoff": cutoff, "run_dir": run, "score": scores[i], "state": "ran"})
    _dump(os.path.join(run_root, f"{lane}-walkforward-{ASOF}-abcd1234", "walkforward.json"),
          {"name": lane, "asof": ASOF, "objective": objective, "select": "min", "state": "ran", "folds": folds,
           "aggregate": {"mean": sum(scores) / len(scores)}})
    return run_root


def write_batch_status(root, lanes, status="ok"):
    """Write the ``batch.json`` a finished workflow-batch leaves."""
    _dump(os.path.join(root, "batch.json"), {"manifest": "m", "lanes": [{"lane": lane, "status": status} for lane in lanes]})
