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

__all__ = ["BookKellySizer", "MAX_COMPONENT_CORRELATION", "MAX_CORRELATION", "TAIL_DF", "component_loadings",
           "factor_correlations", "joint_atoms", "scenario_seed", "shrink_targets", "stressed", "student_t4_cdf",
           "tilt_weights"]

#: Degrees of freedom of the copula's Student t: the Kelly family's one constant (ADR-0299, the lowest with a
#: finite variance). :func:`student_t4_cdf` is this law's closed form, so the two are one decision.
TAIL_DF = 4
#: The most a name may load on the basket factor: a correlation of one would make a name the market itself and
#: leave its idiosyncratic draw no weight (and ``sqrt(1 - r^2)`` zero).
MAX_CORRELATION = .95
#: The most a correlation component's members may share beyond the market (their residual equicorrelation).
MAX_COMPONENT_CORRELATION = .9


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


def factor_correlations(returns, betas, names, components=()):
    """Return each name's correlation with the equal-weight basket of ``names``, from its beta and the volatilities.

    ``beta_a = r_a sigma_a / sigma_m`` for a regression on the basket, so ``r_a = beta_a sigma_m / sigma_a``;
    clipped to ``[0, MAX_CORRELATION]`` (a negative beta is treated as no loading: the copula has one factor and
    no short names). A name whose volatility is zero or not finite gets 0.

    With ``components`` (lists of names, the study's own correlation components), each component of two or more
    names also gets a RESIDUAL correlation: the mean pairwise correlation of its members' returns after the basket
    (``e_a = returns_a - beta_a x basket``), clipped to ``[0, MAX_COMPONENT_CORRELATION]``: an equicorrelation
    model of what the members share beyond the market, the loading squared of a second, component-level factor.

    Parameters
    ----------
    returns : dict
        ``name -> daily log returns`` over the window (``WeeklyOptionPortfolioStudy._returns``).
    betas : dict
        ``name -> beta`` on the basket of ``names`` (``_fit_betas``).
    names : sequence of str
        The basket (the names whose window is finite).
    components : sequence of sequence of str
        Optional groups of names (``WeeklyOptionPortfolioStudy._components``); singletons and names outside the
        basket add nothing.

    Returns
    -------
    dict
        ``correlations`` (``name -> correlation`` for every name in ``betas``), ``sigma_m`` (the basket's daily
        volatility, None when it is not finite), ``component_of`` (``name -> component index`` for the members of a
        component with at least two basket names) and ``component_correlations`` (``component index -> residual
        correlation``).
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
    component_of, component_correlations, raw = {}, {}, {}
    basket_names = set(names)
    for group in components:
        members = [s for s in group if s in basket_names and s in betas]
        if len(members) < 2 or not math.isfinite(sigma_m):
            continue
        residuals = np.array([returns[s]-betas[s]*basket for s in members])
        if not np.all(np.isfinite(residuals)) or np.any(residuals.std(axis=1) == 0):
            continue
        corr = np.corrcoef(residuals)
        pairs = corr[np.triu_indices(len(members), k=1)]
        rho = float(np.mean(pairs)) if np.all(np.isfinite(pairs)) else 0.
        index = len(component_correlations)
        # The clip at zero is a bias upward on a noisy mean, and the basket's own members make small baskets'
        # residuals mechanically negative (biased low): the raw mean is kept beside the applied value for the record.
        component_correlations[index] = float(min(MAX_COMPONENT_CORRELATION, max(0., rho)))
        raw[index] = rho
        for s in members:
            component_of[s] = index
    return {"correlations": out, "sigma_m": sigma_m if math.isfinite(sigma_m) else None,
            "component_of": component_of, "component_correlations": component_correlations,
            "component_correlations_raw": raw}


def stressed(trailing, short):
    """Return the larger of two loading sets, name by name (the trailing window's and a shorter one's).

    The shorter window answers a correlation that jumps within the quarter (a calm 60-session window before a
    crash would otherwise load the names lightly in the very weeks that test them). The larger of two noisy
    estimates is biased upward even when nothing changed: a deliberate stress, stated in the ADR, not an unbiased
    estimate. Component correlations are matched by the trailing set's component index where the short set has
    exactly the same members, else kept from the trailing set and listed in ``unmatched``.
    """
    out = {"correlations": {a: max(trailing["correlations"].get(a, 0.), short["correlations"].get(a, 0.))
                            for a in set(trailing["correlations"]) | set(short["correlations"])},
           "sigma_m": trailing["sigma_m"], "sigma_m_short": short["sigma_m"],
           "component_of": dict(trailing["component_of"]),
           "component_correlations": dict(trailing["component_correlations"]),
           "component_correlations_raw": dict(trailing.get("component_correlations_raw", {})),
           "unmatched": []}
    short_members = {}
    for name, index in short["component_of"].items():
        short_members.setdefault(index, set()).add(name)
    trailing_members = {}
    for name, index in trailing["component_of"].items():
        trailing_members.setdefault(index, set()).add(name)
    for index, members in sorted(trailing_members.items()):
        match = [j for j, m in short_members.items() if m == members]
        if match:
            out["component_correlations"][index] = max(trailing["component_correlations"][index],
                                                       short["component_correlations"][match[0]])
        else:
            out["unmatched"].append(index)
    return out


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


def shrink_targets(targets, reserves, share):
    """Pool each unit's mean per dollar of reserve toward the week's reserve-weighted pooled mean, by a declared share.

    ``m_u = t_u / R_u``, ``m_bar = sum t_u / sum R_u``, ``t_u' = R_u ((1 - share) m_u + share m_bar)``: the winner's
    curse of sizing on selected means is blunted by pooling the selected units' edges per reserve toward their
    common level. The share is DECLARED, not estimated: an empirical-Bayes weight would need the variance of the
    forecast mean's error, which the study does not have. Returns the shrunk targets and the pooled mean.
    """
    import numpy as np

    t, r = np.asarray(targets, dtype=float), np.asarray(reserves, dtype=float)
    if t.shape != r.shape or t.ndim != 1 or t.size == 0 or not np.all(r > 0) or not number_ok(share) or not 0 <= share < 1:
        raise ValueError("one positive reserve per target and a share in [0, 1)")
    pooled = float(t.sum()/r.sum())
    if share == 0:
        return t.copy(), pooled   # exactly the identity, not the identity up to an ulp
    return r*((1.-share)*(t/r)+share*pooled), pooled


def scenario_seed(seed, day):
    """Return the sampler's seed for one decision day: a hash of the declared seed and the date, deterministic."""
    digest = hashlib.sha256(f"{int(seed)}:{day}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def component_loadings(correlations, component_of, component_correlations):
    """Return each name's loading on its component factor, cut so that the market and component loadings together
    stay under ``MAX_CORRELATION`` (``r^2 + s^2 <= MAX_CORRELATION^2``).

    The residual correlation ``rho`` is measured on what the market leaves (variance ``1 - r_a^2`` of a standardised
    name), so the copula's pairwise correlation ``r_a r_b + s_a s_b`` matches the data's ``r_a r_b + rho sqrt((1 -
    r_a^2)(1 - r_b^2))`` when ``s_a = sqrt(rho (1 - r_a^2))`` (the first build used ``sqrt(rho)`` and overstated the
    residual dependence by ``1 / (1 - r^2)``; the amendment's review caught it).
    """
    out = {}
    for name, index in component_of.items():
        r = correlations.get(name, 0.)
        s = math.sqrt(max(0., component_correlations[index])*max(0., 1.-r*r))
        room = math.sqrt(max(0., MAX_CORRELATION**2-r*r))
        out[name] = (index, float(min(s, room)))
    return out


def joint_atoms(masses, symbol_of, correlations, n, seed, components=None):
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
    components : dict or None
        Optional ``name -> (component index, loading)`` (:func:`component_loadings`): a second factor per
        component, shared by its members, with ``r^2 + s^2 <= MAX_CORRELATION^2``; names absent load none.

    Returns
    -------
    dict
        ``atoms`` (``group id -> (n,) int array``), ``uniforms`` (``name -> (n,)``), ``factor`` (the common normal
        factor, ``(n,)``), ``mixing`` (the Student t mixing variable ``TAIL_DF / chi2``, ``(n,)``) and
        ``component_factors`` (``component index -> (n,)``).

    Raises
    ------
    ValueError
        A group names a symbol without a correlation, a correlation or loading is out of range, or ``n`` is below 2.
    """
    import numpy as np

    if type(n) is not int or n < 2:
        raise ValueError("at least two scenarios")
    names = sorted(correlations)
    if any(not number_ok(correlations[a]) or not 0 <= correlations[a] <= MAX_CORRELATION for a in names):
        raise ValueError("correlations must be numbers in [0, MAX_CORRELATION]")
    if any(symbol_of[g] not in correlations for g in masses):
        raise ValueError("every group's name needs a correlation")
    components = components or {}
    if any(a not in correlations or not number_ok(s) or s < 0 or correlations[a]**2+s*s > MAX_CORRELATION**2+1e-12
           for a, (_index, s) in components.items()):
        raise ValueError("component loadings must be nonnegative and keep r^2 + s^2 within MAX_CORRELATION^2")
    rng = np.random.default_rng(int(seed))
    factor = rng.standard_normal(n)
    mixing = TAIL_DF/rng.chisquare(TAIL_DF, n)
    noise = rng.standard_normal((len(names), n))
    indices = sorted({index for index, _s in components.values()})
    component_factors = {index: rng.standard_normal(n) for index in indices}
    root = np.sqrt(mixing)
    uniforms = {}
    for i, name in enumerate(names):
        r = correlations[name]
        index, s = components.get(name, (None, 0.))
        shared = r*factor+(s*component_factors[index] if index is not None else 0.)
        uniforms[name] = student_t4_cdf(root*(shared+math.sqrt(max(0., 1.-r*r-s*s))*noise[i]))
    atoms = {}
    for key, p in masses.items():
        cumulative = np.cumsum(np.asarray(p, dtype=float))
        cumulative[-1] = 1.   # float noise must not leave a draw past the last atom
        atoms[key] = np.minimum(np.searchsorted(cumulative, uniforms[symbol_of[key]], side="right"), len(p)-1)
    return {"atoms": atoms, "uniforms": uniforms, "factor": factor, "mixing": mixing,
            "component_factors": component_factors}


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
    ``wealth_max``, ``knots``, ``envelope_error``, ``core_error``, ``parity`` (the solver-versus-recompute
    mismatch and the tolerance it was held to, below) and the solve ``certificate``; or
    ``{"timeout": True, "incumbent": bool, "certificate"}`` when the solver stopped at its time limit (with or
    without an incumbent), which the study records as a skipped week; or ``{"no_incumbent": True, "status",
    "reason", "certificate"}`` when the stage-2 program has no solution to load before its limit (ADR-0300
    amendment, 2026-11-09): ``status`` is ``infeasible`` when the open books alone put a scenario's wealth below
    ``wealth_lo`` (the program is then infeasible at every count zero, and no solve runs) or the solver says
    infeasible, else ``no_incumbent``; the study trades nothing new that week and records it.

    The parity check (ADR-0300 amendment). The recorded ``value`` is always the exact envelope at the integer
    counts; the solver's objective must agree with it within ``parity_tolerance`` plus two terms derived from the
    solve, never a free allowance: ``feasibility`` (HiGHS's ``mip_feasibility_tolerance`` on the wealth and tangent
    rows, scaled by each row's magnitude, carried through the envelope's steepest live slope) and ``dropped`` (HiGHS
    drops every matrix entry at or below ``small_matrix_value``, 1e-9, when appsi loads the model, so a tangent
    whose slope is that small reads as ``U <= intercept``; the solver's utility can then sit below the envelope by
    at most ``envelope(W) - least dropped live intercept`` in that scenario, and never above it). The solver's
    objective may exceed the recompute by ``parity_tolerance + feasibility`` at most, and fall short of it by that
    plus ``dropped``; ``parity`` records the signed mismatch, the bound it was held to and both terms.

    Parameters
    ----------
    params : dict
        ``gamma`` (>= 1), ``n_tangents`` (int >= 2), ``core`` (> 1), ``parity_tolerance`` (>= 0: the declared part
        of the parity bound above), and the base's ``solver`` and ``solver_options``
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
    #: HiGHS's ``small_matrix_value``: entries at or below it are dropped when appsi loads the model, before any option
    #: the document passes reaches the solver (measured: a 1e-12 option leaves the 6.9e-10 slopes dropped), so a document
    #: cannot lower it; a larger declared value only widens the drop.
    _HIGHS_SMALL_MATRIX_VALUE = 1e-9
    #: Terminations that say the program has no feasible point.
    _INFEASIBLE = ("infeasible", "infeasibleOrUnbounded")

    def run(self, ctx, inputs):
        """Solve; a stage-2 program with nothing to load is a recorded outcome, never an error (ADR-0300 amendment).

        Open books that alone put a scenario below ``wealth_lo`` make the program infeasible at every count zero: no
        solve runs and the outcome is ``infeasible`` with the breach. A solve that ends with nothing to load is a
        timeout when it reached its declared time limit (``maxTimeLimit``, or 90% of the limit used), else
        ``infeasible`` or ``no_incumbent`` by the solver's termination.
        """
        import time

        units, profits, held, weights, funding, w0, cash, lo = self._checked(inputs)
        margin = funding+held-lo
        if bool((margin < 0).any()):
            certificate = {"solver": self.params.get("solver", DEFAULT_SOLVER), "status": "not_solved",
                           "termination": "wealth_floor_breached_at_zero", "objective": None, "bound": None,
                           "gap": None, "seconds": 0., "variables": None, "constraints": None}
            return {"selection": self._empty_week(
                "infeasible", certificate, margin,
                "the open books alone put wealth below wealth_lo in a scenario: infeasible at every count zero")}
        started = time.perf_counter()
        try:
            return super().run(ctx, inputs)
        except RuntimeError as exc:
            # A solver interface that raises instead of returning (appsi with load_solution on): the same rule as
            # ``extract``'s, with the termination unknown.
            if self._NO_INCUMBENT not in str(exc):
                raise
            certificate = {"solver": self.params.get("solver", DEFAULT_SOLVER), "status": "aborted",
                           "termination": "unknown", "objective": None, "bound": None, "gap": None,
                           "seconds": time.perf_counter()-started, "variables": None, "constraints": None}
            return {"selection": self._without_incumbent(certificate, margin)}

    def _solve(self, solver, model):
        """Solve without loading, then load only an incumbent.

        This follows ``TransportActionScore``'s precedent, so a solve with nothing to load returns its termination
        instead of raising.
        """
        from pyomo.environ import Var

        results = solver.solve(model, load_solutions=False)
        model._sizer["incumbent"] = bool(len(results.solution))
        if model._sizer["incumbent"]:
            model.solutions.load_from(results)
        else:
            for variable in model.component_data_objects(Var):
                variable.set_value(None)
        return results

    def _without_incumbent(self, certificate, margin):
        """Classify a solve with nothing to load: a timeout when it reached its declared limit, else a recorded week."""
        limit = self._solver_options().get("time_limit")   # the limit HiGHS was actually given
        if certificate["termination"] == "maxTimeLimit" or (
                number_ok(limit) and limit > 0 and certificate["seconds"] >= self._LIMIT_REACHED*limit):
            # The halt used its time: ADR-0300's timeout, recorded as a maxTimeLimit certificate as before.
            certificate = {**certificate, "status": "aborted", "termination": "maxTimeLimit"}
            return {"timeout": True, "incumbent": False, "certificate": certificate}
        if certificate["termination"] in self._INFEASIBLE:
            return self._empty_week("infeasible", certificate, margin,
                                    "the solver found the stage-2 program infeasible, although every count zero is "
                                    "feasible on exact arithmetic (likely a numerical verdict)")
        return self._empty_week("no_incumbent", certificate, margin,
                                "the solver returned no incumbent before its time limit, although every count zero "
                                "is feasible on exact arithmetic (likely a numerical failure)")

    @staticmethod
    def _empty_week(status, certificate, margin, reason):
        """Return a stage-2 week that sizes nothing new: its status, why, and the floor margin at every count zero."""
        return {"no_incumbent": True, "status": status, "certificate": certificate,
                "reason": {"text": reason, "floor_margin_at_zero": float(margin.min()),
                           "scenarios_below_floor_at_zero": int((margin < 0).sum())}}

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
        return units, profits, held, weights, funding, w0, cash, lo

    def build_model(self, inputs, params):
        import numpy as np
        from pyomo.environ import ConcreteModel, Constraint, NonNegativeIntegers, Objective, Reals, Var, maximize

        units, profits, held, weights, funding, w0, cash, lo = self._checked(inputs)
        if bool(np.any(funding+held < lo)):   # ``run`` records this week as infeasible before any build
            raise ValueError("the open books alone breach the wealth floor in a scenario")
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
                    "kmax": kmax, "w0": w0, "cash": cash, "lo": lo, "envelope": envelope, "live": live,
                    "margin": base-lo, "incumbent": True}
        return m

    def _parity(self, model, k, wealth, value, objective):
        """Return the parity record: the signed mismatch, the bound it is held to and the bound's two derived terms.

        Per scenario ``s`` (``W`` the exact wealth at the integer counts ``k``, ``k~`` the solver's counts, ``eps``
        the pinned ``mip_feasibility_tolerance``, ``sigma`` HiGHS's ``small_matrix_value``):

        * the solver's wealth is within ``D_s = eps max(1, |base_s| + sum_u |p_us| k~_u + |W_s|) + sum_u |p_us|
          |k~_u - k_u|`` of ``W_s`` (the wealth row's feasibility, relative to its magnitude, and the integrality
          residual, measured);
        * over ``J_s``, the tangents that are the least line somewhere in ``[W_s - D_s, W_s + D_s]``, the solver's
          utility is at most ``envelope(W_s) + B_s D_s + eps T_s`` (``B_s`` their largest slope, ``T_s`` the largest
          ``1 + |a_j| + b_j (W_s + D_s)``, the tangent row's magnitude): the ``feasibility`` term;
        * a tangent with ``b_j <= sigma`` is the row ``U <= a_j`` in the solver, so its utility may also sit lower,
          down to ``A_s``, the least such intercept among the scenario's live tangents: the ``dropped`` term
          ``max(0, envelope(W_s) - A_s)``, zero where none is dropped.

        Each term is weighted ``w0 x weight_s`` like the objective and summed. The declared ``parity_tolerance`` covers
        the float arithmetic of the two sums.
        """
        import numpy as np

        data, envelope = model._sizer, model._sizer["envelope"]
        options = self._solver_options()
        highs = self.params.get("solver", DEFAULT_SOLVER) == DEFAULT_SOLVER
        eps = float(options.get("mip_feasibility_tolerance", 1e-6)) if highs else 0.
        sigma = max(self._HIGHS_SMALL_MATRIX_VALUE, float(options.get("small_matrix_value", 0.))) if highs else 0.
        ids, profits = data["ids"], data["profits"]
        raw = {i: float(model.k[i].value or 0.) for i in ids}
        scale = np.abs(data["base"])+np.abs(wealth)+sum(np.abs(profits[i])*abs(raw[i]) for i in ids)
        drift = eps*np.maximum(1., scale)+sum(np.abs(profits[i])*abs(raw[i]-k[i]) for i in ids)
        exact = envelope.value(wealth)
        feasibility, dropped = np.zeros(len(wealth)), np.zeros(len(wealth))
        for s, (w, d) in enumerate(zip(wealth, drift)):
            near = envelope.live(w-d, w+d)
            rows = 1.+np.abs(envelope.intercept[near])+envelope.slope[near]*(w+d)
            feasibility[s] = float(envelope.slope[near].max())*d+eps*float(rows.max())
            flat = [j for j in data["live"][s] if envelope.slope[j] <= sigma]
            if flat:
                dropped[s] = max(0., float(exact[s]-envelope.intercept[flat].min()))
        scaled = data["w0"]*data["weights"]
        declared = float(self.params["parity_tolerance"])
        feasibility, dropped = float(scaled @ feasibility), float(scaled @ dropped)
        mismatch = float(objective-value)
        return {"mismatch": mismatch, "tolerance": declared+feasibility+(dropped if mismatch < 0 else 0.),
                "declared": declared, "feasibility": feasibility, "dropped": dropped,
                "feasibility_tolerance": eps, "small_matrix_value": sigma}

    def extract(self, model, results):
        import numpy as np

        record = self.solve_record
        certificate = {k: v for k, v in record.to_obj().items() if k != "binding"}
        if not model._sizer["incumbent"]:
            return {"selection": self._without_incumbent(certificate, model._sizer["margin"])}
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
        if not number_ok(record.objective):
            raise ValueError("sizer objective differs from the exact recompute of the envelope")
        parity = self._parity(model, k, wealth, value, record.objective)
        if abs(parity["mismatch"]) > parity["tolerance"]:
            raise ValueError("sizer objective differs from the exact recompute of the envelope "
                             f"(mismatch {parity['mismatch']:.6g}, tolerance {parity['tolerance']:.6g})")
        exact = float(data["w0"]*np.dot(data["weights"], tangent_utility(wealth, data["w0"], envelope.gamma)[0]))
        return {"selection": {
            "k": k, "reserve": float(reserve), "value": value, "exact": exact,
            "bound": record.bound, "gap": record.gap,
            "wealth_min": float(wealth.min()), "wealth_max": float(wealth.max()),
            "knots": int(len(envelope.knots)), "envelope_error": envelope.error, "core_error": envelope.core_error,
            "parity": parity, "certificate": certificate}}
