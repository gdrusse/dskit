"""The CDF adapter shares the contract payoff owner and exact units."""

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from index_options.cdf_study import CondorCDFDiagnostic
from index_options.distribution import condor_payoff
from dskit.pipeline.libs.predictive_cdf import CDFHyperparameterStudy, MixtureCurve


def test_predictive_cdf_refinement_config_pins_bounded_grouped_inventory():
    path = Path(__file__).parents[1]/'configs'/'run-predictive-cdf-refinement.json'
    config = json.loads(path.read_text())
    study, experiment = config['study'], config['experiment']
    groups = experiment['candidate_groups']
    assert groups == experiment['search_partitions']
    assert list(groups) == ['weight', 'small', 'regularized', 'mixture']
    assert [len(groups[name]) for name in groups] == [4, 3, 3, 3]
    assert experiment['max_candidates'] == 13
    assert experiment['output'] == 'pipeline_runs/predictive_cdf_refinement_20260928'
    assert study['output'] == 'pipeline_runs/predictive_cdf_refinement_20260928/base'
    assert study['comparison_references'] == ['horizon_empirical', 'incumbent_blend_025']
    assert list(study['models']) == [
        'horizon_empirical', 'pooled_normal_a', 'incumbent_blend_025']
    assert study['models']['horizon_empirical']['calibrate'] is False
    assert study['models']['incumbent_blend_025']['calibrate'] is False
    assert study['features'][27] == 'calendar_dte'
    assert [study['features'][i] for i in [39, 40, 41]] == ['is_SPY', 'is_QQQ', 'is_IWM']
    assert study['features'][38] == 'reference_scale'
    assert experiment['development_years'] == [2016, 2017, 2018]
    assert experiment['label_cutoff'] == '2019-01-01'
    assert experiment['evaluation_partitions'] == {
        'development': [2016, 2017, 2018], 'early': [2019, 2020, 2021],
        'middle': [2022, 2023], 'late': [2024, 2025]}
    assert sum(len(v) for v in experiment['expected_cells']['development'].values()) == 133
    assert sum(len(v) for v in experiment['expected_cells']['evaluation'].values()) == 135

    candidates = experiment['candidates']
    assert [candidates[name]['params']['mlp_weight'] for name in groups['weight']] == [
        .15, .2, .3, .35]
    for group in ('small', 'regularized', 'mixture'):
        assert [candidates[name]['params']['mlp_weight'] for name in groups[group]] == [
            .15, .25, .35]
    endpoint = study['models']['pooled_normal_a']['params']
    assert endpoint == {
        'components': 1, 'hidden': [16], 'epochs': 20, 'batch_size': 1024,
        'learning_rate': .003, 'weight_decay': .1, 'min_scale': .1,
        'activation': 'tanh', 'dropout': 0, 'seeds': [11, 29],
        'device': 'cuda', 'deterministic': True}
    expected_neural = {
        'weight': endpoint,
        'small': {**endpoint, 'hidden': [8]},
        'regularized': {**endpoint, 'weight_decay': .3},
        'mixture': {**endpoint, 'components': 3},
    }
    labels = {
        'weight': 'pooled_normal_a_endpoint',
        'small': 'small_normal_endpoint',
        'regularized': 'regularized_normal_endpoint',
        'mixture': 'three_normal_mixture_endpoint',
    }
    for group, names in groups.items():
        assert all(candidates[name]['params']['mlp'] == expected_neural[group] for name in names)
        assert all(candidates[name]['equivalence'] == labels[group] for name in names)
        assert all(candidates[name]['pooled'] is True for name in names)
        assert all(candidates[name]['calibrate'] is True for name in names)
    assert study['models']['pooled_normal_a']['equivalence'] == labels['weight']
    incumbent = study['models']['incumbent_blend_025']
    assert incumbent['params']['mlp_weight'] == .25
    assert incumbent['params']['mlp'] == endpoint
    assert incumbent['equivalence'] == labels['weight']
    assert incumbent['pooled'] is True
    assert all(candidates[name]['params']['condition_indices'] == [27, 39, 40, 41]
               and candidates[name]['params']['reference_index'] == 38
               and candidates[name]['params']['knots'] == 401
               for names in groups.values() for name in names)
    assert experiment['resolutions'] == {
        'screen_samples': 101, 'final_samples': 401, 'tail_points': 201,
        'integration_points': 101, 'audit_samples': [101, 401, 1601], 'audit_rows': 12}
    CDFHyperparameterStudy(config)

    bad_group = copy.deepcopy(config)
    bad_group['experiment']['candidate_groups']['small'][0] = groups['weight'][0]
    with np.testing.assert_raises_regex(ValueError, 'candidate groups'):
        CDFHyperparameterStudy(bad_group)
    bad_label = copy.deepcopy(config)
    bad_label['experiment']['candidates'][groups['small'][0]]['equivalence'] = 'singleton'
    with np.testing.assert_raises_regex(ValueError, 'equivalence group'):
        CDFHyperparameterStudy(bad_label)

    prior = Path(__file__).parents[1]/'configs'/'run-predictive-cdf-methods.json'
    assert hashlib.sha256(prior.read_bytes()).hexdigest() == (
        'da174820172b462df1d309a94c851e08059cda64074dc654d573144566624f98')
    previous = json.loads(prior.read_text())
    assert config['data'] == previous['data']
    assert config['diagnostic'] == previous['diagnostic']
    unchanged_study = set(previous['study'])-{'output', 'comparison_references', 'models'}
    assert {key: study[key] for key in unchanged_study} == {
        key: previous['study'][key] for key in unchanged_study}
    unchanged_experiment = set(previous['experiment'])-{
        'notes', 'output', 'max_candidates', 'candidates',
        'search_partitions', 'candidate_groups'}
    assert {key: experiment[key] for key in unchanged_experiment} == {
        key: previous['experiment'][key] for key in unchanged_experiment}
    old_empirical = previous['study']['models']['horizon_empirical']
    assert study['models']['horizon_empirical']['class'] == old_empirical['class']
    assert study['models']['horizon_empirical']['params'] == old_empirical['params']


def test_predictive_cdf_methods_config_pins_bounded_grouped_inventory():
    path = Path(__file__).parents[1]/'configs'/'run-predictive-cdf-methods.json'
    config = json.loads(path.read_text())
    study, experiment = config['study'], config['experiment']
    groups = experiment['candidate_groups']
    assert groups == experiment['search_partitions']
    assert list(groups) == ['forest', 'ngboost', 'blend']
    assert [len(groups[name]) for name in groups] == [3, 3, 3]
    assert set(experiment) & {'axes', 'candidate_labels', 'screen_seed', 'final_seeds'} == set()
    assert experiment['output'] == 'pipeline_runs/predictive_cdf_methods_20260928'
    assert experiment['evaluation_partitions'] == {
        'development': [2016, 2017, 2018], 'early': [2019, 2020, 2021],
        'middle': [2022, 2023], 'late': [2024, 2025]}
    assert sum(len(v) for v in experiment['expected_cells']['development'].values()) == 133
    assert sum(len(v) for v in experiment['expected_cells']['evaluation'].values()) == 135
    assert study['features'][27] == 'calendar_dte'
    assert [study['features'][i] for i in [39, 40, 41]] == ['is_SPY', 'is_QQQ', 'is_IWM']
    assert study['features'][38] == 'reference_scale'
    equivalence = 'pooled_normal_a_endpoint'
    assert study['models']['pooled_normal_a']['equivalence'] == equivalence
    assert all(experiment['candidates'][name]['equivalence'] == equivalence
               for name in groups['blend'])
    assert all(spec['pooled'] is True for spec in experiment['candidates'].values())
    assert [experiment['candidates'][name]['params']['min_child']
            for name in groups['forest']] == [20, 50, 100]
    assert all(experiment['candidates'][name]['params']['max_samples_leaf'] is None
               for name in groups['forest'])
    ngboost = [experiment['candidates'][name]['params'] for name in groups['ngboost']]
    assert [(p['depth'], p['min_child'], p['learning_rate']) for p in ngboost] == [
        (2, 20, .03), (3, 20, .03), (2, 50, .05)]
    assert all((p['trees'], p['minibatch_frac'], p['col_sample'], p['tol'], p['seed'])
               == (100, .8, 1., .0001, 829) for p in ngboost)
    blends = [experiment['candidates'][name] for name in groups['blend']]
    assert [spec['params']['mlp_weight'] for spec in blends] == [.1, .25, .5]
    control_mlp = study['models']['pooled_normal_a']['params']
    assert all(spec['params']['mlp'] == control_mlp for spec in blends)
    assert control_mlp == {
        'components': 1, 'hidden': [16], 'epochs': 20, 'batch_size': 1024,
        'learning_rate': .003, 'weight_decay': .1, 'min_scale': .1,
        'activation': 'tanh', 'dropout': 0, 'seeds': [11, 29],
        'device': 'cuda', 'deterministic': True}
    assert experiment['resolutions'] == {
        'screen_samples': 101, 'final_samples': 401, 'tail_points': 201,
        'integration_points': 101, 'audit_samples': [101, 401, 1601], 'audit_rows': 12}
    CDFHyperparameterStudy(config)


def test_vectorized_payoff_matches_existing_owner_at_every_kink():
    strikes = np.array([[80., 90., 110., 120.]])
    prices = np.array([[70., 80., 85., 90., 100., 110., 115., 120., 130.]])
    actual = CondorCDFDiagnostic.payoff(prices, strikes)[0]
    np.testing.assert_allclose(actual, [condor_payoff(x, strikes[0]) for x in prices[0]])
    np.testing.assert_allclose(actual, [-10, -10, -5, 0, 0, 0, -5, -10, -10])


def test_cdf_integral_agrees_with_payoff_quadrature_and_loss_sign():
    frame = pd.DataFrame({'reference_scale': [.04], 'spot': [100.], 'terminal_price': [100.]})
    curve = MixtureCurve([[.2, .8]], [[-1, .2]], [[1.5, .7]])
    draws = curve.quantile((np.arange(8001)+.5)/8001)
    metrics = CondorCDFDiagnostic([-2, -1, 1, 2], 1001)(frame, curve, draws)
    assert metrics['payoff_quadrature_gap'][0] < .0001
    assert metrics['realized_loss_per_share'][0] == 0
    assert metrics['expected_loss_per_share'][0] > 0
    assert metrics['condor_loss_bias'][0] > 0


def test_student_extreme_draws_preserve_bounded_payoff_without_overflow():
    from dskit.pipeline.libs.predictive_cdf import StudentMixtureCurve
    frame = pd.DataFrame({'reference_scale': [.04], 'spot': [100.], 'terminal_price': [100.]})
    curve = StudentMixtureCurve([[1]], [[0]], [[1]], degrees=3)
    diagnostic = CondorCDFDiagnostic([-2, -1, 1, 2], 1001)
    with np.errstate(over='raise', invalid='raise'):
        extreme = diagnostic(frame, curve, np.array([[-1e6, 1e6]]))
    clipped = diagnostic(frame, curve, np.array([[-2., 2.]]))
    for key in extreme:
        np.testing.assert_allclose(extreme[key], clipped[key])
    draws = curve.quantile((np.arange(8001)+.5)/8001)
    assert diagnostic(frame, curve, draws)['payoff_quadrature_gap'][0] < .0001


def test_panel_exact_holiday_settlement_missing_path_and_dividend_flag(tmp_path, monkeypatch):
    import exchange_calendars as xc
    from index_options.cdf_study import ExactExpiryCDFPanel

    dates = xc.get_calendar('XNYS').sessions_in_range('2023-01-03', '2023-07-10')
    prices = {d.strftime('%Y-%m-%d'): 100+i*.1 for i, d in enumerate(dates)}

    class Reader:
        def __init__(self, key, params):
            self.symbol = params['symbol']

        def run(self, ctx, inputs):
            rows = [{'date': d, 'close': 20. if self.symbol == 'RVX' else price,
                     'asof_ms': int(pd.Timestamp(d).timestamp()*1000),
                     'instrument': self.symbol, 'contract': self.symbol,
                     'group': self.symbol, 'dividend_amount': None}
                    for d, price in prices.items() if d != '2023-07-06']
            return {'records': rows}

        def fingerprint(self):
            return {'sha256': 'fixture'}

    monkeypatch.setattr('index_options.cdf_study.IndexCloseRows', Reader)
    rows = pd.DataFrame({'symbol': ['IWM']*4,
                         'quote_date': ['2023-06-29', '2023-06-29', '2023-07-03', '2023-07-04'],
                         'expiry': ['2023-07-01', '2023-07-04', '2023-07-07', '2023-07-07'],
                         'chain_underlying_price': [prices['2023-06-29'], prices['2023-06-29'],
                                                    prices['2023-07-03'], prices['2023-07-03']]})
    surface = tmp_path/'surface.parquet'
    lifecycle = tmp_path/'lifecycle.parquet'
    rows.to_parquet(surface)
    rows[['symbol', 'quote_date', 'expiry']].assign(first_seen_date='2023-06-01').to_parquet(lifecycle)
    adapter = ExactExpiryCDFPanel({'root': 'fixture', 'surface': str(surface), 'lifecycle': str(lifecycle),
                                  'symbols': {'IWM': 'RVX'}, 'price_source': 'fixture', 'iv_source': 'fixture',
                                  'since': '2023-01-01', 'max_dte': 45, 'lags': 22,
                                  'windows': [1, 5, 22, 66], 'feature_gap_days': 7,
                                  'reference_floor': .001, 'spot_tolerance': .02})
    out = adapter.read()
    assert out.settlement_date.tolist() == ['2023-06-30', '2023-07-03']
    assert out.sessions_to_expiry.tolist() == [1, 2]
    assert out.calendar_dte.tolist() == [1, 4]
    assert not out.dividends_known.any()
    np.testing.assert_allclose(out.terminal_return, np.log(
        np.array([prices['2023-06-30'], prices['2023-07-03']])/prices['2023-06-29']))
    assert adapter.refused['IWM']['incomplete_path'] == 1
    assert adapter.refused['IWM']['non_session_quote'] == 1


def test_future_unscheduled_closure_is_label_information_only(tmp_path, monkeypatch):
    import exchange_calendars as xc
    from index_options.cdf_study import ExactExpiryCDFPanel

    dates = xc.get_calendar('XNYS').sessions_in_range('2024-01-02', '2025-01-10')
    prices = {d.strftime('%Y-%m-%d'): 100+i*.1 for i, d in enumerate(dates)}

    class Reader:
        def __init__(self, key, params):
            self.symbol = params['symbol']

        def run(self, ctx, inputs):
            return {'records': [
                {'date': d, 'close': 20. if self.symbol == 'VIX' else price,
                 'asof_ms': int(pd.Timestamp(d).timestamp()*1000),
                 'instrument': self.symbol, 'contract': self.symbol,
                 'group': self.symbol, 'dividend_amount': 0.}
                for d, price in prices.items()]}

        def fingerprint(self):
            return {'sha256': 'fixture'}

    monkeypatch.setattr('index_options.cdf_study.IndexCloseRows', Reader)
    rows = pd.DataFrame({'symbol': ['SPY', 'SPY'], 'quote_date': ['2024-12-26']*2,
                         'expiry': ['2025-01-08', '2025-01-09'],
                         'chain_underlying_price': [prices['2024-12-26']]*2})
    surface, lifecycle = tmp_path/'surface.parquet', tmp_path/'lifecycle.parquet'
    rows.to_parquet(surface)
    rows[['symbol', 'quote_date', 'expiry']].assign(first_seen_date='2024-12-02').to_parquet(lifecycle)
    adapter = ExactExpiryCDFPanel({'root': 'fixture', 'surface': str(surface), 'lifecycle': str(lifecycle),
                                  'symbols': {'SPY': 'VIX'}, 'price_source': 'fixture', 'iv_source': 'fixture',
                                  'since': '2024-01-01', 'max_dte': 45, 'lags': 22,
                                  'windows': [1, 5, 22, 66], 'feature_gap_days': 7,
                                  'reference_floor': .001, 'spot_tolerance': .02})
    out = adapter.read()
    assert out.expiry.tolist() == ['2025-01-08', '2025-01-09']
    assert out.settlement_date.tolist() == ['2025-01-08']*2
    assert out.actual_calendar_dte.tolist() == [13, 13]
    assert out.calendar_dte.tolist() == [13, 14]
    assert out.actual_sessions_to_expiry.tolist() == [8, 8]
    assert out.sessions_to_expiry.tolist() == [8, 9]
    np.testing.assert_allclose(out.log_calendar_dte, np.log([13, 14]))
    np.testing.assert_allclose(out.log_sessions_to_expiry, np.log([8, 9]))
    np.testing.assert_allclose(out.series_total_tenor_calendar, out.series_age_calendar+[13, 14])
    np.testing.assert_allclose(out.series_total_tenor_sessions, out.series_age_sessions+[8, 9])
    np.testing.assert_allclose(out.life_fraction_calendar,
                               out.series_age_calendar/(out.series_age_calendar+[13, 14]))
    np.testing.assert_allclose(out.life_fraction_sessions,
                               out.series_age_sessions/(out.series_age_sessions+[8, 9]))
    np.testing.assert_allclose(out.reference_scale, np.maximum(out.rv_22, .001)*np.sqrt([8, 9]))
