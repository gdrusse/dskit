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
from index_options.datafiles import DataFiles, DataSourceError
from index_options.distribution import condor_payoff
from dskit.onboarding import payload_files
from dskit.pipeline.libs.predictive_cdf import CDFHyperparameterStudy, GridCurve, MixtureCurve




def test_held_out_rho_default_deny_and_duplicate_refusal_before_data_access():
    assert cdf_study.HeldOutRhoCalibration.validate_params({"unexpected": 1})
    owner = cdf_study.HeldOutRhoCalibration("rho", {
        "rho_grid": [0.], "min_dates": 2, "block_days": {"primary": 1, "sensitivity": 2},
        "replicates": 20, "alpha": .1, "seed": 7, "short_q": .1, "wing_strikes": 1,
        "min_trade_count": 1, "min_volume": 1, "publication_lag_sessions": 0,
        "max_settlement_gap_days": 4, "end_before": "2026-01-01",
        "settlement_field": "as_traded_close"})
    duplicate = {"symbol": "X", "quote_date": "2024-01-02", "expiry": "2024-01-03"}
    with pytest.raises(ValueError, match="duplicate mass context"):
        owner.run(None, {"masses": [duplicate, duplicate], "chain": [], "bars": []})


def test_held_out_rho_refuses_nan_or_protected_bar_dates():
    mass = {"symbol": "X", "expiry": "2024-01-03", "settlement_date": "2024-01-03"}
    with pytest.raises(ValueError, match="invalid settlement close"):
        cdf_study.HeldOutRhoCalibration._bar_close(
            mass, [{"symbol": "X", "date": "2024-01-03", "as_traded_close": float("nan")},
                   {"symbol": "X", "date": "2024-01-04", "as_traded_close": 1.}], 4, 0, "2026-01-01", "as_traded_close")
    with pytest.raises(ValueError, match="protected"):
        cdf_study.HeldOutRhoCalibration._bar_close(
            mass, [{"symbol": "X", "date": "2026-01-03", "as_traded_close": 1.},
                   {"symbol": "X", "date": "2026-01-04", "as_traded_close": 1.}], 4, 0, "2026-01-01", "as_traded_close")
def test_held_out_rho_template_is_fixed_and_monthly_history_has_no_future_leakage():
    owner = cdf_study.HeldOutRhoCalibration("rho", {
        "rho_grid": [0., .01], "min_dates": 2, "block_days": {"primary": 1, "sensitivity": 2},
        "replicates": 20, "alpha": .1, "seed": 7, "short_q": .1, "wing_strikes": 1,
        "min_trade_count": 1, "min_volume": 1, "publication_lag_sessions": 1,
        "max_settlement_gap_days": 4, "end_before": "2026-01-01",
        "settlement_field": "as_traded_close",
    })
    masses = [{"symbol": "X", "quote_date": "2024-01-02", "expiry": "2024-01-03", "settlement_date": "2024-01-03", "phase": "calibration", "spot": 100., "reference": .1, "curve": {"kind": "mixture", "weights": [[1.]], "means": [[0.]], "scales": [[1.]]}, "grid": [80., 90., 100., 110., 120.], "masses": [.1, .2, .4, .2, .1], "fit_identity": "fit"}, {"symbol": "X", "quote_date": "2024-02-02", "expiry": "2024-02-03", "settlement_date": "2024-02-03", "phase": "entry", "spot": 100., "reference": .1, "curve": {"kind": "mixture", "weights": [[1.]], "means": [[0.]], "scales": [[1.]]}, "grid": [80., 90., 100., 110., 120.], "masses": [.1, .2, .4, .2, .1], "fit_identity": "fit"}]
    chain = [{"symbol": "X", "quote_date": date, "expiry": expiry, "right": right, "strike": strike, "trade_count": 2, "volume": 2} for date, expiry in [("2024-01-02", "2024-01-03"), ("2024-02-02", "2024-02-03")] for right, strike in [("put", 80.), ("put", 90.), ("put", 100.), ("call", 100.), ("call", 110.), ("call", 120.)]]
    bars = [{"symbol": "X", "date": "2024-01-03", "as_traded_close": 100.}, {"symbol": "X", "date": "2024-01-04", "as_traded_close": 100.}, {"symbol": "X", "date": "2024-01-05", "as_traded_close": 100.}, {"symbol": "X", "date": "2024-02-03", "as_traded_close": 100.}, {"symbol": "X", "date": "2024-02-04", "as_traded_close": 100.}, {"symbol": "X", "date": "2024-02-05", "as_traded_close": 100.}]
    result = owner.run(None, {"masses": masses, "chain": chain, "bars": bars})
    assert not result["skips"], [item["reason"] for item in result["skips"]]
    assert result["rho"]["2024-02"] is None
    assert result["audit"].value["records"][0]["strikes"] == [80., 90., 110., 120.]
    assert result["audit"].value["records"][1]["history_count"] == 1
    audit = result["audit"].value
    assert audit["loss_cache_count"] == 2
    first, second = audit["records"]
    assert (first["settlement_date"], first["publication_date"]) == ("2024-01-03", "2024-01-04")
    assert audit["monthly"][0]["reference"] == "2024-02-02"
    assert first["publication_date"] < audit["monthly"][0]["reference"]
    assert first["fit_identity"] == second["fit_identity"] == "fit"


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


def _span_chain(rows):
    """One dated SPY chain; each row is (strike, type[, bid_size[, ask_size]])."""
    bids = [(r[0]-60.)/10. if r[1] == "put" else (120.-r[0])/10. for r in rows]
    return pd.DataFrame({
        "symbol": ["SPY"] * len(rows), "date": ["2020-01-02"] * len(rows),
        "expiration": ["2020-02-21"] * len(rows),
        "strike": [float(r[0]) for r in rows], "type": [r[1] for r in rows],
        "bid": bids, "ask": [b+.1 for b in bids],
        "bid_size": [r[2] if len(r) > 2 else 3 for r in rows],
        "ask_size": [r[3] if len(r) > 3 else 3 for r in rows]})


_SPAN_ROWS = ([(k, "put") for k in (70, 75, 80, 90, 95)]
              + [(k, "call") for k in (105, 110)])


def test_span_region_keeps_the_gap_that_width_capped_condors_leave_out():
    chain = _span_chain(_SPAN_ROWS)
    span = cdf_study.EligibleCondorChain(max_abs_log_moneyness=.5, region="span")
    found = span.span(chain, spot=100.)
    assert found["bounds"] == {"put": (70., 95.), "call": (105., 110.)}
    assert found["strikes"] == [70., 75., 80., 90., 95., 105., 110.]
    capped = cdf_study.EligibleCondorChain(
        max_abs_log_moneyness=.5, min_wing_width=5., max_wing_width=5.,
        max_candidates=100, fee_per_leg=0., multiplier=100)
    wings = {(c["strikes"][0], c["strikes"][1]) for c in capped.candidates(chain, 100.)}
    assert wings == {(70., 75.), (75., 80.), (90., 95.)}   # nothing reaches 80..90


def test_span_endpoints_need_the_side_a_wing_actually_trades():
    span = cdf_study.EligibleCondorChain(max_abs_log_moneyness=.5, region="span")
    # puts are bought low and sold high: no valid ask at 70 or bid at 95 shrinks the span
    no_ask = _span_chain([(k, "put", 3, 0 if k == 70 else 3) for k in (70, 75, 80, 90, 95)]
                         + [(105, "call"), (110, "call")])
    found = span.span(no_ask, spot=100.)
    assert found["bounds"]["put"] == (75., 95.) and 70. not in found["strikes"]
    no_bid = _span_chain([(k, "put", 0 if k == 95 else 3, 3) for k in (70, 75, 80, 90, 95)]
                         + [(105, "call"), (110, "call")])
    found = span.span(no_bid, spot=100.)
    assert found["bounds"]["put"] == (70., 90.) and 95. not in found["strikes"]
    # calls are sold low and bought high
    no_bid_call = _span_chain([(k, "put") for k in (90, 95)]
                              + [(105, "call", 0, 3), (110, "call"), (115, "call")])
    assert span.span(no_bid_call, spot=100.)["bounds"]["call"] == (110., 115.)
    # a side with a single quotable strike has no span
    one = _span_chain([(95, "put"), (105, "call"), (110, "call")])
    assert "put" not in span.span(one, spot=100.)["bounds"]
    # strikes outside the moneyness band are ignored
    narrow = cdf_study.EligibleCondorChain(max_abs_log_moneyness=.12, region="span")
    assert narrow.span(_span_chain(_SPAN_ROWS), spot=100.)["bounds"]["put"] == (90., 95.)


def test_span_rule_takes_no_condor_settings_and_the_two_rules_do_not_mix():
    with pytest.raises(ValueError, match="invalid eligible-condor rule"):
        cdf_study.EligibleCondorChain(max_abs_log_moneyness=.5, region="span",
                                      max_wing_width=5.)
    with pytest.raises(ValueError, match="invalid eligible-condor rule"):
        cdf_study.EligibleCondorChain(max_abs_log_moneyness=.5, region="wings")
    with pytest.raises(ValueError, match="invalid eligible-condor rule"):
        cdf_study.EligibleCondorChain(max_abs_log_moneyness=.5)
    span = cdf_study.EligibleCondorChain(max_abs_log_moneyness=.5, region="span")
    with pytest.raises(ValueError, match="no condor candidates"):
        span.candidates(_span_chain(_SPAN_ROWS), spot=100.)
    capped = cdf_study.EligibleCondorChain(
        max_abs_log_moneyness=.5, min_wing_width=5., max_wing_width=5.,
        max_candidates=100, fee_per_leg=0., multiplier=100)
    with pytest.raises(ValueError, match="no span region"):
        capped.span(_span_chain(_SPAN_ROWS), spot=100.)


def test_decision_context_span_region_is_one_interval_per_side(tmp_path):
    archive = tmp_path/"spy"
    archive.mkdir()
    _span_chain(_SPAN_ROWS).to_parquet(archive/"options_2020.parquet", index=False)
    config = {"archive_root": str(tmp_path), "max_chain_rows": 100,
              "clock": "after_date_close_indicative_not_executable",
              "limits": {"max_seconds": 1800, "max_resident_mib": 6144},
              "chain_rule": {"max_abs_log_moneyness": .5, "region": "span"}}
    panel = pd.DataFrame({"symbol": ["SPY"], "quote_date": ["2020-01-02"],
                          "expiry": ["2020-02-21"], "spot": [100.],
                          "reference_scale": [.2]})
    context = DecisionRegionContextBuilder(config).build(panel)[0]
    z = lambda k: float(np.log(k/100.)/.2)
    assert context["status"] == "eligible"
    assert context["thresholds"] == pytest.approx([z(k) for k in (70, 75, 80, 90, 95, 105, 110)])
    assert np.ravel(context["intervals"]).tolist() == pytest.approx([z(70), z(95), z(105), z(110)])
    assert len(context["intervals"]) == 2
    assert context["weights"] == pytest.approx([.1]*5+[.25]*2)
    empty = DecisionRegionContextBuilder(config).build(
        panel.assign(quote_date="2020-01-03"))
    assert empty[0]["status"] == "no_eligible_condor"


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
    out = ExactExpiryCDFPanel.add_surface_dynamics(pd.DataFrame(rows), [.1, .5, .9], (1, 5, 22), 22, 252)
    first = out[out.expiry.eq('2020-03-20')].sort_values('quote_date')
    assert first.chain_log_atm_iv_change_22.iloc[-1] == pytest.approx(.22)
    assert first.chain_log_atm_iv_change_22.iloc[:22].isna().all()
    assert out.term_slope_next.notna().sum() == len(dates)
    assert out.term_slope_prev.notna().sum() == len(dates)
    assert out.rn_variance.gt(0).all() and out.rn_right_tail_integral.gt(0).all()


def _term_slopes_by_sort_values(frame):
    """The label-based term-slope reference the positional path must reproduce exactly."""
    prev, nxt = pd.Series(np.nan, index=frame.index), pd.Series(np.nan, index=frame.index)
    for _, index in frame.groupby(['symbol', 'quote_date'], sort=False).groups.items():
        ordered = frame.loc[index].sort_values('actual_calendar_dte')
        if len(ordered) > 1:
            delta = np.diff(ordered.actual_calendar_dte.to_numpy(dtype=float))
            slope = np.divide(np.diff(ordered.chain_log_atm_iv.to_numpy(dtype=float)), delta,
                              out=np.full(len(delta), np.nan), where=delta != 0)
            prev.loc[ordered.index[1:]] = slope
            nxt.loc[ordered.index[:-1]] = slope
    return prev, nxt


@pytest.mark.parametrize('dte_kind', ['int', 'float_with_nan'])
def test_term_slopes_equal_the_sort_values_reference_including_dte_ties(dte_kind):
    rng = np.random.default_rng(3)
    rows = [{'symbol': symbol, 'quote_date': f'2020-01-{day:02d}', 'expiry': f'E{k}',
             'actual_calendar_dte': int(rng.integers(1, 6)),  # few values: many ties
             'chain_log_atm_iv': float(rng.normal())}
            for symbol in ('SPY', 'QQQ') for day in range(1, 21) for k in range(int(rng.integers(1, 8)))]
    frame = pd.DataFrame(rows).sample(frac=1, random_state=4)  # shuffled, non-range index
    frame.index = frame.index*3+7
    if dte_kind == 'float_with_nan':  # the label-based fallback path
        frame['actual_calendar_dte'] = frame.actual_calendar_dte.astype(float).where(
            rng.random(len(frame)) > .1)
    prev, nxt = _term_slopes_by_sort_values(frame)
    out = frame.copy()
    ExactExpiryCDFPanel._add_term_slopes(out)
    pd.testing.assert_series_equal(out.term_slope_prev, prev, check_exact=True, check_names=False)
    pd.testing.assert_series_equal(out.term_slope_next, nxt, check_exact=True, check_names=False)
    assert out.term_slope_prev.notna().any() and out.term_slope_next.notna().any()


def test_settled_and_policy_histories_equal_the_brute_force_filters():
    study = cdf_study.RobustCorrectionStudy
    rng = np.random.default_rng(5)
    dates = pd.bdate_range('2020-01-01', periods=60).strftime('%Y-%m-%d').tolist()
    events, log, policies, policy_log = [], study._event_log(), [], study._policy_log()
    for i, date in enumerate(dates):
        for band in ('short', 'long'):
            expected = pd.DataFrame(study._settled_events(events, date, band))
            got = study._settled_frame(log, date, band)
            if expected.empty:
                assert got.empty
            else:
                pd.testing.assert_frame_equal(got, expected, check_exact=True)
            pd.testing.assert_frame_equal(
                study._policy_history(policy_log, date, 'SPY', band, dates[5]),
                study._radius_history(policies, date, 'SPY', band, dates[5]), check_exact=True)
        # Keys in the order train/optimize build each event (study._EVENT_FIELDS).
        pending = [{'quote_date': date,
                    'settlement_date': dates[min(i+int(rng.integers(0, 9)), len(dates)-1)],
                    'tenor_band': str(rng.choice(['short', 'long'])),
                    'probability': float(rng.random()), 'event': float(rng.integers(0, 2)),
                    'weight': .25} for _ in range(int(rng.integers(0, 4)))]
        events.extend(pending)
        log.extend(pending)
        sized = [{**item, 'symbol': str(rng.choice(['SPY', 'QQQ'])), 'radius': 0.,
                  'residual': float(rng.random())} for item in pending]
        policies.extend(sized)
        policy_log.extend(sized)
    assert log.rows == events and len(events) > 40


def test_macro_event_counts_equal_the_series_comparison():
    rng = np.random.default_rng(6)
    quotes = pd.bdate_range('2020-01-01', periods=80)
    frame = pd.DataFrame({'quote_date': quotes.strftime('%Y-%m-%d'),
                          'planned_settlement_date': (quotes+pd.to_timedelta(
                              rng.integers(1, 40, len(quotes)), unit='D')).strftime('%Y-%m-%d')})
    calendars = {'cpi': [{'event_date': str(day.date()), 'known_at': str((day-pd.Timedelta(days=int(lag))).date())}
                         for day, lag in zip(pd.date_range('2020-01-10', periods=6, freq='MS'),
                                             rng.integers(0, 60, 6))]}
    out, _ = ExactExpiryCDFPanel.add_macro_event_features(frame.copy(), calendars)
    quote, end = pd.to_datetime(frame.quote_date), pd.to_datetime(frame.planned_settlement_date)
    expected = sum(((pd.Timestamp(r['known_at']) <= quote) & (pd.Timestamp(r['event_date']) > quote)
                    & (pd.Timestamp(r['event_date']) <= end)).astype(int) for r in calendars['cpi'])
    pd.testing.assert_series_equal(out.macro_cpi_count, expected.astype(int), check_names=False)
    assert out.macro_cpi_count.dtype == int and out.macro_cpi_count.gt(0).any()


def test_macro_event_sentinel_dates_keep_the_series_comparison():
    frame = pd.DataFrame({'quote_date': ['2020-01-02', '2020-02-03', '2020-03-02'],
                          'planned_settlement_date': ['2020-01-30', '2020-02-28', '2020-03-30']})
    calendars = {'fomc': [{'event_date': '9999-12-31', 'known_at': '2019-01-01'},
                          {'event_date': '2020-02-10', 'known_at': '0001-01-01'}]}
    out, _ = ExactExpiryCDFPanel.add_macro_event_features(frame.copy(), calendars)
    assert out.macro_fomc_count.tolist() == [0, 1, 0]


def test_fred_daily_availability_waits_for_one_complete_exchange_session():
    dates = pd.to_datetime(['2020-07-01', '2020-07-02'])
    available = ExactExpiryCDFPanel._availability_dates(dates, 'XNYS', lag_sessions=1)
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
    out = ExactExpiryCDFPanel.add_matched_dte_vrp(frame.copy(), 1e-3, 22, 252)
    implied = .20**2*10/252
    realized = .01**2*10
    assert out.matched_implied_variance.iloc[0] == pytest.approx(implied)
    assert out.matched_trailing_variance.iloc[0] == pytest.approx(realized)
    assert out.matched_vrp.iloc[0] == pytest.approx(implied-realized)
    changed = frame.copy()
    changed.actual_calendar_dte = 2
    other = ExactExpiryCDFPanel.add_matched_dte_vrp(changed, 1e-3, 22, 252)
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
                                  'reference_floor': .001, 'spot_tolerance': .02,
                                  'dividend_field': 'dividend_amount'})
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


@pytest.mark.parametrize('name', ['run-step4-feature-selection', 'run-step5-model-zoo', 'run-step6-hpo'])
def test_step_configs_declare_the_one_exact_dte_the_study_expects(name):
    config = json.loads((Path(__file__).parents[1]/'configs'/f'{name}.json').read_text())
    horizon = config['data']['exact_dte']
    assert type(horizon) is int and horizon >= 1 and horizon <= config['data']['max_dte']
    for partition, cells in config['experiment']['expected_cells'].items():
        assert cells == {'QQQ': [horizon]}, partition


@pytest.mark.parametrize('name', ['run-step4-feature-selection', 'run-step5-model-zoo', 'run-step6-hpo'])
def test_step_configs_decision_regions_build_as_the_cli_builds_them(name):
    # The CLI builds this block before any stage runs; an unknown key refuses there.
    config = json.loads((Path(__file__).parents[1]/'configs'/f'{name}.json').read_text())
    DecisionRegionContextBuilder(config['data']['decision_regions'])


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
        # The CLI reads provenance through the real method (ADR-0217).
        provenance = cdf_study.ExactExpiryCDFPanel.provenance

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


# -- ADR-0217: the panel reader, its provenance and the family clocks -------------------------------

import math  # noqa: E402
import sys  # noqa: E402
import datetime as dt  # noqa: E402

from index_options.nodes import ExactExpiryPanelRead  # noqa: E402

FE_PROBS = [.01, .05, .1, .25, .5, .75, .9, .95, .99]
#: The engineered feature families' columns, restated here and never read from the shipped document.
FE_FIELDS = {
    "variance_gap": ["matched_implied_variance", "matched_trailing_variance", "matched_vrp",
                     "matched_vrp_ratio"],
    "ohlc_shape": ["overnight_return", "intraday_return", "log_high_low_range",
                   "parkinson_variance", "jump_variance_proxy", "range_variance_5",
                   "range_variance_22", "jump_variance_5", "jump_variance_22",
                   "overnight_variance_5", "overnight_variance_22", "intraday_variance_5",
                   "intraday_variance_22"],
    "positioning_changes": ["chain_log_volume", "chain_put_call_volume_imbalance",
                            "chain_volume_weighted_rel_spread", "chain_log_lag_open_interest",
                            "chain_log_lag_oi_change", "chain_log_delta_oi", "chain_log_gamma_oi",
                            "chain_log_vega_oi"],
    "expanded_volatility_context": [f"market_{n}{s}" for n in
                                    ("vix1y", "rvx", "vxd", "ovx", "vxeem", "vxslv", "vxtlt")
                                    for s in ("", "_missing", "_age_days")],
}
FE_MARKETS = {"market_vix1y": "VIX1Y", "market_rvx": "RVX", "market_vxd": "VXD",
              "market_ovx": "OVX", "market_vxeem": "VXEEM", "market_vxslv": "VXSLV",
              "market_vxtlt": "VXTLT"}
FE_IDENTITY = ["symbol", "quote_date", "expiry"]
FE_COLUMNS = FE_IDENTITY + [f for fields in FE_FIELDS.values() for f in fields]


def _fe_days():
    import exchange_calendars as xc
    return [d.strftime("%Y-%m-%d")
            for d in xc.get_calendar("XNYS").sessions_in_range("2023-01-03", "2023-04-28")]


def _fe_fixture(tmp_path, monkeypatch, name, *, changed_after=None, last=None, skip=None,
                bump=None):
    """Write one synthetic QQQ source set and patch ``IndexCloseRows`` to serve it.

    ``changed_after`` gives every observation dated after it a different value (prices, OHLC
    bars, index closes, chain-feature rows, surface IV); ``last`` removes everything dated after
    it; ``skip`` maps an index symbol to a predicate dropping its observations; ``bump`` maps
    (symbol, date) to a replacement close. Returns the reader config.
    """
    days = _fe_days()
    cut = lambda d: changed_after is not None and d > changed_after  # noqa: E731
    keep = lambda d: last is None or d <= last  # noqa: E731
    bump = bump or {}
    skip = skip or {}
    px = {d: (100+i*.3+math.sin(i))*(1.07 if cut(d) else 1.) for i, d in enumerate(days)}

    class Reader:
        def __init__(self, key, params):
            self.symbol = params["symbol"]

        def run(self, ctx, inputs):
            rows = []
            for i, d in enumerate(days):
                if not keep(d) or skip.get(self.symbol, lambda day: False)(d):
                    continue
                if self.symbol == "QQQ":
                    c = px[d]
                    rows.append({"date": d, "close": c, "open": c*(.97 if cut(d) else .99),
                                 "high": c*(1.03 if cut(d) else 1.01),
                                 "low": c*(.95 if cut(d) else .98), "asof_ms": 0,
                                 "instrument": "QQQ", "contract": "QQQ", "group": "QQQ",
                                 "dividend_amount": None})
                else:
                    close = bump.get((self.symbol, d), 20.+i*.01+(3. if cut(d) else 0.))
                    rows.append({"date": d, "close": close, "asof_ms": 0,
                                 "instrument": self.symbol, "contract": self.symbol,
                                 "group": self.symbol})
            return {"records": rows}

        def fingerprint(self):
            return {"sha256": "fixture"}

    monkeypatch.setattr("index_options.cdf_study.IndexCloseRows", Reader)
    surface = []
    for d in days[30:60]:
        if not keep(d):
            continue
        for k in (7, 14):
            expiry = (pd.Timestamp(d)+pd.Timedelta(days=k)).strftime("%Y-%m-%d")
            surface.append({
                "symbol": "QQQ", "quote_date": d, "expiry": expiry,
                "chain_underlying_price": px[d], "chain_atm_iv": .3 if cut(d) else .2,
                "chain_put25_iv": .22, "chain_call25_iv": .19, "chain_rel_spread": .01,
                "chain_put_call_oi": 1.2, "chain_contracts": 100., "chain_open_interest": 1000.,
                "chain_quote_depth": 50.})
    surface = pd.DataFrame(surface)
    base = tmp_path/name
    base.mkdir()
    surface.to_parquet(base/"surface.parquet")
    surface[FE_IDENTITY].assign(first_seen_date="2023-01-03").to_parquet(base/"lifecycle.parquet")
    chain = surface[FE_IDENTITY].copy()
    for p, q in zip(FE_PROBS, np.linspace(-.05, .05, 9)):
        chain[f"rn_q_{int(round(p*1e4)):04d}"] = q
    chain["rn_proxy_eligible"] = 1
    for j, field in enumerate(FE_FIELDS["positioning_changes"]):
        chain[field] = [.5+.01*j+(1. if cut(d) else 0.) for d in chain.quote_date]
    chain.to_parquet(base/"chain.parquet")
    (base/"chain.parquet.sources.json").write_text("{}")
    return {"root": "x", "surface": str(base/"surface.parquet"),
            "lifecycle": str(base/"lifecycle.parquet"), "chain_features": str(base/"chain.parquet"),
            "raw_chain": {"proxy_probabilities": FE_PROBS}, "symbols": {"QQQ": "VXN"},
            "market_symbols": {k: {"symbol": v, "max_age_days": 7} for k, v in FE_MARKETS.items()},
            "price_source": "p", "iv_source": "i", "since": "2023-01-01", "max_dte": 45,
            "lags": 22, "windows": [1, 5, 22, 66], "feature_gap_days": 7,
            "reference_floor": .001, "spot_tolerance": .02, "surface_features": True,
            "ohlc_windows": [5, 22], "matched_dte_vrp": True}


def _fe_read(cfg, columns=FE_COLUMNS):
    """The engineered rows through the reader node, keyed by identity."""
    out = ExactExpiryPanelRead("panel", {**cfg, "columns": list(columns)}).run(None, {})
    return {tuple(r[k] for k in FE_IDENTITY): r for r in out["records"]}


def test_panel_reader_values_equal_the_read_for_the_same_identities(tmp_path, monkeypatch):
    cfg = _fe_fixture(tmp_path, monkeypatch, "a")
    frame = ExactExpiryCDFPanel(cfg).read()
    node = ExactExpiryPanelRead("panel", {**cfg, "columns": FE_COLUMNS})
    out = node.run(None, {})
    assert len(out["records"]) == len(frame) > 0
    for record, (_, row) in zip(out["records"], frame.iterrows()):          # the read's order
        assert list(record) == FE_COLUMNS
        for name in FE_COLUMNS:
            want = row[name]
            if isinstance(want, float) and math.isnan(want):
                assert record[name] is None
            else:
                assert record[name] == want
    assert all(r[n] is not None for r in out["records"] for n in FE_COLUMNS)    # a full fixture
    assert type(out["records"][0]["matched_vrp"]) is float            # plain Python, not numpy


def test_provenance_is_the_one_owner_of_what_main_hands_the_study(tmp_path, monkeypatch):
    cfg = _fe_fixture(tmp_path, monkeypatch, "a")
    adapter = ExactExpiryCDFPanel(cfg)
    adapter.read()
    expected = {"refused": adapter.refused, "sha256": adapter.source_hashes,
                "readers": adapter.reader_fingerprints, "market_coverage": adapter.market_coverage,
                "macro_event_status": adapter.macro_event_status,
                "adapter_sha256": hashlib.sha256(Path(cdf_study.__file__).read_bytes()).hexdigest()}
    assert adapter.provenance() == expected
    assert list(adapter.provenance()) == list(expected)
    assert set(adapter.market_coverage) == set(FE_MARKETS)
    bare = ExactExpiryCDFPanel({})
    bare.refused, bare.source_hashes, bare.reader_fingerprints = {}, {}, {}
    assert bare.provenance()["market_coverage"] == {} == bare.provenance()["macro_event_status"]
    # and _main passes exactly that dict to the study (the HPO hashes it)
    seen = {}

    class Study:
        def __init__(self, config):
            pass

        def run(self, frame, diagnostic, **kwargs):
            seen.update(kwargs)

    reads = []

    class Panel(ExactExpiryCDFPanel):
        def read(self):
            reads.append(1)
            self.refused, self.source_hashes, self.reader_fingerprints = {"a": 1}, {"b": "c"}, {}
            return pd.DataFrame({"x": [1.]})

    monkeypatch.setattr(cdf_study, "ExactExpiryCDFPanel", Panel)
    monkeypatch.setattr(cdf_study, "CDFHyperparameterStudy", Study)
    config = tmp_path/"config.json"
    config.write_text(json.dumps({"data": {}, "diagnostic": {"strikes_z": [-1., 1.],
                                                            "integration_points": 5},
                                  "experiment": {"output": str(tmp_path/"hpo")}}))
    monkeypatch.setattr(sys, "argv", ["cdf_study", str(config), "--stage", "search"])
    want = {"refused": {"a": 1}, "sha256": {"b": "c"}, "readers": {}, "market_coverage": {},
            "macro_event_status": {},
            "adapter_sha256": hashlib.sha256(Path(cdf_study.__file__).read_bytes()).hexdigest()}
    cdf_study._main()
    assert seen["provenance"] == want and reads == [1]
    seen.clear()
    cdf_study._main()   # a later stage reuses the panel the first one built (ADR-0236 amendment)
    assert seen["provenance"] == want and reads == [1]
    assert (tmp_path/"hpo"/cdf_study.PANEL_CACHE_DIR/"frame.parquet").exists()
    # The panel stage builds the cache in its own process and runs no study.
    seen.clear()
    config.write_text(json.dumps({**json.loads(config.read_text()),
                                  "experiment": {"output": str(tmp_path/"fresh")}}))
    monkeypatch.setattr(sys, "argv", ["cdf_study", str(config), "--stage", "panel"])
    cdf_study._main()
    assert reads == [1, 1] and not seen
    assert (tmp_path/"fresh"/cdf_study.PANEL_CACHE_DIR/"frame.parquet").exists()
    monkeypatch.setattr(sys, "argv", ["cdf_study", str(config), "--stage", "panel",
                                      "--partition", "x"])
    with pytest.raises(SystemExit):
        cdf_study._main()
    assert reads == [1, 1]

    class Contexts(Panel):
        def read(self):
            super().read()
            return pd.DataFrame({"x": [1.], "context": [{"t": [1.]}]})   # never storable

    monkeypatch.setattr(cdf_study, "ExactExpiryCDFPanel", Contexts)
    monkeypatch.setattr(sys, "argv", ["cdf_study", str(config), "--stage", "panel"])
    with pytest.raises(ValueError, match="could not cache the panel"):
        cdf_study._main()
    # A document the HPO study refuses is refused before any panel is read.
    before = len(reads)

    class Refused(Study):
        def __init__(self, config):
            raise ValueError("unknown or missing experiment keys")

    monkeypatch.setattr(cdf_study, "CDFHyperparameterStudy", Refused)
    monkeypatch.setattr(sys, "argv", ["cdf_study", str(config), "--stage", "search"])
    with pytest.raises(ValueError, match="experiment keys"):
        cdf_study._main()
    assert len(reads) == before


def test_expanded_context_joins_only_the_strictly_prior_close_and_ages_it(tmp_path, monkeypatch):
    days = _fe_days()
    entry = days[40]
    bumped = {(sym, entry): 99. for sym in FE_MARKETS.values()}
    skip = {"VXSLV": lambda d: "2023-03-01" <= d <= "2023-03-20",
            "VXTLT": lambda d: d < "2023-02-22"}
    base = _fe_read(_fe_fixture(tmp_path, monkeypatch, "a", skip=skip))
    moved = _fe_read(_fe_fixture(tmp_path, monkeypatch, "b", skip=skip, bump=bumped))
    by_date = {k[1]: v for k, v in base.items()}
    moved_by_date = {k[1]: v for k, v in moved.items()}
    for quote_date, row in by_date.items():                       # a plain prior close, age >= 1
        i = days.index(quote_date)
        if row["market_vix1y_missing"] == 0:
            assert row["market_vix1y"] == pytest.approx(20+(i-1)*.01)
            assert row["market_vix1y_age_days"] == (
                dt.date.fromisoformat(days[i])-dt.date.fromisoformat(days[i-1])).days >= 1
    # perturbing the entry-date close changes nothing that day; the next day reads it
    assert moved_by_date[entry]["market_vix1y"] == by_date[entry]["market_vix1y"]
    assert moved_by_date[days[41]]["market_vix1y"] == 99.
    # an age equal to max_age_days is kept; one more is null with _missing = 1
    kept, stale = by_date["2023-03-07"], by_date["2023-03-08"]
    assert kept["market_vxslv_age_days"] == 7 and kept["market_vxslv_missing"] == 0
    assert kept["market_vxslv"] == pytest.approx(20+days.index("2023-02-28")*.01)
    assert stale["market_vxslv_age_days"] == 8 and stale["market_vxslv"] is None
    assert stale["market_vxslv_missing"] == 1
    # a date before the series starts has no prior close at all
    first = by_date[days[32]]                                     # 2023-02-17, series starts 02-22
    assert first["market_vxtlt"] is None and first["market_vxtlt_missing"] == 1
    assert first["market_vxtlt_age_days"] is None


def test_every_engineered_family_is_causal_in_the_entry_date(tmp_path, monkeypatch):
    days = _fe_days()
    q, last = days[46], days[50]
    a = _fe_read(_fe_fixture(tmp_path, monkeypatch, "a"))
    b = _fe_read(_fe_fixture(tmp_path, monkeypatch, "b", changed_after=q))
    c = _fe_read(_fe_fixture(tmp_path, monkeypatch, "c", last=last))
    assert a.keys() == b.keys() and set(c) < set(a) and c
    early = [k for k in a if k[1] <= q]
    late = [k for k in a if k[1] > q]
    assert early and late
    for key in early:                                  # nothing dated after q* reaches a row at q*
        assert {n: a[key][n] for n in FE_COLUMNS[3:]} == {n: b[key][n] for n in FE_COLUMNS[3:]}
    witness = {"variance_gap": "matched_vrp", "ohlc_shape": "range_variance_5",
               "positioning_changes": "chain_log_volume",
               "expanded_volatility_context": "market_vix1y"}
    for family, field in witness.items():              # and the perturbation does bite later
        assert any(a[k][field] != b[k][field] for k in late), family
    for key, row in c.items():                         # dropping later sources changes no survivor
        assert row == a[key]


def test_positioning_lags_read_only_earlier_snapshots_and_survive_truncation():
    dates = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
    chain = pd.DataFrame({
        "symbol": ["SPY"]*8, "date": [d for d in dates for _ in (0, 1)],
        "expiration": ["2020-02-21"]*8, "strike": [95., 105.]*4, "type": ["put", "call"]*4,
        "mark": [2.]*8, "bid": [1.9]*8, "ask": [2.1]*8, "bid_size": [10]*8, "ask_size": [12]*8,
        "open_interest": [100, 200, 110, 220, 130, 240, 400, 800], "volume": [10, 20, 15, 25, 18, 30, 9, 9],
        "implied_volatility": [.2]*8, "delta": [-.3, .3]*4, "gamma": [.01]*8, "vega": [.1]*8})
    meta = pd.DataFrame({"symbol": ["SPY"]*4, "quote_date": dates, "expiry": ["2020-02-21"]*4,
                         "chain_underlying_price": [100.]*4})
    builder = RawChainFeatureBuilder(nodes=3, moneyness_bounds=[-.1, .1], max_node_gap=.1,
                                     proxy_probabilities=[.1, .5, .9], min_wing_nodes=1,
                                     max_inner_gap=.1, max_outer_gap=.2)
    full = builder.transform(chain, meta)
    cut = builder.transform(chain[chain.date <= dates[2]], meta[meta.quote_date <= dates[2]])
    pd.testing.assert_frame_equal(full.iloc[:3].reset_index(drop=True), cut)
    assert full.chain_log_lag_open_interest.iloc[3] == pytest.approx(np.log1p(370))


# -- store references (ADR-0225): the same bytes by name read the same rows as by path ---------------

def _store_ref(source, relpath=None, **extra):
    entry = {'source': source, 'stream': 'files', **extra}
    if relpath is not None:
        entry['relpath'] = relpath
    return entry


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _decision_chain():
    return pd.DataFrame({
        'symbol': ['SPY']*6, 'date': ['2020-01-02']*6, 'expiration': ['2020-02-21']*6,
        'strike': [85., 90., 95., 105., 110., 115.], 'type': ['put']*3+['call']*3,
        'bid': [1., 2., 3., 3., 2., 1.], 'ask': [1.2, 2.2, 3.2, 3.2, 2.2, 1.2],
        'bid_size': [3]*6, 'ask_size': [3]*6})


def _decision_config(archive_root):
    return {'archive_root': archive_root, 'max_chain_rows': 100,
            'clock': 'after_date_close_indicative_not_executable',
            'limits': {'max_seconds': 1800, 'max_resident_mib': 6144},
            'chain_rule': {'max_abs_log_moneyness': .2, 'min_wing_width': 5.,
                           'max_wing_width': 10., 'max_candidates': 100,
                           'fee_per_leg': 0., 'multiplier': 100}}


def test_decision_context_from_a_store_reference_equals_the_path_read(tmp_path, blob_store):
    archive = tmp_path/'archive'
    (archive/'spy').mkdir(parents=True)
    source = archive/'spy'/'options_2020.parquet'
    _decision_chain().to_parquet(source, index=False)
    blob_store.add('options-archive', archive)
    panel = pd.DataFrame({'symbol': ['SPY', 'SPY'], 'quote_date': ['2020-01-02', '2020-01-03'],
                          'expiry': ['2020-02-21']*2, 'spot': [100.]*2,
                          'reference_scale': [.2]*2})
    legacy = DecisionRegionContextBuilder(_decision_config(str(archive)))
    stored = DecisionRegionContextBuilder(
        _decision_config(_store_ref('options-archive')), DataFiles(blob_store.path))
    want, got = legacy.build(panel), stored.build(panel)

    def body(contexts):
        return [{k: v for k, v in c.items() if k != 'provenance_sha256'} for c in contexts]

    assert body(got) == body(want) and got[0]['status'] == 'eligible'
    assert got[1]['status'] == 'no_eligible_condor'
    assert stored.provenance['matching_chain_rows'] == legacy.provenance['matching_chain_rows'] == 6
    # a path source is named by its path, a store source by name; both carry the file's digest
    assert legacy.provenance['sources'] == {str(source): _sha256(source)}
    assert stored.provenance['sources'] == {
        'store:options-archive/files/spy/options_2020.parquet': _sha256(source)}
    assert {c['provenance_sha256'] for c in got} == {stored.provenance['sha256']}
    snapshot = payload_files(blob_store.path, 'options-archive', 'files')
    assert stored.files.provenance() == [{
        'source': 'options-archive', 'stream': 'files', 'snapshot': snapshot['snapshot'],
        'manifest_sha256': snapshot['manifest_sha256'], 'files': 1}]
    assert legacy.files.provenance() == []


def test_decision_context_refuses_an_unresolvable_or_malformed_archive_reference(
        tmp_path, blob_store):
    reference = _store_ref('options-archive')
    with pytest.raises(DataSourceError, match='needs a store root'):
        DecisionRegionContextBuilder(_decision_config(reference)).build(pd.DataFrame({
            'symbol': ['SPY'], 'quote_date': ['2020-01-02'], 'expiry': ['2020-02-21'],
            'spot': [100.], 'reference_scale': [.2]}))
    for bad in ({'source': 'options-archive'}, _store_ref('options-archive', 'spy/a.parquet'),
                {**reference, 'pin': 'x'}, 3, ''):
        with pytest.raises(ValueError, match='invalid non-executable decision-context protocol'):
            DecisionRegionContextBuilder(_decision_config(bad))


def test_raw_chain_archive_from_a_store_tree_equals_the_path_build(tmp_path, blob_store):
    archive = tmp_path/'archive'
    (archive/'spy').mkdir(parents=True)
    source = archive/'spy'/'options_2020.parquet'
    pd.DataFrame([{'symbol': 'SPY', 'date': '2020-01-02', 'expiration': '2020-02-21',
                   'strike': 100., 'type': 'call', 'mark': 4., 'bid': 3.9, 'ask': 4.1,
                   'bid_size': 10, 'ask_size': 12, 'open_interest': 100,
                   'implied_volatility': .2}]).to_parquet(source, index=False)
    blob_store.add('options-archive', archive)
    meta = pd.DataFrame({'symbol': ['SPY'], 'quote_date': ['2020-01-02'],
                         'expiry': ['2020-02-21'], 'chain_underlying_price': [100.]})
    builder = RawChainFeatureBuilder(nodes=3, moneyness_bounds=[-.1, .1], max_node_gap=.1,
                                     proxy_probabilities=[.1, .5, .9], min_wing_nodes=1,
                                     max_inner_gap=.1, max_outer_gap=.2)
    files = DataFiles(blob_store.path)
    legacy = builder.build_archive(meta, archive, tmp_path/'legacy.parquet')
    stored = builder.build_archive(meta, files.tree(_store_ref('options-archive')),
                                   tmp_path/'stored.parquet')
    pd.testing.assert_frame_equal(stored, legacy)
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path/'stored.parquet'),
                                  pd.read_parquet(tmp_path/'legacy.parquet'))
    old = json.loads(Path(str(tmp_path/'legacy.parquet')+'.sources.json').read_text())
    new = json.loads(Path(str(tmp_path/'stored.parquet')+'.sources.json').read_text())
    digest = _sha256(source)
    assert old['sources'] == {'SPY-2020': {'path': str(source), 'sha256': digest}}
    assert new['sources'] == {'SPY-2020': {
        'path': 'store:options-archive/files/spy/options_2020.parquet', 'sha256': digest}}
    assert 'store' not in old
    record, = files.provenance()
    assert new['store'] == {k: record[k] for k in ('source', 'stream', 'snapshot', 'manifest_sha256')}
    assert new['metadata_sha256'] == old['metadata_sha256']


def test_prepare_reads_store_references_and_writes_only_a_plain_output_path(
        tmp_path, monkeypatch, blob_store):
    import sys

    archive, tables = tmp_path/'archive', tmp_path/'tables'
    (archive/'spy').mkdir(parents=True)
    tables.mkdir()
    pd.DataFrame([{'symbol': 'SPY', 'date': '2020-01-02', 'expiration': '2020-02-21',
                   'strike': 100., 'type': 'call', 'mark': 4., 'bid': 3.9, 'ask': 4.1,
                   'bid_size': 10, 'ask_size': 12, 'open_interest': 100,
                   'implied_volatility': .2}]).to_parquet(
                       archive/'spy'/'options_2020.parquet', index=False)
    identity = {'symbol': ['SPY'], 'quote_date': ['2020-01-02'], 'expiry': ['2020-02-21']}
    pd.DataFrame({**identity, 'chain_underlying_price': [100.]}).to_parquet(
        tables/'surface.parquet', index=False)
    pd.DataFrame({**identity, 'first_seen_date': ['2019-12-01']}).to_parquet(
        tables/'lifecycle.parquet', index=False)
    blob_store.add('options-archive', archive)
    blob_store.add('tables', tables)
    output = tmp_path/'features.parquet'
    config = {'data': {
        'root': blob_store.path, 'surface': _store_ref('tables', 'surface.parquet'),
        'lifecycle': _store_ref('tables', 'lifecycle.parquet'),
        'archive_root': _store_ref('options-archive'), 'chain_features': str(output),
        'raw_chain': {'nodes': 3, 'moneyness_bounds': [-.1, .1], 'max_node_gap': .1,
                      'proxy_probabilities': [.1, .5, .9], 'min_wing_nodes': 1,
                      'max_inner_gap': .1, 'max_outer_gap': .2}}}
    path = tmp_path/'prepare.json'
    path.write_text(json.dumps(config))
    monkeypatch.setattr(sys, 'argv', ['cdf_study', str(path), '--stage', 'prepare'])
    cdf_study._main()
    assert len(pd.read_parquet(output)) == 1
    sidecar = json.loads(Path(str(output)+'.sources.json').read_text())
    assert list(sidecar['sources']) == ['SPY-2020'] and sidecar['store']['source'] == 'options-archive'
    # the output is written, so a store reference cannot name it
    config['data']['chain_features'] = _store_ref('raw-chain-features', 'raw_chain_features.parquet')
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='prepare writes data.chain_features'):
        cdf_study._main()


def test_panel_read_from_store_references_equals_the_path_read(tmp_path, monkeypatch, blob_store):
    cfg = _fe_fixture(tmp_path, monkeypatch, 'a')
    blob_store.add('fixture-tables', tmp_path/'a')

    def ref(relpath):
        return _store_ref('fixture-tables', relpath)

    stored_cfg = {**cfg, 'root': blob_store.path, 'surface': ref('surface.parquet'),
                  'lifecycle': ref('lifecycle.parquet'), 'chain_features': ref('chain.parquet')}
    legacy, stored = ExactExpiryCDFPanel(cfg), ExactExpiryCDFPanel(stored_cfg)
    want, got = legacy.read(), stored.read()
    pd.testing.assert_frame_equal(got, want)
    assert len(got) > 0
    # the same bytes: the manifest's digests are the digests the path read computes
    assert stored.source_hashes == legacy.source_hashes
    assert set(stored.source_hashes) == {'surface', 'lifecycle', 'chain_features',
                                         'chain_feature_sources'}
    snapshot = payload_files(blob_store.path, 'fixture-tables', 'files')
    assert stored.provenance()['store'] == [{
        'source': 'fixture-tables', 'stream': 'files', 'snapshot': snapshot['snapshot'],
        'manifest_sha256': snapshot['manifest_sha256'], 'files': 4}]
    assert 'store' not in legacy.provenance()
    assert {k: v for k, v in stored.provenance().items() if k != 'store'} == legacy.provenance()


def test_panel_read_pins_and_sidecar_are_enforced_for_store_references(
        tmp_path, monkeypatch, blob_store):
    cfg = _fe_fixture(tmp_path, monkeypatch, 'a')
    bare = tmp_path/'bare'
    bare.mkdir()
    for name in ('surface.parquet', 'lifecycle.parquet', 'chain.parquet'):
        (bare/name).write_bytes((tmp_path/'a'/name).read_bytes())
    blob_store.add('fixture-tables', tmp_path/'a')
    blob_store.add('fixture-bare', bare)

    def stored(source, **chain):
        return {**cfg, 'root': blob_store.path,
                'surface': _store_ref(source, 'surface.parquet'),
                'lifecycle': _store_ref(source, 'lifecycle.parquet'),
                'chain_features': _store_ref(source, 'chain.parquet', **chain)}

    with pytest.raises(ValueError, match='raw-chain source hash manifest is missing'):
        ExactExpiryCDFPanel(stored('fixture-bare')).read()
    with pytest.raises(DataSourceError, match='pinned manifest_sha256'):
        ExactExpiryCDFPanel(stored('fixture-tables', manifest_sha256='0'*64)).read()
    manifest = payload_files(blob_store.path, 'fixture-tables', 'files')['manifest_sha256']
    assert len(ExactExpiryCDFPanel(stored('fixture-tables', manifest_sha256=manifest)).read()) > 0
    with pytest.raises(DataSourceError, match="no file 'nope.parquet'"):
        ExactExpiryCDFPanel({**stored('fixture-tables'),
                             'surface': _store_ref('fixture-tables', 'nope.parquet')}).read()


def test_cli_writes_the_resolved_snapshots_into_data_provenance(tmp_path, monkeypatch, blob_store):
    import sys

    (tmp_path/'t').mkdir()
    (tmp_path/'t'/'a.parquet').write_bytes(b'PAR1')
    blob_store.add('tables', tmp_path/'t')
    output, seen = tmp_path/'run', {}

    class Adapter:
        provenance = cdf_study.ExactExpiryCDFPanel.provenance

        def __init__(self, config, holdout_start=None):
            self.refused, self.source_hashes, self.reader_fingerprints = {}, {'surface': 'abc'}, {}
            self.data_files = DataFiles(blob_store.path)

        def read(self):
            seen['path'] = self.data_files.path(_store_ref('tables', 'a.parquet'))
            return pd.DataFrame({'a': [1]})

    class Study:
        def __init__(self, config):
            self.output = Path(config['output'])

        def run(self, frame, diagnostic):
            self.output.mkdir(parents=True)

        def summarize(self, scores):
            pass

    monkeypatch.setattr(cdf_study, 'ExactExpiryCDFPanel', Adapter)
    monkeypatch.setattr(cdf_study, 'ChronologicalCDFStudy', Study)
    config = tmp_path/'config.json'
    config.write_text(json.dumps({
        'data': {}, 'diagnostic': {'strikes_z': [-2, -1, 1, 2], 'integration_points': 11},
        'study': {'output': str(output)}}))
    monkeypatch.setattr(sys, 'argv', ['cdf_study', str(config)])
    cdf_study._main()
    snapshot = payload_files(blob_store.path, 'tables', 'files')
    record = json.loads((output/'data_provenance.json').read_text())
    assert record == {'refused': {}, 'sha256': {'surface': 'abc'}, 'readers': {},
                      'store': [{'source': 'tables', 'stream': 'files',
                                 'snapshot': snapshot['snapshot'],
                                 'manifest_sha256': snapshot['manifest_sha256'], 'files': 1}]}
    assert seen['path'] == snapshot['files']['a.parquet']


def _region_settings(tmp_path, archive_root, underlying_root):
    return {
        'forecast_root': str(tmp_path/'frozen'), 'partition': 'late',
        'models': {'base': 'raw'}, 'symbols': ['SPY'], 'archive_root': archive_root,
        'output': str(tmp_path/'out'), 'max_rows_per_symbol': 1,
        'underlying': {'root': underlying_root, 'source': 'fixture', 'since_ms': 0,
                       'carry_rate': .055},
        'strata': {'tenor_days': [7, 21], 'iv': [20, 30], 'wing_log_moneyness': [.02, .05]},
        'chain_rule': {'max_abs_log_moneyness': .1, 'min_wing_width': 5., 'max_wing_width': 5.,
                       'max_candidates': 10, 'fee_per_leg': 0., 'multiplier': 100},
        'audit': {'price_step': .5, 'support_margin_fraction': .02, 'mesh_tolerance': .5,
                  'floor': .1, 'radius': 0., 'score_refinement_factor': 2,
                  'score_tolerance': .01},
        'limits': {'max_seconds': 1800, 'max_address_space_mib': 6144,
                   'max_chain_rows': 100000, 'max_grid_nodes': 4001}}


def test_decision_region_study_refuses_a_malformed_archive_entry(tmp_path):
    for bad in ({'source': 'options-archive'}, _store_ref('options-archive', 'spy/a.parquet'), 3, ''):
        with pytest.raises(ValueError, match='invalid decision-region archive_root'):
            cdf_study.DecisionRegionStudy(_region_settings(tmp_path, bad, str(tmp_path)))
    cdf_study.DecisionRegionStudy(_region_settings(tmp_path, _store_ref('options-archive'),
                                                   str(tmp_path)))


@pytest.mark.parametrize('mode', ['path', 'store'])
def test_decision_region_prepare_pins_the_archive_it_read(tmp_path, monkeypatch, blob_store, mode):
    archive = tmp_path/'archive'
    (archive/'spy').mkdir(parents=True)
    source = archive/'spy'/'options_2020.parquet'
    _decision_chain().to_parquet(source, index=False)
    blob_store.add('options-archive', archive)
    partition = tmp_path/'frozen'/'evaluate'/'late'
    partition.mkdir(parents=True)
    pd.DataFrame({'symbol': ['SPY'], 'quote_date': ['2020-01-02'], 'expiry': ['2020-02-21'],
                  'settlement_date': ['2020-02-21'], 'actual_calendar_dte': [50],
                  'spot': [100.], 'terminal_price': [101.], 'reference_scale': [.02]}).to_parquet(
                      partition/'input_panel.parquet', index=False)
    settings = _region_settings(
        tmp_path, str(archive) if mode == 'path' else _store_ref('options-archive'),
        blob_store.path)
    study = cdf_study.DecisionRegionStudy(settings)
    monkeypatch.setattr(study, '_verified_partition', lambda _partition: {})
    np.savez_compressed(study._curve_path('SPY', 'base'),
                        identities=np.array([['SPY', '2020-01-02', '2020-02-21']]))
    manifest = study.prepare()
    assert manifest['chain_rows'] == 6
    location = source if mode == 'path' else payload_files(
        blob_store.path, 'options-archive', 'files')['files']['spy/options_2020.parquet']
    assert manifest['sources'][str(location)] == _sha256(source)
    assert set(manifest['sources']) == {str(location), str(study._curve_path('SPY', 'base'))}
    for path, digest in manifest['sources'].items():          # what `evaluate` re-checks
        assert cdf_study.DecisionRegionStudy._digest(path) == digest
    written = json.loads((tmp_path/'out'/'prepare'/'manifest.json').read_text())
    if mode == 'path':
        assert 'store' not in written
    else:
        assert written['store'] == manifest['store']
        assert [r['source'] for r in written['store']] == ['options-archive']


def test_calendar_radius_uses_complete_blocks_and_keeps_legacy_result():
    owner = cdf_study.AdaptiveWassersteinRadius(
        radii=[0., .01], min_dates=4, block_dates=1,
        replicates=100, alpha=.1, seed=7)
    rows = [{"quote_date": f"2024-01-{day:02d}", "radius": radius,
             "residual": residual}
            for day in range(1, 7)
            for radius, residual in ((0., .2), (.01, -.2))]
    history = pd.DataFrame(rows)
    before = owner.select(history)
    assert "calendar_days" in __import__("inspect").signature(owner.select).parameters
    result = owner.select(history, calendar_days=2)
    assert result["radius"] == .01
    assert result["full_means"] == {0.: pytest.approx(.2), .01: pytest.approx(-.2)}
    assert result["support"]["supported"]
    assert before == owner.select(history)


def test_calendar_radius_refuses_uncovered_tail_and_unpaired_radius_dates():
    owner = cdf_study.AdaptiveWassersteinRadius(
        radii=[0., .01], min_dates=2, block_dates=1,
        replicates=100, alpha=.1, seed=7)
    assert "calendar_days" in __import__("inspect").signature(owner.select).parameters
    history = pd.DataFrame([
        {"quote_date": day, "radius": radius, "residual": -.2}
        for day in ("2024-01-01", "2024-01-03")
        for radius in (0., .01)])
    result = owner.select(history, calendar_days=2)
    assert result["radius"] is None
    assert result["support"]["zero_inclusion_dates"] == ["2024-01-03"]
    with pytest.raises(ValueError, match="paired"):
        owner.select(history.iloc[:-1], calendar_days=1)


def test_calendar_radius_full_observed_mean_must_pass(monkeypatch):
    from dskit.pipeline.stats import CalendarBlockBootstrap

    owner = cdf_study.AdaptiveWassersteinRadius(
        radii=[0.], min_dates=2, block_dates=1,
        replicates=100, alpha=.1, seed=7)
    assert "calendar_days" in __import__("inspect").signature(owner.select).parameters
    history = pd.DataFrame({"quote_date": ["2024-01-01", "2024-01-02"],
                            "radius": [0., 0.], "residual": [-1., 3.]})
    # Force a pessimistically unrepresentative draw to isolate the observed-mean gate.
    monkeypatch.setattr(CalendarBlockBootstrap, "sample_indices",
                        lambda self, replicates, seed: [[0, 0]] * replicates)
    result = owner.select(history, calendar_days=1)
    assert result["upper_bounds"][0.] == -1.
    assert result["full_means"][0.] == 1.
    assert result["radius"] is None


def test_calendar_radius_does_not_silently_drop_missing_date():
    owner = cdf_study.AdaptiveWassersteinRadius(
        radii=[0.], min_dates=2, block_dates=1,
        replicates=100, alpha=.1, seed=7)
    history = pd.DataFrame({"quote_date": ["2024-01-01", "2024-01-02", None],
                            "radius": [0., 0., 0.], "residual": [-1., -1., 100.]})
    with pytest.raises(ValueError, match="quote_date"):
        owner.select(history, calendar_days=1)

def test_held_out_rho_all_months_strict_publication_and_no_repeated_lps(monkeypatch):
    params = dict(rho_grid=[0., .01], min_dates=2,
                  block_days={"primary": 1, "sensitivity": 3},
                  replicates=20, alpha=.1, seed=7, short_q=.1, wing_strikes=1,
                  min_trade_count=1, min_volume=1, publication_lag_sessions=1,
                  max_settlement_gap_days=4, end_before="2026-01-01",
                  settlement_field="as_traded_close")
    contexts = [("2024-01-02", "2024-01-31", "calibration"),
                ("2024-02-01", "2024-03-04", "entry"),
                ("2024-03-01", "2024-04-01", "entry")]
    masses, chain = [], []
    for day, expiry, phase in contexts:
        masses.append(dict(symbol="X", quote_date=day, expiry=expiry, phase=phase, settlement_date=expiry,
            spot=100., reference=.1,
            curve={"kind": "mixture", "weights": [[1.]], "means": [[0.]], "scales": [[1.]]},
            grid=[80., 90., 100., 110., 120.], masses=[.1, .2, .4, .2, .1],
            fit_identity="fit", checkpoint_identity="checkpoint",
            source_identity="source", input_identity=day))
        for right, strike in [("put",80.),("put",90.),("put",100.),
                              ("call",100.),("call",110.),("call",120.)]:
            chain.append(dict(symbol="X", quote_date=day, expiry=expiry,
                              right=right, strike=strike, trade_count=2, volume=2))
    bars=[dict(symbol="X", date=day, as_traded_close=100.) for day in
          ["2024-01-31","2024-02-01","2024-02-02",
           "2024-03-04","2024-03-05","2024-03-06",
           "2024-04-01","2024-04-02","2024-04-03"]]
    calls=[]
    original=cdf_study.DiscreteCDFGrid.worst_expected_loss
    def counted(self, *args, **kwargs):
        calls.append(args[1])
        return original(self,*args,**kwargs)
    monkeypatch.setattr(cdf_study.DiscreteCDFGrid,"worst_expected_loss",counted)
    out=cdf_study.HeldOutRhoCalibration("rho",params).run(
        None,dict(masses=masses,chain=chain,bars=bars))
    assert not out["skips"]
    audit=out["audit"].value
    assert set(out["rho"]) == {"2024-02","2024-03"}
    assert [row["history_count"] for row in audit["monthly"]] == [0,1]
    assert audit["records"][0]["settlement_date"] == "2024-01-31"
    assert audit["records"][0]["publication_date"] == "2024-02-01"
    assert len(calls) == len(masses)*len(params["rho_grid"])
    assert all(row["checkpoint_identity"] == "checkpoint" for row in audit["records"])
    assert set(out["rho"].values()) == {None}

    blocked=dict(masses=masses,chain=chain,bars=bars,
                 holding_exclusions=[{**{key:masses[0][key] for key in
                    ("symbol","quote_date","expiry")},"reason":"holding_split"}])
    refused=cdf_study.HeldOutRhoCalibration("rho",params).run(None,blocked)
    assert any(row["reason"] == "holding_split" and
               row["input_identity"] == masses[0]["input_identity"]
               for row in refused["skips"])
    assert refused["audit"].value["monthly"][1]["history_count"] == 0

def test_held_out_rho_calendar_days_are_not_minimum_observation_counts():
    config=json.loads((Path(__file__).parents[1]/"configs"/
                       "run-equity-condor-robust-backtest.json").read_text())
    params=config["pipeline"]["rho_calibration"]["params"]
    assert params["min_dates"] == 8
    assert params["block_days"] == {"primary":31,"sensitivity":62}
    assert cdf_study.HeldOutRhoCalibration.validate_params(params) == []


def test_held_out_rho_publication_from_settlement_and_label_date_agreement():
    mass={"symbol":"X","expiry":"2025-01-31","settlement_date":"2025-01-31"}
    bars=[dict(symbol="X",date=day,as_traded_close=100.)
          for day in ["2025-01-30","2025-01-31","2025-02-03","2025-02-04"]]
    close,settled,published=cdf_study.HeldOutRhoCalibration._bar_close(
        mass,bars,4,1,"2026-01-01","as_traded_close")
    assert (close,settled,published)==(100.,"2025-01-31","2025-02-03")
    with pytest.raises(ValueError,match="forecast settlement"):
        cdf_study.HeldOutRhoCalibration._bar_close(
            mass,[r for r in bars if r["date"]!="2025-01-31"],
            4,1,"2026-01-01","as_traded_close")
