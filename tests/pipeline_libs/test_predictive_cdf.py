"""Independent numerical and temporal contracts for ADR-0189."""

import copy
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from dskit.pipeline.libs.predictive_cdf import (
    AdaptiveEmpiricalMLPBlendCDF, CalibratedCurve, ChronologicalCDFStudy,
    CDFHyperparameterStudy,
    CatBoostQuantileCDF,
    ConvexCurve, GridCurve, MixtureCurve,
    HorizonEmpiricalCDF, ScaledEmpiricalCDF, MonotoneCDF, QuantileCDF, MixtureMLPCDF,
    NGBoostCDF, OptionImpliedTransportCDF, PCAAugmentedCDF, QuantileForestCDF, SetMixtureCDF,
    SplineFlowCDF,
    BetaTransformedPoolCDF, DynamicPITRecalibratedCDF, SemiparametricGPDTailCDF,
    TailConstrainedQuantileBlendCDF,
    DecisionWeightedMixtureMLPCDF, DecisionWeightedMonotoneCDF,
)
from dskit.pipeline.libs import predictive_cdf


def _decision_context(identity=("SPY", "2020-01-02", "2020-02-21")):
    return {"identity": list(identity), "thresholds": [-1., .5],
            "weights": [.5, .5], "intervals": [[-1., -.5], [.2, .5]],
            "status": "eligible",
            "clock": "after_date_close_indicative_not_executable",
            "provenance_sha256": "a"*64}


def test_decision_context_binds_provenance_identity_and_refuses_executable_clock():
    context = _decision_context()
    inventory = predictive_cdf._decision_threshold_inventory([context])
    np.testing.assert_allclose(inventory[0][0], [-1., .5])
    bad = dict(context, provenance_sha256="bad")
    with pytest.raises(ValueError, match="provenance"):
        predictive_cdf._decision_threshold_inventory([bad])
    bad = dict(context, clock="intraday_executable")
    with pytest.raises(ValueError, match="clock"):
        predictive_cdf._decision_threshold_inventory([bad])


def test_decision_weighted_lightgbm_curve_predicts_directly_at_row_strikes():
    model = DecisionWeightedMonotoneCDF(
        thresholds=[-2., 0., 2.], trees=2, leaves=2, min_child=1, threads=1)

    class LogisticCutoff:
        @staticmethod
        def predict_proba(x):
            p = 1/(1+np.exp(-x[:, -1]))
            return np.column_stack([1-p, p])

    model.model = LogisticCutoff()
    context = [_decision_context()]
    curve = model.curve_decision_context(np.array([[3., 4.]]), context)
    for cutoff in context[0]["thresholds"]:
        assert curve.cdf([[cutoff]])[0, 0] == pytest.approx(
            1/(1+np.exp(-cutoff)), abs=1e-12)


def test_decision_weighted_mlp_requires_context_and_trains_analytic_cdf():
    model = DecisionWeightedMixtureMLPCDF(
        components=1, hidden=[3], epochs=1, batch_size=2, seeds=[3],
        device="cpu", deterministic=True, decision_weight=1.,
        global_cdf_weight=.25)
    x = np.array([[0.], [1.]])
    y = np.array([-.2, .3])
    with pytest.raises(ValueError, match="context"):
        model.fit(x, y, x, y)
    contexts = [_decision_context(("SPY", f"2020-01-0{i+2}", "2020-02-21"))
                for i in range(2)]
    model.fit_decision_context(contexts, contexts).fit(x, y, x, y)
    assert np.isfinite(model.curve(x).cdf([[-1., .5], [-1., .5]])).all()


def test_threshold_audit_scores_fixed_intervals_and_refuses_post_choice_weights():
    CDFThresholdAudit = predictive_cdf.CDFThresholdAudit
    grid = np.linspace(80., 120., 401)
    uniform = np.clip((grid - 80.) / 40., 0., 1.)
    intervals = [(85., 95.), (105., 115.)]
    audit = CDFThresholdAudit(grid, intervals, floor=.1)
    assert np.all(audit.weights > 0)
    assert audit.strike_brier(uniform, 90., 100.) == pytest.approx(.25**2)
    assert audit.expected_spread_loss(uniform, 85., 95., "put") == pytest.approx(2.5)
    assert audit.expected_spread_loss(uniform, 105., 115., "call") == pytest.approx(2.5)
    assert audit.weighted_crps(uniform, 100.) < audit.weighted_crps(np.clip(uniform+.2, 0, 1), 100.)
    with pytest.raises(ValueError, match="grid|interval"):
        CDFThresholdAudit(grid, [(79., 95.)], floor=.1)


def test_gridcurve_atom_is_not_smeared_in_log_price_spread_integral():
    from scipy.integrate import quad

    prices = np.array([9., 10., 11., 11., 16., 17.])
    curve = GridCurve(np.log(prices / 13.)[None, :],
                      [[0., 0., 0., .5, 1., 1.]])
    audit = predictive_cdf.CDFThresholdAudit(
        np.arange(9., 17.5, .5), [(10., 12.), (14., 15.)], floor=.1)
    put = audit.gridcurve_log_spread_loss(curve, 0, 10., 12., "put", 13., 1.)
    call = audit.gridcurve_log_spread_loss(curve, 0, 14., 15., "call", 13., 1.)
    reference_put = quad(lambda x: curve.cdf([np.log(x/13.)])[0, 0],
                         10., 12., points=[11.], epsabs=1e-11)[0]
    reference_call = quad(lambda x: 1.-curve.cdf([np.log(x/13.)])[0, 0],
                          14., 15., epsabs=1e-11)[0]
    assert put == pytest.approx(reference_put, abs=1e-10)
    assert call == pytest.approx(reference_call, abs=1e-10)
    assert put+call < .75
    sampled = curve.cdf(np.log(audit.grid/13.))[0]
    assert audit.expected_spread_loss(sampled, 10., 12., "put") + \
           audit.expected_spread_loss(sampled, 14., 15., "call") > .75


def test_clipped_cdf_grid_preserves_interior_cdf_and_w1_budget():
    grid = predictive_cdf.DiscreteCDFGrid([0., 1., 2.], [.2, .8, .9])
    np.testing.assert_allclose(grid.masses, [.2, .6, .2])
    np.testing.assert_allclose(np.cumsum(grid.masses), [.2, .8, 1.])
    centered = predictive_cdf.DiscreteCDFGrid([0., 1., 2.], [0., 1., 1.])
    loss = np.array([1., 0., 1.])
    assert centered.worst_expected_loss(loss, radius=0., spot=1.) == pytest.approx(0.)
    assert centered.worst_expected_loss(loss, radius=.5, spot=1.) == pytest.approx(.5)
    assert centered.worst_expected_loss(loss, radius=1., spot=1.) == pytest.approx(1.)
    with pytest.raises(ValueError, match="CDF"):
        predictive_cdf.DiscreteCDFGrid([0., 1., 2.], [.2, .1, .9])


def test_w1_candidate_selection_uses_one_ball_and_no_trade():
    grid = predictive_cdf.DiscreteCDFGrid([0., 1., 2.], [0., 1., 1.])
    candidates = [
        {"id": "fragile", "net_credit": .4, "loss": [1., 0., 1.]},
        {"id": "stable", "net_credit": .2, "loss": [0., 0., 0.]},
    ]
    assert grid.choose(candidates, radius=0., spot=1.)["id"] == "fragile"
    assert grid.choose(candidates, radius=.5, spot=1.)["id"] == "stable"
    assert grid.choose(candidates[:1], radius=.5, spot=1.)["id"] is None
    assert grid.choose([{"id": "tie", "net_credit": 0., "loss": [0., 0., 0.]}],
                       radius=0., spot=1.)["id"] is None


def test_frozen_curve_archive_restores_exact_grid_rows(tmp_path):
    source = GridCurve(np.array([[0., 1.], [1., 2.]]),
                       np.array([[0., 1.], [0., 1.]]))
    path = tmp_path/"curves.npz"
    np.savez_compressed(path, row_index=np.array([0, 1]), **source._arrays())
    with np.load(path, allow_pickle=False) as archive:
        restored = CDFHyperparameterStudy.restore_curve(archive, [1])
    assert restored.cdf([1.25])[0, 0] == pytest.approx(.25)


class _StaticGridEstimator:
    def __init__(self, shift):
        self.shift = shift

    def _validate_x(self, x):
        assert np.asarray(x).ndim == 2

    def fit(self, x, y, cal_x, cal_y):
        return self

    def curve(self, x):
        values = np.tile(np.linspace(-2, 2, 21) + self.shift, (len(x), 1))
        return GridCurve(values, np.linspace(0, 1, 21))


def _dynamic(**changes):
    params = dict(
        endpoint_class='dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
        endpoint_params={'horizon_index': 0, 'reference_index': 1, 'knots': 41},
        knots=41, map_knots=11, half_life_days=30, lookback_days=365,
        prior_strength=5, minimum_rows=4)
    model = DynamicPITRecalibratedCDF(**{**params, **changes})
    model.endpoint = _StaticGridEstimator(0)
    return model


def test_dynamic_pit_recalibration_is_delayed_same_date_and_future_invariant():
    x = np.ones((8, 2))
    fit_y = np.linspace(-1.5, 1.5, len(x))
    cal_x, cal_y = x[:4], np.array([-1.5, -1., -.5, 0.])
    context = pd.DataFrame({
        'date': ['2019-12-01', '2019-12-02', '2019-12-03', '2019-12-04'],
        'end': ['2019-12-05'] * 4})
    dates = np.array(['2020-01-02', '2020-01-02', '2020-02-10', '2020-03-20'])
    ends = np.array(['2020-01-31', '2020-01-31', '2020-03-01', '2020-04-15'])
    outcomes = np.array([1.5, 1.0, -1.5, 99.])

    first = _dynamic().fit(x, fit_y, cal_x, cal_y)
    first.fit_context(context, date_field='date', end_field='end')
    curve = first.curve_context(x[:4], dates, ends, outcomes)
    state = first._research_state()
    assert state['admitted_rows_by_date'] == [4, 6, 7]
    np.testing.assert_array_equal(curve.values[0], curve.values[1])

    changed = _dynamic().fit(x, fit_y, cal_x, cal_y)
    changed.fit_context(context, date_field='date', end_field='end')
    altered = outcomes.copy(); altered[-1] = -99.
    other = changed.curve_context(x[:4], dates, ends, altered)
    np.testing.assert_array_equal(curve.values, other.values)
    assert (np.diff(curve.values, axis=1) >= 0).all()


def test_dynamic_pit_prior_excludes_immature_calibration_outcomes():
    x = np.ones((8, 2)); fit_y = np.linspace(-1.5, 1.5, len(x))
    context = pd.DataFrame({'date': ['2019-12-01'] * 4,
                            'end': ['2019-12-15', '2019-12-15',
                                    '2020-03-01', '2020-03-01']})
    curves = []
    for future in ([1.5, 1.5], [-1.5, -1.5]):
        model = _dynamic(minimum_rows=2, prior_strength=10).fit(
            x, fit_y, x[:4], np.r_[-1.5, -1.5, future])
        model.fit_context(context, date_field='date', end_field='end')
        curves.append(model.curve_context(
            x[:1], ['2020-01-02'], ['2020-02-01'], [0.]).values)
    np.testing.assert_array_equal(*curves)


def test_dynamic_pit_recency_weighting_and_sparse_global_fallback():
    old = pd.DataFrame({'date': ['2018-01-01'] * 4 + ['2019-12-20'] * 4,
                        'end': ['2018-02-01'] * 4 + ['2019-12-25'] * 4})
    x = np.ones((10, 2)); y = np.linspace(-1.5, 1.5, 10)
    cal_y = np.r_[np.full(4, -1.5), np.full(4, 1.5)]
    short = _dynamic(half_life_days=5, minimum_rows=20).fit(x, y, x[:8], cal_y)
    short.fit_context(old, date_field='date', end_field='end')
    fallback = short.curve_context(x[:1], ['2020-01-02'], ['2020-02-01'], [0.])
    np.testing.assert_allclose(fallback.values, short.curve(x[:1]).values)
    weighted = _dynamic(half_life_days=5, minimum_rows=4, prior_strength=0).fit(
        x, y, x[:8], cal_y)
    weighted.fit_context(old, date_field='date', end_field='end')
    recent = weighted.curve_context(x[:1], ['2020-01-02'], ['2020-02-01'], [0.])
    assert recent.quantile([.5])[0, 0] > fallback.quantile([.5])[0, 0]


def test_beta_transformed_pool_is_finite_monotone_and_has_exact_identity():
    params = dict(
        left_class='dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
        left_params={'horizon_index': 0, 'reference_index': 1, 'knots': 41},
        right_class='dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
        right_params={'horizon_index': 0, 'reference_index': 1, 'knots': 41},
        index_indices=[2, 3], cell_indices=[0, 2, 3], knots=41,
        weights=[0, .5, 1], alphas=[.75, 1, 1.25], betas=[.75, 1, 1.25],
        tail_probability=.1, crps_weight=1, tail_weight=1)
    x = np.array([[5, 1, 1, 0], [5, 1, 0, 1]] * 12, dtype=float)
    y = np.tile(np.linspace(-1.5, 1.5, 12), 2)
    identity = BetaTransformedPoolCDF(**{**params, 'weights': [0], 'alphas': [1], 'betas': [1]})
    identity.left = _StaticGridEstimator(0); identity.right = _StaticGridEstimator(1)
    identity.fit(x, y, x, y)
    expected = identity.left.curve(x).cdf(np.linspace(-3, 3, 51))
    np.testing.assert_allclose(identity.curve(x).cdf(np.linspace(-3, 3, 51)), expected)
    model = BetaTransformedPoolCDF(**params)
    model.left = _StaticGridEstimator(0); model.right = _StaticGridEstimator(1)
    model.fit(x, y, x, y)
    curve = model.curve(x)
    assert np.isfinite(curve.quantile([.001, .05, .5, .95, .999])).all()
    assert (np.diff(curve.cdf(np.linspace(-5, 5, 101)), axis=1) >= -1e-12).all()
    assert model._research_state()['grid_candidates'] == 27


def test_semiparametric_gpd_tails_preserve_center_and_pool_sparse_groups():
    params = dict(
        endpoint_class='dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
        endpoint_params={'horizon_index': 0, 'reference_index': 1, 'knots': 101},
        index_indices=[2, 3], knots=101, splice_probabilities=[.1, .9],
        minimum_exceedances=6, prior_strength=10, shape_bounds=[-.4, .8],
        probability_floor=.0001)
    x = np.array([[5, 1, 1, 0]] * 80 + [[5, 1, 0, 1]] * 4, dtype=float)
    y = np.r_[np.linspace(-4, 4, 80), [-1, -.5, .5, 1]]
    model = SemiparametricGPDTailCDF(**params)
    model.endpoint = _StaticGridEstimator(0)
    model.fit(x, y, x[:20], y[:20])
    curve, base = model.curve(x[:2]), model.endpoint.curve(x[:2])
    np.testing.assert_allclose(curve.quantile([.1, .25, .5, .75, .9]),
                               base.quantile([.1, .25, .5, .75, .9]), atol=1e-10)
    assert np.isfinite(curve.values).all() and (np.diff(curve.values, axis=1) >= 0).all()
    state = model._research_state()
    assert state['groups'][1]['fallback'] == 'global'
    assert all(-.4 <= v['left_shape'] <= .8 and -.4 <= v['right_shape'] <= .8
               for v in state['groups'])


def test_semiparametric_gpd_anchor_batches_are_bounded_and_exact():
    class TrackingGrid(_StaticGridEstimator):
        def __init__(self, shift):
            super().__init__(shift)
            self.maximum_batch = 0

        def curve(self, x):
            self.maximum_batch = max(self.maximum_batch, len(x))
            return super().curve(x)

    params = dict(
        endpoint_class='dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
        endpoint_params={'horizon_index': 0, 'reference_index': 1, 'knots': 101},
        index_indices=[2, 3], knots=101, splice_probabilities=[.1, .9],
        minimum_exceedances=6, prior_strength=10, shape_bounds=[-.4, .8],
        probability_floor=.0001)
    x = np.array([[5, 1, 1, 0]] * 80 + [[5, 1, 0, 1]] * 20, dtype=float)
    y = np.linspace(-4, 4, len(x))
    bounded = SemiparametricGPDTailCDF(**params, anchor_batch_size=7)
    bounded.endpoint = TrackingGrid(0)
    bounded.fit(x, y, x[:20], y[:20])
    unbatched = SemiparametricGPDTailCDF(**params, anchor_batch_size=1000)
    unbatched.endpoint = TrackingGrid(0)
    unbatched.fit(x, y, x[:20], y[:20])
    assert bounded.endpoint.maximum_batch == 7
    np.testing.assert_equal(bounded.global_tails, unbatched.global_tails)
    np.testing.assert_equal(bounded.group_tails, unbatched.group_tails)
    np.testing.assert_array_equal(bounded.curve(x[:5]).values,
                                  unbatched.curve(x[:5]).values)


def test_scores_include_proper_quantile_tail_metrics_and_declared_hits():
    curve = GridCurve([[-2., 0., 2.]], [[0., .5, 1.]])
    scores, _ = ChronologicalCDFStudy.scores(
        curve, np.array([.25]), 401, [[-2, -1], [1, 2]], 201,
        tail_probabilities=[.01, .025, .05, .1, .9, .95, .975, .99])
    assert scores['lower_tail_quantile_score'][0] >= 0
    assert scores['upper_tail_quantile_score'][0] >= 0
    assert {'below_01', 'below_025', 'below_05', 'below_10',
            'above_90', 'above_95', 'above_975', 'above_99'}.issubset(scores)


def _tail_blend(**changes):
    params = dict(
        incumbent_class='dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
        incumbent_params={'horizon_index': 0, 'reference_index': 3, 'knots': 21},
        option_class='dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
        option_params={'horizon_index': 0, 'reference_index': 4, 'knots': 21},
        index_indices=[1, 2], cell_indices=[5, 1, 2], knots=21,
        left_weights=[0, .5, 1], center_weights=[0, .5, 1],
        right_weights=[0, .5, 1], tolerance=1e-12)
    model = TailConstrainedQuantileBlendCDF(**{**params, **changes})
    model.incumbent = _StaticGridEstimator(0)
    model.option = _StaticGridEstimator(1)
    return model


def test_tail_constrained_quantile_blend_is_monotone_and_endpoint_exact():
    p = np.linspace(0, 1, 21)
    incumbent = np.tile(np.linspace(-2, 2, 21), (2, 1))
    option = np.tile(np.linspace(-1, 3, 21), (2, 1))
    np.testing.assert_array_equal(
        TailConstrainedQuantileBlendCDF._blend(incumbent, option, p, [0, 0, 0]),
        incumbent)
    np.testing.assert_array_equal(
        TailConstrainedQuantileBlendCDF._blend(incumbent, option, p, [1, 1, 1]),
        option)
    varied = TailConstrainedQuantileBlendCDF._blend(
        incumbent, option[:, ::-1], p, [0, 1, 0])
    assert (np.diff(varied, axis=1) >= 0).all()
    assert not np.allclose(varied[:, 1], varied[:, 10])
    strict = ConvexCurve(MixtureCurve([[1]], [[0]], [[1]]),
                         MixtureCurve([[1]], [[1]], [[1]]), .25)
    discretized = TailConstrainedQuantileBlendCDF._endpoint_quantiles(strict, p)
    assert discretized.shape == (1, 21)
    assert np.isfinite(discretized).all() and (np.diff(discretized) >= 0).all()


def test_tail_constrained_blend_uses_calibration_labels_cells_and_tail_guards():
    x = np.array([[5, 1, 0, 1, 1, 5], [5, 0, 1, 1, 1, 5]] * 10, dtype=float)
    cal_x = np.array([[5, 1, 0, 1, 1, 5], [5, 1, 0, 1, 1, 5],
                      [5, 1, 0, 1, 1, 7], [5, 0, 1, 1, 1, 5],
                      [5, 0, 1, 1, 1, 7], [5, 0, 1, 1, 1, 9]], dtype=float)
    positive = _tail_blend().fit(x, np.zeros(len(x)), cal_x, np.full(len(cal_x), 1.5))
    negative = _tail_blend().fit(x, np.zeros(len(x)), cal_x, np.full(len(cal_x), -1.5))
    assert not np.array_equal(positive.weights_by_index, negative.weights_by_index)
    np.testing.assert_allclose(positive.calibration_row_weights_by_index[0], [1/4, 1/4, 1/2])
    for row in positive.calibration_diagnostics + negative.calibration_diagnostics:
        assert np.less_equal(row['selected_tail_deviation'],
                             np.asarray(row['incumbent_tail_deviation']) + 1e-12).all()
    assert (np.diff(positive.curve(cal_x).quantile(np.linspace(0, 1, 21)), axis=1) >= 0).all()


@pytest.mark.parametrize('bad', [
    np.array([[5, 0, 0, 1, 1, 5.]]),
    np.array([[5, 1, 1, 1, 1, 5.]]),
    np.array([[5, .5, .5, 1, 1, 5.]]),
])
def test_tail_constrained_blend_refuses_non_one_hot_index_rows(bad):
    with pytest.raises(ValueError, match='one-hot'):
        _tail_blend()._validate_x(bad)


def test_tail_constrained_blend_requires_zero_and_excludes_actual_dte():
    with pytest.raises(ValueError, match='include zero'):
        _tail_blend(left_weights=[.25, .5])
    with pytest.raises(ValueError, match='excluded'):
        _tail_blend(option_params={'horizon_index': 0, 'reference_index': 5, 'knots': 21})


def test_option_implied_transport_uses_proxy_and_falls_back_on_ineligible_rows():
    x = np.array([
        [-2., -1., 0., 1., 2., 1., 5., 1., 0.],
        [99., 99., 99., 99., 99., 0., 5., 1., 0.],
    ])
    y = np.linspace(-2, 2, 20)
    fit_x = np.tile(x[:1], (20, 1))
    fit_x[:, 6] = np.tile([5., 10.], 10)
    model = OptionImpliedTransportCDF(
        proxy_indices=[0, 1, 2, 3, 4], probabilities=[.05, .25, .5, .75, .95],
        eligible_index=5, condition_indices=[6, 7, 8], reference_index=6,
        knots=21, tail_width=3., transport_knots=9,
    ).fit(fit_x, y, fit_x, y)
    curve = model.curve(x)
    np.testing.assert_allclose(curve.quantile([.25, .5, .75])[0], [-1., 0., 1.], atol=.35)
    assert np.isfinite(curve.quantile([.01, .99])).all()
    assert np.all(np.diff(curve.cdf(np.linspace(-6, 6, 101)), axis=1) >= -1e-12)
    assert curve._arrays()['active'].ravel().tolist() == [1, 0]


def test_option_implied_transport_scales_empirical_tail_probability_mass():
    probability = np.linspace(.005, .995, 199)
    y = np.sign(probability-.5)*np.abs(2*probability-1)**2*8
    fit_x = np.tile(np.array([[-2., -1., 0., 1., 2., 1., 5., 1., 0.]]), (len(y), 1))
    model = OptionImpliedTransportCDF(
        proxy_indices=[0, 1, 2, 3, 4], probabilities=[.05, .25, .5, .75, .95],
        eligible_index=5, condition_indices=[6, 7, 8], reference_index=6,
        knots=201, tail_width=3., transport_knots=0,
    ).fit(fit_x, y, fit_x[:20], y[:20])
    row = fit_x[:1]
    empirical = model.fallback.curve(row)
    proxy = model._proxy(row)
    left_anchor, right_anchor = proxy.quantile([.05, .95])[0]
    left_mass = empirical.cdf([[left_anchor]])[0, 0]
    right_mass = 1.-empirical.cdf([[right_anchor]])[0, 0]
    expected = np.r_[
        empirical.quantile([.025*left_mass/.05])[0, 0],
        empirical.quantile([1.-.025*right_mass/.05])[0, 0],
    ]
    curve = model.curve(row)
    np.testing.assert_allclose(curve.quantile([.025, .975])[0], expected)
    np.testing.assert_allclose(curve.quantile([.05, .95])[0],
                               [left_anchor, right_anchor])
    assert np.all(np.diff(curve.quantile(np.linspace(0, 1, 201))[0]) >= 0)


def test_option_transport_partially_pools_index_horizon_maps_and_falls_back_global():
    base = np.array([-2., -1., 0., 1., 2., 1., 5., 1., 0.])
    x = np.column_stack([np.tile(base, (40, 1)), np.r_[np.zeros(20), np.ones(20)]])
    x[20:, 6:9] = [5., 0., 1.]
    y = np.r_[np.linspace(-1.8, -.2, 20), np.linspace(.2, 1.8, 20)]
    common = dict(
        proxy_indices=[0, 1, 2, 3, 4], probabilities=[.05, .25, .5, .75, .95],
        eligible_index=5, condition_indices=[6, 7, 8], reference_index=6,
        knots=21, tail_width=3., transport_knots=9,
        transport_condition_indices=[9])
    separate = OptionImpliedTransportCDF(
        **common, transport_prior_strength=0).fit(x, y, x[:8], y[:8])
    pooled = OptionImpliedTransportCDF(
        **common, transport_prior_strength=1000).fit(x, y, x[:8], y[:8])
    global_model = OptionImpliedTransportCDF(
        **{k: v for k, v in common.items() if k != 'transport_condition_indices'}
    ).fit(x, y, x[:8], y[:8])
    assert len(separate.group_transports) == 2
    keys = sorted(separate.group_transports)
    assert not np.allclose(separate.group_transports[keys[0]],
                           separate.group_transports[keys[1]])
    for key in keys:
        assert np.linalg.norm(pooled.group_transports[key]-pooled.transport_x) < np.linalg.norm(
            separate.group_transports[key]-separate.transport_x)
    unseen = x[:1].copy()
    unseen[:, 9] = 2
    query = [.05, .5, .95]
    np.testing.assert_allclose(separate.curve(unseen).quantile(query),
                               global_model.curve(unseen).quantile(query), rtol=0, atol=0)
    mixed = separate.curve(x[[0, 20]]).quantile(query)
    singles = np.vstack([separate.curve(x[[row]]).quantile(query) for row in (0, 20)])
    np.testing.assert_allclose(mixed, singles, rtol=0, atol=0)
    center_only = OptionImpliedTransportCDF(
        **common, transport_prior_strength=0, transport_local_bounds=[.05, .95]
    ).fit(x, y, x[:8], y[:8])
    central = center_only.curve(x[:1]).quantile(query)
    global_curve = global_model.curve(x[:1]).quantile(query)
    np.testing.assert_allclose(central[:, [0, 2]], global_curve[:, [0, 2]], rtol=0, atol=1e-12)
    assert not np.allclose(central[:, 1], global_curve[:, 1])
    assert (np.diff(center_only.curve(x[:1]).quantile(np.linspace(0, 1, 101))) >= 0).all()


def test_option_transport_conditioning_contract_refuses_invalid_settings():
    common = dict(
        proxy_indices=[0, 1], probabilities=[.25, .75], eligible_index=2,
        condition_indices=[3], reference_index=4, knots=21)
    with pytest.raises(ValueError, match='transport'):
        OptionImpliedTransportCDF(**common, transport_prior_strength=10)
    with pytest.raises(ValueError, match='transport'):
        OptionImpliedTransportCDF(
            **common, transport_condition_indices=[3, 3], transport_prior_strength=10)
    for bounds in ([.95, .05], [0, .95], [.05, 1], [.05], [False, .95]):
        with pytest.raises(ValueError, match='transport'):
            OptionImpliedTransportCDF(
                **common, transport_condition_indices=[3], transport_local_bounds=bounds)


def test_pca_augmentation_fits_components_on_training_rows_only():
    rng = np.random.default_rng(82)
    x = rng.normal(size=(40, 4)); y = x[:, 0]+rng.normal(size=40)
    model = PCAAugmentedCDF(
        estimator_class='dskit.pipeline.libs.predictive_cdf:QuantileCDF',
        estimator_params={'probabilities': [.1, .5, .9], 'trees': 5, 'leaves': 3,
                          'min_child': 2, 'threads': 1, 'tail_width': 2.},
        pca_indices=[0, 1, 2], components=2,
    ).fit(x, y, x[:8]+1000, y[:8])
    np.testing.assert_allclose(model.scaler.mean_, x[:, :3].mean(axis=0))
    assert model.pca.components_.shape == (2, 3)
    assert np.isfinite(model.curve(x[:3]).quantile([.1, .5, .9])).all()


def test_set_mixture_is_permutation_invariant_and_mask_safe():
    rng = np.random.default_rng(42)
    # Three contracts x (moneyness, IV, spread), then three masks and two context fields.
    contracts = rng.normal(size=(32, 3, 3))
    masks = np.ones((32, 3))
    context = rng.normal(size=(32, 2))
    x = np.column_stack([contracts.reshape(32, -1), masks, context])
    x[0, 9:12] = 0
    y = rng.normal(size=32)
    common = dict(nodes=3, node_features=3, tensor_indices=list(range(9)),
                  mask_indices=[9, 10, 11], context_indices=[12, 13],
                  components=2, hidden=8, epochs=2, batch_size=16,
                  seeds=[7], device='cpu', deterministic=True)
    model = SetMixtureCDF(pooling='deepsets', **common).fit(x, y, x[:8], y[:8])
    order = [2, 0, 1]
    permuted = x.copy()
    permuted[:, :9] = contracts[:, order].reshape(32, -1)
    permuted[:, 9:12] = x[:, 9:12][:, order]
    np.testing.assert_allclose(model.curve(x[:6]).cdf([-.5, 0., .5]),
                               model.curve(permuted[:6]).cdf([-.5, 0., .5]), atol=1e-7)
    padded = x.copy()
    padded[:, 6:9] = 1e6
    padded[:, 11] = 0
    baseline = x.copy()
    baseline[:, 11] = 0
    np.testing.assert_allclose(model.curve(padded[:6]).cdf(0),
                               model.curve(baseline[:6]).cdf(0), atol=1e-7)
    assert np.isfinite(model.curve(x[:1]).cdf(0)).all()


def test_spline_flow_returns_ordered_finite_distribution():
    pytest.importorskip('nflows')
    rng = np.random.default_rng(9)
    x = rng.normal(size=(48, 3)); y = .4*x[:, 0] + rng.normal(size=48)
    model = SplineFlowCDF(hidden=8, bins=4, epochs=2, batch_size=16,
                          probabilities=[.01, .1, .5, .9, .99], seed=3,
                          device='cpu', deterministic=True).fit(x, y, x[:8], y[:8])
    curve = model.curve(x[:5])
    q = curve.quantile([.05, .5, .95])
    assert np.isfinite(q).all() and (np.diff(q, axis=1) >= 0).all()


def test_catboost_native_multiquantile_returns_ordered_curve():
    pytest.importorskip('catboost')
    rng = np.random.default_rng(10)
    x = rng.normal(size=(60, 3)); y = x[:, 0] + rng.normal(size=60)
    model = CatBoostQuantileCDF(probabilities=[.05, .5, .95], iterations=8,
                                depth=3, threads=1, task_type='CPU').fit(
                                    x, y, x[:8], y[:8])
    q = model.curve(x[:4]).quantile([.05, .5, .95])
    assert np.isfinite(q).all() and (np.diff(q, axis=1) >= 0).all()


def test_mixture_cdf_hand_values_and_extremes():
    curve = MixtureCurve([[0.25, 0.75]], [[0., 0.]], [[1., 2.]])
    actual = curve.cdf(np.array([[-np.inf, 0., np.inf]]))
    np.testing.assert_allclose(actual, [[0., 0.5, 1.]])


def test_scalar_queries_and_normalized_near_unit_weights():
    curve = MixtureCurve([[1.000009]], [[0]], [[1]])
    np.testing.assert_allclose(curve.cdf(np.inf), [[1.]])
    np.testing.assert_allclose(curve.cdf(0), [[.5]])
    np.testing.assert_allclose(curve.quantile(.5), [[0]])
    grid = GridCurve([[0, 1]], [0, 1])
    np.testing.assert_allclose(grid.cdf(.5), [[.5]])
    np.testing.assert_allclose(grid.quantile(.5), [[.5]])


def test_saved_curve_arrays_preserve_calibration_and_exact_parameters(tmp_path):
    base = MixtureCurve([[.2, .8]], [[-1, 1]], [[.5, 1.]])
    curve = CalibratedCurve(base, [.1, .2, .5, .8], 5)
    path = tmp_path/'curve.npz'
    np.savez_compressed(path, **curve._arrays())
    with np.load(path, allow_pickle=False) as a:
        restored = MixtureCurve(a['weights'], a['means'], a['scales'])
        expected = np.interp(restored.cdf([-3, 0, 2]), a['calibration_x'][0], a['calibration_p'][0])
        np.testing.assert_allclose(curve.cdf([-3, 0, 2]), expected, rtol=0, atol=0)
    np.testing.assert_allclose(GridCurve([[0, 1]], [0, 1])._arrays()['values'], [[0, 1]])


def test_convex_curve_accepts_bounded_row_weights_and_persists_them():
    left = GridCurve([[0, 1], [0, 1]], [0, 1])
    right = GridCurve([[1, 2], [1, 2]], [0, 1])
    curve = ConvexCurve(left, right, [0., 1.])
    np.testing.assert_allclose(curve.cdf([[.5], [1.5]]), [[.5], [.5]])
    np.testing.assert_allclose(curve.quantile([.25, .75]), [[.25, .75], [1.25, 1.75]])
    np.testing.assert_array_equal(curve._arrays()['weight'], [[0.], [1.]])
    with pytest.raises(ValueError, match='weight'):
        ConvexCurve(left, right, [0., 1., .5])
    with pytest.raises(ValueError, match='weight'):
        ConvexCurve(left, right, [0., 1.1])


def test_left_cdf_score_matches_independent_scipy_and_has_finite_gradient():
    import torch
    from scipy.special import ndtr

    model = MixtureMLPCDF(
        components=2, hidden=3, epochs=1, seeds=[3], device='cpu',
        left_cdf_weight=.5, left_cdf_bounds=[-2., 0.], left_cdf_points=5)
    logw = torch.tensor(np.log([[.25, .75], [.6, .4]]), dtype=torch.float64,
                        requires_grad=True)
    mu = torch.tensor([[-.5, .7], [-1., .2]], dtype=torch.float64, requires_grad=True)
    sigma = torch.tensor([[.8, 1.2], [.5, 2.]], dtype=torch.float64, requires_grad=True)
    y = torch.tensor([[-1.25], [.4]], dtype=torch.float64)
    actual = model._left_cdf_score(y, logw, mu, sigma)
    grid = np.linspace(-2., 0., 5)
    predicted = np.exp(logw.detach().numpy())[:, :, None] * ndtr(
        (grid[None, None, :]-mu.detach().numpy()[:, :, None])
        / sigma.detach().numpy()[:, :, None])
    expected = np.mean((predicted.sum(1)-(y.detach().numpy() <= grid))**2)
    assert actual.item() == pytest.approx(expected, abs=1e-12)
    actual.backward()
    assert all(torch.isfinite(value.grad).all() for value in (logw, mu, sigma))


@pytest.mark.parametrize('settings', [
    {'left_cdf_weight': -1}, {'left_cdf_weight': np.inf},
    {'left_cdf_bounds': [0, -1]}, {'left_cdf_bounds': [-2, np.inf]},
    {'left_cdf_bounds': [-1, 1]},
    {'left_cdf_points': 1}, {'left_cdf_points': True},
])
def test_invalid_left_cdf_training_parameters_refuse(settings):
    with pytest.raises(ValueError, match='left'):
        MixtureMLPCDF(device='cpu', **settings)


def test_zero_left_cdf_weight_preserves_legacy_training_path():
    rng = np.random.default_rng(12)
    x, y = rng.normal(size=(32, 3)), rng.normal(size=32)
    common = dict(components=1, hidden=[4], epochs=2, batch_size=16,
                  seeds=[7], device='cpu', deterministic=True)
    legacy = MixtureMLPCDF(**common).fit(x, y, x[:8], y[:8])
    explicit = MixtureMLPCDF(
        **common, left_cdf_weight=0, left_cdf_bounds=[-3, 0], left_cdf_points=9,
    ).fit(x, y, x[:8], y[:8])
    np.testing.assert_allclose(
        legacy.curve(x[:5]).quantile([.1, .5, .9]),
        explicit.curve(x[:5]).quantile([.1, .5, .9]), rtol=0, atol=0)
    assert legacy._equivalence_state() == explicit._equivalence_state()


def test_mlp_feature_selection_preserves_raw_head_routing_and_fitted_identity():
    rng = np.random.default_rng(26)
    x = np.column_stack([rng.normal(size=32), rng.normal(size=32),
                         np.tile([[1., 0.], [0., 1.]], (16, 1)),
                         rng.normal(size=32)])
    y = rng.normal(size=32)
    common = dict(components=1, hidden=[4], epochs=1, seeds=[3],
                  device='cpu', deterministic=True, head_features=[2, 3])
    model = MixtureMLPCDF(**common, feature_indices=[0, 2, 3]).fit(
        x, y, x[:8], y[:8])
    changed = x[:4].copy()
    changed[:, [1, 4]] = 1000.
    np.testing.assert_array_equal(model.curve(x[:4]).cdf([0., 1.]),
                                  model.curve(changed).cdf([0., 1.]))
    assert model._heads(x[:4]).tolist() == [0, 1, 0, 1]
    other = MixtureMLPCDF(**common, feature_indices=[0, 1, 2, 3]).fit(
        x, y, x[:8], y[:8])
    assert model._equivalence_state() != other._equivalence_state()
    with pytest.raises(ValueError, match='feature indices'):
        MixtureMLPCDF(**common, feature_indices=[0, 0])


def test_adaptive_blend_uses_calibration_labels_and_bounds_row_weights():
    rng = np.random.default_rng(4)
    conditions = np.array([[5, 1, 0], [5, 0, 1], [10, 1, 0], [10, 0, 1]])
    x = np.column_stack([
        np.tile(conditions, (12, 1)), np.ones(48), rng.normal(size=48),
        np.tile(conditions[:, 0], 12)])
    y = .15*x[:, 4]+rng.normal(scale=.6, size=48)
    cal_x = np.column_stack([
        np.tile(conditions, (4, 1)), np.ones(16), np.linspace(-2, 2, 16),
        np.tile(conditions[:, 0], 4)])
    common = dict(
        lower_weight=0., upper_weight=.35, condition_indices=[0, 1, 2],
        cell_indices=[5, 1, 2],
        reference_index=3, knots=21, gate_indices=[0, 1, 2, 3, 4],
        grid_bounds=[-3, 3], grid_points=31, l2=.01, max_iter=100, tolerance=1e-9,
        mlp={'components': 1, 'hidden': [4], 'epochs': 2, 'batch_size': 16,
             'seeds': [3], 'device': 'cpu', 'deterministic': True,
             'feature_indices': [0, 1, 2, 3, 4]})
    low = AdaptiveEmpiricalMLPBlendCDF(**common).fit(
        x, y, cal_x, np.full(len(cal_x), -2.))
    high = AdaptiveEmpiricalMLPBlendCDF(**common).fit(
        x, y, cal_x, np.full(len(cal_x), 2.))
    low_weights = np.asarray(low.curve(cal_x).weight).ravel()
    high_weights = np.asarray(high.curve(cal_x).weight).ravel()
    assert (low_weights >= 0).all() and (low_weights <= .35).all()
    assert (high_weights >= 0).all() and (high_weights <= .35).all()
    assert not np.allclose(low_weights, high_weights)
    assert low._equivalence_state() == high._equivalence_state()


def test_adaptive_reporting_cells_use_actual_metadata_only_for_calibration_weights():
    rng = np.random.default_rng(19)
    conditions = np.tile([[5., 1., 0.], [5., 0., 1.],
                          [10., 1., 0.], [10., 0., 1.]], (8, 1))
    x = np.column_stack([conditions, np.ones(len(conditions)),
                         rng.normal(size=len(conditions)), conditions[:, 0]])
    y = rng.normal(size=len(x))
    cal_x = np.array([[5., 1., 0., 1., -1., 5.],
                      [5., 1., 0., 1., 0., 5.],
                      [5., 1., 0., 1., 1., 7.],
                      [5., 1., 0., 1., 2., 9.]])
    model = AdaptiveEmpiricalMLPBlendCDF(
        lower_weight=0., upper_weight=.35, condition_indices=[0, 1, 2],
        cell_indices=[5, 1, 2], reference_index=3, knots=21,
        gate_indices=[0, 1, 2, 3, 4], grid_bounds=[-3., 3.],
        grid_points=31, l2=.01, max_iter=100, tolerance=1e-9,
        mlp={'components': 1, 'hidden': [4], 'epochs': 1, 'seeds': [3],
             'device': 'cpu', 'deterministic': True,
             'feature_indices': [0, 1, 2, 3, 4]})
    model.fit(x, y, cal_x, np.array([-1., 0., 1., 2.]))
    np.testing.assert_allclose(model.calibration_row_weights,
                               [1/6, 1/6, 1/3, 1/3])
    changed = cal_x.copy()
    changed[:, 5] = [15., 16., 17., 18.]
    np.testing.assert_array_equal(model.curve(cal_x).cdf([0., 1.]),
                                  model.curve(changed).cdf([0., 1.]))
    changed[0, 5] = np.nan
    with pytest.raises(ValueError, match='cell metadata'):
        model._validate_x(changed)


def test_adaptive_blend_constructor_default_denies_invalid_gate_contract():
    common = dict(
        lower_weight=0, upper_weight=.35, condition_indices=[0, 1, 2],
        cell_indices=[5, 1, 2],
        reference_index=3, knots=21, gate_indices=[0, 4],
        grid_bounds=[-3, 3], grid_points=31, l2=.01, max_iter=10,
        tolerance=1e-6, mlp={'device': 'cpu', 'feature_indices': [0, 1, 2, 3, 4]})
    for change in (
        {'upper_weight': 1.1}, {'lower_weight': .4}, {'gate_indices': [0, 0]},
        {'grid_bounds': [3, -3]}, {'grid_points': 1}, {'l2': -1},
        {'max_iter': 0}, {'tolerance': 0}, {'cell_indices': [5, 5]},
        {'gate_indices': [0, 5]},
        {'mlp': {'device': 'cpu', 'feature_indices': [0, 1, 2, 3, 4, 5]}},
    ):
        with pytest.raises(ValueError):
            AdaptiveEmpiricalMLPBlendCDF(**{**common, **change})


def test_study_refuses_second_calibration_map_for_adaptive_blend(tmp_path):
    config, _ = _hpo_fixture(tmp_path)
    config['study']['models']['adaptive'] = {
        'class': 'dskit.pipeline.libs.predictive_cdf:AdaptiveEmpiricalMLPBlendCDF',
        'params': {}, 'calibrate': True, 'pooled': True}
    with pytest.raises(ValueError, match='consumes calibration'):
        ChronologicalCDFStudy(config['study'])


def test_split_purges_both_boundaries_and_never_reuses_rows():
    frame = pd.DataFrame({
        'date': ['2016-06-01', '2017-12-20', '2018-02-01',
                 '2018-12-20', '2019-06-01', '2020-01-01'],
        'end': ['2016-06-20', '2018-01-03', '2018-03-01',
                '2019-01-03', '2019-06-30', '2020-01-15'],
    })
    fit, cal, val = ChronologicalCDFStudy.split(frame, 2019, 'date', 'end')
    assert fit.index.tolist() == [0]
    assert cal.index.tolist() == [2]
    assert val.index.tolist() == [4]


@pytest.mark.parametrize('weights,means,scales', [
    ([[.9]], [[0]], [[1]]), ([[1]], [[0]], [[0]]),
    ([[1]], [[np.nan]], [[1]]), ([[-1, 2]], [[0, 1]], [[1, 1]]),
])
def test_invalid_mixture_refuses(weights, means, scales):
    with pytest.raises(ValueError):
        MixtureCurve(weights, means, scales)


def test_mixture_inverse_independent_normal_reference():
    from scipy.stats import norm
    p = np.array([.001, .05, .5, .95, .999])
    c = MixtureCurve([[1], [1]], [[2], [-3]], [[.5], [2]])
    expected = np.array([[2], [-3]])+np.array([[.5], [2]])*norm.ppf(p)
    np.testing.assert_allclose(c.quantile(p), expected, atol=1e-10)
    np.testing.assert_allclose(c.cdf(c.quantile(p)), np.tile(p, (2, 1)), atol=1e-10)


def test_grid_interpolation_and_recalibration_have_correct_direction():
    base = GridCurve([[0, 1], [0, 2]], [0, 1])
    np.testing.assert_allclose(base.cdf([-.1, .5, 2.1]), [[0, .5, 1], [0, .25, 1]])
    mapped = CalibratedCurve(base, [.1, .2, .3], 3)
    np.testing.assert_allclose(mapped.cdf([[.2], [.4]]), [[.5], [.5]])
    np.testing.assert_allclose(mapped.quantile([.5]), [[.2], [.4]])
    with pytest.raises(ValueError):
        GridCurve([[0, 1, 2]], [[0, .8, .7]])


@pytest.mark.parametrize('pit', [[0, 0, 0], [1, 1, 1], [0, 0, 1, 1]])
def test_calibration_endpoint_ties_still_reach_zero_and_one(pit):
    curve = CalibratedCurve(GridCurve([[0, 1]], [0, 1]), pit, 5)
    np.testing.assert_allclose(curve.cdf([-np.inf, np.inf]), [[0, 1]])


def test_crps_matches_existing_independent_sample_owner():
    from dskit.pipeline.distribution_scores import SampleDistribution, Crps
    curve = MixtureCurve([[.3, .7]], [[-1, 1]], [[1, .5]])
    scores, samples = ChronologicalCDFStudy.scores(curve, np.array([.25]), 401, [[-2, -1], [1, 2]], 201)
    distribution = SampleDistribution(samples[0].tolist())
    expected = Crps().score(distribution, .25)
    assert scores['crps'][0] == pytest.approx(expected, abs=1e-12)


def test_empirical_exact_horizon_and_held_out_shape():
    x = np.array([[1, 1], [1, 2], [2, 1], [2, 2.]])
    y = np.array([1., 2., 3., 4.])
    h = HorizonEmpiricalCDF(0, 1, 5).fit(x, y, x, y*100)
    np.testing.assert_allclose(h.curve(x[:1]).quantile([0., 1.]), [[1., 4.]])
    # Scale is identical between the two fits; only held-out outcomes change.
    a = ScaledEmpiricalCDF(10, .02, 41).fit(x, y, x, y)
    b = ScaledEmpiricalCDF(10, .02, 41).fit(x, y, x, y*3)
    np.testing.assert_allclose(b.curve(x).quantile([.2, .8]), 3*a.curve(x).quantile([.2, .8]))


@pytest.mark.parametrize('model', [
    MonotoneCDF([-2, -1, 0, 1, 2], trees=8, min_child=5, threads=1),
    QuantileCDF([.05, .5, .95], trees=8, min_child=5, threads=1),
    MixtureMLPCDF(components=3, hidden=8, epochs=2, seeds=[3], device='cpu'),
])
def test_models_learn_valid_curves_without_reading_calibration_labels(model):
    rng = np.random.default_rng(9)
    x = rng.normal(size=(120, 3))
    y = x[:, 0]+rng.normal(size=120)
    model.fit(x[:80], y[:80], x[80:100], y[80:100])
    c = model.curve(x[100:])
    p = c.cdf(np.linspace(-10, 10, 99))
    assert np.isfinite(p).all()
    assert (np.diff(p, axis=1) >= -1e-7).all()
    assert p.min() >= -1e-7 and p.max() <= 1+1e-7
    q = c.quantile([.1, .5, .9])
    assert (np.diff(q, axis=1) >= 0).all()
    old = q.copy()
    model.fit(x[:80], y[:80], x[80:100], y[80:100]+100)
    np.testing.assert_allclose(model.curve(x[100:]).quantile([.1, .5, .9]), old, atol=1e-6)


def test_study_unknown_config_refuses():
    with pytest.raises(ValueError, match='unknown keys'):
        ChronologicalCDFStudy({'typo': 1})


def test_quantile_rearrangement_actually_repairs_crossing():
    class FixedHead:
        def __init__(self, value):
            self.value = value

        def predict(self, x):
            return np.full(len(x), self.value)

    model = QuantileCDF([.05, .5, .95])
    model.models = [FixedHead(1), FixedHead(-1), FixedHead(0)]
    np.testing.assert_allclose(model.curve(np.zeros((1, 2))).quantile([.05, .5, .95]), [[-1, 0, 1]])


def test_study_end_to_end_paired_counts_and_zero_baseline_skill(tmp_path):
    rows = []
    for year in range(2014, 2020):
        for day in range(1, 13):
            rows.append({'unit': 'A', 'date': f'{year}-03-{day:02}',
                         'end': f'{year}-03-{day+1:02}', 'h': 1,
                         'scale': 1., 'y': (day-6)/5, 'expiry': f'{year}-03-{day+1:02}'})
    # Distinct nominal series share one settlement; both must survive pairing.
    rows.append({**rows[-1], 'expiry': '2019-03-13-series-B'})
    frame = pd.DataFrame(rows)
    c = {'features': ['h', 'scale'], 'group': 'unit', 'date': 'date', 'end': 'end',
         'horizon': 'h', 'target': 'y', 'reference': 'scale', 'years': [2017, 2019],
         'output': str(tmp_path/'out'), 'samples': 41, 'tail_intervals': [[-2, -1], [1, 2]],
         'tail_points': 41, 'calibration_knots': 5, 'development_end': 2017,
         'bootstrap': {'blocks': [3], 'replicates': 10, 'seed': 4},
         'reference_model': 'reference', 'comparison_references': ['reference'],
         'identity': ['unit', 'date', 'expiry'], 'series_identity': ['unit', 'expiry'],
         'models': {'reference': {'class': 'dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
                                  'params': {'horizon_index': 0, 'reference_index': 1, 'knots': 41},
                                  'calibrate': True}}}
    study = ChronologicalCDFStudy(c)
    scores = study.run(frame)
    summary = study.summarize(scores)
    assert len(scores) == 50
    assert summary['metrics'][0]['n'] == 13
    assert summary['metrics'][0]['expiry_series'] == 13
    assert not scores.duplicated(['unit', 'date', 'expiry', 'model', 'variant']).any()
    assert summary['metrics'][0]['equal_cell_skill'] == pytest.approx(0)
    assert summary['paired_block_intervals'][0]['lo'] == pytest.approx(0)
    assert summary['paired_block_intervals'][0]['hi'] == pytest.approx(0)
    with pytest.raises(FileExistsError):
        study.run(frame)


def test_summary_block_bootstrap_drops_unrepresented_sparse_cells(tmp_path):
    output = tmp_path/'sparse-summary'
    output.mkdir()
    config = {
        'features': ['h', 'scale'], 'group': 'unit', 'date': 'date', 'end': 'end',
        'horizon': 'h', 'target': 'y', 'reference': 'scale', 'years': [2017, 2019],
        'output': str(output), 'samples': 21, 'tail_intervals': [[-2, -1]],
        'tail_points': 21, 'calibration_knots': 3, 'development_end': 2017,
        'bootstrap': {'blocks': [1], 'replicates': 20, 'seed': 4},
        'reference_model': 'reference', 'comparison_references': ['reference'],
        'identity': ['unit', 'date', 'expiry'], 'series_identity': ['unit', 'expiry'],
        'models': {'reference': {
            'class': 'dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
            'params': {'horizon_index': 0, 'reference_index': 1, 'knots': 21},
            'calibrate': True}}, 'decision_context': 'decision_region_context'}
    rows = []
    for year in (2017, 2019):
        for h, day in ((1, '01'), (2, '02')):
            for variant in ('raw', 'calibrated'):
                rows.append({'unit': 'A', 'date': f'{year}-03-{day}',
                             'end': f'{year}-03-03', 'expiry': f'{year}-03-03-{h}',
                             'h': h, 'year': year, 'model': 'reference',
                             'variant': variant, 'crps': .2,
                             'decision_strike_brier': .1})
    summary = ChronologicalCDFStudy(config).summarize(pd.DataFrame(rows))
    intervals = summary['paired_block_intervals']
    assert intervals and all(row['lo'] == pytest.approx(0.)
                             and row['hi'] == pytest.approx(0.) for row in intervals)


def test_descriptive_guard_uses_frozen_variants_local_intervals_and_tail_checks(tmp_path):
    output = tmp_path/'guard-summary'
    output.mkdir()
    model = {'class': 'dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
             'params': {'horizon_index': 0, 'reference_index': 1, 'knots': 21},
             'calibrate': True}
    config = {
        'features': ['h', 'scale'], 'group': 'unit', 'date': 'date', 'end': 'end',
        'horizon': 'h', 'target': 'y', 'reference': 'scale', 'years': [2017, 2019],
        'output': str(output), 'samples': 21, 'tail_intervals': [[-2, -1]],
        'tail_points': 21, 'calibration_knots': 3, 'development_end': 2017,
        'bootstrap': {'blocks': [1], 'replicates': 20, 'seed': 4},
        'reference_model': 'reference', 'comparison_references': ['reference'],
        'identity': ['unit', 'date', 'expiry'], 'series_identity': ['unit', 'expiry'],
        'models': {'reference': model, 'candidate': model},
        'decision_context': 'decision_region_context',
        'frozen_variants': {'reference': 'raw', 'candidate': 'raw'},
        'promotion_guard': {
            'reference': 'reference', 'candidate': 'candidate',
            'local_metric': 'decision_strike_brier', 'blocks': [1],
            'min_lower_bound': 0.,
            'noninferiority_pct': {'crps': 1., 'tail_crps': 1.,
                                   'lower_tail_quantile_score': 1.,
                                   'upper_tail_quantile_score': 1.},
            'coverage_targets': {'below_05': .05, 'above_95': .05},
            'max_coverage_deviation_increase': .01,
            'authority': 'descriptive_only'}}
    rows = []
    for year in (2017, 2019):
        for h, day in ((1, '01'), (2, '02')):
            for name in ('reference', 'candidate'):
                for variant in ('raw', 'calibrated'):
                    candidate = name == 'candidate'
                    rows.append({
                        'unit': 'A', 'date': f'{year}-03-{day}',
                        'end': f'{year}-03-03', 'expiry': f'{year}-03-03-{h}',
                        'h': h, 'year': year, 'model': name, 'variant': variant,
                        'decision_strike_brier': .09 if candidate else .1,
                        'crps': .201 if candidate else .2, 'tail_crps': .2,
                        'lower_tail_quantile_score': .2,
                        'upper_tail_quantile_score': .2,
                        'below_05': .05, 'above_95': .05})
    summary = ChronologicalCDFStudy(config).summarize(pd.DataFrame(rows))
    assert summary['selected_variants_from_development'] == config['frozen_variants']
    assert summary['promotion_status'] == 'descriptive_guard_pass_no_promotion'
    assert summary['promotion_guard']['passed']
    assert all(check['passed'] for check in summary['promotion_guard']['local_intervals'])
    assert all(check['passed'] for check in summary['promotion_guard']['noninferiority'])
    assert all(check['passed'] for check in summary['promotion_guard']['coverage'])
    annual = pd.read_csv(output/'skill_by_index_year.csv')
    assert set(annual.year) == {2019}
    assert set(annual.model) == {'reference', 'candidate'}
    failed = copy.deepcopy(config)
    failed['output'] = str(tmp_path/'guard-failure')
    (tmp_path/'guard-failure').mkdir()
    failed['promotion_guard']['min_lower_bound'] = 20.
    refusal = ChronologicalCDFStudy(failed).summarize(pd.DataFrame(rows))
    assert not refusal['promotion_guard']['passed']
    assert refusal['promotion_status'] == 'descriptive_guard_failed_no_promotion'


def test_student_curve_density_family_inverse_and_saved_degrees():
    import dskit.pipeline.libs.predictive_cdf as pack
    from scipy.stats import t
    assert hasattr(pack, 'StudentMixtureCurve'), 'Student CDF family is missing'
    curve = pack.StudentMixtureCurve([[.3, .7]], [[-1, 2]], [[.5, 1.2]], degrees=3)
    q = np.array([-np.inf, -6, -1, 0, 2, 8, np.inf])
    expected = .3*t.cdf(q, 3, loc=-1, scale=.5)+.7*t.cdf(q, 3, loc=2, scale=1.2)
    np.testing.assert_allclose(curve.cdf(q)[0], expected, atol=1e-12)
    p = np.array([.0001, .01, .5, .99, .9999])
    np.testing.assert_allclose(curve.cdf(curve.quantile(p))[0], p, atol=1e-10)
    assert curve._arrays()['kind'] == 'student_mixture'
    assert curve._arrays()['degrees'] == 3
    for degrees in [2, 0, -1, np.inf, np.nan, True]:
        with pytest.raises(ValueError):
            pack.StudentMixtureCurve([[1]], [[0]], [[1]], degrees=degrees)


def test_mlp_head_routing_and_configurable_layers():
    import torch
    try:
        model = MixtureMLPCDF(components=1, hidden=[4], epochs=1, seeds=[11],
                              head_features=[1, 2], activation='relu', dropout=0., device='cpu')
    except TypeError as error:
        pytest.fail(f'head/layer controls are missing: {error}')
    x = np.array([[0., 1, 0], [1., 0, 1], [2., 1, 0], [3., 0, 1]])
    model.fit(x, np.zeros(4), x, np.zeros(4))
    linear = [layer for layer in model.models[0] if isinstance(layer, torch.nn.Linear)]
    assert len(linear) == 2
    assert linear[-1].out_features == 6
    with torch.no_grad():
        linear[-1].weight.zero_()
        linear[-1].bias.copy_(torch.tensor([0., -2., 0., 0., 3., 0.]))
    np.testing.assert_allclose(model.curve(x).means[:, 0], [-2, 3, -2, 3])
    for bad in [[0, 0], [1, 1], [np.nan, 0], [.5, .5]]:
        with pytest.raises(ValueError, match='head'):
            model.curve(np.array([[0., *bad]]))
    unseen = MixtureMLPCDF(components=1, hidden=4, epochs=1, seeds=[11],
                           head_features=[1, 2], device='cpu')
    unseen.fit(x[[0, 2]], np.zeros(2), x[[0, 2]], np.zeros(2))
    with pytest.raises(ValueError, match='unseen'):
        unseen.curve(x[[1]])


def test_student_mlp_likelihood_and_curve_agree():
    import torch
    import dskit.pipeline.libs.predictive_cdf as pack
    from scipy.stats import t
    assert hasattr(pack, 'StudentMixtureMLPCDF'), 'Student likelihood family is missing'
    model = pack.StudentMixtureMLPCDF(degrees=5, components=1, hidden=[4, 3],
                                     epochs=2, seeds=[7], device='cpu')
    y = torch.tensor([[0.], [3.]])
    mu = torch.tensor([[1.], [-1.]])
    scale = torch.tensor([[2.], [.5]])
    np.testing.assert_allclose(model._logp(y, mu, scale).numpy(),
                               t.logpdf(y.numpy(), 5, loc=mu.numpy(), scale=scale.numpy()), rtol=1e-6)
    x = np.arange(24, dtype=float).reshape(12, 2)/10
    model.fit(x[:8], x[:8, 0], x[8:10], x[8:10, 0])
    curve = model.curve(x[10:])
    assert isinstance(curve, pack.StudentMixtureCurve)
    assert curve.degrees == 5
    assert np.isfinite(curve.quantile([.01, .5, .99])).all()


def test_pooled_study_fits_once_and_keeps_index_calibration_and_counts(tmp_path, monkeypatch):
    import json
    import dskit.pipeline.libs.predictive_cdf as pack
    calls = []

    class Spy(pack.CDFEstimator):
        def fit(self, x, y, cal_x, cal_y):
            calls.append((x.copy(), y.copy(), cal_x.copy(), cal_y.copy()))
            return self

        def curve(self, x):
            return MixtureCurve(np.ones((len(x), 1)), np.zeros((len(x), 1)), np.ones((len(x), 1)))

    monkeypatch.setattr(pack, '_PooledSpy', Spy, raising=False)
    rows = [{'unit': unit, 'date': f'{year}-03-01', 'end': f'{year}-03-02',
             'expiry': f'{year}-03-02', 'h': 1, 'scale': 1., 'y': j+year-2014,
             'task': j} for year in range(2014, 2020) for j, unit in enumerate(['A', 'B'])]
    rows += [{**rows[0], 'date': '2014-03-03', 'end': '2014-03-04', 'expiry': '2014-03-04'},
             {'unit': 'A', 'date': '2015-12-31', 'end': '2016-01-02',
              'expiry': '2016-01-02', 'h': 1, 'scale': 1., 'y': 999., 'task': 0},
             {'unit': 'B', 'date': '2016-12-31', 'end': '2017-01-02',
              'expiry': '2017-01-02', 'h': 1, 'scale': 1., 'y': 888., 'task': 1}]
    for row in rows:
        row['signal'] = 1000. if row['date'] >= '2016' else 7.
    rows[0]['signal'] = np.nan
    c = {'features': ['h', 'scale', 'task', 'signal'], 'group': 'unit', 'date': 'date', 'end': 'end',
         'horizon': 'h', 'target': 'y', 'reference': 'scale', 'years': [2017],
         'output': str(tmp_path/'pool'), 'samples': 21, 'tail_intervals': [[-2, -1]],
         'tail_points': 21, 'calibration_knots': 3, 'development_end': 2017,
         'bootstrap': {'blocks': [1], 'replicates': 3, 'seed': 4},
         'reference_model': 'pool', 'comparison_references': ['pool'],
         'identity': ['unit', 'date', 'expiry'], 'series_identity': ['unit', 'expiry'],
         'models': {'pool': {'class': 'dskit.pipeline.libs.predictive_cdf:_PooledSpy',
                            'params': {}, 'calibrate': True, 'pooled': True}}}
    scores = ChronologicalCDFStudy(c).run(pd.DataFrame(rows))
    assert len(calls) == 1, 'pooled model must fit once, not once per index'
    x, y, xc, yc = calls[0]
    assert len(x) == 5 and len(xc) == 2
    assert (x[:, 3] == 7).all(), 'imputation must not use the 1000-valued calibration/validation rows'
    assert (x[:, 2] == 0).sum() == 3 and (x[:, 2] == 1).sum() == 2
    assert set(x[:, 2]) == {0, 1} and set(xc[:, 2]) == {0, 1}
    assert 999 not in y and 888 not in yc
    assert len(scores) == 4 and set(scores.unit) == {'A', 'B'}
    counts = json.loads((tmp_path/'pool'/'counts.json').read_text())
    assert all(r['pool_fit']['n'] == 5 for r in counts)
    assert all(r['pool_fit']['groups'] == {'A': 3, 'B': 2} for r in counts)
    assert sum(r['pool_fit']['new_fit'] for r in counts) == 1
    c['models']['pool']['pooled_typo'] = True
    with pytest.raises(ValueError, match='model'):
        ChronologicalCDFStudy(c)


def _hpo_fixture(tmp_path):
    rows = [{'unit': u, 'date': f'{y}-03-{d:02}', 'end': f'{y}-03-{d+1:02}',
             'expiry': f'{y}-03-{d+1:02}', 'h': 1, 'scale': 1., 'y': (d-3)/5,
             'is_A': int(u == 'A'), 'is_B': int(u == 'B')}
            for u in ['A', 'B'] for y in range(2014, 2020) for d in range(1, 6)]
    rows.append({**rows[0], 'date': '2017-12-15', 'end': '2018-01-15', 'expiry': '2018-01-15'})
    reference = {'class': 'dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
                 'params': {'horizon_index': 0, 'reference_index': 1, 'knots': 21}, 'calibrate': False}
    c = {'features': ['h', 'scale', 'is_A', 'is_B'], 'group': 'unit', 'date': 'date', 'end': 'end',
         'horizon': 'h', 'target': 'y', 'reference': 'scale', 'years': [2017, 2019],
         'output': str(tmp_path/'unused'), 'samples': 41, 'tail_intervals': [[-2, -1]],
         'tail_points': 21, 'calibration_knots': 3, 'development_end': 2017,
         'bootstrap': {'blocks': [1], 'replicates': 5, 'seed': 4},
         'reference_model': 'reference', 'comparison_references': ['reference'],
         'identity': ['unit', 'date', 'expiry'], 'series_identity': ['unit', 'expiry'],
         'models': {'reference': reference}}
    candidate = {'class': 'dskit.pipeline.libs.predictive_cdf:MixtureMLPCDF',
                 'params': {'components': 1, 'hidden': [3], 'epochs': 1, 'seeds': [11], 'device': 'cpu'},
                 'calibrate': True, 'pooled': False}
    e = {'output': str(tmp_path/'hpo'), 'development_years': [2017], 'label_cutoff': '2018-01-01',
         'final_seeds': [11, 29], 'screen_seed': 11, 'max_candidates': 1,
         'candidates': {'candidate': candidate},
         'candidate_labels': {'candidate': {'sharing': 'separate', 'family': 'normal', 'bundle': 'a'}},
         'axes': {'sharing': ['separate'], 'family': ['normal'], 'bundle': ['a']},
         'task_features': {'A': 'is_A', 'B': 'is_B'},
         'resolutions': {'screen_samples': 21, 'final_samples': 41, 'tail_points': 21,
                         'integration_points': 21, 'audit_samples': [21, 41, 81], 'audit_rows': 2},
         'expected_cells': {k: {'A': [1], 'B': [1]} for k in ['development', 'evaluation']},
         'search_partitions': {'separate': ['candidate']},
         'evaluation_partitions': {'development': [2017], 'later': [2019]}}
    return {'study': c, 'experiment': e}, pd.DataFrame(rows)


@pytest.mark.parametrize(('field', 'value'), [
    ('end', None), ('end', np.nan), ('end', pd.NaT),
    ('end', '2017-99-99'), ('end', '2017-02-29'),
    ('date', '2017-1-01'), ('date', '2017-99-99'),
])
def test_study_entrypoints_refuse_incomplete_or_noncanonical_temporal_metadata(
        tmp_path, field, value):
    import copy
    import dskit.pipeline.libs.predictive_cdf as pack

    config, frame = _hpo_fixture(tmp_path)
    frame.loc[0, field] = value  # Outside active folds: validation must precede filtering.
    direct = copy.deepcopy(config['study'])
    direct['output'] = str(tmp_path/'direct-invalid')
    with pytest.raises(ValueError, match='date|temporal'):
        ChronologicalCDFStudy(direct).run(frame)
    with pytest.raises(ValueError, match='date|temporal'):
        pack.CDFHyperparameterStudy(config).run(
            frame, stage='search', partition='separate', provenance={'fixture': 1})
    assert not (tmp_path/'direct-invalid').exists()
    assert not (tmp_path/'hpo/search/separate').exists()


def test_split_validates_temporal_metadata_but_accepts_duplicate_frame_index():
    frame = pd.DataFrame({'date': ['2016-01-01', '2018-01-01'],
                          'end': ['2016-01-02', '2018-01-02']}, index=[0, 0])
    fit, cal, val = ChronologicalCDFStudy.split(frame, 2019, 'date', 'end')
    assert len(fit) == 1 and len(cal) == 1 and val.empty
    frame.iloc[0, frame.columns.get_loc('date')] = 'not-a-date'
    with pytest.raises(ValueError, match='date|temporal'):
        ChronologicalCDFStudy.split(frame, 2019, 'date', 'end')


def test_hpo_json_stages_freeze_development_and_refuse_partial_or_changed_inputs(tmp_path):
    import json
    import dskit.pipeline.libs.predictive_cdf as pack
    assert hasattr(pack, 'CDFHyperparameterStudy'), 'standard JSON HPO orchestration is missing'
    config, frame = _hpo_fixture(tmp_path)
    config['experiment']['selection_guard'] = {
        'reference': 'reference', 'variant': 'raw',
        'metrics': ['below_05', 'above_95'], 'target': .05, 'tolerance': 1.}
    study = pack.CDFHyperparameterStudy(config)
    provenance = {'sources': {'test': 'pinned'}, 'readers': {'fixture': 1}}
    with pytest.raises((FileNotFoundError, ValueError)):
        study.run(frame, stage='select', provenance=provenance)
    study.run(frame, stage='search', partition='separate', provenance=provenance)
    search = pd.read_parquet(tmp_path/'hpo/search/separate/scores.parquet')
    assert (search.end < '2018-01-01').all()
    assert len(search) == 40  # 10 paired rows x 2 variants x 2 models; crossing label excluded
    with pytest.raises(ValueError, match='identity|provenance'):
        study.run(frame, stage='select', provenance={'sources': 'changed'})
    selected = study.run(frame, stage='select', provenance=provenance)
    assert selected['models']['candidate']['params']['seeds'] == [11, 29]
    assert selected['screen_spec_hash'] != selected['final_spec_hash']
    assert selected['variants']['reference'] == 'raw'
    assert selected['selection_guard'] == config['experiment']['selection_guard']
    assert selected['selection_guard_evidence']['separate'][0]['feasible']
    study.run(frame, stage='evaluate', partition='development', provenance=provenance)
    study.run(frame, stage='evaluate', partition='later', provenance=provenance)
    report = study.run(frame, stage='report', provenance=provenance)
    assert report['selected_variants_from_development'] == selected['variants']
    assert all(m['n'] == 10 for m in report['metrics'])
    completion = json.loads((tmp_path/'hpo/report/complete.json').read_text())
    assert completion['stage'] == 'report'
    assert completion['resource']['peak_rss_kib'] > 0
    assert completion['resource']['stage_wall_seconds'] >= 0
    assert (tmp_path/'hpo/report/convergence.json').exists()
    with pytest.raises(FileExistsError):
        study.run(frame, stage='search', partition='separate', provenance=provenance)
    scores_path = tmp_path/'hpo/search/separate/scores.parquet'
    search.iloc[:-1].to_parquet(scores_path, index=False)
    with pytest.raises(ValueError, match='hash|artifact'):
        study.run(frame, stage='select', provenance=provenance)


def test_hpo_selection_guard_filters_by_every_group_and_persists_evidence(tmp_path):
    import dskit.pipeline.libs.predictive_cdf as pack

    config, _ = _hpo_fixture(tmp_path)
    config['experiment']['selection_guard'] = {
        'reference': 'reference', 'variant': 'raw',
        'metrics': ['below_05', 'above_95'], 'target': .05, 'tolerance': 1e-12}
    study = pack.CDFHyperparameterStudy(config)
    rows = []
    values = {
        ('reference', 'raw'): {'A': (.08, .07), 'B': (.02, .09)},
        ('candidate', 'raw'): {'A': (.07, .06), 'B': (.03, .08)},
        ('candidate', 'calibrated'): {'A': (.09, .06), 'B': (.03, .08)},
    }
    for (model, variant), groups in values.items():
        for group, (below, above) in groups.items():
            rows.append({'unit': group, 'model': model, 'variant': variant,
                         'below_05': below, 'above_95': above})
    eligible, evidence = study._guard(
        pd.DataFrame(rows), [('candidate', 'raw'), ('candidate', 'calibrated')])
    assert eligible == [('candidate', 'raw')]
    assert evidence[0]['feasible'] and not evidence[1]['feasible']
    assert len(evidence[0]['comparisons']) == 4
    failed = [row for row in evidence[1]['comparisons'] if not row['passed']]
    assert failed == [{
        'group': 'A', 'metric': 'below_05', 'candidate_mean': .09,
        'reference_mean': .08, 'candidate_deviation': pytest.approx(.04),
        'reference_deviation': pytest.approx(.03), 'passed': False}]
    with pytest.raises(ValueError, match='no candidate'):
        study._guard(pd.DataFrame(rows), [('candidate', 'calibrated')])


def test_hpo_selection_guard_enforces_equal_cell_improvement_and_tail_scores(tmp_path):
    import dskit.pipeline.libs.predictive_cdf as pack

    config, _ = _hpo_fixture(tmp_path)
    config['experiment']['selection_guard'] = {
        'reference': 'reference', 'variant': 'raw',
        'metrics': ['below_05'], 'target': .05, 'tolerance': 1e-12,
        'cell_improvement_metrics': ['crps'],
        'cell_noninferiority_metrics': [
            'lower_tail_quantile_score', 'upper_tail_quantile_score']}
    study = pack.CDFHyperparameterStudy(config)
    rows = []
    for model, crps, lower, upper in [
            ('reference', 1., .4, .3), ('candidate', .9, .39, .31)]:
        for group in ('A', 'B'):
            for horizon in (1, 2):
                rows.append({'unit': group, 'h': horizon, 'model': model,
                             'variant': 'raw', 'below_05': .05, 'crps': crps,
                             'lower_tail_quantile_score': lower,
                             'upper_tail_quantile_score': upper})
    with pytest.raises(ValueError, match='no candidate'):
        study._guard(pd.DataFrame(rows), [('candidate', 'raw')])
    for row in rows:
        if row['model'] == 'candidate':
            row['upper_tail_quantile_score'] = .29
    eligible, evidence = study._guard(pd.DataFrame(rows), [('candidate', 'raw')])
    assert eligible == [('candidate', 'raw')]
    assert {row.get('comparison') for row in evidence[0]['comparisons']} >= {
        'cell_strict_improvement', 'cell_noninferiority'}


def test_hpo_selection_guard_refuses_invalid_contract(tmp_path):
    import dskit.pipeline.libs.predictive_cdf as pack

    config, _ = _hpo_fixture(tmp_path)
    config['experiment']['selection_guard'] = {
        'reference': 'missing', 'variant': 'raw', 'metrics': ['below_05'],
        'target': .05, 'tolerance': 0}
    with pytest.raises(ValueError, match='selection guard'):
        pack.CDFHyperparameterStudy(config)


@pytest.mark.parametrize('mutation', ['head_nan', 'wrong_symbol', 'missing_cell', 'candidate_label', 'seed', 'resolution', 'extra_key'])
def test_hpo_refuses_invalid_contract_before_fits(tmp_path, mutation):
    import dskit.pipeline.libs.predictive_cdf as pack
    assert hasattr(pack, 'CDFHyperparameterStudy'), 'standard JSON HPO orchestration is missing'
    config, frame = _hpo_fixture(tmp_path)
    if mutation == 'head_nan':
        frame.loc[0, 'is_A'] = np.nan
    elif mutation == 'wrong_symbol':
        frame.loc[0, ['is_A', 'is_B']] = [0, 1]
    elif mutation == 'missing_cell':
        config['experiment']['expected_cells']['development']['A'] = [1, 2]
    elif mutation == 'candidate_label':
        config['experiment']['candidate_labels']['candidate']['family'] = 'student'
    elif mutation == 'seed':
        config['experiment']['candidates']['candidate']['params']['seeds'] = [29]
    elif mutation == 'resolution':
        config['experiment']['resolutions']['final_samples'] = 99
    else:
        config['experiment']['accidental_typo'] = True
    with pytest.raises(ValueError):
        pack.CDFHyperparameterStudy(config).run(frame, stage='search', partition='separate', provenance={'fixture': 1})
    assert not (tmp_path/'hpo/search/separate').exists()


@pytest.mark.parametrize('band_year', [2014, 2016, 2017])
def test_direct_study_rejects_raw_head_nan_before_imputation(tmp_path, band_year):
    config, frame = _hpo_fixture(tmp_path)
    c = config['study']
    c['years'] = [2017]
    c['models'] = {'head': {**config['experiment']['candidates']['candidate'],
                          'params': {**config['experiment']['candidates']['candidate']['params'], 'head_features': [2, 3]},
                          'pooled': True}}
    frame.loc[(frame.date.str[:4] == str(band_year)) & (frame.unit == 'A'), 'is_A'] = np.nan
    with pytest.raises(ValueError, match='head'):
        ChronologicalCDFStudy(c).run(frame)
    assert not (tmp_path/'unused/scores.parquet').exists()


@pytest.mark.parametrize('defect', ['missing', 'substituted', 'duplicate', 'extra_model', 'nan_score', 'wrong_year'])
def test_hpo_pairing_checks_actual_identities_not_counts(tmp_path, defect):
    import dskit.pipeline.libs.predictive_cdf as pack
    config, frame = _hpo_fixture(tmp_path)
    study = pack.CDFHyperparameterStudy(config)
    expected = frame[frame.date.str.startswith('2017') & (frame.end < '2018-01-01')]
    scores = pd.concat([expected.assign(model='reference', variant=v, crps=1., year=2017)
                        for v in ['raw', 'calibrated']], ignore_index=True)
    study._check_scores(scores, expected, ['reference'])
    if defect == 'missing':
        scores = scores.iloc[:-1]
    elif defect == 'substituted':
        scores.loc[0, 'expiry'] = '2017-04-01'
    elif defect == 'duplicate':
        scores = pd.concat([scores, scores.iloc[:1]])
    elif defect == 'extra_model':
        scores.loc[0, 'model'] = 'unknown'
    elif defect == 'nan_score':
        scores.loc[0, 'crps'] = np.nan
    else:
        scores.loc[0, 'year'] = 2019
    with pytest.raises(ValueError):
        study._check_scores(scores, expected, ['reference'])


def test_convex_curve_exact_endpoints_inverse_and_flat_state():
    import dskit.pipeline.libs.predictive_cdf as pack
    assert hasattr(pack, 'ConvexCurve'), 'convex curve is missing'
    ConvexCurve = pack.ConvexCurve
    left = GridCurve([[-2., 0., 2.]], [[0., .7, 1.]])
    right = MixtureCurve([[1.]], [[1.]], [[.5]])
    values = np.array([-4., -1., 0., 1., 4.])
    for weight, expected in [(0., left), (1., right)]:
        curve = ConvexCurve(left, right, weight)
        np.testing.assert_allclose(curve.cdf(values), expected.cdf(values), rtol=0, atol=0)
        np.testing.assert_allclose(curve.quantile([1e-12, .5, 1-1e-12]),
                                   expected.quantile([1e-12, .5, 1-1e-12]), rtol=0, atol=0)
    curve = ConvexCurve(left, right, .25)
    np.testing.assert_allclose(curve.cdf(values), .75*left.cdf(values)+.25*right.cdf(values))
    p = np.array([.01, .2, .5, .8, .99])
    np.testing.assert_allclose(curve.cdf(curve.quantile(p)), [p], atol=2e-10)
    state = curve._arrays()
    assert state['kind'] == 'convex' and state['weight'] == .25
    assert state['left_kind'] == 'grid' and state['right_kind'] == 'mixture'
    for weight in [-.1, 1.1, np.nan, True]:
        with pytest.raises(ValueError):
            ConvexCurve(left, right, weight)
    with pytest.raises(ValueError, match='rows'):
        ConvexCurve(left, MixtureCurve([[1.], [1.]], [[0.], [1.]], [[1.], [1.]]), .5)


def test_quantile_forest_and_ngboost_return_valid_honest_curves():
    import dskit.pipeline.libs.predictive_cdf as pack
    assert hasattr(pack, 'QuantileForestCDF') and hasattr(pack, 'NGBoostCDF')
    QuantileForestCDF, NGBoostCDF = pack.QuantileForestCDF, pack.NGBoostCDF
    rng = np.random.default_rng(18)
    x = rng.normal(size=(90, 3))
    y = x[:, 0]+rng.normal(scale=.4, size=90)
    forest = QuantileForestCDF(knots=31, trees=12, min_child=4,
                               max_features=.7, threads=1, seed=9)
    forest.fit(x[:70], y[:70], x[70:80], y[70:80])
    fc = forest.curve(x[80:])
    assert isinstance(fc, GridCurve)
    assert (np.diff(fc.values, axis=1) >= 0).all()
    assert (fc.probabilities[:, 0] == 0).all() and (fc.probabilities[:, -1] == 1).all()
    assert fc.cdf(fc.values[:, :1]-1).max() == 0
    assert fc.cdf(fc.values[:, -1:]+1).min() == 1

    boosted = NGBoostCDF(trees=12, depth=2, min_child=4, learning_rate=.05,
                         minibatch_frac=.8, col_sample=1., tol=1e-4, seed=9)
    boosted.fit(x[:70], y[:70], x[70:80], y[70:80])
    bc = boosted.curve(x[80:])
    assert isinstance(bc, MixtureCurve)
    assert np.isfinite(bc.means).all() and (bc.scales > 0).all()
    dist = boosted.model.pred_dist(x[80:])
    np.testing.assert_allclose(bc.means[:, 0], dist.params['loc'])
    np.testing.assert_allclose(bc.scales[:, 0], dist.params['scale'])


def test_conditioned_empirical_and_blend_match_both_controls():
    import dskit.pipeline.libs.predictive_cdf as pack
    assert hasattr(pack, 'EmpiricalMLPBlendCDF'), 'blend estimator is missing'
    EmpiricalMLPBlendCDF = pack.EmpiricalMLPBlendCDF
    x = np.array([[1., 1., 1, 0], [1., 1., 1, 0],
                  [1., 1., 0, 1], [1., 1., 0, 1]])
    y = np.array([-2., 2., 10., 12.])
    empirical = HorizonEmpiricalCDF(0, 1, 9, condition_indices=[0, 2, 3]).fit(x, y, x, y)
    assert empirical.curve(x[[0]]).quantile(.5)[0, 0] < 5
    assert empirical.curve(x[[2]]).quantile(.5)[0, 0] > 5

    settings = {'components': 1, 'hidden': [4], 'epochs': 1, 'batch_size': 8,
                'seeds': [7], 'device': 'cpu', 'deterministic': True}
    mlp = MixtureMLPCDF(**settings).fit(x, y, x, y)
    left = EmpiricalMLPBlendCDF(0., [0, 2, 3], 1, 9, settings).fit(x, y, x, y)
    right = EmpiricalMLPBlendCDF(1., [0, 2, 3], 1, 9, settings).fit(x, y, x, y)
    query = np.array([-20., 0., 5., 20.])
    np.testing.assert_allclose(left.curve(x).cdf(query), empirical.curve(x).cdf(query), rtol=0, atol=0)
    np.testing.assert_allclose(right.curve(x).cdf(query), mlp.curve(x).cdf(query), rtol=0, atol=0)
    assert right._equivalence_state() == mlp._equivalence_state()
    bad = x.copy()
    bad[0, 2] = np.nan
    with pytest.raises(ValueError, match='condition'):
        left._validate_x(bad)


def test_grouped_hpo_inventory_freezes_specs_and_dependency_versions(tmp_path):
    import copy
    import dskit.pipeline.libs.predictive_cdf as pack

    config, _ = _hpo_fixture(tmp_path)
    e = config['experiment']
    candidate = copy.deepcopy(e['candidates']['candidate'])
    candidate['params']['seeds'] = [11, 29]
    e['candidates'] = {name: copy.deepcopy(candidate) for name in ['forest_a', 'ngboost_a', 'blend_a']}
    for key in ['axes', 'candidate_labels', 'screen_seed', 'final_seeds']:
        del e[key]
    e['max_candidates'] = 3
    e['candidate_groups'] = {name: [name+'_a'] for name in ['forest', 'ngboost', 'blend']}
    e['search_partitions'] = copy.deepcopy(e['candidate_groups'])
    study = pack.CDFHyperparameterStudy(config)
    assert study.grouped
    assert study._dependency_versions() == {}
    assert set(study.experiment['candidate_groups']) == {'forest', 'ngboost', 'blend'}
    bad = copy.deepcopy(config)
    bad['experiment']['candidate_groups']['forest'].append('blend_a')
    with pytest.raises(ValueError, match='group'):
        pack.CDFHyperparameterStudy(bad)
    peerless = copy.deepcopy(config)
    peerless['study']['models']['reference']['equivalence'] = 'endpoint'
    with pytest.raises(ValueError, match='equivalence'):
        pack.CDFHyperparameterStudy(peerless)


def test_equivalence_ledger_verifies_peers_and_records_singletons(tmp_path, monkeypatch):
    import copy
    import json
    import dskit.pipeline.libs.predictive_cdf as pack

    class EqSpy(pack.CDFEstimator):
        def __init__(self, state):
            self.state = state

        def fit(self, x, y, cal_x, cal_y):
            self.fitted_state = f'{self.state}:{float(np.mean(y))}'
            return self

        def curve(self, x):
            return MixtureCurve(np.ones((len(x), 1)), np.zeros((len(x), 1)), np.ones((len(x), 1)))

        def _equivalence_state(self):
            return self.fitted_state

    monkeypatch.setattr(pack, '_EqSpy', EqSpy, raising=False)
    config, frame = _hpo_fixture(tmp_path)
    base = config['study']
    base['years'] = [2017, 2018]
    spec = {'class': 'dskit.pipeline.libs.predictive_cdf:_EqSpy', 'params': {'state': 'same'},
            'calibrate': False, 'pooled': True, 'equivalence': 'endpoint'}
    base['models'] = {'control': spec}
    base['reference_model'] = 'control'
    base['comparison_references'] = ['control']
    base['output'] = str(tmp_path/'singleton')
    ChronologicalCDFStudy(base).run(frame)
    evidence = json.loads((tmp_path/'singleton/equivalence.json').read_text())
    assert [(row['year'], row['status']) for row in evidence] == [(2017, 'unverified'),
                                                                  (2018, 'unverified')]
    assert all(row['members'] == ['control'] for row in evidence)

    paired = copy.deepcopy(base)
    paired['output'] = str(tmp_path/'paired')
    paired['models']['blend'] = copy.deepcopy(spec)
    ChronologicalCDFStudy(paired).run(frame)
    evidence = json.loads((tmp_path/'paired/equivalence.json').read_text())
    assert [(row['year'], row['status']) for row in evidence] == [(2017, 'verified'),
                                                                  (2018, 'verified')]
    assert all(row['members'] == ['blend', 'control'] for row in evidence)

    mismatch = copy.deepcopy(paired)
    mismatch['output'] = str(tmp_path/'mismatch')
    mismatch['models']['blend']['params']['state'] = 'changed'
    with pytest.raises(ValueError, match='equivalence state'):
        ChronologicalCDFStudy(mismatch).run(frame)


def test_optional_dependency_versions_are_identity_inputs(tmp_path, monkeypatch):
    import dskit.pipeline.libs.predictive_cdf as pack
    config, _ = _hpo_fixture(tmp_path)
    study = pack.CDFHyperparameterStudy(config)
    study.experiment['candidates'] = {
        'forest': {'class': 'dskit.pipeline.libs.predictive_cdf:QuantileForestCDF'},
        'boost': {'class': 'dskit.pipeline.libs.predictive_cdf:NGBoostCDF'},
    }
    monkeypatch.setattr('importlib.metadata.version', lambda name: {'ngboost': '0.5.11',
                                                                   'quantile-forest': '1.4.2'}[name])
    assert study._dependency_versions() == {'ngboost': '0.5.11', 'quantile-forest': '1.4.2'}


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'mixed', 'partition'])
def test_grouped_hpo_refuses_inventory_and_grammar_substitution(tmp_path, mutation):
    import copy
    import dskit.pipeline.libs.predictive_cdf as pack
    config, _ = _hpo_fixture(tmp_path)
    e = config['experiment']
    candidate = copy.deepcopy(e['candidates']['candidate'])
    candidate['params']['seeds'] = [11, 29]
    e['candidates'] = {name: copy.deepcopy(candidate) for name in ['forest_a', 'ngboost_a', 'blend_a']}
    for key in ['axes', 'candidate_labels', 'screen_seed', 'final_seeds']:
        del e[key]
    e['max_candidates'] = 3
    e['candidate_groups'] = {name: [name+'_a'] for name in ['forest', 'ngboost', 'blend']}
    e['search_partitions'] = copy.deepcopy(e['candidate_groups'])
    if mutation == 'duplicate':
        e['candidate_groups']['forest'].append('blend_a')
        e['search_partitions'] = copy.deepcopy(e['candidate_groups'])
    elif mutation == 'missing':
        e['candidate_groups']['blend'] = []
        e['search_partitions'] = copy.deepcopy(e['candidate_groups'])
    elif mutation == 'mixed':
        e['screen_seed'] = 11
    else:
        e['search_partitions']['forest'] = ['ngboost_a']
    with pytest.raises(ValueError):
        pack.CDFHyperparameterStudy(config)


@pytest.mark.parametrize('factory', [
    lambda: QuantileForestCDF(knots=2),
    lambda: QuantileForestCDF(knots=11, max_features=0),
    lambda: QuantileForestCDF(knots=11, max_samples_leaf=0),
    lambda: NGBoostCDF(trees=0),
    lambda: NGBoostCDF(minibatch_frac=1.1),
])
def test_new_estimator_invalid_parameters_refuse(factory):
    with pytest.raises(ValueError):
        factory()


def test_deterministic_cuda_requires_prelaunch_workspace_and_restores_flags(monkeypatch):
    import torch
    monkeypatch.delenv('CUBLAS_WORKSPACE_CONFIG', raising=False)
    model = MixtureMLPCDF(device='cuda', deterministic=True)
    with pytest.raises(ValueError, match='CUBLAS_WORKSPACE_CONFIG'):
        with model._deterministic_context():
            pass
    cpu = MixtureMLPCDF(device='cpu', deterministic=True)
    before = torch.are_deterministic_algorithms_enabled()
    with cpu._deterministic_context():
        assert torch.are_deterministic_algorithms_enabled()
    assert torch.are_deterministic_algorithms_enabled() == before


def test_stage_dependency_version_change_invalidates_completed_search(tmp_path, monkeypatch):
    import dskit.pipeline.libs.predictive_cdf as pack
    config, frame = _hpo_fixture(tmp_path)
    study = pack.CDFHyperparameterStudy(config)
    provenance = {'fixture': 1}
    monkeypatch.setattr(study, '_dependency_versions', lambda: {'ngboost': '0.5.11'})
    study.run(frame, stage='search', partition='separate', provenance=provenance)
    monkeypatch.setattr(study, '_dependency_versions', lambda: {'ngboost': '0.5.12'})
    with pytest.raises(ValueError, match='identity'):
        study.run(frame, stage='select', provenance=provenance)


def test_audit_uses_forecast_identity_not_dataframe_index(tmp_path):
    import dskit.pipeline.libs.predictive_cdf as pack
    config, frame = _hpo_fixture(tmp_path)
    study = pack.CDFHyperparameterStudy(config)
    frame = frame.iloc[:2].copy()
    frame.index = [0, 0]
    path = tmp_path/'curves'
    path.mkdir()
    np.savez_compressed(
        path/'fixture-curves.npz', kind='mixture', weights=np.ones((2, 1)),
        means=np.zeros((2, 1)), scales=np.ones((2, 1)), draws=np.zeros((2, 21)),
        row_index=np.array([0, 0]),
        identities=frame[config['study']['identity']].astype(str).to_numpy(dtype=str))
    records = study._audit([path], frame, None)
    assert records[0]['rows'] == 2


def test_audit_restores_sampled_convex_row_weights_exactly(tmp_path, monkeypatch):
    import dskit.pipeline.libs.predictive_cdf as pack
    config, frame = _hpo_fixture(tmp_path)
    study = pack.CDFHyperparameterStudy(config)
    frame = frame.iloc[:3].copy()
    left = GridCurve(np.tile([[-1., 1.]], (3, 1)), [0., 1.])
    right = GridCurve(np.tile([[0., 2.]], (3, 1)), [0., 1.])
    curve = ConvexCurve(left, right, [0., .5, 1.])
    path = tmp_path/'curves'
    path.mkdir()
    np.savez_compressed(path/'rows-curves.npz', **curve._arrays(),
                        row_index=np.arange(3), draws=np.zeros((3, 21)),
                        identities=frame[config['study']['identity']].astype(str).to_numpy(dtype=str))
    restored = []
    original = pack.ConvexCurve

    class CapturedConvexCurve(original):
        def __init__(self, left, right, weight):
            restored.append(np.asarray(weight).copy())
            super().__init__(left, right, weight)

    monkeypatch.setattr(pack, 'ConvexCurve', CapturedConvexCurve)
    assert study._audit([path], frame, None)[0]['rows'] == 2
    np.testing.assert_array_equal(restored[0], [[0.], [1.]])


def test_grouped_multiyear_flow_verifies_blend_and_audits_convex_curves(tmp_path):
    import copy
    import json
    import dskit.pipeline.libs.predictive_cdf as pack
    config, frame = _hpo_fixture(tmp_path)
    c, e = config['study'], config['experiment']
    c['years'], c['development_end'] = [2017, 2018, 2019], 2018
    settings = {'components': 1, 'hidden': [3], 'epochs': 1, 'batch_size': 16,
                'seeds': [11], 'device': 'cpu', 'deterministic': True}
    c['models']['pooled_mlp'] = {
        'class': 'dskit.pipeline.libs.predictive_cdf:MixtureMLPCDF',
        'params': settings, 'calibrate': True, 'pooled': True, 'equivalence': 'endpoint'}
    c['comparison_references'] = ['reference', 'pooled_mlp']
    empirical = copy.deepcopy(c['models']['reference'])
    empirical['pooled'] = True
    scaled = {'class': 'dskit.pipeline.libs.predictive_cdf:ScaledEmpiricalCDF',
              'params': {'alpha': 10, 'floor': .02, 'knots': 21},
              'calibrate': True, 'pooled': True}
    blend = {'class': 'dskit.pipeline.libs.predictive_cdf:EmpiricalMLPBlendCDF',
             'params': {'mlp_weight': .25, 'condition_indices': [0, 2, 3],
                        'reference_index': 1, 'knots': 21, 'mlp': settings},
             'calibrate': True, 'pooled': True, 'equivalence': 'endpoint'}
    e['development_years'], e['label_cutoff'] = [2017, 2018], '2019-01-01'
    e['candidates'] = {'forest_a': empirical, 'ngboost_a': scaled, 'blend_a': blend}
    for key in ['axes', 'candidate_labels', 'screen_seed', 'final_seeds']:
        del e[key]
    e['max_candidates'] = 3
    e['candidate_groups'] = {name: [name+'_a'] for name in ['forest', 'ngboost', 'blend']}
    e['search_partitions'] = copy.deepcopy(e['candidate_groups'])
    e['evaluation_partitions'] = {'development': [2017, 2018], 'later': [2019]}
    study = pack.CDFHyperparameterStudy(config)
    provenance = {'fixture': 1}
    for partition in e['search_partitions']:
        study.run(frame, stage='search', partition=partition, provenance=provenance)
    selected = study.run(frame, stage='select', provenance=provenance)
    assert {'forest_a', 'ngboost_a', 'blend_a'}.issubset(selected['models'])
    for partition in e['evaluation_partitions']:
        study.run(frame, stage='evaluate', partition=partition, provenance=provenance)
    study.run(frame, stage='report', provenance=provenance)
    evidence = json.loads((tmp_path/'hpo/evaluate/later/equivalence.json').read_text())
    assert len(evidence) == 1
    assert evidence[0]['year'] == 2019 and evidence[0]['status'] == 'verified'
    convergence = json.loads((tmp_path/'hpo/report/convergence.json').read_text())
    assert any('blend_a' in row['file'] for row in convergence)


def test_torch_cdf_json_encoder_and_loss_seam_exists():
    assert hasattr(predictive_cdf, "TorchCDF"), "generic JSON Torch CDF missing"


def test_decision_hpo_ranks_local_score_not_global_crps(tmp_path):
    config, _ = _hpo_fixture(tmp_path)
    config["experiment"]["selection_metric"] = "decision_strike_brier"
    config["study"]["decision_context"] = "context"
    study = CDFHyperparameterStudy(config)
    rows = []
    for name, local, global_score in [("reference", .2, .2), ("candidate", .1, .4)]:
        for variant in ("raw", "calibrated"):
            rows.append(dict(unit="A", h=1, model=name, variant=variant,
                             crps=global_score, decision_strike_brier=local))
    rank = study._rank(pd.DataFrame(rows))
    assert rank[("candidate", "raw")] == pytest.approx(.5)


@pytest.mark.parametrize("kind", ["mlp", "gru"])
def test_torch_cdf_encoders_are_row_local_and_calibration_does_not_train(kind):
    torch = pytest.importorskip("torch")
    torch.set_num_threads(1)
    encoder = {"kind": "mlp"} if kind == "mlp" else {
        "kind": "gru", "sequence_indices": [[2], [1], [0]],
        "context_indices": [3], "hidden_size": 4, "num_layers": 1}
    losses = [{"kind": "nll", "weight": .1}, {"kind": "decision_brier", "weight": 1.},
              {"kind": "decision_log", "weight": .2}]
    x = np.random.default_rng(1).normal(size=(6, 4))
    y = np.linspace(-1, 1, 6)
    context = [{"identity": [str(i)], "thresholds": [-.5, .5], "weights": [.5, .5]}
               for i in range(6)]
    def fit(cal_y):
        model = predictive_cdf.TorchCDF(
            encoder=encoder, losses=losses, components=2, hidden=[4],
            epochs=2, batch_size=3, seeds=[7], device="cpu", deterministic=True)
        return model.fit_decision_context(context, context).fit(x, y, x, cal_y)
    model = fit(y)
    other = fit(y+100)
    np.testing.assert_allclose(model.curve(x).cdf([0]), other.curve(x).cdf([0]))
    expected = model.curve(x).cdf([-.3, .4])
    np.testing.assert_allclose(model.curve(x[::-1].copy()).cdf([-.3, .4])[::-1], expected, atol=1e-7)
    np.testing.assert_allclose(model.curve(x[:1]).cdf([-.3, .4]), expected[:1], atol=1e-7)
    report = model.loss_report(x, y, context)
    assert report["n"] == report["eligible_n"] == 6
    assert report["threshold_n"] == 12
    assert report["composite"] == pytest.approx(sum(
        term["mean"]*term["weight"] for term in report["terms"].values()))
    assert np.isfinite(report["composite"])
    # Genuine ordered sequence: flipping lag columns alters the fitted GRU forecast.
    if kind == "gru":
        changed = x.copy()
        changed[:, :3] = changed[:, :3][:, ::-1]
        assert not np.allclose(model.curve(changed).cdf([0]), model.curve(x).cdf([0]))


def test_torch_loss_proper_scores_and_eligible_denominator():
    import torch
    y = torch.tensor([[0.], [9.]])
    logw, mu, sigma = torch.zeros((2, 1)), torch.zeros((2, 1), requires_grad=True), torch.ones((2, 1))
    context = {"thresholds": torch.zeros((2, 1)),
               "weights": torch.tensor([[1.], [0.]])}
    model = predictive_cdf.TorchCDF(
        encoder={"kind": "mlp"}, losses=[{"kind": "nll", "weight": .1},
                                       {"kind": "decision_brier", "weight": 2.},
                                       {"kind": "decision_log", "weight": 3.}],
        device="cpu")
    loss = model._objective_loss(y, logw, mu, sigma, torch.arange(2), context)
    nll = .5*np.log(2*np.pi)+81/4
    assert loss.item() == pytest.approx(.1*nll + 2*.25 + 3*np.log(2))
    loss.backward()
    assert torch.isfinite(mu.grad).all()
    assert mu.grad[0].item() != 0  # local score supplies an actual gradient


@pytest.mark.parametrize("mutation", [
    lambda e,terms: e.update(kind="lstm"),
    lambda e,terms: e.update(unknown=1),
    lambda e,terms: terms.append(dict(terms[0])),
    lambda e,terms: terms[0].update(weight=-1),
    lambda e,terms: terms[0].update(weight=float("nan")),
    lambda e,terms: terms[1].update(kind="unknown"),
])
def test_torch_cdf_refuses_invalid_json(mutation):
    encoder = {"kind": "mlp"}
    losses = [{"kind": "nll", "weight": .1}, {"kind": "decision_brier", "weight": 1.}]
    mutation(encoder, losses)
    with pytest.raises(ValueError):
        predictive_cdf.TorchCDF(encoder=encoder, losses=losses)


def test_generic_decision_scores_count_excluded_and_accept_other_identity_shapes():
    scorer = predictive_cdf.DecisionRegionScores([
        {"identity": ["one"], "thresholds": [0.], "weights": [1.]},
        {"identity": ["two", "region"], "thresholds": [], "weights": []}])
    result = scorer.score(MixtureCurve([[1], [1]], [[0], [0]], [[1], [1]]), [0., 2.])
    assert result["decision_strike_brier"][0] == pytest.approx(.25)
    assert np.isnan(result["decision_strike_brier"][1])
    assert result["decision_strike_count"].tolist() == [1, 0]


def _decision_hpo_fixture(tmp_path):
    config, frame = _hpo_fixture(tmp_path)
    e, c = config["experiment"], config["study"]
    for key in ("final_seeds", "screen_seed", "candidate_labels", "axes"):
        e.pop(key)
    e["candidate_groups"] = e["search_partitions"]
    e["selection_metric"] = "decision_strike_brier"
    c["decision_context"] = "context"
    c["decision_acceptance"] = {"blocks": [1], "min_lower_bound": 0.,
                                "max_bias_increase": .01}
    e["candidates"]["candidate"] = {
        "class": "dskit.pipeline.libs.predictive_cdf:TorchCDF",
        "params": {"encoder": {"kind": "mlp"}, "losses": [
            {"kind": "nll", "weight": .1}, {"kind": "decision_brier", "weight": 1.}],
            "components": 1, "hidden": [3], "epochs": 1, "seeds": [11], "device": "cpu"},
        "calibrate": False}
    contexts = []
    for row in frame.itertuples():
        eligible = not row.date.endswith("01")
        contexts.append({"identity": [row.unit, row.date, row.expiry],
                         "thresholds": [-.3, .3] if eligible else [],
                         "weights": [.5, .5] if eligible else []})
    frame["context"] = contexts
    return config, frame


def test_decision_hpo_end_to_end_keeps_exclusions_and_telemetry(tmp_path):
    import json
    config, frame = _decision_hpo_fixture(tmp_path)
    study = CDFHyperparameterStudy(config)
    provenance = {"fixture": "causal"}
    study.run(frame, stage="search", partition="separate", provenance=provenance)
    chosen = study.run(frame, stage="select", provenance=provenance)
    assert chosen["selection_metric"] == "decision_strike_brier"
    for partition in ("development", "later"):
        study.run(frame, stage="evaluate", partition=partition, provenance=provenance)
    report = study.run(frame, stage="report", provenance=provenance)
    local = next(r for r in report["metrics"] if r["model"] == "candidate"
                 and r["metric"] == "decision_strike_brier")
    assert local["n"] == 8 and local["total_n"] == 10 and local["excluded_n"] == 2
    assert local["threshold_n"] == 16
    assert "candidate" in report["decision_acceptance"]
    counts = json.loads((tmp_path/"hpo/evaluate/later/counts.json").read_text())
    for band in ("training", "calibration", "validation"):
        values = counts[0]["candidate_losses"][band]
        assert values["composite"] > 0
        assert values["n"] > values["eligible_n"] > 0
        assert values["decision_skill_pct"] is not None
    assert "candidate_training_nll_by_seed" not in counts[0]
    scores = pd.read_parquet(tmp_path/"hpo/report/scores.parquet")
    scores.loc[scores.model == "candidate", ["crps", "tail_crps"]] *= 1000
    scores.loc[scores.model == "candidate", ["below_05", "above_95"]] = .99
    destination = tmp_path/"perturbed"
    destination.mkdir()
    altered = ChronologicalCDFStudy({
        **config["study"], "models": chosen["models"], "output": str(destination)})
    second = altered.summarize(scores, chosen["variants"])
    assert second["decision_acceptance"] == report["decision_acceptance"]



@pytest.mark.parametrize("defect", ["eligibility", "missing", "zero_reference"])
def test_decision_pairing_and_denominator_refuse(tmp_path, defect):
    config, frame = _decision_hpo_fixture(tmp_path)
    study = CDFHyperparameterStudy(config)
    expected = frame[frame.date.str.startswith("2017") & (frame.end < "2018-01-01")]
    rows = []
    for name in ("reference", "candidate"):
        for variant in ("raw", "calibrated"):
            for row in expected.itertuples():
                rows.append(dict(unit=row.unit, date=row.date, expiry=row.expiry,
                                 end=row.end, h=row.h, year=2017, model=name, variant=variant,
                                 crps=.5, decision_strike_brier=.2, decision_strike_count=2))
    scores = pd.DataFrame(rows)
    if defect == "eligibility":
        scores.loc[0, "decision_strike_count"] = 0
        scores.loc[0, "decision_strike_brier"] = np.nan
    elif defect == "missing":
        scores.loc[0, "decision_strike_brier"] = np.nan
    else:
        scores.loc[scores.model == "reference", "decision_strike_brier"] = 0
    with pytest.raises(ValueError):
        study._check_scores(scores, expected, ["reference", "candidate"])
        study._rank(scores)


@pytest.mark.parametrize("loss_kind", ["brier", "log"])
@pytest.mark.parametrize("threshold", [.7, -.7, .1])
@pytest.mark.parametrize("direction", [-1, 0, 1])
def test_decision_threshold_precision_at_equality_and_neighbors(threshold, direction, loss_kind):
    import torch
    y = np.nextafter(threshold, -np.inf if direction < 0 else np.inf) if direction else threshold
    scorer = predictive_cdf.DecisionRegionScores([
        {"identity": ["row"], "thresholds": [threshold], "weights": [1.]}])
    curve = MixtureCurve([[1]], [[threshold-1.2815515655446004]], [[1]])
    truth = float(y <= threshold)
    p = curve.cdf([[threshold]])[0, 0]
    score = scorer.score(curve, [y])
    assert score["decision_strike_brier"][0] == pytest.approx((p-truth)**2, abs=1e-12)
    model = predictive_cdf.TorchCDF(encoder={"kind": "mlp"}, losses=[
        {"kind": "nll", "weight": .1}, {"kind": "decision_brier", "weight": 1.}],
        device="cpu")
    model.fit_decision_context([{"identity": ["row"], "thresholds": [threshold],
                                 "weights": [1.]}], [])
    target = model._target_tensor(np.array([y]))
    context = model._training_context(np.zeros((1, 1)))
    loss_class = predictive_cdf._CDFDecisionBrier if loss_kind == "brier" else predictive_cdf._CDFDecisionLog
    values, eligible = loss_class(model.family).values(
        target, torch.zeros((1,1)), torch.tensor([[threshold-1.2815515655446004]]),
        torch.ones((1,1)), context)
    assert eligible.item()
    expected_loss = (p-truth)**2 if loss_kind == "brier" else -np.log(p if truth else 1-p)
    assert values.item() == pytest.approx(expected_loss, abs=1e-7)


@pytest.mark.parametrize("candidate_pooled,reference_pooled", [(True,False),(False,True)])
def test_torch_telemetry_reuses_configured_reference_population(
        tmp_path, candidate_pooled, reference_pooled):
    import json
    config, frame = _decision_hpo_fixture(tmp_path)
    c = config["study"]
    c["models"]["reference"]["pooled"] = reference_pooled
    candidate = config["experiment"]["candidates"]["candidate"]
    candidate["pooled"] = candidate_pooled
    # Reference order must not be an undeclared dependency.
    c["models"] = {"candidate": candidate, **c["models"]}
    frame.loc[frame.unit == "B", "y"] += 5
    study = ChronologicalCDFStudy(c)
    scores = study.run(frame)
    counts = json.loads((tmp_path/"unused/counts.json").read_text())
    for row in counts:
        actual = scores[(scores.unit==row["group"]) & (scores.year==row["year"])
                        & (scores.model=="reference") & (scores.variant=="raw")]
        report = row["candidate_losses"]["validation"]
        assert report["reference_decision_brier"] == pytest.approx(actual.decision_strike_brier.mean())
        if actual.decision_strike_brier.mean() == 0:
            assert report["decision_skill_pct"] is None
        else:
            candidate_rows = scores[(scores.unit==row["group"]) & (scores.year==row["year"])
                        & (scores.model=="candidate") & (scores.variant=="raw")]
            assert report["decision_skill_pct"] == pytest.approx(
                100*(1-candidate_rows.decision_strike_brier.mean()/actual.decision_strike_brier.mean()))


@pytest.mark.parametrize("defect", ["duplicate", "missing", "negative", "ragged", "width"])
def test_torch_gru_refuses_invalid_feature_partitions(defect):
    encoder = {"kind": "gru", "sequence_indices": [[2], [1], [0]],
               "context_indices": [3], "hidden_size": 4, "num_layers": 1}
    if defect == "duplicate":
        encoder["context_indices"] = [0, 3]
    elif defect == "missing":
        encoder["context_indices"] = []
    elif defect == "negative":
        encoder["sequence_indices"][0] = [-1]
    elif defect == "ragged":
        encoder["sequence_indices"][0] = [2, 4]
    else:
        encoder["hidden_size"] = 0
    with pytest.raises(ValueError):
        model = predictive_cdf.TorchCDF(encoder=encoder, losses=[
            {"kind": "nll", "weight": .1}, {"kind": "decision_brier", "weight": 1.}],
            device="cpu")
        model._build_module(4)


_SUBSET_LOSSES = [{"kind": "nll", "weight": .1}, {"kind": "decision_brier", "weight": 1.}]
_SUBSET_GRU = {"kind": "gru", "sequence_indices": [[2], [1], [0]],
               "context_indices": [3], "hidden_size": 4, "num_layers": 1}


def _subset_fit(encoder, feature_indices, x, y):
    """Fit a one-seed CPU TorchCDF on the rows with the given subset (ADR-0224)."""
    context = [{"identity": [str(i)], "thresholds": [-.5, .5], "weights": [.5, .5]}
               for i in range(len(x))]
    extra = {} if feature_indices is None else {"feature_indices": feature_indices}
    model = predictive_cdf.TorchCDF(
        encoder=encoder, losses=_SUBSET_LOSSES, components=2, hidden=[4], epochs=2,
        batch_size=3, seeds=[7], device="cpu", deterministic=True, **extra)
    return model.fit_decision_context(context, context).fit(x, y, x, y)


@pytest.mark.parametrize("encoder, subset", [
    ({"kind": "mlp"}, [0, 2, 3]),
    # GRU positions are WITHIN the subset: the four kept columns, in subset order.
    (_SUBSET_GRU, [5, 3, 1, 0])])
def test_torch_cdf_feature_subset_ignores_excluded_columns_and_keeps_absent_digest(
        encoder, subset):
    pytest.importorskip("torch")
    x = np.random.default_rng(3).normal(size=(6, 6))
    y = np.linspace(-1, 1, 6)
    model = _subset_fit(encoder, subset, x, y)
    changed = x.copy()
    changed[:, [i for i in range(6) if i not in subset]] += 1000.
    np.testing.assert_array_equal(model.curve(x).cdf([-.3, .4]),
                                  model.curve(changed).cdf([-.3, .4]))
    assert model._equivalence_settings()["feature_indices"] == tuple(subset)
    full = _subset_fit(encoder, None, x[:, subset], y)
    assert "feature_indices" not in full._equivalence_settings()
    assert model._equivalence_state() != full._equivalence_state()
    # The subset is a column pick: the same columns fed whole give the same network.
    np.testing.assert_allclose(model.curve(x).cdf([-.3, .4]),
                               full.curve(x[:, subset]).cdf([-.3, .4]), atol=1e-7)


def test_torch_cdf_feature_subset_refuses_mixed_encoder_positions_and_bad_indices():
    pytest.importorskip("torch")
    x = np.random.default_rng(3).normal(size=(6, 6))
    y = np.linspace(-1, 1, 6)
    # Encoder positions read as raw columns (0..5) are not a partition of the 4 kept ones.
    raw = {**_SUBSET_GRU, "sequence_indices": [[5], [4], [3]], "context_indices": [0, 1, 2]}
    with pytest.raises(ValueError, match="within feature_indices"):
        _subset_fit(raw, [5, 3, 1, 0], x, y)
    with pytest.raises(ValueError, match="feature indices"):
        _subset_fit({"kind": "mlp"}, [0, 0], x, y)
    with pytest.raises(ValueError, match="feature indices outside input"):
        _subset_fit({"kind": "mlp"}, [0, 9], x, y)
    # Head routing and the legacy left-tail penalty stay refused beside the subset.
    for refused in ({"head_features": [0, 1]}, {"left_cdf_weight": .5}):
        with pytest.raises(ValueError, match="head_features and left_cdf_weight"):
            predictive_cdf.TorchCDF(encoder={"kind": "mlp"}, losses=_SUBSET_LOSSES,
                                    feature_indices=[2, 3], device="cpu", **refused)


@pytest.mark.parametrize("global_kind", ["nll", "crps"])
@pytest.mark.parametrize("local_kind", ["decision_brier", "decision_log", "wing_twcrps"])
def test_torch_composite_gradient_is_invariant_to_batch_eligibility(local_kind, global_kind):
    import torch
    model = predictive_cdf.TorchCDF(encoder={"kind": "mlp"}, losses=[
        {"kind": global_kind, "weight": .1}, {"kind": local_kind, "weight": 1.}],
        device="cpu")
    column = lambda *values: torch.tensor([[v] for v in values], dtype=torch.float64)
    context = {"thresholds": column(0., 0., 0., 0.), "weights": column(1., 0., 0., 0.),
               "lower": column(-.5, 0., 0., 0.), "upper": column(.5, 0., 0., 0.),
               "density": column(1., 0., 0., 0.)}
    def evaluate(partitions):
        mean = torch.tensor(0., requires_grad=True)
        total = mean*0
        for indices in partitions:
            ix = torch.tensor(indices)
            y = torch.zeros((len(ix), 1), dtype=torch.float64)
            logw, mu, sigma = torch.zeros_like(y), mean.expand_as(y), torch.ones_like(y)
            total = total + len(ix)/4*model._objective_loss(y, logw, mu, sigma, ix, context)
        total.backward()
        return total.item(), mean.grad.item()
    expected = evaluate([[0,1,2,3]])
    assert evaluate([[0],[1],[2],[3]]) == pytest.approx(expected)
    assert evaluate([[0,1],[2,3]]) == pytest.approx(expected)


@pytest.mark.parametrize("outcomes", [[0.], [[0.], [1.]], [0., float("nan")]])
def test_generic_decision_scorer_refuses_unpaired_outcomes(outcomes):
    scorer = predictive_cdf.DecisionRegionScores([
        {"identity": [str(i)], "thresholds": [0.], "weights": [1.]} for i in range(2)])
    with pytest.raises(ValueError, match="outcome"):
        scorer.score(MixtureCurve([[1],[1]], [[0],[0]], [[1],[1]]), outcomes)


def test_torch_loss_report_refuses_unpaired_feature_rows_before_prediction():
    model = predictive_cdf.TorchCDF(encoder={"kind": "mlp"}, losses=[
        {"kind": "nll", "weight": .1}, {"kind": "decision_brier", "weight": 1.}],
        device="cpu")
    context = [{"identity": [str(i)], "thresholds": [0.], "weights": [1.]} for i in range(2)]
    with pytest.raises(ValueError, match="paired"):
        model.loss_report(np.zeros((3, 1)), np.zeros(2), context)



# ---- ADR-0218: CRPS and condor-wing twCRPS ---------------------------------

_GAUSSIAN = {"kind": "gaussian"}
_STUDENT = {"kind": "student", "degrees": 5}


def _family(name, degrees=5):
    if name == "gaussian":
        return predictive_cdf._GaussianFamily(dict(_GAUSSIAN))
    return predictive_cdf._StudentFamily({"kind": "student", "degrees": degrees})


def _family_curve(name, w, m, s, degrees=5):
    if name == "gaussian":
        return MixtureCurve(w, m, s)
    return predictive_cdf.StudentMixtureCurve(w, m, s, degrees=degrees)


def _draw_mixture(rows=4, comps=3, seed=5):
    rng = np.random.default_rng(seed)
    w = rng.dirichlet(np.ones(comps), rows)
    m = rng.normal(0, 1.2, (rows, comps))
    s = np.exp(rng.uniform(np.log(.15), np.log(1.5), (rows, comps)))
    return w, m, s, rng.normal(0, 1.5, rows)


def _reference_cdf(name, w, m, s, degrees=5):
    from scipy.special import ndtr, stdtr

    def cdf(z):
        c = (z-m)/s
        return float((w*(ndtr(c) if name == "gaussian" else stdtr(degrees, c))).sum())
    return cdf


def _quad(function, lo, hi, knots=()):
    from scipy.integrate import quad
    if not hi > lo:
        return 0.
    inside = sorted(k for k in knots if lo < k < hi)
    return quad(function, lo, hi, points=inside or None, limit=500,
                epsabs=1e-13, epsrel=1e-12)[0]


def _reference_wing(name, w, m, s, y, segments):
    cdf = _reference_cdf(name, w, m, s)
    total = 0.
    for lo, hi, density in segments:
        cut = min(max(y, lo), hi)
        total += density*(_quad(lambda z: cdf(z)**2, lo, cut, m)
                          + _quad(lambda z: (1-cdf(z))**2, cut, hi, m))
    return total


def _reference_crps(name, w, m, s, y, degrees=5):
    cdf = _reference_cdf(name, w, m, s, degrees)
    return (_quad(lambda z: cdf(z)**2, -np.inf, y)
            + _quad(lambda z: (1-cdf(z))**2, y, np.inf))


def _wing_record(name, thresholds, intervals):
    n = len(thresholds)
    return {"identity": [name], "thresholds": thresholds,
            "weights": [1./n]*n if n else [], "intervals": intervals}


def _wing_context():
    return [
        _wing_record("a", [-1.2, -.8, .6, 1.], [[-1.2, -.8], [.6, 1.]]),
        _wing_record("b", [-1.5, -1., -.7, .5, .9, 1.4],
                     [[-1.5, -1.], [-1., -.7], [.5, .9], [.9, 1.4]]),
        _wing_record("c", [-2., -1.5, -1., .4, .8],
                     [[-2., -1.], [-1.5, -1.], [.4, .8]]),
        _wing_record("null", [], []),
    ]


# Hand-derived from the records above: each side's union carries mass 1/2,
# uniform on that union, so density = 1/(2 * union length).
_WING_SEGMENTS = [
    [(-1.2, -.8, 1.25), (.6, 1., 1.25)],
    [(-1.5, -.7, .625), (.5, 1.4, .5/.9)],
    [(-2., -1., .5), (.4, .8, 1.25)],
    [],
]


def _torch_context(scorer):
    import torch
    lower, upper, density, _ = scorer.segment_arrays()
    thresholds, weights, _ = scorer.arrays()
    return {key: torch.as_tensor(value, dtype=torch.float64) for key, value in
            dict(lower=lower, upper=upper, density=density,
                 thresholds=thresholds, weights=weights).items()}


def test_wing_vocabulary_has_one_owner_and_matches_scorer_outputs():
    owner = predictive_cdf.DecisionRegionScores
    scorer = owner(_wing_context())
    curve = MixtureCurve(*[a[:4] for a in _draw_mixture(4)[:3]])
    produced = {*scorer.score(curve, np.zeros(4)), *scorer.wing_score(curve, np.zeros(4))}
    assert produced == {*owner.METRIC_COUNTS, *owner.METRIC_COUNTS.values()}
    assert set(owner.PROPER_METRICS) <= set(owner.METRIC_COUNTS)
    assert set(owner.WING_METRICS) <= set(owner.PROPER_METRICS)
    assert "decision_strike_brier" in owner.PROPER_METRICS
    assert set(scorer.wing_score(curve, np.zeros(4))) == {
        *owner.WING_METRICS, *(owner.METRIC_COUNTS[m] for m in owner.WING_METRICS)}


def test_wing_groups_split_by_side_of_zero_and_refuse_straddling_intervals():
    owner = predictive_cdf.DecisionRegionScores
    lower, upper = owner.wing_groups([[-2., -1.], [.5, 1.], [-1., 0.], [0., .2]])
    assert lower == [[-2., -1.], [-1., 0.]] and upper == [[.5, 1.], [0., .2]]
    assert owner.wing_groups([]) == [[], []]
    with pytest.raises(ValueError, match="straddl"):
        owner.wing_groups([[-1., 1.]])


def test_segments_from_groups_unions_each_group_and_splits_mass_evenly():
    owner = predictive_cdf.DecisionRegionScores
    got = owner.segments_from_groups([[[-2., -1.], [-1.5, -1.], [-1., -.5]], [[.4, .8]]])
    assert got == [[-2., -.5, pytest.approx(1/(2*1.5))], [.4, .8, pytest.approx(1/(2*.4))]]
    assert sum(d*(hi-lo) for lo, hi, d in got) == pytest.approx(1.)
    assert owner.segments_from_groups([[], [[.4, .8]]]) == [[.4, .8, pytest.approx(2.5)]]
    assert owner.segments_from_groups([]) == [] == owner.segments_from_groups([[], []])
    touching = owner.segments_from_groups([[[0., 1.]], [[1., 2.]]])
    assert [row[:2] for row in touching] == [[0., 1.], [1., 2.]]
    with pytest.raises(ValueError, match="overlap"):
        owner.segments_from_groups([[[0., 1.]], [[.9, 2.]]])


@pytest.mark.parametrize("mutate,match", [
    (lambda r: r.pop("intervals"), "intervals"),
    (lambda r: r.update(intervals=[]), "both empty or both"),
    (lambda r: r.update(thresholds=[], weights=[]), "both empty or both"),
    (lambda r: r.update(intervals=[[-1.2, .6]]), "straddl"),
    (lambda r: r.update(intervals=[[-1.2, -.79]]), "listed threshold"),
    (lambda r: r.update(intervals=[[-.8, -1.2]]), "invalid"),
    (lambda r: r.update(intervals=[[-1.2, float("nan")]]), "invalid"),
    (lambda r: r.update(intervals=[[-1.2]]), "invalid"),
])
def test_wing_consumers_refuse_bad_intervals_but_other_uses_stay_valid(mutate, match):
    record = _wing_context()[0]
    mutate(record)
    scorer = predictive_cdf.DecisionRegionScores([record])
    scorer.arrays()
    curve = MixtureCurve([[1.]], [[0.]], [[1.]])
    if record.get("thresholds"):
        scorer.score(curve, [0.])
    with pytest.raises(ValueError, match=match):
        scorer.segment_arrays()
    with pytest.raises(ValueError, match=match):
        scorer.wing_score(curve, [0.])


def test_segment_arrays_derive_from_intervals_without_mutating_contexts():
    context = _wing_context()
    before = copy.deepcopy(context)
    lower, upper, density, counts = predictive_cdf.DecisionRegionScores(context).segment_arrays()
    assert context == before
    assert counts.tolist() == [2, 2, 2, 0] and lower.shape == (4, 2)
    for row, expected in enumerate(_WING_SEGMENTS):
        for column, (lo, hi, d) in enumerate(expected):
            assert (lower[row, column], upper[row, column]) == (lo, hi)
            assert density[row, column] == pytest.approx(d)
        assert (density[row, len(expected):] == 0).all()
        assert (upper-lower)[row, len(expected):].tolist() == [0.]*(2-len(expected))
        assert (density*(upper-lower)).sum(1)[row] == pytest.approx(1. if expected else 0.)


def test_all_null_context_pads_to_one_empty_segment_and_scores_nan():
    scorer = predictive_cdf.DecisionRegionScores([_wing_record("n", [], [])])
    lower, upper, density, counts = scorer.segment_arrays()
    assert lower.shape == (1, 1) and counts.tolist() == [0]
    result = scorer.wing_score(MixtureCurve([[1.]], [[0.]], [[1.]]), [0.])
    assert np.isnan(result["decision_wing_twcrps"]).all()
    assert result["decision_wing_segment_count"].tolist() == [0]


@pytest.mark.parametrize("name", ["gaussian", "student"])
def test_wing_twcrps_matches_quadrature_scorer_and_sample_rule(name):
    torch = pytest.importorskip("torch")
    from dskit.pipeline.distribution_scores import SampleDistribution, ThresholdWeightedCrps
    w, m, s, y = _draw_mixture(rows=4)
    scorer = predictive_cdf.DecisionRegionScores(_wing_context())
    curve = _family_curve(name, w, m, s)
    got = scorer.wing_score(curve, y)
    assert got["decision_wing_segment_count"].tolist() == [2, 2, 2, 0]
    assert np.isnan(got["decision_wing_twcrps"][3])
    value = got["decision_wing_twcrps"][:3]
    reference = [_reference_wing(name, w[i], m[i], s[i], y[i], _WING_SEGMENTS[i])
                 for i in range(3)]
    np.testing.assert_allclose(value, reference, rtol=0, atol=1e-10)
    # Torch term and evaluation-side scorer are one quantity on one CDF.
    family = _family(name)
    term = predictive_cdf._CDFWingTwCRPS(family)
    tensors = [torch.tensor(a, dtype=torch.float64) for a in (y[:, None], np.log(w), m, s)]
    values, eligible = term.values(*tensors, _torch_context(scorer))
    assert eligible.tolist() == [True, True, True, False]
    np.testing.assert_allclose(values.numpy()[:3], value, rtol=0, atol=1e-10)
    # The sample-set twCRPS on the same curve's midpoint quantiles agrees.
    quantiles = curve.quantile((np.arange(20000)+.5)/20000)
    for i in range(3):
        sample = sum(d*ThresholdWeightedCrps([[lo, hi]]).score(
            SampleDistribution(quantiles[i]), y[i]) for lo, hi, d in _WING_SEGMENTS[i])
        assert value[i] == pytest.approx(sample, abs=1e-6)


# Measured at pin time against scipy quad (200 mixtures): Gaussian 1e-15,
# Student nu=3 8e-7, nu=5 2e-8, nu=8 9e-11 worst relative error.
@pytest.mark.parametrize("name,degrees,rtol", [
    ("gaussian", 5, 1e-12), ("student", 3, 1e-5), ("student", 5, 2e-7), ("student", 8, 1e-8)])
def test_crps_matches_quadrature_and_evaluation_side_crps(name, degrees, rtol):
    torch = pytest.importorskip("torch")
    from dskit.pipeline.distribution_scores import Crps, SampleDistribution
    w, m, s, y = _draw_mixture(rows=4, comps=4, seed=8)
    y[0] = m[0, 0]  # at a component mean: the diagonal/kink case
    tensors = [torch.tensor(a, dtype=torch.float64) for a in (y[:, None], np.log(w), m, s)]
    got = _family(name, degrees).crps(*tensors).numpy()
    reference = [_reference_crps(name, w[i], m[i], s[i], y[i], degrees) for i in range(len(y))]
    np.testing.assert_allclose(got, reference, rtol=rtol, atol=0)
    quantiles = _family_curve(name, w, m, s, degrees).quantile((np.arange(20000)+.5)/20000)
    sample = [Crps().score(SampleDistribution(quantiles[i]), y[i]) for i in range(len(y))]
    np.testing.assert_allclose(got, sample, rtol=0, atol=2e-5)


@pytest.mark.parametrize("degrees", [3, 4, 5, 8, 9, 30, 31])
def test_student_cdf_series_matches_scipy_and_gradient_is_the_density(degrees):
    torch = pytest.importorskip("torch")
    from scipy.special import stdtr
    from scipy.stats import t as student
    family = _family("student", degrees)
    t = np.r_[-np.logspace(-3, 8, 300)[::-1], 0., np.logspace(-3, 8, 300)]
    got = family.standard_cdf(torch.tensor(t, dtype=torch.float64)).numpy()
    want = stdtr(degrees, t)
    assert np.max(np.abs(got-want)) <= 1e-14
    lower = t < 0
    assert np.max(np.abs(got[lower]-want[lower])/want[lower]) <= 1e-9
    grid = np.r_[np.linspace(-40, 40, 321), 0., -np.sqrt(degrees), np.sqrt(degrees)]
    x = torch.tensor(grid, dtype=torch.float64, requires_grad=True)
    family.standard_cdf(x).sum().backward()
    np.testing.assert_allclose(x.grad.numpy(), student.pdf(grid, degrees),
                               rtol=1e-9, atol=1e-14)
    log_pdf = family.standard_log_pdf(torch.tensor(grid, dtype=torch.float64)).numpy()
    np.testing.assert_allclose(log_pdf, student.logpdf(grid, degrees), rtol=1e-12)
    log_cdf = family.standard_log_cdf(torch.tensor(t, dtype=torch.float64)).numpy()
    np.testing.assert_allclose(log_cdf, np.log(want), rtol=1e-9, atol=1e-13)


def test_gaussian_family_keeps_the_legacy_density_and_cdf_expressions():
    torch = pytest.importorskip("torch")
    import math
    y = torch.tensor([[.3], [-1.]], dtype=torch.float64)
    mu = torch.tensor([[0., .5], [.2, -.4]], dtype=torch.float64)
    sigma = torch.tensor([[1., 2.], [.5, 1.5]], dtype=torch.float64)
    legacy = -.5*((y-mu)/sigma)**2 - sigma.log() - .5*math.log(2*math.pi)
    family = _family("gaussian")
    assert torch.equal(family.log_density(y, mu, sigma), legacy)
    model = MixtureMLPCDF(device="cpu")
    assert torch.equal(model._logp(y, mu, sigma), legacy)
    t = torch.linspace(-3, 3, 7, dtype=torch.float64)
    assert torch.equal(family.standard_cdf(t), torch.special.ndtr(t))
    assert torch.equal(family.standard_log_cdf(t), torch.special.log_ndtr(t))


@pytest.mark.parametrize("name", ["gaussian", "student"])
def test_node_constant_is_one_owner_for_training_and_evaluation(name, monkeypatch):
    torch = pytest.importorskip("torch")
    w, m, s, y = _draw_mixture(rows=3)
    scorer = predictive_cdf.DecisionRegionScores(_wing_context()[:3])
    curve = _family_curve(name, w, m, s)
    tensors = [torch.tensor(a, dtype=torch.float64) for a in (y[:, None], np.log(w), m, s)]

    def both():
        term = predictive_cdf._CDFWingTwCRPS(_family(name))
        values, _ = term.values(*tensors, _torch_context(scorer))
        return values.numpy(), scorer.wing_score(curve, y)["decision_wing_twcrps"]

    fine = both()
    np.testing.assert_allclose(*fine, rtol=0, atol=1e-10)
    monkeypatch.setattr(predictive_cdf, "_QUADRATURE_NODES", 1)
    coarse = both()
    np.testing.assert_allclose(*coarse, rtol=0, atol=1e-10)
    assert np.max(np.abs(coarse[0]-fine[0])) > 1e-6
    nodes, weights = predictive_cdf._gauss_legendre(5)
    assert len(nodes) == len(weights) == 5 and weights.sum() == pytest.approx(2.)
    nodes[:] = 0.
    assert predictive_cdf._gauss_legendre(5)[0].any()


def test_grid_curve_wing_integral_is_exact_for_piecewise_linear_and_atoms():
    uniform = GridCurve([[0., 1.]], [[0., 1.]])
    # y inside: int_.2^.3 z^2 + int_.3^.7 (1-z)^2
    inside = (.3**3-.2**3)/3 + ((1-.3)**3-(1-.7)**3)/3
    got = uniform._segment_integrals(np.array([[.2]]), np.array([[.7]]), np.array([.3]))
    assert got[0, 0] == pytest.approx(inside, abs=1e-15)
    atom = GridCurve([[0., .5, .5, 1.]], [[0., 0., 1., 1.]])
    assert atom._segment_integrals(
        np.array([[.2]]), np.array([[.8]]), np.array([.6]))[0, 0] == pytest.approx(.1)
    # y outside the segment on either side; zero-width padding contributes nothing.
    left = uniform._segment_integrals(np.array([[.2, 0.]]), np.array([[.7, 0.]]), np.array([-1.]))
    assert left[0].tolist() == [pytest.approx(((1-.2)**3-(1-.7)**3)/3), 0.]
    right = uniform._segment_integrals(np.array([[.2]]), np.array([[.7]]), np.array([5.]))
    assert right[0, 0] == pytest.approx((.7**3-.2**3)/3)
    kinked = GridCurve([[-1., 0., 2.]], [[0., .25, 1.]])
    got = kinked._segment_integrals(np.array([[-.5]]), np.array([[1.5]]), np.array([.5]))[0, 0]
    cdf = lambda z: float(np.interp(z, [-1., 0., 2.], [0., .25, 1.]))
    reference = (_quad(lambda z: cdf(z)**2, -.5, .5, [0.])
                 + _quad(lambda z: (1-cdf(z))**2, .5, 1.5, [0.]))
    assert got == pytest.approx(reference, abs=1e-12)


# Measured at pin time: segment/scale 200 gives worst abs error 5e-9 (Gaussian)
# and 1e-10 (Student); at 1000 it is 8e-6. Standardized wings are far wider
# than min_scale allows, so training sits many orders below these bounds.
@pytest.mark.parametrize("name", ["gaussian", "student"])
def test_wing_twcrps_stays_accurate_for_components_narrow_against_the_segment(name):
    torch = pytest.importorskip("torch")
    rng = np.random.default_rng(1)
    term = predictive_cdf._CDFWingTwCRPS(_family(name))
    context = {"lower": torch.tensor([[-10.]]), "upper": torch.tensor([[0.]]),
               "density": torch.tensor([[1.]])}
    for _ in range(6):
        w, m = rng.dirichlet(np.ones(3)), rng.uniform(-11, 1, 3)
        s, y = np.r_[.05, rng.uniform(.05, 2, 2)], rng.uniform(-11, 1)
        got = term.values(
            torch.tensor([[y]], dtype=torch.float64), torch.tensor(np.log(w))[None],
            torch.tensor(m)[None], torch.tensor(s)[None], context)[0].item()
        want = _reference_wing(name, w, m, s, y, [(-10., 0., 1.)])
        assert abs(got-want) <= 5e-8


def _dense_wing(curve, lower, upper, y, points=200_000):
    """Midpoint-rule reference, valid for any curve with a rowwise cdf."""
    out = np.zeros(len(y))
    for i in range(len(y)):
        cut = min(max(y[i], lower), upper)
        for lo, hi, left in ((lower, cut, True), (cut, upper, False)):
            if hi <= lo:
                continue
            z = lo + (np.arange(points)+.5)*(hi-lo)/points
            f = np.interp(z, *curve_grid(curve, i, lo, hi))
            out[i] += ((f if left else 1-f)**2).mean()*(hi-lo)
    return out


def curve_grid(curve, row, lo, hi, points=400_001):
    z = np.linspace(lo, hi, points)
    queries = np.broadcast_to(z, (len(curve.cdf([[0.]])), points))
    return z, curve.cdf(queries)[row]


def test_other_curves_use_documented_gauss_legendre_with_bounded_error():
    w, m, s, y = _draw_mixture(rows=3)
    base = MixtureCurve(w, m, s)
    other = MixtureCurve(w[::-1].copy(), m[::-1].copy(), s[::-1].copy())
    pit = np.array([.05, .2, .35, .5, .65, .9])
    curves = {"convex": ConvexCurve(base, other, .3),
              "calibrated": CalibratedCurve(base, pit, 5),
              "beta": predictive_cdf.BetaTransformedCurve(base, 1.7, .8)}
    lower, upper = np.full((3, 1), -2.), np.full((3, 1), -.4)
    for name, curve in curves.items():
        got = curve._segment_integrals(lower, upper, y)[:, 0]
        want = _dense_wing(curve, -2., -.4, y)
        np.testing.assert_allclose(got, want, rtol=1e-3, err_msg=name)


def test_grid_curve_wing_integral_matches_a_dense_rule_on_random_curves():
    rng = np.random.default_rng(4)
    values = np.sort(rng.normal(size=(4, 7)), 1)
    values[1, 3] = values[1, 4]  # an atom
    probabilities = np.sort(rng.uniform(size=(4, 7)), 1)
    probabilities[:, 0], probabilities[:, -1] = 0., 1.
    curve = GridCurve(values, probabilities)
    y = rng.normal(size=4)
    got = curve._segment_integrals(np.full((4, 1), -1.5), np.full((4, 1), .9), y)[:, 0]
    want = _dense_wing(curve, -1.5, .9, y)
    np.testing.assert_allclose(got, want, rtol=1e-5)


@pytest.mark.parametrize("name", ["gaussian", "student"])
def test_wing_and_crps_gradients_match_finite_differences(name):
    torch = pytest.importorskip("torch")
    w, m, s, y = _draw_mixture(rows=2, comps=2, seed=2)
    family = _family(name)
    scorer = predictive_cdf.DecisionRegionScores(_wing_context()[:2])
    context = _torch_context(scorer)
    yy = torch.tensor(y[:, None], dtype=torch.float64)
    inputs = [torch.tensor(a, dtype=torch.float64, requires_grad=True)
              for a in (np.log(w), m, s)]
    wing = predictive_cdf._CDFWingTwCRPS(family)
    assert torch.autograd.gradcheck(
        lambda logw, mu, sigma: wing.values(yy, logw, mu, sigma, context)[0],
        inputs, eps=1e-6, atol=1e-6, rtol=1e-5)
    assert torch.autograd.gradcheck(
        lambda logw, mu, sigma: family.crps(yy, logw, mu, sigma), inputs,
        eps=1e-6, atol=1e-6, rtol=1e-5)


def test_losses_registry_adds_crps_and_wing_with_declared_scopes():
    registry = predictive_cdf.TorchCDF._LOSSES
    assert {"nll", "crps", "decision_brier", "decision_log", "wing_twcrps"} <= set(registry)
    assert {k: registry[k].scope for k in registry} == {
        "nll": "global", "crps": "global", "decision_brier": "local",
        "decision_log": "local", "wing_twcrps": "local"}
    assert registry["wing_twcrps"].context_keys == ("lower", "upper", "density")
    assert registry["decision_brier"].context_keys == ("thresholds", "weights")
    assert registry["nll"].context_keys == () == registry["crps"].context_keys


@pytest.mark.parametrize("losses", [
    [{"kind": "crps", "weight": 1.}, {"kind": "wing_twcrps", "weight": .5}],
    [{"kind": "crps", "weight": 1.}, {"kind": "decision_log", "weight": .5}],
    [{"kind": "nll", "weight": 1.}, {"kind": "crps", "weight": 1.},
     {"kind": "decision_brier", "weight": .5}, {"kind": "wing_twcrps", "weight": .5}],
])
def test_torch_cdf_requires_one_global_and_one_local_term_not_a_named_nll(losses):
    model = predictive_cdf.TorchCDF(encoder={"kind": "mlp"}, losses=losses, device="cpu")
    assert [t["kind"] for t in model.loss_config] == [t["kind"] for t in losses]


@pytest.mark.parametrize("losses", [
    [{"kind": "nll", "weight": 1.}, {"kind": "crps", "weight": 1.}],
    [{"kind": "decision_brier", "weight": 1.}, {"kind": "wing_twcrps", "weight": 1.}],
    [{"kind": "wing_twcrps", "weight": 1.}],
    [{"kind": "nll", "weight": 1.}, {"kind": "wing_twcrps", "weight": 0.}],
    [{"kind": "crps", "weight": 1.}, {"kind": "wing_twcrps", "weight": 1.},
     {"kind": "wing_twcrps", "weight": 1.}],
    [{"kind": "crps", "weight": 1., "lambda": 2}, {"kind": "wing_twcrps", "weight": 1.}],
])
def test_torch_cdf_refuses_missing_scope_zero_weight_duplicates_and_extra_keys(losses):
    with pytest.raises(ValueError, match="composite loss"):
        predictive_cdf.TorchCDF(encoder={"kind": "mlp"}, losses=losses, device="cpu")


@pytest.mark.parametrize("family", [
    {"kind": "student"}, {"kind": "student", "degrees": 2},
    {"kind": "student", "degrees": 5.}, {"kind": "student", "degrees": 5.5},
    {"kind": "student", "degrees": True}, {"kind": "student", "degrees": "5"},
    {"kind": "gaussian", "degrees": 5}, {"kind": "cauchy"}, {}, "student", [],
])
def test_torch_cdf_family_is_default_deny(family):
    with pytest.raises(ValueError, match="family"):
        predictive_cdf.TorchCDF(encoder={"kind": "mlp"}, family=family, losses=[
            {"kind": "crps", "weight": 1.}, {"kind": "wing_twcrps", "weight": 1.}],
            device="cpu")


def test_torch_cdf_family_selects_curve_and_keeps_default_state_unchanged():
    losses = [{"kind": "nll", "weight": .1}, {"kind": "decision_brier", "weight": 1.}]
    default = predictive_cdf.TorchCDF(encoder={"kind": "mlp"}, losses=losses, device="cpu")
    named = predictive_cdf.TorchCDF(encoder={"kind": "mlp"}, losses=losses, device="cpu",
                                    family=dict(_GAUSSIAN))
    student = predictive_cdf.TorchCDF(encoder={"kind": "mlp"}, losses=losses, device="cpu",
                                      family=dict(_STUDENT))
    assert default.family_config is None and named.family_config == _GAUSSIAN
    assert student.family_config == _STUDENT
    args = ([[1.]], [[0.]], [[1.]])
    assert type(default._curve(*args)) is MixtureCurve
    assert type(named._curve(*args)) is MixtureCurve
    curve = student._curve(*args)
    assert type(curve) is predictive_cdf.StudentMixtureCurve and curve.degrees == 5
    # Digest settings: byte-identical to the legacy literal unless a family is configured.
    assert default._equivalence_settings() == {
        "components": 3, "hidden": (32, 32), "epochs": 20, "batch_size": 1024,
        "seeds": [11, 29], "device": "cpu", "learning_rate": .001,
        "weight_decay": .01, "min_scale": .1, "activation": "tanh", "dropout": 0.,
        "head_features": (), "deterministic": False}
    assert "family" not in default._equivalence_settings()
    assert named._equivalence_settings()["family"] == _GAUSSIAN
    assert student._equivalence_settings()["family"] == _STUDENT
    for model in (default, named, student):
        model.losses = []  # unfitted: the research state only adds the configuration
    assert "family" not in default._research_state()
    assert student._research_state()["family"] == _STUDENT
    assert MixtureMLPCDF(device="cpu")._equivalence_settings() == default._equivalence_settings()


@pytest.mark.parametrize("name", ["gaussian", "student"])
def test_wing_term_null_rows_add_only_global_terms_and_keep_lambda_undiluted(name):
    torch = pytest.importorskip("torch")
    family_config = dict(_GAUSSIAN if name == "gaussian" else _STUDENT)
    model = predictive_cdf.TorchCDF(
        encoder={"kind": "mlp"}, family=family_config, device="cpu",
        losses=[{"kind": "crps", "weight": .7}, {"kind": "wing_twcrps", "weight": .3}])
    context = _wing_context()
    model.fit_decision_context(context, context)
    training = model._training_context(np.zeros((4, 1)))
    w, m, s, y = _draw_mixture(rows=4)
    tensors = [torch.tensor(a, dtype=torch.float64) for a in (y[:, None], np.log(w), m, s)]
    family = _family(name)
    crps = family.crps(*tensors).numpy()
    wing = np.array([_reference_wing(name, w[i], m[i], s[i], y[i], _WING_SEGMENTS[i])
                     for i in range(3)])
    # A minibatch of {eligible row 0, null row 3}: lambda is scaled by N/|E|, never by batch.
    ix = torch.tensor([0, 3])
    batch = [t[ix] for t in tensors]
    got = model._objective_loss(*batch, ix, training).item()
    expected = .7*crps[[0, 3]].mean() + .3*wing[0]/2*4/3
    assert got == pytest.approx(expected, rel=1e-6)
    # An all-null batch contributes the global term only.
    null_only = model._objective_loss(*[t[[3]] for t in tensors], torch.tensor([3]), training)
    assert null_only.item() == pytest.approx(.7*crps[3], rel=1e-9)


def test_wing_term_refuses_missing_intervals_when_the_context_is_attached():
    model = predictive_cdf.TorchCDF(
        encoder={"kind": "mlp"}, device="cpu",
        losses=[{"kind": "nll", "weight": 1.}, {"kind": "wing_twcrps", "weight": .5}])
    good = _wing_context()
    bare = [{k: v for k, v in r.items() if k != "intervals"} for r in good]
    with pytest.raises(ValueError, match="intervals"):
        model.fit_decision_context(bare, good)
    with pytest.raises(ValueError, match="intervals"):
        model.fit_decision_context(good, bare)
    straddle = copy.deepcopy(good)
    straddle[0]["intervals"] = [[-1.2, .6]]
    with pytest.raises(ValueError, match="straddl"):
        model.fit_decision_context(straddle, good)
    model.fit_decision_context(good, good)
    model._training_context(np.zeros((4, 1)))
    with pytest.raises(ValueError, match="no eligible training rows for wing_twcrps"):
        model.fit_decision_context([good[3]], [good[3]])
        model._training_context(np.zeros((1, 1)))
    # Without a wing term the same bare contexts remain valid.
    plain = predictive_cdf.TorchCDF(encoder={"kind": "mlp"}, device="cpu", losses=[
        {"kind": "nll", "weight": 1.}, {"kind": "decision_brier", "weight": 1.}])
    plain.fit_decision_context(bare, bare)
    plain._training_context(np.zeros((4, 1)))


@pytest.mark.parametrize("name", ["gaussian", "student"])
@pytest.mark.parametrize("kind", ["nll", "crps", "decision_brier", "decision_log", "wing_twcrps"])
def test_every_family_and_term_has_finite_gradients_and_independent_reference(name, kind):
    torch = pytest.importorskip("torch")
    from scipy.stats import norm, t as student
    w, m, s, y = _draw_mixture(rows=3)
    family = _family(name)
    scorer = predictive_cdf.DecisionRegionScores(_wing_context()[:3])
    context = _torch_context(scorer)
    mu = torch.tensor(m, dtype=torch.float64, requires_grad=True)
    sigma = torch.tensor(s, dtype=torch.float64, requires_grad=True)
    logw = torch.tensor(np.log(w), dtype=torch.float64, requires_grad=True)
    term = predictive_cdf.TorchCDF._LOSSES[kind](family)
    values, eligible = term.values(torch.tensor(y[:, None]), logw, mu, sigma, context)
    assert eligible.tolist() == [True]*3
    values.sum().backward()
    assert all(torch.isfinite(t.grad).all() and t.grad.abs().sum() > 0
               for t in (mu, sigma, logw))
    dist = norm if name == "gaussian" else student(5)
    cdf = lambda z: (w[:, None, :]*(dist.cdf((np.asarray(z)[:, :, None]-m[:, None, :])
                                              / s[:, None, :]))).sum(2)
    if kind == "nll":
        pdf = (w*dist.pdf((y[:, None]-m)/s)/s).sum(1)
        want = -np.log(pdf)
    elif kind == "crps":
        want = [_reference_crps(name, w[i], m[i], s[i], y[i]) for i in range(3)]
    elif kind == "wing_twcrps":
        want = [_reference_wing(name, w[i], m[i], s[i], y[i], _WING_SEGMENTS[i])
                for i in range(3)]
    else:
        thresholds, weights, _ = scorer.arrays()
        p = cdf(thresholds)
        truth = (y[:, None] <= thresholds)
        want = (((p-truth)**2*weights).sum(1) if kind == "decision_brier" else
                -((np.where(truth, np.log(p), np.log(1-p)))*weights).sum(1))
    np.testing.assert_allclose(values.detach().numpy(), want, rtol=1e-7, atol=1e-9)


@pytest.mark.parametrize("name", ["gaussian", "student"])
def test_torch_cdf_wing_and_crps_train_report_and_curve_end_to_end(name):
    torch = pytest.importorskip("torch")
    family = dict(_GAUSSIAN if name == "gaussian" else _STUDENT)
    context = _wing_context() + _wing_context()
    for i, record in enumerate(context):
        record["identity"] = [str(i)]
    x = np.random.default_rng(1).normal(size=(8, 3))
    y = np.linspace(-1.5, 1.5, 8)
    model = predictive_cdf.TorchCDF(
        encoder={"kind": "mlp"}, family=family, components=2, hidden=[4], epochs=2,
        batch_size=4, seeds=[3], device="cpu", deterministic=True,
        losses=[{"kind": "crps", "weight": 1.}, {"kind": "nll", "weight": .1},
                {"kind": "wing_twcrps", "weight": .5}])
    model.fit_decision_context(context, context).fit(x, y, x, y)
    assert np.isfinite(model.losses).all()
    curve = model.curve(x)
    assert type(curve) is (MixtureCurve if name == "gaussian"
                           else predictive_cdf.StudentMixtureCurve)
    report = model.loss_report(x, y, context)
    assert set(report["terms"]) == {"crps", "nll", "wing_twcrps"}
    assert report["terms"]["crps"]["n"] == 8 and report["terms"]["wing_twcrps"]["n"] == 6
    assert report["eligible_n"] == 6 and report["threshold_n"] == 2*(4+6+5)
    assert report["composite"] == pytest.approx(sum(
        t["mean"]*t["weight"] for t in report["terms"].values()))
    scorer = predictive_cdf.DecisionRegionScores(context)
    wing = scorer.wing_score(curve, y)["decision_wing_twcrps"]
    assert report["terms"]["wing_twcrps"]["mean"] == pytest.approx(np.nanmean(wing), rel=1e-9)
    bare = [{k: v for k, v in r.items() if k != "intervals"} for r in context]
    with pytest.raises(ValueError, match="intervals"):
        model.loss_report(x, y, bare)


def test_gaussian_default_training_loss_is_unchanged_by_the_family_seam():
    """Legacy decision objective reproduced from its documented formula."""
    torch = pytest.importorskip("torch")
    from scipy.stats import norm
    model = predictive_cdf.TorchCDF(encoder={"kind": "mlp"}, device="cpu", losses=[
        {"kind": "nll", "weight": .1}, {"kind": "decision_brier", "weight": 2.},
        {"kind": "decision_log", "weight": 3.}])
    context = _wing_context()
    model.fit_decision_context(context, context)
    training = model._training_context(np.zeros((4, 1)))
    w, m, s, y = _draw_mixture(rows=4)
    ix = torch.tensor([0, 1, 3])
    tensors = [torch.tensor(a, dtype=torch.float64)[ix] for a in (y[:, None], np.log(w), m, s)]
    got = model._objective_loss(*tensors, ix, training).item()
    thresholds, weights, _ = predictive_cdf.DecisionRegionScores(context).arrays()
    nll = -np.log((w*norm.pdf((y[:, None]-m)/s)/s).sum(1))
    p = np.stack([(w[i, :, None]*norm.cdf((thresholds[i]-m[i][:, None])/s[i][:, None])).sum(0)
                  for i in range(4)])
    truth = y[:, None] <= thresholds
    brier = ((p-truth)**2*weights).sum(1)
    log = -((np.where(truth, np.log(p), np.log(1-p)))*weights).sum(1)
    batch = [0, 1, 3]
    expected = (.1*nll[batch].mean() + 2*brier[[0, 1]].sum()/3*4/3
                + 3*log[[0, 1]].sum()/3*4/3)
    assert got == pytest.approx(expected, rel=1e-6)


# --- study wiring: wing_metrics ------------------------------------------------

def _wing_hpo_fixture(tmp_path):
    config, frame = _decision_hpo_fixture(tmp_path)
    contexts = []
    for row in frame.itertuples():
        eligible = not row.date.endswith("01")
        contexts.append(
            {"identity": [row.unit, row.date, row.expiry],
             "thresholds": [-.6, -.3, .3, .6] if eligible else [],
             "weights": [.25]*4 if eligible else [],
             "intervals": [[-.6, -.3], [.3, .6]] if eligible else []})
    frame["context"] = contexts
    config["study"]["wing_metrics"] = True
    config["experiment"]["candidates"]["candidate"]["params"]["losses"] = [
        {"kind": "nll", "weight": .1}, {"kind": "decision_brier", "weight": 1.},
        {"kind": "wing_twcrps", "weight": .5}]
    return config, frame


@pytest.mark.parametrize("value", [1, "yes", None, 0.])
def test_wing_metrics_flag_is_a_strict_bool(tmp_path, value):
    config, _ = _wing_hpo_fixture(tmp_path)
    config["study"]["wing_metrics"] = value
    with pytest.raises(ValueError, match="wing"):
        ChronologicalCDFStudy(config["study"])


def test_wing_metrics_requires_decision_context_and_selection_requires_the_flag(tmp_path):
    config, _ = _wing_hpo_fixture(tmp_path)
    del config["study"]["decision_context"], config["study"]["decision_acceptance"]
    with pytest.raises(ValueError, match="wing"):
        ChronologicalCDFStudy(config["study"])
    config, _ = _wing_hpo_fixture(tmp_path)
    config["experiment"]["selection_metric"] = "decision_wing_twcrps"
    CDFHyperparameterStudy(copy.deepcopy(config))
    config["study"]["wing_metrics"] = False
    with pytest.raises(ValueError, match="selection metric"):
        CDFHyperparameterStudy(copy.deepcopy(config))
    del config["study"]["wing_metrics"]
    with pytest.raises(ValueError, match="selection metric"):
        CDFHyperparameterStudy(copy.deepcopy(config))


def test_without_wing_metrics_scores_contexts_and_config_are_unchanged(tmp_path):
    config, frame = _decision_hpo_fixture(tmp_path)
    before = copy.deepcopy(frame["context"].tolist())
    scores = ChronologicalCDFStudy(config["study"]).run(frame)
    assert not any("wing" in column for column in scores.columns)
    assert frame["context"].tolist() == before
    assert "wing_metrics" not in ChronologicalCDFStudy(config["study"]).to_obj()


def test_decision_selection_guards_accept_only_the_decision_vocabulary(tmp_path):
    config, _ = _wing_hpo_fixture(tmp_path)
    e = config["experiment"]
    e["selection_metric"] = "decision_wing_twcrps"
    e["selection_guard"] = {"reference": "reference", "variant": "raw",
                            "metrics": ["decision_bias"],
                            "cell_improvement_metrics": ["decision_wing_twcrps"],
                            "target": 0., "tolerance": 0.}
    CDFHyperparameterStudy(copy.deepcopy(config))
    e["selection_guard"]["metrics"] = ["crps"]
    with pytest.raises(ValueError, match="decision regions"):
        CDFHyperparameterStudy(copy.deepcopy(config))


@pytest.mark.parametrize("family", [None, {"kind": "student", "degrees": 5}])
def test_wing_hpo_end_to_end_reports_pairs_and_telemetry(tmp_path, family):
    config, frame = _wing_hpo_fixture(tmp_path)
    config["experiment"]["selection_metric"] = "decision_wing_twcrps"
    if family:
        config["experiment"]["candidates"]["candidate"]["params"]["family"] = family
    study = CDFHyperparameterStudy(config)
    provenance = {"fixture": "wing"}
    identity = config["study"]["identity"]
    hash_before = study._frame_hash(frame, identity)
    study.run(frame, stage="search", partition="separate", provenance=provenance)
    chosen = study.run(frame, stage="select", provenance=provenance)
    assert chosen["selection_metric"] == "decision_wing_twcrps"
    for partition in ("development", "later"):
        study.run(frame, stage="evaluate", partition=partition, provenance=provenance)
    report = study.run(frame, stage="report", provenance=provenance)
    assert study._frame_hash(frame, identity) == hash_before
    wing = next(r for r in report["metrics"] if r["model"] == "candidate"
                and r["metric"] == "decision_wing_twcrps")
    strike = next(r for r in report["metrics"] if r["model"] == "candidate"
                  and r["metric"] == "decision_strike_brier")
    assert wing["n"] == strike["n"] == 8 and wing["excluded_n"] == 2
    assert wing["threshold_n"] == 16 and strike["threshold_n"] == 32
    assert wing["equal_cell_skill"] is not None
    pairs = [r for r in report["paired_block_intervals"]
             if r["metric"] == "decision_wing_twcrps"]
    assert pairs and {r["model"] for r in pairs} >= {"candidate"}
    scores = pd.read_parquet(tmp_path/"hpo/report/scores.parquet")
    assert scores.decision_wing_segment_count.isin([0, 2]).all()
    assert scores.loc[scores.decision_wing_segment_count == 0, "decision_wing_twcrps"].isna().all()
    counts = json.loads((tmp_path/"hpo/evaluate/later/counts.json").read_text())
    for band in ("training", "calibration", "validation"):
        values = counts[0]["candidate_losses"][band]
        assert values["wing_twcrps"] > 0 and values["reference_wing_twcrps"] > 0
        assert "wing_twcrps_skill_pct" in values and "wing_twcrps" in values["terms"]
        assert values["terms"]["wing_twcrps"]["n"] == values["eligible_n"]


def test_study_refuses_bad_wing_context_before_fitting_any_model(tmp_path):
    config, frame = _wing_hpo_fixture(tmp_path)
    row = next(i for i, record in enumerate(frame.context) if record["thresholds"])
    frame.at[row, "context"] = {**frame.at[row, "context"], "intervals": [[-.6, -.2]]}
    study = ChronologicalCDFStudy(config["study"])
    with pytest.raises(ValueError, match="listed threshold"):
        study.run(frame)
    assert not (tmp_path/"unused/scores.parquet").exists()


def test_decision_score_pairing_covers_wing_counts_and_eligibility(tmp_path):
    config, frame = _wing_hpo_fixture(tmp_path)
    models = {"candidate": config["experiment"]["candidates"]["candidate"],
              **config["study"]["models"]}
    study = ChronologicalCDFStudy({**config["study"], "models": models})
    scores = study.run(frame)
    study._validate_decision_scores(scores)
    counted = scores.decision_wing_segment_count > 0
    broken = scores.copy()
    row = broken[(broken.model == "reference") & counted].index[0]
    broken.loc[row, "decision_wing_segment_count"] = 0
    with pytest.raises(ValueError):
        study._validate_decision_scores(broken)
    broken = scores.copy()
    row = broken[(broken.model == "candidate") & counted].index[0]
    broken.loc[row, "decision_wing_twcrps"] = np.nan
    with pytest.raises(ValueError, match="eligibility"):
        study._validate_decision_scores(broken)
    broken = scores.drop(columns="decision_wing_segment_count")
    with pytest.raises(ValueError, match="counts missing"):
        study._validate_decision_scores(broken)


def test_option_conversion_nodes_exist():
    for name in ("OptionPriceCDF", "ExpiryCloseLabels", "OptionCDFPanel"):
        assert hasattr(predictive_cdf, name), (
            "raw options cannot yet produce the standard CDF panel: "+name)


def _option_curve_params():
    return {"probabilities": [.01, .05, .1, .25, .5, .75, .9, .95, .99],
            "min_forward_pairs": 3, "min_wing_nodes": 3, "max_inner_gap": .08,
            "max_outer_gap": .12, "max_projection_distance": None,
            "max_forward_dispersion": None}


def _priced_options():
    from scipy.stats import norm
    rows = []
    for strike in np.arange(70., 131., 5.):
        d1 = (np.log(100/strike)+.02)/.2
        call = 100*norm.cdf(d1)-strike*norm.cdf(d1-.2)
        for right, mark in (("call", call), ("put", call-100+strike)):
            rows.append({"strike": strike, "type": right, "mark": mark})
    return pd.DataFrame(rows)


def test_option_price_cdf_exports_exact_inverse_grid_and_known_distribution():
    proxy = predictive_cdf.OptionPriceCDF(**_option_curve_params()).estimate(_priced_options(), 100)
    assert proxy["eligible"]
    assert proxy["forward_ratio"] == pytest.approx(1)
    assert proxy["forward_dispersion"] < 1e-14
    np.testing.assert_array_equal(proxy["quantiles"], np.interp(
        _option_curve_params()["probabilities"], proxy["probabilities"], proxy["log_returns"]))
    assert np.all(np.diff(proxy["probabilities"]) > 0)
    assert proxy["probabilities"][0] == 0 and proxy["probabilities"][-1] == 1
    assert proxy["quantiles"][4] == pytest.approx(-.02, abs=.004)


def test_option_price_cdf_admission_and_optional_guards():
    frame = _priced_options()
    helper = predictive_cdf.OptionPriceCDF(**_option_curve_params())
    assert not helper.estimate(frame[frame.type == "call"], 100)["eligible"]
    assert not helper.estimate(frame[frame.strike > 95], 100)["eligible"]
    frame.loc[(frame.type == "call") & (frame.strike == 100), "mark"] += 5
    proxy = predictive_cdf.OptionPriceCDF(
        **dict(_option_curve_params(), max_projection_distance=0)).estimate(frame, 100)
    assert "projection_distance_exceeded" in proxy["reasons"]


def test_expiry_close_labels_holiday_pending_and_unit_guards():
    bars = [{"symbol": "XYZ", "quote_date": "2024-03-22", "expiry": expiry,
             "price_basis": "trade_close"} for expiry in ("2024-03-29", "2024-05-03")]
    prices = [{"symbol": "XYZ", "quote_date": day, "close": close, "adjusted_close": close/2,
               "close_complete": True, "post_session_split": False, "unit_history_verified": True}
              for day, close in (("2024-03-22", 100), ("2024-03-28", 110))]
    node = predictive_cdf.ExpiryCloseLabels("labels", {"calendar": "XNYS", "asof": "2024-04-01T20:00:00Z"})
    inputs = {"bars": bars, "snapshots": [], "prices": prices}
    records = node.run(None, inputs)["records"]
    assert records[0]["settlement_date"] == "2024-03-28"
    assert records[0]["actual_calendar_dte"] == 6
    assert records[0]["terminal_return"] == pytest.approx(np.log(1.1))
    assert records[1]["terminal_return"] is None
    prices[0]["post_session_split"] = True
    assert node.run(None, inputs)["records"][0]["spot"] is None
    prices[0]["post_session_split"] = False
    prices[0]["close_complete"] = False
    assert node.run(None, inputs)["records"][0]["spot"] is None
    prices[0]["close_complete"] = True
    prices[0]["unit_history_verified"] = False
    rejected = node.run(None, inputs)["records"][0]
    assert rejected["spot"] is None and rejected["unit_check"] == "unverified_or_changed_units"


def test_option_panel_retains_rejected_observations_and_current_clock():
    params = {**_option_curve_params(), "required_multiplier": 100,
              "required_style": "american", "max_quote_age_seconds": 900}
    node = predictive_cdf.OptionCDFPanel("panel", params)
    base = {"symbol": "XYZ", "root_symbol": "XYZ", "quote_date": "2024-03-22",
            "expiry": "2024-05-03", "price_basis": "indicative_quote",
            "multiplier": 100, "contract_size": 100, "style": "american",
            "contract_terms_status": "metadata_present", "observation_reasons": [],
            "quote_timestamp": "2024-03-22T19:59:00Z"}
    rows = [dict(base, **r, contract=str(i)) for i, r in enumerate(_priced_options().to_dict("records"))]
    label = {"symbol": "XYZ", "quote_date": base["quote_date"], "expiry": base["expiry"],
             "price_basis": base["price_basis"], "spot": 100, "entry_close_at": "2024-03-22T20:00:00Z",
             "terminal_return": .02, "actual_calendar_dte": 42}
    inputs = {"bars": [], "snapshots": rows, "labels": [label], "contracts": []}
    out = node.run(None, inputs)
    assert out["records"][0]["rn_proxy_eligible"] == 1
    assert out["records"][0]["asof_ms"] == 1711065600000
    rows[0]["quote_timestamp"] = "2024-03-22T20:01:00Z"
    rows[1]["quote_timestamp"] = "2024-03-22T19:00:00Z"
    rows[2]["contract_terms_status"] = "unverified_contract_terms"
    out = node.run(None, inputs)
    observed = out["options"].value["observations"]
    assert len(observed) == len(rows)
    assert "quote_outside_close_window" in observed[0]["admission_reasons"]
    assert "quote_outside_close_window" in observed[1]["admission_reasons"]
    assert "unverified_contract_terms" in observed[2]["admission_reasons"]
    label["spot"] = None
    assert node.run(None, inputs)["summary"]["eligible_cdfs"] == 0


def test_option_cdf_flat_projection_has_canonical_deduplicated_grid():
    frame = _priced_options()
    frame.loc[(frame.type == "call") & (frame.strike == 105), "mark"] += 3
    proxy = predictive_cdf.OptionPriceCDF(**_option_curve_params()).estimate(frame, 100)
    assert proxy["eligible"]
    assert (np.diff(proxy["raw_projected_probabilities"]) == 0).any()
    assert (np.diff(proxy["probabilities"]) > 0).all()
    np.testing.assert_array_equal(proxy["quantiles"], np.interp(
        _option_curve_params()["probabilities"], proxy["probabilities"], proxy["log_returns"]))


@pytest.mark.parametrize("knob,value", [
    ("min_forward_pairs", 0), ("min_wing_nodes", True),
    ("max_inner_gap", None), ("max_outer_gap", float("nan")),
    ("max_projection_distance", -1), ("probabilities", [.5, .5])])
def test_option_cdf_refuses_invalid_admission_policy(knob, value):
    with pytest.raises(ValueError):
        predictive_cdf.OptionPriceCDF(**dict(_option_curve_params(), **{knob: value}))


@pytest.mark.parametrize("spot", [None, "100", True, 1e-320])
def test_option_price_cdf_refuses_invalid_or_nonfinite_result_spot(spot):
    proxy = predictive_cdf.OptionPriceCDF(**_option_curve_params()).estimate(_priced_options(), spot)
    assert not proxy["eligible"]


def test_option_price_cdf_refuses_nonnumeric_price_record():
    frame = _priced_options().astype({"mark": object})
    frame.loc[0, "mark"] = "invalid"
    assert not predictive_cdf.OptionPriceCDF(**_option_curve_params()).estimate(frame, 100)["eligible"]


def test_option_panel_refuses_quantile_field_collisions_and_missing_close_clock():
    params = {**_option_curve_params(), "required_multiplier": 100,
              "required_style": "american", "max_quote_age_seconds": 900}
    assert predictive_cdf.OptionCDFPanel.validate_params(dict(params, probabilities=[.5, .50001]))
    row = {"contract": "XYZ", "symbol": "XYZ", "root_symbol": "XYZ",
           "multiplier": 100, "contract_size": 100, "style": "american",
           "contract_terms_status": "metadata_present", "mark": 2, "strike": 100,
           "price_basis": "indicative_quote", "quote_timestamp": "2024-03-22T10:00:00Z"}
    node = predictive_cdf.OptionCDFPanel("panel", params)
    assert "unverified_entry_clock" in node._admission(row, {"spot": 100, "entry_close_at": None})
    assert "unverified_entry_clock" in node._admission(row, {"spot": 100})
    for stamp in ("NaT", "invalid", "2024-03-22T19:59:00"):
        malformed = dict(row, quote_timestamp=stamp)
        reasons = node._admission(malformed, {"spot": 100, "entry_close_at": "2024-03-22T20:00:00Z"})
        assert "invalid_quote_clock" in reasons


@pytest.mark.parametrize("day,expiry,reason", [
    ("2024-03-23", "2024-03-29", "non_session_entry"),
    ("2024-03-22", "2024-03-22", "nonpositive_horizon")])
def test_expiry_labels_retain_boundary_calendar_rejections(day, expiry, reason):
    node = predictive_cdf.ExpiryCloseLabels("labels", {"calendar": "XNYS", "asof": "2024-06-01T20:00:00Z"})
    out = node.run(None, {"bars": [{"symbol": "XYZ", "quote_date": day, "expiry": expiry,
                                   "price_basis": "trade_close"}], "snapshots": [], "prices": []})
    assert reason in out["records"][0]["label_reasons"]


# ---- ADR-0223: count-sized fold tables and the patience stop ---------------

HOLDOUT = '2020-02-24'  # day 54 of a 60-day daily panel: the last six days are held out


def _fold_rows(groups=('A',), n=60, lag=1):
    """Daily rows over ``n`` calendar days whose labels settle ``lag`` days later."""
    from datetime import date, timedelta
    rows = []
    for unit in groups:
        for i in range(n):
            day, settle = date(2020, 1, 1)+timedelta(days=i), date(2020, 1, 1)+timedelta(days=i+lag)
            rows.append({'unit': unit, 'date': day.isoformat(), 'end': settle.isoformat(),
                         'expiry': settle.isoformat(), 'h': 1, 'scale': 1., 't': float(i),
                         'y': ((i*7) % 11-5)/5, 'is_A': int(unit == 'A'),
                         'is_B': int(unit == 'B')})
    return rows


def _fold_table(tmp_path, rows, lag, **over):
    """Run the real ``rolling-origin-plan`` on the dev rows and pin its jsonl."""
    from dskit.pipeline.kinds_split import RollingOriginPlan
    params = {'date_field': 'date', 'end_field': 'end', 'holdout_start': HOLDOUT,
              'embargo_days': lag, 'val_n': 5, 'step_n': 5, 'train_n': 20,
              'warmup_folds': 1, **over}
    out = RollingOriginPlan('plan', params).run(
        None, {'records': [r for r in rows if r['end'] < HOLDOUT]})
    path = tmp_path/'folds.jsonl'
    path.write_text(''.join(json.dumps(r, sort_keys=True, separators=(',', ':'))+'\n'
                            for r in out['records']))
    spec = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'holdout_start': HOLDOUT, 'cal_n': 5, 'roles': ['warmup', 'scored']}
    return spec, out


def _fold_config(tmp_path, spec, name='out'):
    return {'features': ['h', 'scale', 't'], 'group': 'unit', 'date': 'date', 'end': 'end',
            'horizon': 'h', 'target': 'y', 'reference': 'scale', 'fold_table': spec,
            'output': str(tmp_path/name), 'samples': 41, 'tail_intervals': [[-2, -1], [1, 2]],
            'tail_points': 41, 'calibration_knots': 5,
            'bootstrap': {'blocks': [3], 'replicates': 10, 'seed': 4},
            'reference_model': 'reference', 'comparison_references': ['reference'],
            'identity': ['unit', 'date', 'expiry'], 'series_identity': ['unit', 'expiry'],
            'models': {'reference': {
                'class': 'dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
                'params': {'horizon_index': 0, 'reference_index': 1, 'knots': 41},
                'calibrate': True}}}


def _fold_setup(tmp_path, lag=1, **over):
    rows = _fold_rows(lag=lag)
    spec, plan = _fold_table(tmp_path, rows, lag, **over)
    return [r for r in rows if r['end'] < HOLDOUT], spec, plan


def test_year_study_config_identity_is_unchanged_by_the_fold_table_keys():
    from dskit.pipeline.base import config_hash
    c = {'features': ['h', 'scale'], 'group': 'unit', 'date': 'date', 'end': 'end',
         'horizon': 'h', 'target': 'y', 'reference': 'scale', 'years': [2017, 2019],
         'output': 'out', 'samples': 41, 'tail_intervals': [[-2, -1], [1, 2]],
         'tail_points': 41, 'calibration_knots': 5, 'development_end': 2017,
         'bootstrap': {'blocks': [3], 'replicates': 10, 'seed': 4},
         'reference_model': 'reference', 'comparison_references': ['reference'],
         'identity': ['unit', 'date', 'expiry'], 'series_identity': ['unit', 'expiry'],
         'models': {'reference': {
             'class': 'dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
             'params': {'horizon_index': 0, 'reference_index': 1, 'knots': 41},
             'calibrate': True}}}
    # Golden taken on 6394a41, before the fold-table keys existed.
    assert config_hash(ChronologicalCDFStudy(c), exclude=()) == (
        '095b0ebdc2d3c620c6e139b0ac90204ab30d3b54f50a627cc5d98136a6acca7a')


def test_year_hpo_document_is_not_rewritten_by_construction(tmp_path):
    config, _ = _hpo_fixture(tmp_path)
    assert CDFHyperparameterStudy(copy.deepcopy(config)).to_obj() == config


def test_fold_table_bands_follow_the_table_with_a_calibration_tail_and_label_purge(tmp_path):
    frame, spec, plan = _fold_setup(tmp_path)
    scores = ChronologicalCDFStudy(_fold_config(tmp_path, spec)).run(pd.DataFrame(frame))
    counts = json.loads((tmp_path/'out/counts.json').read_text())
    folds, seam = plan['records'], plan['metrics']['scored_start']
    assert [c['fold'] for c in counts] == [f['fold'] for f in folds]
    assert 'year' not in scores and set(scores.fold) == {f['fold'] for f in folds}
    days = sorted({r['date'] for r in frame})
    ends = {r['date']: r['end'] for r in frame}
    for count, fold in zip(counts, folds):
        train = [d for d in days if fold['train_start'] <= d <= fold['train_end']]
        tail = train[-5:]
        fit = [d for d in train[:-5] if ends[d] < tail[0]]
        cal = [d for d in tail if ends[d] < fold['val_start']]
        val = [d for d in days if fold['val_start'] <= d <= fold['val_end']]
        if fold['role'] == 'warmup':
            val = [d for d in val if ends[d] < seam]
        assert len(val) < 5 if fold['role'] == 'warmup' else len(val) == 5
        for name, want in (('fit', fit), ('cal', cal), ('val', val)):
            got = count[name]
            assert (got['n'], got['first'], got['last']) == (len(want), want[0], want[-1]), name
        assert count['fit']['latest_label'] < tail[0]
        assert count['cal']['latest_label'] < fold['val_start']
        assert set(scores[scores.fold == fold['fold']].date) == set(val)


def test_fold_table_purges_a_calibration_row_whose_label_reaches_the_validation_window(tmp_path):
    # The sha pins the table, not the frame: a re-read panel may carry labels that outrun the
    # plan's embargo, so the study's own cal-vs-val purge is what keeps val out of the monitor.
    frame, spec, plan = _fold_setup(tmp_path)
    last = plan['records'][-1]
    late = [r for r in frame if r['date'] == last['train_end']]
    assert len(late) == 1 and late[0]['end'] < last['val_start']  # the plan's embargo held
    late[0]['end'] = late[0]['expiry'] = last['val_start']  # now its label settles on val_start
    ChronologicalCDFStudy(_fold_config(tmp_path, spec)).run(pd.DataFrame(frame))
    cal = json.loads((tmp_path/'out/counts.json').read_text())[-1]['cal']
    days = sorted({r['date'] for r in frame})
    tail = [d for d in days if d <= last['train_end']][-5:]
    assert (cal['n'], cal['last']) == (4, tail[-2])  # the train_end day is gone, not just late
    assert cal['last'] < last['train_end'] and cal['latest_label'] < last['val_start']


def test_fold_table_drops_warmup_val_rows_whose_label_reaches_the_scored_seam(tmp_path):
    frame, spec, plan = _fold_setup(tmp_path, lag=3)
    scores = ChronologicalCDFStudy(_fold_config(tmp_path, spec)).run(pd.DataFrame(frame))
    seam = plan['metrics']['scored_start']
    warmup = scores[scores.fold == 1]
    assert len(warmup) and (warmup.end < seam).all()  # a label settling on the seam is cut
    first_scored = scores[scores.fold == 2]
    assert first_scored.date.min() == seam and len(first_scored.date.unique()) == 5


def test_fold_table_roles_choose_which_folds_are_fitted(tmp_path):
    frame, spec, plan = _fold_setup(tmp_path)
    spec['roles'] = ['scored']
    scores = ChronologicalCDFStudy(_fold_config(tmp_path, spec)).run(pd.DataFrame(frame))
    assert set(scores.fold) == {f['fold'] for f in plan['records'] if f['role'] == 'scored'}


@pytest.mark.parametrize(('mutate', 'match'), [
    (lambda c: c.update(years=[2017]), 'exactly one'),
    (lambda c: c.pop('fold_table'), 'missing keys'),
    (lambda c: c.update(development_end=1), 'development_end'),
    (lambda c: c['fold_table'].update(sha256='0'*64), 'sha256'),
    (lambda c: c['fold_table'].pop('cal_n'), 'cal_n'),
    (lambda c: c['fold_table'].update(cal_n=0), 'cal_n'),
    (lambda c: c['fold_table'].update(cal_n=True), 'cal_n'),
    (lambda c: c['fold_table'].update(roles=[]), 'roles'),
    (lambda c: c['fold_table'].update(roles=['validation']), 'roles'),
    (lambda c: c['fold_table'].update(roles=['scored', 'scored']), 'roles'),
    (lambda c: c['fold_table'].update(holdout_start='2020-02-30'), 'holdout_start'),
    (lambda c: c['fold_table'].update(holdout_start='2020-02-10'), 'holdout'),
    (lambda c: c['fold_table'].update(surprise=1), 'fold_table'),
])
def test_fold_table_config_is_default_deny_and_pins_the_file(tmp_path, mutate, match):
    _, spec, _ = _fold_setup(tmp_path)
    config = _fold_config(tmp_path, spec)
    mutate(config)
    with pytest.raises(ValueError, match=match):
        ChronologicalCDFStudy(config)


def test_fold_table_refuses_a_table_edited_after_it_was_pinned(tmp_path):
    _, spec, _ = _fold_setup(tmp_path)
    path = tmp_path/'folds.jsonl'
    path.write_text(path.read_text().replace('"warmup"', '"scored"', 1))
    with pytest.raises(ValueError, match='sha256'):
        ChronologicalCDFStudy(_fold_config(tmp_path, spec))


@pytest.mark.parametrize('late', [
    {'date': HOLDOUT, 'end': '2020-02-25'},          # entered on the first holdout day
    {'date': '2020-02-23', 'end': HOLDOUT},          # label settles on the first holdout day
])
def test_fold_table_refuses_holdout_rows_before_any_output(tmp_path, late):
    frame, spec, _ = _fold_setup(tmp_path)
    frame = pd.DataFrame(frame + [{**frame[0], **late, 'expiry': late['end']}])
    with pytest.raises(ValueError, match='holdout'):
        ChronologicalCDFStudy(_fold_config(tmp_path, spec)).run(frame)
    assert not (tmp_path/'out').exists()


class _SpyCDF(HorizonEmpiricalCDF):
    """Records what the fit was given: the training rows and the monitor rows."""
    seen = []

    def fit(self, x, y, cal_x, cal_y):
        type(self).seen.append((np.array(x)[:, 2], np.array(cal_x)[:, 2]))
        return super().fit(x, y, cal_x, cal_y)


def test_fit_receives_the_calibration_slice_never_the_scored_window(tmp_path, monkeypatch):
    frame, spec, plan = _fold_setup(tmp_path)
    monkeypatch.setattr(predictive_cdf, '_SpyCDF', _SpyCDF, raising=False)
    _SpyCDF.seen = []
    config = _fold_config(tmp_path, spec)
    config['models']['spy'] = {'class': 'dskit.pipeline.libs.predictive_cdf:_SpyCDF',
                               'params': {'horizon_index': 0, 'reference_index': 1, 'knots': 41},
                               'calibrate': False}
    ChronologicalCDFStudy(config).run(pd.DataFrame(frame))
    index = {r['date']: r['t'] for r in frame}
    assert len(_SpyCDF.seen) == len(plan['records'])
    for (train_t, monitor_t), fold in zip(_SpyCDF.seen, plan['records']):
        window = [index[d] for d in index if fold['train_start'] <= d <= fold['train_end']]
        assert set(monitor_t) <= set(window) and len(set(monitor_t)) <= 5
        assert max(train_t) < min(monitor_t)
        assert max(monitor_t) < index[fold['val_start']]


def _fold_hpo_fixture(tmp_path, lag=1):
    rows = _fold_rows(groups=('A', 'B'), lag=lag)
    spec, plan = _fold_table(tmp_path, rows, lag)
    config = _fold_config(tmp_path, spec, 'unused')
    config['features'] = ['h', 'scale', 'is_A', 'is_B']
    candidate = {'class': 'dskit.pipeline.libs.predictive_cdf:MixtureMLPCDF',
                 'params': {'components': 1, 'hidden': [3], 'epochs': 1, 'seeds': [11],
                            'device': 'cpu'}, 'calibrate': True, 'pooled': False}
    experiment = {
        'output': str(tmp_path/'hpo'), 'final_seeds': [11, 29], 'screen_seed': 11,
        'max_candidates': 1, 'candidates': {'candidate': candidate},
        'candidate_labels': {'candidate': {'sharing': 'separate', 'family': 'normal',
                                           'bundle': 'a'}},
        'axes': {'sharing': ['separate'], 'family': ['normal'], 'bundle': ['a']},
        'task_features': {'A': 'is_A', 'B': 'is_B'},
        'resolutions': {'screen_samples': 21, 'final_samples': 41, 'tail_points': 41,
                        'integration_points': 21, 'audit_samples': [21, 41, 81],
                        'audit_rows': 2},
        'expected_cells': {k: {'A': [1], 'B': [1]} for k in ['development', 'evaluation']},
        'search_partitions': {'separate': ['candidate']}}
    frame = pd.DataFrame([r for r in rows if r['end'] < HOLDOUT])
    return {'study': config, 'experiment': experiment}, frame, plan


def test_hpo_selects_on_warmup_folds_and_evaluates_on_scored_folds_only(tmp_path):
    config, frame, plan = _fold_hpo_fixture(tmp_path)
    study = CDFHyperparameterStudy(config)
    seam = plan['metrics']['scored_start']
    warm = {f['fold'] for f in plan['records'] if f['role'] == 'warmup'}
    scored = {f['fold'] for f in plan['records'] if f['role'] == 'scored'}
    provenance = {'sources': {'test': 'pinned'}, 'readers': {'fixture': 1}}
    study.run(frame, stage='search', partition='separate', provenance=provenance)
    search = pd.read_parquet(tmp_path/'hpo/search/separate/scores.parquet')
    assert set(search.fold) == warm and (search.end < seam).all()
    selected = study.run(frame, stage='select', provenance=provenance)
    assert selected['variants']['reference'] == 'raw'
    study.run(frame, stage='evaluate', partition='development', provenance=provenance)
    study.run(frame, stage='evaluate', partition='later', provenance=provenance)
    development = pd.read_parquet(tmp_path/'hpo/evaluate/development/scores.parquet')
    later = pd.read_parquet(tmp_path/'hpo/evaluate/later/scores.parquet')
    assert set(development.fold) == warm and set(later.fold) == scored
    assert later.date.min() == seam and (development.end < seam).all()
    report = study.run(frame, stage='report', provenance=provenance)
    assert report['selected_variants_from_development'] == selected['variants']
    assert (tmp_path/'hpo/report/skill_by_index_fold.csv').exists()


@pytest.mark.parametrize(('mutate', 'match'), [
    (lambda c: c['experiment'].update(development_years=[2017]), 'derived'),
    (lambda c: c['experiment'].update(label_cutoff='2020-02-01'), 'derived'),
    (lambda c: c['experiment'].update(
        evaluation_partitions={'development': [1], 'later': [2]}), 'derived'),
    (lambda c: c['study']['fold_table'].update(roles=['warmup']), 'both'),
    (lambda c: c['study']['fold_table'].update(roles=['scored']), 'both'),
])
def test_hpo_fold_table_derives_partitions_and_refuses_year_keys(tmp_path, mutate, match):
    config, _, _ = _fold_hpo_fixture(tmp_path)
    mutate(config)
    with pytest.raises(ValueError, match=match):
        CDFHyperparameterStudy(config)


def test_hpo_needs_warmup_folds_to_select_on(tmp_path):
    config, _, _ = _fold_hpo_fixture(tmp_path)
    spec, _ = _fold_table(tmp_path, _fold_rows(groups=('A', 'B')), 1, warmup_folds=0)
    config['study']['fold_table'] = spec
    with pytest.raises(ValueError, match='warmup'):
        CDFHyperparameterStudy(config)


def test_hpo_refuses_a_frame_holding_holdout_rows_at_every_stage(tmp_path):
    config, frame, _ = _fold_hpo_fixture(tmp_path)
    late = {**frame.iloc[0].to_dict(), 'date': HOLDOUT, 'end': '2020-02-25',
            'expiry': '2020-02-25'}
    frame = pd.concat([frame, pd.DataFrame([late])], ignore_index=True)
    study = CDFHyperparameterStudy(config)
    for stage, partition in (('search', 'separate'), ('select', None), ('report', None)):
        with pytest.raises(ValueError, match='holdout'):
            study.run(frame, stage=stage, partition=partition, provenance={'x': 1})


def _patience_model(**over):
    settings = dict(components=1, hidden=[3], epochs=6, batch_size=4, seeds=[3],
                    device='cpu', deterministic=True, patience=2)
    return MixtureMLPCDF(**{**settings, **over})


def _training_rows(n=12):
    x = np.linspace(-1, 1, n)[:, None]
    return x, np.sin(3*x[:, 0])


def _script_monitor(monkeypatch, values):
    """Replace the monitor loss by a script, keeping each epoch's weights."""
    snapshots, script = [], iter(values)

    def scripted(self, model, monitor):
        snapshots.append({k: v.detach().clone() for k, v in model.state_dict().items()})
        return next(script)
    monkeypatch.setattr(MixtureMLPCDF, '_monitor_loss', scripted)
    return snapshots


@pytest.mark.parametrize('bad', [0, -1, True, 1.5, '3'])
def test_patience_is_default_deny(bad):
    with pytest.raises(ValueError, match='patience'):
        _patience_model(patience=bad)


def test_patience_stops_after_the_wait_and_restores_the_best_weights(monkeypatch):
    pytest.importorskip('torch')
    import torch
    snapshots = _script_monitor(monkeypatch, [5., 4., 3., 3.5, 3.2, 9.])
    x, y = _training_rows()
    model = _patience_model().fit(x, y, x, y)
    assert len(model.losses[0]) == 5 and model.best_epochs == [3]  # strict improvement only
    assert len(snapshots) == 5
    for name, value in model.models[0].state_dict().items():
        assert torch.equal(value, snapshots[2][name])


def test_reaching_the_epoch_ceiling_before_the_wait_is_exhausted_raises(monkeypatch):
    pytest.importorskip('torch')
    x, y = _training_rows()
    _script_monitor(monkeypatch, [3., 2., 1.])  # still improving at the ceiling
    with pytest.raises(ValueError, match='patience'):
        _patience_model(epochs=3).fit(x, y, x, y)
    _script_monitor(monkeypatch, [1., 2., 2.])  # stalled, but the wait is not over
    with pytest.raises(ValueError, match='patience'):
        _patience_model(epochs=3, patience=3).fit(x, y, x, y)


def test_monitor_loss_is_the_likelihood_of_the_calibration_rows_only():
    pytest.importorskip('torch')
    from scipy.stats import norm
    x, y = _training_rows()
    cal_x, cal_y = x[::2]+.05, y[::2]+3.  # a different population: it must be the one watched
    model = _patience_model(epochs=60, patience=3).fit(x, y, cal_x, cal_y)
    curve = model.curve(cal_x)
    density = (curve.weights*norm.pdf(cal_y[:, None], curve.means, curve.scales)).sum(1)
    assert min(model.monitor_losses[0]) == pytest.approx(-np.log(density).mean(), rel=1e-4)
    assert len(model.losses[0]) == model.best_epochs[0]+3 < 60


def test_absent_patience_keeps_fixed_epochs_and_the_equivalence_digest():
    torch = pytest.importorskip('torch')
    del torch
    from dskit.pipeline.libs.predictive_cdf import TorchCDF
    params = {'encoder': {'kind': 'mlp'},
              'losses': [{'kind': 'nll', 'weight': .1}, {'kind': 'decision_brier', 'weight': 1.}],
              'components': 2, 'hidden': [4], 'epochs': 2, 'batch_size': 4, 'seeds': [3],
              'device': 'cpu', 'deterministic': True}
    settings = TorchCDF(**params)._equivalence_settings()
    assert 'patience' not in settings  # state digests of existing models are unchanged
    assert hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest() == (
        'cb0130d0d6a4bb353ba21f535b38e06f742b4727278defc8966983117458d759')
    assert TorchCDF(**params, patience=2)._equivalence_settings()['patience'] == 2
    x, y = _training_rows()
    assert len(MixtureMLPCDF(components=1, hidden=[3], epochs=4, seeds=[3], device='cpu')
               .fit(x, y, x, y).losses[0]) == 4


def test_torch_cdf_monitors_the_composite_on_the_attached_calibration_context():
    pytest.importorskip('torch')
    context = _wing_context()+_wing_context()+_wing_context()
    for i, record in enumerate(context):
        record['identity'] = [str(i)]
    cal_context = context[:8]
    x = np.random.default_rng(1).normal(size=(12, 3))
    y = np.linspace(-1.5, 1.5, 12)
    model = predictive_cdf.TorchCDF(
        encoder={'kind': 'mlp'}, components=2, hidden=[4], epochs=400, batch_size=5,
        seeds=[3], device='cpu', deterministic=True, patience=3, learning_rate=.05,
        losses=[{'kind': 'crps', 'weight': 1.}, {'kind': 'wing_twcrps', 'weight': .5}])
    with pytest.raises(ValueError, match='context'):
        model.fit(x, y, x[:8], y[:8])  # no context attached: nothing to train or monitor
    model.fit_decision_context(context, cal_context).fit(x, y, x[:8], y[:8]+.4)
    report = model.loss_report(x[:8], y[:8]+.4, cal_context)
    assert min(model.monitor_losses[0]) == pytest.approx(report['composite'], rel=1e-4)
    state = model._research_state()
    assert state['patience'] == 3 and state['best_epoch_by_seed'] == model.best_epochs
    short = _wing_context()
    for i, record in enumerate(short):
        record['identity'] = [f'c{i}']
    with pytest.raises(ValueError, match='calibration'):
        model.fit_decision_context(context, short).fit(x, y, x[:8], y[:8])


def test_patience_is_refused_where_it_cannot_monitor():
    pytest.importorskip('torch')
    x, y = _training_rows()
    contexts = [_decision_context(('SPY', f'2020-01-{i+1:02}', '2020-02-21')) for i in range(12)]
    with pytest.raises(ValueError, match='patience'):
        DecisionWeightedMixtureMLPCDF(
            components=1, hidden=[3], epochs=2, seeds=[3], device='cpu', patience=1
        ).fit_decision_context(contexts, contexts).fit(x, y, x, y)
    with pytest.raises(ValueError, match='patience'):
        SetMixtureCDF(nodes=1, node_features=1, tensor_indices=[0], mask_indices=[1],
                      context_indices=[2], device='cpu', patience=1)


def test_study_refuses_patience_with_a_second_calibration_map(tmp_path):
    _, spec, _ = _fold_setup(tmp_path)
    config = _fold_config(tmp_path, spec)
    config['models']['net'] = {
        'class': 'dskit.pipeline.libs.predictive_cdf:MixtureMLPCDF',
        'params': {'components': 1, 'hidden': [3], 'epochs': 4, 'seeds': [3], 'device': 'cpu',
                   'patience': 2}, 'calibrate': True}
    with pytest.raises(ValueError, match='second map'):
        ChronologicalCDFStudy(config)
    config['models']['net']['calibrate'] = False
    ChronologicalCDFStudy(config)


def test_fold_roles_agree_with_the_plan_kind():
    from dskit.pipeline import kinds_split
    assert predictive_cdf._FoldPlan.ROLES == (kinds_split._ROLE_WARMUP, kinds_split._ROLE_SCORED)


@pytest.mark.parametrize(('edit', 'match'), [
    (lambda rows: [{**r, 'role': 'warmup'} for r in rows], 'scored fold'),
    (lambda rows: [{**rows[0], 'role': 'scored'}]+[{**r, 'role': 'warmup'} for r in rows[1:]],
     'every warmup fold before it'),
    (lambda rows: rows+rows[:1], 'unique folds'),
    (lambda rows: [{k: v for k, v in rows[0].items() if k != 'val_end'}]+rows[1:], 'plan fold'),
    (lambda rows: [{**rows[0], 'val_start': rows[0]['train_start']}]+rows[1:], 'plan fold'),
])
def test_fold_table_refuses_rows_that_are_not_an_ordered_plan(tmp_path, edit, match):
    _, spec, plan = _fold_setup(tmp_path)
    path = tmp_path/'folds.jsonl'
    path.write_text(''.join(json.dumps(r, sort_keys=True)+'\n' for r in edit(plan['records'])))
    spec['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match=match):
        ChronologicalCDFStudy(_fold_config(tmp_path, spec))


def test_fold_table_pooled_models_fit_once_per_fold_on_the_pooled_bands(tmp_path, monkeypatch):
    rows = _fold_rows(groups=('A', 'B'))
    spec, plan = _fold_table(tmp_path, rows, 1)
    frame = pd.DataFrame([r for r in rows if r['end'] < HOLDOUT])
    monkeypatch.setattr(predictive_cdf, '_SpyCDF', _SpyCDF, raising=False)
    _SpyCDF.seen = []
    config = _fold_config(tmp_path, spec)
    config['models']['spy'] = {'class': 'dskit.pipeline.libs.predictive_cdf:_SpyCDF',
                               'params': {'horizon_index': 0, 'reference_index': 1, 'knots': 41},
                               'calibrate': False, 'pooled': True}
    ChronologicalCDFStudy(config).run(frame)
    assert len(_SpyCDF.seen) == len(plan['records'])  # once per fold, not per group and fold
    for train_t, monitor_t in _SpyCDF.seen:
        assert max(train_t) < min(monitor_t)
