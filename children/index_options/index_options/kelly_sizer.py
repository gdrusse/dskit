"""Size the books a dollar formulation selected by a sample-average fractional-Kelly program (ADR-0300).

A ``sizer`` method of :class:`~index_options.ledger_studies.WeeklyOptionPortfolioStudy` solves its dollar
program unchanged (stage 1: the locked formulation's books and leg ratios) and then, in this module, sizes those
books by maximizing the sample-average CRRA utility of terminal wealth over sampled joint settlements (stage 2).
Selection and size are separate on purpose: the one-stage growth objective of ADR-0299 did not fit a real week,
and the dollar program's robustness tools (the W1 radius, the shock gate) stay the selectors.

What this module owns:

* :func:`student_t4_cdf`, the Student t cumulative law with four degrees of freedom in closed form, vectorized
  (the package's scalar owner, :func:`dskit.pipeline.stats.student_t_sf`, is the oracle its test holds it to).
* :func:`factor_correlations`, each name's correlation with the equal-weight basket implied by its beta and the
  two volatilities, clipped to ``[0, MAX_CORRELATION]``.
* :func:`joint_atoms`, ``n`` sampled joint settlements: a one-factor Student t copula (``TAIL_DF`` degrees of
  freedom; a common mixing variable gives every name the same heavy tail at once, which is the market-crash
  dependence the shock gate models) over the names, and for every group the atom of its own forecast grid that
  the name's uniform draw selects (the step inverse of the group's masses, so a scenario lands on an atom and the
  sampled law of each group IS its forecast; a name's groups share the draw, so a name's expiries are one risk,
  as ADR-0299's charge treats them).
* :func:`tilt_weights`, the minimum-relative-entropy scenario weights under which every unit's mean profit is the
  mean the method names (the forecast's, or the W1-worst one): a reweighting of the sampled outcomes, never a shift
  of them, so no profit leaves its structural range.
* :class:`BookKellySizer`, the stage-2 MILP: an integer count per unit, the cash rule, the wealth floor, and
  the tangent-plane envelope of the CRRA utility (``TangentEnvelope``, ADR-0299's knots) over the sampled
  wealths, with an exact post-solve recompute.

Why not :class:`~dskit.pipeline.libs.pyomo.ScenarioUtilitySolve` (the toolkit's Kelly doorway, used by
``EquityKellyMIO`` and ``KalshiBinaryKellyMIO``): it refuses more than ``HARD_N_SCENARIOS_CEILING`` (256)
scenarios, a ceiling measured for another program and well below the sample this sizer needs for a heavy-tailed
joint law; its instruments are shares at a price with a gross return, not units at a reserve with a dollar
profit; it halts on a time limit instead of returning a certificate; and it lays linear knots without ADR-0299's
core band. The envelope, the utility and the determinism pins are shared; the program is this module's own.
The nearest copula seam, ``crypto_trading.sizing.LatentCopula``, enumerates the ``2^n`` joint settlements of
binary markets under a Gaussian factor with a declared correlation; option settlements on a 13- to 25-atom grid
over up to 166 names need sampling, an estimated loading per name and a heavy-tailed factor, so it is not reused.
"""

import hashlib
import math

from dskit.pipeline.libs.pyomo import DEFAULT_SOLVER, PyomoSolve, TangentEnvelope, tangent_utility
from dskit.pipeline.records import number_ok

__all__ = ["BookKellySizer", "MAX_CORRELATION", "TAIL_DF", "factor_correlations", "joint_atoms", "scenario_seed",
           "student_t4_cdf", "tilt_weights"]

#: Degrees of freedom of the copula's Student t: the Kelly family's one constant (ADR-0299, the lowest with a
#: finite variance). :func:`student_t4_cdf` is this law's closed form, so the two are one decision.
TAIL_DF = 4
#: The most a name may load on the basket factor: a correlation of one would make a name the market itself and
#: leave its idiosyncratic draw no weight (and ``sqrt(1 - r^2)`` zero).
MAX_CORRELATION = .95


def student_t4_cdf(x):
    """Return ``P(T <= x)`` for a Student t with four degrees of freedom, elementwise.

    The closed form ``1/2 + x (x^2 + 6) / (2 (x^2 + 4)^(3/2))``, vectorized because the copula evaluates it at
    every name and scenario at once. Its test pins it to :func:`dskit.pipeline.stats.student_t_sf`, the package's
    scalar owner of the Student tail.

    Parameters
    ----------
    x : array_like
        Finite evaluation points.

    Returns
    -------
    numpy.ndarray
        Values in ``[0, 1]``, increasing in ``x``.
    """
    import numpy as np

    x = np.asarray(x, dtype=float)
    return .5+x*(x*x+6.)/(2.*(x*x+4.)**1.5)


def factor_correlations(returns, betas, names):
    """Return each name's correlation with the equal-weight basket of ``names``, from its beta and the volatilities.

    ``beta_a = r_a sigma_a / sigma_m`` for a regression on the basket, so ``r_a = beta_a sigma_m / sigma_a``;
    clipped to ``[0, MAX_CORRELATION]`` (a negative beta is treated as no loading: the copula has one factor and
    no short names). A name whose volatility is zero or not finite gets 0.

    Parameters
    ----------
    returns : dict
        ``name -> daily log returns`` over the window (``WeeklyOptionPortfolioStudy._returns``).
    betas : dict
        ``name -> beta`` on the basket of ``names`` (``_fit_betas``).
    names : sequence of str
        The basket (the names whose window is finite).

    Returns
    -------
    dict
        ``correlations`` (``name -> correlation`` for every name in ``betas``) and ``sigma_m`` (the basket's daily
        volatility, None when it is not finite).
    """
    import numpy as np

    basket = np.mean([returns[s] for s in names], axis=0) if names else np.array([math.nan])
    sigma_m = float(np.std(basket)) if np.all(np.isfinite(basket)) else math.nan
    out = {}
    for name, beta in betas.items():
        finite = np.all(np.isfinite(returns[name]))
        sigma_a = float(np.std(returns[name])) if finite else math.nan
        r = beta*sigma_m/sigma_a if math.isfinite(sigma_m) and math.isfinite(sigma_a) and sigma_a > 0 else 0.
        out[name] = float(min(MAX_CORRELATION, max(0., r))) if math.isfinite(r) else 0.
    return {"correlations": out, "sigma_m": sigma_m if math.isfinite(sigma_m) else None}


def tilt_weights(profits, targets, *, edge=1e-3, tolerance=1e-9):
    """Return scenario weights of least relative entropy to uniform under which each unit's mean profit is its target.

    An exponential tilt (the minimum-KL reweighting): ``w_s`` proportional to ``exp(lambda . x_s)`` with ``lambda``
    the minimizer of the convex dual ``log sum_s exp(lambda . x_s) - lambda . t``, whose gradient is the weighted mean
    less the target. Reweighting moves probability between the SAMPLED outcomes, so no unit's profit leaves its
    structural range (a shift of the samples would carry a winner above its credit and a loser below its reserve);
    a lower target puts more weight on the sampled losses, a higher one on the gains.

    Parameters
    ----------
    profits : array_like, shape (units, scenarios)
        Each unit's sampled profit per scenario.
    targets : array_like, shape (units,)
        The mean each unit must have under the weights.
    edge : float
        A target outside the sampled range (or within ``edge`` of its ends, as a share of the range) is clamped to
        that margin, since no weights reach it; the clamp is reported per unit. A unit whose samples are all equal
        imposes no constraint.
    tolerance : float
        The largest gap between a weighted mean and its (clamped) target, relative to the unit's range, for
        ``converged`` to be true; the weights are returned either way, with the gap, so a caller records it.

    Returns
    -------
    dict
        ``weights`` (``(scenarios,)``, nonnegative, summing to one), ``means`` (each unit's weighted mean),
        ``targets`` (as applied), ``clamped`` (one bool per unit), ``kl`` (the relative entropy to uniform),
        ``max_weight``, ``effective_scenarios`` (``1 / sum w^2``), ``converged`` and ``gap`` (the largest relative miss).

    Raises
    ------
    ValueError
        Malformed arrays.
    """
    import numpy as np
    from scipy.optimize import minimize
    from scipy.special import logsumexp

    x = np.asarray(profits, dtype=float)
    t = np.asarray(targets, dtype=float)
    if x.ndim != 2 or x.shape[1] < 2 or t.shape != (x.shape[0],) or not np.all(np.isfinite(x)) or not np.all(np.isfinite(t)):
        raise ValueError("profits must be a finite (units, scenarios) matrix and targets one finite number per unit")
    units, n = x.shape
    lo, hi = x.min(axis=1), x.max(axis=1)
    span = hi-lo
    live = span > 1e-12*np.maximum(1., np.abs(hi))
    applied = t.copy()
    applied[live] = np.clip(t[live], lo[live]+edge*span[live], hi[live]-edge*span[live])
    clamped = [bool(a != b) for a, b in zip(applied, t)]
    if not live.any():
        weights = np.full(n, 1./n)
    else:
        scale = span[live]
        z = (x[live]-applied[live, None])/scale[:, None]   # centred on the target: the dual's minimum is at zero mean

        def dual(lam):
            a = lam @ z
            m = logsumexp(a)
            w = np.exp(a-m)
            return m-np.log(n), w @ z.T
        best = minimize(dual, np.zeros(int(live.sum())), jac=True, method="L-BFGS-B",
                        options={"ftol": 1e-15, "gtol": 1e-12, "maxiter": 5000})
        lam = best.x
        for _ in range(50):   # Newton polish: the dual's Hessian is the weighted covariance of z, a few dimensions
            a = lam @ z
            w = np.exp(a-logsumexp(a))
            g = w @ z.T
            if np.max(np.abs(g)) <= 1e-13:
                break
            zw = z*w
            h = zw @ z.T-np.outer(g, g)
            step = np.linalg.solve(h+1e-12*np.eye(len(lam)), g)
            lam = lam-step
        a = lam @ z
        weights = np.exp(a-logsumexp(a))
    means = x @ weights
    gap = np.abs(means-applied)/np.where(live, span, 1.)
    converged = bool(np.all(gap[live] <= tolerance)) if live.any() else True
    return {"weights": weights, "means": means, "targets": applied, "clamped": clamped,
            "kl": float(np.sum(weights*np.log(np.maximum(weights, 1e-300)*n))),
            "max_weight": float(weights.max()), "effective_scenarios": float(1./np.sum(weights**2)),
            "converged": converged, "gap": float(gap[live].max()) if live.any() else 0.}


def scenario_seed(seed, day):
    """Return the sampler's seed for one decision day: a hash of the declared seed and the date, deterministic."""
    digest = hashlib.sha256(f"{int(seed)}:{day}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def joint_atoms(masses, symbol_of, correlations, n, seed):
    """Sample ``n`` joint settlements: for every group, the atom of its grid each scenario lands on.

    Parameters
    ----------
    masses : dict
        ``group id -> masses`` (the group's forecast on its prepared grid, summing to one).
    symbol_of : dict
        ``group id -> name``: the groups of one name share the name's draw.
    correlations : dict
        ``name -> correlation`` with the common factor, in ``[0, MAX_CORRELATION]``.
    n : int
        Scenarios, at least 2.
    seed : int
        The generator's seed (:func:`scenario_seed`).

    Returns
    -------
    dict
        ``atoms`` (``group id -> (n,) int array``), ``uniforms`` (``name -> (n,)``), ``factor`` (the common normal
        factor, ``(n,)``) and ``mixing`` (the Student t mixing variable ``TAIL_DF / chi2``, ``(n,)``).

    Raises
    ------
    ValueError
        A group names a symbol without a correlation, a correlation is out of range, or ``n`` is below 2.
    """
    import numpy as np

    if type(n) is not int or n < 2:
        raise ValueError("at least two scenarios")
    names = sorted(correlations)
    if any(not number_ok(correlations[a]) or not 0 <= correlations[a] <= MAX_CORRELATION for a in names):
        raise ValueError("correlations must be numbers in [0, MAX_CORRELATION]")
    if any(symbol_of[g] not in correlations for g in masses):
        raise ValueError("every group's name needs a correlation")
    rng = np.random.default_rng(int(seed))
    factor = rng.standard_normal(n)
    mixing = TAIL_DF/rng.chisquare(TAIL_DF, n)
    noise = rng.standard_normal((len(names), n))
    root = np.sqrt(mixing)
    uniforms = {}
    for i, name in enumerate(names):
        r = correlations[name]
        uniforms[name] = student_t4_cdf(root*(r*factor+math.sqrt(1.-r*r)*noise[i]))
    atoms = {}
    for key, p in masses.items():
        cumulative = np.cumsum(np.asarray(p, dtype=float))
        cumulative[-1] = 1.   # float noise must not leave a draw past the last atom
        atoms[key] = np.minimum(np.searchsorted(cumulative, uniforms[symbol_of[key]], side="right"), len(p)-1)
    return {"atoms": atoms, "uniforms": uniforms, "factor": factor, "mixing": mixing}


class BookKellySizer(PyomoSolve):
    """Size a few units by a sample-average fractional-Kelly program over their joint scenario profits.

    Inputs (one decision week): ``units``, a list of ``{"id", "reserve", "k_max", "profits"}`` (the reserve one
    unit commits, the depth-limited count, and the unit's terminal profit in each of the ``n`` scenarios, already
    recentred by the caller); ``held``, the open books' terminal profit and loss against the funding in each
    scenario (``(n,)``, zeros when none); ``funding`` (the account's funding), ``w0`` (the capital not yet
    committed: funding less open reserves, the Kelly wealth of ADR-0299), ``cash_limit`` (what the new units'
    reserves may total), ``wealth_lo`` (the floor no scenario wealth may fall below, ``0 < wealth_lo < w0``), and
    optional ``weights`` (scenario weights summing to one; equal when absent).

    The program: integer ``k_u`` in ``0..k_max``; ``W_s = funding + held_s + sum_u k_u profit_u,s``, bounded to
    the wealth the scenario can reach and never below ``wealth_lo``; ``U_s`` under the live tangents of the CRRA
    envelope (``gamma`` the reciprocal of the Kelly fraction; ``TangentEnvelope`` with ``n_tangents`` geometric
    knots, ``n_tangents`` more over the ``core`` band and ``w0`` itself, so cash is worth exactly zero);
    ``sum_u k_u reserve_u <= cash_limit``; maximize ``w0 x sum_s weight_s U_s`` (dollars to first order). No
    budget, name or component cap: the utility over the sampled joint tails prices the risk.

    Output: ``selection`` with ``k`` (``unit id -> count``), ``reserve``, ``value`` (the envelope objective),
    ``exact`` (the exact expected utility, scaled the same way), ``bound``, ``gap``, ``wealth_min``,
    ``wealth_max``, ``knots``, ``envelope_error``, ``core_error`` and the solve ``certificate``; or
    ``{"timeout": True, "incumbent": bool, "certificate"}`` when the solver stopped at its time limit (with or
    without an incumbent), which the study records as a skipped week.

    Parameters
    ----------
    params : dict
        ``gamma`` (>= 1), ``n_tangents`` (int >= 2), ``core`` (> 1), ``parity_tolerance`` (>= 0: the recomputed
        objective must agree with the solver's within it), and the base's ``solver`` and ``solver_options``
        (``time_limit`` in seconds applies verbatim). HiGHS determinism pins (one thread, seed 0, no relative gap,
        a tiny absolute one) sit under the document's options.

    Examples
    --------
    One unit, three scenarios, log utility::

        node = BookKellySizer("sizer", {"gamma": 1., "n_tangents": 32, "core": 2., "parity_tolerance": 1e-6})
        out = node.run(None, {"units": [{"id": "a", "reserve": 100., "k_max": 5, "profits": [20., 20., -80.]}],
                              "held": [0., 0., 0.], "funding": 1000., "w0": 1000., "cash_limit": 950.,
                              "wealth_lo": 10.})
        out["selection"]["k"]["a"]   # the growth-optimal whole count
    """

    role = "capital"
    outputs = ("selection",)
    _PARAMS = PyomoSolve._PARAMS+("gamma", "n_tangents", "core", "parity_tolerance")
    #: The ``BudgetedSelect`` and ``ScenarioUtilitySolve`` precedent: integer counts routinely tie, so the gap,
    #: thread races and RNG jitter would each flip which optimal vertex comes back. The absolute gap is in the
    #: objective's dollars-to-first-order units.
    #: ``mip_feasibility_tolerance`` is ADR-0299's: HiGHS's 1e-6 default leaves a variable under its tangent lines by the
    #: tolerance times the intercepts, which the exact recompute would then refuse.
    _HIGHS_PINS = {"threads": 1, "random_seed": 0, "mip_rel_gap": 0., "mip_abs_gap": 1e-7, "mip_feasibility_tolerance": 1e-9}
    #: appsi's words when a solve ends with nothing to load (a time limit before any incumbent, or an infeasible program).
    _NO_INCUMBENT = "no solution can be loaded"
    #: The share of the declared time limit a no-incumbent halt must have used to count as a timeout rather than a defect.
    _LIMIT_REACHED = .9

    def run(self, ctx, inputs):
        """Solve; a time limit reached before any incumbent is a timeout certificate, not an error.

        The program is always feasible at cash (every count zero), so the only solve that returns nothing to load is a
        halt before HiGHS found that point; it is recorded like a halt with an incumbent (``maxTimeLimit``) and the
        study skips the week.
        """
        import time

        started = time.perf_counter()
        try:
            return super().run(ctx, inputs)
        except RuntimeError as exc:
            # appsi's words are the same for an infeasible program, so the halt must also have used its time: a
            # declared limit that the solve reached (the program is feasible at cash, so a fast return is a defect).
            seconds = time.perf_counter()-started
            limit = self._solver_options().get("time_limit")   # the limit HiGHS was actually given
            if (self._NO_INCUMBENT not in str(exc) or not number_ok(limit) or limit <= 0
                    or seconds < self._LIMIT_REACHED*limit):
                raise
            certificate = {"solver": self.params.get("solver", DEFAULT_SOLVER), "status": "aborted",
                           "termination": "maxTimeLimit", "objective": None, "bound": None, "gap": None,
                           "seconds": seconds, "variables": None, "constraints": None}
            return {"selection": {"timeout": True, "incumbent": False, "certificate": certificate}}

    @classmethod
    def validate_params(cls, params):
        problems = super().validate_params(params)
        if not number_ok(params.get("gamma")) or params["gamma"] < 1:
            problems.append("gamma must be a finite number >= 1 (the reciprocal of the Kelly fraction)")
        if type(params.get("n_tangents")) is not int or params["n_tangents"] < 2:
            problems.append("n_tangents must be an int >= 2")
        if not number_ok(params.get("core")) or params["core"] <= 1:
            problems.append("core must be a finite number > 1")
        if not number_ok(params.get("parity_tolerance")) or params["parity_tolerance"] < 0:
            problems.append("parity_tolerance must be finite and nonnegative")
        return problems

    def _solver_options(self):
        options = super()._solver_options()
        if self.params.get("solver", DEFAULT_SOLVER) != DEFAULT_SOLVER:
            return options
        return {**self._HIGHS_PINS, **options}

    @staticmethod
    def _checked(inputs):
        """Validate the week's inputs and return them as arrays."""
        import numpy as np

        if (not isinstance(inputs, dict)
                or set(inputs)-{"weights"} != {"units", "held", "funding", "w0", "cash_limit", "wealth_lo"}):
            raise ValueError("sizer inputs are units, held, funding, w0, cash_limit, wealth_lo and optional weights")
        units = inputs["units"]
        if (not isinstance(units, list) or not units
                or any(not isinstance(u, dict) or set(u) != {"id", "reserve", "k_max", "profits"} for u in units)
                or len({u["id"] for u in units}) != len(units)
                or any(not isinstance(u["id"], str) or not number_ok(u["reserve"]) or u["reserve"] <= 0
                       or type(u["k_max"]) is not int or u["k_max"] < 0 for u in units)):
            raise ValueError("units need unique string ids, a positive reserve, an int k_max >= 0 and profits")
        held = np.asarray(inputs["held"], dtype=float)
        n = held.shape[0] if held.ndim == 1 else 0
        if n < 2 or not np.all(np.isfinite(held)):
            raise ValueError("held must be a finite vector of at least two scenarios")
        profits = {}
        for u in units:
            row = np.asarray(u["profits"], dtype=float)
            if row.shape != (n,) or not np.all(np.isfinite(row)):
                raise ValueError("every unit's profits must be finite and one per scenario")
            profits[u["id"]] = row
        weights = inputs.get("weights")
        weights = np.full(n, 1./n) if weights is None else np.asarray(weights, dtype=float)
        if (weights.shape != (n,) or not np.all(np.isfinite(weights)) or bool(np.any(weights < 0))
                or abs(float(weights.sum())-1.) > 1e-9):
            raise ValueError("weights must be nonnegative, one per scenario and sum to one")
        for key in ("funding", "w0", "cash_limit", "wealth_lo"):
            if not number_ok(inputs[key]):
                raise ValueError(key+" must be a finite number")
        funding, w0, cash, lo = (float(inputs[k]) for k in ("funding", "w0", "cash_limit", "wealth_lo"))
        if not 0 < lo < w0 or cash < 0:
            raise ValueError("needs 0 < wealth_lo < w0 and a nonnegative cash_limit")
        if bool(np.any(funding+held < lo)):
            raise ValueError("the open books alone breach the wealth floor in a scenario")
        return units, profits, held, weights, funding, w0, cash, lo

    def build_model(self, inputs, params):
        import numpy as np
        from pyomo.environ import ConcreteModel, Constraint, NonNegativeIntegers, Objective, Reals, Var, maximize

        units, profits, held, weights, funding, w0, cash, lo = self._checked(inputs)
        ids = [u["id"] for u in units]
        kmax = {u["id"]: u["k_max"] for u in units}
        reserve = {u["id"]: float(u["reserve"]) for u in units}
        base = funding+held
        n = base.shape[0]
        low = base+sum(kmax[i]*np.minimum(profits[i], 0.) for i in ids)
        high = base+sum(kmax[i]*np.maximum(profits[i], 0.) for i in ids)
        envelope = TangentEnvelope(w0, float(params["gamma"]), lo, max(w0, float(high.max())),
                                   int(params["n_tangents"]), float(params["core"]))
        m = ConcreteModel()
        m.units, m.scenarios = list(ids), list(range(n))
        m.k = Var(m.units, domain=NonNegativeIntegers, bounds=lambda m, i: (0, kmax[i]))
        m.W = Var(m.scenarios, domain=Reals, bounds=lambda m, s: (max(lo, float(low[s])), float(high[s])))
        m.U = Var(m.scenarios, domain=Reals)
        m.wealth = Constraint(m.scenarios, rule=lambda m, s:
                              m.W[s] == float(base[s])+sum(float(profits[i][s])*m.k[i] for i in ids))
        live = {s: envelope.live(max(lo, float(low[s])), float(high[s])) for s in range(n)}
        m.tangents = Constraint([(s, j) for s in range(n) for j in live[s]], rule=lambda m, s, j:
                                m.U[s] <= float(envelope.intercept[j])+float(envelope.slope[j])*m.W[s])
        m.cash = Constraint(expr=sum(reserve[i]*m.k[i] for i in ids) <= cash)
        m.objective = Objective(expr=w0*sum(float(weights[s])*m.U[s] for s in range(n)), sense=maximize)
        m._sizer = {"ids": ids, "profits": profits, "base": base, "weights": weights, "reserve": reserve,
                    "kmax": kmax, "w0": w0, "cash": cash, "lo": lo, "envelope": envelope}
        return m

    def extract(self, model, results):
        import numpy as np

        record = self.solve_record
        certificate = {k: v for k, v in record.to_obj().items() if k != "binding"}
        if record.termination == "maxTimeLimit":
            return {"selection": {"timeout": True, "incumbent": True, "certificate": certificate}}
        if record.termination != "optimal" or record.status not in ("ok", "warning"):
            raise ValueError(f"sizer solve ended {record.status}/{record.termination}")
        data = model._sizer
        tol = float(self.params["parity_tolerance"])
        k = {}
        for i in data["ids"]:
            raw = float(model.k[i].value or 0.)
            k[i] = int(round(raw))
            if abs(raw-k[i]) > 1e-6 or not 0 <= k[i] <= data["kmax"][i]:
                raise ValueError("sizer count came back fractional or out of range")
        reserve = sum(data["reserve"][i]*k[i] for i in data["ids"])
        if reserve > data["cash"]+tol:
            raise ValueError("sizer counts breach the cash rule on exact recompute")
        wealth = data["base"]+sum(k[i]*data["profits"][i] for i in data["ids"])
        if bool(np.any(wealth < data["lo"]-tol)):
            raise ValueError("sizer counts breach the wealth floor on exact recompute")
        envelope = data["envelope"]
        value = float(data["w0"]*np.dot(data["weights"], envelope.value(wealth)))
        if not number_ok(record.objective) or abs(record.objective-value) > tol:
            raise ValueError("sizer objective differs from the exact recompute of the envelope")
        exact = float(data["w0"]*np.dot(data["weights"], tangent_utility(wealth, data["w0"], envelope.gamma)[0]))
        return {"selection": {
            "k": k, "reserve": float(reserve), "value": value, "exact": exact,
            "bound": record.bound, "gap": record.gap,
            "wealth_min": float(wealth.min()), "wealth_max": float(wealth.max()),
            "knots": int(len(envelope.knots)), "envelope_error": envelope.error, "core_error": envelope.core_error,
            "certificate": certificate}}
