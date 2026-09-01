"""The ``hpo-grid`` search kind — spec §10 step 3's first owned-kind build.

The grid lives in the document (docs/24 §8): ``space`` maps
``"node.param.path"`` override targets to value lists, ``objective``
is a ``$``-reference into a val-split score node, and the DRIVER owns
re-execution — this class never touches the DAG itself. ``run`` refuses
to execute without ``ctx.rerun``, the driver-injected subgraph seam
(:class:`dskit.pipeline.driver._SearchSeam`): each ``rerun(overrides)``
call re-executes the minimal dirty subgraph and returns the objective as
a float. The winning overrides are returned as ``best_params``; the
driver then applies them one final time so every downstream node
consumes the winner pass.

Determinism rules (all sha256, no RNG state — the ``testing.py``
discipline):

* **Enumeration.** The exhaustive grid is ``itertools.product`` over the
  space's SORTED keys, each key's values in their given order — the last
  sorted key varies fastest. Ties on score go to the FIRST trial in this
  enumeration order.
* **Subsampling.** When ``n_trials`` is present and smaller than the
  grid, each trial is ranked by
  ``sha256("{seed}:{canonical-json(overrides)}")`` (the node's own
  ``seed`` param, default 0; canonical = sorted keys, compact
  separators); the ``n_trials`` lexicographically SMALLEST digests
  survive, and the survivors are evaluated in enumeration order.
* **Seeds ensemble** (I-222 reading of §8 "per-trial seed ensemble"):
  when ``seeds`` is present, every trial is evaluated once per seed with
  an ADDITIONAL override setting the top-level ``"seed"`` param of every
  train-role node inside the re-execution subgraph THAT ALREADY DECLARES
  one (the driver surfaces those paths as ``ctx.rerun.seed_targets``;
  params that do not exist are never created), and the trial's score is
  the MEAN across seeds. ``best_params`` carries only the grid
  overrides — the final winner pass runs under the document's own pinned
  seeds, never a trial seed.

Import cost: stdlib only.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re

from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import parse_node_ref
from dskit.pipeline.kinds_stats import _reject_unknown
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = ["HpoGrid", "TopTrials", "register"]

#: A space key: a node key, a dot, then one or more param path segments.
_SPACE_KEY_OK = r"^[a-z_][a-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$"


def _is_json_scalar(value) -> bool:
    """A JSON-legal scalar grid value: null, bool, finite number, string."""
    if value is None or isinstance(value, (bool, str)):
        return True
    return isinstance(value, (int, float)) and math.isfinite(value)


def _grid(space) -> list:
    """The exhaustive grid, deterministically enumerated: sorted space
    keys, each key's values in their given order (the last sorted key
    varies fastest)."""
    keys = sorted(space)
    return [
        dict(zip(keys, combo)) for combo in itertools.product(*(space[k] for k in keys))
    ]


def _finite_objective(key, overrides, score) -> float:
    """The objective one rerun reported, or a refusal NAMING the trial.

    NaN defeats every strict comparison — ``x < nan`` and ``x > nan`` are
    both False — so a first-reported NaN would silently stick as "best"
    and the driver would then re-apply its overrides as the winner; an
    inf outranks every real trial the same way. A search cannot rank what
    the score node could not measure, so the trial fails loudly at the
    instant it reports, carrying its exact overrides and the value.
    Shared with the optuna pack: one rule, never two drifting ones.
    """
    if not math.isfinite(score):
        raise ValueError(
            f"{key}: trial {overrides!r} reported a non-finite objective "
            f"({score!r}) — NaN/inf cannot rank trials and must never win a "
            "search; fix the score node (or drop the space region that "
            "produces it) so every trial reports a finite number"
        )
    return score


def _trial_digest(seed, overrides) -> str:
    """The subsample rank of one trial: sha256 over the seed and the
    trial's canonical JSON (sorted keys, compact separators)."""
    canon = json.dumps(overrides, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{seed}:{canon}".encode()).hexdigest()


def _subsample(trials, n_trials, seed) -> list:
    """The ``n_trials`` trials with the smallest :func:`_trial_digest`,
    kept in enumeration order (module docstring rule)."""
    ranked = sorted(
        range(len(trials)), key=lambda i: (_trial_digest(seed, trials[i]), i)
    )
    keep = set(ranked[:n_trials])
    return [t for i, t in enumerate(trials) if i in keep]


class HpoGrid(Node):
    """Exhaustive (or subsampled) grid search over document params
    (role ``search``, registered kind ``hpo-grid``).

    Params: ``space`` (required — ``"node.param.path"`` -> non-empty
    value list), ``objective`` (required — a ``$``-reference into a
    val-split score node's output), ``select`` (``min``/``max``, default
    ``min``), ``n_trials`` (optional int >= 1: subsample bar; absent =
    exhaustive), ``seeds`` (optional non-empty int list: per-trial seed
    ensemble), ``seed`` (optional int >= 0, default 0: the subsample
    rank seed). Outputs: ``best_params`` (the winning overrides),
    ``best_score`` (its mean score), ``trials`` (every evaluated trial as
    ``{"overrides", "score"[, "seed_scores"]}``).

    A trial reporting a NON-FINITE objective (NaN/±inf) fails the search
    loudly, named by its overrides (:func:`_finite_objective`) — the
    strict-compare winner loop would otherwise let a first-trial NaN
    stick as "best" and hand the driver its overrides as the winner.
    """

    role = "search"
    outputs = ("best_params", "best_score", "trials")

    #: The class's own knobs — anything else is refused by name. Denying
    #: KEYS only: ``objective``'s VALUE is a ``$``-reference string by
    #: design, and stays legal.
    _PARAMS = ("n_trials", "objective", "seed", "seeds", "select", "space")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        space = params.get("space")
        if not isinstance(space, dict) or not space:
            problems.append(
                f"space must be a non-empty dict of 'node.param.path' -> "
                f"non-empty value list, got {space!r}"
            )
        else:
            for target, grid in space.items():
                if not isinstance(target, str) or not re.match(_SPACE_KEY_OK, target):
                    problems.append(
                        f"space key {target!r} must be '<node>.<param.path>' "
                        "(e.g. 'train.lr')"
                    )
                if not isinstance(grid, (list, tuple)) or not grid:
                    problems.append(
                        f"space[{target!r}] must be a non-empty list of JSON "
                        f"scalars, got {grid!r}"
                    )
                else:
                    bad = [v for v in grid if not _is_json_scalar(v)]
                    if bad:
                        problems.append(
                            f"space[{target!r}] values must be JSON scalars "
                            f"(null/bool/number/string), got {bad!r}"
                        )
        if "objective" not in params:
            problems.append(
                "objective is required (a $-reference to a score node's "
                "val-split output, e.g. '$validate.metrics.loss')"
            )
        elif isinstance(params["objective"], str):
            # The document form IS a $-reference (the planner enforces the
            # score/val wiring); by execute-time construction the driver has
            # materialized it into the base pass's value, so only strings
            # are shape-checked here.
            try:
                parse_node_ref(params["objective"])
            except ConfigError:
                problems.append(
                    f"objective must be a '$node.path' reference, got "
                    f"{params['objective']!r}"
                )
        if params.get("select", "min") not in ("min", "max"):
            problems.append(
                f"select must be 'min' or 'max', got {params.get('select')!r}"
            )
        n_trials = params.get("n_trials")
        if n_trials is not None and (
            isinstance(n_trials, bool) or not isinstance(n_trials, int) or n_trials < 1
        ):
            problems.append(f"n_trials must be an int >= 1, got {n_trials!r}")
        seed = params.get("seed", 0)
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            problems.append(f"seed must be an int >= 0, got {seed!r}")
        seeds = params.get("seeds")
        if seeds is not None and (
            not isinstance(seeds, (list, tuple))
            or not seeds
            or any(isinstance(s, bool) or not isinstance(s, int) for s in seeds)
        ):
            problems.append(
                f"seeds must be a non-empty list of ints when present, got {seeds!r}"
            )
        return problems

    def run(self, ctx, inputs):
        if ctx.rerun is None:
            raise RuntimeError("hpo-grid runs under the driver")
        rerun = ctx.rerun
        space = self.params["space"]
        select = self.params.get("select", "min")
        seeds = self.params.get("seeds")
        trials = _grid(space)
        grid_size = len(trials)
        n_trials = self.params.get("n_trials")
        if n_trials is not None and n_trials < grid_size:
            trials = _subsample(trials, n_trials, self.params.get("seed", 0))
        seed_targets = tuple(getattr(rerun, "seed_targets", ()) or ())
        results = []
        for overrides in trials:
            if seeds:
                seed_scores = []
                for s in seeds:
                    trial = dict(overrides)
                    for target in seed_targets:
                        trial[target] = s
                    # Refused per REPORT, naming the exact rerun call (grid
                    # overrides plus the seed override) — the seed is part
                    # of what failed.
                    seed_scores.append(_finite_objective(self.key, trial, rerun(trial)))
                results.append(
                    {
                        "overrides": overrides,
                        "score": sum(seed_scores) / len(seed_scores),
                        "seed_scores": seed_scores,
                    }
                )
            else:
                results.append(
                    {
                        "overrides": overrides,
                        "score": _finite_objective(
                            self.key, overrides, rerun(dict(overrides))
                        ),
                    }
                )
        best = None
        for trial in results:  # strict compare: ties go to the FIRST enumerated
            if (
                best is None
                or (select == "min" and trial["score"] < best["score"])
                or (select == "max" and trial["score"] > best["score"])
            ):
                best = trial
        self.log.info(
            "hpo-grid: evaluated %d/%d trial(s)%s — best score %s at %s",
            len(results),
            grid_size,
            f" x {len(seeds)} seed(s)" if seeds else "",
            best["score"],
            best["overrides"],
        )
        return {
            "best_params": dict(best["overrides"]),
            "best_score": best["score"],
            "trials": results,
        }


class TopTrials(Node):
    """Pick an ensemble pool from a search ledger (kind ``top-trials``).

    Rank ``trials`` by score, keep the top ``frac``, sample ``size``
    members with replacement, assign distinct seeds (ADR-0052).

    Parameters
    ----------
    params : dict
        ``frac`` (float in (0, 1]), ``size`` (int >= 1), ``seed``
        (int >= 0), optional ``select`` (``min``/``max``, default
        ``min``).

    Examples
    --------
    Draw 5 members from the top tenth::

        node = TopTrials("ensemble", {"frac": 0.1, "size": 5, "seed": 1})
        node.params["size"]  # 5
    """

    role = "transform"
    outputs = ("members", "metrics")
    _PARAMS = ("frac", "seed", "select", "size")

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        frac = params.get("frac")
        if (
            isinstance(frac, bool)
            or not isinstance(frac, (int, float))
            or not (0.0 < float(frac) <= 1.0)
        ):
            problems.append(f"frac must be in (0, 1], got {frac!r}")
        size = params.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            problems.append(f"size must be an int >= 1, got {size!r}")
        seed = params.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            problems.append(f"seed must be an int >= 0, got {seed!r}")
        if params.get("select", "min") not in ("min", "max"):
            problems.append(
                f"select must be 'min' or 'max', got {params.get('select')!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        """Require a list of ``{overrides, score}`` trial rows."""
        trials = inputs.get("trials")
        if not isinstance(trials, list) or not trials:
            return [f"trials must be a non-empty list, got {trials!r}"]
        problems = []
        for i, row in enumerate(trials):
            if not isinstance(row, dict) or "score" not in row:
                problems.append(f"trials[{i}] must be an object with score")
        return problems

    def run(self, ctx, inputs):
        """Rank, keep the top fraction, sample members.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Unused.
        inputs : dict
            ``trials`` from a search node.

        Returns
        -------
        dict
            ``members`` and ``metrics``.
        """
        trials = list(inputs["trials"])
        select = self.params.get("select", "min")
        reverse = select == "max"
        ranked = sorted(trials, key=lambda row: row["score"], reverse=reverse)
        keep = max(1, math.ceil(float(self.params["frac"]) * len(ranked)))
        pool = ranked[:keep]
        size = int(self.params["size"])
        seed = int(self.params["seed"])
        members = []
        for i in range(size):
            digest = hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()
            picked = pool[int(digest, 16) % len(pool)]
            members.append({
                "overrides": dict(picked.get("overrides") or {}),
                "score": picked["score"],
                "seed": seed + i,
            })
        self.log.info(
            "top-trials: %d of %d trials -> %d members",
            keep, len(ranked), size,
        )
        return {
            "members": members,
            "metrics": {
                "n_trials": float(len(ranked)),
                "n_pool": float(keep),
                "n_members": float(size),
            },
        }


def register(registry=None) -> None:
    """Register ``hpo-grid`` and ``top-trials`` into ``registry``."""
    target = DEFAULT_NODE_KINDS if registry is None else registry
    if "hpo-grid" not in target:
        target.register("hpo-grid", HpoGrid, owned=False)
    if "top-trials" not in target:
        target.register("top-trials", TopTrials, owned=False)
