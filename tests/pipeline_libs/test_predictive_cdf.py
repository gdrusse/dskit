"""Independent numerical and temporal contracts for ADR-0189."""

import numpy as np
import pandas as pd
import pytest

from dskit.pipeline.libs.predictive_cdf import (
    CalibratedCurve, ChronologicalCDFStudy, GridCurve, MixtureCurve,
    HorizonEmpiricalCDF, ScaledEmpiricalCDF, MonotoneCDF, QuantileCDF, MixtureMLPCDF,
)


def test_mixture_cdf_hand_values_and_extremes():
    curve = MixtureCurve([[0.25, 0.75]], [[0., 0.]], [[1., 2.]])
    actual = curve.cdf(np.array([[-np.inf, 0., np.inf]]))
    np.testing.assert_allclose(actual, [[0., 0.5, 1.]])


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


def test_study_end_to_end_paired_counts_and_zero_baseline_skill(tmp_path):
    rows = []
    for year in range(2014, 2020):
        for day in range(1, 13):
            rows.append({'unit': 'A', 'date': f'{year}-03-{day:02}',
                         'end': f'{year}-03-{day+1:02}', 'h': 1,
                         'scale': 1., 'y': (day-6)/5})
    frame = pd.DataFrame(rows)
    c = {'features': ['h', 'scale'], 'group': 'unit', 'date': 'date', 'end': 'end',
         'horizon': 'h', 'target': 'y', 'reference': 'scale', 'years': [2017, 2019],
         'output': str(tmp_path/'out'), 'samples': 41, 'tail_intervals': [[-2, -1], [1, 2]],
         'tail_points': 41, 'calibration_knots': 5, 'development_end': 2017,
         'bootstrap': {'blocks': [3], 'replicates': 10, 'seed': 4},
         'reference_model': 'reference', 'comparison_references': ['reference'],
         'models': {'reference': {'class': 'dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
                                  'params': {'horizon_index': 0, 'reference_index': 1, 'knots': 41},
                                  'calibrate': True}}}
    study = ChronologicalCDFStudy(c)
    scores = study.run(frame)
    summary = study.summarize(scores)
    assert len(scores) == 48
    assert summary['metrics'][0]['n'] == 12
    assert summary['metrics'][0]['equal_cell_skill'] == pytest.approx(0)
    assert summary['paired_block_intervals'][0]['lo'] == pytest.approx(0)
    assert summary['paired_block_intervals'][0]['hi'] == pytest.approx(0)
    with pytest.raises(FileExistsError):
        study.run(frame)
