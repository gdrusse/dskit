"""Solver-latency benchmark for the tier-2 ``ScenarioUtilitySolve`` doorway.

ADR-0188 formulation B, test item T12 — the latency half of the plan's
item 8, "Solver trust" (children/intraday_equities/docs/research/
mio-joint-policy/2026-09-26-from-scratch-formulation.md): 100+ randomized
N = 12, S = 128 instances solve with median under 3 s. The other half (a
10 s time limit halts, never trades) is tested on its own.

Each instance is a seeded, realistic per-minute joint decision, driven
through ``dskit.pipeline.libs.pyomo.ScenarioUtilitySolve`` exactly as a
caller drives it (``node.run`` on :class:`LatencySolve`, a concrete
subclass that adds no row of its own), so the program timed is the
doorway's rows and nothing else:

* N = 12 names (the live universe's symbols, as labels only) and S = 128
  equally weighted scenarios;
* per name, ``K_i`` exit-horizon tranches drawn from
  :data:`TRANCHE_CHOICES`, so every name carries a ``(K_i, S)`` payoff
  matrix and ``K_i`` continuous tranche variables;
* JOINT scenarios: every one-minute step is one common factor plus
  idiosyncratic noise, cumulated along the path, so a scenario is
  correlated across names (the factor) and across horizons (the path).
  The exit-at-minute-``k`` return ``r_io(k)`` has mean ``mu_i sqrt(k)``
  (a few bp, some names negative) and dispersion ``sigma_i sqrt(k)``
  (``sigma_i`` 8-15 bp), each (name, horizon) row recentered on its mean
  the way the forecast bundle recenters;
* prices log-uniform on $20-1500; about half the names held; cash about
  $10,000 with ``buying_power = cash``; per-share costs of the Schwab kind
  (a 0.5-6 bp half-spread per side, a sell also paying TAF- and
  Section-31-scale fees) and an exit cost equal to the sell cost;
  ``x_max_i = max(gross bound, price_i * held_i)`` and ``gross_limit`` the
  gross bound ``cash + marks`` (no leverage); ``carried_wealth > 0`` in
  about a third of instances; the wealth envelope derived the way
  ``EquityKellyMIO.instruments`` derives it;
* params: CRRA gamma 2, 32 tangents, a CVaR (alpha 0.95) cap drawn
  around $500, and, as formulation B specifies, no cardinality and no
  minimum ticket.

Every instance is feasible by construction: selling every held share is
always a feasible point, and :func:`make_instance` proves it (inside the
wealth envelope, under the CVaR cap, cash non-negative) before handing the
instance out, refusing by name otherwise.

What is timed
-------------
``wall_seconds`` is the whole ``node.run``: model build, solve, the
``SolveRecord`` read and the exact post-solve recompute. That is the
per-minute decision's own latency and the plan's timing convention
(build + solve, docs/plans/2026-09-intraday-equities-mio.md §4), and it
is what the budget is checked on. ``solve_seconds`` is the doorway's own
``SolveRecord.seconds`` (``solver.solve`` alone). One untimed warm-up
solve runs first, so no timed instance pays pyomo's or highspy's one-time
import and first-solve setup (a live process solves every minute, warm).
HiGHS runs under the doorway's pins (one thread, fixed seed, zero
relative gap) plus a ``time_limit`` hang guard (:data:`HANG_GUARD_S`) far
above the budget, so a slow instance reports its true time instead of
being censored at the production halt.

Timings depend on the machine and its load; a busy machine reads
pessimistic. The report carries the load average and library versions,
and each instance's ``digest`` lets another machine confirm it
regenerated the identical instance before comparing times.

Usage::

    PYTHONPATH=$PWD python tools/mio_latency_bench.py --n 100 --json out.json

The distribution prints as JSON on stdout (one progress line per instance
on stderr). The exit status is 0 only when every solve was optimal and
the budget held: end-to-end median < 3 s and p99 < 10 s.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import tempfile
import time

import numpy as np

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    # This checkout's dskit, never a virtualenv's editable install of some
    # other checkout: timing the wrong doorway is the one failure a latency
    # number cannot reveal. main() re-checks where dskit came from.
    sys.path.insert(0, _REPO_ROOT)

from dskit.pipeline.libs.pyomo import ScenarioUtilitySolve  # noqa: E402
from dskit.pipeline.node import NodeContext  # noqa: E402

#: The live universe's symbols, LABELS only: every number is drawn.
NAMES = (
    "ADBE", "CIEN", "IWM", "LITE", "LLY", "LRCX",
    "LULU", "MSTR", "NOW", "PANW", "TER", "XLK",
)

#: The multiset each name's exit-tranche count ``K_i`` is drawn from. The
#: live universe's admitted caps are 5 for nine names, LLY 3, and LRCX and
#: MSTR 10, so 5 is the common case and 10 the heavy tail; 2 is a synthetic
#: short cap no live name has, drawn for coverage. No 1: every name carries
#: tranche variables (a one-row payoff is the doorway's flat case).
TRANCHE_CHOICES = (2, 3, 5, 5, 5, 10)

#: Scenarios per instance, equally weighted.
N_SCENARIOS = 128

#: The first instance's seed; instance ``j`` of a run is seed ``BASE_SEED + j``.
BASE_SEED = 20260926

#: The untimed warm-up instance's seed, outside every run's seed range.
WARM_UP_SEED = BASE_SEED - 1

#: The budget (plan item 8), on END-TO-END seconds, over instances that
#: must all terminate optimal.
MEDIAN_BUDGET_S = 3.0
P99_BUDGET_S = 10.0

#: The HiGHS ``time_limit`` the benchmark solves under: a hang guard, far
#: above :data:`P99_BUDGET_S`, never the production halt.
HANG_GUARD_S = 60.0

#: The node params every instance shares (``cvar_limit`` is drawn per
#: instance). Formulation B: no cardinality and no minimum-ticket rows.
PARAMS = {
    "risk_aversion_gamma": 2.0,
    "n_tangents": 32,
    "n_scenarios_max": N_SCENARIOS,
    "cvar_alpha": 0.95,
    "cardinality": None,
    "min_ticket": 0.0,
}

# The draw ranges, uniform unless noted. Fees are synthetic magnitudes at
# the scale of fill-policy.json's illustrative Schwab figures, not copies.
PRICE_RANGE = (20.0, 1500.0)  # $/share, log-uniform
DRIFT_BP = (-3.0, 5.0)  # mu_i, bp per sqrt(minute); ~3/8 of names drift down
SIGMA_BP = (8.0, 15.0)  # sigma_i, bp per sqrt(minute)
FACTOR_LOADING = (0.3, 0.8)  # each name's correlation with the common factor
HALF_SPREAD_BP = (0.5, 6.0)  # per side, bp of price
TAF_PER_SHARE = (1e-4, 3e-4)  # $/share, sells only
SEC31_BP = (0.01, 0.03)  # bp of price, sells only
HELD_PROBABILITY = 0.5
HELD_NOTIONAL = (500.0, 4000.0)  # $ per held name
CASH = (8000.0, 12000.0)
CARRIED_PROBABILITY = 1.0 / 3.0
CARRIED = (1000.0, 10000.0)
CVAR_LIMIT = (400.0, 600.0)

# The wealth envelope, EquityKellyMIO.instruments' rule: half-width = the
# notional cap times the largest |scenario return| (both floored at 5%),
# padded 1.5x, the lower edge never below 1% of net worth.
ENVELOPE_FLOOR_FRAC = 0.05
ENVELOPE_PAD = 1.5

#: The decision date the node context carries (the doorway reads none).
ASOF = "2026-09-26"

#: The CVaR cap BINDS when the exact recomputed CVaR is within this many
#: dollars of it. Read off the recompute, never the SolveRecord's
#: ``cvar_cap`` row: nothing prices ``eta``/``z``, so the solver may leave
#: that row tight at a vertex whose true CVaR is far below the cap.
CVAR_BINDING_DOLLARS = 0.01


class LatencySolve(ScenarioUtilitySolve):
    """The doorway with nothing added: the instance IS the inputs, no domain rows.

    :meth:`instruments` and :meth:`payoffs` hand back the instance's own
    fields and :meth:`domain_constraints` adds no row (formulation B's
    false-signal row is a caller's domain policy, not a doorway row), so
    a timed solve is exactly the doorway's program.

    Attributes
    ----------
    last_model : pyomo.environ.ConcreteModel or None
        The model the last :meth:`run` built, kept so the harness can
        count its variables after the timed window closes.
    """

    outputs = ("target", "trades", "cash_after", "metrics")

    last_model = None

    def instruments(self, inputs):
        """Return the instance's ``(names, rows, account)``."""
        return inputs["names"], inputs["rows"], inputs["account"]

    def payoffs(self, inputs):
        """Return the instance's ``(weights, r)``."""
        return inputs["weights"], inputs["r"]

    def domain_constraints(self, model, inputs, params):
        """Add no row; keep a reference to the built model."""
        self.last_model = model


def make_instance(seed):
    """Build one seeded per-minute joint instance (see the module docstring).

    Parameters
    ----------
    seed : int
        The instance's seed for ``numpy.random.default_rng``; the same
        seed gives the same instance, float for float (compare ``digest``).

    Returns
    -------
    dict
        The node's inputs (``names``, ``rows``, ``weights``, ``r`` with a
        ``(K_i, S)`` array per name, ``account``) plus ``params`` (the node
        params, no solver options), ``seed``, ``tranches`` (name -> ``K_i``)
        and ``digest`` (sha256 of the canonical JSON of everything the
        solve reads).

    Raises
    ------
    ValueError
        When the drawn instance fails its own feasibility proof: selling
        every held share must leave cash non-negative and wealth inside the
        envelope with its (certain) loss under the CVaR cap.

    Examples
    --------
    ::

        inst = make_instance(BASE_SEED)
        inst["r"]["LLY"].shape  # (K_LLY, 128)
    """
    rng = np.random.default_rng(seed)
    n = len(NAMES)
    k_max = max(TRANCHE_CHOICES)
    tranches = [int(k) for k in rng.choice(TRANCHE_CHOICES, size=n)]
    price = np.round(np.exp(rng.uniform(*np.log(PRICE_RANGE), size=n)), 2)
    drift = rng.uniform(*DRIFT_BP, size=n) * 1e-4
    sigma = rng.uniform(*SIGMA_BP, size=n) * 1e-4
    loading = rng.uniform(*FACTOR_LOADING, size=n)
    half_spread = rng.uniform(*HALF_SPREAD_BP, size=n) * 1e-4
    taf = float(rng.uniform(*TAF_PER_SHARE))
    sec31 = float(rng.uniform(*SEC31_BP)) * 1e-4
    held_mask = rng.random(n) < HELD_PROBABILITY
    held_notional = rng.uniform(*HELD_NOTIONAL, size=n)
    cash = round(float(rng.uniform(*CASH)), 2)
    carried = round(float(rng.uniform(*CARRIED)), 2)  # drawn always: a fixed draw order
    carried = carried if rng.random() < CARRIED_PROBABILITY else 0.0
    cvar_limit = round(float(rng.uniform(*CVAR_LIMIT)), 2)
    factor = rng.standard_normal((N_SCENARIOS, k_max))
    idio = rng.standard_normal((n, N_SCENARIOS, k_max))

    # One-minute shocks: a common factor plus idiosyncratic noise (unit
    # variance per step), cumulated along the path, so scenario o's exit at
    # minute k shares its first k shocks with every later exit of the same
    # name and its factor draws with every other name.
    shocks = loading[:, None, None] * factor[None] + np.sqrt(1.0 - loading**2)[:, None, None] * idio
    paths = np.cumsum(shocks, axis=2)
    paths -= paths.mean(axis=1, keepdims=True)  # recenter every (name, horizon) row
    root_k = np.sqrt(np.arange(1, k_max + 1))

    names = list(NAMES)
    held = [int(max(1, round(held_notional[i] / price[i]))) if held_mask[i] else 0 for i in range(n)]
    marks = float(sum(price[i] * held[i] for i in range(n)))
    gross = cash + marks
    r, rows = {}, {}
    for i, name in enumerate(names):
        k = tranches[i]
        r[name] = drift[i] * root_k[:k, None] + sigma[i] * paths[i, :, :k].T
        cost_buy = float(half_spread[i] * price[i])
        cost_sell = cost_buy + taf + sec31 * float(price[i])
        rows[name] = {
            "price": float(price[i]),
            "held": held[i],
            "x_max": max(gross, float(price[i]) * held[i]),
            "cost_buy": cost_buy,
            "cost_sell": cost_sell,
            "exit_cost_per_share": cost_sell,
        }

    w0 = gross + carried
    extreme = max(ENVELOPE_FLOOR_FRAC, max(float(np.abs(v).max()) for v in r.values()))
    notional_cap = min(sum(row["x_max"] for row in rows.values()), gross)
    span = max(notional_cap * extreme, ENVELOPE_FLOOR_FRAC * max(w0, 1.0)) * ENVELOPE_PAD
    account = {
        "cash": cash,
        "buying_power": cash,
        "sale_credit": 1.0,
        "cash_reserve": 0.0,
        "gross_limit": gross,
        "wealth_lo": max(0.01 * w0, w0 - span),
        "wealth_hi": w0 + span,
    }
    if carried > 0.0:
        account["carried_wealth"] = carried

    instance = {
        "seed": int(seed),
        "names": names,
        "rows": rows,
        "weights": np.full(N_SCENARIOS, 1.0 / N_SCENARIOS),
        "r": r,
        "account": account,
        "params": {**PARAMS, "cvar_limit": cvar_limit},
        "tranches": dict(zip(names, tranches)),
    }
    _prove_feasible(instance)
    instance["digest"] = _digest(instance)
    return instance


def _prove_feasible(instance):
    """Refuse an instance whose sell-everything point is not feasible."""
    rows, account = instance["rows"], instance["account"]
    proceeds = sum((row["price"] - row["cost_sell"]) * row["held"] for row in rows.values())
    fees = sum(row["cost_sell"] * row["held"] for row in rows.values())
    w0 = account["cash"] + sum(row["price"] * row["held"] for row in rows.values())
    w0 += account.get("carried_wealth", 0.0)
    wealth = w0 - fees
    problems = []
    if account["cash"] + proceeds < account["cash_reserve"]:
        problems.append("selling everything leaves cash below the reserve")
    if not account["wealth_lo"] < wealth < account["wealth_hi"]:
        problems.append(
            f"sell-everything wealth {wealth!r} is outside the envelope "
            f"[{account['wealth_lo']!r}, {account['wealth_hi']!r}]"
        )
    if fees > instance["params"]["cvar_limit"]:
        problems.append(f"sell-everything fees {fees!r} exceed the CVaR cap")
    if problems:
        raise ValueError(f"seed {instance['seed']}: infeasible instance: " + "; ".join(problems))


def _digest(instance):
    """Return the sha256 of the canonical JSON of what the solve reads."""
    canonical = {
        "names": instance["names"],
        "rows": instance["rows"],
        "weights": instance["weights"].tolist(),
        "r": {name: values.tolist() for name, values in instance["r"].items()},
        "account": instance["account"],
        "params": instance["params"],
    }
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _model_sizes(model):
    """Count a built model's variables by kind (None when nothing was built)."""
    if model is None:
        return {"integer": None, "binary": None, "tranche": None}
    from pyomo.environ import Var

    integer = binary = 0
    for var in model.component_data_objects(Var, descend_into=True):
        if var.is_binary():
            binary += 1
        elif var.is_integer():
            integer += 1
    tranche = len(model.e) if model.component("e") is not None else 0
    return {"integer": integer, "binary": binary, "tranche": tranche}


def solve_instance(instance, run_dir, time_limit=HANG_GUARD_S):
    """Drive one instance through :class:`LatencySolve` and time it.

    Parameters
    ----------
    instance : dict
        A :func:`make_instance` result.
    run_dir : str
        The node context's run directory (the doorway writes nothing).
    time_limit : float
        The HiGHS ``time_limit`` hang guard, seconds.

    Returns
    -------
    dict
        ``seed``, ``digest``, ``termination`` (None when the solve itself
        raised), ``error`` (the doorway's refusal as ``"Type: message"``,
        else None), ``wall_seconds``, ``solve_seconds``, ``variables``,
        ``constraints``, ``integer_variables`` (general integers),
        ``binary_variables``, ``tranche_variables``, ``tranches`` (``K_i``
        in name order), ``cvar`` (the exact recompute), ``cvar_limit``,
        ``cvar_binding`` (see :data:`CVAR_BINDING_DOLLARS`), ``carried_wealth``,
        ``n_held_before``, ``n_held``, ``n_traded``, ``target``, ``trades``,
        ``tranche_split`` and ``objective``.
    """
    params = {**instance["params"], "solver_options": {"time_limit": float(time_limit)}}
    node = LatencySolve("latency", params)
    ctx = NodeContext(name="latency", asof=ASOF, run_dir=str(run_dir))
    out, error = None, None
    started = time.perf_counter()
    try:
        out = node.run(ctx, instance)
    except (RuntimeError, AssertionError, ValueError) as exc:
        # The doorway's refusals (non-optimal termination, exact-recompute
        # violation, malformed input), recorded so a batch reports every
        # failure; every caller fails on a non-None error.
        error = f"{type(exc).__name__}: {exc}"
    wall = time.perf_counter() - started
    record = node.solve_record
    sizes = _model_sizes(node.last_model)
    cvar_limit = instance["params"]["cvar_limit"]
    cvar = None if out is None else out["metrics"]["cvar"]
    return {
        "seed": instance["seed"],
        "digest": instance["digest"],
        "termination": None if record is None else record.termination,
        "error": error,
        "wall_seconds": wall,
        "solve_seconds": None if record is None else record.seconds,
        "variables": None if record is None else record.variables,
        "constraints": None if record is None else record.constraints,
        "integer_variables": sizes["integer"],
        "binary_variables": sizes["binary"],
        "tranche_variables": sizes["tranche"],
        "tranches": [instance["tranches"][name] for name in instance["names"]],
        "cvar": cvar,
        "cvar_limit": cvar_limit,
        "cvar_binding": cvar is not None and cvar >= cvar_limit - CVAR_BINDING_DOLLARS,
        "carried_wealth": instance["account"].get("carried_wealth", 0.0),
        "n_held_before": sum(1 for row in instance["rows"].values() if row["held"]),
        "n_held": None if out is None else out["metrics"]["n_held"],
        "n_traded": None if out is None else out["metrics"]["n_traded"],
        "target": None if out is None else out["target"],
        "trades": None if out is None else out["trades"],
        "tranche_split": None if out is None else out["metrics"]["tranches"],
        "objective": None if record is None else record.objective,
    }


def solved(record):
    """Return whether a record is a clean optimal solve."""
    return record["error"] is None and record["termination"] == "optimal"


def _distribution(values):
    """Return min/median/p90/p99/max/mean of ``values``, rounded to 0.1 ms."""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return None
    points = {
        "min": arr.min(),
        "median": np.median(arr),
        "p90": np.percentile(arr, 90),
        "p99": np.percentile(arr, 99),
        "max": arr.max(),
        "mean": arr.mean(),
    }
    return {key: round(float(value), 4) for key, value in points.items()}


def _span(values):
    """Return min/median/max of integer counts (None entries skipped)."""
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    if arr.size == 0:
        return None
    return {"min": int(arr.min()), "median": float(np.median(arr)), "max": int(arr.max())}


def summarize(records):
    """Summarize a batch of :func:`solve_instance` records.

    Parameters
    ----------
    records : list of dict

    Returns
    -------
    dict
        ``n``, ``n_optimal``, ``failures`` (seed, termination, error of every
        record that is not a clean optimal solve), the ``wall_seconds`` and
        ``solve_seconds`` distributions, the size spans (``variables``,
        ``constraints``, ``tranche_variables``, ``integer_variables``,
        ``binary_variables``), ``n_cvar_binding``, ``budget`` and
        ``meets_budget`` (every solve optimal, end-to-end median and p99
        under budget).
    """
    failures = [
        {"seed": r["seed"], "termination": r["termination"], "error": r["error"]}
        for r in records
        if not solved(r)
    ]
    wall = _distribution([r["wall_seconds"] for r in records])
    solve = _distribution([r["solve_seconds"] for r in records if r["solve_seconds"] is not None])
    meets = (
        bool(records)
        and not failures
        and wall["median"] < MEDIAN_BUDGET_S
        and wall["p99"] < P99_BUDGET_S
    )
    sizes = {
        key: _span([r[key] for r in records])
        for key in (
            "variables",
            "constraints",
            "tranche_variables",
            "integer_variables",
            "binary_variables",
        )
    }
    return {
        "n": len(records),
        "n_optimal": len(records) - len(failures),
        "failures": failures,
        "wall_seconds": wall,
        "solve_seconds": solve,
        "sizes": sizes,
        "n_cvar_binding": sum(1 for r in records if r["cvar_binding"]),
        "budget": {"median_s": MEDIAN_BUDGET_S, "p99_s": P99_BUDGET_S, "on": "wall_seconds"},
        "meets_budget": meets,
    }


def describe(records, summary):
    """Render a batch as text: one line per instance, then the distributions.

    ``held`` reads names held before -> after the solve; ``cvar`` is the
    exact recompute in dollars, starred where the cap binds.

    Parameters
    ----------
    records : list of dict
    summary : dict
        :func:`summarize` of ``records``.

    Returns
    -------
    str
    """
    lines = [
        f"{'seed':>9} {'K_i':>26} {'tranche':>7} {'vars':>5} {'rows':>5} "
        f"{'wall_s':>7} {'solve_s':>7} {'held':>6} {'trd':>3} {'cvar':>7} termination"
    ]
    for r in records:
        held = f"{r['n_held_before']}->{_text(r['n_held'])}"
        cvar = f"{_text(r['cvar'], '.0f')}{'*' if r['cvar_binding'] else ''}"
        lines.append(
            f"{r['seed']:>9} {','.join(map(str, r['tranches'])):>26} "
            f"{_text(r['tranche_variables']):>7} {_text(r['variables']):>5} "
            f"{_text(r['constraints']):>5} {r['wall_seconds']:>7.3f} "
            f"{_text(r['solve_seconds'], '.3f'):>7} {held:>6} "
            f"{_text(r['n_traded']):>3} {cvar:>7} "
            f"{r['termination']}{'' if r['error'] is None else '  ' + r['error']}"
        )
    for key in ("wall_seconds", "solve_seconds"):
        lines.append(f"{key}: {json.dumps(summary[key], sort_keys=True)}")
    lines.append(f"sizes: {json.dumps(summary['sizes'], sort_keys=True)}")
    lines.append(
        f"optimal {summary['n_optimal']}/{summary['n']}, CVaR binding in "
        f"{summary['n_cvar_binding']}, meets budget: {summary['meets_budget']}"
    )
    return "\n".join(lines)


def _text(value, spec=""):
    """Format ``value`` with ``spec``, or ``-`` for None."""
    return "-" if value is None else format(value, spec)


def _warm_up(run_dir, time_limit):
    """Solve one untimed instance so no timed one pays imports or first-solve setup."""
    record = solve_instance(make_instance(WARM_UP_SEED), run_dir, time_limit)
    if not solved(record):
        raise RuntimeError(f"the warm-up solve failed: {record['termination']!r}, {record['error']}")


def run_benchmark(n_instances, base_seed=BASE_SEED, time_limit=HANG_GUARD_S, run_dir=None,
                  progress=None):
    """Solve ``n_instances`` consecutive seeds after one untimed warm-up.

    Parameters
    ----------
    n_instances : int
        How many instances, >= 1 (seeds ``base_seed .. base_seed + n - 1``).
    base_seed : int
        The first instance's seed.
    time_limit : float
        The HiGHS ``time_limit`` hang guard, seconds.
    run_dir : str, optional
        The node context's run directory; a temporary directory when None.
    progress : callable, optional
        Called with each record as it lands.

    Returns
    -------
    tuple
        ``(records, summary)``: the :func:`solve_instance` records in seed
        order and their :func:`summarize`.
    """
    if isinstance(n_instances, bool) or not isinstance(n_instances, int) or n_instances < 1:
        raise ValueError(f"n_instances must be an int >= 1, got {n_instances!r}")
    with tempfile.TemporaryDirectory() as scratch:
        where = scratch if run_dir is None else run_dir
        _warm_up(where, time_limit)
        records = []
        for j in range(n_instances):
            record = solve_instance(make_instance(base_seed + j), where, time_limit)
            records.append(record)
            if progress is not None:
                progress(record)
    return records, summarize(records)


def machine_info():
    """Return what the timings depend on: platform, CPUs, load and versions."""
    from importlib.metadata import PackageNotFoundError, version

    versions = {}
    for package in ("pyomo", "highspy", "numpy"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        **versions,
    }


def _require_this_checkout():
    """Refuse to time a dskit imported from anywhere but this checkout."""
    import dskit

    where = os.path.realpath(os.path.dirname(dskit.__file__))
    root = os.path.realpath(_REPO_ROOT)
    if os.path.commonpath([where, root]) != root:
        raise SystemExit(
            f"dskit was imported from {where}, not this checkout ({root}); run with "
            f"PYTHONPATH={root} so the doorway timed is the one beside this tool"
        )


def _progress_line(record):
    """Print one instance's outcome to stderr."""
    print(
        f"seed {record['seed']}: wall {record['wall_seconds']:.3f} s, solve "
        f"{_text(record['solve_seconds'], '.3f')} s, {record['termination']}"
        f"{'' if record['error'] is None else ', ' + record['error']}",
        file=sys.stderr,
        flush=True,
    )


def main(argv=None):
    """Run the benchmark from the command line; return the exit status."""
    parser = argparse.ArgumentParser(
        description="Time ScenarioUtilitySolve on seeded ADR-0188 formulation-B instances."
    )
    parser.add_argument("--n", type=int, default=100, help="instances to solve (default 100)")
    parser.add_argument("--seed", type=int, default=BASE_SEED, help="the first instance's seed")
    parser.add_argument(
        "--time-limit", type=float, default=HANG_GUARD_S, help="HiGHS time_limit hang guard, s"
    )
    parser.add_argument("--json", help="also write the report plus per-instance records here")
    args = parser.parse_args(argv)
    if args.n < 1:
        parser.error("--n must be >= 1")
    if not args.time_limit > 0.0:
        parser.error("--time-limit must be > 0")
    _require_this_checkout()
    records, summary = run_benchmark(args.n, args.seed, args.time_limit, progress=_progress_line)
    report = {"summary": summary, "machine": machine_info(), "base_seed": args.seed}
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({**report, "records": records}, fh, indent=1, sort_keys=True)
    return 0 if summary["meets_budget"] else 1


if __name__ == "__main__":
    sys.exit(main())
