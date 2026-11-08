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
                {**good, "held": [0., -99.5]}, {**good, "extra": 1}):
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
    # The same words from a solve that did not use its time are a defect, not a timeout: re-raised.
    fast = BookKellySizer("sizer", {"gamma": 2., "n_tangents": 32, "core": 2., "parity_tolerance": 1e-6,
                                    "solver_options": {"time_limit": 900}})

    def no_solution(self, solver, model):
        raise RuntimeError("A feasible solution was not found, so no solution can be loaded")
    small = {"units": [{"id": "a", "reserve": 10., "k_max": 3, "profits": [1., -1.]}], "held": [0., 0.],
             "funding": 100., "w0": 100., "cash_limit": 90., "wealth_lo": 1.}
    fast._solve = no_solution.__get__(fast)
    with pytest.raises(RuntimeError, match="no solution can be loaded"):
        fast.run(None, small)
    # With no limit declared a no-incumbent halt is never a timeout either.
    unlimited = BookKellySizer("sizer", {"gamma": 2., "n_tangents": 32, "core": 2., "parity_tolerance": 1e-6})
    unlimited._solve = no_solution.__get__(unlimited)
    with pytest.raises(RuntimeError, match="no solution can be loaded"):
        unlimited.run(None, small)


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
