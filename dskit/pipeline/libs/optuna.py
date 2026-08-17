"""The ``optuna`` library pack — ``OptunaSearch``, TPE-sampled search
(docs/25 §2 row 5, tier 2 per D-146).

This is the optuna-sampled variant of the SAME contract ``hpo-grid``
(:mod:`dskit.pipeline.kinds_search`) reference-implements — shared
semantics, never a forked seam:

* ``objective`` is a ``$``-reference into a val-split score node (the
  planner enforces the wiring, spec §8); by execute time the driver has
  materialized it into the base pass's float, so only strings are
  shape-checked here — exactly ``hpo-grid``'s reading.
* the DRIVER owns re-execution: ``run`` refuses without ``ctx.rerun``,
  the driver-injected subgraph seam
  (:class:`dskit.pipeline.driver._SearchSeam`); each
  ``rerun(overrides)`` call re-executes the minimal dirty subgraph and
  returns the objective as a float.
* the winning overrides are returned as ``best_params``; the driver
  then re-applies them ONE final time so every downstream node consumes
  the winner pass. Ties on score go to the FIRST evaluated trial, the
  ``hpo-grid`` rule.

What this pack ADDS over the grid: optuna's seeded TPE sampler chooses
the trials instead of exhaustive enumeration, and ``space`` values may
be CONTINUOUS specs — ``{"low": .., "high": ..[, "log": true][, "int":
true]}`` — alongside the grid's list-of-scalars (categorical) form.
Both forms share the grid's ``"<node>.<param.path>"`` key grammar
(:data:`dskit.pipeline.kinds_search._SPACE_KEY_OK`, imported, not
copied).

Determinism: the TPE sampler is seeded with the REQUIRED ``seed`` param
and suggestions are drawn over the space's SORTED keys, so the RNG
consumption order is fixed — two runs with the same seed, space,
n_trials and objective produce the SAME trial sequence and the same
winner (tested).

Deliberately absent: pruning. Optuna's pruners act on INTERMEDIATE
values reported mid-trial, and the ``ctx.rerun`` seam returns exactly
one float per trial — there is nothing to prune against. Adding pruner
knobs here would be dead configuration; if a per-epoch reporting seam
ever lands (an I-222 spec ruling), pruning becomes buildable.

INTEGRATION FLAG (for the planner's owner): continuous dict specs
validate clean HERE but are refused at PLAN today —
``dskit.pipeline.planner._search_errors`` hard-codes the
list-of-JSON-scalars space grammar for EVERY search-role node, so a
document carrying ``{"low": .., "high": ..}`` fails ``plan()`` before
this class is consulted. Categorical documents plan and run end to end
now; continuous ones need the planner to learn the spec-dict grammar
(this file may not edit the planner). The gap is pinned by a test in
``tests/pipeline_libs/test_optuna.py``.

Import cost: stdlib + ``dskit.pipeline`` at module level; ``optuna``
is imported ONLY inside ``run()`` (test-enforced by
``tests/pipeline/test_purity.py``).
"""

from __future__ import annotations

import math
import re

from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import parse_node_ref
from dskit.pipeline.kinds_search import (
    _SPACE_KEY_OK,
    _finite_objective,
    _is_json_scalar,
)
from dskit.pipeline.kinds_stats import _reject_unknown
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = ["NODE_KINDS", "OptunaSearch", "register"]

#: The keys a continuous spec dict may carry — anything else is refused
#: by name (default-deny INSIDE the spec, same discipline as the knobs).
_SPEC_KEYS = ("high", "int", "log", "low")

#: Legal ``direction`` values (optuna's own vocabulary).
_DIRECTIONS = ("minimize", "maximize")


def _spec_problems(target, spec) -> list:
    """Problems with one continuous spec dict, empty when none.

    Strict by design: ``low``/``high`` REQUIRED finite numbers with
    ``low < high`` (a degenerate range is a one-value categorical —
    write it as one); ``log``/``int`` optional strict bools; ``log``
    needs ``low > 0`` (``>= 1`` under ``int``, optuna's own floor);
    ``int`` needs integer bounds. Never raises on junk — this runs
    under the conformance fuzz.
    """
    where = f"space[{target!r}]"
    if any(not isinstance(k, str) for k in spec):
        return [f"{where}: spec keys must be strings, got {sorted(map(repr, spec))}"]
    problems = []
    unknown = sorted(set(spec) - set(_SPEC_KEYS))
    if unknown:
        problems.append(
            f"{where}: unknown spec key(s) {unknown} — allowed: {sorted(_SPEC_KEYS)}"
        )
    bounds = {}
    for name in ("low", "high"):
        value = spec.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            problems.append(
                f"{where}: {name} is required and must be a finite number, "
                f"got {value!r}"
            )
        else:
            bounds[name] = value
    for name in ("log", "int"):
        if name in spec and not isinstance(spec[name], bool):
            problems.append(
                f"{where}: {name} must be a bool when present, got {spec[name]!r}"
            )
    is_log = spec.get("log") is True
    is_int = spec.get("int") is True
    if len(bounds) == 2:
        if not bounds["low"] < bounds["high"]:
            problems.append(
                f"{where}: low must be < high, got {bounds['low']!r} >= "
                f"{bounds['high']!r} (a degenerate range is a one-value "
                "categorical — write it as a list)"
            )
        if is_int and any(not isinstance(bounds[n], int) for n in ("low", "high")):
            problems.append(
                f"{where}: int specs need integer bounds, got "
                f"low={bounds['low']!r}, high={bounds['high']!r}"
            )
        if is_log:
            if is_int:
                if bounds["low"] < 1:
                    problems.append(
                        f"{where}: log int specs need low >= 1, got {bounds['low']!r}"
                    )
            elif not bounds["low"] > 0:
                problems.append(
                    f"{where}: log specs need low > 0, got {bounds['low']!r}"
                )
    return problems


class OptunaSearch(Node):
    """Seeded-TPE search over document params (role ``search``,
    registered kind ``optuna-search``) — ``hpo-grid``'s contract, optuna's
    sampler.

    Params: ``space`` (required — ``"node.param.path"`` -> non-empty
    JSON-scalar list (categorical) OR ``{"low", "high"[, "log"][,
    "int"]}`` continuous spec), ``objective`` (required — a
    ``$``-reference into a val-split score node's output), ``n_trials``
    (required int >= 1 — the trial budget; optuna may revisit a
    categorical point, and each visit is a real re-execution),
    ``seed`` (required int >= 0 — seeds the TPE sampler; determinism is
    a contract, not a default), ``direction`` (``minimize``/``maximize``,
    default ``minimize``). Outputs mirror ``hpo-grid``: ``best_params``
    (the winning overrides), ``best_score``, ``trials`` (every evaluated
    trial as ``{"overrides", "score"}``, in execution order).

    A trial reporting a NON-FINITE objective (NaN/±inf) fails the search
    loudly, named by its overrides — ``hpo-grid``'s rule, imported not
    copied (:func:`~dskit.pipeline.kinds_search._finite_objective`);
    optuna's own NaN handling (mark FAIL, keep going) would let this
    node's ledger crown the NaN trial anyway.
    """

    role = "search"
    outputs = ("best_params", "best_score", "trials")

    #: The class's own knobs — anything else is refused by name. Denying
    #: KEYS only: ``objective``'s VALUE is a ``$``-reference string by
    #: design, and stays legal.
    _PARAMS = ("direction", "n_trials", "objective", "seed", "space")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        space = params.get("space")
        if not isinstance(space, dict) or not space:
            problems.append(
                f"space must be a non-empty dict of 'node.param.path' -> "
                f"value list or {{'low','high'}} spec, got {space!r}"
            )
        else:
            for target, spec in space.items():
                if not isinstance(target, str) or not re.match(_SPACE_KEY_OK, target):
                    problems.append(
                        f"space key {target!r} must be '<node>.<param.path>' "
                        "(e.g. 'train.lr')"
                    )
                if isinstance(spec, dict):
                    problems.extend(_spec_problems(target, spec))
                elif not isinstance(spec, (list, tuple)) or not spec:
                    problems.append(
                        f"space[{target!r}] must be a non-empty list of JSON "
                        f"scalars or a {{'low','high'}} spec dict, got {spec!r}"
                    )
                else:
                    bad = [v for v in spec if not _is_json_scalar(v)]
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
            # are shape-checked here — the hpo-grid reading, shared.
            try:
                parse_node_ref(params["objective"])
            except ConfigError:
                problems.append(
                    f"objective must be a '$node.path' reference, got "
                    f"{params['objective']!r}"
                )
        n_trials = params.get("n_trials")
        if isinstance(n_trials, bool) or not isinstance(n_trials, int) or n_trials < 1:
            problems.append(
                f"n_trials is required and must be an int >= 1, got {n_trials!r}"
            )
        seed = params.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            problems.append(
                f"seed is required and must be an int >= 0 (it seeds the TPE "
                f"sampler — determinism is the contract), got {seed!r}"
            )
        if params.get("direction", "minimize") not in _DIRECTIONS:
            problems.append(
                f"direction must be one of {list(_DIRECTIONS)}, got "
                f"{params.get('direction')!r}"
            )
        return problems

    def run(self, ctx, inputs):
        if ctx.rerun is None:
            raise RuntimeError(
                "optuna-search runs under the driver — ctx.rerun (the "
                "subgraph re-execution seam, docs/24 §8) is missing"
            )
        import optuna  # the pack rule: the library only ever pays at execute

        rerun = ctx.rerun
        space = self.params["space"]
        n_trials = self.params["n_trials"]
        direction = self.params.get("direction", "minimize")
        keys = sorted(space)  # fixed suggestion order = fixed RNG consumption
        results = []

        def _objective(trial):
            overrides = {}
            for key in keys:
                spec = space[key]
                if isinstance(spec, dict):
                    log = spec.get("log") is True
                    if spec.get("int") is True:
                        overrides[key] = trial.suggest_int(
                            key, spec["low"], spec["high"], log=log
                        )
                    else:
                        overrides[key] = trial.suggest_float(
                            key, spec["low"], spec["high"], log=log
                        )
                else:
                    overrides[key] = trial.suggest_categorical(key, list(spec))
            # Non-finite refused HERE, at the report, by the rule shared
            # with hpo-grid (_finite_objective). VERIFIED, not assumed:
            # optuna only marks a NaN-returning trial FAIL and carries on
            # — this ledger would already hold the NaN row, the strict
            # winner loop below would let a first-trial NaN stick, and a
            # -inf return optuna even accepts as COMPLETE.
            score = _finite_objective(self.key, overrides, rerun(dict(overrides)))
            results.append({"overrides": overrides, "score": score})
            return score

        # Optuna's own INFO chatter (study creation, per-trial lines) is
        # quieted for the duration — this node's log + the run records
        # carry the trial ledger; the prior verbosity is restored either
        # way so a shared process is left as found.
        verbosity = optuna.logging.get_verbosity()
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        try:
            study = optuna.create_study(
                direction=direction,
                sampler=optuna.samplers.TPESampler(seed=self.params["seed"]),
            )
            # catch=() (the default): a failing trial PROPAGATES — the seam
            # already wrapped it naming the trial's overrides, and a search
            # that quietly skips broken trials reports a fake optimum.
            study.optimize(_objective, n_trials=n_trials)
        finally:
            optuna.logging.set_verbosity(verbosity)
        best = None
        for trial in results:  # strict compare: ties go to the FIRST evaluated
            if (
                best is None
                or (direction == "minimize" and trial["score"] < best["score"])
                or (direction == "maximize" and trial["score"] > best["score"])
            ):
                best = trial
        self.log.info(
            "optuna-search: %d TPE trial(s), seed %d — best score %s at %s",
            len(results),
            self.params["seed"],
            best["score"],
            best["overrides"],
        )
        return {
            "best_params": dict(best["overrides"]),
            "best_score": best["score"],
            "trials": results,
        }


#: The pack's kinds table — the explicit, deliberate registration path
#: (libs/__init__ conventions: nothing auto-registers at toolkit import).
NODE_KINDS = (("optuna-search", OptunaSearch),)


def register(registry=None) -> None:
    """Register this pack's kinds into ``registry`` (default the toolkit
    registry), SKIPPING any name already taken — callable any number of
    times, no import-time side effects."""
    target = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in target:
            target.register(name, cls, owned=False)
