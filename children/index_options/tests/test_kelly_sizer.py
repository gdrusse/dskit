"""The sample-average Kelly sizer (ADR-0300): the copula sampler, the Student t law and the stage-2 program."""

import math

import numpy as np
import pytest

from index_options import kelly_sizer
from index_options.kelly_sizer import BookKellySizer, factor_correlations, joint_atoms, scenario_seed, student_t4_cdf


def test_the_t4_cdf_is_the_packages_student_tail_in_closed_form():
    """The vectorized closed form agrees with ``stats.student_t_sf``, the one owner of the Student tail."""
    from dskit.pipeline.stats import student_t_sf

    points = np.array([-8., -3., -1.5, -.25, 0., .4, 1., 2.5, 6.])
    values = student_t4_cdf(points)
    assert values.shape == points.shape
    for x, v in zip(points, values):
        assert v == pytest.approx(1.-student_t_sf(float(x), kelly_sizer.TAIL_DF), abs=1e-12)
    assert np.all(np.diff(values) > 0) and 0 < values[0] < .01 and .99 < values[-1] < 1


def _three_groups():
    masses = {"A:e1": [.2, .5, .3], "A:e2": [.1, .1, .4, .4], "B:e1": [.6, .4]}
    symbol_of = {"A:e1": "A", "A:e2": "A", "B:e1": "B"}
    return masses, symbol_of


def test_joint_atoms_reproduce_each_groups_forecast_and_a_names_groups_share_one_draw():
    """Every group's atoms are distributed as its masses; the groups of one name are comonotone; the seed pins the draw."""
    masses, symbol_of = _three_groups()
    n = 40000
    sample = joint_atoms(masses, symbol_of, {"A": .6, "B": 0.}, n, scenario_seed(7, "2021-01-04"))
    for key, p in masses.items():
        counts = np.bincount(sample["atoms"][key], minlength=len(p))/n
        assert counts == pytest.approx(p, abs=.012), key
    u = sample["uniforms"]["A"]
    for key in ("A:e1", "A:e2"):
        cumulative = np.cumsum(masses[key])
        atoms = sample["atoms"][key]
        lower = np.where(atoms > 0, cumulative[atoms-1], 0.)
        assert np.all((u >= lower) & (u < np.minimum(cumulative[atoms], 1.) + 1e-15)), "the atom is the step inverse at u"
    again = joint_atoms(masses, symbol_of, {"A": .6, "B": 0.}, n, scenario_seed(7, "2021-01-04"))
    assert all(np.array_equal(again["atoms"][k], sample["atoms"][k]) for k in masses)
    other = joint_atoms(masses, symbol_of, {"A": .6, "B": 0.}, n, scenario_seed(7, "2021-01-11"))
    assert any(not np.array_equal(other["atoms"][k], sample["atoms"][k]) for k in masses)


def test_the_loading_sets_the_dependence_between_names_and_the_tail_is_common():
    """At a loading of .9 two names' uniforms are strongly concordant; at 0 they are independent; both marginals stay
    uniform (the t copula's mixing variable is shared, so the marginals are the Student t's own)."""
    masses = {"A:e": [.5, .5], "B:e": [.5, .5]}
    symbol_of = {"A:e": "A", "B:e": "B"}
    n = 40000
    tight = joint_atoms(masses, symbol_of, {"A": .9, "B": .9}, n, 1)
    loose = joint_atoms(masses, symbol_of, {"A": 0., "B": 0.}, n, 1)
    rho_tight = np.corrcoef(tight["uniforms"]["A"], tight["uniforms"]["B"])[0, 1]
    rho_loose = np.corrcoef(loose["uniforms"]["A"], loose["uniforms"]["B"])[0, 1]
    assert rho_tight > .7 and abs(rho_loose) < .03
    for sample in (tight, loose):
        for name in ("A", "B"):
            u = sample["uniforms"][name]
            assert u.mean() == pytest.approx(.5, abs=.01) and u.var() == pytest.approx(1/12, abs=.003)
    with pytest.raises(ValueError):
        joint_atoms(masses, symbol_of, {"A": 1., "B": 0.}, n, 1)
    with pytest.raises(ValueError):
        joint_atoms(masses, symbol_of, {"A": .5}, n, 1)
    with pytest.raises(ValueError):
        joint_atoms(masses, symbol_of, {"A": .5, "B": .5}, 1, 1)


def test_factor_correlations_follow_the_beta_and_the_two_volatilities():
    rng = np.random.default_rng(3)
    market = rng.standard_normal(500)*.01
    returns = {"M": market, "H": 1.5*market+rng.standard_normal(500)*.005, "N": rng.standard_normal(500)*.02,
               "Z": np.zeros(500), "X": np.r_[market[:-1], np.nan]}
    names = ["M", "H", "N", "Z"]
    basket = np.mean([returns[s] for s in names], axis=0)
    betas = {s: float(np.cov(returns[s], basket, bias=True)[0, 1]/np.var(basket)) for s in names}
    betas["X"] = 1.
    out = factor_correlations(returns, betas, names)
    assert out["sigma_m"] == pytest.approx(float(np.std(basket)))
    for s in ("M", "H", "N"):
        expected = betas[s]*np.std(basket)/np.std(returns[s])
        assert out["correlations"][s] == pytest.approx(min(kelly_sizer.MAX_CORRELATION, max(0., expected)))
    assert out["correlations"]["H"] > out["correlations"]["N"]
    assert out["correlations"]["Z"] == 0. and out["correlations"]["X"] == 0.
    assert all(0 <= r <= kelly_sizer.MAX_CORRELATION for r in out["correlations"].values())
    assert out["component_of"] == {} and out["component_correlations"] == {}, "no components asked for"


def test_component_correlations_are_the_members_residual_equicorrelation_and_load_a_second_factor():
    from index_options.kelly_sizer import component_loadings

    rng = np.random.default_rng(5)
    market = rng.standard_normal(600)*.01
    sector = rng.standard_normal(600)*.008
    returns = {f"S{i}": market+sector+rng.standard_normal(600)*.004 for i in range(3)}
    returns.update({f"I{i}": market+rng.standard_normal(600)*.009 for i in range(3)})
    names = sorted(returns)
    basket = np.mean([returns[s] for s in names], axis=0)
    betas = {s: float(np.cov(returns[s], basket, bias=True)[0, 1]/np.var(basket)) for s in names}
    out = factor_correlations(returns, betas, names, [["S0", "S1", "S2"], ["I0", "I1", "I2"], ["Z9"]])
    assert set(out["component_of"]) == set(names) and len(out["component_correlations"]) == 2
    sector_rho = out["component_correlations"][out["component_of"]["S0"]]
    other_rho = out["component_correlations"][out["component_of"]["I0"]]
    assert .4 < sector_rho <= kelly_sizer.MAX_COMPONENT_CORRELATION and other_rho < .15, (sector_rho, other_rho)
    loads = component_loadings(out["correlations"], out["component_of"], out["component_correlations"])
    for name, (index, s) in loads.items():
        assert index == out["component_of"][name] and 0 <= s and out["correlations"][name]**2+s*s <= .95**2+1e-12
    r0 = out["correlations"]["S0"]
    assert loads["S0"][1] == pytest.approx(min(math.sqrt(sector_rho*(1.-r0*r0)), math.sqrt(.95**2-r0*r0)))
    # The copula's pairwise correlation r_a r_b + s_a s_b is the data's r_a r_b + rho sqrt((1 - r_a^2)(1 - r_b^2))
    # wherever the .95 ceiling does not cut the loading; where it does, the loading is the ceiling's room.
    for name in ("S0", "S1", "S2"):
        r, s = out["correlations"][name], loads[name][1]
        uncut, room = math.sqrt(sector_rho*(1-r*r)), math.sqrt(.95**2-r*r)
        assert s == pytest.approx(min(uncut, room)) and (s == pytest.approx(uncut) or s == pytest.approx(room))
    assert set(out["component_correlations_raw"]) == set(out["component_correlations"])
    assert out["component_correlations_raw"][out["component_of"]["S0"]] >= sector_rho-1e-12
    # The second factor raises the concordance of two members beyond what the market loading alone gives.
    masses = {f"{n}:e": [.5, .5] for n in ("S0", "S1", "I0")}
    symbol_of = {f"{n}:e": n for n in ("S0", "S1", "I0")}
    corr = {n: out["correlations"][n] for n in ("S0", "S1", "I0")}
    with_factor = joint_atoms(masses, symbol_of, corr, 40000, 3, {n: loads[n] for n in corr})
    without = joint_atoms(masses, symbol_of, corr, 40000, 3)
    rho = lambda sample, a, b: np.corrcoef(sample["uniforms"][a], sample["uniforms"][b])[0, 1]
    assert rho(with_factor, "S0", "S1") > rho(without, "S0", "S1")+.05
    assert abs(rho(with_factor, "S0", "I0")-rho(without, "S0", "I0")) < .05, "across components only the market"
    assert set(with_factor["component_factors"]) == {loads["S0"][0], loads["I0"][0]} and without["component_factors"] == {}
    with pytest.raises(ValueError):
        joint_atoms(masses, symbol_of, corr, 100, 3, {"S0": (0, 1.)})   # r^2 + s^2 past the ceiling


def test_stressed_loadings_take_the_larger_window_name_by_name():
    from index_options.kelly_sizer import stressed

    trailing = {"correlations": {"A": .3, "B": .6}, "sigma_m": .01, "component_of": {"A": 0, "B": 0},
                "component_correlations": {0: .2}}
    short = {"correlations": {"A": .8, "B": .4}, "sigma_m": .03, "component_of": {"A": 0, "B": 0},
             "component_correlations": {0: .5}}
    out = stressed(trailing, short)
    assert out["correlations"] == {"A": .8, "B": .6} and out["sigma_m"] == .01 and out["sigma_m_short"] == .03
    assert out["component_correlations"] == {0: .5} and out["component_of"] == {"A": 0, "B": 0}
    other = stressed(trailing, {**short, "component_of": {"A": 0}, "component_correlations": {0: .9}})
    assert other["component_correlations"] == {0: .2}, "a component with other members keeps the trailing value"
    assert other["unmatched"] == [0] and out["unmatched"] == []
    exact, _pooled = __import__("index_options.kelly_sizer", fromlist=["shrink_targets"]).shrink_targets(
        np.array([300.123456789, 100.987654321]), np.array([1000., 3000.]), 0.)
    assert np.array_equal(exact, np.array([300.123456789, 100.987654321])), "share 0 is exactly the identity"


def test_shrink_targets_pool_the_means_per_reserve_by_the_declared_share():
    from index_options.kelly_sizer import shrink_targets

    targets, reserves = np.array([300., 100.]), np.array([1000., 1000.])
    shrunk, pooled = shrink_targets(targets, reserves, .5)
    assert pooled == pytest.approx(.2) and shrunk == pytest.approx([250., 150.])
    none, _ = shrink_targets(targets, reserves, 0.)
    assert none == pytest.approx(targets)
    uneven, pooled2 = shrink_targets(np.array([300., 100.]), np.array([3000., 1000.]), 1-1e-9)
    assert pooled2 == pytest.approx(.1) and uneven == pytest.approx([300., 100.], rel=1e-6), "equal edges per reserve pool to themselves"
    for bad in ((targets, reserves, 1.), (targets, np.array([0., 1000.]), .5), (targets, reserves[:1], .5)):
        with pytest.raises(ValueError):
            shrink_targets(*bad)


def _sizer(gamma, **params):
    return BookKellySizer("sizer", {"gamma": gamma, "n_tangents": 32, "core": 2., "parity_tolerance": 1e-6, **params})


def _binary(gamma, k_max=50, cash=9500., lo=100., held=(0., 0.)):
    return _sizer(gamma).run(None, {
        "units": [{"id": "bet", "reserve": 400., "k_max": k_max, "profits": [100., -400.]}],
        "held": list(held), "weights": [.9, .1], "funding": 10000., "w0": 10000., "cash_limit": cash,
        "wealth_lo": lo})["selection"]


@pytest.mark.parametrize("gamma,expected", [(4., 4), (2., 7), (1., 12)])
def test_the_sizer_takes_the_exact_integer_crra_stake_of_a_binary_bet(gamma, expected):
    """+100 / -400 at probability .9 on 10,000: the integer CRRA stakes at f .25 / .5 / 1 are 4 / 7 / 12
    (``tangent_utility``'s own example), found here by the envelope and checked against the exact utility."""
    from dskit.pipeline.libs.pyomo import tangent_utility

    out = _binary(gamma)
    exact = [float(np.dot([.9, .1], tangent_utility(np.array([10000.+100.*k, 10000.-400.*k]), 10000., gamma)[0]))
             for k in range(25)]   # the stakes that keep the losing wealth positive (the sizer's floor does the same)
    best = int(np.argmax(exact))
    assert best == expected
    assert out["k"]["bet"] == best
    assert out["reserve"] == 400.*best
    assert out["exact"] == pytest.approx(10000.*exact[best], rel=1e-9)
    assert out["value"] >= out["exact"]-1e-9, "the envelope overestimates a concave utility"
    assert out["value"]-out["exact"] <= 10000.*out["envelope_error"]+1e-6
    assert out["wealth_min"] == 10000.-400.*best and out["wealth_max"] == 10000.+100.*best
    assert out["certificate"]["termination"] == "optimal" and out["gap"] == pytest.approx(0., abs=1e-6)


def test_the_cash_rule_and_the_wealth_floor_bound_the_count():
    assert _binary(1., cash=1000.)["k"]["bet"] == 2, "two units of 400 fit under a 1,000 cash limit"
    assert _binary(1., lo=9000.)["k"]["bet"] == 2, "a 9,000 floor allows a 1,000 loss: two units"
    assert _binary(1., k_max=1)["k"]["bet"] == 1
    assert _binary(1., cash=0.)["k"]["bet"] == 0


def test_a_unit_that_hedges_an_open_book_is_credited_in_the_joint_wealth():
    """A unit with a negative mean is not taken alone; against an open book that loses where it pays, it is."""
    unit = {"id": "hedge", "reserve": 100., "k_max": 20, "profits": [-30., 20.]}
    alone = _sizer(2.).run(None, {"units": [unit], "held": [0., 0.], "weights": [.5, .5], "funding": 10000.,
                                  "w0": 10000., "cash_limit": 9500., "wealth_lo": 100.})["selection"]
    hedged = _sizer(2.).run(None, {"units": [unit], "held": [0., -4000.], "weights": [.5, .5], "funding": 10000.,
                                   "w0": 6000., "cash_limit": 5500., "wealth_lo": 60.})["selection"]
    assert alone["k"]["hedge"] == 0 and hedged["k"]["hedge"] > 0
    assert hedged["wealth_min"] == pytest.approx(10000.-4000.+20.*hedged["k"]["hedge"])


def test_the_sizer_refuses_malformed_inputs_and_parameters():
    good = {"units": [{"id": "a", "reserve": 10., "k_max": 3, "profits": [1., -1.]}], "held": [0., 0.],
            "funding": 100., "w0": 100., "cash_limit": 90., "wealth_lo": 1.}
    node = _sizer(2.)
    for bad in ({**good, "units": []}, {**good, "units": [{**good["units"][0], "reserve": 0.}]},
                {**good, "units": [{**good["units"][0], "profits": [1.]}]}, {**good, "held": [0.]},
                {**good, "wealth_lo": 100.}, {**good, "cash_limit": -1.}, {**good, "weights": [.5, .6]},
                {**good, "extra": 1}):
        with pytest.raises(ValueError):
            node.run(None, bad)
    for bad in ({"gamma": .5}, {"n_tangents": 1}, {"core": 1.}, {"parity_tolerance": -1.}, {"other": 1}):
        assert BookKellySizer.validate_params({"gamma": 2., "n_tangents": 32, "core": 2., "parity_tolerance": 1e-6,
                                               **bad})
    assert not BookKellySizer.validate_params({"gamma": 2., "n_tangents": 32, "core": 2., "parity_tolerance": 1e-6})


def test_a_time_limit_before_any_incumbent_returns_a_timeout_certificate_not_an_error():
    node = BookKellySizer("sizer", {"gamma": 2., "n_tangents": 32, "core": 2., "parity_tolerance": 1e-6,
                                    "solver_options": {"time_limit": 1e-4}})
    rng = np.random.default_rng(0)
    out = node.run(None, {"units": [{"id": f"u{i}", "reserve": 100., "k_max": 20,
                                     "profits": (rng.standard_normal(1000)*40.+5.).tolist()} for i in range(6)],
                          "held": [0.]*1000, "funding": 10000., "w0": 10000., "cash_limit": 9500.,
                          "wealth_lo": 100.})["selection"]
    assert out["timeout"] is True and out["incumbent"] is False
    assert out["certificate"]["termination"] == "maxTimeLimit" and out["certificate"]["status"] == "aborted"
    assert out["certificate"]["objective"] is None and out["certificate"]["seconds"] >= 0
    assert set(out["certificate"]) == {"solver", "status", "termination", "objective", "bound", "gap", "seconds",
                                       "variables", "constraints"}
    # The same words from a solve that did not use its time are not a timeout: a recorded no-incumbent week (ADR-0300
    # amendment), never an error that loses the method's quarter.
    fast = BookKellySizer("sizer", {"gamma": 2., "n_tangents": 32, "core": 2., "parity_tolerance": 1e-6,
                                    "solver_options": {"time_limit": 900}})

    def no_solution(self, solver, model):
        raise RuntimeError("A feasible solution was not found, so no solution can be loaded")
    small = {"units": [{"id": "a", "reserve": 10., "k_max": 3, "profits": [1., -1.]}], "held": [0., 0.],
             "funding": 100., "w0": 100., "cash_limit": 90., "wealth_lo": 1.}
    fast._solve = no_solution.__get__(fast)
    out = fast.run(None, small)["selection"]
    assert out["no_incumbent"] is True and out["status"] == "no_incumbent" and "timeout" not in out
    assert out["certificate"]["termination"] == "unknown" and out["certificate"]["objective"] is None
    assert out["reason"]["scenarios_below_floor_at_zero"] == 0 and out["reason"]["floor_margin_at_zero"] == 99.
    # With no limit declared a no-incumbent halt is never a timeout either.
    unlimited = BookKellySizer("sizer", {"gamma": 2., "n_tangents": 32, "core": 2., "parity_tolerance": 1e-6})
    unlimited._solve = no_solution.__get__(unlimited)
    assert unlimited.run(None, small)["selection"]["status"] == "no_incumbent"
    # Any other solver error is still raised.

    def broken(self, solver, model):
        raise RuntimeError("the solver crashed")
    unlimited._solve = broken.__get__(unlimited)
    with pytest.raises(RuntimeError, match="crashed"):
        unlimited.run(None, small)


def test_a_solver_verdict_without_an_incumbent_is_classified_by_its_termination():
    """``infeasible`` when the solver says so, ``no_incumbent`` otherwise, a timeout when the limit was reached."""
    node = BookKellySizer("sizer", {"gamma": 2., "n_tangents": 32, "core": 2., "parity_tolerance": 1e-6,
                                    "solver_options": {"time_limit": 10.}})
    margin = np.array([5., 9.])
    certificate = {"solver": "appsi_highs", "status": "warning", "termination": "infeasible", "objective": None,
                   "bound": None, "gap": None, "seconds": .2, "variables": 7, "constraints": 9}
    assert node._without_incumbent(certificate, margin)["status"] == "infeasible"
    assert node._without_incumbent({**certificate, "termination": "error"}, margin)["status"] == "no_incumbent"
    late = node._without_incumbent({**certificate, "termination": "error", "seconds": 9.5}, margin)
    assert late["timeout"] is True and late["certificate"]["termination"] == "maxTimeLimit"


def test_a_program_highs_finds_infeasible_is_a_recorded_infeasible_week_end_to_end():
    """Real HiGHS through ``_solve`` (load_solutions off): a stage-2 program with no feasible point returns its
    ``infeasible`` termination as a recorded week, never appsi's RuntimeError. The program is made infeasible by a
    subclass row k >= 5 against k_max 3 (the sizer's own program is feasible at every count zero)."""
    from pyomo.environ import Constraint

    class Forced(BookKellySizer):
        def build_model(self, inputs, params):
            m = super().build_model(inputs, params)
            m.forced = Constraint(expr=m.k["a"] >= 5)
            return m

    node = Forced("sizer", {"gamma": 2., "n_tangents": 32, "core": 2., "parity_tolerance": 1e-6,
                            "solver_options": {"time_limit": 900}})
    out = node.run(None, {"units": [{"id": "a", "reserve": 10., "k_max": 3, "profits": [1., -1.]}],
                          "held": [0., 0.], "funding": 100., "w0": 100., "cash_limit": 90.,
                          "wealth_lo": 1.})["selection"]
    assert out["no_incumbent"] is True and out["status"] == "infeasible" and "timeout" not in out and "k" not in out
    assert out["certificate"]["termination"] == "infeasible" and out["certificate"]["objective"] is None
    assert out["reason"]["scenarios_below_floor_at_zero"] == 0 and "likely a numerical verdict" in out["reason"]["text"]


def test_open_books_that_alone_breach_the_floor_make_an_infeasible_week_not_an_error():
    """At every count zero the open books put a scenario below wealth_lo: no solve runs, the week is recorded."""
    out = _sizer(2.).run(None, {"units": [{"id": "a", "reserve": 10., "k_max": 3, "profits": [1., -1.]}],
                                "held": [0., -99.5], "funding": 100., "w0": 100., "cash_limit": 90.,
                                "wealth_lo": 1.})["selection"]
    assert out["no_incumbent"] is True and out["status"] == "infeasible" and "k" not in out
    assert out["reason"]["scenarios_below_floor_at_zero"] == 1
    assert out["reason"]["floor_margin_at_zero"] == pytest.approx(-.5)
    assert out["certificate"]["status"] == "not_solved" and out["certificate"]["objective"] is None
    assert out["certificate"]["termination"] == "wealth_floor_breached_at_zero"


def _gme_week(scale=30000., n=1000, seed=0):
    """A GME-scale week on a 10,000 account: three units whose payoffs reach several times ``scale`` in the tails,
    and open books with heavy-tailed P&L of the same order (the 2021Q1 failure's magnitudes)."""
    rng = np.random.default_rng(seed)
    units = []
    for u in range(3):
        credit = float(rng.uniform(50, 400))*scale/1e3
        tail = rng.random(n) < .08
        loss = np.where(tail, rng.uniform(0, 1, n)*scale, rng.uniform(0, .2, n)*credit)
        if u % 2:
            loss = -np.where(rng.random(n) < .05, rng.uniform(0, 3, n)*scale, -rng.uniform(.5, 1, n)*credit)
        units.append({"id": f"u{u}", "reserve": scale/200, "k_max": 200, "profits": (credit-loss).tolist()})
    held = (rng.standard_t(4, n)*scale/5).clip(-2000, None)
    return {"units": units, "held": held.tolist(), "funding": 12000., "w0": 9910., "cash_limit": 8000.,
            "wealth_lo": 99.1}


def test_the_parity_bound_accepts_a_gme_scale_week_the_plain_tolerance_refused():
    """Payoffs of tens of thousands on a 10,000 account carry scenario wealth past 400 w0, where the CRRA tangents'
    slopes fall below HiGHS's small_matrix_value (1e-9) and are dropped: the solver's utility sits under the envelope
    there. The derived bound accounts for exactly that (the dropped term), the record says so, and the recorded value
    is the exact envelope at the counts."""
    node = _sizer(4., parity_tolerance=1e-3)
    week = _gme_week()
    out = node.run(None, week)["selection"]
    parity = out["parity"]
    assert -parity["mismatch"] > 1e-3, "the plain 1e-3 tolerance of 965b634b refuses this week"
    assert parity["mismatch"] == pytest.approx(node.solve_record.objective-out["value"], abs=1e-12)
    assert parity["dropped"] > 1e-3 and parity["feasibility"] < 1e-3 and parity["declared"] == 1e-3
    assert abs(parity["mismatch"]) <= parity["tolerance"] == pytest.approx(
        parity["declared"]+parity["feasibility"]+parity["dropped"])
    assert parity["small_matrix_value"] == 1e-9 and parity["feasibility_tolerance"] == 1e-9
    assert out["wealth_max"] > 400*week["w0"]
    from dskit.pipeline.libs.pyomo import TangentEnvelope

    base = np.asarray(week["held"])+week["funding"]
    wealth = base+sum(out["k"][u["id"]]*np.asarray(u["profits"]) for u in week["units"])
    high = base+sum(u["k_max"]*np.maximum(np.asarray(u["profits"]), 0.) for u in week["units"])
    envelope = TangentEnvelope(week["w0"], 4., week["wealth_lo"], float(max(week["w0"], high.max())), 32, 2.)
    assert out["value"] == pytest.approx(week["w0"]*float(np.mean(envelope.value(wealth))), abs=1e-9), \
        "the recorded value is the exact recompute, never the solver's number"


def test_the_parity_bound_still_refuses_a_wrong_objective():
    """A solver objective off by a dollar is refused; one above the envelope is refused even inside the dropped term,
    because dropping a slope can only lower the solver's utility."""
    import dataclasses

    class Shifted(BookKellySizer):
        shift = 0.

        def extract(self, model, results):
            if self.shift:
                self.solve_record = dataclasses.replace(self.solve_record,
                                                        objective=self.solve_record.objective+self.shift)
            return super().extract(model, results)

    params = {"gamma": 4., "n_tangents": 32, "core": 2., "parity_tolerance": 1e-3}
    node = Shifted("sizer", params)
    week = _gme_week()
    clean = node.run(None, week)["selection"]["parity"]
    # (A shortfall below the bound is no longer refused: ADR-0300 amendment 2, tested below.)
    for shift in (1.+clean["dropped"], clean["dropped"]/2-clean["mismatch"]):
        node.shift = shift
        with pytest.raises(ValueError, match="sizer objective differs from the exact recompute"):
            node.run(None, week)
    node.shift = 0.
    assert Shifted("sizer", params).run(None, week)["selection"]["parity"] == clean
    small = {"units": [{"id": "bet", "reserve": 400., "k_max": 50, "profits": [100., -400.]}], "held": [0., 0.],
             "weights": [.9, .1], "funding": 10000., "w0": 10000., "cash_limit": 9500., "wealth_lo": 100.}
    node = Shifted("sizer", {**params, "parity_tolerance": 1e-6})
    node.shift = 1e-3
    with pytest.raises(ValueError, match="sizer objective differs"):
        node.run(None, small)


def test_an_under_claim_beyond_the_bound_trades_the_counts_at_the_exact_value_and_says_uncertified():
    """ADR-0300 amendment 2: a solver objective BELOW the exact recompute of its own feasible integer counts by more
    than the bound is not an error: the counts are worth the recompute, which is recorded, and the week is marked
    uncertified (parity status and certificate), never certified."""
    import dataclasses

    class Shifted(BookKellySizer):
        shift = 0.

        def extract(self, model, results):
            if self.shift:
                self.solve_record = dataclasses.replace(self.solve_record,
                                                        objective=self.solve_record.objective+self.shift)
            return super().extract(model, results)

    node = Shifted("sizer", {"gamma": 4., "n_tangents": 32, "core": 2., "parity_tolerance": 1e-3})
    week = _gme_week()
    clean = node.run(None, week)["selection"]
    assert "status" not in clean["parity"] and "uncertified" not in clean["certificate"]
    shift = -1.-clean["parity"]["dropped"]-clean["parity"]["feasibility"]
    node.shift = shift
    out = node.run(None, week)["selection"]
    assert out["k"] == clean["k"] and out["value"] == clean["value"], "the exact recompute of the same counts"
    parity = out["parity"]
    assert parity["status"] == "under_claim_uncertified" and parity["mismatch"] < -parity["tolerance"]
    assert {k: v for k, v in parity.items() if k not in ("status", "mismatch")} == {
        k: v for k, v in clean["parity"].items() if k != "mismatch"}
    assert out["certificate"]["uncertified"] is True
    assert out["certificate"]["objective"] == pytest.approx(clean["certificate"]["objective"]+shift)


def test_the_tilt_reaches_every_target_by_reweighting_and_never_moves_a_sample():
    from index_options.kelly_sizer import tilt_weights

    rng = np.random.default_rng(1)
    profits = np.vstack([np.where(rng.random(2000) < .9, 50., -450.),        # a short premium unit: rare large losses
                         rng.standard_normal(2000)*100.+10.,                 # a symmetric one
                         np.full(2000, 7.)])                                 # a constant one imposes no constraint
    targets = np.array([profits[0].mean()-20., 0., 7.])
    out = tilt_weights(profits, targets)
    w = out["weights"]
    assert w.shape == (2000,) and np.all(w >= 0) and w.sum() == pytest.approx(1.)
    assert out["converged"] and out["clamped"] == [False, False, False]
    assert out["means"][:2] == pytest.approx(targets[:2], abs=1e-6) and out["means"][2] == pytest.approx(7.)
    assert out["gap"] <= 1e-9
    assert (profits[0] @ w) == pytest.approx(targets[0], abs=1e-6)
    loss_mass = w[profits[0] < 0].sum()
    assert loss_mass > np.mean(profits[0] < 0), "a lower target puts more weight on the sampled losses"
    assert out["kl"] > 0 and out["effective_scenarios"] < 2000 and out["max_weight"] >= 1/2000
    same = tilt_weights(profits, profits.mean(axis=1))
    assert same["kl"] == pytest.approx(0., abs=1e-9) and same["effective_scenarios"] == pytest.approx(2000.)
    clamped = tilt_weights(profits[:1], np.array([-600.]))      # below the worst sample: no weights reach it
    assert clamped["clamped"] == [True] and clamped["targets"][0] > -450. and clamped["means"][0] == pytest.approx(
        clamped["targets"][0], abs=1e-6)
    with pytest.raises(ValueError):
        tilt_weights(profits[:1, :1], np.array([0.]))
    with pytest.raises(ValueError):
        tilt_weights(profits, np.array([0., 0.]))
    # Two units that are the same draw with inconsistent targets cannot both be reached: reported, not raised.
    twin = tilt_weights(np.vstack([profits[1], profits[1]]), np.array([0., 30.]))
    assert not twin["converged"] and twin["gap"] > 0 and twin["weights"].sum() == pytest.approx(1.)


def test_scenario_seed_is_deterministic_and_date_specific():
    assert scenario_seed(5, "2021-01-04") == scenario_seed(5, "2021-01-04")
    assert scenario_seed(5, "2021-01-04") != scenario_seed(5, "2021-01-11") != scenario_seed(6, "2021-01-11")
    assert 0 <= scenario_seed(0, "2019-10-01") < 2**64
