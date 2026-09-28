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


def test_hpo_json_stages_freeze_development_and_refuse_partial_or_changed_inputs(tmp_path):
    import json
    import dskit.pipeline.libs.predictive_cdf as pack
    assert hasattr(pack, 'CDFHyperparameterStudy'), 'standard JSON HPO orchestration is missing'
    config, frame = _hpo_fixture(tmp_path)
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
    study.run(frame, stage='evaluate', partition='development', provenance=provenance)
    study.run(frame, stage='evaluate', partition='later', provenance=provenance)
    report = study.run(frame, stage='report', provenance=provenance)
    assert report['selected_variants_from_development'] == selected['variants']
    assert all(m['n'] == 10 for m in report['metrics'])
    assert json.loads((tmp_path/'hpo/report/complete.json').read_text())['stage'] == 'report'
    assert (tmp_path/'hpo/report/convergence.json').exists()
    with pytest.raises(FileExistsError):
        study.run(frame, stage='search', partition='separate', provenance=provenance)
    scores_path = tmp_path/'hpo/search/separate/scores.parquet'
    search.iloc[:-1].to_parquet(scores_path, index=False)
    with pytest.raises(ValueError, match='hash|artifact'):
        study.run(frame, stage='select', provenance=provenance)


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
