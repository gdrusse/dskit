"""The CDF adapter shares the contract payoff owner and exact units."""

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from index_options.cdf_study import CondorCDFDiagnostic, ExactExpiryCDFPanel, RawChainFeatureBuilder
from index_options.distribution import condor_payoff
from dskit.pipeline.libs.predictive_cdf import CDFHyperparameterStudy, MixtureCurve


def test_option_surface_features_are_scale_stable_and_preserve_missingness():
    rows = pd.DataFrame({
        'chain_atm_iv': [.2, .3], 'chain_put25_iv': [.3, np.nan],
        'chain_call25_iv': [.15, .2], 'chain_rel_spread': [.01, np.nan],
        'chain_put_call_oi': [2., np.nan], 'chain_contracts': [99., 0.],
        'chain_open_interest': [999., np.nan], 'chain_quote_depth': [4., 0.],
    })
    out = ExactExpiryCDFPanel.add_surface_features(rows.copy())
    assert out.chain_log_atm_iv.tolist() == pytest.approx(np.log([.2, .3]))
    assert out.chain_log_skew25.iloc[0] == pytest.approx(np.log(.3)-np.log(.15))
    assert out.chain_log_curvature25.iloc[0] == pytest.approx(
        .5*(np.log(.3)+np.log(.15))-np.log(.2))
    assert np.isnan(out.chain_log_skew25.iloc[1])
    assert np.isnan(out.chain_log_curvature25.iloc[1])
    np.testing.assert_array_equal(out.chain_has_25d_pair, [1, 0])
    np.testing.assert_array_equal(out.chain_has_put_call_oi, [1, 0])
    assert out.chain_log_rel_spread.iloc[0] == pytest.approx(np.log1p(.01))
    assert np.isnan(out.chain_log_rel_spread.iloc[1])
    assert out.chain_log_put_call_oi.iloc[0] == pytest.approx(np.log(2))
    assert out.chain_log_contracts.iloc[0] == pytest.approx(np.log1p(99))
    assert out.chain_log_contracts.iloc[1] == 0
    assert np.isnan(out.chain_log_open_interest.iloc[1])


def test_surface_dynamics_are_causal_by_expiry_and_emit_proxy_moments():
    dates = pd.bdate_range('2020-01-02', periods=23).strftime('%Y-%m-%d')
    rows = []
    for expiry, offset, dte in [('2020-03-20', 0., 40), ('2020-04-17', .1, 68)]:
        for i, date in enumerate(dates):
            rows.append({'symbol': 'SPY', 'expiry': expiry, 'quote_date': date,
                         'actual_calendar_dte': dte-i, 'chain_atm_iv': .2,
                         'rv_22': .01, 'chain_log_atm_iv': i/100+offset,
                         'chain_log_skew25': i/200, 'chain_log_curvature25': i/300,
                         'chain_log_put_call_oi': .2,
                         'rn_q_1000': -1., 'rn_q_5000': 0., 'rn_q_9000': 2.})
    out = ExactExpiryCDFPanel.add_surface_dynamics(pd.DataFrame(rows), [.1, .5, .9])
    first = out[out.expiry.eq('2020-03-20')].sort_values('quote_date')
    assert first.chain_log_atm_iv_change_22.iloc[-1] == pytest.approx(.22)
    assert first.chain_log_atm_iv_change_22.iloc[:22].isna().all()
    assert out.term_slope_next.notna().sum() == len(dates)
    assert out.term_slope_prev.notna().sum() == len(dates)
    assert out.rn_variance.gt(0).all() and out.rn_right_tail_integral.gt(0).all()


def test_fred_daily_availability_waits_for_one_complete_exchange_session():
    dates = pd.to_datetime(['2020-07-01', '2020-07-02'])
    available = ExactExpiryCDFPanel._availability_dates(dates, lag_sessions=1)
    assert available.strftime('%Y-%m-%d').tolist() == ['2020-07-06', '2020-07-07']


def test_fred_age_and_staleness_use_observation_date(monkeypatch):
    class FakeObservationRows:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            return {'records': [{'observation_date': '2020-07-01', 'value': 7.}]}

        def fingerprint(self):
            return {'sha256': 'fake'}

    monkeypatch.setattr('index_options.cdf_study.ObservationRows', FakeObservationRows)
    panel = ExactExpiryCDFPanel({'root': '/unused'})
    panel.reader_fingerprints = {}
    frame = pd.DataFrame({'quote_date': ['2020-07-06', '2020-07-07']})
    out = panel._join_fred_features(frame, {
        'rate': {'stream': 'fake', 'field': 'value', 'lag_sessions': 1,
                 'max_age_days': 5},
    })
    assert out.rate_age_days.tolist() == [5, 6]
    assert out.rate.iloc[0] == pytest.approx(7.)
    assert np.isnan(out.rate.iloc[1])


def test_ohlc_features_use_only_current_and_prior_prices():
    prices = pd.DataFrame({
        'date': pd.bdate_range('2020-01-02', periods=6).strftime('%Y-%m-%d'),
        'open': [100., 102., 101., 104., 103., 106.],
        'high': [102., 103., 105., 105., 107., 108.],
        'low': [99., 100., 100., 102., 102., 104.],
        'close': [101., 101., 104., 103., 106., 105.],
    })
    first = ExactExpiryCDFPanel.ohlc_features(prices.iloc[:5], [3])
    extended = ExactExpiryCDFPanel.ohlc_features(prices, [3]).iloc[:5]
    pd.testing.assert_frame_equal(first.reset_index(drop=True), extended.reset_index(drop=True))
    row = first.iloc[1]
    assert row.overnight_return == pytest.approx(np.log(102/101))
    assert row.intraday_return == pytest.approx(np.log(101/102))
    assert row.log_high_low_range == pytest.approx(np.log(103/100))
    assert row.parkinson_variance == pytest.approx(np.log(103/100)**2/(4*np.log(2)))
    assert np.isnan(first.range_variance_3.iloc[1])
    assert np.isfinite(first.range_variance_3.iloc[-1])


def test_matched_dte_vrp_uses_requested_sessions_not_actual_dte():
    frame = pd.DataFrame({
        'chain_atm_iv': [.20], 'rv_22': [.01], 'sessions_to_expiry': [10],
        'actual_calendar_dte': [99],
    })
    out = ExactExpiryCDFPanel.add_matched_dte_vrp(frame.copy(), 1e-3)
    implied = .20**2*10/252
    realized = .01**2*10
    assert out.matched_implied_variance.iloc[0] == pytest.approx(implied)
    assert out.matched_trailing_variance.iloc[0] == pytest.approx(realized)
    assert out.matched_vrp.iloc[0] == pytest.approx(implied-realized)
    changed = frame.copy()
    changed.actual_calendar_dte = 2
    other = ExactExpiryCDFPanel.add_matched_dte_vrp(changed, 1e-3)
    columns = ['matched_implied_variance', 'matched_trailing_variance',
               'matched_vrp', 'matched_vrp_ratio']
    pd.testing.assert_frame_equal(out[columns], other[columns])


def test_macro_event_windows_require_entry_known_schedule_records():
    frame = pd.DataFrame({
        'quote_date': ['2020-01-02', '2020-01-10'],
        'planned_settlement_date': ['2020-01-31', '2020-01-31'],
    })
    records = {'fomc': [
        {'event_date': '2020-01-15', 'known_at': '2019-12-01'},
        {'event_date': '2020-01-20', 'known_at': '2020-01-05'},
        {'event_date': '2020-02-01', 'known_at': '2019-12-01'},
    ]}
    out, status = ExactExpiryCDFPanel.add_macro_event_features(frame.copy(), records)
    assert out.macro_fomc_count.tolist() == [1, 2]
    assert out.macro_any_event.tolist() == [1, 1]
    assert status == {'available': True, 'families': ['fomc']}
    unchanged, missing = ExactExpiryCDFPanel.add_macro_event_features(frame.copy(), None)
    pd.testing.assert_frame_equal(unchanged, frame)
    assert missing['available'] is False and 'point-in-time' in missing['reason']


def test_dividend_path_missing_marks_optimizer_ineligible_without_imputation():
    dividends = pd.Series([np.nan, np.nan, np.nan])
    eligible = ExactExpiryCDFPanel.dividend_window_eligibility(dividends, [0], [2])
    assert eligible.tolist() == [False]
    assert dividends.isna().all()


def test_raw_chain_flow_and_greek_aggregates_are_order_invariant_and_lag_oi():
    dates = ['2020-01-02']*2+['2020-01-03']*2+['2020-01-06']*2
    chain = pd.DataFrame({
        'symbol': ['SPY']*6, 'date': dates, 'expiration': ['2020-02-21']*6,
        'strike': [95.,105.]*3, 'type': ['put','call']*3,
        'mark': [2.,2.]*3, 'bid': [1.9]*6, 'ask': [2.1]*6,
        'bid_size': [10]*6, 'ask_size': [12]*6,
        'open_interest': [100,200,110,220,130,240],
        'volume': [10,20,15,25,18,30],
        'implied_volatility': [.2]*6, 'delta': [-.3,.3]*3,
        'gamma': [.01]*6, 'vega': [.1]*6,
    })
    meta = pd.DataFrame({
        'symbol': ['SPY']*3, 'quote_date': ['2020-01-02','2020-01-03','2020-01-06'],
        'expiry': ['2020-02-21']*3, 'chain_underlying_price': [100.]*3,
    })
    builder = RawChainFeatureBuilder(nodes=3, moneyness_bounds=[-.1,.1],
        max_node_gap=.1, proxy_probabilities=[.1,.5,.9], min_wing_nodes=1,
        max_inner_gap=.1, max_outer_gap=.2)
    first = builder.transform(chain, meta)
    shuffled = builder.transform(chain.sample(frac=1, random_state=8), meta)
    pd.testing.assert_frame_equal(first, shuffled)
    assert first.chain_log_volume.iloc[-1] == pytest.approx(np.log1p(48))
    assert first.chain_put_call_volume_imbalance.iloc[-1] == pytest.approx((18-30)/48)
    assert np.isnan(first.chain_log_lag_open_interest.iloc[0])
    assert first.chain_log_lag_open_interest.iloc[1] == pytest.approx(np.log1p(300))
    assert first.chain_log_lag_oi_change.iloc[2] == pytest.approx(np.log1p(330)-np.log1p(300))
    assert first.chain_log_gamma_oi.iloc[-1] == pytest.approx(np.log1p(.01*(130+240)))


def test_raw_chain_builder_is_order_invariant_and_emits_valid_proxy_quantiles():
    chain = pd.DataFrame({
        'symbol': ['SPY']*10, 'date': ['2020-01-02']*10,
        'expiration': ['2020-02-21']*10,
        'strike': np.repeat([90., 95., 100., 105., 110.], 2),
        'type': ['put', 'call']*5,
        'mark': [1., 11., 2., 7., 4., 4., 8., 2., 12., 1.],
        'bid': [.9, 10.9, 1.9, 6.9, 3.9, 3.9, 7.9, 1.9, 11.9, .9],
        'ask': [1.1, 11.1, 2.1, 7.1, 4.1, 4.1, 8.1, 2.1, 12.1, 1.1],
        'bid_size': [10]*10, 'ask_size': [12]*10,
        'open_interest': [100]*10, 'implied_volatility': [.3, .2, .27, .21, .25, .22, .24, .23, .23, .25],
    })
    meta = pd.DataFrame({'symbol': ['SPY'], 'quote_date': ['2020-01-02'],
                         'expiry': ['2020-02-21'], 'chain_underlying_price': [100.]})
    builder = RawChainFeatureBuilder(nodes=5, moneyness_bounds=[-.15, .15],
                                     max_node_gap=.08, proxy_probabilities=[.1, .25, .5, .75, .9],
                                     min_wing_nodes=2, max_inner_gap=.08, max_outer_gap=.2)
    a = builder.transform(chain, meta)
    b = builder.transform(chain.sample(frac=1, random_state=4), meta)
    pd.testing.assert_frame_equal(a, b)
    q = a.filter(regex='^rn_q_').to_numpy()[0]
    assert np.isfinite(q).all() and (np.diff(q) >= 0).all()
    assert a.rn_proxy_eligible.iloc[0] == 1
    assert a.filter(regex='^chain_node_').shape[1] == 5*(5+1)


def test_raw_chain_builder_refuses_one_sided_proxy_without_crashing():
    chain = pd.DataFrame({
        'symbol': ['SPY'], 'date': ['2020-01-02'], 'expiration': ['2020-02-21'],
        'strike': [100.], 'type': ['call'], 'mark': [4.], 'bid': [3.9], 'ask': [4.1],
        'bid_size': [10], 'ask_size': [12], 'open_interest': [100],
        'implied_volatility': [.2],
    })
    meta = pd.DataFrame({'symbol': ['SPY'], 'quote_date': ['2020-01-02'],
                         'expiry': ['2020-02-21'], 'chain_underlying_price': [100.]})
    builder = RawChainFeatureBuilder(nodes=5, moneyness_bounds=[-.15, .15],
                                     max_node_gap=.08, proxy_probabilities=[.1, .25,.5,.75,.9],
                                     min_wing_nodes=2, max_inner_gap=.08, max_outer_gap=.2)
    out = builder.transform(chain, meta)
    assert out.rn_proxy_eligible.iloc[0] == 0
    assert out.filter(regex='^rn_q_').isna().all(axis=None)


def test_raw_chain_builder_masks_crossed_and_duplicate_contracts_deterministically():
    chain = pd.DataFrame({
        'symbol': ['SPY']*12, 'date': ['2020-01-02']*12,
        'expiration': ['2020-02-21']*12,
        'strike': [90,90,95,95,100,100,105,105,110,110,100,100],
        'type': ['put','call']*5+['put','put'],
        'mark': [1,11,2,7,4,4,8,2,12,1,4,4],
        'bid': [.9,10.9,1.9,6.9,4.1,3.9,7.9,1.9,11.9,.9,3.9,3.9],
        'ask': [1.1,11.1,2.1,7.1,3.9,4.1,8.1,2.1,12.1,1.1,4.1,4.1],
        'bid_size': [10]*12, 'ask_size': [12]*12,
        'open_interest': [100]*12, 'implied_volatility': [.25]*12,
    })
    meta = pd.DataFrame({'symbol': ['SPY'], 'quote_date': ['2020-01-02'],
                         'expiry': ['2020-02-21'], 'chain_underlying_price': [100.]})
    builder = RawChainFeatureBuilder(nodes=5, moneyness_bounds=[-.15, .15],
                                     max_node_gap=.08, proxy_probabilities=[.1,.25,.5,.75,.9],
                                     min_wing_nodes=2, max_inner_gap=.08, max_outer_gap=.2)
    first = builder.transform(chain, meta)
    second = builder.transform(chain.sample(frac=1, random_state=7), meta)
    clean = builder.transform(builder._usable_quotes(chain), meta)
    pd.testing.assert_frame_equal(first, second)
    pd.testing.assert_frame_equal(first, clean)


def test_raw_chain_archive_cache_is_bound_to_annual_file_content(tmp_path):
    archive = tmp_path/'archive'; (archive/'spy').mkdir(parents=True)
    source = archive/'spy'/'options_2020.parquet'
    row = {'symbol': 'SPY', 'date': '2020-01-02', 'expiration': '2020-02-21',
           'strike': 100., 'type': 'call', 'mark': 4., 'bid': 3.9, 'ask': 4.1,
           'bid_size': 10, 'ask_size': 12, 'open_interest': 100,
           'implied_volatility': .2}
    pd.DataFrame([row]).to_parquet(source, index=False)
    meta = pd.DataFrame({'symbol': ['SPY'], 'quote_date': ['2020-01-02'],
                         'expiry': ['2020-02-21'], 'chain_underlying_price': [100.]})
    builder = RawChainFeatureBuilder(nodes=3, moneyness_bounds=[-.1, .1],
                                     max_node_gap=.1, proxy_probabilities=[.1,.5,.9],
                                     min_wing_nodes=1, max_inner_gap=.1, max_outer_gap=.2)
    output = tmp_path/'features.parquet'
    first = builder.build_archive(meta, archive, output)
    first_manifest = Path(str(output)+'.sources.json').read_text()
    row['implied_volatility'] = .3
    pd.DataFrame([row]).to_parquet(source, index=False)
    second = builder.build_archive(meta, archive, output)
    second_manifest = Path(str(output)+'.sources.json').read_text()
    assert first.chain_node_01_iv.iloc[0] == pytest.approx(.2)
    assert second.chain_node_01_iv.iloc[0] == pytest.approx(.3)
    assert first_manifest != second_manifest


def test_raw_chain_archive_cache_is_bound_to_spot_metadata(tmp_path):
    archive = tmp_path/'archive'; (archive/'spy').mkdir(parents=True)
    source = archive/'spy'/'options_2020.parquet'
    pd.DataFrame([{
        'symbol': 'SPY', 'date': '2020-01-02', 'expiration': '2020-02-21',
        'strike': 100., 'type': 'call', 'mark': 4., 'bid': 3.9, 'ask': 4.1,
        'bid_size': 10, 'ask_size': 12, 'open_interest': 100,
        'implied_volatility': .2,
    }]).to_parquet(source, index=False)
    meta = pd.DataFrame({'symbol': ['SPY'], 'quote_date': ['2020-01-02'],
                         'expiry': ['2020-02-21'], 'chain_underlying_price': [100.]})
    builder = RawChainFeatureBuilder(nodes=3, moneyness_bounds=[-.1, .1],
                                     max_node_gap=.1, proxy_probabilities=[.1,.5,.9],
                                     min_wing_nodes=1, max_inner_gap=.1, max_outer_gap=.2)
    output = tmp_path/'features.parquet'
    first = builder.build_archive(meta, archive, output)
    first_manifest = json.loads(Path(str(output)+'.sources.json').read_text())
    meta.loc[0, 'chain_underlying_price'] = 110.
    second = builder.build_archive(meta, archive, output)
    second_manifest = json.loads(Path(str(output)+'.sources.json').read_text())
    assert first.chain_node_01_log_moneyness.iloc[0] == pytest.approx(0.)
    assert second.chain_node_01_log_moneyness.iloc[0] == pytest.approx(np.log(100/110))
    assert first_manifest['metadata_sha256'] != second_manifest['metadata_sha256']


def test_risk_neutral_config_pins_architectures_and_excludes_actual_dte():
    path = Path(__file__).parents[1]/'configs'/'run-predictive-cdf-risk-neutral.json'
    config = json.loads(path.read_text())
    study, experiment = config['study'], config['experiment']
    assert len(study['features']) == 199 and study['features'][52] == 'actual_calendar_dte'
    assert experiment['candidate_groups'] == experiment['search_partitions']
    assert sum(map(len, experiment['candidate_groups'].values())) == 15
    assert list(experiment['candidate_groups']) == [
        'rich_fixed', 'option_transport', 'catboost', 'spline_flow',
        'deepsets', 'set_transformer', 'quantile_forest']
    assert {p['name'] for p in experiment['public_probes']} == {
        'chronos-2', 'timesfm-3', 'moirai-2', 'tabpfn'}
    assert all(p['status'] == 'descriptive_only' and p['training_cutoff'] is None
               for p in experiment['public_probes'])
    for spec in experiment['candidates'].values():
        params = spec['params']
        if spec['class'].endswith(':PCAAugmentedCDF'):
            params = params['estimator_params']['mlp']
        selected = params.get('feature_indices', params.get('context_indices', []))
        assert 52 not in selected
    assert set(config['data']['fred_market_symbols']) == {
        'rate_dff', 'rate_dgs3mo', 'rate_dgs10', 'credit_hy_oas',
        'credit_cp_nonfinancial', 'credit_cp_financial', 'dollar_broad', 'oil_wti',
        'financial_nfci', 'financial_anfci'}
    CDFHyperparameterStudy(config)


@pytest.mark.parametrize('column,value', [
    ('chain_atm_iv', 0), ('chain_put25_iv', -0.1), ('chain_call25_iv', 0),
    ('chain_put_call_oi', 0), ('chain_rel_spread', -0.1),
    ('chain_contracts', -1), ('chain_open_interest', -1), ('chain_quote_depth', -1),
])
def test_option_surface_features_refuse_invalid_finite_values(column, value):
    row = pd.DataFrame({
        'chain_atm_iv': [.2], 'chain_put25_iv': [.3], 'chain_call25_iv': [.15],
        'chain_rel_spread': [.01], 'chain_put_call_oi': [2.],
        'chain_contracts': [99.], 'chain_open_interest': [999.],
        'chain_quote_depth': [4.],
    })
    row.loc[0, column] = value
    with pytest.raises(ValueError, match=column):
        ExactExpiryCDFPanel.add_surface_features(row)


def test_predictive_cdf_option_surface_config_pins_full_factorial_contract():
    root = Path(__file__).parents[1]/'configs'
    config = json.loads((root/'run-predictive-cdf-option-surface.json').read_text())
    prior = json.loads((root/'run-predictive-cdf-downside.json').read_text())
    study, experiment = config['study'], config['experiment']
    groups = experiment['candidate_groups']
    assert config['data']['surface_features'] is True
    assert study['features'][:42] == prior['study']['features']
    assert study['features'][42:52] == [
        'chain_log_atm_iv', 'chain_log_skew25', 'chain_log_curvature25',
        'chain_log_rel_spread', 'chain_log_put_call_oi', 'chain_log_contracts',
        'chain_log_open_interest', 'chain_log_quote_depth',
        'chain_has_25d_pair', 'chain_has_put_call_oi']
    assert study['features'][52] == 'actual_calendar_dte'
    assert study['output'] == 'pipeline_runs/predictive_cdf_option_surface_20260929/base'
    assert experiment['output'] == 'pipeline_runs/predictive_cdf_option_surface_20260929'
    assert experiment['max_candidates'] == 13
    assert groups == experiment['search_partitions']
    assert {name: len(values) for name, values in groups.items()} == {
        'surface': 2, 'tail': 2, 'adaptive': 1, 'surface_tail': 2,
        'surface_adaptive': 2, 'combined': 4}
    assert set().union(*map(set, groups.values())) == set(experiment['candidates'])
    assert sum(map(len, groups.values())) == 13

    base = list(range(42))
    surface = list(range(52))
    assert study['models']['pooled_normal_a']['params']['feature_indices'] == base
    assert study['models']['incumbent_blend_025']['params']['mlp']['feature_indices'] == base
    candidates = experiment['candidates']
    assert all(spec['pooled'] is True and spec['calibrate'] is False
               for spec in candidates.values())
    assert [candidates[name]['params']['mlp_weight'] for name in groups['surface']] == [.25, .35]
    assert all(candidates[name]['params']['mlp']['feature_indices'] == surface
               for name in groups['surface'])
    assert [candidates[name]['params']['mlp']['left_cdf_weight']
            for name in groups['tail']+groups['surface_tail']] == [.25, 1., .25, 1.]
    assert [candidates[name]['params']['upper_weight']
            for name in groups['adaptive']+groups['surface_adaptive']] == [.35, .35, .5]
    assert [candidates[name]['params']['upper_weight']
            for name in groups['combined']] == [.35, .5, .35, .5]
    assert all(candidates[name]['params']['grid_bounds'] == [-6, 6]
               and candidates[name]['params']['grid_points'] == 241
               for name in groups['adaptive']+groups['surface_adaptive']+groups['combined'])
    assert all(candidates[name]['class'].endswith(':AdaptiveEmpiricalMLPBlendCDF')
               for name in groups['adaptive']+groups['surface_adaptive']+groups['combined'])
    assert all(candidates[name]['params']['cell_indices'] == [52, 39, 40, 41]
               and 52 not in candidates[name]['params']['gate_indices']
               and 52 not in candidates[name]['params']['mlp']['feature_indices']
               for name in groups['adaptive']+groups['surface_adaptive']+groups['combined'])
    assert experiment['development_years'] == [2016, 2017, 2018]
    assert experiment['label_cutoff'] == '2019-01-01'
    CDFHyperparameterStudy(config)


def test_predictive_cdf_downside_config_pins_bounded_grouped_inventory():
    path = Path(__file__).parents[1]/'configs'/'run-predictive-cdf-downside.json'
    config = json.loads(path.read_text())
    study, experiment = config['study'], config['experiment']
    groups = experiment['candidate_groups']
    assert groups == experiment['search_partitions']
    assert list(groups) == ['floor', 'heads']
    assert [len(groups[name]) for name in groups] == [6, 3]
    assert experiment['max_candidates'] == 9
    assert experiment['output'] == 'pipeline_runs/predictive_cdf_downside_20260928'
    assert study['output'] == 'pipeline_runs/predictive_cdf_downside_20260928/base'
    assert study['comparison_references'] == ['horizon_empirical', 'incumbent_blend_025']
    assert list(study['models']) == [
        'horizon_empirical', 'pooled_normal_a', 'incumbent_blend_025']
    assert [study['models'][name]['calibrate'] for name in study['models']] == [False, True, False]
    assert len(study['features']) == 42
    assert [study['features'][i] for i in [39, 40, 41]] == ['is_SPY', 'is_QQQ', 'is_IWM']
    assert study['features'][38] == 'reference_scale'
    assert (study['target'], study['reference'], study['calibration_knots']) == (
        'terminal_return', 'reference_scale', 21)
    assert experiment['development_years'] == [2016, 2017, 2018]
    assert experiment['label_cutoff'] == '2019-01-01'
    assert experiment['evaluation_partitions'] == {
        'development': [2016, 2017, 2018], 'early': [2019, 2020, 2021],
        'middle': [2022, 2023], 'late': [2024, 2025]}
    assert sum(len(v) for v in experiment['expected_cells']['development'].values()) == 133
    assert sum(len(v) for v in experiment['expected_cells']['evaluation'].values()) == 135

    candidates = experiment['candidates']
    floor = [candidates[name] for name in groups['floor']]
    heads = [candidates[name] for name in groups['heads']]
    assert all(spec['class'].endswith(':EmpiricalMLPBlendCDF') for spec in candidates.values())
    assert [(spec['params']['mlp']['min_scale'], spec['params']['mlp_weight'])
            for spec in floor] == [
                (.5, .25), (.5, .35), (.75, .25), (.75, .35), (1., .25), (1., .35)]
    assert [spec['equivalence'] for spec in floor] == [
        'floor_min050', 'floor_min050', 'floor_min075', 'floor_min075',
        'floor_min100', 'floor_min100']
    assert [spec['params']['mlp_weight'] for spec in heads] == [.15, .25, .35]
    assert all(spec['params']['mlp']['min_scale'] == .1 for spec in heads)
    assert all(spec['params']['mlp']['head_features'] == [39, 40, 41] for spec in heads)
    assert all(spec['equivalence'] == 'heads_triple' for spec in heads)
    assert all(spec['pooled'] is True and spec['calibrate'] is True
               for spec in candidates.values())
    assert all('head_features' not in spec['params']['mlp'] for spec in floor)
    endpoint = study['models']['pooled_normal_a']['params']
    assert endpoint == {
        'components': 1, 'hidden': [16], 'epochs': 20, 'batch_size': 1024,
        'learning_rate': .003, 'weight_decay': .1, 'min_scale': .1,
        'activation': 'tanh', 'dropout': 0, 'seeds': [11, 29],
        'device': 'cuda', 'deterministic': True}
    assert study['models']['pooled_normal_a']['equivalence'] == 'base_pure_inc'
    incumbent = study['models']['incumbent_blend_025']
    assert incumbent['params']['mlp_weight'] == .25
    assert incumbent['params']['mlp'] == endpoint
    assert incumbent['equivalence'] == 'base_pure_inc'
    assert all(spec['params']['condition_indices'] == [27, 39, 40, 41]
               and spec['params']['reference_index'] == 38
               and spec['params']['knots'] == 401 for spec in candidates.values())
    assert experiment['resolutions'] == {
        'screen_samples': 101, 'final_samples': 401, 'tail_points': 201,
        'integration_points': 101, 'audit_samples': [101, 401, 1601], 'audit_rows': 12}
    CDFHyperparameterStudy(config)

    prior = Path(__file__).parents[1]/'configs'/'run-predictive-cdf-refinement.json'
    assert hashlib.sha256(prior.read_bytes()).hexdigest() == (
        '7375d6f996478cafd021d32af534d39c9c6bb5b0ab3e352fe33b778c3518a786')
    previous = json.loads(prior.read_text())
    assert config['data'] == previous['data']
    assert config['diagnostic'] == previous['diagnostic']
    unchanged_study = set(previous['study'])-{'output', 'models'}
    assert {key: study[key] for key in unchanged_study} == {
        key: previous['study'][key] for key in unchanged_study}
    unchanged_experiment = set(previous['experiment'])-{
        'notes', 'output', 'max_candidates', 'candidates',
        'search_partitions', 'candidate_groups'}
    assert {key: experiment[key] for key in unchanged_experiment} == {
        key: previous['experiment'][key] for key in unchanged_experiment}
    assert study['models']['horizon_empirical'] == previous['study']['models']['horizon_empirical']
    for name in ('pooled_normal_a', 'incumbent_blend_025'):
        assert {key: value for key, value in study['models'][name].items() if key != 'equivalence'} == {
            key: value for key, value in previous['study']['models'][name].items()
            if key != 'equivalence'}


def test_tail_data_config_pins_causal_family_ablation_and_dividend_policy():
    path = Path(__file__).parents[1]/'configs'/'run-predictive-cdf-tail-data.json'
    config = json.loads(path.read_text())
    study, experiment = config['study'], config['experiment']
    assert config['data']['ohlc_windows'] == [5, 22]
    assert config['data']['matched_dte_vrp'] is True
    assert 'macro_event_calendars' not in config['data']
    assert list(experiment['candidates']) == [
        'ohlc_only', 'vrp_only', 'flow_only', 'cboe_only', 'all_local']
    assert experiment['candidate_groups'] == experiment['search_partitions'] == {
        'tail_data': list(experiment['candidates'])}
    assert experiment['selection_guard']['reference'] == 'research_incumbent'
    assert study['features'][52] == 'actual_calendar_dte'
    assert study['features'][199:204] == [
        'overnight_return', 'intraday_return', 'log_high_low_range',
        'parkinson_variance', 'jump_variance_proxy']
    assert study['features'][212:216] == [
        'matched_implied_variance', 'matched_trailing_variance',
        'matched_vrp', 'matched_vrp_ratio']
    for spec in experiment['candidates'].values():
        selected = spec['params']['incumbent_params']['mlp']['feature_indices']
        assert 52 not in selected
    assert 'strategy_dividend_eligible' not in study['features']
    CDFHyperparameterStudy(config)


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
