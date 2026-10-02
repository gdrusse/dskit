"""The CDF adapter shares the contract payoff owner and exact units."""

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from index_options.cdf_study import (CondorCDFDiagnostic, DecisionRegionContextBuilder,
                                     ExactExpiryCDFPanel, RawChainFeatureBuilder)
from index_options import cdf_study
from index_options.distribution import condor_payoff
from dskit.pipeline.libs.predictive_cdf import CDFHyperparameterStudy, GridCurve, MixtureCurve


def test_entry_chain_enumerates_fixed_quotable_condors_and_refuses_undated_execution():
    strikes = [85., 90., 95., 105., 110., 115.]
    rows = pd.DataFrame({
        "symbol": ["SPY"] * 6, "date": ["2020-01-02"] * 6,
        "expiration": ["2020-02-21"] * 6, "strike": strikes,
        "type": ["put"] * 3 + ["call"] * 3,
        "bid": [1., 2., 3., 3., 2., 1.],
        "ask": [1.2, 2.2, 3.2, 3.2, 2.2, 1.2],
        "bid_size": [3] * 6, "ask_size": [3] * 6,
    })
    rule = cdf_study.EligibleCondorChain(
        max_abs_log_moneyness=.2, min_wing_width=5., max_wing_width=10.,
        max_candidates=100, fee_per_leg=0., multiplier=100)
    first = rule.candidates(rows, spot=100.)
    second = rule.candidates(rows.sample(frac=1, random_state=9), spot=100.)
    assert first == second
    assert (90., 95., 105., 110.) in [tuple(x["strikes"]) for x in first]
    assert all(x["net_credit"] > 0 for x in first)
    assert all(x["strikes"][0] < x["strikes"][1] < x["strikes"][2]
               < x["strikes"][3] for x in first)
    rows.loc[rows.strike.eq(95.), "bid_size"] = 0
    assert all(x["strikes"][1] != 95. for x in rule.candidates(rows, spot=100.))
    with pytest.raises(ValueError, match="timestamp"):
        rule.candidates(rows, spot=100., executable=True)


def test_decision_context_uses_all_actual_eligible_strikes_and_pins_sources(tmp_path):
    import resource
    import signal

    before_limit = resource.getrlimit(resource.RLIMIT_AS)
    before_timer = signal.getitimer(signal.ITIMER_REAL)
    archive = tmp_path/"spy"
    archive.mkdir()
    rows = pd.DataFrame({
        "symbol": ["SPY"]*6, "date": ["2020-01-02"]*6,
        "expiration": ["2020-02-21"]*6,
        "strike": [85., 90., 95., 105., 110., 115.],
        "type": ["put"]*3+["call"]*3,
        "bid": [1., 2., 3., 3., 2., 1.],
        "ask": [1.2, 2.2, 3.2, 3.2, 2.2, 1.2],
        "bid_size": [3]*6, "ask_size": [3]*6})
    rows.to_parquet(archive/"options_2020.parquet", index=False)
    config = {"archive_root": str(tmp_path), "max_chain_rows": 100,
              "clock": "after_date_close_indicative_not_executable",
              "limits": {"max_seconds": 1800, "max_resident_mib": 6144},
              "chain_rule": {"max_abs_log_moneyness": .2,
                             "min_wing_width": 5., "max_wing_width": 10.,
                             "max_candidates": 100, "fee_per_leg": 0.,
                             "multiplier": 100}}
    panel = pd.DataFrame({"symbol": ["SPY"], "quote_date": ["2020-01-02"],
                          "expiry": ["2020-02-21"], "spot": [100.],
                          "reference_scale": [.2]})
    builder = DecisionRegionContextBuilder(config)
    context = builder.build(panel)[0]
    assert resource.getrlimit(resource.RLIMIT_AS) == before_limit
    assert signal.getitimer(signal.ITIMER_REAL) == before_timer
    expected = sorted({strike for item in builder.rule.candidates(rows, 100.)
                       for strike in item["strikes"]})
    np.testing.assert_allclose(context["thresholds"],
                               np.log(np.array(expected)/100.)/.2)
    assert context["status"] == "eligible"
    assert sum(context["weights"]) == pytest.approx(1.)
    assert len(context["provenance_sha256"]) == 64
    assert builder.provenance["matching_chain_rows"] == 6

    bounded = dict(config, panel_years=[2019, 2020])
    DecisionRegionContextBuilder(bounded).build(panel)
    with pytest.raises(ValueError, match="declared years"):
        DecisionRegionContextBuilder(
            dict(config, panel_years=[2019])).build(panel)

    rows = pd.concat([rows, rows.iloc[[0]]], ignore_index=True)
    rows.to_parquet(archive/"options_2020.parquet", index=False)
    with pytest.raises(ValueError, match="duplicate|single-entry"):
        DecisionRegionContextBuilder(config).build(panel)


def test_builder_intervals_are_the_one_wing_geometry_the_wing_loss_reads(tmp_path):
    from dskit.pipeline.libs.predictive_cdf import DecisionRegionScores

    archive = tmp_path/"spy"
    archive.mkdir()
    pd.DataFrame({
        "symbol": ["SPY"]*6, "date": ["2020-01-02"]*6,
        "expiration": ["2020-02-21"]*6,
        "strike": [85., 90., 95., 105., 110., 115.],
        "type": ["put"]*3+["call"]*3,
        "bid": [1., 2., 3., 3., 2., 1.], "ask": [1.2, 2.2, 3.2, 3.2, 2.2, 1.2],
        "bid_size": [3]*6, "ask_size": [3]*6}).to_parquet(
            archive/"options_2020.parquet", index=False)
    config = {"archive_root": str(tmp_path), "max_chain_rows": 100,
              "clock": "after_date_close_indicative_not_executable",
              "limits": {"max_seconds": 1800, "max_resident_mib": 6144},
              "chain_rule": {"max_abs_log_moneyness": .2, "min_wing_width": 5.,
                             "max_wing_width": 10., "max_candidates": 100,
                             "fee_per_leg": 0., "multiplier": 100}}
    panel = pd.DataFrame({"symbol": ["SPY"]*2, "quote_date": ["2020-01-02", "2020-01-03"],
                          "expiry": ["2020-02-21"]*2, "spot": [100.]*2,
                          "reference_scale": [.2]*2})
    contexts = DecisionRegionContextBuilder(config).build(panel)
    # No wing-specific key or flag exists: the wing loss reads `intervals` only.
    assert set(contexts[0]) == {"identity", "thresholds", "weights", "intervals",
                                "status", "clock", "provenance_sha256"}
    scorer = DecisionRegionScores(contexts)
    lower, upper, density, counts = scorer.segment_arrays()
    assert counts.tolist() == [2, 0]
    z = lambda strike: np.log(strike/100.)/.2
    put_union, call_union = (z(85.), z(95.)), (z(105.), z(115.))
    sides = {}
    for lo, hi, mass in zip(lower[0], upper[0], density[0]):
        sides.setdefault("put" if hi <= 0 else "call", []).append((lo, hi, mass))
    assert {k: [(lo, hi) for lo, hi, _ in v] for k, v in sides.items()} == {
        "put": [pytest.approx(put_union)], "call": [pytest.approx(call_union)]}
    for side in sides.values():
        assert sum(mass*(hi-lo) for lo, hi, mass in side) == pytest.approx(.5)
    listed = set(contexts[0]["thresholds"])
    assert {*lower[0], *upper[0]} <= listed
    # The union equals the union of that side's intervals from the builder.
    intervals = np.asarray(contexts[0]["intervals"])
    assert intervals[:, 0].min() == lower[0].min() and intervals[:, 1].max() == upper[0].max()
    assert contexts[1]["thresholds"] == [] and contexts[1]["intervals"] == []
    scores = scorer.wing_score(MixtureCurve([[1.]]*2, [[0.]]*2, [[1.]]*2), [0., 0.])
    assert np.isfinite(scores["decision_wing_twcrps"][0])
    assert np.isnan(scores["decision_wing_twcrps"][1])


def test_decision_region_audit_uses_all_wings_and_checks_grid_error():
    grid = np.linspace(80., 120., 401)
    forecast = (grid-80.)/40.
    candidate = {"id": "one", "strikes": (90., 95., 105., 110.),
                 "net_credit": 4.}
    result = cdf_study.CondorDecisionAudit(mesh_tolerance=.1, floor=.1).evaluate(
        grid, forecast, terminal=100., spot=100., candidates=[candidate], radius=0.)
    assert result["nominal"]["id"] == "one"
    assert result["robust"]["id"] == "one"
    assert result["weighted_crps"] > 0
    assert result["strike_brier"] == pytest.approx(np.mean([.25**2, .375**2,
                                                             .375**2, .25**2]))
    assert result["candidates"][0]["expected_loss"] == pytest.approx(3.125)
    assert result["candidates"][0]["put_expected_loss"] == pytest.approx(1.5625)
    assert result["candidates"][0]["call_expected_loss"] == pytest.approx(1.5625)
    assert result["candidates"][0]["realized_loss"] == 0
    stressed = cdf_study.CondorDecisionAudit(mesh_tolerance=.1, floor=.1).evaluate(
        grid, forecast, terminal=100., spot=100., candidates=[candidate], radius=.1)
    assert stressed["robust"]["id"] is None
    with pytest.raises(ValueError, match="mesh"):
        cdf_study.CondorDecisionAudit(mesh_tolerance=.001, floor=.1).evaluate(
            grid[::40], forecast[::40], terminal=100., spot=100.,
            candidates=[candidate], radius=0.)


def test_causal_strike_correction_is_monotone_shrunk_and_requires_history():
    owner = cdf_study.CausalStrikeCDFCorrection(
        prior_strength=20., knots=11, min_events=4, min_dates=2)
    with pytest.raises(ValueError, match="insufficient"):
        owner.fit([.2, .8], [0., 1.], ["2020-01-01", "2020-01-02"])
    owner.fit([.1, .2, .8, .9], [0., 1., 0., 1.],
              ["2020-01-01"]*2+["2020-01-02"]*2,
              weights=[.5, .5, .5, .5])
    mapped = owner.transform(np.linspace(0, 1, 101))
    assert mapped[0] == 0 and mapped[-1] == 1
    assert (np.diff(mapped) >= 0).all()
    curve = owner.curve(GridCurve([[0., 1., 2.]], [[0., .5, 1.]]))
    assert curve.probabilities[0, 0] == 0
    assert curve.probabilities[0, -1] == 1


def test_adaptive_wasserstein_radius_uses_date_blocks_and_abstains():
    owner = cdf_study.AdaptiveWassersteinRadius(
        radii=[0., .01], min_dates=4, block_dates=2,
        replicates=100, alpha=.1, seed=7)
    short = pd.DataFrame({"quote_date": ["2020-01-01"], "radius": [0.],
                          "residual": [.1]})
    assert owner.select(short)["radius"] is None
    rows = []
    for day in range(1, 7):
        date = f"2020-01-{day:02d}"
        rows.extend([{"quote_date": date, "radius": 0., "residual": .2},
                     {"quote_date": date, "radius": .01, "residual": -.2}])
    result = owner.select(pd.DataFrame(rows))
    assert result["radius"] == .01
    assert result == owner.select(pd.DataFrame(rows))


def test_correction_and_radius_histories_respect_all_temporal_boundaries():
    events = [
        {"quote_date": "2020-01-01", "settlement_date": "2020-01-02",
         "tenor_band": "short"},
        {"quote_date": "2020-01-03", "settlement_date": "2020-01-06",
         "tenor_band": "short"},
        {"quote_date": "2020-01-02", "settlement_date": "2020-01-02",
         "tenor_band": "long"},
    ]
    settled = cdf_study.RobustCorrectionStudy._settled_events(
        events, "2020-01-03", "short")
    assert settled == events[:1]
    # A same-date forecast is never admitted, even if its synthetic settlement
    # field is malformed as already available.
    same_date = [{"quote_date": "2020-01-03", "settlement_date": "2020-01-02",
                  "tenor_band": "short"}]
    assert not cdf_study.RobustCorrectionStudy._settled_events(
        same_date, "2020-01-03", "short")

    policies = [
        {"quote_date": "2020-01-01", "settlement_date": "2020-01-02",
         "symbol": "SPY", "tenor_band": "short", "radius": 0., "residual": 0.},
        {"quote_date": "2020-01-05", "settlement_date": "2020-01-05",
         "symbol": "SPY", "tenor_band": "short", "radius": 0., "residual": 0.},
        {"quote_date": "2020-01-06", "settlement_date": "2020-01-06",
         "symbol": "SPY", "tenor_band": "short", "radius": 0., "residual": 0.},
    ]
    history = cdf_study.RobustCorrectionStudy._radius_history(
        policies, "2020-01-06", "SPY", "short", "2020-01-04")
    assert history.quote_date.tolist() == ["2020-01-05"]


def test_nominal_decision_uses_direct_cdf_when_grid_ranking_differs():
    grid = np.arange(9., 18.)
    cdf = [0., 0., .1, .7, .75, .9, .95, 1., 1.]
    candidates = [
        {"id": "A", "strikes": (10., 11., 13., 14.), "net_credit": .5},
        {"id": "B", "strikes": (11., 12., 14., 15.), "net_credit": .5},
    ]
    result = cdf_study.CondorDecisionAudit(mesh_tolerance=.5, floor=.1).evaluate(
        grid, cdf, terminal=12.5, spot=12.5, candidates=candidates, radius=0.)
    assert result["nominal"]["id"] == "A"
    assert result["robust"]["id"] == "A"


def test_atom_aware_nominal_decision_uses_frozen_gridcurve():
    grid = np.arange(9., 17.5, .5)
    curve = GridCurve(np.log(np.array([[9., 10., 11., 11., 16., 17.]])/13.),
                      [[0., 0., 0., .5, 1., 1.]])
    cdf = curve.cdf(np.log(grid/13.))[0]
    candidate = {"id": "atom", "strikes": (10., 12., 14., 15.),
                 "net_credit": .75}
    result = cdf_study.CondorDecisionAudit(mesh_tolerance=.5, floor=.1).evaluate(
        grid, cdf, terminal=13., spot=13., candidates=[candidate], radius=0.,
        curve=curve, reference_scale=1.)
    assert result["nominal"]["id"] == "atom"


def test_american_charge_is_separate_and_missing_charge_withholds_regret():
    grid = np.linspace(80., 120., 401)
    candidate = {"id": "one", "strikes": (90., 95., 105., 110.),
                 "net_credit": 4.}
    audit = cdf_study.CondorDecisionAudit(mesh_tolerance=.1, floor=.1)
    uncharged = audit.evaluate(grid, (grid-80.)/40., terminal=100., spot=100.,
                               candidates=[candidate], radius=0.)
    assert uncharged["nominal"]["regret"] is None
    charged = audit.evaluate(grid, (grid-80.)/40., terminal=100., spot=100.,
                             candidates=[candidate], radius=0.,
                             american_charges={"one": 1.25})
    assert charged["candidates"][0]["american_charge"] == pytest.approx(1.25)
    assert charged["nominal"]["realized_pnl"] == pytest.approx(2.75)


def test_decision_region_source_refuses_unbound_marker_and_wrong_settlement(tmp_path):
    root = tmp_path/"frozen"
    partition = root/"evaluate"/"late"
    partition.mkdir(parents=True)
    (partition/"complete.json").write_text("{}")
    settings = {
        "forecast_root": str(root), "partition": "late",
        "models": {"base": "raw"}, "symbols": ["SPY"],
        "archive_root": str(tmp_path), "output": str(tmp_path/"out"),
        "max_rows_per_symbol": 1,
        "underlying": {"root": str(tmp_path), "source": "fixture",
                       "since_ms": 0, "carry_rate": .055},
        "strata": {"tenor_days": [7, 21], "iv": [20, 30],
                   "wing_log_moneyness": [.02, .05]},
        "chain_rule": {"max_abs_log_moneyness": .1,
                       "min_wing_width": 5., "max_wing_width": 5.,
                       "max_candidates": 10, "fee_per_leg": 0., "multiplier": 100},
        "audit": {"price_step": .5, "support_margin_fraction": .02,
                  "mesh_tolerance": .5, "floor": .1, "radius": 0.,
                  "score_refinement_factor": 2, "score_tolerance": .01},
        "limits": {"max_seconds": 1800, "max_address_space_mib": 6144,
                   "max_chain_rows": 100000, "max_grid_nodes": 4001},
    }
    with pytest.raises(ValueError, match="decision-region JSON"):
        cdf_study.DecisionRegionStudy({**settings, "symbols": ["SPY", "SPY"]})
    with pytest.raises(ValueError, match="decision-region limits"):
        cdf_study.DecisionRegionStudy({**settings, "limits": {
            **settings["limits"], "max_seconds": 1801}})
    with pytest.raises(ValueError, match="raw GridCurve"):
        cdf_study.DecisionRegionStudy({**settings, "models": {"base": "calibrated"}})
    with pytest.raises(ValueError, match="completion"):
        cdf_study.DecisionRegionStudy(settings).prepare()
    frame = pd.DataFrame({
        "quote_date": ["2025-01-02"], "expiry": ["2025-01-10"],
        "settlement_date": ["2025-01-09"], "actual_calendar_dte": [7],
        "spot": [100.], "terminal_price": [101.], "reference_scale": [.02],
    })
    with pytest.raises(ValueError, match="settlement"):
        cdf_study.DecisionRegionStudy.validate_panel_temporal(frame)


def test_decision_region_rejects_unsupported_archived_curve_family(tmp_path):
    source = tmp_path/"convex.npz"
    np.savez_compressed(source, kind="convex")
    with pytest.raises(ValueError, match="raw GridCurve"):
        cdf_study.DecisionRegionStudy._check_curve_archive(source)
    np.savez_compressed(source, kind="grid", calibration_x=np.array([[0., 1.]]))
    with pytest.raises(ValueError, match="raw GridCurve"):
        cdf_study.DecisionRegionStudy._check_curve_archive(source)


def test_decision_region_refinement_requires_small_error_and_stable_rank():
    rows = pd.DataFrame({
        "model": ["base", "base", "candidate", "candidate"],
        "weighted_crps": [.20, .20, .19, .19],
        "weighted_crps_refined": [.20001, .20001, .19001, .19001],
    })
    cdf_study.DecisionRegionStudy.check_weighted_score_refinement(rows, 1e-3)
    with pytest.raises(ValueError, match="quadrature"):
        cdf_study.DecisionRegionStudy.check_weighted_score_refinement(rows, 1e-6)
    unstable = rows.copy()
    unstable.loc[unstable.model.eq("candidate"), "weighted_crps_refined"] = .21
    with pytest.raises(ValueError, match="ranking"):
        cdf_study.DecisionRegionStudy.check_weighted_score_refinement(unstable, .1)


def test_decision_region_block_intervals_are_paired_by_date_and_symbol():
    rows = []
    for day in range(1, 7):
        for symbol in ("SPY", "QQQ"):
            identity = {"symbol": symbol, "quote_date": f"2020-01-{day:02d}",
                        "expiry": "2020-02-21", "reason": None}
            rows.append({**identity, "model": "base", "weighted_crps_refined": 1.,
                         "strike_brier": .5, "loss_mse": 2.})
            rows.append({**identity, "model": "candidate",
                         "weighted_crps_refined": .8,
                         "strike_brier": .4, "loss_mse": 1.6})
    result = cdf_study.DecisionRegionStudy.paired_block_intervals(
        pd.DataFrame(rows), {"reference_model": "base",
                             "metrics": ["weighted_crps_refined", "strike_brier"],
                             "blocks": [2], "replicates": 100,
                             "alpha": .05, "seed": 7})
    assert len(result) == 2
    assert all(item["point_skill_pct"] == pytest.approx(20.) for item in result)
    assert all(item["lo_skill_pct"] == pytest.approx(20.) for item in result)
    assert all(item["hi_skill_pct"] == pytest.approx(20.) for item in result)


def test_decision_region_bootstrap_contract_refuses_unknown_metric(tmp_path):
    settings = {
        "forecast_root": str(tmp_path), "partition": "development",
        "models": {"base": "raw"}, "symbols": ["SPY"],
        "archive_root": str(tmp_path), "output": str(tmp_path/"out"),
        "max_rows_per_symbol": 1,
        "underlying": {"root": str(tmp_path), "source": "fixture",
                       "since_ms": 0, "carry_rate": .055},
        "strata": {"tenor_days": [7, 21], "iv": [20, 30],
                   "wing_log_moneyness": [.02, .05]},
        "chain_rule": {"max_abs_log_moneyness": .1,
                       "min_wing_width": 5., "max_wing_width": 5.,
                       "max_candidates": 10, "fee_per_leg": 0., "multiplier": 100},
        "audit": {"price_step": .5, "support_margin_fraction": .02,
                  "mesh_tolerance": .5, "floor": .1, "radius": 0.,
                  "score_refinement_factor": 2, "score_tolerance": .01},
        "limits": {"max_seconds": 1800, "max_address_space_mib": 6144,
                   "max_chain_rows": 100000, "max_grid_nodes": 4001},
        "bootstrap": {"reference_model": "base", "metrics": ["regret"],
                      "blocks": [2], "replicates": 10, "alpha": .05, "seed": 1},
    }
    with pytest.raises(ValueError, match="bootstrap"):
        cdf_study.DecisionRegionStudy(settings)


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
    # Frozen from the pre-extraction index implementation at eb08ed8.
    np.testing.assert_allclose(q, [-.09166103, -.06480061, -.00031260,
                                  .06041365, .08381542], atol=5e-9, rtol=0)
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


def test_panel_never_reads_rows_dated_or_settling_at_the_holdout(tmp_path, monkeypatch):
    import exchange_calendars as xc
    from index_options.cdf_study import ExactExpiryCDFPanel

    dates = xc.get_calendar('XNYS').sessions_in_range('2023-01-03', '2023-07-10')
    prices = {d.strftime('%Y-%m-%d'): 100+i*.1 for i, d in enumerate(dates)}

    class Reader:
        def __init__(self, key, params):
            self.symbol = params['symbol']

        def run(self, ctx, inputs):
            return {'records': [
                {'date': d, 'close': 20. if self.symbol == 'RVX' else price,
                 'asof_ms': int(pd.Timestamp(d).timestamp()*1000), 'instrument': self.symbol,
                 'contract': self.symbol, 'group': self.symbol, 'dividend_amount': None}
                for d, price in prices.items()]}

        def fingerprint(self):
            return {'sha256': 'fixture'}

    monkeypatch.setattr('index_options.cdf_study.IndexCloseRows', Reader)
    rows = pd.DataFrame({'symbol': ['IWM']*4,
                         'quote_date': ['2023-06-28', '2023-06-29', '2023-06-30', '2023-07-03'],
                         'expiry': ['2023-06-29', '2023-06-30', '2023-07-03', '2023-07-05'],
                         'chain_underlying_price': [prices[d] for d in
                                                    ('2023-06-28', '2023-06-29', '2023-06-30',
                                                     '2023-07-03')]})
    surface, lifecycle = tmp_path/'surface.parquet', tmp_path/'lifecycle.parquet'
    rows.to_parquet(surface)
    rows[['symbol', 'quote_date', 'expiry']].assign(first_seen_date='2023-06-01').to_parquet(lifecycle)
    config = {'root': 'fixture', 'surface': str(surface), 'lifecycle': str(lifecycle),
              'symbols': {'IWM': 'RVX'}, 'price_source': 'fixture', 'iv_source': 'fixture',
              'since': '2023-01-01', 'max_dte': 45, 'lags': 22, 'windows': [1, 5, 22, 66],
              'feature_gap_days': 7, 'reference_floor': .001, 'spot_tolerance': .02}
    everything = ExactExpiryCDFPanel(config)
    assert len(everything.read()) == 4 and 'holdout_locked' not in everything.refused['IWM']
    # 2023-06-30 is the holdout: the row entering that day, the one entering after,
    # and the row whose label settles on it (quote 06-29) are all cut.
    adapter = ExactExpiryCDFPanel(config, holdout_start='2023-06-30')
    out = adapter.read()
    assert out.quote_date.tolist() == ['2023-06-28']
    assert adapter.refused['IWM']['holdout_locked'] == 3
    with pytest.raises(ValueError, match='holdout_start'):
        ExactExpiryCDFPanel(config, holdout_start='2023-6-30')


def test_panel_exact_dte_keeps_only_the_actual_calendar_horizon(tmp_path, monkeypatch):
    import exchange_calendars as xc
    from index_options.cdf_study import ExactExpiryCDFPanel

    dates = xc.get_calendar('XNYS').sessions_in_range('2023-01-03', '2023-07-10')
    prices = {d.strftime('%Y-%m-%d'): 100+i*.1 for i, d in enumerate(dates)}

    class Reader:
        def __init__(self, key, params):
            self.symbol = params['symbol']

        def run(self, ctx, inputs):
            return {'records': [
                {'date': d, 'close': 20. if self.symbol == 'RVX' else price,
                 'asof_ms': int(pd.Timestamp(d).timestamp()*1000), 'instrument': self.symbol,
                 'contract': self.symbol, 'group': self.symbol, 'dividend_amount': None}
                for d, price in prices.items()]}

        def fingerprint(self):
            return {'sha256': 'fixture'}

    monkeypatch.setattr('index_options.cdf_study.IndexCloseRows', Reader)
    # Actual DTE 1, 2, 5, 4 and 7. The 07-04 expiry is a holiday: nominal DTE 5, actual 4.
    quotes = ['2023-06-28', '2023-06-28', '2023-06-28', '2023-06-29', '2023-06-28']
    expiry = ['2023-06-29', '2023-06-30', '2023-07-03', '2023-07-04', '2023-07-05']
    rows = pd.DataFrame({'symbol': ['IWM']*5, 'quote_date': quotes, 'expiry': expiry,
                         'chain_underlying_price': [prices[d] for d in quotes]})
    surface, lifecycle = tmp_path/'surface.parquet', tmp_path/'lifecycle.parquet'
    rows.to_parquet(surface)
    rows[['symbol', 'quote_date', 'expiry']].assign(first_seen_date='2023-06-01').to_parquet(lifecycle)
    config = {'root': 'fixture', 'surface': str(surface), 'lifecycle': str(lifecycle),
              'symbols': {'IWM': 'RVX'}, 'price_source': 'fixture', 'iv_source': 'fixture',
              'since': '2023-01-01', 'max_dte': 45, 'lags': 22, 'windows': [1, 5, 22, 66],
              'feature_gap_days': 7, 'reference_floor': .001, 'spot_tolerance': .02}
    everything = ExactExpiryCDFPanel(config).read()
    assert sorted(everything.actual_calendar_dte) == [1, 2, 4, 5, 7]
    for horizon in (1, 2, 4, 5, 7):
        out = ExactExpiryCDFPanel({**config, 'exact_dte': horizon}).read()
        assert out.actual_calendar_dte.tolist() == [horizon]
        pd.testing.assert_frame_equal(
            out, everything[everything.actual_calendar_dte == horizon].reset_index(drop=True))
    # A horizon the panel never lists is an empty cohort, not a fallback to the rest.
    assert ExactExpiryCDFPanel({**config, 'exact_dte': 3}).read().empty
    for bad in (0, -1, True, '5', 5.5, None):
        with pytest.raises(ValueError, match='exact_dte'):
            ExactExpiryCDFPanel({**config, 'exact_dte': bad})


@pytest.mark.parametrize('name', ['run-step5-model-zoo', 'run-step6-hpo'])
def test_step_configs_declare_the_one_exact_dte_the_study_expects(name):
    config = json.loads((Path(__file__).parents[1]/'configs'/f'{name}.json').read_text())
    horizon = config['data']['exact_dte']
    assert type(horizon) is int and horizon >= 1 and horizon <= config['data']['max_dte']
    for partition, cells in config['experiment']['expected_cells'].items():
        assert cells == {'QQQ': [horizon]}, partition


def test_cli_hands_the_study_holdout_to_the_reader_and_runs_the_fold_table(
        tmp_path, monkeypatch):
    import hashlib
    import sys
    from dskit.pipeline.kinds_split import RollingOriginPlan

    days = pd.date_range('2020-01-01', periods=60)
    frame = pd.DataFrame({
        'symbol': 'QQQ', 'quote_date': days.strftime('%Y-%m-%d'),
        'expiry': (days+pd.Timedelta(days=1)).strftime('%Y-%m-%d'),
        'actual_calendar_dte': 1, 'reference_scale': 1., 'spot': 100.,
        'terminal_return': [((i*7) % 11-5)/50 for i in range(60)]})
    frame['settlement_date'] = frame.expiry
    frame['terminal_price'] = 100.*np.exp(frame.terminal_return)
    holdout = '2020-02-24'
    dev = frame[frame.settlement_date < holdout]
    plan = RollingOriginPlan('plan', {
        'date_field': 'quote_date', 'end_field': 'settlement_date', 'holdout_start': holdout,
        'embargo_days': 1, 'val_n': 5, 'step_n': 5, 'train_n': 20, 'warmup_folds': 1}).run(
        None, {'records': dev.to_dict('records')})
    table = tmp_path/'folds.jsonl'
    table.write_text(''.join(json.dumps(r, sort_keys=True, separators=(',', ':'))+'\n'
                             for r in plan['records']))
    seen = {}

    class Adapter:
        def __init__(self, config, holdout_start=None):
            seen['holdout_start'] = holdout_start
            self.refused, self.source_hashes, self.reader_fingerprints = {}, {}, {}

        def read(self):
            return dev.copy()

    monkeypatch.setattr(cdf_study, 'ExactExpiryCDFPanel', Adapter)
    output = tmp_path/'run'
    config = {
        'data': {}, 'diagnostic': {'strikes_z': [-2, -1, 1, 2], 'integration_points': 11},
        'study': {
            'features': ['actual_calendar_dte', 'reference_scale'], 'group': 'symbol',
            'date': 'quote_date', 'end': 'settlement_date', 'horizon': 'actual_calendar_dte',
            'target': 'terminal_return', 'reference': 'reference_scale',
            'identity': ['symbol', 'quote_date', 'expiry'], 'series_identity': ['symbol', 'expiry'],
            'fold_table': {'path': str(table), 'sha256': hashlib.sha256(table.read_bytes()).hexdigest(),
                           'holdout_start': holdout, 'cal_n': 5, 'roles': ['warmup', 'scored']},
            'output': str(output), 'samples': 21, 'tail_intervals': [[-2, -1], [1, 2]],
            'tail_points': 21, 'calibration_knots': 5,
            'bootstrap': {'blocks': [3], 'replicates': 5, 'seed': 4},
            'reference_model': 'reference', 'comparison_references': ['reference'],
            'models': {'reference': {
                'class': 'dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF',
                'params': {'horizon_index': 0, 'reference_index': 1, 'knots': 21},
                'calibrate': True}}}}
    path = tmp_path/'config.json'
    path.write_text(json.dumps(config))
    monkeypatch.setattr(sys, 'argv', ['cdf_study', str(path)])
    cdf_study._main()
    assert seen == {'holdout_start': holdout}
    scores = pd.read_parquet(output/'scores.parquet')
    assert set(scores.fold) == {f['fold'] for f in plan['records']} and (scores.quote_date < holdout).all()
    assert (output/'skill_by_index_fold.csv').exists() and (output/'comparison.json').exists()


def test_decision_strike_diagnosis_is_paired_and_stratified(tmp_path):
    root, output = tmp_path/"source", tmp_path/"out"
    root.mkdir()
    identities = np.array([["QQQ", "2019-01-02", "2019-01-11"],
                           ["QQQ", "2019-01-03", "2019-02-01"]])
    contexts = [{"identity": list(key), "thresholds": [-.5, .5],
                 "weights": [.5, .5], "intervals": [[-.5, -.1], [.1, .5]],
                 "status": "eligible",
                 "clock": "after_date_close_indicative_not_executable",
                 "provenance_sha256": "a"*64} for key in identities]
    pd.DataFrame({"symbol": "QQQ", "quote_date": identities[:, 1],
                  "expiry": identities[:, 2], "actual_calendar_dte": [9, 29],
                  "terminal_return": [-.01, .01], "reference_scale": [.02, .02],
                  "decision_region_context": contexts}).to_parquet(
                      root/"input_panel.parquet", index=False)
    (root/"protocol.json").write_text("{}")
    values = np.tile([-2., 0., 2.], (2, 1))
    for model, probabilities in {
            "reference": [[0., .5, 1.], [0., .5, 1.]],
            "candidate": [[0., .3, 1.], [0., .7, 1.]]}.items():
        np.savez_compressed(root/f"QQQ-{model}-raw-curves.npz",
                            kind="grid", values=values,
                            probabilities=np.asarray(probabilities),
                            identities=identities, row_index=np.arange(2))
    settings = {"forecast_root": str(root),
                "models": ["reference", "candidate"],
                "reference_model": "reference", "symbol": "QQQ", "years": [2019],
                "output": str(output), "distance_bins": [0, 1, 4],
                "dte_bins": [0, 15, 46],
                "groupings": [[], ["side"], ["dte_band"]],
                "bootstrap": {"blocks": [1], "replicates": 20,
                              "alpha": .05, "seed": 7},
                "limits": {"max_seconds": 1800,
                           "max_address_space_mib": 6144}, "notes": "fixture"}
    result = cdf_study.DecisionStrikeDiagnosisStudy(settings).run()
    detail = pd.read_parquet(output/"threshold_scores.parquet")
    summary = pd.read_csv(output/"stratified_skill.csv")
    assert result["forecast_identities"] == 2
    assert result["threshold_rows"] == 8
    assert set(detail.side) == {"put", "call"}
    assert set(summary.view) == {"overall", "side", "dte_band"}
    assert detail.groupby(["symbol", "quote_date", "expiry", "threshold"]).model.nunique().eq(2).all()


def test_decision_strike_diagnosis_rejects_invalid_bins():
    settings = {"forecast_root": "source", "models": ["a", "b"],
                "reference_model": "a", "symbol": "QQQ", "years": [2019],
                "output": "out", "distance_bins": [0], "dte_bins": [0, 46],
                "groupings": [[]], "bootstrap": {"blocks": [1],
                "replicates": 1, "alpha": .05, "seed": 1},
                "limits": {"max_seconds": 1800,
                "max_address_space_mib": 6144}, "notes": "fixture"}
    with pytest.raises(ValueError, match="invalid decision-strike diagnosis"):
        cdf_study.DecisionStrikeDiagnosisStudy(settings)
