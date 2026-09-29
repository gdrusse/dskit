"""Independent numerical and temporal contracts for ADR-0189."""

import numpy as np
import pandas as pd
import pytest

from dskit.pipeline.libs.predictive_cdf import (
    CalibratedCurve, ChronologicalCDFStudy, GridCurve, MixtureCurve,
    HorizonEmpiricalCDF, ScaledEmpiricalCDF, MonotoneCDF, QuantileCDF, MixtureMLPCDF,
    NGBoostCDF, QuantileForestCDF,
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
